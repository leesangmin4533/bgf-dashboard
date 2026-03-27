# -*- coding: utf-8 -*-
"""
BGF 리테일 사이트 탐색 v5 - 화면 닫기 + TopFrame 데이터셋 + 전체 탐색
"""
import sys, io, time, json
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

TOP_PREFIX = "mainframe.HFrameSet00.VFrameSet00.TopFrame.form.div_topMenu.form."
SUB_PREFIX = "mainframe.HFrameSet00.VFrameSet00.TopFrame.form.pdiv_topMenu_"


def click_dom(driver, dom_id, wait=0.3):
    r = driver.execute_script(f"""
        var el = document.getElementById('{dom_id}');
        if (!el) return 'not_found';
        if (el.offsetParent === null) return 'hidden';
        el.scrollIntoView({{block:'center', inline:'center'}});
        var r = el.getBoundingClientRect();
        var o = {{bubbles:true, cancelable:true, view:window,
                  clientX:r.left+r.width/2, clientY:r.top+r.height/2}};
        el.dispatchEvent(new MouseEvent('mousedown', o));
        el.dispatchEvent(new MouseEvent('mouseup', o));
        el.dispatchEvent(new MouseEvent('click', o));
        return 'ok';
    """)
    if wait > 0:
        time.sleep(wait)
    return r


def dismiss_alert(driver):
    """alert 다이얼로그 자동 닫기"""
    try:
        alert = driver.switch_to.alert
        alert.accept()
        time.sleep(0.3)
        return True
    except:
        return False


def close_all_tabs(driver):
    """열린 모든 화면 탭 닫기 (nexacro 내부 탭)"""
    driver.execute_script("""
        try {
            var app = nexacro.getApplication();
            var topForm = app.mainframe.HFrameSet00.VFrameSet00.TopFrame.form;
            // fn_closeAllTab 함수 존재 확인
            if (typeof topForm.fn_closeAllTab === 'function') {
                topForm.fn_closeAllTab();
            } else if (typeof topForm.fn_close_tab === 'function') {
                topForm.fn_close_tab('all');
            } else {
                // FrameSet에서 직접 탭 정리
                var frameset = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
                if (frameset && frameset.form) {
                    var tabObj = frameset.form.components;
                    // Tab 컴포넌트 찾기
                    if (tabObj) {
                        for (var i = tabObj.length - 1; i >= 0; i--) {
                            var comp = tabObj[i];
                            if (comp && comp.typename === 'Tab' && typeof comp.removeTabpage === 'function') {
                                while (comp.tabcount > 0) {
                                    comp.removeTabpage(comp.tabcount - 1);
                                }
                            }
                        }
                    }
                }
            }
        } catch(e) {}
    """)
    dismiss_alert(driver)
    time.sleep(0.5)


def close_current_tab(driver):
    """현재 탭 닫기"""
    driver.execute_script("""
        try {
            var app = nexacro.getApplication();
            var topForm = app.mainframe.HFrameSet00.VFrameSet00.TopFrame.form;
            if (typeof topForm.fn_closeTab === 'function') {
                topForm.fn_closeTab();
            } else if (typeof topForm.fn_close_tab === 'function') {
                topForm.fn_close_tab();
            } else {
                // Tab 컴포넌트에서 선택된 탭 닫기
                var frameset = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
                if (frameset && frameset.form && frameset.form.components) {
                    for (var i = 0; i < frameset.form.components.length; i++) {
                        var comp = frameset.form.components[i];
                        if (comp && comp.typename === 'Tab' && typeof comp.removeTabpage === 'function') {
                            var idx = comp.tabindex;
                            if (idx >= 0) comp.removeTabpage(idx);
                            break;
                        }
                    }
                }
            }
        } catch(e) {}
    """)
    dismiss_alert(driver)
    time.sleep(0.3)


def discover_submenus(driver, parent_id):
    """상위 메뉴 클릭 후 서브메뉴 패널 스캔"""
    top_dom = f"{TOP_PREFIX}{parent_id}:icontext"
    click_dom(driver, top_dom, wait=0.8)

    panel_prefix = f"{SUB_PREFIX}{parent_id}"
    subs = driver.execute_script(f"""
        var results = [];
        var prefix = '{panel_prefix}';
        var all = document.querySelectorAll('[id^="' + prefix + '"]');
        for (var i = 0; i < all.length; i++) {{
            var id = all[i].id;
            var m = id.match(/\\.form\\.((?:SS_)?ST[A-Z]{{2,4}}\\d{{2,3}}_M0):(?:text|icontext)$/);
            if (m) {{
                var text = (all[i].textContent || '').trim();
                if (text && results.findIndex(x => x.id === m[1]) < 0) {{
                    results.push({{id: m[1], text: text, domId: id}});
                }}
            }}
        }}
        var panelEl = document.querySelector('[id^="' + prefix + '.form"]');
        return {{subs: results, panelExists: !!panelEl, totalEls: all.length}};
    """) or {"subs": [], "panelExists": False, "totalEls": 0}

    driver.execute_script("document.body.dispatchEvent(new MouseEvent('click', {bubbles:true}));")
    time.sleep(0.3)
    return subs


