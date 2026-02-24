"""
전체 품목 발주단위(order_unit_qty) 수집 테스트

- 발주현황조회 방식: click_all_radio(), collect_all_order_unit_qty()
- 홈 바코드 방식: collect_order_unit_via_home()
"""

from unittest.mock import MagicMock, patch

import pytest

from src.collectors.order_status_collector import OrderStatusCollector


# ================================================================
# Fixtures
# ================================================================


@pytest.fixture
def mock_driver():
    """Selenium WebDriver mock"""
    driver = MagicMock()
    return driver


@pytest.fixture
def collector(mock_driver):
    """OrderStatusCollector with mock driver"""
    return OrderStatusCollector(driver=mock_driver, store_id="99999")


# ================================================================
# click_all_radio() 테스트
# ================================================================


class TestClickAllRadio:

    def test_success_via_api(self, collector, mock_driver):
        """Strategy A (API) 성공"""
        mock_driver.execute_script.return_value = {
            "success": True, "method": "api", "value": "0"
        }
        assert collector.click_all_radio() is True

    def test_success_via_text_parent(self, collector, mock_driver):
        """Strategy B (텍스트 부모 클릭) 성공"""
        mock_driver.execute_script.return_value = {
            "success": True, "method": "text_parent"
        }
        assert collector.click_all_radio() is True

    def test_failure_returns_false(self, collector, mock_driver):
        """모든 전략 실패 시 False"""
        mock_driver.execute_script.return_value = {
            "success": False, "error": "rdGubun all radio not found"
        }
        assert collector.click_all_radio() is False

    def test_no_driver_returns_false(self):
        """드라이버 없으면 False"""
        c = OrderStatusCollector(driver=None)
        assert c.click_all_radio() is False

    def test_none_result_returns_false(self, collector, mock_driver):
        """execute_script이 None 반환 시 False"""
        mock_driver.execute_script.return_value = None
        assert collector.click_all_radio() is False


# ================================================================
# collect_all_order_unit_qty() 테스트
# ================================================================


class TestCollectAllOrderUnitQty:

    def test_success_returns_items(self, collector, mock_driver):
        """정상 수집 시 item_cd, order_unit_qty 포함 리스트 반환"""
        # click_all_radio 성공
        mock_driver.execute_script.side_effect = [
            {"success": True, "method": "api"},  # click_all_radio
            {  # dsResult 추출
                "items": [
                    {"item_cd": "1111", "item_nm": "A", "mid_cd": "001", "order_unit_qty": 6},
                    {"item_cd": "2222", "item_nm": "B", "mid_cd": "002", "order_unit_qty": 12},
                ],
                "total": 2,
            },
        ]

        with patch("src.collectors.order_status_collector.time"):
            items = collector.collect_all_order_unit_qty()

        assert items is not None
        assert len(items) == 2
        assert items[0]["item_cd"] == "1111"
        assert items[0]["order_unit_qty"] == 6
        assert items[1]["order_unit_qty"] == 12

    def test_radio_click_failure_returns_none(self, collector, mock_driver):
        """라디오 클릭 실패 시 None"""
        mock_driver.execute_script.return_value = {
            "success": False, "error": "not found"
        }
        result = collector.collect_all_order_unit_qty()
        assert result is None

    def test_dsresult_error_returns_none(self, collector, mock_driver):
        """dsResult 접근 실패 시 None"""
        mock_driver.execute_script.side_effect = [
            {"success": True, "method": "api"},  # click_all_radio
            {"error": "dsResult not found"},  # dsResult 실패
        ]

        with patch("src.collectors.order_status_collector.time"):
            result = collector.collect_all_order_unit_qty()

        assert result is None

    def test_empty_dsresult_returns_empty_list(self, collector, mock_driver):
        """dsResult 0건 시 빈 리스트"""
        mock_driver.execute_script.side_effect = [
            {"success": True, "method": "api"},
            {"items": [], "total": 0},
        ]

        with patch("src.collectors.order_status_collector.time"):
            result = collector.collect_all_order_unit_qty()

        assert result == []

    def test_no_driver_returns_none(self):
        """드라이버 없으면 None"""
        c = OrderStatusCollector(driver=None)
        assert c.collect_all_order_unit_qty() is None

    def test_default_order_unit_qty_is_1(self, collector, mock_driver):
        """ORD_UNIT_QTY가 0이거나 없으면 1로 반환"""
        mock_driver.execute_script.side_effect = [
            {"success": True, "method": "api"},
            {
                "items": [
                    {"item_cd": "3333", "item_nm": "C", "mid_cd": "003", "order_unit_qty": 1},
                ],
                "total": 1,
            },
        ]

        with patch("src.collectors.order_status_collector.time"):
            items = collector.collect_all_order_unit_qty()

        assert items[0]["order_unit_qty"] == 1


