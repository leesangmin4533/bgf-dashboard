"""
BGF "상품 유통기한 관리" 화면 수집기 (STCM130_M0)

커뮤니케이션 > 상품 유통기한 관리 메뉴에서
직원이 PDA로 등록한 철수예정일 데이터를 수집하여 DB에 저장.

데이터셋: dsList (8컬럼)
  LARGE_NM, MID_NM, ITEM_NM, ITEM_CD, EXPIRE_YMD, STORE_CD, DEL_FLAG, NOW_QTY
"""

import time
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

FRAME_ID = "STCM130_M0"

# 메뉴 이동 JS
MENU_JS = """
(function() {
    try {
        var app = nexacro.getApplication();
        app.gfn_menuOpen('STCM130_M0', '상품 유통기한 관리', 'CM::stcm130/stcm130_m0.xfdl');
        return true;
    } catch(e) { return false; }
})();
"""

# 조회 실행 JS (년월 설정 + 현재고 체크 + 조회)
SEARCH_JS_TEMPLATE = """
(function() {{
    try {{
        var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet.STCM130_M0.form;
        var wf = form.div_workForm.form;
        var ds = wf.div_search.form;

        // 조회년월 설정
        var ymDiv = ds.meSearchYm;
        if (ymDiv && ymDiv.form) {{
            // MaskEdit 또는 Calendar 찾기
            var comps = ymDiv.form.components;
            for (var i = 0; i < comps.length; i++) {{
                if (comps[i].value !== undefined) {{
                    comps[i].set_value('{ym}');
                    break;
                }}
            }}
        }}

        // 현재고 있는 상품만 조회 체크
        var chk = ds.chk_nowQtyYn;
        if (chk) chk.set_value('{stock_only}');

        // 조회 버튼 클릭
        var btn = form.div_cmmbtn.form.F_10;
        if (btn) btn.click();

        return true;
    }} catch(e) {{ return e.message; }}
}})();
"""

# 데이터셋 추출 JS
EXTRACT_JS = """
(function() {
    try {
        var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet.STCM130_M0.form;
        var wf = form.div_workForm.form;

        // dsList 찾기
        var ds = null;
        var objs = wf.objects;
        for (var i = 0; i < objs.length; i++) {
            if (objs[i].id === 'dsList') { ds = objs[i]; break; }
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
    } catch(e) { return JSON.stringify({error: e.message, rows: []}); }
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
            time.sleep(2)

            # 프레임 로딩 대기
            for _ in range(10):
                try:
                    check = self.driver.execute_script(f"""
                        try {{
                            var f = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID};
                            return f ? true : false;
                        }} catch(e) {{ return false; }}
                    """)
                    if check:
                        break
                except Exception:
                    pass
                time.sleep(0.5)

            # 2. 조회 실행
            stock_val = "1" if stock_only else "0"
            search_js = SEARCH_JS_TEMPLATE.format(ym=ym, stock_only=stock_val)
            result = self.driver.execute_script(search_js)
            if result is not True:
                logger.warning(f"[ExpiryMgmt] 조회 실행 결과: {result}")

            time.sleep(2)  # 데이터 로딩 대기

            # 3. 데이터 추출
            import json
            raw = self.driver.execute_script(EXTRACT_JS)
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
