"""
사전 발주 평가 모듈 (PreOrderEvaluator)

안전재고 계산 이전에 실행되는 상위 필터.
품절 여부 / 노출 시간 / 인기도를 평가하여 발주 필요성을 먼저 판단하고,
통과한 상품만 기존 안전재고 → 발주 플로우로 보낸다.
모든 판단 기록은 일별 텍스트 파일로 남긴다.
"""

import sqlite3
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.utils.logger import get_logger
from src.infrastructure.database.base_repository import BaseRepository
from src.prediction.eval_config import EvalConfig
from src.prediction.cost_optimizer import CostOptimizer
from src.settings.constants import (
    FORCE_MIN_DAILY_AVG, ENABLE_FORCE_DOWNGRADE,
    CATEGORY_EXPIRY_DAYS,
    ENABLE_FORCE_INTERMITTENT_SUPPRESSION,
    FORCE_INTERMITTENT_SELL_RATIO,
    FORCE_INTERMITTENT_MAX_EXPIRY,
    FORCE_INTERMITTENT_COOLDOWN_DAYS,
)

logger = get_logger(__name__)


class EvalDecision(Enum):
    """사전 발주 평가 결정"""
    FORCE_ORDER = "FORCE_ORDER"      # 현재 품절 → 강제 발주
    URGENT_ORDER = "URGENT_ORDER"    # 노출<1일 + 중인기 이상 → 긴급
    NORMAL_ORDER = "NORMAL_ORDER"    # 노출<2일 + 중인기 이상 → 일반
    PASS = "PASS"                    # 기존 안전재고 플로우에 위임
    SKIP = "SKIP"                    # 재고 충분 + 저인기 → 발주 안함


# 결정별 한글 설명
DECISION_LABELS = {
    EvalDecision.FORCE_ORDER: "강제 발주",
    EvalDecision.URGENT_ORDER: "긴급 발주",
    EvalDecision.NORMAL_ORDER: "일반 발주",
    EvalDecision.PASS: "안전재고 위임",
    EvalDecision.SKIP: "발주 스킵",
}


@dataclass
class PreOrderEvalResult:
    """사전 발주 평가 결과"""
    item_cd: str
    item_nm: str
    mid_cd: str
    decision: EvalDecision
    reason: str
    exposure_days: float        # (현재고+미입고) / 일평균판매
    stockout_frequency: float   # 30일간 재고0일 비율
    popularity_score: float     # 종합 인기도 (0~1)
    current_stock: int
    pending_qty: int
    daily_avg: float
    trend_score: float = 1.0    # 7일/30일 트렌드 비율 (ML 데이터용)


