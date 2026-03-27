# Design: food-stockout-misclassify

> 푸드류 유통기한 만료를 품절로 오판하여 발생하는 삼중 악순환 해결

## 1. 현재 코드 분석

### 1.1 악순환 경로 (코드 레벨)

```
eval_calibrator.py:259
  next_day_stock <= 0 → was_stockout = True  ← 폐기 소멸인지 품절인지 구분 안 함
                                                (disuse_qty는 L260에서 읽지만 was_stockout 판정에 미사용)
  ↓
eval_calibrator.py:451 (_judge_normal_order)
  was_stockout == True → return "UNDER_ORDER"  ← 잘못된 판정
  ↓
improved_predictor.py:1340
  stockout_freq = 1.0 - sell_day_ratio  ← daily_avg=0.1이면 sell_day_ratio≈0.14
  stockout_freq = 0.86                     (7일 중 1일만 판매)
  ↓
improved_predictor.py:1343
  stockout_freq(0.86) > 0.50 → effective_waste_coef = 1.0  ← 폐기계수 완전 면제
  ↓
food.py:1260-1264
  stockout_freq(0.86) >= 0.70 → stockout_boost = 1.30  ← 30% 부스트
  ↓
adjusted_prediction = base * 1.0 (면제) * 1.30 (부스트)  ← 감량 없이 오히려 증가
```

### 1.2 핵심 구조적 문제

| 코드 | 현재 동작 | 문제 |
|------|----------|------|
| `eval_calibrator.py:259` | `was_stockout = next_day_stock <= 0` | 폐기 소멸도 품절로 판정 |
| `eval_calibrator.py:260` | `disuse_qty` 읽지만 미활용 | was_stockout 판정에 사용 안 함 |
| `eval_calibrator.py:451` | `was_stockout → UNDER_ORDER` | 폐기 소멸에도 UNDER_ORDER |
| `improved_predictor.py:1343` | `stockout_freq > 0.50 → 면제` | 폐기율 무관하게 면제 |
| `food.py:1237-1241` | `stockout_freq >= 0.70 → 1.30x` | 폐기 오판 상품에 부스트 |

## 2. 수정 설계

### Fix A: was_stockout에 폐기 구분 추가 [CRITICAL]

**파일**: `src/prediction/eval_calibrator.py`

#### A-1: was_stockout 판정 수정 (L259)

```python
# 현재 (L259-260)
was_stockout = next_day_stock <= 0
disuse_qty = yesterday_sales.get(item_cd, {}).get("disuse_qty", 0)

# 변경
was_stockout = next_day_stock <= 0 and disuse_qty == 0
was_waste_expiry = next_day_stock <= 0 and disuse_qty > 0
```

- `next_day_stock <= 0` 이면서 `disuse_qty == 0`일 때만 진짜 품절
- `next_day_stock <= 0` 이면서 `disuse_qty > 0`이면 폐기 소멸 (품절 아님)
- **주의**: disuse_qty 조회가 L260에 이미 있으므로 순서만 조정

#### A-2: _judge_normal_order 수정 (L448-453)

```python
# 현재 (L448-453)
if mid_cd in FOOD_CATEGORIES:
    if actual_sold > 0:
        return "CORRECT"
    if was_stockout:
        return "UNDER_ORDER"
    return "OVER_ORDER"

# 변경
if mid_cd in FOOD_CATEGORIES:
    if actual_sold > 0:
        return "CORRECT"
    if was_stockout and not was_waste_expiry:
        return "UNDER_ORDER"
    if was_waste_expiry:
        return "OVER_ORDER"  # 폐기 소멸 = 과잉발주
    return "OVER_ORDER"
```

- `was_waste_expiry`를 `record`에 추가하여 전달
- 폐기 소멸 시 UNDER_ORDER 대신 OVER_ORDER 반환

#### A-3: _judge_outcome에 was_waste_expiry 전달

`record` dict에 `was_waste_expiry` 필드 추가:
- L271: `was_waste_expiry=was_waste_expiry,` 추가
- L362: 동일하게 추가

#### A-4: eval_outcomes DB에 was_waste_expiry 저장

`eval_outcomes` 테이블에 이미 `disuse_qty` 컬럼 있음 (L275).
was_waste_expiry는 `disuse_qty > 0`으로 복원 가능하므로 **DB 스키마 변경 불필요**.

