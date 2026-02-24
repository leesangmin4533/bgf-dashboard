# BGF 리테일 자동 발주 시스템 - 보안 취약점 분석 보고서

> 작성일: 2026-02-22
> 분석 대상: `bgf_auto/src/` 전체 (Python 3.12 + Flask + Selenium + SQLite)
> 분석 기준: OWASP Top 10 (2021)

---

## 1. 요약 (Executive Summary)

### 점수: 35/100 (심각한 보안 개선 필요)

| 심각도 | 건수 | 즉시 조치 필요 |
|--------|------|---------------|
| **Critical** | 3건 | 배포 중단급 |
| **High** | 5건 | 릴리스 전 수정 필수 |
| **Medium** | 4건 | 다음 스프린트에 수정 |
| **Low** | 3건 | 백로그 트래킹 |

### 핵심 문제 (Top 3)

1. **실제 비밀번호/API 키가 `.env` 파일에 평문 저장되어 있고, `.gitignore`에 `.env`가 등록되지 않음**
2. **Flask 웹 대시보드에 인증/인가가 전혀 없음 (모든 API 무인증 접근 가능)**
3. **웹 API에서 `subprocess.Popen`으로 시스템 스크립트 실행 가능 (원격 코드 실행 위험)**

---

## 2. OWASP Top 10 체크리스트

### A01: Broken Access Control [CRITICAL]

**문제**: Flask 웹 대시보드에 인증/인가 메커니즘이 전혀 없음

- **모든 API 엔드포인트가 무인증 접근 가능**
  - `POST /api/home/scheduler/start` -- 스케줄러 시작
  - `POST /api/home/scheduler/stop` -- 스케줄러 중지 (프로세스 kill)
  - `POST /api/order/run-script` -- 시스템 스크립트 실행
  - `POST /api/order/adjust` -- 발주량 수정
  - `POST /api/order/exclusions/toggle` -- 발주 설정 변경
  - `GET /api/logs/*` -- 시스템 로그 열람
  - `GET /api/order/exclusions` -- 사업 데이터 열람

- **위치**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\routes\` 전체
- **심각도**: Critical
- **공격 시나리오**: 동일 네트워크 접속자가 브라우저에서 `http://host:5000/api/home/scheduler/stop`을 호출하면 자동 발주 시스템 중단 가능
- **수정 방안**:
  ```python
  # Flask-Login 또는 Basic Auth 추가
  from functools import wraps
  from flask import request, Response

  def check_auth(f):
      @wraps(f)
      def decorated(*args, **kwargs):
          auth = request.authorization
          if not auth or not verify_password(auth.username, auth.password):
              return Response('Unauthorized', 401,
                  {'WWW-Authenticate': 'Basic realm="BGF Dashboard"'})
          return f(*args, **kwargs)
      return decorated
  ```

---

### A02: Cryptographic Failures [CRITICAL]

**문제 1**: `.env` 파일에 실제 인증 정보가 평문으로 저장됨

- **위치**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\.env`
- **노출된 정보**:
  - BGF 리테일 로그인 ID/PW: `BGF_USER_ID_46513=46513`, `BGF_PASSWORD_46513=1113`
  - 카카오 REST API 키: `KAKAO_REST_API_KEY=1a01b8795eec4f853909f272856ea0f2`
  - 카카오 클라이언트 시크릿: `KAKAO_CLIENT_SECRET=VEVAyIaBudBgYCdxpN5dAzxSy0KaaFwl`
  - **카카오 계정 이메일/비밀번호**: `KAKAO_ID=kanura4533@hanmail.net`, `KAKAO_PW=dltkdals23!`
- **심각도**: Critical
- **.gitignore 미등록**: 프로젝트 루트에 `.gitignore`가 없어 Git 추적 대상이 될 수 있음

**문제 2**: 카카오 OAuth 토큰이 JSON 파일에 평문 저장됨

- **위치**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\config\kakao_token.json`
- 토큰 파일이 config/ 디렉토리에 포함되어 코드와 함께 배포될 위험
- chmod 600 설정 시도하지만 Windows에서는 효과 없음 (코드 line 83-87)

**문제 3**: DB에 BGF 비밀번호 평문 저장

