"""
신상품 도입 현황 모듈 테스트

- 점수 계산 로직 (score_calculator)
- 비인기상품 교체 전략 (replacement_strategy)
- 3일발주 스케줄러 (convenience_order_scheduler)
- Repository CRUD
"""

import sqlite3
import pytest
from datetime import datetime

from src.domain.new_product.score_calculator import (
    rate_to_score,
    calculate_total_score,
    score_to_subsidy,
    calculate_needed_items,
    estimate_score_after_orders,
)
from src.domain.new_product.replacement_strategy import (
    select_items_to_suspend,
    prioritize_missing_items,
    group_new_items_by_category,
)
from src.domain.new_product.convenience_order_scheduler import (
    plan_3day_orders,
    should_order_today,
    get_remaining_orders_needed,
)
from src.infrastructure.database.repos.new_product_repo import NewProductStatusRepository


# ═══════════════════════════════════════════════════
# score_calculator 테스트
# ═══════════════════════════════════════════════════

class TestRateToScore:
    """도입률/달성률 → 점수 환산"""

    def test_doip_0_percent(self):
        assert rate_to_score(0, "doip") == 0

    def test_doip_50_percent(self):
        assert rate_to_score(50, "doip") == 40

    def test_doip_70_percent(self):
        """70% → 56점 (80~89.9 구간은 64점이 아닌, 70~79.9 구간 = 56점)"""
        assert rate_to_score(70, "doip") == 56

    def test_doip_95_percent(self):
        """95%+ → 80점 (최고 구간)"""
        assert rate_to_score(95, "doip") == 80

    def test_doip_100_percent(self):
        assert rate_to_score(100, "doip") == 80

    def test_ds_0_percent(self):
        assert rate_to_score(0, "ds") == 0

    def test_ds_70_percent(self):
        """70% → 16점"""
        assert rate_to_score(70, "ds") == 16

    def test_ds_90_percent(self):
        """90%+ → 20점 (최고 구간)"""
        assert rate_to_score(90, "ds") == 20


class TestCalculateTotalScore:
    """종합 점수 계산"""

    def test_max_score(self):
        """최고 점수: 도입률 95% + 달성률 90% = 80 + 20 = 100점"""
        doip, ds, total = calculate_total_score(95, 90)
        assert doip == 80
        assert ds == 20
        assert total == 100

    def test_current_situation(self):
        """현재 상황: 도입률 70.1% + 달성률 53.3%"""
        doip, ds, total = calculate_total_score(70.1, 53.3)
        assert doip == 56
        assert ds == 10
        assert total == 66

    def test_target_score(self):
        """목표: 도입률 95% + 달성률 70% = 80 + 16 = 96점"""
        doip, ds, total = calculate_total_score(95, 70)
        assert total == 96


class TestScoreToSubsidy:
    """점수 → 지원금 구간"""

    def test_95_to_100(self):
        assert score_to_subsidy(95) == 160000
        assert score_to_subsidy(100) == 160000

    def test_88_to_94(self):
        assert score_to_subsidy(88) == 150000
        assert score_to_subsidy(94) == 150000

    def test_84_to_87(self):
        assert score_to_subsidy(84) == 120000

    def test_72_to_83(self):
        assert score_to_subsidy(76) == 110000

    def test_56_to_71(self):
        assert score_to_subsidy(66) == 80000


class TestCalculateNeededItems:
    """목표 달성에 필요한 추가 상품 수"""

    def test_already_achieved(self):
        """이미 달성한 경우"""
        assert calculate_needed_items(96.0, 95.0, 100, 96) == 0

    def test_need_items(self):
        """70% → 95% 달성 필요"""
        needed = calculate_needed_items(70.0, 95.0, 100, 70)
        assert needed == 25  # 95 - 70 = 25

    def test_rounding_up(self):
        """올림 처리"""
        needed = calculate_needed_items(70.0, 95.0, 77, 54)
        # ceil(77 * 95 / 100) = ceil(73.15) = 74, 74 - 54 = 20
        assert needed == 20


