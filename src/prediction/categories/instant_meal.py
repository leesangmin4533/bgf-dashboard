"""
즉석식품 카테고리 예측 모듈

즉석식품(027, 028, 031, 033, 035)의 동적 안전재고 계산:
- 유통기한 그룹 기반: fresh(1-7일), chilled(8-30일), shelf_stable(31-180일), long_life(181일+)
- 같은 카테고리 내 유통기한 7일~480일 상품이 혼재하므로 상품별 분기가 핵심
- 요일 패턴: 평일 안정, 주말 약간 감소

공식: 일평균 × 안전재고일수(유통기한그룹별)
"""

import sqlite3
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# 즉석식품 설정
# =============================================================================
INSTANT_MEAL_CATEGORIES = ["027", "028", "031", "033", "035"]
# 027: 농산식재료, 028: 축수산식재료, 031: 반찬류, 033: 상온즉석식, 035: 냉장즉석식

# 즉석식품 요일별 기본 계수
# 0=월, 1=화, 2=수, 3=목, 4=금, 5=토, 6=일 (Python weekday 기준)
INSTANT_MEAL_WEEKDAY_COEF = {
    0: 1.00,  # 월
    1: 1.00,  # 화
    2: 1.05,  # 수
    3: 1.05,  # 목
    4: 1.00,  # 금
    5: 0.95,  # 토
    6: 0.95,  # 일
}

# 유통기한 그룹별 안전재고 설정
INSTANT_EXPIRY_GROUPS = {
    "fresh": {
        "min_days": 1,
        "max_days": 7,
        "safety_days": 0.5,
        "max_stock_rule": "expiry_based",  # max_stock = 유통기한 × 일평균
        "description": "초신선 (1-7일) - 농산물, 축수산, 냉장즉석",
    },
    "chilled": {
        "min_days": 8,
        "max_days": 30,
        "safety_days": 1.0,
        "max_stock_days": 5.0,
        "description": "냉장 (8-30일) - 반찬류, 소스류",
    },
    "shelf_stable": {
        "min_days": 31,
        "max_days": 180,
        "safety_days": 1.5,
        "max_stock_days": 7.0,
        "description": "상온 안정 (31-180일) - 상온즉석식, 통조림",
    },
    "long_life": {
        "min_days": 181,
        "max_days": 99999,
        "safety_days": 2.0,
        "max_stock_days": 7.0,
        "description": "장기 보관 (181일+) - 건조식품, 레토르트",
    },
}

# 동적 안전재고 CONFIG
INSTANT_MEAL_DYNAMIC_SAFETY_CONFIG = {
    "target_categories": ["027", "028", "031", "033", "035"],
    "enabled": True,
    "analysis_days": 30,
    "min_data_days": 14,
    "default_safety_days": 1.0,
    "max_stock_enabled": True,
}


def _get_db_path() -> str:
    """DB 경로 반환

    Returns:
        bgf_sales.db 파일의 절대 경로 문자열
    """
    return str(Path(__file__).parent.parent.parent.parent / "data" / "bgf_sales.db")


@dataclass
class InstantMealPatternResult:
    """즉석식품 패턴 분석 결과"""
    item_cd: str

    # 데이터 기간 정보
    actual_data_days: int          # 실제 데이터 일수
    analysis_days: int             # 분석 기준 일수
    has_enough_data: bool          # 최소 데이터 충족 여부

    # 판매량 정보
    total_sales: int               # 총 판매량
    daily_avg: float               # 일평균 판매량

    # 요일 정보
    order_weekday: int             # 발주 요일 (0=월, 6=일)
    weekday_coef: float            # 적용된 요일 계수

    # 유통기한 정보
    expiration_days: Optional[int]  # 유통기한 (일)
    expiry_group: str              # fresh/chilled/shelf_stable/long_life
    expiry_source: str             # 'db' 또는 'fallback'

    # 안전재고
    safety_days: float             # 안전재고 일수 (유통기한 그룹별)
    safety_stock: float            # 안전재고 수량

    # 재고 상한선
    max_stock: float               # 최대 재고 상한선
    current_stock: int             # 현재 재고
    pending_qty: int               # 미입고 수량

    # 발주 스킵
    skip_order: bool               # 발주 스킵 여부
    skip_reason: str               # 스킵 사유

    # 최종 결과
    final_safety_stock: float      # 최종 안전재고


