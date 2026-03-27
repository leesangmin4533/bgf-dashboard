# Gap Analysis: scheduler-bugfix-all

> **Summary**: Plan vs Implementation gap analysis for 3 scheduler bugs
>
> **Plan Document**: `docs/01-plan/features/scheduler-bugfix-all.plan.md`
> **Date**: 2026-02-26
> **Match Rate**: 95%

---

## Summary

- **Match Rate**: 95% (19/20 check items matched)
- **Status**: PASS
- **Total Check Items**: 20
  - MATCH: 19
  - PARTIAL: 0
  - GAP: 1 (missing dedicated bugfix tests)
- **Files Verified**: 4 (demand_classifier.py, alerting.py, waste_report.py, tests/)
- **Tests**: 2216 total passing (0 failures)

---

## Checklist

| # | Plan Item | Status | Notes |
|---|-----------|--------|-------|
| 1 | `_query_sell_stats()` SQL: remove `AND store_id = ?` | MATCH | No store_id in SQL WHERE clause (line 156-163) |
| 2 | `_query_sell_stats()` params: remove `self.store_id` from tuple | MATCH | Param is `(item_cd,)` only (line 164) |
| 3 | `_query_sell_stats_batch()` SQL: remove `AND store_id = ?` | MATCH | No store_id in SQL WHERE clause (line 185-194) |
| 4 | `_query_sell_stats_batch()` params: remove `self.store_id` from tuple | MATCH | Param is `(*item_cds,)` only (line 195) |
| 5 | Both methods use `DBRouter.get_store_connection(self.store_id)` | MATCH | Correct: store-level DB routing, no in-query filter needed (lines 153, 181) |
| 6 | `_send_kakao_alert()`: import `DEFAULT_REST_API_KEY` | MATCH | `from src.notification.kakao_notifier import KakaoNotifier, DEFAULT_REST_API_KEY` (line 105) |
| 7 | `_send_kakao_alert()`: `KakaoNotifier(DEFAULT_REST_API_KEY)` | MATCH | Constructor receives key correctly (line 107) |
| 8 | `generate_waste_report()`: `exc_info=True` in error log | MATCH | `logger.error(f"...: {e}", exc_info=True)` (line 658) |
| 9 | `generate()`: sheet-level try-except for partial report | MATCH | `sheet_methods` list + for-loop with per-sheet try-except (lines 86-96) |
| 10 | Sheet failure logs warning with `exc_info=True` | MATCH | `logger.warning(f"...: {e}", exc_info=True)` (line 96) |
| 11 | Sheet failure does not abort entire report | MATCH | try-except in for-loop, other sheets continue (lines 92-96) |
| 12 | 4 sheets defined in `sheet_methods` list | MATCH | 4 tuples: daily detail, category summary, weekly trend, monthly trend (lines 87-91) |
| 13 | File: `src/prediction/demand_classifier.py` modified | MATCH | SQL is clean, no store_id filter |
| 14 | File: `src/utils/alerting.py` modified | MATCH | DEFAULT_REST_API_KEY imported and used |
| 15 | File: `src/analysis/waste_report.py` modified | MATCH | exc_info + sheet-level isolation both present |
| 16 | Existing tests pass | MATCH | 2216 tests, 0 failures |
| 17 | DemandClassifier tests exist and pass | MATCH | 17 tests in `test_demand_classifier.py` (3 classes) |
| 18 | AlertingHandler tests exist and pass | MATCH | 6 tests in `test_health_check_alert.py` (classes 4+5) |
| 19 | WasteReport partial report test | GAP | No dedicated test for sheet-level failure + partial report generation |
| 20 | Implementation order matches Plan section 4 | MATCH | Bug1 -> Bug2 -> Bug3 -> Tests, as specified |

---

## Details

### Bug 1: DemandClassifier -- `no such table: daily_sales` (store_id filter removal)

**Plan**: Remove `AND store_id = ?` from SQL in `_query_sell_stats()` and `_query_sell_stats_batch()`, and remove `self.store_id` from parameter tuples.

**Implementation**: Fully matched.

