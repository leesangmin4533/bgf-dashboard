"""
상권 분석 서비스 (analysis_service.py) 테스트

핵심 함수 검증:
- _calc_traffic_score(): 유동인구 가중치 계산
- _calc_competition_score(): 경쟁 구간별 점수
- _classify_area_type(): 상권 유형 복합 태그
- get_store_analysis(): DB 미등록 매장 조회
- run_store_analysis(): 좌표 없는 매장 처리
"""

import sqlite3
from unittest.mock import patch, MagicMock

import pytest

from src.application.services.analysis_service import (
    _calc_traffic_score,
    _calc_competition_score,
    _classify_area_type,
    get_store_analysis,
    run_store_analysis,
    TRAFFIC_WEIGHTS,
)


# =============================================================================
# _calc_traffic_score 테스트
# =============================================================================
class TestCalcTrafficScore:
    """유동인구 점수 계산 검증."""

    def test_basic_weighted_score(self):
        """subway=2, restaurant=10, cafe=5 입력 시 가중치 계산 정확성."""
        counts = {
            "subway_count": 2,
            "restaurant_count": 10,
            "cafe_count": 5,
        }
        # raw = 2*3 + 10*1 + 5*1 = 6 + 10 + 5 = 21
        # score = min(100, round((21/150)*100, 1)) = round(14.0, 1) = 14.0
        result = _calc_traffic_score(counts)
        expected = round((21 / 150) * 100, 1)
        assert result == expected

    def test_zero_counts(self):
        """모든 카운트 0이면 점수 0."""
        result = _calc_traffic_score({})
        assert result == 0.0

    def test_max_score_cap_at_100(self):
        """raw가 150 이상이면 100점 상한."""
        counts = {
            "subway_count": 50,      # 50*3 = 150 (이것만으로 raw>=150)
            "restaurant_count": 100,
            "cafe_count": 100,
        }
        result = _calc_traffic_score(counts)
        assert result == 100.0

    def test_all_weights_applied(self):
        """모든 가중치 항목이 반영되는지 확인."""
        counts = {k: 1 for k in TRAFFIC_WEIGHTS}
        # raw = 3+2+2+1+1+1+1 = 11
        expected = round((11 / 150) * 100, 1)
        result = _calc_traffic_score(counts)
        assert result == expected

    def test_partial_counts(self):
        """일부 항목만 있어도 정상 계산."""
        counts = {"school_count": 5, "office_count": 3}
        # raw = 5*2 + 3*1 = 13
        expected = round((13 / 150) * 100, 1)
        result = _calc_traffic_score(counts)
        assert result == expected


# =============================================================================
# _calc_competition_score 테스트
# =============================================================================
class TestCalcCompetitionScore:
    """경쟁 점수 구간별 검증."""

    def test_zero_competitor_monopoly(self):
        """편의점 0개 → 독점 (0.0)."""
        assert _calc_competition_score(0) == 0.0

    def test_low_competitor_comfortable(self):
        """편의점 2개 → 여유 (30.0)."""
        assert _calc_competition_score(2) == 30.0

    def test_one_competitor(self):
        """편의점 1개 → 여유 (30.0)."""
        assert _calc_competition_score(1) == 30.0

    def test_medium_competitor_normal(self):
        """편의점 3~4개 → 보통 (60.0)."""
        assert _calc_competition_score(3) == 60.0
        assert _calc_competition_score(4) == 60.0

    def test_high_competitor_competitive(self):
        """편의점 5~7개 → 경쟁 (80.0)."""
        assert _calc_competition_score(5) == 80.0
        assert _calc_competition_score(7) == 80.0

    def test_extreme_competitor_overheated(self):
        """편의점 8개 이상 → 과열 (100.0)."""
        assert _calc_competition_score(8) == 100.0
        assert _calc_competition_score(9) == 100.0
        assert _calc_competition_score(20) == 100.0


# =============================================================================
# _classify_area_type 테스트
# =============================================================================
class TestClassifyAreaType:
    """상권 유형 복합 분류 검증."""

    def test_subway_with_overheated_competition(self):
        """subway=3, competitor=9 → '역세권·경쟁과열'."""
        counts = {"subway_count": 3, "competitor_count": 9}
        result = _classify_area_type(counts)
        assert result == "역세권·경쟁과열"

    def test_commercial_with_competition(self):
        """restaurant=15, competitor=6 → '상업지구·경쟁'."""
        counts = {"restaurant_count": 15, "competitor_count": 6}
        result = _classify_area_type(counts)
        assert result == "상업지구·경쟁"

    def test_residential_apartment(self):
        """daycare=4, subway=0 → '아파트상권'."""
        counts = {"daycare_count": 4, "subway_count": 0}
        result = _classify_area_type(counts)
        assert result == "아파트상권"

    def test_subway_station_area(self):
        """subway=2 → 역세권 (주상권)."""
        counts = {"subway_count": 2}
        result = _classify_area_type(counts)
        assert "역세권" in result

    def test_school_district(self):
        """school=3 → 학원가."""
        counts = {"school_count": 3}
        result = _classify_area_type(counts)
        assert "학원가" in result

    def test_office_type(self):
        """office=5 → 직장형."""
        counts = {"office_count": 5}
        result = _classify_area_type(counts)
        assert "직장형" in result

    def test_default_residential(self):
        """특별한 특성 없으면 주거형."""
        counts = {}
        result = _classify_area_type(counts)
        assert "주거형" in result

    def test_no_sub_tag(self):
        """부특성 태그 조건 미달이면 주상권만 반환."""
        counts = {"subway_count": 2, "competitor_count": 1}
        result = _classify_area_type(counts)
        assert result == "역세권"  # 경쟁 아님, 역근처 제외(이미 역세권)

    def test_cafe_street_sub_tag(self):
        """cafe=10이면 카페거리 부특성."""
        counts = {"cafe_count": 10}
        result = _classify_area_type(counts)
        assert "카페거리" in result

    def test_near_school_sub_tag(self):
        """school=1 (학원가 미달)이면 학교근처 부특성."""
        counts = {"school_count": 1, "competitor_count": 0}
        result = _classify_area_type(counts)
        assert "학교근처" in result


