"""
신상품 도입 현황 수집기

BGF 리테일 사이트 > 점주관리 > ① 신상품 도입 현황 화면에서
도입률, 3일발주 달성률, 미도입 상품 목록 등을 수집하여 DB에 저장

프레임: SS_STBJ460_M0
팝업: STBJ460_P0 (미도입 상품 상세)
데이터셋:
  - dsList (4행): 주차별 신상품 전체 도입률
  - dsConvenienceList (4행): 간편식/디저트 3일발주 달성률
  - dsDetailTotal (4행): 주차별 종합 점수
  - dsDetailMonth (1행): 월별 합계
  - ds_S945: 점수→지원금 구간표
  - ds_S948: 도입률/달성률→점수 환산 기준
  - 팝업 dsDetail: 미도입/미달성 상품 상세
"""

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.settings.ui_config import FRAME_IDS, DS_PATHS
from src.settings.constants import NEW_PRODUCT_POPUP_FRAME_ID
from src.utils.nexacro_helpers import (
    click_menu_by_text, click_submenu_by_text, wait_for_frame,
    close_tab_by_frame_id,
)
from src.infrastructure.database.repos import NewProductStatusRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)

FRAME_ID = FRAME_IDS["NEW_PRODUCT_STATUS"]
DS_PATH = DS_PATHS["NEW_PRODUCT_STATUS"]


