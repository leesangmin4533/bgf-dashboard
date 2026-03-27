# Design: 소분류 내 상품 대체/잠식(Cannibalization) 감지

> **Feature**: substitution-detect
> **Created**: 2026-03-01
> **Status**: Draft
> **Plan**: [substitution-detect.plan.md](../../01-plan/features/substitution-detect.plan.md)

---

## 1. 개요

같은 소분류(small_cd) 내 상품 간 수요 이동(잠식)을 자동 감지하여 발주량을 조정하는 모듈.

---

## 2. 상수 정의 (`src/settings/constants.py`)

```python
# 소분류 내 상품 대체/잠식 감지
# =====================================================================
DB_SCHEMA_VERSION = 49  # v49: substitution_events 테이블

# 잠식 감지 파라미터
SUBSTITUTION_LOOKBACK_DAYS = 30           # 분석 기간 (일)
SUBSTITUTION_RECENT_WINDOW = 14           # 최근 기간 (일) — 이동평균 비교 윈도우
SUBSTITUTION_DECLINE_THRESHOLD = 0.7      # 감소 판정 임계값 (recent/prior < 0.7)
SUBSTITUTION_GROWTH_THRESHOLD = 1.3       # 증가 판정 임계값 (recent/prior > 1.3)
SUBSTITUTION_TOTAL_CHANGE_LIMIT = 0.20    # 소분류 총량 변화 한도 (20%)
SUBSTITUTION_MIN_DAILY_AVG = 0.3          # 최소 일평균 (이하면 분석 제외)
SUBSTITUTION_MIN_ITEMS_IN_GROUP = 2       # 소분류 내 최소 상품 수

# 잠식 발주 감소 계수
SUBSTITUTION_COEF_MILD = 0.9             # 감소율 30~50%
SUBSTITUTION_COEF_MODERATE = 0.8         # 감소율 50~70%
SUBSTITUTION_COEF_SEVERE = 0.7           # 감소율 70% 이상

# 잠식 피드백 유효기간 (일)
SUBSTITUTION_FEEDBACK_EXPIRY_DAYS = 14
```

**Check Item Count**: 12 constants

---

## 3. DB 스키마

### 3.1 substitution_events 테이블 (store DB)

```sql
CREATE TABLE IF NOT EXISTS substitution_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    detection_date TEXT NOT NULL,
    small_cd TEXT NOT NULL,
    small_nm TEXT,
    gainer_item_cd TEXT NOT NULL,
    gainer_item_nm TEXT,
    gainer_prior_avg REAL,
    gainer_recent_avg REAL,
    gainer_growth_rate REAL,
    loser_item_cd TEXT NOT NULL,
    loser_item_nm TEXT,
    loser_prior_avg REAL,
    loser_recent_avg REAL,
    loser_decline_rate REAL,
    adjustment_coefficient REAL NOT NULL DEFAULT 1.0,
    total_change_rate REAL,
    confidence REAL DEFAULT 0.0,
    is_active INTEGER DEFAULT 1,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(store_id, detection_date, loser_item_cd, gainer_item_cd)
);
CREATE INDEX IF NOT EXISTS idx_substitution_store_date
    ON substitution_events(store_id, detection_date);
CREATE INDEX IF NOT EXISTS idx_substitution_loser
    ON substitution_events(loser_item_cd, is_active);
CREATE INDEX IF NOT EXISTS idx_substitution_small_cd
    ON substitution_events(small_cd);
```

**Check Items**: 1 table, 3 indexes, 18 columns

### 3.2 SCHEMA_MIGRATIONS v49 (`src/db/models.py`)

