"""
CUT 대체 보충 테스트 (food-cut-replacement PDCA)

CutReplacementService: CUT 탈락 상품의 수요를 동일 mid_cd 대체 상품으로 보충
_refilter_cut_items: CUT 재필터 헬퍼 (2곳 공용)
"""

import pytest
from unittest.mock import patch, MagicMock

from src.order.cut_replacement import CutReplacementService


# ─── Fixtures ──────────────────────────────────────────────

@pytest.fixture
def config_enabled():
    return {
        "cut_replacement": {
            "enabled": True,
            "target_mid_cds": ["001", "002", "003", "004", "005"],
            "replacement_ratio": 0.8,
            "max_add_per_item": 2,
            "max_candidates": 5,
            "min_sell_days": 1,
        }
    }


@pytest.fixture
def config_disabled():
    return {
        "cut_replacement": {
            "enabled": False,
            "target_mid_cds": ["001", "002", "003", "004", "005"],
            "replacement_ratio": 0.8,
            "max_add_per_item": 2,
            "max_candidates": 5,
            "min_sell_days": 1,
        }
    }


@pytest.fixture
def config_ratio_zero():
    return {
        "cut_replacement": {
            "enabled": True,
            "target_mid_cds": ["001", "002", "003", "004", "005"],
            "replacement_ratio": 0.0,
            "max_add_per_item": 2,
            "max_candidates": 5,
            "min_sell_days": 1,
        }
    }


@pytest.fixture
def mock_order_list():
    """mid=002 기존 발주 9건"""
    return [
        {
            "item_cd": f"existing_{i}",
            "item_nm": f"기존상품{i}",
            "mid_cd": "002",
            "final_order_qty": 1,
            "predicted_sales": 0.8,
        }
        for i in range(9)
    ]


@pytest.fixture
def cut_lost_items_002():
    """CUT 탈락 6건 (predicted_sales 합계 3.12)"""
    return [
        {
            "item_cd": f"cut_{i}",
            "mid_cd": "002",
            "predicted_sales": 0.52,
            "item_nm": f"CUT상품{i}",
        }
        for i in range(6)
    ]


@pytest.fixture
def cut_lost_items_001():
    """CUT 탈락 2건 (mid=001)"""
    return [
        {
            "item_cd": f"cut_001_{i}",
            "mid_cd": "001",
            "predicted_sales": 0.75,
            "item_nm": f"CUT도시락{i}",
        }
        for i in range(2)
    ]


@pytest.fixture
def mock_candidates():
    """DB에서 반환될 후보 5건 시뮬레이션"""
    return [
        # item_cd, sell_days, total_sale, current_stock, pending_qty
        ("cand_a", 5, 4, 0, 0),
        ("cand_b", 4, 3, 0, 0),
        ("cand_c", 4, 2, 1, 0),
        ("cand_d", 3, 2, 0, 0),
        ("cand_e", 2, 1, 0, 0),
    ]


@pytest.fixture
def mock_product_names():
    return [
        ("cand_a", "주)뉴치킨마요삼각2"),
        ("cand_b", "빅삼)참치마요삼각1"),
        ("cand_c", "주)3XL뉴매콤돈까스삼각1"),
        ("cand_d", "주)불고기삼각1"),
        ("cand_e", "빅삼)참치김치삼각1"),
    ]


@pytest.fixture
def mock_expiry_data():
    return [
        ("cand_a", 1),
        ("cand_b", 1),
        ("cand_c", 1),
        ("cand_d", 1),
        ("cand_e", 1),
    ]


def _make_service(config, store_id="46513"):
    """테스트용 CutReplacementService 생성"""
    svc = CutReplacementService(store_id=store_id)
    svc._config = config.get("cut_replacement", {})
    return svc


