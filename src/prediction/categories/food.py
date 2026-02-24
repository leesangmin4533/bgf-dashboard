"""
푸드류 카테고리 예측 모듈

푸드류(001~005, 012)의 동적 안전재고 계산:
- 유통기한 기반: 초단기(1일), 단기(2~3일), 중기(4~7일), 장기(8~30일), 초장기(31일+)
- 폐기율 계수: 폐기율이 높을수록 보수적 발주

공식: 일평균 × 안전재고일수(유통기한별) × 폐기율계수

일평균은 분석 기간(30일) 전체 기준으로 산출 (간헐적 판매 과대추정 방지)
"""

import sqlite3
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Optional, Tuple, Dict

from src.utils.logger import get_logger
from src.settings.constants import (
    DISUSE_COEF_FLOOR, DISUSE_COEF_MULTIPLIER,
    DISUSE_MIN_BATCH_COUNT, DISUSE_IB_LOOKBACK_DAYS,
)

logger = get_logger(__name__)


# =============================================================================
# 푸드류 설정
# =============================================================================
FOOD_CATEGORIES = ['001', '002', '003', '004', '005', '012']
# 001: 도시락, 002: 주먹밥, 003: 김밥, 004: 샌드위치, 005: 햄버거, 012: 빵

FOOD_ANALYSIS_DAYS = 30  # 일평균 산출 분석 기간 (일)

FOOD_EXPIRY_SAFETY_CONFIG = {
    "enabled": True,              # 동적 안전재고 적용 여부

    # 유통기한별 안전재고 일수
    "expiry_groups": {
        "ultra_short": {  # 1일 (당일 폐기)
            "min_days": 0,
            "max_days": 1,
            "safety_days": 0.5,  # 0.3 → 0.5 상향 (Priority 1.3)
            "description": "당일 폐기 (도시락, 김밥, 주먹밥)"
        },
        "short": {  # 2~3일
            "min_days": 2,
            "max_days": 3,
            "safety_days": 0.7,  # 0.5 → 0.7 상향 (Priority 1.3)
            "description": "단기 유통 (샌드위치, 일부 빵)"
        },
        "medium": {  # 4~7일
            "min_days": 4,
            "max_days": 7,
            "safety_days": 1.0,
            "description": "중기 유통 (디저트, 장기빵)"
        },
        "long": {  # 8~30일
            "min_days": 8,
            "max_days": 30,
            "safety_days": 1.5,
            "description": "장기 유통 (일부 디저트)"
        },
        "very_long": {  # 31일+
            "min_days": 31,
            "max_days": 9999,
            "safety_days": 2.0,
            "description": "초장기 유통"
        }
    },

    # 기본 안전재고 일수 (유통기한 정보 없을 때)
    "default_safety_days": 1.0,
}

# 푸드류 Fallback 유통기한 (DB에 없는 경우 카테고리별 기본값)
FOOD_EXPIRY_FALLBACK = {
    '001': 1,   # 도시락
    '002': 1,   # 주먹밥
    '003': 1,   # 김밥
    '004': 2,   # 샌드위치
    '005': 3,   # 햄버거 (alert/config.py: shelf_life_default=3, 74시간)
    '012': 3,   # 빵
    'default': 7
}

# 푸드류 폐기율 계수 (Priority 1.4 완화)
FOOD_DISUSE_COEFFICIENT = {
    # 폐기율 범위: 계수
    (0.00, 0.05): 1.0,    # 5% 미만: 정상
    (0.05, 0.10): 0.95,   # 5~10%: 5% 감소 (0.9 → 0.95)
    (0.10, 0.15): 0.85,   # 10~15%: 15% 감소 (0.8 → 0.85)
    (0.15, 0.20): 0.75,   # 15~20%: 25% 감소 (0.65 → 0.75)
    (0.20, 1.01): 0.7,    # 20% 이상: 30% 감소 (0.5 → 0.7)
}

# 차수별 폐기 실적 기반 조정 계수
DELIVERY_WASTE_COEFFICIENT = {
    # 폐기율 범위: (임계값, 조정계수)
    "threshold_10": (0.10, 1.0),    # 10% 미만: 정상
    "threshold_20": (0.20, 0.90),   # 10~20%: 10% 축소
    "threshold_30": (0.30, 0.80),   # 20~30%: 20% 축소
    "threshold_50": (0.50, 0.65),   # 30~50%: 35% 축소
    "over_50": (1.00, 0.50),        # 50% 이상: 50% 축소
}


