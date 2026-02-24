# Plan: receiving-pattern (ML 입고 패턴 피처 추가)

## 1. 개요

### 1.1 문제 정의
현재 ML 모델의 31개 피처 중 입고 관련 피처는 `pending_qty`(현재 미입고 수량) 단 1개뿐이다.
`pending_qty`는 **시점 스냅샷**에 불과하여 다음 패턴을 전혀 반영하지 못한다:

- **리드타임 패턴**: 발주~입고까지 평균 며칠 걸리는지 (배송차수별 상이)
- **입고 안정성**: 리드타임의 변동성 (일정한지 vs 불규칙한지)
- **숏배송율**: 발주 대비 실제 입고가 부족한 비율
- **입고 요일 편중**: 특정 요일에 입고가 집중되는지

이 정보가 없으면 ML 모델이 "내일 실제로 물건이 들어올지"를 판단하지 못해 재고 예측 정확도가 떨어진다.

### 1.2 목표
`receiving_history` + `order_tracking` 테이블의 기존 이력 데이터를 활용하여
**입고 패턴 피처 5개**를 ML 모델에 추가한다.

### 1.3 기대 효과
- ML 모델이 입고 리드타임/안정성을 학습하여 need 계산 정확도 향상
- 숏배송이 잦은 상품의 안전재고를 자동으로 높이는 효과
- 배송차수(1차/2차)별 특성 반영으로 식품/비식품 예측 차별화

---

## 2. 현재 상태 (As-Is)

### 2.1 기존 입고 관련 데이터

| 테이블 | 핵심 컬럼 | 현재 용도 |
|--------|-----------|-----------|
| `receiving_history` | receiving_date, receiving_time, order_date, order_qty, receiving_qty, delivery_type | 입고 기록 저장만 (ML 미사용) |
| `order_tracking` | order_date, arrival_time, actual_receiving_qty, actual_arrival_time, status | 상태 추적만 (ML 미사용) |
| `realtime_inventory` | pending_qty | ML 피처 1개 (스냅샷) |

### 2.2 기존 ML 피처 (31개)

```
[기본통계 5] daily_avg_7/14/31, stock_qty, pending_qty
[트렌드 2] trend_score, cv
[시간 6] weekday_sin/cos, month_sin/cos, is_weekend, is_holiday
[행사 1] promo_active
[카테고리 5] is_food/alcohol/tobacco/perishable/general_group
[비즈니스 3] expiration_days, disuse_rate, margin_rate
[외부 2] temperature, temperature_delta
[래그 3] lag_7, lag_28, week_over_week
[연관 1] association_score
[휴일컨텍스트 3] holiday_period_days, is_pre_holiday, is_post_holiday
```

---

## 3. 제안 피처 (To-Be)

### 3.1 신규 피처 5개

| # | 피처명 | 타입 | 계산 방법 | 정규화 |
|---|--------|------|-----------|--------|
| 32 | `lead_time_avg` | float | 최근 30일 `receiving_date - order_date` 평균 (일) | / 3.0 (0~1 범위) |
| 33 | `lead_time_cv` | float | 리드타임 변동계수 (std/mean) | clamp(0, 2.0) / 2.0 |
| 34 | `short_delivery_rate` | float | 최근 30일 `(order_qty - receiving_qty) / order_qty` 비율 | 이미 0~1 |
| 35 | `delivery_frequency` | float | 최근 14일 입고 횟수 | / 14.0 (최대 1일 1회 가정) |
| 36 | `pending_age_days` | float | 현재 미입고분의 경과일수 `today - order_date` | / 5.0 (clamp) |

### 3.2 피처별 상세 설계

#### (32) lead_time_avg — 평균 리드타임
```sql
SELECT AVG(julianday(receiving_date) - julianday(order_date))
FROM receiving_history
WHERE item_cd = ? AND store_id = ?
  AND receiving_date >= date('now', '-30 days')
```
- **의미**: 이 상품이 발주 후 평균 며칠 만에 입고되는지
- **폴백**: 데이터 없으면 delivery_type별 기본값 (cold_1=1.0, cold_2=1.0, ambient=1.5)
- **정규화**: / 3.0 → 보통 0~1 범위, 3일 초과는 1.0 cap

#### (33) lead_time_cv — 리드타임 안정성
```sql
-- 평균과 표준편차 동시 계산
SELECT
  AVG(lead_time) as avg_lt,
  CASE WHEN AVG(lead_time) > 0
    THEN (SQRT(SUM((lead_time - avg_lt)^2) / COUNT(*))) / AVG(lead_time)
    ELSE 0
  END as cv
FROM (
  SELECT julianday(receiving_date) - julianday(order_date) as lead_time
  FROM receiving_history
  WHERE item_cd = ? AND store_id = ?
    AND receiving_date >= date('now', '-30 days')
)
```
- **의미**: CV 낮음(0.1) = 매번 같은 날 입고, CV 높음(1.5+) = 불규칙 입고
- **폴백**: 데이터 부족(< 3건)이면 0.5 (중간값)
- **정규화**: min(cv, 2.0) / 2.0

#### (34) short_delivery_rate — 숏배송율
```sql
SELECT
  SUM(CASE WHEN receiving_qty < order_qty THEN 1 ELSE 0 END) * 1.0 / COUNT(*)
FROM receiving_history
WHERE item_cd = ? AND store_id = ?
  AND receiving_date >= date('now', '-30 days')
  AND order_qty > 0
```
- **의미**: 발주했는데 덜 들어온 비율 (0.3 = 30% 숏배송)
- **폴백**: 데이터 없으면 0.0 (숏배송 없음 가정)
- **이미 0~1**: 정규화 불필요

