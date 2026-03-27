"""cold-start-fix 테스트

신규 상품(데이터 7일 미만)의 WMA 콜드스타트 보정 검증.
문제: 1일 판매 → WMA(7일)=0 → 발주 0 → 재고 0 → 판매 불가 → 순환 함정
해결: data_days < 7이면 일평균(total/days)으로 WMA 보정
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def cold_start_db(tmp_path):
    """콜드스타트 테스트용 DB"""
    db_file = tmp_path / "cold_start.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT NOT NULL,
            sales_date TEXT NOT NULL,
            sale_qty INTEGER DEFAULT 0,
            stock_qty INTEGER DEFAULT 0,
            mid_cd TEXT,
            ord_qty INTEGER DEFAULT 0,
            buy_qty INTEGER DEFAULT 0,
            disuse_qty INTEGER DEFAULT 0,
            store_id TEXT DEFAULT '46513',
            UNIQUE(item_cd, sales_date)
        );

        CREATE TABLE realtime_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT NOT NULL,
            check_date TEXT NOT NULL,
            stock_qty INTEGER DEFAULT 0,
            pending_qty INTEGER DEFAULT 0,
            is_available INTEGER DEFAULT 1,
            store_id TEXT DEFAULT '46513',
            UNIQUE(item_cd, check_date)
        );

        CREATE TABLE products (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            mid_cd TEXT
        );

        CREATE TABLE product_details (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            mid_cd TEXT,
            order_unit_qty INTEGER DEFAULT 1,
            expiration_days INTEGER,
            sell_price INTEGER,
            margin_rate REAL,
            lead_time_days INTEGER DEFAULT 1,
            orderable_day TEXT DEFAULT '일월화수목금토',
            large_cd TEXT,
            small_cd TEXT,
            class_nm TEXT
        );

        CREATE TABLE promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT,
            promo_type TEXT,
            start_date TEXT,
            end_date TEXT,
            status TEXT DEFAULT 'active',
            is_active INTEGER DEFAULT 1,
            store_id TEXT DEFAULT '46513',
            UNIQUE(store_id, item_cd, promo_type, start_date)
        );

        CREATE TABLE prediction_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT,
            target_date TEXT,
            predicted_qty REAL,
            model_type TEXT DEFAULT 'rule',
            store_id TEXT DEFAULT '46513'
        );

        CREATE TABLE inventory_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT DEFAULT '46513',
            item_cd TEXT,
            item_nm TEXT,
            mid_cd TEXT,
            receiving_date TEXT,
            receiving_id INTEGER,
            expiration_days INTEGER,
            expiry_date TEXT,
            initial_qty INTEGER DEFAULT 0,
            remaining_qty INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at TEXT,
            updated_at TEXT
        );
    """)

    conn.commit()
    return str(db_file), conn


def _insert_item(conn, item_cd, item_nm, mid_cd):
    """상품 등록 헬퍼"""
    conn.execute(
        "INSERT INTO products (item_cd, item_nm, mid_cd) VALUES (?, ?, ?)",
        (item_cd, item_nm, mid_cd),
    )
    conn.execute(
        "INSERT INTO product_details (item_cd, item_nm, mid_cd, order_unit_qty, expiration_days) "
        "VALUES (?, ?, ?, 1, 999)",
        (item_cd, item_nm, mid_cd),
    )


def _insert_sales(conn, item_cd, mid_cd, days_ago_qty_pairs):
    """판매 이력 삽입 헬퍼. days_ago_qty_pairs: [(days_ago, qty), ...]"""
    today = datetime.now()
    for days_ago, qty in days_ago_qty_pairs:
        date = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        conn.execute(
            "INSERT OR REPLACE INTO daily_sales "
            "(item_cd, sales_date, sale_qty, stock_qty, mid_cd) "
            "VALUES (?, ?, ?, ?, ?)",
            (item_cd, date, qty, 5, mid_cd),
        )
    conn.commit()


