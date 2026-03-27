"""신제품 초기발주 small_cd(소분류) 기반 유사상품 매칭 테스트.

Design Reference: docs/02-design/features/new-product-smallcd.design.md
테스트 항목:
--- calculate_similar_avg small_cd 지원 (5건) ---
1. test_smallcd_similar_avg: small_cd 기반 유사상품 중위값 정확성
2. test_smallcd_fallback_insufficient: small_cd 내 < 3개 -> mid_cd 폴백
3. test_smallcd_fallback_null: small_cd=NULL -> mid_cd 폴백
4. test_smallcd_only_same_category: small_cd 매칭 시 다른 small_cd 배제
5. test_smallcd_empty_string: small_cd="" -> mid_cd 폴백
--- _get_small_cd (2건) ---
6. test_get_small_cd_exists: 정상 조회
7. test_get_small_cd_not_found: 미등록 -> None
--- Boost with small_cd (3건) ---
8. test_smallcd_boost_applied: small_cd 기반 similar_avg로 부스트 적용
9. test_smallcd_boost_with_midcd_fallback: 폴백 시 mid_cd 기반 부스트 적용
10. test_boost_log_includes_smallcd: 로그 메시지에 small_cd 포함
--- Cache enrichment (2건) ---
11. test_smallcd_cache_enrichment: 캐시에 small_cd 정보 포함
12. test_smallcd_mixed_scenario: small_cd 있는 상품 + 없는 상품 혼합
"""

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest


# ── DB fixtures ──────────────────────────────────────

@pytest.fixture
def smallcd_db(tmp_path):
    """테스트용 store DB + common DB (small_cd 관련 테이블 전체)."""
    # store DB
    store_dir = tmp_path / "stores"
    store_dir.mkdir(parents=True, exist_ok=True)
    store_path = store_dir / "46513.db"

    conn = sqlite3.connect(str(store_path))
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
            UNIQUE(item_cd, first_receiving_date)
        )
    """)

    conn.execute("""
        CREATE TABLE daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sales_date TEXT, item_cd TEXT,
            sale_qty INTEGER DEFAULT 0,
            mid_cd TEXT, store_id TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE realtime_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT, stock_qty INTEGER DEFAULT 0,
            is_available INTEGER DEFAULT 1, store_id TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE order_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_date TEXT, item_cd TEXT,
            order_qty INTEGER DEFAULT 0, store_id TEXT
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

    conn.commit()
    conn.close()

    # common DB
    common_path = tmp_path / "common.db"
    cconn = sqlite3.connect(str(common_path))
    cconn.row_factory = sqlite3.Row

    cconn.execute("""
        CREATE TABLE products (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            mid_cd TEXT,
            updated_at TEXT
        )
    """)

    cconn.execute("""
        CREATE TABLE product_details (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            expiration_days INTEGER,
            orderable_day TEXT,
            orderable_status TEXT,
            order_unit_name TEXT,
            order_unit_qty INTEGER DEFAULT 1,
            case_unit_qty INTEGER DEFAULT 1,
            lead_time_days INTEGER DEFAULT 1,
            sell_price INTEGER,
            margin_rate REAL,
            fetched_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            large_cd TEXT,
            small_cd TEXT,
            small_nm TEXT,
            class_nm TEXT
        )
    """)

    cconn.commit()
    cconn.close()

    return {"store": store_path, "common": common_path, "tmp": tmp_path}


def _make_conn(db_info, attach_common=True):
    """store DB 커넥션 (common ATTACH 포함)."""
    conn = sqlite3.connect(str(db_info["store"]))
    conn.row_factory = sqlite3.Row
    if attach_common:
        conn.execute(f"ATTACH DATABASE '{db_info['common']}' AS common")
    return conn


