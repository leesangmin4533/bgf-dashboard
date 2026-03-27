# Design: 과잉발주 보정 시스템 (over-order-correction)

> **Spec Panel Review**: v2 — 전문가 리뷰 반영 완료

## 1. 문제 정의

### 현황 (eval_outcomes 5일 누적 8,202건 기준)

| 지표 | 수치 | 문제점 |
|------|------|--------|
| FORCE_ORDER 적중률 | 15.8% | 84% 과잉 — 품절 상품에 강제 발주했으나 실제 판매 없음 |
| URGENT_ORDER 적중률 | ~40% | 60% 과잉 |
| 발주 전환율 | 20.8% | 발주한 5개 중 1개만 실제 판매 |
| PASS 결정 비율 | ~55% | PASS가 예측기에 위임되어 대부분 발주됨 |

### 근본 원인 분석 (우선순위 순)

| ID | 원인 | 현재값 | 기본값 | 심각도 |
|----|------|--------|--------|--------|
| P1 | **PASS 상품이 예측기 위임 후 대부분 발주됨** | 예측기가 PASS 무조건 발주 | PASS는 발주량 억제 필요 | Critical |
| P2 | **FORCE_ORDER가 판매 실적 없는 품절 상품도 강제 발주** | daily_avg 무시 | 최소 판매량 검증 필요 | Critical |
| P3 | exposure_sufficient 하향 드리프트 | 2.1일 | 3.0일 | High |
| P4 | weight_daily_avg 상향 드리프트 | 0.649 | 0.40 | High |
| P5 | stockout_freq_threshold 최대값 도달 | 0.30 | 0.15 | Medium |
| P6 | 보정 루프 edge 고착 | 상/하한에 수렴 | 중앙으로 복원 | Low |

### P1 설계 의도 확인

`get_filtered_items()` line 1032에 주석:
```python
# PASS는 기존 플로우에 위임 (order_codes에도 skip_codes에도 포함 안 함)
```
PASS를 exclude하지 않은 것은 **의도된 설계**. PASS = "사전 평가에서 판단 불가 → 기존 예측기에 위임"이라는 의미. 따라서 PASS 일괄 제외가 아닌 **PASS 상품의 발주량 하향 조정**이 올바른 접근.

---

## 2. 설계 원칙

1. **최소 침습**: 기존 결정 매트릭스(FORCE/URGENT/NORMAL/PASS/SKIP) 구조 유지
2. **책임 응집**: 결정 로직은 evaluator에만, auto_order는 결정을 소비만
3. **단계적 적용**: Phase 간 canary period (3-5일)으로 효과 분리 측정
4. **검증 가능**: dry_run 사전/사후 비교 + feature flag로 즉시 rollback 가능
5. **설정 가능**: 매직 넘버 없이 모든 조정값을 constants.py 또는 eval_config.py에 정의

---

## 3. Phase 1: 즉시 수정 (PASS 억제 + FORCE 검증)

### 3-1. PASS 상품 발주량 하향 (P1)

PASS를 일괄 exclude하면 "노출 2.1~3.0일" 범위의 상품이 전부 차단되어 품절 위험. 대신 **PASS 상품은 발주 대상에 남기되 발주량을 줄인다**.

**파일**: `src/order/auto_order.py`
**위치**: line 391-396 (get_order_candidates 호출부)

```python
# 현재:
candidates = self.improved_predictor.get_order_candidates(
    target_date=target_date,
    min_order_qty=min_order_qty,
    exclude_items=skip_codes if skip_codes else None
)

# 변경:
# PASS 상품코드 수집
pass_codes = set()
if eval_results:
    pass_codes = {
        cd for cd, r in eval_results.items()
        if r.decision == EvalDecision.PASS
    }

candidates = self.improved_predictor.get_order_candidates(
    target_date=target_date,
    min_order_qty=min_order_qty,
    exclude_items=skip_codes if skip_codes else None
)

# PASS 상품 발주량 하향 조정 (최소 발주 단위로 제한)
if pass_codes:
    pass_adjusted = 0
    for r in candidates:
        if r.item_cd in pass_codes:
            original = r.order_qty
            r.order_qty = min(r.order_qty, PASS_MAX_ORDER_QTY)  # 상수: 1
            if r.order_qty != original:
                pass_adjusted += 1
    if pass_adjusted:
        logger.info(f"PASS 발주량 억제: {pass_adjusted}개 상품")
```

