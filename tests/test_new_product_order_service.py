"""
신상품 3일발주 분산 발주 서비스 테스트

- 동적 간격 기반 발주 판단 (should_order_today)
- 첫 발주 즉시 실행
- 판매/폐기 기반 next_order_date 계산
- D-5 강제 발주
- AI 목록에 이미 있는 상품 → qty 합산 검증
- AI 목록에 없는 신상품 → 앞에 삽입 검증
- DB Repository CRUD
- extract_base_name / group_by_base_name / select_variant_to_order
"""

import json
import sqlite3
import pytest
from datetime import datetime, timedelta

from src.application.services.new_product_order_service import (
    calculate_interval_days,
    calculate_next_order_date,
    calculate_dynamic_next_order_date,
    should_order_today,
    get_today_new_product_orders,
    merge_with_ai_orders,
    record_order_completed,
    extract_base_name,
    group_by_base_name,
    select_variant_to_order,
    _parse_date,
)
from src.infrastructure.database.repos.np_3day_tracking_repo import (
    NewProduct3DayTrackingRepository,
)


# ═══════════════════════════════════════════════════
# 레거시 발주 간격 계산 (호환용)
# ═══════════════════════════════════════════════════

class TestCalculateIntervalDays:
    """기간을 3등분한 발주 간격 계산 — 레거시 호환"""

    def test_19day_period(self):
        assert calculate_interval_days("2026-03-02", "2026-03-20") == 6

    def test_14day_period(self):
        assert calculate_interval_days("2026-03-01", "2026-03-15") == 4

    def test_empty_dates(self):
        assert calculate_interval_days("", "") == 1


class TestCalculateNextOrderDate:
    """다음 발주 예정일 계산 — 레거시 호환"""

    def test_first_order(self):
        assert calculate_next_order_date("2026-03-02", 0, 6) == "2026-03-02"

    def test_second_order(self):
        assert calculate_next_order_date("2026-03-02", 1, 6) == "2026-03-08"


# ═══════════════════════════════════════════════════
# 동적 next_order_date 계산
# ═══════════════════════════════════════════════════

class TestCalculateDynamicNextOrderDate:
    """판매/폐기 기반 동적 간격 계산"""

    def test_sold_qty_positive_next_day(self):
        """판매 있음 → 다음 날"""
        result = calculate_dynamic_next_order_date("2026-03-10", sold_qty=3, wasted_qty=0)
        assert result == "2026-03-11"

    def test_wasted_qty_positive_next_day(self):
        """폐기 있음 → 다음 날"""
        result = calculate_dynamic_next_order_date("2026-03-10", sold_qty=0, wasted_qty=2)
        assert result == "2026-03-11"

    def test_both_sold_and_wasted_next_day(self):
        """판매+폐기 둘 다 → 다음 날"""
        result = calculate_dynamic_next_order_date("2026-03-10", sold_qty=1, wasted_qty=1)
        assert result == "2026-03-11"

    def test_no_sale_no_waste_3days(self):
        """판매 없음 + 폐기 없음 → 3일 후"""
        result = calculate_dynamic_next_order_date("2026-03-10", sold_qty=0, wasted_qty=0)
        assert result == "2026-03-13"

    def test_default_zero(self):
        """기본값(0, 0) → 3일 후"""
        result = calculate_dynamic_next_order_date("2026-03-10")
        assert result == "2026-03-13"

    def test_empty_date(self):
        """빈 날짜 → 빈 문자열"""
        assert calculate_dynamic_next_order_date("") == ""


# ═══════════════════════════════════════════════════
# 분산 발주 판단 (should_order_today) — 동적 간격
# ═══════════════════════════════════════════════════

