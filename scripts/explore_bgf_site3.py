# -*- coding: utf-8 -*-
"""
BGF 리테일 사이트 탐색 v3 - DOM ID 패턴 기반 + 검증된 메뉴 클릭
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


def click_by_dom_id(driver, dom_id):
    """DOM ID로 요소 클릭 (MouseEvent 시뮬레이션)"""
    return driver.execute_script(f"""
        var el = document.getElementById('{dom_id}');
        if (!el || el.offsetParent === null) return false;
        el.scrollIntoView({{block:'center', inline:'center'}});
        var r = el.getBoundingClientRect();
        var o = {{bubbles:true, cancelable:true, view:window,
                  clientX:r.left+r.width/2, clientY:r.top+r.height/2}};
        el.dispatchEvent(new MouseEvent('mousedown', o));
        el.dispatchEvent(new MouseEvent('mouseup', o));
        el.dispatchEvent(new MouseEvent('click', o));
        return true;
    """)


def get_all_menu_ids(driver):
    """DOM에서 _M0 패턴의 모든 메뉴 ID와 텍스트 추출"""
    return driver.execute_script(f"""
        var result = {{topMenus: [], subMenus: {{}}}};
        // 상위 메뉴: div_topMenu 내 모든 _M0 요소
        var prefix = '{TOP_PREFIX}';
        var topEls = document.querySelectorAll('[id^="' + prefix + '"]');
        var seen = new Set();
        for (var i = 0; i < topEls.length; i++) {{
            var fullId = topEls[i].id;
            // STXX000_M0 패턴 추출
            var match = fullId.match(/(ST[A-Z]{{2}}\\d{{3}}_M0|SS_ST[A-Z]{{2}}\\d{{3}}_M0|STON\\d{{3}}_M0|STSE\\d{{3}}_M0)/);
            if (match && !seen.has(match[1])) {{
                seen.add(match[1]);
                var textEl = document.getElementById(fullId);
                var text = textEl ? (textEl.textContent || '').trim() : '';
                result.topMenus.push({{id: match[1], text: text, domId: fullId}});
            }}
        }}

        // 서브메뉴: pdiv_topMenu_ 패널 내 모든 _M0 요소
        var subPrefix = '{SUB_PREFIX}';
        var subPanels = document.querySelectorAll('[id^="' + subPrefix + '"]');
        for (var i = 0; i < subPanels.length; i++) {{
            var panelId = subPanels[i].id;
            // 부모 메뉴 ID 추출
            var parentMatch = panelId.match(/pdiv_topMenu_(ST[A-Z]{{2}}\\d{{3}}_M0|SS_ST|STON|STSE)/);
            if (!parentMatch) continue;

            // 패널 내 모든 _M0 요소
            var subEls = subPanels[i].querySelectorAll('[id*="_M0"]');
            var subSeen = new Set();
            for (var j = 0; j < subEls.length; j++) {{
                var subFullId = subEls[j].id;
                var subMatch = subFullId.match(/(ST[A-Z]{{2}}\\d{{3}}_M0|SS_ST[A-Z]{{2,4}}\\d{{2,3}}_M0)/);
                if (subMatch && !subSeen.has(subMatch[1])) {{
                    subSeen.add(subMatch[1]);
                    var text = (subEls[j].textContent || '').trim();
                    // 부모 패널 ID에서 부모 메뉴 추출
                    var parentId = panelId.replace(subPrefix, '').split('.')[0];
                    if (!result.subMenus[parentId]) result.subMenus[parentId] = [];
                    result.subMenus[parentId].push({{
                        id: subMatch[1], text: text, domId: subFullId
                    }});
                }}
            }}
        }}
        return result;
    """) or {"topMenus": [], "subMenus": {}}


def explore_workframe(driver):
    """WorkFrame의 데이터셋/그리드/버튼 탐색"""
    return driver.execute_script("""
        try {
            var app = nexacro.getApplication();
            var info = {frameId: '', datasets: [], grids: [], buttons: []};
            var frameset = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
            var wf = frameset.WorkFrame;
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
    print("BGF 리테일 사이트 전체 탐색 v3")
    print(f"시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    analyzer = SalesAnalyzer()
    try:
        analyzer.setup_driver()
        analyzer.connect()
        time.sleep(SA_LOGIN_WAIT)
        if not analyzer.do_login():
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
                if(u && (u.indexOf('/st')>=0 || u.indexOf('/ST')>=0 || u.indexOf('/ss')>=0)) {
                    window.__xhrLog.push({url:u, method:this.__m, bodyLen:b?b.length:0, ts:new Date().toISOString()});
                }
                return oS.apply(this,arguments);
            };
        """)

        # ===== [1] 메뉴 구조 추출 (DOM 기반) =====
        print("\n[1] 메뉴 구조 추출...")

        # 상위 메뉴를 하나씩 호버해서 서브메뉴 DOM을 생성
        top_menus = [
            "STBJ000_M0", "STJS000_M0", "STGJ000_M0", "STMB000_M0",
            "STJK000_M0", "STCM000_M0", "STJJ000_M0", "STMS000_M0",
            "STON001_M0", "STSE001_M0"
        ]
        top_names = [
            "발주", "정산", "검수전표", "매출분석",
            "재고", "커뮤니케이션", "점주관리", "마스터",
            "온라인(APP)", "나의 온라인점포"
        ]

        for tm in top_menus:
            dom_id = f"{TOP_PREFIX}{tm}:icontext"
            driver.execute_script(f"""
                var el = document.getElementById('{dom_id}');
                if (el) {{
                    var r = el.getBoundingClientRect();
                    el.dispatchEvent(new MouseEvent('mouseover', {{
                        bubbles:true, clientX:r.left+r.width/2, clientY:r.top+r.height/2
                    }}));
                }}
            """)
            time.sleep(0.3)

        time.sleep(0.5)
        menu_info = get_all_menu_ids(driver)

        # 아무 데나 클릭해서 드롭다운 닫기
        driver.execute_script("""
            document.body.dispatchEvent(new MouseEvent('click', {bubbles:true}));
        """)
        time.sleep(0.3)

        # 메뉴 트리 출력
        known = {"STAJ001_M0", "STBJ030_M0", "STBJ010_M0", "STBJ070_M0",
                 "STGJ010_M0", "SS_STBJ460_M0", "STGJ020_M0", "STMB011_M0"}

        all_screens = []
        for i, tm in enumerate(top_menus):
            name = top_names[i] if i < len(top_names) else tm
            subs = menu_info.get("subMenus", {}).get(tm, [])
            print(f"\n  {name} ({tm})")
            for sub in subs:
                marker = "★사용중" if sub["id"] in known else "미사용"
                print(f"    [{marker}] {sub['text']} ({sub['id']})")
                if sub["id"] not in known:
                    all_screens.append({
                        "parent": tm, "parent_name": name,
                        "id": sub["id"], "name": sub["text"],
                        "domId": sub["domId"]
                    })

        print(f"\n  총 미사용 화면: {len(all_screens)}개")

        # ===== [2] 미사용 화면 진입 + 분석 =====
        print("\n" + "=" * 70)
        print("[2] 미사용 화면 데이터셋 분석")
        print("=" * 70)

        results = {}

        for i, screen in enumerate(all_screens[:20]):
            print(f"\n  [{i+1}/{min(len(all_screens),20)}] {screen['parent_name']} > {screen['name']} ({screen['id']})")

            driver.execute_script("window.__xhrLog = [];")

            # 상위 메뉴 호버
            top_dom = f"{TOP_PREFIX}{screen['parent']}:icontext"
            driver.execute_script(f"""
                var el = document.getElementById('{top_dom}');
                if (el) {{
                    var r = el.getBoundingClientRect();
                    el.dispatchEvent(new MouseEvent('mouseover', {{
                        bubbles:true, clientX:r.left+r.width/2, clientY:r.top+r.height/2
                    }}));
                }}
            """)
            time.sleep(0.5)

            # 서브메뉴 클릭
            sub_dom = screen["domId"]
            clicked = click_by_dom_id(driver, sub_dom)
            if not clicked:
                # :text 접미사 시도
                for suffix in [":text", ":icontext", ""]:
                    alt_id = f"{SUB_PREFIX}{screen['parent']}.form.{screen['id']}{suffix}"
                    clicked = click_by_dom_id(driver, alt_id)
                    if clicked:
                        break

            if not clicked:
                print(f"    [SKIP] 클릭 실패")
                continue

            time.sleep(3)

            # 화면 데이터셋 분석
            wf_info = explore_workframe(driver)
            if wf_info.get("error"):
                print(f"    [ERROR] {wf_info['error']}")
                continue

            print(f"    화면: {wf_info.get('frameId', '?')}")

            if wf_info.get("datasets"):
                print(f"    데이터셋 {len(wf_info['datasets'])}개:")
                for ds in wf_info["datasets"]:
                    cols_str = ", ".join(ds["columns"][:12])
                    extra = "..." if len(ds["columns"]) > 12 else ""
                    print(f"      {ds['name']}: {ds['rows']}행×{ds['cols']}열")
                    if cols_str:
                        print(f"        [{cols_str}{extra}]")

            if wf_info.get("grids"):
                for g in wf_info["grids"]:
                    print(f"    그리드: {g['name']} → {g['bind']}")

            if wf_info.get("buttons"):
                btns = [f"{b['name']}({b['text']})" for b in wf_info["buttons"][:6]]
                print(f"    버튼: {', '.join(btns)}")

            xhr = driver.execute_script("return window.__xhrLog || [];")
            if xhr:
                urls = list(set(x["url"] for x in xhr))
                print(f"    API: {', '.join(urls[:3])}")

            results[screen["id"]] = {
                "name": screen["name"],
                "parent": screen["parent_name"],
                "frameUrl": wf_info.get("frameId", ""),
                "datasets": wf_info.get("datasets", []),
                "grids": wf_info.get("grids", []),
                "buttons": wf_info.get("buttons", []),
                "xhr": xhr[:5]
            }

        # ===== [3] 기존 화면 미활용 데이터셋 확인 =====
        print("\n" + "=" * 70)
        print("[3] 기존 화면 미활용 데이터셋 확인")
        print("=" * 70)

        existing = [
            ("STBJ000_M0", "STBJ030_M0", "단품별 발주"),
            ("STBJ000_M0", "STBJ070_M0", "발주현황조회"),
            ("STMB000_M0", "STMB011_M0", "중분류별 매출"),
            ("STGJ000_M0", "STGJ010_M0", "센터매입"),
        ]

        for parent, sub, name in existing:
            print(f"\n  [{name}] ({sub})")

            top_dom = f"{TOP_PREFIX}{parent}:icontext"
            driver.execute_script(f"""
                var el = document.getElementById('{top_dom}');
                if (el) {{
                    var r = el.getBoundingClientRect();
                    el.dispatchEvent(new MouseEvent('mouseover', {{
                        bubbles:true, clientX:r.left+r.width/2, clientY:r.top+r.height/2
                    }}));
                }}
            """)
            time.sleep(0.5)

            sub_dom = f"{SUB_PREFIX}{parent}.form.{sub}:text"
            clicked = click_by_dom_id(driver, sub_dom)
            if not clicked:
                sub_dom = f"{SUB_PREFIX}{parent}.form.{sub}:icontext"
                clicked = click_by_dom_id(driver, sub_dom)

            time.sleep(3)

            wf_info = explore_workframe(driver)
            if wf_info.get("datasets"):
                for ds in wf_info["datasets"]:
                    cols_str = ", ".join(ds["columns"][:15])
                    extra = "..." if len(ds["columns"]) > 15 else ""
                    print(f"    {ds['name']}: {ds['rows']}행×{ds['cols']}열")
                    if cols_str:
                        print(f"      [{cols_str}{extra}]")

            results[sub + "_existing"] = {
                "name": name, "datasets": wf_info.get("datasets", []),
                "grids": wf_info.get("grids", []),
                "buttons": wf_info.get("buttons", [])
            }

        # 저장
        output = {
            "timestamp": datetime.now().isoformat(),
            "menus": {tm: menu_info.get("subMenus", {}).get(tm, []) for tm in top_menus},
            "unknownScreens": all_screens,
            "screenAnalysis": results
        }
        out_path = project_root / "data" / "bgf_site_exploration.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2, default=str)

        print(f"\n\n결과 저장: {out_path}")
        print("탐색 완료!")

    finally:
        try:
            analyzer.close()
        except:
            pass


if __name__ == "__main__":
    main()
