# Plan: new-product-lifecycle

> 신제품 초기 모니터링 및 라이프사이클 관리

## 1. 배경 및 목적

### 문제
`receiving-new-product-detect` 기능으로 입고 시 신제품이 자동 감지/등록되지만,
등록 이후 별도의 라이프사이클 관리가 없다:
- 초기 N일간 판매 추이를 추적하지 않음
- 판매 데이터 부족(< 7일)으로 예측이 부정확 (slow 분류 → 예측 0)
- mid_cd가 fallback 추정값이라 카테고리 오분류 가능성
- 신제품 감지 사실을 대시보드에서 알려주지 않음
- 초기 발주량 보정 없음 (유사 상품 기반 참고값 부재)

### 목적
신제품 감지 후 **초기 14일 모니터링 기간** 동안:
1. 일별 판매/재고 추이를 자동 수집하여 판매 패턴 조기 파악
2. 유사 상품(같은 mid_cd) 평균을 참고한 초기 발주량 보정
3. 모니터링 완료 후 안정 상태로 자동 전환 (lifecycle status)
4. 대시보드에 신제품 알림 + 모니터링 현황 표시

## 2. 기능 범위

### In-scope
1. **모니터링 추적기** (NewProductMonitor)
   - detected_new_products에 등록된 상품의 일별 판매/재고 자동 수집
   - 모니터링 기간: 감지일로부터 14일
   - Phase 1.35 위치 (Phase 1.3 NewProductCollector 뒤, Phase 1.5 EvalCalibrator 앞)

2. **초기 발주량 보정** (NewProductOrderBooster)
   - 판매 데이터 < 7일인 신제품 → 같은 mid_cd 유사 상품 평균 참조
   - 보정 공식: `max(유사상품_일평균 * 0.7, 현재_예측값)`
   - improved_predictor.py 에 신제품 보정 단계 추가

3. **라이프사이클 상태 관리**
   - detected_new_products 테이블에 `lifecycle_status` 컬럼 추가
   - 상태 흐름: `detected` → `monitoring` → `stable` → `normal`
   - 14일 경과 + 판매 3일 이상 → `stable` 자동 전환
   - 14일 경과 + 판매 0일 → `no_demand` (발주 제외 후보)

4. **대시보드 알림**
   - 홈탭 이벤트에 "신제품 N건 감지" 표시
   - /api/receiving/new-products/monitoring 엔드포인트 (모니터링 중 상품 목록)
   - 일별 판매 추이 차트 데이터 API

### Out-of-scope
- BGF 도입률/달성률(new_product_collector)과의 자동 연계 (별도 feature)
- mid_cd 자동 교정 (BGF 시스템에서 정확한 카테고리 가져오기 불가)
- 신제품 전용 프로모션 로직

## 3. 기술 설계 방향

### 3.1 DB 변경 (v46)

**detected_new_products 테이블 컬럼 추가:**
```sql
ALTER TABLE detected_new_products ADD COLUMN lifecycle_status TEXT DEFAULT 'detected';
ALTER TABLE detected_new_products ADD COLUMN monitoring_start_date TEXT;
ALTER TABLE detected_new_products ADD COLUMN monitoring_end_date TEXT;
ALTER TABLE detected_new_products ADD COLUMN total_sold_qty INTEGER DEFAULT 0;
ALTER TABLE detected_new_products ADD COLUMN sold_days INTEGER DEFAULT 0;
ALTER TABLE detected_new_products ADD COLUMN similar_item_avg REAL;
ALTER TABLE detected_new_products ADD COLUMN status_changed_at TEXT;
```

**신규 테이블: new_product_daily_tracking**
```sql
CREATE TABLE new_product_daily_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_cd TEXT NOT NULL,
    tracking_date TEXT NOT NULL,
    sales_qty INTEGER DEFAULT 0,
    stock_qty INTEGER DEFAULT 0,
    order_qty INTEGER DEFAULT 0,
    store_id TEXT,
    UNIQUE(item_cd, tracking_date, store_id)
);
```

### 3.2 핵심 컴포넌트

