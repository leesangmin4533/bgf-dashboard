"""
폐기 원인 분석 + 자동 피드백 모듈

폐기 이벤트를 자동 분류하고, 원인별 차별화된 발주 조정 피드백을 생성합니다.

원인 분류:
  - OVER_ORDER: 수요 대비 과잉 발주
  - EXPIRY_MISMANAGEMENT: 유통기한 대비 낮은 판매빈도
  - DEMAND_DROP: 외부 요인(날씨/행사종료/트렌드 하락)에 의한 수요 급감
  - MIXED: 복합 또는 불명확
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from src.infrastructure.database.repos import WasteCauseRepository
from src.infrastructure.database.connection import DBRouter
from src.utils.logger import get_logger
from src.settings.constants import (
    WASTE_CAUSE_OVER_ORDER,
    WASTE_CAUSE_EXPIRY_MGMT,
    WASTE_CAUSE_DEMAND_DROP,
    WASTE_CAUSE_MIXED,
    WASTE_FEEDBACK_REDUCE_SAFETY,
    WASTE_FEEDBACK_SUPPRESS,
    WASTE_FEEDBACK_TEMP_REDUCE,
    WASTE_FEEDBACK_DEFAULT,
    WASTE_CAUSE_OVER_ORDER_RATIO,
    WASTE_CAUSE_WASTE_RATIO_HIGH,
    WASTE_CAUSE_DEMAND_DROP_TREND,
    WASTE_CAUSE_DEMAND_DROP_SOLD,
    WASTE_CAUSE_SELL_DAY_LOW,
    WASTE_CAUSE_TEMP_CHANGE_THRESHOLD,
    WASTE_CAUSE_PROMO_ENDED_DAYS,
    WASTE_FEEDBACK_OVER_ORDER_MULT,
    WASTE_FEEDBACK_EXPIRY_MGMT_MULT,
    WASTE_FEEDBACK_DEMAND_DROP_MULT_START,
    WASTE_FEEDBACK_DEMAND_DROP_DECAY_DAYS,
    WASTE_FEEDBACK_OVER_ORDER_EXPIRY_DAYS,
    WASTE_FEEDBACK_EXPIRY_MGMT_EXPIRY_DAYS,
    WASTE_FEEDBACK_DEMAND_DROP_EXPIRY_DAYS,
)

logger = get_logger(__name__)


# =========================================================================
# 데이터 클래스
# =========================================================================

@dataclass
class ClassificationResult:
    """폐기 원인 분류 결과"""
    cause: str
    secondary_cause: Optional[str] = None
    confidence: float = 0.0
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WasteFeedbackResult:
    """피드백 조회 결과 (ImprovedPredictor에서 사용)"""
    multiplier: float = 1.0
    primary_cause: str = ""
    feedback_action: str = WASTE_FEEDBACK_DEFAULT
    confidence: float = 0.0
    has_active_feedback: bool = False


# =========================================================================
# WasteCauseAnalyzer — 폐기 원인 분류기
# =========================================================================

class WasteCauseAnalyzer:
    """폐기 원인 분석 및 피드백 생성"""

    def __init__(self, store_id: str, params: Optional[dict] = None) -> None:
        self.store_id = store_id
        self.params = params or self._load_params()
        self.repo = WasteCauseRepository(store_id=store_id)

    def _load_params(self) -> dict:
        """eval_params.json의 waste_cause 블록 로드"""
        try:
            from pathlib import Path
            config_path = Path(__file__).parent.parent.parent / "config" / "eval_params.json"
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("waste_cause", {})
        except Exception as e:
            logger.debug(f"eval_params.json 로드 실패: {e}")
        return {}

    def analyze_date(self, target_date: str) -> dict:
        """target_date의 폐기 이벤트 전체 분석 후 DB 저장

        Args:
            target_date: 폐기 발생일 (YYYY-MM-DD)
        Returns:
            {"analyzed": N, "by_cause": {cause: count}, "errors": [...]}
        """
        if not self.params.get("enabled", True):
            return {"analyzed": 0, "by_cause": {}, "errors": []}

        events = self._gather_waste_events(target_date)
        if not events:
            return {"analyzed": 0, "by_cause": {}, "errors": []}

        by_cause = {}
        errors = []
        analyzed = 0

        for event in events:
            try:
                item_cd = event["item_cd"]
                mid_cd = event.get("mid_cd", "")
                context = self._gather_context(item_cd, mid_cd, target_date)

                # batch/tracking 소스: order_qty 없으면 initial_qty 폴백
                if context.get("order_qty") is None and event.get("initial_qty"):
                    context["order_qty"] = event["initial_qty"]

                result = self._classify(event, context)
                action, mult, expiry = self._compute_feedback(
                    result.cause, target_date
                )

                record = {
                    "store_id": self.store_id,
                    "analysis_date": datetime.now().strftime("%Y-%m-%d"),
                    "waste_date": target_date,
                    "item_cd": item_cd,
                    "item_nm": event.get("item_nm"),
                    "mid_cd": mid_cd,
                    "waste_qty": event.get("waste_qty", 0),
                    "waste_source": event.get("waste_source", "daily_sales"),
                    "primary_cause": result.cause,
                    "secondary_cause": result.secondary_cause,
                    "confidence": result.confidence,
                    "order_qty": context.get("order_qty"),
                    "daily_avg": context.get("daily_avg"),
                    "predicted_qty": context.get("predicted_qty"),
                    "actual_sold_qty": context.get("actual_sold_qty"),
                    "expiration_days": context.get("expiration_days"),
                    "trend_ratio": context.get("trend_ratio"),
                    "sell_day_ratio": context.get("sell_day_ratio"),
                    "weather_factor": json.dumps(
                        context.get("weather_info", {}), ensure_ascii=False
                    ) if context.get("weather_info") else None,
                    "promo_factor": json.dumps(
                        context.get("promo_info", {}), ensure_ascii=False
                    ) if context.get("promo_info") else None,
                    "holiday_factor": json.dumps(
                        context.get("holiday_info", {}), ensure_ascii=False
                    ) if context.get("holiday_info") else None,
                    "feedback_action": action,
                    "feedback_multiplier": mult,
                    "feedback_expiry_date": expiry,
                }
                self.repo.upsert_cause(record)
                analyzed += 1
                by_cause[result.cause] = by_cause.get(result.cause, 0) + 1

            except Exception as e:
                errors.append(f"{event.get('item_cd', '?')}: {e}")
                logger.debug(f"폐기 원인 분석 실패 ({event.get('item_cd')}): {e}")

        return {"analyzed": analyzed, "by_cause": by_cause, "errors": errors}

    def analyze_range(self, start_date: str, end_date: str) -> dict:
        """기간 소급 분석

        Args:
            start_date: 시작일 (YYYY-MM-DD)
            end_date: 종료일 (YYYY-MM-DD)
        Returns:
            {"total_analyzed": N, "by_date": {date: result}}
        """
        total = 0
        by_date = {}
        current = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            result = self.analyze_date(date_str)
            total += result["analyzed"]
            if result["analyzed"] > 0:
                by_date[date_str] = result
            current += timedelta(days=1)

        return {"total_analyzed": total, "by_date": by_date}

    # -----------------------------------------------------------------
    # 데이터 수집
    # -----------------------------------------------------------------

    def _gather_waste_events(self, target_date: str) -> List[dict]:
        """폐기 이벤트 수집

        기본 소스: daily_sales.disuse_qty > 0 (BGF 공식 보고, 가장 신뢰도 높음)
        보조 소스: inventory_batches, order_tracking (입고량/유통기한 등 컨텍스트 보강용)

        NOTE: inventory_batches/order_tracking의 remaining_qty > 0은
              실제 폐기가 아닌 경우가 97%+ (갱신 지연, 당일 판매 등)이므로
              단독 폐기 판단에 사용하지 않음.
        """
        events_map: Dict[str, dict] = {}

        conn = DBRouter.get_store_connection(self.store_id)
        try:
            # 1) daily_sales — 유일한 폐기 소스 (BGF 공식 보고)
            rows = conn.execute("""
                SELECT item_cd, mid_cd,
                       disuse_qty AS waste_qty,
                       ord_qty AS order_qty_ds,
                       sale_qty
                FROM daily_sales
                WHERE store_id = ? AND sales_date = ? AND disuse_qty > 0
            """, (self.store_id, target_date)).fetchall()
            for r in rows:
                d = dict(r)
                d["waste_source"] = "daily_sales"
                events_map[d["item_cd"]] = d

            # 2) inventory_batches — 컨텍스트 보강 (입고량, 유통기한)
            for item_cd in list(events_map.keys()):
                row = conn.execute("""
                    SELECT initial_qty, remaining_qty, receiving_date, expiration_days
                    FROM inventory_batches
                    WHERE store_id = ? AND item_cd = ?
                      AND expiry_date >= ? AND status IN ('expired', 'active')
                    ORDER BY expiry_date ASC LIMIT 1
                """, (self.store_id, item_cd, target_date)).fetchone()
                if row:
                    events_map[item_cd]["initial_qty"] = row["initial_qty"]
                    events_map[item_cd]["receiving_date"] = row["receiving_date"]
                    if row["expiration_days"]:
                        events_map[item_cd]["batch_expiration_days"] = row["expiration_days"]

            # 3) order_tracking — 컨텍스트 보강 (발주량)
            for item_cd in list(events_map.keys()):
                row = conn.execute("""
                    SELECT order_qty, order_date, expiry_time
                    FROM order_tracking
                    WHERE store_id = ? AND item_cd = ?
                      AND DATE(expiry_time) >= ?
                      AND order_date < DATE(expiry_time)
                    ORDER BY expiry_time ASC LIMIT 1
                """, (self.store_id, item_cd, target_date)).fetchone()
                if row:
                    events_map[item_cd].setdefault("initial_qty", row["order_qty"])
                    events_map[item_cd].setdefault("receiving_date", row["order_date"])
        finally:
            conn.close()

        return list(events_map.values())

    def _gather_context(
        self, item_cd: str, mid_cd: str, waste_date: str
    ) -> dict:
        """분류에 필요한 컨텍스트 데이터 조회"""
        ctx: Dict[str, Any] = {}

        conn = DBRouter.get_store_connection(self.store_id)
        try:
            # eval_outcomes에서 예측/실적 데이터
            row = conn.execute("""
                SELECT daily_avg, predicted_qty, actual_sold_qty,
                       trend_score, stockout_freq, current_stock, pending_qty,
                       order_status
                FROM eval_outcomes
                WHERE store_id = ? AND item_cd = ? AND eval_date = ?
            """, (self.store_id, item_cd, waste_date)).fetchone()
            if row:
                d = dict(row)
                ctx["daily_avg"] = d.get("daily_avg")
                ctx["predicted_qty"] = d.get("predicted_qty")
                ctx["actual_sold_qty"] = d.get("actual_sold_qty")
                ctx["trend_ratio"] = d.get("trend_score")
                ctx["current_stock"] = d.get("current_stock")

            # prediction_logs 폴백 (eval_outcomes에 없는 경우)
            if ctx.get("predicted_qty") is None:
                row = conn.execute("""
                    SELECT predicted_qty, order_qty, current_stock
                    FROM prediction_logs
                    WHERE store_id = ? AND item_cd = ? AND target_date = ?
                    ORDER BY id DESC LIMIT 1
                """, (self.store_id, item_cd, waste_date)).fetchone()
                if row:
                    d = dict(row)
                    ctx.setdefault("predicted_qty", d.get("predicted_qty"))
                    ctx.setdefault("order_qty", d.get("order_qty"))

            # order_qty: daily_sales.ord_qty 폴백
            if ctx.get("order_qty") is None:
                row = conn.execute("""
                    SELECT ord_qty FROM daily_sales
                    WHERE store_id = ? AND item_cd = ? AND sales_date = ?
                """, (self.store_id, item_cd, waste_date)).fetchone()
                if row:
                    ctx["order_qty"] = row["ord_qty"]

            # sell_day_ratio: 최근 30일 중 판매일 비율
            # NOTE: daily_sales에는 판매 발생일만 row가 존재하므로
            #       분모는 len(rows)가 아닌 고정 LOOKBACK_DAYS(30)을 사용
            LOOKBACK_DAYS = 30
            rows = conn.execute("""
                SELECT sale_qty, stock_qty FROM daily_sales
                WHERE store_id = ? AND item_cd = ?
                  AND sales_date >= date(?, '-30 days') AND sales_date <= ?
                ORDER BY sales_date
            """, (self.store_id, item_cd, waste_date, waste_date)).fetchall()
            if rows:
                sell_days = sum(1 for r in rows if r["sale_qty"] and r["sale_qty"] > 0)
                ctx["sell_day_ratio"] = sell_days / LOOKBACK_DAYS
                # daily_avg 폴백
                if ctx.get("daily_avg") is None:
                    total = sum(r["sale_qty"] or 0 for r in rows)
                    ctx["daily_avg"] = total / LOOKBACK_DAYS
            else:
                ctx.setdefault("sell_day_ratio", 0.0)

        finally:
            conn.close()

        # product_details에서 유통기한
        try:
            common_conn = DBRouter.get_common_connection()
            row = common_conn.execute("""
                SELECT expiration_days FROM product_details
                WHERE item_cd = ?
            """, (item_cd,)).fetchone()
            if row:
                ctx["expiration_days"] = row["expiration_days"]
            common_conn.close()
        except Exception as e:
            logger.warning(
                f"product_details 조회 실패 | item_cd={item_cd} | store_id={self.store_id}: {e}"
            )

        # external_factors에서 기온 데이터
        ctx["weather_info"] = self._get_weather_context(waste_date)

        # promotion_changes에서 행사 종료 여부
        ctx["promo_info"] = self._get_promo_context(item_cd, waste_date)

        # holiday 정보
        ctx["holiday_info"] = self._get_holiday_context(waste_date)

        return ctx

    def _get_weather_context(self, waste_date: str) -> Optional[dict]:
        """기온 데이터 조회 (실측 + 전일)"""
        try:
            common_conn = DBRouter.get_common_connection()
            # 당일 기온
            row = common_conn.execute("""
                SELECT factor_value FROM external_factors
                WHERE factor_date = ? AND factor_key IN ('temperature_forecast', 'temperature')
                ORDER BY CASE factor_key
                    WHEN 'temperature_forecast' THEN 1
                    WHEN 'temperature' THEN 2
                END
                LIMIT 1
            """, (waste_date,)).fetchone()
            today_temp = float(row["factor_value"]) if row else None

            # 전일 기온
            prev_date = (datetime.strptime(waste_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            row = common_conn.execute("""
                SELECT factor_value FROM external_factors
                WHERE factor_date = ? AND factor_key IN ('temperature_forecast', 'temperature')
                ORDER BY CASE factor_key
                    WHEN 'temperature_forecast' THEN 1
                    WHEN 'temperature' THEN 2
                END
                LIMIT 1
            """, (prev_date,)).fetchone()
            prev_temp = float(row["factor_value"]) if row else None
            common_conn.close()

            delta = None
            if today_temp is not None and prev_temp is not None:
                delta = today_temp - prev_temp

            return {
                "today_temp": today_temp,
                "prev_temp": prev_temp,
                "delta": delta,
            }
        except Exception as e:
            logger.debug(f"기온 데이터 조회 실패 | waste_date={waste_date}: {e}")
            return None

    def _get_promo_context(self, item_cd: str, waste_date: str) -> Optional[dict]:
        """행사 종료 여부 조회"""
        try:
            promo_days = self.params.get(
                "promo_ended_days", WASTE_CAUSE_PROMO_ENDED_DAYS
            )
            conn = DBRouter.get_store_connection(self.store_id)
            row = conn.execute("""
                SELECT change_type, change_date, prev_promo_type
                FROM promotion_changes
                WHERE store_id = ? AND item_cd = ?
                  AND change_type = 'end'
                  AND change_date BETWEEN date(?, ?) AND ?
                ORDER BY change_date DESC LIMIT 1
            """, (self.store_id, item_cd, waste_date, f"-{promo_days} days", waste_date)).fetchone()
            conn.close()
            if row:
                return {
                    "promo_ended_recently": True,
                    "end_date": row["change_date"],
                    "prev_type": row["prev_promo_type"],
                }
            return {"promo_ended_recently": False}
        except Exception as e:
            logger.debug(
                f"프로모션 컨텍스트 조회 실패 | item_cd={item_cd} | store_id={self.store_id}: {e}"
            )
            return None

    def _get_holiday_context(self, waste_date: str) -> Optional[dict]:
        """휴일 정보 조회"""
        try:
            common_conn = DBRouter.get_common_connection()
            row = common_conn.execute("""
                SELECT factor_value FROM external_factors
                WHERE factor_date = ? AND factor_key = 'is_holiday'
            """, (waste_date,)).fetchone()
            common_conn.close()
            if row:
                return {"is_holiday": row["factor_value"] == "1"}
            return {"is_holiday": False}
        except Exception as e:
            logger.debug(f"휴일 정보 조회 실패 | waste_date={waste_date}: {e}")
            return None

    # -----------------------------------------------------------------
    # 분류 알고리즘
    # -----------------------------------------------------------------

    def _classify(self, event: dict, context: dict) -> ClassificationResult:
        """단일 폐기 이벤트 원인 분류

        우선순위: DEMAND_DROP > OVER_ORDER > EXPIRY_MISMANAGEMENT > MIXED
        """
        scores = {
            WASTE_CAUSE_DEMAND_DROP: 0.0,
            WASTE_CAUSE_OVER_ORDER: 0.0,
            WASTE_CAUSE_EXPIRY_MGMT: 0.0,
        }
        evidence: Dict[str, Any] = {}

        # --- 파라미터 ---
        p = self.params
        over_order_ratio = p.get("over_order_ratio_threshold", WASTE_CAUSE_OVER_ORDER_RATIO)
        waste_ratio_high = p.get("waste_ratio_high", WASTE_CAUSE_WASTE_RATIO_HIGH)
        demand_trend_th = p.get("demand_drop_trend_threshold", WASTE_CAUSE_DEMAND_DROP_TREND)
        demand_sold_th = p.get("demand_drop_sold_threshold", WASTE_CAUSE_DEMAND_DROP_SOLD)
        sell_day_low = p.get("sell_day_ratio_low", WASTE_CAUSE_SELL_DAY_LOW)
        temp_change_th = p.get("temp_change_threshold", WASTE_CAUSE_TEMP_CHANGE_THRESHOLD)

        daily_avg = context.get("daily_avg") or 0.0
        order_qty = context.get("order_qty") or event.get("order_qty_ds") or 0
        predicted_qty = context.get("predicted_qty") or 0
        actual_sold = context.get("actual_sold_qty")
        trend_ratio = context.get("trend_ratio")
        sell_day_ratio = context.get("sell_day_ratio")
        exp_days = context.get("expiration_days") or 1

        waste_qty = event.get("waste_qty", 0)
        initial_qty = event.get("initial_qty", 0)

        weather = context.get("weather_info") or {}
        weather_delta = weather.get("delta")

        promo = context.get("promo_info") or {}
        promo_ended = promo.get("promo_ended_recently", False)

        # -------------------------------------------------------
        # 1) DEMAND_DROP 판별 (외부 요인 먼저)
        # -------------------------------------------------------
        demand_drop_signals = 0

        # 트렌드 급하락
        if trend_ratio is not None and trend_ratio < demand_trend_th:
            demand_drop_signals += 1
            evidence["trend_drop"] = trend_ratio

        # 기온 급변
        if weather_delta is not None and abs(weather_delta) >= temp_change_th:
            demand_drop_signals += 1
            evidence["weather_delta"] = weather_delta

        # 행사 종료
        if promo_ended:
            demand_drop_signals += 1
            evidence["promo_ended"] = True

        # 실제 판매량이 예측 대비 크게 미달
        sold_below = False
        if actual_sold is not None and predicted_qty and predicted_qty > 0:
            sold_ratio = actual_sold / predicted_qty
            if sold_ratio < demand_sold_th:
                sold_below = True
                evidence["sold_vs_predicted"] = round(sold_ratio, 2)

        if demand_drop_signals >= 1 and sold_below:
            scores[WASTE_CAUSE_DEMAND_DROP] = 0.6 + min(0.3, demand_drop_signals * 0.15)
        elif demand_drop_signals >= 2:
            scores[WASTE_CAUSE_DEMAND_DROP] = 0.5

        # -------------------------------------------------------
        # 2) OVER_ORDER 판별
        # -------------------------------------------------------
        if daily_avg > 0 and order_qty > 0:
            consumption_capacity = daily_avg * max(exp_days, 1)
            ratio = order_qty / consumption_capacity
            evidence["over_order_ratio"] = round(ratio, 2)
            if ratio > over_order_ratio:
                scores[WASTE_CAUSE_OVER_ORDER] = min(1.0, 0.5 + (ratio - over_order_ratio) * 0.3)

        # -------------------------------------------------------
        # 3) EXPIRY_MISMANAGEMENT 판별
        # -------------------------------------------------------
        expiry_signals = 0

        if sell_day_ratio is not None and sell_day_ratio < sell_day_low:
            expiry_signals += 1
            evidence["low_sell_day_ratio"] = round(sell_day_ratio, 2)

        if initial_qty and initial_qty > 0 and waste_qty > 0:
            waste_ratio = waste_qty / initial_qty
            evidence["waste_ratio"] = round(waste_ratio, 2)
            if waste_ratio > waste_ratio_high:
                expiry_signals += 1

        if expiry_signals >= 1:
            scores[WASTE_CAUSE_EXPIRY_MGMT] = 0.4 + expiry_signals * 0.2

        # -------------------------------------------------------
        # 최종 판정
        # -------------------------------------------------------
        # 가장 높은 점수의 원인 선택
        max_cause = max(scores, key=scores.get)
        max_score = scores[max_cause]

        # 두 번째 원인
        sorted_causes = sorted(scores.items(), key=lambda x: -x[1])
        secondary = None
        if len(sorted_causes) >= 2 and sorted_causes[1][1] > 0.3:
            secondary = sorted_causes[1][0]

        # 최소 신뢰도 미달 → MIXED
        if max_score < 0.4:
            return ClassificationResult(
                cause=WASTE_CAUSE_MIXED,
                secondary_cause=max_cause if max_score > 0 else None,
                confidence=max(0.3, max_score),
                evidence=evidence,
            )

        return ClassificationResult(
            cause=max_cause,
            secondary_cause=secondary,
            confidence=min(1.0, max_score),
            evidence=evidence,
        )

    # -----------------------------------------------------------------
    # 피드백 계산
    # -----------------------------------------------------------------

    def _compute_feedback(
        self, cause: str, waste_date: str
    ) -> tuple:
        """원인별 피드백 액션/승수/만료일 계산

        Returns:
            (feedback_action, feedback_multiplier, feedback_expiry_date)
        """
        fb_mults = self.params.get("feedback_multipliers", {})
        fb_expiry = self.params.get("feedback_expiry_days", {})
        waste_dt = datetime.strptime(waste_date, "%Y-%m-%d")

        if cause == WASTE_CAUSE_OVER_ORDER:
            mult = fb_mults.get("OVER_ORDER", WASTE_FEEDBACK_OVER_ORDER_MULT)
            days = fb_expiry.get("OVER_ORDER", WASTE_FEEDBACK_OVER_ORDER_EXPIRY_DAYS)
            expiry = (waste_dt + timedelta(days=days)).strftime("%Y-%m-%d")
            return WASTE_FEEDBACK_REDUCE_SAFETY, mult, expiry

        elif cause == WASTE_CAUSE_EXPIRY_MGMT:
            mult = fb_mults.get("EXPIRY_MISMANAGEMENT", WASTE_FEEDBACK_EXPIRY_MGMT_MULT)
            days = fb_expiry.get("EXPIRY_MISMANAGEMENT", WASTE_FEEDBACK_EXPIRY_MGMT_EXPIRY_DAYS)
            expiry = (waste_dt + timedelta(days=days)).strftime("%Y-%m-%d")
            return WASTE_FEEDBACK_SUPPRESS, mult, expiry

        elif cause == WASTE_CAUSE_DEMAND_DROP:
            mult = fb_mults.get("DEMAND_DROP_START", WASTE_FEEDBACK_DEMAND_DROP_MULT_START)
            days = fb_expiry.get("DEMAND_DROP", WASTE_FEEDBACK_DEMAND_DROP_EXPIRY_DAYS)
            expiry = (waste_dt + timedelta(days=days)).strftime("%Y-%m-%d")
            return WASTE_FEEDBACK_TEMP_REDUCE, mult, expiry

        else:
            # MIXED → DEFAULT (기존 disuse_coef에 위임)
            expiry = (waste_dt + timedelta(days=7)).strftime("%Y-%m-%d")
            return WASTE_FEEDBACK_DEFAULT, 1.0, expiry


# =========================================================================
# WasteFeedbackAdjuster — 피드백 조회기 (ImprovedPredictor에서 사용)
# =========================================================================

class WasteFeedbackAdjuster:
    """폐기 원인 기반 발주 피드백 조회

    ImprovedPredictor의 predict_item() 12-2 단계에서 호출하여
    미만료 피드백의 승수를 반환합니다.
    """

    def __init__(self, store_id: str, params: Optional[dict] = None) -> None:
        self.store_id = store_id
        self.params = params or self._load_params()
        self.repo = WasteCauseRepository(store_id=store_id)
        self._cache: Dict[str, WasteFeedbackResult] = {}
        self._preloaded = False

    def _load_params(self) -> dict:
        """eval_params.json의 waste_cause 블록 로드"""
        try:
            from pathlib import Path
            config_path = Path(__file__).parent.parent.parent / "config" / "eval_params.json"
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("waste_cause", {})
        except Exception as e:
            logger.warning(f"WasteFeedbackAdjuster eval_params.json 로드 실패: {e}")
        return {}

    @property
    def enabled(self) -> bool:
        return self.params.get("enabled", True)

    def get_adjustment(self, item_cd: str, mid_cd: str = "") -> WasteFeedbackResult:
        """상품의 활성 피드백 승수 조회

        Args:
            item_cd: 상품 코드
            mid_cd: 중분류 코드 (현재 미사용, 확장용)
        Returns:
            WasteFeedbackResult
        """
        if not self.enabled:
            return WasteFeedbackResult()

        # 캐시 히트
        if item_cd in self._cache:
            return self._cache[item_cd]

        # 프리로드 완료 + 캐시 미스 → 피드백 없음
        if self._preloaded:
            result = WasteFeedbackResult()
            self._cache[item_cd] = result
            return result

        # 개별 조회
        today = datetime.now().strftime("%Y-%m-%d")
        fb = self.repo.get_active_feedback(item_cd, today, store_id=self.store_id)
        result = self._build_result(fb, today)
        self._cache[item_cd] = result
        return result

    def preload(self, item_cds: List[str]) -> None:
        """여러 상품의 피드백을 일괄 프리로드 (DB 쿼리 최소화)

        Args:
            item_cds: 상품 코드 목록
        """
        if not self.enabled or not item_cds:
            self._preloaded = True
            return

        today = datetime.now().strftime("%Y-%m-%d")
        feedbacks = self.repo.get_active_feedbacks_batch(
            item_cds, today, store_id=self.store_id
        )

        for ic in item_cds:
            fb = feedbacks.get(ic)
            self._cache[ic] = self._build_result(fb, today)

        self._preloaded = True

    def _build_result(
        self, fb: Optional[dict], as_of_date: str
    ) -> WasteFeedbackResult:
        """DB 레코드 → WasteFeedbackResult 변환 (시간 감쇄 적용)"""
        if not fb:
            return WasteFeedbackResult()

        multiplier = fb.get("feedback_multiplier", 1.0)
        cause = fb.get("primary_cause", WASTE_CAUSE_MIXED)
        action = fb.get("feedback_action", WASTE_FEEDBACK_DEFAULT)
        confidence = fb.get("confidence", 0.0)

        # DEMAND_DROP: 시간 감쇄 적용
        if cause == WASTE_CAUSE_DEMAND_DROP and multiplier < 1.0:
            fb_mults = self.params.get("feedback_multipliers", {})
            decay_days = int(fb_mults.get(
                "DEMAND_DROP_DECAY_DAYS",
                WASTE_FEEDBACK_DEMAND_DROP_DECAY_DAYS,
            ))
            multiplier = self._apply_demand_drop_decay(
                multiplier, fb["waste_date"], as_of_date, decay_days
            )

        return WasteFeedbackResult(
            multiplier=multiplier,
            primary_cause=cause,
            feedback_action=action,
            confidence=confidence,
            has_active_feedback=multiplier < 1.0,
        )

    @staticmethod
    def _apply_demand_drop_decay(
        base_multiplier: float,
        waste_date: str,
        as_of_date: str,
        decay_days: int,
    ) -> float:
        """DEMAND_DROP 시간 감쇄: waste_date부터 decay_days에 걸쳐 1.0으로 회복

        Args:
            base_multiplier: 시작 승수 (예: 0.80)
            waste_date: 폐기 발생일
            as_of_date: 현재 날짜
            decay_days: 감쇄 기간 (일)
        Returns:
            감쇄 적용된 승수 (base_multiplier ~ 1.0)
        """
        if decay_days <= 0:
            return 1.0
        try:
            waste_dt = datetime.strptime(waste_date, "%Y-%m-%d")
            as_of_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
            days_since = (as_of_dt - waste_dt).days
        except ValueError:
            return base_multiplier

        if days_since >= decay_days:
            return 1.0
        if days_since <= 0:
            return base_multiplier

        factor = days_since / decay_days
        return base_multiplier + (1.0 - base_multiplier) * factor
