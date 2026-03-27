# Design: hourly-sales-api

## Feature: 시간대별 매출 Direct API 수집 + 배송차수 동적 비율

> Plan 참조: `docs/01-plan/features/hourly-sales-api.plan.md`

---

## 1. 아키텍처

```
[Daily Job Phase 1.04]
    │
    ▼
HourlySalesCollector.collect(driver, date)
    │
    ├── 1) XHR 인터셉터 설치 (JS)
    ├── 2) STMB010 메뉴 진입 → /stmb010/selDay body 캡처
    ├── 3) fetch() Direct API 호출
    ├── 4) SSV 파싱 → [{hour, amt, cnt}, ...]
    │
    ▼
HourlySalesRepository.save_hourly_sales()
    │
    ▼
hourly_sales 테이블 (stores/{store_id}.db)
    │
    ▼
food.py: get_dynamic_time_demand_ratio()
    │
    ▼
calculate_delivery_gap_consumption() → 동적 비율 적용
```

---

## 2. DB 스키마

### hourly_sales 테이블

```sql
CREATE TABLE IF NOT EXISTS hourly_sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sales_date TEXT NOT NULL,       -- YYYY-MM-DD
    hour INTEGER NOT NULL,          -- 0~23
    sale_amt REAL DEFAULT 0,        -- 매출액
    sale_cnt INTEGER DEFAULT 0,     -- 판매건수
    sale_cnt_danga REAL DEFAULT 0,  -- 객단가 (CNT_DANGA)
    rate REAL DEFAULT 0,            -- 매출 비율 (%)
    collected_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(sales_date, hour)
);
CREATE INDEX IF NOT EXISTS idx_hourly_sales_date ON hourly_sales(sales_date);
```

- **위치**: `stores/{store_id}.db` (db_type = "store")
- **행 수**: 24행/일 (시간대 00~23)
- **SSV 컬럼 매핑**: HMS→hour, AMT→sale_amt, CNT→sale_cnt

---

## 3. 컴포넌트 설계

### 3.1 HourlySalesCollector

**파일**: `src/collectors/hourly_sales_collector.py`

```python
class HourlySalesCollector:
    """STMB010 시간대별 매출 Direct API 수집기"""

    ENDPOINT = "/stmb010/selDay"
    DATASET_MARKER = "HMS"  # SSV에서 dsList 식별

    def __init__(self, driver=None):
        self.driver = driver
        self._template = None  # 캡처된 body 템플릿

    def collect(self, target_date: str) -> List[Dict]:
        """시간대별 매출 수집 (Direct API)"""
        # 1. body 템플릿 확보 (캡처 or 캐시)
        # 2. 날짜 파라미터 치환
        # 3. fetch() 호출
        # 4. SSV 파싱 → 24행

    def _ensure_template(self) -> str:
        """인터셉터 설치 + STMB010 진입 + body 캡처"""

    def _call_api(self, body: str) -> str:
        """driver.execute_script(fetch(url, body))"""

    def _parse_response(self, ssv_text: str) -> List[Dict]:
        """parse_ssv_dataset(ssv, 'HMS') → [{hour, amt, cnt}, ...]"""
```

**핵심 로직**:
- `_ensure_template()`: STMB010 화면 진입 없이 **캡처된 body 구조**로 직접 호출
- body 치환: `calFromDay` 날짜 파라미터만 교체
- 1회 API 호출로 24시간 전체 데이터 반환 (병렬 불필요)

### 3.2 HourlySalesRepository

**파일**: `src/infrastructure/database/repos/hourly_sales_repo.py`

```python
class HourlySalesRepository(BaseRepository):
    db_type = "store"

    def ensure_table(self) -> None:
        """hourly_sales 테이블 DDL (없으면 생성)"""

    def save_hourly_sales(self, data: List[Dict], sales_date: str) -> int:
        """24행 INSERT OR REPLACE → 저장 건수 반환"""

    def get_hourly_sales(self, sales_date: str) -> List[Dict]:
        """특정 날짜 시간대별 매출 조회"""

    def get_recent_hourly_distribution(self, days: int = 7) -> Dict[int, float]:
        """최근 N일 시간대별 평균 매출 비율 반환
        Returns: {0: 0.02, 1: 0.01, ..., 12: 0.12, ..., 23: 0.03}
        """

    def calculate_time_demand_ratio(self) -> Dict[str, float]:
        """1차/2차 배송 비율 계산
        1차 = sum(07~19) / sum(00~23)
        2차 = 1.00 (전체)
        """
```

### 3.3 food.py 수정

**변경 범위**: `calculate_delivery_gap_consumption()` 함수 내부

```python
# 기존 (Line 195)
time_demand_ratio = DELIVERY_TIME_DEMAND_RATIO.get(delivery_type, 1.0)

# 변경
time_demand_ratio = _get_time_demand_ratio(delivery_type, store_id)

def _get_time_demand_ratio(delivery_type: str, store_id: Optional[str]) -> float:
    """동적 비율 조회 (DB) + 폴백 (고정값)"""
    if store_id:
        try:
            repo = HourlySalesRepository(store_id=store_id)
            ratios = repo.calculate_time_demand_ratio()
            if ratios:
                return ratios.get(delivery_type, 1.0)
        except Exception:
            pass
    return DELIVERY_TIME_DEMAND_RATIO.get(delivery_type, 1.0)
```

