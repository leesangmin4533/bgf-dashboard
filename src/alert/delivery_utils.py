"""
배송 및 유통기한 계산 유틸리티
- 배송 차수 판별 (1차/2차)
- 차수별 폐기 시간 계산
- 남은 유통시간 계산

배송 체계:
  1차: 당일 20:00 도착 (저녁 배송)
  2차: 익일 07:00 도착 (아침 배송)

폐기 계산 공식:
  001/002/003 (도시락/주먹밥/김밥): arrival_time + shelf_life_hours (시간 단위)
    1차: 20:00 도착 + 30시간 = D+3 02:00 폐기
    2차: 07:00 도착 + 31시간 = D+2 14:00 폐기
  기타 (004/005 등): arrival_date(자정) + shelf_life_days 날짜의 expiry_hour
"""

from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import sys
from pathlib import Path

# 경로 설정
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.alert.config import ALERT_CATEGORIES, DELIVERY_CONFIG


def get_delivery_type(item_nm: str) -> Optional[str]:
    """
    상품명에서 배송 차수 판별

    Args:
        item_nm: 상품명 (예: "도)압도적두툼돈까스정식1")

    Returns:
        "1차", "2차", or None
    """
    if not item_nm:
        return None

    # 상품명 끝자리 확인
    last_char = item_nm.strip()[-1]

    if last_char == "1":
        return "1차"
    elif last_char == "2":
        return "2차"

    return None


def get_expiry_hour_for_delivery(delivery_type: str) -> int:
    """
    차수별 폐기 시간 조회

    Args:
        delivery_type: "1차" or "2차"

    Returns:
        폐기 시간 (예: 22, 2)
    """
    config = DELIVERY_CONFIG.get(delivery_type, DELIVERY_CONFIG["1차"])
    return config.get("expiry_hour", 22)


def get_all_expiry_hours() -> List[int]:
    """
    전체 폐기 시간 목록 (스케줄러용)

    DELIVERY_CONFIG의 기본 폐기시간 + shelf_life_hours 카테고리의 추가 폐기시간 수집.

    Returns:
        정렬된 폐기 시간 리스트 (예: [2, 14, 22])
    """
    hours = set(cfg.get("expiry_hour", 22) for cfg in DELIVERY_CONFIG.values())

    # shelf_life_hours가 있는 카테고리의 폐기시간 추가
    for cat in ALERT_CATEGORIES.values():
        shelf_hours_map = cat.get("shelf_life_hours")
        if shelf_hours_map:
            for delivery_type, shelf_hours in shelf_hours_map.items():
                arrival_hour = DELIVERY_CONFIG.get(delivery_type, {}).get("arrival_hour", 0)
                expiry_hour = (arrival_hour + shelf_hours) % 24
                hours.add(expiry_hour)

        # use_product_expiry 카테고리 (012 빵): 자정(0시) 만료
        if cat.get("use_product_expiry"):
            hours.add(0)

    return sorted(hours)


def get_expiry_hours(mid_cd: str) -> List[int]:
    """
    카테고리별 폐기 시간 조회 (하위호환)

    차수별 폐기 시간 체계로 변경됨.
    모든 푸드 카테고리는 동일한 차수별 폐기 시간 적용.

    Args:
        mid_cd: 중분류 코드

    Returns:
        폐기 시간 리스트 (예: [2, 22])
    """
    return get_all_expiry_hours()


def get_arrival_time(delivery_type: str, order_date: Optional[datetime] = None) -> datetime:
    """
    배송 도착 시간 계산

    Args:
        delivery_type: "1차" or "2차"
        order_date: 발주일 (기본값: 오늘)

    Returns:
        도착 예정 시간
    """
    if order_date is None:
        order_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    config = DELIVERY_CONFIG.get(delivery_type, DELIVERY_CONFIG["1차"])

    if config["arrival_next_day"]:
        arrival_date = order_date + timedelta(days=1)
    else:
        arrival_date = order_date

    return arrival_date.replace(hour=config["arrival_hour"], minute=0)


