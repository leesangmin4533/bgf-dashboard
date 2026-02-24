"""
라면 카테고리 예측 모듈

라면(006, 032)의 동적 안전재고 계산:
- 발주가능요일(orderable_day) 기반 발주간격으로 안전재고 결정
- 비발주일에는 발주 스킵
- 상한선: 일평균 x 4일치 (공간 제약)

공식: 일평균 x 발주간격(order_interval)
"""

import sqlite3
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Tuple

from .snack_confection import _is_orderable_today, _calculate_order_interval
from src.settings.constants import RAMEN_DEFAULT_ORDERABLE_DAYS


# =============================================================================
# 라면 동적 안전재고 설정
# =============================================================================
RAMEN_DYNAMIC_SAFETY_CONFIG = {
    "enabled": True,                      # 동적 안전재고 적용 여부
    "target_categories": ["006", "032"],  # 적용 대상: 조리면(006), 면류(032)

    # === 데이터 기간 설정 ===
    "analysis_days": 30,          # 기본 분석 기간 (30일) - SQL 파라미터용 int
    "min_data_days": 7,           # 최소 데이터 일수 (7일 미만이면 기본값 적용) - 비교용 int
    "default_safety_days": 2.0,   # 데이터 부족 시 기본 안전재고 일수

    # === 회전율별 안전재고 일수 ===
    # 일평균 판매량 기준
    "turnover_safety_days": {
        "high": {                 # 고회전: 일평균 5개 이상
            "min_daily_avg": 5.0,
            "safety_days": 2.0,   # 2.0일치 (매일발주+익일배송)
        },
        "medium": {               # 중회전: 일평균 2~4개
            "min_daily_avg": 2.0,
            "safety_days": 2.0,   # 2.0일치
        },
        "low": {                  # 저회전: 일평균 1개 이하
            "min_daily_avg": 0.0,
            "safety_days": 1.0,   # 1.0일치 (재고 최소화)
        },
    },

    # === 최대 재고 상한선 (공간 제약) ===
    "max_stock_enabled": True,
    "max_stock_days": 4.0,        # 최대 일평균 × 4일치 (5.0→4.0, 20% 축소: 과예측 방지)
}


def _get_db_path() -> str:
    """DB 경로 반환"""
    return str(Path(__file__).parent.parent.parent.parent / "data" / "bgf_sales.db")


@dataclass
class RamenPatternResult:
    """라면 판매 패턴 분석 결과"""
    item_cd: str

    # 데이터 기간 정보
    actual_data_days: int          # 실제 데이터 일수
    analysis_days: int             # 분석 기준 일수 (30일)
    has_enough_data: bool          # 최소 데이터 충족 여부 (7일 이상)

    # 회전율 정보
    total_sales: int               # 총 판매량
    daily_avg: float               # 일평균 판매량
    turnover_level: str            # 회전율 레벨 (참고용, safety_days 결정에 미사용)
    safety_days: float             # = order_interval (발주간격 기반)

    # 재고 정보
    current_stock: int             # 현재 재고
    max_stock: float               # 최대 재고 상한선
    is_over_max_stock: bool        # 상한선 초과 여부

    # 최종 결과
    final_safety_stock: float      # 최종 안전재고 (개수)
    skip_order: bool               # 발주 스킵 여부

    # 발주가능요일 정보 (NEW)
    skip_reason: str               # 스킵 사유
    orderable_day: str             # 적용된 발주가능요일
    order_interval: int            # 계산된 발주간격 일수
    is_orderable_today: bool       # 오늘 발주 가능 여부


def is_ramen_category(mid_cd: str) -> bool:
    """라면 카테고리 여부 확인

    Args:
        mid_cd: 중분류 코드

    Returns:
        라면 카테고리(006, 032)이면 True
    """
    return mid_cd in RAMEN_DYNAMIC_SAFETY_CONFIG["target_categories"]


