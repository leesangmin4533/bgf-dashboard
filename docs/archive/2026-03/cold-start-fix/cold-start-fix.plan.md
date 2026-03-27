# Plan: cold-start-fix (신규 상품 콜드스타트 발주 순환 해결)

## 1. 문제 정의

### 현상
- 비식품 신규 상품(담배/잡화/음료 등)이 판매 이력 발생 후에도 자동발주가 0으로 나와 영원히 발주되지 않음
- 실제 사례: `8801116032600` (릴하이브리드3누아르블랙, mid_cd=073)
  - 3/8 판매 1개 발생 → 3/9 발주 0개 → 재고 0 → 판매 불가 → 데이터 미축적 → ∞ 반복

### 순환 구조
```
판매 발생 (1일차) → WMA(7일)=0.00 → 예측=0 → 발주=0
  → 재고=0 → 판매불가 → 데이터 미축적 → WMA 더 낮아짐 → 영원히 발주=0
```

### 4개 안전장치 동시 실패 원인
| 안전장치 | 조건 | 이 상품 | 결과 |
|----------|------|---------|------|
| ROP (최소발주) | sell_day_ratio < 0.3 | 1/1=100% | **스킵** |
| 신제품 부스트 | detected_new_products 등록 | 판매로 발견→미등록 | **스킵** |
| FORCE_ORDER | 품절+고인기 | popularity=0.08→저인기→NORMAL | **미적용** |
| 담배 안전재고 | daily_avg × safety_days | daily_avg=0.04→0 | **0** |

## 2. 해결 방안

### 접근: 데이터 7일 미만 신규 상품 일평균 보정

**수정 위치**: `base_predictor.py` `_compute_wma()` (Line 99~174)

**로직**:
```python
# WMA 계산 후, 데이터 부족 시 일평균 보정
data_days = self._data._get_data_span_days(item_cd)

if data_days < 7 and data_days > 0:
    # 7일 미만이면 actual_days 기반 일평균 사용
    total_sales = sum(row[1] for row in history)
    daily_avg = total_sales / data_days
    if daily_avg > wma_prediction:
        wma_prediction = daily_avg
        logger.info(f"[PRED][cold-start] {item_cd}: data_days={data_days}, "
                    f"daily_avg={daily_avg:.2f} → WMA 보정")
```

**효과**:
- 현재: 1일 1개 판매 → WMA(7일) = 1/7 = 0.14 → 반올림 0
- 수정 후: 1일 1개 판매 → daily_avg = 1/1 = 1.0 → 발주 1개

### 대상 범위
- **전 카테고리 공통** (푸드/비푸드 모두)
- WMA 결과보다 일평균이 높을 때만 보정 (기존 데이터가 충분한 상품에 영향 없음)
- 7일 이상 데이터 축적 후 자동으로 일반 WMA로 전환

## 3. 영향 범위

### 수정 파일
| 파일 | 수정 내용 |
|------|-----------|
| `src/prediction/base_predictor.py` | `_compute_wma()` 내 data_days < 7 보정 추가 |

### 영향 없는 부분
- 기존 7일 이상 데이터 보유 상품: 일평균 < WMA이므로 보정 미적용
- 푸드 카테고리: 이미 exempt 파이프라인으로 별도 처리, 추가 안전망 역할
- ROP/FORCE_ORDER/신제품 부스트: 기존 로직 변경 없음 (독립적으로 작동)

## 4. 테스트 계획

| # | 테스트 | 검증 내용 |
|---|--------|-----------|
| 1 | data_days=1, 판매=1 | WMA 보정 → daily_avg=1.0 적용 |
| 2 | data_days=3, 판매=5 | WMA 보정 → daily_avg=1.67 적용 |
| 3 | data_days=6, 판매=2 | WMA 보정 → daily_avg=0.33 적용 |
| 4 | data_days=7, 판매=7 | 보정 미적용 (기존 WMA 유지) |
| 5 | data_days=1, 판매=0 | daily_avg=0 → 보정 미적용 |
| 6 | data_days=30, 판매=30 | 보정 미적용 (기존 WMA 유지) |
| 7 | data_days=2, WMA > daily_avg | 보정 미적용 (WMA가 더 높음) |
| 8 | 기존 테스트 전체 통과 | 회귀 없음 확인 |

## 5. 리스크

| 리스크 | 대응 |
|--------|------|
| 1일 이상치 판매(대량구매)로 과잉 발주 | WMA가 daily_avg보다 높으면 보정 안 함 → 이상치 처리된 WMA 우선 |
| 반품/입고 오류로 fake 판매 | 7일 후 자동으로 WMA로 전환 → 단기 자가 복구 |

## 6. 구현 우선순위

- **긴급**: 현재 영향받는 신규 상품이 계속 발주 누락 중
- **공수**: 1개 파일, ~10줄 수정
- **위험도**: 낮음 (기존 상품에 영향 없음, 보정 조건이 좁음)
