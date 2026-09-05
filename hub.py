from __future__ import annotations

import argparse
import hashlib
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
    "new_guidance": "신규 가이던스",
    "turnaround_candidate": "턴어라운드 후보",
    "one_off_guidance": "일회성 요인 포함 가이던스",
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
    timestamp = event.get("detected_at") or event.get("date")
    if not timestamp:
        raise ValueError("신호 날짜 누락: 현재 시각으로 대체하지 않습니다")
    event["detected_at"] = parse_time(str(timestamp)).isoformat()
    event.setdefault("source_bot", "unknown")
    event.setdefault("signal", "unknown")
    event.setdefault("company", event["ticker"])
    event.setdefault("summary", "")
    event.setdefault("source_url", "")
    return event


def event_id(event: dict) -> str:
    # Prefer the producer's normalized financial event ID. Never hash prose.
    identity = event.get("event_id") or [event["ticker"], event["source_bot"],
        event["signal"], event["detected_at"], event.get("source_url", "")]
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def deduplicate(events: Iterable[dict]) -> list[dict]:
    unique = {}
    for raw in events:
        try:
            event = normalize_event(raw)
        except (ValueError, TypeError):
            continue
        if not event["ticker"] or event["ticker"] == "UNKNOWN":
            continue
        unique[event_id(event)] = event
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
        score += 2 if any(e["signal"] in {"guidance_up", "turnaround_candidate"} for e in ticker_events) else 0
        score += 1 if any(e["signal"] in {"price_surge", "price_drop"} for e in ticker_events) else 0
        score += 1 if any(e.get("source_url") for e in ticker_events) else 0
        ranked.append(RankedTicker(ticker, sorted(ticker_events, key=lambda x: x["detected_at"], reverse=True), status, score, cross, canon.get(ticker, "")))
    return sorted(ranked, key=lambda x: (-x.score, x.ticker))


def actionable_events(events: Iterable[dict], canon: dict[str, str], reviews: dict) -> list[dict]:
    result = []
    for event in events:
        ticker = event["ticker"]
        review = reviews.get(ticker, {})
        if review.get("status") == "snoozed" and parse_time(review["until"]) > datetime.now(KST):
            continue
        # A date-only canon means all events on that date were covered.
        covered = max(canon.get(ticker, ""), review.get("through", ""))
        if covered:
            if len(covered) == 10:
                if parse_time(event["detected_at"]).date().isoformat() <= covered:
                    continue
            elif parse_time(event["detected_at"]) <= parse_time(covered):
                continue
        result.append(event)
    return result