# 배송 갭 소비량 설정
# 발주(07:00) ~ 배송 도착 사이 예상 소비량을 보정하기 위한 설정
DELIVERY_GAP_CONFIG = {
    "enabled": True,
    "order_hour": 7,       # 발주/스케줄러 실행 시간
    "gap_coefficient": {
        "ultra_short": 0.4,   # 1일 유통 - 보수적 (폐기 위험)
        "short": 0.6,         # 2-3일 유통
        "medium": 0.8,        # 4-7일 유통
        "long": 1.0,          # 8일+
        "very_long": 1.0,     # 31일+
    },
}

# 배송 차수별 시간대 수요 비율 (하루 전체 대비)
# 편의점 푸드는 아침(07-09), 점심(11-14), 저녁(17-20) 피크
# 1차 배송(당일 20:00 도착): 07:00~20:00 = 아침+점심+저녁 피크 포함 → 약 70%
# 2차 배송(익일 07:00 도착): 20:00~07:00+07:00~다음배송 → 약 30% + 다음날 전체
DELIVERY_TIME_DEMAND_RATIO = {
    "1차": 0.70,    # 당일 20시 도착 → 주간 수요(70%) 대응
    "2차": 1.00,    # 익일 07시 도착 → 하루 전체 수요 대응
}


def calculate_delivery_gap_consumption(
    daily_avg: float,
    item_nm: str,
    expiry_group: str,
    mid_cd: Optional[str] = None,
    store_id: Optional[str] = None,
) -> float:
    """
    배송 갭 소비량 계산

    발주 시점(07:00)부터 배송 도착까지의 예상 소비량을 반환한다.
    1차 배송(당일 20:00)은 13시간 갭, 2차 배송(익일 07:00)은 24시간 갭.

    Args:
        daily_avg: 일평균 판매량
        item_nm: 상품명 (끝자리로 차수 판별)
        expiry_group: 유통기한 그룹 (ultra_short, short, medium, long, very_long)

    Returns:
        배송 갭 소비량 (float). 비푸드 또는 비활성 시 0.0
    """
    config = DELIVERY_GAP_CONFIG
    if not config["enabled"] or daily_avg <= 0:
        return 0.0

    # DELIVERY_CONFIG에서 도착 시간 조회
    from src.alert.config import DELIVERY_CONFIG

    # 상품명 끝자리로 차수 판별
    delivery_type = "1차"  # 기본값
    if item_nm and item_nm.strip():
        last_char = item_nm.strip()[-1]
        if last_char == "2":
            delivery_type = "2차"

    delivery_cfg = DELIVERY_CONFIG.get(delivery_type, DELIVERY_CONFIG["1차"])
    arrival_hour = delivery_cfg["arrival_hour"]
    arrival_next_day = delivery_cfg["arrival_next_day"]

    # 갭 시간 계산: 발주시각 → 도착시각
    order_hour = config["order_hour"]
    if arrival_next_day:
        # 익일 도착: 24 - order_hour + arrival_hour
        gap_hours = (24 - order_hour) + arrival_hour
    else:
        # 당일 도착: arrival_hour - order_hour
        gap_hours = arrival_hour - order_hour

    if gap_hours <= 0:
        return 0.0

    # 유통기한 그룹별 계수 (보정값 우선)
    coefficient = config["gap_coefficient"].get(expiry_group, 0.8)
    if mid_cd:
        try:
            from src.prediction.food_waste_calibrator import get_calibrated_food_params
            cal = get_calibrated_food_params(mid_cd, store_id)
            if cal is not None:
                coefficient = cal.gap_coefficient
        except Exception:
            pass

    # 시간대별 수요 비율 적용 (갭 시간 비례 대신 실제 수요 분포 반영)
    time_demand_ratio = DELIVERY_TIME_DEMAND_RATIO.get(delivery_type, 1.0)
    gap_consumption = daily_avg * time_demand_ratio * coefficient
    return round(gap_consumption, 2)


def _get_db_path(store_id: Optional[str] = None) -> str:
    """DB 경로 반환 (매장 DB 우선, 없으면 legacy)"""
    if store_id:
        try:
            from src.infrastructure.database.connection import DBRouter
            store_path = DBRouter.get_store_db_path(store_id)
            if store_path.exists():
                return str(store_path)
        except Exception:
            pass
    return str(Path(__file__).parent.parent.parent.parent / "data" / "bgf_sales.db")