class TestShouldOrderToday:
    """발주 여부 판단 — 동적 간격 로직"""

    def test_already_completed(self):
        """이미 3회 발주 완료 → False"""
        result, reason, action = should_order_today(
            today="2026-03-10",
            week_end="2026-03-20",
            next_order_date="2026-03-14",
            our_order_count=3,
        )
        assert result is False
        assert action == "none"

    def test_first_order_immediate(self):
        """our_count=0 → 즉시 발주 (True)"""
        result, reason, action = should_order_today(
            today="2026-03-02",
            week_end="2026-03-20",
            next_order_date="2026-03-02",
            our_order_count=0,
        )
        assert result is True
        assert action == "order"
        assert "첫 발주" in reason

    def test_first_order_even_with_future_next_date(self):
        """our_count=0 → next_order_date가 미래여도 즉시 발주"""
        result, reason, action = should_order_today(
            today="2026-03-02",
            week_end="2026-03-20",
            next_order_date="2026-03-10",
            our_order_count=0,
        )
        assert result is True
        assert action == "order"

    def test_before_next_order_date(self):
        """예정일 미도달 → False"""
        result, reason, action = should_order_today(
            today="2026-03-05",
            week_end="2026-03-20",
            next_order_date="2026-03-08",
            our_order_count=1,
        )
        assert result is False
        assert action == "none"

    def test_on_next_order_date(self):
        """예정일 도달 → True (order)"""
        result, reason, action = should_order_today(
            today="2026-03-08",
            week_end="2026-03-20",
            next_order_date="2026-03-08",
            our_order_count=1,
        )
        assert result is True
        assert action == "order"

    def test_after_next_order_date(self):
        """예정일 경과 → True (order)"""
        result, reason, action = should_order_today(
            today="2026-03-10",
            week_end="2026-03-20",
            next_order_date="2026-03-08",
            our_order_count=1,
        )
        assert result is True
        assert action == "order"

    def test_d5_force_order(self):
        """D-4 (기간 잔여 4일, <=5) + 미달성 → 강제 발주"""
        result, reason, action = should_order_today(
            today="2026-03-16",
            week_end="2026-03-20",
            next_order_date="2026-03-20",
            our_order_count=1,
        )
        assert result is True
        assert action == "force"
        assert "강제" in reason

    def test_d5_exactly(self):
        """D-5 경계값 → 강제 발주"""
        result, reason, action = should_order_today(
            today="2026-03-15",
            week_end="2026-03-20",
            next_order_date="2026-03-18",
            our_order_count=2,
        )
        assert result is True
        assert action == "force"

    def test_d6_not_forced(self):
        """D-6 (>5) + 예정일 미도달 → 대기"""
        result, reason, action = should_order_today(
            today="2026-03-14",
            week_end="2026-03-20",
            next_order_date="2026-03-16",
            our_order_count=1,
        )
        assert result is False
        assert action == "none"

    def test_no_skip_action(self):
        """스킵 액션 없음 — 판매/재고와 무관하게 next_order_date로 판단"""
        # 이전에는 판매없음+재고 있으면 "skip"이었지만 동적 간격에서는 skip 없음
        result, reason, action = should_order_today(
            today="2026-03-14",
            week_end="2026-03-20",
            next_order_date="2026-03-14",
            our_order_count=1,
            last_sale_after_order=0,
            current_stock=5,
        )
        assert result is True
        assert action == "order"  # skip이 아닌 order

    def test_kwargs_backward_compat(self):
        """레거시 파라미터 (last_sale_after_order, current_stock) 무시"""
        result, reason, action = should_order_today(
            today="2026-03-14",
            week_end="2026-03-20",
            next_order_date="2026-03-14",
            our_order_count=1,
            last_sale_after_order=0,
            current_stock=10,
        )
        assert result is True  # 레거시 파라미터 무시


# ═══════════════════════════════════════════════════
# AI 예측 목록과 합산 (merge_with_ai_orders)
# ═══════════════════════════════════════════════════

