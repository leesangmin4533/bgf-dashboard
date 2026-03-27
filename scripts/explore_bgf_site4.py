# -*- coding: utf-8 -*-
"""
BGF 리테일 사이트 탐색 v4 - 검증된 클릭 패턴 + nexacro API 탐색
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
    """검증된 MouseEvent 시뮬레이션 클릭"""
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


def discover_submenus(driver, parent_id):
    """상위 메뉴 클릭 후 열린 서브메뉴 패널에서 모든 자식 메뉴 추출"""
    # 1) 상위 메뉴 클릭으로 드롭다운 열기
    top_dom = f"{TOP_PREFIX}{parent_id}:icontext"
    click_dom(driver, top_dom, wait=0.8)

    # 2) pdiv_topMenu 패널 내 모든 _M0 요소 검색
    panel_prefix = f"{SUB_PREFIX}{parent_id}"
    subs = driver.execute_script(f"""
        var results = [];
        var prefix = '{panel_prefix}';
        // 패널 자체 확인
        var panelIds = [];
        var all = document.querySelectorAll('[id^="' + prefix + '"]');
        for (var i = 0; i < all.length; i++) {{
            var id = all[i].id;
            // _M0:text 패턴 찾기 (서브메뉴 텍스트 요소)
            var m = id.match(/\\.form\\.((?:SS_)?ST[A-Z]{{2,4}}\\d{{2,3}}_M0):(?:text|icontext)$/);
            if (m) {{
                var text = (all[i].textContent || '').trim();
                if (text && results.findIndex(x => x.id === m[1]) < 0) {{
                    results.push({{id: m[1], text: text, domId: id}});
                }}
            }}
        }}
        // 패널이 있는지 여부도 반환
        var panelEl = document.querySelector('[id^="' + prefix + '.form"]');
        return {{subs: results, panelExists: !!panelEl, totalEls: all.length}};
    """) or {"subs": [], "panelExists": False, "totalEls": 0}

    # 3) 드롭다운 닫기 (body 클릭)
    driver.execute_script("document.body.dispatchEvent(new MouseEvent('click', {bubbles:true}));")
    time.sleep(0.3)

    return subs


def discover_submenus_nexacro(driver):
    """nexacro 내부 API로 전체 메뉴 트리 직접 추출 (대안)"""
    return driver.execute_script("""
        try {
            var app = nexacro.getApplication();
            var topForm = app.mainframe.HFrameSet00.VFrameSet00.TopFrame.form;
            var result = {datasets: [], menuTree: [], allObjects: []};

            // 1) TopFrame의 모든 객체 이름/타입 수집
            if (topForm.objects) {
                for (var i = 0; i < topForm.objects.length; i++) {
                    var obj = topForm.objects[i];
                    if (obj) result.allObjects.push({name: obj.name, type: obj.typename});
                }
            }

            // 2) 모든 Dataset에서 메뉴 관련 데이터 찾기
            if (topForm.objects) {
                for (var i = 0; i < topForm.objects.length; i++) {
                    var obj = topForm.objects[i];
                    if (!obj || obj.typename !== 'Dataset') continue;
                    var rc = obj.getRowCount ? obj.getRowCount() : 0;
                    var cc = obj.getColCount ? obj.getColCount() : 0;
                    var colNames = [];
                    for (var c = 0; c < cc; c++) colNames.push(obj.getColID(c));

                    var dsInfo = {name: obj.name, rows: rc, cols: cc, columns: colNames};

                    // 메뉴 관련 데이터셋이면 내용도 수집
                    if (obj.name.toLowerCase().indexOf('menu') >= 0 ||
                        obj.name.toLowerCase().indexOf('pgm') >= 0 ||
                        rc > 0 && rc < 200) {
                        var data = [];
                        for (var r = 0; r < Math.min(rc, 200); r++) {
                            var row = {};
                            for (var c = 0; c < cc; c++) {
                                var v = obj.getColumn(r, obj.getColID(c));
                                row[obj.getColID(c)] = v != null ? String(v) : '';
                            }
                            data.push(row);
                        }
                        dsInfo.data = data;
                    }
                    result.datasets.push(dsInfo);
                }
            }

            // 3) div_topMenu의 컴포넌트 트리 탐색
            var topMenu = topForm.div_topMenu;
            if (topMenu && topMenu.form && topMenu.form.components) {
                for (var i = 0; i < topMenu.form.components.length; i++) {
                    var comp = topMenu.form.components[i];
                    if (comp && comp.name && comp.name.indexOf('_M0') >= 0) {
                        result.menuTree.push({
                            name: comp.name, type: comp.typename,
                            text: comp.text || '', visible: comp.visible
                        });
                    }
                }
            }

            // 4) pdiv_topMenu_ 패널들 탐색
            if (topForm.components) {
                for (var i = 0; i < topForm.components.length; i++) {
                    var comp = topForm.components[i];
                    if (!comp || !comp.name) continue;
                    if (comp.name.indexOf('pdiv_topMenu_') >= 0) {
                        var panelInfo = {name: comp.name, type: comp.typename, children: []};
                        if (comp.form && comp.form.components) {
                            for (var j = 0; j < comp.form.components.length; j++) {
                                var child = comp.form.components[j];
                                if (child && child.name) {
                                    panelInfo.children.push({
                                        name: child.name, type: child.typename,
                                        text: child.text || ''
                                    });
                                }
                            }
                        }
                        result.menuTree.push(panelInfo);
                    }
                }
            }

            return result;
        } catch(e) {
            return {error: e.message};
        }
    """) or {}


def navigate_to_screen(driver, parent_id, sub_id):
    """상위메뉴 클릭 → 서브메뉴 클릭으로 화면 이동"""
    # 상위 메뉴 클릭
    top_dom = f"{TOP_PREFIX}{parent_id}:icontext"
    r1 = click_dom(driver, top_dom, wait=0.8)

    # 서브메뉴 클릭 (3가지 접미사 시도)
    for suffix in [":text", ":icontext", ""]:
        sub_dom = f"{SUB_PREFIX}{parent_id}.form.{sub_id}{suffix}"
        r2 = click_dom(driver, sub_dom, wait=0.3)
        if r2 == "ok":
            return f"ok ({suffix or 'no_suffix'})"

    return "not_found"


def explore_workframe(driver):
    """WorkFrame 데이터셋/그리드/버튼 + 로드 대기"""
    # WorkFrame이 로드될 때까지 최대 8초 대기
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
    print("BGF 리테일 사이트 전체 탐색 v4")
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

        # ===== [0] nexacro 내부 메뉴 데이터 탐색 =====
        print("\n[0] nexacro 내부 메뉴 데이터 탐색...")
        nx_data = discover_submenus_nexacro(driver)

        if nx_data.get("error"):
            print(f"  ERROR: {nx_data['error']}")
        else:
            if nx_data.get("allObjects"):
                print(f"  TopFrame 객체: {len(nx_data['allObjects'])}개")
                for obj in nx_data["allObjects"]:
                    print(f"    {obj['name']} ({obj['type']})")

            if nx_data.get("datasets"):
                print(f"\n  TopFrame 데이터셋: {len(nx_data['datasets'])}개")
                for ds in nx_data["datasets"]:
                    print(f"    {ds['name']}: {ds['rows']}행×{ds['cols']}열 [{', '.join(ds.get('columns', [])[:10])}]")
                    if ds.get("data"):
                        for row in ds["data"][:5]:
                            vals = [f"{k}={v}" for k,v in list(row.items())[:6] if v]
                            if vals:
                                print(f"      → {', '.join(vals)}")

            if nx_data.get("menuTree"):
                print(f"\n  메뉴 트리 컴포넌트: {len(nx_data['menuTree'])}개")
                for item in nx_data["menuTree"]:
                    if item.get("children"):
                        print(f"    {item['name']} ({item['type']})")
                        for child in item["children"]:
                            print(f"      → {child['name']}: {child['text']} ({child['type']})")
                    else:
                        print(f"    {item['name']}: {item.get('text', '')} ({item['type']})")

        # ===== [1] DOM 기반 서브메뉴 추출 =====
        print("\n" + "=" * 70)
        print("[1] DOM 기반 서브메뉴 추출 (상위메뉴 클릭 → 패널 스캔)")
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
            panel_exists = result.get("panelExists", False)
            total_els = result.get("totalEls", 0)

            print(f"\n  {parent_name} ({parent_id}) - 패널: {'있음' if panel_exists else '없음'}, DOM요소: {total_els}개")
            all_menus[parent_id] = {"name": parent_name, "subs": subs}

            if subs:
                for sub in subs:
                    marker = "★" if sub["id"] in known else "  "
                    print(f"    {marker} {sub['text']} ({sub['id']})")
                    if sub["id"] not in known:
                        unknown_screens.append({
                            "parent": parent_id, "parent_name": parent_name,
                            "id": sub["id"], "name": sub["text"], "domId": sub["domId"]
                        })
            elif panel_exists:
                # 패널은 있지만 서브메뉴를 못 찾은 경우 - 전체 ID 덤프
                dump = driver.execute_script(f"""
                    var ids = [];
                    var prefix = '{SUB_PREFIX}{parent_id}';
                    var all = document.querySelectorAll('[id^="' + prefix + '"]');
                    for (var i = 0; i < Math.min(all.length, 30); i++) {{
                        ids.push(all[i].id);
                    }}
                    return ids;
                """) or []
                print(f"    (패널 내 DOM ID 샘플: {len(dump)}개)")
                for d in dump[:10]:
                    print(f"      {d}")
            else:
                # 패널이 없으면 다른 방법 시도: 전체 DOM에서 해당 패턴 검색
                dump = driver.execute_script(f"""
                    var ids = [];
                    var els = document.querySelectorAll('[id*="pdiv_topMenu_{parent_id}"]');
                    for (var i = 0; i < els.length; i++) ids.push(els[i].id);
                    // 대안: 전체 ID에서 parent 관련 패턴 검색
                    if (ids.length === 0) {{
                        var code = '{parent_id}'.substring(0, 4);  // STBJ, STJS 등
                        var all = document.querySelectorAll('[id*="' + code + '"]');
                        for (var i = 0; i < Math.min(all.length, 20); i++) {{
                            ids.push(all[i].id);
                        }}
                    }}
                    return ids;
                """) or []
                if dump:
                    print(f"    (관련 DOM ID 검색: {len(dump)}개)")
                    for d in dump[:8]:
                        print(f"      {d}")

        print(f"\n  미사용 화면 총: {len(unknown_screens)}개")

        # ===== [2] 미사용 화면 진입 + 데이터셋 분석 =====
        print("\n" + "=" * 70)
        print("[2] 미사용 화면 데이터셋 분석")
        print("=" * 70)

        screen_results = {}
        targets = unknown_screens[:20]

        for i, screen in enumerate(targets):
            print(f"\n  [{i+1}/{len(targets)}] {screen['parent_name']} > {screen['name']} ({screen['id']})")
            driver.execute_script("window.__xhrLog = [];")

            nav = navigate_to_screen(driver, screen["parent"], screen["id"])
            print(f"    네비게이션: {nav}")

            if "not_found" in nav:
                continue

            time.sleep(4)

            wf = explore_workframe(driver)
            if wf.get("error"):
                print(f"    ERROR: {wf['error']}")
                continue

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
                btns = [f"{b['name']}({b['text']})" for b in wf["buttons"][:8]]
                print(f"    버튼: {', '.join(btns)}")

            xhr = driver.execute_script("return window.__xhrLog || [];")
            if xhr:
                urls = list(set(x["url"] for x in xhr))
                print(f"    API 호출:")
                for url in urls[:5]:
                    print(f"      {url}")

            screen_results[screen["id"]] = {
                "name": screen["name"], "parent": screen["parent_name"],
                "frameUrl": frame_url,
                "datasets": wf.get("datasets", []),
                "grids": wf.get("grids", []),
                "buttons": wf.get("buttons", []),
                "combos": wf.get("combos", []),
                "xhr": xhr[:10]
            }

        # ===== [3] 기존 화면 미활용 데이터셋 확인 =====
        print("\n" + "=" * 70)
        print("[3] 기존 화면 재분석 (미활용 데이터셋)")
        print("=" * 70)

        existing = [
            ("STBJ000_M0", "STBJ030_M0", "단품별 발주"),
            ("STBJ000_M0", "STBJ070_M0", "발주현황조회"),
            ("STBJ000_M0", "STBJ010_M0", "카테고리 발주"),
            ("STMB000_M0", "STMB011_M0", "중분류별 매출"),
            ("STGJ000_M0", "STGJ010_M0", "센터매입 조회"),
            ("STGJ000_M0", "STGJ020_M0", "통합 전표 조회"),
        ]

        for parent, sub, name in existing:
            print(f"\n  [{name}] ({sub})")
            driver.execute_script("window.__xhrLog = [];")

            nav = navigate_to_screen(driver, parent, sub)
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
                btns = [f"{b['name']}({b['text']})" for b in wf["buttons"][:8]]
                print(f"    버튼: {', '.join(btns)}")

            xhr = driver.execute_script("return window.__xhrLog || [];")
            if xhr:
                urls = list(set(x["url"] for x in xhr))
                print(f"    API 호출: {', '.join(urls[:5])}")

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
            "nexacroData": nx_data,
            "menus": all_menus,
            "unknownScreens": unknown_screens,
            "screenAnalysis": screen_results
        }
        out_path = project_root / "data" / "bgf_site_exploration_v4.json"
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
