# Plan: 스케줄러 Phase 1.61 + Phase 1.7 버그 수정

## 1. 목적
- Phase 1.61 DemandClassifier가 배포 이후 **26회 연속 실패** (`no such table: daily_sales`)
- Phase 1.7 predict_and_log()가 Phase 2의 부분 기록(~100건)에 의해 전체 예측 로깅(~2000건) 스킵
- 46513 점포의 2/27 푸드 예측이 22건으로 급감 (정상: ~120건)
- 수요 패턴 분류 + 전체 예측 로깅이 정상 동작하도록 복구

## 2. 현재 문제점

### Bug A: `get_all_active_items()` DB 연결 오류
**파일**: `src/infrastructure/database/repos/product_detail_repo.py:365`

```python
# ProductDetailRepository.db_type = "common"  -> common.db 연결
conn = self._get_conn()  # -> common.db (daily_sales 없음!)
cursor.execute("""
    SELECT DISTINCT p.item_cd
    FROM products p
    INNER JOIN daily_sales ds ON p.item_cd = ds.item_cd  -- ERROR!
    ...
""")
```

- `ProductDetailRepository`는 `db_type = "common"` (common.db 연결)
- `get_all_active_items(store_id)` 호출 시 `daily_sales` JOIN 시도
- `daily_sales`는 매장 DB(stores/{store_id}.db)에만 존재
- **결과**: `no such table: daily_sales` 에러 → Phase 1.61 DemandClassifier 전면 실패

### Bug B: `predict_and_log()` 중복 체크 과민
**파일**: `src/prediction/improved_predictor.py:3119`

```python
if existing > 0:  # Phase 2가 100건만 기록해도 전체 스킵!
    logger.info(f"예측 로깅 스킵: 이미 {existing}건")
    return 0
```

- Phase 1.7(독립 예측 로깅)과 Phase 2(자동발주) 모두 prediction_logs에 기록
- Phase 2가 먼저 부분 기록(발주 대상 ~100건만)하면 Phase 1.7이 전체(~2000건) 스킵
- **자정 넘김 세션**: 23:50에 시작 → Phase 1.7이 자정 전 실행 → 전날 기록과 중복 체크
- **결과**: 46513은 104건, 46704는 1891건이라는 비대칭적 기록

## 3. 수정 계획

### 3-1. get_all_active_items() DB 연결 수정 (product_detail_repo.py)
- `store_id` 제공 시 `DBRouter.get_store_connection_with_common(store_id)` 사용
- SQL: `FROM common.products p INNER JOIN daily_sales ds ...` (ATTACH 후 접두사)
- `store_id` 없는 경우: 기존 레거시 DB 연결 유지 (하위 호환)

### 3-2. predict_and_log() 중복 체크 개선 (improved_predictor.py)
- `existing > 0` → `existing >= FULL_PREDICTION_THRESHOLD(500)` 변경
- 500건 미만(Phase 2 부분 기록) → 삭제 후 전체 재기록
- 500건 이상(Phase 1.7 정상 기록) → 스킵

## 4. 수정 파일 목록

| 파일 | 변경 내용 | 변경 규모 |
|------|-----------|-----------|
| `src/infrastructure/database/repos/product_detail_repo.py` | get_all_active_items() DB 연결 분기 | ~20줄 |
| `src/prediction/improved_predictor.py` | predict_and_log() 중복 체크 임계값 | ~15줄 |

## 5. 테스트 계획
- 기존 테스트 2255개 전체 통과 확인
- `get_all_active_items(store_id='46513')` 직접 호출 검증
- DemandClassifier 배치 분류 E2E 검증
- predict_and_log() 부분 기록 재기록 시나리오 검증

## 6. 위험 요소
- `get_all_active_items(store_id=None)` 호출 패턴이 다른 코드에도 있는지 확인 필요
- predict_and_log()의 DELETE + re-INSERT 시 동시 실행 세션과의 경쟁 조건 (SQLite timeout으로 대응)
- FULL_PREDICTION_THRESHOLD=500이 소규모 점포에서 적절한지 모니터링 필요

## 7. 예상 효과
- Phase 1.61: DemandClassifier 수요 패턴 분류 정상 동작 → 비식품 예측 정확도 향상
- Phase 1.7: 전체 예측 로깅 (~2000건) → ML 학습 데이터 완전성 확보
- 46513 푸드 예측: 22건 → ~120건 회복 (정상 수준)
