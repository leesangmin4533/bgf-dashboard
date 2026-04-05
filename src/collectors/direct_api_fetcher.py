"""
Direct API Fetcher - 넥사크로 SSV 프로토콜 직접 호출

Selenium UI 조작 없이 /stbj030/selSearch 엔드포인트에 직접 HTTP 요청을 보내
dsItem, dsOrderSale, gdList 데이터를 조회합니다.

로그인된 Selenium 브라우저 내에서 fetch()를 실행하여 쿠키/세션을 자동 공유합니다.
"""

import json
import time
from typing import Any, Callable, Dict, List, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

# SSV 구분자
RS = '\u001e'  # Record Separator
US = '\u001f'  # Unit Separator


def parse_ssv_dataset(ssv_text: str, dataset_marker: str) -> Optional[Dict[str, Any]]:
    """
    SSV 텍스트에서 특정 데이터셋의 컬럼 헤더와 데이터 행을 파싱

    Args:
        ssv_text: 전체 SSV 응답 텍스트
        dataset_marker: 데이터셋 식별용 컬럼명 (예: 'ITEM_NM' for dsItem)

    Returns:
        {'columns': [...], 'rows': [[...]]} 또는 None
    """
    if not ssv_text:
        return None

    records = ssv_text.split(RS)
    for i, record in enumerate(records):
        if '_RowType_' in record and dataset_marker in record:
            cols_raw = record.split(US)
            columns = [c.split(':')[0] for c in cols_raw]

            rows = []
            # 이후 레코드들이 데이터 행 (빈 레코드나 다음 헤더를 만나면 중지)
            for j in range(i + 1, len(records)):
                row_text = records[j].strip()
                if not row_text or '_RowType_' in row_text:
                    break
                vals = row_text.split(US)
                rows.append(vals)

            return {'columns': columns, 'rows': rows}

    return None


def ssv_row_to_dict(columns: List[str], row: List[str]) -> Dict[str, str]:
    """SSV 컬럼/행을 딕셔너리로 변환"""
    result = {}
    for idx, col in enumerate(columns):
        result[col] = row[idx] if idx < len(row) else ''
    return result


def parse_full_ssv_response(ssv_text: str) -> Dict[str, Any]:
    """
    /stbj030/selSearch SSV 응답 전체를 파싱하여 모든 데이터셋 추출

    Returns:
        {
            'dsItem': {'columns': [...], 'rows': [...]},
            'dsOrderSale': {'columns': [...], 'rows': [...]},
            'gdList': {'columns': [...], 'rows': [...]},
            'dsWeek': {'columns': [...], 'rows': [...]},
        }
    """
    result = {}

    # dsItem: ITEM_NM, NOW_QTY, ORD_UNIT_QTY, EXPIRE_DAY
    ds_item = parse_ssv_dataset(ssv_text, 'ITEM_NM')
    if ds_item:
        result['dsItem'] = ds_item

    # dsOrderSale: ORD_QTY, BUY_QTY, SALE_QTY, DISUSE_QTY
    ds_order_sale = parse_ssv_dataset(ssv_text, 'ORD_QTY')
    if ds_order_sale:
        result['dsOrderSale'] = ds_order_sale

    # gdList: MONTH_EVT, NEXT_MONTH_EVT, CUT_ITEM_YN, HQ_MAEGA_SET, PROFIT_RATE
    ds_gdlist = parse_ssv_dataset(ssv_text, 'MONTH_EVT')
    if ds_gdlist:
        result['gdList'] = ds_gdlist

    # dsWeek: ORD_YMD (날짜 목록)
    ds_week = parse_ssv_dataset(ssv_text, 'ORD_YMD')
    if ds_week and 'ORD_QTY' not in (ds_week.get('columns') or []):
        # ORD_YMD가 있지만 ORD_QTY가 없는 데이터셋이 dsWeek
        result['dsWeek'] = ds_week

    return result


