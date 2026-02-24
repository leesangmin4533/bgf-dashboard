"""
발주 실패 사유 수집기
- BGF 홈화면에서 바코드 입력 → 상품 상세 팝업 → 정지사유(stStopReason) 추출
- eval_outcomes에서 order_status='fail'인 상품 일괄 조회
- 결과를 order_fail_reasons 테이블에 저장

플로우:
1. eval_outcomes에서 실패 상품 목록 조회
2. 각 상품에 대해:
   - edt_pluSearch에 바코드 입력
   - Enter → Quick Search → 클릭 → CallItemDetailPopup 팝업
   - divInfo01.form.stStopReason 추출
   - 팝업 닫기
3. DB 일괄 저장
"""

import time
from datetime import datetime
from typing import Dict, Any, Optional, List
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.utils.logger import get_logger
from src.utils.screenshot import save_screenshot
from src.infrastructure.database.repos import FailReasonRepository
from src.settings.constants import FAIL_REASON_UNKNOWN, FAIL_REASON_MAX_ITEMS
from src.settings.timing import (
    FR_BARCODE_INPUT_WAIT,
    FR_POPUP_OPEN_WAIT,
    FR_POPUP_MAX_CHECKS,
    FR_POPUP_CHECK_INTERVAL,
    FR_POPUP_CLOSE_WAIT,
    FR_BETWEEN_ITEMS,
    FR_DATA_LOAD_MAX_CHECKS,
    FR_DATA_LOAD_CHECK_INTERVAL,
)
from src.settings.ui_config import FAIL_REASON_UI

logger = get_logger(__name__)


