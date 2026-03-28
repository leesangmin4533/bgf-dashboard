"""
하네스 엔지니어링 Week 2 테스트 — SKIP 사유 저장

eval_calibrator → eval_outcome_repo 경로에서
reason, skip_reason, skip_detail이 정상 저장되는지 검증.
"""
import json
import pytest
import sqlite3
from datetime import datetime
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.prediction.pre_order_evaluator import EvalDecision, PreOrderEvalResult
from src.prediction.skip_reason import SkipReason


# ---------------------------------------------------------------------------
# PreOrderEvalResult 필드 테스트
# ---------------------------------------------------------------------------

class TestPreOrderEvalResultFields:
    """PreOrderEvalResult에 skip 필드가 정상 존재하는지"""

    def test_skip_reason_default_none(self):
        r = PreOrderEvalResult(
            item_cd="TEST", item_nm="테스트", mid_cd="001",
            decision=EvalDecision.FORCE_ORDER, reason="강제",
            exposure_days=0, stockout_frequency=0,
            popularity_score=0, current_stock=0,
            pending_qty=0, daily_avg=0,
        )
        assert r.skip_reason is None
        assert r.skip_detail is None

    def test_skip_reason_with_value(self):
        r = PreOrderEvalResult(
            item_cd="TEST", item_nm="테스트", mid_cd="001",
            decision=EvalDecision.SKIP, reason="재고충분+저인기",
            exposure_days=5.0, stockout_frequency=0,
            popularity_score=0.1, current_stock=10,
            pending_qty=0, daily_avg=1.0,
            skip_reason=SkipReason.SKIP_LOW_POPULARITY,
            skip_detail=json.dumps({"exposure_days": 5.0}),
        )
        assert r.skip_reason == "SKIP_LOW_POPULARITY"
        assert "exposure_days" in r.skip_detail

    def test_force_order_no_skip_reason(self):
        """FORCE/URGENT/NORMAL → skip_reason=None"""
        for dec in [EvalDecision.FORCE_ORDER, EvalDecision.URGENT_ORDER, EvalDecision.NORMAL_ORDER]:
            r = PreOrderEvalResult(
                item_cd="T", item_nm="", mid_cd="",
                decision=dec, reason="test",
                exposure_days=0, stockout_frequency=0,
                popularity_score=0, current_stock=0,
                pending_qty=0, daily_avg=0,
            )
            assert r.skip_reason is None

    def test_decision_is_enum(self):
        """decision은 EvalDecision enum이지 str이 아님"""
        r = PreOrderEvalResult(
            item_cd="T", item_nm="", mid_cd="",
            decision=EvalDecision.SKIP, reason="test",
            exposure_days=0, stockout_frequency=0,
            popularity_score=0, current_stock=0,
            pending_qty=0, daily_avg=0,
        )
        assert isinstance(r.decision, EvalDecision)
        assert r.decision.value == "SKIP"


# ---------------------------------------------------------------------------
# SkipReason 코드 테스트
# ---------------------------------------------------------------------------

class TestSkipReasonCodes:

    def test_skip_low_popularity_code(self):
        assert SkipReason.SKIP_LOW_POPULARITY == "SKIP_LOW_POPULARITY"

    def test_skip_unavailable_code(self):
        assert SkipReason.SKIP_UNAVAILABLE == "SKIP_UNAVAILABLE"

    def test_pass_stock_sufficient_code(self):
        assert SkipReason.PASS_STOCK_SUFFICIENT == "PASS_STOCK_SUFFICIENT"

    def test_pass_data_insufficient_code(self):
        assert SkipReason.PASS_DATA_INSUFFICIENT == "PASS_DATA_INSUFFICIENT"


# ---------------------------------------------------------------------------
# eval_calibrator batch 조립 테스트
# ---------------------------------------------------------------------------

