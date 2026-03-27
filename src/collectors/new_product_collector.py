"""
신상품 도입 현황 수집기

BGF 리테일 사이트 > 점주관리 > ① 신상품 도입 현황 화면에서
도입률, 3일발주 달성률, 미도입 상품 목록 등을 수집하여 DB에 저장

수집 방식:
  1차: Direct API (/stbj460/search, /stbj460/searchDetail) — SSV XHR 직접 호출
  2차: Selenium 폴백 — 넥사크로 JS + 팝업 클릭 (Direct API 실패 시)

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
from datetime import datetime, timedelta
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

# ─── Direct API 엔드포인트 ───
STBJ460_SEARCH_URL = "/stbj460/search"
STBJ460_SEARCH_DETAIL_URL = "/stbj460/searchDetail"

# ─── SSV 세션 키 (쿠키에서 읽을 키 목록) ───
_SSV_SESSION_KEYS = [
    "SS_STORE_CD", "SS_PRE_STORE_CD", "SS_STORE_NM",
    "SS_SLC_CD", "SS_LOC_CD", "SS_ADM_SECT_CD",
    "SS_STORE_OWNER_NM", "SS_STORE_POS_QTY",
    "SS_STORE_IP", "SS_SV_EMP_NO", "SS_SSTORE_ID",
    "SS_RCV_ID", "SS_FC_CD", "SS_USER_GRP_ID",
    "SS_USER_NO", "SS_SGGD_CD", "SS_LOGIN_USER_NO",
]

# SSV 세션 키를 JS 배열 리터럴로 변환 (JS 안에서 재사용)
_SSV_SESSION_KEYS_JS = "[" + ",".join(f"'{k}'" for k in _SSV_SESSION_KEYS) + "]"

# ─── 공통 SSV XHR JS 템플릿 ───
# {url}, {biz_params_js}, {parse_mode} 를 format 으로 채워 사용
# parse_mode: "multi" (여러 Dataset) 또는 "single" (dsDetail 단일)
_SSV_XHR_JS = """
(function() {{
    try {{
        var RS = '\\x1e';
        var US = '\\x1f';

        // 1. 쿠키에서 세션변수 읽기
        var cookies = {{}};
        document.cookie.split(';').forEach(function(c) {{
            var p = c.trim().split('=');
            if (p.length >= 2) cookies[p[0].trim()] = p.slice(1).join('=');
        }});

        // 2. SSV body 구성
        var parts = ['SSV:utf-8'];
        {ss_keys_js}.forEach(function(k) {{
            parts.push(k + '=' + (cookies[k] || ''));
        }});
        {biz_params_js}
        parts.push('GV_MENU_ID=0001,SS_STBJ460_M0');
        parts.push('GV_USERFLAG=HOME');
        parts.push('GV_CHANNELTYPE=HOME');

        // 3. 동기 XHR
        var xhr = new XMLHttpRequest();
        xhr.open('POST', '{url}', false);
        xhr.setRequestHeader('Content-Type', 'text/plain;charset=UTF-8');
        xhr.send(parts.join(RS));

        if (xhr.status !== 200) return {{error: 'HTTP ' + xhr.status}};
        var resp = xhr.responseText;

        // ErrorCode 체크
        var errM = resp.match(/ErrorCode:string=([^\\x1e]*)/);
        if (errM && errM[1] !== '0') return {{error: 'ErrorCode=' + errM[1]}};

        // 4. SSV 응답 파싱
        function parseSsvSection(text) {{
            var lines = text.split(RS);
            var colNames = [];
            var rows = [];
            for (var i = 0; i < lines.length; i++) {{
                var line = lines[i];
                if (line.indexOf('_RowType_') === 0) {{
                    line.split(US).slice(1).forEach(function(h) {{
                        colNames.push(h.split(':')[0]);
                    }});
                }}
                if (line.charAt(0) === 'N' && line.charAt(1) === US) {{
                    var vals = line.split(US);
                    var row = {{}};
                    for (var v = 1; v < vals.length && (v-1) < colNames.length; v++) {{
                        row[colNames[v-1]] = vals[v];
                    }}
                    rows.push(row);
                }}
            }}
            return rows;
        }}

        {parse_block}

    }} catch(e) {{
        return {{error: e.message}};
    }}
}})();
"""

# 다중 Dataset 파싱 블록
_PARSE_MULTI = """
        var datasets = {};
        var sections = resp.split('Dataset:');
        for (var s = 1; s < sections.length; s++) {
            var secText = sections[s];
            var dsName = secText.split(RS)[0].trim();
            datasets[dsName] = parseSsvSection(secText);
        }
        return datasets;
