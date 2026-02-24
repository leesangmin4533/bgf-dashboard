"""
StoppedItemRepository + filter_stopped_items 테스트
"""

import sqlite3
import pytest
from unittest.mock import patch, MagicMock

from src.infrastructure.database.repos.stopped_item_repo import StoppedItemRepository
from src.domain.order.order_filter import filter_stopped_items


# ── 헬퍼 ──────────────────────────────────────────────

@pytest.fixture
def stopped_db(tmp_path):
    """stopped_items 테이블이 있는 임시 DB"""
    db_path = tmp_path / "common.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE stopped_items (
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            stop_reason TEXT,
            first_detected_at TEXT NOT NULL,
            last_detected_at TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            UNIQUE(item_cd)
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def repo(stopped_db):
    """StoppedItemRepository with patched DB path"""
    r = StoppedItemRepository()
    r._db_path = str(stopped_db)
    return r


# ── StoppedItemRepository CRUD 테스트 ─────────────────

class TestStoppedItemRepositoryCRUD:
    """기본 CRUD 동작 테스트"""

    def test_upsert_new_item(self, repo):
        """신규 발주정지 상품 등록"""
        assert repo.upsert("ITEM001", "테스트상품", "일시공급불가")
        items = repo.get_active_item_codes()
        assert "ITEM001" in items

    def test_upsert_updates_existing(self, repo):
        """기존 상품 정보 갱신"""
        repo.upsert("ITEM001", "테스트상품", "일시공급불가")
        repo.upsert("ITEM001", "테스트상품2", "단종")

        active = repo.get_all_active()
        assert len(active) == 1
        assert active[0]["stop_reason"] == "단종"
        assert active[0]["item_nm"] == "테스트상품2"

    def test_deactivate(self, repo):
        """발주정지 해제"""
        repo.upsert("ITEM001", "테스트상품", "일시공급불가")
        assert repo.get_count() == 1

        repo.deactivate("ITEM001")
        assert repo.get_count() == 0
        assert "ITEM001" not in repo.get_active_item_codes()

    def test_deactivate_nonexistent(self, repo):
        """존재하지 않는 상품 해제 시 오류 없음"""
        assert repo.deactivate("NONEXIST")

    def test_get_active_item_codes_empty(self, repo):
        """빈 DB에서 조회"""
        assert repo.get_active_item_codes() == set()

    def test_get_count(self, repo):
        """활성 건수 확인"""
        repo.upsert("ITEM001", "상품1", "일시공급불가")
        repo.upsert("ITEM002", "상품2", "단종")
        repo.upsert("ITEM003", "상품3", "발주불가")
        assert repo.get_count() == 3

        repo.deactivate("ITEM002")
        assert repo.get_count() == 2

    def test_reactivate_deactivated(self, repo):
        """해제된 상품 재등록 시 다시 활성화"""
        repo.upsert("ITEM001", "상품1", "일시공급불가")
        repo.deactivate("ITEM001")
        assert repo.get_count() == 0

        repo.upsert("ITEM001", "상품1", "일시공급불가(재)")
        assert repo.get_count() == 1
        active = repo.get_all_active()
        assert active[0]["stop_reason"] == "일시공급불가(재)"


# ── sync_from_fail_reasons 테스트 ─────────────────────

class TestSyncFromFailReasons:
    """Phase 3 결과 동기화 테스트"""

    def test_sync_activates_stopped_items(self, repo):
        """stop_reason이 있는 상품은 활성화"""
        results = [
            {"item_cd": "A001", "item_nm": "상품A", "stop_reason": "일시공급불가"},
            {"item_cd": "A002", "item_nm": "상품B", "stop_reason": "단종"},
        ]
        sync = repo.sync_from_fail_reasons(results)
        assert sync["activated"] == 2
        assert repo.get_count() == 2

    def test_sync_deactivates_normal_items(self, repo):
        """stop_reason이 비어있는 상품은 비활성화"""
        repo.upsert("A001", "상품A", "일시공급불가")
        assert repo.get_count() == 1

        results = [
            {"item_cd": "A001", "item_nm": "상품A", "stop_reason": ""},
        ]
        repo.sync_from_fail_reasons(results)
        assert repo.get_count() == 0

    def test_sync_ignores_unknown(self, repo):
        """알수없음은 활성화하지 않음"""
        results = [
            {"item_cd": "A001", "item_nm": "상품A", "stop_reason": "알수없음"},
        ]
        sync = repo.sync_from_fail_reasons(results)
        assert sync["activated"] == 0
        assert repo.get_count() == 0

    def test_sync_deactivates_previously_stopped_unknown(self, repo):
        """기존 정지 상품이 알수없음으로 오면 해제"""
        repo.upsert("A001", "상품A", "일시공급불가")
        assert repo.get_count() == 1

        results = [
            {"item_cd": "A001", "item_nm": "상품A", "stop_reason": "알수없음"},
        ]
        repo.sync_from_fail_reasons(results)
        assert repo.get_count() == 0

    def test_sync_mixed_results(self, repo):
        """혼합: 일부 활성, 일부 해제"""
        repo.upsert("OLD1", "기존1", "단종")

        results = [
            {"item_cd": "NEW1", "item_nm": "신규1", "stop_reason": "일시공급불가"},
            {"item_cd": "OLD1", "item_nm": "기존1", "stop_reason": ""},
            {"item_cd": "NEW2", "item_nm": "신규2", "stop_reason": None},
        ]
        sync = repo.sync_from_fail_reasons(results)
        assert sync["activated"] == 1
        assert "NEW1" in repo.get_active_item_codes()
        assert "OLD1" not in repo.get_active_item_codes()

    def test_sync_empty_results(self, repo):
        """빈 결과 처리"""
        sync = repo.sync_from_fail_reasons([])
        assert sync["activated"] == 0
        assert sync["deactivated"] == 0

    def test_sync_skips_no_item_cd(self, repo):
        """item_cd가 없는 결과는 건너뜀"""
        results = [
            {"item_nm": "상품A", "stop_reason": "일시공급불가"},
        ]
        sync = repo.sync_from_fail_reasons(results)
        assert sync["activated"] == 0


# ── filter_stopped_items 순수 함수 테스트 ─────────────

class TestFilterStoppedItems:
    """order_filter.py의 filter_stopped_items 테스트"""

    def test_filter_removes_stopped(self):
        """발주정지 상품 제외"""
        order_list = [
            {"item_cd": "A001", "order_qty": 5},
            {"item_cd": "A002", "order_qty": 3},
            {"item_cd": "A003", "order_qty": 2},
        ]
        stopped = {"A002"}
        result = filter_stopped_items(order_list, stopped)
        assert len(result) == 2
        assert all(r["item_cd"] != "A002" for r in result)

    def test_filter_empty_stopped(self):
        """빈 set이면 필터 안 함"""
        order_list = [{"item_cd": "A001", "order_qty": 5}]
        result = filter_stopped_items(order_list, set())
        assert len(result) == 1

    def test_filter_all_stopped(self):
        """전부 정지면 빈 리스트"""
        order_list = [
            {"item_cd": "A001", "order_qty": 5},
            {"item_cd": "A002", "order_qty": 3},
        ]
        stopped = {"A001", "A002"}
        result = filter_stopped_items(order_list, stopped)
        assert len(result) == 0

    def test_filter_no_match(self):
        """매칭 없으면 원본 유지"""
        order_list = [{"item_cd": "A001", "order_qty": 5}]
        stopped = {"X001", "X002"}
        result = filter_stopped_items(order_list, stopped)
        assert len(result) == 1
