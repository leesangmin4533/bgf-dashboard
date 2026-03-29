"""
발주 현황 수집기
- BGF 리테일 사이트에서 발주 현황 조회 데이터 수집
- 모든 발주 이력 수집 (수동 발주 포함)
- order_tracking 테이블 업데이트
"""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.settings.ui_config import FRAME_IDS, DS_PATHS, FAIL_REASON_UI
from src.infrastructure.database.repos import (
    ReceivingRepository,
    OrderTrackingRepository,
)
from src.utils.nexacro_helpers import (
    click_menu_by_text, click_submenu_by_text, wait_for_frame,
    close_tab_by_frame_id, JS_CLICK_HELPER,
)
from src.settings.timing import (
    OS_RADIO_CLICK_WAIT, OS_MENU_CLOSE_WAIT,
    FR_BARCODE_INPUT_WAIT, FR_POPUP_MAX_CHECKS,
    FR_POPUP_CHECK_INTERVAL, FR_POPUP_CLOSE_WAIT, FR_BETWEEN_ITEMS,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


class OrderStatusCollector:
    """발주 현황 수집기"""

    # 프레임 ID
    FRAME_ID = FRAME_IDS["ORDER_STATUS"]

    # 데이터셋 경로
    DS_PATH = DS_PATHS["ORDER_STATUS"]

    def __init__(self, driver: Optional[Any] = None, store_id: Optional[str] = None) -> None:
        """
        Args:
            driver: Selenium WebDriver (로그인된 상태)
            store_id: 매장 코드
        """
        self.driver = driver
        self.store_id = store_id
        self.repo = ReceivingRepository(store_id=self.store_id)
        self.tracking_repo = OrderTrackingRepository(store_id=self.store_id)

    def set_driver(self, driver: Any) -> None:
        """드라이버 설정

        Args:
            driver: Selenium WebDriver 인스턴스
        """
        self.driver = driver

    def navigate_to_order_status_menu(self) -> bool:
        """
        발주 > 발주 현황 조회 메뉴로 이동
        공통 넥사크로 헬퍼(nexacro_helpers) 사용

        Returns:
            성공 여부
        """
        if not self.driver:
            logger.error("드라이버가 설정되지 않음")
            return False

        logger.info("발주 현황 조회 메뉴 이동...")

        try:
            # 1. 발주 메뉴 클릭
            if not click_menu_by_text(self.driver, '발주'):
                logger.error("발주 메뉴 클릭 실패")
                return False

            time.sleep(1)

            # 2. 발주 현황 조회 서브메뉴 클릭 (1회 재시도)
            if not click_submenu_by_text(self.driver, '발주 현황 조회'):
                logger.warning("발주 현황 조회 서브메뉴 클릭 실패 - 2초 후 재시도")
                time.sleep(2)
                if not click_submenu_by_text(self.driver, '발주 현황 조회'):
                    logger.error("발주 현황 조회 서브메뉴 클릭 재시도 실패")
                    return False

            time.sleep(2)

            # 3. 화면 로딩 확인
            if wait_for_frame(self.driver, self.FRAME_ID):
                logger.info("발주 현황 조회 화면 로딩 완료")
                # 인터셉터 설치 (이후 라디오 클릭의 XHR 캡처용)
                self._install_interceptor()
                return True

            logger.error("화면 로딩 타임아웃")
            return False

        except Exception as e:
            logger.error(f"메뉴 이동 실패: {e}")
            return False

    def _install_interceptor(self) -> None:
        """XHR 인터셉터 설치 (Direct API 캡처용)"""
        try:
            from src.collectors.direct_frame_fetcher import install_interceptor
            install_interceptor(self.driver)
        except Exception as e:
            logger.debug(f"인터셉터 설치 실패 (무시): {e}")

    @staticmethod
    def _convert_api_dsresult(rows: List[Dict[str, str]]) -> List[Dict]:
        """Direct API dsResult SSV 딕셔너리 → Selenium 호환 형식 변환"""
        result = []
        for row in rows:
            result.append({
                'ORD_YMD': row.get('ORD_YMD', ''),
                'ITEM_CD': row.get('ITEM_CD', ''),
                'ITEM_NM': row.get('ITEM_NM', ''),
                'MID_CD': row.get('MID_CD', ''),
                'MID_NM': row.get('MID_NM', ''),
                'ORD_CNT': row.get('ORD_CNT', ''),
                'ORD_UNIT_QTY': row.get('ORD_UNIT_QTY', ''),
                'ITEM_WONGA': row.get('ITEM_WONGA', ''),
                'NOW_QTY': row.get('NOW_QTY', ''),
                'NAP_NEXTORD': row.get('NAP_NEXTORD', ''),
                'ORD_INPUT_ID': row.get('ORD_INPUT_ID', ''),
                'ORD_PSS_ID': row.get('ORD_PSS_ID', ''),
            })
        return result

    @staticmethod
    def _convert_api_dsordersale(rows: List[Dict[str, str]]) -> List[Dict]:
        """Direct API dsOrderSale SSV 딕셔너리 → Selenium 호환 형식 변환"""
        result = []
        for row in rows:
            result.append({
                'ORD_YMD': row.get('ORD_YMD', ''),
                'ITEM_CD': row.get('ITEM_CD', ''),
                'JIP_ITEM_CD': row.get('JIP_ITEM_CD', ''),
                'ORD_QTY': row.get('ORD_QTY', ''),
                'BUY_QTY': row.get('BUY_QTY', ''),
                'SALE_QTY': row.get('SALE_QTY', ''),
                'DISUSE_QTY': row.get('DISUSE_QTY', ''),
                'SUM_UNIT_ID': row.get('SUM_UNIT_ID', ''),
            })
        return result

    def get_available_dates(self) -> List[str]:
        """
        조회 가능한 발주일 목록 조회 (dsWeek)

        Returns:
            발주일 목록 [YYYYMMDD, ...]
        """
        if not self.driver:
            return []

        result = self.driver.execute_script(f"""
            try {{
                const app = nexacro.getApplication();
                const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{self.FRAME_ID}.form;
                const wf = form.{self.DS_PATH};

                if (!wf?.dsWeek) return {{error: 'dsWeek not found'}};

                const ds = wf.dsWeek;
                const dates = [];

                for (let i = 0; i < ds.getRowCount(); i++) {{
                    dates.push(ds.getColumn(i, 'ORD_YMD'));
                }}

                return {{dates: dates}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        if result and result.get('dates'):
            return result['dates']

        return []

    def collect_order_status(self) -> List[Dict[str, Any]]:
        """
        발주 현황 데이터 수집 (dsResult)

        Returns:
            발주 데이터 리스트
        """
        if not self.driver:
            return []

        result = self.driver.execute_script(f"""
            try {{
                const app = nexacro.getApplication();
                const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{self.FRAME_ID}.form;
                const wf = form.{self.DS_PATH};

                const ds = wf?.dsResult;
                if (!ds) return {{error: 'dsResult not found'}};

                const rows = [];
                for (let i = 0; i < ds.getRowCount(); i++) {{
                    // Decimal 타입 처리 함수
                    function getVal(col) {{
                        let val = ds.getColumn(i, col);
                        if (val && typeof val === 'object' && val.hi !== undefined) {{
                            val = val.hi;
                        }}
                        return val;
                    }}

                    rows.push({{
                        ORD_YMD: getVal('ORD_YMD'),
                        ITEM_CD: getVal('ITEM_CD'),
                        ITEM_NM: getVal('ITEM_NM'),
                        MID_CD: getVal('MID_CD'),
                        MID_NM: getVal('MID_NM'),
                        PYUN_QTY: getVal('PYUN_QTY'),
                        ORD_CNT: getVal('ORD_CNT'),
                        ORD_UNIT_QTY: getVal('ORD_UNIT_QTY'),
                        ITEM_WONGA: getVal('ITEM_WONGA'),
                        NOW_QTY: getVal('NOW_QTY'),
                        NAP_NEXTORD: getVal('NAP_NEXTORD'),
                        ORD_INPUT_ID: getVal('ORD_INPUT_ID'),
                        ORD_PSS_ID: getVal('ORD_PSS_ID'),
                        STOP_PLAN_YMD: getVal('STOP_PLAN_YMD'),
                        CUT_ITEM_YN: getVal('CUT_ITEM_YN')
                    }});
                }}

                return {{list: rows, total: ds.getRowCount()}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        if not result or result.get('error'):
            logger.warning(f"발주 현황 조회 실패: {result}")
            return []

        data = result.get('list', [])
        logger.info(f"발주 현황: {len(data)}건")

        return data

    def collect_yesterday_orders(self, yesterday: str) -> List[Dict[str, Any]]:
        """BGF 발주현황에서 어제 발주 목록 수집 (order_tracking 정합성 검증용)

        기존 collect_order_status()의 dsResult에서 어제 날짜 발주만 필터링.
        BGF에 실제 접수된 발주만 포함되므로, order_tracking과 대조하여
        false positive (시스템 성공 기록 but BGF 미접수)를 탐지한다.

        Args:
            yesterday: 'YYYY-MM-DD' (대조 대상 날짜)

        Returns:
            [{item_cd, item_nm, order_qty, order_date, ord_input_id}, ...]
        """
        all_orders = self.collect_order_status()
        if not all_orders:
            logger.warning("[발주정합성] BGF 발주현황 조회 실패 또는 데이터 없음")
            return []

        # 날짜 형식 변환: YYYY-MM-DD → YYYYMMDD (BGF ORD_YMD 형식)
        yesterday_ymd = yesterday.replace('-', '')

        filtered = []
        for row in all_orders:
            ord_ymd = str(row.get('ORD_YMD', '')).strip()
            if ord_ymd == yesterday_ymd:
                item_cd = str(row.get('ITEM_CD', '')).strip()
                pyun_qty = int(row.get('PYUN_QTY', 0) or 0)
                ord_unit_qty = int(row.get('ORD_UNIT_QTY', 1) or 1)
                order_qty = pyun_qty * ord_unit_qty
                if item_cd and order_qty > 0:
                    filtered.append({
                        'item_cd': item_cd,
                        'item_nm': str(row.get('ITEM_NM', '')).strip(),
                        'order_qty': order_qty,
                        'order_date': yesterday,
                        'ord_input_id': str(row.get('ORD_INPUT_ID', '')).strip(),
                    })

        logger.info(
            f"[발주정합성] BGF 어제({yesterday}) 발주: {len(filtered)}건 "
            f"(전체 {len(all_orders)}건 중)"
        )
        return filtered

    def collect_order_sale_history(self) -> List[Dict[str, Any]]:
        """
        발주/판매 이력 수집 (dsOrderSale)

        Returns:
            발주/판매 이력 리스트
        """
        if not self.driver:
            return []

        result = self.driver.execute_script(f"""
            try {{
                const app = nexacro.getApplication();
                const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{self.FRAME_ID}.form;
                const wf = form.{self.DS_PATH};

                const ds = wf?.dsOrderSale;
                if (!ds) return {{error: 'dsOrderSale not found'}};

                const rows = [];
                for (let i = 0; i < ds.getRowCount(); i++) {{
                    function getVal(col) {{
                        let val = ds.getColumn(i, col);
                        if (val && typeof val === 'object' && val.hi !== undefined) {{
                            val = val.hi;
                        }}
                        return val;
                    }}

                    rows.push({{
                        ORD_YMD: getVal('ORD_YMD'),
                        ITEM_CD: getVal('ITEM_CD'),
                        JIP_ITEM_CD: getVal('JIP_ITEM_CD'),
                        ORD_QTY: getVal('ORD_QTY'),
                        BUY_QTY: getVal('BUY_QTY'),
                        SALE_QTY: getVal('SALE_QTY'),
                        DISUSE_QTY: getVal('DISUSE_QTY'),
                        SUM_UNIT_ID: getVal('SUM_UNIT_ID')
                    }});
                }}

                return {{list: rows, total: ds.getRowCount()}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        if not result or result.get('error'):
            logger.warning(f"발주/판매 이력 조회 실패: {result}")
            return []

        data = result.get('list', [])
        logger.info(f"발주/판매 이력: {len(data)}건")

        return data

    def _format_order_date(self, ord_ymd: str) -> str:
        """
        발주일 포맷 변환

        Args:
            ord_ymd: 발주일 (YYYYMMDD 형식)

        Returns:
            발주일 (YYYY-MM-DD 형식)
        """
        if not ord_ymd or len(ord_ymd) != 8:
            return ""

        return f"{ord_ymd[:4]}-{ord_ymd[4:6]}-{ord_ymd[6:8]}"

    def _try_direct_api_order_data(self, gubun: str = '0') -> Optional[Dict]:
        """Direct API로 발주 현황 일괄 조회

        Returns:
            성공 시 {'dsResult': [...], 'dsOrderSale': [...]}, 실패 시 None
        """
        try:
            from src.collectors.direct_frame_fetcher import DirectOrderStatusFetcher

            fetcher = DirectOrderStatusFetcher(self.driver)
            if not fetcher.capture_template():
                return None

            data = fetcher.fetch_order_status(gubun)
            if data and (data.get('dsResult') or data.get('dsOrderSale')):
                total = len(data.get('dsResult', [])) + len(data.get('dsOrderSale', []))
                logger.info(
                    f"[OrderStatusAPI] Direct API 성공: "
                    f"dsResult={len(data.get('dsResult', []))}건, "
                    f"dsOrderSale={len(data.get('dsOrderSale', []))}건"
                )
                return data
            return None

        except Exception as e:
            logger.warning(f"[OrderStatusAPI] Direct API 실패: {e}")
            return None

    def update_order_tracking_from_status(self) -> int:
        """
        발주 현황 데이터로 order_tracking 업데이트
        - 수동 발주도 포함하여 모든 발주 추적
        - 발주 현황(dsResult)에서 상품명/중분류 정보 획득
        - 발주/판매 이력(dsOrderSale)에서 수량 정보 획득

        Direct API 우선 시도 → 실패 시 Selenium 폴백.

        Returns:
            업데이트된 건수
        """
        # ★ Direct API 시도 (dsResult + dsOrderSale 일괄 조회)
        api_data = self._try_direct_api_order_data('0')

        if api_data:
            order_status = self._convert_api_dsresult(api_data.get('dsResult', []))
            order_sale_data = self._convert_api_dsordersale(api_data.get('dsOrderSale', []))
        else:
            # Selenium 폴백
            order_status = self.collect_order_status()
            order_sale_data = self.collect_order_sale_history()

        item_info_map = {}
        for status_item in order_status:
            cd = status_item.get('ITEM_CD')
            if cd:
                item_info_map[str(cd)] = {
                    'item_nm': status_item.get('ITEM_NM', ''),
                    'mid_cd': status_item.get('MID_CD', ''),
                }

        if not order_sale_data:
            logger.warning("발주/판매 이력 없음")
            return 0

        updated_count = 0

        for item in order_sale_data:
            ord_ymd = item.get('ORD_YMD')
            item_cd = item.get('ITEM_CD')
            ord_qty = item.get('ORD_QTY')

            # 빈 값 스킵
            if not ord_ymd or not item_cd or not ord_qty:
                continue

            # 발주일 포맷 변환
            order_date = self._format_order_date(ord_ymd)
            if not order_date:
                continue

            try:
                ord_qty_int = int(ord_qty)
                buy_qty_int = int(item.get('BUY_QTY') or 0)
            except (ValueError, TypeError):
                continue

            # 상품 정보 조회
            info = item_info_map.get(str(item_cd), {})
            item_nm = info.get('item_nm', '')
            mid_cd = info.get('mid_cd', '')

            # order_tracking에 저장 (중복 방지: 동일 발주일+상품코드 존재 시 스킵)
            try:
                self.tracking_repo.save_order(
                    order_date=order_date,
                    item_cd=str(item_cd),
                    item_nm=item_nm,
                    mid_cd=mid_cd,
                    delivery_type='',
                    order_qty=ord_qty_int,
                    arrival_time='',
                    expiry_time='',
                    order_source='site'
                )
                updated_count += 1
            except Exception as e:
                # 중복 키 등 DB 에러는 경고만 로깅
                logger.warning(f"order_tracking 저장 실패 ({item_cd}, {order_date}): {e}")

            # 입고 정보가 있으면 업데이트
            if buy_qty_int > 0:
                try:
                    self.tracking_repo.update_order_tracking_receiving(
                        item_cd=str(item_cd),
                        order_date=order_date,
                        receiving_qty=buy_qty_int
                    )
                except Exception as e:
                    logger.warning(f"order_tracking 입고 업데이트 실패 ({item_cd}): {e}")

        logger.info(f"order_tracking 업데이트 완료: {updated_count}건")
        return updated_count

    def get_pending_orders(self) -> List[Dict[str, Any]]:
        """
        미입고 발주 목록 조회
        - 발주했으나 아직 입고되지 않은 상품

        Returns:
            미입고 발주 리스트
        """
        # 발주/판매 이력 수집
        order_sale_data = self.collect_order_sale_history()

        if not order_sale_data:
            return []

        pending_orders = []

        for item in order_sale_data:
            ord_ymd = item.get('ORD_YMD')
            item_cd = item.get('ITEM_CD')
            ord_qty = item.get('ORD_QTY')
            buy_qty = item.get('BUY_QTY')  # 입고수량

            # 빈 값 스킵
            if not ord_ymd or not item_cd:
                continue

            # 발주량이 있고 입고량이 없거나 적은 경우
            try:
                ord_qty_int = int(ord_qty) if ord_qty else 0
                buy_qty_int = int(buy_qty) if buy_qty else 0

                if ord_qty_int > buy_qty_int:
                    pending_orders.append({
                        'order_date': self._format_order_date(ord_ymd),
                        'item_cd': item_cd,
                        'order_qty': ord_qty_int,
                        'received_qty': buy_qty_int,
                        'pending_qty': ord_qty_int - buy_qty_int
                    })
            except (ValueError, TypeError):
                continue

        logger.info(f"미입고 발주: {len(pending_orders)}건")
        return pending_orders

    # === 일반(수동) 발주 상품 수집 ===

    def click_normal_radio(self) -> bool:
        """'일반' 라디오 버튼 클릭

        라디오: rdGubun (div_work.form.Div21.form)
        항목: 전체(0), 일반(1), 자동(2), 스마트(3)

        3단계 폴백 전략:
        1. 넥사크로 Radio API - rdGubun.set_value('1')
        2. "일반" 텍스트 부모 요소 클릭
        3. rdGubun 영역 내 텍스트 검색

        Returns:
            클릭 성공 여부
        """
        if not self.driver:
            return False

        logger.info("일반 라디오 버튼 클릭 시도...")

        result = self.driver.execute_script(JS_CLICK_HELPER + f"""
            try {{
                const app = nexacro.getApplication();
                const frame = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{self.FRAME_ID};
                const wf = frame.form.{self.DS_PATH};

                // Strategy A: 넥사크로 Radio API - rdGubun (Div21 내부)
                var radio = wf.Div21 && wf.Div21.form && wf.Div21.form.rdGubun;
                if (radio && radio.set_value) {{
                    radio.set_value('1');
                    if (radio.on_fire_onitemchanged) {{
                        radio.on_fire_onitemchanged(radio, {{}});
                    }}
                    return {{success: true, method: 'api', component: 'rdGubun', value: '1'}};
                }}

                // Strategy B: "일반" 텍스트 부모 클릭
                const allSpans = document.querySelectorAll(
                    '[id*="{self.FRAME_ID}"] span, [id*="{self.FRAME_ID}"] div'
                );
                for (const el of allSpans) {{
                    if (el.textContent.trim() === '일반' && el.offsetParent !== null) {{
                        clickElement(el.parentElement || el);
                        return {{success: true, method: 'text_parent'}};
                    }}
                }}

                // Strategy C: rdGubun 영역 내 텍스트 검색
                const rdSpans = document.querySelectorAll(
                    'div[id*="rdGubun"] span, div[id*="rdGubun"] div'
                );
                for (const el of rdSpans) {{
                    if (el.textContent.trim() === '일반' && el.offsetParent !== null) {{
                        clickElement(el.parentElement || el);
                        return {{success: true, method: 'text_rdGubun'}};
                    }}
                }}

                return {{success: false, error: 'rdGubun normal radio not found'}};
            }} catch(e) {{
                return {{success: false, error: e.message}};
            }}
        """)

        if result and result.get('success'):
            logger.info(f"일반 라디오 클릭 성공 (method: {result.get('method')})")
            return True

        error = result.get('error', 'unknown') if result else 'no result'
        logger.warning(f"일반 라디오 클릭 실패: {error}")
        return False

    def collect_normal_order_items(self) -> Optional[List[Dict[str, Any]]]:
        """일반(수동) 발주 상품 목록 수집

        일반 탭(rdGubun='1')의 dsResult에서 실제 발주된 건(ORD_CNT > 0)만 수집.
        order_qty = ORD_CNT * ORD_UNIT_QTY.

        Returns:
            [{"item_cd", "item_nm", "mid_cd", "mid_nm", "ord_ymd",
              "ord_cnt", "ord_unit_qty", "order_qty", "ord_input_id", "ord_amt"}, ...]
            실패 시 None, 발주 0건 시 빈 리스트
        """
        if not self.driver:
            return None

        if not self.click_normal_radio():
            logger.warning("일반 라디오 클릭 실패 - None 반환")
            return None

        time.sleep(OS_RADIO_CLICK_WAIT)

        result = self.driver.execute_script(f"""
            try {{
                const app = nexacro.getApplication();
                const wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet
                    .{self.FRAME_ID}.form.{self.DS_PATH};
                const ds = wf?.dsResult;
                if (!ds) return {{error: 'dsResult not found'}};

                function getVal(row, col) {{
                    let val = ds.getColumn(row, col);
                    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                    return val !== null && val !== undefined ? String(val) : '';
                }}

                const items = [];
                const total = ds.getRowCount();
                for (let i = 0; i < total; i++) {{
                    const ordCnt = parseInt(getVal(i, 'ORD_CNT')) || 0;
                    if (ordCnt <= 0) continue;

                    const cd = getVal(i, 'ITEM_CD');
                    if (!cd) continue;

                    const unitQty = parseInt(getVal(i, 'ORD_UNIT_QTY')) || 1;
                    items.push({{
                        item_cd: cd,
                        item_nm: getVal(i, 'ITEM_NM'),
                        mid_cd: getVal(i, 'MID_CD'),
                        mid_nm: getVal(i, 'MID_NM'),
                        ord_ymd: getVal(i, 'ORD_YMD'),
                        ord_cnt: ordCnt,
                        ord_unit_qty: unitQty,
                        order_qty: ordCnt * unitQty,
                        ord_input_id: getVal(i, 'ORD_INPUT_ID'),
                        ord_amt: parseInt(getVal(i, 'ORD_AMT')) || 0
                    }});
                }}
                return {{items: items, total: total, ordered: items.length}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        if not result or result.get('error'):
            logger.warning(f"일반(수동) 발주 상품 조회 실패: {result}")
            return None

        items = result.get('items', [])
        total = result.get('total', 0)
        logger.info(
            f"일반(수동) 발주: {len(items)}개 발주됨 "
            f"(dsResult 총 {total}행, 미발주 {total - len(items)}행 제외)"
        )
        return items

    # === 자동 발주 상품 수집 ===

    def click_auto_radio(self) -> bool:
        """
        "자동" 라디오 버튼 클릭

        라디오: rdGubun (div_work.form.Div21.form)
        항목: 전체(0), 일반(1), 자동(2), 스마트(3)

        3단계 폴백 전략:
        1. 넥사크로 Radio API - rdGubun.set_value('2')
        2. "자동" 텍스트 부모 요소 클릭
        3. rdGubun 영역 내 텍스트 검색

        Returns:
            클릭 성공 여부
        """
        if not self.driver:
            return False

        logger.info("자동 라디오 버튼 클릭 시도...")

        result = self.driver.execute_script(JS_CLICK_HELPER + f"""
            try {{
                const app = nexacro.getApplication();
                const frame = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{self.FRAME_ID};
                const wf = frame.form.{self.DS_PATH};

                // Strategy A: 넥사크로 Radio API - rdGubun (Div21 내부)
                // value 매핑: 0=전체, 1=일반, 2=자동, 3=스마트
                var radio = wf.Div21 && wf.Div21.form && wf.Div21.form.rdGubun;
                if (radio && radio.set_value) {{
                    radio.set_value('2');
                    if (radio.on_fire_onitemchanged) {{
                        radio.on_fire_onitemchanged(radio, {{}});
                    }}
                    return {{success: true, method: 'api', component: 'rdGubun', value: '2'}};
                }}

                // Strategy B: "자동" 텍스트 부모 클릭
                const allSpans = document.querySelectorAll(
                    '[id*="{self.FRAME_ID}"] span, [id*="{self.FRAME_ID}"] div'
                );
                for (const el of allSpans) {{
                    if (el.textContent.trim() === '자동' && el.offsetParent !== null) {{
                        clickElement(el.parentElement || el);
                        return {{success: true, method: 'text_parent'}};
                    }}
                }}

                // Strategy C: rdGubun 영역 내 텍스트 검색
                const rdSpans = document.querySelectorAll(
                    'div[id*="rdGubun"] span, div[id*="rdGubun"] div'
                );
                for (const el of rdSpans) {{
                    if (el.textContent.trim() === '자동' && el.offsetParent !== null) {{
                        clickElement(el.parentElement || el);
                        return {{success: true, method: 'text_rdGubun'}};
                    }}
                }}

                return {{success: false, error: 'rdGubun radio not found'}};
            }} catch(e) {{
                return {{success: false, error: e.message}};
            }}
        """)

        if result and result.get('success'):
            logger.info(f"자동 라디오 클릭 성공 (method: {result.get('method')})")
            return True

        error = result.get('error', 'unknown') if result else 'no result'
        logger.warning(f"자동 라디오 클릭 실패: {error}")
        return False

    def collect_auto_order_items(self) -> Set[str]:
        """
        자동발주 상품코드 목록 수집

        Flow:
        1. "자동" 라디오 클릭
        2. 데이터 갱신 대기
        3. dsResult에서 ITEM_CD 추출

        Returns:
            자동발주 상품코드 set (실패 시 빈 set)
        """
        if not self.driver:
            return set()

        # 자동 라디오 클릭
        if not self.click_auto_radio():
            logger.warning("자동 라디오 클릭 실패 - 빈 목록 반환")
            return set()

        # 데이터 갱신 대기
        time.sleep(OS_RADIO_CLICK_WAIT)

        # dsResult에서 ITEM_CD 추출
        result = self.driver.execute_script(f"""
            try {{
                const app = nexacro.getApplication();
                const wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet
                    .{self.FRAME_ID}.form.{self.DS_PATH};
                const ds = wf?.dsResult;
                if (!ds) return {{error: 'dsResult not found'}};

                const items = [];
                for (let i = 0; i < ds.getRowCount(); i++) {{
                    let cd = ds.getColumn(i, 'ITEM_CD');
                    if (cd && typeof cd === 'object' && cd.hi !== undefined) cd = cd.hi;
                    if (cd) items.push(String(cd));
                }}
                return {{items: items, total: ds.getRowCount()}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        if not result or result.get('error'):
            logger.warning(f"자동발주 상품 조회 실패: {result}")
            return set()

        items = set(result.get('items', []))
        logger.info(f"자동발주 상품: {len(items)}개 (dsResult 총 {result.get('total', 0)}행)")
        return items

    def collect_auto_order_items_detail(self) -> Optional[List[Dict[str, str]]]:
        """자동발주 상품 상세 목록 수집 (DB 캐시용)

        collect_auto_order_items()와 동일한 플로우이나,
        ITEM_CD 외에 ITEM_NM, MID_CD도 함께 반환.

        Returns:
            [{"item_cd": "...", "item_nm": "...", "mid_cd": "..."}, ...]
            실패 시 None (사이트 조회 불가), 정상 0건 시 빈 리스트
        """
        if not self.driver:
            return None

        # 자동 라디오 클릭
        if not self.click_auto_radio():
            logger.warning("자동 라디오 클릭 실패 - None 반환 (detail)")
            return None

        # 데이터 갱신 대기
        time.sleep(OS_RADIO_CLICK_WAIT)

        # dsResult에서 ITEM_CD, ITEM_NM, MID_CD 추출
        result = self.driver.execute_script(f"""
            try {{
                const app = nexacro.getApplication();
                const wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet
                    .{self.FRAME_ID}.form.{self.DS_PATH};
                const ds = wf?.dsResult;
                if (!ds) return {{error: 'dsResult not found'}};

                function getVal(row, col) {{
                    let val = ds.getColumn(row, col);
                    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                    return val ? String(val) : '';
                }}

                const items = [];
                for (let i = 0; i < ds.getRowCount(); i++) {{
                    const cd = getVal(i, 'ITEM_CD');
                    if (cd) {{
                        items.push({{
                            item_cd: cd,
                            item_nm: getVal(i, 'ITEM_NM'),
                            mid_cd: getVal(i, 'MID_CD')
                        }});
                    }}
                }}
                return {{items: items, total: ds.getRowCount()}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        if not result or result.get('error'):
            logger.warning(f"자동발주 상품 상세 조회 실패: {result}")
            return None

        items = result.get('items', [])
        logger.info(f"자동발주 상품 상세: {len(items)}개 (dsResult 총 {result.get('total', 0)}행)")
        return items

    # === 스마트 발주 상품 수집 ===

    def click_smart_radio(self) -> bool:
        """'스마트' 라디오 버튼 클릭

        라디오: rdGubun (div_work.form.Div21.form)
        항목: 전체(0), 일반(1), 자동(2), 스마트(3)

        3단계 폴백 전략:
        1. 넥사크로 Radio API - rdGubun.set_value('3')
        2. "스마트" 텍스트 부모 요소 클릭
        3. rdGubun 영역 내 텍스트 검색
        """
        if not self.driver:
            return False

        logger.info("스마트 라디오 버튼 클릭 시도...")

        result = self.driver.execute_script(JS_CLICK_HELPER + f"""
            try {{
                const app = nexacro.getApplication();
                const frame = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{self.FRAME_ID};
                const wf = frame.form.{self.DS_PATH};

                // Strategy A: 넥사크로 Radio API - rdGubun (Div21 내부)
                // value 매핑: 0=전체, 1=일반, 2=자동, 3=스마트
                var radio = wf.Div21 && wf.Div21.form && wf.Div21.form.rdGubun;
                if (radio && radio.set_value) {{
                    radio.set_value('3');
                    if (radio.on_fire_onitemchanged) {{
                        radio.on_fire_onitemchanged(radio, {{}});
                    }}
                    return {{success: true, method: 'api', component: 'rdGubun', value: '3'}};
                }}

                // Strategy B: "스마트" 텍스트 부모 클릭 (프레임 범위)
                const allSpans = document.querySelectorAll(
                    '[id*="{self.FRAME_ID}"] span, [id*="{self.FRAME_ID}"] div'
                );
                for (const el of allSpans) {{
                    if (el.textContent.trim() === '스마트' && el.offsetParent !== null) {{
                        clickElement(el.parentElement || el);
                        return {{success: true, method: 'text_parent'}};
                    }}
                }}

                // Strategy C: rdGubun 영역 내 텍스트 검색
                const rdSpans = document.querySelectorAll(
                    'div[id*="rdGubun"] span, div[id*="rdGubun"] div'
                );
                for (const el of rdSpans) {{
                    if (el.textContent.trim() === '스마트' && el.offsetParent !== null) {{
                        clickElement(el.parentElement || el);
                        return {{success: true, method: 'text_rdGubun'}};
                    }}
                }}

                return {{success: false, error: 'rdGubun smart radio not found'}};
            }} catch(e) {{
                return {{success: false, error: e.message}};
            }}
        """)

        if result and result.get('success'):
            logger.info(f"스마트 라디오 클릭 성공 (method: {result.get('method')}, value: {result.get('value', '-')})")
            return True

        error = result.get('error', 'unknown') if result else 'no result'
        logger.warning(f"스마트 라디오 클릭 실패: {error}")
        return False

    def collect_smart_order_items(self) -> Set[str]:
        """스마트발주 상품코드 목록 수집

        Returns:
            스마트발주 상품코드 set (실패 시 빈 set)
        """
        if not self.driver:
            return set()

        if not self.click_smart_radio():
            logger.warning("스마트 라디오 클릭 실패 - 빈 목록 반환")
            return set()

        time.sleep(OS_RADIO_CLICK_WAIT)

        result = self.driver.execute_script(f"""
            try {{
                const app = nexacro.getApplication();
                const wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet
                    .{self.FRAME_ID}.form.{self.DS_PATH};
                const ds = wf?.dsResult;
                if (!ds) return {{error: 'dsResult not found'}};

                const items = [];
                for (let i = 0; i < ds.getRowCount(); i++) {{
                    let cd = ds.getColumn(i, 'ITEM_CD');
                    if (cd && typeof cd === 'object' && cd.hi !== undefined) cd = cd.hi;
                    if (cd) items.push(String(cd));
                }}
                return {{items: items, total: ds.getRowCount()}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        if not result or result.get('error'):
            logger.warning(f"스마트발주 상품 조회 실패: {result}")
            return set()

        items = set(result.get('items', []))
        logger.info(f"스마트발주 상품: {len(items)}개 (dsResult 총 {result.get('total', 0)}행)")
        return items

    def collect_smart_order_items_detail(self) -> Optional[List[Dict[str, str]]]:
        """스마트발주 상품 상세 목록 수집 (DB 캐시용)

        Returns:
            [{"item_cd": "...", "item_nm": "...", "mid_cd": "..."}, ...]
            실패 시 None (사이트 조회 불가), 정상 0건 시 빈 리스트
        """
        if not self.driver:
            return None

        if not self.click_smart_radio():
            logger.warning("스마트 라디오 클릭 실패 - None 반환 (detail)")
            return None

        time.sleep(OS_RADIO_CLICK_WAIT)

        result = self.driver.execute_script(f"""
            try {{
                const app = nexacro.getApplication();
                const wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet
                    .{self.FRAME_ID}.form.{self.DS_PATH};
                const ds = wf?.dsResult;
                if (!ds) return {{error: 'dsResult not found'}};

                function getVal(row, col) {{
                    let val = ds.getColumn(row, col);
                    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                    return val ? String(val) : '';
                }}

                const items = [];
                for (let i = 0; i < ds.getRowCount(); i++) {{
                    const cd = getVal(i, 'ITEM_CD');
                    if (cd) {{
                        items.push({{
                            item_cd: cd,
                            item_nm: getVal(i, 'ITEM_NM'),
                            mid_cd: getVal(i, 'MID_CD')
                        }});
                    }}
                }}
                return {{items: items, total: ds.getRowCount()}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        if not result or result.get('error'):
            logger.warning(f"스마트발주 상품 상세 조회 실패: {result}")
            return None

        items = result.get('items', [])
        logger.info(f"스마트발주 상품 상세: {len(items)}개 (dsResult 총 {result.get('total', 0)}행)")
        return items

    # === 전체 품목 발주단위 수집 ===

    def click_all_radio(self) -> bool:
        """'전체' 라디오 버튼 클릭

        라디오: rdGubun (div_work.form.Div21.form)
        항목: 전체(0), 일반(1), 자동(2), 스마트(3)

        3단계 폴백 전략:
        1. 넥사크로 Radio API - rdGubun.set_value('0')
        2. "전체" 텍스트 부모 요소 클릭
        3. rdGubun 영역 내 텍스트 검색

        Returns:
            클릭 성공 여부
        """
        if not self.driver:
            return False

        logger.info("전체 라디오 버튼 클릭 시도...")

        result = self.driver.execute_script(JS_CLICK_HELPER + f"""
            try {{
                const app = nexacro.getApplication();
                const frame = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{self.FRAME_ID};
                const wf = frame.form.{self.DS_PATH};

                // Strategy A: 넥사크로 Radio API - rdGubun (Div21 내부)
                // value 매핑: 0=전체, 1=일반, 2=자동, 3=스마트
                var radio = wf.Div21 && wf.Div21.form && wf.Div21.form.rdGubun;
                if (radio && radio.set_value) {{
                    radio.set_value('0');
                    if (radio.on_fire_onitemchanged) {{
                        radio.on_fire_onitemchanged(radio, {{}});
                    }}
                    return {{success: true, method: 'api', component: 'rdGubun', value: '0'}};
                }}

                // Strategy B: "전체" 텍스트 부모 클릭 (프레임 범위)
                const allSpans = document.querySelectorAll(
                    '[id*="{self.FRAME_ID}"] span, [id*="{self.FRAME_ID}"] div'
                );
                for (const el of allSpans) {{
                    if (el.textContent.trim() === '전체' && el.offsetParent !== null) {{
                        clickElement(el.parentElement || el);
                        return {{success: true, method: 'text_parent'}};
                    }}
                }}

                // Strategy C: rdGubun 영역 내 텍스트 검색
                const rdSpans = document.querySelectorAll(
                    'div[id*="rdGubun"] span, div[id*="rdGubun"] div'
                );
                for (const el of rdSpans) {{
                    if (el.textContent.trim() === '전체' && el.offsetParent !== null) {{
                        clickElement(el.parentElement || el);
                        return {{success: true, method: 'text_rdGubun'}};
                    }}
                }}

                return {{success: false, error: 'rdGubun all radio not found'}};
            }} catch(e) {{
                return {{success: false, error: e.message}};
            }}
        """)

        if result and result.get('success'):
            logger.info(f"전체 라디오 클릭 성공 (method: {result.get('method')})")
            return True

        error = result.get('error', 'unknown') if result else 'no result'
        logger.warning(f"전체 라디오 클릭 실패: {error}")
        return False

    def collect_all_order_unit_qty(self) -> Optional[List[Dict[str, Any]]]:
        """전체 품목 발주단위(ORD_UNIT_QTY) 수집

        "전체" 라디오 선택 후 dsResult에서 모든 상품의
        ITEM_CD, ITEM_NM, MID_CD, ORD_UNIT_QTY를 추출.

        Direct API 우선 시도 → 실패 시 Selenium 폴백.

        Returns:
            [{"item_cd", "item_nm", "mid_cd", "order_unit_qty"}, ...]
            실패 시 None, 정상 0건 시 빈 리스트
        """
        if not self.driver:
            return None

        # Selenium: 라디오 클릭 (Direct API 템플릿 캡처도 겸함)
        if not self.click_all_radio():
            logger.warning("전체 라디오 클릭 실패 - None 반환")
            return None

        time.sleep(OS_RADIO_CLICK_WAIT)

        # ★ Direct API 시도 (라디오 클릭 XHR이 인터셉터에 캡처됨)
        api_data = self._try_direct_api_order_data('0')
        if api_data and api_data.get('dsResult'):
            items = self._extract_order_unit_from_api(api_data['dsResult'])
            if items is not None:
                return items

        # Selenium 폴백: nexacro dsResult 직접 읽기
        return self._read_order_unit_from_nexacro()

    def _extract_order_unit_from_api(
        self, ds_result: List[Dict[str, str]]
    ) -> Optional[List[Dict[str, Any]]]:
        """Direct API dsResult에서 발주단위 추출"""
        items = []
        for row in ds_result:
            cd = row.get('ITEM_CD', '')
            if cd:
                unit = row.get('ORD_UNIT_QTY', '1')
                try:
                    unit_int = int(unit)
                    if unit_int <= 0:
                        unit_int = 1
                except (ValueError, TypeError):
                    unit_int = 1

                items.append({
                    'item_cd': cd,
                    'item_nm': row.get('ITEM_NM', ''),
                    'mid_cd': row.get('MID_CD', ''),
                    'order_unit_qty': unit_int,
                })
        logger.info(f"[OrderStatusAPI] 발주단위 추출: {len(items)}개")
        return items

    def _read_order_unit_from_nexacro(self) -> Optional[List[Dict[str, Any]]]:
        """Selenium으로 nexacro dsResult에서 발주단위 읽기"""
        result = self.driver.execute_script(f"""
            try {{
                const app = nexacro.getApplication();
                const wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet
                    .{self.FRAME_ID}.form.{self.DS_PATH};
                const ds = wf?.dsResult;
                if (!ds) return {{error: 'dsResult not found'}};

                function getVal(row, col) {{
                    let val = ds.getColumn(row, col);
                    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                    return val ? String(val) : '';
                }}

                function getInt(row, col) {{
                    let val = ds.getColumn(row, col);
                    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                    let n = parseInt(val, 10);
                    return isNaN(n) || n <= 0 ? 1 : n;
                }}

                const items = [];
                for (let i = 0; i < ds.getRowCount(); i++) {{
                    const cd = getVal(i, 'ITEM_CD');
                    if (cd) {{
                        items.push({{
                            item_cd: cd,
                            item_nm: getVal(i, 'ITEM_NM'),
                            mid_cd: getVal(i, 'MID_CD'),
                            order_unit_qty: getInt(i, 'ORD_UNIT_QTY')
                        }});
                    }}
                }}
                return {{items: items, total: ds.getRowCount()}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        if not result or result.get('error'):
            logger.warning(f"전체 품목 발주단위 조회 실패: {result}")
            return None

        items = result.get('items', [])
        logger.info(f"전체 품목 발주단위: {len(items)}개 (dsResult 총 {result.get('total', 0)}행)")
        return items

    # === 홈 화면 바코드 검색으로 발주단위 수집 ===

    def collect_order_unit_via_home(
        self, item_codes: List[str]
    ) -> List[Dict[str, Any]]:
        """홈 화면 바코드 검색으로 발주단위 수집

        로그인 직후 홈 상태에서 edt_pluSearch에 상품코드를 입력하고
        CallItemDetailPopup의 dsItemDetail에서 ORD_UNIT_QTY를 추출.

        Args:
            item_codes: 수집 대상 상품코드 리스트

        Returns:
            [{"item_cd", "item_nm", "order_unit_qty"}, ...]
        """
        if not self.driver:
            return []

        results: List[Dict[str, Any]] = []
        total = len(item_codes)
        fail_count = 0

        for idx, item_cd in enumerate(item_codes):
            try:
                logger.info(
                    f"[{idx + 1}/{total}] 발주단위 조회: {item_cd}"
                )

                # 1. 바코드 입력
                if not self._home_input_barcode(item_cd):
                    logger.warning(f"바코드 입력 실패: {item_cd}")
                    fail_count += 1
                    continue

                # 2. Enter 트리거
                if not self._home_trigger_enter():
                    logger.warning(f"Enter 트리거 실패: {item_cd}")
                    fail_count += 1
                    continue

                # 3. Quick Search 클릭
                time.sleep(0.5)
                if not self._home_click_quick_search():
                    logger.warning(f"Quick Search 클릭 실패: {item_cd}")
                    self._home_clear_alerts()
                    fail_count += 1
                    continue

                # 4. 팝업 대기
                if not self._home_wait_for_popup():
                    logger.warning(f"팝업 미표시: {item_cd}")
                    self._home_clear_alerts()
                    fail_count += 1
                    continue

                # 5. ORD_UNIT_QTY 추출
                data = self._home_extract_order_unit(item_cd)

                # 6. 팝업 닫기
                self._home_close_popup()
                time.sleep(FR_POPUP_CLOSE_WAIT)

                if data:
                    results.append(data)

            except Exception as e:
                logger.warning(f"발주단위 조회 오류 ({item_cd}): {e}")
                fail_count += 1
                try:
                    self._home_close_popup()
                except Exception:
                    pass
                try:
                    self._home_clear_alerts()
                except Exception:
                    pass

            # 상품 간 간격
            if idx < total - 1:
                time.sleep(FR_BETWEEN_ITEMS)

        logger.info(
            f"홈 바코드 발주단위 수집 완료: "
            f"{len(results)}/{total}건 성공, {fail_count}건 실패"
        )
        return results

    def _home_input_barcode(self, item_cd: str) -> bool:
        """홈 화면 edt_pluSearch에 바코드 입력"""
        barcode_dom = FAIL_REASON_UI["BARCODE_INPUT_DOM"]

        result = self.driver.execute_script("""
            var barcode = arguments[0];
            try {
                var app = nexacro.getApplication();
                var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                if (wf && wf.form && wf.form.edt_pluSearch) {
                    wf.form.edt_pluSearch.set_value(barcode);
                    return {success: true, method: 'nexacro'};
                }
            } catch(e) {}

            try {
                var el = document.getElementById(arguments[1]);
                if (el) {
                    el.value = barcode;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return {success: true, method: 'dom_id'};
                }
            } catch(e) {}

            try {
                var inputs = document.querySelectorAll('[id*="edt_pluSearch"]');
                for (var i = 0; i < inputs.length; i++) {
                    var inp = inputs[i];
                    var target = inp.tagName === 'INPUT' ? inp : inp.querySelector('input');
                    if (target) {
                        target.value = barcode;
                        target.dispatchEvent(new Event('input', {bubbles: true}));
                        return {success: true, method: 'querySelector'};
                    }
                }
            } catch(e) {}

            return {success: false};
        """, item_cd, barcode_dom)

        if result and result.get("success"):
            time.sleep(FR_BARCODE_INPUT_WAIT)
            return True
        return False

    def _home_trigger_enter(self) -> bool:
        """Enter 키로 상품 상세 팝업 트리거"""
        barcode_dom = FAIL_REASON_UI["BARCODE_INPUT_DOM"]

        result = self.driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                var edt = wf.form.edt_pluSearch;
                if (edt && typeof edt.on_fire_onkeyup === 'function') {
                    var evt = new nexacro.KeyEventInfo(
                        edt, 'onkeyup', 13, false, false, false, false
                    );
                    edt.on_fire_onkeyup(edt, evt);
                    return {success: true, method: 'nexacro_event'};
                }
            } catch(e) {}

            try {
                var domId = arguments[0];
                var el = document.getElementById(domId);
                if (!el) {
                    var els = document.querySelectorAll(
                        '[id*="edt_pluSearch"] input, [id*="edt_pluSearch"]'
                    );
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
        """, barcode_dom)

        return bool(result and result.get("success"))

    def _home_click_quick_search(self) -> bool:
        """Quick Search 드롭다운 첫 항목 클릭"""
        result = self.driver.execute_script("""
            // 1차: nexacro grid 클릭
            try {
                var app = nexacro.getApplication();
                var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                if (wf && wf.form) {
                    var grids = ['grd_quickSearch', 'grd_pluSearch', 'Grid00'];
                    for (var i = 0; i < grids.length; i++) {
                        var g = wf.form[grids[i]];
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

            // 2차: DOM Quick Search 결과 셀 클릭
            try {
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

            // 3차: 첫 번째 보이는 행 클릭
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
                                      clientX: rb.left + rb.width/2,
                                      clientY: rb.top + rb.height/2};
                            row.dispatchEvent(new MouseEvent('mousedown', mo));
                            row.dispatchEvent(new MouseEvent('mouseup', mo));
                            row.dispatchEvent(new MouseEvent('click', mo));
                            return {success: true, method: 'dom_row_click'};
                        }
                    }
                }
            } catch(e) {}

            return {success: false};
        """)

        return bool(result and result.get("success"))

    def _home_wait_for_popup(self) -> bool:
        """CallItemDetailPopup 대기"""
        popup_id = FAIL_REASON_UI["POPUP_ID"]

        for _ in range(FR_POPUP_MAX_CHECKS):
            result = self.driver.execute_script("""
                var pid = arguments[0];
                try {
                    var app = nexacro.getApplication();
                    var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                    if (wf[pid] && wf[pid].form) return {found: true};
                    if (wf.popupframes && wf.popupframes[pid] && wf.popupframes[pid].form)
                        return {found: true};
                    if (wf.form && wf.form[pid] && wf.form[pid].form)
                        return {found: true};
                } catch(e) {}

                try {
                    var el = document.querySelector('[id$="' + pid + '.form"]');
                    if (el && el.offsetParent !== null) return {found: true};
                } catch(e) {}

                return {found: false};
            """, popup_id)

            if result and result.get("found"):
                return True
            time.sleep(FR_POPUP_CHECK_INTERVAL)

        return False

    def _home_extract_order_unit(self, item_cd: str) -> Optional[Dict[str, Any]]:
        """CallItemDetailPopup에서 ORD_UNIT_QTY 추출"""
        popup_id = FAIL_REASON_UI["POPUP_ID"]

        result = self.driver.execute_script("""
            var popupId = arguments[0];
            var itemCd = arguments[1];

            function getPopupForm() {
                try {
                    var app = nexacro.getApplication();
                    var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                    if (wf[popupId] && wf[popupId].form) return wf[popupId].form;
                    if (wf.popupframes && wf.popupframes[popupId])
                        return wf.popupframes[popupId].form;
                    if (wf.form && wf.form[popupId])
                        return wf.form[popupId].form;
                } catch(e) {}
                return null;
            }

            function gv(ds, col) {
                try {
                    var v = ds.getColumn(0, col);
                    if (v && typeof v === 'object' && v.hi !== undefined) v = v.hi;
                    return v ? String(v) : '';
                } catch(e) { return ''; }
            }

            var popupForm = getPopupForm();
            if (!popupForm) return null;

            var d1 = null, d2 = null;
            try {
                d1 = popupForm.dsItemDetail;
                d2 = popupForm.dsItemDetailOrd || d1;
            } catch(e) {}

            // objects에서 찾기
            if (!d1) {
                try {
                    var objs = popupForm.objects;
                    if (objs) {
                        for (var i = 0; i < objs.length; i++) {
                            var nm = objs[i].name || objs[i].id || '';
                            if (nm === 'dsItemDetail') d1 = objs[i];
                            if (nm === 'dsItemDetailOrd') d2 = objs[i];
                        }
                    }
                } catch(e) {}
            }

            if (!d1 && !d2) return null;
            if (!d2) d2 = d1;
            if (!d1) d1 = d2;

            if (d1.getRowCount() === 0 && d2.getRowCount() === 0) return null;

            var oqRaw = gv(d1, 'ORD_UNIT_QTY') || gv(d2, 'ORD_UNIT_QTY')
                     || gv(d1, 'IN_QTY') || gv(d2, 'IN_QTY')
                     || gv(d1, 'BAESOO') || gv(d2, 'BAESOO');
            var oq = parseInt(oqRaw, 10);
            if (isNaN(oq) || oq <= 0) oq = 1;

            return {
                item_cd: gv(d1, 'ITEM_CD') || itemCd,
                item_nm: gv(d1, 'ITEM_NM') || gv(d2, 'ITEM_NM') || '',
                order_unit_qty: oq
            };
        """, popup_id, item_cd)

        return result

    def _home_close_popup(self) -> None:
        """CallItemDetailPopup 닫기"""
        popup_id = FAIL_REASON_UI["POPUP_ID"]
        close_btn = FAIL_REASON_UI["POPUP_CLOSE_BTN"]

        self.driver.execute_script("""
            var popupId = arguments[0];

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

            try {
                var btn = document.getElementById(arguments[1]);
                if (btn) { btn.click(); return; }
            } catch(e) {}

            try {
                var btns = document.querySelectorAll(
                    '[id*="CallItemDetailPopup"][id*="btn_close"]'
                );
                for (var i = 0; i < btns.length; i++) {
                    if (btns[i].offsetParent !== null) {
                        btns[i].click(); return;
                    }
                }
            } catch(e) {}

            try {
                var app2 = nexacro.getApplication();
                var wf2 = app2.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                if (wf2.popupframes && wf2.popupframes[popupId]) {
                    wf2.popupframes[popupId].close();
                }
            } catch(e) {}
        """, popup_id, close_btn)

    def _home_clear_alerts(self) -> None:
        """Alert 다이얼로그 처리"""
        for _ in range(5):
            try:
                alert = self.driver.switch_to.alert
                alert.accept()
                time.sleep(0.2)
            except Exception:
                break

    def close_menu(self) -> bool:
        """
        발주 현황 조회 메뉴 탭 닫기 (verified — DOM 소멸 검증)

        Returns:
            탭 닫기 성공 여부
        """
        from src.utils.nexacro_helpers import close_tab_verified
        result = close_tab_verified(
            self.driver, self.FRAME_ID,
            max_retries=3, poll_timeout=3.0,
        )
        if result:
            time.sleep(OS_MENU_CLOSE_WAIT)
            logger.info("발주 현황 조회 탭 닫기 성공 (verified)")
        else:
            logger.error("발주 현황 조회 탭 닫기 최종 실패 (verified)")
        return result

    def sync_pending_to_order_tracking(self, days_back: int = 7) -> Dict[str, Any]:
        """
        발주현황(전체)에서 미입고 발주를 order_tracking에 동기화.
        자동발주(auto) 외의 발주(수동 등)를 site 소스로 저장하여
        교차검증 시 수동발주도 pending으로 인식되도록 한다.

        Args:
            days_back: 조회 기간 (기본 7일)

        Returns:
            {"synced": int, "skipped": int, "errors": int}
        """
        result = {"synced": 0, "skipped": 0, "errors": 0}

        if not self.driver:
            logger.error("드라이버가 설정되지 않음")
            return result

        try:
            # 1. "전체" 라디오 선택
            if not self.click_all_radio():
                logger.warning("전체 라디오 클릭 실패, 동기화 건너뜀")
                return result

            time.sleep(OS_RADIO_CLICK_WAIT)

            # 2. 발주현황(dsResult) + 발주/판매이력(dsOrderSale) 수집
            order_status = self.collect_order_status()
            item_info_map = {}
            for status_item in order_status:
                cd = status_item.get('ITEM_CD')
                if cd:
                    item_info_map[str(cd)] = {
                        'item_nm': status_item.get('ITEM_NM', ''),
                        'mid_cd': status_item.get('MID_CD', ''),
                    }

            order_sale_data = self.collect_order_sale_history()
            if not order_sale_data:
                logger.info("[발주현황동기화] 발주/판매 이력 없음")
                return result

            # 3. 기간 필터 (최근 N일만)
            from datetime import date as date_type
            cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")

            for item in order_sale_data:
                ord_ymd = item.get('ORD_YMD')
                item_cd = item.get('ITEM_CD')
                ord_qty = item.get('ORD_QTY')
                buy_qty = item.get('BUY_QTY')

                if not ord_ymd or not item_cd or not ord_qty:
                    continue

                # 기간 필터
                raw_ymd = str(ord_ymd).replace('-', '')
                if raw_ymd < cutoff:
                    continue

                try:
                    ord_qty_int = int(ord_qty)
                    buy_qty_int = int(buy_qty) if buy_qty else 0
                except (ValueError, TypeError):
                    continue

                # 이미 입고 완료된 건은 스킵
                if ord_qty_int <= buy_qty_int:
                    result["skipped"] += 1
                    continue

                order_date = self._format_order_date(str(ord_ymd))
                if not order_date:
                    continue

                # 4. OT에 이미 'auto'로 존재하는지 확인
                try:
                    existing_auto = self.tracking_repo.get_existing_order(
                        order_date=order_date,
                        item_cd=str(item_cd),
                        order_source='auto',
                        store_id=self.store_id
                    )
                    if existing_auto:
                        # 자동발주로 이미 기록됨 → 스킵
                        result["skipped"] += 1
                        continue

                    # 5. 'site'로 이미 존재하는지 확인
                    existing_site = self.tracking_repo.get_existing_order(
                        order_date=order_date,
                        item_cd=str(item_cd),
                        order_source='site',
                        store_id=self.store_id
                    )
                    if existing_site:
                        # 이미 site로 저장됨 → 스킵
                        result["skipped"] += 1
                        continue

                    # 6. 신규 저장 (order_source='site')
                    info = item_info_map.get(str(item_cd), {})
                    remaining_qty = ord_qty_int - buy_qty_int

                    self.tracking_repo.save_order(
                        order_date=order_date,
                        item_cd=str(item_cd),
                        item_nm=info.get('item_nm', ''),
                        mid_cd=info.get('mid_cd', ''),
                        delivery_type='',
                        order_qty=remaining_qty,
                        arrival_time='',
                        expiry_time='',
                        store_id=self.store_id,
                        order_source='site'
                    )
                    result["synced"] += 1
                    logger.debug(
                        f"[발주현황동기화] {item_cd} {order_date}: "
                        f"발주={ord_qty_int}, 입고={buy_qty_int}, "
                        f"미입고={remaining_qty} → site 저장"
                    )

                except Exception as e:
                    result["errors"] += 1
                    logger.warning(
                        f"[발주현황동기화] {item_cd} {order_date} 저장 실패: {e}"
                    )

            logger.info(
                f"[발주현황동기화] 완료: "
                f"신규={result['synced']}건, "
                f"스킵={result['skipped']}건, "
                f"에러={result['errors']}건"
            )
            return result

        except Exception as e:
            logger.error(f"[발주현황동기화] 전체 실패: {e}")
            return result

    def post_order_pending_sync(self, order_date: str) -> Dict[str, Any]:
        """
        발주현황 조회 → 오늘 발주건의 미입고 pending 마킹.

        BGF 발주현황 화면 구조 (크롬 확장 조사 2026-03-18):
        - 프레임: STBJ070_M0 (발주현황조회)
        - API: stbj070/selSearch
          args: strOrdYmd="YYYYMMDD" strGubun="0" strOrdTurnHms="100000"
        - dsResult (42컬럼, 메인): ORD_YMD, ITEM_CD, PYUN_QTY, ORD_INPUT_ID 등
          → strOrdYmd로 날짜 지정 가능 (10시 이후 기본=내일)
          → ORD_INPUT_ID: 단품별(재택), 자동발주, 스마트발주, 발주수정(재택) 등
        - dsOrderSale (8컬럼, 주간 이력): ORD_QTY, BUY_QTY (발주방법 없음)
        - calDay: strOrdYmd에 바인딩됨

        전략: Direct API로 strOrdYmd=오늘 지정 → dsResult 사용
              (dsOrderSale은 발주방법 없어 보조용만)

        Args:
            order_date: 발주일 'YYYY-MM-DD' (보통 오늘)

        Returns:
            {'synced': N, 'skipped': M, 'order_date': str, 'by_method': dict}
        """
        result: Dict[str, Any] = {
            "synced": 0, "skipped": 0, "date_mismatch": False,
            "order_date": order_date, "by_method": {},
        }
        now = datetime.now()
        order_date_fmt = order_date.replace("-", "")

        # 1. Direct API 시도 (strOrdYmd로 날짜 지정)
        ds_result_rows = []
        ds_order_sale_rows = []

        try:
            from src.collectors.direct_frame_fetcher import DirectOrderStatusFetcher

            fetcher = DirectOrderStatusFetcher(self.driver)
            if fetcher.capture_template():
                logger.info(f"[pending_sync] Direct API 조회: strOrdYmd={order_date_fmt}")
                api_data = fetcher.fetch_order_status(
                    gubun='0', ord_ymd=order_date_fmt
                )
                ds_result_rows = api_data.get('dsResult', [])
                ds_order_sale_rows = api_data.get('dsOrderSale', [])
        except Exception as e:
            logger.warning(f"[pending_sync] Direct API 시도 실패: {e}")

        # 2. Direct API 실패 시 Selenium 폴백
        if not ds_result_rows:
            logger.info("[pending_sync] Direct API 데이터 없음, Selenium 폴백")

            # calDay 날짜 변경 + 조회
            if not self._set_cal_day_and_search(order_date_fmt):
                result["reason"] = "selenium_fail"
                return result

            # dsResult 수집 (Selenium)
            order_status = self.collect_order_status()
            if order_status:
                ds_result_rows = order_status

            # dsOrderSale도 수집
            order_sale = self.collect_order_sale_history()
            if order_sale:
                ds_order_sale_rows = order_sale

        if not ds_result_rows and not ds_order_sale_rows:
            logger.info("[pending_sync] 데이터 없음")
            result["reason"] = "no_data"
            return result

        # ── 지점 1: BGF API 응답 직후 (필터 전) 004/005 디버그 ──
        _food_debug_rows = [
            r for r in ds_result_rows
            if str(r.get("MID_CD", "")) in ('004', '005')
        ]
        if _food_debug_rows:
            for r in _food_debug_rows:
                logger.info(
                    f"[PENDING_RAW] MID={r.get('MID_CD')} "
                    f"ITEM={r.get('ITEM_NM','')} "
                    f"ORD_YMD={r.get('ORD_YMD','')} "
                    f"PYUN_QTY={r.get('PYUN_QTY','')} "
                    f"ORD_UNIT_QTY={r.get('ORD_UNIT_QTY','')}"
                )
        else:
            logger.info(
                f"[PENDING_RAW] 004/005 없음 — "
                f"BGF 응답 총 {len(ds_result_rows)}건에 미포함"
            )

        # 3. dsResult에서 오늘 발주건 필터
        today_ds_result = [
            r for r in ds_result_rows
            if str(r.get("ORD_YMD", "")).replace("-", "") == order_date_fmt
        ]

        # 날짜 불일치 체크
        if ds_result_rows and not today_ds_result:
            all_ymds = set(
                str(r.get("ORD_YMD", "")).replace("-", "")
                for r in ds_result_rows if r.get("ORD_YMD")
            )
            logger.warning(
                f"[pending_sync] dsResult 날짜 불일치: "
                f"응답={all_ymds}, 기대={order_date_fmt}"
            )
            result["date_mismatch"] = True
            return result

        # ── 지점 2: today_ds_result 필터 후 004/005 디버그 ──
        _food_filtered = [
            r for r in today_ds_result
            if str(r.get("MID_CD", "")) in ('004', '005')
        ]
        if _food_filtered:
            for r in _food_filtered:
                logger.info(
                    f"[PENDING_FILTERED] MID={r.get('MID_CD')} "
                    f"ITEM={r.get('ITEM_NM','')} "
                    f"ORD_YMD={r.get('ORD_YMD','')} "
                    f"PYUN_QTY={r.get('PYUN_QTY','')}"
                )
        else:
            logger.info(
                f"[PENDING_FILTERED] 004/005 없음 — "
                f"today_ds_result {len(today_ds_result)}건 중 제외됨"
            )

        # 4. dsOrderSale에서 BUY_QTY 보충 (입고 수량 = 이미 도착한 수량)
        buy_qty_map: Dict[str, int] = {}
        for r in ds_order_sale_rows:
            ymd = str(r.get("ORD_YMD", "")).replace("-", "")
            if ymd == order_date_fmt:
                item_cd = str(r.get("ITEM_CD", ""))
                try:
                    buy_qty_map[item_cd] = int(r.get("BUY_QTY", 0) or 0)
                except (ValueError, TypeError):
                    pass

        logger.info(
            f"[pending_sync] 발주일={order_date}, "
            f"dsResult 건수={len(today_ds_result)}, "
            f"dsOrderSale BUY_QTY 건수={len(buy_qty_map)}"
        )

        # 5. DB 업데이트
        confirmed_at = now.isoformat()
        from src.infrastructure.database.connection import DBRouter

        conn = DBRouter.get_connection(store_id=self.store_id, table="order_tracking")
        try:
            cursor = conn.cursor()
            method_counts: Dict[str, int] = {}

            for r in today_ds_result:
                item_cd = str(r.get("ITEM_CD", ""))
                if not item_cd:
                    continue

                # PYUN_QTY = 발주 배수 (BigDecimal dict 또는 int)
                pyun_qty_raw = r.get("PYUN_QTY", 0)
                if isinstance(pyun_qty_raw, dict):
                    pyun_qty = int(pyun_qty_raw.get("hi", 0))
                else:
                    try:
                        pyun_qty = int(pyun_qty_raw or 0)
                    except (ValueError, TypeError):
                        pyun_qty = 0

                # ORD_UNIT_QTY = 발주 단위
                unit_raw = r.get("ORD_UNIT_QTY", 1)
                if isinstance(unit_raw, dict):
                    unit_qty = int(unit_raw.get("hi", 1))
                else:
                    try:
                        unit_qty = int(unit_raw or 1)
                    except (ValueError, TypeError):
                        unit_qty = 1

                ord_qty = pyun_qty * max(1, unit_qty)
                buy_qty = buy_qty_map.get(item_cd, 0)
                pending = max(0, ord_qty - buy_qty)

                # 발주방법
                ord_input_id = str(r.get("ORD_INPUT_ID", ""))
                item_nm = str(r.get("ITEM_NM", ""))
                mid_cd = str(r.get("MID_CD", ""))

                if pending <= 0:
                    result["skipped"] += 1
                    continue

                # 유통기한 1일 당일배송 푸드만 pending 저장 제외
                # (내일 발주 시 pending 차감하면 과소발주 발생)
                # 004(샌드위치,2일)/005(햄버거,3일)는 pending 차감 필요 → 제외
                FOOD_SAME_DAY_MID_CDS = {'001', '002', '003'}
                if mid_cd in FOOD_SAME_DAY_MID_CDS:
                    result["skipped"] += 1
                    logger.debug(
                        f"[pending_sync] {item_cd} 당일배송 푸드({mid_cd}) "
                        f"pending 제외"
                    )
                    continue

                method_counts[ord_input_id] = method_counts.get(ord_input_id, 0) + 1

                # order_tracking 업데이트
                cursor.execute(
                    """
                    UPDATE order_tracking
                    SET pending_confirmed = 1,
                        pending_confirmed_at = ?,
                        remaining_qty = ?,
                        ord_input_id = ?
                    WHERE order_date = ? AND item_cd = ?
                    """,
                    (confirmed_at, pending, ord_input_id, order_date, item_cd),
                )

                if cursor.rowcount == 0:
                    # OT에 없으면 신규 삽입 (수동발주, 신상품 등)
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO order_tracking
                            (store_id, order_date, item_cd, item_nm, mid_cd,
                             order_qty, remaining_qty, order_source, status,
                             pending_confirmed, pending_confirmed_at,
                             ord_input_id, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'site', 'ordered', 1, ?, ?, ?, ?)
                        """,
                        (
                            self.store_id, order_date, item_cd,
                            item_nm, mid_cd, ord_qty, pending,
                            confirmed_at, ord_input_id,
                            confirmed_at, confirmed_at,
                        ),
                    )

                # realtime_inventory pending_qty 업데이트
                cursor.execute(
                    """
                    UPDATE realtime_inventory
                    SET pending_qty = ?
                    WHERE item_cd = ?
                    """,
                    (pending, item_cd),
                )

                result["synced"] += 1

                # ── 지점 3: pending_confirmed 갱신 직후 004/005 디버그 ──
                if mid_cd in ('004', '005'):
                    logger.info(
                        f"[PENDING_CONFIRMED] {item_nm} MID={mid_cd} "
                        f"ord_qty={ord_qty} buy_qty={buy_qty} "
                        f"pending={pending} → confirmed=1"
                    )

            # 발주정지 예정 상품 선제 차단
            stop_plan_count = 0
            today_date = datetime.strptime(order_date, "%Y-%m-%d").date()
            tomorrow = today_date + timedelta(days=1)

            for r in today_ds_result:
                stop_raw = str(r.get("STOP_PLAN_YMD", "") or "").strip()
                cut_yn = str(r.get("CUT_ITEM_YN", "") or "")
                ic = str(r.get("ITEM_CD", ""))
                if not ic or not stop_raw:
                    continue

                # 포맷 파싱: "26.03.18 ~" → date(2026, 3, 18)
                try:
                    date_part = stop_raw.split("~")[0].strip()
                    parts = date_part.split(".")
                    if len(parts) == 3:
                        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                        if y < 100:
                            y += 2000
                        from datetime import date as date_cls
                        stop_date = date_cls(y, m, d)
                    else:
                        continue
                except (ValueError, IndexError):
                    continue

                if stop_date <= tomorrow:
                    cursor.execute(
                        """
                        UPDATE realtime_inventory
                        SET is_cut_item = 1,
                            stop_plan_ymd = ?,
                            cut_reason = 'stop_plan',
                            queried_at = ?
                        WHERE item_cd = ?
                        """,
                        (stop_raw, datetime.now().isoformat(), ic),
                    )
                    if cursor.rowcount > 0:
                        stop_plan_count += 1
                        item_nm_val = str(r.get("ITEM_NM", ""))
                        logger.warning(
                            f"[stop_plan] {ic} ({item_nm_val}) "
                            f"발주정지 {stop_raw} → is_cut_item=1"
                        )

            if stop_plan_count > 0:
                result["stop_plan_marked"] = stop_plan_count

            conn.commit()
        finally:
            conn.close()

        result["by_method"] = method_counts
        logger.info(
            f"[pending_sync] 완료: "
            f"synced={result['synced']}, skipped={result['skipped']}, "
            f"발주방법={method_counts}"
            + (f", stop_plan={result.get('stop_plan_marked', 0)}"
               if result.get('stop_plan_marked') else "")
        )

        # 행동 로그 저장 (AI vs 실제 비교)
        try:
            from src.analysis.order_behavior_logger import OrderBehaviorLogger
            behavior_items = [
                {
                    "item_cd": str(r.get("ITEM_CD", "")),
                    "mid_cd": str(r.get("MID_CD", "")),
                    "actual_qty": (
                        (int(r["PYUN_QTY"].get("hi", 0)) if isinstance(r.get("PYUN_QTY"), dict)
                         else int(r.get("PYUN_QTY", 0) or 0))
                        * max(1, int(r["ORD_UNIT_QTY"].get("hi", 1)) if isinstance(r.get("ORD_UNIT_QTY"), dict)
                              else int(r.get("ORD_UNIT_QTY", 1) or 1))
                    ),
                    "ord_input_id": str(r.get("ORD_INPUT_ID", "")),
                    "buy_qty": buy_qty_map.get(str(r.get("ITEM_CD", "")), 0),
                }
                for r in today_ds_result if r.get("ITEM_CD")
            ]
            bl = OrderBehaviorLogger(store_id=self.store_id)
            behavior_result = bl.log_behavior(
                order_date=order_date,
                actual_orders=behavior_items,
            )
            result["behavior"] = behavior_result
        except Exception as e:
            logger.warning(f"[behavior_log] 실패 (pending은 완료됨): {e}")

        return result

    def _set_cal_day_and_search(self, ord_ymd: str) -> bool:
        """Selenium으로 calDay 변경 + 조회 버튼 클릭 (Direct API 폴백용)"""
        if not self.driver:
            return False
        try:
            # calDay 변경
            self.driver.execute_script(f"""
                var app = nexacro.getApplication();
                var dw = app.mainframe.HFrameSet00.VFrameSet00.FrameSet
                    .STBJ070_M0.form.div_workForm.form.div_work.form;
                dw.Div21.form.calDay.set_value('{ord_ymd}');
            """)
            time.sleep(0.5)

            # 전체 라디오 + 조회
            self.click_all_radio()
            time.sleep(OS_RADIO_CLICK_WAIT)

            # 조회 버튼 클릭
            self.driver.execute_script("""
                var app = nexacro.getApplication();
                app.mainframe.HFrameSet00.VFrameSet00.FrameSet
                    .STBJ070_M0.form.div_cmmbtn.form.F_10.click();
            """)
            time.sleep(2)
            return True
        except Exception as e:
            logger.warning(f"[pending_sync] Selenium calDay 변경 실패: {e}")
            return False

    def clear_expired_pending(self, today: str) -> Dict[str, int]:
        """
        만료된 pending 클리어 (입고 확인 기반 2단계 검증).
        Phase 1.95 시작 시 호출.

        검증 우선순위:
        1차: receiving_history — item_cd + receiving_date >= order_date
             → SUM(receiving_qty) >= order_qty → 입고 완료 → 클리어
        2차: order_tracking.status = 'arrived' → 클리어

        만료 기준:
        - 입고 확인됨 → 즉시 클리어
        - 미입고 + 1일 경과 → pending_confirmed_at 갱신 (1일 연장)
        - 미입고 + 2일 이상 → 강제 클리어 (불량 데이터 안전장치)

        Args:
            today: 'YYYY-MM-DD'

        Returns:
            {'cleared': N, 'extended': M, 'force_cleared': K, 'cleared_ri': R}
        """
        from src.infrastructure.database.connection import DBRouter

        today_date = datetime.strptime(today, "%Y-%m-%d").date()

        conn = DBRouter.get_connection(store_id=self.store_id, table="order_tracking")
        try:
            cursor = conn.cursor()

            # 만료 대상 조회 (pending_confirmed_at < today)
            expired_rows = cursor.execute(
                """
                SELECT item_cd, order_date, order_qty,
                       pending_confirmed_at, status
                FROM order_tracking
                WHERE pending_confirmed = 1
                  AND DATE(pending_confirmed_at) < ?
                """,
                (today,),
            ).fetchall()

            cleared = 0
            extended = 0
            force_cleared = 0

            for item_cd, order_date, order_qty, confirmed_at, ot_status in expired_rows:
                order_qty = order_qty or 0

                # --- 1차: receiving_history 입고 확인 ---
                received_qty = cursor.execute(
                    """
                    SELECT COALESCE(SUM(receiving_qty), 0)
                    FROM receiving_history
                    WHERE item_cd = ?
                      AND receiving_date >= ?
                    """,
                    (item_cd, order_date),
                ).fetchone()[0]

                is_arrived = (received_qty >= order_qty) if order_qty > 0 else False

                # --- 2차: order_tracking.status 폴백 ---
                if not is_arrived:
                    is_arrived = (ot_status == 'arrived')

                # --- 경과일 계산 ---
                days_elapsed = 0
                if confirmed_at:
                    try:
                        conf_date = datetime.fromisoformat(confirmed_at).date()
                        days_elapsed = (today_date - conf_date).days
                    except (ValueError, TypeError):
                        days_elapsed = 99  # 파싱 실패 → 강제 클리어

                # --- 판정 ---
                if is_arrived:
                    # 입고 확인 → 클리어
                    self._clear_pending_item(cursor, item_cd, order_date)
                    cleared += 1
                    logger.debug(
                        f"[pending_clear] 클리어 {item_cd} "
                        f"(received={received_qty}/{order_qty})"
                    )
                elif days_elapsed >= 2:
                    # 2일 이상 미입고 → 강제 클리어
                    self._clear_pending_item(cursor, item_cd, order_date)
                    force_cleared += 1
                    logger.warning(
                        f"[pending_clear] 강제클리어 {item_cd} "
                        f"(미입고 {days_elapsed}일 경과)"
                    )
                else:
                    # 1일 미입고 → pending_confirmed_at 갱신 (1일 연장)
                    cursor.execute(
                        """
                        UPDATE order_tracking
                        SET pending_confirmed_at = ?
                        WHERE item_cd = ? AND order_date = ?
                          AND pending_confirmed = 1
                        """,
                        (datetime.now().isoformat(), item_cd, order_date),
                    )
                    extended += 1
                    logger.debug(
                        f"[pending_clear] 연장 {item_cd} "
                        f"(미입고, {days_elapsed}일 경과)"
                    )

            # realtime_inventory: pending_confirmed=0인 상품의 pending_qty 초기화
            cursor.execute(
                """
                UPDATE realtime_inventory
                SET pending_qty = 0
                WHERE pending_qty > 0
                  AND item_cd NOT IN (
                      SELECT item_cd FROM order_tracking
                      WHERE pending_confirmed = 1
                  )
                """,
            )
            cleared_ri = cursor.rowcount

            conn.commit()

            result = {
                "cleared": cleared,
                "extended": extended,
                "force_cleared": force_cleared,
                "cleared_ri": cleared_ri,
                # 호환성: 기존 호출부에서 cleared_ot 참조
                "cleared_ot": cleared + force_cleared,
                "kept_unreceived": extended,
            }

            if cleared > 0 or extended > 0 or force_cleared > 0:
                logger.info(
                    f"[pending_clear] {self.store_id}: "
                    f"클리어={cleared}, 연장={extended}, "
                    f"강제={force_cleared}, RI={cleared_ri}"
                )

            return result
        finally:
            conn.close()

    def _clear_pending_item(
        self, cursor, item_cd: str, order_date: str
    ) -> None:
        """pending_confirmed 클리어 + realtime_inventory.pending_qty=0"""
        cursor.execute(
            """
            UPDATE order_tracking
            SET pending_confirmed = 0,
                pending_confirmed_at = NULL,
                remaining_qty = 0
            WHERE item_cd = ? AND order_date = ?
              AND pending_confirmed = 1
            """,
            (item_cd, order_date),
        )
        cursor.execute(
            """
            UPDATE realtime_inventory
            SET pending_qty = 0
            WHERE item_cd = ?
            """,
            (item_cd,),
        )

    def collect_and_save(self) -> Dict[str, Any]:
        """
        발주 현황 수집 및 저장

        Returns:
            수집 결과 {"total", "pending", "tracking_updated", "success"}
        """
        # 발주 현황 수집
        order_status = self.collect_order_status()

        # 미입고 발주 목록
        pending_orders = self.get_pending_orders()

        # order_tracking 테이블에 발주 이력 저장
        tracking_updated = 0
        try:
            tracking_updated = self.update_order_tracking_from_status()
        except Exception as e:
            logger.warning(f"order_tracking 업데이트 중 오류: {e}")

        return {
            "success": True,
            "total": len(order_status),
            "pending": len(pending_orders),
            "tracking_updated": tracking_updated,
            "pending_orders": pending_orders
        }


def run_order_status_collection(driver) -> Dict[str, Any]:
    """
    발주 현황 수집 실행

    Args:
        driver: 로그인된 WebDriver

    Returns:
        수집 결과
    """
    collector = OrderStatusCollector(driver)

    # 메뉴 이동
    if not collector.navigate_to_order_status_menu():
        logger.error("메뉴 이동 실패")
        return {"success": False, "error": "menu navigation failed"}

    time.sleep(1)

    # 데이터 수집
    result = collector.collect_and_save()

    return result


if __name__ == "__main__":
    print("이 모듈은 직접 실행할 수 없습니다.")
    print("드라이버를 전달하여 run_order_status_collection(driver)를 호출하세요.")
