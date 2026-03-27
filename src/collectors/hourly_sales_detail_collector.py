"""
HourlySalesDetailCollector -- 시간대별 매출 상세(품목별) Direct API 수집기

BGF 리테일 STMB010 팝업의 `/stmb010/selPrdT3` API를 Direct로 호출하여
시간대별(00~23시) 품목별 매출 상세 데이터를 수집합니다.

API 구조 (크롬 확장으로 확인):
- 엔드포인트: /stmb010/selPrdT3
- 요청: SSV body (날짜 + 시간대 파라미터)
- 응답: dsList (ITEM_CD, ITEM_NM, SALE_QTY, RECT_AMT, RATE, MONTH_EVT, ...)

수집 전략:
- 매시 10분에 직전 시간대 수집 (13:10 → 12시 데이터)
- 55분마다 heartbeat로 세션 유지
- 실패 시간대 재시도 큐
- 백필 모드: 과거 6개월 일괄 수집

사용:
    collector = HourlySalesDetailCollector(driver)
    items = collector.collect_hour("2026-03-07", 12)
    # → [{'item_cd': '88...', 'item_nm': '카스...', 'sale_qty': 4, ...}]

    collector.collect_all_hours("2026-03-07")
    # → 0~23시 전체 수집

    collector.backfill(days=180)
    # → 과거 6개월 일괄 수집
"""

import re
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.collectors.direct_api_fetcher import parse_ssv_dataset, ssv_row_to_dict
from src.utils.logger import get_logger

logger = get_logger(__name__)

# SSV 구분자
RS = '\u001e'
US = '\u001f'

# STMB010 팝업 상품구성비 API
DETAIL_ENDPOINT = "/stmb010/selPrdT3"
DETAIL_DATASET_MARKER = "ITEM_CD"

# heartbeat용 가벼운 API (selDay - 기존 시간대별 매출)
HEARTBEAT_ENDPOINT = "/stmb010/selDay"