- **위치**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\infrastructure\database\schema.py` (line 98)
  ```sql
  bgf_password TEXT,  -- stores 테이블에 평문 저장
  ```
- `StoreService.add_store()` (store_service.py line 163-167)에서 비밀번호를 해싱 없이 DB에 저장

**수정 방안**:
1. `.gitignore` 파일 생성하여 `.env`, `config/kakao_token.json`, `data/` 제외
2. DB에 비밀번호 저장 시 bcrypt 등으로 해싱 (또는 환경변수만 사용, DB 저장 제거)
3. 카카오 계정 비밀번호는 환경변수에서도 저장하지 말 것 (OAuth 토큰 관리로 대체)
4. `.env` 파일에 노출된 시크릿 전부 로테이션 (이미 유출된 것으로 간주)

---

### A03: Injection [LOW - 양호]

**현황**: SQL 인젝션 위험은 **낮음**

- Repository 패턴에서 파라미터 바인딩(`?`) 일관 사용
- f-string SQL이 존재하나, 삽입되는 값이 모두 **내부 생성 상수** (예: `store_filter` 헬퍼의 `AND store_id = ?`)
- 웹 API에서 사용자 입력이 SQL에 직접 삽입되는 경우 없음

**주의 포인트**:
- `repository_multi_store.py` line 147-157: `update_store()`에서 동적 SET 절 생성하지만, `allowed_fields` 화이트리스트로 보호됨
  ```python
  allowed_fields = ['store_name', 'location', 'type', 'bgf_user_id', 'bgf_password', 'is_active']
  ```
- `api_prediction.py`의 여러 함수에서 f-string SQL이 보이지만, 변수가 모두 `AND store_id = ?` + `(store_id,)` 패턴

---

### A04: Insecure Design [HIGH]

**문제 1**: 웹 API에서 시스템 프로세스 직접 제어

- **위치**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\routes\api_home.py`
  - `scheduler_start()` (line 91-113): `subprocess.Popen`으로 스케줄러 실행
  - `scheduler_stop()` (line 116-147): PID 기반 프로세스 종료 (`TerminateProcess`)
- **위치**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\routes\api_order.py`
  - `run_script()` (line 552-621): 다양한 Python 스크립트를 subprocess로 실행
  - `stop_script()` (line 645-661): 실행 중인 프로세스 terminate/kill

- **수정 방안**: 최소한 인증 게이트 추가, 이상적으로는 웹과 실행 계층 분리

**문제 2**: Flask SECRET_KEY가 하드코딩된 기본값 사용

- **위치**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\app.py` line 26
  ```python
  app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "bgf-dashboard-local-dev")
  ```
- `.env`에 `FLASK_SECRET_KEY`가 정의되지 않으면 예측 가능한 기본값 사용
- 세션 조작 공격 가능

**문제 3**: `host="0.0.0.0"` 바인딩으로 외부 네트워크 노출

- **위치**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\app.py` line 62
  ```python
  app.run(host="0.0.0.0", port=5000, debug=False)
  ```
- 모든 네트워크 인터페이스에 바인딩 -- 편의점 내부 네트워크의 어떤 기기에서든 접근 가능

---

### A05: Security Misconfiguration [HIGH]

**문제 1**: 보안 헤더 미설정

Flask 앱에 다음 보안 헤더가 모두 누락됨:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY` (Clickjacking 방지)
- `X-XSS-Protection: 1; mode=block`
- `Content-Security-Policy`
- `Strict-Transport-Security` (HTTPS 미사용)

**문제 2**: CORS 정책 미설정

- Flask 앱에 CORS 설정 없음 (기본적으로 Same-Origin만 허용하지만 명시적 설정 권장)

**문제 3**: 에러 응답에 내부 정보 노출

- 여러 API 엔드포인트에서 `str(e)`로 예외 메시지를 그대로 반환:
  ```python
  # api_order.py line 105
  return jsonify({"error": str(e)}), 500

  # api_waste.py line 49
  return jsonify({"error": str(e)}), 500
  ```
- 스택 트레이스, DB 스키마, 파일 경로 등 내부 정보 유출 가능

**수정 방안**:
```python
# Flask after_request 핸들러로 보안 헤더 추가
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response
```

---

### A06: Vulnerable and Outdated Components [MEDIUM]

**문제**: `requirements.txt`에 최소 버전만 지정, 버전 잠금 없음