### Fix B: 품절 면제에 폐기율 교차 검증 [HIGH]

**파일**: `src/prediction/improved_predictor.py`

#### B-1: 폐기계수 면제 조건 강화 (L1343-1362)

```python
# 현재 (L1343-1349)
if stockout_freq > 0.50:
    effective_waste_coef = 1.0  # 완전 면제

# 변경
# 폐기율이 목표+10%p 초과하면 품절 면제 해제
WASTE_EXEMPT_OVERRIDE_THRESHOLD = 0.25  # 상수로 추출
mid_waste_rate = self._get_mid_cd_waste_rate(mid_cd)  # mid_cd 평균 폐기율

if stockout_freq > 0.50 and mid_waste_rate < WASTE_EXEMPT_OVERRIDE_THRESHOLD:
    effective_waste_coef = 1.0  # 폐기율 낮으면 기존대로 면제
elif stockout_freq > 0.50 and mid_waste_rate >= WASTE_EXEMPT_OVERRIDE_THRESHOLD:
    # 폐기율 높으면 면제 해제 → 부분 적용 (최소 0.80)
    effective_waste_coef = max(unified_waste_coef, 0.80)
    logger.info(
        f"[폐기면제해제] {product['item_nm']}: "
        f"stockout={stockout_freq:.0%} but waste_rate={mid_waste_rate:.0%} >= {WASTE_EXEMPT_OVERRIDE_THRESHOLD:.0%} "
        f"→ waste_coef={effective_waste_coef:.2f} (부분적용)"
    )
elif stockout_freq > 0.30:
    effective_waste_coef = max(unified_waste_coef, 0.90)
else:
    effective_waste_coef = unified_waste_coef
```

#### B-2: stockout_boost에 동일 교차 검증 (food.py 호출부)

**파일**: `src/prediction/improved_predictor.py` (L1389)

```python
# 현재 (L1389)
stockout_boost = get_stockout_boost_coefficient(stockout_freq)

# 변경
if mid_waste_rate >= WASTE_EXEMPT_OVERRIDE_THRESHOLD:
    stockout_boost = 1.0  # 폐기율 높으면 부스트 비활성
    logger.info(
        f"[품절부스트해제] {product['item_nm']}: "
        f"waste_rate={mid_waste_rate:.0%} >= {WASTE_EXEMPT_OVERRIDE_THRESHOLD:.0%}"
    )
else:
    stockout_boost = get_stockout_boost_coefficient(stockout_freq)
```

#### B-3: _get_mid_cd_waste_rate() 메서드 추가

```python
def _get_mid_cd_waste_rate(self, mid_cd: str) -> float:
    """최근 14일 mid_cd 평균 폐기율 조회"""
    try:
        conn = DBRouter.get_connection(store_id=self.store_id, table="daily_sales")
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                COALESCE(SUM(disuse_count), 0) as total_disuse,
                COALESCE(SUM(order_qty), 0) as total_order
            FROM daily_sales ds
            JOIN common.products p ON ds.item_cd = p.item_cd
            WHERE p.mid_cd = ?
            AND ds.sale_date >= date('now', '-14 days')
        """, (mid_cd,))
        row = cursor.fetchone()
        if row and row[1] > 0:
            return row[0] / row[1]
        return 0.0
    except Exception as e:
        logger.debug(f"mid_cd waste rate 조회 실패: {e}")
        return 0.0
    finally:
        conn.close()
```

### Fix C: EXEMPT 내 초저회전 예외 (선택, 별도 PDCA)

Plan에서 언급하였으나 영향 범위가 넓어 **별도 PDCA로 분리**. 이번에는 Fix A + Fix B만 구현.

## 3. 수정 파일 목록

| # | 파일 | 수정 내용 | 라인 |
|---|------|----------|------|
| 1 | `src/prediction/eval_calibrator.py` | was_stockout 폐기 구분 | L259, 271, 349-356, 361-363 |
| 2 | `src/prediction/eval_calibrator.py` | _judge_normal_order 폐기 소멸 처리 | L448-453 |
| 3 | `src/prediction/improved_predictor.py` | 폐기 면제 교차 검증 | L1343-1362 |
| 4 | `src/prediction/improved_predictor.py` | stockout_boost 교차 검증 | L1389 |
| 5 | `src/prediction/improved_predictor.py` | _get_mid_cd_waste_rate() 추가 | 신규 메서드 |
| 6 | `src/prediction/prediction_config.py` | WASTE_EXEMPT_OVERRIDE_THRESHOLD 상수 | 신규 상수 |

