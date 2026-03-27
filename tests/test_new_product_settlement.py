"""신제품 안착 판정 테스트.

테스트 항목:
--- 순수 함수 (8건) ---
1. test_analysis_window_ultra_short: 초단기(1일) → 14일 윈도우
2. test_analysis_window_short: 단기(7일) → 21일 윈도우
3. test_analysis_window_medium: 중기(30일) → 30일 윈도우
4. test_analysis_window_long: 장기(90일) → 45일 윈도우
5. test_score_high_all: 전 KPI 우수 → 100점
6. test_score_low_all: 전 KPI 불량 → 0점
7. test_score_short_expiry_weights: 단기 유통기한 가중치 변경
8. test_verdict_settled: 70+ → settled
9. test_verdict_extended: 50~69 + ext < 2 → extended
10. test_verdict_extended_limit: 50~69 + ext >= 2 → failed
11. test_verdict_failed: 49- → failed
--- Repository (3건) ---
12. test_update_settlement: 판정 결과 DB 저장
13. test_get_settlement_due: 판정 기한 도달 제품 조회
14. test_get_settlement_due_extended: extended 재판정 대상
--- Service (6건) ---
15. test_calc_velocity: velocity + ratio 계산 (similar_avg 있음)
16. test_calc_velocity_no_similar: similar_avg 없음 → 0.5 폴백
17. test_calc_velocity_fetches_similar: similar_avg=None → on-demand 계산+캐싱
18. test_calc_cv: 변동계수 계산
19. test_calc_cv_insufficient: 데이터 부족 → None
20. test_analyze_settled: 전체 플로우 → settled 판정
21. test_analyze_failed: 전체 플로우 → failed 판정
--- Monitor 연동 (2건) ---
22. test_monitor_run_includes_settlement: run() 반환에 settlement_results 포함
23. test_monitor_settlement_error_graceful: settlement 실패 → 빈 리스트
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.application.services.new_product_settlement_service import (
    get_analysis_window,
    calculate_settlement_score,
    determine_verdict,
    NewProductSettlementService,
    SettlementResult,
    MAX_EXTENSIONS,
)
from src.infrastructure.database.repos import (
    DetectedNewProductRepository,
    NewProductDailyTrackingRepository,
)


# ─── 순수 함수 테스트 ───


class TestAnalysisWindow:
    def test_ultra_short(self):
        window, phase1 = get_analysis_window(1)
        assert window == 14
        assert phase1 == 5

    def test_short(self):
        window, phase1 = get_analysis_window(7)
        assert window == 21
        assert phase1 == 7

    def test_medium(self):
        window, phase1 = get_analysis_window(30)
        assert window == 30
        assert phase1 == 10

    def test_long(self):
        window, phase1 = get_analysis_window(90)
        assert window == 45
        assert phase1 == 14

    def test_default(self):
        window, phase1 = get_analysis_window(-1)
        assert window == 30
        assert phase1 == 10


class TestSettlementScore:
    def test_high_all(self):
        score = calculate_settlement_score(
            velocity_ratio=1.0, cv=30.0, sellthrough_rate=90.0,
            expiry_risk='Low', rank=0.1, expiration_days=30,
        )
        assert score == 100.0  # 30+25+20+15+10

    def test_low_all(self):
        score = calculate_settlement_score(
            velocity_ratio=0.1, cv=100.0, sellthrough_rate=30.0,
            expiry_risk='High', rank=0.9, expiration_days=30,
        )
        assert score == 0.0

    def test_short_expiry_weights(self):
        """단기 유통기한(<=3일): 소진율/폐기 가중치 상향"""
        score = calculate_settlement_score(
            velocity_ratio=1.0, cv=30.0, sellthrough_rate=90.0,
            expiry_risk='Low', rank=0.1, expiration_days=1,
        )
        # 20+20+25+25+10 = 100
        assert score == 100.0

        # 중간점수 비교: 단기 vs 장기에서 폐기 High일 때 차이
        short = calculate_settlement_score(
            velocity_ratio=1.0, cv=30.0, sellthrough_rate=90.0,
            expiry_risk='High', rank=0.1, expiration_days=1,
        )
        long = calculate_settlement_score(
            velocity_ratio=1.0, cv=30.0, sellthrough_rate=90.0,
            expiry_risk='High', rank=0.1, expiration_days=30,
        )
        # 단기: 폐기 25점 손실 vs 장기: 폐기 15점 손실
        assert short < long

    def test_mid_velocity(self):
        score = calculate_settlement_score(
            velocity_ratio=0.6, cv=60.0, sellthrough_rate=70.0,
            expiry_risk='Mid', rank=0.5, expiration_days=30,
        )
        # 15 + 12.5 + 10 + 7.5 + 5 = 50
        assert score == 50.0


class TestVerdict:
    def test_settled(self):
        assert determine_verdict(70, 0) == 'settled'
        assert determine_verdict(100, 2) == 'settled'

    def test_extended(self):
        assert determine_verdict(55, 0) == 'extended'
        assert determine_verdict(69, 1) == 'extended'

    def test_extended_limit(self):
        assert determine_verdict(55, MAX_EXTENSIONS) == 'failed'
        assert determine_verdict(65, 3) == 'failed'

    def test_failed(self):
        assert determine_verdict(49, 0) == 'failed'
        assert determine_verdict(0, 0) == 'failed'


# ─── DB fixtures ───


@pytest.fixture
def settlement_db(tmp_path):
    """안착 판정용 테스트 DB"""
    db_path = tmp_path / "stores" / "46513.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE detected_new_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            mid_cd TEXT,
            mid_cd_source TEXT DEFAULT 'fallback',
            first_receiving_date TEXT NOT NULL,
            receiving_qty INTEGER DEFAULT 0,
            order_unit_qty INTEGER DEFAULT 1,
            center_cd TEXT, center_nm TEXT, cust_nm TEXT,
            registered_to_products INTEGER DEFAULT 0,
            registered_to_details INTEGER DEFAULT 0,
            registered_to_inventory INTEGER DEFAULT 0,
            detected_at TEXT NOT NULL,
            store_id TEXT,
            lifecycle_status TEXT DEFAULT 'detected',
            monitoring_start_date TEXT,
            monitoring_end_date TEXT,
            total_sold_qty INTEGER DEFAULT 0,
            sold_days INTEGER DEFAULT 0,
            similar_item_avg REAL,
            status_changed_at TEXT,
            analysis_window_days INTEGER,
            extension_count INTEGER DEFAULT 0,
            settlement_score REAL,
            settlement_verdict TEXT,
            settlement_date TEXT,
            settlement_checked_at TEXT,
            UNIQUE(item_cd, first_receiving_date)
        )
    """)

    conn.execute("""
        CREATE TABLE new_product_daily_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT NOT NULL,
            tracking_date TEXT NOT NULL,
            sales_qty INTEGER DEFAULT 0,
            stock_qty INTEGER DEFAULT 0,
            order_qty INTEGER DEFAULT 0,
            store_id TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(item_cd, tracking_date, store_id)
        )
    """)

    conn.execute("""
        CREATE TABLE daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at TEXT, sales_date TEXT NOT NULL,
            item_cd TEXT NOT NULL, mid_cd TEXT,
            sale_qty INTEGER DEFAULT 0, ord_qty INTEGER DEFAULT 0,
            buy_qty INTEGER DEFAULT 0, disuse_qty INTEGER DEFAULT 0,
            stock_qty INTEGER DEFAULT 0, created_at TEXT,
            promo_type TEXT DEFAULT '', store_id TEXT,
            UNIQUE(sales_date, item_cd)
        )
    """)

    conn.execute("""
        CREATE TABLE realtime_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT NOT NULL,
            stock_qty INTEGER DEFAULT 0,
            is_available INTEGER DEFAULT 1,
            store_id TEXT
        )
    """)

    conn.commit()

    # common DB
    common_path = tmp_path / "common.db"
    common_conn = sqlite3.connect(str(common_path))
    common_conn.execute("""
        CREATE TABLE products (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT, mid_cd TEXT
        )
    """)
    common_conn.execute("""
        CREATE TABLE product_details (
            item_cd TEXT PRIMARY KEY,
            expiration_days INTEGER,
            large_cd TEXT, small_cd TEXT, small_nm TEXT, class_nm TEXT
        )
    """)
    common_conn.commit()
    common_conn.close()

    yield {
        "db_path": db_path,
        "common_path": common_path,
        "conn": conn,
    }
    conn.close()