class TestEstimateScoreAfterOrders:
    """발주 후 예상 점수"""

    def test_simulation(self):
        result = estimate_score_after_orders(
            current_doip_cnt=70,
            current_ds_cnt=8,
            total_doip_items=100,
            total_ds_items=15,
            new_doip_orders=25,
            new_ds_orders=3,
        )
        assert result["doip_rate"] == 95.0
        assert result["doip_score"] == 80
        # (8+3)/15 = 73.3%
        assert result["ds_rate"] == 73.3
        assert result["ds_score"] == 16
        assert result["total_score"] == 96
        assert result["subsidy"] == 160000


# ═══════════════════════════════════════════════════
# replacement_strategy 테스트
# ═══════════════════════════════════════════════════

class TestSelectItemsToSuspend:
    """비인기상품 정지 대상 선정"""

    def test_empty_list(self):
        assert select_items_to_suspend([], 3) == []

    def test_zero_count(self):
        items = [{"item_cd": "A", "daily_avg": 0.1}]
        assert select_items_to_suspend(items, 0) == []

    def test_select_lowest_sales(self):
        items = [
            {"item_cd": "A", "daily_avg": 0.5},
            {"item_cd": "B", "daily_avg": 0.1},
            {"item_cd": "C", "daily_avg": 0.2},
        ]
        result = select_items_to_suspend(items, 2)
        assert len(result) == 2
        assert result[0]["item_cd"] == "B"  # 최저
        assert result[1]["item_cd"] == "C"  # 두번째

    def test_exclude_above_threshold(self):
        """일평균 0.3 이상은 정지 대상에서 제외"""
        items = [
            {"item_cd": "A", "daily_avg": 0.5},
            {"item_cd": "B", "daily_avg": 0.1},
        ]
        result = select_items_to_suspend(items, 2)
        assert len(result) == 1  # B만 (A는 0.3 이상이라 제외)
        assert result[0]["item_cd"] == "B"


class TestPrioritizeMissingItems:
    """미도입 상품 우선순위"""

    def test_orderable_first(self):
        items = [
            {"item_cd": "A", "ord_pss_nm": "발주불가", "small_nm": "과자"},
            {"item_cd": "B", "ord_pss_nm": "발주가능", "small_nm": "음료"},
        ]
        result = prioritize_missing_items(items, 10)
        assert result[0]["item_cd"] == "B"  # 발주가능이 먼저

    def test_category_diversity(self):
        """소분류 다양성 고려"""
        items = [
            {"item_cd": "A1", "ord_pss_nm": "발주가능", "small_nm": "과자"},
            {"item_cd": "A2", "ord_pss_nm": "발주가능", "small_nm": "과자"},
            {"item_cd": "B1", "ord_pss_nm": "발주가능", "small_nm": "음료"},
        ]
        result = prioritize_missing_items(items, 10)
        # 과자와 음료가 번갈아 나와야 함
        small_names = [r["small_nm"] for r in result]
        assert small_names[0] != small_names[1] or len(set(small_names)) == 1


class TestGroupNewItemsByCategory:
    """소분류별 그룹화"""

    def test_grouping(self):
        items = [
            {"item_cd": "A", "small_nm": "과자"},
            {"item_cd": "B", "small_nm": "음료"},
            {"item_cd": "C", "small_nm": "과자"},
        ]
        groups = group_new_items_by_category(items)
        assert len(groups) == 2
        assert len(groups["과자"]) == 2
        assert len(groups["음료"]) == 1


# ═══════════════════════════════════════════════════
# convenience_order_scheduler 테스트
# ═══════════════════════════════════════════════════

class TestPlan3DayOrders:
    """3일 발주 계획"""

    def test_normal_period(self):
        """21일 기간 → 3등분: 0일차, 7일차, 14일차"""
        plan = plan_3day_orders("TEST001", "2026-01-01", "2026-01-21")
        assert len(plan) == 3
        assert plan[0] == "2026-01-01"

    def test_short_period(self):
        """짧은 기간에도 3회 계획"""
        plan = plan_3day_orders("TEST001", "2026-01-01", "2026-01-07")
        assert len(plan) == 3

    def test_yy_mm_dd_format(self):
        """YY.MM.DD 형식 지원"""
        plan = plan_3day_orders("TEST001", "26.01.01", "26.01.21")
        assert len(plan) == 3


