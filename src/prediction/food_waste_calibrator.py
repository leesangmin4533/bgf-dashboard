"""
푸드 폐기율 자동 보정 캘리브레이터

중분류(mid_cd)별 + 소분류(small_cd)별 목표 폐기율에 맞춰
safety_days / gap_coefficient / waste_buffer를 자동으로 미세 조정한다.

동작 원리:
    1. 최근 N일(21일) 실제 폐기율 계산 (mid_cd별 + small_cd별)
    2. 목표 폐기율과 비교 (error = actual - target)
    3. 불감대(+-2%p) 밖이면 파라미터 조정
    4. 변경 이력을 food_waste_calibration 테이블에 저장

소분류 보정:
    - SMALL_CD_TARGET_RATES에 등록된 (mid_cd, small_cd) 쌍만 보정
    - 소분류 내 상품 수 < SMALL_CD_MIN_PRODUCTS 이면 mid_cd 폴백
    - small_cd 보정값이 mid_cd 보정값보다 우선

충돌 방지:
    - 요일/날씨/계절/휴일 계수 (수요 예측 레벨) -> 건드리지 않음
    - 동적 폐기계수 (adjusted_prediction 레벨) -> 건드리지 않음
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
    SMALL_CD_TARGET_RATES,
    SMALL_CD_MIN_PRODUCTS,
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
    waste_buffer: int  # [DEPRECATED] apply_food_daily_cap()에서 20%×category_total로 대체. DB 호환용 유지

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "CalibrationParams":
        d = json.loads(s)
        return cls(**d)


@dataclass
class CalibrationResult:
    """단일 mid_cd (또는 small_cd)의 보정 결과"""
    mid_cd: str
    actual_waste_rate: float
    target_waste_rate: float
    error: float
    sample_days: int
    total_order_qty: int
    total_waste_qty: int
    total_sold_qty: int
    small_cd: Optional[str] = None
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
    small_cd: Optional[str] = None,
) -> Optional[CalibrationParams]:
    """DB에서 최신 보정 파라미터를 조회한다.

    우선순위:
        1. small_cd가 주어지면 (mid_cd, small_cd) 보정값 우선 조회
        2. 없으면 mid_cd 보정값 (small_cd='' 행) 폴백

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

        # 1차: small_cd 보정값 조회 (small_cd가 주어진 경우)
        if small_cd:
            try:
                cursor.execute("""
                    SELECT current_params FROM food_waste_calibration
                    WHERE mid_cd = ? AND store_id = ? AND small_cd = ?
                    AND current_params IS NOT NULL
                    ORDER BY calibration_date DESC
                    LIMIT 1
                """, (mid_cd, store_id or "", small_cd))
                row = cursor.fetchone()
                if row and row[0]:
                    return CalibrationParams.from_json(row[0])
            except sqlite3.OperationalError:
                pass  # small_cd 컬럼 미존재 (마이그레이션 전)

        # 2차: mid_cd 보정값 조회 (기존 로직, small_cd='' 또는 NULL)
        try:
            cursor.execute("""
                SELECT current_params FROM food_waste_calibration
                WHERE mid_cd = ? AND store_id = ?
                AND (small_cd IS NULL OR small_cd = '')
                AND current_params IS NOT NULL
                ORDER BY calibration_date DESC
                LIMIT 1
            """, (mid_cd, store_id or ""))
        except sqlite3.OperationalError:
            # small_cd 컬럼 미존재 시 기존 쿼리 폴백
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
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            logger.warning(
                f"[폐기율보정] food_waste_calibration 테이블 누락 "
                f"(store={store_id}) — init_store_db() 재실행 필요"
            )
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
    small_cd: Optional[str] = None,
) -> CalibrationParams:
    """보정값이 있으면 보정값, 없으면 기본값을 반환한다."""
    calibrated = get_calibrated_food_params(mid_cd, store_id, db_path, small_cd)
    if calibrated is not None:
        return calibrated
    return get_default_params(mid_cd)


# =============================================================================
# 메인 캘리브레이터
# =============================================================================

