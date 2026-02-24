"""
발주 실행 모듈
- BGF 시스템에서 실제 발주 수행
- 발주 > 단품별발주 메뉴 이용
- 상품별 발주 가능 요일에 맞춰 요일별 그룹핑 발주
"""

import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from pathlib import Path
from collections import defaultdict

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

from src.collectors.product_info_collector import ProductInfoCollector
from src.utils.timeout_handler import OperationTimer, wait_with_timeout, log_timeout_error, DEFAULT_TIMEOUT
from src.utils.logger import get_logger
from src.utils.popup_manager import auto_close_popups

logger = get_logger(__name__)

from src.settings.timing import (
    ORDER_MENU_AFTER_CLICK, ORDER_SUBMENU_AFTER_CLICK,
    ORDER_TAB_CLOSE_WAIT, ORDER_DATE_POPUP_INTERVAL,
    ORDER_DATE_POPUP_MAX_CHECKS, ORDER_AFTER_DATE_SELECT,
    ORDER_AFTER_POPUP_CLOSE, ORDER_INPUT_FIELD_INIT,
    ORDER_BEFORE_ENTER, ORDER_AFTER_ENTER, ORDER_AFTER_ENTER_EXTRA,
    ORDER_MULTIPLIER_AFTER_INPUT, ORDER_MULTIPLIER_AFTER_ENTER,
    ORDER_SAVE_AFTER_CLICK, ORDER_SAVE_ALERT_WAIT,
    ORDER_BETWEEN_ITEMS, ORDER_LAST_ITEM_EXTRA,
    ORDER_BEFORE_SAVE, ORDER_BETWEEN_DATES, ORDER_DATE_BUTTON_AFTER,
    ORDER_AFTER_DAY_SELECT, ORDER_CELL_ACTIVATE_WAIT,
    ORDER_NEW_ROW_DOM_WAIT, ORDER_CLOSE_POPUP_WAIT,
    ORDER_INPUT_RETRY_WAIT, ORDER_INPUT_VERIFY_WAIT,
    ORDER_SCREEN_CLEANUP_WAIT, ALERT_RETRY_DELAY,
    ALERT_CLEAR_MAX_ATTEMPTS, DOM_SETTLE_WAIT, AFTER_ACTION_WAIT,
    # Phase 1 최적화 상수
    ORDER_AFTER_ENTER_MIN, ORDER_AFTER_ENTER_MAX,
    ORDER_LOADING_CHECK_INTERVAL,
    ORDER_BETWEEN_ITEMS_FAST, ORDER_BETWEEN_ITEMS_SAFE,
)
from src.settings.constants import (
    MAX_ORDER_MULTIPLIER, DEFAULT_ORDERABLE_DAYS, WEEKDAY_KR_TO_NUM,
    # Phase 1 최적화 설정
    ORDER_INPUT_OPTIMIZATION_PHASE,
    DEBUG_SCREENSHOT_ENABLED, DEBUG_SCREENSHOT_ON_ERROR,
    DEBUG_SCREENSHOT_SAMPLE_RATE, DEBUG_SCREENSHOT_MAX_PER_SESSION,
)


