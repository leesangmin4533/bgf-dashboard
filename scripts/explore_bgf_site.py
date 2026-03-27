# -*- coding: utf-8 -*-
"""
BGF 리테일 사이트 전체 메뉴/API 탐색 스크립트

로그인 → 전체 메뉴 구조 추출 → 주요 화면별 네트워크 캡처
→ 미활용 데이터 식별
"""
import sys
import io
import os
import json
import time
from pathlib import Path
from datetime import datetime

if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer"):
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8",
                                                errors="replace", line_buffering=True))

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.sales_analyzer import SalesAnalyzer
from src.settings.timing import SA_LOGIN_WAIT, SA_POPUP_CLOSE_WAIT


# ═══════════════════════════════════════════════════════════════
# XHR 인터셉터 (전체 API 캡처)
# ═══════════════════════════════════════════════════════════════

GLOBAL_INTERCEPTOR_JS = r"""
if (!window.__globalCaptures) {
    window.__globalCaptures = [];
    window.__globalInterceptorInstalled = false;
}
if (!window.__globalInterceptorInstalled) {
    window.__globalInterceptorInstalled = true;
    var origOpen = XMLHttpRequest.prototype.open;
    var origSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function(method, url) {
        this.__captureUrl = url;
        this.__captureMethod = method;
        return origOpen.apply(this, arguments);
    };

    XMLHttpRequest.prototype.send = function(body) {
        var url = this.__captureUrl || '';
        // 모든 API 호출 캡처 (이미지/CSS 제외)
        if (url && !url.match(/\.(png|jpg|gif|css|js|ico|woff)/) &&
            (url.indexOf('/') === 0 || url.indexOf('http') === 0)) {
            window.__globalCaptures.push({
                url: url,
                method: this.__captureMethod || 'GET',
                bodyPreview: body ? String(body).substring(0, 3000) : '',
                bodyLength: body ? String(body).length : 0,
                timestamp: new Date().toISOString()
            });
        }
        return origSend.apply(this, arguments);
    };
    console.log('[Explorer] Global XHR interceptor installed');
}
"""

# gfn_transaction 인터셉터 (넥사크로 내부 호출)
GFN_INTERCEPTOR_JS = r"""
if (!window.__gfnCaptures) {
    window.__gfnCaptures = [];
}
try {
    var topFrame = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
    var workFrame = topFrame.WorkFrame;
    if (workFrame && workFrame.form) {
        var form = workFrame.form;
        // gfn_transaction이 존재하면 래핑
        if (typeof form.gfn_transaction === 'function' && !form.__origGfnTx) {
            form.__origGfnTx = form.gfn_transaction;
            form.gfn_transaction = function() {
                var args = Array.prototype.slice.call(arguments);
                window.__gfnCaptures.push({
                    txId: args[0] || '',
                    svcURL: args[1] || '',
                    inDS: args[2] || '',
                    outDS: args[3] || '',
                    args: args[4] || '',
                    timestamp: new Date().toISOString()
                });
                return form.__origGfnTx.apply(this, arguments);
            };
        }
    }
} catch(e) {
    // 화면 미로딩 시 무시
}
"""


def get_captures(driver):
    """현재까지 캡처된 XHR 목록 조회"""
    try:
        xhr = driver.execute_script("return window.__globalCaptures || [];")
        gfn = driver.execute_script("return window.__gfnCaptures || [];")
        return {"xhr": xhr, "gfn": gfn}
    except:
        return {"xhr": [], "gfn": []}


def clear_captures(driver):
    """캡처 배열 초기화"""
    driver.execute_script("window.__globalCaptures = []; window.__gfnCaptures = [];")


def nx(driver, js_code):
    """넥사크로 JS 실행 (nexacro.getApplication() 래퍼)"""
    wrapped = f"""
        try {{
            var app = nexacro.getApplication();
            {js_code}
        }} catch(e) {{
            return {{error: e.message}};
        }}
    """
    return driver.execute_script(wrapped)


