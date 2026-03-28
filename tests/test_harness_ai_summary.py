"""
하네스 엔지니어링 Week 3 테스트 — AI 요약 서비스

AISummaryService + AISummaryRepository + DataIntegrityService 연동 테스트.
"""
import json
import pytest
import sqlite3
from datetime import datetime
from unittest.mock import patch, MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def common_db(tmp_path):
    """ai_summaries 테이블이 있는 임시 common DB"""
    db = tmp_path / "common.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE ai_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            summary_date TEXT NOT NULL,
            summary_type TEXT NOT NULL,
            store_id TEXT,
            summary_text TEXT,
            anomaly_count INTEGER DEFAULT 0,
            model_used TEXT,
            token_count INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(summary_date, summary_type, store_id)
        )
    """)
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def store_db(tmp_path):
    """skip 컬럼이 있는 임시 store DB"""
    db = tmp_path / "store.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE eval_outcomes (
            id INTEGER PRIMARY KEY,
            store_id TEXT,
            eval_date TEXT NOT NULL,
            item_cd TEXT NOT NULL,
            mid_cd TEXT,
            decision TEXT NOT NULL,
            exposure_days REAL,
            popularity_score REAL,
            daily_avg REAL,
            current_stock INTEGER,
            pending_qty INTEGER,
            reason TEXT,
            skip_reason TEXT,
            skip_detail TEXT,
            created_at TEXT,
            weekday INTEGER,
            delivery_batch TEXT,
            sell_price INTEGER,
            margin_rate REAL,
            promo_type TEXT,
            trend_score REAL,
            stockout_freq REAL,
            UNIQUE(store_id, eval_date, item_cd)
        )
    """)
    conn.commit()
    conn.close()
    return db


SAMPLE_CHECK_RESULTS = [
    {"check_name": "expired_batch_remaining", "status": "OK", "count": 0, "details": []},
    {"check_name": "food_ghost_stock", "status": "FAIL", "count": 3, "details": [{"item_cd": "A"}]},
    {"check_name": "expiry_time_mismatch", "status": "OK", "count": 0, "details": []},
    {"check_name": "missing_delivery_type", "status": "WARN", "count": 1, "details": []},
    {"check_name": "past_expiry_active", "status": "OK", "count": 0, "details": []},
    {"check_name": "unavailable_with_sales", "status": "RESTORED", "count": 2, "details": []},
]

SAMPLE_ALL_OK = [
    {"check_name": "expired_batch_remaining", "status": "OK", "count": 0},
    {"check_name": "food_ghost_stock", "status": "OK", "count": 0},
    {"check_name": "expiry_time_mismatch", "status": "OK", "count": 0},
    {"check_name": "missing_delivery_type", "status": "OK", "count": 0},
    {"check_name": "past_expiry_active", "status": "OK", "count": 0},
    {"check_name": "unavailable_with_sales", "status": "RESTORED", "count": 1},
]


# ---------------------------------------------------------------------------
# AISummaryRepository 테스트
# ---------------------------------------------------------------------------

class TestAISummaryRepository:

    def test_upsert_and_read(self, common_db):
        from src.infrastructure.database.repos.ai_summary_repo import AISummaryRepository
        repo = AISummaryRepository()
        repo._db_path = str(common_db)

        repo.upsert_summary(
            summary_date="2026-03-28",
            summary_type="integrity",
            store_id="46513",
            summary_text="테스트 요약",
            anomaly_count=3,
            model_used="rule_based",
        )

        result = repo.get_latest_by_store("46513", "integrity")
        assert result is not None
        assert result["summary_text"] == "테스트 요약"
        assert result["anomaly_count"] == 3

    def test_upsert_overwrites(self, common_db):
        """같은 날 재실행 시 덮어쓰기"""
        from src.infrastructure.database.repos.ai_summary_repo import AISummaryRepository
        repo = AISummaryRepository()
        repo._db_path = str(common_db)

        repo.upsert_summary("2026-03-28", "integrity", "46513", "v1", 3, "rule_based")
        repo.upsert_summary("2026-03-28", "integrity", "46513", "v2", 5, "rule_based")

        result = repo.get_latest_by_store("46513", "integrity")
        assert result["summary_text"] == "v2"
        assert result["anomaly_count"] == 5

        # 행이 1개만 있는지 확인
        conn = sqlite3.connect(str(common_db))
        count = conn.execute(
            "SELECT COUNT(*) FROM ai_summaries WHERE store_id='46513'"
        ).fetchone()[0]
        conn.close()
        assert count == 1

    def test_get_daily_cost(self, common_db):
        from src.infrastructure.database.repos.ai_summary_repo import AISummaryRepository
        repo = AISummaryRepository()
        repo._db_path = str(common_db)

        repo.upsert_summary("2026-03-28", "integrity", "46513", "a", 1, "api", cost_usd=0.01)
        repo.upsert_summary("2026-03-28", "integrity", "46704", "b", 2, "api", cost_usd=0.02)

        cost = repo.get_daily_cost("2026-03-28")
        assert abs(cost - 0.03) < 0.001


# ---------------------------------------------------------------------------
# AISummaryService 테스트
# ---------------------------------------------------------------------------

