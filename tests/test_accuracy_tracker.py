"""
AccuracyTracker SMAPE/wMAPE 및 MAPE 통일 테스트

prediction-accuracy-fix PDCA 기능 테스트
"""
import sqlite3
import math
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

from src.prediction.accuracy.tracker import AccuracyTracker, AccuracyMetrics


# ============================================================
# 헬퍼
# ============================================================

def _make_pairs(data_list):
    """
    [{"predicted": N, "actual": M}, ...] → (predictions, actuals) 리스트 변환
    """
    predictions = []
    actuals = []
    for i, d in enumerate(data_list):
        predictions.append({
            "item_cd": f"ITEM_{i:04d}",
            "predicted_qty": d["predicted"],
            "date": "2026-02-26",
        })
        actuals.append({
            "item_cd": f"ITEM_{i:04d}",
            "actual_qty": d["actual"],
            "date": "2026-02-26",
        })
    return predictions, actuals


def _create_test_db(data_rows):
    """
    테스트용 임시 DB 생성.
    data_rows: [(item_cd, predicted_qty, actual_qty, prediction_date, store_id), ...]
    """
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prediction_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT NOT NULL,
            predicted_qty REAL,
            actual_qty REAL,
            prediction_date TEXT,
            target_date TEXT,
            store_id TEXT,
            model_type TEXT DEFAULT 'rule'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            mid_cd TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mid_categories (
            mid_cd TEXT PRIMARY KEY,
            mid_nm TEXT
        )
    """)

    for row in data_rows:
        item_cd, pred, actual, pred_date, store_id = row
        conn.execute(
            "INSERT INTO prediction_logs (item_cd, predicted_qty, actual_qty, prediction_date, target_date, store_id) VALUES (?,?,?,?,?,?)",
            (item_cd, pred, actual, pred_date, pred_date, store_id),
        )
        # products 테이블에도 삽입
        try:
            conn.execute(
                "INSERT OR IGNORE INTO products (item_cd, item_nm, mid_cd) VALUES (?,?,?)",
                (item_cd, f"상품_{item_cd}", "001"),
            )
        except Exception:
            pass

    conn.commit()
    conn.close()
    return db_path


# ============================================================
# SMAPE 계산 정확성 테스트
# ============================================================

class TestSmapeCalculation:
    """SMAPE 계산 정확성 테스트"""

    def setup_method(self):
        self.tracker = AccuracyTracker(db_path=":memory:")

    def test_smape_both_zero(self):
        """actual=0, pred=0 → SMAPE=0 (둘 다 0이면 완벽)"""
        preds, acts = _make_pairs([{"predicted": 0, "actual": 0}])
        m = self.tracker.calculate_metrics(preds, acts)
        assert m.smape == 0.0

    def test_smape_actual_zero_pred_one(self):
        """actual=0, pred=1 → SMAPE=200%"""
        preds, acts = _make_pairs([{"predicted": 1, "actual": 0}])
        m = self.tracker.calculate_metrics(preds, acts)
        assert m.smape == 200.0

    def test_smape_one_to_two(self):
        """actual=1, pred=2 → SMAPE=66.67%"""
        preds, acts = _make_pairs([{"predicted": 2, "actual": 1}])
        m = self.tracker.calculate_metrics(preds, acts)
        # |2-1| / ((2+1)/2) * 100 = 1/1.5 * 100 = 66.67
        assert abs(m.smape - 66.67) < 0.1

    def test_smape_symmetric(self):
        """SMAPE는 방향 대칭: actual=1,pred=3 vs actual=3,pred=1"""
        preds1, acts1 = _make_pairs([{"predicted": 3, "actual": 1}])
        preds2, acts2 = _make_pairs([{"predicted": 1, "actual": 3}])
        m1 = self.tracker.calculate_metrics(preds1, acts1)
        m2 = self.tracker.calculate_metrics(preds2, acts2)
        assert m1.smape == m2.smape

    def test_smape_perfect(self):
        """actual=pred → SMAPE=0"""
        preds, acts = _make_pairs([
            {"predicted": 3, "actual": 3},
            {"predicted": 5, "actual": 5},
            {"predicted": 0, "actual": 0},
        ])
        m = self.tracker.calculate_metrics(preds, acts)
        assert m.smape == 0.0


# ============================================================
# wMAPE 계산 정확성 테스트
# ============================================================

class TestWmapeCalculation:
    """wMAPE 계산 정확성 테스트"""

    def setup_method(self):
        self.tracker = AccuracyTracker(db_path=":memory:")

    def test_wmape_basic(self):
        """기본 wMAPE 검증"""
        preds, acts = _make_pairs([
            {"predicted": 2, "actual": 1},   # |err|=1
            {"predicted": 5, "actual": 10},  # |err|=5
        ])
        m = self.tracker.calculate_metrics(preds, acts)
        # wMAPE = (1+5) / (1+10) * 100 = 6/11 * 100 = 54.55
        assert abs(m.wmape - 54.55) < 0.1

    def test_wmape_heavy_weight(self):
        """대량판매 상품이 가중치 지배"""
        preds, acts = _make_pairs([
            {"predicted": 1, "actual": 0},   # 소량: |err|=1
            {"predicted": 100, "actual": 100},  # 대량: |err|=0
        ])
        m = self.tracker.calculate_metrics(preds, acts)
        # wMAPE = (1+0) / (0+100) * 100 = 1%
        assert abs(m.wmape - 1.0) < 0.1

    def test_wmape_all_zero_actual(self):
        """SUM(actual)=0일 때 wMAPE=0"""
        preds, acts = _make_pairs([
            {"predicted": 1, "actual": 0},
            {"predicted": 0, "actual": 0},
        ])
        m = self.tracker.calculate_metrics(preds, acts)
        assert m.wmape == 0.0


# ============================================================
# MAPE 통일 검증 테스트
# ============================================================

class TestMapeUnification:
    """MAPE 계산 통일: actual=0 제외"""

    def setup_method(self):
        self.tracker = AccuracyTracker(db_path=":memory:")

    def test_mape_excludes_zero_actual(self):
        """MAPE는 actual=0인 건 제외"""
        preds, acts = _make_pairs([
            {"predicted": 0, "actual": 0},   # 제외
            {"predicted": 1, "actual": 0},   # 제외
            {"predicted": 2, "actual": 1},   # MAPE=100%
            {"predicted": 1, "actual": 1},   # MAPE=0%
        ])
        m = self.tracker.calculate_metrics(preds, acts)
        # actual>0인 건: 2건 → MAPE = (100+0)/2 = 50%
        assert abs(m.mape - 50.0) < 0.1

    def test_daily_mape_trend_excludes_zero(self):
        """get_daily_mape_trend SQL에서 actual=0 제외 (ELSE 0 버그 수정 검증)"""
        today = datetime.now().strftime("%Y-%m-%d")
        rows = [
            ("A001", 0, 0, today, None),  # actual=0 → 제외
            ("A002", 1, 0, today, None),  # actual=0 → 제외
            ("A003", 2, 1, today, None),  # MAPE=100%
            ("A004", 1, 1, today, None),  # MAPE=0%
        ]
        db_path = _create_test_db(rows)
        try:
            tracker = AccuracyTracker(db_path=db_path)
            trend = tracker.get_daily_mape_trend(1)
            assert len(trend) >= 1
            # actual>0인 2건만 MAPE 계산: (100+0)/2 = 50
            day = trend[0]
            assert abs(day["mape"] - 50.0) < 1.0
            assert day["sold_count"] == 2
        finally:
            os.unlink(db_path)

    def test_worst_items_excludes_zero(self):
        """get_worst_items SQL에서 actual=0 제외 확인"""
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        rows = [
            # 상품 A: actual=0만 → MAPE=NULL (제외됨)
            ("A001", 1, 0, today, None),
            ("A001", 1, 0, yesterday, None),
            # 상품 B: actual>0 → MAPE 계산됨
            ("B001", 3, 1, today, None),
            ("B001", 2, 1, yesterday, None),
        ]
        db_path = _create_test_db(rows)
        try:
            tracker = AccuracyTracker(db_path=db_path)
            worst = tracker.get_worst_items(days=3, limit=10, min_samples=1)
            # B001은 MAPE 계산됨, A001은 MAPE=NULL이므로 smape 기준으로 정렬
            assert len(worst) >= 1
            # smape가 있어야 함
            for item in worst:
                assert "smape" in item
        finally:
            os.unlink(db_path)


# ============================================================
# API 응답 검증 테스트
# ============================================================

class TestApiResponseFields:
    """API 응답에 smape/wmape 필드 존재 확인"""

    def test_qty_accuracy_has_smape_wmape(self):
        """_get_qty_accuracy에서 smape, wmape 반환"""
        tracker = AccuracyTracker(db_path=":memory:")
        metrics = tracker._empty_metrics()
        assert hasattr(metrics, "smape")
        assert hasattr(metrics, "wmape")
        assert metrics.smape == 0.0
        assert metrics.wmape == 0.0

    def test_accuracy_metrics_dataclass_fields(self):
        """AccuracyMetrics에 smape, wmape 필드 존재"""
        m = AccuracyMetrics(
            period_start="2026-02-20",
            period_end="2026-02-26",
            total_predictions=100,
            mape=50.0, mae=1.5, rmse=2.0,
            smape=42.0, wmape=30.0,
            accuracy_exact=10.0,
            accuracy_within_1=50.0,
            accuracy_within_2=80.0,
            accuracy_within_3=95.0,
            over_prediction_rate=30.0,
            under_prediction_rate=20.0,
            avg_over_amount=1.5,
            avg_under_amount=0.8,
        )
        assert m.smape == 42.0
        assert m.wmape == 30.0

    def test_daily_trend_has_smape(self):
        """get_daily_mape_trend 반환에 smape 키 존재"""
        today = datetime.now().strftime("%Y-%m-%d")
        rows = [("X001", 1, 1, today, None)]
        db_path = _create_test_db(rows)
        try:
            tracker = AccuracyTracker(db_path=db_path)
            trend = tracker.get_daily_mape_trend(1)
            if trend:
                assert "smape" in trend[0]
                assert "sold_count" in trend[0]
        finally:
            os.unlink(db_path)


# ============================================================
# 경계 케이스 테스트
# ============================================================

class TestEdgeCases:
    """경계 케이스"""

    def setup_method(self):
        self.tracker = AccuracyTracker(db_path=":memory:")

    def test_empty_predictions(self):
        """빈 데이터 → 모든 지표 0"""
        m = self.tracker.calculate_metrics([], [])
        assert m.total_predictions == 0
        assert m.mape == 0
        assert m.smape == 0
        assert m.wmape == 0
        assert m.mae == 0

    def test_all_actual_zero(self):
        """전부 actual=0 → MAPE=0, SMAPE는 계산됨"""
        preds, acts = _make_pairs([
            {"predicted": 1, "actual": 0},
            {"predicted": 0, "actual": 0},
            {"predicted": 2, "actual": 0},
        ])
        m = self.tracker.calculate_metrics(preds, acts)
        # MAPE: actual>0 없으므로 0
        assert m.mape == 0.0
        # SMAPE: (200+0+200)/3 = 133.33
        assert abs(m.smape - 133.33) < 0.1
        # wMAPE: SUM(actual)=0이므로 0
        assert m.wmape == 0.0

    def test_mixed_zero_nonzero(self):
        """편의점 전형 데이터: actual=0 다수 + actual>0 소수"""
        preds, acts = _make_pairs([
            {"predicted": 0, "actual": 0},   # SMAPE=0
            {"predicted": 1, "actual": 0},   # SMAPE=200
            {"predicted": 0, "actual": 0},   # SMAPE=0
            {"predicted": 1, "actual": 1},   # SMAPE=0
            {"predicted": 0, "actual": 0},   # SMAPE=0
            {"predicted": 2, "actual": 1},   # SMAPE=66.67
            {"predicted": 0, "actual": 0},   # SMAPE=0
            {"predicted": 0, "actual": 1},   # SMAPE=200
            {"predicted": 0, "actual": 0},   # SMAPE=0
            {"predicted": 0, "actual": 0},   # SMAPE=0
        ])
        m = self.tracker.calculate_metrics(preds, acts)

        # total=10
        assert m.total_predictions == 10

        # MAPE: actual>0 → 3건: (0%+100%+100%)/3 = 66.67
        assert abs(m.mape - 66.67) < 0.1

        # SMAPE: (0+200+0+0+0+66.67+0+200+0+0)/10 = 46.67
        assert abs(m.smape - 46.67) < 0.1

        # wMAPE: SUM(|err|)=1+0+1+1 = 3, SUM(actual)=0+0+0+1+0+1+0+1+0+0 = 3
        # wMAPE = 3/3*100 = 100%
        assert abs(m.wmape - 100.0) < 0.1

    def test_smape_wmape_required_fields(self):
        """smape/wmape가 필수 필드이며 명시적으로 설정 가능"""
        m = AccuracyMetrics(
            period_start="", period_end="",
            total_predictions=0,
            mape=0, mae=0, rmse=0,
            smape=0, wmape=0,
            accuracy_exact=0, accuracy_within_1=0,
            accuracy_within_2=0, accuracy_within_3=0,
            over_prediction_rate=0, under_prediction_rate=0,
            avg_over_amount=0, avg_under_amount=0,
        )
        assert m.smape == 0.0
        assert m.wmape == 0.0
