"""
푸드 폐기율 자동 보정 캘리브레이터

중분류별 목표 폐기율에 맞춰 safety_days / gap_coefficient / waste_buffer를
자동으로 미세 조정한다.

동작 원리:
    1. 최근 N일(21일) 실제 폐기율 계산 (mid_cd별)
    2. 목표 폐기율과 비교 (error = actual - target)
    3. 불감대(±2%p) 밖이면 파라미터 조정
    4. 변경 이력을 food_waste_calibration 테이블에 저장

충돌 방지:
    - 요일/날씨/계절/휴일 계수 (수요 예측 레벨) → 건드리지 않음
    - 동적 폐기계수 (adjusted_prediction 레벨) → 건드리지 않음
    - 본 캘리브레이터: safety_days, gap_coef, waste_buffer 값 자체를 조정
"""

import json
import sqlite3
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from src.utils.logger import get_logger
from src.prediction.categories.food import (
    FOOD_CATEGORIES,
    FOOD_EXPIRY_SAFETY_CONFIG,
    DELIVERY_GAP_CONFIG,
    FOOD_EXPIRY_FALLBACK,
    get_food_expiry_group,
)
from src.prediction.categories.food_daily_cap import FOOD_DAILY_CAP_CONFIG
from src.settings.constants import (
    FOOD_WASTE_RATE_TARGETS,
    FOOD_WASTE_CAL_ENABLED,
    FOOD_WASTE_CAL_MIN_DAYS,
    FOOD_WASTE_CAL_LOOKBACK_DAYS,
    FOOD_WASTE_CAL_DEADBAND,
    FOOD_WASTE_CAL_STEP_SMALL,
    FOOD_WASTE_CAL_STEP_LARGE,
    FOOD_WASTE_CAL_ERROR_LARGE,
    FOOD_WASTE_CAL_SAFETY_DAYS_RANGE,
    FOOD_WASTE_CAL_GAP_COEF_RANGE,
    FOOD_WASTE_CAL_WASTE_BUFFER_RANGE,
)

logger = get_logger(__name__)


# =============================================================================
# 데이터 클래스
# =============================================================================

@dataclass
class CalibrationParams:
    """보정 가능 파라미터 스냅샷"""
    safety_days: float
    gap_coefficient: float
    waste_buffer: int

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "CalibrationParams":
        d = json.loads(s)
        return cls(**d)


@dataclass
class CalibrationResult:
    """단일 mid_cd의 보정 결과"""
    mid_cd: str
    actual_waste_rate: float
    target_waste_rate: float
    error: float
    sample_days: int
    total_order_qty: int
    total_waste_qty: int
    total_sold_qty: int
    param_name: Optional[str] = None
    old_value: Optional[float] = None
    new_value: Optional[float] = None
    adjusted: bool = False
    reason: str = ""


# =============================================================================
# 보정 파라미터 조회 (food.py / food_daily_cap.py 에서 사용)
# =============================================================================

