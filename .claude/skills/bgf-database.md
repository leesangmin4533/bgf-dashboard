# BGF 데이터베이스 스킬

## When to Use

- 판매/재고/발주 데이터를 저장하거나 조회할 때
- 새 테이블이나 컬럼을 추가해야 할 때 (스키마 마이그레이션)
- Repository 클래스를 새로 만들거나 기존 것을 확장할 때
- 행사(promotion), 폐기(expiry), 예측(prediction) 관련 데이터를 다룰 때

## Common Pitfalls

- ❌ `repository.py` 없이 직접 SQL 실행 → 코드 중복, 연결 관리 누락
- ✅ 반드시 `BaseRepository` 상속 후 `self._get_conn()` 사용

- ❌ `conn.close()` 누락 → SQLite 잠금(lock) 발생
- ✅ `try/finally` 블록에서 반드시 `conn.close()` 호출

- ❌ 스키마 변경 시 `models.py`만 수정 → 기존 DB에 반영 안됨
- ✅ `SCHEMA_VERSION` 증가 + `SCHEMA_MIGRATIONS`에 ALTER/CREATE 추가

- ❌ `ORD_QTY`를 실제 개수로 사용 → 배수 단위임
- ✅ 실제 개수 = `ORD_QTY × ORD_UNIT_QTY` (입수 곱하기)

- ❌ `daily_sales`에 같은 날짜+상품코드 INSERT → UNIQUE 위반
- ✅ `INSERT OR REPLACE` 또는 `ON CONFLICT` 사용

## Troubleshooting

| 증상 | 원인 | 해결 |
|------|------|------|
| "database is locked" | 다른 프로세스가 DB 사용 중 | `conn.close()` 누락 확인, 동시 접근 방지 |
| 마이그레이션 후 컬럼 없음 | `SCHEMA_VERSION` 미증가 | `models.py`에서 버전 번호 확인 |
| `dict(row)` 에서 KeyError | 컬럼명 오타 | `sqlite3.Row` 반환값의 키 확인 |
| 빈 결과 반환 | 날짜 포맷 불일치 | `YYYY-MM-DD` 형식 확인 (TEXT 타입) |
| 행사 변경 감지 안됨 | `save_monthly_promo()` 미호출 | `OrderPrepCollector`에서 수집 시 자동 저장 확인 |

---

## DB 파일 위치

- **경로**: `data/common.db` (공통) + `data/stores/{store_id}.db` (매장별)
- **레거시**: `data/bgf_sales.db` (과도기 백업)
- **스키마 버전**: v27
- **모델 정의**: `src/db/models.py`
- **Repository**: `src/infrastructure/database/repos/` (19개 파일) — 레거시 shim: `src/db/repository.py`
- **버전 상수**: `src/settings/constants.py` → `DB_SCHEMA_VERSION = 27`

## 핵심 테이블

### daily_sales (일별 판매 - 핵심)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| sales_date | TEXT | 판매일 (YYYY-MM-DD), PK 일부 |
| item_cd | TEXT | 상품코드, PK 일부 |
| mid_cd | TEXT | 중분류코드 |
| sale_qty | INTEGER | 판매수량 |
| ord_qty | INTEGER | 발주수량 |
| buy_qty | INTEGER | 입고수량 |
| disuse_qty | INTEGER | 폐기수량 |
| stock_qty | INTEGER | 재고수량 |

- `UNIQUE(sales_date, item_cd)`

### products (상품 마스터)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| item_cd | TEXT | 상품코드 (PK) |
| item_nm | TEXT | 상품명 |
| mid_cd | TEXT | 중분류코드 (FK) |

### product_details (상품 상세)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| item_cd | TEXT | 상품코드 (PK) |
| expiration_days | INTEGER | 유통기한 (일) |
| orderable_day | TEXT | 발주가능요일 (기본: 일월화수목금토) |
| order_unit_qty | INTEGER | 입수 (1배수당 개수) |
| promo_type | TEXT | 행사유형 (1+1, 2+1) |
| promo_start | TEXT | 행사시작일 |
| promo_end | TEXT | 행사종료일 |

### promotions (행사 정보)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| item_cd | TEXT | 상품코드 |
| promo_type | TEXT | 행사유형 (1+1, 2+1) |
| start_date | TEXT | 시작일 (YYYY-MM-DD) |
| end_date | TEXT | 종료일 (YYYY-MM-DD) |
| is_active | INTEGER | 활성여부 (0/1) |
| item_nm | TEXT | 상품명 |

- `UNIQUE(item_cd, promo_type, start_date)`

