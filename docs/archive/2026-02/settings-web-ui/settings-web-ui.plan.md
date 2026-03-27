# Plan: 설정 관리 웹 UI

## 1. 목적
- 예측 파라미터(eval_params.json)를 웹 UI에서 조회/수정/리셋
- 기능 토글(feature flags)을 웹에서 on/off 전환
- 설정 변경 이력(audit log) 자동 기록
- admin 전용, 매장별 격리 지원

## 2. 현재 상태
- `config/eval_params.json`: 96개 파라미터, 수동 JSON 편집만 가능
- `config/eval_params.default.json`: 기본값 백업 존재
- `AppSettingsRepository`: bool 값만 지원 (app_settings 테이블)
- 설정 탭: 스케줄러 제어 + 사용자 관리만 존재
- 변경 이력 추적 없음

## 3. 범위 (Scope)

### 포함
- eval_params 조회/수정/리셋 API + UI
- 기능 토글 조회/전환 API + UI
- 변경 이력 기록/조회

### 제외 (v2 이후)
- 매장별 eval_params 오버라이드 (config/stores/ 는 기존 구조 유지)
- constants.py 수정 (코드 레벨이므로 웹 UI 부적합)
- 설정 import/export
- 실시간 검증 (범위 체크 등)

## 4. 수정 계획

### 4-1. API 엔드포인트 (api_settings.py)
- `GET /api/settings/eval-params` — eval_params.json 읽기
- `POST /api/settings/eval-params` — eval_params 수정 (admin)
- `POST /api/settings/eval-params/reset` — 기본값 복원 (admin)
- `GET /api/settings/feature-flags` — 기능 토글 목록
- `POST /api/settings/feature-flags/<key>` — 토글 전환 (admin)
- `GET /api/settings/audit-log` — 변경 이력 조회

### 4-2. UI 확장 (index.html + settings.js)
- 설정 탭에 3개 서브탭: "파라미터" | "기능 토글" | "변경 이력"
- 파라미터: 그룹별 접을 수 있는 폼 (daily_avg, calibration, cost_optimizer 등)
- 기능 토글: 스위치 UI
- 변경 이력: 테이블

### 4-3. 감사 로그 (settings_audit_log)
- common.db에 settings_audit_log 테이블 추가
- 모든 설정 변경 시 자동 기록

## 5. 영향 범위
- `src/web/routes/api_settings.py` (신규)
- `src/web/routes/__init__.py` (Blueprint 등록)
- `src/web/templates/index.html` (설정 탭 확장)
- `src/web/static/js/settings.js` (신규)
- `src/web/static/js/app.js` (탭 트리거)
- `tests/test_settings_web_ui.py` (신규)

## 6. 리스크
- eval_params 잘못 수정 시 예측 품질 저하 → 리셋 기능 + 변경 이력으로 방어
- 동시 수정 충돌 → admin 1명 환경이므로 낮음