class TestShouldOrderToday:
    """오늘 발주 여부 판단"""

    def test_on_plan_date(self):
        """계획된 발주일이면 True"""
        result, reason = should_order_today(
            "TEST001", "2026-01-01",
            ["2026-01-01", "2026-01-08", "2026-01-15"],
            current_stock=0, daily_avg_sales=1.0,
        )
        assert result is True

    def test_already_completed(self):
        """3회 완료면 False"""
        result, reason = should_order_today(
            "TEST001", "2026-01-20",
            ["2026-01-01", "2026-01-08", "2026-01-15"],
            current_stock=0, daily_avg_sales=1.0,
            orders_placed=3,
        )
        assert result is False
        assert "3회" in reason

    def test_overstock_skip(self):
        """재고 과잉이면 스킵"""
        result, reason = should_order_today(
            "TEST001", "2026-01-01",
            ["2026-01-01"],
            current_stock=10,
            daily_avg_sales=1.0,
            shelf_life_days=3,  # threshold = 3 * 1.0 * 1.5 = 4.5
        )
        assert result is False
        assert "과잉" in reason

    def test_sold_reorder(self):
        """이전 발주분 판매 완료 → 즉시 재발주"""
        result, reason = should_order_today(
            "TEST001", "2026-01-05",
            ["2026-01-01", "2026-01-08", "2026-01-15"],
            current_stock=0, daily_avg_sales=1.0,
            orders_placed=1, last_order_sold=True,
        )
        assert result is True
        assert "판매 완료" in reason


class TestGetRemainingOrdersNeeded:
    """잔여 필요 발주 횟수"""

    def test_none_placed(self):
        assert get_remaining_orders_needed(0) == 3

    def test_one_placed(self):
        assert get_remaining_orders_needed(1) == 2

    def test_all_placed(self):
        assert get_remaining_orders_needed(3) == 0


# ═══════════════════════════════════════════════════
# Repository CRUD 테스트
# ═══════════════════════════════════════════════════

@pytest.fixture
def np_db():
    """인메모리 DB with new_product 테이블"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE new_product_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            month_ym TEXT NOT NULL,
            week_no INTEGER NOT NULL,
            period TEXT,
            doip_rate REAL, item_cnt INTEGER DEFAULT 0,
            item_ad_cnt INTEGER DEFAULT 0, doip_cnt INTEGER DEFAULT 0,
            midoip_cnt INTEGER DEFAULT 0, ds_rate REAL,
            ds_item_cnt INTEGER DEFAULT 0, ds_cnt INTEGER DEFAULT 0,
            mids_cnt INTEGER DEFAULT 0, doip_score REAL, ds_score REAL,
            tot_score REAL, supp_pay_amt INTEGER DEFAULT 0,
            sta_dd TEXT, end_dd TEXT, week_cont TEXT,
            collected_at TEXT NOT NULL,
            UNIQUE(store_id, month_ym, week_no)
        )
    """)
    conn.execute("""
        CREATE TABLE new_product_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            month_ym TEXT NOT NULL,
            week_no INTEGER NOT NULL,
            item_type TEXT NOT NULL,
            item_cd TEXT NOT NULL,
            item_nm TEXT, small_nm TEXT, ord_pss_nm TEXT,
            week_cont TEXT, ds_yn TEXT,
            is_ordered INTEGER DEFAULT 0,
            ordered_at TEXT,
            collected_at TEXT NOT NULL,
            UNIQUE(store_id, month_ym, week_no, item_type, item_cd)
        )
    """)
    conn.execute("""
        CREATE TABLE new_product_monthly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            month_ym TEXT NOT NULL,
            doip_item_cnt INTEGER DEFAULT 0,
            doip_cnt INTEGER DEFAULT 0,
            doip_rate REAL, doip_score REAL,
            ds_item_cnt INTEGER DEFAULT 0,
            ds_cnt INTEGER DEFAULT 0,
            ds_rate REAL, ds_score REAL,
            tot_score REAL, supp_pay_amt INTEGER DEFAULT 0,
            next_min_score REAL, next_max_score REAL,
            next_supp_pay_amt INTEGER DEFAULT 0,
            collected_at TEXT NOT NULL,
            UNIQUE(store_id, month_ym)
        )
    """)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def np_repo(np_db, tmp_path):
    """NewProductStatusRepository with in-memory DB"""
    db_file = tmp_path / "test_np.db"
    file_conn = sqlite3.connect(str(db_file))
    np_db.backup(file_conn)
    file_conn.close()

    repo = NewProductStatusRepository(db_path=db_file)
    return repo