| 컴포넌트 | 위치 | 역할 |
|-----------|------|------|
| NewProductMonitor | src/application/services/ | 일별 추적 수집 + 상태 전환 |
| NewProductOrderBooster | src/prediction/ | 유사 상품 기반 초기 보정 |
| DetectedNewProductRepo (확장) | repos/ | lifecycle 관련 쿼리 추가 |
| API 엔드포인트 (확장) | api_receiving.py | 모니터링 현황 API |

### 3.3 실행 흐름

```
Phase 1.1:  ReceivingCollector → 신제품 감지 (lifecycle_status='detected')
Phase 1.35: NewProductMonitor
            ├─ detected/monitoring 상품 일별 판매/재고 수집
            ├─ detected → monitoring 전환 (첫 모니터링)
            ├─ 14일 경과 → stable/no_demand 전환
            └─ stable 전환 시 similar_item_avg 계산/저장
Phase 1.7:  ImprovedPredictor
            └─ NewProductOrderBooster (monitoring 상태 상품만)
                └─ 유사 상품 일평균 참조 보정
Phase 2.0:  AutoOrder → 보정된 예측값으로 발주
```

### 3.4 유사 상품 참조 로직

```python
def get_similar_item_avg(item_cd, mid_cd, store_id):
    """같은 mid_cd 상품 중 30일 판매 데이터 있는 상품의 일평균"""
    # 1) products에서 같은 mid_cd 상품 조회 (자기 자신 제외)
    # 2) daily_sales에서 최근 30일 일평균 계산
    # 3) 중위값 반환 (이상치 제거)
    # 4) 없으면 None (보정 안 함)
```

### 3.5 lifecycle_status 전환 규칙

| 현재 상태 | 조건 | 다음 상태 |
|-----------|------|-----------|
| detected | NewProductMonitor 첫 실행 | monitoring |
| monitoring | 14일 경과 + sold_days >= 3 | stable |
| monitoring | 14일 경과 + sold_days == 0 | no_demand |
| monitoring | 14일 경과 + 0 < sold_days < 3 | slow_start |
| stable | 30일 경과 | normal (일반 상품) |
| no_demand | 수동 확인 후 | excluded 또는 monitoring (재시도) |

## 4. 리스크 분석

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 유사 상품이 없는 카테고리 (mid_cd="999") | 보정 불가 | 보정 skip, 기존 로직 유지 |
| Phase 1.35 추가로 daily_job 시간 증가 | 1~2초 | DB 쿼리 최적화 (배치) |
| monitoring 기간 중 과잉 발주 | 재고 누적 | 보정 상한: 유사상품_avg * 1.0 |
| detected_new_products 컬럼 추가 마이그레이션 | v46 필요 | ALTER TABLE (기존 데이터 보존) |

## 5. 테스트 계획

| 테스트 | 내용 |
|--------|------|
| 모니터링 수집 | 일별 판매/재고 정확히 기록되는지 |
| 상태 전환 | detected→monitoring→stable 자동 전환 |
| no_demand 분류 | 14일 판매 0건 시 정확히 분류 |
| 유사 상품 계산 | mid_cd 기반 중위값 정확성 |
| 발주량 보정 | max(유사avg*0.7, 현재예측) 적용 |
| 보정 미적용 | mid_cd=999이거나 유사상품 없을 때 skip |
| Phase 순서 | Phase 1.35 위치 정확성 |
| API 응답 | /monitoring 엔드포인트 정상 응답 |
| 기존 테스트 호환 | 2274개 테스트 깨지지 않음 |

## 6. 구현 순서

1. DB 스키마 v46 (detected_new_products ALTER + new_product_daily_tracking)
2. DetectedNewProductRepository 확장 (lifecycle 쿼리)
3. NewProductMonitor 서비스 작성
4. NewProductOrderBooster 작성
5. improved_predictor.py 보정 단계 추가
6. daily_job.py Phase 1.35 추가
7. Web API 엔드포인트 확장
8. 테스트 작성
9. 기존 테스트 통과 확인