class TestEvalCalibratorBatch:
    """eval_calibrator.save_eval_results()의 batch에 3필드가 포함되는지"""

    def test_batch_includes_skip_fields(self):
        """batch dict에 reason/skip_reason/skip_detail 키 존재"""
        r = PreOrderEvalResult(
            item_cd="8801234", item_nm="테스트상품", mid_cd="042",
            decision=EvalDecision.SKIP, reason="재고충분+저인기",
            exposure_days=5.0, stockout_frequency=0.1,
            popularity_score=0.05, current_stock=10,
            pending_qty=0, daily_avg=1.5,
            skip_reason=SkipReason.SKIP_LOW_POPULARITY,
            skip_detail=json.dumps({"exposure_days": 5.0}),
        )
        # eval_calibrator의 batch 조립 시뮬레이션
        batch_entry = {
            "item_cd": r.item_cd,
            "decision": r.decision.value,
            "reason": r.reason,
            "skip_reason": getattr(r, "skip_reason", None),
            "skip_detail": getattr(r, "skip_detail", None),
        }
        assert batch_entry["reason"] == "재고충분+저인기"
        assert batch_entry["skip_reason"] == "SKIP_LOW_POPULARITY"
        assert "exposure_days" in batch_entry["skip_detail"]

    def test_batch_normal_order_skip_none(self):
        """NORMAL_ORDER → skip 필드 None"""
        r = PreOrderEvalResult(
            item_cd="8801234", item_nm="테스트", mid_cd="001",
            decision=EvalDecision.NORMAL_ORDER, reason="일반",
            exposure_days=1.0, stockout_frequency=0.2,
            popularity_score=0.5, current_stock=2,
            pending_qty=0, daily_avg=3.0,
        )
        assert getattr(r, "skip_reason", None) is None
        assert getattr(r, "skip_detail", None) is None


# ---------------------------------------------------------------------------
# eval_outcome_repo INSERT 테스트
# ---------------------------------------------------------------------------

class TestEvalOutcomeRepoSkipColumns:
    """eval_outcome_repo의 INSERT에 skip 컬럼이 포함되는지"""

    @pytest.fixture
    def db_with_skip_columns(self, tmp_path):
        """skip 컬럼이 있는 테스트 DB"""
        db = tmp_path / "test.db"
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
                weekday INTEGER,
                delivery_batch TEXT,
                sell_price INTEGER,
                margin_rate REAL,
                promo_type TEXT,
                trend_score REAL,
                stockout_freq REAL,
                reason TEXT,
                skip_reason TEXT,
                skip_detail TEXT,
                created_at TEXT,
                UNIQUE(store_id, eval_date, item_cd)
            )
        """)
        conn.commit()
        conn.close()
        return db

    def test_batch_save_with_skip(self, db_with_skip_columns):
        """skip_reason이 DB에 저장되는지"""
        from src.infrastructure.database.repos.eval_outcome_repo import EvalOutcomeRepository
        repo = EvalOutcomeRepository(store_id="99999")
        repo._db_path = str(db_with_skip_columns)

        batch = [{
            "item_cd": "ITEM_A",
            "mid_cd": "042",
            "decision": "SKIP",
            "exposure_days": 5.0,
            "popularity_score": 0.05,
            "daily_avg": 1.5,
            "current_stock": 10,
            "pending_qty": 0,
            "weekday": 5,
            "delivery_batch": None,
            "sell_price": 3000,
            "margin_rate": 30.0,
            "promo_type": None,
            "trend_score": 1.0,
            "stockout_freq": 0.1,
            "reason": "재고충분+저인기",
            "skip_reason": "SKIP_LOW_POPULARITY",
            "skip_detail": json.dumps({"exposure_days": 5.0}),
        }]

        count = repo.save_eval_results_batch("2026-03-28", batch, store_id="99999")
        assert count == 1

        conn = sqlite3.connect(str(db_with_skip_columns))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM eval_outcomes WHERE item_cd='ITEM_A'").fetchone()
        conn.close()

        assert row["reason"] == "재고충분+저인기"
        assert row["skip_reason"] == "SKIP_LOW_POPULARITY"
        assert "exposure_days" in row["skip_detail"]

    def test_batch_save_without_skip(self, db_with_skip_columns):
        """FORCE_ORDER → skip 컬럼 NULL"""
        from src.infrastructure.database.repos.eval_outcome_repo import EvalOutcomeRepository
        repo = EvalOutcomeRepository(store_id="99999")
        repo._db_path = str(db_with_skip_columns)

        batch = [{
            "item_cd": "ITEM_B",
            "mid_cd": "001",
            "decision": "FORCE_ORDER",
            "exposure_days": 0,
            "popularity_score": 0.8,
            "daily_avg": 5.0,
            "current_stock": 0,
            "pending_qty": 0,
            "weekday": 5,
            "delivery_batch": "1차",
            "sell_price": 5000,
            "margin_rate": 25.0,
            "promo_type": None,
            "trend_score": 1.2,
            "stockout_freq": 0.5,
            "reason": "품절 강제발주",
            "skip_reason": None,
            "skip_detail": None,
        }]

        repo.save_eval_results_batch("2026-03-28", batch, store_id="99999")

        conn = sqlite3.connect(str(db_with_skip_columns))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM eval_outcomes WHERE item_cd='ITEM_B'").fetchone()
        conn.close()

        assert row["skip_reason"] is None
        assert row["skip_detail"] is None
        assert row["reason"] == "품절 강제발주"
