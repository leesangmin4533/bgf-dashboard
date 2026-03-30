"""
BasePredictor — 기본 예측 계산 (WMA, Croston, Feature 블렌딩)

ImprovedPredictor에서 추출된 단일 책임 클래스.
수요 패턴별 분기 + 가중이동평균 + 간헐적 수요 보정을 담당한다.

god-class-decomposition PDCA Step 1
"""

import sqlite3
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Callable, Set

from src.utils.logger import get_logger
from .prediction_config import PREDICTION_PARAMS

logger = get_logger(__name__)


class BasePredictor:
    """기본 예측 계산 (WMA + Feature 블렌딩 + Croston)

    ImprovedPredictor로부터 추출된 클래스.
    기본 예측값을 계산하며, 계수 적용은 CoefficientAdjuster가 담당한다.
    """

    def __init__(
        self,
        data_provider,
        feature_calculator,
        store_id: str,
        holiday_context_fn: Optional[Callable[[str], Dict]] = None,
    ):
        """
        Args:
            data_provider: PredictionDataProvider 인스턴스
            feature_calculator: FeatureCalculator 인스턴스
            store_id: 점포 코드
            holiday_context_fn: 연휴 맥락 조회 콜백 (_get_holiday_context)
        """
        self._data = data_provider
        self._feature_calculator = feature_calculator
        self.store_id = store_id
        self._holiday_context_fn = holiday_context_fn

    def _get_connection(self, timeout: int = 30) -> sqlite3.Connection:
        """DB 연결 (PredictionDataProvider로 위임)"""
        return self._data._get_connection(timeout)

    # =========================================================================
    # 메인 진입점
    # =========================================================================

    def compute(self, item_cd: str, product: Dict, target_date: datetime,
                demand_pattern_cache: Dict) -> Tuple:
        """기본 예측 계산 — 수요 패턴 분기 (prediction-redesign)

        Args:
            item_cd: 상품코드
            product: 상품 정보 dict
            target_date: 예측 대상 날짜
            demand_pattern_cache: 수요 패턴 분류 캐시

        Returns:
            (base_prediction, data_days, _wma_days, feat_result,
             sell_day_ratio, intermittent_adjusted)
        """
        from src.prediction.demand_classifier import DEMAND_PATTERN_EXEMPT_MIDS

        mid_cd = product.get("mid_cd", "")
        pattern_result = demand_pattern_cache.get(item_cd)
        pattern = pattern_result.pattern if pattern_result else "frequent"

        # 푸드/디저트: 기존 파이프라인 유지
        if mid_cd in DEMAND_PATTERN_EXEMPT_MIDS or not demand_pattern_cache:
            return self._compute_wma(item_cd, product, target_date)

        # slow: 예측 불필요 (ROP에서 처리)
        # 단, 데이터 부족으로 slow 분류되었으나 실제 판매가 있는 경우 안전장치
        if pattern == "slow":
            sell_day_ratio = pattern_result.sell_day_ratio if pattern_result else 0.0
            data_days = self._data._get_data_span_days(item_cd)

            # 안전장치: 데이터 부족(data_sufficient=False) + 실제 판매 존재 시
            # WMA 폴백으로 최소 예측 보장 (수집 갭으로 인한 오분류 방지)
            if pattern_result and not pattern_result.data_sufficient:
                if pattern_result.sell_days > 0 and pattern_result.total_days > 0:
                    actual_ratio = pattern_result.sell_days / pattern_result.total_days
                    if actual_ratio >= 0.3:
                        logger.info(
                            f"[PRED][slow→WMA] {product.get('item_nm', item_cd)}: "
                            f"pattern=slow BUT data_ratio={actual_ratio:.1%} "
                            f"(sell={pattern_result.sell_days}/total={pattern_result.total_days}) "
                            f"→ WMA 폴백"
                        )
                        return self._compute_wma(item_cd, product, target_date)

            logger.info(
                f"[PRED][slow] {product.get('item_nm', item_cd)}: "
                f"pattern=slow, ratio={sell_day_ratio:.2%} -> 예측 스킵 (ROP)"
            )
            return 0.0, data_days, 0, None, sell_day_ratio, False

        # intermittent: Croston/TSB 예측
        if pattern == "intermittent":
            return self._compute_croston(item_cd, product, target_date, pattern_result)

        # daily / frequent: 기존 WMA (간헐보정은 DemandClassifier가 대체)
        return self._compute_wma(item_cd, product, target_date)

    # =========================================================================
    # WMA 기반 예측
    # =========================================================================

    def _compute_wma(self, item_cd: str, product: Dict, target_date: datetime) -> Tuple:
        """기존 WMA 기반 예측 (daily/frequent/exempt 공용)

        Returns:
            (base_prediction, data_days, _wma_days, feat_result,
             sell_day_ratio, intermittent_adjusted)
        """
        # 판매 이력 조회 + WMA (행사 기간 가중치 감쇄 포함)
        history = self._data.get_sales_history(item_cd, PREDICTION_PARAMS["moving_avg_days"])
        wma_prediction, _wma_days = self.calculate_weighted_average(
            history, clean_outliers=True, mid_cd=product["mid_cd"],
            item_cd=item_cd
        )
        data_days = self._data._get_data_span_days(item_cd)

        # 콜드스타트 보정: 데이터 7일 미만 신규 상품은 일평균으로 WMA 보정
        # WMA(7일)가 소수 판매를 희석시켜 0으로 만드는 순환 함정 방지
        if 0 < data_days < 7 and history:
            total_sales = sum(row[1] for row in history if row[1] > 0)
            if total_sales > 0:
                daily_avg_cold = total_sales / data_days
                if daily_avg_cold > wma_prediction:
                    logger.info(
                        f"[PRED][cold-start] {product['item_nm']}({item_cd}): "
                        f"data_days={data_days}, daily_avg={daily_avg_cold:.2f} > "
                        f"WMA={wma_prediction:.2f} → 보정"
                    )
                    wma_prediction = daily_avg_cold

        logger.info(
            f"[PRED][1-WMA] {product['item_nm']}({item_cd}): "
            f"WMA={wma_prediction:.2f} (days={_wma_days})"
        )

        # dryrun-excel-export: feature blend 전 WMA 원본 캡처
        self._last_wma_raw = round(wma_prediction, 2)

        # Feature 기반 예측 블렌딩 (EWM + 동요일 평균 + 트렌드)
        base_prediction = wma_prediction
        feat_result = None
        try:
            feat_result = self._feature_calculator.calculate(item_cd, target_date)
            if feat_result.rolling_features and feat_result.rolling_features.data_days >= 7:
                wp = feat_result.weighted_prediction
                if wp > 0 and wma_prediction > 0:
                    if feat_result.data_quality == "high":
                        feat_weight = 0.40
                    elif feat_result.data_quality == "medium":
                        feat_weight = 0.25
                    else:
                        feat_weight = 0.10
                    wma_weight = 1.0 - feat_weight
                    base_prediction = round(
                        wma_prediction * wma_weight + wp * feat_weight, 2
                    )
                    if abs(base_prediction - wma_prediction) > 0.1:
                        logger.info(
                            f"[PRED][1-Feat] {product.get('item_nm', item_cd)}: "
                            f"WMA={wma_prediction:.2f} + Feat={wp:.2f} "
                            f"(w={feat_weight:.0%}) -> {base_prediction:.2f}"
                        )
        except Exception as e:
            logger.debug(f"[기간대비] Feature 계산 실패 ({item_cd}): {e}")

        # 간헐적 수요 보정 (가용일 기준)
        intermittent_config = PREDICTION_PARAMS.get("intermittent_demand", {})
        sell_day_ratio = 1.0
        intermittent_adjusted = False

        if intermittent_config.get("enabled", False) and data_days > 0:
            sell_day_ratio = self._calculate_available_sell_ratio(item_cd)
            intermittent_threshold = intermittent_config.get("threshold", 0.6)
            very_intermittent_threshold = intermittent_config.get("very_intermittent_threshold", 0.3)
            min_prediction_floor = intermittent_config.get("min_prediction_floor", 0.2)

            if sell_day_ratio < very_intermittent_threshold:
                original_pred = base_prediction
                base_prediction = max(base_prediction * 0.5, min_prediction_floor)
                intermittent_adjusted = True
                logger.info(
                    f"[간헐적v2-매우] {product['item_nm']}: "
                    f"ratio={sell_day_ratio:.2%}, "
                    f"{original_pred:.2f}→{base_prediction:.2f} "
                    f"(min_floor={min_prediction_floor})"
                )
            elif sell_day_ratio < intermittent_threshold:
                base_prediction *= sell_day_ratio
                intermittent_adjusted = True
                logger.info(
                    f"[간헐적v2-일반] {product['item_nm']}: "
                    f"ratio={sell_day_ratio:.2%}, pred={base_prediction:.2f}"
                )

        return base_prediction, data_days, _wma_days, feat_result, sell_day_ratio, intermittent_adjusted

    # =========================================================================
    # Croston/TSB 간헐수요 예측
    # =========================================================================

    def _compute_croston(self, item_cd: str, product: Dict, target_date: datetime,
                         pattern_result) -> Tuple:
        """Croston/TSB 기반 간헐수요 예측"""
        from src.prediction.croston_predictor import CrostonPredictor
        from src.analysis.croston_optimizer import get_croston_params

        history = self._get_daily_sales_history(item_cd, days=60)
        _alpha, _beta = get_croston_params(self.store_id, item_cd)
        predictor = CrostonPredictor(alpha=_alpha, beta=_beta)
        result = predictor.predict(history)

        data_days = self._data._get_data_span_days(item_cd)
        sell_day_ratio = pattern_result.sell_day_ratio if pattern_result else 0.25

        logger.info(
            f"[PRED][Croston] {product.get('item_nm', item_cd)}({item_cd}): "
            f"forecast={result.forecast:.3f}, "
            f"size={result.demand_size:.2f}, prob={result.demand_probability:.3f}, "
            f"interval={result.intervals_estimate:.1f}d, method={result.method}"
        )

        return result.forecast, data_days, 0, None, sell_day_ratio, True

    # =========================================================================
    # 일별 판매량 조회
    # =========================================================================

    def _get_daily_sales_history(self, item_cd: str, days: int = 60) -> list:
        """일별 판매량 리스트 (오래된것->최신 순)"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT sales_date, sale_qty
                FROM daily_sales
                WHERE item_cd = ? AND store_id = ?
                AND sales_date >= date('now', '-' || ? || ' days')
                ORDER BY sales_date ASC
            """, (item_cd, self.store_id, days))
            return [r[1] or 0 for r in cursor.fetchall()]
        finally:
            conn.close()

    # =========================================================================
    # 가중 이동평균 (WMA)
    # =========================================================================

    def calculate_weighted_average(
        self,
        sales_history: List[Tuple],
        clean_outliers: bool = True,
        mid_cd: Optional[str] = None,
        item_cd: Optional[str] = None
    ) -> Tuple[float, int]:
        """
        가중 이동평균 계산 (이상치 처리 + 품절일 imputation + 행사일 감쇄)

        품절일(stock_qty=0)은 수요 부재가 아니라 공급 부재이므로
        품절일의 판매량을 비품절일 평균으로 대체(imputation)한다.
        기존 "품절일 제외" 방식은 간헐적 판매 상품을 과대추정하는 문제가 있어
        전체 달력일 기준 WMA + 품절일 imputation 방식으로 개선.

        행사 기간 데이터는 연휴 보정과 동일하게 가중치를 감소시켜
        행사 종료 직후 예측 뻥튀기를 방지한다.

        Args:
            sales_history: [(날짜, 판매량, 재고량), ...] 리스트
                           (2-tuple도 하위 호환으로 허용)
            clean_outliers: 이상치 처리 여부 (기본 True)
            mid_cd: 중분류 코드 (카테고리별 이상치 설정용)
            item_cd: 상품코드 (행사 기간 필터링용, None이면 행사 보정 생략)

        Returns: (평균값, 사용된 데이터 일수)
        """
        if not sales_history:
            return 0.0, 0

        original_count = len(sales_history)

        # 품절일 imputation (3-tuple이고 설정 활성화 시)
        stockout_cfg = PREDICTION_PARAMS.get("stockout_filter", {})
        has_stock_info = len(sales_history[0]) >= 3
        if stockout_cfg.get("enabled", False) and has_stock_info:
            fresh_food_mids = set(
                PREDICTION_PARAMS.get("category_floor", {}).get(
                    "target_mid_cds", ["001", "002", "003", "004", "005"]
                )
            )
            include_none_as_stockout = mid_cd in fresh_food_mids

            available = [(d, qty, stk) for d, qty, stk in sales_history
                         if stk is not None and stk > 0]
            stockout = [(d, qty, stk) for d, qty, stk in sales_history
                        if stk is not None and stk == 0]

            if include_none_as_stockout:
                none_days = [(d, qty, stk) for d, qty, stk in sales_history
                             if stk is None]
                stockout = stockout + none_days

            if available and stockout:
                avg_available_sales = sum(row[1] for row in available) / len(available)

                imputed_history = []
                for row in sales_history:
                    if row[2] is not None and row[2] == 0:
                        imputed_history.append((row[0], avg_available_sales, row[2]))
                    elif row[2] is None and include_none_as_stockout:
                        imputed_history.append((row[0], avg_available_sales, row[2]))
                    else:
                        imputed_history.append(row)
                sales_history = imputed_history

        # 판매량 + 날짜 추출 (2-tuple, 3-tuple 모두 호환)
        sales = [row[1] for row in sales_history]
        dates = [row[0] for row in sales_history]

        # 이상치 처리 (최소 5일 이상일 때만)
        if clean_outliers and len(sales) >= 5:
            try:
                from .utils.outlier_handler import clean_sales_data
                result = clean_sales_data(sales, mid_cd=mid_cd, handle_zeros=True)
                sales = result.cleaned_data
            except ImportError:
                pass  # 모듈 없으면 원본 사용

        # 연휴일 WMA 가중치 감소 (연휴 매출 데이터의 예측 왜곡 방지)
        holiday_wma_cfg = PREDICTION_PARAMS.get("holiday_wma_correction", {})
        holiday_weight_factor = 1.0
        holiday_dates_set: Set[str] = set()
        if holiday_wma_cfg.get("enabled", False) and self._holiday_context_fn:
            holiday_weight_factor = holiday_wma_cfg.get("holiday_weight_factor", 0.3)
            for d in dates:
                try:
                    ctx = self._holiday_context_fn(d)
                    if ctx.get("in_period", False):
                        holiday_dates_set.add(d)
                except Exception:
                    pass

        # 행사일 WMA 가중치 감소 (행사 매출 데이터의 예측 왜곡 방지)
        promo_wma_cfg = PREDICTION_PARAMS.get("promo_wma_correction", {})
        promo_weight_factor = promo_wma_cfg.get("promo_weight_factor", 0.25)
        promo_dates_set: Set[str] = set()
        if promo_wma_cfg.get("enabled", True) and item_cd:
            try:
                promo_dates_set = self._data.get_promo_dates_in_range(item_cd, dates)
            except Exception:
                pass  # promotions 테이블 미존재 등

        weights = PREDICTION_PARAMS["weights"]
        total_weight = 0.0
        weighted_sum = 0.0

        for i, qty in enumerate(sales):
            if i == 0:
                w = weights["day_1"]
            elif i == 1:
                w = weights["day_2"]
            elif i == 2:
                w = weights["day_3"]
            else:
                w = weights["day_4_7"] / max(len(sales) - 3, 1)

            # 연휴일이면 가중치 감소
            if i < len(dates) and dates[i] in holiday_dates_set:
                w *= holiday_weight_factor

            # 행사일이면 가중치 감소 (행사 종료 후 예측 뻥튀기 방지)
            if i < len(dates) and dates[i] in promo_dates_set:
                w *= promo_weight_factor

            weighted_sum += qty * w
            total_weight += w

        if total_weight > 0:
            return weighted_sum / total_weight, original_count
        return 0.0, 0

    # =========================================================================
    # 판매일 비율 계산
    # =========================================================================

    def _calculate_sell_day_ratio(self, item_cd: str, data_days: int) -> float:
        """
        판매일 비율 계산 (달력일수 대비 판매일 비율)

        Args:
            item_cd: 상품코드
            data_days: 판매 기록이 있는 일수

        Returns:
            판매일 비율 (0.0 ~ 1.0). 데이터 부족 시 1.0 반환
        """
        if data_days <= 0:
            return 1.0

        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT MIN(sales_date), MAX(sales_date)
                FROM daily_sales
                WHERE item_cd = ? AND store_id = ?
                AND sales_date >= date('now', '-' || ? || ' days')
                AND sale_qty > 0
            """, (item_cd, self.store_id, PREDICTION_PARAMS["moving_avg_days"]))

            row = cursor.fetchone()
            if not row or not row[0] or not row[1]:
                return 1.0

            min_date = datetime.strptime(row[0], "%Y-%m-%d")
            max_date = datetime.strptime(row[1], "%Y-%m-%d")
            calendar_span = (max_date - min_date).days + 1

            if calendar_span <= 0:
                return 1.0

            return min(data_days / calendar_span, 1.0)
        finally:
            conn.close()

    def _calculate_available_sell_ratio(self, item_cd: str) -> float:
        """
        가용일 중 판매일 비율 계산 (품절일 제외)

        품절일(stock_qty=0)을 제외하고, 재고가 있었던 날 중
        실제 판매가 발생한 날의 비율을 반환한다.

        Args:
            item_cd: 상품코드

        Returns:
            가용일 중 판매일 비율 (0.0 ~ 1.0). 가용일=0이면 1.0 반환
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            days = PREDICTION_PARAMS["moving_avg_days"]
            # ★ sell_days 정의: "재고 보유일 중 판매 발생일" (demand_classifier와 동일)
            # stock_qty > 0 조건 포함 — 품절일 제외 의도. 변경 시 demand_classifier와 동기화 필수
            cursor.execute("""
                SELECT
                    SUM(CASE WHEN stock_qty > 0 AND sale_qty > 0 THEN 1 ELSE 0 END) as sell_days,
                    SUM(CASE WHEN stock_qty > 0 THEN 1 ELSE 0 END) as available_days
                FROM daily_sales
                WHERE item_cd = ? AND store_id = ?
                AND sales_date >= date('now', '-' || ? || ' days')
            """, (item_cd, self.store_id, days))

            row = cursor.fetchone()
            if not row:
                return 1.0

            sell_days = row[0] or 0
            available_days = row[1] or 0

            if available_days == 0:
                return 1.0

            return min(sell_days / available_days, 1.0)
        finally:
            conn.close()