```python
49: """
-- v49: substitution_events 테이블 (소분류 내 잠식 감지)
CREATE TABLE IF NOT EXISTS substitution_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    detection_date TEXT NOT NULL,
    small_cd TEXT NOT NULL,
    small_nm TEXT,
    gainer_item_cd TEXT NOT NULL,
    gainer_item_nm TEXT,
    gainer_prior_avg REAL,
    gainer_recent_avg REAL,
    gainer_growth_rate REAL,
    loser_item_cd TEXT NOT NULL,
    loser_item_nm TEXT,
    loser_prior_avg REAL,
    loser_recent_avg REAL,
    loser_decline_rate REAL,
    adjustment_coefficient REAL NOT NULL DEFAULT 1.0,
    total_change_rate REAL,
    confidence REAL DEFAULT 0.0,
    is_active INTEGER DEFAULT 1,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(store_id, detection_date, loser_item_cd, gainer_item_cd)
);
CREATE INDEX IF NOT EXISTS idx_substitution_store_date
    ON substitution_events(store_id, detection_date);
CREATE INDEX IF NOT EXISTS idx_substitution_loser
    ON substitution_events(loser_item_cd, is_active);
CREATE INDEX IF NOT EXISTS idx_substitution_small_cd
    ON substitution_events(small_cd);
""",
```

### 3.3 STORE_SCHEMA 추가 (`src/infrastructure/database/schema.py`)

동일한 CREATE TABLE + INDEX 문을 STORE_SCHEMA 리스트에 추가.

**Check Items**: 2 files modified (models.py, schema.py)

---

## 4. SubstitutionEventRepository (`src/infrastructure/database/repos/substitution_repo.py`)

```python
class SubstitutionEventRepository(BaseRepository):
    """소분류 내 잠식 이벤트 Repository"""
    db_type = "store"
```

### 4.1 메서드

| # | 메서드 | 시그니처 | 설명 |
|---|--------|----------|------|
| 1 | `upsert_event` | `(self, record: dict) -> None` | 잠식 이벤트 UPSERT |
| 2 | `get_active_events` | `(self, item_cd: str, as_of_date: str) -> List[dict]` | 활성 잠식 이벤트 조회 |
| 3 | `get_active_events_batch` | `(self, item_cds: List[str], as_of_date: str) -> Dict[str, List[dict]]` | 배치 조회 |
| 4 | `expire_old_events` | `(self, as_of_date: str) -> int` | 만료 이벤트 비활성화 |
| 5 | `get_events_by_small_cd` | `(self, small_cd: str, days: int = 30) -> List[dict]` | 소분류별 이벤트 조회 |

**Check Items**: 5 methods

### 4.2 upsert_event 상세

```python
def upsert_event(self, record: dict) -> None:
    """잠식 이벤트 UPSERT (store_id+detection_date+loser+gainer 유니크)"""
    conn = self._get_conn()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO substitution_events
            (store_id, detection_date, small_cd, small_nm,
             gainer_item_cd, gainer_item_nm, gainer_prior_avg, gainer_recent_avg, gainer_growth_rate,
             loser_item_cd, loser_item_nm, loser_prior_avg, loser_recent_avg, loser_decline_rate,
             adjustment_coefficient, total_change_rate, confidence,
             is_active, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record["store_id"], record["detection_date"],
            record["small_cd"], record.get("small_nm"),
            record["gainer_item_cd"], record.get("gainer_item_nm"),
            record.get("gainer_prior_avg"), record.get("gainer_recent_avg"),
            record.get("gainer_growth_rate"),
            record["loser_item_cd"], record.get("loser_item_nm"),
            record.get("loser_prior_avg"), record.get("loser_recent_avg"),
            record.get("loser_decline_rate"),
            record.get("adjustment_coefficient", 1.0),
            record.get("total_change_rate"),
            record.get("confidence", 0.0),
            record.get("is_active", 1),
            record.get("expires_at"),
            record.get("created_at", datetime.now().isoformat()),
        ))
        conn.commit()
    finally:
        conn.close()
```

### 4.3 get_active_events 상세

```python
def get_active_events(self, item_cd: str, as_of_date: str) -> List[dict]:
    """item_cd가 loser인 활성 잠식 이벤트 조회"""
    conn = self._get_conn()
    try:
        rows = conn.execute("""
            SELECT * FROM substitution_events
            WHERE loser_item_cd = ? AND is_active = 1
              AND (expires_at IS NULL OR expires_at >= ?)
            ORDER BY detection_date DESC
        """, (item_cd, as_of_date)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
```

