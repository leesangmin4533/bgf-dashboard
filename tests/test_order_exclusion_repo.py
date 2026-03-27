"""
OrderExclusionRepository 테스트

발주 제외 사유 저장소 CRUD 검증:
- save_exclusions_batch: 배치 UPSERT
- get_exclusions_by_date: 날짜별 조회
- get_exclusion_summary: 타입별 카운트
- get_exclusions_by_type: 특정 타입 조회
"""

import pytest
from datetime import datetime

from src.infrastructure.database.repos.order_exclusion_repo import (
    OrderExclusionRepository,
    ExclusionType,
)


STORE_ID = "99999"
EVAL_DATE = "2026-02-25"


class _NoCloseConn:
    """close()를 무시하는 SQLite 연결 래퍼"""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._conn, name)

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        return self._conn.commit()

    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)


@pytest.fixture
def excl_repo(in_memory_db):
    """in_memory_db 기반 OrderExclusionRepository"""
    repo = OrderExclusionRepository(store_id=STORE_ID)
    wrapped = _NoCloseConn(in_memory_db)
    repo._get_conn = lambda: wrapped
    return repo


def _make_exclusion(item_cd, exclusion_type, **kwargs):
    """테스트용 제외 사유 dict 생성"""
    exc = {
        "item_cd": item_cd,
        "exclusion_type": exclusion_type,
    }
    exc.update(kwargs)
    return exc


# ─── ExclusionType 상수 검증 ───

class TestExclusionType:
    def test_all_types_defined(self):
        """10개 제외 타입이 모두 정의되어 있어야 함"""
        assert len(ExclusionType.ALL) == 10

    def test_all_list_matches_constants(self):
        """ALL 리스트가 개별 상수와 일치해야 함"""
        expected = [
            ExclusionType.NOT_CARRIED,
            ExclusionType.CUT,
            ExclusionType.AUTO_ORDER,
            ExclusionType.SMART_ORDER,
            ExclusionType.STOPPED,
            ExclusionType.STOCK_SUFFICIENT,
            ExclusionType.FORCE_SUPPRESSED,
            ExclusionType.DESSERT_STOP,
            ExclusionType.BEVERAGE_STOP,
            ExclusionType.SITE_ORDERED,
        ]
        assert ExclusionType.ALL == expected


# ─── save_exclusions_batch 테스트 ───

class TestSaveExclusionsBatch:
    def test_save_single(self, excl_repo):
        """단일 제외 사유 저장"""
        exclusions = [
            _make_exclusion("ITEM001", ExclusionType.NOT_CARRIED, item_nm="테스트상품")
        ]
        saved = excl_repo.save_exclusions_batch(EVAL_DATE, exclusions)
        assert saved == 1

    def test_save_multiple(self, excl_repo):
        """복수 제외 사유 배치 저장"""
        exclusions = [
            _make_exclusion("ITEM001", ExclusionType.NOT_CARRIED),
            _make_exclusion("ITEM002", ExclusionType.CUT),
            _make_exclusion("ITEM003", ExclusionType.AUTO_ORDER),
        ]
        saved = excl_repo.save_exclusions_batch(EVAL_DATE, exclusions)
        assert saved == 3

    def test_save_empty_list(self, excl_repo):
        """빈 리스트 저장 시 0 반환"""
        saved = excl_repo.save_exclusions_batch(EVAL_DATE, [])
        assert saved == 0

    def test_upsert_updates_type(self, excl_repo):
        """같은 item_cd 재저장 시 exclusion_type 업데이트 (UPSERT)"""
        # 1차 저장: NOT_CARRIED
        excl_repo.save_exclusions_batch(EVAL_DATE, [
            _make_exclusion("ITEM001", ExclusionType.NOT_CARRIED)
        ])
        # 2차 저장: CUT으로 변경
        excl_repo.save_exclusions_batch(EVAL_DATE, [
            _make_exclusion("ITEM001", ExclusionType.CUT)
        ])
        rows = excl_repo.get_exclusions_by_date(EVAL_DATE, STORE_ID)
        assert len(rows) == 1
        assert rows[0]["exclusion_type"] == ExclusionType.CUT

    def test_save_with_all_fields(self, excl_repo):
        """모든 필드가 정확히 저장되는지 확인"""
        exclusions = [
            _make_exclusion(
                "ITEM001", ExclusionType.STOCK_SUFFICIENT,
                item_nm="크림진짬뽕컵", mid_cd="006",
                predicted_qty=5, current_stock=11, pending_qty=3,
                detail="need=-9, stock충분"
            )
        ]
        excl_repo.save_exclusions_batch(EVAL_DATE, exclusions)
        rows = excl_repo.get_exclusions_by_date(EVAL_DATE, STORE_ID)
        assert len(rows) == 1
        row = rows[0]
        assert row["item_cd"] == "ITEM001"
        assert row["item_nm"] == "크림진짬뽕컵"
        assert row["mid_cd"] == "006"
        assert row["exclusion_type"] == ExclusionType.STOCK_SUFFICIENT
        assert row["predicted_qty"] == 5
        assert row["current_stock"] == 11
        assert row["pending_qty"] == 3
        assert row["detail"] == "need=-9, stock충분"


