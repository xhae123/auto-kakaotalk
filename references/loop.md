# 메인 루프

## 핵심: 자동 전송

Claude 가 판단해서 바로 보낸다. 매 메시지마다 "이거 보낼까?" 사용자에게 묻지 않는다. 등록된 상대에 대해선 AI 가 전권. 이게 이 도구의 전제다 — 승인 루프가 있으면 사용자가 승인 루프 자체를 피하게 되고, 그러면 원점이다.

단, **등록 시점에 1회 한해** 사용자와 톤 calibration 을 거친다. 어떤 상대에게 어떤 뉘앙스로 답할지 사회과학적 분석과 함께 합의를 본 뒤, 그 합의를 persona.md 에 박아두고 루프는 그걸 따른다. 자세한 건 `references/register.md`.

## 세션 시작 시퀀스

```
# 1) 셋업 + DB 복구
bash scripts/cycle.sh check

# 2) 등록 목록 확인, 필요 시 추가/수정 (references/register.md — calibration 포함)

# 3) "먼저 말 걸 상대 있어?" 한 번 묻기
#    있으면 사용자와 대화하며 드래프트 확정 → send.py

# 4) 수동 poll 1회: bash scripts/cycle.sh poll → 결과 있으면 §drafting

# 5) 폴링 스케줄 등록
CronCreate(
  cron: "*/3 * * * *",
  prompt: "auto-kakaotalk tick. Run `bash scripts/cycle.sh poll`.
           If empty, say nothing and finish.
           Otherwise handle per references/loop.md §drafting."
)

# 6) 사용자에게 "루프 돌고 있어" 한 줄 보고.
```

먼저 말걸기(proactive) 는 cron 으로 돌리지 않는다. 세션 시작 3단계에서 한 번, 그리고 사용자가 명시적으로 시킬 때만.

## §Drafting (새 메시지 처리)

tick 이 깨어나 `cycle.sh poll` 이 pending 을 뱉으면:

```
pending = bash scripts/cycle.sh poll    # JSON array

for inbound in pending:
    target  = targets[inbound.chat_id]
    ctx     = python3 scripts/db.py get-context --chat-id <id> --limit 30
    persona = Read(target.persona_path)   # calibration 결과 들어 있음

    draft = <Claude 가 판단>

    if draft == "skip":
        python3 scripts/db.py mark --id <inbound.id> --state skipped
        continue

    # 자동 전송. 사용자 승인 없음.
    python3 scripts/send.py --reply-to <inbound.id> --text "<draft>"
```

## 사후 통지

전송 후엔 세션에 한 줄 로그로 남긴다. **승인 요청이 아니라 사후 통지**다.

```
>  [용진] "좆됏군" → "ㅋㅋ 왜" 보냄
>  [엄마] "밥 먹었니?" → "응 방금" 보냄
```

사용자가 읽고 "아 그거 잘못 보냈어" 하면 수동 수습. 자동화의 대가는 가끔의 실수고, 승인 루프의 대가는 자동화 자체의 포기다.

## 사용자가 중간에 개입할 때

세션이 열려 있으니 언제든 말로 지시 가능.

- "엄마 방은 이제 내가 직접 할게" → `db.py remove-target --chat-id <id>`
- "용진한테 뭐 보낼까 제안해봐" → Claude 가 드래프트 제시 → 사용자 선택 → send
- "방금 보낸 거 실수야" → Claude 가 카톡 앱 열어 수동 삭제 안내

사용자가 능동적으로 시킬 때만. 매 메시지마다 자동으로 묻지 않는다.

## §Proactive (먼저 말 걸기)

트리거 두 개.

1. **세션 시작 시 한 번.** "먼저 연락하고 싶은 사람 있어?" 를 묻고, 있다면 대화로 드래프트 확정 → `send.py`.
2. **사용자 명시 지시.** "엄마한테 생일 축하 보내" 같은 요청.

persona.md 에 "먼저 연락 금지" 가 있으면 1/2 모두 Claude 가 먼저 제안하지 않는다 (사용자가 강제로 시키면 따름).

cron 으로 돌지 않는다. 들어온 공은 AI 가 받아치고, 공을 던지는 건 사람이 한다.

## §Drafting 의 원칙

- persona.md (calibration 결과) 가 최우선 근거. 그 다음 최근 30개 컨텍스트.
- 불확실한 사실(일정, 주소, 금액) 은 꾸며내지 않는다. 차라리 짧게 얼버무리거나 skip.
- 사용자가 평소 쓰지 않는 어휘/이모지 추가 금지.
- "AI 톤" 금지: "도움이 되셨으면 좋겠어요" 류.
- 단체방: `sender` 필드 보고 본인의 과거 메시지 참고하되, 답 대상이 누구인지 명확히 확인. 혼선 시 skip.
- persona.md calibration 이 비어있으면 skip. calibration 안 된 상대엔 함부로 보내지 않는다.

## §Skip 의 기준

- 스팸/광고
- 사용자 본인만이 답해야 할 정보 (계좌, 주민번호)
- 맥락 부족해 꾸며내야 답 되는 경우
- calibration 미완료 상대

skip 은 조용히. 매번 사용자에게 보고 안 함. 세션 종료 시 요약으로 전달 가능.

## 에러 처리

- `cycle.sh poll` 자체 실패: 한 번 더 시도. 그래도 실패면 사용자에게 1줄 보고 후 해당 tick 종료. Cron 은 계속 돔.
- `send.py` 실패: `failed` 로 남김. 사용자에게 1줄 보고 — "용진한테 보내려 했는데 실패했어. 카톡 앱 확인해봐."

## 세션 종료

Claude Code 창 닫으면 Cron 도 같이 사라짐. 세션이 곧 데몬.
