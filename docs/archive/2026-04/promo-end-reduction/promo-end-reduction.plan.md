# Plan: 행사 종료 임박 상품 발주 감량 자동화 (promo-end-reduction)

> 작성일: 2026-04-05
> 상태: Plan
> 이슈체인: order-execution.md#행사-종료-임박-상품-발주-감량-자동화
> 마일스톤 기여: **K2 (폐기율)** — HIGH

---

## 1. 문제 정의

### 현상
행사(1+1, 2+1 등) 종료 후 재고가 남아 폐기로 직결.
냉장고 사진 토론(03-30) 교훈: "1+1 종료 5일 전부터 감소해야 하는데 수동 판단에 의존 중".

### 현재 구현 상태

Stage 3(`PromotionAdjuster`)에 D-3 감량 로직이 **이미 존재**:

```
D-3: 50%, D-2: 30%, D-1: 10%, D-0: 0%
```

**하지만 두 가지 문제**:

1. **감량 범위 부족**: D-3은 너무 늦음. D-5부터 감량해야 행사 재고 소진 시간 확보
2. **후행 단계가 감량을 덮어씀**: Stage 3에서 줄여도 이후 단계에서 다시 올라갈 수 있음:
   - Stage 7 (PROMO FLOOR): 행사 중이면 최소값 보장 → **종료 임박도 "행사 중"으로 판정**
   - Stage 8 (CategoryFloor): 카테고리 최소값이 감량값보다 클 수 있음
   - Stage 9 (CAP): 상한만 적용, 하한은 무시

### 근본 원인
Stage 7 PROMO FLOOR이 `days_until_end`를 보지 않고, `current_promo is not None`만 확인.
행사 종료 D-2인 상품도 "행사 중"이므로 FLOOR이 발동하여 감량을 무효화.

### 해결 방향
1. END_ADJUSTMENT를 D-3 → **D-5**로 확장
2. Stage 7 PROMO FLOOR에 **종료 임박 예외** 추가 (D-5 이내면 FLOOR 미적용)
3. 행사 종료 후 **냉각 기간** (D+1 ~ D+7) WMA 블렌딩 (선택)

---

## 2. 변경 범위

### 필수 (Phase 1 — 즉시)

| # | 파일 | 변경 내용 |
|---|------|----------|
| C1 | `src/prediction/promotion/promotion_adjuster.py` | END_ADJUSTMENT에 D-4(70%), D-5(85%) 추가 |
| C2 | `src/prediction/improved_predictor.py` | `_stage_promo_floor()`에 종료 임박 예외 추가 |
| C3 | `src/settings/constants.py` | `PROMO_END_REDUCTION_DAYS = 5` 상수 추가 |

### 선택 (Phase 2 — 효과 확인 후)

| # | 파일 | 변경 내용 |
|---|------|----------|
| C4 | `src/prediction/improved_predictor.py` | 행사 종료 후 냉각 기간 WMA 블렌딩 |
| C5 | `src/settings/constants.py` | `PROMO_COOLDOWN_DAYS = 7`, `PROMO_COOLDOWN_ALPHA_BASE` |

---

## 3. 상세 로직

### 3.1 END_ADJUSTMENT 확장 (C1)

```python
# 현재
END_ADJUSTMENT = {0: 0.0, 1: 0.10, 2: 0.30, 3: 0.50}

# 변경
END_ADJUSTMENT = {0: 0.0, 1: 0.10, 2: 0.30, 3: 0.50, 4: 0.70, 5: 0.85}
```

| D-N | 의미 | 발주 비율 |
|-----|------|----------|
| D-5 | 종료 5일 전 | 85% (15% 감량) |
| D-4 | 종료 4일 전 | 70% (30% 감량) |
| D-3 | 종료 3일 전 | 50% (기존 유지) |
| D-2 | 종료 2일 전 | 30% (기존 유지) |
| D-1 | 종료 1일 전 | 10% (기존 유지) |
| D-0 | 종료 당일 | 0% (기존 유지) |

### 3.2 PROMO FLOOR 종료 임박 예외 (C2)

```python
# 현재 (Stage 7)
if ctx["_promo_current"] is not None:
    floor_qty = max(1, int(normal_avg * 0.5))
    order_qty = max(order_qty, floor_qty)

# 변경
if ctx["_promo_current"] is not None:
    days_left = ctx.get("_promo_days_until_end")
    if days_left is not None and days_left <= PROMO_END_REDUCTION_DAYS:
        pass  # 종료 임박: FLOOR 미적용 (Stage 3 감량 유지)
    else:
        floor_qty = max(1, int(normal_avg * 0.5))
        order_qty = max(order_qty, floor_qty)
```

**필요 조치**: Stage 3에서 `ctx["_promo_days_until_end"]`를 설정해야 함.

### 3.3 상수 (C3)

```python
PROMO_END_REDUCTION_DAYS = 5  # 행사 종료 N일 전부터 감량 시작
```

---

## 4. 위험 분석

| 위험 | 영향 | 대응 |
|------|------|------|
| 행사 연장 시 과소발주 | 연장된 행사 상품 품절 | PromotionAdjuster에 이미 연장 감지 로직 있음 (케이스 1-1) |
| normal_avg 부정확 | 감량 기준이 잘못됨 | 기존 promotion_stats 산출 로직 검증 필요 |
| D-5 감량이 과도 | 행사 상품 품절 | 85%는 15% 감량뿐이라 리스크 낮음 |
| CategoryFloor가 감량 덮어씀 | 감량 무효화 | Stage 8은 mid_cd 단위 최소값이라 개별 상품 감량과 충돌 가능 → 모니터링 |

---

## 5. 검증 계획

1. **구현 직후**: 4매장에서 행사 종료 임박(D-5 이내) 상품 목록 조회, 감량 적용 확인
2. **1주 운영 후**: 행사 종료 상품의 폐기 건수 비교 (적용 전 vs 후)
3. **K2 기여 확인**: milestone_snapshots에서 food 폐기율 추이 확인

---

## 6. CLAUDE.md 체크리스트 연관

체크리스트 #13:
```
13. 행사 종료 임박 상품이 정상 발주량으로 포함되어 있지 않은가?
    → promo_end_date - today <= 5일이면 발주량 감소 또는 0
```

이 기능 완성 시 #13이 코드로 자동 보장됨.
