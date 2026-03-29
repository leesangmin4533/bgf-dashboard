# Design: 디저트 2주 평가 시스템 개선

> Plan 참조: `docs/01-plan/features/dessert-2week-evaluation.plan.md`

## 1. 변경 상세 설계

### 1.1 보호기간 단축 (lifecycle.py)

**파일**: `src/prediction/categories/dessert_decision/lifecycle.py`
**위치**: Lines 22-27, `NEW_PRODUCT_WEEKS` dict

```python
# 현재
NEW_PRODUCT_WEEKS = {
    DessertCategory.A: 4,  # 냉장디저트
    DessertCategory.B: 3,  # 상온단기
    DessertCategory.C: 4,  # 상온장기
    DessertCategory.D: 4,  # 냉장젤리/냉동
}

# 변경
NEW_PRODUCT_WEEKS = {
    DessertCategory.A: 2,  # 냉장디저트 (4→2: 유통기한 2~5일, 2주=4~7사이클)
    DessertCategory.B: 2,  # 상온단기 (3→2: 유통기한 9~20일, 2주=1~2사이클)
    DessertCategory.C: 4,  # 상온장기 (유지: 데이터 축적 필요)
    DessertCategory.D: 4,  # 냉장젤리/냉동 (유지: 장기 관찰 필요)
}
```

**영향**: NEW 생애주기 → GROWTH_DECLINE 전환이 2주 앞당겨짐

### 1.2 GROWTH 판단 기준 강화 (judge.py)

**파일**: `src/prediction/categories/dessert_decision/judge.py`

#### 1.2.1 Category A (Lines 87-161)

현재 GROWTH_DECLINE 로직 (Lines 125-147):
```python
# 현재: 연속 2주 저조 후 판단
consecutive_low = metrics.consecutive_low_weeks  # sale_rate < 50%

if consecutive_low >= 2:
    # sale_rate < 30%가 2주 연속이면 STOP
    recent_rates = metrics.weekly_sale_rates[:2]
    if all(r < 0.30 for r in recent_rates):
        return DessertDecisionResult(decision=DessertDecision.STOP_RECOMMEND, ...)
    return DessertDecisionResult(decision=DessertDecision.REDUCE_ORDER, ...)
elif consecutive_low == 1:
    return DessertDecisionResult(decision=DessertDecision.WATCH, ...)
```

변경:
```python
# 변경: 1주 저조부터 즉시 반응
consecutive_low = metrics.consecutive_low_weeks  # sale_rate < 50%

# sale_rate < 30% 연속 판단
recent_rates = metrics.weekly_sale_rates[:2] if len(metrics.weekly_sale_rates) >= 2 else []
consecutive_very_low = sum(1 for r in recent_rates if r < 0.30)

if consecutive_very_low >= 2:
    return DessertDecisionResult(decision=DessertDecision.STOP_RECOMMEND, ...)
elif consecutive_very_low == 1:
    return DessertDecisionResult(decision=DessertDecision.WATCH,
        decision_reason="판매율 30% 미만 1주 감지 — 추이 관찰", ...)
elif consecutive_low >= 2:
    return DessertDecisionResult(decision=DessertDecision.REDUCE_ORDER, ...)
elif consecutive_low == 1:
    return DessertDecisionResult(decision=DessertDecision.WATCH,
        decision_reason="판매율 50% 미만 1주 감지 — 추이 관찰", ...)
```

#### 1.2.2 Category B (Lines 164-206)

현재 (Lines 189-200):
```python
# 현재: 연속 3주 sale_rate < 40% → STOP
if consecutive_low >= 3:
    return ... STOP_RECOMMEND
elif consecutive_low >= 2:
    return ... WATCH
```

변경:
```python
# 변경: 2주로 단축 (보호기간 2주 단축에 맞춤)
if consecutive_low >= 2:
    return ... STOP_RECOMMEND
elif consecutive_low == 1:
    return ... WATCH
```

### 1.3 자동 확정 기준 강화 (constants.py + service.py)

#### 1.3.1 카테고리별 자동확정 일수 상수 추가

**파일**: `src/settings/constants.py`
**위치**: Line 372-373 부근에 추가

```python
# 현재
DESSERT_CONFIRMED_STOP_WASTE_WEEKS = 2
DESSERT_CONFIRMED_STOP_WASTE_DAYS = 14

# 추가
DESSERT_AUTO_CONFIRM_ZERO_DAYS = {
    "A": 14,   # 냉장디저트: 14일 무판매 → 자동확정 (현재 30일)
    "B": 14,   # 상온단기: 14일 무판매 → 자동확정 (현재 30일)
    "C": 30,   # 상온장기: 30일 유지
    "D": 60,   # 냉장젤리/냉동: 60일 (유통기한이 길어 관찰 필요)
}
```

