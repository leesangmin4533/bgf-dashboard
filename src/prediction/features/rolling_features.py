"""
Rolling Feature 계산기

이동 통계를 활용한 시계열 패턴 캡처:
- rolling_mean: 이동 평균
- rolling_std: 이동 표준편차 (변동성)
- rolling_min: 이동 최소값
- rolling_max: 이동 최대값
- ewm_mean: 지수 가중 이동 평균 (최근 데이터에 가중치)
"""

import sqlite3
import math
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict, Tuple
from pathlib import Path


@dataclass
class RollingFeatures:
    """Rolling Feature 결과"""
    item_cd: str
    target_date: str

    # 7일 이동 통계
    rolling_mean_7: Optional[float] = None
    rolling_std_7: Optional[float] = None
    rolling_min_7: Optional[int] = None
    rolling_max_7: Optional[int] = None

    # 14일 이동 통계
    rolling_mean_14: Optional[float] = None
    rolling_std_14: Optional[float] = None
    rolling_min_14: Optional[int] = None
    rolling_max_14: Optional[int] = None

    # 28일 이동 통계
    rolling_mean_28: Optional[float] = None
    rolling_std_28: Optional[float] = None
    rolling_min_28: Optional[int] = None
    rolling_max_28: Optional[int] = None

    # 90일 이동 통계 (장기 트렌드)
    rolling_mean_90: Optional[float] = None
    rolling_std_90: Optional[float] = None

    # 지수 가중 이동 평균 (최근 데이터 가중치 높음)
    ewm_mean_7: Optional[float] = None   # span=7
    ewm_mean_14: Optional[float] = None  # span=14

    # 변동성 지표
    coefficient_of_variation: Optional[float] = None  # CV = std / mean (28일 기준)

    # 트렌드 지표
    trend_slope: Optional[float] = None  # 7일 vs 28일 비교
    is_trending_up: bool = False
    is_trending_down: bool = False

    # 데이터 품질
    data_days: int = 0  # 실제 사용된 데이터 일수


