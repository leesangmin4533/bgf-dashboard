"""
조회 실패 내성 강화 테스트 (prefetch-fail-tolerance)

핵심 변경:
- 1회 실패 즉시 미취급 마킹 → 3회 연속 실패 시에만 마킹
- query_fail_count 컬럼으로 연속 실패 추적
- unavail_reason ('query_fail' / 'manual') 으로 미취급 사유 구분
- 성공 조회 시 fail_count 초기화
"""

import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

from src.infrastructure.database.repos.inventory_repo import (
    RealtimeInventoryRepository,
    UNAVAILABLE_FAIL_THRESHOLD,
)


STORE_ID = "46513"


class _NoCloseConnection:
    """close()를 무시하는 sqlite3.Connection 래퍼"""
    def __init__(self, conn):
        self._conn = conn
    def close(self):
        pass  # 인메모리 연결 유지
    def __getattr__(self, name):
        return getattr(self._conn, name)


def _create_repo():
    """인메모리 DB로 테스트용 RealtimeInventoryRepository 생성"""
    repo = RealtimeInventoryRepository.__new__(RealtimeInventoryRepository)
    repo.store_id = STORE_ID
    repo.db_path = ":memory:"
    raw_conn = sqlite3.connect(":memory:")
    raw_conn.row_factory = sqlite3.Row
    repo._conn = _NoCloseConnection(raw_conn)

    raw_conn.execute("""
        CREATE TABLE IF NOT EXISTS realtime_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            stock_qty INTEGER DEFAULT 0,
            pending_qty INTEGER DEFAULT 0,
            order_unit_qty INTEGER DEFAULT 1,
            is_available INTEGER DEFAULT 1,
            is_cut_item INTEGER DEFAULT 0,
            orderable_status TEXT,
            queried_at TEXT,
            created_at TEXT,
            query_fail_count INTEGER DEFAULT 0,
            unavail_reason TEXT,
            UNIQUE(store_id, item_cd)
        )
    """)
    raw_conn.commit()

    # _get_conn이 항상 같은 인메모리 연결 반환
    repo._get_conn = lambda: repo._conn
    repo._get_conn_with_common = lambda: repo._conn
    repo._now = lambda: datetime.now().isoformat()
    repo._to_positive_int = lambda x: max(0, int(x or 0))
    repo._to_int = lambda x: int(x or 0)

    return repo


def _get_item(repo, item_cd):
    """DB에서 직접 조회 (is_available, query_fail_count, unavail_reason)"""
    cursor = repo._conn.cursor()
    cursor.execute(
        "SELECT is_available, query_fail_count, unavail_reason FROM realtime_inventory WHERE store_id = ? AND item_cd = ?",
        (STORE_ID, item_cd)
    )
    row = cursor.fetchone()
    if row:
        return {
            "is_available": row[0],
            "query_fail_count": row[1],
            "unavail_reason": row[2],
        }
    return None


class TestIncrementFailCount:
    """increment_fail_count() 단위 테스트"""

    def test_first_fail_stays_available(self):
        """1회 실패 → is_available=1 유지, fail_count=1"""
        repo = _create_repo()
        repo.save("ITEM001", stock_qty=5, store_id=STORE_ID)

        repo.increment_fail_count("ITEM001", store_id=STORE_ID)

        item = _get_item(repo, "ITEM001")
        assert item["is_available"] == 1
        assert item["query_fail_count"] == 1
        assert item["unavail_reason"] is None

    def test_second_fail_stays_available(self):
        """2회 실패 → is_available=1 유지, fail_count=2"""
        repo = _create_repo()
        repo.save("ITEM001", stock_qty=5, store_id=STORE_ID)

        repo.increment_fail_count("ITEM001", store_id=STORE_ID)
        repo.increment_fail_count("ITEM001", store_id=STORE_ID)

        item = _get_item(repo, "ITEM001")
        assert item["is_available"] == 1
        assert item["query_fail_count"] == 2
        assert item["unavail_reason"] is None

    def test_third_fail_marks_unavailable(self):
        """3회 연속 실패 → is_available=0, fail_count=3, unavail_reason='query_fail'"""
        repo = _create_repo()
        repo.save("ITEM001", stock_qty=5, store_id=STORE_ID)

        for _ in range(UNAVAILABLE_FAIL_THRESHOLD):
            repo.increment_fail_count("ITEM001", store_id=STORE_ID)

        item = _get_item(repo, "ITEM001")
        assert item["is_available"] == 0
        assert item["query_fail_count"] == UNAVAILABLE_FAIL_THRESHOLD
        assert item["unavail_reason"] == "query_fail"

    def test_threshold_constant_is_3(self):
        """임계값 상수가 3인지 확인"""
        assert UNAVAILABLE_FAIL_THRESHOLD == 3

    def test_new_item_first_fail(self):
        """DB에 없는 상품 첫 실패 → 새 레코드 생성, is_available=1"""
        repo = _create_repo()

        repo.increment_fail_count("NEW_ITEM", store_id=STORE_ID)

        item = _get_item(repo, "NEW_ITEM")
        assert item is not None
        assert item["is_available"] == 1
        assert item["query_fail_count"] == 1

    def test_fourth_fail_stays_unavailable(self):
        """4회 실패 → is_available=0 유지, fail_count=4"""
        repo = _create_repo()
        repo.save("ITEM001", stock_qty=5, store_id=STORE_ID)

        for _ in range(4):
            repo.increment_fail_count("ITEM001", store_id=STORE_ID)

        item = _get_item(repo, "ITEM001")
        assert item["is_available"] == 0
        assert item["query_fail_count"] == 4


