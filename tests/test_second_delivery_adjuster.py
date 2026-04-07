"""second_delivery_adjuster.execute_boost_orders 회귀 테스트 (d1-bgf-collector-import-fix)"""

from unittest.mock import MagicMock, patch

from src.analysis.second_delivery_adjuster import (
    AdjustmentResult,
    ItemBoostOrder,
    execute_boost_orders,
)
from src.order.order_executor import OrderExecutor


def _make_result(boost_orders):
    return AdjustmentResult(
        run_at="2026-04-07T14:00:00",
        store_id="49965",
        today="2026-04-07",
        total_second_items=len(boost_orders),
        morning_data_available=1,
        boost_targets=len(boost_orders),
        reduce_logged=0,
        boost_orders=boost_orders,
    )


class TestExecuteBoostOrders:
    """execute_boost_orders 회귀 (d1-bgf-collector-import-fix)"""

    def test_calls_execute_order_not_execute_single_order(self):
        """회귀 핵심: execute_order 호출 + spec mock으로 메서드명 오기 방지"""
        mock_executor = MagicMock(spec=OrderExecutor)
        mock_executor.execute_order.return_value = {"success": True}

        result = _make_result([ItemBoostOrder(item_cd="A001", delta_qty=2)])

        with patch(
            "src.order.order_executor.OrderExecutor",
            return_value=mock_executor,
        ), patch("src.analysis.second_delivery_adjuster._get_conn") as mock_conn, patch(
            "src.analysis.second_delivery_adjuster._ensure_log_table"
        ):
            mock_conn.return_value = MagicMock()
            ret = execute_boost_orders(result, driver=MagicMock())

        assert ret == {"executed": 1, "failed": 0}
        mock_executor.execute_order.assert_called_once_with(item_cd="A001", qty=2)
        # spec=OrderExecutor 이므로 만약 execute_single_order를 호출했다면
        # AttributeError로 실패했을 것 (회귀 방지의 핵심)

    def test_handles_failure_increments_failed(self):
        """execute_order가 success=False 반환 시 failed += 1"""
        mock_executor = MagicMock(spec=OrderExecutor)
        mock_executor.execute_order.return_value = {
            "success": False,
            "message": "menu navigation failed",
        }

        result = _make_result([ItemBoostOrder(item_cd="A002", delta_qty=3)])

        with patch(
            "src.order.order_executor.OrderExecutor",
            return_value=mock_executor,
        ), patch("src.analysis.second_delivery_adjuster._get_conn") as mock_conn, patch(
            "src.analysis.second_delivery_adjuster._ensure_log_table"
        ):
            mock_conn.return_value = MagicMock()
            ret = execute_boost_orders(result, driver=MagicMock())

        assert ret == {"executed": 0, "failed": 1}
        mock_executor.execute_order.assert_called_once()

    def test_empty_boost_orders_skips_executor_init(self):
        """boost_orders=[] 시 OrderExecutor 초기화 안 함"""
        result = _make_result([])

        with patch(
            "src.order.order_executor.OrderExecutor"
        ) as mock_executor_cls:
            ret = execute_boost_orders(result, driver=MagicMock())

        assert ret == {"executed": 0, "failed": 0}
        mock_executor_cls.assert_not_called()