def _insert_new_product(conn, item_cd, mid_cd="010", days_ago=30,
                         status="monitoring", sold_qty=20, sold_days=10,
                         similar_avg=2.0, window_days=30, ext_count=0,
                         verdict=None):
    """테스트용 신제품 삽입"""
    first_date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    start_date = first_date
    now = datetime.now().isoformat()

    conn.execute("""
        INSERT INTO detected_new_products
        (item_cd, item_nm, mid_cd, mid_cd_source, first_receiving_date,
         receiving_qty, detected_at, store_id, lifecycle_status,
         monitoring_start_date, total_sold_qty, sold_days,
         similar_item_avg, analysis_window_days, extension_count,
         settlement_verdict)
        VALUES (?, ?, ?, 'fallback', ?, 10, ?, '46513', ?, ?, ?, ?, ?, ?, ?, ?)
    """, (item_cd, f"상품_{item_cd}", mid_cd, first_date, now, status,
          start_date, sold_qty, sold_days, similar_avg, window_days,
          ext_count, verdict))
    conn.commit()


def _insert_tracking(conn, item_cd, days, base_qty=3):
    """일별 추적 데이터 삽입"""
    for i in range(days):
        date = (datetime.now() - timedelta(days=days - i - 1)).strftime("%Y-%m-%d")
        qty = base_qty + (i % 3)  # 약간의 변동
        conn.execute("""
            INSERT INTO new_product_daily_tracking
            (item_cd, tracking_date, sales_qty, stock_qty, order_qty,
             store_id, created_at)
            VALUES (?, ?, ?, 5, 0, '46513', ?)
        """, (item_cd, date, qty, datetime.now().isoformat()))
    conn.commit()


