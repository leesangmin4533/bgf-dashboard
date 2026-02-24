"""
잡화/비식품 카테고리 예측 모듈

잡화류(054, 055, 058, 059, 061, 062, 063, 064, 066, 067, 068, 069, 070, 071)의
동적 안전재고 계산:
- 최소 재고 유지 전략 (판매 시에만 보충)
- 극소량 판매, 진열 목적, 과잉재고 = 자본 낭비
- 요일계수 미적용 (데이터 부족 → 노이즈만 발생)
- 일평균 < 0.3이면 안전재고 0, 재고 0일 때만 1개 발주

공식: 일평균 × 안전재고일수(1.0일), 최대 3일분 상한
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Tuple


# =============================================================================
# 잡화/비식품 설정
# =============================================================================
GENERAL_MERCHANDISE_CATEGORIES = [
    "054", "055", "058", "059", "061", "062", "063", "064",
    "066", "067", "068", "069", "070", "071",
]
# 054: 음료선물세트, 055: 화장품, 058: 의류용품, 059: 액세서리
# 061: 문구류, 062: 전기연료, 063: 우천용상품, 064: 완구류
# 066: 파티/오락용품, 067: 애완용품, 068: 입지특화상품, 069: 소형가전
# 070: 상품권, 071: 편의상품

GENERAL_MERCHANDISE_DYNAMIC_SAFETY_CONFIG = {
    "target_categories": [
        "054", "055", "058", "059", "061", "062", "063", "064",
        "066", "067", "068", "069", "070", "071",
    ],
    "enabled": True,
    "analysis_days": 30,
    "min_data_days": 7,
    "default_safety_days": 1.0,
    "min_stock": 1,
    "max_stock_enabled": True,
    "max_stock_days": 3.0,
    "ultra_low_threshold": 0.3,
}

# 잡화류 요일별 계수 (미적용 - 데이터 부족으로 노이즈만 발생)
# 모든 요일 1.0 고정
DEFAULT_WEEKDAY_COEF = {
    0: 1.00,  # 월
    1: 1.00,  # 화
    2: 1.00,  # 수
    3: 1.00,  # 목
    4: 1.00,  # 금
    5: 1.00,  # 토
    6: 1.00,  # 일
}


@dataclass
class GeneralMerchandisePatternResult:
    """잡화/비식품 패턴 분석 결과"""
    item_cd: str                  # 상품코드
    mid_cd: str                   # 중분류 코드
    daily_avg: float              # 일평균 판매량
    safety_days: float            # 안전재고 일수 (1.0)
    final_safety_stock: float     # 최종 안전재고 수량
    min_stock: int                # 최소 진열 재고 (1)
    max_stock: float              # 최대 재고 상한선 (일평균 × 3일)
    skip_order: bool              # 발주 스킵 여부
    skip_reason: str              # 스킵 사유
    is_ultra_low: bool            # 극소량 판매 여부 (일평균 < 0.3)


def is_general_merchandise_category(mid_cd: str) -> bool:
    """잡화/비식품 카테고리 여부 확인

    Args:
        mid_cd: 중분류 코드

    Returns:
        잡화/비식품 카테고리이면 True
    """
    return mid_cd in GENERAL_MERCHANDISE_CATEGORIES


def _get_db_path() -> str:
    """DB 경로 반환

    Returns:
        bgf_sales.db의 절대 경로 문자열
    """
    return str(Path(__file__).parent.parent.parent.parent / "data" / "bgf_sales.db")


def _learn_weekday_pattern(mid_cd: str, db_path: str = None, min_data_days: int = 7,
                           store_id: Optional[str] = None) -> dict:
    """매장 판매 데이터에서 요일별 계수 학습

    잡화류는 데이터가 너무 부족하여 요일 패턴 학습이 무의미함.
    노이즈 방지를 위해 항상 1.0을 반환하는 것이 원칙이나,
    공통 인터페이스 유지를 위해 학습 로직은 구현.

    Args:
        mid_cd: 중분류 코드
        db_path: DB 경로 (None이면 자동 탐색)
        min_data_days: 최소 데이터 일수 (기본 7일)
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        요일별 계수 딕셔너리 {0: 계수, 1: 계수, ..., 6: 계수}
        (잡화류는 항상 1.0 반환)
    """
    # 잡화류는 요일계수 미적용 - 항상 기본값 반환
    # 데이터가 너무 부족해서 노이즈만 발생하므로 학습하지 않음
    return dict(DEFAULT_WEEKDAY_COEF)


def analyze_general_merchandise_pattern(
    item_cd: str,
    mid_cd: str,
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None,
) -> GeneralMerchandisePatternResult:
    """잡화/비식품 상품 패턴 분석

    최소 재고 유지 전략: 극소량 판매 상품은 안전재고 0으로 설정하고
    재고가 0이 될 때만 1개 발주. 과잉재고 = 자본 낭비.

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드
        db_path: DB 경로 (None이면 자동 탐색)
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        GeneralMerchandisePatternResult: 분석 결과 데이터클래스
    """
    config = GENERAL_MERCHANDISE_DYNAMIC_SAFETY_CONFIG

    if db_path is None:
        db_path = _get_db_path()

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

    # 일평균 계산
    daily_avg = (total_sales / data_days) if data_days > 0 else 0.0

    # 안전재고 일수
    safety_days = config["default_safety_days"]

    # 극소량 판매 판별 (일평균 < 0.3)
    is_ultra_low = daily_avg < config["ultra_low_threshold"]

    # 안전재고 수량 계산
    if is_ultra_low:
        # 극소량 판매: 안전재고 0, 재고 0일 때만 1개 발주
        final_safety_stock = 0.0
    else:
        final_safety_stock = daily_avg * safety_days

    # 최소 진열 재고
    min_stock = config["min_stock"]

    # 최대 재고 상한선 (일평균 × 3일, 매우 엄격)
    if daily_avg > 0:
        max_stock = daily_avg * config["max_stock_days"]
        # 최소 min_stock 이상은 보장
        max_stock = max(max_stock, float(min_stock))
    else:
        max_stock = float(min_stock)

    # 발주 스킵 판단
    skip_order = False
    skip_reason = ""

    if config["max_stock_enabled"] and max_stock > 0:
        if current_stock + pending_qty >= max_stock:
            skip_order = True
            skip_reason = f"상한선 초과 ({current_stock}+{pending_qty} >= {max_stock:.0f})"

    return GeneralMerchandisePatternResult(
        item_cd=item_cd,
        mid_cd=mid_cd,
        daily_avg=round(daily_avg, 2),
        safety_days=safety_days,
        final_safety_stock=round(final_safety_stock, 2),
        min_stock=min_stock,
        max_stock=round(max_stock, 2),
        skip_order=skip_order,
        skip_reason=skip_reason,
        is_ultra_low=is_ultra_low,
    )


def calculate_general_merchandise_dynamic_safety(
    item_cd: str,
    mid_cd: str,
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None,
) -> Tuple[float, GeneralMerchandisePatternResult]:
    """잡화/비식품 동적 안전재고 계산

    극소량 판매(일평균 < 0.3) 상품은 안전재고 0이며,
    현재재고가 0일 때만 1개 발주하는 최소 재고 유지 전략.

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드
        db_path: DB 경로
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        (안전재고_수량, 패턴_결과)
    """
    pattern = analyze_general_merchandise_pattern(
        item_cd, mid_cd, db_path, current_stock, pending_qty, store_id=store_id
    )

    # 특수 로직: 극소량 판매이고 현재재고 0이면 1개 강제 발주
    if pattern.is_ultra_low and current_stock == 0 and pending_qty == 0:
        pattern = GeneralMerchandisePatternResult(
            item_cd=pattern.item_cd,
            mid_cd=pattern.mid_cd,
            daily_avg=pattern.daily_avg,
            safety_days=pattern.safety_days,
            final_safety_stock=float(pattern.min_stock),
            min_stock=pattern.min_stock,
            max_stock=pattern.max_stock,
            skip_order=False,
            skip_reason="",
            is_ultra_low=pattern.is_ultra_low,
        )
        return pattern.final_safety_stock, pattern

    return pattern.final_safety_stock, pattern


def get_safety_stock_with_general_merchandise_pattern(
    mid_cd: str,
    daily_avg: float,
    expiration_days: int,
    item_cd: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None,
) -> Tuple[float, Optional[GeneralMerchandisePatternResult]]:
    """안전재고 계산 (잡화/비식품 동적 패턴 포함)

    Args:
        mid_cd: 중분류 코드
        daily_avg: 일평균 판매량
        expiration_days: 유통기한 일수
        item_cd: 상품코드
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        (안전재고_개수, 잡화_패턴_정보)
        - 잡화/비식품이 아니면 패턴_정보는 None
    """
    # 잡화/비식품이 아니면 기본값 반환
    if not is_general_merchandise_category(mid_cd):
        from .default import get_safety_stock_days
        safety_days = get_safety_stock_days(mid_cd, daily_avg, expiration_days)
        return daily_avg * safety_days, None

    # 잡화/비식품: 동적 안전재고 계산
    if item_cd and GENERAL_MERCHANDISE_DYNAMIC_SAFETY_CONFIG["enabled"]:
        final_safety, pattern = calculate_general_merchandise_dynamic_safety(
            item_cd, mid_cd, None, current_stock, pending_qty, store_id=store_id
        )
        return final_safety, pattern

    # 기본값 (상품코드 없거나 비활성화 시)
    default_days = GENERAL_MERCHANDISE_DYNAMIC_SAFETY_CONFIG["default_safety_days"]
    safety = daily_avg * default_days
    # 극소량 판매 체크
    if daily_avg < GENERAL_MERCHANDISE_DYNAMIC_SAFETY_CONFIG["ultra_low_threshold"]:
        safety = 0.0
    return safety, None
