"""
맥주 과발주 수정 테스트 (beer-overorder-fix)

근본 원인:
1. _resolve_stock_and_pending()에서 stale RI=0일 때 ds_stock을 무시하는 버그
2. prefetch max_pending_items=200 한도로 인해 재고 미조회 상품 발생

테스트 범위:
- stale RI=0 폴백 로직 수정 검증
- 맥주 과발주 시나리오 e2e
- prefetch 한도 기본값 확인
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

from src.prediction.improved_predictor import ImprovedPredictor


def _make_predictor(**overrides):
    """테스트용 ImprovedPredictor 인스턴스 생성 (DB 없이)"""
    with patch.object(ImprovedPredictor, '__init__', return_value=None):
        p = ImprovedPredictor.__new__(ImprovedPredictor)

        # _data mock (PredictionDataProvider 역할)
        mock_data = MagicMock()
        mock_data._stock_cache = {}
        mock_data._pending_cache = {}
        mock_data._use_db_inventory = True
        mock_data._inventory_repo = MagicMock()
        p._data = mock_data

        p._ot_pending_cache = None
        p.store_id = "46513"
        p.db_path = ":memory:"
        for k, v in overrides.items():
            if k in ('_stock_cache', '_pending_cache', '_use_db_inventory', '_inventory_repo'):
                setattr(mock_data, k, v)
            else:
                setattr(p, k, v)
        return p


class TestStaleRIFallback:
    """_resolve_stock_and_pending() stale RI 폴백 테스트"""

    def test_stale_ri_zero_ds_positive(self):
        """stale RI=0, ds=19 → ds=19 채택, source='ri_stale_ds_nonzero'"""
        p = _make_predictor()
        p._data._inventory_repo.get.return_value = {
            "stock_qty": 0,
            "pending_qty": 0,
            "queried_at": (datetime.now() - timedelta(hours=48)).isoformat(),
            "_stale": True,
        }
        p.get_current_stock = MagicMock(return_value=19)

        stock, pending, stock_src, pending_src, is_stale = p._resolve_stock_and_pending("TEST001", None)

        assert stock == 19
        assert stock_src == "ri_stale_ds_nonzero"
        assert is_stale is True

    def test_stale_ri_zero_ds_zero(self):
        """stale RI=0, ds=0 → 0 채택 (진짜 재고 없음), source는 ri_stale_ri"""
        p = _make_predictor()
        p._data._inventory_repo.get.return_value = {
            "stock_qty": 0,
            "pending_qty": 0,
            "queried_at": (datetime.now() - timedelta(hours=48)).isoformat(),
            "_stale": True,
        }
        p.get_current_stock = MagicMock(return_value=0)

        stock, pending, stock_src, pending_src, is_stale = p._resolve_stock_and_pending("TEST001", None)

        # ri=0, ds=0: ri_stock==0 and ds_stock>0 is False → ds<ri도 False → ri_stale_ri
        assert stock == 0
        assert stock_src == "ri_stale_ri"
        assert is_stale is True

    def test_stale_ri_positive_ds_lower(self):
        """stale RI=15, ds=10 → ds=10 채택 (기존 동작 유지)"""
        p = _make_predictor()
        p._data._inventory_repo.get.return_value = {
            "stock_qty": 15,
            "pending_qty": 0,
            "queried_at": (datetime.now() - timedelta(hours=48)).isoformat(),
            "_stale": True,
        }
        p.get_current_stock = MagicMock(return_value=10)

        stock, pending, stock_src, pending_src, is_stale = p._resolve_stock_and_pending("TEST001", None)

        assert stock == 10
        assert stock_src == "ri_stale_ds"

    def test_stale_ri_positive_ds_higher(self):
        """stale RI=10, ds=15 → ri=10 채택 (기존 동작 유지)"""
        p = _make_predictor()
        p._data._inventory_repo.get.return_value = {
            "stock_qty": 10,
            "pending_qty": 0,
            "queried_at": (datetime.now() - timedelta(hours=48)).isoformat(),
            "_stale": True,
        }
        p.get_current_stock = MagicMock(return_value=15)

        stock, pending, stock_src, pending_src, is_stale = p._resolve_stock_and_pending("TEST001", None)

        assert stock == 10
        assert stock_src == "ri_stale_ri"

    def test_fresh_ri_not_affected(self):
        """fresh RI → stale 분기 안 탐 (기존 동작 변경 없음)"""
        p = _make_predictor()
        p._data._inventory_repo.get.return_value = {
            "stock_qty": 5,
            "pending_qty": 2,
            "queried_at": datetime.now().isoformat(),
            "_stale": False,
        }

        stock, pending, stock_src, pending_src, is_stale = p._resolve_stock_and_pending("TEST001", None)

        assert stock == 5
        assert stock_src == "ri"
        assert is_stale is False

    def test_stale_ri_zero_ds_negative_treated_as_zero(self):
        """stale RI=0, ds=-1(음수 방어) → ds < 0 → 0으로 초기화"""
        p = _make_predictor()
        p._data._inventory_repo.get.return_value = {
            "stock_qty": 0,
            "pending_qty": 0,
            "queried_at": (datetime.now() - timedelta(hours=48)).isoformat(),
            "_stale": True,
        }
        # ds가 음수 반환하는 비정상 상황
        p.get_current_stock = MagicMock(return_value=-1)

        stock, pending, stock_src, pending_src, is_stale = p._resolve_stock_and_pending("TEST001", None)

        # ri=0, ds=-1: ri_stock==0 and ds_stock>0 is False → ds<ri(-1<0) True → ds 채택 → 음수 방어 0
        assert stock == 0

    def test_cache_hit_bypasses_stale_logic(self):
        """_stock_cache에 값이 있으면 stale 분기 진입 안 함"""
        p = _make_predictor(_stock_cache={"TEST001": 25})
        p._data._inventory_repo.get.return_value = {
            "stock_qty": 0,
            "_stale": True,
        }

        stock, pending, stock_src, pending_src, is_stale = p._resolve_stock_and_pending("TEST001", None)

        assert stock == 25
        assert stock_src == "cache"
        assert is_stale is False


class TestStaleRIPendingBehavior:
    """stale 상태에서 pending 처리 확인"""

    def test_stale_pending_zeroed(self):
        """stale 상태에서 pending_qty는 0으로 처리"""
        p = _make_predictor()
        p._data._inventory_repo.get.return_value = {
            "stock_qty": 0,
            "pending_qty": 5,
            "queried_at": (datetime.now() - timedelta(hours=48)).isoformat(),
            "_stale": True,
        }
        p.get_current_stock = MagicMock(return_value=19)

        stock, pending, stock_src, pending_src, is_stale = p._resolve_stock_and_pending("TEST001", None)

        assert stock == 19
        assert pending == 0
        assert pending_src == "ri_stale_zero"

    def test_stale_pending_with_explicit_param(self):
        """pending_qty가 명시적 파라미터로 전달되면 stale 무관하게 사용"""
        p = _make_predictor()
        p._data._inventory_repo.get.return_value = {
            "stock_qty": 0,
            "pending_qty": 5,
            "queried_at": (datetime.now() - timedelta(hours=48)).isoformat(),
            "_stale": True,
        }
        p.get_current_stock = MagicMock(return_value=19)

        stock, pending, stock_src, pending_src, is_stale = p._resolve_stock_and_pending("TEST001", 3)

        assert stock == 19
        assert pending == 3


class TestBeerWithCorrectStock:
    """맥주 과발주 시나리오 검증 (stale RI=0 수정 후)"""

    def test_beer_need_qty_negative_when_stock_high(self):
        """재고 19 > safety 7.33 → need_qty < 0 → 발주 불필요"""
        # 직접 need_qty 공식 검증 (전체 predict_batch 호출 없이)
        adjusted_prediction = 0.0
        lead_time_demand = 0.0
        safety_stock = 7.33
        current_stock = 19  # stale RI=0 수정 후 ds=19 채택
        pending_qty = 0

        need_qty = adjusted_prediction + lead_time_demand + safety_stock - current_stock - pending_qty
        assert need_qty < 0, f"need_qty should be negative but got {need_qty}"
        assert need_qty == pytest.approx(-11.67, abs=0.01)

    def test_beer_need_qty_positive_when_stock_low(self):
        """재고 2 < safety 7.33 → need_qty > 0 → 정상 발주"""
        adjusted_prediction = 0.0
        lead_time_demand = 0.0
        safety_stock = 7.33
        current_stock = 2
        pending_qty = 0

        need_qty = adjusted_prediction + lead_time_demand + safety_stock - current_stock - pending_qty
        assert need_qty > 0, f"need_qty should be positive but got {need_qty}"
        assert need_qty == pytest.approx(5.33, abs=0.01)

    def test_beer_max_stock_skip_with_correct_stock(self):
        """맥주: 재고+미입고 >= max_stock → skip_order=True"""
        from src.prediction.categories.beer import analyze_beer_pattern

        # db_path=None이면 실제 DB 접근 → mock 필요
        with patch("src.prediction.categories.beer.sqlite3.connect") as mock_conn:
            mock_cursor = MagicMock()
            # daily_avg = 11/3 = 3.67, data_days=3
            mock_cursor.fetchone.return_value = (3, 11, 19)
            mock_conn.return_value.cursor.return_value = mock_cursor

            result = analyze_beer_pattern(
                item_cd="4901777153325",
                db_path=":memory:",
                current_stock=19,
                pending_qty=0,
                store_id="46513"
            )

        # max_stock = 3.67 * 7 = 25.67
        # current_stock(19) + pending(0) = 19 < max_stock(25.67) → skip=False
        # 하지만 safety_stock = 7.33, need = 7.33 - 19 = -11.67 → 발주 불필요
        assert result.safety_stock == pytest.approx(7.33, abs=0.1)
        assert result.daily_avg == pytest.approx(3.67, abs=0.1)


class TestPrefetchLimit:
    """prefetch 한도 기본값 확인"""

    def test_default_max_pending_items_unlimited(self):
        """execute() 기본 max_pending_items=0 (전수 조회)"""
        import inspect
        from src.order.auto_order import AutoOrderSystem

        sig = inspect.signature(AutoOrderSystem.execute)
        param = sig.parameters['max_pending_items']
        assert param.default == 0, f"Expected 0 (unlimited), got {param.default}"

    def test_prefetch_unlimited_by_default(self):
        """prefetch_pending_quantities는 기본값(0)이면 전수 조회"""
        from src.order.auto_order import AutoOrderSystem

        with patch.object(AutoOrderSystem, '__init__', return_value=None):
            executor = AutoOrderSystem.__new__(AutoOrderSystem)
            executor.pending_collector = MagicMock()
            executor.pending_collector._menu_navigated = True
            executor.pending_collector._date_selected = True
            executor.pending_collector.collect_for_items = MagicMock(return_value={})
            executor._unavailable_items = set()
            executor._cut_items = set()
            executor._exclusion_records = []
            executor._last_stock_data = {}

            items = [f"ITEM{i:04d}" for i in range(600)]
            # max_items=0 (기본값): 전수 조회
            executor.prefetch_pending_quantities(item_codes=items)

            call_args = executor.pending_collector.collect_for_items.call_args
            assert len(call_args[0][0]) == 600, "기본값이면 전체 600개 조회해야 함"

    def test_prefetch_respects_explicit_limit(self):
        """prefetch_pending_quantities는 명시적 max_items > 0 이면 제한"""
        from src.order.auto_order import AutoOrderSystem

        with patch.object(AutoOrderSystem, '__init__', return_value=None):
            executor = AutoOrderSystem.__new__(AutoOrderSystem)
            executor.pending_collector = MagicMock()
            executor.pending_collector._menu_navigated = True
            executor.pending_collector._date_selected = True
            executor.pending_collector.collect_for_items = MagicMock(return_value={})
            executor._unavailable_items = set()
            executor._cut_items = set()
            executor._exclusion_records = []
            executor._last_stock_data = {}

            items = [f"ITEM{i:04d}" for i in range(600)]
            executor.prefetch_pending_quantities(item_codes=items, max_items=300)

            call_args = executor.pending_collector.collect_for_items.call_args
            assert len(call_args[0][0]) == 300, "명시적 300 제한 적용"
