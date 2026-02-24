"""
푸드류 요일별 총량 상한 + 탐색/활용 상품 선별

푸드류(001~005, 012) 발주 시 요일별 평균 판매량 기반으로 총 발주량을 제한하고,
검증된 상품(활용) + 신상품(탐색)을 혼합 배치하여 폐기를 최소화.

현재: 주먹밥(002) 37개 상품 × 1개 = 39개 발주 → 일 판매 ~14개, 폐기 ~23개
목표: 요일 평균 판매 + 3(폐기 허용) = 13~20개로 제한, 폐기 ~3개
"""

import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# 설정 상수
# =============================================================================
FOOD_DAILY_CAP_CONFIG = {
    "enabled": True,
    "target_categories": ['001', '002', '003', '004', '005', '012'],
    "waste_buffer": 3,           # 폐기 허용 수량
    "lookback_days": 21,         # 요일별 평균 계산 기간
    "min_data_weeks": 2,         # 최소 데이터 주 수
    "explore_ratio": 0.25,       # 탐색 슬롯 비율 (25%)
    "new_item_days": 7,          # 신상품 판별 기준 (첫 등장 N일 이내)
    "new_item_max_data": 5,      # 신상품 데이터 일수 상한
    "fallback_daily_avg": 15.0,  # 데이터 부족 시 기본값 (float: 일평균 판매량)
    "explore_fail_threshold": 3,   # N일 연속 판매 0이면 탐색 실패로 판정
    "explore_fail_enabled": True,  # 탐색 실패 필터 활성화
}


def _get_db_path() -> str:
    """DB 경로 반환"""
    return str(Path(__file__).parent.parent.parent.parent / "data" / "bgf_sales.db")


# =============================================================================
# 요일별 평균 판매량 조회
# =============================================================================
def get_weekday_avg_sales(
    mid_cd: str,
    weekday: int,
    lookback_days: Optional[int] = None,
    db_path: Optional[str] = None,
    store_id: Optional[str] = None
) -> float:
    """
    특정 중분류의 요일별 평균 총 판매량 조회

    Args:
        mid_cd: 중분류 코드 (예: '002')
        weekday: 요일 번호 (0=월, 1=화, ..., 6=일)
        lookback_days: 조회 기간 (기본: 21일)
        db_path: DB 경로
        store_id: 점포 코드 (None이면 전체 점포)

    Returns:
        해당 요일의 일평균 총 판매량
    """
    config = FOOD_DAILY_CAP_CONFIG
    if lookback_days is None:
        lookback_days = config["lookback_days"]
    if db_path is None:
        db_path = _get_db_path()

    try:
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            cursor = conn.cursor()

            # 최근 lookback_days일의 일별 총 판매량 조회
            if store_id:
                cursor.execute("""
                    SELECT sales_date, SUM(sale_qty) as daily_total
                    FROM daily_sales
                    WHERE mid_cd = ?
                      AND store_id = ?
                      AND sales_date >= date('now', ?)
                    GROUP BY sales_date
                """, (mid_cd, store_id, f'-{lookback_days} days'))
            else:
                cursor.execute("""
                    SELECT sales_date, SUM(sale_qty) as daily_total
                    FROM daily_sales
                    WHERE mid_cd = ?
                      AND sales_date >= date('now', ?)
                    GROUP BY sales_date
                """, (mid_cd, f'-{lookback_days} days'))

            rows = cursor.fetchall()
        finally:
            conn.close()

        if not rows:
            logger.debug(f"mid_cd={mid_cd} 데이터 없음, fallback 사용")
            return config["fallback_daily_avg"]

        # Python에서 요일 필터링 (SQLite strftime 대신 정확도 보장)
        weekday_totals = []
        all_totals = []

        for sales_date_str, daily_total in rows:
            try:
                dt = datetime.strptime(sales_date_str, "%Y-%m-%d")
                all_totals.append(daily_total or 0)
                if dt.weekday() == weekday:
                    weekday_totals.append(daily_total or 0)
            except (ValueError, TypeError):
                continue

        # 최소 데이터 주 수 확인
        min_weeks = config["min_data_weeks"]
        if len(weekday_totals) >= min_weeks:
            avg = sum(weekday_totals) / len(weekday_totals)
            logger.debug(f"mid_cd={mid_cd}, 요일={weekday}: {len(weekday_totals)}주 평균 {avg:.1f}개")
            return avg

        # 요일 데이터 부족 시 전체 평균 사용
        if all_totals:
            avg = sum(all_totals) / len(all_totals)
            logger.debug(f"mid_cd={mid_cd}: 요일 데이터 부족({len(weekday_totals)}주), 전체 평균 {avg:.1f}개 사용")
            return avg

        return config["fallback_daily_avg"]

    except Exception as e:
        logger.warning(f"요일별 평균 조회 실패 (mid_cd={mid_cd}): {e}")
        return config["fallback_daily_avg"]


