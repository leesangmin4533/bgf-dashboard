"""
STGJ020_M0 Phase 7: ActionChains 정확한 body cell 더블클릭

Phase 6 결과:
  - 그리드 전체: rect {'h': 520, 'w': 992, 'x': 16, 'y': 204}
  - 헤더 행: y=205 (h=26)
  - 데이터 행 0: y=231 (h=31)
  - cell_0_1 (전표구분): x=64, y=233, w=68, h=27

이번: ActionChains 더블클릭을 정확한 데이터 행 좌표에 실행
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

from selenium.webdriver.common.action_chains import ActionChains
from src.sales_analyzer import SalesAnalyzer
from src.settings.constants import DEFAULT_STORE_ID
from src.utils.nexacro_helpers import navigate_menu

FRAME_ID = "STGJ020_M0"


def discover_final(store_id: str = DEFAULT_STORE_ID) -> dict:
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

        if row_count == 0:
            print("[ERROR] 폐기 전표 없음")
            return results

        # ============================================================
        # 2. 데이터 행의 정확한 좌표 구하기
        # ============================================================
        print(f"\n{'='*70}")
        print("  [2] 데이터 행 정확한 좌표 탐색")
        print(f"{'='*70}")

        # body cell_0 (첫 번째 데이터 행)의 DOM 좌표
        cell_rect = driver.execute_script("""
            // body cell의 정확한 좌표: [id*="gdList"][id*="body"] [id*="cell_0"]에서
            // 첫 번째 parent가 있는 cell (children=1인 것이 cell 컨테이너)
            var cells = document.querySelectorAll('[id*="STGJ020"][id*="gdList"][id*="body"] div[id*="cell_0"]');
            var bestCell = null;
            var bestRect = null;

            for (var i = 0; i < cells.length; i++) {
                var c = cells[i];
                var r = c.getBoundingClientRect();
                // children이 1개이고 높이가 25~35인 셀 = 실제 데이터 셀
                if (r.width > 50 && r.height > 20 && r.height < 40 && c.children.length <= 1) {
                    // 전표구분 이나 전표일자 열이 넓이 80 이상
                    if (!bestCell || r.width > bestRect.width) {
                        bestCell = c;
                        bestRect = r;
                    }
                }
            }

            if (bestCell) {
                return {
                    id: bestCell.id.substring(bestCell.id.length - 30),
                    x: Math.round(bestRect.x + bestRect.width / 2),
                    y: Math.round(bestRect.y + bestRect.height / 2),
                    w: Math.round(bestRect.width),
                    h: Math.round(bestRect.height),
                    text: bestCell.innerText || ''
                };
            }

            // 대안: body 영역의 첫 행 전체
            var bodyRows = document.querySelectorAll('[id*="STGJ020"][id*="gdList"][id*="body"][id*="row_0"]');
            if (bodyRows.length > 0) {
                var r = bodyRows[0].getBoundingClientRect();
                return {id: 'row_0', x: Math.round(r.x + r.width/3), y: Math.round(r.y + r.height/2),
                        w: Math.round(r.width), h: Math.round(r.height)};
            }

            return {error: 'no suitable cell found'};
        """)

        print(f"  셀 좌표: {cell_rect}")
        results["cell_rect"] = cell_rect

        if not cell_rect or cell_rect.get("error"):
            # 하드코딩 좌표 사용 (Phase 6에서 확인: 데이터 행 y=231~262)
            cell_rect = {"x": 300, "y": 246}  # 데이터 행 중간
            print(f"  하드코딩 좌표 사용: {cell_rect}")

        # ============================================================
        # 3. 더블클릭 전 상태 캡처
        # ============================================================
        before_frames = driver.execute_script("""
            var fs = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet;
            var r = []; for (var k in fs) { if (fs[k] && fs[k].form && k.match(/^[A-Z]/)) r.push(k); }
            return r;
        """)
        print(f"\n  전 프레임: {before_frames}")

        # 행 0 선택
        driver.execute_script("""
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[arguments[0]].form;
            form.div_workForm.form.dsList.set_rowposition(0);
        """, FRAME_ID)

        # ============================================================
        # 4. ActionChains 더블클릭
        # ============================================================
        print(f"\n{'='*70}")
        print(f"  [4] ActionChains 더블클릭 at ({cell_rect['x']}, {cell_rect['y']})")
        print(f"{'='*70}")

        actions = ActionChains(driver)
        actions.move_by_offset(cell_rect["x"], cell_rect["y"]).double_click().perform()

        # 원점 복귀
        actions = ActionChains(driver)
        actions.move_by_offset(-cell_rect["x"], -cell_rect["y"]).perform()

        print("  더블클릭 완료, 5초 대기...")
        time.sleep(5)

        # ============================================================
        # 5. 더블클릭 후 상태 확인
        # ============================================================
        print(f"\n{'='*70}")
        print("  [5] 더블클릭 후 상태")
        print(f"{'='*70}")

        after_frames = driver.execute_script("""
            var fs = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet;
            var r = []; for (var k in fs) { if (fs[k] && fs[k].form && k.match(/^[A-Z]/)) r.push(k); }
            return r;
        """)
        print(f"  후 프레임: {after_frames}")
        results["after_frames"] = after_frames

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

        # dsGs 확인 (상세 이동 파라미터 확인)
        dsGs = driver.execute_script("""
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[arguments[0]].form;
            var dsGs = form.div_workForm.form.dsGs;
            if (!dsGs) return {error: 'dsGs not found'};
            var row = {};
            for (var c = 0; c < dsGs.getColCount(); c++) {
                var col = dsGs.getColID(c);
                var val = dsGs.getColumn(0, col);
                if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                row[col] = val;
            }
            return row;
        """, FRAME_ID)
        print(f"  dsGs: {dsGs}")
        results["dsGs"] = dsGs

        # ============================================================
        # 6. 모든 프레임 상세 데이터셋 탐색
        # ============================================================
        print(f"\n{'='*70}")
        print("  [6] 전체 프레임 데이터셋 탐색")
        print(f"{'='*70}")

        for fid in after_frames:
            if fid == "WorkFrame":
                continue

            ds_data = driver.execute_script("""
                var fid = arguments[0];
                try {
                    var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                    var datasets = [];

                    function findDs(f, prefix, depth) {
                        if (depth > 6) return;
                        for (var key in f) {
                            try {
                                var obj = f[key];
                                if (!obj || typeof obj !== 'object') continue;
                                if (obj.getRowCount && obj.getColumn && obj.getColCount) {
                                    var rc = obj.getRowCount();
                                    var cc = obj.getColCount();
                                    var cols = [];
                                    for (var c = 0; c < cc; c++) cols.push(obj.getColID(c));

                                    var hasItem = cols.some(function(c) {
                                        return c.indexOf('ITEM') >= 0;
                                    });

                                    var info = {name: key, path: prefix+'.'+key, rowCount: rc,
                                                columns: cols, hasItemCol: hasItem};

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
                                    findDs(obj.form, prefix+'.'+key+'.form', depth + 1);
                                }
                            } catch(e) {}
                        }
                    }

                    findDs(form, 'form', 0);
                    return datasets;
                } catch(e) { return [{error: e.message}]; }
            """, fid)

            if ds_data:
                # ITEM 컬럼이 있고 데이터가 있는 것만 표시
                item_datasets = [d for d in ds_data if d.get("hasItemCol") and d.get("rowCount", 0) > 0]
                other_datasets = [d for d in ds_data if not d.get("hasItemCol") and d.get("rowCount", 0) > 0
                                  and d["name"] not in ("dsChitDiv", "ds_btnList", "ds_menulog")]

                if item_datasets or other_datasets:
                    print(f"\n  === 프레임: {fid} ===")

                if item_datasets:
                    for ds in item_datasets:
                        print(f"\n  *** [{ds['name']}] rows={ds['rowCount']} *** ITEM DATA ***")
                        print(f"      cols: {ds['columns']}")
                        if ds.get("samples"):
                            for i, s in enumerate(ds["samples"][:5]):
                                ss = json.dumps(s, ensure_ascii=False, default=str)
                                if len(ss) > 600: ss = ss[:600] + "..."
                                print(f"      row[{i}]: {ss}")

                if other_datasets:
                    for ds in other_datasets:
                        print(f"\n  [{ds['name']}] rows={ds['rowCount']}")
                        print(f"    cols: {ds['columns']}")
                        if ds.get("samples"):
                            for i, s in enumerate(ds["samples"][:2]):
                                ss = json.dumps(s, ensure_ascii=False, default=str)
                                if len(ss) > 300: ss = ss[:300] + "..."
                                print(f"    row[{i}]: {ss}")

                results[f"ds_{fid}"] = ds_data

        # JSON 저장
        output_path = PROJECT_ROOT / "data" / "waste_slip_final_detail.json"
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
    discover_final(store_id=args.store_id)
