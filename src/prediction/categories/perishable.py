"""
소멸성 상품 카테고리 예측 모듈

소멸성 상품(013, 026, 046)의 동적 안전재고 계산:
- 대상: 떡(013), 과일/채소(026), 요구르트(046)
- 유통기한 기반: 초단기(1-3일) 0.3일, 단기(4-7일) 0.5일, 중기(8-14일) 0.8일, 장기(15일+) 1.0일
- 최대재고: 유통기한일 x 일평균 (최소 3일치)
- 요일계수: DB 학습 기반 (데이터 부족 시 DEFAULT_WEEKDAY_COEF 사용)

공식: 일평균 x 안전재고일수(유통기한별) x 요일계수
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Tuple, List

from src.utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# 소멸성 상품 설정
# =============================================================================
PERISHABLE_CATEGORIES = ["013", "026", "046"]
# 013: 떡, 026: 과일/채소, 046: 요구르트

# 소멸성 상품 기본 요일별 계수
# 0=월, 1=화, 2=수, 3=목, 4=금, 5=토, 6=일 (Python weekday 기준)
DEFAULT_WEEKDAY_COEF = {
    0: 1.00,  # 월
    1: 1.00,  # 화
    2: 1.00,  # 수
    3: 1.00,  # 목
    4: 1.00,  # 금
    5: 1.05,  # 토 (소폭 증가)
    6: 1.05,  # 일 (소폭 증가)
}

# 유통기한별 안전재고일수
PERISHABLE_EXPIRY_CONFIG = {
    "ultra_short": {   # 1-3일 (초단기)
        "min_days": 1,
        "max_days": 3,
        "safety_days": 0.3,
        "description": "초단기 유통 (떡, 일부 과일)"
    },
    "short": {         # 4-7일 (단기)
        "min_days": 4,
        "max_days": 7,
        "safety_days": 0.5,
        "description": "단기 유통 (요구르트, 채소류)"
    },
    "medium": {        # 8-14일 (중기)
        "min_days": 8,
        "max_days": 14,
        "safety_days": 0.8,
        "description": "중기 유통 (일부 과일, 포장 요구르트)"
    },
    "long": {          # 15일+ (장기)
        "min_days": 15,
        "max_days": 9999,
        "safety_days": 1.0,
        "description": "장기 유통 (포장 과일, 건조 떡)"
    },
}

# 소멸성 상품 동적 안전재고 설정
PERISHABLE_DYNAMIC_SAFETY_CONFIG = {
    "target_categories": ["013", "026", "046"],
    "enabled": True,
    "analysis_days": 30,
    "min_data_days": 14,
    "default_safety_days": 0.5,
    "max_stock_enabled": True,
}

# 카테고리별 기본 유통기한 (DB에 없는 경우 fallback)
PERISHABLE_EXPIRY_FALLBACK = {
    "013": 3,    # 떡: 3일
    "026": 5,    # 과일/채소: 5일
    "046": 10,   # 요구르트: 10일
    "default": 7,
}

# 최소 max_stock 일수 (유통기한이 매우 짧더라도 최소 3일치)
MIN_MAX_STOCK_DAYS = 3


def _get_db_path() -> str:
    """DB 경로 반환

    Returns:
        bgf_sales.db의 절대 경로 문자열
    """
    return str(Path(__file__).parent.parent.parent.parent / "data" / "bgf_sales.db")


@dataclass
class PerishablePatternResult:
    """소멸성 상품 패턴 분석 결과"""
    item_cd: str
    mid_cd: str                    # 중분류 코드

    # 데이터 기간 정보
    actual_data_days: int          # 실제 데이터 일수
    analysis_days: int             # 분석 기준 일수
    has_enough_data: bool          # 최소 데이터 충족 여부

    # 판매량 정보
    total_sales: int               # 총 판매량
    daily_avg: float               # 일평균 판매량

    # 유통기한 정보
    expiration_days: int           # 유통기한 (일)
    expiry_group: str              # 그룹 (ultra_short, short, medium, long)
    expiry_data_source: str        # 'db' 또는 'fallback'

    # 요일 정보
    order_weekday: int             # 발주 요일 (0=월, 6=일)
    weekday_coef: float            # 적용된 요일 계수
    weekday_coef_source: str       # 'learned' 또는 'default'

    # 안전재고
    safety_days: float             # 안전재고 일수
    safety_stock: float            # 안전재고 수량

    # 재고 상한선
    max_stock: float               # 최대 재고 상한선 (유통기한일 x 일평균, 최소 3일치)
    current_stock: int             # 현재 재고
    pending_qty: int               # 미입고 수량

    # 발주 스킵
    skip_order: bool               # 발주 스킵 여부
    skip_reason: str               # 스킵 사유

    # 최종 결과
    final_safety_stock: float      # 최종 안전재고
    description: str               # 유통기한 그룹 설명


def is_perishable_category(mid_cd: str) -> bool:
    """소멸성 상품 카테고리 여부 확인

    Args:
        mid_cd: 중분류 코드

    Returns:
        소멸성 상품 카테고리(013, 026, 046)이면 True
    """
    return mid_cd in PERISHABLE_CATEGORIES


def _learn_weekday_pattern(mid_cd: str, db_path: str = None, min_data_days: int = 14, store_id: Optional[str] = None) -> list:
    """매장 판매 데이터에서 요일별 계수를 학습

    최근 30일 데이터에서 요일별 평균 판매량을 계산하여 계수 산출.
    데이터 부족 시 DEFAULT_WEEKDAY_COEF 반환.

    Args:
        mid_cd: 중분류 코드
        db_path: DB 경로 (None이면 자동 탐색)
        min_data_days: 최소 필요 데이터 일수 (기본 14일)
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        [월, 화, 수, 목, 금, 토, 일] 7개 계수 (Python weekday 순서)
    """
    if db_path is None:
        db_path = _get_db_path()

    default_coefs = [DEFAULT_WEEKDAY_COEF[i] for i in range(7)]

    try:
        conn = sqlite3.connect(db_path, timeout=30)
        cursor = conn.cursor()

        # 최근 30일 데이터에서 요일별 판매량 집계
        if store_id:
            cursor.execute("""
                SELECT
                    CAST(strftime('%w', sales_date) AS INTEGER) as dow,
                    COUNT(DISTINCT sales_date) as day_count,
                    SUM(sale_qty) as total_qty
                FROM daily_sales
                WHERE mid_cd = ?
                AND store_id = ?
                AND sales_date >= date('now', '-30 days')
                GROUP BY dow
                ORDER BY dow
            """, (mid_cd, store_id))
        else:
            cursor.execute("""
                SELECT
                    CAST(strftime('%w', sales_date) AS INTEGER) as dow,
                    COUNT(DISTINCT sales_date) as day_count,
                    SUM(sale_qty) as total_qty
                FROM daily_sales
                WHERE mid_cd = ?
                AND sales_date >= date('now', '-30 days')
                GROUP BY dow
                ORDER BY dow
            """, (mid_cd,))

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return default_coefs

        # 총 데이터 일수 확인
        total_days = sum(row[1] for row in rows)
        if total_days < min_data_days:
            return default_coefs

        # 요일별 평균 판매량 계산 (SQLite: 0=일, 1=월, ..., 6=토)
        weekday_avg = {}
        for row in rows:
            sqlite_dow = row[0]   # 0=일, 1=월, ..., 6=토
            day_count = row[1]
            total_qty = row[2] or 0
            avg_qty = total_qty / day_count if day_count > 0 else 0

            # SQLite weekday → Python weekday 변환 (0=일→6, 1=월→0, ..., 6=토→5)
            python_dow = (sqlite_dow - 1) % 7
            weekday_avg[python_dow] = avg_qty

        # 전체 평균 계산
        all_avgs = [v for v in weekday_avg.values() if v > 0]
        if not all_avgs:
            return default_coefs

        overall_avg = sum(all_avgs) / len(all_avgs)
        if overall_avg <= 0:
            return default_coefs

        # 계수 계산 (전체 평균 대비 비율, 0.5~2.5 범위로 클램프)
        learned_coefs = []
        for i in range(7):
            if i in weekday_avg and overall_avg > 0:
                raw_coef = weekday_avg[i] / overall_avg
                clamped_coef = max(0.5, min(2.5, raw_coef))
                learned_coefs.append(round(clamped_coef, 3))
            else:
                learned_coefs.append(DEFAULT_WEEKDAY_COEF[i])

        return learned_coefs

    except Exception as e:
        logger.warning(f"[신선식품] 요일 패턴 학습 실패 (mid_cd={mid_cd}): {e}")
        return default_coefs


def _get_perishable_expiry_group(expiration_days: int) -> Tuple[str, dict]:
    """유통기한으로 소멸성 상품 그룹 분류

    Args:
        expiration_days: 유통기한 (일)

    Returns:
        (그룹명, 그룹설정) 튜플
    """
    for group_name, group_cfg in PERISHABLE_EXPIRY_CONFIG.items():
        if group_cfg["min_days"] <= expiration_days <= group_cfg["max_days"]:
            return group_name, group_cfg

    # 범위 밖이면 long 그룹
    return "long", PERISHABLE_EXPIRY_CONFIG["long"]


def _get_expiration_days(item_cd: str, mid_cd: str, db_path: str = None) -> Tuple[int, str]:
    """유통기한 조회 (DB -> fallback)

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드
        db_path: DB 경로

    Returns:
        (유통기한_일수, 데이터소스) 튜플. 데이터소스는 'db' 또는 'fallback'
    """
    if db_path is None:
        db_path = _get_db_path()

    # 1. DB에서 조회 (product_details.expiration_days)
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT expiration_days
            FROM product_details
            WHERE item_cd = ?
        """, (item_cd,))
        row = cursor.fetchone()
        conn.close()

        if row and row[0] is not None and row[0] > 0:
            return int(row[0]), "db"
    except Exception as e:
        logger.warning(f"소멸성 상품 데이터 조회 실패: {e}")

    # 2. Fallback (카테고리별 기본값)
    fallback_days = PERISHABLE_EXPIRY_FALLBACK.get(mid_cd, PERISHABLE_EXPIRY_FALLBACK["default"])
    return fallback_days, "fallback"


