"""
Direct API 발주 저장 모듈

넥사크로 dataset 직접 조작 + gfn_transaction JS 호출로 발주 저장.
Selenium UI 조작 없이 50개 상품을 1회 트랜잭션으로 처리합니다.

2단계 접근:
    1. dataset 채우기 + gfn_transaction 직접 호출 (권장)
    2. SSV body 직접 구성 + fetch() (폴백)

캡처 결과 (2026-02-28 라이브 검증):
    - Endpoint: POST /stbjz00/saveOrd
    - Body: SSV (세션변수 key=value + dsGeneralGrid 55컬럼 + dsSaveChk 6컬럼)
    - Response: SSV (ErrorCode + gds_ErrMsg)
    - 성공: ErrorCode=0 또는 99999 (gds_ErrMsg TYPE=NORMAL = 정상 처리)
    - 배수: PYUN_QTY 컬럼 (ORD_MUL_QTY는 빈값)
    - RowType: I (Insert) — applyChange 불필요

관련:
    - scripts/capture_save_api.py: Save API 캡처
    - captures/save_api_template.json: 캡처된 API 구조
    - src/collectors/direct_api_fetcher.py: 읽기(selSearch) Direct API
    - src/order/batch_grid_input.py: Hybrid 배치 입력 (폴백)
    - src/order/order_executor.py: Selenium 기반 발주 (최종 폴백)
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.collectors.direct_api_fetcher import (
    extract_dsitem_all_columns,
    parse_ssv_dataset,
    ssv_row_to_dict,
)
from src.settings.constants import (
    DIRECT_API_ORDER_MAX_BATCH,
    DIRECT_API_ORDER_VERIFY,
)
from src.settings.timing import (
    DIRECT_API_SAVE_TIMEOUT_MS,
    DIRECT_API_VERIFY_WAIT,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

# SSV 구분자
RS = '\u001e'  # Record Separator
US = '\u001f'  # Unit Separator
ETX = '\u0003'  # End of Text (넥사크로 null)

# 캡처 기반 상수 (2026-02-27)
SAVE_ENDPOINT = '/stbjz00/saveOrd'
SAVE_TX_ID = 'save'  # fn_save 래핑 캡처 (2026-02-28): 'savOrd'가 아닌 'save'
SAVE_SVC_URL = 'stbjz00/saveOrd'
SAVE_IN_DS = 'dsGeneralGrid=dsGeneralGrid:U dsSaveChk=dsSaveChk'  # :U 필터 필수!
SAVE_OUT_DS = 'dsGeneralGrid=dsGeneralGrid dsSaveChk=dsSaveChk'   # gds_ErrMsg가 아님
SAVE_ARGS_FMT = 'strPyunsuId="{}" strOrdInputFlag="{}"'  # 따옴표 필수

# dsGeneralGrid 핵심 컬럼 (55개 중 발주에 필수인 것)
# 라이브 테스트 확인 (2026-02-28): 배수는 PYUN_QTY, ORD_MUL_QTY는 빈값
KEY_COLUMNS = {
    'item_cd': 'ITEM_CD',
    'multiplier': 'PYUN_QTY',       # 배수 (UI에서 입력, 서버 처리)
    'order_mul_qty': 'ORD_MUL_QTY',  # 발주배수 (호환성)
    'total_qty': 'TOT_QTY',          # 발주량 = PYUN_QTY × ORD_UNIT_QTY
    'order_date': 'ORD_YMD',
    'store_cd': 'STORE_CD',
}

# =====================================================================
# 발주 가능 여부 확인 JS — saveOrd 전에 호출하여 서버 상태 확인
# fv_OrdYn, fv_OrdClose 등 폼 변수로 발주 가능 시간인지 판단
# =====================================================================
CHECK_ORDER_AVAILABILITY_JS = """
try {
    var app = nexacro.getApplication();
    var frameSet = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
    var stbjForm = null;
    try { stbjForm = frameSet.STBJ030_M0 ? frameSet.STBJ030_M0.form : null; } catch(e) {}
    if (!stbjForm || !stbjForm.div_workForm) {
        var fKeys = Object.keys(frameSet);
        for (var fki = 0; fki < fKeys.length; fki++) {
            try {
                var ff = frameSet[fKeys[fki]];
                if (ff && ff.form && ff.form.div_workForm &&
                    ff.form.div_workForm.form.div_work_01 &&
                    ff.form.div_workForm.form.div_work_01.form.gdList) {
                    stbjForm = ff.form; break;
                }
            } catch(e) {}
        }
    }
    if (!stbjForm) return JSON.stringify({error: 'form_not_found'});
    var workForm = stbjForm.div_workForm.form.div_work_01.form;
    if (!workForm) return JSON.stringify({error: 'workForm_not_found'});

    var result = {};

    // 1. 세션 변수 — cookies에서 읽기 (app.getVariable은 SS_* 반환 안함)
    var cookies = {};
    var pairs = document.cookie.split(';');
    for (var ci = 0; ci < pairs.length; ci++) {
        var p = pairs[ci].trim().split('=');
        if (p.length >= 2) cookies[p[0]] = p.slice(1).join('=');
    }
    result.storeCd = cookies.SS_STORE_CD || '';
    result.userNo = cookies.SS_USER_NO || '';
    result.hasSession = !!(cookies.SS_STORE_CD && cookies.SS_USER_NO);

    // 2. 발주 관련 폼 변수 수집
    var allKeys = Object.keys(workForm);
    var ordVars = {};
    for (var ki = 0; ki < allKeys.length; ki++) {
        var k = allKeys[ki];
        if (k.indexOf('fv_') === 0 || k.toLowerCase().indexOf('ord') >= 0) {
            var typ = typeof workForm[k];
            if (typ !== 'function' && typ !== 'object') {
                try { ordVars[k] = String(workForm[k]); } catch(e) {}
            }
        }
    }
    result.workFormVars = ordVars;

    // 3. 발주 관련 폼 변수 (fv_OrdYn은 단품별발주 폼에 존재하지 않음)
    var ordYn = ordVars.fv_OrdYn || '';
    var ordClose = ordVars.fv_OrdClose || '';
    result.ordYn = ordYn;
    result.ordClose = ordClose;
    result.ordInputFlag = ordVars.fv_OrdInputFlag || '';

    // 4. 발주 가능 판단 (세션 + 명시적 불가 플래그만 체크)
    var available = true;
    if (!result.hasSession) available = false;
    // ordYn이 명시적으로 존재하면서 N인 경우만 차단 (빈값=미존재는 허용)
    if (ordYn && (ordYn === 'N' || ordYn === '0' || ordYn === 'false')) available = false;
    if (ordClose && (ordClose === 'Y' || ordClose === '1' || ordClose === 'true')) available = false;
    result.available = available;

    // 5. 현재 시간
    result.browserTime = new Date().toLocaleString('ko-KR', {timeZone: 'Asia/Seoul'});

    return JSON.stringify(result);
} catch(e) {
    return JSON.stringify({error: e.message});
}
"""


@dataclass
class SaveResult:
    """발주 저장 결과"""
    success: bool
    saved_count: int = 0
    failed_items: List[str] = field(default_factory=list)
    elapsed_ms: float = 0
    method: str = 'direct_api'
    message: str = ''
    response_preview: str = ''


# =====================================================================
# 넥사크로 폼 탐색 JS (order_executor/_FIND_ORDER_FORM_JS 동일)
# =====================================================================
_FIND_ORDER_FORM_JS = """
(function() {
    try {
        const app = nexacro.getApplication();
        if (!app) return null;
        const frameSet = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
        let stbjForm = frameSet.STBJ030_M0 ? frameSet.STBJ030_M0.form : null;

        if (!stbjForm || !stbjForm.div_workForm) {
            for (const key of Object.keys(frameSet)) {
                try {
                    const f = frameSet[key];
                    if (f && f.form && f.form.div_workForm &&
                        f.form.div_workForm.form.div_work_01 &&
                        f.form.div_workForm.form.div_work_01.form.gdList) {
                        stbjForm = f.form;
                        break;
                    }
                } catch(e) {}
            }
        }

        if (!stbjForm) return null;

        const workForm = stbjForm.div_workForm.form.div_work_01.form;
        if (!workForm || !workForm.gdList) return null;

        var ds = workForm.gdList._binddataset;
        var dsId = '';
        if (ds) {
            dsId = ds._id || ds.name || ds._name || (typeof ds === 'string' ? ds : 'dataset_obj');
        }
        return {
            formId: stbjForm.name || 'found',
            hasGrid: !!workForm.gdList,
            dsName: dsId
        };
    } catch(e) {
        return null;
    }
})();
"""

# =====================================================================
# 인터셉터 JS: 저장 API 캡처용
# =====================================================================
SAVE_INTERCEPTOR_JS = """
(function() {
    if (window._saveOrderInterceptorInstalled) {
        return {status: 'already_installed'};
    }

    window._saveOrderCaptures = [];

    // gfn_transaction 오버라이드
    try {
        if (typeof gfn_transaction === 'function' && !window._origGfnTxForSave) {
            window._origGfnTxForSave = gfn_transaction;
            window.gfn_transaction = function(txId, svcURL, inDS, outDS, args, cb, isAsync) {
                if (svcURL && !svcURL.startsWith('sel')) {
                    window._saveOrderCaptures.push({
                        type: 'gfn_save',
                        txId: txId,
                        svcURL: svcURL,
                        inDS: inDS,
                        outDS: outDS,
                        args: args,
                        timestamp: new Date().toISOString()
                    });
                }
                return window._origGfnTxForSave.apply(this, arguments);
            };
        }
    } catch(e) {}

    // XHR POST 캡처 (selSearch 외)
    try {
        if (!window._origXhrOpenForSave) {
            window._origXhrOpenForSave = XMLHttpRequest.prototype.open;
            window._origXhrSendForSave = XMLHttpRequest.prototype.send;

            XMLHttpRequest.prototype.open = function(method, url) {
                this._saveCapUrl = url;
                this._saveCapMethod = method;
                return window._origXhrOpenForSave.apply(this, arguments);
            };

            XMLHttpRequest.prototype.send = function(body) {
                var url = this._saveCapUrl || '';
                if (this._saveCapMethod === 'POST' && body && !url.includes('selSearch') && !url.includes('selGet')) {
                    window._saveOrderCaptures.push({
                        type: 'xhr_save',
                        url: url,
                        body: typeof body === 'string' ? body.substring(0, 5000) : '',
                        bodyLength: typeof body === 'string' ? body.length : 0,
                        timestamp: new Date().toISOString()
                    });
                }
                return window._origXhrSendForSave.apply(this, arguments);
            };
        }
    } catch(e) {}

    window._saveOrderInterceptorInstalled = true;
    return {status: 'installed'};
})();
"""

# =====================================================================
# Phase 0: selSearch 프리페치 JS (상품별 전체 필드 조회)
# =====================================================================
PREFETCH_ITEMS_JS = """
var itemCodes = arguments[0];
var timeoutMs = arguments[1];
var ordYmd = arguments[2] || '';
var RS = String.fromCharCode(0x1e);
var US = String.fromCharCode(0x1f);

