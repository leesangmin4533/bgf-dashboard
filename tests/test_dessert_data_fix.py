"""dessert-data-fix: 디저트 대시보드 데이터 정합성 수정 테스트

Fix 1: get_weekly_trend() strftime '%%W' → '%W'
Fix 2: get_pending_stop_count() MAX(id) → MAX(judgment_period_end)
Fix 3: batch_update_operator_action() MAX(id) → MAX(judgment_period_end)

역순 ID 데이터로 MAX(id) vs MAX(judgment_period_end) 차이를 재현하여 검증.
"""

import sqlite3
from datetime import datetime

import pytest

from src.infrastructure.database.repos.dessert_decision_repo import (
    DessertDecisionRepository,
)


# ============================================================================
# Fixture
# ============================================================================

def _create_table(conn: sqlite3.Connection):
    """dessert_decisions 테이블 생성"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dessert_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            mid_cd TEXT DEFAULT '014',
            dessert_category TEXT NOT NULL,
            expiration_days INTEGER,
            small_nm TEXT,
            lifecycle_phase TEXT NOT NULL,
            first_receiving_date TEXT,
            first_receiving_source TEXT,
            weeks_since_intro INTEGER DEFAULT 0,
            judgment_period_start TEXT NOT NULL,
            judgment_period_end TEXT NOT NULL,
            total_order_qty INTEGER DEFAULT 0,
            total_sale_qty INTEGER DEFAULT 0,
            total_disuse_qty INTEGER DEFAULT 0,
            sale_amount INTEGER DEFAULT 0,
            disuse_amount INTEGER DEFAULT 0,
            sell_price INTEGER DEFAULT 0,
            sale_rate REAL DEFAULT 0.0,
            category_avg_sale_qty REAL DEFAULT 0.0,
            prev_period_sale_qty INTEGER DEFAULT 0,
            sale_trend_pct REAL DEFAULT 0.0,
            consecutive_low_weeks INTEGER DEFAULT 0,
            consecutive_zero_months INTEGER DEFAULT 0,
            decision TEXT NOT NULL,
            decision_reason TEXT,
            is_rapid_decline_warning INTEGER DEFAULT 0,
            operator_action TEXT,
            operator_note TEXT,
            action_taken_at TEXT,
            judgment_cycle TEXT NOT NULL,
            category_type TEXT DEFAULT 'dessert',
            created_at TEXT NOT NULL,
            UNIQUE(store_id, item_cd, judgment_period_end)
        )
    """)
    conn.commit()


def _insert_record(conn, item_cd, period_end, decision, operator_action=None,
                    store_id="99999", category="A"):
    """테스트 레코드 삽입 헬퍼"""
    conn.execute("""
        INSERT INTO dessert_decisions (
            store_id, item_cd, item_nm, mid_cd, dessert_category,
            expiration_days, small_nm, lifecycle_phase,
            first_receiving_date, first_receiving_source, weeks_since_intro,
            judgment_period_start, judgment_period_end,
            total_order_qty, total_sale_qty, total_disuse_qty,
            sale_amount, disuse_amount, sell_price, sale_rate,
            category_avg_sale_qty, prev_period_sale_qty, sale_trend_pct,
            consecutive_low_weeks, consecutive_zero_months,
            decision, decision_reason, is_rapid_decline_warning,
            operator_action, judgment_cycle, created_at
        ) VALUES (
            ?, ?, ?, '014', ?,
            3, '냉장디저트', 'established',
            '2025-12-01', 'daily_sales_sold', 13,
            '2026-02-25', ?,
            20, 15, 5,
            15000, 5000, 1000, 0.75,
            20.0, 12, 0.0,
            0, 0,
            ?, 'test', 0,
            ?, 'weekly', ?
        )
    """, (store_id, item_cd, f"테스트_{item_cd}", category,
          period_end, decision, operator_action,
          datetime.now().isoformat()))
    conn.commit()