def is_instant_meal_category(mid_cd: str) -> bool:
    """즉석식품 카테고리 여부 확인

    Args:
        mid_cd: 중분류 코드

    Returns:
        즉석식품 카테고리(027, 028, 031, 033, 035)이면 True
    """
    return mid_cd in INSTANT_MEAL_CATEGORIES


def _learn_weekday_pattern(mid_cd: str, db_path: str = None, min_data_days: int = 14, store_id: Optional[str] = None) -> dict:
    """매장 판매 데이터에서 요일별 계수를 학습

    최근 30일 데이터로 요일별 평균 산출, 전체평균 대비 비율 = 계수.
    0.5~2.5 클램프. min_data_days 미만이면 기본값 반환.

    Args:
        mid_cd: 중분류 코드
        db_path: DB 경로 (None이면 자동 탐색)
        min_data_days: 최소 데이터 일수 (미만이면 기본값 사용)
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        {0: coef, 1: coef, ..., 6: coef} Python weekday 순서 dict
    """
    if db_path is None:
        db_path = _get_db_path()

    try:
        conn = sqlite3.connect(db_path, timeout=30)
        cursor = conn.cursor()

        # 최근 30일간 해당 중분류의 요일별 판매량 집계
        if store_id:
            cursor.execute("""
                SELECT
                    CAST(strftime('%w', sales_date) AS INTEGER) as dow,
                    COUNT(DISTINCT sales_date) as days,
                    SUM(sale_qty) as total_qty
                FROM daily_sales ds
                JOIN products p ON ds.item_cd = p.item_cd
                WHERE p.mid_cd = ?
                AND ds.store_id = ?
                AND ds.sales_date >= date('now', '-30 days')
                GROUP BY dow
            """, (mid_cd, store_id))
        else:
            cursor.execute("""
                SELECT
                    CAST(strftime('%w', sales_date) AS INTEGER) as dow,
                    COUNT(DISTINCT sales_date) as days,
                    SUM(sale_qty) as total_qty
                FROM daily_sales ds
                JOIN products p ON ds.item_cd = p.item_cd
                WHERE p.mid_cd = ?
                AND ds.sales_date >= date('now', '-30 days')
                GROUP BY dow
            """, (mid_cd,))

        rows = cursor.fetchall()
        conn.close()

        # 데이터 일수 확인
        total_days = sum(row[1] for row in rows)
        if total_days < min_data_days:
            logger.debug(f"[즉석식품] mid_cd={mid_cd} 데이터 부족 ({total_days}일 < {min_data_days}일), 기본값 사용")
            return dict(INSTANT_MEAL_WEEKDAY_COEF)

        # 요일별 평균 판매량 계산 (SQLite: 0=일, 1=월, ..., 6=토)
        weekday_avg = {}
        for row in rows:
            sqlite_dow = row[0]  # 0=일, 1=월, ..., 6=토
            days = row[1]
            total_qty = row[2] or 0
            avg = total_qty / days if days > 0 else 0
            # SQLite 요일 → Python weekday 변환: 일(0)→6, 월(1)→0, ..., 토(6)→5
            python_weekday = (sqlite_dow - 1) % 7
            weekday_avg[python_weekday] = avg

        # 전체 평균 대비 비율 = 계수
        all_avgs = [v for v in weekday_avg.values() if v > 0]
        if not all_avgs:
            return dict(INSTANT_MEAL_WEEKDAY_COEF)

        overall_avg = sum(all_avgs) / len(all_avgs)
        if overall_avg <= 0:
            return dict(INSTANT_MEAL_WEEKDAY_COEF)

        learned_coef = {}
        for wd in range(7):
            if wd in weekday_avg and weekday_avg[wd] > 0:
                coef = weekday_avg[wd] / overall_avg
                # 0.5 ~ 2.5 범위로 클램프
                coef = max(0.5, min(2.5, coef))
                learned_coef[wd] = round(coef, 2)
            else:
                learned_coef[wd] = INSTANT_MEAL_WEEKDAY_COEF.get(wd, 1.0)

        logger.debug(f"[즉석식품] mid_cd={mid_cd} 요일계수 학습 완료: {learned_coef}")
        return learned_coef

    except Exception as e:
        logger.warning(f"[즉석식품] mid_cd={mid_cd} 요일 패턴 학습 실패, 기본값 사용: {e}")
        return dict(INSTANT_MEAL_WEEKDAY_COEF)


