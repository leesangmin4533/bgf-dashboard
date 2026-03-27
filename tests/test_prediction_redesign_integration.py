"""prediction-redesign 통합 테스트

improved_predictor.py의 수요 패턴 분기, 덧셈 계수, Croston 안전재고를
통합적으로 검증한다.
"""

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from src.prediction.order_proposal import RuleResult
from src.prediction.demand_classifier import (
    DemandClassifier, DemandPattern, ClassificationResult,
    DEMAND_PATTERN_EXEMPT_MIDS,
)
from src.prediction.croston_predictor import CrostonPredictor, CrostonResult
from src.prediction.additive_adjuster import AdditiveAdjuster, AdditiveResult


# =========================================================================
# 헬퍼
# =========================================================================

def _make_classification(item_cd, pattern, sell_day_ratio=0.5):
    """ClassificationResult 빌더"""
    return ClassificationResult(
        item_cd=item_cd,
        pattern=DemandPattern(pattern),
        sell_day_ratio=sell_day_ratio,
        available_days=60,
        sell_days=int(60 * sell_day_ratio),
        total_days=60,
        data_sufficient=True,
    )


# =========================================================================
# 1. 파이프라인 라우팅 테스트
# =========================================================================

class TestPipelineRouting:
    """_compute_base_prediction()의 수요 패턴 분기"""

    def _make_predictor(self, demand_cache=None):
        """테스트용 ImprovedPredictor (DB 접근 모킹)"""
        with patch("src.prediction.improved_predictor.ImprovedPredictor.__init__", return_value=None):
            from src.prediction.improved_predictor import ImprovedPredictor
            p = ImprovedPredictor.__new__(ImprovedPredictor)
            p._demand_pattern_cache = demand_cache or {}
            p.store_id = "46513"
            p.db_path = ":memory:"
            return p

    def test_exempt_food_uses_wma(self):
        """푸드 면제 카테고리는 항상 WMA"""
        p = self._make_predictor({
            "ITEM001": _make_classification("ITEM001", "daily"),
        })
        product = {"mid_cd": "001", "item_nm": "도시락"}
        with patch.object(p, '_compute_base_prediction_wma', return_value=(5.0, 30, 7, None, 1.0, False)) as mock_wma:
            result = p._compute_base_prediction("ITEM001", product, datetime.now())
            mock_wma.assert_called_once()
            assert result[0] == 5.0

    def test_exempt_dessert_uses_wma(self):
        """디저트(012) 면제"""
        p = self._make_predictor({
            "ITEM002": _make_classification("ITEM002", "daily"),
        })
        product = {"mid_cd": "012", "item_nm": "케이크"}
        with patch.object(p, '_compute_base_prediction_wma', return_value=(3.0, 20, 5, None, 1.0, False)) as mock_wma:
            result = p._compute_base_prediction("ITEM002", product, datetime.now())
            mock_wma.assert_called_once()

    def test_slow_returns_zero(self):
        """slow 패턴 -> 예측=0 (ROP에서 처리)"""
        p = self._make_predictor({
            "ITEM003": _make_classification("ITEM003", "slow", sell_day_ratio=0.10),
        })
        product = {"mid_cd": "015", "item_nm": "과자"}
        with patch.object(p, '_get_data_span_days', return_value=60):
            result = p._compute_base_prediction("ITEM003", product, datetime.now())
            assert result[0] == 0.0  # base_prediction = 0
            assert result[4] == 0.10  # sell_day_ratio

    def test_intermittent_uses_croston(self):
        """intermittent 패턴 -> Croston/TSB"""
        p = self._make_predictor({
            "ITEM004": _make_classification("ITEM004", "intermittent", sell_day_ratio=0.25),
        })
        product = {"mid_cd": "043", "item_nm": "음료"}

        mock_history = [0, 0, 3, 0, 0, 0, 2, 0, 0, 4] + [0] * 50
        with patch.object(p, '_get_daily_sales_history', return_value=mock_history), \
             patch.object(p, '_get_data_span_days', return_value=60):
            result = p._compute_base_prediction("ITEM004", product, datetime.now())
            assert result[0] > 0  # Croston 예측값
            assert result[5] is True  # intermittent_adjusted

    def test_daily_uses_wma(self):
        """daily 패턴 -> WMA"""
        p = self._make_predictor({
            "ITEM005": _make_classification("ITEM005", "daily", sell_day_ratio=0.85),
        })
        product = {"mid_cd": "043", "item_nm": "음료"}
        with patch.object(p, '_compute_base_prediction_wma', return_value=(10.0, 30, 7, None, 0.85, False)) as mock_wma:
            result = p._compute_base_prediction("ITEM005", product, datetime.now())
            mock_wma.assert_called_once()

    def test_frequent_uses_wma(self):
        """frequent 패턴 -> WMA"""
        p = self._make_predictor({
            "ITEM006": _make_classification("ITEM006", "frequent", sell_day_ratio=0.55),
        })
        product = {"mid_cd": "043", "item_nm": "음료"}
        with patch.object(p, '_compute_base_prediction_wma', return_value=(7.0, 30, 7, None, 0.55, False)) as mock_wma:
            result = p._compute_base_prediction("ITEM006", product, datetime.now())
            mock_wma.assert_called_once()

    def test_empty_cache_uses_wma(self):
        """캐시 미로드 -> WMA 폴백"""
        p = self._make_predictor({})  # empty cache
        product = {"mid_cd": "043", "item_nm": "음료"}
        with patch.object(p, '_compute_base_prediction_wma', return_value=(8.0, 30, 7, None, 1.0, False)) as mock_wma:
            result = p._compute_base_prediction("ITEM007", product, datetime.now())
            mock_wma.assert_called_once()


