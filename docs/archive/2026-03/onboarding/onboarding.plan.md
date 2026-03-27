# Plan: onboarding (발주핏 SaaS 온보딩 플로우)

## 문제

현재 시스템은 로컬 PC 기반으로 운영 중이며, 새 매장 등록은 관리자가 수동으로 처리한다.
SaaS 전환을 위해 점주가 브라우저에서 10분 안에 혼자 완료할 수 있는 셀프 온보딩이 필요하다.
기존 회원가입은 관리자 승인 기반(SignupRequestRepository)이라 셀프 온보딩과 충돌하므로,
초대 코드 방식으로 자동 승인 경로를 추가한다.

## 핵심 요구사항

### 5단계 온보딩 플로우

| 단계 | 내용 | 핵심 동작 |
|------|------|----------|
| STEP 1 | 회원가입 | 초대 코드 입력 → 즉시 계정 생성 (관리자 승인 불필요) |
| STEP 2 | 매장 등록 | store_code 입력 → BGF API로 매장명 자동 조회 |
| STEP 3 | BGF 계정 연결 | ID/PW 입력 → 비동기 Selenium 로그인 테스트 → Fernet 암호화 저장 |
| STEP 4 | 카테고리 선택 | 기본 6개 전체 선택 → 해제 가능 |
| STEP 5 | 카카오톡 알림 연결 | 선택사항 (스킵 가능) |

### 기존 시스템과의 차이점

| 항목 | 기존 (관리자 승인) | 온보딩 (셀프) |
|------|-------------------|---------------|
| 가입 방식 | signup_requests → admin approve | 초대 코드 → 즉시 생성 |
| 매장 등록 | 관리자가 stores.json 편집 | 점주가 직접 store_code 입력 |
| BGF 연결 | 관리자가 config 파일 편집 | 웹에서 연결 테스트 후 암호화 저장 |
| 카테고리 | 전체 기본 활성 | 점주가 선택 |

## 설계 결정 사항

### D1: 가입 방식 — 초대 코드

관리자가 발급한 초대 코드(invite_code)로 가입. 코드 1회 사용 후 만료.
- 이유: 완전 공개 가입은 보안 위험, 관리자 승인은 10분 목표 불가능
- invite_codes 테이블 신규 (common.db)

### D2: BGF 테스트 — 비동기 폴링

Selenium BGF 로그인은 10~30초 소요. 동기 HTTP 요청은 타임아웃/워커 블로킹 위험.
```
POST /api/onboarding/bgf/test → {"task_id": "abc123"}  (즉시 반환)
GET  /api/onboarding/bgf/status/<task_id> → {"status": "running|success|failed"}
```
프론트에서 2초 간격 폴링. ThreadPoolExecutor 사용.

### D3: 암호화 — Fernet (AES-128-CBC)

- Fernet은 AES-128-CBC 기반 (AES-256 아님). UI 안내 문구는 "AES 암호화"로 표기
- 환경변수: ORDERFIT_SECRET_KEY (base64 인코딩된 32바이트)
- 키 버전 프리픽스: `v1:gAAAAAB...` (향후 키 로테이션 대비)

### D4: 카카오톡 — 선택사항

STEP 5는 스킵 가능. "건너뛰기" 버튼으로 온보딩 완료 가능.
나중에 설정 페이지에서 연결 가능.

### D5: 매장 자동 조회

store_code 입력 시 stores 테이블 또는 BGF API로 매장명 자동 조회.
점주가 매장명 직접 입력 불필요.

## 수정 파일 (예상)

| 파일 | 변경 |
|------|------|
| `src/infrastructure/database/schema.py` | dashboard_users 컬럼 추가 + invite_codes 테이블 + onboarding_events 테이블 |
| `src/infrastructure/database/repos/onboarding_repo.py` | 신규: 온보딩 CRUD Repository |
| `src/utils/crypto.py` | 신규: Fernet 암복호화 유틸리티 |
| `src/web/routes/onboarding.py` | 신규: onboarding_bp Blueprint (7개 엔드포인트) |
| `src/web/routes/__init__.py` | onboarding_bp 등록 |
| `src/web/templates/onboarding.html` | 신규: 온보딩 SPA (ES5 Vanilla JS) |
| `src/web/middleware.py` | before_request에 온보딩 리다이렉트 추가 (API 경로 제외) |
| `scripts/migrate_onboarding.py` | 신규: 마이그레이션 스크립트 |
| `scripts/generate_invite_code.py` | 신규: 초대 코드 발급 CLI |

## DB 변경

### dashboard_users 컬럼 추가 (common.db)

