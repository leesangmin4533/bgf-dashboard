"""
입고 이력 저장소 (ReceivingRepository)

receiving_history 테이블 CRUD
"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ReceivingRepository(BaseRepository):
    """입고 이력 저장소"""

    db_type = "store"

    def save_receiving(
        self,
        receiving_date: str,
        receiving_time: str,
        chit_no: str,
        item_cd: str,
        item_nm: str,
        mid_cd: str,
        order_date: str,
        order_qty: int,
        receiving_qty: int,
        delivery_type: str,
        center_nm: str,
        center_cd: Optional[str] = None,
        store_id: Optional[str] = None
    ) -> int:
        """
        입고 정보 저장

        Args:
            receiving_date: 입고일 (YYYY-MM-DD)
            receiving_time: 입고시간 (HH:MM)
            chit_no: 전표번호
            item_cd: 상품코드
            item_nm: 상품명
            mid_cd: 중분류 코드
            order_date: 발주일 (YYYY-MM-DD)
            order_qty: 발주수량
            receiving_qty: 입고수량
            delivery_type: 배송타입 (cold_1, cold_2, ambient)
            center_nm: 센터명
            center_cd: 센터코드
            store_id: 매장 코드

        Returns:
            저장된 레코드 ID
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        now = self._now()

        try:
            cursor.execute(
                """
                INSERT OR REPLACE INTO receiving_history
                (receiving_date, receiving_time, chit_no, item_cd, item_nm, mid_cd,
                 order_date, order_qty, receiving_qty, delivery_type, center_nm, center_cd,
                 store_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (receiving_date, receiving_time, chit_no, item_cd, item_nm, mid_cd,
                 order_date, order_qty, receiving_qty, delivery_type, center_nm, center_cd,
                 store_id, now)
            )

            record_id = cursor.lastrowid
            conn.commit()
            return record_id

        finally:
            conn.close()

    def save_bulk_receiving(self, records: List[Dict[str, Any]], store_id: Optional[str] = None) -> Dict[str, int]:
        """
        입고 정보 일괄 저장

        Args:
            records: 입고 데이터 리스트
            store_id: 매장 코드

        Returns:
            {"total": N, "new": N, "updated": N}
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        now = self._now()

        stats = {"total": 0, "new": 0, "updated": 0}
        sf, sp = self._store_filter(None, store_id)

        try:
            for record in records:
                # 기존 데이터 확인
                cursor.execute(
                    f"""
                    SELECT id FROM receiving_history
                    WHERE receiving_date = ? AND item_cd = ? AND chit_no = ? {sf}
                    """,
                    (record.get('receiving_date'), record.get('item_cd'), record.get('chit_no')) + sp
                )
                existing = cursor.fetchone()

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO receiving_history
                    (receiving_date, receiving_time, chit_no, item_cd, item_nm, mid_cd,
                     order_date, order_qty, receiving_qty, delivery_type, center_nm, center_cd,
                     store_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.get('receiving_date'),
                        record.get('receiving_time'),
                        record.get('chit_no'),
                        record.get('item_cd'),
                        record.get('item_nm'),
                        record.get('mid_cd'),
                        record.get('order_date'),
                        record.get('order_qty', 0),
                        record.get('receiving_qty', 0),
                        record.get('delivery_type'),
                        record.get('center_nm'),
                        record.get('center_cd'),
                        store_id,
                        now
                    )
                )

                stats["total"] += 1
                if existing:
                    stats["updated"] += 1
                else:
                    stats["new"] += 1

            conn.commit()
            return stats

        finally:
            conn.close()

    def get_receiving_by_date(self, receiving_date: str, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        특정 날짜의 입고 내역 조회

        Args:
            receiving_date: 입고일 (YYYY-MM-DD)
            store_id: 매장 코드

        Returns:
            입고 내역 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)

            cursor.execute(
                f"""
                SELECT * FROM receiving_history
                WHERE receiving_date = ? {sf}
                ORDER BY receiving_time, item_nm
                """,
                (receiving_date,) + sp
            )

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_pending_receiving(self, item_cd: Optional[str] = None, since_date: Optional[str] = None, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        미입고 상품 조회 (발주했으나 입고되지 않은 상품)

        발주 이력(order_tracking)에서 입고 이력(receiving_history)을 빼서 미입고 파악

        Args:
            item_cd: 상품코드 (None이면 전체)
            since_date: 조회 시작일 (기본: 7일 전)
            store_id: 매장 코드

        Returns:
            미입고 상품 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf_ot, sp_ot = self._store_filter("ot", store_id)
            sf_rh, sp_rh = self._store_filter("rh", store_id)

            if since_date is None:
                from datetime import timedelta
                since_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

            # LEFT JOIN 조건에 store_id 매칭 추가
            store_join = "AND rh.store_id = ot.store_id" if store_id else ""

            if item_cd:
                cursor.execute(
                    f"""
                    SELECT
                        ot.item_cd,
                        ot.item_nm,
                        ot.order_date,
                        ot.order_qty,
                        COALESCE(SUM(rh.receiving_qty), 0) as received_qty,
                        ot.order_qty - COALESCE(SUM(rh.receiving_qty), 0) as pending_qty
                    FROM order_tracking ot
                    LEFT JOIN receiving_history rh
                        ON ot.item_cd = rh.item_cd
                        AND ot.order_date = rh.order_date
                        {store_join}
                    WHERE ot.item_cd = ?
                    AND ot.order_date >= ?
                    AND ot.status IN ('ordered', 'arrived')
                    {sf_ot}
                    GROUP BY ot.item_cd, ot.order_date
                    HAVING pending_qty > 0
                    ORDER BY ot.order_date
                    """,
                    (item_cd, since_date) + sp_ot
                )
            else:
                cursor.execute(
                    f"""
                    SELECT
                        ot.item_cd,
                        ot.item_nm,
                        ot.order_date,
                        ot.order_qty,
                        COALESCE(SUM(rh.receiving_qty), 0) as received_qty,
                        ot.order_qty - COALESCE(SUM(rh.receiving_qty), 0) as pending_qty
                    FROM order_tracking ot
                    LEFT JOIN receiving_history rh
                        ON ot.item_cd = rh.item_cd
                        AND ot.order_date = rh.order_date
                        {store_join}
                    WHERE ot.order_date >= ?
                    AND ot.status IN ('ordered', 'arrived')
                    {sf_ot}
                    GROUP BY ot.item_cd, ot.order_date
                    HAVING pending_qty > 0
                    ORDER BY ot.order_date
                    """,
                    (since_date,) + sp_ot
                )

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_pending_qty_sum(self, item_cd: str, store_id: Optional[str] = None) -> int:
        """
        상품의 미입고 수량 합계 조회

        Args:
            item_cd: 상품코드
            store_id: 매장 코드

        Returns:
            미입고 수량 합계
        """
        pending_list = self.get_pending_receiving(item_cd=item_cd, store_id=store_id)
        return sum(item.get('pending_qty', 0) for item in pending_list)

    def get_receiving_summary(self, days: int = 7, store_id: Optional[str] = None) -> Dict[str, Any]:
        """
        입고 현황 요약

        Args:
            days: 조회 기간 (일)
            store_id: 매장 코드

        Returns:
            입고 현황 요약
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)

            from datetime import timedelta
            since_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

            # 일별 입고 현황
            cursor.execute(
                f"""
                SELECT
                    receiving_date,
                    COUNT(DISTINCT item_cd) as item_count,
                    SUM(receiving_qty) as total_qty,
                    COUNT(DISTINCT delivery_type) as delivery_types
                FROM receiving_history
                WHERE receiving_date >= ? {sf}
                GROUP BY receiving_date
                ORDER BY receiving_date DESC
                """,
                (since_date,) + sp
            )

            daily_summary = [dict(row) for row in cursor.fetchall()]

            # 배송타입별 현황
            cursor.execute(
                f"""
                SELECT
                    delivery_type,
                    COUNT(*) as count,
                    SUM(receiving_qty) as total_qty
                FROM receiving_history
                WHERE receiving_date >= ? {sf}
                GROUP BY delivery_type
                """,
                (since_date,) + sp
            )

            by_delivery_type = [dict(row) for row in cursor.fetchall()]

            return {
                "period_days": days,
                "since_date": since_date,
                "daily_summary": daily_summary,
                "by_delivery_type": by_delivery_type
            }
        finally:
            conn.close()

    def get_collected_dates(self, days: int = 30) -> List[str]:
        """
        수집된 입고일 목록 조회

        Args:
            days: 조회 기간 (일)

        Returns:
            수집된 입고일 목록 (YYYY-MM-DD)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            from datetime import timedelta
            since_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

            cursor.execute(
                """
                SELECT DISTINCT receiving_date
                FROM receiving_history
                WHERE receiving_date >= ?
                ORDER BY receiving_date
                """,
                (since_date,)
            )

            dates = [row[0] for row in cursor.fetchall()]
            return dates
        finally:
            conn.close()

    def update_order_tracking_receiving(self, item_cd: str, order_date: str, receiving_qty: int, arrival_time: Optional[str] = None, store_id: Optional[str] = None) -> int:
        """
        order_tracking 테이블의 실제 입고 정보 업데이트

        Args:
            item_cd: 상품코드
            order_date: 발주일
            receiving_qty: 실제 입고수량
            arrival_time: 실제 도착시간
            store_id: 매장 코드

        Returns:
            업데이트된 행 수
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()
            sf, sp = self._store_filter(None, store_id)

            if arrival_time:
                cursor.execute(
                    f"""
                    UPDATE order_tracking
                    SET actual_receiving_qty = ?,
                        actual_arrival_time = ?,
                        status = CASE WHEN status = 'ordered' THEN 'arrived' ELSE status END,
                        updated_at = ?
                    WHERE item_cd = ? AND order_date = ? {sf}
                    """,
                    (receiving_qty, arrival_time, now, item_cd, order_date) + sp
                )
            else:
                cursor.execute(
                    f"""
                    UPDATE order_tracking
                    SET actual_receiving_qty = ?,
                        status = CASE WHEN status = 'ordered' THEN 'arrived' ELSE status END,
                        updated_at = ?
                    WHERE item_cd = ? AND order_date = ? {sf}
                    """,
                    (receiving_qty, now, item_cd, order_date) + sp
                )

            updated = cursor.rowcount
            conn.commit()
            return updated
        finally:
            conn.close()

    def get_receiving_pattern_stats_batch(
        self,
        store_id: Optional[str] = None,
        days: int = 30
    ) -> Dict[str, Dict[str, float]]:
        """
        매장 전체 상품의 입고 패턴 통계 일괄 조회 (ML 피처용)

        1회 쿼리로 전체 상품의 리드타임/숏배송/빈도를 조회하여
        상품별 개별 쿼리를 피한다.

        Args:
            store_id: 매장 코드
            days: 조회 기간 (일, 기본 30일)

        Returns:
            {item_cd: {
                "lead_time_avg": float,        # 평균 리드타임 (일)
                "lead_time_std": float,         # 리드타임 표준편차
                "short_delivery_rate": float,   # 숏배송율 (0~1)
                "delivery_frequency": int,      # 최근 14일 입고 횟수
                "total_records": int,           # 총 레코드 수
            }}
        """
        from collections import defaultdict
        import math

        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)

            # (A) 개별 lead_time + short 플래그 조회 (SQLite POWER 미지원 대비)
            cursor.execute(
                f"""
                SELECT
                    item_cd,
                    julianday(receiving_date) - julianday(order_date) as lead_time,
                    CASE WHEN receiving_qty < order_qty AND order_qty > 0 THEN 1 ELSE 0 END as is_short
                FROM receiving_history
                WHERE receiving_date >= date('now', '-' || ? || ' days')
                {sf}
                ORDER BY item_cd
                """,
                (days,) + sp
            )

            # Python에서 item_cd별 집계
            item_lead_times = defaultdict(list)
            item_short_counts = defaultdict(int)
            item_total_counts = defaultdict(int)

            for row in cursor.fetchall():
                item_cd = row[0]
                lt = float(row[1]) if row[1] is not None else 0.0
                is_short = int(row[2])

                item_lead_times[item_cd].append(lt)
                item_short_counts[item_cd] += is_short
                item_total_counts[item_cd] += 1

            # (B) 최근 14일 입고 빈도
            cursor.execute(
                f"""
                SELECT
                    item_cd,
                    COUNT(DISTINCT receiving_date) as delivery_frequency
                FROM receiving_history
                WHERE receiving_date >= date('now', '-14 days')
                {sf}
                GROUP BY item_cd
                """,
                sp
            )

            freq_map = {}
            for row in cursor.fetchall():
                freq_map[row[0]] = int(row[1])

            # 결과 조합
            result = {}
            for item_cd, lt_list in item_lead_times.items():
                total = item_total_counts[item_cd]
                avg_lt = sum(lt_list) / len(lt_list) if lt_list else 0.0
                std_lt = 0.0
                if len(lt_list) >= 3 and avg_lt > 0:
                    variance = sum((lt - avg_lt) ** 2 for lt in lt_list) / len(lt_list)
                    std_lt = math.sqrt(variance)

                short_rate = item_short_counts[item_cd] / max(total, 1)

                result[item_cd] = {
                    "lead_time_avg": round(avg_lt, 2),
                    "lead_time_std": round(std_lt, 2),
                    "short_delivery_rate": round(short_rate, 3),
                    "delivery_frequency": freq_map.get(item_cd, 0),
                    "total_records": total,
                }

            return result
        except Exception as e:
            logger.warning(f"[입고패턴] 배치 통계 조회 실패: {e}")
            return {}
        finally:
            conn.close()