def extract_item_data(parsed: Dict[str, Any], item_cd: str) -> Dict[str, Any]:
    """
    파싱된 SSV 데이터를 collect_for_item() 호환 형식으로 변환

    Args:
        parsed: parse_full_ssv_response()의 결과
        item_cd: 상품코드

    Returns:
        collect_for_item() 반환값과 동일한 구조의 딕셔너리
    """
    result = {
        'item_cd': item_cd,
        'item_nm': '',
        'current_stock': 0,
        'order_unit_qty': 1,
        'expiration_days': None,
        'current_month_promo': '',
        'next_month_promo': '',
        'is_cut_item': False,
        'is_empty_response': False,
        'sell_price': '',
        'margin_rate': '',
        'history': [],
        'week_dates': [],
        'success': False,
    }

    # dsItem 추출
    ds_item = parsed.get('dsItem')
    if ds_item and ds_item['rows']:
        row = ssv_row_to_dict(ds_item['columns'], ds_item['rows'][0])
        result['item_cd'] = row.get('ITEM_CD', item_cd)
        result['item_nm'] = row.get('ITEM_NM', '')
        result['current_stock'] = _safe_int(row.get('NOW_QTY', '0'))
        # ORD_UNIT_QTY: 빈값/0이면 None 유지 (1로 폴백하면 입수 불일치 과발주)
        _raw_unit = _safe_int(row.get('ORD_UNIT_QTY'))
        result['order_unit_qty'] = _raw_unit if _raw_unit and _raw_unit > 0 else None
        result['expiration_days'] = _safe_int(row.get('EXPIRE_DAY', '')) or None
        result['success'] = True
    elif ds_item and not ds_item['rows']:
        # CUT/미취급 상품: 헤더만 있고 행이 없음 (HTTP 200이지만 발주 불가)
        result['success'] = True
        result['is_empty_response'] = True
        result['is_cut_item'] = True

    # dsOrderSale 추출 (발주/입고/판매/폐기 이력)
    ds_order_sale = parsed.get('dsOrderSale')
    if ds_order_sale:
        for row_vals in ds_order_sale['rows']:
            row = ssv_row_to_dict(ds_order_sale['columns'], row_vals)
            result['history'].append({
                'date': row.get('ORD_YMD', ''),
                'item_cd': row.get('ITEM_CD', item_cd),
                'ord_qty': _safe_int(row.get('ORD_QTY', '0')),
                'buy_qty': _safe_int(row.get('BUY_QTY', '0')),
                'sale_qty': _safe_int(row.get('SALE_QTY', '0')),
                'disuse_qty': _safe_int(row.get('DISUSE_QTY', '0')),
            })

    # gdList 추출 (행사, CUT, 매가, 이익율)
    ds_gdlist = parsed.get('gdList')
    if ds_gdlist and ds_gdlist['rows']:
        # 마지막 행 사용 (Selenium 버전과 동일)
        last_row = ssv_row_to_dict(ds_gdlist['columns'], ds_gdlist['rows'][-1])
        result['current_month_promo'] = _clean_text(last_row.get('MONTH_EVT', ''))
        result['next_month_promo'] = _clean_text(last_row.get('NEXT_MONTH_EVT', ''))
        result['is_cut_item'] = last_row.get('CUT_ITEM_YN', '0') == '1'
        result['sell_price'] = last_row.get('HQ_MAEGA_SET', '')
        result['margin_rate'] = last_row.get('PROFIT_RATE', '')

    # dsWeek 추출 (날짜 목록)
    ds_week = parsed.get('dsWeek')
    if ds_week:
        for row_vals in ds_week['rows']:
            row = ssv_row_to_dict(ds_week['columns'], row_vals)
            date_val = row.get('ORD_YMD', '')
            if date_val:
                result['week_dates'].append(date_val)

    return result


