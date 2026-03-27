"""
냉동 재발주 바이패스 (frozen-reorder) 테스트

대상:
- FROZEN_REORDER_CONFIG: 설정값 검증
- should_bypass_frozen_surplus_zero(): 바이패스 판정 로직
- improved_predictor._round_to_order_unit(): Branch A 바이패스 통합

테스트 전략:
- 단위: should_bypass 함수 조건별 분기
- 통합: _round_to_order_unit에서 RoundResult.stage="round_frozen_reorder" 확인
- 경계값: 임계값 경계 (stock=3/4, unit=11/12, sales=4/5)
"""

import sys
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from prediction.categories.frozen_ice import (
    FROZEN_REORDER_CONFIG,
    FROZEN_ICE_CATEGORIES,
    FROZEN_ICE_DYNAMIC_SAFETY_CONFIG,
    should_bypass_frozen_surplus_zero,
    is_frozen_ice_category,
)


# =============================================================================
# FROZEN_REORDER_CONFIG 설정값 테스트
# =============================================================================
class TestFrozenReorderConfig:
    """설정값 정합성 테스트"""

    @pytest.mark.unit
    def test_config_enabled(self):
        """기본 enabled=True"""
        assert FROZEN_REORDER_CONFIG["enabled"] is True

    @pytest.mark.unit
    def test_config_min_display_qty(self):
        """최소진열수량 = 3"""
        assert FROZEN_REORDER_CONFIG["min_display_qty"] == 3

    @pytest.mark.unit
    def test_config_large_unit_threshold(self):
        """대형단위 임계값 = 12"""
        assert FROZEN_REORDER_CONFIG["large_unit_threshold"] == 12

    @pytest.mark.unit
    def test_config_min_sales_for_reorder(self):
        """90일 최소 판매수량 = 5"""
        assert FROZEN_REORDER_CONFIG["min_sales_for_reorder"] == 5

    @pytest.mark.unit
    def test_config_max_order_boxes(self):
        """최대 발주 박스 = 1"""
        assert FROZEN_REORDER_CONFIG["max_order_boxes"] == 1

    @pytest.mark.unit
    def test_config_sales_lookup_days(self):
        """판매이력 조회기간 = 90일"""
        assert FROZEN_REORDER_CONFIG["sales_lookup_days"] == 90


# =============================================================================
# Phase A: FROZEN_ICE_DYNAMIC_SAFETY_CONFIG 변경값 테스트
# =============================================================================
class TestDynamicSafetyConfigChanges:
    """frozen-reorder로 변경된 CONFIG 값 검증"""

    @pytest.mark.unit
    def test_analysis_days_90(self):
        """analysis_days: 30→90 변경 확인"""
        assert FROZEN_ICE_DYNAMIC_SAFETY_CONFIG["analysis_days"] == 90

    @pytest.mark.unit
    def test_min_data_days_7(self):
        """min_data_days: 14→7 변경 확인"""
        assert FROZEN_ICE_DYNAMIC_SAFETY_CONFIG["min_data_days"] == 7

    @pytest.mark.unit
    def test_max_stock_days_14(self):
        """max_stock_days: 7→14 변경 확인"""
        assert FROZEN_ICE_DYNAMIC_SAFETY_CONFIG["max_stock_days"] == 14.0


