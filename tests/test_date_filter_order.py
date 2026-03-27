"""
date-filter-order 테스트: --order-date 날짜 필터링 기능
"""
import pytest
import argparse
from unittest.mock import MagicMock, patch


# ──────────────────────────────────────────────
# 1. OrderExecutor.execute_orders target_dates 필터링 (핵심 로직)
# ──────────────────────────────────────────────

class TestOrderExecutorDateFilter:
    """OrderExecutor.execute_orders()의 target_dates 필터링 테스트"""

    def _make_executor(self):
        """테스트용 OrderExecutor (최소 필드만 설정)"""
        from src.order.order_executor import OrderExecutor
        executor = OrderExecutor.__new__(OrderExecutor)
        executor.driver = MagicMock()
        executor.store_id = "46513"
        executor.direct_api_saver = None
        executor._last_selected_date = None
        return executor

    def _grouped_three_dates(self):
        return {
            "2026-03-01": [{"item_cd": "A", "item_nm": "상품A", "final_order_qty": 1}],
            "2026-03-02": [{"item_cd": "B", "item_nm": "상품B", "final_order_qty": 1}],
            "2026-03-03": [{"item_cd": "C", "item_nm": "상품C", "final_order_qty": 1}],
        }

    def test_target_dates_none_no_filter(self):
        """target_dates=None → 전체 날짜 통과 (필터 미적용)"""
        executor = self._make_executor()
        grouped = self._grouped_three_dates()

        with patch.object(executor, 'group_orders_by_date', return_value=grouped):
            with patch.object(executor, 'navigate_to_single_order', return_value=True):
                with patch.object(executor, 'select_order_day', return_value=True):
                    result = executor.execute_orders(
                        order_list=list(grouped.values())[0],
                        target_dates=None,
                        dry_run=True,
                    )
        # 3개 날짜 전부 처리 → grouped_by_date에 3개
        assert len(result.get("grouped_by_date", {})) == 3

    def test_target_dates_single_filter(self):
        """target_dates=['2026-03-01'] → 1개 날짜만 처리"""
        executor = self._make_executor()
        grouped = self._grouped_three_dates()

        with patch.object(executor, 'group_orders_by_date', return_value=grouped):
            with patch.object(executor, 'navigate_to_single_order', return_value=True):
                with patch.object(executor, 'select_order_day', return_value=True):
                    result = executor.execute_orders(
                        order_list=list(grouped.values())[0],
                        target_dates=["2026-03-01"],
                        dry_run=True,
                    )
        assert len(result.get("grouped_by_date", {})) == 1
        assert "2026-03-01" in result["grouped_by_date"]

    def test_target_dates_multi_filter(self):
        """target_dates=['2026-03-01', '2026-03-03'] → 2개 날짜만 처리"""
        executor = self._make_executor()
        grouped = self._grouped_three_dates()

        with patch.object(executor, 'group_orders_by_date', return_value=grouped):
            with patch.object(executor, 'navigate_to_single_order', return_value=True):
                with patch.object(executor, 'select_order_day', return_value=True):
                    result = executor.execute_orders(
                        order_list=list(grouped.values())[0],
                        target_dates=["2026-03-01", "2026-03-03"],
                        dry_run=True,
                    )
        assert len(result.get("grouped_by_date", {})) == 2
        assert "2026-03-02" not in result.get("grouped_by_date", {})

    def test_target_date_single_overrides_filter(self):
        """target_date(단일)가 있으면 target_dates 필터 무시"""
        executor = self._make_executor()

        with patch.object(executor, 'navigate_to_single_order', return_value=True):
            with patch.object(executor, 'select_order_day', return_value=True):
                result = executor.execute_orders(
                    order_list=[{"item_cd": "A", "item_nm": "A", "final_order_qty": 1}],
                    target_date="2026-03-01",
                    target_dates=["2026-03-02"],  # target_date가 우선
                    dry_run=True,
                )
        # target_date=03-01로 처리됨
        assert "2026-03-01" in result.get("grouped_by_date", {})

    def test_empty_target_dates_no_filter(self):
        """target_dates=[] (빈 리스트) → falsy → 필터 미적용"""
        executor = self._make_executor()
        grouped = self._grouped_three_dates()

        with patch.object(executor, 'group_orders_by_date', return_value=grouped):
            with patch.object(executor, 'navigate_to_single_order', return_value=True):
                with patch.object(executor, 'select_order_day', return_value=True):
                    result = executor.execute_orders(
                        order_list=list(grouped.values())[0],
                        target_dates=[],
                        dry_run=True,
                    )
        # 빈 리스트 → 필터 안됨 → 전체
        assert len(result.get("grouped_by_date", {})) == 3

    def test_nonexistent_date_filters_all(self):
        """target_dates에 없는 날짜만 → grouped가 비어서 0건"""
        executor = self._make_executor()
        grouped = self._grouped_three_dates()

        with patch.object(executor, 'group_orders_by_date', return_value=grouped):
            result = executor.execute_orders(
                order_list=list(grouped.values())[0],
                target_dates=["2026-04-01"],  # 존재하지 않는 날짜
                dry_run=True,
            )
        assert result.get("success_count", 0) == 0
        assert len(result.get("grouped_by_date", {})) == 0


