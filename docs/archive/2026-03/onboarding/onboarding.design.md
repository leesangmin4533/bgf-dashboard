# Design: onboarding (발주핏 SaaS 온보딩 플로우)

> 버전 0.1 | 2026-03-14 | BGF리테일 (CU) 자동발주 시스템 SaaS 전환

## 1. 프로젝트 개요

점주가 브라우저에서 10분 안에 혼자 완료하는 5단계 온보딩 플로우.
초대 코드 기반 가입 → 매장 등록 → BGF 계정 연결 → 카테고리 선택 → 카카오톡(선택).

### 1.1 시스템 흐름도

```
[점주 브라우저]
    │
    ▼
┌────────────────────────────────────────────────┐
│  STEP 1: 회원가입                                │
│  초대코드 + username + password                   │
│  → invite_codes 검증 → dashboard_users 생성       │
│  → 자동 로그인 (session 설정)                     │
│  → onboarding_step = 1                           │
└──────────────┬─────────────────────────────────┘
               ▼
┌────────────────────────────────────────────────┐
│  STEP 2: 매장 등록                                │
│  store_code 입력 → 자동 조회 (stores 테이블)       │
│  → 매장명 확인 → dashboard_users 저장              │
│  → onboarding_step = 2                           │
└──────────────┬─────────────────────────────────┘
               ▼
┌────────────────────────────────────────────────┐
│  STEP 3: BGF 계정 연결                            │
│  bgf_id + bgf_password 입력                      │
│  → 비동기 Selenium 테스트 (ThreadPoolExecutor)     │
│  → 프론트 2초 폴링                                │
│  → 성공: Fernet 암호화 → DB 저장                   │
│  → onboarding_step = 3                           │
└──────────────┬─────────────────────────────────┘
               ▼
┌────────────────────────────────────────────────┐
│  STEP 4: 카테고리 선택                            │
│  기본 6개 전체 선택 상태                            │
│  → active_categories (쉼표 구분 mid_cd)            │
│  → onboarding_step = 4                           │
└──────────────┬─────────────────────────────────┘
               ▼
┌────────────────────────────────────────────────┐
│  STEP 5: 카카오톡 연결 (선택)                      │
│  "연결하기" 또는 "건너뛰기"                         │
│  → kakao_connected = 1 또는 0                     │
│  → onboarding_step = 5                           │
│  → 완료 화면                                      │
└────────────────────────────────────────────────┘
```

## 2. DB 스키마 변경 (v58)

### 2.1 dashboard_users 컬럼 추가

```sql
-- v58 마이그레이션
ALTER TABLE dashboard_users ADD COLUMN bgf_id TEXT;
ALTER TABLE dashboard_users ADD COLUMN bgf_password_enc TEXT;
ALTER TABLE dashboard_users ADD COLUMN store_code TEXT;
ALTER TABLE dashboard_users ADD COLUMN store_name TEXT;
ALTER TABLE dashboard_users ADD COLUMN onboarding_step INTEGER DEFAULT 0;
ALTER TABLE dashboard_users ADD COLUMN active_categories TEXT DEFAULT '001,002,003,004,005,012';
ALTER TABLE dashboard_users ADD COLUMN kakao_connected INTEGER DEFAULT 0;
```

### 2.2 invite_codes 테이블 (신규)

```sql
CREATE TABLE IF NOT EXISTS invite_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,           -- UUID4 기반 16자리 (하이픈 제거 앞 16자)
    store_id TEXT,                        -- 특정 매장용 코드 (NULL이면 범용)
    created_by INTEGER,                   -- 발급한 admin user_id
    used_by INTEGER,                      -- 사용한 user_id
    is_used INTEGER DEFAULT 0,
    expires_at TEXT,                       -- 만료 시각 (NULL이면 무기한)
    created_at TEXT NOT NULL,
    used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_invite_codes_code ON invite_codes(code);
```

### 2.3 onboarding_events 테이블 (신규, 분석용)

