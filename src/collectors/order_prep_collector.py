"""
발주 준비 수집기 (Order Prep Collector)
- 단품별 발주 화면에서 상품별 발주/입고 이력 조회
- 미입고 수량 = 발주수량 - 입고수량
- 유통기한(expiration_days) 수집 및 DB 저장
- 발주 전 사전 조회하여 중복 발주 방지
- [v8] pending_order_collector.py 확장/리네이밍
"""

import calendar
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

from src.settings.ui_config import FRAME_IDS, DS_PATHS, MENU_TEXT, SUBMENU_TEXT
from src.settings.timing import (
    POPUP_WAIT_INTERVAL, POPUP_WAIT_MAX_CHECKS,
    PREP_CELL_ACTIVATE_WAIT, PREP_POPUP_CLOSE_WAIT, PREP_FOCUS_WAIT,
    PREP_INPUT_WAIT, PREP_SEARCH_LOAD, PREP_DATE_SELECT_WAIT,
    PREP_DATE_BUTTON_WAIT, PREP_MENU_CLOSE_WAIT, PREP_ITEM_QUERY_DELAY,
)
from src.utils.nexacro_helpers import navigate_menu, close_tab_by_frame_id
from src.infrastructure.database.repos import (
    RealtimeInventoryRepository,
    ProductDetailRepository,
    PromotionRepository,
    SalesRepository,
)
from src.prediction.promotion import PromotionManager
from src.utils.logger import get_logger
from src.utils.popup_manager import auto_close_popups, close_all_popups

logger = get_logger(__name__)


