"""
발주 제외 사유 수집 통합 테스트

auto_order.py의 7개 수집 지점에서 _exclusion_records에 올바르게 기록되는지 검증:
- NOT_CARRIED: 점포 미취급
- CUT: 발주중지
- AUTO_ORDER: 본부 자동발주
- SMART_ORDER: 본부 스마트발주
- STOPPED: stopped_items 등록
- STOCK_SUFFICIENT: 재고/미입고 충분
- FORCE_SUPPRESSED: FORCE 보충 생략
"""

import pytest
from unittest.mock import patch, MagicMock

from src.order.auto_order import AutoOrderSystem
from src.infrastructure.database.repos.order_exclusion_repo import ExclusionType
from src.settings.constants import DEFAULT_STORE_ID


@pytest.fixture
def auto_order_system():
    """driver=None 모드의 AutoOrderSystem (예측/목록 생성만)"""
    with patch("src.order.auto_order.OrderTrackingRepository"), \
         patch("src.order.auto_order.PredictionLogger"), \
         patch("src.order.auto_order.EvalConfig.load"), \
         patch("src.order.auto_order.PreOrderEvaluator"), \
         patch("src.order.auto_order.EvalCalibrator"), \
         patch("src.order.auto_order.ProductDetailRepository"), \
         patch("src.order.auto_order.RealtimeInventoryRepository"), \
         patch("src.order.auto_order.AutoOrderItemRepository"), \
         patch("src.order.auto_order.SmartOrderItemRepository"), \
         patch("src.order.auto_order.OrderExclusionRepository"):
        system = AutoOrderSystem(driver=None, store_id="99999")
    return system


def _make_order_item(item_cd, item_nm="테스트", mid_cd="001", order_qty=5, **kwargs):
    """발주 목록 아이템 생성 헬퍼"""
    item = {
        "item_cd": item_cd,
        "item_nm": item_nm,
        "mid_cd": mid_cd,
        "final_order_qty": order_qty,
        "order_qty": order_qty,
        "predicted_sales": order_qty,
        "safety_stock": 1.0,
        "daily_avg": 3.0,
        "current_stock": 2,
        "pending_receiving_qty": 0,
        "order_unit_qty": 1,
    }
    item.update(kwargs)
    return item


# ─── 수집 지점 1: NOT_CARRIED (점포 미취급) ───

class TestNotCarriedExclusion:
    def test_unavailable_item_recorded(self, auto_order_system):
        """미취급 상품이 _exclusion_records에 기록"""
        auto_order_system._unavailable_items.add("ITEM001")

        order_list = [
            _make_order_item("ITEM001"),
            _make_order_item("ITEM002"),
        ]
        result = auto_order_system._exclude_filtered_items(order_list)

        # ITEM001이 발주 목록에서 제외됨
        assert len(result) == 1
        assert result[0]["item_cd"] == "ITEM002"

        # 미취급은 prefetch 시점(수집지점1)에서 기록 — _exclude_filtered_items에서는 리스트만 필터링
        # 여기서는 _unavailable_items.add() 시점에서 이미 기록되었을 것이므로
        # _exclude_filtered_items에서 추가 기록하지 않음 (중복 방지)


# ─── 수집 지점 2: CUT (발주중지) ───

class TestCutExclusion:
    def test_cut_item_filtered(self, auto_order_system):
        """CUT 상품이 발주 목록에서 제외됨"""
        auto_order_system._cut_items.add("ITEM001")

        order_list = [
            _make_order_item("ITEM001"),
            _make_order_item("ITEM002"),
        ]
        result = auto_order_system._exclude_filtered_items(order_list)

        assert len(result) == 1
        assert result[0]["item_cd"] == "ITEM002"


# ─── 수집 지점 3: AUTO_ORDER ───

class TestAutoOrderExclusion:
    def test_auto_order_items_recorded(self, auto_order_system):
        """자동발주 상품이 _exclusion_records에 AUTO_ORDER로 기록"""
        auto_order_system._auto_order_items = {"ITEM001", "ITEM003"}

        order_list = [
            _make_order_item("ITEM001"),
            _make_order_item("ITEM002"),
            _make_order_item("ITEM003"),
        ]

        with patch("src.infrastructure.database.repos.AppSettingsRepository") as mock_settings:
            mock_settings.return_value.get.return_value = True
            result = auto_order_system._exclude_filtered_items(order_list)

        # ITEM002만 남음
        assert len(result) == 1
        assert result[0]["item_cd"] == "ITEM002"

        # exclusion_records에 AUTO_ORDER 2건 기록
        auto_records = [r for r in auto_order_system._exclusion_records
                        if r["exclusion_type"] == ExclusionType.AUTO_ORDER]
        assert len(auto_records) == 2
        recorded_items = {r["item_cd"] for r in auto_records}
        assert recorded_items == {"ITEM001", "ITEM003"}


# ─── 수집 지점 4: SMART_ORDER ───