**상수**: `PASS_MAX_ORDER_QTY = 1` → `src/config/constants.py`
**Feature flag**: `ENABLE_PASS_SUPPRESSION = True` → `src/config/constants.py`

### 3-2. FORCE_ORDER 최소 판매량 검증 (P2)

**파일**: `src/prediction/pre_order_evaluator.py` — `_make_decision()` 내부
**위치**: line 780-782 (품절 분기)

결정 로직은 evaluator에 응집시킨다 (auto_order.py에서 decision을 변경하지 않는다).

```python
# 현재:
if current_stock <= 0:
    return EvalDecision.FORCE_ORDER, "현재 품절"

# 변경:
if current_stock <= 0:
    if daily_avg < FORCE_MIN_DAILY_AVG:
        # 판매 실적 거의 없는 품절 상품 → NORMAL로 다운그레이드
        return EvalDecision.NORMAL_ORDER, f"품절+일평균{daily_avg:.2f}<{FORCE_MIN_DAILY_AVG}"
    return EvalDecision.FORCE_ORDER, "현재 품절"
```

**주의**: `_make_decision()`에 `daily_avg` 파라미터 추가 필요. 현재 시그니처:
```python
def _make_decision(self, current_stock, exposure_days, popularity_level, stockout_frequency, item_cd)
```
변경:
```python
def _make_decision(self, current_stock, exposure_days, popularity_level, stockout_frequency, item_cd, daily_avg=0.0)
```

**상수**: `FORCE_MIN_DAILY_AVG = 0.1` → `src/config/constants.py`
**Feature flag**: `ENABLE_FORCE_DOWNGRADE = True` → `src/config/constants.py`

### 3-3. Rollback 전략

- Feature flag `ENABLE_PASS_SUPPRESSION`, `ENABLE_FORCE_DOWNGRADE`를 `False`로 변경하면 즉시 롤백
- 각 flag는 `constants.py`에서 관리, 재시작 필요 없음 (다음 발주 사이클에 반영)

---

## 4. Phase 2: 파라미터 드리프트 보정

> **Canary**: Phase 1 배포 후 3-5일 모니터링 완료 후 진행

### 4-1. 파라미터 리셋 전 백업

**파일**: `src/prediction/eval_config.py` — `save()` 메서드

```python
def save(self, path=None):
    filepath = path or EVAL_CONFIG_PATH
    # 변경 전 백업 자동 생성
    if filepath.exists():
        backup = filepath.with_suffix('.json.bak')
        shutil.copy2(filepath, backup)
    # 기존 저장 로직
    ...
```

### 4-2. 기본값 복원력 (Mean Reversion)

**파일**: `src/prediction/eval_calibrator.py` — `update_param()` 호출부

현재 문제: 보정이 한 방향으로 반복 누적 → 파라미터 극단 수렴.

```python
# eval_config.py에 ParamSpec으로 정의 (매직 넘버 제거)
calibration_decay: ParamSpec = field(default_factory=lambda: ParamSpec(
    value=0.7, default=0.7, min_val=0.3, max_val=1.0, max_delta=0.1,
    description="보정 강도 감쇄율 (1.0=감쇄 없음)"
))
calibration_reversion_rate: ParamSpec = field(default_factory=lambda: ParamSpec(
    value=0.1, default=0.1, min_val=0.0, max_val=0.3, max_delta=0.05,
    description="기본값 복원 속도 (0=복원 없음)"
))
```