class OrderPrepCollector:
    """발주 준비 수집기 (미입고 + 유통기한)"""

    # 프레임 ID
    FRAME_ID = FRAME_IDS["SINGLE_ORDER"]

    # 데이터셋 경로
    DS_PATH = DS_PATHS["SINGLE_ORDER"]

    # 미입고 디버그 로그 디렉토리
    _LOG_DIR = Path(__file__).parent.parent.parent / "data" / "logs"

    def __init__(self, driver: Optional[Any] = None, save_to_db: bool = True, store_id: Optional[str] = None) -> None:
        """
        Args:
            driver: Selenium WebDriver (로그인된 상태)
            save_to_db: 조회 결과를 DB에 저장할지 여부 (기본 True)
            store_id: 매장 코드 (멀티 매장 필터링)
        """
        self.driver = driver
        self.store_id = store_id
        self._menu_navigated = False  # 메뉴 이동 여부
        self._date_selected = False   # 날짜 선택 여부
        self._save_to_db = save_to_db
        self._repo = RealtimeInventoryRepository(store_id=self.store_id) if save_to_db else None
        self._product_repo = ProductDetailRepository() if save_to_db else None
        self._promo_repo = PromotionRepository(store_id=self.store_id) if save_to_db else None
        self._promo_manager = PromotionManager(store_id=store_id) if save_to_db else None
        self._sales_repo = SalesRepository(store_id=self.store_id) if save_to_db else None
        self._pending_log_entries: List[Dict[str, Any]] = []
        self._comparison_stats: Dict[str, Any] = {
            'total': 0,
            'matches': 0,
            'differences': 0,
            'simple_higher': 0,
            'complex_higher': 0,
            'max_diff': 0,
            'total_diff': 0,
            'cross_pattern_cases': 0,
            'multiple_order_cases': 0,
        }

    def set_driver(self, driver: Any) -> None:
        """드라이버 설정 및 메뉴/날짜 선택 상태 초기화

        Args:
            driver: Selenium WebDriver 인스턴스
        """
        self.driver = driver
        self._menu_navigated = False
        self._date_selected = False

    def reset_navigation_state(self) -> None:
        """메뉴/날짜 선택 상태 초기화

        벌크 수집 시 메뉴 리프레시 전에 호출하여 상태를 리셋합니다.
        """
        self._menu_navigated = False
        self._date_selected = False

    def navigate_to_menu(self) -> bool:
        """
        발주 > 단품별 발주 메뉴로 이동

        Returns:
            성공 여부
        """
        if not self.driver:
            logger.error("드라이버가 설정되지 않음")
            return False

        if self._menu_navigated:
            logger.info("이미 메뉴 이동됨")
            return True

        logger.info("단품별 발주 메뉴 이동...")

        try:
            success = navigate_menu(
                self.driver,
                MENU_TEXT["ORDER"],
                SUBMENU_TEXT["SINGLE_ORDER"],
                self.FRAME_ID,
            )
            if success:
                self._menu_navigated = True
                logger.info("단품별 발주 메뉴 이동 완료")
            else:
                logger.error("메뉴 이동 실패")
            return success

        except Exception as e:
            logger.error(f"메뉴 이동 실패: {e}")
            return False

    def select_order_date(self, row_index: int = 0) -> bool:
        """
        발주일 선택 팝업에서 날짜 선택

        Args:
            row_index: 선택할 행 인덱스 (0 = 첫 번째 날짜)

        Returns:
            성공 여부
        """
        if self._date_selected:
            return True

        logger.info(f"발주일 선택 (행 {row_index})...")

        try:
            # 팝업 대기 (최대 10초)
            popup_found = False
            for _ in range(POPUP_WAIT_MAX_CHECKS):
                check = self.driver.execute_script("""
                    // gridrow가 보이면 팝업이 열린 것
                    const rows = document.querySelectorAll('[id*="fn_initBalju"][id*="gridrow_"]');
                    if (rows.length > 0) {
                        for (const row of rows) {
                            if (row.offsetParent !== null) return {found: true, count: rows.length};
                        }
                    }
                    // grd_Result 그리드 확인
                    const grid = document.querySelector('[id*="fn_initBalju"][id*="grd_Result"]');
                    if (grid && grid.offsetParent !== null) return {found: true, type: 'grid'};
                    return {found: false};
                """)
                if check and check.get('found'):
                    popup_found = True
                    break
                time.sleep(POPUP_WAIT_INTERVAL)

            if not popup_found:
                logger.error("날짜 선택 팝업 대기 타임아웃")
                return False

            logger.info("팝업 감지됨")

            # 더블클릭으로 날짜 선택 (offsetParent 체크 완화)
            result = self.driver.execute_script("""
                function doubleClick(el) {
                    if (!el) return false;
                    // offsetParent가 null이어도 rect가 있으면 클릭 시도
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 && r.height === 0) return false;

                    el.scrollIntoView({block: 'center'});
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

                // fn_initBalju 내 gridrow 찾기
                let rows = document.querySelectorAll('[id*="fn_initBalju"][id*="gridrow_"]');

                // 못 찾으면 전역에서 grd_Result 내 gridrow 찾기
                if (rows.length === 0) {
                    const grid = document.querySelector('[id*="grd_Result"]');
                    if (grid) {
                        rows = grid.querySelectorAll('[id*="gridrow_"]');
                    }
                }

                if (rows.length === 0) {
                    return {success: false, message: 'no gridrow found'};
                }

                // 지정된 행 또는 첫 번째 행 더블클릭
                const targetIdx = Math.min(arguments[0], rows.length - 1);
                if (doubleClick(rows[targetIdx])) {
                    return {success: true, row: targetIdx};
                }
                if (targetIdx !== 0 && doubleClick(rows[0])) {
                    return {success: true, row: 0, note: 'fallback'};
                }
                return {success: false, message: 'doubleClick failed'};
            """, row_index)

            if not result or not result.get('success'):
                logger.error(f"날짜 더블클릭 실패: {result}")
                return False

            logger.info(f"날짜 행 {result.get('row', 0)} 더블클릭 완료")
            time.sleep(PREP_DATE_SELECT_WAIT)

            # 선택 버튼 클릭 (offsetParent 체크 완화)
            btn_result = self.driver.execute_script("""
                function clickBtn(btn) {
                    if (!btn) return false;
                    const r = btn.getBoundingClientRect();
                    if (r.width === 0 && r.height === 0) return false;

                    btn.scrollIntoView({block: 'center'});
                    const o = {
                        bubbles: true, cancelable: true, view: window,
                        clientX: r.left + r.width/2, clientY: r.top + r.height/2
                    };
                    btn.dispatchEvent(new MouseEvent('mousedown', o));
                    btn.dispatchEvent(new MouseEvent('mouseup', o));
                    btn.dispatchEvent(new MouseEvent('click', o));
                    return true;
                }

                // 1. fn_initBalju 내 Button44 찾기
                let popup = document.querySelector('[id*="fn_initBalju"]');
                if (popup) {
                    const btn = popup.querySelector('[id*="Button44"]');
                    if (btn && clickBtn(btn)) return {success: true, method: 'popup_button44'};
                }

                // 2. 전역에서 Button44 찾기 (표시된 것만)
                const allBtn44 = document.querySelectorAll('[id*="Button44"]');
                for (const btn of allBtn44) {
                    const r = btn.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0 && clickBtn(btn)) {
                        return {success: true, method: 'global_button44'};
                    }
                }

                // 3. "선택" 텍스트 버튼 찾기
                const allBtns = document.querySelectorAll('[id*="Button"], [id*="btn"]');
                for (const btn of allBtns) {
                    const text = (btn.innerText || '').trim();
                    if (text === '선택') {
                        const r = btn.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0 && clickBtn(btn)) {
                            return {success: true, method: 'text_search'};
                        }
                    }
                }

                return {success: false, message: 'button not found'};
            """)

            if btn_result and btn_result.get('success'):
                logger.info(f"선택 버튼 클릭: {btn_result.get('method')}")
            else:
                logger.warning("선택 버튼 클릭 실패 (더블클릭으로 선택 완료 가정)")

            time.sleep(PREP_DATE_BUTTON_WAIT)
            self._date_selected = True
            logger.info("발주일 선택 완료")
            return True

        except Exception as e:
            logger.error(f"날짜 선택 실패: {e}")
            return False

    def _read_expiration_days_from_dom(self) -> Optional[int]:
        """
        DOM에서 유통기한(일) 읽기
        stExpireNm:text 요소에서 값 추출

        Returns:
            유통기한 (일) 또는 None
        """
        try:
            result = self.driver.execute_script("""
                // stExpireNm:text 요소에서 유통기한 읽기
                const possibleIds = [
                    'mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame.topFrameSearch.CallItemDetailPopup.form.divInfo02.form.stExpireNm:text',
                    'mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame.CallItemDetailPopup.form.divInfo02.form.stExpireNm:text',
                    'mainframe.HFrameSet00.VFrameSet00.FrameSet.STBJ030_M0.form.CallItemDetailPopup.form.divInfo02.form.stExpireNm:text'
                ];

                let expirationDays = null;

                // 1. 정확한 ID로 검색
                for (const id of possibleIds) {
                    const el = document.getElementById(id);
                    if (el && el.innerText) {
                        const val = parseInt(el.innerText.trim());
                        if (!isNaN(val) && val > 0) {
                            expirationDays = val;
                            break;
                        }
                    }
                }

                // 2. querySelector fallback
                if (!expirationDays) {
                    const el = document.querySelector('[id*="CallItemDetailPopup"][id*="stExpireNm"][id*=":text"]');
                    if (el && el.innerText) {
                        const val = parseInt(el.innerText.trim());
                        if (!isNaN(val) && val > 0) {
                            expirationDays = val;
                        }
                    }
                }

                // 3. 더 넓은 범위로 검색
                if (!expirationDays) {
                    const allElements = document.querySelectorAll('[id*="stExpireNm"]');
                    for (const el of allElements) {
                        if (el.innerText) {
                            const val = parseInt(el.innerText.trim());
                            if (!isNaN(val) && val > 0) {
                                expirationDays = val;
                                break;
                            }
                        }
                    }
                }

                return expirationDays;
            """)
            return result
        except Exception as e:
            logger.warning(f"유통기한 DOM 읽기 실패: {e}")
            return None

    def _read_expiration_from_dataset(self) -> Optional[int]:
        """
        데이터셋에서 유통기한 읽기 (fallback)
        dsItem 또는 dsItemDetail에서 EXPIRE_DAY 필드 조회

        Returns:
            유통기한 (일) 또는 None
        """
        try:
            result = self.driver.execute_script("""
                try {
                    const app = nexacro.getApplication();
                    const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[arguments[0]].form;
                    const dsPath = arguments[1];
                    const parts = dsPath.split('.');
                    let wf = form;
                    for (const p of parts) { wf = wf?.[p]; }

                    function getVal(ds, row, col) {
                        if (!ds || ds.getRowCount() === 0) return null;
                        let val = ds.getColumn(row, col);
                        if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                        return val;
                    }

                    // dsItem에서 EXPIRE_DAY 조회
                    if (wf.dsItem && wf.dsItem.getRowCount() > 0) {
                        const expireDay = getVal(wf.dsItem, 0, 'EXPIRE_DAY');
                        if (expireDay) return parseInt(expireDay) || null;
                    }

                    return null;
                } catch(e) {
                    return null;
                }
            """, self.FRAME_ID, self.DS_PATH)
            return result
        except Exception as e:
            logger.warning(f"데이터셋 유통기한 읽기 실패: {e}")
            return None

    def save_expiration_to_db(self, item_cd: str, expiration_days: int,
                              force_update: bool = False,
                              sell_price=None, margin_rate=None) -> bool:
        """
        유통기한 + 매가/이익율 DB 저장

        Args:
            item_cd: 상품코드
            expiration_days: 유통기한 (일)
            force_update: 기존 값이 있어도 강제 업데이트
            sell_price: 매가 (원, 문자열 가능 "3,500")
            margin_rate: 이익율 (%, 문자열 가능 "34.35")

        Returns:
            저장 성공 여부
        """
        if not self._product_repo:
            self._product_repo = ProductDetailRepository()

        try:
            # 기존 정보 조회
            existing = self._product_repo.get(item_cd)

            if existing:
                # 기존 유통기한이 있고 force_update가 아니면 유통기한은 유지
                exp_days = expiration_days if (expiration_days or force_update) else existing.get('expiration_days', 0)

                # 기존 값 업데이트
                info = {
                    'item_nm': existing.get('item_nm', ''),
                    'expiration_days': exp_days,
                    'orderable_day': existing.get('orderable_day', '일월화수목금토'),
                    'orderable_status': existing.get('orderable_status', ''),
                    'order_unit_qty': existing.get('order_unit_qty', 1),
                    'sell_price': sell_price,
                    'margin_rate': margin_rate,
                }
            else:
                # 신규 저장 (최소 정보)
                info = {
                    'item_nm': '',
                    'expiration_days': expiration_days,
                    'orderable_day': '일월화수목금토',
                    'orderable_status': '',
                    'order_unit_qty': 1,
                    'sell_price': sell_price,
                    'margin_rate': margin_rate,
                }

            self._product_repo.save(item_cd, info)
            return True
        except Exception as e:
            logger.error(f"유통기한/매가 DB 저장 실패: {e}")
            return False

    def collect_for_item(self, item_cd: str) -> Dict[str, Any]:
        """
        단일 상품 발주 준비 정보 수집

        Args:
            item_cd: 상품코드

        Returns:
            {
                'item_cd': str,           # 상품코드
                'item_nm': str,           # 상품명
                'pending_qty': int,       # 미입고 수량
                'expiration_days': int,   # 유통기한 (일)
                'current_stock': int,     # 현재 재고
                'order_unit_qty': int,    # 발주 단위 (입수)
                'success': bool           # 조회 성공 여부
            }
        """
        if not self.driver:
            return {'item_cd': item_cd, 'success': False}

        logger.info(f"상품 조회: {item_cd}")

        try:
            # 1. 마지막 행 상품명 셀 활성화
            result = self.driver.execute_script("""
                try {
                    const app = nexacro.getApplication?.();
                    const stbjForm = app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet?.[arguments[0]]?.form;
                    const workForm = stbjForm?.div_workForm?.form?.div_work_01?.form;

                    if (!workForm?.gdList?._binddataset) return {error: 'no dataset'};

                    const ds = workForm.gdList._binddataset;
                    const grid = workForm.gdList;
                    let rowCount = ds.getRowCount();
                    let targetRow = 0;

                    if (rowCount === 0) {
                        ds.addRow();
                        targetRow = 0;
                    } else {
                        const lastRow = rowCount - 1;
                        const existingCd = ds.getColumn(lastRow, 'ITEM_CD') || ds.getColumn(lastRow, 'PLU_CD') || '';
                        if (existingCd && existingCd.length > 0) {
                            ds.addRow();
                            targetRow = ds.getRowCount() - 1;
                        } else {
                            targetRow = lastRow;
                        }
                    }

                    ds.set_rowposition(targetRow);
                    if (grid.setFocus) grid.setFocus();
                    if (grid.setCellPos) grid.setCellPos(1);
                    if (grid.showEditor) grid.showEditor(true);

                    // column 1 더블클릭
                    const cellId = 'gridrow_' + targetRow + '.cell_' + targetRow + '_1';
                    const cell = document.querySelector('[id*="gdList"][id*="' + cellId + '"]');
                    if (cell) {
                        const r = cell.getBoundingClientRect();
                        const o = {bubbles: true, cancelable: true, view: window, clientX: r.left + r.width/2, clientY: r.top + r.height/2};
                        cell.dispatchEvent(new MouseEvent('mousedown', o));
                        cell.dispatchEvent(new MouseEvent('mouseup', o));
                        cell.dispatchEvent(new MouseEvent('click', o));
                        cell.dispatchEvent(new MouseEvent('dblclick', o));
                    }

                    return {success: true, targetRow: targetRow};
                } catch(e) {
                    return {error: e.toString()};
                }
            """, self.FRAME_ID)

            time.sleep(PREP_CELL_ACTIVATE_WAIT)

            # 상품검색 팝업 닫기
            self.driver.execute_script("""
                const popups = document.querySelectorAll('[id*="CallItem"][id*="Popup"], [id*="fn_Item"]');
                for (const popup of popups) {
                    if (popup.offsetParent !== null) {
                        const closeBtn = popup.querySelector('[id*="btn_close"], [id*="Close"]');
                        if (closeBtn) closeBtn.click();
                        else document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', keyCode: 27, bubbles: true}));
                    }
                }
            """)
            time.sleep(PREP_POPUP_CLOSE_WAIT)

            # column 1 input에 포커스
            target_row = result.get('targetRow', 0) if result else 0
            self.driver.execute_script("""
                const pattern = 'cell_' + arguments[0] + '_1';
                const inputs = document.querySelectorAll('[id*="gdList"][id*="' + pattern + '"][id*="celledit:input"]');
                if (inputs.length > 0) inputs[0].focus();
            """, target_row)
            time.sleep(PREP_FOCUS_WAIT)

            # 2. 상품코드 입력
            actions = ActionChains(self.driver)
            actions.key_down(Keys.CONTROL).send_keys('a').key_up(Keys.CONTROL)
            actions.send_keys(Keys.DELETE)
            actions.send_keys(item_cd)
            actions.perform()
            time.sleep(PREP_INPUT_WAIT)

            # 3. Enter 키로 상품 검색
            actions = ActionChains(self.driver)
            actions.send_keys(Keys.ENTER)
            actions.perform()
            time.sleep(PREP_SEARCH_LOAD)  # 상품 정보 로딩 대기

            # Alert 처리
            try:
                alert = self.driver.switch_to.alert
                alert_text = alert.text
                alert.accept()
                if '없' in alert_text or '불가' in alert_text:
                    logger.warning(f"{alert_text}")
                    return {'item_cd': item_cd, 'success': False}
            except Exception as e:
                # Alert이 없는 경우 NoAlertPresentException 발생 (정상 흐름)
                logger.debug(f"Alert 없음 (정상): {type(e).__name__}")

            # 4. 유통기한 수집 (DOM 우선, 데이터셋 fallback)
            expiration_days = self._read_expiration_days_from_dom()
            if not expiration_days:
                expiration_days = self._read_expiration_from_dataset()

            # 5. dsOrderSale, dsItem 데이터 조회
            data = self.driver.execute_script("""
                try {
                    const app = nexacro.getApplication();
                    const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[arguments[0]].form;
                    const dsPath = arguments[1];
                    const parts = dsPath.split('.');
                    let wf = form;
                    for (const p of parts) { wf = wf?.[p]; }

                    if (!wf) return {error: 'workForm not found'};

                    function getVal(ds, row, col) {
                        let val = ds.getColumn(row, col);
                        if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                        return val;
                    }

                    const result = {
                        itemInfo: null,
                        history: [],
                        weekDates: [],
                        promoInfo: null
                    };

                    // dsItem - 상품 정보
                    if (wf.dsItem && wf.dsItem.getRowCount() > 0) {
                        result.itemInfo = {
                            item_cd: getVal(wf.dsItem, 0, 'ITEM_CD'),
                            item_nm: getVal(wf.dsItem, 0, 'ITEM_NM'),
                            now_qty: parseInt(getVal(wf.dsItem, 0, 'NOW_QTY')) || 0,
                            order_unit_qty: parseInt(getVal(wf.dsItem, 0, 'ORD_UNIT_QTY')) || 1,
                            expire_day: parseInt(getVal(wf.dsItem, 0, 'EXPIRE_DAY')) || null
                        };
                    }

                    // dsWeek - 날짜 목록
                    if (wf.dsWeek) {
                        for (let i = 0; i < wf.dsWeek.getRowCount(); i++) {
                            result.weekDates.push(getVal(wf.dsWeek, i, 'ORD_YMD'));
                        }
                    }

                    // dsOrderSale - 발주/입고/판매/폐기 이력
                    if (wf.dsOrderSale) {
                        for (let i = 0; i < wf.dsOrderSale.getRowCount(); i++) {
                            result.history.push({
                                date: getVal(wf.dsOrderSale, i, 'ORD_YMD'),
                                item_cd: getVal(wf.dsOrderSale, i, 'ITEM_CD'),
                                ord_qty: parseInt(getVal(wf.dsOrderSale, i, 'ORD_QTY')) || 0,
                                buy_qty: parseInt(getVal(wf.dsOrderSale, i, 'BUY_QTY')) || 0,
                                sale_qty: parseInt(getVal(wf.dsOrderSale, i, 'SALE_QTY')) || 0,
                                disuse_qty: parseInt(getVal(wf.dsOrderSale, i, 'DISUSE_QTY')) || 0
                            });
                        }
                    }

                    // gdList 그리드에서 행사 정보 + 발주중지 여부 + 매가/이익율 조회
                    if (wf.gdList && wf.gdList._binddataset) {
                        const ds = wf.gdList._binddataset;
                        const lastRow = ds.getRowCount() - 1;
                        if (lastRow >= 0) {
                            // 행사 정보 (컬럼 인덱스 11: 당월행사, 12: 익월행사)
                            let curPromo = '';
                            let nextPromo = '';
                            try {
                                curPromo = ds.getColumn(lastRow, ds.getColID(11)) || '';
                            } catch(e2) {}
                            try {
                                nextPromo = ds.getColumn(lastRow, ds.getColID(12)) || '';
                            } catch(e2) {}
                            if (curPromo || nextPromo) {
                                result.promoInfo = {
                                    current_month_promo: curPromo,
                                    next_month_promo: nextPromo
                                };
                            }

                            // 발주중지(CUT) 상품 여부
                            try {
                                result.cutItemYn = ds.getColumn(lastRow, 'CUT_ITEM_YN') || '0';
                            } catch(e2) {
                                result.cutItemYn = '0';
                            }

                            // 매가 조회 (HQ_MAEGA_SET: 본부설정 매가, 정수)
                            let sellPrice = '';
                            try {
                                const v = ds.getColumn(lastRow, 'HQ_MAEGA_SET');
                                if (v != null) sellPrice = String(v);
                            } catch(e2) {}

                            // 이익율 조회 (PROFIT_RATE: 넥사크로 Decimal 타입, .hi에 실제 값)
                            let marginRate = '';
                            try {
                                const pr = ds.getColumn(lastRow, 'PROFIT_RATE');
                                if (pr != null) {
                                    if (typeof pr === 'object' && pr.hi != null) {
                                        marginRate = String(pr.hi);
                                    } else {
                                        marginRate = String(pr);
                                    }
                                }
                            } catch(e2) {}
                            result.priceInfo = { sell_price: sellPrice, margin_rate: marginRate };
                        }
                    }

                    return result;
                } catch(e) {
                    return {error: e.message};
                }
            """, self.FRAME_ID, self.DS_PATH)

            if not data or data.get('error'):
                logger.error(f"데이터 조회 실패: {data}")
                return {'item_cd': item_cd, 'success': False}

            # 6. 결과 정리
            item_info = data.get('itemInfo') or {}
            history = data.get('history', [])

            # 입수(단위수량) - 1배수당 실제 개수
            order_unit_qty = item_info.get('order_unit_qty', 1) or 1

            # 데이터셋에서 유통기한 가져오기 (DOM에서 못 가져왔을 경우)
            if not expiration_days:
                expiration_days = item_info.get('expire_day')

            # 미입고 수량 계산 (설정에 따라 단순/복잡/비교 방식 선택)
            from src.settings.constants import USE_SIMPLIFIED_PENDING, PENDING_COMPARISON_MODE

            if PENDING_COMPARISON_MODE:
                # Phase 3: 비교 모드 - 두 방식 동시 실행, 차이 로깅
                pending_qty, pending_detail = self._calculate_pending_with_comparison(
                    history, order_unit_qty, item_cd, item_info.get('item_nm', '')
                )
            elif USE_SIMPLIFIED_PENDING:
                # 단순화 방식 (item_cd 전달하여 1차/2차 배송 필터링)
                pending_qty = self._calculate_pending_simplified(history, order_unit_qty, item_cd=item_cd)
                pending_detail = []  # 단순화 방식에서는 디버그 로그 없음
            else:
                # 복잡한 방식 (기본값)
                pending_qty, pending_detail = self._calculate_pending_complex(history, order_unit_qty, item_cd)

            # 디버그 로그 수집 (복잡한 방식만 - 단순화 방식은 로그 없음)
            if not USE_SIMPLIFIED_PENDING and pending_detail:
                has_ord_no_buy = any(d['ord_qty'] > 0 and d['buy_qty'] == 0 for d in pending_detail)
                has_buy_no_ord = any(d['ord_qty'] == 0 and d['buy_qty'] > 0 for d in pending_detail)
                naive_pending = sum(d['diff'] for d in pending_detail)
                was_corrected = naive_pending != pending_qty
                if pending_qty > 0 or has_ord_no_buy or has_buy_no_ord or was_corrected:
                    self._pending_log_entries.append({
                        'item_cd': item_cd,
                        'item_nm': item_info.get('item_nm', ''),
                        'current_stock': item_info.get('now_qty', 0),
                        'order_unit_qty': order_unit_qty,
                        'pending_qty': pending_qty,
                        'naive_pending': naive_pending,
                        'was_corrected': was_corrected,
                        'history': pending_detail,
                        'has_ord_no_buy': has_ord_no_buy,
                        'has_buy_no_ord': has_buy_no_ord,
                    })

            # 행사 정보 추출
            promo_info = data.get('promoInfo') or {}
            current_month_promo = promo_info.get('current_month_promo', '')
            next_month_promo = promo_info.get('next_month_promo', '')

            # 발주중지(CUT) 상품 여부
            cut_item_yn_raw = data.get('cutItemYn', '0')
            is_cut_item = cut_item_yn_raw == '1'
            # CUT 감지 디버그: 원시값이 '0'이 아닌 경우도 로깅
            if cut_item_yn_raw and cut_item_yn_raw not in ('0', '1'):
                logger.warning(f"[CUT 감지] {item_cd}: CUT_ITEM_YN 비정상값='{cut_item_yn_raw}'")
            if is_cut_item:
                logger.info(f"[CUT 감지] {item_cd}: 발주중지 확인 (CUT_ITEM_YN='{cut_item_yn_raw}')")

            # 매가/이익율 추출
            price_info = data.get('priceInfo') or {}
            sell_price_raw = price_info.get('sell_price', '')
            margin_rate_raw = price_info.get('margin_rate', '')

            result = {
                'item_cd': item_info.get('item_cd', item_cd),
                'item_nm': item_info.get('item_nm', ''),
                'current_stock': item_info.get('now_qty', 0),
                'order_unit_qty': order_unit_qty,
                'pending_qty': pending_qty,
                'expiration_days': expiration_days,
                'current_month_promo': current_month_promo,
                'next_month_promo': next_month_promo,
                'is_cut_item': is_cut_item,
                'sell_price': sell_price_raw,
                'margin_rate': margin_rate_raw,
                'history': history,
                'week_dates': data.get('weekDates', []),
                'success': True
            }

            # 로그 출력 (행사 정보 + 발주중지 + 매가/이익율 포함)
            cut_text = " [발주중지]" if is_cut_item else ""
            promo_text = ""
            if current_month_promo or next_month_promo:
                promo_text = f", 당월={current_month_promo or '없음'}, 익월={next_month_promo or '없음'}"
            price_text = ""
            if sell_price_raw:
                price_text = f", 매가={sell_price_raw}"
            if margin_rate_raw:
                price_text += f", 이익율={margin_rate_raw}%"
            logger.info(f"{result['item_nm']}{cut_text}: 재고={result['current_stock']}, 미입고={pending_qty}개, 유통기한={expiration_days}일{promo_text}{price_text}")

            # DB에 저장
            if self._save_to_db:
                # 재고/미입고 저장
                if self._repo:
                    self._repo.save(
                        item_cd=result['item_cd'],
                        stock_qty=result['current_stock'],
                        pending_qty=pending_qty,
                        order_unit_qty=order_unit_qty,
                        is_available=True,
                        item_nm=result['item_nm'],
                        is_cut_item=is_cut_item,
                        store_id=self.store_id
                    )

                # 유통기한 + 매가/이익율 저장
                if self._product_repo and (expiration_days or sell_price_raw or margin_rate_raw):
                    self.save_expiration_to_db(
                        result['item_cd'], expiration_days or 0,
                        sell_price=sell_price_raw,
                        margin_rate=margin_rate_raw
                    )

                # buy_qty 역보정: dsOrderSale 입고 데이터로 daily_sales.buy_qty 업데이트
                if self._sales_repo:
                    try:
                        backfill_count = 0
                        for h in history:
                            h_buy_qty = h.get('buy_qty', 0)
                            h_date = h.get('date', '')
                            if h_buy_qty > 0 and h_date:
                                # ORD_YMD 형식: YYYYMMDD → YYYY-MM-DD
                                if len(h_date) == 8:
                                    formatted_date = f"{h_date[:4]}-{h_date[4:6]}-{h_date[6:8]}"
                                else:
                                    formatted_date = h_date
                                if self._sales_repo.update_buy_qty(
                                    sales_date=formatted_date,
                                    item_cd=item_cd,
                                    buy_qty=h_buy_qty
                                ):
                                    backfill_count += 1
                        if backfill_count > 0:
                            logger.info(f"[buy_qty 보정] {result['item_nm']}: {backfill_count}건 역보정 완료")
                    except Exception as e:
                        logger.warning(f"[buy_qty 보정 실패] {item_cd}: {e}")

                # 행사 정보 저장
                if (current_month_promo or next_month_promo) and self._promo_repo:
                    promo_result = self._promo_repo.save_monthly_promo(
                        item_cd=result['item_cd'],
                        item_nm=result['item_nm'],
                        current_month_promo=current_month_promo,
                        next_month_promo=next_month_promo
                    )
                    if promo_result.get('change_detected'):
                        change_type = promo_result['change_type']
                        logger.info(f"[행사변경] {result['item_nm']}: {current_month_promo or '없음'} → {next_month_promo or '없음'} ({change_type})")

                    # 행사/비행사 일평균 통계 갱신
                    if self._promo_manager:
                        try:
                            self._promo_manager.calculate_promotion_stats(result['item_cd'])
                        except Exception as e:
                            logger.warning(f"행사 통계 갱신 실패: {result['item_cd']} - {e}")

                # daily_sales.promo_type 역보정: 행사 기간의 daily_sales에 행사 타입 기록
                if self._sales_repo and current_month_promo:
                    try:
                        today = datetime.now()
                        # 1일/15일 주기: 현재 행사 기간 계산
                        if today.day <= 15:
                            period_start = today.strftime('%Y-%m-01')
                            period_end = today.strftime('%Y-%m-15')
                        else:
                            period_start = today.strftime('%Y-%m-16')
                            last_day = calendar.monthrange(today.year, today.month)[1]
                            period_end = today.strftime(f'%Y-%m-{last_day:02d}')

                        updated = self._sales_repo.update_promo_type(
                            item_cd=item_cd,
                            promo_type=current_month_promo,
                            start_date=period_start,
                            end_date=period_end
                        )
                        if updated > 0:
                            logger.info(f"[promo_type 보정] {result['item_nm']}: {current_month_promo} ({period_start}~{period_end}, {updated}건)")
                    except Exception as e:
                        logger.warning(f"[promo_type 보정 실패] {item_cd}: {e}")

            # 상품 조회 완료 후 남아있는 팝업 정리
            popup_count = close_all_popups(self.driver, silent=False)
            if popup_count > 0:
                logger.info(f"상품 조회 후 팝업 {popup_count}개 정리")

            return result

        except Exception as e:
            logger.error(f"상품 조회 실패: {e}")
            # 조회 실패 시에도 팝업 정리
            popup_count = close_all_popups(self.driver, silent=False)
            if popup_count > 0:
                logger.info(f"조회 실패 후 팝업 {popup_count}개 정리")
            # 조회 실패 시 미취급으로 표시
            if self._save_to_db and self._repo:
                self._repo.mark_unavailable(item_cd, store_id=self.store_id)
            return {'item_cd': item_cd, 'success': False}

    def collect_for_items(self, item_codes: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        여러 상품 일괄 수집

        Args:
            item_codes: 상품코드 목록

        Returns:
            {item_cd: {...}, ...}
        """
        if not self.driver:
            return {}

        # 메뉴 이동 및 날짜 선택
        if not self._menu_navigated:
            if not self.navigate_to_menu():
                return {}

        if not self._date_selected:
            if not self.select_order_date():
                return {}

        self._pending_log_entries = []  # 로그 초기화
        # 비교 통계 초기화
        self._comparison_stats = {
            'total': 0, 'matches': 0, 'differences': 0,
            'simple_higher': 0, 'complex_higher': 0,
            'max_diff': 0, 'total_diff': 0,
            'cross_pattern_cases': 0, 'multiple_order_cases': 0,
        }

        results = {}
        for item_cd in item_codes:
            result = self.collect_for_item(item_cd)
            results[item_cd] = result
            time.sleep(PREP_ITEM_QUERY_DELAY)  # 요청 간 간격

        # 미입고 디버그 로그 저장
        self._write_pending_debug_log()

        # 비교 통계 로그 저장 (비교 모드인 경우)
        from src.settings.constants import PENDING_COMPARISON_MODE
        if PENDING_COMPARISON_MODE:
            self._write_comparison_log()

        return results

    # ============================================================
    # 미입고 디버그 로그
    # ============================================================

    def _write_comparison_log(self) -> None:
        """비교 모드 통계를 파일로 저장"""
        stats = self._comparison_stats
        if stats['total'] == 0:
            return

        self._LOG_DIR.mkdir(parents=True, exist_ok=True)
        today_str = datetime.now().strftime("%Y-%m-%d")
        log_path = self._LOG_DIR / f"pending_comparison_{today_str}.txt"

        now_str = datetime.now().strftime("%H:%M:%S")
        weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][datetime.now().weekday()]

        lines = []
        lines.append("=" * 72)
        lines.append("미입고 계산 방식 비교 로그 (Phase 3)")
        lines.append(f"날짜: {today_str} ({weekday_kr})")
        lines.append(f"생성 시각: {now_str}")
        lines.append("=" * 72)
        lines.append("")

        # 요약 통계
        lines.append("[전체 통계]")
        lines.append("-" * 72)
        lines.append(f"  조회 상품 수: {stats['total']}개")
        lines.append(f"  결과 일치: {stats['matches']}개 ({stats['matches']/stats['total']*100:.1f}%)")
        lines.append(f"  결과 불일치: {stats['differences']}개 ({stats['differences']/stats['total']*100:.1f}%)")
        lines.append("")

        if stats['differences'] > 0:
            lines.append("[차이 분석]")
            lines.append("-" * 72)
            lines.append(f"  복잡>단순: {stats['complex_higher']}건")
            lines.append(f"  단순>복잡: {stats['simple_higher']}건")
            lines.append(f"  최대 차이: {stats['max_diff']}개")
            lines.append(f"  평균 차이: {stats['total_diff']/stats['differences']:.1f}개")
            lines.append("")

            lines.append("[패턴 분석]")
            lines.append("-" * 72)
            lines.append(f"  교차날짜 패턴: {stats['cross_pattern_cases']}건")
            lines.append(f"  다중 발주: {stats['multiple_order_cases']}건")
            lines.append("")

        # 권장 사항
        lines.append("[권장 사항]")
        lines.append("-" * 72)
        match_rate = stats['matches'] / stats['total'] * 100 if stats['total'] > 0 else 0

        if match_rate >= 90:
            lines.append("  ✅ 일치율 90% 이상 → 단순화 방식 적용 가능")
            lines.append("  → constants.py에서 USE_SIMPLIFIED_PENDING = True 설정")
        elif match_rate >= 80:
            lines.append("  ⚠️ 일치율 80~90% → 추가 모니터링 필요")
            lines.append("  → 1주일 더 비교 후 재평가")
        else:
            lines.append("  ❌ 일치율 80% 미만 → 단순화 방식 보류")
            lines.append("  → 복잡한 방식 유지 또는 lookback_days 조정")

        lines.append("")

        if stats['differences'] > 0 and stats['total_diff'] / stats['differences'] > 5:
            lines.append("  ⚠️ 평균 차이 5개 초과 → 영향도 큼")
            lines.append("  → 발주량 변화가 크므로 신중히 결정")

        lines.append("")
        lines.append("=" * 72)
        lines.append("비교 로그 끝")
        lines.append("=" * 72)
        lines.append("")

        try:
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            logger.info(f"비교 통계 저장: {log_path} (조회 {stats['total']}건, 차이 {stats['differences']}건)")
        except Exception as e:
            logger.warning(f"비교 통계 저장 실패: {e}")

    def _write_pending_debug_log(self) -> None:
        """dsOrderSale raw 데이터 + 미입고 계산 과정을 파일로 저장"""
        if not self._pending_log_entries:
            return

        self._LOG_DIR.mkdir(parents=True, exist_ok=True)
        today_str = datetime.now().strftime("%Y-%m-%d")
        log_path = self._LOG_DIR / f"pending_debug_{today_str}.txt"

        now_str = datetime.now().strftime("%H:%M:%S")
        weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][datetime.now().weekday()]

        lines = []
        lines.append("=" * 72)
        lines.append("dsOrderSale 미입고 계산 디버그 로그")
        lines.append(f"날짜: {today_str} ({weekday_kr})")
        lines.append(f"생성 시각: {now_str}")
        lines.append("=" * 72)
        lines.append("")

        # 요약
        total = len(self._pending_log_entries)
        with_pending = sum(1 for e in self._pending_log_entries if e['pending_qty'] > 0)
        cross_date = sum(1 for e in self._pending_log_entries
                         if e['has_ord_no_buy'] and e['has_buy_no_ord'])
        ord_no_buy = sum(1 for e in self._pending_log_entries if e['has_ord_no_buy'])
        buy_no_ord = sum(1 for e in self._pending_log_entries if e['has_buy_no_ord'])
        corrected_count = sum(1 for e in self._pending_log_entries if e.get('was_corrected'))
        total_naive = sum(e.get('naive_pending', e['pending_qty']) for e in self._pending_log_entries)
        total_corrected = sum(e['pending_qty'] for e in self._pending_log_entries)

        lines.append("[요약]")
        lines.append("-" * 72)
        lines.append(f"  로깅 대상: {total}개 (미입고>0 또는 교차날짜 패턴)")
        lines.append(f"  미입고 > 0: {with_pending}개")
        lines.append(f"  발주O 입고X 행 있음: {ord_no_buy}개")
        lines.append(f"  발주X 입고O 행 있음: {buy_no_ord}개")
        lines.append(f"  교차날짜 패턴 (양쪽 모두): {cross_date}개")
        lines.append(f"  ★ 교차날짜 보정 적용: {corrected_count}개 (행별합={total_naive} → 총량={total_corrected}, 차이={total_naive - total_corrected})")
        lines.append("")

        # 교차날짜 패턴 상품 (검증 핵심)
        cross_entries = [e for e in self._pending_log_entries
                         if e['has_ord_no_buy'] and e['has_buy_no_ord']]
        if cross_entries:
            lines.append("[★ 교차날짜 패턴 상품 (발주일 ≠ 입고일)]")
            lines.append("-" * 72)
            for entry in cross_entries:
                self._format_entry(lines, entry, highlight=True)
            lines.append("")

        # 미입고 > 0 상품
        pending_entries = [e for e in self._pending_log_entries
                           if e['pending_qty'] > 0
                           and e not in cross_entries]
        if pending_entries:
            lines.append(f"[미입고 > 0 상품 ({len(pending_entries)}개)]")
            lines.append("-" * 72)
            for entry in pending_entries:
                self._format_entry(lines, entry)
            lines.append("")

        # 기타 (미입고=0이지만 패턴 감지)
        other_entries = [e for e in self._pending_log_entries
                         if e['pending_qty'] == 0
                         and e not in cross_entries]
        if other_entries:
            lines.append(f"[미입고=0, 패턴만 감지 ({len(other_entries)}개)]")
            lines.append("-" * 72)
            for entry in other_entries:
                self._format_entry(lines, entry)
            lines.append("")

        lines.append("=" * 72)
        lines.append("로그 끝")
        lines.append("=" * 72)
        lines.append("")

        try:
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            logger.info(f"미입고 디버그 로그 저장: {log_path} ({total}건)")
        except Exception as e:
            logger.warning(f"미입고 디버그 로그 저장 실패: {e}")

    def _calculate_pending_complex(self, history: List[Dict], order_unit_qty: int, item_cd: str) -> tuple:
        """
        복잡한 미입고 계산 (교차날짜 보정 포함) - 기존 방식

        전체 발주/입고 이력을 집계하여 총량 차이로 미입고를 계산합니다.
        발주일과 입고일이 다른 교차날짜 패턴에서 과대평가를 방지합니다.

        주의: dsOrderSale에 1차/2차 배송 상품(ITEM_CD가 다름)이 혼재할 수 있으므로
        조회 대상 item_cd와 일치하는 행만 필터링하여 계산합니다.

        Args:
            history: dsOrderSale 행 리스트
            order_unit_qty: 입수 (발주 배수당 실제 개수)
            item_cd: 상품코드 (필터링 + 로그용)

        Returns:
            (pending_qty, pending_detail) 튜플
        """
        today_str = datetime.now().strftime("%Y%m%d")

        past_ordered_total = 0
        past_received_total = 0
        today_ordered_total = 0
        today_received_total = 0

        # ITEM_CD 필터링: 1차/2차 배송 구분 (같은 JIP_ITEM_CD에 ITEM_CD가 다른 행이 혼재)
        filtered_history = [
            h for h in history
            if not h.get('item_cd') or h.get('item_cd') == item_cd
        ]
        if len(filtered_history) < len(history):
            skipped = len(history) - len(filtered_history)
            logger.debug(
                f"[ITEM_CD 필터] {item_cd}: 전체 {len(history)}행 중 {skipped}행 제외 "
                f"(다른 배송차수 상품)"
            )

        pending_detail = []  # 디버그 로그용
        for h in filtered_history:
            ord_qty = h.get('ord_qty', 0)  # 발주 배수
            buy_qty = h.get('buy_qty', 0)  # 실제 입고 개수
            ordered_actual = ord_qty * order_unit_qty
            h_date = h.get('date', '')

            # 행별 차이 (디버그용 - 기존 방식 기록)
            diff = (ordered_actual - buy_qty) if ordered_actual > buy_qty else 0

            # 과거/오늘 분리 집계
            if h_date >= today_str:
                today_ordered_total += ordered_actual
                today_received_total += buy_qty
            else:
                past_ordered_total += ordered_actual
                past_received_total += buy_qty

            pending_detail.append({
                'date': h_date,
                'ord_qty': ord_qty,
                'buy_qty': buy_qty,
                'sale_qty': h.get('sale_qty', 0),
                'disuse_qty': h.get('disuse_qty', 0),
                'ordered_actual': ordered_actual,
                'diff': diff,
            })

        # 총량 기반 미입고 계산
        past_pending = max(0, past_ordered_total - past_received_total)
        today_pending = max(0, today_ordered_total - today_received_total)
        pending_qty = past_pending + today_pending

        # 교차날짜 보정 비교 (기존 행별 방식과 차이 로깅)
        naive_pending = sum(d['diff'] for d in pending_detail)
        if naive_pending != pending_qty:
            logger.info(
                f"[교차날짜 보정] {item_cd}: 행별={naive_pending}개 → 총량={pending_qty}개 "
                f"(차이: {naive_pending - pending_qty}개, "
                f"과거발주={past_ordered_total} 과거입고={past_received_total}, "
                f"오늘발주={today_ordered_total} 오늘입고={today_received_total})"
            )

        return pending_qty, pending_detail

    def _calculate_pending_with_comparison(
        self,
        history: List[Dict],
        order_unit_qty: int,
        item_cd: str,
        item_nm: str = ""
    ) -> tuple:
        """
        비교 모드: 두 방식 동시 실행 및 차이 로깅 (Phase 3)

        검증 기간 동안 단순화 방식과 복잡한 방식을 모두 실행하여
        차이를 분석합니다. 실제 발주에는 복잡한 방식 결과를 사용합니다.

        Args:
            history: dsOrderSale 행 리스트
            order_unit_qty: 입수
            item_cd: 상품코드
            item_nm: 상품명 (로그용)

        Returns:
            (pending_qty, pending_detail) 튜플 - 복잡한 방식 결과 사용
        """
        # 1. 두 방식 모두 실행 (item_cd 전달하여 1차/2차 배송 필터링)
        simple_qty = self._calculate_pending_simplified(history, order_unit_qty, item_cd=item_cd)
        complex_qty, pending_detail = self._calculate_pending_complex(history, order_unit_qty, item_cd)

        # 2. 통계 수집
        self._comparison_stats['total'] += 1
        difference = complex_qty - simple_qty

        if difference == 0:
            self._comparison_stats['matches'] += 1
        else:
            self._comparison_stats['differences'] += 1
            abs_diff = abs(difference)
            self._comparison_stats['total_diff'] += abs_diff
            if abs_diff > self._comparison_stats['max_diff']:
                self._comparison_stats['max_diff'] = abs_diff

            if difference > 0:
                self._comparison_stats['complex_higher'] += 1
            else:
                self._comparison_stats['simple_higher'] += 1

        # 3. 차이 감지 및 로깅
        if difference != 0:
            logger.warning(
                f"[미입고차이] {item_cd} {item_nm}: "
                f"단순={simple_qty}개, 복잡={complex_qty}개, "
                f"차이={difference:+d}개 ({'복잡>단순' if difference > 0 else '단순>복잡'}), "
                f"이력={len(history)}건"
            )

            # 4. 상세 비교 정보 수집 (디버그용)
            # 교차날짜 패턴 감지
            has_cross_pattern = any(
                h.get('ord_qty', 0) > 0 and h.get('buy_qty', 0) == 0 for h in history
            ) and any(
                h.get('ord_qty', 0) == 0 and h.get('buy_qty', 0) > 0 for h in history
            )

            if has_cross_pattern:
                logger.info(f"  → 교차날짜 패턴 감지: {item_cd}")
                self._comparison_stats['cross_pattern_cases'] += 1

            # 최근 3일 내 여러 발주 감지
            from src.settings.constants import PENDING_LOOKBACK_DAYS
            cutoff_date = (datetime.now() - timedelta(days=PENDING_LOOKBACK_DAYS)).strftime("%Y%m%d")
            recent_orders = [
                h for h in history
                if h.get('date', '') >= cutoff_date and h.get('ord_qty', 0) > 0
            ]
            if len(recent_orders) > 1:
                logger.info(f"  → {PENDING_LOOKBACK_DAYS}일 내 {len(recent_orders)}건 발주: {item_cd}")
                self._comparison_stats['multiple_order_cases'] += 1

        # 5. 검증 기간 동안은 복잡한 방식 결과 사용 (안전)
        return complex_qty, pending_detail

    def _calculate_pending_simplified(self, history: List[Dict], order_unit_qty: int,
                                      item_cd: str = None, lookback_days: int = None) -> int:
        """
        단순화된 미입고 계산: 최근 N일 내 가장 최근 발주만 확인

        복잡한 전체 이력 집계 대신, 최근 발주 1건만 추적하여
        코드 가독성과 유지보수성을 향상시킵니다.

        주의: dsOrderSale에 1차/2차 배송 상품(ITEM_CD가 다름)이 혼재할 수 있으므로
        item_cd가 주어지면 해당 상품만 필터링하여 계산합니다.

        Args:
            history: dsOrderSale 행 리스트
            order_unit_qty: 입수 (발주 배수당 실제 개수)
            item_cd: 상품코드 (필터링용, None이면 전체 사용)
            lookback_days: 조회 기간 (일, None이면 constants.py에서 가져옴)

        Returns:
            pending_qty (개)
        """
        if not history:
            return 0

        # ITEM_CD 필터링: 1차/2차 배송 구분
        if item_cd:
            history = [
                h for h in history
                if not h.get('item_cd') or h.get('item_cd') == item_cd
            ]
            if not history:
                return 0

        # 조회 기간 설정
        if lookback_days is None:
            from src.settings.constants import PENDING_LOOKBACK_DAYS
            lookback_days = PENDING_LOOKBACK_DAYS

        cutoff_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")

        # 최근 발주 필터링 (lookback_days 이내 + 발주량 > 0)
        recent_orders = [
            h for h in history
            if h.get('date', '') >= cutoff_date and h.get('ord_qty', 0) > 0
        ]

        if not recent_orders:
            return 0

        # 가장 최근 발주 선택
        most_recent = max(recent_orders, key=lambda x: x.get('date', ''))

        ord_qty = most_recent.get('ord_qty', 0)
        buy_qty = most_recent.get('buy_qty', 0)

        ordered_actual = ord_qty * order_unit_qty
        pending = max(0, ordered_actual - buy_qty)

        return pending

    def _format_entry(self, lines: List[str], entry: Dict[str, Any], highlight: bool = False) -> None:
        """로그 항목 1개를 포맷"""
        prefix = "★ " if highlight else "  "
        item_cd = entry['item_cd']
        item_nm = entry['item_nm']
        stock = entry['current_stock']
        unit = entry['order_unit_qty']
        pending = entry['pending_qty']

        naive = entry.get('naive_pending', pending)
        was_corrected = entry.get('was_corrected', False)

        lines.append(f"{prefix}{item_cd} | {item_nm}")
        if was_corrected:
            lines.append(f"  재고={stock}, 입수={unit}, 미입고={pending} (보정 전 행별합={naive}, 차이={naive - pending})")
        else:
            lines.append(f"  재고={stock}, 입수={unit}, 미입고={pending}")

        flags = []
        if entry['has_ord_no_buy']:
            flags.append("발주O입고X")
        if entry['has_buy_no_ord']:
            flags.append("발주X입고O")
        if was_corrected:
            flags.append("교차날짜보정")
        if flags:
            lines.append(f"  패턴: {', '.join(flags)}")

        # 이력 테이블
        history = entry['history']
        if history:
            lines.append(f"  {'날짜':>12s} | {'발주배수':>6s} | {'입고개수':>6s} | {'판매':>4s} | {'폐기':>4s} | {'발주실제':>6s} | {'차이':>4s}")
            for h in history:
                date_str = h['date'] or '?'
                ord_str = str(h['ord_qty']) if h['ord_qty'] > 0 else '-'
                buy_str = str(h['buy_qty']) if h['buy_qty'] > 0 else '-'
                sale_str = str(h['sale_qty']) if h['sale_qty'] > 0 else '-'
                disuse_str = str(h['disuse_qty']) if h['disuse_qty'] > 0 else '-'
                actual_str = str(h['ordered_actual']) if h['ordered_actual'] > 0 else '-'
                diff_str = f"+{h['diff']}" if h['diff'] > 0 else '-'

                marker = ""
                if h['ord_qty'] > 0 and h['buy_qty'] == 0:
                    marker = " ← 발주O 입고X"
                elif h['ord_qty'] == 0 and h['buy_qty'] > 0:
                    marker = " ← 발주X 입고O"

                lines.append(
                    f"  {date_str:>12s} | {ord_str:>6s} | {buy_str:>6s} | "
                    f"{sale_str:>4s} | {disuse_str:>4s} | {actual_str:>6s} | {diff_str:>4s}{marker}"
                )
        lines.append("")

    # ============================================================
    # 하위 호환성을 위한 기존 메서드들
    # ============================================================

    def query_item_order_history(self, item_cd: str) -> Optional[Dict[str, Any]]:
        """
        상품코드로 발주/입고 이력 조회 (하위 호환성)

        Args:
            item_cd: 상품코드

        Returns:
            기존 PendingOrderCollector와 동일한 형식
        """
        result = self.collect_for_item(item_cd)
        if result.get('success'):
            return result
        return None

    def get_pending_qty(self, item_cd: str) -> int:
        """
        상품의 미입고 수량만 조회 (간단 버전)

        Args:
            item_cd: 상품코드

        Returns:
            미입고 수량 (발주 - 입고 합계)
        """
        result = self.collect_for_item(item_cd)
        if result.get('success'):
            return result.get('pending_qty', 0)
        return 0

    def collect_pending_for_items(self, item_codes: List[str]) -> Dict[str, int]:
        """
        여러 상품의 미입고 수량 일괄 조회 (하위 호환성)

        Args:
            item_codes: 상품코드 목록

        Returns:
            {상품코드: 미입고수량, ...}
        """
        results = self.collect_for_items(item_codes)
        return {
            item_cd: data.get('pending_qty', 0)
            for item_cd, data in results.items()
            if data.get('success')
        }

    def collect_and_save(
        self,
        item_codes: List[str],
        return_full_info: bool = False
    ) -> Dict[str, Any]:
        """
        여러 상품의 재고/미입고 수량 조회 및 DB 저장

        Args:
            item_codes: 상품코드 목록
            return_full_info: True면 전체 정보 반환, False면 미입고 수량만 반환

        Returns:
            {
                'pending': {상품코드: 미입고수량, ...},
                'stock': {상품코드: 현재재고, ...},
                'expiration': {상품코드: 유통기한, ...},
                'unavailable': [미취급 상품코드, ...],
                'success_count': 성공 개수,
                'fail_count': 실패 개수
            }
            또는 return_full_info=False일 때: {상품코드: 미입고수량, ...}
        """
        results = self.collect_for_items(item_codes)

        pending_data = {}
        stock_data = {}
        expiration_data = {}
        unavailable = []
        success_count = 0
        fail_count = 0

        for item_cd, data in results.items():
            if data.get('success'):
                pending_data[item_cd] = data.get('pending_qty', 0)
                stock_data[item_cd] = data.get('current_stock', 0)
                if data.get('expiration_days'):
                    expiration_data[item_cd] = data.get('expiration_days')
                success_count += 1
            else:
                unavailable.append(item_cd)
                fail_count += 1

        if not return_full_info:
            return pending_data

        return {
            'pending': pending_data,
            'stock': stock_data,
            'expiration': expiration_data,
            'unavailable': unavailable,
            'success_count': success_count,
            'fail_count': fail_count
        }

    def get_from_db(self, item_cd: str) -> Optional[Dict[str, Any]]:
        """
        DB에서 저장된 재고/미입고 정보 조회

        Args:
            item_cd: 상품코드

        Returns:
            DB에 저장된 정보 또는 None
        """
        if not self._repo:
            self._repo = RealtimeInventoryRepository(store_id=self.store_id)
        return self._repo.get(item_cd)

    def get_all_from_db(self, available_only: bool = True) -> List[Dict[str, Any]]:
        """
        DB에서 모든 재고/미입고 정보 조회

        Args:
            available_only: True면 점포 취급 상품만 조회

        Returns:
            상품 목록
        """
        if not self._repo:
            self._repo = RealtimeInventoryRepository(store_id=self.store_id)
        return self._repo.get_all(available_only=available_only, store_id=self.store_id)

    def get_unavailable_from_db(self) -> List[str]:
        """
        DB에서 미취급 상품 코드 목록 조회

        Returns:
            미취급 상품코드 리스트
        """
        if not self._repo:
            self._repo = RealtimeInventoryRepository(store_id=self.store_id)
        return self._repo.get_unavailable_items(store_id=self.store_id)

    def close_menu(self) -> None:
        """단품별 발주 메뉴 탭 닫기 및 상태 초기화"""
        try:
            logger.info("미입고 조회 메뉴 닫기 시작...")

            # 1차: Alert 모두 처리
            from src.utils.popup_manager import close_alerts
            alert_count = close_alerts(self.driver, max_attempts=5, silent=False)
            if alert_count > 0:
                logger.info(f"메뉴 닫기 전 Alert {alert_count}개 처리")

            # 2차: 팝업 모두 닫기
            popup_count = close_all_popups(self.driver, silent=False)
            logger.info(f"메뉴 닫기: 팝업 {popup_count}개 닫음 (진단 모드)")

            time.sleep(0.3)

            # 3차: 탭 닫기
            close_tab_by_frame_id(self.driver, self.FRAME_ID)
            time.sleep(PREP_MENU_CLOSE_WAIT)

            # 4차: 상태 초기화
            self._menu_navigated = False
            self._date_selected = False

            logger.info("미입고 조회 메뉴 닫기 완료")
        except Exception as e:
            logger.warning(f"메뉴 탭 닫기 실패: {e}")


def run_order_prep_collection(driver, item_codes: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    발주 준비 정보 수집 실행

    Args:
        driver: 로그인된 WebDriver
        item_codes: 조회할 상품코드 목록

    Returns:
        {상품코드: {pending_qty, expiration_days, current_stock, ...}, ...}
    """
    collector = OrderPrepCollector(driver)
    return collector.collect_for_items(item_codes)


if __name__ == "__main__":
    print("이 모듈은 직접 실행할 수 없습니다.")
    print("드라이버를 전달하여 run_order_prep_collection(driver, item_codes)를 호출하세요.")
