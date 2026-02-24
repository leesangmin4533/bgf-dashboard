"""
BGF 자동발주 - 개선된 규칙 기반 예측기
- 31일 실제 데이터 분석 기반
- 카테고리별 요일 계수 적용
- 유통기한별 안전재고 차등 적용
- [v7] DB 기반 재고/미입고 조회 지원
- [v8] Feature Engineering (Lag/Rolling/EWM) 지원
- [v9] 행사 정보 기반 발주량 조정 지원
"""

import sqlite3
from datetime import date, datetime, timedelta
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from pathlib import Path

from src.utils.logger import get_logger
from src.infrastructure.database.repos import (
    ExternalFactorRepository,
)
from src.settings.constants import (
    TOBACCO_MAX_STOCK as DEFAULT_TOBACCO_MAX_STOCK,
    CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW,
    PREDICTION_DATA_DAYS,
    PROMO_MIN_STOCK_UNITS,
    MAX_ORDER_QTY_BY_CATEGORY,  # Priority 1.2
    FOOD_CATEGORIES,  # Priority 1.5
    DEFAULT_STORE_ID,
    FORCE_MAX_DAYS,
    DIFF_FEEDBACK_ENABLED,
    DIFF_FEEDBACK_ADDITION_MIN_QTY,
    SNACK_DEFAULT_ORDERABLE_DAYS,
    RAMEN_DEFAULT_ORDERABLE_DAYS,
)

logger = get_logger(__name__)

# Feature Engineering
from .features import FeatureCalculator, FeatureResult

# 행사 정보 관리
from .promotion import PromotionAdjuster, PromotionManager

# 설정 import (categories 모듈에서)
from .categories import (
    # 기본 설정
    get_shelf_life_group,
    get_weekday_coefficient,
    get_safety_stock_days,
    SHELF_LIFE_CONFIG,
    # 담배/전자담배
    is_tobacco_category,
    get_safety_stock_with_tobacco_pattern,
    TobaccoPatternResult,
    TOBACCO_DYNAMIC_SAFETY_CONFIG,
    # 라면
    is_ramen_category,
    get_safety_stock_with_ramen_pattern,
    RamenPatternResult,
    RAMEN_DYNAMIC_SAFETY_CONFIG,
    # 맥주
    is_beer_category,
    get_safety_stock_with_beer_pattern,
    get_beer_weekday_coef,
    BeerPatternResult,
    BEER_SAFETY_CONFIG,
    # 소주 (신규)
    is_soju_category,
    get_safety_stock_with_soju_pattern,
    get_soju_weekday_coef,
    SojuPatternResult,
    SOJU_SAFETY_CONFIG,
    # 푸드류
    is_food_category,
    get_safety_stock_with_food_pattern,
    get_food_disuse_coefficient,
    get_dynamic_disuse_coefficient,
    get_delivery_waste_adjustment,
    calculate_delivery_gap_consumption,
    get_food_weekday_coefficient,
    get_food_weather_cross_coefficient,
    FoodExpiryResult,
    FOOD_EXPIRY_SAFETY_CONFIG,
    FOOD_ANALYSIS_DAYS,
    # 소멸성 상품 (떡/과일/요구르트)
    is_perishable_category,
    get_safety_stock_with_perishable_pattern,
    PerishablePatternResult,
    # 음료류
    is_beverage_category,
    get_safety_stock_with_beverage_pattern,
    BeveragePatternResult,
    # 냉동/아이스크림
    is_frozen_ice_category,
    get_safety_stock_with_frozen_ice_pattern,
    FrozenIcePatternResult,
    # 즉석식품
    is_instant_meal_category,
    get_safety_stock_with_instant_meal_pattern,
    InstantMealPatternResult,
    # 디저트
    is_dessert_category,
    get_safety_stock_with_dessert_pattern,
    DessertPatternResult,
    # 과자/간식
    is_snack_confection_category,
    get_safety_stock_with_snack_confection_pattern,
    SnackConfectionPatternResult,
    # 일반주류 (양주/와인)
    is_alcohol_general_category,
    get_safety_stock_with_alcohol_general_pattern,
    AlcoholGeneralPatternResult,
    # 생활용품
    is_daily_necessity_category,
    get_safety_stock_with_daily_necessity_pattern,
    DailyNecessityPatternResult,
    # 잡화/비식품
    is_general_merchandise_category,
    get_safety_stock_with_general_merchandise_pattern,
    GeneralMerchandisePatternResult,
)

# 예측 파라미터 (prediction_config에서 유지)
from .prediction_config import (
    PREDICTION_PARAMS,
    ORDER_ADJUSTMENT_RULES,
    get_seasonal_coefficient,
)


@dataclass
class PredictionResult:
    """예측 결과"""
    item_cd: str
    item_nm: str
    mid_cd: str
    target_date: str

    # 예측값
    predicted_qty: float      # 예측 판매량
    adjusted_qty: float       # 조정된 예측량 (요일계수 적용)

    # 발주 계산
    current_stock: int        # 현재 재고
    pending_qty: int          # 미입고 수량
    safety_stock: float       # 안전재고 (동적 패턴 적용 후)
    order_qty: int            # 최종 발주량

    # 메타 정보
    confidence: str           # 신뢰도 (high/medium/low)
    data_days: int            # 사용된 데이터 일수
    weekday_coef: float       # 적용된 요일 계수

    # 담배 동적 안전재고 정보 (담배 카테고리만 유효)
    carton_buffer: int = 0          # 보루 버퍼 (개수)
    carton_frequency: float = 0.0   # 보루 판매 빈도 (30일 환산)
    sellout_multiplier: float = 1.0 # 전량 소진 승수
    sellout_frequency: float = 0.0  # 전량 소진 빈도 (30일 환산)
    tobacco_max_stock: int = DEFAULT_TOBACCO_MAX_STOCK     # 담배 최대 재고 상한선
    tobacco_available_space: int = 0  # 여유분 (30 - 현재고 - 미입고)
    tobacco_skip_order: bool = False  # 상한선으로 인한 발주 스킵
    tobacco_skip_reason: str = ""     # 스킵 사유

    # 라면 동적 안전재고 정보 (라면 카테고리만 유효)
    ramen_turnover_level: str = ""      # 회전율 레벨 (high/medium/low)
    ramen_safety_days: float = 0.0      # 적용된 안전재고 일수
    ramen_max_stock: float = 0.0        # 최대 재고 상한선
    ramen_skip_order: bool = False      # 상한선 초과로 발주 스킵

    # 맥주 요일 기반 동적 안전재고 정보 (맥주 카테고리만 유효)
    beer_weekday_coef: Optional[float] = None     # 요일 계수
    beer_safety_days: Optional[int] = None        # 안전재고 일수 (2 또는 3)
    beer_max_stock: Optional[float] = None        # 최대 재고 상한선
    beer_skip_order: bool = False       # 발주 스킵 여부
    beer_skip_reason: str = ""          # 스킵 사유

    # 소주 요일 기반 동적 안전재고 정보 (소주 카테고리만 유효)
    soju_weekday_coef: Optional[float] = None     # 요일 계수
    soju_safety_days: Optional[int] = None        # 안전재고 일수 (2 또는 3)
    soju_max_stock: Optional[float] = None        # 최대 재고 상한선
    soju_skip_order: bool = False       # 발주 스킵 여부
    soju_skip_reason: str = ""          # 스킵 사유

    # 푸드류 유통기한 기반 동적 안전재고 정보 (푸드류 카테고리만 유효)
    food_expiration_days: Optional[int] = None      # 유통기한 (일)
    food_expiry_group: str = ""           # 그룹 (ultra_short, short, medium, long, very_long)
    food_safety_days: float = 0.0         # 안전재고 일수
    food_data_source: str = ""            # 유통기한 소스 (db/fallback)
    food_disuse_coef: float = 1.0         # 폐기율 계수
    food_gap_consumption: float = 0.0     # 배송 갭 소비량

    # 간헐적 수요 보정 정보
    sell_day_ratio: float = 1.0           # 판매일 비율 (데이터일수 / 달력범위, 1.0=매일 판매)
    intermittent_adjusted: bool = False   # 간헐적 수요 보정 적용 여부

    # 연관 상품 부스트
    association_boost: float = 1.0        # 연관 상품 부스트 계수

    # ML 모델 유형
    model_type: str = "rule"              # rule / ensemble_30 / ensemble_50

    # 재고 소스 메타 (재고 불일치 진단용)
    stock_source: str = ""                # "cache"|"ri"|"ri_stale_ds"|"ri_stale_ri"|"ds"
    pending_source: str = ""              # "cache"|"ri"|"ri_stale_zero"|"ri_fresh"|"none"
    is_stock_stale: bool = False          # realtime_inventory TTL 초과 여부