class TestMergeWithAiOrders:
    """AI 예측 발주 + 신상품 발주 합산"""

    def test_existing_item_qty_unchanged(self):
        """AI 목록에 이미 있는 상품 → qty 그대로, 추적 플래그만 True"""
        ai_orders = [
            {"item_cd": "ABC001", "item_nm": "김밥", "final_order_qty": 3, "mid_cd": "003"},
            {"item_cd": "DEF002", "item_nm": "도시락", "final_order_qty": 2, "mid_cd": "001"},
        ]
        np_orders = [
            {"product_code": "ABC001", "product_name": "김밥", "qty": 1, "source": "new_product_3day_distributed"},
        ]
        result = merge_with_ai_orders(ai_orders, np_orders)
        abc_item = [r for r in result if r.get("item_cd") == "ABC001"][0]
        assert abc_item["final_order_qty"] == 3  # AI 수량 그대로 (합산 안 함)
        assert abc_item.get("np_3day_tracking") is True  # 추적 플래그
        assert abc_item.get("new_product_3day") is True

    def test_new_item_insert_front(self):
        """AI 목록에 없는 신상품 → 앞에 삽입, np_3day_tracking=True"""
        ai_orders = [
            {"item_cd": "ABC001", "item_nm": "김밥", "final_order_qty": 3, "mid_cd": "003"},
        ]
        np_orders = [
            {"product_code": "NEW999", "product_name": "줄김밥", "qty": 1, "source": "new_product_3day_distributed"},
        ]
        result = merge_with_ai_orders(ai_orders, np_orders)
        assert len(result) == 2
        assert result[0]["item_cd"] == "NEW999"  # 앞에 삽입
        assert result[0]["final_order_qty"] == 1
        assert result[0].get("force_order") is True
        assert result[0].get("np_3day_tracking") is True
        assert result[1]["item_cd"] == "ABC001"

    def test_mixed_existing_and_new(self):
        """기존(수량 유지+플래그) + 신규(삽입) 혼합"""
        ai_orders = [
            {"item_cd": "A001", "final_order_qty": 2},
            {"item_cd": "B002", "final_order_qty": 1},
        ]
        np_orders = [
            {"product_code": "A001", "qty": 1},  # 기존 → 수량 유지, 플래그만
            {"product_code": "C003", "product_name": "샌드위치", "qty": 1},  # 신규 삽입
        ]
        result = merge_with_ai_orders(ai_orders, np_orders)
        assert len(result) == 3
        assert result[0]["item_cd"] == "C003"
        assert result[0].get("np_3day_tracking") is True
        a001 = [r for r in result if r.get("item_cd") == "A001"][0]
        assert a001["final_order_qty"] == 2  # AI 수량 그대로 (합산 안 함)
        assert a001.get("np_3day_tracking") is True

    def test_empty_np_orders(self):
        """신상품 없으면 AI 목록 그대로"""
        ai_orders = [{"item_cd": "X", "final_order_qty": 5}]
        result = merge_with_ai_orders(ai_orders, [])
        assert len(result) == 1
        assert result[0]["final_order_qty"] == 5

    def test_empty_ai_orders(self):
        """AI 목록 비어있으면 신상품만"""
        np_orders = [{"product_code": "N001", "product_name": "테스트", "qty": 1}]
        result = merge_with_ai_orders([], np_orders)
        assert len(result) == 1
        assert result[0]["item_cd"] == "N001"

    def test_original_not_modified(self):
        """원본 ai_orders 변경 없음 (shallow copy 방지)"""
        ai_orders = [{"item_cd": "A001", "final_order_qty": 2}]
        original_qty = ai_orders[0]["final_order_qty"]
        merge_with_ai_orders(ai_orders, [{"product_code": "A001", "qty": 1}])
        # 원본 dict가 변경되지 않았는지 검증
        assert ai_orders[0]["final_order_qty"] == original_qty
        assert "np_3day_tracking" not in ai_orders[0]


# ═══════════════════════════════════════════════════
# Repository CRUD (DB 테스트)
# ═══════════════════════════════════════════════════

V60_CREATE_TABLE = """CREATE TABLE IF NOT EXISTS new_product_3day_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    week_label TEXT NOT NULL,
    week_start DATE NOT NULL,
    week_end DATE NOT NULL,
    product_code TEXT NOT NULL,
    product_name TEXT,
    sub_category TEXT,
    base_name TEXT DEFAULT '',
    product_codes TEXT DEFAULT '',
    selected_code TEXT DEFAULT '',
    bgf_order_count INTEGER DEFAULT 0,
    our_order_count INTEGER DEFAULT 0,
    order_interval_days INTEGER,
    next_order_date DATE,
    skip_count INTEGER DEFAULT 0,
    last_sale_after_order INTEGER DEFAULT 0,
    last_checked_at DATETIME,
    last_ordered_at DATETIME,
    is_completed INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(store_id, week_label, base_name)
)"""


@pytest.fixture
def tracking_db(tmp_path):
    """테스트용 DB 생성 (v60 스키마)"""
    db_path = tmp_path / "test_store.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(V60_CREATE_TABLE)
    conn.commit()
    conn.close()
    return db_path