class FailReasonCollector:
    """발주 실패 사유 수집기

    BGF 홈화면의 edt_pluSearch에 바코드를 입력하고 Enter를 치면
    CallItemDetailPopup이 뜨는데, 여기서 stStopReason(정지사유)을 추출한다.
    """

    def __init__(self, driver=None, store_id: Optional[str] = None):
        """초기화

        Args:
            driver: Selenium WebDriver 인스턴스 (None이면 나중에 set_driver)
            store_id: 매장 코드
        """
        self.driver = driver
        self.store_id = store_id
        self.repo = FailReasonRepository(store_id=self.store_id)

    def set_driver(self, driver) -> None:
        """드라이버 설정"""
        self.driver = driver

    def collect_all(self, eval_date: Optional[str] = None, max_items: int = 0) -> Dict[str, Any]:
        """실패 상품 전체 일괄 조회 (메인 진입점)

        Args:
            eval_date: 평가일 (기본값: 오늘)
            max_items: 최대 조회 건수 (0이면 FAIL_REASON_MAX_ITEMS 사용)

        Returns:
            {total, checked, success, failed, results: [...]}
        """
        if eval_date is None:
            eval_date = datetime.now().strftime("%Y-%m-%d")

        limit = max_items if max_items > 0 else FAIL_REASON_MAX_ITEMS

        logger.info(f"[FailReason] 실패 사유 수집 시작: {eval_date}")

        # 미확인 실패 상품 조회
        items = self.repo.get_failed_items_to_check(eval_date, store_id=self.store_id)
        total = len(items)

        if total == 0:
            logger.info("[FailReason] 확인할 실패 상품 없음")
            return {"total": 0, "checked": 0, "success": 0, "failed": 0, "results": []}

        # 최대 건수 제한
        if total > limit:
            logger.info(f"[FailReason] {total}건 → {limit}건으로 제한")
            items = items[:limit]

        logger.info(f"[FailReason] {len(items)}건 조회 시작")

        results = []
        success_count = 0
        fail_count = 0

        for idx, item in enumerate(items):
            item_cd = item["item_cd"]
            item_nm = item.get("item_nm", "")
            mid_cd = item.get("mid_cd", "")

            logger.info(f"[FailReason] ({idx+1}/{len(items)}) {item_cd} {item_nm}")

            try:
                result = self.lookup_stop_reason(item_cd)

                if result:
                    result["item_nm"] = item_nm or result.get("item_nm", "")
                    result["mid_cd"] = mid_cd
                    results.append(result)
                    success_count += 1
                    logger.info(
                        f"  → 정지사유: {result.get('stop_reason', FAIL_REASON_UNKNOWN)}"
                    )
                else:
                    results.append({
                        "item_cd": item_cd,
                        "item_nm": item_nm,
                        "mid_cd": mid_cd,
                        "stop_reason": FAIL_REASON_UNKNOWN,
                    })
                    fail_count += 1
                    logger.warning(f"  → 조회 실패")

            except Exception:
                logger.exception(f"  → 오류 발생: {item_cd}")
                results.append({
                    "item_cd": item_cd,
                    "item_nm": item_nm,
                    "mid_cd": mid_cd,
                    "stop_reason": FAIL_REASON_UNKNOWN,
                })
                fail_count += 1

            # 상품 간 대기
            if idx < len(items) - 1:
                time.sleep(FR_BETWEEN_ITEMS)

        # 일괄 DB 저장
        saved = self.repo.save_fail_reasons_batch(eval_date, results)
        logger.info(
            f"[FailReason] 완료: {success_count}성공 / {fail_count}실패 / {saved}건 저장"
        )

        # stopped_items 동기화 (common.db — 발주정지 상품 관리)
        try:
            from src.infrastructure.database.repos import StoppedItemRepository
            stopped_repo = StoppedItemRepository()
            sync_result = stopped_repo.sync_from_fail_reasons(results)
            logger.info(
                f"[FailReason] stopped_items 동기화: "
                f"{sync_result.get('activated', 0)}건 활성, "
                f"{sync_result.get('deactivated', 0)}건 해제"
            )
        except Exception as e:
            logger.warning(f"[FailReason] stopped_items 동기화 실패: {e}")

        return {
            "total": total,
            "checked": len(results),
            "success": success_count,
            "failed": fail_count,
            "results": results,
        }

    def lookup_stop_reason(self, item_cd: str) -> Optional[Dict[str, Any]]:
        """단일 상품 정지사유 조회

        Args:
            item_cd: 상품코드 (바코드)

        Returns:
            {item_cd, stop_reason, orderable_status, orderable_day, item_nm} 또는 None
        """
        if not self.driver:
            logger.error("[FailReason] WebDriver가 설정되지 않음")
            return None

        try:
            # 1. 기존 Alert 처리
            self._clear_alerts()

            # 2. 바코드 입력
            if not self._input_barcode(item_cd):
                logger.warning(f"[FailReason] 바코드 입력 실패: {item_cd}")
                return None

            # 3. Enter 키 트리거 → Quick Search 드롭다운
            if not self._trigger_enter():
                logger.warning(f"[FailReason] Enter 트리거 실패: {item_cd}")
                return None

            # 4. Quick Search 결과에서 첫 번째 항목 클릭 → CallItemDetailPopup
            time.sleep(FR_POPUP_OPEN_WAIT)
            self._click_quick_search_item()

            # 5. CallItemDetailPopup 대기
            time.sleep(FR_POPUP_OPEN_WAIT)

            # 6. 팝업 대기 (polling)
            if not self._wait_for_popup():
                # Alert가 뜬 경우 (존재하지 않는 상품 등) 처리
                alert_text = self._clear_alerts()
                if alert_text:
                    logger.info(f"[FailReason] Alert: {alert_text}")
                    return {
                        "item_cd": item_cd,
                        "stop_reason": alert_text,
                    }
                logger.warning(f"[FailReason] 팝업 미표시: {item_cd}")
                save_screenshot(self.driver, f"fail_reason_no_popup_{item_cd}")
                return None

            # 6.5 데이터 로딩 대기 (팝업 렌더링과 데이터셋 로딩은 비동기)
            if not self._wait_for_data_loaded():
                logger.warning(f"[FailReason] 데이터 로딩 실패, 추출 시도: {item_cd}")

            # 7. 정지사유 추출
            result = self._extract_stop_reason(item_cd)

            # 8. 팝업 닫기
            self._close_popup()
            time.sleep(FR_POPUP_CLOSE_WAIT)

            return result

        except Exception:
            logger.exception(f"[FailReason] lookup_stop_reason 오류: {item_cd}")
            # 안전하게 팝업/Alert 정리
            try:
                self._close_popup()
            except Exception as e:
                logger.debug(f"팝업 닫기 중: {e}")
            try:
                self._clear_alerts()
            except Exception as e:
                logger.debug(f"Alert 정리 중: {e}")
            return None

    def _input_barcode(self, item_cd: str) -> bool:
        """edt_pluSearch에 바코드 입력

        nexacro set_value → DOM fallback

        Args:
            item_cd: 상품코드

        Returns:
            입력 성공 여부
        """
        result = self.driver.execute_script("""
            var barcode = arguments[0];
            try {
                // 1차: nexacro component로 접근
                var app = nexacro.getApplication();
                var frameSet = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
                var wf = frameSet.WorkFrame;
                if (wf && wf.form && wf.form.edt_pluSearch) {
                    wf.form.edt_pluSearch.set_value(barcode);
                    return {success: true, method: 'nexacro'};
                }
            } catch(e) {}

            try {
                // 2차: DOM ID 직접 접근
                var domId = arguments[1];
                var el = document.getElementById(domId);
                if (el) {
                    el.value = barcode;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return {success: true, method: 'dom_id'};
                }
            } catch(e) {}

            try {
                // 3차: querySelector fallback
                var inputs = document.querySelectorAll('[id*="edt_pluSearch"]');
                for (var i = 0; i < inputs.length; i++) {
                    var inp = inputs[i];
                    if (inp.tagName === 'INPUT' || inp.querySelector('input')) {
                        var target = inp.tagName === 'INPUT' ? inp : inp.querySelector('input');
                        target.value = barcode;
                        target.dispatchEvent(new Event('input', {bubbles: true}));
                        target.dispatchEvent(new Event('change', {bubbles: true}));
                        return {success: true, method: 'querySelector'};
                    }
                }
            } catch(e) {}

            return {success: false};
        """, item_cd, FAIL_REASON_UI["BARCODE_INPUT_DOM"])

        if result and result.get("success"):
            time.sleep(FR_BARCODE_INPUT_WAIT)
            return True
        return False

    def _trigger_enter(self) -> bool:
        """Enter 키로 상품 상세 팝업 트리거

        nexacro on_fire_onkeyup → KeyboardEvent fallback

        Returns:
            트리거 성공 여부
        """
        result = self.driver.execute_script("""
            try {
                // 1차: nexacro 이벤트 직접 호출
                var app = nexacro.getApplication();
                var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                var edt = wf.form.edt_pluSearch;
                if (edt && typeof edt.on_fire_onkeyup === 'function') {
                    // nexacro KeyEventInfo 시뮬레이션
                    var evt = new nexacro.KeyEventInfo(edt, 'onkeyup', 13, false, false, false, false);
                    edt.on_fire_onkeyup(edt, evt);
                    return {success: true, method: 'nexacro_event'};
                }
            } catch(e) {}

            try {
                // 2차: DOM KeyboardEvent
                var domId = arguments[0];
                var el = document.getElementById(domId);
                if (!el) {
                    var els = document.querySelectorAll('[id*="edt_pluSearch"] input, [id*="edt_pluSearch"]');
                    el = els.length > 0 ? els[0] : null;
                }
                if (el) {
                    var keyEvent = new KeyboardEvent('keyup', {
                        key: 'Enter', code: 'Enter', keyCode: 13,
                        which: 13, bubbles: true
                    });
                    el.dispatchEvent(keyEvent);
                    return {success: true, method: 'dom_keyevent'};
                }
            } catch(e) {}

            return {success: false};
        """, FAIL_REASON_UI["BARCODE_INPUT_DOM"])

        return bool(result and result.get("success"))

    def _click_quick_search_item(self) -> bool:
        """Quick Search 드롭다운의 첫 번째 항목 클릭

        edt_pluSearch에 바코드 입력 + Enter 후 Quick Search 드롭다운이 뜨면
        첫 번째 상품 항목을 클릭하여 CallItemDetailPopup을 트리거한다.

        Returns:
            클릭 성공 여부
        """
        result = self.driver.execute_script("""
            // Quick Search 드롭다운 항목 찾기
            // 1차: nexacro grid의 첫 행 클릭 (Quick Search 그리드)
            try {
                var app = nexacro.getApplication();
                var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                if (wf && wf.form) {
                    // Quick Search 관련 그리드/리스트 찾기
                    var form = wf.form;
                    // grd_quickSearch 또는 유사 그리드
                    var grids = ['grd_quickSearch', 'grd_pluSearch', 'Grid00'];
                    for (var i = 0; i < grids.length; i++) {
                        var g = form[grids[i]];
                        if (g && g._currow !== undefined) {
                            g.set_focusrow(0);
                            if (typeof g.on_fire_oncellclick === 'function') {
                                g.on_fire_oncellclick(g, 0, 0);
                                return {success: true, method: 'grid_click_' + grids[i]};
                            }
                            if (typeof g.on_fire_oncelldblclick === 'function') {
                                g.on_fire_oncelldblclick(g, 0, 0);
                                return {success: true, method: 'grid_dblclick_' + grids[i]};
                            }
                        }
                    }
                }
            } catch(e) {}

            // 2차: DOM에서 Quick Search 결과 영역의 첫 행 클릭
            try {
                // Quick Search 결과 셀 (그리드 body 영역의 첫 번째 div 클릭)
                var cells = document.querySelectorAll(
                    '[id*="WorkFrame"][id*="quickSearch"] [id*="body"] [id*="cell_"],' +
                    '[id*="WorkFrame"][id*="pluSearch"] [id*="body"] [id*="cell_"],' +
                    '[id*="WorkFrame"][id*="Quick"] [id*="cell_"]'
                );
                if (cells.length > 0) {
                    var el = cells[0];
                    el.scrollIntoView({block: 'center'});
                    var r = el.getBoundingClientRect();
                    var o = {bubbles: true, cancelable: true, view: window,
                             clientX: r.left + r.width/2, clientY: r.top + r.height/2};
                    el.dispatchEvent(new MouseEvent('mousedown', o));
                    el.dispatchEvent(new MouseEvent('mouseup', o));
                    el.dispatchEvent(new MouseEvent('click', o));
                    return {success: true, method: 'dom_cell_click'};
                }
            } catch(e) {}

            // 3차: Quick Search 리스트 내 첫 번째 보이는 행 클릭
            try {
                var rows = document.querySelectorAll(
                    '[id*="WorkFrame"] [id*="body"] div[id*="cell_0_"]'
                );
                for (var j = 0; j < rows.length; j++) {
                    var row = rows[j];
                    if (row.offsetParent !== null && row.offsetHeight > 0) {
                        var rb = row.getBoundingClientRect();
                        if (rb.width > 0 && rb.height > 0) {
                            var mo = {bubbles: true, cancelable: true, view: window,
                                      clientX: rb.left + rb.width/2, clientY: rb.top + rb.height/2};
                            row.dispatchEvent(new MouseEvent('mousedown', mo));
                            row.dispatchEvent(new MouseEvent('mouseup', mo));
                            row.dispatchEvent(new MouseEvent('click', mo));
                            return {success: true, method: 'dom_row_click', id: row.id};
                        }
                    }
                }
            } catch(e) {}

            return {success: false};
        """)

        if result and result.get("success"):
            logger.info(f"[FailReason] Quick Search 항목 클릭: {result.get('method')}")
            return True

        logger.warning("[FailReason] Quick Search 항목 클릭 실패")
        return False

    def _wait_for_popup(self) -> bool:
        """CallItemDetailPopup 팝업 대기 (polling)

        Returns:
            팝업 열림 여부
        """
        popup_id = FAIL_REASON_UI["POPUP_ID"]

        for _ in range(FR_POPUP_MAX_CHECKS):
            result = self.driver.execute_script("""
                var pid = arguments[0];
                try {
                    var app = nexacro.getApplication();
                    var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                    // 1차: WorkFrame 직접 자식으로 확인
                    if (wf && wf[pid] && wf[pid].form) {
                        return {found: true, method: 'wf_child'};
                    }
                    // 2차: popupframes에서 확인
                    if (wf && wf.popupframes && wf.popupframes[pid] && wf.popupframes[pid].form) {
                        return {found: true, method: 'popupframes'};
                    }
                    // 3차: form에서 직접 확인
                    if (wf && wf.form && wf.form[pid] && wf.form[pid].form) {
                        return {found: true, method: 'form_direct'};
                    }
                } catch(e) {}

                // 4차: DOM에서 팝업 form 요소 존재 확인
                try {
                    var formEl = document.querySelector('[id$="' + pid + '.form"]');
                    if (formEl && formEl.offsetParent !== null) {
                        return {found: true, method: 'dom'};
                    }
                } catch(e) {}

                return {found: false};
            """, popup_id)

            if result and result.get("found"):
                logger.info(f"[FailReason] 팝업 감지: {result.get('method')}")
                return True

            time.sleep(FR_POPUP_CHECK_INTERVAL)

        return False

    def _wait_for_data_loaded(self) -> bool:
        """팝업 내부 데이터(dsItemDetail/stStopReason) 로딩 대기 (polling)

        _wait_for_popup()은 팝업 컨테이너 존재만 확인하므로,
        실제 데이터셋 로딩 완료를 별도로 대기해야 한다.

        Returns:
            데이터 로딩 완료 여부
        """
        popup_id = FAIL_REASON_UI["POPUP_ID"]

        for attempt in range(FR_DATA_LOAD_MAX_CHECKS):
            result = self.driver.execute_script("""
                var popupId = arguments[0];
                try {
                    var app = nexacro.getApplication();
                    var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                    var popup = wf[popupId]
                             || (wf.popupframes && wf.popupframes[popupId])
                             || (wf.form && wf.form[popupId]);
                    if (!popup || !popup.form) return {loaded: false, reason: 'no_popup'};

                    // 1차: dsItemDetail 로딩 확인
                    var ds = popup.form.dsItemDetail;
                    if (ds && ds.getRowCount() > 0) {
                        return {loaded: true, method: 'dsItemDetail'};
                    }

                    // 2차: divInfo01.stStopReason 텍스트 존재 확인
                    var divInfo01 = popup.form.divInfo01;
                    if (divInfo01 && divInfo01.form && divInfo01.form.stStopReason) {
                        var txt = divInfo01.form.stStopReason.text;
                        if (txt && txt.trim()) return {loaded: true, method: 'stStopReason'};
                    }

                    return {loaded: false, reason: 'data_empty'};
                } catch(e) {
                    return {loaded: false, reason: 'error'};
                }
            """, popup_id)

            if result and result.get("loaded"):
                logger.debug(
                    f"[FailReason] 데이터 로딩 확인: {result.get('method')} "
                    f"(attempt {attempt + 1})"
                )
                return True

            time.sleep(FR_DATA_LOAD_CHECK_INTERVAL)

        logger.warning(
            f"[FailReason] 데이터 로딩 타임아웃 ({FR_DATA_LOAD_MAX_CHECKS}회)"
        )
        return False

    def _extract_stop_reason(self, item_cd: str) -> Optional[Dict[str, Any]]:
        """팝업에서 정지사유 및 추가 정보 추출

        stStopReason UI 컴포넌트 우선 추출 → 데이터셋은 보조 정보만

        Args:
            item_cd: 상품코드

        Returns:
            {item_cd, stop_reason, orderable_status, orderable_day, item_nm} 또는 None
        """
        popup_id = FAIL_REASON_UI["POPUP_ID"]

        result = self.driver.execute_script("""
            var popupId = arguments[0];
            var itemCd = arguments[1];

            function getPopupForm() {
                try {
                    var app = nexacro.getApplication();
                    var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                    if (wf[popupId] && wf[popupId].form) return wf[popupId].form;
                    if (wf.popupframes && wf.popupframes[popupId]) return wf.popupframes[popupId].form;
                    if (wf.form && wf.form[popupId]) return wf.form[popupId].form;
                } catch(e) {}
                return null;
            }

            function dsVal(ds, row, col) {
                try {
                    var v = ds.getColumn(row, col);
                    if (v && typeof v === 'object' && v.hi !== undefined) return String(v.hi);
                    return v ? String(v) : null;
                } catch(e) { return null; }
            }

            var r = {item_cd: itemCd, stop_reason: null, item_nm: null,
                     orderable_status: null, orderable_day: null};

            var popupForm = getPopupForm();
            if (!popupForm) return r;

            // 1. stStopReason UI 컴포넌트에서 정지사유 추출 (최우선)
            try {
                var divInfo01 = popupForm.divInfo01;
                if (divInfo01 && divInfo01.form && divInfo01.form.stStopReason) {
                    var txt = divInfo01.form.stStopReason.text;
                    if (txt && txt.trim()) r.stop_reason = txt.trim();
                }
            } catch(e) {}

            // 2. DOM fallback: stStopReason:text 요소에서 직접 읽기
            if (!r.stop_reason) {
                try {
                    var domId = 'mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame.'
                              + popupId + '.form.divInfo01.form.stStopReason:text';
                    var el = document.getElementById(domId);
                    if (el) {
                        var txt = (el.innerText || el.textContent || '').trim();
                        if (txt) r.stop_reason = txt;
                    }
                } catch(e) {}
            }

            // 3. querySelector fallback: stStopReason 포함 요소 검색
            if (!r.stop_reason) {
                try {
                    var els = document.querySelectorAll('[id*="CallItemDetailPopup"][id*="stStopReason"]');
                    for (var i = 0; i < els.length; i++) {
                        var txt = (els[i].innerText || els[i].textContent || '').trim();
                        if (txt) { r.stop_reason = txt; break; }
                    }
                } catch(e) {}
            }

            // 4. dsItemDetail에서 보조 정보 (상품명, 발주상태)
            try {
                var ds = popupForm.dsItemDetail;
                if (ds && ds.getRowCount() > 0) {
                    r.item_nm = dsVal(ds, 0, 'ITEM_NM');
                    r.orderable_status = dsVal(ds, 0, 'ORD_PSS_ID_NM');
                }
            } catch(e) {}

            // 5. dsItemDetailOrd에서 발주요일 등 추가 정보
            try {
                var dsOrd = popupForm.dsItemDetailOrd;
                if (dsOrd && dsOrd.getRowCount() > 0) {
                    r.orderable_day = dsVal(dsOrd, 0, 'ORD_ADAY');
                    if (!r.orderable_status) {
                        r.orderable_status = dsVal(dsOrd, 0, 'ORD_PSS_CHK_NM');
                    }
                    if (!r.item_nm) {
                        r.item_nm = dsVal(dsOrd, 0, 'ITEM_NM');
                    }
                }
            } catch(e) {}

            // 6. divInfo UI 컴포넌트 fallback (상품명)
            if (!r.item_nm) {
                try {
                    var divInfo = popupForm.divInfo;
                    if (divInfo && divInfo.form && divInfo.form.stItemNm) {
                        r.item_nm = divInfo.form.stItemNm.text || null;
                    }
                } catch(e) {}
            }

            return r;
        """, popup_id, item_cd)

        if result:
            # 빈 문자열 → None 정리
            for key in ("stop_reason", "item_nm", "orderable_status", "orderable_day"):
                if result.get(key) == "":
                    result[key] = None
            return result

        return None

    def _close_popup(self) -> None:
        """CallItemDetailPopup 닫기

        btn_close 클릭 → nexacro close → DOM fallback
        """
        popup_id = FAIL_REASON_UI["POPUP_ID"]

        self.driver.execute_script("""
            var popupId = arguments[0];

            // 1차: nexacro btn_close 클릭
            try {
                var app = nexacro.getApplication();
                var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                var popup = wf[popupId]
                         || (wf.popupframes && wf.popupframes[popupId])
                         || (wf.form && wf.form[popupId]);
                if (popup && popup.form && popup.form.btn_close) {
                    popup.form.btn_close.click();
                    return;
                }
            } catch(e) {}

            // 2차: DOM btn_close 클릭
            try {
                var btnId = arguments[1];
                var btn = document.getElementById(btnId);
                if (btn) {
                    btn.click();
                    return;
                }
            } catch(e) {}

            // 3차: querySelector fallback
            try {
                var btns = document.querySelectorAll('[id*="CallItemDetailPopup"][id*="btn_close"]');
                for (var i = 0; i < btns.length; i++) {
                    if (btns[i].offsetParent !== null) {
                        btns[i].click();
                        return;
                    }
                }
            } catch(e) {}

            // 4차: nexacro popupframes.close
            try {
                var app2 = nexacro.getApplication();
                var wf2 = app2.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                if (wf2.popupframes && wf2.popupframes[popupId]) {
                    wf2.popupframes[popupId].close();
                }
            } catch(e) {}
        """, popup_id, FAIL_REASON_UI["POPUP_CLOSE_BTN"])

    def _clear_alerts(self) -> Optional[str]:
        """Alert 다이얼로그 처리

        Returns:
            Alert 텍스트 (없으면 None)
        """
        last_text = None
        for _ in range(5):
            try:
                alert = self.driver.switch_to.alert
                last_text = alert.text
                alert.accept()
                time.sleep(0.2)
            except Exception:
                break
        return last_text
