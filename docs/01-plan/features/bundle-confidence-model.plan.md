# Plan: bundle-confidence-model

> 작성: 2026-04-09 (자동 예약 태스크)
> 기여 KPI: K3 (발주 실패율)
> 선행 작업: bundle-suspect-dynamic-master (PAUSED → SUPERSEDED)
> Issue-Chain: order-execution#bundle-confidence-model

---

## 1. 문제 정의

### 카테고리 마스터 접근의 한계

bundle-suspect-dynamic-master (04-08) 는 `product_details` 통계를 mid_cd 단위로 집계해
카테고리 전체를 STRONG/WEAK/UNKNOWN/NORMAL 로 분류하는 방향이었다.

Step 1~3 실측 분석에서 결정적 한계가 드러남:

| 문제 | 실측 수치 |
|------|-----------|
| mid=023 bundle_pct = 5.5% | unit1=94.5%, BGF API 빈값(NULL) 다수 |
| bundle_pct 단독 기준 | 진짜 위험 상품(unit>1)을 카테고리 평균으로 희석 |
| 카테고리 set 자체가 노이즈 신호 | NULL 30% 초과 시 UNKNOWN 위임 → 사각지대 발생 |

즉, 카테고리 평균은 **잘못 발주될 개별 상품**을 식별하지 못한다.

### 사용자 비판 (04-08)

> "동적 마스터 재계산보다 `product_details` 직접 참조가 더 단순/정확하지 않은가?"

핵심 통찰: 카테고리 set 을 잘 만들어도 상품별 `order_unit_qty` 를 이미 갖고 있는 DB 가 있는데,
왜 카테고리 통계로 우회하는가?

### 결론

카테고리 분류 접근 **폐기**, **상품별 신뢰도 모델**로 전환한다.

---

## 2. 결정적 증거 (bundle-suspect-dynamic-master Step 1~3 발견사항)

### 023(햄/소시지) 사례 — 카테고리 접근의 실패

```
mid=023  bundle_pct = 5.5%  (unit>1 상품 6개 / 전체 109개)
unit1    = 94.5%             (103개)
NULL     = 다수              (BGF API 미반환 → DB 미기록)
```

카테고리 기준으로는 "5.5%이므로 NORMAL → 가드 미적용" 판정.
하지만 실제로는 `8801392060632 CJ아삭한비엔나70g` 의 unit>1 이 과발주를 만들었다.

→ **정답은 상품 하나의 unit_qty 신뢰도**, 카테고리 평균이 아님.

### 49965 수집 공백 문제

49965(두 상품 모두) `product_details` 에 49965 store_id 행이 없음.
다른 매장(46513) 값 unit=1 을 사용 → 묶음 감지 불가.

→ **cross-store 일치성**이 신호: 4매장 중 3매장이 unit>1 이면 나머지도 위험.

### BundleClassifier UNKNOWN 지대

NULL>30% 또는 total<5 → UNKNOWN → 정적 fallback 위임.
fallback 이 없는 신규 mid 는 완전히 사각지대.

→ **sibling 비교** (같은 mid 다른 상품) 로 보완 가능.

---

## 3. 수정 범위

### 신규 모듈

| 모듈 | 위치 | 역할 |
|------|------|------|
| `bundle_confidence_model.py` | `src/domain/order/` | 상품별 신뢰도 계산 순수 함수 |
| `bundle_confidence_service.py` | `src/application/services/` | 캐시 + DB 조회 오케스트레이션 |

### 재사용

| 파일 | 재사용 이유 |
|------|-------------|
| `src/infrastructure/database/repos/bundle_stats_repo.py` | cross-store / sibling 집계 쿼리 기반 재사용 가능 |

### 폐기 후보

| 파일 | 폐기 이유 |
|------|-----------|
| `src/domain/order/bundle_classifier.py` | 카테고리 분류 로직 전체 — 접근 방식 폐기 |
| `tests/unit/order/test_bundle_classifier.py` | 동반 폐기 |

> **폐기 확정은 Design 단계에서 신뢰도 모델 구현 확정 후**. 지금은 폐기 후보로만 표시.

---

## 4. 핵심 설계 원칙

### 원칙 1: 상품별 평가 (카테고리 set 폐기)

카테고리 mid_cd 단위 분류가 아닌, 발주 시 해당 `item_cd` 의 신뢰도를 직접 계산한다.

```python
def confidence_in_unit_qty(item_cd: str) -> float:
    """
    0.0 (신뢰 불가) ~ 1.0 (완전 신뢰) 반환.
    """
    ...
```

### 원칙 2: 다중 신호 가중 합산

단일 신호 (bundle_pct) 의 노이즈를 4개 신호 조합으로 극복한다:

