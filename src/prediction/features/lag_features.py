"""
Lag Feature 계산기

과거 시점 데이터를 기반으로 시계열 패턴을 캡처합니다.
- lag_1: 어제 판매량
- lag_7: 일주일 전 판매량 (동일 요일)
- lag_14: 2주 전 판매량
- lag_28: 4주 전 판매량 (동일 요일)
- lag_365: 1년 전 판매량 (작년 동일 날짜)
"""

import sqlite3
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from pathlib import Path


@dataclass
class LagFeatures:
    """Lag Feature 결과"""
    item_cd: str
    target_date: str  # 예측 대상 날짜

    # 기본 Lag Features
    lag_1: Optional[int] = None   # 어제
    lag_7: Optional[int] = None   # 7일 전 (동일 요일)
    lag_14: Optional[int] = None  # 14일 전
    lag_28: Optional[int] = None  # 28일 전 (동일 요일)
    lag_365: Optional[int] = None  # 1년 전

    # 동일 요일 평균 (최근 4주)
    same_weekday_avg: Optional[float] = None

    # 전주 대비 변화율
    week_over_week_change: Optional[float] = None

    # 데이터 품질 정보
    available_lags: int = 0  # 사용 가능한 lag 수
    data_quality: str = "unknown"  # high/medium/low


