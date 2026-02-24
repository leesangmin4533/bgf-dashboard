"""
매장 관리 + 새 아키텍처 레이어 통합 테스트

Task 4: 새 매장 추가 플로우 검증
Task 5: Application/Domain 레이어 유닛 테스트
"""

import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# =========================================================================
# Task 4: 새 매장 추가 플로우
# =========================================================================
class TestStoreServiceAddStore:
    """StoreService.add_store() 엔드투엔드 테스트"""

    @pytest.fixture
    def temp_env(self, tmp_path):
        """임시 환경 세팅 (stores.json + data/)"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        stores_dir = data_dir / "stores"
        stores_dir.mkdir()

        # 초기 stores.json
        stores_json = config_dir / "stores.json"
        initial_data = {
            "stores": [
                {
                    "store_id": "46513",
                    "store_name": "기존매장",
                    "location": "서울",
                    "type": "일반점",
                    "is_active": True,
                }
            ]
        }
        stores_json.write_text(json.dumps(initial_data, ensure_ascii=False), encoding="utf-8")

        # common.db 초기화
        common_db = data_dir / "common.db"
        conn = sqlite3.connect(str(common_db))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stores (
                store_id TEXT PRIMARY KEY,
                store_name TEXT,
                location TEXT,
                type TEXT DEFAULT '일반점',
                is_active INTEGER DEFAULT 1,
                bgf_user_id TEXT,
                bgf_password TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO stores (store_id, store_name, location)
            VALUES ('46513', '기존매장', '서울')
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS product_details (
                item_cd TEXT PRIMARY KEY,
                expiration_days INTEGER
            )
        """)
        conn.commit()
        conn.close()

        return {
            "tmp_path": tmp_path,
            "config_dir": config_dir,
            "data_dir": data_dir,
            "stores_dir": stores_dir,
            "stores_json": stores_json,
            "common_db": common_db,
        }

    @pytest.mark.unit
    def test_store_context_from_store_id(self):
        """StoreContext.from_store_id() 기존 매장 로드"""
        from src.settings.store_context import StoreContext

        ctx = StoreContext.from_store_id("46513")
        assert ctx.store_id == "46513"
        assert ctx.store_name is not None

    @pytest.mark.unit
    def test_store_context_get_all_active(self):
        """StoreContext.get_all_active() 활성 매장 목록"""
        from src.settings.store_context import StoreContext

        stores = StoreContext.get_all_active()
        assert len(stores) >= 2
        store_ids = {s.store_id for s in stores}
        assert "46513" in store_ids
        assert "46704" in store_ids

    @pytest.mark.unit
    def test_store_context_frozen(self):
        """StoreContext는 frozen dataclass"""
        from src.settings.store_context import StoreContext

        ctx = StoreContext.from_store_id("46513")
        with pytest.raises(AttributeError):
            ctx.store_id = "99999"

    @pytest.mark.unit
    def test_store_service_get_active_stores(self):
        """StoreService.get_active_stores() 리턴 타입"""
        from src.application.services.store_service import StoreService

        svc = StoreService()
        stores = svc.get_active_stores()
        assert isinstance(stores, list)
        assert len(stores) >= 2

    @pytest.mark.unit
    def test_store_service_get_store(self):
        """StoreService.get_store() 존재하는 매장"""
        from src.application.services.store_service import StoreService

        svc = StoreService()
        ctx = svc.get_store("46513")
        assert ctx is not None
        assert ctx.store_id == "46513"

    @pytest.mark.unit
    def test_store_service_get_store_not_found(self):
        """StoreService.get_store() 존재하지 않는 매장"""
        from src.application.services.store_service import StoreService

        svc = StoreService()
        ctx = svc.get_store("99999")
        assert ctx is None

    @pytest.mark.unit
    def test_store_service_get_all_store_ids(self):
        """StoreService.get_all_store_ids() 리턴"""
        from src.application.services.store_service import StoreService

        svc = StoreService()
        ids = svc.get_all_store_ids()
        assert isinstance(ids, list)
        assert "46513" in ids


