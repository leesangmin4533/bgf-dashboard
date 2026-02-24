"""
WasteCauseRepository -- 폐기 원인 분석 결과 저장소

폐기 원인 분류 결과 및 피드백 데이터를 waste_cause_analysis 테이블에 관리합니다.
"""

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class WasteCauseRepository(BaseRepository):
    """폐기 원인 분석 결과 저장소"""

    db_type = "store"

    def upsert_cause(self, record: dict) -> int:
        """폐기 원인 분석 결과 저장 (UPSERT on store_id+waste_date+item_cd)

        Args:
            record: 분석 결과 dict (store_id, waste_date, item_cd 필수)
        Returns:
            저장된 레코드의 id
        """
        conn = self._get_conn()
        now = self._now()
        try:
            cursor = conn.execute("""
                INSERT INTO waste_cause_analysis (
                    store_id, analysis_date, waste_date, item_cd, item_nm,
                    mid_cd, waste_qty, waste_source, primary_cause,
                    secondary_cause, confidence,
                    order_qty, daily_avg, predicted_qty, actual_sold_qty,
                    expiration_days, trend_ratio, sell_day_ratio,
                    weather_factor, promo_factor, holiday_factor,
                    feedback_action, feedback_multiplier, feedback_expiry_date,
                    is_applied, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    0, ?
                )
                ON CONFLICT(store_id, waste_date, item_cd) DO UPDATE SET
                    analysis_date = excluded.analysis_date,
                    item_nm = excluded.item_nm,
                    mid_cd = excluded.mid_cd,
                    waste_qty = excluded.waste_qty,
                    waste_source = excluded.waste_source,
                    primary_cause = excluded.primary_cause,
                    secondary_cause = excluded.secondary_cause,
                    confidence = excluded.confidence,
                    order_qty = excluded.order_qty,
                    daily_avg = excluded.daily_avg,
                    predicted_qty = excluded.predicted_qty,
                    actual_sold_qty = excluded.actual_sold_qty,
                    expiration_days = excluded.expiration_days,
                    trend_ratio = excluded.trend_ratio,
                    sell_day_ratio = excluded.sell_day_ratio,
                    weather_factor = excluded.weather_factor,
                    promo_factor = excluded.promo_factor,
                    holiday_factor = excluded.holiday_factor,
                    feedback_action = excluded.feedback_action,
                    feedback_multiplier = excluded.feedback_multiplier,
                    feedback_expiry_date = excluded.feedback_expiry_date,
                    is_applied = 0
            """, (
                record.get("store_id", self.store_id),
                record.get("analysis_date", now[:10]),
                record["waste_date"],
                record["item_cd"],
                record.get("item_nm"),
                record.get("mid_cd"),
                record.get("waste_qty", 0),
                record.get("waste_source", "daily_sales"),
                record["primary_cause"],
                record.get("secondary_cause"),
                record.get("confidence", 0.0),
                record.get("order_qty"),
                record.get("daily_avg"),
                record.get("predicted_qty"),
                record.get("actual_sold_qty"),
                record.get("expiration_days"),
                record.get("trend_ratio"),
                record.get("sell_day_ratio"),
                record.get("weather_factor"),
                record.get("promo_factor"),
                record.get("holiday_factor"),
                record.get("feedback_action", "DEFAULT"),
                record.get("feedback_multiplier", 1.0),
                record.get("feedback_expiry_date"),
                now,
            ))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"폐기 원인 저장 실패 ({record.get('item_cd')}): {e}")
            raise

    def get_active_feedback(
        self, item_cd: str, as_of_date: str, store_id: Optional[str] = None
    ) -> Optional[dict]:
        """미만료 최신 피드백 1건 조회

        Args:
            item_cd: 상품 코드
            as_of_date: 기준일 (YYYY-MM-DD)
            store_id: 매장 코드 (기본값: self.store_id)
        Returns:
            피드백 dict 또는 None
        """
        sid = store_id or self.store_id
        conn = self._get_conn()
        row = conn.execute("""
            SELECT * FROM waste_cause_analysis
            WHERE item_cd = ? AND store_id = ?
              AND feedback_expiry_date >= ?
              AND feedback_multiplier < 1.0
            ORDER BY waste_date DESC
            LIMIT 1
        """, (item_cd, sid, as_of_date)).fetchone()
        return dict(row) if row else None

    def get_active_feedbacks_batch(
        self, item_cds: List[str], as_of_date: str, store_id: Optional[str] = None
    ) -> Dict[str, dict]:
        """여러 상품의 미만료 피드백 일괄 조회 (프리로드용)

        Args:
            item_cds: 상품 코드 목록
            as_of_date: 기준일 (YYYY-MM-DD)
            store_id: 매장 코드
        Returns:
            {item_cd: feedback_dict} 매핑
        """
        if not item_cds:
            return {}
        sid = store_id or self.store_id
        conn = self._get_conn()
        placeholders = ",".join("?" for _ in item_cds)
        rows = conn.execute(f"""
            SELECT * FROM waste_cause_analysis
            WHERE item_cd IN ({placeholders})
              AND store_id = ?
              AND feedback_expiry_date >= ?
              AND feedback_multiplier < 1.0
            ORDER BY waste_date DESC
        """, (*item_cds, sid, as_of_date)).fetchall()

        result = {}
        for row in rows:
            d = dict(row)
            ic = d["item_cd"]
            if ic not in result:
                result[ic] = d
        return result

    def get_causes_for_period(
        self, start_date: str, end_date: str, store_id: Optional[str] = None
    ) -> List[dict]:
        """기간 내 모든 원인 분석 결과 조회

        Args:
            start_date: 시작일 (YYYY-MM-DD)
            end_date: 종료일 (YYYY-MM-DD)
            store_id: 매장 코드
        Returns:
            분석 결과 list
        """
        sid = store_id or self.store_id
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT * FROM waste_cause_analysis
            WHERE store_id = ? AND waste_date BETWEEN ? AND ?
            ORDER BY waste_date DESC, item_cd
        """, (sid, start_date, end_date)).fetchall()
        return [dict(r) for r in rows]

    def get_cause_summary(
        self, days: int = 14, store_id: Optional[str] = None
    ) -> dict:
        """원인별 집계 (대시보드 위젯용)

        Args:
            days: 조회 기간 (최근 N일)
            store_id: 매장 코드
        Returns:
            {cause: {"count": N, "total_qty": M}} 매핑
        """
        sid = store_id or self.store_id
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT primary_cause,
                   COUNT(*) AS count,
                   COALESCE(SUM(waste_qty), 0) AS total_qty
            FROM waste_cause_analysis
            WHERE store_id = ? AND waste_date >= ?
            GROUP BY primary_cause
        """, (sid, start_date)).fetchall()
        return {
            row["primary_cause"]: {
                "count": row["count"],
                "total_qty": row["total_qty"],
            }
            for row in rows
        }

    def mark_applied(
        self, item_cd: str, waste_date: str, store_id: Optional[str] = None
    ) -> None:
        """피드백 적용 완료 마킹

        Args:
            item_cd: 상품 코드
            waste_date: 폐기 발생일
            store_id: 매장 코드
        """
        sid = store_id or self.store_id
        conn = self._get_conn()
        conn.execute("""
            UPDATE waste_cause_analysis
            SET is_applied = 1
            WHERE item_cd = ? AND waste_date = ? AND store_id = ?
        """, (item_cd, waste_date, sid))
        conn.commit()
