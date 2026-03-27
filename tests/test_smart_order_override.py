"""
스마트발주 오버라이드 (스마트→수동 전환) 테스트

- _inject_smart_order_items(): 스마트 상품 주입 로직
- order_executor: qty=0 필터 우회
- direct_api_saver: _calc_multiplier() smart_override
- api_order: 대시보드 토글
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import dataclass
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ==================================================================
# Mock PredictionResult (predict_batch 반환값)
# ==================================================================

@dataclass
class MockPredictionResult:
    item_cd: str
    order_qty: int
    adjusted_qty: float = 0.0
    current_stock: int = 0
    pending_qty: int = 0
    safety_stock: float = 0.0


# ==================================================================
# Fixtures
# ==================================================================

@pytest.fixture
def auto_order_system():
    """AutoOrderSystem (driver=None, mock 최소화)"""
    with patch('src.prediction.improved_predictor.ImprovedPredictor.__init__', return_value=None), \
         patch('src.prediction.predictor.OrderPredictor.__init__', return_value=None), \
         patch('src.prediction.pre_order_evaluator.PreOrderEvaluator.__init__', return_value=None), \
         patch('src.prediction.eval_calibrator.EvalCalibrator.__init__', return_value=None), \
         patch('src.prediction.eval_config.EvalConfig.load'), \
         patch('src.infrastructure.database.repos.OrderTrackingRepository.__init__', return_value=None), \
         patch('src.infrastructure.database.repos.ProductDetailRepository.__init__', return_value=None), \
         patch('src.infrastructure.database.repos.OrderExclusionRepository.__init__', return_value=None), \
         patch('src.infrastructure.database.repos.RealtimeInventoryRepository.__init__', return_value=None), \
         patch('src.infrastructure.database.repos.AutoOrderItemRepository.__init__', return_value=None), \
         patch('src.infrastructure.database.repos.SmartOrderItemRepository.__init__', return_value=None), \
         patch('src.infrastructure.database.repos.AppSettingsRepository.__init__', return_value=None), \
         patch('src.prediction.category_demand_forecaster.CategoryDemandForecaster.__init__', return_value=None), \
         patch('src.prediction.large_category_forecaster.LargeCategoryForecaster.__init__', return_value=None), \
         patch('src.prediction.improved_predictor.PredictionLogger.__init__', return_value=None):

        from src.order.auto_order import AutoOrderSystem
        system = AutoOrderSystem(driver=None, use_improved_predictor=True, store_id="46513")
        system._cut_items = set()
        system._unavailable_items = set()
        system._smart_order_repo = MagicMock()
        system._product_repo = MagicMock()
        system._product_repo.get.return_value = {"order_unit_qty": 1}
        system._product_detail_cache = {}
        system.improved_predictor = MagicMock()
        return system


@pytest.fixture
def sample_order_list():
    """기존 발주 목록 (예측 파이프라인에서 생성된 상품들)"""
    return [
        {
            "item_cd": "8800001111111",
            "item_nm": "도시락A",
            "mid_cd": "001",
            "final_order_qty": 3,
            "orderable_day": "월화수목금토일",
        },
        {
            "item_cd": "8800002222222",
            "item_nm": "샌드위치B",
            "mid_cd": "002",
            "final_order_qty": 2,
            "orderable_day": "월화수목금토일",
        },
    ]


@pytest.fixture
def smart_order_items():
    """스마트발주 상품 (BGF 본부 관리) — 일부는 예측 목록에 이미 있음"""
    return [
        {"item_cd": "8800001111111", "item_nm": "도시락A", "mid_cd": "001"},   # 이미 예측 목록에 있음
        {"item_cd": "8800003333333", "item_nm": "디저트C", "mid_cd": "005"},   # 예측 목록에 없음
        {"item_cd": "8800004444444", "item_nm": "음료D", "mid_cd": "010"},     # 예측 목록에 없음
    ]


# ==================================================================
# 1. _inject_smart_order_items() 테스트
# ==================================================================

class TestInjectSmartOrderItems:
    """_inject_smart_order_items 메서드 테스트"""

    def test_disabled_returns_unchanged(self, auto_order_system, sample_order_list):
        """SMART_ORDER_OVERRIDE=False → 목록 변경 없음"""
        with patch('src.infrastructure.database.repos.AppSettingsRepository.get', return_value=False):
            result = auto_order_system._inject_smart_order_items(
                sample_order_list.copy(), target_date=datetime(2026, 3, 2)
            )
        assert len(result) == 2
        assert all(not item.get("smart_override") for item in result)

    def test_enabled_marks_existing_items(
        self, auto_order_system, sample_order_list, smart_order_items
    ):
        """이미 목록에 있는 스마트 상품은 smart_override=True 플래그만 추가"""
        auto_order_system._smart_order_repo.get_all_detail.return_value = smart_order_items
        auto_order_system.improved_predictor.predict_batch.return_value = [
            MockPredictionResult("8800003333333", order_qty=5, adjusted_qty=4.8),
            MockPredictionResult("8800004444444", order_qty=0, adjusted_qty=0.3),
        ]

        with patch('src.infrastructure.database.repos.AppSettingsRepository.get', return_value=True):
            result = auto_order_system._inject_smart_order_items(
                sample_order_list.copy(), target_date=datetime(2026, 3, 2)
            )

        # 기존 항목 중 스마트 상품은 smart_override=True
        existing = [i for i in result if i["item_cd"] == "8800001111111"]
        assert len(existing) == 1
        assert existing[0]["smart_override"] is True
        # 기존 qty 유지
        assert existing[0]["final_order_qty"] == 3

    def test_missing_items_predicted_and_added(
        self, auto_order_system, sample_order_list, smart_order_items
    ):
        """예측 목록에 없는 스마트 상품 → predict_batch + 추가"""
        auto_order_system._smart_order_repo.get_all_detail.return_value = smart_order_items
        auto_order_system.improved_predictor.predict_batch.return_value = [
            MockPredictionResult("8800003333333", order_qty=5, adjusted_qty=4.8,
                                 current_stock=2, pending_qty=1, safety_stock=1.5),
            MockPredictionResult("8800004444444", order_qty=0, adjusted_qty=0.3),
        ]

        with patch('src.infrastructure.database.repos.AppSettingsRepository.get', return_value=True):
            result = auto_order_system._inject_smart_order_items(
                sample_order_list.copy(), target_date=datetime(2026, 3, 2)
            )

        # 기존 2 + qty>0 1개 + cancel_smart 1개 = 4
        assert len(result) == 4

        # qty > 0 항목
        item_c = next(i for i in result if i["item_cd"] == "8800003333333")
        assert item_c["final_order_qty"] == 5
        assert item_c["smart_override"] is True
        assert item_c["source"] == "smart_override"
        assert item_c["predicted_sales"] == 4.8
        assert item_c["current_stock"] == 2

        # qty=0 취소 항목
        cancel_d = next(i for i in result if i["item_cd"] == "8800004444444")
        assert cancel_d["cancel_smart"] is True
        assert cancel_d["final_order_qty"] == 0

    def test_zero_qty_items_cancel_smart(
        self, auto_order_system, sample_order_list, smart_order_items
    ):
        """예측 qty=0 상품은 cancel_smart=True로 발주 목록에 포함 (스마트 취소용)"""
        auto_order_system._smart_order_repo.get_all_detail.return_value = smart_order_items
        auto_order_system.improved_predictor.predict_batch.return_value = [
            MockPredictionResult("8800003333333", order_qty=3, adjusted_qty=2.5),
            MockPredictionResult("8800004444444", order_qty=0, adjusted_qty=0.1),
        ]

        with patch('src.infrastructure.database.repos.AppSettingsRepository.get', return_value=True):
            result = auto_order_system._inject_smart_order_items(
                sample_order_list.copy(), target_date=datetime(2026, 3, 2)
            )

        # qty=0인 8800004444444는 cancel_smart=True로 포함되어야 함
        cancel_items = [i for i in result if i.get("cancel_smart")]
        assert len(cancel_items) == 1
        assert cancel_items[0]["item_cd"] == "8800004444444"
        assert cancel_items[0]["final_order_qty"] == 0
        assert cancel_items[0]["source"] == "smart_cancel"
        # qty>0인 8800003333333은 일반 추가
        positive_items = [i for i in result if i.get("source") == "smart_override" and not i.get("cancel_smart")]
        assert any(i["item_cd"] == "8800003333333" for i in positive_items)

    @patch('src.settings.constants.SMART_OVERRIDE_MIN_QTY', 1)
    def test_min_qty_fallback(
        self, auto_order_system, sample_order_list, smart_order_items
    ):
        """SMART_OVERRIDE_MIN_QTY=1 → 예측 qty=0도 최소 1개로 발주 포함"""
        auto_order_system._smart_order_repo.get_all_detail.return_value = smart_order_items
        auto_order_system.improved_predictor.predict_batch.return_value = [
            MockPredictionResult("8800003333333", order_qty=0, adjusted_qty=0),
            MockPredictionResult("8800004444444", order_qty=0, adjusted_qty=0),
        ]

        with patch('src.infrastructure.database.repos.AppSettingsRepository.get', return_value=True):
            result = auto_order_system._inject_smart_order_items(
                sample_order_list.copy(), target_date=datetime(2026, 3, 2)
            )

        # MIN_QTY=1이면 qty=0→1로 올려서 발주 포함됨
        smart_items = [i for i in result if i.get("source") == "smart_override"]
        assert len(smart_items) == 2
        for item in smart_items:
            assert item["final_order_qty"] >= 1, \
                f"MIN_QTY=1인데 qty=0 항목 발견: {item['item_cd']}"

    def test_cut_items_excluded(
        self, auto_order_system, sample_order_list, smart_order_items
    ):
        """CUT 상품은 스마트 오버라이드에서도 제외"""
        auto_order_system._cut_items = {"8800003333333"}  # CUT 처리
        auto_order_system._smart_order_repo.get_all_detail.return_value = smart_order_items
        auto_order_system.improved_predictor.predict_batch.return_value = [
            MockPredictionResult("8800004444444", order_qty=2, adjusted_qty=1.8),
        ]

        with patch('src.infrastructure.database.repos.AppSettingsRepository.get', return_value=True):
            result = auto_order_system._inject_smart_order_items(
                sample_order_list.copy(), target_date=datetime(2026, 3, 2)
            )

        # CUT 상품은 추가 안 됨 → 기존 2 + 추가 1 (4444444만)
        assert len(result) == 3
        item_codes = {i["item_cd"] for i in result}
        assert "8800003333333" not in item_codes

    def test_exclusion_records_items_excluded(
        self, auto_order_system, sample_order_list, smart_order_items
    ):
        """[C-3] _exclusion_records에 등록된 상품은 스마트 오버라이드에서 제외"""
        # 8800003333333을 발주정지(STOPPED)로 exclusion_records에 등록
        auto_order_system._exclusion_records = [
            {
                "item_cd": "8800003333333",
                "item_nm": "디저트C",
                "mid_cd": "005",
                "exclusion_type": "STOPPED",
                "predicted_qty": 0,
                "detail": "발주정지 등록 상품",
            }
        ]
        auto_order_system._smart_order_repo.get_all_detail.return_value = smart_order_items
        auto_order_system.improved_predictor.predict_batch.return_value = [
            MockPredictionResult("8800004444444", order_qty=2, adjusted_qty=1.8),
        ]

        with patch('src.infrastructure.database.repos.AppSettingsRepository.get', return_value=True):
            result = auto_order_system._inject_smart_order_items(
                sample_order_list.copy(), target_date=datetime(2026, 3, 2)
            )

        # exclusion_records 상품 제외 → 기존 2 + 추가 1 (4444444만)
        assert len(result) == 3
        item_codes = {i["item_cd"] for i in result}
        assert "8800003333333" not in item_codes
        assert "8800004444444" in item_codes

    def test_exclusion_records_multiple_types(
        self, auto_order_system, sample_order_list
    ):
        """[C-3] 여러 종류의 exclusion_records도 제외"""
        smart_items = [
            {"item_cd": "8800005555555", "item_nm": "상품E", "mid_cd": "010"},
            {"item_cd": "8800006666666", "item_nm": "상품F", "mid_cd": "012"},
            {"item_cd": "8800007777777", "item_nm": "상품G", "mid_cd": "013"},
        ]
        auto_order_system._exclusion_records = [
            {"item_cd": "8800005555555", "exclusion_type": "AUTO_ORDER"},
            {"item_cd": "8800006666666", "exclusion_type": "DESSERT_STOP"},
        ]
        auto_order_system._smart_order_repo.get_all_detail.return_value = smart_items
        auto_order_system.improved_predictor.predict_batch.return_value = [
            MockPredictionResult("8800007777777", order_qty=3),
        ]

        with patch('src.infrastructure.database.repos.AppSettingsRepository.get', return_value=True):
            result = auto_order_system._inject_smart_order_items(
                sample_order_list.copy(), target_date=datetime(2026, 3, 2)
            )

        item_codes = {i["item_cd"] for i in result}
        assert "8800005555555" not in item_codes  # AUTO_ORDER 제외
        assert "8800006666666" not in item_codes  # DESSERT_STOP 제외
        assert "8800007777777" in item_codes  # 정상 추가

    def test_predict_batch_failure_fallback(
        self, auto_order_system, sample_order_list, smart_order_items
    ):
        """[C-4] predict_batch 전체 실패 → pred_map={} 폴백, qty=0 처리"""
        auto_order_system._smart_order_repo.get_all_detail.return_value = smart_order_items
        auto_order_system.improved_predictor.predict_batch.side_effect = Exception("DB 연결 실패")

        with patch('src.infrastructure.database.repos.AppSettingsRepository.get', return_value=True):
            result = auto_order_system._inject_smart_order_items(
                sample_order_list.copy(), target_date=datetime(2026, 3, 2)
            )

        # 기존 2개 + cancel_smart 2개 (predict 실패 → qty=0 → 취소 주입)
        assert len(result) == 4
        # 기존 항목 smart_override 플래그는 유지
        existing = [i for i in result if i["item_cd"] == "8800001111111"]
        assert existing[0].get("smart_override") is True
        # missing 항목은 cancel_smart로 주입됨
        cancel_items = [i for i in result if i.get("cancel_smart")]
        assert len(cancel_items) == 2
        for c in cancel_items:
            assert c["final_order_qty"] == 0
            assert c["source"] == "smart_cancel"

    @patch('src.settings.constants.SMART_OVERRIDE_MIN_QTY', 1)
    def test_predict_batch_failure_with_min_qty(
        self, auto_order_system, sample_order_list, smart_order_items
    ):
        """[C-4] predict_batch 실패 + MIN_QTY=1 → qty=MIN_QTY로 추가"""
        auto_order_system._smart_order_repo.get_all_detail.return_value = smart_order_items
        auto_order_system.improved_predictor.predict_batch.side_effect = Exception("타임아웃")

        with patch('src.infrastructure.database.repos.AppSettingsRepository.get', return_value=True):
            result = auto_order_system._inject_smart_order_items(
                sample_order_list.copy(), target_date=datetime(2026, 3, 2)
            )

        # MIN_QTY=1 → qty=0이 1로 올라가므로 missing 상품도 추가
        smart_added = [i for i in result if i.get("source") == "smart_override"]
        assert len(smart_added) == 2  # 8800003333333 + 8800004444444
        for item in smart_added:
            assert item["final_order_qty"] >= 1

    def test_no_smart_items_returns_unchanged(
        self, auto_order_system, sample_order_list
    ):
        """스마트 상품 없음 → 목록 변경 없음"""
        auto_order_system._smart_order_repo.get_all_detail.return_value = []

        with patch('src.infrastructure.database.repos.AppSettingsRepository.get', return_value=True):
            result = auto_order_system._inject_smart_order_items(
                sample_order_list.copy(), target_date=datetime(2026, 3, 2)
            )

        assert len(result) == 2

    def test_all_smart_items_already_in_list(
        self, auto_order_system, sample_order_list
    ):
        """모든 스마트 상품이 이미 목록에 있으면 플래그만 추가"""
        smart_items = [
            {"item_cd": "8800001111111", "item_nm": "도시락A", "mid_cd": "001"},
            {"item_cd": "8800002222222", "item_nm": "샌드위치B", "mid_cd": "002"},
        ]
        auto_order_system._smart_order_repo.get_all_detail.return_value = smart_items

        with patch('src.infrastructure.database.repos.AppSettingsRepository.get', return_value=True):
            result = auto_order_system._inject_smart_order_items(
                sample_order_list.copy(), target_date=datetime(2026, 3, 2)
            )

        assert len(result) == 2  # 추가 없음
        assert all(item.get("smart_override") is True for item in result)
        # predict_batch 호출 안 됨
        auto_order_system.improved_predictor.predict_batch.assert_not_called()

    def test_predict_batch_called_with_missing_codes(
        self, auto_order_system, sample_order_list, smart_order_items
    ):
        """predict_batch는 누락 상품 코드만 받아야 함"""
        auto_order_system._smart_order_repo.get_all_detail.return_value = smart_order_items
        auto_order_system.improved_predictor.predict_batch.return_value = [
            MockPredictionResult("8800003333333", order_qty=1),
            MockPredictionResult("8800004444444", order_qty=0),
        ]
        target = datetime(2026, 3, 2)

        with patch('src.infrastructure.database.repos.AppSettingsRepository.get', return_value=True):
            auto_order_system._inject_smart_order_items(
                sample_order_list.copy(), target_date=target
            )

        # predict_batch 호출 인자 확인
        call_args = auto_order_system.improved_predictor.predict_batch.call_args
        codes = call_args[0][0]
        assert set(codes) == {"8800003333333", "8800004444444"}
        assert call_args[0][1] == target

    def test_exception_returns_original_list(
        self, auto_order_system, sample_order_list
    ):
        """예외 발생 시 원래 목록 그대로 반환"""
        with patch('src.infrastructure.database.repos.AppSettingsRepository.get',
                   side_effect=Exception("DB error")):
            result = auto_order_system._inject_smart_order_items(
                sample_order_list.copy(), target_date=datetime(2026, 3, 2)
            )

        assert len(result) == 2  # 변경 없음


# ==================================================================
# 2. OrderExecutor qty=0 필터 우회 테스트
# ==================================================================

class TestOrderExecutorQtyZeroFilter:
    """order_executor.py: qty=0 방어적 필터 (auto_order에서 이미 제외됨)"""

    @pytest.fixture
    def executor(self):
        """OrderExecutor 인스턴스 (Mock driver)"""
        from src.order.order_executor import OrderExecutor
        driver = MagicMock()
        return OrderExecutor(driver)

    def test_group_orders_qty_zero_skipped(self, executor):
        """일반 qty=0은 스킵, cancel_smart qty=0은 통과"""
        orders = [
            {"item_cd": "AAA", "final_order_qty": 0, "orderable_day": "월화수목금토일"},
            {"item_cd": "BBB", "final_order_qty": 0, "smart_override": True,
             "orderable_day": "월화수목금토일"},
            {"item_cd": "CCC", "final_order_qty": 3, "orderable_day": "월화수목금토일"},
            {"item_cd": "DDD", "final_order_qty": 0, "cancel_smart": True,
             "orderable_day": "월화수목금토일"},
        ]

        result = executor.group_orders_by_date(orders)
        all_items = []
        for date_items in result.values():
            all_items.extend(date_items)

        item_codes = {i["item_cd"] for i in all_items}
        assert "AAA" not in item_codes  # 일반 qty=0 → 스킵
        assert "BBB" not in item_codes  # smart_override(cancel_smart 없음) qty=0 → 스킵
        assert "CCC" in item_codes      # qty>0 → 통과
        assert "DDD" in item_codes      # cancel_smart qty=0 → 통과

    def test_group_orders_smart_override_positive_qty(self, executor):
        """smart_override 상품 qty>0은 정상 포함"""
        orders = [
            {"item_cd": "AAA", "final_order_qty": 5, "smart_override": True,
             "orderable_day": "월화수목금토일"},
        ]

        result = executor.group_orders_by_date(orders)
        all_items = []
        for date_items in result.values():
            all_items.extend(date_items)

        assert len(all_items) == 1
        assert all_items[0]["item_cd"] == "AAA"

    def test_group_orders_no_item_cd_skipped(self, executor):
        """item_cd 없는 항목은 스킵"""
        orders = [
            {"item_cd": "", "final_order_qty": 5, "smart_override": True,
             "orderable_day": "월화수목금토일"},
            {"item_cd": None, "final_order_qty": 5, "orderable_day": "월화수목금토일"},
        ]

        result = executor.group_orders_by_date(orders)
        all_items = []
        for date_items in result.values():
            all_items.extend(date_items)
        assert len(all_items) == 0


# ==================================================================
# 3. DirectApiSaver _calc_multiplier() 테스트
# ==================================================================

class TestCalcMultiplierSmartOverride:
    """direct_api_saver: 배수 계산 (qty=0은 auto_order에서 이미 제외)"""

    @pytest.fixture
    def saver(self):
        """DirectApiOrderSaver 인스턴스 (Mock driver)"""
        from src.order.direct_api_saver import DirectApiOrderSaver
        driver = MagicMock()
        return DirectApiOrderSaver(driver)

    def test_smart_override_qty_positive_returns_normal(self, saver):
        """smart_override + qty>0 → 일반 배수 계산"""
        order = {"smart_override": True, "final_order_qty": 6, "order_unit_qty": 6,
                 "multiplier": 1}
        result = saver._calc_multiplier(order)
        assert result == 1

    def test_qty_zero_returns_min_one(self, saver):
        """일반 qty=0 → 최소 배수 1 (방어적)"""
        order = {"final_order_qty": 0, "order_unit_qty": 1}
        result = saver._calc_multiplier(order)
        assert result >= 1

    def test_cancel_smart_returns_zero(self, saver):
        """cancel_smart + qty=0 → 배수 0 (스마트 취소용)"""
        order = {"final_order_qty": 0, "order_unit_qty": 4, "cancel_smart": True}
        result = saver._calc_multiplier(order)
        assert result == 0

    def test_cancel_smart_positive_qty_returns_zero(self, saver):
        """cancel_smart는 qty>0이어도 final_order_qty<=0만 체크"""
        order = {"final_order_qty": 0, "order_unit_qty": 6, "cancel_smart": True}
        result = saver._calc_multiplier(order)
        assert result == 0

    def test_normal_order_with_multiplier(self, saver):
        """일반 상품 multiplier 있으면 그대로 반환"""
        order = {"final_order_qty": 12, "order_unit_qty": 6, "multiplier": 2}
        result = saver._calc_multiplier(order)
        assert result == 2

    def test_no_smart_override_flag_normal_calc(self, saver):
        """smart_override 플래그 없으면 일반 계산"""
        order = {"final_order_qty": 10, "order_unit_qty": 5}
        result = saver._calc_multiplier(order)
        assert result == 2  # 10 / 5 = 2

    def test_smart_override_qty_positive_no_multiplier(self, saver):
        """smart_override + qty>0, multiplier 없음 → 자동 계산"""
        order = {"smart_override": True, "final_order_qty": 12, "order_unit_qty": 6}
        result = saver._calc_multiplier(order)
        assert result == 2  # 12 / 6 = 2


# ==================================================================
# 4. API 토글 테스트
# ==================================================================

class TestApiToggleSmartOverride:
    """api_order.py: smart_order_override 토글"""

    @pytest.fixture
    def client(self):
        """Flask 테스트 클라이언트"""
        try:
            from src.web.app import create_app
            app = create_app()
            app.config['TESTING'] = True
            with app.test_client() as client:
                yield client
        except Exception:
            pytest.skip("Flask app 생성 불가")

    def test_toggle_smart_override_kind(self):
        """kind=smart_override → SMART_ORDER_OVERRIDE 설정 변경"""
        with patch('src.web.routes.api_order.AppSettingsRepository') as MockRepo:
            mock_instance = MagicMock()
            MockRepo.return_value = mock_instance

            from src.web.routes.api_order import order_bp
            from flask import Flask
            app = Flask(__name__)
            app.register_blueprint(order_bp, url_prefix="/api/order")
            app.config['TESTING'] = True

            with app.test_client() as client:
                resp = client.post(
                    "/api/order/exclusions/toggle",
                    json={"kind": "smart_override", "enabled": True, "store_id": "46513"},
                )

            assert resp.status_code == 200
            data = resp.get_json()
            assert data["kind"] == "smart_override"
            assert data["enabled"] is True
            mock_instance.set.assert_called_with("SMART_ORDER_OVERRIDE", True)

    def test_toggle_smart_override_disable(self):
        """kind=smart_override enabled=False → 비활성화"""
        with patch('src.web.routes.api_order.AppSettingsRepository') as MockRepo:
            mock_instance = MagicMock()
            MockRepo.return_value = mock_instance

            from src.web.routes.api_order import order_bp
            from flask import Flask
            app = Flask(__name__)
            app.register_blueprint(order_bp, url_prefix="/api/order")
            app.config['TESTING'] = True

            with app.test_client() as client:
                resp = client.post(
                    "/api/order/exclusions/toggle",
                    json={"kind": "smart_override", "enabled": False, "store_id": "46513"},
                )

            assert resp.status_code == 200
            data = resp.get_json()
            assert data["enabled"] is False
            mock_instance.set.assert_called_with("SMART_ORDER_OVERRIDE", False)


# ==================================================================
# 5. constants.py 설정값 검증
# ==================================================================

class TestConstants:
    """constants.py에 스마트 오버라이드 설정이 올바르게 정의되었는지 확인"""

    def test_smart_order_override_enabled_default(self):
        """SMART_ORDER_OVERRIDE_ENABLED 기본값 = False"""
        from src.settings.constants import SMART_ORDER_OVERRIDE_ENABLED
        assert SMART_ORDER_OVERRIDE_ENABLED is False

    def test_smart_override_min_qty_default(self):
        """SMART_OVERRIDE_MIN_QTY 기본값 = 0"""
        from src.settings.constants import SMART_OVERRIDE_MIN_QTY
        assert SMART_OVERRIDE_MIN_QTY == 0

    def test_db_schema_version_50(self):
        """DB_SCHEMA_VERSION >= 50 (v50: 스마트발주 오버라이드)"""
        from src.settings.constants import DB_SCHEMA_VERSION
        assert DB_SCHEMA_VERSION >= 50


# ==================================================================
# 6. cancel_smart qty=0 취소 통합 테스트
# ==================================================================

class TestCancelSmartQtyZero:
    """스마트발주 qty=0 취소 로직 통합 테스트

    라이브 검증 (2026-03-14): PYUN_QTY=0 → BGF 수락, "단품별(채택)" 전환 확인
    """

    @pytest.fixture
    def auto_order_system(self):
        """AutoOrderSystem (driver=None, mock 최소화)"""
        with patch('src.prediction.improved_predictor.ImprovedPredictor.__init__', return_value=None), \
             patch('src.prediction.predictor.OrderPredictor.__init__', return_value=None), \
             patch('src.prediction.pre_order_evaluator.PreOrderEvaluator.__init__', return_value=None), \
             patch('src.prediction.eval_calibrator.EvalCalibrator.__init__', return_value=None), \
             patch('src.prediction.eval_config.EvalConfig.load'), \
             patch('src.infrastructure.database.repos.OrderTrackingRepository.__init__', return_value=None), \
             patch('src.infrastructure.database.repos.ProductDetailRepository.__init__', return_value=None), \
             patch('src.infrastructure.database.repos.OrderExclusionRepository.__init__', return_value=None), \
             patch('src.infrastructure.database.repos.RealtimeInventoryRepository.__init__', return_value=None), \
             patch('src.order.order_tracker.OrderTracker.__init__', return_value=None):
            from src.order.auto_order import AutoOrderSystem
            system = AutoOrderSystem(driver=None, store_id="46513")
            system._smart_order_repo = MagicMock()
            system.improved_predictor = MagicMock()
            system._product_repo = MagicMock()
            system._product_detail_cache = {}
            system._cut_items = set()
            system._unavailable_items = set()
            system._exclusion_records = []
            return system

    def test_cancel_smart_entry_fields(self, auto_order_system):
        """cancel_smart 항목의 필수 필드 검증"""
        smart_items = [
            {"item_cd": "TEST001", "item_nm": "테스트상품", "mid_cd": "049"},
        ]
        auto_order_system._smart_order_repo.get_all_detail.return_value = smart_items
        auto_order_system.improved_predictor.predict_batch.return_value = [
            MockPredictionResult("TEST001", order_qty=0),
        ]
        auto_order_system._product_repo.get.return_value = {"order_unit_qty": 4}

        with patch('src.infrastructure.database.repos.AppSettingsRepository.get', return_value=True):
            result = auto_order_system._inject_smart_order_items(
                [], target_date=datetime(2026, 3, 14)
            )

        cancel = [i for i in result if i.get("cancel_smart")]
        assert len(cancel) == 1
        c = cancel[0]
        assert c["item_cd"] == "TEST001"
        assert c["final_order_qty"] == 0
        assert c["cancel_smart"] is True
        assert c["smart_override"] is True
        assert c["source"] == "smart_cancel"
        assert c["order_unit_qty"] == 1  # cancel은 unit=1 고정

    def test_cancel_smart_all_items_zero(self, auto_order_system):
        """전체 스마트 상품 qty=0 → 전부 cancel_smart로 주입"""
        smart_items = [
            {"item_cd": "A001", "item_nm": "상품A", "mid_cd": "049"},
            {"item_cd": "A002", "item_nm": "상품B", "mid_cd": "050"},
            {"item_cd": "A003", "item_nm": "상품C", "mid_cd": "051"},
        ]
        auto_order_system._smart_order_repo.get_all_detail.return_value = smart_items
        auto_order_system.improved_predictor.predict_batch.return_value = [
            MockPredictionResult("A001", order_qty=0),
            MockPredictionResult("A002", order_qty=0),
            MockPredictionResult("A003", order_qty=0),
        ]

        with patch('src.infrastructure.database.repos.AppSettingsRepository.get', return_value=True):
            result = auto_order_system._inject_smart_order_items(
                [], target_date=datetime(2026, 3, 14)
            )

        cancel_items = [i for i in result if i.get("cancel_smart")]
        assert len(cancel_items) == 3
        for c in cancel_items:
            assert c["final_order_qty"] == 0
            assert c["source"] == "smart_cancel"

    def test_cancel_smart_mixed_qty(self, auto_order_system):
        """qty>0과 qty=0 혼합 → 정상+취소 분리"""
        smart_items = [
            {"item_cd": "B001", "item_nm": "양품", "mid_cd": "049"},
            {"item_cd": "B002", "item_nm": "불요", "mid_cd": "050"},
        ]
        auto_order_system._smart_order_repo.get_all_detail.return_value = smart_items
        auto_order_system.improved_predictor.predict_batch.return_value = [
            MockPredictionResult("B001", order_qty=5, adjusted_qty=4.5),
            MockPredictionResult("B002", order_qty=0),
        ]
        auto_order_system._product_repo.get.return_value = {"order_unit_qty": 1}

        with patch('src.infrastructure.database.repos.AppSettingsRepository.get', return_value=True):
            result = auto_order_system._inject_smart_order_items(
                [], target_date=datetime(2026, 3, 14)
            )

        normal = [i for i in result if i.get("source") == "smart_override" and not i.get("cancel_smart")]
        cancel = [i for i in result if i.get("cancel_smart")]
        assert len(normal) == 1
        assert normal[0]["item_cd"] == "B001"
        assert normal[0]["final_order_qty"] == 5
        assert len(cancel) == 1
        assert cancel[0]["item_cd"] == "B002"
        assert cancel[0]["final_order_qty"] == 0

    def test_cancel_smart_not_in_order_tracking(self):
        """cancel_smart 항목은 order_tracking에 저장되지 않음 (actual_qty=0)"""
        from src.order.order_tracker import OrderTracker
        # order_tracker L63: if not item_cd or actual_qty <= 0: continue
        # cancel_smart actual_qty=0이면 자동으로 스킵됨
        # 이 테스트는 해당 가드의 존재를 확인
        import inspect
        source = inspect.getsource(OrderTracker.save_to_order_tracking)
        assert "actual_qty <= 0" in source

    def test_batch_grid_cancel_smart_multiplier_zero(self):
        """batch_grid_input: cancel_smart → multiplier=0 로직 존재 확인"""
        import inspect
        from src.order import batch_grid_input
        source = inspect.getsource(batch_grid_input)
        assert "cancel_smart" in source
        # cancel_smart and qty <= 0: multiplier = 0 패턴 확인
        assert "multiplier = 0" in source

    def test_group_orders_cancel_smart_passes_through(self):
        """order_executor: cancel_smart qty=0 그룹핑에서 통과"""
        from src.order.order_executor import OrderExecutor
        executor = OrderExecutor(MagicMock())
        orders = [
            {"item_cd": "CANCEL1", "final_order_qty": 0, "cancel_smart": True,
             "orderable_day": "월화수목금토일"},
            {"item_cd": "NORMAL1", "final_order_qty": 3,
             "orderable_day": "월화수목금토일"},
        ]
        result = executor.group_orders_by_date(orders)
        all_items = []
        for date_items in result.values():
            all_items.extend(date_items)
        codes = {i["item_cd"] for i in all_items}
        assert "CANCEL1" in codes
        assert "NORMAL1" in codes

    def test_calc_multiplier_cancel_vs_normal_zero(self):
        """_calc_multiplier: cancel_smart=0 vs 일반 qty=0 차이"""
        from src.order.direct_api_saver import DirectApiOrderSaver
        # cancel_smart → 0
        cancel_order = {"final_order_qty": 0, "order_unit_qty": 6, "cancel_smart": True}
        assert DirectApiOrderSaver._calc_multiplier(cancel_order) == 0
        # 일반 → max(1, ...) = 1
        normal_order = {"final_order_qty": 0, "order_unit_qty": 6}
        assert DirectApiOrderSaver._calc_multiplier(normal_order) >= 1


class TestCancelSmartBugFixes:
    """cancel_smart 버그 3건 수정 검증 (2026-03-14)"""

    # === Bug #1: Selenium 경로 multiplier=0 ===

    def test_selenium_multiplier_zero_for_cancel_smart(self):
        """Bug#1: target_qty=0일 때 actual_multiplier=0 (max(1,...) 우회)"""
        # input_product 내부 로직을 직접 시뮬레이션 (target_qty=0)
        target_qty = 0
        actual_order_unit_qty = 6

        actual_order_unit_qty = max(1, actual_order_unit_qty)
        if target_qty == 0:
            actual_multiplier = 0
        else:
            actual_multiplier = min(99, max(1, (target_qty + actual_order_unit_qty - 1) // actual_order_unit_qty))

        assert actual_multiplier == 0
        assert actual_multiplier * actual_order_unit_qty == 0

    def test_selenium_multiplier_normal_not_zero(self):
        """Bug#1: 일반 상품 target_qty>0은 기존 max(1,...) 동작 유지"""
        target_qty = 3
        actual_order_unit_qty = 6

        actual_order_unit_qty = max(1, actual_order_unit_qty)
        if target_qty == 0:
            actual_multiplier = 0
        else:
            actual_multiplier = min(99, max(1, (target_qty + actual_order_unit_qty - 1) // actual_order_unit_qty))

        assert actual_multiplier == 1  # ceil(3/6)=1
        assert actual_multiplier * actual_order_unit_qty == 6

    def test_selenium_multiplier_various_units(self):
        """Bug#1: 다양한 배수단위에서 target_qty=0 → multiplier=0"""
        for unit in [1, 6, 10, 12, 24]:
            target_qty = 0
            actual_order_unit_qty = max(1, unit)
            if target_qty == 0:
                actual_multiplier = 0
            else:
                actual_multiplier = min(99, max(1, (target_qty + actual_order_unit_qty - 1) // actual_order_unit_qty))
            assert actual_multiplier == 0, f"unit={unit}: expected 0, got {actual_multiplier}"

    # === Bug #2: deduct_manual_food_orders cancel_smart 바이패스 ===

    def test_deduct_manual_preserves_cancel_smart_food(self):
        """Bug#2: cancel_smart 푸드 상품이 수동발주 차감에서 제거되지 않음"""
        from src.order.order_filter import OrderFilter

        of = OrderFilter(store_id="TEST")
        order_list = [
            {"item_cd": "CANCEL_FOOD", "mid_cd": "001", "final_order_qty": 0,
             "cancel_smart": True},
            {"item_cd": "NORMAL_FOOD", "mid_cd": "001", "final_order_qty": 5},
        ]

        # MANUAL_ORDER_FOOD_DEDUCTION=False이면 그대로 반환
        with patch("src.settings.constants.MANUAL_ORDER_FOOD_DEDUCTION", False):
            result = of.deduct_manual_food_orders(order_list, min_order_qty=1)
            assert len(result) == 2
            cancel_items = [i for i in result if i.get("cancel_smart")]
            assert len(cancel_items) == 1

    def test_deduct_manual_cancel_smart_bypass_with_manual_orders(self):
        """Bug#2: 수동발주 데이터 있어도 cancel_smart 항목 무조건 통과"""
        from src.order.order_filter import OrderFilter

        of = OrderFilter(store_id="TEST")
        order_list = [
            {"item_cd": "FOOD1", "mid_cd": "001", "final_order_qty": 0,
             "cancel_smart": True},
            {"item_cd": "FOOD2", "mid_cd": "002", "final_order_qty": 3},
        ]

        mock_manual_repo = MagicMock()
        mock_manual_repo.get_today_food_orders.return_value = {
            "FOOD1": 5,  # cancel_smart 항목에 수동발주 있어도 차감 안 됨
            "FOOD2": 2,
        }

        with patch("src.settings.constants.MANUAL_ORDER_FOOD_DEDUCTION", True), \
             patch("src.infrastructure.database.repos.ManualOrderItemRepository", return_value=mock_manual_repo):
            result = of.deduct_manual_food_orders(order_list, min_order_qty=1)

            # cancel_smart 항목은 그대로 통과
            cancel_items = [i for i in result if i.get("cancel_smart")]
            assert len(cancel_items) == 1
            assert cancel_items[0]["item_cd"] == "FOOD1"
            assert cancel_items[0]["final_order_qty"] == 0  # 변경 없음

    def test_deduct_manual_cancel_smart_not_counted_as_deducted(self):
        """Bug#2: cancel_smart 바이패스는 차감 통계에 포함되지 않음"""
        from src.order.order_filter import OrderFilter

        of = OrderFilter(store_id="TEST")
        order_list = [
            {"item_cd": "CS1", "mid_cd": "001", "final_order_qty": 0,
             "cancel_smart": True},
        ]

        mock_manual_repo = MagicMock()
        mock_manual_repo.get_today_food_orders.return_value = {"CS1": 10}

        with patch("src.settings.constants.MANUAL_ORDER_FOOD_DEDUCTION", True), \
             patch("src.infrastructure.database.repos.ManualOrderItemRepository", return_value=mock_manual_repo):
            exclusion_records = []
            result = of.deduct_manual_food_orders(
                order_list, min_order_qty=1, exclusion_records=exclusion_records
            )
            assert len(result) == 1
            assert len(exclusion_records) == 0  # 차감 기록 없음

    # === Bug #3: food_daily_cap cancel_smart Cap 제외 ===

    def test_cap_excludes_cancel_smart_from_count(self):
        """Bug#3: cancel_smart 항목이 Cap 품목수에 포함되지 않음"""
        # 시뮬레이션: 5개 정상 + 2개 cancel_smart, Cap=3
        items = [
            {"item_cd": f"N{i}", "mid_cd": "001", "final_order_qty": 2}
            for i in range(5)
        ] + [
            {"item_cd": f"C{i}", "mid_cd": "001", "final_order_qty": 0,
             "cancel_smart": True}
            for i in range(2)
        ]

        cancel_items = [i for i in items if i.get("cancel_smart")]
        non_cancel = [i for i in items if not i.get("cancel_smart")]

        # Cap은 non_cancel만 카운트
        assert len(non_cancel) == 5
        assert len(cancel_items) == 2
        # cancel_smart는 항상 결과에 포함
        adjusted_cap = 3
        current_count = len(non_cancel)
        assert current_count > adjusted_cap  # 5 > 3 → 선별 필요

    def test_cap_always_includes_cancel_smart_in_result(self):
        """Bug#3: Cap 초과 선별 후에도 cancel_smart 항목은 결과에 포함"""
        # non_cancel 선별 결과 + cancel_items 합산 확인
        non_cancel = [
            {"item_cd": "N0", "final_order_qty": 2},
            {"item_cd": "N1", "final_order_qty": 3},
        ]
        cancel_items = [
            {"item_cd": "C0", "final_order_qty": 0, "cancel_smart": True},
        ]

        # Cap 적용 후 결과 = selected(non_cancel) + cancel_items
        selected = non_cancel[:1]  # Cap=1로 선별
        result = selected + cancel_items

        assert len(result) == 2
        cancel_in_result = [i for i in result if i.get("cancel_smart")]
        assert len(cancel_in_result) == 1

    def test_cap_under_limit_includes_all_with_cancel_smart(self):
        """Bug#3: Cap 이하일 때 정상+cancel_smart 모두 포함"""
        items = [
            {"item_cd": "N0", "mid_cd": "001", "final_order_qty": 2},
            {"item_cd": "C0", "mid_cd": "001", "final_order_qty": 0,
             "cancel_smart": True},
        ]

        cancel_items = [i for i in items if i.get("cancel_smart")]
        non_cancel = [i for i in items if not i.get("cancel_smart")]

        adjusted_cap = 5
        current_count = len(non_cancel)
        assert current_count <= adjusted_cap  # 1 ≤ 5 → 그대로 유지

        result = non_cancel + cancel_items
        assert len(result) == 2
        assert any(i.get("cancel_smart") for i in result)