**적용 위치**: `eval_calibrator.py` — 각 `_calibrate_*` 메서드에서 `config.update_param()` 호출 전

```python
# 보정 공식:
# effective_delta = raw_delta * decay + (default - current) * reversion_rate
decay = self.config.calibration_decay.value
reversion_rate = self.config.calibration_reversion_rate.value
reversion = (spec.default - spec.value) * reversion_rate
effective_delta = raw_delta * decay + reversion
new_val = self.config.update_param(param_name, spec.value + effective_delta)
```

### 4-3. 파라미터 범위 조정

**파일**: `src/prediction/eval_config.py`

| 파라미터 | 현재 범위 | 변경 범위 | 이유 |
|----------|----------|----------|------|
| exposure_sufficient | 2.0~5.0 | **2.5~5.0** | 2.0은 normal(2.0)과 동일 → 판별 불가 |
| weight_daily_avg | 0.20~0.65 | 0.20~**0.55** | 0.65는 다른 지표 무의미화 |
| stockout_freq_threshold | 0.05~0.30 | 0.05~**0.25** | 0.30은 대부분 상품 업그레이드 |

### 4-4. 1회 보정 횟수 제한

**파일**: `src/prediction/eval_calibrator.py` — `calibrate()` 메서드

```python
MAX_PARAMS_PER_CALIBRATION = 3  # constants.py

changes = []
# a) 인기도 가중치
weight_changes = self._calibrate_popularity_weights(today, accuracy)
changes.extend(weight_changes)

if len(changes) < MAX_PARAMS_PER_CALIBRATION:
    # b) 노출시간 임계값
    exposure_changes = self._calibrate_exposure_thresholds(today, accuracy)
    changes.extend(exposure_changes[:MAX_PARAMS_PER_CALIBRATION - len(changes)])

if len(changes) < MAX_PARAMS_PER_CALIBRATION:
    # c) 품절빈도 임계값
    stockout_changes = self._calibrate_stockout_threshold(today, accuracy)
    changes.extend(stockout_changes[:MAX_PARAMS_PER_CALIBRATION - len(changes)])
```

### 4-5. 즉시 파라미터 리셋

**파일**: `config/eval_params.json`

| 파라미터 | 현재 | 리셋 값 | 이유 |
|----------|------|---------|------|
| weight_daily_avg | 0.649 | 0.40 | P1 상태에서의 보정 무효 |
| weight_sell_day_ratio | 0.231 | 0.35 | 동일 |
| weight_trend | 0.120 | 0.25 | 동일 |
| exposure_sufficient | 2.1 | 3.0 | 동일 |
| stockout_freq_threshold | 0.30 | 0.15 | 동일 |

### 4-6. Rollback 전략

- `eval_params.json.bak` 자동 백업에서 복원: `copy eval_params.json.bak eval_params.json`
- calibration_decay / reversion_rate를 1.0 / 0.0으로 설정하면 mean reversion 비활성화

---

## 5. Phase 3: eval_outcomes 컬럼 확장 (ML 데이터 축적)

> **Canary**: Phase 2 배포 후 3-5일 모니터링 완료 후 진행

### 5-1. 추가 컬럼

| 컬럼명 | 타입 | 설명 | 수집 시점 |
|--------|------|------|----------|
| `predicted_qty` | INTEGER | 예측기가 계산한 발주 수량 | **발주 후** |
| `actual_order_qty` | INTEGER | 실제 발주된 수량 | **발주 후** |
| `order_status` | TEXT | NOT_ORDERED / DRY_RUN / ORDERED / FAILED | **발주 후** |
| `weekday` | INTEGER | 평가일 요일 (0=월~6=일) | 평가 시 |
| `delivery_batch` | TEXT | 1차/2차 배송 구분 (NULL=해당없음) | 평가 시 |
| `sell_price` | INTEGER | 매가 (원) | 평가 시 |
| `margin_rate` | REAL | 이익율 (%) | 평가 시 |
| `disuse_qty` | INTEGER | 폐기 수량 | 사후 검증 시 |
| `promo_type` | TEXT | 행사 유형 (1+1, 2+1 등) | 평가 시 |
| `trend_score` | REAL | 트렌드 점수 (7일/30일) | 평가 시 |
| `stockout_freq` | REAL | 30일 품절 빈도 | 평가 시 |