# =============================================================================
# 탐색 실패 상품 판별
# =============================================================================
def get_explore_failed_items(
    item_codes: List[str],
    mid_cd: str,
    db_path: Optional[str] = None,
    store_id: Optional[str] = None
) -> set:
    """
    최근 N일 연속 판매 0인 신상품을 탐색 실패로 판별

    daily_sales에 레코드가 존재하지만(진열됨) sale_qty=0인 날이
    explore_fail_threshold일 이상 연속되면 탐색 실패로 판정.

    Args:
        item_codes: 판별 대상 상품코드 목록
        mid_cd: 중분류 코드
        db_path: DB 경로
        store_id: 점포 코드 (None이면 전체 점포)

    Returns:
        탐색 실패 상품코드 set
    """
    config = FOOD_DAILY_CAP_CONFIG

    if not item_codes:
        return set()

    threshold = config["explore_fail_threshold"]
    if db_path is None:
        db_path = _get_db_path()

    try:
        conn = sqlite3.connect(db_path, timeout=30)
        cursor = conn.cursor()

        placeholders = ','.join('?' * len(item_codes))
        if store_id:
            cursor.execute(f"""
                SELECT item_cd,
                       COUNT(*) as record_days,
                       SUM(CASE WHEN sale_qty > 0 THEN 1 ELSE 0 END) as sell_days
                FROM daily_sales
                WHERE item_cd IN ({placeholders})
                  AND mid_cd = ?
                  AND store_id = ?
                  AND sales_date >= date('now', ?)
                GROUP BY item_cd
                HAVING record_days >= ? AND sell_days = 0
            """, (*item_codes, mid_cd, store_id, f'-{threshold} days', threshold))
        else:
            cursor.execute(f"""
                SELECT item_cd,
                       COUNT(*) as record_days,
                       SUM(CASE WHEN sale_qty > 0 THEN 1 ELSE 0 END) as sell_days
                FROM daily_sales
                WHERE item_cd IN ({placeholders})
                  AND mid_cd = ?
                  AND sales_date >= date('now', ?)
                GROUP BY item_cd
                HAVING record_days >= ? AND sell_days = 0
            """, (*item_codes, mid_cd, f'-{threshold} days', threshold))

        failed = {row[0] for row in cursor.fetchall()}
        conn.close()

        if failed:
            logger.debug(
                f"탐색 실패 판별 mid_cd={mid_cd}: "
                f"{len(failed)}/{len(item_codes)}개 (최근 {threshold}일 판매 0)"
            )

        return failed

    except Exception as e:
        logger.warning(f"탐색 실패 판별 오류 (mid_cd={mid_cd}): {e}")
        return set()


