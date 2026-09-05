# Investment Signal Hub

세 투자 봇의 결과를 모아 보유·관심 우선순위, 7일 교차 신호,
NEW_FACTORY 검토 후보, Discord 일일 요약과 주간 위키 후보를 만든다.

매수·매도나 NEW_FACTORY 실행은 자동화하지 않는다. Secrets는
`DISCORD_WEBHOOK_URL`, `HELD_TICKERS`, `WATCH_TICKERS`다. 수량·평단·평가액은
저장하거나 전송하지 않는다.
# 2026-09-05: 증분 보고와 완료 기록

일일보고는 직전까지 보내지 않은 새 사건이 있는 종목만 보낸다. 최근 7일의 관련 사건은 그 종목의 교차 신호 해석에만 쓴다. 새 사건이 없으면 메시지를 생략하며, 주간보고만 최근 7일 전체를 ISO 주차당 한 번 요약한다.

운영 발송과 관리 명령은 `jonnaalive/guidance-up-bot/.github/workflows/signal-hub.yml` 한 곳에서 실행한다. 이 저장소의 수동 workflow는 `--no-discord` 미리보기 전용이다. 영속 이력은 guidance-up-bot의 `digest-state` 브랜치에 저장하며, 최초 실행은 기존 사건을 기준선으로 삼아 재발송하지 않는다.

`config/canon_index.json`의 정본 날짜 이전 사건은 일일 조사 권유에서 제외한다. 새 정본을 만든 작업에서는 다음 명령으로 실제 파일 목록을 동기화하고 이 저장소에 커밋한다. 같은 티커에 여러 날짜가 있으면 최신 날짜를 유지한다.

```sh
python scripts/build_canon_index.py /path/to/ai-workspace /path/to/INVEST --output config/canon_index.json
```

사용자가 `CIEN 분석 완료`라고 말하면 아래 명령으로 운영 이력을 갱신한다. `snoozed`는 기본 30일 보류, `reopen`은 수동 완료·보류 해제다. `review_until`에 별도 시각을 전달할 수 있다.

```sh
gh workflow run signal-hub.yml --repo jonnaalive/guidance-up-bot -f review_ticker=CIEN -f review_status=done
```

같은 보고서의 여러 조각 중 일부가 명확히 거절되면 성공한 조각을 제외하고 재시도한다. 응답 타임아웃은 수신 여부가 불명확하므로 자동 재전송하지 않고 운영 확인을 기다린다.

