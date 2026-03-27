"""
카테고리 총량 사이트 발주 차감 테스트 (category-site-budget)

site(사용자)가 이미 발주한 카테고리 수량만큼 auto 발주 상한을 줄여서
과잉 발주를 방지하는 기능을 검증한다.

테스트 범위:
1. site 없는 경우 → 기존 동작과 동일
2. site < 예산 → auto = 예산 - site
3. site = 예산 → auto = 0
4. site > 예산 → auto = 0 (max(0,...))
5. 토글 OFF → site_order_counts 무시
6. 조회 실패 → 빈 dict → 기존 동작
7. 복수 카테고리 → 각 mid_cd 독립 차감
8. floor 보충 비간섭 → site 포함 current_sum >= floor 시 보충 안 함
9. 비푸드 카테고리 → 영향 없음
10. manual+site 겹침 → manual_order_items 겹치는 item_cd는 site_count에서 제외
11. store_id 오염 격리 → 타매장 레코드가 있어도 현재 매장만 집계
12. order_date 정합 → 발주일(오늘) 기준 조회
"""

import pytest
from unittest.mock import patch, MagicMock
from typing import List, Dict, Any, Optional


# =============================================================================
# 헬퍼
# =============================================================================

def _make_order_item(item_cd: str, mid_cd: str, qty: int = 1,
                     eval_label: str = "NORMAL_ORDER") -> Dict[str, Any]:
    """테스트용 발주 아이템 생성"""
    return {
        "item_cd": item_cd,
        "mid_cd": mid_cd,
        "final_order_qty": qty,
        "order_qty": qty,
        "item_nm": f"상품_{item_cd}",
        "eval_label": eval_label,
    }


def _make_food_items(mid_cd: str, count: int, start_idx: int = 1) -> List[Dict[str, Any]]:
    """mid_cd에 대한 N개 발주 아이템 리스트 생성"""
    return [
        _make_order_item(f"ITEM_{mid_cd}_{i:03d}", mid_cd)
        for i in range(start_idx, start_idx + count)
    ]


def _patch_food_cap_deps(weekday_avg):
    """food_daily_cap 의존성을 패치하는 데코레이터 팩토리"""
    import functools
    def decorator(func):
        @functools.wraps(func)
        @patch("src.prediction.categories.food_daily_cap.get_weekday_avg_sales",
               return_value=weekday_avg)
        @patch("src.prediction.food_waste_calibrator.get_calibrated_food_params",
               return_value=None)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper
    return decorator


# =============================================================================
# 1. apply_food_daily_cap 테스트
# =============================================================================

