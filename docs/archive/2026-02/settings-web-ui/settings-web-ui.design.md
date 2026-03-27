# Design: 설정 관리 웹 UI

## 1. API 설계

### GET /api/settings/eval-params
eval_params.json 전체 조회.

**Response:**
```json
{
  "params": {
    "daily_avg_days": {"value": 14.0, "default": 14.0, "min": 7.0, "max": 30.0, "description": "일평균 판매량 계산 기간"},
    ...
  },
  "groups": {
    "prediction": ["daily_avg_days", "weight_daily_avg", ...],
    "calibration": ["calibration_decay", "calibration_reversion_rate", ...],
    "cost_optimization": {...},
    "holiday": {...},
    "waste_cause": {...}
  }
}
```

### POST /api/settings/eval-params
eval_params 수정 (admin). Body: `{"key": "daily_avg_days", "value": 14.0}`

**Response:** `{"ok": true, "old_value": 12.0, "new_value": 14.0}`

### POST /api/settings/eval-params/reset
기본값 복원 (admin). Body: `{"key": "daily_avg_days"}` 또는 `{"key": "__all__"}`

**Response:** `{"ok": true, "reset_count": 1}`

### GET /api/settings/feature-flags
기능 토글 목록.

**Response:**
```json
{
  "flags": [
    {"key": "ENABLE_PASS_SUPPRESSION", "value": true, "description": "패스 억제"},
    {"key": "ENABLE_FORCE_DOWNGRADE", "value": true, "description": "강제 다운그레이드"},
    ...
  ]
}
```

### POST /api/settings/feature-flags
토글 전환 (admin). Body: `{"key": "ENABLE_PASS_SUPPRESSION", "value": false}`

**Response:** `{"ok": true, "key": "ENABLE_PASS_SUPPRESSION", "old_value": true, "new_value": false}`

### GET /api/settings/audit-log?hours=24
변경 이력 조회.

**Response:**
```json
{
  "logs": [
    {"id": 1, "key": "daily_avg_days", "old_value": "12.0", "new_value": "14.0", "changed_by": "admin", "changed_at": "2026-02-26T10:00:00", "category": "eval_params"}
  ]
}
```

## 2. UI 설계

### 설정 탭 확장 — 3개 서브탭
기존 스케줄러/사용자 관리 아래에 추가:
- **"파라미터"**: eval_params 그룹별 접기/펼치기 폼
- **"기능 토글"**: 스위치 UI (키, 설명, on/off)
- **"변경 이력"**: 최근 24시간 변경 테이블

### 파라미터 UI
- 그룹 5개: 예측 기본, 보정, 비용최적화, 휴일, 폐기원인
- 각 파라미터: 이름, 현재값(입력), 기본값(참고), 범위(min~max)
- [저장] 버튼 (개별), [전체 리셋] 버튼

### 기능 토글 UI
- 7개 토글: ENABLE_PASS_SUPPRESSION, ENABLE_FORCE_DOWNGRADE, ENABLE_FORCE_INTERMITTENT_SUPPRESSION, NEW_PRODUCT_MODULE_ENABLED, NEW_PRODUCT_AUTO_INTRO_ENABLED, DIFF_FEEDBACK_ENABLED, FOOD_WASTE_CAL_ENABLED
- 스위치 + 설명 텍스트

## 3. 감사 로그

### common.db: settings_audit_log 테이블
```sql
CREATE TABLE IF NOT EXISTS settings_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    setting_key TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_by TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    category TEXT NOT NULL
)
```

## 4. 테스트 설계 (10개)

| 테스트 | 검증 내용 |
|--------|----------|
| test_get_eval_params | eval-params GET 응답 형식 |
| test_update_eval_param | 파라미터 수정 동작 |
| test_update_eval_param_range | min/max 범위 검증 |
| test_reset_eval_param | 개별 리셋 동작 |
| test_reset_all_eval_params | 전체 리셋 동작 |
| test_get_feature_flags | feature-flags GET 응답 형식 |
| test_toggle_feature_flag | 토글 전환 동작 |
| test_audit_log_recorded | 변경 시 감사 로그 기록 |
| test_audit_log_query | 감사 로그 조회 |
| test_blueprint_registered | Blueprint 등록 확인 |
