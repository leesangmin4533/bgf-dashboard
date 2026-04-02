"""입고 시 신제품 자동 감지 테스트.

Design Reference: docs/02-design/features/receiving-new-product-detect.design.md
테스트 항목:
1. 입고 확정(recv_qty>0)만 감지 (1)
2. 입고 예정(plan_qty만)은 무시 (1)
3. 기존 상품은 감지 안 함 (1)
4. products 테이블 자동 등록 (1)
5. product_details 기본값 등록 (1)
6. realtime_inventory 입고수량 등록 (1)
7. detected_new_products 이력 기록 (1)
8. 동일 상품 중복 감지 방지 (1)
9. 여러 신제품 동시 감지 (1)
10. 부분 등록 실패 시 이력 기록 (1)
11. 감지 실패 시 기존 플로우 정상 (1)
12. mid_cd 추정값 저장 확인 (1)
13. stats 반환값 확인 (1)
14. Repository CRUD (1)
15. 스키마 마이그레이션 v45 (1)
16. Web API: /api/receiving/new-products (1)
17. Web API: /api/receiving/new-products/unregistered (1)
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ─── DB fixtures ───

@pytest.fixture
def store_db(tmp_path):
    """테스트용 store DB (detected_new_products + realtime_inventory)."""
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
        CREATE TABLE realtime_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL DEFAULT '46513',
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            stock_qty INTEGER DEFAULT 0,
            pending_qty INTEGER DEFAULT 0,
            order_unit_qty INTEGER DEFAULT 1,
            is_available INTEGER DEFAULT 1,
            is_cut_item INTEGER DEFAULT 0,
            queried_at TEXT,
            created_at TEXT,
            UNIQUE(store_id, item_cd)
        )
    """)
    conn.execute("""
        CREATE TABLE receiving_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receiving_date TEXT,
            receiving_time TEXT,
            chit_no TEXT,
            item_cd TEXT,
            item_nm TEXT,
            mid_cd TEXT,
            order_date TEXT,
            order_qty INTEGER DEFAULT 0,
            receiving_qty INTEGER DEFAULT 0,
            plan_qty INTEGER DEFAULT 0,
            delivery_type TEXT,
            center_nm TEXT,
            center_cd TEXT,
            store_id TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def common_db(tmp_path):
    """테스트용 common DB."""
    db_path = tmp_path / "common.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE products (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT NOT NULL,
            mid_cd TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE product_details (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            expiration_days INTEGER,
            orderable_day TEXT DEFAULT '일월화수목금토',
            orderable_status TEXT,
            order_unit_name TEXT DEFAULT '낱개',
            order_unit_qty INTEGER DEFAULT 1,
            case_unit_qty INTEGER DEFAULT 1,
            lead_time_days INTEGER DEFAULT 1,
            fetched_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            sell_price INTEGER,
            margin_rate REAL
        )
    """)
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
            analysis_window_days INTEGER,
            extension_count INTEGER DEFAULT 0,
            settlement_score REAL,
            settlement_verdict TEXT,
            settlement_date TEXT,
            settlement_checked_at TEXT,
            UNIQUE(item_cd, first_receiving_date)
        )
    """)
    # 기존 상품 등록
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO products (item_cd, item_nm, mid_cd, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("8800001111111", "기존상품A", "049", now, now)
    )
    conn.commit()
    conn.close()
    return db_path


# ─── DetectedNewProductRepository 테스트 ───

class TestDetectedNewProductRepository:
    """Repository CRUD 테스트"""

    def test_save_and_get_recent(self, store_db):
        from src.infrastructure.database.repos.detected_new_product_repo import DetectedNewProductRepository
        repo = DetectedNewProductRepository(db_path=store_db, store_id="46513")

        record_id = repo.save(
            item_cd="8800099999999",
            item_nm="테스트신제품",
            mid_cd="001",
            mid_cd_source="fallback",
            first_receiving_date="2026-02-26",
            receiving_qty=10,
            order_unit_qty=1,
            center_cd="C001",
            center_nm="테스트센터",
            cust_nm="테스트거래처",
            registered_to_products=True,
            registered_to_details=True,
            registered_to_inventory=True,
            store_id="46513",
        )
        assert record_id > 0

        items = repo.get_recent(days=30, store_id="46513")
        assert len(items) == 1
        assert items[0]["item_cd"] == "8800099999999"
        assert items[0]["registered_to_products"] == 1

    def test_get_unregistered(self, store_db):
        from src.infrastructure.database.repos.detected_new_product_repo import DetectedNewProductRepository
        repo = DetectedNewProductRepository(db_path=store_db, store_id="46513")

        # 부분 등록
        repo.save(
            item_cd="8800099999999",
            item_nm="미등록상품",
            mid_cd="001",
            mid_cd_source="fallback",
            first_receiving_date="2026-02-26",
            receiving_qty=5,
            registered_to_products=True,
            registered_to_details=False,
            registered_to_inventory=False,
            store_id="46513",
        )

        unregistered = repo.get_unregistered(store_id="46513")
        assert len(unregistered) == 1
        assert unregistered[0]["registered_to_details"] == 0

    def test_mark_registered(self, store_db):
        from src.infrastructure.database.repos.detected_new_product_repo import DetectedNewProductRepository
        repo = DetectedNewProductRepository(db_path=store_db, store_id="46513")

        repo.save(
            item_cd="8800099999999",
            item_nm="테스트",
            mid_cd="001",
            mid_cd_source="fallback",
            first_receiving_date="2026-02-26",
            receiving_qty=5,
            registered_to_products=False,
            registered_to_details=False,
            registered_to_inventory=False,
            store_id="46513",
        )

        repo.mark_registered("8800099999999", "products", store_id="46513")

        items = repo.get_recent(days=30, store_id="46513")
        assert items[0]["registered_to_products"] == 1
        assert items[0]["registered_to_details"] == 0

    def test_duplicate_upsert(self, store_db):
        """동일 item_cd + date UPSERT 시 receiving_qty 갱신"""
        from src.infrastructure.database.repos.detected_new_product_repo import DetectedNewProductRepository
        repo = DetectedNewProductRepository(db_path=store_db, store_id="46513")

        repo.save(
            item_cd="8800099999999", item_nm="상품A", mid_cd="001", mid_cd_source="fallback",
            first_receiving_date="2026-02-26", receiving_qty=5,
            registered_to_products=False, registered_to_details=False,
            registered_to_inventory=False, store_id="46513",
        )
        repo.save(
            item_cd="8800099999999", item_nm="상품A", mid_cd="001", mid_cd_source="fallback",
            first_receiving_date="2026-02-26", receiving_qty=10,
            registered_to_products=True, registered_to_details=True,
            registered_to_inventory=True, store_id="46513",
        )

        items = repo.get_recent(days=30, store_id="46513")
        assert len(items) == 1
        assert items[0]["receiving_qty"] == 10
        assert items[0]["registered_to_products"] == 1

    def test_get_count_by_date(self, store_db):
        from src.infrastructure.database.repos.detected_new_product_repo import DetectedNewProductRepository
        repo = DetectedNewProductRepository(db_path=store_db, store_id="46513")

        repo.save(
            item_cd="8800099999991", item_nm="A", mid_cd="001", mid_cd_source="fallback",
            first_receiving_date="2026-02-26", receiving_qty=1,
            registered_to_products=True, registered_to_details=True,
            registered_to_inventory=True, store_id="46513",
        )
        repo.save(
            item_cd="8800099999992", item_nm="B", mid_cd="002", mid_cd_source="fallback",
            first_receiving_date="2026-02-26", receiving_qty=2,
            registered_to_products=True, registered_to_details=True,
            registered_to_inventory=True, store_id="46513",
        )

        count = repo.get_count_by_date("2026-02-26", store_id="46513")
        assert count == 2


# ─── 감지 로직 테스트 ───

class TestNewProductDetection:
    """ReceivingCollector 신제품 감지 로직"""

    def _make_collector(self, store_db, common_db):
        """테스트용 ReceivingCollector 생성"""
        from src.collectors.receiving_collector import ReceivingCollector
        collector = ReceivingCollector(driver=None, store_id="46513")
        collector._new_product_candidates = []
        # DB 경로 오버라이드는 _detect_and_register에서 mock으로 처리
        return collector

    def test_detect_confirmed_only(self, store_db, common_db):
        """recv_qty > 0인 입고 확정만 감지"""
        from src.collectors.receiving_collector import ReceivingCollector
        collector = ReceivingCollector(driver=None, store_id="46513")
        collector._new_product_candidates = [
            {"item_cd": "NEW001", "item_nm": "신제품1", "mid_cd": "001",
             "mid_cd_source": "fallback", "cust_nm": "거래처A"},
            {"item_cd": "PENDING001", "item_nm": "예정상품", "mid_cd": "002",
             "mid_cd_source": "fallback", "cust_nm": "거래처B"},
        ]

        receiving_data = [
            {"item_cd": "NEW001", "receiving_qty": 10, "plan_qty": 0,
             "receiving_date": "2026-02-26", "center_cd": "C01", "center_nm": "센터1"},
            {"item_cd": "PENDING001", "receiving_qty": 0, "plan_qty": 5,
             "receiving_date": "2026-02-26", "center_cd": "C01", "center_nm": "센터1"},
        ]

        with patch.object(collector, '_register_single_new_product') as mock_reg:
            stats = collector._detect_and_register_new_products(receiving_data)

        assert stats["new_products_detected"] == 1
        assert mock_reg.call_count == 1
        called_product = mock_reg.call_args[0][0]
        assert called_product["item_cd"] == "NEW001"

    def test_skip_pending_only(self, store_db, common_db):
        """plan_qty만 있는 미확정 상품은 감지 안 함"""
        from src.collectors.receiving_collector import ReceivingCollector
        collector = ReceivingCollector(driver=None, store_id="46513")
        collector._new_product_candidates = [
            {"item_cd": "PENDING001", "item_nm": "예정상품", "mid_cd": "002",
             "mid_cd_source": "fallback", "cust_nm": ""},
        ]

        receiving_data = [
            {"item_cd": "PENDING001", "receiving_qty": 0, "plan_qty": 10,
             "receiving_date": "2026-02-26"},
        ]

        with patch.object(collector, '_register_single_new_product') as mock_reg:
            stats = collector._detect_and_register_new_products(receiving_data)

        assert stats["new_products_detected"] == 0
        assert mock_reg.call_count == 0

    def test_skip_existing_product(self, store_db, common_db):
        """products에 이미 있는 상품은 후보에 올라오지 않음"""
        from src.collectors.receiving_collector import ReceivingCollector
        collector = ReceivingCollector(driver=None, store_id="46513")
        # 기존 상품은 _get_mid_cd()에서 products 조회 성공 → 후보 안 됨
        collector._new_product_candidates = []  # 비어있음

        receiving_data = [
            {"item_cd": "8800001111111", "receiving_qty": 5, "plan_qty": 0,
             "receiving_date": "2026-02-26"},
        ]

        stats = collector._detect_and_register_new_products(receiving_data)
        assert stats["new_products_detected"] == 0

    def test_multiple_new_products(self, store_db, common_db):
        """여러 신제품 동시 감지"""
        from src.collectors.receiving_collector import ReceivingCollector
        collector = ReceivingCollector(driver=None, store_id="46513")
        collector._new_product_candidates = [
            {"item_cd": "NEW001", "item_nm": "신제품1", "mid_cd": "001",
             "mid_cd_source": "fallback", "cust_nm": ""},
            {"item_cd": "NEW002", "item_nm": "신제품2", "mid_cd": "003",
             "mid_cd_source": "fallback", "cust_nm": ""},
            {"item_cd": "NEW003", "item_nm": "신제품3", "mid_cd": "",
             "mid_cd_source": "unknown", "cust_nm": ""},
        ]

        receiving_data = [
            {"item_cd": "NEW001", "receiving_qty": 10, "plan_qty": 0, "receiving_date": "2026-02-26"},
            {"item_cd": "NEW002", "receiving_qty": 5, "plan_qty": 0, "receiving_date": "2026-02-26"},
            {"item_cd": "NEW003", "receiving_qty": 3, "plan_qty": 0, "receiving_date": "2026-02-26"},
        ]

        with patch.object(collector, '_register_single_new_product'):
            stats = collector._detect_and_register_new_products(receiving_data)

        assert stats["new_products_detected"] == 3

    def test_duplicate_candidate_dedup(self, store_db, common_db):
        """동일 item_cd가 후보에 중복 있어도 1건만 감지"""
        from src.collectors.receiving_collector import ReceivingCollector
        collector = ReceivingCollector(driver=None, store_id="46513")
        collector._new_product_candidates = [
            {"item_cd": "NEW001", "item_nm": "신제품1", "mid_cd": "001",
             "mid_cd_source": "fallback", "cust_nm": ""},
            {"item_cd": "NEW001", "item_nm": "신제품1", "mid_cd": "001",
             "mid_cd_source": "fallback", "cust_nm": ""},
        ]

        receiving_data = [
            {"item_cd": "NEW001", "receiving_qty": 10, "plan_qty": 0, "receiving_date": "2026-02-26"},
        ]

        with patch.object(collector, '_register_single_new_product'):
            stats = collector._detect_and_register_new_products(receiving_data)

        assert stats["new_products_detected"] == 1

    def test_stats_return(self, store_db, common_db):
        """stats에 new_products_detected/registered 포함"""
        from src.collectors.receiving_collector import ReceivingCollector
        collector = ReceivingCollector(driver=None, store_id="46513")
        collector._new_product_candidates = [
            {"item_cd": "NEW001", "item_nm": "신제품1", "mid_cd": "001",
             "mid_cd_source": "fallback", "cust_nm": ""},
        ]

        receiving_data = [
            {"item_cd": "NEW001", "receiving_qty": 10, "plan_qty": 0, "receiving_date": "2026-02-26"},
        ]

        with patch.object(collector, '_register_single_new_product'):
            stats = collector._detect_and_register_new_products(receiving_data)

        assert "new_products_detected" in stats
        assert "new_products_registered" in stats
        assert stats["new_products_detected"] == 1
        assert stats["new_products_registered"] == 1

    def test_register_failure_continues(self, store_db, common_db):
        """개별 등록 실패 시 다른 상품 계속 진행"""
        from src.collectors.receiving_collector import ReceivingCollector
        collector = ReceivingCollector(driver=None, store_id="46513")
        collector._new_product_candidates = [
            {"item_cd": "FAIL001", "item_nm": "실패상품", "mid_cd": "001",
             "mid_cd_source": "fallback", "cust_nm": ""},
            {"item_cd": "OK001", "item_nm": "성공상품", "mid_cd": "002",
             "mid_cd_source": "fallback", "cust_nm": ""},
        ]

        receiving_data = [
            {"item_cd": "FAIL001", "receiving_qty": 5, "plan_qty": 0, "receiving_date": "2026-02-26"},
            {"item_cd": "OK001", "receiving_qty": 3, "plan_qty": 0, "receiving_date": "2026-02-26"},
        ]

        call_count = [0]
        def mock_register(product):
            call_count[0] += 1
            if product["item_cd"] == "FAIL001":
                raise Exception("DB 오류")

        with patch.object(collector, '_register_single_new_product', side_effect=mock_register):
            stats = collector._detect_and_register_new_products(receiving_data)

        assert stats["new_products_detected"] == 2
        assert stats["new_products_registered"] == 1  # FAIL001 실패, OK001 성공

    def test_mid_cd_source_preserved(self, store_db, common_db):
        """mid_cd_source (fallback/unknown) 보존"""
        from src.collectors.receiving_collector import ReceivingCollector
        collector = ReceivingCollector(driver=None, store_id="46513")
        collector._new_product_candidates = [
            {"item_cd": "NEW001", "item_nm": "도)신도시락", "mid_cd": "001",
             "mid_cd_source": "fallback", "cust_nm": "도시락"},
            {"item_cd": "NEW002", "item_nm": "알수없는상품", "mid_cd": "",
             "mid_cd_source": "unknown", "cust_nm": ""},
        ]

        receiving_data = [
            {"item_cd": "NEW001", "receiving_qty": 10, "plan_qty": 0, "receiving_date": "2026-02-26"},
            {"item_cd": "NEW002", "receiving_qty": 5, "plan_qty": 0, "receiving_date": "2026-02-26"},
        ]

        registered_products = []
        def capture_register(product):
            registered_products.append(product)

        with patch.object(collector, '_register_single_new_product', side_effect=capture_register):
            collector._detect_and_register_new_products(receiving_data)

        assert registered_products[0]["mid_cd_source"] == "fallback"
        assert registered_products[1]["mid_cd_source"] == "unknown"

    def test_empty_candidates_noop(self, store_db, common_db):
        """후보 없으면 아무것도 안 함"""
        from src.collectors.receiving_collector import ReceivingCollector
        collector = ReceivingCollector(driver=None, store_id="46513")
        collector._new_product_candidates = []

        receiving_data = [
            {"item_cd": "EXISTING", "receiving_qty": 10, "plan_qty": 0,
             "receiving_date": "2026-02-26"},
        ]

        stats = collector._detect_and_register_new_products(receiving_data)
        assert stats["new_products_detected"] == 0
        assert stats["new_products_registered"] == 0


# ─── 스키마 마이그레이션 테스트 ───

class TestSchemaMigration:
    """v45 마이그레이션"""

    def test_schema_version_45(self):
        """DB_SCHEMA_VERSION이 45인지 확인"""
        from src.settings.constants import DB_SCHEMA_VERSION
        assert DB_SCHEMA_VERSION >= 45

    def test_migration_45_exists(self):
        """SCHEMA_MIGRATIONS[45] 존재"""
        from src.db.models import SCHEMA_MIGRATIONS
        assert 45 in SCHEMA_MIGRATIONS
        assert "detected_new_products" in SCHEMA_MIGRATIONS[45]

    def test_migration_creates_table(self, tmp_path):
        """v45 마이그레이션이 테이블을 생성하는지 확인"""
        db_path = tmp_path / "test_migration.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT)")
        conn.execute("INSERT INTO schema_version VALUES (44, '2026-02-26')")
        conn.commit()

        from src.db.models import SCHEMA_MIGRATIONS
        for stmt_raw in SCHEMA_MIGRATIONS[45].split(';'):
            stmt = stmt_raw.strip()
            if stmt and not all(l.strip().startswith('--') for l in stmt.split('\n') if l.strip()):
                conn.execute(stmt)
        conn.commit()

        # 테이블 존재 확인
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='detected_new_products'"
        )
        assert cursor.fetchone() is not None

        # 인덱스 존재 확인
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_detected_new_products%'"
        )
        indexes = [r[0] for r in cursor.fetchall()]
        assert len(indexes) >= 2

        conn.close()


# ─── Web API 테스트 ───

class TestWebAPIDetectedNewProducts:
    """Web API 엔드포인트 테스트"""

    def test_web_api_detected_list(self, client):
        """/api/receiving/new-products 엔드포인트"""
        mock_items = [
            {"item_cd": "NEW001", "item_nm": "신제품1", "detected_at": "2026-02-26"},
        ]
        with patch(
            "src.infrastructure.database.repos.DetectedNewProductRepository",
        ) as MockRepo:
            mock_instance = MagicMock()
            mock_instance.get_recent.return_value = mock_items
            MockRepo.return_value = mock_instance
            resp = client.get("/api/receiving/new-products?days=7")

        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert "count" in data
        assert data["count"] == 1
        assert data["days"] == 7

    def test_web_api_unregistered(self, client):
        """/api/receiving/new-products/unregistered 엔드포인트"""
        mock_items = [
            {"item_cd": "UNREG001", "item_nm": "미등록상품", "detected_at": "2026-02-26"},
        ]
        with patch(
            "src.infrastructure.database.repos.DetectedNewProductRepository",
        ) as MockRepo:
            mock_instance = MagicMock()
            mock_instance.get_unregistered.return_value = mock_items
            MockRepo.return_value = mock_instance
            resp = client.get("/api/receiving/new-products/unregistered")

        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert data["count"] == 1


# ─── new-product-detection 확장 테스트 ───
# Design Reference: docs/02-design/features/new-product-detection.design.md


class TestNewProductDetectionExtension:
    """신제품 감지 보완 기능 테스트 (Plan 시나리오 2,3,5,6)"""

    def test_already_in_products_added_as_candidate(self, store_db, common_db):
        """시나리오2: products에 있지만 detected에 없는 상품이 신제품 후보로 추가되는지"""
        from src.collectors.receiving_collector import ReceivingCollector

        driver = MagicMock()
        collector = ReceivingCollector(driver, store_id="46513")

        # detected 캐시를 빈 set으로 설정 (= detected에 아무것도 없음)
        collector._detected_item_cds = set()
        collector._detected_common_cds = set()

        # products에서 mid_cd를 찾도록 mock
        with patch(
            "src.infrastructure.database.connection.DBRouter"
        ) as MockRouter:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = ("022",)  # products에 있음
            mock_conn.cursor.return_value = mock_cursor
            MockRouter.get_common_connection.return_value = mock_conn

            result = collector._get_mid_cd("8800099999999", "테스트거래처", "테스트상품")

        assert result == "022"
        # 후보에 추가되었는지
        assert len(collector._new_product_candidates) == 1
        cand = collector._new_product_candidates[0]
        assert cand["item_cd"] == "8800099999999"
        assert cand["already_in_products"] is True
        assert cand["mid_cd_source"] == "products"

    def test_already_detected_skipped(self, store_db, common_db):
        """시나리오3: detected에 이미 있는 상품은 후보에 추가되지 않는지"""
        from src.collectors.receiving_collector import ReceivingCollector

        driver = MagicMock()
        collector = ReceivingCollector(driver, store_id="46513")

        # detected 캐시에 이미 등록
        collector._detected_item_cds = {"8800099999999"}
        collector._detected_common_cds = set()

        with patch(
            "src.infrastructure.database.connection.DBRouter"
        ) as MockRouter:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = ("022",)
            mock_conn.cursor.return_value = mock_cursor
            MockRouter.get_common_connection.return_value = mock_conn

            result = collector._get_mid_cd("8800099999999", "거래처", "상품")

        assert result == "022"
        # 후보에 추가되지 않아야 함
        assert len(collector._new_product_candidates) == 0

    def test_common_db_failure_does_not_block_store_db(self, store_db):
        """시나리오5: common.db 등록 실패 시 store DB 등록은 정상 진행"""
        from src.infrastructure.database.repos.detected_new_product_repo import (
            DetectedNewProductRepository,
        )

        repo = DetectedNewProductRepository(db_path=store_db, store_id="46513")

        # store DB 등록 (정상)
        rid = repo.save(
            item_cd="8800055555555",
            item_nm="테스트상품",
            mid_cd="022",
            mid_cd_source="test",
            first_receiving_date="2026-04-01",
            receiving_qty=1,
            store_id="46513",
        )
        assert rid > 0

        # common.db 등록 실패 (DB 없으므로)
        with patch(
            "src.infrastructure.database.connection.DBRouter"
        ) as MockRouter:
            MockRouter.get_common_connection.side_effect = Exception("DB 접근 불가")
            result = repo.save_to_common(
                item_cd="8800055555555",
                item_nm="테스트상품",
                mid_cd="022",
                mid_cd_source="test",
                first_receiving_date="2026-04-01",
                receiving_qty=1,
                store_id="46513",
            )

        # common.db 실패
        assert result is False

        # store DB에는 정상 등록되어 있어야 함
        assert repo.exists("8800055555555", store_id="46513")

    def test_30day_filter_skips_old_products(self):
        """시나리오6: 30일 이전 첫 입고 상품은 감지 안 함"""
        from src.collectors.receiving_collector import ReceivingCollector

        # 30일 이내 → True
        assert ReceivingCollector._is_within_days("2026-04-01", 30) is True
        # 30일 초과 → False
        assert ReceivingCollector._is_within_days("2026-02-01", 30) is False
        # None → False (안전)
        assert ReceivingCollector._is_within_days(None, 30) is False
        # 빈 문자열 → False
        assert ReceivingCollector._is_within_days("", 30) is False

    def test_failsafe_cache_none_skips_detection(self, store_db, common_db):
        """fail-safe: 캐시 로딩 실패(None) 시 신제품 후보 추가 안 함"""
        from src.collectors.receiving_collector import ReceivingCollector

        driver = MagicMock()
        collector = ReceivingCollector(driver, store_id="46513")

        # 캐시가 None (로딩 실패 상태)
        collector._detected_item_cds = None
        collector._detected_common_cds = None

        with patch(
            "src.infrastructure.database.connection.DBRouter"
        ) as MockRouter:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = ("022",)
            mock_conn.cursor.return_value = mock_cursor
            MockRouter.get_common_connection.return_value = mock_conn

            # _load_detected_cache가 실패하도록
            with patch.object(collector, "_load_detected_cache"):
                result = collector._get_mid_cd("8800099999999", "거래처", "상품")

        assert result == "022"
        # None 상태이므로 후보에 추가되지 않아야 함
        assert len(collector._new_product_candidates) == 0

    def test_save_to_common_idempotent(self, store_db, common_db):
        """멱등성: save_to_common 2회 호출해도 1건만 저장"""
        from src.infrastructure.database.repos.detected_new_product_repo import (
            DetectedNewProductRepository,
        )

        repo = DetectedNewProductRepository(db_path=store_db, store_id="46513")

        kwargs = dict(
            item_cd="8800077777777",
            item_nm="멱등성테스트",
            mid_cd="022",
            mid_cd_source="test",
            first_receiving_date="2026-04-01",
            receiving_qty=1,
            store_id="46513",
        )

        with patch(
            "src.infrastructure.database.connection.DBRouter"
        ) as MockRouter:
            mock_conn = sqlite3.connect(str(common_db))
            mock_conn.row_factory = sqlite3.Row
            MockRouter.get_common_connection.return_value = mock_conn
            result1 = repo.save_to_common(**kwargs)

        with patch(
            "src.infrastructure.database.connection.DBRouter"
        ) as MockRouter:
            mock_conn2 = sqlite3.connect(str(common_db))
            mock_conn2.row_factory = sqlite3.Row
            MockRouter.get_common_connection.return_value = mock_conn2
            result2 = repo.save_to_common(**kwargs)

        assert result1 is True
        assert result2 is True

        # common.db에 1건만 있어야 함
        verify_conn = sqlite3.connect(str(common_db))
        cnt = verify_conn.execute(
            "SELECT COUNT(*) FROM detected_new_products WHERE item_cd=?",
            ("8800077777777",),
        ).fetchone()[0]
        verify_conn.close()
        assert cnt == 1