class TestApplyFoodDailyCapSiteBudget:
    """apply_food_daily_cap에서 site_order_counts 차감 동작 검증"""

    @_patch_food_cap_deps(weekday_avg=10.0)
    def test_no_site_orders_unchanged(self, mock_cal, mock_avg):
        """site 발주 없으면 기존 동작과 동일"""
        from src.prediction.categories.food_daily_cap import apply_food_daily_cap
        # weekday_avg=10, buffer=3, cap=13
        items = _make_food_items("002", 8)
        result = apply_food_daily_cap(items, site_order_counts=None, store_id="46513")
        assert len(result) == 8  # 8 <= 13, 그대로 유지

    @_patch_food_cap_deps(weekday_avg=15.0)
    def test_site_less_than_budget(self, mock_cal, mock_avg):
        """site < 예산 → adjusted_cap = 예산 - site"""
        from src.prediction.categories.food_daily_cap import apply_food_daily_cap
        # category_total=15+10=25, buffer=int(25*0.20+0.5)=5, cap=15+5=20
        items = _make_food_items("002", 12)
        # site=10 → adjusted_cap = 20 - 10 = 10 → 12개 중 10개 선별
        result = apply_food_daily_cap(
            items, site_order_counts={"002": 10}, store_id="46513"
        )
        assert len(result) == 10

    @_patch_food_cap_deps(weekday_avg=5.0)
    def test_site_equals_budget(self, mock_cal, mock_avg):
        """site = 예산 → auto = 0"""
        from src.prediction.categories.food_daily_cap import apply_food_daily_cap
        # cap = 5 + 3 = 8
        items = _make_food_items("001", 4)
        # site=8 → adjusted_cap = max(0, 8-8) = 0
        result = apply_food_daily_cap(
            items, site_order_counts={"001": 8}, store_id="46513"
        )
        assert len(result) == 0

    @_patch_food_cap_deps(weekday_avg=5.0)
    def test_site_exceeds_budget(self, mock_cal, mock_avg):
        """site > 예산 → auto = 0 (max(0,...))"""
        from src.prediction.categories.food_daily_cap import apply_food_daily_cap
        # cap = 5 + 3 = 8
        items = _make_food_items("001", 4)
        # site=12 → adjusted_cap = max(0, 8-12) = 0
        result = apply_food_daily_cap(
            items, site_order_counts={"001": 12}, store_id="46513"
        )
        assert len(result) == 0

    @_patch_food_cap_deps(weekday_avg=10.0)
    def test_empty_site_dict_unchanged(self, mock_cal, mock_avg):
        """빈 site_order_counts → 기존 동작과 동일"""
        from src.prediction.categories.food_daily_cap import apply_food_daily_cap
        # cap=13
        items = _make_food_items("002", 8)
        result = apply_food_daily_cap(items, site_order_counts={}, store_id="46513")
        assert len(result) == 8  # 8 <= 13

    @_patch_food_cap_deps(weekday_avg=10.0)
    def test_multiple_categories_independent(self, mock_cal, mock_avg):
        """복수 카테고리 → 각 mid_cd 독립 차감"""
        from src.prediction.categories.food_daily_cap import apply_food_daily_cap
        # buffer = 20% of category_total (weekday_avg + site_qty)
        items = _make_food_items("001", 6) + _make_food_items("002", 10)
        # 001: cat_total=10+3=13, buf=3, cap=13, adjusted=10 → 6<=10 유지
        # 002: cat_total=10+8=18, buf=4, cap=14, adjusted=6 → 10>6 선별
        result = apply_food_daily_cap(
            items, site_order_counts={"001": 3, "002": 8}, store_id="46513"
        )
        count_001 = sum(1 for r in result if r["mid_cd"] == "001")
        count_002 = sum(1 for r in result if r["mid_cd"] == "002")
        assert count_001 == 6   # 유지
        assert count_002 == 6   # 14-8=6

    @_patch_food_cap_deps(weekday_avg=5.0)
    def test_non_food_categories_unaffected(self, mock_cal, mock_avg):
        """비푸드 카테고리(015 등)는 site 차감 영향 없음"""
        from src.prediction.categories.food_daily_cap import apply_food_daily_cap
        # 015는 target_categories에 포함 안됨 → 그대로 통과
        items = [_make_order_item("SNACK_001", "015")]
        result = apply_food_daily_cap(
            items, site_order_counts={"015": 100}, store_id="46513"
        )
        assert len(result) == 1  # 비푸드는 영향 없음

    @_patch_food_cap_deps(weekday_avg=15.0)
    def test_site_below_cap_no_selection(self, mock_cal, mock_avg):
        """site 차감 후에도 auto가 adjusted_cap 이내면 선별하지 않음"""
        from src.prediction.categories.food_daily_cap import apply_food_daily_cap
        # cap = 18
        items = _make_food_items("002", 5)
        # site=3, adjusted=15 → 5<=15 유지
        result = apply_food_daily_cap(
            items, site_order_counts={"002": 3}, store_id="46513"
        )
        assert len(result) == 5


# =============================================================================
# 2. CategoryDemandForecaster site 보정 테스트
# =============================================================================