```sql
CREATE TABLE IF NOT EXISTS onboarding_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    step INTEGER NOT NULL,               -- 1~5
    action TEXT NOT NULL,                 -- started, completed, failed, skipped
    error_code TEXT,                       -- invalid_credentials, timeout 등
    duration_sec REAL,                    -- 해당 단계 소요 시간
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_onboarding_events_user ON onboarding_events(user_id);
```

### 2.4 마이그레이션 등록

`src/settings/constants.py`: `DB_SCHEMA_VERSION = 58`

`src/infrastructure/database/schema.py`의 `COMMON_MIGRATIONS`에 v58 추가:
```python
58: [
    "ALTER TABLE dashboard_users ADD COLUMN bgf_id TEXT",
    "ALTER TABLE dashboard_users ADD COLUMN bgf_password_enc TEXT",
    "ALTER TABLE dashboard_users ADD COLUMN store_code TEXT",
    "ALTER TABLE dashboard_users ADD COLUMN store_name TEXT",
    "ALTER TABLE dashboard_users ADD COLUMN onboarding_step INTEGER DEFAULT 0",
    "ALTER TABLE dashboard_users ADD COLUMN active_categories TEXT DEFAULT '001,002,003,004,005,012'",
    "ALTER TABLE dashboard_users ADD COLUMN kakao_connected INTEGER DEFAULT 0",
    """CREATE TABLE IF NOT EXISTS invite_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        store_id TEXT,
        created_by INTEGER,
        used_by INTEGER,
        is_used INTEGER DEFAULT 0,
        expires_at TEXT,
        created_at TEXT NOT NULL,
        used_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_invite_codes_code ON invite_codes(code)",
    """CREATE TABLE IF NOT EXISTS onboarding_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        step INTEGER NOT NULL,
        action TEXT NOT NULL,
        error_code TEXT,
        duration_sec REAL,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_onboarding_events_user ON onboarding_events(user_id)",
],
```

## 3. 암호화 유틸리티

### 3.1 파일: `src/utils/crypto.py`

```python
"""BGF 계정 비밀번호 Fernet 암복호화 유틸리티"""

import os
from cryptography.fernet import Fernet, InvalidToken

KEY_VERSION = "v1"

def _get_fernet() -> Fernet:
    key = os.environ.get("ORDERFIT_SECRET_KEY")
    if not key:
        raise ValueError("ORDERFIT_SECRET_KEY 환경변수가 설정되지 않았습니다")
    return Fernet(key.encode() if isinstance(key, str) else key)

def encrypt_password(plain_text: str) -> str:
    """평문 → 'v1:<encrypted>' 형태 문자열 반환"""
    f = _get_fernet()
    encrypted = f.encrypt(plain_text.encode()).decode()
    return f"{KEY_VERSION}:{encrypted}"

def decrypt_password(encrypted_text: str) -> str:
    """'v1:<encrypted>' → 평문 반환. 실패 시 ValueError."""
    try:
        # 버전 프리픽스 분리
        if ":" in encrypted_text:
            version, token = encrypted_text.split(":", 1)
        else:
            token = encrypted_text  # 레거시 호환
        f = _get_fernet()
        return f.decrypt(token.encode()).decode()
    except (InvalidToken, Exception):
        raise ValueError("복호화 실패")

def validate_secret_key():
    """서버 시작 시 호출. 키가 유효한지 검증."""
    key = os.environ.get("ORDERFIT_SECRET_KEY")
    if not key:
        raise ValueError("ORDERFIT_SECRET_KEY 환경변수가 설정되지 않았습니다. "
                         "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\" 로 생성하세요.")
    try:
        Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        raise ValueError("ORDERFIT_SECRET_KEY 형식이 올바르지 않습니다 (Fernet 키 필요)")
```

### 3.2 보안 규칙

- `decrypt_password()` 결과를 logger에 절대 출력 금지
- 암호화 키는 환경변수에서만 읽음 (코드/DB에 저장 금지)
- 키 생성: `Fernet.generate_key()` (base64 URL-safe 인코딩된 32바이트)
- UI 안내 문구: "AES 암호화" (Fernet=AES-128-CBC이므로 "AES-256" 사용 금지)