### promotion_changes (행사 변경 이력)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| item_cd | TEXT | 상품코드 |
| change_type | TEXT | 변경유형 (start, end, change) |
| change_date | TEXT | 변경 적용일 |
| prev_promo_type | TEXT | 이전 행사 (NULL이면 없었음) |
| next_promo_type | TEXT | 이후 행사 (NULL이면 종료) |

- `UNIQUE(item_cd, change_date, change_type)`

### promotion_stats (행사별 판매 통계)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| item_cd | TEXT | 상품코드 |
| promo_type | TEXT | normal / 1+1 / 2+1 |
| avg_daily_sales | REAL | 일평균 판매량 |
| multiplier | REAL | normal 대비 배율 |

- `UNIQUE(item_cd, promo_type)`

### realtime_inventory (실시간 재고)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| item_cd | TEXT | 상품코드 (UNIQUE) |
| stock_qty | INTEGER | 현재재고 (NOW_QTY) |
| pending_qty | INTEGER | 미입고수량 |
| order_unit_qty | INTEGER | 입수 |
| is_available | INTEGER | 점포취급여부 (0/1) |
| queried_at | TEXT | 조회시점 |

### order_tracking (발주 추적 - 폐기 관리)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| order_date | TEXT | 발주일 |
| item_cd | TEXT | 상품코드 |
| delivery_type | TEXT | 배송차수 (1차/2차) |
| order_qty | INTEGER | 발주수량 |
| arrival_time | TEXT | 도착예정 (YYYY-MM-DD HH:MM) |
| expiry_time | TEXT | 폐기예정 (YYYY-MM-DD HH:MM) |
| status | TEXT | ordered/arrived/selling/expired/disposed |

### eval_outcomes (사전 평가 결과 + 사후 검증) — v9

| 컬럼 | 타입 | 설명 |
|------|------|------|
| eval_date | TEXT | 평가일 (YYYY-MM-DD) |
| item_cd | TEXT | 상품코드 |
| mid_cd | TEXT | 중분류 코드 |
| decision | TEXT | FORCE_ORDER / URGENT_ORDER / NORMAL_ORDER / PASS / SKIP |
| exposure_days | REAL | 예측 노출일수 |
| popularity_score | REAL | 인기도 점수 |
| actual_sold_qty | INTEGER | 실제 판매량 (사후 기록) |
| was_stockout | INTEGER | 품절 여부 (0/1) |
| outcome | TEXT | CORRECT / UNDER_ORDER / OVER_ORDER / MISS |

- `UNIQUE(eval_date, item_cd)`
- v14에서 ML 컬럼 10개 추가 (predicted_qty, weekday, promo_type 등)

### calibration_history (파라미터 보정 이력) — v9

| 컬럼 | 타입 | 설명 |
|------|------|------|
| calibration_date | TEXT | 보정일 |
| param_name | TEXT | 파라미터 이름 |
| old_value | REAL | 이전 값 |
| new_value | REAL | 새 값 |
| reason | TEXT | 보정 사유 |
| accuracy_before | REAL | 보정 전 적중률 |

### inventory_batches (FIFO 재고 배치) — v11

| 컬럼 | 타입 | 설명 |
|------|------|------|
| item_cd | TEXT | 상품코드 |
| receiving_date | TEXT | 입고일 |
| expiration_days | INTEGER | 유통기한 (일) |
| expiry_date | TEXT | 폐기 예정일 |
| initial_qty | INTEGER | 입고 수량 |
| remaining_qty | INTEGER | 잔여 수량 (FIFO 차감) |
| status | TEXT | active / consumed / expired |

### auto_order_items / smart_order_items (발주 제외 캐시) — v15, v16

| 컬럼 | 타입 | 설명 |
|------|------|------|
| item_cd | TEXT | 상품코드 (PK) |
| item_nm | TEXT | 상품명 |
| mid_cd | TEXT | 중분류 코드 |
| detected_at | TEXT | 최초 감지 시점 |
| updated_at | TEXT | 마지막 갱신 시점 |

### app_settings (앱 설정) — v17

| 컬럼 | 타입 | 설명 |
|------|------|------|
| key | TEXT | 설정 키 (PK) |
| value | TEXT | 설정 값 |
| updated_at | TEXT | 갱신 시점 |

- 기본값: `EXCLUDE_AUTO_ORDER=true`, `EXCLUDE_SMART_ORDER=true`

### order_fail_reasons (발주 실패 사유) — v18