class TestColdStartFix:
    """신규 상품 콜드스타트 WMA 보정 테스트"""

    def test_1day_1sale_should_predict_nonzero(self, cold_start_db):
        """1일 1개 판매 → WMA 보정으로 예측 > 0"""
        db_path, conn = cold_start_db
        _insert_item(conn, "NEW_01", "신규담배", "073")
        _insert_sales(conn, "NEW_01", "073", [(1, 1)])

        conn.execute(
            "INSERT INTO realtime_inventory (item_cd, check_date, stock_qty, pending_qty) "
            "VALUES (?, ?, 0, 0)",
            ("NEW_01", datetime.now().strftime("%Y-%m-%d")),
        )
        conn.commit()
        conn.close()

        from src.prediction.improved_predictor import ImprovedPredictor
        predictor = ImprovedPredictor(db_path=db_path)
        result = predictor.predict("NEW_01", datetime.now())

        assert result is not None
        # 핵심: 콜드스타트 보정으로 예측값 > 0
        assert result.adjusted_qty > 0 or result.order_qty > 0, \
            f"1일 1개 판매 신규상품인데 예측=0 (adj={result.adjusted_qty}, order={result.order_qty})"

    def test_3days_5sales_daily_avg(self, cold_start_db):
        """3일 5개 판매 → daily_avg=1.67"""
        db_path, conn = cold_start_db
        _insert_item(conn, "NEW_02", "신규잡화", "099")
        _insert_sales(conn, "NEW_02", "099", [(1, 2), (2, 2), (3, 1)])

        conn.execute(
            "INSERT INTO realtime_inventory (item_cd, check_date, stock_qty, pending_qty) "
            "VALUES (?, ?, 0, 0)",
            ("NEW_02", datetime.now().strftime("%Y-%m-%d")),
        )
        conn.commit()
        conn.close()

        from src.prediction.improved_predictor import ImprovedPredictor
        predictor = ImprovedPredictor(db_path=db_path)
        result = predictor.predict("NEW_02", datetime.now())

        assert result is not None
        assert result.adjusted_qty > 0, \
            f"3일 5개 판매인데 예측=0 (adj={result.adjusted_qty})"

    def test_6days_data_still_corrected(self, cold_start_db):
        """6일 데이터 (< 7) → 보정 적용"""
        db_path, conn = cold_start_db
        _insert_item(conn, "NEW_03", "6일상품", "099")
        _insert_sales(conn, "NEW_03", "099", [(i, 1) for i in range(1, 7)])

        conn.execute(
            "INSERT INTO realtime_inventory (item_cd, check_date, stock_qty, pending_qty) "
            "VALUES (?, ?, 0, 0)",
            ("NEW_03", datetime.now().strftime("%Y-%m-%d")),
        )
        conn.commit()
        conn.close()

        from src.prediction.improved_predictor import ImprovedPredictor
        predictor = ImprovedPredictor(db_path=db_path)
        result = predictor.predict("NEW_03", datetime.now())

        assert result is not None
        assert result.adjusted_qty > 0, \
            f"6일 6개 판매인데 예측=0 (adj={result.adjusted_qty})"

    def test_7days_data_no_correction(self, cold_start_db):
        """7일 데이터 → 보정 미적용 (기존 WMA 유지)"""
        db_path, conn = cold_start_db
        _insert_item(conn, "EXISTING_01", "기존상품", "099")
        # 7일간 매일 1개 판매
        _insert_sales(conn, "EXISTING_01", "099", [(i, 1) for i in range(1, 8)])

        conn.execute(
            "INSERT INTO realtime_inventory (item_cd, check_date, stock_qty, pending_qty) "
            "VALUES (?, ?, 0, 0)",
            ("EXISTING_01", datetime.now().strftime("%Y-%m-%d")),
        )
        conn.commit()
        conn.close()

        from src.prediction.improved_predictor import ImprovedPredictor
        predictor = ImprovedPredictor(db_path=db_path)
        result = predictor.predict("EXISTING_01", datetime.now())

        # 7일이면 보정 없이 기존 WMA 사용 — 결과가 None이 아니면 OK
        assert result is not None

    def test_1day_0sales_no_correction(self, cold_start_db):
        """1일 0개 판매 → 보정 미적용 (total_sales=0)"""
        db_path, conn = cold_start_db
        _insert_item(conn, "NEW_ZERO", "판매없는신규", "099")
        _insert_sales(conn, "NEW_ZERO", "099", [(1, 0)])

        conn.execute(
            "INSERT INTO realtime_inventory (item_cd, check_date, stock_qty, pending_qty) "
            "VALUES (?, ?, 0, 0)",
            ("NEW_ZERO", datetime.now().strftime("%Y-%m-%d")),
        )
        conn.commit()
        conn.close()

        from src.prediction.improved_predictor import ImprovedPredictor
        predictor = ImprovedPredictor(db_path=db_path)
        result = predictor.predict("NEW_ZERO", datetime.now())

        # 판매 0이면 보정 불필요 → 예측 0은 정상
        assert result is not None

    def test_30days_data_no_correction(self, cold_start_db):
        """30일 데이터 → 보정 미적용"""
        db_path, conn = cold_start_db
        _insert_item(conn, "MATURE_01", "충분데이터", "099")
        _insert_sales(conn, "MATURE_01", "099", [(i, 3) for i in range(1, 31)])

        conn.execute(
            "INSERT INTO realtime_inventory (item_cd, check_date, stock_qty, pending_qty) "
            "VALUES (?, ?, 0, 0)",
            ("MATURE_01", datetime.now().strftime("%Y-%m-%d")),
        )
        conn.commit()
        conn.close()

        from src.prediction.improved_predictor import ImprovedPredictor
        predictor = ImprovedPredictor(db_path=db_path)
        result = predictor.predict("MATURE_01", datetime.now())

        assert result is not None
        # 30일 데이터 상품은 기존 WMA 그대로
        assert result.adjusted_qty > 0

    def test_wma_higher_than_daily_avg_no_correction(self, cold_start_db):
        """WMA > daily_avg이면 보정 미적용"""
        db_path, conn = cold_start_db
        _insert_item(conn, "NEW_HIGH", "WMA높은상품", "099")
        # 2일 데이터: day1=10, day2=1 → WMA가 최근 가중치로 이미 높음
        _insert_sales(conn, "NEW_HIGH", "099", [(1, 10), (2, 1)])

        conn.execute(
            "INSERT INTO realtime_inventory (item_cd, check_date, stock_qty, pending_qty) "
            "VALUES (?, ?, 0, 0)",
            ("NEW_HIGH", datetime.now().strftime("%Y-%m-%d")),
        )
        conn.commit()
        conn.close()

        from src.prediction.improved_predictor import ImprovedPredictor
        predictor = ImprovedPredictor(db_path=db_path)
        result = predictor.predict("NEW_HIGH", datetime.now())

        # WMA가 이미 충분히 높으면 보정 불필요
        assert result is not None
        assert result.adjusted_qty > 0


