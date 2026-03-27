# Plan: hourly-sales-api

## Feature: 시간대별 매출 Direct API 수집 + 배송차수 동적 비율

### 개요

BGF 리테일 STMB010 화면(`/stmb010/selDay`)에서 시간대별 매출 데이터를 Direct API로 수집하여,
현재 하드코딩된 `DELIVERY_TIME_DEMAND_RATIO`(1차=0.70, 2차=1.00)를 **실제 매출 분포 기반 동적 비율**로 대체한다.

### 배경

#### 현재 문제
- `food.py:126-129`의 `DELIVERY_TIME_DEMAND_RATIO`가 고정값:
  - 1차 배송(당일 20:00 도착): 0.70 (07:00~20:00 = 주간 수요 70% 추정)
  - 2차 배송(익일 07:00 도착): 1.00 (하루 전체)
- 실제 매출 분포는 요일/계절/날씨에 따라 변동 → 고정 비율은 정확도 한계

#### 캡처 완료 API
- **엔드포인트**: `POST /stmb010/selDay`
- **body 크기**: 1,116자 (SSV)
- **response 크기**: 1,765자 (SSV)
- **response Dataset**: `dsList`
- **response 컬럼**: `HMS:string(2)`, `AMT:bigdecimal`, `CNT:bigdecimal`
  - HMS: 시간대(00~23), AMT: 매출액, CNT: 판매건수
  - 24행 = 시간대별 매장 전체 매출 집계

### 목표

1. STMB010 Direct API로 시간대별 매출 수집 (Selenium 불필요)
2. `hourly_sales` DB 테이블에 저장
3. 실제 시간대별 매출 비율로 `DELIVERY_TIME_DEMAND_RATIO` 동적 계산
4. 기존 고정 비율은 데이터 부족 시 폴백으로 유지

### 현재 vs 목표 플로우

#### 현재 (고정값)
```
calculate_delivery_gap_consumption()
  → DELIVERY_TIME_DEMAND_RATIO = {"1차": 0.70, "2차": 1.00}  # 고정
  → gap_consumption = daily_avg * 0.70 * coefficient
```

#### 목표 (동적 비율)
```
Phase 1.04 (매출 수집 후):
  → HourlySalesCollector.collect(date) → /stmb010/selDay API
  → hourly_sales 테이블에 24시간 매출 저장

calculate_delivery_gap_consumption():
  → get_dynamic_time_demand_ratio(delivery_type, store_id)
  → DB에서 최근 7일 시간대별 매출 조회
  → 07:00~20:00 비율 계산 → 1차 비율
  → 데이터 없으면 → 기존 0.70 폴백
```

### 구현 계획

#### Step 1: DB 스키마 (`hourly_sales` 테이블)

```sql
CREATE TABLE IF NOT EXISTS hourly_sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sales_date TEXT NOT NULL,       -- YYYY-MM-DD
    hour INTEGER NOT NULL,          -- 0~23
    sale_amt REAL DEFAULT 0,        -- 매출액
    sale_cnt INTEGER DEFAULT 0,     -- 판매건수
    collected_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(sales_date, hour)
);
CREATE INDEX IF NOT EXISTS idx_hourly_sales_date ON hourly_sales(sales_date);
```

- **db_type**: `"store"` (매장별 DB)
- daily_sales 무영향 (별도 테이블)
- 24행/일 (00~23시)

#### Step 2: HourlySalesCollector

```python
class HourlySalesCollector:
    """STMB010 시간대별 매출 Direct API 수집기"""

    def collect(self, driver, target_date: str) -> List[Dict]:
        """
        1. XHR 인터셉터 설치 (1회)
        2. STMB010 메뉴 진입 → /stmb010/selDay body 캡처
        3. 날짜 파라미터 치환 → fetch() 직접 호출
        4. SSV 응답 파싱 → [{hour, amt, cnt}, ...]
        """
```