def extract_menu_structure(driver):
    """넥사크로 TopFrame에서 전체 메뉴 구조 추출"""
    return nx(driver, r"""
        var menus = [];
        var topForm = app.mainframe.HFrameSet00.VFrameSet00.TopFrame.form;
        var menuDiv = topForm.div_topMenu;
        if (!menuDiv) return menus;

        var components = menuDiv.form.components;
        if (!components) return menus;

        for (var i = 0; i < components.length; i++) {
            var comp = components[i];
            if (!comp || !comp.name) continue;
            var name = comp.name;
            if (name.match(/^ST[A-Z]{2}\d{3}_M0$/) || name.match(/^SS_/)) {
                menus.push({
                    id: name,
                    text: comp.text || comp.value || name
                });
            }
        }
        return menus;
    """) or []


def extract_submenus(driver, parent_menu_id):
    """특정 상위 메뉴의 하위 메뉴 추출"""
    return nx(driver, f"""
        var subs = [];
        var topForm = app.mainframe.HFrameSet00.VFrameSet00.TopFrame.form;
        var panelName = 'pdiv_topMenu_{parent_menu_id}';
        var panel = topForm[panelName];
        if (!panel || !panel.form || !panel.form.components) return subs;

        var comps = panel.form.components;
        for (var i = 0; i < comps.length; i++) {{
            var comp = comps[i];
            if (!comp || !comp.name) continue;
            subs.push({{
                id: comp.name,
                text: comp.text || comp.value || comp.name,
                visible: comp.visible !== false
            }});
        }}
        return subs;
    """) or []


def click_menu(driver, menu_id):
    """메뉴 클릭"""
    nx(driver, f"""
        var topForm = app.mainframe.HFrameSet00.VFrameSet00.TopFrame.form;
        var el = topForm.div_topMenu.form['{menu_id}'];
        if (el && el._element_node) el._element_node.click();
        return true;
    """)
    return True


def explore_screen_datasets(driver, frame_id):
    """현재 열린 화면의 데이터셋/그리드/컴포넌트 구조 탐색"""
    result = nx(driver, f"""
        var info = {{datasets: [], grids: [], buttons: []}};
        var frameset = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
        var frame = frameset['{frame_id}'] || frameset.WorkFrame;
        if (!frame || !frame.form) return info;
        var form = frame.form;

        function scanForm(f, prefix) {{
            if (!f) return;
            if (f.objects) {{
                for (var i = 0; i < f.objects.length; i++) {{
                    var obj = f.objects[i];
                    if (obj && obj.typename === 'Dataset') {{
                        var colCount = 0, rowCount = 0, colNames = [];
                        try {{
                            colCount = obj.getColCount ? obj.getColCount() : 0;
                            rowCount = obj.getRowCount ? obj.getRowCount() : 0;
                            for (var c = 0; c < Math.min(colCount, 50); c++) {{
                                colNames.push(obj.getColID(c));
                            }}
                        }} catch(e) {{}}
                        info.datasets.push({{
                            name: prefix + obj.name,
                            colCount: colCount, rowCount: rowCount,
                            columns: colNames
                        }});
                    }}
                }}
            }}
            if (f.components) {{
                for (var i = 0; i < f.components.length; i++) {{
                    var comp = f.components[i];
                    if (!comp) continue;
                    if (comp.typename === 'Grid') {{
                        info.grids.push({{
                            name: prefix + comp.name,
                            binddataset: comp.binddataset || ''
                        }});
                    }}
                    if (comp.typename === 'Button' && comp.name && comp.name.match(/^(F_|btn_)/)) {{
                        info.buttons.push({{
                            name: prefix + comp.name,
                            text: comp.text || ''
                        }});
                    }}
                    if (comp.form) scanForm(comp.form, prefix + comp.name + '.');
                }}
            }}
        }}
        scanForm(form, '');
        return info;
    """)
    return result or {"datasets": [], "grids": [], "buttons": []}


