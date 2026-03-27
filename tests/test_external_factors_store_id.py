"""
external_factors store_id 격리 테스트
v57: 매장별 날씨/급여일 데이터 분리, 캘린더는 공통 유지
"""

import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.infrastructure.database.repos.external_factor_repo import ExternalFactorRepository


@pytest.fixture
def tmp_db(tmp_path):
    """store_id 포함 external_factors 테이블이 있는 임시 DB"""
    db_path = tmp_path / "test_common.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE external_factors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            factor_date TEXT NOT NULL,
            factor_type TEXT NOT NULL,
            factor_key TEXT NOT NULL,
            factor_value TEXT,
            store_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(factor_date, factor_type, factor_key, store_id)
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def repo(tmp_db):
    """임시 DB 사용하는 ExternalFactorRepository"""
    return ExternalFactorRepository(db_path=tmp_db)


# =========================================================================
# save_factor store_id 격리 테스트
# =========================================================================

class TestSaveFactorStoreId:
    """save_factor에 store_id 전달 시 매장별 격리 검증"""

    def test_save_with_store_id(self, repo, tmp_db):
        """store_id 전달 시 해당 매장으로 저장"""
        repo.save_factor("2026-03-10", "weather", "temperature", "15", store_id="46513")

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM external_factors").fetchone()
        conn.close()

        assert row["store_id"] == "46513"
        assert row["factor_value"] == "15"

    def test_save_default_store_id_empty(self, repo, tmp_db):
        """store_id 미전달 시 기본값 '' 저장"""
        repo.save_factor("2026-03-10", "calendar", "is_holiday", "true")

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM external_factors").fetchone()
        conn.close()

        assert row["store_id"] == ""

    def test_different_stores_not_overwritten(self, repo, tmp_db):
        """같은 날짜+키, 다른 store_id → 별도 행 저장 (덮어쓰기 없음)"""
        repo.save_factor("2026-03-10", "weather", "temperature", "15", store_id="46513")
        repo.save_factor("2026-03-10", "weather", "temperature", "12", store_id="46514")
        repo.save_factor("2026-03-10", "weather", "temperature", "18", store_id="46515")

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT store_id, factor_value FROM external_factors ORDER BY store_id"
        ).fetchall()
        conn.close()

        assert len(rows) == 3
        data = {r["store_id"]: r["factor_value"] for r in rows}
        assert data["46513"] == "15"
        assert data["46514"] == "12"
        assert data["46515"] == "18"

    def test_same_store_upsert(self, repo, tmp_db):
        """같은 store_id → UPSERT (값 갱신)"""
        repo.save_factor("2026-03-10", "weather", "temperature", "15", store_id="46513")
        repo.save_factor("2026-03-10", "weather", "temperature", "20", store_id="46513")

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM external_factors").fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0]["factor_value"] == "20"

    def test_calendar_shared_across_stores(self, repo, tmp_db):
        """캘린더 데이터(store_id='')는 1개만 저장"""
        repo.save_factor("2026-03-10", "calendar", "is_holiday", "true")
        repo.save_factor("2026-03-10", "calendar", "is_holiday", "false")

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM external_factors").fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0]["factor_value"] == "false"
        assert rows[0]["store_id"] == ""


# =========================================================================
# get_factors store_id 필터 테스트
# =========================================================================

class TestGetFactorsStoreId:
    """get_factors store_id 필터링 검증"""

    def _seed(self, repo):
        """테스트 데이터 삽입"""
        repo.save_factor("2026-03-10", "weather", "temperature", "15", store_id="46513")
        repo.save_factor("2026-03-10", "weather", "temperature", "12", store_id="46514")
        repo.save_factor("2026-03-10", "weather", "dust_grade_forecast", "나쁨", store_id="46513")
        repo.save_factor("2026-03-10", "calendar", "is_holiday", "false")  # 공통 store_id=''
        repo.save_factor("2026-03-10", "payday", "boost_days", "[25]", store_id="46513")

    def test_get_with_store_id(self, repo):
        """store_id 지정 시 해당 매장 데이터만 반환"""
        self._seed(repo)
        factors = repo.get_factors("2026-03-10", factor_type="weather", store_id="46513")
        keys = {f["factor_key"] for f in factors}
        assert "temperature" in keys
        assert "dust_grade_forecast" in keys
        assert len(factors) == 2

    def test_get_other_store_id(self, repo):
        """다른 store_id → 해당 매장 데이터만"""
        self._seed(repo)
        factors = repo.get_factors("2026-03-10", factor_type="weather", store_id="46514")
        assert len(factors) == 1
        assert factors[0]["factor_value"] == "12"

    def test_get_store_id_none_returns_all(self, repo):
        """store_id=None → 모든 store_id 반환"""
        self._seed(repo)
        factors = repo.get_factors("2026-03-10")
        assert len(factors) == 5  # 전체

    def test_get_store_id_none_with_type(self, repo):
        """store_id=None + factor_type → 해당 타입 전체"""
        self._seed(repo)
        factors = repo.get_factors("2026-03-10", factor_type="weather")
        assert len(factors) == 3  # 46513(temp+dust) + 46514(temp)

    def test_get_calendar_common(self, repo):
        """캘린더(store_id='') 조회"""
        self._seed(repo)
        factors = repo.get_factors("2026-03-10", factor_type="calendar", store_id="")
        assert len(factors) == 1
        assert factors[0]["factor_key"] == "is_holiday"

    def test_get_empty_store_id_no_weather(self, repo):
        """store_id='' → 날씨 데이터 없음 (캘린더만)"""
        self._seed(repo)
        factors = repo.get_factors("2026-03-10", factor_type="weather", store_id="")
        assert len(factors) == 0

    def test_get_payday_with_store_id(self, repo):
        """급여일 데이터 매장별 조회"""
        self._seed(repo)
        factors = repo.get_factors("2026-03-10", factor_type="payday", store_id="46513")
        assert len(factors) == 1
        assert factors[0]["factor_key"] == "boost_days"

    def test_get_payday_wrong_store(self, repo):
        """다른 매장의 급여일 데이터 → 없음"""
        self._seed(repo)
        factors = repo.get_factors("2026-03-10", factor_type="payday", store_id="46514")
        assert len(factors) == 0