# XHR 인터셉터 JS (selPrdT3 전용)
INTERCEPTOR_JS = """
if (!window.__detailCaptures) {
    window.__detailCaptures = [];
}
if (!window.__detailInterceptorInstalled) {
    window.__detailInterceptorInstalled = true;
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
            window.__detailCaptures.push({
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

# 시간대 더블클릭 시뮬레이션 JS (STMB010 그리드에서 특정 시간대 클릭)
# 그리드는 div2 내부: wf.div2.form.gdList (0~11시), gdList2 (12~23시)
# 핸들러는 workForm 레벨: wf.gdList_oncelldblclick / gdList2_oncelldblclick
DBLCLICK_HOUR_JS = """
try {
    var app = nexacro.getApplication();
    var frame = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB010_M0;
    var wf = frame.form.div_workForm.form;

    // dsListMain에서 target hour 행 찾기
    var ds = wf.dsListMain;
    if (!ds || ds.getRowCount() === 0) return 'DS_EMPTY';

    var targetRow = -1;
    for (var r = 0; r < ds.getRowCount(); r++) {
        var hms = ds.getColumn(r, 'HMS');
        if (parseInt(hms) === arguments[0]) {
            targetRow = r;
            break;
        }
    }

    if (targetRow < 0) return 'HOUR_NOT_FOUND';

    // rowposition 설정
    ds.set_rowposition(targetRow);

    // 그리드 (div2 내부)
    var hour = arguments[0];
    var grid = hour < 12
        ? wf.div2.form.gdList
        : wf.div2.form.gdList2;
    var handlerName = hour < 12
        ? 'gdList_oncelldblclick'
        : 'gdList2_oncelldblclick';

    var evt = {
        cell: 0,
        col: 0,
        row: targetRow,
        clientx: 100,
        clienty: 100
    };

    // 핸들러 직접 호출 (workForm 레벨)
    if (typeof wf[handlerName] === 'function') {
        wf[handlerName](grid, evt);
        return 'OK';
    }

    // 폴백: 이벤트 직접 발생
    if (grid && grid.oncelldblclick && grid.oncelldblclick.fireEvent) {
        grid.oncelldblclick.fireEvent(grid, evt);
        return 'OK_EVENT';
    }

    return 'NO_HANDLER';
} catch(e) {
    return 'ERROR: ' + e.message;
}
"""

# selPrdT3 Dataset 정의 (출력 컬럼)
PRDT3_DS_DEFINITION = (
    'Dataset:dsList\u001f_RowType_\u001f'
    'ITEM_CD:STRING(256)\u001f'
    'ITEM_NM:STRING(256)\u001f'
    'SALE_QTY:INT(256)\u001f'
    'RECT_AMT:BIGDECIMAL(256)\u001f'
    'RATE:BIGDECIMAL(256)\u001f'
    'MONTH_EVT:STRING(256)\u001f'
    'G_RECT_AMT:BIGDECIMAL(256)\u001f'
    'ORD_ITEM:STRING(256)'
)


class HourlySalesDetailCollector:
    """STMB010 시간대별 매출 상세 Direct API 수집기"""

    def __init__(self, driver: Any = None):
        self.driver = driver
        self._template_selday: Optional[str] = None  # heartbeat용
        self._template_prdt3: Optional[str] = None  # 상품구성비용
        self._last_heartbeat: float = time.time()
        self._failed_queue: List[Tuple[str, int]] = []  # (date, hour) 재시도 큐

    # ── 공개 API ──────────────────────────────────

    def collect_hour(self, target_date: str, hour: int) -> List[Dict]:
        """특정 날짜+시간대의 품목별 상세 수집

        Args:
            target_date: 'YYYYMMDD' 또는 'YYYY-MM-DD'
            hour: 0~23

        Returns:
            [{'item_cd': '88...', 'item_nm': '카스...', 'sale_qty': 4,
              'sale_amt': 12000, 'receipt_amt': 12000, 'rate': 15.38,
              'month_evt': '할인', 'ord_item': '88...'}]
            실패 시 빈 리스트
        """
        date_str = target_date.replace('-', '')
        if len(date_str) != 8 or not (0 <= hour <= 23):
            logger.error(f"[HSD] 잘못된 파라미터: date={target_date}, hour={hour}")
            return []

        try:
            # 세션 유지 체크
            self._check_heartbeat()

            # 1. body 템플릿 확보
            template = self._ensure_template_prdt3()
            if not template:
                logger.warning("[HSD] selPrdT3 템플릿 캡처 실패")
                self._failed_queue.append((date_str, hour))
                return []

            # 2. 날짜 + 시간대 파라미터 치환
            body = self._replace_params(template, date_str, hour)

            # 3. Direct API 호출
            if not hasattr(self, '_logged_template'):
                logger.info(f"[HSD] 템플릿 body (처음 1회): {body[:300]}...")
                self._logged_template = True
            ssv_text = self._call_api(DETAIL_ENDPOINT, body)
            if not ssv_text:
                if not hasattr(self, '_logged_empty_response'):
                    logger.warning(
                        f"[HSD] API 응답 없음 ({date_str} H{hour:02d}), "
                        f"endpoint={DETAIL_ENDPOINT}"
                    )
                    self._logged_empty_response = True
                else:
                    logger.warning(
                        f"[HSD] API 응답 없음 ({date_str} H{hour:02d})"
                    )
                self._failed_queue.append((date_str, hour))
                return []

            # 4. SSV 파싱
            result = self._parse_prdt3_response(ssv_text)
            if result:
                logger.info(
                    f"[HSD] {date_str} H{hour:02d} → {len(result)}개 품목"
                )
            return result, ssv_text

        except Exception as e:
            logger.error(
                f"[HSD] 수집 실패 ({date_str} H{hour:02d}): {e}"
            )
            self._failed_queue.append((date_str, hour))
            return []

    def collect_all_hours(self, target_date: str,
                          hours: Optional[List[int]] = None,
                          delay: float = 0.25) -> Dict[int, List[Dict]]:
        """특정 날짜의 전체/지정 시간대 수집

        Args:
            target_date: 'YYYYMMDD' 또는 'YYYY-MM-DD'
            hours: 수집할 시간대 리스트 (None=0~23 전체)
            delay: API 호출 간 딜레이 (초)

        Returns:
            {0: [...], 1: [...], ..., 23: [...]}
        """
        if hours is None:
            hours = list(range(24))

        results = {}
        success = 0
        fail = 0

        for hour in hours:
            try:
                result = self.collect_hour(target_date, hour)
                if isinstance(result, tuple):
                    items, ssv = result
                    # 유효한 API 응답 (빈 리스트도 성공 — 해당 시간 매출 없음)
                    results[hour] = items
                    success += 1
                else:
                    # API 실패 (collect_hour이 plain [] 반환)
                    fail += 1
            except Exception as e:
                logger.error(f"[HSD] H{hour:02d} 수집 에러: {e}")
                fail += 1

            if delay > 0 and hour < hours[-1]:
                time.sleep(delay)

        date_str = target_date.replace('-', '')
        logger.info(
            f"[HSD] {date_str} 수집 완료: "
            f"성공 {success}/{len(hours)}, 실패 {fail}"
        )
        return results

    def retry_failed(self, max_retries: int = 3,
                     delay: float = 1.0) -> int:
        """실패 큐에 있는 시간대 재시도

        Returns:
            성공 건수
        """
        if not self._failed_queue:
            return 0

        retry_items = list(set(self._failed_queue))
        self._failed_queue.clear()
        recovered = 0

        logger.info(f"[HSD] 재시도 시작: {len(retry_items)}건")

        for date_str, hour in retry_items:
            for attempt in range(max_retries):
                try:
                    result = self.collect_hour(date_str, hour)
                    items = result[0] if isinstance(result, tuple) else result
                    if items:
                        recovered += 1
                        break
                except Exception:
                    pass
                time.sleep(delay)

        logger.info(f"[HSD] 재시도 결과: {recovered}/{len(retry_items)} 복구")
        return recovered

    def backfill(self, days: int = 180, start_date: str = None,
                 end_date: str = None,
                 delay_per_hour: float = 0.3,
                 delay_per_day: float = 2.0,
                 skip_collected: bool = True,
                 collected_checker: Callable = None) -> Dict[str, int]:
        """과거 데이터 일괄 수집 (백필)

        Args:
            days: 수집 기간 (기본 180일 = 6개월)
            start_date: 시작일 (None=오늘-days)
            end_date: 종료일 (None=어제)
            delay_per_hour: 시간대 간 딜레이
            delay_per_day: 날짜 간 딜레이
            skip_collected: 이미 수집된 날짜 건너뛰기
            collected_checker: (date_str) -> List[int] 수집완료 시간대 반환 함수

        Returns:
            {'total_days': N, 'success_days': N, 'total_items': N}
        """
        if end_date:
            end_dt = datetime.strptime(end_date.replace('-', ''), '%Y%m%d')
        else:
            end_dt = datetime.now() - timedelta(days=1)  # 어제까지
        if start_date:
            begin = datetime.strptime(start_date.replace('-', ''), '%Y%m%d')
        else:
            begin = end_dt - timedelta(days=days)

        stats = {'total_days': 0, 'success_days': 0, 'total_items': 0}
        current = begin

        logger.info(
            f"[HSD] 백필 시작: {begin.strftime('%Y-%m-%d')} ~ "
            f"{end_dt.strftime('%Y-%m-%d')} ({days}일)"
        )

        while current <= end_dt:
            date_str = current.strftime('%Y%m%d')
            stats['total_days'] += 1

            # 이미 수집된 시간대 건너뛰기
            hours_to_collect = list(range(24))
            if skip_collected and collected_checker:
                collected = collected_checker(
                    current.strftime('%Y-%m-%d')
                )
                hours_to_collect = [
                    h for h in range(24) if h not in collected
                ]
                if not hours_to_collect:
                    logger.debug(f"[HSD] {date_str} 이미 수집 완료 → 건너뛰기")
                    current += timedelta(days=1)
                    continue

            # 수집
            results = self.collect_all_hours(
                date_str, hours=hours_to_collect, delay=delay_per_hour
            )

            if results:
                stats['success_days'] += 1
                day_items = sum(len(v) for v in results.values())
                stats['total_items'] += day_items

            current += timedelta(days=1)

            if delay_per_day > 0:
                time.sleep(delay_per_day)

            # 10일마다 진행 상황 로그
            if stats['total_days'] % 10 == 0:
                logger.info(
                    f"[HSD] 백필 진행: {stats['total_days']}일 처리, "
                    f"{stats['total_items']}건 수집"
                )

        logger.info(
            f"[HSD] 백필 완료: {stats['success_days']}/{stats['total_days']}일 "
            f"성공, 총 {stats['total_items']}건"
        )
        return stats

    def send_heartbeat(self) -> bool:
        """세션 유지 heartbeat (가벼운 selDay 호출)

        Returns:
            성공 여부
        """
        try:
            template = self._ensure_template_selday()
            if not template:
                return False

            today = datetime.now().strftime('%Y%m%d')
            body = self._replace_date_only(template, today)
            result = self._call_api(HEARTBEAT_ENDPOINT, body)

            if result:
                self._last_heartbeat = time.time()
                logger.debug("[HSD] heartbeat 성공")
                return True
            return False
        except Exception as e:
            logger.warning(f"[HSD] heartbeat 실패: {e}")
            return False

    @property
    def failed_count(self) -> int:
        """실패 큐 크기"""
        return len(self._failed_queue)

    # ── 내부 메서드 ──────────────────────────────

    def _check_heartbeat(self) -> None:
        """55분 경과 시 자동 heartbeat"""
        elapsed = time.time() - self._last_heartbeat
        if elapsed >= 55 * 60:  # 55분
            logger.info(
                f"[HSD] 세션 유지 heartbeat 전송 "
                f"(경과 {elapsed/60:.0f}분)"
            )
            self.send_heartbeat()

    def _ensure_template_prdt3(self) -> Optional[str]:
        """selPrdT3 body 템플릿 확보

        STMB010 화면에서 시간대 더블클릭 → 팝업 → 상품구성비 탭 클릭 시
        발생하는 XHR body를 캡처합니다.
        """
        if self._template_prdt3:
            return self._template_prdt3

        if not self.driver:
            logger.error("[HSD] 드라이버 없음 — 템플릿 캡처 불가")
            return None

        try:
            # 인터셉터 설치
            self.driver.execute_script(INTERCEPTOR_JS)

            # STMB010 화면으로 이동 + dsListMain 데이터 로딩 대기
            self._navigate_to_stmb010()
            data_ready = False
            for _w in range(40):  # 최대 20초 대기
                try:
                    _ok = self.driver.execute_script("""
                        try {
                            var app = nexacro.getApplication();
                            var f = app.mainframe.HFrameSet00.VFrameSet00
                                .FrameSet.STMB010_M0;
                            if (!f || !f.form) return 0;
                            var wf = f.form.div_workForm;
                            if (!wf || !wf.form) return 1;
                            var ds = wf.form.dsListMain;
                            if (!ds || ds.getRowCount() === 0) return 2;
                            return 3;
                        } catch(e) { return -1; }
                    """)
                    if _ok == 3:
                        data_ready = True
                        logger.info("[HSD] STMB010 데이터 로딩 완료")
                        break
                except Exception:
                    pass
                time.sleep(0.5)

            if not data_ready:
                logger.warning("[HSD] STMB010 데이터 로딩 타임아웃, selDay 폴백 시도")
                # selDay 캡처 확인 (페이지 초기 로드 시 발생)
                captures = self.driver.execute_script(
                    "return window.__detailCaptures || []"
                )
                for cap in (captures or []):
                    url = cap.get('url', '')
                    if 'selDay' in url and cap.get('body'):
                        self._template_selday = cap['body']
                        logger.info("[HSD] selDay 템플릿 캡처 (페이지 로드)")
                        break
                return self._ensure_template_prdt3_fallback()

            # 시간대 더블클릭 (00시) → 팝업 열기
            result = self.driver.execute_script(DBLCLICK_HOUR_JS, 0)
            if result not in ('OK', 'OK_EVENT'):
                logger.warning(f"[HSD] 더블클릭 실패: {result}")
                # selDay 캡처 확보
                captures = self.driver.execute_script(
                    "return window.__detailCaptures || []"
                )
                for cap in (captures or []):
                    if 'selDay' in cap.get('url', '') and cap.get('body'):
                        self._template_selday = cap['body']
                        break
                return self._ensure_template_prdt3_fallback()
            # 팝업 열림 대기 — 폴링 (최적화: sleep(3)→폴링 최대 5초)
            popup_open = False
            for _pw in range(25):  # 0.2s × 25 = 5s max
                popup_open = self.driver.execute_script("""
                    try {
                        var app = nexacro.getApplication();
                        var p0 = app.mainframe.HFrameSet00.VFrameSet00
                            .FrameSet.STMB010_M0.STMB010_P0;
                        return p0 && p0.form ? true : false;
                    } catch(e) { return false; }
                """)
                if popup_open:
                    break
                time.sleep(0.2)
            if not popup_open:
                logger.warning("[HSD] 팝업 미열림, 폴백")
                return self._ensure_template_prdt3_fallback()

            # 상품구성비 탭 클릭 (selPrdT3 XHR 발생)
            self.driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    var p0 = app.mainframe.HFrameSet00.VFrameSet00
                        .FrameSet.STMB010_M0.STMB010_P0;
                    if (p0.form.div_tab_btn_03_onclick)
                        p0.form.div_tab_btn_03_onclick();
                } catch(e) {}
            """)
            # XHR 캡처 대기 — 폴링 (최적화: sleep(3)→폴링 최대 5초)
            captures = []
            for _cw in range(25):  # 0.2s × 25 = 5s max
                captures = self.driver.execute_script(
                    "return window.__detailCaptures || []"
                ) or []
                if any('selPrdT3' in (c.get('url', '')) for c in captures):
                    break
                time.sleep(0.2)


            for cap in captures:
                url = cap.get('url', '')
                if 'selPrdT3' in url and cap.get('body'):
                    self._template_prdt3 = cap['body']
                    logger.info(
                        f"[HSD] selPrdT3 템플릿 캡처 성공 "
                        f"(body {len(self._template_prdt3)}자)"
                    )

                    # selDay 템플릿도 함께 캡처 (heartbeat용)
                    for cap2 in captures:
                        if 'selDay' in cap2.get('url', '') and cap2.get('body'):
                            self._template_selday = cap2['body']
                            break

                    # 팝업 닫기
                    self._close_popup()
                    return self._template_prdt3

            logger.warning(
                f"[HSD] selPrdT3 캡처 0건 (전체 {len(captures)}건)"
            )
            return self._ensure_template_prdt3_fallback()

        except Exception as e:
            logger.error(f"[HSD] 템플릿 캡처 에러: {e}")
            return None

    def _ensure_template_prdt3_fallback(self) -> Optional[str]:
        """템플릿 캡처 폴백: selDay body 변환 또는 직접 구성

        selPrdT3 body는 selDay와 세션 변수는 같지만 비즈니스 파라미터가 다름:
        - selDay: strGubun, strSaleGubun, calFromDay, calToDay
        - selPrdT3: strTime, strPosNo, strchkGubun, GV_MENU_ID,
                    GV_CHANNELTYPE, Dataset정의
        """
        # 1. selDay 템플릿 → selPrdT3 변환
        if self._template_selday:
            body = self._build_prdt3_from_selday(self._template_selday)
            if body:
                self._template_prdt3 = body
                logger.info(
                    f"[HSD] selDay 기반 selPrdT3 템플릿 변환 "
                    f"({len(body)}자)"
                )
                return self._template_prdt3

        # 2. 넥사크로 세션변수에서 직접 구성
        if self.driver:
            try:
                built = self.driver.execute_script("""
                    try {
                        var app = nexacro.getApplication();
                        var storeId = app.GV_CHANNELVAL || '';
                        var today = new Date();
                        var y = today.getFullYear();
                        var m = String(today.getMonth()+1).padStart(2,'0');
                        var d = String(today.getDate()).padStart(2,'0');
                        var ymd = y + m + d;

                        var RS = '\\u001e';
                        var US = '\\u001f';

                        // 쿠키에서 SS_ 세션변수 읽기
                        var cookies = {};
                        var cp = document.cookie.split(';');
                        for (var ci = 0; ci < cp.length; ci++) {
                            var p = cp[ci].trim().split('=');
                            if (p.length >= 2) {
                                cookies[p[0].trim()] = p.slice(1).join('=');
                            }
                        }

                        var parts = ['SSV:utf-8', 'GV_USERFLAG=HOME'];

                        // 세션 변수
                        var ssKeys = [
                            'SS_STORE_CD', 'SS_PRE_STORE_CD', 'SS_STORE_NM',
                            'SS_SLC_CD', 'SS_LOC_CD', 'SS_ADM_SECT_CD',
                            'SS_STORE_OWNER_NM', 'SS_STORE_POS_QTY',
                            'SS_STORE_IP', 'SS_SV_EMP_NO', 'SS_SSTORE_ID',
                            'SS_RCV_ID', 'SS_FC_CD', 'SS_USER_GRP_ID',
                            'SS_USER_NO', 'SS_SGGD_CD', 'SS_LOGIN_USER_NO'
                        ];
                        for (var si = 0; si < ssKeys.length; si++) {
                            parts.push(
                                ssKeys[si] + '=' + (cookies[ssKeys[si]] || '')
                            );
                        }

                        // selPrdT3 비즈니스 파라미터
                        parts.push('strYmd=' + ymd);
                        parts.push('strPosNo=');
                        parts.push('strTime=00');
                        parts.push('strchkGubun=0');
                        parts.push('strStoreCd=' + storeId);
                        parts.push('GV_MENU_ID=0001,STMB010_M0');
                        parts.push('GV_USERFLAG=HOME');
                        parts.push('GV_CHANNELTYPE=HOME');

                        // Dataset 정의
                        var ds = [
                            'Dataset:dsList', '_RowType_',
                            'ITEM_CD:STRING(256)', 'ITEM_NM:STRING(256)',
                            'SALE_QTY:INT(256)', 'RECT_AMT:BIGDECIMAL(256)',
                            'RATE:BIGDECIMAL(256)', 'MONTH_EVT:STRING(256)',
                            'G_RECT_AMT:BIGDECIMAL(256)',
                            'ORD_ITEM:STRING(256)'
                        ];
                        parts.push(ds.join(US));

                        return parts.join(RS);
                    } catch(e) { return null; }
                """)
                if built:
                    self._template_prdt3 = built
                    logger.info(
                        f"[HSD] 넥사크로 세션변수 기반 selPrdT3 직접 구성 "
                        f"({len(built)}자)"
                    )
                    return self._template_prdt3
            except Exception as e:
                logger.warning(f"[HSD] 직접 구성 실패: {e}")

        logger.error("[HSD] 폴백 템플릿도 생성 불가")
        return None

    def _build_prdt3_from_selday(self, selday_body: str) -> Optional[str]:
        """selDay body → selPrdT3 body 변환

        세션 변수 유지, selDay 비즈니스 파라미터 → selPrdT3 파라미터 교체
        """
        parts = selday_body.split(RS)

        # 세션 변수 부분만 추출 (str*/cal* 파라미터 제외)
        session_parts = []
        store_cd = ''
        date_str = ''

        for part in parts:
            if part.startswith('strGubun='):
                continue
            if part.startswith('strSaleGubun='):
                continue
            if part.startswith('calFromDay='):
                continue
            if part.startswith('calToDay='):
                continue
            if part.startswith('strPreStoreCd='):
                continue
            if part.startswith('strYmd='):
                date_str = part.split('=', 1)[1]
                continue
            if part.startswith('strStoreCd='):
                store_cd = part.split('=', 1)[1]
                continue
            session_parts.append(part)

        if not store_cd:
            # SS_STORE_CD에서 추출
            for part in parts:
                if part.startswith('SS_STORE_CD='):
                    store_cd = part.split('=', 1)[1]
                    break
        if not date_str:
            date_str = datetime.now().strftime('%Y%m%d')

        # selPrdT3 비즈니스 파라미터 추가
        session_parts.extend([
            f'strYmd={date_str}',
            'strPosNo=',
            'strTime=00',
            'strchkGubun=0',
            f'strStoreCd={store_cd}',
            'GV_MENU_ID=0001,STMB010_M0',
            'GV_USERFLAG=HOME',
            'GV_CHANNELTYPE=HOME',
            PRDT3_DS_DEFINITION,
        ])

        return RS.join(session_parts)

    def _ensure_template_selday(self) -> Optional[str]:
        """selDay body 템플릿 확보 (heartbeat용)"""
        if self._template_selday:
            return self._template_selday

        # 기존 HourlySalesCollector에서 캡처된 것 재활용
        if not self.driver:
            return None

        try:
            self.driver.execute_script(INTERCEPTOR_JS)
            self._navigate_to_stmb010()
            time.sleep(3)

            captures = self.driver.execute_script(
                "return window.__detailCaptures || []"
            )
            for cap in captures:
                if 'selDay' in cap.get('url', '') and cap.get('body'):
                    self._template_selday = cap['body']
                    return self._template_selday
        except Exception as e:
            logger.error(f"[HSD] selDay 템플릿 캡처 실패: {e}")

        return None

    def _navigate_to_stmb010(self) -> None:
        """STMB010 화면으로 이동 (DOM MouseEvent 시뮬레이션)"""
        # 1단계: 매출분석 메뉴 클릭
        menu_js = """
        function clickById(id) {
            var el = document.getElementById(id);
            if (!el || el.offsetParent === null) return false;
            el.scrollIntoView({block: 'center', inline: 'center'});
            var r = el.getBoundingClientRect();
            var o = {
                bubbles: true, cancelable: true, view: window,
                clientX: r.left + r.width / 2,
                clientY: r.top + r.height / 2
            };
            el.dispatchEvent(new MouseEvent('mousedown', o));
            el.dispatchEvent(new MouseEvent('mouseup', o));
            el.dispatchEvent(new MouseEvent('click', o));
            return true;
        }
        return clickById(
            'mainframe.HFrameSet00.VFrameSet00.TopFrame.form'
            + '.div_topMenu.form.STMB000_M0:icontext'
        );
        """
        result = self.driver.execute_script(menu_js)
        logger.debug(f"[HSD] 매출분석 메뉴 클릭: {result}")
        time.sleep(1)

        # 2단계: 시간대별 매출 서브메뉴 클릭
        sub_js = """
        function clickById(id) {
            var el = document.getElementById(id);
            if (!el || el.offsetParent === null) return false;
            el.scrollIntoView({block: 'center', inline: 'center'});
            var r = el.getBoundingClientRect();
            var o = {
                bubbles: true, cancelable: true, view: window,
                clientX: r.left + r.width / 2,
                clientY: r.top + r.height / 2
            };
            el.dispatchEvent(new MouseEvent('mousedown', o));
            el.dispatchEvent(new MouseEvent('mouseup', o));
            el.dispatchEvent(new MouseEvent('click', o));
            return true;
        }
        return clickById(
            'mainframe.HFrameSet00.VFrameSet00.TopFrame.form'
            + '.pdiv_topMenu_STMB000_M0.form.STMB010_M0:text'
        );
        """
        result2 = self.driver.execute_script(sub_js)
        logger.debug(f"[HSD] 시간대별 매출 서브메뉴 클릭: {result2}")

    def _close_popup(self) -> None:
        """STMB010_P0 팝업 닫기"""
        try:
            self.driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    var p0 = app.mainframe.HFrameSet00.VFrameSet00
                        .FrameSet.STMB010_M0.STMB010_P0;
                    if (p0 && p0.close) p0.close();
                } catch(e) {}
            """)
        except Exception:
            pass

    def _replace_params(self, template: str, date_str: str,
                        hour: int) -> str:
        """body 템플릿에서 날짜 + 시간대 파라미터 치환"""
        body = template
        # 날짜 치환
        body = re.sub(r'(strYmd=)\d{8}', rf'\g<1>{date_str}', body)
        body = re.sub(r'(calFromDay=)\d{8}', rf'\g<1>{date_str}', body)
        body = re.sub(r'(calToDay=)\d{8}', rf'\g<1>{date_str}', body)

        # 시간대 치환 — selPrdT3는 strTime 사용 (NOT strHms)
        hour_str = f'{hour:02d}'
        body = re.sub(r'(strTime=)\d{2}', rf'\g<1>{hour_str}', body)

        return body

    def _replace_date_only(self, template: str, date_str: str) -> str:
        """날짜 파라미터만 치환 (heartbeat용)"""
        body = template
        body = re.sub(r'(calFromDay=)\d{8}', rf'\g<1>{date_str}', body)
        body = re.sub(r'(calToDay=)\d{8}', rf'\g<1>{date_str}', body)
        body = re.sub(r'(strYmd=)\d{8}', rf'\g<1>{date_str}', body)
        return body

    def _call_api(self, endpoint: str, body: str) -> Optional[str]:
        """fetch()로 Direct API 호출"""
        if not self.driver:
            return None

        url = f"https://store.bgfretail.com{endpoint}"

        try:
            result = self.driver.execute_async_script(
                """
                var callback = arguments[arguments.length - 1];
                fetch(arguments[0], {
                    method: 'POST',
                    headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                    body: arguments[1]
                })
                .then(function(r) {
                    return r.text().then(function(t) {
                        return {status: r.status, text: t};
                    });
                })
                .then(function(d) {
                    if (d.status === 200) callback(d.text);
                    else callback('__ERR_STATUS_' + d.status);
                })
                .catch(function(e) { callback('__ERR_' + e.message); });
                """,
                url,
                body
            )
            if result is None:
                return None
            if isinstance(result, str) and result.startswith('__ERR_'):
                if not hasattr(self, '_logged_api_error'):
                    logger.warning(f"[HSD] API 에러: {result}")
                    self._logged_api_error = True
                return None
            return result if result else None
        except Exception as e:
            logger.error(f"[HSD] API 호출 실패 ({endpoint}): {e}")
            return None

    def _parse_prdt3_response(self, ssv_text: str) -> List[Dict]:
        """selPrdT3 SSV 응답 파싱 → 품목별 리스트

        Returns:
            [{'item_cd': '88...', 'item_nm': '카스...', 'sale_qty': 4,
              'sale_amt': 12000, 'receipt_amt': 12000, 'rate': 15.38,
              'month_evt': '할인', 'ord_item': '88...'}]
        """
        parsed = parse_ssv_dataset(ssv_text, DETAIL_DATASET_MARKER)
        if not parsed:
            return []

        columns = parsed['columns']
        result = []

        for row_vals in parsed['rows']:
            d = ssv_row_to_dict(columns, row_vals)

            item_cd = d.get('ITEM_CD', '').strip()
            if not item_cd:
                continue

            entry = {
                'item_cd': item_cd,
                'item_nm': d.get('ITEM_NM', '').strip(),
                'sale_qty': self._safe_int(d.get('SALE_QTY', '0')),
                'sale_amt': self._safe_float(d.get('RECT_AMT', '0')),
                'receipt_amt': self._safe_float(d.get('G_RECT_AMT', '0')),
                'rate': self._safe_float(d.get('RATE', '0')),
                'month_evt': d.get('MONTH_EVT', '').strip(),
                'ord_item': d.get('ORD_ITEM', '').strip(),
            }

            # 빈 상품(수량 0, 금액 0) 건너뛰기
            if entry['sale_qty'] == 0 and entry['sale_amt'] == 0:
                continue

            result.append(entry)

        return result

    @staticmethod
    def _safe_float(val: str) -> float:
        try:
            return float(val) if val else 0.0
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _safe_int(val: str) -> int:
        try:
            return int(float(val)) if val else 0
        except (ValueError, TypeError):
            return 0
