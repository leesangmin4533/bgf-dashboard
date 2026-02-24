"""
상품 상세 정보 수집기
- BGF 상품조회 화면에서 상품 상세 팝업 열어서 정보 조회
- 유통기한, 발주가능요일, 입수개수(배수) 등 수집
- DB에 캐싱

플로우:
1. 발주 목록의 상품 코드들 확인
2. DB에 상세정보가 없는 상품 필터링
3. 상품조회 메뉴로 이동
4. 상품코드 입력 -> 조회 -> 상세정보 팝업 열기
5. 유통기한, 발주요일, 입수개수 추출 -> DB 저장
6. 반복
"""

import time
from datetime import datetime
from typing import Dict, Any, Optional, List
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.infrastructure.database.connection import get_connection
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ProductInfoCollector:
    """상품 상세 정보 수집기"""

    # 메뉴 ID
    MENU_PRODUCT = "mainframe.HFrameSet00.VFrameSet00.TopFrame.form.div_topMenu.form.STMB000_M0:icontext"  # 상품 메뉴
    SUBMENU_PRODUCT_SEARCH = "상품조회"  # 서브메뉴 텍스트

    def __init__(self, driver: Optional[Any] = None) -> None:
        self.driver = driver
        self._in_product_search_screen: bool = False

    def set_driver(self, driver: Any) -> None:
        """드라이버 설정

        Args:
            driver: Selenium WebDriver 인스턴스
        """
        self.driver = driver

    def get_from_db(self, item_cd: str) -> Optional[Dict[str, Any]]:
        """DB에서 상품 상세 정보 조회

        Args:
            item_cd: 상품코드

        Returns:
            상품 상세 정보 딕셔너리 또는 None
        """
        conn = get_connection()
        try:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT item_cd, item_nm, expiration_days, orderable_day, orderable_status,
                       order_unit_name, order_unit_qty, case_unit_qty, fetched_at,
                       promo_type, promo_name, promo_start, promo_end, promo_updated
                FROM product_details
                WHERE item_cd = ?
            """, (item_cd,))

            row = cursor.fetchone()
        finally:
            conn.close()

        if row:
            return {
                "item_cd": row["item_cd"],
                "item_nm": row["item_nm"],
                "expiration_days": row["expiration_days"],
                "orderable_day": row["orderable_day"],
                "orderable_status": row["orderable_status"],
                "order_unit_name": row["order_unit_name"],
                "order_unit_qty": row["order_unit_qty"],
                "case_unit_qty": row["case_unit_qty"],
                "fetched_at": row["fetched_at"],
                "promo_type": row["promo_type"],
                "promo_name": row["promo_name"],
                "promo_start": row["promo_start"],
                "promo_end": row["promo_end"],
                "promo_updated": row["promo_updated"]
            }
        return None

    def save_to_db(self, item_cd: str, data: Dict[str, Any]) -> bool:
        """상품 상세 정보 DB 저장

        Args:
            item_cd: 상품코드
            data: 저장할 상품 상세 정보 딕셔너리

        Returns:
            저장 성공 여부
        """
        now = datetime.now().isoformat()
        conn = get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT OR REPLACE INTO product_details
                (item_cd, item_nm, expiration_days, orderable_day, orderable_status,
                 order_unit_name, order_unit_qty, case_unit_qty, fetched_at, created_at, updated_at,
                 promo_type, promo_name, promo_start, promo_end, promo_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item_cd,
                data.get("item_nm"),
                data.get("expiration_days"),
                data.get("orderable_day", "일월화수목금토"),
                data.get("orderable_status"),
                data.get("order_unit_name"),
                data.get("order_unit_qty", 1),
                data.get("case_unit_qty", 1),
                now,
                now,
                now,
                data.get("promo_type"),
                data.get("promo_name"),
                data.get("promo_start"),
                data.get("promo_end"),
                now if data.get("promo_type") else None  # 행사 정보가 있을 때만 업데이트 시간 기록
            ))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"DB 저장 실패: {e}")
            return False
        finally:
            conn.close()

    def fetch_from_bgf(self, item_cd: str) -> Optional[Dict[str, Any]]:
        """BGF 시스템에서 상품 상세 정보 조회 (상품조회 화면)

        Args:
            item_cd: 상품코드

        Returns:
            상품 상세 정보 딕셔너리 또는 None
        """
        if not self.driver:
            logger.error("WebDriver가 없습니다")
            return None

        try:
            logger.info(f"상품 상세 조회: {item_cd}")

            # 상품조회 화면(STMB011)에서 상품 상세 팝업 열어서 정보 가져오기
            result = self.driver.execute_script(f"""
                const productCode = "{item_cd}";
                const result = {{ success: false, data: null }};

                try {{
                    const app = nexacro.getApplication?.();
                    if (!app) return {{ success: false, message: "app not found" }};

                    // 현재 화면의 데이터셋에서 정보 찾기 (단품별발주 화면에서)
                    const stbjForm = app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet?.STBJ030_M0?.form;
                    const workForm = stbjForm?.div_workForm?.form?.div_work_01?.form;

                    if (workForm?.gdList?._binddataset) {{
                        const ds = workForm.gdList._binddataset;
                        const rows = ds.getRowCount();

                        // 마지막 행(방금 입력한 상품) 데이터 읽기
                        if (rows > 0) {{
                            const lastRow = rows - 1;

                            // 컬럼명 목록에서 데이터 추출
                            const colNames = [];
                            for (let i = 0; i < ds.colcount; i++) {{
                                try {{ colNames.push(ds.getColID(i)); }} catch(e) {{}}
                            }}

                            const data = {{}};
                            for (const col of colNames) {{
                                try {{
                                    data[col] = ds.getColumn(lastRow, col);
                                }} catch(e) {{}}
                            }}

                            // 필요한 필드 매핑
                            result.data = {{
                                item_nm: data.ITEM_NM || data.item_nm || '',
                                expiration_days: data.EXPIRE_DAY || data.VAL_TERM || data.FRESH_TERM || null,
                                orderable_day: data.ORD_ADAY || data.ORD_DAY || '일월화수목금토',
                                orderable_status: data.ORD_STAT_NM || data.ORD_GB_NM || '',
                                order_unit_name: data.ORD_UNIT_NM || data.UNIT_NM || '',
                                order_unit_qty: data.IN_QTY || data.ORD_UNIT_QTY || 1,
                                case_unit_qty: data.CASE_QTY || data.CASE_UNIT_QTY || 1,
                                raw_data: data
                            }};
                            result.success = true;
                        }}
                    }}

                    return result;

                }} catch(e) {{
                    return {{ success: false, message: e.toString() }};
                }}
            """)

            if result and result.get("success"):
                data = result.get("data", {})
                logger.info(f"상품명: {data.get('item_nm')}, 발주가능요일: {data.get('orderable_day')}")
                return data
            else:
                logger.warning(f"조회 실패: {result.get('message', 'unknown')}")
                return None

        except Exception as e:
            logger.error(f"BGF 조회 오류: {e}")
            return None

    def fetch_from_grid_after_input(self, row_index: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        상품 입력 후 그리드에서 상세 정보 읽기
        (발주 화면에서 상품코드 입력 후 Enter 친 다음 호출)

        Args:
            row_index: 읽을 행 인덱스 (None이면 마지막 행)

        Returns:
            상품 상세 정보 딕셔너리 또는 None
        """
        if not self.driver:
            return None

        try:
            result = self.driver.execute_script(f"""
                const targetRow = {row_index if row_index is not None else 'null'};
                const app = nexacro.getApplication?.();
                const stbjForm = app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet?.STBJ030_M0?.form;
                const workForm = stbjForm?.div_workForm?.form?.div_work_01?.form;

                if (!workForm?.gdList?._binddataset) {{
                    return {{ success: false, message: "dataset not found" }};
                }}

                const ds = workForm.gdList._binddataset;
                const rows = ds.getRowCount();

                if (rows === 0) {{
                    return {{ success: false, message: "no rows" }};
                }}

                // 대상 행 결정 (지정되지 않으면 마지막 행)
                const row = (targetRow !== null && targetRow >= 0) ? targetRow : rows - 1;

                // 모든 컬럼 읽기
                const data = {{}};
                const colNames = [];
                const colCount = ds.colcount || 50;
                for (let i = 0; i < colCount; i++) {{
                    try {{
                        const colId = ds.getColID(i);
                        colNames.push(colId);
                        data[colId] = ds.getColumn(row, colId);
                    }} catch(e) {{}}
                }}

                // 배수(IN_QTY) 컬럼 찾기 - 여러 가능한 이름 시도
                let orderUnitQty = 1;
                const qtyColNames = ['IN_QTY', 'ORD_UNIT_QTY', 'BAESOO', 'MUL_QTY', 'UNIT_QTY', 'MIN_ORD_QTY'];
                for (const col of qtyColNames) {{
                    if (data[col] && parseInt(data[col]) > 0) {{
                        orderUnitQty = parseInt(data[col]);
                        break;
                    }}
                }}

                return {{
                    success: true,
                    row: row,
                    colNames: colNames,
                    data: {{
                        item_cd: data.ITEM_CD || data.PLU_CD || '',
                        item_nm: data.ITEM_NM || '',
                        expiration_days: parseInt(data.EXPIRE_DAY || data.VAL_TERM || data.FRESH_TERM) || null,
                        orderable_day: data.ORD_ADAY || data.ORD_DAY || '일월화수목금토',
                        orderable_status: data.ORD_STAT_NM || '',
                        order_unit_name: data.ORD_UNIT_NM || '',
                        order_unit_qty: orderUnitQty,
                        case_unit_qty: parseInt(data.CASE_QTY) || 1
                    }},
                    raw: data
                }};
            """)

            if result and result.get("success"):
                return result.get("data")
            return None

        except Exception as e:
            logger.error(f"그리드 데이터 읽기 실패: {e}")
            return None

    def get_product_info(self, item_cd: str, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        """
        상품 상세 정보 조회 (캐시 우선, 없으면 BGF 조회)

        Args:
            item_cd: 상품 코드
            force_refresh: True면 DB 캐시 무시하고 BGF에서 새로 조회

        Returns:
            상품 상세 정보 dict 또는 None
        """
        # 1. DB 캐시 확인
        if not force_refresh:
            cached = self.get_from_db(item_cd)
            if cached:
                logger.info(f"[CACHE] {item_cd}: {cached.get('item_nm')}")
                return cached

        # 2. BGF에서 조회
        if self.driver:
            data = self.fetch_from_bgf(item_cd)
            if data:
                # 3. DB에 저장
                self.save_to_db(item_cd, data)
                data["item_cd"] = item_cd
                return data

        return None

    def collect_after_product_input(self, item_cd: str) -> bool:
        """
        상품 입력 후 그리드에서 정보 수집하여 DB 저장

        Args:
            item_cd: 상품 코드

        Returns:
            성공 여부
        """
        # DB에 이미 있으면 스킵
        if self.get_from_db(item_cd):
            return True

        # 그리드에서 정보 읽기
        data = self.fetch_from_grid_after_input()
        if data and data.get("item_nm"):
            self.save_to_db(item_cd, data)
            logger.info(f"[DB 저장] {item_cd}: {data.get('item_nm')}, 발주가능요일={data.get('orderable_day')}")
            return True

        return False

    def collect_product_infos(self, item_codes: List[str], force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        """
        여러 상품의 상세 정보 수집

        Args:
            item_codes: 상품 코드 리스트
            force_refresh: True면 모두 새로 조회

        Returns:
            {item_cd: {info}, ...}
        """
        results = {}

        logger.info(f"상품 정보 수집: {len(item_codes)}개 상품")

        for i, item_cd in enumerate(item_codes):
            logger.info(f"[{i+1}/{len(item_codes)}] {item_cd}")
            info = self.get_product_info(item_cd, force_refresh)
            if info:
                results[item_cd] = info

            # BGF 조회 시 딜레이
            if self.driver and (force_refresh or not self.get_from_db(item_cd)):
                time.sleep(1)

        logger.info(f"수집 성공: {len(results)}건")
        return results

    # =========================================================================
    # 상품조회 화면에서 상세정보 수집 (발주 전 사전 수집)
    # =========================================================================

    def get_items_without_details(self, item_codes: List[str]) -> List[str]:
        """
        DB에 상세정보가 없는 상품 코드 목록 반환

        Args:
            item_codes: 확인할 상품 코드 리스트

        Returns:
            상세정보가 없는 상품 코드 리스트
        """
        missing = []
        for item_cd in item_codes:
            cached = self.get_from_db(item_cd)
            # 입수개수(order_unit_qty)가 없거나 1인 경우 재조회 필요
            if not cached or not cached.get("order_unit_qty") or cached.get("order_unit_qty") == 1:
                # order_unit_qty가 1이면 실제로 1인지 아직 조회 안된건지 확인 필요
                # fetched_at이 없으면 아직 BGF에서 조회 안함
                if not cached or not cached.get("fetched_at"):
                    missing.append(item_cd)
        return missing

    def navigate_to_product_search(self) -> bool:
        """
        상품 > 상품조회 메뉴로 이동

        Returns:
            성공 여부
        """
        if not self.driver:
            logger.error("WebDriver가 없습니다")
            return False

        logger.info("상품조회 메뉴로 이동...")

        try:
            # 1. 상품 메뉴 클릭 (매출분석 옆에 있는 상품 메뉴)
            # 실제 메뉴 ID는 STMD (상품마스터) 또는 STIT (상품)일 수 있음
            result = self.driver.execute_script("""
                function clickByText(targetText) {
                    // 상단 메뉴에서 텍스트로 찾기
                    const menuElements = document.querySelectorAll('[id*="pdiv_topMenu"] [id*=":text"], [id*="div_topMenu"] [id*=":text"], [id*=":icontext"]');

                    for (const el of menuElements) {
                        const text = (el.innerText || '').trim();
                        if (text === targetText && el.offsetParent !== null) {
                            el.scrollIntoView({block: 'center'});
                            const r = el.getBoundingClientRect();
                            const o = {
                                bubbles: true, cancelable: true, view: window,
                                clientX: r.left + r.width/2, clientY: r.top + r.height/2
                            };
                            el.dispatchEvent(new MouseEvent('mousedown', o));
                            el.dispatchEvent(new MouseEvent('mouseup', o));
                            el.dispatchEvent(new MouseEvent('click', o));
                            return { success: true, id: el.id, text: text };
                        }
                    }
                    return { success: false, message: 'menu not found: ' + targetText };
                }

                // "상품" 또는 "상품관리" 메뉴 찾기
                let result = clickByText('상품');
                if (!result.success) {
                    result = clickByText('상품관리');
                }
                if (!result.success) {
                    result = clickByText('마스터');
                }
                return result;
            """)

            if not result.get('success'):
                logger.warning(f"상품 메뉴 클릭 실패: {result.get('message')}")
                # 직접 발주 메뉴에서 상품 조회하는 방식으로 대체
                return self._navigate_via_order_menu()

            logger.info(f"상품 메뉴 클릭: {result.get('text')}")
            time.sleep(1)

            # 2. 상품조회 서브메뉴 클릭
            result = self.driver.execute_script("""
                function clickSubmenu(targetText) {
                    // 서브메뉴에서 텍스트로 찾기
                    const allElements = document.querySelectorAll('[id*="pdiv_topMenu"] *');

                    for (const el of allElements) {
                        const text = (el.innerText || '').trim();
                        if (text === targetText && el.offsetParent !== null && el.children.length <= 1) {
                            el.scrollIntoView({block: 'center'});
                            const r = el.getBoundingClientRect();
                            const o = {
                                bubbles: true, cancelable: true, view: window,
                                clientX: r.left + r.width/2, clientY: r.top + r.height/2
                            };
                            el.dispatchEvent(new MouseEvent('mousedown', o));
                            el.dispatchEvent(new MouseEvent('mouseup', o));
                            el.dispatchEvent(new MouseEvent('click', o));
                            return { success: true, id: el.id, text: text };
                        }
                    }
                    return { success: false, message: 'submenu not found: ' + targetText };
                }

                return clickSubmenu('상품조회');
            """)

            if result.get('success'):
                logger.info(f"상품조회 서브메뉴 클릭: {result.get('text')}")
                self._in_product_search_screen = True
                time.sleep(1.5)
                return True
            else:
                logger.warning(f"상품조회 서브메뉴 클릭 실패: {result.get('message')}")
                return False

        except Exception as e:
            logger.error(f"상품조회 메뉴 이동 실패: {e}")
            return False

    def _navigate_via_order_menu(self) -> bool:
        """발주 메뉴 통해서 상품 검색 (대체 방식)"""
        # 이 방식은 단품별발주 화면에서 상품 입력 후 그리드에서 정보 읽기
        # 실제로는 상품조회 화면이 별도로 필요
        logger.info("발주 메뉴 통한 상품 조회 (대체 방식)")
        return True

    def search_product_in_screen(self, item_cd: str) -> bool:
        """
        상품조회 화면에서 상품 검색

        Args:
            item_cd: 상품 코드

        Returns:
            성공 여부
        """
        if not self.driver:
            return False

        try:
            # 상품코드 입력 필드에 값 설정 후 조회
            result = self.driver.execute_script(f"""
                const itemCd = "{item_cd}";

                try {{
                    const app = nexacro.getApplication();

                    // 상품조회 화면 폼 찾기 (STIT, STMD 등 여러 패턴 시도)
                    const frameSet = app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet;
                    let form = null;

                    // 가능한 화면 ID 패턴들
                    const screenIds = ['STIT010_M0', 'STMD010_M0', 'STMB010_M0', 'STIT001_M0'];
                    for (const id of screenIds) {{
                        if (frameSet?.[id]?.form) {{
                            form = frameSet[id].form;
                            break;
                        }}
                    }}

                    if (!form) {{
                        return {{ success: false, message: 'product search form not found' }};
                    }}

                    // 상품코드 입력 필드 찾기
                    const searchForm = form.div_workForm?.form?.div_search?.form ||
                                      form.div_workForm?.form ||
                                      form;

                    // 가능한 입력 필드명들
                    const fieldNames = ['edt_itemCd', 'edt_pluCd', 'edt_productCd', 'edt_ITEM_CD', 'edtItemCd'];
                    let inputField = null;

                    for (const name of fieldNames) {{
                        if (searchForm[name]) {{
                            inputField = searchForm[name];
                            break;
                        }}
                    }}

                    if (inputField) {{
                        inputField.set_value(itemCd);
                    }} else {{
                        // DOM에서 직접 찾기
                        const inputs = document.querySelectorAll('input[id*="edt_"]');
                        for (const inp of inputs) {{
                            if (inp.offsetParent !== null) {{
                                inp.value = itemCd;
                                inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                break;
                            }}
                        }}
                    }}

                    // 조회 버튼 클릭
                    setTimeout(() => {{
                        const searchBtn = form.div_cmmbtn?.form?.F_10 ||
                                         searchForm.btn_search ||
                                         searchForm.btnSearch;
                        if (searchBtn?.click) {{
                            searchBtn.click();
                        }}
                    }}, 300);

                    return {{ success: true }};

                }} catch(e) {{
                    return {{ success: false, message: e.toString() }};
                }}
            """)

            if result.get('success'):
                logger.info(f"상품 검색: {item_cd}")
                time.sleep(1.5)  # 검색 결과 로딩 대기
                return True
            else:
                logger.warning(f"상품 검색 실패: {result.get('message')}")
                return False

        except Exception as e:
            logger.error(f"상품 검색 오류: {e}")
            return False

    def open_product_detail_popup(self, item_cd: str) -> Optional[Dict[str, Any]]:
        """
        상품 상세 팝업 열어서 정보 추출

        Args:
            item_cd: 상품 코드

        Returns:
            상품 상세 정보 dict 또는 None
        """
        if not self.driver:
            return None

        try:
            # 그리드에서 상품 행 더블클릭 -> 상세 팝업 열기
            result = self.driver.execute_script(f"""
                const targetItemCd = "{item_cd}";

                try {{
                    const app = nexacro.getApplication();
                    const frameSet = app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet;

                    // 현재 화면 찾기
                    let form = null;
                    const screenIds = ['STIT010_M0', 'STMD010_M0', 'STMB010_M0', 'STBJ030_M0'];
                    for (const id of screenIds) {{
                        if (frameSet?.[id]?.form) {{
                            form = frameSet[id].form;
                            break;
                        }}
                    }}

                    if (!form) {{
                        return {{ success: false, message: 'form not found' }};
                    }}

                    // 그리드 데이터셋에서 상품 찾기
                    const workForm = form.div_workForm?.form?.div_work_01?.form ||
                                    form.div_workForm?.form ||
                                    form;

                    const grid = workForm.gdList || workForm.grd_list || workForm.Grid00;
                    if (!grid?._binddataset) {{
                        return {{ success: false, message: 'grid dataset not found' }};
                    }}

                    const ds = grid._binddataset;
                    const rowCount = ds.getRowCount();

                    // 상품코드로 행 찾기
                    let targetRow = -1;
                    for (let i = 0; i < rowCount; i++) {{
                        const cd = ds.getColumn(i, 'ITEM_CD') || ds.getColumn(i, 'PLU_CD');
                        if (cd === targetItemCd) {{
                            targetRow = i;
                            break;
                        }}
                    }}

                    if (targetRow < 0) {{
                        // 첫 번째 행 사용 (검색 결과가 1개인 경우)
                        if (rowCount > 0) {{
                            targetRow = 0;
                        }} else {{
                            return {{ success: false, message: 'product not found in grid' }};
                        }}
                    }}

                    // 행 선택 및 더블클릭 (상세 팝업 열기)
                    grid.selectRow(targetRow);

                    // 더블클릭 이벤트 발생
                    if (grid.oncelldblclick) {{
                        const evt = new nexacro.GridClickEventInfo(
                            grid, "oncelldblclick", false, false, false, false,
                            0, 0, targetRow, targetRow
                        );
                        grid.oncelldblclick._fireEvent(grid, evt);
                    }}

                    return {{ success: true, row: targetRow }};

                }} catch(e) {{
                    return {{ success: false, message: e.toString() }};
                }}
            """)

            if not result.get('success'):
                logger.warning(f"상세 팝업 열기 실패: {result.get('message')}")
                return None

            logger.info(f"상세 팝업 열기 (행: {result.get('row')})")
            time.sleep(1.5)  # 팝업 로딩 대기

            # 팝업에서 데이터 추출
            detail_data = self._extract_from_detail_popup()

            # 팝업 닫기
            self._close_detail_popup()

            return detail_data

        except Exception as e:
            logger.error(f"상세 팝업 오류: {e}")
            return None

    def _extract_from_detail_popup(self) -> Optional[Dict[str, Any]]:
        """상세 팝업에서 데이터 추출"""
        try:
            result = self.driver.execute_script("""
                try {
                    const app = nexacro.getApplication();

                    // 상세 팝업 데이터셋 찾기 (dsItemDetail, dsDetail 등)
                    const frameSet = app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet;

                    // 팝업 폼 찾기
                    let popupForm = null;
                    const popupIds = ['fn_ItemDetail', 'pop_ItemDetail', 'STIT010_P0', 'ItemDetailPopup'];

                    for (const id of popupIds) {
                        const popup = frameSet?.[id]?.form || document.querySelector('[id*="' + id + '"]');
                        if (popup) {
                            popupForm = popup.form || popup;
                            break;
                        }
                    }

                    // 팝업이 없으면 현재 화면에서 데이터 읽기
                    if (!popupForm) {
                        // 현재 활성 화면에서 dsDetail 찾기
                        for (const key in frameSet) {
                            if (frameSet[key]?.form?.dsDetail || frameSet[key]?.form?.dsItemDetail) {
                                popupForm = frameSet[key].form;
                                break;
                            }
                        }
                    }

                    // 데이터셋에서 정보 추출
                    let ds = popupForm?.dsDetail || popupForm?.dsItemDetail;

                    if (!ds) {
                        // div_workForm 안에서 찾기
                        ds = popupForm?.div_workForm?.form?.dsDetail ||
                             popupForm?.div_workForm?.form?.dsItemDetail;
                    }

                    if (!ds || ds.getRowCount() === 0) {
                        // 그리드 바인드 데이터셋에서 찾기
                        const grid = popupForm?.gdList || popupForm?.Grid00;
                        if (grid?._binddataset && grid._binddataset.getRowCount() > 0) {
                            ds = grid._binddataset;
                        }
                    }

                    if (!ds || ds.getRowCount() === 0) {
                        return { success: false, message: 'detail dataset not found or empty' };
                    }

                    // 첫 번째 행에서 데이터 추출
                    const row = 0;
                    const data = {};

                    // 모든 컬럼 읽기
                    const colCount = ds.colcount || 50;
                    for (let i = 0; i < colCount; i++) {
                        try {
                            const colId = ds.getColID(i);
                            data[colId] = ds.getColumn(row, colId);
                        } catch(e) {}
                    }

                    // 필요한 필드 매핑
                    const result = {
                        item_cd: data.ITEM_CD || data.PLU_CD || '',
                        item_nm: data.ITEM_NM || '',
                        expiration_days: parseInt(data.EXPIRE_DAY || data.VAL_TERM || data.FRESH_TERM || data.SHELF_LIFE) || null,
                        orderable_day: data.ORD_ADAY || data.ORD_DAY || data.ORDERABLE_DAY || '일월화수목금토',
                        orderable_status: data.ORD_STAT_NM || data.ORD_GB_NM || data.STATUS || '',
                        order_unit_name: data.ORD_UNIT_NM || data.UNIT_NM || '',
                        order_unit_qty: parseInt(data.IN_QTY || data.ORD_UNIT_QTY || data.PACK_QTY || data.BAESOO) || 1,
                        case_unit_qty: parseInt(data.CASE_QTY || data.CASE_UNIT_QTY || data.BOX_QTY) || 1,
                        raw_data: data
                    };

                    return { success: true, data: result };

                } catch(e) {
                    return { success: false, message: e.toString() };
                }
            """)

            if result and result.get('success'):
                data = result.get('data', {})
                logger.info(f"상품명: {data.get('item_nm')}")
                logger.info(f"유통기한: {data.get('expiration_days')}일")
                logger.info(f"발주요일: {data.get('orderable_day')}")
                logger.info(f"입수개수: {data.get('order_unit_qty')}")
                return data
            else:
                logger.warning(f"팝업 데이터 추출 실패: {result.get('message')}")
                return None

        except Exception as e:
            logger.error(f"팝업 데이터 추출 오류: {e}")
            return None

    def _close_detail_popup(self) -> None:
        """상세 팝업 닫기"""
        try:
            self.driver.execute_script("""
                // 닫기/확인 버튼 찾아서 클릭
                function clickByText(text) {
                    const allElements = document.querySelectorAll('div, span, button');
                    for (const el of allElements) {
                        const elText = (el.innerText || '').trim();
                        if (elText === text && el.offsetParent !== null && el.children.length <= 1) {
                            el.scrollIntoView({block: 'center'});
                            const r = el.getBoundingClientRect();
                            const o = {
                                bubbles: true, cancelable: true, view: window,
                                clientX: r.left + r.width/2, clientY: r.top + r.height/2
                            };
                            el.dispatchEvent(new MouseEvent('mousedown', o));
                            el.dispatchEvent(new MouseEvent('mouseup', o));
                            el.dispatchEvent(new MouseEvent('click', o));
                            return true;
                        }
                    }
                    return false;
                }

                // 닫기 버튼 시도
                if (!clickByText('닫기')) {
                    clickByText('확인');
                }
            """)
            time.sleep(0.5)
        except Exception as e:
            logger.debug(f"팝업 닫기 중: {e}")

    def prefetch_product_details(
        self,
        item_codes: List[str],
        use_order_screen: bool = False
    ) -> Dict[str, Dict[str, Any]]:
        """
        발주 전 상품 상세 정보 사전 수집 (핵심 메서드)

        Args:
            item_codes: 수집할 상품 코드 리스트
            use_order_screen: False 권장 - 메인 화면에서 상품 상세 팝업으로 수집

        Returns:
            {item_cd: {info}, ...}
        """
        if not self.driver:
            logger.error("WebDriver가 없습니다")
            return {}

        # DB에 상세정보가 없는 상품만 필터링
        missing_items = self.get_items_without_details(item_codes)

        if not missing_items:
            logger.info("모든 상품의 상세정보가 DB에 있습니다")
            # 기존 DB 데이터 반환
            results = {}
            for item_cd in item_codes:
                cached = self.get_from_db(item_cd)
                if cached:
                    results[item_cd] = cached
            return results

        logger.info(f"상품 상세정보 사전 수집 (메인 화면) - 전체: {len(item_codes)}개, 수집필요: {len(missing_items)}개")

        results = {}
        success_count = 0
        fail_count = 0

        if use_order_screen:
            # [비권장] 단품별발주 화면에서 수집 - 화면 상태 방해 가능
            logger.warning("발주 화면 사용은 권장하지 않습니다")
            return self._prefetch_via_order_screen(missing_items, item_codes)

        # [권장] 메인 화면에서 상품 상세 팝업 직접 호출
        logger.info("메인 화면에서 상품 상세 팝업으로 정보 수집...")

        for i, item_cd in enumerate(missing_items):
            logger.info(f"[{i+1}/{len(missing_items)}] {item_cd}")

            # Alert 처리
            self._clear_all_alerts()

            # 메인 화면에서 상품 상세 팝업 직접 호출
            detail_data = self._fetch_product_detail_via_popup(item_cd)

            if detail_data and detail_data.get("item_nm"):
                # DB 저장
                self.save_to_db(item_cd, detail_data)
                results[item_cd] = detail_data
                success_count += 1
                logger.info(f"상품명: {detail_data.get('item_nm')}, 입수개수: {detail_data.get('order_unit_qty')}, 발주요일: {detail_data.get('orderable_day')}")
            else:
                fail_count += 1
                logger.warning("상품 정보 조회 실패")

            time.sleep(0.5)

        # 기존 DB 데이터도 결과에 포함
        for item_cd in item_codes:
            if item_cd not in results:
                cached = self.get_from_db(item_cd)
                if cached:
                    results[item_cd] = cached

        logger.info(f"상품 정보 수집 완료 - 성공: {success_count}건, 실패: {fail_count}건")

        return results

    def _fetch_product_detail_via_popup(self, item_cd: str) -> Optional[Dict[str, Any]]:
        """
        메인 화면에서 상품 상세 팝업(CallItemDetailPopup)을 호출하여 정보 조회
        - 검색 필드에 상품코드 입력 후 Enter 키로 팝업 열기
        - 팝업에서 정보 추출 후 팝업 닫기

        Args:
            item_cd: 상품 코드

        Returns:
            상품 상세 정보 dict 또는 None
        """
        if not self.driver:
            return None

        try:
            # 1단계: 검색 필드에 상품코드 입력
            result = self.driver.execute_script(f"""
                const productCode = "{item_cd}";

                try {{
                    const app = nexacro.getApplication?.();
                    if (!app) return {{ success: false, message: "app not found" }};

                    // 메인 폼 찾기
                    let main = null;
                    const possiblePaths = [
                        app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet?.WorkFrame?.form,
                        app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet?.STMB011_M0?.form,
                        app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet?.form,
                        app?.mainframe?.form
                    ];
                    for (const path of possiblePaths) {{
                        if (path) {{ main = path; break; }}
                    }}

                    // 검색 필드 찾기 (넥사크로 컴포넌트)
                    let searchField = null;
                    const possibleSearchFields = [
                        main?.edt_pluSearch, main?.components?.edt_pluSearch,
                        main?.edt_search, main?.edt_product, main?.edt_item, main?.edt_plu, main?.edt_code,
                        main?.div_workForm?.form?.edt_pluSearch, main?.div_workForm?.form?.edt_search
                    ];
                    for (const field of possibleSearchFields) {{
                        if (field && typeof field.set_value === 'function') {{
                            searchField = field;
                            break;
                        }}
                    }}

                    if (searchField) {{
                        // 상품코드 입력
                        searchField.setFocus?.();
                        searchField.set_value(productCode);
                        searchField.text = productCode;
                        return {{ success: true, method: "nexacro_field", fieldId: searchField.id }};
                    }}

                    // DOM에서 직접 검색 필드 찾기
                    const inputs = document.querySelectorAll('input[id*="edt_plu"], input[id*="edt_search"], input[id*="pluSearch"]');
                    for (const inp of inputs) {{
                        if (inp.offsetParent !== null) {{
                            inp.focus();
                            inp.value = productCode;
                            inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            return {{ success: true, method: "dom_input", inputId: inp.id }};
                        }}
                    }}

                    return {{ success: false, message: "searchField not found" }};

                }} catch(e) {{
                    return {{ success: false, message: e.toString() }};
                }}
            """)

            if not result or not result.get("success"):
                logger.warning(f"검색 필드 입력 실패: {result.get('message', 'unknown')}")
                return None

            logger.info(f"검색 필드 입력: {result.get('method')}")
            time.sleep(0.3)

            # 2단계: Enter 키 눌러서 팝업 열기
            enter_result = self.driver.execute_script("""
                try {
                    // 현재 포커스된 요소에서 Enter 키 이벤트 발생
                    const activeEl = document.activeElement;
                    if (activeEl) {
                        // KeyboardEvent로 Enter 키 발생
                        const enterEvent = new KeyboardEvent('keydown', {
                            key: 'Enter',
                            code: 'Enter',
                            keyCode: 13,
                            which: 13,
                            bubbles: true,
                            cancelable: true
                        });
                        activeEl.dispatchEvent(enterEvent);

                        // keyup도 발생
                        const keyupEvent = new KeyboardEvent('keyup', {
                            key: 'Enter',
                            code: 'Enter',
                            keyCode: 13,
                            which: 13,
                            bubbles: true,
                            cancelable: true
                        });
                        activeEl.dispatchEvent(keyupEvent);

                        return { success: true, element: activeEl.id || activeEl.tagName };
                    }

                    // 넥사크로 방식으로 Enter 키 이벤트
                    const app = nexacro.getApplication?.();
                    const main = app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet?.WorkFrame?.form ||
                                app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet?.STMB011_M0?.form;

                    if (main?.edt_pluSearch) {
                        const field = main.edt_pluSearch;
                        // 넥사크로 keyup 이벤트 발생 (13 = Enter)
                        try { field.on_fire_onkeyup?.(field, 13, false, false, false); } catch(e) {}
                        // onkeydown도 시도
                        try { field.on_fire_onkeydown?.(field, 13, false, false, false); } catch(e) {}
                        return { success: true, element: "nexacro_keyup" };
                    }

                    return { success: false, message: "no active element" };
                } catch(e) {
                    return { success: false, message: e.toString() };
                }
            """)

            logger.info(f"Enter 키 전송: {enter_result.get('element', 'unknown')}")
            time.sleep(2)  # 팝업 열림 대기

            # 3단계: 팝업에서 데이터 추출
            detail_data = self._extract_from_call_item_detail_popup(item_cd)

            # 4단계: 팝업 닫기
            self._close_call_item_detail_popup()

            return detail_data

        except Exception as e:
            logger.error(f"오류: {e}")
            return None

    def _extract_from_call_item_detail_popup(self, item_cd: str) -> Optional[Dict[str, Any]]:
        """
        CallItemDetailPopup 팝업에서 상품 정보 추출
        - 팝업이 열릴 때까지 대기 (최대 5초)
        - 데이터셋에서 정보 추출
        """
        # 최대 5초간 팝업 대기 (0.5초 간격으로 10번 시도)
        for attempt in range(10):
            try:
                result = self.driver.execute_script(f"""
                    const targetItemCd = "{item_cd}";

                    try {{
                        const app = nexacro.getApplication?.();
                        if (!app) return {{ success: false, message: "app not found", retry: false }};

                        // 팝업 폼 찾기
                        let popupForm = null;
                        let popupId = null;

                        // 1. popupframes에서 CallItemDetailPopup 찾기
                        const pf = app.popupframes;
                        if (pf && typeof pf === "object") {{
                            // CallItemDetailPopup 직접 찾기
                            if (pf.CallItemDetailPopup?.form) {{
                                popupForm = pf.CallItemDetailPopup.form;
                                popupId = "CallItemDetailPopup";
                            }} else {{
                                // 마지막 팝업 사용
                                const ids = Object.keys(pf);
                                if (ids.length > 0) {{
                                    const lastId = ids[ids.length - 1];
                                    const last = pf[lastId];
                                    if (last?.form) {{
                                        popupForm = last.form;
                                        popupId = lastId;
                                    }}
                                }}
                            }}
                        }}

                        // 2. window.newChild에서 찾기
                        if (!popupForm && window.newChild?.form) {{
                            popupForm = window.newChild.form;
                            popupId = "newChild";
                        }}

                        if (!popupForm) {{
                            return {{ success: false, message: "popup not opened yet", retry: true }};
                        }}

                        // 데이터셋 찾기 (참고용 코드 패턴)
                        const objs = popupForm.objects || {{}};
                        let d1 = objs.dsItemDetail;
                        let d2 = objs.dsItemDetailOrd || d1;

                        // objects에 없으면 form에서 직접 찾기
                        if (!d1) {{
                            d1 = popupForm.dsItemDetail || popupForm.dsDetail;
                            d2 = popupForm.dsItemDetailOrd || d1;
                        }}

                        const row = 0;

                        function gv(ds, col) {{
                            try {{
                                if (!ds || typeof ds.getRowCount !== 'function' || ds.getRowCount() <= 0) return null;
                                const v = ds.getColumn(row, col);
                                return (v == null) ? null : String(v);
                            }} catch(e) {{ return null; }}
                        }}

                        // 데이터셋에 데이터가 있는지 확인
                        const itemNm = gv(d1, "ITEM_NM");
                        if (!itemNm) {{
                            return {{ success: false, message: "dataset empty or loading", retry: true, popupId: popupId }};
                        }}

                        // 행사 정보 추출
                        // 1) 데이터셋에서 시도
                        let promoType = gv(d1, "EVT_NM") || gv(d1, "PROMO_NM") || gv(d1, "PROMO_TYPE") ||
                                       gv(d1, "EVT_TYPE") || gv(d1, "EVENT_NM") || gv(d2, "EVT_NM") ||
                                       gv(d2, "PROMO_NM") || gv(d1, "EVT_GB_NM") || gv(d1, "HANGSA_NM") || null;

                        // 2) DOM에서 stEvt01 textarea 직접 읽기
                        if (!promoType || promoType.trim() === '') {{
                            // 여러 가능한 경로 시도
                            const possibleIds = [
                                'mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame.CallItemDetailPopup.form.stEvt01:textarea',
                                'mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame.topFrameSearch.CallItemDetailPopup.form.stEvt01:textarea'
                            ];
                            for (const id of possibleIds) {{
                                const evtTextarea = document.getElementById(id);
                                if (evtTextarea && evtTextarea.value) {{
                                    const rawText = evtTextarea.value.trim();
                                    // "당월 : 1+1 | ..." 또는 "본월 : 1+1 | ..." 형식에서 행사 유형 추출
                                    // "1+1" 또는 "2+1" 패턴 찾기 (당월/본월 행 기준)
                                    const match = rawText.match(/(당월|본월)\\s*:\\s*(\\d\\+\\d|[^|]+?)\\s*\\|/);
                                    if (match && match[2]) {{
                                        promoType = match[2].trim();
                                    }} else {{
                                        // 1+1 또는 2+1 직접 찾기
                                        const directMatch = rawText.match(/(\\d\\+\\d)/);
                                        if (directMatch) {{
                                            promoType = directMatch[1];
                                        }}
                                    }}
                                    break;
                                }}
                            }}
                            // querySelector 폴백
                            if (!promoType) {{
                                const evtText = document.querySelector('[id*="CallItemDetailPopup"][id*="stEvt01"]:not([id*="vscrollbar"])');
                                if (evtText) {{
                                    const rawText = (evtText.value || evtText.innerText || evtText.textContent || '').trim();
                                    const match = rawText.match(/(당월|본월)\\s*:\\s*(\\d\\+\\d|[^|]+?)\\s*\\|/);
                                    if (match && match[2]) {{
                                        promoType = match[2].trim();
                                    }} else {{
                                        const directMatch = rawText.match(/(\\d\\+\\d)/);
                                        if (directMatch) {{
                                            promoType = directMatch[1];
                                        }}
                                    }}
                                }}
                            }}
                        }}

                        const promoName = gv(d1, "EVT_ITEM_NM") || gv(d1, "PROMO_DESC") || promoType || null;
                        const promoStart = gv(d1, "EVT_START_DATE") || gv(d1, "PROMO_START") ||
                                          gv(d1, "EVT_FROM_DATE") || gv(d1, "HANGSA_START") || null;
                        const promoEnd = gv(d1, "EVT_END_DATE") || gv(d1, "PROMO_END") ||
                                        gv(d1, "EVT_TO_DATE") || gv(d1, "HANGSA_END") || null;

                        // 데이터 추출
                        const data = {{
                            item_cd: gv(d1, "ITEM_CD") || gv(d1, "PLU_CD") || targetItemCd,
                            item_nm: itemNm,
                            expiration_days: parseInt(gv(d1, "EXPIRE_DAY")) || null,
                            orderable_day: gv(d2, "ORD_ADAY") || gv(d1, "ORD_ADAY") || '일월화수목금토',
                            orderable_status: gv(d2, "ORD_PSS_ID_NM") || gv(d2, "ORD_STAT_NM") || gv(d2, "ORD_GB") || '',
                            order_unit_name: gv(d1, "ORD_UNIT_NM") || gv(d2, "ORD_UNIT_NM") || '',
                            order_unit_qty: parseInt(gv(d1, "ORD_UNIT_QTY") || gv(d2, "ORD_UNIT_QTY")) || 1,
                            case_unit_qty: parseInt(gv(d1, "CASE_UNIT_QTY") || gv(d2, "CASE_UNIT_QTY")) || 1,
                            promo_type: promoType,
                            promo_name: promoName,
                            promo_start: promoStart,
                            promo_end: promoEnd
                        }};

                        return {{ success: true, data: data, popupId: popupId }};

                    }} catch(e) {{
                        return {{ success: false, message: e.toString(), retry: false }};
                    }}
                """)

                if result and result.get("success"):
                    data = result.get("data")
                    promo_info = f", 행사={data.get('promo_type')}" if data.get('promo_type') else ""
                    logger.info(f"팝업 데이터: {data.get('item_nm')}, 입수={data.get('order_unit_qty')}, 요일={data.get('orderable_day')}{promo_info}")
                    return data

                # 재시도 가능하면 대기 후 다시
                if result and result.get("retry"):
                    if attempt < 9:
                        time.sleep(0.5)
                        continue
                    else:
                        logger.warning(f"팝업 대기 시간 초과: {result.get('message')}")
                        return None
                else:
                    logger.warning(f"팝업 데이터 추출 실패: {result.get('message', 'unknown')}")
                    return None

            except Exception as e:
                logger.error(f"팝업 데이터 추출 오류: {e}")
                if attempt < 9:
                    time.sleep(0.5)
                    continue
                return None

        return None

    def _close_call_item_detail_popup(self) -> None:
        """CallItemDetailPopup 팝업 닫기 (참고용 코드 패턴)"""
        try:
            result = self.driver.execute_script("""
                try {
                    const app = nexacro.getApplication?.();

                    // 1. 참고용 코드의 btn_close ID로 찾기
                    const btnId = "mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame.CallItemDetailPopup.form.btn_close";
                    const el = document.getElementById(btnId);
                    if (el && el.offsetParent !== null) {
                        el.scrollIntoView({block:'center'});
                        const r = el.getBoundingClientRect();
                        const o = { bubbles:true, cancelable:true, view:window, clientX:r.left+r.width/2, clientY:r.top+r.height/2 };
                        el.dispatchEvent(new MouseEvent('mousedown', o));
                        el.dispatchEvent(new MouseEvent('mouseup', o));
                        el.dispatchEvent(new MouseEvent('click', o));
                        return { success: true, method: "btn_close_id" };
                    }

                    // 2. 팝업 내 닫기 버튼 찾기 (btn_close, btn_cancel 등)
                    const closeButtons = document.querySelectorAll('[id*="CallItemDetail"][id*="btn_close"], [id*="CallItemDetail"][id*="btn_cancel"]');
                    for (const btn of closeButtons) {
                        if (btn.offsetParent !== null) {
                            const r = btn.getBoundingClientRect();
                            const o = { bubbles:true, cancelable:true, view:window, clientX:r.left+r.width/2, clientY:r.top+r.height/2 };
                            btn.dispatchEvent(new MouseEvent('mousedown', o));
                            btn.dispatchEvent(new MouseEvent('mouseup', o));
                            btn.dispatchEvent(new MouseEvent('click', o));
                            return { success: true, method: "btn_close_query" };
                        }
                    }

                    // 3. 넥사크로 팝업프레임에서 close 호출
                    const pf = app?.popupframes;
                    if (pf?.CallItemDetailPopup) {
                        pf.CallItemDetailPopup.close();
                        return { success: true, method: "popupframes.close" };
                    }

                    // 4. 마지막 팝업 닫기
                    if (pf && typeof pf === "object") {
                        const ids = Object.keys(pf);
                        if (ids.length > 0) {
                            const lastId = ids[ids.length - 1];
                            const last = pf[lastId];
                            if (last?.close) {
                                last.close();
                                return { success: true, method: "last_popup.close", popupId: lastId };
                            }
                        }
                    }

                    // 5. window.newChild 닫기
                    if (window.newChild?.close) {
                        window.newChild.close();
                        return { success: true, method: "newChild.close" };
                    }

                    // 6. ESC 키로 닫기 시도
                    document.dispatchEvent(new KeyboardEvent('keydown', {
                        key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true
                    }));
                    return { success: true, method: "escape_key" };

                } catch(e) {
                    return { success: false, message: e.toString() };
                }
            """)

            if result and result.get("success"):
                logger.info(f"팝업 닫기: {result.get('method')}")
            time.sleep(0.5)

        except Exception as e:
            logger.error(f"팝업 닫기 오류: {e}")

    def _extract_from_popup_dataset(self, item_cd: str) -> Optional[Dict[str, Any]]:
        """팝업에서 열린 데이터셋에서 상품 정보 추출"""
        try:
            result = self.driver.execute_script(f"""
                const targetItemCd = "{item_cd}";

                try {{
                    const app = nexacro.getApplication();
                    const mainframe = app.mainframe;

                    // 팝업 데이터셋 찾기 (여러 패턴 시도)
                    let ds = null;

                    // 1. 팝업 프레임에서 찾기
                    const popupFrames = document.querySelectorAll('[id*="fn_"][id*="ItemDetail"], [id*="pop_"], [id*="STIT010_P"]');
                    for (const frame of popupFrames) {{
                        const frameId = frame.id?.split('.').pop() || '';
                        const frameSet = mainframe?.HFrameSet00?.VFrameSet00?.FrameSet || mainframe?.HFrameSet00?.FrameSet;

                        if (frameSet?.[frameId]?.form?.dsDetail) {{
                            ds = frameSet[frameId].form.dsDetail;
                            break;
                        }}
                        if (frameSet?.[frameId]?.form?.dsItemDetail) {{
                            ds = frameSet[frameId].form.dsItemDetail;
                            break;
                        }}
                    }}

                    // 2. 전역 데이터셋에서 찾기
                    if (!ds) {{
                        const frameSet = mainframe?.HFrameSet00?.VFrameSet00?.FrameSet;
                        for (const key in frameSet) {{
                            const form = frameSet[key]?.form;
                            if (form?.dsDetail && form.dsDetail.getRowCount() > 0) {{
                                ds = form.dsDetail;
                                break;
                            }}
                            if (form?.dsItemDetail && form.dsItemDetail.getRowCount() > 0) {{
                                ds = form.dsItemDetail;
                                break;
                            }}
                        }}
                    }}

                    // 3. DOM에서 직접 팝업 폼 찾기
                    if (!ds) {{
                        const popupForms = document.querySelectorAll('[id*="fn_"][id*="form"]');
                        for (const pf of popupForms) {{
                            // 넥사크로 객체 참조 찾기
                            const formId = pf.id || '';
                            // ... (추가 탐색 로직)
                        }}
                    }}

                    if (!ds || ds.getRowCount() === 0) {{
                        return {{ success: false, message: "dataset not found or empty" }};
                    }}

                    // 첫 번째 행에서 데이터 추출
                    const row = 0;
                    const data = {{}};

                    // 모든 컬럼 읽기
                    const colCount = ds.colcount || 100;
                    const colNames = [];
                    for (let i = 0; i < colCount; i++) {{
                        try {{
                            const colId = ds.getColID(i);
                            colNames.push(colId);
                            data[colId] = ds.getColumn(row, colId);
                        }} catch(e) {{}}
                    }}

                    // 필요한 필드 매핑
                    return {{
                        success: true,
                        data: {{
                            item_cd: data.ITEM_CD || data.PLU_CD || targetItemCd,
                            item_nm: data.ITEM_NM || data.PROD_NM || '',
                            expiration_days: parseInt(data.EXPIRE_DAY || data.VAL_TERM || data.FRESH_TERM || data.SHELF_LIFE) || null,
                            orderable_day: data.ORD_ADAY || data.ORD_DAY || data.ORDERABLE_DAY || '일월화수목금토',
                            orderable_status: data.ORD_STAT_NM || data.ORD_GB_NM || '',
                            order_unit_name: data.ORD_UNIT_NM || data.UNIT_NM || '',
                            order_unit_qty: parseInt(data.IN_QTY || data.ORD_UNIT_QTY || data.PACK_QTY || data.BAESOO) || 1,
                            case_unit_qty: parseInt(data.CASE_QTY || data.CASE_UNIT_QTY || data.BOX_QTY) || 1
                        }},
                        columns: colNames
                    }};

                }} catch(e) {{
                    return {{ success: false, message: e.toString() }};
                }}
            """)

            if result and result.get("success"):
                return result.get("data")
            else:
                logger.warning(f"데이터셋 추출 실패: {result.get('message', 'unknown')}")
                return None

        except Exception as e:
            logger.error(f"데이터셋 추출 오류: {e}")
            return None

    def _fetch_product_detail_direct(self, item_cd: str) -> Optional[Dict[str, Any]]:
        """
        팝업 없이 직접 API/트랜잭션으로 상품 상세 조회
        (상품조회 메뉴 이동 후 검색)
        """
        try:
            logger.info(f"직접 조회 시도: {item_cd}")

            # 상품조회 메뉴로 이동
            if not self.navigate_to_product_search():
                logger.error("상품조회 메뉴 이동 실패")
                return None

            time.sleep(1)

            # 상품 검색
            if self.search_product_in_screen(item_cd):
                time.sleep(1)

                # 그리드에서 정보 읽기
                result = self.driver.execute_script(f"""
                    const targetItemCd = "{item_cd}";

                    try {{
                        const app = nexacro.getApplication();
                        const frameSet = app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet;

                        // 상품조회 화면 찾기
                        let form = null;
                        const screenIds = ['STIT010_M0', 'STMD010_M0', 'STMB010_M0', 'STMB011_M0'];

                        for (const id of screenIds) {{
                            if (frameSet?.[id]?.form) {{
                                form = frameSet[id].form;
                                break;
                            }}
                        }}

                        if (!form) {{
                            return {{ success: false, message: "product search form not found" }};
                        }}

                        // 그리드 데이터셋 찾기
                        const workForm = form.div_workForm?.form || form;
                        const grid = workForm.gdList || workForm.Grid00 || workForm.grd_list;

                        if (!grid?._binddataset) {{
                            return {{ success: false, message: "grid dataset not found" }};
                        }}

                        const ds = grid._binddataset;
                        if (ds.getRowCount() === 0) {{
                            return {{ success: false, message: "no search results" }};
                        }}

                        // 첫 번째 행에서 데이터 추출
                        const row = 0;
                        const data = {{}};

                        const colCount = ds.colcount || 50;
                        for (let i = 0; i < colCount; i++) {{
                            try {{
                                const colId = ds.getColID(i);
                                data[colId] = ds.getColumn(row, colId);
                            }} catch(e) {{}}
                        }}

                        return {{
                            success: true,
                            data: {{
                                item_cd: data.ITEM_CD || data.PLU_CD || targetItemCd,
                                item_nm: data.ITEM_NM || '',
                                expiration_days: parseInt(data.EXPIRE_DAY || data.VAL_TERM) || null,
                                orderable_day: data.ORD_ADAY || data.ORD_DAY || '일월화수목금토',
                                orderable_status: data.ORD_STAT_NM || '',
                                order_unit_name: data.ORD_UNIT_NM || '',
                                order_unit_qty: parseInt(data.IN_QTY || data.ORD_UNIT_QTY) || 1,
                                case_unit_qty: parseInt(data.CASE_QTY) || 1
                            }}
                        }};

                    }} catch(e) {{
                        return {{ success: false, message: e.toString() }};
                    }}
                """)

                # 상품조회 탭 닫기
                self._close_product_search_tab()

                if result and result.get("success"):
                    return result.get("data")

            return None

        except Exception as e:
            logger.error(f"직접 조회 오류: {e}")
            return None

    def _close_any_popup(self) -> None:
        """열린 팝업 닫기"""
        try:
            self.driver.execute_script("""
                // 닫기 버튼 찾아서 클릭
                const closeButtons = document.querySelectorAll(
                    '[id*="btn_close"], [id*="btnClose"], [id*="btn_cancel"], ' +
                    '[id*="Button"][id*="close"], [id*="확인"], [id*="닫기"]'
                );

                for (const btn of closeButtons) {
                    if (btn.offsetParent !== null) {
                        const r = btn.getBoundingClientRect();
                        const o = {
                            bubbles: true, cancelable: true, view: window,
                            clientX: r.left + r.width/2, clientY: r.top + r.height/2
                        };
                        btn.dispatchEvent(new MouseEvent('mousedown', o));
                        btn.dispatchEvent(new MouseEvent('mouseup', o));
                        btn.dispatchEvent(new MouseEvent('click', o));
                        break;
                    }
                }

                // ESC 키 이벤트 발생
                document.dispatchEvent(new KeyboardEvent('keydown', {
                    key: 'Escape', code: 'Escape', keyCode: 27, which: 27,
                    bubbles: true
                }));
            """)
            time.sleep(0.3)
        except Exception as e:
            logger.debug(f"ESC 키 이벤트 처리 중: {e}")

    def _close_product_search_tab(self) -> None:
        """상품조회 탭 닫기"""
        try:
            self.driver.execute_script("""
                // 상품조회 탭의 닫기 버튼 찾기
                const tabs = document.querySelectorAll('[id*="tab_"][id*="STIT"], [id*="tab_"][id*="STMB"], [id*="tab_"][id*="STMD"]');

                for (const tab of tabs) {
                    const closeBtn = tab.querySelector('[id*="close"], [id*="Close"]') ||
                                    tab.parentElement?.querySelector('[id*="close"]');
                    if (closeBtn && closeBtn.offsetParent !== null) {
                        closeBtn.click();
                        break;
                    }
                }

                // 대안: 현재 탭 닫기 버튼
                const topCloseBtn = document.querySelector('[id*="btn_topClose"]');
                if (topCloseBtn && topCloseBtn.offsetParent !== null) {
                    // topCloseBtn.click();
                }
            """)
            time.sleep(0.5)
        except Exception as e:
            logger.debug(f"상품조회 탭 닫기 중: {e}")

    def _prefetch_via_order_screen(
        self, missing_items: List[str], all_item_codes: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """
        [비권장] 단품별발주 화면에서 상품 정보 수집
        화면 상태를 방해할 수 있으므로 use_order_screen=False 권장
        """
        results = {}
        success_count = 0
        fail_count = 0

        # 발주 메뉴로 이동
        if not self._navigate_to_order_menu():
            logger.error("발주 메뉴 이동 실패")
            return results

        # 날짜 선택 (오늘)
        if not self._select_first_order_day():
            logger.warning("날짜 선택 실패, 계속 진행")

        time.sleep(1)

        for i, item_cd in enumerate(missing_items):
            logger.info(f"[{i+1}/{len(missing_items)}] {item_cd}")

            # 먼저 Alert 처리
            self._clear_all_alerts()

            # 상품코드 입력
            input_result = self._input_product_for_info(item_cd)

            # 입력 후 Alert 처리
            time.sleep(0.5)
            alert_text = self._clear_all_alerts()

            if alert_text and ("올바른" in alert_text or "상품" in alert_text):
                logger.warning(f"발주 불가 상품: {alert_text[:30]}")
                fail_count += 1
                continue

            if input_result:
                time.sleep(1)
                try:
                    grid_data = self.fetch_from_grid_after_input()

                    if grid_data and grid_data.get("item_nm"):
                        self.save_to_db(item_cd, grid_data)
                        results[item_cd] = grid_data
                        success_count += 1
                        logger.info(f"입수개수: {grid_data.get('order_unit_qty')}")
                    else:
                        fail_count += 1
                        logger.warning("그리드 데이터 읽기 실패")
                except Exception as e:
                    fail_count += 1
                    logger.error(f"오류: {e}")
                    self._clear_all_alerts()
            else:
                fail_count += 1
                logger.warning("상품 입력 실패")

            time.sleep(0.5)

        # 화면 초기화
        self._cancel_current_order()
        self._go_to_home()

        # 기존 DB 데이터도 결과에 포함
        for item_cd in all_item_codes:
            if item_cd not in results:
                cached = self.get_from_db(item_cd)
                if cached:
                    results[item_cd] = cached

        logger.info(f"성공: {success_count}건, 실패: {fail_count}건")
        return results

    def _navigate_to_order_menu(self) -> bool:
        """발주 > 단품별 발주 메뉴로 이동"""
        try:
            # 발주 메뉴 클릭
            result = self.driver.execute_script("""
                function clickById(id) {
                    const el = document.getElementById(id);
                    if (!el || el.offsetParent === null) return false;
                    el.scrollIntoView({block: 'center'});
                    const r = el.getBoundingClientRect();
                    const o = {
                        bubbles: true, cancelable: true, view: window,
                        clientX: r.left + r.width/2, clientY: r.top + r.height/2
                    };
                    el.dispatchEvent(new MouseEvent('mousedown', o));
                    el.dispatchEvent(new MouseEvent('mouseup', o));
                    el.dispatchEvent(new MouseEvent('click', o));
                    return true;
                }

                return clickById("mainframe.HFrameSet00.VFrameSet00.TopFrame.form.div_topMenu.form.STBJ000_M0:icontext");
            """)

            if not result:
                return False

            time.sleep(1)

            # 단품별 발주 서브메뉴 클릭
            result = self.driver.execute_script("""
                function clickByText(text) {
                    const elements = document.querySelectorAll('[id*="pdiv_topMenu"] *');
                    for (const el of elements) {
                        const elText = (el.innerText || '').trim();
                        if (elText === text && el.offsetParent !== null) {
                            el.scrollIntoView({block: 'center'});
                            const r = el.getBoundingClientRect();
                            const o = {
                                bubbles: true, cancelable: true, view: window,
                                clientX: r.left + r.width/2, clientY: r.top + r.height/2
                            };
                            el.dispatchEvent(new MouseEvent('mousedown', o));
                            el.dispatchEvent(new MouseEvent('mouseup', o));
                            el.dispatchEvent(new MouseEvent('click', o));
                            return true;
                        }
                    }
                    return false;
                }

                return clickByText('단품별 발주');
            """)

            time.sleep(1.5)
            return result

        except Exception as e:
            logger.error(f"발주 메뉴 이동 실패: {e}")
            return False

    def _select_first_order_day(self) -> bool:
        """첫 번째 발주 가능일 선택"""
        try:
            time.sleep(2)  # 팝업 대기

            result = self.driver.execute_script("""
                // 팝업에서 첫 번째 행 더블클릭
                const popup = document.querySelector('[id*="fn_initBalju"]');
                if (!popup) return false;

                const firstRow = popup.querySelector('[id*="gridrow_0"]');
                if (!firstRow) return false;

                firstRow.scrollIntoView({block: 'center'});
                const r = firstRow.getBoundingClientRect();
                const o = {
                    bubbles: true, cancelable: true, view: window,
                    clientX: r.left + r.width/2, clientY: r.top + r.height/2,
                    detail: 2
                };

                firstRow.dispatchEvent(new MouseEvent('mousedown', o));
                firstRow.dispatchEvent(new MouseEvent('mouseup', o));
                firstRow.dispatchEvent(new MouseEvent('click', o));
                firstRow.dispatchEvent(new MouseEvent('dblclick', {...o, detail: 2}));

                return true;
            """)

            if result:
                time.sleep(0.5)
                # 선택 버튼 클릭
                self.driver.execute_script("""
                    const popup = document.querySelector('[id*="fn_initBalju"]');
                    if (!popup) return;

                    const btn = popup.querySelector('[id*="Button44"]');
                    if (btn) {
                        const r = btn.getBoundingClientRect();
                        const o = {
                            bubbles: true, cancelable: true, view: window,
                            clientX: r.left + r.width/2, clientY: r.top + r.height/2
                        };
                        btn.dispatchEvent(new MouseEvent('mousedown', o));
                        btn.dispatchEvent(new MouseEvent('mouseup', o));
                        btn.dispatchEvent(new MouseEvent('click', o));
                    }
                """)
                time.sleep(1)
                return True

            return False

        except Exception:
            return False

    def _input_product_for_info(self, item_cd: str) -> bool:
        """상품 정보 수집용 상품코드 입력"""
        try:
            result = self.driver.execute_script(f"""
                const itemCd = "{item_cd}";

                // 입력 필드 찾기
                const inputs = document.querySelectorAll('[id*="gdList"][id*="celledit:input"]');
                let inputEl = null;

                for (const inp of inputs) {{
                    if (inp.offsetParent !== null) {{
                        inputEl = inp;
                        break;
                    }}
                }}

                if (!inputEl) {{
                    // 그리드 마지막 행 클릭
                    const rows = document.querySelectorAll('[id*="gdList"][id*="gridrow_"]');
                    if (rows.length > 0) {{
                        const lastRow = rows[rows.length - 1];
                        const cell = lastRow.querySelector('[id*="cell_"][id*="_1"]');
                        if (cell) {{
                            cell.click();
                        }}
                    }}
                    return {{ success: false, needRetry: true }};
                }}

                inputEl.focus();
                inputEl.value = itemCd;
                inputEl.dispatchEvent(new Event('input', {{ bubbles: true }}));
                inputEl.dispatchEvent(new Event('change', {{ bubbles: true }}));

                // Enter 키
                setTimeout(() => {{
                    const enterEvt = new KeyboardEvent('keydown', {{
                        key: 'Enter', code: 'Enter', keyCode: 13, which: 13,
                        bubbles: true, cancelable: true
                    }});
                    inputEl.dispatchEvent(enterEvt);
                }}, 200);

                return {{ success: true }};
            """)

            if result.get('needRetry'):
                time.sleep(0.5)
                return self._input_product_for_info(item_cd)

            return result.get('success', False)

        except Exception:
            return False

    def _handle_alert(self) -> None:
        """Alert 처리"""
        try:
            alert = self.driver.switch_to.alert
            alert.accept()
        except Exception as e:
            logger.debug(f"Alert 처리 중: {e}")

    def _clear_all_alerts(self) -> Optional[str]:
        """모든 Alert 처리 및 마지막 Alert 텍스트 반환"""
        last_text = None
        for _ in range(5):
            try:
                alert = self.driver.switch_to.alert
                last_text = alert.text
                alert.accept()
                time.sleep(0.2)
            except Exception:
                break
        return last_text

    def _go_to_home(self) -> None:
        """홈 화면으로 이동 (완전 초기화)"""
        try:
            logger.info("홈 화면으로 이동...")

            # 모든 Alert 먼저 처리
            for _ in range(5):
                try:
                    alert = self.driver.switch_to.alert
                    alert.accept()
                    time.sleep(0.2)
                except Exception:
                    break

            # Home 탭 클릭
            self.driver.execute_script("""
                // Home 탭 찾아서 클릭
                const homeTab = document.querySelector('[id*="Home"][id*="tab"]') ||
                               document.querySelector('[id*="Home"]');

                if (homeTab && homeTab.offsetParent !== null) {
                    homeTab.click();
                    return true;
                }

                // 대안: 모든 열린 탭 닫기
                const closeBtns = document.querySelectorAll('[id*="btn_topClose"]');
                for (const btn of closeBtns) {
                    if (btn.offsetParent !== null) {
                        try { btn.click(); } catch(e) {}
                    }
                }

                return true;
            """)

            time.sleep(1)
            logger.info("홈 화면 이동 완료")

        except Exception as e:
            logger.warning(f"홈 이동 실패: {e}")

    def _cancel_current_order(self) -> None:
        """현재 발주 취소 및 화면 초기화 (정보 수집 후 저장하지 않음)"""
        try:
            logger.info("정보 수집 완료 - 화면 초기화...")

            # 1. 그리드 데이터 전체 삭제 시도
            self.driver.execute_script("""
                try {
                    const app = nexacro.getApplication();
                    const stbjForm = app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet?.STBJ030_M0?.form;
                    const workForm = stbjForm?.div_workForm?.form?.div_work_01?.form;

                    if (workForm?.gdList?._binddataset) {
                        const ds = workForm.gdList._binddataset;
                        // 모든 행 삭제
                        while (ds.getRowCount() > 0) {
                            ds.deleteRow(0);
                        }
                        console.log('[OK] 그리드 데이터 삭제 완료');
                    }
                } catch(e) {
                    console.log('[WARN] 그리드 초기화 실패:', e);
                }
            """)
            time.sleep(0.3)

            # 2. 메인 화면(홈)으로 이동하여 완전 초기화
            self.driver.execute_script("""
                try {
                    // 홈 버튼 또는 메인 메뉴 클릭
                    const homeBtn = document.querySelector('[id*="btn_home"]') ||
                                   document.querySelector('[id*="btnHome"]') ||
                                   document.querySelector('[id*="HOME"]');
                    if (homeBtn && homeBtn.offsetParent !== null) {
                        homeBtn.click();
                        return;
                    }

                    // 대안: 현재 탭 닫기 버튼 찾기
                    const closeTabBtn = document.querySelector('[id*="btn_topClose"]');
                    if (closeTabBtn && closeTabBtn.offsetParent !== null) {
                        closeTabBtn.click();
                    }
                } catch(e) {
                    console.log('[WARN] 화면 이동 실패:', e);
                }
            """)
            time.sleep(1)

            logger.info("화면 초기화 완료")

        except Exception as e:
            logger.warning(f"화면 초기화 실패: {e}")


# 테스트용
if __name__ == "__main__":
    collector = ProductInfoCollector()

    # DB 조회 테스트
    info = collector.get_from_db("8801858011024")
    print(f"DB 조회 결과: {info}")
