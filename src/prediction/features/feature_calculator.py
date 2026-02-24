"""
통합 Feature Calculator

Lag Features와 Rolling Features를 통합하여 예측에 사용할 수 있는
종합적인 Feature Set을 제공합니다.

주요 기능:
1. Lag Features + Rolling Features 통합 계산
2. 가중 예측값 계산 (EWM, 동일요일 평균 활용)
3. 변동성 기반 안전재고 조정
4. 트렌드 기반 발주량 조정
"""

import sqlite3
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict, Tuple
from pathlib import Path

from .lag_features import LagFeatureCalculator, LagFeatures
from .rolling_features import RollingFeatureCalculator, RollingFeatures


@dataclass
class FeatureResult:
    """통합 Feature 결과"""
    item_cd: str
    target_date: str

    # 기본 예측값
    base_prediction: float = 0.0           # 단순 평균
    ewm_prediction: float = 0.0            # EWM 기반 예측
    same_weekday_prediction: float = 0.0   # 동일 요일 평균
    weighted_prediction: float = 0.0       # 가중 결합 예측

    # Lag Features
    lag_features: Optional[LagFeatures] = None

    # Rolling Features
    rolling_features: Optional[RollingFeatures] = None

    # 안전재고 조정
    base_safety_days: float = 1.0          # 기본 안전재고 일수
    adjusted_safety_days: float = 1.0      # 변동성 조정 후
    volatility_level: str = "stable"       # stable/normal/volatile/highly_volatile

    # 트렌드 조정
    trend_adjustment: float = 1.0          # 트렌드 기반 조정 계수
    trend_direction: str = "stable"        # up/down/stable

    # 메타 정보
    data_quality: str = "unknown"          # high/medium/low
    calculation_method: str = ""           # 사용된 계산 방법 설명


