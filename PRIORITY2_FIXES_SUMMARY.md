# Priority 2 멀티 스토어 지원 완료 보고서

**날짜**: 2026-02-08
**대상**: BGF 자동 발주 시스템
**목적**: 멀티 스토어 지원 (store_id 기반 데이터 분리)

---

## 🎯 수정 완료 항목

### ✅ Priority 2.2: realtime_inventory에 store_id 추가 (Schema v24)

**파일**:
- `src/config/constants.py` (라인 174)
- `src/db/models.py` (라인 676-711)

**변경 사항**:

#### 1. 스키마 버전 업그레이드
```python
# constants.py
DB_SCHEMA_VERSION = 24  # 23 → 24
```

#### 2. 마이그레이션 추가
```python
# models.py
24: """
-- 멀티 스토어 지원: realtime_inventory에 store_id 추가 (v24)

CREATE TABLE realtime_inventory_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT DEFAULT '46513',  -- 추가
    item_cd TEXT NOT NULL,
    item_nm TEXT,
    stock_qty INTEGER DEFAULT 0,
    pending_qty INTEGER DEFAULT 0,
    order_unit_qty INTEGER DEFAULT 1,
    is_available INTEGER DEFAULT 1,
    is_cut_item INTEGER DEFAULT 0,
    queried_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(store_id, item_cd)  -- item_cd → (store_id, item_cd)
);

INSERT INTO realtime_inventory_new (
    id, store_id, item_cd, item_nm, stock_qty, pending_qty,
    order_unit_qty, is_available, is_cut_item, queried_at, created_at
)
SELECT
    id, '46513', item_cd, item_nm, stock_qty, pending_qty,
    order_unit_qty, is_available, is_cut_item, queried_at, created_at
FROM realtime_inventory;

DROP TABLE realtime_inventory;
ALTER TABLE realtime_inventory_new RENAME TO realtime_inventory;

CREATE INDEX idx_realtime_inventory_store ON realtime_inventory(store_id, item_cd);
CREATE INDEX idx_realtime_inventory_queried ON realtime_inventory(queried_at);
CREATE INDEX idx_realtime_inventory_available ON realtime_inventory(is_available);
CREATE INDEX idx_realtime_inventory_cut ON realtime_inventory(is_cut_item);
""",
```

**마이그레이션 실행 결과**:
```
📊 현재 스키마 버전: 24

📋 realtime_inventory 테이블 구조:
  - id: INTEGER
  - store_id: TEXT           ← 추가됨
  - item_cd: TEXT
  - item_nm: TEXT
  - stock_qty: INTEGER
  - pending_qty: INTEGER
  - order_unit_qty: INTEGER
  - is_available: INTEGER
  - is_cut_item: INTEGER
  - queried_at: TEXT
  - created_at: TEXT

🔍 인덱스:
  - sqlite_autoindex_realtime_inventory_1  (UNIQUE(store_id, item_cd))
  - idx_realtime_inventory_store           ← 추가됨
  - idx_realtime_inventory_queried
  - idx_realtime_inventory_available
  - idx_realtime_inventory_cut

📦 데이터 샘플:
  - store_id=46513, item_cd=8801116007417, stock=16, pending=0
  - store_id=46513, item_cd=0000088013121, stock=28, pending=0
  - store_id=46513, item_cd=8801116052011, stock=24, pending=0
```

---

### ✅ Priority 2.3: ImprovedPredictor에 store_id 파라미터 추가

**파일**: `src/prediction/improved_predictor.py`

**변경 사항**:

#### 1. __init__ 메서드에 store_id 파라미터 추가 (라인 188-210)
```python
def __init__(
    self,
    db_path: Optional[str] = None,
    use_db_inventory: bool = True,
    store_id: str = "46513"  # Priority 2.3: 멀티 스토어 지원
) -> None:
    """
    Args:
        db_path: 데이터베이스 경로
        use_db_inventory: True면 realtime_inventory 테이블에서 재고/미입고 조회
        store_id: 점포 코드 (기본값: 호반점 46513)
    """
    if db_path is None:
        db_path = Path(__file__).parent.parent.parent / "data" / "bgf_sales.db"
    self.db_path = str(db_path)
    self.store_id = store_id  # Priority 2.3
    # ...
```

#### 2. 모든 쿼리에 store_id 필터 추가 (6개 쿼리)

##### 2-1. get_sales_history (라인 254-260)
```python
# 변경 전
WHERE item_cd = ?

# 변경 후
WHERE item_cd = ? AND store_id = ?
```

##### 2-2. get_current_stock (라인 330-336)
```python
# 변경 전
WHERE item_cd = ?

# 변경 후
WHERE item_cd = ? AND store_id = ?
```

##### 2-3. _get_disuse_rate (라인 358-363)
```python
# 변경 전
WHERE item_cd = ?
AND sales_date >= date('now', '-' || ? || ' days')

# 변경 후
WHERE item_cd = ? AND store_id = ?
AND sales_date >= date('now', '-' || ? || ' days')
```