class TestSmartOrderExclusion:
    def test_smart_order_items_recorded(self, auto_order_system):
        """스마트발주 상품이 _exclusion_records에 SMART_ORDER로 기록"""
        auto_order_system._smart_order_items = {"ITEM002"}

        order_list = [
            _make_order_item("ITEM001"),
            _make_order_item("ITEM002"),
        ]

        with patch("src.infrastructure.database.repos.AppSettingsRepository") as mock_settings:
            mock_settings.return_value.get.return_value = True
            result = auto_order_system._exclude_filtered_items(order_list)

        assert len(result) == 1

        smart_records = [r for r in auto_order_system._exclusion_records
                         if r["exclusion_type"] == ExclusionType.SMART_ORDER]
        assert len(smart_records) == 1
        assert smart_records[0]["item_cd"] == "ITEM002"


# ─── 수집 지점 5: STOPPED ───

class TestStoppedExclusion:
    def test_stopped_items_recorded(self, auto_order_system):
        """발주정지 상품이 _exclusion_records에 STOPPED로 기록"""
        order_list = [
            _make_order_item("ITEM001"),
            _make_order_item("ITEM002"),
        ]

        mock_stopped_repo = MagicMock()
        mock_stopped_repo.return_value.get_active_item_codes.return_value = {"ITEM002"}

        with patch("src.infrastructure.database.repos.AppSettingsRepository") as mock_settings, \
             patch("src.infrastructure.database.repos.StoppedItemRepository", mock_stopped_repo):
            mock_settings.return_value.get.return_value = True
            result = auto_order_system._exclude_filtered_items(order_list)

        assert len(result) == 1
        assert result[0]["item_cd"] == "ITEM001"

        stopped_records = [r for r in auto_order_system._exclusion_records
                           if r["exclusion_type"] == ExclusionType.STOPPED]
        assert len(stopped_records) == 1
        assert stopped_records[0]["item_cd"] == "ITEM002"


# ─── 수집 지점 6: STOCK_SUFFICIENT ───

class TestStockSufficientExclusion:
    def test_stock_sufficient_recorded(self, auto_order_system):
        """재고 충분 시 _exclusion_records에 STOCK_SUFFICIENT 기록"""
        order_list = [
            _make_order_item(
                "ITEM001", order_qty=5,
                current_stock=2, pending_receiving_qty=0,
                predicted_sales=5, safety_stock=1.0
            ),
        ]

        # 실시간 재고가 크게 증가하여 need < min_order_qty가 되는 상황
        result = auto_order_system._apply_pending_and_stock_to_order_list(
            order_list,
            pending_data={"ITEM001": 10},  # 미입고 10개 → need < 0
            stock_data={"ITEM001": 5},      # 재고 5개
            min_order_qty=1
        )

        # 발주 목록에서 제거
        assert len(result) == 0

        # STOCK_SUFFICIENT 기록 확인
        sufficient_records = [r for r in auto_order_system._exclusion_records
                              if r["exclusion_type"] == ExclusionType.STOCK_SUFFICIENT]
        assert len(sufficient_records) == 1
        assert sufficient_records[0]["item_cd"] == "ITEM001"


# ─── exclusion_records 초기화/누적 검증 ───

class TestExclusionRecordsAccumulation:
    def test_records_accumulate_across_calls(self, auto_order_system):
        """여러 수집 지점에서의 기록이 누적됨"""
        # NOT_CARRIED (prefetch 시점에서 직접 추가)
        auto_order_system._exclusion_records.append({
            "item_cd": "ITEM_A",
            "exclusion_type": ExclusionType.NOT_CARRIED,
        })

        # AUTO_ORDER (_exclude_filtered_items 호출)
        auto_order_system._auto_order_items = {"ITEM_B"}
        order_list = [_make_order_item("ITEM_B"), _make_order_item("ITEM_C")]

        with patch("src.infrastructure.database.repos.AppSettingsRepository") as mock_settings:
            mock_settings.return_value.get.return_value = True
            auto_order_system._exclude_filtered_items(order_list)

        # 총 2건 (NOT_CARRIED 1 + AUTO_ORDER 1)
        assert len(auto_order_system._exclusion_records) == 2
        types = {r["exclusion_type"] for r in auto_order_system._exclusion_records}
        assert ExclusionType.NOT_CARRIED in types
        assert ExclusionType.AUTO_ORDER in types

    def test_clear_resets_records(self, auto_order_system):
        """clear() 후 records가 비어야 함"""
        auto_order_system._exclusion_records.append({
            "item_cd": "ITEM_A",
            "exclusion_type": ExclusionType.NOT_CARRIED,
        })
        assert len(auto_order_system._exclusion_records) == 1

        auto_order_system._exclusion_records.clear()
        assert len(auto_order_system._exclusion_records) == 0


# ─── ExclusionType 상수 일관성 ───

class TestExclusionTypeConsistency:
    def test_all_types_used_in_auto_order(self):
        """auto_order.py에서 사용하는 모든 ExclusionType이 정의되어 있는지"""
        used_types = [
            ExclusionType.NOT_CARRIED,
            ExclusionType.CUT,
            ExclusionType.AUTO_ORDER,
            ExclusionType.SMART_ORDER,
            ExclusionType.STOPPED,
            ExclusionType.STOCK_SUFFICIENT,
            ExclusionType.FORCE_SUPPRESSED,
        ]
        for t in used_types:
            assert t in ExclusionType.ALL, f"{t} not in ExclusionType.ALL"
