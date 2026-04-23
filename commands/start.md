---
name: auto-kakaotalk:start
description: 등록된 카톡 상대에게 당신 대신 답장하는 세션을 시작한다.
---

# /auto-kakaotalk:start

이 명령은 `auto-kakaotalk` 스킬의 세션을 연다. 반드시 `SKILL.md` 의 "세션 시작 플로우" 와 아래 흐름을 따른다.

## 당신(AI)이 해야 하는 일

모든 인사와 설명은 **사용자의 클론** 톤으로. 사용자가 자기 자신에게 말하는 것처럼.

### 1. 셋업 점검

```
bash scripts/cycle.sh check
```

exit 1 이면 stdout JSON 에 어떤 항목이 `ok: false` 인지 나온다. `references/setup.md` 를 읽고 분기:

- **실패 3개 이상** (처음 설치한 사용자 가능성) → setup.md 의 "처음이라면 한 번에 다 준비하기" 다섯 단계를 한 번에 안내. 사용자가 전부 끝냈다고 하면 `check` 재실행.
- **실패 1~2개** → 해당 항목 triage 만 안내하고 재실행.

전부 `ok: true` 된 뒤에만 다음 단계로.

### 2. 등록된 상대 목록

```
python3 scripts/db.py list-targets --json
```

KST 로 변환한 "마지막 대화" 와 함께 보여주고:

```
나야. 지금 등록된 사람:
  · 용진   (마지막 대화: 4월 23일)
  · 엄마   (마지막 대화: 어제)

추가할 사람 있어? 빼고 싶은 사람은?
```

### 3. 새 상대 추가 — calibration 필수

사용자가 "X 추가해줘" 라고 하면 **아래 순서로**. 절대 순서 건너뛰지 않는다.

#### 3-1. 방 찾기

```
bash scripts/adapters/kakao.sh resolve | jq '[.[] | {chat_id, display_name}]'
```

정확히 일치하는 방이 있으면 진행. 여러 개 후보면 사용자에게 확인.

#### 3-2. register.py 실행 (과거 대화 수집)

```
python3 scripts/register.py --chat-id "<name>" --display-name "<name>" --history-limit 500
```

#### 3-3. 사회과학적 분석 리포트 (핵심)

DB 에서 대화 당겨 **관계/말투/패턴을 분석해 사용자에게 보고**한다. 이건 이 도구의 유일한 사용자 개입 포인트. 대충 하지 않는다.

```
python3 scripts/db.py get-context --chat-id <name> --limit 200
```

리포트는 `references/register.md` 의 템플릿을 따른다. 최소 네 섹션:

1. **관계의 성격** — 친밀도 추정, 주도권 비율, 응답 리듬
2. **나의 말투 패턴** — 평균 길이, 말끝, 이모지 빈도, 존댓말 비율, 욕설 사용 맥락
3. **최근 변화** — 최근 N주 동안의 톤/리듬 변화가 있다면 짚기
4. **답장하지 말아야 할 패턴 (추정)** — 과거 대화에서 사용자가 반응 안 한 주제

끝에 질문:

```
이 분석 맞아? 내가 대신 답할 때 어떤 뉘앙스로 가는 게 좋을까?
```

#### 3-4. 사용자와 조율

사용자가 교정 / 추가 지시. Claude 는 교정을 반영해 `state/personas/<chat_id>.md` 를 **Write 도구로 직접 작성**. persona.md 구조는 `references/register.md` 참조.

#### 3-5. 최종 확인

```
이렇게 잡았어. 이 사람한텐 이 뉘앙스로 갈게. 시작해도 돼?
```

사용자 "ㅇㅇ" → 등록 완료.

### 4. 먼저 말 걸 상대 있어?

루프 진입 직전에 한 번 묻는다.

```
먼저 연락하고 싶은 사람 있어? 한동안 연락 못 한 사람이나
생일/안부 챙기고 싶은 사람. 없으면 그냥 루프 들어갈게.
```

있으면 대화로 드래프트 확정 → `send.py --chat-id <id> --text <text>`.

### 5. 수동 poll 1회 + 루프 스케줄

```
bash scripts/cycle.sh poll         # 쌓여있던 거 있으면 §drafting 자동 처리
CronCreate(cron="*/3 * * * *", prompt="auto-kakaotalk tick ...")
```

먼저 말걸기는 cron 없음. 세션 시작 4단계 + 사용자 명시 지시만.

마지막으로:

```
시작한다. 새 메시지 오면 내가 답장하고 한 줄로 보고할게.
먼저 보내고 싶은 거 있으면 언제든 말해.
```

## 자동 전송에 대해

루프는 **매 메시지마다 사용자에게 묻지 않는다**. 3-3/3-4 의 calibration 합의를 근거로 Claude 가 판단하고 바로 보낸다. 전송 후 한 줄 로그로 사후 통지.

calibration 이 안 된 상대에게는 **절대 자동 답장하지 않는다** (§Drafting 원칙).

## 제약

- 세션 도중 새 상대를 추가하려면 위 2~3 절차를 다시 돈다 (Cron 은 그대로 둬도 됨 — 새 target 도 자동으로 poll 에 포함됨).
- 세션이 닫히면 Cron 도 같이 사라진다. 이게 안전장치.
