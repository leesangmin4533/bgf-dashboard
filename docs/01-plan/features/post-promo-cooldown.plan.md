# Plan: Post-Promo Cooldown (행사 종료 후 냉각 기간)

## 개요

행사(1+1, 2+1 등) 종료 후 WMA가 행사 기간 매출 데이터로 오염되어 과대 예측이 지속되는 문제를 해결한다.

## 문제 정의

### 사례: 삼립)신선가득꿀호떡 (8801068406832, 012 빵)

| 기간 | 행사 | 일평균 판매 |
|------|------|-----------|
| ~2월 | 2+1 포함 | 1.45개 (normal_avg) |
| 3/1~3/31 | 1+1 | 2.7개 |
| 4/1~ | 없음 | **1개** (실제) → **3.3개** (예측) |

### 원인 분석 (Step 0 진단 결과)

1. **WMA 7일 중 6일이 행사 데이터** — `promo_weight_factor=0.25` 감쇠로도 부족
2. **Branch D(비행사 캡)는 조건 충족** — `normal_avg=1.45`, `daily_avg=3.3 > 1.45*1.3`
3. **Branch D 적용 후 `rule_order_qty=2`까지 낮아짐**
4. **하지만 최종 `order_qty=3`** — 후처리 단계에서 다시 올라감

```
pred=3.3 → Branch D 캡 → rule=2 → ??? 후처리 → final=3
```

### 핵심 질문

- **Q1**: rule=2에서 final=3으로 올린 후처리 단계는 무엇인가?
- **Q2**: Branch D 캡이 후처리에서 무효화되는 것이 정상인가?
- **Q3**: 냉각 로직을 추가해야 하는가, 아니면 후처리 우선순위 수정으로 해결 가능한가?

## 조사 범위

### Phase 1: 후처리 단계 추적 (우선)

1. `rule_order_qty=2`에서 `order_qty=3`으로 올라가는 경로 확인
   - CategoryFloor (mid 하한)
   - LargeFloor (large 하한)
   - 배수 반올림 (order_unit_qty=1이므로 해당 없음)
   - 프로모션 최소 보장 (행사 종료 후에도 적용되는지?)
   - DiffFeedback 보정
   - ML 앙상블 블렌딩

2. `stage_trace`가 비어있는 원인 확인 → 로깅 보강 필요 여부

### Phase 2: 냉각 로직 설계 (Phase 1 결과에 따라)

**Phase 1에서 후처리 수정으로 해결되면 Phase 2 불필요.**

해결 안 되면 토론 결과 기반 냉각 로직 구현:

```
행사 종료 후 D+1 ~ D+7:
  alpha = 비행사 실제데이터 일수 / 7
  예측 = normal_avg × (1 - alpha) + WMA × alpha
```

- 구현 위치: `improved_predictor.py` Branch D 앞에 Branch E 추가
- 감지: `promotions.end_date` 기준 최근 7일 이내 종료 여부
- 폴백: `normal_avg=0`이면 냉각 미적용
- 엣지케이스: 냉각 중 새 행사 시작 → 행사 모드 우선

## 전문가 토론 결과 (2026-04-02)

### 합의 사항
- 문제는 실재함 (3명 동의)
- **먼저 기존 Branch D 후처리 경로를 추적해야 함** (3명 합의)
- normal_avg 신뢰성은 확보됨 (promotion_stats에 1.45 산출)

### 리스크 (악마의 변호인)
- normal_avg의 계절성 미반영 (2월 겨울 → 4월 봄)
- 연속 행사 시 비행사 데이터 부족
- hard switch 대신 블렌딩 권장
- DiffFeedback/ML과의 상호작용 검증 필요

### 구현 방향 (실용주의)
- Branch D 강화 vs Branch E 신설 → Phase 1 결과에 따라 결정
- `prediction_config.py`에 `post_promo_cooldown` 설정 추가
- 점진적 블렌딩 (alpha 공식)

## 영향 범위

- `src/prediction/improved_predictor.py` — `_apply_promotion_adjustment()`
- `src/prediction/prediction_config.py` — 설정 추가
- `src/prediction/promotion/promotion_manager.py` — 최근 종료 행사 조회 메서드 (Phase 2)
- 테스트: `tests/test_promo_cooldown.py` 신규

## 성공 기준

- 행사 종료 후 1~7일 내에 예측값이 `normal_avg ± 30%` 이내로 수렴
- 기존 행사 진행 중 예측에 영향 없음 (회귀 없음)
- Match Rate 유지 (기존 97% 이상)