# =============================================================================
# should_bypass_frozen_surplus_zero 테스트
# =============================================================================
class TestShouldBypassFrozenSurplusZero:
    """바이패스 판정 함수 테스트"""

    def _mock_db_sales(self, sales_qty):
        """DB 조회를 모킹하여 판매량 반환"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (sales_qty,)
        return mock_conn

    # --- 정상 바이패스 (True) ---

    @pytest.mark.unit
    @patch("prediction.categories.frozen_ice.sqlite3")
    @patch("prediction.categories.frozen_ice._get_db_path")
    def test_bypass_all_conditions_met(self, mock_db_path, mock_sqlite):
        """모든 조건 충족 시 True 반환"""
        mock_db_path.return_value = ":memory:"
        mock_sqlite.connect.return_value = self._mock_db_sales(10)

        result = should_bypass_frozen_surplus_zero(
            item_cd="ITEM001", mid_cd="021",
            current_stock=0, order_unit_qty=24,
            store_id="46513"
        )
        assert result is True

    @pytest.mark.unit
    @patch("prediction.categories.frozen_ice.sqlite3")
    @patch("prediction.categories.frozen_ice._get_db_path")
    def test_bypass_stock_at_boundary_3(self, mock_db_path, mock_sqlite):
        """stock=3 (경계값) → True (<=3)"""
        mock_db_path.return_value = ":memory:"
        mock_sqlite.connect.return_value = self._mock_db_sales(10)

        result = should_bypass_frozen_surplus_zero(
            item_cd="ITEM001", mid_cd="034",
            current_stock=3, order_unit_qty=24
        )
        assert result is True

    @pytest.mark.unit
    @patch("prediction.categories.frozen_ice.sqlite3")
    @patch("prediction.categories.frozen_ice._get_db_path")
    def test_bypass_unit_at_boundary_12(self, mock_db_path, mock_sqlite):
        """unit=12 (경계값) → True (>=12)"""
        mock_db_path.return_value = ":memory:"
        mock_sqlite.connect.return_value = self._mock_db_sales(10)

        result = should_bypass_frozen_surplus_zero(
            item_cd="ITEM001", mid_cd="100",
            current_stock=0, order_unit_qty=12
        )
        assert result is True

    @pytest.mark.unit
    @patch("prediction.categories.frozen_ice.sqlite3")
    @patch("prediction.categories.frozen_ice._get_db_path")
    def test_bypass_sales_at_boundary_5(self, mock_db_path, mock_sqlite):
        """sales=5 (경계값) → True (>=5)"""
        mock_db_path.return_value = ":memory:"
        mock_sqlite.connect.return_value = self._mock_db_sales(5)

        result = should_bypass_frozen_surplus_zero(
            item_cd="ITEM001", mid_cd="021",
            current_stock=0, order_unit_qty=24
        )
        assert result is True

    @pytest.mark.unit
    @patch("prediction.categories.frozen_ice.sqlite3")
    @patch("prediction.categories.frozen_ice._get_db_path")
    def test_bypass_mid_cd_034(self, mock_db_path, mock_sqlite):
        """034 (냉동즉석식) 바이패스 허용"""
        mock_db_path.return_value = ":memory:"
        mock_sqlite.connect.return_value = self._mock_db_sales(20)

        result = should_bypass_frozen_surplus_zero(
            item_cd="ITEM002", mid_cd="034",
            current_stock=1, order_unit_qty=40
        )
        assert result is True

    @pytest.mark.unit
    @patch("prediction.categories.frozen_ice.sqlite3")
    @patch("prediction.categories.frozen_ice._get_db_path")
    def test_bypass_mid_cd_100(self, mock_db_path, mock_sqlite):
        """100 (RI아이스크림) 바이패스 허용"""
        mock_db_path.return_value = ":memory:"
        mock_sqlite.connect.return_value = self._mock_db_sales(15)

        result = should_bypass_frozen_surplus_zero(
            item_cd="ITEM003", mid_cd="100",
            current_stock=2, order_unit_qty=30
        )
        assert result is True

    # --- 바이패스 거부 (False) ---

    @pytest.mark.unit
    def test_reject_disabled(self):
        """enabled=False → False (DB 조회 안 함)"""
        with patch.dict(FROZEN_REORDER_CONFIG, {"enabled": False}):
            result = should_bypass_frozen_surplus_zero(
                item_cd="ITEM001", mid_cd="021",
                current_stock=0, order_unit_qty=24
            )
            assert result is False

    @pytest.mark.unit
    def test_reject_non_frozen_category(self):
        """비냉동 카테고리(049) → False"""
        result = should_bypass_frozen_surplus_zero(
            item_cd="ITEM001", mid_cd="049",
            current_stock=0, order_unit_qty=24
        )
        assert result is False

    @pytest.mark.unit
    def test_reject_food_category(self):
        """푸드 카테고리(001) → False"""
        result = should_bypass_frozen_surplus_zero(
            item_cd="ITEM001", mid_cd="001",
            current_stock=0, order_unit_qty=24
        )
        assert result is False

    @pytest.mark.unit
    def test_reject_stock_above_threshold(self):
        """stock=4 (> min_display_qty 3) → False"""
        result = should_bypass_frozen_surplus_zero(
            item_cd="ITEM001", mid_cd="021",
            current_stock=4, order_unit_qty=24
        )
        assert result is False

    @pytest.mark.unit
    def test_reject_small_order_unit(self):
        """unit=11 (< large_unit_threshold 12) → False"""
        result = should_bypass_frozen_surplus_zero(
            item_cd="ITEM001", mid_cd="021",
            current_stock=0, order_unit_qty=11
        )
        assert result is False

    @pytest.mark.unit
    @patch("prediction.categories.frozen_ice.sqlite3")
    @patch("prediction.categories.frozen_ice._get_db_path")
    def test_reject_low_sales(self, mock_db_path, mock_sqlite):
        """sales=4 (< min_sales_for_reorder 5) → False"""
        mock_db_path.return_value = ":memory:"
        mock_sqlite.connect.return_value = self._mock_db_sales(4)

        result = should_bypass_frozen_surplus_zero(
            item_cd="ITEM001", mid_cd="021",
            current_stock=0, order_unit_qty=24
        )
        assert result is False

    @pytest.mark.unit
    @patch("prediction.categories.frozen_ice.sqlite3")
    @patch("prediction.categories.frozen_ice._get_db_path")
    def test_reject_zero_sales(self, mock_db_path, mock_sqlite):
        """sales=0 (판매실적 없음) → False"""
        mock_db_path.return_value = ":memory:"
        mock_sqlite.connect.return_value = self._mock_db_sales(0)

        result = should_bypass_frozen_surplus_zero(
            item_cd="ITEM001", mid_cd="021",
            current_stock=0, order_unit_qty=24
        )
        assert result is False

    @pytest.mark.unit
    @patch("prediction.categories.frozen_ice._get_db_path")
    def test_reject_db_error(self, mock_db_path):
        """DB 오류 시 안전하게 False 반환"""
        mock_db_path.side_effect = Exception("DB error")

        result = should_bypass_frozen_surplus_zero(
            item_cd="ITEM001", mid_cd="021",
            current_stock=0, order_unit_qty=24
        )
        assert result is False

    # --- 경계값 테스트 ---

    @pytest.mark.unit
    def test_stock_exactly_0(self):
        """stock=0 (완전 품절) → 카테고리/단위 조건 통과 후 DB 조회"""
        with patch("prediction.categories.frozen_ice.sqlite3") as mock_sqlite, \
             patch("prediction.categories.frozen_ice._get_db_path") as mock_db:
            mock_db.return_value = ":memory:"
            mock_sqlite.connect.return_value = self._mock_db_sales(10)
            result = should_bypass_frozen_surplus_zero(
                item_cd="ITEM001", mid_cd="021",
                current_stock=0, order_unit_qty=24
            )
            assert result is True

    @pytest.mark.unit
    def test_unit_exactly_1_reject(self):
        """unit=1 (소형) → False (임계값 미만)"""
        result = should_bypass_frozen_surplus_zero(
            item_cd="ITEM001", mid_cd="021",
            current_stock=0, order_unit_qty=1
        )
        assert result is False


# =============================================================================
# 통합 테스트: _round_to_order_unit에서 frozen_reorder 바이패스
# =============================================================================
class TestRoundToOrderUnitFrozenBypass:
    """improved_predictor._round_to_order_unit 통합 테스트"""

    def _make_pattern(self, max_stock=5.0, skip_order=False, daily_avg=0.3):
        """FrozenIcePatternResult를 흉내내는 간단 객체 생성"""
        pattern = MagicMock()
        pattern.max_stock = max_stock
        pattern.skip_order = skip_order
        pattern.daily_avg = daily_avg
        return pattern

    @pytest.mark.unit
    @patch("src.prediction.categories.frozen_ice.sqlite3")
    @patch("src.prediction.categories.frozen_ice._get_db_path")
    def test_round_result_stage_frozen_reorder(self, mock_db_path, mock_sqlite):
        """바이패스 시 RoundResult.stage == 'round_frozen_reorder' 확인"""
        mock_db_path.return_value = ":memory:"
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (10,)
        mock_sqlite.connect.return_value = mock_conn

        from prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor.__new__(ImprovedPredictor)
        predictor.store_id = "46513"

        product = {
            "item_cd": "FROZEN001",
            "item_nm": "아이스크림 24입",
            "mid_cd": "021",
        }
        ctx = {}

        # new_cat_pattern.max_stock > 0 이면 Branch A 진입
        pattern = self._make_pattern(max_stock=5.0)

        # stock=1: days_cover = 1/0.3 = 3.3 >= 0.5 → needs_ceil=False
        # floor_qty = floor(0.3/24)*24 = 0 → else 블록 → frozen bypass
        result = predictor._round_to_order_unit(
            order_qty=0.3,
            order_unit=24,
            mid_cd="021",
            product=product,
            daily_avg=0.3,
            current_stock=1,
            pending_qty=0,
            safety_stock=0.5,
            adjusted_prediction=0.3,
            ctx=ctx,
            new_cat_pattern=pattern,
            is_default_category=False,
            data_days=60,
        )
        assert result.stage == "round_frozen_reorder"
        assert result.qty == 24  # 1 box × 24
        assert result.selected == "frozen_bypass"

    @pytest.mark.unit
    def test_round_non_frozen_goes_to_surplus_zero(self):
        """비냉동(049)은 바이패스 안 됨 → surplus_zero 또는 ceil"""
        from prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor.__new__(ImprovedPredictor)
        predictor.store_id = "46513"

        product = {
            "item_cd": "BEER001",
            "item_nm": "맥주 24입",
            "mid_cd": "049",
        }
        ctx = {}

        pattern = self._make_pattern(max_stock=5.0)

        result = predictor._round_to_order_unit(
            order_qty=0.3,
            order_unit=24,
            mid_cd="049",
            product=product,
            daily_avg=0.3,
            current_stock=0,
            pending_qty=0,
            safety_stock=0.5,
            adjusted_prediction=0.3,
            ctx=ctx,
            new_cat_pattern=pattern,
            is_default_category=False,
            data_days=60,
        )
        # 비냉동은 frozen_reorder 스테이지가 아닌 다른 스테이지
        assert result.stage != "round_frozen_reorder"
