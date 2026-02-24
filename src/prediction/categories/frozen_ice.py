"""
냉동/아이스크림 카테고리 예측 모듈

냉동/아이스크림(021, 034, 100)의 동적 안전재고 계산:
- 계절 가중치: 여름(6-8월) 수요 급증, 겨울(12-2월) 수요 감소
- 요일 패턴: 주말 증가 (토 1.30배, 일 1.40배)
- 안전재고: 여름 2.0일, 겨울 1.0일, 그 외 1.5일
- 상한선: 일평균 × 7.0일 (냉동고 공간 제약)

공식: 일평균 × 요일계수 × 계절계수 (예측), 일평균 × 안전재고일수 (안전재고)
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# 냉동/아이스크림 설정
# =============================================================================
FROZEN_ICE_CATEGORIES = ["021", "034", "100"]
# 021: 일반아이스크림, 034: 냉동즉석식, 100: RI아이스크림

# 냉동/아이스크림 요일별 기본 계수
# 0=월, 1=화, 2=수, 3=목, 4=금, 5=토, 6=일 (Python weekday 기준)
FROZEN_ICE_WEEKDAY_COEF = {
    0: 0.90,  # 월
    1: 0.90,  # 화
    2: 0.95,  # 수
    3: 0.95,  # 목
    4: 1.00,  # 금
    5: 1.30,  # 토 (증가)
    6: 1.40,  # 일 (최고)
}

# 월별 기본 계절 계수 (학습 데이터 부족 시 사용)
DEFAULT_SEASONAL_COEF = {
    1: 0.60,   # 1월 (겨울 최저)
    2: 0.65,   # 2월
    3: 0.80,   # 3월
    4: 0.95,   # 4월
    5: 1.15,   # 5월
    6: 1.40,   # 6월 (여름 시작)
    7: 1.60,   # 7월 (여름 최고)
    8: 1.50,   # 8월
    9: 1.20,   # 9월
    10: 0.95,  # 10월
    11: 0.75,  # 11월
    12: 0.60,  # 12월 (겨울)
}

# 냉동/아이스크림 안전재고 설정
FROZEN_SAFETY_CONFIG = {
    "default_safety_days": 1.5,      # 기본 안전재고 일수 (봄/가을)
    "summer_safety_days": 2.0,       # 여름(6-8월) 안전재고 일수 (품절 방지)
    "winter_safety_days": 1.0,       # 겨울(12-2월) 안전재고 일수 (재고 최소화)
    "summer_months": [6, 7, 8],      # 여름 해당 월
    "winter_months": [12, 1, 2],     # 겨울 해당 월
}

# 동적 안전재고 CONFIG
FROZEN_ICE_DYNAMIC_SAFETY_CONFIG = {
    "target_categories": ["021", "034", "100"],
    "enabled": True,
    "analysis_days": 30,
    "min_data_days": 14,
    "default_safety_days": 1.5,
    "max_stock_enabled": True,
    "max_stock_days": 7.0,
    "seasonal_enabled": True,
}


def _get_db_path() -> str:
    """DB 경로 반환

    Returns:
        bgf_sales.db 파일의 절대 경로 문자열
    """
    return str(Path(__file__).parent.parent.parent.parent / "data" / "bgf_sales.db")


@dataclass
class FrozenIcePatternResult:
    """냉동/아이스크림 패턴 분석 결과"""
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

    # 계절 정보
    current_month: int             # 현재 월
    seasonal_coef: float           # 적용된 계절 계수
    seasonal_source: str           # 계절 계수 출처 ('learned' 또는 'default')

    # 안전재고
    safety_days: float             # 안전재고 일수 (계절별 조정)
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


def is_frozen_ice_category(mid_cd: str) -> bool:
    """냉동/아이스크림 카테고리 여부 확인

    Args:
        mid_cd: 중분류 코드

    Returns:
        냉동/아이스크림 카테고리(021, 034, 100)이면 True
    """
    return mid_cd in FROZEN_ICE_CATEGORIES


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
            logger.debug(f"[냉동/아이스크림] mid_cd={mid_cd} 데이터 부족 ({total_days}일 < {min_data_days}일), 기본값 사용")
            return dict(FROZEN_ICE_WEEKDAY_COEF)

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
            return dict(FROZEN_ICE_WEEKDAY_COEF)

        overall_avg = sum(all_avgs) / len(all_avgs)
        if overall_avg <= 0:
            return dict(FROZEN_ICE_WEEKDAY_COEF)

        learned_coef = {}
        for wd in range(7):
            if wd in weekday_avg and weekday_avg[wd] > 0:
                coef = weekday_avg[wd] / overall_avg
                # 0.5 ~ 2.5 범위로 클램프
                coef = max(0.5, min(2.5, coef))
                learned_coef[wd] = round(coef, 2)
            else:
                learned_coef[wd] = FROZEN_ICE_WEEKDAY_COEF.get(wd, 1.0)

        logger.debug(f"[냉동/아이스크림] mid_cd={mid_cd} 요일계수 학습 완료: {learned_coef}")
        return learned_coef

    except Exception:
        logger.warning(f"[냉동/아이스크림] mid_cd={mid_cd} 요일 패턴 학습 실패, 기본값 사용")
        return dict(FROZEN_ICE_WEEKDAY_COEF)


def _learn_seasonal_pattern(mid_cd: str, db_path: str = None, store_id: Optional[str] = None) -> dict:
    """매장 데이터에서 월별 계절 패턴 학습

    최소 3개월 데이터 필요. 부족 시 DEFAULT_SEASONAL_COEF 반환.
    각 월의 일평균 판매량을 전체 일평균 대비 비율로 계산하여 계절 계수 생성.

    Args:
        mid_cd: 중분류 코드
        db_path: DB 경로 (None이면 자동 탐색)
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        {1: coef, 2: coef, ..., 12: coef} 월별 계절 계수 dict
    """
    if db_path is None:
        db_path = _get_db_path()

    try:
        conn = sqlite3.connect(db_path, timeout=30)
        cursor = conn.cursor()

        # 전체 기간의 월별 판매량 집계
        if store_id:
            cursor.execute("""
                SELECT
                    CAST(strftime('%m', sales_date) AS INTEGER) as month,
                    COUNT(DISTINCT sales_date) as days,
                    SUM(sale_qty) as total_qty
                FROM daily_sales ds
                JOIN products p ON ds.item_cd = p.item_cd
                WHERE p.mid_cd = ?
                AND ds.store_id = ?
                GROUP BY month
            """, (mid_cd, store_id))
        else:
            cursor.execute("""
                SELECT
                    CAST(strftime('%m', sales_date) AS INTEGER) as month,
                    COUNT(DISTINCT sales_date) as days,
                    SUM(sale_qty) as total_qty
                FROM daily_sales ds
                JOIN products p ON ds.item_cd = p.item_cd
                WHERE p.mid_cd = ?
                GROUP BY month
            """, (mid_cd,))

        rows = cursor.fetchall()
        conn.close()

        # 최소 3개월 데이터 확인
        months_with_data = len(rows)
        if months_with_data < 3:
            logger.debug(f"[냉동/아이스크림] mid_cd={mid_cd} 계절 데이터 부족 ({months_with_data}개월 < 3개월), 기본값 사용")
            return dict(DEFAULT_SEASONAL_COEF)

        # 월별 일평균 계산
        monthly_avg = {}
        for row in rows:
            month = row[0]
            days = row[1]
            total_qty = row[2] or 0
            monthly_avg[month] = total_qty / days if days > 0 else 0

        # 전체 일평균
        all_avgs = [v for v in monthly_avg.values() if v > 0]
        if not all_avgs:
            return dict(DEFAULT_SEASONAL_COEF)

        overall_avg = sum(all_avgs) / len(all_avgs)
        if overall_avg <= 0:
            return dict(DEFAULT_SEASONAL_COEF)

        # 월별 계수 계산 (전체 대비 비율)
        learned_seasonal = {}
        for month in range(1, 13):
            if month in monthly_avg and monthly_avg[month] > 0:
                coef = monthly_avg[month] / overall_avg
                # 0.3 ~ 3.0 범위로 클램프
                coef = max(0.3, min(3.0, coef))
                learned_seasonal[month] = round(coef, 2)
            else:
                # 데이터 없는 월은 기본값 사용
                learned_seasonal[month] = DEFAULT_SEASONAL_COEF[month]

        logger.debug(f"[냉동/아이스크림] mid_cd={mid_cd} 계절계수 학습 완료: {learned_seasonal}")
        return learned_seasonal

    except Exception:
        logger.warning(f"[냉동/아이스크림] mid_cd={mid_cd} 계절 패턴 학습 실패, 기본값 사용")
        return dict(DEFAULT_SEASONAL_COEF)


def _get_seasonal_safety_days(month: int) -> float:
    """현재 월에 따른 안전재고 일수 반환

    여름(6-8월)은 품절 방지를 위해 3.0일, 겨울(12-2월)은 재고 최소화를 위해 1.5일,
    그 외 봄/가을은 기본 2.0일 적용.

    Args:
        month: 현재 월 (1~12)

    Returns:
        안전재고 일수 (1.5, 2.0, 또는 3.0)
    """
    config = FROZEN_SAFETY_CONFIG
    if month in config["summer_months"]:
        return config["summer_safety_days"]
    elif month in config["winter_months"]:
        return config["winter_safety_days"]
    else:
        return config["default_safety_days"]


def analyze_frozen_ice_pattern(
    item_cd: str,
    mid_cd: str = None,
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None
) -> FrozenIcePatternResult:
    """냉동/아이스크림 상품 패턴 분석

    계절 가중치와 요일 패턴을 결합하여 냉동/아이스크림 상품의
    안전재고를 계산한다. 여름에는 품절 방지, 겨울에는 재고 최소화.

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드 (요일/계절 학습용, None이면 학습 생략)
        db_path: DB 경로 (None이면 자동 탐색)
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        FrozenIcePatternResult: 패턴 분석 결과 데이터클래스
    """
    config = FROZEN_ICE_DYNAMIC_SAFETY_CONFIG

    if db_path is None:
        db_path = _get_db_path()

    now = datetime.now()
    order_weekday = now.weekday()  # 0=월, 6=일
    current_month = now.month

    # 요일 계수 (학습 또는 기본값)
    if mid_cd:
        learned_weekday = _learn_weekday_pattern(mid_cd, db_path, config["min_data_days"], store_id=store_id)
    else:
        learned_weekday = FROZEN_ICE_WEEKDAY_COEF
    weekday_coef = learned_weekday.get(order_weekday, 1.0)

    # 계절 계수 (학습 또는 기본값)
    seasonal_source = "default"
    if config["seasonal_enabled"] and mid_cd:
        learned_seasonal = _learn_seasonal_pattern(mid_cd, db_path, store_id=store_id)
        if learned_seasonal != DEFAULT_SEASONAL_COEF:
            seasonal_source = "learned"
        seasonal_coef = learned_seasonal.get(current_month, 1.0)
    else:
        seasonal_coef = DEFAULT_SEASONAL_COEF.get(current_month, 1.0)

    # 안전재고 일수 (계절별 조정)
    safety_days = _get_seasonal_safety_days(current_month)

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

    # 최대 재고 상한선
    max_stock = daily_avg * config["max_stock_days"] if daily_avg > 0 else 0

    # 발주 스킵 판단
    skip_order = False
    skip_reason = ""

    if config["max_stock_enabled"] and max_stock > 0:
        if current_stock + pending_qty >= max_stock:
            skip_order = True
            skip_reason = f"상한선 초과 ({current_stock}+{pending_qty} >= {max_stock:.0f})"

    return FrozenIcePatternResult(
        item_cd=item_cd,
        actual_data_days=data_days,
        analysis_days=config["analysis_days"],
        has_enough_data=has_enough_data,
        total_sales=total_sales,
        daily_avg=round(daily_avg, 2),
        order_weekday=order_weekday,
        weekday_coef=weekday_coef,
        current_month=current_month,
        seasonal_coef=seasonal_coef,
        seasonal_source=seasonal_source,
        safety_days=safety_days,
        safety_stock=round(safety_stock, 2),
        max_stock=round(max_stock, 2),
        current_stock=current_stock,
        pending_qty=pending_qty,
        skip_order=skip_order,
        skip_reason=skip_reason,
        final_safety_stock=round(safety_stock, 2),
    )


def calculate_frozen_ice_dynamic_safety(
    item_cd: str,
    mid_cd: str = None,
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None
) -> Tuple[float, FrozenIcePatternResult]:
    """냉동/아이스크림 동적 안전재고 계산

    계절 가중치와 요일 패턴을 종합하여 동적 안전재고를 산출한다.

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드 (요일/계절 학습용)
        db_path: DB 경로
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        (안전재고_수량, 패턴_결과)
    """
    pattern = analyze_frozen_ice_pattern(
        item_cd, mid_cd, db_path, current_stock, pending_qty, store_id=store_id
    )
    return pattern.final_safety_stock, pattern


def get_safety_stock_with_frozen_ice_pattern(
    mid_cd: str,
    daily_avg: float,
    expiration_days: int,
    item_cd: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None
) -> Tuple[float, Optional[FrozenIcePatternResult]]:
    """안전재고 계산 (냉동/아이스크림 동적 패턴 포함)

    냉동/아이스크림 카테고리이면 계절 가중치와 요일 패턴 기반 동적 안전재고를 계산하고,
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
        (안전재고_개수, 냉동아이스크림_패턴_정보)
        - 냉동/아이스크림이 아니면 패턴_정보는 None
    """
    # 냉동/아이스크림이 아니면 기본값 반환
    if not is_frozen_ice_category(mid_cd):
        from .default import get_safety_stock_days
        safety_days = get_safety_stock_days(mid_cd, daily_avg, expiration_days)
        return daily_avg * safety_days, None

    # 냉동/아이스크림: 동적 안전재고 계산
    if item_cd and FROZEN_ICE_DYNAMIC_SAFETY_CONFIG["enabled"]:
        final_safety, pattern = calculate_frozen_ice_dynamic_safety(
            item_cd, mid_cd, None, current_stock, pending_qty, store_id=store_id
        )
        return final_safety, pattern

    # 기본값 (상품코드 없거나 비활성화 시)
    default_days = FROZEN_ICE_DYNAMIC_SAFETY_CONFIG["default_safety_days"]
    return daily_avg * default_days, None