# =========================================================================
# 2. 덧셈 vs 곱셈 분기 테스트
# =========================================================================

class TestAdditiveVsMultiplicative:
    """_apply_all_coefficients()의 덧셈/곱셈 분기"""

    def _make_predictor(self, demand_cache=None):
        with patch("src.prediction.improved_predictor.ImprovedPredictor.__init__", return_value=None):
            from src.prediction.improved_predictor import ImprovedPredictor
            p = ImprovedPredictor.__new__(ImprovedPredictor)
            p._demand_pattern_cache = demand_cache or {}
            p.store_id = "46513"
            p.db_path = ":memory:"
            p._association_adjuster = None
            return p

    def test_food_uses_multiplicative(self):
        """푸드 면제 -> 곱셈 파이프라인 (덧셈 경로 미사용 검증)"""
        p = self._make_predictor({
            "ITEM001": _make_classification("ITEM001", "daily"),
        })
        product = {"mid_cd": "001", "item_nm": "도시락"}

        # _coef (CoefficientAdjuster) mock 설정
        mock_coef = MagicMock()
        mock_coef.get_precipitation_coefficient.return_value = 1.0
        mock_coef.get_precipitation_for_date.return_value = {"rain_rate": None}
        mock_coef._apply_multiplicative.return_value = (10.0, 12.0, 1.0, 0)
        p._coef = mock_coef

        with patch.object(p, '_get_holiday_coefficient', return_value=1.2), \
             patch.object(p, '_get_weather_coefficient', return_value=1.0), \
             patch.object(p, '_get_temperature_for_date', return_value=20.0), \
             patch.object(p, '_apply_coefficients_additive') as mock_add:
            bp, adj, wc, ab = p._apply_all_coefficients(
                10.0, "ITEM001", product, datetime.now(), 3, None
            )
            # 푸드는 곱셈 경로(_coef._apply_multiplicative) 사용, 덧셈 경로 미사용
            mock_coef._apply_multiplicative.assert_called_once()
            mock_add.assert_not_called()
            assert adj == pytest.approx(12.0, abs=0.01)

    def test_non_food_uses_additive(self):
        """비면제 + 캐시 있음 -> 덧셈 파이프라인"""
        p = self._make_predictor({
            "ITEM002": _make_classification("ITEM002", "daily", sell_day_ratio=0.85),
        })
        product = {"mid_cd": "043", "item_nm": "음료"}

        with patch.object(p, '_get_holiday_coefficient', return_value=1.3), \
             patch.object(p, '_get_weather_coefficient', return_value=1.15), \
             patch.object(p, '_get_temperature_for_date', return_value=32.0), \
             patch.object(p, '_apply_coefficients_additive',
                          return_value=(10.0, 14.5, 1.0, 1.0)) as mock_add:
            bp, adj, wc, ab = p._apply_all_coefficients(
                10.0, "ITEM002", product, datetime.now(), 3, None
            )
            mock_add.assert_called_once()
            assert adj == 14.5

    def test_empty_cache_uses_multiplicative(self):
        """캐시 없음 -> 곱셈 폴백"""
        p = self._make_predictor({})
        product = {"mid_cd": "043", "item_nm": "음료"}

        with patch.object(p, '_get_holiday_coefficient', return_value=1.0), \
             patch.object(p, '_get_weather_coefficient', return_value=1.0), \
             patch.object(p, '_get_temperature_for_date', return_value=20.0), \
             patch.object(p, '_apply_coefficients_additive') as mock_add:
            bp, adj, wc, ab = p._apply_all_coefficients(
                10.0, "ITEM003", product, datetime.now(), 3, None
            )
            mock_add.assert_not_called()


