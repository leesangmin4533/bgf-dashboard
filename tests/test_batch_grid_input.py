"""
BatchGridInputter 테스트 (10개)

그리드 상태 확인, 배치 입력, 저장 연동, 에러 핸들링 테스트
"""

import pytest
from unittest.mock import MagicMock, call

from src.order.batch_grid_input import BatchGridInputter
from src.order.direct_api_saver import SaveResult


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def mock_driver():
    """Mock Selenium WebDriver"""
    driver = MagicMock()
    return driver


@pytest.fixture
def inputter(mock_driver):
    """BatchGridInputter 인스턴스"""
    return BatchGridInputter(mock_driver)


@pytest.fixture
def sample_orders():
    """샘플 발주 목록"""
    return [
        {'item_cd': '8801043036016', 'final_order_qty': 5, 'order_unit_qty': 1, 'multiplier': 5},
        {'item_cd': '8809112345678', 'final_order_qty': 12, 'order_unit_qty': 6, 'multiplier': 2},
    ]


# =====================================================================
# 그리드 상태 확인 테스트
# =====================================================================

class TestCheckGridReady:
    """check_grid_ready() 테스트"""

    def test_grid_ready(self, inputter, mock_driver):
        """그리드 준비 완료"""
        mock_driver.execute_script.return_value = {
            'ready': True,
            'dsName': 'dsOrder',
            'rowCount': 0,
            'columns': ['ITEM_CD', 'ORD_QTY', 'ORD_UNIT_QTY'],
            'hasItemCd': True,
            'hasOrdQty': True,
        }

        result = inputter.check_grid_ready()
        assert result['ready'] is True
        assert result['hasItemCd'] is True
        assert result['hasOrdQty'] is True

    def test_grid_no_gdlist(self, inputter, mock_driver):
        """gdList 없음"""
        mock_driver.execute_script.return_value = {
            'ready': False,
            'reason': 'no_gdList',
        }

        result = inputter.check_grid_ready()
        assert result['ready'] is False

    def test_grid_exception(self, inputter, mock_driver):
        """JS 실행 예외"""
        mock_driver.execute_script.side_effect = Exception("JS error")

        result = inputter.check_grid_ready()
        assert result['ready'] is False
        assert 'exception' in result['reason']


# =====================================================================
# 그리드 배치 입력 테스트
# =====================================================================

class TestPopulateGrid:
    """populate_grid() 테스트"""

    def test_populate_success(self, inputter, mock_driver, sample_orders):
        """배치 입력 성공"""
        mock_driver.execute_script.return_value = {
            'success': True,
            'added': 2,
            'total': 2,
            'errors': [],
            'dsRowCount': 2,
        }

        result = inputter.populate_grid(sample_orders)
        assert result['success'] is True
        assert result['added'] == 2

    def test_populate_empty_orders(self, inputter):
        """빈 주문 목록"""
        result = inputter.populate_grid([])
        assert result['success'] is True
        assert result['added'] == 0

    def test_populate_partial_failure(self, inputter, mock_driver, sample_orders):
        """부분 실패"""
        mock_driver.execute_script.return_value = {
            'success': True,
            'added': 1,
            'total': 2,
            'errors': [{'item_cd': '8809112345678', 'error': 'setColumn failed'}],
            'dsRowCount': 1,
        }

        result = inputter.populate_grid(sample_orders)
        assert result['success'] is True
        assert result['added'] == 1
        assert len(result['errors']) == 1

    def test_populate_calculates_multiplier(self, inputter, mock_driver):
        """multiplier 자동 계산"""
        orders = [{'item_cd': '1234', 'final_order_qty': 7, 'order_unit_qty': 3, 'multiplier': 0}]
        mock_driver.execute_script.return_value = {'success': True, 'added': 1, 'total': 1, 'errors': [], 'dsRowCount': 1}

        result = inputter.populate_grid(orders)
        assert result['success'] is True

        # JS에 전달된 items 확인
        call_args = mock_driver.execute_script.call_args
        items_arg = call_args[0][1]  # 두 번째 인자가 items
        assert items_arg[0]['multiplier'] == 3  # 7/3 올림 = 3


# =====================================================================
# 배치 입력 + 저장 테스트
# =====================================================================

class TestInputBatch:
    """input_batch() 테스트"""

    def test_input_batch_success(self, inputter, mock_driver, sample_orders):
        """배치 입력 + 저장 성공"""
        # check_grid_ready
        mock_driver.execute_script.side_effect = [
            # check_grid_ready
            {'ready': True, 'dsName': 'ds', 'rowCount': 0, 'columns': ['ITEM_CD', 'ORD_QTY'], 'hasItemCd': True, 'hasOrdQty': True},
            # populate_grid
            {'success': True, 'added': 2, 'total': 2, 'errors': [], 'dsRowCount': 2},
        ]

        confirm_fn = MagicMock(return_value={'success': True, 'method': 'text_search'})

        result = inputter.input_batch(sample_orders, '20260227', confirm_fn=confirm_fn)
        assert result.success is True
        assert result.saved_count == 2
        assert result.method == 'batch_grid'
        confirm_fn.assert_called_once()

    def test_input_batch_grid_not_ready(self, inputter, mock_driver, sample_orders):
        """그리드 미준비"""
        mock_driver.execute_script.return_value = {'ready': False, 'reason': 'no_gdList'}

        result = inputter.input_batch(sample_orders, '20260227')
        assert result.success is False
        assert 'not ready' in result.message

    def test_input_batch_empty_orders(self, inputter):
        """빈 주문"""
        result = inputter.input_batch([], '20260227')
        assert result.success is True
        assert result.saved_count == 0


# =====================================================================
# 그리드 읽기/초기화 테스트
# =====================================================================

class TestGridOperations:
    """그리드 읽기/초기화 테스트"""

    def test_read_grid_state(self, inputter, mock_driver):
        """그리드 상태 읽기"""
        mock_driver.execute_script.return_value = [
            {'ITEM_CD': '1234', 'ORD_QTY': '5'},
            {'ITEM_CD': '5678', 'ORD_QTY': '3'},
        ]

        result = inputter.read_grid_state()
        assert len(result) == 2
        assert result[0]['ITEM_CD'] == '1234'

    def test_clear_grid(self, inputter, mock_driver):
        """그리드 초기화"""
        mock_driver.execute_script.return_value = True

        result = inputter.clear_grid()
        assert result is True
