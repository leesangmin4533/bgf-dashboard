"""
폐기 원인 분석 + 자동 피드백 테스트

- WasteCauseAnalyzer: 분류 로직
- WasteFeedbackAdjuster: 피드백 조회 + 시간 감쇄
- WasteCauseRepository: DB CRUD
- 웹 API: /api/waste/*
"""

import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.analysis.waste_cause_analyzer import (
    WasteCauseAnalyzer,
    WasteFeedbackAdjuster,
    ClassificationResult,
    WasteFeedbackResult,
)
from src.infrastructure.database.repos.waste_cause_repo import WasteCauseRepository
from src.settings.constants import (
    WASTE_CAUSE_OVER_ORDER,
    WASTE_CAUSE_EXPIRY_MGMT,
    WASTE_CAUSE_DEMAND_DROP,
    WASTE_CAUSE_MIXED,
    WASTE_FEEDBACK_REDUCE_SAFETY,
    WASTE_FEEDBACK_SUPPRESS,
    WASTE_FEEDBACK_TEMP_REDUCE,
    WASTE_FEEDBACK_DEFAULT,
)


# =========================================================================
# 픽스처
# =========================================================================

@pytest.fixture
def waste_db(tmp_path):
    """폐기 분석 테스트용 DB"""
    db_file = tmp_path / "test_waste.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE waste_cause_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            analysis_date TEXT NOT NULL,
            waste_date TEXT NOT NULL,
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            mid_cd TEXT,
            waste_qty INTEGER NOT NULL DEFAULT 0,
            waste_source TEXT NOT NULL,
            primary_cause TEXT NOT NULL,
            secondary_cause TEXT,
            confidence REAL DEFAULT 0.0,
            order_qty INTEGER,
            daily_avg REAL,
            predicted_qty INTEGER,
            actual_sold_qty INTEGER,
            expiration_days INTEGER,
            trend_ratio REAL,
            sell_day_ratio REAL,
            weather_factor TEXT,
            promo_factor TEXT,
            holiday_factor TEXT,
            feedback_action TEXT NOT NULL DEFAULT 'DEFAULT',
            feedback_multiplier REAL DEFAULT 1.0,
            feedback_expiry_date TEXT,
            is_applied INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(store_id, waste_date, item_cd)
        )
    """)
    conn.commit()
    conn.close()
    return db_file


@pytest.fixture
def waste_repo(waste_db):
    """WasteCauseRepository 인스턴스"""
    return WasteCauseRepository(db_path=waste_db, store_id="46513")


@pytest.fixture
def analyzer_params():
    """테스트용 분석 파라미터"""
    return {
        "enabled": True,
        "lookback_days": 14,
        "over_order_ratio_threshold": 1.5,
        "waste_ratio_high": 0.5,
        "demand_drop_trend_threshold": 0.6,
        "demand_drop_sold_threshold": 0.5,
        "sell_day_ratio_low": 0.5,
        "temp_change_threshold": 10.0,
        "promo_ended_days": 3,
        "feedback_multipliers": {
            "OVER_ORDER": 0.75,
            "EXPIRY_MISMANAGEMENT": 0.85,
            "DEMAND_DROP_START": 0.80,
            "DEMAND_DROP_DECAY_DAYS": 7,
        },
        "feedback_expiry_days": {
            "OVER_ORDER": 14,
            "EXPIRY_MISMANAGEMENT": 21,
            "DEMAND_DROP": 7,
        },
    }


# =========================================================================
# 1. 분류 로직 테스트
# =========================================================================

class TestClassification:
    """WasteCauseAnalyzer._classify() 테스트"""

    def _make_analyzer(self, params):
        analyzer = WasteCauseAnalyzer.__new__(WasteCauseAnalyzer)
        analyzer.store_id = "46513"
        analyzer.params = params
        analyzer.repo = MagicMock()
        return analyzer

    def test_classify_over_order_clear(self, analyzer_params):
        """과잉 발주: 발주량이 소비능력의 2배 이상"""
        a = self._make_analyzer(analyzer_params)
        event = {"item_cd": "A001", "waste_qty": 5, "initial_qty": 10}
        context = {
            "daily_avg": 2.0,
            "order_qty": 8,
            "expiration_days": 2,
            "predicted_qty": 4,
            "actual_sold_qty": 3,
            "trend_ratio": 1.0,
            "sell_day_ratio": 0.8,
            "weather_info": {"delta": 2.0},
            "promo_info": {"promo_ended_recently": False},
        }
        result = a._classify(event, context)
        assert result.cause == WASTE_CAUSE_OVER_ORDER
        assert result.confidence >= 0.5

    def test_classify_over_order_borderline(self, analyzer_params):
        """과잉 발주 경계값: ratio=1.6 (임계값 1.5 직상)"""
        a = self._make_analyzer(analyzer_params)
        event = {"item_cd": "A002", "waste_qty": 2, "initial_qty": 5}
        context = {
            "daily_avg": 2.0,
            "order_qty": 5,  # 5 / (2.0*1.5...) ≈ 1.67
            "expiration_days": 1,  # 2.0 * 1 = 2.0, ratio = 5/2 = 2.5
            "predicted_qty": 3,
            "actual_sold_qty": 2,
            "trend_ratio": 0.9,
            "sell_day_ratio": 0.7,
            "weather_info": {},
            "promo_info": {"promo_ended_recently": False},
        }
        result = a._classify(event, context)
        assert result.cause == WASTE_CAUSE_OVER_ORDER

    def test_classify_demand_drop_trend(self, analyzer_params):
        """수요 급감: 트렌드 급하락 + 실매출 미달"""
        a = self._make_analyzer(analyzer_params)
        event = {"item_cd": "B001", "waste_qty": 3, "initial_qty": 5}
        context = {
            "daily_avg": 5.0,
            "order_qty": 5,
            "expiration_days": 1,
            "predicted_qty": 5,
            "actual_sold_qty": 2,  # < 5 * 0.5
            "trend_ratio": 0.4,  # < 0.6
            "sell_day_ratio": 0.8,
            "weather_info": {"delta": 3.0},
            "promo_info": {"promo_ended_recently": False},
        }
        result = a._classify(event, context)
        assert result.cause == WASTE_CAUSE_DEMAND_DROP

    def test_classify_demand_drop_weather(self, analyzer_params):
        """수요 급감: 기온 급변 (12도 하락) + 실매출 미달"""
        a = self._make_analyzer(analyzer_params)
        event = {"item_cd": "B002", "waste_qty": 4, "initial_qty": 6}
        context = {
            "daily_avg": 3.0,
            "order_qty": 3,
            "expiration_days": 1,
            "predicted_qty": 3,
            "actual_sold_qty": 1,  # < 3 * 0.5
            "trend_ratio": 0.8,
            "sell_day_ratio": 0.7,
            "weather_info": {"delta": -12.0},  # 12도 하락
            "promo_info": {"promo_ended_recently": False},
        }
        result = a._classify(event, context)
        assert result.cause == WASTE_CAUSE_DEMAND_DROP

    def test_classify_demand_drop_promo_ended(self, analyzer_params):
        """수요 급감: 행사 종료 + 실매출 미달"""
        a = self._make_analyzer(analyzer_params)
        event = {"item_cd": "B003", "waste_qty": 5, "initial_qty": 10}
        context = {
            "daily_avg": 4.0,
            "order_qty": 4,
            "expiration_days": 1,
            "predicted_qty": 4,
            "actual_sold_qty": 1,  # < 4 * 0.5
            "trend_ratio": 0.7,
            "sell_day_ratio": 0.6,
            "weather_info": {"delta": 2.0},
            "promo_info": {"promo_ended_recently": True},
        }
        result = a._classify(event, context)
        assert result.cause == WASTE_CAUSE_DEMAND_DROP

    def test_classify_expiry_mgmt_low_sell_ratio(self, analyzer_params):
        """유통기한 관리 실패: 낮은 판매빈도"""
        a = self._make_analyzer(analyzer_params)
        event = {"item_cd": "C001", "waste_qty": 2, "initial_qty": 3}
        context = {
            "daily_avg": 0.3,
            "order_qty": 1,
            "expiration_days": 3,
            "predicted_qty": 1,
            "actual_sold_qty": 1,
            "trend_ratio": 0.9,
            "sell_day_ratio": 0.2,  # < 0.5
            "weather_info": {},
            "promo_info": {"promo_ended_recently": False},
        }
        result = a._classify(event, context)
        assert result.cause == WASTE_CAUSE_EXPIRY_MGMT

    def test_classify_expiry_mgmt_high_waste_ratio(self, analyzer_params):
        """유통기한 관리 실패: 폐기율 60% (임계값 50% 초과)"""
        a = self._make_analyzer(analyzer_params)
        event = {"item_cd": "C002", "waste_qty": 6, "initial_qty": 10}
        context = {
            "daily_avg": 2.0,
            "order_qty": 3,
            "expiration_days": 2,  # 2.0 * 2 = 4, ratio = 3/4 = 0.75 (under 1.5)
            "predicted_qty": 3,
            "actual_sold_qty": 2,
            "trend_ratio": 0.8,
            "sell_day_ratio": 0.4,  # low
            "weather_info": {},
            "promo_info": {"promo_ended_recently": False},
        }
        result = a._classify(event, context)
        assert result.cause == WASTE_CAUSE_EXPIRY_MGMT

    def test_classify_mixed_no_clear_cause(self, analyzer_params):
        """복합/불명확: 어느 조건에도 명확히 해당 안 됨"""
        a = self._make_analyzer(analyzer_params)
        event = {"item_cd": "D001", "waste_qty": 1, "initial_qty": 5}
        context = {
            "daily_avg": 3.0,
            "order_qty": 3,
            "expiration_days": 2,  # ratio = 3 / (3*2) = 0.5 (< 1.5)
            "predicted_qty": 3,
            "actual_sold_qty": 2,  # 2/3 = 0.67 (> 0.5)
            "trend_ratio": 0.8,   # > 0.6
            "sell_day_ratio": 0.7, # > 0.5
            "weather_info": {"delta": 3.0},  # < 10
            "promo_info": {"promo_ended_recently": False},
        }
        result = a._classify(event, context)
        assert result.cause == WASTE_CAUSE_MIXED


# =========================================================================
# 2. 피드백 계산 테스트
# =========================================================================

class TestFeedbackComputation:
    """WasteCauseAnalyzer._compute_feedback() 테스트"""

    def _make_analyzer(self, params):
        analyzer = WasteCauseAnalyzer.__new__(WasteCauseAnalyzer)
        analyzer.store_id = "46513"
        analyzer.params = params
        analyzer.repo = MagicMock()
        return analyzer

    def test_feedback_over_order(self, analyzer_params):
        a = self._make_analyzer(analyzer_params)
        action, mult, expiry = a._compute_feedback(WASTE_CAUSE_OVER_ORDER, "2026-02-10")
        assert action == WASTE_FEEDBACK_REDUCE_SAFETY
        assert mult == 0.75
        assert expiry == "2026-02-24"  # +14일

    def test_feedback_expiry_mgmt(self, analyzer_params):
        a = self._make_analyzer(analyzer_params)
        action, mult, expiry = a._compute_feedback(WASTE_CAUSE_EXPIRY_MGMT, "2026-02-10")
        assert action == WASTE_FEEDBACK_SUPPRESS
        assert mult == 0.85
        assert expiry == "2026-03-03"  # +21일

    def test_feedback_demand_drop(self, analyzer_params):
        a = self._make_analyzer(analyzer_params)
        action, mult, expiry = a._compute_feedback(WASTE_CAUSE_DEMAND_DROP, "2026-02-10")
        assert action == WASTE_FEEDBACK_TEMP_REDUCE
        assert mult == 0.80
        assert expiry == "2026-02-17"  # +7일

    def test_feedback_mixed_default(self, analyzer_params):
        a = self._make_analyzer(analyzer_params)
        action, mult, expiry = a._compute_feedback(WASTE_CAUSE_MIXED, "2026-02-10")
        assert action == WASTE_FEEDBACK_DEFAULT
        assert mult == 1.0

    def test_demand_drop_decay_day0(self):
        result = WasteFeedbackAdjuster._apply_demand_drop_decay(
            0.80, "2026-02-10", "2026-02-10", 7
        )
        assert result == 0.80

    def test_demand_drop_decay_day3(self):
        result = WasteFeedbackAdjuster._apply_demand_drop_decay(
            0.80, "2026-02-10", "2026-02-13", 7
        )
        expected = 0.80 + (1.0 - 0.80) * (3.0 / 7.0)
        assert abs(result - expected) < 0.001

    def test_demand_drop_decay_day7(self):
        result = WasteFeedbackAdjuster._apply_demand_drop_decay(
            0.80, "2026-02-10", "2026-02-17", 7
        )
        assert result == 1.0

    def test_demand_drop_decay_after_expiry(self):
        result = WasteFeedbackAdjuster._apply_demand_drop_decay(
            0.80, "2026-02-10", "2026-02-20", 7
        )
        assert result == 1.0


# =========================================================================
# 3. Repository 테스트
# =========================================================================

class TestWasteCauseRepository:
    """WasteCauseRepository CRUD 테스트"""

    def test_upsert_and_retrieve(self, waste_repo):
        waste_repo.upsert_cause({
            "store_id": "46513",
            "waste_date": "2026-02-10",
            "item_cd": "A001",
            "item_nm": "테스트상품",
            "mid_cd": "001",
            "waste_qty": 5,
            "waste_source": "batch",
            "primary_cause": WASTE_CAUSE_OVER_ORDER,
            "confidence": 0.8,
            "feedback_action": WASTE_FEEDBACK_REDUCE_SAFETY,
            "feedback_multiplier": 0.75,
            "feedback_expiry_date": "2026-02-24",
        })

        results = waste_repo.get_causes_for_period(
            "2026-02-01", "2026-02-28", store_id="46513"
        )
        assert len(results) == 1
        assert results[0]["item_cd"] == "A001"
        assert results[0]["primary_cause"] == WASTE_CAUSE_OVER_ORDER

    def test_upsert_conflict_updates(self, waste_repo):
        """같은 store_id+waste_date+item_cd → UPDATE"""
        waste_repo.upsert_cause({
            "store_id": "46513",
            "waste_date": "2026-02-10",
            "item_cd": "A001",
            "primary_cause": WASTE_CAUSE_OVER_ORDER,
            "waste_source": "batch",
            "feedback_multiplier": 0.75,
            "feedback_expiry_date": "2026-02-24",
        })
        waste_repo.upsert_cause({
            "store_id": "46513",
            "waste_date": "2026-02-10",
            "item_cd": "A001",
            "primary_cause": WASTE_CAUSE_DEMAND_DROP,
            "waste_source": "batch",
            "feedback_multiplier": 0.80,
            "feedback_expiry_date": "2026-02-17",
        })

        results = waste_repo.get_causes_for_period(
            "2026-02-01", "2026-02-28", store_id="46513"
        )
        assert len(results) == 1
        assert results[0]["primary_cause"] == WASTE_CAUSE_DEMAND_DROP
        assert results[0]["feedback_multiplier"] == 0.80

    def test_get_active_feedback_not_expired(self, waste_repo):
        today = datetime.now().strftime("%Y-%m-%d")
        expiry = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
        waste_repo.upsert_cause({
            "store_id": "46513",
            "waste_date": today,
            "item_cd": "A001",
            "primary_cause": WASTE_CAUSE_OVER_ORDER,
            "waste_source": "batch",
            "feedback_multiplier": 0.75,
            "feedback_expiry_date": expiry,
        })

        fb = waste_repo.get_active_feedback("A001", today, store_id="46513")
        assert fb is not None
        assert fb["feedback_multiplier"] == 0.75

    def test_get_active_feedback_expired(self, waste_repo):
        today = datetime.now().strftime("%Y-%m-%d")
        expired = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        waste_repo.upsert_cause({
            "store_id": "46513",
            "waste_date": "2026-01-01",
            "item_cd": "A001",
            "primary_cause": WASTE_CAUSE_OVER_ORDER,
            "waste_source": "batch",
            "feedback_multiplier": 0.75,
            "feedback_expiry_date": expired,
        })

        fb = waste_repo.get_active_feedback("A001", today, store_id="46513")
        assert fb is None

    def test_cause_summary(self, waste_repo):
        today = datetime.now().strftime("%Y-%m-%d")
        for i, cause in enumerate([
            WASTE_CAUSE_OVER_ORDER,
            WASTE_CAUSE_OVER_ORDER,
            WASTE_CAUSE_DEMAND_DROP,
        ]):
            waste_repo.upsert_cause({
                "store_id": "46513",
                "waste_date": today,
                "item_cd": f"ITEM{i}",
                "primary_cause": cause,
                "waste_source": "batch",
                "waste_qty": 3,
                "feedback_multiplier": 1.0,
                "feedback_expiry_date": today,
            })

        summary = waste_repo.get_cause_summary(days=7, store_id="46513")
        assert summary[WASTE_CAUSE_OVER_ORDER]["count"] == 2
        assert summary[WASTE_CAUSE_DEMAND_DROP]["count"] == 1

    def test_batch_preload(self, waste_repo):
        today = datetime.now().strftime("%Y-%m-%d")
        expiry = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
        waste_repo.upsert_cause({
            "store_id": "46513",
            "waste_date": today,
            "item_cd": "B001",
            "primary_cause": WASTE_CAUSE_OVER_ORDER,
            "waste_source": "batch",
            "feedback_multiplier": 0.75,
            "feedback_expiry_date": expiry,
        })
        waste_repo.upsert_cause({
            "store_id": "46513",
            "waste_date": today,
            "item_cd": "B002",
            "primary_cause": WASTE_CAUSE_DEMAND_DROP,
            "waste_source": "batch",
            "feedback_multiplier": 0.80,
            "feedback_expiry_date": expiry,
        })

        batch = waste_repo.get_active_feedbacks_batch(
            ["B001", "B002", "B003"], today, store_id="46513"
        )
        assert "B001" in batch
        assert "B002" in batch
        assert "B003" not in batch


# =========================================================================
# 4. WasteFeedbackAdjuster 테스트
# =========================================================================

class TestWasteFeedbackAdjuster:
    """WasteFeedbackAdjuster 통합 테스트"""

    def test_no_feedback_returns_default(self, waste_db, analyzer_params):
        adj = WasteFeedbackAdjuster(store_id="46513", params=analyzer_params)
        adj.repo = WasteCauseRepository(db_path=waste_db, store_id="46513")

        result = adj.get_adjustment("UNKNOWN_ITEM")
        assert result.multiplier == 1.0
        assert result.has_active_feedback is False

    def test_active_feedback_returns_multiplier(self, waste_db, analyzer_params):
        repo = WasteCauseRepository(db_path=waste_db, store_id="46513")
        today = datetime.now().strftime("%Y-%m-%d")
        expiry = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
        repo.upsert_cause({
            "store_id": "46513",
            "waste_date": today,
            "item_cd": "X001",
            "primary_cause": WASTE_CAUSE_OVER_ORDER,
            "waste_source": "batch",
            "feedback_multiplier": 0.75,
            "feedback_expiry_date": expiry,
            "confidence": 0.85,
        })

        adj = WasteFeedbackAdjuster(store_id="46513", params=analyzer_params)
        adj.repo = repo

        result = adj.get_adjustment("X001")
        assert result.multiplier == 0.75
        assert result.has_active_feedback is True
        assert result.primary_cause == WASTE_CAUSE_OVER_ORDER

    def test_demand_drop_decays_over_time(self, waste_db, analyzer_params):
        repo = WasteCauseRepository(db_path=waste_db, store_id="46513")
        # 3일 전 폐기
        waste_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        expiry = (datetime.now() + timedelta(days=4)).strftime("%Y-%m-%d")
        repo.upsert_cause({
            "store_id": "46513",
            "waste_date": waste_date,
            "item_cd": "Y001",
            "primary_cause": WASTE_CAUSE_DEMAND_DROP,
            "waste_source": "daily_sales",
            "feedback_multiplier": 0.80,
            "feedback_expiry_date": expiry,
        })

        adj = WasteFeedbackAdjuster(store_id="46513", params=analyzer_params)
        adj.repo = repo

        result = adj.get_adjustment("Y001")
        # 3일 경과, 7일 감쇄: 0.80 + (1.0 - 0.80) * (3/7) ≈ 0.886
        expected = 0.80 + 0.20 * (3.0 / 7.0)
        assert abs(result.multiplier - expected) < 0.01
        assert result.has_active_feedback is True

    def test_preload_caches_results(self, waste_db, analyzer_params):
        repo = WasteCauseRepository(db_path=waste_db, store_id="46513")
        today = datetime.now().strftime("%Y-%m-%d")
        expiry = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
        repo.upsert_cause({
            "store_id": "46513",
            "waste_date": today,
            "item_cd": "Z001",
            "primary_cause": WASTE_CAUSE_OVER_ORDER,
            "waste_source": "batch",
            "feedback_multiplier": 0.75,
            "feedback_expiry_date": expiry,
        })

        adj = WasteFeedbackAdjuster(store_id="46513", params=analyzer_params)
        adj.repo = repo
        adj.preload(["Z001", "Z002"])

        r1 = adj.get_adjustment("Z001")
        r2 = adj.get_adjustment("Z002")
        assert r1.has_active_feedback is True
        assert r2.has_active_feedback is False
        assert r1.multiplier == 0.75
        assert r2.multiplier == 1.0

    def test_disabled_returns_default(self, waste_db):
        params = {"enabled": False}
        adj = WasteFeedbackAdjuster(store_id="46513", params=params)
        adj.repo = WasteCauseRepository(db_path=waste_db, store_id="46513")

        result = adj.get_adjustment("ANY_ITEM")
        assert result.multiplier == 1.0
        assert result.has_active_feedback is False


# =========================================================================
# 5. analyze_date 통합 테스트
# =========================================================================

class TestAnalyzeDate:
    """WasteCauseAnalyzer.analyze_date() 통합 테스트 (모킹)"""

    def test_analyze_date_no_waste_events(self, waste_db, analyzer_params):
        """폐기 이벤트 없음 → analyzed=0"""
        analyzer = WasteCauseAnalyzer.__new__(WasteCauseAnalyzer)
        analyzer.store_id = "46513"
        analyzer.params = analyzer_params
        analyzer.repo = WasteCauseRepository(db_path=waste_db, store_id="46513")

        with patch.object(analyzer, '_gather_waste_events', return_value=[]):
            result = analyzer.analyze_date("2026-02-10")

        assert result["analyzed"] == 0
        assert result["by_cause"] == {}

    def test_analyze_date_saves_results(self, waste_db, analyzer_params):
        """폐기 이벤트 있음 → DB에 저장"""
        analyzer = WasteCauseAnalyzer.__new__(WasteCauseAnalyzer)
        analyzer.store_id = "46513"
        analyzer.params = analyzer_params
        analyzer.repo = WasteCauseRepository(db_path=waste_db, store_id="46513")

        events = [
            {"item_cd": "A001", "item_nm": "과잉상품", "mid_cd": "001",
             "waste_qty": 5, "waste_source": "batch", "initial_qty": 10},
        ]
        context = {
            "daily_avg": 2.0, "order_qty": 8, "expiration_days": 2,
            "predicted_qty": 4, "actual_sold_qty": 3,
            "trend_ratio": 1.0, "sell_day_ratio": 0.8,
            "weather_info": None, "promo_info": None, "holiday_info": None,
        }

        with patch.object(analyzer, '_gather_waste_events', return_value=events), \
             patch.object(analyzer, '_gather_context', return_value=context):
            result = analyzer.analyze_date("2026-02-10")

        assert result["analyzed"] == 1
        assert WASTE_CAUSE_OVER_ORDER in result["by_cause"]

        # DB에 저장되었는지 확인
        stored = analyzer.repo.get_causes_for_period(
            "2026-02-10", "2026-02-10", store_id="46513"
        )
        assert len(stored) == 1
        assert stored[0]["item_cd"] == "A001"

    def test_analyze_date_idempotent(self, waste_db, analyzer_params):
        """같은 날 2회 실행 → upsert로 중복 없음"""
        analyzer = WasteCauseAnalyzer.__new__(WasteCauseAnalyzer)
        analyzer.store_id = "46513"
        analyzer.params = analyzer_params
        analyzer.repo = WasteCauseRepository(db_path=waste_db, store_id="46513")

        events = [
            {"item_cd": "B001", "item_nm": "테스트", "mid_cd": "001",
             "waste_qty": 3, "waste_source": "daily_sales", "initial_qty": 5},
        ]
        context = {
            "daily_avg": 1.0, "order_qty": 3, "expiration_days": 1,
            "predicted_qty": 2, "actual_sold_qty": 1,
            "trend_ratio": 0.4, "sell_day_ratio": 0.7,
            "weather_info": None, "promo_info": None, "holiday_info": None,
        }

        with patch.object(analyzer, '_gather_waste_events', return_value=events), \
             patch.object(analyzer, '_gather_context', return_value=context):
            r1 = analyzer.analyze_date("2026-02-10")
            r2 = analyzer.analyze_date("2026-02-10")

        assert r1["analyzed"] == 1
        assert r2["analyzed"] == 1

        stored = analyzer.repo.get_causes_for_period(
            "2026-02-10", "2026-02-10", store_id="46513"
        )
        assert len(stored) == 1


# =========================================================================
# 6. 웹 API 테스트
# =========================================================================

class TestWebAPI:
    """Flask API 테스트"""

    @pytest.fixture
    def waste_app(self, waste_db):
        from flask import Flask
        from src.web.routes.api_waste import waste_bp

        app = Flask(__name__)
        app.register_blueprint(waste_bp, url_prefix="/api/waste")
        app.config["TESTING"] = True
        return app

    def test_summary_endpoint(self, waste_app, waste_db):
        repo = WasteCauseRepository(db_path=waste_db, store_id="46513")
        today = datetime.now().strftime("%Y-%m-%d")
        repo.upsert_cause({
            "store_id": "46513",
            "waste_date": today,
            "item_cd": "W001",
            "primary_cause": WASTE_CAUSE_OVER_ORDER,
            "waste_source": "batch",
            "waste_qty": 5,
            "feedback_multiplier": 0.75,
            "feedback_expiry_date": today,
        })

        with waste_app.test_client() as client:
            with patch(
                "src.web.routes.api_waste.WasteCauseRepository",
                return_value=repo,
            ):
                resp = client.get("/api/waste/summary?store_id=46513")

        assert resp.status_code == 200
        data = resp.get_json()
        assert "by_cause" in data

    def test_feedback_endpoint_no_feedback(self, waste_app, waste_db):
        repo = WasteCauseRepository(db_path=waste_db, store_id="46513")

        with waste_app.test_client() as client:
            with patch(
                "src.web.routes.api_waste.WasteCauseRepository",
                return_value=repo,
            ):
                resp = client.get("/api/waste/feedback/NONEXIST?store_id=46513")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["has_feedback"] is False