@dataclass
class FoodExpiryResult:
    """푸드류 유통기한 분석 결과"""
    item_cd: str
    mid_cd: str                   # 중분류 코드

    # 유통기한 정보
    expiration_days: int          # 유통기한 (일)
    expiry_group: str             # 그룹 (ultra_short, short, medium, long, very_long)
    safety_days: float            # 안전재고 일수
    data_source: str              # 'db' 또는 'fallback'

    # 판매량 정보
    daily_avg: float              # 일평균 판매량
    actual_data_days: int         # 실제 데이터 일수

    # 최종 결과
    safety_stock: float           # 최종 안전재고 수량 (일평균 × 안전재고일수)
    description: str              # 그룹 설명


def is_food_category(mid_cd: str) -> bool:
    """푸드류 카테고리 여부 확인

    Args:
        mid_cd: 중분류 코드

    Returns:
        푸드류 카테고리(001~005, 012)이면 True
    """
    return mid_cd in FOOD_CATEGORIES


def get_food_expiry_group(expiration_days: Optional[int]) -> Tuple[str, Dict[str, Any]]:
    """
    유통기한으로 그룹 분류

    Args:
        expiration_days: 유통기한 (일)

    Returns:
        (그룹명, 그룹설정)
    """
    config = FOOD_EXPIRY_SAFETY_CONFIG["expiry_groups"]

    if expiration_days is None:
        return "medium", config["medium"]

    for group_name, group_cfg in config.items():
        if group_cfg["min_days"] <= expiration_days <= group_cfg["max_days"]:
            return group_name, group_cfg

    # 범위 밖이면 very_long
    return "very_long", config["very_long"]