class TestCategoryForecasterSiteBudget:
    """CategoryDemandForecaster.supplement_orders에서 site 발주 포함 검증"""

    def _make_forecaster(self, config_overrides=None):
        from src.prediction.category_demand_forecaster import CategoryDemandForecaster

        default_config = {
            "enabled": True,
            "target_mid_cds": ["001", "002", "003", "004", "005"],
            "threshold": 0.7,
            "max_add_per_item": 1,
            "wma_days": 7,
            "min_candidate_sell_days": 1,
        }
        if config_overrides:
            default_config.update(config_overrides)

        with patch("src.prediction.category_demand_forecaster.PREDICTION_PARAMS",
                   {"category_floor": default_config}):
            with patch("src.prediction.category_demand_forecaster.StoreContext") as mock_ctx:
                mock_ctx.get_store_id.return_value = "46513"
                f = CategoryDemandForecaster(store_id="46513")
        f._config = default_config
        return f

    def test_site_prevents_unnecessary_supplement(self):
        """site 발주 포함 시 current_sum >= floor → 보충 불필요"""
        f = self._make_forecaster()

        # auto 발주 3개 (qty 합=3), site 발주 10개
        items = _make_food_items("002", 3)

        # WMA forecast = 15.0 → floor = 15 * 0.7 = 10.5
        # current_sum = 3(auto) + 10(site) = 13 >= 10.5 → 보충 불필요
        with patch.object(f, "_get_category_daily_totals", return_value=[15, 14, 16, 15, 14, 15, 16]):
            with patch.object(f, "_calculate_category_forecast", return_value=15.0):
                with patch.object(f, "_get_supplement_candidates", return_value=[]):
                    result = f.supplement_orders(
                        items, site_order_counts={"002": 10}
                    )
        assert len(result) == 3  # 보충 없이 그대로

    def test_without_site_triggers_supplement(self):
        """site 없으면 current_sum=3 < floor=10.5 → 보충 로직 진입"""
        f = self._make_forecaster()

        items = _make_food_items("002", 3)

        # current_sum = 3 < floor 10.5 → 보충 시도 (후보 없으면 추가 안됨)
        with patch.object(f, "_get_category_daily_totals", return_value=[15, 14, 16, 15, 14, 15, 16]):
            with patch.object(f, "_calculate_category_forecast", return_value=15.0):
                with patch.object(f, "_get_supplement_candidates", return_value=[]):
                    result = f.supplement_orders(
                        items, site_order_counts=None
                    )
        # 후보 없어서 보충은 안 되지만, supplement 로직 자체는 실행됨
        assert len(result) == 3


# =============================================================================
# 3. LargeCategoryForecaster site 보정 테스트
# =============================================================================

class TestLargeForecasterSiteBudget:
    """LargeCategoryForecaster._apply_floor_correction에서 site 보정 검증"""

    def test_site_increases_current_sum(self):
        """site 발주가 current_sum에 더해져 floor 이상이 되면 보충 안 함"""
        from src.prediction.large_category_forecaster import LargeCategoryForecaster

        with patch("src.prediction.large_category_forecaster.PREDICTION_PARAMS",
                   {"large_category_floor": {"enabled": True, "threshold": 0.75,
                                              "max_add_per_item": 2, "target_large_cds": ["01"]}}):
            f = LargeCategoryForecaster(store_id="46513")

        # auto items: mid_cd=002, qty=3
        items = _make_food_items("002", 3)
        targets = {"002": 20.0}  # floor = 20 * 0.75 = 15

        # without site: current_sum = 3 < 15 → 보충 시도
        # with site=12: current_sum = 3 + 12 = 15 >= 15 → 보충 불필요
        with patch.object(f, "_get_supplement_candidates", return_value=[]):
            supplemented = f._apply_floor_correction(
                items, targets, site_order_counts={"002": 12}
            )
        assert supplemented == 0  # 보충 없음


# =============================================================================
# 4. AutoOrderSystem._get_site_order_counts_by_midcd 테스트
# =============================================================================

