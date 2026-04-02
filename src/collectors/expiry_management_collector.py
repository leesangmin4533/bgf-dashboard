"""
BGF "상품 유통기한 관리" 화면 수집기 (STCM130_M0)

커뮤니케이션 > 상품 유통기한 관리 메뉴에서
직원이 PDA로 등록한 철수예정일 데이터를 수집하여 DB에 저장.

데이터셋: dsList (8컬럼)
  LARGE_NM, MID_NM, ITEM_NM, ITEM_CD, EXPIRE_YMD, STORE_CD, DEL_FLAG, NOW_QTY
"""

import json
import time
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

FRAME_ID = "STCM130_M0"

# 메뉴 이동 JS — TopFrame.form.gfn_openMenuId 사용
MENU_JS = """
(function() {
    try {
        var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.TopFrame.form;
        form.gfn_openMenuId('STCM130_M0');
        return true;
    } catch(e) { return 'ERR:' + String(e); }
})();
"""

# form 구조 탐색 JS (실제 컴포넌트 구조를 런타임에 파악)
DISCOVER_JS = """
(function() {
    try {
        var f = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet.STCM130_M0;
        if (!f) return JSON.stringify({error: 'frame_not_found'});
        if (!f.form) return JSON.stringify({error: 'form_not_loaded'});

        var form = f.form;
        var info = {components: [], datasets: [], divs: []};

        // 1단계: form 직속 컴포넌트 탐색
        if (form.components) {
            for (var i = 0; i < form.components.length; i++) {
                var c = form.components[i];
                info.components.push(c.id + '(' + (c.typeid || c.constructor.name || '?') + ')');
            }
        }

        // 2단계: objects에서 Dataset 찾기
        if (form.objects) {
            for (var i = 0; i < form.objects.length; i++) {
                var o = form.objects[i];
                if (o.id && (o.id.indexOf('ds') === 0 || o.id.indexOf('DS') === 0 || o.id.indexOf('Ds') === 0)) {
                    info.datasets.push(o.id + '(rows=' + (o.rowcount || 0) + ')');
                }
            }
        }

        // 3단계: div_workForm 탐색
        var wf = null;
        if (form.div_workForm) {
            wf = form.div_workForm.form;
            if (wf && wf.components) {
                for (var i = 0; i < wf.components.length; i++) {
                    var c = wf.components[i];
                    info.divs.push('wf.' + c.id + '(' + (c.typeid || '?') + ')');
                }
            }
            // div_search 하위도 탐색
            if (wf && wf.div_search && wf.div_search.form && wf.div_search.form.components) {
                var ds = wf.div_search.form;
                for (var i = 0; i < ds.components.length; i++) {
                    var c = ds.components[i];
                    info.divs.push('wf.div_search.' + c.id + '(' + (c.typeid || '?') + ')');
                }
            }
        }

        // 4단계: div_cmmbtn 탐색 (조회 버튼)
        if (form.div_cmmbtn && form.div_cmmbtn.form && form.div_cmmbtn.form.components) {
            var btn = form.div_cmmbtn.form;
            for (var i = 0; i < btn.components.length; i++) {
                var c = btn.components[i];
                info.divs.push('btn.' + c.id + '(' + (c.typeid || '?') + ')');
            }
        }

        return JSON.stringify(info);
    } catch(e) { return JSON.stringify({error: 'discover_error: ' + String(e)}); }
})();
"""

