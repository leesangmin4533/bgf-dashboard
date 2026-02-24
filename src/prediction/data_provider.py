"""
예측 데이터 제공자 (DB/인프라 접근)

PredictionDataProvider: ImprovedPredictor에서 DB 접근 메서드 분리 (Phase 6-2)
- _get_connection, get_sales_history, get_product_info, get_current_stock 등
- 재고/미입고 캐시 관리
- realtime_inventory 연동
"""

import sqlite3
from datetime import datetime
from typing import Optional, Dict, List, Tuple

from src.utils.logger import get_logger
from src.infrastructure.database.repos import RealtimeInventoryRepository
from src.settings.constants import DEFAULT_STORE_ID

from .categories import FOOD_ANALYSIS_DAYS

logger = get_logger(__name__)


class PredictionDataProvider:
    """예측에 필요한 DB/인프라 데이터 접근 계층

    ImprovedPredictor에서 순수 데이터 조회 메서드를 분리하여
    단일 책임 원칙을 적용한다.
    """

    def __init__(
        self,
        db_path: str,
        store_id: str = DEFAULT_STORE_ID,
        use_db_inventory: bool = True,
    ) -> None:
        """
        Args:
            db_path: 데이터베이스 경로
            store_id: 점포 코드
            use_db_inventory: True면 realtime_inventory 테이블에서 재고/미입고 조회
        """
        self.db_path = str(db_path)
        self.store_id = store_id
        self._use_db_inventory = use_db_inventory
        self._inventory_repo = RealtimeInventoryRepository(store_id=self.store_id) if use_db_inventory else None
        self._pending_cache: Dict[str, int] = {}  # 미입고 수량 캐시
        self._stock_cache: Dict[str, int] = {}    # 실시간 재고 캐시 (BGF 시스템에서 조회)
        self._promo_cache: Optional[Dict[str, list]] = None  # 행사 기간 캐시

    def _get_connection(self, timeout: int = 30) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        if self.store_id:
            from src.infrastructure.database.connection import attach_common_with_views
            attach_common_with_views(conn, self.store_id)
        return conn

    def get_sales_history(
        self,
        item_cd: str,
        days: int = 7
    ) -> List[Tuple[str, int, Optional[int]]]:
        """상품의 최근 N일 판매 이력 조회 (달력일 기준)

        달력일 기준으로 최근 N일의 판매 이력을 반환한다.
        판매 기록이 없는 날은 (날짜, 0, None)으로 채워진다.

        - sale_qty=0, stock_qty=None : 레코드 없음 (데이터 미수집 또는 비판매일)
        - sale_qty=0, stock_qty=0   : 확인된 품절일 (imputation 대상)
        - sale_qty>0, stock_qty>0   : 정상 판매일

        Args:
            item_cd: 상품코드
            days: 조회 일수 (달력일 기준, 기본 7일)

        Returns:
            [(날짜, 판매량, 재고량|None), ...] 리스트 (최신 순)
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            cursor.execute("""
                WITH RECURSIVE dates(d) AS (
                    SELECT date('now', '-1 day')
                    UNION ALL
                    SELECT date(d, '-1 day')
                    FROM dates
                    WHERE d > date('now', '-' || ? || ' days')
                )
                SELECT
                    dates.d AS sales_date,
                    COALESCE(ds.sale_qty, 0) AS sale_qty,
                    ds.stock_qty AS stock_qty
                FROM dates
                LEFT JOIN daily_sales ds
                    ON ds.sales_date = dates.d
                    AND ds.item_cd = ?
                    AND ds.store_id = ?
                ORDER BY dates.d DESC
            """, (days, item_cd, self.store_id))

            result = cursor.fetchall()
            return [(row[0], row[1], row[2]) for row in result]
        finally:
            conn.close()

    def _get_data_span_days(self, item_cd: str) -> int:
        """상품의 전체 데이터 기간(달력일) 조회 -- ML 활성화 판단용

        첫 판매일부터 마지막 판매일까지의 달력일 수를 반환.
        0판매일도 포함된 연속 기간으로, ML 모델 활성화(30일/60일)
        조건 판단에 사용한다.

        Returns:
            달력일 수 (데이터 없으면 0)
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT CAST(
                    julianday(MAX(sales_date)) - julianday(MIN(sales_date)) + 1
                    AS INTEGER
                ) AS span_days
                FROM daily_sales
                WHERE item_cd = ? AND store_id = ?
            """, (item_cd, self.store_id))
            row = cursor.fetchone()
            return row[0] if row and row[0] else 0
        except Exception:
            return 0
        finally:
            conn.close()

    def get_product_info(self, item_cd: str) -> Optional[Dict[str, object]]:
        """상품 정보 조회

        Args:
            item_cd: 상품코드

        Returns:
            상품 정보 딕셔너리 또는 None
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    p.item_cd,
                    p.item_nm,
                    p.mid_cd,
                    pd.expiration_days,
                    pd.order_unit_qty,
                    pd.sell_price,
                    pd.margin_rate,
                    pd.lead_time_days,
                    pd.orderable_day
                FROM products p
                LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
                WHERE p.item_cd = ?
            """, (item_cd,))

            row = cursor.fetchone()

            if row:
                return {
                    "item_cd": row[0],
                    "item_nm": row[1],
                    "mid_cd": row[2],
                    "expiration_days": row[3] if row[3] is not None else 365,  # 없으면 1년으로
                    "order_unit_qty": row[4] if row[4] not in (None, 0) else 1,
                    "sell_price": row[5],
                    "margin_rate": row[6],
                    "lead_time_days": row[7] if row[7] is not None and row[7] > 0 else 1,
                    "orderable_day": row[8] if row[8] else "일월화수목금토",
                }
            return None
        finally:
            conn.close()

    def get_current_stock(self, item_cd: str) -> int:
        """현재 재고 조회 (가장 최근 데이터)

        Args:
            item_cd: 상품코드

        Returns:
            현재 재고 수량 (데이터 없으면 0)
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT stock_qty
                FROM daily_sales
                WHERE item_cd = ? AND store_id = ?
                ORDER BY sales_date DESC
                LIMIT 1
            """, (item_cd, self.store_id))

            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else 0
        finally:
            conn.close()

    def _get_disuse_rate(self, item_cd: str, days: int = FOOD_ANALYSIS_DAYS) -> float:
        """
        상품의 폐기율 조회 (inventory_batches 우선, daily_sales fallback)

        inventory_batches의 유통기한 역추적 데이터가 daily_sales.disuse_qty보다
        정확도가 높으므로 우선 사용하고, 배치 데이터가 없는 경우 기존 방식으로 fallback.

        Args:
            item_cd: 상품코드
            days: 조회 기간

        Returns:
            폐기율 (0.0 ~ 1.0)
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # 1차: inventory_batches 기반 (완료된 배치 = consumed + expired)
            try:
                cursor.execute("""
                    SELECT SUM(initial_qty) as total_initial,
                           SUM(CASE WHEN status = 'expired' THEN remaining_qty ELSE 0 END) as total_waste
                    FROM inventory_batches
                    WHERE item_cd = ? AND store_id = ?
                    AND status IN ('consumed', 'expired')
                """, (item_cd, self.store_id))

                row = cursor.fetchone()
                if row and row[0] and row[0] > 0:
                    total_initial = row[0]
                    total_waste = row[1] or 0
                    return total_waste / total_initial
            except Exception:
                # inventory_batches 테이블 미존재 시 fallback
                pass

            # 2차 fallback: daily_sales.disuse_qty 기반
            cursor.execute("""
                SELECT SUM(sale_qty), SUM(disuse_qty)
                FROM daily_sales
                WHERE item_cd = ? AND store_id = ?
                AND sales_date >= date('now', '-' || ? || ' days')
            """, (item_cd, self.store_id, days))

            row = cursor.fetchone()

            if row and ((row[0] and row[0] > 0) or (row[1] and row[1] > 0)):
                total_sales = row[0] or 0
                total_disuse = row[1] or 0
                total = total_sales + total_disuse
                return total_disuse / total if total > 0 else 0.0
            return 0.0
        finally:
            conn.close()

    def set_pending_cache(self, pending_data: Dict[str, int]) -> None:
        """미입고 수량 캐시 설정

        Args:
            pending_data: {상품코드: 미입고수량} 딕셔너리
        """
        self._pending_cache = pending_data
        logger.info(f"미입고 수량 캐시 설정: {len(pending_data)}개 상품")

    def clear_pending_cache(self) -> None:
        """미입고 수량 캐시 초기화"""
        self._pending_cache = {}

    def set_stock_cache(self, stock_data: Dict[str, int]) -> None:
        """실시간 재고 캐시 설정 (BGF 시스템에서 조회한 값)

        Args:
            stock_data: {상품코드: 재고수량} 딕셔너리
        """
        self._stock_cache = stock_data
        logger.info(f"실시간 재고 캐시 설정: {len(stock_data)}개 상품")

    def clear_stock_cache(self) -> None:
        """실시간 재고 캐시 초기화"""
        self._stock_cache = {}

    def get_inventory_from_db(self, item_cd: str) -> Optional[Dict[str, object]]:
        """
        DB(realtime_inventory)에서 재고/미입고 정보 조회

        Args:
            item_cd: 상품코드

        Returns:
            {
                'stock_qty': 현재 재고,
                'pending_qty': 미입고 수량,
                'is_available': 점포 취급 여부
            } 또는 None
        """
        if not self._use_db_inventory or not self._inventory_repo:
            return None
        return self._inventory_repo.get(item_cd)

    def get_unavailable_items_from_db(self) -> List[str]:
        """
        DB에서 점포 미취급 상품 코드 목록 조회

        Returns:
            미취급 상품코드 리스트
        """
        if not self._use_db_inventory or not self._inventory_repo:
            return []
        return self._inventory_repo.get_unavailable_items(store_id=self.store_id)

    def is_item_available(self, item_cd: str) -> bool:
        """
        상품이 점포에서 취급 가능한지 확인

        Args:
            item_cd: 상품코드

        Returns:
            True=취급 가능, False=미취급
        """
        if not self._use_db_inventory or not self._inventory_repo:
            return True  # DB 사용 안하면 모두 취급 가능으로 간주

        data = self._inventory_repo.get(item_cd)
        if data is None:
            return True  # DB에 없으면 취급 가능으로 간주
        return data.get('is_available', True)

    def load_inventory_to_cache(self) -> None:
        """
        DB의 모든 재고/미입고 데이터를 캐시에 로드

        realtime_inventory 테이블 데이터를 _stock_cache, _pending_cache에 로드
        """
        if not self._use_db_inventory or not self._inventory_repo:
            logger.info("DB 인벤토리 사용 안함, 캐시 로드 스킵")
            return

        all_data = self._inventory_repo.get_all(available_only=True, exclude_cut=True, store_id=self.store_id)
        stock_data = {}
        pending_data = {}

        for item in all_data:
            item_cd = item.get('item_cd')
            if item_cd:
                stock_data[item_cd] = item.get('stock_qty', 0)
                pending_data[item_cd] = item.get('pending_qty', 0)

        self._stock_cache = stock_data
        self._pending_cache = pending_data
        logger.info(f"DB에서 캐시 로드: 재고 {len(stock_data)}개, 미입고 {len(pending_data)}개")

    # ------------------------------------------------------------------
    # 행사 기간 조회 (WMA/Feature에서 행사 매출 필터링용)
    # ------------------------------------------------------------------

    def _load_promo_periods_cache(self) -> Dict[str, list]:
        """전체 상품의 행사 기간을 캐시로 로드

        Returns:
            {item_cd: [(start_date, end_date), ...]}
        """
        if self._promo_cache is not None:
            return self._promo_cache

        self._promo_cache = {}
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT item_cd, start_date, end_date
                FROM promotions
                WHERE is_active = 1
            """)
            for row in cursor.fetchall():
                self._promo_cache.setdefault(row[0], []).append((row[1], row[2]))
        except Exception:
            pass  # promotions 테이블 미존재 시 빈 캐시
        finally:
            conn.close()
        return self._promo_cache

    def is_promo_date(self, item_cd: str, date_str: str) -> bool:
        """특정 날짜가 해당 상품의 행사 기간인지 확인

        Args:
            item_cd: 상품코드
            date_str: 날짜 (YYYY-MM-DD)

        Returns:
            행사 기간이면 True
        """
        cache = self._load_promo_periods_cache()
        periods = cache.get(item_cd, [])
        return any(s <= date_str <= e for s, e in periods)

    def get_promo_dates_in_range(self, item_cd: str, dates: list) -> set:
        """날짜 목록 중 행사 기간에 해당하는 날짜 set 반환

        Args:
            item_cd: 상품코드
            dates: 날짜 문자열 리스트

        Returns:
            행사 기간에 해당하는 날짜 set
        """
        cache = self._load_promo_periods_cache()
        periods = cache.get(item_cd, [])
        if not periods:
            return set()
        return {d for d in dates if any(s <= d <= e for s, e in periods)}
