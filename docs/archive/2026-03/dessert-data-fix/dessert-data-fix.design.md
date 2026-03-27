# Design: dessert-data-fix (디저트 대시보드 데이터 정합성 수정)

**Plan 참조**: `docs/01-plan/features/dessert-data-fix.plan.md`

---

## 1. 수정 대상 파일

| # | 파일 | 변경 유형 |
|---|------|----------|
| 1 | `src/infrastructure/database/repos/dessert_decision_repo.py` | 버그 수정 3개소 |
| 2 | `tests/test_dessert_data_fix.py` | 신규 (테스트 ~10개) |

---

## 2. 구현 상세

### Fix 1: `get_weekly_trend()` — strftime `%%W` → `%W`

**위치**: `dessert_decision_repo.py` L366

**Before**:
```python
CAST(strftime('%%W', judgment_period_end) AS INTEGER) as week_num,
```

**After**:
```python
CAST(strftime('%W', judgment_period_end) AS INTEGER) as week_num,
```

**이유**: Python `conn.execute("""...""", params)` 에서 `?` 파라미터 바인딩 사용 시 `%`는 특수문자가 아님. `%%W`는 SQLite에 그대로 전달되어 `%%`가 리터럴 `%` 출력 → `CAST('%W' AS INTEGER)` = 0 으로 모든 레코드가 W0으로 집계됨.

---

### Fix 2: `get_pending_stop_count()` — `MAX(id)` → `MAX(judgment_period_end)`

**위치**: `dessert_decision_repo.py` L400-414

**Before**:
```python
cursor = conn.execute("""
    SELECT COUNT(*)
    FROM dessert_decisions d
    INNER JOIN (
        SELECT item_cd, MAX(id) as max_id
        FROM dessert_decisions
        WHERE store_id = ?
        GROUP BY item_cd
    ) latest ON d.id = latest.max_id
    WHERE d.store_id = ?
      AND d.decision = 'STOP_RECOMMEND'
      AND d.operator_action IS NULL
""", (sid, sid))
```

**After**:
```python
cursor = conn.execute("""
    SELECT COUNT(*)
    FROM dessert_decisions d
    INNER JOIN (
        SELECT item_cd, MAX(judgment_period_end) as max_date
        FROM dessert_decisions
        WHERE store_id = ?
        GROUP BY item_cd
    ) latest ON d.item_cd = latest.item_cd
           AND d.judgment_period_end = latest.max_date
    WHERE d.store_id = ?
      AND d.decision = 'STOP_RECOMMEND'
      AND d.operator_action IS NULL
""", (sid, sid))
```

**이유**: `MAX(id)`는 삽입 순서에 의존하므로 시간순 최신 레코드를 보장하지 않음. `get_latest_decisions()`, `get_decision_summary()`, `get_confirmed_stop_items()`, `get_stop_recommended_items()` 등 다른 메서드는 이미 `MAX(judgment_period_end)` 사용. 일관성 통일.

---

### Fix 3: `batch_update_operator_action()` — `MAX(id)` → `MAX(judgment_period_end)`

**위치**: `dessert_decision_repo.py` L309-322

**Before**:
```python
cursor = conn.execute(f"""
    SELECT d.id, d.item_cd
    FROM dessert_decisions d
    INNER JOIN (
        SELECT item_cd, MAX(id) as max_id
        FROM dessert_decisions
        WHERE store_id = ?
        GROUP BY item_cd
    ) latest ON d.id = latest.max_id
    WHERE d.store_id = ?
      AND d.item_cd IN ({placeholders})
      AND d.decision = 'STOP_RECOMMEND'
      AND d.operator_action IS NULL
""", [sid, sid] + list(item_cds))
```

**After**:
```python
cursor = conn.execute(f"""
    SELECT d.id, d.item_cd
    FROM dessert_decisions d
    INNER JOIN (
        SELECT item_cd, MAX(judgment_period_end) as max_date
        FROM dessert_decisions
        WHERE store_id = ?
        GROUP BY item_cd
    ) latest ON d.item_cd = latest.item_cd
           AND d.judgment_period_end = latest.max_date
    WHERE d.store_id = ?
      AND d.item_cd IN ({placeholders})
      AND d.decision = 'STOP_RECOMMEND'
      AND d.operator_action IS NULL
""", [sid, sid] + list(item_cds))
```