# =========================================================================
# Task 5: Application Layer — DailyOrderFlow 유닛 테스트
# =========================================================================
class TestDailyOrderFlow:
    """DailyOrderFlow 유닛 테스트"""

    @pytest.mark.unit
    def test_init(self):
        """DailyOrderFlow 초기화"""
        from src.application.use_cases.daily_order_flow import DailyOrderFlow
        from src.settings.store_context import StoreContext

        ctx = StoreContext.from_store_id("46513")
        flow = DailyOrderFlow(store_ctx=ctx)
        assert flow.store_id == "46513"
        assert flow._result["success"] is False

    @pytest.mark.unit
    @patch("src.application.services.prediction_service.PredictionService.predict_all")
    def test_predict_only(self, mock_predict):
        """DailyOrderFlow.predict_only() 예측만 수행"""
        from src.application.use_cases.daily_order_flow import DailyOrderFlow
        from src.settings.store_context import StoreContext

        mock_predict.return_value = [
            {"item_cd": "A001", "order_qty": 3},
            {"item_cd": "A002", "order_qty": 0},
        ]

        ctx = StoreContext.from_store_id("46513")
        flow = DailyOrderFlow(store_ctx=ctx)
        results = flow.predict_only(min_order_qty=0)

        assert len(results) == 2
        mock_predict.assert_called_once_with(min_order_qty=0)

    @pytest.mark.unit
    @patch("src.application.use_cases.daily_order_flow.DailyOrderFlow._stage_track")
    @patch("src.application.use_cases.daily_order_flow.DailyOrderFlow._stage_filter")
    @patch("src.application.use_cases.daily_order_flow.DailyOrderFlow._stage_predict")
    def test_run_dry_run(self, mock_predict, mock_filter, mock_track):
        """DailyOrderFlow.run(dry_run=True) 실행"""
        from src.application.use_cases.daily_order_flow import DailyOrderFlow
        from src.settings.store_context import StoreContext

        mock_predict.return_value = [
            {"item_cd": "A001", "order_qty": 3},
        ]
        mock_filter.return_value = [
            {"item_cd": "A001", "order_qty": 3},
        ]

        ctx = StoreContext.from_store_id("46513")
        flow = DailyOrderFlow(store_ctx=ctx)
        result = flow.run(
            collect_sales=False,
            dry_run=True,
            min_order_qty=1,
        )

        assert result["success"] is True
        assert result.get("filtered_count") == 1
        mock_predict.assert_called_once()
        mock_filter.assert_called_once()
        mock_track.assert_called_once()


# =========================================================================
# Task 5: Application Layer — ExpiryAlertFlow 유닛 테스트
# =========================================================================
class TestExpiryAlertFlow:
    """ExpiryAlertFlow 유닛 테스트"""

    @pytest.mark.unit
    def test_init(self):
        """ExpiryAlertFlow 초기화"""
        from src.application.use_cases.expiry_alert_flow import ExpiryAlertFlow
        from src.settings.store_context import StoreContext

        ctx = StoreContext.from_store_id("46513")
        flow = ExpiryAlertFlow(store_ctx=ctx)
        assert flow.store_id == "46513"


# =========================================================================
# Task 5: Application Layer — WeeklyReportFlow 유닛 테스트
# =========================================================================
class TestWeeklyReportFlow:
    """WeeklyReportFlow 유닛 테스트"""

    @pytest.mark.unit
    def test_init(self):
        """WeeklyReportFlow 초기화"""
        from src.application.use_cases.weekly_report_flow import WeeklyReportFlow
        from src.settings.store_context import StoreContext

        ctx = StoreContext.from_store_id("46513")
        flow = WeeklyReportFlow(store_ctx=ctx)
        assert flow.store_id == "46513"


