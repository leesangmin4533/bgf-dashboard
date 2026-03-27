"""
site 기발주 상품 auto 발주 스킵 필터 테스트

BGF saveOrd는 UPSERT이므로 auto가 제출하면 site 수량을 덮어씀.
사용자가 의도적으로 입력한 수량이 AI 예측값으로 교체되는 것을 방지.

테스트 대상:
1. OrderTrackingRepository.get_site_ordered_items()
2. OrderFilter.exclude_site_ordered_items()
"""

import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

from src.infrastructure.database.repos.order_tracking_repo import OrderTrackingRepository
from src.infrastructure.database.repos.order_exclusion_repo import ExclusionType
from src.order.order_filter import OrderFilter

MOCK_TARGET = (
    "src.infrastructure.database.repos.order_tracking_repo"
    ".OrderTrackingRepository.get_site_ordered_items"
)


# ─── Fixtures ───

@pytest.fixture
def store_db(tmp_path):
    """테스트용 매장 DB 생성"""
    db_path = tmp_path / "test_store.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE order_tracking (
            id INTEGER PRIMARY KEY,
            store_id TEXT,
            order_date TEXT,
            item_cd TEXT,
            item_nm TEXT,
            mid_cd TEXT,
            delivery_type TEXT,
            order_qty INTEGER DEFAULT 0,
            remaining_qty INTEGER DEFAULT 0,
            status TEXT DEFAULT 'ordered',
            alert_sent INTEGER DEFAULT 0,
            order_source TEXT DEFAULT 'site',
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(store_id, order_date, item_cd)
        )
    """)
    conn.commit()
    return conn, str(db_path)


def _insert_order(conn, order_date, item_cd, order_source, order_qty,
                   item_nm="테스트상품", mid_cd="049", store_id="99999"):
    """테스트 발주 데이터 삽입"""
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT INTO order_tracking
            (store_id, order_date, item_cd, item_nm, mid_cd,
             delivery_type, order_qty, order_source, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, '1차', ?, ?, ?, ?)
    """, (store_id, order_date, item_cd, item_nm, mid_cd,
          order_qty, order_source, now, now))
    conn.commit()


def _make_order_item(item_cd, final_order_qty=3, item_nm="테스트", mid_cd="049", **kwargs):
    """auto 발주 후보 dict 생성"""
    item = {
        "item_cd": item_cd,
        "item_nm": item_nm,
        "mid_cd": mid_cd,
        "final_order_qty": final_order_qty,
        "order_qty": final_order_qty,
    }
    item.update(kwargs)
    return item


# ─── ExclusionType 테스트 ───

class TestExclusionTypeSiteOrdered:
    def test_site_ordered_constant_defined(self):
        """SITE_ORDERED 상수가 정의되어 있어야 함"""
        assert ExclusionType.SITE_ORDERED == "SITE_ORDERED"

    def test_site_ordered_in_all_list(self):
        """SITE_ORDERED가 ALL 리스트에 포함되어야 함"""
        assert ExclusionType.SITE_ORDERED in ExclusionType.ALL


# ─── OrderTrackingRepository.get_site_ordered_items 테스트 ───

class TestGetSiteOrderedItems:
    def test_returns_site_orders_only(self, store_db):
        """site 발주만 반환하고 auto/manual은 제외"""
        conn, db_path = store_db
        _insert_order(conn, "2026-03-17", "ITEM_A", "site", 10)
        _insert_order(conn, "2026-03-17", "ITEM_B", "auto", 5)
        _insert_order(conn, "2026-03-17", "ITEM_C", "manual", 3)
        _insert_order(conn, "2026-03-17", "ITEM_D", "site", 7)

        repo = OrderTrackingRepository(store_id="99999")
        with patch.object(repo, '_get_conn', return_value=conn):
            result = repo.get_site_ordered_items("2026-03-17")

        assert result == {"ITEM_A": 10, "ITEM_D": 7}
        assert "ITEM_B" not in result
        assert "ITEM_C" not in result

    def test_filters_by_date(self, store_db):
        """지정 날짜만 반환"""
        conn, db_path = store_db
        _insert_order(conn, "2026-03-17", "ITEM_A", "site", 10)
        _insert_order(conn, "2026-03-16", "ITEM_B", "site", 5)

        repo = OrderTrackingRepository(store_id="99999")
        with patch.object(repo, '_get_conn', return_value=conn):
            result = repo.get_site_ordered_items("2026-03-17")

        assert result == {"ITEM_A": 10}

    def test_empty_when_no_site_orders(self, store_db):
        """site 발주가 없으면 빈 dict 반환"""
        conn, db_path = store_db
        _insert_order(conn, "2026-03-17", "ITEM_A", "auto", 5)

        repo = OrderTrackingRepository(store_id="99999")
        with patch.object(repo, '_get_conn', return_value=conn):
            result = repo.get_site_ordered_items("2026-03-17")

        assert result == {}

    def test_returns_empty_on_db_error(self):
        """DB 에러 시 빈 dict 반환 (발주 흐름 중단 방지)"""
        repo = OrderTrackingRepository(store_id="99999")
        # _get_conn 자체를 실패시키되, 에러 핸들링은 메서드 내부 except에서 처리
        broken_conn = MagicMock()
        broken_conn.cursor.return_value.execute.side_effect = Exception("DB error")
        with patch.object(repo, '_get_conn', return_value=broken_conn):
            result = repo.get_site_ordered_items("2026-03-17")

        assert result == {}


# ─── OrderFilter.exclude_site_ordered_items 테스트 ───

