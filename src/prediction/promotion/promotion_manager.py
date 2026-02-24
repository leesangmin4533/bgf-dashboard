"""
행사 정보 관리 모듈

DB에 저장된 행사 정보를 조회하고 관리
"""

import sqlite3
from typing import Any, Optional, List, Dict
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from pathlib import Path

from src.settings.constants import DEFAULT_STORE_ID


@dataclass
class PromotionStatus:
    """상품의 행사 상태"""
    item_cd: str
    item_nm: str

    # 현재 상태
    current_promo: Optional[str]        # '1+1', '2+1', None
    current_end_date: Optional[str]     # 종료일
    days_until_end: Optional[int]       # 종료까지 남은 일수

    # 다음 상태
    next_promo: Optional[str]           # 다음 행사
    next_start_date: Optional[str]      # 다음 시작일

    # 변경 정보
    will_change: bool                   # 행사 변경 예정 여부
    change_type: Optional[str]          # 'end', 'start', 'change'

    # 판매 통계
    normal_avg: float                   # 평시 일평균
    promo_avg: float                    # 행사 시 일평균
    promo_multiplier: float             # 행사 배율


class PromotionManager:
    """
    행사 정보 관리자

    사용법:
        manager = PromotionManager()

        # 상품별 행사 상태 조회
        status = manager.get_promotion_status("8801234567890")

        # 종료 임박 행사 조회
        ending_soon = manager.get_ending_promotions(days=3)

        # 행사 배율 조회
        multiplier = manager.get_promo_multiplier("8801234567890", "1+1")
    """

    def __init__(self, db_path: Optional[str] = None, store_id: Optional[str] = None) -> None:
        if db_path is None:
            db_path = Path(__file__).parent.parent.parent.parent / "data" / "bgf_sales.db"
        self.db_path = str(db_path)
        self.store_id = store_id

    def _get_connection(self, timeout: int = 30) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        if self.store_id:
            from src.infrastructure.database.connection import attach_common_with_views
            attach_common_with_views(conn, self.store_id)
        return conn

    def get_promotion_status(self, item_cd: str) -> Optional[PromotionStatus]:
        """
        상품의 현재 행사 상태 조회

        Args:
            item_cd: 상품코드

        Returns:
            PromotionStatus 객체
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        today = date.today().strftime('%Y-%m-%d')

        # 현재 행사 조회
        from src.db.store_query import store_filter
        sf, sp = store_filter(None, self.store_id)
        cursor.execute(f"""
            SELECT promo_type, start_date, end_date
            FROM promotions
            WHERE item_cd = ?
              AND start_date <= ?
              AND end_date >= ?
              AND is_active = 1
              {sf}
            ORDER BY start_date DESC
            LIMIT 1
        """, (item_cd, today, today) + sp)
        current = cursor.fetchone()

        # 다음 행사 조회
        cursor.execute(f"""
            SELECT promo_type, start_date, end_date
            FROM promotions
            WHERE item_cd = ?
              AND start_date > ?
              {sf}
            ORDER BY start_date ASC
            LIMIT 1
        """, (item_cd, today) + sp)
        next_promo = cursor.fetchone()

        # 상품명 조회
        cursor.execute(
            "SELECT item_nm FROM products WHERE item_cd = ?",
            (item_cd,)
        )
        product = cursor.fetchone()
        item_nm = product['item_nm'] if product else ""

        conn.close()

        # 판매 통계 조회
        stats = self._get_promotion_stats(item_cd)

        # 남은 일수 계산
        days_until_end = None
        if current and current['end_date']:
            end = datetime.strptime(current['end_date'], '%Y-%m-%d').date()
            days_until_end = (end - date.today()).days

        # 변경 여부 판단
        will_change = False
        change_type = None

        if current and not next_promo:
            will_change = True
            change_type = 'end'
        elif current and next_promo and current['promo_type'] != next_promo['promo_type']:
            will_change = True
            change_type = 'change'
        elif not current and next_promo:
            will_change = True
            change_type = 'start'

        return PromotionStatus(
            item_cd=item_cd,
            item_nm=item_nm,
            current_promo=current['promo_type'] if current else None,
            current_end_date=current['end_date'] if current else None,
            days_until_end=days_until_end,
            next_promo=next_promo['promo_type'] if next_promo else None,
            next_start_date=next_promo['start_date'] if next_promo else None,
            will_change=will_change,
            change_type=change_type,
            normal_avg=stats.get('normal_avg', 0),
            promo_avg=stats.get('promo_avg', 0),
            promo_multiplier=stats.get('multiplier', 1.0),
        )

    def get_ending_promotions(self, days: int = 3) -> List[PromotionStatus]:
        """
        종료 임박 행사 상품 조회

        Args:
            days: 며칠 이내 종료 예정

        Returns:
            PromotionStatus 리스트 (종료일 임박 순)
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        today = date.today()
        target_date = (today + timedelta(days=days)).strftime('%Y-%m-%d')
        today_str = today.strftime('%Y-%m-%d')

        from src.db.store_query import store_filter
        sf, sp = store_filter(None, self.store_id)
        cursor.execute(f"""
            SELECT DISTINCT item_cd
            FROM promotions
            WHERE end_date BETWEEN ? AND ?
              AND is_active = 1
              {sf}
            ORDER BY end_date ASC
        """, (today_str, target_date) + sp)

        rows = cursor.fetchall()
        conn.close()

        results = []
        for row in rows:
            status = self.get_promotion_status(row['item_cd'])
            if status and status.current_promo:
                results.append(status)

        return results

    def get_starting_promotions(self, days: int = 3) -> List[PromotionStatus]:
        """
        시작 예정 행사 상품 조회

        Args:
            days: 며칠 이내 시작 예정

        Returns:
            PromotionStatus 리스트
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        today = date.today()
        target_date = (today + timedelta(days=days)).strftime('%Y-%m-%d')
        today_str = today.strftime('%Y-%m-%d')

        from src.db.store_query import store_filter
        sf, sp = store_filter(None, self.store_id)
        cursor.execute(f"""
            SELECT DISTINCT item_cd
            FROM promotions
            WHERE start_date BETWEEN ? AND ?
              {sf}
            ORDER BY start_date ASC
        """, (today_str, target_date) + sp)

        rows = cursor.fetchall()
        conn.close()

        results = []
        for row in rows:
            status = self.get_promotion_status(row['item_cd'])
            if status:
                results.append(status)

        return results

    def get_promo_multiplier(
        self,
        item_cd: str,
        promo_type: str
    ) -> float:
        """
        행사 배율 조회

        Args:
            item_cd: 상품코드
            promo_type: 행사 유형 ('1+1', '2+1')

        Returns:
            배율 (예: 3.0 = 평시 대비 3배)
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # 상품별 배율 조회
        from src.db.store_query import store_filter
        sf, sp = store_filter(None, self.store_id)
        cursor.execute(f"""
            SELECT multiplier
            FROM promotion_stats
            WHERE item_cd = ? AND promo_type = ?
              {sf}
        """, (item_cd, promo_type) + sp)
        stats = cursor.fetchone()
        conn.close()

        if stats and stats['multiplier']:
            return stats['multiplier']

        # 상품별 데이터 없으면 카테고리 기본 배율
        return self._get_default_multiplier(item_cd, promo_type)

    def _get_default_multiplier(self, item_cd: str, promo_type: str) -> float:
        """카테고리별 기본 배율

        Args:
            item_cd: 상품코드
            promo_type: 행사 유형 (1+1, 2+1)

        Returns:
            카테고리별 기본 행사 배율
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # 상품 카테고리 조회
        cursor.execute(
            "SELECT mid_cd FROM products WHERE item_cd = ?",
            (item_cd,)
        )
        product = cursor.fetchone()
        conn.close()

        mid_cd = product['mid_cd'] if product else ""

        # 카테고리별 기본 배율
        default_multipliers = {
            # 음료
            '010': {'1+1': 3.5, '2+1': 2.5},
            '011': {'1+1': 3.5, '2+1': 2.5},

            # 과자
            '020': {'1+1': 3.0, '2+1': 2.2},
            '021': {'1+1': 3.0, '2+1': 2.2},

            # 라면
            '006': {'1+1': 2.5, '2+1': 2.0},
            '032': {'1+1': 2.5, '2+1': 2.0},

            # 유제품
            '040': {'1+1': 4.0, '2+1': 2.8},

            # 도시락/김밥
            '001': {'1+1': 2.0, '2+1': 1.5},
            '002': {'1+1': 2.0, '2+1': 1.5},
            '003': {'1+1': 2.0, '2+1': 1.5},

            # 맥주
            '049': {'1+1': 3.0, '2+1': 2.0},

            # 기본값
            'default': {'1+1': 2.5, '2+1': 1.8},
        }

        category_rates = default_multipliers.get(mid_cd, default_multipliers['default'])
        return category_rates.get(promo_type, 1.0)

    def _get_promotion_stats(self, item_cd: str) -> Dict[str, float]:
        """상품별 행사 통계 조회

        Args:
            item_cd: 상품코드

        Returns:
            {"normal_avg": 평시 일평균, "promo_avg": 행사 일평균, "multiplier": 배율}
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        result = {
            'normal_avg': 0,
            'promo_avg': 0,
            'multiplier': 1.0
        }

        # 평시 판매량
        from src.db.store_query import store_filter
        sf, sp = store_filter(None, self.store_id)
        cursor.execute(f"""
            SELECT avg_daily_sales FROM promotion_stats
            WHERE item_cd = ? AND promo_type = 'normal'
              {sf}
        """, (item_cd,) + sp)
        normal = cursor.fetchone()

        if normal:
            result['normal_avg'] = normal['avg_daily_sales'] or 0

        # 행사 시 판매량 (1+1 또는 2+1 중 최신)
        cursor.execute(f"""
            SELECT avg_daily_sales, multiplier, promo_type
            FROM promotion_stats
            WHERE item_cd = ? AND promo_type IN ('1+1', '2+1')
              {sf}
            ORDER BY last_calculated DESC
            LIMIT 1
        """, (item_cd,) + sp)
        promo = cursor.fetchone()

        if promo:
            result['promo_avg'] = promo['avg_daily_sales'] or 0
            result['multiplier'] = promo['multiplier'] or 1.0

        conn.close()
        return result

    def calculate_promotion_stats(self, item_cd: str) -> None:
        """
        상품의 행사별 판매 통계 계산 및 저장

        최근 90일 판매 데이터를 행사/비행사 기간으로 분류하여
        평시 일평균, 행사 일평균, 배율을 계산 후 promotion_stats 테이블에 저장한다.

        Args:
            item_cd: 상품코드
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        try:
            from src.db.store_query import store_filter
            sf, sp = store_filter(None, self.store_id)
            # 행사 기간 조회
            cursor.execute(f"""
                SELECT promo_type, start_date, end_date
                FROM promotions
                WHERE item_cd = ?
                  {sf}
            """, (item_cd,) + sp)
            promos = cursor.fetchall()

            promo_periods = []
            for promo in promos:
                promo_periods.append({
                    'type': promo['promo_type'],
                    'start': promo['start_date'],
                    'end': promo['end_date']
                })

            # 전체 판매 데이터 조회 (daily_sales - store_id 필터 적용)
            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()
            cursor.execute(f"""
                SELECT sales_date, sale_qty
                FROM daily_sales
                WHERE item_cd = ?
                AND sales_date >= date('now', '-90 days')
                {store_filter}
                ORDER BY sales_date
            """, (item_cd,) + store_params)
            sales = cursor.fetchall()

            # 행사별 분류
            normal_sales = []
            promo_sales = {'1+1': [], '2+1': []}

            for sale in sales:
                sale_date = sale['sales_date']
                qty = sale['sale_qty']

                is_promo = False
                for period in promo_periods:
                    if period['start'] <= sale_date <= period['end']:
                        promo_type = period['type']
                        if promo_type in promo_sales:
                            promo_sales[promo_type].append(qty)
                        is_promo = True
                        break

                if not is_promo:
                    normal_sales.append(qty)

            # 통계 계산 및 저장
            if normal_sales:
                avg = sum(normal_sales) / len(normal_sales)
                store_val = self.store_id or DEFAULT_STORE_ID
                cursor.execute("""
                    INSERT OR REPLACE INTO promotion_stats
                    (store_id, item_cd, promo_type, avg_daily_sales, total_days, total_sales, last_calculated)
                    VALUES (?, ?, 'normal', ?, ?, ?, ?)
                """, (store_val, item_cd, avg, len(normal_sales), sum(normal_sales), now))

            for promo_type, sales_list in promo_sales.items():
                if sales_list:
                    avg = sum(sales_list) / len(sales_list)
                    normal_avg = sum(normal_sales) / len(normal_sales) if normal_sales else 1
                    multiplier = avg / normal_avg if normal_avg > 0 else 1.0

                    cursor.execute("""
                        INSERT OR REPLACE INTO promotion_stats
                        (store_id, item_cd, promo_type, avg_daily_sales, total_days, total_sales, multiplier, last_calculated)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (store_val, item_cd, promo_type, avg, len(sales_list), sum(sales_list), multiplier, now))

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_all_active_promotions(self) -> List[Dict[str, Any]]:
        """현재 진행 중인 모든 행사 조회

        Returns:
            진행 중인 행사 정보 리스트 (종료일 오름차순)
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        today = date.today().strftime('%Y-%m-%d')

        from src.db.store_query import store_filter
        sf, sp = store_filter("p", self.store_id)
        cursor.execute(f"""
            SELECT p.item_cd, p.item_nm, p.promo_type, p.start_date, p.end_date,
                   pr.item_nm as product_nm
            FROM promotions p
            LEFT JOIN products pr ON p.item_cd = pr.item_cd
            WHERE p.start_date <= ?
              AND p.end_date >= ?
              AND p.is_active = 1
              {sf}
            ORDER BY p.end_date ASC
        """, (today, today) + sp)

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]


# =============================================================================
# 테스트
# =============================================================================
if __name__ == "__main__":
    manager = PromotionManager()

    # 종료 임박 행사 조회
    print("\n[종료 임박 행사 (3일 이내)]")
    ending = manager.get_ending_promotions(days=3)
    for status in ending:
        print(f"  {status.item_nm[:15]}: {status.current_promo} D-{status.days_until_end}")

    # 시작 예정 행사 조회
    print("\n[시작 예정 행사 (3일 이내)]")
    starting = manager.get_starting_promotions(days=3)
    for status in starting:
        print(f"  {status.item_nm[:15]}: {status.next_promo} 시작 {status.next_start_date}")
