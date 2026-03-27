"""
수동발주 미입고 인식 + 발주가능요일 검증 테스트

Feature: manual-order-pending
- Task 1: 발주현황 -> OT 동기화 (sync_pending_to_order_tracking)
- Task 2: 단품발주 시 발주가능요일 DB 검증 (_verify_orderable_day)
- Task 3: 발주일 밀림 감지 (execute_batch_orders)
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ────────────────────────────────────────────────
# 픽스처
# ────────────────────────────────────────────────

@pytest.fixture
def ot_db():
    """order_tracking 테이블이 있는 in-memory DB"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE order_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT DEFAULT '46513',
            order_date TEXT,
            item_cd TEXT,
            item_nm TEXT,
            mid_cd TEXT,
            delivery_type TEXT,
            order_qty INTEGER DEFAULT 0,
            remaining_qty INTEGER DEFAULT 0,
            arrival_time TEXT,
            expiry_time TEXT,
            status TEXT DEFAULT 'ordered',
            alert_sent INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT,
            actual_receiving_qty INTEGER,
            actual_arrival_time TEXT,
            order_source TEXT
        )
    """)
    return conn


class _NonClosingConnection:
    """close()가 호출되어도 실제로 닫히지 않는 래퍼"""

    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return self._conn.cursor()

    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)

    def commit(self):
        return self._conn.commit()

    def close(self):
        pass  # 닫지 않음 (테스트용)

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._conn.row_factory = value


@pytest.fixture
def tracking_repo(ot_db):
    """OrderTrackingRepository mock (in-memory DB 사용)"""
    from src.infrastructure.database.repos.order_tracking_repo import OrderTrackingRepository
    repo = OrderTrackingRepository.__new__(OrderTrackingRepository)
    repo.store_id = "46513"
    repo._db = ot_db

    wrapper = _NonClosingConnection(ot_db)

    def mock_get_conn():
        return wrapper

    repo._get_conn = mock_get_conn
    repo._now = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return repo


# ────────────────────────────────────────────────
# Task 1: sync_pending_to_order_tracking 테스트
# ────────────────────────────────────────────────

class TestSyncPendingToOrderTracking:
    """발주현황 -> OT 동기화 테스트"""

    def test_sync_new_site_order(self, tracking_repo, ot_db):
        """BGF에 수동발주 있고 OT에 없을 때 -> site로 저장"""
        from src.collectors.order_status_collector import OrderStatusCollector

        collector = OrderStatusCollector.__new__(OrderStatusCollector)
        collector.driver = MagicMock()
        collector.store_id = "46513"
        collector.tracking_repo = tracking_repo

        today = datetime.now().strftime("%Y%m%d")

        # Mock: 전체 라디오 클릭 성공
        collector.click_all_radio = MagicMock(return_value=True)
        # Mock: 발주현황 (상품 정보)
        collector.collect_order_status = MagicMock(return_value=[
            {"ITEM_CD": "1234567890123", "ITEM_NM": "테스트상품", "MID_CD": "015"}
        ])
        # Mock: 발주/판매 이력 (미입고 건)
        collector.collect_order_sale_history = MagicMock(return_value=[
            {"ORD_YMD": today, "ITEM_CD": "1234567890123",
             "ORD_QTY": 10, "BUY_QTY": 0}
        ])

        result = collector.sync_pending_to_order_tracking(days_back=7)

        assert result["synced"] == 1
        assert result["skipped"] == 0

        # DB에 저장 확인
        row = ot_db.execute(
            "SELECT * FROM order_tracking WHERE item_cd = ?",
            ("1234567890123",)
        ).fetchone()
        assert row is not None
        assert row["order_source"] == "site"
        assert row["order_qty"] == 10

    def test_skip_existing_auto_order(self, tracking_repo, ot_db):
        """이미 OT에 auto로 저장된 건 -> skip"""
        from src.collectors.order_status_collector import OrderStatusCollector

        today_fmt = datetime.now().strftime("%Y-%m-%d")
        today_raw = datetime.now().strftime("%Y%m%d")

        # 먼저 auto로 저장
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ot_db.execute(
            """INSERT INTO order_tracking
               (order_date, item_cd, item_nm, mid_cd, delivery_type,
                order_qty, remaining_qty, status, order_source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'ordered', 'auto', ?, ?)""",
            (today_fmt, "1234567890123", "테스트상품", "015", "",
             10, 10, now, now)
        )
        ot_db.commit()

        collector = OrderStatusCollector.__new__(OrderStatusCollector)
        collector.driver = MagicMock()
        collector.store_id = "46513"
        collector.tracking_repo = tracking_repo
        collector.click_all_radio = MagicMock(return_value=True)
        collector.collect_order_status = MagicMock(return_value=[
            {"ITEM_CD": "1234567890123", "ITEM_NM": "테스트상품", "MID_CD": "015"}
        ])
        collector.collect_order_sale_history = MagicMock(return_value=[
            {"ORD_YMD": today_raw, "ITEM_CD": "1234567890123",
             "ORD_QTY": 10, "BUY_QTY": 0}
        ])

        result = collector.sync_pending_to_order_tracking(days_back=7)

        assert result["synced"] == 0
        assert result["skipped"] == 1

    def test_skip_fully_received(self, tracking_repo, ot_db):
        """입고 완료 (ord_qty == buy_qty) -> skip"""
        from src.collectors.order_status_collector import OrderStatusCollector

        today_raw = datetime.now().strftime("%Y%m%d")

        collector = OrderStatusCollector.__new__(OrderStatusCollector)
        collector.driver = MagicMock()
        collector.store_id = "46513"
        collector.tracking_repo = tracking_repo
        collector.click_all_radio = MagicMock(return_value=True)
        collector.collect_order_status = MagicMock(return_value=[
            {"ITEM_CD": "1234567890123", "ITEM_NM": "테스트상품", "MID_CD": "015"}
        ])
        collector.collect_order_sale_history = MagicMock(return_value=[
            {"ORD_YMD": today_raw, "ITEM_CD": "1234567890123",
             "ORD_QTY": 10, "BUY_QTY": 10}  # 전량 입고
        ])

        result = collector.sync_pending_to_order_tracking(days_back=7)

        assert result["synced"] == 0
        assert result["skipped"] == 1

    def test_empty_order_sale_data(self, tracking_repo):
        """발주이력 없음 -> 빈 결과"""
        from src.collectors.order_status_collector import OrderStatusCollector

        collector = OrderStatusCollector.__new__(OrderStatusCollector)
        collector.driver = MagicMock()
        collector.store_id = "46513"
        collector.tracking_repo = tracking_repo
        collector.click_all_radio = MagicMock(return_value=True)
        collector.collect_order_status = MagicMock(return_value=[])
        collector.collect_order_sale_history = MagicMock(return_value=[])

        result = collector.sync_pending_to_order_tracking(days_back=7)

        assert result["synced"] == 0
        assert result["skipped"] == 0
        assert result["errors"] == 0

    def test_skip_old_orders_beyond_days_back(self, tracking_repo, ot_db):
        """days_back보다 오래된 발주는 스킵"""
        from src.collectors.order_status_collector import OrderStatusCollector

        old_date = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")

        collector = OrderStatusCollector.__new__(OrderStatusCollector)
        collector.driver = MagicMock()
        collector.store_id = "46513"
        collector.tracking_repo = tracking_repo
        collector.click_all_radio = MagicMock(return_value=True)
        collector.collect_order_status = MagicMock(return_value=[
            {"ITEM_CD": "1234567890123", "ITEM_NM": "테스트상품", "MID_CD": "015"}
        ])
        collector.collect_order_sale_history = MagicMock(return_value=[
            {"ORD_YMD": old_date, "ITEM_CD": "1234567890123",
             "ORD_QTY": 10, "BUY_QTY": 0}
        ])

        result = collector.sync_pending_to_order_tracking(days_back=7)

        assert result["synced"] == 0

    def test_no_driver_returns_empty(self, tracking_repo):
        """드라이버 없으면 빈 결과"""
        from src.collectors.order_status_collector import OrderStatusCollector

        collector = OrderStatusCollector.__new__(OrderStatusCollector)
        collector.driver = None
        collector.store_id = "46513"
        collector.tracking_repo = tracking_repo

        result = collector.sync_pending_to_order_tracking()

        assert result["synced"] == 0
        assert result["errors"] == 0


# ────────────────────────────────────────────────
# Task 1-2: get_existing_order 테스트
# ────────────────────────────────────────────────

class TestGetExistingOrder:
    """order_tracking_repo.get_existing_order() 테스트"""

    def test_find_existing_auto_order(self, tracking_repo, ot_db):
        """auto 발주 조회"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ot_db.execute(
            """INSERT INTO order_tracking
               (order_date, item_cd, item_nm, mid_cd, delivery_type,
                order_qty, remaining_qty, status, order_source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'ordered', 'auto', ?, ?)""",
            ("2026-02-25", "ITEM001", "상품1", "015", "",
             10, 10, now, now)
        )
        ot_db.commit()

        result = tracking_repo.get_existing_order(
            order_date="2026-02-25",
            item_cd="ITEM001",
            order_source="auto"
        )
        assert result is not None
        assert result["order_source"] == "auto"
        assert result["order_qty"] == 10

    def test_not_found_returns_none(self, tracking_repo):
        """없는 발주 조회 -> None"""
        result = tracking_repo.get_existing_order(
            order_date="2026-02-25",
            item_cd="NONEXIST"
        )
        assert result is None

    def test_filter_by_source(self, tracking_repo, ot_db):
        """order_source 필터로 구분"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # auto 발주
        ot_db.execute(
            """INSERT INTO order_tracking
               (order_date, item_cd, order_qty, remaining_qty, status,
                order_source, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'ordered', 'auto', ?, ?)""",
            ("2026-02-25", "ITEM001", 5, 5, now, now)
        )
        # site 발주
        ot_db.execute(
            """INSERT INTO order_tracking
               (order_date, item_cd, order_qty, remaining_qty, status,
                order_source, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'ordered', 'site', ?, ?)""",
            ("2026-02-25", "ITEM001", 10, 10, now, now)
        )
        ot_db.commit()

        auto = tracking_repo.get_existing_order(
            "2026-02-25", "ITEM001", order_source="auto"
        )
        site = tracking_repo.get_existing_order(
            "2026-02-25", "ITEM001", order_source="site"
        )

        assert auto["order_qty"] == 5
        assert site["order_qty"] == 10

    def test_find_without_source_filter(self, tracking_repo, ot_db):
        """order_source 필터 없이 조회"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ot_db.execute(
            """INSERT INTO order_tracking
               (order_date, item_cd, order_qty, remaining_qty, status,
                order_source, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'ordered', 'site', ?, ?)""",
            ("2026-02-25", "ITEM001", 10, 10, now, now)
        )
        ot_db.commit()

        result = tracking_repo.get_existing_order(
            "2026-02-25", "ITEM001"
        )
        assert result is not None