def analyze_perishable_pattern(
    item_cd: str,
    mid_cd: str,
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None
) -> PerishablePatternResult:
    """소멸성 상품 패턴 분석

    유통기한 기반 안전재고와 요일별 학습 계수를 결합하여 분석.

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드
        db_path: DB 경로 (None이면 자동 탐색)
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        PerishablePatternResult: 분석 결과 데이터클래스
    """
    config = PERISHABLE_DYNAMIC_SAFETY_CONFIG
    if db_path is None:
        db_path = _get_db_path()

    analysis_days = config["analysis_days"]
    min_data_days = config["min_data_days"]

    # === 유통기한 조회 ===
    expiration_days, expiry_data_source = _get_expiration_days(item_cd, mid_cd, db_path)

    # === 유통기한 그룹 분류 ===
    expiry_group, group_cfg = _get_perishable_expiry_group(expiration_days)
    safety_days = group_cfg["safety_days"]
    description = group_cfg["description"]

    # === 요일 계수 학습 ===
    learned_coefs = _learn_weekday_pattern(mid_cd, db_path, min_data_days, store_id=store_id)
    order_weekday = datetime.now().weekday()

    # 학습 성공 여부 판단 (기본값과 다르면 학습된 것)
    default_list = [DEFAULT_WEEKDAY_COEF[i] for i in range(7)]
    weekday_coef_source = "learned" if learned_coefs != default_list else "default"
    weekday_coef = learned_coefs[order_weekday]

    # === DB에서 판매 데이터 조회 ===
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        cursor = conn.cursor()

        if store_id:
            cursor.execute("""
                SELECT COUNT(DISTINCT sales_date) as data_days,
                       COALESCE(SUM(sale_qty), 0) as total_sales
                FROM daily_sales
                WHERE item_cd = ?
                AND store_id = ?
                AND sales_date >= date('now', '-' || ? || ' days')
            """, (item_cd, store_id, analysis_days))
        else:
            cursor.execute("""
                SELECT COUNT(DISTINCT sales_date) as data_days,
                       COALESCE(SUM(sale_qty), 0) as total_sales
                FROM daily_sales
                WHERE item_cd = ?
                AND sales_date >= date('now', '-' || ? || ' days')
            """, (item_cd, analysis_days))

        row = cursor.fetchone()
        conn.close()

        actual_data_days = row[0] or 0
        total_sales = row[1] or 0
    except Exception as e:
        logger.warning(f"[신선식품] 판매 데이터 조회 실패 ({item_cd}): {e}")
        actual_data_days = 0
        total_sales = 0

    has_enough_data = actual_data_days >= min_data_days

    # === 일평균 계산 ===
    daily_avg = (total_sales / actual_data_days) if actual_data_days > 0 else 0.0

    # === 안전재고 계산 (일평균 x 안전재고일수 x 요일계수) ===
    safety_stock = daily_avg * safety_days * weekday_coef

    # === 최대 재고 상한선 (유통기한일 x 일평균, 최소 3일치) ===
    max_stock_by_expiry = expiration_days * daily_avg
    max_stock_minimum = MIN_MAX_STOCK_DAYS * daily_avg
    max_stock = max(max_stock_by_expiry, max_stock_minimum) if daily_avg > 0 else 0

    # === 발주 스킵 판단 ===
    skip_order = False
    skip_reason = ""

    if config["max_stock_enabled"] and max_stock > 0:
        if current_stock + pending_qty >= max_stock:
            skip_order = True
            skip_reason = (
                f"상한선 초과 ({current_stock}+{pending_qty} >= {max_stock:.0f})"
            )

    return PerishablePatternResult(
        item_cd=item_cd,
        mid_cd=mid_cd,
        actual_data_days=actual_data_days,
        analysis_days=analysis_days,
        has_enough_data=has_enough_data,
        total_sales=total_sales,
        daily_avg=round(daily_avg, 2),
        expiration_days=expiration_days,
        expiry_group=expiry_group,
        expiry_data_source=expiry_data_source,
        order_weekday=order_weekday,
        weekday_coef=round(weekday_coef, 3),
        weekday_coef_source=weekday_coef_source,
        safety_days=safety_days,
        safety_stock=round(safety_stock, 2),
        max_stock=round(max_stock, 2),
        current_stock=current_stock,
        pending_qty=pending_qty,
        skip_order=skip_order,
        skip_reason=skip_reason,
        final_safety_stock=round(safety_stock, 2),
        description=description,
    )


