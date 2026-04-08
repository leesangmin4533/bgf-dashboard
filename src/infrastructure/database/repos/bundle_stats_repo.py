"""
BundleStatsRepository — 묶음 통계 저장소

bundle-suspect-dynamic-master 의 Infrastructure 레이어.
common.db 의 product_details + products 조인으로 mid_cd 별 묶음 통계를 집계한다.

설계: docs/02-design/features/bundle-suspect-dynamic-master.design.md §3.1
Issue-Chain: order-execution#bundle-suspect-dynamic-master
"""

from typing import Dict

from src.infrastructure.database.base_repository import BaseRepository
from src.domain.order.bundle_classifier import BundleStats
from src.utils.logger import get_logger

logger = get_logger(__name__)


class BundleStatsRepository(BaseRepository):
    """mid_cd 별 product_details.order_unit_qty 통계 조회

    common.db 전용 — 매장 무관 전역 마스터 데이터.
    """

    db_type = "common"

    def fetch_bundle_stats(self) -> Dict[str, BundleStats]:
        """mid_cd 별 묶음 통계 집계

        product_details 와 products 를 조인하여 mid_cd 별로
        (total, bundle_n, null_n, unit1_n) 을 집계한다.
        mid_cd 가 NULL 인 상품은 제외한다.

        Returns:
            {mid_cd(3자리 zfill): BundleStats}
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT
                    p.mid_cd AS mid_cd,
                    COUNT(*) AS total,
                    SUM(CASE WHEN pd.order_unit_qty > 1 THEN 1 ELSE 0 END) AS bundle_n,
                    SUM(CASE WHEN pd.order_unit_qty IS NULL THEN 1 ELSE 0 END) AS null_n,
                    SUM(CASE WHEN pd.order_unit_qty = 1 THEN 1 ELSE 0 END) AS unit1_n
                FROM product_details pd
                INNER JOIN products p USING(item_cd)
                WHERE p.mid_cd IS NOT NULL AND p.mid_cd != ''
                GROUP BY p.mid_cd
                """
            ).fetchall()

            result: Dict[str, BundleStats] = {}
            for row in rows:
                mid_cd = str(row[0]).zfill(3)
                result[mid_cd] = BundleStats(
                    mid_cd=mid_cd,
                    total=int(row[1] or 0),
                    bundle_n=int(row[2] or 0),
                    null_n=int(row[3] or 0),
                    unit1_n=int(row[4] or 0),
                )

            logger.debug(
                f"[BundleStatsRepo] fetched {len(result)} mid groups "
                f"(total products: {sum(s.total for s in result.values())})"
            )
            return result
        finally:
            try:
                conn.close()
            except Exception:
                pass