# 조회 실행 JS (년월 설정 + 현재고 체크 + 조회)
# 검증된 구조: div_search.form.meSearchYm.form.mae_monthCal + div_cmmbtn.form.F_10
SEARCH_JS_TEMPLATE = """
(function() {{
    try {{
        var f = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet.STCM130_M0;
        if (!f || !f.form) return 'ERR:frame_not_ready';

        var form = f.form;
        var wf = form.div_workForm.form;
        var ds = wf.div_search.form;

        // 조회년월 설정 — meSearchYm > mae_monthCal
        var ymComp = ds.meSearchYm;
        if (ymComp && ymComp.form && ymComp.form.mae_monthCal) {{
            ymComp.form.mae_monthCal.set_value('{ym}');
        }}

        // 현재고 있는 상품만 조회 체크
        var chk = ds.chk_nowQtyYn;
        if (chk) chk.set_value('{stock_only}');

        // 조회 버튼 클릭
        var btn = form.div_cmmbtn.form.F_10;
        if (btn) {{
            btn.click();
            return true;
        }}
        return 'ERR:F_10_not_found';
    }} catch(e) {{ return 'ERR:' + String(e); }}
}})();
"""

# 데이터셋 추출 JS — form과 wf.objects 양쪽에서 dsList 탐색
EXTRACT_JS = """
(function() {
    try {
        var f = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet.STCM130_M0;
        if (!f || !f.form) return JSON.stringify({error: 'frame_not_ready', rows: []});

        var form = f.form;
        var wf = form.div_workForm ? form.div_workForm.form : null;

        // dsList 찾기 — form.objects + wf.objects 양쪽 탐색
        var ds = null;
        var searchTargets = [];
        if (form.objects) searchTargets.push(form.objects);
        if (wf && wf.objects) searchTargets.push(wf.objects);

        for (var t = 0; t < searchTargets.length && !ds; t++) {
            var objs = searchTargets[t];
            for (var i = 0; i < objs.length; i++) {
                if (objs[i].id === 'dsList') { ds = objs[i]; break; }
            }
        }
        if (!ds) return JSON.stringify({error: 'dsList not found', rows: []});

        var rows = [];
        for (var r = 0; r < ds.rowcount; r++) {
            var qty = ds.getColumn(r, 'NOW_QTY');
            rows.push({
                item_cd: ds.getColumn(r, 'ITEM_CD'),
                item_nm: ds.getColumn(r, 'ITEM_NM'),
                large_nm: ds.getColumn(r, 'LARGE_NM'),
                mid_nm: ds.getColumn(r, 'MID_NM'),
                expire_ymd: ds.getColumn(r, 'EXPIRE_YMD'),
                store_cd: ds.getColumn(r, 'STORE_CD'),
                now_qty: (qty && qty.hi !== undefined) ? qty.hi : (qty || 0)
            });
        }
        return JSON.stringify({rows: rows, total: ds.rowcount});
    } catch(e) { return JSON.stringify({error: 'extract_error: ' + String(e), rows: []}); }
})();
"""


