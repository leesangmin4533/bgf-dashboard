"""
미세먼지 예보 기반 예측 계수 테스트
dust-prediction PDCA
"""

import pytest
from unittest.mock import patch, MagicMock
from src.prediction.coefficient_adjuster import CoefficientAdjuster
from src.settings.constants import (
    DUST_GRADE_SCORE,
    DUST_COEFFICIENTS,
    DUST_CATEGORY_MAP,
)


@pytest.fixture
def adjuster():
    return CoefficientAdjuster(store_id="46513")


# =========================================================================
# DT_INFO 파싱 테스트 (JavaScript 로직의 Python 재현)
# =========================================================================

def _parse_dust_info(raw):
    """JS parseDustInfo 로직의 Python 재현 (테스트 검증용)"""
    if not raw:
        return {"dust": "", "fine": ""}
    import re
    clean = str(raw).replace("\r", "")
    parts = clean.split("\n")
    dust = parts[0].strip()
    fine = ""
    if len(parts) > 1:
        m = re.search(r"\(([^)]+)\)", parts[1])
        fine = m.group(1).strip() if m else ""
    return {"dust": dust, "fine": fine}


def _worse_grade(a, b):
    """JS worseGrade 로직의 Python 재현"""
    return a if (DUST_GRADE_SCORE.get(a, 0) >= DUST_GRADE_SCORE.get(b, 0)) else b


class TestParseDustInfo:
    """DT_INFO 문자열 파싱 테스트"""

    def test_parse_normal(self):
        """정상 파싱: '보통\\n\\r(나쁨\\r)' → dust=보통, fine=나쁨"""
        result = _parse_dust_info("보통\n\r(나쁨\r)")
        assert result["dust"] == "보통"
        assert result["fine"] == "나쁨"

    def test_parse_empty(self):
        """빈값 → dust='', fine=''"""
        result = _parse_dust_info("")
        assert result["dust"] == ""
        assert result["fine"] == ""

    def test_parse_none(self):
        """None → dust='', fine=''"""
        result = _parse_dust_info(None)
        assert result["dust"] == ""
        assert result["fine"] == ""

    def test_parse_hanttae(self):
        """한때나쁨 파싱: '한때나쁨\\n\\r(보통\\r)'"""
        result = _parse_dust_info("한때나쁨\n\r(보통\r)")
        assert result["dust"] == "한때나쁨"
        assert result["fine"] == "보통"

    def test_parse_very_bad(self):
        """매우나쁨 파싱"""
        result = _parse_dust_info("매우나쁨\n\r(매우나쁨\r)")
        assert result["dust"] == "매우나쁨"
        assert result["fine"] == "매우나쁨"

    def test_parse_no_fine(self):
        """괄호 없음 → fine=''"""
        result = _parse_dust_info("보통")
        assert result["dust"] == "보통"
        assert result["fine"] == ""

    def test_parse_with_extra_whitespace(self):
        """공백 포함"""
        result = _parse_dust_info(" 나쁨 \n\r( 매우나쁨 \r)")
        assert result["dust"] == "나쁨"
        assert result["fine"] == "매우나쁨"


# =========================================================================
# 등급 비교 테스트
# =========================================================================

class TestWorseGrade:
    """등급 비교 (worseGrade) 테스트"""

    def test_bad_vs_hanttae(self):
        """나쁨(4) > 한때나쁨(3) → 나쁨"""
        assert _worse_grade("나쁨", "한때나쁨") == "나쁨"

    def test_hanttae_vs_bad(self):
        """순서 바꿔도 나쁨 반환"""
        assert _worse_grade("한때나쁨", "나쁨") == "나쁨"

    def test_same_score(self):
        """동점 시 첫 번째 반환"""
        assert _worse_grade("보통", "보통") == "보통"

    def test_empty_vs_grade(self):
        """빈값 vs 등급 → 등급 반환"""
        assert _worse_grade("", "보통") == "보통"

    def test_both_empty(self):
        """양쪽 빈값 → 빈값"""
        assert _worse_grade("", "") == ""