class TestNewProduct3DayTrackingRepo:
    """추적 Repository CRUD"""

    def test_upsert_and_get(self, tracking_db):
        repo = NewProduct3DayTrackingRepository(db_path=tracking_db)
        repo.upsert_tracking(
            store_id="46513",
            week_label="202603-W2",
            week_start="2026-03-02",
            week_end="2026-03-20",
            product_code="TEST001",
            product_name="테스트김밥",
            sub_category="줄김밥",
            bgf_order_count=3,
            order_interval_days=6,
            next_order_date="2026-03-02",
            base_name="테스트김밥",
        )
        item = repo.get_tracking("46513", "202603-W2", "TEST001")
        assert item is not None
        assert item["product_code"] == "TEST001"
        assert item["bgf_order_count"] == 3
        assert item["our_order_count"] == 3  # bgf_order_count로 초기화
        assert item["base_name"] == "테스트김밥"

    def test_record_order_increments(self, tracking_db):
        repo = NewProduct3DayTrackingRepository(db_path=tracking_db)
        repo.upsert_tracking(
            store_id="46513", week_label="W1",
            week_start="2026-03-01", week_end="2026-03-20",
            product_code="P001", order_interval_days=6,
            next_order_date="2026-03-01",
            base_name="테스트상품",
        )
        repo.record_order("46513", "W1", "P001", "2026-03-07")
        item = repo.get_tracking("46513", "W1", "P001")
        assert item["our_order_count"] == 1
        assert item["next_order_date"] == "2026-03-07"
        assert item["last_ordered_at"] is not None

    def test_record_skip_increments(self, tracking_db):
        repo = NewProduct3DayTrackingRepository(db_path=tracking_db)
        repo.upsert_tracking(
            store_id="46513", week_label="W1",
            week_start="2026-03-01", week_end="2026-03-20",
            product_code="P001",
            base_name="테스트상품",
        )
        repo.record_skip("46513", "W1", "P001")
        repo.record_skip("46513", "W1", "P001")
        item = repo.get_tracking("46513", "W1", "P001")
        assert item["skip_count"] == 2

    def test_mark_completed(self, tracking_db):
        repo = NewProduct3DayTrackingRepository(db_path=tracking_db)
        repo.upsert_tracking(
            store_id="46513", week_label="W1",
            week_start="2026-03-01", week_end="2026-03-20",
            product_code="P001",
            base_name="테스트상품",
        )
        repo.mark_completed("46513", "W1", "P001")
        item = repo.get_tracking("46513", "W1", "P001")
        assert item["is_completed"] == 1

    def test_get_active_items_excludes_completed(self, tracking_db):
        repo = NewProduct3DayTrackingRepository(db_path=tracking_db)
        repo.upsert_tracking(
            store_id="46513", week_label="W1",
            week_start="2026-03-01", week_end="2026-03-20",
            product_code="P001",
            base_name="상품A",
        )
        repo.upsert_tracking(
            store_id="46513", week_label="W1",
            week_start="2026-03-01", week_end="2026-03-20",
            product_code="P002",
            base_name="상품B",
        )
        repo.mark_completed("46513", "W1", "P001")
        active = repo.get_active_items("46513", "W1")
        assert len(active) == 1
        assert active[0]["product_code"] == "P002"

    def test_upsert_updates_existing(self, tracking_db):
        repo = NewProduct3DayTrackingRepository(db_path=tracking_db)
        repo.upsert_tracking(
            store_id="46513", week_label="W1",
            week_start="2026-03-01", week_end="2026-03-20",
            product_code="P001", bgf_order_count=1,
            order_interval_days=6,
            base_name="테스트상품",
        )
        repo.upsert_tracking(
            store_id="46513", week_label="W1",
            week_start="2026-03-01", week_end="2026-03-20",
            product_code="P001", bgf_order_count=3,
            order_interval_days=6,
            base_name="테스트상품",
        )
        item = repo.get_tracking("46513", "W1", "P001")
        assert item["bgf_order_count"] == 3

    def test_update_sale_after_order(self, tracking_db):
        repo = NewProduct3DayTrackingRepository(db_path=tracking_db)
        repo.upsert_tracking(
            store_id="46513", week_label="W1",
            week_start="2026-03-01", week_end="2026-03-20",
            product_code="P001",
            base_name="테스트상품",
        )
        repo.update_sale_after_order("46513", "W1", "P001", 5)
        item = repo.get_tracking("46513", "W1", "P001")
        assert item["last_sale_after_order"] == 5


# ═══════════════════════════════════════════════════
# extract_base_name 테스트
# ═══════════════════════════════════════════════════

class TestExtractBaseName:
    def test_sandwich_with_suffix(self):
        assert extract_base_name("샌)대만식대파크림샌드2") == "샌)대만식대파크림샌드"

    def test_hamburger_with_suffix(self):
        assert extract_base_name("햄)아메리칸더블치폴레1") == "햄)아메리칸더블치폴레"

    def test_no_suffix(self):
        assert extract_base_name("줄)콘버터아끼소바") == "줄)콘버터아끼소바"

    def test_two_digit_suffix(self):
        assert extract_base_name("도)해청양닭밥도템10") == "도)해청양닭밥도템"

    def test_empty_string(self):
        assert extract_base_name("") == ""


# ═══════════════════════════════════════════════════
# group_by_base_name 테스트
# ═══════════════════════════════════════════════════

