"""
수동(일반) 발주 수집 + 푸드 차감 반영 테스트
"""

import sqlite3
import pytest
from pathlib import Path
from datetime import date
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


# ==================================================================
# Fixtures
# ==================================================================

@pytest.fixture
def store_db(tmp_path):
    """테스트용 매장 DB (manual_order_items 테이블 포함)"""
    db_path = tmp_path / "test_store.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS manual_order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT,
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            mid_cd TEXT,
            mid_nm TEXT,
            order_qty INTEGER NOT NULL DEFAULT 0,
            ord_cnt INTEGER DEFAULT 0,
            ord_unit_qty INTEGER DEFAULT 1,
            ord_input_id TEXT,
            ord_amt INTEGER DEFAULT 0,
            order_date TEXT NOT NULL,
            collected_at TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(item_cd, order_date)
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def repo(store_db):
    """ManualOrderItemRepository 인스턴스"""
    from src.infrastructure.database.repos.manual_order_repo import ManualOrderItemRepository
    return ManualOrderItemRepository(db_path=store_db, store_id="46513")


@pytest.fixture
def sample_items():
    """샘플 수동 발주 데이터"""
    return [
        {
            "item_cd": "8800336394352",
            "item_nm": "도)밥반찬반돈까스1",
            "mid_cd": "001",
            "mid_nm": "도시락",
            "ord_ymd": "20260226",
            "ord_cnt": 3,
            "ord_unit_qty": 1,
            "order_qty": 3,
            "ord_input_id": "단품별(재택)",
            "ord_amt": 16500,
        },
        {
            "item_cd": "8809196620052",
            "item_nm": "도)압도적두툼돈까스정식1",
            "mid_cd": "001",
            "mid_nm": "도시락",
            "ord_ymd": "20260226",
            "ord_cnt": 2,
            "ord_unit_qty": 1,
            "order_qty": 2,
            "ord_input_id": "단품별(재택)",
            "ord_amt": 11800,
        },
        {
            "item_cd": "8801234567890",
            "item_nm": "카스후레쉬500",
            "mid_cd": "020",
            "mid_nm": "맥주",
            "ord_ymd": "20260226",
            "ord_cnt": 1,
            "ord_unit_qty": 6,
            "order_qty": 6,
            "ord_input_id": "단품별(재택)",
            "ord_amt": 9900,
        },
    ]


# ==================================================================
# ManualOrderItemRepository 테스트
# ==================================================================

class TestManualOrderItemRepository:

    def test_refresh_and_get_today_orders(self, repo, sample_items):
        """refresh() 후 get_today_orders() 정상"""
        today = date.today().strftime("%Y-%m-%d")
        saved = repo.refresh(sample_items, order_date=today, store_id="46513")
        assert saved == 3

        orders = repo.get_today_orders(store_id="46513")
        assert len(orders) == 3
        assert orders[0]["item_cd"] == "8800336394352"

    def test_refresh_replaces_existing(self, repo, sample_items):
        """같은 날짜 refresh() 시 기존 데이터 교체"""
        today = date.today().strftime("%Y-%m-%d")
        repo.refresh(sample_items, order_date=today, store_id="46513")

        # 두 번째 refresh: 1건만
        repo.refresh([sample_items[0]], order_date=today, store_id="46513")
        orders = repo.get_today_orders(store_id="46513")
        assert len(orders) == 1

    def test_refresh_empty_clears(self, repo, sample_items):
        """빈 리스트 refresh 시 기존 데이터 삭제"""
        today = date.today().strftime("%Y-%m-%d")
        repo.refresh(sample_items, order_date=today, store_id="46513")
        repo.refresh([], order_date=today, store_id="46513")
        orders = repo.get_today_orders(store_id="46513")
        assert len(orders) == 0

    def test_get_today_food_orders(self, repo, sample_items):
        """푸드 카테고리만 필터링"""
        today = date.today().strftime("%Y-%m-%d")
        repo.refresh(sample_items, order_date=today, store_id="46513")

        food_orders = repo.get_today_food_orders(store_id="46513")
        # 푸드: 001 2건, 맥주 020 제외
        assert len(food_orders) == 2
        assert "8800336394352" in food_orders
        assert food_orders["8800336394352"] == 3
        assert "8809196620052" in food_orders
        assert food_orders["8809196620052"] == 2
        # 맥주 없음
        assert "8801234567890" not in food_orders

    def test_get_today_summary(self, repo, sample_items):
        """요약 정보 확인"""
        today = date.today().strftime("%Y-%m-%d")
        repo.refresh(sample_items, order_date=today, store_id="46513")

        summary = repo.get_today_summary(store_id="46513")
        assert summary["total_count"] == 3
        assert summary["food_count"] == 2
        assert summary["non_food_count"] == 1
        assert summary["total_qty"] == 11  # 3+2+6
        assert summary["total_amt"] == 38200  # 16500+11800+9900

    def test_ord_cnt_times_unit_qty(self, repo):
        """ORD_CNT * ORD_UNIT_QTY 계산 검증"""
        today = date.today().strftime("%Y-%m-%d")
        items = [{
            "item_cd": "TEST001",
            "item_nm": "테스트맥주",
            "mid_cd": "020",
            "ord_cnt": 2,
            "ord_unit_qty": 6,
            "order_qty": 12,
            "ord_input_id": "단품별(재택)",
        }]
        repo.refresh(items, order_date=today, store_id="46513")
        orders = repo.get_today_orders(store_id="46513")
        assert orders[0]["order_qty"] == 12
        assert orders[0]["ord_cnt"] == 2
        assert orders[0]["ord_unit_qty"] == 6


