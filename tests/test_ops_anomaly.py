"""ops_anomaly.py 단위 테스트 - 5개 지표 이상 판정 순수 함수"""

import pytest
from src.domain.ops_anomaly import (
    detect_anomalies,
    OpsAnomaly,
    _check_prediction_accuracy,
    _check_order_failure,
    _check_waste_rate,
    _check_collection_failure,
    _check_integrity_unresolved,
)


class TestDetectAnomalies:
    """detect_anomalies 통합 테스트"""

    def test_empty_metrics(self):
        result = detect_anomalies({})
        assert result == []

    def test_all_insufficient_data(self):
        metrics = {
            "prediction_accuracy": {"insufficient_data": True},
            "order_failure": {"insufficient_data": True},
            "waste_rate": {"insufficient_data": True},
            "collection_failure": {"insufficient_data": True},
            "integrity_unresolved": {"insufficient_data": True},
        }
        result = detect_anomalies(metrics)
        assert result == []

    def test_mixed_anomalies(self):
        metrics = {
            "prediction_accuracy": {"categories": []},  # 정상
            "order_failure": {"recent_7d": 20, "prev_7d": 8},  # 이상
            "waste_rate": {"categories": []},  # 정상
            "collection_failure": {"types": []},  # 정상
            "integrity_unresolved": {"checks": []},  # 정상
        }
        result = detect_anomalies(metrics)
        assert len(result) == 1
        assert result[0].metric_name == "order_failure"

    def test_none_values_skipped(self):
        """None 값 지표 스킵"""
        metrics = {
            "prediction_accuracy": None,
            "order_failure": {"recent_7d": 0, "prev_7d": 0},
        }
        result = detect_anomalies(metrics)
        assert result == []


class TestPredictionAccuracy:
    """예측 정확도 하락 감지"""

    def test_no_degradation(self):
        data = {"categories": [
            {"mid_cd": "001", "mae_7d": 1.0, "mae_14d": 1.0},
        ]}
        assert _check_prediction_accuracy(data) is None

    def test_single_category_degraded_p2(self):
        data = {"categories": [
            {"mid_cd": "001", "mae_7d": 2.5, "mae_14d": 1.8},  # 2.5/1.8=1.39 > 1.2
        ]}
        result = _check_prediction_accuracy(data)
        assert result is not None
        assert result.priority == "P2"
        assert "001" in result.description

    def test_three_categories_degraded_p1(self):
        data = {"categories": [
            {"mid_cd": "001", "mae_7d": 2.5, "mae_14d": 1.8},
            {"mid_cd": "002", "mae_7d": 3.0, "mae_14d": 2.0},
            {"mid_cd": "003", "mae_7d": 4.0, "mae_14d": 2.5},
        ]}
        result = _check_prediction_accuracy(data)
        assert result is not None
        assert result.priority == "P1"  # 3개 이상이면 P1

    def test_boundary_exact_ratio(self):
        """정확히 1.2배는 이상이 아닌 경계값"""
        data = {"categories": [
            {"mid_cd": "001", "mae_7d": 1.2, "mae_14d": 1.0},  # 정확히 1.2 -> NOT >
        ]}
        assert _check_prediction_accuracy(data) is None

    def test_empty_categories(self):
        assert _check_prediction_accuracy({"categories": []}) is None

    def test_zero_mae_14d(self):
        """14d MAE가 0이면 비교 스킵"""
        data = {"categories": [
            {"mid_cd": "001", "mae_7d": 1.0, "mae_14d": 0},
        ]}
        assert _check_prediction_accuracy(data) is None


