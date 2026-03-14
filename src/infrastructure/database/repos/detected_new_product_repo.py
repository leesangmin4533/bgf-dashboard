"""
DetectedNewProductRepository — 입고 시 감지된 신제품 이력 저장소

센터매입조회에서 입고 확정 시 DB 미등록 상품을 기록
"""

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DetectedNewProductRepository(BaseRepository):
    """신제품 감지 이력 저장소"""

    db_type = "store"

    def save(
        self,
        item_cd: str,
        item_nm: str,
        mid_cd: str,
        mid_cd_source: str,
        first_receiving_date: str,
        receiving_qty: int,
        order_unit_qty: int = 1,
        center_cd: Optional[str] = None,
        center_nm: Optional[str] = None,
        cust_nm: Optional[str] = None,
        registered_to_products: bool = False,
        registered_to_details: bool = False,
        registered_to_inventory: bool = False,
        store_id: Optional[str] = None,
    ) -> int:
        """신제품 감지 이력 저장 (UPSERT: item_cd + first_receiving_date 기준)

        Args:
            item_cd: 상품코드
            item_nm: 상품명
            mid_cd: 추정 중분류 코드
            mid_cd_source: mid_cd 출처 ('fallback' | 'unknown')
            first_receiving_date: 최초 입고일 (YYYY-MM-DD)
            receiving_qty: 입고 수량
            order_unit_qty: 발주 단위 수량
            center_cd: 센터 코드
            center_nm: 센터명
            cust_nm: 거래처명
            registered_to_products: products 등록 여부
            registered_to_details: product_details 등록 여부
            registered_to_inventory: realtime_inventory 등록 여부
            store_id: 매장 코드

        Returns:
            저장된 레코드 ID
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()

            cursor.execute(
                """
                INSERT INTO detected_new_products
                (item_cd, item_nm, mid_cd, mid_cd_source,
                 first_receiving_date, receiving_qty, order_unit_qty,
                 center_cd, center_nm, cust_nm,
                 registered_to_products, registered_to_details, registered_to_inventory,
                 detected_at, store_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_cd, first_receiving_date) DO UPDATE SET
                    item_nm = excluded.item_nm,
                    receiving_qty = excluded.receiving_qty,
                    registered_to_products = excluded.registered_to_products,
                    registered_to_details = excluded.registered_to_details,
                    registered_to_inventory = excluded.registered_to_inventory
                """,
                (
                    item_cd, item_nm, mid_cd, mid_cd_source,
                    first_receiving_date, receiving_qty, order_unit_qty,
                    center_cd, center_nm, cust_nm,
                    1 if registered_to_products else 0,
                    1 if registered_to_details else 0,
                    1 if registered_to_inventory else 0,
                    now, store_id,
                )
            )

            record_id = cursor.lastrowid
            conn.commit()
            return record_id
        finally:
            conn.close()

    def get_by_date_range(
        self, start_date: str, end_date: str, store_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """기간별 감지 이력 조회

        Args:
            start_date: 시작일 (YYYY-MM-DD)
            end_date: 종료일 (YYYY-MM-DD)
            store_id: 매장 코드 (선택)

        Returns:
            감지 이력 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            query = """
                SELECT * FROM detected_new_products
                WHERE first_receiving_date BETWEEN ? AND ?
            """
            params: list = [start_date, end_date]

            if store_id:
                query += " AND store_id = ?"
                params.append(store_id)

            query += " ORDER BY detected_at DESC"
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_unregistered(self, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """등록 미완료 상품 조회 (products/details/inventory 중 하나라도 미등록)

        Args:
            store_id: 매장 코드 (선택)

        Returns:
            미등록 상품 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            query = """
                SELECT * FROM detected_new_products
                WHERE (registered_to_products = 0
                    OR registered_to_details = 0
                    OR registered_to_inventory = 0)
            """
            params: list = []

            if store_id:
                query += " AND store_id = ?"
                params.append(store_id)

            query += " ORDER BY detected_at DESC"
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_recent(
        self, days: int = 30, store_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """최근 N일 감지 이력

        Args:
            days: 조회 기간 (일)
            store_id: 매장 코드 (선택)

        Returns:
            감지 이력 목록
        """
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")
        return self.get_by_date_range(start_date, end_date, store_id)

    def mark_registered(
        self, item_cd: str, field: str, store_id: Optional[str] = None
    ) -> None:
        """등록 완료 마킹

        Args:
            item_cd: 상품코드
            field: 'products' | 'details' | 'inventory'
            store_id: 매장 코드 (선택)
        """
        column_map = {
            "products": "registered_to_products",
            "details": "registered_to_details",
            "inventory": "registered_to_inventory",
        }

        column = column_map.get(field)
        if not column:
            logger.warning(f"잘못된 필드: {field}")
            return

        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            query = f"UPDATE detected_new_products SET {column} = 1 WHERE item_cd = ?"
            params: list = [item_cd]

            if store_id:
                query += " AND store_id = ?"
                params.append(store_id)

            cursor.execute(query, params)
            conn.commit()
        finally:
            conn.close()

    # ── lifecycle 확장 (v46) ──────────────────────────────

    def get_by_lifecycle_status(
        self, statuses: List[str], store_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """lifecycle_status 기준 조회

        Args:
            statuses: 조회할 상태 리스트 (예: ['detected', 'monitoring'])
            store_id: 매장 코드 (선택)

        Returns:
            해당 상태의 상품 목록
        """
        if not statuses:
            return []

        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            placeholders = ", ".join("?" for _ in statuses)
            query = f"""
                SELECT * FROM detected_new_products
                WHERE lifecycle_status IN ({placeholders})
            """
            params: list = list(statuses)

            if store_id:
                query += " AND store_id = ?"
                params.append(store_id)

            query += " ORDER BY detected_at DESC"
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def update_lifecycle(
        self,
        item_cd: str,
        status: Optional[str] = None,
        monitoring_start: Optional[str] = None,
        monitoring_end: Optional[str] = None,
        total_sold_qty: Optional[int] = None,
        sold_days: Optional[int] = None,
        similar_item_avg: Optional[float] = None,
        store_id: Optional[str] = None,
    ) -> None:
        """lifecycle 상태 + 집계 업데이트

        Args:
            item_cd: 상품코드
            status: 새 lifecycle_status
            monitoring_start: 모니터링 시작일
            monitoring_end: 모니터링 종료일
            total_sold_qty: 총 판매수량
            sold_days: 판매일수
            similar_item_avg: 유사 상품 일평균
            store_id: 매장 코드 (선택)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()

            set_parts = []
            params: list = []

            if status is not None:
                set_parts.extend(["lifecycle_status = ?", "status_changed_at = ?"])
                params.extend([status, now])

            if monitoring_start is not None:
                set_parts.append("monitoring_start_date = ?")
                params.append(monitoring_start)
            if monitoring_end is not None:
                set_parts.append("monitoring_end_date = ?")
                params.append(monitoring_end)
            if total_sold_qty is not None:
                set_parts.append("total_sold_qty = ?")
                params.append(total_sold_qty)
            if sold_days is not None:
                set_parts.append("sold_days = ?")
                params.append(sold_days)
            if similar_item_avg is not None:
                set_parts.append("similar_item_avg = ?")
                params.append(similar_item_avg)

            if not set_parts:
                return

            set_clause = ", ".join(set_parts)
            query = f"UPDATE detected_new_products SET {set_clause} WHERE item_cd = ?"
            params.append(item_cd)

            if store_id:
                query += " AND store_id = ?"
                params.append(store_id)

            cursor.execute(query, params)
            conn.commit()
        finally:
            conn.close()

    def get_monitoring_summary(
        self, store_id: Optional[str] = None
    ) -> Dict[str, int]:
        """모니터링 현황 요약 (상태별 건수)

        Args:
            store_id: 매장 코드 (선택)

        Returns:
            {"detected": 2, "monitoring": 5, "stable": 3, ...}
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            query = """
                SELECT lifecycle_status, COUNT(*) as cnt
                FROM detected_new_products
            """
            params: list = []

            if store_id:
                query += " WHERE store_id = ?"
                params.append(store_id)

            query += " GROUP BY lifecycle_status"
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return {row["lifecycle_status"]: row["cnt"] for row in rows}
        finally:
            conn.close()

    # ── settlement 확장 (v56) ──────────────────────────────

    def update_settlement(
        self,
        item_cd: str,
        settlement_score: float,
        settlement_verdict: str,
        settlement_date: str,
        analysis_window_days: Optional[int] = None,
        extension_count: Optional[int] = None,
        lifecycle_status: Optional[str] = None,
        store_id: Optional[str] = None,
    ) -> None:
        """안착 판정 결과 업데이트

        Args:
            item_cd: 상품코드
            settlement_score: 판정 점수 (0~100)
            settlement_verdict: 'settled' | 'failed' | 'extended'
            settlement_date: 판정일 (YYYY-MM-DD)
            analysis_window_days: 분석 윈도우 (일)
            extension_count: 연장 횟수
            lifecycle_status: lifecycle 동시 업데이트 (선택)
            store_id: 매장 코드
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()

            set_parts = [
                "settlement_score = ?",
                "settlement_verdict = ?",
                "settlement_date = ?",
                "settlement_checked_at = ?",
            ]
            params: list = [settlement_score, settlement_verdict, settlement_date, now]

            if analysis_window_days is not None:
                set_parts.append("analysis_window_days = ?")
                params.append(analysis_window_days)
            if extension_count is not None:
                set_parts.append("extension_count = ?")
                params.append(extension_count)
            if lifecycle_status is not None:
                set_parts.append("lifecycle_status = ?")
                set_parts.append("status_changed_at = ?")
                params.extend([lifecycle_status, now])

            set_clause = ", ".join(set_parts)
            query = f"UPDATE detected_new_products SET {set_clause} WHERE item_cd = ?"
            params.append(item_cd)

            sid = store_id or self.store_id
            if sid:
                query += " AND store_id = ?"
                params.append(sid)

            cursor.execute(query, params)
            conn.commit()
        finally:
            conn.close()

    def get_settlement_due(
        self, today: str, store_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """판정 기한 도달한 제품 조회

        조건:
            - lifecycle_status IN ('monitoring', 'stable', 'slow_start')
            - settlement_verdict IS NULL 또는 'extended'
            - first_receiving_date + analysis_window_days <= today

        Args:
            today: 오늘 날짜 (YYYY-MM-DD)
            store_id: 매장 코드

        Returns:
            판정 대상 제품 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            query = """
                SELECT * FROM detected_new_products
                WHERE lifecycle_status IN ('monitoring', 'stable', 'slow_start')
                  AND (settlement_verdict IS NULL OR settlement_verdict = 'extended')
                  AND analysis_window_days IS NOT NULL
                  AND date(first_receiving_date, '+' || analysis_window_days || ' days') <= ?
            """
            params: list = [today]

            sid = store_id or self.store_id
            if sid:
                query += " AND store_id = ?"
                params.append(sid)

            query += " ORDER BY first_receiving_date ASC"
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_count_by_date(
        self, target_date: str, store_id: Optional[str] = None
    ) -> int:
        """특정 날짜 감지 건수

        Args:
            target_date: 대상일 (YYYY-MM-DD)
            store_id: 매장 코드 (선택)

        Returns:
            감지 건수
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            query = "SELECT COUNT(*) FROM detected_new_products WHERE first_receiving_date = ?"
            params: list = [target_date]

            if store_id:
                query += " AND store_id = ?"
                params.append(store_id)

            cursor.execute(query, params)
            row = cursor.fetchone()
            return row[0] if row else 0
        finally:
            conn.close()
