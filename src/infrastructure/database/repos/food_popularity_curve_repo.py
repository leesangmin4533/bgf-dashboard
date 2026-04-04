"""
FoodPopularityCurveRepository — 소진율 곡선 저장/조회

상품별 입고 후 시간대별 누적 소진율을 EMA(지수이동평균)로 축적합니다.

용도:
- Phase 1.06에서 전일 발주 건의 소진율 갱신
- Phase 1.68~1.95 예측 시 purity_factor 조회
- 대시보드에서 인기도 순위 표시
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 소진율 곡선 추적 범위 (경과시간)
MAX_ELAPSED_HOURS_1ST = 24  # 1차배송: 07:00 ~ 다음날 06:59
MAX_ELAPSED_HOURS_2ND = 16  # 2차배송: 15:00 ~ 다음날 06:59


class FoodPopularityCurveRepository(BaseRepository):
    """소진율 곡선 Repository (stores/{store_id}.db)"""

    db_type = "store"

    def ensure_table(self):
        """테이블 존재 확인 (init_store_db가 처리하지만 안전장치)"""
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS food_popularity_curve (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_cd TEXT NOT NULL,
                    delivery_type TEXT NOT NULL,
                    elapsed_hours INTEGER NOT NULL,
                    avg_sold_rate REAL NOT NULL DEFAULT 0.0,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    last_updated TEXT NOT NULL,
                    store_id TEXT,
                    UNIQUE(item_cd, delivery_type, elapsed_hours)
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def upsert_curve(
        self,
        item_cd: str,
        delivery_type: str,
        elapsed_hours: int,
        new_sold_rate: float,
        alpha: float = 0.1,
    ) -> None:
        """EMA 증분 갱신으로 소진율 곡선 업데이트

        new_rate = α × today_rate + (1-α) × old_rate
        첫 기록(sample_count=0)이면 today_rate를 그대로 저장.

        Args:
            item_cd: 상품코드
            delivery_type: '1차' 또는 '2차'
            elapsed_hours: 입고 후 경과시간 (1~24)
            new_sold_rate: 오늘 관측된 소진율 (0.0~1.0)
            alpha: EMA 계수 (기본 0.1)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            # 기존 값 조회
            row = cursor.execute("""
                SELECT avg_sold_rate, sample_count
                FROM food_popularity_curve
                WHERE item_cd = ? AND delivery_type = ? AND elapsed_hours = ?
            """, (item_cd, delivery_type, elapsed_hours)).fetchone()

            now = self._now()
            if row:
                old_rate, old_count = row[0], row[1]
                # EMA 블렌딩
                blended = alpha * new_sold_rate + (1 - alpha) * old_rate
                cursor.execute("""
                    UPDATE food_popularity_curve
                    SET avg_sold_rate = ?, sample_count = ?, last_updated = ?,
                        store_id = ?
                    WHERE item_cd = ? AND delivery_type = ? AND elapsed_hours = ?
                """, (
                    round(blended, 4), old_count + 1, now,
                    self.store_id,
                    item_cd, delivery_type, elapsed_hours,
                ))
            else:
                # 첫 기록
                cursor.execute("""
                    INSERT INTO food_popularity_curve
                    (item_cd, delivery_type, elapsed_hours, avg_sold_rate,
                     sample_count, last_updated, store_id)
                    VALUES (?, ?, ?, ?, 1, ?, ?)
                """, (
                    item_cd, delivery_type, elapsed_hours,
                    round(new_sold_rate, 4), now, self.store_id,
                ))
            conn.commit()
        finally:
            conn.close()

    def upsert_curves_bulk(
        self,
        item_cd: str,
        delivery_type: str,
        hourly_rates: Dict[int, float],
        alpha: float = 0.1,
    ) -> int:
        """한 상품의 전체 경과시간 곡선을 일괄 갱신

        Args:
            item_cd: 상품코드
            delivery_type: '1차' 또는 '2차'
            hourly_rates: {경과시간: 소진율} (예: {1: 0.1, 2: 0.25, ...})
            alpha: EMA 계수

        Returns:
            갱신된 시간대 수
        """
        updated = 0
        for elapsed_hours, rate in hourly_rates.items():
            self.upsert_curve(item_cd, delivery_type, elapsed_hours, rate, alpha)
            updated += 1
        return updated

    def get_curve(
        self, item_cd: str, delivery_type: str
    ) -> Dict[int, float]:
        """특정 상품의 소진율 곡선 조회

        Returns:
            {경과시간: 소진율} 딕셔너리. 예: {1: 0.05, 2: 0.15, ..., 24: 0.72}
        """
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT elapsed_hours, avg_sold_rate
                FROM food_popularity_curve
                WHERE item_cd = ? AND delivery_type = ?
                ORDER BY elapsed_hours
            """, (item_cd, delivery_type)).fetchall()
            return {r[0]: r[1] for r in rows}
        finally:
            conn.close()

    def get_final_rate(
        self, item_cd: str, delivery_type: str
    ) -> Tuple[Optional[float], int]:
        """최종 소진율 + 샘플 수 반환

        최대 경과시간(1차=24, 2차=16)의 소진율을 반환합니다.

        Returns:
            (avg_sold_rate, sample_count) 또는 (None, 0)
        """
        max_hours = (
            MAX_ELAPSED_HOURS_1ST if delivery_type == "1차"
            else MAX_ELAPSED_HOURS_2ND
        )
        conn = self._get_conn()
        try:
            row = conn.execute("""
                SELECT avg_sold_rate, sample_count
                FROM food_popularity_curve
                WHERE item_cd = ? AND delivery_type = ? AND elapsed_hours = ?
            """, (item_cd, delivery_type, max_hours)).fetchone()
            if row:
                return (row[0], row[1])
            return (None, 0)
        finally:
            conn.close()

    def get_top_popular_items(
        self, delivery_type: str = "1차", limit: int = 20
    ) -> List[Dict]:
        """인기도 상위 상품 조회 (입고 후 6시간 내 소진율 기준)

        Returns:
            [{item_cd, avg_sold_rate_6h, sample_count}, ...]
        """
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT item_cd,
                       AVG(avg_sold_rate) as avg_rate_6h,
                       MIN(sample_count) as min_sample
                FROM food_popularity_curve
                WHERE delivery_type = ?
                  AND elapsed_hours BETWEEN 1 AND 6
                  AND sample_count >= 10
                GROUP BY item_cd
                ORDER BY avg_rate_6h DESC
                LIMIT ?
            """, (delivery_type, limit)).fetchall()
            return [
                {"item_cd": r[0], "avg_sold_rate_6h": r[1], "sample_count": r[2]}
                for r in rows
            ]
        finally:
            conn.close()