def get_top_frame_data(driver):
    """TopFrame에서 제공하는 전역 데이터셋 탐색"""
    return nx(driver, r"""
        var info = {datasets: [], globals: {}};
        var topForm = app.mainframe.HFrameSet00.VFrameSet00.TopFrame.form;

        if (topForm.objects) {
            for (var i = 0; i < topForm.objects.length; i++) {
                var obj = topForm.objects[i];
                if (obj && obj.typename === 'Dataset') {
                    var colCount = 0, rowCount = 0, colNames = [], sample = [];
                    try {
                        colCount = obj.getColCount ? obj.getColCount() : 0;
                        rowCount = obj.getRowCount ? obj.getRowCount() : 0;
                        for (var c = 0; c < Math.min(colCount, 30); c++) {
                            colNames.push(obj.getColID(c));
                        }
                        if (rowCount > 0) {
                            var row = {};
                            for (var c = 0; c < Math.min(colCount, 30); c++) {
                                var val = obj.getColumn(0, obj.getColID(c));
                                row[obj.getColID(c)] = val ? String(val).substring(0, 100) : '';
                            }
                            sample.push(row);
                        }
                    } catch(e) {}
                    info.datasets.push({
                        name: obj.name, colCount: colCount,
                        rowCount: rowCount, columns: colNames,
                        sample: sample
                    });
                }
            }
        }

        var gvars = ['GV_STORE_CD', 'GV_CHANNELTYPE', 'GV_USER_ID',
                     'GV_STORE_NM', 'GV_OPEN_DT', 'GV_FO_FLAG',
                     'GV_FO_CD', 'GV_APP_VER', 'GV_FO_ZONE'];
        for (var g = 0; g < gvars.length; g++) {
            try { info.globals[gvars[g]] = String(app[gvars[g]] || ''); } catch(e) {}
        }
        return info;
    """) or {"datasets": [], "globals": {}}


def navigate_to_screen(driver, top_menu_id, sub_menu_id):
    """상위 메뉴 클릭 → 하위 메뉴 클릭으로 화면 이동"""
    try:
        nx(driver, f"""
            var topForm = app.mainframe.HFrameSet00.VFrameSet00.TopFrame.form;
            var el = topForm.div_topMenu.form['{top_menu_id}'];
            if (el && el._element_node) {{
                var evt = document.createEvent('MouseEvents');
                evt.initMouseEvent('mouseover', true, true, window, 0, 0, 0, 0, 0, false, false, false, false, 0, null);
                el._element_node.dispatchEvent(evt);
            }}
            return true;
        """)
        time.sleep(0.5)

        nx(driver, f"""
            var topForm = app.mainframe.HFrameSet00.VFrameSet00.TopFrame.form;
            var panelName = 'pdiv_topMenu_{top_menu_id}';
            var panel = topForm[panelName];
            if (panel && panel.form) {{
                var sub = panel.form['{sub_menu_id}'];
                if (sub && sub._element_node) sub._element_node.click();
            }}
            return true;
        """)
        time.sleep(2)
        return True
    except Exception as e:
        print(f"  [ERROR] 화면 이동 실패: {e}")
        return False


