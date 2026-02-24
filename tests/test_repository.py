"""
db/repository.py 유닛 테스트

- SalesRepository: save_daily_sales(), get_daily_sales(), get_stats_summary()
- ProductDetailRepository: save(), get(), get_all()
- OrderRepository: save_order(), get_pending_orders(), update_status()

각 Repository 클래스는 BaseRepository를 상속하며 db_path를 생성자에서 받는다.
tmp_path로 격리된 DB 파일을 생성하여 테스트한다.
"""

import sqlite3
from pathlib import Path

import pytest

from src.db.models import init_db
from src.infrastructure.database.repos import (
    OrderRepository,
    ProductDetailRepository,
    SalesRepository,
)


# =========================================================================
# 공통 픽스처
# =========================================================================

@pytest.fixture
def test_db(tmp_path):
    """테스트용 격리 DB 파일 생성 (전체 스키마 초기화)"""
    db_file = tmp_path / "test_repo.db"
    init_db(db_file)
    return db_file


@pytest.fixture
def sales_repo(test_db):
    """SalesRepository 인스턴스"""
    return SalesRepository(db_path=test_db)


@pytest.fixture
def product_detail_repo(test_db):
    """ProductDetailRepository 인스턴스"""
    return ProductDetailRepository(db_path=test_db)


@pytest.fixture
def order_repo(test_db):
    """OrderRepository 인스턴스"""
    return OrderRepository(db_path=test_db)


# =========================================================================
# SalesRepository 테스트
# =========================================================================