class TestExcludeSiteOrderedItems:
    def test_excludes_site_ordered_items(self):
        """site 기발주 상품이 auto 목록에서 제외됨"""
        filt = OrderFilter(store_id="99999")
        order_list = [
            _make_order_item("ITEM_A", 3),
            _make_order_item("ITEM_B", 5),
            _make_order_item("ITEM_C", 2),
        ]
        exclusion_records = []

        with patch(MOCK_TARGET, return_value={"ITEM_A": 10, "ITEM_C": 4}):
            result = filt.exclude_site_ordered_items(
                order_list, "2026-03-17", exclusion_records
            )

        assert len(result) == 1
        assert result[0]["item_cd"] == "ITEM_B"

    def test_exclusion_records_populated(self):
        """제외된 상품의 exclusion_records가 올바르게 기록됨"""
        filt = OrderFilter(store_id="99999")
        order_list = [
            _make_order_item("ITEM_A", 3, item_nm="한양마시다봉"),
        ]
        exclusion_records = []

        with patch(MOCK_TARGET, return_value={"ITEM_A": 10}):
            filt.exclude_site_ordered_items(
                order_list, "2026-03-17", exclusion_records
            )

        assert len(exclusion_records) == 1
        rec = exclusion_records[0]
        assert rec["item_cd"] == "ITEM_A"
        assert rec["exclusion_type"] == ExclusionType.SITE_ORDERED
        assert "site_qty=10" in rec["detail"]
        assert rec["predicted_qty"] == 3

    def test_no_site_orders_returns_all(self):
        """site 발주가 없으면 전체 목록 그대로 반환"""
        filt = OrderFilter(store_id="99999")
        order_list = [
            _make_order_item("ITEM_A", 3),
            _make_order_item("ITEM_B", 5),
        ]
        exclusion_records = []

        with patch(MOCK_TARGET, return_value={}):
            result = filt.exclude_site_ordered_items(
                order_list, "2026-03-17", exclusion_records
            )

        assert len(result) == 2
        assert len(exclusion_records) == 0

    def test_cancel_smart_bypasses_filter(self):
        """cancel_smart 항목은 site 필터 면제"""
        filt = OrderFilter(store_id="99999")
        order_list = [
            _make_order_item("ITEM_A", 0, cancel_smart=True),
            _make_order_item("ITEM_B", 3),
        ]
        exclusion_records = []

        with patch(MOCK_TARGET, return_value={"ITEM_A": 5, "ITEM_B": 10}):
            result = filt.exclude_site_ordered_items(
                order_list, "2026-03-17", exclusion_records
            )

        # cancel_smart=True인 ITEM_A는 통과, ITEM_B는 제외
        assert len(result) == 1
        assert result[0]["item_cd"] == "ITEM_A"
        assert result[0].get("cancel_smart") is True

    def test_db_error_returns_all(self):
        """DB 에러 시 전체 목록 그대로 반환 (발주 흐름 중단 방지)"""
        filt = OrderFilter(store_id="99999")
        order_list = [
            _make_order_item("ITEM_A", 3),
        ]
        exclusion_records = []

        with patch(MOCK_TARGET, side_effect=Exception("DB error")):
            result = filt.exclude_site_ordered_items(
                order_list, "2026-03-17", exclusion_records
            )

        assert len(result) == 1
        assert result[0]["item_cd"] == "ITEM_A"

    def test_no_overlap_returns_all(self):
        """site 발주와 auto 후보가 겹치지 않으면 전부 유지"""
        filt = OrderFilter(store_id="99999")
        order_list = [
            _make_order_item("ITEM_X", 3),
            _make_order_item("ITEM_Y", 5),
        ]
        exclusion_records = []

        with patch(MOCK_TARGET, return_value={"ITEM_A": 10, "ITEM_B": 7}):
            result = filt.exclude_site_ordered_items(
                order_list, "2026-03-17", exclusion_records
            )

        assert len(result) == 2
        assert len(exclusion_records) == 0

    def test_mixed_overlap_partial_exclude(self):
        """일부만 겹칠 때 겹치는 것만 제외"""
        filt = OrderFilter(store_id="99999")
        order_list = [
            _make_order_item("ITEM_A", 3),
            _make_order_item("ITEM_B", 5),
            _make_order_item("ITEM_C", 2),
            _make_order_item("ITEM_D", 4),
        ]
        exclusion_records = []

        with patch(MOCK_TARGET, return_value={"ITEM_A": 10, "ITEM_C": 4}):
            result = filt.exclude_site_ordered_items(
                order_list, "2026-03-17", exclusion_records
            )

        assert len(result) == 2
        assert [r["item_cd"] for r in result] == ["ITEM_B", "ITEM_D"]
        assert len(exclusion_records) == 2

    def test_exclusion_record_detail_format(self):
        """exclusion_records의 detail에 site_qty가 명시됨"""
        filt = OrderFilter(store_id="99999")
        order_list = [
            _make_order_item("ITEM_A", 3, item_nm="한양마시다봉20g", mid_cd="072"),
        ]
        exclusion_records = []

        with patch(MOCK_TARGET, return_value={"ITEM_A": 10}):
            filt.exclude_site_ordered_items(
                order_list, "2026-03-17", exclusion_records
            )

        rec = exclusion_records[0]
        assert rec["exclusion_type"] == "SITE_ORDERED"
        assert rec["mid_cd"] == "072"
        assert rec["item_nm"] == "한양마시다봉20g"
        assert "site_qty=10" in rec["detail"]
