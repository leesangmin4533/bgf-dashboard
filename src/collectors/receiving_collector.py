"""
입고 데이터 수집기
- BGF 리테일 사이트에서 센터매입 조회/확정 데이터 수집
- 실제 입고 시간, 입고 수량 수집
- receiving_history 테이블에 저장
"""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException

from src.settings.ui_config import FRAME_IDS, MENU_TEXT, SUBMENU_TEXT
from src.utils.nexacro_helpers import navigate_menu, close_tab_by_frame_id
from src.infrastructure.database.repos import (
    ReceivingRepository,
    OrderTrackingRepository,
    InventoryBatchRepository,
    RealtimeInventoryRepository,
)
from src.settings.constants import (
    FOOD_CATEGORIES,
    CATEGORY_EXPIRY_DAYS,
    DEFAULT_EXPIRY_DAYS_FOOD,
    DEFAULT_EXPIRY_DAYS_NON_FOOD,
    DEFAULT_STORE_ID,
)
from src.settings.timing import (
    RECEIVING_DATE_SELECT_WAIT,
    RECEIVING_DATA_LOAD_WAIT,
    COLLECTION_RETRY_WAIT,
    COLLECTION_MAX_RETRIES
)
from src.utils.logger import get_logger

# 푸드류 배송차수별 폐기시간 설정
# mid_cd: {delivery_type: (days_offset, expiry_hour)}
# 1차: 저녁 20시 도착, 다다음날 새벽 02시 폐기
# 2차: 아침 07시 도착, 다음날 14시 폐기
FOOD_EXPIRY_CONFIG = {
    '001': {'1차': (2, 2),  '2차': (1, 14)},  # 도시락
    '002': {'1차': (2, 2),  '2차': (1, 14)},  # 주먹밥
    '003': {'1차': (2, 2),  '2차': (1, 14)},  # 김밥
    '004': {'1차': (3, 22), '2차': (2, 10)},  # 샌드위치
    '005': {'1차': (3, 22), '2차': (2, 10)},  # 햄버거
}

logger = get_logger(__name__)