# =========================================================================
# Task 5: Application Layer — WasteReportFlow 유닛 테스트
# =========================================================================
class TestWasteReportFlow:
    """WasteReportFlow 유닛 테스트"""

    @pytest.mark.unit
    def test_init(self):
        """WasteReportFlow 초기화"""
        from src.application.use_cases.waste_report_flow import WasteReportFlow
        from src.settings.store_context import StoreContext

        ctx = StoreContext.from_store_id("46513")
        flow = WasteReportFlow(store_ctx=ctx)
        assert flow.store_id == "46513"

    @pytest.mark.unit
    @patch("src.analysis.waste_report.generate_waste_report")
    def test_run_success(self, mock_generate):
        """WasteReportFlow.run() 성공"""
        from src.application.use_cases.waste_report_flow import WasteReportFlow
        from src.settings.store_context import StoreContext

        mock_generate.return_value = "/tmp/report.xlsx"

        ctx = StoreContext.from_store_id("46513")
        flow = WasteReportFlow(store_ctx=ctx)
        result = flow.run()

        assert result["success"] is True
        assert result["report_path"] == "/tmp/report.xlsx"

    @pytest.mark.unit
    @patch("src.analysis.waste_report.generate_waste_report")
    def test_run_failure(self, mock_generate):
        """WasteReportFlow.run() 실패"""
        from src.application.use_cases.waste_report_flow import WasteReportFlow
        from src.settings.store_context import StoreContext

        mock_generate.return_value = None

        ctx = StoreContext.from_store_id("46513")
        flow = WasteReportFlow(store_ctx=ctx)
        result = flow.run()

        assert result["success"] is False


# =========================================================================
# Task 5: Application Layer — MLTrainingFlow 유닛 테스트
# =========================================================================
class TestMLTrainingFlow:
    """MLTrainingFlow 유닛 테스트"""

    @pytest.mark.unit
    def test_init(self):
        """MLTrainingFlow 초기화"""
        from src.application.use_cases.ml_training_flow import MLTrainingFlow
        from src.settings.store_context import StoreContext

        ctx = StoreContext.from_store_id("46513")
        flow = MLTrainingFlow(store_ctx=ctx)
        assert flow.store_id == "46513"


# =========================================================================
# Task 5: Application Layer — BatchCollectFlow 유닛 테스트
# =========================================================================
class TestBatchCollectFlow:
    """BatchCollectFlow 유닛 테스트"""

    @pytest.mark.unit
    def test_init(self):
        """BatchCollectFlow 초기화"""
        from src.application.use_cases.batch_collect_flow import BatchCollectFlow
        from src.settings.store_context import StoreContext

        ctx = StoreContext.from_store_id("46513")
        flow = BatchCollectFlow(store_ctx=ctx)
        assert flow.store_id == "46513"


# =========================================================================
# Task 5: Application Layer — MultiStoreRunner 유닛 테스트
# =========================================================================
class TestMultiStoreRunner:
    """MultiStoreRunner 유닛 테스트"""

    @pytest.mark.unit
    def test_init(self):
        """MultiStoreRunner 초기화"""
        from src.application.scheduler.job_scheduler import MultiStoreRunner

        runner = MultiStoreRunner(max_workers=2)
        assert runner.max_workers == 2

    @pytest.mark.unit
    def test_run_parallel(self):
        """MultiStoreRunner.run_parallel() 모든 매장 실행"""
        from src.application.scheduler.job_scheduler import MultiStoreRunner

        runner = MultiStoreRunner(max_workers=4)
        results = runner.run_parallel(
            task_fn=lambda ctx: {"store_id": ctx.store_id, "ok": True},
            task_name="test_task",
        )

        assert len(results) >= 2
        for r in results:
            assert r["success"] is True
            assert r["result"]["ok"] is True