def main():
    print("=" * 70)
    print("BGF 리테일 사이트 전체 탐색")
    print(f"시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 1. 로그인
    print("\n[1] BGF 로그인...")
    analyzer = SalesAnalyzer()
    try:
        analyzer.setup_driver()
        analyzer.connect()
        time.sleep(SA_LOGIN_WAIT)

        if not analyzer.do_login():
            print("[ERROR] 로그인 실패!")
            return
        print("[OK] 로그인 성공")
        time.sleep(SA_POPUP_CLOSE_WAIT * 2)
        analyzer.close_popup()
        time.sleep(SA_POPUP_CLOSE_WAIT)

        driver = analyzer.driver

        # 2. 글로벌 인터셉터 설치
        print("\n[2] XHR 인터셉터 설치...")
        driver.execute_script(GLOBAL_INTERCEPTOR_JS)
        print("[OK]")

        # 3. TopFrame 전역 데이터 탐색
        print("\n[3] TopFrame 전역 데이터...")
        top_data = get_top_frame_data(driver)
        if top_data.get("globals"):
            print(f"  전역 변수:")
            for k, v in top_data["globals"].items():
                if v:
                    print(f"    {k} = {v}")
        if top_data.get("datasets"):
            print(f"  데이터셋: {len(top_data['datasets'])}개")
            for ds in top_data["datasets"]:
                print(f"    {ds['name']}: {ds['rowCount']}행 × {ds['colCount']}열")
                if ds.get("columns"):
                    print(f"      컬럼: {', '.join(ds['columns'][:15])}")
                if ds.get("sample"):
                    for row in ds["sample"][:1]:
                        for k, v in list(row.items())[:8]:
                            if v:
                                print(f"      {k}={v}")

        # 4. 메뉴 구조 추출
        print("\n[4] 메뉴 구조 탐색...")
        menus = extract_menu_structure(driver)
        print(f"  상위 메뉴: {len(menus)}개")

        all_results = {
            "timestamp": datetime.now().isoformat(),
            "top_data": top_data,
            "menus": [],
            "screens": {}
        }

        for menu in menus:
            menu_id = menu.get("id", "")
            menu_text = menu.get("text", menu_id)
            print(f"\n  ┌─ {menu_text} ({menu_id})")

            # 상위 메뉴 클릭하여 하위 메뉴 노출
            click_menu(driver, menu_id)
            time.sleep(0.5)

            subs = extract_submenus(driver, menu_id)
            menu_info = {"id": menu_id, "text": menu_text, "submenus": []}

            for sub in subs:
                sub_id = sub.get("id", "")
                sub_text = sub.get("text", sub_id)
                visible = sub.get("visible", True)
                status = "✓" if visible else "✗"
                print(f"  │  {status} {sub_text} ({sub_id})")
                menu_info["submenus"].append(sub)

            all_results["menus"].append(menu_info)

        # 아무 곳이나 클릭해서 메뉴 닫기
        time.sleep(0.5)

        # 5. 주요 미탐색 화면 진입 + 데이터셋 분석
        print("\n" + "=" * 70)
        print("[5] 주요 화면 데이터셋 분석")
        print("=" * 70)

        # 탐색할 화면 목록 (현재 미사용 또는 부분 사용)
        screens_to_explore = []

        # 메뉴에서 발견된 화면 중 현재 FRAME_IDS에 없는 것 찾기
        known_frames = {"STAJ001_M0", "STBJ030_M0", "STBJ010_M0", "STBJ070_M0",
                        "STGJ010_M0", "SS_STBJ460_M0", "STGJ020_M0", "STMB011_M0"}

        for menu_info in all_results["menus"]:
            for sub in menu_info.get("submenus", []):
                sub_id = sub.get("id", "")
                if sub_id and sub_id not in known_frames and sub.get("visible", True):
                    screens_to_explore.append({
                        "top_menu": menu_info["id"],
                        "sub_menu": sub_id,
                        "text": sub.get("text", sub_id),
                        "parent_text": menu_info.get("text", "")
                    })

        print(f"\n  미탐색 화면: {len(screens_to_explore)}개")

        # 각 화면 방문하여 데이터셋 구조 확인 (최대 15개)
        for i, screen in enumerate(screens_to_explore[:15]):
            frame_id = screen["sub_menu"]
            print(f"\n  [{i+1}] {screen['parent_text']} > {screen['text']} ({frame_id})")

            clear_captures(driver)

            if navigate_to_screen(driver, screen["top_menu"], frame_id):
                time.sleep(2)

                # gfn_transaction 인터셉터 설치 시도
                driver.execute_script(GFN_INTERCEPTOR_JS)

                # 데이터셋 구조 탐색
                ds_info = explore_screen_datasets(driver, frame_id)

                if ds_info.get("datasets"):
                    print(f"      데이터셋: {len(ds_info['datasets'])}개")
                    for ds in ds_info["datasets"]:
                        cols_preview = ", ".join(ds.get("columns", [])[:10])
                        extra = f"..." if len(ds.get("columns", [])) > 10 else ""
                        print(f"        {ds['name']}: {ds.get('rowCount',0)}행×{ds.get('colCount',0)}열")
                        if cols_preview:
                            print(f"          [{cols_preview}{extra}]")

                if ds_info.get("grids"):
                    print(f"      그리드: {len(ds_info['grids'])}개")
                    for g in ds_info["grids"]:
                        print(f"        {g['name']} → {g.get('binddataset','')}")

                if ds_info.get("buttons"):
                    btns = [b["name"] + ("(" + b["text"] + ")" if b.get("text") else "")
                            for b in ds_info["buttons"]]
                    print(f"      버튼: {', '.join(btns[:10])}")

                # XHR 캡처 확인
                caps = get_captures(driver)
                if caps["xhr"]:
                    unique_urls = list(set(c.get("url", "") for c in caps["xhr"]))
                    print(f"      API 호출: {len(caps['xhr'])}건")
                    for url in unique_urls[:5]:
                        print(f"        → {url}")

                all_results["screens"][frame_id] = {
                    "text": screen["text"],
                    "parent": screen["parent_text"],
                    "datasets": ds_info.get("datasets", []),
                    "grids": ds_info.get("grids", []),
                    "buttons": ds_info.get("buttons", []),
                    "api_calls": caps["xhr"][:10],
                    "gfn_calls": caps["gfn"][:10]
                }

        # 6. 이미 사용 중인 화면도 분석 (추가 데이터셋 확인)
        print("\n" + "=" * 70)
        print("[6] 기존 화면 미활용 데이터 확인")
        print("=" * 70)

        existing_screens = [
            {"top": "STBJ000_M0", "sub": "STBJ030_M0", "name": "단품별 발주"},
            {"top": "STBJ000_M0", "sub": "STBJ070_M0", "name": "발주현황조회"},
            {"top": "STMB000_M0", "sub": "STMB011_M0", "name": "중분류별 매출"},
        ]

        for screen in existing_screens:
            print(f"\n  [{screen['name']}] ({screen['sub']})")
            clear_captures(driver)

            if navigate_to_screen(driver, screen["top"], screen["sub"]):
                time.sleep(3)
                driver.execute_script(GFN_INTERCEPTOR_JS)

                ds_info = explore_screen_datasets(driver, screen["sub"])
                if ds_info.get("datasets"):
                    for ds in ds_info["datasets"]:
                        cols = ds.get("columns", [])
                        print(f"    {ds['name']}: {ds.get('rowCount',0)}행×{ds.get('colCount',0)}열")
                        if cols:
                            print(f"      [{', '.join(cols[:15])}{'...' if len(cols)>15 else ''}]")

                if ds_info.get("buttons"):
                    btns = [f"{b['name']}({b.get('text','')})" for b in ds_info["buttons"]]
                    print(f"    버튼: {', '.join(btns[:10])}")

                all_results["screens"][screen["sub"] + "_existing"] = {
                    "text": screen["name"],
                    "datasets": ds_info.get("datasets", []),
                    "grids": ds_info.get("grids", []),
                    "buttons": ds_info.get("buttons", []),
                }

        # 7. 결과 저장
        output_path = project_root / "data" / "bgf_site_exploration.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n\n결과 저장: {output_path}")

        print("\n" + "=" * 70)
        print("탐색 완료!")
        print("=" * 70)

    finally:
        try:
            analyzer.close()
            print("브라우저 종료")
        except:
            pass


if __name__ == "__main__":
    main()
