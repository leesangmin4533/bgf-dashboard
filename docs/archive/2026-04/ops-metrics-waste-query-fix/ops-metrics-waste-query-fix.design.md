# Design: ops_metrics waste_rate 쿼리 버그 수정 (ops-metrics-waste-query-fix)

> 작성일: 2026-04-07
> 상태: Design
> Plan: docs/01-plan/features/ops-metrics-waste-query-fix.plan.md
> 이슈체인: scheduling.md#ops-metrics-waste-rate-mid-cd-컬럼-부재

---

## 1. 설계 개요

**1-라인 수정 + 1-라인 연결 변경으로 완결.**

- `ops_metrics.py:_waste_rate()`에서 사용하는 커넥션을 `DBRouter.get_store_connection()` → `DBRouter.get_store_connection_with_common()`로 교체
- waste_slip_items 쿼리에 `JOIN common.products p ON wsi.item_cd = p.item_cd` 추가, `GROUP BY p.mid_cd`
- daily_sales 쿼리는 **수정 불필요** (daily_sales.mid_cd 컬럼 존재, schema.py:1060 인덱스 확인)

---

## 2. Design 결정 (Plan 구멍 5개 메우기)

### 결정 1: ATTACH 방식 — 기존 헬퍼 사용
`DBRouter.get_store_connection_with_common(store_id)` 사용 (connection.py:108-120).
- 이미 `ATTACH DATABASE '{common_path}' AS common` 실행하여 반환
- `attach_common_with_views()` 대안도 있으나, temp VIEW는 불필요 (직접 JOIN으로 충분)
- **근거**: 기존 패턴 재사용, 추가 import/래핑 없음

### 결정 2: JOIN 전략 — INNER JOIN
```sql
FROM waste_slip_items wsi
JOIN common.products p ON wsi.item_cd = p.item_cd
```
- products에 없는 item_cd(신제품 미동기화 등)는 **집계에서 제외**
- LEFT JOIN 하면 `mid_cd IS NULL` 바구니 생겨서 K2 결과 왜곡 (분자가 NULL 카테고리로 흘러가 해석 불가)
- 데이터 품질 영향을 경고 로그로 남김: 폐기 항목 중 products 매칭률이 95% 미만이면 `logger.warning`

### 결정 3: daily_sales 쿼리 상태 — 수정 불필요
- schema.py:1060 `CREATE INDEX idx_daily_sales_mid ON daily_sales(mid_cd)` 존재 → 컬럼 있음 확정
- 현재 실패가 첫 쿼리(waste_slip_items)에서 예외 → 두번째 쿼리(daily_sales) 도달 전 `except` 진입
- 첫 쿼리 수정하면 두번째 쿼리는 자연스럽게 정상 동작

### 결정 4: 회귀 테스트 — 3개 케이스
1. **정상 매칭**: waste_slip_items 3개 상품 + products 매핑 → categories 반환 확인
2. **products 미매칭 제외**: 5개 중 2개만 products에 존재 → 2개만 집계되고 경고 로그 발생
3. **빈 데이터**: waste_slip_items 비어있음 → `categories: []` 또는 insufficient_data

### 결정 5: 성능 — 무시 가능
- ATTACH 오버헤드: 연결당 1회, ~1ms
- JOIN: `products.item_cd`는 PRIMARY KEY (schema.py 확인), `idx_wsi_item` 인덱스 존재 (schema.py:1150) → O(log n)
- 현재 4매장 병렬 23:55 실행, 기존 < 5초 → JOIN 추가로도 여전히 < 5초 예상

---

## 3. 변경 전/후 코드

### 변경 전 (`ops_metrics.py:134-207`)
```python
def _waste_rate(self) -> dict:
    conn = DBRouter.get_store_connection(self.store_id)   # ← 매장 DB만
    try:
        cursor = conn.cursor()
        # data_days 체크 생략
        cursor.execute("""
            SELECT mid_cd,                                   # ← 존재하지 않는 컬럼
                   SUM(CASE WHEN chit_date >= date('now', '-7 days') THEN qty ELSE 0 END) as waste_7d,
                   SUM(qty) as waste_30d
            FROM waste_slip_items
            WHERE chit_date >= date('now', '-30 days')
            GROUP BY mid_cd
        """)
```

### 변경 후
```python
def _waste_rate(self) -> dict:
    conn = DBRouter.get_store_connection_with_common(self.store_id)  # ← common ATTACH
    try:
        cursor = conn.cursor()
        # data_days 체크 동일
        cursor.execute("""
            SELECT p.mid_cd,
                   SUM(CASE WHEN wsi.chit_date >= date('now', '-7 days') THEN wsi.qty ELSE 0 END) as waste_7d,
                   SUM(wsi.qty) as waste_30d,
                   COUNT(*) as wsi_rows
            FROM waste_slip_items wsi
            JOIN common.products p ON wsi.item_cd = p.item_cd
            WHERE wsi.chit_date >= date('now', '-30 days')
            GROUP BY p.mid_cd
        """)
        waste_map = {}
        for row in cursor.fetchall():
            waste_map[row["mid_cd"]] = {
                "waste_7d": row["waste_7d"] or 0,
                "waste_30d": row["waste_30d"] or 0,
            }

        # 매칭률 경고 (products에 없는 item_cd 비율)
        cursor.execute("""
            SELECT
                SUM(CASE WHEN p.item_cd IS NULL THEN wsi.qty ELSE 0 END) as unmatched_qty,
                SUM(wsi.qty) as total_qty
            FROM waste_slip_items wsi
            LEFT JOIN common.products p ON wsi.item_cd = p.item_cd
            WHERE wsi.chit_date >= date('now', '-30 days')
        """)
        row = cursor.fetchone()
        total = row["total_qty"] or 0
        unmatched = row["unmatched_qty"] or 0
        if total > 0 and unmatched / total > 0.05:
            logger.warning(
                f"[OpsMetrics] {self.store_id} waste_rate products 미매칭 "
                f"{unmatched}/{total} ({100*unmatched/total:.1f}%) "
                f"— 신제품 products 동기화 확인 필요"
            )

        # daily_sales 쿼리 부분은 동일 (mid_cd 컬럼 존재)
        ...
```

