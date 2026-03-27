"""
SubstitutionEventRepository -- 소분류 내 잠식 이벤트 CRUD

소분류(small_cd) 내 상품 간 수요 이동(잠식/cannibalization) 이벤트를
저장, 조회, 만료 처리합니다.
"""

from datetime import datetime
from typing import Dict, List, Optional

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SubstitutionEventRepository(BaseRepository):
    """소분류 내 잠식 이벤트 Repository"""

    db_type = "store"

    def upsert_event(self, record: dict) -> None:
        """잠식 이벤트 UPSERT (store_id+detection_date+loser+gainer 유니크)

        Args:
            record: 잠식 이벤트 데이터
        """
        conn = self._get_conn()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO substitution_events
                (store_id, detection_date, small_cd, small_nm,
                 gainer_item_cd, gainer_item_nm,
                 gainer_prior_avg, gainer_recent_avg, gainer_growth_rate,
                 loser_item_cd, loser_item_nm,
                 loser_prior_avg, loser_recent_avg, loser_decline_rate,
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

    def get_active_events(self, item_cd: str, as_of_date: str) -> List[dict]:
        """item_cd가 loser인 활성 잠식 이벤트 조회

        Args:
            item_cd: 상품 코드 (loser 기준)
            as_of_date: 기준일 (YYYY-MM-DD)
        Returns:
            활성 잠식 이벤트 목록
        """
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

    def get_active_events_batch(
        self, item_cds: List[str], as_of_date: str
    ) -> Dict[str, List[dict]]:
        """여러 상품의 활성 잠식 이벤트 배치 조회

        Args:
            item_cds: 상품 코드 목록
            as_of_date: 기준일 (YYYY-MM-DD)
        Returns:
            {item_cd: [event, ...], ...}
        """
        result: Dict[str, List[dict]] = {ic: [] for ic in item_cds}
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
                loser_cd = d["loser_item_cd"]
                if loser_cd in result:
                    result[loser_cd].append(d)
            return result
        finally:
            conn.close()

    def expire_old_events(self, as_of_date: str) -> int:
        """만료일 지난 이벤트 비활성화

        Args:
            as_of_date: 기준일 (YYYY-MM-DD)
        Returns:
            비활성화된 이벤트 수
        """
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

    def get_events_by_small_cd(
        self, small_cd: str, days: int = 30
    ) -> List[dict]:
        """소분류별 최근 이벤트 조회

        Args:
            small_cd: 소분류 코드
            days: 조회 기간 (일)
        Returns:
            이벤트 목록
        """
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