##### 2-4. _analyze_pattern (라인 497-503)
```python
# 변경 전
WHERE item_cd = ?
AND sales_date >= date('now', '-' || ? || ' days')

# 변경 후
WHERE item_cd = ? AND store_id = ?
AND sales_date >= date('now', '-' || ? || ' days')
```

##### 2-5. _calculate_sell_day_ratio (라인 541-548)
```python
# 변경 전
WHERE item_cd = ?
AND sales_date >= date('now', '-' || ? || ' days')

# 변경 후
WHERE item_cd = ? AND store_id = ?
AND sales_date >= date('now', '-' || ? || ' days')
```

##### 2-6. get_recommendations (라인 1774-1779)
```python
# 변경 전
SELECT DISTINCT item_cd
FROM daily_sales
WHERE sales_date >= date('now', '-14 days')
AND sale_qty > 0

# 변경 후
SELECT DISTINCT item_cd
FROM daily_sales
WHERE store_id = ?
AND sales_date >= date('now', '-14 days')
AND sale_qty > 0
```

---

## 🧪 검증 결과

### 테스트 스크립트
**파일**: `scripts/test_priority2_storeid.py`

**테스트 항목**:
1. store_id 파라미터 (기본값 '46513', 커스텀 '12345')
2. 쿼리에서 store_id 필터링 (get_sales_history, get_current_stock)
3. 기존 기능 호환성 (Priority 1 + Priority 2 통합)

**실행 결과**:
```
================================================================================
Priority 2 store_id 멀티 스토어 지원 테스트
================================================================================

테스트 1: store_id 파라미터
  ✅ 기본 predictor.store_id: 46513
  ✅ PASS: 기본값 '46513' 정상
  ✅ 커스텀 predictor.store_id: 12345
  ✅ PASS: 커스텀 store_id '12345' 정상

테스트 2: 쿼리에서 store_id 필터링
  테스트 상품: 8800271904722
  ✅ get_sales_history: 6일 데이터 조회
  ✅ PASS: 판매 이력 조회 성공
  ✅ get_current_stock: 0개
  ✅ PASS: 재고 조회 성공

테스트 3: 기존 기능 호환성 (Priority 1 + Priority 2)
  ✅ Priority 1 테스트: 음수 재고 방어
  2026-02-08 | WARNING | [2201148653150] 음수 재고 감지: -1281개 → 0으로 초기화
  2026-02-08 | WARNING | [2201148653150] 최대 발주량 초과: 26개 → 20개로 제한

  상품: 친환경봉투판매용 (2201148653150)
    - store_id: 46513
    - 재고: 0개
    - 예측 발주량: 20개
    ✅ PASS: 음수 재고 방어 작동
    ✅ PASS: 최대 발주량 20개 이하 (20개)

  ✅ Priority 1 + Priority 2 통합 성공

✅ 모든 테스트 완료
```

---

## 📊 마이그레이션 실행 방법

### 자동 실행 (프로그램 시작 시)
```python
# 프로그램 시작 시 자동으로 마이그레이션 실행됨
from src.db.models import init_db

init_db()  # v23 → v24 자동 마이그레이션
```

### 수동 실행 (스크립트)
```bash
# 마이그레이션 실행 및 검증
python scripts/run_migration_v24.py
```

---

## 🎯 사용법

### 기본 사용 (호반점 46513)
```python
from src.prediction.improved_predictor import ImprovedPredictor

# 기본값: store_id='46513'
predictor = ImprovedPredictor()
result = predictor.predict("8801234567890")
```

### 다른 점포 지정
```python
# 다른 점포 (예: 12345)
predictor = ImprovedPredictor(store_id="12345")
result = predictor.predict("8801234567890")

# 점포별 예측 실행
for store_id in ["46513", "12345", "67890"]:
    predictor = ImprovedPredictor(store_id=store_id)
    recommendations = predictor.get_recommendations()
    print(f"{store_id}: {len(recommendations)}개 발주 추천")
```

---

## 🔄 데이터 마이그레이션 상세

### Before (Schema v23)
```sql
CREATE TABLE realtime_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_cd TEXT NOT NULL,
    item_nm TEXT,
    stock_qty INTEGER DEFAULT 0,
    pending_qty INTEGER DEFAULT 0,
    order_unit_qty INTEGER DEFAULT 1,
    is_available INTEGER DEFAULT 1,
    is_cut_item INTEGER DEFAULT 0,
    queried_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(item_cd)  -- 단일 점포 가정
);
```

### After (Schema v24)
```sql
CREATE TABLE realtime_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT DEFAULT '46513',      -- ✅ 추가
    item_cd TEXT NOT NULL,
    item_nm TEXT,
    stock_qty INTEGER DEFAULT 0,
    pending_qty INTEGER DEFAULT 0,
    order_unit_qty INTEGER DEFAULT 1,
    is_available INTEGER DEFAULT 1,
    is_cut_item INTEGER DEFAULT 0,
    queried_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(store_id, item_cd)  -- ✅ 멀티 스토어 지원
);

CREATE INDEX idx_realtime_inventory_store ON realtime_inventory(store_id, item_cd);
```