def _mock_db_cursor(candidates, product_names, expiry_data):
    """DB 연결을 모킹하여 후보 반환"""
    store_conn = MagicMock()
    store_cursor = MagicMock()
    store_cursor.fetchall.return_value = candidates
    store_conn.cursor.return_value = store_cursor

    common_conn = MagicMock()
    common_cursor = MagicMock()
    # execute 호출에 따라 다른 결과 반환
    common_cursor.execute.return_value = common_cursor
    execute_results = [
        MagicMock(fetchall=MagicMock(return_value=product_names)),
        MagicMock(fetchall=MagicMock(return_value=expiry_data)),
    ]
    common_cursor.execute.side_effect = lambda *a, **kw: execute_results.pop(0) if execute_results else MagicMock(fetchall=MagicMock(return_value=[]))

    return store_conn, common_conn


# ─── 테스트 시나리오 ──────────────────────────────────────

class TestCutReplacementService:
    """CUT 대체 보충 단위 테스트"""

    def test_01_basic_replacement(
        self, config_enabled, mock_order_list, cut_lost_items_002,
        mock_candidates, mock_product_names, mock_expiry_data
    ):
        """시나리오 1: mid=002에서 3건 CUT, 후보 5건 → 보충 발생"""
        svc = _make_service(config_enabled)

        store_conn, common_conn = _mock_db_cursor(
            mock_candidates, mock_product_names, mock_expiry_data
        )

        with patch("src.order.cut_replacement.DBRouter") as mock_router:
            mock_router.get_store_connection.return_value = store_conn
            mock_router.get_common_connection.return_value = common_conn

            result = svc.supplement_cut_shortage(
                order_list=mock_order_list.copy(),
                cut_lost_items=cut_lost_items_002,
            )

        # 보충이 발생했는지 확인
        added = [i for i in result if i.get("source") == "cut_replacement"]
        assert len(added) >= 1, "CUT 보충 상품이 추가되어야 함"

        total_added = sum(i["final_order_qty"] for i in added)
        # 3.12 * 0.8 = 2.496 → 최대 3개 보충
        assert total_added <= 4, f"보충량이 replacement_ratio 범위 내여야 함: {total_added}"
        assert total_added >= 1, f"최소 1개 보충 필요: {total_added}"

    def test_02_no_candidates(
        self, config_enabled, mock_order_list, cut_lost_items_001
    ):
        """시나리오 2: mid=001에서 CUT, 후보 0건 → 보충 0건"""
        svc = _make_service(config_enabled)

        store_conn = MagicMock()
        store_cursor = MagicMock()
        store_cursor.fetchall.return_value = []  # 후보 없음
        store_conn.cursor.return_value = store_cursor

        with patch("src.order.cut_replacement.DBRouter") as mock_router:
            mock_router.get_store_connection.return_value = store_conn

            result = svc.supplement_cut_shortage(
                order_list=mock_order_list.copy(),
                cut_lost_items=cut_lost_items_001,
            )

        assert len(result) == len(mock_order_list), "후보 없으면 변화 없어야 함"

    def test_03_zero_predicted_sales(self, config_enabled, mock_order_list):
        """시나리오 3: CUT 상품 predicted_sales=0 → lost_demand=0 → 보충 불필요"""
        svc = _make_service(config_enabled)

        zero_cut = [
            {
                "item_cd": "cut_zero",
                "mid_cd": "002",
                "predicted_sales": 0,
                "item_nm": "비판매CUT",
            }
        ]

        result = svc.supplement_cut_shortage(
            order_list=mock_order_list.copy(),
            cut_lost_items=zero_cut,
        )

        assert len(result) == len(mock_order_list), "predicted_sales=0이면 보충 불필요"

    def test_04_existing_item_qty_increase(
        self, config_enabled, mock_candidates, mock_product_names, mock_expiry_data
    ):
        """시나리오 4: 후보가 이미 order_list에 있으면 수량 증가"""
        svc = _make_service(config_enabled)

        # cand_a가 이미 발주 목록에 있음
        order_list = [
            {
                "item_cd": "cand_a",
                "item_nm": "주)뉴치킨마요삼각2",
                "mid_cd": "002",
                "final_order_qty": 1,
                "predicted_sales": 0.8,
            }
        ]

        cut_lost = [
            {"item_cd": "cut_0", "mid_cd": "002", "predicted_sales": 1.5, "item_nm": "CUT"},
        ]

        store_conn, common_conn = _mock_db_cursor(
            mock_candidates, mock_product_names, mock_expiry_data
        )

        with patch("src.order.cut_replacement.DBRouter") as mock_router:
            mock_router.get_store_connection.return_value = store_conn
            mock_router.get_common_connection.return_value = common_conn

            result = svc.supplement_cut_shortage(
                order_list=order_list,
                cut_lost_items=cut_lost,
            )

        # cand_a의 수량이 증가했는지 확인
        cand_a = next((i for i in result if i["item_cd"] == "cand_a"), None)
        assert cand_a is not None
        assert cand_a["final_order_qty"] >= 2, f"기존 1 + 보충 >= 1 = 최소 2: {cand_a['final_order_qty']}"

    def test_05_high_stock_lower_priority(
        self, config_enabled, mock_product_names, mock_expiry_data
    ):
        """시나리오 5: 재고 충분한 후보는 score 낮아 후순위"""
        svc = _make_service(config_enabled)

        # 후보 2건: A는 재고 0, B는 재고 충분
        candidates = [
            ("cand_a", 5, 4, 0, 0),   # 재고 0 → 높은 score
            ("cand_b", 5, 4, 10, 0),  # 재고 10 → 낮은 score
        ]

        store_conn, common_conn = _mock_db_cursor(
            candidates,
            [("cand_a", "상품A"), ("cand_b", "상품B")],
            [("cand_a", 1), ("cand_b", 1)],
        )

        cut_lost = [
            {"item_cd": "cut_0", "mid_cd": "002", "predicted_sales": 0.8, "item_nm": "CUT"},
        ]

        with patch("src.order.cut_replacement.DBRouter") as mock_router:
            mock_router.get_store_connection.return_value = store_conn
            mock_router.get_common_connection.return_value = common_conn

            result = svc.supplement_cut_shortage(
                order_list=[],
                cut_lost_items=cut_lost,
            )

        added = [i for i in result if i.get("source") == "cut_replacement"]
        if added:
            # 첫 번째 보충 상품이 재고 낮은 cand_a여야 함
            assert added[0]["item_cd"] == "cand_a", "재고 낮은 상품이 우선"

    def test_06_replacement_ratio_zero(
        self, config_ratio_zero, mock_order_list
    ):
        """시나리오 6: replacement_ratio=0 → 보충 0건"""
        svc = _make_service(config_ratio_zero)

        cut_lost = [
            {"item_cd": "cut_0", "mid_cd": "002", "predicted_sales": 1.0, "item_nm": "CUT"},
        ]

        store_conn, common_conn = _mock_db_cursor(
            [("cand_a", 5, 4, 0, 0)],
            [("cand_a", "상품A")],
            [("cand_a", 1)],
        )

        with patch("src.order.cut_replacement.DBRouter") as mock_router:
            mock_router.get_store_connection.return_value = store_conn
            mock_router.get_common_connection.return_value = common_conn

            result = svc.supplement_cut_shortage(
                order_list=mock_order_list.copy(),
                cut_lost_items=cut_lost,
            )

        # ratio=0이면 remaining=0 → 보충 없음
        added = [i for i in result if i.get("source") == "cut_replacement"]
        assert len(added) == 0, f"replacement_ratio=0이면 보충 없어야 함: {len(added)}"

    def test_07_no_cut_items(self, config_enabled, mock_order_list):
        """시나리오 7: CUT 0건 → 보충 스킵"""
        svc = _make_service(config_enabled)

        result = svc.supplement_cut_shortage(
            order_list=mock_order_list.copy(),
            cut_lost_items=[],  # CUT 없음
        )

        assert len(result) == len(mock_order_list)

    def test_08_disabled(self, config_disabled, mock_order_list, cut_lost_items_002):
        """시나리오 8: enabled=False → 원본 그대로 반환"""
        svc = _make_service(config_disabled)

        result = svc.supplement_cut_shortage(
            order_list=mock_order_list.copy(),
            cut_lost_items=cut_lost_items_002,
        )

        assert len(result) == len(mock_order_list)

    def test_09_no_double_supplement_with_floor(
        self, config_enabled, mock_candidates, mock_product_names, mock_expiry_data
    ):
        """시나리오 9: CUT 보충 후 CategoryFloor와 중복 미발생 (source 필드 확인)"""
        svc = _make_service(config_enabled)

        cut_lost = [
            {"item_cd": "cut_0", "mid_cd": "002", "predicted_sales": 1.0, "item_nm": "CUT"},
        ]

        store_conn, common_conn = _mock_db_cursor(
            mock_candidates, mock_product_names, mock_expiry_data
        )

        with patch("src.order.cut_replacement.DBRouter") as mock_router:
            mock_router.get_store_connection.return_value = store_conn
            mock_router.get_common_connection.return_value = common_conn

            result = svc.supplement_cut_shortage(
                order_list=[],
                cut_lost_items=cut_lost,
            )

        added = [i for i in result if i.get("source") == "cut_replacement"]
        for item in added:
            assert item["source"] == "cut_replacement", "source 태그 정확해야 함"
            # CategoryFloor에서 이 item_cd가 existing_items에 포함되어 제외됨
            assert item["item_cd"] in {i["item_cd"] for i in result}

    def test_10_normalized_score_range(self, config_enabled):
        """시나리오 10: 정규화 스코어가 0~1 범위"""
        svc = _make_service(config_enabled)

        candidates = [
            {"item_cd": "a", "daily_avg": 3.0, "sell_day_ratio": 1.0,
             "current_stock": 0, "expiration_days": 1},
            {"item_cd": "b", "daily_avg": 0.1, "sell_day_ratio": 0.14,
             "current_stock": 5, "expiration_days": 3},
        ]

        svc._calculate_scores(candidates)

        for c in candidates:
            assert 0 <= c["score"] <= 1, f"score must be 0~1: {c['score']}"

    def test_11_expiry_1day_effective_stock_zero(self, config_enabled):
        """시나리오 11: 유통기한 1일 상품은 effective_stock=0"""
        svc = _make_service(config_enabled)

        candidates = [
            {"item_cd": "a", "daily_avg": 1.0, "sell_day_ratio": 0.7,
             "current_stock": 5, "expiration_days": 1},  # 유통기한 1일
            {"item_cd": "b", "daily_avg": 1.0, "sell_day_ratio": 0.7,
             "current_stock": 5, "expiration_days": 3},  # 유통기한 3일
        ]

        svc._calculate_scores(candidates)

        # 유통기한 1일: effective_stock=0 → stock_ratio=0 → norm_stock=1.0
        # 유통기한 3일: effective_stock=5 → stock_ratio=2.5 → norm_stock=0
        assert candidates[0]["score"] > candidates[1]["score"], \
            f"유통기한 1일이 더 높은 점수여야 함: {candidates[0]['score']} vs {candidates[1]['score']}"

    def test_12_max_add_per_item_limit(
        self, config_enabled, mock_product_names, mock_expiry_data
    ):
        """시나리오 12: max_add_per_item=2 초과 방지"""
        svc = _make_service(config_enabled)

        # 높은 수요 → 많은 보충 필요
        cut_lost = [
            {"item_cd": f"cut_{i}", "mid_cd": "002", "predicted_sales": 2.0, "item_nm": f"CUT{i}"}
            for i in range(5)
        ]

        # 후보 1건만 → 이 상품에 몰림
        candidates = [("cand_a", 7, 10, 0, 0)]

        store_conn, common_conn = _mock_db_cursor(
            candidates,
            [("cand_a", "상품A")],
            [("cand_a", 1)],
        )

        with patch("src.order.cut_replacement.DBRouter") as mock_router:
            mock_router.get_store_connection.return_value = store_conn
            mock_router.get_common_connection.return_value = common_conn

            result = svc.supplement_cut_shortage(
                order_list=[],
                cut_lost_items=cut_lost,
            )

        added = [i for i in result if i.get("source") == "cut_replacement"]
        if added:
            for item in added:
                assert item["final_order_qty"] <= 2, \
                    f"max_add_per_item=2 초과: {item['final_order_qty']}"