# ================================================================
# collect_order_unit_via_home() 테스트 (홈 바코드 검색 방식)
# ================================================================


class TestCollectOrderUnitViaHome:

    def test_no_driver_returns_empty(self):
        """드라이버 없으면 빈 리스트"""
        c = OrderStatusCollector(driver=None)
        assert c.collect_order_unit_via_home(["1111"]) == []

    def test_success_single_item(self, collector, mock_driver):
        """단일 상품 성공"""
        mock_driver.execute_script.side_effect = [
            {"success": True, "method": "nexacro"},     # _home_input_barcode
            {"success": True, "method": "nexacro_event"},  # _home_trigger_enter
            {"success": True, "method": "grid_click"},   # _home_click_quick_search
            {"found": True},                              # _home_wait_for_popup
            {"item_cd": "1111", "item_nm": "A", "order_unit_qty": 6},  # extract
            None,                                         # _home_close_popup
        ]

        with patch("src.collectors.order_status_collector.time"):
            items = collector.collect_order_unit_via_home(["1111"])

        assert len(items) == 1
        assert items[0]["item_cd"] == "1111"
        assert items[0]["order_unit_qty"] == 6

    def test_success_multiple_items(self, collector, mock_driver):
        """복수 상품 성공"""
        mock_driver.execute_script.side_effect = [
            # 상품 1
            {"success": True}, {"success": True}, {"success": True},
            {"found": True},
            {"item_cd": "1111", "item_nm": "A", "order_unit_qty": 6},
            None,
            # 상품 2
            {"success": True}, {"success": True}, {"success": True},
            {"found": True},
            {"item_cd": "2222", "item_nm": "B", "order_unit_qty": 12},
            None,
        ]

        with patch("src.collectors.order_status_collector.time"):
            items = collector.collect_order_unit_via_home(["1111", "2222"])

        assert len(items) == 2
        assert items[0]["order_unit_qty"] == 6
        assert items[1]["order_unit_qty"] == 12

    def test_barcode_input_failure_skips(self, collector, mock_driver):
        """바코드 입력 실패 시 해당 상품 건너뛰기"""
        mock_driver.execute_script.side_effect = [
            {"success": False},  # 첫 상품 바코드 입력 실패
            # 두 번째 상품 성공
            {"success": True}, {"success": True}, {"success": True},
            {"found": True},
            {"item_cd": "2222", "item_nm": "B", "order_unit_qty": 10},
            None,
        ]

        with patch("src.collectors.order_status_collector.time"):
            items = collector.collect_order_unit_via_home(["1111", "2222"])

        assert len(items) == 1
        assert items[0]["item_cd"] == "2222"

    def test_popup_not_shown_skips(self, collector, mock_driver):
        """팝업 미표시 시 해당 상품 건너뛰기"""
        # FR_POPUP_MAX_CHECKS번 found: False 반환 후 다음 상품
        popup_checks = [{"found": False}] * 10

        mock_driver.execute_script.side_effect = [
            {"success": True}, {"success": True}, {"success": True},
            *popup_checks,
            # 두 번째 상품 성공
            {"success": True}, {"success": True}, {"success": True},
            {"found": True},
            {"item_cd": "2222", "item_nm": "B", "order_unit_qty": 24},
            None,
        ]

        with patch("src.collectors.order_status_collector.time"):
            items = collector.collect_order_unit_via_home(["1111", "2222"])

        assert len(items) == 1
        assert items[0]["order_unit_qty"] == 24

    def test_extract_null_skips(self, collector, mock_driver):
        """추출 결과 None이면 결과에 미포함"""
        mock_driver.execute_script.side_effect = [
            {"success": True}, {"success": True}, {"success": True},
            {"found": True},
            None,    # extract 실패 (dsItemDetail 없음)
            None,    # close_popup
        ]

        with patch("src.collectors.order_status_collector.time"):
            items = collector.collect_order_unit_via_home(["1111"])

        assert len(items) == 0

    def test_empty_list_returns_empty(self, collector):
        """빈 리스트 입력 시 빈 결과"""
        items = collector.collect_order_unit_via_home([])
        assert items == []