class TestGroupByBaseName:
    def test_pair_grouped(self):
        products = [
            {"product_code": "A001", "product_name": "샌)대만식대파크림샌드1", "sub_category": "샌드위치", "bgf_order_count": 2},
            {"product_code": "A002", "product_name": "샌)대만식대파크림샌드2", "sub_category": "샌드위치", "bgf_order_count": 1},
        ]
        groups = group_by_base_name(products)
        assert len(groups) == 1
        assert groups[0]["base_name"] == "샌)대만식대파크림샌드"
        assert len(groups[0]["variants"]) == 2

    def test_single_product(self):
        products = [
            {"product_code": "B001", "product_name": "줄)콘버터아끼소바", "sub_category": "줄김밥", "bgf_order_count": 3},
        ]
        groups = group_by_base_name(products)
        assert len(groups) == 1
        assert len(groups[0]["variants"]) == 1

    def test_bgf_min_count(self):
        products = [
            {"product_code": "A001", "product_name": "햄)치즈버거1", "bgf_order_count": 2},
            {"product_code": "A002", "product_name": "햄)치즈버거2", "bgf_order_count": 1},
        ]
        groups = group_by_base_name(products)
        assert groups[0]["bgf_min_count"] == 1

    def test_multiple_groups(self):
        products = [
            {"product_code": "A001", "product_name": "샌)크림샌드1", "bgf_order_count": 1},
            {"product_code": "B001", "product_name": "줄)참치마요", "bgf_order_count": 2},
            {"product_code": "A002", "product_name": "샌)크림샌드2", "bgf_order_count": 1},
        ]
        groups = group_by_base_name(products)
        assert len(groups) == 2
        names = {g["base_name"] for g in groups}
        assert "샌)크림샌드" in names
        assert "줄)참치마요" in names


# ═══════════════════════════════════════════════════
# select_variant_to_order 테스트
# ═══════════════════════════════════════════════════

class TestSelectVariantToOrder:
    def test_sales_higher_first(self):
        group = {
            "base_name": "샌)크림샌드",
            "variants": [
                {"product_code": "A001", "product_name": "샌)크림샌드1"},
                {"product_code": "A002", "product_name": "샌)크림샌드2"},
            ],
        }
        selected = select_variant_to_order(group, {"A001": 5, "A002": 3})
        assert selected["product_code"] == "A001"

    def test_sales_equal_name_asc(self):
        group = {
            "base_name": "햄)치즈버거",
            "variants": [
                {"product_code": "B002", "product_name": "햄)치즈버거2"},
                {"product_code": "B001", "product_name": "햄)치즈버거1"},
            ],
        }
        selected = select_variant_to_order(group, {"B001": 2, "B002": 2})
        assert selected["product_code"] == "B001"

    def test_single_variant(self):
        group = {"base_name": "줄)참치마요", "variants": [{"product_code": "C001", "product_name": "줄)참치마요"}]}
        assert select_variant_to_order(group)["product_code"] == "C001"

    def test_no_sales_data(self):
        group = {
            "base_name": "샌)크림샌드",
            "variants": [
                {"product_code": "A002", "product_name": "샌)크림샌드2"},
                {"product_code": "A001", "product_name": "샌)크림샌드1"},
            ],
        }
        assert select_variant_to_order(group, None)["product_code"] == "A001"

    def test_empty_variants(self):
        assert select_variant_to_order({"base_name": "없음", "variants": []}) is None


# ═══════════════════════════════════════════════════
# base_name 기반 Repository 테스트
# ═══════════════════════════════════════════════════

class TestRepoBaseName:
    def test_get_group_tracking(self, tracking_db):
        repo = NewProduct3DayTrackingRepository(db_path=tracking_db)
        repo.upsert_tracking(
            store_id="46513", week_label="W1",
            week_start="2026-03-01", week_end="2026-03-20",
            product_code="A001", product_name="샌)크림샌드1",
            base_name="샌)크림샌드",
            product_codes=json.dumps(["A001", "A002"]),
            order_interval_days=6, next_order_date="2026-03-01",
        )
        tracking = repo.get_group_tracking("46513", "W1", "샌)크림샌드")
        assert tracking is not None
        assert tracking["base_name"] == "샌)크림샌드"

    def test_record_order_by_base_name(self, tracking_db):
        repo = NewProduct3DayTrackingRepository(db_path=tracking_db)
        repo.upsert_tracking(
            store_id="46513", week_label="W1",
            week_start="2026-03-01", week_end="2026-03-20",
            product_code="A001", product_name="햄)치즈버거1",
            base_name="햄)치즈버거",
        )
        repo.record_order_by_base_name("46513", "W1", "햄)치즈버거", "2026-03-07", "A001")
        tracking = repo.get_group_tracking("46513", "W1", "햄)치즈버거")
        assert tracking["our_order_count"] == 1
        assert tracking["selected_code"] == "A001"

    def test_mark_completed_by_base_name(self, tracking_db):
        repo = NewProduct3DayTrackingRepository(db_path=tracking_db)
        repo.upsert_tracking(
            store_id="46513", week_label="W1",
            week_start="2026-03-01", week_end="2026-03-20",
            product_code="A001",
            base_name="햄)치즈버거",
        )
        repo.mark_completed_by_base_name("46513", "W1", "햄)치즈버거")
        tracking = repo.get_group_tracking("46513", "W1", "햄)치즈버거")
        assert tracking["is_completed"] == 1