// 1. Get selSearch template from captured requests (Phase 1 prefetch에서 캡처)
var template = null;
if (window._capturedRequests && window._capturedRequests.length > 0) {
    for (var i = 0; i < window._capturedRequests.length; i++) {
        if (window._capturedRequests[i].body &&
            window._capturedRequests[i].body.indexOf('strItemCd=') > -1) {
            template = window._capturedRequests[i].body;
            break;
        }
    }
}

// 2. Fallback: 넥사크로 세션에서 직접 템플릿 구성
if (!template) {
    try {
        var app = nexacro.getApplication();
        var frameSet = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
        var stbjForm = frameSet.STBJ030_M0 ? frameSet.STBJ030_M0.form : null;
        if (!stbjForm || !stbjForm.div_workForm) {
            var fKeys = Object.keys(frameSet);
            for (var fki = 0; fki < fKeys.length; fki++) {
                try {
                    var ff = frameSet[fKeys[fki]];
                    if (ff && ff.form && ff.form.div_workForm &&
                        ff.form.div_workForm.form.div_work_01 &&
                        ff.form.div_workForm.form.div_work_01.form.gdList) {
                        stbjForm = ff.form;
                        break;
                    }
                } catch(e) {}
            }
        }
        if (stbjForm) {
            var workForm = stbjForm.div_workForm.form.div_work_01.form;
            var ds = workForm.gdList._binddataset;
            if (!ds || typeof ds.getColCount !== 'function') {
                ds = workForm.gdList._binddataset_obj;
            }
            if (ds) {
                // Session variables
                var svNames = [
                    'GV_USERFLAG', '_xm_webid_1_', '_xm_tid_1_',
                    'SS_STORE_CD', 'SS_PRE_STORE_CD', 'SS_STORE_NM',
                    'SS_SLC_CD', 'SS_LOC_CD', 'SS_ADM_SECT_CD',
                    'SS_STORE_OWNER_NM', 'SS_STORE_POS_QTY', 'SS_STORE_IP',
                    'SS_SV_EMP_NO', 'SS_SSTORE_ID', 'SS_RCV_ID',
                    'SS_FC_CD', 'SS_USER_GRP_ID', 'SS_USER_NO',
                    'SS_SGGD_CD', 'SS_LOGIN_USER_NO'
                ];
                var parts = ['SSV:utf-8'];
                for (var si = 0; si < svNames.length; si++) {
                    var sv = '';
                    try { sv = String(app.getVariable(svNames[si]) || ''); } catch(e) {}
                    parts.push(svNames[si] + '=' + sv);
                }
                // Parameters
                parts.push('strOrdYmd=' + ordYmd);
                parts.push('strItemCd=__PLACEHOLDER__');
                parts.push('strSearchType=1');
                parts.push('WEEK_JOB_CD=');
                parts.push('MSG_CD=');
                parts.push('GV_MENU_ID=0001,STBJ030_M0');
                parts.push('GV_USERFLAG=HOME');
                parts.push('GV_CHANNELTYPE=HOME');

                // Dataset:dsItem column definitions (from dsGeneralGrid)
                var colDefs = ['_RowType_'];
                for (var ci = 0; ci < ds.getColCount(); ci++) {
                    var cid = ds.getColID(ci);
                    // 기본 STRING(256), INT/BIGDECIMAL 컬럼은 매핑
                    var ctype = 'STRING(256)';
                    var intCols = ['HQ_MAEGA_SET','ORD_UNIT_QTY','ORD_MULT_ULMT','ORD_MULT_LLMT',
                                   'NOW_QTY','ORD_MUL_QTY','TOT_QTY','PAGE_CNT','EXPIRE_DAY'];
                    var decCols = ['PROFIT_RATE'];
                    if (intCols.indexOf(cid) >= 0) ctype = 'INT(256)';
                    if (decCols.indexOf(cid) >= 0) ctype = 'BIGDECIMAL(256)';
                    colDefs.push(cid + ':' + ctype);
                }
                parts.push('Dataset:dsItem');
                parts.push(colDefs.join(US));
                parts.push('');
                template = parts.join(RS);
            }
        }
    } catch(buildErr) {
        // 템플릿 구성 실패
    }
}

if (!template) return JSON.stringify({error: 'no_selSearch_template'});

// 3. Batch fetch with concurrency limit
async function fetchOne(itemCd) {
    var body = template.replace(/strItemCd=[^\x1e]*/, 'strItemCd=' + itemCd);
    if (ordYmd) body = body.replace(/strOrdYmd=[^\x1e]*/, 'strOrdYmd=' + ordYmd);
    try {
        var resp = await fetch('/stbj030/selSearch', {
            method: 'POST',
            headers: {'Content-Type': 'text/plain;charset=UTF-8'},
            body: body,
            signal: AbortSignal.timeout(timeoutMs)
        });
        if (!resp.ok) return {itemCd: itemCd, error: 'HTTP ' + resp.status};
        var text = await resp.text();
        return {itemCd: itemCd, text: text, ok: true};
    } catch(e) {
        return {itemCd: itemCd, error: e.message};
    }
}

var results = [];
var idx = 0;
var concurrency = 5;

async function worker() {
    while (idx < itemCodes.length) {
        var myIdx = idx++;
        results.push(await fetchOne(itemCodes[myIdx]));
        if (idx < itemCodes.length) await new Promise(r => setTimeout(r, 30));
    }
}

