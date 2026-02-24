"""
매출 데이터 수집기
- BGF 리테일 사이트에서 중분류별 매출 데이터 수집
- 다중 날짜 수집 지원 (한 번 로그인으로 여러 날짜 수집)
"""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# 상위 경로 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from .base import BaseCollector
from sales_analyzer import SalesAnalyzer
from src.settings.timing import INTER_REQUEST_DELAY, AFTER_ACTION_WAIT, SA_TAB_CLOSE_WAIT
from src.settings.constants import COLLECTION_RETRY_BASE_WAIT, DEFAULT_STORE_ID
from src.settings.timing import COLLECTION_MAX_RETRIES
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SalesCollector(BaseCollector):
    """BGF 매출 데이터 수집기"""

    def __init__(self, store_id: str = DEFAULT_STORE_ID) -> None:
        super().__init__(name="SalesCollector")
        self.store_id = store_id
        self.analyzer: Optional[SalesAnalyzer] = None
        self.weather_data: Optional[Dict[str, Any]] = None
        self._is_logged_in: bool = False

    def _ensure_login(self) -> bool:
        """로그인 확인 및 필요시 로그인 수행"""
        if self._is_logged_in and self.analyzer:
            return True

        self.analyzer = SalesAnalyzer(store_id=self.store_id)

        # 드라이버 설정 및 접속
        self.analyzer.setup_driver()
        self.analyzer.connect()

        # 로그인
        if not self.analyzer.do_login():
            raise Exception("Login failed")

        # 팝업 닫기 및 날씨 정보 수집
        self.analyzer.close_popup()
        self.weather_data = self.analyzer.collect_weather()

        self._is_logged_in = True
        return True

    def _ensure_sales_menu(self) -> bool:
        """매출분석 메뉴 이동 확인"""
        if not self._is_logged_in:
            self._ensure_login()

        # 매출분석 메뉴 이동
        if not self.analyzer.navigate_to_sales_menu():
            raise Exception("Navigation to sales menu failed")

        return True

    def collect(self, target_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        매출 데이터 수집 (단일 날짜)

        Args:
            target_date: 수집 대상 날짜 (YYYY-MM-DD)
                         기본값: 어제 날짜

        Returns:
            수집된 판매 데이터 리스트
        """
        if target_date is None:
            yesterday = datetime.now() - timedelta(days=1)
            target_date = yesterday.strftime("%Y-%m-%d")

        logger.info(f"Target date: {target_date}")

        try:
            self._ensure_login()
            self._ensure_sales_menu()

            # 날짜 변환: YYYY-MM-DD → YYYYMMDD
            date_yyyymmdd = target_date.replace("-", "")
            data = self.analyzer.collect_all_mid_category_data(date_yyyymmdd)

            return data or []

        finally:
            # 단일 수집에서는 종료
            self.close()

    def collect_multiple_dates(
        self,
        dates: List[str],
        save_callback: Optional[Any] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        여러 날짜의 데이터를 한 번 로그인으로 수집

        Args:
            dates: 수집할 날짜 리스트 (YYYY-MM-DD 형식)
            save_callback: 각 날짜 수집 후 호출할 저장 함수
                          (date_str, data) -> stats

        Returns:
            {날짜: {success, data, stats}} 형태의 결과
        """
        results = {}

        try:
            # 1. 로그인 (한 번만)
            logger.info(f"Collecting {len(dates)} dates in one session")
            self._ensure_login()
            self._ensure_sales_menu()

            # 2. 각 날짜별 수집
            for i, date_str in enumerate(dates):
                logger.info(f"[{i+1}/{len(dates)}] Collecting: {date_str}")

                try:
                    # 날짜 변환 및 수집
                    date_yyyymmdd = date_str.replace("-", "")
                    data = self.analyzer.collect_all_mid_category_data(date_yyyymmdd)

                    result = {
                        "success": bool(data),
                        "data": data or [],
                        "item_count": len(data) if data else 0
                    }

                    # 저장 콜백 호출
                    if save_callback and data:
                        try:
                            stats = save_callback(date_str, data)
                            result["stats"] = stats
                            logger.info(f"Saved: {stats.get('total', 0)} items")
                        except Exception as e:
                            logger.error(f"Save failed: {e}")
                            result["save_error"] = str(e)

                    results[date_str] = result

                except Exception as e:
                    logger.error(f"Collection failed: {e}")
                    results[date_str] = {
                        "success": False,
                        "error": str(e),
                        "data": []
                    }

                # 요청 간 짧은 딜레이
                if i < len(dates) - 1:
                    time.sleep(INTER_REQUEST_DELAY)

        except Exception as e:
            logger.error(f"Session error: {e}")
            # 아직 수집되지 않은 날짜들 실패 처리
            for date_str in dates:
                if date_str not in results:
                    results[date_str] = {
                        "success": False,
                        "error": str(e),
                        "data": []
                    }

        return results

    def get_driver(self) -> Optional[Any]:
        """현재 드라이버 반환 (자동 발주에서 재사용용)"""
        return self.analyzer.driver if self.analyzer else None

    def get_analyzer(self) -> Optional[SalesAnalyzer]:
        """현재 analyzer 반환"""
        return self.analyzer

    def _close_any_popup(self, driver: Any, max_attempts: int = 3) -> None:
        """팝업 닫기 (MouseEvent 시뮬레이션 방식 - 강화된 버전)"""
        for attempt in range(max_attempts):
            try:
                result = driver.execute_script("""
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

                    // 방법 1: 닫기/확인 텍스트가 있는 버튼 찾기 (팝업 내부)
                    const closeTexts = ['닫기', '확인', '취소', '아니오', 'OK', 'Close', 'Cancel', 'No'];
                    const allElements = document.querySelectorAll('div, span, button');

                    for (let i = 0; i < allElements.length; i++) {
                        const el = allElements[i];
                        const text = el.innerText ? el.innerText.trim() : '';

                        // 정확히 닫기/확인 텍스트만 있는 요소
                        if (closeTexts.includes(text) && el.offsetParent !== null && el.children.length <= 1) {
                            if (clickByElement(el)) {
                                return {clicked: true, method: 'text', text: text, id: el.id};
                            }
                        }
                    }

                    // 방법 2: 특정 ID 패턴으로 닫기 버튼 찾기 (btn_topClose 추가)
                    const closeIds = ['btn_topClose', 'btnClose', 'btn_close', 'btn_enter', 'btn_ok', 'btn_cancel'];
                    for (let id of closeIds) {
                        const els = document.querySelectorAll('[id*="' + id + '"]');
                        for (let el of els) {
                            if (el.offsetParent !== null) {
                                if (clickByElement(el)) {
                                    return {clicked: true, method: 'id_pattern', id: el.id};
                                }
                            }
                        }
                    }

                    // 방법 3: popupframe 내부의 닫기 버튼 (로그인 후 팝업)
                    const popupFrames = document.querySelectorAll('[id*="popupframe"], [id*="Popup"], [id*="popup"]');
                    for (const frame of popupFrames) {
                        if (frame.offsetParent === null) continue;
                        const closeBtn = frame.querySelector('[id*="Close"], [id*="close"], [id*="btn_"]');
                        if (closeBtn && closeBtn.offsetParent !== null) {
                            if (clickByElement(closeBtn)) {
                                return {clicked: true, method: 'popup_frame', id: closeBtn.id};
                            }
                        }
                    }

                    return {clicked: false};
                """)

                if result and result.get("clicked"):
                    logger.info(f"Popup closed: {result.get('method')} - {result.get('text') or result.get('id')}")
                    time.sleep(INTER_REQUEST_DELAY)
                else:
                    break  # 팝업 없음

            except Exception as e:
                logger.error(f"Popup close error: {e}")
                break

    def close_sales_menu(self) -> bool:
        """매출분석 메뉴 탭 닫기 (자동 발주 전환용)

        Returns:
            탭 닫기 성공 여부
        """
        if not self.analyzer:
            return False

        driver = self.analyzer.driver
        max_attempts = 3

        for attempt in range(max_attempts):
            try:
                # 탭 닫기 버튼 클릭
                # 패턴: tab_openList.tabbutton_N.extrabutton:icontext
                result = driver.execute_script(r"""
                    // nexacro 이벤트 트리거 함수
                    function triggerClick(el) {
                        // 방법 1: 직접 클릭
                        el.click();

                        // 방법 2: 마우스 이벤트 시뮬레이션
                        const rect = el.getBoundingClientRect();
                        const mouseDown = new MouseEvent('mousedown', {
                            bubbles: true, cancelable: true, view: window,
                            clientX: rect.left + rect.width/2,
                            clientY: rect.top + rect.height/2
                        });
                        const mouseUp = new MouseEvent('mouseup', {
                            bubbles: true, cancelable: true, view: window,
                            clientX: rect.left + rect.width/2,
                            clientY: rect.top + rect.height/2
                        });
                        const clickEvt = new MouseEvent('click', {
                            bubbles: true, cancelable: true, view: window,
                            clientX: rect.left + rect.width/2,
                            clientY: rect.top + rect.height/2
                        });
                        el.dispatchEvent(mouseDown);
                        el.dispatchEvent(mouseUp);
                        el.dispatchEvent(clickEvt);
                        return true;
                    }

                    // 중분류별 매출 탭 찾아서 닫기
                    // 1. 먼저 "중분류별 매출" 텍스트가 있는 탭 버튼 찾기
                    const tabTexts = document.querySelectorAll('[id*="tab_openList"][id*="tabbuttonitemtext"]');
                    for (const textEl of tabTexts) {
                        if (textEl.innerText && textEl.innerText.includes('중분류별 매출')) {
                            // 같은 탭 버튼의 extrabutton (X 버튼) 찾기
                            const tabId = textEl.id;
                            const match = tabId.match(/tabbutton_(\d+)/);
                            if (match) {
                                const tabNum = match[1];
                                const closeBtn = document.querySelector(
                                    '[id*="tab_openList.tabbutton_' + tabNum + '.extrabutton"]'
                                );
                                if (closeBtn && closeBtn.offsetParent !== null) {
                                    triggerClick(closeBtn);
                                    return {success: true, method: 'extrabutton', tabNum: tabNum, id: closeBtn.id};
                                }
                            }
                        }
                    }

                    // 2. 활성 탭의 extrabutton 클릭 (폴백)
                    const activeTab = document.querySelector('[id*="tab_openList"][id*="tabbutton_"][class*="selected"]') ||
                                     document.querySelector('[id*="tab_openList"][id*="tabbutton_"]:last-child');
                    if (activeTab && activeTab.id) {
                        const match = activeTab.id.match(/tabbutton_(\d+)/);
                        if (match) {
                            const tabNum = match[1];
                            const closeBtn = document.querySelector(
                                '[id*="tab_openList.tabbutton_' + tabNum + '.extrabutton"]'
                            );
                            if (closeBtn && closeBtn.offsetParent !== null) {
                                triggerClick(closeBtn);
                                return {success: true, method: 'active_tab_extrabutton', tabNum: tabNum};
                            }
                        }
                    }

                    // 3. 모든 extrabutton 중 마지막 것 클릭 (최후 수단)
                    const allExtraButtons = document.querySelectorAll('[id*="tab_openList"][id*="extrabutton"]');
                    if (allExtraButtons.length > 0) {
                        const lastBtn = allExtraButtons[allExtraButtons.length - 1];
                        if (lastBtn.offsetParent !== null) {
                            triggerClick(lastBtn);
                            return {success: true, method: 'last_extrabutton', id: lastBtn.id};
                        }
                    }

                    return {success: false, reason: 'no_tab_close_button_found'};
                """)

                if result and result.get("success"):
                    time.sleep(SA_TAB_CLOSE_WAIT)

                    # 팝업이 떴을 수 있으므로 팝업 닫기 시도 (여러 번)
                    for _ in range(3):
                        self._close_any_popup(driver)
                        time.sleep(AFTER_ACTION_WAIT)

                    # 탭이 실제로 닫혔는지 확인
                    is_closed = driver.execute_script("""
                        // 중분류별 매출 탭이 더 이상 없는지 확인
                        const tabTexts = document.querySelectorAll('[id*="tab_openList"][id*="tabbuttonitemtext"]');
                        for (const el of tabTexts) {
                            if (el.innerText && el.innerText.includes('중분류별 매출')) {
                                return false;  // 아직 있음
                            }
                        }
                        return true;  // 없음 = 닫힘
                    """)

                    if is_closed:
                        logger.info(f"Sales menu tab closed (method: {result.get('method')})")
                        return True
                    else:
                        logger.warning(f"Tab click succeeded but tab still visible, retry {attempt+1}")
                else:
                    logger.warning(f"Tab close attempt {attempt+1} failed: {result}")

            except Exception as e:
                logger.error(f"Tab close error (attempt {attempt+1}): {e}")

            time.sleep(AFTER_ACTION_WAIT)

        # 최종 실패 시에도 계속 진행 (발주 메뉴에서 탭을 덮어쓸 수 있음)
        logger.warning("Could not close sales menu tab, continuing anyway")
        return False

    def close(self) -> None:
        """리소스 정리 (WebDriver 종료 및 로그인 상태 초기화)"""
        if self.analyzer:
            try:
                self.analyzer.close()
            except Exception as e:
                logger.warning(f"드라이버 종료 실패: {e}")
            self.analyzer = None
        self._is_logged_in = False

    def validate(self, data: List[Dict[str, Any]]) -> bool:
        """수집 데이터 유효성 검증

        Args:
            data: 수집된 매출 데이터 리스트

        Returns:
            필수 필드(ITEM_CD, MID_CD, MID_NM)가 존재하면 True
        """
        if not data:
            logger.warning("No data collected")
            return False

        required_fields = ["ITEM_CD", "MID_CD", "MID_NM"]

        for item in data[:5]:
            for field in required_fields:
                if field not in item or not item[field]:
                    logger.warning(f"Missing field: {field}")
                    return False

        logger.info(f"Validation passed: {len(data)} items")
        return True

    def run(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """수집 실행 및 결과 반환 (날씨 정보 포함)

        Args:
            target_date: 수집 대상 날짜 (YYYY-MM-DD)

        Returns:
            수집 결과 딕셔너리 (success, data, weather 등 포함)
        """
        result = super().run(target_date)

        if self.weather_data:
            result["weather"] = self.weather_data

        return result

    def collect_with_retry(
        self, target_date: Optional[str] = None, max_retries: int = 3
    ) -> Dict[str, Any]:
        """재시도 로직이 포함된 수집

        Args:
            target_date: 수집 대상 날짜 (YYYY-MM-DD)
            max_retries: 최대 재시도 횟수

        Returns:
            수집 결과 딕셔너리
        """
        for attempt in range(max_retries):
            result = self.run(target_date)

            if result["success"]:
                return result

            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * COLLECTION_RETRY_BASE_WAIT
                logger.warning(f"Retry {attempt + 1}/{max_retries} after {wait_time}s...")
                time.sleep(wait_time)

        return result


def run_collection(target_date: Optional[str] = None) -> Dict[str, Any]:
    """수집 실행 헬퍼 함수

    Args:
        target_date: 수집 대상 날짜 (YYYY-MM-DD)

    Returns:
        재시도 로직이 적용된 수집 결과 딕셔너리
    """
    collector = SalesCollector()
    return collector.collect_with_retry(target_date)


if __name__ == "__main__":
    result = run_collection()
    print(f"\nResult: {result['success']}, Items: {len(result['data'])}")
