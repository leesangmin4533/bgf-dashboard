"""
CoefficientAdjuster — 계수 적용 (연휴/기온/요일/계절/연관)

ImprovedPredictor에서 추출된 단일 책임 클래스.
기본 예측값에 다양한 보정 계수를 적용한다.

god-class-decomposition PDCA Step 2
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

from src.utils.logger import get_logger
from src.infrastructure.database.repos import ExternalFactorRepository
from .prediction_config import PREDICTION_PARAMS, get_seasonal_coefficient
from .categories import (
    get_weekday_coefficient,
    is_food_category,
    get_food_weekday_coefficient,
    get_food_weather_cross_coefficient,
    get_food_precipitation_cross_coefficient,
)

logger = get_logger(__name__)


class CoefficientAdjuster:
    """계수 적용 (연휴/기온/요일/계절/연관/트렌드)

    ImprovedPredictor로부터 추출된 클래스.
    기본 예측값에 보정 계수를 적용한다.
    """

    # 기온 기반 카테고리별 수요 조정 계수
    WEATHER_COEFFICIENTS = {
        "hot_boost": {
            "categories": ["010", "034", "039", "043", "045", "048", "021", "100"],
            "temp_threshold": 30,
            "coefficient": 1.15,
        },
        "hot_reduce": {
            "categories": ["001", "002", "003", "004", "005"],
            "temp_threshold": 30,
            "coefficient": 0.90,
        },
        "cold_boost": {
            "categories": ["006", "027", "028", "031", "032", "033", "035"],
            "temp_threshold": 5,
            "coefficient": 1.10,
            "below": True,
        },
    }

    # 기온 급변 보정 계수
    WEATHER_DELTA_COEFFICIENTS = {
        "sudden_cold": {
            "categories": ["006", "027", "028", "031", "032", "033", "035",
                           "001", "002", "003", "004", "005"],
            "delta_threshold": -10,
            "below": True,
            "coefficient": 1.10,
        },
        "sudden_cold_reduce": {
            "categories": ["010", "034", "039", "043", "045", "048", "021", "100"],
            "delta_threshold": -10,
            "below": True,
            "coefficient": 0.90,
        },
        "sudden_hot": {
            "categories": ["010", "034", "039", "043", "045", "048", "021", "100"],
            "delta_threshold": 10,
            "below": False,
            "coefficient": 1.10,
        },
        "sudden_hot_reduce": {
            "categories": ["001", "002", "003", "004", "005",
                           "006", "027", "028", "031", "032", "033", "035"],
            "delta_threshold": 10,
            "below": False,
            "coefficient": 0.90,
        },
    }

    # 강수 기반 카테고리별 수요 조정 계수
    PRECIPITATION_COEFFICIENTS = {
        "light_rain": {  # 강수확률 30~60%
            "categories": ["001", "002", "003", "004", "005", "012"],
            "coefficient": 0.95,
        },
        "moderate_rain": {  # 강수확률 60~80%
            "categories": ["001", "002", "003", "004", "005", "012"],
            "coefficient": 0.90,
        },
        "moderate_rain_boost": {  # 60~80% 라면/핫푸드 수요 증가
            "categories": ["015", "016", "017", "018"],
            "coefficient": 1.05,
        },
        "heavy_rain": {  # 강수확률 80%+ 또는 강수량 10mm+
            "categories": ["001", "002", "003", "004", "005", "012"],
            "coefficient": 0.85,
        },
        "heavy_rain_boost": {  # 80%+ 라면/핫푸드 수요 증가
            "categories": ["015", "016", "017", "018"],
            "coefficient": 1.10,
        },
        "snow": {  # 눈
            "categories": ["001", "002", "003", "004", "005", "012"],
            "coefficient": 0.82,
        },
        "snow_boost": {  # 눈 시 라면/핫푸드 수요 증가
            "categories": ["015", "016", "017", "018"],
            "coefficient": 1.12,
        },
    }

    # Phase A-1: 강수량(rain_qty) 구간별 기본 계수
    RAIN_QTY_BASE_COEFFICIENTS: Dict[str, float] = {
        "none":     1.00,
        "drizzle":  0.97,   # 0.1~2mm
        "light":    0.93,   # 2~5mm
        "moderate": 0.87,   # 5~15mm
        "heavy":    0.80,   # 15mm+
    }

    # Phase A-1: 강수 등급 순서 (상향 조정만 허용)
    _RAIN_LEVEL_ORDER: Dict[str, int] = {
        "none": 0, "drizzle": 1, "light": 2, "moderate": 3, "heavy": 4, "snow": 5,
    }

    # Phase A-2: 날씨 유형(weather_cd_nm) 기반 외출 감소 계수
    # external_factors에 저장만 되던 데이터를 실제 계수로 연결
    # 강수(rain_rate/rain_qty) 계수와 독립적 신호 — 흐림/안개는 비 없이도 외출↓
    # 소나기는 rain_rate 계수가 이미 처리하므로 1.0 유지
    SKY_CONDITION_COEFFICIENTS: Dict[str, float] = {
        "맑음":    1.00,
        "구름많음": 0.98,
        "흐림":    0.95,   # 외출 감소
        "안개":    0.93,   # 외출 감소
        "황사":    0.90,   # 외출 크게 감소
        "소나기":  1.00,   # rain_rate 계수가 이미 처리
    }

    # Phase A-3: 급여일 부스트 대상 카테고리 (충동구매 카테고리)
    PAYDAY_BOOST_CATEGORIES = {
        "015", "016", "017", "018", "019", "020", "029", "030",  # 스낵
        "010", "039", "043", "045", "048",                        # 음료
        "021", "034",                                              # 아이스
        "049", "050",                                              # 주류
    }

    # Phase A-3: 급여일 오프셋별 부스트 계수 (D-day, D+1, D+2)
    PAYDAY_OFFSET_COEFFICIENTS: Dict[int, float] = {
        0: 1.08,   # 급여 당일
        1: 1.05,   # 급여일 다음날
        2: 1.02,   # 급여일 2일 후
    }

    # Phase A-3: 월말 수요 감소 계수
    MONTH_END_COEFFICIENT: float = 0.95
    MONTH_END_START_DAY: int = 28

    def __init__(self, store_id: str):
        """
        Args:
            store_id: 점포 코드
        """
        self.store_id = store_id

    # =========================================================================
    # 연휴 관련
    # =========================================================================

    def check_holiday(self, date_str: str) -> bool:
        """external_factors 테이블에서 휴일 여부 확인"""
        ctx = self.get_holiday_context(date_str)
        return ctx.get("is_holiday", False)

    def get_holiday_context(self, date_str: str) -> Dict:
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
            from src.collectors.calendar_collector import get_holiday_context
            return get_holiday_context(date_str)
        except Exception as e:
            logger.debug(f"연휴 맥락 조회 실패 (날짜: {date_str}): {e}")
            try:
                from src.collectors.calendar_collector import get_holiday_context
                return get_holiday_context(date_str)
            except Exception:
                return default

    def get_holiday_coefficient(self, date_str: str, mid_cd: str) -> float:
        """연휴 맥락 기반 차등 계수 계산

        Args:
            date_str: 대상 날짜 (YYYY-MM-DD)
            mid_cd: 중분류 코드

        Returns:
            계수 (1.0 = 조정 없음)
        """
        holiday_cfg = PREDICTION_PARAMS.get("holiday", {})
        if not holiday_cfg.get("enabled", False):
            return 1.0

        ctx = self.get_holiday_context(date_str)

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
        group_key = group.replace("_group", "")
        cat_mult = cat_mults.get(group_key, 1.0)

        # 최종 계수
        if ctx["is_pre_holiday"] or ctx["is_post_holiday"]:
            final = position_mod * cat_mult
        else:
            final = base_coef * position_mod * cat_mult

        final = max(0.7, min(final, 2.5))
        return round(final, 3)

    # =========================================================================
    # 기온 관련
    # =========================================================================

    def get_temperature_for_date(self, date_str: str) -> Optional[float]:
        """날짜별 기온 조회 (예보 우선, 실측 폴백)"""
        try:
            repo = ExternalFactorRepository()
            factors = repo.get_factors(date_str, factor_type='weather', store_id=self.store_id)

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

            return forecast_temp if forecast_temp is not None else actual_temp
        except Exception as e:
            logger.debug(f"기온 조회 실패 (날짜: {date_str}): {e}")
            return None

    def get_temperature_delta(self, date_str: str) -> Optional[float]:
        """전일 대비 기온 변화량 계산"""
        try:
            target_temp = self.get_temperature_for_date(date_str)
            if target_temp is None:
                return None

            prev_date = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            prev_temp = self.get_temperature_for_date(prev_date)
            if prev_temp is None:
                return None

            return target_temp - prev_temp
        except Exception as e:
            logger.debug(f"기온 변화량 계산 실패 (날짜: {date_str}): {e}")
            return None

    def get_weather_coefficient(self, date_str: str, mid_cd: str) -> float:
        """기온 기반 수요 조정 계수 반환

        1. 절대 기온 임계값 (30도+, 5도-) 체크
        2. 전일 대비 기온 급변 (10도+-) 체크
        3. 두 계수를 곱하여 반환
        """
        try:
            coef = 1.0

            temp = self.get_temperature_for_date(date_str)
            if temp is None:
                return 1.0

            # 1. QW-3: 음료 기온 우선 분기 (25도+ 구간, Flag 제어)
            _bev_applied = False
            try:
                from src.settings.constants import (
                    BEVERAGE_TEMP_PRIORITY_ENABLED,
                    BEVERAGE_TEMP_SENSITIVITY,
                )
                if BEVERAGE_TEMP_PRIORITY_ENABLED and mid_cd in BEVERAGE_TEMP_SENSITIVITY:
                    sens = BEVERAGE_TEMP_SENSITIVITY[mid_cd]
                    if temp >= 30:
                        coef = sens.get("30_plus", 1.15)
                        _bev_applied = True
                    elif temp >= 25:
                        coef = sens.get("25_30", 1.10)
                        _bev_applied = True
            except ImportError:
                pass

            # 1-1. 기존 절대 기온 임계값 계수 (음료 우선 분기 미적용 시)
            if not _bev_applied:
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
            delta = self.get_temperature_delta(date_str)
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

    # =========================================================================
    # 급여일 관련 (Phase A-3)
    # =========================================================================

    def get_payday_coefficient(self, date_str: str, mid_cd: str) -> float:
        """매출 패턴 기반 급여일 효과 계수 반환 (Phase A-3)

        DB 우선 조회 → 없으면 하드코딩 폴백:
          [DB 있을 때]
            boost_days  에 해당 일(day) 포함 + 대상 카테고리 → offset 계수
            decline_days에 해당 일(day) 포함               → 0.95x
          [DB 없을 때 폴백]
            10일/25일 ±2일 부스트 + 28일~ 감소 (하드코딩)

        대상 카테고리 (PAYDAY_BOOST_CATEGORIES): 스낵·음료·아이스·주류
        비대상 카테고리: 월말 decline 만 적용 (boost 없음)
        """
        from src.settings.constants import PAYDAY_ENABLED

        if not PAYDAY_ENABLED:
            return 1.0

        try:
            target = datetime.strptime(date_str, "%Y-%m-%d")
            day = target.day

            # ── DB 우선 조회 ──
            boost_days, decline_days = self._load_payday_windows(date_str)

            if boost_days is not None:  # DB 데이터 있음
                # 1. 부스트 구간 — 대상 카테고리만
                if mid_cd in self.PAYDAY_BOOST_CATEGORIES and day in boost_days:
                    offset = self._calc_window_offset(day, boost_days)
                    coef = self.PAYDAY_OFFSET_COEFFICIENTS.get(offset, 1.02)
                    logger.debug(
                        f"[급여일][A-3][DB] {date_str}: day={day}, "
                        f"boost offset={offset} → {coef}x (mid_cd={mid_cd})"
                    )
                    return coef

                # 2. 감소 구간 — 전 카테고리
                if decline_days and day in decline_days:
                    logger.debug(
                        f"[급여일][A-3][DB] {date_str}: day={day}, "
                        f"decline → {self.MONTH_END_COEFFICIENT}x"
                    )
                    return self.MONTH_END_COEFFICIENT

                return 1.0

            # ── 폴백: 하드코딩 (DB 데이터 없을 때) ──
            logger.debug(f"[급여일][A-3] DB 데이터 없음 — 하드코딩 폴백 ({date_str})")

            if mid_cd in self.PAYDAY_BOOST_CATEGORIES:
                for payday in (10, 25):
                    offset = day - payday
                    if offset in self.PAYDAY_OFFSET_COEFFICIENTS:
                        coef = self.PAYDAY_OFFSET_COEFFICIENTS[offset]
                        logger.debug(
                            f"[급여일][A-3][폴백] {date_str}: day={day}, "
                            f"payday={payday}, offset=+{offset} → {coef}x"
                        )
                        return coef

            if day >= self.MONTH_END_START_DAY:
                return self.MONTH_END_COEFFICIENT

            return 1.0

        except Exception as e:
            logger.debug(f"급여일 계수 조회 실패 (날짜: {date_str}): {e}")
            return 1.0

    def _load_payday_windows(
        self, date_str: str
    ) -> Tuple[Optional[List[int]], Optional[List[int]]]:
        """external_factors 에서 분석된 boost/decline 날짜 목록 로드

        Returns:
            (boost_days, decline_days) — DB 데이터 없으면 (None, None)
        """
        try:
            import json as _json
            target = datetime.strptime(date_str, "%Y-%m-%d")
            # 분석 결과는 해당 월의 1일로 저장
            month_1st = target.replace(day=1).strftime("%Y-%m-%d")

            repo = ExternalFactorRepository()
            factors = repo.get_factors(month_1st, factor_type='payday', store_id=self.store_id)
            if not factors:
                return None, None

            fmap = {f['factor_key']: f['factor_value'] for f in factors}
            boost_raw = fmap.get('boost_days')
            decline_raw = fmap.get('decline_days')

            if not boost_raw:
                return None, None

            boost_days = _json.loads(boost_raw)
            decline_days = _json.loads(decline_raw) if decline_raw else []
            return boost_days, decline_days

        except Exception as e:
            logger.debug(f"payday DB 조회 실패: {e}")
            return None, None

    def _calc_window_offset(self, day: int, boost_days: List[int]) -> int:
        """boost_days 구간 내에서 현재 day 의 연속 오프셋 계산

        예) boost_days=[10, 11, 12, 25, 26]
            day=10 → offset=0 (구간 첫날)
            day=11 → offset=1
            day=25 → offset=0 (새 구간 첫날)
        """
        sorted_days = sorted(boost_days)
        idx = sorted_days.index(day) if day in sorted_days else -1
        if idx < 0:
            return 0

        # 이전 날짜와 연속인지 확인하면서 구간 시작점 역추적
        start_idx = idx
        while start_idx > 0 and sorted_days[start_idx] - sorted_days[start_idx - 1] == 1:
            start_idx -= 1

        return idx - start_idx

    # =========================================================================
    # 미세먼지 관련 (Phase A-4)
    # =========================================================================

    def get_dust_data_for_date(self, date_str: str) -> Dict[str, str]:
        """external_factors에서 미세먼지 예보 등급 조회

        Returns:
            {"dust_grade": "보통"|"나쁨"|..., "fine_dust_grade": "나쁨"|...}
        """
        result = {"dust_grade": "", "fine_dust_grade": ""}
        try:
            repo = ExternalFactorRepository()
            factors = repo.get_factors(date_str, factor_type='weather', store_id=self.store_id)
            if not factors:
                return result
            factor_map = {f['factor_key']: f['factor_value'] for f in factors}
            result["dust_grade"] = factor_map.get("dust_grade_forecast", "")
            result["fine_dust_grade"] = factor_map.get("fine_dust_grade_forecast", "")
            return result
        except Exception as e:
            logger.debug(f"미세먼지 데이터 조회 실패 ({date_str}): {e}")
            return result

    def get_dust_coefficient(self, date_str: str, mid_cd: str) -> float:
        """미세먼지 예보 기반 수요 감소 계수

        판정: dust_grade, fine_dust_grade 중 높은 점수 기준
        - 점수 < 4 (좋음/보통/한때나쁨): 1.0 (영향 없음)
        - 점수 4 (나쁨): 카테고리별 감소
        - 점수 5 (매우나쁨): 카테고리별 큰 감소
        """
        from src.settings.constants import (
            DUST_PREDICTION_ENABLED, DUST_GRADE_SCORE,
            DUST_COEFFICIENTS, DUST_CATEGORY_MAP,
        )

        if not DUST_PREDICTION_ENABLED:
            return 1.0

        try:
            dust_data = self.get_dust_data_for_date(date_str)
            dust_grade = dust_data.get("dust_grade", "")
            fine_dust_grade = dust_data.get("fine_dust_grade", "")

            if not dust_grade and not fine_dust_grade:
                return 1.0

            # 둘 중 더 나쁜 등급의 점수
            score = max(
                DUST_GRADE_SCORE.get(dust_grade, 0),
                DUST_GRADE_SCORE.get(fine_dust_grade, 0),
            )

            if score < 4:  # 좋음/보통/한때나쁨
                return 1.0

            # 등급 결정
            level = "very_bad" if score >= 5 else "bad"

            # 카테고리 매핑
            cat_key = "default"
            for key, mids in DUST_CATEGORY_MAP.items():
                if mid_cd in mids:
                    cat_key = key
                    break

            coef = DUST_COEFFICIENTS.get(level, {}).get(cat_key, 1.0)

            if coef != 1.0:
                logger.debug(
                    f"[PRED][Dust] {date_str} mid={mid_cd}: "
                    f"dust={dust_grade} fine={fine_dust_grade} "
                    f"score={score} → {coef:.2f}x"
                )

            return coef

        except Exception as e:
            logger.debug(f"미세먼지 계수 계산 실패 ({date_str}): {e}")
            return 1.0

    # =========================================================================
    # 강수 관련
    # =========================================================================

    def get_sky_condition_coefficient(self, date_str: str) -> float:
        """날씨 유형(weather_cd_nm) 기반 외출 감소 계수 반환 (Phase A-2)

        external_factors 테이블의 weather_cd_nm 값을 읽어
        SKY_CONDITION_COEFFICIENTS에 매핑한다.

        - 강수 계수(rain_rate/rain_qty)와 독립적인 별도 신호
        - 흐림·안개·황사는 비가 없어도 외출을 줄여 편의점 방문↓
        - 값이 없거나 알 수 없는 날씨 유형 → 안전하게 1.0 반환
        """
        from src.settings.constants import SKY_CONDITION_ENABLED

        if not SKY_CONDITION_ENABLED:
            return 1.0

        try:
            repo = ExternalFactorRepository()
            factors = repo.get_factors(date_str, factor_type='weather', store_id=self.store_id)
            if not factors:
                return 1.0

            factor_map = {f['factor_key']: f['factor_value'] for f in factors}
            sky_nm = factor_map.get('weather_cd_nm_forecast', '')
            if not sky_nm:
                return 1.0

            coef = self.SKY_CONDITION_COEFFICIENTS.get(sky_nm, 1.0)
            if coef != 1.0:
                logger.debug(
                    f"[하늘상태][A-2] {date_str}: weather_cd_nm='{sky_nm}' → {coef}x"
                )
            return coef
        except Exception as e:
            logger.debug(f"하늘상태 계수 조회 실패 (날짜: {date_str}): {e}")
            return 1.0

    def get_precipitation_for_date(self, date_str: str) -> Dict:
        """외부 요인 테이블에서 강수 예보 데이터 조회

        Returns:
            {"rain_rate": float|None, "rain_qty": float|None, "is_snow": bool}
        """
        result = {"rain_rate": None, "rain_qty": None, "is_snow": False}
        try:
            repo = ExternalFactorRepository()
            factors = repo.get_factors(date_str, factor_type='weather', store_id=self.store_id)
            if not factors:
                return result
            factor_map = {f['factor_key']: f['factor_value'] for f in factors}

            rain_rate_str = factor_map.get('rain_rate_forecast')
            if rain_rate_str is not None:
                try:
                    result["rain_rate"] = float(rain_rate_str)
                except (ValueError, TypeError):
                    pass

            rain_qty_str = factor_map.get('rain_qty_forecast')
            if rain_qty_str is not None:
                try:
                    result["rain_qty"] = float(rain_qty_str)
                except (ValueError, TypeError):
                    pass

            result["is_snow"] = factor_map.get('is_snow_forecast') == '1'

            return result
        except Exception as e:
            logger.debug(f"강수 데이터 조회 실패 (날짜: {date_str}): {e}")
            return result

    @staticmethod
    def _get_rain_qty_level(rain_qty: Optional[float]) -> str:
        """강수량(mm)을 등급으로 변환 (Phase A-1)

        Returns:
            "none" | "drizzle" | "light" | "moderate" | "heavy"
        """
        if rain_qty is None or rain_qty <= 0:
            return "none"
        if rain_qty < 2:
            return "drizzle"
        if rain_qty < 5:
            return "light"
        if rain_qty < 15:
            return "moderate"
        return "heavy"

    def get_precipitation_coefficient(self, date_str: str, mid_cd: str) -> float:
        """강수 예보 기반 수요 조정 계수 반환

        Phase A-1: rain_qty 구간 세분화 (RAIN_QTY_INTENSITY_ENABLED 토글)
        - 기존: rain_qty ≥ 10mm → boolean (heavy 판정)
        - 개선: 4단계 구간별 차등 계수 (drizzle/light/moderate/heavy)

        우선순위: 눈 > rain_rate 등급 + rain_qty 등급(상향만) > 1.0
        """
        from src.settings.constants import RAIN_QTY_INTENSITY_ENABLED

        try:
            precip = self.get_precipitation_for_date(date_str)
            rain_rate = precip.get("rain_rate")
            rain_qty = precip.get("rain_qty")
            is_snow = precip.get("is_snow", False)

            if rain_rate is None:
                return 1.0

            # 강수확률 30% 미만이면 영향 없음
            if rain_rate < 30:
                return 1.0

            # ── rain_rate 기반 등급 결정 ──
            if is_snow:
                rate_level = "snow"
            elif rain_rate >= 80:
                rate_level = "heavy"
            elif rain_rate >= 60:
                rate_level = "moderate"
            else:  # 30~60%
                rate_level = "light"

            # ── Phase A-1: rain_qty 등급으로 상향 조정 ──
            if RAIN_QTY_INTENSITY_ENABLED and rate_level != "snow":
                qty_level = self._get_rain_qty_level(rain_qty)
                rate_order = self._RAIN_LEVEL_ORDER.get(rate_level, 0)
                qty_order = self._RAIN_LEVEL_ORDER.get(qty_level, 0)
                if qty_order > rate_order:
                    logger.debug(
                        f"[강수A1] rain_qty 상향: {rate_level}→{qty_level} "
                        f"(qty={rain_qty}mm)"
                    )
                    rate_level = qty_level
            else:
                qty_level = "none"
                # 기존 호환: rain_qty ≥ 10mm → heavy 승격
                if not RAIN_QTY_INTENSITY_ENABLED and rate_level != "snow":
                    if rain_qty is not None and rain_qty >= 10:
                        rate_level = "heavy"

            level = rate_level

            # ── 카테고리에 맞는 계수 찾기 ──
            coef = 1.0

            # 기본 감소 계수 (푸드/빵)
            rule_key = f"{level}_rain" if level != "snow" else "snow"
            rule = self.PRECIPITATION_COEFFICIENTS.get(rule_key)
            if rule and mid_cd in rule["categories"]:
                coef = rule["coefficient"]

            # 부스트 계수 (라면/핫푸드)
            boost_key = f"{level}_rain_boost" if level != "snow" else "snow_boost"
            boost_rule = self.PRECIPITATION_COEFFICIENTS.get(boost_key)
            if boost_rule and mid_cd in boost_rule["categories"]:
                coef = boost_rule["coefficient"]

            # Phase A-1: 비푸드/비라면 카테고리에 rain_qty 기반 범용 계수 적용
            if RAIN_QTY_INTENSITY_ENABLED and coef == 1.0 and level != "snow":
                qty_coef = self.RAIN_QTY_BASE_COEFFICIENTS.get(qty_level, 1.0)
                if qty_coef < 1.0:
                    coef = qty_coef
                    logger.debug(
                        f"[강수A1-범용] {date_str}: mid_cd={mid_cd}, "
                        f"qty_level={qty_level} → {coef}x"
                    )

            if coef != 1.0:
                logger.debug(
                    f"[강수계수] {date_str}: level={level}, rate={rain_rate}%, "
                    f"qty={rain_qty}mm, snow={is_snow}, mid_cd={mid_cd} → {coef}x"
                )

            return coef
        except Exception as e:
            logger.debug(f"강수 계수 조회 실패 (날짜: {date_str}): {e}")
            return 1.0

    # =========================================================================
    # 통합 계수 적용
    # =========================================================================

    def apply(self, base_prediction, item_cd, product, target_date,
              sqlite_weekday, feat_result,
              demand_pattern_cache, food_weekday_cache,
              association_adjuster, db_path):
        """연휴/기온/요일/계절/연관/트렌드/강수 계수 일괄 적용

        Returns:
            (base_prediction, adjusted_prediction, weekday_coef, assoc_boost)
        """
        from src.prediction.demand_classifier import DEMAND_PATTERN_EXEMPT_MIDS

        mid_cd = product["mid_cd"]
        target_date_str = target_date.strftime("%Y-%m-%d")
        _temp = self.get_temperature_for_date(target_date_str)

        # -- 공통: 계수값 계산 --
        holiday_coef = self.get_holiday_coefficient(target_date_str, mid_cd)
        weather_coef = self.get_weather_coefficient(target_date_str, mid_cd)

        # 강수 계수 (기온 계수에 곱하기 병합)
        precip_coef = self.get_precipitation_coefficient(target_date_str, mid_cd)
        weather_coef *= precip_coef

        # Phase A-2: 하늘상태 계수 (weather_cd_nm 활용)
        # 강수 계수와 독립적 신호 — 흐림/안개/황사는 비 없이도 외출↓
        sky_coef = self.get_sky_condition_coefficient(target_date_str)
        if sky_coef != 1.0:
            weather_coef *= sky_coef
            logger.debug(
                f"[PRED][2-Sky] {product.get('item_nm', item_cd)}: "
                f"sky_coef={sky_coef}x → weather_coef={weather_coef:.3f}"
            )

        # Phase A-4: 미세먼지 계수
        dust_coef = self.get_dust_coefficient(target_date_str, mid_cd)
        if dust_coef != 1.0:
            weather_coef *= dust_coef
            logger.debug(
                f"[PRED][2-Dust] {product.get('item_nm', item_cd)}: "
                f"dust_coef={dust_coef}x → weather_coef={weather_coef:.3f}"
            )

        food_wx_coef = 1.0
        food_precip_coef = 1.0
        if is_food_category(mid_cd):
            food_wx_coef = get_food_weather_cross_coefficient(mid_cd, _temp)
            food_precip_coef = get_food_precipitation_cross_coefficient(
                mid_cd, self.get_precipitation_for_date(target_date_str).get("rain_rate")
            )

        weekday_coef = get_weekday_coefficient(mid_cd, sqlite_weekday)
        weekday_source = "static"
        if is_food_category(mid_cd):
            if food_weekday_cache.get(mid_cd) is not None:
                food_wd_coef = food_weekday_cache[mid_cd]
            else:
                food_wd_coef = get_food_weekday_coefficient(
                    mid_cd, sqlite_weekday,
                    store_id=self.store_id, db_path=db_path,
                )
            if food_wd_coef != 1.0:
                weekday_coef = food_wd_coef
                weekday_source = "food-cache" if food_weekday_cache.get(mid_cd) is not None else "food-DB"

        seasonal_coef = get_seasonal_coefficient(mid_cd, target_date.month)

        assoc_boost = 1.0
        if association_adjuster:
            try:
                assoc_boost = association_adjuster.get_association_boost(item_cd, mid_cd)
            except Exception as e:
                logger.debug(f"[연관분석] 부스트 실패 ({item_cd}): {e}")

        _trend_adjustment = 1.0
        if feat_result:
            try:
                _trend_adjustment = feat_result.trend_adjustment
            except Exception:
                pass

        # -- Phase A-3: 급여일 계수 (후처리로 양쪽 경로에 공통 적용) --
        payday_coef = self.get_payday_coefficient(target_date_str, mid_cd)

        # -- 분기: 덧셈 vs 곱셈 --
        pattern_result = demand_pattern_cache.get(item_cd)
        use_additive = (
            demand_pattern_cache
            and mid_cd not in DEMAND_PATTERN_EXEMPT_MIDS
            and pattern_result is not None
        )

        if use_additive:
            result = self._apply_additive(
                base_prediction, item_cd, product, target_date_str,
                pattern_result, _temp,
                holiday_coef, weather_coef, food_wx_coef, food_precip_coef,
                weekday_coef, weekday_source, seasonal_coef,
                assoc_boost, _trend_adjustment, feat_result,
            )
        else:
            # -- 기존 곱셈 파이프라인 (food/dessert/미분류) --
            result = self._apply_multiplicative(
                base_prediction, item_cd, product, target_date, target_date_str,
                _temp, holiday_coef, weather_coef, food_wx_coef, food_precip_coef,
                weekday_coef, weekday_source, seasonal_coef,
                assoc_boost, _trend_adjustment, feat_result, sqlite_weekday,
            )

        # -- Phase A-3: 급여일 계수 후처리 적용 --
        if payday_coef != 1.0:
            bp, adj_pred, wd_coef, ab = result
            before_payday = adj_pred
            adj_pred *= payday_coef
            logger.info(
                f"[PRED][2-Payday] {product.get('item_nm', item_cd)}: "
                f"{before_payday:.2f} × {payday_coef} → {adj_pred:.2f} "
                f"(mid_cd={mid_cd})"
            )
            return bp, adj_pred, wd_coef, ab

        return result

    def _apply_multiplicative(
        self, base_prediction, item_cd, product, target_date, target_date_str,
        temp, holiday_coef, weather_coef, food_wx_coef, food_precip_coef,
        weekday_coef, weekday_source, seasonal_coef,
        assoc_boost, trend_adjustment, feat_result, sqlite_weekday,
    ):
        """기존 곱셈 파이프라인 (food/dessert/미분류)"""
        if holiday_coef != 1.0:
            base_prediction *= holiday_coef
            logger.info(f"[PRED][2-Holiday] {product.get('item_nm', item_cd)}: {holiday_coef}x (date={target_date_str})")

        if weather_coef != 1.0:
            base_prediction *= weather_coef
            logger.info(
                f"[PRED][2-Weather] {product.get('item_nm', item_cd)}: "
                f"{weather_coef:.2f}x (temp={temp})"
            )

        if food_wx_coef != 1.0:
            base_prediction *= food_wx_coef
            logger.info(
                f"[PRED][2-FoodWx] {product.get('item_nm', item_cd)}: "
                f"temp={temp}, {food_wx_coef:.2f}x"
            )

        if food_precip_coef != 1.0:
            base_prediction *= food_precip_coef
            logger.info(
                f"[PRED][2-Precip] {product.get('item_nm', item_cd)}: "
                f"{food_precip_coef:.2f}x (mid_cd={product.get('mid_cd')})"
            )

        adjusted_prediction = base_prediction * weekday_coef
        if weekday_coef != 1.0:
            logger.info(
                f"[PRED][2-Weekday] {product.get('item_nm', item_cd)}: "
                f"{weekday_coef:.2f}x (day={sqlite_weekday}, src={weekday_source})"
            )

        if seasonal_coef != 1.0:
            before_seasonal = adjusted_prediction
            adjusted_prediction *= seasonal_coef
            logger.info(
                f"[PRED][2-Season] {product.get('item_nm', item_cd)}: "
                f"{before_seasonal:.2f} x {seasonal_coef} "
                f"(month={target_date.month}) -> {adjusted_prediction:.2f}"
            )

        if assoc_boost > 1.0:
            before_assoc = adjusted_prediction
            adjusted_prediction *= assoc_boost
            logger.info(
                f"[연관분석] {product.get('item_nm', item_cd)}: "
                f"{before_assoc:.2f} x {assoc_boost:.3f} -> {adjusted_prediction:.2f}"
            )

        if trend_adjustment != 1.0:
            before_trend = adjusted_prediction
            adjusted_prediction *= trend_adjustment
            _trend_direction = getattr(feat_result, 'trend_direction', 'stable') if feat_result else 'stable'
            logger.info(
                f"[PRED][2-Trend] {product.get('item_nm', item_cd)}: "
                f"{before_trend:.2f} x {trend_adjustment} "
                f"({_trend_direction}) -> {adjusted_prediction:.2f}"
            )

        # 복합 계수 바닥값
        compound_floor = base_prediction * 0.15
        if adjusted_prediction < compound_floor:
            logger.warning(
                f"[PRED][2-Floor] {product.get('item_nm', item_cd)}: "
                f"{adjusted_prediction:.2f} < floor {compound_floor:.2f}, "
                f"clamped to {compound_floor:.2f}"
            )
            adjusted_prediction = compound_floor

        return base_prediction, adjusted_prediction, weekday_coef, assoc_boost

    def _apply_additive(
        self, base_prediction, item_cd, product, target_date_str,
        pattern_result, temp,
        holiday_coef, weather_coef, food_wx_coef, food_precip_coef,
        weekday_coef, weekday_source, seasonal_coef,
        assoc_boost, trend_adjustment, feat_result,
    ):
        """덧셈 기반 계수 통합 (prediction-redesign)"""
        from src.prediction.additive_adjuster import AdditiveAdjuster

        pattern_name = pattern_result.pattern.value if hasattr(pattern_result.pattern, 'value') else str(pattern_result.pattern)
        adjuster = AdditiveAdjuster(pattern=pattern_name)

        # food_precip_coef를 food_weather_cross_coef에 병합하여 전달
        combined_food_wx_coef = food_wx_coef * food_precip_coef

        result = adjuster.apply(
            base_prediction=base_prediction,
            holiday_coef=holiday_coef,
            weather_coef=weather_coef,
            food_weather_cross_coef=combined_food_wx_coef,
            weekday_coef=weekday_coef,
            seasonal_coef=seasonal_coef,
            association_boost=assoc_boost,
            trend_adjustment=trend_adjustment,
        )

        adjusted_prediction = result.adjusted_prediction

        if result.clamped_delta != result.delta_sum:
            logger.info(
                f"[PRED][2-Additive] {product.get('item_nm', item_cd)}: "
                f"base={base_prediction:.2f}, pattern={pattern_name}, "
                f"delta_sum={result.delta_sum:+.3f} -> clamped={result.clamped_delta:+.3f}, "
                f"mult={result.multiplier:.3f}, adj={adjusted_prediction:.2f}"
            )
        elif abs(result.delta_sum) > 0.01:
            logger.info(
                f"[PRED][2-Additive] {product.get('item_nm', item_cd)}: "
                f"base={base_prediction:.2f}, pattern={pattern_name}, "
                f"delta={result.delta_sum:+.3f}, adj={adjusted_prediction:.2f}"
            )

        return base_prediction, adjusted_prediction, weekday_coef, assoc_boost