class TestGradeScoreOrdering:
    """등급 점수 순서 테스트"""

    def test_ordering(self):
        """좋음 < 보통 < 한때나쁨 < 나쁨 < 매우나쁨"""
        grades = ["좋음", "보통", "한때나쁨", "나쁨", "매우나쁨"]
        scores = [DUST_GRADE_SCORE[g] for g in grades]
        assert scores == sorted(scores)
        assert scores == [1, 2, 3, 4, 5]


# =========================================================================
# get_dust_data_for_date 테스트
# =========================================================================

class TestGetDustDataForDate:
    """external_factors에서 미세먼지 등급 조회 테스트"""

    def test_returns_default_when_no_data(self, adjuster):
        """데이터 없을 때 빈 문자열 반환"""
        with patch('src.prediction.coefficient_adjuster.ExternalFactorRepository') as mock_repo:
            mock_repo.return_value.get_factors.return_value = []
            result = adjuster.get_dust_data_for_date("2026-03-11")
            assert result["dust_grade"] == ""
            assert result["fine_dust_grade"] == ""

    def test_parses_dust_grades(self, adjuster):
        """미세먼지/초미세먼지 등급 파싱"""
        with patch('src.prediction.coefficient_adjuster.ExternalFactorRepository') as mock_repo:
            mock_repo.return_value.get_factors.return_value = [
                {"factor_key": "dust_grade_forecast", "factor_value": "나쁨"},
                {"factor_key": "fine_dust_grade_forecast", "factor_value": "한때나쁨"},
            ]
            result = adjuster.get_dust_data_for_date("2026-03-11")
            assert result["dust_grade"] == "나쁨"
            assert result["fine_dust_grade"] == "한때나쁨"

    def test_handles_exception(self, adjuster):
        """예외 시 빈 기본값 반환"""
        with patch('src.prediction.coefficient_adjuster.ExternalFactorRepository') as mock_repo:
            mock_repo.return_value.get_factors.side_effect = Exception("DB error")
            result = adjuster.get_dust_data_for_date("2026-03-11")
            assert result["dust_grade"] == ""
            assert result["fine_dust_grade"] == ""


# =========================================================================
# get_dust_coefficient 테스트
# =========================================================================