# ──────────────────────────────────────────────
# 2. 파라미터 전파 테스트 (콜 체인)
# ──────────────────────────────────────────────

class TestCallChainPropagation:
    """target_dates가 콜 체인을 통과하는지 테스트"""

    def test_run_daily_order_passes_to_execute(self):
        """AutoOrderSystem.run_daily_order → execute에 target_dates 전달"""
        from src.order.auto_order import AutoOrderSystem

        system = AutoOrderSystem.__new__(AutoOrderSystem)
        system.driver = MagicMock()
        system.store_id = "46513"
        system.use_improved_predictor = True
        system._cut_items = set()

        with patch.object(system, 'execute', return_value={
            "success": True, "success_count": 0, "fail_count": 0
        }) as mock_execute:
            with patch('src.order.auto_order.close_alerts', return_value=0):
                with patch('src.order.auto_order.close_all_popups', return_value=0):
                    system.run_daily_order(
                        dry_run=True,
                        target_dates=["2026-03-01"],
                    )

        mock_execute.assert_called_once()
        assert mock_execute.call_args.kwargs.get("target_dates") == ["2026-03-01"]

    def test_stage_auto_order_passes_to_run_daily_order(self):
        """DailyOrderFlow._stage_auto_order → run_daily_order에 target_dates 전달"""
        from src.application.use_cases.daily_order_flow import DailyOrderFlow
        from src.settings.store_context import StoreContext

        ctx = MagicMock(spec=StoreContext)
        ctx.store_id = "46513"

        flow = DailyOrderFlow.__new__(DailyOrderFlow)
        flow.driver = MagicMock()
        flow.store_ctx = ctx
        flow.use_improved_predictor = True
        flow._result = {"stages": {}}

        mock_system = MagicMock()
        mock_system.run_daily_order.return_value = {
            "success": True, "success_count": 5, "fail_count": 0
        }

        with patch('src.order.auto_order.AutoOrderSystem', return_value=mock_system):
            flow._stage_auto_order(
                dry_run=True,
                target_dates=["2026-03-01"],
            )

        mock_system.run_daily_order.assert_called_once()
        assert mock_system.run_daily_order.call_args.kwargs.get("target_dates") == ["2026-03-01"]


# ──────────────────────────────────────────────
# 3. CLI 인자 파싱 테스트
# ──────────────────────────────────────────────

class TestCLIArgs:
    """run_scheduler.py CLI --order-date 파싱"""

    def test_order_date_single(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--order-date", type=str, action="append", default=None)
        args = parser.parse_args(["--order-date", "2026-03-01"])
        assert args.order_date == ["2026-03-01"]

    def test_order_date_multiple(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--order-date", type=str, action="append", default=None)
        args = parser.parse_args(["--order-date", "2026-03-01", "--order-date", "2026-03-02"])
        assert args.order_date == ["2026-03-01", "2026-03-02"]

    def test_order_date_not_specified(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--order-date", type=str, action="append", default=None)
        args = parser.parse_args([])
        assert args.order_date is None
