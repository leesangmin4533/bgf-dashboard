# PDCA Report: CostOptimizer 활성화

## 1. 개요
| 항목 | 내용 |
|------|------|
| Feature | cost-optimizer-activation |
| 목적 | 마진x회전율 2D 매트릭스 기반 발주 비용 최적화 활성화 |
| Match Rate | 99% |
| 신규 테스트 | 16개 |
| 전체 테스트 | 2,185개 (전부 통과) |

## 2. 문제 및 해결

### 버그 A: 레거시 DB 경로
- **문제**: `DB_PATH = bgf_sales.db` (레거시 단일 DB) 직접 참조
- **해결**: `DBRouter.get_store_connection_with_common(store_id)` 사용
- **결과**: 매장 분할 DB(stores/{id}.db) + common.db ATTACH 정상 동작

### 버그 B: SQL 쿼리
- **문제**: `JOIN products p` — 매장 DB에 products 테이블 없음
- **해결**: `JOIN common.products p` — ATTACH된 common DB 접두사 사용
- **결과**: daily_sales(매장 DB) + products(common DB) 크로스 DB JOIN 정상

### 활성화
- **문제**: `self._cost_optimizer = None` (improved_predictor.py)
- **해결**: `CostOptimizer(store_id=self.store_id)` 인스턴스 생성
- **결과**: 3곳에서 활성화 (안전재고 계수, 폐기계수, SKIP 오프셋)

## 3. 수정 파일

| 파일 | 변경 | 상세 |
|------|------|------|
| `src/prediction/cost_optimizer.py` | 수정 | DB 경로 제거, import 변경, SQL 수정 |
| `src/prediction/improved_predictor.py` | 수정 | CostOptimizer import 추가, None → 인스턴스 |
| `tests/test_cost_optimizer_activation.py` | 신규 | 16개 테스트 (11클래스) |

## 4. CostOptimizer 동작 요약

```
상품별 margin_rate + daily_avg(회전율)
  → cost_ratio = margin / (100 - margin)
  → margin_level (high/mid/base/low)
  → turnover_level (high/mid/low/unknown)
  → 2D 매트릭스 조회:
      margin_multiplier  : 안전재고 계수 (0.80 ~ 1.35)
      disuse_modifier    : 폐기계수 보정 (0.80 ~ 1.20)
      skip_offset        : SKIP 임계값 (−0.7 ~ +0.7)
  + 판매비중 보너스 (category_share ≥ 10% → +0.05)
  → composite_score = margin_multiplier + share_bonus
```

### 적용 효과
- **고마진+고회전**: 안전재고↑(×1.35), 폐기허용↑(×1.20), SKIP 어렵게(+0.7일)
- **저마진+저회전**: 안전재고↓(×0.80), 폐기억제↑(×0.80), SKIP 쉽게(−0.7일)

## 5. 테스트 결과

| 클래스 | 테스트 수 | 내용 |
|--------|:--------:|------|
| TestDisabled | 1 | 비활성 시 기본값 |
| TestNoMargin | 1 | margin 없으면 disabled |
| TestMarginLevels | 2 | 고/저 마진 multiplier |
| TestMatrix2D | 2 | 2D 매트릭스 조회 |
| TestFallback1D | 1 | unknown → 1D 폴백 |
| TestCategoryShares | 2 | 분할 DB 판매비중 |
| TestCache | 1 | 캐시 히트 |
| TestBulk | 1 | 일괄 조회 |
| TestConfigLoad | 1 | eval_params 로드 |
| TestImprovedPredictorIntegration | 1 | 활성화 확인 |
| TestPreOrderEvalSkipOffset | 1 | SKIP 오프셋 |
| TestDBRouterUsage | 2 | 소스 감사 |

## 6. 미해결 (LOW)
- improved_predictor 통합 테스트 2개 미작성 (safety_stock 곱셈, disuse_modifier 적용)
- 단위 테스트로 CostInfo 값은 검증됨, 적용 로직은 기존 코드에서 검증
- 필요 시 추후 추가 가능

## 7. 완료 일자
- 2026-02-26
