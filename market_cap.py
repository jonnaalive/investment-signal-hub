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
_COMPANY_QUERY = """
import json, sys
import yfinance as yf
info = yf.Ticker(sys.argv[1]).get_info()
print(json.dumps({k: info.get(k) for k in ('symbol', 'sector', 'industry', 'longBusinessSummary')}))
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


# ── 업종/회사소개 (yfinance sector·industry·longBusinessSummary 기반, 부분 한글화) ──
SECTOR_KR = {
    "Technology": "기술", "Financial Services": "금융", "Healthcare": "헬스케어",
    "Consumer Cyclical": "경기소비재", "Consumer Defensive": "필수소비재",
    "Industrials": "산업재", "Energy": "에너지", "Basic Materials": "소재",
    "Communication Services": "커뮤니케이션", "Real Estate": "부동산", "Utilities": "유틸리티",
}

INDUSTRY_KR = {
    "Semiconductors": "반도체", "Software - Application": "응용 소프트웨어",
    "Software - Infrastructure": "인프라 소프트웨어", "Grocery Stores": "식료품점",
    "Internet Retail": "온라인 유통", "Specialty Retail": "전문 소매",
    "Discount Stores": "할인점", "Packaged Foods": "가공식품",
    "Biotechnology": "바이오텍", "Drug Manufacturers - General": "종합 제약",
    "Medical Devices": "의료기기", "Aerospace & Defense": "항공우주/방위산업",
    "Oil & Gas E&P": "석유/가스 탐사/생산", "Oil & Gas Integrated": "종합 석유/가스",
    "Auto Manufacturers": "자동차", "Auto Parts": "자동차 부품",
    "Banks - Regional": "지방은행", "Banks - Diversified": "종합은행",
    "Asset Management": "자산운용", "Insurance - Property & Casualty": "손해보험",
    "Telecom Services": "통신서비스", "Entertainment": "엔터테인먼트",
    "Utilities - Regulated Electric": "전력 (규제)", "Utilities - Renewable": "신재생 에너지",
}

_BIZ_TRANSLATIONS = {
    "cloud computing": "클라우드 컴퓨팅", "artificial intelligence": "AI",
    "machine learning": "머신러닝", "cybersecurity": "사이버보안",
    "semiconductor": "반도체", "data center": "데이터센터", "e-commerce": "이커머스",
    "biotechnology": "바이오테크", "pharmaceutical": "제약", "medical device": "의료기기",
    "renewable energy": "신재생에너지", "solar energy": "태양광", "wind energy": "풍력",
    "oil and gas": "석유/가스", "defense": "방위산업", "aerospace": "항공우주",
    "software": "소프트웨어", "platform": "플랫폼", "logistics": "물류",
    "food products": "식품", "consumer products": "소비재", "grocery": "식료품",
    "manufactures": "제조", "develops": "개발", "designs": "설계",
    "distributes": "유통", "operates": "운영", "sells": "판매", "produces": "생산",
    "worldwide": "글로벌", "united states": "미국",
}


_VERBS = (
    r"provides?|offers?|delivers?|supplies|develops?|manufactures?|produces?"
    r"|designs?|operates? as|engages? in|is an?|distributes?|focuses on|specializes? in"
)


def _translate(text: str) -> str:
    result = text.lower().strip().rstrip(".")
    for eng, kor in _BIZ_TRANSLATIONS.items():
        result = result.replace(eng.lower(), kor)
    return re.sub(r"\s+and\s+", "/", result).strip()


def _describe(sector: str | None, industry: str | None, summary: str | None) -> str:
    label = INDUSTRY_KR.get(industry, industry) if industry else (SECTOR_KR.get(sector, sector) if sector else "")
    intro = ""
    if summary:
        first = summary.split(". ")[0].strip().rstrip(".")
        # Drop the subject ("Company X provides ...") and keep the business predicate.
        match = re.search(rf"\b(?:{_VERBS})\s+(.*)", first, re.IGNORECASE)
        biz_part = match.group(1).strip() if match else first
        translated = _translate(biz_part)
        if any("가" <= c <= "힣" for c in translated):
            intro = translated
    if label and intro:
        return f"{label} · {intro}"
    return label or intro or UNAVAILABLE


@lru_cache(maxsize=256)
def company_info_label(ticker: str) -> str:
    symbol = (ticker or "").strip().upper()
    if re.fullmatch(r"[A-Z]+\.[AB]", symbol):
        symbol = symbol.replace(".", "-")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.^=-]{0,24}", symbol):
        return UNAVAILABLE
    try:
        result = subprocess.run(
            [sys.executable, "-c", _COMPANY_QUERY, symbol],
            capture_output=True, text=True, timeout=20, check=True,
        )
        info = json.loads(result.stdout.strip().splitlines()[-1])
        if str(info.get("symbol", "")).upper() != symbol:
            return UNAVAILABLE
        return _describe(info.get("sector"), info.get("industry"), info.get("longBusinessSummary"))
    except (subprocess.SubprocessError, OSError, ValueError, IndexError, AttributeError):
        return UNAVAILABLE
