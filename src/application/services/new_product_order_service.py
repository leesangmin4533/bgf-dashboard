"""
신상품 3일발주 분산 발주 서비스

기간 내 3회 발주를 균등 분산하며, 판매 없을 시 스킵 + D-3 강제 발주.
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
    """기간을 3등분한 발주 간격 (일수) 계산"""
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
    """다음 발주 예정일 계산

    next_order_date = week_start + (our_order_count * interval_days)
    """
    start = _parse_date(week_start)
    if not start:
        return ""
    next_dt = start + timedelta(days=our_order_count * interval_days)
    return next_dt.strftime("%Y-%m-%d")


def should_order_today(
    today: str,
    week_end: str,
    next_order_date: str,
    our_order_count: int,
    last_sale_after_order: int,
    current_stock: int = 0,
) -> tuple:
    """오늘 발주 여부 판단 (분산 발주 로직)

    Args:
        today: 오늘 날짜 (YYYY-MM-DD)
        week_end: 주차 종료일
        next_order_date: 다음 발주 예정일
        our_order_count: 이미 발주한 횟수
        last_sale_after_order: 마지막 발주 이후 판매량
        current_stock: 현재 재고

    Returns:
        (should_order: bool, reason: str, action: str)
        action: "order" | "skip" | "force" | "none"
    """
    # 이미 3회 달성
    if our_order_count >= NEW_PRODUCT_DS_MIN_ORDERS:
        return False, "이미 3회 발주 완료", "none"

    today_dt = _parse_date(today)
    next_dt = _parse_date(next_order_date)
    end_dt = _parse_date(week_end)

    if not today_dt or not end_dt:
        return False, "날짜 파싱 실패", "none"

    # 날짜 도달 전: 절대 발주 안 함
    if next_dt and today_dt < next_dt:
        return False, f"발주 예정일({next_order_date}) 미도달", "none"

    # 날짜 도달 후
    remaining_days = (end_dt - today_dt).days

    # D-3 이내 미달성 → 강제 발주 (기간 놓치지 않기 위해)
    if remaining_days <= NEW_PRODUCT_DS_FORCE_REMAINING_DAYS:
        return True, f"기간 잔여 {remaining_days}일 — 강제 발주", "force"

    # 이전 발주 존재 + 판매 없음 + 재고 있음 → 스킵
    if our_order_count > 0 and last_sale_after_order == 0 and current_stock > 0:
        return False, f"판매 없음(재고 {current_stock}) — 스킵 후 재체크", "skip"

    # 그 외: 발주 실행
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

    repo = NP3DayTrackingRepo(store_id=store_id)
    orders_to_place = []

    # 모든 활성 주차에서 미완료 항목 조회
    active_items = _get_all_active_items_in_range(repo, store_id, today)

    # week_label별로 그룹핑 후 base_name 그룹 처리
    items_by_week = {}
    for item in active_items:
        wl = item.get("week_label", "")
        if wl not in items_by_week:
            items_by_week[wl] = []
        items_by_week[wl].append(item)

    for week_label, week_items in items_by_week.items():
        # base_name 그룹핑
        groups = group_by_base_name(week_items)

        for group in groups:
            base_name = group["base_name"]
            variants = group["variants"]

            # 그룹의 대표 항목에서 추적 정보 가져오기 (base_name 기반 조회)
            tracking = repo.get_group_tracking(store_id, week_label, base_name)
            if tracking:
                our_count = tracking.get("our_order_count", 0)
                next_date = tracking.get("next_order_date", "")
                week_end = tracking.get("week_end", "")
                last_sale = tracking.get("last_sale_after_order", 0)
                last_ordered_at = tracking.get("last_ordered_at", "")
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
                last_sale = first_item.get("last_sale_after_order", 0)
                last_ordered_at = first_item.get("last_ordered_at", "")

            # 판매량 업데이트 (그룹 내 모든 variant 합산)
            if sales_fn and our_count > 0:
                total_sale = 0
                for v in variants:
                    try:
                        total_sale += sales_fn(v["product_code"], last_ordered_at)
                    except Exception as e:
                        logger.warning(f"판매량 조회 실패 {v['product_code']}: {e}")
                last_sale = total_sale

            # 재고 조회 (그룹 내 모든 variant 합산)
            current_stock = 0
            if stock_fn:
                for v in variants:
                    try:
                        current_stock += stock_fn(v["product_code"])
                    except Exception:
                        pass

            should, reason, action = should_order_today(
                today=today,
                week_end=week_end,
                next_order_date=next_date,
                our_order_count=our_count,
                last_sale_after_order=last_sale,
                current_stock=current_stock,
            )

            if action == "skip":
                # 그룹의 모든 variant에 스킵 기록
                for v in variants:
                    repo.record_skip(store_id, week_label, v["product_code"])
                logger.info(f"신상품3일 스킵: {base_name} ({reason})")
                continue

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
                    logger.info(f"신상품3일 발주대상: {selected['product_code']} (그룹={base_name}, {reason})")

    return orders_to_place


def record_order_completed(
    store_id: str,
    week_label: str,
    product_code: str,
    week_start: str = "",
    interval_days: int = 0,
    our_order_count_after: int = 0,
    base_name: str = "",
    selected_code: str = "",
) -> None:
    """발주 완료 후 추적 업데이트

    our_order_count 증가 + next_order_date 갱신 + 3회 달성 시 is_completed
    """
    from src.infrastructure.database.repos import NP3DayTrackingRepo

    repo = NP3DayTrackingRepo(store_id=store_id)

    # 다음 발주 예정일 계산
    next_date = ""
    if week_start and interval_days > 0:
        next_date = calculate_next_order_date(week_start, our_order_count_after, interval_days)

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
        logger.info(f"신상품3일 완료: {base_name or product_code} (3회 달성)")


def merge_with_ai_orders(
    ai_orders: List[Dict],
    new_product_orders: List[Dict],
) -> List[Dict]:
    """AI 예측 발주 목록에 신상품 발주 합산

    - AI 목록에 이미 있는 상품: qty += 신상품 qty, 라벨 추가
    - AI 목록에 없는 신상품: 앞에 삽입 (force_order)

    Args:
        ai_orders: AI 예측 발주 목록 (item_cd/final_order_qty 키)
        new_product_orders: 신상품 발주 목록 (product_code/qty 키)

    Returns:
        합산된 발주 목록 (ai_orders 원본을 수정하지 않고 새 리스트 반환)
    """
    result = list(ai_orders)
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
            # AI가 이미 예측한 상품 → 수량 합산
            idx = ai_code_map[code]
            result[idx]["final_order_qty"] = result[idx].get("final_order_qty", 0) + qty
            existing_label = result[idx].get("label", "")
            result[idx]["label"] = (existing_label + "+신상품3일").lstrip("+")
            result[idx]["new_product_3day"] = True
            logger.info(
                f"신상품3일 합산: {code} AI={result[idx].get('final_order_qty', 0) - qty}+"
                f"{qty}={result[idx].get('final_order_qty', 0)}"
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
                "orderable_day": "일월화수목금토",
                "week_label": np_item.get("week_label", ""),
            })
            logger.info(f"신상품3일 신규삽입: {code} qty={qty}")

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
