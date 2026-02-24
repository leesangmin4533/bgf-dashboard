"""
종합 연휴 모듈 테스트

- holidays 라이브러리 통합 (calendar_collector)
- 연속 연휴 기간 인식
- 연휴 맥락 정보
- 차등 계수 계산
- WMA 연휴 보정
- ML Feature 확장
"""

import pytest
import numpy as np
from datetime import datetime
from unittest.mock import patch, MagicMock


# =============================================================================
# 1. calendar_collector: holidays 라이브러리 통합
# =============================================================================

class TestGetKoreanHolidays:
    """holidays 라이브러리 기반 공휴일 자동 생성"""

    def test_2026_seollal_correct_dates(self):
        """2026년 설날이 2/16~18로 정확한지 확인"""
        from src.collectors.calendar_collector import get_korean_holidays

        h = get_korean_holidays(2026)
        # 설날(음력 1/1) = 양력 2/17
        assert "2026-02-17" in h
        # 설날 전후
        assert "2026-02-16" in h
        assert "2026-02-18" in h
        # 이전 오류 날짜가 아닌지 확인
        assert "2026-01-28" not in h
        assert "2026-01-29" not in h

    def test_2026_chuseok_dates(self):
        """2026년 추석이 9/24~26인지 확인"""
        from src.collectors.calendar_collector import get_korean_holidays

        h = get_korean_holidays(2026)
        assert "2026-09-25" in h  # 추석 본날
        assert "2026-09-24" in h
        assert "2026-09-26" in h

    def test_substitute_holidays_included(self):
        """대체공휴일이 포함되는지 확인"""
        from src.collectors.calendar_collector import get_korean_holidays

        h = get_korean_holidays(2026)
        # 2026년 삼일절(3/1 일) → 3/2 대체공휴일
        assert "2026-03-02" in h

    def test_backward_compatible_KOREAN_HOLIDAYS(self):
        """KOREAN_HOLIDAYS 딕셔너리가 하위 호환되는지"""
        from src.collectors.calendar_collector import KOREAN_HOLIDAYS

        assert isinstance(KOREAN_HOLIDAYS, dict)
        # 2025, 2026 모두 포함
        assert "2025-01-01" in KOREAN_HOLIDAYS
        assert "2026-01-01" in KOREAN_HOLIDAYS


class TestIsHoliday:
    """is_holiday() 하위 호환"""

    def test_seollal_is_holiday(self):
        from src.collectors.calendar_collector import is_holiday

        assert is_holiday("2026-02-17") is True  # 설날
        assert is_holiday("2026-02-16") is True  # 설날 연휴
        assert is_holiday("2026-02-18") is True  # 설날 연휴

    def test_normal_day_not_holiday(self):
        from src.collectors.calendar_collector import is_holiday

        assert is_holiday("2026-02-19") is False  # 목요일, 평일

    def test_old_incorrect_dates_not_holiday(self):
        """이전 잘못된 날짜가 공휴일이 아닌지"""
        from src.collectors.calendar_collector import is_holiday

        # 2026-01-28~30은 2026년에는 공휴일이 아님
        assert is_holiday("2026-01-28") is False
        assert is_holiday("2026-01-29") is False


# =============================================================================
# 2. 연속 연휴 기간 인식
# =============================================================================

class TestHolidayPeriods:
    """연속 연휴 기간 인식"""

    def test_seollal_period_2026(self):
        """2026 설날 연휴: 2/14(토)~2/18(수) 5일 연속"""
        from src.collectors.calendar_collector import get_holiday_periods

        periods = get_holiday_periods(2026)
        # 설날 관련 기간 찾기
        seollal = None
        for p in periods:
            if "2026-02-17" in p["dates"]:
                seollal = p
                break

        assert seollal is not None
        assert seollal["days"] >= 4  # 최소 4일 (주말+공휴일 연결)
        assert "2026-02-16" in seollal["dates"]
        assert "2026-02-17" in seollal["dates"]
        assert "2026-02-18" in seollal["dates"]

    def test_chuseok_period_2026(self):
        """2026 추석 연휴 기간 감지"""
        from src.collectors.calendar_collector import get_holiday_periods

        periods = get_holiday_periods(2026)
        chuseok = None
        for p in periods:
            if "2026-09-25" in p["dates"]:
                chuseok = p
                break

        assert chuseok is not None
        assert chuseok["days"] >= 3

    def test_periods_have_required_keys(self):
        """연휴 기간 딕셔너리에 필수 키가 있는지"""
        from src.collectors.calendar_collector import get_holiday_periods

        periods = get_holiday_periods(2026)
        assert len(periods) > 0

        for p in periods:
            assert "start" in p
            assert "end" in p
            assert "days" in p
            assert "name" in p
            assert "dates" in p
            assert p["days"] == len(p["dates"])

    def test_caching_works(self):
        """lru_cache가 동작하는지"""
        from src.collectors.calendar_collector import get_holiday_periods

        p1 = get_holiday_periods(2026)
        p2 = get_holiday_periods(2026)
        assert p1 is p2  # 같은 객체 (캐시)