# ─── get_exclusions_by_date 테스트 ───

class TestGetExclusionsByDate:
    def test_empty_date(self, excl_repo):
        """데이터 없는 날짜 조회 시 빈 리스트"""
        rows = excl_repo.get_exclusions_by_date("2099-01-01", STORE_ID)
        assert rows == []

    def test_returns_all_for_date(self, excl_repo):
        """특정 날짜의 모든 제외 사유 반환"""
        exclusions = [
            _make_exclusion("ITEM001", ExclusionType.NOT_CARRIED),
            _make_exclusion("ITEM002", ExclusionType.CUT),
            _make_exclusion("ITEM003", ExclusionType.STOPPED),
        ]
        excl_repo.save_exclusions_batch(EVAL_DATE, exclusions)
        rows = excl_repo.get_exclusions_by_date(EVAL_DATE, STORE_ID)
        assert len(rows) == 3


# ─── get_exclusion_summary 테스트 ───

class TestGetExclusionSummary:
    def test_summary_counts(self, excl_repo):
        """타입별 카운트 요약 정확성"""
        exclusions = [
            _make_exclusion("ITEM001", ExclusionType.NOT_CARRIED),
            _make_exclusion("ITEM002", ExclusionType.NOT_CARRIED),
            _make_exclusion("ITEM003", ExclusionType.NOT_CARRIED),
            _make_exclusion("ITEM004", ExclusionType.CUT),
            _make_exclusion("ITEM005", ExclusionType.AUTO_ORDER),
            _make_exclusion("ITEM006", ExclusionType.AUTO_ORDER),
        ]
        excl_repo.save_exclusions_batch(EVAL_DATE, exclusions)
        summary = excl_repo.get_exclusion_summary(EVAL_DATE, STORE_ID)
        assert summary[ExclusionType.NOT_CARRIED] == 3
        assert summary[ExclusionType.CUT] == 1
        assert summary[ExclusionType.AUTO_ORDER] == 2

    def test_summary_empty(self, excl_repo):
        """데이터 없을 때 빈 dict"""
        summary = excl_repo.get_exclusion_summary("2099-01-01", STORE_ID)
        assert summary == {}


# ─── get_exclusions_by_type 테스트 ───

class TestGetExclusionsByType:
    def test_filter_by_type(self, excl_repo):
        """특정 타입만 필터링"""
        exclusions = [
            _make_exclusion("ITEM001", ExclusionType.NOT_CARRIED),
            _make_exclusion("ITEM002", ExclusionType.CUT),
            _make_exclusion("ITEM003", ExclusionType.NOT_CARRIED),
        ]
        excl_repo.save_exclusions_batch(EVAL_DATE, exclusions)

        not_carried = excl_repo.get_exclusions_by_type(
            EVAL_DATE, ExclusionType.NOT_CARRIED, STORE_ID
        )
        assert len(not_carried) == 2
        for row in not_carried:
            assert row["exclusion_type"] == ExclusionType.NOT_CARRIED

        cut = excl_repo.get_exclusions_by_type(
            EVAL_DATE, ExclusionType.CUT, STORE_ID
        )
        assert len(cut) == 1

    def test_filter_nonexistent_type(self, excl_repo):
        """존재하지 않는 타입 조회 시 빈 리스트"""
        exclusions = [
            _make_exclusion("ITEM001", ExclusionType.NOT_CARRIED),
        ]
        excl_repo.save_exclusions_batch(EVAL_DATE, exclusions)

        result = excl_repo.get_exclusions_by_type(
            EVAL_DATE, ExclusionType.SMART_ORDER, STORE_ID
        )
        assert result == []
