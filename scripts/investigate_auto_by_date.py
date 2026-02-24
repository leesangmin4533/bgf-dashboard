"""
46704(이천동양점) 발주일별 자동발주 건수 조사
- 각 발주일(dsWeek)별로 자동 라디오 클릭 후 dsResult 건수 확인
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


def investigate_by_date():
    print(f"\n{'='*60}")
    print(f"  발주일별 자동발주 건수 조사 - 매장: {STORE_ID}")
    print(f"{'='*60}\n")

    # 로그인
    print("[1] 로그인 중...")
    analyzer = SalesAnalyzer(store_id=STORE_ID)
    analyzer.setup_driver()
    analyzer.connect()
    driver = analyzer.driver

    result = driver.execute_script("""
        try {
            var app = nexacro.getApplication();
            var form = app.mainframe.HFrameSet00.LoginFrame.form.div_login.form;
            form.edt_id.set_value(arguments[0]);
            form.edt_pw.set_value(arguments[1]);
            form.btn_login.click();
            return {success: true};
        } catch(e) { return {error: e.message}; }
    """, STORE_ID, "1113")
    print(f"  로그인: {result}")
    time.sleep(5)

    # 팝업 닫기
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
    print("[2] 발주 현황 조회 메뉴 이동...")
    click_menu_by_text(driver, '발주')
    time.sleep(1.5)
    click_submenu_by_text(driver, '발주 현황 조회')
    time.sleep(3)
    frame_ok = wait_for_frame(driver, FRAME_ID, max_wait=15)
    print(f"  프레임 로딩: {frame_ok}")
    if not frame_ok:
        driver.quit()
        return
    time.sleep(2)

    # dsWeek 발주일 목록 가져오기
    week_result = driver.execute_script(f"""
        try {{
            var app = nexacro.getApplication();
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form.{DS_PATH};
            var ds = wf.dsWeek;
            if (!ds) return {{error: 'dsWeek not found'}};
            var dates = [];
            for (var i = 0; i < ds.getRowCount(); i++) {{
                dates.push(ds.getColumn(i, 'ORD_YMD'));
            }}
            return {{dates: dates, currentRow: ds.rowposition}};
        }} catch(e) {{ return {{error: e.message}}; }}
    """)
    dates = week_result.get('dates', [])
    current_row = week_result.get('currentRow', -1)
    print(f"  발주일 목록: {dates}")
    print(f"  현재 선택: row={current_row} -> {dates[current_row] if 0 <= current_row < len(dates) else '?'}")

    # 각 발주일별로 전체/자동/스마트 건수 조회
    print(f"\n{'='*60}")
    print(f"  {'발주일':>10s} | {'전체':>5s} | {'자동':>5s} | {'스마트':>5s} | {'일반':>5s}")
    print(f"  {'-'*10}-+-{'-'*5}-+-{'-'*5}-+-{'-'*5}-+-{'-'*5}")

    for idx, date in enumerate(dates):
        # 발주일 변경 (dsWeek rowposition 변경)
        driver.execute_script(f"""
            try {{
                var app = nexacro.getApplication();
                var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form.{DS_PATH};
                var ds = wf.dsWeek;
                ds.set_rowposition({idx});
                // onrowposchanged 이벤트 발생시키기
                if (wf.grd_week && typeof wf.grd_week.on_fire_oncellclick === 'function') {{
                    wf.grd_week.on_fire_oncellclick(wf.grd_week, {idx}, 0);
                }}
            }} catch(e) {{}}
        """)
        time.sleep(2)

        # 전체(0) 조회
        counts = {}
        for radio_val, radio_name in [('0', '전체'), ('2', '자동'), ('3', '스마트'), ('1', '일반')]:
            driver.execute_script(f"""
                try {{
                    var app = nexacro.getApplication();
                    var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form.{DS_PATH};
                    var radio = wf.Div21 && wf.Div21.form && wf.Div21.form.rdGubun;
                    if (radio && radio.set_value) {{
                        radio.set_value('{radio_val}');
                        if (radio.on_fire_onitemchanged) radio.on_fire_onitemchanged(radio, {{}});
                    }}
                }} catch(e) {{}}
            """)
            time.sleep(1.5)

            result = driver.execute_script(f"""
                try {{
                    var app = nexacro.getApplication();
                    var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form.{DS_PATH};
                    var ds = wf.dsResult;
                    if (!ds) return {{count: -1}};
                    return {{count: ds.getRowCount()}};
                }} catch(e) {{ return {{count: -1}}; }}
            """)
            counts[radio_name] = result.get('count', -1)

        fmt_date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
        marker = " <-- today" if date == "20260224" else (" <-- tomorrow" if date == "20260225" else "")
        print(f"  {fmt_date:>10s} | {counts.get('전체', '?'):>5} | {counts.get('자동', '?'):>5} | {counts.get('스마트', '?'):>5} | {counts.get('일반', '?'):>5}{marker}")

    print(f"  {'-'*10}-+-{'-'*5}-+-{'-'*5}-+-{'-'*5}-+-{'-'*5}")

    # 정리
    print(f"\n브라우저 닫기...")
    driver.quit()
    print("조사 완료!")


if __name__ == "__main__":
    investigate_by_date()