def read_topframe_datasets(driver):
    """TopFrame 데이터셋 내용 읽기 (메뉴 구조 포함)"""
    return driver.execute_script("""
        try {
            var app = nexacro.getApplication();
            var topForm = app.mainframe.HFrameSet00.VFrameSet00.TopFrame.form;
            var result = {};

            var dsNames = ['ds_topMenu', 'ds_orgMenu', 'ds_depth1', 'ds_depth2',
                          'ds_myMenu', 'ds_depth1srn', 'ds_Permission'];

            for (var d = 0; d < dsNames.length; d++) {
                var dsName = dsNames[d];
                var ds = topForm[dsName];
                if (!ds) continue;

                var rc = ds.getRowCount ? ds.getRowCount() : 0;
                var cc = ds.getColCount ? ds.getColCount() : 0;
                var colNames = [];
                for (var c = 0; c < cc; c++) colNames.push(ds.getColID(c));

                var rows = [];
                for (var r = 0; r < Math.min(rc, 300); r++) {
                    var row = {};
                    for (var c = 0; c < cc; c++) {
                        var v = ds.getColumn(r, ds.getColID(c));
                        row[ds.getColID(c)] = v != null ? String(v) : '';
                    }
                    rows.push(row);
                }
                result[dsName] = {rows: rc, cols: cc, columns: colNames, data: rows};
            }
            return result;
        } catch(e) {
            return {error: e.message};
        }
    """) or {}


def navigate_to_screen(driver, parent_id, sub_id):
    """상위메뉴 → 서브메뉴 클릭"""
    top_dom = f"{TOP_PREFIX}{parent_id}:icontext"
    click_dom(driver, top_dom, wait=0.8)

    for suffix in [":text", ":icontext", ""]:
        sub_dom = f"{SUB_PREFIX}{parent_id}.form.{sub_id}{suffix}"
        r2 = click_dom(driver, sub_dom, wait=0.3)
        if r2 == "ok":
            return f"ok ({suffix or 'no_suffix'})"
    return "not_found"


