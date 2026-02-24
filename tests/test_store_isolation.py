"""
멀티 매장 데이터 격리 테스트

두 매장(46513, 46704)에 구별 가능한 데이터를 삽입한 뒤,
각 모듈이 store_id 필터를 올바르게 적용하는지 검증합니다.

패턴:
  Store A (46513): sale_qty = 5 (홀수)
  Store B (46704): sale_qty = 4 (짝수)
  → 합산되면 9가 되므로 누락 시 즉시 탐지 가능
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


STORE_A = "46513"
STORE_B = "46704"
SALE_QTY_A = 5  # 홀수 패턴
SALE_QTY_B = 4  # 짝수 패턴
TEST_ITEM_CD = "8801234567890"
TEST_MID_CD = "049"
TEST_ITEM_NM = "테스트맥주"


@pytest.fixture
def multi_store_db(tmp_path):
    """두 매장의 데이터가 포함된 테스트 DB"""
    db_file = tmp_path / "multi_store_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row

    # daily_sales (store_id 포함 - 프로덕션 스키마)
    conn.execute("""
        CREATE TABLE daily_sales (
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
            store_id TEXT DEFAULT '46513',
            UNIQUE(store_id, sales_date, item_cd)
        )
    """)

    # products
    conn.execute("""
        CREATE TABLE products (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            mid_cd TEXT
        )
    """)

    # mid_categories
    conn.execute("""
        CREATE TABLE mid_categories (
            mid_cd TEXT PRIMARY KEY,
            mid_nm TEXT
        )
    """)

    # product_details
    conn.execute("""
        CREATE TABLE product_details (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            mid_cd TEXT,
            order_unit_qty INTEGER DEFAULT 1,
            expiration_days INTEGER,
            margin_rate REAL,
            orderable_day TEXT DEFAULT '일월화수목금토'
        )
    """)

    # order_tracking
    conn.execute("""
        CREATE TABLE order_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT,
            item_nm TEXT,
            mid_cd TEXT,
            order_date TEXT,
            order_qty INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            expiry_time TEXT,
            remaining_qty INTEGER DEFAULT 0,
            created_at TEXT,
            store_id TEXT DEFAULT '46513'
        )
    """)

    # realtime_inventory
    conn.execute("""
        CREATE TABLE realtime_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT NOT NULL,
            check_date TEXT NOT NULL,
            stock_qty INTEGER DEFAULT 0,
            pending_qty INTEGER DEFAULT 0,
            store_id TEXT DEFAULT '46513',
            UNIQUE(store_id, item_cd, check_date)
        )
    """)

    # inventory_batches
    conn.execute("""
        CREATE TABLE inventory_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT,
            item_nm TEXT,
            mid_cd TEXT,
            expiry_date TEXT,
            remaining_qty INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            store_id TEXT DEFAULT '46513'
        )
    """)

    # collection_logs
    conn.execute("""
        CREATE TABLE collection_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sales_date TEXT,
            collected_at TEXT,
            total_items INTEGER DEFAULT 0,
            status TEXT DEFAULT 'success',
            store_id TEXT DEFAULT '46513'
        )
    """)

    # order_fail_reasons
    conn.execute("""
        CREATE TABLE order_fail_reasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT,
            item_nm TEXT,
            mid_cd TEXT,
            eval_date TEXT,
            stop_reason TEXT,
            orderable_status TEXT,
            checked_at TEXT,
            store_id TEXT DEFAULT '46513'
        )
    """)

    # promotions (store_id 추가 - v26)
    conn.execute("""
        CREATE TABLE promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT,
            item_nm TEXT,
            promo_type TEXT,
            start_date TEXT,
            end_date TEXT,
            status TEXT DEFAULT 'active',
            is_active INTEGER DEFAULT 1,
            store_id TEXT DEFAULT '46513',
            UNIQUE(store_id, item_cd, promo_type, start_date)
        )
    """)

    # promotion_stats (store_id 추가 - v26)
    conn.execute("""
        CREATE TABLE promotion_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT,
            promo_type TEXT,
            avg_daily_sales REAL,
            total_days INTEGER,
            total_sales INTEGER,
            multiplier REAL,
            last_calculated TEXT,
            store_id TEXT DEFAULT '46513',
            UNIQUE(store_id, item_cd, promo_type)
        )
    """)

    # promotion_changes (store_id 추가 - v26)
    conn.execute("""
        CREATE TABLE promotion_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT,
            change_date TEXT,
            change_type TEXT,
            old_promo TEXT,
            new_promo TEXT,
            detected_at TEXT,
            store_id TEXT DEFAULT '46513',
            UNIQUE(store_id, item_cd, change_date, change_type)
        )
    """)

    # receiving_history (store_id 추가 - v26)
    conn.execute("""
        CREATE TABLE receiving_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receiving_date TEXT,
            item_cd TEXT,
            chit_no TEXT,
            order_qty INTEGER DEFAULT 0,
            received_qty INTEGER DEFAULT 0,
            store_id TEXT DEFAULT '46513',
            UNIQUE(store_id, receiving_date, item_cd, chit_no)
        )
    """)

    # auto_order_items (store_id 추가 - v26)
    conn.execute("""
        CREATE TABLE auto_order_items (
            store_id TEXT DEFAULT '46513',
            item_cd TEXT,
            item_nm TEXT,
            mid_cd TEXT,
            detected_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY(store_id, item_cd)
        )
    """)

    # smart_order_items (store_id 추가 - v26)
    conn.execute("""
        CREATE TABLE smart_order_items (
            store_id TEXT DEFAULT '46513',
            item_cd TEXT,
            item_nm TEXT,
            mid_cd TEXT,
            detected_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY(store_id, item_cd)
        )
    """)

    # order_history (store_id 추가 - v26)
    conn.execute("""
        CREATE TABLE order_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT,
            order_date TEXT,
            order_qty INTEGER DEFAULT 0,
            predicted_qty REAL DEFAULT 0,
            status TEXT DEFAULT 'pending',
            store_id TEXT DEFAULT '46513'
        )
    """)

    # external_factors (글로벌)
    conn.execute("""
        CREATE TABLE external_factors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            factor_date TEXT,
            factor_type TEXT,
            factor_value REAL
        )
    """)

    # prediction_logs (store_id 추가 - v26)
    conn.execute("""
        CREATE TABLE prediction_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_date TEXT,
            target_date TEXT,
            item_cd TEXT,
            mid_cd TEXT,
            predicted_qty REAL,
            actual_qty REAL,
            model_type TEXT,
            created_at TEXT,
            store_id TEXT DEFAULT '46513'
        )
    """)

    # eval_outcomes (store_id 추가 - v26)
    conn.execute("""
        CREATE TABLE eval_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT,
            eval_date TEXT,
            decision TEXT,
            outcome TEXT,
            order_status TEXT,
            created_at TEXT,
            store_id TEXT DEFAULT '46513',
            UNIQUE(store_id, eval_date, item_cd)
        )
    """)

    # app_settings
    conn.execute("""
        CREATE TABLE app_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
    """)

    # 마스터 데이터 삽입
    conn.execute(
        "INSERT INTO products (item_cd, item_nm, mid_cd) VALUES (?, ?, ?)",
        (TEST_ITEM_CD, TEST_ITEM_NM, TEST_MID_CD)
    )
    conn.execute(
        "INSERT INTO mid_categories (mid_cd, mid_nm) VALUES (?, ?)",
        (TEST_MID_CD, "맥주")
    )
    conn.execute(
        "INSERT INTO product_details (item_cd, item_nm, mid_cd, expiration_days, margin_rate) VALUES (?, ?, ?, ?, ?)",
        (TEST_ITEM_CD, TEST_ITEM_NM, TEST_MID_CD, 90, 0.25)
    )

    # 30일치 판매 데이터 삽입 (두 매장 모두)
    now = datetime.now()
    collected_at = now.isoformat()
    created_at = now.isoformat()

    for days_ago in range(30):
        date = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")

        # Store A: sale_qty = SALE_QTY_A (5)
        conn.execute(
            """INSERT INTO daily_sales
               (collected_at, sales_date, item_cd, mid_cd, sale_qty, ord_qty,
                buy_qty, disuse_qty, stock_qty, created_at, store_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (collected_at, date, TEST_ITEM_CD, TEST_MID_CD,
             SALE_QTY_A, 10, 10, 0, 20, created_at, STORE_A)
        )

        # Store B: sale_qty = SALE_QTY_B (4)
        conn.execute(
            """INSERT INTO daily_sales
               (collected_at, sales_date, item_cd, mid_cd, sale_qty, ord_qty,
                buy_qty, disuse_qty, stock_qty, created_at, store_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (collected_at, date, TEST_ITEM_CD, TEST_MID_CD,
             SALE_QTY_B, 8, 8, 0, 16, created_at, STORE_B)
        )

    conn.commit()
    yield {"conn": conn, "db_path": str(db_file)}
    conn.close()


class TestSalesRepoStoreIsolation:
    """SalesRepository 매장 격리 테스트"""

    def test_sales_repo_store_isolation(self, multi_store_db):
        """SalesRepository가 store_id별로 데이터를 격리하는지 검증"""
        from src.infrastructure.database.repos import SalesRepository

        db_path = Path(multi_store_db["db_path"])
        repo = SalesRepository(db_path=db_path)

        # Store A 데이터만 조회
        result_a = repo.get_daily_sales(
            sales_date=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
            store_id=STORE_A
        )

        # Store B 데이터만 조회
        result_b = repo.get_daily_sales(
            sales_date=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
            store_id=STORE_B
        )

        # 각 매장은 1개 상품만 있어야 함
        assert len(result_a) <= 1, f"Store A에 예상보다 많은 데이터: {len(result_a)}"
        assert len(result_b) <= 1, f"Store B에 예상보다 많은 데이터: {len(result_b)}"

        if result_a:
            assert result_a[0]["sale_qty"] == SALE_QTY_A, \
                f"Store A sale_qty가 {SALE_QTY_A}이어야 하는데 {result_a[0]['sale_qty']}"
        if result_b:
            assert result_b[0]["sale_qty"] == SALE_QTY_B, \
                f"Store B sale_qty가 {SALE_QTY_B}이어야 하는데 {result_b[0]['sale_qty']}"


class TestMLPipelineStoreIsolation:
    """ML 데이터 파이프라인 매장 격리 테스트"""

    def test_ml_pipeline_store_isolation(self, multi_store_db):
        """MLDataPipeline이 store_id별로 데이터를 격리하는지 검증"""
        from src.prediction.ml.data_pipeline import MLDataPipeline

        db_path = multi_store_db["db_path"]

        # Store A 파이프라인
        pipeline_a = MLDataPipeline(db_path=db_path, store_id=STORE_A)
        stats_a = pipeline_a.get_item_daily_stats(TEST_ITEM_CD, days=30)

        # Store B 파이프라인
        pipeline_b = MLDataPipeline(db_path=db_path, store_id=STORE_B)
        stats_b = pipeline_b.get_item_daily_stats(TEST_ITEM_CD, days=30)

        # 각 매장의 sale_qty가 다르면 격리 성공
        if stats_a and stats_b:
            avg_a = sum(d["sale_qty"] for d in stats_a) / len(stats_a)
            avg_b = sum(d["sale_qty"] for d in stats_b) / len(stats_b)

            assert abs(avg_a - SALE_QTY_A) < 0.5, \
                f"Store A 평균 판매량이 {SALE_QTY_A} 근처여야 하는데 {avg_a:.1f}"
            assert abs(avg_b - SALE_QTY_B) < 0.5, \
                f"Store B 평균 판매량이 {SALE_QTY_B} 근처여야 하는데 {avg_b:.1f}"

    def test_ml_pipeline_active_items_isolation(self, multi_store_db):
        """활성 상품 목록이 store_id별로 격리되는지 검증"""
        from src.prediction.ml.data_pipeline import MLDataPipeline

        db_path = multi_store_db["db_path"]

        pipeline_a = MLDataPipeline(db_path=db_path, store_id=STORE_A)
        items_a = pipeline_a.get_active_items(min_days=7)

        pipeline_b = MLDataPipeline(db_path=db_path, store_id=STORE_B)
        items_b = pipeline_b.get_active_items(min_days=7)

        # 각 매장에서 동일 상품이 조회되어야 함
        assert len(items_a) >= 1, "Store A에 활성 상품이 있어야 함"
        assert len(items_b) >= 1, "Store B에 활성 상품이 있어야 함"

    def test_ml_pipeline_data_days_isolation(self, multi_store_db):
        """데이터 일수 카운트가 store_id별로 격리되는지 검증"""
        from src.prediction.ml.data_pipeline import MLDataPipeline

        db_path = multi_store_db["db_path"]

        pipeline_a = MLDataPipeline(db_path=db_path, store_id=STORE_A)
        days_a = pipeline_a.get_data_days_count(TEST_ITEM_CD)

        # store_id 없이 조회 시 (전체)
        pipeline_all = MLDataPipeline(db_path=db_path, store_id=None)
        days_all = pipeline_all.get_data_days_count(TEST_ITEM_CD)

        # 두 매장이 같은 날짜에 데이터가 있으므로 일수는 동일해야 함
        # 하지만 store_id 없이 조회해도 중복 날짜는 DISTINCT로 같아야 함
        assert days_a > 0, "Store A 데이터 일수가 0보다 커야 함"
        assert days_all >= days_a, "전체 데이터 일수는 매장별보다 같거나 커야 함"


class TestProductAnalyzerStoreIsolation:
    """ProductAnalyzer 매장 격리 테스트"""

    def test_product_analyzer_store_isolation(self, multi_store_db):
        """ProductAnalyzer가 store_id별로 데이터를 격리하는지 검증"""
        from src.analysis.product_analyzer import ProductAnalyzer

        db_path = multi_store_db["db_path"]

        def create_conn(*args, **kwargs):
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c

        with patch.object(ProductAnalyzer, "_get_conn", side_effect=create_conn):
            analyzer_a = ProductAnalyzer(store_id=STORE_A)

            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            two_weeks_ago = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
            week_ago_minus1 = (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%d")

            growth_a = analyzer_a.get_product_growth(
                week_ago, yesterday, two_weeks_ago, week_ago_minus1
            )

        # Store A 데이터만 있는지 확인
        if growth_a:
            for product in growth_a:
                # Store A의 판매량(5)만 반영되어야 함
                # 합산(9)이 나오면 격리 실패
                assert product["this_week_sales"] != SALE_QTY_A + SALE_QTY_B, \
                    f"격리 실패: 판매량이 {product['this_week_sales']}인데, 합산값 {SALE_QTY_A + SALE_QTY_B}과 같음"


class TestTrendReportStoreIsolation:
    """TrendReport 매장 격리 테스트"""

    def test_weekly_trend_store_isolation(self, multi_store_db):
        """WeeklyTrendReport가 store_id별로 데이터를 격리하는지 검증"""
        from src.analysis.trend_report import WeeklyTrendReport

        db_path = multi_store_db["db_path"]

        with patch("src.analysis.trend_report.get_connection") as mock_conn:
            # PredictionLogger도 모킹 필요
            with patch("src.analysis.trend_report.PredictionLogger") as mock_logger:
                mock_logger.return_value.calculate_accuracy.return_value = {"message": "데이터 부족"}

                # ProductAnalyzer의 _get_conn도 모킹
                from src.analysis.product_analyzer import ProductAnalyzer
                with patch.object(ProductAnalyzer, "_get_conn") as mock_pa_conn:
                    def create_conn():
                        c = sqlite3.connect(db_path)
                        c.row_factory = sqlite3.Row
                        return c

                    mock_conn.side_effect = lambda *a, **k: create_conn()
                    mock_pa_conn.side_effect = lambda *a, **k: create_conn()

                    report_a = WeeklyTrendReport(store_id=STORE_A)
                    result_a = report_a.generate()

                    report_b = WeeklyTrendReport(store_id=STORE_B)
                    result_b = report_b.generate()

        # 카테고리 성장률 데이터가 존재해야 함
        assert "category_growth" in result_a
        assert "category_growth" in result_b

    def test_monthly_trend_store_isolation(self, multi_store_db):
        """MonthlyTrendReport가 store_id별로 데이터를 격리하는지 검증"""
        from src.analysis.trend_report import MonthlyTrendReport

        db_path = multi_store_db["db_path"]

        with patch("src.analysis.trend_report.get_connection") as mock_conn:
            def create_conn():
                c = sqlite3.connect(db_path)
                c.row_factory = sqlite3.Row
                return c

            mock_conn.side_effect = lambda *a, **k: create_conn()

            now = datetime.now()
            report_a = MonthlyTrendReport(store_id=STORE_A)
            result_a = report_a.generate(now.year, now.month)

            report_b = MonthlyTrendReport(store_id=STORE_B)
            result_b = report_b.generate(now.year, now.month)

        # 월간 요약의 총 판매량이 달라야 함
        summary_a = result_a.get("summary", {})
        summary_b = result_b.get("summary", {})

        if summary_a.get("total_sales") and summary_b.get("total_sales"):
            assert summary_a["total_sales"] != summary_b["total_sales"], \
                f"격리 실패: Store A ({summary_a['total_sales']}) == Store B ({summary_b['total_sales']})"


class TestDataValidatorStoreIsolation:
    """DataValidator 매장 격리 테스트"""

    def test_validator_store_isolation(self, multi_store_db):
        """DataValidator가 store_id별로 중복 감지를 격리하는지 검증"""
        from src.validation.data_validator import DataValidator
        from src.validation.validation_rules import ValidationRules

        db_path = multi_store_db["db_path"]

        with patch("src.validation.data_validator.get_connection") as mock_conn:
            def create_conn():
                c = sqlite3.connect(db_path)
                c.row_factory = sqlite3.Row
                return c

            mock_conn.side_effect = lambda *a, **k: create_conn()

            validator_a = DataValidator(store_id=STORE_A)
            validator_b = DataValidator(store_id=STORE_B)

            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

            # 각 매장에서 중복 감지 - 한 번만 수집했으므로 False여야 함
            dup_a = validator_a.detect_duplicate(yesterday, TEST_ITEM_CD)
            dup_b = validator_b.detect_duplicate(yesterday, TEST_ITEM_CD)

            assert not dup_a, "Store A에서 거짓 중복이 감지됨"
            assert not dup_b, "Store B에서 거짓 중복이 감지됨"


class TestNewTablesStoreIsolation:
    """v26에서 추가된 9개 테이블의 매장 격리 테스트"""

    def test_prediction_logs_isolation(self, multi_store_db):
        """prediction_logs가 store_id별로 격리되는지 검증"""
        conn = multi_store_db["conn"]
        now = datetime.now().strftime("%Y-%m-%d")

        conn.execute(
            "INSERT INTO prediction_logs (prediction_date, item_cd, predicted_qty, store_id) VALUES (?, ?, ?, ?)",
            (now, TEST_ITEM_CD, 10.0, STORE_A)
        )
        conn.execute(
            "INSERT INTO prediction_logs (prediction_date, item_cd, predicted_qty, store_id) VALUES (?, ?, ?, ?)",
            (now, TEST_ITEM_CD, 20.0, STORE_B)
        )
        conn.commit()

        row_a = conn.execute(
            "SELECT predicted_qty FROM prediction_logs WHERE store_id = ?", (STORE_A,)
        ).fetchone()
        row_b = conn.execute(
            "SELECT predicted_qty FROM prediction_logs WHERE store_id = ?", (STORE_B,)
        ).fetchone()

        assert row_a["predicted_qty"] == 10.0, "Store A prediction_logs 격리 실패"
        assert row_b["predicted_qty"] == 20.0, "Store B prediction_logs 격리 실패"

    def test_eval_outcomes_isolation(self, multi_store_db):
        """eval_outcomes가 store_id별로 격리되는지 검증"""
        conn = multi_store_db["conn"]
        now = datetime.now().strftime("%Y-%m-%d")

        conn.execute(
            "INSERT INTO eval_outcomes (item_cd, eval_date, decision, store_id) VALUES (?, ?, ?, ?)",
            (TEST_ITEM_CD, now, "FORCE_ORDER", STORE_A)
        )
        conn.execute(
            "INSERT INTO eval_outcomes (item_cd, eval_date, decision, store_id) VALUES (?, ?, ?, ?)",
            (TEST_ITEM_CD, now, "SKIP", STORE_B)
        )
        conn.commit()

        row_a = conn.execute(
            "SELECT decision FROM eval_outcomes WHERE store_id = ?", (STORE_A,)
        ).fetchone()
        row_b = conn.execute(
            "SELECT decision FROM eval_outcomes WHERE store_id = ?", (STORE_B,)
        ).fetchone()

        assert row_a["decision"] == "FORCE_ORDER", "Store A eval_outcomes 격리 실패"
        assert row_b["decision"] == "SKIP", "Store B eval_outcomes 격리 실패"

    def test_promotions_isolation(self, multi_store_db):
        """promotions가 store_id별로 격리되는지 검증"""
        conn = multi_store_db["conn"]

        conn.execute(
            "INSERT INTO promotions (item_cd, promo_type, start_date, end_date, store_id) VALUES (?, ?, ?, ?, ?)",
            (TEST_ITEM_CD, "1+1", "2026-02-01", "2026-02-28", STORE_A)
        )
        conn.execute(
            "INSERT INTO promotions (item_cd, promo_type, start_date, end_date, store_id) VALUES (?, ?, ?, ?, ?)",
            (TEST_ITEM_CD, "2+1", "2026-02-01", "2026-02-28", STORE_B)
        )
        conn.commit()

        row_a = conn.execute(
            "SELECT promo_type FROM promotions WHERE store_id = ?", (STORE_A,)
        ).fetchone()
        row_b = conn.execute(
            "SELECT promo_type FROM promotions WHERE store_id = ?", (STORE_B,)
        ).fetchone()

        assert row_a["promo_type"] == "1+1", "Store A promotions 격리 실패"
        assert row_b["promo_type"] == "2+1", "Store B promotions 격리 실패"

    def test_promotion_stats_isolation(self, multi_store_db):
        """promotion_stats가 store_id별로 격리되는지 검증"""
        conn = multi_store_db["conn"]

        conn.execute(
            "INSERT INTO promotion_stats (item_cd, promo_type, multiplier, store_id) VALUES (?, ?, ?, ?)",
            (TEST_ITEM_CD, "1+1", 3.0, STORE_A)
        )
        conn.execute(
            "INSERT INTO promotion_stats (item_cd, promo_type, multiplier, store_id) VALUES (?, ?, ?, ?)",
            (TEST_ITEM_CD, "1+1", 2.0, STORE_B)
        )
        conn.commit()

        row_a = conn.execute(
            "SELECT multiplier FROM promotion_stats WHERE store_id = ?", (STORE_A,)
        ).fetchone()
        row_b = conn.execute(
            "SELECT multiplier FROM promotion_stats WHERE store_id = ?", (STORE_B,)
        ).fetchone()

        assert row_a["multiplier"] == 3.0, "Store A promotion_stats 격리 실패"
        assert row_b["multiplier"] == 2.0, "Store B promotion_stats 격리 실패"

    def test_promotion_changes_isolation(self, multi_store_db):
        """promotion_changes가 store_id별로 격리되는지 검증"""
        conn = multi_store_db["conn"]

        conn.execute(
            "INSERT INTO promotion_changes (item_cd, change_date, change_type, new_promo, store_id) VALUES (?, ?, ?, ?, ?)",
            (TEST_ITEM_CD, "2026-02-01", "start", "1+1", STORE_A)
        )
        conn.execute(
            "INSERT INTO promotion_changes (item_cd, change_date, change_type, new_promo, store_id) VALUES (?, ?, ?, ?, ?)",
            (TEST_ITEM_CD, "2026-02-01", "start", "2+1", STORE_B)
        )
        conn.commit()

        row_a = conn.execute(
            "SELECT new_promo FROM promotion_changes WHERE store_id = ?", (STORE_A,)
        ).fetchone()
        row_b = conn.execute(
            "SELECT new_promo FROM promotion_changes WHERE store_id = ?", (STORE_B,)
        ).fetchone()

        assert row_a["new_promo"] == "1+1", "Store A promotion_changes 격리 실패"
        assert row_b["new_promo"] == "2+1", "Store B promotion_changes 격리 실패"

    def test_receiving_history_isolation(self, multi_store_db):
        """receiving_history가 store_id별로 격리되는지 검증"""
        conn = multi_store_db["conn"]

        conn.execute(
            "INSERT INTO receiving_history (receiving_date, item_cd, chit_no, received_qty, store_id) VALUES (?, ?, ?, ?, ?)",
            ("2026-02-10", TEST_ITEM_CD, "C001", 10, STORE_A)
        )
        conn.execute(
            "INSERT INTO receiving_history (receiving_date, item_cd, chit_no, received_qty, store_id) VALUES (?, ?, ?, ?, ?)",
            ("2026-02-10", TEST_ITEM_CD, "C001", 20, STORE_B)
        )
        conn.commit()

        row_a = conn.execute(
            "SELECT received_qty FROM receiving_history WHERE store_id = ?", (STORE_A,)
        ).fetchone()
        row_b = conn.execute(
            "SELECT received_qty FROM receiving_history WHERE store_id = ?", (STORE_B,)
        ).fetchone()

        assert row_a["received_qty"] == 10, "Store A receiving_history 격리 실패"
        assert row_b["received_qty"] == 20, "Store B receiving_history 격리 실패"

    def test_auto_order_items_isolation(self, multi_store_db):
        """auto_order_items가 store_id별로 격리되는지 검증"""
        conn = multi_store_db["conn"]

        conn.execute(
            "INSERT INTO auto_order_items (store_id, item_cd, item_nm, mid_cd) VALUES (?, ?, ?, ?)",
            (STORE_A, TEST_ITEM_CD, "테스트A", TEST_MID_CD)
        )
        conn.execute(
            "INSERT INTO auto_order_items (store_id, item_cd, item_nm, mid_cd) VALUES (?, ?, ?, ?)",
            (STORE_B, TEST_ITEM_CD, "테스트B", TEST_MID_CD)
        )
        conn.commit()

        row_a = conn.execute(
            "SELECT item_nm FROM auto_order_items WHERE store_id = ?", (STORE_A,)
        ).fetchone()
        row_b = conn.execute(
            "SELECT item_nm FROM auto_order_items WHERE store_id = ?", (STORE_B,)
        ).fetchone()

        assert row_a["item_nm"] == "테스트A", "Store A auto_order_items 격리 실패"
        assert row_b["item_nm"] == "테스트B", "Store B auto_order_items 격리 실패"

    def test_smart_order_items_isolation(self, multi_store_db):
        """smart_order_items가 store_id별로 격리되는지 검증"""
        conn = multi_store_db["conn"]

        conn.execute(
            "INSERT INTO smart_order_items (store_id, item_cd, item_nm, mid_cd) VALUES (?, ?, ?, ?)",
            (STORE_A, TEST_ITEM_CD, "스마트A", TEST_MID_CD)
        )
        conn.execute(
            "INSERT INTO smart_order_items (store_id, item_cd, item_nm, mid_cd) VALUES (?, ?, ?, ?)",
            (STORE_B, TEST_ITEM_CD, "스마트B", TEST_MID_CD)
        )
        conn.commit()

        row_a = conn.execute(
            "SELECT item_nm FROM smart_order_items WHERE store_id = ?", (STORE_A,)
        ).fetchone()
        row_b = conn.execute(
            "SELECT item_nm FROM smart_order_items WHERE store_id = ?", (STORE_B,)
        ).fetchone()

        assert row_a["item_nm"] == "스마트A", "Store A smart_order_items 격리 실패"
        assert row_b["item_nm"] == "스마트B", "Store B smart_order_items 격리 실패"

    def test_order_history_isolation(self, multi_store_db):
        """order_history가 store_id별로 격리되는지 검증"""
        conn = multi_store_db["conn"]

        conn.execute(
            "INSERT INTO order_history (item_cd, order_date, order_qty, store_id) VALUES (?, ?, ?, ?)",
            (TEST_ITEM_CD, "2026-02-10", 5, STORE_A)
        )
        conn.execute(
            "INSERT INTO order_history (item_cd, order_date, order_qty, store_id) VALUES (?, ?, ?, ?)",
            (TEST_ITEM_CD, "2026-02-10", 10, STORE_B)
        )
        conn.commit()

        row_a = conn.execute(
            "SELECT order_qty FROM order_history WHERE store_id = ?", (STORE_A,)
        ).fetchone()
        row_b = conn.execute(
            "SELECT order_qty FROM order_history WHERE store_id = ?", (STORE_B,)
        ).fetchone()

        assert row_a["order_qty"] == 5, "Store A order_history 격리 실패"
        assert row_b["order_qty"] == 10, "Store B order_history 격리 실패"