def _insert_daily_sales(conn, item_cd, mid_cd="010", days=30,
                         avg_sale=3, avg_buy=4):
    """daily_sales 데이터 삽입"""
    for i in range(days):
        date = (datetime.now() - timedelta(days=days - i - 1)).strftime("%Y-%m-%d")
        conn.execute("""
            INSERT INTO daily_sales
            (collected_at, sales_date, item_cd, mid_cd, sale_qty,
             buy_qty, stock_qty, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 5, ?)
        """, (datetime.now().isoformat(), date, item_cd, mid_cd,
              avg_sale, avg_buy, datetime.now().isoformat()))
    conn.commit()


def _insert_common_product(common_path, item_cd, mid_cd="010", expiry_days=30):
    """공통 DB에 상품 등록"""
    conn = sqlite3.connect(str(common_path))
    conn.execute("INSERT OR IGNORE INTO products (item_cd, item_nm, mid_cd) VALUES (?, ?, ?)",
                 (item_cd, f"상품_{item_cd}", mid_cd))
    conn.execute("INSERT OR IGNORE INTO product_details (item_cd, expiration_days) VALUES (?, ?)",
                 (item_cd, expiry_days))
    conn.commit()
    conn.close()


# ─── Repository 테스트 ───


class TestSettlementRepository:
    def test_update_settlement(self, settlement_db):
        conn = settlement_db["conn"]
        _insert_new_product(conn, "ITEM001")

        from src.infrastructure.database.repos import DetectedNewProductRepository
        repo = DetectedNewProductRepository(db_path=settlement_db["db_path"])

        repo.update_settlement(
            item_cd="ITEM001",
            settlement_score=75.0,
            settlement_verdict="settled",
            settlement_date="2026-03-08",
            analysis_window_days=30,
        )

        row = conn.execute(
            "SELECT * FROM detected_new_products WHERE item_cd = 'ITEM001'"
        ).fetchone()

        assert row["settlement_score"] == 75.0
        assert row["settlement_verdict"] == "settled"
        assert row["settlement_date"] == "2026-03-08"
        assert row["settlement_checked_at"] is not None

    def test_get_settlement_due(self, settlement_db):
        conn = settlement_db["conn"]
        # 판정 기한 도달 (45일 전 입점, 윈도우 30일)
        _insert_new_product(conn, "ITEM_DUE", days_ago=45, window_days=30)
        # 아직 미도달 (5일 전 입점, 윈도우 30일)
        _insert_new_product(conn, "ITEM_NOT_DUE", days_ago=5, window_days=30)

        from src.infrastructure.database.repos import DetectedNewProductRepository
        repo = DetectedNewProductRepository(db_path=settlement_db["db_path"])

        today = datetime.now().strftime("%Y-%m-%d")
        due = repo.get_settlement_due(today)

        item_cds = [d["item_cd"] for d in due]
        assert "ITEM_DUE" in item_cds
        assert "ITEM_NOT_DUE" not in item_cds

    def test_get_settlement_due_extended(self, settlement_db):
        conn = settlement_db["conn"]
        # extended → 재판정 대상
        _insert_new_product(conn, "ITEM_EXT", days_ago=60, window_days=45,
                             verdict="extended", ext_count=1)

        from src.infrastructure.database.repos import DetectedNewProductRepository
        repo = DetectedNewProductRepository(db_path=settlement_db["db_path"])

        today = datetime.now().strftime("%Y-%m-%d")
        due = repo.get_settlement_due(today)

        item_cds = [d["item_cd"] for d in due]
        assert "ITEM_EXT" in item_cds