```sql
ALTER TABLE dashboard_users ADD COLUMN bgf_id TEXT;
ALTER TABLE dashboard_users ADD COLUMN bgf_password_enc TEXT;  -- Fernet 암호화
ALTER TABLE dashboard_users ADD COLUMN store_code TEXT;
ALTER TABLE dashboard_users ADD COLUMN store_name TEXT;
ALTER TABLE dashboard_users ADD COLUMN onboarding_step INTEGER DEFAULT 0;
ALTER TABLE dashboard_users ADD COLUMN active_categories TEXT DEFAULT '001,002,003,004,005,012';
ALTER TABLE dashboard_users ADD COLUMN kakao_connected INTEGER DEFAULT 0;
```

### invite_codes 테이블 (common.db, 신규)

```sql
CREATE TABLE IF NOT EXISTS invite_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    created_by INTEGER,
    used_by INTEGER,
    is_used INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    used_at TEXT
);
```

### onboarding_events 테이블 (common.db, 신규)

```sql
CREATE TABLE IF NOT EXISTS onboarding_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    step INTEGER,
    action TEXT,         -- started, completed, failed, skipped
    error_code TEXT,
    duration_sec REAL,
    created_at TEXT NOT NULL
);
```

## API 엔드포인트

| Method | Path | 설명 | 인증 |
|--------|------|------|------|
| GET | /onboarding | 온보딩 메인 페이지 | 로그인 필요 |
| GET | /api/onboarding/status | 현재 단계 조회 | 로그인 필요 |
| POST | /api/onboarding/signup | STEP 1: 초대 코드 + 회원가입 | 비인증 |
| POST | /api/onboarding/store | STEP 2: 매장 등록 | 로그인 필요 |
| POST | /api/onboarding/store/lookup | STEP 2: 매장 자동 조회 | 로그인 필요 |
| POST | /api/onboarding/bgf/test | STEP 3: BGF 연결 테스트 시작 | 로그인 필요 |
| GET | /api/onboarding/bgf/status/\<task_id\> | STEP 3: 연결 테스트 결과 폴링 | 로그인 필요 |
| POST | /api/onboarding/categories | STEP 4: 카테고리 선택 | 로그인 필요 |
| POST | /api/onboarding/complete | STEP 5: 온보딩 완료 | 로그인 필요 |

## 보안 요구사항

1. **BGF 비밀번호**: Fernet 암호화 후 DB 저장, 로그 출력 절대 금지
2. **ORDERFIT_SECRET_KEY**: 미설정 시 서버 시작 불가 (ValueError)
3. **초대 코드**: 1회 사용 후 만료, UUID4 기반 16자리
4. **BGF 테스트 Rate Limit**: 3회/300초 (계정 브루트포스 방지)
5. **입력 검증**: store_code `^\d{5}$`, bgf_id 4~20자 영숫자
6. **CSRF**: Referer 헤더 검증 (기존 미들웨어 패턴 따름)

## 테스트 계획

| 테스트 | 검증 항목 |
|--------|----------|
| test_encrypt_decrypt_roundtrip | Fernet 암복호화 일관성 |
| test_encrypt_no_plaintext | 암호화 결과에 원문 미포함 |
| test_encrypt_key_version | v1: 프리픽스 포함 확인 |
| test_invite_code_single_use | 코드 1회 사용 후 재사용 불가 |
| test_signup_with_invite | 초대 코드로 계정 생성 |
| test_save_store_info | 매장 정보 저장 + step=2 |
| test_save_bgf_encrypted | DB에 평문 미저장 |
| test_step_progression | 단계 순서 보장 (step=2에서 step=4 불가) |
| test_bgf_test_rate_limit | 3회/300초 제한 |
| test_categories_save | 카테고리 저장/조회 |
| test_skip_kakao | 카카오 없이 온보딩 완료 |
| test_resume_onboarding | 브라우저 새로고침 후 재개 |
| test_before_request_redirect | step<5 시 /onboarding 리다이렉트 (API 제외) |

## 영향 분석

| 항목 | 영향 |
|------|------|
| DB 스키마 | v53+: dashboard_users 컬럼 7개 추가 + 테이블 2개 추가 |
| 기존 인증 | 변경 없음 (기존 admin/viewer 로그인 그대로 유지) |
| 기존 라우트 | 변경 없음 (onboarding_bp 신규 추가만) |
| Selenium | 기존 SalesAnalyzer.login() 재사용 (중복 구현 금지) |
| 프론트엔드 | ES5 Vanilla JS 유지, 모바일 반응형 필수 |

## 제약사항

1. ES5 스타일 유지 (const, let, 화살표함수 금지)
2. 기존 파일 수정 최소화 (schema.py 마이그레이션 + __init__.py 등록 + middleware.py 리다이렉트)
3. Selenium 코드 중복 금지 (SalesAnalyzer 재사용)
4. 로그에 비밀번호 출력 금지
5. ORDERFIT_SECRET_KEY 미설정 시 서버 시작 불가