| 컬럼 | 타입 | 설명 |
|------|------|------|
| eval_date | TEXT | 평가일 |
| item_cd | TEXT | 상품코드 |
| stop_reason | TEXT | 중지 사유 |
| orderable_status | TEXT | 발주가능 상태 |
| order_status | TEXT | fail (기본) |

- `UNIQUE(eval_date, item_cd)`

## Repository 패턴

### 기본 구조

```python
from src.db.repository import BaseRepository

class MyRepository(BaseRepository):
    """새 Repository 생성 시 BaseRepository 상속"""

    def my_method(self):
        conn = self._get_conn()  # SQLite 연결 (row_factory=sqlite3.Row)
        cursor = conn.cursor()
        now = self._now()        # 현재시간 ISO 포맷

        try:
            cursor.execute("SELECT ...", (param,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
```

### 사용 가능한 Repository 클래스 (17개)

| 클래스 | 위치 (line) | 용도 |
|--------|:-----------:|------|
| `BaseRepository` | 24 | 기본 클래스 (`_get_conn()`, `_now()`) |
| `SalesRepository` | 71 | 판매 데이터 CRUD |
| `ExternalFactorRepository` | 659 | 외부 요인 (날씨, 공휴일) |
| `ProductDetailRepository` | 729 | 상품 상세 정보 |
| `OrderRepository` | 979 | 발주 이력 |
| `PredictionRepository` | 1114 | 예측 로그 |
| `OrderTrackingRepository` | 1222 | 발주 추적 (배치별 폐기 관리) |
| `ReceivingRepository` | 1538 | 입고 이력 |
| `RealtimeInventoryRepository` | 1917 | 실시간 재고/미입고/CUT 상태 |
| `PromotionRepository` | 2306 | 행사 정보 (1+1, 2+1, 변경 이력) |
| `EvalOutcomeRepository` | 2640 | 사전 평가 결과 + 사후 검증 |
| `CalibrationRepository` | 3109 | 파라미터 보정 이력 |
| `InventoryBatchRepository` | 3208 | FIFO 재고 배치 (폐기 추적) |
| `AutoOrderItemRepository` | 3585 | 자동발주 상품 캐시 |
| `SmartOrderItemRepository` | 3687 | 스마트발주 상품 캐시 |
| `AppSettingsRepository` | 3787 | 앱 설정 (대시보드 토글 등) |
| `FailReasonRepository` | 3836 | 발주 실패 사유 추적 |

### 스키마 마이그레이션

```python
# src/config/constants.py
DB_SCHEMA_VERSION = 18  # 현재 버전

# src/db/models.py - SCHEMA_MIGRATIONS
# 새 마이그레이션 추가 시:
# 1. constants.py의 DB_SCHEMA_VERSION += 1
# 2. models.py의 SCHEMA_MIGRATIONS[새버전] = "ALTER TABLE ... / CREATE TABLE ..."
```

### 마이그레이션 이력 (v9~v27)

| 버전 | 변경 내용 |
|:----:|----------|
| v9 | `eval_outcomes` + `calibration_history` 테이블 (사전평가+사후보정) |
| v10 | `realtime_inventory.is_cut_item` 컬럼 (발주중지 플래그) |
| v11 | `inventory_batches` 테이블 (FIFO 폐기 배치 관리) |
| v12 | `daily_sales.promo_type` 컬럼 (행사 타입 역보정) |
| v13 | `product_details.sell_price`, `margin_rate` 컬럼 |
| v14 | `eval_outcomes` ML 컬럼 확장 (과잉발주 보정용 10개 필드) |
| v15 | `auto_order_items` 테이블 (자동발주 상품 캐시) |
| v16 | `smart_order_items` 테이블 (스마트발주 상품 캐시) |
| v17 | `app_settings` 테이블 (대시보드 토글 설정) |
| v18 | `order_fail_reasons` 테이블 (발주 실패 사유 추적) |
| v19~v21 | DB 분할 준비 (common.db + stores/{id}.db) |
| v22 | `order_tracking`, `collection_logs`에 store_id 추가 |
| v23 | `stores`, `store_eval_params` 테이블 |
| v24 | `realtime_inventory`에 store_id 추가 |
| v25 | `inventory_batches`에 store_id 추가 |
| v26 | 9개 테이블 store_id 일괄 추가 (prediction_logs, order_history, eval_outcomes, promotions, promotion_stats, promotion_changes, receiving_history, auto_order_items, smart_order_items) |
| v27 | v26 불완전 마이그레이션 수리 (eval_outcomes/promotions UNIQUE 재생성, prediction_logs 인덱스) |
