# 초기 셋업

## 이 문서를 언제 읽나

`bash scripts/cycle.sh check` 가 실패했을 때. stdout 에 `ok: false` 로 찍힌 항목마다 아래 해당 섹션을 보고 사용자에게 한국어로 안내한다.

분기:

- **실패 항목이 3개 이상**: 처음 설치한 사용자일 가능성이 높음. "처음이라면 한 번에 다 준비하기" 를 먼저 안내해서 한 번에 끝낸다.
- **실패 항목이 1~2개**: 기존 사용자 환경 일부 깨짐. 해당 항목 triage 만 안내.

## 안내 원칙

- CLI 에러 원문 그대로 노출 금지. 사람 말로 번역.
- 명령은 복붙할 수 있게 통째로.
- System Settings 경로는 영문 메뉴명도 함께 (환경에 따라 표시 다름).
- 한 번에 한 스텝씩. 다 끝난 뒤 `다시 check 돌려볼게` 라고 명시.

---

## 처음이라면 한 번에 다 준비하기

실패가 여러 개면 이걸 한 번에 통으로 안내한다. 다섯 단계, 5~10분.

### 1. KakaoTalk Mac 앱 설치 + 로그인

```bash
# Homebrew 로 Mac App Store 바이너리 설치하려면 mas 필요
brew install mas
mas install 869223134   # KakaoTalk for Mac
```

`mas install` 이 `Not signed in` 으로 실패하면 먼저 App Store 앱에서 로그인.

그리고 카카오톡 Mac 앱을 열어 카카오 계정으로 로그인. 이 스킬이 읽어야 할 로컬 DB 가 로그인 시점에 만들어진다.

### 2. 필요한 브루 바이너리 두 개

```bash
brew install cliclick
brew install silver-flight-group/tap/kakaocli
```

- `cliclick`: 카톡 UI 에 메시지 붙여넣기/Enter 처리에 사용
- `kakaocli`: 카톡의 암호화된 로컬 DB (SQLCipher) 쿼리

### 3. 터미널에 Full Disk Access 권한

System Settings > Privacy & Security > Full Disk Access 에서 현재 터미널 앱 (iTerm, Terminal, Warp 등) 토글 켜기. 권한 켠 뒤 터미널 재시작.

이게 없으면 카톡 DB 파일 자체를 읽지 못한다. 에러가 조용히 난다.

### 4. 터미널에 Accessibility 권한

System Settings > Privacy & Security > Accessibility 에서 같은 터미널 앱 토글 켜기. 권한 켠 뒤 터미널 재시작.

이게 없으면 AppleScript 가 실패해서 메시지 전송이 안 된다.

### 5. 확인

```bash
bash scripts/cycle.sh check
# {"ok":true} 나오면 통과
```

전부 통과하면 본 플로우로 돌아간다. 한 번만 하면 재부팅해도 유지된다.

---

## 개별 항목 triage (한두 개만 실패할 때)

### `macos: false`

지원 중단. 다른 OS 는 안 된다. 사용자에게 알리고 종료.

### `kakaotalk_app: false`

```bash
brew install mas
mas install 869223134
```

`mas install` 이 막히면 App Store 앱 먼저 로그인.

### `cliclick: false`

```bash
brew install cliclick
```

### `kakaocli: false`

```bash
brew install silver-flight-group/tap/kakaocli
```

tap 추가가 필요한 구조. `Error: No available formula` 가 뜨면 tap 이 캐시에서 빠진 상태. `brew update` 후 재시도.

### `kakao_auth: false`

원인이 둘 중 하나. 순서대로 확인:

**1. Full Disk Access 권한 없음.**

System Settings > Privacy & Security > Full Disk Access 에서 현재 터미널 앱 토글 켜기. 켠 뒤 터미널 재시작.

**2. KakaoTalk 에 로그인 안 된 상태.**

카카오톡 Mac 앱을 열어 로그인. 로그인 후 강제 재해석:

```bash
python3 scripts/adapters/_kakao_auth.py auth --refresh
```

그래도 안 되면 user_id 자동 감지 실패일 수 있다. SHA-512 preimage search 로 찾아주긴 하는데 시간이 좀 걸린다 (최대 1,000,000,000 까지 스캔). 기본 설정으로 보통 수십 초 안에 끝남.

### `accessibility: false`

System Settings > Privacy & Security > Accessibility 에서 터미널 앱 토글 켜기. 켠 뒤 터미널 재시작. 이 권한이 없으면 메시지 전송이 조용히 실패한다.

---

## 전부 통과한 뒤

```bash
bash scripts/cycle.sh check
# {"ok":true}
```

이게 나오면 `/auto-kakaotalk:start` 로 돌아가 상대 등록 플로우 진행.

## 한 번 셋업한 뒤

위 설정은 재부팅·재로그인해도 유지된다. 같은 Mac 에서 다시 물을 필요 없음. 단 카톡 앱 버전이 크게 업데이트되면 auth 가 한 번 깨질 수 있는데, `--refresh` 로 재해석하면 대개 복구된다.
