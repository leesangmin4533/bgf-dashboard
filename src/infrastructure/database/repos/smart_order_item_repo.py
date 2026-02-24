"""
SmartOrderItemRepository — 스마트발주 상품 캐시 저장소

원본: src/db/repository.py SmartOrderItemRepository (lines 4360-4468)
"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SmartOrderItemRepository(BaseRepository):
    """스마트발주 상품 캐시 저장소

    사이트 '발주 현황 조회 > 스마트' 탭의 상품 목록을 캐싱.
    사이트 조회 성공 시 refresh()로 전체 교체, 실패 시 DB 캐시 사용.
    """

    db_type = "store"

    def get_all_item_codes(self, store_id: Optional[str] = None) -> List[str]:
        """캐싱된 스마트발주 상품코드 목록 반환"""
        conn = self._get_conn()
        try:
            sf, sp = self._store_filter(None, store_id)
            rows = conn.execute(
                f"SELECT item_cd FROM smart_order_items WHERE 1=1 {sf}",
                sp
            ).fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()

    def get_all_detail(self, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """상세 정보 포함 전체 목록"""
        conn = self._get_conn()
        try:
            sf, sp = self._store_filter(None, store_id)
            rows = conn.execute(
                "SELECT item_cd, item_nm, mid_cd, detected_at, updated_at "
                f"FROM smart_order_items WHERE 1=1 {sf} ORDER BY item_cd",
                sp
            ).fetchall()
            return [
                {
                    "item_cd": r[0], "item_nm": r[1], "mid_cd": r[2],
                    "detected_at": r[3], "updated_at": r[4]
                }
                for r in rows
            ]
        finally:
            conn.close()

    def refresh(self, items: List[Dict[str, str]], store_id: Optional[str] = None) -> int:
        """사이트 조회 결과로 전체 교체

        사이트에서 실제 0건이면 기존 캐시도 삭제한다 (이전 캐시로 잘못 제외 방지).
        호출자는 사이트 조회 실패(None) vs 성공(빈 리스트)을 구분하여
        실패 시에는 이 메서드를 호출하지 않아야 한다.

        Args:
            items: [{"item_cd": "...", "item_nm": "...", "mid_cd": "..."}, ...]
            store_id: 매장 코드

        Returns:
            저장된 상품 수
        """
        conn = self._get_conn()
        now = self._now()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)
            cursor.execute(f"DELETE FROM smart_order_items WHERE 1=1 {sf}", sp)

            if not items:
                conn.commit()
                logger.info("스마트발주 상품 0건 - 기존 캐시 삭제 완료")
                return 0

            valid_items = [
                (
                    store_id,
                    item.get("item_cd"),
                    item.get("item_nm"),
                    item.get("mid_cd"),
                    now,
                    now
                )
                for item in items if item.get("item_cd")
            ]
            cursor.executemany(
                """INSERT INTO smart_order_items
                   (store_id, item_cd, item_nm, mid_cd, detected_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                valid_items
            )
            conn.commit()
            return len(valid_items)
        finally:
            conn.close()

    def get_count(self, store_id: Optional[str] = None) -> int:
        """캐시된 상품 수"""
        conn = self._get_conn()
        try:
            sf, sp = self._store_filter(None, store_id)
            row = conn.execute(
                f"SELECT COUNT(*) FROM smart_order_items WHERE 1=1 {sf}",
                sp
            ).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def get_last_updated(self, store_id: Optional[str] = None) -> Optional[str]:
        """마지막 캐시 갱신 시각"""
        conn = self._get_conn()
        try:
            sf, sp = self._store_filter(None, store_id)
            row = conn.execute(
                f"SELECT MAX(updated_at) FROM smart_order_items WHERE 1=1 {sf}",
                sp
            ).fetchone()
            return row[0] if row and row[0] else None
        finally:
            conn.close()