### 4.4 get_active_events_batch 상세

```python
def get_active_events_batch(self, item_cds: List[str], as_of_date: str) -> Dict[str, List[dict]]:
    """여러 상품의 활성 잠식 이벤트 배치 조회"""
    result = {ic: [] for ic in item_cds}
    if not item_cds:
        return result
    conn = self._get_conn()
    try:
        placeholders = ",".join("?" * len(item_cds))
        rows = conn.execute(f"""
            SELECT * FROM substitution_events
            WHERE loser_item_cd IN ({placeholders}) AND is_active = 1
              AND (expires_at IS NULL OR expires_at >= ?)
            ORDER BY detection_date DESC
        """, (*item_cds, as_of_date)).fetchall()
        for r in rows:
            d = dict(r)
            result[d["loser_item_cd"]].append(d)
        return result
    finally:
        conn.close()
```

### 4.5 expire_old_events 상세

```python
def expire_old_events(self, as_of_date: str) -> int:
    """만료일 지난 이벤트 비활성화"""
    conn = self._get_conn()
    try:
        cursor = conn.execute("""
            UPDATE substitution_events
            SET is_active = 0
            WHERE is_active = 1 AND expires_at IS NOT NULL AND expires_at < ?
        """, (as_of_date,))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
```

### 4.6 get_events_by_small_cd 상세

```python
def get_events_by_small_cd(self, small_cd: str, days: int = 30) -> List[dict]:
    """소분류별 최근 이벤트 조회"""
    conn = self._get_conn()
    try:
        rows = conn.execute("""
            SELECT * FROM substitution_events
            WHERE small_cd = ? AND detection_date >= date('now', ?)
            ORDER BY detection_date DESC
        """, (small_cd, f"-{days} days")).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
```

---

## 5. SubstitutionDetector (`src/analysis/substitution_detector.py`)

### 5.1 클래스 구조

```python
class SubstitutionDetector:
    """소분류 내 상품 대체/잠식 감지기"""

    def __init__(self, store_id: str) -> None:
        self.store_id = store_id
        self.repo = SubstitutionEventRepository(store_id=store_id)
```

### 5.2 메서드

| # | 메서드 | 시그니처 | 설명 |
|---|--------|----------|------|
| 1 | `detect_all` | `(self, target_date: str) -> dict` | 전체 소분류 잠식 분석 |
| 2 | `detect_cannibalization` | `(self, small_cd: str, target_date: str, days: int = 30) -> List[dict]` | 단일 소분류 잠식 감지 |
| 3 | `_get_items_in_small_cd` | `(self, small_cd: str) -> List[dict]` | 소분류 내 상품 조회 |
| 4 | `_get_sales_data` | `(self, item_cd: str, start_date: str, end_date: str) -> List[dict]` | 판매 데이터 조회 |
| 5 | `_calculate_moving_averages` | `(self, sales: List[dict], recent_days: int) -> Tuple[float, float]` | 전반/후반 이동평균 계산 |
| 6 | `_classify_items` | `(self, items_with_avg: List[dict]) -> Tuple[List[dict], List[dict]]` | gainer/loser 분류 |
| 7 | `_compute_adjustment_coefficient` | `(self, decline_rate: float) -> float` | 감소율 -> 조정 계수 |
| 8 | `_calculate_confidence` | `(self, gainer: dict, loser: dict, total_change_rate: float) -> float` | 신뢰도 계산 |
| 9 | `get_adjustment` | `(self, item_cd: str) -> float` | 상품의 활성 잠식 계수 조회 |
| 10 | `preload` | `(self, item_cds: List[str]) -> None` | 배치 프리로드 |

**Check Items**: 10 methods

### 5.3 detect_all 상세