class LagFeatureCalculator:
    """Lag Feature 계산기"""

    # 기본 Lag 기간 설정
    DEFAULT_LAGS = [1, 7, 14, 28, 365]

    def __init__(self, db_path: Optional[str] = None, store_id: Optional[str] = None) -> None:
        if db_path is None:
            db_path = Path(__file__).parent.parent.parent.parent / "data" / "bgf_sales.db"
        self.db_path = str(db_path)
        self.store_id = store_id

    def _get_connection(self, timeout: int = 30) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        return conn

    def calculate(
        self,
        item_cd: str,
        target_date: Optional[datetime] = None,
        lags: Optional[List[int]] = None
    ) -> LagFeatures:
        """
        상품의 Lag Features 계산

        Args:
            item_cd: 상품코드
            target_date: 예측 대상 날짜 (기본: 내일)
            lags: 계산할 lag 일수 리스트 (기본: [1, 7, 14, 28, 365])

        Returns:
            LagFeatures 객체
        """
        if target_date is None:
            target_date = datetime.now() + timedelta(days=1)

        if lags is None:
            lags = self.DEFAULT_LAGS

        target_str = target_date.strftime("%Y-%m-%d")

        # Lag 값 조회
        lag_values = self._get_lag_values(item_cd, target_date, lags)

        # 동일 요일 평균 계산
        same_weekday_avg = self._calculate_same_weekday_avg(item_cd, target_date)

        # 전주 대비 변화율 계산
        wow_change = self._calculate_week_over_week_change(item_cd, target_date)

        # 사용 가능한 lag 수 계산
        available_count = sum(1 for v in lag_values.values() if v is not None)

        # 데이터 품질 판단
        if available_count >= 4:
            data_quality = "high"
        elif available_count >= 2:
            data_quality = "medium"
        else:
            data_quality = "low"

        return LagFeatures(
            item_cd=item_cd,
            target_date=target_str,
            lag_1=lag_values.get(1),
            lag_7=lag_values.get(7),
            lag_14=lag_values.get(14),
            lag_28=lag_values.get(28),
            lag_365=lag_values.get(365),
            same_weekday_avg=same_weekday_avg,
            week_over_week_change=wow_change,
            available_lags=available_count,
            data_quality=data_quality,
        )

    def _get_lag_values(
        self,
        item_cd: str,
        target_date: datetime,
        lags: List[int]
    ) -> Dict[int, Optional[int]]:
        """각 lag 기간의 판매량 조회

        Args:
            item_cd: 상품코드
            target_date: 기준 날짜
            lags: lag 일수 리스트

        Returns:
            {lag일수: 판매량 또는 None} 딕셔너리
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        store_filter = "AND store_id = ?" if self.store_id else ""
        store_params = (self.store_id,) if self.store_id else ()

        result = {}
        for lag in lags:
            lag_date = target_date - timedelta(days=lag)
            lag_date_str = lag_date.strftime("%Y-%m-%d")

            cursor.execute(f"""
                SELECT sale_qty
                FROM daily_sales
                WHERE item_cd = ? AND sales_date = ? {store_filter}
            """, (item_cd, lag_date_str) + store_params)

            row = cursor.fetchone()
            result[lag] = row[0] if row else None

        conn.close()
        return result

    def _calculate_same_weekday_avg(
        self,
        item_cd: str,
        target_date: datetime,
        weeks: int = 4
    ) -> Optional[float]:
        """
        동일 요일 평균 계산 (최근 N주, 행사 기간 제외)

        행사 기간(1+1, 2+1)의 매출은 정상 수요가 아니므로 제외하고
        비행사 기간의 동요일만으로 평균을 계산한다.
        행사 제외 후 데이터가 없으면 전체(행사 포함) 평균으로 폴백.

        Args:
            item_cd: 상품코드
            target_date: 예측 대상 날짜
            weeks: 조회 주 수 (기본: 4주)

        Returns:
            동일 요일 평균 판매량
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # 동일 요일 날짜들 생성
        weekday = target_date.weekday()
        dates = []
        for i in range(1, weeks + 1):
            date = target_date - timedelta(weeks=i)
            dates.append(date.strftime("%Y-%m-%d"))

        if not dates:
            conn.close()
            return None

        store_filter = "AND store_id = ?" if self.store_id else ""
        store_params = [self.store_id] if self.store_id else []

        placeholders = ','.join(['?' for _ in dates])

        # 1차: 행사 기간 제외 동요일 평균
        try:
            cursor.execute(f"""
                SELECT AVG(ds.sale_qty) as avg_qty, COUNT(*) as cnt
                FROM daily_sales ds
                WHERE ds.item_cd = ? AND ds.sales_date IN ({placeholders}) {store_filter}
                AND NOT EXISTS (
                    SELECT 1 FROM promotions p
                    WHERE p.item_cd = ds.item_cd
                    AND p.is_active = 1
                    AND ds.sales_date BETWEEN p.start_date AND p.end_date
                )
            """, [item_cd] + dates + store_params)

            row = cursor.fetchone()
            if row and row['cnt'] and row['cnt'] > 0:
                conn.close()
                return round(row['avg_qty'], 2)
        except Exception:
            pass  # promotions 테이블 미존재 등 → 전체 평균 폴백

        # 2차 폴백: 전체(행사 포함) 동요일 평균
        cursor.execute(f"""
            SELECT AVG(sale_qty) as avg_qty, COUNT(*) as cnt
            FROM daily_sales
            WHERE item_cd = ? AND sales_date IN ({placeholders}) {store_filter}
        """, [item_cd] + dates + store_params)

        row = cursor.fetchone()
        conn.close()

        if row and row['cnt'] > 0:
            return round(row['avg_qty'], 2)
        return None

    def _calculate_week_over_week_change(
        self,
        item_cd: str,
        target_date: datetime
    ) -> Optional[float]:
        """
        전주 대비 변화율 계산

        Returns:
            변화율 (예: 0.15 = 15% 증가, -0.10 = 10% 감소)
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # 지난주 동일 요일
        last_week = target_date - timedelta(weeks=1)
        two_weeks_ago = target_date - timedelta(weeks=2)

        store_filter = "AND store_id = ?" if self.store_id else ""
        store_params = (self.store_id,) if self.store_id else ()

        cursor.execute(f"""
            SELECT sales_date, sale_qty
            FROM daily_sales
            WHERE item_cd = ? AND sales_date IN (?, ?) {store_filter}
            ORDER BY sales_date DESC
        """, (item_cd, last_week.strftime("%Y-%m-%d"), two_weeks_ago.strftime("%Y-%m-%d")) + store_params)

        rows = cursor.fetchall()
        conn.close()

        if len(rows) < 2:
            return None

        this_week_qty = rows[0]['sale_qty']  # 더 최근
        last_week_qty = rows[1]['sale_qty']

        if last_week_qty == 0:
            if this_week_qty > 0:
                return 1.0  # 100% 증가
            return 0.0

        change = (this_week_qty - last_week_qty) / last_week_qty
        return round(change, 3)

    def calculate_batch(
        self,
        item_codes: List[str],
        target_date: Optional[datetime] = None
    ) -> Dict[str, LagFeatures]:
        """
        여러 상품의 Lag Features 일괄 계산

        Args:
            item_codes: 상품코드 리스트
            target_date: 예측 대상 날짜

        Returns:
            {item_cd: LagFeatures} 딕셔너리
        """
        result = {}
        for item_cd in item_codes:
            result[item_cd] = self.calculate(item_cd, target_date)
        return result

    def get_lag_series(
        self,
        item_cd: str,
        target_date: datetime,
        days: int = 30
    ) -> List[Tuple[str, int]]:
        """
        지정 기간의 판매 이력 시계열 조회

        Args:
            item_cd: 상품코드
            target_date: 기준 날짜
            days: 조회 일수

        Returns:
            [(날짜, 판매량), ...] 리스트 (최신 순)
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        start_date = target_date - timedelta(days=days)

        store_filter = "AND store_id = ?" if self.store_id else ""
        store_params = (self.store_id,) if self.store_id else ()

        cursor.execute(f"""
            SELECT sales_date, sale_qty
            FROM daily_sales
            WHERE item_cd = ?
            AND sales_date >= ?
            AND sales_date < ?
            {store_filter}
            ORDER BY sales_date DESC
        """, (item_cd, start_date.strftime("%Y-%m-%d"), target_date.strftime("%Y-%m-%d")) + store_params)

        rows = cursor.fetchall()
        conn.close()

        return [(row['sales_date'], row['sale_qty']) for row in rows]


# =============================================================================
# 테스트/데모
# =============================================================================
if __name__ == "__main__":
    from datetime import datetime

    calculator = LagFeatureCalculator()

    # 테스트: DB에서 임의 상품 조회
    conn = sqlite3.connect(calculator.db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT item_cd FROM daily_sales
        WHERE sales_date >= date('now', '-7 days')
        LIMIT 5
    """)
    items = [row[0] for row in cursor.fetchall()]
    conn.close()

    print("\n[Lag Features 계산 테스트]")
    print("-" * 80)

    target = datetime.now() + timedelta(days=1)
    for item_cd in items:
        features = calculator.calculate(item_cd, target)
        print(f"\n상품: {item_cd}")
        print(f"  lag_1: {features.lag_1}, lag_7: {features.lag_7}, lag_14: {features.lag_14}")
        print(f"  lag_28: {features.lag_28}, lag_365: {features.lag_365}")
        print(f"  동일요일 평균: {features.same_weekday_avg}")
        print(f"  전주 대비: {features.week_over_week_change}")
        print(f"  품질: {features.data_quality} ({features.available_lags}/5)")