# =========================================================================
# 마이그레이션 v57 호환성 테스트
# =========================================================================

class TestMigrationV57Compat:
    """기존 데이터(store_id='')가 마이그레이션 후에도 정상 조회되는지 검증"""

    def test_legacy_data_accessible(self, repo):
        """store_id='' (레거시 데이터) → store_id=None 조회 시 반환"""
        repo.save_factor("2026-03-09", "weather", "temperature", "10")  # store_id=''
        factors = repo.get_factors("2026-03-09", factor_type="weather")
        assert len(factors) == 1
        assert factors[0]["factor_value"] == "10"

    def test_legacy_data_with_empty_store_id(self, repo):
        """store_id='' 명시 조회 → 레거시 데이터 반환"""
        repo.save_factor("2026-03-09", "weather", "temperature", "10")
        factors = repo.get_factors("2026-03-09", factor_type="weather", store_id="")
        assert len(factors) == 1

    def test_legacy_data_not_returned_for_specific_store(self, repo):
        """특정 store_id 조회 시 레거시(store_id='') 데이터 미반환"""
        repo.save_factor("2026-03-09", "weather", "temperature", "10")
        factors = repo.get_factors("2026-03-09", factor_type="weather", store_id="46513")
        assert len(factors) == 0


# =========================================================================
# 통합 시나리오 테스트
# =========================================================================

class TestIntegrationScenarios:
    """3개 매장 운영 시나리오"""

    def test_three_stores_weather(self, repo):
        """3개 매장의 기온 데이터가 독립 저장/조회"""
        stores = {"46513": "15", "46514": "12", "46515": "18"}
        for sid, temp in stores.items():
            repo.save_factor("2026-03-10", "weather", "temperature", temp, store_id=sid)

        for sid, expected_temp in stores.items():
            factors = repo.get_factors("2026-03-10", factor_type="weather", store_id=sid)
            assert len(factors) == 1
            assert factors[0]["factor_value"] == expected_temp

    def test_dust_per_store(self, repo):
        """매장별 미세먼지 등급 독립 저장"""
        repo.save_factor("2026-03-11", "weather", "dust_grade_forecast", "보통", store_id="46513")
        repo.save_factor("2026-03-11", "weather", "dust_grade_forecast", "나쁨", store_id="46514")

        f1 = repo.get_factors("2026-03-11", "weather", store_id="46513")
        f2 = repo.get_factors("2026-03-11", "weather", store_id="46514")

        assert f1[0]["factor_value"] == "보통"
        assert f2[0]["factor_value"] == "나쁨"

    def test_calendar_shared_weather_isolated(self, repo):
        """캘린더는 공유, 날씨는 매장별 격리"""
        # 공통 캘린더
        repo.save_factor("2026-03-10", "calendar", "is_holiday", "false")

        # 매장별 날씨
        repo.save_factor("2026-03-10", "weather", "temperature", "15", store_id="46513")
        repo.save_factor("2026-03-10", "weather", "temperature", "12", store_id="46514")

        # 캘린더는 모든 매장에서 동일하게 조회
        cal = repo.get_factors("2026-03-10", "calendar", store_id="")
        assert len(cal) == 1
        assert cal[0]["factor_value"] == "false"

        # 날씨는 매장별
        w1 = repo.get_factors("2026-03-10", "weather", store_id="46513")
        w2 = repo.get_factors("2026-03-10", "weather", store_id="46514")
        assert w1[0]["factor_value"] == "15"
        assert w2[0]["factor_value"] == "12"

    def test_payday_per_store(self, repo):
        """급여일 데이터 매장별 격리"""
        import json
        repo.save_factor("2026-03-01", "payday", "boost_days", json.dumps([25, 26]), store_id="46513")
        repo.save_factor("2026-03-01", "payday", "boost_days", json.dumps([10, 11]), store_id="46514")

        f1 = repo.get_factors("2026-03-01", "payday", store_id="46513")
        f2 = repo.get_factors("2026-03-01", "payday", store_id="46514")

        assert json.loads(f1[0]["factor_value"]) == [25, 26]
        assert json.loads(f2[0]["factor_value"]) == [10, 11]