@pytest.fixture
def repo_with_data(tmp_path):
    """역순 ID 데이터로 MAX(id) vs MAX(judgment_period_end) 차이를 재현.

    삽입 순서 (ID 순):
      ID 1: A001, 2026-03-04 (최신), STOP_RECOMMEND  ← 가장 낮은 ID = 최신 기간
      ID 2: A001, 2026-02-25 (과거), KEEP             ← 높은 ID = 과거 기간
      ID 3: A001, 2026-02-18 (과거), KEEP             ← 가장 높은 ID = 가장 과거

    이 구조에서:
      MAX(id) = 3 → 2026-02-18 (과거) KEEP ← 버그
      MAX(judgment_period_end) = '2026-03-04' (최신) STOP_RECOMMEND ← 정상

      ID 4: A002, 2026-03-04 (최신), KEEP
      ID 5: A002, 2026-02-25 (과거), STOP_RECOMMEND

      ID 6: A003, 2026-03-04 (최신), WATCH
    """
    db_path = tmp_path / "test_fix.db"
    conn = sqlite3.connect(str(db_path))
    _create_table(conn)

    # A001: 최신=STOP_RECOMMEND (낮은 ID), 과거=KEEP (높은 ID)
    _insert_record(conn, "A001", "2026-03-04", "STOP_RECOMMEND")  # id=1
    _insert_record(conn, "A001", "2026-02-25", "KEEP")            # id=2
    _insert_record(conn, "A001", "2026-02-18", "KEEP")            # id=3

    # A002: 최신=KEEP, 과거=STOP_RECOMMEND
    _insert_record(conn, "A002", "2026-03-04", "KEEP")            # id=4
    _insert_record(conn, "A002", "2026-02-25", "STOP_RECOMMEND")  # id=5

    # A003: 최신=WATCH (단일 레코드)
    _insert_record(conn, "A003", "2026-03-04", "WATCH")           # id=6

    conn.close()

    repo = DessertDecisionRepository(store_id="99999")
    repo._get_conn = lambda: sqlite3.connect(str(db_path))
    repo._get_conn_rr = repo._get_conn
    return repo


@pytest.fixture
def repo_empty(tmp_path):
    """데이터 없는 빈 DB"""
    db_path = tmp_path / "test_empty.db"
    conn = sqlite3.connect(str(db_path))
    _create_table(conn)
    conn.close()

    repo = DessertDecisionRepository(store_id="99999")
    repo._get_conn = lambda: sqlite3.connect(str(db_path))
    repo._get_conn_rr = repo._get_conn
    return repo


@pytest.fixture
def repo_multi_week(tmp_path):
    """복수 주차 데이터 (주간 추이 테스트용)"""
    db_path = tmp_path / "test_trend.db"
    conn = sqlite3.connect(str(db_path))
    _create_table(conn)

    # W06: 2026-02-11 (수요일)
    _insert_record(conn, "B001", "2026-02-11", "KEEP")
    _insert_record(conn, "B002", "2026-02-11", "STOP_RECOMMEND")

    # W08: 2026-02-25 (수요일)
    _insert_record(conn, "B001", "2026-02-25", "KEEP")
    _insert_record(conn, "B002", "2026-02-25", "WATCH")
    _insert_record(conn, "B003", "2026-02-25", "KEEP")

    # W09: 2026-03-04 (수요일)
    _insert_record(conn, "B001", "2026-03-04", "KEEP")
    _insert_record(conn, "B002", "2026-03-04", "STOP_RECOMMEND")
    _insert_record(conn, "B003", "2026-03-04", "WATCH")

    conn.close()

    repo = DessertDecisionRepository(store_id="99999")
    repo._get_conn = lambda: sqlite3.connect(str(db_path))
    repo._get_conn_rr = repo._get_conn
    return repo


# ============================================================================
# Test 1: get_weekly_trend — 올바른 주차 분리
# ============================================================================