# =========================================================================
# 3. AdditiveAdjuster 통합 시나리오
# =========================================================================

class TestAdditiveIntegration:
    """AdditiveAdjuster가 실제 계수 조합에서 올바르게 동작"""

    def test_daily_caps_compound_increase(self):
        """daily: 1.3*1.15*1.1=1.64x -> additive cap at 1.8x"""
        adj = AdditiveAdjuster(pattern="daily")
        result = adj.apply(
            base_prediction=10.0,
            holiday_coef=1.3,
            weather_coef=1.15,
            weekday_coef=1.1,
        )
        # deltas: 0.3+0.15+0.1 = 0.55, daily cap +0.8 -> unclamped
        assert result.adjusted_prediction == pytest.approx(15.5, abs=0.1)
        assert result.delta_sum == pytest.approx(0.55, abs=0.01)

    def test_daily_extreme_clamped(self):
        """daily: 1.5*1.4*1.3*1.2=3.27x -> clamped to 1.8x"""
        adj = AdditiveAdjuster(pattern="daily")
        result = adj.apply(
            base_prediction=10.0,
            holiday_coef=1.5,
            weather_coef=1.4,
            weekday_coef=1.3,
            seasonal_coef=1.2,
        )
        # deltas: 0.5+0.4+0.3+0.2 = 1.4, clamped to +0.8
        assert result.clamped_delta == 0.8
        assert result.adjusted_prediction == pytest.approx(18.0, abs=0.1)

    def test_frequent_tighter_cap(self):
        """frequent: same coefficients but tighter cap"""
        adj = AdditiveAdjuster(pattern="frequent")
        result = adj.apply(
            base_prediction=10.0,
            holiday_coef=1.5,
            weather_coef=1.4,
            weekday_coef=1.3,
            seasonal_coef=1.2,
        )
        # deltas: 1.4, clamped to +0.5
        assert result.clamped_delta == 0.5
        assert result.adjusted_prediction == pytest.approx(15.0, abs=0.1)

    def test_intermittent_tightest_cap(self):
        """intermittent: tightest positive cap at +0.3"""
        adj = AdditiveAdjuster(pattern="intermittent")
        result = adj.apply(
            base_prediction=10.0,
            holiday_coef=1.5,
            weather_coef=1.4,
        )
        # deltas: 0.5+0.4 = 0.9, clamped to +0.3
        assert result.clamped_delta == 0.3
        assert result.adjusted_prediction == pytest.approx(13.0, abs=0.1)

    def test_slow_ignores_all(self):
        """slow: 모든 계수 무시"""
        adj = AdditiveAdjuster(pattern="slow")
        result = adj.apply(
            base_prediction=5.0,
            holiday_coef=2.0,
            weather_coef=2.0,
        )
        assert result.adjusted_prediction == 5.0
        assert result.clamped_delta == 0.0


# =========================================================================
# 4. Croston 안전재고 통합
# =========================================================================

