"""
상품 상세 정보 일괄 수집기

- 정보 미비 상품을 대상으로 CallItemDetailPopup에서 상세 정보 수집
- Direct API 우선 → Selenium 폴백 (direct-api-popup PDCA)
- common.db products + product_details 업데이트

실행:
    매일 01:00 스케줄 (run_scheduler.py)
    python run_scheduler.py --fetch-detail

플로우 (Direct API):
    1. 수집 대상 선별 (DB 조회)
    2. BGF 로그인 (쿠키 확보)
    3. DirectPopupFetcher.fetch_product_details() 배치 호출
    4. 실패 건만 Selenium 폴백

플로우 (Selenium 레거시):
    1. 수집 대상 선별 (DB 조회)
    2. BGF 로그인 + 발주 화면 진입
    3. 상품별: 바코드입력 -> 팝업 -> 추출 -> DB저장 -> 닫기
    4. 진행상황 로깅 + 에러 건너뛰기
"""

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.infrastructure.database.repos.product_detail_repo import ProductDetailRepository
from src.settings.timing import (
    BD_BARCODE_INPUT_WAIT,
    BD_POPUP_MAX_CHECKS,
    BD_POPUP_CHECK_INTERVAL,
    BD_DATA_LOAD_MAX_CHECKS,
    BD_DATA_LOAD_CHECK_INTERVAL,
    BD_POPUP_CLOSE_WAIT,
    BD_BETWEEN_ITEMS,
    BD_MAX_ITEMS_PER_RUN,
)
from src.settings.ui_config import FAIL_REASON_UI
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ProductDetailBatchCollector:
    """상품 상세 정보 일괄 수집기"""

    def __init__(self, driver: Any, store_id: Optional[str] = None):
        self.driver = driver
        self.store_id = store_id
        self._detail_repo = ProductDetailRepository()
        self._stats: Dict[str, int] = {
            "total": 0, "success": 0, "skip": 0, "fail": 0
        }

    # ──────────────────────────────────────────
    # 공개 API
    # ──────────────────────────────────────────

    def get_items_to_fetch(self, limit: int = None) -> List[str]:
        """정보 미비 상품 코드 목록 반환

        대상 기준:
        1. product_details.fetched_at IS NULL (BGF 사이트 미조회)
        2. product_details.expiration_days IS NULL (유통기한 누락)
        3. product_details.orderable_day = '일월화수목금토' (기본값 그대로)
        4. products.mid_cd IN ('999', '') (카테고리 미분류)
        5. product_details.large_cd IS NULL (대분류 미수집)

        Args:
            limit: 최대 개수 (기본: BD_MAX_ITEMS_PER_RUN = 200)

        Returns:
            수집이 필요한 item_cd 리스트
        """
        max_items = limit or BD_MAX_ITEMS_PER_RUN
        return self._detail_repo.get_items_needing_detail_fetch(max_items)

    def collect_all(self, item_codes: List[str] = None) -> Dict[str, int]:
        """일괄 수집 실행

        Direct API 우선 시도 → 실패 건만 Selenium 폴백.

        Args:
            item_codes: 수집할 상품 코드 (None이면 자동 선별)

        Returns:
            {"total": N, "success": N, "skip": N, "fail": N}
        """
        if item_codes is None:
            item_codes = self.get_items_to_fetch()

        if not item_codes:
            logger.info("[BatchDetail] 수집 대상 없음")
            return self._stats

        self._stats = {
            "total": len(item_codes), "success": 0, "skip": 0, "fail": 0
        }
        logger.info(f"[BatchDetail] 수집 시작: {len(item_codes)}개 상품")

        # ── Direct API 우선 시도 ──
        remaining = self._try_direct_api(item_codes)

        # ── Selenium 폴백 (Direct API 실패 건) ──
        if remaining:
            logger.info(
                f"[BatchDetail] Selenium 폴백: {len(remaining)}개 "
                f"(Direct API 성공: {self._stats['success']}개)"
            )
            self._collect_selenium(remaining)

        logger.info(
            f"[BatchDetail] 완료: "
            f"전체={self._stats['total']}, 성공={self._stats['success']}, "
            f"스킵={self._stats['skip']}, 실패={self._stats['fail']}"
        )
        return self._stats

    def _try_direct_api(self, item_codes: List[str]) -> List[str]:
        """Direct API로 배치 조회 시도

        Returns:
            실패한 item_cd 목록 (Selenium 폴백 대상)
        """
        try:
            from src.collectors.direct_popup_fetcher import DirectPopupFetcher

            fetcher = DirectPopupFetcher(
                self.driver, concurrency=5, timeout_ms=8000
            )

            # 캡처된 템플릿 확인
            if not fetcher.capture_template():
                logger.info("[BatchDetail] Direct API 템플릿 없음 → Selenium 전체 사용")
                return item_codes

            results = fetcher.fetch_product_details(
                item_codes,
                on_progress=lambda done, total: logger.info(
                    f"[BatchDetail/API] 진행: {done}/{total}"
                ),
            )

            # 성공 건 DB 저장
            for item_cd, data in results.items():
                try:
                    self._save_to_db(item_cd, data)
                    self._stats["success"] += 1
                except Exception as e:
                    logger.warning(f"[BatchDetail/API] {item_cd} DB 저장 실패: {e}")
                    self._stats["fail"] += 1

            # 실패 건 반환
            failed = [c for c in item_codes if c not in results]
            if failed:
                logger.info(
                    f"[BatchDetail/API] {len(results)}건 성공, "
                    f"{len(failed)}건 폴백 대상"
                )
            return failed

        except ImportError:
            logger.warning("[BatchDetail] direct_popup_fetcher 임포트 실패")
            return item_codes
        except Exception as e:
            logger.warning(f"[BatchDetail] Direct API 실패: {e}")
            return item_codes

    def _collect_selenium(self, item_codes: List[str]) -> None:
        """Selenium 방식으로 수집 (레거시)"""
        for i, item_cd in enumerate(item_codes):
            try:
                if (i + 1) % 10 == 0 or i == 0:
                    logger.info(
                        f"[BatchDetail/Selenium] 진행: {i+1}/{len(item_codes)} "
                        f"(성공={self._stats['success']}, "
                        f"실패={self._stats['fail']})"
                    )

                result = self._fetch_single_item(item_cd)

                if result is None:
                    self._stats["fail"] += 1
                elif result == "skip":
                    self._stats["skip"] += 1
                else:
                    self._save_to_db(item_cd, result)
                    self._stats["success"] += 1

            except Exception as e:
                logger.warning(f"[BatchDetail/Selenium] {item_cd} 오류: {e}")
                self._stats["fail"] += 1

            if i < len(item_codes) - 1:
                time.sleep(BD_BETWEEN_ITEMS)

    # ──────────────────────────────────────────
    # 단일 상품 수집
    # ──────────────────────────────────────────

    def _fetch_single_item(self, item_cd: str) -> Any:
        """단일 상품 팝업 조회 (홈 화면에서 실행)

        FailReasonCollector 패턴 동일:
        바코드입력 -> Enter -> Quick Search 클릭 -> 팝업대기 -> 데이터추출 -> 팝업닫기

        Returns:
            추출된 데이터 dict, 스킵이면 "skip", 실패면 None
        """
        popup_id = FAIL_REASON_UI["POPUP_ID"]  # "CallItemDetailPopup"

        # 0. 기존 Alert 처리
        self._clear_alerts()

        # 1. 바코드 입력
        if not self._input_barcode(item_cd):
            logger.warning(f"[BatchDetail] {item_cd} 바코드 입력 실패")
            return None

        time.sleep(BD_BARCODE_INPUT_WAIT)

        # 2. Enter 키 트리거
        if not self._trigger_enter():
            logger.warning(f"[BatchDetail] {item_cd} Enter 트리거 실패")
            return None

        # 3. Quick Search 드롭다운 첫 항목 클릭 -> CallItemDetailPopup
        time.sleep(0.5)
        self._click_quick_search_item()
        time.sleep(0.5)

        # 4. 팝업 대기 (폴링)
        if not self._wait_for_popup(popup_id):
            # Alert 처리 (존재하지 않는 상품 등)
            alert_text = self._clear_alerts()
            if alert_text:
                logger.info(f"[BatchDetail] {item_cd} Alert: {alert_text}")
            else:
                logger.warning(f"[BatchDetail] {item_cd} 팝업 미출현")
            return None

        # 5. 데이터 로딩 대기
        if not self._wait_for_data_load(popup_id):
            logger.warning(f"[BatchDetail] {item_cd} 데이터 로딩 타임아웃")
            self._close_popup(popup_id)
            return None

        # 6. 데이터 추출
        data = self._extract_detail(item_cd, popup_id)

        # 7. 팝업 닫기
        self._close_popup(popup_id)
        time.sleep(BD_POPUP_CLOSE_WAIT)

        return data

    def _clear_alerts(self) -> Optional[str]:
        """브라우저 Alert 다이얼로그 처리"""
        try:
            from selenium.webdriver.common.alert import Alert
            alert = Alert(self.driver)
            text = alert.text
            alert.accept()
            return text
        except Exception:
            return None

    # ──────────────────────────────────────────
    # 바코드 입력 + Enter + Quick Search (FailReasonCollector 패턴)
    # ──────────────────────────────────────────

    def _input_barcode(self, item_cd: str) -> bool:
        """edt_pluSearch에 바코드 입력 (FailReasonCollector 동일 패턴)

        1차: nexacro set_value
        2차: DOM ID
        3차: querySelector
        """
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
                var domId = arguments[1];
                var el = document.getElementById(domId);
                if (el) {
                    el.value = barcode;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    return {success: true, method: 'dom_id'};
                }
            } catch(e) {}
            try {
                var inputs = document.querySelectorAll(
                    '[id*="edt_pluSearch"] input, [id*="edt_pluSearch"]');
                for (var i = 0; i < inputs.length; i++) {
                    if (inputs[i].offsetParent !== null) {
                        inputs[i].value = barcode;
                        inputs[i].dispatchEvent(
                            new Event('input', {bubbles: true}));
                        return {success: true, method: 'querySelector'};
                    }
                }
            } catch(e) {}
            return {success: false};
        """, item_cd, FAIL_REASON_UI["BARCODE_INPUT_DOM"])

        if result and result.get("success"):
            logger.debug(
                f"[BatchDetail] 바코드 입력: {result.get('method')}")
            return True
        return False

    def _trigger_enter(self) -> bool:
        """Enter 키로 Quick Search 트리거 (FailReasonCollector 동일 패턴)"""
        result = self.driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                var edt = wf.form.edt_pluSearch;
                if (edt && typeof edt.on_fire_onkeyup === 'function') {
                    var evt = new nexacro.KeyEventInfo(
                        edt, 'onkeyup', 13, false, false, false, false);
                    edt.on_fire_onkeyup(edt, evt);
                    return {success: true, method: 'nexacro_event'};
                }
            } catch(e) {}
            try {
                var domId = arguments[0];
                var el = document.getElementById(domId);
                if (!el) {
                    var els = document.querySelectorAll(
                        '[id*="edt_pluSearch"] input');
                    el = els.length > 0 ? els[0] : null;
                }
                if (el) {
                    el.dispatchEvent(new KeyboardEvent('keyup', {
                        key: 'Enter', code: 'Enter',
                        keyCode: 13, which: 13, bubbles: true
                    }));
                    return {success: true, method: 'dom_keyevent'};
                }
            } catch(e) {}
            return {success: false};
        """, FAIL_REASON_UI["BARCODE_INPUT_DOM"])

        return bool(result and result.get("success"))

    def _click_quick_search_item(self) -> bool:
        """Quick Search 드롭다운 첫 항목 클릭 -> CallItemDetailPopup 트리거

        FailReasonCollector._click_quick_search_item() 동일 패턴.
        """
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
                                return {success: true, method: grids[i]};
                            }
                            if (typeof g.on_fire_oncelldblclick === 'function') {
                                g.on_fire_oncelldblclick(g, 0, 0);
                                return {success: true, method: grids[i]};
                            }
                        }
                    }
                }
            } catch(e) {}
            // 2차: DOM 셀 클릭
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
                             clientX: r.left + r.width/2,
                             clientY: r.top + r.height/2};
                    el.dispatchEvent(new MouseEvent('mousedown', o));
                    el.dispatchEvent(new MouseEvent('mouseup', o));
                    el.dispatchEvent(new MouseEvent('click', o));
                    return {success: true, method: 'dom_cell'};
                }
            } catch(e) {}
            return {success: false};
        """)

        if result and result.get("success"):
            logger.debug(
                f"[BatchDetail] Quick Search 클릭: {result.get('method')}")
        return bool(result and result.get("success"))

    def _wait_for_popup(self, popup_id: str) -> bool:
        """CallItemDetailPopup 출현 폴링 (BD_POPUP_MAX_CHECKS회)"""
        for _ in range(BD_POPUP_MAX_CHECKS):
            result = self.driver.execute_script("""
                var pid = arguments[0];
                try {
                    var app = nexacro.getApplication();
                    var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                    if (wf[pid] && wf[pid].form) return {found: true};
                    if (wf.popupframes && wf.popupframes[pid])
                        return {found: true};
                    if (wf.form && wf.form[pid]) return {found: true};
                } catch(e) {}
                var formEl = document.querySelector(
                    '[id$="' + pid + '.form"]');
                if (formEl && formEl.offsetParent !== null)
                    return {found: true};
                return {found: false};
            """, popup_id)
            if result and result.get("found"):
                return True
            time.sleep(BD_POPUP_CHECK_INTERVAL)
        return False

    def _wait_for_data_load(self, popup_id: str) -> bool:
        """데이터 로딩 폴링 (dsItemDetail 행 존재 확인)"""
        for _ in range(BD_DATA_LOAD_MAX_CHECKS):
            result = self.driver.execute_script("""
                var pid = arguments[0];
                try {
                    var app = nexacro.getApplication();
                    var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                    var popup = wf[pid]
                             || (wf.popupframes && wf.popupframes[pid])
                             || (wf.form && wf.form[pid]);
                    if (popup && popup.form && popup.form.dsItemDetail) {
                        if (popup.form.dsItemDetail.getRowCount() > 0)
                            return {loaded: true};
                    }
                } catch(e) {}
                return {loaded: false};
            """, popup_id)
            if result and result.get("loaded"):
                return True
            time.sleep(BD_DATA_LOAD_CHECK_INTERVAL)
        return False

    # ──────────────────────────────────────────
    # 데이터 추출 (핵심)
    # ──────────────────────────────────────────

    def _extract_detail(
        self, item_cd: str, popup_id: str
    ) -> Optional[Dict[str, Any]]:
        """CallItemDetailPopup에서 상세 정보 추출

        dsItemDetail + dsItemDetailOrd 모두 조회.
        Phase 0에서 확인된 컬럼만 추출.
        """
        result = self.driver.execute_script("""
            var popupId = arguments[0];
            var itemCd = arguments[1];

            function getPopupForm() {
                try {
                    var app = nexacro.getApplication();
                    var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                    if (wf[popupId] && wf[popupId].form)
                        return wf[popupId].form;
                    if (wf.popupframes && wf.popupframes[popupId])
                        return wf.popupframes[popupId].form;
                    if (wf.form && wf.form[popupId])
                        return wf.form[popupId].form;
                } catch(e) {}
                return null;
            }

            function dsVal(ds, row, col) {
                try {
                    var v = ds.getColumn(row, col);
                    if (v && typeof v === 'object' && v.hi !== undefined)
                        return String(v.hi);
                    return v ? String(v) : null;
                } catch(e) { return null; }
            }

            var popupForm = getPopupForm();
            if (!popupForm) return null;

            var d1 = popupForm.dsItemDetail;
            var d2 = popupForm.dsItemDetailOrd || d1;
            if (!d1 || d1.getRowCount() <= 0) return null;

            var r = {
                item_cd: dsVal(d1, 0, 'ITEM_CD')
                      || dsVal(d1, 0, 'PLU_CD') || itemCd,
                item_nm: dsVal(d1, 0, 'ITEM_NM'),

                // 카테고리 3단계 분류
                mid_cd: dsVal(d1, 0, 'MID_CD')
                     || dsVal(d1, 0, 'MCLS_CD') || null,
                large_cd: dsVal(d1, 0, 'LARGE_CD')
                       || dsVal(d1, 0, 'LCLS_CD') || null,
                large_nm: dsVal(d1, 0, 'LARGE_NM')
                       || dsVal(d1, 0, 'LCLS_NM') || null,
                mid_nm: dsVal(d1, 0, 'MID_NM')
                     || dsVal(d1, 0, 'MCLS_NM') || null,
                small_cd: dsVal(d1, 0, 'SMALL_CD')
                       || dsVal(d1, 0, 'SCLS_CD') || null,
                small_nm: dsVal(d1, 0, 'SMALL_NM')
                       || dsVal(d1, 0, 'SCLS_NM') || null,
                class_nm: dsVal(d1, 0, 'CLASS_NM') || null,

                // 유통기한
                expiration_days: parseInt(
                    dsVal(d1, 0, 'EXPIRE_DAY')) || null,

                // 발주 정보
                orderable_day: dsVal(d2, 0, 'ORD_ADAY')
                            || dsVal(d1, 0, 'ORD_ADAY') || null,
                orderable_status: dsVal(d1, 0, 'ORD_PSS_ID_NM')
                               || dsVal(d2, 0, 'ORD_PSS_CHK_NM')
                               || null,
                order_unit_qty: parseInt(
                    dsVal(d1, 0, 'ORD_UNIT_QTY')
                    || dsVal(d2, 0, 'ORD_UNIT_QTY')) || null,
                order_unit_name: dsVal(d1, 0, 'ORD_UNIT_NM') || null,
                case_unit_qty: parseInt(
                    dsVal(d1, 0, 'CASE_UNIT_QTY')) || null,

                // 가격 (Phase 0에서 컬럼명 확정 후 수정)
                // 후보: SELL_PRC, MAEGA_AMT
                sell_price: parseInt(
                    dsVal(d1, 0, 'SELL_PRC')
                    || dsVal(d1, 0, 'MAEGA_AMT')) || null,

                // 정지 사유
                order_stop_date: dsVal(d1, 0, 'ORD_STOP_YMD')
                              || dsVal(d1, 0, 'ORD_STOP_DT') || null
            };

            return r;
        """, popup_id, item_cd)

        if result and result.get("item_nm"):
            logger.info(
                f"[BatchDetail] {item_cd}: {result.get('item_nm')}, "
                f"cat={result.get('large_cd')}/{result.get('mid_cd')}/{result.get('small_cd')}, "
                f"expire={result.get('expiration_days')}, "
                f"day={result.get('orderable_day')}"
            )
            return result
        return None

    # ──────────────────────────────────────────
    # DB 저장
    # ──────────────────────────────────────────

    def _save_to_db(self, item_cd: str, data: Dict[str, Any]) -> None:
        """추출 데이터를 common.db에 저장

        1. products.mid_cd 업데이트 (현재 '999' 또는 '' 인 경우만)
        2. product_details 부분 업데이트 (NULL/기본값인 필드만)
        """
        now = datetime.now().isoformat()

        # 1. products.mid_cd 업데이트
        mid_cd = data.get("mid_cd")
        if mid_cd and mid_cd.strip():
            self._detail_repo.update_product_mid_cd(item_cd, mid_cd)

        # 2. mid_categories 대분류 정보 업데이트
        if mid_cd and mid_cd.strip():
            mid_nm = data.get("mid_nm")
            large_cd = data.get("large_cd")
            large_nm = data.get("large_nm")
            if mid_nm or large_cd or large_nm:
                self._detail_repo.upsert_mid_category_detail(
                    mid_cd=mid_cd,
                    mid_nm=mid_nm,
                    large_cd=large_cd,
                    large_nm=large_nm,
                )

        # 3. product_details 부분 업데이트
        self._detail_repo.bulk_update_from_popup(item_cd, {
            "item_nm": data.get("item_nm"),
            "expiration_days": data.get("expiration_days"),
            "orderable_day": data.get("orderable_day"),
            "orderable_status": data.get("orderable_status"),
            "order_unit_qty": data.get("order_unit_qty"),
            "order_unit_name": data.get("order_unit_name"),
            "case_unit_qty": data.get("case_unit_qty"),
            "sell_price": data.get("sell_price"),
            "large_cd": data.get("large_cd"),
            "small_cd": data.get("small_cd"),
            "small_nm": data.get("small_nm"),
            "class_nm": data.get("class_nm"),
            "fetched_at": now,
        })

    # ──────────────────────────────────────────
    # 팝업 닫기 (3단계 폴백)
    # ──────────────────────────────────────────

    def _close_popup(self, popup_id: str) -> None:
        """CallItemDetailPopup 닫기 (FailReasonCollector 패턴 동일)"""
        self.driver.execute_script("""
            var popupId = arguments[0];
            // 1차: nexacro btn_close
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
            // 2차: DOM btn_close
            try {
                var btnId = 'mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame.'
                          + popupId + '.form.btn_close';
                var el = document.getElementById(btnId);
                if (el && el.offsetParent !== null) {
                    var r = el.getBoundingClientRect();
                    var o = {
                        bubbles: true, cancelable: true, view: window,
                        clientX: r.left + r.width/2,
                        clientY: r.top + r.height/2
                    };
                    el.dispatchEvent(new MouseEvent('mousedown', o));
                    el.dispatchEvent(new MouseEvent('mouseup', o));
                    el.dispatchEvent(new MouseEvent('click', o));
                    return;
                }
            } catch(e) {}
            // 3차: querySelector
            try {
                var btns = document.querySelectorAll(
                    '[id*="' + popupId + '"][id*="btn_close"]');
                for (var i = 0; i < btns.length; i++) {
                    if (btns[i].offsetParent !== null) {
                        btns[i].click();
                        return;
                    }
                }
            } catch(e) {}
        """, popup_id)