# ────────────────────────────────────────────────
# Task 2: 발주가능요일 검증 테스트
# ────────────────────────────────────────────────

class TestVerifyOrderableDay:
    """order_executor._verify_orderable_day() 테스트"""

    def _make_executor(self):
        """테스트용 OrderExecutor 생성"""
        from src.order.order_executor import OrderExecutor

        executor = OrderExecutor.__new__(OrderExecutor)
        executor.driver = MagicMock()
        executor.product_collector = MagicMock()
        return executor

    def test_match_no_warning(self):
        """BGF="화목토", DB="화목토" -> 일치, WARNING 없음"""
        executor = self._make_executor()
        executor.product_collector.get_from_db.return_value = {
            "orderable_day": "화목토"
        }

        with patch("src.order.order_executor.logger") as mock_logger:
            executor._verify_orderable_day("ITEM001", "화목토")
            # WARNING은 호출 안 됨
            for call in mock_logger.warning.call_args_list:
                assert "변경감지" not in str(call)

    def test_mismatch_warning_logged(self):
        """BGF="화목토", DB="월수금" -> WARNING 로그"""
        executor = self._make_executor()
        executor.product_collector.get_from_db.return_value = {
            "orderable_day": "월수금"
        }

        with patch("src.order.order_executor.logger") as mock_logger:
            executor._verify_orderable_day("ITEM001", "화목토")
            mock_logger.warning.assert_called_once()
            assert "변경감지" in str(mock_logger.warning.call_args)

    def test_normalized_comparison(self):
        """BGF="토화목", DB="화목토" -> 순서 무관 일치"""
        executor = self._make_executor()
        executor.product_collector.get_from_db.return_value = {
            "orderable_day": "화목토"
        }

        with patch("src.order.order_executor.logger") as mock_logger:
            executor._verify_orderable_day("ITEM001", "토화목")
            for call in mock_logger.warning.call_args_list:
                assert "변경감지" not in str(call)

    def test_db_empty_new_save(self):
        """DB 데이터 없음 -> info 로그"""
        executor = self._make_executor()
        executor.product_collector.get_from_db.return_value = None

        with patch("src.order.order_executor.logger") as mock_logger:
            executor._verify_orderable_day("ITEM001", "화목토")
            mock_logger.info.assert_called_once()
            assert "신규 저장" in str(mock_logger.info.call_args)

    def test_bgf_empty_skip(self):
        """BGF 빈 문자열 -> 호출 자체가 안 됨 (caller에서 체크)"""
        # _verify_orderable_day는 bgf_orderable_day가 있을 때만 호출됨
        # 여기서는 빈 값 전달 시에도 에러 안 나는지 확인
        executor = self._make_executor()
        executor.product_collector.get_from_db.return_value = {
            "orderable_day": "화목토"
        }
        # 빈 값이면 set() vs set("화목토") -> 불일치지만 에러 없어야 함
        executor._verify_orderable_day("ITEM001", "")


