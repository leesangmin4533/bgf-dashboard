"""
Direct Sales Fetcher - 매출 화면(STMB011) SSV 직접 API 호출

Selenium UI 조작 없이 /stmb011/selSearch, /stmb011/selDetailSearch 엔드포인트에
직접 HTTP 요청을 보내 중분류별 매출 데이터를 수집합니다.

로그인된 Selenium 브라우저 내에서 fetch()를 실행하여 쿠키/세션을 자동 공유합니다.
"""

import time
from typing import Any, Callable, Dict, List, Optional

from src.collectors.direct_api_fetcher import parse_ssv_dataset, ssv_row_to_dict
from src.utils.logger import get_logger

logger = get_logger(__name__)

# SSV 구분자
RS = '\u001e'  # Record Separator
US = '\u001f'  # Unit Separator


def parse_sales_list_response(ssv_text: str) -> List[Dict[str, Any]]:
    """
    /stmb011/selSearch 응답에서 dsList(중분류 목록) 파싱

    Returns:
        [{MID_CD, MID_NM, SALE_QTY, SALE_AMT, RATE}, ...]
    """
    ds = parse_ssv_dataset(ssv_text, 'MID_CD')
    if not ds:
        return []

    categories = []
    for row_vals in ds['rows']:
        row = ssv_row_to_dict(ds['columns'], row_vals)
        categories.append({
            'MID_CD': row.get('MID_CD', ''),
            'MID_NM': row.get('MID_NM', ''),
            'SALE_QTY': _safe_int(row.get('SALE_QTY', '0')),
            'SALE_AMT': _safe_int(row.get('SALE_AMT', '0')),
            'RATE': row.get('RATE', '0'),
        })

    return categories


def parse_sales_detail_response(ssv_text: str, mid_cd: str, mid_nm: str) -> List[Dict[str, Any]]:
    """
    /stmb011/selDetailSearch 응답에서 dsDetail(상품별 상세) 파싱

    Returns:
        [{MID_CD, MID_NM, ITEM_CD, ITEM_NM, SALE_QTY, ORD_QTY, BUY_QTY, DISUSE_QTY, STOCK_QTY}, ...]
    """
    ds = parse_ssv_dataset(ssv_text, 'ITEM_CD')
    if not ds:
        return []

    items = []
    for row_vals in ds['rows']:
        row = ssv_row_to_dict(ds['columns'], row_vals)
        items.append({
            'MID_CD': mid_cd,
            'MID_NM': mid_nm,
            'ITEM_CD': row.get('ITEM_CD', ''),
            'ITEM_NM': row.get('ITEM_NM', ''),
            'SALE_QTY': _safe_int(row.get('SALE_QTY', '0')),
            'ORD_QTY': _safe_int(row.get('ORD_QTY', '0')),
            'BUY_QTY': _safe_int(row.get('BUY_QTY', '0')),
            'DISUSE_QTY': _safe_int(row.get('DISUSE_QTY', '0')),
            'STOCK_QTY': _safe_int(row.get('STOCK_QTY', '0')),
        })

    return items


def _safe_int(val: Any) -> int:
    """안전한 정수 변환"""
    if val is None or val == '':
        return 0
    try:
        return int(float(str(val)))
    except (ValueError, TypeError):
        return 0