def calculate_perishable_dynamic_safety(
    item_cd: str,
    mid_cd: str,
    daily_avg: Optional[float] = None,
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None
) -> Tuple[float, Optional[PerishablePatternResult]]:
    """소멸성 상품 동적 안전재고 계산

    유통기한 그룹별 안전재고일수와 DB 학습 요일계수를 결합하여 계산.

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드
        daily_avg: 일평균 판매량 (None이면 DB에서 조회)
        db_path: DB 경로
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        (최종 안전재고, 패턴 분석 결과) 튜플
    """
    config = PERISHABLE_DYNAMIC_SAFETY_CONFIG

    if not config["enabled"]:
        # 비활성화 시 기본값 반환
        default_safety = (daily_avg or 0) * config["default_safety_days"]
        return default_safety, None

    # 패턴 분석
    pattern = analyze_perishable_pattern(
        item_cd, mid_cd, db_path, current_stock, pending_qty, store_id=store_id
    )

    return pattern.final_safety_stock, pattern


def get_safety_stock_with_perishable_pattern(
    mid_cd: str,
    daily_avg: float,
    expiration_days: int,
    item_cd: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None
) -> Tuple[float, Optional[PerishablePatternResult]]:
    """안전재고 계산 (소멸성 상품 동적 패턴 포함)

    통합 진입점. 소멸성 상품이면 유통기한+요일계수 기반 동적 안전재고를 계산하고,
    아니면 기본 로직으로 위임.

    Args:
        mid_cd: 중분류 코드
        daily_avg: 일평균 판매량
        expiration_days: 유통기한 일수
        item_cd: 상품코드
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        (안전재고_개수, 소멸성_패턴_정보) 튜플
        - 소멸성 상품이 아니면 패턴_정보는 None
    """
    # 소멸성 상품이 아니면 기본값 반환
    if not is_perishable_category(mid_cd):
        from .default import get_safety_stock_days
        safety_days = get_safety_stock_days(mid_cd, daily_avg, expiration_days)
        return daily_avg * safety_days, None

    # 소멸성 상품: 동적 안전재고 계산
    if item_cd and PERISHABLE_DYNAMIC_SAFETY_CONFIG["enabled"]:
        final_safety, pattern = calculate_perishable_dynamic_safety(
            item_cd, mid_cd, daily_avg, None, current_stock, pending_qty, store_id=store_id
        )
        return final_safety, pattern

    # 기본값 (상품코드 없거나 비활성화 시)
    default_days = PERISHABLE_DYNAMIC_SAFETY_CONFIG["default_safety_days"]
    return daily_avg * default_days, None