**핵심 diff**: 3곳
1. L136 커넥션: `get_store_connection` → `get_store_connection_with_common`
2. L150-157 쿼리: `JOIN common.products p ON wsi.item_cd = p.item_cd`, `GROUP BY p.mid_cd`
3. 신규: 매칭률 경고 쿼리 + 로그 (품질 모니터링)

---

## 4. 회귀 테스트 설계

파일: `tests/test_ops_metrics.py` (있으면 확장, 없으면 생성)

```python
class TestWasteRateQuery:
    """_waste_rate products JOIN 회귀 테스트 (ops-metrics-waste-query-fix)"""

    def test_waste_rate_joins_products_for_mid_cd(self, in_memory_db):
        """정상: waste_slip_items + products JOIN → mid_cd별 집계"""
        # setup: products 3개(mid_cd=001,002,005), waste_slip_items 3개 매칭
        # setup: daily_sales 30일치 샘플
        result = OpsMetricsCollector("TEST").get_metrics()
        assert "categories" in result["waste_rate"]
        assert len(result["waste_rate"]["categories"]) == 3
        mids = [c["mid_cd"] for c in result["waste_rate"]["categories"]]
        assert "001" in mids

    def test_waste_rate_excludes_unmatched_items(self, in_memory_db, caplog):
        """products 미매칭 상품은 제외 + 경고 로그"""
        # setup: waste_slip_items 5개 중 2개만 products에 존재
        result = OpsMetricsCollector("TEST").get_metrics()
        assert len(result["waste_rate"]["categories"]) <= 2  # 2개 mid_cd
        assert any("products 미매칭" in r.message for r in caplog.records)

    def test_waste_rate_no_such_column_regression(self, in_memory_db):
        """회귀: 'no such column: mid_cd' OperationalError 재발 방지"""
        # waste_slip_items 스키마에 mid_cd 없어도 쿼리 성공해야 함
        result = OpsMetricsCollector("TEST").get_metrics()
        assert result["waste_rate"] != {"insufficient_data": True}
```

### 테스트 환경 요구사항
- `in_memory_db` fixture에 `waste_slip_items` + `products` + `daily_sales` 스키마 필요
- ATTACH를 in-memory에서 재현하기 어려우면: `:memory:` 대신 tmp_path에 실제 파일 DB 2개 생성 후 ATTACH

---

## 5. 구현 순서

| 순서 | 작업 | 파일 | 변경 규모 |
|------|------|------|----------|
| 1 | 기존 test_ops_metrics.py 확인/생성 | tests/ | 확인 |
| 2 | `_waste_rate()` 커넥션/쿼리 수정 | ops_metrics.py L134~ | ~15 lines |
| 3 | 매칭률 경고 쿼리 추가 | ops_metrics.py | +15 lines |
| 4 | 회귀 테스트 3개 작성 | test_ops_metrics.py | +80 lines |
| 5 | `pytest tests/test_ops_metrics.py -v` | — | — |
| 6 | 수동 재현: `python -c "from src.analysis.ops_metrics import OpsMetricsCollector; import json; print(json.dumps(OpsMetricsCollector('46513').get_metrics()['waste_rate'], ensure_ascii=False, indent=2))"` | — | — |
| 7 | 이슈체인 `[OPEN] → [WATCHING]` + 시도 1 기록 | scheduling.md | — |
| 8 | 커밋 + 푸시 | — | — |
| 9 | 다음 OpsMetricsCollector 실행 (23:55 또는 수동) 후 K2 상태 확인 | milestone_snapshots | — |

---

## 6. 검증 방법

### 단위 테스트
```bash
pytest tests/test_ops_metrics.py::TestWasteRateQuery -v
```

### 통합 재현
```bash
cd bgf_auto
python -c "
from src.analysis.ops_metrics import OpsMetricsCollector
import json
for sid in ['46513', '46704', '47863', '49965']:
    r = OpsMetricsCollector(sid).get_metrics()['waste_rate']
    print(sid, json.dumps(r, ensure_ascii=False)[:200])
"
```

### 성공 기준
1. 4매장 전부 `{"categories": [...]}` 반환 (insufficient_data 아님)
2. `[OpsMetrics] waste_rate 실패` 로그 소멸
3. 회귀 테스트 3개 통과
4. 매칭률 경고 로그 확인 (신제품 동기화 모니터링 부수 효과)
5. 다음 milestone_snapshots에서 K2 NO_DATA → ACHIEVED/NOT_MET 전환

---

## 7. 롤백 계획

커밋 단위 revert로 즉시 롤백 가능. 쿼리 실패해도 기존과 동일한 `insufficient_data` 반환이라 서비스 영향 없음.

---

## 8. 다음 단계

`/pdca do ops-metrics-waste-query-fix` — Phase A 구현 시작
