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

            # 2. 발주 현황 조회 서브메뉴 클릭
            if not click_submenu_by_text(self.driver, '발주 현황 조회'):
                logger.error("발주 현황 조회 서브메뉴 클릭 실패")
                return False

            time.sleep(2)

            # 3. 화면 로딩 확인
            if wait_for_frame(self.driver, self.FRAME_ID):
                logger.info("발주 현황 조회 화면 로딩 완료")
                return True

            logger.error("화면 로딩 타임아웃")
            return False

        except Exception as e:
            logger.error(f"메뉴 이동 실패: {e}")
            return False

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
                        ORD_CNT: getVal('ORD_CNT'),
                        ORD_UNIT_QTY: getVal('ORD_UNIT_QTY'),
                        ITEM_WONGA: getVal('ITEM_WONGA'),
                        NOW_QTY: getVal('NOW_QTY'),
                        NAP_NEXTORD: getVal('NAP_NEXTORD'),
                        ORD_INPUT_ID: getVal('ORD_INPUT_ID'),
                        ORD_PSS_ID: getVal('ORD_PSS_ID')
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

    def update_order_tracking_from_status(self) -> int:
        """
        발주 현황 데이터로 order_tracking 업데이트
        - 수동 발주도 포함하여 모든 발주 추적
        - 발주 현황(dsResult)에서 상품명/중분류 정보 획득
        - 발주/판매 이력(dsOrderSale)에서 수량 정보 획득

        Returns:
            업데이트된 건수
        """
        # 발주 현황에서 상품 정보 수집 (ITEM_NM, MID_CD 포함)
        order_status = self.collect_order_status()
        item_info_map = {}
        for status_item in order_status:
            cd = status_item.get('ITEM_CD')
            if cd:
                item_info_map[str(cd)] = {
                    'item_nm': status_item.get('ITEM_NM', ''),
                    'mid_cd': status_item.get('MID_CD', ''),
                }

        # 발주/판매 이력 수집
        order_sale_data = self.collect_order_sale_history()

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

        Returns:
            [{"item_cd", "item_nm", "mid_cd", "order_unit_qty"}, ...]
            실패 시 None, 정상 0건 시 빈 리스트
        """
        if not self.driver:
            return None

        if not self.click_all_radio():
            logger.warning("전체 라디오 클릭 실패 - None 반환")
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
        발주 현황 조회 메뉴 탭 닫기

        Returns:
            탭 닫기 성공 여부
        """
        for attempt in range(3):
            try:
                closed = close_tab_by_frame_id(self.driver, self.FRAME_ID)
                if closed:
                    time.sleep(OS_MENU_CLOSE_WAIT)
                    logger.info(f"발주 현황 조회 탭 닫기 성공 (시도 {attempt + 1})")
                    return True
                logger.warning(f"발주 현황 조회 탭 닫기 실패 (시도 {attempt + 1}/3)")
            except Exception as e:
                logger.warning(f"발주 현황 조회 탭 닫기 예외 (시도 {attempt + 1}/3): {e}")
            time.sleep(0.5)
        logger.error("발주 현황 조회 탭 닫기 3회 실패")
        return False

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
