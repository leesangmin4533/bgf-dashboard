"""
폐기 전표 수집 및 검증 테스트

WasteSlipRepository, WasteVerificationService 테스트
"""

import sqlite3
from datetime import datetime, timedelta

import pytest

# ================================================================
# Fixtures
# ================================================================


@pytest.fixture
def waste_db():
    """폐기 전표 테스트용 인메모리 DB"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sales_date TEXT NOT NULL,
            item_cd TEXT NOT NULL,
            mid_cd TEXT,
            sale_qty INTEGER DEFAULT 0,
            disuse_qty INTEGER DEFAULT 0,
            stock_qty INTEGER DEFAULT 0,
            store_id TEXT,
            created_at TEXT,
            UNIQUE(sales_date, item_cd)
        )
    """)

    conn.execute("""
        CREATE TABLE waste_slips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            chit_date TEXT NOT NULL,
            chit_no TEXT NOT NULL,
            chit_flag TEXT,
            chit_id TEXT,
            chit_id_nm TEXT,
            item_cnt INTEGER DEFAULT 0,
            center_cd TEXT,
            center_nm TEXT,
            wonga_amt REAL DEFAULT 0,
            maega_amt REAL DEFAULT 0,
            nap_plan_ymd TEXT,
            conf_id TEXT,
            cre_ymdhms TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            UNIQUE(store_id, chit_date, chit_no)
        )
    """)

    conn.execute("""
        CREATE TABLE waste_verification_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            verification_date TEXT NOT NULL,
            slip_count INTEGER DEFAULT 0,
            slip_item_count INTEGER DEFAULT 0,
            daily_sales_disuse_count INTEGER DEFAULT 0,
            gap INTEGER DEFAULT 0,
            gap_percentage REAL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'UNKNOWN',
            details TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(store_id, verification_date)
        )
    """)

    conn.commit()
    yield conn
    conn.close()


# ================================================================
# WasteSlipRepository 테스트
# ================================================================