def get_expiry_time_for_delivery(
    delivery_type: str,
    mid_cd: str,
    arrival_time: datetime,
    expiration_days: int = None
) -> datetime:
    """
    배송 차수별 정확한 폐기 시간 계산

    001/002/003 (도시락/주먹밥/김밥):
      arrival_time + shelf_life_hours 시간 단위 계산
      1차: 토요일 20:00 도착 + 30시간 = 월요일 02:00 폐기
      2차: 토요일 07:00 도착 + 31시간 = 일요일 14:00 폐기

    기타 (004/005 등):
      arrival_date(자정) + shelf_life_days 날짜의 expiry_hour
      1차: 토요일 20:00 도착 → 일요일 22:00 폐기
      2차: 토요일 07:00 도착 → 일요일 02:00 폐기

    Args:
        delivery_type: "1차" or "2차"
        mid_cd: 중분류 코드
        arrival_time: 도착 시간
        expiration_days: 상품별 유통일수 (012 빵 등, use_product_expiry 카테고리용)

    Returns:
        폐기 예정 시간
    """
    category = ALERT_CATEGORIES.get(mid_cd, {})

    # shelf_life_hours가 있으면 시간 단위 계산 (001/002/003)
    shelf_hours_map = category.get("shelf_life_hours")
    if shelf_hours_map and delivery_type in shelf_hours_map:
        shelf_hours = shelf_hours_map[delivery_type]
        return arrival_time + timedelta(hours=shelf_hours)

    # use_product_expiry (012 빵): B방식 날짜 단위
    # 공식: arrival_date + expiration_days + 1 의 00:00 (자정 만료)
    if category.get("use_product_expiry") and expiration_days:
        arrival_date = arrival_time.replace(hour=0, minute=0, second=0, microsecond=0)
        expiry_date = arrival_date + timedelta(days=expiration_days + 1)
        return expiry_date  # 00:00 (자정 만료)

    # 기존 로직: 일 단위 + 고정 expiry_hour (004/005 등)
    expiry_hour = get_expiry_hour_for_delivery(delivery_type)
    shelf_life_days = category.get("shelf_life_default", 1)

    arrival_date = arrival_time.replace(hour=0, minute=0, second=0, microsecond=0)
    expiry_date = arrival_date + timedelta(days=shelf_life_days)

    return expiry_date.replace(hour=expiry_hour, minute=0, second=0, microsecond=0)


def _get_expiry_hour_for_category(mid_cd: str, delivery_type: str) -> int:
    """
    카테고리 + 차수별 정확한 폐기시간(hour) 반환

    001/002/003: shelf_life_hours 기반으로 도착시간 + N시간의 hour 부분
      1차: 20 + 30 = 02시, 2차: 7 + 31 = 14시
    기타: DELIVERY_CONFIG의 expiry_hour
    """
    category = ALERT_CATEGORIES.get(mid_cd, {})
    shelf_hours_map = category.get("shelf_life_hours")

    if shelf_hours_map and delivery_type in shelf_hours_map:
        arrival_hour = DELIVERY_CONFIG.get(delivery_type, {}).get("arrival_hour", 0)
        shelf_hours = shelf_hours_map[delivery_type]
        return (arrival_hour + shelf_hours) % 24

    return get_expiry_hour_for_delivery(delivery_type)


def get_next_expiry_time(
    mid_cd: str,
    from_time: Optional[datetime] = None,
    delivery_type: Optional[str] = None
) -> datetime:
    """
    다음 폐기 시간 계산

    delivery_type이 지정되면 해당 차수의 폐기 시간만 고려.
    지정되지 않으면 모든 차수의 폐기 시간 중 가장 가까운 것.

    mid_cd에 따라 카테고리별 정확한 폐기시간 사용:
      001/002/003 1차 → 02:00, 2차 → 14:00
      004/005 등 1차 → 22:00, 2차 → 02:00

    Args:
        mid_cd: 중분류 코드
        from_time: 기준 시간 (기본값: 현재)
        delivery_type: 배송 차수 (None이면 전체)

    Returns:
        다음 폐기 시간
    """
    if from_time is None:
        from_time = datetime.now()

    if delivery_type:
        expiry_hours = [_get_expiry_hour_for_category(mid_cd, delivery_type)]
    else:
        # 해당 카테고리의 모든 차수 폐기시간
        expiry_hours = sorted(set(
            _get_expiry_hour_for_category(mid_cd, dt)
            for dt in DELIVERY_CONFIG.keys()
        ))

    current_hour = from_time.hour

    # 오늘 남은 폐기 시간 찾기
    for hour in sorted(expiry_hours):
        if hour > current_hour:
            return from_time.replace(hour=hour, minute=0, second=0, microsecond=0)

    # 오늘 폐기 시간 지남 → 내일 첫 폐기 시간
    next_day = from_time + timedelta(days=1)
    first_hour = min(expiry_hours)
    return next_day.replace(hour=first_hour, minute=0, second=0, microsecond=0)