## 4. Repository

### 4.1 파일: `src/infrastructure/database/repos/onboarding_repo.py`

```python
class OnboardingRepository(BaseRepository):
    """온보딩 CRUD (common.db)"""
    db_type = "common"
```

### 4.2 메서드 시그니처

```python
# ── 초대 코드 ──
def create_invite_code(store_id: str | None, created_by: int,
                       expires_at: str | None = None) -> str:
    """초대 코드 생성. UUID4 앞 16자리 반환."""

def validate_invite_code(code: str) -> dict | None:
    """코드 유효성 검증. 유효하면 코드 정보 dict, 아니면 None.
    조건: is_used=0, expires_at 미경과."""

def use_invite_code(code: str, user_id: int) -> bool:
    """코드 사용 처리. is_used=1, used_by=user_id, used_at=now."""

# ── 온보딩 상태 ──
def get_onboarding_status(user_id: int) -> dict:
    """현재 단계, store_code, store_name, active_categories, kakao_connected 반환."""

def update_onboarding_step(user_id: int, step: int, **kwargs) -> None:
    """onboarding_step 업데이트 + 추가 필드(kwargs) 동시 갱신.
    단계 역행 방지: 새 step < 현재 step이면 무시."""

# ── STEP 2: 매장 ──
def save_store_info(user_id: int, store_code: str, store_name: str) -> None:
    """매장 정보 저장 + onboarding_step=2."""

# ── STEP 3: BGF 계정 ──
def save_bgf_credentials(user_id: int, bgf_id: str, bgf_password_enc: str) -> None:
    """암호화된 BGF 계정 저장 + onboarding_step=3."""

# ── STEP 4: 카테고리 ──
def save_categories(user_id: int, category_codes: list[str]) -> None:
    """쉼표 구분 문자열로 저장 + onboarding_step=4."""

# ── STEP 5: 완료 ──
def complete_onboarding(user_id: int, kakao_connected: bool = False) -> None:
    """onboarding_step=5, kakao_connected 업데이트."""

# ── 분석 이벤트 ──
def log_event(user_id: int, step: int, action: str,
              error_code: str | None = None, duration_sec: float | None = None) -> None:
    """onboarding_events에 이벤트 기록."""
```

## 5. Flask Blueprint

### 5.1 파일: `src/web/routes/onboarding.py`

```python
from flask import Blueprint
onboarding_bp = Blueprint("onboarding", __name__)
```

### 5.2 엔드포인트 상세

#### GET /onboarding — 온보딩 메인 페이지

```python
@onboarding_bp.route("/onboarding")
def onboarding_page():
    """온보딩 SPA 렌더링. 로그인 필요."""
    return render_template("onboarding.html")
```

#### GET /api/onboarding/status — 현재 단계 조회

```python
# 응답 예시
{
    "step": 2,
    "store_code": "46513",
    "store_name": "CU 동양대점",
    "active_categories": "001,002,003,004,005,012",
    "kakao_connected": false,
    "completed": false
}
```

#### POST /api/onboarding/signup — STEP 1 (비인증)

```python
# 요청
{"invite_code": "a1b2c3d4e5f6g7h8", "username": "owner01", "password": "pass1234",
 "full_name": "김점주", "phone": "010-1234-5678"}

# 처리 흐름
# 1. invite_code 유효성 검증 (is_used=0, 만료 미경과)
# 2. username 중복 검사
# 3. dashboard_users 생성 (role=viewer, store_id=초대코드의 store_id 또는 빈값)
# 4. invite_code 사용 처리
# 5. 자동 로그인 (session 설정)
# 6. onboarding_step = 1
# 7. onboarding_events 기록

# 응답 (성공)
{"success": true, "step": 1}

# 응답 (실패)
{"success": false, "error": "invalid_invite_code"}
{"success": false, "error": "username_taken"}
```