class RollingFeatureCalculator:
    """Rolling Feature 계산기"""

    # 기본 윈도우 크기
    DEFAULT_WINDOWS = [7, 14, 28, 90]

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
        windows: Optional[List[int]] = None
    ) -> RollingFeatures:
        """
        상품의 Rolling Features 계산

        Args:
            item_cd: 상품코드
            target_date: 예측 대상 날짜 (기본: 내일)
            windows: 윈도우 크기 리스트 (기본: [7, 14, 28, 90])

        Returns:
            RollingFeatures 객체
        """
        if target_date is None:
            target_date = datetime.now() + timedelta(days=1)

        if windows is None:
            windows = self.DEFAULT_WINDOWS

        target_str = target_date.strftime("%Y-%m-%d")

        # 판매 이력 조회 (최대 윈도우 + 여유분)
        max_window = max(windows)
        sales_data = self._get_sales_data(item_cd, target_date, max_window + 7)

        if not sales_data:
            return RollingFeatures(
                item_cd=item_cd,
                target_date=target_str,
                data_days=0
            )

        # 판매량만 추출 (최신 순)
        sales = [s[1] for s in sales_data]

        # 각 윈도우별 통계 계산
        stats = {}
        for window in windows:
            stats[window] = self._calculate_window_stats(sales, window)

        # EWM 계산
        ewm_7 = self._calculate_ewm(sales, span=7)
        ewm_14 = self._calculate_ewm(sales, span=14)

        # 변동성 계수 (28일 기준)
        cv = None
        if stats.get(28) and stats[28]['mean'] and stats[28]['mean'] > 0:
            cv = round(stats[28]['std'] / stats[28]['mean'], 3) if stats[28]['std'] else 0

        # 트렌드 계산 (7일 vs 28일 평균 비교)
        trend_slope, is_up, is_down = self._calculate_trend(stats)

        return RollingFeatures(
            item_cd=item_cd,
            target_date=target_str,
            # 7일
            rolling_mean_7=stats.get(7, {}).get('mean'),
            rolling_std_7=stats.get(7, {}).get('std'),
            rolling_min_7=stats.get(7, {}).get('min'),
            rolling_max_7=stats.get(7, {}).get('max'),
            # 14일
            rolling_mean_14=stats.get(14, {}).get('mean'),
            rolling_std_14=stats.get(14, {}).get('std'),
            rolling_min_14=stats.get(14, {}).get('min'),
            rolling_max_14=stats.get(14, {}).get('max'),
            # 28일
            rolling_mean_28=stats.get(28, {}).get('mean'),
            rolling_std_28=stats.get(28, {}).get('std'),
            rolling_min_28=stats.get(28, {}).get('min'),
            rolling_max_28=stats.get(28, {}).get('max'),
            # 90일
            rolling_mean_90=stats.get(90, {}).get('mean'),
            rolling_std_90=stats.get(90, {}).get('std'),
            # EWM
            ewm_mean_7=ewm_7,
            ewm_mean_14=ewm_14,
            # 변동성
            coefficient_of_variation=cv,
            # 트렌드
            trend_slope=trend_slope,
            is_trending_up=is_up,
            is_trending_down=is_down,
            # 데이터 품질
            data_days=len(sales),
        )

    def _get_sales_data(
        self,
        item_cd: str,
        target_date: datetime,
        days: int
    ) -> List[Tuple[str, int]]:
        """판매 이력 조회 (최신 순, 0판매일 보간 + 행사일 imputation)

        DB에 기록이 없는 날짜를 sale_qty=0으로 보간하여
        연속 시계열을 반환한다. 이를 통해 data_days가
        달력일 기준으로 계산되어 ML 활성화 조건을 정확히 반영.

        행사 기간(1+1, 2+1) 판매량은 비행사일 평균으로 대체하여
        Rolling 통계가 행사 매출에 의해 왜곡되는 것을 방지한다.

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
            ORDER BY sales_date ASC
        """, (item_cd, start_date.strftime("%Y-%m-%d"), target_date.strftime("%Y-%m-%d")) + store_params)

        rows = cursor.fetchall()

        # 행사 기간 조회
        promo_dates = set()
        try:
            cursor.execute("""
                SELECT start_date, end_date
                FROM promotions
                WHERE item_cd = ? AND is_active = 1
            """, (item_cd,))
            promo_periods = [(r[0], r[1]) for r in cursor.fetchall()]
        except Exception:
            promo_periods = []

        conn.close()

        if not rows:
            return []

        # 0판매일 보간: 첫 기록~마지막 기록 사이 빈 날짜를 0으로 채움
        existing = {row['sales_date']: row['sale_qty'] for row in rows}
        first = datetime.strptime(rows[0]['sales_date'], "%Y-%m-%d")
        last = datetime.strptime(rows[-1]['sales_date'], "%Y-%m-%d")

        filled = []
        cur = first
        while cur <= last:
            ds = cur.strftime("%Y-%m-%d")
            qty = existing.get(ds, 0)
            # 행사 기간 판매일 마킹
            is_promo = any(s <= ds <= e for s, e in promo_periods)
            if is_promo:
                promo_dates.add(ds)
            filled.append((ds, qty, is_promo))
            cur += timedelta(days=1)

        # 행사일 imputation: 비행사일 평균으로 대체
        if promo_dates:
            non_promo_sales = [qty for _, qty, is_p in filled if not is_p]
            if non_promo_sales:
                non_promo_avg = sum(non_promo_sales) / len(non_promo_sales)
                filled = [
                    (ds, round(non_promo_avg) if is_p else qty, is_p)
                    for ds, qty, is_p in filled
                ]

        # (날짜, 판매량) 튜플로 변환, 최신 순 정렬
        result = [(ds, qty) for ds, qty, _ in filled]
        result.reverse()
        return result

    def _calculate_window_stats(
        self,
        sales: List[int],
        window: int
    ) -> Dict[str, Optional[float]]:
        """윈도우 크기에 대한 통계 계산

        Args:
            sales: 판매량 리스트 (최신 순)
            window: 윈도우 크기 (일)

        Returns:
            {"mean", "std", "min", "max"} 통계 딕셔너리
        """
        if len(sales) < window:
            # 데이터가 부족하면 있는 만큼만 사용
            window_data = sales
        else:
            window_data = sales[:window]

        if not window_data:
            return {'mean': None, 'std': None, 'min': None, 'max': None}

        n = len(window_data)
        mean_val = sum(window_data) / n

        # 표준편차 계산
        if n > 1:
            variance = sum((x - mean_val) ** 2 for x in window_data) / (n - 1)
            std_val = math.sqrt(variance)
        else:
            std_val = 0.0

        return {
            'mean': round(mean_val, 2),
            'std': round(std_val, 2),
            'min': min(window_data),
            'max': max(window_data),
        }

    def _calculate_ewm(
        self,
        sales: List[int],
        span: int
    ) -> Optional[float]:
        """
        지수 가중 이동 평균 계산 (Exponential Weighted Moving Average)

        EWM = 최근 데이터에 더 높은 가중치 부여
        alpha = 2 / (span + 1)

        Args:
            sales: 판매량 리스트 (최신 순)
            span: span 파라미터

        Returns:
            EWM 평균값
        """
        if not sales:
            return None

        # 최신 순으로 되어 있으므로 역순으로 변환
        reversed_sales = list(reversed(sales[:span * 2]))  # 충분한 데이터 사용

        if not reversed_sales:
            return None

        alpha = 2 / (span + 1)
        ewm = reversed_sales[0]  # 초기값

        for i in range(1, len(reversed_sales)):
            ewm = alpha * reversed_sales[i] + (1 - alpha) * ewm

        return round(ewm, 2)

    def _calculate_trend(
        self,
        stats: Dict[int, Dict[str, Any]]
    ) -> Tuple[Optional[float], bool, bool]:
        """
        트렌드 계산 (7일 vs 28일 평균 비교)

        Args:
            stats: 윈도우별 통계 딕셔너리

        Returns:
            (slope, is_up, is_down)
            slope: 양수면 상승 트렌드, 음수면 하락 트렌드
        """
        mean_7 = stats.get(7, {}).get('mean')
        mean_28 = stats.get(28, {}).get('mean')

        if mean_7 is None or mean_28 is None or mean_28 == 0:
            return None, False, False

        # slope = (최근 7일 - 28일) / 28일
        slope = round((mean_7 - mean_28) / mean_28, 3)

        # 10% 이상 변화를 트렌드로 판단
        is_up = slope >= 0.10
        is_down = slope <= -0.10

        return slope, is_up, is_down

    def calculate_volatility_safety_stock(
        self,
        item_cd: str,
        base_safety_days: float,
        target_date: Optional[datetime] = None
    ) -> Tuple[float, str]:
        """
        변동성 기반 안전재고 계산

        CV(변동계수)에 따라 안전재고 일수 조정:
        - CV < 0.3: 안정적 → 기본 안전재고
        - CV 0.3~0.5: 보통 → 기본 × 1.2
        - CV 0.5~0.8: 불안정 → 기본 × 1.5
        - CV > 0.8: 매우 불안정 → 기본 × 2.0

        Args:
            item_cd: 상품코드
            base_safety_days: 기본 안전재고 일수
            target_date: 예측 대상 날짜

        Returns:
            (조정된 안전재고 일수, 변동성 레벨)
        """
        features = self.calculate(item_cd, target_date, windows=[28])
        cv = features.coefficient_of_variation

        if cv is None or cv < 0.3:
            return base_safety_days, "stable"
        elif cv < 0.5:
            return base_safety_days * 1.2, "normal"
        elif cv < 0.8:
            return base_safety_days * 1.5, "volatile"
        else:
            return base_safety_days * 2.0, "highly_volatile"

    def calculate_batch(
        self,
        item_codes: List[str],
        target_date: Optional[datetime] = None
    ) -> Dict[str, RollingFeatures]:
        """
        여러 상품의 Rolling Features 일괄 계산

        Args:
            item_codes: 상품코드 리스트
            target_date: 예측 대상 날짜

        Returns:
            {item_cd: RollingFeatures} 딕셔너리
        """
        result = {}
        for item_cd in item_codes:
            result[item_cd] = self.calculate(item_cd, target_date)
        return result


# =============================================================================
# 테스트/데모
# =============================================================================
if __name__ == "__main__":
    from datetime import datetime

    calculator = RollingFeatureCalculator()

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

    print("\n[Rolling Features 계산 테스트]")
    print("-" * 80)

    target = datetime.now() + timedelta(days=1)
    for item_cd in items:
        features = calculator.calculate(item_cd, target)
        print(f"\n상품: {item_cd}")
        print(f"  7일 평균: {features.rolling_mean_7}, 표준편차: {features.rolling_std_7}")
        print(f"  28일 평균: {features.rolling_mean_28}, 표준편차: {features.rolling_std_28}")
        print(f"  EWM(7): {features.ewm_mean_7}, EWM(14): {features.ewm_mean_14}")
        print(f"  변동계수(CV): {features.coefficient_of_variation}")
        print(f"  트렌드: {features.trend_slope} (상승={features.is_trending_up}, 하락={features.is_trending_down})")
        print(f"  데이터 일수: {features.data_days}")

        # 변동성 기반 안전재고 테스트
        safety, level = calculator.calculate_volatility_safety_stock(item_cd, 1.0, target)
        print(f"  변동성 안전재고: {safety:.2f}일 ({level})")