def calculate_remaining_hours(
    item_nm: str,
    mid_cd: str,
    current_time: Optional[datetime] = None
) -> Tuple[float, datetime, str]:
    """
    상품의 남은 유통시간 계산

    차수별 폐기 시간 적용:
      1차(상품명 끝 1) → 22:00 폐기
      2차(상품명 끝 2) → 02:00 폐기

    Args:
        item_nm: 상품명
        mid_cd: 중분류 코드
        current_time: 현재 시간 (기본값: now)

    Returns:
        (남은 시간, 폐기 예정 시간, 배송 차수)
    """
    if current_time is None:
        current_time = datetime.now()

    delivery_type = get_delivery_type(item_nm) or "1차"
    expiry_time = get_next_expiry_time(mid_cd, current_time, delivery_type=delivery_type)

    remaining = expiry_time - current_time
    remaining_hours = remaining.total_seconds() / 3600

    return (round(remaining_hours, 1), expiry_time, delivery_type)


def calculate_shelf_life_after_arrival(
    item_nm: str,
    mid_cd: str,
    order_date: Optional[datetime] = None,
    expiration_days: Optional[int] = None
) -> Tuple[float, datetime, datetime]:
    """
    발주 후 도착~폐기까지 유통시간 계산

    예시 (금요일 발주):
      도시락(001) 1차: 토요일 20:00 도착 + 30시간 = 월요일 02:00 폐기 (30시간)
      도시락(001) 2차: 토요일 07:00 도착 + 31시간 = 일요일 14:00 폐기 (31시간)
      샌드위치(004) 1차: 토요일 20:00 도착 → 일요일 22:00 폐기 (26시간)
      샌드위치(004) 2차: 토요일 07:00 도착 → 일요일 02:00 폐기 (19시간)

    Args:
        item_nm: 상품명
        mid_cd: 중분류 코드
        order_date: 발주일 (기본값: 오늘)
        expiration_days: 상품별 유통기한 일수 (use_product_expiry 카테고리용)

    Returns:
        (유통시간(시간), 도착시간, 폐기시간)
    """
    if order_date is None:
        order_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # 배송 차수 및 도착 시간
    delivery_type = get_delivery_type(item_nm) or "1차"
    arrival_time = get_arrival_time(delivery_type, order_date)

    # 차수별 정확한 폐기 시간 계산
    expiry_time = get_expiry_time_for_delivery(
        delivery_type, mid_cd, arrival_time, expiration_days=expiration_days
    )

    shelf_life = expiry_time - arrival_time
    shelf_life_hours = shelf_life.total_seconds() / 3600

    return (round(shelf_life_hours, 1), arrival_time, expiry_time)


def format_time_remaining(hours: float) -> str:
    """
    남은 시간을 읽기 쉬운 형태로 변환

    Args:
        hours: 남은 시간 (시간 단위)

    Returns:
        "3시간", "1일 5시간" 등
    """
    if hours < 0:
        return "폐기됨"
    elif hours < 1:
        minutes = int(hours * 60)
        return f"{minutes}분"
    elif hours < 24:
        return f"{int(hours)}시간"
    else:
        days = int(hours // 24)
        remaining_hours = int(hours % 24)
        if remaining_hours > 0:
            return f"{days}일 {remaining_hours}시간"
        return f"{days}일"


# 테스트
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    test_items = [
        ("도)압도적두툼돈까스정식1", "001"),
        ("도)압도적두툼돈까스정식2", "001"),
        ("주)참기름닭갈비삼각1", "002"),
        ("주)참기름닭갈비삼각2", "002"),
        ("김)야채김밥1", "003"),
        ("김)야채김밥2", "003"),
        ("샌)햄치즈샌드위치1", "004"),
        ("샌)햄치즈샌드위치2", "004"),
    ]

    print("=== 배송/유통기한 테스트 ===")
    print(f"현재 시간: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()

    for item_nm, mid_cd in test_items:
        delivery = get_delivery_type(item_nm)
        remaining, expiry, _ = calculate_remaining_hours(item_nm, mid_cd)
        shelf_hours, arrival, expiry2 = calculate_shelf_life_after_arrival(item_nm, mid_cd)

        print(f"{item_nm}")
        print(f"  배송: {delivery}")
        print(f"  현재→폐기: {format_time_remaining(remaining)} ({expiry.strftime('%m/%d %H:%M')})")
        print(f"  도착→폐기: {format_time_remaining(shelf_hours)} "
              f"(도착:{arrival.strftime('%m/%d %H:%M')} → 폐기:{expiry2.strftime('%m/%d %H:%M')})")
        print()