class TestNewProductRepository:
    """Repository CRUD 테스트"""

    def test_save_and_get_weekly(self, np_repo):
        np_repo.save_weekly_status("46513", "202601", 1, {
            "doip_rate": 70.1, "item_cnt": 77,
            "doip_cnt": 54, "midoip_cnt": 23,
            "ds_rate": 53.3, "tot_score": 76,
        })

        result = np_repo.get_weekly_status("46513", "202601")
        assert len(result) == 1
        assert result[0]["doip_rate"] == 70.1

    def test_save_items(self, np_repo):
        items = [
            {"item_cd": "8801234567890", "item_nm": "테스트상품A",
             "small_nm": "과자", "ord_pss_nm": "발주가능"},
            {"item_cd": "8801234567891", "item_nm": "테스트상품B",
             "small_nm": "음료", "ord_pss_nm": "발주불가"},
        ]
        saved = np_repo.save_items("46513", "202601", 1, "midoip", items)
        assert saved == 2

    def test_get_unordered_items(self, np_repo):
        items = [
            {"item_cd": "A001", "item_nm": "상품A", "ord_pss_nm": "발주가능"},
            {"item_cd": "A002", "item_nm": "상품B", "ord_pss_nm": "발주가능"},
            {"item_cd": "A003", "item_nm": "상품C", "ord_pss_nm": "발주불가"},
        ]
        np_repo.save_items("46513", "202601", 1, "midoip", items)

        unordered = np_repo.get_unordered_items("46513", "202601", "midoip")
        assert len(unordered) == 2  # 발주가능만

    def test_mark_as_ordered(self, np_repo):
        items = [
            {"item_cd": "A001", "item_nm": "상품A", "ord_pss_nm": "발주가능"},
        ]
        np_repo.save_items("46513", "202601", 1, "midoip", items)
        np_repo.mark_as_ordered("46513", "202601", "A001")

        unordered = np_repo.get_unordered_items("46513", "202601", "midoip")
        assert len(unordered) == 0

    def test_get_items_in_verification(self, np_repo):
        items = [
            {"item_cd": "A001", "item_nm": "상품A", "ord_pss_nm": "발주가능"},
            {"item_cd": "A002", "item_nm": "상품B", "ord_pss_nm": "발주가능"},
        ]
        np_repo.save_items("46513", "202601", 1, "midoip", items)
        np_repo.mark_as_ordered("46513", "202601", "A001")

        verification = np_repo.get_items_in_verification("46513", "202601")
        assert "A001" in verification
        assert "A002" not in verification

    def test_save_monthly(self, np_repo):
        np_repo.save_monthly("46513", "202601", {
            "doip_item_cnt": 100, "doip_cnt": 70,
            "doip_rate": 70.0, "doip_score": 56,
            "ds_item_cnt": 15, "ds_cnt": 8,
            "ds_rate": 53.3, "ds_score": 10,
            "tot_score": 66, "supp_pay_amt": 80000,
        })

        result = np_repo.get_monthly_summary("46513", "202601")
        assert result is not None
        assert result["tot_score"] == 66

    def test_current_status(self, np_repo):
        np_repo.save_weekly_status("46513", "202601", 1, {"tot_score": 60})
        np_repo.save_weekly_status("46513", "202601", 2, {"tot_score": 70})
        np_repo.save_weekly_status("46513", "202601", 3, {"tot_score": 80})

        current = np_repo.get_current_status("46513", "202601")
        assert current is not None
        assert current["week_no"] == 3
        assert current["tot_score"] == 80
