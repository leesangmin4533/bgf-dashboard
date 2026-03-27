"""
Direct Popup Fetcher - CallItemDetailPopup API 직접 호출

Selenium 팝업 조작 없이 /stbjz00/selItemDetailSearch, /stbjz00/selItemDetailOrd
엔드포인트에 직접 HTTP 요청을 보내 dsItemDetail, dsItemDetailOrd 데이터를 조회합니다.

로그인된 Selenium 브라우저 내에서 fetch()를 실행하여 쿠키/세션을 자동 공유합니다.

사용처:
    - product_detail_batch_collector: 상품 상세 일괄 수집
    - fail_reason_collector: 발주 실패 사유 조회
    - promotion_collector: 행사 정보 조회

캡처된 엔드포인트 (2026-02-27):
    1. /stbjz00/selItemDetailSearch → dsItemDetail (98 cols)
    2. /stbjz00/selItemDetailOrd    → dsItemDetailOrd (30 cols)
    3. /stbjz00/selItemDetailSale   → dsOrderSale (90일 이력)
"""

import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from src.collectors.direct_api_fetcher import (
    parse_ssv_dataset,
    ssv_row_to_dict,
    _safe_int,
    _clean_text,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

# SSV 구분자
RS = '\u001e'  # Record Separator
US = '\u001f'  # Unit Separator

# API 엔드포인트
ENDPOINT_DETAIL = '/stbjz00/selItemDetailSearch'
ENDPOINT_ORD = '/stbjz00/selItemDetailOrd'
ENDPOINT_SALE = '/stbjz00/selItemDetailSale'


# ═══════════════════════════════════════════════════════════════════
# SSV 응답 파싱
# ═══════════════════════════════════════════════════════════════════

def parse_popup_detail(ssv_text: str) -> Optional[Dict[str, str]]:
    """selItemDetailSearch SSV 응답 → dsItemDetail 첫 행 딕셔너리"""
    ds = parse_ssv_dataset(ssv_text, 'ITEM_NM')
    if ds and ds['rows']:
        return ssv_row_to_dict(ds['columns'], ds['rows'][0])
    return None


def parse_popup_ord(ssv_text: str) -> Optional[Dict[str, str]]:
    """selItemDetailOrd SSV 응답 → dsItemDetailOrd 첫 행 딕셔너리"""
    ds = parse_ssv_dataset(ssv_text, 'ORD_ADAY')
    if ds and ds['rows']:
        return ssv_row_to_dict(ds['columns'], ds['rows'][0])
    return None


def parse_popup_sale(ssv_text: str) -> List[Dict[str, str]]:
    """selItemDetailSale SSV 응답 → dsOrderSale 전체 행 리스트"""
    ds = parse_ssv_dataset(ssv_text, 'ORD_QTY')
    if not ds:
        return []
    return [ssv_row_to_dict(ds['columns'], row) for row in ds['rows']]


def extract_product_detail(
    detail_row: Optional[Dict[str, str]],
    ord_row: Optional[Dict[str, str]],
    item_cd: str,
) -> Optional[Dict[str, Any]]:
    """
    product_detail_batch_collector._extract_detail()과 동일한 구조로 변환

    Returns:
        item_cd, item_nm, mid_cd, large_cd, small_cd,
        expiration_days, orderable_day, orderable_status,
        order_unit_qty, order_unit_name, sell_price,
        order_stop_date, ... 등
    """
    if not detail_row:
        return None

    item_nm = detail_row.get('ITEM_NM', '')
    if not item_nm:
        return None

    r = {
        'item_cd': detail_row.get('ITEM_CD') or detail_row.get('PLU_CD') or item_cd,
        'item_nm': item_nm,

        # 카테고리 3단계
        'mid_cd': detail_row.get('MID_CD') or detail_row.get('MCLS_CD') or None,
        'large_cd': detail_row.get('LARGE_CD') or detail_row.get('LCLS_CD') or None,
        'large_nm': detail_row.get('LARGE_NM') or detail_row.get('LCLS_NM') or None,
        'mid_nm': detail_row.get('MID_NM') or detail_row.get('MCLS_NM') or None,
        'small_cd': detail_row.get('SMALL_CD') or detail_row.get('SCLS_CD') or None,
        'small_nm': detail_row.get('SMALL_NM') or detail_row.get('SCLS_NM') or None,
        'class_nm': detail_row.get('CLASS_NM') or None,

        # 유통기한
        'expiration_days': _safe_int(detail_row.get('EXPIRE_DAY')) or None,

        # 발주 정보 (dsItemDetail 기본값)
        'orderable_day': None,
        'orderable_status': (
            detail_row.get('ORD_PSS_ID_NM') or None
        ),
        'order_unit_qty': _safe_int(detail_row.get('ORD_UNIT_QTY')) or None,
        'order_unit_name': detail_row.get('ORD_UNIT_NM') or None,
        'case_unit_qty': _safe_int(detail_row.get('CASE_UNIT_QTY')) or None,

        # 가격
        'sell_price': (
            _safe_int(detail_row.get('SELL_PRC'))
            or _safe_int(detail_row.get('ITEM_MAEGA'))
            or _safe_int(detail_row.get('MAEGA_AMT'))
            or None
        ),

        # 정지 사유
        'order_stop_date': (
            detail_row.get('ORD_STOP_SYMD')
            or detail_row.get('ORD_STOP_DT')
            or None
        ),
        'order_stop_end_date': detail_row.get('ORD_STOP_EYMD') or None,
    }

    # dsItemDetailOrd 보완 (우선순위 높음)
    if ord_row:
        ord_aday = ord_row.get('ORD_ADAY')
        if ord_aday:
            r['orderable_day'] = ord_aday
        ord_status = ord_row.get('ORD_PSS_CHK_NM')
        if ord_status:
            r['orderable_status'] = ord_status
        ord_unit = _safe_int(ord_row.get('ORD_UNIT_QTY'))
        if ord_unit:
            r['order_unit_qty'] = ord_unit
        ord_stop_s = ord_row.get('ORD_STOP_SYMD')
        if ord_stop_s:
            r['order_stop_date'] = ord_stop_s
        ord_stop_e = ord_row.get('ORD_STOP_EYMD')
        if ord_stop_e:
            r['order_stop_end_date'] = ord_stop_e

    return r


def extract_fail_reason(
    detail_row: Optional[Dict[str, str]],
    ord_row: Optional[Dict[str, str]],
    item_cd: str,
) -> Optional[Dict[str, Any]]:
    """
    fail_reason_collector 호환 형식으로 변환

    Returns:
        item_cd, orderable_status, orderable_day,
        order_stop_date, stop_reason, ...
    """
    if not detail_row:
        return None

    r = {
        'item_cd': detail_row.get('ITEM_CD') or item_cd,
        'item_nm': detail_row.get('ITEM_NM') or '',
        'orderable_status': detail_row.get('ORD_PSS_ID_NM') or None,
        'orderable_day': None,
        'order_stop_date': (
            detail_row.get('ORD_STOP_SYMD')
            or detail_row.get('ORD_STOP_DT')
            or None
        ),
        'order_stop_end_date': detail_row.get('ORD_STOP_EYMD') or None,
        'stop_reason': None,
        'reason_id': detail_row.get('REASON_ID') or None,
    }

    # dsItemDetailOrd 보완
    if ord_row:
        r['orderable_day'] = ord_row.get('ORD_ADAY') or None
        r['orderable_status'] = (
            ord_row.get('ORD_PSS_CHK_NM')
            or r['orderable_status']
        )
        ord_stop = ord_row.get('ORD_STOP_SYMD')
        if ord_stop:
            r['order_stop_date'] = ord_stop
        ord_stop_e = ord_row.get('ORD_STOP_EYMD')
        if ord_stop_e:
            r['order_stop_end_date'] = ord_stop_e

    # 정지 사유 추론 (Selenium 버전과 동일)
    if not r['stop_reason'] and r['order_stop_date']:
        r['stop_reason'] = '일시공급불가'

    return r


def extract_promotion(
    detail_row: Optional[Dict[str, str]],
    item_cd: str,
) -> Optional[Dict[str, Any]]:
    """
    promotion_collector 호환 형식으로 변환

    dsItemDetail.EVT01 필드에 행사 텍스트가 포함됨:
      "당월 : 1+1 | 26.01.01~26.01.31 | 행사 요일 : 매일 | 방식 : 판촉비"
    """
    if not detail_row:
        return None

    evt_text = detail_row.get('EVT01') or ''
    evt_mobi = detail_row.get('EVT01_MOBI') or ''

    return {
        'item_cd': detail_row.get('ITEM_CD') or item_cd,
        'item_nm': detail_row.get('ITEM_NM') or '',
        'evt_text': evt_text,
        'evt_mobi': evt_mobi,
    }


# ═══════════════════════════════════════════════════════════════════
# DirectPopupFetcher 클래스
# ═══════════════════════════════════════════════════════════════════

class DirectPopupFetcher:
    """
    CallItemDetailPopup Direct API 호출

    로그인된 Selenium 브라우저 내에서 fetch()로
    /stbjz00/selItemDetailSearch + /stbjz00/selItemDetailOrd를 직접 호출합니다.

    Usage:
        fetcher = DirectPopupFetcher(driver)
        if fetcher.capture_template():
            data = fetcher.fetch_item_detail("8809196620052")
            # or batch:
            results = fetcher.fetch_items_batch(["8809196620052", ...])
    """

    def __init__(
        self,
        driver: Any,
        concurrency: int = 5,
        timeout_ms: int = 8000,
    ):
        self.driver = driver
        self.concurrency = concurrency
        self.timeout_ms = timeout_ms
        self._detail_template: Optional[str] = None
        self._ord_template: Optional[str] = None
        self._ord_ymd: Optional[str] = None

    # ──────────────────────────────────────────
    # 템플릿 캡처 (1회)
    # ──────────────────────────────────────────

    def capture_template(self) -> bool:
        """
        브라우저에서 XHR 인터셉터를 설치하고 CallItemDetailPopup을 1회 트리거하여
        selItemDetailSearch/selItemDetailOrd의 body 템플릿을 캡처

        이미 캡처된 요청이 __popupCaptures에 있으면 그것을 사용합니다.

        Returns:
            템플릿 캡처 성공 여부
        """
        try:
            templates = self.driver.execute_script(r"""
                var caps = window.__popupCaptures || [];
                var detail = null, ord = null;
                for (var i = 0; i < caps.length; i++) {
                    var c = caps[i];
                    if (!detail && c.url && c.url.indexOf('selItemDetailSearch') >= 0) {
                        detail = c.bodyPreview || '';
                    }
                    if (!ord && c.url && c.url.indexOf('selItemDetailOrd') >= 0) {
                        ord = c.bodyPreview || '';
                    }
                }
                return {detail: detail, ord: ord};
            """)

            if templates and templates.get('detail'):
                self._detail_template = templates['detail']
                self._ord_template = templates.get('ord')
                logger.info(
                    f"[PopupAPI] 템플릿 캡처 성공: "
                    f"detail={len(self._detail_template)}B, "
                    f"ord={len(self._ord_template or '')}B"
                )
                return True

        except Exception as e:
            logger.warning(f"[PopupAPI] 인터셉터 캡처 실패: {e}")

        return False

    def set_templates(self, detail_body: str, ord_body: str = None):
        """템플릿을 직접 설정 (테스트용)"""
        self._detail_template = detail_body
        self._ord_template = ord_body

    @property
    def has_template(self) -> bool:
        return bool(self._detail_template)

    # ──────────────────────────────────────────
    # 단일 상품 조회
    # ──────────────────────────────────────────

    def fetch_item_detail(
        self,
        item_cd: str,
        ord_ymd: str = None,
        include_ord: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        단일 상품 상세 조회 (selItemDetailSearch + selItemDetailOrd)

        Args:
            item_cd: 상품코드 (바코드)
            ord_ymd: 발주일자 (기본: 내일)
            include_ord: dsItemDetailOrd도 함께 조회할지

        Returns:
            {'detail': {dsItemDetail 파싱}, 'ord': {dsItemDetailOrd 파싱}} 또는 None
        """
        if not self._detail_template:
            logger.error("[PopupAPI] 템플릿 없음")
            return None

        if not ord_ymd:
            from datetime import timedelta
            ord_ymd = (datetime.now() + timedelta(days=1)).strftime('%Y%m%d')

        try:
            raw = self.driver.execute_script("""
                var detailTmpl = arguments[0];
                var ordTmpl = arguments[1];
                var itemCd = arguments[2];
                var ordYmd = arguments[3];
                var timeoutMs = arguments[4];
                var includeOrd = arguments[5];

                // strItemCd=바코드 교체 (RS 구분자까지)
                function replaceParam(body, key, val) {
                    var re = new RegExp(key + '=[^\\u001e]*');
                    return body.replace(re, key + '=' + val);
                }

                var detailBody = replaceParam(detailTmpl, 'strItemCd', itemCd);
                detailBody = replaceParam(detailBody, 'strOrdYmd', ordYmd);

                async function doFetch(url, body) {
                    var resp = await fetch(url, {
                        method: 'POST',
                        headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                        body: body,
                        signal: AbortSignal.timeout(timeoutMs)
                    });
                    return await resp.text();
                }

                var result = {};
                result.detail = await doFetch('/stbjz00/selItemDetailSearch', detailBody);

                if (includeOrd && ordTmpl) {
                    var ordBody = replaceParam(ordTmpl, 'strItemCd', itemCd);
                    ordBody = replaceParam(ordBody, 'strOrdYmd', ordYmd);
                    result.ord = await doFetch('/stbjz00/selItemDetailOrd', ordBody);
                }

                return result;
            """, self._detail_template, self._ord_template or '',
                item_cd, ord_ymd, self.timeout_ms, include_ord)

            if not raw:
                return None

            detail_row = parse_popup_detail(raw.get('detail', ''))
            ord_row = parse_popup_ord(raw.get('ord', '')) if raw.get('ord') else None

            return {
                'detail': detail_row,
                'ord': ord_row,
                'raw_detail': raw.get('detail', ''),
                'raw_ord': raw.get('ord', ''),
            }

        except Exception as e:
            logger.warning(f"[PopupAPI] {item_cd} 조회 실패: {e}")
            return None

    # ──────────────────────────────────────────
    # 배치 조회 (JS worker pool)
    # ──────────────────────────────────────────

    def fetch_items_batch(
        self,
        item_codes: List[str],
        ord_ymd: str = None,
        include_ord: bool = True,
        on_progress: Optional[Callable[[int, int], None]] = None,
        delay_ms: int = 50,
    ) -> Dict[str, Dict[str, Any]]:
        """
        여러 상품 배치 조회 (브라우저 내 비동기 병렬)

        Args:
            item_codes: 상품코드 목록
            ord_ymd: 발주일자 (기본: 내일)
            include_ord: dsItemDetailOrd도 함께 조회
            on_progress: 진행 콜백 (processed, total)
            delay_ms: 요청 간 딜레이 (ms)

        Returns:
            {item_cd: {'detail': {...}, 'ord': {...}}, ...}
        """
        if not self._detail_template:
            logger.error("[PopupAPI] 템플릿 없음")
            return {}

        if not item_codes:
            return {}

        if not ord_ymd:
            from datetime import timedelta
            ord_ymd = (datetime.now() + timedelta(days=1)).strftime('%Y%m%d')

        total = len(item_codes)
        logger.info(
            f"[PopupAPI] 배치 조회 시작: {total}개 (concurrency={self.concurrency})"
        )
        start_time = time.time()

        try:
            raw_results = self.driver.execute_script("""
                var detailTmpl = arguments[0];
                var ordTmpl = arguments[1];
                var barcodes = arguments[2];
                var ordYmd = arguments[3];
                var concurrency = arguments[4];
                var timeoutMs = arguments[5];
                var delayMs = arguments[6];
                var includeOrd = arguments[7];

                function replaceParam(body, key, val) {
                    var re = new RegExp(key + '=[^\\u001e]*');
                    return body.replace(re, key + '=' + val);
                }

                async function fetchOne(barcode) {
                    var dBody = replaceParam(detailTmpl, 'strItemCd', barcode);
                    dBody = replaceParam(dBody, 'strOrdYmd', ordYmd);
                    var r = {barcode: barcode};
                    try {
                        var resp = await fetch('/stbjz00/selItemDetailSearch', {
                            method: 'POST',
                            headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                            body: dBody,
                            signal: AbortSignal.timeout(timeoutMs)
                        });
                        r.detail = await resp.text();
                        r.ok = true;
                    } catch(e) {
                        r.error = e.message;
                        r.ok = false;
                        return r;
                    }
                    if (includeOrd && ordTmpl) {
                        try {
                            var oBody = replaceParam(ordTmpl, 'strItemCd', barcode);
                            oBody = replaceParam(oBody, 'strOrdYmd', ordYmd);
                            var resp2 = await fetch('/stbjz00/selItemDetailOrd', {
                                method: 'POST',
                                headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                                body: oBody,
                                signal: AbortSignal.timeout(timeoutMs)
                            });
                            r.ord = await resp2.text();
                        } catch(e) {
                            r.ordError = e.message;
                        }
                    }
                    return r;
                }

                var results = [];
                var idx = 0;
                async function worker() {
                    while (idx < barcodes.length) {
                        var myIdx = idx++;
                        var r = await fetchOne(barcodes[myIdx]);
                        results.push(r);
                        if (delayMs > 0) {
                            await new Promise(res => setTimeout(res, delayMs));
                        }
                    }
                }

                var workers = [];
                for (var w = 0; w < Math.min(concurrency, barcodes.length); w++) {
                    workers.push(worker());
                }
                await Promise.all(workers);
                return results;
            """, self._detail_template, self._ord_template or '',
                item_codes, ord_ymd, self.concurrency,
                self.timeout_ms, delay_ms, include_ord)

        except Exception as e:
            logger.error(f"[PopupAPI] 배치 JS 실행 실패: {e}")
            return {}

        elapsed = time.time() - start_time

        # Python 측 파싱
        results = {}
        success_count = 0
        error_count = 0

        for entry in (raw_results or []):
            barcode = entry.get('barcode', '')
            if entry.get('ok') and entry.get('detail'):
                detail_row = parse_popup_detail(entry['detail'])
                ord_row = (
                    parse_popup_ord(entry['ord'])
                    if entry.get('ord') else None
                )
                if detail_row:
                    results[barcode] = {
                        'detail': detail_row,
                        'ord': ord_row,
                    }
                    success_count += 1
                else:
                    error_count += 1
            else:
                error_count += 1

            processed = success_count + error_count
            if on_progress and processed % 50 == 0:
                on_progress(processed, total)

        rate = total / elapsed if elapsed > 0 else 0
        logger.info(
            f"[PopupAPI] 배치 완료: {success_count}/{total}건 성공, "
            f"{error_count}건 실패, {elapsed:.1f}초 ({rate:.0f}건/초)"
        )

        return results

    # ──────────────────────────────────────────
    # 고수준 래퍼 (collector 통합용)
    # ──────────────────────────────────────────

    def fetch_product_details(
        self,
        item_codes: List[str],
        ord_ymd: str = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        product_detail_batch_collector 호환: 상품 상세 배치 조회

        Returns:
            {item_cd: extract_product_detail() 결과, ...}
        """
        raw = self.fetch_items_batch(
            item_codes, ord_ymd=ord_ymd,
            include_ord=True, on_progress=on_progress,
        )
        results = {}
        for item_cd, data in raw.items():
            extracted = extract_product_detail(
                data.get('detail'), data.get('ord'), item_cd
            )
            if extracted:
                results[item_cd] = extracted
        return results

    def fetch_fail_reasons(
        self,
        item_codes: List[str],
        ord_ymd: str = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        fail_reason_collector 호환: 발주 실패 사유 배치 조회

        Returns:
            {item_cd: extract_fail_reason() 결과, ...}
        """
        raw = self.fetch_items_batch(
            item_codes, ord_ymd=ord_ymd,
            include_ord=True, on_progress=on_progress,
        )
        results = {}
        for item_cd, data in raw.items():
            extracted = extract_fail_reason(
                data.get('detail'), data.get('ord'), item_cd
            )
            if extracted:
                results[item_cd] = extracted
        return results

    def fetch_promotions(
        self,
        item_codes: List[str],
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        promotion_collector 호환: 행사 정보 배치 조회

        dsItemDetail.EVT01에 행사 텍스트 포함 → ord 불필요

        Returns:
            {item_cd: extract_promotion() 결과, ...}
        """
        raw = self.fetch_items_batch(
            item_codes, include_ord=False, on_progress=on_progress,
        )
        results = {}
        for item_cd, data in raw.items():
            extracted = extract_promotion(data.get('detail'), item_cd)
            if extracted:
                results[item_cd] = extracted
        return results
