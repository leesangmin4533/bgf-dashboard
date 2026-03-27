# 멀티 점포 ML 모델 아키텍처 설계

**작성일**: 2026-02-06
**현재 상황**: 단일 점포(46513) → 멀티 점포(46513, 46704)
**핵심 질문**: ML 모델을 점포별로 분리해야 하는가?

---

## 1. 현재 시스템 분석

### 1.1 현재 구조 (단일 점포 가정)

```python
class ImprovedPredictor:
    def __init__(self, db_path=None, use_db_inventory=True):
        # store_id 파라미터 없음

    def get_sales_history(self, item_cd, days=7):
        cursor.execute("""
            SELECT sales_date, sale_qty, stock_qty
            FROM daily_sales
            WHERE item_cd = ?       -- store_id 필터 없음!
            ORDER BY sales_date DESC
            LIMIT ?
        """, (item_cd, days))
```

**문제점:**
- 호반점(46513)과 동양점(46704) 데이터가 섞여서 조회됨
- 점포별 특성(규모, 위치, 고객층)이 반영되지 않음
- 한 점포의 이상치가 다른 점포 예측에 영향

---

## 2. 멀티 점포 ML 모델 설계 전략

### 전략 1: **점포별 독립 모델** (Store-Specific Models)

```
호반점 모델                동양점 모델
┌──────────────┐          ┌──────────────┐
│ Predictor    │          │ Predictor    │
│ store=46513  │          │ store=46704  │
│              │          │              │
│ 데이터:      │          │ 데이터:      │
│ - 호반점만   │          │ - 동양점만   │
│ - 6개월      │          │ - 6개월      │
└──────────────┘          └──────────────┘
```

**장점:**
- ✅ 점포별 특성 정확히 반영
- ✅ 독립적인 파라미터 튜닝 가능
- ✅ 한 점포 데이터 오류가 다른 점포에 영향 없음
- ✅ 점포별 성과 측정 명확

**단점:**
- ❌ 데이터가 적은 신규 점포는 예측 정확도 낮음
- ❌ 점포 추가 시마다 새 모델 필요
- ❌ 공통 패턴 학습 불가
- ❌ 유지보수 비용 증가

**적합한 경우:**
- 점포 간 특성 차이가 큼 (대도시 vs 지방, 역세권 vs 주택가)
- 각 점포의 데이터가 충분함 (6개월 이상)
- 점포 수가 적음 (2~10개)

---

### 전략 2: **통합 모델 + 점포 특성** (Unified Model with Store Features)

```
통합 예측 모델
┌──────────────────────────────────┐
│ Predictor                        │
│                                  │
│ 입력:                            │
│ - item_cd                        │
│ - store_id (특성으로)            │
│ - store_size (점포 규모)         │
│ - store_location (위치)          │
│                                  │
│ 데이터: 모든 점포 통합           │
└──────────────────────────────────┘
```

**장점:**
- ✅ 모든 점포 데이터로 공통 패턴 학습
- ✅ 신규 점포도 기존 학습 활용 (Transfer Learning)
- ✅ 하나의 모델로 모든 점포 처리
- ✅ 데이터 부족 문제 완화

**단점:**
- ❌ 점포별 세밀한 조정 어려움
- ❌ 점포 특성 피처 설계 필요
- ❌ 한 점포 이상치가 전체에 영향 가능
- ❌ 복잡한 모델 구조

**적합한 경우:**
- 점포 간 특성이 유사함 (동일 브랜드, 유사 상권)
- 점포 수가 많음 (10개 이상)
- 신규 점포 추가가 빈번함

---

### 전략 3: **하이브리드 모델** (Hybrid: Base + Store-Specific)

```
기본 모델 (모든 점포 학습)
        ↓
┌──────────────────────────┐
│ 점포별 파인튜닝          │
├──────────────────────────┤
│ 호반점 조정 레이어       │
│ 동양점 조정 레이어       │
└──────────────────────────┘
```

**장점:**
- ✅ 공통 패턴 + 점포별 특성 모두 반영
- ✅ 신규 점포는 기본 모델 사용 → 데이터 쌓이면 튜닝
- ✅ 최고의 정확도
- ✅ 확장성 우수

