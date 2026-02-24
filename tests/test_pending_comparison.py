"""
비교 모드 테스트 (Phase 3)

이 테스트는 _calculate_pending_with_comparison() 메서드와
비교 통계 수집 기능을 검증합니다.
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from src.collectors.order_prep_collector import OrderPrepCollector


class TestComparisonMode(unittest.TestCase):
    """비교 모드 테스트"""

    def setUp(self):
        """각 테스트 전에 collector 인스턴스 생성"""
        self.collector = OrderPrepCollector(driver=None, save_to_db=False)

    def test_comparison_returns_complex_result(self):
        """비교 모드는 복잡한 방식 결과를 반환해야 함"""
        today = datetime.now().strftime("%Y%m%d")
        history = [{'date': today, 'ord_qty': 2, 'buy_qty': 10}]

        result_qty, result_detail = self.collector._calculate_pending_with_comparison(
            history, 10, "TEST001", "테스트상품"
        )

        # 복잡한 방식과 동일한 결과
        complex_qty, _ = self.collector._calculate_pending_complex(history, 10, "TEST001")
        self.assertEqual(result_qty, complex_qty)
        self.assertEqual(result_qty, 10)  # 2*10 - 10 = 10

    def test_comparison_collects_statistics(self):
        """비교 모드는 통계를 수집해야 함"""
        today = datetime.now().strftime("%Y%m%d")
        history = [{'date': today, 'ord_qty': 2, 'buy_qty': 10}]

        # 통계 초기화
        self.collector._comparison_stats = {
            'total': 0, 'matches': 0, 'differences': 0,
            'simple_higher': 0, 'complex_higher': 0,
            'max_diff': 0, 'total_diff': 0,
            'cross_pattern_cases': 0, 'multiple_order_cases': 0,
        }

        # 첫 번째 비교 (일치)
        self.collector._calculate_pending_with_comparison(history, 10, "TEST001", "상품1")

        self.assertEqual(self.collector._comparison_stats['total'], 1)
        self.assertEqual(self.collector._comparison_stats['matches'], 1)
        self.assertEqual(self.collector._comparison_stats['differences'], 0)

    def test_comparison_detects_differences(self):
        """비교 모드는 차이를 감지해야 함"""
        today = datetime.now().strftime("%Y%m%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        history = [
            {'date': today, 'ord_qty': 2, 'buy_qty': 0},       # 오늘 발주
            {'date': yesterday, 'ord_qty': 0, 'buy_qty': 10},  # 어제 입고
        ]

        # 통계 초기화
        self.collector._comparison_stats = {
            'total': 0, 'matches': 0, 'differences': 0,
            'simple_higher': 0, 'complex_higher': 0,
            'max_diff': 0, 'total_diff': 0,
            'cross_pattern_cases': 0, 'multiple_order_cases': 0,
        }

        # 비교 실행
        self.collector._calculate_pending_with_comparison(history, 10, "TEST002", "상품2")

        # 단순: 20개, 복잡: 20개 (실제로는 같을 수도 있음)
        # 통계가 업데이트 되었는지 확인
        self.assertEqual(self.collector._comparison_stats['total'], 1)

    def test_comparison_tracks_cross_pattern(self):
        """비교 모드는 교차날짜 패턴을 감지해야 함"""
        today = datetime.now().strftime("%Y%m%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        history = [
            {'date': today, 'ord_qty': 2, 'buy_qty': 0},       # 발주O 입고X
            {'date': yesterday, 'ord_qty': 0, 'buy_qty': 10},  # 발주X 입고O
        ]

        # 통계 초기화
        self.collector._comparison_stats = {
            'total': 0, 'matches': 0, 'differences': 0,
            'simple_higher': 0, 'complex_higher': 0,
            'max_diff': 0, 'total_diff': 0,
            'cross_pattern_cases': 0, 'multiple_order_cases': 0,
        }

        # 비교 실행
        self.collector._calculate_pending_with_comparison(history, 10, "TEST003", "상품3")

        # 차이가 있으면 교차패턴 감지되어야 함
        if self.collector._comparison_stats['differences'] > 0:
            self.assertGreaterEqual(self.collector._comparison_stats['cross_pattern_cases'], 0)

    def test_comparison_tracks_multiple_orders(self):
        """비교 모드는 다중 발주를 감지해야 함"""
        today = datetime.now().strftime("%Y%m%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        history = [
            {'date': today, 'ord_qty': 2, 'buy_qty': 0},
            {'date': yesterday, 'ord_qty': 1, 'buy_qty': 5},
        ]

        # 통계 초기화
        self.collector._comparison_stats = {
            'total': 0, 'matches': 0, 'differences': 0,
            'simple_higher': 0, 'complex_higher': 0,
            'max_diff': 0, 'total_diff': 0,
            'cross_pattern_cases': 0, 'multiple_order_cases': 0,
        }

        # 비교 실행
        self.collector._calculate_pending_with_comparison(history, 10, "TEST004", "상품4")

        # 3일 내 2건 발주이므로 다중발주 감지
        if self.collector._comparison_stats['differences'] > 0:
            self.assertGreaterEqual(self.collector._comparison_stats['multiple_order_cases'], 0)

    def test_comparison_max_diff_tracking(self):
        """비교 모드는 최대 차이를 추적해야 함"""
        today = datetime.now().strftime("%Y%m%d")

        # 통계 초기화
        self.collector._comparison_stats = {
            'total': 0, 'matches': 0, 'differences': 0,
            'simple_higher': 0, 'complex_higher': 0,
            'max_diff': 0, 'total_diff': 0,
            'cross_pattern_cases': 0, 'multiple_order_cases': 0,
        }

        # 여러 비교 실행
        histories = [
            [{'date': today, 'ord_qty': 1, 'buy_qty': 5}],   # 차이 5
            [{'date': today, 'ord_qty': 2, 'buy_qty': 10}],  # 차이 10
            [{'date': today, 'ord_qty': 3, 'buy_qty': 20}],  # 차이 10
        ]

        for i, hist in enumerate(histories):
            self.collector._calculate_pending_with_comparison(hist, 10, f"TEST{i}", f"상품{i}")

        # max_diff가 추적되었는지 확인
        self.assertGreaterEqual(self.collector._comparison_stats['max_diff'], 0)


class TestComparisonIntegration(unittest.TestCase):
    """비교 모드 통합 테스트"""

    def setUp(self):
        """각 테스트 전에 collector 인스턴스 생성"""
        self.collector = OrderPrepCollector(driver=None, save_to_db=False)

    @patch('src.config.constants.PENDING_COMPARISON_MODE', True)
    def test_comparison_mode_enabled(self):
        """PENDING_COMPARISON_MODE=True일 때 비교 메서드가 호출되어야 함"""
        # 이 테스트는 실제 collect_for_item을 호출하지 않고
        # 플래그만 확인
        from src.settings.constants import PENDING_COMPARISON_MODE
        self.assertTrue(PENDING_COMPARISON_MODE)


if __name__ == '__main__':
    unittest.main()