class TestSuccessResetsFailCount:
    """성공 조회 시 fail_count 리셋 테스트"""

    def test_success_resets_fail_count(self):
        """성공 조회(save) → fail_count=0, unavail_reason=NULL"""
        repo = _create_repo()

        # 2회 실패
        repo.increment_fail_count("ITEM001", store_id=STORE_ID)
        repo.increment_fail_count("ITEM001", store_id=STORE_ID)

        item = _get_item(repo, "ITEM001")
        assert item["query_fail_count"] == 2

        # 성공 조회
        repo.save("ITEM001", stock_qty=5, store_id=STORE_ID)

        item = _get_item(repo, "ITEM001")
        assert item["query_fail_count"] == 0
        assert item["unavail_reason"] is None
        assert item["is_available"] == 1

    def test_save_resets_after_threshold(self):
        """3회 실패(미취급 마킹) 후 성공 조회 → is_available=1, fail_count=0"""
        repo = _create_repo()

        # 3회 실패 → 미취급
        for _ in range(UNAVAILABLE_FAIL_THRESHOLD):
            repo.increment_fail_count("ITEM001", store_id=STORE_ID)

        item = _get_item(repo, "ITEM001")
        assert item["is_available"] == 0

        # 성공 조회 → 복구
        repo.save("ITEM001", stock_qty=3, is_available=True, store_id=STORE_ID)

        item = _get_item(repo, "ITEM001")
        assert item["is_available"] == 1
        assert item["query_fail_count"] == 0
        assert item["unavail_reason"] is None

    def test_intermittent_fail_resets(self):
        """실패→성공→실패 → 카운트 리셋 후 재시작"""
        repo = _create_repo()

        # 2회 실패
        repo.increment_fail_count("ITEM001", store_id=STORE_ID)
        repo.increment_fail_count("ITEM001", store_id=STORE_ID)

        # 성공 → 리셋
        repo.save("ITEM001", stock_qty=5, store_id=STORE_ID)

        # 다시 1회 실패
        repo.increment_fail_count("ITEM001", store_id=STORE_ID)

        item = _get_item(repo, "ITEM001")
        assert item["query_fail_count"] == 1  # 리셋 후 재시작
        assert item["is_available"] == 1

    def test_save_bulk_resets_fail_count(self):
        """save_bulk()도 fail_count 리셋"""
        repo = _create_repo()

        # 2회 실패
        repo.increment_fail_count("ITEM001", store_id=STORE_ID)
        repo.increment_fail_count("ITEM001", store_id=STORE_ID)

        # 벌크 저장
        repo.save_bulk([{
            "item_cd": "ITEM001",
            "stock_qty": 10,
            "store_id": STORE_ID,
        }])

        item = _get_item(repo, "ITEM001")
        assert item["query_fail_count"] == 0
        assert item["unavail_reason"] is None


class TestMarkUnavailableManual:
    """mark_unavailable() 명시적 마킹 테스트"""

    def test_mark_unavailable_still_works(self):
        """명시적 mark_unavailable → 즉시 is_available=0, unavail_reason='manual'"""
        repo = _create_repo()
        repo.save("ITEM001", stock_qty=5, store_id=STORE_ID)

        repo.mark_unavailable("ITEM001", store_id=STORE_ID)

        item = _get_item(repo, "ITEM001")
        assert item["is_available"] == 0
        assert item["unavail_reason"] == "manual"

    def test_unavail_reason_distinguishes(self):
        """query_fail vs manual 구분 가능"""
        repo = _create_repo()

        # 상품 A: 조회 실패로 미취급
        repo.save("ITEM_A", stock_qty=5, store_id=STORE_ID)
        for _ in range(UNAVAILABLE_FAIL_THRESHOLD):
            repo.increment_fail_count("ITEM_A", store_id=STORE_ID)

        # 상품 B: 수동 미취급
        repo.save("ITEM_B", stock_qty=5, store_id=STORE_ID)
        repo.mark_unavailable("ITEM_B", store_id=STORE_ID)

        item_a = _get_item(repo, "ITEM_A")
        item_b = _get_item(repo, "ITEM_B")

        assert item_a["unavail_reason"] == "query_fail"
        assert item_b["unavail_reason"] == "manual"
        assert item_a["is_available"] == 0
        assert item_b["is_available"] == 0

    def test_mark_unavailable_new_item(self):
        """DB에 없는 상품도 즉시 미취급 마킹"""
        repo = _create_repo()
        repo.mark_unavailable("NEW_ITEM", store_id=STORE_ID)

        item = _get_item(repo, "NEW_ITEM")
        assert item["is_available"] == 0
        assert item["unavail_reason"] == "manual"


class TestOrderPrepCollectorIntegration:
    """order_prep_collector.py에서 increment_fail_count 호출 확인"""

    def test_collector_calls_increment_on_failure(self):
        """조회 실패 시 mark_unavailable 대신 increment_fail_count 호출"""
        from src.collectors.order_prep_collector import OrderPrepCollector

        with patch.object(OrderPrepCollector, '__init__', return_value=None):
            collector = OrderPrepCollector.__new__(OrderPrepCollector)
            collector._save_to_db = True
            collector._repo = MagicMock()
            collector.store_id = STORE_ID
            # driver.execute_script가 예외를 발생시키도록 설정
            mock_driver = MagicMock()
            mock_driver.execute_script.side_effect = Exception("타임아웃")
            collector.driver = mock_driver

            with patch('src.collectors.order_prep_collector.close_all_popups', return_value=0):
                result = collector.collect_for_item("TEST_ITEM")

            # increment_fail_count 호출 확인
            collector._repo.increment_fail_count.assert_called_once_with(
                "TEST_ITEM", store_id=STORE_ID
            )
            # mark_unavailable은 호출되지 않아야 함
            collector._repo.mark_unavailable.assert_not_called()
            assert result["success"] is False