```python
def confidence_in_unit_qty(item_cd: str) -> float:
    # 신호 1: cross-store 일치성 (4매장 product_details unit_qty 비교)
    # 신호 2: sibling 일치성 (같은 mid 형제 상품 median unit_qty 비교)
    # 신호 3: 가격대 (1,500원 미만 상품은 묶음 발주 가능성 낮음)
    # 신호 4: BGF API NULL 비율 (수집 결함 — unit_qty 신뢰 불가 신호)

    return weighted_avg(signals)  # 가중치는 Design 단계 확정

# 발주 시점:
if confidence < CONFIDENCE_THRESHOLD:
    BLOCK + [BLOCK/unit-qty] 알림
```

### 원칙 3: 정적 fallback 영구 유지 (이중 안전망)

신뢰도 모델이 작동 불가(DB 장애, 수집 공백)일 때도 `BUNDLE_SUSPECT_MID_CDS` 로 최소 안전 보장.

```
order_executor 가드:
  1. confidence_service 호출 → 신뢰도 < 임계값 → BLOCK
  2. (신뢰도 계산 실패 시) 정적 BUNDLE_SUSPECT_MID_CDS 확인 → BLOCK
  3. 둘 다 통과 → 발주 진행
```

### 원칙 4: 단순성 우선

복잡한 동적 마스터 재계산보다, product_details 를 직접 읽는 것이 더 단순하고 정확하다.
Design 단계에서 신호별 구현 복잡도 검토 후 단순한 것 우선 선택.

---

## 5. Design 단계 결정 항목

| # | 항목 | 후보 옵션 |
|---|------|-----------|
| 1 | 신호 가중치 | 균등 가중 vs 교차 검증 최적화 vs 사용자 지정 |
| 2 | 신뢰도 임계값 (CONFIDENCE_THRESHOLD) | 0.5 vs 0.6 vs 0.7 |
| 3 | cross-store 일치 측정 방법 | 과반수 투표 vs median 일치 vs "1개라도 unit>1이면 의심" |
| 4 | sibling 모집단 정의 | 같은 mid_cd 전체 vs 동일 mid + 가격대 유사 상품 |
| 5 | 가격대 컷오프 | 1,500원 vs 2,000원 vs 중위 가격 기준 상대값 |
| 6 | NULL/결측 처리 | null_ratio > 30% → 신뢰도 0.0(강제 BLOCK) vs 0.5(중간) vs fallback 위임 |

> 6개 결정 항목 → `/discuss bundle-confidence-model` 로 토론 후 Design 진입 권장.

---

## 6. 검증 계획 (Check 단계 사전 정의)

### 회귀 방지

- 현재 `BUNDLE_SUSPECT_MID_CDS` 로 BLOCK 중인 mid 상품들이 신뢰도 모델에서도 BLOCK 유지
- 단위 테스트: 정적 fallback 결과 = 신뢰도 모델 결과 (동일 상품 기준)

### 신규 효과

- 04-08 사고 상품 (`8801392060632`) — 신뢰도 모델에서 임계값 미달 → BLOCK 여부 확인
- 49965 product_details 미수집 상품 — cross-store 신호로 감지 여부 확인

### 운영 모니터링

- 신뢰도 0.5 미만 상품 비율 daily 리포트 (예상 규모 파악)
- false positive 확인: 정상 묶음 상품(담배, 맥주 6캔 등)이 BLOCK 되지 않는지

### Match Rate 목표

- 90% (회귀 0건 + 04-08 사고 재현 BLOCK 확인 + false positive < 5%)

---

## 7. 다른 PDCA와의 충돌 분석

| 피처 | 상태 | 충돌 여부 |
|------|------|-----------|
| food-underprediction-secondary | design | 없음 (예측 레이어 수정, 발주 가드 별개) |
| bundle-suspect-dynamic-master | PAUSED | **직접 후속** — 동일 order_executor 가드 영역, 병행 금지 |
| order-overstock-guard | 완료 | 없음 (재고 과잉 필터, 로직 분리) |

→ **bundle-suspect-dynamic-master 는 본 계획이 진행되면 SUPERSEDED 처리**.
두 계획을 동시에 구현하면 order_executor 가드 충돌 발생.

---

## 8. 재사용 가능 자산

| 자산 | 파일 | 재사용 방식 |
|------|------|-------------|
| BundleStats(데이터 클래스) | `bundle_classifier.py:33` | 도메인 값 객체 — 재사용 or 이동 |
| cross-store 집계 쿼리 | `bundle_stats_repo.py` | 신뢰도 신호 1 (cross-store 일치성) 기반 |
| mid 별 통계 쿼리 | `bundle_stats_repo.py` | 신호 4 (NULL 비율) 기반 |
| BUNDLE_SUSPECT_MID_CDS | `constants.py:262` | 정적 fallback 로 영구 유지 |
| L1/L2/L3 가드 분기 구조 | `order_executor.py` | 신뢰도 임계값 판단 위치 재사용 |

---

## 관련 이슈

- 직전 계획: order-execution#bundle-suspect-dynamic-master (SUPERSEDED)
- 원인 이슈: order-execution#product_details-order_unit_qty-불일치
- 이번 계획: order-execution#bundle-confidence-model