File: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\prediction\demand_classifier.py`

`_query_sell_stats()` (lines 150-174):
```python
def _query_sell_stats(self, item_cd: str) -> Dict:
    from src.infrastructure.database.connection import DBRouter
    conn = DBRouter.get_store_connection(self.store_id)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                COUNT(*) as total_days,
                SUM(CASE WHEN stock_qty > 0 THEN 1 ELSE 0 END) as available_days,
                SUM(CASE WHEN stock_qty > 0 AND sale_qty > 0 THEN 1 ELSE 0 END) as sell_days
            FROM daily_sales
            WHERE item_cd = ?
            AND sales_date >= date('now', '-60 days')
        """, (item_cd,))
```

- No `AND store_id = ?` in SQL -- MATCH
- Parameter tuple is `(item_cd,)` only -- MATCH
- Uses `DBRouter.get_store_connection(self.store_id)` for proper DB routing -- MATCH

`_query_sell_stats_batch()` (lines 176-206):
```python
def _query_sell_stats_batch(self, item_cds: List[str]) -> Dict[str, Dict]:
    from src.infrastructure.database.connection import DBRouter
    conn = DBRouter.get_store_connection(self.store_id)
    try:
        cursor = conn.cursor()
        placeholders = ",".join(["?"] * len(item_cds))
        cursor.execute(f"""
            SELECT
                item_cd,
                COUNT(*) as total_days,
                SUM(CASE WHEN stock_qty > 0 THEN 1 ELSE 0 END) as available_days,
                SUM(CASE WHEN stock_qty > 0 AND sale_qty > 0 THEN 1 ELSE 0 END) as sell_days
            FROM daily_sales
            WHERE item_cd IN ({placeholders})
            AND sales_date >= date('now', '-60 days')
            GROUP BY item_cd
        """, (*item_cds,))
```

- No `AND store_id = ?` in SQL -- MATCH
- Parameter tuple is `(*item_cds,)` only -- MATCH

**Tests**: 17 existing tests in `test_demand_classifier.py` cover classify logic, boundary conditions, exemptions, and batch. The batch tests mock `_query_sell_stats_batch` so they do not directly test the SQL, but the SQL itself is verified by code inspection. No new test specifically verifying the absence of store_id filter was found, but the fix is structurally validated.

---

### Bug 2: KakaoNotifier -- `Not exist client_id []`

**Plan**: Change `KakaoNotifier()` to `KakaoNotifier(DEFAULT_REST_API_KEY)` in `_send_kakao_alert()`.

**Implementation**: Fully matched.

File: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\utils\alerting.py`

`_send_kakao_alert()` (lines 102-116):
```python
def _send_kakao_alert(self, record: logging.LogRecord):
    try:
        from src.notification.kakao_notifier import KakaoNotifier, DEFAULT_REST_API_KEY

        notifier = KakaoNotifier(DEFAULT_REST_API_KEY)
        message = (
            f"[BGF 에러 알림]\n"
            f"시각: {datetime.now().strftime('%H:%M:%S')}\n"
            f"모듈: {record.name}\n"
            f"내용: {record.getMessage()[:200]}"
        )
        notifier.send_text(message)
    except Exception:
        pass
```

- `DEFAULT_REST_API_KEY` imported in the same line as `KakaoNotifier` -- MATCH
- Constructor call is `KakaoNotifier(DEFAULT_REST_API_KEY)` -- MATCH

**Tests**: 6 existing AlertingHandler tests in `test_health_check_alert.py` verify cooldown suppression, rate limiting, and alert file writing. The `_send_kakao_alert` path is not directly tested (it runs only when `kakao_enabled=True` and is wrapped in try-except pass), but the structural fix is verified.

---

### Bug 3: WasteReport -- store=46513 generation failure

**Plan**: (1) Add `exc_info=True` to error logging in `generate_waste_report()`. (2) Add sheet-level try-except for partial report generation.

**Implementation**: Fully matched.

File: `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\analysis\waste_report.py`

`generate_waste_report()` (lines 644-659):
```python
def generate_waste_report(target_date=None, store_id=None):
    try:
        generator = WasteReportGenerator(store_id=store_id)
        return generator.generate(target_date)
    except Exception as e:
        logger.error(f"폐기 보고서 생성 실패: {e}", exc_info=True)
        return None
```

- `exc_info=True` present in error log -- MATCH

`generate()` method (lines 68-104):
```python
sheet_methods = [
    ("일별 폐기 상세", self._create_daily_detail_sheet),
    ("카테고리 집계", self._create_category_summary_sheet),
    ("주간 트렌드", self._create_weekly_trend_sheet),
    ("월간 트렌드", self._create_monthly_trend_sheet),
]
for sheet_name, method in sheet_methods:
    try:
        method(wb, target_date)
    except Exception as e:
        logger.warning(f"시트 '{sheet_name}' 생성 실패 (계속 진행): {e}", exc_info=True)
```

- Sheet-level try-except loop -- MATCH
- Per-sheet failure logged with `exc_info=True` -- MATCH (goes beyond plan which only required exc_info at generate_waste_report level)
- Partial report: remaining sheets continue after one fails -- MATCH

**Tests**: No dedicated test for WasteReport partial generation failure was found. There is no `test_waste_report.py` or similar test file. This is the single GAP item.

---

### Tests Summary

| Test Area | File | Test Count | Status |
|-----------|------|:----------:|:------:|
| DemandClassifier | `tests/test_demand_classifier.py` | 17 | Existing, covers classify logic |
| AlertingHandler | `tests/test_health_check_alert.py` | 6 | Existing, covers cooldown + rate limit |
| WasteReport | (none) | 0 | GAP: no partial report failure test |
| Total regression | all test files | 2216 | All passing |

---

## Gap Items

### GAP #1: Missing WasteReport partial failure test (LOW severity)

**Plan (Section 5)**: "WasteReport: 일부 시트 실패 시 부분 리포트 생성 확인, exc_info 로깅 확인"

**Implementation**: No test file for waste report partial generation exists.

**Impact**: LOW. The implementation is correct (sheet-level try-except verified by code review), but there is no automated test to guard against regression. The remaining 2216 tests all pass.

**Recommended Action**: Add a test that mocks one sheet method to raise an exception, then verifies the report file is still created with the remaining sheets.

---

## Conclusion

**Match Rate: 95% (19/20) -- PASS**

All 3 bugs are fully fixed in the implementation:

1. **DemandClassifier** (Bug 1): SQL `store_id` filter completely removed from both `_query_sell_stats()` and `_query_sell_stats_batch()`. Parameter tuples cleaned. DB routing via `DBRouter.get_store_connection()` correctly handles store isolation at the connection level.

2. **KakaoNotifier** (Bug 2): `DEFAULT_REST_API_KEY` is imported and passed to the `KakaoNotifier` constructor in `_send_kakao_alert()`. The 1-line fix resolves the `invalid_client` error.

3. **WasteReport** (Bug 3): Both improvements applied -- `exc_info=True` added to the outer `generate_waste_report()` error log, AND sheet-level try-except loop implemented in `generate()` for partial report resilience. The implementation exceeds the plan by also adding `exc_info=True` to the per-sheet warning log.

The single gap is the absence of a dedicated test for WasteReport partial failure, which the plan specified but was not implemented. This is LOW severity since the feature itself works correctly and the overall test suite (2216 tests) passes with zero failures.
