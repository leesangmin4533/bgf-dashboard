# Completion Report: db-router-unify

> 카테고리 15개 파일 sqlite3.connect → DBRouter 통합

## 1. Summary

| 항목 | 값 |
|------|-----|
| Feature | db-router-unify |
| 시작일 | 2026-04-02 |
| 완료일 | 2026-04-02 |
| Match Rate | 100% |
| Iteration | 0회 |
| Commit | `f272f4c` |

## 2. Problem

15개 카테고리 Strategy 파일에서 **35건의 sqlite3.connect() 직접 호출**이 DBRouter를 우회.
- WAL 모드 누락 → 병렬 실행 시 "database is locked" 가능
- 동일한 `_get_db_path()` 함수 15개 파일에 중복
- timeout 누락 3건

## 3. Solution

| 변경 | 내용 |
|------|------|
| `_db.py` 신규 | `get_conn(store_id, db_path)` 공통 헬퍼 → DBRouter 래핑 |
| 15개 파일 수정 | `sqlite3.connect()` → `get_conn()` 교체 (35건) |
| 테스트 수정 | mock 대상 `_get_db_path` → `get_conn` 교체 (8건) |
| common DB 분리 | product_details 조회 → `get_conn(db_path=...)` |

## 4. Impact

| 지표 | Before | After |
|------|--------|-------|
| sqlite3.connect (카테고리) | 35건 | **0건** |
| WAL 적용 카테고리 | 0건 | **35건** |
| _get_db_path 중복 | 15개 | deprecated (get_conn 대체) |
| timeout 누락 | 3건 | **0건** |
| 테스트 통과 | 1,560 | 1,560 (변화 없음) |

## 5. Remaining Work

- `src/prediction/` 비카테고리 (~23건): feature_calculator, lag_features 등
- `src/web/routes/` (~15건): api_settings, api_category 등
- `src/report/`, `src/analysis/` (~20건)
- `scripts/` (~70건, 낮은 우선순위)