"""

# 단일 Dataset (dsDetail) 파싱 블록
_PARSE_SINGLE = """
        var rows = parseSsvSection(resp);
        return {rows: rows, count: rows.length};
"""


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

    # ─── 메뉴 이동 (Selenium 폴백용) ───

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

    # ═══════════════════════════════════════════════════
    # Direct API 수집 (1차 경로)
    # ═══════════════════════════════════════════════════

    def _exec_ssv_js(
        self, url: str, biz_params: List[str], parse_mode: str = "multi"
    ) -> Optional[Any]:
        """SSV XHR JS 실행 공통 래퍼

        Args:
            url: API 엔드포인트 (예: "/stbj460/search")
            biz_params: 비즈니스 파라미터 리스트 (예: ["strMonthYm=202603"])
            parse_mode: "multi" (여러 Dataset) 또는 "single" (dsDetail)

        Returns:
            파싱된 결과 dict 또는 None
        """
        if not self.driver:
            return None

        biz_js = "\n".join(f"        parts.push('{p}');" for p in biz_params)
        parse_block = _PARSE_MULTI if parse_mode == "multi" else _PARSE_SINGLE

        js = _SSV_XHR_JS.format(
            url=url,
            ss_keys_js=_SSV_SESSION_KEYS_JS,
            biz_params_js=biz_js,
            parse_block=parse_block,
        )

        try:
            result = self.driver.execute_script(js)
        except Exception as e:
            logger.warning(f"[DirectAPI] JS 실행 실패 ({url}): {e}")
            return None

        if not isinstance(result, dict):
            return None

        if result.get("error"):
            logger.warning("[DirectAPI] %s 오류: %s", url, result["error"])
            return None

        return result

    def _collect_all_via_direct_api(self, month_ym: str) -> Optional[Dict]:
        """Direct API로 메인 6개 Dataset을 한 번에 수집

        POST /stbj460/search → dsList, dsConvenienceList,
        dsDetailTotal, dsDetailMonth, dsPeriodWeekList, dsNextSuppAmt

        Args:
            month_ym: 조회 월 (예: "202603")

        Returns:
            {"dsList": [...], "dsConvenienceList": [...], ...} 또는 None
        """
        result = self._exec_ssv_js(
            url=STBJ460_SEARCH_URL,
            biz_params=[f"strMonthYm={month_ym}"],
            parse_mode="multi",
        )
        if result and result.get("dsList"):
            ds_summary = {k: len(v) for k, v in result.items()}
            logger.info("[DirectAPI] /stbj460/search 성공: %s", ds_summary)
            return result
        return None

    def _collect_detail_via_direct_api(
        self,
        store_cd: str,
        yyyy_cd: str,
        mnth_cd: str,
        n_week_cd: str,
        item_type: str,
    ) -> Optional[List[Dict]]:
        """Direct API로 미도입/미달성 상품 수집

        POST /stbj460/searchDetail → dsDetail 행 리스트

        Args:
            store_cd: 매장코드
            yyyy_cd: 연도 (예: "2026")
            mnth_cd: 월 (예: "03")
            n_week_cd: 주차 코드 (예: "09", "12")
            item_type: "midoip" (미도입) 또는 "mids" (3일 미달성)

        Returns:
            정규화된 상품 리스트 또는 None (실패)
        """
        # strType: 01=도입률(gdList), 02=간편/디저트(gdList2)
        # strDoipYN: N=미도입만 (type=01), 빈값(type=02)
        if item_type == "midoip":
            str_type, str_doip_yn, str_ds_yn = "01", "N", ""
        else:  # mids (3일 미달성)
            str_type, str_doip_yn, str_ds_yn = "02", "", ""

        result = self._exec_ssv_js(
            url=STBJ460_SEARCH_DETAIL_URL,
            biz_params=[
                f"strStoreCd={store_cd}",
                f"strYyyyCd={yyyy_cd}",
                f"strMnthCd={mnth_cd}",
                f"strNweekCd={n_week_cd}",
                f"strType={str_type}",
                f"strDoipYN={str_doip_yn}",
                f"strDsYN={str_ds_yn}",
            ],
            parse_mode="single",
        )
        if result is None:
            return None

        raw_rows = result.get("rows", [])
        logger.info(
            "[DirectAPI] searchDetail 성공 week=%s type=%s count=%d",
            n_week_cd, item_type, len(raw_rows),
        )
        return self._normalize_detail_rows(raw_rows)

    @staticmethod
    def _normalize_detail_rows(raw_rows: List[Dict]) -> List[Dict]:
        """dsDetail 행을 정규화된 dict 리스트로 변환"""
        normalized = []
        for row in raw_rows:
            normalized.append({
                "item_cd": row.get("ITEM_CD", ""),
                "item_nm": row.get("ITEM_NM", ""),
                "small_nm": row.get("SMALL_NM", ""),
                "ord_pss_nm": row.get("ORD_PSS_NM", ""),
                "week_cont": row.get("WEEK_CONT", ""),
                "ds_yn": row.get("DS_YN", ""),
            })
        return normalized

    def _enrich_items_with_mid_cd(self, items: List[Dict]) -> List[Dict]:
        """item_cd → mid_cd 매핑 (common.products 조회)

        BGF dsDetail에는 mid_cd가 없으므로 common DB에서 조회하여 보강.
        """
        if not items:
            return items

        from src.infrastructure.database.connection import DBRouter

        item_cds = [it["item_cd"] for it in items if it.get("item_cd")]
        if not item_cds:
            return items

        try:
            conn = DBRouter.get_connection(table="products")
            try:
                cursor = conn.cursor()
                placeholders = ",".join(["?"] * len(item_cds))
                cursor.execute(
                    f"SELECT item_cd, mid_cd FROM products WHERE item_cd IN ({placeholders})",
                    item_cds,
                )
                mid_cd_map = {row["item_cd"]: row["mid_cd"] for row in cursor.fetchall()}
            finally:
                conn.close()
        except Exception as e:
            logger.warning("[신상품수집] mid_cd 조회 실패: %s", e)
            mid_cd_map = {}

        enriched = 0
        for item in items:
            mid_cd = mid_cd_map.get(item.get("item_cd", ""), "")
            item["mid_cd"] = mid_cd
            if mid_cd:
                enriched += 1

        if enriched:
            logger.debug("[신상품수집] mid_cd 보강 %d/%d건", enriched, len(items))
        return items

    # ═══════════════════════════════════════════════════
    # 전체 수집 + DB 저장 (메인 진입점)
    # ═══════════════════════════════════════════════════

    def collect_and_save(self) -> Dict:
        """전체 데이터 수집 + DB 저장

        1차: Direct API로 메인 데이터 + 상세 데이터 모두 수집
        2차: 실패 시 기존 Selenium/넥사크로 JS 폴백

        Returns:
            수집 결과 요약 dict
        """
        try:
            # 1단계: Direct API 시도
            logger.info("[신상품수집] Direct API 시도")
            current_ym = datetime.now().strftime("%Y%m")
            main_data = self._collect_all_via_direct_api(current_ym)

            if main_data and main_data.get("dsList"):
                logger.info("[신상품수집] Direct API 성공 → 상세 수집 시작")
                return self._process_direct_api_data(main_data)
            else:
                raise ValueError("Direct API 빈 응답 또는 dsList 없음")

        except Exception as e:
            logger.warning("[신상품수집] Direct API 실패: %s → Selenium 폴백", e)
            return self._collect_and_save_selenium()

    def _process_direct_api_data(self, main_data: Dict) -> Dict:
        """Direct API 응답 데이터를 처리하여 DB에 저장

        Args:
            main_data: _collect_all_via_direct_api() 반환값

        Returns:
            수집 결과 요약 dict
        """
        result = {
            "success": False,
            "weekly_count": 0,
            "monthly": None,
            "missing_items": {},
            "unachieved_items": {},
            "method": "direct_api",
        }

        doip_rows = main_data["dsList"]
        ds_rows = main_data.get("dsConvenienceList", [])
        detail_rows = main_data.get("dsDetailTotal", [])
        dm = main_data.get("dsDetailMonth", [])
        month_data = dm[0] if dm else None

        if not doip_rows:
            logger.error("[신상품수집] dsList 비어있음 [direct_api]")
            return result

        # month_ym 결정
        yyyy = str(doip_rows[0].get("YYYY_CD", ""))
        mm = str(doip_rows[0].get("N_MNTH_CD", "")).zfill(2)
        month_ym = f"{yyyy}{mm}" if yyyy and mm else datetime.now().strftime("%Y%m")
        store_cd = str(doip_rows[0].get("STORE_CD", self.store_id or ""))

        logger.info(
            "[신상품수집] %s (%d주차) 수집 시작 [direct_api]",
            month_ym, len(doip_rows),
        )

        # N_WEEK_CD → dsConvenienceList 매핑 (주차 코드 기반)
        conv_by_week = {}
        for row in ds_rows:
            wk = str(row.get("N_WEEK_CD", ""))
            if wk:
                conv_by_week[wk] = row

        # 주차별 저장 + 상세 수집
        for i, doip in enumerate(doip_rows):
            week_no = int(doip.get("N_WEEK_CD", i + 1))
            n_week_cd = str(doip.get("N_WEEK_CD", "")).zfill(2)
            ds = conv_by_week.get(n_week_cd, ds_rows[i] if i < len(ds_rows) else {})
            detail = detail_rows[i] if i < len(detail_rows) else {}

            # 주차별 현황 저장
            week_data = self._build_week_data(doip, ds, detail)
            self.repo.save_weekly_status(self.store_id, month_ym, week_no, week_data)
            result["weekly_count"] += 1

            # 미도입 상품 수집 (Direct API)
            midoip_cnt = self._to_num(doip.get("MIDOIP_CNT")) or 0
            if midoip_cnt > 0:
                items = self._collect_detail_via_direct_api(
                    store_cd, yyyy, mm, n_week_cd, "midoip"
                )
                if items:
                    items = self._enrich_items_with_mid_cd(items)
                    saved = self.repo.save_items(
                        self.store_id, month_ym, week_no, "midoip", items
                    )
                    result["missing_items"][week_no] = saved
                    logger.info(
                        "  %d주차 미도입 %d건 저장 [direct_api]", week_no, saved
                    )

            # 3일발주 미달성 상품 수집 (Direct API)
            mids_cnt = self._to_num(ds.get("MIDS_CNT")) or 0
            if mids_cnt > 0:
                items = self._collect_detail_via_direct_api(
                    store_cd, yyyy, mm, n_week_cd, "mids"
                )
                if items:
                    items = self._enrich_items_with_mid_cd(items)
                    saved = self.repo.save_items(
                        self.store_id, month_ym, week_no, "mids", items
                    )
                    result["unachieved_items"][week_no] = saved
                    logger.info(
                        "  %d주차 미달성 %d건 저장 [direct_api]", week_no, saved
                    )

        # 월별 합계 저장
        if month_data:
            monthly_save = self._build_monthly_data(month_data)
            self.repo.save_monthly(self.store_id, month_ym, monthly_save)
            result["monthly"] = monthly_save
            logger.info(
                "  월별 합계 저장: 종합 %s점 [direct_api]",
                monthly_save.get("tot_score"),
            )

        # tracking 동기화 (new_product_items → new_product_3day_tracking)
        self._sync_to_tracking(month_ym)

        result["success"] = True
        logger.info(
            "[신상품수집] 수집 완료: %d주차 [direct_api]", result["weekly_count"]
        )
        return result

    # ═══════════════════════════════════════════════════
    # Selenium 폴백 수집 (2차 경로)
    # ═══════════════════════════════════════════════════

    def _collect_and_save_selenium(self) -> Dict:
        """기존 Selenium/넥사크로 JS 방식으로 전체 수집 + DB 저장

        Direct API 실패 시 호출되는 폴백 경로.
        넥사크로 프레임이 열려있어야 동작.
        """
        result = {
            "success": False,
            "weekly_count": 0,
            "monthly": None,
            "missing_items": {},
            "unachieved_items": {},
            "method": "selenium_fallback",
        }

        try:
            doip_rows = self.collect_weekly_doip()
            ds_rows = self.collect_weekly_ds()
            detail_rows = self.collect_detail_total()
            month_data = self.collect_detail_month()

            if not doip_rows:
                logger.error("[신상품수집] dsList 비어있음 [selenium]")
                return result

            yyyy = str(doip_rows[0].get("YYYY_CD", ""))
            mm = str(doip_rows[0].get("N_MNTH_CD", "")).zfill(2)
            month_ym = (
                f"{yyyy}{mm}" if yyyy and mm
                else datetime.now().strftime("%Y%m")
            )

            logger.info(
                "[신상품수집] %s (%d주차) 수집 시작 [selenium]",
                month_ym, len(doip_rows),
            )

            for i, doip in enumerate(doip_rows):
                week_no = int(doip.get("N_WEEK_CD", i + 1))
                ds = ds_rows[i] if i < len(ds_rows) else {}
                detail = detail_rows[i] if i < len(detail_rows) else {}

                week_data = self._build_week_data(doip, ds, detail)
                self.repo.save_weekly_status(
                    self.store_id, month_ym, week_no, week_data
                )
                result["weekly_count"] += 1

                # 미도입 상품 (팝업 클릭)
                midoip_cnt = self._to_num(doip.get("MIDOIP_CNT")) or 0
                if midoip_cnt > 0:
                    items = self._collect_popup_items(i, "midoip")
                    if items:
                        items = self._enrich_items_with_mid_cd(items)
                        saved = self.repo.save_items(
                            self.store_id, month_ym, week_no, "midoip", items
                        )
                        result["missing_items"][week_no] = saved
                        logger.info(
                            "  %d주차 미도입 %d건 저장 [selenium]", week_no, saved
                        )

                # 3일 미달성 상품 (팝업 클릭)
                mids_cnt = self._to_num(ds.get("MIDS_CNT")) or 0
                if mids_cnt > 0:
                    items = self._collect_popup_items(i, "mids")
                    if items:
                        items = self._enrich_items_with_mid_cd(items)
                        saved = self.repo.save_items(
                            self.store_id, month_ym, week_no, "mids", items
                        )
                        result["unachieved_items"][week_no] = saved
                        logger.info(
                            "  %d주차 미달성 %d건 저장 [selenium]", week_no, saved
                        )

            # 월별 합계 저장
            if month_data:
                monthly_save = self._build_monthly_data(month_data)
                self.repo.save_monthly(self.store_id, month_ym, monthly_save)
                result["monthly"] = monthly_save
                logger.info(
                    "  월별 합계 저장: 종합 %s점 [selenium]",
                    monthly_save.get("tot_score"),
                )

            # tracking 동기화 (new_product_items → new_product_3day_tracking)
            self._sync_to_tracking(month_ym)

            result["success"] = True
            logger.info(
                "[신상품수집] 수집 완료: %d주차 [selenium]", result["weekly_count"]
            )

        except Exception as e:
            logger.error(f"[신상품수집] Selenium 수집 실패: {e}", exc_info=True)

        return result

    # ═══════════════════════════════════════════════════
    # tracking 동기화 (new_product_items → 3day_tracking)
    # ═══════════════════════════════════════════════════

    def _sync_to_tracking(self, month_ym: str) -> None:
        """new_product_items(mids) → new_product_3day_tracking UPSERT

        수집 완료 후 분산발주 추적 테이블에 동기화.
        Phase B (get_today_new_product_orders) 가 참조할 데이터를 생성.
        """
        try:
            from src.infrastructure.database.repos.np_3day_tracking_repo import (
                NewProduct3DayTrackingRepository,
            )
            from src.application.services.new_product_order_service import (
                extract_base_name,
                calculate_interval_days,
            )

            tracking_repo = NewProduct3DayTrackingRepository(
                store_id=self.store_id
            )

            # 종료된 주차 일괄 완료 처리 (is_completed=1)
            today_str = datetime.now().strftime("%Y-%m-%d")
            closed = tracking_repo.close_expired_weeks(
                self.store_id, today_str,
            )
            if closed:
                logger.info(
                    "[신상품수집] 종료 주차 %d건 완료 처리", closed,
                )

            mids_rows = self.repo.get_mids_for_tracking(
                self.store_id, month_ym
            )
            if not mids_rows:
                logger.info("[신상품수집] mids 항목 없음 — tracking 동기화 스킵")
                return

            # get_mids_for_tracking()이 SQL에서 mid_cd 필터 적용 완료
            synced = 0
            for row in mids_rows:
                item_cd = row["item_cd"]
                item_nm = row.get("item_nm", "")
                small_nm = row.get("small_nm", "")

                week_no = row["week_no"]
                sta_dd = row.get("sta_dd", "")
                end_dd = row.get("end_dd", "")

                if not sta_dd or not end_dd:
                    # 3월(202603) new_product_status에 sta_dd/end_dd NULL인 경우
                    # 오늘부터 14일 기본 기간 부여하여 tracking 등록 가능하게 함
                    today_dt = datetime.now()
                    sta_dd = today_dt.strftime("%Y-%m-%d")
                    end_dd = (today_dt + timedelta(days=14)).strftime("%Y-%m-%d")
                    logger.info(
                        "[신상품수집] sta_dd NULL → 폴백 기간 부여: %s ~ %s (week=%d)",
                        sta_dd, end_dd, week_no,
                    )

                # week_label 생성: "202603_W09" 형식
                week_label = f"{month_ym}_W{week_no:02d}"

                # DS_YN 파싱 → BGF 기존 달성 횟수
                # 예) "1/3(미달성)" → placed=1
                ds_yn = row.get("ds_yn", "0/3")
                try:
                    placed = int(str(ds_yn).split("/")[0])
                except (ValueError, IndexError):
                    placed = 0

                # base_name 생성 (그룹핑 키)
                base_name = extract_base_name(item_nm) if item_nm else item_cd

                # 발주 간격 계산
                try:
                    interval = calculate_interval_days(sta_dd, end_dd)
                except Exception:
                    interval = 3

                # next_order_date: 오늘 또는 sta_dd 중 나중 날짜
                today_str = datetime.now().strftime("%Y-%m-%d")
                next_date = max(today_str, sta_dd) if sta_dd else today_str

                tracking_repo.upsert_tracking(
                    store_id=self.store_id,
                    week_label=week_label,
                    week_start=sta_dd,
                    week_end=end_dd,
                    product_code=item_cd,
                    product_name=item_nm,
                    sub_category=small_nm,
                    bgf_order_count=placed,
                    order_interval_days=interval,
                    next_order_date=next_date,
                    base_name=base_name,
                    product_codes=item_cd,
                )
                synced += 1

            logger.info(
                "[신상품수집] tracking 동기화 %d건 완료 "
                "(month=%s) [sync_to_tracking]",
                synced, month_ym,
            )

        except Exception as e:
            logger.warning("[신상품수집] tracking 동기화 실패: %s", e)

    # ═══════════════════════════════════════════════════
    # 공통 데이터 빌더 (Direct API / Selenium 모두 사용)
    # ═══════════════════════════════════════════════════

    def _build_week_data(self, doip: Dict, ds: Dict, detail: Dict) -> Dict:
        """주차별 저장용 데이터 dict 구성"""
        return {
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

    def _build_monthly_data(self, month_data: Dict) -> Dict:
        """월별 합계 저장용 데이터 dict 구성"""
        return {
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
            "next_supp_pay_amt": self._to_num(
                month_data.get("NEXT_SUPP_PAY_AMT")
            ),
        }

    @staticmethod
    def _to_num(val: Any) -> Any:
        """넥사크로 BigInt 객체 → 숫자 변환"""
        if isinstance(val, dict) and "hi" in val:
            return val["hi"]
        return val

    # ═══════════════════════════════════════════════════
    # Selenium 폴백용 넥사크로 JS 수집 메서드
    # ═══════════════════════════════════════════════════

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

    def collect_weekly_doip(self) -> List[Dict]:
        """dsList - 주차별 신상품 전체 도입률 수집 (Selenium 폴백용)"""
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

    def collect_weekly_ds(self) -> List[Dict]:
        """dsConvenienceList - 간편식/디저트 3일발주 달성률 (Selenium 폴백용)"""
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

    def collect_detail_total(self) -> List[Dict]:
        """dsDetailTotal - 주차별 종합 점수 (Selenium 폴백용)"""
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

    def collect_detail_month(self) -> Optional[Dict]:
        """dsDetailMonth - 월별 합계 (Selenium 폴백용)"""
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

    # ─── 팝업 클릭 수집 (Selenium 폴백용) ───

    def collect_missing_items(self, week_idx: int) -> List[Dict]:
        """미도입 상품 목록 수집 (Selenium 팝업 클릭)"""
        return self._collect_popup_items(week_idx, "midoip")

    def collect_unachieved_items(self, week_idx: int) -> List[Dict]:
        """3일발주 미달성 상품 목록 수집 (Selenium 팝업 클릭)"""
        return self._collect_popup_items(week_idx, "mids")

    def _collect_popup_items(self, week_idx: int, item_type: str) -> List[Dict]:
        """팝업에서 상품 목록 수집 (Selenium 폴백용)

        미도입수(midoip) 또는 미달성수(mids) 셀을 JS MouseEvent로
        클릭하여 팝업을 열고, dsDetail 데이터셋에서 상품 목록을 읽는다.
        """
        if not self.driver:
            return []

        click_col = "MIDOIP_CNT" if item_type == "midoip" else "MIDS_CNT"
        ds_name = "dsList" if item_type == "midoip" else "dsConvenienceList"
        grid_id = "gdList" if item_type == "midoip" else "gdList2"

        # 미도입/미달성 수 확인 → 0이면 스킵 (최대 3회 재시도)
        check_result = None
        for attempt in range(3):
            check_result = self._exec_ds_js(f"""
                var ds = wf.{ds_name};
                if (!ds) return {{error: '{ds_name} not found'}};
                var cnt_val = ds.getColumn({week_idx}, '{click_col}');
                if (!cnt_val || cnt_val == 0) return {{skip: true, count: 0}};
                return {{count: cnt_val}};
            """)
            if (isinstance(check_result, dict)
                    and check_result.get("error") == f"{ds_name} not found"):
                if attempt < 2:
                    time.sleep(1)
                    logger.debug(
                        "[팝업수집] %s 미로딩, 재시도 %d/3 week_idx=%s type=%s",
                        ds_name, attempt + 1, week_idx, item_type,
                    )
                    continue
            break

        if not isinstance(check_result, dict):
            logger.warning(
                "[팝업수집] JS 반환값 비정상 week_idx=%s type=%s result=%s",
                week_idx, item_type, check_result,
            )
            return []

        if check_result.get("error"):
            logger.warning(
                "[팝업수집] 접근 실패 week_idx=%s type=%s error=%s",
                week_idx, item_type, check_result.get("error"),
            )
            return []

        if check_result.get("skip"):
            logger.debug("[팝업수집] cnt=0 스킵 week_idx=%s type=%s", week_idx, item_type)
            return []

        logger.info(
            "[팝업수집] 클릭 시작 week_idx=%s type=%s cnt=%s",
            week_idx, item_type, check_result.get("count"),
        )

        # 1. 셀 좌표 획득 (3단계 폴백)
        cell_rect = self.driver.execute_script(f"""
            try {{
                var app = nexacro.getApplication();
                var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form;
                var wf = form.{DS_PATH};
                var grid = wf.div2.form.{grid_id};
                if (!grid) return {{error: 'grid not found'}};

                var cellIdx = -1;
                for (var i = 0; i < grid.getCellCount('body'); i++) {{
                    var txt = grid.getCellProperty('body', i, 'text');
                    if (txt && txt.indexOf('{click_col}') >= 0) {{
                        cellIdx = i; break;
                    }}
                }}
                if (cellIdx < 0) cellIdx = {5 if item_type == "midoip" else 4};

                // 방법1: _getBodyCellElem
                if (typeof grid._getBodyCellElem === 'function') {{
                    var cellElem = grid._getBodyCellElem({week_idx}, cellIdx);
                    if (cellElem && cellElem._element_node) {{
                        var rect = cellElem._element_node.getBoundingClientRect();
                        return {{x: Math.round(rect.left + rect.width/2),
                                 y: Math.round(rect.top + rect.height/2),
                                 cellIdx: cellIdx, method: 'getBodyCellElem'}};
                    }}
                }}

                // 방법2: _control_element 추정
                var gElem = grid._control_element;
                if (gElem && gElem._element_node) {{
                    var gRect = gElem._element_node.getBoundingClientRect();
                    var cols = grid.getCellCount('body');
                    var colW = gRect.width / cols;
                    return {{x: Math.round(gRect.left + colW*cellIdx + colW/2),
                             y: Math.round(gRect.top + 30 + 20*{week_idx} + 10),
                             cellIdx: cellIdx, method: 'estimated'}};
                }}

                // 방법3: DOM handle ID (가장 안정적)
                var hId = grid._control_element && grid._control_element.handle
                    ? grid._control_element.handle.id : null;
                if (hId) {{
                    var gDom = document.getElementById(hId);
                    if (gDom) {{
                        var cells = gDom.querySelectorAll('.GridCellControl');
                        var totalCols = grid.getCellCount('body');
                        var domIdx = ({week_idx} + 1) * totalCols + cellIdx;
                        if (domIdx < cells.length) {{
                            var dR = cells[domIdx].getBoundingClientRect();
                            if (dR.width > 5 && dR.height > 5) {{
                                return {{x: Math.round(dR.left + dR.width/2),
                                         y: Math.round(dR.top + dR.height/2),
                                         cellIdx: cellIdx, method: 'dom_handle_idx'}};
                            }}
                        }}
                    }}
                }}

                return {{error: 'cannot find cell element (all 3 methods failed)'}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        if isinstance(cell_rect, dict) and cell_rect.get("error"):
            logger.warning(
                "[팝업수집] 셀 좌표 실패 week=%s type=%s: %s",
                week_idx, item_type, cell_rect["error"],
            )
            return []

        logger.debug(
            "[팝업수집] 좌표 week=%s type=%s x=%s y=%s method=%s",
            week_idx, item_type,
            cell_rect.get("x"), cell_rect.get("y"), cell_rect.get("method"),
        )

        # 2. JS MouseEvent dispatch (절대 좌표 클릭)
        try:
            self.driver.execute_script("""
                var x = arguments[0], y = arguments[1];
                var el = document.elementFromPoint(x, y);
                if (!el) return;
                ['mousedown', 'mouseup', 'click'].forEach(function(evtType) {
                    var evt = new MouseEvent(evtType, {
                        bubbles: true, cancelable: true, view: window,
                        clientX: x, clientY: y, screenX: x, screenY: y
                    });
                    el.dispatchEvent(evt);
                });
            """, cell_rect["x"], cell_rect["y"])
        except Exception as e:
            logger.warning(f"셀 클릭 실패 (week={week_idx}, type={item_type}): {e}")
            return []

        time.sleep(3)

        # 3. 팝업 dsDetail 수집
        popup_result = self.driver.execute_script(f"""
            try {{
                var app = nexacro.getApplication();
                var pf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID};
                var popup = pf.{NEW_PRODUCT_POPUP_FRAME_ID};
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

        # 4. 팝업 닫기
        self._close_popup()

        if isinstance(popup_result, dict) and popup_result.get("error"):
            logger.warning(
                "팝업 데이터 수집 실패 (week=%s, type=%s): %s",
                week_idx, item_type, popup_result["error"],
            )
            return []

        return self._normalize_detail_rows(popup_result or [])

    def _close_popup(self) -> None:
        """팝업 닫기 (JS close)"""
        try:
            self.driver.execute_script(f"""
                try {{
                    var app = nexacro.getApplication();
                    var pf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID};
                    var popup = pf.{NEW_PRODUCT_POPUP_FRAME_ID};
                    if (popup && popup.form) popup.form.close();
                }} catch(e) {{}}
            """)
            time.sleep(0.5)
        except Exception:
            pass