class ExpiryManagementCollector:
    """BGF 상품 유통기한 관리 화면 수집기"""

    def __init__(self, driver: Any, store_id: str):
        self.driver = driver
        self.store_id = store_id

    def collect(self, target_month: Optional[str] = None, stock_only: bool = True) -> Dict[str, Any]:
        """상품 유통기한 관리 데이터 수집

        Args:
            target_month: 조회년월 (YYYY-MM, None이면 현재 월)
            stock_only: 현재고 있는 상품만 조회

        Returns:
            {success, count, saved, data: [...]}
        """
        if not target_month:
            target_month = datetime.now().strftime("%Y-%m")

        ym = target_month.replace("-", "")  # YYYYMM

        try:
            # 1. 메뉴 이동
            logger.info(f"[ExpiryMgmt] 메뉴 이동: {FRAME_ID}")
            self.driver.execute_script(MENU_JS)
            time.sleep(3)

            # 프레임 로딩 대기 (form까지 준비 확인)
            frame_ready = False
            for _ in range(15):
                try:
                    check = self.driver.execute_script(f"""
                        try {{
                            var f = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID};
                            return (f && f.form) ? true : false;
                        }} catch(e) {{ return false; }}
                    """)
                    if check:
                        frame_ready = True
                        break
                except Exception:
                    pass
                time.sleep(0.5)

            if not frame_ready:
                logger.warning(f"[ExpiryMgmt] 프레임 로딩 타임아웃 ({FRAME_ID})")
                return {"success": False, "error": "frame_load_timeout", "count": 0}

            # 1.5. form 구조 진단 (디버그용 — 문제 발생 시 로그 확인)
            try:
                discover_raw = self.driver.execute_script(DISCOVER_JS)
                if discover_raw:
                    discover_data = json.loads(discover_raw)
                    if discover_data.get("error"):
                        logger.warning(f"[ExpiryMgmt] 구조 탐색 오류: {discover_data['error']}")
                    else:
                        logger.debug(f"[ExpiryMgmt] 구조: {discover_data}")
                else:
                    logger.warning("[ExpiryMgmt] 구조 탐색 결과: None")
            except Exception as e:
                logger.debug(f"[ExpiryMgmt] 구조 탐색 실패: {e}")

            # 2. 조회 실행
            stock_val = "1" if stock_only else "0"
            search_js = SEARCH_JS_TEMPLATE.format(ym=ym, stock_only=stock_val)
            result = self.driver.execute_script(search_js)
            if result is not True:
                logger.warning(f"[ExpiryMgmt] 조회 실행 결과: {result}")

            time.sleep(3)  # 데이터 로딩 대기

            # 3. 데이터 추출
            raw = self.driver.execute_script(EXTRACT_JS)
            if raw is None:
                logger.error("[ExpiryMgmt] 추출 JS가 None 반환 — 프레임 접근 실패")
                return {"success": False, "error": "extract_js_returned_none", "count": 0}

            data = json.loads(raw)

            if data.get("error"):
                logger.error(f"[ExpiryMgmt] 추출 오류: {data['error']}")
                return {"success": False, "error": data["error"], "count": 0}

            rows = data.get("rows", [])
            logger.info(f"[ExpiryMgmt] 수집: {len(rows)}건 ({target_month})")

            # 4. DB 저장
            saved = self._save_to_db(rows, target_month)

            return {
                "success": True,
                "count": len(rows),
                "saved": saved,
                "month": target_month,
            }

        except Exception as e:
            logger.error(f"[ExpiryMgmt] 수집 실패: {e}")
            return {"success": False, "error": str(e), "count": 0}

    def _save_to_db(self, rows: List[Dict], collected_month: str) -> int:
        """수집 데이터를 DB에 저장"""
        from src.infrastructure.database.connection import DBRouter

        conn = DBRouter.get_store_connection(self.store_id)
        try:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            saved = 0

            for row in rows:
                item_cd = row.get("item_cd")
                expire_ymd = row.get("expire_ymd")
                if not item_cd or not expire_ymd:
                    continue

                try:
                    cursor.execute("""
                        INSERT INTO expiry_management
                        (store_id, item_cd, item_nm, large_nm, mid_nm,
                         expire_ymd, now_qty, collected_month, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (store_id, item_cd, expire_ymd) DO UPDATE SET
                            item_nm = excluded.item_nm,
                            now_qty = excluded.now_qty,
                            updated_at = excluded.updated_at
                    """, (
                        self.store_id, item_cd, row.get("item_nm"),
                        row.get("large_nm"), row.get("mid_nm"),
                        expire_ymd, row.get("now_qty", 0),
                        collected_month, now, now
                    ))
                    saved += 1
                except Exception as e:
                    logger.debug(f"[ExpiryMgmt] 저장 실패 ({item_cd}): {e}")

            conn.commit()
            logger.info(f"[ExpiryMgmt] DB 저장: {saved}/{len(rows)}건")
            return saved
        finally:
            conn.close()

    def close_tab(self) -> None:
        """STCM130_M0 탭 닫기"""
        try:
            from src.utils.nexacro_helpers import close_tab_verified
            close_tab_verified(self.driver, FRAME_ID)
        except Exception as e:
            logger.debug(f"[ExpiryMgmt] 탭 닫기 실패: {e}")
