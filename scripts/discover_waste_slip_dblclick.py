"""
STGJ020_M0 Phase 6: DOM MouseEvent 더블클릭으로 상세 페이지 진입

_getBodyCellElem이 없는 그리드 → DOM querySelector로 body cell 찾기
→ MouseEvent dblclick 디스패치 → 상세 페이지 프레임/데이터셋 탐색
"""

import sys
import io
import json
import time
from pathlib import Path

if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.sales_analyzer import SalesAnalyzer
from src.settings.constants import DEFAULT_STORE_ID
from src.utils.nexacro_helpers import navigate_menu

FRAME_ID = "STGJ020_M0"


def discover_dblclick_detail(store_id: str = DEFAULT_STORE_ID) -> dict:
    analyzer = SalesAnalyzer(store_id=store_id)
    results = {}

    try:
        # 로그인
        print("[0] 로그인...")
        analyzer.setup_driver()
        analyzer.connect()
        if not analyzer.do_login():
            return {}
        analyzer.close_popup()
        time.sleep(2)
        print("[OK] 로그인 성공")

        driver = analyzer.driver

        # 메뉴 이동 + 폐기 필터 + 조회
        print(f"\n[1] 메뉴 이동 + 폐기 필터 조회")
        navigate_menu(driver, "검수전표", "통합 전표 조회", FRAME_ID)
        time.sleep(2)

        driver.execute_script("""
            var fid = arguments[0];
            var app = nexacro.getApplication();
            var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
            var wf = form.div_workForm.form;
            var ds = wf.dsChitDiv;
            for (var r = 0; r < ds.getRowCount(); r++) {
                if (ds.getColumn(r, 'CODE') === '10') {
                    wf.div2.form.divSearch.form.cbChitDiv.set_index(r);
                    wf.dsSearch.setColumn(0, 'strChitDiv', '10');
                    break;
                }
            }
            if (typeof form.fn_commBtn_10 === 'function') form.fn_commBtn_10();
            else form.div_cmmbtn.form.F_10.click();
        """, FRAME_ID)
        time.sleep(3)

        row_count = driver.execute_script("""
            return nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[arguments[0]].form
                .div_workForm.form.dsList.getRowCount();
        """, FRAME_ID)
        print(f"  폐기 전표: {row_count}건")

        # ============================================================
        # 2. DOM에서 그리드 body cell 찾기
        # ============================================================
        print(f"\n{'='*70}")
        print("  [2] DOM에서 그리드 body cell 탐색")
        print(f"{'='*70}")

        dom_cells = driver.execute_script("""
            // STGJ020_M0 프레임의 gdList 관련 DOM 탐색
            var patterns = [
                '[id*="STGJ020"][id*="gdList"] [id*="body"] [id*="cell_0"]',
                '[id*="STGJ020"][id*="gdList"][id*="body"] [id*="cell_0"]',
                '[id*="STGJ020"][id*="gdList"] div[class*="Grid"] [id*="cell"]',
                '[id*="STGJ020"][id*="gdList"] div[id*="cell_0"]',
                '[id*="STGJ020"][id*="gd"] div[id*="cell_0"]',
                '[id*="STGJ020_M0"] [id*="gdList"]',
                '[id*="STGJ020"] [id*="gdList"]',
            ];

            var allResults = {};
            for (var p = 0; p < patterns.length; p++) {
                try {
                    var els = document.querySelectorAll(patterns[p]);
                    if (els.length > 0) {
                        allResults[patterns[p]] = [];
                        for (var i = 0; i < Math.min(els.length, 10); i++) {
                            var el = els[i];
                            var r = el.getBoundingClientRect();
                            allResults[patterns[p]].push({
                                id: el.id,
                                tag: el.tagName,
                                class: el.className ? el.className.substring(0, 50) : '',
                                children: el.children.length,
                                rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}
                            });
                        }
                    }
                } catch(e) {}
            }
            return allResults;
        """)

        for pattern, els in dom_cells.items():
            print(f"\n  Pattern: {pattern}")
            for el in els:
                print(f"    {el['id'][:80]} tag={el['tag']} children={el['children']} rect={el['rect']}")

        results["dom_cells"] = dom_cells

        # ============================================================
        # 3. 넥사크로 그리드의 실제 DOM 요소 찾기 (대안)
        # ============================================================
        print(f"\n{'='*70}")
        print("  [3] 넥사크로 그리드 내부 DOM 구조 탐색")
        print(f"{'='*70}")

        grid_dom = driver.execute_script("""
            var fid = arguments[0];
            try {
                var app = nexacro.getApplication();
                var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                var wf = form.div_workForm.form;
                var grid = wf.div2.form.gdList;

                if (!grid) return {error: 'gdList not found in div2.form'};

                var result = {
                    name: grid.name,
                    type: grid.constructor ? grid.constructor.name : typeof grid,
                    binddataset: grid.binddataset,
                    visible: grid.visible,
                    // 내부 DOM 접근 시도
                    has_element: !!grid._element,
                    has_element_node: false,
                    methods: []
                };

                // grid의 DOM 관련 메서드 확인
                for (var key in grid) {
                    if (typeof grid[key] === 'function' && (
                        key.indexOf('Cell') >= 0 ||
                        key.indexOf('body') >= 0 ||
                        key.indexOf('Body') >= 0 ||
                        key.indexOf('elem') >= 0 ||
                        key.indexOf('Elem') >= 0 ||
                        key.indexOf('Band') >= 0 ||
                        key.indexOf('band') >= 0 ||
                        key.indexOf('rect') >= 0 ||
                        key.indexOf('Rect') >= 0 ||
                        key.indexOf('Row') >= 0 ||
                        key.indexOf('row') >= 0 ||
                        key.indexOf('node') >= 0 ||
                        key.indexOf('Node') >= 0
                    )) {
                        result.methods.push(key);
                    }
                }

                // grid._element 또는 grid._control_element 탐색
                if (grid._element) {
                    result.has_element = true;
                    var el = grid._element;
                    if (el._element_node) {
                        result.has_element_node = true;
                        var node = el._element_node;
                        result.element_id = node.id || '';
                        result.element_tag = node.tagName || '';

                        // 자식 탐색
                        var children = [];
                        for (var i = 0; i < Math.min(node.children.length, 10); i++) {
                            var c = node.children[i];
                            children.push({id: c.id || '', tag: c.tagName, class: (c.className || '').substring(0, 50)});
                        }
                        result.element_children = children;
                    }
                }

                // 대안: grid._control_element
                if (grid._control_element) {
                    var ce = grid._control_element;
                    result.has_control_element = true;
                    if (ce._element_node) {
                        result.control_element_id = ce._element_node.id;
                    }

                    // body band 찾기
                    if (ce._body_band) {
                        result.has_body_band = true;
                        if (ce._body_band._element_node) {
                            result.body_band_id = ce._body_band._element_node.id;
                            var bNode = ce._body_band._element_node;
                            var bChildren = [];
                            for (var i = 0; i < Math.min(bNode.children.length, 5); i++) {
                                var c = bNode.children[i];
                                var cr = c.getBoundingClientRect();
                                bChildren.push({
                                    id: c.id || '',
                                    tag: c.tagName,
                                    rect: {x: Math.round(cr.x), y: Math.round(cr.y), w: Math.round(cr.width), h: Math.round(cr.height)}
                                });
                            }
                            result.body_children = bChildren;
                        }
                    }
                }

                return result;
            } catch(e) {
                return {error: e.message, stack: (e.stack || '').substring(0, 300)};
            }
        """, FRAME_ID)

        results["grid_dom"] = grid_dom
        print(f"  그리드: {json.dumps(grid_dom, indent=2, ensure_ascii=False, default=str)[:2000]}")

        # ============================================================
        # 4. 그리드 DOM body 행에서 더블클릭 좌표 구하기
        # ============================================================
        print(f"\n{'='*70}")
        print("  [4] 그리드 body 행 좌표 + MouseEvent 더블클릭")
        print(f"{'='*70}")

        # 더블클릭 전 프레임 목록
        before_frames = driver.execute_script("""
            var fs = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet;
            var r = []; for (var k in fs) { if (fs[k] && fs[k].form && k.match(/^[A-Z]/)) r.push(k); }
            return r;
        """)

        dblclick_result = driver.execute_script("""
            var fid = arguments[0];
            try {
                var app = nexacro.getApplication();
                var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                var wf = form.div_workForm.form;
                var grid = wf.div2.form.gdList;

                // 행 0 선택
                wf.dsList.set_rowposition(0);

                // 방법 1: grid._control_element._body_band에서 첫 행 찾기
                if (grid._control_element && grid._control_element._body_band) {
                    var bodyBand = grid._control_element._body_band;
                    var bodyNode = bodyBand._element_node;
                    if (bodyNode && bodyNode.children.length > 0) {
                        // 첫 번째 행 (보통 row_0)
                        var firstRow = bodyNode.children[0];
                        if (firstRow) {
                            firstRow.scrollIntoView({block: 'center'});
                            var r = firstRow.getBoundingClientRect();
                            if (r.width > 0 && r.height > 0) {
                                var opts = {bubbles: true, cancelable: true, view: window,
                                           clientX: r.left + r.width / 2, clientY: r.top + r.height / 2};
                                firstRow.dispatchEvent(new MouseEvent('mousedown', opts));
                                firstRow.dispatchEvent(new MouseEvent('mouseup', opts));
                                firstRow.dispatchEvent(new MouseEvent('click', opts));
                                firstRow.dispatchEvent(new MouseEvent('dblclick', opts));
                                return {success: true, method: 'body_band_dblclick', id: firstRow.id,
                                        rect: {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)}};
                            }
                        }
                    }
                }

                // 방법 2: grid._element에서 body 찾기
                if (grid._element && grid._element._element_node) {
                    var gridNode = grid._element._element_node;
                    // body 영역 탐색 (class에 'body' 포함되는 것)
                    var bodyEls = gridNode.querySelectorAll('[id*="body"]');
                    if (bodyEls.length > 0) {
                        var bodyEl = bodyEls[0];
                        // 첫 번째 보이는 행
                        for (var c = 0; c < bodyEl.children.length; c++) {
                            var child = bodyEl.children[c];
                            var cr = child.getBoundingClientRect();
                            if (cr.width > 0 && cr.height > 0) {
                                var opts = {bubbles: true, cancelable: true, view: window,
                                           clientX: cr.left + cr.width / 2, clientY: cr.top + cr.height / 2};
                                child.dispatchEvent(new MouseEvent('mousedown', opts));
                                child.dispatchEvent(new MouseEvent('mouseup', opts));
                                child.dispatchEvent(new MouseEvent('click', opts));
                                child.dispatchEvent(new MouseEvent('dblclick', opts));
                                return {success: true, method: 'element_body_dblclick', id: child.id,
                                        rect: {x: Math.round(cr.x + cr.width/2), y: Math.round(cr.y + cr.height/2)}};
                            }
                        }
                    }
                }

                // 방법 3: DOM 전체에서 gdList body cell 탐색
                var allCells = document.querySelectorAll('[id*="gdList"][id*="body"] div, [id*="gdList"] [id*="body"] div');
                for (var i = 0; i < allCells.length; i++) {
                    var cell = allCells[i];
                    var ccr = cell.getBoundingClientRect();
                    if (ccr.width > 20 && ccr.height > 10 && ccr.y > 100) {
                        var opts = {bubbles: true, cancelable: true, view: window,
                                   clientX: ccr.left + ccr.width / 2, clientY: ccr.top + ccr.height / 2};
                        cell.dispatchEvent(new MouseEvent('mousedown', opts));
                        cell.dispatchEvent(new MouseEvent('mouseup', opts));
                        cell.dispatchEvent(new MouseEvent('click', opts));
                        cell.dispatchEvent(new MouseEvent('dblclick', opts));
                        return {success: true, method: 'dom_cell_dblclick', id: cell.id,
                                rect: {x: Math.round(ccr.x + ccr.width/2), y: Math.round(ccr.y + ccr.height/2)}};
                    }
                }

                return {success: false, reason: 'no clickable element found'};
            } catch(e) {
                return {error: e.message};
            }
        """, FRAME_ID)

        print(f"  더블클릭: {dblclick_result}")
        results["dblclick"] = dblclick_result

        if dblclick_result and dblclick_result.get("success"):
            time.sleep(4)

            # 후 프레임 확인
            after_frames = driver.execute_script("""
                var fs = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet;
                var r = []; for (var k in fs) { if (fs[k] && fs[k].form && k.match(/^[A-Z]/)) r.push(k); }
                return r;
            """)
            print(f"\n  전 프레임: {before_frames}")
            print(f"  후 프레임: {after_frames}")

            new_frames = [f for f in after_frames if f not in before_frames]
            print(f"  새 프레임: {new_frames}")

            tabs = driver.execute_script("""
                var tabs = [];
                var els = document.querySelectorAll('[id*="tab_openList"][id*="tabbuttonitemtext"]');
                for (var i = 0; i < els.length; i++) {
                    var text = (els[i].innerText || '').trim();
                    if (text) tabs.push({idx: i, text: text});
                }
                return tabs;
            """)
            print(f"  탭: {tabs}")

            # 새 프레임이 있으면 데이터셋 탐색
            target_frames = new_frames if new_frames else [f for f in after_frames if f != "WorkFrame"]

            for fid in target_frames:
                ds_data = driver.execute_script("""
                    var fid = arguments[0];
                    try {
                        var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                        var datasets = [];

                        function findDs(f, prefix, depth) {
                            if (depth > 5) return;
                            for (var key in f) {
                                try {
                                    var obj = f[key];
                                    if (!obj || typeof obj !== 'object') continue;
                                    if (obj.getRowCount && obj.getColumn && obj.getColCount) {
                                        var rc = obj.getRowCount();
                                        var cc = obj.getColCount();
                                        var cols = [];
                                        for (var c = 0; c < cc; c++) cols.push(obj.getColID(c));

                                        var info = {name: key, path: prefix+'.'+key, rowCount: rc, columns: cols};
                                        if (rc > 0) {
                                            var samples = [];
                                            for (var r = 0; r < Math.min(rc, 5); r++) {
                                                var row = {};
                                                for (var ci = 0; ci < Math.min(cc, 30); ci++) {
                                                    var val = obj.getColumn(r, cols[ci]);
                                                    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                                                    row[cols[ci]] = val;
                                                }
                                                samples.push(row);
                                            }
                                            info.samples = samples;
                                        }
                                        datasets.push(info);
                                    }
                                    if (obj.form && (key.startsWith('div') || key.startsWith('tab'))) {
                                        findDs(obj.form, prefix+'.'+key+'.form', depth+1);
                                    }
                                } catch(e) {}
                            }
                        }
                        findDs(form, 'form', 0);
                        return datasets;
                    } catch(e) { return [{error: e.message}]; }
                """, fid)

                if ds_data:
                    print(f"\n  === 프레임: {fid} ===")
                    for ds in ds_data:
                        if ds.get("rowCount", 0) > 0:
                            cols = ds.get("columns", [])
                            item_cols = [c for c in cols if 'ITEM' in c or 'QTY' in c]
                            marker = " *** ITEM" if item_cols else ""
                            print(f"\n  [{ds['name']}] rows={ds['rowCount']}{marker}")
                            print(f"    cols: {cols}")
                            if ds.get("samples"):
                                for i, s in enumerate(ds["samples"][:3]):
                                    ss = json.dumps(s, ensure_ascii=False, default=str)
                                    if len(ss) > 500: ss = ss[:500] + "..."
                                    print(f"    row[{i}]: {ss}")

                    results[f"ds_{fid}"] = ds_data
        else:
            # ActionChains 더블클릭 시도 (좌표 직접 구하기)
            print("\n  [대안] ActionChains 더블클릭 시도")

            # 그리드 전체 영역 좌표 구하기
            grid_rect = driver.execute_script("""
                var fid = arguments[0];
                try {
                    var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                    var grid = form.div_workForm.form.div2.form.gdList;
                    if (grid._element && grid._element._element_node) {
                        var r = grid._element._element_node.getBoundingClientRect();
                        return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)};
                    }
                    return {error: 'no element node'};
                } catch(e) { return {error: e.message}; }
            """, FRAME_ID)
            print(f"  그리드 영역: {grid_rect}")

            if grid_rect and not grid_rect.get("error"):
                from selenium.webdriver.common.action_chains import ActionChains

                # 그리드 body 첫 행은 보통 헤더 아래, 약 y + 30 위치
                click_x = grid_rect["x"] + grid_rect["w"] // 3
                click_y = grid_rect["y"] + 50  # 헤더 + 첫 번째 행

                print(f"  클릭 좌표: ({click_x}, {click_y})")

                actions = ActionChains(driver)
                actions.move_by_offset(click_x, click_y).double_click().perform()
                actions = ActionChains(driver)
                actions.move_by_offset(-click_x, -click_y).perform()

                print("  더블클릭 완료, 4초 대기...")
                time.sleep(4)

                # 후 프레임 확인
                after_frames = driver.execute_script("""
                    var fs = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet;
                    var r = []; for (var k in fs) { if (fs[k] && fs[k].form && k.match(/^[A-Z]/)) r.push(k); }
                    return r;
                """)
                print(f"  후 프레임: {after_frames}")
                new_frames = [f for f in after_frames if f not in before_frames]
                print(f"  새 프레임: {new_frames}")

                tabs = driver.execute_script("""
                    var tabs = [];
                    var els = document.querySelectorAll('[id*="tab_openList"][id*="tabbuttonitemtext"]');
                    for (var i = 0; i < els.length; i++) {
                        var text = (els[i].innerText || '').trim();
                        if (text) tabs.push({idx: i, text: text});
                    }
                    return tabs;
                """)
                print(f"  탭: {tabs}")

                # 데이터셋 탐색
                for fid in (new_frames if new_frames else [f for f in after_frames if f != "WorkFrame"]):
                    ds_data = driver.execute_script("""
                        var fid = arguments[0];
                        try {
                            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                            var datasets = [];
                            function findDs(f, prefix, depth) {
                                if (depth > 5) return;
                                for (var key in f) {
                                    try {
                                        var obj = f[key];
                                        if (!obj || typeof obj !== 'object') continue;
                                        if (obj.getRowCount && obj.getColumn && obj.getColCount) {
                                            var rc = obj.getRowCount();
                                            var cc = obj.getColCount();
                                            var cols = [];
                                            for (var c = 0; c < cc; c++) cols.push(obj.getColID(c));
                                            var info = {name: key, rowCount: rc, columns: cols};
                                            if (rc > 0) {
                                                var samples = [];
                                                for (var r = 0; r < Math.min(rc, 5); r++) {
                                                    var row = {};
                                                    for (var ci = 0; ci < Math.min(cc, 30); ci++) {
                                                        var val = obj.getColumn(r, cols[ci]);
                                                        if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                                                        row[cols[ci]] = val;
                                                    }
                                                    samples.push(row);
                                                }
                                                info.samples = samples;
                                            }
                                            datasets.push(info);
                                        }
                                        if (obj.form && (key.startsWith('div') || key.startsWith('tab'))) {
                                            findDs(obj.form, prefix+'.'+key+'.form', depth+1);
                                        }
                                    } catch(e) {}
                                }
                            }
                            findDs(form, 'form', 0);
                            return datasets;
                        } catch(e) { return [{error: e.message}]; }
                    """, fid)

                    if ds_data:
                        print(f"\n  === 프레임: {fid} ===")
                        for ds in ds_data:
                            if ds.get("rowCount", 0) > 0:
                                print(f"\n  [{ds['name']}] rows={ds['rowCount']}")
                                print(f"    cols: {ds.get('columns', [])}")
                                if ds.get("samples"):
                                    for i, s in enumerate(ds["samples"][:3]):
                                        ss = json.dumps(s, ensure_ascii=False, default=str)
                                        if len(ss) > 500: ss = ss[:500] + "..."
                                        print(f"    row[{i}]: {ss}")
                        results[f"ds_{fid}"] = ds_data

        # JSON 저장
        output_path = PROJECT_ROOT / "data" / "waste_slip_dblclick_detail.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n  결과 저장: {output_path}")

        return results

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        return results

    finally:
        try:
            if analyzer and analyzer.driver:
                analyzer.close()
        except Exception:
            pass


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--store-id", default=DEFAULT_STORE_ID)
    args = parser.parse_args()
    discover_dblclick_detail(store_id=args.store_id)