class TestWasteSlipRepository:
    """WasteSlipRepository 테스트"""

    def test_save_waste_slips(self, waste_db, monkeypatch):
        """폐기 전표 저장"""
        from src.infrastructure.database.repos.waste_slip_repo import WasteSlipRepository

        repo = WasteSlipRepository(store_id="46513")
        monkeypatch.setattr(repo, "_get_conn", lambda: waste_db)

        slips = [
            {
                "CHIT_YMD": "20260219",
                "CHIT_NO": "10044420011",
                "CHIT_FLAG": "PC",
                "CHIT_ID": "04",
                "CHIT_ID_NM": "폐기",
                "ITEM_CNT": 1,
                "CENTER_CD": "9512204",
                "CENTER_NM": "씨제이오산냉장1",
                "WONGA_AMT": 3835,
                "MAEGA_AMT": 5900,
                "NAP_PLAN_YMD": "20260219",
                "CONF_ID": "확정(점포)",
                "CRE_YMDHMS": "20260219021002",
            },
            {
                "CHIT_YMD": "20260219",
                "CHIT_NO": "10044430011",
                "CHIT_FLAG": "PC",
                "CHIT_ID": "04",
                "CHIT_ID_NM": "폐기",
                "ITEM_CNT": 3,
                "CENTER_CD": "9512204",
                "CENTER_NM": "씨제이오산냉장1",
                "WONGA_AMT": 2100,
                "MAEGA_AMT": 3400,
                "NAP_PLAN_YMD": "20260219",
                "CONF_ID": "확정(점포)",
                "CRE_YMDHMS": "20260219025328",
            },
        ]

        saved = repo.save_waste_slips(slips, store_id="46513")
        assert saved == 2

        # 조회 확인
        cursor = waste_db.cursor()
        cursor.execute("SELECT COUNT(*) FROM waste_slips")
        assert cursor.fetchone()[0] == 2

        # 날짜 변환 확인
        cursor.execute("SELECT chit_date FROM waste_slips ORDER BY id")
        rows = cursor.fetchall()
        assert rows[0][0] == "2026-02-19"
        assert rows[1][0] == "2026-02-19"

    def test_save_waste_slips_upsert(self, waste_db, monkeypatch):
        """동일 전표 중복 저장 시 업데이트"""
        from src.infrastructure.database.repos.waste_slip_repo import WasteSlipRepository

        repo = WasteSlipRepository(store_id="46513")
        monkeypatch.setattr(repo, "_get_conn", lambda: waste_db)

        slip = [{
            "CHIT_YMD": "20260219",
            "CHIT_NO": "10044420011",
            "CHIT_FLAG": "PC",
            "CHIT_ID": "04",
            "CHIT_ID_NM": "폐기",
            "ITEM_CNT": 1,
            "WONGA_AMT": 3835,
            "MAEGA_AMT": 5900,
        }]

        repo.save_waste_slips(slip, store_id="46513")
        # 동일 전표 다시 저장 (업데이트)
        slip[0]["ITEM_CNT"] = 5
        repo.save_waste_slips(slip, store_id="46513")

        cursor = waste_db.cursor()
        cursor.execute("SELECT COUNT(*) FROM waste_slips")
        assert cursor.fetchone()[0] == 1

        cursor.execute("SELECT item_cnt FROM waste_slips")
        assert cursor.fetchone()[0] == 5

    def test_get_waste_slips(self, waste_db, monkeypatch):
        """기간별 폐기 전표 조회"""
        from src.infrastructure.database.repos.waste_slip_repo import WasteSlipRepository

        repo = WasteSlipRepository(store_id="46513")
        monkeypatch.setattr(repo, "_get_conn", lambda: waste_db)

        # 2일치 데이터 삽입
        for date_str, count in [("2026-02-18", 3), ("2026-02-19", 2)]:
            for i in range(count):
                waste_db.execute(
                    """INSERT INTO waste_slips
                    (store_id, chit_date, chit_no, item_cnt, wonga_amt, maega_amt, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    ("46513", date_str, f"CHIT_{date_str}_{i}", i + 1, 1000 * (i + 1), 1500 * (i + 1),
                     datetime.now().isoformat()),
                )
        waste_db.commit()

        slips = repo.get_waste_slips("2026-02-18", "2026-02-19", store_id="46513")
        assert len(slips) == 5

        # 하루만 조회
        slips_one_day = repo.get_waste_slips("2026-02-19", "2026-02-19", store_id="46513")
        assert len(slips_one_day) == 2

    def test_get_daily_waste_summary(self, waste_db, monkeypatch):
        """날짜별 폐기 요약"""
        from src.infrastructure.database.repos.waste_slip_repo import WasteSlipRepository

        repo = WasteSlipRepository(store_id="46513")
        monkeypatch.setattr(repo, "_get_conn", lambda: waste_db)

        # 테스트 데이터
        now = datetime.now().isoformat()
        waste_db.execute(
            "INSERT INTO waste_slips (store_id, chit_date, chit_no, item_cnt, wonga_amt, maega_amt, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("46513", "2026-02-19", "CHIT001", 3, 5000, 8000, now),
        )
        waste_db.execute(
            "INSERT INTO waste_slips (store_id, chit_date, chit_no, item_cnt, wonga_amt, maega_amt, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("46513", "2026-02-19", "CHIT002", 2, 3000, 5000, now),
        )
        waste_db.commit()

        summary = repo.get_daily_waste_summary("2026-02-19", "2026-02-19", store_id="46513")
        assert len(summary) == 1
        assert summary[0]["slip_count"] == 2
        assert summary[0]["item_count"] == 5
        assert summary[0]["wonga_total"] == 8000
        assert summary[0]["maega_total"] == 13000

    def test_get_latest_collection_date(self, waste_db, monkeypatch):
        """최신 수집 날짜 조회"""
        from src.infrastructure.database.repos.waste_slip_repo import WasteSlipRepository

        repo = WasteSlipRepository(store_id="46513")
        monkeypatch.setattr(repo, "_get_conn", lambda: waste_db)

        # 빈 상태
        assert repo.get_latest_collection_date(store_id="46513") is None

        # 데이터 삽입
        now = datetime.now().isoformat()
        waste_db.execute(
            "INSERT INTO waste_slips (store_id, chit_date, chit_no, item_cnt, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("46513", "2026-02-17", "CHIT001", 1, now),
        )
        waste_db.execute(
            "INSERT INTO waste_slips (store_id, chit_date, chit_no, item_cnt, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("46513", "2026-02-19", "CHIT002", 1, now),
        )
        waste_db.commit()

        assert repo.get_latest_collection_date(store_id="46513") == "2026-02-19"

    def test_save_verification_result(self, waste_db, monkeypatch):
        """검증 결과 저장"""
        from src.infrastructure.database.repos.waste_slip_repo import WasteSlipRepository

        repo = WasteSlipRepository(store_id="46513")
        monkeypatch.setattr(repo, "_get_conn", lambda: waste_db)

        record_id = repo.save_verification_result(
            verification_date="2026-02-19",
            slip_count=5,
            slip_item_count=12,
            daily_sales_disuse_count=4,
            gap=8,
            gap_percentage=66.7,
            status="MISMATCH",
            details='{"wonga_total": 50000}',
            store_id="46513",
        )
        assert record_id > 0

        # 이력 조회
        history = repo.get_verification_history(days=30, store_id="46513")
        assert len(history) == 1
        assert history[0]["status"] == "MISMATCH"
        assert history[0]["gap"] == 8

    def test_decimal_handling(self, waste_db, monkeypatch):
        """Decimal 타입(dict) 처리"""
        from src.infrastructure.database.repos.waste_slip_repo import WasteSlipRepository

        repo = WasteSlipRepository(store_id="46513")
        monkeypatch.setattr(repo, "_get_conn", lambda: waste_db)

        slips = [{
            "CHIT_YMD": "20260219",
            "CHIT_NO": "CHIT_DEC",
            "WONGA_AMT": {"hi": 3835.5},
            "MAEGA_AMT": {"hi": 5900},
            "ITEM_CNT": 2,
        }]

        saved = repo.save_waste_slips(slips, store_id="46513")
        assert saved == 1

        cursor = waste_db.cursor()
        cursor.execute("SELECT wonga_amt, maega_amt FROM waste_slips")
        row = cursor.fetchone()
        assert row[0] == 3835.5
        assert row[1] == 5900


# ================================================================
# WasteVerificationService 테스트
# ================================================================


class TestWasteVerificationService:
    """WasteVerificationService 테스트"""

    def _setup_service(self, waste_db, monkeypatch):
        """서비스 설정 헬퍼"""
        from src.application.services.waste_verification_service import WasteVerificationService

        service = WasteVerificationService(store_id="46513")
        monkeypatch.setattr(service.slip_repo, "_get_conn", lambda: waste_db)
        monkeypatch.setattr(service.sales_repo, "_get_conn", lambda: waste_db)
        return service

    def test_verify_date_no_data(self, waste_db, monkeypatch):
        """데이터 없는 날짜 검증"""
        service = self._setup_service(waste_db, monkeypatch)

        result = service.verify_date("2026-02-19", auto_save=False)
        assert result["status"] == "NO_DATA"
        assert result["gap"] == 0

    def test_verify_date_slip_missing(self, waste_db, monkeypatch):
        """전표 없고 매출분석에만 폐기 있음"""
        service = self._setup_service(waste_db, monkeypatch)

        # daily_sales에 폐기 데이터
        waste_db.execute(
            "INSERT INTO daily_sales (sales_date, item_cd, disuse_qty, created_at)"
            " VALUES (?, ?, ?, ?)",
            ("2026-02-19", "ITEM001", 2, datetime.now().isoformat()),
        )
        waste_db.commit()

        result = service.verify_date("2026-02-19", auto_save=False)
        assert result["status"] == "SLIP_MISSING"
        assert result["daily_sales_disuse_count"] == 1

    def test_verify_date_sales_missing(self, waste_db, monkeypatch):
        """전표 있고 매출분석에 폐기 없음"""
        service = self._setup_service(waste_db, monkeypatch)

        # waste_slips에 데이터
        now = datetime.now().isoformat()
        waste_db.execute(
            "INSERT INTO waste_slips (store_id, chit_date, chit_no, item_cnt, wonga_amt, maega_amt, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("46513", "2026-02-19", "CHIT001", 3, 5000, 8000, now),
        )
        waste_db.commit()

        result = service.verify_date("2026-02-19", auto_save=False)
        assert result["status"] == "SALES_MISSING"
        assert result["slip_item_count"] == 3
        assert result["gap"] == 3

    def test_verify_date_ok(self, waste_db, monkeypatch):
        """전표와 매출분석 일치"""
        service = self._setup_service(waste_db, monkeypatch)

        now = datetime.now().isoformat()
        # 전표: 품목 3건
        waste_db.execute(
            "INSERT INTO waste_slips (store_id, chit_date, chit_no, item_cnt, wonga_amt, maega_amt, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("46513", "2026-02-19", "CHIT001", 3, 5000, 8000, now),
        )
        # 매출분석: 3건 (일치)
        for i in range(3):
            waste_db.execute(
                "INSERT INTO daily_sales (sales_date, item_cd, disuse_qty, created_at)"
                " VALUES (?, ?, ?, ?)",
                ("2026-02-19", f"ITEM{i:03d}", 1, now),
            )
        waste_db.commit()

        result = service.verify_date("2026-02-19", auto_save=False)
        assert result["status"] == "OK"
        assert result["gap"] == 0

    def test_verify_date_mismatch(self, waste_db, monkeypatch):
        """전표와 매출분석 불일치"""
        service = self._setup_service(waste_db, monkeypatch)

        now = datetime.now().isoformat()
        # 전표: 품목 10건
        waste_db.execute(
            "INSERT INTO waste_slips (store_id, chit_date, chit_no, item_cnt, wonga_amt, maega_amt, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("46513", "2026-02-19", "CHIT001", 10, 50000, 80000, now),
        )
        # 매출분석: 3건 (불일치 - 70% 갭)
        for i in range(3):
            waste_db.execute(
                "INSERT INTO daily_sales (sales_date, item_cd, disuse_qty, created_at)"
                " VALUES (?, ?, ?, ?)",
                ("2026-02-19", f"ITEM{i:03d}", 1, now),
            )
        waste_db.commit()

        result = service.verify_date("2026-02-19", auto_save=False)
        assert result["status"] == "MISMATCH"
        assert result["slip_item_count"] == 10
        assert result["daily_sales_disuse_count"] == 3
        assert result["gap"] == 7
        assert result["gap_percentage"] == 70.0

    def test_verify_date_auto_save(self, waste_db, monkeypatch):
        """검증 결과 자동 저장"""
        service = self._setup_service(waste_db, monkeypatch)

        result = service.verify_date("2026-02-19", auto_save=True)
        assert result["status"] == "NO_DATA"

        # verification_log에 저장 확인
        cursor = waste_db.cursor()
        cursor.execute("SELECT COUNT(*) FROM waste_verification_log")
        assert cursor.fetchone()[0] == 1

        cursor.execute("SELECT status FROM waste_verification_log WHERE verification_date = ?",
                        ("2026-02-19",))
        assert cursor.fetchone()[0] == "NO_DATA"

    def test_verify_range(self, waste_db, monkeypatch):
        """기간 검증"""
        service = self._setup_service(waste_db, monkeypatch)

        now = datetime.now().isoformat()
        # 2일치 데이터 (하루만 전표 있음)
        waste_db.execute(
            "INSERT INTO waste_slips (store_id, chit_date, chit_no, item_cnt, wonga_amt, maega_amt, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("46513", "2026-02-18", "CHIT001", 5, 10000, 15000, now),
        )
        waste_db.execute(
            "INSERT INTO daily_sales (sales_date, item_cd, disuse_qty, created_at)"
            " VALUES (?, ?, ?, ?)",
            ("2026-02-18", "ITEM001", 2, now),
        )
        waste_db.commit()

        result = service.verify_range("2026-02-18", "2026-02-19", auto_save=False)

        assert result["summary"]["total_days"] == 2
        # 2/18: MISMATCH (전표 5, 매출 1)
        # 2/19: NO_DATA
        assert result["summary"]["MISMATCH"] == 1 or result["summary"]["SALES_MISSING"] == 0
        assert result["summary"]["NO_DATA"] == 1
        assert len(result["daily"]) == 2

    def test_get_gap_summary(self, waste_db, monkeypatch):
        """갭 요약"""
        service = self._setup_service(waste_db, monkeypatch)

        now = datetime.now().isoformat()
        waste_db.execute(
            "INSERT INTO waste_slips (store_id, chit_date, chit_no, item_cnt, wonga_amt, maega_amt, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("46513", "2026-02-19", "CHIT001", 5, 10000, 15000, now),
        )
        waste_db.execute(
            "INSERT INTO waste_slips (store_id, chit_date, chit_no, item_cnt, wonga_amt, maega_amt, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("46513", "2026-02-19", "CHIT002", 3, 6000, 9000, now),
        )
        waste_db.execute(
            "INSERT INTO daily_sales (sales_date, item_cd, disuse_qty, created_at)"
            " VALUES (?, ?, ?, ?)",
            ("2026-02-19", "ITEM001", 2, now),
        )
        waste_db.commit()

        gap = service.get_gap_summary("2026-02-19", "2026-02-19")
        assert gap["slip_total_items"] == 8
        assert gap["daily_sales_disuse_count"] == 1
        assert gap["gap"] == 7
        assert gap["slip_total_wonga"] == 16000
        assert gap["slip_total_maega"] == 24000


# ================================================================
# WasteSlipCollector 테스트 (단위 - 드라이버 Mock)
# ================================================================


class TestWasteSlipCollector:
    """WasteSlipCollector 단위 테스트"""

    def test_init(self):
        """초기화"""
        from src.collectors.waste_slip_collector import WasteSlipCollector

        collector = WasteSlipCollector(store_id="46513")
        assert collector.store_id == "46513"
        assert collector.driver is None
        assert collector.FRAME_ID == "STGJ020_M0"

    def test_collect_without_driver(self):
        """드라이버 없이 수집 시도"""
        from src.collectors.waste_slip_collector import WasteSlipCollector

        collector = WasteSlipCollector(store_id="46513")
        result = collector.collect_waste_slips()
        assert result["success"] is False
        assert "driver" in result["error"]

    def test_set_driver(self):
        """드라이버 설정"""
        from src.collectors.waste_slip_collector import WasteSlipCollector

        collector = WasteSlipCollector(store_id="46513")
        mock_driver = object()
        collector.set_driver(mock_driver)
        assert collector.driver is mock_driver

    def test_waste_chit_div_code(self):
        """폐기 전표 구분 코드 확인"""
        from src.collectors.waste_slip_collector import WASTE_CHIT_DIV_CODE
        assert WASTE_CHIT_DIV_CODE == "10"

    def test_default_days_is_1(self):
        """기본 수집 기간이 1일(전날+당일)인지 확인"""
        import inspect
        from src.collectors.waste_slip_collector import WasteSlipCollector

        sig = inspect.signature(WasteSlipCollector.collect_waste_slips)
        assert sig.parameters["days"].default == 1

    def test_collect_today_covers_yesterday_and_today(self, monkeypatch):
        """collect_today_waste_slips가 전날+당일 범위로 호출되는지 확인"""
        from src.collectors.waste_slip_collector import WasteSlipCollector
        from datetime import datetime, timedelta

        collector = WasteSlipCollector(store_id="46513")
        called_with = {}

        def mock_collect(from_date=None, to_date=None, days=1, save_to_db=True):
            called_with["from_date"] = from_date
            called_with["to_date"] = to_date
            return {"success": True, "count": 0}

        monkeypatch.setattr(collector, "collect_waste_slips", mock_collect)
        collector.collect_today_waste_slips()

        now = datetime.now()
        expected_yesterday = (now - timedelta(days=1)).strftime("%Y%m%d")
        expected_today = now.strftime("%Y%m%d")
        assert called_with["from_date"] == expected_yesterday
        assert called_with["to_date"] == expected_today


# ================================================================
# UI 설정 테스트
# ================================================================


class TestUiConfig:
    """UI 설정에 WASTE_SLIP 추가 확인"""

    def test_waste_slip_frame_id(self):
        """WASTE_SLIP 프레임 ID 설정"""
        from src.settings.ui_config import FRAME_IDS
        assert "WASTE_SLIP" in FRAME_IDS
        assert FRAME_IDS["WASTE_SLIP"] == "STGJ020_M0"

    def test_waste_slip_submenu(self):
        """WASTE_SLIP 서브메뉴 설정"""
        from src.settings.ui_config import SUBMENU_TEXT
        assert "WASTE_SLIP" in SUBMENU_TEXT
        assert SUBMENU_TEXT["WASTE_SLIP"] == "통합 전표 조회"

    def test_schema_version(self):
        """DB 스키마 버전이 최신인지"""
        from src.settings.constants import DB_SCHEMA_VERSION
        assert DB_SCHEMA_VERSION >= 36
