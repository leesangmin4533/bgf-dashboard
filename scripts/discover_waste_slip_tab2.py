"""
STGJ020_M0 Phase 8: 두 번째 탭(상세) 확인

더블클릭 후 탭 2개 열림 → 두 번째 탭 클릭 → 프레임 내용 변화 확인
또한 상세 불필요 시: dsList 전체 덤프로 폐기 총량 파악
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


def discover_tab2(store_id: str = DEFAULT_STORE_ID) -> dict:
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
        # 2. dsList 전체 덤프 (폐기 전표 목록)
        # ============================================================
        print(f"\n{'='*70}")
        print("  [2] dsList 전체 덤프 (폐기 전표 목록)")
        print(f"{'='*70}")

        all_slips = driver.execute_script("""
            var fid = arguments[0];
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
            var ds = form.div_workForm.form.dsList;
            var cols = [];
            for (var c = 0; c < ds.getColCount(); c++) cols.push(ds.getColID(c));

            var rows = [];
            for (var r = 0; r < ds.getRowCount(); r++) {
                var row = {};
                for (var ci = 0; ci < cols.length; ci++) {
                    var val = ds.getColumn(r, cols[ci]);
                    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                    row[cols[ci]] = val;
                }
                rows.push(row);
            }
            return {columns: cols, rows: rows, count: ds.getRowCount()};
        """, FRAME_ID)

        results["all_slips"] = all_slips

        # 날짜별 집계
        date_summary = {}
        total_items = 0
        for row in all_slips["rows"]:
            ymd = row.get("CHIT_YMD", "")
            if ymd not in date_summary:
                date_summary[ymd] = {"count": 0, "items": 0, "wonga": 0, "maega": 0}
            date_summary[ymd]["count"] += 1
            date_summary[ymd]["items"] += row.get("ITEM_CNT", 0) or 0
            date_summary[ymd]["wonga"] += row.get("WONGA_AMT", 0) or 0
            date_summary[ymd]["maega"] += row.get("MAEGA_AMT", 0) or 0
            total_items += row.get("ITEM_CNT", 0) or 0

        print(f"  총 전표: {all_slips['count']}건, 총 품목: {total_items}건")
        print(f"\n  [날짜별 집계]")
        for ymd in sorted(date_summary.keys(), reverse=True):
            s = date_summary[ymd]
            date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}" if len(ymd) == 8 else ymd
            print(f"  {date_str}: 전표 {s['count']:3d}건, 품목 {s['items']:3d}건, "
                  f"원가 {s['wonga']:>10,}원, 매가 {s['maega']:>10,}원")

        results["date_summary"] = date_summary

        # ============================================================
        # 3. 더블클릭 → 두 번째 탭 확인
        # ============================================================
        print(f"\n{'='*70}")
        print("  [3] 더블클릭 → 두 번째 탭 내용 확인")
        print(f"{'='*70}")

        # 행 선택 + 더블클릭
        driver.execute_script("""
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[arguments[0]].form;
            form.div_workForm.form.dsList.set_rowposition(0);
        """, FRAME_ID)

        cell_rect = driver.execute_script("""
            var cells = document.querySelectorAll('[id*="STGJ020"][id*="gdList"][id*="body"] div[id*="cell_0"]');
            for (var i = 0; i < cells.length; i++) {
                var c = cells[i];
                var r = c.getBoundingClientRect();
                if (r.width > 50 && r.height > 20 && r.height < 40 && c.children.length <= 1) {
                    return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
                }
            }
            return {x: 300, y: 246};
        """)

        actions = ActionChains(driver)
        actions.move_by_offset(cell_rect["x"], cell_rect["y"]).double_click().perform()
        actions = ActionChains(driver)
        actions.move_by_offset(-cell_rect["x"], -cell_rect["y"]).perform()
        print(f"  더블클릭 at ({cell_rect['x']}, {cell_rect['y']})")
        time.sleep(5)

        # 탭 목록 확인
        tabs = driver.execute_script("""
            var tabs = [];
            var els = document.querySelectorAll('[id*="tab_openList"][id*="tabbuttonitemtext"]');
            for (var i = 0; i < els.length; i++) {
                var text = (els[i].innerText || '').trim();
                if (text) tabs.push({idx: i, id: els[i].id, text: text});
            }
            return tabs;
        """)
        print(f"  탭: {tabs}")

        # 두 번째 탭 클릭 (ActionChains로)
        if len(tabs) > 1:
            tab2_rect = driver.execute_script("""
                var els = document.querySelectorAll('[id*="tab_openList"][id*="tabbuttonitemtext"]');
                if (els.length > 1) {
                    var r = els[1].getBoundingClientRect();
                    return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
                }
                return null;
            """)

            if tab2_rect:
                print(f"\n  두 번째 탭 클릭 at ({tab2_rect['x']}, {tab2_rect['y']})")
                actions = ActionChains(driver)
                actions.move_by_offset(tab2_rect["x"], tab2_rect["y"]).click().perform()
                actions = ActionChains(driver)
                actions.move_by_offset(-tab2_rect["x"], -tab2_rect["y"]).perform()
                time.sleep(3)

                # 프레임 변화 확인
                after_frames = driver.execute_script("""
                    var fs = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet;
                    var r = []; for (var k in fs) { if (fs[k] && fs[k].form && k.match(/^[A-Z]/)) r.push(k); }
                    return r;
                """)
                print(f"  프레임: {after_frames}")

                # STGJ020_M0 프레임의 데이터셋 변화 확인
                ds_after = driver.execute_script("""
                    var fid = arguments[0];
                    var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                    var wf = form.div_workForm ? form.div_workForm.form : form;

                    var datasets = {};
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

                                    datasets[key] = {rowCount: rc, columns: cols};

                                    if (rc > 0) {
                                        var sample = {};
                                        for (var ci = 0; ci < Math.min(cc, 30); ci++) {
                                            var val = obj.getColumn(0, cols[ci]);
                                            if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                                            sample[cols[ci]] = val;
                                        }
                                        datasets[key].sample = sample;
                                    }
                                }
                                if (obj.form && (key.startsWith('div') || key.startsWith('tab'))) {
                                    findDs(obj.form, prefix+'.'+key, depth+1);
                                }
                            } catch(e) {}
                        }
                    }
                    findDs(wf, 'wf', 0);
                    findDs(form, 'form', 0);
                    return datasets;
                """, FRAME_ID)

                for name, info in ds_after.items():
                    rc = info.get("rowCount", 0)
                    if rc > 0:
                        cols = info.get("columns", [])
                        has_item = any("ITEM" in c for c in cols)
                        marker = " *** ITEM" if has_item else ""
                        print(f"\n  [{name}] rows={rc}{marker}")
                        print(f"    cols: {cols}")
                        if info.get("sample"):
                            ss = json.dumps(info["sample"], ensure_ascii=False, default=str)
                            if len(ss) > 500: ss = ss[:500] + "..."
                            print(f"    sample: {ss}")

                results["tab2_datasets"] = ds_after

                # 새 프레임 탐색 (탭 클릭 후)
                for fid in after_frames:
                    if fid in ("WorkFrame", FRAME_ID):
                        continue
                    # 새 프레임 데이터셋 탐색
                    new_ds = driver.execute_script("""
                        var fid = arguments[0];
                        try {
                            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                            var datasets = {};
                            function scan(f, prefix, d) {
                                if (d > 5) return;
                                for (var key in f) {
                                    try {
                                        var obj = f[key];
                                        if (!obj) continue;
                                        if (obj.getRowCount && obj.getColumn) {
                                            var rc = obj.getRowCount();
                                            if (rc > 0) {
                                                var cc = obj.getColCount();
                                                var cols = [];
                                                for (var c = 0; c < cc; c++) cols.push(obj.getColID(c));
                                                var sample = {};
                                                for (var ci = 0; ci < Math.min(cc, 30); ci++) {
                                                    var val = obj.getColumn(0, cols[ci]);
                                                    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                                                    sample[cols[ci]] = val;
                                                }
                                                datasets[key] = {rowCount: rc, columns: cols, sample: sample};
                                            }
                                        }
                                        if (obj.form && key.startsWith('div')) {
                                            scan(obj.form, prefix+'.'+key, d+1);
                                        }
                                    } catch(e) {}
                                }
                            }
                            scan(form, 'form', 0);
                            return {frame: fid, datasets: datasets};
                        } catch(e) { return {error: e.message}; }
                    """, fid)
                    if new_ds.get("datasets"):
                        print(f"\n  === 새 프레임: {fid} ===")
                        for k, v in new_ds["datasets"].items():
                            print(f"  [{k}] rows={v['rowCount']} cols={v['columns']}")
                            if v.get("sample"):
                                ss = json.dumps(v["sample"], ensure_ascii=False, default=str)
                                print(f"    sample: {ss[:400]}")

        # JSON 저장
        output_path = PROJECT_ROOT / "data" / "waste_slip_tab2_detail.json"
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
    discover_tab2(store_id=args.store_id)
