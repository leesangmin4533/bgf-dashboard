"""
46704 자동발주 상세 조사
- 전체 목록에서 ORD_INPUT_ID별 분류
- '자동발주예정' 상품 전체 상세 출력
- '자동' 라디오 필터 결과와 비교
"""

import os
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from src.sales_analyzer import SalesAnalyzer
from src.settings.ui_config import FRAME_IDS, DS_PATHS
from src.utils.nexacro_helpers import (
    click_menu_by_text, click_submenu_by_text, wait_for_frame,
    JS_CLICK_HELPER,
)

STORE_ID = "46704"
FRAME_ID = FRAME_IDS["ORDER_STATUS"]
DS_PATH = DS_PATHS["ORDER_STATUS"]


def investigate():
    print(f"\n{'='*60}")
    print(f"  46704 자동발주 상세 조사")
    print(f"{'='*60}\n")

    # 로그인
    analyzer = SalesAnalyzer(store_id=STORE_ID)
    analyzer.setup_driver()
    analyzer.connect()
    driver = analyzer.driver

    driver.execute_script("""
        try {
            var app = nexacro.getApplication();
            var form = app.mainframe.HFrameSet00.LoginFrame.form.div_login.form;
            form.edt_id.set_value(arguments[0]);
            form.edt_pw.set_value(arguments[1]);
            form.btn_login.click();
        } catch(e) {}
    """, STORE_ID, "1113")
    time.sleep(5)

    driver.execute_script("""
        try {
            var btns = document.querySelectorAll('[id*="btn_close"]');
            for (var j = 0; j < btns.length; j++) {
                if (btns[j].offsetParent !== null) { btns[j].click(); break; }
            }
        } catch(e) {}
    """)
    time.sleep(2)

    # 메뉴 이동
    click_menu_by_text(driver, '발주')
    time.sleep(1.5)
    click_submenu_by_text(driver, '발주 현황 조회')
    time.sleep(3)
    wait_for_frame(driver, FRAME_ID, max_wait=15)
    time.sleep(2)

    # 현재 선택된 발주일 확인
    week_info = driver.execute_script(f"""
        try {{
            var app = nexacro.getApplication();
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form.{DS_PATH};
            var ds = wf.dsWeek;
            var dates = [];
            for (var i = 0; i < ds.getRowCount(); i++) {{
                dates.push(ds.getColumn(i, 'ORD_YMD'));
            }}
            return {{dates: dates, currentRow: ds.rowposition, selectedDate: ds.getColumn(ds.rowposition, 'ORD_YMD')}};
        }} catch(e) {{ return {{error: e.message}}; }}
    """)
    print(f"[발주일] {week_info}")

    # 1. "전체" 라디오 상태에서 전체 dsResult 모든 컬럼 덤프
    print(f"\n[전체 목록 - ORD_INPUT_ID별 분류]")

    full_result = driver.execute_script(f"""
        try {{
            var app = nexacro.getApplication();
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form.{DS_PATH};

            // 전체 라디오
            var radio = wf.Div21 && wf.Div21.form && wf.Div21.form.rdGubun;
            if (radio) {{
                radio.set_value('0');
                if (radio.on_fire_onitemchanged) radio.on_fire_onitemchanged(radio, {{}});
            }}

            return {{set: true}};
        }} catch(e) {{ return {{error: e.message}}; }}
    """)
    time.sleep(2)

    all_items = driver.execute_script(f"""
        try {{
            var app = nexacro.getApplication();
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form.{DS_PATH};
            var ds = wf.dsResult;
            if (!ds) return {{error: 'dsResult not found'}};

            // 먼저 컬럼 목록 확인
            var cols = [];
            if (ds._colInfos) {{
                for (var c = 0; c < ds._colInfos.length; c++) {{
                    cols.push(ds._colInfos[c].id || ds._colInfos[c].name);
                }}
            }}

            function getVal(row, col) {{
                var val = ds.getColumn(row, col);
                if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                return val !== null && val !== undefined ? String(val) : '';
            }}

            var total = ds.getRowCount();
            var byInputId = {{}};
            var autoItems = [];

            for (var i = 0; i < total; i++) {{
                var inputId = getVal(i, 'ORD_INPUT_ID') || '(empty)';
                var itemCd = getVal(i, 'ITEM_CD');
                var itemNm = getVal(i, 'ITEM_NM');
                var midCd = getVal(i, 'MID_CD');
                var midNm = getVal(i, 'MID_NM');
                var ordCnt = getVal(i, 'ORD_CNT');
                var ordUnitQty = getVal(i, 'ORD_UNIT_QTY');
                var ordPssNm = getVal(i, 'ORD_PSS_NM');
                var ordPssId = getVal(i, 'ORD_PSS_ID');

                if (!byInputId[inputId]) byInputId[inputId] = [];
                byInputId[inputId].push({{
                    item_cd: itemCd,
                    item_nm: itemNm,
                    mid_cd: midCd,
                    mid_nm: midNm,
                    ord_cnt: ordCnt,
                    ord_unit_qty: ordUnitQty,
                    ord_pss_nm: ordPssNm,
                    ord_pss_id: ordPssId,
                    ord_input_id: inputId
                }});

                if (inputId.indexOf('자동') >= 0) {{
                    autoItems.push({{
                        item_cd: itemCd,
                        item_nm: itemNm,
                        mid_cd: midCd,
                        mid_nm: midNm,
                        ord_input_id: inputId,
                        ord_cnt: ordCnt
                    }});
                }}
            }}

            var summary = {{}};
            for (var key in byInputId) {{
                summary[key] = byInputId[key].length;
            }}

            return {{
                total: total,
                columns: cols,
                summary: summary,
                autoItems: autoItems
            }};
        }} catch(e) {{ return {{error: e.message}}; }}
    """)

    print(f"  총 건수: {all_items.get('total', '?')}")
    print(f"  컬럼: {all_items.get('columns', [])}")
    print(f"\n  ORD_INPUT_ID 분포:")
    for k, v in sorted(all_items.get('summary', {}).items()):
        print(f"    {k:30s}: {v}건")

    auto_items = all_items.get('autoItems', [])
    print(f"\n  === '자동' 포함 상품 ({len(auto_items)}개) ===")
    for i, item in enumerate(auto_items):
        print(f"  {i+1:3d}. {item['item_cd']:15s} | {item['item_nm']:30s} | mid={item['mid_cd']} | input={item['ord_input_id']} | cnt={item['ord_cnt']}")

    # 2. "자동" 라디오 결과
    print(f"\n[자동 라디오 필터 결과]")
    driver.execute_script(f"""
        try {{
            var app = nexacro.getApplication();
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form.{DS_PATH};
            var radio = wf.Div21 && wf.Div21.form && wf.Div21.form.rdGubun;
            if (radio) {{
                radio.set_value('2');
                if (radio.on_fire_onitemchanged) radio.on_fire_onitemchanged(radio, {{}});
            }}
        }} catch(e) {{}}
    """)
    time.sleep(2)

    auto_radio_result = driver.execute_script(f"""
        try {{
            var app = nexacro.getApplication();
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form.{DS_PATH};
            var ds = wf.dsResult;
            if (!ds) return {{error: 'dsResult not found'}};

            function getVal(row, col) {{
                var val = ds.getColumn(row, col);
                if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                return val !== null && val !== undefined ? String(val) : '';
            }}

            var items = [];
            for (var i = 0; i < ds.getRowCount(); i++) {{
                items.push({{
                    item_cd: getVal(i, 'ITEM_CD'),
                    item_nm: getVal(i, 'ITEM_NM'),
                    mid_cd: getVal(i, 'MID_CD'),
                    ord_input_id: getVal(i, 'ORD_INPUT_ID'),
                    ord_cnt: getVal(i, 'ORD_CNT')
                }});
            }}
            return {{total: ds.getRowCount(), items: items}};
        }} catch(e) {{ return {{error: e.message}}; }}
    """)

    radio_items = auto_radio_result.get('items', [])
    print(f"  자동 라디오 필터 결과: {auto_radio_result.get('total', '?')}건")
    for i, item in enumerate(radio_items):
        print(f"  {i+1:3d}. {item['item_cd']:15s} | {item['item_nm']:30s} | mid={item['mid_cd']} | input={item['ord_input_id']} | cnt={item['ord_cnt']}")

    # 비교
    auto_from_all = set(item['item_cd'] for item in auto_items)
    auto_from_radio = set(item['item_cd'] for item in radio_items)

    print(f"\n[비교]")
    print(f"  전체에서 '자동' 필터: {len(auto_from_all)}건")
    print(f"  자동 라디오 필터:    {len(auto_from_radio)}건")
    print(f"  교집합:             {len(auto_from_all & auto_from_radio)}건")
    if auto_from_all - auto_from_radio:
        print(f"  전체에만 있음:       {auto_from_all - auto_from_radio}")
    if auto_from_radio - auto_from_all:
        print(f"  라디오에만 있음:     {auto_from_radio - auto_from_all}")

    driver.quit()
    print("\n조사 완료!")


if __name__ == "__main__":
    investigate()