def get_food_expiration_days(item_cd: str, mid_cd: str, db_path: Optional[str] = None) -> Tuple[int, str]:
    """
    유통기한 조회 (DB → fallback)

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드
        db_path: DB 경로 (무시됨 - product_details는 항상 common.db에서 조회)

    Returns:
        (유통기한_일수, 데이터소스)
    """
    # product_details는 common.db에만 존재 → 항상 common DB 사용
    try:
        from src.infrastructure.database.connection import DBRouter
        common_path = str(DBRouter.get_common_db_path())
    except Exception:
        common_path = str(Path(__file__).parent.parent.parent.parent / "data" / "bgf_sales.db")

    # 1. DB에서 조회 (product_details.expiration_days)
    try:
        conn = sqlite3.connect(common_path, timeout=30)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT expiration_days
                FROM product_details
                WHERE item_cd = ?
            """, (item_cd,))
            row = cursor.fetchone()

            if row and row[0] is not None and row[0] > 0:
                return int(row[0]), "db"
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"푸드 DB 조회 실패 ({item_cd}): {e}")

    # 2. Fallback (카테고리별 기본값)
    fallback_days = FOOD_EXPIRY_FALLBACK.get(mid_cd, FOOD_EXPIRY_FALLBACK['default'])
    return fallback_days, "fallback"


def get_food_disuse_coefficient(disuse_rate: Optional[float]) -> float:
    """
    폐기율에 따른 계수 반환 (고정 계단 함수)

    .. deprecated::
        get_dynamic_disuse_coefficient()를 사용하세요.
        이 함수는 하위 호환용으로만 유지됩니다.

    Args:
        disuse_rate: 폐기율 (0.0 ~ 1.0)

    Returns:
        폐기율 계수
    """
    if disuse_rate is None:
        return 1.0

    for (min_rate, max_rate), coef in FOOD_DISUSE_COEFFICIENT.items():
        if min_rate <= disuse_rate < max_rate:
            return coef

    return 1.0


def get_dynamic_disuse_coefficient(
    item_cd: str,
    mid_cd: str,
    days: int = 30,
    db_path: Optional[str] = None,
    store_id: Optional[str] = None
) -> float:
    """
    상품별 30일 실적 기반 동적 폐기율 계수 계산

    고정 FOOD_DISUSE_COEFFICIENT 대신 상품별/중분류별 실제 폐기 데이터를
    기반으로 연속적인 계수를 반환한다.

    계수 공식: max(0.5, 1.0 - disuse_rate * 1.5)
    - 폐기율 0% → 1.0 (감량 없음)
    - 폐기율 10% → 0.85
    - 폐기율 20% → 0.70
    - 폐기율 33%+ → 0.50 (하한)

    같은 중분류(mid_cd)의 평균 폐기율도 참고하여 블렌딩한다.
    - 상품별 데이터 충분(7일+): 상품 80% + 중분류 20%
    - 상품별 데이터 부족: 중분류 100%

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드
        days: 분석 기간 (기본 30일)
        db_path: DB 경로
        store_id: 점포 코드 (None이면 전체 점포)

    Returns:
        동적 폐기율 계수 (0.5 ~ 1.0)
    """
    if db_path is None:
        db_path = _get_db_path(store_id)

    item_rate = None
    mid_rate = None

    try:
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            cursor = conn.cursor()

            # === 1차: inventory_batches 기반 (유통기한 역추적, 정확도 높음) ===
            ib_item_found = False
            ib_mid_found = False
            item_batch_count = 0
            item_data_days = 0  # daily_sales fallback용 (캘린더 일수)
            ib_lookback = DISUSE_IB_LOOKBACK_DAYS

            try:
                # 상품별 폐기율 (최근 ib_lookback일)
                if store_id:
                    cursor.execute("""
                        SELECT SUM(initial_qty) as total_initial,
                               SUM(CASE WHEN status = 'expired' THEN remaining_qty ELSE 0 END) as total_waste,
                               COUNT(*) as batch_cnt
                        FROM inventory_batches
                        WHERE item_cd = ? AND store_id = ?
                        AND status IN ('consumed', 'expired')
                        AND receiving_date >= date('now', '-' || ? || ' days')
                    """, (item_cd, store_id, ib_lookback))
                else:
                    cursor.execute("""
                        SELECT SUM(initial_qty) as total_initial,
                               SUM(CASE WHEN status = 'expired' THEN remaining_qty ELSE 0 END) as total_waste,
                               COUNT(*) as batch_cnt
                        FROM inventory_batches
                        WHERE item_cd = ?
                        AND status IN ('consumed', 'expired')
                        AND receiving_date >= date('now', '-' || ? || ' days')
                    """, (item_cd, ib_lookback))
                row = cursor.fetchone()

                if row and row[0] and row[0] > 0:
                    item_rate = (row[1] or 0) / row[0]
                    item_batch_count = row[2] or 0
                    ib_item_found = True

                # 중분류별 평균 폐기율 (최근 ib_lookback일)
                if store_id:
                    cursor.execute("""
                        SELECT SUM(initial_qty) as total_initial,
                               SUM(CASE WHEN status = 'expired' THEN remaining_qty ELSE 0 END) as total_waste
                        FROM inventory_batches
                        WHERE mid_cd = ? AND store_id = ?
                        AND status IN ('consumed', 'expired')
                        AND receiving_date >= date('now', '-' || ? || ' days')
                    """, (mid_cd, store_id, ib_lookback))
                else:
                    cursor.execute("""
                        SELECT SUM(initial_qty) as total_initial,
                               SUM(CASE WHEN status = 'expired' THEN remaining_qty ELSE 0 END) as total_waste
                        FROM inventory_batches
                        WHERE mid_cd = ?
                        AND status IN ('consumed', 'expired')
                        AND receiving_date >= date('now', '-' || ? || ' days')
                    """, (mid_cd, ib_lookback))
                row = cursor.fetchone()

                if row and row[0] and row[0] > 0:
                    mid_rate = (row[1] or 0) / row[0]
                    ib_mid_found = True

            except sqlite3.OperationalError:
                # inventory_batches 테이블 미존재 시 fallback
                pass

            # === 2차 fallback: daily_sales.disuse_qty 기반 ===
            if not ib_item_found:
                if store_id:
                    cursor.execute("""
                        SELECT SUM(sale_qty), SUM(disuse_qty), COUNT(*)
                        FROM daily_sales
                        WHERE item_cd = ? AND store_id = ?
                        AND sales_date >= date('now', '-' || ? || ' days')
                    """, (item_cd, store_id, days))
                else:
                    cursor.execute("""
                        SELECT SUM(sale_qty), SUM(disuse_qty), COUNT(*)
                        FROM daily_sales
                        WHERE item_cd = ?
                        AND sales_date >= date('now', '-' || ? || ' days')
                    """, (item_cd, days))
                row = cursor.fetchone()

                item_data_days = row[2] if row and row[2] else 0
                if row and (row[0] or row[1]):
                    total_sales = row[0] or 0
                    total_disuse = row[1] or 0
                    total = total_sales + total_disuse
                    if total > 0:
                        item_rate = total_disuse / total

            if not ib_mid_found:
                if store_id:
                    cursor.execute("""
                        SELECT SUM(sale_qty), SUM(disuse_qty)
                        FROM daily_sales
                        WHERE mid_cd = ? AND store_id = ?
                        AND sales_date >= date('now', '-' || ? || ' days')
                    """, (mid_cd, store_id, days))
                else:
                    cursor.execute("""
                        SELECT SUM(sale_qty), SUM(disuse_qty)
                        FROM daily_sales
                        WHERE mid_cd = ?
                        AND sales_date >= date('now', '-' || ? || ' days')
                    """, (mid_cd, days))
                row = cursor.fetchone()

                if row and (row[0] or row[1]):
                    total_sales = row[0] or 0
                    total_disuse = row[1] or 0
                    total = total_sales + total_disuse
                    if total > 0:
                        mid_rate = total_disuse / total
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[동적폐기계수] DB 조회 실패 ({item_cd}): {e}")

    # 블렌딩: 표본 충분하면 상품 80% + 중분류 20%
    # inventory_batches: 배치 수 >= DISUSE_MIN_BATCH_COUNT
    # daily_sales fallback: 캘린더 일수 >= 7
    sample_sufficient = False
    sample_n = 0
    if ib_item_found:
        sample_sufficient = item_batch_count >= DISUSE_MIN_BATCH_COUNT
        sample_n = item_batch_count
    elif item_data_days >= 7:
        sample_sufficient = True
        sample_n = item_data_days

    blend_source = ""
    if item_rate is not None and sample_sufficient:
        if mid_rate is not None:
            blended_rate = item_rate * 0.8 + mid_rate * 0.2
            blend_source = f"item({item_rate:.1%},n={sample_n})×0.8+mid({mid_rate:.1%})×0.2"
        else:
            blended_rate = item_rate
            blend_source = f"item_only({item_rate:.1%},n={sample_n})"
    elif mid_rate is not None:
        blended_rate = mid_rate
        blend_source = f"mid_only({mid_rate:.1%})"
    else:
        return 1.0  # 데이터 없으면 감량 없음

    # 연속 함수: max(FLOOR, 1.0 - rate * MULTIPLIER)
    coef = max(DISUSE_COEF_FLOOR, 1.0 - blended_rate * DISUSE_COEF_MULTIPLIER)

    # 하한 도달 시 경고 로그 (과도한 감량 진단용)
    if coef <= DISUSE_COEF_FLOOR:
        logger.warning(
            f"[동적폐기계수] {item_cd} (mid={mid_cd}): coef={DISUSE_COEF_FLOOR}(하한) "
            f"blended_rate={blended_rate:.1%} [{blend_source}] "
            f"→ 예측값 {(1.0 - DISUSE_COEF_FLOOR)*100:.0f}% 감량됨. "
            f"배치수({sample_n})가 적으면 과도한 감량 가능"
        )
    elif coef < 0.8:
        logger.info(
            f"[동적폐기계수] {item_cd} (mid={mid_cd}): coef={coef:.3f} "
            f"blended_rate={blended_rate:.1%} [{blend_source}]"
        )

    return round(coef, 3)


def analyze_food_expiry_pattern(
    item_cd: str,
    mid_cd: str,
    db_path: Optional[str] = None,
    store_id: Optional[str] = None
) -> FoodExpiryResult:
    """
    푸드류 유통기한 기반 패턴 분석

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드
        db_path: DB 경로 (기본: bgf_sales.db)
        store_id: 점포 코드 (None이면 전체 점포)

    Returns:
        FoodExpiryResult: 분석 결과 데이터클래스
    """
    config = FOOD_EXPIRY_SAFETY_CONFIG
    if db_path is None:
        db_path = _get_db_path(store_id)

    # 유통기한 조회
    expiration_days, data_source = get_food_expiration_days(item_cd, mid_cd, db_path)

    # 유통기한 그룹 분류
    expiry_group, group_cfg = get_food_expiry_group(expiration_days)
    safety_days = group_cfg["safety_days"]
    description = group_cfg["description"]

    # 보정값 우선 적용
    try:
        from src.prediction.food_waste_calibrator import get_calibrated_food_params
        cal = get_calibrated_food_params(mid_cd, store_id)
        if cal is not None:
            safety_days = cal.safety_days
    except Exception:
        pass

    # 일평균 판매량 조회 (최근 FOOD_ANALYSIS_DAYS일)
    analysis_days = FOOD_ANALYSIS_DAYS
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            cursor = conn.cursor()
            if store_id:
                cursor.execute("""
                    SELECT COUNT(*), SUM(sale_qty)
                    FROM daily_sales
                    WHERE item_cd = ? AND store_id = ?
                    AND sales_date >= date('now', '-' || ? || ' days')
                """, (item_cd, store_id, analysis_days))
            else:
                cursor.execute("""
                    SELECT COUNT(*), SUM(sale_qty)
                    FROM daily_sales
                    WHERE item_cd = ?
                    AND sales_date >= date('now', '-' || ? || ' days')
                """, (item_cd, analysis_days))
            row = cursor.fetchone()

            actual_data_days = row[0] if row and row[0] else 0
            total_sales = row[1] if row and row[1] else 0
            # 일평균 계산:
            # - 신규 상품 (7일 미만): 실제 데이터일수로 나눔 (과소평가 방지)
            # - 전환 구간 (7~13일): 선형 블렌딩 (급변 방지)
            # - 기존 상품 (14일 이상): 전체 분석 기간으로 나눔 (간헐적 판매 과대추정 방지)
            if total_sales > 0:
                if actual_data_days < 7:
                    daily_avg = total_sales / max(actual_data_days, 1)
                elif actual_data_days < 14:
                    short_avg = total_sales / actual_data_days
                    long_avg = total_sales / analysis_days
                    blend_ratio = (actual_data_days - 7) / 7.0
                    daily_avg = short_avg * (1 - blend_ratio) + long_avg * blend_ratio
                else:
                    daily_avg = total_sales / analysis_days
            else:
                daily_avg = 0
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[푸드] 판매 데이터 조회 실패 ({item_cd}): {e}")
        actual_data_days = 0
        daily_avg = 0

    # 안전재고 계산
    safety_stock = daily_avg * safety_days

    return FoodExpiryResult(
        item_cd=item_cd,
        mid_cd=mid_cd,
        expiration_days=expiration_days,
        expiry_group=expiry_group,
        safety_days=safety_days,
        data_source=data_source,
        daily_avg=round(daily_avg, 2),
        actual_data_days=actual_data_days,
        safety_stock=round(safety_stock, 2),
        description=description,
    )


def calculate_food_dynamic_safety(
    item_cd: str,
    mid_cd: str,
    daily_avg: Optional[float] = None,
    disuse_rate: Optional[float] = None,
    db_path: Optional[str] = None,
    store_id: Optional[str] = None
) -> Tuple[float, Optional[FoodExpiryResult]]:
    """
    푸드류 동적 안전재고 계산

    공식: 일평균 × 안전재고일수(유통기한별) × 폐기율계수

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드
        daily_avg: 일평균 판매량 (None이면 DB에서 조회)
        disuse_rate: 폐기율 (None이면 계수 1.0)
        db_path: DB 경로

    Returns:
        (최종 안전재고, 패턴 분석 결과)
    """
    config = FOOD_EXPIRY_SAFETY_CONFIG

    if not config["enabled"]:
        # 비활성화 시 기본값 반환
        default_safety = (daily_avg or 0) * config["default_safety_days"]
        return default_safety, None

    # 패턴 분석
    pattern = analyze_food_expiry_pattern(item_cd, mid_cd, db_path, store_id=store_id)

    # 일평균 재설정 (파라미터로 주어지면 덮어쓰기)
    if daily_avg is not None:
        pattern = FoodExpiryResult(
            item_cd=pattern.item_cd,
            mid_cd=pattern.mid_cd,
            expiration_days=pattern.expiration_days,
            expiry_group=pattern.expiry_group,
            safety_days=pattern.safety_days,
            data_source=pattern.data_source,
            daily_avg=round(daily_avg, 2),
            actual_data_days=pattern.actual_data_days,
            safety_stock=round(daily_avg * pattern.safety_days, 2),
            description=pattern.description,
        )

    # 폐기율 계수 적용 (동적 함수 우선, disuse_rate 직접 전달 시 연속 공식 사용)
    if disuse_rate is not None:
        disuse_coef = max(DISUSE_COEF_FLOOR, 1.0 - disuse_rate * DISUSE_COEF_MULTIPLIER)
    else:
        disuse_coef = 1.0
    final_safety = pattern.safety_stock * disuse_coef

    return round(final_safety, 2), pattern


def get_delivery_waste_adjustment(
    item_cd: str,
    item_nm: str,
    db_path: Optional[str] = None,
    store_id: Optional[str] = None
) -> float:
    """
    차수별 폐기 실적 기반 발주량 조정 계수

    order_tracking에서 해당 차수(1차/2차)의 과거 폐기 데이터를 분석하여,
    폐기 비율에 따라 발주량 축소 계수를 반환.

    Args:
        item_cd: 상품코드
        item_nm: 상품명 (끝자리로 차수 판별)
        db_path: DB 경로
        store_id: 점포 코드 (None이면 전체 점포)

    Returns:
        조정 계수 (1.0=기본, <1.0=축소)
    """
    if db_path is None:
        db_path = _get_db_path(store_id)

    # 차수 판별
    if not item_nm or not item_nm.strip():
        return 1.0
    last_char = item_nm.strip()[-1]
    if last_char not in ("1", "2"):
        return 1.0
    delivery_type = f"{last_char}차"

    try:
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            cursor = conn.cursor()

            # 해당 차수의 최근 N일간 폐기 실적
            if store_id:
                cursor.execute("""
                    SELECT
                        COALESCE(SUM(order_qty), 0) as total_ordered,
                        COALESCE(SUM(remaining_qty), 0) as total_wasted
                    FROM order_tracking
                    WHERE item_cd = ?
                    AND delivery_type = ?
                    AND store_id = ?
                    AND status IN ('expired', 'disposed')
                    AND order_date >= date('now', '-' || ? || ' days')
                """, (item_cd, delivery_type, store_id, FOOD_ANALYSIS_DAYS))
            else:
                cursor.execute("""
                    SELECT
                        COALESCE(SUM(order_qty), 0) as total_ordered,
                        COALESCE(SUM(remaining_qty), 0) as total_wasted
                    FROM order_tracking
                    WHERE item_cd = ?
                    AND delivery_type = ?
                    AND status IN ('expired', 'disposed')
                    AND order_date >= date('now', '-' || ? || ' days')
                """, (item_cd, delivery_type, FOOD_ANALYSIS_DAYS))

            row = cursor.fetchone()

            if not row or row[0] == 0:
                return 1.0

            total_ordered = row[0]
            total_wasted = row[1]
            waste_rate = total_wasted / total_ordered

            # 폐기율 구간별 조정 계수 (상수 사용)
            if waste_rate < DELIVERY_WASTE_COEFFICIENT["threshold_10"][0]:
                return DELIVERY_WASTE_COEFFICIENT["threshold_10"][1]
            elif waste_rate < DELIVERY_WASTE_COEFFICIENT["threshold_20"][0]:
                return DELIVERY_WASTE_COEFFICIENT["threshold_20"][1]
            elif waste_rate < DELIVERY_WASTE_COEFFICIENT["threshold_30"][0]:
                return DELIVERY_WASTE_COEFFICIENT["threshold_30"][1]
            elif waste_rate < DELIVERY_WASTE_COEFFICIENT["threshold_50"][0]:
                return DELIVERY_WASTE_COEFFICIENT["threshold_50"][1]
            else:
                return DELIVERY_WASTE_COEFFICIENT["over_50"][1]
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[푸드] 폐기 계수 조회 실패: {e}")
        return 1.0


# 기온×푸드 교차 효과 계수
# 기온 구간별 푸드 카테고리 수요 보정 (편의점 실측 패턴 기반)
# 키: (기온하한, 기온상한), 값: {mid_cd: 계수}
FOOD_WEATHER_CROSS_COEFFICIENTS = {
    # 혹한기 (0도 이하): 도시락↑(따뜻한 식사), 샌드위치/김밥↓(차가운 식사)
    (-99, 0): {
        "001": 1.10,  # 도시락 +10%
        "002": 0.95,  # 주먹밥 -5%
        "003": 0.90,  # 김밥 -10%
        "004": 0.88,  # 샌드위치 -12%
        "005": 1.05,  # 햄버거 +5%
        "012": 1.05,  # 빵 +5%
    },
    # 한랭기 (0~10도): 도시락 소폭↑, 김밥/샌드위치 소폭↓
    (0, 10): {
        "001": 1.05,
        "002": 0.98,
        "003": 0.95,
        "004": 0.95,
        "005": 1.02,
        "012": 1.02,
    },
    # 적온기 (10~25도): 모든 카테고리 정상
    (10, 25): {},  # 모두 1.0
    # 고온기 (25~33도): 김밥↑(가벼운 식사), 도시락↓(무거운 식사)
    (25, 33): {
        "001": 0.95,  # 도시락 -5%
        "002": 1.02,  # 주먹밥 +2%
        "003": 1.05,  # 김밥 +5%
        "004": 1.05,  # 샌드위치 +5%
        "005": 0.98,
        "012": 0.98,
    },
    # 폭염기 (33도 이상): 전체 식사 수요 감소 (더위로 식욕↓)
    (33, 99): {
        "001": 0.88,
        "002": 0.92,
        "003": 0.95,
        "004": 0.95,
        "005": 0.90,
        "012": 0.92,
    },
}


def get_food_weather_cross_coefficient(mid_cd: str, temperature: Optional[float]) -> float:
    """기온×푸드 교차 효과 계수 반환

    Args:
        mid_cd: 중분류 코드
        temperature: 기온 (섭씨). None이면 1.0 반환.

    Returns:
        교차 효과 계수 (0.88 ~ 1.10)
    """
    if temperature is None or mid_cd not in FOOD_CATEGORIES:
        return 1.0

    for (temp_min, temp_max), coefs in FOOD_WEATHER_CROSS_COEFFICIENTS.items():
        if temp_min <= temperature < temp_max:
            return coefs.get(mid_cd, 1.0)

    return 1.0


def get_food_weekday_coefficient(
    mid_cd: str,
    weekday: int,
    store_id: Optional[str] = None,
    db_path: Optional[str] = None,
    lookback_days: int = 28,
) -> float:
    """
    푸드 카테고리의 DB 기반 동적 요일 계수

    최근 N일 daily_sales에서 요일별 평균 판매량을 계산하고,
    전체 평균 대비 해당 요일의 비율을 계수로 반환한다.

    Args:
        mid_cd: 중분류 코드 (푸드 카테고리만)
        weekday: 요일 (0=일, 1=월, ..., 6=토) — SQLite strftime 기준
        store_id: 점포 코드
        db_path: DB 경로
        lookback_days: 조회 기간 (기본 28일 = 4주)

    Returns:
        요일 계수 (1.0 = 평균). 데이터 부족 시 1.0.
    """
    if mid_cd not in FOOD_CATEGORIES:
        return 1.0

    if db_path is None:
        db_path = _get_db_path(store_id)

    try:
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            cursor = conn.cursor()

            # 요일별 총 판매량 집계 (4주)
            if store_id:
                cursor.execute("""
                    SELECT
                        CAST(strftime('%w', sales_date) AS INTEGER) as wd,
                        SUM(sale_qty) as total_qty,
                        COUNT(DISTINCT sales_date) as day_cnt
                    FROM daily_sales
                    WHERE mid_cd = ? AND store_id = ?
                    AND sales_date >= date('now', '-' || ? || ' days')
                    GROUP BY wd
                """, (mid_cd, store_id, lookback_days))
            else:
                cursor.execute("""
                    SELECT
                        CAST(strftime('%w', sales_date) AS INTEGER) as wd,
                        SUM(sale_qty) as total_qty,
                        COUNT(DISTINCT sales_date) as day_cnt
                    FROM daily_sales
                    WHERE mid_cd = ?
                    AND sales_date >= date('now', '-' || ? || ' days')
                    GROUP BY wd
                """, (mid_cd, lookback_days))

            rows = cursor.fetchall()
        finally:
            conn.close()

        if len(rows) < 5:
            # 5개 요일 미만 데이터면 의미 없음
            return 1.0

        # 요일별 일평균 계산
        weekday_avgs = {}
        for wd, total_qty, day_cnt in rows:
            if day_cnt > 0:
                weekday_avgs[wd] = (total_qty or 0) / day_cnt

        if not weekday_avgs:
            return 1.0

        # 전체 일평균
        overall_avg = sum(weekday_avgs.values()) / len(weekday_avgs)
        if overall_avg <= 0:
            return 1.0

        target_avg = weekday_avgs.get(weekday, overall_avg)
        raw_coef = target_avg / overall_avg

        # 안전 범위: 0.80 ~ 1.25 (과도한 변동 방지)
        return round(max(0.80, min(1.25, raw_coef)), 3)

    except Exception as e:
        logger.debug(f"[푸드요일계수] DB 조회 실패 ({mid_cd}): {e}")
        return 1.0


def get_safety_stock_with_food_pattern(
    mid_cd: str,
    daily_avg: float,
    item_cd: Optional[str] = None,
    disuse_rate: Optional[float] = None,
    store_id: Optional[str] = None,
    db_path: Optional[str] = None
) -> Tuple[float, Optional[FoodExpiryResult]]:
    """
    안전재고 계산 (푸드류 유통기한 패턴 포함)

    Args:
        mid_cd: 중분류 코드
        daily_avg: 일평균 판매량
        item_cd: 상품코드
        disuse_rate: 폐기율
        store_id: 점포 코드 (None이면 전체 점포)
        db_path: DB 경로 (None이면 store_id 기반 자동 결정)

    Returns:
        (안전재고_개수, 푸드_패턴_정보)
        - 푸드류가 아니면 패턴_정보는 None
    """
    # 푸드류가 아니면 기본값 반환
    if not is_food_category(mid_cd):
        safety_days = FOOD_EXPIRY_SAFETY_CONFIG["default_safety_days"]
        return daily_avg * safety_days, None

    # 푸드류: 동적 안전재고 계산
    if item_cd and FOOD_EXPIRY_SAFETY_CONFIG["enabled"]:
        final_safety, pattern = calculate_food_dynamic_safety(
            item_cd, mid_cd, daily_avg, disuse_rate,
            db_path=db_path, store_id=store_id
        )
        return final_safety, pattern

    # 기본값
    default_days = FOOD_EXPIRY_SAFETY_CONFIG["default_safety_days"]
    return daily_avg * default_days, None
