"""
신상품 3일발주 분산 발주 서비스

기간 내 3회 발주를 판매/폐기 기반 동적 간격으로 분산.
- 판매 or 폐기 발생 → 다음 날 즉시 재발주
- 미판매+미폐기 → 3일 후 재발주
- D-5 이내 미달성 → 강제 발주
AI 예측 발주량과 합산하여 총량 발주.
1/2차 상품은 base_name 기준으로 그룹핑하여 그룹당 1회 발주.
"""

import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Callable

from src.settings.constants import (
    NEW_PRODUCT_DS_MIN_ORDERS,
    NEW_PRODUCT_INTRO_ORDER_QTY,
    NEW_PRODUCT_DS_FORCE_REMAINING_DAYS,
    NEW_PRODUCT_DS_NO_SALE_INTERVAL_DAYS,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


def extract_base_name(product_name: str) -> str:
    """상품명 말미 숫자 제거하여 기본명 추출

    예: "샌)대만식대파크림샌드2" → "샌)대만식대파크림샌드"
        "햄)아메리칸더블치폴레1" → "햄)아메리칸더블치폴레"
        "줄)콘버터아끼소바" → "줄)콘버터아끼소바" (숫자 없음)
        "도)해청양닭밥도템10" → "도)해청양닭밥도템"
    """
    if not product_name:
        return product_name
    return re.sub(r'\d+$', '', product_name).strip()


def group_by_base_name(products: List[Dict]) -> List[Dict]:
    """base_name 기준으로 상품 그룹핑

    Args:
        products: [{"product_code", "product_name", "bgf_current_count", ...}, ...]

    Returns:
        [{
            "base_name": "샌)대만식대파크림샌드",
            "sub_category": "샌드위치",
            "variants": [{"product_code", "product_name", "bgf_current_count"}, ...],
            "bgf_min_count": 1,
        }, ...]
    """
    groups = {}
    for p in products:
        name = p.get("product_name", "")
        base = extract_base_name(name)
        if base not in groups:
            groups[base] = {
                "base_name": base,
                "sub_category": p.get("sub_category", ""),
                "variants": [],
            }
        groups[base]["variants"].append({
            "product_code": p.get("product_code", ""),
            "product_name": name,
            "bgf_current_count": p.get("bgf_current_count", p.get("bgf_order_count", 0)),
        })

    result = []
    for g in groups.values():
        g["bgf_min_count"] = min(
            (v.get("bgf_current_count", 0) for v in g["variants"]),
            default=0,
        )
        result.append(g)
    return result


def select_variant_to_order(group: Dict, sales_data: Optional[Dict] = None) -> Optional[Dict]:
    """그룹에서 오늘 발주할 variant 1개 선택

    Args:
        group: group_by_base_name() 반환 항목
        sales_data: {product_code: sale_qty, ...} 판매량 맵

    Returns:
        선택된 variant dict 또는 None
    """
    variants = group.get("variants", [])
    if not variants:
        return None
    if len(variants) == 1:
        return variants[0]

    if sales_data is None:
        sales_data = {}

    # 정렬: 판매량 내림차순 → 이름 오름차순 (1차 < 2차)
    sorted_variants = sorted(
        variants,
        key=lambda v: (-sales_data.get(v["product_code"], 0), v["product_name"]),
    )
    return sorted_variants[0]


def calculate_interval_days(week_start: str, week_end: str) -> int:
    """기간을 3등분한 발주 간격 (일수) 계산 — 레거시 호환용"""
    start = _parse_date(week_start)
    end = _parse_date(week_end)
    if not start or not end:
        return 1
    total_days = (end - start).days
    if total_days <= 0:
        return 1
    return max(1, total_days // NEW_PRODUCT_DS_MIN_ORDERS)


def calculate_next_order_date(
    week_start: str,
    our_order_count: int,
    interval_days: int,
) -> str:
    """다음 발주 예정일 계산 — 레거시 호환용

    next_order_date = week_start + (our_order_count * interval_days)
    """
    start = _parse_date(week_start)
    if not start:
        return ""
    next_dt = start + timedelta(days=our_order_count * interval_days)
    return next_dt.strftime("%Y-%m-%d")


def calculate_dynamic_next_order_date(
    today: str,
    sold_qty: int = 0,
    wasted_qty: int = 0,
) -> str:
    """판매/폐기 기반 동적 다음 발주 예정일 계산

    - 판매 or 폐기 발생 → 다음 날 즉시 재발주
    - 판매도 폐기도 없음 → 3일 후 재발주

    Args:
        today: 오늘 날짜 (YYYY-MM-DD)
        sold_qty: 마지막 발주 이후 판매량
        wasted_qty: 마지막 발주 이후 폐기량

    Returns:
        다음 발주 예정일 (YYYY-MM-DD)
    """
    today_dt = _parse_date(today)
    if not today_dt:
        return ""

    if sold_qty > 0 or wasted_qty > 0:
        # 판매 있음 OR 폐기 있음 → 다음 날 즉시 재발주
        next_dt = today_dt + timedelta(days=1)
    else:
        # 판매도 없고 폐기도 없음 → 3일 후
        next_dt = today_dt + timedelta(days=NEW_PRODUCT_DS_NO_SALE_INTERVAL_DAYS)

    return next_dt.strftime("%Y-%m-%d")


def should_order_today(
    today: str,
    week_end: str,
    next_order_date: str,
    our_order_count: int,
    **kwargs,
) -> tuple:
    """오늘 발주 여부 판단 (동적 간격 로직)

    Args:
        today: 오늘 날짜 (YYYY-MM-DD)
        week_end: 주차 종료일
        next_order_date: 다음 발주 예정일
        our_order_count: 이미 발주한 횟수
        **kwargs: 하위 호환용 (last_sale_after_order, current_stock 등 무시)

    Returns:
        (should_order: bool, reason: str, action: str)
        action: "order" | "force" | "none"
    """
    # 이미 3회 달성
    if our_order_count >= NEW_PRODUCT_DS_MIN_ORDERS:
        logger.info(f"[신상품3일발주] 판단: 이미 3회 완료 (count={our_order_count})")
        return False, "이미 3회 발주 완료", "none"

    today_dt = _parse_date(today)
    end_dt = _parse_date(week_end)

    if not today_dt or not end_dt:
        logger.warning(f"[신상품3일발주] 판단: 날짜 파싱 실패 (today={today}, week_end={week_end})")
        return False, "날짜 파싱 실패", "none"

    # 첫 발주 전 → 즉시 발주
    if our_order_count == 0:
        logger.info(f"[신상품3일발주] 판단: 첫 발주 즉시 실행 (today={today})")
        return True, "첫 발주 — 즉시 실행", "order"

    # D-5 이내 미달성 → 강제 발주 (보험)
    remaining_days = (end_dt - today_dt).days
    if remaining_days <= NEW_PRODUCT_DS_FORCE_REMAINING_DAYS:
        logger.info(
            f"[신상품3일발주] 판단: D-{remaining_days} 강제 발주 "
            f"(count={our_order_count}, week_end={week_end})"
        )
        return True, f"기간 잔여 {remaining_days}일 — 강제 발주", "force"

    # next_order_date 미도달 → 대기
    next_dt = _parse_date(next_order_date)
    if next_dt and today_dt < next_dt:
        logger.info(
            f"[신상품3일발주] 판단: 대기 — 예정일 {next_order_date} 미도달 "
            f"(count={our_order_count})"
        )
        return False, f"발주 예정일({next_order_date}) 미도달", "none"

    # next_order_date 도달 → 발주
    logger.info(
        f"[신상품3일발주] 판단: 예정일 도달 ({next_order_date}) → 발주 실행 "
        f"(count={our_order_count})"
    )
    return True, f"발주 예정일 도달 ({next_order_date})", "order"


def get_today_new_product_orders(
    store_id: str,
    sales_fn: Optional[Callable] = None,
    stock_fn: Optional[Callable] = None,
    today: Optional[str] = None,
) -> List[Dict]:
    """오늘 발주할 신상품 목록 조회 (그룹 단위)

    1/2차 상품을 base_name으로 그룹핑하여, 그룹당 should_order_today 1회 판단.
    발주 대상이면 select_variant_to_order로 1개 선택.

    Args:
        store_id: 매장 코드
        sales_fn: 판매량 조회 함수 (item_cd, last_ordered_at) -> int
        stock_fn: 재고 조회 함수 (item_cd) -> int
        today: 오늘 날짜 (테스트용, 기본=오늘)

    Returns:
        [{"product_code": ..., "product_name": ..., "sub_category": ..., "qty": 1, ...}, ...]
    """
    from src.infrastructure.database.repos import NP3DayTrackingRepo

    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    today_dt = _parse_date(today)
    if not today_dt:
        return []

    logger.info(f"[신상품3일발주] 발주대상 조회 시작 (store={store_id}, today={today})")

    repo = NP3DayTrackingRepo(store_id=store_id)
    orders_to_place = []

    # 모든 활성 주차에서 미완료 항목 조회
    active_items = _get_all_active_items_in_range(repo, store_id, today)

    if not active_items:
        logger.info("[신상품3일발주] 활성 항목 없음 — 스킵")
        return []
    logger.info(f"[신상품3일발주] 활성 항목 {len(active_items)}건 조회 완료")

    # week_label별로 그룹핑 후 base_name 그룹 처리
    items_by_week = {}
    for item in active_items:
        wl = item.get("week_label", "")
        if wl not in items_by_week:
            items_by_week[wl] = []
        items_by_week[wl].append(item)

    # 마감 빠른 주차(week_end 오름차순) 우선 처리 — 주차간 중복 방지
    sorted_weeks = sorted(
        items_by_week.items(),
        key=lambda x: min(i.get("week_end", "9999") for i in x[1]),
    )
    seen_base_names: set = set()

    for week_label, week_items in sorted_weeks:
        # base_name 그룹핑
        groups = group_by_base_name(week_items)

        for group in groups:
            base_name = group["base_name"]
            variants = group["variants"]

            # 주차간 중복 방지: 마감 빠른 주차가 이미 이 base_name을 점유
            if base_name in seen_base_names:
                logger.info(f"[신상품3일발주] 주차간 중복 스킵: {base_name} (week={week_label})")
                continue
            seen_base_names.add(base_name)

            # 그룹의 대표 항목에서 추적 정보 가져오기 (base_name 기반 조회)
            tracking = repo.get_group_tracking(store_id, week_label, base_name)
            if tracking:
                our_count = tracking.get("our_order_count", 0)
                next_date = tracking.get("next_order_date", "")
                week_end = tracking.get("week_end", "")
            else:
                # base_name 레코드 없으면 variants 중 첫 항목 기준
                first_item = week_items[0]
                for item in week_items:
                    if extract_base_name(item.get("product_name", "")) == base_name:
                        first_item = item
                        break
                our_count = first_item.get("our_order_count", 0)
                next_date = first_item.get("next_order_date", "")
                week_end = first_item.get("week_end", "")

            should, reason, action = should_order_today(
                today=today,
                week_end=week_end,
                next_order_date=next_date,
                our_order_count=our_count,
            )

            if should:
                # 판매 데이터로 variant 선택
                sales_data = {}
                if sales_fn:
                    for v in variants:
                        try:
                            sales_data[v["product_code"]] = sales_fn(v["product_code"], "")
                        except Exception:
                            sales_data[v["product_code"]] = 0

                selected = select_variant_to_order(group, sales_data)
                if selected:
                    orders_to_place.append({
                        "product_code": selected["product_code"],
                        "product_name": selected["product_name"],
                        "base_name": base_name,
                        "sub_category": group.get("sub_category", ""),
                        "qty": NEW_PRODUCT_INTRO_ORDER_QTY,
                        "week_label": week_label,
                        "source": "new_product_3day_distributed",
                    })
                    logger.info(
                        f"[신상품3일발주] 발주대상: {selected['product_code']} "
                        f"({selected['product_name']}) 그룹={base_name}, {reason}"
                    )

    logger.info(
        f"[신상품3일발주] 발주대상 조회 완료: {len(orders_to_place)}건 "
        f"(활성주차 {len(sorted_weeks)}개)"
    )
    return orders_to_place


def record_order_completed(
    store_id: str,
    week_label: str,
    product_code: str,
    our_order_count_after: int = 0,
    base_name: str = "",
    selected_code: str = "",
    sold_qty: int = 0,
    wasted_qty: int = 0,
    today: str = "",
    # 레거시 호환 파라미터 (무시)
    week_start: str = "",
    interval_days: int = 0,
) -> None:
    """발주 완료 후 추적 업데이트

    our_order_count 증가 + 판매/폐기 기반 동적 next_order_date 갱신 + 3회 달성 시 is_completed
    """
    from src.infrastructure.database.repos import NP3DayTrackingRepo

    repo = NP3DayTrackingRepo(store_id=store_id)

    # 다음 발주 예정일 계산 (동적 간격)
    if not today:
        today = datetime.now().strftime("%Y-%m-%d")
    next_date = calculate_dynamic_next_order_date(today, sold_qty, wasted_qty)

    # 동적 간격 사유 결정
    if sold_qty > 0:
        interval_reason = f"판매감지(sold={sold_qty})"
    elif wasted_qty > 0:
        interval_reason = f"폐기감지(wasted={wasted_qty})"
    else:
        interval_reason = "미판매+미폐기"

    item_label = base_name or product_code
    logger.info(
        f"[신상품3일발주] 추적: {item_label} count={our_order_count_after} "
        f"next={next_date} 사유={interval_reason}"
    )

    # base_name이 있으면 그룹 단위로 업데이트
    if base_name:
        repo.record_order_by_base_name(store_id, week_label, base_name, next_date, selected_code)
    else:
        repo.record_order(store_id, week_label, product_code, next_date)

    # 3회 달성 시 완료 표시
    if our_order_count_after >= NEW_PRODUCT_DS_MIN_ORDERS:
        if base_name:
            repo.mark_completed_by_base_name(store_id, week_label, base_name)
        else:
            repo.mark_completed(store_id, week_label, product_code)
        logger.info(f"[신상품3일발주] 완료: {item_label} — 3회 달성 (week={week_label})")


def merge_with_ai_orders(
    ai_orders: List[Dict],
    new_product_orders: List[Dict],
) -> List[Dict]:
    """AI 예측 발주 목록에 신상품 3일발주 통합

    - AI 목록에 이미 있는 상품: 수량 변경 없음, np_3day_tracking 플래그만 표시 (횟수 인정)
    - AI 목록에 없는 신상품: 앞에 삽입 (force_order, qty=1)

    발주 완료 후 np_3day_tracking=True인 상품은 모두 our_order_count += 1 처리.

    Args:
        ai_orders: AI 예측 발주 목록 (item_cd/final_order_qty 키)
        new_product_orders: 신상품 발주 목록 (product_code/qty 키)

    Returns:
        통합된 발주 목록 (ai_orders 원본을 수정하지 않고 새 리스트 반환)
    """
    result = [dict(item) for item in ai_orders]
    ai_code_map = {}
    for i, item in enumerate(result):
        code = item.get("item_cd", "")
        if code:
            ai_code_map[code] = i

    inserted = []
    for np_item in new_product_orders:
        code = np_item.get("product_code", "")
        qty = np_item.get("qty", 1)

        if code in ai_code_map:
            # AI가 이미 발주 예정 → 수량 변경 없음, 추적 플래그만 표시
            # 발주 완료 후 our_order_count 증가 처리 (횟수 인정)
            idx = ai_code_map[code]
            result[idx]["np_3day_tracking"] = True
            result[idx]["new_product_3day"] = True
            logger.info(
                f"[신상품3일발주] AI중복: {code} "
                f"({result[idx].get('item_nm', '')}) "
                f"AI수량={result[idx].get('final_order_qty', 0)} 유지 (횟수만 인정)"
            )
        else:
            # AI 목록에 없는 신상품 → 앞에 삽입
            inserted.append({
                "item_cd": code,
                "item_nm": np_item.get("product_name", ""),
                "mid_cd": "",
                "final_order_qty": qty,
                "source": np_item.get("source", "new_product_3day_distributed"),
                "label": "신상품3일발주",
                "force_order": True,
                "new_product_3day": True,
                "np_3day_tracking": True,
                "orderable_day": "일월화수목금토",
                "week_label": np_item.get("week_label", ""),
            })
            logger.info(
                f"[신상품3일발주] AI미등록 강제삽입: {code} "
                f"({np_item.get('product_name', '')}) qty={qty}"
            )

    return inserted + result


# ─── 내부 헬퍼 ───

def _parse_date(date_str: str) -> Optional[datetime]:
    """날짜 문자열 파싱"""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%y.%m.%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


def _get_all_active_items_in_range(repo, store_id: str, today: str) -> List[Dict]:
    """현재 날짜가 week_start~week_end 범위에 속하는 모든 미완료 항목 조회"""
    conn = repo._get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT * FROM new_product_3day_tracking
               WHERE store_id = ?
                 AND is_completed = 0
                 AND week_start <= ?
                 AND week_end >= ?
               ORDER BY next_order_date, product_code""",
            (store_id, today, today),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()
