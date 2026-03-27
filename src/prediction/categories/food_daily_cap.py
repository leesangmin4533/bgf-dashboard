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
    "waste_buffer": 3,           # [DEPRECATED] 20%×category_total로 대체됨. 캘리브레이터 호환용 유지
    "lookback_days": 28,         # 요일별 평균 계산 기간 (예측 4주 윈도우와 정합)
    "min_data_weeks": 2,         # 최소 데이터 주 수
    "explore_ratio": 0.25,       # 탐색 슬롯 비율 (25%)
    "new_item_days": 7,          # 신상품 판별 기준 (첫 등장 N일 이내)
    "new_item_max_data": 5,      # 신상품 데이터 일수 상한
    "fallback_daily_avg": 15.0,  # 데이터 부족 시 기본값 (float: 일평균 판매량)
    "explore_fail_threshold": 3,   # N일 연속 판매 0이면 탐색 실패로 판정
    "explore_fail_enabled": True,  # 탐색 실패 필터 활성화
}


def _get_db_path(store_id: str = None) -> str:
    """DB 경로 반환"""
    from src.infrastructure.database.connection import resolve_db_path
    return resolve_db_path(store_id=store_id)


def _resolve_db_path(store_id: Optional[str], db_path: Optional[str]) -> str:
    """store_id가 있으면 매장 DB, 없으면 레거시 DB 경로 반환"""
    if db_path is not None:
        return db_path
    if store_id:
        try:
            from src.infrastructure.database.connection import DBRouter
            store_path = DBRouter.get_store_db_path(store_id)
            if store_path.exists():
                return str(store_path)
        except Exception:
            pass
    return _get_db_path()


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
    db_path = _resolve_db_path(store_id, db_path)

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
    db_path = _resolve_db_path(store_id, db_path)

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
# 수량 합산 절삭 (Cap 초과 방지)
# =============================================================================
def _trim_qty_to_cap(
    selected: List[Dict[str, Any]],
    cap: int,
    floor_protected_codes: Optional[set] = None
) -> List[Dict[str, Any]]:
    """
    선별된 품목의 수량 합이 cap을 초과하면 후순위부터 수량 절삭.

    우선순위: 리스트 앞쪽(proven/exploit)이 높고, 뒤쪽(new/explore)이 낮음.
    절삭 순서:
      1단계: Floor 비보호 품목을 뒤에서부터 절삭
      2단계: 그래도 초과 시 Floor 보호 품목도 뒤에서부터 절삭 (Cap 절대 초과 불가)
    """
    if not selected:
        return selected

    total_qty = sum(i.get("final_order_qty", 1) for i in selected)
    if total_qty <= cap:
        return selected

    floor_protected = floor_protected_codes or set()
    trimmed = [dict(i) for i in selected]  # shallow copy
    excess = total_qty - cap

    # 1단계: 비보호 품목 먼저 절삭 (뒤에서부터)
    for i in range(len(trimmed) - 1, -1, -1):
        if excess <= 0:
            break
        item_cd = trimmed[i].get("item_cd")
        if item_cd in floor_protected:
            continue  # Floor 보호 품목은 1단계에서 건너뜀
        qty = trimmed[i].get("final_order_qty", 1)
        reduce = min(qty, excess)
        new_qty = qty - reduce
        if new_qty <= 0:
            logger.debug(
                f"[CapTrim] {item_cd} 제거 (qty={qty}→0)"
            )
            trimmed.pop(i)
        else:
            trimmed[i]["final_order_qty"] = new_qty
            logger.debug(
                f"[CapTrim] {item_cd} qty 절삭 ({qty}→{new_qty})"
            )
        excess -= reduce

    # 2단계: 비보호로 부족하면 Floor 보호 품목도 절삭 (Cap 절대 우선)
    if excess > 0 and floor_protected:
        logger.info(
            f"[CapTrim] 비보호 절삭 후에도 {excess}개 초과 → Floor 보호 품목 절삭"
        )
        for i in range(len(trimmed) - 1, -1, -1):
            if excess <= 0:
                break
            qty = trimmed[i].get("final_order_qty", 1)
            reduce = min(qty, excess)
            new_qty = qty - reduce
            if new_qty <= 0:
                logger.debug(
                    f"[CapTrim] {trimmed[i].get('item_cd')} Floor보호 제거 (qty={qty}→0)"
                )
                trimmed.pop(i)
            else:
                trimmed[i]["final_order_qty"] = new_qty
                logger.debug(
                    f"[CapTrim] {trimmed[i].get('item_cd')} Floor보호 절삭 ({qty}→{new_qty})"
                )
            excess -= reduce

    return trimmed