def get_calibrated_food_params(
    mid_cd: str,
    store_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Optional[CalibrationParams]:
    """DB에서 최신 보정 파라미터를 조회한다.

    Returns:
        CalibrationParams 또는 None (보정 이력이 없으면)
    """
    if db_path is None:
        try:
            from src.infrastructure.database.connection import DBRouter
            if store_id:
                conn = DBRouter.get_store_connection(store_id)
            else:
                from src.infrastructure.database.connection import get_connection
                conn = get_connection()
        except Exception:
            return None
    else:
        try:
            conn = sqlite3.connect(db_path, timeout=10)
        except Exception:
            return None

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT current_params FROM food_waste_calibration
            WHERE mid_cd = ? AND store_id = ?
            AND current_params IS NOT NULL
            ORDER BY calibration_date DESC
            LIMIT 1
        """, (mid_cd, store_id or ""))
        row = cursor.fetchone()
        if row and row[0]:
            return CalibrationParams.from_json(row[0])
        return None
    except sqlite3.OperationalError:
        # 테이블 미존재 (마이그레이션 전)
        return None
    finally:
        conn.close()


def get_default_params(mid_cd: str) -> CalibrationParams:
    """mid_cd의 기본(하드코딩) 파라미터를 반환한다."""
    exp_days = FOOD_EXPIRY_FALLBACK.get(mid_cd, FOOD_EXPIRY_FALLBACK.get("default", 7))
    expiry_group, group_cfg = get_food_expiry_group(exp_days)

    safety_days = group_cfg["safety_days"]
    gap_coef = DELIVERY_GAP_CONFIG["gap_coefficient"].get(expiry_group, 0.5)
    waste_buffer = FOOD_DAILY_CAP_CONFIG["waste_buffer"]

    return CalibrationParams(
        safety_days=safety_days,
        gap_coefficient=gap_coef,
        waste_buffer=waste_buffer,
    )


def get_effective_params(
    mid_cd: str,
    store_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> CalibrationParams:
    """보정값이 있으면 보정값, 없으면 기본값을 반환한다."""
    calibrated = get_calibrated_food_params(mid_cd, store_id, db_path)
    if calibrated is not None:
        return calibrated
    return get_default_params(mid_cd)


# =============================================================================
# 메인 캘리브레이터
# =============================================================================

class FoodWasteRateCalibrator:
    """푸드 카테고리 중분류별 폐기율 목표 자동 보정기

    매일 daily_job.py Phase 1.56에서 실행되어:
    1. 최근 N일 실제 폐기율 계산 (mid_cd별)
    2. 목표 폐기율과 비교
    3. 불감대(±2%p) 밖이면 파라미터 미세 조정
    4. 변경 이력을 DB에 저장
    """

    def __init__(
        self,
        store_id: Optional[str] = None,
        db_path: Optional[str] = None,
    ):
        self.store_id = store_id
        self._db_path = db_path

    # -----------------------------------------------------------------
    # public API
    # -----------------------------------------------------------------

    def calibrate(self) -> Dict[str, Any]:
        """전체 푸드 카테고리에 대해 보정 실행

        Returns:
            {"calibrated": bool, "results": [CalibrationResult, ...]}
        """
        if not FOOD_WASTE_CAL_ENABLED:
            return {"calibrated": False, "reason": "disabled", "results": []}

        # 기존 극단값을 현재 안전 범위로 클램프 (1회성 교정)
        clamped = self._clamp_stale_params()
        if clamped > 0:
            logger.info(f"[캘리브레이터] {clamped}개 mid_cd의 극단값 클램프 완료")

        results = []
        any_adjusted = False

        for mid_cd in FOOD_CATEGORIES:
            target = FOOD_WASTE_RATE_TARGETS.get(mid_cd)
            if target is None:
                continue

            result = self._calibrate_mid_cd(mid_cd, target)
            results.append(result)

            if result.adjusted:
                any_adjusted = True
                self._save_calibration(result)
                logger.info(
                    f"[폐기율보정] {mid_cd}: "
                    f"실제={result.actual_waste_rate:.1%} "
                    f"목표={result.target_waste_rate:.1%} "
                    f"({result.param_name}: "
                    f"{result.old_value:.3f}->{result.new_value:.3f})"
                )
            else:
                self._save_calibration(result)
                logger.debug(
                    f"[폐기율보정] {mid_cd}: "
                    f"실제={result.actual_waste_rate:.1%} "
                    f"목표={result.target_waste_rate:.1%} "
                    f"({result.reason})"
                )

        return {
            "calibrated": any_adjusted,
            "results": [asdict(r) for r in results],
        }

    # -----------------------------------------------------------------
    # 보정 로직
    # -----------------------------------------------------------------

    def _calibrate_mid_cd(self, mid_cd: str, target: float) -> CalibrationResult:
        """단일 중분류의 폐기율 보정"""
        # 1. 실제 폐기율 계산
        stats = self._get_waste_stats(mid_cd)

        result = CalibrationResult(
            mid_cd=mid_cd,
            actual_waste_rate=stats["waste_rate"],
            target_waste_rate=target,
            error=stats["waste_rate"] - target,
            sample_days=stats["sample_days"],
            total_order_qty=stats["total_order"],
            total_waste_qty=stats["total_waste"],
            total_sold_qty=stats["total_sold"],
        )

        # 2. 데이터 부족 체크
        if stats["sample_days"] < FOOD_WASTE_CAL_MIN_DAYS:
            result.reason = f"data_insufficient ({stats['sample_days']}/{FOOD_WASTE_CAL_MIN_DAYS})"
            return result

        if stats["total_order"] == 0:
            result.reason = "no_orders"
            return result

        # 3. 불감대 체크
        error = result.error
        if abs(error) <= FOOD_WASTE_CAL_DEADBAND:
            result.reason = f"within_deadband (error={error:+.1%})"
            return result

        # 4. 히스테리시스 체크 — 연속 2일 같은 방향이어야 조정
        #    단, 폐기율 < 목표 (error < 0 = 품절 위험) 시에는 면제하여 빠른 회복 허용
        if error > 0:
            if not self._check_consistent_direction(mid_cd, error):
                result.reason = f"hysteresis (error={error:+.1%}, direction not consistent)"
                return result

        # 5. 조정 방향 및 크기 결정
        current_params = get_effective_params(mid_cd, self.store_id, self._db_path)
        new_params = CalibrationParams(
            safety_days=current_params.safety_days,
            gap_coefficient=current_params.gap_coefficient,
            waste_buffer=current_params.waste_buffer,
        )

        exp_days = FOOD_EXPIRY_FALLBACK.get(mid_cd, FOOD_EXPIRY_FALLBACK.get("default", 7))
        expiry_group, _ = get_food_expiry_group(exp_days)

        if error > 0:
            # 폐기율이 목표보다 높음 → 발주 줄이기
            param_name, old_val, new_val = self._reduce_order(
                new_params, expiry_group, error
            )
        else:
            # 폐기율이 목표보다 낮음 → 품절 위험, 발주 늘리기
            param_name, old_val, new_val = self._increase_order(
                new_params, expiry_group, error
            )

        if param_name is None:
            result.reason = "at_limit"
            return result

        result.param_name = param_name
        result.old_value = old_val
        result.new_value = new_val
        result.adjusted = True
        result.reason = f"error={error:+.1%}"
        return result

    def _check_consistent_direction(self, mid_cd: str, current_error: float) -> bool:
        """최근 보정 이력에서 오차 방향이 현재와 일치하는지 확인 (히스테리시스)

        연속 2일 이상 같은 방향(부호)이어야 조정을 허용한다.
        이력이 없으면 (첫 보정) 허용.

        Args:
            mid_cd: 중분류 코드
            current_error: 현재 오차 (actual - target)

        Returns:
            True면 조정 허용, False면 건너뛰기
        """
        conn = self._get_conn()
        if conn is None:
            return True  # DB 없으면 허용

        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT error FROM food_waste_calibration
                WHERE mid_cd = ? AND store_id = ?
                ORDER BY calibration_date DESC
                LIMIT 1
            """, (mid_cd, self.store_id or ""))
            row = cursor.fetchone()
            conn.close()

            if row is None:
                return True  # 이력 없음 (첫 보정) → 허용

            prev_error = row[0]
            # 같은 방향(부호)이면 허용
            return (current_error > 0 and prev_error > 0) or (current_error < 0 and prev_error < 0)
        except Exception:
            if conn:
                conn.close()
            return True  # 오류 시 허용

    # 복합 감쇄 하한: safety_days × gap_coefficient의 곱이 이 값 이하이면 감소 중단
    COMPOUND_FLOOR = 0.1

    def _reduce_order(
        self,
        params: CalibrationParams,
        expiry_group: str,
        error: float,
    ) -> Tuple[Optional[str], Optional[float], Optional[float]]:
        """폐기율이 높을 때 → 파라미터 감소 (발주 줄이기)

        우선순위: safety_days → gap_coefficient
        (waste_buffer는 영향 범위가 크므로 가장 마지막)

        ※ compound floor: safety × gap 곱이 COMPOUND_FLOOR 이하이면 더 이상 감소하지 않음
          (복합 감쇄 효과로 사실상 발주 불가 상태 방지)
        """
        # 복합 감쇄 하한 체크: safety × gap 곱이 너무 낮으면 감소 중단
        compound = params.safety_days * params.gap_coefficient
        if compound <= self.COMPOUND_FLOOR:
            logger.info(
                f"[캘리브레이터] compound floor 도달 "
                f"(safety={params.safety_days:.3f} x gap={params.gap_coefficient:.3f} "
                f"= {compound:.3f} <= {self.COMPOUND_FLOOR}) -> 감소 중단"
            )
            return None, None, None

        step = FOOD_WASTE_CAL_STEP_LARGE if error > FOOD_WASTE_CAL_ERROR_LARGE else FOOD_WASTE_CAL_STEP_SMALL

        # 1순위: safety_days 감소
        sd_range = FOOD_WASTE_CAL_SAFETY_DAYS_RANGE.get(expiry_group, (0.35, 0.8))
        old_sd = params.safety_days
        new_sd = max(sd_range[0], round(old_sd - step, 3))
        if new_sd < old_sd:
            params.safety_days = new_sd
            return "safety_days", old_sd, new_sd

        # 2순위: gap_coefficient 감소
        gc_range = FOOD_WASTE_CAL_GAP_COEF_RANGE.get(expiry_group, (0.2, 0.7))
        old_gc = params.gap_coefficient
        new_gc = max(gc_range[0], round(old_gc - step, 3))
        if new_gc < old_gc:
            params.gap_coefficient = new_gc
            return "gap_coefficient", old_gc, new_gc

        # 모두 하한 도달
        return None, None, None

    def _increase_order(
        self,
        params: CalibrationParams,
        expiry_group: str,
        error: float,
    ) -> Tuple[Optional[str], Optional[float], Optional[float]]:
        """폐기율이 낮을 때 → 파라미터 증가 (발주 늘리기)

        우선순위: safety_days → gap_coefficient

        ※ 심각한 과소발주(오차 10%p+) 시 step 1.5배 가속 (최대 0.08)
        """
        abs_error = abs(error)
        step = FOOD_WASTE_CAL_STEP_LARGE if abs_error > FOOD_WASTE_CAL_ERROR_LARGE else FOOD_WASTE_CAL_STEP_SMALL

        # 심각한 과소발주 시 추가 부스트 (오차 10%p 이상)
        if abs_error > 0.10:
            step = min(round(step * 1.5, 3), 0.08)
            logger.info(
                f"[캘리브레이터] 과소발주 가속 회복 "
                f"(error={error:+.1%}, step={step})"
            )

        # 1순위: safety_days 증가
        sd_range = FOOD_WASTE_CAL_SAFETY_DAYS_RANGE.get(expiry_group, (0.35, 0.8))
        old_sd = params.safety_days
        new_sd = min(sd_range[1], round(old_sd + step, 3))
        if new_sd > old_sd:
            params.safety_days = new_sd
            return "safety_days", old_sd, new_sd

        # 2순위: gap_coefficient 증가
        gc_range = FOOD_WASTE_CAL_GAP_COEF_RANGE.get(expiry_group, (0.2, 0.7))
        old_gc = params.gap_coefficient
        new_gc = min(gc_range[1], round(old_gc + step, 3))
        if new_gc > old_gc:
            params.gap_coefficient = new_gc
            return "gap_coefficient", old_gc, new_gc

        # 모두 상한 도달
        return None, None, None

    # -----------------------------------------------------------------
    # 데이터 조회
    # -----------------------------------------------------------------

    def _get_waste_stats(self, mid_cd: str) -> Dict[str, Any]:
        """mid_cd의 최근 N일 폐기 통계를 조회한다.

        1차: waste_slip_items (전표 상세 품목)에서 폐기 수량 조회
        2차: 데이터 없으면 daily_sales.disuse_qty 폴백 (축적 과도기 대응)
        판매량은 항상 daily_sales.sale_qty 사용.

        Returns:
            {
                "waste_rate": float (0.0~1.0),
                "sample_days": int,
                "total_sold": int,
                "total_waste": int,
                "total_order": int,  # sold + waste
                "waste_source": str,  # "slip_items" or "daily_sales"
            }
        """
        default = {
            "waste_rate": 0.0,
            "sample_days": 0,
            "total_sold": 0,
            "total_waste": 0,
            "total_order": 0,
            "waste_source": "none",
        }

        conn = self._get_conn()
        if conn is None:
            return default

        try:
            cursor = conn.cursor()
            lookback = FOOD_WASTE_CAL_LOOKBACK_DAYS

            # 1차: waste_slip_items에서 폐기 수량 (전표 기반, 정확)
            # products 테이블은 common.db에 있으므로 daily_sales의
            # item_cd를 서브쿼리로 mid_cd 매칭
            waste_from_slip = 0
            waste_source = "daily_sales"
            try:
                if self.store_id:
                    cursor.execute("""
                        SELECT COALESCE(SUM(wsi.qty), 0) as total_waste
                        FROM waste_slip_items wsi
                        WHERE wsi.item_cd IN (
                            SELECT DISTINCT item_cd FROM daily_sales
                            WHERE mid_cd = ? AND store_id = ?
                        )
                        AND wsi.store_id = ?
                        AND wsi.chit_date >= date('now', '-' || ? || ' days')
                    """, (mid_cd, self.store_id, self.store_id, lookback))
                else:
                    cursor.execute("""
                        SELECT COALESCE(SUM(wsi.qty), 0) as total_waste
                        FROM waste_slip_items wsi
                        WHERE wsi.item_cd IN (
                            SELECT DISTINCT item_cd FROM daily_sales
                            WHERE mid_cd = ?
                        )
                        AND wsi.chit_date >= date('now', '-' || ? || ' days')
                    """, (mid_cd, lookback))

                slip_row = cursor.fetchone()
                if slip_row:
                    waste_from_slip = slip_row[0] or 0
            except sqlite3.OperationalError:
                # waste_slip_items 테이블이 아직 없을 수 있음 (마이그레이션 전)
                waste_from_slip = 0

            # 판매량 조회 (항상 daily_sales)
            if self.store_id:
                cursor.execute("""
                    SELECT
                        COUNT(DISTINCT sales_date) as sample_days,
                        COALESCE(SUM(sale_qty), 0) as total_sold,
                        COALESCE(SUM(disuse_qty), 0) as total_waste_ds
                    FROM daily_sales
                    WHERE mid_cd = ? AND store_id = ?
                    AND sales_date >= date('now', '-' || ? || ' days')
                """, (mid_cd, self.store_id, lookback))
            else:
                cursor.execute("""
                    SELECT
                        COUNT(DISTINCT sales_date) as sample_days,
                        COALESCE(SUM(sale_qty), 0) as total_sold,
                        COALESCE(SUM(disuse_qty), 0) as total_waste_ds
                    FROM daily_sales
                    WHERE mid_cd = ?
                    AND sales_date >= date('now', '-' || ? || ' days')
                """, (mid_cd, lookback))

            row = cursor.fetchone()
            if not row or row[0] == 0:
                return default

            sample_days = row[0]
            total_sold = row[1]
            total_waste_ds = row[2]

            # 폐기 수량 결정: waste_slip_items 우선, 없으면 daily_sales 폴백
            if waste_from_slip > 0:
                total_waste = waste_from_slip
                waste_source = "slip_items"
            else:
                total_waste = total_waste_ds
                waste_source = "daily_sales"

            total_order = total_sold + total_waste

            waste_rate = total_waste / total_order if total_order > 0 else 0.0

            return {
                "waste_rate": waste_rate,
                "sample_days": sample_days,
                "total_sold": total_sold,
                "total_waste": total_waste,
                "total_order": total_order,
                "waste_source": waste_source,
            }
        except sqlite3.OperationalError as e:
            logger.debug(f"[폐기율보정] 통계 조회 실패 ({mid_cd}): {e}")
            return default
        finally:
            conn.close()

    # -----------------------------------------------------------------
    # DB 저장
    # -----------------------------------------------------------------

    def _save_calibration(self, result: CalibrationResult) -> None:
        """보정 결과를 DB에 저장한다."""
        conn = self._get_conn()
        if conn is None:
            return

        try:
            # 현재 유효 파라미터 스냅샷
            current = get_effective_params(
                result.mid_cd, self.store_id, self._db_path
            )

            # 조정이 있었으면 파라미터 업데이트
            if result.adjusted and result.param_name:
                setattr(current, result.param_name, result.new_value)

            cursor = conn.cursor()
            today = datetime.now().strftime("%Y-%m-%d")

            cursor.execute("""
                INSERT OR REPLACE INTO food_waste_calibration (
                    store_id, mid_cd, calibration_date,
                    actual_waste_rate, target_waste_rate, error,
                    sample_days, total_order_qty, total_waste_qty, total_sold_qty,
                    param_name, old_value, new_value,
                    current_params, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.store_id or "",
                result.mid_cd,
                today,
                result.actual_waste_rate,
                result.target_waste_rate,
                result.error,
                result.sample_days,
                result.total_order_qty,
                result.total_waste_qty,
                result.total_sold_qty,
                result.param_name,
                result.old_value,
                result.new_value,
                current.to_json(),
                datetime.now().isoformat(),
            ))
            conn.commit()
        except sqlite3.OperationalError as e:
            logger.debug(f"[폐기율보정] 저장 실패 ({result.mid_cd}): {e}")
        finally:
            conn.close()

    # -----------------------------------------------------------------
    # 극단값 클램프
    # -----------------------------------------------------------------

    def _clamp_stale_params(self) -> int:
        """기존 보정값이 현재 안전 범위 하한 미만이면 하한으로 클램프.

        DB 스키마 안전 범위가 상향 조정된 경우(예: ultra_short safety_days 0.2→0.35),
        이전에 저장된 극단값이 새 하한 아래일 수 있다. 이를 한 번 교정한다.

        Returns:
            클램프된 mid_cd 수
        """
        conn = self._get_conn()
        if conn is None:
            return 0

        clamped = 0
        try:
            for mid_cd in FOOD_CATEGORIES:
                params = get_calibrated_food_params(mid_cd, self.store_id, self._db_path)
                if params is None:
                    continue

                exp_days = FOOD_EXPIRY_FALLBACK.get(mid_cd, FOOD_EXPIRY_FALLBACK.get("default", 7))
                expiry_group, _ = get_food_expiry_group(exp_days)

                sd_range = FOOD_WASTE_CAL_SAFETY_DAYS_RANGE.get(expiry_group, (0.35, 0.8))
                gc_range = FOOD_WASTE_CAL_GAP_COEF_RANGE.get(expiry_group, (0.2, 0.7))

                changed = False
                if params.safety_days < sd_range[0]:
                    logger.warning(
                        f"[캘리브레이터클램프] {mid_cd} safety_days="
                        f"{params.safety_days:.3f} < 하한 {sd_range[0]} -> 클램프"
                    )
                    params.safety_days = sd_range[0]
                    changed = True
                if params.gap_coefficient < gc_range[0]:
                    logger.warning(
                        f"[캘리브레이터클램프] {mid_cd} gap_coef="
                        f"{params.gap_coefficient:.3f} < 하한 {gc_range[0]} -> 클램프"
                    )
                    params.gap_coefficient = gc_range[0]
                    changed = True

                if changed:
                    try:
                        cursor = conn.cursor()
                        today = datetime.now().strftime("%Y-%m-%d")
                        cursor.execute("""
                            INSERT OR REPLACE INTO food_waste_calibration (
                                store_id, mid_cd, calibration_date,
                                actual_waste_rate, target_waste_rate, error,
                                sample_days, current_params, created_at
                            ) VALUES (?, ?, ?, 0, 0, 0, 0, ?, ?)
                        """, (
                            self.store_id or "", mid_cd, today,
                            params.to_json(), datetime.now().isoformat()
                        ))
                        clamped += 1
                    except sqlite3.OperationalError as e:
                        logger.debug(f"[캘리브레이터클램프] 저장 실패 ({mid_cd}): {e}")

            if clamped > 0:
                conn.commit()
        except Exception as e:
            logger.warning(f"[캘리브레이터클램프] 클램프 실패: {e}")
        finally:
            conn.close()

        return clamped

    # -----------------------------------------------------------------
    # 연결 헬퍼
    # -----------------------------------------------------------------

    def _get_conn(self) -> Optional[sqlite3.Connection]:
        """DB 연결을 반환한다."""
        if self._db_path:
            try:
                return sqlite3.connect(self._db_path, timeout=10)
            except Exception:
                return None

        try:
            from src.infrastructure.database.connection import DBRouter, get_connection
            if self.store_id:
                return DBRouter.get_store_connection(self.store_id)
            return get_connection()
        except Exception:
            return None