class ReceivingCollector:
    """입고 데이터 수집기"""

    # 프레임 ID
    FRAME_ID = FRAME_IDS["RECEIVING"]

    def __init__(self, driver: Optional[Any] = None, store_id: Optional[str] = None) -> None:
        """
        Args:
            driver: Selenium WebDriver (로그인된 상태)
            store_id: 매장 코드
        """
        self.driver = driver
        self.store_id = store_id
        self.repo = ReceivingRepository(store_id=self.store_id)
        self._new_product_candidates: List[Dict] = []  # 신제품 후보 축적용

    @staticmethod
    def _to_int(value) -> int:
        """넥사크로 반환값을 int로 안전 변환"""
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return int(value)
            except ValueError:
                return 0
        return 0

    def set_driver(self, driver: Any) -> None:
        """드라이버 설정

        Args:
            driver: Selenium WebDriver 인스턴스
        """
        self.driver = driver

    def navigate_to_receiving_menu(self) -> bool:
        """
        검수전표 > 센터매입 조회/확정 메뉴로 이동

        Returns:
            성공 여부
        """
        if not self.driver:
            logger.error("드라이버가 설정되지 않음")
            return False

        logger.info("센터매입 조회/확정 메뉴 이동...")

        try:
            success = navigate_menu(
                self.driver,
                MENU_TEXT["RECEIVING"],
                SUBMENU_TEXT["RECEIVING_CENTER"],
                self.FRAME_ID,
            )
            if success:
                logger.info("센터매입 조회/확정 화면 로딩 완료")
                time.sleep(RECEIVING_DATA_LOAD_WAIT)

                # 팝업 자동 닫기 (Selenium XPath 방법)
                popup_result = self._close_popups()
                if popup_result.get('closed', 0) > 0:
                    logger.info(f"차단 팝업 {popup_result['closed']}개 자동 닫기 완료")
                    time.sleep(1.0)  # 팝업 닫힌 후 안정화 대기
            else:
                logger.error("메뉴 이동 실패")
            return success

        except Exception as e:
            logger.error(f"메뉴 이동 실패: {e}")
            return False

    def _close_popups(self) -> Dict[str, Any]:
        """팝업 자동 닫기 (Selenium XPath)

        넥사크로 팝업은 WorkFrame 등 독립 프레임에 렌더링되므로
        JavaScript 프레임 스코핑이 실패할 수 있음.
        Selenium native XPath로 프레임 무관하게 팝업 닫기.

        Returns:
            {"success": bool, "closed": int, "debug": dict}
        """
        debug_info = {
            "method": "selenium_xpath",
            "attempts": []
        }
        closed = 0

        try:
            # 여러 XPath 패턴으로 "닫기" 버튼 찾기
            xpaths = [
                "//*[text()='닫기']",
                "//button[contains(text(), '닫기')]",
                "//div[contains(text(), '닫기')]",
                "//*[text()='×']",
                "//*[@id[contains(., 'btn_close')]]",
                "//*[@class[contains(., 'btn_close')]]"
            ]

            for xpath in xpaths:
                try:
                    elements = self.driver.find_elements(By.XPATH, xpath)

                    for elem in elements:
                        # 실제로 보이는 요소만 클릭
                        if elem.is_displayed():
                            try:
                                elem.click()
                                closed += 1
                                debug_info["attempts"].append(f"SUCCESS_{elem.tag_name}")
                                # 첫 번째 성공 시 즉시 종료
                                return {"success": True, "closed": closed, "debug": debug_info}
                            except Exception as e:
                                # Element click intercepted 등의 에러 - 다음 요소 시도
                                debug_info["attempts"].append(f"click_failed_{str(e)[:30]}")
                                continue

                except NoSuchElementException:
                    continue

            return {"success": True, "closed": closed, "debug": debug_info}

        except Exception as e:
            logger.debug(f"팝업 닫기 오류 (무시): {e}")
            return {"success": False, "error": str(e), "debug": debug_info}

    def get_available_dates(self) -> List[Dict[str, Any]]:
        """
        조회 가능한 입고일 목록 조회 (dsAcpYmd)

        Returns:
            입고일 목록 [{DGFW_YMD, VIEW_YMD, ...}, ...]
        """
        if not self.driver:
            return []

        result = self.driver.execute_script(f"""
            try {{
                const app = nexacro.getApplication();
                const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{self.FRAME_ID}.form;
                const wf = form.div_workForm?.form;

                if (!wf?.dsAcpYmd) return {{error: 'dsAcpYmd not found'}};

                const ds = wf.dsAcpYmd;
                const rows = [];

                for (let i = 0; i < ds.getRowCount(); i++) {{
                    rows.push({{
                        DGFW_YMD: ds.getColumn(i, 'DGFW_YMD'),
                        VIEW_YMD: ds.getColumn(i, 'VIEW_YMD'),
                        OSTORE_ORD_ID: ds.getColumn(i, 'OSTORE_ORD_ID'),
                        RES_GUBN: ds.getColumn(i, 'RES_GUBN')
                    }});
                }}

                return {{dates: rows}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        if result and result.get('dates'):
            return result['dates']

        return []

    def select_date(self, dgfw_ymd: str) -> bool:
        """
        특정 날짜 선택 (cbAcpYmd 콤보 조작 + 핸들러 호출)

        fn_searchChitListPopup()이 cbAcpYmd.value/index를 직접 읽으므로
        dsAcpYmd.set_rowposition()만으로는 데이터가 갱신되지 않음.
        콤보 set_index + onitemchanged 핸들러를 호출해야 서버 트랜잭션이 발생.

        Args:
            dgfw_ymd: 입고일 (YYYYMMDD 형식)

        Returns:
            성공 여부
        """
        if not self.driver:
            return False

        result = self.driver.execute_script(f"""
            try {{
                const app = nexacro.getApplication();
                const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{self.FRAME_ID}.form;
                const wf = form.div_workForm?.form;

                if (!wf?.dsAcpYmd) return {{error: 'dsAcpYmd not found'}};

                const ds = wf.dsAcpYmd;
                const targetDate = "{dgfw_ymd}";

                // dsAcpYmd에서 날짜 인덱스 찾기
                let targetIdx = -1;
                for (let i = 0; i < ds.getRowCount(); i++) {{
                    if (ds.getColumn(i, 'DGFW_YMD') === targetDate) {{
                        targetIdx = i;
                        break;
                    }}
                }}

                if (targetIdx < 0) {{
                    return {{success: false, reason: 'date not found'}};
                }}

                // 콤보 set_index로 UI 갱신
                const cb = wf.div1?.form?.divSearch?.form?.cbAcpYmd;
                if (!cb) return {{error: 'cbAcpYmd combo not found'}};

                cb.set_index(targetIdx);

                // onitemchanged 핸들러 호출 → dsSearch 세팅 + fn_searchChitListPopup (XHR)
                if (typeof wf.div1_divSearch_cbAcpYmd_onitemchanged === 'function') {{
                    wf.div1_divSearch_cbAcpYmd_onitemchanged(cb, {{
                        postindex: targetIdx
                    }});
                }}

                return {{success: true, row: targetIdx, value: cb.value}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        if result and result.get('success'):
            # XHR 트랜잭션 완료 대기 (서버 응답 + 데이터셋 갱신)
            # 대기 시간 축소: 4.0초 → 2.5초 (서버 응답 충분, 추가 여유분 최소화)
            time.sleep(RECEIVING_DATE_SELECT_WAIT + 0.5)
            return True

        if result and result.get('error'):
            logger.warning(f"날짜 선택 오류: {result['error']}")

        return False

    def _determine_delivery_type(self, center_nm: str, item_nm: str, mid_cd: str = '') -> str:
        """
        배송 타입 결정 (1차/2차 판별: item_nm 끝자리 우선)

        mid_cd가 푸드류(001~005,012)인 경우만 1차/2차 구분.
        비푸드류는 'ambient' 반환.

        Args:
            center_nm: 센터명 (예: 수도권이온저온2)
            item_nm: 상품명
            mid_cd: 중분류 코드

        Returns:
            배송타입 ('1차', '2차', 'ambient')
        """
        FOOD_MID_CDS = {'001', '002', '003', '004', '005', '012'}

        if mid_cd in FOOD_MID_CDS:
            if item_nm and item_nm.strip().endswith('2'):
                return '2차'
            elif item_nm and item_nm.strip().endswith('1'):
                return '1차'

        return 'ambient'

    def _format_receiving_time(self, ais_hms: str) -> str:
        """
        도착시간 포맷 변환

        Args:
            ais_hms: 도착시간 (MMDDHHMM 형식, 예: 01270645)

        Returns:
            시간 (HH:MM 형식)
        """
        if not ais_hms or len(ais_hms) < 8:
            return ""

        # 01270645 -> 06:45
        hh = ais_hms[4:6]
        mm = ais_hms[6:8]

        return f"{hh}:{mm}"

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

    def collect_receiving_data(self, dgfw_ymd: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        입고 데이터 수집 (모든 전표의 상품 수집)

        Direct API 우선 시도 → 실패 시 Selenium 폴백.

        Args:
            dgfw_ymd: 입고일 (YYYYMMDD 형식, None이면 현재 선택된 날짜)

        Returns:
            입고 데이터 리스트
        """
        if not self.driver:
            return []

        # 인터셉터 설치 (select_date의 XHR 캡처용)
        self._install_interceptor()

        # 날짜 선택
        if dgfw_ymd:
            if not self.select_date(dgfw_ymd):
                logger.warning(f"날짜 선택 실패: {dgfw_ymd}")

        # dsListPopup에서 전표 정보 조회 (Selenium - 이미 로딩됨)
        popup_data = self.driver.execute_script(f"""
            try {{
                const app = nexacro.getApplication();
                const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{self.FRAME_ID}.form;
                const wf = form.div_workForm?.form;

                const ds = wf?.dsListPopup;
                if (!ds) return {{error: 'dsListPopup not found'}};

                const rows = [];
                for (let i = 0; i < ds.getRowCount(); i++) {{
                    rows.push({{
                        ROW_INDEX: i,
                        CHIT_NO: ds.getColumn(i, 'CHIT_NO'),
                        DGFW_YMD: ds.getColumn(i, 'DGFW_YMD'),
                        ORD_YMD: ds.getColumn(i, 'ORD_YMD'),
                        AIS_HMS: ds.getColumn(i, 'AIS_HMS'),
                        ACP_HMS: ds.getColumn(i, 'ACP_HMS'),
                        CENTER_NM: ds.getColumn(i, 'CENTER_NM')
                    }});
                }}

                return {{popup: rows, count: ds.getRowCount()}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        if not popup_data or not popup_data.get('popup'):
            logger.warning("전표 목록 없음")
            return []

        chit_list = popup_data['popup']
        logger.info(f"전표 수: {len(chit_list)}개")

        # ★ Direct API로 전표별 상품 일괄 조회 시도
        direct_data = self._try_direct_api_items(chit_list, dgfw_ymd)
        if direct_data is not None:
            return direct_data

        # ═══ Selenium 폴백: 전표별 순차 조회 ═══
        return self._collect_items_via_selenium(chit_list, dgfw_ymd)

    def _install_interceptor(self) -> None:
        """XHR 인터셉터 설치 (Direct API 캡처용)"""
        try:
            from src.collectors.direct_frame_fetcher import install_interceptor
            install_interceptor(self.driver)
        except Exception as e:
            logger.debug(f"인터셉터 설치 실패 (무시): {e}")

    def _try_direct_api_items(
        self,
        chit_list: List[Dict],
        dgfw_ymd: Optional[str],
    ) -> Optional[List[Dict[str, Any]]]:
        """Direct API로 전표별 상품 일괄 조회

        Returns:
            성공 시 입고 데이터 리스트, 실패 시 None (Selenium 폴백)
        """
        try:
            from src.collectors.direct_frame_fetcher import DirectReceivingFetcher

            fetcher = DirectReceivingFetcher(self.driver)
            if not fetcher.capture_templates():
                logger.debug("[ReceivingAPI] 템플릿 캡처 실패, Selenium 폴백")
                return None

            chit_nos = [c['CHIT_NO'] for c in chit_list if c.get('CHIT_NO')]
            if not chit_nos:
                return None

            date_str = dgfw_ymd or ''
            results = fetcher.fetch_items_for_chits(chit_nos, date_str)
            if not results:
                logger.debug("[ReceivingAPI] API 조회 결과 없음, Selenium 폴백")
                return None

            # 성공률 체크: 50% 미만이면 Selenium 폴백
            ok_count = len(results)
            success_rate = ok_count / len(chit_nos) if chit_nos else 0
            if success_rate < 0.5:
                logger.warning(
                    f"[ReceivingAPI] 성공률 낮음 "
                    f"({ok_count}/{len(chit_nos)}={success_rate:.0%}), "
                    f"Selenium 폴백"
                )
                return None

            # SSV 딕셔너리 → 입고 레코드 변환
            all_data = []
            for chit in chit_list:
                chit_no = chit.get('CHIT_NO')
                items = results.get(chit_no, [])

                for item in items:
                    record = self._build_receiving_record(chit, item, dgfw_ymd)
                    all_data.append(record)

            logger.info(
                f"[ReceivingAPI] Direct API 성공: "
                f"{len(chit_nos)}전표, {ok_count}성공, {len(all_data)}건"
            )
            return all_data

        except Exception as e:
            logger.warning(f"[ReceivingAPI] Direct API 실패, Selenium 폴백: {e}")
            return None

    def _build_receiving_record(
        self,
        chit: Dict,
        item: Dict[str, str],
        dgfw_ymd: Optional[str],
    ) -> Dict[str, Any]:
        """전표 헤더 + 상품 SSV → 입고 레코드 변환"""
        chit_no = chit.get('CHIT_NO', '')

        dgfw_raw = chit.get('DGFW_YMD') or dgfw_ymd
        if dgfw_raw and len(dgfw_raw) == 8:
            receiving_date = f"{dgfw_raw[:4]}-{dgfw_raw[4:6]}-{dgfw_raw[6:8]}"
        else:
            receiving_date = datetime.now().strftime("%Y-%m-%d")

        ais_hms = chit.get('AIS_HMS', '')
        receiving_time = self._format_receiving_time(ais_hms)

        ord_ymd = chit.get('ORD_YMD', '')
        order_date = self._format_order_date(ord_ymd)

        center_nm = chit.get('CENTER_NM', '')

        mid_cd = self._get_mid_cd(
            item.get('ITEM_CD'),
            item.get('CUST_NM', ''),
            item.get('ITEM_NM', '')
        )

        delivery_type = self._determine_delivery_type(
            center_nm, item.get('ITEM_NM', ''), mid_cd
        )

        return {
            'receiving_date': receiving_date,
            'receiving_time': receiving_time,
            'chit_no': chit_no,
            'item_cd': item.get('ITEM_CD'),
            'item_nm': item.get('ITEM_NM'),
            'mid_cd': mid_cd,
            'order_date': order_date,
            'order_qty': self._to_int(item.get('ORD_QTY', 0)),
            'plan_qty': self._to_int(item.get('NAP_PLAN_QTY', 0)),
            'receiving_qty': self._to_int(item.get('NAP_QTY', 0)),
            'delivery_type': delivery_type,
            'center_nm': center_nm,
            'center_cd': item.get('CENTER_CD')
        }

    def _collect_items_via_selenium(
        self,
        chit_list: List[Dict],
        dgfw_ymd: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Selenium으로 전표별 상품 순차 수집 (기존 로직)"""
        all_receiving_data = []

        for chit_idx, chit in enumerate(chit_list):
            chit_no = chit.get('CHIT_NO')
            logger.info(f"[{chit_idx + 1}/{len(chit_list)}] 전표 {chit_no} 처리 중...")

            # 전표 선택 (rowposition 변경)
            select_result = self.driver.execute_script(f"""
                try {{
                    const app = nexacro.getApplication();
                    const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{self.FRAME_ID}.form;
                    const wf = form.div_workForm?.form;

                    const ds = wf?.dsListPopup;
                    if (!ds) return {{error: 'dsListPopup not found'}};

                    ds.set_rowposition({chit['ROW_INDEX']});

                    return {{success: true, row: {chit['ROW_INDEX']}}};
                }} catch(e) {{
                    return {{error: e.message}};
                }}
            """)

            if not select_result or not select_result.get('success'):
                logger.warning(f"전표 선택 실패: {chit_no}")
                continue

            time.sleep(RECEIVING_DATA_LOAD_WAIT)

            # dsList에서 선택된 전표의 상품 데이터 조회
            list_data = self.driver.execute_script(f"""
                try {{
                    const app = nexacro.getApplication();
                    const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{self.FRAME_ID}.form;
                    const wf = form.div_workForm?.form;

                    const ds = wf?.dsList;
                    if (!ds) return {{error: 'dsList not found'}};

                    const rows = [];
                    for (let i = 0; i < ds.getRowCount(); i++) {{
                        let wonga = ds.getColumn(i, 'ITEM_WONGA');
                        if (wonga && typeof wonga === 'object' && wonga.hi !== undefined) {{
                            wonga = wonga.hi;
                        }}

                        rows.push({{
                            CHIT_NO: ds.getColumn(i, 'CHIT_NO'),
                            CHIT_SEQ: ds.getColumn(i, 'CHIT_SEQ'),
                            ITEM_CD: ds.getColumn(i, 'ITEM_CD'),
                            ITEM_NM: ds.getColumn(i, 'ITEM_NM'),
                            CUST_NM: ds.getColumn(i, 'CUST_NM'),
                            ORD_QTY: ds.getColumn(i, 'ORD_QTY'),
                            NAP_QTY: ds.getColumn(i, 'NAP_QTY'),
                            NAP_PLAN_QTY: ds.getColumn(i, 'NAP_PLAN_QTY'),
                            ORD_UNIT_QTY: ds.getColumn(i, 'ORD_UNIT_QTY'),
                            CENTER_CD: ds.getColumn(i, 'CENTER_CD'),
                            ITEM_WONGA: wonga
                        }});
                    }}

                    return {{list: rows, count: ds.getRowCount()}};
                }} catch(e) {{
                    return {{error: e.message}};
                }}
            """)

            if not list_data or not list_data.get('list'):
                logger.warning(f"전표 {chit_no}: 상품 데이터 없음")
                continue

            item_count = len(list_data['list'])
            logger.info(f"전표 {chit_no}: {item_count}개 상품 수집")

            for item in list_data['list']:
                record = self._build_receiving_record(chit, item, dgfw_ymd)
                all_receiving_data.append(record)

        logger.info(f"총 {len(all_receiving_data)}개 상품 수집 완료")
        return all_receiving_data

    def _get_mid_cd(self, item_cd: str, cust_nm: str = "", item_nm: str = "") -> str:
        """
        중분류 코드 조회 (common.db products 테이블 우선, 실패 시 추정)

        Args:
            item_cd: 상품코드
            cust_nm: 거래처명 (fallback용)
            item_nm: 상품명 (fallback용)

        Returns:
            중분류 코드
        """
        if not item_cd:
            return self._fallback_mid_cd(cust_nm, item_nm)

        # common.db의 products 테이블에서 조회
        # (products는 매장 DB가 아닌 common.db에 있음)
        try:
            from src.infrastructure.database.connection import DBRouter
            conn = DBRouter.get_common_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT mid_cd FROM products WHERE item_cd = ?",
                    (item_cd,)
                )
                row = cursor.fetchone()

                if row and row[0]:
                    return row[0]
            finally:
                conn.close()

        except Exception as e:
            logger.debug(f"products 테이블 조회 실패 ({item_cd}): {e}")

        # products 미등록 → 신제품 후보로 축적 + mid_cd 추정
        estimated_mid = self._fallback_mid_cd(cust_nm, item_nm)
        self._new_product_candidates.append({
            "item_cd": item_cd,
            "item_nm": item_nm,
            "cust_nm": cust_nm,
            "mid_cd": estimated_mid,
            "mid_cd_source": "fallback" if estimated_mid else "unknown",
        })
        return estimated_mid

    def _fallback_mid_cd(self, cust_nm: str, item_nm: str) -> str:
        """
        중분류 코드 추정 (거래처명/상품명 기반)

        BGF 상품명 규칙: "도)" = 도시락, "주)" = 주먹밥, "김)" = 김밥,
        "샌)" = 샌드위치, "겟모닝)" = 겟모닝 시리즈 (김밥/샌드 등)

        Args:
            cust_nm: 거래처명
            item_nm: 상품명

        Returns:
            추정된 중분류 코드 (빈 문자열 가능)
        """
        cust_nm = cust_nm or ""
        item_nm = item_nm or ""

        # 상품명/거래처명 기반 추정 (약어 패턴 + 풀네임)
        if "도시락" in cust_nm or "도시락" in item_nm or item_nm.startswith("도)"):
            return "001"
        if "주먹밥" in cust_nm or "주먹밥" in item_nm or item_nm.startswith("주)"):
            return "002"
        if "김밥" in cust_nm or "김밥" in item_nm or "삼각" in item_nm or item_nm.startswith("김)"):
            return "003"
        if "샌드" in cust_nm or "샌드" in item_nm or item_nm.startswith("샌)"):
            return "004"
        if "버거" in cust_nm or "버거" in item_nm or "햄버거" in item_nm:
            return "005"
        if "빵" in cust_nm or "빵" in item_nm or "페이스트리" in item_nm:
            return "012"
        # 겟모닝 시리즈: 상품명에 김밥/샌드 키워드가 있으면 해당 카테고리
        if item_nm.startswith("겟모닝)"):
            if "김밥" in item_nm:
                return "003"
            if "샌드" in item_nm:
                return "004"
            return "001"  # 기본: 도시락 계열

        return ""

    def _get_expiration_days(self, mid_cd: str, item_cd: str = None) -> int:
        """
        유통기한 조회 (product_details 우선 → 카테고리 기본값 폴백)

        Args:
            mid_cd: 중분류 코드
            item_cd: 상품코드 (product_details 조회용)

        Returns:
            유통기한 (일)
        """
        # 1순위: product_details (다이렉트 API 실제값)
        if item_cd:
            try:
                from src.infrastructure.database.repos import ProductDetailRepository
                repo = ProductDetailRepository()
                pd = repo.get_detail(item_cd)
                if pd and pd.get('expiration_days'):
                    exp = pd['expiration_days']
                    if exp not in (9999, 999):  # 비식품 마커 제외
                        return exp
            except Exception:
                pass

        if not mid_cd:
            return DEFAULT_EXPIRY_DAYS_NON_FOOD

        # 2순위: 카테고리별 매핑에서 조회
        if mid_cd in CATEGORY_EXPIRY_DAYS:
            return CATEGORY_EXPIRY_DAYS[mid_cd]

        # 3순위: 푸드류/비푸드류 기본값
        if mid_cd in FOOD_CATEGORIES:
            return DEFAULT_EXPIRY_DAYS_FOOD
        else:
            return DEFAULT_EXPIRY_DAYS_NON_FOOD

    def _calc_expiry_datetime(
        self, recv_date: str, recv_time: str,
        mid_cd: str, delivery_type: str
    ) -> Optional[str]:
        """mid_cd + delivery_type 기반 정확한 폐기시간 계산

        FOOD_EXPIRY_CONFIG에 매핑이 있으면 차수별 정밀 시간 반환.
        없으면 None → 기존 date 기반 expiry_date 사용.

        Args:
            recv_date: 입고일 (YYYY-MM-DD)
            recv_time: 입고 시간 (HH:MM)
            mid_cd: 중분류 코드
            delivery_type: '1차'/'2차'/'ambient'

        Returns:
            'YYYY-MM-DD HH:MM:SS' 또는 None (폴백 시)
        """
        config = FOOD_EXPIRY_CONFIG.get(mid_cd, {})
        dt_config = config.get(delivery_type)

        if dt_config:
            days_offset, expiry_hour = dt_config
            recv_dt = datetime.strptime(recv_date, '%Y-%m-%d')
            expiry_dt = recv_dt + timedelta(days=days_offset)
            return expiry_dt.strftime('%Y-%m-%d') + f' {expiry_hour:02d}:00:00'

        return None

    def collect_and_save(self, dgfw_ymd: Optional[str] = None) -> Dict[str, int]:
        """
        입고 데이터 수집 및 DB 저장

        Args:
            dgfw_ymd: 입고일 (YYYYMMDD 형식)

        Returns:
            저장 통계 {"total", "new", "updated", "batches_created"}
        """
        self._new_product_candidates = []  # 매 호출마다 초기화

        data = self.collect_receiving_data(dgfw_ymd)

        if not data:
            return {"total": 0, "new": 0, "updated": 0, "batches_created": 0}

        stats = self.repo.save_bulk_receiving(data, store_id=self.store_id)

        logger.info(f"저장 완료: 총 {stats['total']}건 (신규 {stats['new']}, 업데이트 {stats['updated']})")

        # ★ 신제품 감지 및 등록 (입고 확정 상품만)
        try:
            new_product_stats = self._detect_and_register_new_products(data)
            stats.update(new_product_stats)
        except Exception as e:
            logger.warning(f"신제품 감지/등록 실패 (입고 플로우 계속): {e}")

        # 입고 데이터 기반으로 배치/추적 레코드 자동 생성
        batches_created = self._create_batches_from_receiving(data)
        stats['batches_created'] = batches_created

        if batches_created > 0:
            logger.info(f"배치 생성 완료: {batches_created}건")

        # ★ 입고 기반 재고 갱신
        stock_stats = self.update_stock_from_receiving(data)
        stats['stock_updated'] = stock_stats['updated']
        stats['stock_skipped'] = stock_stats['skipped_fresh'] + stock_stats['skipped_no_data']

        if stock_stats['updated'] > 0:
            logger.info(f"재고 갱신: {stock_stats['updated']}건 "
                        f"(스킵: 최신={stock_stats['skipped_fresh']}, "
                        f"미등록={stock_stats['skipped_no_data']})")

        # ★ 발주 분석: 입고 데이터와 자동발주 스냅샷 비교
        try:
            from src.analysis.order_diff_tracker import OrderDiffTracker
            diff_tracker = OrderDiffTracker(store_id=self.store_id)
            order_dates = {r['order_date'] for r in data if r.get('order_date')}
            for od in order_dates:
                od_data = [r for r in data if r.get('order_date') == od]
                diff_result = diff_tracker.compare_and_save(
                    order_date=od, receiving_data=od_data
                )
                if diff_result and diff_result.get('diffs'):
                    s = diff_result['summary']
                    logger.info(
                        f"[발주분석] {od}: "
                        f"변경={s.get('items_qty_changed', 0)}, "
                        f"추가={s.get('items_added', 0)}, "
                        f"제거={s.get('items_removed', 0)}"
                    )
        except Exception as e:
            logger.debug(f"[발주분석] 차이 분석 실패 (무시): {e}")

        return stats

    def update_stock_from_receiving(self, receiving_data: List[Dict]) -> Dict[str, int]:
        """
        입고 데이터 기반 realtime_inventory.stock_qty 갱신

        입고 확정된 상품의 재고를 무조건 반영한다.
        Phase 2 prefetch가 BGF 실재고로 덮어쓰므로 이중 반영 우려 없음.

        Args:
            receiving_data: collect_receiving_data() 반환값

        Returns:
            {"updated": N, "skipped_no_data": N}
        """
        inventory_repo = RealtimeInventoryRepository(store_id=self.store_id)

        stats = {"updated": 0, "skipped_fresh": 0, "skipped_no_data": 0, "pending_set": 0}

        store_id = self.store_id or DEFAULT_STORE_ID

        # 같은 상품이 여러 전표에 있을 수 있으므로 상품별 합산
        item_receiving_qty: Dict[str, int] = {}
        item_pending_qty: Dict[str, int] = {}  # 검수 미확정 → 미입고
        for record in receiving_data:
            item_cd = record.get('item_cd')
            if not item_cd:
                continue
            recv_qty = self._to_int(record.get('receiving_qty', 0))
            plan_qty = self._to_int(record.get('plan_qty', 0))

            if recv_qty > 0:
                # 검수 확정 상품 → 재고 반영
                item_receiving_qty[item_cd] = item_receiving_qty.get(item_cd, 0) + recv_qty
            elif plan_qty > 0:
                # 검수 미확정 + 납품예정 있음 → 미입고
                item_pending_qty[item_cd] = item_pending_qty.get(item_cd, 0) + plan_qty

        # 1) 검수 확정 상품: stock_qty 갱신 (항상 반영)
        for item_cd, total_recv_qty in item_receiving_qty.items():
            try:
                current = inventory_repo.get(item_cd, store_id=store_id)

                if not current:
                    stats["skipped_no_data"] += 1
                    continue

                # 재고 갱신: stock_qty += receiving_qty
                new_stock = current.get("stock_qty", 0) + total_recv_qty

                # pending_qty 차감 (0 이하 방지)
                new_pending = max(0, current.get("pending_qty", 0) - total_recv_qty)

                inventory_repo.save(
                    item_cd=item_cd,
                    stock_qty=new_stock,
                    pending_qty=new_pending,
                    order_unit_qty=current.get("order_unit_qty", 1),
                    is_available=current.get("is_available", True),
                    item_nm=current.get("item_nm"),
                    is_cut_item=current.get("is_cut_item", False),
                    store_id=store_id,
                )

                logger.debug(
                    f"재고 갱신: {item_cd} stock {current.get('stock_qty', 0)}→{new_stock} "
                    f"(+{total_recv_qty}), pending {current.get('pending_qty', 0)}→{new_pending}"
                )
                stats["updated"] += 1

            except Exception as e:
                logger.warning(f"재고 갱신 실패 ({item_cd}): {e}")

        # 2) 검수 미확정 상품: pending_qty 설정 (미입고 상태)
        for item_cd, plan_total in item_pending_qty.items():
            if item_cd in item_receiving_qty:
                continue  # 이미 확정 처리된 상품은 스킵
            try:
                current = inventory_repo.get(item_cd, store_id=store_id)
                if not current:
                    stats["skipped_no_data"] += 1
                    continue

                # pending_qty가 이미 plan_total 이상이면 스킵
                cur_pending = current.get("pending_qty", 0)
                if cur_pending >= plan_total:
                    continue

                inventory_repo.save(
                    item_cd=item_cd,
                    stock_qty=current.get("stock_qty", 0),
                    pending_qty=plan_total,
                    order_unit_qty=current.get("order_unit_qty", 1),
                    is_available=current.get("is_available", True),
                    item_nm=current.get("item_nm"),
                    is_cut_item=current.get("is_cut_item", False),
                    store_id=store_id,
                )
                logger.debug(f"미입고 설정: {item_cd} pending {cur_pending}→{plan_total}")
                stats["pending_set"] += 1

            except Exception as e:
                logger.warning(f"미입고 설정 실패 ({item_cd}): {e}")

        logger.info(
            f"재고 갱신: {stats['updated']}건 (스킵: 최신={stats['skipped_fresh']}, "
            f"미등록={stats['skipped_no_data']}), 미입고 설정: {stats['pending_set']}건"
        )
        return stats

    def _create_batches_from_receiving(self, receiving_data: List[Dict]) -> int:
        """
        입고 데이터로부터 배치/추적 레코드 생성

        - 푸드류(001-005): order_tracking 갱신/생성 + inventory_batches 생성 (폐기 역추적용)
        - 비푸드류: inventory_batches 생성

        Args:
            receiving_data: 입고 데이터 리스트

        Returns:
            생성된 배치 수
        """
        tracking_repo = OrderTrackingRepository(store_id=self.store_id)
        batch_repo = InventoryBatchRepository(store_id=self.store_id)
        created_count = 0

        try:
            for record in receiving_data:
                item_cd = record.get('item_cd')
                item_nm = record.get('item_nm')
                mid_cd = record.get('mid_cd', '')
                recv_date = record.get('receiving_date')
                recv_time = record.get('receiving_time')
                recv_qty = self._to_int(record.get('receiving_qty', 0))
                order_date = record.get('order_date')

                # 필수 데이터 누락 시 명확한 로깅
                if not all([item_cd, recv_date, recv_qty]):
                    logger.error(
                        f"배치 생성 스킵 (필수 데이터 누락): "
                        f"item_cd={item_cd}, item_nm={item_nm}, "
                        f"recv_date={recv_date}, recv_qty={recv_qty}"
                    )
                    continue

                # order_date 정규화: 빈값이면 recv_date로 대체 (조회 전!)
                # NULL = NULL은 SQLite에서 FALSE → 빈 order_date로 조회 시 항상 miss → 중복 생성 방지
                effective_order_date = order_date if order_date else recv_date

                # 유통기한 조회 (product_details 우선 → 카테고리 폴백)
                expiration_days = self._get_expiration_days(mid_cd, item_cd=item_cd)

                # 1차/2차 판별
                center_nm = record.get('center_nm', '')
                delivery_type = self._determine_delivery_type(
                    center_nm, item_nm, mid_cd
                )

                # 정확한 폐기시간 계산 (차수별 시간 반영)
                expiry_datetime = self._calc_expiry_datetime(
                    recv_date, recv_time or '', mid_cd, delivery_type
                )

                # 푸드류 처리: order_tracking + inventory_batches 모두 생성
                if mid_cd in FOOD_CATEGORIES:
                    # 1) order_tracking 갱신 또는 생성
                    try:
                        # 해당 발주건 찾기 (store_id 포함)
                        existing = tracking_repo.get_by_item_and_order_date(
                            item_cd, effective_order_date, store_id=self.store_id
                        )

                        if existing:
                            # 기존 레코드 업데이트
                            arrival_time = f"{recv_date} {recv_time}" if recv_time else recv_date
                            tracking_repo.update_receiving(
                                tracking_id=existing['id'],
                                receiving_qty=recv_qty,
                                arrival_time=arrival_time
                            )
                            logger.debug(f"order_tracking 업데이트: {item_nm} (발주일: {effective_order_date})")
                        else:
                            # 새 레코드 생성 (수동 발주 또는 누락된 자동 발주)
                            arrival_time = f"{recv_date} {recv_time}" if recv_time else recv_date

                            # 차수별 정밀 폐기시간 사용, 없으면 기존 방식
                            if expiry_datetime:
                                expiry_time = expiry_datetime
                            else:
                                recv_dt = datetime.strptime(recv_date, '%Y-%m-%d')
                                expiry_dt = recv_dt + timedelta(days=expiration_days)
                                expiry_time = expiry_dt.strftime('%Y-%m-%d 23:59:59')

                            tracking_repo.save_order(
                                order_date=effective_order_date,
                                item_cd=item_cd,
                                item_nm=item_nm,
                                mid_cd=mid_cd,
                                delivery_type=delivery_type,
                                order_qty=recv_qty,
                                arrival_time=arrival_time,
                                expiry_time=expiry_time,
                                store_id=self.store_id,
                                order_source='receiving'
                            )
                            logger.debug(f"order_tracking 생성: {item_nm} ({delivery_type}, 입고: {recv_date})")

                    except Exception as e:
                        logger.warning(f"order_tracking 처리 실패 ({item_nm}): {e}")

                    # 2) inventory_batches도 생성 (폐기 역추적용)
                    try:
                        existing_batch = batch_repo.get_batch_by_item_and_date(
                            item_cd=item_cd,
                            receiving_date=recv_date,
                            store_id=self.store_id
                        )
                        if not existing_batch:
                            batch_repo.create_batch(
                                item_cd=item_cd,
                                item_nm=item_nm,
                                mid_cd=mid_cd,
                                receiving_date=recv_date,
                                expiration_days=expiration_days,
                                initial_qty=recv_qty,
                                store_id=self.store_id,
                                delivery_type=delivery_type,
                                expiry_datetime=expiry_datetime,
                            )
                            created_count += 1
                            logger.debug(f"inventory_batches 생성 (푸드): {item_nm} ({delivery_type}, 입고: {recv_date}, {recv_qty}개)")
                    except Exception as e:
                        logger.warning(f"inventory_batches 생성 실패 (푸드 {item_nm}): {e}")

                # 비푸드류 처리: inventory_batches만 생성
                else:
                    try:
                        # 중복 배치 확인
                        existing_batch = batch_repo.get_batch_by_item_and_date(
                            item_cd=item_cd,
                            receiving_date=recv_date,
                            store_id=self.store_id
                        )
                        if existing_batch:
                            logger.debug(f"배치 이미 존재: {item_nm} ({recv_date}) — 스킵")
                            continue

                        # inventory_batches 생성
                        batch_repo.create_batch(
                            item_cd=item_cd,
                            item_nm=item_nm,
                            mid_cd=mid_cd,
                            receiving_date=recv_date,
                            expiration_days=expiration_days,
                            initial_qty=recv_qty,
                            store_id=self.store_id,
                            delivery_type=delivery_type,
                        )

                        created_count += 1
                        logger.debug(f"inventory_batches 생성: {item_nm} ({delivery_type}, 입고: {recv_date}, {recv_qty}개)")

                    except Exception as e:
                        logger.warning(f"inventory_batches 생성 실패 ({item_nm}): {e}")

        except Exception as e:
            logger.error(f"배치 생성 중 오류: {e}")

        return created_count

    def collect_multiple_dates(
        self,
        dates: Optional[List[str]] = None,
        days: int = 7,
        use_all_available: bool = False
    ) -> Dict[str, Any]:
        """
        여러 날짜의 입고 데이터 수집

        Args:
            dates: 수집할 날짜 목록 (YYYYMMDD 형식)
            days: dates가 None일 때 최근 N일 수집 (기본값 7일로 증가)
            use_all_available: True면 조회 가능한 모든 날짜 수집

        Returns:
            수집 결과 {날짜: stats, ...}
        """
        if dates is None:
            if use_all_available:
                # 조회 가능한 모든 날짜 수집
                available_dates_data = self.get_available_dates()
                dates = [d['DGFW_YMD'] for d in available_dates_data]
                logger.info(f"조회 가능한 전체 날짜 수집: {len(dates)}일")
            else:
                # 최근 N일 날짜 생성
                dates = []
                for i in range(days):
                    d = datetime.now() - timedelta(days=i)
                    dates.append(d.strftime("%Y%m%d"))

        results = {}

        for dgfw_ymd in dates:
            logger.info(f"수집: {dgfw_ymd}...")

            try:
                stats = self.collect_and_save(dgfw_ymd)
                results[dgfw_ymd] = {
                    "success": True,
                    "stats": stats
                }
            except Exception as e:
                logger.error(f"{dgfw_ymd} 수집 실패: {e}")
                results[dgfw_ymd] = {
                    "success": False,
                    "error": str(e)
                }

            time.sleep(0.5)

        return results

    def update_order_tracking(self, receiving_date: Optional[str] = None) -> int:
        """
        입고 데이터로 order_tracking 업데이트

        Args:
            receiving_date: 입고일 (YYYY-MM-DD)

        Returns:
            업데이트된 건수
        """
        if receiving_date is None:
            receiving_date = datetime.now().strftime("%Y-%m-%d")

        # 해당 날짜 입고 데이터 조회
        receiving_list = self.repo.get_receiving_by_date(
            receiving_date, store_id=self.store_id
        )

        updated_count = 0

        for item in receiving_list:
            item_cd = item.get('item_cd')
            order_date = item.get('order_date')
            receiving_qty = self._to_int(item.get('receiving_qty', 0))
            receiving_time = item.get('receiving_time')

            if not item_cd or not order_date:
                continue

            # order_tracking 업데이트
            arrival_time = f"{receiving_date} {receiving_time}" if receiving_time else None
            updated = self.repo.update_order_tracking_receiving(
                item_cd=item_cd,
                order_date=order_date,
                receiving_qty=receiving_qty,
                arrival_time=arrival_time,
                store_id=self.store_id
            )

            updated_count += updated

        logger.info(f"order_tracking 업데이트: {updated_count}건")
        return updated_count

    # ═══════════════════════════════════════════════════════
    # 신제품 감지 및 등록
    # ═══════════════════════════════════════════════════════

    def _detect_and_register_new_products(self, receiving_data: List[Dict]) -> Dict[str, int]:
        """입고 확정 상품 중 DB 미등록 상품을 신제품으로 감지 및 등록

        핵심 규칙:
        - receiving_qty > 0 (입고 확정)인 후보만 신제품으로 확정
        - plan_qty만 있는 미확정(입고 예정) 상품은 무시

        Args:
            receiving_data: collect_receiving_data() 반환값

        Returns:
            {"new_products_detected": N, "new_products_registered": N}
        """
        stats = {"new_products_detected": 0, "new_products_registered": 0}

        if not self._new_product_candidates:
            return stats

        # 입고 확정된 item_cd → 해당 레코드 매핑
        confirmed_items: Dict[str, Dict] = {}
        for record in receiving_data:
            item_cd = record.get("item_cd")
            recv_qty = self._to_int(record.get("receiving_qty", 0))
            if item_cd and recv_qty > 0:
                if item_cd not in confirmed_items:
                    confirmed_items[item_cd] = record

        # 후보 중 입고 확정 + products 미등록만 필터
        new_products: List[Dict] = []
        seen: set = set()
        for candidate in self._new_product_candidates:
            item_cd = candidate["item_cd"]
            if item_cd in seen:
                continue
            if item_cd not in confirmed_items:
                continue  # 입고 예정/미확정 → 무시
            seen.add(item_cd)
            # 입고 데이터 병합
            recv_record = confirmed_items[item_cd]
            candidate["receiving_qty"] = self._to_int(recv_record.get("receiving_qty", 0))
            candidate["receiving_date"] = recv_record.get("receiving_date")
            candidate["order_unit_qty"] = self._to_int(recv_record.get("order_unit_qty", 1)) or 1
            candidate["center_cd"] = recv_record.get("center_cd")
            candidate["center_nm"] = recv_record.get("center_nm")
            new_products.append(candidate)

        if not new_products:
            return stats

        stats["new_products_detected"] = len(new_products)
        logger.info(f"[신제품 감지] {len(new_products)}건 (입고 확정 기준)")

        # 일괄 등록
        for product in new_products:
            try:
                self._register_single_new_product(product)
                stats["new_products_registered"] += 1
            except Exception as e:
                logger.warning(f"신제품 등록 실패 ({product['item_cd']}): {e}")

        logger.info(
            f"[신제품 등록] {stats['new_products_registered']}/{stats['new_products_detected']}건 완료"
        )
        return stats

    def _get_expiry_days(self, mid_cd: str) -> int:
        """mid_cd 기반 유통기한 추정"""
        from src.settings.constants import (
            CATEGORY_EXPIRY_DAYS, DEFAULT_EXPIRY_DAYS_FOOD,
            DEFAULT_EXPIRY_DAYS_NON_FOOD, FOOD_CATEGORIES,
        )
        if mid_cd and mid_cd in CATEGORY_EXPIRY_DAYS:
            return CATEGORY_EXPIRY_DAYS[mid_cd]
        if mid_cd and mid_cd in FOOD_CATEGORIES:
            return DEFAULT_EXPIRY_DAYS_FOOD
        return DEFAULT_EXPIRY_DAYS_NON_FOOD

    def _register_single_new_product(self, product: Dict) -> None:
        """신제품 1건을 products + product_details + realtime_inventory에 등록

        Args:
            product: 신제품 후보 정보
        """
        item_cd = product["item_cd"]
        item_nm = product.get("item_nm", "")
        mid_cd = product.get("mid_cd", "")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        store_id = self.store_id or DEFAULT_STORE_ID

        registered = {"products": False, "details": False, "inventory": False}

        # 1) products 테이블 (common.db)
        try:
            from src.infrastructure.database.connection import DBRouter
            conn = DBRouter.get_common_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT OR IGNORE INTO products (item_cd, item_nm, mid_cd, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (item_cd, item_nm, mid_cd or "999", now, now)
                )
                conn.commit()
                registered["products"] = cursor.rowcount > 0
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"products 등록 실패 ({item_cd}): {e}")

        # 2) product_details 테이블 (common.db) — 기본값
        try:
            from src.infrastructure.database.repos import ProductDetailRepository
            detail_repo = ProductDetailRepository()
            expiry_days = self._get_expiry_days(mid_cd)
            detail_repo.save(item_cd, {
                "item_nm": item_nm,
                "expiration_days": expiry_days,
                "order_unit_qty": product.get("order_unit_qty", 1),
            })
            registered["details"] = True
        except Exception as e:
            logger.warning(f"product_details 등록 실패 ({item_cd}): {e}")

        # 3) realtime_inventory (store DB) — 입고 수량으로 초기 재고
        try:
            inventory_repo = RealtimeInventoryRepository(store_id=self.store_id)
            inventory_repo.save(
                item_cd=item_cd,
                stock_qty=product.get("receiving_qty", 0),
                pending_qty=0,
                order_unit_qty=product.get("order_unit_qty", 1),
                is_available=True,
                item_nm=item_nm,
                store_id=store_id,
            )
            registered["inventory"] = True
        except Exception as e:
            logger.warning(f"realtime_inventory 등록 실패 ({item_cd}): {e}")

        # 4) detected_new_products (store DB) — 이력 기록
        try:
            from src.infrastructure.database.repos import DetectedNewProductRepository
            detect_repo = DetectedNewProductRepository(store_id=self.store_id)
            detect_repo.save(
                item_cd=item_cd,
                item_nm=item_nm,
                mid_cd=mid_cd,
                mid_cd_source=product.get("mid_cd_source", "unknown"),
                first_receiving_date=product.get("receiving_date", now[:10]),
                receiving_qty=product.get("receiving_qty", 0),
                order_unit_qty=product.get("order_unit_qty", 1),
                center_cd=product.get("center_cd"),
                center_nm=product.get("center_nm"),
                cust_nm=product.get("cust_nm"),
                registered_to_products=registered["products"],
                registered_to_details=registered["details"],
                registered_to_inventory=registered["inventory"],
                store_id=store_id,
            )
        except Exception as e:
            logger.warning(f"detected_new_products 기록 실패 ({item_cd}): {e}")

        logger.info(
            f"[신제품] {item_cd} ({item_nm}) mid_cd={mid_cd}: "
            f"products={'O' if registered['products'] else 'X'}, "
            f"details={'O' if registered['details'] else 'X'}, "
            f"inventory={'O' if registered['inventory'] else 'X'}"
        )

    def close_receiving_menu(self) -> bool:
        """센터매입 조회/확정 메뉴 탭 닫기

        Returns:
            탭 닫기 성공 여부
        """
        if not self.driver:
            return False

        try:
            success = close_tab_by_frame_id(self.driver, self.FRAME_ID)
            if success:
                logger.info("센터매입 탭 닫기 완료")
                time.sleep(0.5)
            else:
                logger.warning("센터매입 탭 닫기 버튼을 찾지 못함")
            return success

        except Exception as e:
            logger.error(f"탭 닫기 오류: {e}")
            return False


def run_receiving_collection(
    driver: Any,
    days: int = 7,
    use_all_available: bool = False,
    store_id: Optional[str] = None
) -> None:
    """
    입고 데이터 수집 실행

    Args:
        driver: 로그인된 WebDriver
        days: 수집할 일수 (기본값 7일로 증가)
        use_all_available: True면 조회 가능한 모든 날짜 수집
        store_id: 매장 ID
    """
    collector = ReceivingCollector(driver, store_id=store_id)

    # 메뉴 이동
    if not collector.navigate_to_receiving_menu():
        logger.error("메뉴 이동 실패")
        return

    # 조회 가능한 날짜 확인
    available_dates = collector.get_available_dates()
    logger.info(f"조회 가능 날짜: {len(available_dates)}개")

    if available_dates:
        if use_all_available:
            # 모든 날짜 수집
            target_dates = [d['DGFW_YMD'] for d in available_dates]
            logger.info(f"전체 날짜 수집: {len(target_dates)}일")
        else:
            # 최근 N일 수집
            target_dates = [d['DGFW_YMD'] for d in available_dates[:days]]
            logger.info(f"최근 {days}일 수집: {target_dates}")

        results = collector.collect_multiple_dates(target_dates)

        # 결과 출력
        for date, result in results.items():
            if result.get('success'):
                stats = result.get('stats', {})
                logger.info(f"수집 결과 - {date}: 총 {stats.get('total', 0)}건")
            else:
                logger.error(f"수집 결과 - {date}: 실패 - {result.get('error')}")


if __name__ == "__main__":
    print("이 모듈은 직접 실행할 수 없습니다.")
    print("드라이버를 전달하여 run_receiving_collection(driver)를 호출하세요.")
