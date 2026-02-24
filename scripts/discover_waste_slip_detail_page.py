"""
STGJ020_M0 Phase 4: 폐기 전표 상세 페이지 구조 탐색

Phase 3 결과:
  - 전표구분 CODE="10" (폐기)
  - dsList 100건 수집 완료
  - 행 더블클릭 시 새 탭 "통합 전표 조회" 추가 열림
  - dsGs에 전표 정보 설정됨 (gvVar04=04, gvVar07=전표번호)
  - 같은 프레임 ID(STGJ020_M0) 재사용이 아닌 별도 프레임 열릴 가능성

이번 탐색:
  1. 로그인 → 메뉴 이동 → 폐기 필터 조회
  2. 첫 번째 행 더블클릭 (ActionChains 실제 클릭)
  3. 열린 모든 프레임의 dataset 전수 조사
  4. 상세 데이터(ITEM_CD, ITEM_NM, 수량 등) 매핑
"""

import sys
import io
import json
import time
import argparse
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


def discover_detail_page(store_id: str = DEFAULT_STORE_ID) -> dict:
    analyzer = SalesAnalyzer(store_id=store_id)
    results = {}

    try:
        # 로그인
        print("[0] 로그인...")
        analyzer.setup_driver()
        analyzer.connect()
        if not analyzer.do_login():
            print("[ERROR] 로그인 실패")
            return {}
        analyzer.close_popup()
        time.sleep(2)
        print("[OK] 로그인 성공")

        driver = analyzer.driver

        # 메뉴 이동
        print(f"\n[1] 통합 전표 조회 이동 ({FRAME_ID})")
        success = navigate_menu(driver, "검수전표", "통합 전표 조회", FRAME_ID)
        print(f"  이동: {success}")
        time.sleep(2)

        # 폐기(10) 필터 설정 + 조회
        print(f"\n[2] 전표구분 = 폐기(10) 설정 + 조회")

        filter_result = driver.execute_script("""
            var fid = arguments[0];
            try {
                var app = nexacro.getApplication();
                var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                var wf = form.div_workForm.form;

                var ds = wf.dsChitDiv;
                var targetIdx = -1;
                for (var r = 0; r < ds.getRowCount(); r++) {
                    if (ds.getColumn(r, 'CODE') === '10') {
                        targetIdx = r;
                        break;
                    }
                }
                if (targetIdx < 0) return {error: 'CODE 10 not found'};

                var cb = wf.div2.form.divSearch.form.cbChitDiv;
                cb.set_index(targetIdx);

                var dsSearch = wf.dsSearch;
                if (dsSearch) dsSearch.setColumn(0, 'strChitDiv', '10');

                return {success: true, index: targetIdx, text: cb.text};
            } catch(e) { return {error: e.message}; }
        """, FRAME_ID)
        print(f"  필터: {filter_result}")

        # 조회 실행
        driver.execute_script("""
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[arguments[0]].form;
            if (typeof form.fn_commBtn_10 === 'function') form.fn_commBtn_10();
            else if (form.div_cmmbtn && form.div_cmmbtn.form.F_10) form.div_cmmbtn.form.F_10.click();
        """, FRAME_ID)
        time.sleep(3)

        # 결과 확인
        row_count = driver.execute_script("""
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[arguments[0]].form;
            return form.div_workForm.form.dsList.getRowCount();
        """, FRAME_ID)
        print(f"  폐기 전표: {row_count}건")

        if row_count == 0:
            print("[ERROR] 폐기 전표 0건")
            return results

        # ============================================================
        # 3. fn_moveDetailPage 함수 소스 코드 확인
        # ============================================================
        print(f"\n{'='*70}")
        print("  [3] fn_moveDetailPage 함수 분석")
        print(f"{'='*70}")

        fn_source = driver.execute_script("""
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[arguments[0]].form;
            if (typeof form.fn_moveDetailPage === 'function') {
                return form.fn_moveDetailPage.toString().substring(0, 2000);
            }
            // 그리드 이벤트 핸들러 확인
            var wf = form.div_workForm.form;
            var results = {};

            // div2 내 gdList 확인
            var divForm = wf.div2?.form;
            if (divForm) {
                var gdList = divForm.gdList;
                if (!gdList) {
                    // divSearch 아래일 수도 있음
                    for (var key in divForm) {
                        if (key.startsWith('gd') || key.startsWith('gr')) {
                            results['found_grid_' + key] = true;
                        }
                    }
                }
                if (gdList) {
                    results.gdList_exists = true;
                    if (gdList.oncelldblclick) {
                        try {
                            results.dblclick_handler = gdList.oncelldblclick.toString().substring(0, 500);
                        } catch(e) { results.dblclick_handler_error = e.message; }
                    }
                }
            }

            // form 레벨 이벤트 확인
            for (var key in form) {
                if (typeof form[key] === 'function' && (key.indexOf('move') >= 0 || key.indexOf('Move') >= 0 || key.indexOf('detail') >= 0 || key.indexOf('Detail') >= 0 || key.indexOf('dblclick') >= 0)) {
                    try {
                        results['fn_' + key] = form[key].toString().substring(0, 500);
                    } catch(e) {}
                }
            }
            return results;
        """, FRAME_ID)

        results["fn_analysis"] = fn_source
        if isinstance(fn_source, str):
            print(f"  fn_moveDetailPage 소스:\n{fn_source[:1000]}")
        elif isinstance(fn_source, dict):
            for k, v in fn_source.items():
                print(f"  {k}: {str(v)[:300]}")

        # ============================================================
        # 4. 현재 프레임 상태 캡처 후 행 선택 & 더블클릭
        # ============================================================
        print(f"\n{'='*70}")
        print("  [4] 더블클릭 전 프레임 목록")
        print(f"{'='*70}")

        before_frames = driver.execute_script("""
            var app = nexacro.getApplication();
            var fs = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
            var frames = [];
            for (var key in fs) {
                if (fs[key] && fs[key].form && key.match(/^[A-Z]/)) {
                    frames.push(key);
                }
            }
            return frames;
        """)
        print(f"  프레임: {before_frames}")

        # 행 0 선택 후 더블클릭 (ActionChains 사용)
        print(f"\n[4-2] 행 0 더블클릭 (ActionChains)")

        # 그리드 셀 좌표 구하기
        cell_rect = driver.execute_script("""
            var fid = arguments[0];
            try {
                var app = nexacro.getApplication();
                var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                var wf = form.div_workForm.form;

                // gdList 찾기
                var grid = null;
                var searchIn = [wf, wf.div2?.form];
                for (var i = 0; i < searchIn.length; i++) {
                    var f = searchIn[i];
                    if (!f) continue;
                    for (var key in f) {
                        if ((key.startsWith('gd') || key.startsWith('gr')) && f[key] && f[key]._getBodyCellElem) {
                            grid = f[key];
                            break;
                        }
                    }
                    if (grid) break;
                }

                if (!grid) return {error: 'grid not found'};

                // 행 0 위치로 이동
                wf.dsList.set_rowposition(0);

                // 첫 번째 행의 좌표 구하기
                var cellElem = grid._getBodyCellElem(0, 1);
                if (!cellElem || !cellElem._element_node) return {error: 'cell element not found'};

                var rect = cellElem._element_node.getBoundingClientRect();
                return {
                    x: Math.round(rect.x + rect.width / 2),
                    y: Math.round(rect.y + rect.height / 2),
                    width: rect.width,
                    height: rect.height,
                    gridName: grid.name || 'unknown'
                };
            } catch(e) {
                return {error: e.message};
            }
        """, FRAME_ID)

        print(f"  셀 좌표: {cell_rect}")

        if cell_rect and not cell_rect.get("error"):
            from selenium.webdriver.common.action_chains import ActionChains

            actions = ActionChains(driver)
            actions.move_by_offset(cell_rect["x"], cell_rect["y"]).double_click().perform()

            # 원점 복귀
            actions = ActionChains(driver)
            actions.move_by_offset(-cell_rect["x"], -cell_rect["y"]).perform()

            print("  더블클릭 완료")
            time.sleep(4)  # 상세 페이지 로딩 대기
        else:
            print(f"  좌표 취득 실패, JS 이벤트로 대체")
            # JS로 그리드 더블클릭 이벤트 발생
            driver.execute_script("""
                var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[arguments[0]].form;
                var wf = form.div_workForm.form;
                wf.dsList.set_rowposition(0);

                // 그리드에 oncelldblclick 이벤트 fire
                var grid = wf.div2?.form?.gdList || wf.gdList;
                if (grid && grid.oncelldblclick) {
                    grid.oncelldblclick._fireEvent(grid, {cell: 0, col: 0, row: 0, clickitem: 'body'});
                }
            """, FRAME_ID)
            time.sleep(4)

        # ============================================================
        # 5. 더블클릭 후 프레임 변화 탐색
        # ============================================================
        print(f"\n{'='*70}")
        print("  [5] 더블클릭 후 프레임/탭 상태")
        print(f"{'='*70}")

        after_frames = driver.execute_script("""
            var app = nexacro.getApplication();
            var fs = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
            var frames = [];
            for (var key in fs) {
                if (fs[key] && fs[key].form && key.match(/^[A-Z]/)) {
                    frames.push(key);
                }
            }
            return frames;
        """)
        print(f"  프레임: {after_frames}")
        results["after_frames"] = after_frames

        # 탭 목록
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
        results["tabs"] = tabs

        # 새 프레임 찾기
        new_frames = [f for f in after_frames if f not in before_frames]
        print(f"  새 프레임: {new_frames}")

        # 모든 프레임의 데이터셋 탐색
        print(f"\n{'='*70}")
        print("  [6] 모든 프레임 데이터셋 전수 조사")
        print(f"{'='*70}")

        for frame_id in after_frames:
            ds_info = driver.execute_script("""
                var fid = arguments[0];
                try {
                    var app = nexacro.getApplication();
                    var frame = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid];
                    if (!frame || !frame.form) return null;
                    var form = frame.form;

                    var datasets = [];

                    function findDs(f, prefix, depth) {
                        if (depth > 5) return;
                        for (var key in f) {
                            try {
                                var obj = f[key];
                                if (!obj) continue;
                                if (obj.getRowCount && obj.getColumn && obj.getColCount) {
                                    var rc = obj.getRowCount();
                                    var cc = obj.getColCount();
                                    var cols = [];
                                    for (var c = 0; c < cc; c++) cols.push(obj.getColID(c));

                                    var info = {
                                        name: key,
                                        path: prefix + '.' + key,
                                        rowCount: rc,
                                        colCount: cc,
                                        columns: cols
                                    };

                                    if (rc > 0 && cc > 0) {
                                        var samples = [];
                                        for (var r = 0; r < Math.min(rc, 3); r++) {
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

                                if (obj.form && (key.startsWith('div') || key === 'div_workForm' || key === 'div_cmmbtn')) {
                                    findDs(obj.form, prefix + '.' + key + '.form', depth + 1);
                                }
                            } catch(e) {}
                        }
                    }

                    findDs(form, 'form', 0);
                    return {frame_id: fid, datasets: datasets};
                } catch(e) { return {error: e.message}; }
            """, frame_id)

            if ds_info and ds_info.get("datasets"):
                results[f"frame_{frame_id}"] = ds_info
                has_data = [d for d in ds_info["datasets"] if d.get("rowCount", 0) > 0]
                if has_data:
                    print(f"\n  === 프레임: {frame_id} ===")
                    for ds in has_data:
                        print(f"\n  [{ds['name']}] rows={ds['rowCount']} cols={ds['colCount']}")
                        print(f"    columns: {ds.get('columns', [])}")
                        if ds.get("samples"):
                            for i, s in enumerate(ds["samples"][:2]):
                                # 출력 길이 제한
                                sample_str = json.dumps(s, ensure_ascii=False, default=str)
                                if len(sample_str) > 300:
                                    sample_str = sample_str[:300] + "..."
                                print(f"    row[{i}]: {sample_str}")

        # ============================================================
        # 7. 탭 리스트에서 활성 탭 확인 + 전환 시도
        # ============================================================
        print(f"\n{'='*70}")
        print("  [7] 탭 전환 시도 (상세 탭)")
        print(f"{'='*70}")

        # 두 번째 탭이 있으면 클릭
        if len(tabs) > 1:
            tab_click = driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    var topFrame = app.mainframe.HFrameSet00.TopFrame;
                    var tabCtrl = topFrame.form.tab_openList;
                    if (!tabCtrl) return {error: 'tab_openList not found'};

                    var tabCount = tabCtrl.tabcount || tabCtrl.getTabCount();
                    var tabInfos = [];
                    for (var i = 0; i < tabCount; i++) {
                        var btn = tabCtrl.tabbuttons[i];
                        tabInfos.push({
                            idx: i,
                            text: btn ? btn.text : 'N/A'
                        });
                    }

                    // 마지막 탭 (상세) 선택
                    if (tabCount > 1) {
                        tabCtrl.set_tabindex(tabCount - 1);
                        return {tabs: tabInfos, selected: tabCount - 1};
                    }
                    return {tabs: tabInfos};
                } catch(e) { return {error: e.message}; }
            """)
            print(f"  탭 전환: {tab_click}")
            time.sleep(2)

            # 전환 후 프레임 재확인
            active_frames = driver.execute_script("""
                var app = nexacro.getApplication();
                var fs = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
                var frames = [];
                for (var key in fs) {
                    if (fs[key] && fs[key].form && key.match(/^[A-Z]/)) {
                        var form = fs[key].form;
                        var visible = fs[key].visible !== false;
                        frames.push({id: key, visible: visible});
                    }
                }
                return frames;
            """)
            print(f"  프레임 상태: {active_frames}")

            # 활성 프레임의 데이터셋 중 ITEM 관련 탐색
            for frame_info in active_frames:
                fid = frame_info.get("id", "")
                if fid == "WorkFrame":
                    continue

                item_ds = driver.execute_script("""
                    var fid = arguments[0];
                    try {
                        var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                        var wf = form.div_workForm ? form.div_workForm.form : form;

                        var found = [];
                        function search(f, prefix, depth) {
                            if (depth > 4) return;
                            for (var key in f) {
                                try {
                                    var obj = f[key];
                                    if (!obj) continue;

                                    if (obj.getRowCount && obj.getColumn && obj.getColCount) {
                                        var rc = obj.getRowCount();
                                        var cc = obj.getColCount();
                                        if (rc > 0 || key.toLowerCase().indexOf('detail') >= 0
                                            || key.toLowerCase().indexOf('item') >= 0
                                            || key.toLowerCase().indexOf('sub') >= 0) {
                                            var cols = [];
                                            for (var c = 0; c < cc; c++) cols.push(obj.getColID(c));

                                            var info = {name: key, path: prefix + '.' + key, rowCount: rc, columns: cols};

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
                                            found.push(info);
                                        }
                                    }

                                    if (obj.form && (key.startsWith('div') || key === 'tab')) {
                                        search(obj.form, prefix + '.' + key + '.form', depth + 1);
                                    }
                                } catch(e) {}
                            }
                        }

                        search(wf, 'wf', 0);
                        search(form, 'form', 0);
                        return {frame_id: fid, datasets: found};
                    } catch(e) { return {error: e.message}; }
                """, fid)

                if item_ds and item_ds.get("datasets"):
                    for ds in item_ds["datasets"]:
                        if ds.get("rowCount", 0) > 0 or "item" in ds["name"].lower() or "detail" in ds["name"].lower():
                            print(f"\n  *** [{fid}] {ds['name']} rows={ds['rowCount']}")
                            print(f"      columns: {ds.get('columns', [])}")
                            if ds.get("samples"):
                                for i, s in enumerate(ds["samples"][:3]):
                                    sample_str = json.dumps(s, ensure_ascii=False, default=str)
                                    if len(sample_str) > 500:
                                        sample_str = sample_str[:500] + "..."
                                    print(f"      row[{i}]: {sample_str}")
                    results[f"detail_frame_{fid}"] = item_ds

        # JSON 저장
        output_path = PROJECT_ROOT / "data" / "waste_slip_detail_page.json"
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--store-id", default=DEFAULT_STORE_ID)
    args = parser.parse_args()
    discover_detail_page(store_id=args.store_id)