class TestOrderFailure:
    """발주 실패 급증 감지"""

    def test_no_increase(self):
        data = {"recent_7d": 5, "prev_7d": 10}
        assert _check_order_failure(data) is None

    def test_significant_increase_p1(self):
        data = {"recent_7d": 16, "prev_7d": 10}  # 16/10=1.6 > 1.5
        result = _check_order_failure(data)
        assert result is not None
        assert result.priority == "P1"

    def test_prev_zero_recent_below_threshold(self):
        """이전 0건, 최근 2건 -> 스킵"""
        data = {"recent_7d": 2, "prev_7d": 0}
        assert _check_order_failure(data) is None

    def test_prev_zero_recent_above_threshold(self):
        """이전 0건, 최근 3건 이상 -> 감지"""
        data = {"recent_7d": 5, "prev_7d": 0}
        result = _check_order_failure(data)
        assert result is not None
        assert result.priority == "P1"

    def test_boundary_exact_ratio(self):
        """정확히 1.5배는 이상이 아닌 경계값"""
        data = {"recent_7d": 15, "prev_7d": 10}  # 15/10=1.5 -> NOT >
        assert _check_order_failure(data) is None


class TestWasteRate:
    """폐기율 상승 감지"""

    def test_no_increase(self):
        data = {"categories": [
            {"mid_cd": "001", "rate_7d": 0.03, "rate_30d": 0.03},
        ]}
        assert _check_waste_rate(data) == []

    def test_food_category_p1(self):
        data = {"categories": [
            {"mid_cd": "001", "rate_7d": 0.06, "rate_30d": 0.03},  # 2.0 > 1.5
        ]}
        result = _check_waste_rate(data)
        assert len(result) == 1
        assert result[0].priority == "P1"  # 푸드는 P1

    def test_nonfood_category_p2(self):
        data = {"categories": [
            {"mid_cd": "010", "rate_7d": 0.10, "rate_30d": 0.05},  # 2.0 > 1.5
        ]}
        result = _check_waste_rate(data)
        assert len(result) == 1
        assert result[0].priority == "P2"

    def test_multiple_categories(self):
        data = {"categories": [
            {"mid_cd": "001", "rate_7d": 0.06, "rate_30d": 0.03},
            {"mid_cd": "010", "rate_7d": 0.08, "rate_30d": 0.04},
        ]}
        result = _check_waste_rate(data)
        assert len(result) == 2

    def test_zero_rate_30d(self):
        """30일 폐기율 0이면 스킵"""
        data = {"categories": [
            {"mid_cd": "001", "rate_7d": 0.05, "rate_30d": 0},
        ]}
        assert _check_waste_rate(data) == []


class TestCollectionFailure:
    """수집 연속 실패 감지"""

    def test_no_failure(self):
        data = {"types": [{"type": "sales", "consecutive_fails": 0}]}
        assert _check_collection_failure(data) is None

    def test_three_day_failure_p1(self):
        data = {"types": [{"type": "sales", "consecutive_fails": 3}]}
        result = _check_collection_failure(data)
        assert result is not None
        assert result.priority == "P1"

    def test_below_threshold(self):
        data = {"types": [{"type": "sales", "consecutive_fails": 2}]}
        assert _check_collection_failure(data) is None

    def test_empty_types(self):
        assert _check_collection_failure({"types": []}) is None


class TestIntegrityUnresolved:
    """자전 시스템 미해결 감지"""

    def test_no_unresolved(self):
        data = {"checks": [{"name": "food_ghost_stock", "consecutive_days": 3}]}
        assert _check_integrity_unresolved(data) is None

    def test_seven_day_unresolved_p2(self):
        data = {"checks": [{"name": "food_ghost_stock", "consecutive_days": 7}]}
        result = _check_integrity_unresolved(data)
        assert result is not None
        assert result.priority == "P2"

    def test_fourteen_day_unresolved_p1(self):
        data = {"checks": [{"name": "food_ghost_stock", "consecutive_days": 14}]}
        result = _check_integrity_unresolved(data)
        assert result is not None
        assert result.priority == "P1"

    def test_empty_checks(self):
        assert _check_integrity_unresolved({"checks": []}) is None
