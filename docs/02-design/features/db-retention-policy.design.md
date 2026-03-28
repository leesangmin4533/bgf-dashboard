# DB 보존 정책 설계서

## 1. 보존 규칙

| 테이블 | 보존 기간 | 삭제 조건 | 근거 |
|--------|----------|----------|------|
| eval_outcomes | 90일 | eval_date < 90일 전 | ML 학습 윈도우 90일 |
| prediction_logs | 60일 | prediction_date < 60일 전 | 디버깅 용도 |
| hourly_sales_detail | 90일 | sales_date < 90일 전 | 시간대별 분석 |
| calibration_history | 120일 | calibrated_at < 120일 전 | 보정 추이 확인 |

## 2. 구현 위치

`src/scheduler/phases/collection.py`의 수집 Phase 마지막에 purge 단계 추가.

이유:
- 수집 완료 후 실행 → 새 데이터 저장 확인 후 오래된 데이터 삭제
- 발주 실행 전에 완료 → 발주에 영향 없음

## 3. 구현 설계

### 함수: `purge_old_data(store_id, conn)`

```python
def purge_old_data(store_id: str, conn) -> Dict[str, int]:
    """오래된 데이터 삭제 (보존 정책)"""
    retention = {
        "eval_outcomes": ("eval_date", 90),
        "prediction_logs": ("prediction_date", 60),
        "hourly_sales_detail": ("sales_date", 90),
        "calibration_history": ("calibrated_at", 120),
    }

    results = {}
    for table, (date_col, days) in retention.items():
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        cursor = conn.execute(
            f"DELETE FROM {table} WHERE {date_col} < ?", (cutoff,)
        )
        results[table] = cursor.rowcount
    conn.commit()
    return results
```

### 호출 위치

```python
# src/scheduler/phases/collection.py
# run_collection_phases() 마지막에 추가

# Phase 1.36: 데이터 보존 정책 (오래된 기록 삭제)
from src.infrastructure.database.connection import DBRouter
purge_conn = DBRouter.get_store_connection(store_id)
purge_result = purge_old_data(store_id, purge_conn)
if any(purge_result.values()):
    logger.info(f"[Phase 1.36] 데이터 정리: {purge_result}")
purge_conn.close()
```

## 4. 주의사항

- VACUUM 사용 금지 (WAL 모드에서 DB 잠금)
- DELETE만 실행, SQLite가 페이지 자동 재활용
- date_col이 NULL인 행은 삭제 안 됨 (WHERE 조건에 의해 자연 보호)
- 로그에 삭제 건수 기록 (감사 추적)

## 5. 테스트 계획

- [ ] 각 테이블의 date_col이 실제 존재하는지 확인
- [ ] 91일 전 데이터가 삭제되는지 확인
- [ ] 89일 전 데이터가 보존되는지 확인
- [ ] 기존 테스트 전체 통과
