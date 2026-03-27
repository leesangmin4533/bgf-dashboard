# Plan: cut-replacement-fix

## 1. 개요

### 문제 정의
CUT(발주중지) 상품의 수요를 동일 mid_cd 대체 상품으로 보충하는 `CutReplacementService`가
구현되어 있고 `enabled=True`이나, **실제로 한 번도 실행된 적이 없다.**

로그 분석 결과 "CUT보충" 메시지 출현 0회, `substitution_events` 레코드 0건.

### 근본 원인
**2중 경로 CUT 필터링의 구조적 결함:**

1. `pre_order_evaluator.py:1163-1176` — CUT 상품을 `EvalDecision.SKIP` 처리하고
   `item_codes`에서 제거. **수요 데이터(daily_avg, mid_cd)가 이 시점에 폐기됨.**
2. `auto_order.py:218-243` `_refilter_cut_items()` — `order_list`에서 CUT 찾기.
   하지만 ①에서 이미 제거된 상품은 예측조차 되지 않아 order_list에 없음.
3. `auto_order.py:957` — `self._cut_lost_items`가 빈 리스트 → `CutReplacementService` 미호출.

### 영향 범위
- 46513 점포 001(도시락): 일평균 4.9개 중 CUT 3개 상품 수요 ≈ 2개 손실
- 전체 푸드 카테고리(001~005, 012)에서 CUT 상품이 있는 모든 mid_cd 동일 영향

## 2. 수정 방안

### 핵심 전략
`pre_order_evaluator`에서 CUT SKIP 처리 시 **수요 데이터를 보존**하여
`auto_order`의 `_cut_lost_items`에 전달한다.

### 수정 대상 파일 (2개)

#### Fix A: `src/prediction/pre_order_evaluator.py`
- **위치**: `evaluate_all()` 내 CUT SKIP 처리 블록 (lines 1163-1176)
- **변경**: CUT SKIP 시 `PreOrderEvalResult`에 `daily_avg`와 `mid_cd`를 실제 값으로 채움
  - 현재: `mid_cd=""`, `daily_avg=0` (모든 정보 폐기)
  - 수정: `all_daily_avgs`에서 조회한 실제 `daily_avg`, `products_map`에서 조회한 실제 `mid_cd` 사용
- **조건**: `all_daily_avgs`와 `products_map`은 CUT SKIP 블록 이후에 로딩됨 → **로딩 순서를 조정**하거나, CUT 블록에서 별도 배치 조회 수행

#### Fix B: `src/order/auto_order.py`
- **위치**: CUT 보충 호출 직전 (line 957 부근)
- **변경**: `eval_results`에서 CUT SKIP 항목을 추출하여 `_cut_lost_items`에 추가
  ```python
  # eval_results에서 CUT SKIP 수요 데이터 복원
  if eval_results:
      for cd, r in eval_results.items():
          if r.decision == EvalDecision.SKIP and "CUT" in r.reason:
              if r.mid_cd in FOOD_CATEGORIES and r.daily_avg > 0:
                  self._cut_lost_items.append({
                      "item_cd": cd,
                      "mid_cd": r.mid_cd,
                      "predicted_sales": r.daily_avg,
                      "item_nm": r.item_nm,
                  })
  ```

### 수정하지 않는 파일
- `cut_replacement.py` — 보충 로직 자체는 정상. 입력만 채워주면 됨.
- `_refilter_cut_items()` — prefetch 실시간 감지용으로 유지 (기존 역할 그대로).

## 3. 데이터 흐름 (수정 후)

```
[pre_order_evaluator]
  CUT 7개 → SKIP 처리 + daily_avg/mid_cd 보존
  → eval_results에 수요 데이터 포함

[auto_order]
  _cut_lost_items = []
  + _refilter_cut_items() → prefetch 실시간 CUT 포착 (기존)
  + eval_results CUT SKIP → _cut_lost_items에 추가 (★신규)
  → CutReplacementService(cut_lost_items=[3건])
  → 동일 mid_cd 대체 상품에 손실수요 80% 보충
```

## 4. 테스트 계획

| # | 테스트 | 검증 내용 |
|---|--------|-----------|
| 1 | CUT SKIP에 daily_avg/mid_cd 보존 | PreOrderEvalResult 필드 값 확인 |
| 2 | eval_results → _cut_lost_items 변환 | CUT SKIP 항목만 추출, 비CUT SKIP 제외 |
| 3 | FOOD_CATEGORIES 필터 | 비푸드 CUT은 _cut_lost_items에 미포함 |
| 4 | daily_avg=0인 CUT 제외 | 수요 없는 CUT은 보충 대상 아님 |
| 5 | CutReplacementService 정상 호출 | cut_lost_items 비어있지 않을 때 호출 확인 |
| 6 | 보충 수량 검증 | 손실수요 × 0.8 = 보충 목표, max_add_per_item 준수 |
| 7 | 기존 _refilter_cut_items 동작 유지 | prefetch 감지 CUT도 여전히 포착 |
| 8 | 중복 방지 | 동일 item_cd가 refilter + eval 양쪽에서 중복 안 됨 |

## 5. 리스크

- **로딩 순서**: `all_daily_avgs`가 CUT 블록 이후에 로딩되므로 순서 조정 필요
- **성능**: CUT 상품 수가 적으므로 (보통 10개 미만) 추가 조회 부담 미미
- **기존 테스트**: CutReplacementService 14개 + pre_order_evaluator 기존 테스트에 영향 없어야 함