class FeatureCalculator:
    """통합 Feature 계산기"""

    # 가중치 설정 (기본값 - medium quality)
    WEIGHT_CONFIG = {
        "simple_avg": 0.20,       # 단순 이동평균 (7일)
        "ewm": 0.30,              # 지수 가중 이동평균
        "same_weekday": 0.30,     # 동일 요일 평균
        "rolling_28": 0.20,       # 28일 이동평균 (장기 앵커)
    }

    # 데이터 품질별 적응형 가중치
    ADAPTIVE_WEIGHTS = {
        "high": {      # 14+ days: EWM과 요일패턴 강화, 28일 안정 앵커
            "simple_avg": 0.15,
            "ewm": 0.30,
            "same_weekday": 0.30,
            "rolling_28": 0.25,
        },
        "medium": {    # 7-13 days: 균형있는 가중치
            "simple_avg": 0.25,
            "ewm": 0.30,
            "same_weekday": 0.30,
            "rolling_28": 0.15,
        },
        "low": {       # <7 days: 단순평균 우선, 28일 없을 수 있음
            "simple_avg": 0.45,
            "ewm": 0.30,
            "same_weekday": 0.15,
            "rolling_28": 0.10,
        },
    }

    # 트렌드 조정 설정
    TREND_ADJUSTMENT = {
        "strong_up": 1.15,        # 강한 상승 트렌드 (+20% 이상)
        "up": 1.08,               # 상승 트렌드 (+10~20%)
        "stable": 1.0,            # 안정
        "down": 0.92,             # 하락 트렌드 (-10~20%)
        "strong_down": 0.85,      # 강한 하락 트렌드 (-20% 이상)
    }

    def __init__(self, db_path: Optional[str] = None, store_id: Optional[str] = None) -> None:
        if db_path is None:
            db_path = Path(__file__).parent.parent.parent.parent / "data" / "bgf_sales.db"
        self.db_path = str(db_path)
        self.store_id = store_id

        # 개별 계산기 초기화
        self.lag_calculator = LagFeatureCalculator(db_path=self.db_path, store_id=self.store_id)
        self.rolling_calculator = RollingFeatureCalculator(db_path=self.db_path, store_id=self.store_id)

    def _get_adaptive_weights(self, data_quality: str) -> Dict[str, float]:
        """데이터 품질에 따른 적응형 가중치 반환

        Args:
            data_quality: 데이터 품질 (high, medium, low)

        Returns:
            가중치 딕셔너리
        """
        return self.ADAPTIVE_WEIGHTS.get(data_quality, self.WEIGHT_CONFIG)

    def _get_connection(self, timeout: int = 30) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        return conn

    def calculate(
        self,
        item_cd: str,
        target_date: Optional[datetime] = None,
        base_safety_days: float = 1.0
    ) -> FeatureResult:
        """
        상품의 통합 Features 계산

        Args:
            item_cd: 상품코드
            target_date: 예측 대상 날짜 (기본: 내일)
            base_safety_days: 기본 안전재고 일수

        Returns:
            FeatureResult 객체
        """
        if target_date is None:
            target_date = datetime.now() + timedelta(days=1)

        target_str = target_date.strftime("%Y-%m-%d")

        # 1. Lag Features 계산
        lag_features = self.lag_calculator.calculate(item_cd, target_date)

        # 2. Rolling Features 계산
        rolling_features = self.rolling_calculator.calculate(item_cd, target_date)

        # 3. 기본 예측값들 추출
        base_prediction = rolling_features.rolling_mean_7 or 0.0
        ewm_prediction = rolling_features.ewm_mean_7 or 0.0
        same_weekday_prediction = lag_features.same_weekday_avg or 0.0
        rolling_28_prediction = rolling_features.rolling_mean_28 or 0.0

        # 4. 데이터 품질 판단 (가중치 결정에 필요)
        data_quality = self._assess_data_quality(lag_features, rolling_features)

        # 5. 가중 결합 예측 계산 (적응형 가중치 + 28일 이동평균)
        weighted_prediction = self._calculate_weighted_prediction(
            base_prediction, ewm_prediction, same_weekday_prediction,
            data_quality, rolling_28_prediction
        )

        # 6. 변동성 기반 안전재고 조정
        adjusted_safety, volatility_level = self._adjust_safety_by_volatility(
            rolling_features, base_safety_days
        )

        # 7. 트렌드 기반 조정
        trend_adj, trend_dir = self._calculate_trend_adjustment(rolling_features)

        # 8. 계산 방법 설명 생성
        calculation_method = self._generate_calculation_method(
            lag_features, rolling_features, volatility_level, trend_dir
        )

        return FeatureResult(
            item_cd=item_cd,
            target_date=target_str,
            base_prediction=base_prediction,
            ewm_prediction=ewm_prediction,
            same_weekday_prediction=same_weekday_prediction,
            weighted_prediction=weighted_prediction,
            lag_features=lag_features,
            rolling_features=rolling_features,
            base_safety_days=base_safety_days,
            adjusted_safety_days=adjusted_safety,
            volatility_level=volatility_level,
            trend_adjustment=trend_adj,
            trend_direction=trend_dir,
            data_quality=data_quality,
            calculation_method=calculation_method,
        )

    def _calculate_weighted_prediction(
        self,
        simple_avg: float,
        ewm: float,
        same_weekday: float,
        data_quality: str = "medium",
        rolling_28: float = 0.0
    ) -> float:
        """
        가중 결합 예측 계산 (적응형 가중치 + 28일 이동평균)

        유효한 값에만 가중치를 적용하고, 없는 값은 제외.
        28일 이동평균을 장기 앵커로 포함하여 단기 변동(행사, 이벤트)에
        의한 예측 왜곡을 완화한다.

        Args:
            simple_avg: 단순 이동평균 (7일)
            ewm: 지수 가중 이동평균
            same_weekday: 동일 요일 평균
            data_quality: 데이터 품질 (high/medium/low)
            rolling_28: 28일 이동평균 (장기 앵커)

        Returns:
            가중 결합 예측값
        """
        # 데이터 품질에 따른 적응형 가중치 사용
        weights = self._get_adaptive_weights(data_quality)
        total_weight = 0.0
        weighted_sum = 0.0

        if simple_avg > 0:
            weighted_sum += simple_avg * weights["simple_avg"]
            total_weight += weights["simple_avg"]

        if ewm > 0:
            weighted_sum += ewm * weights["ewm"]
            total_weight += weights["ewm"]

        if same_weekday > 0:
            weighted_sum += same_weekday * weights["same_weekday"]
            total_weight += weights["same_weekday"]

        if rolling_28 > 0:
            weighted_sum += rolling_28 * weights["rolling_28"]
            total_weight += weights["rolling_28"]

        if total_weight > 0:
            return round(weighted_sum / total_weight, 2)
        return 0.0

    def _adjust_safety_by_volatility(
        self,
        rolling_features: RollingFeatures,
        base_safety_days: float
    ) -> Tuple[float, str]:
        """
        변동성 기반 안전재고 조정

        CV(변동계수)에 따라 안전재고 일수 조정
        """
        cv = rolling_features.coefficient_of_variation

        if cv is None or cv < 0.3:
            return base_safety_days, "stable"
        elif cv < 0.5:
            return round(base_safety_days * 1.1, 2), "normal"
        elif cv < 0.8:
            return round(base_safety_days * 1.2, 2), "volatile"
        else:
            return round(base_safety_days * 1.3, 2), "highly_volatile"

    def _calculate_trend_adjustment(
        self,
        rolling_features: RollingFeatures
    ) -> Tuple[float, str]:
        """
        트렌드 기반 조정 계수 계산

        상승 트렌드: 발주량 증가
        하락 트렌드: 발주량 감소
        """
        slope = rolling_features.trend_slope

        if slope is None:
            return 1.0, "stable"

        if slope >= 0.20:
            return self.TREND_ADJUSTMENT["strong_up"], "strong_up"
        elif slope >= 0.10:
            return self.TREND_ADJUSTMENT["up"], "up"
        elif slope <= -0.20:
            return self.TREND_ADJUSTMENT["strong_down"], "strong_down"
        elif slope <= -0.10:
            return self.TREND_ADJUSTMENT["down"], "down"
        else:
            return self.TREND_ADJUSTMENT["stable"], "stable"

    def _assess_data_quality(
        self,
        lag_features: LagFeatures,
        rolling_features: RollingFeatures
    ) -> str:
        """데이터 품질 종합 판단

        Lag Features 품질과 Rolling Features 데이터 일수를 종합하여
        high/medium/low 판단한다.

        Args:
            lag_features: Lag Feature 결과
            rolling_features: Rolling Feature 결과

        Returns:
            데이터 품질 레벨 (high, medium, low)
        """
        lag_quality = lag_features.data_quality
        data_days = rolling_features.data_days

        # 두 지표를 종합하여 판단
        if lag_quality == "high" and data_days >= 14:
            return "high"
        elif lag_quality in ["high", "medium"] and data_days >= 7:
            return "medium"
        else:
            return "low"

    def _generate_calculation_method(
        self,
        lag_features: LagFeatures,
        rolling_features: RollingFeatures,
        volatility_level: str,
        trend_dir: str
    ) -> str:
        """계산 방법 설명 생성

        Args:
            lag_features: Lag Feature 결과
            rolling_features: Rolling Feature 결과
            volatility_level: 변동성 레벨
            trend_dir: 트렌드 방향

        Returns:
            계산 방법 설명 문자열
        """
        parts = []

        # 사용된 데이터
        parts.append(f"데이터: {rolling_features.data_days}일")

        # EWM 사용 여부
        if rolling_features.ewm_mean_7:
            parts.append("EWM 적용")

        # 동일 요일 평균 사용 여부
        if lag_features.same_weekday_avg:
            parts.append("요일패턴")

        # 변동성 조정
        if volatility_level != "stable":
            parts.append(f"변동성:{volatility_level}")

        # 트렌드 조정
        if trend_dir != "stable":
            parts.append(f"트렌드:{trend_dir}")

        return ", ".join(parts) if parts else "기본"

    def calculate_enhanced_prediction(
        self,
        item_cd: str,
        target_date: Optional[datetime] = None,
        weekday_coef: float = 1.0,
        base_safety_days: float = 1.0
    ) -> Dict[str, Any]:
        """
        향상된 예측 결과 반환

        기존 predictor와 통합하기 위한 메서드

        Args:
            item_cd: 상품코드
            target_date: 예측 대상 날짜
            weekday_coef: 요일 계수 (카테고리별)
            base_safety_days: 기본 안전재고 일수

        Returns:
            {
                'prediction': 최종 예측값,
                'safety_days': 조정된 안전재고 일수,
                'trend_adjustment': 트렌드 조정 계수,
                'features': FeatureResult 전체,
            }
        """
        features = self.calculate(item_cd, target_date, base_safety_days)

        # 가중 예측값에 요일 계수 적용
        adjusted_prediction = features.weighted_prediction * weekday_coef

        # 트렌드 조정 적용
        final_prediction = adjusted_prediction * features.trend_adjustment

        return {
            'prediction': round(final_prediction, 2),
            'base_prediction': features.weighted_prediction,
            'safety_days': features.adjusted_safety_days,
            'trend_adjustment': features.trend_adjustment,
            'trend_direction': features.trend_direction,
            'volatility_level': features.volatility_level,
            'data_quality': features.data_quality,
            'calculation_method': features.calculation_method,
            'features': features,
        }

    def calculate_batch(
        self,
        item_codes: List[str],
        target_date: Optional[datetime] = None,
        base_safety_days: float = 1.0
    ) -> Dict[str, FeatureResult]:
        """
        여러 상품의 Features 일괄 계산

        Args:
            item_codes: 상품코드 리스트
            target_date: 예측 대상 날짜
            base_safety_days: 기본 안전재고 일수

        Returns:
            {item_cd: FeatureResult} 딕셔너리
        """
        result = {}
        for item_cd in item_codes:
            result[item_cd] = self.calculate(item_cd, target_date, base_safety_days)
        return result


