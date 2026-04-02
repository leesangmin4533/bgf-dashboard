"""
담배/전자담배 카테고리 예측 모듈

담배(072), 전자담배(073)의 동적 안전재고 계산:
- 3종 분류: LIL(전자담배 액상), 보루형(카톤 구매 이력), 낱개형
- 보루 패턴: 10갑 단위 구매 빈도 분석
- 전량 소진 패턴: 재고 소진 빈도 분석
- 상한선: 낱개형 11, 보루형 41, LIL류 max(daily*3, 11)
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from src.prediction.categories._db import get_conn
from src.settings.constants import (
    TOBACCO_DISPLAY_MAX,
    TOBACCO_BOURU_UNIT,
    TOBACCO_BOURU_THRESHOLD,
    TOBACCO_BOURU_LOOKBACK_DAYS,
    TOBACCO_BOURU_FREQ_MULTIPLIER,
    TOBACCO_BOURU_DECAY_DAYS,
    TOBACCO_BOURU_DECAY_RATIO,
    TOBACCO_BOURU_MAX_STOCK,
    TOBACCO_SINGLE_URGENT_THRESHOLD,
    TOBACCO_BOURU_URGENT_THRESHOLD,
    TOBACCO_FORCE_MIN_DAILY_AVG,
    LIL_BOURU_THRESHOLD,
)


# =============================================================================
# 담배 동적 안전재고 설정 (기존 유지 — 후방 호환)
# =============================================================================
TOBACCO_DYNAMIC_SAFETY_CONFIG = {
    "enabled": True,              # 동적 안전재고 적용 여부
    "target_categories": ["072", "073"],  # 적용 대상: 담배, 전자담배

    # === 데이터 기간 설정 ===
    "analysis_days": 30,          # 기본 분석 기간 (30일) - SQL 파라미터용 int
    "min_data_days": 7,           # 최소 데이터 일수 (7일 미만이면 패턴 분석 스킵) - 비교용 int
    "default_safety_days": 2.0,   # 데이터 부족 시 기본 안전재고 일수

    # === 보루 패턴 설정 ===
    "carton_unit": 10,            # 보루 단위 (10갑) - 정수 연산용 int
    "min_sales_for_carton": 10,   # 보루 판매로 간주할 최소 판매량 - 비교용 int

    # 보루 빈도별 추가 안전재고 (30일 환산 기준, 개수)
    "carton_buffer": {
        "high": 10,    # 빈도 4회 이상 (주 1회): +10개
        "medium": 5,   # 빈도 2~3회 (격주 1회): +5개
        "low": 0,      # 빈도 1회 이하: +0개
    },
    "carton_thresholds": {
        "high": 4,     # 4회 이상 = high
        "medium": 2,   # 2~3회 = medium
    },

    # === 전량 소진 패턴 설정 ===
    # 전량 소진 빈도별 안전재고 승수 (30일 환산 기준)
    "sellout_multiplier": {
        "high": 1.5,   # 3회 이상: ×1.5
        "medium": 1.2, # 1~2회: ×1.2
        "low": 1.0,    # 0회: ×1.0
    },
    "sellout_thresholds": {
        "high": 3,     # 3회 이상 = high
        "medium": 1,   # 1~2회 = medium
    },

    # === 최대 재고 상한선 설정 ===
    "max_stock_enabled": True,
    "max_stock": 30,              # 담배 상품당 최대 보유 수량 (낱개형 기본)
    "min_order_unit": 10,         # 최소 발주 단위 (입수)
}


def _get_db_path(store_id: str = None) -> str:
    """DB 경로 반환 (deprecated - get_conn 사용 권장)"""
    from src.infrastructure.database.connection import resolve_db_path
    return resolve_db_path(store_id=store_id)


@dataclass
class TobaccoPatternResult:
    """담배 판매 패턴 분석 결과"""
    item_cd: str

    # 데이터 기간 정보
    actual_data_days: int          # 실제 데이터 일수
    analysis_days: int             # 분석 기준 일수 (30일)
    has_enough_data: bool          # 최소 데이터 충족 여부 (7일 이상)

    # 일평균 판매량
    daily_avg: float               # 일평균 판매량

    # 보루 패턴
    carton_count_raw: int          # 실제 보루 판매 일수
    carton_count_scaled: float     # 30일 환산 빈도
    carton_level: str              # high/medium/low
    carton_buffer: int             # 추가 안전재고 개수
    carton_dates: List[str]        # 보루 판매 날짜 목록

    # 전량 소진 패턴
    sellout_count_raw: int         # 실제 전량 소진 일수
    sellout_count_scaled: float    # 30일 환산 빈도
    sellout_level: str             # high/medium/low
    sellout_multiplier: float      # 안전재고 승수

    # 최대 재고 관련
    current_stock: int             # 현재 재고
    max_stock: int                 # 최대 재고 상한선
    available_space: int           # 여유분 (상한 - 현재고 - 미입고)
    skip_order: bool               # 발주 스킵 여부
    skip_reason: str               # 스킵 사유

    # 최종 결과
    final_buffer: int              # 최종 추가 안전재고 (보루 버퍼)
    final_multiplier: float        # 최종 승수 (전량 소진)
    final_safety_stock: float      # 최종 안전재고 (개수)

    # ── 신규 분류 필드 (tobacco-strategy-improvement) ──
    tobacco_type: str = ""                  # "lil" | "bouru" | "single"
    target_stock: int = 0                   # 목표 재고
    bouru_count_60d: int = 0                # 60일 보루 발생 횟수
    bouru_count_30d: int = 0                # 30일 보루 발생 횟수
    decay_applied: bool = False             # 감쇠 적용 여부
    suggested_decision: str = ""            # FORCE / URGENT / NORMAL / SKIP
    decision_reason: str = ""               # 판정 이유 (한글)


def is_tobacco_category(mid_cd: str) -> bool:
    """담배/전자담배 카테고리 여부 확인

    Args:
        mid_cd: 중분류 코드

    Returns:
        담배 또는 전자담배 카테고리이면 True
    """
    return mid_cd in TOBACCO_DYNAMIC_SAFETY_CONFIG["target_categories"]


# =============================================================================
# 보루 이력 조회 (DB)
# =============================================================================
def _query_bouru_history(
    item_cd: str,
    store_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Tuple[int, int]:
    """daily_sales에서 보루 구매 이력 조회

    Args:
        item_cd: 상품코드
        store_id: 매장 ID
        db_path: DB 경로 (테스트용)

    Returns:
        (bouru_count_60d, bouru_count_30d)
    """
    conn = get_conn(store_id=store_id, db_path=db_path)
    try:
        cursor = conn.cursor()
        if store_id:
            cursor.execute("""
                SELECT
                    SUM(CASE WHEN sale_qty >= ? THEN 1 ELSE 0 END) as cnt_60d,
                    SUM(CASE WHEN sale_qty >= ?
                         AND sales_date >= date('now', '-' || ? || ' days')
                         THEN 1 ELSE 0 END) as cnt_30d
                FROM daily_sales
                WHERE item_cd = ?
                AND store_id = ?
                AND sales_date >= date('now', '-' || ? || ' days')
            """, (TOBACCO_BOURU_THRESHOLD, TOBACCO_BOURU_THRESHOLD,
                  TOBACCO_BOURU_DECAY_DAYS, item_cd, store_id,
                  TOBACCO_BOURU_LOOKBACK_DAYS))
        else:
            cursor.execute("""
                SELECT
                    SUM(CASE WHEN sale_qty >= ? THEN 1 ELSE 0 END) as cnt_60d,
                    SUM(CASE WHEN sale_qty >= ?
                         AND sales_date >= date('now', '-' || ? || ' days')
                         THEN 1 ELSE 0 END) as cnt_30d
                FROM daily_sales
                WHERE item_cd = ?
                AND sales_date >= date('now', '-' || ? || ' days')
            """, (TOBACCO_BOURU_THRESHOLD, TOBACCO_BOURU_THRESHOLD,
                  TOBACCO_BOURU_DECAY_DAYS, item_cd,
                  TOBACCO_BOURU_LOOKBACK_DAYS))

        row = cursor.fetchone()
        if row:
            return (row[0] or 0, row[1] or 0)
        return (0, 0)
    finally:
        conn.close()


# =============================================================================
# 신규: 보루/낱개/LIL 분류 기반 안전재고 계산
# =============================================================================
def classify_and_calculate_tobacco(
    mid_cd: str,
    item_cd: str,
    daily_avg: float,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None,
    bouru_count_60d: Optional[int] = None,
    bouru_count_30d: Optional[int] = None,
) -> TobaccoPatternResult:
    """보루/낱개/LIL 3종 분류 + 목표재고 + 판정

    Args:
        mid_cd: 중분류 코드 ("072" 또는 "073")
        item_cd: 상품코드
        daily_avg: 일평균 판매량
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID
        bouru_count_60d: 60일 보루 횟수 (None이면 DB 조회)
        bouru_count_30d: 30일 보루 횟수 (None이면 DB 조회)

    Returns:
        TobaccoPatternResult (신규 필드 포함)
    """
    cs = current_stock or 0
    pq = pending_qty or 0

    # ── Step 1: 분류 ──────────────────────────────────────
    if mid_cd == "073":
        tobacco_type = "lil"
        b60d, b30d = 0, 0
    else:
        # DB에서 보루 이력 조회 (외부에서 주입 가능)
        if bouru_count_60d is not None and bouru_count_30d is not None:
            b60d, b30d = bouru_count_60d, bouru_count_30d
        else:
            b60d, b30d = _query_bouru_history(item_cd, store_id)
        tobacco_type = "bouru" if b60d > 0 else "single"

    # ── Step 2: 목표 재고 계산 ────────────────────────────
    decay_applied = False

    if tobacco_type == "lil":
        target_stock = max(round(daily_avg * 3), TOBACCO_DISPLAY_MAX)
        max_stock = target_stock  # LIL은 자체 목표가 상한

    elif tobacco_type == "bouru":
        bouru_safety = round(b60d * TOBACCO_BOURU_FREQ_MULTIPLIER) * TOBACCO_BOURU_UNIT
        # 최근 30일 이력 없으면 감쇠
        if b30d == 0:
            bouru_safety = int(bouru_safety * TOBACCO_BOURU_DECAY_RATIO)
            decay_applied = True
        target_stock = min(TOBACCO_DISPLAY_MAX + bouru_safety, TOBACCO_BOURU_MAX_STOCK)
        max_stock = TOBACCO_BOURU_MAX_STOCK

    else:  # single
        target_stock = TOBACCO_DISPLAY_MAX
        max_stock = TOBACCO_DISPLAY_MAX

    # ── Step 3: 판정 ─────────────────────────────────────
    if tobacco_type == "lil":
        if cs == 0 and daily_avg >= TOBACCO_FORCE_MIN_DAILY_AVG:
            decision = "FORCE"
            reason = f"LIL 품절 + 일평균 {daily_avg:.1f}≥{TOBACCO_FORCE_MIN_DAILY_AVG}"
        elif cs < target_stock * 0.3:
            decision = "URGENT"
            reason = f"LIL 재고 {cs} < 목표의 30%({target_stock * 0.3:.0f})"
        elif cs < target_stock:
            decision = "NORMAL"
            reason = f"LIL 재고 {cs} < 목표 {target_stock}"
        else:
            decision = "SKIP"
            reason = f"LIL 재고 {cs} ≥ 목표 {target_stock}"

    elif tobacco_type == "bouru":
        if cs == 0 and daily_avg >= TOBACCO_FORCE_MIN_DAILY_AVG:
            decision = "FORCE"
            reason = f"보루형 품절 + 일평균 {daily_avg:.1f}≥{TOBACCO_FORCE_MIN_DAILY_AVG}"
        elif cs < TOBACCO_BOURU_URGENT_THRESHOLD:
            decision = "URGENT"
            reason = f"보루형 재고 {cs} < {TOBACCO_BOURU_URGENT_THRESHOLD} (보루 판매 불가)"
        elif cs < target_stock:
            decision = "NORMAL"
            reason = f"보루형 재고 {cs} < 목표 {target_stock}"
        else:
            decision = "SKIP"
            reason = f"보루형 재고 {cs} ≥ 목표 {target_stock}"

    else:  # single
        if cs == 0 and daily_avg >= TOBACCO_FORCE_MIN_DAILY_AVG:
            decision = "FORCE"
            reason = f"낱개형 품절 + 일평균 {daily_avg:.1f}≥{TOBACCO_FORCE_MIN_DAILY_AVG}"
        elif cs < TOBACCO_SINGLE_URGENT_THRESHOLD:
            decision = "URGENT"
            reason = f"낱개형 재고 {cs} < {TOBACCO_SINGLE_URGENT_THRESHOLD}"
        elif cs < target_stock:
            decision = "NORMAL"
            reason = f"낱개형 재고 {cs} < 목표 {target_stock}"
        else:
            decision = "SKIP"
            reason = f"낱개형 재고 {cs} ≥ 목표 {target_stock}"

    # ── Step 4: available_space & skip 계산 ──────────────
    available_space = max(0, target_stock - cs - pq)
    # 미입고 포함 시 공간 없으면 SKIP 전환
    if available_space == 0 and decision not in ("FORCE",):
        decision = "SKIP"
        reason = f"재고({cs})+미입고({pq}) ≥ 목표({target_stock})"
    skip_order = (decision == "SKIP")
    skip_reason = reason if skip_order else ""

    # ── 안전재고 값 (파이프라인 연동용) ──────────────────
    # target_stock을 safety_stock으로 반환, 파이프라인에서 available_space로 cap
    final_safety_stock = float(target_stock)

    return TobaccoPatternResult(
        item_cd=item_cd,
        actual_data_days=0,    # 이 경로에서는 개별 row 분석 안 함
        analysis_days=TOBACCO_BOURU_LOOKBACK_DAYS,
        has_enough_data=True,
        daily_avg=round(daily_avg, 2),
        # 보루 패턴 (후방 호환)
        carton_count_raw=b60d,
        carton_count_scaled=float(b60d),
        carton_level="high" if b60d >= 4 else ("medium" if b60d >= 2 else "low"),
        carton_buffer=0,
        carton_dates=[],
        # 전량 소진 패턴 (후방 호환, 이 경로에서는 미분석)
        sellout_count_raw=0,
        sellout_count_scaled=0.0,
        sellout_level="low",
        sellout_multiplier=1.0,
        # 재고 관련
        current_stock=cs,
        max_stock=max_stock,
        available_space=available_space,
        skip_order=skip_order,
        skip_reason=skip_reason,
        # 기존 최종 결과 (후방 호환)
        final_buffer=0,
        final_multiplier=1.0,
        final_safety_stock=round(final_safety_stock, 2),
        # ── 신규 분류 필드 ──
        tobacco_type=tobacco_type,
        target_stock=target_stock,
        bouru_count_60d=b60d,
        bouru_count_30d=b30d,
        decay_applied=decay_applied,
        suggested_decision=decision,
        decision_reason=reason,
    )


# =============================================================================
# 기존 분석 함수 (후방 호환 유지)
# =============================================================================
def analyze_tobacco_pattern(
    item_cd: str,
    db_path: Optional[str] = None,
    current_stock: Optional[int] = None,
    pending_qty: int = 0,
    store_id: Optional[str] = None
) -> TobaccoPatternResult:
    """
    담배 상품의 보루 + 전량소진 패턴 종합 분석 (기존 로직 유지)

    Args:
        item_cd: 상품코드
        db_path: DB 경로 (기본: bgf_sales.db)
        current_stock: 현재 재고 (None이면 DB에서 조회)
        pending_qty: 미입고 수량 (기본 0)

    Returns:
        TobaccoPatternResult: 분석 결과 데이터클래스
    """
    config = TOBACCO_DYNAMIC_SAFETY_CONFIG
    analysis_days = config["analysis_days"]
    min_data_days = config["min_data_days"]
    carton_unit = config["carton_unit"]
    min_carton_sales = config["min_sales_for_carton"]
    max_stock = config["max_stock"]
    min_order_unit = config["min_order_unit"]
    default_safety_days = config["default_safety_days"]

    # DB에서 판매 데이터 조회 (날짜 오름차순 정렬)
    conn = get_conn(store_id=store_id, db_path=db_path)
    cursor = conn.cursor()

    if store_id:
        cursor.execute("""
            SELECT sales_date, sale_qty, stock_qty
            FROM daily_sales
            WHERE item_cd = ?
            AND store_id = ?
            AND sales_date >= date('now', '-' || ? || ' days')
            ORDER BY sales_date ASC
        """, (item_cd, store_id, analysis_days))
    else:
        cursor.execute("""
            SELECT sales_date, sale_qty, stock_qty
            FROM daily_sales
            WHERE item_cd = ?
            AND sales_date >= date('now', '-' || ? || ' days')
            ORDER BY sales_date ASC
        """, (item_cd, analysis_days))

    rows = cursor.fetchall()
    conn.close()

    # 실제 데이터 일수
    actual_data_days = len(rows)
    has_enough_data = actual_data_days >= min_data_days

    # 일평균 판매량 계산
    total_sales = sum(row[1] or 0 for row in rows)
    daily_avg = total_sales / actual_data_days if actual_data_days > 0 else 0

    # 현재 재고 조회 (파라미터로 안 주어지면 DB에서 가장 최근 값)
    if current_stock is None:
        current_stock = rows[-1][2] if rows and rows[-1][2] is not None else 0

    # 기본값 초기화
    carton_count_raw = 0
    carton_dates = []
    sellout_count_raw = 0

    if has_enough_data and rows:
        # === 보루 패턴 분석 ===
        # 조건: 판매량 >= 10 AND 10의 배수
        for i, (sales_date, sale_qty, stock_qty) in enumerate(rows):
            if sale_qty and sale_qty >= min_carton_sales and sale_qty % carton_unit == 0:
                carton_count_raw += 1
                carton_dates.append(sales_date)

        # === 전량 소진 패턴 분석 ===
        # 조건: 판매량 > 0 AND 판매량 == 전일 마감재고
        # 첫 번째 날은 전일 재고가 없으므로 제외
        for i in range(1, len(rows)):
            prev_date, prev_sale, prev_stock = rows[i - 1]
            curr_date, curr_sale, curr_stock = rows[i]

            # 전일 마감재고 = 전일 재고 (stock_qty는 마감 재고)
            # 당일 판매량 == 전일 마감재고 → 전량 소진
            if curr_sale and curr_sale > 0 and prev_stock is not None:
                if curr_sale == prev_stock:
                    sellout_count_raw += 1

    # === 30일 환산 빈도 계산 ===
    scale_factor = analysis_days / actual_data_days if actual_data_days > 0 else 1.0
    carton_count_scaled = carton_count_raw * scale_factor
    sellout_count_scaled = sellout_count_raw * scale_factor

    # === 보루 레벨 및 버퍼 결정 ===
    carton_thresholds = config["carton_thresholds"]
    carton_buffers = config["carton_buffer"]

    if carton_count_scaled >= carton_thresholds["high"]:
        carton_level = "high"
    elif carton_count_scaled >= carton_thresholds["medium"]:
        carton_level = "medium"
    else:
        carton_level = "low"

    carton_buffer = carton_buffers[carton_level] if has_enough_data else 0

    # === 전량 소진 레벨 및 승수 결정 ===
    sellout_thresholds = config["sellout_thresholds"]
    sellout_multipliers = config["sellout_multiplier"]

    if sellout_count_scaled >= sellout_thresholds["high"]:
        sellout_level = "high"
    elif sellout_count_scaled >= sellout_thresholds["medium"]:
        sellout_level = "medium"
    else:
        sellout_level = "low"

    sellout_multiplier = sellout_multipliers[sellout_level] if has_enough_data else 1.0

    # === 안전재고 계산 ===
    # 공식: (일평균 × 2일 + 보루버퍼) × 전량소진계수
    if has_enough_data:
        base_safety = daily_avg * default_safety_days
        final_safety_stock = (base_safety + carton_buffer) * sellout_multiplier
    else:
        # 데이터 부족: 기본 2일치만
        final_safety_stock = daily_avg * default_safety_days

    # === 최대 재고 상한선 체크 ===
    available_space = max_stock - current_stock - pending_qty
    skip_order = False
    skip_reason = ""

    if config["max_stock_enabled"]:
        if available_space <= 0:
            # 여유분 없음 → 발주 스킵
            skip_order = True
            skip_reason = f"상한선 초과 (현재고{current_stock}+미입고{pending_qty}≥{max_stock})"
        elif available_space < min_order_unit:
            # 여유분 < 최소 입수(10개) → 발주 스킵
            skip_order = True
            skip_reason = f"여유분 부족 (여유{available_space}<입수{min_order_unit})"

    return TobaccoPatternResult(
        item_cd=item_cd,
        actual_data_days=actual_data_days,
        analysis_days=analysis_days,
        has_enough_data=has_enough_data,
        daily_avg=round(daily_avg, 2),
        carton_count_raw=carton_count_raw,
        carton_count_scaled=round(carton_count_scaled, 1),
        carton_level=carton_level,
        carton_buffer=carton_buffer,
        carton_dates=carton_dates,
        sellout_count_raw=sellout_count_raw,
        sellout_count_scaled=round(sellout_count_scaled, 1),
        sellout_level=sellout_level,
        sellout_multiplier=sellout_multiplier,
        current_stock=current_stock,
        max_stock=max_stock,
        available_space=available_space,
        skip_order=skip_order,
        skip_reason=skip_reason,
        final_buffer=carton_buffer,
        final_multiplier=sellout_multiplier,
        final_safety_stock=round(final_safety_stock, 2),
    )


def calculate_tobacco_dynamic_safety(
    item_cd: str,
    daily_avg: Optional[float] = None,
    db_path: Optional[str] = None,
    current_stock: Optional[int] = None,
    pending_qty: int = 0,
    store_id: Optional[str] = None
) -> Tuple[float, Optional[TobaccoPatternResult]]:
    """
    담배 동적 안전재고 계산

    공식: (일평균 × 2일 + 보루버퍼) × 전량소진계수

    Args:
        item_cd: 상품코드
        daily_avg: 일평균 판매량 (None이면 DB에서 계산)
        db_path: DB 경로
        current_stock: 현재 재고 (None이면 DB에서 조회)
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        (최종 안전재고, 패턴 분석 결과)
    """
    config = TOBACCO_DYNAMIC_SAFETY_CONFIG

    if not config["enabled"]:
        # 비활성화 시 기본값 반환
        default_safety = (daily_avg or 0) * config["default_safety_days"]
        return default_safety, None

    # 패턴 분석 (현재 재고 및 미입고 포함)
    pattern = analyze_tobacco_pattern(item_cd, db_path, current_stock, pending_qty, store_id)

    # 패턴에서 계산된 안전재고 사용
    return pattern.final_safety_stock, pattern


def get_safety_stock_with_tobacco_pattern(
    mid_cd: str,
    daily_avg: float,
    expiration_days: int,
    item_cd: Optional[str] = None,
    current_stock: Optional[int] = None,
    pending_qty: int = 0,
    store_id: Optional[str] = None
) -> Tuple[float, Optional[TobaccoPatternResult]]:
    """
    안전재고 계산 — 보루/낱개/LIL 3종 분류 기반

    신규 로직: classify_and_calculate_tobacco() 호출
    - LIL(073): 일평균×3일 안전재고, 최소 진열대(11)
    - 보루형: 진열대(11) + 보루안전재고, 상한 41, 30일 감쇠
    - 낱개형: 진열대(11) 고정

    Args:
        mid_cd: 중분류 코드
        daily_avg: 일평균 판매량
        expiration_days: 유통기한 일수
        item_cd: 상품코드
        current_stock: 현재 재고 (담배 상한선 체크용)
        pending_qty: 미입고 수량 (담배 상한선 체크용)
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        (안전재고_개수, TobaccoPatternResult)
        - 담배가 아니면 패턴_정보는 None
    """
    # 담배/전자담배가 아니면 기본값 반환
    if mid_cd not in TOBACCO_DYNAMIC_SAFETY_CONFIG["target_categories"]:
        from .default import get_safety_stock_days
        safety_days = get_safety_stock_days(mid_cd, daily_avg, expiration_days)
        return daily_avg * safety_days, None

    # 담배: 신규 3종 분류 기반 계산
    if item_cd and TOBACCO_DYNAMIC_SAFETY_CONFIG["enabled"]:
        pattern = classify_and_calculate_tobacco(
            mid_cd=mid_cd,
            item_cd=item_cd,
            daily_avg=daily_avg,
            current_stock=current_stock or 0,
            pending_qty=pending_qty,
            store_id=store_id,
        )
        return pattern.final_safety_stock, pattern

    # 기본값 (상품코드 없거나 비활성화 시)
    default_days = TOBACCO_DYNAMIC_SAFETY_CONFIG["default_safety_days"]
    return daily_avg * default_days, None