class PreOrderEvaluator(BaseRepository):
    """
    사전 발주 평가기

    안전재고 계산 이전에 실행되어 발주 필요성을 먼저 판단한다.
    모든 파라미터는 EvalConfig에서 로드 (자동 보정 결과 반영).
    인기도 등급은 고정값 대신 매 실행 시 백분위 기반으로 계산.
    """

    db_type = "store"

    def __init__(self, db_path: Optional[Path] = None, config: Optional[EvalConfig] = None, store_id: Optional[str] = None) -> None:
        super().__init__(db_path, store_id=store_id)
        self.config = config or EvalConfig.load()
        self.store_id = store_id
        self._log_dir = Path(__file__).parent.parent.parent / "data" / "logs"
        self._log_dir.mkdir(parents=True, exist_ok=True)
        # 분포 적응형 임계값 (evaluate_all 시 계산)
        self._adaptive_thresholds = None
        # 비용 최적화
        self._cost_optimizer = CostOptimizer(store_id=self.store_id)

    def _get_conn(self) -> sqlite3.Connection:
        """매장 DB + common.db ATTACH (products 테이블 JOIN 필요)"""
        conn = super()._get_conn()
        if self.store_id and not self._db_path:
            from src.infrastructure.database.connection import attach_common_with_views
            conn = attach_common_with_views(conn, self.store_id)
        return conn

    # =========================================================================
    # 데이터 조회
    # =========================================================================

    def _get_product_info(self, item_cd: str) -> Optional[Dict[str, object]]:
        """상품 정보 조회 (item_nm, mid_cd)"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT item_cd, item_nm, mid_cd FROM products WHERE item_cd = ?",
                (item_cd,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _get_current_inventory(self, item_cd: str) -> Tuple[int, int]:
        """
        현재 재고와 미입고 수량 조회

        우선순위: realtime_inventory → daily_sales (최근)

        Returns:
            (current_stock, pending_qty)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            # realtime_inventory 먼저
            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()
            cursor.execute(
                f"SELECT stock_qty, pending_qty FROM realtime_inventory WHERE item_cd = ? AND is_available = 1 {store_filter}",
                (item_cd,) + store_params
            )
            row = cursor.fetchone()
            if row:
                return (row[0] or 0, row[1] or 0)

            # fallback: daily_sales 최근 재고
            cursor.execute(
                f"""
                SELECT stock_qty FROM daily_sales
                WHERE item_cd = ? {store_filter}
                ORDER BY sales_date DESC
                LIMIT 1
                """,
                (item_cd,) + store_params
            )
            row = cursor.fetchone()
            stock = row[0] if row else 0
            return (stock, 0)
        finally:
            conn.close()

    def _get_daily_avg_sales(self, item_cd: str, days: Optional[int] = None) -> float:
        """
        일평균 판매량 계산 (최근 N일, 가용일 기준)

        품절일(stock_qty=0)을 분모에서 제외하여 실제 수요를 추정한다.
        가용일이 3일 미만이면 달력 기준으로 폴백한다.

        Args:
            item_cd: 상품코드
            days: 조회 기간 (None이면 config에서 로드)

        Returns:
            일평균 판매량
        """
        if days is None:
            days = int(self.config.daily_avg_days.value)
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()
            cursor.execute(
                f"""
                SELECT COALESCE(SUM(sale_qty), 0) as total_sales,
                       COUNT(*) as data_days,
                       SUM(CASE WHEN stock_qty > 0 THEN 1 ELSE 0 END) as available_days
                FROM daily_sales
                WHERE item_cd = ?
                AND sales_date >= date('now', '-' || ? || ' days')
                {store_filter}
                """,
                (item_cd, days) + store_params
            )
            row = cursor.fetchone()
            total_sales = row[0] if row else 0
            available_days = row[2] if row else 0

            # 가용일 기준 (3일 미만이면 달력 기준 폴백)
            if available_days and available_days >= 3:
                return total_sales / available_days
            return total_sales / days if days > 0 else 0.0
        finally:
            conn.close()

    def _get_stockout_frequency(self, item_cd: str, days: int = 30) -> float:
        """
        품절 빈도 계산 (최근 N일 중 재고=0인 날 비율)

        Args:
            item_cd: 상품코드
            days: 조회 기간 (기본 30일)

        Returns:
            품절 빈도 (0.0 ~ 1.0)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) as total_days,
                    SUM(CASE WHEN stock_qty = 0 THEN 1 ELSE 0 END) as zero_days
                FROM daily_sales
                WHERE item_cd = ?
                AND sales_date >= date('now', '-' || ? || ' days')
                {store_filter}
                """,
                (item_cd, days) + store_params
            )
            row = cursor.fetchone()
            total_days = row[0] if row else 0
            zero_days = row[1] if row else 0

            if total_days == 0:
                return 0.0
            return zero_days / total_days
        finally:
            conn.close()

    def _get_sell_day_ratio(self, item_cd: str, days: int = 30) -> float:
        """
        판매일 비율 계산 (조회 기간 중 실제 판매가 있었던 날 비율)

        Returns:
            판매일 비율 (0.0 ~ 1.0)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) as total_days,
                    SUM(CASE WHEN sale_qty > 0 THEN 1 ELSE 0 END) as sell_days
                FROM daily_sales
                WHERE item_cd = ?
                AND sales_date >= date('now', '-' || ? || ' days')
                {store_filter}
                """,
                (item_cd, days) + store_params
            )
            row = cursor.fetchone()
            total_days = row[0] if row else 0
            sell_days = row[1] if row else 0

            if total_days == 0:
                return 0.0
            return sell_days / total_days
        finally:
            conn.close()

    def _get_last_order_date(self, item_cd: str, days: int = 7) -> Optional[str]:
        """
        최근 발주일 조회 (간헐수요 격일 발주 판정용)

        Args:
            item_cd: 상품코드
            days: 조회 기간 (기본 7일)

        Returns:
            최근 발주일 (YYYY-MM-DD) 또는 None
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()
            cursor.execute(
                f"""
                SELECT MAX(sales_date) as last_order_date
                FROM daily_sales
                WHERE item_cd = ?
                AND ord_qty > 0
                AND sales_date >= date('now', '-' || ? || ' days')
                {store_filter}
                """,
                (item_cd, days) + store_params
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else None
        finally:
            conn.close()

    def _get_trend_ratio(self, item_cd: str) -> float:
        """
        트렌드 계산 (최근 7일 평균 / 최근 30일 평균)

        Returns:
            트렌드 비율 (>1 이면 상승 추세, <1 이면 하락 추세)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()

            # 최근 7일 평균
            cursor.execute(
                f"""
                SELECT COALESCE(AVG(sale_qty), 0)
                FROM daily_sales
                WHERE item_cd = ?
                AND sales_date >= date('now', '-7 days')
                AND sale_qty > 0
                {store_filter}
                """,
                (item_cd,) + store_params
            )
            avg_7 = cursor.fetchone()[0] or 0.0

            # 최근 30일 평균
            cursor.execute(
                f"""
                SELECT COALESCE(AVG(sale_qty), 0)
                FROM daily_sales
                WHERE item_cd = ?
                AND sales_date >= date('now', '-30 days')
                AND sale_qty > 0
                {store_filter}
                """,
                (item_cd,) + store_params
            )
            avg_30 = cursor.fetchone()[0] or 0.0

            if avg_30 <= 0:
                return 1.0
            return avg_7 / avg_30
        finally:
            conn.close()

    def _get_all_daily_avgs(self, days: Optional[int] = None, conn: Optional[sqlite3.Connection] = None) -> List[float]:
        """
        모든 상품의 일평균 판매량 목록 조회 (정규화용)

        Args:
            days: 조회 기간 (None이면 config에서 로드)
            conn: 외부 커넥션 (None이면 내부 생성)

        Returns:
            일평균 판매량 리스트
        """
        if days is None:
            days = int(self.config.daily_avg_days.value)
        own_conn = conn is None
        if own_conn:
            conn = self._get_conn()
        try:
            cursor = conn.cursor()
            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()
            cursor.execute(
                f"""
                SELECT item_cd, CAST(SUM(sale_qty) AS REAL) / ? as daily_avg
                FROM daily_sales
                WHERE sales_date >= date('now', '-' || ? || ' days')
                {store_filter}
                GROUP BY item_cd
                HAVING SUM(sale_qty) > 0
                """,
                (days, days) + store_params
            )
            return [row[1] for row in cursor.fetchall()]
        finally:
            if own_conn:
                conn.close()

    # =========================================================================
    # 배치 데이터 조회 (evaluate_all 전용)
    # =========================================================================

    @staticmethod
    def _chunked(items: List[str], chunk_size: int = 900) -> List[List[str]]:
        """SQLite IN 절 파라미터 제한(999)을 위한 청크 분할"""
        return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]

    @staticmethod
    def _median(values: List[float]) -> float:
        """정렬된 리스트의 중앙값 계산"""
        if not values:
            return 0.0
        s = sorted(values)
        mid = len(s) // 2
        if len(s) % 2 == 1:
            return s[mid]
        return (s[mid - 1] + s[mid]) / 2

    def _batch_load_products(self, item_codes: List[str], conn: sqlite3.Connection) -> Dict[str, Dict[str, object]]:
        """
        상품 정보 일괄 조회

        Returns:
            {item_cd: {"item_cd": ..., "item_nm": ..., "mid_cd": ...}, ...}
        """
        result = {}
        cursor = conn.cursor()
        for chunk in self._chunked(item_codes):
            placeholders = ",".join("?" * len(chunk))
            cursor.execute(
                f"SELECT item_cd, item_nm, mid_cd FROM products WHERE item_cd IN ({placeholders})",
                chunk
            )
            for row in cursor.fetchall():
                result[row["item_cd"]] = dict(row)
        return result

    def _batch_load_inventory(self, item_codes: List[str], conn: sqlite3.Connection) -> Dict[str, Tuple[int, int]]:
        """
        현재 재고/미입고 일괄 조회 (realtime_inventory 우선, daily_sales fallback)
        유통기한 기반 staleness 체크: stale이면 stock_qty=0 처리

        Returns:
            {item_cd: (current_stock, pending_qty), ...}
        """
        from datetime import datetime, timedelta
        from src.infrastructure.database.repos.inventory_repo import RealtimeInventoryRepository

        result = {}
        cursor = conn.cursor()
        store_filter = "AND ri.store_id = ?" if self.store_id else ""
        store_params = [self.store_id] if self.store_id else []
        prefix = "common." if self.store_id else ""

        now = datetime.now()

        # 1) realtime_inventory + 유통기한 JOIN 일괄 조회
        for chunk in self._chunked(item_codes):
            placeholders = ",".join("?" * len(chunk))
            cursor.execute(
                f"""
                SELECT ri.item_cd, ri.stock_qty, ri.pending_qty,
                       ri.queried_at, pd.expiration_days, p.mid_cd
                FROM realtime_inventory ri
                LEFT JOIN {prefix}product_details pd ON ri.item_cd = pd.item_cd
                LEFT JOIN {prefix}products p ON ri.item_cd = p.item_cd
                WHERE ri.item_cd IN ({placeholders}) AND ri.is_available = 1 AND ri.is_cut_item = 0
                {store_filter}
                """,
                chunk + store_params
            )
            for row in cursor.fetchall():
                stock_qty = row["stock_qty"] or 0
                pending_qty = row["pending_qty"] or 0

                # 유통기한 기반 staleness 체크
                queried_at = row["queried_at"]
                expiry_days = row["expiration_days"]
                mid_cd = row["mid_cd"]
                stale_hours = RealtimeInventoryRepository._get_stale_hours_for_expiry(
                    expiry_days, mid_cd
                )

                is_stale = False
                if queried_at:
                    try:
                        queried_dt = datetime.fromisoformat(queried_at)
                        cutoff = now - timedelta(hours=stale_hours)
                        is_stale = queried_dt < cutoff
                    except (ValueError, TypeError):
                        is_stale = True
                else:
                    is_stale = True

                if is_stale:
                    stock_qty = 0  # stale 재고는 0 처리

                result[row["item_cd"]] = (stock_qty, pending_qty)

        # 2) realtime_inventory에 없는 항목은 daily_sales fallback
        missing = [c for c in item_codes if c not in result]
        if missing:
            for chunk in self._chunked(missing):
                placeholders = ",".join("?" * len(chunk))
                if self.store_id:
                    cursor.execute(
                        f"""
                        SELECT item_cd, stock_qty
                        FROM daily_sales
                        WHERE rowid IN (
                            SELECT MAX(rowid) FROM daily_sales
                            WHERE item_cd IN ({placeholders})
                            AND store_id = ?
                            GROUP BY item_cd
                        )
                        """,
                        chunk + [self.store_id]
                    )
                else:
                    cursor.execute(
                        f"""
                        SELECT item_cd, stock_qty
                        FROM daily_sales
                        WHERE rowid IN (
                            SELECT MAX(rowid) FROM daily_sales
                            WHERE item_cd IN ({placeholders})
                            GROUP BY item_cd
                        )
                        """,
                        chunk
                    )
                for row in cursor.fetchall():
                    result[row["item_cd"]] = (row["stock_qty"] if row["stock_qty"] else 0, 0)

        # 3) 어디에도 없는 항목은 (0, 0)
        for c in item_codes:
            if c not in result:
                result[c] = (0, 0)

        return result

    def _batch_load_daily_sales_stats(
        self, item_codes: List[str], conn: sqlite3.Connection, days: Optional[int] = None
    ) -> Dict[str, Dict[str, float]]:
        """
        일평균(달력일 기준), 품절빈도, 판매일비율을 단일 GROUP BY 쿼리로 일괄 계산

        daily_avg = total_sales / (달력일 - 확인된 품절일)
        - 달력일 기반 분모를 사용하여 간헐적 상품의 과대추정 방지
        - 확인된 품절일(stock_qty=0 레코드)은 공급 부재이므로 분모에서 제외
        - (달력일 - 품절일) < 3이면 달력일 전체로 폴백
        stockout_freq/sell_day_ratio는 고정 30일 달력 윈도우 기준.

        Returns:
            {item_cd: {"daily_avg": float, "stockout_freq": float, "sell_day_ratio": float}, ...}
        """
        if days is None:
            days = int(self.config.daily_avg_days.value)
        stockout_days_window = 30  # _get_stockout_frequency / _get_sell_day_ratio 기본값
        max_days = max(days, stockout_days_window)

        result = {}
        cursor = conn.cursor()
        store_filter = "AND store_id = ?" if self.store_id else ""
        store_params = [self.store_id] if self.store_id else []
        for chunk in self._chunked(item_codes):
            placeholders = ",".join("?" * len(chunk))
            cursor.execute(
                f"""
                SELECT
                    item_cd,
                    COALESCE(SUM(CASE WHEN sales_date >= date('now', '-' || ? || ' days')
                                     THEN sale_qty ELSE 0 END), 0) as total_sales,
                    SUM(CASE WHEN sales_date >= date('now', '-' || ? || ' days') AND stock_qty > 0
                             THEN 1 ELSE 0 END) as available_days,
                    SUM(CASE WHEN sales_date >= date('now', '-' || ? || ' days') AND stock_qty = 0
                             THEN 1 ELSE 0 END) as confirmed_stockout_days,
                    SUM(CASE WHEN sales_date >= date('now', '-30 days') THEN 1 ELSE 0 END) as total_days_30,
                    SUM(CASE WHEN sales_date >= date('now', '-30 days') AND stock_qty = 0
                             THEN 1 ELSE 0 END) as zero_days_30,
                    SUM(CASE WHEN sales_date >= date('now', '-30 days') AND sale_qty > 0
                             THEN 1 ELSE 0 END) as sell_days_30
                FROM daily_sales
                WHERE item_cd IN ({placeholders})
                AND sales_date >= date('now', '-' || ? || ' days')
                {store_filter}
                GROUP BY item_cd
                """,
                [days, days, days] + chunk + [max_days] + store_params
            )
            for row in cursor.fetchall():
                total_days_30 = row["total_days_30"] or 0
                confirmed_stockout = row["confirmed_stockout_days"] or 0

                # 달력일 기준 분모: 전체 기간 - 확인된 품절일
                # 품절일은 공급 부재이므로 분모에서 제외 (수요가 있었을 수 있음)
                calendar_available = days - confirmed_stockout
                if calendar_available < 3:
                    calendar_available = days  # 폴백: 달력일 전체
                daily_avg = row["total_sales"] / calendar_available if calendar_available > 0 else 0.0

                result[row["item_cd"]] = {
                    "daily_avg": daily_avg,
                    "stockout_freq": (row["zero_days_30"] or 0) / total_days_30 if total_days_30 > 0 else 0.0,
                    "sell_day_ratio": (row["sell_days_30"] or 0) / 30,  # 달력 30일 기준
                }

        # 데이터 없는 항목 기본값
        for c in item_codes:
            if c not in result:
                result[c] = {"daily_avg": 0.0, "stockout_freq": 0.0, "sell_day_ratio": 0.0}

        return result

    def _batch_load_trend_ratios(self, item_codes: List[str], conn: sqlite3.Connection) -> Dict[str, float]:
        """
        7일/30일 트렌드 비율 일괄 계산 (CASE WHEN 조건부 집계)

        Returns:
            {item_cd: trend_ratio, ...}
        """
        result = {}
        cursor = conn.cursor()
        store_filter = "AND store_id = ?" if self.store_id else ""
        store_params = [self.store_id] if self.store_id else []
        for chunk in self._chunked(item_codes):
            placeholders = ",".join("?" * len(chunk))
            cursor.execute(
                f"""
                SELECT
                    item_cd,
                    COALESCE(AVG(CASE WHEN sales_date >= date('now', '-7 days') AND sale_qty > 0
                                     THEN sale_qty END), 0) as avg_7,
                    COALESCE(AVG(CASE WHEN sales_date >= date('now', '-30 days') AND sale_qty > 0
                                     THEN sale_qty END), 0) as avg_30
                FROM daily_sales
                WHERE item_cd IN ({placeholders})
                AND sales_date >= date('now', '-30 days')
                {store_filter}
                GROUP BY item_cd
                """,
                chunk + store_params
            )
            for row in cursor.fetchall():
                avg_7 = row["avg_7"] or 0.0
                avg_30 = row["avg_30"] or 0.0
                result[row["item_cd"]] = avg_7 / avg_30 if avg_30 > 0 else 1.0

        # 데이터 없는 항목 기본값
        for c in item_codes:
            if c not in result:
                result[c] = 1.0

        return result

    def _batch_load_last_order_dates(
        self, item_codes: List[str], conn: sqlite3.Connection
    ) -> Dict[str, Optional[str]]:
        """
        상품별 최근 발주일 일괄 조회 (간헐수요 격일 발주 판정용)

        daily_sales.ord_qty > 0 인 가장 최근 날짜를 반환한다. (7일 윈도우)

        Returns:
            {item_cd: "YYYY-MM-DD" 또는 None, ...}
        """
        result: Dict[str, Optional[str]] = {}
        cursor = conn.cursor()
        store_filter = "AND store_id = ?" if self.store_id else ""
        store_params = [self.store_id] if self.store_id else []
        for chunk in self._chunked(item_codes):
            placeholders = ",".join("?" * len(chunk))
            cursor.execute(
                f"""
                SELECT item_cd, MAX(sales_date) as last_order_date
                FROM daily_sales
                WHERE item_cd IN ({placeholders})
                AND ord_qty > 0
                AND sales_date >= date('now', '-7 days')
                {store_filter}
                GROUP BY item_cd
                """,
                chunk + store_params
            )
            for row in cursor.fetchall():
                result[row["item_cd"]] = row["last_order_date"]

        # 데이터 없는 항목은 None
        for c in item_codes:
            if c not in result:
                result[c] = None

        return result

    def _batch_load_lead_times(
        self, item_codes: List[str], conn: sqlite3.Connection
    ) -> Dict[str, float]:
        """
        상품별 리드타임(발주→입고 간격) 일괄 조회

        우선순위:
          1) receiving_history (order_date → receiving_date 중앙값)
          2) daily_sales (ord_qty>0 날짜 → buy_qty>0 날짜 간격)
          3) product_details.lead_time_days
          4) 폴백: 1.0일

        Returns:
            {item_cd: lead_time_days, ...}
        """
        result = {}
        cursor = conn.cursor()

        # 1순위: receiving_history에서 order_date → receiving_date 간격 중앙값
        for chunk in self._chunked(item_codes):
            placeholders = ",".join("?" * len(chunk))
            cursor.execute(
                f"""
                SELECT item_cd,
                       julianday(receiving_date) - julianday(order_date) as gap
                FROM receiving_history
                WHERE item_cd IN ({placeholders})
                AND order_date IS NOT NULL
                AND receiving_date IS NOT NULL
                AND julianday(receiving_date) >= julianday(order_date)
                """,
                chunk
            )
            # 상품별 gap 수집
            gaps_by_item: Dict[str, List[float]] = {}
            for row in cursor.fetchall():
                ic = row["item_cd"]
                gap = row["gap"]
                if gap is not None and gap >= 0:
                    gaps_by_item.setdefault(ic, []).append(gap)

            for ic, gaps in gaps_by_item.items():
                if gaps:
                    result[ic] = max(self._median(gaps), 0.0)

        # 2순위: daily_sales에서 발주→입고 간격 (아직 없는 항목만)
        missing = [c for c in item_codes if c not in result]
        if missing:
            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = [self.store_id] if self.store_id else []
            for chunk in self._chunked(missing):
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"""
                    SELECT item_cd, sales_date, ord_qty, buy_qty
                    FROM daily_sales
                    WHERE item_cd IN ({placeholders})
                    AND (ord_qty > 0 OR buy_qty > 0)
                    AND sales_date >= date('now', '-90 days')
                    {store_filter}
                    ORDER BY item_cd, sales_date
                    """,
                    chunk + store_params
                )
                rows = cursor.fetchall()
                daily_gaps = self._analyze_order_delivery_gaps(rows)
                for ic, gap in daily_gaps.items():
                    if ic not in result:
                        result[ic] = gap

        # 3순위: product_details.lead_time_days (아직 없는 항목만)
        missing = [c for c in item_codes if c not in result]
        if missing:
            for chunk in self._chunked(missing):
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"""
                    SELECT item_cd, lead_time_days
                    FROM product_details
                    WHERE item_cd IN ({placeholders})
                    AND lead_time_days IS NOT NULL
                    AND lead_time_days > 0
                    """,
                    chunk
                )
                for row in cursor.fetchall():
                    result[row["item_cd"]] = float(row["lead_time_days"])

        # 4순위: 폴백 1.0일
        for c in item_codes:
            if c not in result:
                result[c] = 1.0

        return result

    @staticmethod
    def _analyze_order_delivery_gaps(rows: List[sqlite3.Row]) -> Dict[str, float]:
        """
        daily_sales 행에서 발주→입고 간격 추출

        ord_qty>0인 날짜 이후 가장 가까운 buy_qty>0 날짜와의 간격을 계산.
        입고일을 정렬 후 bisect로 검색하여 O(N log M) 복잡도.

        Args:
            rows: daily_sales 행 목록 (item_cd, sales_date, ord_qty, buy_qty)

        Returns:
            {item_cd: median_gap_days, ...}
        """
        import bisect
        from datetime import datetime as dt

        # 상품별 발주일/입고일 수집
        order_dates: Dict[str, List[str]] = {}
        buy_dates: Dict[str, List[str]] = {}
        for row in rows:
            ic = row["item_cd"]
            if (row["ord_qty"] or 0) > 0:
                order_dates.setdefault(ic, []).append(row["sales_date"])
            if (row["buy_qty"] or 0) > 0:
                buy_dates.setdefault(ic, []).append(row["sales_date"])

        result = {}
        for ic, o_dates in order_dates.items():
            b_dates = buy_dates.get(ic, [])
            if not b_dates:
                continue

            # 입고일을 datetime으로 변환 후 정렬 (bisect용)
            b_dates_dt = sorted(dt.strptime(d, "%Y-%m-%d") for d in b_dates)

            gaps = []
            for od in o_dates:
                od_dt = dt.strptime(od, "%Y-%m-%d")
                # bisect로 발주일 이후 가장 가까운 입고일 탐색
                idx = bisect.bisect_left(b_dates_dt, od_dt)
                if idx < len(b_dates_dt):
                    gap = (b_dates_dt[idx] - od_dt).days
                    gaps.append(float(gap))

            if gaps:
                result[ic] = max(PreOrderEvaluator._median(gaps), 0.0)

        return result

    # =========================================================================
    # 핵심 알고리즘
    # =========================================================================

    def _calculate_exposure_days(
        self, current_stock: int, pending_qty: int, daily_avg: float,
        lead_time: float = 0.0
    ) -> float:
        """
        노출 시간(일) 계산

        lead_time > 0이면 입고 시점 기준 노출일수를 반환한다.
        exposure_at_arrival = raw_exposure - lead_time

        Args:
            current_stock: 현재 재고
            pending_qty: 미입고 수량
            daily_avg: 일평균 판매량
            lead_time: 발주→입고 리드타임 (일). 0이면 기존 동작과 동일.

        Returns:
            노출 가능 일수 (일평균이 0이면 999.0)
        """
        if daily_avg <= 0:
            return 999.0  # 판매 이력 없으면 충분한 것으로 간주
        raw = (current_stock + pending_qty) / daily_avg
        return max(raw - lead_time, 0.0)

    def _calculate_popularity(
        self,
        daily_avg: float,
        sell_day_ratio: float,
        trend_ratio: float,
        all_daily_avgs: List[float],
    ) -> float:
        """
        종합 인기도 계산 (0~1, 수요 지표만)

        인기도 = w1×norm(가용일평균) + w2×판매일비율 + w3×norm(트렌드)

        노출/재고 정보는 결정 매트릭스에서 별도로 처리하므로
        인기도는 순수 수요 신호만 반영한다.

        Args:
            daily_avg: 가용일평균 판매량
            sell_day_ratio: 판매일 비율 (0~1)
            trend_ratio: 트렌드 비율 (7일/30일)
            all_daily_avgs: 모든 상품의 일평균 (정규화 기준)

        Returns:
            인기도 점수 (0.0 ~ 1.0)
        """
        # 일평균 정규화 (min-max, 상위 클리핑)
        if all_daily_avgs:
            max_avg = max(all_daily_avgs) if all_daily_avgs else 1.0
            norm_avg = min(daily_avg / max_avg, 1.0) if max_avg > 0 else 0.0
        else:
            norm_avg = 0.0

        # 트렌드 정규화 (0.5~1.5 → 0~1)
        norm_trend = max(0.0, min((trend_ratio - 0.5) / 1.0, 1.0))

        # 가중 합산 (config에서 가중치 로드, 수요 지표 3개)
        weights = self.config.get_popularity_weights()
        score = (
            weights["daily_avg"] * norm_avg
            + weights["sell_day_ratio"] * sell_day_ratio
            + weights["trend"] * norm_trend
        )
        return round(min(max(score, 0.0), 1.0), 4)

    def _get_popularity_level(self, score: float) -> str:
        """
        인기도 등급 반환 (high/medium/low)

        분포 적응형 임계값이 있으면 사용, 없으면 config 백분위 기준으로
        고정 임계값(0.6/0.3) 대신 상대 순위를 기반으로 판단한다.
        """
        if self._adaptive_thresholds:
            high_th = self._adaptive_thresholds.get("high", 0.6)
            medium_th = self._adaptive_thresholds.get("medium", 0.3)
        else:
            high_th = 0.6
            medium_th = 0.3

        if score >= high_th:
            return "high"
        elif score >= medium_th:
            return "medium"
        return "low"

    def _make_decision(
        self,
        current_stock: int,
        exposure_days: float,
        popularity_level: str,
        stockout_frequency: float,
        item_cd: Optional[str] = None,
        daily_avg: float = 0.0,
        mid_cd: str = "",
        sell_day_ratio: float = 1.0,
        last_order_date: Optional[str] = None
    ) -> Tuple[EvalDecision, str]:
        """
        결정 매트릭스 적용

        | 상태           | 고인기       | 중인기       | 저인기       |
        |----------------|-------------|-------------|-------------|
        | 현재 품절       | FORCE       | FORCE       | FORCE       |
        | 노출 < 1일     | URGENT      | URGENT      | NORMAL      |
        | 노출 < 2일     | NORMAL      | NORMAL      | PASS        |
        | 노출 >= 3일    | PASS        | PASS        | SKIP        |

        추가: 품절빈도 high + 노출<2일 → 우선순위 1단계 상승
        추가: 품절이지만 일평균 < FORCE_MIN_DAILY_AVG → NORMAL로 다운그레이드
        추가: 품절 + 간헐수요(빈도<50%) + 유통기한1일 → PASS (폐기 사이클 방지)

        Returns:
            (결정, 사유)
        """
        # 임계값 로드 (config)
        th_urgent = self.config.exposure_urgent.value
        th_normal = self.config.exposure_normal.value
        th_sufficient = self.config.exposure_sufficient.value
        stockout_threshold = self.config.stockout_freq_threshold.value

        # [비용최적화] SKIP 임계값 오프셋 적용
        if self._cost_optimizer and self._cost_optimizer.enabled and item_cd:
            cost_info = self._cost_optimizer.get_item_cost_info(item_cd, daily_avg=daily_avg, mid_cd=mid_cd)
            if cost_info.enabled and cost_info.skip_offset != 0.0:
                th_sufficient += cost_info.skip_offset
                th_sufficient = max(th_sufficient, th_normal + 0.1)

        # 현재 품절
        if current_stock <= 0:
            # 기존: 일평균 극소 → NORMAL 다운그레이드
            if ENABLE_FORCE_DOWNGRADE and daily_avg < FORCE_MIN_DAILY_AVG:
                return EvalDecision.NORMAL_ORDER, f"품절+일평균{daily_avg:.2f}<{FORCE_MIN_DAILY_AVG}"

            # 간헐수요 + 유통기한1일 → PASS (폐기 사이클 방지)
            if ENABLE_FORCE_INTERMITTENT_SUPPRESSION and mid_cd:
                expiry_days = CATEGORY_EXPIRY_DAYS.get(mid_cd)
                if (expiry_days is not None
                        and expiry_days <= FORCE_INTERMITTENT_MAX_EXPIRY
                        and sell_day_ratio < FORCE_INTERMITTENT_SELL_RATIO):
                    sell_pct = int(sell_day_ratio * 100)
                    reason_parts = f"품절+간헐수요(빈도{sell_pct}%)+유통{expiry_days}일"
                    # 최근 발주 체크 (사유에 추가 정보)
                    if last_order_date:
                        try:
                            from datetime import date as _date
                            days_since = (_date.today() - _date.fromisoformat(last_order_date)).days
                            if days_since <= FORCE_INTERMITTENT_COOLDOWN_DAYS:
                                reason_parts += f"+최근발주{days_since}일전"
                        except (ValueError, TypeError):
                            pass
                    return EvalDecision.PASS, reason_parts + "->PASS"

            # CUT 미확인 의심 상품: 조회 오래됨 + 재고0 → FORCE 대신 NORMAL
            if hasattr(self, '_stale_cut_suspects') and item_cd and item_cd in self._stale_cut_suspects:
                from src.settings.constants import CUT_STATUS_STALE_DAYS
                return EvalDecision.NORMAL_ORDER, f"품절+CUT미확인(조회>{CUT_STATUS_STALE_DAYS}일)"

            return EvalDecision.FORCE_ORDER, "현재 품절"

        # 기본 결정 매트릭스 (config 임계값 사용)
        if exposure_days < th_urgent:
            if popularity_level in ("high", "medium"):
                decision = EvalDecision.URGENT_ORDER
                reason = f"노출<{th_urgent}일+{popularity_level}인기"
            else:
                decision = EvalDecision.NORMAL_ORDER
                reason = f"노출<{th_urgent}일+저인기"
        elif exposure_days < th_normal:
            if popularity_level in ("high", "medium"):
                decision = EvalDecision.NORMAL_ORDER
                reason = f"노출<{th_normal}일+{popularity_level}인기"
            else:
                decision = EvalDecision.PASS
                reason = f"노출<{th_normal}일+저인기"
        elif exposure_days >= th_sufficient:
            if popularity_level == "low":
                decision = EvalDecision.SKIP
                reason = "재고충분+저인기"
            else:
                decision = EvalDecision.PASS
                reason = f"재고충분+{popularity_level}인기"
        else:
            # th_normal <= exposure_days < th_sufficient
            decision = EvalDecision.PASS
            reason = f"노출{exposure_days:.1f}일"

        # 품절빈도 높고 노출<th_normal이면 1단계 상승
        if stockout_frequency >= stockout_threshold and exposure_days < th_normal:
            upgrade_map = {
                EvalDecision.PASS: EvalDecision.NORMAL_ORDER,
                EvalDecision.NORMAL_ORDER: EvalDecision.URGENT_ORDER,
            }
            if decision in upgrade_map:
                decision = upgrade_map[decision]
                reason += f"+품절빈도{stockout_frequency:.0%}"

        return decision, reason

    # =========================================================================
    # 평가 실행
    # =========================================================================

    def evaluate(self, item_cd: str, all_daily_avgs: Optional[List[float]] = None) -> Optional[PreOrderEvalResult]:
        """
        단일 상품 사전 평가

        Args:
            item_cd: 상품코드
            all_daily_avgs: 모든 상품 일평균 리스트 (정규화용, None이면 조회)

        Returns:
            PreOrderEvalResult 또는 None (상품 정보 없음)
        """
        product = self._get_product_info(item_cd)
        if not product:
            return None

        # 데이터 수집
        current_stock, pending_qty = self._get_current_inventory(item_cd)
        daily_avg = self._get_daily_avg_sales(item_cd)
        stockout_freq = self._get_stockout_frequency(item_cd)
        sell_day_ratio = self._get_sell_day_ratio(item_cd)
        trend_ratio = self._get_trend_ratio(item_cd)
        last_order_date = self._get_last_order_date(item_cd)

        # 정규화용 데이터 (없으면 조회)
        if all_daily_avgs is None:
            all_daily_avgs = self._get_all_daily_avgs()

        # 노출 시간
        exposure_days = self._calculate_exposure_days(
            current_stock, pending_qty, daily_avg
        )

        # 인기도 (수요 지표만)
        popularity = self._calculate_popularity(
            daily_avg, sell_day_ratio, trend_ratio, all_daily_avgs
        )
        popularity_level = self._get_popularity_level(popularity)

        # 결정
        decision, reason = self._make_decision(
            current_stock, exposure_days, popularity_level, stockout_freq,
            item_cd=item_cd, daily_avg=daily_avg, mid_cd=product["mid_cd"] or "",
            sell_day_ratio=sell_day_ratio, last_order_date=last_order_date,
        )

        return PreOrderEvalResult(
            item_cd=item_cd,
            item_nm=product["item_nm"] or "",
            mid_cd=product["mid_cd"] or "",
            decision=decision,
            reason=reason,
            exposure_days=round(exposure_days, 1),
            stockout_frequency=round(stockout_freq, 4),
            popularity_score=popularity,
            current_stock=current_stock,
            pending_qty=pending_qty,
            daily_avg=round(daily_avg, 2),
        )

    def evaluate_all(
        self,
        item_codes: Optional[List[str]] = None,
        write_log: bool = True
    ) -> Dict[str, PreOrderEvalResult]:
        """
        전체 상품 사전 평가 (배치 로딩)

        단일 커넥션에서 4개 배치 쿼리로 모든 데이터를 사전 로딩한 후,
        루프에서는 메모리 조회만 수행한다. (N+1 쿼리 제거)

        Args:
            item_codes: 평가할 상품코드 목록 (None이면 최근 판매 상품 자동 조회)
            write_log: True면 텍스트 로그 파일 기록

        Returns:
            {item_cd: PreOrderEvalResult, ...}
        """
        # 평가 대상 상품 조회
        if item_codes is None:
            item_codes = self._get_active_item_codes()

        if not item_codes:
            logger.info("사전 평가 대상 상품 없음")
            return {}

        # CUT(발주중지) 상품 제외 → SKIP 처리 + CUT 미확인 의심 상품 조회
        results = {}
        self._stale_cut_suspects = set()  # FORCE_ORDER 가드용
        cut_conn = self._get_conn()
        try:
            cut_cursor = cut_conn.cursor()
            store_filter_sql = "AND store_id = ?" if self.store_id else ""
            store_params_sql = [self.store_id] if self.store_id else []
            cut_cursor.execute(
                f"SELECT item_cd FROM realtime_inventory WHERE is_cut_item = 1 {store_filter_sql}",
                store_params_sql
            )
            cut_items = {row[0] for row in cut_cursor.fetchall()}

            # CUT 미확인 의심 상품: 재고0 + 조회 오래됨 → FORCE_ORDER 방지용
            try:
                from src.settings.constants import CUT_STATUS_STALE_DAYS
                cut_cursor.execute(
                    f"""SELECT item_cd FROM realtime_inventory
                    WHERE is_cut_item = 0 AND is_available = 1
                    AND stock_qty = 0
                    AND queried_at < datetime('now', '-' || ? || ' days')
                    {store_filter_sql}""",
                    [CUT_STATUS_STALE_DAYS] + store_params_sql
                )
                self._stale_cut_suspects = {row[0] for row in cut_cursor.fetchall()}
                if self._stale_cut_suspects:
                    logger.info(f"CUT 미확인 의심 상품 {len(self._stale_cut_suspects)}개 (FORCE 가드 적용)")
            except Exception as e:
                logger.warning(f"CUT 미확인 의심 상품 조회 실패: {e}")
        finally:
            cut_conn.close()

        if cut_items:
            cut_in_candidates = [cd for cd in item_codes if cd in cut_items]
            if cut_in_candidates:
                for cd in cut_in_candidates:
                    results[cd] = PreOrderEvalResult(
                        item_cd=cd, item_nm="", mid_cd="",
                        decision=EvalDecision.SKIP,
                        reason="CUT(발주중지) 상품",
                        exposure_days=0, stockout_frequency=0,
                        popularity_score=0, current_stock=0,
                        pending_qty=0, daily_avg=0, trend_score=0,
                    )
                item_codes = [cd for cd in item_codes if cd not in cut_items]
                logger.info(f"CUT 상품 {len(cut_in_candidates)}개 SKIP 처리")

        logger.info(f"사전 발주 평가 시작: {len(item_codes)}개 상품")

        # 단일 커넥션으로 배치 데이터 로딩
        conn = self._get_conn()
        try:
            all_daily_avgs = self._get_all_daily_avgs(conn=conn)
            products_map = self._batch_load_products(item_codes, conn)
            inventory_map = self._batch_load_inventory(item_codes, conn)
            sales_stats_map = self._batch_load_daily_sales_stats(item_codes, conn)
            trend_map = self._batch_load_trend_ratios(item_codes, conn)
            last_order_map = self._batch_load_last_order_dates(item_codes, conn)
        finally:
            conn.close()

        logger.info(
            f"배치 로딩 완료: 상품={len(products_map)} 재고={len(inventory_map)} "
            f"판매통계={len(sales_stats_map)} 트렌드={len(trend_map)}"
        )

        # 1차 패스: 인기도 점수 계산 (분포 적응형 임계값용)
        pre_scores = []
        pre_data = []
        for item_cd in item_codes:
            product = products_map.get(item_cd)
            if not product:
                continue

            current_stock, pending_qty = inventory_map.get(item_cd, (0, 0))
            stats = sales_stats_map.get(item_cd, {"daily_avg": 0.0, "stockout_freq": 0.0, "sell_day_ratio": 0.0})
            daily_avg = stats["daily_avg"]
            stockout_freq = stats["stockout_freq"]
            sell_day_ratio = stats["sell_day_ratio"]
            trend_ratio = trend_map.get(item_cd, 1.0)

            exposure = self._calculate_exposure_days(
                current_stock, pending_qty, daily_avg
            )
            popularity = self._calculate_popularity(
                daily_avg, sell_day_ratio, trend_ratio, all_daily_avgs
            )

            pre_scores.append(popularity)
            pre_data.append({
                "item_cd": item_cd,
                "product": product,
                "current_stock": current_stock,
                "pending_qty": pending_qty,
                "daily_avg": daily_avg,
                "stockout_freq": stockout_freq,
                "sell_day_ratio": sell_day_ratio,
                "exposure_days": exposure,
                "popularity": popularity,
                "trend_ratio": trend_ratio,
                "last_order_date": last_order_map.get(item_cd),
            })

        # 분포 적응형 임계값 계산
        self._adaptive_thresholds = self._compute_adaptive_thresholds(pre_scores)

        # 2차 패스: 결정 매트릭스 적용 (CUT SKIP 결과를 보존)
        for d in pre_data:
            popularity_level = self._get_popularity_level(d["popularity"])
            decision, reason = self._make_decision(
                d["current_stock"], d["exposure_days"],
                popularity_level, d["stockout_freq"],
                item_cd=d["item_cd"], daily_avg=d["daily_avg"],
                mid_cd=d["product"]["mid_cd"] or "",
                sell_day_ratio=d["sell_day_ratio"],
                last_order_date=d["last_order_date"],
            )
            result = PreOrderEvalResult(
                item_cd=d["item_cd"],
                item_nm=d["product"]["item_nm"] or "",
                mid_cd=d["product"]["mid_cd"] or "",
                decision=decision,
                reason=reason,
                exposure_days=round(d["exposure_days"], 1),
                stockout_frequency=round(d["stockout_freq"], 4),
                popularity_score=d["popularity"],
                current_stock=d["current_stock"],
                pending_qty=d["pending_qty"],
                daily_avg=round(d["daily_avg"], 2),
                trend_score=round(d["trend_ratio"], 4),
            )
            results[d["item_cd"]] = result

        # 결과 요약 로그
        summary = self._summarize_results(results)
        logger.info(
            f"사전 평가 완료: {summary['total']}개 | "
            f"FORCE:{summary['FORCE_ORDER']} URGENT:{summary['URGENT_ORDER']} "
            f"NORMAL:{summary['NORMAL_ORDER']} PASS:{summary['PASS']} SKIP:{summary['SKIP']}"
        )

        # 텍스트 로그 기록
        if write_log:
            self._write_log_file(results, summary)

        return results

    def get_filtered_items(
        self, results: Dict[str, PreOrderEvalResult]
    ) -> Tuple[List[str], set]:
        """
        평가 결과에서 발주 대상 / 스킵 대상 분리

        Args:
            results: evaluate_all() 결과

        Returns:
            (order_codes, skip_codes)
            - order_codes: FORCE/URGENT/NORMAL 결정된 상품코드 (우선순위 정렬)
            - skip_codes: SKIP 결정된 상품코드 set
        """
        # 결정별 분류
        force_items = []
        urgent_items = []
        normal_items = []
        skip_codes = set()

        for item_cd, result in results.items():
            if result.decision == EvalDecision.FORCE_ORDER:
                force_items.append(item_cd)
            elif result.decision == EvalDecision.URGENT_ORDER:
                urgent_items.append(item_cd)
            elif result.decision == EvalDecision.NORMAL_ORDER:
                normal_items.append(item_cd)
            elif result.decision == EvalDecision.SKIP:
                skip_codes.add(item_cd)
            # PASS는 기존 플로우에 위임 (order_codes에도 skip_codes에도 포함 안 함)

        # 우선순위 정렬: FORCE → URGENT → NORMAL
        order_codes = force_items + urgent_items + normal_items

        return order_codes, skip_codes

    # =========================================================================
    # 내부 유틸리티
    # =========================================================================

    def _compute_adaptive_thresholds(self, all_scores: List[float]) -> Dict[str, float]:
        """
        분포 적응형 인기도 임계값 계산

        전체 인기도 점수의 백분위를 사용하여 고/중/저 인기 기준을 결정.
        config의 popularity_high_percentile / popularity_low_percentile 사용.

        Args:
            all_scores: 전체 상품 인기도 점수 리스트

        Returns:
            {"high": float, "medium": float}
        """
        if not all_scores or len(all_scores) < 10:
            return {"high": 0.6, "medium": 0.3}

        sorted_scores = sorted(all_scores)
        n = len(sorted_scores)

        high_pct = self.config.popularity_high_percentile.value / 100.0
        low_pct = self.config.popularity_low_percentile.value / 100.0

        high_idx = min(int(n * high_pct), n - 1)
        low_idx = min(int(n * low_pct), n - 1)

        high_threshold = sorted_scores[high_idx]
        medium_threshold = sorted_scores[low_idx]

        # 최소 간격 보장
        if high_threshold - medium_threshold < 0.05:
            medium_threshold = max(0.0, high_threshold - 0.05)

        logger.info(
            f"분포 적응형 임계값: 고인기>={high_threshold:.3f} (P{self.config.popularity_high_percentile.value:.0f}), "
            f"중인기>={medium_threshold:.3f} (P{self.config.popularity_low_percentile.value:.0f}), "
            f"샘플={n}개"
        )

        return {"high": high_threshold, "medium": medium_threshold}

    def _get_active_item_codes(self) -> List[str]:
        """최근 N일 판매 이력이 있는 상품코드 목록"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()
            cursor.execute(
                f"""
                SELECT DISTINCT item_cd
                FROM daily_sales
                WHERE sales_date >= date('now', '-' || ? || ' days')
                AND sale_qty > 0
                {store_filter}
                """,
                (int(self.config.daily_avg_days.value),) + store_params
            )
            return [row[0] for row in cursor.fetchall()]
        finally:
            conn.close()

    def _summarize_results(
        self, results: Dict[str, PreOrderEvalResult]
    ) -> Dict[str, int]:
        """결과 요약 통계"""
        summary = {
            "total": len(results),
            "FORCE_ORDER": 0,
            "URGENT_ORDER": 0,
            "NORMAL_ORDER": 0,
            "PASS": 0,
            "SKIP": 0,
        }
        for result in results.values():
            summary[result.decision.value] += 1
        return summary

    # =========================================================================
    # 텍스트 로그
    # =========================================================================

    def _write_log_file(
        self,
        results: Dict[str, PreOrderEvalResult],
        summary: Dict[str, int]
    ) -> Optional[str]:
        """
        일별 텍스트 로그 파일 생성

        파일: data/logs/order_decisions_YYYY-MM-DD.txt

        Returns:
            생성된 파일 경로 또는 None
        """
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
        time_str = now.strftime("%H:%M:%S")

        filename = f"order_decisions_{date_str}.txt"
        filepath = self._log_dir / filename

        # 결정별 그룹핑
        groups = {d: [] for d in EvalDecision}
        for result in results.values():
            groups[result.decision].append(result)

        # 각 그룹 내 인기도 내림차순 정렬
        for decision in groups:
            groups[decision].sort(key=lambda r: r.popularity_score, reverse=True)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                # 헤더
                f.write("=" * 64 + "\n")
                f.write("BGF 사전 발주 평가 리포트\n")
                f.write(f"날짜: {date_str} ({weekday_kr})\n")
                f.write(f"평가 시각: {time_str}\n")
                f.write("=" * 64 + "\n\n")

                # 설정값 기록
                weights = self.config.get_popularity_weights()
                f.write("[설정]\n")
                f.write(f"일평균기간={int(self.config.daily_avg_days.value)}일 | ")
                f.write(f"가중치: 일평균={weights['daily_avg']:.2f} 판매일={weights['sell_day_ratio']:.2f} 트렌드={weights['trend']:.2f} | ")
                f.write(f"노출임계: 긴급<{self.config.exposure_urgent.value:.1f} 일반<{self.config.exposure_normal.value:.1f} 충분>={self.config.exposure_sufficient.value:.1f} | ")
                f.write(f"품절빈도={self.config.stockout_freq_threshold.value:.0%}\n")
                if self._adaptive_thresholds:
                    f.write(f"분포적응: 고인기>={self._adaptive_thresholds['high']:.3f} 중인기>={self._adaptive_thresholds['medium']:.3f}\n")
                f.write("\n")

                # 요약
                f.write("[요약]\n")
                f.write(
                    f"총 평가: {summary['total']}개 | "
                    f"FORCE: {summary['FORCE_ORDER']} | "
                    f"URGENT: {summary['URGENT_ORDER']} | "
                    f"NORMAL: {summary['NORMAL_ORDER']} | "
                    f"PASS: {summary['PASS']} | "
                    f"SKIP: {summary['SKIP']}\n\n"
                )

                # 각 결정 그룹 출력
                decision_order = [
                    EvalDecision.FORCE_ORDER,
                    EvalDecision.URGENT_ORDER,
                    EvalDecision.NORMAL_ORDER,
                    EvalDecision.PASS,
                    EvalDecision.SKIP,
                ]

                for decision in decision_order:
                    items = groups[decision]
                    label = DECISION_LABELS[decision]
                    f.write(f"[{decision.value}] {label} ({len(items)}개)\n")
                    f.write("-" * 64 + "\n")

                    if not items:
                        f.write("(없음)\n")
                    else:
                        for r in items:
                            item_nm = (r.item_nm or "")[:20]
                            stockout_pct = int(r.stockout_frequency * 100)
                            f.write(
                                f"{r.item_cd} | {item_nm:<20} | {r.reason} | "
                                f"노출={r.exposure_days}일 "
                                f"인기={r.popularity_score:.2f} "
                                f"품절율={stockout_pct}% "
                                f"재고={r.current_stock} "
                                f"미입고={r.pending_qty} "
                                f"일평균={r.daily_avg}\n"
                            )
                    f.write("\n")

            logger.info(f"사전 평가 로그 저장: {filepath}")
            return str(filepath)

        except Exception as e:
            logger.warning(f"사전 평가 로그 저장 실패: {e}")
            return None