# =============================================================================
# 상품 선별 (cap 적용)
# =============================================================================
def select_items_with_cap(
    order_list: List[Dict[str, Any]],
    total_cap: int,
    explore_ratio: Optional[float] = None,
    db_path: Optional[str] = None,
    store_id: Optional[str] = None,
    floor_protected_codes: Optional[set] = None
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
        floor_protected_codes (set, optional):
            Cap 절삭 시 후순위로 처리할 Floor 보충 품목 코드 집합.
            None이면 모든 품목 동일 순서로 절삭.

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

    # Floor 보호 품목이 선별에서 탈락했으면 다시 포함 (비보호 품목을 밀어냄)
    if floor_protected_codes:
        selected_codes = {item.get('item_cd') for item in selected}
        all_items_map = {item.get('item_cd'): item for item in order_list}
        missing_floor = [
            all_items_map[code] for code in floor_protected_codes
            if code in all_items_map and code not in selected_codes
        ]
        if missing_floor:
            # 비보호 품목 중 후순위를 제거하여 Floor 품목 삽입
            for floor_item in missing_floor:
                # 뒤에서부터 비보호 품목 하나 제거
                removed = False
                for i in range(len(selected) - 1, -1, -1):
                    if selected[i].get('item_cd') not in floor_protected_codes:
                        selected.pop(i)
                        removed = True
                        break
                selected.append(floor_item)
                if not removed:
                    # 비보호 남아있지 않으면 그냥 추가 (후속 _trim_qty_to_cap이 처리)
                    pass
            logger.info(
                f"[CapSelect] Floor 보호 {len(missing_floor)}개 품목 선별 복원 "
                f"(mid_cd={mid_cd})"
            )

    logger.debug(
        f"mid_cd={mid_cd}: cap={total_cap}, "
        f"proven={len(selected_proven)}/{len(proven_items)}, "
        f"new={len(selected_new)}/{len(new_items)}"
    )

    return selected


# =============================================================================
# 메인 진입점
# =============================================================================
def _get_pending_by_midcd(
    mid_cds: List[str],
    store_id: Optional[str] = None,
) -> Dict[str, int]:
    """카테고리별 입고예정(pending) 수량 조회

    어제+오늘 발주 중 remaining_qty > 0인 항목의 합계.
    004(샌드위치)/005(햄버거)처럼 유통기한이 2일 이상인 카테고리에서
    입고예정 수량을 Cap에 반영하기 위해 사용.

    Args:
        mid_cds: 조회 대상 중분류 코드 리스트
        store_id: 매장 코드

    Returns:
        {mid_cd: pending_qty_sum, ...}
    """
    if not mid_cds or not store_id:
        return {}

    try:
        from src.infrastructure.database.connection import DBRouter
        conn = DBRouter.get_connection(store_id=store_id, table="order_tracking")
        try:
            cursor = conn.cursor()
            placeholders = ','.join('?' * len(mid_cds))
            # 어제+오늘 발주만 대상 (오래된 미정리 건 제외)
            cursor.execute(f"""
                SELECT mid_cd, SUM(remaining_qty) as total_pending
                FROM order_tracking
                WHERE mid_cd IN ({placeholders})
                  AND remaining_qty > 0
                  AND status NOT IN ('invalidated', 'disposed')
                  AND order_date >= date('now', '-1 day', 'localtime')
                GROUP BY mid_cd
            """, mid_cds)
            return {row[0]: row[1] for row in cursor.fetchall()}
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[Cap] pending 조회 실패 (무시): {e}")
        return {}


# 입고예정 Cap 차감 대상 카테고리 (유통기한 2일 이상)
PENDING_CAP_DEDUCT_CATEGORIES = ['004', '005', '012']


def apply_food_daily_cap(
    order_list: List[Dict[str, Any]],
    target_date: Optional[datetime] = None,
    db_path: Optional[str] = None,
    store_id: Optional[str] = None,
    site_order_counts: Optional[Dict[str, int]] = None,
    floor_protected_codes: Optional[set] = None
) -> List[Dict[str, Any]]:
    """
    푸드류 요일별 총량 상한 적용 (메인 진입점)

    1. target_date에서 weekday 추출
    2. mid_cd별로 order_list 그룹핑
    3. 각 food mid_cd에 대해:
       a. weekday_avg = get_weekday_avg_sales(mid_cd, weekday, store_id)
       b. total_cap = round(weekday_avg) + waste_buffer
       c. site 발주 건수만큼 차감: adjusted_cap = max(0, total_cap - site_count)
       d. 해당 mid_cd 상품이 adjusted_cap 이하면 그대로 유지
       e. adjusted_cap 초과 시 select_items_with_cap()으로 선별
       f. Floor 보호 품목은 마지막에 절삭 (비보호 우선 절삭)
    4. food 아닌 상품은 그대로 통과
    5. 전체 합쳐서 반환

    Args:
        order_list: 전체 발주 목록
        target_date: 발주 대상 날짜 (None이면 내일)
        db_path: DB 경로
        store_id: 점포 코드 (None이면 전체 점포)
        site_order_counts: {mid_cd: int} site 발주 수량 (카테고리 예산 차감용)
        floor_protected_codes: Floor 보충된 품목 코드 set (절삭 시 후순위 보호)

    Returns:
        총량 상한이 적용된 발주 목록
    """
    config = FOOD_DAILY_CAP_CONFIG

    if not config["enabled"]:
        return order_list

    if not order_list:
        return order_list

    target_categories = config["target_categories"]

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

    # 004/005 입고예정 조회 (유통기한 2일+ → 입고분이 Cap에 영향)
    pending_by_mid = {}
    pending_target = [m for m in PENDING_CAP_DEDUCT_CATEGORIES if m in grouped]
    if pending_target and store_id:
        pending_by_mid = _get_pending_by_midcd(pending_target, store_id=store_id)

    for mid_cd, items in grouped.items():
        # 푸드류가 아닌 상품은 그대로 통과
        if mid_cd not in target_categories:
            result.extend(items)
            continue

        # 요일별 평균 판매량 조회
        weekday_avg = get_weekday_avg_sales(mid_cd, weekday, db_path=db_path, store_id=store_id)

        # 총량 상한 계산 (카테고리 총량의 20% 여유재고)
        # NOTE: total_cap과 비교 대상 모두 수량(qty) 기반.
        # 행사 부스트 등으로 품목당 qty>1이 될 수 있으므로 sum(qty) 비교 필수.
        site_qty = (site_order_counts or {}).get(mid_cd, 0)
        category_total = weekday_avg + site_qty
        effective_buffer = int(category_total * 0.20 + 0.5)
        total_cap = round(weekday_avg) + effective_buffer
        logger.info(
            f"[Cap] {mid_cd} buffer={effective_buffer} "
            f"(category_total={category_total:.1f} × 20%)"
        )

        # site(사용자) 발주 차감
        site_count = (site_order_counts or {}).get(mid_cd, 0)
        adjusted_cap = max(0, total_cap - site_count)
        if site_count > 0:
            logger.info(
                f"[SiteBudget] mid_cd={mid_cd}: "
                f"예산{total_cap} - site{site_count} = auto상한{adjusted_cap}"
            )

        # 004(샌드위치)/005(햄버거) 입고예정 차감
        # 유통기한 2일 이상이므로 입고분이 재고로 남아 Cap에 영향
        pending_qty = pending_by_mid.get(mid_cd, 0)
        if pending_qty > 0 and mid_cd in PENDING_CAP_DEDUCT_CATEGORIES:
            before_cap = adjusted_cap
            adjusted_cap = max(0, adjusted_cap - pending_qty)
            logger.info(
                f"[Cap입고차감] {mid_cd}: "
                f"auto상한{before_cap} - 입고예정{pending_qty} = {adjusted_cap}"
            )

        # cancel_smart 항목 분리: Cap 품목수에서 제외, 결과에 항상 포함
        # cancel_smart(qty=0)는 스마트 취소용이므로 Cap 슬롯을 차지하면 안 됨
        cancel_items = [i for i in items if i.get("cancel_smart")]
        non_cancel = [i for i in items if not i.get("cancel_smart")]

        # 현재 수량 합산이 adjusted_cap 이하면 그대로 유지
        # NOTE: 품목수(len) 대신 수량합(sum qty)으로 비교 — 행사 부스트 등으로 qty>1인 경우 대응
        current_qty_sum = sum(i.get("final_order_qty", 1) for i in non_cancel)
        current_count = len(non_cancel)
        if current_qty_sum <= adjusted_cap:
            result.extend(non_cancel + cancel_items)
            logger.debug(
                f"mid_cd={mid_cd}: {current_count}품목(qty합={current_qty_sum}) ≤ cap {adjusted_cap}, 그대로 유지"
                + (f" (cancel_smart={len(cancel_items)}개 별도)" if cancel_items else "")
            )
            continue

        # 해당 mid_cd의 Floor 보호 품목 필터링
        mid_floor_protected = None
        if floor_protected_codes:
            mid_item_codes = {i.get("item_cd") for i in non_cancel}
            mid_protected = floor_protected_codes & mid_item_codes
            if mid_protected:
                mid_floor_protected = mid_protected

        # adjusted_cap 초과 시 선별 (cancel_smart 제외한 정상 상품만 대상)
        selected = select_items_with_cap(
            non_cancel, adjusted_cap, db_path=db_path, store_id=store_id,
            floor_protected_codes=mid_floor_protected
        )

        # 선별 후에도 수량 합이 cap 초과 시 후순위부터 수량 절삭
        # Floor 보호 품목은 비보호 품목 모두 절삭 후에야 절삭됨
        selected = _trim_qty_to_cap(selected, adjusted_cap, floor_protected_codes=mid_floor_protected)

        result.extend(selected + cancel_items)
        cap_applied_count += 1

        selected_qty_sum = sum(i.get("final_order_qty", 1) for i in selected)
        logger.info(
            f"푸드 총량 상한 적용 mid_cd={mid_cd}: "
            f"{current_count}품목(qty합={current_qty_sum}) → {len(selected)}품목(qty합={selected_qty_sum}) "
            f"(요일평균 {weekday_avg:.1f} + 버퍼 {effective_buffer} = 예산{total_cap}"
            f"{f', site차감 {site_count}' if site_count > 0 else ''}"
            f", auto상한 {adjusted_cap})"
            + (f" + cancel_smart={len(cancel_items)}개 별도 통과" if cancel_items else "")
        )

    if cap_applied_count > 0:
        logger.info(f"푸드류 총량 상한: {cap_applied_count}개 카테고리에 적용됨")

    return result