#### POST /api/onboarding/store — STEP 2

```python
# 요청
{"store_code": "46513"}

# 처리 흐름
# 1. store_code 검증: ^\d{5}$
# 2. stores 테이블에서 매장명 조회 (없으면 store_code 그대로 사용)
# 3. dashboard_users.store_code, store_name, store_id 업데이트
# 4. onboarding_step = 2

# 응답
{"success": true, "step": 2, "store_name": "CU 동양대점"}
```

#### POST /api/onboarding/store/lookup — 매장 자동 조회

기존 `StoreRepository.get_store(store_id)` 재사용 (common.db stores 테이블).

```python
# 요청
{"store_code": "46513"}

# 처리: StoreRepository().get_store(store_code) → store_name, location 반환

# 응답
{"found": true, "store_name": "CU 동양대점", "address": "..."}
{"found": false}
```

#### POST /api/onboarding/bgf/test — STEP 3 (비동기)

```python
# 요청
{"bgf_id": "myid", "bgf_password": "mypass"}

# 처리 흐름
# 1. bgf_id 검증: 4~20자 영숫자
# 2. Rate Limit 체크: 3회/300초
# 3. ThreadPoolExecutor에 Selenium 로그인 태스크 제출
# 4. task_id 즉시 반환

# 응답
{"task_id": "abc123"}

# Rate Limit 초과
{"success": false, "error": "rate_limited", "retry_after": 180}
```

**BGF 로그인 태스크 (백그라운드)**:
```python
def _bgf_login_task(task_id, bgf_id, bgf_password, user_id):
    """ThreadPoolExecutor에서 실행되는 BGF 로그인 테스트"""
    # 1. SalesAnalyzer 인스턴스 생성 (임시 store_id)
    # 2. setup_driver() → connect() → do_login(bgf_id, bgf_password)
    # 3. 성공: encrypt_password() → save_bgf_credentials() → task 결과 success
    # 4. 실패: task 결과 failed + error_code
    # 5. finally: driver.quit()
    # 주의: SalesAnalyzer.do_login() 재사용, 중복 구현 금지
```

#### GET /api/onboarding/bgf/status/\<task_id\> — 폴링

```python
# 응답 (진행중)
{"status": "running"}

# 응답 (성공)
{"status": "success", "step": 3}

# 응답 (실패)
{"status": "failed", "error": "invalid_credentials"}
{"status": "failed", "error": "server_error"}
{"status": "failed", "error": "timeout"}
```

**태스크 저장소**: 모듈 레벨 dict + TTL 정리
```python
_bgf_tasks = {}  # {task_id: {"status": "running|success|failed", "error": None, "created_at": time.time()}}
_executor = ThreadPoolExecutor(max_workers=2)
BGF_TEST_TIMEOUT = 90  # 초 — cold start(15s) + 넥사크로 로딩(6s) + 로그인(5s) + 여유

# 5분 이상 된 태스크 자동 정리 (메모리 누수 방지)
```

#### POST /api/onboarding/categories — STEP 4

```python
# 요청
{"categories": ["001", "002", "003", "004", "005", "012"]}

# 처리 흐름
# 1. 유효한 mid_cd인지 검증 (VALID_FOOD_CATEGORIES 상수 대조)
# 2. 최소 1개 이상 선택 확인
# 3. 쉼표 구분 문자열로 저장
# 4. onboarding_step = 4

# 응답
{"success": true, "step": 4}
```

#### POST /api/onboarding/complete — STEP 5

```python
# 요청
{"kakao_connected": false}  # 또는 true

# 처리 흐름
# 1. onboarding_step = 5
# 2. kakao_connected 업데이트
# 3. onboarding_events 기록

# 응답
{"success": true, "step": 5, "completed": true}
```

### 5.3 Rate Limiting

BGF 테스트 엔드포인트 전용 Rate Limit 추가:

`src/web/middleware.py`의 `RateLimiter.endpoint_limits`에:
```python
'/api/onboarding/bgf/test': 3,  # 3회/300초
```