# =============================================================================
# 테스트/데모
# =============================================================================
if __name__ == "__main__":
    from datetime import datetime

    calculator = FeatureCalculator()

    # 테스트: DB에서 임의 상품 조회
    conn = sqlite3.connect(calculator.db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT ds.item_cd, p.item_nm, p.mid_cd
        FROM daily_sales ds
        JOIN products p ON ds.item_cd = p.item_cd
        WHERE ds.sales_date >= date('now', '-7 days')
        AND ds.sale_qty > 0
        LIMIT 10
    """)
    items = cursor.fetchall()
    conn.close()

    print("\n[통합 Feature 계산 테스트]")
    print("=" * 100)

    target = datetime.now() + timedelta(days=1)
    for row in items:
        item_cd, item_nm, mid_cd = row

        # 기본 계산
        result = calculator.calculate(item_cd, target, base_safety_days=1.0)

        print(f"\n{item_nm[:20]:<20} ({item_cd})")
        print("-" * 60)
        print(f"  예측값:")
        print(f"    - 기본 평균: {result.base_prediction}")
        print(f"    - EWM 예측: {result.ewm_prediction}")
        print(f"    - 동일요일: {result.same_weekday_prediction}")
        print(f"    - 가중 결합: {result.weighted_prediction}")
        print(f"  안전재고:")
        print(f"    - 기본: {result.base_safety_days}일 → 조정: {result.adjusted_safety_days}일")
        print(f"    - 변동성: {result.volatility_level}")
        print(f"  트렌드:")
        print(f"    - 방향: {result.trend_direction}, 조정: {result.trend_adjustment}")
        print(f"  품질: {result.data_quality}")
        print(f"  방법: {result.calculation_method}")

        # 향상된 예측 테스트 (요일 계수 1.2 적용)
        enhanced = calculator.calculate_enhanced_prediction(
            item_cd, target, weekday_coef=1.2, base_safety_days=1.5
        )
        print(f"  향상된 예측: {enhanced['prediction']} (base={enhanced['base_prediction']}, trend={enhanced['trend_adjustment']})")
