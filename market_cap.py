"""Optional market-cap enrichment. Never infer cap from stale event data."""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from datetime import datetime, timezone
from functools import lru_cache

UNAVAILABLE = "조회 불가"
_QUERY = """
import json, sys
import yfinance as yf
info = yf.Ticker(sys.argv[1]).get_info()
print(json.dumps({k: info.get(k) for k in ('symbol', 'marketCap', 'currency')}))
"""


def format_quote(quote: dict, symbol: str, checked_at: str) -> str:
    value, currency = quote.get("marketCap"), quote.get("currency")
    if str(quote.get("symbol", "")).upper() != symbol:
        return UNAVAILABLE
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return UNAVAILABLE
    if not math.isfinite(value) or value <= 0 or not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        return UNAVAILABLE
    if value >= 1e12:
        amount = f"{value / 1e12:,.2f}조"
    elif value >= 1e8:
        amount = f"{value / 1e8:,.2f}억"
    else:
        amount = f"{value:,.0f}"
    return f"{amount} {currency} · Yahoo Finance · 조회 {checked_at}"


@lru_cache(maxsize=256)
def market_cap_label(ticker: str) -> str:
    symbol = (ticker or "").strip().upper()
    # Preserve exchange suffixes (e.g. .HK); normalize US share classes only.
    if re.fullmatch(r"[A-Z]+\.[AB]", symbol):
        symbol = symbol.replace(".", "-")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.^=-]{0,24}", symbol):
        return UNAVAILABLE
    try:
        result = subprocess.run(
            [sys.executable, "-c", _QUERY, symbol],
            capture_output=True, text=True, timeout=20, check=True,
        )
        quote = json.loads(result.stdout.strip().splitlines()[-1])
        checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return format_quote(quote, symbol, checked_at)
    except (subprocess.SubprocessError, OSError, ValueError, IndexError, AttributeError):
        return UNAVAILABLE