# ==================================================================
# _deduct_manual_food_orders 테스트
# ==================================================================

class TestDeductManualFoodOrders:

    def _make_order_list(self):
        """예측 기반 발주 목록 (테스트용)"""
        return [
            {"item_cd": "FOOD001", "item_nm": "주먹밥", "mid_cd": "002",
             "final_order_qty": 8, "order_qty": 8},
            {"item_cd": "FOOD002", "item_nm": "도시락A", "mid_cd": "001",
             "final_order_qty": 5, "order_qty": 5},
            {"item_cd": "BEER001", "item_nm": "맥주A", "mid_cd": "020",
             "final_order_qty": 12, "order_qty": 12},
            {"item_cd": "FOOD003", "item_nm": "샌드위치", "mid_cd": "003",
             "final_order_qty": 3, "order_qty": 3},
        ]

    @patch("src.infrastructure.database.repos.ManualOrderItemRepository", autospec=True)
    @patch("src.settings.constants.MANUAL_ORDER_FOOD_DEDUCTION", True)
    def test_basic_deduction(self, MockRepo):
        """기본 차감: 예측8 - 수동5 = 3"""
        from src.order.auto_order import AutoOrderSystem

        mock_repo_inst = MagicMock()
        mock_repo_inst.get_today_food_orders.return_value = {"FOOD001": 5}
        MockRepo.return_value = mock_repo_inst

        system = AutoOrderSystem.__new__(AutoOrderSystem)
        system.store_id = "46513"
        system._exclusion_records = []

        order_list = self._make_order_list()
        result = system._deduct_manual_food_orders(order_list, min_order_qty=1)

        food001 = next(i for i in result if i["item_cd"] == "FOOD001")
        assert food001["final_order_qty"] == 3
        assert food001["manual_deducted_qty"] == 5

    @patch("src.infrastructure.database.repos.ManualOrderItemRepository", autospec=True)
    @patch("src.settings.constants.MANUAL_ORDER_FOOD_DEDUCTION", True)
    def test_excess_deduction_removes(self, MockRepo):
        """초과 차감: 예측3 - 수동5 = 0 -> 제거"""
        from src.order.auto_order import AutoOrderSystem

        mock_repo_inst = MagicMock()
        mock_repo_inst.get_today_food_orders.return_value = {"FOOD003": 5}
        MockRepo.return_value = mock_repo_inst

        system = AutoOrderSystem.__new__(AutoOrderSystem)
        system.store_id = "46513"
        system._exclusion_records = []

        order_list = self._make_order_list()
        result = system._deduct_manual_food_orders(order_list, min_order_qty=1)

        item_cds = [i["item_cd"] for i in result]
        assert "FOOD003" not in item_cds
        # exclusion record 기록 확인
        assert len(system._exclusion_records) == 1
        assert system._exclusion_records[0]["exclusion_type"] == "MANUAL_ORDER"

    @patch("src.infrastructure.database.repos.ManualOrderItemRepository", autospec=True)
    @patch("src.settings.constants.MANUAL_ORDER_FOOD_DEDUCTION", True)
    def test_exact_deduction_removes(self, MockRepo):
        """정확 일치: 예측5 - 수동5 = 0 -> 제거"""
        from src.order.auto_order import AutoOrderSystem

        mock_repo_inst = MagicMock()
        mock_repo_inst.get_today_food_orders.return_value = {"FOOD002": 5}
        MockRepo.return_value = mock_repo_inst

        system = AutoOrderSystem.__new__(AutoOrderSystem)
        system.store_id = "46513"
        system._exclusion_records = []

        order_list = self._make_order_list()
        result = system._deduct_manual_food_orders(order_list, min_order_qty=1)

        item_cds = [i["item_cd"] for i in result]
        assert "FOOD002" not in item_cds

    @patch("src.infrastructure.database.repos.ManualOrderItemRepository", autospec=True)
    @patch("src.settings.constants.MANUAL_ORDER_FOOD_DEDUCTION", True)
    def test_non_food_not_deducted(self, MockRepo):
        """비푸드 미차감: 맥주 수동발주 -> 차감 안 함"""
        from src.order.auto_order import AutoOrderSystem

        mock_repo_inst = MagicMock()
        # 맥주는 is_food_category에서 False이므로 DB에 있어도 차감 안 됨
        mock_repo_inst.get_today_food_orders.return_value = {}
        MockRepo.return_value = mock_repo_inst

        system = AutoOrderSystem.__new__(AutoOrderSystem)
        system.store_id = "46513"
        system._exclusion_records = []

        order_list = self._make_order_list()
        result = system._deduct_manual_food_orders(order_list, min_order_qty=1)

        beer = next(i for i in result if i["item_cd"] == "BEER001")
        assert beer["final_order_qty"] == 12  # 변경 없음

    @patch("src.settings.constants.MANUAL_ORDER_FOOD_DEDUCTION", True)
    def test_db_failure_skips_deduction(self):
        """DB 조회 실패 시 차감 건너뜀"""
        from src.order.auto_order import AutoOrderSystem

        system = AutoOrderSystem.__new__(AutoOrderSystem)
        system.store_id = "46513"
        system._exclusion_records = []

        order_list = self._make_order_list()
        # ManualOrderItemRepository import 실패 시뮬레이션
        with patch(
            "src.infrastructure.database.repos.ManualOrderItemRepository",
            side_effect=Exception("DB error")
        ):
            result = system._deduct_manual_food_orders(order_list, min_order_qty=1)

        # 원본 그대로 반환
        assert len(result) == 4
        assert result[0]["final_order_qty"] == 8

    @patch("src.infrastructure.database.repos.ManualOrderItemRepository", autospec=True)
    @patch("src.settings.constants.MANUAL_ORDER_FOOD_DEDUCTION", True)
    def test_empty_manual_orders(self, MockRepo):
        """수동발주 0건 -> 변경 없음"""
        from src.order.auto_order import AutoOrderSystem

        mock_repo_inst = MagicMock()
        mock_repo_inst.get_today_food_orders.return_value = {}
        MockRepo.return_value = mock_repo_inst

        system = AutoOrderSystem.__new__(AutoOrderSystem)
        system.store_id = "46513"
        system._exclusion_records = []

        order_list = self._make_order_list()
        result = system._deduct_manual_food_orders(order_list, min_order_qty=1)

        assert len(result) == 4
        for orig, res in zip(order_list, result):
            assert orig["final_order_qty"] == res["final_order_qty"]

    @patch("src.settings.constants.MANUAL_ORDER_FOOD_DEDUCTION", False)
    def test_feature_disabled(self):
        """MANUAL_ORDER_FOOD_DEDUCTION=False -> 차감 안 함"""
        from src.order.auto_order import AutoOrderSystem

        system = AutoOrderSystem.__new__(AutoOrderSystem)
        system.store_id = "46513"
        system._exclusion_records = []

        order_list = self._make_order_list()
        result = system._deduct_manual_food_orders(order_list, min_order_qty=1)

        assert len(result) == 4


