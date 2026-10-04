import time
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
import requests

from review_polls import MAX_SYNC_FAILURES, create_poll, sync_polls, poll_key, url_for


def fixture(monkeypatch):
    now = datetime(2026, 9, 5, tzinfo=timezone.utc)
    spec = {"ticker": "CIEN", "through": (now - timedelta(days=1)).isoformat(), "ids": ["event1"]}
    message = {"id": "123456", "poll": {"answers": [
        {"answer_id": 11, "poll_media": {"text": "분석 완료"}},
        {"answer_id": 22, "poll_media": {"text": "30일 보류"}},
        {"answer_id": 33, "poll_media": {"text": "다시 검토"}}]}}
    reply = Mock(status_code=200, json=lambda: message, raise_for_status=Mock())
    post = Mock(return_value=reply)
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", Mock(return_value=reply))
    return now, spec, message, post


def test_poll_readback_missing_results_and_vote_changes(monkeypatch):
    now, spec, message, post = fixture(monkeypatch)
    state, save = {}, Mock()
    key = create_poll(spec, state, save, "https://example.invalid/hook")
    create_poll(spec, state, save, "https://example.invalid/hook")
    assert post.call_count == 1
    assert sync_polls(state, save, "https://example.invalid/hook", now)["unknown"] == 1
    assert "reviews" not in state
    message["poll"]["results"] = {"answer_counts": [{"id": 11, "count": 1}]}
    assert sync_polls(state, save, "https://example.invalid/hook", now)["applied"] == 1
    assert state["reviews"]["CIEN"]["through"] == spec["through"]
    assert sync_polls(state, save, "https://example.invalid/hook", now)["applied"] == 0
    message["poll"]["results"] = {"answer_counts": [{"id": 22, "count": 1}]}
    sync_polls(state, save, "https://example.invalid/hook", now)
    until = state["reviews"]["CIEN"]["until"]
    assert until == (now + timedelta(days=30)).isoformat()
    sync_polls(state, save, "https://example.invalid/hook", now + timedelta(days=1))
    assert state["reviews"]["CIEN"]["until"] == until
    message["poll"]["results"] = {"answer_counts": [{"id": 33, "count": 1}]}
    sync_polls(state, save, "https://example.invalid/hook", now)
    assert "CIEN" not in state["reviews"]


def test_test_poll_never_changes_company_state(monkeypatch):
    now, spec, message, post = fixture(monkeypatch)
    spec["test"] = True
    state = {}
    create_poll(spec, state, lambda: None, "https://example.invalid/hook")
    message["poll"]["results"] = {"answer_counts": [{"id": 11, "count": 1}]}
    assert sync_polls(state, lambda: None, "https://example.invalid/hook", now)["applied"] == 1
    assert "reviews" not in state


def test_conflicting_votes_and_old_poll_do_not_override(monkeypatch):
    now, spec, message, post = fixture(monkeypatch)
    state = {}
    key = create_poll(spec, state, lambda: None, "https://example.invalid/hook")
    message["poll"]["results"] = {"answer_counts": [{"id": 11, "count": 1}, {"id": 22, "count": 1}]}
    assert sync_polls(state, lambda: None, "https://example.invalid/hook", now)["applied"] == 0
    state["active_polls"]["CIEN"] = "newer-poll"
    message["poll"]["results"] = {"answer_counts": [{"id": 11, "count": 1}]}
    assert sync_polls(state, lambda: None, "https://example.invalid/hook", now)["checked"] == 0


def test_timeout_does_not_create_duplicate(monkeypatch):
    now, spec, message, post = fixture(monkeypatch)
    post.side_effect = requests.Timeout()
    state = {}
    with pytest.raises(RuntimeError):
        create_poll(spec, state, lambda: None, "https://example.invalid/hook")
    with pytest.raises(RuntimeError):
        create_poll(spec, state, lambda: None, "https://example.invalid/hook")
    assert post.call_count == 1
    assert state["polls"][poll_key(spec)]["status"] == "uncertain"


def test_rate_limited_readback_retries_then_succeeds(monkeypatch):
    now, spec, message, post = fixture(monkeypatch)
    ok = Mock(status_code=200, json=lambda: message, raise_for_status=Mock())
    ok.headers = {}
    limited = Mock(status_code=429, raise_for_status=Mock())
    limited.headers = {"Retry-After": "0"}
    get = Mock(side_effect=[limited, ok])
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(time, "sleep", Mock())
    state = {}
    create_poll(spec, state, lambda: None, "https://example.invalid/hook")
    message["poll"]["results"] = {"answer_counts": [{"id": 11, "count": 1}]}
    assert sync_polls(state, lambda: None, "https://example.invalid/hook", now)["applied"] == 1
    assert get.call_count == 2


def test_persistent_readback_failure_stops_blocking_reports(monkeypatch):
    now, spec, message, post = fixture(monkeypatch)
    monkeypatch.setattr(requests, "get", Mock(side_effect=requests.Timeout()))
    monkeypatch.setattr(time, "sleep", Mock())
    state = {}
    key = create_poll(spec, state, lambda: None, "https://example.invalid/hook")
    for attempt in range(1, MAX_SYNC_FAILURES):
        assert sync_polls(state, lambda: None, "https://example.invalid/hook", now)["failed"] == 1
        assert state["polls"][key]["sync_failures"] == attempt
    result = sync_polls(state, lambda: None, "https://example.invalid/hook", now)
    assert result == {"checked": 0, "applied": 0, "unknown": 1, "failed": 0}
    assert state["polls"][key]["status"] == "unreadable"
    assert sync_polls(state, lambda: None, "https://example.invalid/hook", now)["failed"] == 0


def test_recovered_readback_clears_the_failure_count(monkeypatch):
    now, spec, message, post = fixture(monkeypatch)
    state = {}
    key = create_poll(spec, state, lambda: None, "https://example.invalid/hook")
    monkeypatch.setattr(requests, "get", Mock(side_effect=requests.Timeout()))
    monkeypatch.setattr(time, "sleep", Mock())
    sync_polls(state, lambda: None, "https://example.invalid/hook", now)
    assert state["polls"][key]["sync_failures"] == 1
    fixture(monkeypatch)
    sync_polls(state, lambda: None, "https://example.invalid/hook", now)
    assert "sync_failures" not in state["polls"][key]


def test_webhook_thread_query_preserved():
    url = "https://example.invalid/webhooks/1/token?thread_id=789&wait=true"
    assert url_for(url, "123") == "https://example.invalid/webhooks/1/token/messages/123?thread_id=789"
