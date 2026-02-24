"""
라면(ramen) 카테고리 모듈 단위 테스트

대상: src/prediction/categories/ramen.py
- is_ramen_category(): 라면 카테고리 여부 확인
"""

import pytest

from src.prediction.categories.ramen import (
    is_ramen_category,
    RAMEN_DYNAMIC_SAFETY_CONFIG,
)


# =============================================================================
# is_ramen_category 테스트
# =============================================================================
class TestIsRamenCategory:
    """라면 카테고리 판별 테스트"""

    @pytest.mark.unit
    def test_006_is_ramen(self):
        """'006' (조리면)은 라면 카테고리"""
        assert is_ramen_category("006") is True

    @pytest.mark.unit
    def test_032_is_ramen(self):
        """'032' (면류)는 라면 카테고리"""
        assert is_ramen_category("032") is True

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", [
        "049", "050", "072", "073", "001", "012", "044", "900", "999",
        "", "6", "06", "0006", "032 ",
    ])
    def test_non_ramen_categories(self, mid_cd):
        """라면이 아닌 카테고리는 False 반환"""
        assert is_ramen_category(mid_cd) is False

    @pytest.mark.unit
    def test_target_categories_config(self):
        """RAMEN_DYNAMIC_SAFETY_CONFIG에 '006', '032' 포함 확인"""
        target = RAMEN_DYNAMIC_SAFETY_CONFIG["target_categories"]
        assert "006" in target
        assert "032" in target
        assert len(target) == 2

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", RAMEN_DYNAMIC_SAFETY_CONFIG["target_categories"])
    def test_all_configured_categories_are_ramen(self, mid_cd):
        """설정에 등록된 모든 카테고리가 is_ramen_category() True 반환"""
        assert is_ramen_category(mid_cd) is True

    @pytest.mark.unit
    def test_config_enabled(self):
        """동적 안전재고 기능이 활성화 상태 확인"""
        assert RAMEN_DYNAMIC_SAFETY_CONFIG["enabled"] is True

    @pytest.mark.unit
    def test_config_safety_values(self):
        """라면 설정 핵심값 확인"""
        config = RAMEN_DYNAMIC_SAFETY_CONFIG
        assert config["default_safety_days"] == 2.0
        assert config["max_stock_days"] == 4.0  # 5.0→4.0 (20% 축소: 과예측 방지)
        assert config["analysis_days"] == 30
        assert config["min_data_days"] == 7

    @pytest.mark.unit
    def test_turnover_config_structure(self):
        """회전율별 안전재고 설정 구조 확인"""
        turnover = RAMEN_DYNAMIC_SAFETY_CONFIG["turnover_safety_days"]
        assert "high" in turnover
        assert "medium" in turnover
        assert "low" in turnover
        # 고회전: 2.0일, 중회전: 2.0일, 저회전: 1.0일
        assert turnover["high"]["safety_days"] == 2.0
        assert turnover["medium"]["safety_days"] == 2.0
        assert turnover["low"]["safety_days"] == 1.0
        # 임계값 확인
        assert turnover["high"]["min_daily_avg"] == 5.0
        assert turnover["medium"]["min_daily_avg"] == 2.0
        assert turnover["low"]["min_daily_avg"] == 0.0

    @pytest.mark.unit
    def test_numeric_input_not_ramen(self):
        """정수 입력은 라면으로 판별되지 않음"""
        assert is_ramen_category(6) is False   # type: ignore
        assert is_ramen_category(32) is False  # type: ignore

    @pytest.mark.unit
    def test_similar_codes_not_ramen(self):
        """유사 코드가 라면으로 판별되지 않는지 확인"""
        assert is_ramen_category("005") is False
        assert is_ramen_category("007") is False
        assert is_ramen_category("031") is False
        assert is_ramen_category("033") is False
