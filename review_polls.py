"""Single-owner Discord polls, read back through the sending webhook token."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests

CHOICES = {"분석 완료": "done", "30일 보류": "snoozed", "다시 검토": "reopen"}


def url_for(webhook, message_id=None):
    parts = urlsplit(webhook)
    query = dict(parse_qsl(parts.query))
    query.pop("wait", None)
    path = parts.path.rstrip("/")
    if message_id is not None:
        if not str(message_id).isdigit():
            raise ValueError("Invalid Discord message ID")
        path += "/messages/" + str(message_id)
    else:
        query["wait"] = "true"
    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), ""))


def poll_key(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def create_poll(spec, state, save, webhook):
    key = poll_key(spec)
    polls = state.setdefault("polls", {})
    existing = polls.get(key)
    if existing:
        if existing.get("status") in {"sending", "uncertain"}:
            raise RuntimeError("투표 발송 확인 필요: 자동 중복 발송 보류")
        return key
    ticker = spec["ticker"]
    record = {**spec, "status": "sending", "created_at": datetime.now(timezone.utc).isoformat()}
    polls[key] = record
    save()
    title = "연결 테스트: 아무 항목이나 선택해 주세요" if spec.get("test") else f"{ticker}: 이 기업 분석 상태는?"
    content = "테스트 투표입니다. 종목의 실제 분석 상태는 바꾸지 않습니다." if spec.get("test") else (
        f"{ticker} · 이 알림의 분석 상태를 선택해 주세요.\n"
        "분석 완료는 이 알림까지의 사건에 적용됩니다. 이후 새 사건은 업데이트 대상으로 남습니다.\n"
        "선택은 다음 투표 확인 때 반영됩니다. 답을 바꿀 수 있으며, 투표 취소만으로 기존 기록은 지워지지 않습니다.")
    try:
        response = requests.post(url_for(webhook), json={"content": content,
            "allowed_mentions": {"parse": []}, "poll": {"question": {"text": title},
                "answers": [{"poll_media": {"text": label}} for label in CHOICES],
                "duration": 768, "allow_multiselect": False}}, timeout=20)
        response.raise_for_status()
        message = response.json()
        message_id = str(message["id"])
        if not message_id.isdigit():
            raise ValueError("Invalid message response")
        answers = {str(a["answer_id"]): CHOICES[a["poll_media"]["text"]] for a in message["poll"]["answers"]}
        if set(answers.values()) != set(CHOICES.values()):
            raise ValueError("Poll answers missing")
        record.update({"status": "active", "message_id": message_id, "answers": answers})
        state.setdefault("active_polls", {})[ticker] = key
        save()
        print(f"투표 생성: {ticker} message={message_id}")
        return key
    except requests.HTTPError as exc:
        polls.pop(key, None)
        save()
        code = exc.response.status_code if exc.response is not None else "unknown"
        raise RuntimeError(f"Discord 투표 생성 거절 (HTTP {code})") from None
    except Exception:
        record["status"] = "uncertain"
        save()
        raise RuntimeError("Discord 투표 생성 응답 불명: 운영 확인 필요") from None


def sync_polls(state, save, webhook, now=None):
    now = now or datetime.now(timezone.utc)
    stats = {"checked": 0, "applied": 0, "unknown": 0, "failed": 0}
    for key, record in state.get("polls", {}).items():
        if record.get("status") != "active" or state.get("active_polls", {}).get(record["ticker"]) != key:
            continue
        try:
            response = requests.get(url_for(webhook, record["message_id"]), timeout=20)
            if response.status_code == 404:
                record["status"] = "deleted"
                save()
                continue
            response.raise_for_status()
            message = response.json()
            if str(message.get("id")) != record["message_id"]:
                raise ValueError("Wrong message returned")
            stats["checked"] += 1
            results = message.get("poll", {}).get("results")
            if not isinstance(results, dict):
                stats["unknown"] += 1
                continue
            votes = results.get("answer_counts", [])
            total = sum(int(answer.get("count", 0)) for answer in votes)
            selected = [str(a["id"]) for a in votes if a.get("count", 0) == 1]
            # Single-owner channel: conflicting/multiple voters never mutate state.
            if total == 1 and len(selected) == 1 and selected[0] in record["answers"]:
                choice = record["answers"][selected[0]]
                if choice != record.get("applied_choice"):
                    if not record.get("test"):
                        reviews = state.setdefault("reviews", {})
                        if choice == "done":
                            reviews[record["ticker"]] = {"status": "done", "through": record["through"], "poll": key}
                        elif choice == "snoozed":
                            reviews[record["ticker"]] = {"status": "snoozed", "until": (now + timedelta(days=30)).isoformat(), "poll": key}
                        else:
                            reviews.pop(record["ticker"], None)
                        state.pop("pending", None)
                    record.update({"applied_choice": choice, "applied_at": now.isoformat()})
                    stats["applied"] += 1
                    print(f"투표 반영: {record['ticker']} {choice}" + (" (연결 테스트)" if record.get("test") else ""))
            elif total > 1:
                stats["unknown"] += 1
            if results.get("is_finalized"):
                record["status"] = "closed"
            save()
        except (requests.RequestException, ValueError, TypeError, KeyError):
            stats["failed"] += 1
            print(f"투표 조회 실패: {record['ticker']} (다음 실행 재시도)")
    print(f"투표 확인: {stats}")
    return stats