**이유**: Fix 2와 동일. 일괄 운영자 액션이 잘못된 (과거) 레코드를 대상으로 하는 문제 수정.

---

## 3. 테스트 설계

**파일**: `tests/test_dessert_data_fix.py`

### 3-1. Fixture: 테스트 DB 세팅

```python
@pytest.fixture
def repo_with_data(tmp_path):
    """역순 ID 데이터로 MAX(id) vs MAX(judgment_period_end) 차이를 재현"""
    # item_cd='A001' — 최신(03-04)=STOP_RECOMMEND가 낮은 ID, 과거(02-11)=KEEP이 높은 ID
    # item_cd='A002' — 최신(03-04)=KEEP
    # item_cd='A003' — 최신(03-04)=WATCH
```

### 3-2. 테스트 케이스

| # | 테스트 | 검증 내용 |
|---|--------|---------|
| 1 | `test_weekly_trend_correct_week_numbers` | 서로 다른 주차 데이터가 올바른 W값으로 분리 반환 |
| 2 | `test_weekly_trend_not_all_w0` | 복수 주차 존재 시 W0 단일 포인트가 아님 |
| 3 | `test_pending_stop_count_with_reversed_ids` | ID 역순에도 최신 judgment_period_end의 STOP_RECOMMEND 카운트 |
| 4 | `test_pending_stop_count_zero_when_no_stops` | STOP_RECOMMEND 없으면 0 |
| 5 | `test_pending_stop_count_excludes_actioned` | operator_action 있으면 제외 |
| 6 | `test_batch_update_targets_latest_period` | 최신 judgment_period_end 레코드 대상 업데이트 확인 |
| 7 | `test_batch_update_ignores_old_period_stops` | 과거 기간의 STOP_RECOMMEND는 무시 |
| 8 | `test_summary_and_pending_count_consistent` | `get_decision_summary().current.STOP_RECOMMEND` == `get_pending_stop_count()` (미처리 시) |
| 9 | `test_weekly_trend_empty_returns_empty_list` | 데이터 없으면 빈 리스트 |
| 10 | `test_batch_update_no_items_returns_empty` | 빈 item_cds 리스트 → 빈 결과 |

---

## 4. 구현 순서

1. **Fix 1** — `get_weekly_trend()` strftime 1줄 수정
2. **Fix 2** — `get_pending_stop_count()` 서브쿼리 3줄 수정
3. **Fix 3** — `batch_update_operator_action()` 서브쿼리 3줄 수정
4. **테스트** — `test_dessert_data_fix.py` 작성 + 실행
5. **기존 테스트** — `pytest tests/` 전체 통과 확인
6. **테스트 데이터 정리** — 기존 48건 삭제 (실제 판단 실행은 별도 작업)

---

## 5. 영향 범위 교차 확인

### `dessert_decision_repo.py` 내 전체 메서드별 "최신 레코드" 기준 현황

| 메서드 | 현재 기준 | 수정 후 | 비고 |
|--------|----------|---------|------|
| `get_latest_decisions()` | `MAX(judgment_period_end)` | 변경 없음 | ✓ 정상 |
| `get_confirmed_stop_items()` | `MAX(judgment_period_end)` | 변경 없음 | ✓ 정상 |
| `get_stop_recommended_items()` | `MAX(judgment_period_end)` | 변경 없음 | ✓ 정상 |
| `get_decision_summary()` | `MAX(judgment_period_end)` | 변경 없음 | ✓ 정상 |
| `get_pending_stop_count()` | ~~`MAX(id)`~~ | **→ `MAX(judgment_period_end)`** | 🔴 Fix 2 |
| `batch_update_operator_action()` | ~~`MAX(id)`~~ | **→ `MAX(judgment_period_end)`** | 🔴 Fix 3 |
| `get_weekly_trend()` | N/A (전체 이력) | strftime 수정 | 🔴 Fix 1 |
| `get_item_decision_history()` | `ORDER BY judgment_period_end DESC` | 변경 없음 | ✓ 정상 |
| `get_decisions_by_period()` | 기간 필터 | 변경 없음 | ✓ 정상 |
| `update_operator_action()` | `WHERE id = ?` (직접 지정) | 변경 없음 | ✓ 정상 |
| `save_decisions_batch()` | UPSERT (conflict key) | 변경 없음 | ✓ 정상 |

→ **수정 후 전체 메서드가 `MAX(judgment_period_end)` 기준으로 통일됨.**