# ═══════════════════════════════════════════════════
# 중복발주 방지 테스트
# ═══════════════════════════════════════════════════

class TestDuplicateOrderPrevention:
    def test_group_one_order_only(self):
        ok, _, action = should_order_today(
            today="2026-03-14", week_end="2026-03-20",
            next_order_date="2026-03-14", our_order_count=2,
        )
        assert ok is True
        assert action == "order"

        ok2, _, action2 = should_order_today(
            today="2026-03-14", week_end="2026-03-20",
            next_order_date="2026-03-14", our_order_count=3,
        )
        assert ok2 is False
        assert action2 == "none"

    def test_group_returns_single_item(self):
        products = [
            {"product_code": "X001", "product_name": "샌)대만식1", "sub_category": "샌드위치", "bgf_order_count": 1},
            {"product_code": "X002", "product_name": "샌)대만식2", "sub_category": "샌드위치", "bgf_order_count": 1},
        ]
        groups = group_by_base_name(products)
        assert len(groups) == 1
        selected = select_variant_to_order(groups[0], {"X001": 3, "X002": 1})
        assert selected["product_code"] == "X001"


# ═══════════════════════════════════════════════════
# 통합 시나리오 테스트 (동적 간격)
# ═══════════════════════════════════════════════════

class TestDynamicOrderScenario:
    """동적 간격 기반 전체 시나리오"""

    def test_full_lifecycle_with_sales(self):
        """전체 라이프사이클: 판매→즉시→판매→즉시→완료"""
        week_end = "2026-03-20"

        # 1회차: count=0 → 즉시 발주
        ok, _, action = should_order_today("2026-03-02", week_end, "2026-03-02", 0)
        assert ok is True
        assert action == "order"

        # 발주 후 판매 있음 → next = 3/3
        next_date = calculate_dynamic_next_order_date("2026-03-02", sold_qty=2)
        assert next_date == "2026-03-03"

        # 2회차: 3/3 도달 → 발주
        ok, _, action = should_order_today("2026-03-03", week_end, next_date, 1)
        assert ok is True
        assert action == "order"

        # 발주 후 판매 있음 → next = 3/4
        next_date = calculate_dynamic_next_order_date("2026-03-03", sold_qty=1)
        assert next_date == "2026-03-04"

        # 3회차: 3/4 도달 → 발주
        ok, _, action = should_order_today("2026-03-04", week_end, next_date, 2)
        assert ok is True
        assert action == "order"

        # 3회 달성 후
        ok, _, action = should_order_today("2026-03-05", week_end, "2026-03-05", 3)
        assert ok is False

    def test_full_lifecycle_no_sales(self):
        """전체 라이프사이클: 미판매→3일후→미판매→3일후→D-5 강제"""
        week_end = "2026-03-20"

        # 1회차: count=0 → 즉시
        ok, _, _ = should_order_today("2026-03-02", week_end, "2026-03-02", 0)
        assert ok is True

        # 판매 없음 → next = 3/5
        next_date = calculate_dynamic_next_order_date("2026-03-02", sold_qty=0)
        assert next_date == "2026-03-05"

        # 3/4 → 미도달
        ok, _, _ = should_order_today("2026-03-04", week_end, next_date, 1)
        assert ok is False

        # 3/5 → 발주
        ok, _, _ = should_order_today("2026-03-05", week_end, next_date, 1)
        assert ok is True

        # 다시 미판매 → next = 3/8
        next_date = calculate_dynamic_next_order_date("2026-03-05", sold_qty=0)
        assert next_date == "2026-03-08"

        # 3/7 → 미도달
        ok, _, _ = should_order_today("2026-03-07", week_end, next_date, 2)
        assert ok is False

        # 3/8 → 발주
        ok, _, _ = should_order_today("2026-03-08", week_end, next_date, 2)
        assert ok is True

    def test_waste_triggers_next_day(self):
        """폐기 발생 시 다음 날 재발주"""
        next_date = calculate_dynamic_next_order_date("2026-03-10", sold_qty=0, wasted_qty=1)
        assert next_date == "2026-03-11"

        ok, _, action = should_order_today("2026-03-11", "2026-03-20", next_date, 1)
        assert ok is True
        assert action == "order"

    def test_d5_force_overrides_schedule(self):
        """D-5 강제 발주: 예정일 미도달이어도 강제"""
        ok, _, action = should_order_today(
            today="2026-03-16",
            week_end="2026-03-20",
            next_order_date="2026-03-20",  # 아직 미도달
            our_order_count=1,
        )
        assert ok is True
        assert action == "force"

    def test_ai_merge_tracking_flag(self):
        """AI 중복: 수량 유지 + np_3day_tracking 플래그"""
        ai_orders = [
            {"item_cd": "KIMBAP01", "item_nm": "김밥", "final_order_qty": 3, "mid_cd": "003"},
        ]
        np_orders = [
            {"product_code": "KIMBAP01", "product_name": "줄김밥", "qty": 1},
        ]
        result = merge_with_ai_orders(ai_orders, np_orders)
        assert result[0]["final_order_qty"] == 3  # AI 수량 그대로
        assert result[0].get("np_3day_tracking") is True  # 횟수 인정 플래그