# =============================================================================
# 상품 분류 (검증/신상품)
# =============================================================================
def classify_items(
    order_list: List[Dict[str, Any]],
    mid_cd: str,
    db_path: Optional[str] = None,
    store_id: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    발주 목록의 상품을 검증 상품과 신상품으로 분류

    분류 기준:
    - 검증 상품 (proven): data_days >= 6 (14일 중 6일 이상 판매 데이터)
    - 신상품 (new): data_days < new_item_max_data(5)

    탐색 실패 필터:
    - 최근 N일 연속 판매 0인 신상품은 탐색 대상에서 제외

    Args:
        order_list: 해당 mid_cd의 발주 목록
        mid_cd: 중분류 코드
        db_path: DB 경로 (탐색 실패 판별용)
        store_id: 점포 코드 (None이면 전체 점포)

    Returns:
        (proven_items, new_items)
    """
    config = FOOD_DAILY_CAP_CONFIG
    new_item_max_data = config["new_item_max_data"]

    proven_items = []
    new_items = []

    for item in order_list:
        data_days = item.get("data_days", 0) or 0

        if data_days >= 6:
            proven_items.append(item)
        elif data_days < new_item_max_data:
            new_items.append(item)
        else:
            # 6 > data_days >= 5: 중간 영역 → proven으로 분류
            proven_items.append(item)

    # 정렬: data_days 내림차순 (데이터 많은 순)
    proven_items.sort(key=lambda x: -(x.get("data_days", 0) or 0))
    new_items.sort(key=lambda x: -(x.get("data_days", 0) or 0))

    # 탐색 실패 상품 필터링 (최근 N일 연속 판매 0인 신상품 제외)
    if config["explore_fail_enabled"] and new_items:
        new_item_codes = [item.get("item_cd") for item in new_items]
        failed_items = get_explore_failed_items(new_item_codes, mid_cd, db_path, store_id=store_id)
        if failed_items:
            original_count = len(new_items)
            new_items = [item for item in new_items if item.get("item_cd") not in failed_items]
            logger.info(
                f"탐색 실패 제외 mid_cd={mid_cd}: "
                f"{original_count - len(new_items)}개 제외 "
                f"(최근 {config['explore_fail_threshold']}일 판매 0)"
            )

    return proven_items, new_items


# =============================================================================
# 상품 선별 (cap 적용)
# =============================================================================
def select_items_with_cap(
    order_list: List[Dict[str, Any]],
    total_cap: int,
    explore_ratio: Optional[float] = None,
    db_path: Optional[str] = None,
    store_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    총량 상한 내에서 탐색/활용 상품 혼합 선별

    예시:
        total_cap = 20, explore_ratio = 0.25
        → explore_slots = 5, exploit_slots = 15
        1. proven 상품에서 상위 15개 선택
        2. new 상품에서 상위 5개 선택
        3. new 부족 시 → proven에서 추가 채움
        4. proven 부족 시 → new에서 추가 채움

    Args:
        order_list: 해당 mid_cd의 발주 목록
        total_cap: 총량 상한
        explore_ratio: 탐색 슬롯 비율 (기본: 0.25)
        db_path: DB 경로 (탐색 실패 판별용)
        store_id: 점포 코드 (None이면 전체 점포)

    Returns:
        선별된 발주 목록 (각 상품 final_order_qty=1 유지)
    """
    config = FOOD_DAILY_CAP_CONFIG
    if explore_ratio is None:
        explore_ratio = config["explore_ratio"]

    if not order_list:
        return []

    # 상품 분류
    mid_cd = order_list[0].get("mid_cd", "")
    proven_items, new_items = classify_items(order_list, mid_cd, db_path, store_id=store_id)

    # 슬롯 계산
    explore_slots = round(total_cap * explore_ratio)
    exploit_slots = total_cap - explore_slots

    # 1. proven 상품에서 exploit_slots만큼 선택
    selected_proven = proven_items[:exploit_slots]

    # 2. new 상품에서 explore_slots만큼 선택
    selected_new = new_items[:explore_slots]

    # 3. 부족분 채우기
    remaining_slots = total_cap - len(selected_proven) - len(selected_new)

    if remaining_slots > 0:
        if len(selected_new) < explore_slots:
            # new 부족 → proven에서 추가
            already_selected = {item.get('item_cd') for item in selected_proven}
            for item in proven_items:
                if remaining_slots <= 0:
                    break
                if item.get('item_cd') not in already_selected:
                    selected_proven.append(item)
                    already_selected.add(item.get('item_cd'))
                    remaining_slots -= 1

        if remaining_slots > 0 and len(selected_proven) < exploit_slots:
            # proven 부족 → new에서 추가
            already_selected = {item.get('item_cd') for item in selected_new}
            for item in new_items:
                if remaining_slots <= 0:
                    break
                if item.get('item_cd') not in already_selected:
                    selected_new.append(item)
                    already_selected.add(item.get('item_cd'))
                    remaining_slots -= 1

    # 합치기
    selected = selected_proven + selected_new

    logger.debug(
        f"mid_cd={mid_cd}: cap={total_cap}, "
        f"proven={len(selected_proven)}/{len(proven_items)}, "
        f"new={len(selected_new)}/{len(new_items)}"
    )

    return selected


# =============================================================================
# 메인 진입점
# =============================================================================
def apply_food_daily_cap(
    order_list: List[Dict[str, Any]],
    target_date: Optional[datetime] = None,
    db_path: Optional[str] = None,
    store_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    푸드류 요일별 총량 상한 적용 (메인 진입점)

    1. target_date에서 weekday 추출
    2. mid_cd별로 order_list 그룹핑
    3. 각 food mid_cd에 대해:
       a. weekday_avg = get_weekday_avg_sales(mid_cd, weekday, store_id)
       b. total_cap = round(weekday_avg) + waste_buffer
       c. 해당 mid_cd 상품이 cap 이하면 그대로 유지
       d. cap 초과 시 select_items_with_cap()으로 선별
    4. food 아닌 상품은 그대로 통과
    5. 전체 합쳐서 반환

    Args:
        order_list: 전체 발주 목록
        target_date: 발주 대상 날짜 (None이면 내일)
        db_path: DB 경로
        store_id: 점포 코드 (None이면 전체 점포)

    Returns:
        총량 상한이 적용된 발주 목록
    """
    config = FOOD_DAILY_CAP_CONFIG

    if not config["enabled"]:
        return order_list

    if not order_list:
        return order_list

    target_categories = config["target_categories"]
    waste_buffer = config["waste_buffer"]

    # 요일 추출
    if target_date is None:
        target_date = datetime.now() + timedelta(days=1)
    weekday = target_date.weekday()

    # mid_cd별 그룹핑
    grouped = defaultdict(list)
    for item in order_list:
        mid_cd = item.get("mid_cd", "")
        grouped[mid_cd].append(item)

    result = []
    cap_applied_count = 0

    for mid_cd, items in grouped.items():
        # 푸드류가 아닌 상품은 그대로 통과
        if mid_cd not in target_categories:
            result.extend(items)
            continue

        # 요일별 평균 판매량 조회
        weekday_avg = get_weekday_avg_sales(mid_cd, weekday, db_path=db_path, store_id=store_id)

        # 총량 상한 계산 (보정값 우선)
        effective_buffer = waste_buffer
        try:
            from src.prediction.food_waste_calibrator import get_calibrated_food_params
            cal = get_calibrated_food_params(mid_cd, store_id, db_path)
            if cal is not None:
                effective_buffer = cal.waste_buffer
        except Exception:
            pass
        total_cap = round(weekday_avg) + effective_buffer

        # 현재 상품 수가 cap 이하면 그대로 유지
        current_count = len(items)
        if current_count <= total_cap:
            result.extend(items)
            logger.debug(
                f"mid_cd={mid_cd}: {current_count}개 ≤ cap {total_cap}개, 그대로 유지"
            )
            continue

        # cap 초과 시 선별
        selected = select_items_with_cap(items, total_cap, db_path=db_path, store_id=store_id)
        result.extend(selected)
        cap_applied_count += 1

        logger.info(
            f"푸드 총량 상한 적용 mid_cd={mid_cd}: "
            f"{current_count}개 → {len(selected)}개 "
            f"(요일평균 {weekday_avg:.1f} + 버퍼 {waste_buffer} = cap {total_cap})"
        )

    if cap_applied_count > 0:
        logger.info(f"푸드류 총량 상한: {cap_applied_count}개 카테고리에 적용됨")

    return result
