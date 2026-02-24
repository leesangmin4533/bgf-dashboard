"""
공휴일/요일 정보 수집기 (holidays 라이브러리 기반)

- 한국 공휴일 자동 계산 (음력 공휴일 + 대체공휴일 포함)
- 연속 연휴 기간 인식 (주말+공휴일 연결)
- 연휴 맥락 정보 (위치, 전날, 후 첫 영업일)
"""

from datetime import datetime, date, timedelta
from functools import lru_cache
from typing import Any, Dict, List, Optional

import holidays

from src.utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# 1. holidays 라이브러리 기반 공휴일 자동 생성
# =============================================================================

def get_korean_holidays(year: int) -> Dict[str, str]:
    """holidays 라이브러리로 해당 연도 한국 공휴일 생성

    음력 공휴일(설날, 추석, 부처님오신날)과 대체공휴일이 자동 계산된다.

    Args:
        year: 연도 (예: 2026)

    Returns:
        {"2026-02-17": "설날", "2026-02-16": "설날 연휴", ...}
    """
    kr = holidays.KR(years=year)
    return {d.strftime("%Y-%m-%d"): name for d, name in sorted(kr.items())}


# 하위 호환: 기존 코드에서 KOREAN_HOLIDAYS를 직접 참조하는 곳이 있음
KOREAN_HOLIDAYS = {**get_korean_holidays(2025), **get_korean_holidays(2026)}


# 요일 한글 매핑
DAY_OF_WEEK_KR = {
    0: "월",
    1: "화",
    2: "수",
    3: "목",
    4: "금",
    5: "토",
    6: "일",
}


# =============================================================================
# 2. 연속 연휴 기간 인식
# =============================================================================

@lru_cache(maxsize=8)
def get_holiday_periods(year: int) -> List[Dict[str, Any]]:
    """연속 연휴 기간 계산 (주말+공휴일 연결)

    주말(토,일)과 공휴일이 연속되면 하나의 연휴 기간으로 묶는다.
    예: 2026년 설날 → 2/15(일) + 2/16(월,연휴) + 2/17(화,설날) + 2/18(수,연휴) = 4일

    Args:
        year: 연도

    Returns:
        [
            {
                "start": "2026-02-15",
                "end": "2026-02-18",
                "days": 4,
                "name": "설날",
                "dates": ["2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18"],
            }, ...
        ]
    """
    kr = holidays.KR(years=year)
    holiday_dates = set(kr.keys())

    # 주말 + 공휴일 = "쉬는 날" 집합 (해당 연도 전체)
    off_days = set()
    start_of_year = date(year, 1, 1)
    end_of_year = date(year, 12, 31)
    current = start_of_year
    while current <= end_of_year:
        if current.weekday() >= 5 or current in holiday_dates:
            off_days.add(current)
        current += timedelta(days=1)

    # 연속 기간 탐지 (공휴일을 하나 이상 포함하는 연속 쉬는 날만)
    sorted_days = sorted(off_days)
    if not sorted_days:
        return []

    periods = []
    group = [sorted_days[0]]

    for i in range(1, len(sorted_days)):
        if sorted_days[i] - sorted_days[i - 1] == timedelta(days=1):
            group.append(sorted_days[i])
        else:
            _maybe_add_period(group, kr, periods)
            group = [sorted_days[i]]
    _maybe_add_period(group, kr, periods)

    return periods


def _maybe_add_period(
    group: List[date], kr: holidays.HolidayBase, periods: List[Dict]
) -> None:
    """연속 쉬는 날 그룹에서 공휴일 포함 + 2일 이상인 것만 연휴로 등록"""
    if len(group) < 2:
        return
    # 공휴일이 하나 이상 포함되어야 연휴
    holiday_in_group = [d for d in group if d in kr]
    if not holiday_in_group:
        return

    # 대표 이름: 그룹 내 공휴일 이름 중 "연휴"/"대체"가 아닌 것 우선
    names = [kr[d] for d in holiday_in_group]
    main_name = next(
        (n for n in names if "연휴" not in n and "대체" not in n),
        names[0],
    )

    periods.append({
        "start": group[0].strftime("%Y-%m-%d"),
        "end": group[-1].strftime("%Y-%m-%d"),
        "days": len(group),
        "name": main_name,
        "dates": [d.strftime("%Y-%m-%d") for d in group],
    })


# =============================================================================
# 3. 연휴 맥락 정보
# =============================================================================