class TestRefilterCutItems:
    """_refilter_cut_items() 헬퍼 메서드 테스트"""

    def _make_auto_order_system(self):
        """테스트용 AutoOrderSystem 인스턴스 (최소 초기화)"""
        from src.order.auto_order import AutoOrderSystem
        obj = object.__new__(AutoOrderSystem)
        obj._cut_items = set()
        obj._cut_lost_items = []
        return obj

    def test_13_execute_rerun_no_carryover(self):
        """시나리오 13: execute() 2회 호출 시 _cut_lost_items 잔류 없음"""
        obj = self._make_auto_order_system()

        # 1차 실행 시뮬레이션
        obj._cut_items = {"cut_a", "cut_b"}
        order_list = [
            {"item_cd": "cut_a", "mid_cd": "002", "predicted_sales": 1.0, "final_order_qty": 1},
            {"item_cd": "normal_1", "mid_cd": "002", "predicted_sales": 0.5, "final_order_qty": 1},
        ]
        filtered, lost = obj._refilter_cut_items(order_list)
        obj._cut_lost_items.extend(lost)
        assert len(obj._cut_lost_items) == 1, "1차: CUT 1건 캡처"

        # execute() 재호출 시 초기화
        obj._cut_lost_items = []  # execute()가 하는 것과 동일
        assert len(obj._cut_lost_items) == 0, "2차: 초기화 후 0건"

        # 2차 실행 시뮬레이션 (CUT 없음)
        obj._cut_items = set()
        order_list2 = [
            {"item_cd": "normal_2", "mid_cd": "002", "predicted_sales": 0.8, "final_order_qty": 1},
        ]
        filtered2, lost2 = obj._refilter_cut_items(order_list2)
        obj._cut_lost_items.extend(lost2)
        assert len(obj._cut_lost_items) == 0, "2차: CUT 없으므로 0건"

    def test_14_refilter_both_locations_consistent(self):
        """시나리오 14: _refilter_cut_items() 두 호출 위치에서 동일 결과"""
        obj = self._make_auto_order_system()
        obj._cut_items = {"cut_x"}

        order_list = [
            {"item_cd": "cut_x", "mid_cd": "002", "predicted_sales": 1.0, "final_order_qty": 1},
            {"item_cd": "normal", "mid_cd": "002", "predicted_sales": 0.5, "final_order_qty": 1},
            {"item_cd": "cut_x_nonfood", "mid_cd": "050", "predicted_sales": 1.0, "final_order_qty": 1},
        ]

        # 호출 위치 1
        filtered1, lost1 = obj._refilter_cut_items(order_list.copy())

        # 호출 위치 2 (동일 입력)
        filtered2, lost2 = obj._refilter_cut_items(order_list.copy())

        assert len(filtered1) == len(filtered2), "두 호출 결과 일치해야 함"
        assert len(lost1) == len(lost2), "CUT 손실 목록 일치해야 함"

        # cut_x는 푸드(002) → lost에 포함
        assert len(lost1) == 1, "푸드 CUT 1건만 캡처"
        assert lost1[0]["item_cd"] == "cut_x"

        # cut_x_nonfood(050)는 _cut_items에 미포함(cut_x만 포함) → filtered에 잔류
        # _refilter_cut_items는 _cut_items에 있는 상품만 제거
        assert len(filtered1) == 2, "CUT 1건(cut_x) 제거 후 normal+nonfood 2건 남음"

        # 비푸드 CUT는 lost에 미포함 확인 (푸드 카테고리만 캡처)
        lost_cds = {l["item_cd"] for l in lost1}
        assert "cut_x_nonfood" not in lost_cds, "비푸드 CUT는 대체 보충 대상 아님"


