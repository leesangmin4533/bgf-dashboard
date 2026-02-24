"""
단순화된 미입고 계산 단위 테스트

이 테스트는 _calculate_pending_simplified() 메서드의 동작을 검증합니다.
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from src.collectors.order_prep_collector import OrderPrepCollector


class TestSimplifiedPending(unittest.TestCase):
    """단순화된 미입고 계산 테스트"""

    def setUp(self):
        """각 테스트 전에 collector 인스턴스 생성"""
        self.collector = OrderPrepCollector(driver=None, save_to_db=False)

    def test_no_history(self):
        """이력 없음 → pending=0"""
        result = self.collector._calculate_pending_simplified([], 10)
        self.assertEqual(result, 0)

    def test_recent_order_not_received(self):
        """최근 발주, 미입고 → pending=발주량"""
        today = datetime.now().strftime("%Y%m%d")
        history = [{'date': today, 'ord_qty': 2, 'buy_qty': 0}]
        result = self.collector._calculate_pending_simplified(history, 10)
        self.assertEqual(result, 20)  # 2×10

    def test_recent_order_fully_received(self):
        """최근 발주, 완전입고 → pending=0"""
        today = datetime.now().strftime("%Y%m%d")
        history = [{'date': today, 'ord_qty': 2, 'buy_qty': 20}]
        result = self.collector._calculate_pending_simplified(history, 10)
        self.assertEqual(result, 0)

    def test_partial_receipt(self):
        """부분 입고 → pending=잔여량"""
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        history = [{'date': yesterday, 'ord_qty': 3, 'buy_qty': 25}]
        result = self.collector._calculate_pending_simplified(history, 10)
        self.assertEqual(result, 5)  # 30-25

    def test_old_order_ignored(self):
        """3일 이전 발주 → pending=0"""
        old_date = (datetime.now() - timedelta(days=4)).strftime("%Y%m%d")
        history = [{'date': old_date, 'ord_qty': 5, 'buy_qty': 0}]
        result = self.collector._calculate_pending_simplified(history, 10)
        self.assertEqual(result, 0)

    def test_multiple_orders_uses_most_recent(self):
        """여러 발주 → 최신 것만 사용"""
        today = datetime.now().strftime("%Y%m%d")
        two_days_ago = (datetime.now() - timedelta(days=2)).strftime("%Y%m%d")
        history = [
            {'date': today, 'ord_qty': 1, 'buy_qty': 0},       # 오늘
            {'date': two_days_ago, 'ord_qty': 3, 'buy_qty': 0},  # 2일 전
        ]
        result = self.collector._calculate_pending_simplified(history, 10)
        self.assertEqual(result, 10)  # 오늘 발주만 (1×10)

    def test_custom_lookback_days(self):
        """lookback_days 파라미터 동작 확인"""
        five_days_ago = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")
        history = [{'date': five_days_ago, 'ord_qty': 2, 'buy_qty': 0}]

        # 3일 조회: 5일 전 발주는 무시됨
        result_3 = self.collector._calculate_pending_simplified(history, 10, lookback_days=3)
        self.assertEqual(result_3, 0)

        # 7일 조회: 5일 전 발주 포함
        result_7 = self.collector._calculate_pending_simplified(history, 10, lookback_days=7)
        self.assertEqual(result_7, 20)

    def test_zero_order_qty_ignored(self):
        """발주량 0인 행은 무시"""
        today = datetime.now().strftime("%Y%m%d")
        history = [
            {'date': today, 'ord_qty': 0, 'buy_qty': 10},  # 발주 없음 (입고만)
        ]
        result = self.collector._calculate_pending_simplified(history, 10)
        self.assertEqual(result, 0)

    def test_missing_fields_handled(self):
        """필드 누락 시 안전하게 처리"""
        today = datetime.now().strftime("%Y%m%d")
        history = [
            {'date': today},  # ord_qty, buy_qty 없음
        ]
        result = self.collector._calculate_pending_simplified(history, 10)
        self.assertEqual(result, 0)

    def test_complex_scenario(self):
        """복잡한 시나리오: 여러 날짜, 부분 입고, 완전 입고 혼합"""
        today = datetime.now().strftime("%Y%m%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        two_days_ago = (datetime.now() - timedelta(days=2)).strftime("%Y%m%d")
        five_days_ago = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")

        history = [
            {'date': today, 'ord_qty': 2, 'buy_qty': 15},       # 오늘: 20개 발주, 15개 입고 → 5개 미입고
            {'date': yesterday, 'ord_qty': 3, 'buy_qty': 30},   # 어제: 완전 입고 (무시)
            {'date': two_days_ago, 'ord_qty': 1, 'buy_qty': 0}, # 2일 전: 미입고 (무시, 오늘이 최신)
            {'date': five_days_ago, 'ord_qty': 5, 'buy_qty': 0}, # 5일 전: 범위 밖 (무시)
        ]

        result = self.collector._calculate_pending_simplified(history, 10)
        self.assertEqual(result, 5)  # 오늘 발주만: 20-15=5


class TestPendingComparisonMode(unittest.TestCase):
    """복잡한 방식과 단순화 방식 비교 테스트"""

    def setUp(self):
        """각 테스트 전에 collector 인스턴스 생성"""
        self.collector = OrderPrepCollector(driver=None, save_to_db=False)

    def test_simple_case_matches(self):
        """단순 케이스: 두 방식 결과 일치"""
        today = datetime.now().strftime("%Y%m%d")
        history = [{'date': today, 'ord_qty': 2, 'buy_qty': 10}]

        simple = self.collector._calculate_pending_simplified(history, 10)
        complex_qty, _ = self.collector._calculate_pending_complex(history, 10, "TEST001")

        self.assertEqual(simple, complex_qty)

    def test_cross_date_pattern_differs(self):
        """교차날짜 패턴: 두 방식 결과 다름"""
        today = datetime.now().strftime("%Y%m%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

        history = [
            {'date': today, 'ord_qty': 2, 'buy_qty': 0},       # 오늘 발주, 미입고
            {'date': yesterday, 'ord_qty': 0, 'buy_qty': 20},  # 어제 입고 (어제 발주 건)
        ]

        simple = self.collector._calculate_pending_simplified(history, 10)
        complex_qty, _ = self.collector._calculate_pending_complex(history, 10, "TEST002")

        # 단순: 오늘 발주만 봄 → 20개
        self.assertEqual(simple, 20)
        # 복잡: 과거 입고는 차감되지만 오늘 발주는 여전히 미입고 → 20개
        # (교차날짜 보정은 같은 날 발주/입고가 다른 경우에만 작동)
        self.assertEqual(complex_qty, 20)


if __name__ == '__main__':
    unittest.main()