# ─── Service 테스트 ───


class TestSettlementService:
    def test_calc_velocity(self):
        """velocity + ratio 계산 검증 (similar_avg 있음 → 즉시 계산)"""
        service = NewProductSettlementService.__new__(NewProductSettlementService)
        cursor = MagicMock()
        v, ratio = service._calc_velocity(cursor, {
            "total_sold_qty": 30,
            "sold_days": 10,
            "similar_item_avg": 2.0,
        })
        assert v == 3.0
        assert ratio == 1.5

    def test_calc_velocity_no_similar(self):
        """similar_avg 없음 → _fetch_similar_avg 호출 → 결과 없으면 0.5"""
        service = NewProductSettlementService.__new__(NewProductSettlementService)
        service.store_id = "46513"
        service.new_product_repo = MagicMock()

        # cursor: small_cd 조회 None, similar 조회 결과 없음
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []

        v, ratio = service._calc_velocity(cursor, {
            "item_cd": "ITEM_TEST",
            "total_sold_qty": 10,
            "sold_days": 5,
            "similar_item_avg": None,
            "mid_cd": "010",
        })
        assert v == 2.0
        assert ratio == 0.5  # 유사상품 없음 → 중간값

    def test_calc_velocity_fetches_similar(self, settlement_db):
        """similar_avg=None → on-demand 계산 + DB 캐싱"""
        conn = settlement_db["conn"]
        _insert_new_product(conn, "ITEM_FETCH", mid_cd="010", days_ago=20,
                             sold_qty=15, sold_days=5, similar_avg=None)
        # 카테고리 비교용 상품 daily_sales (mid_cd=010, 5개)
        common_conn = sqlite3.connect(str(settlement_db["common_path"]))
        for i in range(5):
            icd = f"SIM{i:03d}"
            common_conn.execute(
                "INSERT OR IGNORE INTO products VALUES (?, ?, '010')",
                (icd, f"유사_{i}"),
            )
            common_conn.execute(
                "INSERT OR IGNORE INTO product_details (item_cd, expiration_days) VALUES (?, 30)",
                (icd,),
            )
        common_conn.commit()
        common_conn.close()

        for i in range(5):
            for d in range(10):
                date = (datetime.now() - timedelta(days=10 - d)).strftime("%Y-%m-%d")
                conn.execute("""
                    INSERT OR IGNORE INTO daily_sales
                    (collected_at, sales_date, item_cd, mid_cd, sale_qty,
                     buy_qty, stock_qty, created_at)
                    VALUES (?, ?, ?, '010', ?, 3, 5, ?)
                """, (datetime.now().isoformat(), date, f"SIM{i:03d}",
                      2 + i, datetime.now().isoformat()))
        conn.commit()

        attached = sqlite3.connect(str(settlement_db["db_path"]))
        attached.row_factory = sqlite3.Row
        attached.execute(f"ATTACH DATABASE '{settlement_db['common_path']}' AS common")
        cursor = attached.cursor()

        service = NewProductSettlementService.__new__(NewProductSettlementService)
        service.store_id = "46513"
        service.new_product_repo = MagicMock()

        v, ratio = service._calc_velocity(cursor, {
            "item_cd": "ITEM_FETCH",
            "total_sold_qty": 15,
            "sold_days": 5,
            "similar_item_avg": None,
            "mid_cd": "010",
        })

        assert v == 3.0  # 15/5
        # similar_avg 계산됨 → ratio != 0.5
        assert ratio != 0.5
        assert ratio > 0
        # DB에 캐싱됨
        service.new_product_repo.update_lifecycle.assert_called_once()
        call_kwargs = service.new_product_repo.update_lifecycle.call_args
        assert call_kwargs.kwargs.get("similar_item_avg") is not None

        attached.close()

    def test_calc_cv(self, settlement_db):
        conn = settlement_db["conn"]
        _insert_tracking(conn, "ITEM_CV", days=10, base_qty=5)

        service = NewProductSettlementService.__new__(NewProductSettlementService)
        service.store_id = "46513"
        service.tracking_repo = MagicMock()

        # 모의 데이터: 안정적인 판매 (날짜 포함)
        service.tracking_repo.get_tracking_history.return_value = [
            {"tracking_date": "2026-03-03", "sales_qty": 5},
            {"tracking_date": "2026-03-04", "sales_qty": 6},
            {"tracking_date": "2026-03-05", "sales_qty": 5},
            {"tracking_date": "2026-03-06", "sales_qty": 7},
            {"tracking_date": "2026-03-07", "sales_qty": 5},
        ]

        cv, threshold = service._calc_cv("ITEM_CV")
        assert cv is not None
        assert cv < 50  # 안정적
        assert threshold in (80, 90)

    def test_calc_cv_insufficient(self, settlement_db):
        service = NewProductSettlementService.__new__(NewProductSettlementService)
        service.store_id = "46513"
        service.tracking_repo = MagicMock()
        service.tracking_repo.get_tracking_history.return_value = [
            {"tracking_date": "2026-03-07", "sales_qty": 1},
            {"tracking_date": "2026-03-08", "sales_qty": 0},
        ]

        cv, threshold = service._calc_cv("ITEM_FEW")
        assert cv is None

    def _make_attached_conn(self, settlement_db):
        """store DB + common ATTACH 연결 생성"""
        store_conn = sqlite3.connect(str(settlement_db["db_path"]))
        store_conn.row_factory = sqlite3.Row
        store_conn.execute(
            f"ATTACH DATABASE '{settlement_db['common_path']}' AS common"
        )
        return store_conn

    def test_analyze_settled(self, settlement_db):
        """전체 플로우 → settled 판정"""
        conn = settlement_db["conn"]
        _insert_new_product(conn, "ITEM_GOOD", mid_cd="010", days_ago=35,
                             sold_qty=30, sold_days=15, similar_avg=2.0,
                             window_days=30)
        _insert_tracking(conn, "ITEM_GOOD", days=15, base_qty=5)
        _insert_daily_sales(conn, "ITEM_GOOD", avg_sale=5, avg_buy=6)
        conn.execute("""
            INSERT INTO realtime_inventory (item_cd, stock_qty, is_available, store_id)
            VALUES ('ITEM_GOOD', 3, 1, '46513')
        """)
        conn.commit()

        _insert_common_product(settlement_db["common_path"], "ITEM_GOOD",
                                expiry_days=30)

        # 카테고리 비교용 다른 상품 추가
        common_conn = sqlite3.connect(str(settlement_db["common_path"]))
        for i in range(5):
            common_conn.execute(
                "INSERT OR IGNORE INTO products VALUES (?, ?, '010')",
                (f"OTHER{i}", f"기존_{i}"),
            )
        common_conn.commit()
        common_conn.close()

        for i in range(5):
            for d in range(30):
                date = (datetime.now() - timedelta(days=30 - d)).strftime("%Y-%m-%d")
                conn.execute("""
                    INSERT OR IGNORE INTO daily_sales
                    (collected_at, sales_date, item_cd, mid_cd, sale_qty,
                     buy_qty, stock_qty, created_at)
                    VALUES (?, ?, ?, '010', ?, 3, 5, ?)
                """, (datetime.now().isoformat(), date, f"OTHER{i}",
                      2 + i % 3, datetime.now().isoformat()))
        conn.commit()

        attached_conn = self._make_attached_conn(settlement_db)

        service = NewProductSettlementService(store_id="46513")
        service._get_analysis_conn = lambda: attached_conn
        service.new_product_repo = DetectedNewProductRepository(
            db_path=settlement_db["db_path"]
        )
        service.tracking_repo = NewProductDailyTrackingRepository(
            db_path=settlement_db["db_path"]
        )

        result = service.analyze("ITEM_GOOD")

        assert result is not None
        assert result.verdict == "settled"
        assert result.score >= 70
        assert result.velocity > 0
        assert result.velocity_ratio > 0

    def test_analyze_failed(self, settlement_db):
        """전체 플로우 → failed 판정 (판매 0)"""
        conn = settlement_db["conn"]
        _insert_new_product(conn, "ITEM_BAD", mid_cd="010", days_ago=35,
                             sold_qty=0, sold_days=0, similar_avg=2.0,
                             window_days=30)
        _insert_daily_sales(conn, "ITEM_BAD", avg_sale=0, avg_buy=10)
        conn.execute("""
            INSERT INTO realtime_inventory (item_cd, stock_qty, is_available, store_id)
            VALUES ('ITEM_BAD', 20, 1, '46513')
        """)
        conn.commit()

        _insert_common_product(settlement_db["common_path"], "ITEM_BAD",
                                expiry_days=30)

        attached_conn = self._make_attached_conn(settlement_db)

        service = NewProductSettlementService(store_id="46513")
        service._get_analysis_conn = lambda: attached_conn
        service.new_product_repo = DetectedNewProductRepository(
            db_path=settlement_db["db_path"]
        )
        service.tracking_repo = NewProductDailyTrackingRepository(
            db_path=settlement_db["db_path"]
        )

        result = service.analyze("ITEM_BAD")

        assert result is not None
        assert result.verdict == "failed"
        assert result.score < 50