#### 1.3.2 `_auto_confirm_zero_sales()` 카테고리별 분기

**파일**: `src/application/services/dessert_decision_service.py`
**위치**: Lines 190-241

현재 시그니처:
```python
def _auto_confirm_zero_sales(self, reference_date: str, days: int = 30):
```

변경:
```python
def _auto_confirm_zero_sales(self, reference_date: str):
    """카테고리별 자동확정 일수 적용"""
    from src.settings.constants import DESSERT_AUTO_CONFIRM_ZERO_DAYS

    # STOP_RECOMMEND 상태인 상품 조회
    stop_items = self.repo.get_stop_recommended_items(store_id=self.store_id)

    confirmed_cds = []
    for item in stop_items:
        category = item.get("dessert_category", "A")
        days = DESSERT_AUTO_CONFIRM_ZERO_DAYS.get(category, 30)

        # 해당 일수 동안 판매 0인지 확인
        ref = datetime.strptime(reference_date, "%Y-%m-%d")
        check_start = (ref - timedelta(days=days)).strftime("%Y-%m-%d")

        has_sales = self._check_has_sales(
            item["item_cd"], check_start, reference_date
        )
        if not has_sales:
            confirmed_cds.append(item["item_cd"])

    if confirmed_cds:
        self.repo.batch_update_operator_action(
            item_cds=confirmed_cds,
            action="CONFIRMED_STOP",
            note=f"자동확정: 카테고리별 무판매 기간 초과",
            store_id=self.store_id,
        )
    return len(confirmed_cds)
```

### 1.4 폐기율 기반 즉시 자동확정 (service.py)

**파일**: `src/application/services/dessert_decision_service.py`
**위치**: `run()` 메서드 내 auto-confirm 호출 부분 (Line 168-171)

추가할 메서드:
```python
def _auto_confirm_high_waste_immediate(self, reference_date: str) -> int:
    """보호기간 종료 + waste_rate >= 100% → 즉시 자동확정

    기존 _auto_confirm_high_waste()는 150% + 연속 2주 조건이지만,
    이 메서드는 보호기간 종료 즉시 100% 이상이면 바로 확정.
    """
    stop_items = self.repo.get_stop_recommended_items(store_id=self.store_id)

    confirmed_cds = []
    for item in stop_items:
        lifecycle = item.get("lifecycle_phase")
        if lifecycle == "NEW":
            continue  # 보호기간 중은 스킵

        waste_rate = item.get("waste_rate", 0)
        if waste_rate and waste_rate >= 1.0:  # 100%
            confirmed_cds.append(item["item_cd"])

    if confirmed_cds:
        self.repo.batch_update_operator_action(
            item_cds=confirmed_cds,
            action="CONFIRMED_STOP",
            note=f"자동확정: 보호기간 종료 + 폐기율 100%+",
            store_id=self.store_id,
        )
    return len(confirmed_cds)
```

`run()` 메서드에서 호출 순서:
```python
# Line 168-176 영역
auto_zero = self._auto_confirm_zero_sales(ref_date)      # 변경 1.3
auto_waste = self._auto_confirm_high_waste(ref_date)      # 기존 유지
auto_imm = self._auto_confirm_high_waste_immediate(ref_date)  # 추가 1.4
```

## 2. 전문가 토론 결과 (2026-03-29)

> 악마의 변호인 / SRE 운영 전문가 / 실용주의 개발자 3명 병렬 검토

### 합의사항

1. **변경 4 (waste_rate 100% 즉시확정) 삭제**: MOQ 문제를 상품 탓으로 오인 위험. 기존 150%/2주 로직으로 충분.
2. **변경 3 (카테고리별 자동확정 분기) → 2차 릴리스로**: 효과는 있지만 Step 2,3만으로 1차 목표 달성.
3. **Feature Flag 필수**: `DESSERT_2WEEK_EVALUATION_ENABLED` 상수 추가. False→즉시 기존 동작 복구.
4. **롤백 SQL 사전 준비**: `operator_note`에 배포 식별자 포함 (`"auto[v2w]: ..."`)
5. **프로모션 보호 추가**: promotions 테이블에 활성 행사 있으면 보호기간 리셋

### 주요 리스크 및 완화