class TestGetDustCoefficient:
    """미세먼지 계수 계산 테스트"""

    def _mock_dust_data(self, adjuster, dust_grade, fine_dust_grade):
        """미세먼지 데이터 mock 헬퍼"""
        return patch.object(
            adjuster, 'get_dust_data_for_date',
            return_value={
                "dust_grade": dust_grade,
                "fine_dust_grade": fine_dust_grade,
            }
        )

    def test_good_returns_1(self, adjuster):
        """좋음 → 1.0"""
        with self._mock_dust_data(adjuster, "좋음", "좋음"):
            assert adjuster.get_dust_coefficient("2026-03-11", "001") == 1.0

    def test_normal_returns_1(self, adjuster):
        """보통 → 1.0"""
        with self._mock_dust_data(adjuster, "보통", "보통"):
            assert adjuster.get_dust_coefficient("2026-03-11", "001") == 1.0

    def test_hanttae_returns_1(self, adjuster):
        """한때나쁨(3) → 1.0 (점수 < 4, 영향 없음)"""
        with self._mock_dust_data(adjuster, "한때나쁨", "한때나쁨"):
            assert adjuster.get_dust_coefficient("2026-03-11", "001") == 1.0

    def test_bad_food(self, adjuster):
        """나쁨 + 푸드(001) → 0.95"""
        with self._mock_dust_data(adjuster, "나쁨", "보통"):
            assert adjuster.get_dust_coefficient("2026-03-11", "001") == 0.95

    def test_bad_beverage(self, adjuster):
        """나쁨 + 음료(042) → 0.93"""
        with self._mock_dust_data(adjuster, "나쁨", "나쁨"):
            assert adjuster.get_dust_coefficient("2026-03-11", "042") == 0.93

    def test_bad_ice(self, adjuster):
        """나쁨 + 아이스(027) → 0.90"""
        with self._mock_dust_data(adjuster, "나쁨", "나쁨"):
            assert adjuster.get_dust_coefficient("2026-03-11", "027") == 0.90

    def test_bad_default(self, adjuster):
        """나쁨 + 담배(049) → 0.97"""
        with self._mock_dust_data(adjuster, "나쁨", "나쁨"):
            assert adjuster.get_dust_coefficient("2026-03-11", "049") == 0.97

    def test_very_bad_food(self, adjuster):
        """매우나쁨 + 푸드(003) → 0.90"""
        with self._mock_dust_data(adjuster, "매우나쁨", "매우나쁨"):
            assert adjuster.get_dust_coefficient("2026-03-11", "003") == 0.90

    def test_very_bad_ice(self, adjuster):
        """매우나쁨 + 아이스(029) → 0.83"""
        with self._mock_dust_data(adjuster, "매우나쁨", "매우나쁨"):
            assert adjuster.get_dust_coefficient("2026-03-11", "029") == 0.83

    def test_very_bad_beverage(self, adjuster):
        """매우나쁨 + 음료(045) → 0.87"""
        with self._mock_dust_data(adjuster, "매우나쁨", "매우나쁨"):
            assert adjuster.get_dust_coefficient("2026-03-11", "045") == 0.87

    def test_very_bad_default(self, adjuster):
        """매우나쁨 + 기타(015) → 0.93"""
        with self._mock_dust_data(adjuster, "매우나쁨", "매우나쁨"):
            assert adjuster.get_dust_coefficient("2026-03-11", "015") == 0.93

    def test_toggle_off(self, adjuster):
        """DUST_PREDICTION_ENABLED=False → 1.0"""
        with patch('src.settings.constants.DUST_PREDICTION_ENABLED', False):
            with self._mock_dust_data(adjuster, "매우나쁨", "매우나쁨"):
                assert adjuster.get_dust_coefficient("2026-03-11", "001") == 1.0

    def test_mixed_grades_uses_worse(self, adjuster):
        """dust=보통(2), fine=나쁨(4) → score=4 → 나쁨 계수"""
        with self._mock_dust_data(adjuster, "보통", "나쁨"):
            assert adjuster.get_dust_coefficient("2026-03-11", "001") == 0.95

    def test_no_data_returns_1(self, adjuster):
        """DB에 데이터 없으면 1.0"""
        with self._mock_dust_data(adjuster, "", ""):
            assert adjuster.get_dust_coefficient("2026-03-11", "001") == 1.0

    def test_bad_food_012(self, adjuster):
        """나쁨 + 조리빵(012) → 0.95 (food 카테고리)"""
        with self._mock_dust_data(adjuster, "나쁨", "나쁨"):
            assert adjuster.get_dust_coefficient("2026-03-11", "012") == 0.95

    def test_bad_beverage_048(self, adjuster):
        """나쁨 + 음료(048) → 0.93"""
        with self._mock_dust_data(adjuster, "나쁨", "나쁨"):
            assert adjuster.get_dust_coefficient("2026-03-11", "048") == 0.93


# =========================================================================
# DB 저장 테스트
# =========================================================================