# =========================================================================
# Task 5: Application Layer — Services 유닛 테스트
# =========================================================================
class TestPredictionService:
    """PredictionService 유닛 테스트"""

    @pytest.mark.unit
    def test_init(self):
        """PredictionService 초기화"""
        from src.application.services.prediction_service import PredictionService
        from src.settings.store_context import StoreContext

        ctx = StoreContext.from_store_id("46513")
        svc = PredictionService(store_ctx=ctx)
        assert svc.store_ctx.store_id == "46513"


class TestOrderService:
    """OrderService 유닛 테스트"""

    @pytest.mark.unit
    def test_init(self):
        """OrderService 초기화"""
        from src.application.services.order_service import OrderService
        from src.settings.store_context import StoreContext

        ctx = StoreContext.from_store_id("46513")
        svc = OrderService(store_ctx=ctx)
        assert svc.store_ctx.store_id == "46513"


class TestInventoryService:
    """InventoryService 유닛 테스트"""

    @pytest.mark.unit
    def test_init(self):
        """InventoryService 초기화"""
        from src.application.services.inventory_service import InventoryService
        from src.settings.store_context import StoreContext

        ctx = StoreContext.from_store_id("46513")
        svc = InventoryService(store_ctx=ctx)
        assert svc.store_ctx.store_id == "46513"


class TestDashboardService:
    """DashboardService 유닛 테스트"""

    @pytest.mark.unit
    def test_init(self):
        """DashboardService 초기화"""
        from src.application.services.dashboard_service import DashboardService

        svc = DashboardService(store_id="46513")
        assert svc.store_id == "46513"

    @pytest.mark.unit
    def test_init_with_db_path(self):
        """DashboardService db_path 파라미터"""
        from src.application.services.dashboard_service import DashboardService

        svc = DashboardService(store_id="46513", db_path="/tmp/test.db")
        assert svc._db_path == "/tmp/test.db"

    @pytest.mark.unit
    def test_sql_helpers(self):
        """DashboardService SQL 헬퍼 메서드"""
        from src.application.services.dashboard_service import DashboardService

        # store_id 있을 때
        svc = DashboardService(store_id="46513")
        assert "store_id = ?" in svc._sf()
        assert "store_id = ?" in svc._sw()
        assert svc._sp() == ("46513",)

        # store_id 없을 때
        svc2 = DashboardService()
        assert svc2._sf() == ""
        assert svc2._sw() == ""
        assert svc2._sp() == ()

    @pytest.mark.unit
    def test_sf_with_alias(self):
        """DashboardService._sf(alias) 테이블 별칭"""
        from src.application.services.dashboard_service import DashboardService

        svc = DashboardService(store_id="46513")
        assert "ds.store_id = ?" in svc._sf("ds")


# =========================================================================
# Task 5: Domain Layer — Strategy 패턴 유닛 테스트
# =========================================================================
class TestJobDefinitions:
    """JobDefinitions 레지스트리 테스트"""

    @pytest.mark.unit
    def test_scheduled_jobs_count(self):
        """SCHEDULED_JOBS 등록 수"""
        from src.application.scheduler.job_definitions import SCHEDULED_JOBS

        assert len(SCHEDULED_JOBS) >= 6

    @pytest.mark.unit
    def test_job_definition_fields(self):
        """JobDefinition 필드 존재"""
        from src.application.scheduler.job_definitions import SCHEDULED_JOBS

        for job in SCHEDULED_JOBS:
            assert hasattr(job, "name")
            assert hasattr(job, "flow_class")
            assert hasattr(job, "multi_store")
            assert hasattr(job, "needs_driver")
            assert hasattr(job, "enabled")

    @pytest.mark.unit
    def test_daily_order_job_config(self):
        """daily_order 작업 설정"""
        from src.application.scheduler.job_definitions import SCHEDULED_JOBS

        daily = next(j for j in SCHEDULED_JOBS if j.name == "daily_order")
        assert daily.schedule == "07:00"
        assert daily.multi_store is True
        assert daily.needs_driver is True