```python
def detect_all(self, target_date: str) -> dict:
    """전체 소분류에 대해 잠식 분석

    Args:
        target_date: 분석 기준일 (YYYY-MM-DD)
    Returns:
        {"analyzed_groups": N, "events_detected": N, "by_small_cd": {...}, "errors": [...]}
    """
    # 1) 만료 이벤트 정리
    self.repo.expire_old_events(target_date)

    # 2) 판매 데이터가 있는 소분류 목록 조회
    small_cds = self._get_active_small_cds(target_date)

    analyzed = 0
    detected = 0
    by_small_cd = {}
    errors = []

    for small_cd in small_cds:
        try:
            events = self.detect_cannibalization(small_cd, target_date)
            analyzed += 1
            if events:
                detected += len(events)
                by_small_cd[small_cd] = len(events)
        except Exception as e:
            errors.append(f"{small_cd}: {e}")
            logger.debug(f"잠식 감지 실패 ({small_cd}): {e}")

    return {
        "analyzed_groups": analyzed,
        "events_detected": detected,
        "by_small_cd": by_small_cd,
        "errors": errors,
    }
```

### 5.4 detect_cannibalization 상세

```python
def detect_cannibalization(
    self, small_cd: str, target_date: str,
    days: int = SUBSTITUTION_LOOKBACK_DAYS
) -> List[dict]:
    """특정 소분류 내 잠식 감지

    Args:
        small_cd: 소분류 코드
        target_date: 기준일
        days: 분석 기간 (기본 30일)
    Returns:
        감지된 잠식 이벤트 목록
    """
    items = self._get_items_in_small_cd(small_cd)
    if len(items) < SUBSTITUTION_MIN_ITEMS_IN_GROUP:
        return []

    # 날짜 계산
    end_date = target_date
    start_date = (datetime.strptime(target_date, "%Y-%m-%d")
                  - timedelta(days=days)).strftime("%Y-%m-%d")

    # 상품별 이동평균 계산
    items_with_avg = []
    for item in items:
        sales = self._get_sales_data(item["item_cd"], start_date, end_date)
        prior_avg, recent_avg = self._calculate_moving_averages(
            sales, SUBSTITUTION_RECENT_WINDOW
        )
        if prior_avg < SUBSTITUTION_MIN_DAILY_AVG:
            continue  # 판매 미미한 상품 제외
        ratio = recent_avg / prior_avg if prior_avg > 0 else 1.0
        items_with_avg.append({
            **item,
            "prior_avg": prior_avg,
            "recent_avg": recent_avg,
            "ratio": ratio,
        })

    # 소분류 총량 변화 확인
    total_prior = sum(i["prior_avg"] for i in items_with_avg)
    total_recent = sum(i["recent_avg"] for i in items_with_avg)
    if total_prior > 0:
        total_change_rate = abs(total_recent - total_prior) / total_prior
    else:
        total_change_rate = 0.0

    if total_change_rate > SUBSTITUTION_TOTAL_CHANGE_LIMIT:
        return []  # 총량 자체가 변화 -> 잠식이 아닌 외부 요인

    # gainer/loser 분류
    gainers, losers = self._classify_items(items_with_avg)

    if not gainers or not losers:
        return []

    # 이벤트 생성
    events = []
    for loser in losers:
        for gainer in gainers:
            decline_rate = loser["ratio"]  # < 1.0
            coef = self._compute_adjustment_coefficient(decline_rate)
            confidence = self._calculate_confidence(
                gainer, loser, total_change_rate
            )
            expires_at = (
                datetime.strptime(target_date, "%Y-%m-%d")
                + timedelta(days=SUBSTITUTION_FEEDBACK_EXPIRY_DAYS)
            ).strftime("%Y-%m-%d")

            record = {
                "store_id": self.store_id,
                "detection_date": target_date,
                "small_cd": small_cd,
                "small_nm": loser.get("small_nm"),
                "gainer_item_cd": gainer["item_cd"],
                "gainer_item_nm": gainer.get("item_nm"),
                "gainer_prior_avg": round(gainer["prior_avg"], 2),
                "gainer_recent_avg": round(gainer["recent_avg"], 2),
                "gainer_growth_rate": round(gainer["ratio"], 3),
                "loser_item_cd": loser["item_cd"],
                "loser_item_nm": loser.get("item_nm"),
                "loser_prior_avg": round(loser["prior_avg"], 2),
                "loser_recent_avg": round(loser["recent_avg"], 2),
                "loser_decline_rate": round(decline_rate, 3),
                "adjustment_coefficient": coef,
                "total_change_rate": round(total_change_rate, 3),
                "confidence": confidence,
                "is_active": 1,
                "expires_at": expires_at,
            }
            self.repo.upsert_event(record)
            events.append(record)

    return events
```

