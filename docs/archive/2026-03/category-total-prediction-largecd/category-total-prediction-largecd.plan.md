# Plan: category-total-prediction-largecd

## 1. 개요

**목표**: 대분류(large_cd) 기반 총량 예측으로 카테고리 수요 예측 정밀화

**현재 상태**:
- `CategoryDemandForecaster`가 mid_cd 단위(001~005)로 총량 예측 + floor 보충
- 개별 mid_cd 수준에서만 작동, 대분류 단위의 상위 보정 없음
- large_cd 컬럼이 mid_categories, product_details에 v47에서 추가됨

**개선 방향**:
- large_cd 단위 총량 예측 (WMA) → 하위 mid_cd 비율 배분
- 개별 상품 예측 합계 < mid_cd 예상 수요 시 floor 보충
- 기존 CategoryDemandForecaster(mid_cd level)를 보완하는 상위 계층 로직

## 2. 범위 (Scope)

### In-Scope
- `LargeCategoryForecaster` 클래스 신규 생성
- large_cd별 daily_sales 합계 WMA 예측
- 하위 mid_cd별 최근 14일 매출 비율 계산
- 총량 x mid_cd 비율 = mid_cd별 예상 수요
- 개별 상품 예측 합계 < mid_cd 예상 수요면 부족분 floor 보충
- auto_order.py에 통합 (기존 CategoryDemandForecaster 뒤에 실행)
- prediction_config.py에 `large_category_floor` 설정 추가
- constants.py에 LARGE_CD_TO_MID_CD 매핑 추가
- 15+ 단위 테스트

### Out-of-Scope
- DB 스키마 변경 (기존 v49 유지, large_cd 컬럼은 이미 존재)
- ML 모델 변경
- 기존 CategoryDemandForecaster 수정 (보완만)

## 3. 핵심 알고리즘

```
1. large_cd별 daily_sales 합계 → WMA 총량 예측
   - JOIN: daily_sales + common.products (mid_cd) + common.mid_categories (large_cd)
   - 또는 common.product_details (large_cd)
   - WMA: 최근 14일, 선형 가중 (최신일 = 가장 높은 가중치)

2. 하위 mid_cd별 최근 14일 매출 비율 계산
   - 각 mid_cd의 매출 합계 / large_cd 전체 매출 합계

3. 총량 x mid_cd 비율 = mid_cd별 예상 수요

4. 개별 상품 예측 합계 vs mid_cd 예상 수요 비교
   - 합계 < 예상 수요 * threshold → 부족분 보충
```

## 4. 적용 대상 large_cd

| large_cd | 이름 | 하위 mid_cd |
|----------|------|-------------|
| 01 | 간편식사 | 001, 002, 003, 004, 005 |
| 02 | 즉석식품 | 006, 007, 008, 009, 010, 011, 012 |
| 12 | 과자류 | 015, 016, 017, 020, 021 |

초기 구현은 설정 기반으로 target_large_cds를 지정하여 확장 가능하게.

## 5. 변경 파일 목록

| 파일 | 변경 내용 |
|------|-----------|
| `src/prediction/large_category_forecaster.py` | 신규: LargeCategoryForecaster 클래스 |
| `src/prediction/prediction_config.py` | `large_category_floor` 설정 블록 추가 |
| `src/settings/constants.py` | LARGE_CD_TO_MID_CD 매핑 추가 |
| `src/order/auto_order.py` | LargeCategoryForecaster 통합 |
| `tests/test_large_category_forecaster.py` | 15+ 테스트 |

## 6. 리스크 및 완화

| 리스크 | 완화 |
|--------|------|
| large_cd 데이터 미등록 | mid_categories.large_cd 누락 시 product_details.large_cd fallback |
| 기존 mid_cd floor와 중복 보충 | LargeCategoryForecaster는 기존 floor 결과 이후에 실행, 이미 보충된 수량 반영 |
| 과도한 보충 | threshold(70%) + max_add_per_item(2) 제한 |

## 7. 의존성

- mid_categories.large_cd (v47에서 추가)
- product_details.large_cd (v47에서 추가)
- DBRouter (cross-DB query: store DB + common DB ATTACH)
- 기존 CategoryDemandForecaster 패턴 참조
