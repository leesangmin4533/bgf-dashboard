"""
OrderExclusionRepository -- 발주 제외 사유 추적 저장소 (store DB)

auto_order.py에서 발주 목록에서 제외된 상품의 사유를 기록합니다.
- NOT_CARRIED: BGF 사이트 점포 미취급
- CUT: 발주중지(CUT) 판정
- AUTO_ORDER: 본부 자동발주 대상
- SMART_ORDER: 본부 스마트발주 대상
- STOPPED: stopped_items 등록 상품
- STOCK_SUFFICIENT: 재고/미입고 충분 (need<=0)
- FORCE_SUPPRESSED: FORCE 보충 생략 (pending 충분)
"""

from typing import List, Dict, Any, Optional

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ExclusionType:
    """발주 제외 사유 타입 상수"""
    NOT_CARRIED = "NOT_CARRIED"
    CUT = "CUT"
    AUTO_ORDER = "AUTO_ORDER"
    SMART_ORDER = "SMART_ORDER"
    STOPPED = "STOPPED"
    STOCK_SUFFICIENT = "STOCK_SUFFICIENT"
    FORCE_SUPPRESSED = "FORCE_SUPPRESSED"
    DESSERT_STOP = "DESSERT_STOP"
    BEVERAGE_STOP = "BEVERAGE_STOP"
    SITE_ORDERED = "SITE_ORDERED"

    ALL = [
        NOT_CARRIED, CUT, AUTO_ORDER, SMART_ORDER,
        STOPPED, STOCK_SUFFICIENT, FORCE_SUPPRESSED,
        DESSERT_STOP, BEVERAGE_STOP, SITE_ORDERED,
    ]


class OrderExclusionRepository(BaseRepository):
    """발주 제외 사유 저장소 (store DB)"""

    db_type = "store"

    def save_exclusions_batch(
        self, eval_date: str, exclusions: List[Dict[str, Any]]
    ) -> int:
        """발주 제외 사유 배치 UPSERT

        Args:
            eval_date: 평가일 (YYYY-MM-DD)
            exclusions: 제외 사유 리스트
                [{item_cd, item_nm?, mid_cd?, exclusion_type,
                  predicted_qty?, current_stock?, pending_qty?, detail?}]

        Returns:
            저장된 건수
        """
        if not exclusions:
            return 0

        conn = self._get_conn()
        saved = 0
        store_id = self.store_id or "46513"
        try:
            now = self._now()
            for exc in exclusions:
                try:
                    conn.execute("""
                        INSERT INTO order_exclusions
                            (store_id, eval_date, item_cd, item_nm, mid_cd,
                             exclusion_type, predicted_qty, current_stock,
                             pending_qty, detail, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(store_id, eval_date, item_cd) DO UPDATE SET
                            item_nm = COALESCE(excluded.item_nm, order_exclusions.item_nm),
                            mid_cd = COALESCE(excluded.mid_cd, order_exclusions.mid_cd),
                            exclusion_type = excluded.exclusion_type,
                            predicted_qty = excluded.predicted_qty,
                            current_stock = excluded.current_stock,
                            pending_qty = excluded.pending_qty,
                            detail = excluded.detail,
                            created_at = excluded.created_at
                    """, (
                        store_id,
                        eval_date,
                        exc.get("item_cd"),
                        exc.get("item_nm"),
                        exc.get("mid_cd"),
                        exc.get("exclusion_type", ""),
                        exc.get("predicted_qty", 0),
                        exc.get("current_stock", 0),
                        exc.get("pending_qty", 0),
                        exc.get("detail"),
                        now,
                    ))
                    saved += 1
                except Exception as e:
                    logger.warning(f"order_exclusion 저장 실패: {exc.get('item_cd')} — {e}")
            conn.commit()
        finally:
            conn.close()
        return saved

    def get_exclusions_by_date(
        self, eval_date: str, store_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """날짜별 제외 사유 조회

        Args:
            eval_date: 평가일 (YYYY-MM-DD)
            store_id: 매장 코드

        Returns:
            제외 사유 리스트
        """
        conn = self._get_conn()
        try:
            sf, sp = self._store_filter(None, store_id)
            rows = conn.execute(f"""
                SELECT id, store_id, eval_date, item_cd, item_nm, mid_cd,
                       exclusion_type, predicted_qty, current_stock,
                       pending_qty, detail, created_at
                FROM order_exclusions
                WHERE eval_date = ? {sf}
                ORDER BY id
            """, (eval_date,) + sp).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_exclusion_summary(
        self, eval_date: str, store_id: Optional[str] = None
    ) -> Dict[str, int]:
        """타입별 제외 카운트 요약

        Args:
            eval_date: 평가일
            store_id: 매장 코드

        Returns:
            {"NOT_CARRIED": 53, "CUT": 2, ...}
        """
        conn = self._get_conn()
        try:
            sf, sp = self._store_filter(None, store_id)
            rows = conn.execute(f"""
                SELECT exclusion_type, COUNT(*) as cnt
                FROM order_exclusions
                WHERE eval_date = ? {sf}
                GROUP BY exclusion_type
                ORDER BY cnt DESC
            """, (eval_date,) + sp).fetchall()
            return {row["exclusion_type"]: row["cnt"] for row in rows}
        finally:
            conn.close()

    def get_exclusions_by_type(
        self, eval_date: str, exclusion_type: str,
        store_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """특정 타입의 제외 사유 조회

        Args:
            eval_date: 평가일
            exclusion_type: 제외 타입 (ExclusionType 상수)
            store_id: 매장 코드

        Returns:
            해당 타입 제외 사유 리스트
        """
        conn = self._get_conn()
        try:
            sf, sp = self._store_filter(None, store_id)
            rows = conn.execute(f"""
                SELECT id, store_id, eval_date, item_cd, item_nm, mid_cd,
                       exclusion_type, predicted_qty, current_stock,
                       pending_qty, detail, created_at
                FROM order_exclusions
                WHERE eval_date = ? AND exclusion_type = ? {sf}
                ORDER BY id
            """, (eval_date, exclusion_type) + sp).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