### 5.5 _get_items_in_small_cd 상세

```python
def _get_items_in_small_cd(self, small_cd: str) -> List[dict]:
    """소분류 내 상품 목록 조회 (common DB product_details + products)"""
    conn = DBRouter.get_common_connection()
    try:
        rows = conn.execute("""
            SELECT pd.item_cd, p.item_nm, p.mid_cd,
                   pd.small_cd, pd.small_nm
            FROM product_details pd
            JOIN products p ON pd.item_cd = p.item_cd
            WHERE pd.small_cd = ?
        """, (small_cd,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
```

### 5.6 _get_sales_data 상세

```python
def _get_sales_data(
    self, item_cd: str, start_date: str, end_date: str
) -> List[dict]:
    """판매 데이터 조회 (store DB daily_sales)"""
    conn = DBRouter.get_store_connection(self.store_id)
    try:
        rows = conn.execute("""
            SELECT sales_date, sale_qty
            FROM daily_sales
            WHERE store_id = ? AND item_cd = ?
              AND sales_date >= ? AND sales_date <= ?
            ORDER BY sales_date
        """, (self.store_id, item_cd, start_date, end_date)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
```

### 5.7 _calculate_moving_averages 상세

```python
def _calculate_moving_averages(
    self, sales: List[dict], recent_days: int
) -> Tuple[float, float]:
    """판매 데이터를 전반/후반으로 나누어 일평균 계산

    Args:
        sales: 판매 데이터 리스트 (날짜 순)
        recent_days: 최근 윈도우 크기
    Returns:
        (prior_avg, recent_avg)
    """
    if not sales:
        return 0.0, 0.0

    # 날짜 기준으로 분할
    all_dates = sorted(set(s["sales_date"] for s in sales))
    if len(all_dates) < recent_days:
        # 데이터 부족 시 전체를 반반
        mid = len(all_dates) // 2
        if mid == 0:
            total = sum(s["sale_qty"] or 0 for s in sales)
            avg = total / max(len(all_dates), 1)
            return avg, avg
        split_date = all_dates[mid]
    else:
        split_date = all_dates[-recent_days]

    prior_sales = [s for s in sales if s["sales_date"] < split_date]
    recent_sales = [s for s in sales if s["sales_date"] >= split_date]

    prior_total = sum(s["sale_qty"] or 0 for s in prior_sales)
    recent_total = sum(s["sale_qty"] or 0 for s in recent_sales)

    prior_days = max(len(set(s["sales_date"] for s in prior_sales)), 1)
    recent_days_actual = max(len(set(s["sales_date"] for s in recent_sales)), 1)

    return prior_total / prior_days, recent_total / recent_days_actual
```

### 5.8 _classify_items 상세

```python
def _classify_items(
    self, items_with_avg: List[dict]
) -> Tuple[List[dict], List[dict]]:
    """상품을 gainer(수요 증가)와 loser(수요 감소)로 분류

    Returns:
        (gainers, losers)
    """
    gainers = [i for i in items_with_avg
               if i["ratio"] >= SUBSTITUTION_GROWTH_THRESHOLD]
    losers = [i for i in items_with_avg
              if i["ratio"] <= SUBSTITUTION_DECLINE_THRESHOLD]
    return gainers, losers
```

### 5.9 _compute_adjustment_coefficient 상세