| 리스크 | 등급 | 완화 |
|--------|------|------|
| 계절/이벤트 상품 조기 중단 | 🔴 | 프로모션 보호 + 향후 seasonal_flag |
| STOP 후 재입점 경로 없음 | 🔴 | 대시보드에서 수동 OVERRIDE_KEEP 가능 (기존 기능) |
| Cat B 2주 데이터 부족 | 🟡 | 결품 기간 제외 보정 (2차 릴리스) |
| 기존 테스트 깨짐 | 🟡 | 상수값 단언 3~4개 수정 필요 |

## 3. 구현 순서 (토론 반영, 1차 릴리스)

```
Step 1: constants.py — Feature Flag + 프로모션 보호 상수 추가
Step 2: lifecycle.py — NEW_PRODUCT_WEEKS A:2, B:2 변경 (Flag 분기)
Step 3: judge.py — Cat B 연속 주 3→2 단축 (Flag 분기)
Step 4: dessert_decision_service.py — 프로모션 보호 로직 추가
Step 5: 테스트 수정 및 검증
Step 6: rollback SQL 스크립트 작성
```

### 2차 릴리스 (1차 안정화 후)
- `DESSERT_AUTO_CONFIRM_ZERO_DAYS` 카테고리별 분기
- 결품 기간 제외 보정
- 모니터링 대시보드 쿼리 추가

## 4. 수정 파일 요약

| Step | 파일 | 변경 위치 | 변경 유형 |
|------|------|----------|----------|
| 1 | `src/settings/constants.py` | L372 부근 | Flag + 상수 추가 |
| 2 | `src/prediction/categories/dessert_decision/lifecycle.py` | L22-27 | 값 변경 (4→2, 3→2) + Flag 분기 |
| 3 | `src/prediction/categories/dessert_decision/judge.py` | L189-200 | Cat B 3→2 + Flag 분기 |
| 4 | `src/application/services/dessert_decision_service.py` | run() 내 | 프로모션 보호 로직 추가 |
| 5 | `tests/test_dessert_decision.py` | 상수 단언 | 경계값 업데이트 |
| 6 | `scripts/rollback_dessert_2week.sql` | 신규 | 롤백 SQL |

## 5. 데이터 흐름

```
[스케줄러: Mon 22:00 Cat A / Mon 22:15 Cat B / 22:30 Cat C,D]
  ↓
DessertDecisionService.run(target_categories=["A"])
  ↓
0. Feature Flag 확인 → DESSERT_2WEEK_EVALUATION_ENABLED?
  ↓ (True)
1. 상품 로드 (mid_cd=014, Cat A 필터)
  ↓
2. 프로모션 보호 체크 → promotions 활성 상품은 보호기간 리셋 (★추가)
  ↓
3. 생애주기 판단 → NEW_PRODUCT_WEEKS["A"] = 2 (★변경)
   - 2주 미만: NEW → KEEP/WATCH만 가능
   - 2~8주: GROWTH_DECLINE → 판단 적용
   - 8주+: ESTABLISHED
  ↓
4. 메트릭 수집 (7일 윈도우)
  ↓
5. 판정: judge_category_b() (★변경: 3주→2주 연속 저조)
  ↓
6. 자동확정:
   a) _auto_confirm_zero_sales() → 30일 (기존 유지, 2차에서 카테고리별 분기)
   b) _auto_confirm_high_waste() → 150% + 2주 연속 (기존 유지)
  ↓
7. CONFIRMED_STOP → OrderFilter에서 발주 차단
```

## 6. 영향 범위

### 영향 있는 컴포넌트
- `judge_category_b()` — Cat B 판단 로직 변경 (3→2주)
- `determine_lifecycle()` — Cat A/B 보호기간 파라미터 변경
- `DessertDecisionService.run()` — 프로모션 보호 추가
- `OrderFilter` — 변경 없음 (기존 confirmed_stop_items() 그대로 사용)

### 영향 없는 컴포넌트
- Cat C/D 판단 로직, BeverageDecision, NewProductSettlement, 3일 발주, AutoOrderSystem

## 7. 검증 계획

### 단위 검증
1. `lifecycle.py`: Cat A/B에서 2주 경과 시 GROWTH_DECLINE 진입 확인
2. `judge.py`: Cat B 2주 연속 저조 시 STOP_RECOMMEND 반환 확인
3. 프로모션 보호: 활성 프로모션 상품이 NEW 생애주기 유지 확인

### 통합 검증
4. Feature Flag False → 기존 동작 (4주 보호) 확인
5. Feature Flag True → 2주 보호 후 판단 진입 확인
6. 기존 CONFIRMED_STOP 상품이 OrderFilter에서 정상 차단 확인

### 회귀 검증
7. pytest 전체 실행 — 기존 테스트 통과 확인 (상수 단언 3~4개 수정 후)
8. Cat C/D 판단 결과 변경 없음 확인