class TestWeeklyTrend:

    def test_weekly_trend_correct_week_numbers(self, repo_multi_week):
        """서로 다른 주차 데이터가 올바른 W값으로 분리 반환"""
        result = repo_multi_week.get_weekly_trend(store_id="99999", weeks=8)

        # 3개 주차 존재해야 함
        assert len(result) >= 2, f"Expected >=2 weeks, got {len(result)}: {result}"

        weeks = [r["week"] for r in result]
        # W6, W8, W9 또는 비슷한 값 — 중요한 건 W0 아닌 유의미한 값
        for w in weeks:
            assert w != "W0", f"모든 데이터가 W0으로 집계됨 (strftime %%W 버그)"

    def test_weekly_trend_not_all_w0(self, repo_multi_week):
        """복수 주차 존재 시 W0 단일 데이터 포인트가 아님"""
        result = repo_multi_week.get_weekly_trend(store_id="99999", weeks=8)
        assert len(result) > 1, "모든 데이터가 단일 포인트로 집계됨"

    def test_weekly_trend_empty_returns_empty_list(self, repo_empty):
        """데이터 없으면 빈 리스트"""
        result = repo_empty.get_weekly_trend(store_id="99999", weeks=8)
        assert result == []


# ============================================================================
# Test 2: get_pending_stop_count — 역순 ID 대응
# ============================================================================

class TestPendingStopCount:

    def test_pending_stop_count_with_reversed_ids(self, repo_with_data):
        """ID 역순에도 최신 judgment_period_end의 STOP_RECOMMEND 카운트.

        A001: 최신(03-04) = STOP_RECOMMEND → 카운트
        A002: 최신(03-04) = KEEP → 미카운트
        A003: 최신(03-04) = WATCH → 미카운트
        → 기대값 = 1
        """
        count = repo_with_data.get_pending_stop_count(store_id="99999")
        assert count == 1, (
            f"Expected 1 (A001 latest=STOP_RECOMMEND), got {count}. "
            f"MAX(id) 버그 시 A001 과거 KEEP이 선택되어 0 반환"
        )

    def test_pending_stop_count_zero_when_no_stops(self, repo_empty):
        """STOP_RECOMMEND 없으면 0"""
        count = repo_empty.get_pending_stop_count(store_id="99999")
        assert count == 0

    def test_pending_stop_count_excludes_actioned(self, tmp_path):
        """operator_action 있으면 제외"""
        db_path = tmp_path / "test_actioned.db"
        conn = sqlite3.connect(str(db_path))
        _create_table(conn)

        # STOP_RECOMMEND이지만 이미 처리됨
        _insert_record(conn, "X001", "2026-03-04", "STOP_RECOMMEND",
                        operator_action="CONFIRMED_STOP")
        # STOP_RECOMMEND 미처리
        _insert_record(conn, "X002", "2026-03-04", "STOP_RECOMMEND")
        conn.close()

        repo = DessertDecisionRepository(store_id="99999")
        repo._get_conn = lambda: sqlite3.connect(str(db_path))
        repo._get_conn_rr = repo._get_conn

        count = repo.get_pending_stop_count(store_id="99999")
        assert count == 1, f"Expected 1 (X001 actioned excluded), got {count}"


# ============================================================================
# Test 3: batch_update_operator_action — 최신 기간 대상
# ============================================================================