def get_holiday_context(date_str: str) -> Dict[str, Any]:
    """특정 날짜의 연휴 맥락 정보 반환

    Args:
        date_str: YYYY-MM-DD 형식

    Returns:
        {
            "is_holiday": True/False,     # 공휴일 여부
            "holiday_name": "설날",       # 공휴일 이름 (비공휴일이면 "")
            "in_period": True/False,      # 연속 연휴 기간 내인지
            "period_days": 4,             # 연휴 총 길이 (비연휴 0)
            "position": 2,               # 연휴 내 위치 (1-based, 비연휴 0)
            "is_pre_holiday": True/False, # 연휴 전날인지
            "is_post_holiday": True/False,# 연휴 후 첫 영업일인지
        }
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d").date()
    year = dt.year

    holiday_flag = is_holiday(date_str)
    holiday_name = get_holiday_name(date_str)

    periods = get_holiday_periods(year)

    # 현재 날짜가 연휴 기간 내인지 확인
    in_period = False
    period_days = 0
    position = 0
    is_pre = False
    is_post = False

    for period in periods:
        if date_str in period["dates"]:
            in_period = True
            period_days = period["days"]
            position = period["dates"].index(date_str) + 1
            break

    if not in_period:
        # 연휴 전날 또는 후 첫 영업일 확인
        for period in periods:
            p_start = datetime.strptime(period["start"], "%Y-%m-%d").date()
            p_end = datetime.strptime(period["end"], "%Y-%m-%d").date()

            # 연휴 전날: 연휴 시작일 바로 전날
            if dt == p_start - timedelta(days=1):
                is_pre = True
                period_days = period["days"]
                break

            # 연휴 후 첫 영업일: 연휴 종료일 다음날부터 쉬는 날이 아닌 첫 날
            next_day = p_end + timedelta(days=1)
            # 연휴 끝난 후 주말이 이어질 수 있으므로 최대 3일 탐색
            for _ in range(3):
                if next_day == dt:
                    is_post = True
                    period_days = period["days"]
                    break
                if next_day.weekday() >= 5:  # 주말이면 건너뜀
                    next_day += timedelta(days=1)
                else:
                    break
            if is_post:
                break

    return {
        "is_holiday": holiday_flag,
        "holiday_name": holiday_name,
        "in_period": in_period,
        "period_days": period_days,
        "position": position,
        "is_pre_holiday": is_pre,
        "is_post_holiday": is_post,
    }


# =============================================================================
# 4. 기존 하위 호환 함수
# =============================================================================

def get_day_of_week(date_str: str) -> str:
    """날짜의 요일 반환

    Args:
        date_str: YYYY-MM-DD 형식

    Returns:
        요일 (월, 화, 수, 목, 금, 토, 일)
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return DAY_OF_WEEK_KR[dt.weekday()]


def is_holiday(date_str: str) -> bool:
    """공휴일 여부 확인 (holidays 라이브러리 기반)

    Args:
        date_str: YYYY-MM-DD 형식의 날짜 문자열

    Returns:
        공휴일이면 True, 아니면 False
    """
    try:
        year = int(date_str[:4])
        kr = holidays.KR(years=year)
        dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        return dt in kr
    except Exception:
        return date_str in KOREAN_HOLIDAYS


def get_holiday_name(date_str: str) -> str:
    """공휴일 이름 반환

    Args:
        date_str: YYYY-MM-DD 형식의 날짜 문자열

    Returns:
        공휴일 이름 (공휴일이 아니면 빈 문자열)
    """
    try:
        year = int(date_str[:4])
        kr = holidays.KR(years=year)
        dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        return kr.get(dt, "")
    except Exception:
        return KOREAN_HOLIDAYS.get(date_str, "")


def is_weekend(date_str: str) -> bool:
    """주말 여부 확인

    Args:
        date_str: YYYY-MM-DD 형식의 날짜 문자열

    Returns:
        토요일 또는 일요일이면 True, 아니면 False
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.weekday() >= 5  # 토(5), 일(6)


def get_calendar_info(date_str: str) -> Dict[str, Any]:
    """날짜의 캘린더 정보 반환 (연휴 맥락 포함)

    Args:
        date_str: YYYY-MM-DD 형식

    Returns:
        {
            "date": "2026-02-17",
            "day_of_week": "화",
            "day_of_week_num": 1,
            "is_weekend": False,
            "is_holiday": True,
            "holiday_name": "설날",
            "is_workday": False,
            # 연휴 맥락
            "in_period": True,
            "period_days": 4,
            "position": 3,
            "is_pre_holiday": False,
            "is_post_holiday": False,
        }
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    dow = dt.weekday()
    weekend = dow >= 5
    holiday = is_holiday(date_str)

    ctx = get_holiday_context(date_str)

    return {
        "date": date_str,
        "day_of_week": DAY_OF_WEEK_KR[dow],
        "day_of_week_num": dow,
        "is_weekend": weekend,
        "is_holiday": holiday,
        "holiday_name": ctx["holiday_name"],
        "is_workday": not (weekend or holiday),
        # 연휴 맥락
        "in_period": ctx["in_period"],
        "period_days": ctx["period_days"],
        "position": ctx["position"],
        "is_pre_holiday": ctx["is_pre_holiday"],
        "is_post_holiday": ctx["is_post_holiday"],
    }


