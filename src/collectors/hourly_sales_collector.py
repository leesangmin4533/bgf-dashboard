"""
HourlySalesCollector — STMB010 시간대별 매출 Direct API 수집기

BGF 리테일 STMB010 화면의 `/stmb010/selDay` API를 Direct로 호출하여
시간대별(00~23시) 매출 데이터를 수집합니다.

용도:
- 시간대별 매출액/판매건수 수집 (24행/일)
- DELIVERY_TIME_DEMAND_RATIO 동적 계산의 데이터 소스

사용:
    collector = HourlySalesCollector(driver)
    data = collector.collect("2026-02-27")
    # → [{'hour': 0, 'sale_amt': 125000, 'sale_cnt': 15, ...}, ...]
"""

import time
from typing import Any, Dict, List, Optional

from src.collectors.direct_api_fetcher import parse_ssv_dataset, ssv_row_to_dict
from src.utils.logger import get_logger

logger = get_logger(__name__)

# SSV 구분자
RS = '\u001e'
US = '\u001f'

# STMB010 API 설정
STMB010_ENDPOINT = "/stmb010/selDay"
STMB010_DATASET_MARKER = "HMS"

# XHR 인터셉터 JS (STMB010 전용)
INTERCEPTOR_JS = """
if (!window.__hourlyCaptures) {
    window.__hourlyCaptures = [];
}
if (!window.__hourlyInterceptorInstalled) {
    window.__hourlyInterceptorInstalled = true;
    var origOpen = XMLHttpRequest.prototype.open;
    var origSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function(method, url) {
        this.__captureUrl = url;
        this.__captureMethod = method;
        return origOpen.apply(this, arguments);
    };

    XMLHttpRequest.prototype.send = function(body) {
        var url = this.__captureUrl || '';
        if (url.indexOf('stmb010') >= 0) {
            window.__hourlyCaptures.push({
                url: url,
                method: this.__captureMethod,
                body: body ? String(body) : '',
                timestamp: new Date().toISOString()
            });
        }
        return origSend.apply(this, arguments);
    };
}
"""


