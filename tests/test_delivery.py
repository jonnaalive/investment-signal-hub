import json
import sys
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
import requests

import hub


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(hub, "ROOT", tmp_path)
    monkeypatch.setenv("HUB_STATE_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.invalid/webhook")
    monkeypatch.setenv("HELD_TICKERS", "ABC")
    monkeypatch.setattr(sys, "argv", ["hub.py"])
    post = Mock(return_value=Mock(raise_for_status=Mock()))
    monkeypatch.setattr(hub.requests, "post", post)
    events = []
    monkeypatch.setattr(hub, "fetch_events", lambda: events)
    return tmp_path, events, post


def event(when=None, identity="one"):
    return {"ticker": "ABC", "source_bot": "guidance", "signal": "guidance_up",
        "detected_at": (when or datetime.now(hub.KST)).isoformat(),
        "source_url": "https://example.invalid/filing", "event_id": identity, "summary": "매출 전망 개선"}


def test_first_migration_no_replay_then_incremental_only(env):
    root, events, post = env
    events.append(event(datetime.now(hub.KST) - timedelta(days=1)))
    hub.main()
    assert post.call_count == 0
    events.append(event(identity="new"))
    hub.main()
    sent = post.call_count
    assert sent > 0
    hub.main()
    assert post.call_count == sent
    events[-1]["summary"] = "AI가 요약 문구를 바꿨음"
    hub.main()
    assert post.call_count == sent


def test_completed_and_later_same_day_event(env, monkeypatch):
    root, events, post = env
    hub.main()
    events.append(event())
    monkeypatch.setattr(sys, "argv", ["hub.py", "--review-ticker", "ABC"])
    hub.main()
    monkeypatch.setattr(sys, "argv", ["hub.py"])
    hub.main()
    assert post.call_count == 0
    events.append(event(identity="after-completion"))
    hub.main()
    assert post.call_count > 0
    text = "".join(call.kwargs["json"]["content"] for call in post.call_args_list)
    assert "/new-factory-update ABC" in text
    assert "기존 정본 없음" not in text


def test_missing_dates_not_refreshed_and_distinct_same_day_events():
    assert hub.deduplicate([{"ticker": "ABC"}]) == []
    assert len(hub.deduplicate([event(identity="a"), event(identity="b")])) == 2


def test_canon_suppresses_covered_events():
    events = [event(datetime(2026, 9, 1, tzinfo=hub.KST)), event(datetime(2026, 9, 3, tzinfo=hub.KST), "new")]
    assert hub.actionable_events(events, {"ABC": "2026-09-02"}, {}) == events[1:]


def test_partial_discord_failure_retries_only_remaining_chunks(env):
    root, _, post = env
    state = {"chunks": {}}
    path = root / "state.json"
    post.side_effect = [Mock(raise_for_status=Mock()), requests.HTTPError()]
    with pytest.raises(requests.HTTPError):
        hub.post_discord("a" * 3000, state=state, state_path=path)
    assert len(json.loads(path.read_text())["chunks"]) == 1
    post.reset_mock(side_effect=True)
    post.return_value = Mock(raise_for_status=Mock())
    hub.post_discord("a" * 3000, state=state, state_path=path)
    assert post.call_count == 1
    assert len(post.call_args.kwargs["json"]["content"]) == 1100


def test_ambiguous_timeout_does_not_resend(env):
    root, _, post = env
    state = {"chunks": {}}
    post.side_effect = requests.Timeout()
    with pytest.raises(requests.Timeout):
        hub.post_discord("report", state=state, state_path=root / "state.json")
    with pytest.raises(RuntimeError):
        hub.post_discord("report", state=state, state_path=root / "state.json")
    assert post.call_count == 1


def test_weekly_once_per_week(env, monkeypatch):
    root, events, post = env
    hub.main()
    events.append(event())
    monkeypatch.setattr(sys, "argv", ["hub.py", "--weekly"])
    hub.main()
    count = post.call_count
    assert count > 0
    hub.main()
    assert post.call_count == count


def test_snooze_and_reopen(env, monkeypatch):
    root, events, post = env
    hub.main()
    events.append(event())
    monkeypatch.setattr(sys, "argv", ["hub.py", "--review-ticker", "ABC", "--review-status", "snoozed"])
    hub.main()
    monkeypatch.setattr(sys, "argv", ["hub.py"])
    hub.main()
    assert not post.called
    monkeypatch.setattr(sys, "argv", ["hub.py", "--review-ticker", "ABC", "--review-status", "reopen"])
    hub.main()
    monkeypatch.setattr(sys, "argv", ["hub.py"])
    hub.main()
    assert post.called