def explain_why_now(item: RankedTicker) -> tuple[str, str, str]:
    signals = {event["signal"] for event in item.events}
    latest = parse_time(item.events[0]["detected_at"]).strftime("%m월 %d일")

    if "guidance_up" in signals and "price_drop" in signals:
        why = f"{latest}까지 좋은 회사 숫자와 약한 주가가 동시에 잡혔다. 시장 기대치가 더 높았는지, 섹터 매도인지 구분하면 과잉반응 후보를 찾을 수 있다."
        check = "상향 폭을 직전 회사 목표와 비교하고, FCF·수주·마진 중 빠진 악재가 있는지 확인"
        avoid = "주가가 싸 보인다는 이유만으로 바로 매수하지 않기"
    elif "guidance_up" in signals and "price_surge" in signals:
        why = f"{latest}까지 가이던스 상향이 가격 재평가로 이어졌다. 상향 폭보다 주가 반응이 큰지 지금 확인해야 추격매수와 구조적 개선을 구분할 수 있다."
        check = "가이던스 중간값 변화율, 다음 연도 연결성, 거래량과 숏커버 가능성 확인"
        avoid = "급등 자체를 NEW_FACTORY 매수 신호로 해석하지 않기"
    elif "earnings_downside" in signals and "price_drop" in signals:
        why = f"{latest}까지 실적 위험과 가격 하락이 겹쳤다. 보유 논리의 KPI가 실제로 훼손됐는지 늦기 전에 확인할 구간이다."
        check = "가이던스 하향 여부, 일회성 비용, 기존 정본의 폐기조건 충족 여부 확인"
        avoid = "헤드라인 미스만 보고 자동 손절하지 않기"
    elif "earnings_screen" in signals and "price_drop" in signals:
        why = f"{latest}까지 재무 개선 신호와 주가 하락이 엇갈렸다. 실적은 좋아지는데 기대치나 배수만 낮아진 종목인지 볼 가치가 있다."
        check = "매출 성장의 P·Q 분해, 현금흐름, 하락이 개별 악재인지 섹터 수급인지 확인"
        avoid = "52주 저가 근접만으로 바닥을 단정하지 않기"
    elif item.cross_signal:
        bots = len({event["source_bot"] for event in item.events})
        why = f"최근 7일 안에 서로 다른 {bots}개 수집기가 같은 종목을 독립적으로 포착했다. 단발성 뉴스보다 정보 변화 가능성이 높아진 시점이다."
        check = "각 신호가 같은 사건의 중복 보도인지, 서로 다른 펀더멘털·가격 증거인지 확인"
        avoid = "교차 감지를 매수 확정 신호로 사용하지 않기"
    elif item.status == "보유":
        why = f"{latest} 새 신호가 실제 보유 자본과 연결된다. 신규 종목보다 먼저 기존 투자 논리와 충돌하는지 확인해야 한다."
        check = "정본의 핵심 KPI·폐기조건·다음 실적 확인 항목과 대조"
        avoid = "신호 하나로 목표가나 매수선을 자동 변경하지 않기"
    elif item.status == "관심":
        why = f"{latest} 새 신호가 기존 관심 종목의 진입 전제를 바꿀 수 있다. 가격보다 논리가 먼저 개선됐는지 확인할 때다."
        check = "기존 관망 사유가 해소됐는지, 정본 이후 새 정보인지 확인"
        avoid = "관심 등록을 매수 승인으로 오해하지 않기"
    else:
        why = f"{latest} 전체 시장 수집에서 새 후보로 잡혔다. 아직 내 종목은 아니므로 기존 보유보다 우선하지 않되, 반복 신호가 생기는지 관찰할 가치가 있다."
        check = "원자료 존재, 시가총액·유동성, 일회성 여부, 기존 위키 테마 연결 확인"
        avoid = "단일 신호만으로 NEW_FACTORY를 대량 실행하지 않기"
    return why, check, avoid


def _section(title: str, items: list[RankedTicker]) -> list[str]:
    lines = [f"## {title}", ""]
    if not items:
        return lines + ["- 없음", ""]
    for item in items[:15]:
        lines += [f"### {item.ticker}: {'교차' if item.cross_signal else item.status} · 점수 {item.score}", f"- 상태: {item.status}"]
        for event in item.events[:4]:
            label = LABELS.get(event["signal"], event["signal"])
            lines.append(f"- {label}: {event.get('summary', '')}".rstrip(": "))
        why, check, avoid = explain_why_now(item)
        lines += [f"- **왜 지금:** {why}", f"- **확인할 것:** {check}", f"- **지금 하지 말 것:** {avoid}"]
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
        why, check, _ = explain_why_now(item)
        lines.append(f"- **{item.ticker}**: {basis}, 제안 `{command} {item.ticker}`")
        lines.append(f"  - 선정 이유: {why}")
        lines.append(f"  - 실행 전 확인: {check}")
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