class FoodWasteRateCalibrator:
    """푸드 카테고리 중분류/소분류별 폐기율 목표 자동 보정기

    매일 daily_job.py Phase 1.56에서 실행되어:
    1. 최근 N일 실제 폐기율 계산 (mid_cd별 + small_cd별)
    2. 목표 폐기율과 비교
    3. 불감대(+-2%p) 밖이면 파라미터 미세 조정
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
        """전체 푸드 카테고리에 대해 보정 실행 (mid_cd + small_cd)

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

        # === Phase 1: mid_cd별 보정 (기존 로직) ===
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

        # === Phase 2: small_cd별 보정 (신규) ===
        small_cd_results = self._calibrate_all_small_cds()
        for result in small_cd_results:
            results.append(result)
            if result.adjusted:
                any_adjusted = True
                self._save_calibration(result)
                logger.info(
                    f"[폐기율보정] {result.mid_cd}/{result.small_cd}: "
                    f"실제={result.actual_waste_rate:.1%} "
                    f"목표={result.target_waste_rate:.1%} "
                    f"({result.param_name}: "
                    f"{result.old_value:.3f}->{result.new_value:.3f})"
                )
            else:
                self._save_calibration(result)
                logger.debug(
                    f"[폐기율보정] {result.mid_cd}/{result.small_cd}: "
                    f"실제={result.actual_waste_rate:.1%} "
                    f"목표={result.target_waste_rate:.1%} "
                    f"({result.reason})"
                )

        return {
            "calibrated": any_adjusted,
            "results": [asdict(r) for r in results],
        }

    # -----------------------------------------------------------------
    # 소분류 보정 로직
    # -----------------------------------------------------------------

    def _calibrate_all_small_cds(self) -> List[CalibrationResult]:
        """SMALL_CD_TARGET_RATES에 등록된 모든 소분류에 대해 보정 실행"""
        results = []

        # mid_cd별로 그룹화하여 처리
        mid_cd_small_cds: Dict[str, List[str]] = {}
        for (mid_cd, small_cd) in SMALL_CD_TARGET_RATES:
            if mid_cd not in mid_cd_small_cds:
                mid_cd_small_cds[mid_cd] = []
            mid_cd_small_cds[mid_cd].append(small_cd)

        for mid_cd, small_cds in mid_cd_small_cds.items():
            if mid_cd not in FOOD_CATEGORIES:
                continue

            for small_cd in small_cds:
                target = SMALL_CD_TARGET_RATES.get((mid_cd, small_cd))
                if target is None:
                    continue

                # 상품 수 체크: 최소 SMALL_CD_MIN_PRODUCTS 이상이어야 보정
                product_count = self._count_products_in_small_cd(mid_cd, small_cd)
                if product_count < SMALL_CD_MIN_PRODUCTS:
                    result = CalibrationResult(
                        mid_cd=mid_cd,
                        small_cd=small_cd,
                        actual_waste_rate=0.0,
                        target_waste_rate=target,
                        error=0.0,
                        sample_days=0,
                        total_order_qty=0,
                        total_waste_qty=0,
                        total_sold_qty=0,
                        reason=f"fallback_to_mid (products={product_count}<{SMALL_CD_MIN_PRODUCTS})",
                    )
                    results.append(result)
                    continue

                result = self._calibrate_small_cd(mid_cd, small_cd, target)
                results.append(result)

        return results

    def _calibrate_small_cd(
        self, mid_cd: str, small_cd: str, target: float
    ) -> CalibrationResult:
        """단일 소분류의 폐기율 보정"""
        # 1. 실제 폐기율 계산 (소분류 필터)
        stats = self._get_waste_stats_by_small_cd(mid_cd, small_cd)

        result = CalibrationResult(
            mid_cd=mid_cd,
            small_cd=small_cd,
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

        # 4. 히스테리시스 체크 (소분류용)
        if error > 0:
            if not self._check_consistent_direction(mid_cd, error, small_cd=small_cd):
                result.reason = f"hysteresis (error={error:+.1%}, direction not consistent)"
                return result

        # 5. 조정 방향 및 크기 결정
        current_params = get_effective_params(
            mid_cd, self.store_id, self._db_path, small_cd=small_cd
        )
        new_params = CalibrationParams(
            safety_days=current_params.safety_days,
            gap_coefficient=current_params.gap_coefficient,
            waste_buffer=current_params.waste_buffer,
        )

        exp_days = FOOD_EXPIRY_FALLBACK.get(mid_cd, FOOD_EXPIRY_FALLBACK.get("default", 7))
        expiry_group, _ = get_food_expiry_group(exp_days)

        if error > 0:
            param_name, old_val, new_val = self._reduce_order(
                new_params, expiry_group, error
            )
        else:
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

    def _count_products_in_small_cd(self, mid_cd: str, small_cd: str) -> int:
        """소분류 내 활성 상품 수를 조회한다.

        daily_sales에 small_cd가 없으므로 product_details(common.db) JOIN 필요.
        테스트에서는 단일 DB를 사용하므로 ATTACH 실패 시 직접 쿼리 폴백.
        """
        conn = self._get_conn()
        if conn is None:
            return 0

        try:
            cursor = conn.cursor()
            lookback = FOOD_WASTE_CAL_LOOKBACK_DAYS

            # common.db ATTACH 시도
            common_attached = self._attach_common_db(conn)

            if common_attached:
                pd_table = "common_db.product_details"
            else:
                pd_table = "product_details"

            if self.store_id:
                cursor.execute(f"""
                    SELECT COUNT(DISTINCT ds.item_cd)
                    FROM daily_sales ds
                    JOIN {pd_table} pd ON ds.item_cd = pd.item_cd
                    WHERE ds.mid_cd = ? AND pd.small_cd = ?
                    AND ds.store_id = ?
                    AND ds.sales_date >= date('now', '-' || ? || ' days')
                """, (mid_cd, small_cd, self.store_id, lookback))
            else:
                cursor.execute(f"""
                    SELECT COUNT(DISTINCT ds.item_cd)
                    FROM daily_sales ds
                    JOIN {pd_table} pd ON ds.item_cd = pd.item_cd
                    WHERE ds.mid_cd = ? AND pd.small_cd = ?
                    AND ds.sales_date >= date('now', '-' || ? || ' days')
                """, (mid_cd, small_cd, lookback))

            row = cursor.fetchone()
            return row[0] if row else 0
        except sqlite3.OperationalError as e:
            logger.debug(f"[폐기율보정] 상품 수 조회 실패 ({mid_cd}/{small_cd}): {e}")
            return 0
        finally:
            conn.close()

    def _get_waste_stats_by_small_cd(
        self, mid_cd: str, small_cd: str
    ) -> Dict[str, Any]:
        """소분류(small_cd)의 최근 N일 폐기 통계를 조회한다.

        daily_sales에 small_cd가 없으므로 product_details JOIN이 필요하다.
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

            # common.db ATTACH 시도
            common_attached = self._attach_common_db(conn)

            if common_attached:
                pd_table = "common_db.product_details"
            else:
                pd_table = "product_details"

            # 판매량 + 폐기량 조회 (daily_sales JOIN product_details)
            if self.store_id:
                cursor.execute(f"""
                    SELECT
                        COUNT(DISTINCT ds.sales_date) as sample_days,
                        COALESCE(SUM(ds.sale_qty), 0) as total_sold,
                        COALESCE(SUM(ds.disuse_qty), 0) as total_waste_ds
                    FROM daily_sales ds
                    JOIN {pd_table} pd ON ds.item_cd = pd.item_cd
                    WHERE ds.mid_cd = ? AND pd.small_cd = ?
                    AND ds.store_id = ?
                    AND ds.sales_date >= date('now', '-' || ? || ' days')
                """, (mid_cd, small_cd, self.store_id, lookback))
            else:
                cursor.execute(f"""
                    SELECT
                        COUNT(DISTINCT ds.sales_date) as sample_days,
                        COALESCE(SUM(ds.sale_qty), 0) as total_sold,
                        COALESCE(SUM(ds.disuse_qty), 0) as total_waste_ds
                    FROM daily_sales ds
                    JOIN {pd_table} pd ON ds.item_cd = pd.item_cd
                    WHERE ds.mid_cd = ? AND pd.small_cd = ?
                    AND ds.sales_date >= date('now', '-' || ? || ' days')
                """, (mid_cd, small_cd, lookback))

            row = cursor.fetchone()
            if not row or row[0] == 0:
                return default

            sample_days = row[0]
            total_sold = row[1]
            total_waste = row[2]
            total_order = total_sold + total_waste
            waste_rate = total_waste / total_order if total_order > 0 else 0.0

            return {
                "waste_rate": waste_rate,
                "sample_days": sample_days,
                "total_sold": total_sold,
                "total_waste": total_waste,
                "total_order": total_order,
                "waste_source": "daily_sales",
            }
        except sqlite3.OperationalError as e:
            logger.debug(f"[폐기율보정] 소분류 통계 조회 실패 ({mid_cd}/{small_cd}): {e}")
            return default
        finally:
            conn.close()

    # -----------------------------------------------------------------
    # 보정 로직 (기존)
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

        # 4. 히스테리시스 체크 -- 연속 2일 같은 방향이어야 조정
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
            # 폐기율이 목표보다 높음 -> 발주 줄이기
            param_name, old_val, new_val = self._reduce_order(
                new_params, expiry_group, error
            )
        else:
            # 폐기율이 목표보다 낮음 -> 품절 위험, 발주 늘리기
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

    def _check_consistent_direction(
        self, mid_cd: str, current_error: float,
        small_cd: Optional[str] = None,
    ) -> bool:
        """최근 보정 이력에서 오차 방향이 현재와 일치하는지 확인 (히스테리시스)

        연속 2일 이상 같은 방향(부호)이어야 조정을 허용한다.
        이력이 없으면 (첫 보정) 허용.

        Args:
            mid_cd: 중분류 코드
            current_error: 현재 오차 (actual - target)
            small_cd: 소분류 코드 (None이면 mid_cd 레벨)

        Returns:
            True면 조정 허용, False면 건너뛰기
        """
        conn = self._get_conn()
        if conn is None:
            return True  # DB 없으면 허용

        try:
            cursor = conn.cursor()
            if small_cd:
                try:
                    cursor.execute("""
                        SELECT error FROM food_waste_calibration
                        WHERE mid_cd = ? AND store_id = ? AND small_cd = ?
                        ORDER BY calibration_date DESC
                        LIMIT 1
                    """, (mid_cd, self.store_id or "", small_cd))
                except sqlite3.OperationalError:
                    # small_cd 컬럼 미존재
                    return True
            else:
                cursor.execute("""
                    SELECT error FROM food_waste_calibration
                    WHERE mid_cd = ? AND store_id = ?
                    ORDER BY calibration_date DESC
                    LIMIT 1
                """, (mid_cd, self.store_id or ""))
            row = cursor.fetchone()

            if row is None:
                return True  # 이력 없음 (첫 보정) -> 허용

            prev_error = row[0]
            # 같은 방향(부호)이면 허용
            return (current_error > 0 and prev_error > 0) or (current_error < 0 and prev_error < 0)
        except Exception:
            return True  # 오류 시 허용
        finally:
            conn.close()

    # 복합 감쇄 하한: safety_days * gap_coefficient의 곱이 이 값 이하이면 감소 중단
    # 0.10 → 0.15 상향 (food-waste-unify: 안전재고 최소 보장)
    COMPOUND_FLOOR = 0.15

    def _reduce_order(
        self,
        params: CalibrationParams,
        expiry_group: str,
        error: float,
    ) -> Tuple[Optional[str], Optional[float], Optional[float]]:
        """폐기율이 높을 때 -> 파라미터 감소 (발주 줄이기)

        우선순위: safety_days -> gap_coefficient
        (waste_buffer는 영향 범위가 크므로 가장 마지막)

        compound floor: safety * gap 곱이 COMPOUND_FLOOR 이하이면 더 이상 감소하지 않음
          (복합 감쇄 효과로 사실상 발주 불가 상태 방지)
        """
        # 복합 감쇄 하한 체크: safety * gap 곱이 너무 낮으면 감소 중단
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
        """폐기율이 낮을 때 -> 파라미터 증가 (발주 늘리기)

        우선순위: safety_days -> gap_coefficient

        심각한 과소발주(오차 10%p+) 시 step 2.0배 가속 (최대 0.12)
        (food-waste-unify: 1.5x/0.08 → 2.0x/0.12 상향)
        """
        abs_error = abs(error)
        step = FOOD_WASTE_CAL_STEP_LARGE if abs_error > FOOD_WASTE_CAL_ERROR_LARGE else FOOD_WASTE_CAL_STEP_SMALL

        # 심각한 과소발주 시 추가 부스트 (오차 10%p 이상)
        if abs_error > 0.10:
            step = min(round(step * 2.0, 3), 0.12)
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
    # 데이터 조회 (기존 mid_cd 레벨)
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
                result.mid_cd, self.store_id, self._db_path,
                small_cd=result.small_cd,
            )

            # 조정이 있었으면 파라미터 업데이트
            if result.adjusted and result.param_name:
                setattr(current, result.param_name, result.new_value)

            cursor = conn.cursor()
            today = datetime.now().strftime("%Y-%m-%d")
            small_cd_val = result.small_cd or ""

            # small_cd 컬럼이 있는 경우
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO food_waste_calibration (
                        store_id, mid_cd, small_cd, calibration_date,
                        actual_waste_rate, target_waste_rate, error,
                        sample_days, total_order_qty, total_waste_qty, total_sold_qty,
                        param_name, old_value, new_value,
                        current_params, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    self.store_id or "",
                    result.mid_cd,
                    small_cd_val,
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
            except sqlite3.OperationalError:
                # small_cd 컬럼이 없는 경우 (마이그레이션 전) -> 기존 스키마로 폴백
                if small_cd_val:
                    # 소분류 보정인데 컬럼이 없으면 저장 건너뛰기
                    logger.debug(
                        f"[폐기율보정] small_cd 컬럼 미존재, "
                        f"소분류 보정 저장 건너뜀 ({result.mid_cd}/{small_cd_val})"
                    )
                    return
                # mid_cd 보정은 기존 스키마로 저장
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

        DB 스키마 안전 범위가 상향 조정된 경우(예: ultra_short safety_days 0.2->0.35),
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
                        try:
                            cursor.execute("""
                                INSERT OR REPLACE INTO food_waste_calibration (
                                    store_id, mid_cd, small_cd, calibration_date,
                                    actual_waste_rate, target_waste_rate, error,
                                    sample_days, current_params, created_at
                                ) VALUES (?, ?, '', ?, 0, 0, 0, 0, ?, ?)
                            """, (
                                self.store_id or "", mid_cd, today,
                                params.to_json(), datetime.now().isoformat()
                            ))
                        except sqlite3.OperationalError:
                            # small_cd 컬럼 미존재 시 기존 스키마
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

    def _attach_common_db(self, conn: sqlite3.Connection) -> bool:
        """common.db를 ATTACH한다. 이미 ATTACH되었거나 성공하면 True.

        db_path가 설정된 경우(테스트 모드) product_details가 같은 DB에 있으므로
        ATTACH를 건너뛴다.
        """
        # db_path 직접 지정 시 (테스트 모드): product_details가 같은 DB에 있음
        if self._db_path:
            return False

        try:
            from src.infrastructure.database.connection import DBRouter
            common_path = DBRouter.get_common_db_path()
            conn.execute(f"ATTACH DATABASE '{common_path}' AS common_db")
            return True
        except Exception:
            # 이미 ATTACH되었거나 실패
            # product_details가 현재 DB에 있는지 확인
            try:
                conn.execute("SELECT 1 FROM product_details LIMIT 1")
                return False  # product_details가 현재 DB에 있음
            except sqlite3.OperationalError:
                return False