# =============================================================================
# 3. 연휴 맥락 정보
# =============================================================================

class TestHolidayContext:
    """get_holiday_context() 연휴 맥락 정보"""

    def test_seollal_day(self):
        """설날 당일 맥락"""
        from src.collectors.calendar_collector import get_holiday_context

        ctx = get_holiday_context("2026-02-17")
        assert ctx["is_holiday"] is True
        assert ctx["in_period"] is True
        assert ctx["period_days"] >= 4
        assert ctx["position"] > 0
        assert ctx["is_pre_holiday"] is False
        assert ctx["is_post_holiday"] is False

    def test_pre_holiday(self):
        """연휴 전날 감지"""
        from src.collectors.calendar_collector import get_holiday_context

        ctx = get_holiday_context("2026-02-13")
        assert ctx["is_pre_holiday"] is True
        assert ctx["in_period"] is False
        assert ctx["period_days"] > 0

    def test_post_holiday(self):
        """연휴 후 첫 영업일 감지"""
        from src.collectors.calendar_collector import get_holiday_context

        ctx = get_holiday_context("2026-02-19")
        assert ctx["is_post_holiday"] is True
        assert ctx["in_period"] is False

    def test_normal_day(self):
        """평일은 모든 연휴 플래그가 False"""
        from src.collectors.calendar_collector import get_holiday_context

        ctx = get_holiday_context("2026-03-10")
        assert ctx["is_holiday"] is False
        assert ctx["in_period"] is False
        assert ctx["is_pre_holiday"] is False
        assert ctx["is_post_holiday"] is False
        assert ctx["period_days"] == 0

    def test_weekend_in_period(self):
        """연휴 기간 내 주말도 in_period=True"""
        from src.collectors.calendar_collector import get_holiday_context

        # 2/14(토)는 연휴 기간 내
        ctx = get_holiday_context("2026-02-14")
        assert ctx["in_period"] is True


# =============================================================================
# 4. 차등 계수
# =============================================================================

class TestHolidayCoefficient:
    """improved_predictor._get_holiday_coefficient() 차등 계수"""

    def _make_predictor(self):
        """테스트용 predictor 생성 (DB 없이)"""
        from src.prediction.improved_predictor import ImprovedPredictor
        return ImprovedPredictor.__new__(ImprovedPredictor)

    @patch("src.prediction.improved_predictor.ExternalFactorRepository")
    def test_seollal_first_day_food(self, mock_repo_cls):
        """설날 첫날 + 식품 카테고리 → 1.0보다 큰 계수"""
        mock_repo_cls.return_value.get_factors.return_value = []
        predictor = self._make_predictor()

        coef = predictor._get_holiday_coefficient("2026-02-14", "001")  # 도시락
        # 연휴 기간 시작(첫날) + 식품 → 1.0보다 커야 함
        assert coef > 1.0

    @patch("src.prediction.improved_predictor.ExternalFactorRepository")
    def test_normal_day_returns_1(self, mock_repo_cls):
        """평일은 1.0 반환"""
        mock_repo_cls.return_value.get_factors.return_value = []
        predictor = self._make_predictor()

        coef = predictor._get_holiday_coefficient("2026-03-10", "001")
        assert coef == 1.0

    @patch("src.prediction.improved_predictor.ExternalFactorRepository")
    def test_post_holiday_less_than_1(self, mock_repo_cls):
        """연휴 후 첫 영업일은 1.0 미만 가능 (0.90 * cat)"""
        mock_repo_cls.return_value.get_factors.return_value = []
        predictor = self._make_predictor()

        # 일반 카테고리 (cat_mult=1.0) → 0.90
        coef = predictor._get_holiday_coefficient("2026-02-19", "072")  # 담배
        assert coef < 1.0

    @patch("src.prediction.improved_predictor.ExternalFactorRepository")
    def test_alcohol_gets_higher_coefficient(self, mock_repo_cls):
        """주류 카테고리는 식품보다 높은 계수"""
        mock_repo_cls.return_value.get_factors.return_value = []
        predictor = self._make_predictor()

        coef_food = predictor._get_holiday_coefficient("2026-02-17", "001")
        coef_alcohol = predictor._get_holiday_coefficient("2026-02-17", "049")
        assert coef_alcohol > coef_food

    @patch("src.prediction.improved_predictor.ExternalFactorRepository")
    def test_disabled_returns_1(self, mock_repo_cls):
        """holiday.enabled=False이면 항상 1.0"""
        mock_repo_cls.return_value.get_factors.return_value = []
        predictor = self._make_predictor()

        from src.prediction.prediction_config import PREDICTION_PARAMS
        original = PREDICTION_PARAMS["holiday"]["enabled"]
        PREDICTION_PARAMS["holiday"]["enabled"] = False
        try:
            coef = predictor._get_holiday_coefficient("2026-02-17", "001")
            assert coef == 1.0
        finally:
            PREDICTION_PARAMS["holiday"]["enabled"] = original