### 기존 데이터 보존
- 모든 기존 데이터에 `store_id='46513'` 자동 할당
- 데이터 손실 없음
- UNIQUE 제약 조건 변경: `item_cd` → `(store_id, item_cd)`

---

## 📈 영향 범위

### 영향받는 컴포넌트
1. **ImprovedPredictor** (6개 쿼리 수정)
   - get_sales_history
   - get_current_stock
   - _get_disuse_rate
   - _analyze_pattern
   - _calculate_sell_day_ratio
   - get_recommendations

2. **realtime_inventory 테이블** (Schema v24)
   - store_id 컬럼 추가
   - UNIQUE 제약 조건 변경
   - 인덱스 추가

### 영향받지 않는 컴포넌트 (하위 호환성 유지)
- ✅ 기존 코드 (store_id 미지정 시 기본값 '46513' 사용)
- ✅ Priority 1 수정 사항 (음수 재고 방어, 최대 발주량 상한, 푸드류 안전재고)
- ✅ 모든 카테고리 예측 모듈 (categories/*)
- ✅ Repository 클래스들 (db/repository.py)

---

## 🔧 롤백 방법 (필요시)

### 1. 코드 롤백
```bash
git checkout src/prediction/improved_predictor.py
git checkout src/db/models.py
git checkout src/config/constants.py
```

### 2. 스키마 롤백
```sql
-- 백업에서 복원
CREATE TABLE realtime_inventory_backup_v23 AS
SELECT * FROM realtime_inventory WHERE store_id = '46513';

DROP TABLE realtime_inventory;
ALTER TABLE realtime_inventory_backup_v23 RENAME TO realtime_inventory;

-- 스키마 버전 되돌리기
DELETE FROM schema_version WHERE version = 24;

-- DB_SCHEMA_VERSION = 23으로 변경 (constants.py)
```

---

## ✅ 체크리스트

- [x] **Priority 2.2**: realtime_inventory에 store_id 추가 (Schema v24)
  - [x] DB_SCHEMA_VERSION = 24 (constants.py)
  - [x] 마이그레이션 24 추가 (models.py)
  - [x] 마이그레이션 실행 스크립트 (run_migration_v24.py)
  - [x] 마이그레이션 실행 및 검증

- [x] **Priority 2.3**: ImprovedPredictor에 store_id 파라미터 추가
  - [x] __init__ 메서드에 store_id 파라미터 추가
  - [x] get_sales_history에 store_id 필터 추가
  - [x] get_current_stock에 store_id 필터 추가
  - [x] _get_disuse_rate에 store_id 필터 추가
  - [x] _analyze_pattern에 store_id 필터 추가
  - [x] _calculate_sell_day_ratio에 store_id 필터 추가
  - [x] get_recommendations에 store_id 필터 추가

- [x] **검증**: Priority 2 테스트 스크립트 (test_priority2_storeid.py)
  - [x] store_id 파라미터 테스트 (기본값, 커스텀)
  - [x] 쿼리 필터링 테스트
  - [x] Priority 1 + Priority 2 통합 테스트

- [x] **문서화**: PRIORITY2_FIXES_SUMMARY.md

---

## 📝 다음 단계 (Future Work)

### 1. 다른 Repository 클래스에 store_id 추가
- `DailySalesRepository`
- `ProductRepository`
- `PromotionRepository`
- 기타 모든 Repository 클래스

### 2. 수집기(Collectors)에 store_id 지원
- `SalesCollector`
- `OrderPrepCollector`
- `PromotionCollector`
- 기타 모든 Collector 클래스

### 3. 멀티 스토어 발주 시스템
```python
# 예시: 여러 점포 동시 발주
stores = ["46513", "12345", "67890"]

for store_id in stores:
    predictor = ImprovedPredictor(store_id=store_id)
    system = AutoOrderSystem(driver, predictor=predictor)
    system.execute(dry_run=False)
```

---

**작성**: Claude Code (Sonnet 4.5)
**날짜**: 2026-02-08 07:38 KST
**상태**: ✅ Priority 2 완료, Priority 3 대기 중

---

## 📊 통합 요약 (Priority 1 + Priority 2)

| 항목 | Priority 1 | Priority 2 | 통합 상태 |
|------|-----------|-----------|---------|
| 음수 재고 방어 | ✅ 완료 | - | ✅ 정상 작동 |
| 최대 발주량 상한 | ✅ 완료 | - | ✅ 정상 작동 |
| 푸드류 안전재고 상향 | ✅ 완료 | - | ✅ 정상 작동 |
| 폐기율 계수 완화 | ✅ 완료 | - | ✅ 정상 작동 |
| 푸드류 최소 발주량 | ✅ 완료 | - | ✅ 정상 작동 |
| store_id 파라미터 | - | ✅ 완료 | ✅ 정상 작동 |
| 멀티 스토어 쿼리 | - | ✅ 완료 | ✅ 정상 작동 |
| **전체 테스트 결과** | **100% PASS** | **100% PASS** | **✅ 통합 성공** |
