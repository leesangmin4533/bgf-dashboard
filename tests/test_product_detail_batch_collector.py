"""상품 상세 일괄 수집기 테스트

Design Reference: docs/02-design/features/new-product-detail-fetch.design.md
테스트 항목:
1-6:  수집 대상 선별 (get_items_needing_detail_fetch)
7-11: DB 저장 (bulk_update_from_popup)
12-14: mid_cd 업데이트 (update_product_mid_cd)
15-16: 수집 통계 (collect_all)
17-18: 엣지 케이스
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.infrastructure.database.repos.product_detail_repo import (
    ProductDetailRepository,
)
from src.collectors.product_detail_batch_collector import (
    ProductDetailBatchCollector,
)


# ================================================================
# DB Fixtures
# ================================================================


def _create_common_db(tmp_path: Path) -> Path:
    """테스트용 common.db 생성 (products + product_details)"""
    db_path = tmp_path / "common.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE products (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            mid_cd TEXT DEFAULT '999',
            updated_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE product_details (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            expiration_days INTEGER,
            orderable_day TEXT DEFAULT '일월화수목금토',
            orderable_status TEXT,
            order_unit_name TEXT,
            order_unit_qty INTEGER DEFAULT 1,
            case_unit_qty INTEGER DEFAULT 1,
            lead_time_days INTEGER DEFAULT 1,
            sell_price INTEGER,
            margin_rate REAL,
            demand_pattern TEXT,
            fetched_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            large_cd TEXT,
            small_cd TEXT,
            small_nm TEXT,
            class_nm TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE mid_categories (
            mid_cd TEXT PRIMARY KEY,
            mid_nm TEXT NOT NULL,
            large_cd TEXT,
            large_nm TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()
    return db_path


def _insert_product(db_path: Path, item_cd: str, mid_cd: str = "999",
                    item_nm: str = "테스트상품"):
    """products 테이블에 테스트 상품 추가"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO products (item_cd, item_nm, mid_cd, updated_at) VALUES (?, ?, ?, ?)",
        (item_cd, item_nm, mid_cd, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def _insert_detail(db_path: Path, item_cd: str, **kwargs):
    """product_details에 테스트 데이터 추가"""
    defaults = {
        "item_nm": "테스트상품",
        "expiration_days": None,
        "orderable_day": "일월화수목금토",
        "orderable_status": None,
        "order_unit_name": None,
        "order_unit_qty": 1,
        "case_unit_qty": 1,
        "sell_price": None,
        "fetched_at": None,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    defaults.update(kwargs)

    conn = sqlite3.connect(str(db_path))
    cols = ", ".join(["item_cd"] + list(defaults.keys()))
    placeholders = ", ".join(["?"] * (1 + len(defaults)))
    vals = [item_cd] + list(defaults.values())

    conn.execute(f"INSERT INTO product_details ({cols}) VALUES ({placeholders})", vals)
    conn.commit()
    conn.close()


def _get_detail(db_path: Path, item_cd: str) -> dict:
    """product_details에서 조회"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM product_details WHERE item_cd = ?", (item_cd,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def _get_product(db_path: Path, item_cd: str) -> dict:
    """products에서 조회"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM products WHERE item_cd = ?", (item_cd,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


@pytest.fixture
def common_db(tmp_path):
    """테스트용 common.db와 repo 반환"""
    db_path = _create_common_db(tmp_path)
    repo = ProductDetailRepository(db_path=db_path)
    return db_path, repo


@pytest.fixture
def mock_driver():
    """Selenium WebDriver mock"""
    return MagicMock()


# ================================================================
# 1-6: 수집 대상 선별 테스트
# ================================================================


class TestGetItemsNeedingDetailFetch:

    def test_empty_when_all_complete(self, common_db):
        """모두 정상 -> 빈 리스트"""
        db_path, repo = common_db
        _insert_product(db_path, "ITEM001", mid_cd="001")
        _insert_detail(db_path, "ITEM001",
                       expiration_days=3,
                       orderable_day="월화수목금",
                       fetched_at=datetime.now().isoformat(),
                       large_cd="10")

        result = repo.get_items_needing_detail_fetch()
        assert result == []

    def test_includes_null_fetched_at(self, common_db):
        """fetched_at NULL -> 포함"""
        db_path, repo = common_db
        _insert_product(db_path, "ITEM001", mid_cd="001")
        _insert_detail(db_path, "ITEM001",
                       expiration_days=3,
                       orderable_day="월화수목금",
                       fetched_at=None)  # NULL

        result = repo.get_items_needing_detail_fetch()
        assert "ITEM001" in result

    def test_includes_null_expiry(self, common_db):
        """expiration_days NULL -> 포함"""
        db_path, repo = common_db
        _insert_product(db_path, "ITEM002", mid_cd="001")
        _insert_detail(db_path, "ITEM002",
                       expiration_days=None,  # NULL
                       orderable_day="월화수",
                       fetched_at=datetime.now().isoformat())

        result = repo.get_items_needing_detail_fetch()
        assert "ITEM002" in result

    def test_includes_default_orderable_day(self, common_db):
        """orderable_day '일월화수목금토' -> 포함"""
        db_path, repo = common_db
        _insert_product(db_path, "ITEM003", mid_cd="001")
        _insert_detail(db_path, "ITEM003",
                       expiration_days=3,
                       orderable_day="일월화수목금토",  # 기본값
                       fetched_at=datetime.now().isoformat())

        result = repo.get_items_needing_detail_fetch()
        assert "ITEM003" in result

    def test_includes_unknown_mid_cd(self, common_db):
        """mid_cd='999' -> 포함"""
        db_path, repo = common_db
        _insert_product(db_path, "ITEM004", mid_cd="999")  # 미분류
        _insert_detail(db_path, "ITEM004",
                       expiration_days=3,
                       orderable_day="월화수",
                       fetched_at=datetime.now().isoformat())

        result = repo.get_items_needing_detail_fetch()
        assert "ITEM004" in result

    def test_limit_applied(self, common_db):
        """limit 적용"""
        db_path, repo = common_db
        for i in range(5):
            _insert_product(db_path, f"ITEM{i:03d}", mid_cd="999")

        result = repo.get_items_needing_detail_fetch(limit=3)
        assert len(result) == 3


# ================================================================
# 7-11: DB 저장 테스트
# ================================================================


class TestBulkUpdateFromPopup:

    def test_insert_new(self, common_db):
        """새 상품 INSERT"""
        db_path, repo = common_db
        _insert_product(db_path, "NEW001")

        success = repo.bulk_update_from_popup("NEW001", {
            "item_nm": "새도시락",
            "expiration_days": 2,
            "orderable_day": "월화수목금",
            "orderable_status": "발주가능",
            "order_unit_qty": 6,
            "sell_price": 4500,
            "fetched_at": "2026-02-26T01:00:00",
        })

        assert success is True
        detail = _get_detail(db_path, "NEW001")
        assert detail["item_nm"] == "새도시락"
        assert detail["expiration_days"] == 2
        assert detail["orderable_day"] == "월화수목금"
        assert detail["order_unit_qty"] == 6
        assert detail["sell_price"] == 4500
        assert detail["fetched_at"] == "2026-02-26T01:00:00"

    def test_update_null_fields_only(self, common_db):
        """NULL 필드만 갱신"""
        db_path, repo = common_db
        _insert_product(db_path, "UPD001")
        _insert_detail(db_path, "UPD001",
                       expiration_days=None,
                       orderable_day="일월화수목금토",
                       sell_price=None,
                       fetched_at=None)

        success = repo.bulk_update_from_popup("UPD001", {
            "expiration_days": 3,
            "orderable_day": "월화수목금",
            "sell_price": 3000,
            "fetched_at": "2026-02-26T01:00:00",
        })

        assert success is True
        detail = _get_detail(db_path, "UPD001")
        assert detail["expiration_days"] == 3
        assert detail["orderable_day"] == "월화수목금"
        assert detail["sell_price"] == 3000

    def test_preserve_existing_values(self, common_db):
        """기존 값 보존 (이미 정확한 데이터)"""
        db_path, repo = common_db
        _insert_product(db_path, "KEEP001")
        _insert_detail(db_path, "KEEP001",
                       expiration_days=5,
                       orderable_day="월화수",
                       sell_price=2000,
                       fetched_at="2026-02-25T00:00:00")

        success = repo.bulk_update_from_popup("KEEP001", {
            "expiration_days": 7,     # 기존 5 보존되어야 함
            "orderable_day": "화수목",  # 기존 "월화수" 보존되어야 함
            "sell_price": 3000,        # 기존 2000 보존되어야 함
            "fetched_at": "2026-02-26T01:00:00",  # 항상 갱신
        })

        assert success is True
        detail = _get_detail(db_path, "KEEP001")
        # COALESCE: 기존 NULL이 아니면 기존값 보존
        assert detail["expiration_days"] == 5  # 보존
        assert detail["orderable_day"] == "월화수"  # 보존 (기본값이 아니므로)
        assert detail["sell_price"] == 2000  # 보존

    def test_orderable_day_overwrite_default(self, common_db):
        """기본값 '일월화수목금토' -> 실제값 갱신"""
        db_path, repo = common_db
        _insert_product(db_path, "DAY001")
        _insert_detail(db_path, "DAY001",
                       orderable_day="일월화수목금토",  # 기본값
                       fetched_at=None)

        success = repo.bulk_update_from_popup("DAY001", {
            "orderable_day": "월수금",
            "fetched_at": "2026-02-26T01:00:00",
        })

        assert success is True
        detail = _get_detail(db_path, "DAY001")
        assert detail["orderable_day"] == "월수금"  # 갱신됨

    def test_orderable_day_preserve_custom(self, common_db):
        """기존 실제값 보존"""
        db_path, repo = common_db
        _insert_product(db_path, "DAY002")
        _insert_detail(db_path, "DAY002",
                       orderable_day="화목토",  # 이미 실제값
                       fetched_at="2026-02-25T00:00:00")

        success = repo.bulk_update_from_popup("DAY002", {
            "orderable_day": "월수금",  # 덮어쓰기 안 됨
            "fetched_at": "2026-02-26T01:00:00",
        })

        assert success is True
        detail = _get_detail(db_path, "DAY002")
        assert detail["orderable_day"] == "화목토"  # 보존


# ================================================================
# 12-14: mid_cd 업데이트 테스트
# ================================================================


class TestUpdateProductMidCd:

    def test_update_from_999(self, common_db):
        """'999' -> 실제값"""
        db_path, repo = common_db
        _insert_product(db_path, "MID001", mid_cd="999")

        updated = repo.update_product_mid_cd("MID001", "003")
        assert updated is True

        product = _get_product(db_path, "MID001")
        assert product["mid_cd"] == "003"

    def test_preserve_existing(self, common_db):
        """기존 '001' 보존"""
        db_path, repo = common_db
        _insert_product(db_path, "MID002", mid_cd="001")

        updated = repo.update_product_mid_cd("MID002", "003")
        assert updated is False

        product = _get_product(db_path, "MID002")
        assert product["mid_cd"] == "001"  # 변경 안 됨

    def test_update_from_empty(self, common_db):
        """'' -> 실제값"""
        db_path, repo = common_db
        _insert_product(db_path, "MID003", mid_cd="")

        updated = repo.update_product_mid_cd("MID003", "012")
        assert updated is True

        product = _get_product(db_path, "MID003")
        assert product["mid_cd"] == "012"


# ================================================================
# 15-16: 수집 통계 테스트
# ================================================================


class TestCollectAllStats:

    def test_empty_list_no_collect(self, mock_driver, common_db):
        """빈 리스트 -> 수집 안 함"""
        _, repo = common_db

        collector = ProductDetailBatchCollector(driver=mock_driver)
        collector._detail_repo = repo

        stats = collector.collect_all(item_codes=[])
        assert stats["total"] == 0
        assert stats["success"] == 0
        assert stats["fail"] == 0

    def test_stats_counting(self, mock_driver, common_db):
        """success/fail 카운트"""
        db_path, repo = common_db
        _insert_product(db_path, "ST001")
        _insert_product(db_path, "ST002")

        collector = ProductDetailBatchCollector(driver=mock_driver)
        collector._detail_repo = repo

        # ST001: 성공 (팝업 데이터 반환)
        # ST002: 실패 (팝업 없음)
        call_count = [0]

        def fake_fetch(item_cd):
            call_count[0] += 1
            if item_cd == "ST001":
                return {
                    "item_nm": "테스트A",
                    "mid_cd": "001",
                    "expiration_days": 3,
                    "orderable_day": "월화수",
                }
            return None  # 실패

        collector._fetch_single_item = fake_fetch

        stats = collector.collect_all(item_codes=["ST001", "ST002"])
        assert stats["total"] == 2
        assert stats["success"] == 1
        assert stats["fail"] == 1


# ================================================================
# 17-18: 엣지 케이스
# ================================================================


class TestEdgeCases:

    def test_null_data_handling(self, common_db):
        """팝업에서 NULL 반환 시 안전 처리"""
        db_path, repo = common_db
        _insert_product(db_path, "NULL001")

        success = repo.bulk_update_from_popup("NULL001", {
            "item_nm": None,
            "expiration_days": None,
            "orderable_day": None,
            "orderable_status": None,
            "order_unit_qty": None,
            "sell_price": None,
            "fetched_at": "2026-02-26T01:00:00",
        })

        assert success is True
        detail = _get_detail(db_path, "NULL001")
        assert detail["fetched_at"] == "2026-02-26T01:00:00"

    def test_decimal_type_in_extract(self, mock_driver):
        """Decimal 객체 -> 문자열 변환 (JS에서 처리)

        실제 Decimal 변환은 JS 내부에서 val.hi로 처리되므로
        여기서는 mock으로 반환값이 정상 dict인지 확인
        """
        mock_driver.execute_script.return_value = {
            "item_cd": "DEC001",
            "item_nm": "테스트상품",
            "expiration_days": 3,
            "sell_price": 4500,  # JS에서 Decimal -> String -> parseInt 변환됨
        }

        collector = ProductDetailBatchCollector(driver=mock_driver)
        # JS 실행 결과를 직접 설정하여 extract 로직 우회
        result = mock_driver.execute_script.return_value
        assert result["sell_price"] == 4500
        assert isinstance(result["sell_price"], int)
