# Plan: food-cut-replacement

> 푸드 CUT(발주중지) 상품 발생 시 동일 카테고리 대체 보충

## 1. 문제 정의

### 현상
3/3 46513 매장 기준, 주먹밥(002) 예측 17건 중 **6건이 CUT** 판정으로 탈락.
기존 CategoryDemandForecaster/LargeCategoryForecaster가 보충하지 못하고 최종 9건만 제출.

| 카테고리 | 예측 | CUT 탈락 | 미입고 탈락 | 최종 | 손실률 |
|----------|:----:|:--------:|:----------:|:----:|:------:|
| 002 주먹밥 | 17 | 6 | 1 | 9 | 47% |

### 근본 원인
1. CUT 필터(`order_filter.py:58`)가 상품을 **단순 제거**만 함
2. CUT 재필터(`auto_order.py:1053`)도 prefetch 후 추가 제거만 수행
3. CategoryDemandForecaster(`threshold=0.7`)는 **총량 기준 70% 이상이면 미작동**
   - 예측 17개 중 9개 남음 = 53% → 작동하지만 **CUT 상품의 수요분을 명시적으로 인식하지 못함**
   - 보충 후보 선정 시 이미 order=0인 상품은 `min_candidate_sell_days=1`로 제한
4. CUT 탈락분의 **수요(adj 합계)를 다른 상품에 재배분하는 로직 없음**

### 영향
- 푸드류(001~005)는 유통기한이 1~3일 → 결품 시 당일 매출 직접 손실
- CUT 상품은 보통 소분류(small_cd) 단위로 단종 → 같은 mid_cd 내 대체 가능 상품 존재

## 2. 목표

| 항목 | 목표 |
|------|------|
| CUT 발생 시 대체 보충률 | 탈락 수요의 **80% 이상** 보충 |
| 적용 범위 | 푸드류 mid_cd 001~005 (012 빵 포함 가능) |
| 기존 플로우 영향 | 최소 변경 (기존 Floor 보충과 공존) |
| 테스트 커버리지 | Match Rate 95% 이상 |

## 3. 해결 방안

### 접근: CUT 재필터 직후에 "CUT 보충" 단계 삽입

```
기존 플로우:
  예측 → 필터링 → FORCE 보충 → 신상품 → 스마트 → cap → Floor보충 → 대분류Floor

변경 후:
  예측 → 필터링 → FORCE 보충 → 신상품 → 스마트 → cap
  → ★ CUT 대체 보충 (신규)
  → Floor보충 → 대분류Floor
```

### 왜 이 위치인가
- CUT 재필터(`auto_order.py:1053`) 이후 = CUT 상품이 **확정적으로 제거된 시점**
- Floor 보충 **이전** = Floor가 CUT 보충분까지 포함해 총량 검증 가능
- 스마트발주/cap **이후** = 최종 발주 목록이 거의 확정된 상태

### 핵심 알고리즘

```
1. CUT 탈락 목록에서 mid_cd별 손실 수요 합산
   lost_demand[mid_cd] = sum(CUT상품.adj_prediction)

2. mid_cd별 대체 후보 선정
   후보 조건:
   - 동일 mid_cd
   - CUT 아님
   - 기존 발주 목록에 없음 (이미 발주 중인 상품은 수량 증가 대신)
   - 최근 7일 판매 1일 이상
   - SKIP 평가 아님
   - 재고+미입고 < safety_stock (보충 필요한 상품 우선)

3. 후보 우선순위
   score = daily_avg * 0.5 + sell_day_ratio * 0.3 + (1 - stock_ratio) * 0.2
   - daily_avg: 일평균 판매 (높을수록 우선)
   - sell_day_ratio: 최근 7일 중 판매일 비율 (높을수록 우선)
   - stock_ratio: 현재재고/안전재고 (낮을수록 우선)

4. 보충 분배
   - 후보를 score 순 정렬
   - lost_demand를 소진할 때까지 각 후보에 1개씩 할당
   - 상품당 최대 보충량: max(1, round(해당상품 adj_prediction))
   - 총 보충량 상한: lost_demand * REPLACEMENT_RATIO (기본 0.8)

5. 발주 목록에 병합
   - 기존 상품: final_order_qty += 보충량
   - 신규 상품: order_list에 추가 (source="cut_replacement")
```

