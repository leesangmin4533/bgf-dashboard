"""신제품 라이프사이클 모니터링 테스트.

Design Reference: docs/02-design/features/new-product-lifecycle.design.md
테스트 항목:
--- NewProductMonitor (8건) ---
1. test_collect_daily_tracking: 일별 판매/재고/발주 정확히 저장
2. test_detected_to_monitoring: detected -> monitoring 전환
3. test_monitoring_to_stable: 14일 + sold_days>=3 -> stable
4. test_monitoring_to_no_demand: 14일 + sold_days==0 -> no_demand
5. test_monitoring_to_slow_start: 14일 + 1<=sold_days<3 -> slow_start
6. test_stable_to_normal: stable + 30일 -> normal
7. test_similar_avg_calculation: mid_cd 기반 중위값 정확성
8. test_similar_avg_no_match: mid_cd=999 -> None 반환
--- NewProductOrderBooster (5건) ---
9. test_boost_applied: monitoring + similar_avg -> 보정 적용
10. test_boost_skip_stable: stable 상태 -> 보정 미적용
11. test_boost_skip_no_similar: similar_avg=None -> 보정 미적용
12. test_boost_cap: 보정값이 기존보다 낮으면 기존 유지
13. test_boost_cache_loading: _new_product_cache 정상 로딩
--- Repository (4건) ---
14. test_lifecycle_status_query: get_by_lifecycle_status 정확성
15. test_update_lifecycle: update_lifecycle 필드 업데이트
16. test_tracking_save_and_get: 일별 추적 UPSERT + 조회
17. test_monitoring_summary: 상태별 건수 집계
--- API + 스키마 (3건) ---
18. test_monitoring_api: /new-products/monitoring 응답
19. test_tracking_api: /new-products/<item_cd>/tracking 응답
20. test_schema_v46: SCHEMA_MIGRATIONS[46] 존재 + 실행
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ─── DB fixtures ───

@pytest.fixture
def lifecycle_db(tmp_path):
    """테스트용 store DB (lifecycle 관련 테이블 전체)."""
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
            center_cd TEXT,
            center_nm TEXT,
            cust_nm TEXT,
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
            sales_date TEXT,
            item_cd TEXT,
            sale_qty INTEGER DEFAULT 0,
            mid_cd TEXT,
            store_id TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE realtime_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT,
            stock_qty INTEGER DEFAULT 0,
            is_available INTEGER DEFAULT 1,
            store_id TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE auto_order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_date TEXT,
            item_cd TEXT,
            order_qty INTEGER DEFAULT 0,
            store_id TEXT
        )
    """)

    conn.commit()
    conn.close()
    return db_path


def _insert_detected(db_path, item_cd, mid_cd="001", status="detected",
                     monitoring_start=None, store_id="46513"):
    """detected_new_products 테스트 데이터 삽입 헬퍼."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO detected_new_products
           (item_cd, item_nm, mid_cd, mid_cd_source, first_receiving_date,
            receiving_qty, detected_at, store_id, lifecycle_status,
            monitoring_start_date)
           VALUES (?, ?, ?, 'fallback', '2026-02-10', 10, ?, ?, ?, ?)""",
        (item_cd, f"테스트상품_{item_cd}", mid_cd,
         datetime.now().isoformat(), store_id, status, monitoring_start),
    )
    conn.commit()
    conn.close()