#### (35) delivery_frequency — 입고 빈도
```sql
SELECT COUNT(DISTINCT receiving_date)
FROM receiving_history
WHERE item_cd = ? AND store_id = ?
  AND receiving_date >= date('now', '-14 days')
```
- **의미**: 최근 2주간 입고된 횟수 (자주 들어오는 상품 vs 간헐 입고)
- **폴백**: 데이터 없으면 0.0
- **정규화**: / 14.0 (2주간 최대 14회)

#### (36) pending_age_days — 미입고 경과일
```python
# order_tracking에서 현재 pending 상태인 가장 오래된 발주의 경과일
pending_orders = order_tracking.get_pending_orders(item_cd, store_id)
if pending_orders:
    oldest_order_date = min(o.order_date for o in pending_orders)
    pending_age = (today - oldest_order_date).days
else:
    pending_age = 0
```
- **의미**: 미입고가 오래 걸리고 있으면 지연 가능성 높음
- **폴백**: pending 없으면 0.0
- **정규화**: min(days, 5) / 5.0

---

## 4. 수정 파일 목록

| # | 파일 | 변경 내용 |
|---|------|-----------|
| 1 | `src/prediction/ml/feature_builder.py` | FEATURE_NAMES에 5개 추가 (31→36), `build_features()` 파라미터 추가, 피처 계산 로직 |
| 2 | `src/infrastructure/database/repos/receiving_repo.py` | `get_receiving_pattern_stats(item_cd, store_id, days=30)` 메서드 추가 |
| 3 | `src/infrastructure/database/repos/order_tracking_repo.py` | `get_pending_age_days(item_cd, store_id)` 메서드 추가 |
| 4 | `src/prediction/improved_predictor.py` | ML 피처 빌드 시 receiving_stats 조회 & 전달 |
| 5 | `tests/test_receiving_pattern_features.py` | 신규 테스트 파일 |

---

## 5. 구현 순서

### Phase 1: DB 쿼리 레이어 (repos)
1. `receiving_repo.py`에 `get_receiving_pattern_stats()` 추가
   - 1회 쿼리로 lead_time_avg, lead_time_std, short_delivery_count, total_count, delivery_frequency 반환
2. `order_tracking_repo.py`에 `get_pending_age_days()` 추가

### Phase 2: 피처 빌더 확장
3. `feature_builder.py`의 FEATURE_NAMES 확장 (31→36)
4. `build_features()` 시그니처에 `receiving_stats: dict = None` 파라미터 추가
5. 피처 계산 + 정규화 로직 구현
6. 기존 호출부 호환성 유지 (receiving_stats=None이면 기본값 0.0)

### Phase 3: 예측기 연동
7. `improved_predictor.py`의 ML 피처 빌드 호출부에서 receiving_stats 조회 & 전달
8. 성능 고려: 상품별 개별 쿼리 대신 배치 쿼리 검토

### Phase 4: 테스트
9. 단위 테스트: 피처 계산 정확성 (정규화, 폴백, 엣지케이스)
10. 통합 테스트: DB 쿼리 + 피처 빌드 end-to-end
11. 기존 테스트 회귀 확인 (FEATURE_NAMES 변경 → model hash 변경)

---

## 6. 위험 요소 및 대응

### 6.1 기존 모델 무효화
- **문제**: FEATURE_NAMES가 31→36으로 변경되면 기존 학습 모델의 feature hash 불일치 → 모델 재학습 필요
- **대응**: feature_builder의 MD5 hash가 바뀌므로 자동 재학습 트리거됨 (기존 설계)
- **추가 안전장치**: 모델 로드 시 feature_count 불일치면 rule-only 모드 자동 전환 (기존 로직)

### 6.2 receiving_history 데이터 부족
- **문제**: 신규 상품이나 최근 입고가 없는 상품은 receiving_history가 비어있음
- **대응**: 모든 피처에 폴백 기본값 설정 (lead_time_avg=0.33, cv=0.5, short_rate=0.0 등)

### 6.3 성능 (쿼리 오버헤드)
- **문제**: 상품 300개 × 개별 쿼리 = 300회 DB 호출
- **대응 A**: 배치 쿼리로 store 전체 한 번에 조회 후 dict로 매핑
- **대응 B**: 최소한 receiving_repo.get_receiving_pattern_stats_batch(store_id, days=30) 제공

### 6.4 FEATURE_NAMES 순서 의존성
- **문제**: 기존 코드에서 인덱스로 접근하는 곳이 있을 수 있음
- **대응**: 기존 31개 피처 순서 유지, 새 5개는 끝에 append

---

## 7. 성공 기준

| 항목 | 기준 |
|------|------|
| 피처 추가 | FEATURE_NAMES 31→36개, build_features() 정상 동작 |
| 폴백 | receiving_history 없는 상품도 에러 없이 기본값 사용 |
| 역호환 | 기존 31-feature 모델 로드 시 자동 rule-only 전환 |
| 성능 | 배치 쿼리로 전체 상품 1회 조회, 추가 지연 < 5초 |
| 테스트 | 신규 15+개 테스트, 기존 1633개 회귀 없음 |
| 재학습 | feature hash 변경 감지 → 자동 재학습 성공 |

---

## 8. 비고

- 이 기능은 ML 모델에만 영향을 주며, 규칙 기반 예측 파이프라인(24단계)은 변경하지 않음
- ML 앙상블 가중치(30%/50%)는 기존 유지 — 피처 추가 후 효과 관찰 후 조정 검토
- 기존 `delivery_gap_consumption` (배송 중 소비 보정)은 규칙 기반으로 독립 유지
