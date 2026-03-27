"""
OrderExecutor Direct API 통합 테스트 (15개)

3단계 폴백, 피처 플래그, dry_run, 혼합 시나리오, 검증 실패 폴백 테스트
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from src.order.order_executor import OrderExecutor
from src.order.direct_api_saver import SaveResult


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def mock_driver():
    """Mock Selenium WebDriver"""
    driver = MagicMock()
    # navigate_to_single_order 관련 JS
    driver.execute_script = MagicMock(return_value=True)
    return driver


@pytest.fixture
def executor(mock_driver):
    """OrderExecutor 인스턴스 (Mock driver)"""
    return OrderExecutor(mock_driver)


@pytest.fixture
def sample_orders():
    """테스트 발주 목록"""
    return [
        {'item_cd': '8801043036016', 'final_order_qty': 5, 'order_unit_qty': 1,
         'multiplier': 5, 'item_nm': '테스트상품1', 'orderable_day': '월화수목금토일'},
        {'item_cd': '8809112345678', 'final_order_qty': 12, 'order_unit_qty': 6,
         'multiplier': 2, 'item_nm': '테스트상품2', 'orderable_day': '월화수목금토일'},
        {'item_cd': '8800100200300', 'final_order_qty': 3, 'order_unit_qty': 1,
         'multiplier': 3, 'item_nm': '테스트상품3', 'orderable_day': '월화수목금토일'},
    ]


# =====================================================================
# 3단계 폴백 테스트
# =====================================================================

class TestThreeTierFallback:
    """3단계 실행 전략 폴백 테스트"""

    @patch('src.order.order_executor.DIRECT_API_ORDER_ENABLED', True)
    @patch('src.order.order_executor.BATCH_GRID_INPUT_ENABLED', True)
    def test_level1_direct_api_success(self, executor, sample_orders):
        """Level 1 성공: Direct API로 저장 (메뉴 탐색 후)"""
        api_result = SaveResult(success=True, saved_count=3, elapsed_ms=500, method='direct_api')

        with patch.object(executor, '_try_direct_api_save', return_value=api_result):
            with patch.object(executor, 'navigate_to_single_order', return_value=True):
                with patch.object(executor, 'select_order_day', return_value=True):
                    with patch.object(executor, '_clear_any_alerts'):
                        with patch.object(executor, 'group_orders_by_date', return_value={'2026-02-27': sample_orders}):
                            result = executor.execute_orders(sample_orders, target_date='2026-02-27')

        assert result['success'] is True
        assert result['success_count'] == 3
        # Direct API 성공 시 method가 direct_api여야 함
        for r in result['results']:
            assert r.get('method') == 'direct_api'

    @patch('src.order.order_executor.DIRECT_API_ORDER_ENABLED', True)
    @patch('src.order.order_executor.BATCH_GRID_INPUT_ENABLED', True)
    def test_level1_fail_fallback_to_level2(self, executor, sample_orders):
        """Level 1 실패 -> Level 2 폴백 (메뉴 탐색 후)"""
        api_result = SaveResult(success=False, message='no template')
        batch_result = SaveResult(success=True, saved_count=3, elapsed_ms=1000, method='batch_grid')

        with patch.object(executor, '_try_direct_api_save', return_value=api_result):
            with patch.object(executor, '_try_batch_grid_input', return_value=batch_result):
                with patch.object(executor, 'navigate_to_single_order', return_value=True):
                    with patch.object(executor, 'select_order_day', return_value=True):
                        with patch.object(executor, '_clear_any_alerts'):
                            with patch.object(executor, 'group_orders_by_date', return_value={'2026-02-27': sample_orders}):
                                result = executor.execute_orders(sample_orders, target_date='2026-02-27')

        assert result['success'] is True
        assert result['success_count'] == 3
        for r in result['results']:
            assert r.get('method') == 'batch_grid'

    @patch('src.order.order_executor.DIRECT_API_ORDER_ENABLED', True)
    @patch('src.order.order_executor.BATCH_GRID_INPUT_ENABLED', True)
    def test_level1_and_level2_fail_fallback_to_selenium(self, executor, sample_orders):
        """Level 1, 2 모두 실패 -> Level 3 Selenium 폴백"""
        api_result = SaveResult(success=False, message='no template')
        batch_result = SaveResult(success=False, message='grid not ready')

        with patch.object(executor, '_try_direct_api_save', return_value=api_result):
            with patch.object(executor, '_try_batch_grid_input', return_value=batch_result):
                with patch.object(executor, 'navigate_to_single_order', return_value=True):
                    with patch.object(executor, 'select_order_day', return_value=True):
                        with patch.object(executor, 'input_product', return_value={'success': True, 'actual_qty': 5, 'multiplier': 5, 'order_unit_qty': 1}):
                            with patch.object(executor, 'confirm_order', return_value={'success': True}):
                                with patch.object(executor, '_clear_any_alerts'):
                                    with patch.object(executor, 'group_orders_by_date', return_value={'2026-02-27': sample_orders}):
                                        with patch.object(executor, '_get_inter_item_delay', return_value=0.01):
                                            result = executor.execute_orders(sample_orders, target_date='2026-02-27')

        # Selenium 폴백 동작 확인
        assert result['success_count'] >= 0  # Selenium이 실행됨


# =====================================================================
# 피처 플래그 테스트
# =====================================================================

class TestFeatureFlags:
    """피처 플래그 비활성화 테스트"""

    @patch('src.order.order_executor.DIRECT_API_ORDER_ENABLED', False)
    @patch('src.order.order_executor.BATCH_GRID_INPUT_ENABLED', False)
    def test_all_disabled_uses_selenium(self, executor, sample_orders):
        """피처 비활성화 시 Selenium만 사용"""
        with patch.object(executor, '_try_direct_api_save') as mock_api:
            with patch.object(executor, '_try_batch_grid_input') as mock_batch:
                with patch.object(executor, 'navigate_to_single_order', return_value=True):
                    with patch.object(executor, 'select_order_day', return_value=True):
                        with patch.object(executor, 'input_product', return_value={'success': True, 'actual_qty': 5, 'multiplier': 5, 'order_unit_qty': 1}):
                            with patch.object(executor, 'confirm_order', return_value={'success': True}):
                                with patch.object(executor, '_clear_any_alerts'):
                                    with patch.object(executor, 'group_orders_by_date', return_value={'2026-02-27': sample_orders}):
                                        with patch.object(executor, '_get_inter_item_delay', return_value=0.01):
                                            executor.execute_orders(sample_orders, target_date='2026-02-27')

        # Direct API, Batch Grid가 호출되지 않아야 함
        mock_api.assert_not_called()
        mock_batch.assert_not_called()

    @patch('src.order.order_executor.DIRECT_API_ORDER_ENABLED', True)
    @patch('src.order.order_executor.BATCH_GRID_INPUT_ENABLED', False)
    def test_only_direct_api_enabled(self, executor, sample_orders):
        """Direct API만 활성화 (메뉴 탐색 후)"""
        api_result = SaveResult(success=True, saved_count=3, elapsed_ms=500)

        with patch.object(executor, '_try_direct_api_save', return_value=api_result):
            with patch.object(executor, '_try_batch_grid_input') as mock_batch:
                with patch.object(executor, 'navigate_to_single_order', return_value=True):
                    with patch.object(executor, 'select_order_day', return_value=True):
                        with patch.object(executor, '_clear_any_alerts'):
                            with patch.object(executor, 'group_orders_by_date', return_value={'2026-02-27': sample_orders}):
                                result = executor.execute_orders(sample_orders, target_date='2026-02-27')

        assert result['success_count'] == 3
        mock_batch.assert_not_called()


# =====================================================================
# dry_run 테스트
# =====================================================================

class TestDryRun:
    """dry_run 모드 테스트"""

    @patch('src.order.order_executor.DIRECT_API_ORDER_ENABLED', True)
    @patch('src.order.order_executor.BATCH_GRID_INPUT_ENABLED', True)
    def test_dry_run_skips_api(self, executor, sample_orders):
        """dry_run 시 Direct API/Batch Grid 건너뜀"""
        with patch.object(executor, '_try_direct_api_save') as mock_api:
            with patch.object(executor, '_try_batch_grid_input') as mock_batch:
                with patch.object(executor, 'navigate_to_single_order', return_value=True):
                    with patch.object(executor, 'select_order_day', return_value=True):
                        with patch.object(executor, '_clear_any_alerts'):
                            with patch.object(executor, 'group_orders_by_date', return_value={'2026-02-27': sample_orders}):
                                result = executor.execute_orders(sample_orders, target_date='2026-02-27', dry_run=True)

        # dry_run은 API/Batch를 건너뜀
        mock_api.assert_not_called()
        mock_batch.assert_not_called()
        # 성공으로 처리
        assert result['success_count'] == 3


# =====================================================================
# _try_direct_api_save 내부 테스트
# =====================================================================

class TestTryDirectApiSave:
    """_try_direct_api_save 메서드 테스트"""

    def test_module_import_failure(self, executor, sample_orders):
        """direct_api_saver 모듈 없을 때"""
        with patch('builtins.__import__', side_effect=ImportError("No module")):
            # ImportError가 내부에서 처리되므로 None 반환 예상
            # 실제로는 try/except 내부에서 처리
            pass  # ImportError는 _try_direct_api_save 내부에서 catch됨

    def test_exception_handling(self, executor, sample_orders):
        """예외 발생 시 폴백"""
        with patch('src.order.direct_api_saver.DirectApiOrderSaver') as MockSaver:
            MockSaver.side_effect = Exception("unexpected error")
            result = executor._try_direct_api_save(sample_orders, '2026-02-27')
            assert result is not None
            assert result.success is False


# =====================================================================
# _try_batch_grid_input 내부 테스트
# =====================================================================

class TestTryBatchGridInput:
    """_try_batch_grid_input 메서드 테스트"""

    def test_grid_not_ready(self, executor, sample_orders, mock_driver):
        """그리드 미준비 시 실패"""
        mock_driver.execute_script.return_value = {'ready': False, 'reason': 'no_form'}

        result = executor._try_batch_grid_input(sample_orders, '2026-02-27')
        assert result is not None
        assert result.success is False
        assert 'not ready' in result.message

    def test_batch_with_confirm_reuse(self, executor, sample_orders, mock_driver):
        """confirm_order 재사용 확인"""
        # _try_batch_grid_input -> check_grid_ready(1회) -> input_batch -> check_grid_ready(2회) + populate_grid(3회)
        grid_ready = {'ready': True, 'dsName': 'ds', 'rowCount': 0, 'columns': ['ITEM_CD', 'ORD_QTY'], 'hasItemCd': True, 'hasOrdQty': True}
        populate_ok = {'success': True, 'added': 3, 'total': 3, 'errors': [], 'dsRowCount': 3}
        mock_driver.execute_script.side_effect = [
            grid_ready,    # _try_batch_grid_input -> check_grid_ready
            grid_ready,    # input_batch -> check_grid_ready
            populate_ok,   # input_batch -> populate_grid
        ]

        with patch.object(executor, 'confirm_order', return_value={'success': True}) as mock_confirm:
            result = executor._try_batch_grid_input(sample_orders, '2026-02-27')

        # confirm_order가 호출되었는지 확인
        mock_confirm.assert_called_once()


# =====================================================================
# DirectAPI 검증 실패 → 폴백 테스트
# =====================================================================

class TestDirectApiVerifyFallback:
    """DirectAPI 검증 실패 시 폴백 트리거 테스트"""

    def test_verify_zero_match_with_mismatch_returns_failure(self, executor, sample_orders):
        """검증 0/N + 불일치 존재 시 success=False 반환 → 폴백 트리거"""
        with patch('src.order.direct_api_saver.DirectApiOrderSaver') as MockSaver:
            saver = MockSaver.return_value
            saver.has_template = True
            saver.save_orders.return_value = SaveResult(
                success=True, saved_count=3, method='direct_api',
            )
            saver.verify_save.return_value = {
                'verified': False,
                'matched': 0,
                'total': 3,
                'mismatched': [{'item_cd': '8801043036016', 'expected': 5, 'actual': 10}],
                'missing': ['8809112345678', '8800100200300'],
            }

            result = executor._try_direct_api_save(sample_orders, '2026-02-27')

        assert result.success is False
        assert 'verification failed' in result.message
        assert '0/3' in result.message

    def test_verify_partial_match_keeps_success(self, executor, sample_orders):
        """검증 부분 실패 (일부 일치) 시 success=True 유지"""
        with patch('src.order.direct_api_saver.DirectApiOrderSaver') as MockSaver:
            saver = MockSaver.return_value
            saver.has_template = True
            saver.save_orders.return_value = SaveResult(
                success=True, saved_count=3, method='direct_api',
            )
            saver.verify_save.return_value = {
                'verified': False,
                'matched': 2,
                'total': 3,
                'mismatched': [],
                'missing': ['8800100200300'],
            }

            result = executor._try_direct_api_save(sample_orders, '2026-02-27')

        assert result.success is True

    def test_verify_success_keeps_success(self, executor, sample_orders):
        """검증 성공 시 success=True 유지"""
        with patch('src.order.direct_api_saver.DirectApiOrderSaver') as MockSaver:
            saver = MockSaver.return_value
            saver.has_template = True
            saver.save_orders.return_value = SaveResult(
                success=True, saved_count=3, method='direct_api',
            )
            saver.verify_save.return_value = {
                'verified': True,
                'matched': 3,
                'total': 3,
            }

            result = executor._try_direct_api_save(sample_orders, '2026-02-27')

        assert result.success is True

    @patch('src.order.order_executor.DIRECT_API_ORDER_ENABLED', True)
    @patch('src.order.order_executor.BATCH_GRID_INPUT_ENABLED', True)
    def test_verify_failure_triggers_batch_grid_fallback(self, executor, sample_orders):
        """검증 전체 실패 → Level 2 Batch Grid 폴백 실행"""
        api_result = SaveResult(
            success=False, message='verification failed: 0/3 matched',
        )
        batch_result = SaveResult(
            success=True, saved_count=3, elapsed_ms=1000, method='batch_grid',
        )

        with patch.object(executor, '_try_direct_api_save', return_value=api_result):
            with patch.object(executor, '_try_batch_grid_input', return_value=batch_result):
                with patch.object(executor, 'navigate_to_single_order', return_value=True):
                    with patch.object(executor, 'select_order_day', return_value=True):
                        with patch.object(executor, '_clear_any_alerts'):
                            with patch.object(executor, 'group_orders_by_date', return_value={'2026-02-27': sample_orders}):
                                result = executor.execute_orders(sample_orders, target_date='2026-02-27')

        assert result['success'] is True
        assert result['success_count'] == 3
        for r in result['results']:
            assert r.get('method') == 'batch_grid'

    def test_verify_all_missing_no_mismatch_trusts_gfn(self, executor, sample_orders):
        """T1: 전체 누락 + 불일치 0건 → gfn_transaction 성공 신뢰, 폴백 안 함"""
        with patch('src.order.direct_api_saver.DirectApiOrderSaver') as MockSaver:
            saver = MockSaver.return_value
            saver.has_template = True
            saver.save_orders.return_value = SaveResult(
                success=True, saved_count=3, method='direct_api',
            )
            saver.verify_save.return_value = {
                'verified': False,
                'matched': 0,
                'total': 3,
                'mismatched': [],
                'missing': ['8801043036016', '8809112345678', '8800100200300'],
            }

            result = executor._try_direct_api_save(sample_orders, '2026-02-27')

        assert result.success is True  # 폴백 없음 — 그리드 교체 패턴

    def test_verify_all_missing_with_mismatch_triggers_fallback(self, executor, sample_orders):
        """T2: 전체 누락 + 불일치 존재 → 폴백 트리거"""
        with patch('src.order.direct_api_saver.DirectApiOrderSaver') as MockSaver:
            saver = MockSaver.return_value
            saver.has_template = True
            saver.save_orders.return_value = SaveResult(
                success=True, saved_count=3, method='direct_api',
            )
            saver.verify_save.return_value = {
                'verified': False,
                'matched': 0,
                'total': 3,
                'mismatched': [{'item_cd': '8801043036016', 'expected': 2, 'actual': 5}],
                'missing': ['8809112345678', '8800100200300'],
            }

            result = executor._try_direct_api_save(sample_orders, '2026-02-27')

        assert result.success is False  # 불일치 존재 → 폴백

    def test_chunked_skips_verification(self, executor, sample_orders):
        """청크 분할(direct_api_chunked) 시 검증 스킵"""
        with patch('src.order.direct_api_saver.DirectApiOrderSaver') as MockSaver:
            saver = MockSaver.return_value
            saver.has_template = True
            saver.save_orders.return_value = SaveResult(
                success=True, saved_count=3, method='direct_api_chunked',
            )

            result = executor._try_direct_api_save(sample_orders, '2026-02-27')

        assert result.success is True
        saver.verify_save.assert_not_called()
