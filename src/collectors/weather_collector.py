"""
날씨 데이터 수집기
- BGF 리테일 사이트에서 날씨 정보 수집
- TopFrame 예보 그리드에서 내일/모레 예보 기온 추출
- STZZZ80_P0 팝업에서 미세먼지/초미세먼지 예보 추출
"""

import sys
import re
import time
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

            # 3. 팝업 닫기 (광고)
            self.analyzer.close_popup()

            # 3.5 날씨 팝업 오픈 (미세먼지 수집용)
            self._open_weather_popup()

            # 4. 날씨 정보 추출
            weather_data = self._extract_weather_info()

            return [weather_data] if weather_data else []

        finally:
            if self.analyzer:
                self.analyzer.close()

    def _open_weather_popup(self) -> bool:
        """날씨정보(주간) 팝업 오픈 (STZZZ80_P0)"""
        try:
            self.analyzer.driver.execute_script("""
                var topForm = nexacro.getApplication()
                    .mainframe.HFrameSet00.VFrameSet00.TopFrame.form;
                // pdiv_weather 영역 클릭 → STZZZ80_P0 팝업 생성
                if (topForm.pdiv_weather) {
                    topForm.pdiv_weather.set_visible(true);
                    // 실제 팝업 트리거는 img_weather 클릭 이벤트
                    topForm.img_weather.click();
                }
            """)
            time.sleep(2)  # 팝업 + 트랜잭션 응답 대기

            # 팝업 존재 확인
            exists = self.analyzer.driver.execute_script("""
                try {
                    var pf = nexacro.getApplication()
                        .mainframe.HFrameSet00.VFrameSet00.TopFrame
                        .STZZZ80_P0;
                    return pf && pf.form ? true : false;
                } catch(e) { return false; }
            """)
            if exists:
                logger.info("Weather popup (STZZZ80_P0) opened")
            else:
                logger.warning("Weather popup not found after click")
            return bool(exists)
        except Exception as e:
            logger.warning(f"Failed to open weather popup: {e}")
            return False

    def _extract_weather_info(self) -> Optional[Dict[str, Any]]:
        """TopFrame에서 날씨 정보 추출"""
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
                            result.forecast_precipitation = {};
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

                                    // 최고기온 (Decimal .hi 파싱)
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

                                    // 강수확률 (Decimal .hi 파싱)
                                    var rainRateRaw = dsTmr.getColumn(r, 'RAIN_RATE');
                                    var rainRate = null;
                                    if (rainRateRaw !== null && rainRateRaw !== undefined) {
                                        if (typeof rainRateRaw === 'object' && rainRateRaw.hi !== undefined) {
                                            rainRate = rainRateRaw.hi;
                                        } else {
                                            rainRate = parseFloat(rainRateRaw);
                                        }
                                    }

                                    // 강수량 (Decimal .hi 파싱)
                                    var rainQtyRaw = dsTmr.getColumn(r, 'RAIN_QTY');
                                    var rainQty = null;
                                    if (rainQtyRaw !== null && rainQtyRaw !== undefined) {
                                        if (typeof rainQtyRaw === 'object' && rainQtyRaw.hi !== undefined) {
                                            rainQty = rainQtyRaw.hi;
                                        } else {
                                            rainQty = parseFloat(rainQtyRaw);
                                        }
                                    }

                                    // 강수유형명 + 날씨설명 (문자열)
                                    var rainTyNm = dsTmr.getColumn(r, 'RAIN_TY_NM') || '';
                                    var weatherCdNm = dsTmr.getColumn(r, 'WEATHER_CD_NM') || '';
                                    var isSnow = (rainTyNm.indexOf('눈') >= 0) || (weatherCdNm.indexOf('눈') >= 0);

                                    result.forecast_precipitation[ymdStr] = {
                                        rain_rate: rainRate,
                                        rain_qty: rainQty,
                                        rain_type_nm: rainTyNm.trim(),
                                        weather_cd_nm: weatherCdNm.trim(),
                                        is_snow: isSnow
                                    };
                                }
                                result.forecast_source = 'ds_weatherTomorrow';
                                result.forecast_rows = dsTmr.getRowCount();
                            }

                            topForm.ds_weatherCond.setColumn(0, 'AFTER_DAY', '2');
                        }
                    } catch(eForecast) {
                        result.forecast_error = eForecast.message;
                    }

                    // ── B-2: 미세먼지 — dsList01Org ───────────────
                    try {
                        var popupForm = null;
                        try {
                            popupForm = topForm
                                ? topForm.parent.STZZZ80_P0.form : null;
                        } catch(e2) {}

                        if (popupForm && popupForm.dsList01Org) {
                            var ds = popupForm.dsList01Org;

                            function parseDustInfo(raw) {
                                if (!raw) return {dust: '', fine: ''};
                                var clean = String(raw).replace(/\r/g, '');
                                var parts = clean.split('\n');
                                var dust = parts[0].trim();
                                var fine = '';
                                if (parts.length > 1) {
                                    var m = parts[1].match(/\(([^)]+)\)/);
                                    fine = m ? m[1].trim() : '';
                                }
                                return {dust: dust, fine: fine};
                            }

                            var gradeScore = {
                                '좋음':1, '보통':2, '한때나쁨':3, '나쁨':4, '매우나쁨':5
                            };
                            function worseGrade(a, b) {
                                return (gradeScore[a] || 0) >= (gradeScore[b] || 0) ? a : b;
                            }

                            // 내일/모레만 (오늘 제외: di=1부터)
                            var today2 = new Date();
                            var dustByDate = {};
                            for (var di = 1; di <= 2; di++) {
                                var d = new Date(today2);
                                d.setDate(today2.getDate() + di);
                                var ymd2 = d.getFullYear() + '-'
                                    + String(d.getMonth()+1).padStart(2,'0') + '-'
                                    + String(d.getDate()).padStart(2,'0');

                                var amIdx = String(di * 2 + 1).padStart(2, '0');
                                var pmIdx = String(di * 2 + 2).padStart(2, '0');

                                var amRaw = ds.getColumn(0, 'DT_INFO_' + amIdx);
                                var pmRaw = ds.getColumn(0, 'DT_INFO_' + pmIdx);
                                var am = parseDustInfo(amRaw);
                                var pm = parseDustInfo(pmRaw);

                                dustByDate[ymd2] = {
                                    dust: worseGrade(am.dust, pm.dust),
                                    fine: worseGrade(am.fine, pm.fine)
                                };
                            }

                            // forecast_precipitation에 병합
                            if (!result.forecast_precipitation) result.forecast_precipitation = {};
                            for (var dk in dustByDate) {
                                if (!result.forecast_precipitation[dk]) {
                                    result.forecast_precipitation[dk] = {
                                        rain_rate: null, rain_qty: null,
                                        rain_type_nm: '', weather_cd_nm: '',
                                        is_snow: false
                                    };
                                }
                                result.forecast_precipitation[dk].dust_grade = dustByDate[dk].dust;
                                result.forecast_precipitation[dk].fine_dust_grade = dustByDate[dk].fine;
                            }
                            result.dust_source = 'dsList01Org';
                        } else {
                            result.dust_source = 'unavailable';
                        }
                    } catch(eDust) {
                        result.dust_parse_error = eDust.message;
                        result.dust_source = 'error';
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

                forecast_precip = weather_info.get("forecast_precipitation", {})
                if forecast_precip:
                    for fp_date, fp_data in forecast_precip.items():
                        logger.info(
                            f"Forecast precip: {fp_date} rate={fp_data.get('rain_rate')}% "
                            f"qty={fp_data.get('rain_qty')}mm "
                            f"snow={fp_data.get('is_snow')} "
                            f"weather={fp_data.get('weather_cd_nm')}"
                        )

                dust_source = weather_info.get("dust_source", "?")
                logger.info(f"Dust source: {dust_source}")
                if dust_source == 'dsList01Org':
                    for fp_date, fp_data in forecast_precip.items():
                        if fp_data.get('dust_grade') or fp_data.get('fine_dust_grade'):
                            logger.info(
                                f"Dust forecast: {fp_date} "
                                f"dust={fp_data.get('dust_grade')} "
                                f"fine={fp_data.get('fine_dust_grade')}"
                            )
                if weather_info.get('dust_parse_error'):
                    logger.warning(f"Dust parse error: {weather_info['dust_parse_error']}")

                if weather_info.get('forecast_error'):
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