> **변경점 (리뷰 반영)**: predicted_qty 수집 시점을 "평가 시" → "발주 후"로 변경 (순환 의존 방지). order_status 컬럼 추가 (미발주/dry_run/발주/실패 구분).

### 5-2. DB 마이그레이션

**파일**: `src/db/models.py` — `MIGRATIONS` dict에 신규 버전 추가

```sql
-- Schema v12: eval_outcomes ML 컬럼 확장
ALTER TABLE eval_outcomes ADD COLUMN predicted_qty INTEGER;
ALTER TABLE eval_outcomes ADD COLUMN actual_order_qty INTEGER;
ALTER TABLE eval_outcomes ADD COLUMN order_status TEXT;
ALTER TABLE eval_outcomes ADD COLUMN weekday INTEGER;
ALTER TABLE eval_outcomes ADD COLUMN delivery_batch TEXT;
ALTER TABLE eval_outcomes ADD COLUMN sell_price INTEGER;
ALTER TABLE eval_outcomes ADD COLUMN margin_rate REAL;
ALTER TABLE eval_outcomes ADD COLUMN disuse_qty INTEGER;
ALTER TABLE eval_outcomes ADD COLUMN promo_type TEXT;
ALTER TABLE eval_outcomes ADD COLUMN trend_score REAL;
ALTER TABLE eval_outcomes ADD COLUMN stockout_freq REAL;
```

### 5-3. 데이터 수집 연동

**평가 시 수집** (`pre_order_evaluator.py` — `evaluate_all()`):

```
추가 저장 필드:
    weekday        ← datetime.now().weekday()
    delivery_batch ← item_nm[-1] if (mid_cd in FOOD_MID_CODES and item_nm[-1] in ('1','2')) else NULL
    sell_price     ← product_details.sell_price
    margin_rate    ← product_details.margin_rate
    promo_type     ← promotions 테이블 조회
    trend_score    ← _calculate_popularity()에서 이미 계산한 값
    stockout_freq  ← _calculate_stockout_frequency()에서 이미 계산한 값
```

> **변경점 (리뷰 반영)**: delivery_batch에 `mid_cd in FOOD_MID_CODES` 조건 추가 (비푸드 상품의 숫자 접미사 오판 방지)

**발주 후 수집** (`auto_order.py` — `execute()`):

```python
# 발주 완료 후 ML 데이터 업데이트
for item in order_list:
    outcome_repo.update_order_result(
        eval_date=today,
        item_cd=item['item_cd'],
        predicted_qty=item.get('original_order_qty', item['final_order_qty']),
        actual_order_qty=item['final_order_qty'],
        order_status='ORDERED' if not dry_run else 'DRY_RUN'
    )
```

**사후 검증 시 수집** (`eval_calibrator.py` — `verify_yesterday()`):

```python
# 기존 update_outcome()에 disuse_qty 추가
disuse_qty = yesterday_sales.get(item_cd, {}).get('disuse_qty', 0)
self.outcome_repo.update_outcome(
    ...,  # 기존 파라미터
    disuse_qty=disuse_qty
)
```

### 5-4. Rollback 전략

- ALTER TABLE ADD COLUMN은 SQLite에서 비파괴적 (기존 데이터 영향 없음)
- 새 컬럼이 NULL이면 기존 로직에 영향 없음 → 코드 롤백만으로 충분

---

