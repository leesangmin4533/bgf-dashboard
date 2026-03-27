# Plan: dessert-data-fix (디저트 대시보드 데이터 정합성 수정)

## 문제
디저트 대시보드에 표시되는 데이터가 실제 DB/서비스와 불일치.
조사 결과 코드 버그 2건 + 테스트 데이터 문제를 확인함.

## 근본 원인

### 🔴 코드 버그 (실제 서비스 실행 시에도 발생)

**Bug 1: `get_weekly_trend()` strftime `%%W` 오류**
- 위치: `dessert_decision_repo.py` L366
- 현상: `strftime('%%W', ...)` → SQLite가 리터럴 `%W` 반환 → `CAST('%W' AS INTEGER)` = 0
- 결과: 모든 레코드가 W0으로 집계, 주간추이 차트 단일 데이터 포인트
- 수정: `'%%W'` → `'%W'`

**Bug 2: `MAX(id)` vs `MAX(judgment_period_end)` 불일치**
- 위치: `dessert_decision_repo.py` — `get_pending_stop_count()` L400, `batch_update_operator_action()` L313
- 현상: `MAX(id)`가 시간순 최신 레코드를 보장하지 않음
- 결과: `get_pending_stop_count()` = 0 반환 (실제 3건), 일괄 운영자 액션이 잘못된 레코드 대상
- 수정: `MAX(id)` → `MAX(judgment_period_end)` + JOIN 기준 변경 (id → item_cd + judgment_period_end)

### 🟡 테스트 데이터 문제 (실제 서비스 실행으로 해결)

| 항목 | 테스트 데이터 값 | 실제 서비스 생성값 |
|------|-----------------|-------------------|
| sale_rate | `75.0` (퍼센트) | `0.75` (비율, `calc_sale_rate()`) |
| judgment_cycle | `'WEEKLY'` (대문자) | `'weekly'` (소문자, `JudgmentCycle` enum) |
| first_receiving_source | `'CENTER'` | `'daily_sales_sold'` 등 (`FirstReceivingSource` enum) |
| 상품 수 | 12 / 174 | 전체 디저트 대상 |

## 수정 범위

### Fix 1: `get_weekly_trend()` — strftime 수정
- 파일: `dessert_decision_repo.py` L366
- 변경: `strftime('%%W', judgment_period_end)` → `strftime('%W', judgment_period_end)`
- 범위: 1줄

### Fix 2: `get_pending_stop_count()` — MAX 기준 통일
- 파일: `dessert_decision_repo.py` L400-412
- 변경: `MAX(id) as max_id` → `MAX(judgment_period_end) as max_date` + JOIN 기준 변경
- 범위: 서브쿼리 3줄

### Fix 3: `batch_update_operator_action()` — MAX 기준 통일
- 파일: `dessert_decision_repo.py` L309-317
- 변경: `MAX(id) as max_id` → `MAX(judgment_period_end) as max_date` + JOIN 기준 변경
- 범위: 서브쿼리 3줄

### Fix 4: 테스트 데이터 정리 + 실제 판단 실행
- 기존 48건 테스트 데이터 삭제 (또는 실제 판단 실행으로 UPSERT 덮어쓰기)
- `python run_scheduler.py --dessert-decision` 실행으로 실제 데이터 생성
- 174개 디저트 상품 대상 판단 결과 확인

## 수정 파일 요약

| 파일 | 변경 |
|------|------|
| `src/infrastructure/database/repos/dessert_decision_repo.py` | Fix 1,2,3 (3개소) |
| `data/stores/46513.db` | Fix 4 (테스트 데이터 정리) |
| `tests/test_dessert_data_fix.py` | 신규 테스트 (~10개) |

## 테스트 계획

1. `get_weekly_trend()`: 복수 주차 데이터로 올바른 W값 반환 검증
2. `get_pending_stop_count()`: STOP_RECOMMEND + NULL operator_action 카운트 정확성
3. `batch_update_operator_action()`: 최신 judgment_period_end 레코드 대상 확인
4. `get_decision_summary()` vs `get_pending_stop_count()` 교차 검증
5. 실제 판단 실행 후 sale_rate 비율 형식(0.xx) 확인

## 영향 분석

| 항목 | 영향 |
|------|------|
| DB 스키마 | 변경 없음 |
| API | 동일 엔드포인트, 응답값 정확도 향상 |
| JS (dessert.js) | 변경 없음 (API 응답 형식 동일) |
| 기존 테스트 | 통과 (쿼리 결과 변경이지만 기존 테스트는 이 메서드 미사용) |
| OrderFilter | 영향 없음 (`get_confirmed_stop_items()`는 `MAX(judgment_period_end)` 사용) |