def analyze_ramen_pattern(
    item_cd: str,
    db_path: Optional[str] = None,
    store_id: Optional[str] = None,
    orderable_day: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
) -> RamenPatternResult:
    """
    라면 상품의 발주가능요일 기반 패턴 분석

    Args:
        item_cd: 상품코드
        db_path: DB 경로 (기본: bgf_sales.db)
        store_id: 매장 ID (None이면 전체 매장)
        orderable_day: 발주가능요일 (None이면 기본값 적용)
        current_stock: 현재 재고 (외부 전달)
        pending_qty: 미입고 수량

    Returns:
        RamenPatternResult: 분석 결과 데이터클래스
    """
    config = RAMEN_DYNAMIC_SAFETY_CONFIG
    if db_path is None:
        db_path = _get_db_path()

    analysis_days = config["analysis_days"]
    min_data_days = config["min_data_days"]
    default_safety_days = config["default_safety_days"]
    turnover_config = config["turnover_safety_days"]

    # DB에서 판매 데이터 조회
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    if store_id:
        cursor.execute("""
            SELECT sales_date, sale_qty, stock_qty
            FROM daily_sales
            WHERE item_cd = ?
            AND store_id = ?
            AND sales_date >= date('now', '-' || ? || ' days')
            ORDER BY sales_date DESC
        """, (item_cd, store_id, analysis_days))
    else:
        cursor.execute("""
            SELECT sales_date, sale_qty, stock_qty
            FROM daily_sales
            WHERE item_cd = ?
            AND sales_date >= date('now', '-' || ? || ' days')
            ORDER BY sales_date DESC
        """, (item_cd, analysis_days))

    rows = cursor.fetchall()
    conn.close()

    # 실제 데이터 일수
    actual_data_days = len(rows)
    has_enough_data = actual_data_days >= min_data_days

    # 판매량 계산
    total_sales = sum(row[1] or 0 for row in rows)
    daily_avg = total_sales / actual_data_days if actual_data_days > 0 else 0

    # 현재 재고 (가장 최근 데이터)
    current_stock = rows[0][2] if rows and rows[0][2] is not None else 0

    # === orderable_day 처리 ===
    effective_orderable_day = orderable_day or RAMEN_DEFAULT_ORDERABLE_DAYS
    order_interval = _calculate_order_interval(effective_orderable_day)
    is_today_orderable = _is_orderable_today(effective_orderable_day)

    # === 회전율 레벨 결정 (참고용, safety_days에는 미사용) ===
    if has_enough_data:
        if daily_avg >= turnover_config["high"]["min_daily_avg"]:
            turnover_level = "high"
        elif daily_avg >= turnover_config["medium"]["min_daily_avg"]:
            turnover_level = "medium"
        else:
            turnover_level = "low"
    else:
        turnover_level = "unknown"

    # === [변경] 안전재고 = 일평균 x 발주간격 ===
    safety_days = float(order_interval)
    final_safety_stock = daily_avg * order_interval

    # === 최대 재고 상한선 체크 ===
    max_stock = daily_avg * config["max_stock_days"] if daily_avg > 0 else 0
    is_over_max_stock = False
    skip_order = False
    skip_reason = ""

    # 비발주일 스킵 (최우선)
    if not is_today_orderable:
        skip_order = True
        skip_reason = f"비발주일 (orderable_day={effective_orderable_day})"

    # 상한선 초과 스킵
    elif config["max_stock_enabled"] and max_stock > 0:
        total_available = current_stock + pending_qty
        if total_available >= max_stock:
            is_over_max_stock = True
            skip_order = True
            skip_reason = f"상한선 초과 ({total_available} >= {max_stock:.0f})"

    return RamenPatternResult(
        item_cd=item_cd,
        actual_data_days=actual_data_days,
        analysis_days=analysis_days,
        has_enough_data=has_enough_data,
        total_sales=total_sales,
        daily_avg=round(daily_avg, 2),
        turnover_level=turnover_level,
        safety_days=safety_days,
        current_stock=current_stock,
        max_stock=round(max_stock, 1),
        is_over_max_stock=is_over_max_stock,
        final_safety_stock=round(final_safety_stock, 2),
        skip_order=skip_order,
        skip_reason=skip_reason,
        orderable_day=effective_orderable_day,
        order_interval=order_interval,
        is_orderable_today=is_today_orderable,
    )


def calculate_ramen_dynamic_safety(
    item_cd: str,
    daily_avg: Optional[float] = None,
    db_path: Optional[str] = None,
    store_id: Optional[str] = None,
    orderable_day: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
) -> Tuple[float, Optional[RamenPatternResult]]:
    """
    라면 동적 안전재고 계산

    Args:
        item_cd: 상품코드
        daily_avg: 일평균 판매량 (None이면 DB에서 조회)
        db_path: DB 경로
        store_id: 매장 ID (None이면 전체 매장)
        orderable_day: 발주가능요일 (None이면 기본값 적용)
        current_stock: 현재 재고 (외부 전달)
        pending_qty: 미입고 수량

    Returns:
        (최종 안전재고, 패턴 분석 결과)
    """
    config = RAMEN_DYNAMIC_SAFETY_CONFIG

    if not config["enabled"]:
        # 비활성화 시 기본값 반환
        default_safety = (daily_avg or 0) * config["default_safety_days"]
        return default_safety, None

    # 패턴 분석
    pattern = analyze_ramen_pattern(
        item_cd, db_path, store_id,
        orderable_day=orderable_day,
        current_stock=current_stock,
        pending_qty=pending_qty,
    )

    return pattern.final_safety_stock, pattern


def get_safety_stock_with_ramen_pattern(
    mid_cd: str,
    daily_avg: float,
    expiration_days: int,
    item_cd: Optional[str] = None,
    store_id: Optional[str] = None,
    orderable_day: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
) -> Tuple[float, Optional[RamenPatternResult]]:
    """
    안전재고 계산 (라면 동적 패턴 포함)

    Args:
        mid_cd: 중분류 코드
        daily_avg: 일평균 판매량
        expiration_days: 유통기한 일수
        item_cd: 상품코드
        store_id: 매장 ID (None이면 전체 매장)
        orderable_day: 발주가능요일 (None이면 기본값 적용)
        current_stock: 현재 재고 (외부 전달)
        pending_qty: 미입고 수량

    Returns:
        (안전재고_개수, 라면_패턴_정보)
        - 라면이 아니면 패턴_정보는 None
    """
    # 라면이 아니면 기본값 반환
    if not is_ramen_category(mid_cd):
        from .default import get_safety_stock_days
        safety_days = get_safety_stock_days(mid_cd, daily_avg, expiration_days)
        return daily_avg * safety_days, None

    # 라면: 동적 안전재고 계산
    if item_cd and RAMEN_DYNAMIC_SAFETY_CONFIG["enabled"]:
        final_safety, pattern = calculate_ramen_dynamic_safety(
            item_cd, store_id=store_id,
            orderable_day=orderable_day,
            current_stock=current_stock,
            pending_qty=pending_qty,
        )
        return final_safety, pattern

    # 기본값 (item_cd 없을 때)
    effective_orderable_day = orderable_day or RAMEN_DEFAULT_ORDERABLE_DAYS
    order_interval = _calculate_order_interval(effective_orderable_day)
    default_safety = daily_avg * order_interval
    return default_safety, None
