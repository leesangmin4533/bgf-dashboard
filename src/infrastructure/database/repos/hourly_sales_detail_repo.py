"""
HourlySalesDetailRepository -- 시간대별 매출 상세(품목별) 데이터 저장/조회

STMB010 팝업의 `/stmb010/selPrdT3` API에서 수집한
시간대별 + 품목별 매출 상세 데이터를 관리합니다.

구조:
- raw_hourly_sales_detail: API 응답 원본 보존
- hourly_sales_detail: 가공 후 분석용 테이블

용도:
- 시간대별 어떤 상품이 얼마나 팔렸는지 추적
- 시간대별 수요 패턴 학습
- 행사 효과 분석 (MONTH_EVT)
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class HourlySalesDetailRepository(BaseRepository):
    """시간대별 매출 상세 Repository (stores/{store_id}.db)"""

    db_type = "store"

    DDL = """
    CREATE TABLE IF NOT EXISTS raw_hourly_sales_detail (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sales_date TEXT NOT NULL,
        hour INTEGER NOT NULL,
        ssv_response TEXT,
        item_count INTEGER DEFAULT 0,
        collected_at TEXT NOT NULL,
        UNIQUE(sales_date, hour)
    );

    CREATE TABLE IF NOT EXISTS hourly_sales_detail (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sales_date TEXT NOT NULL,
        hour INTEGER NOT NULL,
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        sale_qty INTEGER DEFAULT 0,
        sale_amt REAL DEFAULT 0,
        receipt_amt REAL DEFAULT 0,
        rate REAL DEFAULT 0,
        month_evt TEXT DEFAULT '',
        ord_item TEXT DEFAULT '',
        collected_at TEXT NOT NULL,
        UNIQUE(sales_date, hour, item_cd)
    );

    CREATE INDEX IF NOT EXISTS idx_hsd_date_hour
        ON hourly_sales_detail(sales_date, hour);
    CREATE INDEX IF NOT EXISTS idx_hsd_item
        ON hourly_sales_detail(item_cd);
    CREATE INDEX IF NOT EXISTS idx_hsd_date
        ON hourly_sales_detail(sales_date);
    CREATE INDEX IF NOT EXISTS idx_raw_hsd_date
        ON raw_hourly_sales_detail(sales_date);
    """

    def ensure_table(self) -> None:
        """테이블이 없으면 생성"""
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

    def save_raw(self, sales_date: str, hour: int, ssv_response: str,
                 item_count: int) -> None:
        """API 원본 응답 저장 (UPSERT)"""
        self.ensure_table()
        conn = self._get_conn()
        now = self._now()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO raw_hourly_sales_detail
                (sales_date, hour, ssv_response, item_count, collected_at)
                VALUES (?, ?, ?, ?, ?)
            """, (sales_date, hour, ssv_response, item_count, now))
            conn.commit()
        except Exception as e:
            logger.error(f"[HSD] raw 저장 실패 {sales_date} H{hour:02d}: {e}")
            conn.rollback()
        finally:
            conn.close()

    def save_detail(self, sales_date: str, hour: int,
                    items: List[Dict]) -> int:
        """가공된 품목별 상세 저장 (UPSERT)

        Args:
            sales_date: 'YYYY-MM-DD'
            hour: 0~23
            items: [{'item_cd': '...', 'item_nm': '...', 'sale_qty': 3, ...}]

        Returns:
            저장된 행 수
        """
        if not items:
            return 0

        self.ensure_table()
        conn = self._get_conn()
        now = self._now()
        saved = 0

        try:
            cursor = conn.cursor()
            for item in items:
                item_cd = item.get('item_cd', '').strip()
                if not item_cd:
                    continue
                cursor.execute("""
                    INSERT OR REPLACE INTO hourly_sales_detail
                    (sales_date, hour, item_cd, item_nm, sale_qty, sale_amt,
                     receipt_amt, rate, month_evt, ord_item, collected_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    sales_date,
                    hour,
                    item_cd,
                    item.get('item_nm', ''),
                    self._to_int(item.get('sale_qty', 0)),
                    self._to_float(item.get('sale_amt', 0)) or 0,
                    self._to_float(item.get('receipt_amt', 0)) or 0,
                    self._to_float(item.get('rate', 0)) or 0,
                    item.get('month_evt', ''),
                    item.get('ord_item', ''),
                    now,
                ))
                saved += 1
            conn.commit()
        except Exception as e:
            logger.error(f"[HSD] 상세 저장 실패 {sales_date} H{hour:02d}: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()

        return saved

    def get_hourly_detail(self, sales_date: str, hour: int) -> List[Dict]:
        """특정 날짜+시간대의 품목별 상세 조회"""
        self.ensure_table()
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT item_cd, item_nm, sale_qty, sale_amt, receipt_amt,
                       rate, month_evt
                FROM hourly_sales_detail
                WHERE sales_date = ? AND hour = ?
                ORDER BY sale_amt DESC
            """, (sales_date, hour))
            return [
                {
                    'item_cd': r[0], 'item_nm': r[1], 'sale_qty': r[2],
                    'sale_amt': r[3], 'receipt_amt': r[4], 'rate': r[5],
                    'month_evt': r[6],
                }
                for r in cursor.fetchall()
            ]
        finally:
            conn.close()

    def get_daily_item_summary(self, sales_date: str) -> List[Dict]:
        """특정 날짜의 전체 시간대 품목 집계"""
        self.ensure_table()
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT item_cd, item_nm,
                       SUM(sale_qty) as total_qty,
                       SUM(sale_amt) as total_amt,
                       MIN(hour) as first_hour,
                       MAX(hour) as last_hour,
                       COUNT(DISTINCT hour) as active_hours
                FROM hourly_sales_detail
                WHERE sales_date = ?
                GROUP BY item_cd
                ORDER BY total_amt DESC
            """, (sales_date,))
            return [
                {
                    'item_cd': r[0], 'item_nm': r[1], 'total_qty': r[2],
                    'total_amt': r[3], 'first_hour': r[4], 'last_hour': r[5],
                    'active_hours': r[6],
                }
                for r in cursor.fetchall()
            ]
        finally:
            conn.close()

    def get_item_hourly_pattern(self, item_cd: str, days: int = 30
                                ) -> Dict[int, float]:
        """특정 상품의 시간대별 평균 판매량 패턴 (최근 N일)"""
        self.ensure_table()
        conn = self._get_conn()
        try:
            start_date = (
                datetime.now() - timedelta(days=days)
            ).strftime('%Y-%m-%d')
            cursor = conn.cursor()
            cursor.execute("""
                SELECT hour, AVG(sale_qty) as avg_qty
                FROM hourly_sales_detail
                WHERE item_cd = ? AND sales_date >= ?
                GROUP BY hour
                ORDER BY hour
            """, (item_cd, start_date))
            return {r[0]: r[1] for r in cursor.fetchall()}
        finally:
            conn.close()

    def get_collected_hours(self, sales_date: str) -> List[int]:
        """특정 날짜에 수집 완료된 시간대 목록"""
        self.ensure_table()
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT hour FROM hourly_sales_detail
                WHERE sales_date = ?
                ORDER BY hour
            """, (sales_date,))
            return [r[0] for r in cursor.fetchall()]
        finally:
            conn.close()

    def get_failed_hours(self, sales_date: str) -> List[int]:
        """수집 실패한 시간대 목록 (현재 시각 기준 과거 시간대 중 미수집)"""
        collected = set(self.get_collected_hours(sales_date))
        now_hour = datetime.now().hour
        today = datetime.now().strftime('%Y-%m-%d')

        if sales_date == today:
            # 오늘이면 현재시각 이전 시간대만
            expected = set(range(0, now_hour))
        else:
            # 과거 날짜면 0~23 전체
            expected = set(range(0, 24))

        return sorted(expected - collected)