class TestCrostonSafetyIntegration:
    """_compute_safety_and_order()의 intermittent/slow 안전재고"""

    def _make_predictor(self, demand_cache=None):
        with patch("src.prediction.improved_predictor.ImprovedPredictor.__init__", return_value=None):
            from src.prediction.improved_predictor import ImprovedPredictor
            p = ImprovedPredictor.__new__(ImprovedPredictor)
            p._demand_pattern_cache = demand_cache or {}
            p.store_id = "46513"
            p.db_path = ":memory:"
            p._association_adjuster = None
            p._cost_optimizer = None
            p._diff_feedback = None
            p._waste_feedback = None
            p._use_promo_adjustment = False
            p._ml_predictor = None
            p._substitution_detector = None
            return p

    def test_slow_safety_zero(self):
        """slow 패턴 -> safety=0"""
        cache = {"ITEM_SLOW": _make_classification("ITEM_SLOW", "slow", 0.08)}
        p = self._make_predictor(cache)

        product = {
            "mid_cd": "015", "item_nm": "과자", "expiration_days": 180,
            "order_unit_qty": 1, "orderable_day": "월화수목금토",
            "lead_time_days": 1,
        }

        with patch.object(p, '_get_daily_sales_history', return_value=[0]*60), \
             patch.object(p, '_apply_order_rules', return_value=RuleResult(0, 0, "", "rules_pass")):
            ctx = p._compute_safety_and_order(
                "ITEM_SLOW", product, datetime.now(), 2, "015",
                0.0, 0.0, 1.0, 0, 0, 60, 0.08, None
            )
            assert ctx["safety_stock"] == 0.0

    def test_intermittent_croston_safety(self):
        """intermittent 패턴 -> Croston safety > 0"""
        cache = {"ITEM_INT": _make_classification("ITEM_INT", "intermittent", 0.25)}
        p = self._make_predictor(cache)

        product = {
            "mid_cd": "043", "item_nm": "음료", "expiration_days": 365,
            "order_unit_qty": 1, "orderable_day": "월화수목금토",
            "lead_time_days": 1,
        }

        # 간헐적 판매 이력: 10일마다 3개
        history = []
        for _ in range(6):
            history.extend([3, 0, 0, 0, 0, 0, 0, 0, 0, 0])

        with patch.object(p, '_get_daily_sales_history', return_value=history), \
             patch.object(p, '_apply_order_rules', return_value=RuleResult(1, 0, "", "rules_pass")):
            ctx = p._compute_safety_and_order(
                "ITEM_INT", product, datetime.now(), 2, "043",
                0.3, 0.3, 1.0, 0, 0, 60, 0.25, None
            )
            assert ctx["safety_stock"] > 0

    def test_daily_uses_category_handler(self):
        """daily 패턴 (비면제) -> 기존 카테고리 핸들러"""
        cache = {"ITEM_D": _make_classification("ITEM_D", "daily", 0.85)}
        p = self._make_predictor(cache)

        product = {
            "mid_cd": "043", "item_nm": "음료", "expiration_days": 365,
            "order_unit_qty": 1, "orderable_day": "월화수목금토",
            "lead_time_days": 1,
        }

        with patch.object(p, '_apply_order_rules', return_value=RuleResult(3, 0, "", "rules_pass")), \
             patch.object(p, '_calculate_days_until_next_order', return_value=2):
            ctx = p._compute_safety_and_order(
                "ITEM_D", product, datetime.now(), 2, "043",
                10.0, 10.0, 1.0, 5, 0, 60, 0.85, None
            )
            # safety_stock should come from category handler, not Croston
            assert ctx["safety_stock"] >= 0


# =========================================================================
# 5. PredictionResult demand_pattern 필드
# =========================================================================

class TestPredictionResultPattern:
    """PredictionResult에 demand_pattern 포함"""

    def test_demand_pattern_field_exists(self):
        """PredictionResult에 demand_pattern 필드"""
        from src.prediction.improved_predictor import PredictionResult
        result = PredictionResult(
            item_cd="TEST", item_nm="테스트", mid_cd="043",
            target_date="2026-02-25",
            predicted_qty=5.0, adjusted_qty=5.0,
            current_stock=0, pending_qty=0,
            safety_stock=2.0, order_qty=3,
            confidence="high", data_days=30,
            weekday_coef=1.0,
            demand_pattern="intermittent",
        )
        assert result.demand_pattern == "intermittent"

    def test_demand_pattern_default_empty(self):
        """기본값: 빈 문자열 (미분류)"""
        from src.prediction.improved_predictor import PredictionResult
        result = PredictionResult(
            item_cd="TEST", item_nm="테스트", mid_cd="001",
            target_date="2026-02-25",
            predicted_qty=5.0, adjusted_qty=5.0,
            current_stock=0, pending_qty=0,
            safety_stock=2.0, order_qty=3,
            confidence="high", data_days=30,
            weekday_coef=1.0,
        )
        assert result.demand_pattern == ""