def extract_dsitem_all_columns(ssv_text: str) -> Optional[Dict[str, str]]:
    """
    selSearch SSV 응답에서 dsItem 전체 컬럼→값 매핑 추출

    dsGeneralGrid 채우기용: dsItem 응답의 모든 컬럼 값을 그대로 반환합니다.
    dsItem 컬럼 구조가 dsGeneralGrid와 동일하므로 직접 매핑 가능합니다.

    Args:
        ssv_text: selSearch SSV 응답 전체 텍스트

    Returns:
        {'STORE_CD': '46513', 'ITEM_NM': '오뚜기)스파게티컵', ...} 또는 None
    """
    ds = parse_ssv_dataset(ssv_text, 'ITEM_NM')
    if not ds or not ds['rows']:
        return None
    row_dict = ssv_row_to_dict(ds['columns'], ds['rows'][0])
    # _RowType_ 제외 (addRow가 자동 설정)
    row_dict.pop('_RowType_', None)
    return row_dict


def _safe_int(val: Any) -> int:
    """안전한 정수 변환"""
    if val is None or val == '':
        return 0
    try:
        return int(float(str(val)))
    except (ValueError, TypeError):
        return 0


def _clean_text(val: str) -> str:
    """줄바꿈 제거 및 trim"""
    if not val:
        return ''
    return val.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ').strip()