# =========================================================================
# CoefficientAdjuster store_id 전달 검증
# =========================================================================

class TestCoefficientAdjusterStoreId:
    """CoefficientAdjuster가 get_factors 호출 시 store_id 전달하는지 검증"""

    @patch("src.prediction.coefficient_adjuster.ExternalFactorRepository")
    def test_temperature_passes_store_id(self, MockRepo):
        """get_temperature_for_date → get_factors(store_id=self.store_id)"""
        mock_repo = MagicMock()
        mock_repo.get_factors.return_value = []
        MockRepo.return_value = mock_repo

        from src.prediction.coefficient_adjuster import CoefficientAdjuster
        adj = CoefficientAdjuster(store_id="46513")
        adj.get_temperature_for_date("2026-03-10")

        mock_repo.get_factors.assert_called_once_with(
            "2026-03-10", factor_type="weather", store_id="46513"
        )

    @patch("src.prediction.coefficient_adjuster.ExternalFactorRepository")
    def test_dust_passes_store_id(self, MockRepo):
        """get_dust_data_for_date → get_factors(store_id=self.store_id)"""
        mock_repo = MagicMock()
        mock_repo.get_factors.return_value = []
        MockRepo.return_value = mock_repo

        from src.prediction.coefficient_adjuster import CoefficientAdjuster
        adj = CoefficientAdjuster(store_id="46513")
        adj.get_dust_data_for_date("2026-03-10")

        mock_repo.get_factors.assert_called_once_with(
            "2026-03-10", factor_type="weather", store_id="46513"
        )

    @patch("src.prediction.coefficient_adjuster.ExternalFactorRepository")
    def test_precipitation_passes_store_id(self, MockRepo):
        """get_precipitation_data → get_factors(store_id=self.store_id)"""
        mock_repo = MagicMock()
        mock_repo.get_factors.return_value = []
        MockRepo.return_value = mock_repo

        from src.prediction.coefficient_adjuster import CoefficientAdjuster
        adj = CoefficientAdjuster(store_id="46513")
        adj.get_precipitation_for_date("2026-03-10")

        mock_repo.get_factors.assert_called_once_with(
            "2026-03-10", factor_type="weather", store_id="46513"
        )

    @patch("src.prediction.coefficient_adjuster.ExternalFactorRepository")
    def test_holiday_no_store_id(self, MockRepo):
        """_get_holiday_context → get_factors(store_id 없음, 캘린더 공통)"""
        mock_repo = MagicMock()
        mock_repo.get_factors.return_value = []
        MockRepo.return_value = mock_repo

        from src.prediction.coefficient_adjuster import CoefficientAdjuster
        adj = CoefficientAdjuster(store_id="46513")
        adj.get_holiday_context("2026-03-10")

        # 캘린더는 store_id 미전달
        mock_repo.get_factors.assert_called_once_with(
            "2026-03-10", factor_type="calendar"
        )


# =========================================================================
# daily_job.py store_id 전달 검증
# =========================================================================

class TestDailyJobStoreId:
    """DailyCollectionJob._save_weather_data가 store_id 전달하는지 검증"""

    @patch("src.scheduler.daily_job.ExternalFactorRepository")
    @patch("src.scheduler.daily_job.SalesRepository")
    def test_save_weather_passes_store_id(self, MockSalesRepo, MockExtRepo):
        """_save_weather_data → save_factor(store_id=self.store_id)"""
        mock_repo = MagicMock()
        MockExtRepo.return_value = mock_repo
        MockSalesRepo.return_value = MagicMock()

        from src.scheduler.daily_job import DailyCollectionJob
        job = DailyCollectionJob(store_id="46513")

        weather = {
            "temperature": 15,
            "forecast_daily": {"2026-03-11": 18},
            "forecast_precipitation": {
                "2026-03-11": {
                    "rain_rate": 30,
                    "dust_grade": "나쁨",
                    "fine_dust_grade": "보통",
                }
            }
        }
        job._save_weather_data("2026-03-10", weather)

        # 모든 weather 타입 호출에 store_id="46513" 포함 확인
        for call in mock_repo.save_factor.call_args_list:
            kwargs = call.kwargs if call.kwargs else {}
            args = call.args if call.args else ()
            # positional 또는 keyword로 factor_type 확인
            factor_type = kwargs.get("factor_type", args[1] if len(args) > 1 else "")
            if factor_type == "weather":
                sid = kwargs.get("store_id", "")
                assert sid == "46513", f"weather save_factor missing store_id: {call}"