# =============================================================================
# 5. ML Feature 확장
# =============================================================================

class TestMLFeatureExtension:
    """ML Feature 30개 (기존 27 + 연휴 맥락 3)"""

    def test_feature_names_count_36(self):
        """FEATURE_NAMES가 36개 (31+입고패턴5)"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        assert len(MLFeatureBuilder.FEATURE_NAMES) == 36

    def test_new_features_in_names(self):
        """새 연휴 feature가 FEATURE_NAMES에 포함"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        names = MLFeatureBuilder.FEATURE_NAMES
        assert "holiday_period_days" in names
        assert "is_pre_holiday" in names
        assert "is_post_holiday" in names

    def test_build_features_returns_36(self):
        """build_features()가 36개 feature 배열 반환 (31+입고패턴5)"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        daily_sales = [
            {"sales_date": f"2026-02-{10+i:02d}", "sale_qty": 5}
            for i in range(14)
        ]
        features = MLFeatureBuilder.build_features(
            daily_sales=daily_sales,
            target_date="2026-02-17",
            mid_cd="001",
            is_holiday=True,
            holiday_period_days=5,
            is_pre_holiday=False,
            is_post_holiday=False,
        )
        assert features is not None
        assert features.shape == (36,)

    def test_holiday_period_days_normalized(self):
        """holiday_period_days가 /7.0으로 정규화"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        daily_sales = [
            {"sales_date": f"2026-02-{10+i:02d}", "sale_qty": 5}
            for i in range(14)
        ]
        features = MLFeatureBuilder.build_features(
            daily_sales=daily_sales,
            target_date="2026-02-17",
            mid_cd="001",
            holiday_period_days=7,
        )
        # holiday_period_days 인덱스 = 27 (0-based)
        idx = MLFeatureBuilder.FEATURE_NAMES.index("holiday_period_days")
        assert abs(features[idx] - 1.0) < 0.01  # 7/7 = 1.0

    def test_pre_post_holiday_flags(self):
        """pre/post holiday 플래그 테스트"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        daily_sales = [
            {"sales_date": f"2026-02-{10+i:02d}", "sale_qty": 5}
            for i in range(14)
        ]
        features = MLFeatureBuilder.build_features(
            daily_sales=daily_sales,
            target_date="2026-02-13",
            mid_cd="001",
            is_pre_holiday=True,
            is_post_holiday=False,
        )
        idx_pre = MLFeatureBuilder.FEATURE_NAMES.index("is_pre_holiday")
        idx_post = MLFeatureBuilder.FEATURE_NAMES.index("is_post_holiday")
        assert features[idx_pre] == 1.0
        assert features[idx_post] == 0.0


# =============================================================================
# 6. save_calendar_info / get_calendar_info
# =============================================================================

class TestCalendarInfo:
    """get_calendar_info()에 연휴 맥락 포함 확인"""

    def test_seollal_calendar_info(self):
        from src.collectors.calendar_collector import get_calendar_info

        info = get_calendar_info("2026-02-17")
        assert info["is_holiday"] is True
        assert info["in_period"] is True
        assert info["period_days"] >= 4
        assert info["position"] > 0

    def test_normal_day_calendar_info(self):
        from src.collectors.calendar_collector import get_calendar_info

        info = get_calendar_info("2026-03-10")
        assert info["is_holiday"] is False
        assert info["in_period"] is False
        assert info["period_days"] == 0

    def test_save_calendar_info_calls_repo(self):
        """save_calendar_info가 연휴 맥락도 저장하는지"""
        from src.collectors.calendar_collector import save_calendar_info

        mock_repo = MagicMock()
        save_calendar_info("2026-02-17", mock_repo)

        # save_factor 호출 확인 (기존 6 + 연휴 맥락 4 = 10+ 호출)
        call_keys = [
            call.kwargs.get("factor_key") or call[1].get("factor_key", "")
            for call in mock_repo.save_factor.call_args_list
        ]
        # keyword argument로 전달되므로
        keys_saved = set()
        for call in mock_repo.save_factor.call_args_list:
            _, kwargs = call
            keys_saved.add(kwargs.get("factor_key", ""))

        assert "is_holiday" in keys_saved
        assert "holiday_period_days" in keys_saved
        assert "holiday_position" in keys_saved
        assert "is_pre_holiday" in keys_saved
        assert "is_post_holiday" in keys_saved