class TestBatchUpdateOperatorAction:

    def test_batch_update_targets_latest_period(self, tmp_path):
        """최신 judgment_period_end 레코드 대상 업데이트 확인.

        A001: 최신(03-04)=STOP_RECOMMEND(id=1) 이 대상이어야 함.
        MAX(id)=3(과거 KEEP)이 아닌 MAX(judgment_period_end)='03-04'(STOP_RECOMMEND).
        """
        db_path = tmp_path / "test_batch.db"
        conn = sqlite3.connect(str(db_path))
        _create_table(conn)

        # A001: 최신=STOP_RECOMMEND(id=1), 과거=KEEP(id=2,3)
        _insert_record(conn, "A001", "2026-03-04", "STOP_RECOMMEND")  # id=1
        _insert_record(conn, "A001", "2026-02-25", "KEEP")            # id=2
        _insert_record(conn, "A001", "2026-02-18", "KEEP")            # id=3
        conn.close()

        repo = DessertDecisionRepository(store_id="99999")
        repo._get_conn = lambda: sqlite3.connect(str(db_path))
        repo._get_conn_rr = repo._get_conn

        results = repo.batch_update_operator_action(
            item_cds=["A001"],
            action="CONFIRMED_STOP",
            store_id="99999",
        )

        assert len(results) == 1, (
            f"Expected 1 update (A001 latest STOP_RECOMMEND), got {len(results)}. "
            f"MAX(id) 버그 시 id=3(과거 KEEP)이 선택되어 0 반환"
        )
        assert results[0]["item_cd"] == "A001"
        assert results[0]["action"] == "CONFIRMED_STOP"

        # DB 검증: id=1(최신)이 업데이트되었는지 확인
        verify_conn = sqlite3.connect(str(db_path))
        cursor = verify_conn.execute(
            "SELECT id, operator_action FROM dessert_decisions WHERE item_cd='A001' AND operator_action IS NOT NULL"
        )
        rows = cursor.fetchall()
        verify_conn.close()

        assert len(rows) == 1
        updated_id, action = rows[0]
        assert updated_id == 1, f"Expected id=1 (latest period), got id={updated_id}"
        assert action == "CONFIRMED_STOP"

    def test_batch_update_ignores_old_period_stops(self, tmp_path):
        """과거 기간의 STOP_RECOMMEND는 무시.

        A002: 최신(03-04)=KEEP, 과거(02-25)=STOP_RECOMMEND
        → 최신이 KEEP이므로 업데이트 대상 아님.
        """
        db_path = tmp_path / "test_batch_old.db"
        conn = sqlite3.connect(str(db_path))
        _create_table(conn)

        _insert_record(conn, "A002", "2026-03-04", "KEEP")
        _insert_record(conn, "A002", "2026-02-25", "STOP_RECOMMEND")
        conn.close()

        repo = DessertDecisionRepository(store_id="99999")
        repo._get_conn = lambda: sqlite3.connect(str(db_path))
        repo._get_conn_rr = repo._get_conn

        results = repo.batch_update_operator_action(
            item_cds=["A002"],
            action="CONFIRMED_STOP",
            store_id="99999",
        )

        assert len(results) == 0, (
            f"Expected 0 (A002 latest=KEEP), got {len(results)}. "
            f"과거 STOP_RECOMMEND가 잘못 선택됨"
        )

    def test_batch_update_no_items_returns_empty(self, repo_with_data):
        """빈 item_cds 리스트 → 빈 결과"""
        results = repo_with_data.batch_update_operator_action(
            item_cds=[],
            action="CONFIRMED_STOP",
            store_id="99999",
        )
        assert results == []


# ============================================================================
# Test 4: 교차 검증 — summary vs pending count
# ============================================================================

class TestCrossValidation:

    def test_summary_and_pending_count_consistent(self, repo_with_data):
        """get_decision_summary().current.STOP_RECOMMEND == get_pending_stop_count() (미처리 시).

        모든 STOP_RECOMMEND가 미처리(operator_action=NULL)인 상태에서
        summary의 STOP_RECOMMEND 수와 pending count가 일치해야 함.
        """
        summary = repo_with_data.get_decision_summary(store_id="99999")
        pending = repo_with_data.get_pending_stop_count(store_id="99999")

        stop_count_in_summary = summary["current"].get("STOP_RECOMMEND", 0)

        assert stop_count_in_summary == pending, (
            f"Summary STOP_RECOMMEND={stop_count_in_summary}, "
            f"pending_stop_count={pending}. "
            f"MAX(id) vs MAX(judgment_period_end) 불일치"
        )


