"""
날씨 데이터 수집기
- BGF 리테일 사이트에서 날씨 정보 수집
- TopFrame 예보 그리드에서 내일/모레 예보 기온 추출
"""

import sys
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from .base import BaseCollector
from sales_analyzer import SalesAnalyzer
from src.utils.logger import get_logger

logger = get_logger(__name__)


class WeatherCollector(BaseCollector):
    """BGF 사이트 날씨 데이터 수집기"""

    def __init__(self) -> None:
        super().__init__(name="WeatherCollector")
        self.analyzer: Optional[SalesAnalyzer] = None

    def collect(self, target_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        날씨 데이터 수집

        Args:
            target_date: 수집 대상 날짜 (사용하지 않음 - 현재 날씨만 수집)

        Returns:
            수집된 날씨 데이터 리스트
        """
        self.analyzer = SalesAnalyzer()

        try:
            # 1. 드라이버 설정 및 접속
            self.analyzer.setup_driver()
            self.analyzer.connect()

            # 2. 로그인
            if not self.analyzer.do_login():
                raise Exception("Login failed")

            # 3. 팝업 닫기
            self.analyzer.close_popup()

            # 4. 날씨 정보 추출
            weather_data = self._extract_weather_info()

            return [weather_data] if weather_data else []

        finally:
            if self.analyzer:
                self.analyzer.close()

    def _extract_weather_info(self) -> Optional[Dict[str, Any]]:
        """TopFrame에서 날씨 정보 추출"""
        import time
        time.sleep(1)  # 페이지 로딩 대기

        try:
            weather_info = self.analyzer.driver.execute_script("""
                try {
                    var result = {};

                    // 1. 현재 온도 (sta_degree)
                    var degreeEl = document.querySelector("[id*='sta_degree']");
                    if (degreeEl) {
                        var tempText = degreeEl.innerText || '';
                        // "6도" 또는 "-6도" 형식에서 숫자 추출
                        var match = tempText.match(/(-?\\d+)/);
                        result.temperature = match ? parseInt(match[1]) : null;
                        result.temperature_raw = tempText.trim();
                    }

                    // 2. 날짜/시간 (sta_date)
                    var dateEl = document.querySelector("[id*='sta_date']");
                    if (dateEl) {
                        result.datetime_raw = dateEl.innerText.trim();
                        // "2026-01-25(일) 20:14:31" 형식 파싱
                        var dateMatch = result.datetime_raw.match(/(\\d{4}-\\d{2}-\\d{2})/);
                        result.date = dateMatch ? dateMatch[1] : null;
                    }

                    // 3. 날씨 아이콘 (img_weather) - 이미지 src에서 날씨 유형 추출
                    var weatherImg = document.querySelector("[id*='img_weather'] img, [id*='img_weather'][style*='background']");
                    if (weatherImg) {
                        var src = weatherImg.src || weatherImg.style.backgroundImage || '';
                        result.weather_icon = src;

                        // 아이콘 파일명에서 날씨 유형 추출
                        if (src.includes('sunny') || src.includes('clear')) {
                            result.weather_type = 'sunny';
                        } else if (src.includes('cloud')) {
                            result.weather_type = 'cloudy';
                        } else if (src.includes('rain')) {
                            result.weather_type = 'rainy';
                        } else if (src.includes('snow')) {
                            result.weather_type = 'snowy';
                        } else {
                            result.weather_type = 'unknown';
                        }
                    }

                    // 4. 예보 데이터 수집 (ds_weatherTomorrow 트랜잭션)
                    result.forecast_daily = {};
                    try {
                        var topForm = null;
                        try {
                            var app = nexacro.getApplication();
                            topForm = app.mainframe.HFrameSet00.VFrameSet00.TopFrame.form;
                        } catch(e1) {
                            try {
                                topForm = mainframe.HFrameSet00.VFrameSet00.TopFrame.form;
                            } catch(e1b) {}
                        }

                        if (topForm && topForm.ds_weatherCond) {
                            topForm.ds_weatherCond.setColumn(0, 'AFTER_DAY', '2');
                            topForm.gfn_transaction(
                                'selTopWeatherForecast',
                                'weather/selTopWeather',
                                'ds_weatherCond=ds_weatherCond',
                                'ds_weatherTomorrow=ds_weatherTomorrow',
                                '', 'fn_callback', false
                            );

                            var dsTmr = topForm.ds_weatherTomorrow;
                            if (dsTmr && dsTmr.getRowCount && dsTmr.getRowCount() > 0) {
                                var today = new Date().toISOString().substring(0, 10);
                                for (var r = 0; r < dsTmr.getRowCount(); r++) {
                                    var ymd = dsTmr.getColumn(r, 'WEATHER_YMD');
                                    if (!ymd) continue;
                                    var ymdStr = String(ymd);
                                    if (ymdStr.length === 8) {
                                        ymdStr = ymdStr.substring(0,4) + '-' + ymdStr.substring(4,6) + '-' + ymdStr.substring(6,8);
                                    }
                                    if (ymdStr <= today) continue;

                                    var highest = dsTmr.getColumn(r, 'HIGHEST_TMPT');
                                    var temp = null;
                                    if (highest !== null && highest !== undefined) {
                                        if (typeof highest === 'object' && highest.hi !== undefined) {
                                            temp = highest.hi;
                                        } else {
                                            temp = parseFloat(highest);
                                        }
                                    }
                                    if (temp !== null && !isNaN(temp)) {
                                        result.forecast_daily[ymdStr] = temp;
                                    }
                                }
                                result.forecast_source = 'ds_weatherTomorrow';
                                result.forecast_rows = dsTmr.getRowCount();
                            }

                            topForm.ds_weatherCond.setColumn(0, 'AFTER_DAY', '2');
                        }
                    } catch(eForecast) {
                        result.forecast_error = eForecast.message;
                    }

                    // 5. 매장 정보
                    var storeEl = document.querySelector("[id*='sta_storeNm']");
                    if (storeEl) {
                        result.store_name = storeEl.innerText.trim();
                    }

                    return result;
                } catch (e) {
                    return {error: e.message};
                }
            """)

            if weather_info and not weather_info.get('error'):
                logger.info(f"Temperature: {weather_info.get('temperature')}°C")
                logger.info(f"Date: {weather_info.get('date')}")
                logger.info(f"Store: {weather_info.get('store_name')}")

                forecast_daily = weather_info.get("forecast_daily", {})
                if forecast_daily:
                    logger.info(f"Forecast daily max temps: {forecast_daily}")
                elif weather_info.get('forecast_error'):
                    logger.debug(f"Forecast error: {weather_info['forecast_error']}")

                return weather_info
            else:
                logger.error(f"Error: {weather_info.get('error')}")
                return None

        except Exception as e:
            logger.error(f"Extract error: {e}")
            return None

    def validate(self, data: List[Dict[str, Any]]) -> bool:
        """수집 데이터 유효성 검증

        Args:
            data: 수집된 날씨 데이터 리스트

        Returns:
            온도와 날짜가 포함되어 있으면 True
        """
        if not data:
            return False

        item = data[0]
        # 최소한 온도와 날짜가 있어야 함
        if item.get('temperature') is None or not item.get('date'):
            logger.warning("Missing temperature or date")
            return False

        return True


def collect_weather() -> Dict[str, Any]:
    """날씨 수집 헬퍼 함수

    Returns:
        수집 결과 딕셔너리 (success, data, collected_at 등 포함)
    """
    collector = WeatherCollector()
    return collector.run()


if __name__ == "__main__":
    result = collect_weather()
    print(f"\nResult: {result}")