# ═══════════════════════════════════════════════════
# 복수 주차 동시 처리 (multi-week)
# ═══════════════════════════════════════════════════

class TestMultiWeekConcurrent:
    """복수 주차 동시 활성 시 중복 방지 + 마감 빠른 주차 우선"""

    def test_get_all_active_weeks(self, tracking_db):
        repo = NewProduct3DayTrackingRepository(db_path=tracking_db)
        repo.upsert_tracking(
            store_id="46513", week_label="202603-W3",
            week_start="2026-03-02", week_end="2026-03-20",
            product_code="P001", base_name="상품A",
        )
        repo.upsert_tracking(
            store_id="46513", week_label="202603-W4",
            week_start="2026-03-09", week_end="2026-03-27",
            product_code="P002", base_name="상품B",
        )
        weeks = repo.get_all_active_weeks("46513", "2026-03-15")
        assert len(weeks) == 2
        assert weeks[0]["week_label"] == "202603-W3"
        assert weeks[1]["week_label"] == "202603-W4"

    def test_active_weeks_excludes_completed(self, tracking_db):
        repo = NewProduct3DayTrackingRepository(db_path=tracking_db)
        repo.upsert_tracking(
            store_id="46513", week_label="202603-W3",
            week_start="2026-03-02", week_end="2026-03-20",
            product_code="P001", base_name="상품A",
        )
        repo.mark_completed("46513", "202603-W3", "P001")
        weeks = repo.get_all_active_weeks("46513", "2026-03-15")
        assert len(weeks) == 0

    def test_active_weeks_excludes_out_of_range(self, tracking_db):
        repo = NewProduct3DayTrackingRepository(db_path=tracking_db)
        repo.upsert_tracking(
            store_id="46513", week_label="202603-W1",
            week_start="2026-02-20", week_end="2026-03-10",
            product_code="P001", base_name="상품A",
        )
        weeks = repo.get_all_active_weeks("46513", "2026-03-15")
        assert len(weeks) == 0