# ============================================================================
# Test 5: 30일 미판매 자동 CONFIRMED_STOP
# ============================================================================

class TestAutoConfirmZeroSales:

    @pytest.fixture
    def service_with_data(self, tmp_path):
        """daily_sales + dessert_decisions 를 가진 서비스 fixture"""
        from src.application.services.dessert_decision_service import (
            DessertDecisionService,
        )

        db_path = tmp_path / "test_auto.db"
        conn = sqlite3.connect(str(db_path))
        _create_table(conn)

        # daily_sales 테이블 생성
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collected_at TEXT NOT NULL,
                sales_date TEXT NOT NULL,
                item_cd TEXT NOT NULL,
                mid_cd TEXT NOT NULL,
                sale_qty INTEGER DEFAULT 0,
                ord_qty INTEGER DEFAULT 0,
                buy_qty INTEGER DEFAULT 0,
                disuse_qty INTEGER DEFAULT 0,
                stock_qty INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                promo_type TEXT DEFAULT '',
                store_id TEXT,
                UNIQUE(sales_date, item_cd)
            )
        """)

        # S001: STOP_RECOMMEND + 30일 내 판매 없음 → 자동확인 대상
        _insert_record(conn, "S001", "2026-03-04", "STOP_RECOMMEND")

        # S002: STOP_RECOMMEND + 30일 내 판매 있음 → 자동확인 안 됨
        _insert_record(conn, "S002", "2026-03-04", "STOP_RECOMMEND")
        conn.execute("""
            INSERT INTO daily_sales (collected_at, sales_date, item_cd, mid_cd, sale_qty, created_at)
            VALUES ('2026-03-03', '2026-03-03', 'S002', '014', 5, '2026-03-03')
        """)

        # S003: KEEP → 자동확인 대상 아님
        _insert_record(conn, "S003", "2026-03-04", "KEEP")

        conn.commit()
        conn.close()

        service = DessertDecisionService(store_id="99999")
        service.decision_repo._get_conn = lambda: sqlite3.connect(str(db_path))
        service.decision_repo._get_conn_rr = service.decision_repo._get_conn
        return service, db_path

    def test_auto_confirm_zero_sales_items(self, service_with_data):
        """30일 판매 0 STOP_RECOMMEND → CONFIRMED_STOP 자동처리"""
        service, db_path = service_with_data

        results = [
            {"item_cd": "S001", "decision": "STOP_RECOMMEND"},
            {"item_cd": "S002", "decision": "STOP_RECOMMEND"},
            {"item_cd": "S003", "decision": "KEEP"},
        ]

        confirmed = service._auto_confirm_zero_sales(results)

        # S001만 자동확인 (판매 없음)
        assert "S001" in confirmed
        # S002는 판매 있으므로 제외
        assert "S002" not in confirmed

        # DB 확인
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT operator_action, operator_note FROM dessert_decisions "
            "WHERE item_cd='S001' AND operator_action IS NOT NULL"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "CONFIRMED_STOP"
        assert "미판매 자동확인" in row[1]

    def test_auto_confirm_skips_items_with_sales(self, service_with_data):
        """30일 내 판매 있으면 자동확인 안 됨"""
        service, db_path = service_with_data

        results = [
            {"item_cd": "S002", "decision": "STOP_RECOMMEND"},
        ]

        confirmed = service._auto_confirm_zero_sales(results)
        assert confirmed == []

        # pending 상태 유지 확인
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT operator_action FROM dessert_decisions WHERE item_cd='S002'"
        ).fetchone()
        conn.close()
        assert row[0] is None  # 미처리 상태 유지

    def test_auto_confirm_no_stop_recommend(self, service_with_data):
        """STOP_RECOMMEND 없으면 빈 리스트"""
        service, _ = service_with_data

        results = [
            {"item_cd": "S003", "decision": "KEEP"},
        ]

        confirmed = service._auto_confirm_zero_sales(results)
        assert confirmed == []
