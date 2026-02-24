"""
신상품 도입 점수 계산기

- 도입률 → 도입 점수 (80% 가중치)
- 3일발주 달성률 → 달성 점수 (20% 가중치)
- 종합 점수 → 지원금 구간
"""

import math
from typing import List, Dict, Optional, Tuple


# 점수 환산 기준 (ds_S948에서 수집, 기본 폴백값)
DEFAULT_SCORE_CONVERSION = {
    "doip": [
        # (rate_start, rate_end, score)
        (0, 9.9, 0), (10, 19.9, 8), (20, 29.9, 16), (30, 39.9, 24),
        (40, 49.9, 32), (50, 59.9, 40), (60, 69.9, 48), (70, 79.9, 56),
        (80, 89.9, 64), (90, 94.9, 72), (95, 100, 80),
    ],
    "ds": [
        (0, 9.9, 0), (10, 19.9, 2), (20, 29.9, 4), (30, 39.9, 6),
        (40, 49.9, 8), (50, 59.9, 10), (60, 69.9, 12), (70, 79.9, 16),
        (80, 89.9, 18), (90, 100, 20),
    ],
}

# 점수 → 지원금 구간 (ds_S945에서 수집, 기본 폴백값)
DEFAULT_SUBSIDY_TABLE = [
    # (score_min, score_max, amount)
    (95, 100, 160000),
    (88, 94, 150000),
    (84, 87, 120000),
    (72, 83, 110000),
    (56, 71, 80000),
    (40, 55, 40000),
    (0, 39, 0),
]


def rate_to_score(
    rate: float,
    score_type: str,
    conversion_table: Optional[List[Tuple]] = None,
) -> float:
    """도입률/달성률 → 점수 환산

    Args:
        rate: 비율 (0~100)
        score_type: 'doip' or 'ds'
        conversion_table: 커스텀 환산표 [(start, end, score), ...]
    """
    table = conversion_table or DEFAULT_SCORE_CONVERSION.get(score_type, [])
    for start, end, score in table:
        if start <= rate <= end:
            return score
    if rate >= 100:
        return table[-1][2] if table else 0
    return 0


def calculate_total_score(
    doip_rate: float,
    ds_rate: float,
    conversion_tables: Optional[Dict] = None,
) -> Tuple[float, float, float]:
    """종합 점수 계산

    Returns:
        (doip_score, ds_score, total_score)
    """
    doip_conv = (conversion_tables or {}).get("doip")
    ds_conv = (conversion_tables or {}).get("ds")

    doip_score = rate_to_score(doip_rate, "doip", doip_conv)
    ds_score = rate_to_score(ds_rate, "ds", ds_conv)
    total = doip_score + ds_score
    return doip_score, ds_score, total


def score_to_subsidy(
    total_score: float,
    subsidy_table: Optional[List[Tuple]] = None,
) -> int:
    """종합 점수 → 지원금"""
    table = subsidy_table or DEFAULT_SUBSIDY_TABLE
    for score_min, score_max, amount in table:
        if score_min <= total_score <= score_max:
            return amount
    return 0


def calculate_needed_items(
    current_rate: float,
    target_rate: float,
    total_cnt: int,
    current_cnt: int,
) -> int:
    """목표 달성에 필요한 추가 상품 수

    Args:
        current_rate: 현재 비율 (%)
        target_rate: 목표 비율 (%)
        total_cnt: 전체 대상 상품 수
        current_cnt: 현재 달성 상품 수

    Returns:
        추가로 필요한 상품 수 (0 이상)
    """
    if current_rate >= target_rate:
        return 0
    needed_cnt = math.ceil(total_cnt * target_rate / 100.0)
    gap = needed_cnt - current_cnt
    return max(0, gap)


def estimate_score_after_orders(
    current_doip_cnt: int,
    current_ds_cnt: int,
    total_doip_items: int,
    total_ds_items: int,
    new_doip_orders: int = 0,
    new_ds_orders: int = 0,
    conversion_tables: Optional[Dict] = None,
) -> Dict:
    """발주 후 예상 점수 시뮬레이션

    Returns:
        dict with estimated rates, scores, total, subsidy
    """
    new_doip = current_doip_cnt + new_doip_orders
    new_ds = current_ds_cnt + new_ds_orders

    est_doip_rate = (new_doip / total_doip_items * 100) if total_doip_items > 0 else 0
    est_ds_rate = (new_ds / total_ds_items * 100) if total_ds_items > 0 else 0

    est_doip_rate = min(est_doip_rate, 100.0)
    est_ds_rate = min(est_ds_rate, 100.0)

    doip_score, ds_score, total = calculate_total_score(
        est_doip_rate, est_ds_rate, conversion_tables
    )
    subsidy = score_to_subsidy(total)

    return {
        "doip_rate": round(est_doip_rate, 1),
        "ds_rate": round(est_ds_rate, 1),
        "doip_score": doip_score,
        "ds_score": ds_score,
        "total_score": total,
        "subsidy": subsidy,
    }