def _insert_product(db_info, item_cd, mid_cd, small_cd=None, small_nm=None):
    """products + product_details 삽입."""
    now = datetime.now().isoformat()
    cconn = sqlite3.connect(str(db_info["common"]))
    cconn.execute(
        "INSERT OR REPLACE INTO products (item_cd, item_nm, mid_cd, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (item_cd, f"상품_{item_cd}", mid_cd, now),
    )
    cconn.execute(
        "INSERT OR REPLACE INTO product_details "
        "(item_cd, item_nm, small_cd, small_nm, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (item_cd, f"상품_{item_cd}", small_cd, small_nm, now, now),
    )
    cconn.commit()
    cconn.close()


def _insert_daily_sales(db_info, item_cd, days=10, daily_qty=3):
    """daily_sales 데이터 삽입."""
    conn = sqlite3.connect(str(db_info["store"]))
    for day in range(days):
        date = (datetime.now() - timedelta(days=day)).strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO daily_sales (sales_date, item_cd, sale_qty) VALUES (?, ?, ?)",
            (date, item_cd, daily_qty),
        )
    conn.commit()
    conn.close()


def _insert_detected(db_info, item_cd, mid_cd="001", status="detected",
                     monitoring_start=None, similar_avg=None, store_id="46513"):
    """detected_new_products 삽입."""
    conn = sqlite3.connect(str(db_info["store"]))
    conn.execute(
        """INSERT INTO detected_new_products
           (item_cd, item_nm, mid_cd, mid_cd_source, first_receiving_date,
            receiving_qty, detected_at, store_id, lifecycle_status,
            monitoring_start_date, similar_item_avg)
           VALUES (?, ?, ?, 'fallback', '2026-02-10', 10, ?, ?, ?, ?, ?)""",
        (item_cd, f"테스트_{item_cd}", mid_cd,
         datetime.now().isoformat(), store_id, status, monitoring_start, similar_avg),
    )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════
# 1. calculate_similar_avg small_cd 지원 (5건)
# ═══════════════════════════════════════════════════════

