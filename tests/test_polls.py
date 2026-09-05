from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
import requests

from review_polls import create_poll, sync_polls, poll_key, url_for


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


def test_webhook_thread_query_preserved():
    url = "https://example.invalid/webhooks/1/token?thread_id=789&wait=true"
    assert url_for(url, "123") == "https://example.invalid/webhooks/1/token/messages/123?thread_id=789"