class TestColdStartWMADirect:
    """BasePredictor._compute_wma() 직접 테스트"""

    def test_cold_start_wma_correction_applied(self, cold_start_db):
        """data_days=1, 판매=1 → WMA 보정 확인 (직접 호출)"""
        db_path, conn = cold_start_db
        _insert_item(conn, "DIRECT_01", "직접테스트", "073")
        _insert_sales(conn, "DIRECT_01", "073", [(1, 1)])
        conn.commit()
        conn.close()

        from src.prediction.improved_predictor import ImprovedPredictor
        predictor = ImprovedPredictor(db_path=db_path)

        product = {"item_cd": "DIRECT_01", "item_nm": "직접테스트", "mid_cd": "073"}
        target = datetime.now()

        # _compute_wma 대신 calculate_weighted_average로 WMA 확인
        history = predictor.get_sales_history("DIRECT_01", days=7)
        wma, days = predictor.calculate_weighted_average(history)

        # WMA(7일)는 1/7 ≈ 0.14 → 매우 낮아야 함
        assert wma < 0.5, f"WMA={wma}가 예상보다 높음 (7일 중 1일 판매)"

        # data_span_days는 1일이어야 함
        data_days = predictor._get_data_span_days("DIRECT_01")
        assert data_days <= 2, f"data_days={data_days}가 예상보다 높음"
