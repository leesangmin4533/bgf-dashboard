"""
BGF 리테일 매출 분석 데이터 수집 모듈
- 로그인
- 매출분석 > 중분류별 매출 구성비 데이터 수집
"""

import os
import sys
import time
import json
import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from selenium import webdriver

# .env 파일 로드
load_dotenv(Path(__file__).parent.parent / ".env")
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from webdriver_manager.chrome import ChromeDriverManager

# 타임아웃 핸들러 import
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.timeout_handler import OperationTimer, wait_with_timeout, log_timeout_error, DEFAULT_TIMEOUT
from src.utils.logger import get_logger
from src.settings.constants import DEFAULT_STORE_ID

logger = get_logger(__name__)


class SalesAnalyzer:
    """BGF 리테일 매출 분석 데이터 수집 클래스"""

    BASE_URL = "https://store.bgfretail.com/websrc/deploy/index.html"

    def __init__(self, store_id: str = DEFAULT_STORE_ID) -> None:
        """매출 분석기 초기화 (크롬 드라이버, 데이터 저장소 준비)

        Args:
            store_id: 점포 코드
        """
        self.store_id = store_id
        self.USER_ID, self.PASSWORD = self._load_credentials(store_id)
        self.driver: Optional[webdriver.Chrome] = None
        self.sales_data: List[Any] = []
        self.mid_categories: List[Dict[str, Any]] = []  # 중분류 목록
        self.weather_data: Optional[Dict[str, Any]] = None  # 날씨 정보

    def _load_credentials(self, store_id: str) -> tuple:
        """점포별 BGF 로그인 정보 로드

        환경변수에서 점포별 인증 정보를 로드합니다.
        - BGF_USER_ID_{store_id}
        - BGF_PASSWORD_{store_id}

        Args:
            store_id: 점포 코드

        Returns:
            (user_id, password) 튜플

        Raises:
            ValueError: 환경변수가 설정되지 않은 경우
        """
        try:
            from src.config.store_config import StoreConfigLoader
            loader = StoreConfigLoader()
            config = loader.get_store_config(store_id)
            logger.info(f"[{store_id}] 점포 설정 로드: {config.store_name}")
            return config.bgf_user_id, config.bgf_password
        except ValueError as e:
            # StoreConfigLoader가 실패한 경우, 레거시 환경변수 시도
            logger.debug(f"[{store_id}] 점포별 환경변수 로드 실패: {e}")

            # 레거시 환경변수 (BGF_USER_ID, BGF_PASSWORD) 시도
            user_id = os.environ.get("BGF_USER_ID") or ""
            password = os.environ.get("BGF_PASSWORD") or ""

            if not user_id or not password:
                raise ValueError(
                    f"점포 {store_id}의 BGF 로그인 정보가 없습니다.\n"
                    ".env 파일에 다음을 추가하세요:\n"
                    f"BGF_USER_ID_{store_id}=your_user_id\n"
                    f"BGF_PASSWORD_{store_id}=your_password"
                )

            logger.warning(f"[{store_id}] 레거시 환경변수 사용 (BGF_USER_ID/BGF_PASSWORD)")
            return user_id, password

    def setup_driver(self) -> None:
        """크롬 드라이버 설정 (자동화 감지 우회 포함)"""
        chrome_options = Options()

        # 창 크기 설정
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")

        # 자동화 감지 우회
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

        # 드라이버 생성
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=chrome_options)
        self.driver.implicitly_wait(10)
        self.driver.set_page_load_timeout(60)

        # 자동화 감지 우회 스크립트
        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        })

        logger.info("크롬 드라이버 설정 완료")

    def connect(self) -> None:
        """BGF 리테일 사이트 접속 및 넥사크로 로딩 대기"""
        logger.info(f"접속: {self.BASE_URL}")
        self.driver.get(self.BASE_URL)

        # 넥사크로 로딩 대기 (로그인 폼이 준비될 때까지)
        for i in range(20):
            try:
                # 로그인 폼이 완전히 로딩되었는지 확인
                result = self.driver.execute_script("""
                    try {
                        var app = nexacro.getApplication();
                        if (!app) return false;
                        var form = app.mainframe.HFrameSet00.LoginFrame.form.div_login.form;
                        return form && form.edt_id && form.edt_pw && form.btn_login;
                    } catch (e) {
                        return false;
                    }
                """)
                if result:
                    logger.info("로그인 폼 로딩 완료")
                    break
            except Exception as e:
                logger.debug(f"로그인 폼 로딩 대기 중: {e}")
            time.sleep(0.3)
        else:
            logger.warning("로그인 폼 로딩 타임아웃 - 계속 진행")

        self.get_screenshot("01_main.png")

    def find_and_debug_elements(self) -> None:
        """페이지 내 input, button, 클릭 가능 요소를 탐색하여 디버그 로그에 출력"""
        logger.debug("페이지 요소 분석 중...")

        # 모든 input 요소
        inputs = self.driver.find_elements(By.TAG_NAME, "input")
        logger.debug(f"입력 필드 ({len(inputs)}개):")
        for inp in inputs:
            inp_id = inp.get_attribute("id")
            inp_type = inp.get_attribute("type")
            inp_name = inp.get_attribute("name")
            if inp_id:
                logger.debug(f"  - ID: {inp_id}, Type: {inp_type}, Name: {inp_name}")

        # 모든 button 요소
        buttons = self.driver.find_elements(By.TAG_NAME, "button")
        logger.debug(f"버튼 ({len(buttons)}개):")
        for btn in buttons:
            btn_id = btn.get_attribute("id")
            btn_text = btn.text[:30] if btn.text else ""
            if btn_id or btn_text:
                logger.debug(f"  - ID: {btn_id}, Text: {btn_text}")

        # div 중 클릭 가능한 요소
        clickables = self.driver.find_elements(By.CSS_SELECTOR, "[onclick], [role='button']")
        logger.debug(f"클릭 가능 요소 ({len(clickables)}개):")
        for elem in clickables[:10]:
            logger.debug(f"  - ID: {elem.get_attribute('id')}, Class: {elem.get_attribute('class')[:50]}")

    def do_login(self) -> bool:
        """로그인 수행 (넥사크로 객체 직접 접근 방식)

        Returns:
            로그인 성공 여부
        """
        # 자격 증명 검증
        if not self.USER_ID or not self.PASSWORD:
            logger.error("BGF_USER_ID/BGF_PASSWORD 환경변수가 설정되지 않았습니다.")
            return False

        logger.info(f"로그인 시도: {self.USER_ID}")

        with OperationTimer("로그인", timeout=DEFAULT_TIMEOUT) as timer:
            try:
                # 넥사크로 객체에 직접 접근하여 값 설정 (최대 3회 재시도)
                for attempt in range(3):
                    login_script = f"""
                        try {{
                            var app = nexacro.getApplication();
                            var form = app.mainframe.HFrameSet00.LoginFrame.form.div_login.form;

                            // set_value()와 text 속성 둘 다 설정
                            form.edt_id.set_value("{self.USER_ID}");
                            form.edt_id.text = "{self.USER_ID}";

                            form.edt_pw.set_value("{self.PASSWORD}");
                            form.edt_pw.text = "{self.PASSWORD}";

                            return 'nexacro_set';
                        }} catch (e) {{
                            return 'nexacro_error: ' + e.message;
                        }}
                    """
                    result = self.driver.execute_script(login_script)
                    logger.info(f"넥사크로 값 설정: {result}")

                    if 'nexacro_set' in result:
                        break
                    time.sleep(0.5)
                    timer.check("로그인_값_설정")

                time.sleep(0.3)
                self.get_screenshot("02_login_input.png")
                timer.check("스크린샷_저장")

                # 지연 실행으로 로그인 버튼 클릭
                click_script = """
                    try {
                        var app = nexacro.getApplication();
                        var form = app.mainframe.HFrameSet00.LoginFrame.form.div_login.form;

                        setTimeout(function() {
                            form.btn_login.click();
                        }, 300);

                        return 'click_scheduled';
                    } catch (e) {
                        return 'click_error: ' + e.message;
                    }
                """
                result = self.driver.execute_script(click_script)
                logger.info(f"로그인 버튼 클릭: {result}")
                timer.check("로그인_버튼_클릭")

                time.sleep(1.5)

                # Alert 확인
                try:
                    alert = self.driver.switch_to.alert
                    alert_text = alert.text
                    logger.warning(f"{alert_text}")
                    alert.accept()
                    time.sleep(1)
                except Exception as e:
                    logger.debug(f"Alert 확인 중: {e}")

                # GV_CHANNELTYPE으로 로그인 성공 확인 (타임아웃 적용)
                def check_login_success() -> bool:
                    check_script = """
                        try {
                            var app = nexacro.getApplication();
                            return app.GV_CHANNELTYPE;
                        } catch (e) {
                            return null;
                        }
                    """
                    channel_type = self.driver.execute_script(check_script)
                    logger.info(f"GV_CHANNELTYPE: {channel_type}")
                    return channel_type == 'HOME'

                login_success = wait_with_timeout(
                    check_login_success,
                    timeout=DEFAULT_TIMEOUT,
                    check_interval=1.0,
                    operation_name="로그인_성공_확인"
                )

                if login_success:
                    logger.info("로그인 성공! (GV_CHANNELTYPE === 'HOME')")
                    self.get_screenshot("03_after_login.png")
                    return True

                # 로그인 성공 여부 확인 (백업 방법)
                current_url = self.driver.current_url
                logger.warning(f"로그인 확인 실패 (GV_CHANNELTYPE !== 'HOME'), URL: {current_url}")

                self.get_screenshot("03_after_login.png")
                return False

            except Exception as e:
                log_timeout_error("로그인", timer.elapsed(), {
                    "error": str(e),
                    "error_type": type(e).__name__
                })
                logger.error(f"로그인 실패: {e}")
                return False

    def _click_login_button(self) -> bool:
        """로그인 버튼 클릭 (백업 메서드 - 좌표 기반 클릭)"""
        try:
            # 방법 1: 넥사크로 객체로 직접 클릭
            script = """
                try {
                    var app = nexacro.getApplication();
                    var form = app.mainframe.HFrameSet00.LoginFrame.form.div_login.form;
                    form.btn_login.click();
                    return 'nexacro_click';
                } catch (e) {
                    // 방법 2: DOM에서 찾아서 좌표 기반 클릭
                    var loginBtn = document.querySelector('[id*="btn_login"]');
                    if (loginBtn && loginBtn.offsetParent !== null) {
                        var rect = loginBtn.getBoundingClientRect();
                        var centerX = rect.left + rect.width / 2;
                        var centerY = rect.top + rect.height / 2;

                        var events = ['mousedown', 'mouseup', 'click'];
                        events.forEach(function(eventType) {
                            var event = new MouseEvent(eventType, {
                                bubbles: true,
                                cancelable: true,
                                view: window,
                                clientX: centerX,
                                clientY: centerY
                            });
                            loginBtn.dispatchEvent(event);
                        });
                        return 'dom_click';
                    }
                    return 'not_found: ' + e.message;
                }
            """
            result = self.driver.execute_script(script)
            if result:
                logger.info(f"로그인 버튼 클릭 ({result})")
                time.sleep(1)

            return True

        except Exception as e:
            logger.warning(f"로그인 버튼 클릭 실패: {e}")
            return False

    def close_popup(self) -> None:
        """로그인 후 팝업 닫기 (MouseEvent 시뮬레이션 방식, 최대 3회 시도)"""
        logger.info("팝업 확인 및 닫기...")

        # 최대 3번 반복 시도 (빠른 처리)
        for attempt in range(3):
            try:
                # 팝업 닫기 버튼 클릭 (정확한 ID 기반 + 텍스트 기반)
                close_script = """
                    // MouseEvent 시뮬레이션으로 클릭하는 함수
                    function clickByElement(el) {
                        if (!el || el.offsetParent === null) return false;
                        el.scrollIntoView({block: 'center', inline: 'center'});
                        const r = el.getBoundingClientRect();
                        const o = {
                            bubbles: true,
                            cancelable: true,
                            view: window,
                            clientX: r.left + r.width / 2,
                            clientY: r.top + r.height / 2
                        };
                        el.dispatchEvent(new MouseEvent('mousedown', o));
                        el.dispatchEvent(new MouseEvent('mouseup', o));
                        el.dispatchEvent(new MouseEvent('click', o));
                        return true;
                    }

                    // 1. 닫기/확인 텍스트가 있는 버튼 찾기 (팝업 내부)
                    const closeTexts = ['닫기', '확인'];
                    const allElements = document.querySelectorAll('div, span, button');

                    for (let i = 0; i < allElements.length; i++) {
                        const el = allElements[i];
                        const text = el.innerText ? el.innerText.trim() : '';

                        // 정확히 닫기/확인 텍스트만 있는 요소
                        if (closeTexts.includes(text) && el.offsetParent !== null && el.children.length <= 1) {
                            if (clickByElement(el)) {
                                return 'clicked_text: ' + text;
                            }
                        }
                    }

                    // 2. 특정 ID 패턴으로 닫기 버튼 찾기
                    const closeIds = ['btn_topClose', 'btnClose', 'btn_close', 'btn_enter'];
                    for (let id of closeIds) {
                        const els = document.querySelectorAll('[id*="' + id + '"]');
                        for (let el of els) {
                            if (el.offsetParent !== null) {
                                if (clickByElement(el)) {
                                    return 'clicked_id: ' + el.id;
                                }
                            }
                        }
                    }

                    return false;
                """
                result = self.driver.execute_script(close_script)

                if result:
                    logger.info(f"팝업 닫기: {result}")
                    time.sleep(0.5)  # 0.5초로 단축
                else:
                    logger.info("닫을 팝업 없음")
                    break

            except Exception as e:
                logger.warning(f"팝업 닫기 실패: {e}")
                break

        logger.info("팝업 닫기 완료")

    def collect_weather(self) -> Optional[Dict[str, Any]]:
        """TopFrame에서 날씨 정보 수집

        Returns:
            날씨 정보 딕셔너리 (온도, 날씨유형, 요일, 매장 등) 또는 None
        """
        logger.info("날씨 정보 수집 중...")

        try:
            weather_info = self.driver.execute_script("""
                try {
                    var result = {};

                    // 1. 현재 온도 (sta_degree)
                    var degreeEl = document.querySelector("[id*='sta_degree']");
                    if (degreeEl) {
                        var tempText = degreeEl.innerText || '';
                        var match = tempText.match(/(-?\\d+)/);
                        result.temperature = match ? parseInt(match[1]) : null;
                        result.temperature_raw = tempText.trim();
                    }

                    // 2. 날짜/시간 (sta_date)
                    var dateEl = document.querySelector("[id*='sta_date']");
                    if (dateEl) {
                        result.datetime_raw = dateEl.innerText.trim();
                        var dateMatch = result.datetime_raw.match(/(\\d{4}-\\d{2}-\\d{2})/);
                        result.date = dateMatch ? dateMatch[1] : null;

                        // 요일 추출
                        var dayMatch = result.datetime_raw.match(/\\((.)\\)/);
                        result.day_of_week = dayMatch ? dayMatch[1] : null;
                    }

                    // 3. 날씨 아이콘 이미지에서 날씨 유형 추출
                    var weatherImg = document.querySelector("[id*='img_weather']");
                    if (weatherImg) {
                        var style = window.getComputedStyle(weatherImg);
                        var bgImage = style.backgroundImage || '';
                        result.weather_icon_url = bgImage;

                        // 아이콘 URL에서 날씨 유형 추정
                        var iconLower = bgImage.toLowerCase();
                        if (iconLower.includes('sun') || iconLower.includes('clear') || iconLower.includes('01')) {
                            result.weather_type = 'sunny';
                        } else if (iconLower.includes('cloud') || iconLower.includes('02') || iconLower.includes('03')) {
                            result.weather_type = 'cloudy';
                        } else if (iconLower.includes('rain') || iconLower.includes('09') || iconLower.includes('10')) {
                            result.weather_type = 'rainy';
                        } else if (iconLower.includes('snow') || iconLower.includes('13')) {
                            result.weather_type = 'snowy';
                        } else {
                            result.weather_type = 'unknown';
                        }
                    }

                    // 4. 매장 정보
                    var storeEl = document.querySelector("[id*='sta_storeNm']");
                    if (storeEl) {
                        result.store_name = storeEl.innerText.trim();
                        var storeMatch = result.store_name.match(/\\((\\d+)\\)/);
                        result.store_id = storeMatch ? storeMatch[1] : null;
                    }

                    // 5. 예보 데이터 수집 (ds_weatherTomorrow 트랜잭션)
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
                            // AFTER_DAY=2 설정 (오늘+내일+모레)
                            topForm.ds_weatherCond.setColumn(0, 'AFTER_DAY', '2');

                            // 서버 트랜잭션 동기 호출
                            topForm.gfn_transaction(
                                'selTopWeatherForecast',
                                'weather/selTopWeather',
                                'ds_weatherCond=ds_weatherCond',
                                'ds_weatherTomorrow=ds_weatherTomorrow',
                                '', 'fn_callback', false
                            );

                            // ds_weatherTomorrow에서 날짜별 최고기온 추출
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
                                    // 오늘 제외 (실측 사용)
                                    if (ymdStr <= today) continue;

                                    var highest = dsTmr.getColumn(r, 'HIGHEST_TMPT');
                                    var temp = null;
                                    if (highest !== null && highest !== undefined) {
                                        // 넥사크로 Decimal 객체: .hi에 정수값
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

                            // 원래 AFTER_DAY 복원
                            topForm.ds_weatherCond.setColumn(0, 'AFTER_DAY', '2');
                        }
                    } catch(eForecast) {
                        result.forecast_error = eForecast.message;
                    }

                    return result;
                } catch (e) {
                    return {error: e.message};
                }
            """)

            if weather_info and not weather_info.get('error'):
                self.weather_data = weather_info
                logger.info(f"온도: {weather_info.get('temperature')}도")
                logger.info(f"날짜: {weather_info.get('date')} ({weather_info.get('day_of_week')})")
                logger.info(f"날씨: {weather_info.get('weather_type', 'N/A')}")
                logger.info(f"매장: {weather_info.get('store_name')}")

                forecast_daily = weather_info.get("forecast_daily", {})
                if forecast_daily:
                    logger.info(f"예보 최고기온: {forecast_daily}")
                elif weather_info.get('forecast_error'):
                    logger.debug(f"예보 수집 오류: {weather_info['forecast_error']}")

                return weather_info
            else:
                logger.warning(f"날씨 정보 수집 실패: {weather_info.get('error')}")
                return None

        except Exception as e:
            logger.error(f"날씨 수집 오류: {e}")
            return None

    def navigate_to_sales_menu(self) -> bool:
        """매출분석 > 중분류별 매출 구성비 메뉴로 이동

        Returns:
            메뉴 이동 성공 여부
        """
        logger.info("매출분석 메뉴로 이동...")

        with OperationTimer("매출분석_메뉴_이동", timeout=DEFAULT_TIMEOUT) as timer:
            try:
                # 팝업 로딩 대기 후 닫기
                time.sleep(2)
                self.close_popup()
                time.sleep(0.5)
                timer.check("팝업_닫기")

                self.get_screenshot("04_popup_closed.png")

                # 정확한 ID 기반 메뉴 클릭 (MouseEvent 시뮬레이션)
                logger.info("매출분석 메뉴 클릭 (정확한 ID 기반)...")

                menu_script = """
                    // MouseEvent 시뮬레이션으로 클릭
                    function clickById(id) {
                        const el = document.getElementById(id);
                        if (!el || el.offsetParent === null) {
                            console.warn('ID 클릭 실패:', id);
                            return false;
                        }
                        el.scrollIntoView({block: 'center', inline: 'center'});
                        const r = el.getBoundingClientRect();
                        const o = {
                            bubbles: true,
                            cancelable: true,
                            view: window,
                            clientX: r.left + r.width / 2,
                            clientY: r.top + r.height / 2
                        };
                        el.dispatchEvent(new MouseEvent('mousedown', o));
                        el.dispatchEvent(new MouseEvent('mouseup', o));
                        el.dispatchEvent(new MouseEvent('click', o));
                        console.log('클릭 완료:', id);
                        return true;
                    }

                    // 매출분석 탭 클릭 (정확한 ID)
                    const topMenuId = "mainframe.HFrameSet00.VFrameSet00.TopFrame.form.div_topMenu.form.STMB000_M0:icontext";
                    return clickById(topMenuId);
                """
                result = self.driver.execute_script(menu_script)
                logger.info(f"매출분석 메뉴 클릭 결과: {result}")
                timer.check("매출분석_메뉴_클릭")

                time.sleep(1)
                self.get_screenshot("05_menu_sales.png")

                # 서브메뉴: 중분류별 매출 구성비 클릭
                logger.info("중분류별 매출 구성비 서브메뉴 클릭...")
                submenu_script = """
                    function clickById(id) {
                        const el = document.getElementById(id);
                        if (!el || el.offsetParent === null) {
                            console.warn('ID 클릭 실패:', id);
                            return false;
                        }
                        el.scrollIntoView({block: 'center', inline: 'center'});
                        const r = el.getBoundingClientRect();
                        const o = {
                            bubbles: true,
                            cancelable: true,
                            view: window,
                            clientX: r.left + r.width / 2,
                            clientY: r.top + r.height / 2
                        };
                        el.dispatchEvent(new MouseEvent('mousedown', o));
                        el.dispatchEvent(new MouseEvent('mouseup', o));
                        el.dispatchEvent(new MouseEvent('click', o));
                        console.log('클릭 완료:', id);
                        return true;
                    }

                    // 중분류별 매출 구성비 클릭 (정확한 ID)
                    const subMenuId = "mainframe.HFrameSet00.VFrameSet00.TopFrame.form.pdiv_topMenu_STMB000_M0.form.STMB011_M0:text";
                    return clickById(subMenuId);
                """
                result = self.driver.execute_script(submenu_script)
                logger.info(f"서브메뉴 클릭 결과: {result}")
                timer.check("서브메뉴_클릭")

                time.sleep(1.5)
                self.get_screenshot("06_submenu.png")
                timer.check("스크린샷_저장")

                logger.info(f"메뉴 이동 완료 ({timer.elapsed():.1f}초)")
                return True

            except Exception as e:
                log_timeout_error("매출분석_메뉴_이동", timer.elapsed(), {
                    "error": str(e),
                    "error_type": type(e).__name__
                })
                logger.error(f"메뉴 이동 실패: {e}")
                return False

    def _click_menu_by_text(self, text: str) -> bool:
        """텍스트로 메뉴 클릭"""
        try:
            # XPath로 텍스트 포함 요소 찾기
            xpath = f"//*[contains(text(), '{text}')]"
            elements = self.driver.find_elements(By.XPATH, xpath)

            for elem in elements:
                if elem.is_displayed():
                    try:
                        elem.click()
                        logger.info(f"메뉴 클릭: {text}")
                        return True
                    except Exception:
                        # ActionChains 사용
                        actions = ActionChains(self.driver)
                        actions.move_to_element(elem).click().perform()
                        logger.info(f"메뉴 클릭 (ActionChains): {text}")
                        return True

            # JavaScript로 클릭
            script = f"""
                var elems = document.querySelectorAll('*');
                for (var i = 0; i < elems.length; i++) {{
                    if (elems[i].textContent.includes('{text}') &&
                        elems[i].offsetParent !== null) {{
                        elems[i].click();
                        return true;
                    }}
                }}
                return false;
            """
            self.driver.execute_script(script)
            return True

        except Exception as e:
            logger.warning(f"메뉴 클릭 실패 ({text}): {e}")
            return False

    def set_date_and_search(self, date_str: str) -> bool:
        """
        날짜를 설정하고 조회 버튼 클릭

        Args:
            date_str: YYYYMMDD 형식의 날짜 (예: "20260124")

    Returns:
        날짜 설정 및 조회 성공 여부
    """
        logger.info(f"날짜 설정: {date_str}")

        try:
            result = self.driver.execute_script(f"""
                try {{
                    const app = nexacro.getApplication();
                    const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB011_M0.form;

                    // 날짜 설정
                    form.div_workForm.form.div2.form.div_search.form.calFromDay.set_value("{date_str}");
                    console.log("[SET] calFromDay 값 → {date_str}");

                    // 조회 버튼 클릭
                    setTimeout(function() {{
                        form.div_cmmbtn.form.F_10.click();
                        console.log("[ACTION] 조회 버튼(F_10) 클릭");
                    }}, 300);

                    return true;
                }} catch (e) {{
                    console.error("날짜 설정 실패:", e.message);
                    return false;
                }}
            """)

            if result:
                logger.info(f"날짜 {date_str} 설정 및 조회 완료")
                time.sleep(2)  # 데이터 로딩 대기
                return True
            else:
                logger.warning("날짜 설정 실패")
                return False

        except Exception as e:
            logger.error(f"날짜 설정 오류: {e}")
            return False

    def wait_for_dataset(self, timeout: int = 30) -> bool:
        """dsList 데이터셋 로딩 대기

        Args:
            timeout: 최대 대기 시간 (초)

        Returns:
            로딩 완료 여부
        """
        logger.info("데이터셋 로딩 대기중...")

        start_time = time.time()
        last_row_count = -1
        stable_since = time.time()

        while time.time() - start_time < timeout:
            try:
                row_count = self.driver.execute_script("""
                    try {
                        const app = nexacro.getApplication();
                        if (!app || !app.mainframe) return 0;

                        const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB011_M0?.form;
                        if (!form) return 0;

                        const dsList = form.div_workForm?.form?.dsList;
                        if (!dsList || typeof dsList.getRowCount !== 'function') return 0;

                        return dsList.getRowCount();
                    } catch (e) {
                        return 0;
                    }
                """)

                if row_count > 0:
                    if row_count == last_row_count:
                        if time.time() - stable_since > 2:
                            logger.info(f"데이터셋 로딩 완료: {row_count}개 중분류")
                            return True
                    else:
                        last_row_count = row_count
                        stable_since = time.time()

                time.sleep(0.5)
            except Exception as e:
                time.sleep(0.5)

        logger.warning(f"데이터셋 로딩 타임아웃 (최종 행수: {last_row_count})")
        return False

    def get_all_mid_categories(self) -> List[Dict[str, Any]]:
        """모든 중분류 목록 수집

        Returns:
            중분류 목록 (index, MID_CD, MID_NM 포함)
        """
        logger.info("중분류 전체 목록 수집 시작...")

        # 데이터셋 로딩 대기
        self.wait_for_dataset()

        try:
            # dsList에서 모든 중분류 목록 가져오기
            categories = self.driver.execute_script("""
                try {
                    const app = nexacro.getApplication();
                    const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB011_M0.form;
                    const dsList = form.div_workForm.form.dsList;

                    const result = [];
                    const rowCount = dsList.getRowCount();

                    for (let i = 0; i < rowCount; i++) {
                        result.push({
                            index: i,
                            MID_CD: dsList.getColumn(i, "MID_CD"),
                            MID_NM: dsList.getColumn(i, "MID_NM")
                        });
                    }

                    return result;
                } catch (e) {
                    console.error('중분류 목록 조회 실패:', e);
                    return [];
                }
            """)

            if categories:
                logger.info(f"총 {len(categories)}개 중분류 발견:")
                for cat in categories:
                    logger.info(f"  - [{cat['MID_CD']}] {cat['MID_NM']}")
                self.mid_categories = categories
                return categories
            else:
                logger.warning("중분류 목록을 찾을 수 없습니다")
                return []

        except Exception as e:
            logger.error(f"중분류 목록 조회 실패: {e}")
            return []

    def save_mid_categories_list(self, filename: Optional[str] = None) -> Optional[str]:
        """중분류 목록만 JSON 파일로 저장

        Args:
            filename: 저장 파일명 (기본값: mid_categories_날짜시간.json)

        Returns:
            저장된 파일 경로 또는 None
        """
        if not hasattr(self, 'mid_categories') or not self.mid_categories:
            logger.warning("저장할 중분류 목록 없음")
            return

        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"mid_categories_{timestamp}.json"

        # data 폴더에 저장
        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
        os.makedirs(data_dir, exist_ok=True)
        filepath = os.path.join(data_dir, filename)

        # index 필드 제거하고 저장
        categories_to_save = [
            {"MID_CD": cat["MID_CD"], "MID_NM": cat["MID_NM"]}
            for cat in self.mid_categories
        ]

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(categories_to_save, f, ensure_ascii=False, indent=2)

        logger.info(f"중분류 목록 저장: {filepath}")
        return filepath

    def wait_for_transaction(self, timeout: int = 10) -> bool:
        """트랜잭션 완료 대기

        Args:
            timeout: 최대 대기 시간 (초)

        Returns:
            트랜잭션 완료 여부
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                is_complete = self.driver.execute_script("""
                    try {
                        const app = nexacro.getApplication();
                        // 트랜잭션이 진행 중이 아니면 완료
                        return !app._is_loading && !app._async_transaction_count;
                    } catch (e) {
                        return true;
                    }
                """)

                if is_complete:
                    return True

                time.sleep(0.3)
            except Exception:
                time.sleep(0.3)

        return False

    def wait_for_detail_data_stable(self, timeout: int = 15) -> int:
        """dsDetail 데이터셋이 완전히 로딩되어 안정화될 때까지 대기

        행 수가 3번 연속 동일하면 안정화로 판단

        Args:
            timeout: 최대 대기 시간 (초)

        Returns:
            안정화된 행 수 (타임아웃 시 마지막 확인 행 수)
        """
        logger.info("dsDetail 데이터 로딩 대기중...")
        start_time = time.time()
        last_row_count = -1
        stable_count = 0
        required_stable = 3  # 3번 연속 같은 값이면 안정화로 판단

        while time.time() - start_time < timeout:
            try:
                row_count = self.driver.execute_script("""
                    try {
                        const app = nexacro.getApplication();
                        const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB011_M0.form;
                        const dsDetail = form.div_workForm.form.dsDetail;
                        if (!dsDetail) return -1;
                        return dsDetail.getRowCount();
                    } catch (e) {
                        return -1;
                    }
                """)

                if row_count >= 0:
                    if row_count == last_row_count:
                        stable_count += 1
                        if stable_count >= required_stable:
                            logger.info(f"dsDetail 로딩 완료: {row_count}개 상품")
                            return row_count
                    else:
                        stable_count = 0
                        last_row_count = row_count

                time.sleep(0.3)
            except Exception as e:
                time.sleep(0.3)

        logger.warning(f"dsDetail 로딩 타임아웃 (최종 행수: {last_row_count})")
        return last_row_count

    def click_mid_category(self, index: int) -> bool:
        """특정 중분류 클릭 (상세 데이터 로딩)

        Args:
            index: 중분류 행 인덱스

        Returns:
            클릭 성공 여부
        """
        try:
            result = self.driver.execute_script(f"""
                try {{
                    const app = nexacro.getApplication();
                    const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB011_M0.form;
                    const gdList = form.div_workForm.form.div2.form.gdList;

                    // 행 선택
                    gdList.selectRow({index});

                    // 클릭 이벤트 발생
                    const evt = new nexacro.GridClickEventInfo(
                        gdList, "oncellclick", false, false, false, false,
                        0, 0, {index}, {index}
                    );

                    if (gdList.oncellclick) {{
                        gdList.oncellclick._fireEvent(gdList, evt);
                    }}

                    return true;
                }} catch (e) {{
                    console.error('중분류 클릭 실패:', e);
                    return false;
                }}
            """)

            return result
        except Exception as e:
            logger.error(f"중분류 클릭 실패: {e}")
            return False

    def click_mid_category_and_wait(self, index: int, timeout: int = 15) -> int:
        """중분류 클릭 후 dsDetail 데이터 로딩 완료까지 대기

        Args:
            index: 중분류 행 인덱스
            timeout: 최대 대기 시간 (초)

        Returns:
            로딩된 상품 행 수 (실패 시 -1)
        """
        try:
            # JavaScript에서 클릭 후 데이터 로딩 완료까지 대기
            result = self.driver.execute_async_script(f"""
                const callback = arguments[arguments.length - 1];
                const timeout = {timeout * 1000};
                const startTime = Date.now();

                try {{
                    const app = nexacro.getApplication();
                    const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB011_M0.form;
                    const gdList = form.div_workForm.form.div2.form.gdList;
                    const dsDetail = form.div_workForm.form.dsDetail;

                    // 클릭 전 현재 상세 데이터 행 수 기록
                    const prevRowCount = dsDetail ? dsDetail.getRowCount() : 0;

                    // 행 선택 및 클릭 이벤트 발생
                    gdList.selectRow({index});
                    const evt = new nexacro.GridClickEventInfo(
                        gdList, "oncellclick", false, false, false, false,
                        0, 0, {index}, {index}
                    );
                    if (gdList.oncellclick) {{
                        gdList.oncellclick._fireEvent(gdList, evt);
                    }}

                    // 데이터 로딩 완료 대기 (행 수 변화 후 안정화 확인)
                    let stableCount = 0;
                    let lastRowCount = -1;

                    const checkInterval = setInterval(() => {{
                        const currentRowCount = dsDetail ? dsDetail.getRowCount() : 0;

                        // 행 수가 변했고 (또는 처음부터 데이터가 있고), 안정화됨
                        if (currentRowCount === lastRowCount && currentRowCount >= 0) {{
                            stableCount++;
                            if (stableCount >= 3) {{  // 3회 연속 동일하면 로딩 완료
                                clearInterval(checkInterval);
                                callback({{ success: true, rowCount: currentRowCount }});
                                return;
                            }}
                        }} else {{
                            stableCount = 0;
                            lastRowCount = currentRowCount;
                        }}

                        // 타임아웃 체크
                        if (Date.now() - startTime > timeout) {{
                            clearInterval(checkInterval);
                            callback({{ success: false, rowCount: lastRowCount, error: 'timeout' }});
                        }}
                    }}, 300);

                }} catch (e) {{
                    callback({{ success: false, error: e.message }});
                }}
            """)

            if result and result.get('success'):
                return result.get('rowCount', 0)
            else:
                logger.warning(f"중분류 클릭/대기 실패: {result}")
                return -1

        except Exception as e:
            logger.error(f"click_mid_category_and_wait 실패: {e}")
            return -1

    def get_detail_data(self) -> List[Dict[str, Any]]:
        """현재 선택된 중분류의 상세 데이터 가져오기

        Returns:
            상품별 데이터 리스트 (ITEM_CD, ITEM_NM, SALE_QTY 등)
        """
        try:
            data = self.driver.execute_script("""
                try {
                    const app = nexacro.getApplication();
                    const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB011_M0.form;
                    const dsDetail = form.div_workForm.form.dsDetail;

                    const items = [];
                    const rowCount = dsDetail.getRowCount();

                    for (let i = 0; i < rowCount; i++) {
                        items.push({
                            ITEM_CD: dsDetail.getColumn(i, "ITEM_CD"),
                            ITEM_NM: dsDetail.getColumn(i, "ITEM_NM"),
                            SALE_QTY: dsDetail.getColumn(i, "SALE_QTY"),
                            ORD_QTY: dsDetail.getColumn(i, "ORD_QTY"),
                            BUY_QTY: dsDetail.getColumn(i, "BUY_QTY"),
                            DISUSE_QTY: dsDetail.getColumn(i, "DISUSE_QTY"),
                            STOCK_QTY: dsDetail.getColumn(i, "STOCK_QTY")
                        });
                    }

                    return items;
                } catch (e) {
                    console.error('상세 데이터 조회 실패:', e);
                    return [];
                }
            """)

            return data or []
        except Exception as e:
            logger.error(f"상세 데이터 조회 실패: {e}")
            return []

    def collect_all_mid_category_data(self, target_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        모든 중분류의 상품 데이터 수집

        Args:
            target_date: 수집 대상 날짜 (YYYYMMDD 형식, 예: "20260124")
                         None이면 현재 화면 데이터 수집

        Returns:
            전체 중분류 상품 데이터 리스트
        """
        logger.info("모든 중분류 상품 데이터 수집 시작")
        if target_date:
            logger.info(f"대상 날짜: {target_date}")

        # 0. 날짜 설정 (지정된 경우)
        if target_date:
            self.set_date_and_search(target_date)
            # 데이터 로딩 대기
            self.wait_for_dataset()

        # 1. 중분류 목록 가져오기
        categories = self.get_all_mid_categories()
        if not categories:
            logger.error("중분류 목록이 없습니다")
            return []

        all_data = []

        # 2. 각 중분류 순회하며 데이터 수집
        for cat in categories:
            idx = cat['index']
            mid_cd = cat['MID_CD']
            mid_nm = cat['MID_NM']

            logger.info(f"[{idx+1}/{len(categories)}] 중분류 [{mid_cd}] {mid_nm} 처리중...")

            # 중분류 클릭 및 데이터 로딩 완료 대기 (JavaScript에서 처리)
            row_count = self.click_mid_category_and_wait(idx, timeout=15)

            if row_count >= 0:
                # 상세 데이터 가져오기
                detail_items = self.get_detail_data()

                # 검증: 가져온 데이터 수와 예상 행 수 비교
                if row_count > 0 and len(detail_items) != row_count:
                    logger.warning(f"데이터 불일치! 예상: {row_count}, 실제: {len(detail_items)}")
                    # 재시도: 추가 대기 후 다시 가져오기
                    time.sleep(1)
                    detail_items = self.get_detail_data()
                    logger.warning(f"재시도 후: {len(detail_items)}개")

                # 중분류 정보 추가
                for item in detail_items:
                    item['MID_CD'] = mid_cd
                    item['MID_NM'] = mid_nm

                all_data.extend(detail_items)
                logger.info(f"  -> {len(detail_items)}개 상품 수집 완료")
            else:
                logger.warning("클릭/로딩 실패")

            time.sleep(0.3)  # 다음 중분류 처리 전 잠시 대기

        logger.info(f"수집 완료! 총 {len(all_data)}개 상품")

        self.sales_data = all_data
        return all_data

    def extract_table_data(self) -> List[Any]:
        """HTML 테이블 또는 Grid 컴포넌트에서 데이터 추출

        Returns:
            추출된 행 데이터 리스트
        """
        logger.info("데이터 추출 중...")
        time.sleep(1)

        try:
            # 방법 1: HTML 테이블에서 추출
            script = """
                var result = [];
                var tables = document.querySelectorAll('table');
                tables.forEach(function(table) {
                    var rows = table.querySelectorAll('tr');
                    rows.forEach(function(row) {
                        var cells = row.querySelectorAll('th, td');
                        var rowData = [];
                        cells.forEach(function(cell) {
                            rowData.push(cell.textContent.trim());
                        });
                        if (rowData.length > 0 && rowData.some(x => x !== '')) {
                            result.push(rowData);
                        }
                    });
                });
                return result;
            """
            data = self.driver.execute_script(script)

            if data and len(data) > 0:
                logger.info(f"테이블에서 {len(data)}행 추출")
                self.sales_data = data
                return data

            # 방법 2: Grid 컴포넌트에서 추출
            script = """
                var result = [];
                var gridCells = document.querySelectorAll('[class*="Grid"] [class*="cell"], [id*="grid"] [class*="cell"]');
                var currentRow = [];
                gridCells.forEach(function(cell, index) {
                    currentRow.push(cell.textContent.trim());
                    if (currentRow.length >= 10) {
                        result.push(currentRow);
                        currentRow = [];
                    }
                });
                if (currentRow.length > 0) result.push(currentRow);
                return result;
            """
            data = self.driver.execute_script(script)

            if data and len(data) > 0:
                logger.info(f"Grid에서 {len(data)}행 추출")
                self.sales_data = data
                return data

            logger.warning("데이터를 찾지 못했습니다")
            return []

        except Exception as e:
            logger.error(f"데이터 추출 실패: {e}")
            return []

    def save_to_csv(self, filename: Optional[str] = None) -> Optional[str]:
        """수집된 매출 데이터를 CSV 파일로 저장

        Args:
            filename: 저장 파일명 (기본값: mid_category_data_날짜시간.csv)

        Returns:
            저장된 파일 경로 또는 None
        """
        if not self.sales_data:
            logger.warning("저장할 데이터 없음")
            return

        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"mid_category_data_{timestamp}.csv"

        # data 폴더에 저장
        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
        os.makedirs(data_dir, exist_ok=True)
        filepath = os.path.join(data_dir, filename)

        # dict 형태의 데이터인 경우
        if self.sales_data and isinstance(self.sales_data[0], dict):
            fieldnames = ['MID_CD', 'MID_NM', 'ITEM_CD', 'ITEM_NM',
                          'SALE_QTY', 'ORD_QTY', 'BUY_QTY', 'DISUSE_QTY', 'STOCK_QTY']
            with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self.sales_data)
        else:
            # 기존 리스트 형태
            with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerows(self.sales_data)

        logger.info(f"CSV 저장: {filepath}")
        return filepath

    def save_to_json(self, filename: Optional[str] = None) -> Optional[str]:
        """수집된 매출 데이터를 JSON 파일로 저장

        Args:
            filename: 저장 파일명 (기본값: mid_category_data_날짜시간.json)

        Returns:
            저장된 파일 경로 또는 None
        """
        if not self.sales_data:
            logger.warning("저장할 데이터 없음")
            return

        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"mid_category_data_{timestamp}.json"

        # data 폴더에 저장
        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
        os.makedirs(data_dir, exist_ok=True)
        filepath = os.path.join(data_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.sales_data, f, ensure_ascii=False, indent=2)

        logger.info(f"JSON 저장: {filepath}")
        return filepath

    def get_screenshot(self, filename: str) -> None:
        """스크린샷 저장 (data/screenshots/ 디렉토리에 통합 저장)

        Args:
            filename: 저장할 파일명 (예: "01_main.png")
        """
        from src.utils.screenshot import get_screenshot_dir
        filepath = str(get_screenshot_dir() / filename)
        self.driver.save_screenshot(filepath)
        logger.info(f"[SCREENSHOT] {filepath}")

    def close(self) -> None:
        """브라우저 종료"""
        if self.driver:
            self.driver.quit()
            logger.info("브라우저 종료")


