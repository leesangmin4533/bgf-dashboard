"""
StoppedItemRepository -- 발주정지 상품 저장소 (common.db)

Phase 3 실패사유 수집 후 stop_reason이 있는 상품을 등록하고,
예측/발주 시 is_active=1인 상품을 제외 대상으로 반환합니다.
"""

from datetime import datetime
from typing import List, Dict, Any, Optional, Set

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class StoppedItemRepository(BaseRepository):
    """발주정지 상품 저장소 (common.db)"""

    db_type = "common"

    def upsert(self, item_cd: str, item_nm: Optional[str] = None,
               stop_reason: Optional[str] = None) -> bool:
        """발주정지 상품 등록/갱신

        Args:
            item_cd: 상품코드
            item_nm: 상품명
            stop_reason: 정지사유

        Returns:
            저장 성공 여부
        """
        conn = self._get_conn()
        try:
            now = self._now()
            conn.execute("""
                INSERT INTO stopped_items
                    (item_cd, item_nm, stop_reason, first_detected_at, last_detected_at, is_active)
                VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT(item_cd) DO UPDATE SET
                    item_nm = COALESCE(excluded.item_nm, stopped_items.item_nm),
                    stop_reason = excluded.stop_reason,
                    last_detected_at = excluded.last_detected_at,
                    is_active = 1
            """, (item_cd, item_nm, stop_reason, now, now))
            conn.commit()
            return True
        except Exception:
            logger.exception(f"stopped_items upsert 실패: {item_cd}")
            return False
        finally:
            conn.close()

    def deactivate(self, item_cd: str) -> bool:
        """발주정지 해제 (is_active=0)

        Args:
            item_cd: 상품코드

        Returns:
            업데이트 성공 여부
        """
        conn = self._get_conn()
        try:
            now = self._now()
            conn.execute("""
                UPDATE stopped_items
                SET is_active = 0, last_detected_at = ?
                WHERE item_cd = ? AND is_active = 1
            """, (now, item_cd))
            conn.commit()
            return True
        except Exception:
            logger.exception(f"stopped_items deactivate 실패: {item_cd}")
            return False
        finally:
            conn.close()

    def get_active_item_codes(self) -> Set[str]:
        """활성 발주정지 상품 코드 set 반환"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT item_cd FROM stopped_items WHERE is_active = 1"
            ).fetchall()
            return {row[0] for row in rows}
        finally:
            conn.close()

    def get_all_active(self) -> List[Dict[str, Any]]:
        """활성 발주정지 상품 전체 조회"""
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT item_cd, item_nm, stop_reason,
                       first_detected_at, last_detected_at
                FROM stopped_items
                WHERE is_active = 1
                ORDER BY last_detected_at DESC
            """).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_count(self) -> int:
        """활성 발주정지 상품 건수"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM stopped_items WHERE is_active = 1"
            ).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def sync_from_fail_reasons(self, results: List[Dict[str, Any]]) -> Dict[str, int]:
        """Phase 3 수집 결과에서 stopped_items 동기화

        - stop_reason이 비어있지 않은 상품 -> upsert (is_active=1)
        - stop_reason이 비어있거나 '알수없음'인 상품 -> deactivate (is_active=0)

        Args:
            results: FailReasonCollector.collect_all() 결과

        Returns:
            {"activated": N, "deactivated": N}
        """
        activated = 0
        deactivated = 0

        conn = self._get_conn()
        try:
            now = self._now()
            for r in results:
                item_cd = r.get("item_cd")
                if not item_cd:
                    continue

                stop_reason = (r.get("stop_reason") or "").strip()
                item_nm = r.get("item_nm")

                if stop_reason and stop_reason != "알수없음":
                    conn.execute("""
                        INSERT INTO stopped_items
                            (item_cd, item_nm, stop_reason,
                             first_detected_at, last_detected_at, is_active)
                        VALUES (?, ?, ?, ?, ?, 1)
                        ON CONFLICT(item_cd) DO UPDATE SET
                            item_nm = COALESCE(excluded.item_nm, stopped_items.item_nm),
                            stop_reason = excluded.stop_reason,
                            last_detected_at = excluded.last_detected_at,
                            is_active = 1
                    """, (item_cd, item_nm, stop_reason, now, now))
                    activated += 1
                else:
                    conn.execute("""
                        UPDATE stopped_items
                        SET is_active = 0, last_detected_at = ?
                        WHERE item_cd = ? AND is_active = 1
                    """, (now, item_cd))
                    if conn.total_changes > 0:
                        deactivated += 1

            conn.commit()
        except Exception:
            logger.exception("stopped_items sync 실패")
        finally:
            conn.close()

        if activated or deactivated:
            logger.info(
                f"[StoppedItems] 동기화: {activated}건 활성화, "
                f"{deactivated}건 해제"
            )

        return {"activated": activated, "deactivated": deactivated}