def _insert_tracking(db_path, item_cd, date, sales_qty=0, store_id="46513"):
    """new_product_daily_tracking 테스트 데이터 삽입 헬퍼."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO new_product_daily_tracking
           (item_cd, tracking_date, sales_qty, stock_qty, order_qty, store_id, created_at)
           VALUES (?, ?, ?, 0, 0, ?, ?)""",
        (item_cd, date, sales_qty, store_id, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════
# 1. NewProductMonitor 테스트 (8건)
# ═══════════════════════════════════════════════════════

class TestNewProductMonitor:
    """NewProductMonitor 서비스 테스트."""

    def test_collect_daily_tracking(self, lifecycle_db):
        """일별 판매/재고/발주 데이터가 정확히 저장되는지 확인."""
        _insert_detected(lifecycle_db, "ITEM001", status="monitoring",
                         monitoring_start="2026-02-20")

        today = "2026-02-26"

        from src.infrastructure.database.repos.detected_new_product_repo import DetectedNewProductRepository
        from src.infrastructure.database.repos.np_tracking_repo import NewProductDailyTrackingRepository

        detect_repo = DetectedNewProductRepository(db_path=lifecycle_db)
        tracking_repo = NewProductDailyTrackingRepository(db_path=lifecycle_db)

        from src.application.services.new_product_monitor import NewProductMonitor
        monitor = NewProductMonitor.__new__(NewProductMonitor)
        monitor.store_id = "46513"
        monitor.detect_repo = detect_repo
        monitor.tracking_repo = tracking_repo

        # private helper를 직접 mock
        monitor._get_daily_sales = lambda d: {"ITEM001": 5}
        monitor._get_stock_map = lambda: {"ITEM001": 3}
        monitor._get_order_map = lambda d: {"ITEM001": 6}

        items = detect_repo.get_by_lifecycle_status(["monitoring"])
        saved = monitor.collect_daily_tracking(items, today)
        assert saved == 1

        history = tracking_repo.get_tracking_history("ITEM001")
        assert len(history) == 1
        assert history[0]["sales_qty"] == 5
        assert history[0]["stock_qty"] == 3
        assert history[0]["order_qty"] == 6

    def test_detected_to_monitoring(self, lifecycle_db):
        """detected -> monitoring 전환."""
        _insert_detected(lifecycle_db, "ITEM001", status="detected")

        from src.infrastructure.database.repos.detected_new_product_repo import DetectedNewProductRepository
        from src.infrastructure.database.repos.np_tracking_repo import NewProductDailyTrackingRepository

        detect_repo = DetectedNewProductRepository(db_path=lifecycle_db)
        tracking_repo = NewProductDailyTrackingRepository(db_path=lifecycle_db)

        from src.application.services.new_product_monitor import NewProductMonitor
        monitor = NewProductMonitor.__new__(NewProductMonitor)
        monitor.store_id = "46513"
        monitor.detect_repo = detect_repo
        monitor.tracking_repo = tracking_repo

        items = detect_repo.get_by_lifecycle_status(["detected"])
        changes = monitor.update_lifecycle_status(items, "2026-02-26")

        assert len(changes) == 1
        assert changes[0]["from"] == "detected"
        assert changes[0]["to"] == "monitoring"

        updated = detect_repo.get_by_lifecycle_status(["monitoring"])
        assert len(updated) == 1
        assert updated[0]["monitoring_start_date"] == "2026-02-26"

    def test_monitoring_to_stable(self, lifecycle_db):
        """14일 + sold_days>=3 -> stable."""
        start = "2026-02-10"
        _insert_detected(lifecycle_db, "ITEM001", status="monitoring",
                         monitoring_start=start)

        # 3일 판매 데이터
        for i in range(3):
            date = (datetime(2026, 2, 10) + timedelta(days=i)).strftime("%Y-%m-%d")
            _insert_tracking(lifecycle_db, "ITEM001", date, sales_qty=2)

        from src.infrastructure.database.repos.detected_new_product_repo import DetectedNewProductRepository
        from src.infrastructure.database.repos.np_tracking_repo import NewProductDailyTrackingRepository

        detect_repo = DetectedNewProductRepository(db_path=lifecycle_db)
        tracking_repo = NewProductDailyTrackingRepository(db_path=lifecycle_db)

        from src.application.services.new_product_monitor import NewProductMonitor
        monitor = NewProductMonitor.__new__(NewProductMonitor)
        monitor.store_id = "46513"
        monitor.detect_repo = detect_repo
        monitor.tracking_repo = tracking_repo

        items = detect_repo.get_by_lifecycle_status(["monitoring"])
        today = "2026-02-26"  # 16일 경과
        changes = monitor.update_lifecycle_status(items, today)

        assert len(changes) == 1
        assert changes[0]["to"] == "stable"
        assert changes[0]["sold_days"] == 3

    def test_monitoring_to_no_demand(self, lifecycle_db):
        """14일 + sold_days==0 -> no_demand."""
        _insert_detected(lifecycle_db, "ITEM001", status="monitoring",
                         monitoring_start="2026-02-10")

        from src.infrastructure.database.repos.detected_new_product_repo import DetectedNewProductRepository
        from src.infrastructure.database.repos.np_tracking_repo import NewProductDailyTrackingRepository

        detect_repo = DetectedNewProductRepository(db_path=lifecycle_db)
        tracking_repo = NewProductDailyTrackingRepository(db_path=lifecycle_db)

        from src.application.services.new_product_monitor import NewProductMonitor
        monitor = NewProductMonitor.__new__(NewProductMonitor)
        monitor.store_id = "46513"
        monitor.detect_repo = detect_repo
        monitor.tracking_repo = tracking_repo

        items = detect_repo.get_by_lifecycle_status(["monitoring"])
        changes = monitor.update_lifecycle_status(items, "2026-02-26")

        assert len(changes) == 1
        assert changes[0]["to"] == "no_demand"
        assert changes[0]["sold_days"] == 0

    def test_monitoring_to_slow_start(self, lifecycle_db):
        """14일 + 1<=sold_days<3 -> slow_start."""
        _insert_detected(lifecycle_db, "ITEM001", status="monitoring",
                         monitoring_start="2026-02-10")
        _insert_tracking(lifecycle_db, "ITEM001", "2026-02-15", sales_qty=1)

        from src.infrastructure.database.repos.detected_new_product_repo import DetectedNewProductRepository
        from src.infrastructure.database.repos.np_tracking_repo import NewProductDailyTrackingRepository

        detect_repo = DetectedNewProductRepository(db_path=lifecycle_db)
        tracking_repo = NewProductDailyTrackingRepository(db_path=lifecycle_db)

        from src.application.services.new_product_monitor import NewProductMonitor
        monitor = NewProductMonitor.__new__(NewProductMonitor)
        monitor.store_id = "46513"
        monitor.detect_repo = detect_repo
        monitor.tracking_repo = tracking_repo

        items = detect_repo.get_by_lifecycle_status(["monitoring"])
        changes = monitor.update_lifecycle_status(items, "2026-02-26")

        assert len(changes) == 1
        assert changes[0]["to"] == "slow_start"
        assert changes[0]["sold_days"] == 1

    def test_stable_to_normal(self, lifecycle_db):
        """stable + 30일 경과 -> normal."""
        _insert_detected(lifecycle_db, "ITEM001", status="stable",
                         monitoring_start="2026-01-20")

        from src.infrastructure.database.repos.detected_new_product_repo import DetectedNewProductRepository
        from src.infrastructure.database.repos.np_tracking_repo import NewProductDailyTrackingRepository

        detect_repo = DetectedNewProductRepository(db_path=lifecycle_db)
        tracking_repo = NewProductDailyTrackingRepository(db_path=lifecycle_db)

        from src.application.services.new_product_monitor import NewProductMonitor
        monitor = NewProductMonitor.__new__(NewProductMonitor)
        monitor.store_id = "46513"
        monitor.detect_repo = detect_repo
        monitor.tracking_repo = tracking_repo

        items = detect_repo.get_by_lifecycle_status(["stable"])
        changes = monitor.update_lifecycle_status(items, "2026-02-26")

        assert len(changes) == 1
        assert changes[0]["from"] == "stable"
        assert changes[0]["to"] == "normal"

    def test_similar_avg_calculation(self, lifecycle_db, tmp_path):
        """mid_cd 기반 유사 상품 중위값 계산."""
        # common.db에 products 생성
        common_path = tmp_path / "common.db"
        cconn = sqlite3.connect(str(common_path))
        cconn.execute("""
            CREATE TABLE products (
                item_cd TEXT PRIMARY KEY,
                item_nm TEXT,
                mid_cd TEXT
            )
        """)
        # 같은 mid_cd "001" 상품 3개 (ITEM001은 자기 자신)
        cconn.execute("INSERT INTO products VALUES ('ITEM001', '신제품', '001')")
        cconn.execute("INSERT INTO products VALUES ('ITEM002', '기존A', '001')")
        cconn.execute("INSERT INTO products VALUES ('ITEM003', '기존B', '001')")
        cconn.commit()
        cconn.close()

        # daily_sales에 유사 상품 판매 데이터 추가 (최근 30일 이내 날짜 사용)
        conn = sqlite3.connect(str(lifecycle_db))
        from datetime import datetime as _dt
        base_date = _dt.now() - timedelta(days=15)  # 15일 전부터
        for day in range(10):
            date = (base_date + timedelta(days=day)).strftime("%Y-%m-%d")
            conn.execute(
                "INSERT INTO daily_sales (sales_date, item_cd, sale_qty) VALUES (?, ?, ?)",
                (date, "ITEM002", 3),  # 일평균 3
            )
            conn.execute(
                "INSERT INTO daily_sales (sales_date, item_cd, sale_qty) VALUES (?, ?, ?)",
                (date, "ITEM003", 5),  # 일평균 5
            )
        conn.commit()
        conn.close()

        with patch("src.infrastructure.database.connection.DBRouter") as mock_router, \
             patch("src.infrastructure.database.connection.attach_common_with_views") as mock_attach:

            def make_conn(*args, **kwargs):
                c = sqlite3.connect(str(lifecycle_db))
                c.row_factory = sqlite3.Row
                c.execute(f"ATTACH DATABASE '{common_path}' AS common")
                return c
            mock_router.get_store_connection.side_effect = make_conn
            mock_attach.side_effect = lambda conn, sid: conn

            from src.application.services.new_product_monitor import NewProductMonitor
            monitor = NewProductMonitor.__new__(NewProductMonitor)
            monitor.store_id = "46513"

            result = monitor.calculate_similar_avg("ITEM001", "001")
            assert result is not None
            # 중위값: median([3.0, 5.0]) = 4.0
            assert result == 4.0

    def test_similar_avg_no_match(self, lifecycle_db):
        """mid_cd=999 -> None 반환."""
        from src.application.services.new_product_monitor import NewProductMonitor
        monitor = NewProductMonitor.__new__(NewProductMonitor)
        monitor.store_id = "46513"

        result = monitor.calculate_similar_avg("ITEM001", "999")
        assert result is None


# ═══════════════════════════════════════════════════════
# 2. NewProductOrderBooster 테스트 (5건)
# ═══════════════════════════════════════════════════════

class TestNewProductOrderBooster:
    """ImprovedPredictor._apply_new_product_boost 테스트."""

    def _make_predictor(self, cache=None):
        """ImprovedPredictor mock (캐시만 설정)."""
        from src.prediction.improved_predictor import ImprovedPredictor
        predictor = ImprovedPredictor.__new__(ImprovedPredictor)
        predictor.store_id = "46513"
        predictor._new_product_cache = cache or {}
        return predictor

    def test_boost_applied(self):
        """monitoring + similar_avg -> 보정 적용."""
        cache = {
            "ITEM001": {
                "item_cd": "ITEM001",
                "lifecycle_status": "monitoring",
                "similar_item_avg": 10.0,
            }
        }
        predictor = self._make_predictor(cache)

        # prediction=3, similar_avg*0.7=7.0 > 3 -> boost
        result = predictor._apply_new_product_boost(
            item_cd="ITEM001", mid_cd="001", order_qty=2,
            prediction=3.0, current_stock=0, pending_qty=0, safety_stock=1.0
        )
        # boosted = max(7.0, 3.0) = 7.0
        # new_order = max(1, round(7.0 - 0 - 0 + 1.0)) = 8
        assert result == 8

    def test_boost_skip_stable(self):
        """stable 상태 -> 보정 미적용."""
        cache = {
            "ITEM001": {
                "item_cd": "ITEM001",
                "lifecycle_status": "stable",
                "similar_item_avg": 10.0,
            }
        }
        predictor = self._make_predictor(cache)

        result = predictor._apply_new_product_boost(
            item_cd="ITEM001", mid_cd="001", order_qty=2,
            prediction=3.0, current_stock=0, pending_qty=0, safety_stock=1.0
        )
        assert result == 2  # 변경 없음

    def test_boost_skip_no_similar(self):
        """similar_avg=None -> 보정 미적용."""
        cache = {
            "ITEM001": {
                "item_cd": "ITEM001",
                "lifecycle_status": "monitoring",
                "similar_item_avg": None,
            }
        }
        predictor = self._make_predictor(cache)

        result = predictor._apply_new_product_boost(
            item_cd="ITEM001", mid_cd="001", order_qty=5,
            prediction=3.0, current_stock=0, pending_qty=0, safety_stock=1.0
        )
        assert result == 5  # 변경 없음

    def test_boost_cap(self):
        """보정값이 기존보다 낮으면 기존 유지."""
        cache = {
            "ITEM001": {
                "item_cd": "ITEM001",
                "lifecycle_status": "monitoring",
                "similar_item_avg": 2.0,  # similar*0.7 = 1.4 < prediction
            }
        }
        predictor = self._make_predictor(cache)

        result = predictor._apply_new_product_boost(
            item_cd="ITEM001", mid_cd="001", order_qty=5,
            prediction=5.0, current_stock=0, pending_qty=0, safety_stock=1.0
        )
        assert result == 5  # 변경 없음

    def test_boost_cache_loading(self, lifecycle_db):
        """_new_product_cache 정상 로딩."""
        _insert_detected(lifecycle_db, "ITEM001", status="monitoring",
                         monitoring_start="2026-02-20")

        # similar_item_avg 값 수동 설정
        conn = sqlite3.connect(str(lifecycle_db))
        conn.execute(
            "UPDATE detected_new_products SET similar_item_avg = 5.0 WHERE item_cd = 'ITEM001'"
        )
        conn.commit()
        conn.close()

        from src.prediction.improved_predictor import ImprovedPredictor
        predictor = ImprovedPredictor.__new__(ImprovedPredictor)
        predictor.store_id = "46513"
        predictor._new_product_cache = {}

        with patch("src.infrastructure.database.repos.DetectedNewProductRepository") as MockRepo:
            mock_instance = MockRepo.return_value
            mock_instance.get_by_lifecycle_status.return_value = [
                {"item_cd": "ITEM001", "lifecycle_status": "monitoring",
                 "similar_item_avg": 5.0}
            ]

            predictor._load_new_product_cache()
            assert "ITEM001" in predictor._new_product_cache
            assert predictor._new_product_cache["ITEM001"]["similar_item_avg"] == 5.0


# ═══════════════════════════════════════════════════════
# 3. Repository 테스트 (4건)
# ═══════════════════════════════════════════════════════

class TestRepositories:
    """DetectedNewProductRepository + NewProductDailyTrackingRepository 테스트."""

    def test_lifecycle_status_query(self, lifecycle_db):
        """get_by_lifecycle_status 정확성."""
        _insert_detected(lifecycle_db, "ITEM001", status="detected")
        _insert_detected(lifecycle_db, "ITEM002", status="monitoring",
                         monitoring_start="2026-02-20")
        _insert_detected(lifecycle_db, "ITEM003", status="stable",
                         monitoring_start="2026-02-01")

        from src.infrastructure.database.repos.detected_new_product_repo import DetectedNewProductRepository
        repo = DetectedNewProductRepository(db_path=lifecycle_db)

        # detected + monitoring
        items = repo.get_by_lifecycle_status(["detected", "monitoring"])
        assert len(items) == 2
        codes = {i["item_cd"] for i in items}
        assert codes == {"ITEM001", "ITEM002"}

        # stable only
        items = repo.get_by_lifecycle_status(["stable"])
        assert len(items) == 1
        assert items[0]["item_cd"] == "ITEM003"

        # empty list
        items = repo.get_by_lifecycle_status([])
        assert len(items) == 0

    def test_update_lifecycle(self, lifecycle_db):
        """update_lifecycle 필드 업데이트."""
        _insert_detected(lifecycle_db, "ITEM001", status="detected")

        from src.infrastructure.database.repos.detected_new_product_repo import DetectedNewProductRepository
        repo = DetectedNewProductRepository(db_path=lifecycle_db)

        repo.update_lifecycle(
            item_cd="ITEM001",
            status="monitoring",
            monitoring_start="2026-02-26",
            total_sold_qty=10,
            sold_days=3,
            similar_item_avg=4.5,
        )

        items = repo.get_by_lifecycle_status(["monitoring"])
        assert len(items) == 1
        item = items[0]
        assert item["lifecycle_status"] == "monitoring"
        assert item["monitoring_start_date"] == "2026-02-26"
        assert item["total_sold_qty"] == 10
        assert item["sold_days"] == 3
        assert item["similar_item_avg"] == 4.5
        assert item["status_changed_at"] is not None

    def test_tracking_save_and_get(self, lifecycle_db):
        """일별 추적 UPSERT + 조회."""
        from src.infrastructure.database.repos.np_tracking_repo import NewProductDailyTrackingRepository
        repo = NewProductDailyTrackingRepository(db_path=lifecycle_db)

        # 저장
        repo.save("ITEM001", "2026-02-26", sales_qty=5, stock_qty=3,
                   order_qty=6, store_id="46513")

        # 조회
        history = repo.get_tracking_history("ITEM001", store_id="46513")
        assert len(history) == 1
        assert history[0]["sales_qty"] == 5

        # UPSERT (같은 item+date)
        repo.save("ITEM001", "2026-02-26", sales_qty=8, stock_qty=2,
                   order_qty=10, store_id="46513")
        history = repo.get_tracking_history("ITEM001", store_id="46513")
        assert len(history) == 1
        assert history[0]["sales_qty"] == 8  # 업데이트됨

        # sold_days / total_sold
        assert repo.get_sold_days_count("ITEM001", store_id="46513") == 1
        assert repo.get_total_sold_qty("ITEM001", store_id="46513") == 8

    def test_monitoring_summary(self, lifecycle_db):
        """상태별 건수 집계."""
        _insert_detected(lifecycle_db, "ITEM001", status="detected")
        _insert_detected(lifecycle_db, "ITEM002", status="monitoring",
                         monitoring_start="2026-02-20")
        _insert_detected(lifecycle_db, "ITEM003", status="monitoring",
                         monitoring_start="2026-02-19")
        _insert_detected(lifecycle_db, "ITEM004", status="stable",
                         monitoring_start="2026-02-01")

        from src.infrastructure.database.repos.detected_new_product_repo import DetectedNewProductRepository
        repo = DetectedNewProductRepository(db_path=lifecycle_db)

        summary = repo.get_monitoring_summary()
        assert summary.get("detected") == 1
        assert summary.get("monitoring") == 2
        assert summary.get("stable") == 1


# ═══════════════════════════════════════════════════════
# 4. API + 스키마 테스트 (3건)
# ═══════════════════════════════════════════════════════

class TestAPIAndSchema:
    """Web API + 스키마 마이그레이션 테스트."""

    def test_monitoring_api(self):
        """/new-products/monitoring 응답."""
        mock_repo = MagicMock()
        mock_repo.return_value.get_monitoring_summary.return_value = {
            "detected": 1, "monitoring": 3
        }
        mock_repo.return_value.get_by_lifecycle_status.return_value = [
            {"item_cd": "ITEM001", "lifecycle_status": "monitoring"}
        ]

        with patch("src.infrastructure.database.repos.DetectedNewProductRepository",
                    mock_repo):
            from src.web.routes.api_receiving import receiving_bp
            from flask import Flask
            app = Flask(__name__)
            app.register_blueprint(receiving_bp, url_prefix="/api/receiving")

            with app.test_client() as client:
                resp = client.get("/api/receiving/new-products/monitoring?store_id=46513")
                assert resp.status_code == 200
                data = resp.get_json()
                assert "summary" in data
                assert "items" in data
                assert data["count"] == 1

    def test_tracking_api(self):
        """/new-products/<item_cd>/tracking 응답."""
        mock_repo = MagicMock()
        mock_repo.return_value.get_tracking_history.return_value = [
            {"tracking_date": "2026-02-25", "sales_qty": 3, "stock_qty": 5, "order_qty": 6},
            {"tracking_date": "2026-02-26", "sales_qty": 4, "stock_qty": 2, "order_qty": 8},
        ]

        with patch("src.infrastructure.database.repos.NewProductDailyTrackingRepository",
                    mock_repo):
            from src.web.routes.api_receiving import receiving_bp
            from flask import Flask
            app = Flask(__name__)
            app.register_blueprint(receiving_bp, url_prefix="/api/receiving")

            with app.test_client() as client:
                resp = client.get("/api/receiving/new-products/ITEM001/tracking?store_id=46513")
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["item_cd"] == "ITEM001"
                assert len(data["tracking"]) == 2

    def test_schema_v46(self):
        """SCHEMA_MIGRATIONS[46] 존재 + 실행 가능."""
        from src.db.models import SCHEMA_MIGRATIONS
        assert 46 in SCHEMA_MIGRATIONS

        migration_sql = SCHEMA_MIGRATIONS[46]
        assert "lifecycle_status" in migration_sql
        assert "new_product_daily_tracking" in migration_sql

        # SQLite에서 실행 가능한지 확인
        conn = sqlite3.connect(":memory:")

        # 기존 테이블 먼저 생성 (ALTER TABLE 대상)
        conn.execute("""
            CREATE TABLE detected_new_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_cd TEXT,
                first_receiving_date TEXT,
                detected_at TEXT,
                UNIQUE(item_cd, first_receiving_date)
            )
        """)

        for stmt in migration_sql.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)

        # lifecycle_status 컬럼 확인
        cursor = conn.execute("PRAGMA table_info(detected_new_products)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "lifecycle_status" in columns
        assert "monitoring_start_date" in columns

        # new_product_daily_tracking 테이블 확인
        cursor = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='new_product_daily_tracking'"
        )
        assert cursor.fetchone()[0] == 1

        conn.close()