## 6. 수정 대상 파일 요약

| # | 파일 | Phase | 변경 내용 |
|---|------|-------|----------|
| 1 | `src/config/constants.py` | P1 | `PASS_MAX_ORDER_QTY`, `FORCE_MIN_DAILY_AVG`, feature flags |
| 2 | `src/order/auto_order.py` | P1 | PASS 발주량 억제 (feature flag 기반) |
| 3 | `src/prediction/pre_order_evaluator.py` | P1 | FORCE 다운그레이드 (daily_avg 파라미터 추가) |
| 4 | `src/prediction/eval_config.py` | P2 | 파라미터 범위 조정 + calibration_decay/reversion_rate 추가 + save() 백업 |
| 5 | `src/prediction/eval_calibrator.py` | P2 | mean reversion 보정 + 1회 변경 상한 |
| 6 | `config/eval_params.json` | P2 | 드리프트 파라미터 기본값 리셋 |
| 7 | `src/db/models.py` | P3 | Schema v12 마이그레이션 (11컬럼 추가) |
| 8 | `src/db/repository.py` | P3 | EvalOutcomeRepository 메서드 확장 |
| 9 | `src/prediction/pre_order_evaluator.py` | P3 | evaluate_all()에서 추가 필드 저장 |
| 10 | `src/prediction/eval_calibrator.py` | P3 | verify_yesterday()에서 disuse_qty 저장 |
| 11 | `src/order/auto_order.py` | P3 | 발주 후 predicted_qty/actual_order_qty/order_status 업데이트 |

---

## 7. 예상 효과

### Phase 1 (즉시)

```
현재: 1,640개/일 평가 → ~600개 발주 (전환율 20.8%)
수정후: PASS 상품 발주량 1개 제한 + FORCE 다운그레이드
→ 총 발주 수량 대폭 감소, 발주 건수는 유지 (품절 방지)
→ 목표 전환율 35%+
```

### Phase 2 (Phase 1 + 5일 후)

```
파라미터 리셋 + mean reversion 적용
→ 적중률 목표: FORCE 40%+, URGENT 60%+, 전체 50%+
→ 현재 전체 적중률 ~35% → 목표 50%+
```

### Phase 3 (Phase 2 + 5일 후)

```
eval_outcomes에 ML용 feature 11개 축적
→ 일 1,640건 × 30일 = ~49,000 레코드 (한 달)
→ 충분한 학습 데이터 확보 후 ML 모델 도입 가능
```

---

## 8. 검증 방법

### Phase 1 검증 (배포 후 3-5일)

```bash
# 1. dry_run으로 사전/사후 비교
python scripts/run_full_flow.py --no-collect --max-items 999 --dry-run

# 2. 로그 확인
#    "PASS 발주량 억제: N개 상품" 로그 확인
#    "품절+일평균X.XX<0.1" 사유로 NORMAL 결정된 상품 확인

# 3. eval_outcomes에서 효과 측정
sqlite3 data/bgf_sales.db "
    SELECT decision, COUNT(*),
        AVG(CASE WHEN outcome='CORRECT' THEN 1.0 ELSE 0.0 END) as accuracy
    FROM eval_outcomes
    WHERE eval_date >= date('now', '-3 days') AND outcome IS NOT NULL
    GROUP BY decision
"
```

### Phase 2 검증 (배포 후 3-5일)

```bash
# 1. eval_params.json 리셋 확인
python -c "import json; d=json.load(open('config/eval_params.json')); print(d['weight_daily_avg']['value'])"
# 기대: 0.4

# 2. 백업 파일 존재 확인
ls config/eval_params.json.bak

# 3. 보정 이력에서 mean reversion 효과 확인
sqlite3 data/bgf_sales.db "SELECT * FROM calibration_history ORDER BY id DESC LIMIT 10"
```

### Phase 3 검증 (배포 후 7일)