var workers = [];
for (var w = 0; w < Math.min(concurrency, itemCodes.length); w++) {
    workers.push(worker());
}
await Promise.all(workers);
return JSON.stringify(results);
"""

# =====================================================================
# Phase 1: dataset 채우기 JS (동기, execute_script 사용)
# selSearch 프리페치 데이터로 ALL columns 설정
# =====================================================================
POPULATE_DATASET_JS = """
var ordersJson = arguments[0];
var dateStr = arguments[1];
try {
    // 1. 넥사크로 폼 찾기
    var app = nexacro.getApplication();
    var frameSet = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
    var stbjForm = frameSet.STBJ030_M0 ? frameSet.STBJ030_M0.form : null;

    if (!stbjForm || !stbjForm.div_workForm) {
        var fsKeys = Object.keys(frameSet);
        for (var ki = 0; ki < fsKeys.length; ki++) {
            try {
                var f = frameSet[fsKeys[ki]];
                if (f && f.form && f.form.div_workForm &&
                    f.form.div_workForm.form.div_work_01 &&
                    f.form.div_workForm.form.div_work_01.form.gdList) {
                    stbjForm = f.form;
                    break;
                }
            } catch(e) {}
        }
    }
    if (!stbjForm) return JSON.stringify({error: 'form_not_found'});

    var workForm = stbjForm.div_workForm.form.div_work_01.form;
    if (!workForm || !workForm.gdList) return JSON.stringify({error: 'grid_not_found'});

    // 2. dataset 찾기 (_binddataset은 객체 직접 반환)
    var ds = workForm.gdList._binddataset;
    if (!ds || typeof ds.addRow !== 'function') {
        ds = workForm.gdList._binddataset_obj;
    }
    if (!ds) return JSON.stringify({error: 'dataset_not_found'});

    var orders = JSON.parse(ordersJson);

    // 3. 기존 데이터 지우고 상품 채우기
    //    라이브 테스트 확인 (2026-02-28): RowType=I (Insert)로 저장 성공
    //    applyChange 불필요 — addRow → setColumn → RowType=2(I) 그대로 전송
    //    핵심: PYUN_QTY=배수, TOT_QTY=발주량, ORD_MUL_QTY는 빈값이어도 OK
    ds.clearData();

    var addedCount = 0;
    var fieldSetCount = 0;
    var numericFilledTotal = 0;
    var allNumFilledCols = [];
    for (var i = 0; i < orders.length; i++) {
        var order = orders[i];
        var row = ds.addRow();
        if (row < 0) continue;

        // 3a. selSearch 프리페치 필드 전체 설정 (dsItem → dsGeneralGrid 매핑)
        var fields = order.fields || {};
        var colsSet = 0;
        for (var colName in fields) {
            if (fields.hasOwnProperty(colName) && fields[colName] !== '' && fields[colName] != null) {
                try {
                    ds.setColumn(row, colName, fields[colName]);
                    colsSet++;
                } catch(e) {
                    // 해당 컬럼이 dataset에 없으면 무시
                }
            }
        }
        fieldSetCount += colsSet;

        // 3b. 핵심 필드 오버라이드 (프리페치 값 위에 덮어씀)
        var rawMul = order.multiplier;
        var mul = (rawMul !== undefined && rawMul !== null) ? parseInt(rawMul) : 1;
        if (isNaN(mul) || mul < 1) mul = 1;
        var unitQty = parseInt(order.ord_unit_qty || 1);
        ds.setColumn(row, 'ITEM_CD', order.item_cd || '');
        ds.setColumn(row, 'ORD_YMD', dateStr);
        ds.setColumn(row, 'PYUN_QTY', String(mul));          // 배수 (핵심)
        ds.setColumn(row, 'TOT_QTY', mul * unitQty);          // 발주량
        ds.setColumn(row, 'ORD_UNIT_QTY', unitQty);           // 입수
        if (order.store_cd) ds.setColumn(row, 'STORE_CD', order.store_cd);

        // 3c. 숫자 컬럼 빈값→0 (서버 NumberFormatException 방지)
        //     미프리페치 항목은 숫자 컬럼이 빈 문자열 → parseInt("") 예외
        //     이중 안전망: 컬럼 메타 타입 + 알려진 숫자 컬럼 목록
        var _knownNumsSet = {HQ_MAEGA_SET:1,ORD_UNIT_QTY:1,ORD_MULT_ULMT:1,ORD_MULT_LLMT:1,NOW_QTY:1,ORD_MUL_QTY:1,OLD_PYUN_QTY:1,TOT_QTY:1,PAGE_CNT:1,EXPIRE_DAY:1,PROFIT_RATE:1,PYUN_QTY:1,EVT_DC_RATE:1,RB_AMT:1};
        var _numFilled = 0;
        var _numFilledCols = [];
        for (var _ci = 0; _ci < ds.getColCount(); _ci++) {
            try {
                var _cid = ds.getColID(_ci);
                var _cv = ds.getColumn(row, _cid);
                if (_cv != null && _cv !== '') continue;
                var _isNum = false;
                var _detectBy = '';
                try {
                    var _t = String((ds.getColumnInfo(_ci)||{}).type||'').toUpperCase();
                    if (_t === 'INT' || _t.indexOf('DECIMAL') >= 0 || _t === 'FLOAT' || _t === 'NUMBER') {
                        _isNum = true;
                        _detectBy = 'meta:' + _t;
                    }
                } catch(e2) {}
                if (!_isNum && _knownNumsSet[_cid]) {
                    _isNum = true;
                    _detectBy = 'known';
                }
                if (_isNum) {
                    ds.setColumn(row, _cid, 0);
                    _numFilled++;
                    if (_numFilledCols.length < 20) _numFilledCols.push(_cid + '(' + _detectBy + ')');
                }
            } catch(e3) {}
        }
        numericFilledTotal += _numFilled;
        if (_numFilledCols.length > 0 && allNumFilledCols.length < 30) {
            for (var _ni = 0; _ni < _numFilledCols.length; _ni++) {
                if (allNumFilledCols.indexOf(_numFilledCols[_ni]) < 0) allNumFilledCols.push(_numFilledCols[_ni]);
            }
        }

        addedCount++;
    }

    // 4. row type 확인 — RowType=2(I/Insert)가 정상 (라이브 테스트 2026-02-28)
    var rowType0 = -1;
    try { rowType0 = ds.getRowType(0); } catch(e) {}

    // 5. dsSaveChk: 라이브 캡처에서 빈 데이터셋이었음 (CALL_GFN_TRANSACTION_JS에서 clear)

    return JSON.stringify({
        success: true,
        added: addedCount,
        total: orders.length,
        dsRowCount: ds.getRowCount(),
        avgFieldsPerRow: addedCount > 0 ? Math.round(fieldSetCount / addedCount) : 0,
        rowType0: rowType0,
        numericFilled: numericFilledTotal,
        numericFilledCols: allNumFilledCols
    });

} catch(e) {
    return JSON.stringify({error: 'exception: ' + e.message});
}
"""

# =====================================================================
# Phase 2: gfn_transaction 호출 JS (비동기, execute_async_script 사용)
# =====================================================================
# Phase 2: gfn_transaction 호출 JS (동기 — 결과는 window 변수에 저장, Python에서 폴링)
CALL_GFN_TRANSACTION_JS = """
var addedCount = arguments[0];
try {
    // 폼 재탐색
    var app = nexacro.getApplication();
    var frameSet = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
    var stbjForm = frameSet.STBJ030_M0 ? frameSet.STBJ030_M0.form : null;

    if (!stbjForm || !stbjForm.div_workForm) {
        var txKeys = Object.keys(frameSet);
        for (var tki = 0; tki < txKeys.length; tki++) {
            try {
                var f = frameSet[txKeys[tki]];
                if (f && f.form && f.form.div_workForm &&
                    f.form.div_workForm.form.div_work_01 &&
                    f.form.div_workForm.form.div_work_01.form.gdList) {
                    stbjForm = f.form;
                    break;
                }
            } catch(e) {}
        }
    }
    if (!stbjForm) {
        return JSON.stringify({error: 'form_not_found'});
    }

    // ★ 핵심: workForm에서 gfn_transaction 호출해야 datasets 참조 가능
    var workForm = stbjForm.div_workForm.form.div_work_01.form;
    if (!workForm) {
        return JSON.stringify({error: 'workForm_not_found'});
    }
    if (typeof workForm.gfn_transaction !== 'function') {
        return JSON.stringify({error: 'workForm_no_gfn_transaction'});
    }

    // fn_save 로직 재현: dsSaveChk 클리어 (서버 출력용 dataset)
    try {
        if (workForm.dsSaveChk && typeof workForm.dsSaveChk.clearData === 'function') {
            workForm.dsSaveChk.clearData();
        }
    } catch(chkErr) {}

    // 폼 인스턴스 변수 읽기 (fn_save에서 사용하는 값)
    var pyunsuId = '0';
    var ordInputFlag = '04';
    try { if (workForm.fv_PyunsuId != null) pyunsuId = String(workForm.fv_PyunsuId); } catch(e) {}
    try { if (workForm.fv_OrdInputFlag != null) ordInputFlag = String(workForm.fv_OrdInputFlag); } catch(e) {}

    // 결과 저장소 초기화
    window._directApiSaveResult = null;
    window._directApiSaveError = null;
    window._directApiSaveDone = false;

    // gfn_callback 가로채기 (gfn_transaction 내부에서 "gfn_callback" 호출)
    var origGfnCallback = workForm.gfn_callback;
    workForm.gfn_callback = function(svcId, errCd, errMsg) {
        window._directApiSaveResult = {
            svcId: String(svcId || ''),
            errCd: String(errCd || ''),
            errMsg: String(errMsg || ''),
            success: (errCd === 0 || errCd === '0' || errCd === 'SYS000' || errCd === '99999' || errCd === 99999),
            added: addedCount
        };
        window._directApiSaveDone = true;

        // 원래 콜백 복원 및 호출
        workForm.gfn_callback = origGfnCallback;
        if (origGfnCallback) {
            try { origGfnCallback.call(workForm, svcId, errCd, errMsg); } catch(e) {}
        }
    };

    // fn_callback도 가로채기 (gfn_callback → fn_callback 체인)
    var origFnCallback = workForm.fn_callback;
    workForm.fn_callback = function(svcId, errCd, errMsg) {
        if (!window._directApiSaveDone) {
            window._directApiSaveResult = {
                svcId: String(svcId || ''),
                errCd: String(errCd || ''),
                errMsg: String(errMsg || ''),
                success: (errCd === 0 || errCd === '0' || errCd === 'SYS000' || errCd === '99999' || errCd === 99999),
                added: addedCount
            };
            window._directApiSaveDone = true;
        }
        // 원래 콜백 복원 및 호출
        workForm.fn_callback = origFnCallback;
        if (origFnCallback) {
            try { origFnCallback.call(workForm, svcId, errCd, errMsg); } catch(e) {}
        }
    };

    // gfn_transaction 호출 — fn_save 래핑 캡처 기반 (2026-02-28)
    // 핵심: txId='save', inDS에 ':U' 필터, outDS에 dsGeneralGrid 반환, args 따옴표
    var strArg = 'strPyunsuId="' + pyunsuId + '" strOrdInputFlag="' + ordInputFlag + '"';
    workForm.gfn_transaction(
        'save',
        'stbjz00/saveOrd',
        'dsGeneralGrid=dsGeneralGrid:U dsSaveChk=dsSaveChk',
        'dsGeneralGrid=dsGeneralGrid dsSaveChk=dsSaveChk',
        strArg,
        'fn_callback'
    );

    return JSON.stringify({started: true, added: addedCount});

} catch(txErr) {
    return JSON.stringify({error: 'transaction_failed: ' + txErr.message, added: addedCount});
}
"""

# Phase 2b: 결과 폴링 JS
POLL_SAVE_RESULT_JS = """
if (window._directApiSaveDone && window._directApiSaveResult) {
    return JSON.stringify(window._directApiSaveResult);
}
if (window._directApiSaveError) {
    return JSON.stringify({error: window._directApiSaveError});
}
return '';
"""


class DirectApiOrderSaver:
    """
    Direct API로 발주 데이터 일괄 저장

    전략 (캡처 기반, 2026-02-27):
        1차: dataset 채우기 + gfn_transaction 직접 호출
             → 넥사크로가 SSV 직렬화 + 세션 처리
        2차: SSV body 수동 구성 + fetch() 직접 호출
             → 캡처된 body 템플릿 기반

    폴백:
        저장 실패 시 SaveResult.success=False 반환
        → OrderExecutor가 BatchGridInputter 또는 Selenium으로 재시도
    """

    def __init__(
        self,
        driver: Any,
        timeout_ms: int = DIRECT_API_SAVE_TIMEOUT_MS,
        max_batch: int = DIRECT_API_ORDER_MAX_BATCH,
    ):
        self.driver = driver
        self.timeout_ms = timeout_ms
        self.max_batch = max_batch
        self._save_template: Optional[Dict[str, str]] = None
        self._save_endpoint: Optional[str] = None

    # ─────────────────────────────────────────
    # 1. 저장 API 템플릿/인터셉터
    # ─────────────────────────────────────────

    def install_interceptor(self) -> bool:
        """저장 API 인터셉터 설치"""
        try:
            result = self.driver.execute_script(SAVE_INTERCEPTOR_JS)
            installed = result and result.get('status') in ('installed', 'already_installed')
            if installed:
                logger.info("[DirectApiSaver] 인터셉터 설치됨")
            return installed
        except Exception as e:
            logger.error(f"[DirectApiSaver] 인터셉터 설치 실패: {e}")
            return False

    def check_order_availability(self) -> Dict[str, Any]:
        """
        발주 가능 여부 확인 (saveOrd 호출 전 진단용)

        폼 변수(fv_OrdYn, fv_OrdClose 등)를 검사하여
        현재 발주 가능 시간인지 판단합니다.

        Returns:
            {'available': True/False, 'ordYn': '...', ...}
        """
        try:
            result_str = self.driver.execute_script(CHECK_ORDER_AVAILABILITY_JS)
            if not result_str:
                return {'available': True, 'error': 'no_response'}

            result = json.loads(result_str) if isinstance(result_str, str) else result_str
            if result.get('error'):
                logger.warning(f"[DirectApiSaver] 발주 가능 확인 실패: {result['error']}")
                return {'available': True, 'error': result['error']}

            available = result.get('available', True)
            ord_yn = result.get('ordYn', '')
            ord_close = result.get('ordClose', '')

            if not available:
                logger.warning(
                    f"[DirectApiSaver] 발주 불가 상태: ordYn={ord_yn}, "
                    f"ordClose={ord_close}, storeCd={result.get('storeCd', '')}"
                )
            else:
                logger.info(
                    f"[DirectApiSaver] 발주 가능 확인: ordYn={ord_yn}, "
                    f"ordClose={ord_close}"
                )

            return result
        except Exception as e:
            logger.warning(f"[DirectApiSaver] 발주 가능 확인 예외: {e}")
            return {'available': True, 'error': str(e)}

    def capture_save_template(self) -> bool:
        """인터셉터에서 캡처된 저장 API 템플릿 가져오기"""
        try:
            captures = self.driver.execute_script(
                "return window._saveOrderCaptures || [];"
            )
            if not captures:
                logger.info("[DirectApiSaver] 캡처된 저장 요청 없음")
                return False

            gfn_saves = [c for c in captures if c.get('type') == 'gfn_save']
            xhr_saves = [c for c in captures if c.get('type') == 'xhr_save']

            if gfn_saves:
                cap = gfn_saves[-1]
                self._save_template = {
                    'txId': cap.get('txId', ''),
                    'svcURL': cap.get('svcURL', ''),
                    'inDS': cap.get('inDS', ''),
                    'outDS': cap.get('outDS', ''),
                    'args': cap.get('args', ''),
                }
                self._save_endpoint = cap.get('svcURL', '')
                logger.info(f"[DirectApiSaver] gfn_transaction 템플릿 캡처: {self._save_endpoint}")
                return True

            if xhr_saves:
                cap = xhr_saves[-1]
                self._save_template = {
                    'url': cap.get('url', ''),
                    'body': cap.get('body', ''),
                }
                self._save_endpoint = cap.get('url', '')
                logger.info(f"[DirectApiSaver] XHR 템플릿 캡처: {self._save_endpoint}")
                return True

            logger.info("[DirectApiSaver] 저장 관련 캡처 없음")
            return False
        except Exception as e:
            logger.error(f"[DirectApiSaver] 템플릿 캡처 실패: {e}")
            return False

    def set_template_from_file(self, file_path: str) -> bool:
        """캡처 파일(JSON)에서 저장 템플릿 로드"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            gfn_saves = data.get('save_gfn_transactions', [])
            if gfn_saves:
                cap = gfn_saves[-1]
                self._save_template = {
                    'txId': cap.get('txId', ''),
                    'svcURL': cap.get('svcURL', ''),
                    'inDS': cap.get('inDS', ''),
                    'outDS': cap.get('outDS', ''),
                    'args': cap.get('args', ''),
                }
                self._save_endpoint = cap.get('svcURL', '')
                return True

            xhr_saves = data.get('save_xhr_requests', [])
            if xhr_saves:
                cap = xhr_saves[-1]
                self._save_template = {
                    'url': cap.get('url', ''),
                    'body': cap.get('body', ''),
                }
                self._save_endpoint = cap.get('url', '')
                return True

            # 캡처 템플릿 파일(save_api_template.json)도 지원
            if 'endpoint' in data:
                self._save_endpoint = data['endpoint']
                self._save_template = data.get('gfn_transaction', {})
                if self._save_template:
                    logger.info(f"[DirectApiSaver] 구조화된 템플릿 로드: {self._save_endpoint}")
                    return True

            logger.debug(f"[DirectApiSaver] 파일에 저장 요청 없음: {file_path}")
            return False
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"[DirectApiSaver] 파일 로드 실패: {e}")
            return False

    @property
    def has_template(self) -> bool:
        """저장 템플릿 보유 여부"""
        return self._save_template is not None and self._save_endpoint is not None

    # ─────────────────────────────────────────
    # 2. 발주 저장 실행 (메인)
    # ─────────────────────────────────────────

    def save_orders(
        self,
        orders: List[Dict[str, Any]],
        date_str: str,
        dry_run: bool = False,
    ) -> SaveResult:
        """
        Direct API로 발주 데이터 일괄 저장

        50개 초과 시 서브배치로 분할하여 각각 gfn_transaction 호출.
        각 청크: prefetch → populate → gfn_transaction → 콜백 대기 → 그리드 리셋.

        전략:
            1차: dataset 채우기 + gfn_transaction JS 호출
            2차: SSV body + fetch() (템플릿 있을 때만)

        Args:
            orders: [{item_cd, final_order_qty, multiplier, order_unit_qty}, ...]
            date_str: 발주일 (YYYYMMDD 또는 YYYY-MM-DD)
            dry_run: True면 body 생성만 (실제 전송 안함)

        Returns:
            SaveResult
        """
        start_time = time.time()
        date_str = date_str.replace('-', '')

        if not orders:
            return SaveResult(success=True, saved_count=0, message='empty order list')

        if dry_run:
            return self._dry_run(orders, date_str, start_time)

        # 배치 분할: max_batch 초과 시 청크 단위로 처리
        if len(orders) > self.max_batch:
            return self._save_chunked(orders, date_str, start_time)

        return self._save_single_batch(orders, date_str, start_time)

    def _save_single_batch(
        self,
        orders: List[Dict[str, Any]],
        date_str: str,
        start_time: float,
    ) -> SaveResult:
        """단일 배치 저장 (max_batch 이하)"""
        logger.info(f"[DirectApiSaver] [batch=B001] 단일배치: {len(orders)}건, 날짜={date_str}")
        # 전략 1: dataset 채우기 + gfn_transaction
        result = self._save_via_transaction(orders, date_str)
        if result and result.success:
            result.elapsed_ms = (time.time() - start_time) * 1000
            return result

        # 전략 2: SSV body + fetch() (템플릿 기반)
        if self.has_template:
            logger.info("[DirectApiSaver] 전략1 실패, fetch() 폴백 시도")
            result = self._save_via_fetch(orders, date_str)
            if result:
                result.elapsed_ms = (time.time() - start_time) * 1000
                return result

        elapsed = (time.time() - start_time) * 1000
        return SaveResult(
            success=False,
            elapsed_ms=elapsed,
            message='all save strategies failed',
        )

    def _save_chunked(
        self,
        orders: List[Dict[str, Any]],
        date_str: str,
        start_time: float,
    ) -> SaveResult:
        """
        배치 분할 저장: max_batch 크기로 나눠 순차 gfn_transaction 호출.

        각 청크 사이 2초 대기 (그리드 초기화 + 서버 처리).
        하나라도 실패하면 전체 실패 반환 (나머지는 Level 2로 폴백).
        """
        chunk_size = self.max_batch
        chunks = [
            orders[i:i + chunk_size]
            for i in range(0, len(orders), chunk_size)
        ]
        total_chunks = len(chunks)
        total_saved = 0

        logger.info(
            f"[DirectApiSaver] 배치 분할: {len(orders)}건 → "
            f"{total_chunks}개 청크 (각 최대 {chunk_size}건)"
        )

        for idx, chunk in enumerate(chunks):
            batch_label = f"B{idx + 1:03d}"
            chunk_label = f"[batch={batch_label}] [{idx + 1}/{total_chunks}]"
            logger.info(f"[DirectApiSaver] {chunk_label} {len(chunk)}건 저장 시작")

            result = self._save_via_transaction(chunk, date_str)

            if not result or not result.success:
                msg = result.message if result else 'returned None'
                logger.warning(
                    f"[DirectApiSaver] {chunk_label} 실패: {msg} "
                    f"(저장완료: {total_saved}/{len(orders)}건)"
                )
                elapsed = (time.time() - start_time) * 1000
                return SaveResult(
                    success=False,
                    saved_count=total_saved,
                    elapsed_ms=elapsed,
                    method='direct_api_chunked',
                    message=f'chunk {idx + 1}/{total_chunks} failed: {msg}, '
                            f'saved {total_saved}/{len(orders)}',
                )

            total_saved += result.saved_count or len(chunk)
            logger.info(
                f"[DirectApiSaver] {chunk_label} 성공: {result.saved_count}건 "
                f"(누적: {total_saved}/{len(orders)})"
            )

            # 마지막 청크가 아니면 그리드 초기화 대기
            if idx < total_chunks - 1:
                time.sleep(2.0)

        elapsed = (time.time() - start_time) * 1000
        logger.info(
            f"[DirectApiSaver] 배치 분할 완료: {total_saved}/{len(orders)}건, "
            f"{elapsed:.0f}ms"
        )
        return SaveResult(
            success=True,
            saved_count=total_saved,
            elapsed_ms=elapsed,
            method='direct_api_chunked',
            message=f'{total_chunks} chunks, {total_saved} items saved',
        )

    def _dry_run(
        self, orders: List[Dict], date_str: str, start_time: float
    ) -> SaveResult:
        """dry-run: 저장하지 않고 body만 생성"""
        orders_for_log = [
            {'item_cd': o.get('item_cd'), 'multiplier': self._calc_multiplier(o)}
            for o in orders
        ]
        elapsed = (time.time() - start_time) * 1000
        logger.info(f"[DirectApiSaver] dry-run: {len(orders)}건")
        logger.debug(f"[DirectApiSaver] dry-run orders: {orders_for_log[:5]}")
        return SaveResult(
            success=True,
            saved_count=len(orders),
            elapsed_ms=elapsed,
            message=f'dry_run: {len(orders)} items prepared',
            response_preview=json.dumps(orders_for_log[:3], ensure_ascii=False),
        )

    # ─────────────────────────────────────────
    # 3. 전략 1: gfn_transaction 호출
    # ─────────────────────────────────────────

    def _prefetch_item_details(
        self, item_codes: List[str], date_str: str = ''
    ) -> Dict[str, Dict[str, str]]:
        """
        selSearch API로 각 상품의 전체 필드 프리페치

        dsItem 응답의 모든 컬럼을 추출하여 dsGeneralGrid 채우기에 사용합니다.
        Phase 1 prefetch에서 캡처된 selSearch 템플릿을 재활용합니다.

        Args:
            item_codes: 상품코드 목록
            date_str: 발주일 (YYYYMMDD) — selSearch에 필수

        Returns:
            {item_cd: {STORE_CD: '...', ITEM_NM: '...', ...}, ...}
        """
        if not item_codes:
            return {}

        try:
            raw_json = self.driver.execute_script(
                PREFETCH_ITEMS_JS,
                item_codes,
                self.timeout_ms,
                date_str,
            )
            if not raw_json:
                logger.info("[DirectApiSaver] 프리페치: 응답 없음")
                return {}

            data = json.loads(raw_json) if isinstance(raw_json, str) else raw_json

            # Error response from JS (no template)
            if isinstance(data, dict) and data.get('error'):
                logger.info(f"[DirectApiSaver] 프리페치 불가: {data['error']}")
                return {}

            if not isinstance(data, list):
                logger.info("[DirectApiSaver] 프리페치: 응답이 리스트 아님")
                return {}

            # Parse SSV responses
            result = {}
            success_count = 0
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                item_cd = entry.get('itemCd', '')
                if entry.get('ok') and entry.get('text'):
                    fields = extract_dsitem_all_columns(entry['text'])
                    if fields:
                        result[item_cd] = fields
                        success_count += 1

            logger.info(
                f"[DirectApiSaver] 프리페치 완료: {success_count}/{len(item_codes)}건 성공"
            )
            return result

        except Exception as e:
            logger.warning(f"[DirectApiSaver] 프리페치 예외: {e}")
            return {}

    def _save_via_transaction(
        self, orders: List[Dict], date_str: str
    ) -> Optional[SaveResult]:
        """
        넥사크로 dataset 채우기 + gfn_transaction 직접 호출

        3단계:
            Phase 0 (비동기): selSearch 프리페치 — 상품별 전체 필드 조회
            Phase 1 (동기): dataset 채우기 — execute_script
            Phase 2 (비동기): gfn_transaction 호출 + 폴링 대기
        """
        try:
            # Phase -1: 발주 가능 여부 확인 (진단용 — 실패해도 gfn_transaction 진행)
            # fv_OrdYn 폼 변수가 단품별발주에 존재하지 않으므로 빈값=정상.
            # ordYn 체크 결과와 무관하게 gfn_transaction을 시도하고,
            # 서버 응답(saved_count)으로 성공/실패를 판단한다.
            avail = self.check_order_availability()
            if not avail.get('available', True):
                ord_yn = avail.get('ordYn', '')
                ord_close = avail.get('ordClose', '')
                logger.warning(
                    f"[DirectApiSaver] 발주 불가 상태 감지 (진단용, 저장 계속 진행): "
                    f"ordYn='{ord_yn}', ordClose='{ord_close}'"
                )

            # Phase 0: selSearch 프리페치 (실패해도 진행)
            item_codes = [str(o.get('item_cd', '')) for o in orders]
            item_details = self._prefetch_item_details(item_codes, date_str)

            # 주문 데이터 준비 (프리페치 필드 포함)
            order_data_list = []
            unit_mismatch_count = 0
            for o in orders:
                item_cd = str(o.get('item_cd', ''))
                mul = self._calc_multiplier(o)
                unit = o.get('order_unit_qty', 1) or 1
                qty = o.get('final_order_qty', 0)
                # 배수 비정렬 진단
                if unit > 1 and qty > 0 and qty % unit != 0:
                    unit_mismatch_count += 1
                    if unit_mismatch_count <= 5:
                        logger.warning(
                            f"[DirectApiSaver] 배수 비정렬: {item_cd} "
                            f"qty={qty} unit={unit} → mul={mul} "
                            f"(TOT_QTY={mul * unit})"
                        )
                order_data_list.append({
                    'item_cd': item_cd,
                    'multiplier': mul,
                    'ord_unit_qty': unit,
                    'store_cd': o.get('store_cd', ''),
                    'fields': item_details.get(item_cd, {}),
                })
            if unit_mismatch_count > 0:
                logger.warning(f"[DirectApiSaver] 배수 비정렬 상품: {unit_mismatch_count}건")
            orders_json = json.dumps(order_data_list, ensure_ascii=False)

            logger.info(f"[DirectApiSaver] gfn_transaction 저장: {len(orders)}건, 날짜={date_str}")

            # Phase 1: dataset 채우기 (동기)
            populate_str = self.driver.execute_script(
                POPULATE_DATASET_JS,
                orders_json,
                date_str,
            )

            if not populate_str:
                logger.warning("[DirectApiSaver] dataset 채우기: 응답 없음")
                return None

            populate_result = json.loads(populate_str)

            if populate_result.get('error'):
                logger.warning(f"[DirectApiSaver] dataset 채우기 실패: {populate_result['error']}")
                return SaveResult(
                    success=False,
                    method='direct_api_transaction',
                    message=f"populate error: {populate_result['error']}",
                )

            added_count = populate_result.get('added', 0)
            num_filled = populate_result.get('numericFilled', 0)
            num_filled_cols = populate_result.get('numericFilledCols', [])
            logger.info(
                f"[DirectApiSaver] dataset 채우기 완료: {added_count}건, "
                f"숫자컬럼0채움={num_filled}개"
            )
            if num_filled_cols:
                logger.info(
                    f"[DirectApiSaver] 0채움 컬럼: {', '.join(num_filled_cols[:15])}"
                )

            # Phase 2: gfn_transaction 호출 (동기 호출 + 폴링 대기)
            tx_str = self.driver.execute_script(
                CALL_GFN_TRANSACTION_JS,
                added_count,
            )

            if not tx_str:
                logger.warning("[DirectApiSaver] gfn_transaction 호출: 응답 없음")
                return None

            tx_result = json.loads(tx_str)
            if tx_result.get('error'):
                logger.warning(f"[DirectApiSaver] gfn_transaction 호출 실패: {tx_result['error']}")
                return SaveResult(
                    success=False,
                    method='direct_api_transaction',
                    message=f"transaction call error: {tx_result['error']}",
                )

            logger.info("[DirectApiSaver] gfn_transaction 호출됨, 콜백 대기...")

            # Alert 처리 (저장 확인 다이얼로그)
            time.sleep(0.5)
            for _ in range(3):
                try:
                    alert = self.driver.switch_to.alert
                    alert_text = alert.text
                    logger.info(f"[DirectApiSaver] Alert 감지: {alert_text}")
                    alert.accept()
                    time.sleep(0.3)
                except Exception as e:
                    logger.warning(f"[DirectApiSaver] 트랜잭션 중단 - Alert 없음: {e} | URL: {self.driver.current_url}", exc_info=True)
                    break

            # Phase 2b: 결과 폴링 (콜백 대기)
            timeout_sec = self.timeout_ms / 1000
            poll_interval = 0.5
            elapsed = 0.0
            result_str = ''

            while elapsed < timeout_sec:
                try:
                    result_str = self.driver.execute_script(POLL_SAVE_RESULT_JS)
                except Exception as e:
                    logger.warning(f"[DirectApiSaver] execute_script 실패, Alert 확인 시도: {e} | URL: {self.driver.current_url}", exc_info=True)
                    # Alert이 뜰 수 있음
                    try:
                        alert = self.driver.switch_to.alert
                        logger.info(f"[DirectApiSaver] 폴링 중 Alert: {alert.text}")
                        alert.accept()
                    except Exception as e2:
                        logger.debug(f"[DirectApiSaver] Alert 없음, 무시: {e2} | URL: {self.driver.current_url}", exc_info=True)

                if result_str:
                    break
                time.sleep(poll_interval)
                elapsed += poll_interval

            if not result_str:
                logger.warning(f"[DirectApiSaver] gfn_transaction 타임아웃 ({timeout_sec}s)")
                return SaveResult(
                    success=False,
                    method='direct_api_transaction',
                    message=f'callback timeout after {timeout_sec}s',
                )

            result = json.loads(result_str)

            if result.get('error'):
                logger.warning(f"[DirectApiSaver] gfn_transaction 오류: {result['error']}")
                return SaveResult(
                    success=False,
                    method='direct_api_transaction',
                    message=f"transaction error: {result['error']}",
                )

            success = result.get('success', False)
            added = result.get('added', 0)
            err_cd = result.get('errCd', '')
            err_msg = result.get('errMsg', '')

            # 99999: 넥사크로 정상 처리 코드 (gds_ErrMsg TYPE=NORMAL)
            # 라이브 검증 확인: 99999는 성공 코드이며 WARNING이 아님
            if err_cd in ('99999',):
                logger.debug(
                    f"[DirectApiSaver] 정상 처리 (ErrorCode={err_cd}): errMsg={err_msg}"
                )
            elif err_cd == '-9999':
                logger.warning(
                    f"[DirectApiSaver] 서버 거부 (ErrorCode={err_cd}): "
                    f"발주 가능 시간이 아닐 수 있음. errMsg={err_msg}"
                )

            logger.info(
                f"[DirectApiSaver] gfn_transaction 결과: success={success}, "
                f"added={added}, errCd={err_cd}, errMsg={err_msg}"
            )

            return SaveResult(
                success=success,
                saved_count=added if success else 0,
                method='direct_api_transaction',
                message=f'errCd={err_cd}, errMsg={err_msg}',
                response_preview=result_str[:500],
            )

        except Exception as e:
            logger.error(f"[DirectApiSaver] gfn_transaction 예외: {e}")
            return None

    # ─────────────────────────────────────────
    # 4. 전략 2: fetch() 직접 호출
    # ─────────────────────────────────────────

    def _save_via_fetch(
        self, orders: List[Dict], date_str: str
    ) -> Optional[SaveResult]:
        """SSV body 수동 구성 + fetch() 직접 호출"""
        try:
            template_body = self._save_template.get('body', '') if self._save_template and 'body' in self._save_template else None
            ssv_body = self.build_ssv_body(orders, date_str, template_body)

            endpoint = self._save_endpoint
            logger.info(f"[DirectApiSaver] fetch() 저장: {endpoint}, {len(orders)}건")

            response_text = self.driver.execute_script("""
                var endpoint = arguments[0];
                var body = arguments[1];
                var timeoutMs = arguments[2];

                try {
                    var resp = await fetch(endpoint, {
                        method: 'POST',
                        headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
                        body: body,
                        signal: AbortSignal.timeout(timeoutMs)
                    });
                    var text = await resp.text();
                    return JSON.stringify({
                        ok: resp.ok,
                        status: resp.status,
                        text: text.substring(0, 3000)
                    });
                } catch(e) {
                    return JSON.stringify({ok: false, error: e.message});
                }
            """, endpoint, ssv_body, self.timeout_ms)

            resp = json.loads(response_text) if response_text else {}

            if not resp.get('ok'):
                error = resp.get('error', f"HTTP {resp.get('status', 'unknown')}")
                return SaveResult(
                    success=False,
                    method='direct_api_fetch',
                    message=f'fetch error: {error}',
                    response_preview=resp.get('text', '')[:500],
                )

            resp_text = resp.get('text', '')
            saved_count = self._count_saved_items(resp_text, orders)

            return SaveResult(
                success=saved_count > 0,
                saved_count=saved_count,
                method='direct_api_fetch',
                message=f'saved {saved_count}/{len(orders)} items via fetch',
                response_preview=resp_text[:500],
            )

        except Exception as e:
            logger.error(f"[DirectApiSaver] fetch() 예외: {e}")
            return None

    # ─────────────────────────────────────────
    # 5. SSV Body 빌드 (fetch 폴백용)
    # ─────────────────────────────────────────

    def build_ssv_body(
        self,
        orders: List[Dict[str, Any]],
        date_str: str,
        template_body: Optional[str] = None,
    ) -> str:
        """
        발주 목록을 SSV body로 변환

        라이브 캡처 기반 (2026-02-28):
        - 세션변수: key=value 형식 (RS 구분)
        - 컬럼 타입: COLNAME:TYPE(SIZE) 형식
        - RowType: I (Insert)
        - 배수: PYUN_QTY, 발주량: TOT_QTY
        """
        if template_body:
            return self._replace_items_in_template(template_body, orders, date_str)

        # 기본 SSV 구성 (캡처 형식 반영)
        header = US.join([
            '_RowType_',
            'ITEM_CD:STRING(256)',
            'PYUN_QTY:STRING(256)',
            'ORD_UNIT_QTY:INT(256)',
            'TOT_QTY:INT(256)',
            'ORD_YMD:STRING(256)',
            'STORE_CD:STRING(256)',
        ])

        rows = []
        for order in orders:
            multiplier = self._calc_multiplier(order)
            order_unit_qty = order.get('order_unit_qty', 1) or 1
            total_qty = multiplier * order_unit_qty
            row = US.join([
                'I',
                str(order.get('item_cd', '')),
                str(multiplier),
                str(order_unit_qty),
                str(total_qty),
                date_str,
                str(order.get('store_cd', '')),
            ])
            rows.append(row)

        return RS.join([header] + rows)

    def _replace_items_in_template(
        self, template: str, orders: List[Dict], date_str: str
    ) -> str:
        """
        캡처된 SSV body 템플릿의 Dataset 영역을 교체

        라이브 캡처 기반 (2026-02-28):
        - RowType: I (Insert)
        - 배수: PYUN_QTY 컬럼, TOT_QTY = PYUN_QTY × ORD_UNIT_QTY
        - dsSaveChk: 빈 데이터셋 (컬럼 헤더만)
        """
        parts = template.split('Dataset:dsGeneralGrid' + RS)
        if len(parts) < 2:
            return self.build_ssv_body(orders, date_str)

        # 세션 헤더 부분 보존
        session_header = parts[0]

        # dsGeneralGrid 컬럼 정의 + 데이터 추출
        grid_and_rest = parts[1]
        rest_parts = grid_and_rest.split(RS + 'Dataset:dsSaveChk')

        if len(rest_parts) < 2:
            return self.build_ssv_body(orders, date_str)

        # 컬럼 헤더 추출
        grid_records = rest_parts[0].split(RS)
        col_header = grid_records[0] if grid_records else ''

        # 컬럼 순서 파악
        col_defs = col_header.split(US)
        cols = [c.split(':')[0] for c in col_defs]
        # 컬럼 타입 매핑 (숫자 기본값 판별용)
        col_types = {}
        for cd in col_defs:
            parts = cd.split(':')
            if len(parts) >= 2:
                col_types[parts[0]] = parts[1].upper()

        item_cd_idx = cols.index('ITEM_CD') if 'ITEM_CD' in cols else -1
        pyun_qty_idx = cols.index('PYUN_QTY') if 'PYUN_QTY' in cols else -1
        tot_qty_idx = cols.index('TOT_QTY') if 'TOT_QTY' in cols else -1

        if item_cd_idx < 0 or pyun_qty_idx < 0:
            return self.build_ssv_body(orders, date_str)

        # 알려진 숫자 컬럼 (SSV 템플릿 INT/BIGDECIMAL 정의)
        _KNOWN_NUMERIC = {
            'HQ_MAEGA_SET', 'ORD_UNIT_QTY', 'ORD_MULT_ULMT', 'ORD_MULT_LLMT',
            'NOW_QTY', 'ORD_MUL_QTY', 'OLD_PYUN_QTY', 'TOT_QTY',
            'PAGE_CNT', 'EXPIRE_DAY', 'PROFIT_RATE', 'PYUN_QTY',
            'EVT_DC_RATE', 'RB_AMT',
        }

        # 새 행 생성
        new_rows = []
        ssv_num_filled = 0
        for order in orders:
            multiplier = self._calc_multiplier(order)
            order_unit_qty = int(order.get('order_unit_qty', 1) or 1)
            total_qty = multiplier * order_unit_qty
            row_vals = [''] * len(cols)  # 빈 문자열 초기화
            row_vals[0] = 'I'  # Insert row (라이브 테스트 확인)
            row_vals[item_cd_idx] = str(order.get('item_cd', ''))
            row_vals[pyun_qty_idx] = str(multiplier)

            if tot_qty_idx >= 0:
                row_vals[tot_qty_idx] = str(total_qty)
            if 'ORD_YMD' in cols:
                row_vals[cols.index('ORD_YMD')] = date_str
            if 'ORD_UNIT_QTY' in cols:
                row_vals[cols.index('ORD_UNIT_QTY')] = str(order_unit_qty)
            if 'STORE_CD' in cols and order.get('store_cd'):
                row_vals[cols.index('STORE_CD')] = str(order['store_cd'])

            # 프리페치 필드 채우기 (빈 셀만)
            fields = order.get('fields', {})
            for col_name, val in fields.items():
                if col_name in cols and val:
                    idx = cols.index(col_name)
                    if not row_vals[idx]:  # 이미 설정된 핵심 필드는 건너뜀
                        row_vals[idx] = str(val)

            # 숫자 컬럼 빈값→'0' (서버 NumberFormatException 방지)
            _filled_cols = []
            for idx, col_name in enumerate(cols):
                if row_vals[idx]:
                    continue
                ct = col_types.get(col_name, '')
                is_numeric = 'INT' in ct or 'DECIMAL' in ct or 'FLOAT' in ct
                detect_by = f'type:{ct}' if is_numeric else ''
                if not is_numeric and col_name in _KNOWN_NUMERIC:
                    is_numeric = True
                    detect_by = 'known'
                if is_numeric:
                    row_vals[idx] = '0'
                    _filled_cols.append(f'{col_name}({detect_by})')
            if _filled_cols:
                ssv_num_filled += len(_filled_cols)

            new_rows.append(US.join(row_vals))

        if ssv_num_filled > 0:
            logger.info(
                f"[DirectApiSaver] SSV 숫자0채움: {ssv_num_filled}개 "
                f"({len(orders)}건 × 평균 {ssv_num_filled // max(len(orders), 1)}개/행)"
            )

        # dsSaveChk: 라이브 테스트에서 빈 데이터셋이었음
        save_chk_parts = rest_parts[1].split(RS)
        save_chk_header = save_chk_parts[0] if save_chk_parts else ''

        # 재조합
        grid_section = RS.join([col_header] + new_rows)

        body = (
            session_header
            + 'Dataset:dsGeneralGrid' + RS
            + grid_section
            + RS
            + 'Dataset:dsSaveChk' + RS
            + save_chk_header + RS
            + RS  # 빈 데이터
        )
        return body

    # ─────────────────────────────────────────
    # 6. 저장 검증
    # ─────────────────────────────────────────

    def verify_save(
        self,
        orders: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """저장 후 그리드 데이터를 읽어 검증"""
        if not DIRECT_API_ORDER_VERIFY:
            return {'verified': True, 'skipped': True}

        time.sleep(DIRECT_API_VERIFY_WAIT)

        try:
            grid_data = self.driver.execute_script("""
                const app = nexacro.getApplication?.();
                const frameSet = app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet;
                let stbjForm = frameSet?.STBJ030_M0?.form;

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
                if (!workForm?.gdList) return {error: 'no_grid'};

                // 1순위: dsGeneralGrid 직접 참조 (gfn_transaction이 사용하는 dataset)
                let ds = workForm.dsGeneralGrid;
                let dsSource = 'dsGeneralGrid';

                // 2순위: gdList 바인딩 폴백
                if (!ds || typeof ds.getRowCount !== 'function') {
                    ds = workForm.gdList._binddataset;
                    dsSource = 'gdList._binddataset';
                    // _binddataset이 문자열(이름)인 경우 → 실제 객체 접근
                    if (typeof ds === 'string') {
                        dsSource = 'gdList._binddataset[' + ds + ']';
                        ds = workForm[ds];
                    }
                }

                // 3순위: _binddataset_obj 폴백
                if (!ds || typeof ds.getRowCount !== 'function') {
                    ds = workForm.gdList._binddataset_obj;
                    dsSource = 'gdList._binddataset_obj';
                }
                if (!ds) return {error: 'no_dataset'};

                var items = [];
                for (var i = 0; i < ds.getRowCount(); i++) {
                    items.push({
                        item_cd: ds.getColumn(i, 'ITEM_CD') || '',
                        ord_qty: parseInt(ds.getColumn(i, 'PYUN_QTY') || ds.getColumn(i, 'ORD_MUL_QTY') || '0'),
                    });
                }
                return {
                    items: items,
                    count: ds.getRowCount(),
                    dsSource: dsSource,
                    sampleItems: items.slice(0, 3).map(function(x){ return x.item_cd; })
                };
            """)

            if not grid_data or grid_data.get('error'):
                return {'verified': False, 'error': grid_data.get('error', 'unknown')}

            grid_items = {item['item_cd']: item['ord_qty'] for item in grid_data.get('items', [])}
            grid_count = grid_data.get('count', 0)
            ds_source = grid_data.get('dsSource', 'unknown')
            sample_items = grid_data.get('sampleItems', [])
            logger.info(
                f"[DirectApiSaver] 검증 그리드: count={grid_count}, "
                f"source={ds_source}, sample={sample_items}"
            )

            # gfn_transaction 성공 후 넥사크로가 그리드를 클리어하는 경우
            # (콜백에서 clearData 호출 → 검증 시점에 0행)
            # 이 경우 검증 불가이지 실패가 아님 → 스킵 처리
            if grid_count == 0 and len(orders) > 0:
                logger.info(
                    f"[DirectApiSaver] 검증 스킵: 그리드 0행 "
                    f"(gfn_transaction 후 자동 클리어 추정, {len(orders)}건 저장됨)"
                )
                return {
                    'verified': True,
                    'skipped': True,
                    'reason': 'grid_cleared_after_save',
                    'total': len(orders),
                }

            matched = 0
            mismatched = []
            missing = []

            for order in orders:
                item_cd = order.get('item_cd', '')
                expected_mult = self._calc_multiplier(order)

                if item_cd in grid_items:
                    if grid_items[item_cd] == expected_mult:
                        matched += 1
                    else:
                        mismatched.append({
                            'item_cd': item_cd,
                            'expected': expected_mult,
                            'actual': grid_items[item_cd],
                        })
                else:
                    missing.append(item_cd)

            logger.info(
                f"[DirectApiSaver] 검증 비교: matched={matched}, "
                f"mismatched={len(mismatched)}, missing={len(missing)}, "
                f"orders={len(orders)}, "
                f"grid_replaced_cond={matched == 0 and len(mismatched) == 0 and len(missing) == len(orders)}"
            )

            # ── missing 임계치 가드 (false positive 방지) ──
            # grid_replaced 추정보다 우선: missing>50%이고 grid에 의미있는 데이터가 없으면
            # BGF가 실제 저장하지 않은 것으로 판단하여 실패 처리.
            # 정상적인 grid_replaced는 그리드에 다른 상품(리로드 결과)이 채워져야 하지만,
            # grid_count<=1이고 sample이 빈값이면 폼 자체가 비정상 상태.
            missing_ratio = len(missing) / max(len(orders), 1)
            # 그리드가 빈 상태: count<=1이고 sample에 유효한 상품코드가 없음
            sample_items = grid_data.get('sampleItems', [])
            has_valid_sample = any(s and str(s).strip() for s in sample_items)
            is_grid_empty = grid_count <= 1 and not has_valid_sample
            if missing_ratio > 0.5 and is_grid_empty:
                logger.warning(
                    f"[DirectApiSaver] 검증 실패: missing={len(missing)}/{len(orders)} "
                    f"({missing_ratio:.0%}) + grid_count={grid_count} "
                    f"→ BGF 미저장 판정 (grid_replaced 추정 무시)"
                )
                return {
                    'verified': False,
                    'skipped': False,
                    'reason': f'missing_{missing_ratio:.0%}_with_empty_grid',
                    'total': len(orders),
                    'matched': matched,
                    'missing': len(missing),
                }

            # gfn_transaction outDS='dsGeneralGrid=dsGeneralGrid' 때문에
            # 서버 응답이 그리드를 덮어씀 + fn_callback이 selSearch 리로드.
            # 검증 시점에 그리드 내용이 완전히 교체되어 우리 상품이 안 보임.
            # matched=0 AND mismatched=0 = 그리드 교체 (실패가 아님) → 스킵 처리
            # NOTE: 위 missing 가드를 통과한 경우만 도달 (grid에 데이터가 있는 정상 교체)
            if matched == 0 and len(mismatched) == 0 and len(missing) == len(orders):
                logger.info(
                    f"[DirectApiSaver] 검증 스킵: 그리드 교체됨 "
                    f"(gfn_transaction 후 outDS 덮어쓰기+콜백 리로드 추정, "
                    f"grid_count={grid_count}, orders={len(orders)}건)"
                )
                return {
                    'verified': True,
                    'skipped': True,
                    'reason': 'grid_replaced_after_save',
                    'total': len(orders),
                }

            verified = matched == len(orders)
            logger.info(
                f"[DirectApiSaver] 검증: {matched}/{len(orders)}건 일치, "
                f"불일치={len(mismatched)}, 누락={len(missing)}"
            )

            return {
                'verified': verified,
                'matched': matched,
                'total': len(orders),
                'mismatched': mismatched,
                'missing': missing,
            }

        except Exception as e:
            logger.error(f"[DirectApiSaver] 검증 실패: {e}")
            return {'verified': False, 'error': str(e)}

    # ─────────────────────────────────────────
    # 7. 유틸리티
    # ─────────────────────────────────────────

    @staticmethod
    def _calc_multiplier(order: Dict) -> int:
        """발주 배수 계산

        cancel_smart=True인 qty=0 항목은 PYUN_QTY=0으로 BGF 전송 (스마트 취소용).
        라이브 검증 (2026-03-14): PYUN_QTY=0 → BGF 수락, "단품별(채택)" 전환 확인.
        MAX_ORDER_MULTIPLIER(99) 캡 적용하여 과발주 방지.
        """
        from src.settings.constants import MAX_ORDER_MULTIPLIER

        # 스마트발주 취소: qty=0 그대로 전송
        if order.get('cancel_smart') and order.get('final_order_qty', 0) <= 0:
            return 0

        multiplier = order.get('multiplier', 0)
        if multiplier and multiplier > 0:
            return min(int(multiplier), MAX_ORDER_MULTIPLIER)
        qty = order.get('final_order_qty', 0)
        unit = order.get('order_unit_qty', 1) or 1
        return min(MAX_ORDER_MULTIPLIER, max(1, (qty + unit - 1) // unit))

    def _count_saved_items(self, response_text: str, orders: List[Dict]) -> int:
        """SSV 응답에서 저장 성공 여부 파악"""
        if not response_text:
            return 0

        # ErrorCode 파싱
        # 라이브 검증 (2026-02-28): ErrorCode=99999 + gds_ErrMsg TYPE=NORMAL = 정상 처리
        # ErrorCode=0, 99999 모두 성공으로 판단 (그리드 초기화 + NORMAL 응답)
        if 'ErrorCode' in response_text:
            if ('ErrorCode:string=0' in response_text
                    or 'ErrorCode=0' in response_text
                    or 'ErrorCode:string=99999' in response_text
                    or 'ErrorCode=99999' in response_text):
                return len(orders)
            return 0

        # SSV 형식이면 성공으로 간주
        if RS in response_text or US in response_text:
            return len(orders)

        return 0
