from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import requests

ROOT = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
SOURCES = {
    "guidance": "https://raw.githubusercontent.com/jonnaalive/guidance-up-bot/main/data/latest_signals.json",
    "surge": "https://raw.githubusercontent.com/jonnaalive/telegram-surge-bot-discord/main/data/latest_signals.json",
    "earnings": "https://raw.githubusercontent.com/jonnaalive/earnings-screener-discord/main/data/weekly_hits.json",
}
LABELS = {
    "guidance_up": "가이던스 상향",
    "price_surge": "주가 급등",
    "price_drop": "주가 급락",
    "earnings_downside": "실적 다운사이드",
    "earnings_screen": "실적 스크리너",
}


def normalize_ticker(value: str) -> str:
    ticker = (value or "").strip().upper()
    for suffix in (".KS", ".KQ", ".T"):
        if ticker.endswith(suffix):
            ticker = ticker[: -len(suffix)]
    return ticker


def parse_tickers(raw: str) -> set[str]:
    return {normalize_ticker(x) for x in raw.split(",") if normalize_ticker(x)}


def parse_time(value: str) -> datetime:
    if not value:
        return datetime.now(KST)
    value = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = datetime.fromisoformat(value[:10])
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=KST)).astimezone(KST)


def normalize_event(raw: dict) -> dict:
    event = dict(raw)
    event["ticker"] = normalize_ticker(str(event.get("ticker", "")))
    event["detected_at"] = parse_time(str(event.get("detected_at") or event.get("date") or "")).isoformat()
    event.setdefault("source_bot", "unknown")
    event.setdefault("signal", "unknown")
    event.setdefault("company", event["ticker"])
    event.setdefault("summary", "")
    event.setdefault("source_url", "")
    return event


def deduplicate(events: Iterable[dict]) -> list[dict]:
    unique = {}
    for raw in events:
        event = normalize_event(raw)
        if not event["ticker"] or event["ticker"] == "UNKNOWN":
            continue
        day = parse_time(event["detected_at"]).date().isoformat()
        unique[(event["ticker"], event["source_bot"], event["signal"], day)] = event
    return sorted(unique.values(), key=lambda x: x["detected_at"], reverse=True)


def retain_window(events: Iterable[dict], days: int, now: datetime | None = None) -> list[dict]:
    cutoff = (now or datetime.now(KST)) - timedelta(days=days)
    return [e for e in events if parse_time(e["detected_at"]) >= cutoff]


@dataclass
class RankedTicker:
    ticker: str
    events: list[dict]
    status: str
    score: int
    cross_signal: bool
    canon_date: str = ""

    @property
    def needs_factory(self) -> str:
        if not self.cross_signal and self.score < 5:
            return ""
        return "update" if self.canon_date else "new"


def rank_events(events: Iterable[dict], held: set[str], watch: set[str], canon: dict[str, str]) -> list[RankedTicker]:
    grouped = defaultdict(list)
    for event in events:
        grouped[event["ticker"]].append(event)
    ranked = []
    for ticker, ticker_events in grouped.items():
        cross = len({e["source_bot"] for e in ticker_events}) >= 2
        status, score = ("보유", 5) if ticker in held else (("관심", 3) if ticker in watch else ("미보유", 0))
        score += 4 if cross else 0
        score += 2 if any(e["signal"] == "guidance_up" for e in ticker_events) else 0
        score += 1 if any(e["signal"] in {"price_surge", "price_drop"} for e in ticker_events) else 0
        score += 1 if any(e.get("source_url") for e in ticker_events) else 0
        ranked.append(RankedTicker(ticker, sorted(ticker_events, key=lambda x: x["detected_at"], reverse=True), status, score, cross, canon.get(ticker, "")))
    return sorted(ranked, key=lambda x: (-x.score, x.ticker))