## 4. 테스트 설계

### 단위 테스트

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | `test_was_stockout_with_disuse` | stock=0 + disuse>0 → was_stockout=False, was_waste_expiry=True |
| 2 | `test_was_stockout_without_disuse` | stock=0 + disuse=0 → was_stockout=True, was_waste_expiry=False |
| 3 | `test_judge_normal_food_waste_expiry` | 푸드 + was_waste_expiry → OVER_ORDER |
| 4 | `test_judge_normal_food_real_stockout` | 푸드 + was_stockout(진짜) → UNDER_ORDER |
| 5 | `test_waste_exempt_override_high_waste` | stockout>50% + waste_rate>25% → 면제 해제 |
| 6 | `test_waste_exempt_normal_low_waste` | stockout>50% + waste_rate<25% → 기존 면제 유지 |
| 7 | `test_stockout_boost_disabled_high_waste` | waste_rate>25% → boost=1.0 |
| 8 | `test_stockout_boost_enabled_low_waste` | waste_rate<25% → boost=기존값 |
| 9 | `test_get_mid_cd_waste_rate` | 14일 폐기율 계산 정확성 |
| 10 | `test_get_mid_cd_waste_rate_no_data` | 데이터 없으면 0.0 반환 |

### 통합 테스트

| # | 테스트 | 검증 |
|---|--------|------|
| 11 | `test_low_avg_food_no_infinite_order` | daily_avg=0.1 상품: UNDER_ORDER 연속 해소 |
| 12 | `test_high_waste_rate_prediction_reduced` | 폐기율 32%: 예측값 감소 확인 |
| 13 | `test_real_stockout_still_boosted` | 진짜 품절(disuse=0): 부스트 유지 확인 |

### 회귀 테스트

| # | 테스트 | 검증 |
|---|--------|------|
| 14 | 기존 3700+ 테스트 전체 | 회귀 없음 |
| 15 | eval_outcomes 기존 테스트 | was_stockout 변경으로 인한 기존 판정 호환 |

## 5. 영향 분석

### 후행 덮어쓰기 체크

| 이 수정 이후 실행되는 단계 | 덮어쓰기 위험 | 대응 |
|--------------------------|:------------:|------|
| DiffFeedback (Step 8) | ⚠️ | DiffFeedback은 eval_outcomes의 outcome을 참조 → OVER_ORDER가 늘면 penalty 증가 → **의도한 효과** |
| FoodWasteRateCalibrator (Phase 1.56) | ✅ | 폐기율 감소하면 캘리브레이터도 안정 → 양성 피드백 |
| CategoryDemandForecaster (Step 11) | ✅ | 개별 예측 감소 → Floor 트리거 가능성 → Cap이 최종 차단 |

### 기존 정상 상품 영향

- daily_avg >= 0.5 상품: sell_day_ratio >= 0.50 → stockout_freq <= 0.50 → 면제 조건 미해당 → **영향 없음**
- daily_avg 0.2~0.5 + 폐기율 < 25%: 기존대로 면제 유지 → **영향 없음**
- daily_avg < 0.2 + 폐기율 >= 25%: 면제 해제 + 부스트 비활성 → **의도한 감량**

## 6. 상수 설계

```python
# prediction_config.py 추가
WASTE_EXEMPT_OVERRIDE_THRESHOLD = 0.25   # 폐기율 25% 초과 시 품절 면제 해제
WASTE_EXEMPT_PARTIAL_FLOOR = 0.80        # 면제 해제 시 최소 폐기계수
WASTE_RATE_LOOKBACK_DAYS = 14            # 폐기율 조회 기간
```

## 7. 구현 순서

1. `prediction_config.py` — 상수 3개 추가
2. `improved_predictor.py` — `_get_mid_cd_waste_rate()` 메서드 추가
3. `improved_predictor.py` — L1343-1362 폐기 면제 교차 검증
4. `improved_predictor.py` — L1389 stockout_boost 교차 검증
5. `eval_calibrator.py` — L259 was_stockout 폐기 구분
6. `eval_calibrator.py` — L448-453 _judge_normal_order 수정
7. 테스트 15개 작성
8. 기존 테스트 전체 실행
