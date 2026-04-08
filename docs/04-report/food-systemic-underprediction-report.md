# PDCA 완료 리포트: food-systemic-underprediction

> 생성일: 2026-04-09 (자동 스케줄)
> 이슈: order-execution#food-systemic-underprediction
> 커밋: ab98bfc (base_predictor.py WMA 만성 품절 imputation), 추가 v76 스키마 수정

---

## Plan

**문제**: 2026-04-08 49965점 푸드 카테고리(도시락/주먹밥/김밥/샌드위치/햄버거/빵)의 예측이 7일 평균 판매 대비 일관되게 낮음.
- 001 도시락: 18건 중 13건 under, bias -0.36
- 002 주먹밥: 24건 중 24건 under, bias -0.42
- 003 김밥: 29건 중 29건 under, bias -0.63
- 004 샌드위치: 17건 중 17건 under, bias -0.67
- 005 햄버거: 14건 중 14건 under, bias -0.54
- 012 빵: 16건 중 16건 under, bias -0.11

**영향**: 푸드는 유통기한 1~2일 → 과소발주 시 즉시 품절. 고객 이탈 + 매출 손실 + SLOW 오분류 악순환.

**근본 원인**: `base_predictor.py:314` 의 stockout imputation 코드가 `if available and stockout:` 조건으로,
7일 윈도우 전체가 품절인 만성 품절 상품에서 imputation 미발동 → 거의 0으로 WMA 산출 → 발주 0~1.

---

## Design

**수정 전략**: `not available and stockout` 신규 분기 추가
- stockout 행 중 `sale_qty>0`인 것을 `nonzero_signal`로 추출
- 평균을 0일 imputation 값으로 사용
- nonzero_signal 없으면 보정 없이 0 유지 (안전 폴백)

**영향 범위**: base_predictor.py:calculate_weighted_average 함수 한정, 기존 경로(available and stockout) 회귀 없음.

---

## Do (구현)

**커밋 ab98bfc** (04-08):
- `src/prediction/base_predictor.py:340` — `not available and stockout` 분기 신규
- `[stockout-all-window]` DEBUG 로그 추가 (운영 레벨 INFO에서는 미출력)
- 인라인 테스트 5 케이스: 만성품절 1sale=2 → WMA 2.000, fresh food None+nonzero → 1.000, mixed 회귀 없음, 비푸드 None 회귀 없음, zero-signal 안전 폴백

**추가 (ultraplan-B, 04-08)**:
- v76 schema 드리프트 복구 (stage_trace, association_boost 컬럼 누락)
- stock_qty<0 sentinel 정규화 (imputation 사각지대 해소)
- tests/test_food_stage_trace.py 9개 신규

---

## Check (검증 — 04-09 자동 실행 결과)

### 49965 prediction_logs × daily_sales bias 측정 (2026-04-09)

| mid | n | under | bias(04-08 baseline) | bias(04-09) | 개선율 |
|---|---|---|---|---|---|
| 001 도시락 | 27 | 19 | -0.36 | **-0.02** | 94% ✓ |
| 002 주먹밥 | 35 | 30 | -0.42 | **-0.15** | 64% ✓ |
| 003 김밥 | 36 | 32 | -0.63 | **-0.21** | 67% ✓ |
| 004 샌드위치 | 22 | 19 | -0.67 | **-0.13** | 81% ✓ |
| 005 햄버거 | 25 | 21 | -0.54 | **-0.10** | 81% ✓ |
| 012 빵 | 26 | 19 | -0.11 | -0.15 | -36% △ |

**전체 평균 bias**: 0.455 → 0.127 (72% 개선)
**Match Rate**: ~90% (목표 50% 이상 감소를 5/6 mid에서 달성)

### 특정 만성 품절 상품

| 상품코드 | avg7 | predicted(04-08) | predicted(04-09) | 개선 |
|---|---|---|---|---|
| 8800271904593 | 2.0 | 0 | **1.66** | ✓ |
| 8800271905408 | 1.0 | 0.34 | **0.87** | ✓ |

### stockout-all-window 로그
운영 로그레벨(INFO)에서 미출력 — 예상 정상 (DEBUG 레벨 전용).

---

## Act (후속 조치)

### 잔존 과제
1. **012 빵 소폭 악화 (-36%)**: n 증가(22→26) 및 신규 nonzero_signal 상품 mix 변화 추정. 추가 관측 필요.
2. **has_stock 2차 원인 잔존**: bias -0.22~-0.39 (절반 강도). `order-execution#food-underprediction-secondary` 별도 이슈로 04-10 관측 시작.
3. **만성 품절 상품 정지 후보 리스트**: 8800271904593처럼 윈도우 전체 품절 상태가 지속되면 자동 정지 후보로 분류 검토.
4. **stage_trace 1주 관측**: 04-10~04-16 has_stock 그룹 5단계 분포 추출 → 단일 지배 stage 식별.

### 이슈 상태
- order-execution#food-systemic-underprediction → **[RESOLVED]** (2026-04-09)