class DirectApiFetcher:
    """
    넥사크로 SSV 프로토콜 기반 직접 API 호출

    로그인된 Selenium 브라우저 내에서 fetch()를 실행하여
    /stbj030/selSearch 엔드포인트에 직접 요청을 보냅니다.
    """

    def __init__(self, driver: Any, concurrency: int = 5, timeout_ms: int = 8000):
        """
        Args:
            driver: 로그인된 Selenium WebDriver
            concurrency: 동시 요청 수 (서버 부하 방지)
            timeout_ms: 개별 요청 타임아웃 (밀리초)
        """
        self.driver = driver
        self.concurrency = concurrency
        self.timeout_ms = timeout_ms
        self._request_template: Optional[str] = None

    def capture_request_template(self) -> bool:
        """
        브라우저에서 /stbj030/selSearch 요청 템플릿을 캡처

        단품별 발주 화면에서 1회 수동 검색 후, 해당 요청의 body를 캡처합니다.
        이미 _capturedRequests에 있으면 그것을 사용합니다.

        인터셉터 3중 설치:
        1. fetch() 패치 — 현대 브라우저 기본
        2. XMLHttpRequest 프로토타입 패치 — 일반 XHR
        3. 넥사크로 CommunicationManager 후킹 — 넥사크로 Transaction 요청

        Returns:
            캡처 성공 여부
        """
        try:
            template = self.driver.execute_script("""
                // 이미 캡처된 것이 있으면 사용
                if (window._capturedRequests && window._capturedRequests.length > 0) {
                    var cap = window._capturedRequests[0];
                    return cap.body || cap;
                }

                // 인터셉터 설치 (멱등)
                if (!window._requestInterceptorInstalled) {
                    window._capturedRequests = [];
                    var TARGET = 'stbj030/selSearch';

                    // === Layer 1: fetch() 패치 ===
                    var origFetch = window.fetch;
                    window.fetch = function(url, opts) {
                        if (typeof url === 'string' && url.indexOf(TARGET) >= 0) {
                            window._capturedRequests.push({
                                url: url,
                                body: opts && opts.body ? opts.body : '',
                                source: 'fetch'
                            });
                        }
                        return origFetch.apply(this, arguments);
                    };

                    // === Layer 2: XMLHttpRequest 프로토타입 패치 ===
                    var origXhrOpen = XMLHttpRequest.prototype.open;
                    var origXhrSend = XMLHttpRequest.prototype.send;
                    XMLHttpRequest.prototype.open = function(method, url) {
                        this._interceptUrl = url;
                        return origXhrOpen.apply(this, arguments);
                    };
                    XMLHttpRequest.prototype.send = function(body) {
                        if (this._interceptUrl && this._interceptUrl.indexOf(TARGET) >= 0) {
                            window._capturedRequests.push({
                                url: this._interceptUrl,
                                body: body || '',
                                source: 'xhr'
                            });
                        }
                        return origXhrSend.apply(this, arguments);
                    };

                    // === Layer 3: 넥사크로 통신 레이어 후킹 ===
                    try {
                        if (typeof nexacro !== 'undefined') {
                            // 방법 A: nexacro.__requestBodyCapture (넥사크로 Transaction 후킹)
                            var commProto = nexacro._CommunicationManager
                                         || (nexacro.CommunicationManager && nexacro.CommunicationManager.prototype);
                            if (commProto && commProto._sendRequest && !commProto._origSendRequest) {
                                commProto._origSendRequest = commProto._sendRequest;
                                commProto._sendRequest = function() {
                                    var url = arguments[0] || '';
                                    var body = arguments[1] || '';
                                    if (typeof url === 'string' && url.indexOf(TARGET) >= 0) {
                                        window._capturedRequests.push({
                                            url: url, body: body, source: 'nexacro_comm'
                                        });
                                    }
                                    return commProto._origSendRequest.apply(this, arguments);
                                };
                            }

                            // 방법 B: nexacro.HttpRequest 패치 (XHR 래퍼)
                            if (nexacro.HttpRequest && nexacro.HttpRequest.prototype.open
                                && !nexacro.HttpRequest.prototype._origOpen) {
                                var httpProto = nexacro.HttpRequest.prototype;
                                httpProto._origOpen = httpProto.open;
                                httpProto._origSend = httpProto.send;
                                httpProto.open = function(method, url) {
                                    this._nxInterceptUrl = url;
                                    return httpProto._origOpen.apply(this, arguments);
                                };
                                httpProto.send = function(body) {
                                    if (this._nxInterceptUrl
                                        && this._nxInterceptUrl.indexOf(TARGET) >= 0) {
                                        window._capturedRequests.push({
                                            url: this._nxInterceptUrl,
                                            body: body || '',
                                            source: 'nexacro_http'
                                        });
                                    }
                                    return httpProto._origSend.apply(this, arguments);
                                };
                            }
                        }
                    } catch(nxErr) {
                        // 넥사크로 후킹 실패해도 Layer 1,2로 동작 가능
                    }

                    window._requestInterceptorInstalled = true;
                }

                return null;
            """)

            if template:
                self._request_template = template
                logger.info("[DirectAPI] 요청 템플릿 캡처 성공 (기존 캡처)")
                return True

            logger.info("[DirectAPI] 인터셉터 설치됨 (3-layer) - 다음 검색 시 캡처 예정")
            return False

        except Exception as e:
            logger.error(f"[DirectAPI] 템플릿 캡처 실패: {e}")
            return False

    def set_request_template(self, template: str) -> None:
        """요청 템플릿을 직접 설정"""
        self._request_template = template

    def reset_interceptor(self) -> None:
        """인터셉터를 강제 재설치 (캡처 실패 시 재시도용)"""
        try:
            self.driver.execute_script("""
                window._requestInterceptorInstalled = false;
                window._capturedRequests = [];
            """)
            logger.info("[DirectAPI] 인터셉터 리셋 완료")
        except Exception as e:
            logger.warning(f"[DirectAPI] 인터셉터 리셋 실패: {e}")
        # 다음 capture_request_template 호출 시 재설치됨

    def ensure_template(self) -> bool:
        """
        요청 템플릿이 준비되었는지 확인하고, 없으면 캡처 시도

        Returns:
            템플릿 준비 여부
        """
        if self._request_template:
            return True

        # 브라우저에서 캡처 시도
        if self.capture_request_template():
            return True

        # 캡처된 것이 없으면, 재시도 (인터셉터 설치 후 대기)
        logger.info("[DirectAPI] 캡처 대기 중...")
        time.sleep(1)
        return self.capture_request_template()

    def fetch_item_data(self, item_cd: str) -> Optional[Dict[str, Any]]:
        """
        단일 상품 데이터 직접 조회

        Args:
            item_cd: 상품코드 (바코드)

        Returns:
            extract_item_data() 결과 또는 None (실패 시)
        """
        if not self._request_template:
            logger.error("[DirectAPI] 요청 템플릿 없음")
            return None

        try:
            ssv_text = self.driver.execute_script("""
                var body = arguments[0].replace(/strItemCd=[^\\u001e]*/, 'strItemCd=' + arguments[1]);
                var resp = await fetch('/stbj030/selSearch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
                    body: body,
                    signal: AbortSignal.timeout(arguments[2])
                });
                if (!resp.ok) return null;
                return await resp.text();
            """, self._request_template, item_cd, self.timeout_ms)

            if not ssv_text:
                return None

            parsed = parse_full_ssv_response(ssv_text)
            return extract_item_data(parsed, item_cd)

        except Exception as e:
            logger.warning(f"[DirectAPI] {item_cd} 조회 실패: {e}")
            return None

    def _validate_template(self, sample_item_cd: str) -> bool:
        """배치 실행 전 1건으로 템플릿 유효성 검증

        Args:
            sample_item_cd: 테스트용 상품코드

        Returns:
            True이면 템플릿 유효, False이면 재캡처 필요
        """
        if not self._request_template:
            return False

        try:
            probe = self.driver.execute_script("""
                var body = arguments[0].replace(/strItemCd=[^\\u001e]*/, 'strItemCd=' + arguments[1]);
                try {
                    var resp = await fetch('/stbj030/selSearch', {
                        method: 'POST',
                        headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
                        body: body,
                        signal: AbortSignal.timeout(arguments[2])
                    });
                    var text = await resp.text();
                    return { status: resp.status, ok: resp.ok, len: text.length,
                             hasItem: text.indexOf('ITEM_NM') > -1,
                             snippet: text.substring(0, 200) };
                } catch(e) {
                    return { status: 0, ok: false, error: e.message };
                }
            """, self._request_template, sample_item_cd, self.timeout_ms)

            if not probe:
                logger.warning("[DirectAPI] 템플릿 검증: probe 응답 없음")
                return False

            status = probe.get('status', 0)
            has_item = probe.get('hasItem', False)
            resp_len = probe.get('len', 0)

            if not probe.get('ok'):
                logger.warning(
                    f"[DirectAPI] 템플릿 검증 실패: HTTP {status}, "
                    f"error={probe.get('error', 'N/A')}"
                )
                return False

            if not has_item or resp_len < 100:
                snippet = probe.get('snippet', '')
                logger.warning(
                    f"[DirectAPI] 템플릿 검증 실패: HTTP {status}, "
                    f"len={resp_len}, hasItem={has_item}, "
                    f"snippet={snippet[:100]}"
                )
                return False

            logger.info(
                f"[DirectAPI] 템플릿 검증 성공: HTTP {status}, "
                f"len={resp_len}, hasItem=True"
            )
            return True

        except Exception as e:
            logger.warning(f"[DirectAPI] 템플릿 검증 예외: {e}")
            return False

    def fetch_items_batch(
        self,
        item_codes: List[str],
        on_progress: Optional[Callable[[int, int], None]] = None,
        delay_ms: int = 30,
    ) -> Dict[str, Dict[str, Any]]:
        """
        여러 상품 배치 조회 (브라우저 내 비동기 병렬)

        Args:
            item_codes: 상품코드 목록
            on_progress: 진행 콜백 (processed, total)
            delay_ms: 요청 간 딜레이 (ms)

        Returns:
            {item_cd: extract_item_data() 결과, ...}
        """
        if not self._request_template:
            logger.error("[DirectAPI] 요청 템플릿 없음")
            return {}

        if not item_codes:
            return {}

        total = len(item_codes)

        # 배치 전 1건 검증
        if not self._validate_template(item_codes[0]):
            logger.warning(
                "[DirectAPI] 템플릿 검증 실패 → 템플릿 무효화, Selenium 폴백 전환"
            )
            self._request_template = None
            return {}

        logger.info(f"[DirectAPI] 배치 조회 시작: {total}개 상품 (concurrency={self.concurrency})")
        start_time = time.time()

        # 브라우저 내에서 배치 처리 (JS async/await + concurrency 제한)
        try:
            raw_results = self.driver.execute_script("""
                var template = arguments[0];
                var barcodes = arguments[1];
                var concurrency = arguments[2];
                var timeoutMs = arguments[3];
                var delayMs = arguments[4];

                async function fetchOne(barcode) {
                    var body = template.replace(/strItemCd=[^\\u001e]*/, 'strItemCd=' + barcode);
                    try {
                        var resp = await fetch('/stbj030/selSearch', {
                            method: 'POST',
                            headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
                            body: body,
                            signal: AbortSignal.timeout(timeoutMs)
                        });
                        if (!resp.ok) {
                            return { barcode: barcode, error: 'HTTP ' + resp.status, ok: false, status: resp.status };
                        }
                        var text = await resp.text();
                        return { barcode: barcode, text: text, ok: true, status: resp.status };
                    } catch(e) {
                        return { barcode: barcode, error: e.message, ok: false, status: 0 };
                    }
                }

                // concurrency 제한 병렬 실행
                var results = [];
                var idx = 0;

                async function worker() {
                    while (idx < barcodes.length) {
                        var myIdx = idx++;
                        var r = await fetchOne(barcodes[myIdx]);
                        results.push(r);
                        if (delayMs > 0) await new Promise(res => setTimeout(res, delayMs));
                    }
                }

                var workers = [];
                for (var w = 0; w < Math.min(concurrency, barcodes.length); w++) {
                    workers.push(worker());
                }
                await Promise.all(workers);

                return results;
            """, self._request_template, item_codes, self.concurrency,
                self.timeout_ms, delay_ms)

        except Exception as e:
            logger.error(f"[DirectAPI] 배치 조회 JS 실행 실패: {e}")
            return {}

        elapsed = time.time() - start_time

        # SSV 응답 파싱 (Python 측) + 실패유형 진단
        results = {}
        success_count = 0
        js_error_count = 0    # JS fetch 자체 실패 (네트워크, 타임아웃, HTTP 에러)
        parse_error_count = 0  # fetch 성공했으나 SSV에 dsItem 없음
        sample_logged = False

        for entry in (raw_results or []):
            barcode = entry.get('barcode', '')
            if entry.get('ok') and entry.get('text'):
                parsed = parse_full_ssv_response(entry['text'])
                item_data = extract_item_data(parsed, barcode)
                if item_data.get('success'):
                    results[barcode] = item_data
                    success_count += 1
                else:
                    parse_error_count += 1
                    if not sample_logged:
                        resp_text = entry.get('text', '')
                        logger.warning(
                            f"[DirectAPI] SSV 파싱 실패 샘플 (HTTP {entry.get('status', '?')}): "
                            f"barcode={barcode}, len={len(resp_text)}, "
                            f"datasets={list(parsed.keys()) if parsed else 'empty'}, "
                            f"snippet={resp_text[:200]}"
                        )
                        sample_logged = True
            else:
                js_error_count += 1
                if not sample_logged:
                    logger.warning(
                        f"[DirectAPI] JS fetch 실패 샘플: "
                        f"barcode={barcode}, status={entry.get('status', '?')}, "
                        f"error={entry.get('error', 'N/A')}"
                    )
                    sample_logged = True

            processed = success_count + js_error_count + parse_error_count
            if on_progress and processed % 50 == 0:
                on_progress(processed, total)

        rate = total / elapsed if elapsed > 0 else 0
        error_detail = ""
        if js_error_count > 0 or parse_error_count > 0:
            error_detail = f" (JS에러={js_error_count}, SSV파싱실패={parse_error_count})"
        logger.info(
            f"[DirectAPI] 배치 조회 완료: {success_count}/{total}건 성공, "
            f"{js_error_count + parse_error_count}건 실패{error_detail}, "
            f"{elapsed:.1f}초 ({rate:.0f}건/초)"
        )

        return results
