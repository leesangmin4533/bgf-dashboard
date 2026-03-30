"""수요 패턴 자동 분류기

60일 판매 이력의 sell_day_ratio 기반으로 4단계 분류:
- daily (>=70%): WMA 기존 파이프라인
- frequent (40~69%): WMA + 축소 계수
- intermittent (15~39%): Croston/TSB
- slow (<15%): ROP only

푸드류(001~005, 012)/디저트는 면제 (기존 파이프라인 유지).
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
from enum import Enum

from src.utils.logger import get_logger

logger = get_logger(__name__)


class DemandPattern(str, Enum):
    """수요 패턴 4단계"""
    DAILY = "daily"
    FREQUENT = "frequent"
    INTERMITTENT = "intermittent"
    SLOW = "slow"


# 분류 임계값
DEMAND_PATTERN_THRESHOLDS = {
    "daily": 0.70,
    "frequent": 0.40,
    "intermittent": 0.15,
}

# 수요 패턴 분류 면제 (DAILY 고정, WMA 파이프라인 직행)
# ★ 변경 시 tests/test_demand_classifier.py의 test_exempt_mids_contains_required 확인
# ★ 변경 시 scripts/impact_check.py 실행 필수
DEMAND_PATTERN_EXEMPT_MIDS = {
    "001",  # 도시락 — 당일폐기, 매일 발주 (2026-02)
    "002",  # 주먹밥 — 동일 (2026-02)
    "003",  # 김밥 — 동일 (2026-02)
    "004",  # 샌드위치 — 동일 (2026-02)
    "005",  # 햄버거 — 동일 (2026-02)
    "012",  # 빵 — 유통기한 3일, 고회전 (2026-02)
    "047",  # 우유 — 당일소진 고회전, SLOW 오분류 방지 (2026-03-31)
}

# 최소 데이터 요건
MIN_DATA_DAYS = 14
ANALYSIS_WINDOW_DAYS = 60

# sparse-fix 보정 최소 조건: 윈도우 대비 최소 판매일 비율
# 60일 중 최소 5% (3일) 이상 판매 이력이 있어야 수집 갭 보정 허용
# 이하이면 진짜 slow로 간주 (예: 2/60=3.3% → 보정 안 함)
SPARSE_FIX_MIN_WINDOW_RATIO = 0.05


@dataclass
class ClassificationResult:
    """분류 결과"""
    item_cd: str
    pattern: DemandPattern
    sell_day_ratio: float
    available_days: int
    sell_days: int
    total_days: int
    data_sufficient: bool


class DemandClassifier:
    """수요 패턴 분류기

    Args:
        store_id: 매장 코드
    """

    def __init__(self, store_id: str = "46513"):
        self.store_id = store_id

    def classify_item(self, item_cd: str, mid_cd: str = "") -> ClassificationResult:
        """단일 상품 분류"""
        if mid_cd in DEMAND_PATTERN_EXEMPT_MIDS:
            return ClassificationResult(
                item_cd=item_cd, pattern=DemandPattern.DAILY,
                sell_day_ratio=1.0, available_days=0, sell_days=0,
                total_days=0, data_sufficient=True,
            )

        stats = self._query_sell_stats(item_cd)
        return self._classify_from_stats(item_cd, stats)

    def classify_batch(self, items: List[Dict]) -> Dict[str, ClassificationResult]:
        """배치 분류 (전체 발주 대상 상품)

        Args:
            items: [{"item_cd": ..., "mid_cd": ...}, ...]

        Returns:
            {item_cd: ClassificationResult}
        """
        results = {}

        # 면제 상품: DAILY 고정
        exempt_items = [i for i in items if i.get("mid_cd", "") in DEMAND_PATTERN_EXEMPT_MIDS]
        target_items = [i for i in items if i.get("mid_cd", "") not in DEMAND_PATTERN_EXEMPT_MIDS]

        for item in exempt_items:
            results[item["item_cd"]] = ClassificationResult(
                item_cd=item["item_cd"], pattern=DemandPattern.DAILY,
                sell_day_ratio=1.0, available_days=0, sell_days=0,
                total_days=0, data_sufficient=True,
            )

        # 대상 상품: 배치 쿼리
        if target_items:
            item_cds = [i["item_cd"] for i in target_items]
            batch_stats = self._query_sell_stats_batch(item_cds)

            for item in target_items:
                icd = item["item_cd"]
                stats = batch_stats.get(icd, {"available_days": 0, "sell_days": 0, "total_days": 0})
                results[icd] = self._classify_from_stats(icd, stats)

        return results

    def _classify_from_stats(self, item_cd: str, stats: Dict) -> ClassificationResult:
        """통계에서 패턴 분류

        누락일 보정: daily_sales에 판매 0인 날이 미등록될 수 있음.
        total_days(레코드 수) < ANALYSIS_WINDOW_DAYS(60) 이면
        누락일을 "재고有 + 판매無"로 간주하여 분모에 포함.
        """
        available_days = stats["available_days"]
        sell_days = stats["sell_days"]
        total_days = stats["total_days"]

        # ── 누락일 보정 ──────────────────────────────
        # daily_sales 레코드가 윈도우보다 적으면
        # 누락일 = 재고 있으나 판매 없는 날로 간주
        missing_days = max(0, ANALYSIS_WINDOW_DAYS - total_days)
        effective_available = available_days + missing_days

        if total_days < MIN_DATA_DAYS:
            # 데이터 부족: 두 가지 비율을 모두 확인
            # (1) 윈도우 비율: 60일 대비 판매일 비율 (과발주 방지)
            # (2) 데이터 비율: 실제 기록일 중 판매일 비율 (수집 갭 보정)
            window_ratio = sell_days / ANALYSIS_WINDOW_DAYS
            data_ratio = sell_days / total_days if total_days > 0 else 0.0

            if window_ratio < DEMAND_PATTERN_THRESHOLDS["intermittent"]:
                # 60일 중 판매일 < 15%이지만, 기록된 날 중 판매 비율이 높으면
                # 데이터 수집 갭(미수집일)으로 인한 착시 → SLOW가 아닌 실제 비율 기반 분류
                #
                # ★ window_ratio 하한 가드 (sparse-fix-v2):
                # 60일 대비 판매일이 최소 5% 이상이어야 "수집 갭"으로 판단.
                # 이하(예: 2/60=3.3%)이면 진짜 초저회전 상품이므로 SLOW 유지.
                # 배경: total_days=2, sell_days=2 → data_ratio=100%이지만
                # 실제로는 60일 중 2일만 판매된 slow 상품.
                if (data_ratio >= DEMAND_PATTERN_THRESHOLDS["frequent"]
                        and window_ratio >= SPARSE_FIX_MIN_WINDOW_RATIO):
                    # 기록일 중 40%+ 판매 AND 윈도우 대비 5%+ → 수집 갭이지 수요 부족이 아님
                    logger.debug(
                        f"[DemandClassifier] {item_cd}: data_insufficient → FREQUENT "
                        f"(window={window_ratio:.1%} >= {SPARSE_FIX_MIN_WINDOW_RATIO:.0%} "
                        f"AND data={data_ratio:.1%} >= 40%)"
                    )
                    return ClassificationResult(
                        item_cd=item_cd, pattern=DemandPattern.FREQUENT,
                        sell_day_ratio=round(data_ratio, 4),
                        available_days=available_days,
                        sell_days=sell_days, total_days=total_days,
                        data_sufficient=False,
                    )
                # 기록일 중에서도 판매가 적으면 진짜 slow
                logger.debug(
                    f"[DemandClassifier] {item_cd}: data_insufficient → SLOW "
                    f"(sell={sell_days}/window={ANALYSIS_WINDOW_DAYS} = {window_ratio:.1%}, "
                    f"data_ratio={data_ratio:.1%})"
                )
                return ClassificationResult(
                    item_cd=item_cd, pattern=DemandPattern.SLOW,
                    sell_day_ratio=round(window_ratio, 4),
                    available_days=available_days,
                    sell_days=sell_days, total_days=total_days,
                    data_sufficient=False,
                )
            # 나머지 데이터 부족 → 기존 폴백 (FREQUENT)
            return ClassificationResult(
                item_cd=item_cd, pattern=DemandPattern.FREQUENT,
                sell_day_ratio=0.5, available_days=available_days,
                sell_days=sell_days, total_days=total_days,
                data_sufficient=False,
            )

        # ── 정상 분류: 보정된 available 기준 ──────────
        if effective_available > 0:
            sell_day_ratio = sell_days / effective_available
        else:
            sell_day_ratio = 0.0

        if sell_day_ratio >= DEMAND_PATTERN_THRESHOLDS["daily"]:
            pattern = DemandPattern.DAILY
        elif sell_day_ratio >= DEMAND_PATTERN_THRESHOLDS["frequent"]:
            pattern = DemandPattern.FREQUENT
        elif sell_day_ratio >= DEMAND_PATTERN_THRESHOLDS["intermittent"]:
            pattern = DemandPattern.INTERMITTENT
        else:
            pattern = DemandPattern.SLOW

        return ClassificationResult(
            item_cd=item_cd, pattern=pattern,
            sell_day_ratio=round(sell_day_ratio, 4),
            available_days=available_days,
            sell_days=sell_days, total_days=total_days,
            data_sufficient=True,
        )

    def _query_sell_stats(self, item_cd: str) -> Dict:
        """단일 상품 판매 통계 (60일)"""
        from src.infrastructure.database.connection import DBRouter
        conn = DBRouter.get_store_connection(self.store_id)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COUNT(*) as total_days,
                    SUM(CASE WHEN stock_qty > 0 THEN 1 ELSE 0 END) as available_days,
                    SUM(CASE WHEN stock_qty > 0 AND sale_qty > 0 THEN 1 ELSE 0 END) as sell_days
                FROM daily_sales
                WHERE item_cd = ?
                AND sales_date >= date('now', '-60 days')
            """, (item_cd,))
            row = cursor.fetchone()
            if row:
                return {
                    "total_days": row[0] or 0,
                    "available_days": row[1] or 0,
                    "sell_days": row[2] or 0,
                }
            return {"total_days": 0, "available_days": 0, "sell_days": 0}
        finally:
            conn.close()

    def _query_sell_stats_batch(self, item_cds: List[str]) -> Dict[str, Dict]:
        """배치 판매 통계 (60일, 단일 쿼리)"""
        if not item_cds:
            return {}
        from src.infrastructure.database.connection import DBRouter
        conn = DBRouter.get_store_connection(self.store_id)
        try:
            cursor = conn.cursor()
            placeholders = ",".join(["?"] * len(item_cds))
            cursor.execute(f"""
                SELECT
                    item_cd,
                    COUNT(*) as total_days,
                    SUM(CASE WHEN stock_qty > 0 THEN 1 ELSE 0 END) as available_days,
                    SUM(CASE WHEN stock_qty > 0 AND sale_qty > 0 THEN 1 ELSE 0 END) as sell_days
                FROM daily_sales
                WHERE item_cd IN ({placeholders})
                AND sales_date >= date('now', '-60 days')
                GROUP BY item_cd
            """, (*item_cds,))

            results = {}
            for row in cursor.fetchall():
                results[row[0]] = {
                    "total_days": row[1] or 0,
                    "available_days": row[2] or 0,
                    "sell_days": row[3] or 0,
                }
            return results
        finally:
            conn.close()