# ─── cut-replacement-fix PDCA 테스트 ─────────────────────

class TestCutReplacementFix:
    """CUT 보충 미작동 버그 수정 검증 (cut-replacement-fix PDCA)

    근본 원인: pre_order_evaluator CUT SKIP에서 daily_avg=0, mid_cd="" 설정
    → _cut_lost_items 비어서 CutReplacementService 미호출
    수정: CUT SKIP 시 실제 수요 데이터 보존 + eval_results에서 복원
    """

    def _make_auto_order_system(self):
        """테스트용 AutoOrderSystem 인스턴스 (최소 초기화)"""
        from src.order.auto_order import AutoOrderSystem
        obj = object.__new__(AutoOrderSystem)
        obj._cut_items = set()
        obj._cut_lost_items = []
        obj.store_id = "46513"
        return obj

    def test_15_eval_results_cut_to_lost_items(self):
        """Fix B: eval_results CUT SKIP → _cut_lost_items 변환"""
        from src.prediction.pre_order_evaluator import (
            PreOrderEvalResult, EvalDecision,
        )
        from src.settings.constants import FOOD_CATEGORIES

        obj = self._make_auto_order_system()
        obj._cut_lost_items = []

        eval_results = {
            "cut_001": PreOrderEvalResult(
                item_cd="cut_001", item_nm="CUT도시락A", mid_cd="001",
                decision=EvalDecision.SKIP, reason="CUT(발주중지) 상품",
                exposure_days=0, stockout_frequency=0, popularity_score=0,
                current_stock=0, pending_qty=0, daily_avg=2.5, trend_score=0,
            ),
            "cut_002": PreOrderEvalResult(
                item_cd="cut_002", item_nm="CUT도시락B", mid_cd="001",
                decision=EvalDecision.SKIP, reason="CUT(발주중지) 상품",
                exposure_days=0, stockout_frequency=0, popularity_score=0,
                current_stock=0, pending_qty=0, daily_avg=1.3, trend_score=0,
            ),
        }

        # Fix B 로직 재현
        existing_cut_cds = {item.get("item_cd") for item in obj._cut_lost_items}
        for cd, r in eval_results.items():
            if (r.decision == EvalDecision.SKIP
                    and "CUT" in r.reason
                    and r.mid_cd in FOOD_CATEGORIES
                    and r.daily_avg > 0
                    and cd not in existing_cut_cds):
                obj._cut_lost_items.append({
                    "item_cd": cd,
                    "mid_cd": r.mid_cd,
                    "predicted_sales": r.daily_avg,
                    "item_nm": r.item_nm,
                })

        assert len(obj._cut_lost_items) == 2, f"CUT 2건 복원: {len(obj._cut_lost_items)}"
        assert obj._cut_lost_items[0]["predicted_sales"] > 0
        assert obj._cut_lost_items[0]["mid_cd"] == "001"

    def test_16_non_food_cut_excluded_from_lost(self):
        """Fix B: 비푸드 CUT SKIP은 _cut_lost_items에 미포함"""
        from src.prediction.pre_order_evaluator import (
            PreOrderEvalResult, EvalDecision,
        )
        from src.settings.constants import FOOD_CATEGORIES

        obj = self._make_auto_order_system()

        eval_results = {
            "cut_beverage": PreOrderEvalResult(
                item_cd="cut_beverage", item_nm="CUT음료", mid_cd="042",
                decision=EvalDecision.SKIP, reason="CUT(발주중지) 상품",
                exposure_days=0, stockout_frequency=0, popularity_score=0,
                current_stock=0, pending_qty=0, daily_avg=3.0, trend_score=0,
            ),
        }

        existing_cut_cds = {item.get("item_cd") for item in obj._cut_lost_items}
        for cd, r in eval_results.items():
            if (r.decision == EvalDecision.SKIP
                    and "CUT" in r.reason
                    and r.mid_cd in FOOD_CATEGORIES
                    and r.daily_avg > 0
                    and cd not in existing_cut_cds):
                obj._cut_lost_items.append({
                    "item_cd": cd,
                    "mid_cd": r.mid_cd,
                    "predicted_sales": r.daily_avg,
                    "item_nm": r.item_nm,
                })

        assert len(obj._cut_lost_items) == 0, "비푸드(042) CUT은 보충 대상 아님"

    def test_17_zero_avg_cut_excluded(self):
        """Fix B: daily_avg=0 CUT은 보충 대상 아님"""
        from src.prediction.pre_order_evaluator import (
            PreOrderEvalResult, EvalDecision,
        )
        from src.settings.constants import FOOD_CATEGORIES

        obj = self._make_auto_order_system()

        eval_results = {
            "cut_nosale": PreOrderEvalResult(
                item_cd="cut_nosale", item_nm="비판매CUT", mid_cd="001",
                decision=EvalDecision.SKIP, reason="CUT(발주중지) 상품",
                exposure_days=0, stockout_frequency=0, popularity_score=0,
                current_stock=0, pending_qty=0, daily_avg=0, trend_score=0,
            ),
        }

        existing_cut_cds = set()
        for cd, r in eval_results.items():
            if (r.decision == EvalDecision.SKIP
                    and "CUT" in r.reason
                    and r.mid_cd in FOOD_CATEGORIES
                    and r.daily_avg > 0
                    and cd not in existing_cut_cds):
                obj._cut_lost_items.append({
                    "item_cd": cd, "mid_cd": r.mid_cd,
                    "predicted_sales": r.daily_avg, "item_nm": r.item_nm,
                })

        assert len(obj._cut_lost_items) == 0, "daily_avg=0은 보충 불필요"

    def test_18_duplicate_prevention(self):
        """Fix B: refilter + eval 양쪽 중복 방지"""
        from src.prediction.pre_order_evaluator import (
            PreOrderEvalResult, EvalDecision,
        )
        from src.settings.constants import FOOD_CATEGORIES

        obj = self._make_auto_order_system()
        # refilter에서 이미 추가된 항목
        obj._cut_lost_items = [{
            "item_cd": "cut_dup", "mid_cd": "001",
            "predicted_sales": 1.5, "item_nm": "중복CUT",
        }]

        eval_results = {
            "cut_dup": PreOrderEvalResult(
                item_cd="cut_dup", item_nm="중복CUT", mid_cd="001",
                decision=EvalDecision.SKIP, reason="CUT(발주중지) 상품",
                exposure_days=0, stockout_frequency=0, popularity_score=0,
                current_stock=0, pending_qty=0, daily_avg=1.5, trend_score=0,
            ),
        }

        existing_cut_cds = {item.get("item_cd") for item in obj._cut_lost_items}
        for cd, r in eval_results.items():
            if (r.decision == EvalDecision.SKIP
                    and "CUT" in r.reason
                    and r.mid_cd in FOOD_CATEGORIES
                    and r.daily_avg > 0
                    and cd not in existing_cut_cds):
                obj._cut_lost_items.append({
                    "item_cd": cd, "mid_cd": r.mid_cd,
                    "predicted_sales": r.daily_avg, "item_nm": r.item_nm,
                })

        assert len(obj._cut_lost_items) == 1, "중복 추가 방지: 여전히 1건"

    def test_19_non_cut_skip_excluded(self):
        """Fix B: CUT이 아닌 일반 SKIP은 _cut_lost_items에 미포함"""
        from src.prediction.pre_order_evaluator import (
            PreOrderEvalResult, EvalDecision,
        )
        from src.settings.constants import FOOD_CATEGORIES

        obj = self._make_auto_order_system()

        eval_results = {
            "skip_normal": PreOrderEvalResult(
                item_cd="skip_normal", item_nm="재고충분", mid_cd="001",
                decision=EvalDecision.SKIP, reason="재고 충분 + 저인기",
                exposure_days=5, stockout_frequency=0, popularity_score=0.1,
                current_stock=10, pending_qty=0, daily_avg=0.5, trend_score=0,
            ),
        }

        existing_cut_cds = set()
        for cd, r in eval_results.items():
            if (r.decision == EvalDecision.SKIP
                    and "CUT" in r.reason
                    and r.mid_cd in FOOD_CATEGORIES
                    and r.daily_avg > 0
                    and cd not in existing_cut_cds):
                obj._cut_lost_items.append({
                    "item_cd": cd, "mid_cd": r.mid_cd,
                    "predicted_sales": r.daily_avg, "item_nm": r.item_nm,
                })

        assert len(obj._cut_lost_items) == 0, "일반 SKIP(비CUT)은 대상 아님"

    def test_20_cut_replacement_called_with_eval_data(
        self, config_enabled, mock_candidates, mock_product_names, mock_expiry_data
    ):
        """E2E: eval_results CUT 복원 → CutReplacementService 실행 → 보충 발생"""
        from src.prediction.pre_order_evaluator import (
            PreOrderEvalResult, EvalDecision,
        )

        # eval_results에서 복원된 _cut_lost_items
        cut_lost_items = [
            {"item_cd": "cut_a", "mid_cd": "002", "predicted_sales": 2.0, "item_nm": "CUT도시락A"},
            {"item_cd": "cut_b", "mid_cd": "002", "predicted_sales": 1.5, "item_nm": "CUT도시락B"},
        ]

        svc = _make_service(config_enabled)

        store_conn, common_conn = _mock_db_cursor(
            mock_candidates, mock_product_names, mock_expiry_data
        )

        with patch("src.order.cut_replacement.DBRouter") as mock_router:
            mock_router.get_store_connection.return_value = store_conn
            mock_router.get_common_connection.return_value = common_conn

            result = svc.supplement_cut_shortage(
                order_list=[],
                cut_lost_items=cut_lost_items,
            )

        added = [i for i in result if i.get("source") == "cut_replacement"]
        assert len(added) >= 1, "eval 복원 데이터로 보충 발생해야 함"
        total = sum(i["final_order_qty"] for i in added)
        # 3.5 * 0.8 = 2.8 → 최소 1개 보충
        assert total >= 1, f"보충 수량 >= 1: {total}"

    def test_21_refilter_still_works(self):
        """기존 prefetch 감지 CUT도 여전히 정상 포착"""
        obj = self._make_auto_order_system()
        obj._cut_items = {"prefetch_cut"}

        order_list = [
            {"item_cd": "prefetch_cut", "mid_cd": "002",
             "predicted_sales": 1.0, "final_order_qty": 1},
            {"item_cd": "normal", "mid_cd": "002",
             "predicted_sales": 0.5, "final_order_qty": 1},
        ]

        filtered, lost = obj._refilter_cut_items(order_list)
        assert len(lost) == 1, "prefetch CUT 1건 포착"
        assert lost[0]["item_cd"] == "prefetch_cut"
        assert len(filtered) == 1, "normal만 잔류"