# =============================================================================
# 5. DB 저장
# =============================================================================

def save_calendar_info(date_str: str, repo: Any) -> None:
    """캘린더 정보를 DB에 저장 (연휴 맥락 포함)

    Args:
        date_str: YYYY-MM-DD 형식
        repo: ExternalFactorRepository 인스턴스
    """
    info = get_calendar_info(date_str)

    # 요일 저장
    repo.save_factor(
        factor_date=date_str,
        factor_type="calendar",
        factor_key="day_of_week",
        factor_value=info["day_of_week"],
    )

    repo.save_factor(
        factor_date=date_str,
        factor_type="calendar",
        factor_key="day_of_week_num",
        factor_value=str(info["day_of_week_num"]),
    )

    # 주말 여부
    repo.save_factor(
        factor_date=date_str,
        factor_type="calendar",
        factor_key="is_weekend",
        factor_value="true" if info["is_weekend"] else "false",
    )

    # 공휴일 여부
    repo.save_factor(
        factor_date=date_str,
        factor_type="calendar",
        factor_key="is_holiday",
        factor_value="true" if info["is_holiday"] else "false",
    )

    if info["holiday_name"]:
        repo.save_factor(
            factor_date=date_str,
            factor_type="calendar",
            factor_key="holiday_name",
            factor_value=info["holiday_name"],
        )

    # 영업일 여부
    repo.save_factor(
        factor_date=date_str,
        factor_type="calendar",
        factor_key="is_workday",
        factor_value="true" if info["is_workday"] else "false",
    )

    # 연휴 맥락 저장
    repo.save_factor(
        factor_date=date_str,
        factor_type="calendar",
        factor_key="holiday_period_days",
        factor_value=str(info["period_days"]),
    )

    repo.save_factor(
        factor_date=date_str,
        factor_type="calendar",
        factor_key="holiday_position",
        factor_value=str(info["position"]),
    )

    repo.save_factor(
        factor_date=date_str,
        factor_type="calendar",
        factor_key="is_pre_holiday",
        factor_value="true" if info["is_pre_holiday"] else "false",
    )

    repo.save_factor(
        factor_date=date_str,
        factor_type="calendar",
        factor_key="is_post_holiday",
        factor_value="true" if info["is_post_holiday"] else "false",
    )

    status = "공휴일: " + info["holiday_name"] if info["is_holiday"] else (
        "평일" if info["is_workday"] else "주말"
    )
    period_info = ""
    if info["in_period"]:
        period_info = f" [연휴 {info['position']}/{info['period_days']}일]"
    elif info["is_pre_holiday"]:
        period_info = f" [연휴 전날, {info['period_days']}일 연휴]"
    elif info["is_post_holiday"]:
        period_info = f" [연휴 후 첫 영업일]"

    logger.info(
        f"{date_str} ({info['day_of_week']}) - {status}{period_info}"
    )


def backfill_holiday_data(start_date: str, end_date: str) -> int:
    """지정 기간의 공휴일/연휴 정보를 DB에 일괄 저장/갱신

    Args:
        start_date: 시작일 (YYYY-MM-DD)
        end_date: 종료일 (YYYY-MM-DD)

    Returns:
        처리된 일수
    """
    from src.infrastructure.database.repos import ExternalFactorRepository

    repo = ExternalFactorRepository()
    current = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    count = 0

    while current <= end:
        save_calendar_info(current.strftime("%Y-%m-%d"), repo)
        current += timedelta(days=1)
        count += 1

    logger.info(f"공휴일 데이터 백필 완료: {start_date} ~ {end_date} ({count}일)")
    return count


if __name__ == "__main__":
    # 2026년 공휴일 확인
    print("=== 2026년 한국 공휴일 ===")
    for d, name in sorted(get_korean_holidays(2026).items()):
        print(f"  {d}: {name}")

    print("\n=== 2026년 연속 연휴 ===")
    for period in get_holiday_periods(2026):
        print(f"  {period['name']}: {period['start']} ~ {period['end']} ({period['days']}일)")

    print("\n=== 설날 연휴 맥락 ===")
    test_dates = [
        "2026-02-14",  # 토 (설 전전날)
        "2026-02-15",  # 일 (연휴 시작)
        "2026-02-16",  # 월 (설날 연휴)
        "2026-02-17",  # 화 (설날)
        "2026-02-18",  # 수 (설날 연휴)
        "2026-02-19",  # 목 (연휴 후 첫 영업일)
    ]
    for d in test_dates:
        ctx = get_holiday_context(d)
        print(f"  {d}: {ctx}")
