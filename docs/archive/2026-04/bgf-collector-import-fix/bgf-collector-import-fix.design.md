# Design — bgf-collector-import-fix

## API 매핑
| Before (존재 X) | After (실존) |
|---|---|
| `from src.collectors.bgf_collector import BGFCollector` | `from src.collectors.sales_collector import SalesCollector` |
| `collector = BGFCollector(store_id=sid)` | `collector = SalesCollector(store_id=sid)` |
| `driver = collector.login()` | `collector._ensure_login(); driver = collector.get_driver()` |
| `collector.close()` | `collector.close()` (동일) |

검증:
- `SalesCollector._ensure_login()` — 실패 시 `Exception("Login failed")` raise
- `SalesCollector.get_driver()` — Optional[Any] 반환
- `SalesCollector.close()` — 존재

## 수정 블록 (daily_job.py:928-950)
```python
try:
    from src.collectors.sales_collector import SalesCollector
    collector = SalesCollector(store_id=sid)
    try:
        collector._ensure_login()
    except Exception as login_err:
        logger.warning(f"[D-1] 로그인 실패 (store={sid}): {login_err}")
        all_results[sid] = {"success": False, "error": f"login failed: {login_err}"}
        collector.close()
        continue
    driver = collector.get_driver()
    if driver:
        exec_result = execute_boost_orders(result, driver)
        collector.close()
        all_results[sid] = {
            "success": True,
            "boost_targets": result.boost_targets,
            "executed": exec_result["executed"],
            "failed": exec_result["failed"],
            "reduce_logged": result.reduce_logged,
        }
    else:
        logger.warning(f"[D-1] 드라이버 획득 실패 (store={sid})")
        all_results[sid] = {"success": False, "error": "driver unavailable"}
        collector.close()
```

## 테스트
- `test_daily_job_d1_imports.py` — `from src.scheduler.daily_job import *` 성공 검증
- 통합 테스트는 실전 D-1 트리거에 의존 (2026-04-08 14:00 로그 확인으로 대체)
