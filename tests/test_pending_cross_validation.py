"""pending_qty 교차검증 테스트 (realtime_inventory vs order_tracking)

order_tracking 보완 (올리는 방향만):
- OT > RI인 경우: OT값으로 보완 (BGF 미반영 발주 보완, 중복발주 방지)
- OT < RI인 경우: RI 신뢰 (수동 발주는 order_tracking에 미기록)

그룹:
1. OrderTrackingRepository.get_pending_qty_sum_batch() — 6개
2. _load_ot_pending_cache() — 3개
3. _resolve_stock_and_pending() 교차검증 — 12개
4. 통합 테스트 — 3개
"""

import sqlite3
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime, timedelta


# ═══════════════════════════════════════════════════
# 1. OrderTrackingRepository.get_pending_qty_sum_batch()
# ═══════════════════════════════════════════════════

class TestGetPendingQtySumBatch:
    """OrderTrackingRepository.get_pending_qty_sum_batch() 테스트"""

    def _make_repo_with_db(self, conn):
        """테스트용 repo 생성 (conn을 직접 반환하도록 패치)"""
        from src.infrastructure.database.repos.order_tracking_repo import OrderTrackingRepository
        repo = OrderTrackingRepository.__new__(OrderTrackingRepository)
        repo.store_id = "46513"
        repo._get_conn = lambda: conn
        return repo

    def _setup_db(self):
        """인메모리 DB에 order_tracking 테이블 생성"""
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE order_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id TEXT DEFAULT '46513',
                order_date TEXT,
                item_cd TEXT,
                item_nm TEXT,
                mid_cd TEXT,
                delivery_type TEXT,
                order_qty INTEGER DEFAULT 0,
                remaining_qty INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                arrival_time TEXT,
                created_at TEXT,
                UNIQUE(order_date, item_cd)
            )
        """)
        return conn

    def test_empty_table(self):
        """빈 order_tracking => {}"""
        conn = self._setup_db()
        repo = self._make_repo_with_db(conn)
        result = repo.get_pending_qty_sum_batch(store_id="46513")
        assert result == {}

    def test_single_ordered_item(self):
        """ordered 상태 1건 => {item_cd: remaining_qty}"""
        conn = self._setup_db()
        conn.execute("""
            INSERT INTO order_tracking (store_id, order_date, item_cd, order_qty, remaining_qty, status)
            VALUES ('46513', '2026-02-24', 'ITEM001', 16, 16, 'ordered')
        """)
        repo = self._make_repo_with_db(conn)
        result = repo.get_pending_qty_sum_batch(store_id="46513")
        assert result == {"ITEM001": 16}

    def test_multiple_orders_same_item(self):
        """동일 상품 2건(ordered+arrived) => 합산"""
        conn = self._setup_db()
        conn.execute("""
            INSERT INTO order_tracking (store_id, order_date, item_cd, order_qty, remaining_qty, status)
            VALUES ('46513', '2026-02-23', 'ITEM001', 16, 10, 'ordered')
        """)
        conn.execute("""
            INSERT INTO order_tracking (store_id, order_date, item_cd, order_qty, remaining_qty, status)
            VALUES ('46513', '2026-02-24', 'ITEM001', 16, 6, 'arrived')
        """)
        repo = self._make_repo_with_db(conn)
        result = repo.get_pending_qty_sum_batch(store_id="46513")
        assert result == {"ITEM001": 16}  # 10 + 6

    def test_disposed_excluded(self):
        """disposed/expired 상태 제외"""
        conn = self._setup_db()
        conn.execute("""
            INSERT INTO order_tracking (store_id, order_date, item_cd, order_qty, remaining_qty, status)
            VALUES ('46513', '2026-02-24', 'ITEM001', 16, 16, 'disposed')
        """)
        conn.execute("""
            INSERT INTO order_tracking (store_id, order_date, item_cd, order_qty, remaining_qty, status)
            VALUES ('46513', '2026-02-23', 'ITEM002', 12, 12, 'expired')
        """)
        repo = self._make_repo_with_db(conn)
        result = repo.get_pending_qty_sum_batch(store_id="46513")
        assert result == {}

    def test_zero_remaining_excluded(self):
        """remaining_qty=0 제외 (입고 완료)"""
        conn = self._setup_db()
        conn.execute("""
            INSERT INTO order_tracking (store_id, order_date, item_cd, order_qty, remaining_qty, status)
            VALUES ('46513', '2026-02-24', 'ITEM001', 16, 0, 'arrived')
        """)
        repo = self._make_repo_with_db(conn)
        result = repo.get_pending_qty_sum_batch(store_id="46513")
        assert result == {}

    def test_store_filter(self):
        """store_id 필터 동작"""
        conn = self._setup_db()
        conn.execute("""
            INSERT INTO order_tracking (store_id, order_date, item_cd, order_qty, remaining_qty, status)
            VALUES ('46513', '2026-02-24', 'ITEM001', 16, 16, 'ordered')
        """)
        conn.execute("""
            INSERT INTO order_tracking (store_id, order_date, item_cd, order_qty, remaining_qty, status)
            VALUES ('99999', '2026-02-24', 'ITEM002', 12, 12, 'ordered')
        """)
        repo = self._make_repo_with_db(conn)
        result = repo.get_pending_qty_sum_batch(store_id="46513")
        assert "ITEM001" in result
        assert "ITEM002" not in result


# ═══════════════════════════════════════════════════
# 2. _load_ot_pending_cache()
# ═══════════════════════════════════════════════════

class TestLoadOtPendingCache:
    """_load_ot_pending_cache() 테스트"""

    def _make_predictor(self):
        """__init__ 바이패스 predictor 생성"""
        from src.prediction.improved_predictor import ImprovedPredictor
        predictor = ImprovedPredictor.__new__(ImprovedPredictor)
        predictor.store_id = "46513"
        predictor._ot_pending_cache = None
        return predictor

    @patch("src.infrastructure.database.repos.order_tracking_repo.OrderTrackingRepository")
    def test_cache_populated(self, MockRepo):
        """정상 로드 시 dict 저장"""
        mock_repo = MockRepo.return_value
        mock_repo.get_pending_qty_sum_batch.return_value = {"ITEM001": 16}

        predictor = self._make_predictor()
        predictor._load_ot_pending_cache()

        assert predictor._ot_pending_cache == {"ITEM001": 16}

    @patch("src.infrastructure.database.repos.order_tracking_repo.OrderTrackingRepository")
    def test_cache_none_on_error(self, MockRepo):
        """예외 시 None 유지 (교차검증 비활성)"""
        MockRepo.side_effect = Exception("DB error")

        predictor = self._make_predictor()
        predictor._load_ot_pending_cache()

        assert predictor._ot_pending_cache is None

    @patch("src.infrastructure.database.repos.order_tracking_repo.OrderTrackingRepository")
    def test_cache_empty_dict_valid(self, MockRepo):
        """빈 결과 시 {} (None과 구분, 모든 pending=0 의미)"""
        mock_repo = MockRepo.return_value
        mock_repo.get_pending_qty_sum_batch.return_value = {}

        predictor = self._make_predictor()
        predictor._load_ot_pending_cache()

        assert predictor._ot_pending_cache is not None
        assert predictor._ot_pending_cache == {}


# ═══════════════════════════════════════════════════
# 3. _resolve_stock_and_pending() 교차검증
# ═══════════════════════════════════════════════════

class TestPendingCrossValidation:
    """_resolve_stock_and_pending() 교차검증 로직 테스트"""

    def _make_predictor_with_ri(self, stock_qty=0, pending_qty=32,
                                 queried_hours_ago=1, expiry_days=117,
                                 ot_pending_cache=None):
        """realtime_inventory 데이터가 있는 predictor 구성

        _use_db_inventory, _inventory_repo는 property로 _data에서 위임되므로
        _data mock에 설정해야 함.
        """
        from src.prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor.__new__(ImprovedPredictor)
        predictor.store_id = "46513"
        predictor._ot_pending_cache = ot_pending_cache

        queried_at = (datetime.now() - timedelta(hours=queried_hours_ago)).isoformat()

        mock_repo = MagicMock()
        mock_repo.get.return_value = {
            "stock_qty": max(0, stock_qty),
            "pending_qty": pending_qty,
            "queried_at": queried_at,
            "_stale": False,  # TTL 내 = fresh
        }

        # _data mock (_use_db_inventory, _inventory_repo는 property로 _data에서 위임)
        data = MagicMock()
        data._stock_cache = {}
        data._pending_cache = {}
        data._use_db_inventory = True
        data._inventory_repo = mock_repo
        predictor._data = data

        # get_current_stock 폴백 (사용되지 않지만 필요)
        predictor.get_current_stock = lambda item_cd: 0

        return predictor

    def test_ri_trusted_when_ot_lower(self):
        """RI=32, OT=0 => pending=32 (RI 신뢰, 수동발주는 OT에 미기록)"""
        predictor = self._make_predictor_with_ri(
            pending_qty=32,
            ot_pending_cache={}  # OT에 pending 없음
        )
        _, pending, _, source, _ = predictor._resolve_stock_and_pending("ITEM001", None)
        assert pending == 32
        assert source == "ri_fresh"

    def test_ri_trusted_when_ot_partial(self):
        """RI=32, OT=12 => pending=32 (RI 신뢰, OT < RI는 수동발주 가능성)"""
        predictor = self._make_predictor_with_ri(
            pending_qty=32,
            ot_pending_cache={"ITEM001": 12}
        )
        _, pending, _, source, _ = predictor._resolve_stock_and_pending("ITEM001", None)
        assert pending == 32
        assert source == "ri_fresh"

    def test_ot_fill_when_ot_higher(self):
        """RI=5, OT=10 => pending=10 (OT가 크면 OT값으로 보완)"""
        predictor = self._make_predictor_with_ri(
            pending_qty=5,
            ot_pending_cache={"ITEM001": 10}
        )
        _, pending, _, source, _ = predictor._resolve_stock_and_pending("ITEM001", None)
        assert pending == 10
        assert source == "ot_fill"

    def test_no_correction_when_equal(self):
        """RI=16, OT=16 => pending=16 (일치 시 보정 없음)"""
        predictor = self._make_predictor_with_ri(
            pending_qty=16,
            ot_pending_cache={"ITEM001": 16}
        )
        _, pending, _, source, _ = predictor._resolve_stock_and_pending("ITEM001", None)
        assert pending == 16
        assert source == "ri_fresh"

    def test_no_correction_when_stale(self):
        """stale 데이터는 이미 pending=0 처리되므로 교차검증 대상 아님"""
        from src.prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor.__new__(ImprovedPredictor)
        predictor.store_id = "46513"
        predictor._ot_pending_cache = {}

        mock_repo = MagicMock()
        mock_repo.get.return_value = {
            "stock_qty": 0,
            "pending_qty": 32,
            "queried_at": (datetime.now() - timedelta(hours=100)).isoformat(),
            "_stale": True,  # stale!
        }

        data = MagicMock()
        data._stock_cache = {}
        data._pending_cache = {}
        data._use_db_inventory = True
        data._inventory_repo = mock_repo
        predictor._data = data
        predictor.get_current_stock = lambda item_cd: 0

        _, pending, _, source, _ = predictor._resolve_stock_and_pending("ITEM001", None)
        assert pending == 0
        assert source == "ri_stale_zero"  # stale 처리에 의해 0, 교차검증 아님

    def test_no_correction_when_cache_source(self):
        """pending_source=cache => 교차검증 대상 아님"""
        from src.prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor.__new__(ImprovedPredictor)
        predictor.store_id = "46513"
        predictor._ot_pending_cache = {}  # 로드 성공

        data = MagicMock()
        data._stock_cache = {"ITEM001": 5}
        data._pending_cache = {"ITEM001": 20}  # 캐시에 pending 있음
        data._use_db_inventory = False
        data._inventory_repo = None
        predictor._data = data

        _, pending, _, source, _ = predictor._resolve_stock_and_pending("ITEM001", None)
        assert pending == 20  # 캐시값 그대로
        assert source == "cache"

    def test_no_correction_when_ot_cache_none(self):
        """_ot_pending_cache=None => 교차검증 비활성 (RI값 그대로)"""
        predictor = self._make_predictor_with_ri(
            pending_qty=32,
            ot_pending_cache=None  # 로드 실패
        )
        _, pending, _, source, _ = predictor._resolve_stock_and_pending("ITEM001", None)
        assert pending == 32  # RI값 그대로
        assert source == "ri_fresh"

    def test_ot_fill_when_ri_pending_zero(self):
        """RI pending=0, OT=10 => pending=10 (BGF 미반영 발주 보완, 중복발주 방지)"""
        predictor = self._make_predictor_with_ri(
            pending_qty=0,
            ot_pending_cache={"ITEM001": 10}
        )
        _, pending, _, source, _ = predictor._resolve_stock_and_pending("ITEM001", None)
        assert pending == 10
        assert source == "ot_fill"

    def test_ri_zero_ot_not_in_cache(self):
        """RI pending=0, OT 캐시에 item_cd 없음 => pending=0 (양쪽 다 0)"""
        predictor = self._make_predictor_with_ri(
            pending_qty=0,
            ot_pending_cache={}  # 해당 상품 없음
        )
        _, pending, _, source, _ = predictor._resolve_stock_and_pending("ITEM001", None)
        assert pending == 0
        assert source == "ri_fresh"

    def test_ri_zero_ot_five_fill(self):
        """RI pending=0, OT pending=5 => pending=5 (핵심 버그 수정 케이스)"""
        predictor = self._make_predictor_with_ri(
            pending_qty=0,
            ot_pending_cache={"ITEM001": 5}
        )
        _, pending, _, source, _ = predictor._resolve_stock_and_pending("ITEM001", None)
        assert pending == 5
        assert source == "ot_fill"

    def test_ri_eight_ot_three_ri_trusted(self):
        """RI pending=8, OT pending=3 => pending=8 (RI 신뢰, 수동발주 가능성)"""
        predictor = self._make_predictor_with_ri(
            pending_qty=8,
            ot_pending_cache={"ITEM001": 3}
        )
        _, pending, _, source, _ = predictor._resolve_stock_and_pending("ITEM001", None)
        assert pending == 8
        assert source == "ri_fresh"

    def test_ri_positive_ot_empty_ri_trusted(self):
        """RI pending=8, OT캐시에 없음(=0) => pending=8 (RI 신뢰, 수동발주 가능성)"""
        predictor = self._make_predictor_with_ri(
            pending_qty=8,
            ot_pending_cache={}  # item_cd 없음 = pending 0
        )
        _, pending, _, source, _ = predictor._resolve_stock_and_pending("ITEM001", None)
        assert pending == 8
        assert source == "ri_fresh"


# ═══════════════════════════════════════════════════
# 4. 통합 테스트
# ═══════════════════════════════════════════════════

class TestPendingCrossValidationIntegration:
    """예측 전체 플로우 통합 테스트"""

    @patch("src.prediction.improved_predictor.ImprovedPredictor.__init__", return_value=None)
    def test_predict_batch_loads_cache(self, mock_init):
        """predict_batch() 호출 시 _load_ot_pending_cache 호출 확인"""
        from src.prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor.__new__(ImprovedPredictor)
        predictor.store_id = "46513"
        predictor._ot_pending_cache = None
        predictor._receiving_stats_cache = {}
        predictor._demand_pattern_cache = {}
        predictor._substitution_detector = None
        # predict_batch 내부에서 직접 접근하는 속성 (getattr 보호 없는 것들)
        predictor._ml_predictor = None
        predictor._stacking = None
        predictor._data = None
        predictor._promo_adjuster = None
        predictor._feature_calculator = None
        predictor._daily_stats_cache = {}

        with patch.object(predictor, '_load_receiving_stats_cache'), \
             patch.object(predictor, '_load_ot_pending_cache') as mock_load, \
             patch.object(predictor, '_load_demand_pattern_cache'), \
             patch.object(predictor, '_load_food_coef_cache'), \
             patch.object(predictor, '_load_group_context_caches'), \
             patch.object(predictor, '_get_substitution_detector', return_value=None), \
             patch.object(predictor, '_get_waste_feedback', return_value=None), \
             patch.object(predictor, 'predict', return_value=None):
            predictor.predict_batch(["ITEM001"])
            mock_load.assert_called_once()

    def test_ri_pending_trusted_over_empty_ot(self):
        """RI pending=32, OT={} => pending=32 (RI 신뢰, 수동발주 가능성)

        raise-only 설계: OT < RI인 경우 RI를 낮추지 않음.
        수동 발주(BGF 사이트 직접)는 order_tracking에 미기록되므로
        OT=0이라고 RI를 0으로 낮추면 수동발주 미입고를 무시하게 됨.
        """
        from src.prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor.__new__(ImprovedPredictor)
        predictor.store_id = "46513"
        predictor._ot_pending_cache = {}  # OT: 모두 입고완료

        mock_repo = MagicMock()
        mock_repo.get.return_value = {
            "stock_qty": 0,
            "pending_qty": 32,
            "queried_at": datetime.now().isoformat(),
            "_stale": False,
        }

        data = MagicMock()
        data._stock_cache = {}
        data._pending_cache = {}
        data._use_db_inventory = True
        data._inventory_repo = mock_repo
        predictor._data = data
        predictor.get_current_stock = lambda item_cd: 0

        stock, pending, _, source, _ = predictor._resolve_stock_and_pending("ITEM001", None)

        # RI 값 그대로 유지
        assert stock == 0
        assert pending == 32
        assert source == "ri_fresh"

    def test_normal_pending_unchanged(self):
        """RI=OT 일치 시 기존 동작 유지"""
        from src.prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor.__new__(ImprovedPredictor)
        predictor.store_id = "46513"
        predictor._ot_pending_cache = {"ITEM001": 16}  # OT도 16

        mock_repo = MagicMock()
        mock_repo.get.return_value = {
            "stock_qty": 2,
            "pending_qty": 16,
            "queried_at": datetime.now().isoformat(),
            "_stale": False,
        }

        data = MagicMock()
        data._stock_cache = {}
        data._pending_cache = {}
        data._use_db_inventory = True
        data._inventory_repo = mock_repo
        predictor._data = data
        predictor.get_current_stock = lambda item_cd: 0

        stock, pending, _, source, _ = predictor._resolve_stock_and_pending("ITEM001", None)
        assert stock == 2
        assert pending == 16
        assert source == "ri_fresh"  # 보정 없음