```python
def _compute_adjustment_coefficient(self, decline_rate: float) -> float:
    """감소율 기반 발주 조정 계수 계산

    Args:
        decline_rate: recent/prior 비율 (예: 0.5 = 50%로 감소)
    Returns:
        조정 계수 (0.7 ~ 1.0)
    """
    if decline_rate <= 0.3:
        return SUBSTITUTION_COEF_SEVERE    # 0.7
    elif decline_rate <= 0.5:
        return SUBSTITUTION_COEF_MODERATE  # 0.8
    elif decline_rate <= 0.7:
        return SUBSTITUTION_COEF_MILD      # 0.9
    else:
        return 1.0
```

### 5.10 _calculate_confidence 상세

```python
def _calculate_confidence(
    self, gainer: dict, loser: dict, total_change_rate: float
) -> float:
    """잠식 판정 신뢰도 계산

    기준:
    - 총량 변화가 작을수록 높음 (잠식에 의한 이동)
    - gainer 증가율과 loser 감소율이 클수록 높음
    """
    # 총량 안정성: 0~20% 변화 -> 1.0~0.5
    stability = max(0.5, 1.0 - total_change_rate * 2.5)

    # 변화 강도: gainer 증가율 + loser 감소량
    growth_strength = min(1.0, (gainer["ratio"] - 1.0) / 1.0)  # 0~1
    decline_strength = min(1.0, (1.0 - loser["ratio"]) / 0.7)  # 0~1

    confidence = stability * 0.4 + growth_strength * 0.3 + decline_strength * 0.3
    return round(min(1.0, max(0.0, confidence)), 2)
```

### 5.11 get_adjustment 상세

```python
def get_adjustment(self, item_cd: str) -> float:
    """상품의 활성 잠식 조정 계수 조회

    Args:
        item_cd: 상품 코드
    Returns:
        조정 계수 (0.7~1.0, 잠식 없으면 1.0)
    """
    if item_cd in self._cache:
        return self._cache[item_cd]

    if self._preloaded:
        return self._cache.get(item_cd, 1.0)

    today = datetime.now().strftime("%Y-%m-%d")
    events = self.repo.get_active_events(item_cd, today)
    if not events:
        self._cache[item_cd] = 1.0
        return 1.0

    # 가장 낮은(보수적) 계수 사용
    coef = min(e.get("adjustment_coefficient", 1.0) for e in events)
    self._cache[item_cd] = coef
    return coef
```

### 5.12 preload 상세

```python
def preload(self, item_cds: List[str]) -> None:
    """여러 상품의 잠식 이벤트 배치 프리로드

    Args:
        item_cds: 상품 코드 목록
    """
    today = datetime.now().strftime("%Y-%m-%d")
    events_map = self.repo.get_active_events_batch(item_cds, today)

    for item_cd in item_cds:
        events = events_map.get(item_cd, [])
        if events:
            coef = min(e.get("adjustment_coefficient", 1.0) for e in events)
            self._cache[item_cd] = coef
        else:
            self._cache[item_cd] = 1.0

    self._preloaded = True
```

---

## 6. improved_predictor.py 통합

### 6.1 초기화 (`__init__`)

```python
# 잠식 감지 조정기 (lazy-load)
self._substitution_detector = None
```

**Check Item**: 1 attribute

### 6.2 lazy loader

```python
def _get_substitution_detector(self):
    """SubstitutionDetector lazy 로드"""
    if self._substitution_detector is None:
        try:
            from src.analysis.substitution_detector import SubstitutionDetector
            self._substitution_detector = SubstitutionDetector(store_id=self.store_id)
        except Exception as e:
            logger.debug(f"SubstitutionDetector 초기화 실패: {e}")
            self._substitution_detector = False  # sentinel
    return self._substitution_detector if self._substitution_detector is not False else None
```

**Check Items**: 1 method

### 6.3 predict_item 내 적용 위치

폐기 원인 피드백과 카테고리 max cap 사이에 삽입:

