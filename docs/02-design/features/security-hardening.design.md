# Design: security-hardening

> Plan 참조: `docs/01-plan/features/security-hardening.plan.md`

## 1. 아키텍처 변경

### 변경 전
```
요청 -> Flask 라우트 -> 응답 (무방비)
```

### 변경 후
```
요청 -> [보안 헤더] -> [접근 로깅] -> [Rate Limiter] -> Flask 라우트 -> 응답
```

### 파일 변경 맵

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `src/web/app.py` | 수정 | 보안 헤더, 접근 로깅 after/before_request 추가 |
| `src/web/middleware.py` | **신규** | RateLimiter 클래스 |
| `src/application/services/store_service.py` | 수정 | 비밀번호 해싱, stores.json 비밀번호 필드 제거 |
| `src/infrastructure/database/schema.py` | 수정 | stores 테이블 bgf_password 컬럼 주석 변경 |
| `src/db/models.py` | 수정 | SCHEMA_MIGRATIONS에 v34 추가 |
| `src/settings/constants.py` | 수정 | DB_SCHEMA_VERSION 34 |
| `requirements.txt` | 수정 | flask 추가, 버전 고정 |
| `tests/test_web_security.py` | **신규** | 보안 테스트 |

## 2. 상세 설계

### 2.1 보안 헤더 (`src/web/app.py`)

`create_app()` 내 `@app.after_request`에 추가:

```python
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    # CSP: self + CDN (차트 라이브러리 등)
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'"
    )
    return response
```

### 2.2 접근 로깅 (`src/web/app.py`)

`@app.before_request`에 추가:

```python
@app.before_request
def log_request():
    logger.info(f"[API] {request.method} {request.path} from {request.remote_addr}")
```

### 2.3 Rate Limiter (`src/web/middleware.py`)

외부 의존성 없이 인메모리 슬라이딩 윈도우 방식:

```python
import time
import threading
from collections import defaultdict
from flask import request, jsonify

class RateLimiter:
    def __init__(self, default_limit=60, window_seconds=60):
        self._requests = defaultdict(list)  # {ip: [timestamp, ...]}
        self._lock = threading.Lock()
        self.default_limit = default_limit
        self.window_seconds = window_seconds
        # 엔드포인트별 제한 (무거운 연산)
        self.endpoint_limits = {
            '/api/order/run-script': 5,
            '/api/order/predict': 10,
            '/api/report/baseline': 5,
        }

    def check(self):
        """before_request에서 호출. 429 반환 시 차단."""
        ip = request.remote_addr
        if ip == '127.0.0.1':
            return None  # localhost 제외

        limit = self.endpoint_limits.get(request.path, self.default_limit)
        now = time.time()
        cutoff = now - self.window_seconds

        with self._lock:
            self._requests[ip] = [t for t in self._requests[ip] if t > cutoff]
            if len(self._requests[ip]) >= limit:
                return jsonify({"error": "요청 빈도 제한 초과"}), 429
            self._requests[ip].append(now)

        return None
```

`app.py`에서 적용:

```python
from src.web.middleware import RateLimiter

rate_limiter = RateLimiter(default_limit=60, window_seconds=60)

@app.before_request
def check_rate_limit():
    result = rate_limiter.check()
    if result:
        return result
```

### 2.4 비밀번호 해싱

hashlib 사용 (외부 의존성 없음):

```python
# src/application/services/store_service.py 내부
import hashlib
import os

def _hash_password(password: str) -> str:
    """SHA-256 + salt 해싱"""
    salt = os.urandom(16).hex()
    hashed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}${hashed}"

def _verify_password(password: str, stored: str) -> bool:
    """해싱된 비밀번호 검증"""
    if '$' not in stored:
        # 레거시 평문 비밀번호 -> 비교 후 True면 마이그레이션
        return password == stored
    salt, hashed = stored.split('$', 1)
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest() == hashed
```

#### stores.json 변경

```python
# 변경 전
data.setdefault("stores", []).append({
    ...
    "bgf_user_id": bgf_user_id,
    "bgf_password": bgf_password,  # 평문
})

# 변경 후
data.setdefault("stores", []).append({
    ...
    "bgf_user_id": bgf_user_id,
    # bgf_password 제거 - 환경변수에서만 관리
})
```

#### DB 마이그레이션 (v34)

기존 평문 비밀번호를 해싱으로 변환:

```python
# models.py SCHEMA_MIGRATIONS[34]
"""
UPDATE stores SET bgf_password = 'MIGRATED_TO_ENV'
WHERE bgf_password IS NOT NULL
  AND bgf_password NOT LIKE '%$%'
"""
```

실제 비밀번호는 환경변수(`BGF_PASSWORD_{store_id}`)에서 로드하므로, DB의 `bgf_password`는 더 이상 실제 비밀번호를 저장하지 않습니다.

### 2.5 의존성 고정 (`requirements.txt`)

```
selenium==4.27.1
webdriver-manager==4.0.2
python-dotenv==1.0.1
requests==2.32.3
schedule==1.2.2
flask==3.1.0

# 예측 모듈용
pandas==2.2.3
numpy==1.26.4
scikit-learn==1.6.1
holidays==0.64
```

## 3. 구현 순서

| 순서 | 작업 | 파일 | 의존성 |
|:----:|------|------|--------|
| 1 | 보안 헤더 추가 | `app.py` | 없음 |
| 2 | 접근 로깅 추가 | `app.py` | 없음 |
| 3 | Rate Limiter 구현 | `middleware.py` (신규) | 없음 |
| 4 | Rate Limiter 적용 | `app.py` | #3 |
| 5 | 비밀번호 해싱 함수 | `store_service.py` | 없음 |
| 6 | stores.json 비밀번호 제거 | `store_service.py` | #5 |
| 7 | DB 마이그레이션 v34 | `models.py`, `constants.py` | #5 |
| 8 | 의존성 버전 고정 | `requirements.txt` | 없음 |
| 9 | 보안 테스트 작성 | `test_web_security.py` (신규) | #1~#7 |

## 4. 테스트 계획

### `tests/test_web_security.py` (신규)

```python
class TestSecurityHeaders:
    def test_x_content_type_options(self, client)
    def test_x_frame_options(self, client)
    def test_csp_header(self, client)
    def test_referrer_policy(self, client)

class TestRateLimiter:
    def test_normal_request_passes(self)
    def test_exceeds_limit_returns_429(self)
    def test_localhost_exempt(self)
    def test_endpoint_specific_limit(self)
    def test_window_expiry_resets(self)

class TestInputValidation:
    def test_invalid_store_id_rejected(self, client)
    def test_valid_store_id_accepted(self, client)
    def test_invalid_category_rejected(self, client)

class TestErrorResponses:
    def test_500_no_internal_info(self, client)
    def test_404_generic_message(self, client)

class TestPasswordHashing:
    def test_hash_password_returns_salted(self)
    def test_verify_correct_password(self)
    def test_verify_wrong_password(self)
    def test_verify_legacy_plaintext(self)
```

## 5. 변경하지 않는 것

- 넥사크로 Selenium 인증 플로우 (BGF 사이트 로그인)
- 기존 Repository 패턴
- 카카오 알림 API
- 예측/발주 비즈니스 로직
- conftest.py in_memory_db 스키마 (stores 테이블 구조 변경 없음)