**단점:**
- ❌ 가장 복잡한 구조
- ❌ 개발/유지보수 비용 높음
- ❌ 두 단계 학습 필요

**적합한 경우:**
- 장기 운영 계획 (1년 이상)
- 점포 수가 중간 (5~20개)
- 높은 예측 정확도 요구

---

## 3. BGF 프로젝트 권장 전략

### 현재 상황 분석

| 항목 | 현황 |
|------|------|
| 점포 수 | 2개 (호반, 동양) |
| 데이터 기간 | 6개월 |
| 점포 특성 | 유사 (같은 이천시, 동일 브랜드) |
| 확장 계획 | 추가 점포 가능성 있음 |
| 개발 리소스 | 제한적 |

### 권장: **전략 1 (점포별 독립 모델)** → 단기

**이유:**
1. 점포 수가 적음 (2개)
2. 각 점포 데이터 충분 (6개월)
3. 구현이 가장 단순
4. 점포별 성과 측정 명확
5. 기존 코드 최소 수정

**구현 방법:**
```python
class ImprovedPredictor:
    def __init__(self, store_id: str, db_path=None):
        self.store_id = store_id  # 추가

    def get_sales_history(self, item_cd, days=7):
        cursor.execute("""
            SELECT sales_date, sale_qty, stock_qty
            FROM daily_sales
            WHERE item_cd = ? AND store_id = ?  -- 필터 추가
            ORDER BY sales_date DESC
            LIMIT ?
        """, (item_cd, self.store_id, days))
```

---

## 4. 구현 로드맵

### Phase 1: 점포별 모델 분리 (1~2주)

**작업 내역:**
1. ImprovedPredictor에 `store_id` 파라미터 추가
2. 모든 SQL 쿼리에 `store_id` 필터 추가
3. AutoOrderSystem에 `store_id` 전달
4. 점포별 eval_params.json 생성
   - `config/stores/46513_eval_params.json`
   - `config/stores/46704_eval_params.json`

**영향 범위:**
- `src/prediction/improved_predictor.py` (30개 메서드)
- `src/order/auto_order.py`
- `src/scheduler/daily_job.py`
- 모든 Repository 클래스 (이미 store_id 지원)

---

### Phase 2: 점포별 성과 분석 (1주)

**작업 내역:**
1. 점포별 예측 정확도 추적
2. 점포별 발주 통계 리포트
3. 점포 비교 대시보드
4. Kaggle-style 성과 지표
   - RMSE (Root Mean Squared Error)
   - MAE (Mean Absolute Error)
   - 품절률, 폐기율

---

### Phase 3: 통합 모델 실험 (선택, 2~3주)

점포가 5개 이상 증가하면:
1. 전략 2 (통합 모델) 구현
2. A/B 테스트 (독립 vs 통합)
3. 성과 비교 후 전환 결정

---

## 5. 필수 수정 파일 목록

### 5.1 핵심 파일

| 파일 | 수정 내용 | 우선순위 |
|------|----------|----------|
| `improved_predictor.py` | `__init__`에 store_id 추가 | 🔴 High |
| | 모든 SQL에 `WHERE store_id = ?` 추가 | 🔴 High |
| `auto_order.py` | Predictor 초기화 시 store_id 전달 | 🔴 High |
| `daily_job.py` | 점포별 스케줄 작업 분리 | 🔴 High |
| `eval_config.py` | 점포별 파라미터 파일 로드 | 🟡 Medium |
| `pre_order_evaluator.py` | store_id 필터 추가 | 🟡 Medium |

### 5.2 Repository (이미 store_id 지원)

✅ 이미 구현됨:
- `SalesRepository.get_sales_by_date(store_id)`
- `OrderRepository.save_order_result(store_id)`
- `PredictionRepository.save_prediction(store_id)`

---

## 6. 점포별 파라미터 예시

### 호반점 (46513) - 대형점

```json
{
  "store_id": "46513",
  "store_name": "이천호반베르디움점",
  "store_size": "large",
  "daily_order_limit": 50,
  "popularity": {
    "stockout_rate_threshold": 0.15,
    "min_daily_avg": 0.5
  },
  "category_weights": {
    "001": 1.2,  // 도시락 - 수요 많음
    "049": 1.1   // 맥주
  }
}
```

