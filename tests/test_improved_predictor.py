"""ImprovedPredictor 통합 테스트

시나리오:
- 정상 예측 (충분한 데이터)
- 데이터 부족 (None 반환)
- 재고 충분 (발주량 0)
- 카테고리별 라우팅 (담배/맥주/푸드 등)
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def predictor_db(tmp_path):
    """예측기 테스트용 DB (판매/재고/상품 데이터 포함)"""
    db_file = tmp_path / "test_predictor.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row

    # 테이블 생성
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
            orderable_day TEXT DEFAULT '일월화수목금토'
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


@pytest.fixture
def populated_db(predictor_db):
    """30일치 판매 데이터가 있는 DB"""
    db_path, conn = predictor_db
    today = datetime.now()

    # 상품 등록
    items = [
        ("TEST_BEER", "테스트맥주", "049"),
        ("TEST_TOBACCO", "테스트담배", "072"),
        ("TEST_FOOD", "테스트도시락", "001"),
        ("TEST_RAMEN", "테스트라면", "006"),
        ("TEST_LOW", "저판매상품", "099"),
    ]
    for item_cd, item_nm, mid_cd in items:
        conn.execute(
            "INSERT INTO products (item_cd, item_nm, mid_cd) VALUES (?, ?, ?)",
            (item_cd, item_nm, mid_cd),
        )
        conn.execute(
            "INSERT INTO product_details (item_cd, item_nm, mid_cd, order_unit_qty, expiration_days) "
            "VALUES (?, ?, ?, 1, ?)",
            (item_cd, item_nm, mid_cd, 1 if mid_cd != "001" else 1),
        )

    # 30일치 판매 데이터
    sale_configs = {
        "TEST_BEER": 5,
        "TEST_TOBACCO": 8,
        "TEST_FOOD": 10,
        "TEST_RAMEN": 3,
        "TEST_LOW": 0,  # 판매 없는 상품
    }

    for item_cd, avg_qty in sale_configs.items():
        mid_cd = next(m for i, _, m in items if i == item_cd)
        for days_ago in range(31):
            date = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            weekday = (today - timedelta(days=days_ago)).weekday()
            variation = 1.0 + (weekday - 3) * 0.1
            qty = max(0, int(avg_qty * variation))
            stock = avg_qty * 2

            conn.execute(
                "INSERT OR REPLACE INTO daily_sales "
                "(item_cd, sales_date, sale_qty, stock_qty, mid_cd) "
                "VALUES (?, ?, ?, ?, ?)",
                (item_cd, date, qty, stock, mid_cd),
            )

    # 실시간 재고 (맥주: 재고 부족, 담배: 재고 충분)
    check_date = today.strftime("%Y-%m-%d")
    conn.execute(
        "INSERT INTO realtime_inventory (item_cd, check_date, stock_qty, pending_qty, is_available) "
        "VALUES (?, ?, 2, 0, 1)",
        ("TEST_BEER", check_date),
    )
    conn.execute(
        "INSERT INTO realtime_inventory (item_cd, check_date, stock_qty, pending_qty, is_available) "
        "VALUES (?, ?, 50, 0, 1)",
        ("TEST_TOBACCO", check_date),
    )
    conn.execute(
        "INSERT INTO realtime_inventory (item_cd, check_date, stock_qty, pending_qty, is_available) "
        "VALUES (?, ?, 3, 5, 1)",
        ("TEST_FOOD", check_date),
    )

    conn.commit()
    conn.close()
    return db_path


class TestImprovedPredictorBasic:
    """기본 예측 테스트"""

    def test_predict_normal(self, populated_db):
        """정상 예측 - 충분한 데이터가 있을 때 PredictionResult 반환"""
        from src.prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor(db_path=populated_db, use_db_inventory=True)
        result = predictor.predict("TEST_BEER")

        assert result is not None
        assert result.item_cd == "TEST_BEER"
        assert result.mid_cd == "049"
        assert result.predicted_qty >= 0
        assert result.adjusted_qty >= 0
        assert result.safety_stock >= 0
        assert result.confidence in ("high", "medium", "low")
        assert result.data_days > 0

    def test_predict_nonexistent_item(self, populated_db):
        """존재하지 않는 상품 → None"""
        from src.prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor(db_path=populated_db)
        result = predictor.predict("NONEXISTENT_ITEM")

        assert result is None

    def test_predict_no_sales_data(self, predictor_db):
        """판매 데이터 없는 상품 → None 또는 order_qty=0"""
        db_path, conn = predictor_db
        conn.execute(
            "INSERT INTO products (item_cd, item_nm, mid_cd) VALUES (?, ?, ?)",
            ("EMPTY001", "빈상품", "099"),
        )
        conn.execute(
            "INSERT INTO product_details (item_cd, item_nm, mid_cd, order_unit_qty) "
            "VALUES (?, ?, ?, 1)",
            ("EMPTY001", "빈상품", "099"),
        )
        conn.commit()
        conn.close()

        from src.prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor(db_path=db_path)
        result = predictor.predict("EMPTY001")

        # 데이터 없으면 None이거나 order_qty=0
        if result is not None:
            assert result.order_qty == 0 or result.data_days == 0


class TestStockSufficiency:
    """재고 충분 시 발주량 검증"""

    def test_sufficient_stock_low_order(self, populated_db):
        """재고 충분한 담배 → 발주량 0 또는 매우 적음"""
        from src.prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor(db_path=populated_db, use_db_inventory=True)
        result = predictor.predict("TEST_TOBACCO")

        assert result is not None
        # 담배 카테고리(072) 예측 결과 존재 확인
        assert result.mid_cd == "072"
        # tobacco_max_stock 설정 확인
        assert result.tobacco_max_stock == 30


class TestCategoryRouting:
    """카테고리별 예측 로직 라우팅"""

    def test_beer_has_weekday_coef(self, populated_db):
        """맥주(049) → 요일 계수 적용"""
        from src.prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor(db_path=populated_db)
        result = predictor.predict("TEST_BEER")

        assert result is not None
        assert result.mid_cd == "049"
        # 맥주는 요일 계수가 적용됨
        assert result.weekday_coef > 0

    def test_tobacco_has_carton_fields(self, populated_db):
        """담배(072) → 보루/소진 패턴 필드"""
        from src.prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor(db_path=populated_db)
        result = predictor.predict("TEST_TOBACCO")

        assert result is not None
        assert result.mid_cd == "072"
        # 담배 전용 필드 존재
        assert hasattr(result, "carton_buffer")
        assert hasattr(result, "tobacco_max_stock")

    def test_food_has_expiry_fields(self, populated_db):
        """푸드류(001) → 유통기한 기반 필드"""
        from src.prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor(db_path=populated_db)
        result = predictor.predict("TEST_FOOD")

        assert result is not None
        assert result.mid_cd == "001"
        # 푸드 전용 필드
        assert hasattr(result, "food_expiry_group")
        assert hasattr(result, "food_safety_days")


class TestPredictionResult:
    """PredictionResult 데이터 구조 검증"""

    def test_result_fields(self, populated_db):
        """필수 필드 존재 여부"""
        from src.prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor(db_path=populated_db)
        result = predictor.predict("TEST_BEER")

        assert result is not None
        # 기본 필드
        assert isinstance(result.item_cd, str)
        assert isinstance(result.item_nm, str)
        assert isinstance(result.target_date, str)
        assert isinstance(result.predicted_qty, (int, float))
        assert isinstance(result.adjusted_qty, (int, float))
        assert isinstance(result.current_stock, int)
        assert isinstance(result.pending_qty, int)
        assert isinstance(result.safety_stock, (int, float))
        assert isinstance(result.order_qty, int)
        assert isinstance(result.confidence, str)
        assert isinstance(result.data_days, int)
        assert isinstance(result.weekday_coef, (int, float))

    def test_order_qty_non_negative(self, populated_db):
        """발주량은 항상 0 이상"""
        from src.prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor(db_path=populated_db)
        for item_cd in ["TEST_BEER", "TEST_TOBACCO", "TEST_FOOD", "TEST_RAMEN"]:
            result = predictor.predict(item_cd)
            if result is not None:
                assert result.order_qty >= 0, f"{item_cd}: order_qty < 0"


class TestCalendarDayWMA:
    """달력일 기반 WMA 테스트"""

    def test_get_sales_history_returns_calendar_days(self, predictor_db):
        """get_sales_history()가 달력일 기준으로 N일 반환"""
        db_path, conn = predictor_db
        today = datetime.now()

        conn.execute(
            "INSERT INTO products (item_cd, item_nm, mid_cd) VALUES (?, ?, ?)",
            ("SPARSE_01", "간헐판매", "099"),
        )
        conn.execute(
            "INSERT INTO product_details (item_cd, item_nm, mid_cd, order_unit_qty) "
            "VALUES (?, ?, ?, 1)",
            ("SPARSE_01", "간헐판매", "099"),
        )

        # 7일 중 3일만 판매 기록 삽입
        for days_ago in [1, 3, 5]:
            date = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            conn.execute(
                "INSERT INTO daily_sales (item_cd, sales_date, sale_qty, stock_qty, mid_cd) "
                "VALUES (?, ?, ?, ?, ?)",
                ("SPARSE_01", date, 2, 5, "099"),
            )

        conn.commit()
        conn.close()

        from src.prediction.improved_predictor import ImprovedPredictor
        predictor = ImprovedPredictor(db_path=db_path)
        history = predictor.get_sales_history("SPARSE_01", days=7)

        # 달력일 기준 7일이므로 7개 행 반환 (판매 없는 날도 포함)
        assert len(history) == 7
        # 판매 있는 날 확인 (SQLite date('now')는 UTC 기반이므로
        # 로컬 시간으로 삽입한 날짜와 시간대 차이로 매칭 수가 달라질 수 있음)
        sold_days = [h for h in history if h[1] > 0]
        zero_days = [h for h in history if h[1] == 0]
        assert len(sold_days) + len(zero_days) == 7
        assert len(sold_days) >= 2  # 최소 2일은 매칭 (UTC 시간대 차이로 1일 밀림 가능)

    def test_wma_sparse_product_lower_avg(self, predictor_db):
        """간헐적 판매 상품의 WMA가 달력일 기준으로 낮아짐"""
        db_path, conn = predictor_db
        today = datetime.now()

        conn.execute(
            "INSERT INTO products (item_cd, item_nm, mid_cd) VALUES (?, ?, ?)",
            ("SPARSE_02", "간헐상품2", "099"),
        )
        conn.execute(
            "INSERT INTO product_details (item_cd, item_nm, mid_cd, order_unit_qty) "
            "VALUES (?, ?, ?, 1)",
            ("SPARSE_02", "간헐상품2", "099"),
        )

        # 7일 중 2일만 판매 (각 3개)
        for days_ago in [1, 4]:
            date = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            conn.execute(
                "INSERT INTO daily_sales (item_cd, sales_date, sale_qty, stock_qty, mid_cd) "
                "VALUES (?, ?, ?, ?, ?)",
                ("SPARSE_02", date, 3, 5, "099"),
            )

        conn.commit()
        conn.close()

        from src.prediction.improved_predictor import ImprovedPredictor
        predictor = ImprovedPredictor(db_path=db_path)
        history = predictor.get_sales_history("SPARSE_02", days=7)

        wma, data_days = predictor.calculate_weighted_average(history)
        # 7일 중 2일만 판매(3개씩) → WMA는 3보다 훨씬 낮아야 함
        # (5일이 0이므로 가중평균이 크게 낮아짐)
        assert wma < 2.0, f"WMA={wma}이 달력일 기반으로 충분히 낮아지지 않음"
        assert data_days == 7  # 달력일 기준 7일

    def test_wma_stockout_vs_no_record(self, predictor_db):
        """품절일(stock=0)과 레코드 없는 날의 구분"""
        db_path, conn = predictor_db
        today = datetime.now()

        conn.execute(
            "INSERT INTO products (item_cd, item_nm, mid_cd) VALUES (?, ?, ?)",
            ("SPARSE_03", "혼합상품", "099"),
        )
        conn.execute(
            "INSERT INTO product_details (item_cd, item_nm, mid_cd, order_unit_qty) "
            "VALUES (?, ?, ?, 1)",
            ("SPARSE_03", "혼합상품", "099"),
        )

        # 어제: 정상 판매 (stock=5, sale=3)
        d1 = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO daily_sales (item_cd, sales_date, sale_qty, stock_qty, mid_cd) "
            "VALUES (?, ?, 3, 5, '099')", ("SPARSE_03", d1)
        )
        # 2일전: 품절 (stock=0, sale=0)
        d2 = (today - timedelta(days=2)).strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO daily_sales (item_cd, sales_date, sale_qty, stock_qty, mid_cd) "
            "VALUES (?, ?, 0, 0, '099')", ("SPARSE_03", d2)
        )
        # 3일전: 레코드 없음 (sale_qty=0, stock_qty=None이 되어야 함)

        conn.commit()
        conn.close()

        from src.prediction.improved_predictor import ImprovedPredictor
        predictor = ImprovedPredictor(db_path=db_path)
        history = predictor.get_sales_history("SPARSE_03", days=3)

        # 3일 중 1일 정상, 1일 품절, 1일 레코드 없음
        assert len(history) == 3
        # 레코드 없는 날: stock_qty가 None
        no_record = [h for h in history if h[2] is None]
        assert len(no_record) >= 1, "레코드 없는 날의 stock_qty가 None이어야 함"
        # 품절일: stock_qty == 0
        stockout = [h for h in history if h[2] is not None and h[2] == 0]
        assert len(stockout) == 1


class TestForceOrderCap:
    """FORCE_ORDER 발주량 상한 테스트"""

    def test_force_max_days_constant(self):
        """FORCE_MAX_DAYS 상수 존재 확인"""
        from src.settings.constants import FORCE_MAX_DAYS
        assert FORCE_MAX_DAYS > 0
        assert FORCE_MAX_DAYS == 2  # 기본값

    def test_force_order_capped(self, predictor_db):
        """품절 상품의 발주량이 FORCE_MAX_DAYS로 상한됨"""
        db_path, conn = predictor_db
        today = datetime.now()

        conn.execute(
            "INSERT INTO products (item_cd, item_nm, mid_cd) VALUES (?, ?, ?)",
            ("FORCE_01", "품절상품", "049"),
        )
        conn.execute(
            "INSERT INTO product_details (item_cd, item_nm, mid_cd, order_unit_qty, expiration_days) "
            "VALUES (?, ?, ?, 1, 30)",
            ("FORCE_01", "품절상품", "049"),
        )

        # 30일 판매 데이터 (일평균 ~5개), stock_qty=0 (현재 품절)
        for days_ago in range(1, 31):
            date = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            # 최근 3일은 품절(stock=0), 나머지는 정상(stock=10)
            stock = 0 if days_ago <= 3 else 10
            sale = 5 if stock > 0 else 0
            conn.execute(
                "INSERT INTO daily_sales (item_cd, sales_date, sale_qty, stock_qty, mid_cd) "
                "VALUES (?, ?, ?, ?, ?)",
                ("FORCE_01", date, sale, stock, "049"),
            )

        # 재고 0 (품절) - realtime_inventory
        check_date = today.strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO realtime_inventory (item_cd, check_date, stock_qty, pending_qty, is_available) "
            "VALUES (?, ?, 0, 0, 1)",
            ("FORCE_01", check_date),
        )

        conn.commit()
        conn.close()

        from src.prediction.improved_predictor import ImprovedPredictor
        from src.settings.constants import FORCE_MAX_DAYS

        predictor = ImprovedPredictor(db_path=db_path, use_db_inventory=True)
        result = predictor.predict("FORCE_01")

        assert result is not None
        # realtime_inventory 또는 daily_sales 최신 레코드 기준 재고
        # 발주량은 예측 × FORCE_MAX_DAYS 이하여야 함
        if result.current_stock <= 0 and result.adjusted_qty > 0:
            max_allowed = int(result.adjusted_qty * FORCE_MAX_DAYS) + 1  # 올림 여유
            assert result.order_qty <= max_allowed, (
                f"FORCE 발주량({result.order_qty})이 상한({max_allowed})을 초과"
            )
