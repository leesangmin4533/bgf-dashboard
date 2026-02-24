"""
폐기 전표 수집기 (WasteSlipCollector)

BGF 리테일 사이트 > 검수전표 > 통합 전표 조회 화면에서
전표구분='폐기'(CODE=10) 필터링 후 폐기 전표 헤더 + 상세 품목 수집

구조:
  - 프레임 ID: STGJ020_M0
  - 메뉴 경로: 검수전표 > 통합 전표 조회
  - 전표구분 콤보: cbChitDiv (innerdataset: dsChitDiv), 폐기 CODE = "10"
  - 검색 파라미터: dsSearch (strFromDt, strToDt, strChitDiv, strStoreCd)
  - 결과 데이터셋: dsList (21 컬럼)
  - 조회 실행: fn_commBtn_10() 또는 F_10.click()
  - 상세 품목: STGJ020_P1 팝업 > dsListType0~4 (dsGsTmp 강제갱신 패턴)
"""

import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.settings.ui_config import FRAME_IDS, MENU_TEXT, SUBMENU_TEXT
from src.utils.nexacro_helpers import navigate_menu, close_tab_by_frame_id
from src.infrastructure.database.repos.waste_slip_repo import WasteSlipRepository
from src.settings.timing import (
    DATA_LOAD_WAIT,
    COLLECTION_RETRY_WAIT,
    COLLECTION_MAX_RETRIES,
    WS_DSGS_SETUP_WAIT,
    WS_POPUP_CLOSE_WAIT,
    WS_POPUP_OPEN_WAIT,
    WS_SEARCH_WAIT,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 폐기 전표구분 CODE
WASTE_CHIT_DIV_CODE = "10"


class WasteSlipCollector:
    """폐기 전표 수집기

    검수전표 > 통합 전표 조회 화면에서 폐기 전표 데이터를 수집합니다.
    ReceivingCollector 패턴 기반.
    """

    FRAME_ID = FRAME_IDS.get("WASTE_SLIP", "STGJ020_M0")

    def __init__(
        self,
        driver: Optional[Any] = None,
        store_id: Optional[str] = None,
    ) -> None:
        self.driver = driver
        self.store_id = store_id
        self.repo = WasteSlipRepository(store_id=self.store_id)

    def set_driver(self, driver: Any) -> None:
        """드라이버 설정"""
        self.driver = driver

    # ================================================================
    # 메뉴 탐색
    # ================================================================

    def navigate_to_waste_slip_menu(self) -> bool:
        """검수전표 > 통합 전표 조회 메뉴로 이동"""
        if not self.driver:
            logger.error("드라이버가 설정되지 않음")
            return False

        logger.info("통합 전표 조회 메뉴 이동...")

        try:
            success = navigate_menu(
                self.driver,
                MENU_TEXT["RECEIVING"],      # "검수전표"
                SUBMENU_TEXT["WASTE_SLIP"],   # "통합 전표 조회"
                self.FRAME_ID,
            )
            if success:
                logger.info(f"통합 전표 조회 이동 성공 (프레임: {self.FRAME_ID})")
                time.sleep(DATA_LOAD_WAIT)
            else:
                logger.error("통합 전표 조회 메뉴 이동 실패")
            return success
        except Exception as e:
            logger.error(f"메뉴 이동 예외: {e}")
            return False

    def close_waste_slip_menu(self) -> None:
        """통합 전표 조회 탭 닫기"""
        if not self.driver:
            return
        try:
            close_tab_by_frame_id(self.driver, self.FRAME_ID)
            logger.info("통합 전표 조회 탭 닫기 완료")
        except Exception as e:
            logger.warning(f"탭 닫기 실패: {e}")

    # ================================================================
    # 날짜 + 필터 설정
    # ================================================================

    def _set_date_range_and_filter(
        self, from_date: str, to_date: str
    ) -> bool:
        """날짜 범위 설정 + 전표구분='폐기'(10) 필터 설정

        Args:
            from_date: 시작일 (YYYYMMDD)
            to_date: 종료일 (YYYYMMDD)

        Returns:
            성공 여부
        """
        result = self.driver.execute_script(
            """
            var fid = arguments[0];
            var fromDt = arguments[1];
            var toDt = arguments[2];
            var chitDiv = arguments[3];
            try {
                var app = nexacro.getApplication();
                var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                var wf = form.div_workForm.form;

                // 1) 전표구분 콤보 설정 (폐기 = CODE "10")
                var dsChitDiv = wf.dsChitDiv;
                var targetIdx = -1;
                for (var r = 0; r < dsChitDiv.getRowCount(); r++) {
                    if (dsChitDiv.getColumn(r, 'CODE') === chitDiv) {
                        targetIdx = r;
                        break;
                    }
                }
                if (targetIdx < 0) return {error: 'CODE ' + chitDiv + ' not found'};

                var cb = wf.div2.form.divSearch.form.cbChitDiv;
                cb.set_index(targetIdx);

                // 2) dsSearch 파라미터 설정
                var dsSearch = wf.dsSearch;
                dsSearch.setColumn(0, 'strChitDiv', chitDiv);
                dsSearch.setColumn(0, 'strFromDt', fromDt);
                dsSearch.setColumn(0, 'strToDt', toDt);

                return {
                    success: true,
                    chitDiv: chitDiv,
                    fromDt: fromDt,
                    toDt: toDt,
                    comboText: cb.text
                };
            } catch(e) {
                return {error: e.message};
            }
            """,
            self.FRAME_ID,
            from_date,
            to_date,
            WASTE_CHIT_DIV_CODE,
        )

        if result and result.get("success"):
            logger.info(
                f"필터 설정: {result.get('comboText')} / "
                f"{from_date}~{to_date}"
            )
            return True
        else:
            logger.error(f"필터 설정 실패: {result}")
            return False

    def _execute_search(self) -> bool:
        """조회 실행 (F10)"""
        result = self.driver.execute_script(
            """
            var fid = arguments[0];
            try {
                var form = nexacro.getApplication()
                    .mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                if (typeof form.fn_commBtn_10 === 'function') {
                    form.fn_commBtn_10();
                    return {method: 'fn_commBtn_10', success: true};
                }
                var cmmbtn = form.div_cmmbtn;
                if (cmmbtn && cmmbtn.form && cmmbtn.form.F_10) {
                    cmmbtn.form.F_10.click();
                    return {method: 'F_10', success: true};
                }
                return {error: 'no search method found'};
            } catch(e) {
                return {error: e.message};
            }
            """,
            self.FRAME_ID,
        )

        if result and result.get("success"):
            logger.info(f"조회 실행: {result.get('method')}")
            time.sleep(DATA_LOAD_WAIT + 1)  # 서버 응답 대기
            return True
        else:
            logger.error(f"조회 실행 실패: {result}")
            return False

    # ================================================================
    # 데이터 수집
    # ================================================================

    def _collect_waste_slips(self) -> List[Dict[str, Any]]:
        """dsList에서 폐기 전표 데이터 수집

        Returns:
            폐기 전표 목록 [{CHIT_YMD, CHIT_NO, ITEM_CNT, ...}, ...]
        """
        data = self.driver.execute_script(
            """
            var fid = arguments[0];
            try {
                var form = nexacro.getApplication()
                    .mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                var ds = form.div_workForm.form.dsList;
                if (!ds) return {error: 'dsList not found'};

                var cols = [
                    'CHIT_FLAG', 'CHIT_ID', 'CHIT_ID_NM',
                    'CHIT_YMD', 'CHIT_NO', 'CHIT_ID_NO',
                    'ITEM_CNT', 'CENTER_CD', 'CENTER_NM',
                    'WONGA_AMT', 'MAEGA_AMT',
                    'NAP_PLAN_YMD', 'CRE_YMDHMS', 'CONF_ID',
                    'LARGE_CD', 'LSTORE_NM', 'RSTORE_NM',
                    'RET_CHIT_NO', 'MAEIP_CHIT_NO'
                ];

                var rows = [];
                for (var r = 0; r < ds.getRowCount(); r++) {
                    var row = {};
                    for (var c = 0; c < cols.length; c++) {
                        var val = ds.getColumn(r, cols[c]);
                        if (val && typeof val === 'object'
                            && val.hi !== undefined) val = val.hi;
                        row[cols[c]] = val;
                    }
                    rows.push(row);
                }
                return {rows: rows, count: ds.getRowCount()};
            } catch(e) {
                return {error: e.message};
            }
            """,
            self.FRAME_ID,
        )

        if not data or data.get("error"):
            logger.error(f"데이터 수집 실패: {data}")
            return []

        logger.info(f"폐기 전표 수집: {data.get('count', 0)}건")
        return data.get("rows", [])

    # ================================================================
    # 팝업 기반 상세 품목 수집 (STGJ020_P1)
    # ================================================================

    def _set_dsgs_params(self, idx: int) -> bool:
        """dsList 행 기반으로 dsGs에 gvVar 파라미터 설정

        Args:
            idx: dsList 행 인덱스

        Returns:
            성공 여부
        """
        result = self.driver.execute_script(
            """
            var fid = arguments[0];
            var idx = arguments[1];
            try {
                var form = nexacro.getApplication()
                    .mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                var wf = form.div_workForm.form;
                wf.dsList.set_rowposition(idx);

                var fields = ['CHIT_ID','CHIT_ID_NM','NAP_PLAN_YMD','CHIT_NO',
                              'CENTER_NM','CHIT_ID_NO','LSTORE_NM','RSTORE_NM',
                              'LARGE_CD','CHIT_FLAG','RET_CHIT_NO',
                              'MAEIP_CHIT_NO','CHIT_YMD'];
                var gvNums = ['04','05','06','07','08','09','10','11',
                              '12','13','14','15','18'];

                for (var i = 0; i < fields.length; i++) {
                    var val = String(wf.dsList.getColumn(idx, fields[i]) || '');
                    wf['gvVar' + gvNums[i]] = val;
                    wf.dsGs.setColumn(0, 'gvVar' + gvNums[i], val);
                }
                wf.gvVar20 = "Y";
                return {success: true};
            } catch(e) {
                return {error: e.message};
            }
            """,
            self.FRAME_ID,
            idx,
        )
        time.sleep(WS_DSGS_SETUP_WAIT)

        if result and result.get("success"):
            return True
        logger.warning(f"dsGs 설정 실패 (idx={idx}): {result}")
        return False

    def _close_existing_popup(self) -> None:
        """열려있는 모든 넥사크로 팝업 닫기 (close + destroy)"""
        self.driver.execute_script(
            """
            var popupframes = nexacro.getPopupFrames(
                nexacro.getApplication().mainframe);
            if (popupframes) {
                for (var i = popupframes.length - 1; i >= 0; i--) {
                    try {
                        popupframes[i].close();
                        popupframes[i].destroy();
                    } catch(e) {}
                }
            }
            """
        )
        time.sleep(WS_POPUP_CLOSE_WAIT)

    def _open_detail_popup(self) -> bool:
        """STGJ020_P1 상세 팝업 열기

        Returns:
            성공 여부
        """
        result = self.driver.execute_script(
            """
            var fid = arguments[0];
            try {
                var form = nexacro.getApplication()
                    .mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                var wf = form.div_workForm.form;
                var oArg = {};
                oArg.dsArg = wf.dsGs;
                oArg.strStoreCd = wf.strStoreCd;
                oArg.strStoreNm = wf.strStoreNm;
                wf.gfn_openPopup("STGJ020_P1",
                    "GJ::STGJ020_P1.xfdl", oArg,
                    "fn_popupCallback", {});
                return {success: true};
            } catch(e) {
                return {error: e.message};
            }
            """,
            self.FRAME_ID,
        )
        time.sleep(WS_POPUP_OPEN_WAIT)

        if result and result.get("success"):
            return True
        logger.warning(f"팝업 열기 실패: {result}")
        return False

    def _update_dsgs_tmp_and_search(
        self, slip: Dict[str, Any]
    ) -> bool:
        """팝업 내 dsGsTmp 강제 갱신 + fn_selSearch 재호출

        핵심: 팝업의 dsGsTmp는 첫 번째 전표값으로 캐싱됨.
        매 전표마다 gvVar 값을 갱신하고 서버 트랜잭션을 재호출해야 함.

        Args:
            slip: 전표 헤더 딕셔너리 (CHIT_ID, CHIT_NO, CHIT_YMD 등)

        Returns:
            성공 여부
        """
        chit_id = str(slip.get("CHIT_ID", "") or "")
        chit_id_nm = str(slip.get("CHIT_ID_NM", "") or "")
        nap_ymd = str(slip.get("NAP_PLAN_YMD", "") or "")
        chit_no = str(slip.get("CHIT_NO", "") or "")
        center_nm = str(slip.get("CENTER_NM", "") or "")
        chit_id_no = str(slip.get("CHIT_ID_NO", "") or "")
        chit_flag = str(slip.get("CHIT_FLAG", "") or "")
        ymd = str(slip.get("CHIT_YMD", "") or "")

        result = self.driver.execute_script(
            """
            var chitId = arguments[0];
            var chitIdNm = arguments[1];
            var napYmd = arguments[2];
            var chitNo = arguments[3];
            var centerNm = arguments[4];
            var chitIdNo = arguments[5];
            var chitFlag = arguments[6];
            var ymd = arguments[7];
            try {
                var popupframes = nexacro.getPopupFrames(
                    nexacro.getApplication().mainframe);
                if (!popupframes || popupframes.length === 0)
                    return {error: 'no popup'};
                var popup = popupframes[popupframes.length - 1];
                if (!popup || !popup.form)
                    return {error: 'no form'};
                var pf = popup.form;

                // dsGsTmp 갱신
                pf.dsGsTmp.setColumn(0, 'gvVar04', chitId);
                pf.dsGsTmp.setColumn(0, 'gvVar05', chitIdNm);
                pf.dsGsTmp.setColumn(0, 'gvVar06', napYmd);
                pf.dsGsTmp.setColumn(0, 'gvVar07', chitNo);
                pf.dsGsTmp.setColumn(0, 'gvVar08', centerNm);
                pf.dsGsTmp.setColumn(0, 'gvVar09', chitIdNo);
                pf.dsGsTmp.setColumn(0, 'gvVar13', chitFlag);
                pf.dsGsTmp.setColumn(0, 'gvVar18', ymd);

                // 이전 데이터 클리어 + 행 초기화
                for (var t = 0; t <= 4; t++) {
                    var ds = pf['dsListType' + t];
                    if (ds && ds.clearData) ds.clearData();
                    if (ds && ds.deleteAll) ds.deleteAll();
                }

                // 서버 트랜잭션 재호출
                if (typeof pf.fn_selSearch === 'function') {
                    pf.fn_selSearch();
                }
                return {success: true};
            } catch(e) {
                return {error: e.message};
            }
            """,
            chit_id,
            chit_id_nm,
            nap_ymd,
            chit_no,
            center_nm,
            chit_id_no,
            chit_flag,
            ymd,
        )
        time.sleep(WS_SEARCH_WAIT)

        if result and result.get("success"):
            return True
        logger.warning(f"dsGsTmp 갱신 실패 (전표={chit_no}): {result}")
        return False

    def _extract_popup_items(self) -> List[Dict[str, Any]]:
        """팝업 dsListType0~4에서 품목 데이터 추출

        Returns:
            품목 딕셔너리 리스트
        """
        data = self.driver.execute_script(
            """
            try {
                var popupframes = nexacro.getPopupFrames(
                    nexacro.getApplication().mainframe);
                if (!popupframes || popupframes.length === 0)
                    return {error: 'no popup'};
                var popup = popupframes[popupframes.length - 1];
                if (!popup || !popup.form)
                    return {error: 'no form'};
                var pf = popup.form;
                var result = {items: []};

                result.gvVar07 = String(
                    pf.dsGsTmp.getColumn(0, 'gvVar07') || '');

                for (var t = 0; t <= 4; t++) {
                    var ds = pf['dsListType' + t];
                    if (ds && ds.getRowCount && ds.getRowCount() > 0) {
                        var cc = ds.getColCount();
                        var cols = [];
                        for (var c = 0; c < cc; c++)
                            cols.push(ds.getColID(c));

                        for (var r = 0; r < ds.getRowCount(); r++) {
                            var row = {_dsType: t};
                            for (var c = 0; c < cols.length; c++) {
                                var val = ds.getColumn(r, cols[c]);
                                if (val && typeof val === 'object'
                                    && val.hi !== undefined) val = val.hi;
                                row[cols[c]] = val;
                            }
                            result.items.push(row);
                        }
                    }
                }
                return result;
            } catch(e) {
                return {error: e.message};
            }
            """
        )

        if not data or data.get("error"):
            logger.warning(f"품목 추출 실패: {data}")
            return []

        return data.get("items", [])

    def _collect_detail_items(
        self, slip_list: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """전표 목록 순회하며 팝업 기반 상세 품목 수집

        Args:
            slip_list: 전표 헤더 목록 (dsList에서 수집된 것)

        Returns:
            전체 상세 품목 리스트 (CHIT_YMD, CHIT_NO 포함)
        """
        all_items: List[Dict[str, Any]] = []

        for idx, slip in enumerate(slip_list):
            chit_no = str(slip.get("CHIT_NO", "") or "")
            chit_ymd = str(slip.get("CHIT_YMD", "") or "")
            item_cnt = slip.get("ITEM_CNT") or 0

            logger.info(
                f"  [{idx+1}/{len(slip_list)}] "
                f"{chit_ymd} 전표={chit_no} 예상품목={item_cnt}"
            )

            try:
                # 1) dsGs 파라미터 설정
                if not self._set_dsgs_params(idx):
                    logger.warning(f"  전표 {chit_no}: dsGs 설정 실패, skip")
                    continue

                # 2) 기존 팝업 닫기
                self._close_existing_popup()

                # 3) 팝업 열기
                if not self._open_detail_popup():
                    logger.warning(f"  전표 {chit_no}: 팝업 열기 실패, skip")
                    continue

                # 4) dsGsTmp 강제 갱신 + fn_selSearch
                if not self._update_dsgs_tmp_and_search(slip):
                    logger.warning(
                        f"  전표 {chit_no}: dsGsTmp 갱신 실패, skip"
                    )
                    continue

                # 5) 데이터 추출
                items = self._extract_popup_items()

                if items:
                    # 각 품목에 전표 정보 부착
                    for item in items:
                        item["CHIT_YMD"] = chit_ymd
                        item["CHIT_NO"] = chit_no
                    all_items.extend(items)
                    logger.info(f"    -> {len(items)}건 추출")
                else:
                    logger.info(f"    -> 품목 0건")

            except Exception as e:
                logger.warning(f"  전표 {chit_no} 상세 수집 오류: {e}")
                continue

        # 최종 팝업 정리
        try:
            self._close_existing_popup()
        except Exception:
            pass

        logger.info(
            f"[WasteSlip] 상세 품목 수집 완료: "
            f"전표 {len(slip_list)}건 -> 품목 {len(all_items)}건"
        )
        return all_items

    # ================================================================
    # 공개 API
    # ================================================================

    def collect_waste_slips(
        self,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        days: int = 1,
        save_to_db: bool = True,
    ) -> Dict[str, Any]:
        """폐기 전표 수집 (메뉴 이동 -> 필터 -> 조회 -> 수집 -> 저장)

        Args:
            from_date: 시작일 (YYYYMMDD). None이면 today - days (기본 전날)
            to_date: 종료일 (YYYYMMDD). None이면 today
            days: from_date 미지정 시 기본 조회 기간 (일, 기본 1=전날+당일)
            save_to_db: DB 저장 여부

        Returns:
            {success, count, saved, date_summary, ...}
        """
        if not self.driver:
            return {"success": False, "error": "driver not set"}

        now = datetime.now()
        if not to_date:
            to_date = now.strftime("%Y%m%d")
        if not from_date:
            from_dt = now - timedelta(days=days)
            from_date = from_dt.strftime("%Y%m%d")

        logger.info(f"[WasteSlip] 수집 시작: {from_date}~{to_date}")

        # 1) 메뉴 이동
        if not self.navigate_to_waste_slip_menu():
            return {"success": False, "error": "menu navigation failed"}

        try:
            # 2) 필터 설정
            if not self._set_date_range_and_filter(from_date, to_date):
                return {"success": False, "error": "filter setup failed"}

            # 3) 조회 실행
            if not self._execute_search():
                return {"success": False, "error": "search execution failed"}

            # 4) 데이터 수집
            slips = self._collect_waste_slips()

            if not slips:
                logger.info("[WasteSlip] 폐기 전표 없음")
                return {
                    "success": True,
                    "count": 0,
                    "saved": 0,
                    "from_date": from_date,
                    "to_date": to_date,
                }

            # 5) 상세 품목 수집 (팝업 기반)
            detail_items: List[Dict[str, Any]] = []
            try:
                detail_items = self._collect_detail_items(slips)
            except Exception as detail_err:
                logger.warning(
                    f"[WasteSlip] 상세 품목 수집 실패 "
                    f"(헤더 수집은 정상): {detail_err}"
                )

            # 6) 날짜별 집계
            date_summary = {}
            for slip in slips:
                ymd = slip.get("CHIT_YMD", "")
                if ymd not in date_summary:
                    date_summary[ymd] = {
                        "slip_count": 0,
                        "item_count": 0,
                        "wonga_total": 0,
                        "maega_total": 0,
                    }
                date_summary[ymd]["slip_count"] += 1
                date_summary[ymd]["item_count"] += slip.get("ITEM_CNT") or 0
                date_summary[ymd]["wonga_total"] += slip.get("WONGA_AMT") or 0
                date_summary[ymd]["maega_total"] += slip.get("MAEGA_AMT") or 0

            # 7) DB 저장
            saved = 0
            detail_saved = 0
            if save_to_db:
                saved = self.repo.save_waste_slips(slips, self.store_id)
                logger.info(f"[WasteSlip] 헤더 DB 저장: {saved}건")

                if detail_items:
                    detail_saved = self.repo.save_waste_slip_items(
                        detail_items, self.store_id
                    )
                    logger.info(
                        f"[WasteSlip] 상세 품목 DB 저장: {detail_saved}건"
                    )

            result = {
                "success": True,
                "count": len(slips),
                "saved": saved,
                "detail_count": len(detail_items),
                "detail_saved": detail_saved,
                "from_date": from_date,
                "to_date": to_date,
                "date_summary": date_summary,
            }

            logger.info(
                f"[WasteSlip] 수집 완료: 전표 {len(slips)}건 "
                f"(헤더 DB {saved}건), "
                f"상세 품목 {len(detail_items)}건 "
                f"(DB {detail_saved}건), "
                f"기간 {from_date}~{to_date}"
            )
            return result

        except Exception as e:
            logger.error(f"[WasteSlip] 수집 오류: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}

        finally:
            self.close_waste_slip_menu()

    def collect_today_waste_slips(
        self, save_to_db: bool = True
    ) -> Dict[str, Any]:
        """전날 + 당일 폐기 전표 수집 (기본 수집 단위)"""
        now = datetime.now()
        yesterday = (now - timedelta(days=1)).strftime("%Y%m%d")
        today = now.strftime("%Y%m%d")
        return self.collect_waste_slips(
            from_date=yesterday, to_date=today, save_to_db=save_to_db
        )
