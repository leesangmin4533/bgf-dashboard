"""
사전 발주 평가 보정기 (EvalCalibrator)

1. 사후 검증: 어제 결정이 맞았는지 오늘 데이터로 판정
2. 적중률 계산: 결정별 / 전체 적중률
3. 자동 보정: 적중률 기반 파라미터 조정
   - 인기도 가중치: 상관계수 비례 재배분
   - 노출시간 임계값: SKIP 후 품절 분포 기반
   - 품절빈도 임계값: 업그레이드 적중률 기반
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.utils.logger import get_logger
from src.infrastructure.database.base_repository import BaseRepository
from src.infrastructure.database.repos import (
    EvalOutcomeRepository,
    CalibrationRepository,
)
from src.prediction.eval_config import EvalConfig, ParamSpec
from src.settings.constants import FOOD_MID_CODES, MAX_PARAMS_PER_CALIBRATION

logger = get_logger(__name__)

# 최소 샘플 수 (이보다 적으면 보정 스킵)
MIN_SAMPLES_FOR_CALIBRATION = 50


class EvalCalibrator:
    """
    사전 발주 평가 보정기

    매일 실행:
      1) 어제 평가 결과에 대해 오늘 데이터로 사후 검증
      2) 누적 적중률 계산
      3) 충분한 데이터가 쌓이면 파라미터 자동 보정
    """

    def __init__(self, config: Optional[EvalConfig] = None, store_id: Optional[str] = None) -> None:
        self.config = config or EvalConfig.load()
        self.store_id = store_id
        self.outcome_repo = EvalOutcomeRepository(store_id=self.store_id)
        self.calibration_repo = CalibrationRepository(store_id=self.store_id)
        self._sales_repo = None  # lazy import

    def _get_sales_repo(self) -> Any:
        """SalesRepository lazy 로드"""
        if self._sales_repo is None:
            from src.infrastructure.database.repos import SalesRepository
            self._sales_repo = SalesRepository(store_id=self.store_id)
        return self._sales_repo

    def _apply_mean_reversion(self, param_name: str, raw_target: float) -> float:
        """Mean reversion이 적용된 보정 목표값 계산

        raw_target과 현재값의 차이에 decay를 적용하고,
        기본값 방향으로 reversion_rate만큼 복원력을 추가한다.

        Args:
            param_name: 파라미터 이름
            raw_target: 보정이 원하는 목표값

        Returns:
            mean reversion이 적용된 최종 목표값
        """
        spec: ParamSpec = getattr(self.config, param_name)
        decay = self.config.calibration_decay.value
        reversion_rate = self.config.calibration_reversion_rate.value

        raw_delta = raw_target - spec.value
        reversion = (spec.default - spec.value) * reversion_rate
        effective_delta = raw_delta * decay + reversion

        return spec.value + effective_delta

    # =========================================================================
    # 1단계: 평가 결과 저장
    # =========================================================================

    def save_eval_results(self, eval_results: Dict[str, Any]) -> int:
        """PreOrderEvaluator 결과를 eval_outcomes 테이블에 저장 (ML 컬럼 포함)

        Args:
            eval_results: {item_cd: PreOrderEvalResult, ...}

        Returns:
            저장 건수
        """
        if not eval_results:
            return 0

        today = datetime.now().strftime("%Y-%m-%d")
        weekday = datetime.now().weekday()

        # ML 부가 데이터 배치 조회
        item_codes = list(eval_results.keys())
        margin_map = self._batch_load_margin_data(item_codes)
        promo_map = self._batch_load_promo_data(item_codes)

        batch = []
        for item_cd, r in eval_results.items():
            # 배송 차수: 푸드류만 상품명으로 판별
            delivery_batch = None
            if r.mid_cd in FOOD_MID_CODES and r.item_nm:
                last_char = r.item_nm.strip()[-1] if r.item_nm.strip() else ""
                if last_char == "1":
                    delivery_batch = "1차"
                elif last_char == "2":
                    delivery_batch = "2차"

            margin_info = margin_map.get(item_cd, {})
            batch.append({
                "item_cd": item_cd,
                "mid_cd": r.mid_cd,
                "decision": r.decision.value,
                "exposure_days": r.exposure_days,
                "popularity_score": r.popularity_score,
                "daily_avg": r.daily_avg,
                "current_stock": r.current_stock,
                "pending_qty": r.pending_qty,
                "weekday": weekday,
                "delivery_batch": delivery_batch,
                "sell_price": margin_info.get("sell_price"),
                "margin_rate": margin_info.get("margin_rate"),
                "promo_type": promo_map.get(item_cd),
                "trend_score": r.trend_score,
                "stockout_freq": r.stockout_frequency,
            })

        count = self.outcome_repo.save_eval_results_batch(today, batch, store_id=self.store_id)
        logger.info(f"평가 결과 저장: {count}건 ({today})")
        return count

    def _batch_load_margin_data(self, item_codes: List[str]) -> Dict[str, Dict]:
        """product_details에서 sell_price, margin_rate 배치 조회"""
        if not item_codes:
            return {}
        try:
            from src.infrastructure.database.repos import ProductDetailRepository
            repo = ProductDetailRepository()
            conn = repo._get_conn()
            try:
                cursor = conn.cursor()
                result = {}
                # 100개씩 배치 조회
                for i in range(0, len(item_codes), 100):
                    chunk = item_codes[i:i + 100]
                    placeholders = ','.join('?' * len(chunk))
                    cursor.execute(
                        f"""
                        SELECT item_cd, sell_price, margin_rate
                        FROM product_details
                        WHERE item_cd IN ({placeholders})
                        """,
                        chunk
                    )
                    for row in cursor.fetchall():
                        result[row[0]] = {
                            "sell_price": row[1],
                            "margin_rate": row[2],
                        }
                return result
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"매가/이익율 배치 조회 실패: {e}")
            return {}

    def _batch_load_promo_data(self, item_codes: List[str]) -> Dict[str, str]:
        """promotions 테이블에서 현재 활성 행사 타입 배치 조회"""
        if not item_codes:
            return {}
        try:
            from src.infrastructure.database.repos import PromotionRepository
            repo = PromotionRepository(store_id=self.store_id)
            conn = repo._get_conn()
            try:
                cursor = conn.cursor()
                today = datetime.now().strftime("%Y-%m-%d")
                result = {}
                for i in range(0, len(item_codes), 100):
                    chunk = item_codes[i:i + 100]
                    placeholders = ','.join('?' * len(chunk))
                    cursor.execute(
                        f"""
                        SELECT item_cd, promo_type
                        FROM promotions
                        WHERE item_cd IN ({placeholders})
                          AND start_date <= ?
                          AND end_date >= ?
                          AND is_active = 1
                        """,
                        (*chunk, today, today)
                    )
                    for row in cursor.fetchall():
                        result[row[0]] = row[1]
                return result
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"행사 타입 배치 조회 실패: {e}")
            return {}

    # =========================================================================
    # 2단계: 사후 검증
    # =========================================================================

    def verify_yesterday(self) -> Dict[str, int]:
        """
        어제 결정을 오늘 데이터로 사후 검증

        판정 기준:
        - FORCE_ORDER: 오늘 판매>0 → CORRECT, 판매=0+재고증가 → OVER_ORDER
        - URGENT_ORDER: 재고 1일 내 소진 → CORRECT, 2일+ 여유 → OVER_ORDER
        - NORMAL_ORDER: 정상 판매 → CORRECT, 품절 → UNDER_ORDER
        - PASS: 품절 미발생 → CORRECT, 품절 → UNDER_ORDER
        - SKIP: 품절 미발생 → CORRECT, 품절 → MISS

        Returns:
            {"verified": N, "correct": N, "under": N, "over": N, "miss": N}
        """
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")

        # 어제 미검증 결과 조회
        unverified = self.outcome_repo.get_unverified(yesterday, store_id=self.store_id)
        if not unverified:
            logger.info(f"사후 검증 대상 없음 ({yesterday})")
            return {"verified": 0, "correct": 0, "under": 0, "over": 0, "miss": 0}

        logger.info(f"사후 검증 시작: {len(unverified)}건 ({yesterday})")

        # 오늘 판매 데이터 일괄 조회
        today_sales = self._get_sales_map(today)
        yesterday_sales = self._get_sales_map(yesterday)

        stats = {"verified": 0, "correct": 0, "under": 0, "over": 0, "miss": 0}

        for record in unverified:
            item_cd = record["item_cd"]
            decision = record["decision"]

            # 실제 판매량 (평가일 = 어제)
            actual_sold = yesterday_sales.get(item_cd, {}).get("sale_qty", 0)

            # 오늘(다음날) 재고
            next_day_stock = today_sales.get(item_cd, {}).get("stock_qty", None)
            if next_day_stock is None:
                # 오늘 데이터 없으면 어제 재고 - 판매로 추정
                prev_stock = record.get("current_stock", 0) or 0
                next_day_stock = max(0, prev_stock - actual_sold)

            was_stockout = next_day_stock <= 0
            disuse_qty = yesterday_sales.get(item_cd, {}).get("disuse_qty", 0)
            was_waste = disuse_qty > 0

            # 판정
            outcome = self._judge_outcome(decision, actual_sold, next_day_stock, was_stockout, record)

            # DB 업데이트
            self.outcome_repo.update_outcome(
                eval_date=yesterday,
                item_cd=item_cd,
                actual_sold_qty=actual_sold,
                next_day_stock=next_day_stock,
                was_stockout=was_stockout,
                was_waste=was_waste,
                outcome=outcome,
                disuse_qty=disuse_qty if disuse_qty > 0 else None,
                store_id=self.store_id
            )

            stats["verified"] += 1
            if outcome == "CORRECT":
                stats["correct"] += 1
            elif outcome == "UNDER_ORDER":
                stats["under"] += 1
            elif outcome == "OVER_ORDER":
                stats["over"] += 1
            elif outcome == "MISS":
                stats["miss"] += 1

        accuracy = stats["correct"] / stats["verified"] if stats["verified"] > 0 else 0
        logger.info(
            f"사후 검증 완료: {stats['verified']}건 | "
            f"적중={stats['correct']} 과소={stats['under']} "
            f"과잉={stats['over']} 미스={stats['miss']} | "
            f"적중률={accuracy:.1%}"
        )

        return stats

    def backfill_verification(self, lookback_days: int = 7) -> Dict[str, int]:
        """
        놓친 날의 eval_outcomes를 소급 검증합니다.

        스케줄러 미실행, 수집 실패 등으로 verify_yesterday()가 호출되지 않은
        날의 outcome=NULL 레코드를 일괄 검증합니다.

        Returns:
            {"backfilled": N, "correct": N, "under": N, "over": N, "miss": N}
        """
        today = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

        # 미검증 레코드 전체 조회 (여러 날짜)
        unverified = self.outcome_repo.get_unverified_range(
            start_date=start_date,
            end_date=today,
            store_id=self.store_id,
        )

        if not unverified:
            logger.info(f"소급 검증 대상 없음 ({start_date} ~ {today})")
            return {"backfilled": 0, "correct": 0, "under": 0, "over": 0, "miss": 0}

        # 날짜별로 그룹화
        by_date = {}
        for record in unverified:
            eval_date = record["eval_date"]
            by_date.setdefault(eval_date, []).append(record)

        logger.info(f"소급 검증 시작: {len(unverified)}건, {len(by_date)}일치")

        stats = {"backfilled": 0, "correct": 0, "under": 0, "over": 0, "miss": 0}

        for eval_date, records in sorted(by_date.items()):
            # eval_date의 다음날 데이터로 검증
            next_date = (datetime.strptime(eval_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            eval_day_sales = self._get_sales_map(eval_date)
            next_day_sales = self._get_sales_map(next_date)

            # 벌크 업데이트 데이터 수집
            updates = []
            verified_count = 0
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for record in records:
                item_cd = record["item_cd"]
                decision = record["decision"]

                actual_sold = eval_day_sales.get(item_cd, {}).get("sale_qty", 0)

                next_day_stock = next_day_sales.get(item_cd, {}).get("stock_qty", None)
                if next_day_stock is None:
                    prev_stock = record.get("current_stock", 0) or 0
                    next_day_stock = max(0, prev_stock - actual_sold)

                was_stockout = next_day_stock <= 0
                disuse_qty = eval_day_sales.get(item_cd, {}).get("disuse_qty", 0)
                was_waste = disuse_qty > 0

                outcome = self._judge_outcome(decision, actual_sold, next_day_stock, was_stockout, record)

                updates.append((
                    actual_sold, next_day_stock,
                    1 if was_stockout else 0, 1 if was_waste else 0,
                    outcome, disuse_qty if disuse_qty > 0 else None,
                    now_str,
                    eval_date, item_cd,
                ))

                stats["backfilled"] += 1
                verified_count += 1
                if outcome == "CORRECT":
                    stats["correct"] += 1
                elif outcome == "UNDER_ORDER":
                    stats["under"] += 1
                elif outcome == "OVER_ORDER":
                    stats["over"] += 1
                elif outcome == "MISS":
                    stats["miss"] += 1

            # 날짜 단위 벌크 커밋
            if updates:
                self.outcome_repo.bulk_update_outcomes(updates, store_id=self.store_id)

            logger.info(f"  소급 검증 {eval_date}: {verified_count}건")

        accuracy = stats["correct"] / stats["backfilled"] if stats["backfilled"] > 0 else 0
        logger.info(
            f"소급 검증 완료: {stats['backfilled']}건 | "
            f"적중={stats['correct']} 과소={stats['under']} "
            f"과잉={stats['over']} 미스={stats['miss']} | "
            f"적중률={accuracy:.1%}"
        )

        return stats

    def _judge_outcome(
        self, decision: str, actual_sold: int, next_day_stock: int,
        was_stockout: bool, record: Dict[str, Any]
    ) -> str:
        """결정에 대한 사후 판정"""
        if decision == "FORCE_ORDER":
            # 품절 상품에 강제 발주 → 실제로 팔렸으면 적중
            if actual_sold > 0:
                return "CORRECT"
            return "OVER_ORDER"

        elif decision == "URGENT_ORDER":
            # 긴급 발주 → 재고 빨리 소진됐으면 적중
            if was_stockout or actual_sold > 0:
                return "CORRECT"
            return "OVER_ORDER"

        elif decision == "NORMAL_ORDER":
            # 일반 발주 → 판매 발생 + 재고 감소면 적중
            if actual_sold > 0:
                return "CORRECT"
            if was_stockout:
                return "UNDER_ORDER"
            return "OVER_ORDER"

        elif decision == "PASS":
            # 안전재고 위임 → 품절 안 났으면 적중
            if not was_stockout:
                return "CORRECT"
            return "UNDER_ORDER"

        elif decision == "SKIP":
            # 발주 스킵 → 품절 안 났으면 적중, 품절 → 미스
            if not was_stockout:
                return "CORRECT"
            return "MISS"

        return "CORRECT"

    def _get_sales_map(self, date: str) -> Dict[str, Dict[str, Any]]:
        """특정 날짜의 판매 데이터를 item_cd 기준 dict로 반환"""
        repo = self._get_sales_repo()
        rows = repo.get_daily_sales(date)
        return {row["item_cd"]: row for row in rows}

    # =========================================================================
    # 3단계: 자동 보정
    # =========================================================================

    def calibrate(self, min_samples: int = MIN_SAMPLES_FOR_CALIBRATION) -> Dict[str, Any]:
        """
        누적 데이터 기반 파라미터 자동 보정

        30일 누적 적중률을 분석하여:
        a) 인기도 가중치 → 상관계수 비례 재배분
        b) 노출시간 임계값 → SKIP 후 품절 분포 기반
        c) 품절빈도 임계값 → 업그레이드 적중률 기반

        Args:
            min_samples: 최소 샘플 수 (미만이면 보정 스킵)

        Returns:
            {"calibrated": bool, "changes": [...], "accuracy": {...}}
        """
        today = datetime.now().strftime("%Y-%m-%d")
        accuracy = self.outcome_repo.get_accuracy_stats(days=30)
        total = accuracy.get("total_verified", 0)

        if total < min_samples:
            logger.info(f"보정 스킵: 샘플 부족 ({total}/{min_samples})")
            return {
                "calibrated": False,
                "reason": f"샘플 부족 ({total}/{min_samples})",
                "accuracy": accuracy,
                "changes": [],
            }

        logger.info(f"자동 보정 시작: {total}건 데이터, 전체 적중률={accuracy['overall_accuracy']:.1%}")

        changes = []

        # a) 인기도 가중치 보정 (상관계수 비례)
        weight_changes = self._calibrate_popularity_weights(today, accuracy)
        changes.extend(weight_changes)

        # b) 노출시간 임계값 보정 (MAX_PARAMS_PER_CALIBRATION 제한)
        if len(changes) < MAX_PARAMS_PER_CALIBRATION:
            exposure_changes = self._calibrate_exposure_thresholds(today, accuracy)
            remaining = MAX_PARAMS_PER_CALIBRATION - len(changes)
            changes.extend(exposure_changes[:remaining])

        # c) 품절빈도 임계값 보정 (MAX_PARAMS_PER_CALIBRATION 제한)
        if len(changes) < MAX_PARAMS_PER_CALIBRATION:
            stockout_changes = self._calibrate_stockout_threshold(today, accuracy)
            remaining = MAX_PARAMS_PER_CALIBRATION - len(changes)
            changes.extend(stockout_changes[:remaining])

        if len(changes) >= MAX_PARAMS_PER_CALIBRATION:
            logger.info(f"보정 상한 도달: {MAX_PARAMS_PER_CALIBRATION}개까지만 변경")

        # 변경 사항 저장
        if changes:
            self.config.normalize_weights()
            self.config.save()
            logger.info(f"보정 완료: {len(changes)}개 파라미터 변경")
        else:
            logger.info("보정 완료: 변경 없음")

        return {
            "calibrated": len(changes) > 0,
            "changes": changes,
            "accuracy": accuracy,
        }

    def _calibrate_popularity_weights(
        self, today: str, accuracy: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        인기도 가중치 보정 (수요 지표 3개)

        일평균↔실제판매, 인기도↔실제판매 상관계수를 계산하여
        3개 가중치(daily_avg, sell_day_ratio, trend)를 비례 재배분
        """
        changes = []
        data = self.outcome_repo.get_correlation_data(days=30)
        if len(data) < MIN_SAMPLES_FOR_CALIBRATION:
            return changes

        # 일평균 ↔ 실제판매 상관
        daily_avgs = [d["daily_avg"] for d in data if d["actual_sold_qty"] is not None]
        actuals = [d["actual_sold_qty"] for d in data if d["actual_sold_qty"] is not None]

        if len(daily_avgs) < MIN_SAMPLES_FOR_CALIBRATION:
            return changes

        # 단순 상관계수 계산
        corr_daily = self._pearson_correlation(daily_avgs, actuals)

        # popularity_score ↔ actual_sold_qty
        pop_scores = [d["popularity_score"] for d in data if d["actual_sold_qty"] is not None]
        corr_pop = self._pearson_correlation(pop_scores, actuals)

        if corr_daily is None or corr_pop is None:
            return changes

        # 수요 지표 3개 가중치 재배분
        abs_corr_daily = abs(corr_daily)
        abs_corr_pop = abs(corr_pop)

        # daily_avg 상관 → daily_avg 가중치, pop 상관 → sell_day_ratio 가중치
        total_corr = abs_corr_daily + abs_corr_pop + 0.01  # 0 방지

        target_w_daily = abs_corr_daily / total_corr
        target_w_sell = abs_corr_pop / total_corr * 0.8
        target_w_trend = max(0.10, 1.0 - target_w_daily - target_w_sell)
        # 합계 1.0 정규화
        w_sum = target_w_daily + target_w_sell + target_w_trend
        target_w_daily /= w_sum
        target_w_sell /= w_sum
        target_w_trend /= w_sum

        # 현재값과 목표의 차이가 0.05 이상일 때만 조정
        current_weights = self.config.get_popularity_weights()
        param_map = {
            "weight_daily_avg": target_w_daily,
            "weight_sell_day_ratio": target_w_sell,
            "weight_trend": max(target_w_trend, 0.10),
        }

        for param_name, target in param_map.items():
            current = current_weights.get(param_name.replace("weight_", ""), 0)
            if abs(target - current) >= 0.05:
                old_val = getattr(self.config, param_name).value
                reverted_target = self._apply_mean_reversion(param_name, target)
                new_val = self.config.update_param(param_name, reverted_target)
                if new_val is not None and abs(new_val - old_val) > 1e-6:
                    change = {
                        "param": param_name,
                        "old": round(old_val, 4),
                        "new": round(new_val, 4),
                        "reason": f"상관계수 기반 (daily={corr_daily:.3f}, pop={corr_pop:.3f})",
                    }
                    changes.append(change)
                    self.calibration_repo.save_calibration(
                        calibration_date=today,
                        param_name=param_name,
                        old_value=old_val,
                        new_value=new_val,
                        reason=change["reason"],
                        accuracy_before=accuracy.get("overall_accuracy"),
                        sample_size=len(data),
                    )

        return changes

    def _calibrate_exposure_thresholds(
        self, today: str, accuracy: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        노출시간 임계값 보정

        SKIP 결정 후 품절이 발생한 상품들의 노출일수 분포를 보고:
        - 품절 발생 건의 P90 노출일 > 현재 sufficient 임계값이면 상향
        - FORCE/URGENT 후 과잉이 많으면 임계값 하향
        """
        changes = []
        skip_stats = self.outcome_repo.get_skip_stockout_stats(days=30)

        total_skip = skip_stats.get("total_skip", 0)
        if total_skip < 20:
            return changes

        miss_rate = skip_stats.get("miss_rate", 0)
        min_exposure = skip_stats.get("min_exposure_days")

        # SKIP 후 품절률이 높으면 (>15%) sufficient 임계값 상향
        if miss_rate > 0.15 and min_exposure is not None:
            current_sufficient = self.config.exposure_sufficient.value
            # 품절 발생한 최소 노출일 + 1일을 새 임계값으로
            suggested = min_exposure + 1.0
            if suggested > current_sufficient:
                old_val = current_sufficient
                reverted = self._apply_mean_reversion("exposure_sufficient", suggested)
                new_val = self.config.update_param("exposure_sufficient", reverted)
                if new_val is not None and abs(new_val - old_val) > 0.01:
                    change = {
                        "param": "exposure_sufficient",
                        "old": round(old_val, 2),
                        "new": round(new_val, 2),
                        "reason": f"SKIP 후 품절률={miss_rate:.1%}, 최소노출={min_exposure:.1f}일",
                    }
                    changes.append(change)
                    self.calibration_repo.save_calibration(
                        calibration_date=today,
                        param_name="exposure_sufficient",
                        old_value=old_val,
                        new_value=new_val,
                        reason=change["reason"],
                        accuracy_before=accuracy.get("overall_accuracy"),
                        sample_size=total_skip,
                    )

        # SKIP 후 품절률이 낮으면 (<5%) sufficient 임계값 하향 시도
        elif miss_rate < 0.05 and total_skip >= 30:
            current_sufficient = self.config.exposure_sufficient.value
            suggested = current_sufficient - 0.3
            if suggested >= self.config.exposure_sufficient.min_val:
                old_val = current_sufficient
                reverted = self._apply_mean_reversion("exposure_sufficient", suggested)
                new_val = self.config.update_param("exposure_sufficient", reverted)
                if new_val is not None and abs(new_val - old_val) > 0.01:
                    change = {
                        "param": "exposure_sufficient",
                        "old": round(old_val, 2),
                        "new": round(new_val, 2),
                        "reason": f"SKIP 후 품절률 낮음={miss_rate:.1%} → 기준 완화",
                    }
                    changes.append(change)
                    self.calibration_repo.save_calibration(
                        calibration_date=today,
                        param_name="exposure_sufficient",
                        old_value=old_val,
                        new_value=new_val,
                        reason=change["reason"],
                        accuracy_before=accuracy.get("overall_accuracy"),
                        sample_size=total_skip,
                    )

        return changes

    def _calibrate_stockout_threshold(
        self, today: str, accuracy: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        품절빈도 임계값 보정

        업그레이드(URGENT/NORMAL)의 적중률을 기준으로:
        - 적중률 > 목표 → 현재값 유지 또는 약간 완화
        - 적중률 < 50% → 임계값 하향 (더 엄격하게)
        """
        changes = []
        upgrade_stats = self.outcome_repo.get_upgrade_stats(days=30)

        total = upgrade_stats.get("total_upgraded", 0)
        if total < 20:
            return changes

        upgrade_accuracy = upgrade_stats.get("accuracy", 0)
        target = self.config.target_accuracy.value
        current_threshold = self.config.stockout_freq_threshold.value

        if upgrade_accuracy < 0.50:
            # 적중률 매우 낮음 → 임계값 상향 (더 엄격 = 업그레이드 발동 줄임)
            suggested = current_threshold + 0.02  # 15% → 17% (기준 높이면 발동 적어짐)
            old_val = current_threshold
            reverted = self._apply_mean_reversion("stockout_freq_threshold", suggested)
            new_val = self.config.update_param("stockout_freq_threshold", reverted)
            if new_val is not None and abs(new_val - old_val) > 0.001:
                change = {
                    "param": "stockout_freq_threshold",
                    "old": round(old_val, 4),
                    "new": round(new_val, 4),
                    "reason": f"업그레이드 적중률 낮음={upgrade_accuracy:.1%} → 기준 상향",
                }
                changes.append(change)
                self.calibration_repo.save_calibration(
                    calibration_date=today,
                    param_name="stockout_freq_threshold",
                    old_value=old_val,
                    new_value=new_val,
                    reason=change["reason"],
                    accuracy_before=upgrade_accuracy,
                    sample_size=total,
                )

        elif upgrade_accuracy > target + 0.15:
            # 적중률 매우 높음 → 임계값 하향 (더 관대 = 업그레이드 발동 늘림)
            suggested = current_threshold - 0.02  # 15% → 13%
            old_val = current_threshold
            reverted = self._apply_mean_reversion("stockout_freq_threshold", suggested)
            new_val = self.config.update_param("stockout_freq_threshold", reverted)
            if new_val is not None and abs(new_val - old_val) > 0.001:
                change = {
                    "param": "stockout_freq_threshold",
                    "old": round(old_val, 4),
                    "new": round(new_val, 4),
                    "reason": f"업그레이드 적중률 높음={upgrade_accuracy:.1%} → 기준 하향",
                }
                changes.append(change)
                self.calibration_repo.save_calibration(
                    calibration_date=today,
                    param_name="stockout_freq_threshold",
                    old_value=old_val,
                    new_value=new_val,
                    reason=change["reason"],
                    accuracy_before=upgrade_accuracy,
                    sample_size=total,
                )

        return changes

    # =========================================================================
    # 유틸리티
    # =========================================================================

    @staticmethod
    def _pearson_correlation(x: List[float], y: List[float]) -> Optional[float]:
        """피어슨 상관계수 계산 (외부 라이브러리 없이)"""
        n = min(len(x), len(y))
        if n < 10:
            return None

        x = x[:n]
        y = y[:n]

        mean_x = sum(x) / n
        mean_y = sum(y) / n

        cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
        var_x = sum((xi - mean_x) ** 2 for xi in x)
        var_y = sum((yi - mean_y) ** 2 for yi in y)

        denom = (var_x * var_y) ** 0.5
        if denom < 1e-10:
            return None

        return cov / denom

    def run_daily_calibration(self) -> Dict[str, Dict[str, Any]]:
        """
        일일 보정 플로우 전체 실행

        1) 어제 결정 사후 검증
        2) 파라미터 보정 (충분한 데이터 시)

        Returns:
            {"verification": {...}, "calibration": {...}}
        """
        logger.info("=" * 50)
        logger.info("일일 보정 플로우 시작")
        logger.info("=" * 50)

        # 1) 사후 검증
        verification = self.verify_yesterday()

        # 2) 자동 보정
        calibration = self.calibrate()

        logger.info("일일 보정 플로우 완료")
        return {
            "verification": verification,
            "calibration": calibration,
        }