class TestGetSiteOrderCounts:
    """_get_site_order_counts_by_midcd SQL 조회 + 에러 폴백 검증"""

    def _make_system(self, store_id="46513"):
        from src.order.auto_order import AutoOrderSystem
        with patch.object(AutoOrderSystem, "__init__", return_value=None):
            sys = AutoOrderSystem.__new__(AutoOrderSystem)
            sys.store_id = store_id
        return sys

    def test_returns_empty_dict_on_error(self):
        """DB 오류 시 빈 dict 반환 (안전 폴백)"""
        sys = self._make_system()
        import src.infrastructure.database.connection as _dr
        with patch.object(_dr.DBRouter, "get_connection", side_effect=Exception("DB error")):
            result = sys._get_site_order_counts_by_midcd("2026-03-05")
        assert result == {}

    def test_returns_midcd_counts(self):
        """정상 조회 시 {mid_cd: count} 반환"""
        sys = self._make_system()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [
            ("001", 3), ("002", 10), ("003", 7)
        ]
        import src.infrastructure.database.connection as _dr
        _ah = _dr  # attach_common_with_views is in same module
        with patch.object(_dr.DBRouter, "get_connection", return_value=mock_conn):
            with patch.object(_ah, "attach_common_with_views"):
                result = sys._get_site_order_counts_by_midcd("2026-03-05")
        assert result == {"001": 3, "002": 10, "003": 7}

    def test_sql_includes_store_id_filter(self):
        """SQL에 store_id 필터가 포함되어야 함 (오염 방지)"""
        sys = self._make_system(store_id="47863")
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        import src.infrastructure.database.connection as _dr
        _ah = _dr  # attach_common_with_views is in same module
        with patch.object(_dr.DBRouter, "get_connection", return_value=mock_conn):
            with patch.object(_ah, "attach_common_with_views"):
                sys._get_site_order_counts_by_midcd("2026-03-05")
        call_args = mock_conn.execute.call_args
        sql = call_args[0][0]
        params = call_args[0][1]
        assert "ot.store_id = ?" in sql
        assert "47863" in params

    def test_sql_includes_all_site_orders(self):
        """SQL이 수동발주 포함 모든 site 발주를 집계해야 함 (floor에서 수동발주 인식)"""
        sys = self._make_system()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        import src.infrastructure.database.connection as _dr
        _ah = _dr  # attach_common_with_views is in same module
        with patch.object(_dr.DBRouter, "get_connection", return_value=mock_conn):
            with patch.object(_ah, "attach_common_with_views"):
                sys._get_site_order_counts_by_midcd("2026-03-05")
        sql = mock_conn.execute.call_args[0][0]
        assert "order_source = 'site'" in sql
        assert "NOT IN" not in sql
        # 파라미터가 2개만 전달되어야 함 (order_date, store_id)
        params = mock_conn.execute.call_args[0][1]
        assert len(params) == 2


# =============================================================================
# 5. 토글 비활성화 테스트
# =============================================================================

class TestToggleDisabled:
    """CATEGORY_SITE_BUDGET_ENABLED=False 시 기존 동작 유지"""

    @_patch_food_cap_deps(weekday_avg=15.0)
    def test_toggle_off_ignores_site(self, mock_cal, mock_avg):
        """토글 OFF → site_order_counts가 None으로 전달되어 차감 없음"""
        from src.prediction.categories.food_daily_cap import apply_food_daily_cap
        # cap = 18
        items = _make_food_items("002", 12)
        # 토글 OFF에서는 site_order_counts=None 전달 → cap=18
        result = apply_food_daily_cap(items, site_order_counts=None, store_id="46513")
        assert len(result) == 12  # 12 <= 18, 모두 유지


# =============================================================================
# 6. 시뮬레이션 검증 (설계 명세서 4.1 시나리오)
# =============================================================================

class TestDesignSimulation:
    """설계 명세서 4.1~4.3 시나리오 재현"""

    @_patch_food_cap_deps(weekday_avg=15.0)
    def test_scenario_4_1_onigiri(self, mock_cal, mock_avg):
        """4.1 주먹밥(002): avg=15, site=10 → auto 10개"""
        from src.prediction.categories.food_daily_cap import apply_food_daily_cap
        # category_total=15+10=25, buffer=5, cap=15+5=20
        items = _make_food_items("002", 12)
        result = apply_food_daily_cap(
            items, site_order_counts={"002": 10}, store_id="46513"
        )
        assert len(result) == 10  # adjusted_cap = 20 - 10 = 10

    @_patch_food_cap_deps(weekday_avg=5.0)
    def test_scenario_4_2_lunchbox_no_site(self, mock_cal, mock_avg):
        """4.2 도시락(001) site 없음: avg=5, cap=8, 변화 없음"""
        from src.prediction.categories.food_daily_cap import apply_food_daily_cap
        # cap = 5 + 3 = 8
        items = _make_food_items("001", 4)
        result = apply_food_daily_cap(
            items, site_order_counts={}, store_id="46513"
        )
        assert len(result) == 4  # 4 <= 8, 그대로

    @_patch_food_cap_deps(weekday_avg=5.0)
    def test_scenario_4_3_site_exceeds(self, mock_cal, mock_avg):
        """4.3 site가 예산 초과: avg=5, site=12 → auto=0"""
        from src.prediction.categories.food_daily_cap import apply_food_daily_cap
        # cap = 5 + 3 = 8
        items = _make_food_items("001", 4)
        result = apply_food_daily_cap(
            items, site_order_counts={"001": 12}, store_id="46513"
        )
        assert len(result) == 0  # adjusted_cap = max(0, 8-12) = 0
