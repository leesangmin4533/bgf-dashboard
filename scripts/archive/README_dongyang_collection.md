# 동양점(46704) 6개월 데이터 수집 가이드

## 개요

호반점(46513)과 동일한 DB 구조로 동양점(46704) 데이터를 수집하고 중분류 매출 구성비를 분석합니다.

---

## DB 구조

### daily_sales 테이블 (호반점과 동일)

```sql
CREATE TABLE daily_sales (
    id INTEGER PRIMARY KEY,
    sales_date TEXT,
    store_id TEXT,      -- '46513'(호반) or '46704'(동양)
    item_cd TEXT,       -- 상품코드 (13자리)
    mid_cd TEXT,        -- 중분류코드
    sale_qty INTEGER,   -- 판매량
    ord_qty INTEGER,    -- 발주량
    buy_qty INTEGER,    -- 입고량
    disuse_qty INTEGER, -- 폐기량
    stock_qty INTEGER,  -- 재고량
    ...
);
```

**특징:**
- 호반점(46513)과 동양점(46704)이 동일한 테이블에 store_id로 구분
- 중분류별 매출 데이터는 mid_cd로 집계
- 구성비는 쿼리로 실시간 계산

---

## 사용법

### 1. 6개월 데이터 수집 + 구성비 계산

```bash
cd bgf_auto
python scripts/collect_dongyang_6months.py
```

**실행 과정:**
1. 동양점(46704) 6개월 데이터 자동 수집
2. 중분류별 판매량 집계
3. 구성비 계산 및 출력

**예상 소요 시간:** 약 360분 (180일 × 2분/일)

---

### 2. 구성비만 재계산 (수집 생략)

이미 데이터가 있는 경우 수집 없이 구성비만 계산:

```bash
python scripts/collect_dongyang_6months.py --analysis-only
```

---

### 3. 호반점과 비교 분석

```bash
python scripts/collect_dongyang_6months.py --compare
```

**출력 예시:**
```
점포별 매출 비교 (2025-08-06 ~ 2026-02-05)
================================================================================

[호반점 (46513)]
  수집 일수: 180일
  중분류 수:  25개
  상품 수:   150개
  총 판매량: 12,500개
  일평균:    69.4개

[동양점 (46704)]
  수집 일수: 180일
  중분류 수:  23개
  상품 수:   130개
  총 판매량: 10,800개
  일평균:    60.0개
```

---

### 4. 3개월 데이터만 분석

```bash
python scripts/collect_dongyang_6months.py --analysis-only --months 3
```

---

## 출력 예시

### 중분류별 매출 구성비

```
동양점(46704) 중분류별 매출 구성비 (2025-08-06 ~ 2026-02-05)
================================================================================

기간: 2025-08-06 ~ 2026-02-05 (6개월)
전체 판매량: 10,800개
중분류 수: 23개

--------------------------------------------------------------------------------
   중분류    |      중분류명       | 상품수 |     판매량 |  구성비  | 일평균
--------------------------------------------------------------------------------
    072     |       담배         |   35  |     2,400  |  22.22% |    13.3
    049     |       맥주         |   12  |     1,800  |  16.67% |    10.0
    050     |       소주         |    8  |     1,500  |  13.89% |     8.3
    001     |      도시락        |   15  |     1,200  |  11.11% |     6.7
    032     |       면류         |   18  |       900  |   8.33% |     5.0
    ...
--------------------------------------------------------------------------------
    합계    |                   |  130  |    10,800  | 100.00% |
================================================================================

[수집 현황]
  수집 일수: 180일
  최초 날짜: 2025-08-06
  최근 날짜: 2026-02-05
  예상 일수: 184일
  완성률: 97.8%
```

---

## 데이터 확인

### SQL로 직접 조회

```sql
-- 동양점 중분류별 판매량 (최근 7일)
SELECT
    mc.mid_cd,
    mc.mid_nm,
    COUNT(DISTINCT ds.item_cd) as item_count,
    SUM(ds.sale_qty) as total_sale_qty
FROM daily_sales ds
JOIN mid_categories mc ON ds.mid_cd = mc.mid_cd
WHERE ds.store_id = '46704'
  AND ds.sales_date >= date('now', '-7 days')
GROUP BY mc.mid_cd, mc.mid_nm
ORDER BY total_sale_qty DESC;
```

### Python으로 조회

```python
from src.db.models import get_connection

conn = get_connection()
cursor = conn.cursor()

cursor.execute('''
    SELECT mid_cd, SUM(sale_qty) as total
    FROM daily_sales
    WHERE store_id = '46704'
      AND sales_date >= date('now', '-30 days')
    GROUP BY mid_cd
    ORDER BY total DESC
''')

for row in cursor.fetchall():
    print(f"{row['mid_cd']}: {row['total']:,}개")

conn.close()
```

---

## 주의사항

### 1. 데이터 수집 시간
- 6개월(약 180일) 수집: 약 360분 (6시간)
- 배치 단위(30일): 약 60분
- 배치 간 30초 대기

### 2. 중복 수집 방지
- 기본적으로 이미 수집된 날짜는 건너뜀
- `--force` 옵션으로 재수집 가능 (HistoricalDataCollector 사용)

### 3. 구성비 계산
- 실시간 계산 (저장되지 않음)
- 필요시 언제든지 `--analysis-only`로 재계산 가능

### 4. 호반점과의 일관성
- 동일한 테이블 (daily_sales)
- 동일한 컬럼 구조
- store_id로만 구분

---

## 문제 해결

### Q1. "동양점 데이터가 없습니다" 오류

**원인:** 데이터 수집이 완료되지 않음

**해결:**
```bash
# 데이터 수집 실행
python scripts/collect_dongyang_6months.py
```

### Q2. 수집 중단 후 재개

**원인:** 네트워크 오류 등으로 중단

**해결:**
```bash
# 누락된 날짜만 자동 수집
python scripts/collect_dongyang_6months.py
```

### Q3. 구성비가 0%로 나옴

**원인:** 해당 기간에 판매 데이터 없음

**확인:**
```bash
# 수집 현황 확인
python -m src.collectors.historical_data_collector --store-id 46704 --status-only
```

---

## 관련 파일

| 파일 | 설명 |
|------|------|
| `scripts/collect_dongyang_6months.py` | 동양점 데이터 수집 스크립트 |
| `src/collectors/historical_data_collector.py` | 범용 과거 데이터 수집기 |
| `src/db/models.py` | DB 스키마 정의 (daily_sales 테이블) |
| `src/db/repository.py` | SalesRepository (데이터 저장) |
| `config/stores.json` | 점포 정보 (46513, 46704) |

---

## 참고

- 호반점(46513): 이천호반베르디움점 (본점)
- 동양점(46704): 이천동양점 (2호점)
- DB 파일: `data/bgf_sales.db`
- 스키마 버전: v20

---

**작성일:** 2026-02-06
**업데이트:** 호반점 구조 기반 스크립트 생성
