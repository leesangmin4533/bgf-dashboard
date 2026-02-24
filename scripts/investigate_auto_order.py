"""
46704(이천동양점) 자동발주 목록 조사 스크립트
- BGF 사이트에 로그인
- 발주 현황 조회 메뉴 이동
- "전체" / "자동" / "스마트" 라디오 각각 클릭하여 dsResult 건수 비교
- 자동발주 목록 상세 출력
"""

import os
import sys
import time
from pathlib import Path

# 프로젝트 루트 설정
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from src.sales_analyzer import SalesAnalyzer
from src.collectors.order_status_collector import OrderStatusCollector
from src.settings.ui_config import FRAME_IDS, DS_PATHS
from src.utils.nexacro_helpers import (
    click_menu_by_text, click_submenu_by_text, wait_for_frame,
    close_tab_by_frame_id, JS_CLICK_HELPER,
)

STORE_ID = "46704"
FRAME_ID = FRAME_IDS["ORDER_STATUS"]
DS_PATH = DS_PATHS["ORDER_STATUS"]


def investigate():
    """메인 조사 함수"""
    print(f"\n{'='*60}")
    print(f"  BGF 자동발주 목록 조사 - 매장: {STORE_ID}")
    print(f"{'='*60}\n")

    # 1. 로그인
    print("[1/5] 로그인 중...")
    analyzer = SalesAnalyzer(store_id=STORE_ID)
    analyzer.setup_driver()
    analyzer.connect()

    driver = analyzer.driver

    # 로그인 실행
    try:
        result = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var form = app.mainframe.HFrameSet00.LoginFrame.form.div_login.form;
                form.edt_id.set_value(arguments[0]);
                form.edt_pw.set_value(arguments[1]);
                form.btn_login.click();
                return {success: true};
            } catch(e) {
                return {error: e.message};
            }
        """, STORE_ID, "1113")
        print(f"  로그인 결과: {result}")
    except Exception as e:
        print(f"  로그인 에러: {e}")
        driver.quit()
        return

    time.sleep(5)

    # 팝업 닫기
    try:
        result = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var mf = app.mainframe;
                // 팝업 닫기 시도
                var frames = ['popFrame', 'PopupFrame', 'childframe'];
                for (var i = 0; i < frames.length; i++) {
                    try {
                        if (mf[frames[i]] && mf[frames[i]].form && mf[frames[i]].form.btn_close) {
                            mf[frames[i]].form.btn_close.click();
                            return {closed: frames[i]};
                        }
                    } catch(e2) {}
                }
                // DOM에서 팝업 닫기 버튼 찾기
                var btns = document.querySelectorAll('[id*="btn_close"]');
                for (var j = 0; j < btns.length; j++) {
                    if (btns[j].offsetParent !== null) {
                        btns[j].click();
                        return {closed: 'dom_btn_close'};
                    }
                }
                return {closed: false};
            } catch(e) {
                return {error: e.message};
            }
        """)
        print(f"  팝업 처리: {result}")
    except Exception as e:
        print(f"  팝업 처리 에러: {e}")

    time.sleep(2)

    # 로그인 상태 확인
    try:
        store_info = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                return {
                    storeId: app.gds_userInfo?.getColumn(0, 'STORE_CD') || app.GV_STORE_CD || 'unknown',
                    channelType: app.GV_CHANNELTYPE || 'unknown',
                    appReady: true
                };
            } catch(e) {
                return {error: e.message};
            }
        """)
        print(f"  로그인 상태: {store_info}")
    except Exception as e:
        print(f"  로그인 상태 확인 에러: {e}")

    # 2. 발주 현황 조회 메뉴 이동
    print(f"\n[2/5] 발주 현황 조회 메뉴 이동 중... (FRAME_ID: {FRAME_ID})")

    # 메뉴 클릭
    menu_ok = click_menu_by_text(driver, '발주')
    print(f"  발주 메뉴 클릭: {menu_ok}")
    time.sleep(1.5)

    # 서브메뉴 클릭
    sub_ok = click_submenu_by_text(driver, '발주 현황 조회')
    print(f"  발주 현황 조회 서브메뉴 클릭: {sub_ok}")
    time.sleep(3)

    # 프레임 로딩 대기
    frame_ok = wait_for_frame(driver, FRAME_ID, max_wait=15)
    print(f"  프레임 로딩: {frame_ok}")

    if not frame_ok:
        print("  [실패] 발주 현황 조회 화면 로딩 실패!")
        driver.quit()
        return

    time.sleep(2)

    # 3. "전체" 라디오 - dsResult 건수 확인
    print(f"\n[3/5] 전체 라디오 (value=0) 클릭...")

    result_all = driver.execute_script(JS_CLICK_HELPER + f"""
        try {{
            var app = nexacro.getApplication();
            var frame = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID};
            var wf = frame.form.{DS_PATH};

            // rdGubun 현재값 확인
            var radio = wf.Div21 && wf.Div21.form && wf.Div21.form.rdGubun;
            var currentVal = radio ? radio.value : 'unknown';

            // 전체(0) 설정
            if (radio && radio.set_value) {{
                radio.set_value('0');
                if (radio.on_fire_onitemchanged) {{
                    radio.on_fire_onitemchanged(radio, {{}});
                }}
            }}

            return {{
                success: true,
                currentVal: currentVal,
                newVal: '0'
            }};
        }} catch(e) {{
            return {{error: e.message}};
        }}
    """)
    print(f"  전체 라디오 결과: {result_all}")
    time.sleep(3)

    # dsResult 건수 확인 (전체)
    result_total = driver.execute_script(f"""
        try {{
            var app = nexacro.getApplication();
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form.{DS_PATH};
            var ds = wf.dsResult;
            if (!ds) return {{error: 'dsResult not found'}};

            var count = ds.getRowCount();
            var samples = [];
            for (var i = 0; i < Math.min(5, count); i++) {{
                function getVal(row, col) {{
                    var val = ds.getColumn(row, col);
                    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                    return val ? String(val) : '';
                }}
                samples.push({{
                    item_cd: getVal(i, 'ITEM_CD'),
                    item_nm: getVal(i, 'ITEM_NM'),
                    ord_input_id: getVal(i, 'ORD_INPUT_ID'),
                    ord_pss_nm: getVal(i, 'ORD_PSS_NM')
                }});
            }}

            return {{total: count, samples: samples}};
        }} catch(e) {{
            return {{error: e.message}};
        }}
    """)
    print(f"  [전체] dsResult: {result_total}")

    # 4. "자동" 라디오 클릭
    print(f"\n[4/5] 자동 라디오 (value=2) 클릭...")

    result_auto_click = driver.execute_script(JS_CLICK_HELPER + f"""
        try {{
            var app = nexacro.getApplication();
            var frame = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID};
            var wf = frame.form.{DS_PATH};

            var radio = wf.Div21 && wf.Div21.form && wf.Div21.form.rdGubun;
            if (radio && radio.set_value) {{
                radio.set_value('2');
                if (radio.on_fire_onitemchanged) {{
                    radio.on_fire_onitemchanged(radio, {{}});
                }}
                return {{success: true, method: 'api', value: '2'}};
            }}

            return {{success: false, error: 'rdGubun not found'}};
        }} catch(e) {{
            return {{error: e.message}};
        }}
    """)
    print(f"  자동 라디오 클릭 결과: {result_auto_click}")
    time.sleep(3)

    # dsResult 건수 확인 (자동)
    result_auto = driver.execute_script(f"""
        try {{
            var app = nexacro.getApplication();
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form.{DS_PATH};
            var ds = wf.dsResult;
            if (!ds) return {{error: 'dsResult not found'}};

            var count = ds.getRowCount();
            var items = [];
            for (var i = 0; i < count; i++) {{
                function getVal(row, col) {{
                    var val = ds.getColumn(row, col);
                    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                    return val ? String(val) : '';
                }}
                items.push({{
                    item_cd: getVal(i, 'ITEM_CD'),
                    item_nm: getVal(i, 'ITEM_NM'),
                    mid_cd: getVal(i, 'MID_CD'),
                    mid_nm: getVal(i, 'MID_NM'),
                    ord_cnt: getVal(i, 'ORD_CNT'),
                    ord_unit_qty: getVal(i, 'ORD_UNIT_QTY'),
                    ord_pss_nm: getVal(i, 'ORD_PSS_NM')
                }});
            }}

            return {{total: count, items: items}};
        }} catch(e) {{
            return {{error: e.message}};
        }}
    """)

    print(f"\n  [자동] dsResult 건수: {result_auto.get('total', 'error')}")
    if result_auto.get('items'):
        print(f"\n  === 자동발주 상품 목록 ({len(result_auto['items'])}개) ===")
        for i, item in enumerate(result_auto['items']):
            print(f"  {i+1:3d}. {item.get('item_cd','?'):15s} | {item.get('item_nm','?'):30s} | mid={item.get('mid_cd','?')} | pss={item.get('ord_pss_nm','?')}")

    # 5. "스마트" 라디오 클릭
    print(f"\n[5/5] 스마트 라디오 (value=3) 클릭...")

    result_smart_click = driver.execute_script(JS_CLICK_HELPER + f"""
        try {{
            var app = nexacro.getApplication();
            var frame = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID};
            var wf = frame.form.{DS_PATH};

            var radio = wf.Div21 && wf.Div21.form && wf.Div21.form.rdGubun;
            if (radio && radio.set_value) {{
                radio.set_value('3');
                if (radio.on_fire_onitemchanged) {{
                    radio.on_fire_onitemchanged(radio, {{}});
                }}
                return {{success: true, method: 'api', value: '3'}};
            }}

            return {{success: false, error: 'rdGubun not found'}};
        }} catch(e) {{
            return {{error: e.message}};
        }}
    """)
    print(f"  스마트 라디오 클릭 결과: {result_smart_click}")
    time.sleep(3)

    # dsResult 건수 확인 (스마트)
    result_smart = driver.execute_script(f"""
        try {{
            var app = nexacro.getApplication();
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form.{DS_PATH};
            var ds = wf.dsResult;
            if (!ds) return {{error: 'dsResult not found'}};

            var count = ds.getRowCount();
            var items = [];
            for (var i = 0; i < Math.min(10, count); i++) {{
                function getVal(row, col) {{
                    var val = ds.getColumn(row, col);
                    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                    return val ? String(val) : '';
                }}
                items.push({{
                    item_cd: getVal(i, 'ITEM_CD'),
                    item_nm: getVal(i, 'ITEM_NM'),
                    mid_cd: getVal(i, 'MID_CD')
                }});
            }}

            return {{total: count, samples: items}};
        }} catch(e) {{
            return {{error: e.message}};
        }}
    """)
    print(f"  [스마트] dsResult 건수: {result_smart.get('total', 'error')}")

    # 6. 발주일 정보 확인 (dsWeek)
    print(f"\n[추가] dsWeek 발주일 목록 확인...")
    result_week = driver.execute_script(f"""
        try {{
            var app = nexacro.getApplication();
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form.{DS_PATH};
            var ds = wf.dsWeek;
            if (!ds) return {{error: 'dsWeek not found'}};

            var dates = [];
            for (var i = 0; i < ds.getRowCount(); i++) {{
                dates.push(ds.getColumn(i, 'ORD_YMD'));
            }}
            return {{dates: dates, selectedRow: ds.rowposition}};
        }} catch(e) {{
            return {{error: e.message}};
        }}
    """)
    print(f"  dsWeek: {result_week}")

    # 7. ORD_PSS_NM 컬럼 값 분석 (발주가능 상태)
    print(f"\n[추가] 전체 목록에서 ORD_PSS_NM 값 분포 확인...")

    # 다시 전체로 변경
    driver.execute_script(JS_CLICK_HELPER + f"""
        try {{
            var app = nexacro.getApplication();
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form.{DS_PATH};
            var radio = wf.Div21 && wf.Div21.form && wf.Div21.form.rdGubun;
            if (radio) {{
                radio.set_value('0');
                if (radio.on_fire_onitemchanged) radio.on_fire_onitemchanged(radio, {{}});
            }}
        }} catch(e) {{}}
    """)
    time.sleep(3)

    result_pss = driver.execute_script(f"""
        try {{
            var app = nexacro.getApplication();
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form.{DS_PATH};
            var ds = wf.dsResult;
            if (!ds) return {{error: 'dsResult not found'}};

            var pssMap = {{}};
            var inputMap = {{}};
            var total = ds.getRowCount();

            for (var i = 0; i < total; i++) {{
                function getVal(row, col) {{
                    var val = ds.getColumn(row, col);
                    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                    return val ? String(val) : '';
                }}

                var pss = getVal(i, 'ORD_PSS_NM') || '(empty)';
                var inputId = getVal(i, 'ORD_INPUT_ID') || '(empty)';

                pssMap[pss] = (pssMap[pss] || 0) + 1;
                inputMap[inputId] = (inputMap[inputId] || 0) + 1;
            }}

            return {{
                total: total,
                pss_distribution: pssMap,
                input_id_distribution: inputMap
            }};
        }} catch(e) {{
            return {{error: e.message}};
        }}
    """)
    print(f"  [전체] 총 건수: {result_pss.get('total', '?')}")
    print(f"  ORD_PSS_NM 분포: {result_pss.get('pss_distribution', {})}")
    print(f"  ORD_INPUT_ID 분포: {result_pss.get('input_id_distribution', {})}")

    # === 비교: 46513 매장 DB 캐시 데이터와 비교 ===
    print(f"\n{'='*60}")
    print(f"  결과 요약")
    print(f"{'='*60}")
    print(f"  [전체] dsResult: {result_total.get('total', '?')} 건")
    print(f"  [자동] dsResult: {result_auto.get('total', '?')} 건")
    print(f"  [스마트] dsResult: {result_smart.get('total', '?')} 건")
    print(f"  dsWeek 발주일: {result_week.get('dates', [])}")

    # 정리
    print(f"\n브라우저 닫기...")
    driver.quit()
    print("조사 완료!")


if __name__ == "__main__":
    investigate()