def _get_expiration_days(item_cd: str, db_path: str = None) -> Tuple[Optional[int], str]:
    """상품의 유통기한 조회 (DB → fallback)

    product_details 테이블에서 유통기한을 조회하고, 정보가 없으면 None 반환.

    Args:
        item_cd: 상품코드
        db_path: DB 경로 (None이면 자동 탐색)

    Returns:
        (유통기한_일수, 데이터소스) - 정보 없으면 (None, 'fallback')
    """
    if db_path is None:
        db_path = _get_db_path()

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
        logger.warning(f"즉석식품 데이터 조회 실패: {e}")

    return None, "fallback"


def _classify_expiry_group(expiration_days: Optional[int]) -> Tuple[str, dict]:
    """유통기한으로 그룹 분류

    유통기한 정보가 없으면 chilled 그룹으로 폴백한다.
    (같은 카테고리 내 7일~480일 상품이 혼재하므로 중간값인 chilled이 안전)

    Args:
        expiration_days: 유통기한 일수 (None이면 chilled 폴백)

    Returns:
        (그룹명, 그룹설정 dict)
    """
    if expiration_days is None:
        # 유통기한 정보 없으면 chilled 그룹으로 폴백
        return "chilled", INSTANT_EXPIRY_GROUPS["chilled"]

    for group_name, group_cfg in INSTANT_EXPIRY_GROUPS.items():
        if group_cfg["min_days"] <= expiration_days <= group_cfg["max_days"]:
            return group_name, group_cfg

    # 범위 밖이면 long_life
    return "long_life", INSTANT_EXPIRY_GROUPS["long_life"]


def _calculate_max_stock(
    expiry_group: str,
    group_cfg: dict,
    daily_avg: float,
    expiration_days: Optional[int]
) -> float:
    """유통기한 그룹별 최대 재고 상한선 계산

    fresh 그룹은 유통기한 기반(유통기한 x 일평균),
    나머지 그룹은 max_stock_days 기반(일수 x 일평균)으로 계산.

    Args:
        expiry_group: 유통기한 그룹명
        group_cfg: 그룹 설정 dict
        daily_avg: 일평균 판매량
        expiration_days: 유통기한 일수

    Returns:
        최대 재고 상한선 수량
    """
    if daily_avg <= 0:
        return 0

    if expiry_group == "fresh" and expiration_days is not None:
        # fresh: 유통기한 × 일평균
        return daily_avg * expiration_days
    else:
        # chilled, shelf_stable, long_life: max_stock_days × 일평균
        max_stock_days = group_cfg.get("max_stock_days", 5.0)
        return daily_avg * max_stock_days