- **위치**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\requirements.txt`
  ```
  selenium>=4.15.0
  requests>=2.31.0
  scikit-learn>=1.3.0
  ```
- `flask` 자체가 requirements.txt에 없음 (의존성으로 설치된 것으로 추정)
- 버전 상한 미지정으로 취약 버전 설치 가능

**수정 방안**:
1. `pip freeze > requirements.lock` 으로 정확한 버전 고정
2. `flask` 명시 추가
3. `pip-audit` 또는 `safety` 도구로 정기 취약점 스캔

---

### A07: Identification and Authentication Failures [CRITICAL]

**문제 1**: 웹 대시보드에 인증 메커니즘 부재 (A01과 연관)

**문제 2**: BGF 로그인 정보 관리 취약

- 모든 매장의 BGF 비밀번호가 동일 (`1113`) -- `.env` 파일에서 확인
- 비밀번호 복잡도 검증 없음
- 로그인 시도 횟수 제한(Rate Limiting) 없음

**문제 3**: 카카오 자동 재인증에서 평문 비밀번호 사용

- **위치**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\notification\kakao_notifier.py` line 235-249
  ```python
  kakao_id = os.environ.get("KAKAO_ID", "")
  kakao_pw = os.environ.get("KAKAO_PW", "")
  ```
- 개인 카카오 계정 이메일/비밀번호를 환경변수에 저장하여 자동 로그인에 사용
- 계정 탈취 시 카카오 계정 전체 침해 가능

---

### A08: Software and Data Integrity Failures [LOW]

- `webdriver_manager`가 Chrome 드라이버를 자동 다운로드하는데, 무결성 검증 메커니즘은 webdriver_manager 라이브러리에 위임
- 자동 업데이트된 드라이버가 변조될 경우 공급망 공격 가능 (낮은 확률)

---

### A09: Security Logging and Monitoring Failures [MEDIUM]

**긍정적 요소**:
- `src/utils/logger.py`로 로깅 체계 구축
- `log_parser.py`로 로그 분석 체계 구축

**부족한 점**:
- 웹 API 접근 로그 기록 없음 (누가 어떤 API를 호출했는지 추적 불가)
- 인증 실패 로그 없음 (인증 자체가 없으므로)
- 비정상 접근 탐지/알림 없음
- BGF 로그인 실패 시도에 대한 경보 없음

---

### A10: Server-Side Request Forgery (SSRF) [LOW]

- SSRF 직접 경로는 발견되지 않음
- `requests.post()`는 카카오 API 고정 URL에만 사용 (`KAKAO_TOKEN_URL`, `KAKAO_MESSAGE_URL`)
- 사용자 입력이 URL에 반영되는 경로 없음

---

## 3. 추가 발견사항

### 3.1 XSS 취약점 [MEDIUM]