# ─── Monitor 연동 테스트 ───


class TestMonitorSettlement:
    @patch("src.application.services.new_product_monitor.NewProductMonitor._get_daily_sales")
    @patch("src.application.services.new_product_monitor.NewProductMonitor._get_stock_map")
    @patch("src.application.services.new_product_monitor.NewProductMonitor._get_order_map")
    @patch("src.application.services.new_product_settlement_service.NewProductSettlementService.run_due_settlements")
    def test_monitor_run_includes_settlement(
        self, mock_settle, mock_order, mock_stock, mock_sales
    ):
        """run() 반환에 settlement_results 포함"""
        mock_sales.return_value = {}
        mock_stock.return_value = {}
        mock_order.return_value = {}
        mock_settle.return_value = []

        monitor = MagicMock()
        monitor.store_id = "46513"

        # 실제 run 메서드 대신 반환 구조 확인
        from src.application.services.new_product_monitor import NewProductMonitor
        m = NewProductMonitor.__new__(NewProductMonitor)
        m.store_id = "46513"
        m.detect_repo = MagicMock()
        m.tracking_repo = MagicMock()
        m.detect_repo.get_by_lifecycle_status.return_value = []

        result = m.run()
        assert "settlement_results" in result

    def test_monitor_settlement_error_graceful(self):
        """settlement 실패 → 빈 리스트"""
        from src.application.services.new_product_monitor import NewProductMonitor
        m = NewProductMonitor.__new__(NewProductMonitor)
        m.store_id = "46513"

        with patch(
            "src.application.services.new_product_settlement_service.NewProductSettlementService.run_due_settlements",
            side_effect=Exception("DB error"),
        ):
            result = m._run_settlement()
            assert result == []
