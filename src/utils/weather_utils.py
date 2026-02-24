"""
날씨 데이터 유틸리티

- 예보 원시 데이터(forecast_raw) → 날짜별 최고기온 변환
"""

from datetime import datetime
from typing import Any, Dict, List

from src.utils.logger import get_logger

logger = get_logger(__name__)


def parse_forecast_daily(weather_info: Dict[str, Any]) -> Dict[str, float]:
    """예보 원시 데이터에서 날짜별 최고기온 추출

    forecast_raw 행에서 날짜/시간/기온 컬럼을 찾아
    날짜별 최고기온(max)을 계산한다.

    Args:
        weather_info: collect_weather()의 반환값 (forecast_raw, forecast_columns 포함)

    Returns:
        {날짜(YYYY-MM-DD): 최고기온, ...} (오늘 제외, 내일+모레)
    """
    forecast_raw = weather_info.get("forecast_raw", [])
    if not forecast_raw:
        return {}

    columns = weather_info.get("forecast_columns", [])
    col_lower = [c.lower() for c in columns]

    # 기온 컬럼 탐색
    temp_col = None
    for candidate in ["temp", "temperature", "tmp", "ta", "degree"]:
        for i, c in enumerate(col_lower):
            if candidate in c:
                temp_col = columns[i]
                break
        if temp_col:
            break

    # 날짜 컬럼 탐색
    date_col = None
    for candidate in ["date", "fcst_date", "ymd", "dt"]:
        for i, c in enumerate(col_lower):
            if candidate in c:
                date_col = columns[i]
                break
        if date_col:
            break

    if not temp_col:
        logger.debug(f"Forecast: temp column not found in {columns}")
        return {}

    # 시간 컬럼 탐색 (날짜 컬럼 없을 때 폴백)
    time_col = None
    if not date_col:
        for cand in ["time", "hour", "fcst_time", "tm"]:
            for i, c in enumerate(col_lower):
                if cand in c:
                    time_col = columns[i]
                    break
            if time_col:
                break

    # 날짜별 기온 수집
    daily_temps: Dict[str, List[float]] = {}
    today_str = datetime.now().strftime("%Y-%m-%d")

    for row in forecast_raw:
        try:
            temp_val = row.get(temp_col)
            if temp_val is None or temp_val == '':
                continue
            temp_float = float(temp_val)

            # 날짜 결정
            date_str = None
            if date_col and row.get(date_col):
                raw_date = str(row[date_col])
                if len(raw_date) == 8 and raw_date.isdigit():
                    raw_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                date_str = raw_date[:10]
            elif time_col:
                raw_time = str(row.get(time_col, ''))
                if len(raw_time) >= 10:
                    date_str = f"{raw_time[:4]}-{raw_time[4:6]}-{raw_time[6:8]}"

            if not date_str:
                continue

            if date_str not in daily_temps:
                daily_temps[date_str] = []
            daily_temps[date_str].append(temp_float)
        except (ValueError, TypeError, IndexError):
            continue

    # 최고기온 계산, 오늘 제외 (내일 이후만)
    result: Dict[str, float] = {}
    for date_str, temps in sorted(daily_temps.items()):
        if date_str <= today_str:
            continue
        result[date_str] = max(temps)

    return result
