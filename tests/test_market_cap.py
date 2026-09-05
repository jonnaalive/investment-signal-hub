import json
import subprocess
from unittest.mock import Mock

import pytest
import market_cap


def test_daily_displays_cap_and_fallback():
    import hub
    item = hub.RankedTicker("ABC", [{"signal": "guidance_up", "detected_at": "2026-09-05T00:00:00+00:00"}], "보유", 7, False)
    report = hub.build_daily([item], market_caps={"ABC": "12.50억 USD"})
    assert report.count("시가총액: 12.50억 USD") == 2
    assert "시가총액: 조회 불가" in hub.build_daily([item])


@pytest.mark.parametrize("value, expected", [(1.25e12, "1.25조 USD"), (1.25e9, "12.50억 USD"), (1e6, "1,000,000 USD")])
def test_units(value, expected):
    assert market_cap.format_quote({"symbol": "ABC", "marketCap": value, "currency": "USD"}, "ABC", "date").startswith(expected)


@pytest.mark.parametrize("value", [None, 0, -1, float("nan"), float("inf"), True, "123"])
def test_bad_values(value):
    assert market_cap.format_quote({"symbol": "ABC", "marketCap": value, "currency": "USD"}, "ABC", "date") == market_cap.UNAVAILABLE


def test_symbol_currency_and_failure(monkeypatch):
    assert market_cap.format_quote({"symbol": "OTHER", "marketCap": 1e9, "currency": "USD"}, "ABC", "date") == market_cap.UNAVAILABLE
    assert market_cap.format_quote({"symbol": "ABC", "marketCap": 1e9}, "ABC", "date") == market_cap.UNAVAILABLE
    market_cap.market_cap_label.cache_clear()
    monkeypatch.setattr(market_cap.subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("quote", 20)))
    assert market_cap.market_cap_label("ABC") == market_cap.UNAVAILABLE


def test_cache_and_class_symbol(monkeypatch):
    market_cap.market_cap_label.cache_clear()
    run = Mock(return_value=Mock(stdout=json.dumps({"symbol": "BRK-B", "marketCap": 1e12, "currency": "USD"})))
    monkeypatch.setattr(market_cap.subprocess, "run", run)
    assert "조회" in market_cap.market_cap_label("BRK.B")
    assert "1.00조 USD" in market_cap.market_cap_label("BRK.B")
    assert run.call_count == 1
    assert run.call_args.args[0][-1] == "BRK-B"
    assert run.call_args.kwargs["timeout"] == 20
    market_cap.market_cap_label.cache_clear()
