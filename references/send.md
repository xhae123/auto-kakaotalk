# 전송 2-phase

## 왜 2-phase 인가

전송 경로는 AppleScript UI 자동화다. 카톡 Mac 앱 입력창에 글자를 찍고 Enter 를 누르는 것. 한 번 누르면 되돌릴 수 없고, 앱 상태 (어떤 방이 열려 있는지) 에 따라 **엉뚱한 방에 갈 수도** 있다. 그래서 최악의 시나리오는 **중복 전송**이다. "못 보냈다" 보다 "두 번 보냈다" 가 비교할 수 없이 나쁘다.

2-phase 는 이 비대칭을 반영한 설계다.

## 상태 전이

```
drafted  --(phase 1: DB commit)-->  sending
sending  --(phase 2: adapter)---->  sent   (정상)
sending  --(phase 2 fail)--------->  failed (adapter 예외)
sending  --(process crash)-------->  (stuck)
(stuck)  --(next session recover)->  failed
```

## Phase 1: DB 먼저 커밋

`send.py` 는 adapter 를 호출하기 **전에** outbound 행을 `sending` 으로 flip 하고 commit 한다. 여기서 프로세스가 죽어도 상태는 남는다.

## Phase 2: adapter 호출

`osascript send_via_ui.applescript <방이름> <본문> send` 를 실행. 성공 시 exit 0, 실패 시 exit != 0.

```bash
bash scripts/adapters/kakao.sh send "<display_name>" "<text>"
```

## Phase 3: 결과 반영

- exit 0 → `state = 'sent'`, `body = draft_text`, 대응하는 inbound 를 `responded` 로.
- exit != 0 → `state = 'failed'`, `error = adapter stderr 앞 500자`. 대응하는 inbound 는 `queued` 로 유지 (다음 cycle 에 재시도 검토).

## 크래시 복구

세션 시작 시 `cycle.sh check` 가 `db.py recover` 를 호출한다. `sending` 에 남아 있는 행은 **보수적으로 `failed` 처리**.

판단 근거: AppleScript 가 Enter 까지 성공했는지 우리는 알 길이 없다.
- 진짜로 보냈다면 → 다음 poll 에서 outbound 로 돌아온다. 사람은 중복 전송 당하지 않는다.
- 안 보냈다면 → 실제로 실패. failed 로 표시된 게 맞다.

즉 복구 정책은 **false negative 쪽으로 안전하게**. 필요 시 사용자가 수동으로 재전송.

## 사용 패턴

### 패턴 A: 답장 (대응 inbound 있음)
```bash
python3 scripts/send.py --reply-to <inbound_id> --text "<답장>"
```

### 패턴 B: 먼저 말 걸기 (대응 inbound 없음)
```bash
python3 scripts/send.py --chat-id <chat_id> --text "<본문>"
```

### 패턴 C: 드래프트/전송 분리
```bash
python3 scripts/db.py draft --reply-to <inbound_id> --text "..."
# or: python3 scripts/db.py draft --chat-id <chat_id> --text "..."
python3 scripts/send.py --outbound-id <outbound_id>
```

루프 답장은 A, 먼저 말 걸기는 B, 신규 상대/민감한 상대는 C 를 고려.

## 금기

- adapter 를 루프나 다른 스크립트에서 직접 호출하지 않는다. 반드시 `send.py` 경유. DB 상태 누락의 원천이 된다.
- 같은 outbound_id 에 대해 send.py 를 두 번 호출하지 않는다. state 가드가 있지만 경쟁 조건이 아예 안 나게 하는 건 단일 진입점뿐이다.