class ImprovedPredictor:
    """개선된 규칙 기반 예측기"""

    def __init__(
        self,
        db_path: Optional[str] = None,
        use_db_inventory: bool = True,
        store_id: str = DEFAULT_STORE_ID
    ) -> None:
        """
        Args:
            db_path: 데이터베이스 경로
            use_db_inventory: True면 realtime_inventory 테이블에서 재고/미입고 조회
            store_id: 점포 코드
        """
        if db_path is None:
            from src.infrastructure.database.connection import DBRouter
            db_path = str(DBRouter.get_store_db_path(store_id)) if store_id else str(DBRouter.get_legacy_db_path())
        self.db_path = str(db_path)
        self.store_id = store_id  # Priority 2.3

        # Phase 6-2: DB/인프라 접근을 PredictionDataProvider로 위임
        from .data_provider import PredictionDataProvider
        self._data = PredictionDataProvider(
            db_path=self.db_path,
            store_id=self.store_id,
            use_db_inventory=use_db_inventory,
        )

        self._feature_calculator = FeatureCalculator(str(self.db_path), store_id=self.store_id)
        self._promo_adjuster = PromotionAdjuster(PromotionManager(str(self.db_path), store_id=self.store_id))
        self._use_promo_adjustment = True  # 행사 조정 활성화 여부

        # 비용 최적화 모듈 (비활성화 - SQL 버그 및 레거시 DB 경로 이슈)
        self._cost_optimizer = None

        # 연관 상품 조정기
        self._association_adjuster = None
        try:
            from src.prediction.association.association_adjuster import AssociationAdjuster
            assoc_params = PREDICTION_PARAMS.get("association", {})
            self._association_adjuster = AssociationAdjuster(
                store_id=self.store_id,
                db_path=str(self.db_path),
                params=assoc_params,
            )
        except Exception:
            pass  # 연관 분석 모듈 미설치 시 무시

        # 발주 차이 피드백 조정기
        self._diff_feedback = None
        if DIFF_FEEDBACK_ENABLED:
            try:
                from src.prediction.diff_feedback import DiffFeedbackAdjuster
                self._diff_feedback = DiffFeedbackAdjuster(store_id=self.store_id)
            except Exception:
                pass  # 피드백 모듈 미설치/DB 미존재 시 무시

        # 폐기 원인 피드백 조정기 (lazy-load)
        self._waste_feedback = None

        # ML 예측기 (Phase 2) — 매장별 모델 분리
        self._ml_predictor = None
        try:
            from src.prediction.ml.model import MLPredictor
            self._ml_predictor = MLPredictor(store_id=self.store_id)
        except Exception:
            pass  # ML 모듈 미설치 시 규칙 기반만 사용

        # 입고 패턴 통계 배치 캐시 (receiving-pattern)
        self._receiving_stats_cache: Dict[str, Dict[str, float]] = {}

    # =========================================================================
    # Phase 6-2: 데이터 접근 위임 메서드 + 캐시 프로퍼티
    # =========================================================================

    def _get_connection(self, timeout: int = 30) -> sqlite3.Connection:
        """DB 연결 (PredictionDataProvider로 위임)"""
        return self._data._get_connection(timeout)

    @property
    def _pending_cache(self) -> Dict[str, int]:
        return self._data._pending_cache

    @_pending_cache.setter
    def _pending_cache(self, value: Dict[str, int]) -> None:
        self._data._pending_cache = value

    @property
    def _stock_cache(self) -> Dict[str, int]:
        return self._data._stock_cache

    @_stock_cache.setter
    def _stock_cache(self, value: Dict[str, int]) -> None:
        self._data._stock_cache = value

    @property
    def _use_db_inventory(self) -> bool:
        return self._data._use_db_inventory

    @property
    def _inventory_repo(self):
        return self._data._inventory_repo

    def _get_waste_feedback(self):
        """WasteFeedbackAdjuster lazy 로드"""
        if self._waste_feedback is None:
            try:
                from src.analysis.waste_cause_analyzer import WasteFeedbackAdjuster
                self._waste_feedback = WasteFeedbackAdjuster(store_id=self.store_id)
            except Exception as e:
                logger.debug(f"WasteFeedbackAdjuster 초기화 실패: {e}")
                self._waste_feedback = False  # sentinel: don't retry
        return self._waste_feedback if self._waste_feedback is not False else None

    def _check_holiday(self, date_str: str) -> bool:
        """external_factors 테이블에서 휴일 여부 확인

        DB 조회 → calendar_collector fallback 순서로 확인

        Args:
            date_str: 날짜 문자열 (YYYY-MM-DD)

        Returns:
            휴일이면 True, 아니면 False
        """
        ctx = self._get_holiday_context(date_str)
        return ctx.get("is_holiday", False)

    def _get_holiday_context(self, date_str: str) -> Dict:
        """연휴 맥락 정보 조회 (DB 우선 → calendar_collector fallback)

        Returns:
            {
                "is_holiday": bool,
                "in_period": bool,
                "period_days": int,
                "position": int,
                "is_pre_holiday": bool,
                "is_post_holiday": bool,
            }
        """
        default = {
            "is_holiday": False,
            "in_period": False,
            "period_days": 0,
            "position": 0,
            "is_pre_holiday": False,
            "is_post_holiday": False,
        }
        try:
            # DB에서 calendar factor 조회
            repo = ExternalFactorRepository()
            factors = repo.get_factors(date_str, factor_type='calendar')
            if factors:
                factor_map = {f['factor_key']: f['factor_value'] for f in factors}
                is_hol_str = factor_map.get('is_holiday', 'false').lower()
                return {
                    "is_holiday": is_hol_str in ('true', '1', 'yes'),
                    "in_period": int(factor_map.get('holiday_period_days', '0') or '0') > 0
                                 and int(factor_map.get('holiday_position', '0') or '0') > 0,
                    "period_days": int(factor_map.get('holiday_period_days', '0') or '0'),
                    "position": int(factor_map.get('holiday_position', '0') or '0'),
                    "is_pre_holiday": factor_map.get('is_pre_holiday', 'false').lower() in ('true', '1'),
                    "is_post_holiday": factor_map.get('is_post_holiday', 'false').lower() in ('true', '1'),
                }
            # DB에 없으면 calendar_collector fallback
            from src.collectors.calendar_collector import get_holiday_context
            return get_holiday_context(date_str)
        except Exception as e:
            logger.debug(f"연휴 맥락 조회 실패 (날짜: {date_str}): {e}")
            try:
                from src.collectors.calendar_collector import get_holiday_context
                return get_holiday_context(date_str)
            except Exception:
                return default

    def _get_holiday_coefficient(self, date_str: str, mid_cd: str) -> float:
        """연휴 맥락 기반 차등 계수 계산

        연휴 길이, 연휴 내 위치, 카테고리에 따라 차등화된 계수를 반환한다.
        비연휴/비관련일이면 1.0을 반환.

        Args:
            date_str: 대상 날짜 (YYYY-MM-DD)
            mid_cd: 중분류 코드

        Returns:
            계수 (1.0 = 조정 없음)
        """
        holiday_cfg = PREDICTION_PARAMS.get("holiday", {})
        if not holiday_cfg.get("enabled", False):
            return 1.0

        ctx = self._get_holiday_context(date_str)

        # 연휴 기간 내도 아니고 전날/후일도 아니면 1.0
        if not ctx["in_period"] and not ctx["is_pre_holiday"] and not ctx["is_post_holiday"]:
            return 1.0

        # 1. 연휴 길이별 기본 계수
        period_days = ctx["period_days"]
        thresholds = holiday_cfg.get("period_thresholds", {"short": 2, "long": 5})
        period_coefs = holiday_cfg.get("period_coefficients", {"single": 1.2, "short": 1.3, "long": 1.4})

        if period_days >= thresholds.get("long", 5):
            base_coef = period_coefs.get("long", 1.4)
        elif period_days >= thresholds.get("short", 2):
            base_coef = period_coefs.get("short", 1.3)
        else:
            base_coef = period_coefs.get("single", 1.2)

        # 2. 위치별 보정
        pos_mods = holiday_cfg.get("position_modifiers", {})
        if ctx["is_pre_holiday"]:
            position_mod = pos_mods.get("pre_holiday", 1.15)
        elif ctx["is_post_holiday"]:
            position_mod = pos_mods.get("post_holiday", 0.90)
        elif ctx["in_period"]:
            pos = ctx["position"]
            total = ctx["period_days"]
            if pos == 1:
                position_mod = pos_mods.get("first_day", 1.35)
            elif pos == total:
                position_mod = pos_mods.get("last_day", 1.10)
            else:
                position_mod = pos_mods.get("middle", 1.25)
        else:
            position_mod = 1.0

        # 3. 카테고리 보정
        cat_mults = holiday_cfg.get("category_multipliers", {})
        from src.prediction.ml.feature_builder import get_category_group
        group = get_category_group(mid_cd)
        # group은 "food_group", "alcohol_group" 등 → 키 매핑
        group_key = group.replace("_group", "")
        cat_mult = cat_mults.get(group_key, 1.0)

        # 최종 계수 = 위치 보정 × 카테고리 보정
        # (기본 계수는 위치 보정에 이미 반영된 형태)
        # pre_holiday/post_holiday는 기본 계수 없이 위치 보정만 × 카테고리
        if ctx["is_pre_holiday"] or ctx["is_post_holiday"]:
            final = position_mod * cat_mult
        else:
            final = base_coef * position_mod * cat_mult

        # 합리적 범위 제한 (0.7 ~ 2.5)
        final = max(0.7, min(final, 2.5))

        return round(final, 3)

    # 기온 기반 카테고리별 수요 조정 계수
    # 카테고리 그룹: {기온 조건: 계수}
    WEATHER_COEFFICIENTS = {
        # 음료/아이스크림: 고온에 수요 증가
        "hot_boost": {
            "categories": ["010", "034", "039", "043", "045", "048", "021", "100"],
            "temp_threshold": 30,
            "coefficient": 1.15,  # 30도 이상 시 15% 증가
        },
        # 도시락/김밥/샌드위치: 고온에 수요 감소 (뜨거운 음식 기피)
        "hot_reduce": {
            "categories": ["001", "002", "003", "004", "005"],
            "temp_threshold": 30,
            "coefficient": 0.90,  # 30도 이상 시 10% 감소
        },
        # 즉석식품/라면: 저온에 수요 증가
        "cold_boost": {
            "categories": ["006", "027", "028", "031", "032", "033", "035"],
            "temp_threshold": 5,
            "coefficient": 1.10,  # 5도 이하 시 10% 증가
            "below": True,
        },
    }

    def _get_temperature_for_date(self, date_str: str) -> Optional[float]:
        """날짜별 기온 조회 (예보 우선, 실측 폴백)

        조회 우선순위:
        1. temperature_forecast (예보) — 미래 날짜에 가장 정확
        2. temperature (실측) — 오늘/과거 날짜에 정확
        3. None → 호출자에서 1.0 폴백

        Args:
            date_str: 대상 날짜 (YYYY-MM-DD)

        Returns:
            기온(float) 또는 None
        """
        try:
            repo = ExternalFactorRepository()
            factors = repo.get_factors(date_str, factor_type='weather')

            forecast_temp = None
            actual_temp = None

            for factor in factors:
                key = factor.get('factor_key')
                try:
                    val = float(factor.get('factor_value', ''))
                except (ValueError, TypeError):
                    continue

                if key == 'temperature_forecast':
                    forecast_temp = val
                elif key == 'temperature':
                    actual_temp = val

            # 예보 우선 (미래 날짜), 실측 폴백 (오늘/과거)
            return forecast_temp if forecast_temp is not None else actual_temp
        except Exception as e:
            logger.debug(f"기온 조회 실패 (날짜: {date_str}): {e}")
            return None

    def _get_temperature_delta(self, date_str: str) -> Optional[float]:
        """전일 대비 기온 변화량 계산

        target_date 기온 - (target_date - 1일) 기온.
        양 날짜 모두 기온 데이터가 있어야 계산 가능.

        Args:
            date_str: 대상 날짜 (YYYY-MM-DD)

        Returns:
            기온 변화량(float) 또는 None (데이터 부족)
        """
        try:
            target_temp = self._get_temperature_for_date(date_str)
            if target_temp is None:
                return None

            prev_date = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            prev_temp = self._get_temperature_for_date(prev_date)
            if prev_temp is None:
                return None

            return target_temp - prev_temp
        except Exception as e:
            logger.debug(f"기온 변화량 계산 실패 (날짜: {date_str}): {e}")
            return None

    # 기온 급변 보정 계수 (전일 대비 변화량 기반)
    # 양 날짜 모두 정확한 기온 데이터가 있을 때만 적용
    WEATHER_DELTA_COEFFICIENTS = {
        # 급격한 기온 하락 (10도 이상 하락) → 따뜻한 음식 수요 증가
        "sudden_cold": {
            "categories": ["006", "027", "028", "031", "032", "033", "035",
                           "001", "002", "003", "004", "005"],
            "delta_threshold": -10,
            "below": True,
            "coefficient": 1.10,  # 10% 증가
        },
        # 급격한 기온 하락 (10도 이상 하락) → 음료/아이스크림 수요 감소
        "sudden_cold_reduce": {
            "categories": ["010", "034", "039", "043", "045", "048", "021", "100"],
            "delta_threshold": -10,
            "below": True,
            "coefficient": 0.90,  # 10% 감소
        },
        # 급격한 기온 상승 (10도 이상 상승) → 음료/아이스크림 수요 증가
        "sudden_hot": {
            "categories": ["010", "034", "039", "043", "045", "048", "021", "100"],
            "delta_threshold": 10,
            "below": False,
            "coefficient": 1.10,  # 10% 증가
        },
        # 급격한 기온 상승 (10도 이상 상승) → 따뜻한 음식 수요 감소
        "sudden_hot_reduce": {
            "categories": ["001", "002", "003", "004", "005",
                           "006", "027", "028", "031", "032", "033", "035"],
            "delta_threshold": 10,
            "below": False,
            "coefficient": 0.90,  # 10% 감소
        },
    }

    def _get_weather_coefficient(self, date_str: str, mid_cd: str) -> float:
        """기온 기반 수요 조정 계수 반환

        1. 절대 기온 임계값 (30도↑, 5도↓) 체크
        2. 전일 대비 기온 급변 (10도↑↓) 체크 (양 날짜 데이터 필요)
        3. 두 계수를 곱하여 반환

        Args:
            date_str: 대상 날짜 (YYYY-MM-DD)
            mid_cd: 중분류 코드

        Returns:
            기온 계수 (1.0=조정 없음)
        """
        try:
            coef = 1.0

            temp = self._get_temperature_for_date(date_str)
            if temp is None:
                return 1.0

            # 1. 절대 기온 임계값 계수
            for rule_name, rule in self.WEATHER_COEFFICIENTS.items():
                if mid_cd in rule["categories"]:
                    if rule.get("below", False):
                        if temp <= rule["temp_threshold"]:
                            coef = rule["coefficient"]
                            break
                    else:
                        if temp >= rule["temp_threshold"]:
                            coef = rule["coefficient"]
                            break

            # 2. 기온 급변 계수 (전일 대비 변화량)
            delta = self._get_temperature_delta(date_str)
            if delta is not None:
                for rule_name, rule in self.WEATHER_DELTA_COEFFICIENTS.items():
                    if mid_cd in rule["categories"]:
                        if rule.get("below", False):
                            if delta <= rule["delta_threshold"]:
                                coef *= rule["coefficient"]
                                logger.debug(
                                    f"[기온급변] {rule_name}: delta={delta:+.1f}도 → {rule['coefficient']}x"
                                )
                                break
                        else:
                            if delta >= rule["delta_threshold"]:
                                coef *= rule["coefficient"]
                                logger.debug(
                                    f"[기온급변] {rule_name}: delta={delta:+.1f}도 → {rule['coefficient']}x"
                                )
                                break

            return coef
        except Exception as e:
            logger.debug(f"기온 계수 조회 실패 (날짜: {date_str}): {e}")
            return 1.0

    def get_sales_history(self, item_cd: str, days: int = 7) -> List[Tuple[str, int, Optional[int]]]:
        """상품의 최근 N일 판매 이력 조회 (PredictionDataProvider로 위임)"""
        return self._data.get_sales_history(item_cd, days)

    def _get_data_span_days(self, item_cd: str) -> int:
        """상품의 전체 데이터 기간 (PredictionDataProvider로 위임)"""
        return self._data._get_data_span_days(item_cd)

    def get_product_info(self, item_cd: str) -> Optional[Dict[str, object]]:
        """상품 정보 조회 (PredictionDataProvider로 위임)"""
        return self._data.get_product_info(item_cd)

    def get_current_stock(self, item_cd: str) -> int:
        """현재 재고 조회 (PredictionDataProvider로 위임)"""
        return self._data.get_current_stock(item_cd)

    def _get_disuse_rate(self, item_cd: str, days: int = FOOD_ANALYSIS_DAYS) -> float:
        """상품의 폐기율 조회 (PredictionDataProvider로 위임)"""
        return self._data._get_disuse_rate(item_cd, days)

    def set_pending_cache(self, pending_data: Dict[str, int]) -> None:
        """미입고 수량 캐시 설정 (PredictionDataProvider로 위임)"""
        self._data.set_pending_cache(pending_data)

    def clear_pending_cache(self) -> None:
        """미입고 수량 캐시 초기화 (PredictionDataProvider로 위임)"""
        self._data.clear_pending_cache()

    def set_stock_cache(self, stock_data: Dict[str, int]) -> None:
        """실시간 재고 캐시 설정 (PredictionDataProvider로 위임)"""
        self._data.set_stock_cache(stock_data)

    def clear_stock_cache(self) -> None:
        """실시간 재고 캐시 초기화 (PredictionDataProvider로 위임)"""
        self._data.clear_stock_cache()

    def get_inventory_from_db(self, item_cd: str) -> Optional[Dict[str, object]]:
        """DB에서 재고/미입고 정보 조회 (PredictionDataProvider로 위임)"""
        return self._data.get_inventory_from_db(item_cd)

    def get_unavailable_items_from_db(self) -> List[str]:
        """DB에서 미취급 상품 코드 조회 (PredictionDataProvider로 위임)"""
        return self._data.get_unavailable_items_from_db()

    def is_item_available(self, item_cd: str) -> bool:
        """상품 취급 가능 여부 (PredictionDataProvider로 위임)"""
        return self._data.is_item_available(item_cd)

    def load_inventory_to_cache(self) -> None:
        """DB에서 재고/미입고 데이터 캐시 로드 (PredictionDataProvider로 위임)"""
        self._data.load_inventory_to_cache()

    def _get_promo_dates(self, item_cd: str, dates: list) -> set:
        """날짜 목록 중 행사 기간에 해당하는 날짜 set 반환 (위임)"""
        return self._data.get_promo_dates_in_range(item_cd, dates)

    @staticmethod
    def _calculate_days_until_next_order(orderable_day: str) -> int:
        """다음 발주 가능일까지 일수 계산

        Args:
            orderable_day: 발주 가능 요일 문자열 (예: "월수금", "일월화수목금토")

        Returns:
            다음 발주 가능일까지 일수 (1~7). 파싱 실패 시 기본값 2
        """
        day_map = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
        available = {day_map[c] for c in (orderable_day or "") if c in day_map}
        if not available:
            return 2  # 기본값: 격일 발주 가정
        today_wd = datetime.now().weekday()
        for i in range(1, 8):
            if (today_wd + i) % 7 in available:
                return i
        return 2

    def _calculate_sell_day_ratio(self, item_cd: str, data_days: int) -> float:
        """
        판매일 비율 계산 (달력일수 대비 판매일 비율)

        가중이동평균은 판매 기록이 있는 날만 대상으로 계산하므로,
        간헐적 수요 상품의 일평균을 과대 추정할 수 있음.
        이 메서드는 실제 달력 범위 대비 판매일 비율을 반환.

        예: 18일 달력 범위 중 3일 판매 → 3/18 = 0.17

        Args:
            item_cd: 상품코드
            data_days: 판매 기록이 있는 일수

        Returns:
            판매일 비율 (0.0 ~ 1.0). 데이터 부족 시 1.0 반환
        """
        if data_days <= 0:
            return 1.0

        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            # 최신 날짜와 최고(가장 오래된) 날짜의 달력 범위 계산
            cursor.execute("""
                SELECT MIN(sales_date), MAX(sales_date)
                FROM daily_sales
                WHERE item_cd = ? AND store_id = ?
                AND sales_date >= date('now', '-' || ? || ' days')
                AND sale_qty > 0
            """, (item_cd, self.store_id, PREDICTION_PARAMS["moving_avg_days"]))

            row = cursor.fetchone()
            if not row or not row[0] or not row[1]:
                return 1.0

            min_date = datetime.strptime(row[0], "%Y-%m-%d")
            max_date = datetime.strptime(row[1], "%Y-%m-%d")
            calendar_span = (max_date - min_date).days + 1  # 양 끝 포함

            if calendar_span <= 0:
                return 1.0

            return min(data_days / calendar_span, 1.0)
        finally:
            conn.close()

    def _calculate_available_sell_ratio(self, item_cd: str) -> float:
        """
        가용일 중 판매일 비율 계산 (품절일 제외)

        품절일(stock_qty=0)을 제외하고, 재고가 있었던 날 중
        실제 판매가 발생한 날의 비율을 반환한다.
        기존 _calculate_sell_day_ratio()와 달리 품절과 비판매를 구분한다.

        예: 7일 중 4일 품절, 3일 재고 보유, 3일 모두 판매 → 3/3 = 1.0
        예: 7일 중 0일 품절, 3일 판매, 4일 미판매 → 3/7 = 0.43

        Args:
            item_cd: 상품코드

        Returns:
            가용일 중 판매일 비율 (0.0 ~ 1.0). 가용일=0이면 1.0 반환
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            days = PREDICTION_PARAMS["moving_avg_days"]
            cursor.execute("""
                SELECT
                    SUM(CASE WHEN stock_qty > 0 AND sale_qty > 0 THEN 1 ELSE 0 END) as sell_days,
                    SUM(CASE WHEN stock_qty > 0 THEN 1 ELSE 0 END) as available_days
                FROM daily_sales
                WHERE item_cd = ? AND store_id = ?
                AND sales_date >= date('now', '-' || ? || ' days')
            """, (item_cd, self.store_id, days))

            row = cursor.fetchone()
            if not row:
                return 1.0

            sell_days = row[0] or 0
            available_days = row[1] or 0

            if available_days == 0:
                return 1.0

            return min(sell_days / available_days, 1.0)
        finally:
            conn.close()

    def calculate_weighted_average(
        self,
        sales_history: List[Tuple],
        clean_outliers: bool = True,
        mid_cd: Optional[str] = None,
        item_cd: Optional[str] = None
    ) -> Tuple[float, int]:
        """
        가중 이동평균 계산 (이상치 처리 + 품절일 imputation + 행사일 감쇄)

        품절일(stock_qty=0)은 수요 부재가 아니라 공급 부재이므로
        품절일의 판매량을 비품절일 평균으로 대체(imputation)한다.
        기존 "품절일 제외" 방식은 간헐적 판매 상품을 과대추정하는 문제가 있어
        전체 달력일 기준 WMA + 품절일 imputation 방식으로 개선.

        행사 기간 데이터는 연휴 보정과 동일하게 가중치를 감소시켜
        행사 종료 직후 예측 뻥튀기를 방지한다.

        Args:
            sales_history: [(날짜, 판매량, 재고량), ...] 리스트
                           (2-tuple도 하위 호환으로 허용)
            clean_outliers: 이상치 처리 여부 (기본 True)
            mid_cd: 중분류 코드 (카테고리별 이상치 설정용)
            item_cd: 상품코드 (행사 기간 필터링용, None이면 행사 보정 생략)

        Returns: (평균값, 사용된 데이터 일수)
        """
        if not sales_history:
            return 0.0, 0

        original_count = len(sales_history)

        # 품절일 imputation (3-tuple이고 설정 활성화 시)
        # - stock_qty > 0  : 정상 판매일 (원본 유지)
        # - stock_qty == 0 : 확인된 품절일 → 비품절일 평균으로 대체 (공급 부재)
        # - stock_qty is None : 레코드 없는 날 → sale_qty=0 유지 (수요 없음/미수집)
        stockout_cfg = PREDICTION_PARAMS.get("stockout_filter", {})
        has_stock_info = len(sales_history[0]) >= 3
        if stockout_cfg.get("enabled", False) and has_stock_info:
            available = [(d, qty, stk) for d, qty, stk in sales_history
                         if stk is not None and stk > 0]
            stockout = [(d, qty, stk) for d, qty, stk in sales_history
                        if stk is not None and stk == 0]
            # stk is None인 날(레코드 없음)은 imputation 대상에서 제외

            if available and stockout:
                # 비품절일의 평균 판매량 계산
                avg_available_sales = sum(row[1] for row in available) / len(available)

                # 확인된 품절일(stock_qty==0)만 비품절일 평균으로 대체
                imputed_history = []
                for row in sales_history:
                    if row[2] is not None and row[2] == 0:  # 확인된 품절일
                        imputed_history.append((row[0], avg_available_sales, row[2]))
                    else:
                        imputed_history.append(row)
                sales_history = imputed_history

        # 판매량 + 날짜 추출 (2-tuple, 3-tuple 모두 호환)
        sales = [row[1] for row in sales_history]
        dates = [row[0] for row in sales_history]

        # 이상치 처리 (최소 5일 이상일 때만)
        if clean_outliers and len(sales) >= 5:
            try:
                from .utils.outlier_handler import clean_sales_data
                result = clean_sales_data(sales, mid_cd=mid_cd, handle_zeros=True)
                sales = result.cleaned_data
            except ImportError:
                pass  # 모듈 없으면 원본 사용

        # 연휴일 WMA 가중치 감소 (연휴 매출 데이터의 예측 왜곡 방지)
        holiday_wma_cfg = PREDICTION_PARAMS.get("holiday_wma_correction", {})
        holiday_weight_factor = 1.0
        holiday_dates_set = set()
        if holiday_wma_cfg.get("enabled", False):
            holiday_weight_factor = holiday_wma_cfg.get("holiday_weight_factor", 0.3)
            for d in dates:
                try:
                    ctx = self._get_holiday_context(d)
                    if ctx.get("in_period", False):
                        holiday_dates_set.add(d)
                except Exception:
                    pass

        # 행사일 WMA 가중치 감소 (행사 매출 데이터의 예측 왜곡 방지)
        promo_wma_cfg = PREDICTION_PARAMS.get("promo_wma_correction", {})
        promo_weight_factor = promo_wma_cfg.get("promo_weight_factor", 0.25)
        promo_dates_set = set()
        if promo_wma_cfg.get("enabled", True) and item_cd:
            try:
                promo_dates_set = self._get_promo_dates(item_cd, dates)
            except Exception:
                pass  # promotions 테이블 미존재 등

        weights = PREDICTION_PARAMS["weights"]
        total_weight = 0.0
        weighted_sum = 0.0

        for i, qty in enumerate(sales):
            if i == 0:
                w = weights["day_1"]
            elif i == 1:
                w = weights["day_2"]
            elif i == 2:
                w = weights["day_3"]
            else:
                w = weights["day_4_7"] / max(len(sales) - 3, 1)

            # 연휴일이면 가중치 감소
            if i < len(dates) and dates[i] in holiday_dates_set:
                w *= holiday_weight_factor

            # 행사일이면 가중치 감소 (행사 종료 후 예측 뻥튀기 방지)
            if i < len(dates) and dates[i] in promo_dates_set:
                w *= promo_weight_factor

            weighted_sum += qty * w
            total_weight += w

        if total_weight > 0:
            return weighted_sum / total_weight, original_count  # 원본 데이터 일수 반환
        return 0.0, 0

    def predict(
        self,
        item_cd: str,
        target_date: Optional[datetime] = None,
        pending_qty: Optional[int] = None
    ) -> Optional[PredictionResult]:
        """
        상품 판매량 예측 및 발주량 계산

        Args:
            item_cd: 상품코드
            target_date: 예측 대상 날짜 (기본: 내일)
            pending_qty: 미입고 수량 (None이면 캐시에서 조회)

        Returns:
            PredictionResult 또는 None (상품 정보 없음)
        """
        # 1. 상품 정보 및 기본 데이터 로드
        product = self.get_product_info(item_cd)
        if not product:
            return None

        if target_date is None:
            target_date = datetime.now() + timedelta(days=1)

        weekday = target_date.weekday()  # 0=월, 6=일
        sqlite_weekday = (weekday + 1) % 7
        mid_cd = product["mid_cd"]
        target_date_str = target_date.strftime("%Y-%m-%d")

        # 2. 기본 예측 계산 (WMA + Feature 블렌딩 + 간헐적 수요 보정)
        base_prediction, data_days, _wma_days, feat_result, sell_day_ratio, intermittent_adjusted = (
            self._compute_base_prediction(item_cd, product, target_date)
        )

        # 3. 계수 적용 (연휴, 기온, 요일, 계절, 연관, 트렌드)
        base_prediction, adjusted_prediction, weekday_coef, assoc_boost = (
            self._apply_all_coefficients(
                base_prediction, item_cd, product, target_date,
                sqlite_weekday, feat_result
            )
        )

        # 4. 현재 재고 및 미입고 조회 (소스 메타 포함)
        current_stock, pending_qty, _stock_src, _pending_src, _is_stale = self._resolve_stock_and_pending(
            item_cd, pending_qty
        )

        # 5. 카테고리별 안전재고 계산 + 발주량 산출
        result_ctx = self._compute_safety_and_order(
            item_cd, product, target_date, weekday, mid_cd,
            base_prediction, adjusted_prediction, weekday_coef,
            current_stock, pending_qty, data_days,
            sell_day_ratio, feat_result
        )

        # 6. 최종 요약 로그 + PredictionResult 생성
        final_order_qty = int(max(0, result_ctx["order_qty"]))
        logger.info(
            f"[PRED][SUMMARY] {product['item_nm']}({item_cd}) mid={mid_cd} "
            f"| WMA={round(base_prediction, 2)} adj={round(result_ctx['adjusted_prediction'], 2)} "
            f"| wd={round(result_ctx.get('weekday_coef', weekday_coef), 2)} "
            f"| stk={current_stock} pnd={pending_qty} "
            f"| safe={round(result_ctx['safety_stock'], 2)} "
            f"| order={final_order_qty} "
            f"| model={result_ctx.get('model_type', 'rule')}"
        )
        return PredictionResult(
            item_cd=item_cd,
            item_nm=product["item_nm"],
            mid_cd=mid_cd,
            target_date=target_date_str,
            predicted_qty=round(base_prediction, 2),
            adjusted_qty=round(result_ctx["adjusted_prediction"], 2),
            current_stock=current_stock,
            pending_qty=pending_qty,
            safety_stock=round(result_ctx["safety_stock"], 2),
            order_qty=int(max(0, result_ctx["order_qty"])),
            confidence="high" if _wma_days >= 7 else ("medium" if _wma_days >= 3 else "low"),
            data_days=data_days,
            weekday_coef=round(result_ctx.get("weekday_coef", weekday_coef), 2),
            # 담배 패턴
            carton_buffer=result_ctx.get("carton_buffer", 0),
            carton_frequency=result_ctx.get("carton_frequency", 0.0),
            sellout_multiplier=result_ctx.get("sellout_multiplier", 1.0),
            sellout_frequency=result_ctx.get("sellout_frequency", 0.0),
            tobacco_max_stock=result_ctx.get("tobacco_max_stock", DEFAULT_TOBACCO_MAX_STOCK),
            tobacco_available_space=result_ctx.get("tobacco_available_space", 0),
            tobacco_skip_order=result_ctx.get("tobacco_skip_order", False),
            tobacco_skip_reason=result_ctx.get("tobacco_skip_reason", ""),
            # 라면 패턴
            ramen_turnover_level=result_ctx.get("ramen_turnover_level", ""),
            ramen_safety_days=result_ctx.get("ramen_safety_days", 0.0),
            ramen_max_stock=result_ctx.get("ramen_max_stock", 0.0),
            ramen_skip_order=result_ctx.get("ramen_skip_order", False),
            # 맥주 패턴
            beer_weekday_coef=result_ctx.get("beer_weekday_coef"),
            beer_safety_days=result_ctx.get("beer_safety_days"),
            beer_max_stock=result_ctx.get("beer_max_stock"),
            beer_skip_order=result_ctx.get("beer_skip_order", False),
            beer_skip_reason=result_ctx.get("beer_skip_reason", ""),
            # 소주 패턴
            soju_weekday_coef=result_ctx.get("soju_weekday_coef"),
            soju_safety_days=result_ctx.get("soju_safety_days"),
            soju_max_stock=result_ctx.get("soju_max_stock"),
            soju_skip_order=result_ctx.get("soju_skip_order", False),
            soju_skip_reason=result_ctx.get("soju_skip_reason", ""),
            # 푸드류 패턴
            food_expiration_days=result_ctx.get("food_expiration_days"),
            food_expiry_group=result_ctx.get("food_expiry_group", ""),
            food_safety_days=result_ctx.get("food_safety_days", 0.0),
            food_data_source=result_ctx.get("food_data_source", ""),
            food_disuse_coef=result_ctx.get("food_disuse_coef", 1.0),
            food_gap_consumption=result_ctx.get("food_gap_consumption", 0.0),
            # 간헐적 수요 보정
            sell_day_ratio=round(sell_day_ratio, 4),
            intermittent_adjusted=intermittent_adjusted,
            # 연관 상품 부스트
            association_boost=assoc_boost,
            # ML 모델 유형
            model_type=result_ctx.get("model_type", "rule"),
            # 재고 소스 메타 (재고 불일치 진단용)
            stock_source=_stock_src,
            pending_source=_pending_src,
            is_stock_stale=_is_stale,
        )

    # =========================================================================
    # Phase 6-3: predict() 서브루틴
    # =========================================================================

    def _compute_base_prediction(self, item_cd, product, target_date):
        """기본 예측 계산: WMA + Feature 블렌딩 + 간헐적 수요 보정

        Returns:
            (base_prediction, data_days, _wma_days, feat_result,
             sell_day_ratio, intermittent_adjusted)
        """
        # 판매 이력 조회 + WMA (행사 기간 가중치 감쇄 포함)
        history = self.get_sales_history(item_cd, PREDICTION_PARAMS["moving_avg_days"])
        wma_prediction, _wma_days = self.calculate_weighted_average(
            history, clean_outliers=True, mid_cd=product["mid_cd"],
            item_cd=item_cd
        )
        data_days = self._get_data_span_days(item_cd)
        logger.info(
            f"[PRED][1-WMA] {product['item_nm']}({item_cd}): "
            f"WMA={wma_prediction:.2f} (days={_wma_days})"
        )

        # Feature 기반 예측 블렌딩 (EWM + 동요일 평균 + 트렌드)
        base_prediction = wma_prediction
        feat_result = None
        try:
            feat_result = self._feature_calculator.calculate(item_cd, target_date)
            if feat_result.rolling_features and feat_result.rolling_features.data_days >= 7:
                wp = feat_result.weighted_prediction
                if wp > 0 and wma_prediction > 0:
                    if feat_result.data_quality == "high":
                        feat_weight = 0.40
                    elif feat_result.data_quality == "medium":
                        feat_weight = 0.25
                    else:
                        feat_weight = 0.10
                    wma_weight = 1.0 - feat_weight
                    base_prediction = round(
                        wma_prediction * wma_weight + wp * feat_weight, 2
                    )
                    if abs(base_prediction - wma_prediction) > 0.1:
                        logger.info(
                            f"[PRED][1-Feat] {product.get('item_nm', item_cd)}: "
                            f"WMA={wma_prediction:.2f} + Feat={wp:.2f} "
                            f"(w={feat_weight:.0%}) -> {base_prediction:.2f}"
                        )
        except Exception as e:
            logger.debug(f"[기간대비] Feature 계산 실패 ({item_cd}): {e}")

        # 간헐적 수요 보정 (가용일 기준)
        intermittent_config = PREDICTION_PARAMS.get("intermittent_demand", {})
        sell_day_ratio = 1.0
        intermittent_adjusted = False

        if intermittent_config.get("enabled", False) and data_days > 0:
            sell_day_ratio = self._calculate_available_sell_ratio(item_cd)
            intermittent_threshold = intermittent_config.get("threshold", 0.6)
            very_intermittent_threshold = intermittent_config.get("very_intermittent_threshold", 0.3)
            min_prediction_floor = intermittent_config.get("min_prediction_floor", 0.2)

            if sell_day_ratio < very_intermittent_threshold:
                original_pred = base_prediction
                base_prediction = max(base_prediction * 0.5, min_prediction_floor)
                intermittent_adjusted = True
                logger.info(
                    f"[간헐적v2-매우] {product['item_nm']}: "
                    f"ratio={sell_day_ratio:.2%}, "
                    f"{original_pred:.2f}→{base_prediction:.2f} "
                    f"(min_floor={min_prediction_floor})"
                )
            elif sell_day_ratio < intermittent_threshold:
                base_prediction *= sell_day_ratio
                intermittent_adjusted = True
                logger.info(
                    f"[간헐적v2-일반] {product['item_nm']}: "
                    f"ratio={sell_day_ratio:.2%}, pred={base_prediction:.2f}"
                )

        return base_prediction, data_days, _wma_days, feat_result, sell_day_ratio, intermittent_adjusted

    def _apply_all_coefficients(self, base_prediction, item_cd, product, target_date,
                                sqlite_weekday, feat_result):
        """연휴/기온/요일/계절/연관/트렌드 계수 일괄 적용

        Returns:
            (base_prediction, adjusted_prediction, weekday_coef, assoc_boost)
        """
        target_date_str = target_date.strftime("%Y-%m-%d")
        _temp = self._get_temperature_for_date(target_date_str)

        # 연휴 차등 계수
        holiday_coef = self._get_holiday_coefficient(target_date_str, product["mid_cd"])
        if holiday_coef != 1.0:
            base_prediction *= holiday_coef
            logger.info(f"[PRED][2-Holiday] {product.get('item_nm', item_cd)}: {holiday_coef}x (date={target_date_str})")

        # 기온 계수
        weather_coef = self._get_weather_coefficient(target_date_str, product["mid_cd"])
        if weather_coef != 1.0:
            base_prediction *= weather_coef
            logger.info(
                f"[PRED][2-Weather] {product.get('item_nm', item_cd)}: "
                f"{weather_coef:.2f}x (temp={_temp})"
            )

        # 기온×푸드 교차 효과 (카테고리별 기온 민감도)
        if is_food_category(product["mid_cd"]):
            food_wx_coef = get_food_weather_cross_coefficient(product["mid_cd"], _temp)
            if food_wx_coef != 1.0:
                base_prediction *= food_wx_coef
                logger.info(
                    f"[PRED][2-FoodWx] {product.get('item_nm', item_cd)}: "
                    f"temp={_temp}, {food_wx_coef:.2f}x"
                )

        # 요일 계수 (푸드: DB 기반 동적 계수 우선)
        weekday_coef = get_weekday_coefficient(product["mid_cd"], sqlite_weekday)
        weekday_source = "static"
        if is_food_category(product["mid_cd"]):
            food_wd_coef = get_food_weekday_coefficient(
                product["mid_cd"], sqlite_weekday,
                store_id=self.store_id, db_path=self.db_path,
            )
            if food_wd_coef != 1.0:
                weekday_coef = food_wd_coef
                weekday_source = "food-DB"
        adjusted_prediction = base_prediction * weekday_coef
        if weekday_coef != 1.0:
            logger.info(
                f"[PRED][2-Weekday] {product.get('item_nm', item_cd)}: "
                f"{weekday_coef:.2f}x (day={sqlite_weekday}, src={weekday_source})"
            )

        # 계절 계수
        seasonal_coef = get_seasonal_coefficient(product["mid_cd"], target_date.month)
        if seasonal_coef != 1.0:
            before_seasonal = adjusted_prediction
            adjusted_prediction *= seasonal_coef
            logger.info(
                f"[PRED][2-Season] {product.get('item_nm', item_cd)}: "
                f"{before_seasonal:.2f} x {seasonal_coef} "
                f"(month={target_date.month}) -> {adjusted_prediction:.2f}"
            )

        # 연관 상품 부스트
        assoc_boost = 1.0
        if self._association_adjuster:
            try:
                assoc_boost = self._association_adjuster.get_association_boost(
                    item_cd, product["mid_cd"]
                )
                if assoc_boost > 1.0:
                    before_assoc = adjusted_prediction
                    adjusted_prediction *= assoc_boost
                    logger.info(
                        f"[연관분석] {product.get('item_nm', item_cd)}: "
                        f"{before_assoc:.2f} × {assoc_boost:.3f} → {adjusted_prediction:.2f}"
                    )
            except Exception as e:
                logger.debug(f"[연관분석] 부스트 실패 ({item_cd}): {e}")

        # 트렌드 조정
        _trend_adjustment = 1.0
        _trend_direction = "stable"
        if feat_result:
            try:
                _trend_adjustment = feat_result.trend_adjustment
                _trend_direction = feat_result.trend_direction
            except Exception:
                pass
        if _trend_adjustment != 1.0:
            before_trend = adjusted_prediction
            adjusted_prediction *= _trend_adjustment
            logger.info(
                f"[PRED][2-Trend] {product.get('item_nm', item_cd)}: "
                f"{before_trend:.2f} x {_trend_adjustment} "
                f"({_trend_direction}) -> {adjusted_prediction:.2f}"
            )

        # 복합 계수 바닥값: 7개 계수 곱이 극단적으로 낮아지는 것 방지
        compound_floor = base_prediction * 0.15
        if adjusted_prediction < compound_floor:
            logger.warning(
                f"[PRED][2-Floor] {product.get('item_nm', item_cd)}: "
                f"{adjusted_prediction:.2f} < floor {compound_floor:.2f}, "
                f"clamped to {compound_floor:.2f}"
            )
            adjusted_prediction = compound_floor

        return base_prediction, adjusted_prediction, weekday_coef, assoc_boost

    def _resolve_stock_and_pending(self, item_cd, pending_qty):
        """현재 재고 및 미입고 수량 조회 (캐시/DB 우선순위)

        재고 조회 우선순위:
        1. _stock_cache (외부에서 직접 설정한 캐시)
        2. realtime_inventory (BGF 사이트 실시간 조회) — 유통기한 기반 TTL 이내만
        3. daily_sales 최근 stock_qty (폴백)

        ※ realtime_inventory의 queried_at이 유통기한 기반 TTL 초과 시
           오래된 '유령 재고'로 판단하여 daily_sales 폴백 사용
           (1일 유통기한: 18h, 2일: 36h, 3일+: 54h/36h)

        Returns:
            (current_stock, pending_qty, stock_source, pending_source, is_stale)
        """
        inv_data = None
        stock_source = ""
        pending_source = ""
        is_stale = False

        if self._use_db_inventory and self._inventory_repo:
            if item_cd not in self._stock_cache or (pending_qty is None and item_cd not in self._pending_cache):
                inv_data = self._inventory_repo.get(item_cd)

        if item_cd in self._stock_cache:
            current_stock = self._stock_cache[item_cd]
            stock_source = "cache"
        elif inv_data and inv_data.get('stock_qty') is not None:
            # 유효기간 검증: 오래된 데이터면 daily_sales 폴백
            if inv_data.get('_stale', False):
                is_stale = True
                ds_stock = self.get_current_stock(item_cd)
                ri_stock = inv_data['stock_qty']
                # 오래된 ri 값보다 daily_sales가 더 작으면(=더 현실적) ds 채택
                if ds_stock < ri_stock:
                    logger.debug(
                        f"[{item_cd}] 오래된 재고 데이터 감지 "
                        f"(ri={ri_stock}, ds={ds_stock}, queried={inv_data.get('queried_at', '?')}) "
                        f"-> daily_sales 값 사용"
                    )
                    current_stock = ds_stock
                    stock_source = "ri_stale_ds"
                else:
                    current_stock = ri_stock
                    stock_source = "ri_stale_ri"
            else:
                current_stock = inv_data['stock_qty']
                stock_source = "ri"
        else:
            current_stock = self.get_current_stock(item_cd)
            stock_source = "ds"

        # 음수 재고 방어
        if current_stock < 0:
            logger.warning(f"[{item_cd}] 음수 재고 감지: {current_stock}개 -> 0으로 초기화")
            current_stock = 0

        if pending_qty is None:
            if item_cd in self._pending_cache:
                pending_qty = self._pending_cache[item_cd]
                pending_source = "cache"
            elif inv_data:
                if inv_data.get('_stale', False):
                    ri_pending = inv_data.get('pending_qty', 0)
                    if ri_pending > 0:
                        logger.debug(
                            f"[{item_cd}] 오래된 미입고 데이터 무시 "
                            f"(pending={ri_pending}, queried={inv_data.get('queried_at', '?')})"
                        )
                    pending_qty = 0
                    pending_source = "ri_stale_zero"
                else:
                    pending_qty = inv_data.get('pending_qty', 0)
                    pending_source = "ri_fresh"
            else:
                pending_qty = 0
                pending_source = "none"
        else:
            pending_source = "cache" if item_cd in self._pending_cache else "param"

        # 음수 미입고 방어
        if pending_qty < 0:
            logger.warning(f"[{item_cd}] 음수 미입고 감지: {pending_qty}개 -> 0으로 초기화")
            pending_qty = 0

        return current_stock, pending_qty, stock_source, pending_source, is_stale

    def _compute_safety_and_order(
        self, item_cd, product, target_date, weekday, mid_cd,
        base_prediction, adjusted_prediction, weekday_coef,
        current_stock, pending_qty, data_days,
        sell_day_ratio, feat_result
    ):
        """카테고리별 안전재고 계산 + 발주량 산출 + ML 앙상블 + 후처리

        Returns:
            dict: 안전재고, 발주량, 카테고리 패턴 정보 등을 포함하는 컨텍스트
        """
        daily_avg = adjusted_prediction
        intermittent_config = PREDICTION_PARAMS.get("intermittent_demand", {})

        # 카테고리 패턴 정보 초기화
        ctx = {
            "adjusted_prediction": adjusted_prediction,
            "weekday_coef": weekday_coef,
            "carton_buffer": 0, "carton_frequency": 0.0,
            "sellout_multiplier": 1.0, "sellout_frequency": 0.0,
            "tobacco_max_stock": DEFAULT_TOBACCO_MAX_STOCK, "tobacco_available_space": 0,
            "tobacco_skip_order": False, "tobacco_skip_reason": "",
            "ramen_turnover_level": "", "ramen_safety_days": 0.0,
            "ramen_max_stock": 0.0, "ramen_skip_order": False,
            "food_expiration_days": None, "food_expiry_group": "",
            "food_safety_days": 0.0, "food_data_source": "",
            "food_disuse_coef": 1.0, "food_gap_consumption": 0.0,
            "beer_weekday_coef": None, "beer_safety_days": None,
            "beer_max_stock": None, "beer_skip_order": False, "beer_skip_reason": "",
            "soju_weekday_coef": None, "soju_safety_days": None,
            "soju_max_stock": None, "soju_skip_order": False, "soju_skip_reason": "",
            "model_type": "rule",
        }
        new_cat_skip_order = False
        new_cat_pattern = None
        is_default_category = False
        food_skip_order = False

        # 카테고리별 안전재고 계산
        if is_ramen_category(mid_cd):
            ramen_orderable_day = product.get("orderable_day") or RAMEN_DEFAULT_ORDERABLE_DAYS
            safety_stock, ramen_pattern = get_safety_stock_with_ramen_pattern(
                mid_cd, daily_avg, product["expiration_days"], item_cd,
                store_id=self.store_id,
                orderable_day=ramen_orderable_day,
                current_stock=current_stock,
                pending_qty=pending_qty,
            )
            if ramen_pattern:
                ctx["ramen_turnover_level"] = ramen_pattern.turnover_level
                ctx["ramen_safety_days"] = ramen_pattern.safety_days
                ctx["ramen_max_stock"] = ramen_pattern.max_stock
                ctx["ramen_skip_order"] = ramen_pattern.skip_order
                if ramen_pattern.skip_order and not ramen_pattern.is_orderable_today:
                    logger.info(
                        f"[라면 비발주일] {product['item_nm']} ({item_cd}): "
                        f"orderable_day={ramen_orderable_day} -> 오늘 발주 스킵"
                    )

        elif is_tobacco_category(mid_cd):
            safety_stock, tobacco_pattern = get_safety_stock_with_tobacco_pattern(
                mid_cd, daily_avg, product["expiration_days"], item_cd,
                current_stock, pending_qty, store_id=self.store_id
            )
            if tobacco_pattern:
                ctx["carton_buffer"] = tobacco_pattern.carton_buffer
                ctx["carton_frequency"] = tobacco_pattern.carton_count_scaled
                ctx["sellout_multiplier"] = tobacco_pattern.sellout_multiplier
                ctx["sellout_frequency"] = tobacco_pattern.sellout_count_scaled
                ctx["tobacco_max_stock"] = tobacco_pattern.max_stock
                ctx["tobacco_available_space"] = tobacco_pattern.available_space
                ctx["tobacco_skip_order"] = tobacco_pattern.skip_order
                ctx["tobacco_skip_reason"] = tobacco_pattern.skip_reason

        elif is_beer_category(mid_cd):
            safety_stock, beer_pattern = get_safety_stock_with_beer_pattern(
                mid_cd, daily_avg, product["expiration_days"], item_cd,
                current_stock, pending_qty, store_id=self.store_id
            )
            if beer_pattern:
                ctx["beer_weekday_coef"] = beer_pattern.weekday_coef
                ctx["beer_safety_days"] = beer_pattern.safety_days
                ctx["beer_max_stock"] = beer_pattern.max_stock
                ctx["beer_skip_order"] = beer_pattern.skip_order
                ctx["beer_skip_reason"] = beer_pattern.skip_reason
                weekday_coef = beer_pattern.weekday_coef
                adjusted_prediction = base_prediction * weekday_coef
                daily_avg = adjusted_prediction
                ctx["adjusted_prediction"] = adjusted_prediction
                ctx["weekday_coef"] = weekday_coef

        elif is_soju_category(mid_cd):
            safety_stock, soju_pattern = get_safety_stock_with_soju_pattern(
                mid_cd, daily_avg, product["expiration_days"], item_cd,
                current_stock, pending_qty, store_id=self.store_id
            )
            if soju_pattern:
                ctx["soju_weekday_coef"] = soju_pattern.weekday_coef
                ctx["soju_safety_days"] = soju_pattern.safety_days
                ctx["soju_max_stock"] = soju_pattern.max_stock
                ctx["soju_skip_order"] = soju_pattern.skip_order
                ctx["soju_skip_reason"] = soju_pattern.skip_reason
                weekday_coef = soju_pattern.weekday_coef
                adjusted_prediction = base_prediction * weekday_coef
                daily_avg = adjusted_prediction
                ctx["adjusted_prediction"] = adjusted_prediction
                ctx["weekday_coef"] = weekday_coef

        elif is_food_category(mid_cd):
            food_disuse_coef = get_dynamic_disuse_coefficient(
                item_cd, mid_cd, db_path=self.db_path, store_id=self.store_id
            )
            disuse_rate = self._get_disuse_rate(item_cd)

            if self._cost_optimizer and self._cost_optimizer.enabled:
                cost_info = self._cost_optimizer.get_item_cost_info(item_cd, daily_avg=daily_avg, mid_cd=mid_cd)
                if cost_info.enabled and cost_info.disuse_modifier != 1.0:
                    original_coef = food_disuse_coef
                    food_disuse_coef = min(food_disuse_coef * cost_info.disuse_modifier, 1.0)
                    logger.info(
                        f"[비용최적화] {product['item_nm']}: "
                        f"폐기계수 {original_coef:.2f}→{food_disuse_coef:.2f} "
                        f"(마진={cost_info.margin_rate}%, 회전={cost_info.turnover_level}, "
                        f"비중={cost_info.category_share:.1%})"
                    )

            delivery_waste_adj = get_delivery_waste_adjustment(
                item_cd, product["item_nm"], store_id=self.store_id
            )
            effective_waste_coef = min(food_disuse_coef, delivery_waste_adj)
            if effective_waste_coef < 1.0:
                adjusted_prediction *= effective_waste_coef
                daily_avg = adjusted_prediction
                ctx["adjusted_prediction"] = adjusted_prediction
                logger.info(
                    f"폐기 보정: {product['item_nm']} "
                    f"(disuse={food_disuse_coef:.2f}, delivery={delivery_waste_adj:.2f}, "
                    f"effective={effective_waste_coef:.2f})"
                )

            safety_stock, food_pattern = get_safety_stock_with_food_pattern(
                mid_cd, daily_avg, item_cd, disuse_rate=None,
                store_id=self.store_id, db_path=self.db_path
            )
            ctx["food_disuse_coef"] = food_disuse_coef
            if food_pattern:
                ctx["food_expiration_days"] = food_pattern.expiration_days
                ctx["food_expiry_group"] = food_pattern.expiry_group
                ctx["food_safety_days"] = food_pattern.safety_days
                ctx["food_data_source"] = food_pattern.data_source

                # 캘리브레이터 바닥 근접 경고
                if food_pattern.safety_days <= 0.35:
                    logger.warning(
                        f"[캘리브레이터경고] {product['item_nm']}: "
                        f"safety_days={food_pattern.safety_days} (바닥 근접), "
                        f"mid_cd={mid_cd}"
                    )

                food_gap_consumption = calculate_delivery_gap_consumption(
                    daily_avg=daily_avg,
                    item_nm=product["item_nm"],
                    expiry_group=food_pattern.expiry_group,
                    mid_cd=mid_cd,
                    store_id=self.store_id,
                )
                ctx["food_gap_consumption"] = food_gap_consumption
                if food_gap_consumption > 0:
                    logger.info(
                        f"배송갭 소비량: {product['item_nm']} "
                        f"(gap={food_gap_consumption:.2f}, "
                        f"group={food_pattern.expiry_group})"
                    )

            exp_days = ctx["food_expiration_days"] if ctx["food_expiration_days"] else product["expiration_days"]
            food_max_stock_days = min(exp_days + 1, 7)
            food_max_stock = daily_avg * food_max_stock_days
            if daily_avg >= 0.5 and food_max_stock > 0 and current_stock + pending_qty >= food_max_stock:
                food_skip_order = True

        elif is_perishable_category(mid_cd):
            safety_stock, new_cat_pattern = get_safety_stock_with_perishable_pattern(
                mid_cd, daily_avg, product["expiration_days"], item_cd,
                current_stock, pending_qty, store_id=self.store_id
            )
            if new_cat_pattern and new_cat_pattern.skip_order:
                new_cat_skip_order = True

        elif is_beverage_category(mid_cd):
            safety_stock, new_cat_pattern = get_safety_stock_with_beverage_pattern(
                mid_cd, daily_avg, product["expiration_days"], item_cd,
                current_stock, pending_qty, store_id=self.store_id
            )
            if new_cat_pattern and new_cat_pattern.skip_order:
                new_cat_skip_order = True

        elif is_frozen_ice_category(mid_cd):
            safety_stock, new_cat_pattern = get_safety_stock_with_frozen_ice_pattern(
                mid_cd, daily_avg, product["expiration_days"], item_cd,
                current_stock, pending_qty, store_id=self.store_id
            )
            if new_cat_pattern and new_cat_pattern.skip_order:
                new_cat_skip_order = True

        elif is_instant_meal_category(mid_cd):
            safety_stock, new_cat_pattern = get_safety_stock_with_instant_meal_pattern(
                mid_cd, daily_avg, product["expiration_days"], item_cd,
                current_stock, pending_qty, store_id=self.store_id
            )
            if new_cat_pattern and new_cat_pattern.skip_order:
                new_cat_skip_order = True

        elif is_dessert_category(mid_cd):
            safety_stock, new_cat_pattern = get_safety_stock_with_dessert_pattern(
                mid_cd, daily_avg, product["expiration_days"], item_cd,
                current_stock, pending_qty, store_id=self.store_id
            )
            if new_cat_pattern and new_cat_pattern.skip_order:
                new_cat_skip_order = True

        elif is_snack_confection_category(mid_cd):
            snack_orderable_day = product.get("orderable_day") or SNACK_DEFAULT_ORDERABLE_DAYS
            safety_stock, new_cat_pattern = get_safety_stock_with_snack_confection_pattern(
                mid_cd, daily_avg, product["expiration_days"], item_cd,
                current_stock, pending_qty, store_id=self.store_id,
                orderable_day=snack_orderable_day
            )
            if new_cat_pattern and new_cat_pattern.skip_order:
                new_cat_skip_order = True
                if not new_cat_pattern.is_orderable_today:
                    logger.info(
                        f"[스낵 비발주일] {product['item_nm']} ({item_cd}): "
                        f"orderable_day={snack_orderable_day} -> 오늘 발주 스킵"
                    )

        elif is_alcohol_general_category(mid_cd):
            safety_stock, new_cat_pattern = get_safety_stock_with_alcohol_general_pattern(
                mid_cd, daily_avg, product["expiration_days"], item_cd,
                current_stock, pending_qty, store_id=self.store_id
            )
            if new_cat_pattern:
                weekday_coef = new_cat_pattern.weekday_coef
                adjusted_prediction = base_prediction * weekday_coef
                daily_avg = adjusted_prediction
                ctx["adjusted_prediction"] = adjusted_prediction
                ctx["weekday_coef"] = weekday_coef
                if new_cat_pattern.skip_order:
                    new_cat_skip_order = True

        elif is_daily_necessity_category(mid_cd):
            safety_stock, new_cat_pattern = get_safety_stock_with_daily_necessity_pattern(
                mid_cd, daily_avg, product["expiration_days"], item_cd,
                current_stock, pending_qty, store_id=self.store_id
            )
            if new_cat_pattern and new_cat_pattern.skip_order:
                new_cat_skip_order = True

        elif is_general_merchandise_category(mid_cd):
            safety_stock, new_cat_pattern = get_safety_stock_with_general_merchandise_pattern(
                mid_cd, daily_avg, product["expiration_days"], item_cd,
                current_stock, pending_qty, store_id=self.store_id
            )
            if new_cat_pattern and new_cat_pattern.skip_order:
                new_cat_skip_order = True

        else:
            is_default_category = True
            safety_days = get_safety_stock_days(mid_cd, daily_avg, product["expiration_days"])
            safety_stock = daily_avg * safety_days

        # 비용최적화: 안전재고에 마진x회전율 계수 적용
        if self._cost_optimizer and self._cost_optimizer.enabled:
            cost_info = self._cost_optimizer.get_item_cost_info(item_cd, daily_avg=daily_avg, mid_cd=mid_cd)
            if cost_info.enabled and cost_info.composite_score != 1.0:
                original_safety = safety_stock
                safety_stock *= cost_info.composite_score
                safety_stock = max(safety_stock, 0)
                logger.info(
                    f"[비용최적화] {product['item_nm']}: "
                    f"안전재고 {original_safety:.1f}→{safety_stock:.1f} "
                    f"(마진={cost_info.margin_rate}%, 회전={cost_info.turnover_level}, "
                    f"비중={cost_info.category_share:.1%}, 계수={cost_info.composite_score:.2f})"
                )

        # 간헐적 수요: 최소 안전재고 보장
        if sell_day_ratio < 0.3 and daily_avg > 0:
            min_safety_days = intermittent_config.get("min_safety_stock_days", 0.5)
            min_safety_stock = daily_avg * min_safety_days
            if safety_stock < min_safety_stock:
                original_safety = safety_stock
                safety_stock = min_safety_stock
                logger.info(
                    f"[최소안전재고] {product['item_nm']}: "
                    f"{original_safety:.2f}→{safety_stock:.2f} "
                    f"(ratio={sell_day_ratio:.2%}, {min_safety_days}일치)"
                )

        # 발주량 계산 (lead_time 반영)
        lead_time = product.get("lead_time_days", 1)
        lead_time_demand = adjusted_prediction * (lead_time - 1) if lead_time > 1 else 0.0
        if lead_time > 1:
            logger.debug(
                f"[리드타임] {product['item_nm']}: "
                f"lead_time={lead_time}일, 추가수요={lead_time_demand:.1f}"
            )

        food_gap_consumption = ctx.get("food_gap_consumption", 0.0)
        need_qty = (adjusted_prediction + lead_time_demand
                    + safety_stock + food_gap_consumption
                    - current_stock - pending_qty)

        if need_qty > 0:
            logger.info(
                f"[PRED][3-Need] {product['item_nm']}: need={need_qty:.1f} "
                f"(pred={adjusted_prediction:.2f}+lead={lead_time_demand:.1f}"
                f"+safety={safety_stock:.1f}+gap={food_gap_consumption:.1f}"
                f"-stock={current_stock}-pending={pending_qty})"
            )

        # ★ 최소재고 전략: 가용재고(재고+미입고)가 다음 발주일까지 충분하면 발주 불필요
        effective_stock = current_stock + pending_qty
        if effective_stock > 0 and need_qty > 0:
            days_until_next = self._calculate_days_until_next_order(
                product.get("orderable_day", "일월화수목금토"))
            cover_need = adjusted_prediction * days_until_next
            if effective_stock >= cover_need:
                logger.info(
                    f"[미입고충분] {product['item_nm']}: "
                    f"가용={effective_stock}(stock={current_stock}+pending={pending_qty}), "
                    f"필요={cover_need:.1f}({days_until_next}일분) -> 발주 생략"
                )
                need_qty = 0

        # FORCE_ORDER 발주량 상한
        if current_stock <= 0 and need_qty > 0 and adjusted_prediction > 0:
            force_cap = adjusted_prediction * FORCE_MAX_DAYS
            if need_qty > force_cap:
                logger.info(
                    f"[FORCE상한] {product['item_nm']}: "
                    f"need_qty={need_qty:.1f} -> cap={force_cap:.1f} "
                    f"(예측={adjusted_prediction:.2f} x {FORCE_MAX_DAYS}일)"
                )
                need_qty = force_cap

        # 카테고리별 상한선 체크
        if ctx["ramen_skip_order"]:
            need_qty = 0
        if ctx["tobacco_skip_order"]:
            need_qty = 0
        elif is_tobacco_category(mid_cd) and ctx["tobacco_available_space"] > 0:
            if need_qty > ctx["tobacco_available_space"]:
                need_qty = ctx["tobacco_available_space"]
        if ctx["beer_skip_order"]:
            need_qty = 0
        if ctx["soju_skip_order"]:
            need_qty = 0
        if food_skip_order:
            need_qty = 0
        if new_cat_skip_order:
            need_qty = 0

        # 발주 조정 규칙 적용
        order_qty = self._apply_order_rules(
            need_qty=need_qty, product=product, weekday=weekday,
            current_stock=current_stock, daily_avg=daily_avg,
            pending_qty=pending_qty
        )

        # ROP (재주문점) 로직
        rop_enabled = intermittent_config.get("rop_enabled", True)
        if rop_enabled and sell_day_ratio < 0.3:
            if current_stock == 0 and order_qty == 0:
                order_qty = 1
                logger.info(
                    f"[ROP] {product['item_nm']}: "
                    f"재고=0, ratio={sell_day_ratio:.2%} → 발주 1개"
                )
            elif current_stock == 0 and pending_qty > 0:
                logger.debug(
                    f"[ROP Skip] {product['item_nm']}: "
                    f"재고=0 but 미입고={pending_qty}개 → 발주 생략"
                )

        # 행사 기반 발주 조정
        if self._use_promo_adjustment:
            order_qty = self._apply_promotion_adjustment(
                item_cd, product, order_qty, weekday_coef,
                safety_stock, current_stock, pending_qty, daily_avg
            )

        # ML 앙상블
        order_qty, model_type = self._apply_ml_ensemble(
            item_cd, product, mid_cd, order_qty, data_days, target_date,
            current_stock, pending_qty, safety_stock, feat_result
        )
        ctx["model_type"] = model_type

        # 발주 단위 맞춤 (올림 + max_stock 체크)
        order_unit = product["order_unit_qty"]
        if order_qty > 0 and order_unit > 1:
            order_qty = self._round_to_order_unit(
                order_qty, order_unit, mid_cd, product, daily_avg,
                current_stock, pending_qty, safety_stock, adjusted_prediction,
                ctx, new_cat_pattern, is_default_category
            )

        # 발주 차이 피드백 페널티
        if self._diff_feedback and self._diff_feedback.enabled and order_qty > 0:
            penalty = self._diff_feedback.get_removal_penalty(item_cd)
            if penalty < 1.0:
                old_qty = order_qty
                order_qty = max(1, int(order_qty * penalty))
                logger.info(
                    f"[Diff피드백] {product['item_nm']}: "
                    f"{old_qty}→{order_qty} (penalty={penalty}, "
                    f"제거 {self._diff_feedback._removal_cache.get(item_cd, {}).get('removal_count', 0)}회)"
                )

        # 폐기 원인 피드백
        waste_fb = self._get_waste_feedback()
        if waste_fb and waste_fb.enabled and order_qty > 0:
            try:
                fb_result = waste_fb.get_adjustment(item_cd, product["mid_cd"])
                if fb_result.has_active_feedback and fb_result.multiplier < 1.0:
                    old_qty = order_qty
                    order_qty = max(1, int(order_qty * fb_result.multiplier))
                    logger.info(
                        f"[폐기피드백] {product['item_nm']}: "
                        f"{old_qty}->{order_qty} "
                        f"(원인={fb_result.primary_cause}, "
                        f"승수={fb_result.multiplier:.2f}, "
                        f"신뢰={fb_result.confidence:.2f})"
                    )
            except Exception as e:
                logger.debug(f"[폐기피드백] 실패 ({item_cd}): {e}")

        # 카테고리별 최대 발주량 상한
        max_qty = MAX_ORDER_QTY_BY_CATEGORY.get(product["mid_cd"])
        if max_qty and order_qty > max_qty:
            logger.warning(
                f"[{item_cd}] 최대 발주량 초과: {order_qty}개 → {max_qty}개로 제한 "
                f"(카테고리: {product['mid_cd']})"
            )
            order_qty = max_qty

        ctx["safety_stock"] = safety_stock
        ctx["order_qty"] = order_qty
        return ctx

    def _apply_promotion_adjustment(self, item_cd, product, order_qty,
                                    weekday_coef, safety_stock,
                                    current_stock, pending_qty, daily_avg):
        """행사 기반 발주 조정 (점진적 전환 + 최소 발주)

        Returns:
            조정된 order_qty
        """
        try:
            promo_mgr = self._promo_adjuster.promo_manager
            promo_status = promo_mgr.get_promotion_status(item_cd)
            if not promo_status:
                return order_qty

            # 행사/비행사 통계 미산출 시 on-demand 계산
            if promo_status.current_promo and promo_status.promo_avg == 0:
                try:
                    promo_mgr.calculate_promotion_stats(item_cd)
                    promo_status = promo_mgr.get_promotion_status(item_cd)
                except Exception as e:
                    logger.warning(f"행사 통계 계산 실패 ({item_cd}): {e}")

            # (A) 행사 종료 임박 (D-3 이내)
            if (promo_status.current_promo
                    and promo_status.days_until_end is not None
                    and promo_status.days_until_end <= 3):
                adj_result = self._promo_adjuster.adjust_order_quantity(
                    item_cd=item_cd, base_qty=order_qty,
                    current_stock=current_stock, pending_qty=pending_qty
                )
                if adj_result.adjusted_qty != order_qty:
                    old_qty = order_qty
                    order_qty = adj_result.adjusted_qty
                    logger.info(
                        f"[행사종료조정] {item_cd}: D-{promo_status.days_until_end} "
                        f"발주 {old_qty}→{order_qty} ({adj_result.adjustment_reason})"
                    )

            # (B) 행사 시작 임박 (D-3 이내)
            elif (not promo_status.current_promo
                  and promo_status.next_promo
                  and promo_status.next_start_date):
                try:
                    next_start = datetime.strptime(
                        promo_status.next_start_date, '%Y-%m-%d'
                    ).date()
                    days_until_start = (next_start - date.today()).days
                except Exception as e:
                    logger.warning(f"행사 시작일 파싱 실패: {e}")
                    days_until_start = -1

                if 0 <= days_until_start <= 3:
                    adj_result = self._promo_adjuster.adjust_order_quantity(
                        item_cd=item_cd, base_qty=order_qty,
                        current_stock=current_stock, pending_qty=pending_qty
                    )
                    if adj_result.adjusted_qty != order_qty:
                        old_qty = order_qty
                        order_qty = adj_result.adjusted_qty
                        logger.info(
                            f"[행사시작조정] {item_cd}: D-{days_until_start} "
                            f"발주 {old_qty}→{order_qty} ({adj_result.adjustment_reason})"
                        )

            # (C) 행사 안정기 -> 예측 부족 시 행사 일평균 보정
            elif (promo_status.current_promo
                  and promo_status.promo_avg > 0
                  and daily_avg < promo_status.promo_avg * 0.8):
                promo_need = (promo_status.promo_avg * weekday_coef
                              + safety_stock - current_stock - pending_qty)
                promo_order = int(max(0, promo_need))
                if promo_order > order_qty:
                    old_qty = order_qty
                    order_qty = promo_order
                    logger.info(
                        f"[행사중보정] {item_cd}: {promo_status.current_promo} "
                        f"행사avg {promo_status.promo_avg:.1f} 적용 "
                        f"(예측avg {daily_avg:.1f} < 행사avg×0.8), "
                        f"발주 {old_qty}→{order_qty}"
                    )

            # (D) 비행사 안정기 -> 예측 과다 시 평시 일평균 보정
            elif (not promo_status.current_promo
                  and promo_status.normal_avg > 0
                  and daily_avg > promo_status.normal_avg * 1.3):
                normal_need = (promo_status.normal_avg * weekday_coef
                               + safety_stock - current_stock - pending_qty)
                normal_order = int(max(0, normal_need))
                if 0 <= normal_order < order_qty:
                    old_qty = order_qty
                    order_qty = normal_order
                    logger.info(
                        f"[비행사보정] {item_cd}: "
                        f"평시avg {promo_status.normal_avg:.1f} 적용 "
                        f"(예측avg {daily_avg:.1f} > 평시avg×1.3), "
                        f"발주 {old_qty}→{order_qty}"
                    )

            # 행사 최소 발주량 보정 (1+1->2, 2+1->3)
            if promo_status.current_promo and order_qty > 0:
                promo_unit = PROMO_MIN_STOCK_UNITS.get(promo_status.current_promo, 1)

                # (1) 발주량 자체가 행사 단위 미만이면 행사 단위로 올림
                if order_qty < promo_unit:
                    old_qty = order_qty
                    order_qty = promo_unit
                    logger.info(
                        f"[행사배수보정] {item_cd}: "
                        f"{promo_status.current_promo} 최소발주={promo_unit}개 "
                        f"(발주 {old_qty}->{order_qty})"
                    )

                # (2) 재고+미입고+발주가 행사 단위 미만이면 부족분 추가 (기존 로직 유지)
                expected_stock = current_stock + pending_qty + order_qty
                if expected_stock < promo_unit:
                    shortage = promo_unit - expected_stock
                    order_qty += shortage
                    logger.info(
                        f"[행사최소보정] {item_cd}: "
                        f"{promo_status.current_promo} 최소 {promo_unit}개 "
                        f"(재고{current_stock}+미입고{pending_qty}+"
                        f"발주{order_qty-shortage}={expected_stock} -> +{shortage})"
                    )

        except Exception as e:
            logger.warning(f"행사 조정 실패 ({item_cd}), 기존 발주량 유지: {e}")

        return order_qty

    def _apply_ml_ensemble(self, item_cd, product, mid_cd, order_qty,
                           data_days, target_date,
                           current_stock, pending_qty, safety_stock,
                           feat_result):
        """ML 앙상블 (규칙 기반과 ML 예측의 가중 평균)

        Returns:
            (order_qty, model_type)
        """
        model_type = "rule"
        if not (self._ml_predictor and order_qty > 0 and data_days >= 30):
            return order_qty, model_type

        try:
            from src.prediction.ml.feature_builder import MLFeatureBuilder
            from src.prediction.ml.data_pipeline import MLDataPipeline

            pipeline = MLDataPipeline(self.db_path, store_id=self.store_id)
            daily_sales = pipeline.get_item_daily_stats(item_cd, days=90)
            target_date_str = target_date.strftime("%Y-%m-%d")

            # Lag Features
            _lag_7 = _lag_28 = _wow = None
            try:
                if feat_result and feat_result.lag_features:
                    lf = feat_result.lag_features
                    _lag_7 = lf.lag_7
                    _lag_28 = lf.lag_28
                    _wow = lf.week_over_week_change
            except Exception:
                pass

            # 연관 점수
            _assoc_score = 0.0
            if self._association_adjuster:
                try:
                    _assoc_score = self._association_adjuster.get_association_score(item_cd, mid_cd)
                except Exception:
                    pass

            # 연휴/기온 맥락
            _holiday_ctx = self._get_holiday_context(target_date_str)
            _temp_for_ml = self._get_temperature_for_date(target_date_str)
            _temp_delta = self._get_temperature_delta(target_date_str)

            # 행사 정보 조회
            _promo_active = False
            if self._promo_adjuster and hasattr(self._promo_adjuster, 'promo_manager'):
                try:
                    promo_status = self._promo_adjuster.promo_manager.get_promotion_status(item_cd)
                    _promo_active = bool(promo_status and promo_status.current_promo)
                except Exception:
                    pass

            # 입고 패턴 캐시 조회 (receiving-pattern)
            _recv_stats = self._receiving_stats_cache.get(item_cd, {})

            features = MLFeatureBuilder.build_features(
                daily_sales=daily_sales,
                target_date=target_date_str,
                mid_cd=mid_cd,
                stock_qty=current_stock,
                pending_qty=pending_qty,
                expiration_days=product.get("expiration_days", 0) or 0,
                disuse_rate=self._get_disuse_rate(item_cd) if is_food_category(mid_cd) else 0.0,
                margin_rate=product.get("margin_rate", 0) or 0.0,
                temperature=_temp_for_ml,
                temperature_delta=_temp_delta,
                lag_7=_lag_7,
                lag_28=_lag_28,
                week_over_week=_wow,
                is_holiday=_holiday_ctx.get("is_holiday", False),
                association_score=_assoc_score,
                holiday_period_days=_holiday_ctx.get("period_days", 0),
                is_pre_holiday=_holiday_ctx.get("is_pre_holiday", False),
                is_post_holiday=_holiday_ctx.get("is_post_holiday", False),
                promo_active=_promo_active,
                receiving_stats=_recv_stats,
            )

            if features is not None:
                ml_pred = self._ml_predictor.predict(features, mid_cd)
                if ml_pred is not None:
                    rule_order = order_qty
                    if data_days < 60:
                        ml_weight = 0.3
                        model_type = "ensemble_30"
                    else:
                        ml_weight = 0.5
                        model_type = "ensemble_50"

                    ml_order = max(0, ml_pred + safety_stock - current_stock - pending_qty)
                    blended = (1 - ml_weight) * rule_order + ml_weight * ml_order
                    order_qty = max(0, round(blended))

                    logger.debug(
                        f"[ML앙상블] {item_cd}: rule={rule_order}, "
                        f"ml_sale={ml_pred:.1f}, ml_order={ml_order:.0f}, "
                        f"weight={ml_weight}, blended={order_qty}"
                    )
        except Exception as e:
            logger.debug(f"[ML앙상블] 실패 ({item_cd}), 규칙 기반 유지: {e}")

        return order_qty, model_type

    def _round_to_order_unit(self, order_qty, order_unit, mid_cd, product,
                             daily_avg, current_stock, pending_qty,
                             safety_stock, adjusted_prediction,
                             ctx, new_cat_pattern, is_default_category):
        """발주 단위 맞춤 (올림 + max_stock 체크)

        Returns:
            조정된 order_qty
        """
        ceil_qty = ((order_qty + order_unit - 1) // order_unit) * order_unit
        floor_qty = (order_qty // order_unit) * order_unit

        # 카테고리별 max_stock 결정
        cat_max_stock = None
        if is_tobacco_category(mid_cd):
            cat_max_stock = ctx["tobacco_max_stock"]
        elif is_beer_category(mid_cd) and ctx.get("beer_max_stock") is not None:
            cat_max_stock = ctx["beer_max_stock"]
        elif is_soju_category(mid_cd) and ctx.get("soju_max_stock") is not None:
            cat_max_stock = ctx["soju_max_stock"]
        elif is_ramen_category(mid_cd) and ctx["ramen_max_stock"] > 0:
            cat_max_stock = ctx["ramen_max_stock"]
        elif is_food_category(mid_cd) and ctx.get("food_expiration_days"):
            exp_days = ctx["food_expiration_days"] or product["expiration_days"]
            cat_max_stock = daily_avg * min(exp_days + 1, 7)
        elif new_cat_pattern and hasattr(new_cat_pattern, 'max_stock'):
            cat_max_stock = new_cat_pattern.max_stock
        max_cat_qty = MAX_ORDER_QTY_BY_CATEGORY.get(mid_cd)
        if max_cat_qty:
            cat_max_stock = min(cat_max_stock, max_cat_qty) if cat_max_stock else max_cat_qty

        # 올림 시 max_stock 초과 여부 체크 -> 초과하면 내림 적용
        if cat_max_stock and cat_max_stock > 0:
            if current_stock + pending_qty + ceil_qty > cat_max_stock and floor_qty > 0:
                logger.info(
                    f"[발주단위] {product['item_nm']}: "
                    f"올림 {ceil_qty} → 내림 {floor_qty} "
                    f"(max_stock={cat_max_stock:.0f}, "
                    f"재고={current_stock}+미입고={pending_qty})"
                )
                return floor_qty
            else:
                return ceil_qty
        elif is_default_category:
            surplus = ceil_qty - order_qty
            if surplus >= safety_stock and current_stock + surplus >= adjusted_prediction + safety_stock:
                return 0
            else:
                return ceil_qty
        else:
            return ceil_qty

    def _apply_order_rules(
        self,
        need_qty: float,
        product: Dict[str, object],
        weekday: int,  # 0=월, 6=일
        current_stock: int,
        daily_avg: float,
        pending_qty: int = 0
    ) -> int:
        """발주 조정 규칙 적용

        금요일 부스트, 폐기 방지, 재고 과다 방지 등의 규칙을 순차 적용하여
        최종 발주량을 결정한다.

        Args:
            need_qty: 기본 필요량
            product: 상품 정보 딕셔너리
            weekday: 요일 (0=월, 6=일)
            current_stock: 현재 재고
            daily_avg: 일평균 판매량
            pending_qty: 미입고 수량 (재고과다 방지에 반영)

        Returns:
            조정된 발주 수량 (정수)
        """
        rules = ORDER_ADJUSTMENT_RULES
        order_qty = need_qty

        # Priority 1.5: 푸드류 최소 발주량 보장 (재고 없을 때) - 다른 규칙보다 먼저 적용
        if product["mid_cd"] in FOOD_CATEGORIES:
            # 재고 없고 미입고도 없고 일평균이 0.2개 이상이면 최소 1개 발주
            if current_stock == 0 and pending_qty == 0 and daily_avg >= 0.2:
                return 1  # 최소 1개 발주 (다른 규칙 무시)

        # 1. 금요일 부스트 (주류, 담배)
        if rules["friday_boost"]["enabled"]:
            if weekday == 4 and product["mid_cd"] in rules["friday_boost"]["categories"]:
                order_qty *= rules["friday_boost"]["boost_rate"]

        # 2. 폐기 방지 (초단기 상품)
        if rules["disuse_prevention"]["enabled"]:
            if product["expiration_days"] <= rules["disuse_prevention"]["shelf_life_threshold"]:
                order_qty *= rules["disuse_prevention"]["reduction_rate"]

        # 3. 재고 과다 방지 (★ pending_qty 포함하여 가용재고 기준으로 판단)
        if rules["overstock_prevention"]["enabled"]:
            if daily_avg > 0:
                effective_stock = current_stock + pending_qty
                stock_days = effective_stock / daily_avg
                if stock_days >= rules["overstock_prevention"]["stock_days_threshold"]:
                    if rules["overstock_prevention"]["skip_order"]:
                        return 0

        # 4. 반올림 + 최소 발주 임계값
        # 간헐적 수요 지원: 0.1개 이상이면 발주 허용
        min_order_threshold = PREDICTION_PARAMS.get("min_order_threshold", 0.5)

        if order_qty < min_order_threshold:
            return 0  # 임계값 미만: 발주 안 함
        elif order_qty < 1.0:
            return 1  # 1개 미만: 최소 1개 발주
        else:
            # 반올림 로직 (기존 유지)
            threshold = PREDICTION_PARAMS["round_up_threshold"]
            if order_qty - int(order_qty) >= threshold:
                return int(order_qty) + 1
            return int(order_qty)

    def _load_receiving_stats_cache(self) -> None:
        """입고 패턴 통계 배치 캐시 로드 (receiving-pattern)

        receiving_repo + order_tracking_repo에서 1회 배치 쿼리하여
        self._receiving_stats_cache에 {item_cd: stats_dict} 저장
        """
        try:
            from src.infrastructure.database.repos.receiving_repo import ReceivingRepository
            from src.infrastructure.database.repos.order_tracking_repo import OrderTrackingRepository

            recv_repo = ReceivingRepository(store_id=self.store_id)
            ot_repo = OrderTrackingRepository(store_id=self.store_id)

            # 입고 패턴 (30일)
            pattern_stats = recv_repo.get_receiving_pattern_stats_batch(
                store_id=self.store_id, days=30
            )

            # pending 경과일
            pending_ages = ot_repo.get_pending_age_batch(store_id=self.store_id)

            # 머지
            self._receiving_stats_cache = {}
            all_items = set(pattern_stats.keys()) | set(pending_ages.keys())
            for item_cd in all_items:
                stats = dict(pattern_stats.get(item_cd, {}))
                stats["pending_age_days"] = pending_ages.get(item_cd, 0)
                self._receiving_stats_cache[item_cd] = stats

            logger.info(f"[입고패턴] 캐시 로드: {len(self._receiving_stats_cache)}개 상품")
        except Exception as e:
            logger.warning(f"[입고패턴] 캐시 로드 실패 (무시): {e}")
            self._receiving_stats_cache = {}

    def predict_batch(
        self,
        item_codes: List[str],
        target_date: Optional[datetime] = None,
        pending_quantities: Optional[Dict[str, int]] = None
    ) -> List[PredictionResult]:
        """여러 상품 일괄 예측

        Args:
            item_codes: 상품코드 리스트
            target_date: 예측 대상 날짜 (기본: 내일)
            pending_quantities: {상품코드: 미입고수량} 딕셔너리

        Returns:
            PredictionResult 리스트
        """
        results = []
        pending_quantities = pending_quantities or {}

        # 입고 패턴 배치 캐시 프리로드 (DB 쿼리 2회)
        self._load_receiving_stats_cache()

        # 폐기 원인 피드백 프리로드 (DB 쿼리 1회)
        waste_fb = self._get_waste_feedback()
        if waste_fb and waste_fb.enabled:
            try:
                waste_fb.preload(item_codes)
            except Exception as e:
                logger.debug(f"WasteFeedback preload 실패: {e}")

        for item_cd in item_codes:
            pending = pending_quantities.get(item_cd, None)
            result = self.predict(item_cd, target_date, pending)
            if result:
                results.append(result)

        return results

    # =========================================================================
    # Feature Engineering 기반 예측 메서드 (v8)
    # =========================================================================

    def calculate_enhanced_prediction(
        self,
        item_cd: str,
        target_date: Optional[datetime] = None,
        weekday_coef: float = 1.0,
        base_safety_days: float = 1.0
    ) -> Optional[Dict[str, object]]:
        """
        Feature Engineering 기반 향상된 예측

        Lag Features, Rolling Features, EWM, 트렌드 분석을 활용한
        더 정교한 예측 결과를 반환합니다.

        Args:
            item_cd: 상품코드
            target_date: 예측 대상 날짜 (기본: 내일)
            weekday_coef: 요일 계수
            base_safety_days: 기본 안전재고 일수

        Returns:
            {
                'prediction': 최종 예측값,
                'safety_days': 변동성 조정된 안전재고 일수,
                'trend_adjustment': 트렌드 조정 계수,
                'volatility_level': 변동성 레벨,
                'data_quality': 데이터 품질,
                'features': FeatureResult 객체,
            }
        """
        if target_date is None:
            target_date = datetime.now() + timedelta(days=1)

        return self._feature_calculator.calculate_enhanced_prediction(
            item_cd, target_date, weekday_coef, base_safety_days
        )

    def predict_with_features(
        self,
        item_cd: str,
        target_date: Optional[datetime] = None,
        pending_qty: Optional[int] = None,
        use_enhanced: bool = True
    ) -> Optional[PredictionResult]:
        """
        Feature Engineering을 활용한 예측

        기존 predict() 메서드를 확장하여 Feature Engineering 결과를 반영합니다.

        Args:
            item_cd: 상품코드
            target_date: 예측 대상 날짜 (기본: 내일)
            pending_qty: 미입고 수량
            use_enhanced: True면 Feature Engineering 적용 (기본 True)

        Returns:
            PredictionResult (기존과 동일 구조)
        """
        if not use_enhanced:
            return self.predict(item_cd, target_date, pending_qty)

        # 1. 상품 정보 조회
        product = self.get_product_info(item_cd)
        if not product:
            return None

        # 2. 대상 날짜 설정
        if target_date is None:
            target_date = datetime.now() + timedelta(days=1)

        weekday = target_date.weekday()
        sqlite_weekday = (weekday + 1) % 7

        # 3. 요일 계수 조회
        mid_cd = product["mid_cd"]
        weekday_coef = get_weekday_coefficient(mid_cd, sqlite_weekday)

        # 맥주/소주는 자체 요일 계수 사용
        if is_beer_category(mid_cd):
            weekday_coef = get_beer_weekday_coef(weekday)
        elif is_soju_category(mid_cd):
            weekday_coef = get_soju_weekday_coef(weekday)

        # 4. 기본 안전재고 일수 조회
        base_safety_days = get_safety_stock_days(
            mid_cd,
            1.0,  # 임시 daily_avg
            product["expiration_days"]
        )

        # 5. Feature Engineering 예측
        enhanced = self.calculate_enhanced_prediction(
            item_cd, target_date, weekday_coef, base_safety_days
        )

        if enhanced is None or enhanced['prediction'] <= 0:
            # Feature 계산 실패 시 기존 예측으로 fallback
            return self.predict(item_cd, target_date, pending_qty)

        # 6. 현재 재고/미입고 조회 (inv_data 1회 조회로 통합)
        inv_data = None
        if self._use_db_inventory and self._inventory_repo:
            if item_cd not in self._stock_cache or (pending_qty is None and item_cd not in self._pending_cache):
                inv_data = self._inventory_repo.get(item_cd)

        if item_cd in self._stock_cache:
            current_stock = self._stock_cache[item_cd]
        elif inv_data and inv_data.get('stock_qty') is not None:
            current_stock = inv_data['stock_qty']
        else:
            current_stock = self.get_current_stock(item_cd)

        if pending_qty is None:
            if item_cd in self._pending_cache:
                pending_qty = self._pending_cache[item_cd]
            elif inv_data:
                if inv_data.get('_stale', False):
                    pending_qty = 0
                else:
                    pending_qty = inv_data.get('pending_qty', 0)
            else:
                pending_qty = 0

        # 7. 발주량 계산
        base_prediction = enhanced['base_prediction']
        adjusted_prediction = enhanced['prediction']
        safety_days = enhanced['safety_days']
        safety_stock = base_prediction * safety_days

        need_qty = adjusted_prediction + safety_stock - current_stock - pending_qty

        # 8. 발주 조정 규칙 적용
        order_qty = self._apply_order_rules(
            need_qty=need_qty,
            product=product,
            weekday=weekday,
            current_stock=current_stock,
            daily_avg=base_prediction,
            pending_qty=pending_qty
        )

        # 9. 발주 단위 맞춤
        order_unit = product["order_unit_qty"]
        if order_qty > 0 and order_unit > 1:
            unit_qty = ((order_qty + order_unit - 1) // order_unit) * order_unit

            # 전용 핸들러 없는 카테고리만 과잉발주 보정 적용
            has_dedicated_handler = any([
                is_ramen_category(mid_cd), is_tobacco_category(mid_cd),
                is_beer_category(mid_cd), is_soju_category(mid_cd),
                is_food_category(mid_cd), is_perishable_category(mid_cd),
                is_beverage_category(mid_cd), is_frozen_ice_category(mid_cd),
                is_instant_meal_category(mid_cd), is_dessert_category(mid_cd),
                is_snack_confection_category(mid_cd),
                is_alcohol_general_category(mid_cd), is_daily_necessity_category(mid_cd),
                is_general_merchandise_category(mid_cd),
            ])
            if not has_dedicated_handler:
                surplus = unit_qty - order_qty  # 올림으로 인한 잉여
                if surplus >= safety_stock and current_stock + surplus >= adjusted_prediction + safety_stock:
                    order_qty = 0
                else:
                    order_qty = unit_qty
            else:
                order_qty = unit_qty

        # 10. 신뢰도 판단
        data_quality = enhanced['data_quality']
        if data_quality == "high":
            confidence = "high"
        elif data_quality == "medium":
            confidence = "medium"
        else:
            confidence = "low"

        return PredictionResult(
            item_cd=item_cd,
            item_nm=product["item_nm"],
            mid_cd=mid_cd,
            target_date=target_date.strftime("%Y-%m-%d"),
            predicted_qty=round(base_prediction, 2),
            adjusted_qty=round(adjusted_prediction, 2),
            current_stock=current_stock,
            pending_qty=pending_qty,
            safety_stock=round(safety_stock, 2),
            order_qty=int(max(0, order_qty)),
            confidence=confidence,
            data_days=enhanced['features'].rolling_features.data_days if enhanced['features'].rolling_features else 0,
            weekday_coef=round(weekday_coef, 2),
        )

    def get_feature_summary(
        self,
        item_cd: str,
        target_date: Optional[datetime] = None
    ) -> Dict[str, Optional[float]]:
        """
        상품의 Feature 요약 정보 조회

        디버깅 및 분석용 메서드

        Args:
            item_cd: 상품코드
            target_date: 예측 대상 날짜

        Returns:
            Feature 요약 딕셔너리
        """
        if target_date is None:
            target_date = datetime.now() + timedelta(days=1)

        features = self._feature_calculator.calculate(item_cd, target_date)

        lag = features.lag_features
        roll = features.rolling_features

        return {
            'item_cd': item_cd,
            'target_date': features.target_date,
            # Lag Features
            'lag_1': lag.lag_1 if lag else None,
            'lag_7': lag.lag_7 if lag else None,
            'lag_28': lag.lag_28 if lag else None,
            'same_weekday_avg': lag.same_weekday_avg if lag else None,
            'week_over_week_change': lag.week_over_week_change if lag else None,
            # Rolling Features
            'rolling_mean_7': roll.rolling_mean_7 if roll else None,
            'rolling_mean_28': roll.rolling_mean_28 if roll else None,
            'rolling_std_28': roll.rolling_std_28 if roll else None,
            'ewm_mean_7': roll.ewm_mean_7 if roll else None,
            'coefficient_of_variation': roll.coefficient_of_variation if roll else None,
            'trend_slope': roll.trend_slope if roll else None,
            # 통합
            'weighted_prediction': features.weighted_prediction,
            'volatility_level': features.volatility_level,
            'trend_direction': features.trend_direction,
            'data_quality': features.data_quality,
            'calculation_method': features.calculation_method,
        }

    # =========================================================================
    # 행사 정보 기반 발주 조정 메서드 (v9)
    # =========================================================================

    def set_promo_adjustment(self, enabled: bool = True) -> None:
        """행사 조정 활성화/비활성화

        Args:
            enabled: True면 활성화, False면 비활성화
        """
        self._use_promo_adjustment = enabled
        logger.info(f"행사 조정: {'활성화' if enabled else '비활성화'}")

    def apply_promotion_adjustment(
        self,
        item_cd: str,
        base_qty: int,
        current_stock: int = 0,
        pending_qty: int = 0
    ) -> Tuple[int, str]:
        """
        행사 상태에 따른 발주량 조정

        Args:
            item_cd: 상품코드
            base_qty: 기본 발주량
            current_stock: 현재 재고
            pending_qty: 미입고 수량

        Returns:
            (조정된 발주량, 조정 사유)
        """
        if not self._use_promo_adjustment:
            return base_qty, "행사 조정 비활성화"

        try:
            result = self._promo_adjuster.adjust_order_quantity(
                item_cd=item_cd,
                base_qty=base_qty,
                current_stock=current_stock,
                pending_qty=pending_qty
            )
            return result.adjusted_qty, result.adjustment_reason
        except Exception as e:
            logger.warning(f"행사 조정 실패 ({item_cd}): {e}")
            return base_qty, "조정 실패"

    def get_promotion_status(self, item_cd: str) -> Optional[Dict[str, Optional[float]]]:
        """
        상품의 행사 상태 조회

        Args:
            item_cd: 상품코드

        Returns:
            {
                'current_promo': 현재 행사,
                'days_until_end': 종료까지 남은 일수,
                'next_promo': 다음 행사,
                'will_change': 변경 예정 여부,
                'promo_multiplier': 행사 배율,
            }
        """
        try:
            status = self._promo_adjuster.promo_manager.get_promotion_status(item_cd)
            if not status:
                return None

            return {
                'item_nm': status.item_nm,
                'current_promo': status.current_promo,
                'current_end_date': status.current_end_date,
                'days_until_end': status.days_until_end,
                'next_promo': status.next_promo,
                'next_start_date': status.next_start_date,
                'will_change': status.will_change,
                'change_type': status.change_type,
                'promo_multiplier': status.promo_multiplier,
                'normal_avg': status.normal_avg,
            }
        except Exception as e:
            logger.warning(f"행사 상태 조회 실패 ({item_cd}): {e}")
            return None

    def get_ending_promotions(self, days: int = 3) -> List[Dict[str, object]]:
        """
        종료 임박 행사 상품 조회

        Args:
            days: 며칠 이내 종료 예정

        Returns:
            행사 종료 임박 상품 리스트
        """
        try:
            statuses = self._promo_adjuster.promo_manager.get_ending_promotions(days)
            return [
                {
                    'item_cd': s.item_cd,
                    'item_nm': s.item_nm,
                    'promo': s.current_promo,
                    'days_remaining': s.days_until_end,
                    'next_promo': s.next_promo,
                    'promo_multiplier': s.promo_multiplier,
                }
                for s in statuses
            ]
        except Exception as e:
            logger.warning(f"종료 임박 행사 조회 실패: {e}")
            return []

    def get_high_risk_promo_items(self) -> List[Dict[str, object]]:
        """
        고위험 행사 상품 조회 (종료 D-1 이내, 다음 행사 없음)

        Returns:
            고위험 상품 리스트
        """
        try:
            return self._promo_adjuster.get_high_risk_items()
        except Exception as e:
            logger.warning(f"고위험 상품 조회 실패: {e}")
            return []

    def get_order_candidates(
        self,
        target_date: Optional[datetime] = None,
        min_order_qty: int = 1,
        exclude_items: Optional[set] = None
    ) -> List[PredictionResult]:
        """
        발주 대상 상품 추출

        Args:
            target_date: 예측 대상 날짜
            min_order_qty: 최소 발주량
            exclude_items: 제외할 상품코드 set (사전 평가에서 SKIP된 상품)

        Returns:
            발주량이 min_order_qty 이상인 PredictionResult 리스트
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # 최근 판매 이력이 있는 상품만
            cursor.execute("""
                SELECT DISTINCT item_cd
                FROM daily_sales
                WHERE store_id = ?
                AND sales_date >= date('now', '-14 days')
                AND sale_qty > 0
            """, (self.store_id,))

            item_codes = [row[0] for row in cursor.fetchall()]
        finally:
            conn.close()

        # 스킵 상품 제외
        if exclude_items:
            item_codes = [cd for cd in item_codes if cd not in exclude_items]

        # 검증기간 중 신상품 제외 (도입 후 검증 완료 전까지 예측에서 빠짐)
        try:
            from src.infrastructure.database.repos import NewProductStatusRepository
            from datetime import datetime as _dt
            np_repo = NewProductStatusRepository(store_id=self.store_id)
            month_ym = _dt.now().strftime("%Y%m")
            verification_items = np_repo.get_items_in_verification(self.store_id, month_ym)
            if verification_items:
                item_codes = [cd for cd in item_codes if cd not in verification_items]
                logger.info(f"신상품 검증기간 {len(verification_items)}개 예측 제외")
        except Exception:
            pass

        # 발주정지 상품 제외 (common.db stopped_items)
        try:
            from src.infrastructure.database.repos import StoppedItemRepository
            stopped_repo = StoppedItemRepository()
            stopped_items = stopped_repo.get_active_item_codes()
            if stopped_items:
                before = len(item_codes)
                item_codes = [cd for cd in item_codes if cd not in stopped_items]
                excluded = before - len(item_codes)
                if excluded:
                    logger.info(f"발주정지 상품 {excluded}개 예측 대상에서 제외")
        except Exception as e:
            logger.warning(f"stopped_items 조회 실패: {e}")

        # CUT(발주중지) 상품 제외
        cut_items = set()
        if self._use_db_inventory and self._inventory_repo:
            cut_items = set(self._inventory_repo.get_cut_items(store_id=self.store_id))
            if cut_items:
                before = len(item_codes)
                item_codes = [cd for cd in item_codes if cd not in cut_items]
                excluded = before - len(item_codes)
                if excluded:
                    logger.info(f"CUT 상품 {excluded}개 예측 대상에서 제외")

        # 미취급 상품 제외 (is_available=0 + 판매이력 기반)
        unavailable_items = set()
        if self._use_db_inventory and self._inventory_repo:
            unavailable_items = set(self._inventory_repo.get_unavailable_items(
                store_id=self.store_id))

        # 판매이력 기반: 최근 30일 판매=0 + 현재 재고=0 -> 미취급 판정
        if item_codes:
            try:
                conn = self._get_connection()
                try:
                    cursor = conn.cursor()
                    # item_codes를 청크로 나누어 쿼리 (SQLite 파라미터 제한 대응)
                    zero_sales_30d = set()
                    chunk_size = 500
                    for i in range(0, len(item_codes), chunk_size):
                        chunk = item_codes[i:i + chunk_size]
                        placeholders = ','.join('?' * len(chunk))
                        cursor.execute(f"""
                            SELECT item_cd
                            FROM daily_sales
                            WHERE store_id = ? AND item_cd IN ({placeholders})
                            AND sales_date >= date('now', '-30 days')
                            GROUP BY item_cd
                            HAVING SUM(sale_qty) = 0
                        """, (self.store_id, *chunk))
                        zero_sales_30d.update(row[0] for row in cursor.fetchall())
                finally:
                    conn.close()

                # 30일 판매0 중 재고+미입고도 0인 것만 미취급으로 분류
                if zero_sales_30d:
                    for ic in zero_sales_30d:
                        inv = self.get_inventory_from_db(ic)
                        if inv and inv.get('stock_qty', 0) <= 0 and inv.get('pending_qty', 0) <= 0:
                            unavailable_items.add(ic)
            except Exception as e:
                logger.debug(f"판매이력 기반 미취급 필터 실패 (무시): {e}")

        if unavailable_items:
            before = len(item_codes)
            item_codes = [cd for cd in item_codes if cd not in unavailable_items]
            excluded = before - len(item_codes)
            if excluded:
                logger.info(f"미취급 상품 {excluded}개 예측 대상에서 제외")

        # 일괄 예측
        results = self.predict_batch(item_codes, target_date)

        # 반복 추가 상품 강제 포함 (Diff 피드백: recall 향상)
        if self._diff_feedback and self._diff_feedback.enabled:
            try:
                freq_added = self._diff_feedback.get_frequently_added_items()
                existing_items = set(item_codes)
                injected = 0
                for fa in freq_added:
                    if fa["item_cd"] not in existing_items:
                        # 미취급/CUT 상품은 주입하지 않음
                        if fa["item_cd"] in unavailable_items or fa["item_cd"] in cut_items:
                            continue
                        try:
                            pr = self.predict(fa["item_cd"], target_date)
                            if pr:
                                if pr.order_qty == 0:
                                    pr.order_qty = max(
                                        DIFF_FEEDBACK_ADDITION_MIN_QTY,
                                        fa["avg_qty"],
                                    )
                                results.append(pr)
                                injected += 1
                                logger.info(
                                    f"[Diff주입] {fa['item_nm']}: "
                                    f"추가 {fa['count']}회 -> 발주 {pr.order_qty}개"
                                )
                        except Exception:
                            pass  # 개별 상품 예측 실패 무시
                if injected > 0:
                    logger.info(f"[Diff피드백] 반복 추가 상품 {injected}개 주입")
            except Exception as e:
                logger.debug(f"[Diff피드백] 주입 실패: {e}")

        # 발주량 있는 것만 필터링
        return [r for r in results if r.order_qty >= min_order_qty]

    def predict_and_log(self, target_date: Optional[datetime] = None) -> int:
        """전체 활성 상품 예측 수행 + prediction_logs 저장 (자동발주와 독립)

        자동발주 실행 여부와 무관하게 매일 예측을 기록하여
        예측 정확도 추적 및 ML 학습 데이터를 축적한다.
        이미 오늘 날짜로 prediction_logs가 있으면 스킵하여 중복 방지.

        Args:
            target_date: 예측 대상 날짜 (기본: 내일)

        Returns:
            저장된 예측 건수
        """
        prediction_logger = PredictionLogger(self.db_path, store_id=self.store_id)

        # 중복 체크: 오늘 이미 기록했으면 스킵
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            today = datetime.now().strftime("%Y-%m-%d")
            from src.db.store_query import store_filter
            sf, sp = store_filter(None, self.store_id)
            cursor.execute(
                f"SELECT COUNT(*) FROM prediction_logs WHERE prediction_date = ? {sf}",
                (today,) + sp
            )
            existing = cursor.fetchone()[0]
            if existing > 0:
                logger.info(f"예측 로깅 스킵: 오늘({today}) 이미 {existing}건 기록됨")
                return 0
        finally:
            conn.close()

        # 전체 활성 상품 예측 (min_order_qty=0으로 발주량 0인 상품도 포함)
        results = self.get_order_candidates(
            target_date=target_date,
            min_order_qty=0
        )

        if not results:
            logger.info("예측 로깅: 예측 대상 상품 없음")
            return 0

        # prediction_logs에 저장
        saved = prediction_logger.log_predictions_batch(results)
        logger.info(f"예측 로깅: {saved}/{len(results)}건 저장")
        return saved


# =============================================================================
# PredictionLogger re-export (Phase 6-1: prediction_logger.py로 분리)
# =============================================================================
from .prediction_logger import PredictionLogger