### 5.4 Blueprint 등록

`src/web/routes/__init__.py`:
```python
from .onboarding import onboarding_bp
app.register_blueprint(onboarding_bp)  # url_prefix 없음 (/onboarding은 페이지, /api/onboarding/...은 API)
```

### 5.5 Middleware 온보딩 리다이렉트

`src/web/middleware.py`의 `check_auth_and_store_access()`:
```python
# 공개 경로에 추가
_PUBLIC_PREFIXES = (
    "/api/auth/login", "/api/auth/logout", "/api/auth/signup",
    "/api/onboarding/signup",  # 신규: 비인증 가입
    "/login", "/static/",
)

# 인증 통과 후, 온보딩 미완료 리다이렉트 추가
def check_auth_and_store_access():
    # ... 기존 인증 로직 ...

    # 온보딩 미완료 체크 (API 경로, /onboarding, /static 제외)
    if (not path.startswith("/api/") and
        not path.startswith("/onboarding") and
        not path.startswith("/static/")):
        user = _get_current_user()  # session에서 user_id로 DB 조회
        if user and user.get("onboarding_step", 5) < 5:
            return redirect("/onboarding")
```

## 6. 프론트엔드 (SPA)

### 6.1 파일: `src/web/templates/onboarding.html`

ES5 Vanilla JS, 기존 login.html/index.html 스타일 그대로 따름.

### 6.2 UI 구조

```html
<div id="onboarding-container">
    <!-- 진행 표시 바 -->
    <div id="progress-bar">
        <span class="step active" data-step="1">1. 회원가입</span>
        <span class="step" data-step="2">2. 매장 등록</span>
        <span class="step" data-step="3">3. BGF 연결</span>
        <span class="step" data-step="4">4. 카테고리</span>
        <span class="step" data-step="5">5. 알림 설정</span>
    </div>

    <!-- 각 단계 (한 번에 하나만 표시) -->
    <div id="step-1" class="step-panel"> ... </div>
    <div id="step-2" class="step-panel hidden"> ... </div>
    <div id="step-3" class="step-panel hidden"> ... </div>
    <div id="step-4" class="step-panel hidden"> ... </div>
    <div id="step-5" class="step-panel hidden"> ... </div>
    <div id="step-complete" class="step-panel hidden"> ... </div>
</div>
```

### 6.3 STEP 1 — 회원가입

```
초대 코드  [________________]
아이디     [________________]
비밀번호   [________________]
비밀번호 확인 [________________]
이름       [________________]
연락처     [________________]

[ 가입하기 ]
```

- 초대 코드 필수, 유효하지 않으면 에러 표시
- 비밀번호 확인 일치 검증 (클라이언트)
- 성공 시 자동 로그인 → STEP 2로 이동

### 6.4 STEP 2 — 매장 등록

```
매장 코드  [_____]  [ 조회 ]

  ✅ CU 동양대점
  주소: 경북 영주시 ...

[ 다음 ]
```

- 5자리 숫자 검증 (클라이언트)
- "조회" 클릭 → `/api/onboarding/store/lookup` 호출
- 매장명 확인 후 "다음" 활성화

### 6.5 STEP 3 — BGF 계정 연결

```
🔒 BGF 스토어 시스템 계정 연결

BGF 아이디  [________________]
BGF 비밀번호 [________________]

입력하신 정보는 AES 암호화 후 저장됩니다.
발주 실행에만 사용되며, 외부에 공개되지 않습니다.

[ BGF 계정 연결 테스트 ]

◻ 연결 확인 중... (스피너)
✅ 연결 완료!
❌ 아이디 또는 비밀번호를 확인해 주세요
❌ BGF 시스템 점검 중입니다. 잠시 후 다시 시도해 주세요

[ 다음 ]  ← 연결 성공 후 활성화
```

- "연결 테스트" 클릭 → POST bgf/test → task_id 받음
- 2초 간격 GET bgf/status/\<task_id\> 폴링
- 최대 90초 타임아웃 (45회 폴링) — Selenium cold start(10~15초) + 넥사크로 로딩 고려
- 성공 시 "다음" 활성화

