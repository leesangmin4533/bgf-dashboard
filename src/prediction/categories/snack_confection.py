"""
과자/간식 카테고리 예측 모듈

과자류(015~020, 029, 030)의 동적 안전재고 계산:
(014 디저트는 dessert.py로 분리됨)
- 발주가능요일(orderable_day) 기반 안전재고 계산
- 비발주일에는 발주 스킵
- 발주간격(최대 gap) 만큼의 재고를 확보
- 상한선: 일평균 x 5일 (장기유통이므로 여유)

공식: 일평균 x 발주간격(orderable_day 기반) x 요일계수, 상한선 초과 시 발주 스킵
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Tuple

from src.utils.logger import get_logger
from src.settings.constants import SNACK_DEFAULT_ORDERABLE_DAYS

logger = get_logger(__name__)


# =============================================================================
# 과자/간식 설정
# =============================================================================
SNACK_CONFECTION_TARGET_CATEGORIES = ["015", "016", "017", "018", "019", "020", "029", "030"]

SNACK_CONFECTION_DYNAMIC_SAFETY_CONFIG = {
    "target_categories": ["015", "016", "017", "018", "019", "020", "029", "030"],
    "enabled": True,
    "analysis_days": 30,
    "min_data_days": 14,
    "default_safety_days": 0.8,
    "max_stock_enabled": True,
    "max_stock_days": 5.0,  # 7.0→5.0 (30% 축소: 과예측 방지)
}

# 회전율별 안전재고일수 (참고용으로 유지, 발주간격 기반으로 대체됨)
SNACK_SAFETY_CONFIG = {
    "high_turnover": {          # 일평균 5개 이상
        "min_daily_avg": 5.0,
        "safety_days": 1.2,     # 1.5→1.2 (20% 축소)
    },
    "medium_turnover": {        # 일평균 2~5개
        "min_daily_avg": 2.0,
        "safety_days": 0.8,     # 1.0→0.8 (20% 축소)
    },
    "low_turnover": {           # 일평균 2개 미만
        "min_daily_avg": 0.0,
        "safety_days": 0.6,     # 0.8→0.6 (25% 축소)
    },
}

# 과자류 요일계수 - 실측 데이터 기반 (최근 28일, mid_cd IN 015/016/019/020)
# 0=월, 1=화, 2=수, 3=목, 4=금, 5=토, 6=일 (Python weekday 기준)
DEFAULT_WEEKDAY_COEF = {
    0: 1.06,  # 월
    1: 0.99,  # 화
    2: 1.04,  # 수
    3: 0.84,  # 목
    4: 1.01,  # 금
    5: 1.20,  # 토: 1.34→1.20 (과도한 토요일 부스트 보수적 축소)
    6: 0.74,  # 일
}


@dataclass
class SnackConfectionPatternResult:
    """과자/간식 패턴 분석 결과"""
    item_cd: str                  # 상품코드
    mid_cd: str                   # 중분류 코드
    daily_avg: float              # 일평균 판매량
    turnover_level: str           # 회전율 레벨 (high/medium/low/unknown) - 참고용
    safety_days: float            # 적용된 안전재고 일수 (= 발주간격)
    final_safety_stock: float     # 최종 안전재고 (개수)
    max_stock: float              # 최대 재고 상한선
    skip_order: bool              # 발주 스킵 여부
    skip_reason: str              # 스킵 사유
    weekday_coef: float           # 적용된 요일 계수
    orderable_day: str            # 적용된 발주가능요일
    order_interval: int           # 계산된 발주간격 (일수)
    is_orderable_today: bool      # 오늘 발주 가능 여부


def is_snack_confection_category(mid_cd: str) -> bool:
    """과자/간식 카테고리 여부 확인 (014 디저트 제외)

    Args:
        mid_cd: 중분류 코드

    Returns:
        과자/간식 카테고리(015~020, 029, 030)이면 True
    """
    return mid_cd in SNACK_CONFECTION_TARGET_CATEGORIES


def _is_orderable_today(orderable_day: str) -> bool:
    """오늘이 발주 가능 요일인지 확인

    Args:
        orderable_day: 발주 가능 요일 문자열 (예: "화목토")

    Returns:
        오늘이 발주 가능 요일이면 True. 파싱 실패 시 True (안전 폴백)
    """
    day_map = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
    available = {day_map[c] for c in (orderable_day or "") if c in day_map}
    if not available:
        return True  # 파싱 실패 시 발주 허용 (안전 폴백)
    today_wd = datetime.now().weekday()
    return today_wd in available


def _calculate_order_interval(orderable_day: str) -> int:
    """발주 가능 요일 간 최대 간격(일수) 계산

    발주가능요일 사이의 최대 갭을 구하여 안전재고 일수로 사용.
    예: 화목토 → [1,3,5] → 간격 [2,2,3] → 최대 3 (토→화)

    Args:
        orderable_day: 발주 가능 요일 문자열 (예: "화목토", "월수금")

    Returns:
        최대 발주간격 (1~7). 파싱 실패 시 기본값 2
    """
    day_map = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
    available = sorted({day_map[c] for c in (orderable_day or "") if c in day_map})
    if not available:
        return 2  # 기본값: 격일 가정

    if len(available) == 1:
        return 7  # 주 1회

    # 연속 간격 계산 (순환 포함: 마지막→첫 번째)
    gaps = []
    for i in range(len(available)):
        next_i = (i + 1) % len(available)
        gap = (available[next_i] - available[i]) % 7
        gaps.append(gap)

    return max(gaps)


def _get_db_path() -> str:
    """DB 경로 반환

    Returns:
        bgf_sales.db 절대 경로 문자열
    """
    return str(Path(__file__).parent.parent.parent.parent / "data" / "bgf_sales.db")


def _learn_weekday_pattern(mid_cd: str, db_path: str = None, min_data_days: int = 14,
                           store_id: Optional[str] = None) -> dict:
    """매장 판매 데이터에서 요일별 계수 학습.

    최근 30일 데이터로 요일별 평균 산출, 전체평균 대비 비율.
    0.5~2.5 클램프. min_data_days 미만이면 기본값.

    Args:
        mid_cd: 중분류 코드
        db_path: DB 경로 (None이면 자동 탐색)
        min_data_days: 최소 데이터 일수
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        {0: coef, 1: coef, ..., 6: coef} (Python weekday: 월=0, 일=6)
    """
    if db_path is None:
        db_path = _get_db_path()
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        if store_id:
            cursor.execute("""
                SELECT CAST(strftime('%w', sales_date) AS INTEGER) as dow,
                       SUM(sale_qty) as total, COUNT(DISTINCT sales_date) as days
                FROM daily_sales
                WHERE mid_cd = ? AND store_id = ? AND sales_date >= date('now', '-30 days')
                GROUP BY dow
            """, (mid_cd, store_id))
        else:
            cursor.execute("""
                SELECT CAST(strftime('%w', sales_date) AS INTEGER) as dow,
                       SUM(sale_qty) as total, COUNT(DISTINCT sales_date) as days
                FROM daily_sales
                WHERE mid_cd = ? AND sales_date >= date('now', '-30 days')
                GROUP BY dow
            """, (mid_cd,))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return dict(DEFAULT_WEEKDAY_COEF)

        # SQLite dow: 0=일,1=월,...,6=토 → Python weekday: 월=0,...,일=6
        sqlite_to_py = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 0: 6}
        dow_avgs = {}
        total_days = 0
        for dow_sqlite, total_qty, day_count in rows:
            py_dow = sqlite_to_py.get(dow_sqlite, dow_sqlite)
            dow_avgs[py_dow] = total_qty / max(1, day_count)
            total_days += day_count

        if total_days < min_data_days:
            return dict(DEFAULT_WEEKDAY_COEF)

        overall_avg = sum(dow_avgs.values()) / max(1, len(dow_avgs))
        if overall_avg <= 0:
            return dict(DEFAULT_WEEKDAY_COEF)

        learned = {}
        for d in range(7):
            if d in dow_avgs and overall_avg > 0:
                coef = dow_avgs[d] / overall_avg
                learned[d] = max(0.5, min(2.5, round(coef, 2)))
            else:
                learned[d] = DEFAULT_WEEKDAY_COEF.get(d, 1.0)
        return learned
    except Exception as e:
        logger.warning(f"[과자/제과] 요일 패턴 학습 실패 (mid_cd={mid_cd}): {e}")
        return dict(DEFAULT_WEEKDAY_COEF)


def _get_turnover_level(daily_avg: float) -> Tuple[str, float]:
    """일평균 판매량 기반 회전율 레벨 및 안전재고일수 결정

    Args:
        daily_avg: 일평균 판매량

    Returns:
        (회전율 레벨, 안전재고 일수)
    """
    if daily_avg >= SNACK_SAFETY_CONFIG["high_turnover"]["min_daily_avg"]:
        return "high", SNACK_SAFETY_CONFIG["high_turnover"]["safety_days"]
    elif daily_avg >= SNACK_SAFETY_CONFIG["medium_turnover"]["min_daily_avg"]:
        return "medium", SNACK_SAFETY_CONFIG["medium_turnover"]["safety_days"]
    else:
        return "low", SNACK_SAFETY_CONFIG["low_turnover"]["safety_days"]


def analyze_snack_confection_pattern(
    item_cd: str,
    mid_cd: str = "",
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None,
    orderable_day: Optional[str] = None,
) -> SnackConfectionPatternResult:
    """과자/간식 상품 패턴 분석

    발주가능요일 기반으로 안전재고를 계산하고,
    비발주일이거나 재고+미입고가 상한선 이상이면 발주를 스킵한다.

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드
        db_path: DB 경로 (None이면 자동 탐색)
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)
        orderable_day: 발주 가능 요일 (None이면 SNACK_DEFAULT_ORDERABLE_DAYS)

    Returns:
        SnackConfectionPatternResult
    """
    config = SNACK_CONFECTION_DYNAMIC_SAFETY_CONFIG

    if db_path is None:
        db_path = _get_db_path()

    # 발주가능요일 결정 (DB값 우선, 없으면 스낵 기본값)
    effective_orderable_day = orderable_day or SNACK_DEFAULT_ORDERABLE_DAYS

    # 발주간격 계산
    order_interval = _calculate_order_interval(effective_orderable_day)

    # 오늘 발주 가능 여부
    today_orderable = _is_orderable_today(effective_orderable_day)

    # 오늘 요일
    weekday = datetime.now().weekday()  # 0=월, 6=일

    # 요일계수 학습 (과자류는 변동이 적어 기본값에 가까움)
    learned_coef = _learn_weekday_pattern(mid_cd, db_path, config["min_data_days"], store_id=store_id)
    weekday_coef = learned_coef.get(weekday, 1.0)

    # DB에서 일평균 판매량 조회
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    if store_id:
        cursor.execute("""
            SELECT COUNT(DISTINCT sales_date) as data_days,
                   COALESCE(SUM(sale_qty), 0) as total_sales
            FROM daily_sales
            WHERE item_cd = ? AND store_id = ?
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

    # 회전율 레벨 (참고용 유지)
    if has_enough_data:
        turnover_level, _ = _get_turnover_level(daily_avg)
    else:
        turnover_level = "unknown"

    # [변경] 안전재고 = 일평균 x 발주간격 x 요일계수
    # 기존: daily_avg x safety_days(회전율별 0.6~1.2) x weekday_coef
    # 변경: daily_avg x order_interval x weekday_coef
    safety_days = float(order_interval)
    final_safety_stock = daily_avg * order_interval * weekday_coef

    # 최대 재고 상한선
    max_stock = daily_avg * config["max_stock_days"] if daily_avg > 0 else 0

    # 발주 스킵 판단
    skip_order = False
    skip_reason = ""

    # 비발주일이면 스킵 (최우선)
    if not today_orderable:
        skip_order = True
        skip_reason = f"비발주일 (orderable_day={effective_orderable_day})"

    # 상한선 초과 스킵
    elif config["max_stock_enabled"] and max_stock > 0:
        if current_stock + pending_qty >= max_stock:
            skip_order = True
            skip_reason = f"상한선 초과 ({current_stock}+{pending_qty} >= {max_stock:.0f})"

    return SnackConfectionPatternResult(
        item_cd=item_cd,
        mid_cd=mid_cd,
        daily_avg=round(daily_avg, 2),
        turnover_level=turnover_level,
        safety_days=safety_days,
        final_safety_stock=round(final_safety_stock, 2),
        max_stock=round(max_stock, 1),
        skip_order=skip_order,
        skip_reason=skip_reason,
        weekday_coef=round(weekday_coef, 2),
        orderable_day=effective_orderable_day,
        order_interval=order_interval,
        is_orderable_today=today_orderable,
    )


