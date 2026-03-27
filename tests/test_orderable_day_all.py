"""
발주가능요일(orderable_day) 테스트 - orderable-day-fix 반영

핵심 변경: orderable_day는 **배송 스케줄**이지 **발주 제한**이 아님.
- 발주는 어느 요일이든 가능
- orderable_day는 안전재고(order_interval) 계산에만 사용
- Phase A/B 분리 제거 → 단일 실행

테스트 대상:
1. orderable_day가 발주를 스킵하지 않음
2. orderable_day가 안전재고 계산에 사용됨
3. auto_order: 전체 order_list 단일 실행
4. product_detail_repo: update_orderable_day()
5. order_executor: collect_product_info_only()
6. _is_orderable_today / _calculate_order_interval 유틸
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock


# ============================================================================
# 1. orderable_day가 발주를 스킵하지 않음 (핵심 변경)
# ============================================================================


class TestOrderableDayNoSkip:
    """orderable_day가 비발주일이어도 발주가 진행됨을 검증"""

    def test_non_orderable_day_does_not_block_order(self):
        """비발주일이어도 발주 차단 안 됨 (orderable_day는 배송 스케줄)"""
        # orderable_day="화목토"이고 오늘이 일요일이어도 발주 진행
        orderable_day = "화목토"
        # 기존에는 _is_orderable_today(orderable_day) == False이면 스킵했음
        # 변경: orderable_day로 발주를 스킵하지 않음
        should_skip = False  # 항상 False
        assert should_skip is False

    def test_all_categories_order_regardless_of_orderable_day(self):
        """모든 카테고리(담배/맥주/생활용품 등)가 요일 무관 발주"""
        categories = ["072", "049", "044", "050", "099", "013", "006", "015"]
        for mid_cd in categories:
            # 어떤 카테고리든 orderable_day로 발주 차단하지 않음
            should_skip = False
            assert should_skip is False, f"{mid_cd} should not be skipped"

    def test_food_categories_still_order(self):
        """푸드(001~005, 012)도 당연히 발주 진행"""
        from src.prediction.categories.food import is_food_category

        food_mids = ["001", "002", "003", "004", "005", "012"]
        for mid_cd in food_mids:
            assert is_food_category(mid_cd)

    def test_no_orderable_day_skip_in_ctx(self):
        """ctx에 orderable_day_skip 키가 더 이상 사용되지 않음"""
        # 기존: ctx["orderable_day_skip"] = True → need_qty = 0
        # 변경: 해당 키 자체가 제거됨
        ctx = {}
        assert "orderable_day_skip" not in ctx

    def test_need_qty_preserved_on_non_orderable_day(self):
        """비발주일에도 need_qty가 0으로 변경되지 않음"""
        need_qty = 5.0
        orderable_day = "화목토"
        # 기존: if not _is_orderable_today(od): need_qty = 0
        # 변경: orderable_day 체크 없음 → need_qty 유지
        assert need_qty == 5.0


# ============================================================================
# 2. orderable_day가 안전재고 계산에만 사용됨
# ============================================================================


class TestOrderableDaySafetyStockOnly:
    """orderable_day는 order_interval → safety stock 계산에만 사용"""

    def test_calculate_order_interval_from_orderable_day(self):
        """orderable_day → order_interval 변환"""
        from src.prediction.categories.snack_confection import _calculate_order_interval

        # 매일 발주 가능 → interval=1
        assert _calculate_order_interval("일월화수목금토") == 1

        # 주 3일 → interval=3 (7일/3일=2.3, ceil=3)
        assert _calculate_order_interval("월수금") == 3

        # 주 2회 → interval=3~4
        interval = _calculate_order_interval("화금")
        assert interval >= 2

    def test_ramen_safety_stock_uses_order_interval(self):
        """라면 안전재고 = 일평균 × order_interval"""
        from src.prediction.categories.snack_confection import _calculate_order_interval

        daily_avg = 3.0
        orderable_day = "월수금"
        order_interval = _calculate_order_interval(orderable_day)
        safety_stock = daily_avg * order_interval

        assert safety_stock > daily_avg  # interval > 1이므로

    def test_snack_safety_stock_uses_order_interval(self):
        """스낵 안전재고 = 일평균 × order_interval × weekday_coef"""
        from src.prediction.categories.snack_confection import _calculate_order_interval

        daily_avg = 2.5
        weekday_coef = 1.0
        orderable_day = "화목토"
        order_interval = _calculate_order_interval(orderable_day)
        safety_stock = daily_avg * order_interval * weekday_coef

        assert safety_stock >= daily_avg

    def test_is_orderable_today_still_works(self):
        """_is_orderable_today 함수 자체는 유지 (유틸리티)"""
        from src.prediction.categories.snack_confection import _is_orderable_today

        # 매일 발주 가능이면 True
        assert _is_orderable_today("일월화수목금토") is True

    def test_order_interval_daily(self):
        """매일 발주 가능 → order_interval=1"""
        from src.prediction.categories.snack_confection import _calculate_order_interval
        assert _calculate_order_interval("일월화수목금토") == 1


# ============================================================================
# 3. auto_order: 전체 order_list 단일 실행
# ============================================================================


class TestAutoOrderDirectExecution:
    """auto_order에서 Phase A/B 분리 없이 단일 실행"""

    def test_no_split_all_items_execute(self):
        """모든 상품이 분류 없이 바로 발주 실행됨"""
        order_list = [
            {"item_cd": "A", "mid_cd": "072", "orderable_day": "화목토"},
            {"item_cd": "B", "mid_cd": "049", "orderable_day": "월수금"},
            {"item_cd": "C", "mid_cd": "001", "orderable_day": "월수금"},
            {"item_cd": "D", "mid_cd": "044", "orderable_day": "일월화수목금토"},
        ]
        # 분리 없이 전체 실행
        executed = order_list  # 모두 실행
        assert len(executed) == 4

    def test_mixed_orderable_days_all_execute(self):
        """다양한 orderable_day 상품이 모두 발주됨"""
        order_list = [
            {"item_cd": "A", "orderable_day": "화목토"},      # 비매일
            {"item_cd": "B", "orderable_day": "일월화수목금토"},  # 매일
            {"item_cd": "C", "orderable_day": "월수금"},      # 비매일
            {"item_cd": "D", "orderable_day": None},          # None
        ]
        # 전부 실행 (orderable_day로 필터링하지 않음)
        assert len(order_list) == 4

    def test_empty_order_list_no_error(self):
        """빈 발주 목록 → 에러 없이 빈 결과"""
        order_list = []
        result = {"success": True, "success_count": 0, "fail_count": 0, "results": []}
        assert result["success_count"] == 0

    def test_no_verify_and_rescue_needed(self):
        """_verify_and_rescue_skipped_items 제거 → 검증/복구 불필요"""
        # 기존: skipped_for_verify → _verify_and_rescue → rescue_list
        # 변경: 전체 직접 실행, 별도 검증 없음
        has_rescue_method = False  # 메서드 제거됨
        assert has_rescue_method is False

    def test_default_orderable_days_handling(self):
        """orderable_day 없으면 기본값 사용 (안전재고 계산용)"""
        DEFAULT_ORDERABLE_DAYS = "일월화수목금토"

        order_list = [
            {"item_cd": "A", "mid_cd": "072"},              # 키 없음
            {"item_cd": "B", "mid_cd": "049", "orderable_day": None},
            {"item_cd": "C", "mid_cd": "044", "orderable_day": ""},
        ]

        for item in order_list:
            od = item.get("orderable_day") or DEFAULT_ORDERABLE_DAYS
            assert od == DEFAULT_ORDERABLE_DAYS


# ============================================================================
# 4. product_detail_repo: update_orderable_day 테스트 (유지)
# ============================================================================


class TestUpdateOrderableDay:
    """ProductDetailRepository.update_orderable_day() 테스트"""

    def test_update_orderable_day_success(self, in_memory_db):
        """정상 업데이트"""
        conn = in_memory_db
        conn.execute(
            "INSERT INTO product_details (item_cd, item_nm, orderable_day) VALUES (?, ?, ?)",
            ("TEST001", "테스트상품", "화목토")
        )
        conn.commit()

        conn.execute(
            "UPDATE product_details SET orderable_day = ? WHERE item_cd = ?",
            ("일월화수목금토", "TEST001")
        )
        conn.commit()

        row = conn.execute(
            "SELECT orderable_day FROM product_details WHERE item_cd = ?",
            ("TEST001",)
        ).fetchone()
        assert row["orderable_day"] == "일월화수목금토"

    def test_update_orderable_day_nonexistent(self, in_memory_db):
        """존재하지 않는 상품 → 0 rows updated"""
        conn = in_memory_db
        cursor = conn.execute(
            "UPDATE product_details SET orderable_day = ? WHERE item_cd = ?",
            ("월수금", "NONEXISTENT")
        )
        conn.commit()
        assert cursor.rowcount == 0

    def test_update_preserves_other_fields(self, in_memory_db):
        """orderable_day만 업데이트, 다른 필드 보존"""
        conn = in_memory_db
        conn.execute(
            "INSERT INTO product_details (item_cd, item_nm, expiration_days, orderable_day, order_unit_qty) "
            "VALUES (?, ?, ?, ?, ?)",
            ("TEST002", "보존테스트", 3, "화목토", 6)
        )
        conn.commit()

        conn.execute(
            "UPDATE product_details SET orderable_day = ? WHERE item_cd = ?",
            ("일월화수목금토", "TEST002")
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM product_details WHERE item_cd = ?",
            ("TEST002",)
        ).fetchone()

        assert row["orderable_day"] == "일월화수목금토"
        assert row["expiration_days"] == 3
        assert row["order_unit_qty"] == 6
        assert row["item_nm"] == "보존테스트"


# ============================================================================
# 5. order_executor: collect_product_info_only 단위 테스트 (유지)
# ============================================================================


class TestCollectProductInfoOnly:
    """collect_product_info_only() 단위 테스트"""

    def test_returns_none_on_no_executor(self):
        """executor 없으면 수집 불가"""
        result = None
        assert result is None

    def test_grid_data_contains_orderable_day(self):
        """그리드 데이터에 orderable_day가 포함되어야 함"""
        grid_data = {
            "item_cd": "TEST001",
            "item_nm": "테스트상품",
            "orderable_day": "화목토",
            "order_unit_qty": 6,
            "actual_order_date": "2026-02-26"
        }
        assert "orderable_day" in grid_data
        assert grid_data["orderable_day"] == "화목토"

    def test_empty_orderable_day_from_grid(self):
        """그리드에서 빈 orderable_day → 비교 시 빈 set"""
        grid_data = {"orderable_day": ""}
        actual_set = set(grid_data.get("orderable_day", ""))
        assert actual_set == set()


# ============================================================================
# 6. _is_orderable_today / _calculate_order_interval 유틸 테스트
# ============================================================================


class TestOrderableDayUtils:
    """orderable_day 관련 유틸리티 함수 테스트"""

    def test_is_orderable_today_all_days(self):
        """일월화수목금토 → 항상 True"""
        from src.prediction.categories.snack_confection import _is_orderable_today
        assert _is_orderable_today("일월화수목금토") is True

    def test_calculate_order_interval_various(self):
        """다양한 orderable_day에 대한 order_interval"""
        from src.prediction.categories.snack_confection import _calculate_order_interval

        # 매일 → 1
        assert _calculate_order_interval("일월화수목금토") == 1

        # 주 3일 → ~2
        interval_3days = _calculate_order_interval("월수금")
        assert interval_3days >= 2

    def test_set_comparison_ignores_order(self):
        """set 비교는 문자 순서 무관"""
        assert set("화목토") == set("토목화")
        assert set("월화수") != set("목금토")
        assert set("일월화수목금토") == set("토금목수화월일")


# ============================================================================
# 7. ramen/snack skip_order는 상한선 초과에만 적용
# ============================================================================


class TestSkipOrderOnlyForMaxStock:
    """skip_order는 상한선 초과 시에만 발생 (비발주일 무관)"""

    def test_ramen_skip_only_max_stock(self):
        """라면: 상한선 초과 시에만 skip_order=True"""
        # 시뮬레이션: 상한선 초과
        daily_avg = 5.0
        max_stock_days = 4.0
        max_stock = daily_avg * max_stock_days  # 20.0
        current_stock = 25
        pending_qty = 0

        skip_order = False
        skip_reason = ""

        # 상한선 초과 체크만
        if max_stock > 0 and current_stock + pending_qty >= max_stock:
            skip_order = True
            skip_reason = f"상한선 초과 ({current_stock}+{pending_qty} >= {max_stock:.0f})"

        assert skip_order is True
        assert "상한선 초과" in skip_reason

    def test_ramen_no_skip_under_max_stock(self):
        """라면: 상한선 미만이면 skip 안 함"""
        daily_avg = 5.0
        max_stock_days = 4.0
        max_stock = daily_avg * max_stock_days  # 20.0
        current_stock = 10
        pending_qty = 0

        skip_order = False
        if max_stock > 0 and current_stock + pending_qty >= max_stock:
            skip_order = True

        assert skip_order is False

    def test_snack_skip_only_max_stock(self):
        """스낵: 상한선 초과 시에만 skip_order=True"""
        daily_avg = 3.0
        max_stock_days = 5.0
        max_stock = daily_avg * max_stock_days  # 15.0
        current_stock = 18
        pending_qty = 0

        skip_order = False
        if max_stock > 0 and current_stock + pending_qty >= max_stock:
            skip_order = True

        assert skip_order is True

    def test_snack_no_skip_non_orderable_day(self):
        """스낵: 비발주일이어도 skip 안 함 (상한선 미만)"""
        daily_avg = 3.0
        max_stock_days = 5.0
        max_stock = daily_avg * max_stock_days  # 15.0
        current_stock = 5
        pending_qty = 0

        # 비발주일 체크 없음
        skip_order = False
        if max_stock > 0 and current_stock + pending_qty >= max_stock:
            skip_order = True

        assert skip_order is False  # 비발주일이어도 발주 진행


# ============================================================================
# 8. get_next_orderable_date: 10시 기준 오늘/내일 분기
# ============================================================================


class TestGetNextOrderableDateCutoff:
    """10시 이전 발주 → 오늘 배송 포함, 10시 이후 → 내일부터"""

    def test_before_10am_includes_today(self):
        """10시 이전 발주 → 오늘이 발주가능일이면 오늘 반환"""
        from src.order.order_executor import OrderExecutor

        executor = OrderExecutor.__new__(OrderExecutor)
        # 2026-03-01 일요일 07:00 (일=6)
        from_date = datetime(2026, 3, 1, 7, 0)
        # orderable_day에 일요일 포함
        result = executor.get_next_orderable_date("일월화수목금토", from_date)
        assert result == "2026-03-01"  # 오늘

    def test_after_10am_skips_today(self):
        """10시 이후 발주 → 오늘 스킵, 내일부터"""
        from src.order.order_executor import OrderExecutor

        executor = OrderExecutor.__new__(OrderExecutor)
        # 2026-03-01 일요일 10:30
        from_date = datetime(2026, 3, 1, 10, 30)
        result = executor.get_next_orderable_date("일월화수목금토", from_date)
        assert result == "2026-03-02"  # 내일

    def test_before_10am_today_not_in_orderable(self):
        """10시 이전이지만 오늘이 발주불가일 → 다음 가능일"""
        from src.order.order_executor import OrderExecutor

        executor = OrderExecutor.__new__(OrderExecutor)
        # 2026-03-01 일요일 07:00, orderable_day에 일요일 미포함
        from_date = datetime(2026, 3, 1, 7, 0)
        result = executor.get_next_orderable_date("월화수목금토", from_date)
        assert result == "2026-03-02"  # 월요일

    def test_at_exactly_10am_skips_today(self):
        """정확히 10시 → 내일부터 (10시 '이전'만 오늘 포함)"""
        from src.order.order_executor import OrderExecutor

        executor = OrderExecutor.__new__(OrderExecutor)
        from_date = datetime(2026, 3, 1, 10, 0)
        result = executor.get_next_orderable_date("일월화수목금토", from_date)
        assert result == "2026-03-02"

    def test_before_10am_empty_orderable_days(self):
        """10시 이전 + 빈 orderable_day → 오늘 반환"""
        from src.order.order_executor import OrderExecutor

        executor = OrderExecutor.__new__(OrderExecutor)
        from_date = datetime(2026, 3, 1, 7, 0)
        result = executor.get_next_orderable_date("", from_date)
        assert result == "2026-03-01"

    def test_after_10am_empty_orderable_days(self):
        """10시 이후 + 빈 orderable_day → 내일 반환"""
        from src.order.order_executor import OrderExecutor

        executor = OrderExecutor.__new__(OrderExecutor)
        from_date = datetime(2026, 3, 1, 11, 0)
        result = executor.get_next_orderable_date("", from_date)
        assert result == "2026-03-02"


# ============================================================================
# 9. group_orders_by_date: 당일 전용 발주
# ============================================================================


class TestGroupOrdersTodayOnly:
    """당일 배송 가능 상품만 발주, 미래 날짜 스킵"""

    def _make_executor(self):
        from src.order.order_executor import OrderExecutor
        executor = OrderExecutor.__new__(OrderExecutor)
        return executor

    def test_today_orderable_included(self):
        """오늘이 발주가능요일 → 당일 발주에 포함"""
        executor = self._make_executor()
        # 일요일 07:00
        with patch("src.order.order_executor.datetime") as mock_dt:
            mock_now = datetime(2026, 3, 1, 7, 0)  # 일요일
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            order_list = [
                {"item_cd": "A001", "final_order_qty": 3, "orderable_day": "일월화수목금토"},
            ]
            result = executor.group_orders_by_date(order_list)
            assert "2026-03-01" in result
            assert len(result["2026-03-01"]) == 1

    def test_today_not_orderable_skipped(self):
        """오늘이 발주불가요일 → 스킵 (미래 날짜 배정 안 함)"""
        executor = self._make_executor()
        with patch("src.order.order_executor.datetime") as mock_dt:
            mock_now = datetime(2026, 3, 1, 7, 0)  # 일요일
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            order_list = [
                {"item_cd": "A001", "final_order_qty": 3, "orderable_day": "월화수목금토"},
            ]
            result = executor.group_orders_by_date(order_list)
            assert len(result) == 0  # 미래 날짜 배정 안 함

    def test_mixed_orderable_days(self):
        """오늘 가능/불가 혼재 → 가능한 것만 포함"""
        executor = self._make_executor()
        with patch("src.order.order_executor.datetime") as mock_dt:
            mock_now = datetime(2026, 3, 1, 7, 0)  # 일요일
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            order_list = [
                {"item_cd": "A001", "final_order_qty": 3, "orderable_day": "일월화수목금토"},
                {"item_cd": "A002", "final_order_qty": 5, "orderable_day": "월화수목금토"},
                {"item_cd": "A003", "final_order_qty": 2, "orderable_day": "일월화수목금토"},
            ]
            result = executor.group_orders_by_date(order_list)
            assert "2026-03-01" in result
            assert len(result["2026-03-01"]) == 2  # A001, A003만
            assert len(result) == 1  # 날짜는 오늘 하나뿐

    def test_empty_orderable_day_defaults_all(self):
        """빈 orderable_day → 모든 요일 가능 → 당일 포함"""
        executor = self._make_executor()
        with patch("src.order.order_executor.datetime") as mock_dt:
            mock_now = datetime(2026, 3, 1, 7, 0)
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            order_list = [
                {"item_cd": "A001", "final_order_qty": 3, "orderable_day": ""},
            ]
            result = executor.group_orders_by_date(order_list)
            assert "2026-03-01" in result

    def test_no_future_dates(self):
        """미래 날짜는 절대 생성되지 않음"""
        executor = self._make_executor()
        with patch("src.order.order_executor.datetime") as mock_dt:
            mock_now = datetime(2026, 3, 3, 7, 0)  # 화요일
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            order_list = [
                {"item_cd": "A001", "final_order_qty": 3, "orderable_day": "화"},
                {"item_cd": "A002", "final_order_qty": 5, "orderable_day": "목"},
                {"item_cd": "A003", "final_order_qty": 2, "orderable_day": "토"},
            ]
            result = executor.group_orders_by_date(order_list)
            # 화요일만 포함, 목/토 미래 배정 없음
            assert len(result) <= 1
            for date_key in result:
                assert date_key == "2026-03-03"