### 6.6 STEP 4 — 카테고리 선택

```
발주 자동화할 카테고리를 선택하세요.

☑ 도시락 (001)  마진율 28.8%
☑ 주먹밥 (002)  마진율 38.8%
☑ 김밥   (003)  마진율 31.9%
☑ 샌드위치(004)  마진율 33.8%
☑ 햄버거 (005)  마진율 34.7%
☑ 빵     (012)  마진율 38.3%

[ 다음 ]
```

- 기본값: 전체 선택
- 최소 1개 이상 선택 필수
- 체크박스 터치 타겟: 48px 이상 (모바일)

### 6.7 STEP 5 — 카카오톡 연결

```
📱 카카오톡 알림 연결 (선택)

매일 아침 발주 완료 알림을 카카오톡으로 받을 수 있습니다.
폐기 위험 상품, 행사 변경 알림도 함께 받아보세요.

[ 카카오톡 연결하기 ]
[ 나중에 하기 ]      ← 스킵 가능
```

### 6.8 완료 화면

```
✅ 설정 완료!

내일 아침 7시, 발주핏이 자동으로 푸드 발주를 완료합니다.
(카카오톡 연결 시) 카카오톡으로 결과 알림을 보내드립니다.

[ 발주 미리보기 ]   ← /api/order/predict 호출 (dry_order)
[ 대시보드로 이동 ]  ← /
```

### 6.9 JavaScript 구조 (ES5)

```javascript
(function() {
    var currentStep = 1;
    var bgfTaskId = null;
    var pollInterval = null;

    // 페이지 로드 시 서버에서 현재 단계 조회
    function init() {
        fetch('/api/onboarding/status')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.completed) {
                    window.location.href = '/';
                    return;
                }
                currentStep = (data.step || 0) + 1;  // 다음 단계로
                showStep(currentStep);
            });
    }

    function showStep(step) {
        // 모든 step-panel 숨기기
        // step-N 표시
        // progress-bar 업데이트
    }

    function submitStep1() { /* POST /api/onboarding/signup */ }
    function submitStep2() { /* POST /api/onboarding/store */ }
    function testBgf()     { /* POST /api/onboarding/bgf/test → 폴링 시작 */ }
    function pollBgfStatus() { /* GET /api/onboarding/bgf/status/<task_id> */ }
    function submitStep4() { /* POST /api/onboarding/categories */ }
    function submitStep5(skip) { /* POST /api/onboarding/complete */ }

    document.addEventListener('DOMContentLoaded', init);
})();
```

### 6.10 반응형 (모바일)

```css
@media (max-width: 768px) {
    .onboarding-container { padding: 16px; max-width: 100%; }
    .step-panel input { font-size: 16px; }  /* iOS 확대 방지 */
    .category-checkbox { min-height: 48px; } /* 터치 타겟 */
    .progress-bar { font-size: 12px; flex-wrap: wrap; }
}
```

## 7. 초대 코드 발급 CLI

### 7.1 파일: `scripts/generate_invite_code.py`

```bash
# 사용법
python scripts/generate_invite_code.py                    # 범용 코드 1개
python scripts/generate_invite_code.py --store 46513      # 특정 매장용
python scripts/generate_invite_code.py --count 5          # 5개 일괄
python scripts/generate_invite_code.py --expires 7d       # 7일 후 만료
```

## 8. 구현 순서

```
1. DB 마이그레이션 (schema.py v58 + constants.py 버전 증가)
2. src/utils/crypto.py (Fernet 암복호화)
3. src/infrastructure/database/repos/onboarding_repo.py (Repository)
4. tests/test_onboarding.py (테스트 먼저 작성)
5. src/web/routes/onboarding.py (Blueprint + 엔드포인트)
6. src/web/templates/onboarding.html (프론트엔드 SPA)
7. src/web/routes/__init__.py (Blueprint 등록)
8. src/web/middleware.py (온보딩 리다이렉트)
9. scripts/generate_invite_code.py (CLI 도구)
10. 전체 테스트 실행 + 기존 테스트 회귀 확인
```

