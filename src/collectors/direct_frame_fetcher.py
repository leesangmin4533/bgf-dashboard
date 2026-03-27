"""
Direct Frame Fetcher - 넥사크로 프레임 레벨 API 직접 호출

Selenium UI 조작(날짜 콤보 변경, 전표 row 선택 등) 없이
프레임의 서버 트랜잭션 엔드포인트에 직접 HTTP 요청을 보냅니다.

로그인된 Selenium 브라우저 내에서 fetch()를 실행하여 쿠키/세션을 자동 공유합니다.

대상 엔드포인트:
    4순위: /stgj010/searchChitListPopup, /stgj010/search (입고)
    5순위: /stgj020/search (폐기 전표 헤더)
    5-1순위: /stgj020/searchDetailType1 (폐기 전표 상세 품목)
    6순위: /stbj070/selSearch (발주 현황)
"""

import time
from typing import Any, Callable, Dict, List, Optional

from src.collectors.direct_api_fetcher import (
    parse_ssv_dataset,
    ssv_row_to_dict,
    _safe_int,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

# SSV 구분자
RS = '\u001e'
US = '\u001f'


# ═══════════════════════════════════════════════════════════════════
# 공유 XHR 인터셉터 (Direct API 템플릿 캡처용)
# ═══════════════════════════════════════════════════════════════════

INTERCEPTOR_JS = r"""
(function() {
    if (window.__collectorCaptures) return 'already_installed';

    window.__collectorCaptures = [];

    var origFetch = window.fetch;
    window.fetch = function(url, opts) {
        var entry = {
            type: 'fetch',
            url: String(url),
            method: (opts && opts.method) || 'GET',
            bodyPreview: '',
            timestamp: new Date().toISOString()
        };
        if (opts && opts.body) {
            entry.bodyPreview = String(opts.body).substring(0, 3000);
        }
        var p = origFetch.apply(this, arguments);
        p.then(function(resp) {
            return resp.clone().text().then(function(text) {
                entry.status = resp.status;
                entry.responsePreview = text.substring(0, 2000);
                entry.responseLength = text.length;
            });
        }).catch(function() {});
        window.__collectorCaptures.push(entry);
        return p;
    };

    var origOpen = XMLHttpRequest.prototype.open;
    var origSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function(method, url) {
        this._capUrl = String(url);
        this._capMethod = method;
        return origOpen.apply(this, arguments);
    };

    XMLHttpRequest.prototype.send = function(body) {
        var self = this;
        var entry = {
            type: 'xhr',
            url: self._capUrl || '',
            method: self._capMethod || 'POST',
            bodyPreview: body ? String(body).substring(0, 3000) : '',
            timestamp: new Date().toISOString()
        };
        self.addEventListener('load', function() {
            entry.status = self.status;
            entry.responsePreview = (self.responseText || '').substring(0, 2000);
            entry.responseLength = (self.responseText || '').length;
        });
        window.__collectorCaptures.push(entry);
        return origSend.apply(this, arguments);
    };

    return 'installed';
})();
"""


def install_interceptor(driver) -> bool:
    """XHR/fetch 인터셉터 설치"""
    try:
        driver.execute_script(INTERCEPTOR_JS)
        return True
    except Exception as e:
        logger.debug(f"인터셉터 설치 실패: {e}")
        return False


def get_captures(driver, clear: bool = False) -> list:
    """캡처된 요청 목록 반환"""
    try:
        return driver.execute_script("""
            var caps = window.__collectorCaptures || [];
            if (arguments[0]) window.__collectorCaptures = [];
            return caps;
        """, clear) or []
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════
# SSV Body 파라미터 치환
# ═══════════════════════════════════════════════════════════════════

def replace_ssv_column(body: str, col_name: str, new_value: str) -> str:
    """
    SSV body 템플릿의 dsSearch 데이터셋에서 특정 컬럼 값을 치환

    Args:
        body: SSV body 전체 텍스트
        col_name: 치환할 컬럼명 (예: 'strChitNo')
        new_value: 새 값

    Returns:
        치환된 SSV body
    """
    records = body.split(RS)
    for i, record in enumerate(records):
        if '_RowType_' in record and col_name in record:
            # 헤더 레코드 → 컬럼 인덱스 찾기
            cols = record.split(US)
            col_idx = -1
            for j, col in enumerate(cols):
                if col_name in col:
                    col_idx = j
                    break

            if col_idx >= 0 and i + 1 < len(records):
                # 다음 레코드가 데이터 행
                vals = records[i + 1].split(US)
                if col_idx < len(vals):
                    vals[col_idx] = new_value
                    records[i + 1] = US.join(vals)
            break

    return RS.join(records)


def parse_all_datasets(ssv_text: str) -> Dict[str, Dict[str, Any]]:
    """
    SSV 응답에서 모든 데이터셋을 파싱

    Returns:
        {dataset_name: {'columns': [...], 'rows': [[...], ...]}}
    """
    if not ssv_text:
        return {}

    result = {}
    records = ssv_text.split(RS)
    i = 0

    while i < len(records):
        record = records[i]

        # Dataset:이름 마커 찾기
        if record.startswith('Dataset:'):
            ds_name = record.split(':')[1].strip()

            # 다음 레코드가 컬럼 헤더
            if i + 1 < len(records) and '_RowType_' in records[i + 1]:
                cols_raw = records[i + 1].split(US)
                columns = [c.split(':')[0] for c in cols_raw]

                rows = []
                j = i + 2
                while j < len(records):
                    row_text = records[j].strip()
                    if not row_text or row_text.startswith('Dataset:') or '_RowType_' in row_text:
                        break
                    vals = row_text.split(US)
                    rows.append(vals)
                    j += 1

                result[ds_name] = {'columns': columns, 'rows': rows}
                i = j
                continue

        i += 1

    return result


# ═══════════════════════════════════════════════════════════════════
# Receiving Direct API (STGJ010_M0)
# ═══════════════════════════════════════════════════════════════════

RECEIVING_ENDPOINT_CHITLIST = '/stgj010/searchChitListPopup'
RECEIVING_ENDPOINT_SEARCH = '/stgj010/search'


def parse_receiving_chit_list(ssv_text: str) -> List[Dict[str, str]]:
    """searchChitListPopup SSV → dsListPopup 전표 목록"""
    ds = parse_ssv_dataset(ssv_text, 'NAP_PLAN_YMD')
    if not ds:
        return []
    return [ssv_row_to_dict(ds['columns'], row) for row in ds['rows']]


def parse_receiving_items(ssv_text: str) -> List[Dict[str, str]]:
    """search SSV → dsList 상품 목록"""
    ds = parse_ssv_dataset(ssv_text, 'ITEM_CD')
    if not ds:
        return []
    return [ssv_row_to_dict(ds['columns'], row) for row in ds['rows']]


class DirectReceivingFetcher:
    """
    센터매입 조회(STGJ010_M0) Direct API 호출

    기존 Selenium 흐름:
        날짜 선택 → dsListPopup 로딩 → 전표 행 선택(N회) → dsList 읽기
    Direct API:
        1회 searchChitListPopup → 전표 목록
        N회 search (병렬) → 전표별 상품 목록
    """

    def __init__(self, driver: Any, concurrency: int = 3, timeout_ms: int = 8000):
        self.driver = driver
        self.concurrency = concurrency
        self.timeout_ms = timeout_ms
        self._chitlist_template: Optional[str] = None
        self._search_template: Optional[str] = None

    def capture_templates(self) -> bool:
        """
        브라우저의 __collectorCaptures에서 템플릿 캡처

        Returns:
            캡처 성공 여부
        """
        try:
            templates = self.driver.execute_script(r"""
                var caps = window.__collectorCaptures || [];
                var chitlist = null, search = null;
                for (var i = 0; i < caps.length; i++) {
                    var c = caps[i];
                    if (!chitlist && c.url && c.url.indexOf('searchChitListPopup') >= 0) {
                        chitlist = c.bodyPreview || '';
                    }
                    if (!search && c.url && c.url.indexOf('/stgj010/search') >= 0
                        && c.url.indexOf('searchChitListPopup') < 0) {
                        search = c.bodyPreview || '';
                    }
                }
                return {chitlist: chitlist, search: search};
            """)

            if templates and templates.get('chitlist'):
                self._chitlist_template = templates['chitlist']
                self._search_template = templates.get('search') or templates['chitlist']
                logger.info(
                    f"[ReceivingAPI] 템플릿 캡처 성공: "
                    f"chitlist={len(self._chitlist_template)}B, "
                    f"search={len(self._search_template)}B"
                )
                return True

        except Exception as e:
            logger.warning(f"[ReceivingAPI] 캡처 실패: {e}")

        return False

    def set_templates(self, chitlist_body: str, search_body: str = None):
        """템플릿 직접 설정 (테스트용)"""
        self._chitlist_template = chitlist_body
        self._search_template = search_body or chitlist_body

    @property
    def has_template(self) -> bool:
        return bool(self._chitlist_template)

    def fetch_chit_list(self, dgfw_ymd: str) -> List[Dict[str, str]]:
        """
        특정 날짜의 전표 목록 조회

        Args:
            dgfw_ymd: 출고일 (YYYYMMDD)

        Returns:
            전표 목록 [{NAP_PLAN_YMD, DGFW_YMD, CENTER_NM, CHIT_NO, AIS_HMS, ...}]
        """
        if not self._chitlist_template:
            return []

        try:
            ssv_text = self.driver.execute_script("""
                var body = arguments[0];
                var dgfwYmd = arguments[1];
                var timeoutMs = arguments[2];

                // SSV 컬럼값 치환 함수
                function replaceCol(body, colName, newVal) {
                    var RS = '\\u001e', US = '\\u001f';
                    var records = body.split(RS);
                    for (var i = 0; i < records.length; i++) {
                        if (records[i].indexOf('_RowType_') >= 0 && records[i].indexOf(colName) >= 0) {
                            var cols = records[i].split(US);
                            var idx = -1;
                            for (var j = 0; j < cols.length; j++) {
                                if (cols[j].indexOf(colName) >= 0) { idx = j; break; }
                            }
                            if (idx >= 0 && i + 1 < records.length) {
                                var vals = records[i + 1].split(US);
                                if (idx < vals.length) {
                                    vals[idx] = newVal;
                                    records[i + 1] = vals.join(US);
                                }
                            }
                            break;
                        }
                    }
                    return records.join(RS);
                }

                body = replaceCol(body, 'strNapYmd', dgfwYmd);
                body = replaceCol(body, 'strAcpYmd', dgfwYmd);

                var resp = await fetch('/stgj010/searchChitListPopup', {
                    method: 'POST',
                    headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                    body: body,
                    signal: AbortSignal.timeout(timeoutMs)
                });
                return await resp.text();
            """, self._chitlist_template, dgfw_ymd, self.timeout_ms)

            if not ssv_text:
                return []

            return parse_receiving_chit_list(ssv_text)

        except Exception as e:
            logger.warning(f"[ReceivingAPI] 전표목록 조회 실패 ({dgfw_ymd}): {e}")
            return []

    def fetch_items_for_chits(
        self,
        chit_nos: List[str],
        dgfw_ymd: str,
        delay_ms: int = 50,
    ) -> Dict[str, List[Dict[str, str]]]:
        """
        여러 전표의 상품 목록을 병렬 조회

        Args:
            chit_nos: 전표번호 목록
            dgfw_ymd: 출고일 (YYYYMMDD)
            delay_ms: 요청 간 딜레이 (ms)

        Returns:
            {chit_no: [상품 목록], ...}
        """
        if not self._search_template or not chit_nos:
            return {}

        try:
            raw_results = self.driver.execute_script("""
                var template = arguments[0];
                var chitNos = arguments[1];
                var dgfwYmd = arguments[2];
                var concurrency = arguments[3];
                var timeoutMs = arguments[4];
                var delayMs = arguments[5];

                function replaceCol(body, colName, newVal) {
                    var RS = '\\u001e', US = '\\u001f';
                    var records = body.split(RS);
                    for (var i = 0; i < records.length; i++) {
                        if (records[i].indexOf('_RowType_') >= 0 && records[i].indexOf(colName) >= 0) {
                            var cols = records[i].split(US);
                            var idx = -1;
                            for (var j = 0; j < cols.length; j++) {
                                if (cols[j].indexOf(colName) >= 0) { idx = j; break; }
                            }
                            if (idx >= 0 && i + 1 < records.length) {
                                var vals = records[i + 1].split(US);
                                if (idx < vals.length) {
                                    vals[idx] = newVal;
                                    records[i + 1] = vals.join(US);
                                }
                            }
                            break;
                        }
                    }
                    return records.join(RS);
                }

                async function fetchOne(chitNo) {
                    var body = replaceCol(template, 'strChitNo', chitNo);
                    body = replaceCol(body, 'strNapYmd', dgfwYmd);
                    body = replaceCol(body, 'strAcpYmd', dgfwYmd);
                    try {
                        var resp = await fetch('/stgj010/search', {
                            method: 'POST',
                            headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                            body: body,
                            signal: AbortSignal.timeout(timeoutMs)
                        });
                        var text = await resp.text();
                        return {chitNo: chitNo, text: text, ok: true};
                    } catch(e) {
                        return {chitNo: chitNo, error: e.message, ok: false};
                    }
                }

                var results = [];
                var idx = 0;

                async function worker() {
                    while (idx < chitNos.length) {
                        var myIdx = idx++;
                        var r = await fetchOne(chitNos[myIdx]);
                        results.push(r);
                        if (delayMs > 0) await new Promise(res => setTimeout(res, delayMs));
                    }
                }

                var workers = [];
                for (var w = 0; w < Math.min(concurrency, chitNos.length); w++) {
                    workers.push(worker());
                }
                await Promise.all(workers);

                return results;
            """, self._search_template, chit_nos, dgfw_ymd,
                self.concurrency, self.timeout_ms, delay_ms)

            if not raw_results:
                return {}

            result = {}
            ok_count = 0
            for r in raw_results:
                if r.get('ok') and r.get('text'):
                    items = parse_receiving_items(r['text'])
                    if items:
                        result[r['chitNo']] = items
                        ok_count += 1

            logger.info(
                f"[ReceivingAPI] 전표 상품 조회: "
                f"{ok_count}/{len(chit_nos)} 성공"
            )
            return result

        except Exception as e:
            logger.warning(f"[ReceivingAPI] 배치 조회 실패: {e}")
            return {}


# ═══════════════════════════════════════════════════════════════════
# Waste Slip Direct API (STGJ020_M0)
# ═══════════════════════════════════════════════════════════════════

WASTE_SLIP_ENDPOINT = '/stgj020/search'


def parse_waste_slip_list(ssv_text: str) -> List[Dict[str, str]]:
    """stgj020/search SSV → dsList 폐기 전표 목록"""
    ds = parse_ssv_dataset(ssv_text, 'CHIT_FLAG')
    if not ds:
        return []
    return [ssv_row_to_dict(ds['columns'], row) for row in ds['rows']]


class DirectWasteSlipFetcher:
    """
    통합 전표 조회(STGJ020_M0) Direct API 호출

    기존 Selenium 흐름:
        날짜 설정 → 전표구분 콤보 → F10 검색 → dsList 읽기
    Direct API:
        1회 search → 전체 전표 목록 (날짜/필터 파라미터 치환)
    """

    def __init__(self, driver: Any, timeout_ms: int = 8000):
        self.driver = driver
        self.timeout_ms = timeout_ms
        self._search_template: Optional[str] = None

    def capture_template(self) -> bool:
        """브라우저 __collectorCaptures에서 템플릿 캡처"""
        try:
            template = self.driver.execute_script(r"""
                var caps = window.__collectorCaptures || [];
                for (var i = 0; i < caps.length; i++) {
                    var c = caps[i];
                    if (c.url && c.url.indexOf('/stgj020/search') >= 0) {
                        return c.bodyPreview || '';
                    }
                }
                return null;
            """)

            if template:
                self._search_template = template
                logger.info(f"[WasteSlipAPI] 템플릿 캡처 성공: {len(template)}B")
                return True

        except Exception as e:
            logger.warning(f"[WasteSlipAPI] 캡처 실패: {e}")

        return False

    def set_template(self, search_body: str):
        """템플릿 직접 설정 (테스트용)"""
        self._search_template = search_body

    @property
    def has_template(self) -> bool:
        return bool(self._search_template)

    def fetch_waste_slips(
        self,
        from_date: str,
        to_date: str,
        chit_div: str = '10',
    ) -> List[Dict[str, str]]:
        """
        폐기 전표 목록 조회

        Args:
            from_date: 시작일 (YYYYMMDD)
            to_date: 종료일 (YYYYMMDD)
            chit_div: 전표구분 코드 (10=폐기)

        Returns:
            전표 목록 [{CHIT_FLAG, CHIT_YMD, CHIT_NO, ITEM_CNT, ...}]
        """
        if not self._search_template:
            return []

        try:
            ssv_text = self.driver.execute_script("""
                var body = arguments[0];
                var fromDt = arguments[1];
                var toDt = arguments[2];
                var chitDiv = arguments[3];
                var timeoutMs = arguments[4];

                function replaceCol(body, colName, newVal) {
                    var RS = '\\u001e', US = '\\u001f';
                    var records = body.split(RS);
                    for (var i = 0; i < records.length; i++) {
                        if (records[i].indexOf('_RowType_') >= 0 && records[i].indexOf(colName) >= 0) {
                            var cols = records[i].split(US);
                            var idx = -1;
                            for (var j = 0; j < cols.length; j++) {
                                if (cols[j].indexOf(colName) >= 0) { idx = j; break; }
                            }
                            if (idx >= 0 && i + 1 < records.length) {
                                var vals = records[i + 1].split(US);
                                if (idx < vals.length) {
                                    vals[idx] = newVal;
                                    records[i + 1] = vals.join(US);
                                }
                            }
                            break;
                        }
                    }
                    return records.join(RS);
                }

                body = replaceCol(body, 'strFromDt', fromDt);
                body = replaceCol(body, 'strToDt', toDt);
                body = replaceCol(body, 'strChitDiv', chitDiv);

                var resp = await fetch('/stgj020/search', {
                    method: 'POST',
                    headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                    body: body,
                    signal: AbortSignal.timeout(timeoutMs)
                });
                return await resp.text();
            """, self._search_template, from_date, to_date, chit_div, self.timeout_ms)

            if not ssv_text:
                return []

            return parse_waste_slip_list(ssv_text)

        except Exception as e:
            logger.warning(f"[WasteSlipAPI] 조회 실패 ({from_date}~{to_date}): {e}")
            return []


# ═══════════════════════════════════════════════════════════════════
# Order Status Direct API (STBJ070_M0)
# ═══════════════════════════════════════════════════════════════════

ORDER_STATUS_ENDPOINT = '/stbj070/selSearch'


def parse_order_status_result(ssv_text: str) -> Dict[str, Any]:
    """
    stbj070/selSearch SSV → dsResult + dsOrderSale + dsWeek

    parse_all_datasets()를 사용하여 multi-dataset 경계를 정확히 처리

    Returns:
        {
            'dsResult': [row_dict, ...],
            'dsOrderSale': [row_dict, ...],
            'dsWeek': [date_str, ...],
        }
    """
    result = {'dsResult': [], 'dsOrderSale': [], 'dsWeek': []}

    datasets = parse_all_datasets(ssv_text)
    if not datasets:
        return result

    # dsResult
    ds_result = datasets.get('dsResult')
    if ds_result:
        result['dsResult'] = [
            ssv_row_to_dict(ds_result['columns'], row)
            for row in ds_result['rows']
        ]

    # dsOrderSale
    ds_order_sale = datasets.get('dsOrderSale')
    if ds_order_sale:
        result['dsOrderSale'] = [
            ssv_row_to_dict(ds_order_sale['columns'], row)
            for row in ds_order_sale['rows']
        ]

    # dsWeek
    ds_week = datasets.get('dsWeek')
    if ds_week:
        for row_vals in ds_week['rows']:
            row = ssv_row_to_dict(ds_week['columns'], row_vals)
            ymd = row.get('ORD_YMD', '')
            if ymd:
                result['dsWeek'].append(ymd)

    return result


class DirectOrderStatusFetcher:
    """
    발주 현황 조회(STBJ070_M0) Direct API 호출

    기존 Selenium 흐름:
        메뉴 이동 → 라디오 클릭 → dsResult 읽기 + dsOrderSale 읽기
    Direct API:
        1회 selSearch → dsResult + dsOrderSale + dsWeek 일괄 반환
    """

    def __init__(self, driver: Any, timeout_ms: int = 10000):
        self.driver = driver
        self.timeout_ms = timeout_ms
        self._search_template: Optional[str] = None

    def capture_template(self) -> bool:
        """브라우저 __collectorCaptures에서 템플릿 캡처"""
        try:
            template = self.driver.execute_script(r"""
                var caps = window.__collectorCaptures || [];
                for (var i = 0; i < caps.length; i++) {
                    var c = caps[i];
                    if (c.url && c.url.indexOf('/stbj070/selSearch') >= 0) {
                        return c.bodyPreview || '';
                    }
                }
                return null;
            """)

            if template:
                self._search_template = template
                logger.info(f"[OrderStatusAPI] 템플릿 캡처 성공: {len(template)}B")
                return True

        except Exception as e:
            logger.warning(f"[OrderStatusAPI] 캡처 실패: {e}")

        return False

    def set_template(self, search_body: str):
        """템플릿 직접 설정 (테스트용)"""
        self._search_template = search_body

    @property
    def has_template(self) -> bool:
        return bool(self._search_template)

    def fetch_order_status(self, gubun: str = '0', ord_ymd: str = '') -> Dict[str, Any]:
        """
        발주 현황 조회

        Args:
            gubun: 발주 구분 (0=전체, 1=일반, 2=자동, 3=스마트)
            ord_ymd: 발주일자 (YYYYMMDD). 빈 문자열이면 기본값(BGF 서버 결정)

        Returns:
            {'dsResult': [...], 'dsOrderSale': [...], 'dsWeek': [...]}
        """
        if not self._search_template:
            return {'dsResult': [], 'dsOrderSale': [], 'dsWeek': []}

        try:
            ssv_text = self.driver.execute_script("""
                var body = arguments[0];
                var gubun = arguments[1];
                var timeoutMs = arguments[2];
                var ordYmd = arguments[3];

                // SSV body 내 컬럼값 치환
                function replaceCol(body, colName, newVal) {
                    var RS = '\\u001e', US = '\\u001f';
                    var records = body.split(RS);
                    for (var i = 0; i < records.length; i++) {
                        if (records[i].indexOf('_RowType_') >= 0 && records[i].indexOf(colName) >= 0) {
                            var cols = records[i].split(US);
                            var idx = -1;
                            for (var j = 0; j < cols.length; j++) {
                                if (cols[j].indexOf(colName) >= 0) { idx = j; break; }
                            }
                            if (idx >= 0 && i + 1 < records.length) {
                                var vals = records[i + 1].split(US);
                                if (idx < vals.length) {
                                    vals[idx] = newVal;
                                    records[i + 1] = vals.join(US);
                                }
                            }
                            break;
                        }
                    }
                    return records.join(RS);
                }

                if (body.indexOf('strGubun') >= 0) {
                    body = replaceCol(body, 'strGubun', gubun);
                }
                if (ordYmd && body.indexOf('strOrdYmd') >= 0) {
                    body = replaceCol(body, 'strOrdYmd', ordYmd);
                }

                var resp = await fetch('/stbj070/selSearch', {
                    method: 'POST',
                    headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                    body: body,
                    signal: AbortSignal.timeout(timeoutMs)
                });
                return await resp.text();
            """, self._search_template, gubun, self.timeout_ms, ord_ymd)

            if not ssv_text:
                return {'dsResult': [], 'dsOrderSale': [], 'dsWeek': []}

            return parse_order_status_result(ssv_text)

        except Exception as e:
            logger.warning(f"[OrderStatusAPI] 조회 실패: {e}")
            return {'dsResult': [], 'dsOrderSale': [], 'dsWeek': []}


# ═══════════════════════════════════════════════════════════════════
# Waste Slip Detail Direct API (STGJ020_P1 → /stgj020/searchDetailType1)
# ═══════════════════════════════════════════════════════════════════

WASTE_SLIP_DETAIL_ENDPOINT = '/stgj020/searchDetailType1'


def parse_waste_slip_detail(ssv_text: str) -> List[Dict[str, str]]:
    """stgj020/searchDetailType1 SSV → 상세 품목 리스트

    응답에는 dsListType0~4 데이터셋이 포함됨.
    모든 데이터셋의 행을 합쳐서 반환.

    Args:
        ssv_text: SSV 응답 텍스트

    Returns:
        품목 딕셔너리 리스트 (ITEM_CD, ITEM_NM, QTY, WONGA, MAEGA 등)
    """
    if not ssv_text:
        return []

    datasets = parse_all_datasets(ssv_text)
    if not datasets:
        return []

    all_items = []
    # dsListType0 ~ dsListType4 순회
    for t in range(5):
        ds_name = f'dsListType{t}'
        ds = datasets.get(ds_name)
        if ds and ds.get('rows'):
            for row in ds['rows']:
                item = ssv_row_to_dict(ds['columns'], row)
                item['_dsType'] = str(t)
                all_items.append(item)

    return all_items


class DirectWasteSlipDetailFetcher:
    """
    폐기 전표 상세 품목 Direct API 조회

    기존: 팝업 열기/닫기 반복 (7.8초/건)
    Direct API: /stgj020/searchDetailType1 직접 호출 (~0.5초/건)
    """

    ENDPOINT = '/stgj020/searchDetailType1'

    def __init__(self, driver: Any, timeout_ms: int = 8000):
        self.driver = driver
        self.timeout_ms = timeout_ms
        self._detail_template: Optional[str] = None

    def capture_template(self) -> bool:
        """__collectorCaptures에서 searchDetailType1 템플릿 캡처

        Returns:
            캡처 성공 여부
        """
        try:
            template = self.driver.execute_script(r"""
                var caps = window.__collectorCaptures || [];
                for (var i = 0; i < caps.length; i++) {
                    var c = caps[i];
                    if (c.url && c.url.indexOf('searchDetailType1') >= 0) {
                        return c.bodyPreview || '';
                    }
                }
                return null;
            """)

            if template:
                self._detail_template = template
                logger.info(
                    f"[WasteSlipDetailAPI] 템플릿 캡처 성공: {len(template)}B"
                )
                return True

        except Exception as e:
            logger.warning(f"[WasteSlipDetailAPI] 캡처 실패: {e}")

        return False

    def set_template(self, detail_body: str):
        """템플릿 직접 설정 (테스트용)"""
        self._detail_template = detail_body

    @property
    def has_template(self) -> bool:
        return bool(self._detail_template)

    def fetch_slip_details(
        self,
        chit_no: str,
        chit_ymd: str,
        chit_id: str = '04',
        store_cd: str = None,
    ) -> List[Dict[str, str]]:
        """단일 전표의 상세 품목 Direct API 조회

        BGF 서버는 strChitNoList('(전표번호)' 형식)와 strChitDiv(CHIT_ID)를 참조.
        주의: CHIT_FLAG(10=폐기)와 CHIT_ID(04=폐기)는 다른 코드 체계.

        Args:
            chit_no: 전표번호
            chit_ymd: 전표일자 (YYYYMMDD)
            chit_id: 전표구분 ID (CHIT_ID, 기본 '04' = 폐기). CHIT_FLAG(10)와 다름!
            store_cd: 점포코드 (None이면 템플릿 값 유지)

        Returns:
            상세 품목 리스트
        """
        if not self._detail_template:
            return []

        try:
            ssv_text = self.driver.execute_script("""
                var body = arguments[0];
                var chitNo = arguments[1];
                var chitYmd = arguments[2];
                var chitId = arguments[3];
                var storeCd = arguments[4];
                var timeoutMs = arguments[5];

                // SSV 컬럼값 치환 함수
                function replaceCol(body, colName, newVal) {
                    var RS = '\\u001e', US = '\\u001f';
                    var records = body.split(RS);
                    for (var i = 0; i < records.length; i++) {
                        if (records[i].indexOf('_RowType_') >= 0 && records[i].indexOf(colName) >= 0) {
                            var cols = records[i].split(US);
                            var idx = -1;
                            for (var j = 0; j < cols.length; j++) {
                                if (cols[j].indexOf(colName) >= 0) { idx = j; break; }
                            }
                            if (idx >= 0 && i + 1 < records.length) {
                                var vals = records[i + 1].split(US);
                                if (idx < vals.length) {
                                    vals[idx] = newVal;
                                    records[i + 1] = vals.join(US);
                                }
                            }
                            break;
                        }
                    }
                    return records.join(RS);
                }

                // ★ 핵심: strChitNoList에 ('전표번호') 형식으로 치환
                body = replaceCol(body, 'strChitNoList', "('" + chitNo + "')");
                body = replaceCol(body, 'strChitYmd', chitYmd);
                // strChitDiv = CHIT_ID (폐기=04, 센터매입=00 등)
                body = replaceCol(body, 'strChitDiv', chitId);
                // strChitNo는 빈 값 유지 (서버가 strChitNoList 참조)
                body = replaceCol(body, 'strChitNo', '');
                if (storeCd) {
                    body = replaceCol(body, 'strStoreCd', storeCd);
                }

                var resp = await fetch('/stgj020/searchDetailType1', {
                    method: 'POST',
                    headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                    body: body,
                    signal: AbortSignal.timeout(timeoutMs)
                });
                return await resp.text();
            """, self._detail_template, chit_no, chit_ymd,
                chit_id, store_cd or '', self.timeout_ms)

            if not ssv_text:
                return []

            return parse_waste_slip_detail(ssv_text)

        except Exception as e:
            logger.warning(
                f"[WasteSlipDetailAPI] 전표 상세 조회 실패 "
                f"(전표={chit_no}): {e}"
            )
            return []

    def fetch_all_slip_details(
        self,
        slip_list: List[Dict[str, Any]],
        delay: float = 0.3,
    ) -> List[Dict[str, Any]]:
        """전표 목록의 상세 품목 일괄 조회

        Args:
            slip_list: 전표 헤더 목록 [{CHIT_NO, CHIT_YMD, CHIT_FLAG, ...}]
            delay: 요청 간 딜레이 (초)

        Returns:
            전체 상세 품목 리스트 (각 품목에 CHIT_NO, CHIT_YMD 부착)
        """
        if not self._detail_template or not slip_list:
            return []

        all_items: List[Dict[str, Any]] = []
        ok_count = 0

        for idx, slip in enumerate(slip_list):
            chit_no = str(slip.get('CHIT_NO', '') or '')
            chit_ymd = str(slip.get('CHIT_YMD', '') or '')
            # CHIT_ID = 전표구분 ID (폐기=04). CHIT_FLAG(10)와 다른 코드 체계
            chit_id = str(slip.get('CHIT_ID', '') or '04')

            if not chit_no:
                continue

            items = self.fetch_slip_details(
                chit_no=chit_no,
                chit_ymd=chit_ymd,
                chit_id=chit_id,
            )

            if items:
                # 각 품목에 전표 정보 부착
                for item in items:
                    item['CHIT_NO'] = chit_no
                    item['CHIT_YMD'] = chit_ymd
                all_items.extend(items)
                ok_count += 1

            # 요청 간 딜레이 (서버 부하 방지)
            if delay > 0 and idx < len(slip_list) - 1:
                time.sleep(delay)

        logger.info(
            f"[WasteSlipDetailAPI] 전표 상세 조회: "
            f"{ok_count}/{len(slip_list)} 성공, "
            f"총 {len(all_items)}건 품목"
        )
        return all_items