class DirectSalesFetcher:
    """
    매출 화면(STMB011) Direct API 호출

    로그인된 Selenium 브라우저 내에서 fetch()를 실행하여
    /stmb011/selSearch, /stmb011/selDetailSearch에 직접 요청을 보냅니다.
    """

    def __init__(self, driver: Any, concurrency: int = 5, timeout_ms: int = 5000):
        """
        Args:
            driver: 로그인된 Selenium WebDriver
            concurrency: 동시 요청 수 (서버 부하 방지)
            timeout_ms: 개별 요청 타임아웃 (밀리초)
        """
        self.driver = driver
        self.concurrency = concurrency
        self.timeout_ms = timeout_ms
        self._search_template: Optional[str] = None
        self._detail_template: Optional[str] = None

    def install_interceptor(self) -> bool:
        """
        XHR 인터셉터를 설치하여 요청 body를 캡처할 준비

        Returns:
            설치 성공 여부
        """
        try:
            result = self.driver.execute_script("""
                if (window._salesFetcherInterceptor) return 'already';
                window._salesFetcherCaptures = [];

                var origOpen = XMLHttpRequest.prototype.open;
                var origSend = XMLHttpRequest.prototype.send;

                XMLHttpRequest.prototype.open = function(m, u) {
                    this._sfMethod = m;
                    this._sfUrl = u;
                    return origOpen.apply(this, arguments);
                };
                XMLHttpRequest.prototype.send = function(body) {
                    var xhr = this;
                    if (body && xhr._sfUrl) {
                        var url = xhr._sfUrl;
                        if (url.includes('stmb011/selSearch') || url.includes('stmb011/selDetailSearch')) {
                            window._salesFetcherCaptures.push({
                                url: url,
                                body: body
                            });
                        }
                    }
                    return origSend.apply(this, arguments);
                };

                window._salesFetcherInterceptor = true;
                return 'installed';
            """)
            logger.info(f"[DirectSales] 인터셉터: {result}")
            return True
        except Exception as e:
            logger.error(f"[DirectSales] 인터셉터 설치 실패: {e}")
            return False

    def capture_templates_from_interceptor(self) -> bool:
        """
        인터셉터에서 캡처된 요청 body를 템플릿으로 저장

        Returns:
            템플릿 캡처 성공 여부
        """
        try:
            captures = self.driver.execute_script(
                "return window._salesFetcherCaptures || []"
            )

            for cap in captures:
                url = cap.get('url', '')
                body = cap.get('body', '')
                if 'selDetailSearch' in url and not self._detail_template:
                    self._detail_template = body
                    logger.info(f"[DirectSales] selDetailSearch 템플릿 캡처 ({len(body)} bytes)")
                elif 'selSearch' in url and 'Detail' not in url and not self._search_template:
                    self._search_template = body
                    logger.info(f"[DirectSales] selSearch 템플릿 캡처 ({len(body)} bytes)")

            return bool(self._search_template)
        except Exception as e:
            logger.error(f"[DirectSales] 템플릿 캡처 실패: {e}")
            return False

    def set_templates(self, search_template: str, detail_template: Optional[str] = None) -> None:
        """요청 템플릿을 직접 설정"""
        self._search_template = search_template
        if detail_template:
            self._detail_template = detail_template

    def _build_detail_template_from_search(self, search_body: str) -> str:
        """
        selSearch 템플릿에서 selDetailSearch 템플릿 생성

        selSearch body에서 strGubun 제거, strMidCd 추가, Dataset:dsList → Dataset:dsDetail 변경
        """
        records = search_body.split(RS)
        new_records = []

        for rec in records:
            # strGubun 제거
            if rec.startswith('strGubun='):
                continue
            # Dataset:dsList → Dataset:dsDetail
            if rec.startswith('Dataset:dsList'):
                new_records.append('strMidCd=__MID_CD__')
                new_records.append(
                    'Dataset:dsDetail'
                )
                # 컬럼 정의도 변경
                continue
            # dsList 컬럼 정의 → dsDetail 컬럼 정의
            if '_RowType_' in rec and 'MID_CD' in rec and 'SALE_AMT' in rec:
                new_records.append(
                    '_RowType_' + US +
                    'MID_NM:STRING(256)' + US +
                    'ITEM_CD:STRING(256)' + US +
                    'ITEM_NM:STRING(256)' + US +
                    'SALE_QTY:INT(256)' + US +
                    'ORD_QTY:INT(256)' + US +
                    'BUY_QTY:INT(256)' + US +
                    'DISUSE_QTY:INT(256)' + US +
                    'STOCK_QTY:INT(256)'
                )
                continue
            new_records.append(rec)

        return RS.join(new_records)

    def fetch_categories(self, date_str: str) -> Optional[List[Dict[str, Any]]]:
        """
        /stmb011/selSearch 호출로 중분류 목록 조회

        Args:
            date_str: YYYYMMDD 형식 날짜

        Returns:
            [{MID_CD, MID_NM, SALE_QTY, SALE_AMT, RATE}, ...] 또는 None
        """
        if not self._search_template:
            logger.error("[DirectSales] selSearch 템플릿 없음")
            return None

        try:
            # 날짜 교체
            body = self._search_template
            body = _replace_ssv_param(body, 'strFromYmd', date_str)
            body = _replace_ssv_param(body, 'strToYmd', date_str)

            ssv_text = self.driver.execute_script("""
                var resp = await fetch('/stmb011/selSearch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
                    body: arguments[0],
                    signal: AbortSignal.timeout(arguments[1])
                });
                return await resp.text();
            """, body, self.timeout_ms)

            if not ssv_text:
                logger.warning("[DirectSales] selSearch 빈 응답")
                return None

            categories = parse_sales_list_response(ssv_text)
            logger.info(f"[DirectSales] selSearch → {len(categories)}개 중분류")
            return categories

        except Exception as e:
            logger.error(f"[DirectSales] selSearch 실패: {e}")
            return None

    def fetch_category_detail(self, date_str: str, mid_cd: str) -> Optional[str]:
        """
        /stmb011/selDetailSearch 호출로 단일 중분류 상세 조회 (SSV raw 반환)

        Args:
            date_str: YYYYMMDD 형식 날짜
            mid_cd: 중분류 코드 (예: "001")

        Returns:
            SSV 응답 텍스트 또는 None
        """
        template = self._detail_template
        if not template:
            logger.error("[DirectSales] selDetailSearch 템플릿 없음")
            return None

        body = _replace_ssv_param(template, 'strFromYmd', date_str)
        body = _replace_ssv_param(body, 'strToYmd', date_str)
        body = _replace_ssv_param(body, 'strMidCd', mid_cd)

        try:
            ssv_text = self.driver.execute_script("""
                var resp = await fetch('/stmb011/selDetailSearch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
                    body: arguments[0],
                    signal: AbortSignal.timeout(arguments[1])
                });
                return await resp.text();
            """, body, self.timeout_ms)
            return ssv_text
        except Exception as e:
            logger.warning(f"[DirectSales] {mid_cd} 상세 조회 실패: {e}")
            return None

    def fetch_all_categories_detail(
        self,
        date_str: str,
        categories: List[Dict[str, Any]],
        on_progress: Optional[Callable[[int, int], None]] = None,
        delay_ms: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        모든 중분류의 상세 데이터를 배치 병렬 조회

        Args:
            date_str: YYYYMMDD 형식 날짜
            categories: fetch_categories() 결과 [{MID_CD, MID_NM, ...}]
            on_progress: 진행 콜백 (processed, total)
            delay_ms: 요청 간 딜레이 (ms)

        Returns:
            [{MID_CD, MID_NM, ITEM_CD, ITEM_NM, SALE_QTY, ORD_QTY, BUY_QTY, DISUSE_QTY, STOCK_QTY}, ...]
        """
        template = self._detail_template
        if not template:
            logger.error("[DirectSales] selDetailSearch 템플릿 없음")
            return []

        if not categories:
            return []

        # 날짜 교체된 베이스 템플릿
        base_body = _replace_ssv_param(template, 'strFromYmd', date_str)
        base_body = _replace_ssv_param(base_body, 'strToYmd', date_str)

        mid_codes = [cat['MID_CD'] for cat in categories]
        mid_nm_map = {cat['MID_CD']: cat.get('MID_NM', '') for cat in categories}
        total = len(mid_codes)

        logger.info(
            f"[DirectSales] 배치 상세조회 시작: {total}개 중분류 "
            f"(concurrency={self.concurrency})"
        )
        start_time = time.time()

        try:
            raw_results = self.driver.execute_script("""
                var baseBody = arguments[0];
                var midCodes = arguments[1];
                var concurrency = arguments[2];
                var timeoutMs = arguments[3];
                var delayMs = arguments[4];

                async function fetchOne(midCd) {
                    var body = baseBody.replace(/strMidCd=[^\\u001e]*/, 'strMidCd=' + midCd);
                    try {
                        var resp = await fetch('/stmb011/selDetailSearch', {
                            method: 'POST',
                            headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
                            body: body,
                            signal: AbortSignal.timeout(timeoutMs)
                        });
                        var text = await resp.text();
                        return { midCd: midCd, text: text, ok: true };
                    } catch(e) {
                        return { midCd: midCd, error: e.message, ok: false };
                    }
                }

                var results = [];
                var idx = 0;

                async function worker() {
                    while (idx < midCodes.length) {
                        var myIdx = idx++;
                        var r = await fetchOne(midCodes[myIdx]);
                        results.push(r);
                        if (delayMs > 0) await new Promise(res => setTimeout(res, delayMs));
                    }
                }

                var workers = [];
                for (var w = 0; w < Math.min(concurrency, midCodes.length); w++) {
                    workers.push(worker());
                }
                await Promise.all(workers);

                return results;
            """, base_body, mid_codes, self.concurrency, self.timeout_ms, delay_ms)

        except Exception as e:
            logger.error(f"[DirectSales] 배치 조회 JS 실행 실패: {e}")
            return []

        elapsed = time.time() - start_time

        # SSV 응답 파싱
        all_items = []
        success_count = 0
        error_count = 0

        for entry in (raw_results or []):
            mid_cd = entry.get('midCd', '')
            mid_nm = mid_nm_map.get(mid_cd, '')

            if entry.get('ok') and entry.get('text'):
                items = parse_sales_detail_response(entry['text'], mid_cd, mid_nm)
                all_items.extend(items)
                success_count += 1
            else:
                error_count += 1
                logger.warning(
                    f"[DirectSales] {mid_cd} 실패: {entry.get('error', 'unknown')}"
                )

            processed = success_count + error_count
            if on_progress and processed % 10 == 0:
                on_progress(processed, total)

        rate = total / elapsed if elapsed > 0 else 0
        logger.info(
            f"[DirectSales] 배치 조회 완료: {success_count}/{total}건 성공, "
            f"{error_count}건 실패, {len(all_items)}개 상품, "
            f"{elapsed:.1f}초 ({rate:.0f}건/초)"
        )

        return all_items

    def collect_all(
        self,
        date_str: str,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        전체 플로우: 중분류 목록 조회 → 상세 데이터 배치 조회

        Args:
            date_str: YYYYMMDD 형식 날짜
            on_progress: 진행 콜백

        Returns:
            [{MID_CD, MID_NM, ITEM_CD, ITEM_NM, SALE_QTY, ORD_QTY, BUY_QTY, DISUSE_QTY, STOCK_QTY}, ...]
        """
        # 1. 중분류 목록 조회
        categories = self.fetch_categories(date_str)
        if not categories:
            logger.error("[DirectSales] 중분류 목록 조회 실패")
            return []

        # 2. 상세 데이터 배치 조회
        return self.fetch_all_categories_detail(
            date_str, categories, on_progress=on_progress
        )


def _replace_ssv_param(body: str, param_name: str, new_value: str) -> str:
    """SSV body에서 파라미터 값 교체"""
    import re
    pattern = param_name + r'=[^\x1e]*'
    replacement = f'{param_name}={new_value}'
    return re.sub(pattern, replacement, body)