## 4. 수정 대상 파일

| 파일 | 변경 내용 |
|------|----------|
| `src/order/auto_order.py` | CUT 보충 단계 호출 (generate_order_list_new 내) |
| `src/order/order_adjuster.py` | `supplement_cut_shortage()` 메서드 추가 |
| `src/order/order_data_loader.py` | CUT 탈락 상품 정보(adj, mid_cd) 보관 헬퍼 |
| `tests/test_cut_replacement.py` | 단위 테스트 |

### 변경하지 않는 파일
- `order_filter.py` — 기존 CUT 필터 로직 유지
- `category_demand_forecaster.py` — 독립적으로 동작 유지
- `large_category_forecaster.py` — 독립적으로 동작 유지
- `improved_predictor.py` — 예측 로직 변경 없음

## 5. 설정 (토글)

```python
# config 또는 eval_params
CUT_REPLACEMENT = {
    "enabled": True,
    "target_mid_cds": ["001", "002", "003", "004", "005"],  # 012 선택적
    "replacement_ratio": 0.8,      # CUT 수요의 80%까지 보충
    "max_add_per_item": 2,         # 상품당 최대 +2개
    "max_candidates": 5,           # mid_cd당 최대 후보 5개
    "min_sell_days": 1,            # 최근 7일 중 최소 판매일
}
```

## 6. 로깅

```
[CUT보충] mid=002: CUT 6건(수요합=3.12) → 후보 8건 → 보충 3건(수요 2.50/3.12, 80%)
[CUT보충]   주)뉴치킨마요삼각2: +1 (score=0.82, daily_avg=0.66)
[CUT보충]   빅삼)참치마요삼각1: +1 (score=0.75, daily_avg=0.70)
[CUT보충]   주)3XL뉴매콤돈까스삼각1: +1 (score=0.68, daily_avg=0.53)
[CUT보충] 총 보충: 001=0, 002=3, 003=0, 004=0, 005=0
```

## 7. 테스트 시나리오

| # | 시나리오 | 기대 결과 |
|---|---------|----------|
| 1 | mid=002에서 3건 CUT, 후보 5건 존재 | 2~3건 보충 (80% 이내) |
| 2 | mid=001에서 2건 CUT, 후보 0건 (전부 CUT) | 보충 0건, 경고 로그 |
| 3 | CUT 상품 adj=0 (판매 없는 CUT) | lost_demand=0 → 보충 불필요 |
| 4 | 후보가 이미 발주 목록에 있음 | 기존 상품 수량 증가 |
| 5 | 후보 재고+미입고 충분 | 해당 후보 스킵, 다음 후보로 |
| 6 | replacement_ratio=0 (비활성) | 보충 0건 |
| 7 | CUT 0건 (정상 상황) | 보충 단계 스킵 |
| 8 | 012(빵) CUT 발생 | target_mid_cds에 포함 시 보충 |
| 9 | Floor 보충과 중복 방지 | CUT 보충분이 Floor에서 이중 추가 안 됨 |
| 10 | cap 초과 방지 | CUT 보충 후에도 food_daily_cap 미초과 |

## 8. 리스크 및 완화

| 리스크 | 완화 |
|--------|------|
| 과발주 → 폐기 증가 | replacement_ratio=0.8 (100%가 아닌 80%) + cap 유지 |
| 부적절한 대체상품 선정 | score 기반 우선순위 + 판매 이력 필수 조건 |
| Floor 보충과 이중 적용 | CUT 보충분을 order_list에 먼저 반영 → Floor가 이를 인식 |
| 설정 오류 | enabled 토글 + 보수적 기본값 |

## 9. 구현 순서

1. `order_adjuster.py`에 `supplement_cut_shortage()` 구현
2. `auto_order.py`에서 CUT 재필터 직후 호출
3. CUT 탈락 정보(item_cd, mid_cd, adj) 전달 구조 추가
4. 테스트 작성 (10개 시나리오)
5. 3/4 라이브 로그에서 보충 효과 검증