def explore_workframe(driver):
    """WorkFrame 데이터셋/그리드/버튼 탐색"""
    for _ in range(16):
        check = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                if (wf && wf.form && wf.form.objects) return true;
                return false;
            } catch(e) { return false; }
        """)
        if check:
            break
        time.sleep(0.5)

    return driver.execute_script("""
        try {
            var app = nexacro.getApplication();
            var info = {frameId: '', datasets: [], grids: [], buttons: [], combos: []};
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
            if (!wf || !wf.form) return info;
            info.frameId = wf.formurl || '';

            function scanForm(f, prefix) {
                if (!f) return;
                if (f.objects) {
                    for (var i = 0; i < f.objects.length; i++) {
                        var obj = f.objects[i];
                        if (obj && obj.typename === 'Dataset') {
                            var cc = 0, rc = 0, cn = [];
                            try {
                                cc = obj.getColCount ? obj.getColCount() : 0;
                                rc = obj.getRowCount ? obj.getRowCount() : 0;
                                for (var c = 0; c < Math.min(cc, 60); c++) cn.push(obj.getColID(c));
                            } catch(e) {}
                            info.datasets.push({name: prefix+obj.name, cols: cc, rows: rc, columns: cn});
                        }
                    }
                }
                if (f.components) {
                    for (var i = 0; i < f.components.length; i++) {
                        var comp = f.components[i];
                        if (!comp) continue;
                        if (comp.typename === 'Grid')
                            info.grids.push({name: prefix+comp.name, bind: comp.binddataset||''});
                        if (comp.typename === 'Button' && comp.name)
                            info.buttons.push({name: prefix+comp.name, text: comp.text||''});
                        if (comp.typename === 'Combo' && comp.name)
                            info.combos.push({name: prefix+comp.name, bind: comp.binddataset||'', text: comp.text||''});
                        if (comp.form) scanForm(comp.form, prefix+comp.name+'.');
                    }
                }
            }
            scanForm(wf.form, '');
            return info;
        } catch(e) {
            return {error: e.message};
        }
    """) or {}


def main():
    print("=" * 70)
    print("BGF 리테일 사이트 전체 탐색 v5")
    print(f"시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

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

        # XHR 인터셉터
        driver.execute_script("""
            window.__xhrLog = [];
            var oO = XMLHttpRequest.prototype.open, oS = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(m,u) { this.__u=u; this.__m=m; return oO.apply(this,arguments); };
            XMLHttpRequest.prototype.send = function(b) {
                var u=this.__u||'';
                if(u && (u.indexOf('/st')>=0 || u.indexOf('/ST')>=0 || u.indexOf('/ss')>=0 || u.indexOf('/SS')>=0)) {
                    window.__xhrLog.push({url:u, method:this.__m, bodyLen:b?b.length:0,
                        body: b ? b.substring(0, 500) : '', ts:new Date().toISOString()});
                }
                return oS.apply(this,arguments);
            };
        """)

        # ===== [0] TopFrame 데이터셋 내용 읽기 =====
        print("\n[0] TopFrame 데이터셋 내용 읽기...")
        tf_data = read_topframe_datasets(driver)
        if tf_data.get("error"):
            print(f"  ERROR: {tf_data['error']}")
        else:
            for ds_name, ds_info in tf_data.items():
                print(f"\n  {ds_name}: {ds_info['rows']}행×{ds_info['cols']}열")
                print(f"    컬럼: {', '.join(ds_info.get('columns', []))}")
                for row in ds_info.get("data", [])[:8]:
                    vals = [f"{k}={v}" for k, v in list(row.items())[:8] if v]
                    if vals:
                        print(f"    → {', '.join(vals)}")
                if ds_info['rows'] > 8:
                    print(f"    ... (총 {ds_info['rows']}행)")

        # ===== [1] 서브메뉴 추출 =====
        print("\n" + "=" * 70)
        print("[1] 서브메뉴 추출")
        print("=" * 70)

        top_menus = [
            ("STBJ000_M0", "발주"),
            ("STJS000_M0", "정산"),
            ("STGJ000_M0", "검수전표"),
            ("STMB000_M0", "매출분석"),
            ("STJK000_M0", "재고"),
            ("STCM000_M0", "커뮤니케이션"),
            ("STJJ000_M0", "점주관리"),
            ("STMS000_M0", "마스터"),
            ("STON001_M0", "온라인(APP)"),
            ("STSE001_M0", "나의 온라인점포"),
        ]

        known = {"STAJ001_M0", "STBJ030_M0", "STBJ010_M0", "STBJ070_M0",
                 "STGJ010_M0", "SS_STBJ460_M0", "STGJ020_M0", "STMB011_M0"}

        all_menus = {}
        unknown_screens = []

        for parent_id, parent_name in top_menus:
            result = discover_submenus(driver, parent_id)
            subs = result.get("subs", [])
            all_menus[parent_id] = {"name": parent_name, "subs": subs}

            print(f"\n  {parent_name} ({parent_id}) [{len(subs)}개]")
            for sub in subs:
                marker = "★" if sub["id"] in known else " "
                print(f"    {marker} {sub['text']} ({sub['id']})")
                if sub["id"] not in known:
                    unknown_screens.append({
                        "parent": parent_id, "parent_name": parent_name,
                        "id": sub["id"], "name": sub["text"], "domId": sub["domId"]
                    })

        print(f"\n  기존 사용: {len(known)}개, 미사용: {len(unknown_screens)}개")

        # ===== [2] 미사용 화면 진입 + 분석 (탭 닫기 포함) =====
        print("\n" + "=" * 70)
        print("[2] 미사용 화면 데이터셋 분석")
        print("=" * 70)

        screen_results = {}
        # 우선순위 화면: 재고, 매출분석, 검수전표 > 발주 > 나머지
        priority_parents = ["STJK000_M0", "STMB000_M0", "STGJ000_M0", "STBJ000_M0",
                           "STJS000_M0", "STMS000_M0", "STJJ000_M0", "STCM000_M0"]
        sorted_screens = sorted(unknown_screens,
                               key=lambda s: (priority_parents.index(s["parent"])
                                             if s["parent"] in priority_parents else 99))

        tab_count = 0
        for i, screen in enumerate(sorted_screens):
            # 탭 6개 열렸으면 전부 닫기
            if tab_count >= 5:
                print("    [탭 정리 중...]")
                close_all_tabs(driver)
                tab_count = 0
                time.sleep(1)

            print(f"\n  [{i+1}/{len(sorted_screens)}] {screen['parent_name']} > {screen['name']} ({screen['id']})")
            driver.execute_script("window.__xhrLog = [];")

            # alert 미리 처리
            dismiss_alert(driver)

            nav = navigate_to_screen(driver, screen["parent"], screen["id"])
            dismiss_alert(driver)

            if "not_found" in nav:
                print(f"    [SKIP] 클릭 실패")
                continue

            tab_count += 1
            print(f"    네비게이션: {nav}")
            time.sleep(3)

            wf = explore_workframe(driver)
            if wf.get("error"):
                print(f"    ERROR: {wf['error']}")
                continue

            frame_url = wf.get("frameId", "")
            print(f"    화면URL: {frame_url}")

            if wf.get("datasets"):
                print(f"    데이터셋 {len(wf['datasets'])}개:")
                for ds in wf["datasets"]:
                    cols = ", ".join(ds["columns"][:12])
                    extra = "..." if len(ds["columns"]) > 12 else ""
                    print(f"      {ds['name']}: {ds['rows']}행×{ds['cols']}열")
                    if cols:
                        print(f"        [{cols}{extra}]")

            if wf.get("grids"):
                for g in wf["grids"]:
                    print(f"    그리드: {g['name']} → {g['bind']}")

            if wf.get("buttons"):
                btns = [f"{b['name']}({b['text']})" for b in wf["buttons"][:6]]
                print(f"    버튼: {', '.join(btns)}")

            xhr = driver.execute_script("return window.__xhrLog || [];")
            if xhr:
                urls = list(set(x["url"] for x in xhr))
                print(f"    API: {', '.join(urls[:5])}")

            screen_results[screen["id"]] = {
                "name": screen["name"], "parent": screen["parent_name"],
                "frameUrl": frame_url,
                "datasets": wf.get("datasets", []),
                "grids": wf.get("grids", []),
                "buttons": wf.get("buttons", []),
                "combos": wf.get("combos", []),
                "xhr": xhr[:10]
            }

        # ===== [3] 기존 화면 재분석 =====
        print("\n" + "=" * 70)
        print("[3] 기존 화면 재분석")
        print("=" * 70)

        close_all_tabs(driver)
        time.sleep(1)

        existing = [
            ("STBJ000_M0", "STBJ030_M0", "단품별 발주"),
            ("STBJ000_M0", "STBJ070_M0", "발주현황조회"),
            ("STMB000_M0", "STMB011_M0", "중분류별 매출"),
            ("STGJ000_M0", "STGJ010_M0", "센터매입 조회"),
            ("STGJ000_M0", "STGJ020_M0", "통합 전표 조회"),
        ]

        for idx, (parent, sub, name) in enumerate(existing):
            if idx > 0 and idx % 4 == 0:
                close_all_tabs(driver)
                time.sleep(1)

            print(f"\n  [{name}] ({sub})")
            driver.execute_script("window.__xhrLog = [];")
            dismiss_alert(driver)

            nav = navigate_to_screen(driver, parent, sub)
            dismiss_alert(driver)
            print(f"    네비게이션: {nav}")
            time.sleep(4)

            wf = explore_workframe(driver)
            frame_url = wf.get("frameId", "")
            print(f"    화면URL: {frame_url}")

            if wf.get("datasets"):
                print(f"    데이터셋 {len(wf['datasets'])}개:")
                for ds in wf["datasets"]:
                    cols = ", ".join(ds["columns"][:15])
                    extra = "..." if len(ds["columns"]) > 15 else ""
                    print(f"      {ds['name']}: {ds['rows']}행×{ds['cols']}열")
                    if cols:
                        print(f"        [{cols}{extra}]")

            if wf.get("grids"):
                for g in wf["grids"]:
                    print(f"    그리드: {g['name']} → {g['bind']}")

            if wf.get("buttons"):
                btns = [f"{b['name']}({b['text']})" for b in wf["buttons"][:6]]
                print(f"    버튼: {', '.join(btns)}")

            xhr = driver.execute_script("return window.__xhrLog || [];")
            if xhr:
                urls = list(set(x["url"] for x in xhr))
                print(f"    API: {', '.join(urls[:5])}")

            screen_results[sub + "_existing"] = {
                "name": name, "frameUrl": frame_url,
                "datasets": wf.get("datasets", []),
                "grids": wf.get("grids", []),
                "buttons": wf.get("buttons", []),
                "combos": wf.get("combos", []),
                "xhr": xhr[:10]
            }

        # 저장
        output = {
            "timestamp": datetime.now().isoformat(),
            "topFrameDatasets": tf_data,
            "menus": all_menus,
            "unknownScreens": unknown_screens,
            "screenAnalysis": screen_results
        }
        out_path = project_root / "data" / "bgf_site_exploration_v5.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2, default=str)

        print(f"\n\n결과 저장: {out_path}")
        print("=" * 70)
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