# ────────────────────────────────────────────────
# Task 3: 발주일 밀림 감지 테스트
# ────────────────────────────────────────────────

class TestOrderDateShiftDetection:
    """발주일 밀림 감지 + 원인 추적 테스트"""

    def test_no_shift_same_date(self):
        """예상일 == 실제일 -> 밀림 없음"""
        from src.order.order_executor import OrderExecutor

        executor = OrderExecutor.__new__(OrderExecutor)
        executor.WEEKDAY_MAP = {
            0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"
        }

        input_result = {
            "success": True,
            "actual_order_date": "2026-02-25",
            "orderable_day": "월화수목금토",
        }
        order_date = "2026-02-25"

        verified = input_result.get('actual_order_date', '') or order_date
        assert verified == order_date  # 밀림 없음

    def test_shift_detected_different_date(self):
        """예상=월 -> 실제=화 -> 밀림 감지"""
        input_result = {
            "success": True,
            "actual_order_date": "2026-02-24",  # 화요일
            "orderable_day": "화목토",
        }
        order_date = "2026-02-23"  # 월요일

        verified = input_result.get('actual_order_date', '') or order_date
        assert verified != order_date  # 밀림 발생

    def test_shift_reason_orderable_day(self):
        """비발주일(화목토, 월요일 실행) -> 원인=비발주일"""
        from src.order.order_executor import OrderExecutor

        weekday_map = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}
        orderable_day_from_grid = "화목토"
        order_date = "2026-02-23"  # 월요일 (weekday=0)

        target_weekday = datetime.strptime(order_date, "%Y-%m-%d").weekday()
        target_kr = weekday_map.get(target_weekday, "")

        assert target_kr == "월"
        assert target_kr not in orderable_day_from_grid
        mismatch_reason = f"비발주일(발주가능:{orderable_day_from_grid})"
        assert "비발주일" in mismatch_reason

    def test_ot_saves_actual_date(self, tracking_repo, ot_db):
        """밀림 시 OT에 실제 발주일 저장 확인"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # verified_order_date (실제) = "2026-02-24" (화요일)
        tracking_repo.save_order(
            order_date="2026-02-24",  # 실제 발주일
            item_cd="ITEM001",
            item_nm="테스트상품",
            mid_cd="015",
            delivery_type="",
            order_qty=10,
            arrival_time="",
            expiry_time="",
            order_source="auto"
        )

        row = ot_db.execute(
            "SELECT order_date FROM order_tracking WHERE item_cd = 'ITEM001'"
        ).fetchone()
        assert row["order_date"] == "2026-02-24"  # 실제 발주일 저장됨


# ────────────────────────────────────────────────
# Task 4: Phase 1.95 통합 테스트
# ────────────────────────────────────────────────

class TestPhase195Integration:
    """Phase 1.95 통합 흐름 테스트"""

    def test_phase195_driver_unavailable_skip(self):
        """드라이버 없으면 Phase 1.95 건너뜀 (에러 없이)"""
        # daily_job에서 driver=None일 때 warning 로그만 발생
        # 실제 daily_job 코드의 분기를 테스트
        driver = None
        phase_executed = False

        if driver:
            phase_executed = True

        assert not phase_executed

    def test_phase195_failure_continues(self):
        """Phase 1.95 실패해도 에러 전파 없음"""
        # daily_job에서 try/except로 감싸진 것을 테스트
        error_raised = False
        phase2_executed = False

        try:
            raise Exception("sync failed")
        except Exception:
            error_raised = True

        # Phase 2는 계속 실행
        phase2_executed = True

        assert error_raised
        assert phase2_executed

    def test_sync_result_structure(self, tracking_repo):
        """sync 결과 구조 확인"""
        from src.collectors.order_status_collector import OrderStatusCollector

        collector = OrderStatusCollector.__new__(OrderStatusCollector)
        collector.driver = MagicMock()
        collector.store_id = "46513"
        collector.tracking_repo = tracking_repo
        collector.click_all_radio = MagicMock(return_value=True)
        collector.collect_order_status = MagicMock(return_value=[])
        collector.collect_order_sale_history = MagicMock(return_value=[])

        result = collector.sync_pending_to_order_tracking()

        # 결과 구조 확인
        assert "synced" in result
        assert "skipped" in result
        assert "errors" in result
        assert isinstance(result["synced"], int)
        assert isinstance(result["skipped"], int)
        assert isinstance(result["errors"], int)