def analyze_instant_meal_pattern(
    item_cd: str,
    mid_cd: str = None,
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None
) -> InstantMealPatternResult:
    """즉석식품 상품 패턴 분석

    상품별 유통기한을 조회하여 그룹을 분류하고, 그룹별로 차별화된
    안전재고와 최대 재고 상한선을 적용한다.

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드 (요일 학습용, None이면 학습 생략)
        db_path: DB 경로 (None이면 자동 탐색)
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        InstantMealPatternResult: 패턴 분석 결과 데이터클래스
    """
    config = INSTANT_MEAL_DYNAMIC_SAFETY_CONFIG

    if db_path is None:
        db_path = _get_db_path()

    from datetime import datetime
    order_weekday = datetime.now().weekday()  # 0=월, 6=일

    # 요일 계수 (학습 또는 기본값)
    if mid_cd:
        learned_weekday = _learn_weekday_pattern(mid_cd, db_path, config["min_data_days"], store_id=store_id)
    else:
        learned_weekday = INSTANT_MEAL_WEEKDAY_COEF
    weekday_coef = learned_weekday.get(order_weekday, 1.0)

    # 유통기한 조회 및 그룹 분류
    expiration_days, expiry_source = _get_expiration_days(item_cd, db_path)
    expiry_group, group_cfg = _classify_expiry_group(expiration_days)
    safety_days = group_cfg["safety_days"]

    # DB에서 일평균 판매량 조회
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
        """, (item_cd, store_id, config["analysis_days"]))
    else:
        cursor.execute("""
            SELECT COUNT(DISTINCT sales_date) as data_days,
                   COALESCE(SUM(sale_qty), 0) as total_sales
            FROM daily_sales
            WHERE item_cd = ?
            AND sales_date >= date('now', '-' || ? || ' days')
        """, (item_cd, config["analysis_days"]))

    row = cursor.fetchone()
    conn.close()

    data_days = row[0] or 0
    total_sales = row[1] or 0
    has_enough_data = data_days >= config["min_data_days"]

    # 일평균 계산
    daily_avg = (total_sales / data_days) if data_days > 0 else 0.0

    # 안전재고 수량
    safety_stock = daily_avg * safety_days

    # 최대 재고 상한선 (그룹별 차등)
    max_stock = _calculate_max_stock(expiry_group, group_cfg, daily_avg, expiration_days)

    # 발주 스킵 판단
    skip_order = False
    skip_reason = ""

    if config["max_stock_enabled"] and max_stock > 0:
        if current_stock + pending_qty >= max_stock:
            skip_order = True
            skip_reason = f"상한선 초과 ({current_stock}+{pending_qty} >= {max_stock:.0f})"

    return InstantMealPatternResult(
        item_cd=item_cd,
        actual_data_days=data_days,
        analysis_days=config["analysis_days"],
        has_enough_data=has_enough_data,
        total_sales=total_sales,
        daily_avg=round(daily_avg, 2),
        order_weekday=order_weekday,
        weekday_coef=weekday_coef,
        expiration_days=expiration_days,
        expiry_group=expiry_group,
        expiry_source=expiry_source,
        safety_days=safety_days,
        safety_stock=round(safety_stock, 2),
        max_stock=round(max_stock, 2),
        current_stock=current_stock,
        pending_qty=pending_qty,
        skip_order=skip_order,
        skip_reason=skip_reason,
        final_safety_stock=round(safety_stock, 2),
    )


def calculate_instant_meal_dynamic_safety(
    item_cd: str,
    mid_cd: str = None,
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None
) -> Tuple[float, InstantMealPatternResult]:
    """즉석식품 동적 안전재고 계산

    유통기한 그룹별로 차별화된 안전재고를 산출한다.

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드 (요일 학습용)
        db_path: DB 경로
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        (안전재고_수량, 패턴_결과)
    """
    pattern = analyze_instant_meal_pattern(
        item_cd, mid_cd, db_path, current_stock, pending_qty, store_id=store_id
    )
    return pattern.final_safety_stock, pattern


def get_safety_stock_with_instant_meal_pattern(
    mid_cd: str,
    daily_avg: float,
    expiration_days: int,
    item_cd: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None
) -> Tuple[float, Optional[InstantMealPatternResult]]:
    """안전재고 계산 (즉석식품 동적 패턴 포함)

    즉석식품 카테고리이면 유통기한 그룹 기반 동적 안전재고를 계산하고,
    해당 카테고리가 아니면 기본 안전재고를 반환한다.

    Args:
        mid_cd: 중분류 코드
        daily_avg: 일평균 판매량
        expiration_days: 유통기한 일수
        item_cd: 상품코드
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        (안전재고_개수, 즉석식품_패턴_정보)
        - 즉석식품이 아니면 패턴_정보는 None
    """
    # 즉석식품이 아니면 기본값 반환
    if not is_instant_meal_category(mid_cd):
        from .default import get_safety_stock_days
        safety_days = get_safety_stock_days(mid_cd, daily_avg, expiration_days)
        return daily_avg * safety_days, None

    # 즉석식품: 동적 안전재고 계산
    if item_cd and INSTANT_MEAL_DYNAMIC_SAFETY_CONFIG["enabled"]:
        final_safety, pattern = calculate_instant_meal_dynamic_safety(
            item_cd, mid_cd, None, current_stock, pending_qty, store_id=store_id
        )
        return final_safety, pattern

    # 기본값 (상품코드 없거나 비활성화 시)
    default_days = INSTANT_MEAL_DYNAMIC_SAFETY_CONFIG["default_safety_days"]
    return daily_avg * default_days, None
