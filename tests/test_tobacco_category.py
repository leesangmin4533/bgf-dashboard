"""
담배/전자담배(tobacco) 카테고리 모듈 단위 테스트

대상: src/prediction/categories/tobacco.py
- is_tobacco_category(): 담배/전자담배 카테고리 여부 확인
"""

import pytest

from src.prediction.categories.tobacco import (
    is_tobacco_category,
    TOBACCO_DYNAMIC_SAFETY_CONFIG,
)


# =============================================================================
# is_tobacco_category 테스트
# =============================================================================
class TestIsTobaccoCategory:
    """담배/전자담배 카테고리 판별 테스트"""

    @pytest.mark.unit
    def test_072_is_tobacco(self):
        """'072' (담배)는 담배 카테고리"""
        assert is_tobacco_category("072") is True

    @pytest.mark.unit
    def test_073_is_tobacco(self):
        """'073' (전자담배)는 담배 카테고리"""
        assert is_tobacco_category("073") is True

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", [
        "049", "050", "051", "001", "032", "044", "900", "999", "", "72", "0072",
    ])
    def test_non_tobacco_categories(self, mid_cd):
        """담배/전자담배가 아닌 카테고리는 False 반환"""
        assert is_tobacco_category(mid_cd) is False

    @pytest.mark.unit
    def test_target_categories_config(self):
        """TOBACCO_DYNAMIC_SAFETY_CONFIG에 '072', '073' 포함 확인"""
        target = TOBACCO_DYNAMIC_SAFETY_CONFIG["target_categories"]
        assert "072" in target
        assert "073" in target
        assert len(target) == 2

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", TOBACCO_DYNAMIC_SAFETY_CONFIG["target_categories"])
    def test_all_configured_categories_are_tobacco(self, mid_cd):
        """설정에 등록된 모든 카테고리가 is_tobacco_category() True 반환"""
        assert is_tobacco_category(mid_cd) is True

    @pytest.mark.unit
    def test_config_safety_values(self):
        """담배 설정 핵심값 확인: 상한선 30개, 기본 안전재고 2.0일"""
        config = TOBACCO_DYNAMIC_SAFETY_CONFIG
        assert config["max_stock"] == 30
        assert config["default_safety_days"] == 2.0
        assert config["min_order_unit"] == 10
        assert config["carton_unit"] == 10

    @pytest.mark.unit
    def test_config_enabled(self):
        """동적 안전재고 기능이 활성화 상태 확인"""
        assert TOBACCO_DYNAMIC_SAFETY_CONFIG["enabled"] is True

    @pytest.mark.unit
    def test_numeric_string_not_int(self):
        """정수가 아닌 문자열 입력 처리"""
        assert is_tobacco_category(72) is False  # type: ignore
        assert is_tobacco_category(73) is False  # type: ignore

    @pytest.mark.unit
    def test_similar_codes_not_tobacco(self):
        """유사 코드가 담배로 판별되지 않는지 확인"""
        assert is_tobacco_category("071") is False
        assert is_tobacco_category("074") is False
        assert is_tobacco_category("0072") is False
        assert is_tobacco_category("072 ") is False