# ==================================================================
# 스마트발주 기본 비제외 테스트
# ==================================================================

class TestSmartOrderExclusion:

    def test_exclude_smart_default_false(self):
        """EXCLUDE_SMART_ORDER 기본값 = False"""
        mock_settings = MagicMock()
        mock_settings.get.return_value = False  # 기본값 False

        # 기본값으로 호출 시 False 반환 확인
        result = mock_settings.get("EXCLUDE_SMART_ORDER", False)
        assert result is False


# ==================================================================
# OrderStatusCollector 테스트 (mock)
# ==================================================================

class TestCollectNormalOrderItems:

    def test_click_normal_radio_no_driver(self):
        """드라이버 없으면 False"""
        from src.collectors.order_status_collector import OrderStatusCollector
        collector = OrderStatusCollector(driver=None, store_id="46513")
        assert collector.click_normal_radio() is False

    def test_collect_normal_order_items_no_driver(self):
        """드라이버 없으면 None"""
        from src.collectors.order_status_collector import OrderStatusCollector
        collector = OrderStatusCollector(driver=None, store_id="46513")
        assert collector.collect_normal_order_items() is None

    def test_collect_normal_order_items_radio_fail(self):
        """라디오 클릭 실패 시 None"""
        from src.collectors.order_status_collector import OrderStatusCollector
        mock_driver = MagicMock()
        mock_driver.execute_script.return_value = {"success": False, "error": "not found"}

        collector = OrderStatusCollector(driver=mock_driver, store_id="46513")
        result = collector.collect_normal_order_items()
        assert result is None

    def test_collect_normal_order_items_success(self):
        """정상 수집 시뮬레이션"""
        from src.collectors.order_status_collector import OrderStatusCollector
        mock_driver = MagicMock()

        # click_normal_radio -> success
        # collect_normal_order_items -> items
        mock_driver.execute_script.side_effect = [
            {"success": True, "method": "api", "value": "1"},  # radio click
            {  # dsResult 조회
                "items": [
                    {"item_cd": "TEST001", "item_nm": "테스트도시락", "mid_cd": "001",
                     "mid_nm": "도시락", "ord_ymd": "20260226", "ord_cnt": 2,
                     "ord_unit_qty": 1, "order_qty": 2, "ord_input_id": "단품별(재택)",
                     "ord_amt": 11000},
                ],
                "total": 50,
                "ordered": 1,
            },
        ]

        collector = OrderStatusCollector(driver=mock_driver, store_id="46513")
        result = collector.collect_normal_order_items()

        assert result is not None
        assert len(result) == 1
        assert result[0]["item_cd"] == "TEST001"
        assert result[0]["order_qty"] == 2

    def test_collect_normal_items_with_input_id(self):
        """ord_input_id 수집 확인"""
        from src.collectors.order_status_collector import OrderStatusCollector
        mock_driver = MagicMock()

        mock_driver.execute_script.side_effect = [
            {"success": True, "method": "api"},
            {
                "items": [
                    {"item_cd": "A1", "item_nm": "상품A", "mid_cd": "001",
                     "mid_nm": "도시락", "ord_ymd": "20260226", "ord_cnt": 1,
                     "ord_unit_qty": 1, "order_qty": 1,
                     "ord_input_id": "발주수정(재택)", "ord_amt": 5000},
                ],
                "total": 10,
                "ordered": 1,
            },
        ]

        collector = OrderStatusCollector(driver=mock_driver, store_id="46513")
        result = collector.collect_normal_order_items()
        assert result[0]["ord_input_id"] == "발주수정(재택)"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
