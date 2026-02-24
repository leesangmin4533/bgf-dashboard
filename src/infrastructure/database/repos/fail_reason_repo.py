"""
FailReasonRepository — 발주 실패 사유 저장소

원본: src/db/repository.py FailReasonRepository (lines 4520-4722)
"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class FailReasonRepository(BaseRepository):
    """발주 실패 사유 저장소"""

    db_type = "store"

    def save_fail_reason(
        self,
        eval_date: str,
        item_cd: str,
        item_nm: Optional[str] = None,
        mid_cd: Optional[str] = None,
        stop_reason: Optional[str] = None,
        orderable_status: Optional[str] = None,
        orderable_day: Optional[str] = None,
        order_status: str = "fail",
    ) -> bool:
        """실패 사유 단건 저장 (UPSERT)

        Args:
            eval_date: 평가일 (YYYY-MM-DD)
            item_cd: 상품코드
            item_nm: 상품명
            mid_cd: 중분류 코드
            stop_reason: 정지사유
            orderable_status: 발주가능상태
            orderable_day: 발주가능요일
            order_status: 발주상태 (기본 fail)

        Returns:
            저장 성공 여부
        """
        conn = self._get_conn()
        store_id = self.store_id or "46513"
        try:
            now = self._now()
            conn.execute("""
                INSERT INTO order_fail_reasons
                    (store_id, eval_date, item_cd, item_nm, mid_cd, stop_reason,
                     orderable_status, orderable_day, order_status,
                     checked_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, eval_date, item_cd) DO UPDATE SET
                    item_nm = excluded.item_nm,
                    mid_cd = excluded.mid_cd,
                    stop_reason = excluded.stop_reason,
                    orderable_status = excluded.orderable_status,
                    orderable_day = excluded.orderable_day,
                    order_status = excluded.order_status,
                    checked_at = excluded.checked_at
            """, (store_id, eval_date, item_cd, item_nm, mid_cd, stop_reason,
                  orderable_status, orderable_day, order_status, now, now))
            conn.commit()
            return True
        except Exception:
            logger.exception(f"save_fail_reason 실패: {item_cd}")
            return False
        finally:
            conn.close()

    def save_fail_reasons_batch(
        self, eval_date: str, results: List[Dict[str, Any]]
    ) -> int:
        """실패 사유 일괄 저장

        Args:
            eval_date: 평가일
            results: 결과 리스트 [{item_cd, item_nm, mid_cd, stop_reason, ...}]

        Returns:
            저장된 건수
        """
        conn = self._get_conn()
        saved = 0
        store_id = self.store_id or "46513"
        try:
            now = self._now()
            for r in results:
                try:
                    conn.execute("""
                        INSERT INTO order_fail_reasons
                            (store_id, eval_date, item_cd, item_nm, mid_cd, stop_reason,
                             orderable_status, orderable_day, order_status,
                             checked_at, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(store_id, eval_date, item_cd) DO UPDATE SET
                            item_nm = excluded.item_nm,
                            mid_cd = excluded.mid_cd,
                            stop_reason = excluded.stop_reason,
                            orderable_status = excluded.orderable_status,
                            orderable_day = excluded.orderable_day,
                            order_status = excluded.order_status,
                            checked_at = excluded.checked_at
                    """, (
                        store_id,
                        eval_date,
                        r.get("item_cd"),
                        r.get("item_nm"),
                        r.get("mid_cd"),
                        r.get("stop_reason"),
                        r.get("orderable_status"),
                        r.get("orderable_day"),
                        r.get("order_status", "fail"),
                        now, now,
                    ))
                    saved += 1
                except Exception:
                    logger.warning(f"fail_reason 저장 실패: {r.get('item_cd')}")
            conn.commit()
        finally:
            conn.close()
        return saved

    def get_fail_reasons_by_date(self, eval_date: str, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """날짜별 실패 사유 조회

        Args:
            eval_date: 평가일 (YYYY-MM-DD)
            store_id: 매장 코드

        Returns:
            실패 사유 리스트
        """
        conn = self._get_conn()
        try:
            sf, sp = self._store_filter(None, store_id)
            rows = conn.execute(f"""
                SELECT eval_date, item_cd, item_nm, mid_cd, stop_reason,
                       orderable_status, orderable_day, order_status,
                       checked_at, created_at
                FROM order_fail_reasons
                WHERE eval_date = ? {sf}
                ORDER BY id
            """, (eval_date,) + sp).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_failed_items_to_check(self, eval_date: str, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """eval_outcomes에서 fail 상품 중 아직 미확인 건 조회

        Args:
            eval_date: 평가일
            store_id: 매장 코드

        Returns:
            미확인 실패 상품 리스트
        """
        # products는 common DB에 있으므로 ATTACH 필요
        conn = self._get_conn_with_common()
        try:
            sf_eo, sp_eo = self._store_filter("eo", store_id)
            sf_fr, sp_fr = self._store_filter("fr", store_id)
            # common DB prefix: store DB에서 common ATTACH 시 "common." 접두사 필요
            p_prefix = "common." if (self.db_type == "store" and self.store_id and not self._db_path) else ""
            rows = conn.execute(f"""
                SELECT eo.item_cd, eo.mid_cd,
                       COALESCE(p.item_nm, '') AS item_nm
                FROM eval_outcomes eo
                LEFT JOIN {p_prefix}products p ON eo.item_cd = p.item_cd
                WHERE eo.eval_date = ?
                  {sf_eo}
                  AND eo.order_status = 'fail'
                  AND eo.item_cd NOT IN (
                      SELECT fr.item_cd
                      FROM order_fail_reasons fr
                      WHERE fr.eval_date = ? {sf_fr}
                  )
                ORDER BY eo.id
            """, (eval_date,) + sp_eo + (eval_date,) + sp_fr).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_today_fail_summary(self, store_id: Optional[str] = None) -> Dict[str, Any]:
        """대시보드용 오늘 실패 요약

        Args:
            store_id: 매장 코드

        Returns:
            {fail_count, checked_count, items: [...]}
        """
        today = datetime.now().strftime("%Y-%m-%d")
        conn = self._get_conn()
        try:
            sf, sp = self._store_filter(None, store_id)

            # eval_outcomes에서 오늘 fail 건수
            fail_row = conn.execute(f"""
                SELECT COUNT(*) FROM eval_outcomes
                WHERE eval_date = ? AND order_status = 'fail' {sf}
            """, (today,) + sp).fetchone()
            fail_count = fail_row[0] if fail_row else 0

            # order_fail_reasons에서 확인된 건
            rows = conn.execute(f"""
                SELECT item_cd, item_nm, mid_cd, stop_reason,
                       orderable_status, checked_at
                FROM order_fail_reasons
                WHERE eval_date = ? {sf}
                ORDER BY id
            """, (today,) + sp).fetchall()

            items = [dict(r) for r in rows]
            return {
                "fail_count": fail_count,
                "checked_count": len(items),
                "items": items,
            }
        finally:
            conn.close()