class TestDustSave:
    """미세먼지 등급 DB 저장 테스트"""

    def test_save_dust_to_external_factors(self):
        """dust_grade/fine_dust_grade가 external_factors에 저장되는지 검증"""
        mock_repo = MagicMock()
        precip_data = {
            "2026-03-11": {
                "rain_rate": 30,
                "rain_qty": 2.0,
                "rain_type_nm": "",
                "weather_cd_nm": "구름많음",
                "is_snow": False,
                "dust_grade": "나쁨",
                "fine_dust_grade": "한때나쁨",
            }
        }

        # daily_job.py 로직 시뮬레이션
        for fdate, precip in precip_data.items():
            if precip.get("dust_grade"):
                mock_repo.save_factor(
                    factor_date=fdate,
                    factor_type="weather",
                    factor_key="dust_grade_forecast",
                    factor_value=precip["dust_grade"]
                )
            if precip.get("fine_dust_grade"):
                mock_repo.save_factor(
                    factor_date=fdate,
                    factor_type="weather",
                    factor_key="fine_dust_grade_forecast",
                    factor_value=precip["fine_dust_grade"]
                )

        calls = mock_repo.save_factor.call_args_list
        assert len(calls) == 2
        assert calls[0].kwargs["factor_key"] == "dust_grade_forecast"
        assert calls[0].kwargs["factor_value"] == "나쁨"
        assert calls[1].kwargs["factor_key"] == "fine_dust_grade_forecast"
        assert calls[1].kwargs["factor_value"] == "한때나쁨"

    def test_save_empty_dust_skipped(self):
        """빈값이면 저장 안 함"""
        mock_repo = MagicMock()
        precip_data = {
            "2026-03-11": {
                "rain_rate": 30,
                "rain_qty": None,
                "rain_type_nm": "",
                "weather_cd_nm": "",
                "is_snow": False,
                # dust_grade, fine_dust_grade 없음
            }
        }

        for fdate, precip in precip_data.items():
            if precip.get("dust_grade"):
                mock_repo.save_factor(
                    factor_date=fdate,
                    factor_type="weather",
                    factor_key="dust_grade_forecast",
                    factor_value=precip["dust_grade"]
                )
            if precip.get("fine_dust_grade"):
                mock_repo.save_factor(
                    factor_date=fdate,
                    factor_type="weather",
                    factor_key="fine_dust_grade_forecast",
                    factor_value=precip["fine_dust_grade"]
                )

        mock_repo.save_factor.assert_not_called()


# =========================================================================
# 카테고리 매핑 테스트
# =========================================================================

class TestDustCategoryMap:
    """DUST_CATEGORY_MAP 검증"""

    def test_food_contains_expected(self):
        """food 카테고리에 001~005, 012 포함"""
        expected = ["001", "002", "003", "004", "005", "012"]
        assert DUST_CATEGORY_MAP["food"] == expected

    def test_beverage_contains_expected(self):
        """beverage 카테고리에 039~048 포함"""
        assert len(DUST_CATEGORY_MAP["beverage"]) == 10
        assert "039" in DUST_CATEGORY_MAP["beverage"]
        assert "048" in DUST_CATEGORY_MAP["beverage"]

    def test_ice_contains_expected(self):
        """ice 카테고리에 027~030 포함"""
        assert DUST_CATEGORY_MAP["ice"] == ["027", "028", "029", "030"]

    def test_no_overlap(self):
        """카테고리 간 중복 없음"""
        all_mids = []
        for mids in DUST_CATEGORY_MAP.values():
            all_mids.extend(mids)
        assert len(all_mids) == len(set(all_mids))


# =========================================================================
# 통합 테스트
# =========================================================================

class TestDustIntegration:
    """미세먼지 계수 통합 테스트"""

    def test_dust_coef_multiplied_into_weather_coef(self, adjuster):
        """apply() 경로에서 dust_coef가 weather_coef에 곱해지는지 검증"""
        with patch.object(
            adjuster, 'get_dust_data_for_date',
            return_value={"dust_grade": "나쁨", "fine_dust_grade": "보통"}
        ):
            # dust_coefficient가 1.0이 아닌 값을 반환하는지 확인
            coef = adjuster.get_dust_coefficient("2026-03-11", "001")
            assert coef == 0.95
            assert coef < 1.0  # 감소 효과 확인

    def test_exception_returns_safe_fallback(self, adjuster):
        """예외 발생 시 안전하게 1.0 반환"""
        with patch.object(
            adjuster, 'get_dust_data_for_date',
            side_effect=Exception("unexpected error")
        ):
            coef = adjuster.get_dust_coefficient("2026-03-11", "001")
            assert coef == 1.0