class TestSalesRepository:
    """SalesRepository 클래스 테스트"""

    @pytest.mark.db
    def test_save_daily_sales_new_item(self, sales_repo):
        """신규 상품 판매 데이터를 저장하면 new 카운트가 증가한다"""
        data = [
            {
                "ITEM_CD": "8801234567890",
                "ITEM_NM": "테스트맥주",
                "MID_CD": "049",
                "MID_NM": "맥주",
                "SALE_QTY": 5,
                "ORD_QTY": 10,
                "BUY_QTY": 10,
                "DISUSE_QTY": 0,
                "STOCK_QTY": 15,
            }
        ]
        result = sales_repo.save_daily_sales(data, "2026-01-30")

        assert result["total"] == 1
        assert result["new"] == 1
        assert result["updated"] == 0

    @pytest.mark.db
    def test_save_daily_sales_update_existing(self, sales_repo):
        """같은 날짜/상품으로 다시 저장하면 updated 카운트가 증가한다"""
        data = [
            {
                "ITEM_CD": "8801234567890",
                "ITEM_NM": "테스트맥주",
                "MID_CD": "049",
                "MID_NM": "맥주",
                "SALE_QTY": 5,
                "ORD_QTY": 10,
                "BUY_QTY": 10,
                "DISUSE_QTY": 0,
                "STOCK_QTY": 15,
            }
        ]

        # 첫 번째 저장
        sales_repo.save_daily_sales(data, "2026-01-30")

        # 동일 날짜/상품으로 두 번째 저장 (업데이트)
        data[0]["SALE_QTY"] = 8
        result = sales_repo.save_daily_sales(data, "2026-01-30")

        assert result["total"] == 1
        assert result["new"] == 0
        assert result["updated"] == 1

    @pytest.mark.db
    def test_save_daily_sales_skips_empty_item_cd(self, sales_repo):
        """ITEM_CD가 비어있으면 건너뛴다"""
        data = [
            {
                "ITEM_CD": "",
                "ITEM_NM": "빈코드상품",
                "MID_CD": "049",
                "MID_NM": "맥주",
                "SALE_QTY": 5,
            }
        ]
        result = sales_repo.save_daily_sales(data, "2026-01-30")

        assert result["total"] == 0

    @pytest.mark.db
    def test_save_daily_sales_multiple_items(self, sales_repo):
        """여러 상품을 한 번에 저장할 수 있다"""
        data = [
            {
                "ITEM_CD": "8801234567890",
                "ITEM_NM": "맥주A",
                "MID_CD": "049",
                "MID_NM": "맥주",
                "SALE_QTY": 5,
            },
            {
                "ITEM_CD": "8801234567891",
                "ITEM_NM": "소주A",
                "MID_CD": "050",
                "MID_NM": "소주",
                "SALE_QTY": 3,
            },
        ]
        result = sales_repo.save_daily_sales(data, "2026-01-30")

        assert result["total"] == 2
        assert result["new"] == 2

    @pytest.mark.db
    def test_get_daily_sales_returns_data(self, sales_repo):
        """저장된 판매 데이터를 날짜로 조회할 수 있다"""
        data = [
            {
                "ITEM_CD": "8801234567890",
                "ITEM_NM": "테스트맥주",
                "MID_CD": "049",
                "MID_NM": "맥주",
                "SALE_QTY": 5,
                "ORD_QTY": 10,
                "BUY_QTY": 10,
                "DISUSE_QTY": 0,
                "STOCK_QTY": 15,
            }
        ]
        sales_repo.save_daily_sales(data, "2026-01-30")

        result = sales_repo.get_daily_sales("2026-01-30")

        assert len(result) == 1
        assert result[0]["item_cd"] == "8801234567890"
        assert result[0]["sale_qty"] == 5

    @pytest.mark.db
    def test_get_daily_sales_filter_by_mid_cd(self, sales_repo):
        """중분류 코드로 필터링할 수 있다"""
        data = [
            {
                "ITEM_CD": "8801234567890",
                "ITEM_NM": "맥주A",
                "MID_CD": "049",
                "MID_NM": "맥주",
                "SALE_QTY": 5,
            },
            {
                "ITEM_CD": "8801234567891",
                "ITEM_NM": "소주A",
                "MID_CD": "050",
                "MID_NM": "소주",
                "SALE_QTY": 3,
            },
        ]
        sales_repo.save_daily_sales(data, "2026-01-30")

        result = sales_repo.get_daily_sales("2026-01-30", mid_cd="049")

        assert len(result) == 1
        assert result[0]["mid_cd"] == "049"

    @pytest.mark.db
    def test_get_daily_sales_empty_for_missing_date(self, sales_repo):
        """데이터가 없는 날짜를 조회하면 빈 리스트를 반환한다"""
        result = sales_repo.get_daily_sales("2020-01-01")
        assert result == []

    @pytest.mark.db
    def test_get_stats_summary_empty_db(self, sales_repo):
        """빈 DB의 통계 요약을 조회할 수 있다"""
        result = sales_repo.get_stats_summary()

        assert result["total_products"] == 0
        assert result["total_categories"] == 0
        assert result["total_days"] == 0

    @pytest.mark.db
    def test_get_stats_summary_with_data(self, sales_repo):
        """데이터가 있는 DB의 통계 요약을 조회할 수 있다"""
        data = [
            {
                "ITEM_CD": "8801234567890",
                "ITEM_NM": "맥주A",
                "MID_CD": "049",
                "MID_NM": "맥주",
                "SALE_QTY": 5,
            },
            {
                "ITEM_CD": "8801234567891",
                "ITEM_NM": "소주A",
                "MID_CD": "050",
                "MID_NM": "소주",
                "SALE_QTY": 3,
            },
        ]
        sales_repo.save_daily_sales(data, "2026-01-30")
        sales_repo.save_daily_sales(
            [data[0]],  # 맥주A만 다른 날짜에 추가
            "2026-01-29",
        )

        result = sales_repo.get_stats_summary()

        assert result["total_products"] == 2
        assert result["total_categories"] == 2
        assert result["total_days"] == 2
        assert result["first_date"] == "2026-01-29"
        assert result["last_date"] == "2026-01-30"

    @pytest.mark.db
    def test_get_sales_history(self, sales_repo):
        """특정 상품의 판매 이력을 조회할 수 있다"""
        item = {
            "ITEM_CD": "8801234567890",
            "ITEM_NM": "맥주A",
            "MID_CD": "049",
            "MID_NM": "맥주",
            "SALE_QTY": 5,
            "STOCK_QTY": 10,
        }
        sales_repo.save_daily_sales([item], "2026-01-28")
        item["SALE_QTY"] = 7
        sales_repo.save_daily_sales([item], "2026-01-29")
        item["SALE_QTY"] = 3
        sales_repo.save_daily_sales([item], "2026-01-30")

        result = sales_repo.get_sales_history("8801234567890", days=30)

        assert len(result) == 3
        # 최신순 정렬
        assert result[0]["sales_date"] == "2026-01-30"
        assert result[0]["sale_qty"] == 3


# =========================================================================
# ProductDetailRepository 테스트
# =========================================================================