### 3.4 daily_job.py 통합

```python
# Phase 1.04: 시간대별 매출 수집
def _collect_hourly_sales(self):
    collector = HourlySalesCollector(driver=self.driver)
    for date in [yesterday, today]:
        data = collector.collect(target_date=date)
        if data:
            repo = HourlySalesRepository(store_id=self.store_id)
            repo.ensure_table()
            saved = repo.save_hourly_sales(data, date)
            logger.info(f"[Phase 1.04] 시간대별 매출 {saved}건 저장 ({date})")
```

### 3.5 ui_config.py 추가

```python
FRAME_IDS["HOURLY_SALES_DETAIL"] = "STMB010_M0"  # 시간대별 매출 정보
```

---

## 4. SSV 파싱 상세

### 요청 body 구조 (캡처됨, 1116자)

```
SSV:utf-8
GV_USERFLAG=HOME
_xm_webid_1_={session}
_xm_tid_1_={txid}
SS_STORE_CD={store_cd}        ← 매장코드
...
calFromDay={YYYYMMDD}         ← 조회 날짜 (치환 대상)
...
```

### 응답 구조

```
SSV:UTF-8
ErrorCode:string=0
ErrorMsg:string=
xm_tid:string={txid}
Dataset:dsList
_RowType_{US}HMS:string(2){US}AMT:bigdecimal(0){US}CNT:bigdecimal(0){US}CNT_DANGA:bigdecimal(0){US}RATE:bigdecimal(0){US}PAGE_CNT:bigdecimal(0)
{RS}00{US}125000{US}15{US}8333{US}2.5{US}0
{RS}01{US}85000{US}10{US}8500{US}1.7{US}0
...
{RS}23{US}95000{US}12{US}7917{US}1.9{US}0
```

### 파싱 코드

```python
from src.collectors.direct_api_fetcher import parse_ssv_dataset, ssv_row_to_dict

parsed = parse_ssv_dataset(ssv_text, 'HMS')
# parsed = {'columns': ['_RowType_', 'HMS', 'AMT', 'CNT', 'CNT_DANGA', 'RATE', 'PAGE_CNT'],
#           'rows': [[...], ...]}

for row in parsed['rows']:
    d = ssv_row_to_dict(parsed['columns'], row)
    # d = {'HMS': '12', 'AMT': '1250000', 'CNT': '150', 'CNT_DANGA': '8333', 'RATE': '25.0'}
```

---

## 5. 동적 비율 계산 로직

```python
def calculate_time_demand_ratio(self) -> Dict[str, float]:
    """
    최근 7일 시간대별 매출에서 배송차수 비율 계산

    1차 배송 (당일 20:00 도착):
      - 대응 시간: 07:00 ~ 19:59
      - 비율 = sum(07~19 매출) / sum(00~23 매출)

    2차 배송 (익일 07:00 도착):
      - 대응 시간: 전체 24시간
      - 비율 = 1.00 (고정)
    """
    dist = self.get_recent_hourly_distribution(days=7)
    if not dist:
        return {}

    total = sum(dist.values())
    if total <= 0:
        return {}

    daytime_sum = sum(dist.get(h, 0) for h in range(7, 20))
    ratio_1st = round(daytime_sum / total, 3)

    # 안전 범위: 0.50 ~ 0.90 (극단값 방지)
    ratio_1st = max(0.50, min(0.90, ratio_1st))

    return {"1차": ratio_1st, "2차": 1.00}
```

---

## 6. 테스트 계획

| # | 테스트 | 검증 내용 |
|---|--------|----------|
| 1 | SSV 파싱 테스트 | HMS 24행 정상 파싱 |
| 2 | DB 저장/조회 | hourly_sales UPSERT, 조회 |
| 3 | 비율 계산 (정상) | 7일 데이터 → 1차 비율 0.65~0.75 |
| 4 | 비율 계산 (데이터 없음) | 빈 딕셔너리 반환 → 폴백 |
| 5 | 비율 안전 범위 | 0.50 이하/0.90 이상 클램프 |
| 6 | food.py 폴백 | DB 에러 시 기존 0.70 사용 |
| 7 | food.py 동적 비율 | DB 데이터 있을 때 동적 값 사용 |
| 8 | daily_job 통합 | Phase 1.04 정상 실행 |
| 9 | ensure_table | 테이블 없을 때 자동 생성 |
| 10 | 날짜 범위 조회 | 특정 날짜 + 최근 N일 |

---

## 7. 구현 순서

1. `hourly_sales_repo.py` — DB 테이블 DDL + CRUD
2. `hourly_sales_collector.py` — Direct API 수집기
3. `food.py` 수정 — 동적 비율 조회 함수
4. `daily_job.py` 수정 — Phase 1.04 추가
5. `ui_config.py` 수정 — FRAME_ID 추가
6. `tests/test_hourly_sales.py` — 전체 테스트
