"""날씨 예보 연동 테스트

테스트 항목:
- _get_temperature_for_date(): 예보 우선, 실측 폴백
- _get_weather_coefficient(): 예보 기온 기반 계수
- ML feature builder에 temperature 전달
- _save_weather_data(): forecast_daily 저장
- parse_forecast_daily(): 원시 데이터 → 날짜별 최고기온
- UPSERT 동작: 같은 날짜 예보 재저장
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def weather_db(tmp_path):
    """날씨 테스트용 DB (external_factors 테이블 포함)"""
    db_file = tmp_path / "test_weather.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE external_factors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            factor_date TEXT NOT NULL,
            factor_type TEXT NOT NULL,
            factor_key TEXT NOT NULL,
            factor_value TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(factor_date, factor_type, factor_key)
        )
    """)
    conn.execute("CREATE INDEX idx_ef_date ON external_factors(factor_date)")
    conn.execute("CREATE INDEX idx_ef_type ON external_factors(factor_type)")

    conn.commit()
    conn.close()
    return str(db_file)


def _insert_factor(db_path, factor_date, factor_type, factor_key, factor_value):
    """테스트 헬퍼: external_factors에 데이터 삽입"""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO external_factors
           (factor_date, factor_type, factor_key, factor_value, created_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(factor_date, factor_type, factor_key) DO UPDATE SET
             factor_value = excluded.factor_value""",
        (factor_date, factor_type, factor_key, str(factor_value),
         datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


# =============================================================
# _get_temperature_for_date 테스트
# =============================================================

class TestGetTemperatureForDate:
    """_get_temperature_for_date() 조회 우선순위 테스트"""

    def test_forecast_preferred_over_actual(self, weather_db):
        """예보 + 실측 모두 있으면 예보 기온 사용"""
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_factor(weather_db, tomorrow, "weather", "temperature", "20")
        _insert_factor(weather_db, tomorrow, "weather", "temperature_forecast", "35")

        with patch(
            "src.infrastructure.database.repos.external_factor_repo."
            "ExternalFactorRepository._get_conn"
        ) as mock_conn:
            conn = sqlite3.connect(weather_db)
            conn.row_factory = sqlite3.Row
            mock_conn.return_value = conn

            from src.prediction.improved_predictor import ImprovedPredictor
            predictor = ImprovedPredictor.__new__(ImprovedPredictor)
            result = predictor._get_temperature_for_date(tomorrow)

        assert result == 35.0

    def test_actual_fallback_when_no_forecast(self, weather_db):
        """예보 없으면 실측 기온 사용"""
        today = datetime.now().strftime("%Y-%m-%d")
        _insert_factor(weather_db, today, "weather", "temperature", "25")

        with patch(
            "src.infrastructure.database.repos.external_factor_repo."
            "ExternalFactorRepository._get_conn"
        ) as mock_conn:
            conn = sqlite3.connect(weather_db)
            conn.row_factory = sqlite3.Row
            mock_conn.return_value = conn

            from src.prediction.improved_predictor import ImprovedPredictor
            predictor = ImprovedPredictor.__new__(ImprovedPredictor)
            result = predictor._get_temperature_for_date(today)

        assert result == 25.0

    def test_none_when_no_data(self, weather_db):
        """데이터 없으면 None"""
        with patch(
            "src.infrastructure.database.repos.external_factor_repo."
            "ExternalFactorRepository._get_conn"
        ) as mock_conn:
            conn = sqlite3.connect(weather_db)
            conn.row_factory = sqlite3.Row
            mock_conn.return_value = conn

            from src.prediction.improved_predictor import ImprovedPredictor
            predictor = ImprovedPredictor.__new__(ImprovedPredictor)
            result = predictor._get_temperature_for_date("2099-12-31")

        assert result is None


# =============================================================
# _get_weather_coefficient 테스트 (예보 기온 활용)
# =============================================================

class TestWeatherCoefficientWithForecast:
    """예보 기온 기반 기온계수 테스트"""

    def test_hot_boost_from_forecast(self, weather_db):
        """예보 35도 → 음료 카테고리 hot_boost 1.15"""
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_factor(weather_db, tomorrow, "weather", "temperature_forecast", "35")

        with patch(
            "src.infrastructure.database.repos.external_factor_repo."
            "ExternalFactorRepository._get_conn"
        ) as mock_conn:
            conn = sqlite3.connect(weather_db)
            conn.row_factory = sqlite3.Row
            mock_conn.return_value = conn

            from src.prediction.improved_predictor import ImprovedPredictor
            predictor = ImprovedPredictor.__new__(ImprovedPredictor)
            coef = predictor._get_weather_coefficient(tomorrow, "010")  # 음료

        assert coef == 1.15

    def test_cold_boost_from_forecast(self, weather_db):
        """예보 -3도 → 즉석식품 cold_boost 1.10"""
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_factor(weather_db, tomorrow, "weather", "temperature_forecast", "-3")

        with patch(
            "src.infrastructure.database.repos.external_factor_repo."
            "ExternalFactorRepository._get_conn"
        ) as mock_conn:
            conn = sqlite3.connect(weather_db)
            conn.row_factory = sqlite3.Row
            mock_conn.return_value = conn

            from src.prediction.improved_predictor import ImprovedPredictor
            predictor = ImprovedPredictor.__new__(ImprovedPredictor)
            coef = predictor._get_weather_coefficient(tomorrow, "006")  # 즉석식품

        assert coef == 1.10

    def test_no_coefficient_for_normal_temp(self, weather_db):
        """예보 20도 → 계수 1.0 (조정 없음)"""
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_factor(weather_db, tomorrow, "weather", "temperature_forecast", "20")

        with patch(
            "src.infrastructure.database.repos.external_factor_repo."
            "ExternalFactorRepository._get_conn"
        ) as mock_conn:
            conn = sqlite3.connect(weather_db)
            conn.row_factory = sqlite3.Row
            mock_conn.return_value = conn

            from src.prediction.improved_predictor import ImprovedPredictor
            predictor = ImprovedPredictor.__new__(ImprovedPredictor)
            coef = predictor._get_weather_coefficient(tomorrow, "010")

        assert coef == 1.0

    def test_no_data_returns_1(self, weather_db):
        """데이터 없으면 계수 1.0"""
        with patch(
            "src.infrastructure.database.repos.external_factor_repo."
            "ExternalFactorRepository._get_conn"
        ) as mock_conn:
            conn = sqlite3.connect(weather_db)
            conn.row_factory = sqlite3.Row
            mock_conn.return_value = conn

            from src.prediction.improved_predictor import ImprovedPredictor
            predictor = ImprovedPredictor.__new__(ImprovedPredictor)
            coef = predictor._get_weather_coefficient("2099-12-31", "010")

        assert coef == 1.0


# =============================================================
# _parse_forecast_daily 테스트
# =============================================================

class TestParseForecastDaily:
    """예보 원시 데이터 → 날짜별 최고기온 변환"""

    def test_basic_parsing(self):
        """정상 데이터에서 날짜별 최고기온 추출"""
        from src.utils.weather_utils import parse_forecast_daily

        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        day_after = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")

        weather_info = {
            "forecast_columns": ["fcst_date", "fcst_time", "temperature"],
            "forecast_raw": [
                {"fcst_date": tomorrow, "fcst_time": "0600", "temperature": "5"},
                {"fcst_date": tomorrow, "fcst_time": "1200", "temperature": "15"},
                {"fcst_date": tomorrow, "fcst_time": "1500", "temperature": "18"},
                {"fcst_date": day_after, "fcst_time": "0600", "temperature": "3"},
                {"fcst_date": day_after, "fcst_time": "1400", "temperature": "12"},
            ]
        }

        result = parse_forecast_daily(weather_info)
        assert result[tomorrow] == 18.0  # 최고기온
        assert result[day_after] == 12.0

    def test_yyyymmdd_date_format(self):
        """YYYYMMDD 형식 날짜 변환"""
        from src.utils.weather_utils import parse_forecast_daily

        tomorrow = datetime.now() + timedelta(days=1)
        tomorrow_str = tomorrow.strftime("%Y-%m-%d")
        tomorrow_compact = tomorrow.strftime("%Y%m%d")

        weather_info = {
            "forecast_columns": ["date", "tmp"],
            "forecast_raw": [
                {"date": tomorrow_compact, "tmp": "30"},
                {"date": tomorrow_compact, "tmp": "25"},
            ]
        }

        result = parse_forecast_daily(weather_info)
        assert result[tomorrow_str] == 30.0

    def test_excludes_today(self):
        """오늘 데이터는 제외 (실측 사용)"""
        from src.utils.weather_utils import parse_forecast_daily

        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        weather_info = {
            "forecast_columns": ["fcst_date", "temp"],
            "forecast_raw": [
                {"fcst_date": today, "temp": "20"},
                {"fcst_date": tomorrow, "temp": "30"},
            ]
        }

        result = parse_forecast_daily(weather_info)
        assert today not in result
        assert result[tomorrow] == 30.0

    def test_empty_raw_returns_empty(self):
        """빈 데이터 → 빈 dict"""
        from src.utils.weather_utils import parse_forecast_daily

        result = parse_forecast_daily({"forecast_raw": []})
        assert result == {}

    def test_no_raw_returns_empty(self):
        """forecast_raw 키 없음 → 빈 dict"""
        from src.utils.weather_utils import parse_forecast_daily

        result = parse_forecast_daily({})
        assert result == {}

    def test_no_temp_column_returns_empty(self):
        """기온 컬럼 못 찾으면 빈 dict"""
        from src.utils.weather_utils import parse_forecast_daily

        weather_info = {
            "forecast_columns": ["date", "unknown_col"],
            "forecast_raw": [
                {"date": "2099-01-01", "unknown_col": "abc"},
            ]
        }

        result = parse_forecast_daily(weather_info)
        assert result == {}


# =============================================================
# _save_weather_data 예보 저장 테스트
# =============================================================

class TestSaveWeatherDataForecast:
    """daily_job._save_weather_data()에서 예보 기온 저장"""

    def test_forecast_daily_saved(self, weather_db):
        """forecast_daily가 있으면 temperature_forecast로 저장"""
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        day_after = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")

        weather = {
            "temperature": 10,
            "weather_type": "sunny",
            "forecast_daily": {
                tomorrow: 25.0,
                day_after: 18.5,
            }
        }

        # ExternalFactorRepository mock
        repo = MagicMock()
        from src.scheduler.daily_job import DailyCollectionJob
        job = DailyCollectionJob.__new__(DailyCollectionJob)
        job.weather_repo = repo

        # logger mock
        with patch("src.scheduler.daily_job.logger"):
            job._save_weather_data("2026-02-18", weather)

        # save_factor 호출 확인
        calls = repo.save_factor.call_args_list
        # temperature + weather_type + forecast(2) = 4 calls
        assert len(calls) == 4

        # forecast 저장 확인
        forecast_calls = [
            c for c in calls
            if c.kwargs.get("factor_key") == "temperature_forecast"
               or (len(c.args) > 2 and c.args[2] == "temperature_forecast")
        ]
        assert len(forecast_calls) == 2

    def test_no_forecast_no_extra_saves(self, weather_db):
        """forecast_daily 없으면 기존 동작 유지"""
        weather = {"temperature": 10, "weather_type": "sunny"}

        repo = MagicMock()
        from src.scheduler.daily_job import DailyCollectionJob
        job = DailyCollectionJob.__new__(DailyCollectionJob)
        job.weather_repo = repo

        with patch("src.scheduler.daily_job.logger"):
            job._save_weather_data("2026-02-18", weather)

        # temperature + weather_type = 2 calls (예보 없음)
        calls = repo.save_factor.call_args_list
        assert len(calls) == 2


# =============================================================
# ExternalFactorRepository UPSERT 테스트
# =============================================================

class TestForecastUpsert:
    """같은 날짜 예보 재저장 시 UPSERT 동작"""

    def test_forecast_upsert(self, weather_db):
        """동일 키에 재저장 시 값 갱신"""
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        _insert_factor(weather_db, tomorrow, "weather", "temperature_forecast", "25")
        _insert_factor(weather_db, tomorrow, "weather", "temperature_forecast", "30")

        conn = sqlite3.connect(weather_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT factor_value FROM external_factors "
            "WHERE factor_date=? AND factor_key='temperature_forecast'",
            (tomorrow,)
        ).fetchall()
        conn.close()

        assert len(rows) == 1  # 중복 없음
        assert rows[0]["factor_value"] == "30"  # 최신값

    def test_forecast_and_actual_coexist(self, weather_db):
        """temperature와 temperature_forecast가 같은 날짜에 공존"""
        date = "2026-07-15"
        _insert_factor(weather_db, date, "weather", "temperature", "20")
        _insert_factor(weather_db, date, "weather", "temperature_forecast", "35")

        conn = sqlite3.connect(weather_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT factor_key, factor_value FROM external_factors "
            "WHERE factor_date=? AND factor_type='weather' ORDER BY factor_key",
            (date,)
        ).fetchall()
        conn.close()

        assert len(rows) == 2
        keys = {r["factor_key"]: r["factor_value"] for r in rows}
        assert keys["temperature"] == "20"
        assert keys["temperature_forecast"] == "35"


# =============================================================
# _get_temperature_delta 테스트
# =============================================================

class TestGetTemperatureDelta:
    """전일 대비 기온 변화량 테스트"""

    def test_delta_positive(self, weather_db):
        """오늘 25도, 내일 35도 → delta +10"""
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_factor(weather_db, today, "weather", "temperature", "25")
        _insert_factor(weather_db, tomorrow, "weather", "temperature_forecast", "35")

        def _make_conn():
            c = sqlite3.connect(weather_db)
            c.row_factory = sqlite3.Row
            return c

        with patch(
            "src.infrastructure.database.repos.external_factor_repo."
            "ExternalFactorRepository._get_conn",
            side_effect=_make_conn,
        ):
            from src.prediction.improved_predictor import ImprovedPredictor
            predictor = ImprovedPredictor.__new__(ImprovedPredictor)
            delta = predictor._get_temperature_delta(tomorrow)

        assert delta == 10.0

    def test_delta_negative(self, weather_db):
        """오늘 25도, 내일 10도 → delta -15"""
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_factor(weather_db, today, "weather", "temperature", "25")
        _insert_factor(weather_db, tomorrow, "weather", "temperature_forecast", "10")

        def _make_conn():
            c = sqlite3.connect(weather_db)
            c.row_factory = sqlite3.Row
            return c

        with patch(
            "src.infrastructure.database.repos.external_factor_repo."
            "ExternalFactorRepository._get_conn",
            side_effect=_make_conn,
        ):
            from src.prediction.improved_predictor import ImprovedPredictor
            predictor = ImprovedPredictor.__new__(ImprovedPredictor)
            delta = predictor._get_temperature_delta(tomorrow)

        assert delta == -15.0

    def test_delta_none_when_prev_missing(self, weather_db):
        """전일 데이터 없으면 None"""
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_factor(weather_db, tomorrow, "weather", "temperature_forecast", "20")

        with patch(
            "src.infrastructure.database.repos.external_factor_repo."
            "ExternalFactorRepository._get_conn"
        ) as mock_conn:
            conn = sqlite3.connect(weather_db)
            conn.row_factory = sqlite3.Row
            mock_conn.return_value = conn

            from src.prediction.improved_predictor import ImprovedPredictor
            predictor = ImprovedPredictor.__new__(ImprovedPredictor)
            delta = predictor._get_temperature_delta(tomorrow)

        assert delta is None

    def test_delta_none_when_target_missing(self, weather_db):
        """당일 데이터 없으면 None"""
        with patch(
            "src.infrastructure.database.repos.external_factor_repo."
            "ExternalFactorRepository._get_conn"
        ) as mock_conn:
            conn = sqlite3.connect(weather_db)
            conn.row_factory = sqlite3.Row
            mock_conn.return_value = conn

            from src.prediction.improved_predictor import ImprovedPredictor
            predictor = ImprovedPredictor.__new__(ImprovedPredictor)
            delta = predictor._get_temperature_delta("2099-12-31")

        assert delta is None


# =============================================================
# 기온 급변 보정 계수 테스트
# =============================================================

class TestWeatherDeltaCoefficient:
    """기온 급변 보정 계수 (전일 대비 10도 이상 변화)"""

    def test_sudden_cold_boosts_warm_food(self, weather_db):
        """15도 급락 → 즉석식품(006) 수요 +10%"""
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_factor(weather_db, today, "weather", "temperature", "20")
        _insert_factor(weather_db, tomorrow, "weather", "temperature_forecast", "5")

        def _make_conn():
            c = sqlite3.connect(weather_db)
            c.row_factory = sqlite3.Row
            return c

        with patch(
            "src.infrastructure.database.repos.external_factor_repo."
            "ExternalFactorRepository._get_conn",
            side_effect=_make_conn,
        ):
            from src.prediction.improved_predictor import ImprovedPredictor
            predictor = ImprovedPredictor.__new__(ImprovedPredictor)
            coef = predictor._get_weather_coefficient(tomorrow, "006")

        # 절대 기온 5도 → cold_boost 1.10 * 급변 sudden_cold 1.10 = 1.21
        assert abs(coef - 1.21) < 0.01

    def test_sudden_cold_reduces_drinks(self, weather_db):
        """15도 급락 → 음료(010) 수요 -10%"""
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_factor(weather_db, today, "weather", "temperature", "25")
        _insert_factor(weather_db, tomorrow, "weather", "temperature_forecast", "10")

        def _make_conn():
            c = sqlite3.connect(weather_db)
            c.row_factory = sqlite3.Row
            return c

        with patch(
            "src.infrastructure.database.repos.external_factor_repo."
            "ExternalFactorRepository._get_conn",
            side_effect=_make_conn,
        ):
            from src.prediction.improved_predictor import ImprovedPredictor
            predictor = ImprovedPredictor.__new__(ImprovedPredictor)
            coef = predictor._get_weather_coefficient(tomorrow, "010")

        # 절대 기온 10도 → 임계값 미충족 (30도↑ 아님) → 1.0
        # 급변 -15도 → sudden_cold_reduce 0.90
        assert abs(coef - 0.90) < 0.01

    def test_sudden_hot_boosts_drinks(self, weather_db):
        """12도 급상승 → 음료(010) 수요 +10%"""
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_factor(weather_db, today, "weather", "temperature", "18")
        _insert_factor(weather_db, tomorrow, "weather", "temperature_forecast", "30")

        def _make_conn():
            c = sqlite3.connect(weather_db)
            c.row_factory = sqlite3.Row
            return c

        with patch(
            "src.infrastructure.database.repos.external_factor_repo."
            "ExternalFactorRepository._get_conn",
            side_effect=_make_conn,
        ):
            from src.prediction.improved_predictor import ImprovedPredictor
            predictor = ImprovedPredictor.__new__(ImprovedPredictor)
            coef = predictor._get_weather_coefficient(tomorrow, "010")

        # 절대 기온 30도 → hot_boost 1.15 * 급변 sudden_hot 1.10 = 1.265
        assert abs(coef - 1.265) < 0.01

    def test_no_delta_when_prev_missing(self, weather_db):
        """전일 데이터 없으면 급변 보정 미적용 (절대 기온만 적용)"""
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_factor(weather_db, tomorrow, "weather", "temperature_forecast", "35")

        def _make_conn():
            c = sqlite3.connect(weather_db)
            c.row_factory = sqlite3.Row
            return c

        with patch(
            "src.infrastructure.database.repos.external_factor_repo."
            "ExternalFactorRepository._get_conn",
            side_effect=_make_conn,
        ):
            from src.prediction.improved_predictor import ImprovedPredictor
            predictor = ImprovedPredictor.__new__(ImprovedPredictor)
            coef = predictor._get_weather_coefficient(tomorrow, "010")

        # 절대 기온 35도 → hot_boost 1.15만 (급변 데이터 없으므로 delta 미적용)
        assert coef == 1.15

    def test_small_delta_no_effect(self, weather_db):
        """5도 변화 → 급변 미충족, 절대 기온만 적용"""
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_factor(weather_db, today, "weather", "temperature", "20")
        _insert_factor(weather_db, tomorrow, "weather", "temperature_forecast", "15")

        def _make_conn():
            c = sqlite3.connect(weather_db)
            c.row_factory = sqlite3.Row
            return c

        with patch(
            "src.infrastructure.database.repos.external_factor_repo."
            "ExternalFactorRepository._get_conn",
            side_effect=_make_conn,
        ):
            from src.prediction.improved_predictor import ImprovedPredictor
            predictor = ImprovedPredictor.__new__(ImprovedPredictor)
            coef = predictor._get_weather_coefficient(tomorrow, "010")

        # 15도 → 절대 임계값 미충족, delta -5 → 급변 미충족 → 1.0
        assert coef == 1.0


# =============================================================
# ML feature: temperature_delta 테스트
# =============================================================

class TestMLFeatureTemperatureDelta:
    """ML feature builder에 temperature_delta 전달 확인"""

    def test_delta_included_in_features(self):
        """temperature_delta가 feature 배열에 포함"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        sales = [{"sales_date": f"2026-02-{d:02d}", "sale_qty": 5} for d in range(1, 16)]
        features = MLFeatureBuilder.build_features(
            daily_sales=sales,
            target_date="2026-02-16",
            mid_cd="010",
            temperature=25.0,
            temperature_delta=-12.0,
        )

        assert features is not None
        # temperature_delta는 FEATURE_NAMES에서 temperature 바로 다음
        idx = MLFeatureBuilder.FEATURE_NAMES.index("temperature_delta")
        # -12.0 / 15.0 = -0.8
        assert abs(features[idx] - (-0.8)) < 0.01

    def test_delta_none_defaults_to_zero(self):
        """temperature_delta=None → 0.0"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        sales = [{"sales_date": f"2026-02-{d:02d}", "sale_qty": 5} for d in range(1, 16)]
        features = MLFeatureBuilder.build_features(
            daily_sales=sales,
            target_date="2026-02-16",
            mid_cd="010",
            temperature=25.0,
            temperature_delta=None,
        )

        assert features is not None
        idx = MLFeatureBuilder.FEATURE_NAMES.index("temperature_delta")
        assert features[idx] == 0.0

    def test_feature_count_matches(self):
        """feature 배열 길이가 FEATURE_NAMES 길이와 일치"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        sales = [{"sales_date": f"2026-02-{d:02d}", "sale_qty": 5} for d in range(1, 16)]
        features = MLFeatureBuilder.build_features(
            daily_sales=sales,
            target_date="2026-02-16",
            mid_cd="010",
            temperature=20.0,
            temperature_delta=5.0,
        )

        assert features is not None
        assert len(features) == len(MLFeatureBuilder.FEATURE_NAMES)