# =============================================================================
# get_store_analysis 테스트
# =============================================================================
class TestGetStoreAnalysis:
    """저장된 상권 분석 결과 조회 검증."""

    def test_nonexistent_store_returns_empty_dict(self, tmp_path):
        """DB에 없는 매장 → 빈 dict 반환 (에러 없음)."""
        db_path = str(tmp_path / "common.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE store_analysis (
                store_id TEXT PRIMARY KEY,
                competitor_count INTEGER DEFAULT 0,
                school_count INTEGER DEFAULT 0,
                hospital_count INTEGER DEFAULT 0,
                restaurant_count INTEGER DEFAULT 0,
                cafe_count INTEGER DEFAULT 0,
                subway_count INTEGER DEFAULT 0,
                office_count INTEGER DEFAULT 0,
                daycare_count INTEGER DEFAULT 0,
                traffic_score REAL DEFAULT 0,
                competition_score REAL DEFAULT 0,
                area_type TEXT DEFAULT '',
                analyzed_at TEXT DEFAULT '',
                radius_m INTEGER DEFAULT 500
            )
        """)
        conn.commit()
        conn.close()

        mock_conn = sqlite3.connect(db_path)
        with patch("src.application.services.analysis_service.DBRouter") as mock_router:
            mock_router.get_common_connection.return_value = mock_conn

            result = get_store_analysis("NONEXISTENT")

        assert result == {}

    def test_existing_store_returns_data(self, tmp_path):
        """DB에 있는 매장 → 데이터 dict 반환."""
        db_path = str(tmp_path / "common.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE store_analysis (
                store_id TEXT PRIMARY KEY,
                competitor_count INTEGER DEFAULT 0,
                traffic_score REAL DEFAULT 0,
                competition_score REAL DEFAULT 0,
                area_type TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            INSERT INTO store_analysis (store_id, competitor_count, traffic_score, competition_score, area_type)
            VALUES ('46513', 3, 45.0, 60.0, '역세권')
        """)
        conn.commit()
        conn.close()

        mock_conn = sqlite3.connect(db_path)
        with patch("src.application.services.analysis_service.DBRouter") as mock_router:
            mock_router.get_common_connection.return_value = mock_conn

            result = get_store_analysis("46513")

        assert result["store_id"] == "46513"
        assert result["area_type"] == "역세권"
        assert result["traffic_score"] == 45.0


# =============================================================================
# run_store_analysis 테스트
# =============================================================================
class TestRunStoreAnalysis:
    """상권 분석 실행 검증."""

    def test_no_api_key_returns_empty(self):
        """KAKAO_REST_API_KEY 미설정 → {} 반환, 예외 없음."""
        with patch.dict("os.environ", {}, clear=True):
            # 환경변수에서 KAKAO_REST_API_KEY 제거
            import os
            env_backup = os.environ.pop("KAKAO_REST_API_KEY", None)
            try:
                result = run_store_analysis("46513")
                assert result == {}
            finally:
                if env_backup:
                    os.environ["KAKAO_REST_API_KEY"] = env_backup

    def test_no_coordinates_returns_empty(self, tmp_path):
        """lat/lng 없는 매장 → {} 반환, 예외 없음."""
        db_path = str(tmp_path / "common.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE stores (store_id TEXT, lat REAL, lng REAL)")
        # lat, lng 없는 매장
        conn.execute("INSERT INTO stores VALUES ('99999', NULL, NULL)")
        conn.commit()

        mock_conn = sqlite3.connect(db_path)
        with patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test_key"}):
            with patch("src.application.services.analysis_service.DBRouter") as mock_router:
                mock_router.get_common_connection.return_value = mock_conn

                result = run_store_analysis("99999")

        assert result == {}

    def test_store_not_in_db_returns_empty(self, tmp_path):
        """stores 테이블에 없는 매장 → {} 반환."""
        db_path = str(tmp_path / "common.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE stores (store_id TEXT, lat REAL, lng REAL)")
        conn.commit()

        mock_conn = sqlite3.connect(db_path)
        with patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test_key"}):
            with patch("src.application.services.analysis_service.DBRouter") as mock_router:
                mock_router.get_common_connection.return_value = mock_conn

                result = run_store_analysis("MISSING")

        assert result == {}