class HourlySalesCollector:
    """STMB010 시간대별 매출 Direct API 수집기"""

    def __init__(self, driver: Any = None):
        self.driver = driver
        self._template: Optional[str] = None

    def collect(self, target_date: str) -> List[Dict]:
        """시간대별 매출 수집

        Args:
            target_date: 'YYYYMMDD' 또는 'YYYY-MM-DD' 형식

        Returns:
            [{'hour': 0, 'sale_amt': 125000.0, 'sale_cnt': 15, ...}, ...]
            실패 시 빈 리스트
        """
        # 날짜 형식 정규화 (YYYYMMDD)
        date_str = target_date.replace('-', '')
        if len(date_str) != 8:
            logger.error(f"[HourlySales] 잘못된 날짜 형식: {target_date}")
            return []

        try:
            # 1. body 템플릿 확보
            template = self._ensure_template()
            if not template:
                logger.warning("[HourlySales] body 템플릿 캡처 실패")
                return []

            # 2. 날짜 파라미터 치환
            body = self._replace_date_param(template, date_str)

            # 3. Direct API 호출
            ssv_text = self._call_api(body)
            if not ssv_text:
                logger.warning(f"[HourlySales] API 응답 없음 ({date_str})")
                return []

            # 4. SSV 파싱
            result = self._parse_response(ssv_text)
            if result:
                logger.info(f"[HourlySales] {date_str} → {len(result)}건 수집")
            return result

        except Exception as e:
            logger.error(f"[HourlySales] 수집 실패 ({date_str}): {e}")
            return []

    def _ensure_template(self) -> Optional[str]:
        """body 템플릿 확보 (캐시 우선, 없으면 인터셉터 캡처)"""
        if self._template:
            return self._template

        if not self.driver:
            logger.error("[HourlySales] 드라이버 없음 — 템플릿 캡처 불가")
            return None

        try:
            # 인터셉터 설치
            self.driver.execute_script(INTERCEPTOR_JS)
            logger.debug("[HourlySales] XHR 인터셉터 설치 완료")

            # STMB010 화면 진입 (메뉴 클릭)
            self._navigate_to_stmb010()
            time.sleep(3)  # 화면 로딩 + 자동 API 호출 대기

            # 캡처된 body 확인
            captures = self.driver.execute_script(
                "return window.__hourlyCaptures || []"
            )

            for cap in captures:
                url = cap.get('url', '')
                if 'stmb010' in url and cap.get('body'):
                    self._template = cap['body']
                    logger.info(
                        f"[HourlySales] 템플릿 캡처 성공 "
                        f"(body {len(self._template)}자)"
                    )
                    return self._template

            logger.warning(
                f"[HourlySales] stmb010 캡처 0건 "
                f"(전체 {len(captures)}건)"
            )
            return None

        except Exception as e:
            logger.error(f"[HourlySales] 템플릿 캡처 에러: {e}")
            return None

    def _navigate_to_stmb010(self) -> None:
        """STMB010 화면으로 이동 (매출분석 > 시간대별 매출 정보)"""
        js = """
        try {
            var app = nexacro.getApplication();
            var topForm = app.mainframe.HFrameSet00.VFrameSet00.TopFrame.form;

            // 매출분석 메뉴 클릭
            var menuBtn = topForm.div_topMenu.form.STMB000_M0;
            if (menuBtn) {
                var rect = menuBtn.getOffsetRect();
                var evt = new nexacro.MouseEventInfo();
                evt.clientx = rect.left + 30;
                evt.clienty = rect.top + 10;
                menuBtn.onclick.fireEvent(menuBtn, evt);
            }

            // 서브메뉴 패널에서 STMB010 클릭 (200ms 딜레이)
            setTimeout(function() {
                var panel = topForm.pdiv_topMenu_STMB000_M0;
                if (panel && panel.form) {
                    var sub = panel.form.STMB010_M0;
                    if (sub) {
                        var rect2 = sub.getOffsetRect();
                        var evt2 = new nexacro.MouseEventInfo();
                        evt2.clientx = rect2.left + 30;
                        evt2.clienty = rect2.top + 10;
                        sub.onclick.fireEvent(sub, evt2);
                    }
                }
            }, 200);

            return true;
        } catch(e) {
            return e.message;
        }
        """
        result = self.driver.execute_script(js)
        if result is not True:
            # 폴백: DOM ID 기반 클릭
            self._navigate_fallback()

    def _navigate_fallback(self) -> None:
        """폴백: DOM 요소 직접 클릭으로 STMB010 이동"""
        try:
            # 매출분석 메뉴 클릭
            self.driver.execute_script("""
                var el = document.querySelector(
                    '[id*="STMB000_M0"][id$=":icontext"]'
                );
                if (el) {
                    el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                    el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
                    el.dispatchEvent(new MouseEvent('click', {bubbles:true}));
                }
            """)
            time.sleep(0.5)

            # 시간대별 매출 서브메뉴 클릭
            self.driver.execute_script("""
                var el = document.querySelector(
                    '[id*="STMB010_M0"][id$=":text"]'
                );
                if (el) {
                    el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                    el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
                    el.dispatchEvent(new MouseEvent('click', {bubbles:true}));
                }
            """)
        except Exception as e:
            logger.warning(f"[HourlySales] 폴백 네비게이션 실패: {e}")

    def _replace_date_param(self, template: str, date_str: str) -> str:
        """body 템플릿에서 날짜 파라미터 치환

        SSV body의 날짜 관련 필드를 target_date로 교체.
        calFromDay, calToDay 등의 날짜 파라미터를 RS 구분자 전까지 치환.
        """
        import re

        body = template
        # calFromDay=YYYYMMDD 패턴 치환
        body = re.sub(
            r'(calFromDay=)\d{8}',
            rf'\g<1>{date_str}',
            body
        )
        # calToDay=YYYYMMDD 패턴 치환
        body = re.sub(
            r'(calToDay=)\d{8}',
            rf'\g<1>{date_str}',
            body
        )
        # strYmd 패턴 치환 (일부 화면)
        body = re.sub(
            r'(strYmd=)\d{8}',
            rf'\g<1>{date_str}',
            body
        )
        return body

    def _call_api(self, body: str) -> Optional[str]:
        """fetch()로 Direct API 호출"""
        if not self.driver:
            return None

        js = """
        return await (async function() {
            try {
                var resp = await fetch(arguments[0], {
                    method: 'POST',
                    headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                    body: arguments[1]
                });
                if (!resp.ok) return null;
                return await resp.text();
            } catch(e) {
                return null;
            }
        })();
        """

        endpoint = f"https://store.bgfretail.com{STMB010_ENDPOINT}"

        try:
            result = self.driver.execute_script(
                f"""
                var callback = arguments[arguments.length - 1];
                fetch("{endpoint}", {{
                    method: 'POST',
                    headers: {{'Content-Type': 'text/plain;charset=UTF-8'}},
                    body: arguments[0]
                }})
                .then(function(r) {{ return r.text(); }})
                .then(function(t) {{ callback(t); }})
                .catch(function(e) {{ callback(null); }});
                """,
                body
            )
            return result
        except Exception:
            # execute_async_script 폴백
            try:
                result = self.driver.execute_async_script(
                    """
                    var callback = arguments[arguments.length - 1];
                    fetch(arguments[0], {
                        method: 'POST',
                        headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                        body: arguments[1]
                    })
                    .then(function(r) { return r.text(); })
                    .then(function(t) { callback(t); })
                    .catch(function(e) { callback(null); });
                    """,
                    endpoint,
                    body
                )
                return result
            except Exception as e:
                logger.error(f"[HourlySales] API 호출 실패: {e}")
                return None

    def _parse_response(self, ssv_text: str) -> List[Dict]:
        """SSV 응답에서 dsList(시간대별 매출) 파싱

        Returns:
            [{'hour': 0, 'sale_amt': 125000.0, 'sale_cnt': 15, ...}, ...]
        """
        parsed = parse_ssv_dataset(ssv_text, STMB010_DATASET_MARKER)
        if not parsed:
            logger.warning("[HourlySales] SSV 파싱 실패 — HMS 데이터셋 미발견")
            return []

        columns = parsed['columns']
        result = []

        for row_vals in parsed['rows']:
            d = ssv_row_to_dict(columns, row_vals)

            hour_str = d.get('HMS', d.get('_RowType_', ''))
            if not hour_str or not hour_str.strip().isdigit():
                continue

            entry = {
                'hour': int(hour_str.strip()),
                'sale_amt': self._safe_float(d.get('AMT', '0')),
                'sale_cnt': self._safe_int(d.get('CNT', '0')),
                'sale_cnt_danga': self._safe_float(d.get('CNT_DANGA', '0')),
                'rate': self._safe_float(d.get('RATE', '0')),
            }

            # 유효 시간대만 (0~23)
            if 0 <= entry['hour'] <= 23:
                result.append(entry)

        return sorted(result, key=lambda x: x['hour'])

    @staticmethod
    def _safe_float(val: str) -> float:
        """안전한 float 변환"""
        try:
            return float(val) if val else 0.0
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _safe_int(val: str) -> int:
        """안전한 int 변환"""
        try:
            return int(float(val)) if val else 0
        except (ValueError, TypeError):
            return 0
