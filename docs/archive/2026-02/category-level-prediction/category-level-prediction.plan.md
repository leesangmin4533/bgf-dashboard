# Plan: category-level-prediction

## 1. 개요

신선식품(001~005) 카테고리의 과소발주 문제를 해결한다.
현재 시스템은 **품목별 개별 예측**만 수행하는데, 신선식품은 품목 수가 많고(20~40종)
로테이션이 심하며 재고보유율이 28~39%로 극도로 낮아 개별 예측이 왜곡된다.
**카테고리 총량 예측**을 추가하여 개별 예측의 하한선(floor)으로 활용한다.

## 2. 문제 분석

### 2.1 현상
- 46513점포 주먹밥(002): 3일 평균 매출 13.3개, 자동발주 4개 (30% 수준)
- 46704점포 주먹밥(002): 3일 평균 매출 16.7개, 자동발주 11개 (66% 수준)
- 양 점포 모두 자동발주만으로 부족, 46704는 수동 보충(35개)으로 보완 중

### 2.2 근본 원인: 레코드 부재 = 수요 0 오인
- BGF 매출조회(dsDetail)는 활동 없는 상품의 레코드를 반환하지 않음
- 데이터 부재일은 `(날짜, 0, None)`으로 채워지며, stock_qty=None이므로 imputation 제외
- 결과: 30일 중 20일 레코드 없는 상품의 WMA가 0.14~0.28로 과소추정
- 실제 수요(재고 있을 때 일평균): 1.0~1.33개

### 2.3 기존 stockout imputation의 한계
- `stock_qty == 0` (확인된 품절일)은 이미 imputation 대상 (비품절일 평균으로 대체)
- `stock_qty is None` (레코드 없는 날)은 imputation 대상에서 **제외** (코멘트: "수요 없음/미수집")
- 신선식품의 경우 레코드 없는 날 = 매장에 상품 자체가 없었던 날이므로 사실상 품절과 동일

### 2.4 영향 범위 (46513 기준)

| mid_cd | 카테고리 | 고유품목 | 일평균매출 | 재고보유율 |
|--------|---------|:------:|:--------:|:--------:|
| 001 | 도시락 | 20개 | 3.7개 | 27.8% |
| 002 | 주먹밥 | 40개 | 13.6개 | 31.4% |
| 003 | 김밥 | 23개 | 4.7개 | 31.9% |
| 004 | 샌드위치류 | 18개 | 4.2개 | 28.1% |
| 005 | 햄버거류 | 13개 | 1.8개 | 38.6% |

## 3. 해결 방안

### 3.1 접근: 2단계 예측 (개별 + 카테고리 총량)

```
기존: 품목별 WMA → 계수 → 발주
변경: 품목별 WMA → 계수 → [카테고리 총량 floor 보정] → 발주
```

### 3.2 카테고리 총량 예측기 (CategoryDemandForecaster)

**위치**: `src/prediction/category_demand_forecaster.py` (신규)

1. **카테고리 일별 총 매출 집계**: `daily_sales`에서 mid_cd별 SUM(sale_qty) 시계열 생성
   - 개별 품목과 달리 카테고리 총량은 매일 레코드가 존재 (누락 없음)
   - 따라서 WMA 왜곡 없이 정확한 총량 예측 가능

2. **카테고리 총량 WMA 계산**: 7일 가중이동평균 (기존 WMA 로직 재사용)
   - 요일 계수, 날씨 계수 등 기존 피처 블렌딩 적용

3. **개별 예측 합산 vs 카테고리 총량 비교**:
   - `sum(개별_예측) < 카테고리_총량 * threshold` 이면 부족분 분배
   - threshold: 0.7 (개별 합이 총량의 70% 미만이면 보정)

4. **부족분 분배 로직**:
   - 부족분 = 카테고리_총량 - sum(개별_예측)
   - 최근 판매 빈도 높은 품목 우선 분배 (sell_day_ratio 기준)
   - 최대 분배량: 품목당 +1개 (과잉발주 방지)

### 3.3 레코드 부재일 imputation 개선

**위치**: `src/prediction/improved_predictor.py` `calculate_weighted_average()`

- 현재: `stock_qty is None` → sale_qty=0 유지 (수요 없음 취급)
- 변경: 신선식품(001~005) mid_cd이고, 비품절일 평균 > 0 이면
  `stock_qty is None`도 imputation 대상에 포함 (비품절일 평균으로 대체)
- 조건: `min_available_days >= 3` (비품절 데이터 최소 3일 확보)

### 3.4 적용 위치 (파이프라인)

```
improved_predictor.py predict_single()
  ├── Phase 1: WMA + Feature블렌딩 (개별 예측) -- imputation 개선 적용
  ├── Phase 2~12: 기존 계수, 안전재고, 프로모션, ML 등
  └── Phase 13 (신규): 카테고리 총량 floor 보정
       └── CategoryDemandForecaster.adjust_orders()
```

**호출 시점**: `auto_order.py`에서 개별 예측 완료 후, 실제 발주 전
- 카테고리별로 예측 결과를 묶어 총량 비교
- 부족분을 재고 0인 품목에 우선 분배

## 4. 수정 대상 파일

| 파일 | 변경 | 설명 |
|------|------|------|
| `src/prediction/category_demand_forecaster.py` | **신규** | 카테고리 총량 예측기 |
| `src/prediction/improved_predictor.py` | 수정 | WMA imputation에 None 날 포함 (신선식품) |
| `src/prediction/prediction_config.py` | 수정 | category_floor 설정 추가 |
| `src/application/services/auto_order.py` | 수정 | 발주 전 카테고리 floor 보정 호출 |
| `src/infrastructure/database/repos/sales_repo.py` | 수정 | 카테고리별 일별 총매출 조회 메서드 |
| `tests/test_category_demand_forecaster.py` | **신규** | 테스트 |

## 5. 설정

```python
# prediction_config.py 추가
"category_floor": {
    "enabled": True,
    "target_mid_cds": ["001", "002", "003", "004", "005"],
    "threshold": 0.7,        # 개별합이 총량의 70% 미만이면 보정
    "max_add_per_item": 1,   # 품목당 최대 추가 발주
    "wma_days": 7,           # 카테고리 총량 WMA 기간
}
```

## 6. 구현 순서

1. `sales_repo.py`: 카테고리별 일별 총매출 조회 메서드 추가
2. `category_demand_forecaster.py`: 카테고리 총량 예측기 구현
3. `improved_predictor.py`: WMA imputation 신선식품 확장
4. `prediction_config.py`: 설정 추가
5. `auto_order.py`: 카테고리 floor 보정 호출
6. 테스트 작성 및 실행

## 7. 검증 기준

- [ ] 46513 주먹밥(002) 자동발주: 4개 → 10개 이상
- [ ] 46513 도시락(001) 자동발주 증가 확인
- [ ] 비식품(016 스낵 등) 발주량 변화 없음 (대상 아님)
- [ ] 기존 2216개 테스트 전부 통과
- [ ] 신규 테스트 15개 이상

## 8. 리스크

- **과잉발주 위험**: threshold를 보수적으로 설정(0.7), max_add_per_item=1로 상한
- **비판매 상품에 발주**: 최근 판매 빈도(sell_day_ratio) 기준 분배로 방지
- **기존 imputation과 충돌**: 신선식품만 대상, None imputation은 별도 조건 분기