class TestSmallCdSimilarAvg:
    """calculate_similar_avg의 small_cd 우선 매칭 + 폴백."""

    def _make_monitor(self, db_info):
        """NewProductMonitor 생성 (DB 경로 주입)."""
        from src.infrastructure.database.repos.detected_new_product_repo import (
            DetectedNewProductRepository,
        )
        from src.infrastructure.database.repos.np_tracking_repo import (
            NewProductDailyTrackingRepository,
        )
        from src.application.services.new_product_monitor import NewProductMonitor

        monitor = NewProductMonitor.__new__(NewProductMonitor)
        monitor.store_id = "46513"
        monitor.detect_repo = DetectedNewProductRepository(db_path=db_info["store"])
        monitor.tracking_repo = NewProductDailyTrackingRepository(db_path=db_info["store"])
        return monitor

    def test_smallcd_similar_avg(self, smallcd_db):
        """small_cd 기반 유사상품 중위값 정확성 (>= 3개)."""
        # 신제품 (대상)
        _insert_product(smallcd_db, "NEW001", "001", small_cd="001", small_nm="정식도시락")

        # 같은 mid_cd + small_cd 유사상품 3개
        for i, qty in enumerate([3, 5, 7], start=1):
            item = f"SIM00{i}"
            _insert_product(smallcd_db, item, "001", small_cd="001", small_nm="정식도시락")
            _insert_daily_sales(smallcd_db, item, days=10, daily_qty=qty)

        # 같은 mid_cd 다른 small_cd 상품 (noise)
        _insert_product(smallcd_db, "OTHER01", "001", small_cd="002", small_nm="한끼도시락")
        _insert_daily_sales(smallcd_db, "OTHER01", days=10, daily_qty=20)

        monitor = self._make_monitor(smallcd_db)

        with patch("src.infrastructure.database.connection.DBRouter") as mock_router, \
             patch("src.infrastructure.database.connection.attach_common_with_views") as mock_attach:
            mock_router.get_store_connection.side_effect = lambda sid: _make_conn(smallcd_db)
            mock_attach.side_effect = lambda conn, sid: conn

            result = monitor.calculate_similar_avg("NEW001", "001", small_cd="001")

        # 중위값: median([3.0, 5.0, 7.0]) = 5.0
        assert result is not None
        assert result == 5.0

    def test_smallcd_fallback_insufficient(self, smallcd_db):
        """small_cd 내 < 3개 -> mid_cd 폴백."""
        _insert_product(smallcd_db, "NEW001", "001", small_cd="001")

        # small_cd=001 유사상품 2개 (< 3)
        for i, qty in enumerate([3, 5], start=1):
            item = f"SIM00{i}"
            _insert_product(smallcd_db, item, "001", small_cd="001")
            _insert_daily_sales(smallcd_db, item, days=10, daily_qty=qty)

        # mid_cd=001 다른 small_cd 상품 3개 (폴백 대상)
        for i, qty in enumerate([10, 12, 14], start=1):
            item = f"OTHER0{i}"
            _insert_product(smallcd_db, item, "001", small_cd="002")
            _insert_daily_sales(smallcd_db, item, days=10, daily_qty=qty)

        monitor = self._make_monitor(smallcd_db)

        with patch("src.infrastructure.database.connection.DBRouter") as mock_router, \
             patch("src.infrastructure.database.connection.attach_common_with_views") as mock_attach:
            mock_router.get_store_connection.side_effect = lambda sid: _make_conn(smallcd_db)
            mock_attach.side_effect = lambda conn, sid: conn

            result = monitor.calculate_similar_avg("NEW001", "001", small_cd="001")

        # mid_cd 폴백 → 5개 상품 전체: median([3,5,10,12,14]) = 10.0
        assert result is not None
        assert result == 10.0

    def test_smallcd_fallback_null(self, smallcd_db):
        """small_cd=None -> mid_cd 폴백."""
        _insert_product(smallcd_db, "NEW001", "001", small_cd=None)

        for i, qty in enumerate([4, 6, 8], start=1):
            item = f"SIM00{i}"
            _insert_product(smallcd_db, item, "001", small_cd="001")
            _insert_daily_sales(smallcd_db, item, days=10, daily_qty=qty)

        monitor = self._make_monitor(smallcd_db)

        with patch("src.infrastructure.database.connection.DBRouter") as mock_router, \
             patch("src.infrastructure.database.connection.attach_common_with_views") as mock_attach:
            mock_router.get_store_connection.side_effect = lambda sid: _make_conn(smallcd_db)
            mock_attach.side_effect = lambda conn, sid: conn

            result = monitor.calculate_similar_avg("NEW001", "001", small_cd=None)

        # mid_cd 폴백: median([4,6,8]) = 6.0
        assert result == 6.0

    def test_smallcd_only_same_category(self, smallcd_db):
        """small_cd 매칭 시 다른 small_cd 상품이 결과에 포함되지 않는지 확인."""
        _insert_product(smallcd_db, "NEW001", "001", small_cd="001")

        # small_cd=001: 높은 판매량 3개
        for i, qty in enumerate([10, 10, 10], start=1):
            item = f"A00{i}"
            _insert_product(smallcd_db, item, "001", small_cd="001")
            _insert_daily_sales(smallcd_db, item, days=10, daily_qty=qty)

        # small_cd=002: 매우 낮은 판매량 (포함되면 중위값 크게 변동)
        for i, qty in enumerate([1, 1, 1], start=1):
            item = f"B00{i}"
            _insert_product(smallcd_db, item, "001", small_cd="002")
            _insert_daily_sales(smallcd_db, item, days=10, daily_qty=qty)

        monitor = self._make_monitor(smallcd_db)

        with patch("src.infrastructure.database.connection.DBRouter") as mock_router, \
             patch("src.infrastructure.database.connection.attach_common_with_views") as mock_attach:
            mock_router.get_store_connection.side_effect = lambda sid: _make_conn(smallcd_db)
            mock_attach.side_effect = lambda conn, sid: conn

            result = monitor.calculate_similar_avg("NEW001", "001", small_cd="001")

        # small_cd=001만: median([10,10,10]) = 10.0 (002가 포함되면 다른 값)
        assert result == 10.0

    def test_smallcd_empty_string(self, smallcd_db):
        """small_cd="" -> mid_cd 폴백."""
        _insert_product(smallcd_db, "NEW001", "001", small_cd="")

        for i, qty in enumerate([2, 4, 6], start=1):
            item = f"SIM00{i}"
            _insert_product(smallcd_db, item, "001", small_cd="001")
            _insert_daily_sales(smallcd_db, item, days=10, daily_qty=qty)

        monitor = self._make_monitor(smallcd_db)

        with patch("src.infrastructure.database.connection.DBRouter") as mock_router, \
             patch("src.infrastructure.database.connection.attach_common_with_views") as mock_attach:
            mock_router.get_store_connection.side_effect = lambda sid: _make_conn(smallcd_db)
            mock_attach.side_effect = lambda conn, sid: conn

            result = monitor.calculate_similar_avg("NEW001", "001", small_cd="")

        # empty string -> mid_cd 폴백: median([2,4,6]) = 4.0
        assert result == 4.0


