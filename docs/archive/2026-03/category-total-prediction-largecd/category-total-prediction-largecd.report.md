# Report: category-total-prediction-largecd

## 요약

| 항목 | 값 |
|------|-----|
| 기능명 | 카테고리 총량 예측 정밀화 (large_cd) |
| Match Rate | **100%** (15/15) |
| 테스트 | **19개 전부 통과** |
| 기존 테스트 | **15개 통과** (기존 category_demand_forecaster 무영향) |
| 신규 파일 | 2개 (large_category_forecaster.py, test_large_category_forecaster.py) |
| 수정 파일 | 3개 (constants.py, prediction_config.py, auto_order.py) |
| DB 변경 | 없음 (기존 v49 유지) |

## 구현 내용

### 핵심 알고리즘
1. **large_cd별 총량 WMA 예측**: 최근 14일 daily_sales 합계의 가중 이동 평균
2. **mid_cd 비율 계산**: large_cd 내 각 mid_cd의 매출 비율 (합 = 1.0)
3. **mid_cd별 예상 수요 배분**: 총량 x mid_cd 비율
4. **floor 보충**: 개별 상품 예측 합 < mid_cd 예상 수요의 75%이면 부족분 보충

### 적용 대상
- large_cd=01 (간편식사): 001~005
- large_cd=02 (즉석식품): 006~012
- large_cd=12 (과자류): 015~021
- 설정 기반으로 추가 large_cd 확장 가능

### 파이프라인 위치
```
auto_order.py → get_recommendations()
  ├─ apply_food_daily_cap()           # 푸드 총량 상한
  ├─ CategoryDemandForecaster         # mid_cd level floor (기존)
  └─ LargeCategoryForecaster          # large_cd level floor (신규)
```

## 파일 변경 상세

### 신규: src/prediction/large_category_forecaster.py (340줄)
- `LargeCategoryForecaster` 클래스
- `forecast_large_cd_total()`: large_cd WMA 총량 예측
- `get_mid_cd_ratios()`: mid_cd 비율 계산
- `distribute_to_mid_cd()`: 총량 배분
- `_apply_floor_correction()`: 부족분 보충
- `_get_supplement_candidates()`: 보충 후보 선정 (판매 빈도순)
- `_distribute_shortage()`: 부족분 분배

### 수정: src/settings/constants.py (+22줄)
- `LARGE_CD_TO_MID_CD` 매핑 추가 (18개 large_cd)
- mid_categories.large_cd DB 미등록 시 fallback 용도

### 수정: src/prediction/prediction_config.py (+12줄)
- `large_category_floor` 설정 블록 추가
- enabled, target_large_cds, threshold, max_add_per_item, wma_days, ratio_days, min_candidate_sell_days

### 수정: src/order/auto_order.py (+15줄)
- `LargeCategoryForecaster` import 추가
- `__init__`에서 `_large_category_forecaster` 인스턴스 생성
- `get_recommendations()`에서 기존 CategoryDemandForecaster 뒤에 실행

### 신규: tests/test_large_category_forecaster.py (310줄)
- 19개 테스트 (7개 클래스)
- WMA 계산(3), mid_cd 비율(3), 총량 배분(2), floor 보충(4), 통합(3), 엣지(2), 속성(2)

## 테스트 결과

```
============================= 19 passed in 15.16s =============================
```

| 테스트 카테고리 | 수 | 결과 |
|----------------|----|----|
| WMA 계산 | 3 | PASS |
| mid_cd 비율 | 3 | PASS |
| 총량 배분 | 2 | PASS |
| floor 보충 | 4 | PASS |
| 통합 플로우 | 3 | PASS |
| 엣지 케이스 | 2 | PASS |
| 속성 검증 | 2 | PASS |
| **합계** | **19** | **ALL PASS** |

## 설계 결정 사항

| 결정 | 이유 |
|------|------|
| 기존 CategoryDemandForecaster 변경하지 않음 | 역할 분리: mid_cd level(기존) vs large_cd level(신규) |
| large_cd → mid_cd 매핑을 DB + 상수 2중 폴백 | mid_categories.large_cd 미등록 매장 대비 |
| threshold 0.75 (기존 0.7보다 높음) | large_cd level은 mid_cd level보다 상위 보정이므로 더 보수적 |
| max_add_per_item 2 (기존 1보다 높음) | large_cd 내 여러 mid_cd에 걸쳐 부족분이 분산되므로 |
| WMA 기간 14일 (기존 7일보다 길음) | large_cd 총량은 변동성이 낮으므로 장기 추세 반영 |
| auto_order.py 통합 (improved_predictor.py 아닌) | 기존 패턴과 동일 (CategoryDemandForecaster도 auto_order.py) |

## 안전장치

- `enabled` 설정으로 즉시 비활성화 가능
- Exception wrapper: 실패 시 원본 order_list 유지
- `max_add_per_item` + `threshold`로 과잉 보충 방지
- 전체 보충량 로깅 (large_cd별, mid_cd별 상세)
- CUT/SKIP 상품 자동 제외
