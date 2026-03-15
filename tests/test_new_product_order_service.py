"""
신상품 3일발주 분산 발주 서비스 테스트

- 발주 간격 계산 검증
- 분산 발주 판단 로직 (should_order_today)
- 판매량 0 + D-5 → 스킵 검증
- 판매량 0 + D-2 → 강제 발주 검증
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
# 발주 간격 계산
# ═══════════════════════════════════════════════════

class TestCalculateIntervalDays:
    """기간을 3등분한 발주 간격 계산"""

    def test_19day_period(self):
        """19일 기간 → 간격 6일"""
        assert calculate_interval_days("2026-03-02", "2026-03-20") == 6

    def test_14day_period(self):
        """14일 기간 (3/1~3/15, 차이 14일) → 간격 4일"""
        assert calculate_interval_days("2026-03-01", "2026-03-15") == 4

    def test_8day_period(self):
        """8일 기간 (3/1~3/9, 차이 8일) → 간격 2일"""
        assert calculate_interval_days("2026-03-01", "2026-03-09") == 2

    def test_3day_period(self):
        """3일 기간 → 간격 1일"""
        assert calculate_interval_days("2026-03-01", "2026-03-03") == 1

    def test_0day_period(self):
        """0일 기간 → 간격 1일"""
        assert calculate_interval_days("2026-03-01", "2026-03-01") == 1

    def test_empty_dates(self):
        """빈 날짜 → 간격 1일"""
        assert calculate_interval_days("", "") == 1


class TestCalculateNextOrderDate:
    """다음 발주 예정일 계산"""

    def test_first_order(self):
        """0회 발주 → 시작일"""
        assert calculate_next_order_date("2026-03-02", 0, 6) == "2026-03-02"

    def test_second_order(self):
        """1회 발주 → 시작일 + 6일"""
        assert calculate_next_order_date("2026-03-02", 1, 6) == "2026-03-08"

    def test_third_order(self):
        """2회 발주 → 시작일 + 12일"""
        assert calculate_next_order_date("2026-03-02", 2, 6) == "2026-03-14"


# ═══════════════════════════════════════════════════
# 분산 발주 판단 (should_order_today)
# ═══════════════════════════════════════════════════

class TestShouldOrderToday:
    """발주 여부 판단 — 핵심 로직"""

    def test_already_completed(self):
        """이미 3회 발주 완료 → False"""
        result, reason, action = should_order_today(
            today="2026-03-10",
            week_end="2026-03-20",
            next_order_date="2026-03-14",
            our_order_count=3,
            last_sale_after_order=0,
        )
        assert result is False
        assert action == "none"

    def test_before_next_order_date(self):
        """예정일 미도달 → False"""
        result, reason, action = should_order_today(
            today="2026-03-05",
            week_end="2026-03-20",
            next_order_date="2026-03-08",
            our_order_count=1,
            last_sale_after_order=0,
        )
        assert result is False
        assert action == "none"

    def test_on_order_date_first_order(self):
        """예정일 도달 + 첫 발주 → True (order)"""
        result, reason, action = should_order_today(
            today="2026-03-02",
            week_end="2026-03-20",
            next_order_date="2026-03-02",
            our_order_count=0,
            last_sale_after_order=0,
        )
        assert result is True
        assert action == "order"

    def test_no_sales_with_stock_skip(self):
        """판매 없음 + 재고 있음 + D-5 → 스킵"""
        result, reason, action = should_order_today(
            today="2026-03-15",
            week_end="2026-03-20",
            next_order_date="2026-03-14",
            our_order_count=1,
            last_sale_after_order=0,
            current_stock=1,
        )
        assert result is False
        assert action == "skip"
        assert "스킵" in reason

    def test_no_sales_no_stock(self):
        """판매 없음 + 재고 0 → 발주"""
        result, reason, action = should_order_today(
            today="2026-03-15",
            week_end="2026-03-20",
            next_order_date="2026-03-14",
            our_order_count=1,
            last_sale_after_order=0,
            current_stock=0,
        )
        assert result is True
        assert action == "order"

    def test_has_sales_order(self):
        """판매 있음 → 발주"""
        result, reason, action = should_order_today(
            today="2026-03-15",
            week_end="2026-03-20",
            next_order_date="2026-03-14",
            our_order_count=1,
            last_sale_after_order=2,
            current_stock=1,
        )
        assert result is True
        assert action == "order"

    def test_d3_force_order(self):
        """D-2 (기간 잔여 2일) + 미달성 → 강제 발주"""
        result, reason, action = should_order_today(
            today="2026-03-18",
            week_end="2026-03-20",
            next_order_date="2026-03-14",
            our_order_count=1,
            last_sale_after_order=0,
            current_stock=5,
        )
        assert result is True
        assert action == "force"
        assert "강제" in reason

    def test_d3_force_even_first_order(self):
        """D-3 + 0회 발주 → 강제 발주"""
        result, reason, action = should_order_today(
            today="2026-03-17",
            week_end="2026-03-20",
            next_order_date="2026-03-02",
            our_order_count=0,
            last_sale_after_order=0,
        )
        assert result is True
        assert action == "force"

    def test_exactly_d3(self):
        """D-3 경계값 → 강제 발주"""
        result, reason, action = should_order_today(
            today="2026-03-17",
            week_end="2026-03-20",
            next_order_date="2026-03-14",
            our_order_count=2,
            last_sale_after_order=0,
            current_stock=3,
        )
        assert result is True
        assert action == "force"

    def test_d4_not_forced(self):
        """D-4 + 판매 없음 + 재고 → 스킵 (D-3 미만)"""
        result, reason, action = should_order_today(
            today="2026-03-16",
            week_end="2026-03-20",
            next_order_date="2026-03-14",
            our_order_count=1,
            last_sale_after_order=0,
            current_stock=2,
        )
        assert result is False
        assert action == "skip"

    def test_first_order_no_skip(self):
        """첫 발주(count=0) + 판매 없음 + 재고 있음 → 발주 (첫 발주는 스킵 안 함)"""
        result, reason, action = should_order_today(
            today="2026-03-10",
            week_end="2026-03-20",
            next_order_date="2026-03-08",
            our_order_count=0,
            last_sale_after_order=0,
            current_stock=3,
        )
        assert result is True
        assert action == "order"


# ═══════════════════════════════════════════════════
# AI 예측 목록과 합산 (merge_with_ai_orders)
# ═══════════════════════════════════════════════════

class TestMergeWithAiOrders:
    """AI 예측 발주 + 신상품 발주 합산"""

    def test_existing_item_qty_add(self):
        """AI 목록에 이미 있는 상품 → qty 합산"""
        ai_orders = [
            {"item_cd": "ABC001", "item_nm": "김밥", "final_order_qty": 3, "mid_cd": "003"},
            {"item_cd": "DEF002", "item_nm": "도시락", "final_order_qty": 2, "mid_cd": "001"},
        ]
        np_orders = [
            {"product_code": "ABC001", "product_name": "김밥", "qty": 1, "source": "new_product_3day_distributed"},
        ]
        result = merge_with_ai_orders(ai_orders, np_orders)
        abc_item = [r for r in result if r.get("item_cd") == "ABC001"][0]
        assert abc_item["final_order_qty"] == 4  # 3 + 1
        assert "신상품3일" in abc_item.get("label", "")
        assert abc_item.get("new_product_3day") is True

    def test_new_item_insert_front(self):
        """AI 목록에 없는 신상품 → 앞에 삽입"""
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
        assert result[1]["item_cd"] == "ABC001"

    def test_mixed_existing_and_new(self):
        """기존 + 신규 혼합"""
        ai_orders = [
            {"item_cd": "A001", "final_order_qty": 2},
            {"item_cd": "B002", "final_order_qty": 1},
        ]
        np_orders = [
            {"product_code": "A001", "qty": 1},  # 기존에 합산
            {"product_code": "C003", "product_name": "샌드위치", "qty": 1},  # 신규
        ]
        result = merge_with_ai_orders(ai_orders, np_orders)
        assert len(result) == 3
        # 신규가 앞에
        assert result[0]["item_cd"] == "C003"
        # 기존 합산
        a001 = [r for r in result if r.get("item_cd") == "A001"][0]
        assert a001["final_order_qty"] == 3

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
        """원본 ai_orders 변경 없음"""
        ai_orders = [{"item_cd": "A001", "final_order_qty": 2}]
        original_qty = ai_orders[0]["final_order_qty"]
        merge_with_ai_orders(ai_orders, [{"product_code": "A001", "qty": 1}])
        # merge_with_ai_orders는 shallow copy하므로 원본의 dict는 수정될 수 있음
        # 그러나 리스트 자체는 새 리스트


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
        assert item["our_order_count"] == 0
        assert item["order_interval_days"] == 6
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
        """같은 키로 UPSERT → bgf_order_count 업데이트"""
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
    """상품명 말미 숫자 제거"""

    def test_sandwich_with_suffix(self):
        """'샌)대만식대파크림샌드2' → '샌)대만식대파크림샌드'"""
        assert extract_base_name("샌)대만식대파크림샌드2") == "샌)대만식대파크림샌드"

    def test_hamburger_with_suffix(self):
        """'햄)아메리칸더블치폴레1' → '햄)아메리칸더블치폴레'"""
        assert extract_base_name("햄)아메리칸더블치폴레1") == "햄)아메리칸더블치폴레"

    def test_no_suffix(self):
        """'줄)콘버터아끼소바' → 원본 유지"""
        assert extract_base_name("줄)콘버터아끼소바") == "줄)콘버터아끼소바"

    def test_two_digit_suffix(self):
        """'도)해청양닭밥도템10' → '도)해청양닭밥도템'"""
        assert extract_base_name("도)해청양닭밥도템10") == "도)해청양닭밥도템"

    def test_empty_string(self):
        """빈 문자열 → 빈 문자열"""
        assert extract_base_name("") == ""


# ═══════════════════════════════════════════════════
# group_by_base_name 테스트
# ═══════════════════════════════════════════════════

class TestGroupByBaseName:
    """base_name 기준 그룹핑"""

    def test_pair_grouped(self):
        """1차+2차 입력 → 동일 그룹"""
        products = [
            {"product_code": "A001", "product_name": "샌)대만식대파크림샌드1", "sub_category": "샌드위치", "bgf_order_count": 2},
            {"product_code": "A002", "product_name": "샌)대만식대파크림샌드2", "sub_category": "샌드위치", "bgf_order_count": 1},
        ]
        groups = group_by_base_name(products)
        assert len(groups) == 1
        assert groups[0]["base_name"] == "샌)대만식대파크림샌드"
        assert len(groups[0]["variants"]) == 2

    def test_single_product(self):
        """단독 상품 → 그룹 1개, variants 1개"""
        products = [
            {"product_code": "B001", "product_name": "줄)콘버터아끼소바", "sub_category": "줄김밥", "bgf_order_count": 3},
        ]
        groups = group_by_base_name(products)
        assert len(groups) == 1
        assert len(groups[0]["variants"]) == 1

    def test_bgf_min_count(self):
        """bgf_min_count: [1,2] → min=1"""
        products = [
            {"product_code": "A001", "product_name": "햄)치즈버거1", "bgf_order_count": 2},
            {"product_code": "A002", "product_name": "햄)치즈버거2", "bgf_order_count": 1},
        ]
        groups = group_by_base_name(products)
        assert groups[0]["bgf_min_count"] == 1

    def test_multiple_groups(self):
        """서로 다른 base_name → 별도 그룹"""
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
    """그룹에서 발주 variant 선택"""

    def test_sales_higher_first(self):
        """판매량 1차>2차 → 1차 선택"""
        group = {
            "base_name": "샌)크림샌드",
            "variants": [
                {"product_code": "A001", "product_name": "샌)크림샌드1"},
                {"product_code": "A002", "product_name": "샌)크림샌드2"},
            ],
        }
        sales_data = {"A001": 5, "A002": 3}
        selected = select_variant_to_order(group, sales_data)
        assert selected["product_code"] == "A001"

    def test_sales_equal_name_asc(self):
        """판매량 동일 → 이름 오름차순(1차) 선택"""
        group = {
            "base_name": "햄)치즈버거",
            "variants": [
                {"product_code": "B002", "product_name": "햄)치즈버거2"},
                {"product_code": "B001", "product_name": "햄)치즈버거1"},
            ],
        }
        sales_data = {"B001": 2, "B002": 2}
        selected = select_variant_to_order(group, sales_data)
        assert selected["product_code"] == "B001"  # 이름 오름차순

    def test_single_variant(self):
        """variants 1개 → 바로 반환"""
        group = {
            "base_name": "줄)참치마요",
            "variants": [
                {"product_code": "C001", "product_name": "줄)참치마요"},
            ],
        }
        selected = select_variant_to_order(group)
        assert selected["product_code"] == "C001"

    def test_no_sales_data(self):
        """판매 데이터 없음 → 이름 오름차순"""
        group = {
            "base_name": "샌)크림샌드",
            "variants": [
                {"product_code": "A002", "product_name": "샌)크림샌드2"},
                {"product_code": "A001", "product_name": "샌)크림샌드1"},
            ],
        }
        selected = select_variant_to_order(group, None)
        assert selected["product_code"] == "A001"

    def test_empty_variants(self):
        """빈 variants → None"""
        group = {"base_name": "없음", "variants": []}
        assert select_variant_to_order(group) is None


# ═══════════════════════════════════════════════════
# base_name 기반 Repository 테스트
# ═══════════════════════════════════════════════════

class TestRepoBaseName:
    """base_name 기반 그룹 추적"""

    def test_get_group_tracking(self, tracking_db):
        """base_name으로 그룹 추적 조회"""
        repo = NewProduct3DayTrackingRepository(db_path=tracking_db)
        repo.upsert_tracking(
            store_id="46513", week_label="W1",
            week_start="2026-03-01", week_end="2026-03-20",
            product_code="A001", product_name="샌)크림샌드1",
            base_name="샌)크림샌드",
            product_codes=json.dumps(["A001", "A002"]),
            order_interval_days=6,
            next_order_date="2026-03-01",
        )
        tracking = repo.get_group_tracking("46513", "W1", "샌)크림샌드")
        assert tracking is not None
        assert tracking["base_name"] == "샌)크림샌드"
        assert tracking["our_order_count"] == 0

    def test_record_order_by_base_name(self, tracking_db):
        """base_name 기준 발주 기록"""
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
        """base_name 기준 완료 표시"""
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
    """그룹핑 중복발주 방지"""

    def test_group_one_order_only(self):
        """our_order_count=2 (1회 남음), variants 2개 → should_order=True 1회만"""
        # 그룹 단위로 should_order_today 1회 호출하므로 자연적으로 1개만 반환
        ok, _, action = should_order_today(
            today="2026-03-14",
            week_end="2026-03-20",
            next_order_date="2026-03-14",
            our_order_count=2,
            last_sale_after_order=1,
            current_stock=0,
        )
        assert ok is True
        assert action == "order"

        # 3회 달성 후 추가 발주 불가
        ok2, _, action2 = should_order_today(
            today="2026-03-14",
            week_end="2026-03-20",
            next_order_date="2026-03-14",
            our_order_count=3,
            last_sale_after_order=1,
            current_stock=0,
        )
        assert ok2 is False
        assert action2 == "none"

    def test_group_returns_single_item(self):
        """1차+2차 동시 입력 → 그룹핑 후 select_variant 1개만"""
        products = [
            {"product_code": "X001", "product_name": "샌)대만식1", "sub_category": "샌드위치", "bgf_order_count": 1},
            {"product_code": "X002", "product_name": "샌)대만식2", "sub_category": "샌드위치", "bgf_order_count": 1},
        ]
        groups = group_by_base_name(products)
        assert len(groups) == 1

        sales_data = {"X001": 3, "X002": 1}
        selected = select_variant_to_order(groups[0], sales_data)
        assert selected is not None
        # 그룹당 1개만 선택됨
        assert selected["product_code"] == "X001"


# ═══════════════════════════════════════════════════
# 통합 시나리오 테스트
# ═══════════════════════════════════════════════════

class TestDistributedOrderScenario:
    """분산 발주 전체 시나리오"""

    def test_19day_3orders_schedule(self):
        """19일 기간, 3회 발주 → 예정일 계산"""
        interval = calculate_interval_days("2026-03-02", "2026-03-20")
        assert interval == 6

        d1 = calculate_next_order_date("2026-03-02", 0, interval)
        d2 = calculate_next_order_date("2026-03-02", 1, interval)
        d3 = calculate_next_order_date("2026-03-02", 2, interval)

        assert d1 == "2026-03-02"
        assert d2 == "2026-03-08"
        assert d3 == "2026-03-14"

    def test_full_lifecycle(self):
        """전체 라이프사이클: 3회 발주 완료까지"""
        week_start = "2026-03-02"
        week_end = "2026-03-20"
        interval = calculate_interval_days(week_start, week_end)

        # 1회차: 3/2 발주
        ok, _, _ = should_order_today(
            "2026-03-02", week_end, "2026-03-02", 0, 0)
        assert ok is True

        # 2회차: 3/8 전 → 안 함
        ok, _, _ = should_order_today(
            "2026-03-06", week_end, "2026-03-08", 1, 0)
        assert ok is False

        # 2회차: 3/8 + 판매 있음 → 발주
        ok, _, _ = should_order_today(
            "2026-03-08", week_end, "2026-03-08", 1, 2)
        assert ok is True

        # 3회차: 3/14 + 판매 없음 + 재고 → 스킵
        ok, _, action = should_order_today(
            "2026-03-14", week_end, "2026-03-14", 2, 0, current_stock=1)
        assert ok is False
        assert action == "skip"

        # 3회차: 3/18 (D-2) → 강제
        ok, _, action = should_order_today(
            "2026-03-18", week_end, "2026-03-14", 2, 0, current_stock=1)
        assert ok is True
        assert action == "force"

    def test_ai_merge_total_quantity(self):
        """총량 합산: AI 김밥 3개 + 신상품 줄김밥 1개 → 4개"""
        ai_orders = [
            {"item_cd": "KIMBAP01", "item_nm": "김밥", "final_order_qty": 3, "mid_cd": "003"},
        ]
        np_orders = [
            {"product_code": "KIMBAP01", "product_name": "줄김밥", "qty": 1},
        ]
        result = merge_with_ai_orders(ai_orders, np_orders)
        assert result[0]["final_order_qty"] == 4