## 9. 테스트 명세

### 9.1 파일: `tests/test_onboarding.py`

```python
# ── crypto ──
class TestCrypto:
    test_encrypt_decrypt_roundtrip       # 암호화 → 복호화 = 원문
    test_encrypt_no_plaintext            # 암호화 결과에 원문 미포함
    test_encrypt_key_version_prefix      # "v1:" 프리픽스 확인
    test_decrypt_invalid_token           # 잘못된 토큰 → ValueError
    test_validate_secret_key_missing     # 키 없으면 ValueError

# ── invite code ──
class TestInviteCode:
    test_create_invite_code              # 코드 생성 (16자리)
    test_validate_valid_code             # 유효 코드 검증 성공
    test_validate_used_code              # 사용 완료 코드 → None
    test_validate_expired_code           # 만료 코드 → None
    test_use_invite_code                 # 사용 처리 (is_used=1, used_by 설정)

# ── onboarding repo ──
class TestOnboardingRepo:
    test_save_store_info                 # 매장 저장 + step=2
    test_save_bgf_credentials_encrypted  # DB에 평문 미저장
    test_save_categories                 # 카테고리 저장/조회
    test_step_progression                # 단계 순서 보장
    test_step_no_regression              # step=3에서 step=1 불가
    test_complete_onboarding             # step=5 + completed
    test_log_event                       # 이벤트 기록

# ── API ──
class TestOnboardingAPI:
    test_signup_with_valid_invite        # 초대 코드 가입 성공
    test_signup_invalid_invite           # 잘못된 코드 → 에러
    test_signup_username_taken           # 중복 username → 에러
    test_store_lookup                    # 매장 조회
    test_store_invalid_code              # 잘못된 store_code → 에러
    test_bgf_test_start                  # task_id 반환
    test_bgf_test_rate_limit             # 3회 초과 → 429
    test_categories_save                 # 카테고리 저장
    test_categories_empty                # 빈 목록 → 에러
    test_complete_with_kakao             # 카카오 연결 완료
    test_complete_skip_kakao             # 카카오 건너뛰기 완료
    test_redirect_incomplete_onboarding  # step<5 → /onboarding
    test_no_redirect_api                 # API 경로는 리다이렉트 안 함
```

### 9.2 Fixtures

```python
@pytest.fixture
def onboarding_db(tmp_path):
    """테스트용 common.db (dashboard_users + invite_codes + onboarding_events)"""

@pytest.fixture
def repo(onboarding_db):
    return OnboardingRepository(db_path=onboarding_db)

@pytest.fixture
def app(onboarding_db):
    """Flask test client with onboarding_bp registered"""

@pytest.fixture
def env_secret_key(monkeypatch):
    monkeypatch.setenv("ORDERFIT_SECRET_KEY", Fernet.generate_key().decode())
```

## 10. 기존 코드 영향 범위

| 파일 | 변경 내용 | 위험도 |
|------|----------|--------|
| `src/settings/constants.py` | DB_SCHEMA_VERSION 57→58 | 낮음 (마이그레이션 자동) |
| `src/infrastructure/database/schema.py` | v58 마이그레이션 추가 | 낮음 (ALTER TABLE만) |
| `src/web/routes/__init__.py` | onboarding_bp import + 등록 1줄 | 낮음 |
| `src/web/middleware.py` | _PUBLIC_PREFIXES 1개 추가 + 리다이렉트 조건 5줄 | 중간 (기존 라우팅 영향) |

**신규 파일 (6개)**:
- `src/utils/crypto.py`
- `src/infrastructure/database/repos/onboarding_repo.py`
- `src/web/routes/onboarding.py`
- `src/web/templates/onboarding.html`
- `scripts/generate_invite_code.py`
- `tests/test_onboarding.py`