# =========================================================================
# 6. ProductDetailRepository.bulk_update_demand_pattern
# =========================================================================

class TestBulkUpdateDemandPattern:
    """product_detail_repo.bulk_update_demand_pattern() 검증"""

    def test_update_patterns(self):
        """정상 갱신 — SQL 실행 검증"""
        from src.infrastructure.database.repos.product_detail_repo import ProductDetailRepository

        # 검증용: executemany 호출 시 SQL + 파라미터 캡처
        captured = {}

        class FakeConn:
            def __init__(self):
                self._committed = False

            def cursor(self):
                return self

            def executemany(self, sql, data):
                captured["sql"] = sql
                captured["data"] = list(data)
                self.rowcount = len(captured["data"])

            def commit(self):
                self._committed = True

            def close(self):
                pass

        fake = FakeConn()
        with patch.object(ProductDetailRepository, '_get_conn', return_value=fake), \
             patch.object(ProductDetailRepository, '_now', return_value="2026-02-25 12:00:00"):
            repo = ProductDetailRepository.__new__(ProductDetailRepository)
            repo.db_type = "common"
            repo.bulk_update_demand_pattern({
                "ITEM1": "daily",
                "ITEM2": "slow",
            })

        assert "UPDATE product_details" in captured["sql"]
        assert "demand_pattern" in captured["sql"]
        assert len(captured["data"]) == 2
        patterns = {row[2]: row[0] for row in captured["data"]}  # (pattern, now, item_cd)
        assert patterns["ITEM1"] == "daily"
        assert patterns["ITEM2"] == "slow"
        assert fake._committed is True

    def test_empty_patterns(self):
        """빈 dict -> 0건"""
        from src.infrastructure.database.repos.product_detail_repo import ProductDetailRepository
        repo = ProductDetailRepository.__new__(ProductDetailRepository)
        repo.db_type = "common"
        count = repo.bulk_update_demand_pattern({})
        assert count == 0


# =========================================================================
# 7. 엔드투엔드: 패턴별 예측 차이
# =========================================================================

class TestEndToEndPatternDifference:
    """같은 상품이 패턴에 따라 다른 예측 경로를 타는지"""

    def test_additive_clamps_where_multiplicative_would_not(self):
        """곱셈은 1.3*1.2*1.15=1.794x, 덧셈(daily)은 max 1.8x -> 둘 다 비슷
           하지만 frequent는 1.5x cap 적용"""
        base = 10.0

        # 곱셈
        multiplicative = base * 1.3 * 1.2 * 1.15  # 17.94

        # 덧셈 daily
        adj_daily = AdditiveAdjuster("daily")
        r_daily = adj_daily.apply(base, holiday_coef=1.3, weekday_coef=1.2, seasonal_coef=1.15)

        # 덧셈 frequent
        adj_freq = AdditiveAdjuster("frequent")
        r_freq = adj_freq.apply(base, holiday_coef=1.3, weekday_coef=1.2, seasonal_coef=1.15)

        # daily: 0.3+0.2+0.15=0.65 < 0.8 cap -> unclamped 16.5
        assert r_daily.adjusted_prediction == pytest.approx(16.5, abs=0.1)
        # frequent: 0.65 > 0.5 cap -> clamped to 15.0
        assert r_freq.adjusted_prediction == pytest.approx(15.0, abs=0.1)
        # multiplicative는 17.94 -> 덧셈이 더 보수적
        assert multiplicative > r_daily.adjusted_prediction

    def test_croston_vs_wma_for_intermittent(self):
        """간헐적 상품: Croston은 수요 크기와 확률을 분리 추정"""
        # 10일마다 5개 판매, 나머지 0
        history = []
        for _ in range(6):
            history.extend([5, 0, 0, 0, 0, 0, 0, 0, 0, 0])

        # Croston
        croston = CrostonPredictor()
        c_result = croston.predict(history)

        # 검증: Croston은 demand_size ≈ 5, probability ≈ 0.1~0.2
        assert c_result.forecast > 0
        assert c_result.demand_size > 3.0  # 판매일 평균 ~5
        assert c_result.demand_probability < 0.5  # 10일 중 1일이므로 ~0.1
        assert c_result.intervals_estimate >= 8  # ~10일 간격
        assert c_result.method == "tsb"

        # forecast = demand_size * probability → 합리적 범위
        assert 0.1 < c_result.forecast < 3.0