def post_discord(markdown: str, *, state: dict | None = None, state_path: Path | None = None, report_key: str = "") -> None:
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        raise RuntimeError("DISCORD_WEBHOOK_URL 누락: 발송 완료로 기록하지 않습니다")
    sent_chunks = (state or {}).get("chunks", {})
    for offset in range(0, len(markdown), 1900):
        chunk = markdown[offset:offset + 1900]
        key = hashlib.sha256((report_key + str(offset) + chunk).encode()).hexdigest()
        if key in sent_chunks:
            if sent_chunks[key] in {"sending", "uncertain"}:
                raise RuntimeError("Discord 수신 여부 불명: 자동 재발송 보류, 운영 확인 필요")
            continue
        sent_chunks[key] = "sending"
        if state is not None and state_path is not None:
            state["chunks"] = sent_chunks
            save_json(state_path, state)
        try:
            requests.post(webhook + ("&" if "?" in webhook else "?") + "wait=true",
                json={"content": chunk, "allowed_mentions": {"parse": []}}, timeout=20).raise_for_status()
        except requests.HTTPError:
            sent_chunks.pop(key)
            if state is not None and state_path is not None:
                save_json(state_path, state)
            raise
        except Exception:
            sent_chunks[key] = "uncertain"
            if state is not None and state_path is not None:
                save_json(state_path, state)
            raise
        sent_chunks[key] = datetime.now(KST).isoformat()
        if state is not None and state_path is not None:
            state["chunks"] = sent_chunks
            save_json(state_path, state)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weekly", action="store_true")
    parser.add_argument("--no-discord", action="store_true")
    parser.add_argument("--review-ticker", default="")
    parser.add_argument("--review-status", choices=["done", "snoozed", "reopen"], default="done")
    parser.add_argument("--review-until", default="")
    args = parser.parse_args()
    now = datetime.now(KST)
    state_path = Path(os.getenv("HUB_STATE_DIR", str(ROOT / "runtime"))) / "delivery.json"
    state = load_json(state_path, {})
    if args.review_ticker:
        if not state:
            state = {"since": now.isoformat(), "sent": {}, "weeks": {}, "chunks": {}}
        ticker = normalize_ticker(args.review_ticker)
        if not ticker.replace(".", "").replace("-", "").isalnum():
            raise ValueError("유효하지 않은 티커")
        reviews = state.setdefault("reviews", {})
        if args.review_status == "reopen":
            reviews.pop(ticker, None)
        elif args.review_status == "snoozed":
            until = parse_time(args.review_until) if args.review_until else now + timedelta(days=30)
            if until <= now:
                raise ValueError("보류 종료일은 미래여야 합니다")
            reviews[ticker] = {"status": "snoozed", "until": until.isoformat()}
        else:
            through = parse_time(args.review_until) if args.review_until else now
            if through > now:
                raise ValueError("분석 완료 기준일은 미래일 수 없습니다")
            reviews[ticker] = {"status": "done", "through": through.isoformat()}
        # Discard a partly delivered report if its recommendations became obsolete.
        state.pop("pending", None)
        save_json(state_path, state)
        print(f"{ticker}: {args.review_status} 기록 완료")
        return
    merged = retain_window(deduplicate([*load_json(ROOT / "data/events.json", []), *fetch_events()]), 35)
    save_json(ROOT / "data/events.json", merged)
    canon = {k.upper(): v for k, v in load_json(ROOT / "config/canon_index.json", {}).items()}
    reviews = state.get("reviews", {})
    covered_canon = dict(canon)
    for ticker, review in reviews.items():
        if review.get("status") == "done":
            canon[ticker] = max(canon.get(ticker, ""), review["through"][:10])
    window = retain_window(merged, 7, now)
    if not state:
        # First deployment/cache loss must not replay the existing 7-day backlog.
        state = {"since": now.isoformat(), "sent": {}, "weeks": {}, "chunks": {}}
        if not args.no_discord:
            save_json(state_path, state)
            print("Delivery state initialized; existing events are the baseline.")
            return
        state["since"] = (now - timedelta(days=1)).isoformat()
    selected = window if args.weekly else [e for e in actionable_events(window, covered_canon, reviews)
        if event_id(e) not in state["sent"] and parse_time(e["detected_at"]) > parse_time(state["since"])]
    week = now.strftime("%G-W%V")
    key = "weekly:" + week if args.weekly else "daily"
    if (not selected and key not in state.get("pending", {})) or (args.weekly and week in state["weeks"]):
        print("새 신호 없음: Discord 발송 생략")
        return
    changed = {e["ticker"] for e in selected}
    context = window if args.weekly else [e for e in actionable_events(window, covered_canon, reviews) if e["ticker"] in changed]
    ranked = rank_events(context, parse_tickers(os.getenv("HELD_TICKERS", "")), parse_tickers(os.getenv("WATCH_TICKERS", "")), canon)
    report = build_weekly(ranked, now) if args.weekly else build_daily(ranked, now)
    output = ROOT / "output" / ("weekly_wiki_candidate.md" if args.weekly else "daily_digest.md")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report + "\n", encoding="utf-8")
    print(report)
    if not args.no_discord:
        # Keep the exact report for chunk-level retries, including its date.
        pending = state.setdefault("pending", {}).get(key)
        if pending:
            report, delivered_ids = pending["report"], pending["ids"]
        else:
            delivered_ids = [event_id(e) for e in selected]
            state["pending"][key] = {"report": report, "ids": delivered_ids}
            save_json(state_path, state)
        post_discord(report, state=state, state_path=state_path, report_key=key)
        if args.weekly:
            state["weeks"][week] = now.isoformat()
        else:
            state["sent"].update({eid: now.isoformat() for eid in delivered_ids})
        state["pending"].pop(key, None)
        save_json(state_path, state)


if __name__ == "__main__":
    main()