- **위치**: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\web\templates\index.html`
- Flask Jinja2 템플릿은 기본적으로 자동 이스케이핑 적용 (양호)
- 그러나 SPA 구조에서 JavaScript가 API 응답을 DOM에 직접 삽입할 경우 XSS 위험 존재
- API 응답의 `item_nm` (상품명) 등 사용자 유래 데이터가 sanitize 없이 반환됨

### 3.2 CSRF 보호 미비 [HIGH]

- Flask 앱에 CSRF 토큰 보호가 없음
- POST 엔드포인트들 (`/scheduler/start`, `/run-script`, `/adjust` 등)이 CSRF 공격에 노출
- 대시보드에 접속한 사용자가 악성 사이트를 방문하면 발주 실행 등이 가능

### 3.3 Rate Limiting 미비 [MEDIUM]

- 모든 API 엔드포인트에 Rate Limiting 없음
- `POST /api/order/predict` 등 무거운 연산을 반복 호출 가능 (DoS)

### 3.4 Selenium 보안 [LOW]

- 자동화 감지 우회 코드 사용 (line 107-121) -- 보안보다는 기능 요구사항
- `page_load_timeout=60` -- 합리적
- `--no-sandbox` 옵션 -- Docker 환경이 아닌 Windows에서는 불필요, 보안 약화

### 3.5 stores.json에 인증 정보 저장 위험 [HIGH]

- `StoreService._add_to_stores_json()` (store_service.py line 137-145)에서 `bgf_user_id`, `bgf_password`를 `stores.json`에 저장
- 현재 `stores.json`에는 인증 정보가 없지만, `add_store()` API 사용 시 저장될 수 있음

---

## 4. 우선순위별 조치 계획

### Phase 1: 긴급 조치 (즉시)

| No | 조치 | 대상 파일 | 심각도 |
|----|------|----------|--------|
| 1 | `.gitignore` 생성: `.env`, `config/kakao_token.json`, `data/`, `*.db` 제외 | 프로젝트 루트 | Critical |
| 2 | `.env` 노출된 시크릿 전부 로테이션 (BGF PW 변경, 카카오 API 키 재발급) | .env | Critical |
| 3 | 카카오 계정 비밀번호(`KAKAO_PW`)를 `.env`에서 제거 | .env | Critical |
| 4 | Flask `host` 바인딩을 `127.0.0.1`로 변경 (또는 방화벽 설정) | app.py | High |

### Phase 2: 인증/인가 (1주 내)

| No | 조치 | 대상 파일 | 심각도 |
|----|------|----------|--------|
| 5 | Flask Basic Auth 또는 Flask-Login 추가 | web/app.py, routes/ | Critical |
| 6 | CSRF 토큰 도입 (Flask-WTF) | web/app.py | High |
| 7 | `FLASK_SECRET_KEY` 환경변수 필수 설정 (기본값 제거) | web/app.py | High |
| 8 | 보안 헤더 추가 (`X-Frame-Options`, `X-Content-Type-Options` 등) | web/app.py | High |

### Phase 3: 데이터 보호 (2주 내)

| No | 조치 | 대상 파일 | 심각도 |
|----|------|----------|--------|
| 9 | DB stores 테이블에서 `bgf_password` 컬럼 제거 (환경변수만 사용) | schema.py, store_service.py | High |
| 10 | stores.json에 인증 정보 저장하지 않도록 수정 | store_service.py | High |
| 11 | 에러 응답에서 내부 정보 제거 (generic 메시지만 반환) | routes/*.py | Medium |
| 12 | 웹 API 접근 로그 기록 추가 | web/app.py | Medium |

### Phase 4: 방어 심화 (1개월 내)

| No | 조치 | 대상 파일 | 심각도 |
|----|------|----------|--------|
| 13 | Rate Limiting 추가 (Flask-Limiter) | web/app.py | Medium |
| 14 | `requirements.txt`에 Flask 추가 + 버전 고정 | requirements.txt | Medium |
| 15 | `pip-audit`로 의존성 취약점 스캔 자동화 | CI/CD | Medium |
| 16 | Selenium `--no-sandbox` 옵션 제거 (Windows에서 불필요) | sales_analyzer.py | Low |

---

## 5. 참고: 양호한 부분

다음 사항은 보안적으로 양호합니다:

1. **SQL 인젝션 방어**: Repository 패턴에서 파라미터 바인딩 일관 사용
2. **인증 정보 환경변수 분리**: `StoreConfigLoader`가 환경변수에서 로드하는 패턴 (코드에 하드코딩하지 않음)
3. **Jinja2 자동 이스케이핑**: Flask 템플릿의 기본 XSS 방어
4. **에러 핸들러 구조**: `app.py`에 전역 에러 핸들러 정의 (500 응답 시 일반적 메시지)
5. **토큰 파일 권한 제한 시도**: `kakao_notifier.py`에서 chmod 600 시도
6. **StoreRepository에서 인증 정보 미반환**: `get_active_stores()`에서 비밀번호 제외 (line 46-47)
7. **request timeout 설정**: requests 라이브러리 호출 시 timeout=10 일관 사용

---

## 부록: 분석 대상 파일 목록

| 파일 | 분석 항목 |
|------|----------|
| `src/sales_analyzer.py` | BGF 로그인 로직, Selenium 보안 |
| `src/web/app.py` | Flask 설정, SECRET_KEY, 바인딩 |
| `src/web/routes/*.py` (8개) | API 인증, 입력 검증, 에러 처리 |
| `src/notification/kakao_notifier.py` | OAuth, 토큰 관리, 자동 인증 |
| `src/infrastructure/database/connection.py` | DB 연결, ATTACH |
| `src/infrastructure/database/base_repository.py` | SQL 파라미터 바인딩 |
| `src/infrastructure/database/repos/*.py` (19개) | SQL 인젝션 검증 |
| `src/infrastructure/database/schema.py` | 스키마에 평문 비밀번호 |
| `src/config/store_config.py` | 인증 정보 로드 |
| `src/application/services/store_service.py` | 매장 추가 시 비밀번호 처리 |
| `src/db/repository_multi_store.py` | stores 테이블 CRUD |
| `.env` / `.env.example` | 환경변수 관리 |
| `config/kakao_token.json` | OAuth 토큰 저장 |
| `config/stores.json` | 매장 메타데이터 |
| `requirements.txt` | 의존성 관리 |
