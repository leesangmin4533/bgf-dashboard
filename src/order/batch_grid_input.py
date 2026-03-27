"""
Hybrid 배치 그리드 입력 모듈

넥사크로 dataset.setColumn()으로 그리드에 직접 데이터를 채운 후
기존 UI 저장 버튼(confirm_order)으로 저장합니다.

Selenium 개별 입력(3.3초/상품) 대신 JS로 일괄 입력(~1초/50상품)하여
85% 이상 시간 단축을 달성합니다.

관련:
    - src/order/order_executor.py: confirm_order() 저장 로직 재사용
    - src/order/direct_api_saver.py: Direct API 방식 (Level 1)
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.order.direct_api_saver import SaveResult
from src.settings.constants import MAX_ORDER_MULTIPLIER
from src.settings.timing import (
    BATCH_GRID_POPULATE_WAIT,
    BATCH_GRID_SAVE_WAIT,
    BATCH_GRID_ROW_DELAY_MS,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


# 넥사크로 폼 탐색 JS (order_executor._FIND_ORDER_FORM_JS 동일)
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


class BatchGridInputter:
    """
    넥사크로 dataset 직접 조작으로 배치 발주 입력

    사용 플로우:
        1. input_batch()로 그리드에 상품 일괄 입력
        2. 내부적으로 confirm_order() 호출하여 저장
        3. read_grid_state()로 입력 결과 확인 (선택)

    제한:
        - 넥사크로 dataset 객체에 접근 가능해야 함
        - 단품별 발주(STBJ030_M0) 화면이 열려있어야 함
    """

    def __init__(self, driver: Any):
        self.driver = driver

    # ─────────────────────────────────────────
    # 1. 그리드 상태 확인
    # ─────────────────────────────────────────

    def check_grid_ready(self) -> Dict[str, Any]:
        """
        그리드가 배치 입력 가능한 상태인지 확인

        Returns:
            {ready: bool, dataset_name: str, row_count: int, columns: [...]}
        """
        try:
            result = self.driver.execute_script(_FIND_ORDER_FORM_JS + """
                if (!workForm?.gdList) {
                    return {ready: false, reason: 'no_gdList'};
                }

                // _binddataset은 Dataset 객체 직접 반환 (문자열 아님)
                var ds = workForm.gdList._binddataset;
                if (!ds || typeof ds.getColCount !== 'function') {
                    ds = workForm.gdList._binddataset_obj;
                }
                if (!ds) {
                    return {ready: false, reason: 'dataset_not_found'};
                }

                var dsId = ds._id || ds.name || ds._name || 'unknown';

                // 컬럼 목록 확인
                var cols = [];
                try {
                    for (var i = 0; i < ds.getColCount(); i++) {
                        cols.push(ds.getColID(i));
                    }
                } catch(e) {
                    cols = ['error: ' + e.message];
                }

                return {
                    ready: true,
                    dsName: dsId,
                    rowCount: ds.getRowCount(),
                    columns: cols,
                    hasItemCd: cols.includes('ITEM_CD'),
                    hasOrdQty: cols.includes('ORD_QTY'),
                };
            """)

            return result or {'ready': False, 'reason': 'js_returned_null'}

        except Exception as e:
            return {'ready': False, 'reason': f'exception: {e}'}

    def read_grid_state(self) -> List[Dict[str, Any]]:
        """
        현재 그리드 데이터 읽기

        Returns:
            [{item_cd, ord_qty, item_nm, ...}, ...]
        """
        try:
            result = self.driver.execute_script(_FIND_ORDER_FORM_JS + """
                if (!workForm?.gdList) return [];

                let ds = workForm.gdList._binddataset;
                if (!ds || typeof ds.getRowCount !== 'function') {
                    ds = workForm.gdList._binddataset_obj;
                }
                if (!ds) return [];

                var items = [];
                for (var i = 0; i < ds.getRowCount(); i++) {
                    var item = {};
                    for (var j = 0; j < ds.getColCount(); j++) {
                        var colId = ds.getColID(j);
                        item[colId] = ds.getColumn(i, colId) || '';
                    }
                    items.push(item);
                }
                return items;
            """)

            return result or []

        except Exception as e:
            logger.error(f"[BatchGrid] 그리드 읽기 실패: {e}")
            return []

    # ─────────────────────────────────────────
    # 2. 그리드 배치 입력
    # ─────────────────────────────────────────

    def populate_grid(
        self, orders: List[Dict[str, Any]], date_str: str = ''
    ) -> Dict[str, Any]:
        """
        넥사크로 dataset에 상품 데이터 일괄 입력

        Args:
            orders: [{item_cd, multiplier 또는 final_order_qty, order_unit_qty}, ...]
            date_str: 발주일 (YYYYMMDD 또는 YYYY-MM-DD)

        Returns:
            {success: bool, added: int, errors: [...]}
        """
        if not orders:
            return {'success': True, 'added': 0}

        date_str = date_str.replace('-', '')

        # 배수 계산 + 필수 필드 포함
        items_js = []
        unit_mismatch_count = 0
        for order in orders:
            item_cd = str(order.get('item_cd', ''))
            multiplier = order.get('multiplier', 0)
            ord_unit_qty = int(order.get('order_unit_qty', 1) or 1)
            if multiplier <= 0:
                qty = order.get('final_order_qty', 0)
                # 스마트발주 취소: qty=0 그대로 전송 (PYUN_QTY=0)
                if order.get('cancel_smart') and qty <= 0:
                    multiplier = 0
                else:
                    multiplier = max(1, (qty + ord_unit_qty - 1) // ord_unit_qty)
                    # 배수 비정렬 진단: order_unit_qty > 1인데 qty가 배수가 아닌 경우
                    if ord_unit_qty > 1 and qty % ord_unit_qty != 0 and qty > 0:
                        unit_mismatch_count += 1
                        if unit_mismatch_count <= 5:
                            logger.warning(
                                f"[BatchGrid] 배수 비정렬: {item_cd} "
                                f"qty={qty} unit={ord_unit_qty} → mul={int(multiplier)} "
                                f"(TOT_QTY={int(multiplier)*ord_unit_qty})"
                            )
            multiplier = min(multiplier, MAX_ORDER_MULTIPLIER)

            items_js.append({
                'item_cd': item_cd,
                'multiplier': int(multiplier),
                'ord_unit_qty': ord_unit_qty,
                'store_cd': str(order.get('store_cd', '')),
            })
        if unit_mismatch_count > 0:
            logger.warning(f"[BatchGrid] 배수 비정렬 상품: {unit_mismatch_count}건")

        try:
            result = self.driver.execute_script(_FIND_ORDER_FORM_JS + """
                if (!workForm?.gdList) {
                    return {success: false, error: 'no_gdList'};
                }

                var ds = workForm.gdList._binddataset;
                if (!ds || typeof ds.addRow !== 'function') {
                    ds = workForm.gdList._binddataset_obj;
                }
                if (!ds) {
                    return {success: false, error: 'no_dataset'};
                }

                var items = arguments[0];
                var delayMs = arguments[1];
                var dateStr = arguments[2];
                var added = 0;
                var errors = [];

                // 숫자 컬럼 빈값→0 판별용 목록
                var _knownNums = 'HQ_MAEGA_SET,ORD_UNIT_QTY,ORD_MULT_ULMT,ORD_MULT_LLMT,NOW_QTY,ORD_MUL_QTY,OLD_PYUN_QTY,TOT_QTY,PAGE_CNT,EXPIRE_DAY,PROFIT_RATE,PYUN_QTY';

                // 세션 쿠키에서 PRE_STORE_CD 읽기
                var _preStoreCd = '';
                try {
                    var _pairs = document.cookie.split(';');
                    for (var _pi = 0; _pi < _pairs.length; _pi++) {
                        var _p = _pairs[_pi].trim().split('=');
                        if (_p[0] === 'SS_PRE_STORE_CD') { _preStoreCd = _p.slice(1).join('='); break; }
                    }
                } catch(e) {}

                // 현재 시각 (CURDAY, CURTIME)
                var _now = new Date();
                var _curDay = _now.getFullYear() + ('0' + (_now.getMonth()+1)).slice(-2) + ('0' + _now.getDate()).slice(-2);
                var _curTime = ('0' + _now.getHours()).slice(-2) + ('0' + _now.getMinutes()).slice(-2) + ('0' + _now.getSeconds()).slice(-2);

                for (var i = 0; i < items.length; i++) {
                    try {
                        var row = ds.addRow();
                        var itm = items[i];

                        // 핵심 필드 설정 (PYUN_QTY=배수, TOT_QTY=발주량)
                        ds.setColumn(row, 'ITEM_CD', itm.item_cd);
                        ds.setColumn(row, 'JIP_ITEM_CD', itm.item_cd);
                        ds.setColumn(row, 'PYUN_QTY', String(itm.multiplier));
                        ds.setColumn(row, 'TOT_QTY', itm.multiplier * itm.ord_unit_qty);
                        ds.setColumn(row, 'ORD_UNIT_QTY', itm.ord_unit_qty);
                        ds.setColumn(row, 'ITEM_CHK', '1');
                        ds.setColumn(row, 'PYUN_ID', '1');
                        ds.setColumn(row, 'CT_ITEM_YN', '0');
                        ds.setColumn(row, 'CUT_ITEM_YN', '0');
                        ds.setColumn(row, 'ORD_MULT_ULMT', 99);
                        ds.setColumn(row, 'ORD_MULT_LLMT', 1);
                        ds.setColumn(row, 'PRE_STORE_CD', _preStoreCd);
                        ds.setColumn(row, 'CURDAY', _curDay);
                        ds.setColumn(row, 'CURTIME', _curTime);
                        if (dateStr) ds.setColumn(row, 'ORD_YMD', dateStr);
                        if (itm.store_cd) ds.setColumn(row, 'STORE_CD', itm.store_cd);

                        // 숫자 컬럼 빈값→0 (NumberFormatException 방지)
                        for (var _ci = 0; _ci < ds.getColCount(); _ci++) {
                            try {
                                var _cid = ds.getColID(_ci);
                                var _cv = ds.getColumn(row, _cid);
                                if (_cv != null && _cv !== '') continue;
                                var _isNum = false;
                                try {
                                    var _t = String((ds.getColumnInfo(_ci)||{}).type||'').toUpperCase();
                                    if (_t === 'INT' || _t.indexOf('DECIMAL') >= 0 || _t === 'FLOAT' || _t === 'NUMBER') _isNum = true;
                                } catch(e2) {}
                                if (!_isNum && _knownNums.indexOf(_cid) >= 0) _isNum = true;
                                if (_isNum) ds.setColumn(row, _cid, 0);
                            } catch(e3) {}
                        }

                        added++;
                    } catch(e) {
                        errors.push({
                            item_cd: items[i].item_cd,
                            error: e.message
                        });
                    }
                }

                // 그리드 UI 갱신
                try {
                    workForm.gdList.set_enableredraw(true);
                    if (typeof ds.applyChange === 'function') {
                        ds.applyChange();
                    }
                } catch(e) {}

                return {
                    success: added > 0,
                    added: added,
                    total: items.length,
                    errors: errors,
                    dsRowCount: ds.getRowCount()
                };
            """, items_js, BATCH_GRID_ROW_DELAY_MS, date_str)

            if result:
                logger.info(
                    f"[BatchGrid] 그리드 입력: {result.get('added', 0)}/{len(orders)}건, "
                    f"에러={len(result.get('errors', []))}건"
                )

            return result or {'success': False, 'error': 'js_returned_null'}

        except Exception as e:
            logger.error(f"[BatchGrid] 그리드 입력 실패: {e}")
            return {'success': False, 'error': str(e)}

    def clear_grid(self) -> bool:
        """그리드 데이터 초기화"""
        try:
            result = self.driver.execute_script(_FIND_ORDER_FORM_JS + """
                if (!workForm?.gdList) return false;

                var ds = workForm.gdList._binddataset;
                if (!ds || typeof ds.clearData !== 'function') {
                    ds = workForm.gdList._binddataset_obj;
                }
                if (!ds) return false;

                ds.clearData();
                return true;
            """)
            return result or False
        except Exception as e:
            logger.warning(f"[BatchGrid] 그리드 초기화 실패: {e}")
            return False

    # ─────────────────────────────────────────
    # 3. 배치 입력 + 저장
    # ─────────────────────────────────────────

    def input_batch(
        self,
        orders: List[Dict[str, Any]],
        date_str: str,
        confirm_fn: Any = None,
    ) -> SaveResult:
        """
        배치 그리드 입력 + 저장 실행

        Args:
            orders: 발주 목록
            date_str: 발주일 (YYYY-MM-DD 또는 YYYYMMDD)
            confirm_fn: 저장 함수 (None이면 내장 저장 사용)

        Returns:
            SaveResult
        """
        start_time = time.time()

        if not orders:
            return SaveResult(success=True, saved_count=0, method='batch_grid', message='empty')

        # 1. 그리드 준비 확인
        grid_status = self.check_grid_ready()
        if not grid_status.get('ready'):
            reason = grid_status.get('reason', 'unknown')
            return SaveResult(
                success=False, method='batch_grid',
                message=f'grid not ready: {reason}',
            )

        logger.info(f"[BatchGrid] 그리드 준비 완료: ds={grid_status.get('dsName')}")

        # 2. 그리드에 데이터 입력
        populate_result = self.populate_grid(orders, date_str)
        if not populate_result.get('success'):
            error = populate_result.get('error', 'unknown')
            return SaveResult(
                success=False, method='batch_grid',
                message=f'populate failed: {error}',
            )

        time.sleep(BATCH_GRID_POPULATE_WAIT)

        # 3. 저장
        try:
            if confirm_fn:
                # OrderExecutor.confirm_order() 재사용
                save_result = confirm_fn()
            else:
                # 내장 저장 (confirm_order와 동일한 로직)
                save_result = self._confirm_save()

            time.sleep(BATCH_GRID_SAVE_WAIT)

            elapsed = (time.time() - start_time) * 1000
            added = populate_result.get('added', 0)

            if save_result and save_result.get('success'):
                logger.info(f"[BatchGrid] 배치 저장 완료: {added}건, {elapsed:.0f}ms")
                return SaveResult(
                    success=True,
                    saved_count=added,
                    elapsed_ms=elapsed,
                    method='batch_grid',
                    message=f'saved {added} items via batch grid',
                )
            else:
                msg = save_result.get('message', 'save failed') if save_result else 'confirm returned None'
                return SaveResult(
                    success=False,
                    saved_count=0,
                    elapsed_ms=elapsed,
                    method='batch_grid',
                    message=msg,
                )

        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            return SaveResult(
                success=False,
                elapsed_ms=elapsed,
                method='batch_grid',
                message=f'save exception: {e}',
            )

    def _confirm_save(self) -> Dict[str, Any]:
        """
        내장 저장 로직 (order_executor.confirm_order 간소화 버전)

        저장 버튼 클릭 + Alert 처리
        """
        try:
            result = self.driver.execute_script(_FIND_ORDER_FORM_JS + """
                // "저장" 텍스트 버튼 찾기
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
                        return {success: true, method: 'text_search'};
                    }
                }

                // 넥사크로 폼에서 찾기
                if (stbjForm) {
                    const workBtn = stbjForm.div_workBtn?.form;
                    if (workBtn) {
                        for (const [name, obj] of Object.entries(workBtn)) {
                            if (name.startsWith('btn_') && obj?.click) {
                                const btnText = obj.text || '';
                                if (btnText.includes('저장')) {
                                    obj.click();
                                    return {success: true, method: 'nexacro_btn'};
                                }
                            }
                        }
                    }
                }

                return {success: false, message: 'save button not found'};
            """)

            if not result or not result.get('success'):
                return result or {'success': False, 'message': 'JS returned null'}

            # Alert 처리
            time.sleep(1.0)
            for _ in range(3):
                try:
                    alert = self.driver.switch_to.alert
                    alert.accept()
                    time.sleep(0.5)
                except Exception:
                    break

            return result

        except Exception as e:
            return {'success': False, 'message': str(e)}
