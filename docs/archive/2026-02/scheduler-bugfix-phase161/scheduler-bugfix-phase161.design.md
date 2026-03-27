# Design: 스케줄러 Phase 1.61 + Phase 1.7 버그 수정

## 1. 개요

Plan 문서(`scheduler-bugfix-phase161.plan.md`)에 정의된 2개 버그를 수정하는 설계.
- **Bug A**: `get_all_active_items()` DB 연결 오류 (Phase 1.61 DemandClassifier 전면 실패)
- **Bug B**: `predict_and_log()` 중복 체크 과민 (Phase 1.7 예측 로깅 0건)

## 2. 아키텍처 컨텍스트

### 2-1. DB 분할 구조
```
data/
├── common.db           # products, mid_categories, product_details 등
└── stores/
    ├── 46513.db        # daily_sales, prediction_logs, order_tracking 등
    └── 46704.db
```

### 2-2. Repository DB 연결 패턴
```
BaseRepository._get_conn()
  ├── db_type="common"  → DBRouter.get_common_connection()     → common.db
  └── db_type="store"   → DBRouter.get_store_connection(sid)   → stores/{sid}.db
                           + attach_common_with_views()          (common ATTACH)
```

### 2-3. Daily Job Phase 순서 (관련 부분)
```
Phase 1.6  → 예측 실적 비교
Phase 1.61 → DemandClassifier 수요패턴 분류  ← Bug A
Phase 1.65 → 유령 재고 정리
Phase 1.7  → 독립 예측 로깅                   ← Bug B
Phase 2    → 자동발주 (부분 예측 로깅)
```

## 3. Bug A 설계: get_all_active_items() DB 연결 수정

### 3-1. 문제 분석

| 항목 | 값 |
|------|-----|
| 클래스 | `ProductDetailRepository` |
| db_type | `"common"` |
| 메서드 | `get_all_active_items(days, store_id)` |
| 원인 | `self._get_conn()` → common.db 연결, `daily_sales` 없음 |
| 영향 | Phase 1.61 DemandClassifier **26회 연속 실패** |

### 3-2. 수정 설계

**파일**: `src/infrastructure/database/repos/product_detail_repo.py:365-419`

**분기 전략**:
```
get_all_active_items(days, store_id)
  ├── store_id 있음 → DBRouter.get_store_connection_with_common(store_id)
  │                    SQL: common.products p JOIN daily_sales ds
  └── store_id 없음 → self._get_conn() (레거시 단일 DB)
                       SQL: products p JOIN daily_sales ds
```

**핵심 변경점**:
1. `store_id` 제공 시 `DBRouter.get_store_connection_with_common(store_id)` 사용
2. ATTACH된 common DB는 `common.products` 접두사로 접근
3. `daily_sales`는 매장 DB 기본 스키마 → 접두사 불필요
4. `store_id` 없는 경우 기존 로직 100% 유지 (하위 호환)

**SQL 변경**:
```sql
-- Before (common.db에서 실행 → daily_sales 없음!)
SELECT DISTINCT p.item_cd
FROM products p
INNER JOIN daily_sales ds ON p.item_cd = ds.item_cd
WHERE ds.sales_date >= date('now', ? || ' days')

-- After (store DB + common ATTACH에서 실행)
SELECT DISTINCT p.item_cd
FROM common.products p              -- ATTACH된 common.db
INNER JOIN daily_sales ds ON ...     -- 매장 DB의 daily_sales
WHERE ds.sales_date >= date('now', ? || ' days')
```

### 3-3. 데이터 흐름

```
daily_job.py Phase 1.61
  → ProductDetailRepository().get_all_active_items(days=30, store_id='46513')
    → DBRouter.get_store_connection_with_common('46513')
      → stores/46513.db OPEN + ATTACH common.db AS common
    → SQL: common.products JOIN daily_sales
    → Return: ~2082개 활성 상품 코드
  → DemandClassifier(store_id='46513').classify_batch(item_codes)
    → 수요패턴 분류 (daily/frequent/intermittent/slow)
```

## 4. Bug B 설계: predict_and_log() 중복 체크 개선

### 4-1. 문제 분석

| 항목 | 값 |
|------|-----|
| 클래스 | `ImprovedPredictor` |
| 메서드 | `predict_and_log()` |
| 원인 | `existing > 0` → Phase 2 부분 기록(~100건)에도 전체 스킵 |
| 영향 | 46513: 104건(Phase 2만), 46704: 1891건(Phase 1.7 정상) |

### 4-2. 수정 설계

**파일**: `src/prediction/improved_predictor.py:3117-3145`

**임계값 기반 3분기 전략**:
```
predict_and_log()
  → COUNT prediction_logs WHERE prediction_date = today
    ├── existing >= 500 → 스킵 (Phase 1.7 정상 기록 있음)
    ├── 0 < existing < 500 → DELETE 후 전체 재기록 (Phase 2 부분 기록)
    └── existing == 0 → 전체 신규 기록
```

**상수 정의**:
```python
FULL_PREDICTION_THRESHOLD = 500
```
- 활성 상품 ~2000건 기준, 500건 미만은 부분 기록으로 판단
- Phase 2 자동발주는 보통 50~200건만 기록

### 4-3. 시퀀스 다이어그램

```
정상 케이스 (Phase 1.7 먼저 실행):
Phase 1.7 → COUNT=0 → 전체 예측 ~2000건 기록
Phase 2   → log_predictions_batch_if_needed() → COUNT>0 → 스킵

Phase 2가 먼저 실행된 케이스 (자정 넘김):
Phase 2   → 부분 기록 ~100건
Phase 1.7 → COUNT=100 (<500) → DELETE 100건 → 전체 ~2000건 재기록

이미 정상 기록된 케이스:
Phase 1.7 → COUNT=1891 (>=500) → 스킵
```

### 4-4. 경쟁 조건 대응

```
Phase 1.7 DELETE + INSERT  ←→  Phase 2 동시 실행?
  → SQLite 파일 락으로 순서 보장 (busy_timeout=5000ms)
  → Phase 순서상 1.7이 먼저 실행, Phase 2가 나중 → 실질적 경쟁 없음
```

## 5. 수정 파일 요약

| 파일 | 변경 내용 | 줄 수 |
|------|-----------|-------|
| `product_detail_repo.py:365-419` | store_id 분기 + common.products 접두사 | ~20줄 |
| `improved_predictor.py:3117-3145` | FULL_PREDICTION_THRESHOLD + 3분기 로직 | ~15줄 |

## 6. 테스트 설계

### 6-1. 기존 테스트 호환
- 전체 2255개 테스트 통과 확인 완료

### 6-2. Bug A 검증
- `get_all_active_items(store_id='46513')` → 2082개 반환 확인
- `get_all_active_items(store_id='46704')` → 2517개 반환 확인
- `get_all_active_items(store_id=None)` → 레거시 경로 동작 확인

### 6-3. Bug B 검증
- existing=0 → 전체 기록 시나리오
- existing=100(부분) → DELETE + 전체 재기록 시나리오
- existing=1891(정상) → 스킵 시나리오

## 7. 위험 요소 및 완화

| 위험 | 완화 |
|------|------|
| store_id=None 호출 패턴 깨짐 | else 분기에 기존 로직 100% 유지 |
| DELETE + INSERT 시 데이터 유실 | SQLite busy_timeout, Phase 순서 보장 |
| 소규모 점포에서 THRESHOLD=500 적절성 | 최소 활성 상품이 ~500개 이상 확인됨 |
| common.products 접두사 SQL 호환성 | ATTACH 패턴은 프로젝트 전반에서 사용 중 |
