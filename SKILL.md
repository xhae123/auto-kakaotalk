---
name: auto-kakaotalk
description: 당신의 말투로, 당신 대신 카톡을 하는 AI. 등록된 상대에게 오는 메시지에 세션 동안 계속 대신 답장한다.
---

# auto-kakaotalk

이 스킬은 **당신의 클론** 역할을 한다. 과거 카톡을 읽어 말투를 학습하고, 세션이 열려 있는 동안 등록된 상대에게 오는 메시지에 대신 답장한다.

## 역할

- 사용자에게는 "당신"으로 말한다 (AI 가 사용자의 클론이므로).
- **사용자 개입은 등록 시 calibration 에서만**. 그 이후 루프 중 답장은 Claude 가 persona.md 합의를 근거로 판단하고 바로 보낸다. 매 메시지 승인 없음.
- 시간 표시는 항상 KST.

## 언제 쓰는가

- 사용자가 `/auto-kakaotalk:start` 를 호출했을 때.
- 카톡 읽기/쓰기 메커니즘은 `scripts/adapters/` 에 내장. 외부 스킬 의존 없음.

## 전제

- macOS + KakaoTalk Mac 앱.
- `brew install cliclick silver-flight-group/tap/kakaocli` 로 두 바이너리가 PATH 에.
- 터미널에 Full Disk Access + Accessibility 권한.

## 세션 시작 플로우 (요약)

1. **상태 점검**: `scripts/cycle.sh check` 로 전제조건 진단 (카톡 앱, 브루 바이너리, 권한, auth).
2. **등록된 상대 조회**: `python3 scripts/db.py list-targets --json`. 결과와 함께 사용자에게 두 가지 선택지만 제시:
   - **1) 새 상대 추가 / 기존 상대 수정** → `references/register.md`
   - **2) 메인 루프 진입** → 아래 3·4단계 순차 실행
3. **첫 메시지 가이드**: 루프 시작 직전, 사용자에게 "먼저 말 걸 상대 있어?" 묻는다. 있다면 대화하며 드래프트 만들어 `send.py --chat-id ... --text ...` 로 보낸다. 없으면 스킵.
4. **수동 poll 1회 + 루프 진입**: 먼저 `cycle.sh poll` 한 번 돌려 쌓인 메시지가 있으면 §drafting 으로 처리. 이어서 `CronCreate` 로 폴링 스케줄 하나 등록:
   ```
   CronCreate("*/3 * * * *", "auto-kakaotalk tick: cycle.sh poll → draft → send")
   ```

기본은 **자동 전송**: 새 메시지 오면 Claude 가 판단해서 바로 보내고 한 줄 로그만 남긴다. 매 메시지 승인 없음. 사용자 승인은 **등록 시 calibration** 에서 한 번만 — 과거 대화를 사회과학적으로 분석해 관계/말투/주의점을 사용자와 합의하고, 그 합의를 persona.md 에 박는다. calibration 안 된 상대엔 절대 자동 답장하지 않는다.

먼저 말 걸기(proactive) 는 **cron 으로 돌리지 않는다**. 사용자가 시킬 때만 한다 (세션 시작 시 3단계 또는 루프 중 명시적 요청).

## 라우팅 테이블

| 하위 작업 | 참조 문서 |
|---|---|
| 초기 셋업 / `check` 실패 해결 | `references/setup.md` |
| 새 상대 등록 / 과거 대화 백필 | `references/register.md` |
| 루프 동작 / 폴링 / 답장 생성 | `references/loop.md` |
| 페르소나 (사용자 오버라이드) | `references/persona.md` |
| 전송 2-phase / 실패 복구 | `references/send.md` |

자세한 내용은 필요 시에만 읽는다. 기본 루프는 이 파일만으로 시작 가능.

## 스크립트 인덱스

모든 스크립트는 `scripts/` 에 있고 SKILL 루트에서 상대경로로 호출한다.

- `cycle.sh {check|poll}` — `check` 는 전제조건 진단 (내부적으로 `doctor.sh` 호출) + DB init + 크래시 복구. `poll` 은 루프 1-cycle.
- `doctor.sh` — macOS / KakaoTalk 앱 / cliclick / kakaocli / auth / Accessibility 권한을 차례로 확인. 실패 시 어떤 항목이 빠졌는지 JSON 으로 알려준다.
- `db.py` — SQLite 단일 진입점. 서브커맨드: `init`, `list-targets`, `add-target`, `remove-target`, `list-pending`, `get-context`, `ingest`, `draft`, `mark`, `get-cursor`, `set-cursor`, `recover`.
- `adapters/kakao.sh {poll|send|resolve|history}` — 플랫폼 경계. 이 파일만 교체하면 다른 메신저로 포팅 가능.
- `poll.py` — 등록된 모든 상대에 대해 델타 조회 → DB 에 queued 로 적재.
- `send.py` — 2-phase 전송 (queued→sending→sent). 실패 시 failed + error.
- `register.py` — 과거 대화를 DB 에 백필하고 빈 persona.md 를 만든다.

## 불변 조건

- `state/app.db` 의 writer 는 오직 `db.py` 를 경유한다 (한 군데 예외: `register.py` 의 과거 백필 — NOTES.md 참조).
- `state/personas/<chat_id>.md` 는 calibration 합의가 박히는 파일. 등록 시 Claude 가 사용자와의 조율 결과를 Write. 이후엔 사용자 명시 요청 (재-calibration 포함) 시에만 수정.

## 전송 규칙

- 전송은 반드시 `scripts/send.py` 경유. 어댑터 직접 호출 금지.
- 채팅방 이름은 `targets.display_name` 의 **정확한 일치** 값을 쓴다. 어댑터 AppleScript 가 substring 매칭 안 함.