def main(auto_close: bool = False, collect_all: bool = True, categories_only: bool = False) -> None:
    """
    BGF 리테일 매출 분석 데이터 수집

    Args:
        auto_close: True면 자동 종료
        collect_all: True면 모든 중분류 데이터 수집 (기본값)
        categories_only: True면 중분류 목록만 수집 (상품 데이터 제외)
    """
    logger.info("BGF 리테일 매출 분석 데이터 수집")

    analyzer = SalesAnalyzer()

    try:
        # 1. 드라이버 설정 및 접속
        analyzer.setup_driver()
        analyzer.connect()

        # 2. 로그인
        analyzer.do_login()

        # 3. 매출분석 메뉴 이동
        analyzer.navigate_to_sales_menu()

        # 4. 데이터 수집
        if categories_only:
            # 중분류 목록만 수집
            categories = analyzer.get_all_mid_categories()
            if categories:
                analyzer.save_mid_categories_list()
                logger.info(f"중분류 목록 {len(categories)}개 저장 완료!")
            data = None
        elif collect_all:
            # 모든 중분류 데이터 수집 (신규 기능)
            data = analyzer.collect_all_mid_category_data()
        else:
            # 기존 방식: 현재 화면 데이터만 추출
            data = analyzer.extract_table_data()

        if data:
            logger.info(f"추출된 데이터 ({len(data)}개 항목):")
            # 처음 5개만 미리보기
            for i, row in enumerate(data[:5]):
                if isinstance(row, dict):
                    logger.info(f"  [{row.get('MID_CD')}] {row.get('MID_NM')} - "
                                f"{row.get('ITEM_NM')} (판매: {row.get('SALE_QTY')})")
                else:
                    logger.info(f"  {row}")
            if len(data) > 5:
                logger.info(f"  ... 외 {len(data) - 5}개")

            # 저장
            csv_path = analyzer.save_to_csv()
            json_path = analyzer.save_to_json()

            # 중분류 목록도 별도 저장
            if analyzer.mid_categories:
                analyzer.save_mid_categories_list()

            logger.info("데이터 수집 및 저장 완료!")
        elif not categories_only:
            logger.warning("수집된 데이터 없음")

        # 5. 대기
        if auto_close:
            logger.info("10초 후 자동 종료...")
            time.sleep(10)
        else:
            logger.info("확인 후 Enter 키를 누르면 종료...")
            try:
                input()
            except EOFError:
                time.sleep(30)

    except KeyboardInterrupt:
        logger.info("중단됨")

    finally:
        analyzer.close()


if __name__ == "__main__":
    import sys

    # 명령줄 옵션 파싱
    auto_close = "--auto" in sys.argv or "-a" in sys.argv
    categories_only = "--categories-only" in sys.argv or "-c" in sys.argv
    simple_mode = "--simple" in sys.argv or "-s" in sys.argv

    if "--help" in sys.argv or "-h" in sys.argv:
        print("""
BGF 리테일 매출 분석 데이터 수집기

사용법: python sales_analyzer.py [옵션]

옵션:
  --auto, -a           자동 종료 모드
  --categories-only, -c 중분류 목록만 수집 (상품 데이터 제외)
  --simple, -s         기존 방식 (현재 화면 데이터만 추출)
  --help, -h           도움말 표시

기본 동작:
  모든 중분류를 순회하며 전체 상품 데이터를 수집합니다.
""")
    else:
        main(
            auto_close=auto_close,
            collect_all=not simple_mode,
            categories_only=categories_only
        )