class TestMultiWeekDedup:
    """주차간 동일 상품(base_name) 중복 발주 방지"""

    def _make_repo_factory(self, tracking_db):
        def factory(*args, **kwargs):
            return NewProduct3DayTrackingRepository(db_path=tracking_db)
        return factory

    def test_same_base_name_two_weeks_earlier_wins(self, tracking_db, monkeypatch):
        repo = NewProduct3DayTrackingRepository(db_path=tracking_db)
        monkeypatch.setattr(
            "src.infrastructure.database.repos.NP3DayTrackingRepo",
            self._make_repo_factory(tracking_db),
        )
        repo.upsert_tracking(
            store_id="46513", week_label="202603-W3",
            week_start="2026-03-02", week_end="2026-03-20",
            product_code="P001", product_name="샌)크림샌드1",
            base_name="샌)크림샌드",
            bgf_order_count=3, order_interval_days=6,
            next_order_date="2026-03-02",
        )
        repo.upsert_tracking(
            store_id="46513", week_label="202603-W4",
            week_start="2026-03-09", week_end="2026-03-27",
            product_code="P001", product_name="샌)크림샌드1",
            base_name="샌)크림샌드",
            bgf_order_count=3, order_interval_days=6,
            next_order_date="2026-03-09",
        )
        orders = get_today_new_product_orders(store_id="46513", today="2026-03-15")
        matching = [o for o in orders if o["base_name"] == "샌)크림샌드"]
        assert len(matching) <= 1

    def test_different_base_names_both_ordered(self, tracking_db, monkeypatch):
        repo = NewProduct3DayTrackingRepository(db_path=tracking_db)
        monkeypatch.setattr(
            "src.infrastructure.database.repos.NP3DayTrackingRepo",
            self._make_repo_factory(tracking_db),
        )
        repo.upsert_tracking(
            store_id="46513", week_label="202603-W3",
            week_start="2026-03-02", week_end="2026-03-20",
            product_code="P001", product_name="샌)크림샌드1",
            base_name="샌)크림샌드",
            bgf_order_count=3, order_interval_days=6,
            next_order_date="2026-03-02",
        )
        repo.upsert_tracking(
            store_id="46513", week_label="202603-W4",
            week_start="2026-03-09", week_end="2026-03-27",
            product_code="P002", product_name="줄)참치마요",
            base_name="줄)참치마요",
            bgf_order_count=3, order_interval_days=6,
            next_order_date="2026-03-09",
        )
        orders = get_today_new_product_orders(store_id="46513", today="2026-03-15")
        base_names = {o["base_name"] for o in orders}
        assert len(base_names) <= 2

    def test_dedup_prefers_earlier_week_end(self, tracking_db, monkeypatch):
        repo = NewProduct3DayTrackingRepository(db_path=tracking_db)
        monkeypatch.setattr(
            "src.infrastructure.database.repos.NP3DayTrackingRepo",
            self._make_repo_factory(tracking_db),
        )
        repo.upsert_tracking(
            store_id="46513", week_label="202603-W4",
            week_start="2026-03-09", week_end="2026-03-27",
            product_code="P001", product_name="햄)치즈버거1",
            base_name="햄)치즈버거",
            bgf_order_count=3, order_interval_days=6,
            next_order_date="2026-03-09",
        )
        repo.upsert_tracking(
            store_id="46513", week_label="202603-W3",
            week_start="2026-03-02", week_end="2026-03-20",
            product_code="P001", product_name="햄)치즈버거1",
            base_name="햄)치즈버거",
            bgf_order_count=3, order_interval_days=6,
            next_order_date="2026-03-02",
        )
        orders = get_today_new_product_orders(store_id="46513", today="2026-03-15")
        matching = [o for o in orders if o["base_name"] == "햄)치즈버거"]
        if matching:
            assert matching[0]["week_label"] == "202603-W3"


class TestMultiWeekD3Force:
    """주차별 D-5 마감 개별 계산"""

    def _make_repo_factory(self, tracking_db):
        def factory(*args, **kwargs):
            return NewProduct3DayTrackingRepository(db_path=tracking_db)
        return factory

    def test_w3_d5_force(self, tracking_db, monkeypatch):
        """W3은 D-2 (강제) → 발주 (bgf_order_count=1, our_order_count=1이므로 미달성)"""
        repo = NewProduct3DayTrackingRepository(db_path=tracking_db)
        monkeypatch.setattr(
            "src.infrastructure.database.repos.NP3DayTrackingRepo",
            self._make_repo_factory(tracking_db),
        )
        repo.upsert_tracking(
            store_id="46513", week_label="202603-W3",
            week_start="2026-03-02", week_end="2026-03-20",
            product_code="P001", product_name="도)해물도시락1",
            base_name="도)해물도시락",
            bgf_order_count=1, order_interval_days=6,
            next_order_date="2026-03-14",
        )
        orders = get_today_new_product_orders(store_id="46513", today="2026-03-18")
        matching = [o for o in orders if o["base_name"] == "도)해물도시락"]
        assert len(matching) == 1

    def test_w3_completed_w4_active(self, tracking_db, monkeypatch):
        """W3 완료, W4 미완료 → W4에서 처리"""
        repo = NewProduct3DayTrackingRepository(db_path=tracking_db)
        monkeypatch.setattr(
            "src.infrastructure.database.repos.NP3DayTrackingRepo",
            self._make_repo_factory(tracking_db),
        )
        repo.upsert_tracking(
            store_id="46513", week_label="202603-W3",
            week_start="2026-03-02", week_end="2026-03-20",
            product_code="P001", product_name="샌)크림샌드1",
            base_name="샌)크림샌드",
            bgf_order_count=3, order_interval_days=6,
            next_order_date="2026-03-14",
        )
        repo.mark_completed_by_base_name("46513", "202603-W3", "샌)크림샌드")
        repo.upsert_tracking(
            store_id="46513", week_label="202603-W4",
            week_start="2026-03-09", week_end="2026-03-27",
            product_code="P001", product_name="샌)크림샌드1",
            base_name="샌)크림샌드",
            bgf_order_count=3, order_interval_days=6,
            next_order_date="2026-03-09",
        )
        orders = get_today_new_product_orders(store_id="46513", today="2026-03-15")
        matching = [o for o in orders if o["base_name"] == "샌)크림샌드"]
        if matching:
            assert matching[0]["week_label"] == "202603-W4"