# ═══════════════════════════════════════════════════════
# 2. _get_small_cd (2건)
# ═══════════════════════════════════════════════════════

class TestGetSmallCd:
    """_get_small_cd 메서드 테스트."""

    def test_get_small_cd_exists(self, smallcd_db):
        """small_cd 정상 조회."""
        _insert_product(smallcd_db, "ITEM001", "001", small_cd="003", small_nm="용기도시락")

        from src.application.services.new_product_monitor import NewProductMonitor
        monitor = NewProductMonitor.__new__(NewProductMonitor)
        monitor.store_id = "46513"

        with patch("src.infrastructure.database.connection.DBRouter") as mock_router:
            def make_common_conn():
                c = sqlite3.connect(str(smallcd_db["common"]))
                c.row_factory = sqlite3.Row
                return c
            mock_router.get_common_connection.side_effect = make_common_conn

            result = monitor._get_small_cd("ITEM001")

        assert result == "003"

    def test_get_small_cd_not_found(self, smallcd_db):
        """미등록 상품 -> None."""
        from src.application.services.new_product_monitor import NewProductMonitor
        monitor = NewProductMonitor.__new__(NewProductMonitor)
        monitor.store_id = "46513"

        with patch("src.infrastructure.database.connection.DBRouter") as mock_router:
            def make_common_conn():
                c = sqlite3.connect(str(smallcd_db["common"]))
                c.row_factory = sqlite3.Row
                return c
            mock_router.get_common_connection.side_effect = make_common_conn

            result = monitor._get_small_cd("NONEXIST")

        assert result is None


# ═══════════════════════════════════════════════════════
# 3. Boost with small_cd (3건)
# ═══════════════════════════════════════════════════════

class TestSmallCdBoost:
    """_apply_new_product_boost에서 small_cd 정보 활용."""

    def _make_predictor(self, cache=None):
        """ImprovedPredictor mock (캐시만 설정)."""
        from src.prediction.improved_predictor import ImprovedPredictor
        predictor = ImprovedPredictor.__new__(ImprovedPredictor)
        predictor.store_id = "46513"
        predictor._new_product_cache = cache or {}
        return predictor

    def test_smallcd_boost_applied(self):
        """small_cd 정보가 있는 상태에서 부스트 적용."""
        cache = {
            "ITEM001": {
                "item_cd": "ITEM001",
                "lifecycle_status": "monitoring",
                "similar_item_avg": 10.0,
                "small_cd": "001",
            }
        }
        predictor = self._make_predictor(cache)

        result = predictor._apply_new_product_boost(
            item_cd="ITEM001", mid_cd="001", order_qty=2,
            prediction=3.0, current_stock=0, pending_qty=0, safety_stock=1.0
        )
        # boosted = max(10.0*0.7, 3.0) = 7.0
        # new_order = max(1, round(7.0 - 0 - 0 + 1.0)) = 8
        assert result == 8

    def test_smallcd_boost_with_midcd_fallback(self):
        """small_cd=None인 경우에도 기존 부스트 적용."""
        cache = {
            "ITEM001": {
                "item_cd": "ITEM001",
                "lifecycle_status": "monitoring",
                "similar_item_avg": 8.0,
                "small_cd": None,  # 소분류 없음
            }
        }
        predictor = self._make_predictor(cache)

        result = predictor._apply_new_product_boost(
            item_cd="ITEM001", mid_cd="001", order_qty=2,
            prediction=3.0, current_stock=0, pending_qty=0, safety_stock=1.0
        )
        # boosted = max(8.0*0.7, 3.0) = 5.6
        # new_order = max(1, round(5.6 - 0 - 0 + 1.0)) = 7
        assert result == 7

    def test_boost_log_includes_smallcd(self):
        """로그 메시지에 small_cd 정보 포함."""
        import logging

        cache = {
            "ITEM001": {
                "item_cd": "ITEM001",
                "lifecycle_status": "monitoring",
                "similar_item_avg": 10.0,
                "small_cd": "003",
            }
        }
        predictor = self._make_predictor(cache)

        with patch("src.prediction.improved_predictor.logger") as mock_logger:
            predictor._apply_new_product_boost(
                item_cd="ITEM001", mid_cd="001", order_qty=2,
                prediction=3.0, current_stock=0, pending_qty=0, safety_stock=1.0
            )

            mock_logger.info.assert_called_once()
            log_msg = mock_logger.info.call_args[0][0]
            assert "small_cd=003" in log_msg