class TestProductDetailRepository:
    """ProductDetailRepository 클래스 테스트"""

    @pytest.mark.db
    def test_save_and_get(self, product_detail_repo):
        """상품 상세 정보를 저장하고 조회할 수 있다"""
        info = {
            "item_nm": "테스트맥주500ml",
            "expiration_days": 365,
            "orderable_day": "일월화수목금토",
            "orderable_status": "정상",
            "order_unit_name": "낱개",
            "order_unit_qty": 1,
            "case_unit_qty": 24,
            "lead_time_days": 1,
        }
        product_detail_repo.save("8801234567890", info)

        result = product_detail_repo.get("8801234567890")

        assert result is not None
        assert result["item_cd"] == "8801234567890"
        assert result["item_nm"] == "테스트맥주500ml"
        assert result["expiration_days"] == 365
        assert result["order_unit_qty"] == 1
        assert result["case_unit_qty"] == 24

    @pytest.mark.db
    def test_get_nonexistent_returns_none(self, product_detail_repo):
        """존재하지 않는 상품을 조회하면 None을 반환한다"""
        result = product_detail_repo.get("9999999999999")
        assert result is None

    @pytest.mark.db
    def test_save_upsert_updates_existing(self, product_detail_repo):
        """같은 상품코드로 다시 저장하면 업데이트된다"""
        info1 = {
            "item_nm": "맥주A",
            "expiration_days": 365,
            "order_unit_qty": 1,
        }
        product_detail_repo.save("8801234567890", info1)

        info2 = {
            "item_nm": "맥주A (변경)",
            "expiration_days": 180,
            "order_unit_qty": 6,
        }
        product_detail_repo.save("8801234567890", info2)

        result = product_detail_repo.get("8801234567890")

        assert result["item_nm"] == "맥주A (변경)"
        assert result["expiration_days"] == 180
        assert result["order_unit_qty"] == 6

    @pytest.mark.db
    def test_get_all_empty(self, product_detail_repo):
        """상품이 없으면 빈 리스트를 반환한다"""
        result = product_detail_repo.get_all()
        assert result == []

    @pytest.mark.db
    def test_get_all_multiple_items(self, product_detail_repo):
        """여러 상품의 상세 정보를 모두 조회할 수 있다"""
        items = [
            ("8801234567890", {"item_nm": "맥주A", "expiration_days": 365}),
            ("8801234567891", {"item_nm": "소주A", "expiration_days": 365}),
            ("8801234567892", {"item_nm": "도시락A", "expiration_days": 1}),
        ]
        for item_cd, info in items:
            product_detail_repo.save(item_cd, info)

        result = product_detail_repo.get_all()

        assert len(result) == 3
        # item_cd 순으로 정렬됨
        assert result[0]["item_cd"] == "8801234567890"
        assert result[1]["item_cd"] == "8801234567891"
        assert result[2]["item_cd"] == "8801234567892"

    @pytest.mark.db
    def test_exists_true(self, product_detail_repo):
        """저장된 상품은 exists()가 True를 반환한다"""
        product_detail_repo.save("8801234567890", {"item_nm": "맥주A"})
        assert product_detail_repo.exists("8801234567890") is True

    @pytest.mark.db
    def test_exists_false(self, product_detail_repo):
        """존재하지 않는 상품은 exists()가 False를 반환한다"""
        assert product_detail_repo.exists("9999999999999") is False

    @pytest.mark.db
    def test_save_with_product_name_key(self, product_detail_repo):
        """product_name 키로도 상품명이 저장된다"""
        info = {
            "product_name": "맥주B",
            "expiration_days": 365,
        }
        product_detail_repo.save("8801234567890", info)

        result = product_detail_repo.get("8801234567890")
        assert result["item_nm"] == "맥주B"

    @pytest.mark.db
    def test_bulk_update_order_unit_qty_new_items(self, product_detail_repo):
        """새 상품의 order_unit_qty를 일괄 저장할 수 있다"""
        items = [
            {"item_cd": "1111111111111", "item_nm": "캔디A", "order_unit_qty": 12},
            {"item_cd": "2222222222222", "item_nm": "라면B", "order_unit_qty": 6},
        ]
        count = product_detail_repo.bulk_update_order_unit_qty(items)
        assert count == 2

        r1 = product_detail_repo.get("1111111111111")
        assert r1["order_unit_qty"] == 12
        r2 = product_detail_repo.get("2222222222222")
        assert r2["order_unit_qty"] == 6

    @pytest.mark.db
    def test_bulk_update_order_unit_qty_preserves_existing(self, product_detail_repo):
        """기존 상품의 다른 필드(expiration_days 등)를 보존한다"""
        product_detail_repo.save("8801234567890", {
            "item_nm": "맥주A",
            "expiration_days": 365,
            "order_unit_qty": 1,
        })

        items = [
            {"item_cd": "8801234567890", "item_nm": "맥주A", "order_unit_qty": 24},
        ]
        product_detail_repo.bulk_update_order_unit_qty(items)

        result = product_detail_repo.get("8801234567890")
        assert result["order_unit_qty"] == 24
        assert result["expiration_days"] == 365

    @pytest.mark.db
    def test_bulk_update_order_unit_qty_empty_list(self, product_detail_repo):
        """빈 리스트를 넘기면 0건 반환"""
        assert product_detail_repo.bulk_update_order_unit_qty([]) == 0

    @pytest.mark.db
    def test_bulk_update_order_unit_qty_preserves_item_nm(self, product_detail_repo):
        """item_nm이 빈 문자열이면 기존 값을 보존한다"""
        product_detail_repo.save("8801234567890", {
            "item_nm": "맥주A",
            "order_unit_qty": 1,
        })

        items = [
            {"item_cd": "8801234567890", "item_nm": "", "order_unit_qty": 6},
        ]
        product_detail_repo.bulk_update_order_unit_qty(items)

        result = product_detail_repo.get("8801234567890")
        assert result["order_unit_qty"] == 6
        assert result["item_nm"] == "맥주A"