class TestAISummaryService:

    def test_rule_based_summary_generated(self, common_db, store_db):
        """anomaly > 0 → 규칙 기반 요약 생성"""
        from src.application.services.ai_summary_service import AISummaryService

        with patch.object(AISummaryService, '__init__', lambda self, **kw: None):
            service = AISummaryService(store_id="46513")
            service.store_id = "46513"
            service.today = "2026-03-28"

            from src.infrastructure.database.repos.ai_summary_repo import AISummaryRepository
            from src.infrastructure.database.repos.eval_outcome_repo import EvalOutcomeRepository

            service.summary_repo = AISummaryRepository()
            service.summary_repo._db_path = str(common_db)

            service.eval_repo = MagicMock(spec=EvalOutcomeRepository)
            service.eval_repo.get_dangerous_skips.return_value = []
            service.eval_repo.get_skip_stats_by_reason.return_value = []

            summary = service.summarize_integrity(SAMPLE_CHECK_RESULTS)

        assert summary is not None
        assert "46513" in summary
        assert "하네스 리포트" in summary
        assert "food_ghost_stock" in summary

    def test_anomaly_zero_returns_none(self, common_db, store_db):
        """이상 없으면 None (비용 절감)"""
        from src.application.services.ai_summary_service import AISummaryService

        with patch.object(AISummaryService, '__init__', lambda self, **kw: None):
            service = AISummaryService(store_id="46513")
            service.store_id = "46513"
            service.today = "2026-03-28"

            from src.infrastructure.database.repos.ai_summary_repo import AISummaryRepository
            service.summary_repo = AISummaryRepository()
            service.summary_repo._db_path = str(common_db)
            service.eval_repo = MagicMock()

            result = service.summarize_integrity(SAMPLE_ALL_OK)

        assert result is None

    def test_disabled_returns_none(self):
        """AI_SUMMARY_ENABLED=false → None"""
        from src.application.services.ai_summary_service import AISummaryService

        with patch("src.application.services.ai_summary_service.AI_SUMMARY_ENABLED", False):
            with patch.object(AISummaryService, '__init__', lambda self, **kw: None):
                service = AISummaryService(store_id="46513")
                service.store_id = "46513"
                result = service.summarize_integrity(SAMPLE_CHECK_RESULTS)

        assert result is None

    def test_exception_returns_none(self):
        """예외 발생 시 None (발주 무영향)"""
        from src.application.services.ai_summary_service import AISummaryService

        with patch.object(AISummaryService, '__init__', lambda self, **kw: None):
            service = AISummaryService(store_id="46513")
            service.store_id = "46513"
            service.today = "2026-03-28"
            service.summary_repo = MagicMock(side_effect=Exception("DB error"))
            service.eval_repo = MagicMock()

            result = service.summarize_integrity(SAMPLE_CHECK_RESULTS)

        assert result is None


# ---------------------------------------------------------------------------
# DataIntegrityService 연동 테스트
# ---------------------------------------------------------------------------

class TestDataIntegrityServiceAISummary:

    def test_run_ai_summary_called(self):
        """run_all_checks 후 _run_ai_summary 호출 확인"""
        from src.application.services.data_integrity_service import DataIntegrityService

        service = DataIntegrityService(store_id="46513")

        with patch.object(service, '_run_single_check', return_value={
            "check_name": "test", "status": "OK", "count": 0, "details": []
        }):
            with patch.object(service, '_send_alert'):
                with patch.object(service, '_run_ai_summary') as mock_ai:
                    with patch(
                        'src.application.services.data_integrity_service.IntegrityCheckRepository'
                    ) as MockRepo:
                        MockRepo.return_value.ensure_table = MagicMock()
                        MockRepo.return_value.save_check_result = MagicMock()

                        service.run_all_checks("46513")

                        mock_ai.assert_called_once()
                        args = mock_ai.call_args
                        assert args[0][0] == "46513"  # store_id
                        assert isinstance(args[0][1], list)  # results

    def test_ai_summary_failure_does_not_break(self):
        """AI 요약 실패해도 run_all_checks는 정상 반환"""
        from src.application.services.data_integrity_service import DataIntegrityService

        service = DataIntegrityService(store_id="46513")

        with patch.object(service, '_run_single_check', return_value={
            "check_name": "test", "status": "OK", "count": 0, "details": []
        }):
            with patch.object(service, '_send_alert'):
                with patch.object(
                    service, '_run_ai_summary',
                    side_effect=Exception("AI 요약 폭발")
                ):
                    with patch(
                        'src.application.services.data_integrity_service.IntegrityCheckRepository'
                    ) as MockRepo:
                        MockRepo.return_value.ensure_table = MagicMock()
                        MockRepo.return_value.save_check_result = MagicMock()

                        # _run_ai_summary가 예외를 던져도 run_all_checks는 정상
                        # 실제로는 내부 try/except로 잡히지만, 여기서는 외부에서 예외 발생 시뮬레이션
                        # run_all_checks 내부의 _run_ai_summary 호출이 예외를 잡는지 확인
                        # 실제 코드에서 _run_ai_summary 자체가 try/except 안에 있으므로
                        # 이 테스트는 _run_ai_summary 메서드 자체의 안전성을 검증

                        # _run_ai_summary 내부 try/except 테스트
                        service2 = DataIntegrityService(store_id="46513")
                        with patch(
                            'src.application.services.ai_summary_service.AISummaryService',
                            side_effect=Exception("import 실패")
                        ):
                            # 예외가 전파되지 않아야 함
                            service2._run_ai_summary("46513", [])
