# Design: 행사 종료 임박 상품 발주 감량 자동화 (promo-end-reduction)

> 작성일: 2026-04-05
> Plan 참조: `docs/01-plan/features/promo-end-reduction.plan.md`

---

## 1. 구현 개요

행사 종료 감지 범위를 D-3 → **D-5**로 확장. 변경 최소화(상수+조건 2곳).

### 코드 읽기 후 Plan 수정 사항

Plan에서 Stage 7(PROMO FLOOR)이 감량을 덮어쓴다고 추정했으나, **실제 코드 확인 결과 Stage 7은 현재 사실상 미동작**(qty > 0이면서 qty < 1은 정수에서 불가).

실제 문제는:
1. `PromotionAdjuster` 케이스 1 조건이 `days_until_end <= 3` 하드코딩
2. `END_ADJUSTMENT`에 D-4, D-5 키 미정의
3. `get_adjustment_summary()`도 `<= 3` 하드코딩

→ **C2(PROMO FLOOR 수정)는 불필요**. C1과 C3만 필요.

---

## 2. 변경 파일

```
변경 파일 (3개):
  src/settings/constants.py                        # PROMO_END_REDUCTION_DAYS 상수
  src/prediction/promotion/promotion_adjuster.py   # END_ADJUSTMENT 확장 + 조건 변경
  tests/ (신규 또는 기존)                           # 테스트
```

---

## 3. 상세 설계

### 3.1 constants.py — 상수 추가

```python
PROMO_END_REDUCTION_DAYS = 5  # 행사 종료 N일 전부터 감량 시작
```

### 3.2 promotion_adjuster.py — 3곳 변경

#### 변경 A: END_ADJUSTMENT 딕셔너리 확장

```python
# 현재 (라인 45-50)
END_ADJUSTMENT = {
    3: 0.50,    # D-3: 50%로 감소
    2: 0.30,    # D-2: 30%로 감소
    1: 0.10,    # D-1: 10%로 감소
    0: 0.00,    # D-day: 발주 중단
}

# 변경
END_ADJUSTMENT = {
    5: 0.85,    # D-5: 85%로 감소 (15% 감량)
    4: 0.70,    # D-4: 70%로 감소 (30% 감량)
    3: 0.50,    # D-3: 50%로 감소 (기존 유지)
    2: 0.30,    # D-2: 30%로 감소 (기존 유지)
    1: 0.10,    # D-1: 10%로 감소 (기존 유지)
    0: 0.00,    # D-day: 발주 중단 (기존 유지)
}
```

#### 변경 B: 케이스 1 조건 확장 (라인 103)

```python
# 현재
if (status.current_promo
        and status.days_until_end is not None
        and status.days_until_end <= 3):

# 변경 — 상수 참조
from src.settings.constants import PROMO_END_REDUCTION_DAYS

if (status.current_promo
        and status.days_until_end is not None
        and status.days_until_end <= PROMO_END_REDUCTION_DAYS):
```

#### 변경 C: get_adjustment_summary() 조건 확장 (라인 294)

```python
# 현재
if status.days_until_end <= 3 and not status.next_promo:

# 변경
if status.days_until_end <= PROMO_END_REDUCTION_DAYS and not status.next_promo:
```

### 3.3 ctx["_promo_days_until_end"] 전달 (improved_predictor.py)

Stage 3에서 `days_until_end`를 ctx에 저장하면 향후 활용 가능. **현재 Stage 7이 미동작이므로 필수는 아니지만**, 디버깅과 향후 확장을 위해 추가.

```python
# _apply_promo_adjustment 내부, ctx["_promo_current"] = _promo_current 근처
ctx["_promo_days_until_end"] = (
    promo_status.days_until_end if promo_status else None
)
```

위치: improved_predictor.py 라인 ~2610 근처

---

## 4. 구현 순서

| 순서 | 작업 | 파일 | 줄 |
|------|------|------|---|
| 1 | `PROMO_END_REDUCTION_DAYS = 5` 추가 | constants.py | 1줄 |
| 2 | END_ADJUSTMENT에 D-4, D-5 추가 | promotion_adjuster.py | 2줄 |
| 3 | 케이스 1 조건 `<= 3` → `<= PROMO_END_REDUCTION_DAYS` | promotion_adjuster.py | 2줄 (import + 조건) |
| 4 | get_adjustment_summary 조건 동일 변경 | promotion_adjuster.py | 1줄 |
| 5 | ctx에 `_promo_days_until_end` 추가 | improved_predictor.py | 3줄 |
| 6 | 테스트 추가 | tests/ | ~50줄 |

**총 변경량**: ~60줄 (기존 수정 ~10줄 + 테스트 ~50줄)

---

## 5. 테스트 설계

```python
# D-5 감량 적용 확인
def test_end_adjustment_d5():
    # days_until_end=5, no next_promo → factor 0.85

# D-4 감량 적용 확인
def test_end_adjustment_d4():
    # days_until_end=4, no next_promo → factor 0.70

# D-6은 감량 미적용 (범위 밖)
def test_no_adjustment_d6():
    # days_until_end=6 → 케이스 3(행사 중) 적용

# 행사 연장 시 D-5여도 감량 스킵
def test_extended_promo_skips_reduction():
    # _is_promo_extended=True → factor 1.0

# 다음 행사 있으면 D-5여도 감량 약화
def test_next_promo_same_type():
    # next_promo == current_promo → "동일 행사 연속"

# get_adjustment_summary D-5 포함
def test_summary_includes_d5():
    # ending_soon에 D-5 상품 포함
```

---

## 6. 하위호환

| 변경 | 기존 동작 영향 |
|------|---------------|
| END_ADJUSTMENT 확장 | D-3 이하 값 변경 없음 → 기존 동작 100% 유지 |
| 케이스 1 조건 확장 | D-4~D-5가 새로 케이스 1에 진입 (기존: 케이스 3 행사 배율 적용) |
| PROMO_END_REDUCTION_DAYS | 새 상수 추가, 기존 상수 변경 없음 |

**유일한 동작 변화**: D-4~D-5 상품이 행사 배율 → 감량 비율로 전환. 이것이 이 기능의 목적.

---

## 7. 영향받는 파이프라인 단계

```
Stage 3 (PROMO) ← 여기서 감량 적용
  ↓
Stage 4 (ML) — 영향 없음 (base_qty 기반이 아닌 후처리)
Stage 5 (신상품) — 영향 없음
Stage 6 (DiffFeedback) — 감량된 qty에 추가 피드백 적용 가능
Stage 7 (PROMO FLOOR) — 현재 사실상 미동작 (qty > 0 and qty < 1 = 불가)
Stage 8 (CategoryFloor) — mid_cd 최소값이 감량값보다 클 경우 덮어쓸 수 있음 → 모니터링
Stage 9 (CAP) — 상한만 적용, 감량에 영향 없음
```

Stage 8 충돌 가능성은 구현 후 모니터링으로 확인.
