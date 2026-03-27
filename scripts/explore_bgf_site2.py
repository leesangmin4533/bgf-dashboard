# -*- coding: utf-8 -*-
"""
BGF 리테일 사이트 탐색 v2 - DOM 기반 메뉴 추출 + 화면별 데이터셋 분석
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


def nx(driver, js):
    """nexacro.getApplication() 래퍼"""
    return driver.execute_script(f"""
        try {{
            var app = nexacro.getApplication();
            {js}
        }} catch(e) {{ return {{__error: e.message}}; }}
    """)


def get_all_menus_via_dom(driver):
    """DOM에서 메뉴 ID 패턴으로 전체 메뉴 구조 추출"""
    return driver.execute_script(r"""
        var result = [];
        // 메뉴 텍스트가 있는 모든 요소 찾기
        var allEls = document.querySelectorAll('[id*="_M0"]');
        for (var i = 0; i < allEls.length; i++) {
            var el = allEls[i];
            var id = el.id || '';
            // TopFrame 메뉴 항목만 (너무 많은 결과 방지)
            if (id.indexOf('TopFrame') >= 0 || id.indexOf('topMenu') >= 0) {
                var text = '';
                var textEl = el.querySelector('[class*="text"]') || el;
                text = textEl.textContent || textEl.innerText || '';
                text = text.trim();
                if (text && id) {
                    result.push({id: id, text: text});
                }
            }
        }
        return result;
    """) or []


def get_menus_nexacro(driver):
    """넥사크로 API로 메뉴 데이터셋에서 직접 메뉴 구조 추출"""
    result = nx(driver, r"""
        var menus = [];
        var topForm = app.mainframe.HFrameSet00.VFrameSet00.TopFrame.form;

        // ds_menu 데이터셋 찾기
        if (topForm.objects) {
            for (var i = 0; i < topForm.objects.length; i++) {
                var obj = topForm.objects[i];
                if (obj && obj.typename === 'Dataset' && obj.name.toLowerCase().indexOf('menu') >= 0) {
                    var colCount = obj.getColCount ? obj.getColCount() : 0;
                    var rowCount = obj.getRowCount ? obj.getRowCount() : 0;
                    var colNames = [];
                    for (var c = 0; c < colCount; c++) {
                        colNames.push(obj.getColID(c));
                    }

                    var rows = [];
                    for (var r = 0; r < Math.min(rowCount, 200); r++) {
                        var row = {};
                        for (var c = 0; c < colCount; c++) {
                            var val = obj.getColumn(r, obj.getColID(c));
                            row[obj.getColID(c)] = val ? String(val) : '';
                        }
                        rows.push(row);
                    }
                    menus.push({
                        dsName: obj.name,
                        colCount: colCount,
                        rowCount: rowCount,
                        columns: colNames,
                        rows: rows
                    });
                }
            }
        }

        // 모든 데이터셋 이름도 수집
        var allDs = [];
        if (topForm.objects) {
            for (var i = 0; i < topForm.objects.length; i++) {
                var obj = topForm.objects[i];
                if (obj && obj.typename === 'Dataset') {
                    allDs.push({
                        name: obj.name,
                        rows: obj.getRowCount ? obj.getRowCount() : 0,
                        cols: obj.getColCount ? obj.getColCount() : 0
                    });
                }
            }
        }

        return {menus: menus, allDatasets: allDs};
    """)
    return result or {}


def navigate_menu(driver, menu_id):
    """넥사크로 메뉴 네비게이션 함수 직접 호출"""
    result = nx(driver, f"""
        var topForm = app.mainframe.HFrameSet00.VFrameSet00.TopFrame.form;
        // fn_menu_click 또는 유사 함수 찾기
        if (typeof topForm.fn_menu_click === 'function') {{
            topForm.fn_menu_click('{menu_id}');
            return 'fn_menu_click';
        }}
        // gfn_setMenu 시도
        if (typeof topForm.gfn_setMenu === 'function') {{
            topForm.gfn_setMenu('{menu_id}');
            return 'gfn_setMenu';
        }}
        // 직접 DOM 클릭
        var el = null;
        var allEls = document.querySelectorAll('[id*="{menu_id}"]');
        for (var i = 0; i < allEls.length; i++) {{
            if (allEls[i].id.indexOf('text') >= 0 || allEls[i].id.indexOf('icon') >= 0) {{
                el = allEls[i];
                break;
            }}
        }}
        if (!el && allEls.length > 0) el = allEls[0];
        if (el) {{
            el.click();
            return 'dom_click';
        }}
        return 'not_found';
    """)
    return result


def explore_current_screen(driver):
    """현재 열린 WorkFrame의 데이터셋/그리드/버튼 구조 탐색"""
    result = nx(driver, r"""
        var info = {frameId: '', datasets: [], grids: [], buttons: []};
        var frameset = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;

        // WorkFrame 확인
        var wf = frameset.WorkFrame;
        if (!wf) return info;

        // 현재 열린 화면 ID
        info.frameId = wf.formurl || '';

        var form = wf.form;
        if (!form) return info;

        function scanForm(f, prefix) {
            if (!f) return;
            if (f.objects) {
                for (var i = 0; i < f.objects.length; i++) {
                    var obj = f.objects[i];
                    if (obj && obj.typename === 'Dataset') {
                        var colCount = 0, rowCount = 0, colNames = [];
                        try {
                            colCount = obj.getColCount ? obj.getColCount() : 0;
                            rowCount = obj.getRowCount ? obj.getRowCount() : 0;
                            for (var c = 0; c < Math.min(colCount, 60); c++) {
                                colNames.push(obj.getColID(c));
                            }
                        } catch(e) {}
                        info.datasets.push({
                            name: prefix + obj.name,
                            colCount: colCount, rowCount: rowCount,
                            columns: colNames
                        });
                    }
                }
            }
            if (f.components) {
                for (var i = 0; i < f.components.length; i++) {
                    var comp = f.components[i];
                    if (!comp) continue;
                    if (comp.typename === 'Grid') {
                        info.grids.push({
                            name: prefix + comp.name,
                            bind: comp.binddataset || ''
                        });
                    }
                    if (comp.typename === 'Button' && comp.name) {
                        info.buttons.push({
                            name: prefix + comp.name,
                            text: comp.text || ''
                        });
                    }
                    if (comp.form) scanForm(comp.form, prefix + comp.name + '.');
                }
            }
        }
        scanForm(form, '');
        return info;
    """)
    return result or {"frameId": "", "datasets": [], "grids": [], "buttons": []}


def main():
    print("=" * 70)
    print("BGF 리테일 사이트 탐색 v2")
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
            var origOpen = XMLHttpRequest.prototype.open;
            var origSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(m, u) {
                this.__url = u; this.__method = m;
                return origOpen.apply(this, arguments);
            };
            XMLHttpRequest.prototype.send = function(b) {
                var u = this.__url || '';
                if (u && u.indexOf('/st') >= 0) {
                    window.__xhrLog.push({
                        url: u, method: this.__method,
                        bodyLen: b ? b.length : 0,
                        ts: new Date().toISOString()
                    });
                }
                return origSend.apply(this, arguments);
            };
        """)

        # ===== [1] TopFrame 데이터셋 + 메뉴 데이터셋 =====
        print("\n[1] TopFrame 데이터셋 + 메뉴 구조 탐색")
        menu_data = get_menus_nexacro(driver)

        if menu_data.get("allDatasets"):
            print(f"\n  TopFrame 데이터셋: {len(menu_data['allDatasets'])}개")
            for ds in menu_data["allDatasets"]:
                print(f"    {ds['name']}: {ds['rows']}행 × {ds['cols']}열")

        menus_by_parent = {}  # parent_id -> [child menus]
        all_leaf_menus = []   # 최종 화면 메뉴

        if menu_data.get("menus"):
            for mds in menu_data["menus"]:
                print(f"\n  메뉴 데이터셋: {mds['dsName']} ({mds['rowCount']}행)")
                print(f"    컬럼: {', '.join(mds['columns'])}")

                for row in mds.get("rows", []):
                    menu_id = row.get("MENU_ID") or row.get("menu_id") or ""
                    menu_nm = row.get("MENU_NM") or row.get("menu_nm") or ""
                    parent = row.get("PARENT_MENU_ID") or row.get("parent_menu_id") or ""
                    level = row.get("MENU_LEVEL") or row.get("LVL") or ""
                    url = row.get("MENU_URL") or row.get("URL") or row.get("PGM_ID") or ""

                    if parent not in menus_by_parent:
                        menus_by_parent[parent] = []
                    menus_by_parent[parent].append({
                        "id": menu_id, "name": menu_nm,
                        "parent": parent, "level": level, "url": url
                    })

                    # 화면 URL이 있는 것이 리프 메뉴
                    if url and "_M0" in url:
                        all_leaf_menus.append({
                            "id": menu_id, "name": menu_nm,
                            "parent": parent, "url": url
                        })

        # 메뉴 트리 출력
        if all_leaf_menus:
            print(f"\n  리프 메뉴 (화면): {len(all_leaf_menus)}개")
            for m in all_leaf_menus:
                print(f"    {m['url']}: {m['name']}")

        # 현재 사용 중인 화면
        known = {"STAJ001_M0", "STBJ030_M0", "STBJ010_M0", "STBJ070_M0",
                 "STGJ010_M0", "SS_STBJ460_M0", "STGJ020_M0", "STMB011_M0"}

        unknown = [m for m in all_leaf_menus if m["url"] not in known]
        print(f"\n  미사용 화면: {len(unknown)}개")
        for m in unknown:
            print(f"    {m['url']}: {m['name']}")

        # ===== [2] 주요 화면 진입 + 데이터셋 분석 =====
        print("\n" + "=" * 70)
        print("[2] 화면별 데이터셋 분석")
        print("=" * 70)

        # 탐색 대상: 미사용 + 기존 화면
        targets = unknown[:15]  # 미사용 최대 15개
        # 기존 화면도 추가
        for frame_id in ["STBJ030_M0", "STBJ070_M0", "STMB011_M0"]:
            targets.append({"url": frame_id, "name": f"[기존] {frame_id}"})

        all_screen_info = {}

        for i, target in enumerate(targets):
            frame_id = target["url"]
            name = target["name"]

            print(f"\n  [{i+1}/{len(targets)}] {name} ({frame_id})")

            # XHR 로그 클리어
            driver.execute_script("window.__xhrLog = [];")

            # 메뉴 클릭으로 이동
            nav_result = navigate_menu(driver, frame_id)
            print(f"    네비게이션: {nav_result}")
            time.sleep(3)

            # 데이터셋 분석
            screen_info = explore_current_screen(driver)
            frame_url = screen_info.get("frameId", "")
            print(f"    현재 화면: {frame_url}")

            if screen_info.get("datasets"):
                print(f"    데이터셋 {len(screen_info['datasets'])}개:")
                for ds in screen_info["datasets"]:
                    cols_str = ", ".join(ds["columns"][:12])
                    extra = "..." if len(ds["columns"]) > 12 else ""
                    print(f"      {ds['name']}: {ds['rowCount']}행×{ds['colCount']}열")
                    if cols_str:
                        print(f"        [{cols_str}{extra}]")

            if screen_info.get("grids"):
                print(f"    그리드 {len(screen_info['grids'])}개:")
                for g in screen_info["grids"]:
                    print(f"      {g['name']} → {g['bind']}")

            if screen_info.get("buttons"):
                btns = [f"{b['name']}({b['text']})" for b in screen_info["buttons"][:8]]
                print(f"    버튼: {', '.join(btns)}")

            # XHR 캡처
            xhr_log = driver.execute_script("return window.__xhrLog || [];")
            if xhr_log:
                urls = list(set(x.get("url", "") for x in xhr_log))
                print(f"    API 호출 {len(xhr_log)}건:")
                for url in urls[:5]:
                    print(f"      → {url}")

            all_screen_info[frame_id] = {
                "name": name,
                "frameUrl": frame_url,
                "datasets": screen_info.get("datasets", []),
                "grids": screen_info.get("grids", []),
                "buttons": screen_info.get("buttons", []),
                "xhrCalls": xhr_log[:10]
            }

        # 결과 저장
        output = {
            "timestamp": datetime.now().isoformat(),
            "topFrameDatasets": menu_data.get("allDatasets", []),
            "allLeafMenus": all_leaf_menus,
            "unknownMenus": unknown,
            "screens": all_screen_info
        }
        out_path = project_root / "data" / "bgf_site_exploration.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2, default=str)

        print(f"\n결과 저장: {out_path}")
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