class NewProductCollector:
    """신상품 도입 현황 수집기"""

    def __init__(
        self, driver: Optional[Any] = None, store_id: Optional[str] = None
    ) -> None:
        self.driver = driver
        self.store_id = store_id
        self.repo = NewProductStatusRepository(store_id=store_id)

    def set_driver(self, driver: Any) -> None:
        self.driver = driver

    # ─── 메뉴 이동 ───

    def navigate_to_menu(self) -> bool:
        """점주관리 > 신상품 도입 현황 메뉴로 이동"""
        if not self.driver:
            logger.error("드라이버가 설정되지 않음")
            return False

        logger.info("신상품 도입 현황 메뉴 이동...")
        try:
            if not click_menu_by_text(self.driver, "점주관리"):
                logger.error("점주관리 메뉴 클릭 실패")
                return False
            time.sleep(1)

            if not click_submenu_by_text(self.driver, "신상품 도입 현황"):
                logger.error("신상품 도입 현황 서브메뉴 클릭 실패")
                return False
            time.sleep(2)

            if wait_for_frame(self.driver, FRAME_ID):
                logger.info("신상품 도입 현황 화면 로딩 완료")
                return True

            logger.error("화면 로딩 타임아웃")
            return False
        except Exception as e:
            logger.error(f"메뉴 이동 실패: {e}")
            return False

    def close_menu(self) -> bool:
        """탭 닫기"""
        if not self.driver:
            return False
        return close_tab_by_frame_id(self.driver, FRAME_ID)

    # ─── 데이터셋 수집 JS ───

    def _exec_ds_js(self, js_body: str) -> Any:
        """넥사크로 데이터셋 접근 JS 실행 (공통 래퍼)"""
        js = f"""
        try {{
            var app = nexacro.getApplication();
            var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form;
            var wf = form.{DS_PATH};
            {js_body}
        }} catch(e) {{
            return {{error: e.message}};
        }}
        """
        return self.driver.execute_script(js)

    def _to_num(self, val: Any) -> Any:
        """넥사크로 BigInt 객체 → 숫자 변환"""
        if isinstance(val, dict) and "hi" in val:
            return val["hi"]
        return val

    # ─── 주차별 도입률 (dsList) ───

    def collect_weekly_doip(self) -> List[Dict]:
        """dsList - 주차별 신상품 전체 도입률 수집"""
        result = self._exec_ds_js("""
            var ds = wf.dsList;
            if (!ds) return {error: 'dsList not found'};
            var cnt = ds.getRowCount();
            var rows = [];
            var cols = ['STORE_CD','YYYY_CD','N_MNTH_CD','PERIOD','N_WEEK_CD',
                        'DOIP_RATE','ITEM_CNT','ITEM_AD_CNT','DOIP_CNT',
                        'MIDOIP_CNT','WEEK_CONT','WEEK_DISP2'];
            for (var r = 0; r < cnt; r++) {
                var row = {};
                for (var c = 0; c < cols.length; c++) {
                    var v = ds.getColumn(r, cols[c]);
                    if (v && typeof v === 'object' && v.hi !== undefined) v = v.hi;
                    row[cols[c]] = v;
                }
                rows.push(row);
            }
            return rows;
        """)
        if isinstance(result, dict) and result.get("error"):
            logger.error(f"dsList 수집 실패: {result['error']}")
            return []
        return result or []

    # ─── 3일발주 달성률 (dsConvenienceList) ───

    def collect_weekly_ds(self) -> List[Dict]:
        """dsConvenienceList (4행) - 간편식/디저트 3일발주 달성률"""
        result = self._exec_ds_js("""
            var ds = wf.dsConvenienceList;
            if (!ds) return {error: 'dsConvenienceList not found'};
            var cnt = ds.getRowCount();
            var rows = [];
            var cols = ['STORE_CD','N_WEEK_CD','PERIOD','ITEM_CNT',
                        'DS_CNT','MIDS_CNT','DS_RATE','WEEK_CONT'];
            for (var r = 0; r < cnt; r++) {
                var row = {};
                for (var c = 0; c < cols.length; c++) {
                    var v = ds.getColumn(r, cols[c]);
                    if (v && typeof v === 'object' && v.hi !== undefined) v = v.hi;
                    row[cols[c]] = v;
                }
                rows.push(row);
            }
            return rows;
        """)
        if isinstance(result, dict) and result.get("error"):
            logger.error(f"dsConvenienceList 수집 실패: {result['error']}")
            return []
        return result or []

    # ─── 종합 점수 (dsDetailTotal) ───

    def collect_detail_total(self) -> List[Dict]:
        """dsDetailTotal (4행) - 주차별 종합 점수"""
        result = self._exec_ds_js("""
            var ds = wf.dsDetailTotal;
            if (!ds) return {error: 'dsDetailTotal not found'};
            var cnt = ds.getRowCount();
            var rows = [];
            var cols = ['WEEK_CONT','PERIOD','DOIP_RATE','DOIP_SCORE',
                        'DS_RATE','DS_SCORE','TOT_SCORE','SUPP_PAY_AMT','N_WEEK_CD'];
            for (var r = 0; r < cnt; r++) {
                var row = {};
                for (var c = 0; c < cols.length; c++) {
                    var v = ds.getColumn(r, cols[c]);
                    if (v && typeof v === 'object' && v.hi !== undefined) v = v.hi;
                    row[cols[c]] = v;
                }
                rows.push(row);
            }
            return rows;
        """)
        if isinstance(result, dict) and result.get("error"):
            logger.error(f"dsDetailTotal 수집 실패: {result['error']}")
            return []
        return result or []

    # ─── 월별 합계 (dsDetailMonth) ───

    def collect_detail_month(self) -> Optional[Dict]:
        """dsDetailMonth (1행) - 월별 합계"""
        result = self._exec_ds_js("""
            var ds = wf.dsDetailMonth;
            if (!ds) return {error: 'dsDetailMonth not found'};
            if (ds.getRowCount() === 0) return null;
            var cols = ['DOIP_ITEM_CNT','DOIP_CNT','DOIP_RATE','DOIP_SCORE',
                        'DS_ITEM_CNT','DS_CNT','DS_RATE','DS_SCORE',
                        'TOT_SCORE','SUPP_PAY_AMT',
                        'NEXT_MIN_SCORE','NEXT_MAX_SCORE','NEXT_SUPP_PAY_AMT'];
            var row = {};
            for (var c = 0; c < cols.length; c++) {
                var v = ds.getColumn(0, cols[c]);
                if (v && typeof v === 'object' && v.hi !== undefined) v = v.hi;
                row[cols[c]] = v;
            }
            return row;
        """)
        if isinstance(result, dict) and result.get("error"):
            logger.error(f"dsDetailMonth 수집 실패: {result['error']}")
            return None
        return result

    # ─── 점수 환산 기준 (ds_S948) ───

    def collect_score_conversion(self) -> List[Dict]:
        """ds_S948 - 도입률/달성률→점수 환산 기준"""
        result = self._exec_ds_js("""
            var ds = wf.ds_S948;
            if (!ds) return {error: 'ds_S948 not found'};
            var cnt = ds.getRowCount();
            var rows = [];
            var cols = ['CODE','NAME','REMARKS1','REMARKS2','REMARKS3'];
            for (var r = 0; r < cnt; r++) {
                var row = {};
                for (var c = 0; c < cols.length; c++) {
                    var v = ds.getColumn(r, cols[c]);
                    if (v && typeof v === 'object' && v.hi !== undefined) v = v.hi;
                    row[cols[c]] = v;
                }
                rows.push(row);
            }
            return rows;
        """)
        if isinstance(result, dict) and result.get("error"):
            logger.error(f"ds_S948 수집 실패: {result['error']}")
            return []
        return result or []

    # ─── 점수→지원금 구간표 (ds_S945) ───

    def collect_subsidy_table(self) -> List[Dict]:
        """ds_S945 - 점수→지원금 구간표"""
        result = self._exec_ds_js("""
            var ds = wf.ds_S945;
            if (!ds) return {error: 'ds_S945 not found'};
            var cnt = ds.getRowCount();
            var rows = [];
            var cols = ['CODE','NAME','REMARKS1','REMARKS2','REMARKS3',
                        'REMARKS4','REMARKS5','REMARKS6'];
            for (var r = 0; r < cnt; r++) {
                var row = {};
                for (var c = 0; c < cols.length; c++) {
                    var v = ds.getColumn(r, cols[c]);
                    if (v && typeof v === 'object' && v.hi !== undefined) v = v.hi;
                    row[cols[c]] = v;
                }
                rows.push(row);
            }
            return rows;
        """)
        if isinstance(result, dict) and result.get("error"):
            logger.error(f"ds_S945 수집 실패: {result['error']}")
            return []
        return result or []

    # ─── 미도입/미달성 상품 팝업 ───

    def collect_missing_items(self, week_idx: int) -> List[Dict]:
        """미도입 상품 목록 수집 (MIDOIP_CNT 셀 클릭 → 팝업)

        Args:
            week_idx: 주차 인덱스 (0~3)

        Returns:
            미도입 상품 리스트
        """
        return self._collect_popup_items(week_idx, "midoip")

    def collect_unachieved_items(self, week_idx: int) -> List[Dict]:
        """3일발주 미달성 상품 목록 수집 (MIDS_CNT 셀 클릭 → 팝업)

        Args:
            week_idx: 주차 인덱스 (0~3)

        Returns:
            미달성 상품 리스트
        """
        return self._collect_popup_items(week_idx, "mids")

    def _collect_popup_items(self, week_idx: int, item_type: str) -> List[Dict]:
        """팝업에서 상품 목록 수집 (공통)

        미도입수(midoip) 또는 미달성수(mids) 셀을 Selenium 실제 클릭으로
        팝업을 열고, dsDetail 데이터셋에서 상품 목록을 읽는다.

        Note: 넥사크로는 JS 이벤트 fire만으로는 서버 트랜잭션이 발생하지 않아
              팝업 데이터가 로드되지 않음. 실제 마우스 클릭 필요.
        """
        if not self.driver:
            return []

        click_col = "MIDOIP_CNT" if item_type == "midoip" else "MIDS_CNT"
        ds_name = "dsList" if item_type == "midoip" else "dsConvenienceList"
        grid_id = "gdList" if item_type == "midoip" else "gdList2"

        # 미도입/미달성 수 확인 -> 0이면 스킵
        check_result = self._exec_ds_js(f"""
            var ds = wf.{ds_name};
            if (!ds) return {{error: '{ds_name} not found'}};
            var cnt_val = ds.getColumn({week_idx}, '{click_col}');
            if (cnt_val && typeof cnt_val === 'object' && cnt_val.hi !== undefined)
                cnt_val = cnt_val.hi;
            if (!cnt_val || cnt_val == 0) return {{skip: true, count: 0}};
            return {{count: cnt_val}};
        """)

        if isinstance(check_result, dict) and check_result.get("skip"):
            return []

        # 1. 그리드에서 대상 셀의 실제 화면 좌표를 구하여 Selenium으로 클릭
        cell_rect = self.driver.execute_script(f"""
            try {{
                var app = nexacro.getApplication();
                var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form;
                var wf = form.{DS_PATH};
                var grid = wf.div2.form.{grid_id};
                if (!grid) return {{error: 'grid not found'}};

                // {click_col} 바인딩된 셀 인덱스 찾기
                var cellIdx = -1;
                for (var i = 0; i < grid.getCellCount('body'); i++) {{
                    var txt = grid.getCellProperty('body', i, 'text');
                    if (txt && txt.indexOf('{click_col}') >= 0) {{
                        cellIdx = i;
                        break;
                    }}
                }}
                // 폴백: midoip=6번째(인덱스5), mids=5번째(인덱스4) 열
                if (cellIdx < 0) cellIdx = {5 if item_type == "midoip" else 4};

                // 셀 DOM 요소의 화면 좌표 계산
                var cellElem = grid._getBodyCellElem({week_idx}, cellIdx);
                if (cellElem && cellElem._element_node) {{
                    var rect = cellElem._element_node.getBoundingClientRect();
                    return {{
                        x: Math.round(rect.left + rect.width / 2),
                        y: Math.round(rect.top + rect.height / 2),
                        cellIdx: cellIdx
                    }};
                }}

                // 폴백: 그리드 자체 좌표에서 계산
                var gElem = grid._control_element;
                if (gElem && gElem._element_node) {{
                    var gRect = gElem._element_node.getBoundingClientRect();
                    // 대략적 위치 추정 (헤더 30px + 행높이 20px * row)
                    var estY = gRect.top + 30 + 20 * {week_idx} + 10;
                    var cols = grid.getCellCount('body');
                    var colW = gRect.width / cols;
                    var estX = gRect.left + colW * cellIdx + colW / 2;
                    return {{x: Math.round(estX), y: Math.round(estY), cellIdx: cellIdx, estimated: true}};
                }}

                return {{error: 'cannot find cell element'}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        if isinstance(cell_rect, dict) and cell_rect.get("error"):
            logger.warning(f"셀 좌표 획득 실패 (week={week_idx}, type={item_type}): {cell_rect['error']}")
            return []

        # Selenium ActionChains로 실제 클릭
        from selenium.webdriver.common.action_chains import ActionChains
        try:
            actions = ActionChains(self.driver)
            actions.move_by_offset(cell_rect["x"], cell_rect["y"]).click().perform()
            actions.move_by_offset(-cell_rect["x"], -cell_rect["y"]).perform()
        except Exception as e:
            logger.warning(f"셀 클릭 실패 (week={week_idx}, type={item_type}): {e}")
            return []

        time.sleep(3)

        # 2. 팝업 프레임에서 dsDetail 수집
        popup_frame = NEW_PRODUCT_POPUP_FRAME_ID
        result = self.driver.execute_script(f"""
            try {{
                var app = nexacro.getApplication();
                var parentFrame = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID};
                var popup = parentFrame.{popup_frame};
                if (!popup || !popup.form) return {{error: 'popup not found'}};
                var ds = popup.form.dsDetail;
                if (!ds) return {{error: 'dsDetail not found'}};
                var cnt = ds.getRowCount();
                var rows = [];
                var cols = ['ORD_PSS_NM','WEEK','SMALL_NM','ITEM_CD',
                            'ITEM_NM','WEEK_CONT','DS_YN'];
                for (var r = 0; r < cnt; r++) {{
                    var row = {{}};
                    for (var c = 0; c < cols.length; c++) {{
                        var v = ds.getColumn(r, cols[c]);
                        if (v && typeof v === 'object' && v.hi !== undefined) v = v.hi;
                        row[cols[c]] = v;
                    }}
                    rows.push(row);
                }}
                return rows;
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        # 3. 팝업 닫기
        self._close_popup()

        if isinstance(result, dict) and result.get("error"):
            logger.warning(f"팝업 데이터 수집 실패 (week={week_idx}, type={item_type}): {result['error']}")
            return []

        items = result or []
        normalized = []
        for item in items:
            normalized.append({
                "item_cd": item.get("ITEM_CD", ""),
                "item_nm": item.get("ITEM_NM", ""),
                "small_nm": item.get("SMALL_NM", ""),
                "ord_pss_nm": item.get("ORD_PSS_NM", ""),
                "week_cont": item.get("WEEK_CONT", ""),
                "ds_yn": item.get("DS_YN", ""),
            })
        return normalized

    def _close_popup(self) -> None:
        """팝업 닫기 (JS close + 폴백 Selenium 클릭)"""
        try:
            self.driver.execute_script(f"""
                try {{
                    var app = nexacro.getApplication();
                    var parentFrame = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID};
                    var popup = parentFrame.{NEW_PRODUCT_POPUP_FRAME_ID};
                    if (popup && popup.form) {{
                        popup.form.close();
                    }}
                }} catch(e) {{}}
            """)
            time.sleep(0.5)
        except Exception:
            pass

    # ─── 전체 수집 + DB 저장 ───

    def collect_and_save(self) -> Dict:
        """전체 데이터 수집 + DB 저장

        Returns:
            수집 결과 요약 dict
        """
        result = {
            "success": False,
            "weekly_count": 0,
            "monthly": None,
            "missing_items": {},
            "unachieved_items": {},
        }

        try:
            # 1. 주차별 도입률 수집
            doip_rows = self.collect_weekly_doip()
            ds_rows = self.collect_weekly_ds()
            detail_rows = self.collect_detail_total()
            month_data = self.collect_detail_month()

            if not doip_rows:
                logger.error("도입률 데이터 수집 실패 (dsList 비어있음)")
                return result

            # month_ym 결정 (YYYY_CD + N_MNTH_CD → "202601")
            yyyy = str(doip_rows[0].get("YYYY_CD", ""))
            mm = str(doip_rows[0].get("N_MNTH_CD", "")).zfill(2)
            month_ym = f"{yyyy}{mm}" if yyyy and mm else datetime.now().strftime("%Y%m")

            logger.info(f"신상품 도입 현황 수집: {month_ym} ({len(doip_rows)}주차)")

            # 2. 주차별 저장
            for i, doip in enumerate(doip_rows):
                week_no = int(doip.get("N_WEEK_CD", i + 1))
                ds = ds_rows[i] if i < len(ds_rows) else {}
                detail = detail_rows[i] if i < len(detail_rows) else {}

                week_data = {
                    "period": doip.get("PERIOD"),
                    "doip_rate": self._to_num(doip.get("DOIP_RATE")),
                    "item_cnt": self._to_num(doip.get("ITEM_CNT")),
                    "item_ad_cnt": self._to_num(doip.get("ITEM_AD_CNT")),
                    "doip_cnt": self._to_num(doip.get("DOIP_CNT")),
                    "midoip_cnt": self._to_num(doip.get("MIDOIP_CNT")),
                    "ds_rate": self._to_num(ds.get("DS_RATE")),
                    "ds_item_cnt": self._to_num(ds.get("ITEM_CNT")),
                    "ds_cnt": self._to_num(ds.get("DS_CNT")),
                    "mids_cnt": self._to_num(ds.get("MIDS_CNT")),
                    "doip_score": self._to_num(detail.get("DOIP_SCORE")),
                    "ds_score": self._to_num(detail.get("DS_SCORE")),
                    "tot_score": self._to_num(detail.get("TOT_SCORE")),
                    "supp_pay_amt": self._to_num(detail.get("SUPP_PAY_AMT")),
                    "sta_dd": doip.get("STA_DD"),
                    "end_dd": doip.get("END_DD"),
                    "week_cont": doip.get("WEEK_CONT"),
                }

                self.repo.save_weekly_status(
                    self.store_id, month_ym, week_no, week_data
                )
                result["weekly_count"] += 1

                # 3. 미도입 상품 수집 (미도입 수 > 0인 주차만)
                midoip_cnt = self._to_num(doip.get("MIDOIP_CNT")) or 0
                if midoip_cnt > 0:
                    items = self.collect_missing_items(i)
                    if items:
                        saved = self.repo.save_items(
                            self.store_id, month_ym, week_no, "midoip", items
                        )
                        result["missing_items"][week_no] = saved
                        logger.info(f"  {week_no}주차 미도입 {saved}건 저장")

                # 4. 3일발주 미달성 상품 수집
                mids_cnt = self._to_num(ds.get("MIDS_CNT")) or 0
                if mids_cnt > 0:
                    items = self.collect_unachieved_items(i)
                    if items:
                        saved = self.repo.save_items(
                            self.store_id, month_ym, week_no, "mids", items
                        )
                        result["unachieved_items"][week_no] = saved
                        logger.info(f"  {week_no}주차 미달성 {saved}건 저장")

            # 5. 월별 합계 저장
            if month_data:
                monthly_save = {
                    "doip_item_cnt": self._to_num(month_data.get("DOIP_ITEM_CNT")),
                    "doip_cnt": self._to_num(month_data.get("DOIP_CNT")),
                    "doip_rate": self._to_num(month_data.get("DOIP_RATE")),
                    "doip_score": self._to_num(month_data.get("DOIP_SCORE")),
                    "ds_item_cnt": self._to_num(month_data.get("DS_ITEM_CNT")),
                    "ds_cnt": self._to_num(month_data.get("DS_CNT")),
                    "ds_rate": self._to_num(month_data.get("DS_RATE")),
                    "ds_score": self._to_num(month_data.get("DS_SCORE")),
                    "tot_score": self._to_num(month_data.get("TOT_SCORE")),
                    "supp_pay_amt": self._to_num(month_data.get("SUPP_PAY_AMT")),
                    "next_min_score": self._to_num(month_data.get("NEXT_MIN_SCORE")),
                    "next_max_score": self._to_num(month_data.get("NEXT_MAX_SCORE")),
                    "next_supp_pay_amt": self._to_num(month_data.get("NEXT_SUPP_PAY_AMT")),
                }
                self.repo.save_monthly(self.store_id, month_ym, monthly_save)
                result["monthly"] = monthly_save
                logger.info(f"  월별 합계 저장: 종합 {monthly_save.get('tot_score')}점")

            result["success"] = True
            logger.info(f"신상품 도입 현황 수집 완료: {result['weekly_count']}주차")

        except Exception as e:
            logger.error(f"신상품 도입 현황 수집 실패: {e}", exc_info=True)

        return result