class OrderExecutor:
    """발주 실행기"""

    # 메뉴 ID
    MENU_ORDER = "mainframe.HFrameSet00.VFrameSet00.TopFrame.form.div_topMenu.form.STBJ000_M0:icontext"
    # 단품별 발주 - 텍스트로 찾기
    SUBMENU_SINGLE_ORDER_TEXT = "단품별 발주"
    # 발주 프레임 ID (단품별 발주)
    ORDER_FRAME_ID = "STBJ030_M0"

    # 요일 매핑 (Python weekday -> 한글)
    WEEKDAY_MAP = {
        0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"
    }

    # 동적 프레임 탐색을 포함하는 공통 JS 코드 (stbjForm 획득)
    _FIND_ORDER_FORM_JS = """
        const app = nexacro.getApplication?.();
        const frameSet = app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet;
        let stbjForm = frameSet?.STBJ030_M0?.form;

        // 폴백: STBJ030_M0에 gdList가 없으면 다른 프레임에서 탐색
        if (!stbjForm?.div_workForm?.form?.div_work_01?.form?.gdList) {
            if (frameSet) {
                for (const key of Object.keys(frameSet)) {
                    try {
                        const candidate = frameSet[key]?.form?.div_workForm?.form?.div_work_01?.form;
                        if (candidate?.gdList?._binddataset) {
                            stbjForm = frameSet[key].form;
                            break;
                        }
                    } catch(e) {}
                }
            }
        }

        const workForm = stbjForm?.div_workForm?.form?.div_work_01?.form;
    """

    def __init__(self, driver: Any) -> None:
        """
        Args:
            driver: Selenium WebDriver (로그인된 상태)
        """
        self.driver = driver
        self._scripts_loaded = False
        self.product_collector = ProductInfoCollector(driver)
        self._last_selected_date = None  # select_order_day()에서 실제 선택된 날짜

    def _wait_for_dataset_ready(self, max_wait: int = 15, interval: float = 0.5) -> bool:
        """
        발주 그리드의 dataset 바인딩 대기

        프레임이 로드되어도 gdList._binddataset이 바인딩되기까지
        추가 시간이 필요함 (넥사크로 비동기 초기화)

        Args:
            max_wait: 최대 대기 횟수
            interval: 대기 간격 (초)

        Returns:
            dataset 준비 여부
        """
        for attempt in range(max_wait):
            result = self.driver.execute_script(self._FIND_ORDER_FORM_JS + """
                if (workForm?.gdList?._binddataset) {
                    return {ready: true, dsName: workForm.gdList._binddataset._name || 'unknown'};
                }
                if (workForm?.gdList) {
                    return {ready: false, reason: 'no_binddataset', hasGrid: true};
                }
                if (stbjForm) {
                    return {ready: false, reason: 'no_gdList', hasForm: true};
                }
                return {ready: false, reason: 'no_form'};
            """)
            if result and result.get('ready'):
                logger.info(f"dataset 바인딩 완료 ({(attempt + 1) * interval:.1f}초, ds={result.get('dsName', '?')})")
                return True
            if attempt > 0 and attempt % 5 == 0:
                reason = result.get('reason', 'unknown') if result else 'no_result'
                logger.debug(f"dataset 대기 중... ({(attempt + 1) * interval:.1f}초, {reason})")
            time.sleep(interval)

        reason = result.get('reason', 'unknown') if result else 'no_result'
        logger.warning(f"dataset 바인딩 대기 실패 ({max_wait * interval:.1f}초, 마지막: {reason})")
        return False

    def navigate_to_single_order(self) -> bool:
        """
        발주 > 단품별 발주 메뉴로 이동

        Returns:
            성공 여부
        """
        # 메뉴 이동 전 화면 정리
        from src.utils.popup_manager import close_all_popups, close_alerts
        logger.info("=" * 60)
        logger.info("단품별 발주 메뉴 이동 시작")
        logger.info("=" * 60)

        # Alert와 팝업 먼저 정리
        alert_count = close_alerts(self.driver, max_attempts=5, silent=False)
        popup_count = close_all_popups(self.driver, silent=False)
        if alert_count > 0 or popup_count > 0:
            logger.info(f"메뉴 이동 전 정리: Alert {alert_count}개, 팝업 {popup_count}개")

        logger.info("단품별 발주 메뉴로 이동 중...")

        with OperationTimer("단품별_발주_메뉴_이동", timeout=DEFAULT_TIMEOUT) as timer:
            try:
                # 0. 기존 탭이 열려있으면 닫기 (새로운 화면으로 시작하기 위해)
                self._close_existing_order_tab()
                timer.check("기존_탭_닫기")

                # 1. 발주 메뉴 클릭 (텍스트로 찾기)
                result = self._click_top_menu_by_text("발주")
                if not result:
                    # ID로 재시도
                    result = self._click_element_by_id(self.MENU_ORDER)
                if not result:
                    log_timeout_error("발주_메뉴_클릭", timer.elapsed(), {"status": "menu_click_failed"})
                    logger.error("발주 메뉴 클릭 실패")
                    return False

                timer.check("발주_메뉴_클릭")
                time.sleep(ORDER_MENU_AFTER_CLICK)

                # 2. 단품별 발주 서브메뉴 클릭 (텍스트로 찾기, 최대 3회 재시도)
                submenu_clicked = False
                for attempt in range(3):
                    result = self._click_element_by_text(self.SUBMENU_SINGLE_ORDER_TEXT)
                    if result:
                        submenu_clicked = True
                        break
                    logger.warning(f"서브메뉴 클릭 실패 (시도 {attempt + 1}/3)")
                    # 발주 메뉴를 다시 클릭하여 서브메뉴 표시
                    time.sleep(1.0)
                    self._click_top_menu_by_text("발주")
                    time.sleep(ORDER_MENU_AFTER_CLICK)

                if not submenu_clicked:
                    log_timeout_error("단품별_발주_서브메뉴_클릭", timer.elapsed(), {"status": "submenu_click_failed"})
                    logger.error("단품별 발주 메뉴 클릭 실패 (3회 재시도 후)")
                    return False

                timer.check("서브메뉴_클릭")
                time.sleep(ORDER_SUBMENU_AFTER_CLICK)

                # 3. 프레임 로딩 대기 (STBJ030_M0 프레임이 DOM에 존재할 때까지)
                from src.utils.nexacro_helpers import wait_for_frame
                frame_loaded = wait_for_frame(self.driver, self.ORDER_FRAME_ID, max_wait=20, interval=0.5)
                if not frame_loaded:
                    logger.error(f"프레임 로딩 실패: {self.ORDER_FRAME_ID} (10초 대기 후)")
                    return False
                timer.check("프레임_로딩")

                # 4. dataset 바인딩 대기 (gdList._binddataset이 준비될 때까지)
                dataset_ready = self._wait_for_dataset_ready(max_wait=15, interval=0.5)
                if not dataset_ready:
                    logger.warning("dataset 바인딩 대기 실패 (7.5초), 계속 진행...")
                timer.check("dataset_바인딩")

                # 5. 그리드 초기화 확인
                self._clear_order_grid()
                timer.check("그리드_초기화")

                logger.info(f"단품별 발주 메뉴 이동 완료 ({timer.elapsed():.1f}초)")
                return True

            except Exception as e:
                log_timeout_error("단품별_발주_메뉴_이동", timer.elapsed(), {
                    "error": str(e),
                    "error_type": type(e).__name__
                })
                logger.error(f"메뉴 이동 실패: {e}")
                return False

    def _click_top_menu_by_text(self, menu_text: str) -> bool:
        """상단 메뉴를 텍스트로 찾아서 클릭"""
        try:
            result = self.driver.execute_script("""
                // div_topMenu 내의 메뉴 아이템 찾기
                const menuItems = document.querySelectorAll('[id*="div_topMenu"] [id*=":icontext"], [id*="div_topMenu"] [id*=":text"]');
                const targetText = arguments[0];

                for (const el of menuItems) {
                    const text = (el.innerText || '').trim();
                    if (text === targetText && el.offsetParent !== null) {
                        el.scrollIntoView({block: 'center'});
                        const r = el.getBoundingClientRect();
                        const o = {
                            bubbles: true, cancelable: true, view: window,
                            clientX: r.left + r.width/2, clientY: r.top + r.height/2
                        };
                        el.dispatchEvent(new MouseEvent('mousedown', o));
                        el.dispatchEvent(new MouseEvent('mouseup', o));
                        el.dispatchEvent(new MouseEvent('click', o));
                        console.log('상단 메뉴 클릭:', text, el.id);
                        return true;
                    }
                }

                // 모든 요소에서 찾기 (fallback)
                const allElements = document.querySelectorAll('*');
                for (const el of allElements) {
                    const text = (el.innerText || '').trim();
                    if (text === targetText && el.id && el.id.includes('topMenu') && el.offsetParent !== null) {
                        el.scrollIntoView({block: 'center'});
                        const r = el.getBoundingClientRect();
                        const o = {
                            bubbles: true, cancelable: true, view: window,
                            clientX: r.left + r.width/2, clientY: r.top + r.height/2
                        };
                        el.dispatchEvent(new MouseEvent('mousedown', o));
                        el.dispatchEvent(new MouseEvent('mouseup', o));
                        el.dispatchEvent(new MouseEvent('click', o));
                        console.log('상단 메뉴 클릭 (fallback):', text, el.id);
                        return true;
                    }
                }

                console.warn('메뉴를 찾지 못함:', targetText);
                return false;
            """, menu_text)
            logger.info(f"상단 메뉴 '{menu_text}' 클릭 결과: {result}")
            return result
        except Exception as e:
            logger.error(f"상단 메뉴 클릭 오류: {e}")
            return False

    def _close_existing_order_tab(self) -> None:
        """기존 발주 관련 탭 닫기 (단품별발주, 발주현황 등)"""
        try:
            result = self.driver.execute_script("""
                // 발주 관련 모든 탭 닫기 (STBJ로 시작하는 프레임)
                const closeButtons = document.querySelectorAll('[id*="btn_topClose"]');
                let closedCount = 0;

                for (const btn of closeButtons) {
                    const btnId = btn.id || '';
                    // STBJ로 시작하는 탭 (발주 관련)
                    if ((btnId.includes('STBJ030') ||  // 단품별발주
                         btnId.includes('STBJ070') ||  // 발주현황
                         btnId.includes('STBJ010')) && // 카테고리발주
                        btn.offsetParent !== null) {
                        btn.click();
                        closedCount++;
                        console.log('[OK] 발주 탭 닫기:', btnId);
                    }
                }

                return {closed: closedCount};
            """)

            if result and result.get('closed', 0) > 0:
                logger.info(f"기존 발주 탭 {result['closed']}개 닫음")

            time.sleep(ORDER_TAB_CLOSE_WAIT)
        except Exception as e:
            logger.debug(f"탭 닫기 중 오류: {e}")

    def _clear_order_grid(self) -> None:
        """발주 그리드 초기화"""
        try:
            self.driver.execute_script("""
                try {
                    """ + self._FIND_ORDER_FORM_JS + """

                    if (workForm?.gdList?._binddataset) {
                        const ds = workForm.gdList._binddataset;
                        const rowCount = ds.getRowCount();

                        if (rowCount > 0) {
                            // 모든 행 삭제
                            for (let i = rowCount - 1; i >= 0; i--) {
                                ds.deleteRow(i);
                            }
                            console.log('[OK] 그리드 ' + rowCount + '행 삭제');
                        }
                    }
                } catch(e) {
                    console.log('[WARN] 그리드 초기화 실패:', e);
                }
            """)
        except Exception as e:
            logger.debug(f"팝업 닫기 중 오류: {e}")

    def _click_element_by_text(self, text: str) -> bool:
        """텍스트로 요소 찾아서 클릭"""
        try:
            result = self.driver.execute_script("""
                const targetText = arguments[0];
                const elements = document.querySelectorAll('[id*="pdiv_topMenu"] [id*=":text"]');

                for (const el of elements) {
                    const elText = (el.innerText || '').trim();
                    if (elText === targetText && el.offsetParent !== null) {
                        el.scrollIntoView({block: 'center'});
                        const r = el.getBoundingClientRect();
                        const o = {
                            bubbles: true, cancelable: true, view: window,
                            clientX: r.left + r.width/2, clientY: r.top + r.height/2
                        };
                        el.dispatchEvent(new MouseEvent('mousedown', o));
                        el.dispatchEvent(new MouseEvent('mouseup', o));
                        el.dispatchEvent(new MouseEvent('click', o));
                        console.log('클릭:', el.id, elText);
                        return true;
                    }
                }

                // 대안: 모든 요소에서 찾기
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const elText = (el.innerText || '').trim();
                    if (elText === targetText && el.offsetParent !== null && el.id) {
                        el.scrollIntoView({block: 'center'});
                        const r = el.getBoundingClientRect();
                        const o = {
                            bubbles: true, cancelable: true, view: window,
                            clientX: r.left + r.width/2, clientY: r.top + r.height/2
                        };
                        el.dispatchEvent(new MouseEvent('mousedown', o));
                        el.dispatchEvent(new MouseEvent('mouseup', o));
                        el.dispatchEvent(new MouseEvent('click', o));
                        return true;
                    }
                }

                return false;
            """, text)
            return result
        except Exception:
            return False

    def select_order_day(self, target_date: Optional[str] = None, select_first_if_not_found: bool = True) -> bool:
        """
        발주 요일 선택 (팝업에서 더블클릭)

        Args:
            target_date: 대상 날짜 (YYYY-MM-DD), 기본값: 오늘
            select_first_if_not_found: 대상 날짜가 없으면 첫 번째 날짜 선택

        Returns:
            성공 여부

        Note:
            - 발주 마감은 오전 10시
            - 10시 이후에는 오늘 날짜가 리스트에 없을 수 있음
            - 7시에 실행 권장
        """
        if target_date is None:
            target_date = datetime.now().strftime("%Y-%m-%d")

        # 날짜를 한글 형식으로 변환 (2026년 01월 26일)
        dt = datetime.strptime(target_date, "%Y-%m-%d")
        date_kr = f"{dt.year}년 {dt.month:02d}월 {dt.day:02d}일"
        weekday = dt.weekday()
        weekday_kr = self.WEEKDAY_MAP.get(weekday, "")

        logger.info(f"요일 선택 (더블클릭): {target_date} ({weekday_kr}요일)")
        logger.info(f"  -> 검색할 날짜: {date_kr}")

        # 전체 작업에 타임아웃 적용 (10초)
        start_time = time.time()

        def check_timeout(step_name: str) -> bool:
            """타임아웃 체크 - True면 타임아웃"""
            elapsed = time.time() - start_time
            if elapsed > DEFAULT_TIMEOUT:
                logger.warning(f"{step_name} 타임아웃 ({elapsed:.1f}초)")
                log_timeout_error(f"발주일_선택_{step_name}", elapsed, {
                    "target_date": target_date,
                    "status": "timeout"
                })
                return True
            return False

        try:
            # 1. 팝업 대기 및 확인 (1초 간격 체크, 최대 10초)
            popup_found = False
            for check_count in range(ORDER_DATE_POPUP_MAX_CHECKS):
                if check_timeout("팝업_대기"):
                    return False

                result = self.driver.execute_script("""
                    // 1. gridrow 확인 (가장 확실한 지표)
                    const rows = document.querySelectorAll('[id*="fn_initBalju"][id*="gridrow"]');
                    if (rows.length > 0) {
                        return {found: true, type: 'gridrow', count: rows.length};
                    }

                    // 2. grd_Result 그리드 확인 (offsetParent 무시)
                    const grid = document.querySelector('[id*="fn_initBalju"][id*="grd_Result"]');
                    if (grid) {
                        // offsetParent가 null이어도 요소가 존재하면 팝업 열림
                        return {found: true, type: 'grd_Result', visible: grid.offsetParent !== null};
                    }

                    // 3. fn_initBalju 팝업 존재 확인 (offsetParent 무시)
                    const popup = document.querySelector('[id*="fn_initBalju"]');
                    if (popup) {
                        return {found: true, type: 'fn_initBalju', visible: popup.offsetParent !== null};
                    }

                    return {found: false};
                """)

                if result and result.get('found'):
                    popup_found = True
                    break

                time.sleep(ORDER_DATE_POPUP_INTERVAL)

            if not popup_found:
                elapsed = time.time() - start_time
                log_timeout_error("발주일_선택_팝업_대기", elapsed, {
                    "target_date": target_date,
                    "status": "popup_not_found"
                })
                logger.error(f"팝업 감지 실패 ({elapsed:.1f}초)")
                return False

            elapsed = time.time() - start_time
            logger.info(f"팝업 감지됨 ({elapsed:.1f}초)")

            # 2. 타임아웃 체크
            if check_timeout("날짜_선택_시작"):
                return False

            # 3. 발주일 선택 팝업에서 해당 날짜 행 더블클릭
            result = self.driver.execute_script("""
                const targetDate = arguments[0];
                const selectFirstIfNotFound = arguments[1];

                // 더블클릭 함수
                function doubleClick(el) {
                    if (!el || el.offsetParent === null) return false;

                    el.scrollIntoView({block: 'center'});
                    const r = el.getBoundingClientRect();
                    const o = {
                        bubbles: true, cancelable: true, view: window,
                        clientX: r.left + r.width/2, clientY: r.top + r.height/2,
                        detail: 2
                    };

                    el.dispatchEvent(new MouseEvent('mousedown', o));
                    el.dispatchEvent(new MouseEvent('mouseup', o));
                    el.dispatchEvent(new MouseEvent('click', o));
                    el.dispatchEvent(new MouseEvent('mousedown', o));
                    el.dispatchEvent(new MouseEvent('mouseup', o));
                    el.dispatchEvent(new MouseEvent('click', o));
                    el.dispatchEvent(new MouseEvent('dblclick', {...o, detail: 2}));

                    return true;
                }

                // 팝업 찾기
                let popup = document.querySelector('[id*="fn_initBalju"]');
                if (!popup || popup.offsetParent === null) {
                    popup = document.querySelector('[id*="grd_Result"]')?.parentElement?.parentElement;
                }
                if (!popup) {
                    return { success: false, message: 'popup not found' };
                }

                // 그리드 행들 탐색
                const rows = popup.querySelectorAll('[id*="gridrow_"]');
                let firstRow = null;

                for (const row of rows) {
                    const rowText = (row.innerText || '');

                    if (!firstRow && row.id.includes('gridrow_0')) {
                        firstRow = { el: row, text: rowText.substring(0, 50) };
                    }

                    if (rowText.includes(targetDate)) {
                        if (doubleClick(row)) {
                            return { success: true, text: rowText.substring(0, 50), method: 'target_date' };
                        }
                    }
                }

                // 첫 번째 행 선택
                if (selectFirstIfNotFound && firstRow) {
                    if (doubleClick(firstRow.el)) {
                        return { success: true, text: firstRow.text, method: 'first_available' };
                    }
                }

                return { success: false, message: 'target date not found: ' + targetDate };
            """, date_kr, select_first_if_not_found)

            # 4. 타임아웃 체크
            if check_timeout("날짜_선택_완료"):
                return False

            if result and result.get('success'):
                method = result.get('method')
                row_text = result.get('text', '')

                # 팝업 행 텍스트에서 실제 날짜 추출 (예: "2026년 02월 20일" → "2026-02-20")
                self._last_selected_date = self._parse_date_from_popup_text(row_text)

                if method == 'first_available':
                    logger.warning(
                        f"대상 날짜 없음 -> 첫 번째 가능 날짜 선택"
                        f" (요청: {target_date}, 실제: {self._last_selected_date or 'N/A'})"
                    )
                else:
                    logger.info(f"날짜 더블클릭 성공: {method}")
                logger.info(f"선택된 행: {row_text}")

                time.sleep(ORDER_AFTER_DATE_SELECT)

                # 5. 타임아웃 체크
                if check_timeout("선택_버튼_클릭"):
                    return False

                # "선택" 버튼 클릭
                btn_result = self._click_popup_select_button()
                if btn_result:
                    logger.info("선택 버튼 클릭 -> 팝업 닫힘")
                else:
                    logger.warning("선택 버튼 클릭 실패 (더블클릭으로 선택 완료 가정)")

                time.sleep(ORDER_AFTER_POPUP_CLOSE)

                # 팝업 닫힌 후 잔여 Alert 처리
                self._clear_any_alerts(silent=True)

                # 6. 타임아웃 체크
                if check_timeout("입력_필드_초기화"):
                    return False

                # 입력 필드 초기화
                self._initialize_input_field()

                elapsed = time.time() - start_time
                logger.info(f"요일 선택 완료 ({elapsed:.1f}초)")
                return True
            else:
                msg = result.get('message') if result else 'JS execution returned None'
                elapsed = time.time() - start_time
                log_timeout_error("발주일_선택", elapsed, {
                    "target_date": target_date,
                    "status": "date_selection_failed",
                    "message": msg
                })
                logger.warning(f"날짜 찾기 실패: {msg}")
                return False

        except Exception as e:
            elapsed = time.time() - start_time
            log_timeout_error("발주일_선택", elapsed, {
                "target_date": target_date,
                "error": str(e),
                "error_type": type(e).__name__
            })
            logger.error(f"요일 선택 실패: {e}")
            return False

    @staticmethod
    def _parse_date_from_popup_text(text: str) -> Optional[str]:
        """팝업 행 텍스트에서 날짜 추출

        Args:
            text: 팝업 행 텍스트 (예: "2026년 02월 20일 (목)")

        Returns:
            "YYYY-MM-DD" 형식 또는 None
        """
        import re
        m = re.search(r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일', text or '')
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return None

    def _click_order_date_button(self) -> bool:
        """
        발주일자 버튼 클릭 (다른 날짜 선택용)
        저장 후 다른 날짜로 발주하려면 이 버튼을 클릭해야 날짜 팝업이 뜸
        """
        try:
            result = self.driver.execute_script("""
                // 발주일자 버튼 클릭
                // 동적 프레임 탐색으로 버튼 ID 획득
                const _app = nexacro.getApplication?.();
                const _frameSet = _app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet;
                let _frameId = 'STBJ030_M0';
                let _stbjForm = _frameSet?.STBJ030_M0?.form;

                if (!_stbjForm?.div_workForm?.form?.div_work_01?.form?.gdList) {
                    if (_frameSet) {
                        for (const key of Object.keys(_frameSet)) {
                            try {
                                const candidate = _frameSet[key]?.form?.div_workForm?.form?.div_work_01?.form;
                                if (candidate?.gdList) {
                                    _stbjForm = _frameSet[key].form;
                                    _frameId = key;
                                    break;
                                }
                            } catch(e) {}
                        }
                    }
                }

                const btnId = 'mainframe.HFrameSet00.VFrameSet00.FrameSet.' + _frameId + '.form.div_workForm.form.div_work_01.form.Div10.form.btn_stOrdYmd';
                const btn = document.getElementById(btnId);

                if (btn && btn.offsetParent !== null) {
                    btn.scrollIntoView({block: 'center'});
                    const r = btn.getBoundingClientRect();
                    const o = {
                        bubbles: true, cancelable: true, view: window,
                        clientX: r.left + r.width/2, clientY: r.top + r.height/2
                    };
                    btn.dispatchEvent(new MouseEvent('mousedown', o));
                    btn.dispatchEvent(new MouseEvent('mouseup', o));
                    btn.dispatchEvent(new MouseEvent('click', o));
                    return { success: true, method: 'btn_id' };
                }

                // 대안: btn_stOrdYmd가 포함된 버튼 찾기
                const allBtns = document.querySelectorAll('[id*="btn_stOrdYmd"]');
                for (const b of allBtns) {
                    if (b.offsetParent !== null) {
                        b.scrollIntoView({block: 'center'});
                        const r = b.getBoundingClientRect();
                        const o = {
                            bubbles: true, cancelable: true, view: window,
                            clientX: r.left + r.width/2, clientY: r.top + r.height/2
                        };
                        b.dispatchEvent(new MouseEvent('mousedown', o));
                        b.dispatchEvent(new MouseEvent('mouseup', o));
                        b.dispatchEvent(new MouseEvent('click', o));
                        return { success: true, method: 'btn_partial', id: b.id };
                    }
                }

                return { success: false, message: 'btn_stOrdYmd not found' };
            """)

            if result and result.get('success'):
                logger.info(f"발주일자 버튼 클릭: {result.get('method')}")
                return True
            else:
                msg = result.get('message') if result else 'JS returned None'
                logger.warning(f"발주일자 버튼 클릭 실패: {msg}")
                return False

        except Exception as e:
            logger.error(f"발주일자 버튼 클릭 오류: {e}")
            return False

    def _click_popup_select_button(self) -> bool:
        """팝업의 '선택' 버튼 클릭"""
        try:
            result = self.driver.execute_script("""
                // 클릭 함수
                function clickElement(el) {
                    if (!el || el.offsetParent === null) return false;
                    el.scrollIntoView({block: 'center'});
                    const r = el.getBoundingClientRect();
                    const o = {
                        bubbles: true, cancelable: true, view: window,
                        clientX: r.left + r.width/2, clientY: r.top + r.height/2
                    };
                    el.dispatchEvent(new MouseEvent('mousedown', o));
                    el.dispatchEvent(new MouseEvent('mouseup', o));
                    el.dispatchEvent(new MouseEvent('click', o));
                    return true;
                }

                // 1. 팝업 찾기 (다양한 패턴)
                let popup = document.querySelector('[id*="fn_initBalju"]');
                if (!popup || popup.offsetParent === null) {
                    popup = document.querySelector('[id*="initBalju"]');
                }
                if (!popup || popup.offsetParent === null) {
                    popup = document.querySelector('[id*="grd_Result"]')?.closest('[id*="popup"], [id*="Popup"], [id*="frame"], [id*="Frame"]');
                }

                // 2. Button44 (선택 버튼) 직접 찾기
                let btn = popup?.querySelector('[id*="Button44"]');
                if (btn && clickElement(btn)) return true;

                // 3. "선택" 텍스트가 있는 버튼 찾기 (팝업 내)
                if (popup) {
                    const allEls = popup.querySelectorAll('*');
                    for (const el of allEls) {
                        const text = (el.innerText || '').trim();
                        if (text === '선택' && el.offsetParent !== null) {
                            if (clickElement(el)) return true;
                        }
                    }
                }

                // 4. 전역에서 "선택" 버튼 찾기 (팝업을 못 찾았을 경우)
                const allBtns = document.querySelectorAll('[id*="Button"], [id*="btn"]');
                for (const btn of allBtns) {
                    const text = (btn.innerText || '').trim();
                    if (text === '선택' && btn.offsetParent !== null) {
                        if (clickElement(btn)) return true;
                    }
                }

                return false;
            """)
            return result
        except Exception:
            return False

    def input_product(self, item_cd: str, target_qty: int) -> Dict[str, Any]:
        """
        상품 입력 (그리드 상품명 셀에 직접 입력)
        - 상품코드 입력 후 그리드에서 실제 배수 단위를 읽어옴
        - 올바른 배수를 계산해서 입력

        Args:
            item_cd: 상품 코드
            target_qty: 목표 발주 수량 (배수 계산 전)

        Returns:
            결과 {success, message, actual_multiplier, order_unit_qty, ...}
        """
        logger.info(f"상품 입력: {item_cd}, 목표수량: {target_qty}")

        try:
            # 0. 먼저 혹시 남아있는 Alert 모두 처리 (이전 상품에서 발생한 Alert)
            alert_text = self._clear_any_alerts()
            if alert_text:
                time.sleep(AFTER_ACTION_WAIT)

            # 0.5. 항상 마지막 행의 상품명 셀(column 1)을 클릭하여 정확한 위치에 입력
            # (이전 배수 셀에 포커스가 남아있는 문제 방지)
            self._click_last_row_item_cell()
            time.sleep(ORDER_CELL_ACTIVATE_WAIT)

            # 1. 그리드 상품명 셀에 상품코드 입력
            logger.debug(f"[OPT] Phase={ORDER_INPUT_OPTIMIZATION_PHASE}, 상품={item_cd}")
            focus_check = None
            input_success = False
            method_used = 'unknown'

            if ORDER_INPUT_OPTIMIZATION_PHASE >= 2:
                # Phase 2: 최적화된 입력 방식
                logger.info(f"[Phase2] 상품코드 입력 시작: {item_cd}")
                input_result = self._input_product_code_optimized(item_cd)
                if not input_result.get('success'):
                    logger.warning("상품코드 입력 실패 (Phase 2)")
                    # 에러 시 스크린샷
                    self._save_debug_screenshot("error_input", item_cd=item_cd, force=True)
                    return {"success": False, "message": "상품코드 입력 실패"}
                # Phase 2 성공
                input_success = True
                method_used = 'phase2_optimized'
            else:
                # Phase 0/1: 기존 방식
                # _click_last_row_item_cell()에서 Shift+Tab으로 column 1에 포커스를 맞춤
                # 현재 포커스된 요소가 column 1인지 확인
                focus_check = self.driver.execute_script("""
                    const focusedEl = document.activeElement;
                    if (focusedEl && focusedEl.tagName === 'INPUT' && focusedEl.id.includes('celledit')) {
                        // 컬럼 1 (상품명)인지 확인 - cell_N_1 뒤에 숫자가 아닌 문자
                        const isCol1 = focusedEl.id.match(/cell_\\d+_1[^0-9]/) || focusedEl.id.match(/_1:celledit/);
                        return {
                            hasInput: true,
                        isCol1: isCol1 ? true : false,
                        elementId: focusedEl.id,
                        currentValue: focusedEl.value
                    };
                }
                return { hasInput: false, isCol1: false };
            """)

            if isinstance(focus_check, dict) and focus_check.get('hasInput') and focus_check.get('isCol1'):
                # Column 1에 포커스가 있음 - Selenium ActionChains로 실제 키 입력
                logger.info(f"Column 1 포커스 확인: {focus_check.get('elementId', '')[:40]}")

                # Ctrl+A로 전체 선택 후 삭제, 새 값 입력
                actions = ActionChains(self.driver)
                actions.key_down(Keys.CONTROL).send_keys('a').key_up(Keys.CONTROL)  # Ctrl+A
                actions.send_keys(Keys.DELETE)  # 삭제
                actions.send_keys(item_cd)  # 상품코드 입력
                actions.perform()

                time.sleep(DOM_SETTLE_WAIT)

                # 입력 확인
                verify = self.driver.execute_script("""
                    const el = document.activeElement;
                    return el && el.tagName === 'INPUT' ? el.value : '';
                """)
                if verify == item_cd:
                    input_success = True
                    method_used = 'selenium_keys'
                    logger.info("상품코드 입력: selenium_keys")
                else:
                    logger.warning(f"입력값 불일치: 입력={item_cd}, 확인={verify}")

            # Column 1에 포커스가 없거나 입력 실패 시 재시도
            if not input_success:
                for retry_attempt in range(3):
                    # _click_last_row_item_cell() 다시 호출
                    self._click_last_row_item_cell()
                    time.sleep(AFTER_ACTION_WAIT)

                    # 포커스 확인
                    focus_check = self.driver.execute_script("""
                        const focusedEl = document.activeElement;
                        if (focusedEl && focusedEl.tagName === 'INPUT' && focusedEl.id.includes('celledit')) {
                            const isCol1 = focusedEl.id.match(/cell_\\d+_1[^0-9]/) || focusedEl.id.match(/_1:celledit/);
                            return { hasInput: true, isCol1: isCol1 ? true : false, elementId: focusedEl.id };
                        }
                        return { hasInput: false, isCol1: false };
                    """)

                    if focus_check and focus_check.get('hasInput'):
                        # Input이 있으면 Selenium으로 입력 시도
                        actions = ActionChains(self.driver)
                        actions.key_down(Keys.CONTROL).send_keys('a').key_up(Keys.CONTROL)
                        actions.send_keys(Keys.DELETE)
                        actions.send_keys(item_cd)
                        actions.perform()

                        time.sleep(DOM_SETTLE_WAIT)

                        verify = self.driver.execute_script("""
                            const el = document.activeElement;
                            return el && el.tagName === 'INPUT' ? el.value : '';
                        """)
                        if verify == item_cd:
                            input_success = True
                            method_used = f'selenium_keys_retry{retry_attempt+1}'
                            logger.info(f"상품코드 입력: {method_used}")
                            break
                    else:
                        # Input이 없으면 직접 찾아서 JavaScript로 입력 시도
                        result = self.driver.execute_script("""
                            const itemCd = arguments[0];

                            function setInputValue(input, value) {
                                input.focus();
                                input.setSelectionRange(0, input.value.length);
                                input.value = '';
                                input.value = value;
                                input.dispatchEvent(new Event('input', { bubbles: true }));
                                input.dispatchEvent(new Event('change', { bubbles: true }));
                                return input.value === value;
                            }

                            // gdList 내에서 마지막 행의 상품명 컬럼(column 1) input 직접 찾기
                            """ + self._FIND_ORDER_FORM_JS + """

                            if (workForm?.gdList?._binddataset) {
                                const ds = workForm.gdList._binddataset;
                                const lastRow = ds.getRowCount() - 1;

                                if (lastRow >= 0) {
                                    // 마지막 행의 상품명 셀 (column 1) 직접 input 찾기
                                    const allInputs = document.querySelectorAll('[id*="gdList"][id*="celledit:input"]');
                                    let col1Input = null;
                                    const col1Pattern = new RegExp('cell_' + lastRow + '_1[^0-9]');
                                    for (const inp of allInputs) {
                                        if (inp.id.match(col1Pattern) && inp.offsetParent !== null) {
                                            col1Input = inp;
                                            break;
                                        }
                                    }

                                    if (col1Input) {
                                        const success = setInputValue(col1Input, itemCd);
                                        return { success: success, method: 'direct_col1_input' };
                                    }

                                    // 데이터셋에 직접 상품코드 설정
                                    try {
                                        ds.setColumn(lastRow, 'ITEM_CD', itemCd);
                                        return { success: true, method: 'dataset_set', row: lastRow };
                                    } catch(e) {}
                                }
                            }

                            return { success: false, message: 'column 1 input not found' };
                        """, item_cd)

                        if result and result.get('success'):
                            input_success = True
                            method_used = result.get('method', 'js_fallback')
                            logger.info(f"상품코드 입력: {method_used}")
                            break

                # 최종 결과 (Phase 0/1)
                result = {
                    'success': input_success,
                    'method': method_used
                }

                if not input_success:
                    logger.warning("상품코드 입력 실패")
                    return {"success": False, "message": "상품코드 입력 실패"}

            # ★ 상품코드 입력 후 스크린샷 (Phase 1: 조건부)
            self._save_debug_screenshot("debug_input", item_cd=item_cd)

            # 2. Enter 키로 상품 검색 실행 (Selenium ActionChains 사용)
            time.sleep(ORDER_BEFORE_ENTER)
            try:
                actions = ActionChains(self.driver)
                actions.send_keys(Keys.ENTER)
                actions.perform()
                logger.info("Enter 키 전송: selenium_keys")
            except Exception as e:
                logger.warning(f"Enter 키 전송 실패: {e}")

            # 3.5. 상품 정보 로딩 대기 (Phase 1: 동적 대기)
            if ORDER_INPUT_OPTIMIZATION_PHASE >= 1:
                # Phase 1: 동적 로딩 대기
                loading_result = self._wait_for_loading_complete(item_cd)
                if not loading_result.get('loaded'):
                    logger.warning(f"로딩 감지 실패, 폴백 사용 (대기: {loading_result.get('wait_time', 0):.2f}초)")
            else:
                # Phase 0: 기존 방식
                time.sleep(ORDER_AFTER_ENTER)

            # Enter 후 Alert 체크 (상품 없음, 발주 불가 등)
            alert_text = self._clear_any_alerts(silent=True)
            if alert_text:
                # 오류 Alert면 중단
                if '없' in alert_text or '불가' in alert_text or '올바른' in alert_text:
                    logger.warning(f"{alert_text}")
                    return {
                        "success": False,
                        "item_cd": item_cd,
                        "message": f"발주 불가: {alert_text}"
                    }
                else:
                    logger.warning(f"{alert_text}")

            # ★ Enter 후 그리드 상태 스크린샷 (Phase 1: 조건부)
            self._save_debug_screenshot("debug_after_enter", item_cd=item_cd)

            # 그리드에서 실제 배수(order_unit_qty) 읽기 - 해당 상품코드 행에서
            grid_data = self._read_product_info_from_grid(item_cd)

            if grid_data:
                actual_order_unit_qty = grid_data.get("order_unit_qty", 1) or 1
                item_nm = grid_data.get("item_nm", "")
                orderable_day = grid_data.get("orderable_day", "")
                actual_order_date = grid_data.get("actual_order_date", "")

                # DB에 저장
                self.product_collector.save_to_db(item_cd, grid_data)
                logger.info(f"그리드 상품명: {item_nm}, 배수단위: {actual_order_unit_qty}, 발주요일: {orderable_day}"
                            + (f", 실제발주일: {actual_order_date}" if actual_order_date else ""))
            else:
                # 그리드에서 못 읽으면 DB에서 조회
                db_data = self.product_collector.get_from_db(item_cd)
                if db_data and db_data.get("order_unit_qty"):
                    actual_order_unit_qty = db_data.get("order_unit_qty", 1)
                    logger.info(f"DB 배수단위: {actual_order_unit_qty} (그리드 읽기 실패, DB 사용)")
                else:
                    actual_order_unit_qty = 1
                    logger.warning("그리드/DB 데이터 없음, 배수단위=1 사용")

            # 올바른 배수 계산 (목표수량 / 배수단위, 최대 99)
            actual_order_unit_qty = max(1, actual_order_unit_qty)  # 0 방지
            actual_multiplier = min(MAX_ORDER_MULTIPLIER, max(1, (target_qty + actual_order_unit_qty - 1) // actual_order_unit_qty))
            actual_qty = actual_multiplier * actual_order_unit_qty
            logger.info(f"계산 목표: {target_qty}개 -> 배수: {actual_multiplier} x {actual_order_unit_qty} = {actual_qty}개")

            # 4. Alert 모두 처리 (최대 발주 수량 안내 등)
            # 배수 입력 전에 모든 Alert 정리
            remaining_alert = self._clear_any_alerts(silent=True)
            if remaining_alert:
                logger.warning(f"ALERT 정리: {remaining_alert[:40]}")
                # 발주 불가 조건 확인
                if '없' in remaining_alert or '불가' in remaining_alert:
                    return {
                        "success": False,
                        "item_cd": item_cd,
                        "multiplier": actual_multiplier,
                        "order_unit_qty": actual_order_unit_qty,
                        "message": f"발주 불가: {remaining_alert}"
                    }

            # 5. 발주 배수 입력
            if ORDER_INPUT_OPTIMIZATION_PHASE >= 2:
                # Phase 2: 최적화된 배수 입력
                qty_result = self._input_quantity_optimized(actual_multiplier)
            else:
                # Phase 0/1: 기존 방식
                qty_result = self._input_order_quantity(actual_multiplier)

            # 실제 발주일: 그리드 ORD_YMD > 팝업 선택 날짜 > None
            grid_order_date = grid_data.get("actual_order_date", "") if grid_data else ""

            return {
                "success": True,
                "item_cd": item_cd,
                "multiplier": actual_multiplier,
                "order_unit_qty": actual_order_unit_qty,
                "actual_qty": actual_qty,
                "actual_order_date": grid_order_date or self._last_selected_date or "",
                "qty_input": qty_result.get('success', False) if qty_result else False,
                "message": "OK"
            }

        except Exception as e:
            return {"success": False, "message": str(e)}

    def _handle_alert(self) -> Dict[str, Any]:
        """Alert 팝업 처리"""
        try:
            alert = self.driver.switch_to.alert
            alert_text = alert.text
            alert.accept()
            return {"handled": True, "alert_text": alert_text}
        except Exception:
            return {"handled": False, "alert_text": None}

    def _clear_any_alerts(self, silent: bool = False) -> Optional[str]:
        """
        남아있는 Alert 모두 처리

        Args:
            silent: True면 출력 안함

        Returns:
            마지막 Alert 텍스트 또는 None
        """
        last_alert_text = None
        for _ in range(10):  # 최대 10개 Alert 처리
            try:
                alert = self.driver.switch_to.alert
                alert_text = alert.text
                alert.accept()
                last_alert_text = alert_text
                if not silent:
                    logger.warning(f"ALERT 처리: {alert_text}")
                time.sleep(ALERT_RETRY_DELAY)
            except Exception:
                break  # Alert 없으면 종료
        return last_alert_text

    def _wait_for_loading_complete(
        self,
        item_cd: str,
        timeout: float = None
    ) -> Dict[str, Any]:
        """
        상품정보 로딩 완료를 동적으로 감지 (Phase 1 최적화)

        JavaScript로 데이터셋 상태를 폴링하여 로딩 완료 시점을 감지.
        고정 대기 시간 대신 실제 로딩 완료 시 즉시 진행.

        Args:
            item_cd: 상품코드
            timeout: 최대 대기 시간 (기본값: ORDER_AFTER_ENTER_MAX)

        Returns:
            {
                'loaded': bool,      # 로딩 완료 여부
                'wait_time': float,  # 실제 대기 시간
                'method': str        # 감지 방법
            }
        """
        if timeout is None:
            timeout = ORDER_AFTER_ENTER_MAX

        start_time = time.time()
        min_wait = ORDER_AFTER_ENTER_MIN  # 0.3초
        check_interval = ORDER_LOADING_CHECK_INTERVAL  # 0.1초

        # 최소 대기 시간 보장
        time.sleep(min_wait)
        elapsed = min_wait

        while elapsed < timeout:
            # JavaScript로 로딩 상태 확인
            result = self.driver.execute_script(self._FIND_ORDER_FORM_JS + """

                if (!workForm?.gdList?._binddataset) {
                    return {loaded: false, reason: 'no_dataset'};
                }

                const ds = workForm.gdList._binddataset;
                const lastRow = ds.getRowCount() - 1;

                if (lastRow < 0) {
                    return {loaded: false, reason: 'no_row'};
                }

                // 로딩 완료 조건:
                // 1. 상품명(ITEM_NM)이 존재
                // 2. 배수단위(ORDER_UNIT_QTY)가 0보다 큼
                const itemNm = ds.getColumn(lastRow, 'ITEM_NM') || '';
                const orderUnitQty = ds.getColumn(lastRow, 'ORDER_UNIT_QTY') || 0;

                if (itemNm.length > 0 && orderUnitQty > 0) {
                    return {
                        loaded: true,
                        item_nm: itemNm,
                        order_unit_qty: orderUnitQty
                    };
                }

                return {loaded: false, reason: 'not_ready'};
            """)

            if result and result.get('loaded'):
                elapsed = time.time() - start_time
                logger.debug(f"[TIMING] 로딩 완료 감지: {elapsed:.2f}초 (상품: {item_cd})")
                return {
                    'loaded': True,
                    'wait_time': elapsed,
                    'method': 'dynamic_detection',
                    'data': result
                }

            time.sleep(check_interval)
            elapsed = time.time() - start_time

        # 타임아웃 - 기존 방식으로 폴백
        elapsed = time.time() - start_time
        logger.warning(f"[TIMING] 로딩 감지 타임아웃: {elapsed:.2f}초 (상품: {item_cd})")
        return {
            'loaded': False,
            'wait_time': elapsed,
            'method': 'timeout_fallback'
        }

    def _save_debug_screenshot(
        self,
        stage: str,
        item_cd: str = "",
        force: bool = False
    ) -> Optional[str]:
        """
        조건부 스크린샷 저장 (Phase 1 최적화)

        프로덕션 환경에서는 스크린샷을 비활성화하여 I/O 시간 단축.
        에러 발생 시에만 디버깅용으로 저장.

        Args:
            stage: 'input', 'after_enter', 'error' 등
            item_cd: 상품코드 (선택)
            force: True면 설정 무시하고 무조건 저장

        Returns:
            스크린샷 경로 또는 None
        """
        # 강제 저장 (에러 등)
        if force or (stage == 'error' and DEBUG_SCREENSHOT_ON_ERROR):
            from src.utils.screenshot import save_screenshot
            return save_screenshot(self.driver, stage, item_cd=item_cd)

        # 전체 비활성화
        if not DEBUG_SCREENSHOT_ENABLED:
            return None

        # 세션당 최대 수 체크
        if not hasattr(self, '_screenshot_count'):
            self._screenshot_count = 0

        if self._screenshot_count >= DEBUG_SCREENSHOT_MAX_PER_SESSION:
            return None

        # 샘플링
        if DEBUG_SCREENSHOT_SAMPLE_RATE > 0:
            import random
            if random.random() > DEBUG_SCREENSHOT_SAMPLE_RATE:
                return None

        # 저장
        from src.utils.screenshot import save_screenshot
        path = save_screenshot(self.driver, stage, item_cd=item_cd)
        if path:
            self._screenshot_count += 1
            logger.debug(f"[SCREENSHOT] {stage}: {path} (count: {self._screenshot_count})")
        return path

    def _get_inter_item_delay(self, had_error: bool = False) -> float:
        """
        상황별 행 간 대기 시간 결정 (Phase 1 최적화)

        Args:
            had_error: 이전 상품 처리 중 에러 발생 여부

        Returns:
            대기 시간 (초)
        """
        if had_error:
            # 에러 발생 시 안전하게
            return ORDER_BETWEEN_ITEMS_SAFE  # 0.5초
        else:
            # 정상 처리 시 빠르게
            return ORDER_BETWEEN_ITEMS_FAST  # 0.2초

    def _activate_cell_js(self) -> Optional[Dict]:
        """발주 그리드 셀 활성화 JS 실행 (동적 프레임 탐색 포함)"""
        return self.driver.execute_script("""
            try {
                """ + self._FIND_ORDER_FORM_JS + """

                if (!workForm?.gdList?._binddataset) return {error: 'no dataset'};

                const ds = workForm.gdList._binddataset;
                const grid = workForm.gdList;
                let rowCount = ds.getRowCount();
                let targetRow = 0;
                let addedNewRow = false;

                if (rowCount === 0) {
                    ds.addRow();
                    targetRow = 0;
                    addedNewRow = true;
                } else {
                    const lastRow = rowCount - 1;
                    const itemCd = ds.getColumn(lastRow, 'ITEM_CD') || ds.getColumn(lastRow, 'PLU_CD') || '';

                    if (itemCd && itemCd.length > 0) {
                        ds.addRow();
                        targetRow = ds.getRowCount() - 1;
                        addedNewRow = true;
                    } else {
                        targetRow = lastRow;
                    }
                }

                ds.set_rowposition(targetRow);
                if (grid.setFocus) grid.setFocus();
                if (grid.setCellPos) grid.setCellPos(1);
                if (grid.showEditor) grid.showEditor(true);

                const cellId = 'gridrow_' + targetRow + '.cell_' + targetRow + '_1';
                const cell = document.querySelector('[id*="gdList"][id*="' + cellId + '"]');
                if (cell && cell.offsetParent !== null) {
                    const r = cell.getBoundingClientRect();
                    const o = {
                        bubbles: true, cancelable: true, view: window,
                        clientX: r.left + r.width/2, clientY: r.top + r.height/2
                    };
                    cell.dispatchEvent(new MouseEvent('mousedown', o));
                    cell.dispatchEvent(new MouseEvent('mouseup', o));
                    cell.dispatchEvent(new MouseEvent('click', o));
                    cell.dispatchEvent(new MouseEvent('dblclick', o));
                }

                return {success: true, targetRow: targetRow, addedNewRow: addedNewRow};
            } catch(e) {
                return {error: e.toString()};
            }
        """)

    def _click_last_row_item_cell(self) -> None:
        """
        마지막 행의 상품명 셀(column 1) 편집 모드 활성화

        단순화된 로직:
        1. 마지막 행 확인, 필요시 새 행 추가
        2. nexacro API로 행/셀 위치 설정 + 편집 모드 활성화
        3. no dataset 시 최대 10회 재시도 (0.5~1.0초 간격)
        """
        try:
            result = self._activate_cell_js()

            if not result or result.get('error'):
                error_msg = result.get('error', 'unknown') if result else 'no result'
                if error_msg == 'no dataset':
                    # dataset 미로드 → 재시도 (프레임 비동기 로딩 대응)
                    max_retries = 10
                    for retry in range(max_retries):
                        wait = 1.0 if retry < 3 else 0.5  # 처음 3회는 1초, 이후 0.5초
                        time.sleep(wait)
                        logger.info(f"[셀활성화] no dataset 재시도 {retry + 1}/{max_retries}")
                        result = self._activate_cell_js()
                        if result and not result.get('error'):
                            break
                    else:
                        logger.warning(f"셀 활성화 실패 ({max_retries}회 재시도 후): {error_msg}")
                        return
                else:
                    logger.warning(f"셀 활성화 실패: {result}")
                    return

            target_row = result.get('targetRow', 0)

            # 행 추가 시 DOM 업데이트 대기
            if result.get('addedNewRow'):
                time.sleep(ORDER_NEW_ROW_DOM_WAIT)

            # 팝업이 열렸으면 닫기
            self._close_product_search_popup()
            time.sleep(ORDER_CLOSE_POPUP_WAIT)

            # column 1 input에 포커스 (단순화)
            self.driver.execute_script("""
                const pattern = 'cell_' + arguments[0] + '_1';
                const inputs = document.querySelectorAll('[id*="gdList"][id*="' + pattern + '"][id*="celledit:input"]');
                if (inputs.length > 0) {
                    inputs[0].focus();
                }
            """, target_row)

        except Exception as e:
            logger.warning(f"_click_last_row_item_cell 오류: {e}")

    def _input_product_code_optimized(
        self,
        item_cd: str,
        max_retries: int = 2
    ) -> Dict[str, Any]:
        """
        최적화된 상품코드 입력 (Phase 2)

        전략:
        1차 시도: JavaScript 직접 입력 (빠름, 95% 성공)
        2차 시도: Selenium ActionChains (안정적, 5% 대체)

        Args:
            item_cd: 상품코드
            max_retries: 최대 재시도 횟수

        Returns:
            {
                'success': bool,
                'method': str,       # 'js_direct', 'dataset_direct', 'selenium_fallback'
                'attempts': int,     # 실제 시도 횟수
                'elapsed': float     # 소요 시간
            }
        """
        start_time = time.time()

        # 1차 시도: Selenium ActionChains (실제 키보드 이벤트 - 넥사크로 호환)
        # _click_last_row_item_cell()에서 이미 셀 포커스 완료
        try:
            actions = ActionChains(self.driver)
            actions.key_down(Keys.CONTROL).send_keys('a').key_up(Keys.CONTROL)
            actions.send_keys(Keys.DELETE)
            actions.send_keys(item_cd)
            actions.perform()

            time.sleep(DOM_SETTLE_WAIT)

            # 검증
            verify = self.driver.execute_script("""
                const el = document.activeElement;
                return el && el.tagName === 'INPUT' ? el.value : '';
            """)

            elapsed = time.time() - start_time

            if verify == item_cd:
                logger.info(f"[Phase2] 상품코드 입력: selenium_direct ({elapsed:.2f}초)")
                return {
                    'success': True,
                    'method': 'selenium_direct',
                    'attempts': 1,
                    'elapsed': elapsed
                }
            else:
                logger.warning(f"[Phase2] 입력값 불일치: 입력={item_cd}, 확인={verify}")
        except Exception as e:
            logger.warning(f"[Phase2] Selenium 입력 실패: {e}")

        # 2차 시도: JS로 셀 포커스 재설정 후 Selenium 재입력
        logger.debug("[Phase2] 1차 실패, 셀 포커스 재설정 후 재시도")
        time.sleep(ORDER_INPUT_RETRY_WAIT)

        try:
            # JS로 마지막 행 컬럼1 input을 찾아 포커스
            focus_result = self.driver.execute_script(self._FIND_ORDER_FORM_JS + """

                if (!workForm?.gdList?._binddataset) return {success: false};

                const ds = workForm.gdList._binddataset;
                const lastRow = ds.getRowCount() - 1;
                if (lastRow < 0) return {success: false};

                const allInputs = document.querySelectorAll('[id*="gdList"][id*="celledit:input"]');
                const col1Pattern = new RegExp('cell_' + lastRow + '_1[^0-9]');

                for (const inp of allInputs) {
                    if (inp.id.match(col1Pattern) && inp.offsetParent !== null) {
                        inp.focus();
                        inp.select();
                        return {success: true, elementId: inp.id};
                    }
                }
                return {success: false};
            """)

            if focus_result and focus_result.get('success'):
                actions = ActionChains(self.driver)
                actions.key_down(Keys.CONTROL).send_keys('a').key_up(Keys.CONTROL)
                actions.send_keys(Keys.DELETE)
                actions.send_keys(item_cd)
                actions.perform()

                time.sleep(DOM_SETTLE_WAIT)

                verify = self.driver.execute_script("""
                    const el = document.activeElement;
                    return el && el.tagName === 'INPUT' ? el.value : '';
                """)

                elapsed = time.time() - start_time

                if verify == item_cd:
                    logger.info(f"[Phase2] 상품코드 입력: selenium_retry ({elapsed:.2f}초)")
                    return {
                        'success': True,
                        'method': 'selenium_retry',
                        'attempts': 2,
                        'elapsed': elapsed
                    }
        except Exception as e:
            logger.warning(f"[Phase2] 재시도 실패: {e}")

        elapsed = time.time() - start_time
        return {
            'success': False,
            'method': 'failed',
            'attempts': 2,
            'elapsed': elapsed
        }

    def _input_quantity_optimized(
        self,
        multiplier: int
    ) -> Dict[str, Any]:
        """
        최적화된 배수 입력 (Phase 2)

        상품코드 Enter 후 커서가 배수 칸(column 2)에 자동 위치하므로
        Selenium ActionChains로 실제 키보드 이벤트를 전송하여 입력.
        (넥사크로는 JS DOM 조작으로 값 설정 시 내부 데이터에 반영되지 않음)

        Args:
            multiplier: 발주 배수

        Returns:
            {
                'success': bool,
                'method': str,
                'elapsed': float
            }
        """
        start_time = time.time()

        # 1차 시도: Selenium ActionChains (실제 키보드 이벤트 - 넥사크로 호환)
        try:
            actions = ActionChains(self.driver)
            # 기존 값 지우고 새 값 입력
            actions.key_down(Keys.CONTROL).send_keys('a').key_up(Keys.CONTROL)
            actions.send_keys(Keys.DELETE)
            actions.send_keys(str(multiplier))
            actions.perform()

            time.sleep(ORDER_MULTIPLIER_AFTER_INPUT)

            # Enter 키로 입력 확정 및 다음 행 생성
            actions = ActionChains(self.driver)
            actions.send_keys(Keys.ENTER)
            actions.perform()

            time.sleep(ORDER_MULTIPLIER_AFTER_ENTER)

            # Alert 처리 (최대 발주 가능수 안내 등)
            alert_text = self._clear_any_alerts(silent=True)
            if alert_text:
                logger.warning(f"배수 입력 후 Alert: {alert_text[:40]}")

            elapsed = time.time() - start_time
            logger.info(f"[Phase2] 배수 입력: selenium_keys (x{multiplier}, {elapsed:.2f}초)")
            return {
                'success': True,
                'method': 'selenium_keys',
                'elapsed': elapsed
            }
        except Exception as e:
            logger.warning(f"[Phase2] 배수 Selenium 입력 실패: {e}")

        # 2차 시도: TAB으로 배수 칸 이동 후 Selenium 입력
        try:
            actions = ActionChains(self.driver)
            actions.send_keys(Keys.TAB)
            actions.perform()
            time.sleep(0.1)

            actions = ActionChains(self.driver)
            actions.key_down(Keys.CONTROL).send_keys('a').key_up(Keys.CONTROL)
            actions.send_keys(Keys.DELETE)
            actions.send_keys(str(multiplier))
            actions.send_keys(Keys.ENTER)
            actions.perform()

            time.sleep(ORDER_MULTIPLIER_AFTER_ENTER)

            elapsed = time.time() - start_time
            logger.info(f"[Phase2] 배수 입력: selenium_tab_retry (x{multiplier}, {elapsed:.2f}초)")
            return {
                'success': True,
                'method': 'selenium_tab_retry',
                'elapsed': elapsed
            }
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"[Phase2] 배수 입력 실패: {e}")
            return {
                'success': False,
                'method': 'failed',
                'elapsed': elapsed
            }

    def _close_product_search_popup(self) -> None:
        """상품검색 팝업 닫기 (column 1 클릭 시 열릴 수 있음)"""
        try:
            self.driver.execute_script("""
                // 상품검색 팝업 (CallItemDetailPopup 등) 닫기
                const popups = document.querySelectorAll('[id*="CallItem"][id*="Popup"], [id*="fn_Item"]');
                for (const popup of popups) {
                    if (popup.offsetParent !== null) {
                        // 닫기 버튼 찾기
                        const closeBtn = popup.querySelector('[id*="btn_close"], [id*="Close"]');
                        if (closeBtn) {
                            closeBtn.click();
                            console.log('[OK] 상품검색 팝업 닫기');
                            return;
                        }
                        // ESC 키로 닫기
                        document.dispatchEvent(new KeyboardEvent('keydown', {
                            key: 'Escape', code: 'Escape', keyCode: 27, bubbles: true
                        }));
                    }
                }
            """)
        except Exception as e:
            logger.debug(f"Alert 정리 실패: {e}")

    def _initialize_input_field(self) -> None:
        """
        입력 필드 초기화 (날짜 선택 후 첫 번째 행의 상품명 셀 클릭)
        새 날짜 선택 후 그리드가 비어있을 때 사용
        """
        try:
            self.driver.execute_script("""
                try {
                    """ + self._FIND_ORDER_FORM_JS + """

                    if (!workForm?.gdList) return;

                    // 그리드의 첫 번째 행, 상품명 셀(column 1) 클릭
                    const cellId = 'gridrow_0.cell_0_1';
                    const cell = document.querySelector('[id*="gdList"][id*="' + cellId + '"]');

                    if (cell && cell.offsetParent !== null) {
                        cell.scrollIntoView({block: 'center'});
                        const r = cell.getBoundingClientRect();
                        const o = {
                            bubbles: true, cancelable: true, view: window,
                            clientX: r.left + r.width/2, clientY: r.top + r.height/2
                        };
                        cell.dispatchEvent(new MouseEvent('mousedown', o));
                        cell.dispatchEvent(new MouseEvent('mouseup', o));
                        cell.dispatchEvent(new MouseEvent('click', o));
                        console.log('[OK] 첫 번째 셀 클릭하여 입력 모드 활성화');
                    } else {
                        // 대안: 넥사크로 API로 셀 선택
                        if (workForm.gdList.setCellPos) {
                            workForm.gdList.setCellPos(0, 1);
                        }
                    }
                } catch(e) {
                    console.warn('입력 필드 초기화 실패:', e);
                }
            """)
            time.sleep(ORDER_INPUT_FIELD_INIT)
        except Exception as e:
            logger.debug(f"화면 정리 실패: {e}")

    def _activate_input_field(self) -> None:
        """
        입력 필드 활성화 (Alert 처리 후 입력 필드 재활성화)
        마지막 행의 상품명 셀 클릭하여 입력 모드 활성화
        """
        try:
            self.driver.execute_script("""
                try {
                    """ + self._FIND_ORDER_FORM_JS + """

                    if (!workForm?.gdList?._binddataset) return;

                    const ds = workForm.gdList._binddataset;
                    let lastRowIndex = ds.getRowCount() - 1;
                    if (lastRowIndex < 0) lastRowIndex = 0;

                    // 마지막 행의 상품명 셀 (column 1) 클릭
                    const cellId = 'gridrow_' + lastRowIndex + '.cell_' + lastRowIndex + '_1';
                    const cell = document.querySelector('[id*="gdList"][id*="' + cellId + '"]');

                    if (cell && cell.offsetParent !== null) {
                        const r = cell.getBoundingClientRect();
                        const o = {
                            bubbles: true, cancelable: true, view: window,
                            clientX: r.left + r.width/2, clientY: r.top + r.height/2
                        };
                        cell.dispatchEvent(new MouseEvent('mousedown', o));
                        cell.dispatchEvent(new MouseEvent('mouseup', o));
                        cell.dispatchEvent(new MouseEvent('click', o));
                    }
                } catch(e) {
                    console.warn('입력 필드 활성화 실패:', e);
                }
            """)
            time.sleep(ORDER_CELL_ACTIVATE_WAIT)
        except Exception as e:
            logger.debug(f"탭 닫기 실패: {e}")

    def _input_order_quantity(self, multiplier: int) -> Dict[str, Any]:
        """발주 배수 입력 - 상품코드 Enter 후 커서가 자동으로 배수 칸에 있음

        단순화된 로직:
        - DOM 위치 찾기 없이 바로 숫자 입력
        - 상품코드 입력 -> Enter -> (커서 자동 이동) -> 숫자 입력
        """
        try:
            # 커서가 이미 배수 입력 칸에 있으므로 바로 숫자 입력
            actions = ActionChains(self.driver)
            actions.send_keys(str(multiplier))  # 배수 입력
            actions.perform()

            input_result = {"success": True, "method": "direct_input", "multiplier": multiplier}
            logger.info(f"발주 배수 입력 완료: direct_input, 배수={multiplier}")

            # Enter 키로 입력 확정 및 다음 행 생성
            time.sleep(ORDER_MULTIPLIER_AFTER_INPUT)
            actions = ActionChains(self.driver)
            actions.send_keys(Keys.ENTER)
            actions.perform()

            # Alert 처리 (최대 발주 가능수 안내 등)
            time.sleep(ORDER_MULTIPLIER_AFTER_ENTER)
            alert_text = self._clear_any_alerts(silent=True)
            if alert_text:
                logger.warning(f"{alert_text[:40]}")

            return input_result

        except Exception as e:
            logger.warning(f"배수 입력 오류: {e}")
            return {"success": False, "message": str(e)}

    def confirm_order(self) -> Dict[str, Any]:
        """
        발주 확정 (저장 버튼 클릭)

        Returns:
            결과 {success, message}
        """
        logger.info("발주 저장...")

        try:
            # 저장 버튼 클릭
            result = self.driver.execute_script("""
                // 방법 1: "저장" 또는 "저 장" 텍스트가 있는 버튼 찾기
                const allElements = document.querySelectorAll('[id*="STBJ030"] [id*="btn"], [id*="Button"]');
                for (const el of allElements) {
                    const text = (el.innerText || '').trim().replace(/\\s+/g, '');
                    if (text === '저장' && el.offsetParent !== null) {
                        el.scrollIntoView({block: 'center'});
                        const r = el.getBoundingClientRect();
                        const o = {
                            bubbles: true, cancelable: true, view: window,
                            clientX: r.left + r.width/2, clientY: r.top + r.height/2
                        };
                        el.dispatchEvent(new MouseEvent('mousedown', o));
                        el.dispatchEvent(new MouseEvent('mouseup', o));
                        el.dispatchEvent(new MouseEvent('click', o));
                        return { success: true, method: 'text_search', elementId: el.id };
                    }
                }

                // 방법 2: 넥사크로 폼에서 저장 버튼 찾기
                """ + self._FIND_ORDER_FORM_JS + """

                if (stbjForm) {
                    // div_workBtn 내 버튼들
                    const workBtn = stbjForm.div_workBtn?.form;
                    if (workBtn) {
                        for (const [name, obj] of Object.entries(workBtn)) {
                            if (name.startsWith('btn_') && obj?.click) {
                                const btnText = obj.text || '';
                                if (btnText.includes('저장')) {
                                    obj.click();
                                    return { success: true, method: 'nexacro_btn', name: name };
                                }
                            }
                        }
                    }

                    // 직접 버튼 찾기
                    const btnSave = stbjForm.btn_save || stbjForm.btnSave;
                    if (btnSave?.click) {
                        btnSave.click();
                        return { success: true, method: 'btn_save' };
                    }
                }

                // 방법 3: DOM에서 저장 버튼 텍스트로 찾기
                const allBtns = document.querySelectorAll('*');
                for (const btn of allBtns) {
                    const text = (btn.innerText || '').trim();
                    if ((text === '저장' || text === '저 장') && btn.offsetParent !== null) {
                        const r = btn.getBoundingClientRect();
                        // 버튼 크기가 적절한지 확인 (너무 큰 컨테이너 제외)
                        if (r.width < 100 && r.height < 50) {
                            const o = {
                                bubbles: true, cancelable: true, view: window,
                                clientX: r.left + r.width/2, clientY: r.top + r.height/2
                            };
                            btn.dispatchEvent(new MouseEvent('mousedown', o));
                            btn.dispatchEvent(new MouseEvent('mouseup', o));
                            btn.dispatchEvent(new MouseEvent('click', o));
                            return { success: true, method: 'dom_search', elementId: btn.id || 'unknown' };
                        }
                    }
                }

                return { success: false, message: 'save button not found' };
            """)

            if not result or not result.get('success'):
                msg = result.get('message') if result else 'JS execution returned None'
                logger.warning(f"저장 버튼 찾기 실패: {msg}")
                return result if result else {"success": False, "message": msg}

            logger.info(f"저장 버튼 클릭: {result.get('method')}")

            # Alert 처리 (확인 팝업)
            time.sleep(ORDER_SAVE_AFTER_CLICK)
            for _ in range(3):
                try:
                    alert = self.driver.switch_to.alert
                    alert_text = alert.text
                    logger.warning(f"{alert_text}")
                    alert.accept()
                    time.sleep(ORDER_SAVE_ALERT_WAIT)
                except Exception:
                    break

            # 저장 완료 후 잔여 Alert 정리
            self._clear_any_alerts(silent=True)

            logger.info("발주 저장 완료")
            return result

        except Exception as e:
            return {"success": False, "message": str(e)}

    def execute_order(
        self,
        item_cd: str,
        qty: int,
        target_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        단일 상품 발주 실행

        Args:
            item_cd: 상품 코드
            qty: 발주 수량
            target_date: 발주 날짜 (YYYY-MM-DD)

        Returns:
            결과 {success, item_cd, qty, ...}
        """
        logger.info(f"발주 실행: {item_cd} x {qty}")

        # 1. 메뉴 이동
        if not self.navigate_to_single_order():
            return {"success": False, "message": "menu navigation failed"}

        # 2. 요일 선택
        if not self.select_order_day(target_date):
            logger.warning("요일 선택 실패, 계속 진행")

        # 3. 상품 입력
        input_result = self.input_product(item_cd, qty)
        if not input_result.get('success'):
            return input_result

        # 4. 발주 확정
        confirm_result = self.confirm_order()

        # 5. 발주 완료 후 팝업/Alert 정리 (홈화면 복귀 전)
        try:
            from src.utils.popup_manager import close_all_popups, close_alerts
            alert_count = close_alerts(self.driver, max_attempts=5, silent=True)
            popup_count = close_all_popups(self.driver, silent=True)
            if alert_count > 0 or popup_count > 0:
                logger.info(f"발주 완료 후 정리: Alert {alert_count}개, 팝업 {popup_count}개")
        except Exception as e:
            logger.debug(f"화면 정리 실패 (무시 가능): {e}")

        return {
            "success": confirm_result.get('success', False),
            "item_cd": item_cd,
            "qty": qty,
            "message": confirm_result.get('message', 'OK')
        }

    def get_next_orderable_date(self, orderable_days: str, from_date: Optional[datetime] = None) -> str:
        """
        가장 빠른 발주 가능일 계산

        Args:
            orderable_days: 발주 가능 요일 문자열 (예: "월화수", "일월화수목금토")
            from_date: 기준 날짜 (기본값: 오늘)

        Returns:
            가장 빠른 발주 가능일 (YYYY-MM-DD)
        """
        if from_date is None:
            from_date = datetime.now()

        # 요일 문자 -> 숫자 매핑 (월=0, 일=6)
        day_map = WEEKDAY_KR_TO_NUM

        # 발주 가능 요일 파싱
        available_days = set()
        for char in orderable_days:
            if char in day_map:
                available_days.add(day_map[char])

        if not available_days:
            # 발주 가능 요일 정보 없으면 내일 반환
            return (from_date + timedelta(days=1)).strftime("%Y-%m-%d")

        # 오늘부터 7일 내 가장 빠른 발주 가능일 찾기
        for i in range(1, 8):  # 내일부터 시작
            check_date = from_date + timedelta(days=i)
            if check_date.weekday() in available_days:
                return check_date.strftime("%Y-%m-%d")

        # 못 찾으면 내일 반환
        return (from_date + timedelta(days=1)).strftime("%Y-%m-%d")

    def group_orders_by_date(self, order_list: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """
        발주 목록을 발주 가능 날짜별로 그룹핑

        Args:
            order_list: 발주 목록 [{item_cd, final_order_qty, orderable_day, ...}, ...]

        Returns:
            날짜별 그룹핑된 발주 목록 {"2026-01-27": [{...}, ...], "2026-01-28": [...]}
        """
        from_date = datetime.now()
        grouped = defaultdict(list)

        for item in order_list:
            item_cd = item.get("item_cd")
            qty = item.get("final_order_qty", 0)

            if not item_cd or qty <= 0:
                continue

            # 발주 가능 요일 (기본값: 모든 요일)
            orderable_days = item.get("orderable_day", DEFAULT_ORDERABLE_DAYS)
            if not orderable_days:
                orderable_days = DEFAULT_ORDERABLE_DAYS

            # 가장 빠른 발주 가능일 계산
            order_date = self.get_next_orderable_date(orderable_days, from_date)

            grouped[order_date].append(item)

        # 날짜순 정렬하여 반환
        return dict(sorted(grouped.items()))

    def execute_orders(
        self,
        order_list: List[Dict[str, Any]],
        target_date: Optional[str] = None,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        여러 상품 발주 실행 (요일별 그룹핑)

        Args:
            order_list: 발주 목록 [{item_cd, final_order_qty, orderable_day, ...}, ...]
            target_date: 발주 날짜 (None이면 상품별 가장 빠른 발주 가능일 자동 선택)
            dry_run: True면 실제 발주 안함 (테스트용)

        Returns:
            결과 {success_count, fail_count, results: [...]}
        """
        logger.info("발주 실행 시작")
        logger.info(f"상품 수: {len(order_list)}개")
        logger.info(f"모드: {'테스트(dry_run)' if dry_run else '실제 발주'}")

        # 요일별 그룹핑
        if target_date:
            # 특정 날짜 지정 시 모든 상품을 해당 날짜로
            # datetime 객체면 문자열로 변환
            if hasattr(target_date, 'strftime'):
                target_date = target_date.strftime("%Y-%m-%d")
            grouped_orders = {target_date: order_list}
        else:
            # 상품별 발주 가능일에 맞춰 그룹핑
            grouped_orders = self.group_orders_by_date(order_list)

        logger.info("발주 일정")
        for date, items in grouped_orders.items():
            logger.info(f"  {date}: {len(items)}개 상품")

        results = []
        total_success = 0
        total_fail = 0

        # 날짜별로 순차 발주
        is_first_date = True  # 첫 번째 날짜 여부
        for order_date, items in grouped_orders.items():
            logger.info(f"[{order_date}] 발주 시작 ({len(items)}개 상품)")

            if is_first_date:
                # 첫 번째 날짜: 메뉴 이동 (자동으로 팝업 뜸)
                nav_success = False
                for nav_attempt in range(3):
                    if self.navigate_to_single_order():
                        nav_success = True
                        break
                    logger.warning(f"메뉴 이동 실패 (시도 {nav_attempt + 1}/3), 팝업 정리 후 재시도...")
                    self._clear_any_alerts(silent=True)
                    time.sleep(1)
                if not nav_success:
                    logger.error("메뉴 이동 3회 실패, 해당 날짜 건너뜀")
                    for item in items:
                        results.append({
                            "item_cd": item.get("item_cd"),
                            "qty": item.get("final_order_qty", 0),
                            "order_date": order_date,
                            "success": False,
                            "message": "menu navigation failed (3 retries)"
                        })
                        total_fail += 1
                    continue
                is_first_date = False
            else:
                # 두 번째 이후 날짜: 발주일자 버튼 클릭하여 팝업 열기
                logger.info("발주일자 버튼 클릭하여 다음 날짜 선택...")
                self._clear_any_alerts(silent=True)  # 버튼 클릭 전 Alert 정리
                if not self._click_order_date_button():
                    logger.error("발주일자 버튼 클릭 실패")
                    # 메뉴 재이동 시도 (최대 3회)
                    nav_success = False
                    for nav_attempt in range(3):
                        logger.info(f"메뉴 재이동 시도 ({nav_attempt + 1}/3)...")
                        self._clear_any_alerts(silent=True)
                        time.sleep(1)
                        if self.navigate_to_single_order():
                            nav_success = True
                            break
                        logger.warning(f"메뉴 재이동 실패 (시도 {nav_attempt + 1}/3)")
                    if not nav_success:
                        logger.error("메뉴 재이동 3회 실패, 해당 날짜 건너뜀")
                        for item in items:
                            results.append({
                                "item_cd": item.get("item_cd"),
                                "qty": item.get("final_order_qty", 0),
                                "order_date": order_date,
                                "success": False,
                                "message": "date button click failed (3 retries)"
                            })
                            total_fail += 1
                        continue

            time.sleep(ORDER_DATE_BUTTON_AFTER)

            # 2. 요일 선택
            if not self.select_order_day(order_date):
                logger.warning(f"{order_date} 요일 선택 실패, 계속 진행")

            time.sleep(ORDER_AFTER_DAY_SELECT)

            # 3. 상품들 입력
            date_success = 0
            date_fail = 0

            for i, item in enumerate(items):
                item_cd = item.get("item_cd")
                target_qty = item.get("final_order_qty", 0)
                item_nm = item.get("item_nm", item_cd)

                if not item_cd or target_qty <= 0:
                    continue

                logger.info(f"[{i+1}/{len(items)}] {item_nm} ({item_cd}): 목표 {target_qty}개")

                # 각 상품 처리 전 Alert 처리 (이전 상품에서 발생한 Alert)
                self._clear_any_alerts(silent=True)

                if dry_run:
                    results.append({
                        "item_cd": item_cd,
                        "target_qty": target_qty,
                        "order_date": order_date,
                        "success": True,
                        "dry_run": True
                    })
                    date_success += 1
                    continue

                # 상품 입력 (목표 수량 전달, 실제 배수는 그리드에서 읽어서 계산)
                input_result = self.input_product(item_cd, target_qty)

                if input_result and input_result.get('success'):
                    date_success += 1
                else:
                    date_fail += 1
                    msg = input_result.get('message', 'unknown') if input_result else 'input_product returned None'
                    logger.error(f"입력 실패: {msg}")

                # input_result가 None일 경우 기본값 사용
                if input_result:
                    # 실제 발주일: BGF 그리드/팝업에서 확인된 날짜 우선, 없으면 계산된 날짜
                    verified_order_date = input_result.get('actual_order_date', '') or order_date

                    if verified_order_date != order_date:
                        logger.warning(
                            f"발주일 불일치 감지: 예상={order_date}, "
                            f"실제={verified_order_date} ({item_cd})"
                        )

                    results.append({
                        "item_cd": item_cd,
                        "target_qty": target_qty,
                        "actual_qty": input_result.get('actual_qty', 0),
                        "multiplier": input_result.get('multiplier', 0),
                        "order_unit_qty": input_result.get('order_unit_qty', 1),
                        "order_date": verified_order_date,
                        "success": input_result.get('success', False),
                        "message": input_result.get('message', '')
                    })
                else:
                    results.append({
                        "item_cd": item_cd,
                        "target_qty": target_qty,
                        "actual_qty": 0,
                        "multiplier": 0,
                        "order_unit_qty": 1,
                        "order_date": order_date,
                        "success": False,
                        "message": "input_product returned None"
                    })

                # 다음 상품 입력 전 대기 (Phase 1: 에러 여부에 따라 조정)
                had_error = not input_result.get('success', False) if input_result else True
                if i == len(items) - 1:
                    time.sleep(ORDER_LAST_ITEM_EXTRA)  # 마지막 상품: 입력 완료 확인용 추가 대기
                else:
                    if ORDER_INPUT_OPTIMIZATION_PHASE >= 1:
                        # Phase 1: 상태 기반 대기
                        delay = self._get_inter_item_delay(had_error=had_error)
                        time.sleep(delay)
                    else:
                        # Phase 0: 기존 방식
                        time.sleep(ORDER_BETWEEN_ITEMS)

            # 4. 해당 날짜 발주 저장 (모든 상품 입력 완료 후)
            time.sleep(ORDER_BEFORE_SAVE)  # 추가 안정화 대기
            if not dry_run and date_success > 0:
                logger.info(f"[{order_date}] 발주 저장 중...")
                save_result = self.confirm_order()
                if save_result and save_result.get('success'):
                    logger.info(f"{order_date} 발주 저장 완료 ({date_success}건)")
                else:
                    logger.warning(f"{order_date} 발주 저장 실패")

            total_success += date_success
            total_fail += date_fail

            logger.info(f"[{order_date}] 완료: 성공 {date_success}건, 실패 {date_fail}건")

            # 다음 날짜 발주 전 대기
            time.sleep(ORDER_BETWEEN_DATES)

            # 다음 날짜 전환 전 잔여 Alert/팝업 정리
            self._clear_any_alerts(silent=True)

        # 전체 발주 완료 후 팝업/Alert 정리 (홈화면 복귀 전)
        logger.info("=" * 60)
        logger.info("전체 발주 완료 - 화면 정리 시작")
        try:
            from src.utils.popup_manager import close_all_popups, close_alerts
            alert_count = close_alerts(self.driver, max_attempts=10, silent=False)
            popup_count = close_all_popups(self.driver, silent=False)
            if alert_count > 0 or popup_count > 0:
                logger.info(f"[OK] Alert {alert_count}개, 팝업 {popup_count}개 정리 완료")
        except Exception as e:
            logger.warning(f"화면 정리 실패 (무시 가능): {e}")
        logger.info("=" * 60)

        logger.info("전체 발주 완료")
        logger.info(f"총 성공: {total_success}건")
        logger.info(f"총 실패: {total_fail}건")

        return {
            "success": total_fail == 0,
            "success_count": total_success,
            "fail_count": total_fail,
            "results": results,
            "grouped_by_date": {date: len(items) for date, items in grouped_orders.items()}
        }

    def _read_product_info_from_grid(self, item_cd: str) -> Optional[Dict[str, Any]]:
        """
        그리드에서 특정 상품코드의 정보 읽기

        Args:
            item_cd: 찾을 상품 코드

        Returns:
            상품 정보 dict 또는 None
        """
        try:
            result = self.driver.execute_script("""
                const targetItemCd = arguments[0];

                try {
                    """ + self._FIND_ORDER_FORM_JS + """

                    if (!workForm?.gdList?._binddataset) {
                        return { success: false, message: "dataset not found" };
                    }

                    const ds = workForm.gdList._binddataset;
                    const rows = ds.getRowCount();

                    if (rows === 0) {
                        return { success: false, message: "no rows" };
                    }

                    // 상품코드로 행 찾기 (역순으로 - 최근 입력한 것 우선)
                    let targetRow = -1;
                    for (let i = rows - 1; i >= 0; i--) {
                        const cd = ds.getColumn(i, 'ITEM_CD') || ds.getColumn(i, 'PLU_CD') || '';
                        if (cd === targetItemCd) {
                            targetRow = i;
                            break;
                        }
                    }

                    // 못 찾으면 마지막 행 사용
                    if (targetRow < 0) {
                        targetRow = rows - 1;
                    }

                    // 모든 컬럼 읽기
                    const data = {};
                    const colCount = ds.colcount || 50;
                    for (let i = 0; i < colCount; i++) {
                        try {
                            const colId = ds.getColID(i);
                            data[colId] = ds.getColumn(targetRow, colId);
                        } catch(e) {}
                    }

                    // 배수(IN_QTY) 컬럼 찾기 - 여러 가능한 이름 시도
                    let orderUnitQty = 1;
                    const qtyColNames = ['IN_QTY', 'ORD_UNIT_QTY', 'BAESOO', 'MUL_QTY', 'UNIT_QTY', 'MIN_ORD_QTY'];
                    for (const col of qtyColNames) {
                        if (data[col] && parseInt(data[col]) > 0) {
                            orderUnitQty = parseInt(data[col]);
                            break;
                        }
                    }

                    // 상품코드 확인
                    const foundItemCd = data.ITEM_CD || data.PLU_CD || '';
                    const foundItemNm = data.ITEM_NM || '';

                    return {
                        success: true,
                        row: targetRow,
                        matched: foundItemCd === targetItemCd,
                        data: {
                            item_cd: foundItemCd,
                            item_nm: foundItemNm,
                            expiration_days: parseInt(data.EXPIRE_DAY || data.VAL_TERM || data.FRESH_TERM) || null,
                            orderable_day: data.ORD_ADAY || data.ORD_DAY || '일월화수목금토',
                            orderable_status: data.ORD_STAT_NM || '',
                            order_unit_name: data.ORD_UNIT_NM || '',
                            order_unit_qty: orderUnitQty,
                            case_unit_qty: parseInt(data.CASE_QTY) || 1,
                            actual_order_date: data.ORD_YMD || data.DLV_YMD || ''
                        }
                    };

                } catch(e) {
                    return { success: false, message: e.toString() };
                }
            """, item_cd)

            if result and result.get("success"):
                data = result.get("data", {})
                matched = result.get("matched", False)

                if not matched:
                    logger.warning(f"상품코드 불일치! 요청: {item_cd}, 발견: {data.get('item_cd')}")

                return data

            return None

        except Exception as e:
            logger.error(f"그리드 읽기 오류: {e}")
            return None

    def _click_element_by_id(self, element_id: str) -> bool:
        """ID로 요소 클릭"""
        try:
            result = self.driver.execute_script("""
                const el = document.getElementById(arguments[0]);
                if (!el || el.offsetParent === null) return false;

                el.scrollIntoView({block: 'center'});
                const r = el.getBoundingClientRect();
                const o = {
                    bubbles: true, cancelable: true, view: window,
                    clientX: r.left + r.width/2, clientY: r.top + r.height/2
                };
                el.dispatchEvent(new MouseEvent('mousedown', o));
                el.dispatchEvent(new MouseEvent('mouseup', o));
                el.dispatchEvent(new MouseEvent('click', o));
                return true;
            """, element_id)
            return result
        except Exception:
            return False


# 테스트용
if __name__ == "__main__":
    print("OrderExecutor 모듈 로드됨")
    print("사용법: OrderExecutor(driver).execute_orders(order_list)")