def _section(title: str, items: list[RankedTicker]) -> list[str]:
    lines = [f"## {title}", ""]
    if not items:
        return lines + ["- 없음", ""]
    for item in items[:15]:
        lines += [f"### {item.ticker}: {'교차' if item.cross_signal else item.status} · 점수 {item.score}", f"- 상태: {item.status}"]
        for event in item.events[:4]:
            label = LABELS.get(event["signal"], event["signal"])
            lines.append(f"- {label}: {event.get('summary', '')}".rstrip(": "))
        lines.append("")
    return lines


def build_daily(ranked: list[RankedTicker], now: datetime | None = None) -> str:
    now = now or datetime.now(KST)
    lines = [f"# 투자 신호 일일보고: {now:%Y-%m-%d}", ""]
    lines += _section("즉시 확인", [x for x in ranked if x.score >= 7])
    lines += _section("조사 대기열", [x for x in ranked if 4 <= x.score < 7])
    lines += ["## NEW_FACTORY 검토 후보", ""]
    factory = [x for x in ranked if x.needs_factory]
    if not factory:
        lines.append("- 없음")
    for item in factory:
        command = "/new-factory-update" if item.needs_factory == "update" else "/new-factory"
        basis = f"기존 정본 {item.canon_date}" if item.canon_date else "기존 정본 없음"
        lines.append(f"- **{item.ticker}**: {basis}, 제안 `{command} {item.ticker}`")
    lines += ["", "## 보관", "", f"- 낮은 우선순위 {len([x for x in ranked if x.score < 4])}종", "", "> 조사 우선순위이며 매수·매도 및 NEW_FACTORY 실행을 자동화하지 않는다."]
    return "\n".join(lines)


def build_weekly(ranked: list[RankedTicker], now: datetime | None = None) -> str:
    now = now or datetime.now(KST)
    combinations = {}
    for item in ranked:
        key = " + ".join(sorted({LABELS.get(e["signal"], e["signal"]) for e in item.events}))
        combinations[key] = combinations.get(key, 0) + 1
    lines = [f"# {now:%Y년 %W주차} 투자 신호 패턴 후보", ""]
    lines += [f"- {key}: {count}종" for key, count in sorted(combinations.items(), key=lambda x: (-x[1], x[0])) if count >= 2]
    if len(lines) == 2:
        lines.append("- 2회 이상 반복된 패턴 없음")
    return "\n".join(lines + ["", "> 위키 자동 반영본이 아니라 `/ingest` 검토용 후보다."])


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_events() -> list[dict]:
    events = []
    for name, url in SOURCES.items():
        try:
            response = requests.get(url, timeout=20)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, list):
                if name == "earnings":
                    events.extend({
                        "ticker": row.get("ticker", ""),
                        "company": row.get("name", ""),
                        "date": row.get("date", ""),
                        "source_bot": "earnings",
                        "signal": "earnings_screen",
                        "summary": f"실적 스크리너 {row.get('pass_count', 0)}/4 통과",
                    } for row in payload)
                else:
                    events.extend(payload)
        except requests.RequestException as exc:
            print(f"WARN {name}: {exc}")
    return events


def post_discord(markdown: str) -> None:
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        return
    for offset in range(0, len(markdown), 1900):
        requests.post(webhook, json={"content": markdown[offset:offset + 1900]}, timeout=20).raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weekly", action="store_true")
    parser.add_argument("--no-discord", action="store_true")
    args = parser.parse_args()
    merged = retain_window(deduplicate([*load_json(ROOT / "data/events.json", []), *fetch_events()]), 35)
    save_json(ROOT / "data/events.json", merged)
    canon = {k.upper(): v for k, v in load_json(ROOT / "config/canon_index.json", {}).items()}
    ranked = rank_events(retain_window(merged, 7), parse_tickers(os.getenv("HELD_TICKERS", "")), parse_tickers(os.getenv("WATCH_TICKERS", "")), canon)
    report = build_weekly(ranked) if args.weekly else build_daily(ranked)
    output = ROOT / "output" / ("weekly_wiki_candidate.md" if args.weekly else "daily_digest.md")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report + "\n", encoding="utf-8")
    print(report)
    if not args.no_discord:
        post_discord(report)


if __name__ == "__main__":
    main()