- 기존 `direct_api_fetcher.py`의 SSV 파싱 함수 재사용
- 병렬 호출 불필요 (1회 API 호출로 24시간 데이터 일괄 반환)
- 소요시간: ~1초 (현재 수집 없음 → 순증)

#### Step 3: HourlySalesRepository

```python
class HourlySalesRepository(BaseRepository):
    db_type = "store"

    def save_hourly_sales(self, data: List[Dict], sales_date: str) -> int
    def get_hourly_sales(self, sales_date: str) -> List[Dict]
    def get_recent_hourly_distribution(self, days: int = 7) -> Dict[int, float]
    def calculate_time_demand_ratio(self, store_id: str) -> Dict[str, float]
```

#### Step 4: 동적 비율 계산

```python
def get_dynamic_time_demand_ratio(store_id: str) -> Dict[str, float]:
    """최근 7일 시간대별 매출에서 1차/2차 비율 계산"""
    repo = HourlySalesRepository(store_id=store_id)
    distribution = repo.get_recent_hourly_distribution(days=7)

    if not distribution:
        return {"1차": 0.70, "2차": 1.00}  # 폴백

    total = sum(distribution.values())
    # 1차: 07:00~20:00 비율
    daytime = sum(distribution.get(h, 0) for h in range(7, 20))
    ratio_1st = round(daytime / total, 2) if total > 0 else 0.70

    return {"1차": ratio_1st, "2차": 1.00}
```

#### Step 5: food.py 통합

- `calculate_delivery_gap_consumption()`에서 `get_dynamic_time_demand_ratio()` 호출
- `DELIVERY_TIME_DEMAND_RATIO` 상수는 폴백으로 유지
- 데이터 없거나 에러 시 기존 고정값 사용

#### Step 6: 스케줄러 통합

- `daily_job.py` Phase 1.04 삽입 (Phase 1 매출 수집 직후)
- 실행 조건: 매일 1회 (어제+오늘 2일분 수집)

### 수정 대상 파일

| 파일 | 변경 | 비고 |
|------|------|------|
| `src/infrastructure/database/repos/hourly_sales_repo.py` | **신규** | DB Repository |
| `src/collectors/hourly_sales_collector.py` | **신규** | Direct API 수집기 |
| `src/prediction/categories/food.py` | 수정 | 동적 비율 통합 |
| `src/application/daily_job.py` | 수정 | Phase 1.04 추가 |
| `src/settings/ui_config.py` | 수정 | STMB010 FRAME_ID 추가 |
| `tests/test_hourly_sales.py` | **신규** | 테스트 |

### 예상 효과

| 항목 | 현재 | 개선 후 |
|------|------|--------|
| 배송차수 수요 비율 | 고정 0.70/1.00 | 실제 매출 기반 동적 |
| 데이터 수집 | 없음 | 매일 자동 (~1초) |
| 배송 갭 소비량 정확도 | ±20% 추정 오차 | ±5% 이내 |
| 과소/과잉 발주 | 고정 비율 오차 누적 | 매출 패턴 반영 |

### 리스크

| 리스크 | 대응 |
|--------|------|
| API body 템플릿 만료 | 인터셉터 재캡처 (기존 패턴) |
| 매출 데이터 0건 (야간) | 0건 시간대 무시, 유효 시간대만 비율 계산 |
| 기존 테스트 깨짐 | DELIVERY_TIME_DEMAND_RATIO 폴백 유지로 기존 로직 호환 |
| 서버 인증 실패 | Selenium 쿠키 공유 (direct_api_fetcher 동일 패턴) |

### 의존성

- `src/collectors/direct_api_fetcher.py`: `parse_ssv_dataset()`, `ssv_row_to_dict()`
- `src/infrastructure/database/base_repository.py`: `BaseRepository`
- BGF 사이트 API: `/stmb010/selDay` (캡처 완료)
