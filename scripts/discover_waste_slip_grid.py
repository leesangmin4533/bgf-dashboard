"""
STGJ020_M0 Phase 5: 그리드 정확한 경로 찾기 + ActionChains 더블클릭 → 상세 화면

Phase 4에서 grid not found 발생. 그리드 경로를 재귀적으로 찾아야 함.
이후 ActionChains로 실제 더블클릭하여 상세 페이지 진입.
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


def discover_grid_and_detail(store_id: str = DEFAULT_STORE_ID) -> dict:
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
        # 2. 그리드 컴포넌트 재귀 탐색
        # ============================================================
        print(f"\n{'='*70}")
        print("  [2] 그리드 컴포넌트 재귀 탐색")
        print(f"{'='*70}")

        grid_info = driver.execute_script("""
            var fid = arguments[0];
            var app = nexacro.getApplication();
            var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;

            var grids = [];

            function findGrids(f, prefix, depth) {
                if (depth > 8) return;
                for (var key in f) {
                    try {
                        var obj = f[key];
                        if (!obj || typeof obj !== 'object') continue;

                        // Grid 체크: _getBodyCellElem 또는 binddataset 속성 가진 그리드
                        if (obj._getBodyCellElem ||
                            (obj.binddataset && obj.getCellCount) ||
                            (obj.constructor && obj.constructor.name === 'Grid')) {
                            grids.push({
                                name: key,
                                path: prefix + '.' + key,
                                type: obj.constructor ? obj.constructor.name : typeof obj,
                                binddataset: obj.binddataset || 'N/A',
                                has_getBodyCellElem: !!obj._getBodyCellElem,
                                has_oncelldblclick: !!obj.oncelldblclick,
                                visible: obj.visible !== false
                            });
                        }

                        // 하위 div/tab 탐색
                        if (obj.form && (key.startsWith('div') || key.startsWith('tab') || key === 'div_workForm' || key === 'div_cmmbtn')) {
                            findGrids(obj.form, prefix + '.' + key + '.form', depth + 1);
                        }
                    } catch(e) {}
                }
            }

            findGrids(form, 'form', 0);
            return grids;
        """, FRAME_ID)

        results["grids"] = grid_info
        for g in grid_info:
            print(f"  Grid: {g['name']} path={g['path']}")
            print(f"    binddataset={g.get('binddataset')} getBodyCell={g.get('has_getBodyCellElem')} dblclick={g.get('has_oncelldblclick')}")

        if not grid_info:
            print("  [WARN] 그리드 없음! DOM 직접 탐색으로 전환")

            # DOM에서 직접 그리드 엘리먼트 찾기
            dom_grids = driver.execute_script("""
                var els = document.querySelectorAll('[id*="gdList"], [id*="grid"], [id*="Grid"]');
                var result = [];
                for (var i = 0; i < els.length; i++) {
                    result.push({
                        id: els[i].id,
                        tagName: els[i].tagName,
                        className: els[i].className
                    });
                }
                return result;
            """)
            print(f"  DOM 그리드: {dom_grids}")
            results["dom_grids"] = dom_grids

        # ============================================================
        # 3. 그리드 셀 좌표 구하기 (정확한 경로 사용)
        # ============================================================
        print(f"\n{'='*70}")
        print("  [3] 그리드 셀 좌표 구하기")
        print(f"{'='*70}")

        # 그리드 경로를 사용하여 좌표 구하기
        grid_path = grid_info[0]["path"] if grid_info else None

        cell_rect = driver.execute_script("""
            var fid = arguments[0];
            var gridPath = arguments[1];
            try {
                var app = nexacro.getApplication();
                var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;

                // 경로에서 그리드 객체 접근
                var grid = null;
                if (gridPath) {
                    var parts = gridPath.split('.');
                    var obj = form;
                    for (var i = 1; i < parts.length; i++) {
                        obj = obj[parts[i]];
                        if (!obj) break;
                    }
                    grid = obj;
                }

                if (!grid) {
                    // 대체: wf에서 직접 탐색
                    var wf = form.div_workForm?.form;
                    if (wf) {
                        function findGrid(f) {
                            for (var key in f) {
                                try {
                                    if (f[key] && f[key]._getBodyCellElem) return f[key];
                                    if (f[key] && f[key].form) {
                                        var found = findGrid(f[key].form);
                                        if (found) return found;
                                    }
                                } catch(e) {}
                            }
                            return null;
                        }
                        grid = findGrid(wf);
                    }
                }

                if (!grid || !grid._getBodyCellElem) {
                    return {error: 'grid not found even with path: ' + gridPath};
                }

                // dsList 행 위치 설정
                var wf = form.div_workForm.form;
                wf.dsList.set_rowposition(0);

                // 여러 셀 시도
                for (var col = 0; col < 5; col++) {
                    try {
                        var cellElem = grid._getBodyCellElem(0, col);
                        if (cellElem && cellElem._element_node) {
                            var rect = cellElem._element_node.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                return {
                                    x: Math.round(rect.x + rect.width / 2),
                                    y: Math.round(rect.y + rect.height / 2),
                                    width: Math.round(rect.width),
                                    height: Math.round(rect.height),
                                    col: col,
                                    gridName: grid.name || 'unknown'
                                };
                            }
                        }
                    } catch(e) {}
                }

                return {error: 'no valid cell found'};
            } catch(e) {
                return {error: e.message, stack: e.stack ? e.stack.substring(0, 300) : ''};
            }
        """, FRAME_ID, grid_path)

        print(f"  셀 좌표: {cell_rect}")
        results["cell_rect"] = cell_rect

        if cell_rect and not cell_rect.get("error"):
            # ============================================================
            # 4. ActionChains 더블클릭
            # ============================================================
            print(f"\n{'='*70}")
            print("  [4] ActionChains 실제 더블클릭")
            print(f"{'='*70}")

            from selenium.webdriver.common.action_chains import ActionChains

            # 더블클릭 전 프레임 목록
            before_frames = driver.execute_script("""
                var fs = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet;
                var r = []; for (var k in fs) { if (fs[k] && fs[k].form && k.match(/^[A-Z]/)) r.push(k); }
                return r;
            """)
            print(f"  전 프레임: {before_frames}")

            actions = ActionChains(driver)
            actions.move_by_offset(cell_rect["x"], cell_rect["y"]).double_click().perform()

            # 원점 복귀
            actions = ActionChains(driver)
            actions.move_by_offset(-cell_rect["x"], -cell_rect["y"]).perform()

            print("  더블클릭 완료, 4초 대기...")
            time.sleep(4)

            # 더블클릭 후 프레임 목록
            after_frames = driver.execute_script("""
                var fs = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet;
                var r = []; for (var k in fs) { if (fs[k] && fs[k].form && k.match(/^[A-Z]/)) r.push(k); }
                return r;
            """)
            print(f"  후 프레임: {after_frames}")
            results["after_frames"] = after_frames

            new_frames = [f for f in after_frames if f not in before_frames]
            print(f"  새 프레임: {new_frames}")

            # 탭 확인
            tabs = driver.execute_script("""
                var tabs = [];
                var els = document.querySelectorAll('[id*="tab_openList"][id*="tabbuttonitemtext"]');
                for (var i = 0; i < els.length; i++) {
                    var text = (els[i].innerText || '').trim();
                    if (text) tabs.push({id: els[i].id, text: text});
                }
                return tabs;
            """)
            print(f"  탭: {[t['text'] for t in tabs]}")

            # ============================================================
            # 5. 새 프레임 or 현재 프레임 상세 데이터셋 탐색
            # ============================================================
            print(f"\n{'='*70}")
            print("  [5] 상세 데이터셋 탐색")
            print(f"{'='*70}")

            # 각 프레임의 데이터셋 탐색
            for frame_id in after_frames:
                if frame_id == "WorkFrame":
                    continue

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

                                        var info = {name: key, path: prefix + '.' + key, rowCount: rc, columns: cols};

                                        // ITEM 관련 컬럼 포함 여부 확인
                                        var hasItemCol = cols.some(function(c) {
                                            return c.indexOf('ITEM') >= 0 || c.indexOf('QTY') >= 0 || c.indexOf('AMT') >= 0;
                                        });
                                        info.hasItemColumns = hasItemCol;

                                        if (rc > 0) {
                                            var samples = [];
                                            var maxRows = Math.min(rc, 5);
                                            for (var r = 0; r < maxRows; r++) {
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
                                        findDs(obj.form, prefix + '.' + key + '.form', depth + 1);
                                    }
                                } catch(e) {}
                            }
                        }

                        findDs(form, 'form', 0);
                        return datasets;
                    } catch(e) { return [{error: e.message}]; }
                """, frame_id)

                if ds_data:
                    has_items = [d for d in ds_data if d.get("rowCount", 0) > 0 or d.get("hasItemColumns")]
                    if has_items:
                        print(f"\n  === 프레임: {frame_id} ===")
                        for ds in has_items:
                            marker = " *** ITEM DATA" if ds.get("hasItemColumns") and ds.get("rowCount", 0) > 0 else ""
                            print(f"\n  [{ds['name']}] rows={ds.get('rowCount', 0)}{marker}")
                            print(f"    cols: {ds.get('columns', [])}")
                            if ds.get("samples"):
                                for i, s in enumerate(ds["samples"][:3]):
                                    sample_str = json.dumps(s, ensure_ascii=False, default=str)
                                    if len(sample_str) > 500:
                                        sample_str = sample_str[:500] + "..."
                                    print(f"    row[{i}]: {sample_str}")

                        results[f"detail_{frame_id}"] = has_items
        else:
            print(f"\n  [ERROR] 셀 좌표 없음: {cell_rect}")

            # 대안: DOM에서 그리드 행 직접 찾기
            print("\n  [대안] DOM에서 그리드 행 찾기...")

            dom_row = driver.execute_script("""
                // STGJ020_M0 관련 DOM 요소 탐색
                var els = document.querySelectorAll('[id*="STGJ020"]');
                var gridEls = [];
                for (var i = 0; i < Math.min(els.length, 50); i++) {
                    var el = els[i];
                    if (el.id.indexOf('grid') >= 0 || el.id.indexOf('Grid') >= 0 ||
                        el.id.indexOf('gdList') >= 0 || el.id.indexOf('body') >= 0) {
                        gridEls.push({
                            id: el.id,
                            tag: el.tagName,
                            rect: el.getBoundingClientRect ? {
                                x: Math.round(el.getBoundingClientRect().x),
                                y: Math.round(el.getBoundingClientRect().y),
                                w: Math.round(el.getBoundingClientRect().width),
                                h: Math.round(el.getBoundingClientRect().height)
                            } : null
                        });
                    }
                }
                return gridEls;
            """)

            for el in (dom_row or []):
                print(f"  {el}")
            results["dom_grid_rows"] = dom_row

        # JSON 저장
        output_path = PROJECT_ROOT / "data" / "waste_slip_grid_detail.json"
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
    discover_grid_and_detail(store_id=args.store_id)