### 동양점 (46704) - 중형점

```json
{
  "store_id": "46704",
  "store_name": "이천동양점",
  "store_size": "medium",
  "daily_order_limit": 40,
  "popularity": {
    "stockout_rate_threshold": 0.20,
    "min_daily_avg": 0.3
  },
  "category_weights": {
    "072": 1.3,  // 담배 - 수요 많음
    "050": 1.1   // 소주
  }
}
```

---

## 7. 테스트 전략

### 7.1 단위 테스트

```python
def test_store_specific_prediction():
    """점포별 예측 분리 테스트"""

    # 호반점 예측기
    predictor_hoban = ImprovedPredictor(store_id="46513")
    result_hoban = predictor_hoban.predict("8801234567890")

    # 동양점 예측기
    predictor_dongyang = ImprovedPredictor(store_id="46704")
    result_dongyang = predictor_dongyang.predict("8801234567890")

    # 동일 상품이라도 점포별로 다른 예측값
    assert result_hoban.order_qty != result_dongyang.order_qty
```

### 7.2 통합 테스트

```python
def test_multi_store_daily_job():
    """멀티 점포 일일 작업 테스트"""

    from src.scheduler.daily_job import run_daily_job

    # 호반점 작업
    result_hoban = run_daily_job(store_id="46513")
    assert result_hoban['store_id'] == "46513"

    # 동양점 작업
    result_dongyang = run_daily_job(store_id="46704")
    assert result_dongyang['store_id'] == "46704"
```

---

## 8. 마이그레이션 체크리스트

### 기존 코드 → 멀티 점포

- [ ] ImprovedPredictor.\_\_init\_\_ store_id 추가
- [ ] 30개 SQL 쿼리에 store_id 필터 추가
- [ ] AutoOrderSystem store_id 전달
- [ ] 스케줄러 점포별 실행 지원
- [ ] 점포별 eval_params.json 생성
- [ ] 단위 테스트 작성
- [ ] 통합 테스트 실행
- [ ] 프로덕션 배포 전 2주 테스트
- [ ] 롤백 플랜 준비
- [ ] 모니터링 대시보드 점포 필터 추가

---

## 9. 위험 요소 및 대응

| 위험 | 영향 | 확률 | 대응 방안 |
|------|------|------|----------|
| 기존 호반점 데이터 오염 | 🔴 High | Low | DB 백업 후 작업, store_id NULL은 46513으로 간주 |
| 동양점 데이터 부족 | 🟡 Medium | Medium | 3개월 이상 수집 후 자동발주 시작 |
| 성능 저하 (쿼리 속도) | 🟢 Low | Low | store_id 인덱스 추가 |
| 점포 간 데이터 혼동 | 🔴 High | Low | 엄격한 테스트 + 코드 리뷰 |

---

## 10. 결론 및 권장사항

### 즉시 실행 (1~2주 내)

1. ✅ **ImprovedPredictor에 store_id 추가** (필수)
   - 현재 모든 쿼리가 점포 구분 없이 실행 중
   - 호반/동양 데이터 혼재로 예측 부정확

2. ✅ **점포별 eval_params.json 분리**
   - 점포별 특성 반영
   - 독립적인 파라미터 튜닝

3. ✅ **스케줄러 멀티 점포 지원**
   - 점포별 독립 실행
   - 점포별 성과 추적

### 중장기 검토 (3개월 후)

- 점포가 5개 이상 되면 통합 모델 실험
- A/B 테스트로 성과 비교
- 필요시 하이브리드 모델 전환

### 추가 고려사항

- 신규 점포 추가 시 최소 3개월 데이터 수집 후 자동발주 시작
- 점포별 KPI 대시보드 구축
- 점포 간 베스트 프랙티스 공유 체계

---

**최종 권장:** 단기적으로 **점포별 독립 모델**로 빠르게 구현하고, 점포 수 증가 시 **통합 모델**로 전환 검토

**작성자:** Claude
**검토 필요:** 시스템 아키텍트, ML 엔지니어
**다음 단계:** Phase 1 구현 착수