# ═══════════════════════════════════════════════════════
# 4. Cache enrichment (2건)
# ═══════════════════════════════════════════════════════

class TestCacheEnrichment:
    """_enrich_cache_with_small_cd 캐시 보강."""

    def test_smallcd_cache_enrichment(self, smallcd_db):
        """캐시에 small_cd 정보가 올바르게 추가되는지."""
        _insert_product(smallcd_db, "ITEM001", "001", small_cd="001")
        _insert_product(smallcd_db, "ITEM002", "001", small_cd="002")

        from src.prediction.improved_predictor import ImprovedPredictor
        predictor = ImprovedPredictor.__new__(ImprovedPredictor)
        predictor.store_id = "46513"
        predictor._new_product_cache = {
            "ITEM001": {"item_cd": "ITEM001", "lifecycle_status": "monitoring"},
            "ITEM002": {"item_cd": "ITEM002", "lifecycle_status": "monitoring"},
        }

        with patch("src.infrastructure.database.connection.DBRouter") as mock_router:
            def make_common_conn():
                c = sqlite3.connect(str(smallcd_db["common"]))
                c.row_factory = sqlite3.Row
                return c
            mock_router.get_common_connection.side_effect = make_common_conn

            predictor._enrich_cache_with_small_cd()

        assert predictor._new_product_cache["ITEM001"]["small_cd"] == "001"
        assert predictor._new_product_cache["ITEM002"]["small_cd"] == "002"

    def test_smallcd_mixed_scenario(self, smallcd_db):
        """small_cd 있는 상품 + 없는 상품 혼합 시 캐시 정상 작동."""
        _insert_product(smallcd_db, "ITEM001", "001", small_cd="001")
        # ITEM002는 product_details에 없음 (small_cd 조회 불가)

        from src.prediction.improved_predictor import ImprovedPredictor
        predictor = ImprovedPredictor.__new__(ImprovedPredictor)
        predictor.store_id = "46513"
        predictor._new_product_cache = {
            "ITEM001": {"item_cd": "ITEM001", "lifecycle_status": "monitoring"},
            "ITEM002": {"item_cd": "ITEM002", "lifecycle_status": "monitoring"},
        }

        with patch("src.infrastructure.database.connection.DBRouter") as mock_router:
            def make_common_conn():
                c = sqlite3.connect(str(smallcd_db["common"]))
                c.row_factory = sqlite3.Row
                return c
            mock_router.get_common_connection.side_effect = make_common_conn

            predictor._enrich_cache_with_small_cd()

        # ITEM001: small_cd 정상
        assert predictor._new_product_cache["ITEM001"]["small_cd"] == "001"
        # ITEM002: small_cd 없음 → 키 자체가 없거나 None
        assert predictor._new_product_cache["ITEM002"].get("small_cd") is None