# =========================================================================
# OrderRepository 테스트
# =========================================================================

class TestOrderRepository:
    """OrderRepository 클래스 테스트"""

    @pytest.mark.db
    def test_save_order_returns_id(self, order_repo):
        """발주 이력 저장 시 레코드 ID를 반환한다"""
        order_id = order_repo.save_order(
            order_date="2026-01-30",
            item_cd="8801234567890",
            mid_cd="049",
            predicted_qty=5,
            recommended_qty=10,
            current_stock=3,
            status="pending",
        )

        assert isinstance(order_id, int)
        assert order_id > 0

    @pytest.mark.db
    def test_save_order_default_status_is_pending(self, order_repo):
        """기본 status는 'pending' 이다"""
        order_id = order_repo.save_order(
            order_date="2026-01-30",
            item_cd="8801234567890",
        )

        orders = order_repo.get_pending_orders()
        assert len(orders) == 1
        assert orders[0]["status"] == "pending"

    @pytest.mark.db
    def test_get_pending_orders_empty(self, order_repo):
        """pending 발주가 없으면 빈 리스트를 반환한다"""
        result = order_repo.get_pending_orders()
        assert result == []

    @pytest.mark.db
    def test_get_pending_orders_returns_only_pending(self, order_repo):
        """pending 상태의 발주만 반환한다"""
        # pending 발주
        order_repo.save_order(
            order_date="2026-01-30",
            item_cd="8801234567890",
            status="pending",
        )
        # ordered 발주
        order_repo.save_order(
            order_date="2026-01-30",
            item_cd="8801234567891",
            status="ordered",
        )

        result = order_repo.get_pending_orders()

        assert len(result) == 1
        assert result[0]["item_cd"] == "8801234567890"

    @pytest.mark.db
    def test_update_status(self, order_repo):
        """발주 상태를 업데이트할 수 있다"""
        order_id = order_repo.save_order(
            order_date="2026-01-30",
            item_cd="8801234567890",
            status="pending",
        )

        order_repo.update_status(order_id, "ordered")

        # pending 목록에서 사라져야 함
        pending = order_repo.get_pending_orders()
        assert len(pending) == 0

    @pytest.mark.db
    def test_update_status_with_actual_qty(self, order_repo):
        """상태 업데이트 시 실제 발주 수량도 함께 변경할 수 있다"""
        order_id = order_repo.save_order(
            order_date="2026-01-30",
            item_cd="8801234567890",
            predicted_qty=5,
            recommended_qty=10,
            status="pending",
        )

        order_repo.update_status(order_id, "ordered", actual_qty=8)

        orders = order_repo.get_orders_by_date("2026-01-30")
        assert len(orders) == 1
        assert orders[0]["status"] == "ordered"
        assert orders[0]["actual_order_qty"] == 8

    @pytest.mark.db
    def test_get_orders_by_date(self, order_repo):
        """날짜별 발주 이력을 조회할 수 있다"""
        order_repo.save_order(
            order_date="2026-01-30",
            item_cd="8801234567890",
            mid_cd="049",
            predicted_qty=5,
        )
        order_repo.save_order(
            order_date="2026-01-30",
            item_cd="8801234567891",
            mid_cd="050",
            predicted_qty=3,
        )
        order_repo.save_order(
            order_date="2026-01-29",
            item_cd="8801234567892",
            mid_cd="072",
            predicted_qty=8,
        )

        result = order_repo.get_orders_by_date("2026-01-30")

        assert len(result) == 2

    @pytest.mark.db
    def test_get_orders_by_date_empty(self, order_repo):
        """발주가 없는 날짜를 조회하면 빈 리스트를 반환한다"""
        result = order_repo.get_orders_by_date("2020-01-01")
        assert result == []

    @pytest.mark.db
    def test_save_multiple_orders_increments_id(self, order_repo):
        """여러 발주를 저장하면 ID가 순차적으로 증가한다"""
        id1 = order_repo.save_order(
            order_date="2026-01-30",
            item_cd="8801234567890",
        )
        id2 = order_repo.save_order(
            order_date="2026-01-30",
            item_cd="8801234567891",
        )

        assert id2 > id1