```sql
SELECT COUNT(*) as total,
    COUNT(predicted_qty) as has_pred,
    COUNT(actual_order_qty) as has_actual,
    COUNT(order_status) as has_status,
    COUNT(weekday) as has_weekday,
    COUNT(delivery_batch) as has_batch,
    COUNT(disuse_qty) as has_disuse
FROM eval_outcomes
WHERE eval_date >= date('now', '-7 days');
```

---

## 9. 위험 요소 및 대응

| 위험 | 심각도 | 대응 |
|------|--------|------|
| PASS 발주량 억제로 품절 | Medium | PASS_MAX_ORDER_QTY=1 (완전 차단 아님). feature flag로 즉시 해제 가능 |
| FORCE 다운그레이드로 필요 발주 누락 | Low | daily_avg < 0.1 = 2주간 거의 미판매. NORMAL로 유지 (완전 제외 아님) |
| 파라미터 리셋 직후 적중률 일시 하락 | Low | 기존 보정은 P1 상태에서의 결과이므로 무효. 리셋이 올바른 출발점 |
| Phase 1+2 동시 적용 시 원인 분리 불가 | Medium | **Phase 간 canary period 3-5일 적용** |
| eval_params.json 롤백 불가 | Medium | **save() 시 자동 .bak 백업** |
| delivery_batch 비푸드 오판 | Low | **mid_cd in FOOD_MID_CODES 조건 추가** |

---

## 10. 구현 순서

```
[Phase 1] PASS 억제 + FORCE 검증
  1. constants.py: PASS_MAX_ORDER_QTY, FORCE_MIN_DAILY_AVG, feature flags
  2. pre_order_evaluator.py: _make_decision()에 daily_avg 파라미터 + FORCE 다운그레이드
  3. auto_order.py: PASS 발주량 억제 로직 (feature flag 기반)
  4. dry_run 테스트
  ── canary period: 3-5일 ──

[Phase 2] 드리프트 보정
  5. eval_config.py: save() 백업 + 파라미터 범위 조정 + decay/reversion ParamSpec
  6. eval_calibrator.py: mean reversion 보정 + 1회 변경 상한
  7. eval_params.json: 기본값 리셋
  8. dry_run 테스트
  ── canary period: 3-5일 ──

[Phase 3] ML 데이터 축적
  9.  models.py: Schema v12 마이그레이션 (11컬럼)
  10. repository.py: EvalOutcomeRepository 메서드 확장
  11. pre_order_evaluator.py: evaluate_all()에서 추가 필드 저장
  12. eval_calibrator.py: verify_yesterday()에서 disuse_qty 저장
  13. auto_order.py: 발주 후 predicted_qty/actual_order_qty/order_status 업데이트
  14. 7일 후 컬럼 축적 검증
```

---

## 11. Spec Panel Review 반영 내역

| # | Issue | 심각도 | 조치 |
|---|-------|--------|------|
| 1 | P1은 버그가 아니라 의도된 설계 | Critical | PASS 일괄 제외 → **PASS 발주량 하향**으로 변경 |
| 2 | FORCE 다운그레이드 위치 모호 | Major | `_make_decision()` **한 곳에만** 적용 |
| 3 | Mean reversion 매직 넘버 | Medium | `eval_config.py`에 **ParamSpec으로 정의** |
| 4 | Phase 동시 적용 위험 | Medium | Phase 간 **canary period 3-5일** 추가 |
| 5 | Rollback 전략 부재 | Major | **feature flag** + `eval_params.json.bak` 자동 백업 |
| 6 | actual_order_qty 상태 구분 | Minor | **order_status 컬럼** 추가 (NOT_ORDERED/DRY_RUN/ORDERED/FAILED) |
| 7 | predicted_qty 순환 의존 | Major | 수집 시점을 **발주 후**로 변경 |
| 8 | delivery_batch 오판 | Minor | **mid_cd in FOOD_MID_CODES** 조건 추가 |
