"""UserTendencyAnalyzer 테스트 (order_diffs 기반)"""

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from src.analysis.user_tendency import (
    ADD_HIGH,
    MIN_INTERVENTION,
    REMOVE_HIGH,
    TRUST_LOW,
    UserTendencyAnalyzer,
    # 하위 호환 export
    PASSIVE_THRESHOLD,
    AGGRESSIVE_THRESHOLD,
    MIN_SITE_COUNT,
)


def _create_analysis_db(db_path):
    """order_analysis.db 테스트용 생성"""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS order_diffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            order_date TEXT NOT NULL,
            receiving_date TEXT NOT NULL,
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            mid_cd TEXT,
            diff_type TEXT NOT NULL,
            auto_order_qty INTEGER DEFAULT 0,
            predicted_qty INTEGER DEFAULT 0,
            eval_decision TEXT,
            confirmed_order_qty INTEGER DEFAULT 0,
            receiving_qty INTEGER DEFAULT 0,
            qty_diff INTEGER DEFAULT 0,
            receiving_diff INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(store_id, order_date, item_cd, receiving_date)
        );
        CREATE TABLE IF NOT EXISTS order_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT, order_date TEXT, item_cd TEXT,
            item_nm TEXT, mid_cd TEXT,
            predicted_qty INTEGER, recommended_qty INTEGER,
            final_order_qty INTEGER, current_stock INTEGER,
            pending_qty INTEGER, eval_decision TEXT,
            order_unit_qty INTEGER, order_success INTEGER,
            confidence TEXT, delivery_type TEXT, data_days INTEGER,
            created_at TEXT,
            UNIQUE(store_id, order_date, item_cd)
        );
        CREATE TABLE IF NOT EXISTS order_diff_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT, order_date TEXT, receiving_date TEXT,
            total_auto_items INTEGER, total_confirmed_items INTEGER,
            items_unchanged INTEGER, items_qty_changed INTEGER,
            items_added INTEGER, items_removed INTEGER,
            items_not_comparable INTEGER,
            total_auto_qty INTEGER, total_confirmed_qty INTEGER,
            total_receiving_qty INTEGER, match_rate REAL,
            has_receiving_data INTEGER, created_at TEXT,
            UNIQUE(store_id, order_date, receiving_date)
        );
        CREATE TABLE IF NOT EXISTS stock_discrepancy_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT, order_date TEXT, item_cd TEXT,
            item_nm TEXT, mid_cd TEXT,
            discrepancy_type TEXT, severity TEXT,
            stock_at_prediction INTEGER, pending_at_prediction INTEGER,
            stock_at_order INTEGER, pending_at_order INTEGER,
            stock_diff INTEGER, pending_diff INTEGER,
            stock_source TEXT, is_stock_stale INTEGER,
            original_order_qty INTEGER, recalculated_order_qty INTEGER,
            order_impact INTEGER, description TEXT, created_at TEXT,
            UNIQUE(store_id, order_date, item_cd)
        );
    """)
    conn.close()
    return db_path


@pytest.fixture
def store_db(tmp_path):
    """테스트용 매장 DB + order_analysis DB 생성"""
    store_id = "99999"
    store_db_dir = tmp_path / "stores"
    store_db_dir.mkdir()
    store_db_path = store_db_dir / f"{store_id}.db"
    analysis_db_path = tmp_path / "order_analysis.db"

    # order_analysis.db 생성
    _create_analysis_db(analysis_db_path)

    # 매장 DB: daily_sales + user_order_tendency
    conn_store = sqlite3.connect(str(store_db_path))
    conn_store.executescript("""
        CREATE TABLE daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at TEXT, sales_date TEXT, item_cd TEXT,
            mid_cd TEXT, sale_qty INTEGER, ord_qty INTEGER,
            buy_qty INTEGER, disuse_qty INTEGER, stock_qty INTEGER,
            created_at TEXT, promo_type TEXT, store_id TEXT,
            UNIQUE(store_id, sales_date, item_cd)
        );
        CREATE TABLE user_order_tendency (
            store_id TEXT NOT NULL,
            mid_cd TEXT NOT NULL,
            period_days INTEGER DEFAULT 90,
            removed_count INTEGER DEFAULT 0,
            added_count INTEGER DEFAULT 0,
            qty_changed_count INTEGER DEFAULT 0,
            qty_up_count INTEGER DEFAULT 0,
            qty_down_count INTEGER DEFAULT 0,
            remove_rate REAL,
            add_rate REAL,
            qty_up_rate REAL,
            tendency TEXT,
            zero_stock_rate REAL,
            updated_at TEXT,
            PRIMARY KEY (store_id, mid_cd)
        );
    """)

    # order_diffs 테스트 데이터 삽입
    now_str = datetime.now().isoformat()
    conn_analysis = sqlite3.connect(str(analysis_db_path))

    def _insert_diff(item_cd, mid_cd, diff_type, auto_qty, confirmed_qty, day_offset):
        d = (datetime.now() - timedelta(days=day_offset)).strftime("%Y-%m-%d")
        rd = (datetime.now() - timedelta(days=day_offset - 1)).strftime("%Y-%m-%d")
        conn_analysis.execute(
            """INSERT OR REPLACE INTO order_diffs
               (store_id, order_date, receiving_date, item_cd, mid_cd,
                diff_type, auto_order_qty, confirmed_order_qty, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (store_id, d, rd, item_cd, mid_cd, diff_type, auto_qty, confirmed_qty, now_str),
        )

    # 049 맥주: removed 20건 + added 3건 → remove_rate ~0.87 → passive
    for i in range(20):
        _insert_diff(f"BEER_R{i}", "049", "removed", 5, 0, i + 1)
    for i in range(3):
        _insert_diff(f"BEER_A{i}", "049", "added", 0, 3, i + 1)

    # 072 담배: removed 3건 + added 20건 → add_rate ~0.87 → aggressive
    for i in range(3):
        _insert_diff(f"TOB_R{i}", "072", "removed", 5, 0, i + 1)
    for i in range(20):
        _insert_diff(f"TOB_A{i}", "072", "added", 0, 10, i + 1)

    # 032 라면: removed 3건 + added 3건 + qty_changed 20건 → balanced
    #  (remove_rate=3/26≈0.12, add_rate=3/26≈0.12 → balanced)
    for i in range(3):
        _insert_diff(f"RAMEN_R{i}", "032", "removed", 3, 0, i + 1)
    for i in range(3):
        _insert_diff(f"RAMEN_A{i}", "032", "added", 0, 2, i + 1)
    for i in range(20):
        _insert_diff(f"RAMEN_Q{i}", "032", "qty_changed", 3, 5, i + 1)

    # 014 디저트: removed 12건 + added 12건 → selective
    for i in range(12):
        _insert_diff(f"DST_R{i}", "014", "removed", 2, 0, i + 1)
    for i in range(12):
        _insert_diff(f"DST_A{i}", "014", "added", 0, 3, i + 1)

    # 050 소주: 총 5건 → insufficient_data
    for i in range(3):
        _insert_diff(f"SOJU_R{i}", "050", "removed", 5, 0, i + 1)
    for i in range(2):
        _insert_diff(f"SOJU_A{i}", "050", "added", 0, 3, i + 1)

    # 020 과자: removed 1건 + added 1건 + qty_changed 30건 → trust
    #  (remove_rate=0.03, add_rate=0.03 → trust)
    _insert_diff("SNACK_R0", "020", "removed", 3, 0, 1)
    _insert_diff("SNACK_A0", "020", "added", 0, 2, 2)
    for i in range(30):
        _insert_diff(f"SNACK_Q{i}", "020", "qty_changed", 3, 4, i + 1)

    conn_analysis.commit()
    conn_analysis.close()

    # daily_sales (stock 데이터)
    today = datetime.now().strftime("%Y-%m-%d")
    for i in range(30):
        d = (datetime.now() - timedelta(days=i + 1)).strftime("%Y-%m-%d")
        # 049 맥주: stock=0 이 많음
        conn_store.execute(
            """INSERT OR REPLACE INTO daily_sales
               (collected_at, sales_date, item_cd, mid_cd,
                sale_qty, ord_qty, buy_qty, disuse_qty, stock_qty,
                created_at, store_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (today, d, "BEER_R0", "049", 2, 1, 1, 0,
             0 if i % 3 != 0 else 3, today, store_id),
        )
        # 032 라면: stock 있음
        conn_store.execute(
            """INSERT OR REPLACE INTO daily_sales
               (collected_at, sales_date, item_cd, mid_cd,
                sale_qty, ord_qty, buy_qty, disuse_qty, stock_qty,
                created_at, store_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (today, d, "RAMEN_R0", "032", 1, 1, 0, 0, 5, today, store_id),
        )

    conn_store.commit()
    conn_store.close()

    return {
        "store_id": store_id,
        "store_db_path": store_db_path,
        "analysis_db_path": analysis_db_path,
        "tmp_path": tmp_path,
    }


@pytest.fixture
def analyzer(store_db):
    """order_analysis_repo와 DBRouter를 mock하여 테스트 DB로 연결하는 analyzer"""
    info = store_db

    from src.infrastructure.database.repos.order_analysis_repo import (
        OrderAnalysisRepository,
    )

    # order_analysis.db mock
    test_repo = OrderAnalysisRepository(db_path=str(info["analysis_db_path"]))

    def mock_get_connection(store_id=None, table=None):
        return sqlite3.connect(str(info["store_db_path"]))

    with patch(
        "src.analysis.user_tendency.DBRouter.get_connection",
        side_effect=mock_get_connection,
    ):
        a = UserTendencyAnalyzer(
            store_id=info["store_id"],
            analysis_repo=test_repo,
        )
        yield a


class TestAnalyze:
    """analyze() 메서드 테스트"""

    def test_tendency_passive(self, analyzer):
        """removed 많으면 passive"""
        results = analyzer.analyze(period_days=90)
        assert results["049"] == "passive"

    def test_tendency_aggressive(self, analyzer):
        """added 많으면 aggressive"""
        results = analyzer.analyze(period_days=90)
        assert results["072"] == "aggressive"

    def test_tendency_balanced(self, analyzer):
        """삭제/추가/변경 고르면 balanced"""
        results = analyzer.analyze(period_days=90)
        assert results["032"] == "balanced"

    def test_tendency_selective(self, analyzer):
        """삭제+추가 둘 다 높으면 selective"""
        results = analyzer.analyze(period_days=90)
        assert results["014"] == "selective"

    def test_tendency_insufficient_data(self, analyzer):
        """개입 건수 부족 시 insufficient_data"""
        results = analyzer.analyze(period_days=90)
        assert results["050"] == "insufficient_data"

    def test_tendency_trust(self, analyzer):
        """삭제/추가 둘 다 매우 낮으면 trust"""
        results = analyzer.analyze(period_days=90)
        assert results["020"] == "trust"

    def test_all_categories_present(self, analyzer):
        """6개 카테고리 모두 분석됨"""
        results = analyzer.analyze(period_days=90)
        assert set(results.keys()) == {"049", "072", "032", "014", "050", "020"}

    def test_db_upsert(self, analyzer, store_db):
        """analyze 후 DB에 저장됨"""
        analyzer.analyze(period_days=90)
        conn = sqlite3.connect(str(store_db["store_db_path"]))
        rows = conn.execute(
            "SELECT COUNT(*) FROM user_order_tendency"
        ).fetchone()[0]
        conn.close()
        assert rows == 6

    def test_upsert_updates_existing(self, analyzer, store_db):
        """두 번 실행해도 행 수 동일 (UPSERT)"""
        analyzer.analyze(period_days=90)
        analyzer.analyze(period_days=90)
        conn = sqlite3.connect(str(store_db["store_db_path"]))
        rows = conn.execute(
            "SELECT COUNT(*) FROM user_order_tendency"
        ).fetchone()[0]
        conn.close()
        assert rows == 6

    def test_remove_rate_passive(self, analyzer, store_db):
        """passive 카테고리의 remove_rate >= REMOVE_HIGH"""
        analyzer.analyze(period_days=90)
        conn = sqlite3.connect(str(store_db["store_db_path"]))
        row = conn.execute(
            "SELECT remove_rate FROM user_order_tendency WHERE mid_cd = '049'"
        ).fetchone()
        conn.close()
        assert row[0] is not None
        assert row[0] >= REMOVE_HIGH

    def test_add_rate_aggressive(self, analyzer, store_db):
        """aggressive 카테고리의 add_rate >= ADD_HIGH"""
        analyzer.analyze(period_days=90)
        conn = sqlite3.connect(str(store_db["store_db_path"]))
        row = conn.execute(
            "SELECT add_rate FROM user_order_tendency WHERE mid_cd = '072'"
        ).fetchone()
        conn.close()
        assert row[0] is not None
        assert row[0] >= ADD_HIGH

    def test_qty_up_rate_populated(self, analyzer, store_db):
        """qty_changed가 있는 카테고리의 qty_up_rate 채워짐"""
        analyzer.analyze(period_days=90)
        conn = sqlite3.connect(str(store_db["store_db_path"]))
        row = conn.execute(
            "SELECT qty_up_rate, qty_up_count, qty_down_count "
            "FROM user_order_tendency WHERE mid_cd = '032'"
        ).fetchone()
        conn.close()
        assert row[0] is not None  # qty_up_rate 채워짐
        assert row[1] + row[2] > 0  # qty_up + qty_down > 0

    def test_removed_count_stored(self, analyzer, store_db):
        """removed_count가 정확하게 저장됨"""
        analyzer.analyze(period_days=90)
        conn = sqlite3.connect(str(store_db["store_db_path"]))
        row = conn.execute(
            "SELECT removed_count, added_count FROM user_order_tendency "
            "WHERE mid_cd = '049'"
        ).fetchone()
        conn.close()
        assert row[0] == 20  # 049: 20건 removed
        assert row[1] == 3   # 049: 3건 added


class TestGetPassiveCategories:
    """get_passive_categories() 테스트"""

    def test_returns_passive_only(self, analyzer):
        """소극형만 반환"""
        analyzer.analyze(period_days=90)
        passive = analyzer.get_passive_categories()
        assert "049" in passive
        assert "032" not in passive
        assert "072" not in passive

    def test_empty_before_analyze(self, analyzer):
        """analyze 전에는 빈 리스트"""
        passive = analyzer.get_passive_categories()
        assert passive == []


class TestGetTendency:
    """get_tendency() 테스트"""

    def test_returns_correct_tendency(self, analyzer):
        """특정 mid_cd의 성향 반환"""
        analyzer.analyze(period_days=90)
        assert analyzer.get_tendency("049") == "passive"
        assert analyzer.get_tendency("032") == "balanced"
        assert analyzer.get_tendency("072") == "aggressive"
        assert analyzer.get_tendency("014") == "selective"
        assert analyzer.get_tendency("020") == "trust"

    def test_returns_none_for_unknown(self, analyzer):
        """미존재 mid_cd에 None 반환"""
        assert analyzer.get_tendency("999") is None


class TestGetSummary:
    """get_summary() 테스트"""

    def test_returns_dataframe(self, analyzer):
        """DataFrame 반환"""
        analyzer.analyze(period_days=90)
        df = analyzer.get_summary()
        assert len(df) == 6
        assert "tendency" in df.columns
        assert "remove_rate" in df.columns
        assert "add_rate" in df.columns

    def test_contains_new_columns(self, analyzer):
        """새 컬럼이 모두 포함됨"""
        analyzer.analyze(period_days=90)
        df = analyzer.get_summary()
        expected_cols = [
            "removed_count", "added_count", "qty_changed_count",
            "qty_up_count", "qty_down_count",
            "remove_rate", "add_rate", "qty_up_rate", "tendency",
        ]
        for col in expected_cols:
            assert col in df.columns, f"Missing column: {col}"

    def test_zero_stock_rate_populated(self, analyzer):
        """zero_stock_rate가 채워짐"""
        analyzer.analyze(period_days=90)
        df = analyzer.get_summary()
        beer_row = df[df["mid_cd"] == "049"]
        assert not beer_row.empty
        assert beer_row.iloc[0]["zero_stock_rate"] >= 0


class TestThresholdConstants:
    """임계값 상수 테스트"""

    def test_remove_high(self):
        assert REMOVE_HIGH == 0.3

    def test_add_high(self):
        assert ADD_HIGH == 0.3

    def test_trust_low(self):
        assert TRUST_LOW == 0.1

    def test_min_intervention(self):
        assert MIN_INTERVENTION == 10

    def test_backward_compat_exports(self):
        """하위 호환 export 확인"""
        assert PASSIVE_THRESHOLD == 0.7
        assert AGGRESSIVE_THRESHOLD == 1.3
        assert MIN_SITE_COUNT == MIN_INTERVENTION
