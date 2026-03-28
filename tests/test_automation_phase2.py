"""
자동화 2단계 테스트 — 액션 제안 생성 + 카카오 포맷 + integrity 연동
"""
import json
import pytest
import sqlite3
from unittest.mock import patch, MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.prediction.action_type import ActionType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_CHECKS_WITH_ANOMALY = [
    {"check_name": "expired_batch_remaining", "status": "OK", "count": 0, "details": []},
    {"check_name": "food_ghost_stock", "status": "FAIL", "count": 3, "details": [{"item_cd": "A"}]},
    {"check_name": "expiry_time_mismatch", "status": "WARN", "count": 1, "details": []},
    {"check_name": "missing_delivery_type", "status": "OK", "count": 0, "details": []},
    {"check_name": "past_expiry_active", "status": "WARN", "count": 2, "details": []},
    {"check_name": "unavailable_with_sales", "status": "FAIL", "count": 1, "details": []},
]

SAMPLE_ALL_OK = [
    {"check_name": n, "status": "OK", "count": 0, "details": []}
    for n in ["expired_batch_remaining", "food_ghost_stock", "expiry_time_mismatch",
              "missing_delivery_type", "past_expiry_active", "unavailable_with_sales"]
]

@pytest.fixture
def store_db(tmp_path):
    db = tmp_path / "store.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""CREATE TABLE action_proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proposed_at TEXT, proposal_date TEXT NOT NULL,
        store_id TEXT NOT NULL, item_cd TEXT,
        action_type TEXT NOT NULL, reason TEXT NOT NULL,
        suggestion TEXT NOT NULL, evidence TEXT,
        status TEXT DEFAULT 'PENDING', resolved_at TEXT
    )""")
    conn.commit()
    conn.close()
    return db


# ---------------------------------------------------------------------------
# ActionType 테스트
# ---------------------------------------------------------------------------

class TestActionType:
    def test_all_7_codes(self):
        codes = [
            ActionType.RESTORE_IS_AVAILABLE,
            ActionType.CLEAR_GHOST_STOCK,
            ActionType.FIX_EXPIRY_TIME,
            ActionType.CLEAR_EXPIRED_BATCH,
            ActionType.CHECK_DELIVERY_TYPE,
            ActionType.DEACTIVATE_EXPIRED,
            ActionType.MANUAL_CHECK_REQUIRED,
        ]
        assert len(codes) == 7
        assert len(set(codes)) == 7  # 중복 없음


# ---------------------------------------------------------------------------
# ActionProposalService 테스트
# ---------------------------------------------------------------------------

class TestActionProposalService:

    def test_unavailable_generates_RESTORE(self, store_db):
        from src.application.services.action_proposal_service import ActionProposalService
        svc = ActionProposalService(store_id="99999")
        svc.proposal_repo._db_path = str(store_db)
        svc.eval_repo = MagicMock()
        svc.eval_repo.get_dangerous_skips.return_value = [
            {"item_cd": "ITEM_X", "stock": 0, "sales_7d": 5, "skip_reason": "SKIP_UNAVAILABLE"}
        ]

        proposals = svc.generate(SAMPLE_CHECKS_WITH_ANOMALY)
        restore = [p for p in proposals if p["action_type"] == ActionType.RESTORE_IS_AVAILABLE]
        assert len(restore) >= 1
        assert restore[0]["item_cd"] == "ITEM_X"

    def test_food_ghost_stock_generates_CLEAR(self, store_db):
        from src.application.services.action_proposal_service import ActionProposalService
        svc = ActionProposalService(store_id="99999")
        svc.proposal_repo._db_path = str(store_db)
        svc.eval_repo = MagicMock()
        svc.eval_repo.get_dangerous_skips.return_value = []

        proposals = svc.generate(SAMPLE_CHECKS_WITH_ANOMALY)
        ghost = [p for p in proposals if p["action_type"] == ActionType.CLEAR_GHOST_STOCK]
        assert len(ghost) == 1

    def test_expiry_time_mismatch_generates_FIX(self, store_db):
        from src.application.services.action_proposal_service import ActionProposalService
        svc = ActionProposalService(store_id="99999")
        svc.proposal_repo._db_path = str(store_db)
        svc.eval_repo = MagicMock()
        svc.eval_repo.get_dangerous_skips.return_value = []

        proposals = svc.generate(SAMPLE_CHECKS_WITH_ANOMALY)
        expiry = [p for p in proposals if p["action_type"] == ActionType.FIX_EXPIRY_TIME]
        assert len(expiry) == 1

    def test_past_expiry_generates_DEACTIVATE(self, store_db):
        from src.application.services.action_proposal_service import ActionProposalService
        svc = ActionProposalService(store_id="99999")
        svc.proposal_repo._db_path = str(store_db)
        svc.eval_repo = MagicMock()
        svc.eval_repo.get_dangerous_skips.return_value = []

        proposals = svc.generate(SAMPLE_CHECKS_WITH_ANOMALY)
        deact = [p for p in proposals if p["action_type"] == ActionType.DEACTIVATE_EXPIRED]
        assert len(deact) == 1

    def test_all_ok_no_proposals(self, store_db):
        from src.application.services.action_proposal_service import ActionProposalService
        svc = ActionProposalService(store_id="99999")
        svc.proposal_repo._db_path = str(store_db)
        svc.eval_repo = MagicMock()

        proposals = svc.generate(SAMPLE_ALL_OK)
        assert proposals == []

    def test_proposals_saved_as_PENDING(self, store_db):
        from src.application.services.action_proposal_service import ActionProposalService
        svc = ActionProposalService(store_id="99999")
        svc.proposal_repo._db_path = str(store_db)
        svc.eval_repo = MagicMock()
        svc.eval_repo.get_dangerous_skips.return_value = []

        svc.generate(SAMPLE_CHECKS_WITH_ANOMALY)

        conn = sqlite3.connect(str(store_db))
        rows = conn.execute("SELECT status FROM action_proposals").fetchall()
        conn.close()
        assert all(r[0] == "PENDING" for r in rows)

    def test_exception_returns_empty(self, store_db):
        """_analyze 자체가 실패하면 빈 리스트 반환"""
        from src.application.services.action_proposal_service import ActionProposalService
        svc = ActionProposalService(store_id="99999")
        svc.proposal_repo._db_path = str(store_db)
        svc.eval_repo = MagicMock()

        # _analyze에서 예외 발생 시뮬레이션
        with patch.object(svc, '_analyze', side_effect=Exception("분석 실패")):
            result = svc.generate(SAMPLE_CHECKS_WITH_ANOMALY)
        assert result == []


# ---------------------------------------------------------------------------
# KakaoProposalFormatter 테스트
# ---------------------------------------------------------------------------

class TestKakaoProposalFormatter:

    def test_empty_proposals(self):
        from src.application.services.kakao_proposal_formatter import KakaoProposalFormatter
        msg = KakaoProposalFormatter().format("46513", [])
        assert "46513" in msg
        assert "이상 없음" in msg

    def test_max_1000_chars(self):
        from src.application.services.kakao_proposal_formatter import KakaoProposalFormatter
        proposals = [
            {"item_cd": f"ITEM_{i}", "action_type": "TEST", "reason": "x" * 100, "suggestion": "y" * 100}
            for i in range(10)
        ]
        msg = KakaoProposalFormatter().format("46513", proposals)
        assert len(msg) <= 1000

    def test_over_5_shows_remainder(self):
        from src.application.services.kakao_proposal_formatter import KakaoProposalFormatter
        proposals = [
            {"item_cd": None, "action_type": "TEST", "reason": "r", "suggestion": "s"}
            for _ in range(8)
        ]
        msg = KakaoProposalFormatter().format("46513", proposals)
        assert "외 3건" in msg


# ---------------------------------------------------------------------------
# DataIntegrityService 연동 테스트
# ---------------------------------------------------------------------------

class TestDataIntegrityServiceProposals:

    def test_run_action_proposals_called(self):
        from src.application.services.data_integrity_service import DataIntegrityService
        svc = DataIntegrityService(store_id="46513")

        with patch.object(svc, '_run_single_check', return_value={
            "check_name": "test", "status": "OK", "count": 0, "details": []
        }):
            with patch.object(svc, '_send_alert'):
                with patch.object(svc, '_run_ai_summary'):
                    with patch.object(svc, '_run_action_proposals') as mock_prop:
                        with patch(
                            'src.application.services.data_integrity_service.IntegrityCheckRepository'
                        ) as MockRepo:
                            MockRepo.return_value.ensure_table = MagicMock()
                            MockRepo.return_value.save_check_result = MagicMock()
                            svc.run_all_checks("46513")
                            mock_prop.assert_called_once()

    def test_proposal_failure_does_not_break(self):
        from src.application.services.data_integrity_service import DataIntegrityService
        svc = DataIntegrityService(store_id="46513")
        # _run_action_proposals 내부에서 예외가 잡히는지 확인
        svc._run_action_proposals("46513", [])  # 빈 리스트 → 제안 0건 → 정상 종료
