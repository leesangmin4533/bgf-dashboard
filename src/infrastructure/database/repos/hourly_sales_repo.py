"""
HourlySalesRepository — 시간대별 매출 데이터 저장/조회

STMB010 Direct API에서 수집한 시간대별(00~23시) 매출 데이터를 관리합니다.

용도:
- 시간대별 매출액/판매건수 저장 (24행/일)
- 최근 N일 시간대 분포 계산
- 배송차수 동적 수요 비율 산출 (DELIVERY_TIME_DEMAND_RATIO 대체)
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class HourlySalesRepository(BaseRepository):
    """시간대별 매출 Repository (stores/{store_id}.db)"""

    db_type = "store"

    DDL = """
    CREATE TABLE IF NOT EXISTS hourly_sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sales_date TEXT NOT NULL,
        hour INTEGER NOT NULL,
        sale_amt REAL DEFAULT 0,
        sale_cnt INTEGER DEFAULT 0,
        sale_cnt_danga REAL DEFAULT 0,
        rate REAL DEFAULT 0,
        collected_at TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(sales_date, hour)
    );
    CREATE INDEX IF NOT EXISTS idx_hourly_sales_date
        ON hourly_sales(sales_date);
    """

    def ensure_table(self) -> None:
        """hourly_sales 테이블이 없으면 생성"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            for stmt in self.DDL.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    cursor.execute(stmt)
            conn.commit()
        finally:
            conn.close()

    def save_hourly_sales(self, data: List[Dict], sales_date: str) -> int:
        """시간대별 매출 저장 (24행 UPSERT)

        Args:
            data: [{'hour': 0, 'sale_amt': 125000, 'sale_cnt': 15, ...}, ...]
            sales_date: 'YYYY-MM-DD' 형식

        Returns:
            저장된 행 수
        """
        if not data:
            return 0

        self.ensure_table()
        conn = self._get_conn()
        now = self._now()
        saved = 0

        try:
            cursor = conn.cursor()
            for entry in data:
                cursor.execute("""
                    INSERT OR REPLACE INTO hourly_sales
                    (sales_date, hour, sale_amt, sale_cnt, sale_cnt_danga, rate,
                     collected_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    sales_date,
                    int(entry.get('hour', 0)),
                    self._to_float(entry.get('sale_amt', 0)),
                    self._to_int(entry.get('sale_cnt', 0)),
                    self._to_float(entry.get('sale_cnt_danga', 0)),
                    self._to_float(entry.get('rate', 0)),
                    now,
                    now,
                ))
                saved += 1
            conn.commit()
            logger.info(f"[HourlySales] {sales_date} {saved}건 저장")
        except Exception as e:
            logger.error(f"[HourlySales] 저장 실패: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()

        return saved

    def get_hourly_sales(self, sales_date: str) -> List[Dict]:
        """특정 날짜의 시간대별 매출 조회

        Returns:
            [{'hour': 0, 'sale_amt': 125000, 'sale_cnt': 15, ...}, ...]
        """
        self.ensure_table()
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT hour, sale_amt, sale_cnt, sale_cnt_danga, rate
                FROM hourly_sales
                WHERE sales_date = ?
                ORDER BY hour
            """, (sales_date,))

            rows = cursor.fetchall()
            return [
                {
                    'hour': row[0],
                    'sale_amt': row[1],
                    'sale_cnt': row[2],
                    'sale_cnt_danga': row[3],
                    'rate': row[4],
                }
                for row in rows
            ]
        finally:
            conn.close()

    def get_recent_hourly_distribution(self, days: int = 7) -> Dict[int, float]:
        """최근 N일 시간대별 평균 매출 비율 조회

        Args:
            days: 조회 기간 (기본 7일)

        Returns:
            {0: avg_amt_hour0, 1: avg_amt_hour1, ..., 23: avg_amt_hour23}
        """
        self.ensure_table()
        conn = self._get_conn()
        try:
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

            cursor = conn.cursor()
            cursor.execute("""
                SELECT hour, AVG(sale_amt) as avg_amt
                FROM hourly_sales
                WHERE sales_date BETWEEN ? AND ?
                GROUP BY hour
                ORDER BY hour
            """, (start_date, end_date))

            rows = cursor.fetchall()
            if not rows:
                return {}

            return {row[0]: row[1] for row in rows}
        finally:
            conn.close()

    def calculate_time_demand_ratio(self, days: int = 14) -> Dict[str, float]:
        """배송차수별 시간대 수요 비율 계산 (1차/2차 분리, hourly_sales_detail 기반)

        1차 상품(item_nm LIKE '%1')과 2차 상품(item_nm LIKE '%2')을
        분리하여 각각의 주간(07-19시) 수요 비중을 계산합니다.

        Args:
            days: 조회 기간 (기본 14일)

        Returns:
            {'1차': 0.50, '2차': 1.00} 또는 {} (데이터 없음)
        """
        try:
            conn = self._get_conn()
            try:
                # 1차 상품 (item_nm 끝자리 '1', 푸드류 001~005)
                rows_1 = conn.execute("""
                    SELECT h.hour, SUM(h.sale_qty) as qty
                    FROM hourly_sales_detail h
                    WHERE h.item_cd IN (
                        SELECT DISTINCT item_cd FROM daily_sales
                        WHERE item_nm LIKE '%1'
                          AND mid_cd IN ('001','002','003','004','005')
                    )
                    AND h.sales_date >= date('now', ?)
                    AND h.sales_date < date('now')
                    GROUP BY h.hour
                """, (f"-{days} days",)).fetchall()

                # 2차 상품 (item_nm 끝자리 '2', 푸드류 001~005)
                rows_2 = conn.execute("""
                    SELECT h.hour, SUM(h.sale_qty) as qty
                    FROM hourly_sales_detail h
                    WHERE h.item_cd IN (
                        SELECT DISTINCT item_cd FROM daily_sales
                        WHERE item_nm LIKE '%2'
                          AND mid_cd IN ('001','002','003','004','005')
                    )
                    AND h.sales_date >= date('now', ?)
                    AND h.sales_date < date('now')
                    GROUP BY h.hour
                """, (f"-{days} days",)).fetchall()
            finally:
                conn.close()

            def _calc_daytime_ratio(rows) -> Optional[float]:
                """07~19시 주간 비중 계산 (0.50~0.90 클램프)"""
                if not rows:
                    return None
                dist = {int(r[0]): float(r[1]) for r in rows}
                total = sum(dist.values())
                if total <= 0:
                    return None
                daytime = sum(dist.get(h, 0) for h in range(7, 20))
                ratio = round(daytime / total, 3)
                return max(0.50, min(0.90, ratio))

            ratio_1 = _calc_daytime_ratio(rows_1)
            ratio_2 = _calc_daytime_ratio(rows_2)

            result = {}
            if ratio_1 is not None:
                result["1차"] = ratio_1
            if ratio_2 is not None:
                logger.info(f"[HourlySales] 2차 주간비중={ratio_2} (참조용, 고정 1.00 사용)")
            # 2차는 항상 1.00 (하루 전체 수요 대응)
            result["2차"] = 1.00

            return result if result else {}
        except Exception as e:
            logger.warning(f"[HourlySales] 동적 비율 계산 실패: {e}")
            return {}