```python
        # 폐기 원인 피드백
        waste_fb = self._get_waste_feedback()
        # ... (기존 코드)

        # 소분류 내 잠식 계수 적용
        sub_detector = self._get_substitution_detector()
        if sub_detector and order_qty > 0:
            try:
                sub_coef = sub_detector.get_adjustment(item_cd)
                if sub_coef < 1.0:
                    old_qty = order_qty
                    order_qty = max(1, int(order_qty * sub_coef))
                    logger.info(
                        f"[잠식감지] {product['item_nm']}: "
                        f"{old_qty}->{order_qty} (계수={sub_coef:.2f})"
                    )
            except Exception as e:
                logger.debug(f"[잠식감지] 실패 ({item_cd}): {e}")

        # 카테고리별 최대 발주량 상한
        max_qty = MAX_ORDER_QTY_BY_CATEGORY.get(product["mid_cd"])
        # ...
```

**Check Items**: 1 block (잠식 계수 적용)

### 6.4 predict_all 내 프리로드

```python
        # 잠식 감지 프리로드 (DB 쿼리 1회)
        sub_detector = self._get_substitution_detector()
        if sub_detector:
            try:
                sub_detector.preload(item_codes)
            except Exception as e:
                logger.debug(f"SubstitutionDetector preload 실패: {e}")
```

**Check Items**: 1 block (프리로드)

---

## 7. repos/__init__.py 수정

```python
from .substitution_repo import SubstitutionEventRepository
```

`__all__` 리스트에 `"SubstitutionEventRepository"` 추가.

**Check Items**: 2 lines (import + __all__)

---

## 8. 테스트 계획 (`tests/test_substitution_detector.py`)

| # | 테스트 클래스 | 테스트 메서드 | 설명 |
|---|--------------|-------------|------|
| 1 | TestSubstitutionRepository | test_upsert_and_query | UPSERT + 조회 |
| 2 | TestSubstitutionRepository | test_batch_query | 배치 조회 |
| 3 | TestSubstitutionRepository | test_expire_old_events | 만료 처리 |
| 4 | TestSubstitutionRepository | test_events_by_small_cd | 소분류별 조회 |
| 5 | TestMovingAverage | test_basic_calculation | 기본 이동평균 계산 |
| 6 | TestMovingAverage | test_empty_sales | 빈 데이터 처리 |
| 7 | TestMovingAverage | test_short_data | 짧은 데이터 처리 |
| 8 | TestClassifyItems | test_gainer_loser_split | 정상 분류 |
| 9 | TestClassifyItems | test_no_gainer | gainer 없는 케이스 |
| 10 | TestClassifyItems | test_no_loser | loser 없는 케이스 |
| 11 | TestAdjustmentCoefficient | test_mild_decline | 경미한 감소 (30~50%) |
| 12 | TestAdjustmentCoefficient | test_moderate_decline | 중간 감소 (50~70%) |
| 13 | TestAdjustmentCoefficient | test_severe_decline | 심한 감소 (70%+) |
| 14 | TestAdjustmentCoefficient | test_no_decline | 감소 없음 |
| 15 | TestConfidence | test_high_confidence | 높은 신뢰도 |
| 16 | TestConfidence | test_low_confidence | 낮은 신뢰도 |
| 17 | TestDetectCannibalization | test_basic_cannibalization | 기본 잠식 감지 |
| 18 | TestDetectCannibalization | test_no_cannibalization_total_change | 총량 변화 시 비감지 |
| 19 | TestDetectCannibalization | test_insufficient_items | 상품 수 부족 시 스킵 |
| 20 | TestDetectCannibalization | test_low_sales_excluded | 저판매 상품 제외 |
| 21 | TestPredictorIntegration | test_substitution_coefficient_applied | 예측기 통합 계수 적용 |
| 22 | TestPredictorIntegration | test_preload_batch | 배치 프리로드 |

**Total: 22 tests**

---

## 9. 체크 아이템 요약

| Section | Items |
|---------|:-----:|
| 2. Constants | 12 |
| 3. DB Schema | 4 (1 table + 3 indexes) |
| 4. Repository | 5 methods |
| 5. SubstitutionDetector | 10 methods |
| 6. improved_predictor.py | 4 items |
| 7. repos/__init__.py | 2 lines |
| 8. Tests | 22 tests |
| **Total** | **59** |