def calculate_snack_confection_dynamic_safety(
    item_cd: str,
    mid_cd: str = "",
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None,
    orderable_day: Optional[str] = None,
) -> Tuple[float, SnackConfectionPatternResult]:
    """과자/간식 동적 안전재고 계산

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드
        db_path: DB 경로
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)
        orderable_day: 발주 가능 요일 (None이면 SNACK_DEFAULT_ORDERABLE_DAYS)

    Returns:
        (안전재고_수량, 패턴_결과)
    """
    pattern = analyze_snack_confection_pattern(
        item_cd, mid_cd, db_path, current_stock, pending_qty,
        store_id=store_id, orderable_day=orderable_day
    )
    return pattern.final_safety_stock, pattern


def get_safety_stock_with_snack_confection_pattern(
    mid_cd: str,
    daily_avg: float,
    expiration_days: int,
    item_cd: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None,
    orderable_day: Optional[str] = None,
) -> Tuple[float, Optional[SnackConfectionPatternResult]]:
    """안전재고 계산 (과자/간식 동적 패턴 포함)

    Args:
        mid_cd: 중분류 코드
        daily_avg: 일평균 판매량
        expiration_days: 유통기한 일수
        item_cd: 상품코드
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)
        orderable_day: 발주 가능 요일 (None이면 SNACK_DEFAULT_ORDERABLE_DAYS)

    Returns:
        (안전재고_개수, 과자_패턴_정보)
        - 과자류가 아니면 패턴_정보는 None
    """
    # 과자류가 아니면 기본값 반환
    if not is_snack_confection_category(mid_cd):
        from .default import get_safety_stock_days
        safety_days = get_safety_stock_days(mid_cd, daily_avg, expiration_days)
        return daily_avg * safety_days, None

    # 과자류: 동적 안전재고 계산
    if item_cd and SNACK_CONFECTION_DYNAMIC_SAFETY_CONFIG["enabled"]:
        final_safety, pattern = calculate_snack_confection_dynamic_safety(
            item_cd, mid_cd, None, current_stock, pending_qty,
            store_id=store_id, orderable_day=orderable_day
        )
        return final_safety, pattern

    # 기본값 (상품코드 없거나 비활성화 시)
    default_days = SNACK_CONFECTION_DYNAMIC_SAFETY_CONFIG["default_safety_days"]
    return daily_avg * default_days, None
