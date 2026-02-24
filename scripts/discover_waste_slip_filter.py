"""
STGJ020_M0 Phase 3: 폐기 필터링 + 상세 데이터 탐색

확인된 구조:
  - 프레임 ID: STGJ020_M0
  - 전표구분 콤보: cbChitDiv (innerdataset: dsChitDiv, CODE/NAME)
  - 폐기 CODE: "10" (CHIT_ID_NM: "폐기") ← 수정됨: "04"는 무료택배
  - 날짜: calFromDate, calToDate
  - 조회: F_10 클릭 또는 fn_commBtn_10()
  - 결과 그리드: gdList (bind: dsList)
  - dsList 컬럼: CHIT_ID, CHIT_ID_NM, CHIT_YMD, CHIT_NO, ITEM_CNT, CENTER_NM, WONGA_AMT...

이번 탐색:
  1. dsChitDiv에서 "폐기" CODE 확인
  2. cbChitDiv를 "10"(폐기)로 설정
  3. 조회 실행
  4. 폐기 전표 목록 전체 덤프
  5. 첫 번째 폐기 전표 더블클릭 → 상세 화면 프레임/데이터셋 탐색
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


def discover_filter_and_detail(store_id: str = DEFAULT_STORE_ID) -> dict:
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

        # ============================================================
        # 2. dsChitDiv에서 폐기 CODE 확인
        # ============================================================
        print(f"\n{'='*70}")
        print("  [2] dsChitDiv 전체 옵션 덤프")
        print(f"{'='*70}")

        chitdiv_data = driver.execute_script("""
            var fid = arguments[0];
            try {
                var app = nexacro.getApplication();
                var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                var wf = form.div_workForm.form;
                var ds = wf.dsChitDiv;
                if (!ds) return {error: 'dsChitDiv not found'};

                var rows = [];
                for (var r = 0; r < ds.getRowCount(); r++) {
                    rows.push({
                        CODE: ds.getColumn(r, 'CODE'),
                        NAME: ds.getColumn(r, 'NAME')
                    });
                }
                return {rows: rows, count: ds.getRowCount()};
            } catch(e) {
                return {error: e.message};
            }
        """, FRAME_ID)

        results["chitdiv_options"] = chitdiv_data
        if chitdiv_data.get("rows"):
            for row in chitdiv_data["rows"]:
                marker = " *** WASTE" if row.get("CODE") == "10" or "폐기" in (row.get("NAME") or "") else ""
                print(f"  CODE={row.get('CODE', ''):4s} NAME={row.get('NAME', '')}{marker}")

        # ============================================================
        # 3. cbChitDiv를 "10"(폐기)로 설정 + 조회
        # ============================================================
        print(f"\n{'='*70}")
        print("  [3] 전표구분 = 폐기(10) 설정 + 조회")
        print(f"{'='*70}")

        filter_result = driver.execute_script("""
            var fid = arguments[0];
            try {
                var app = nexacro.getApplication();
                var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                var wf = form.div_workForm.form;

                // dsChitDiv에서 "10" 인덱스 찾기
                var ds = wf.dsChitDiv;
                var targetIdx = -1;
                for (var r = 0; r < ds.getRowCount(); r++) {
                    if (ds.getColumn(r, 'CODE') === '10') {
                        targetIdx = r;
                        break;
                    }
                }

                if (targetIdx < 0) return {error: 'CODE 04 not found in dsChitDiv'};

                // cbChitDiv 콤보 설정
                var cb = wf.div2.form.divSearch.form.cbChitDiv;
                if (!cb) return {error: 'cbChitDiv combo not found'};

                cb.set_index(targetIdx);

                // dsSearch의 strChitDiv도 설정
                var dsSearch = wf.dsSearch;
                if (dsSearch) {
                    dsSearch.setColumn(0, 'strChitDiv', '10');
                }

                // oncloseup 핸들러 호출
                if (typeof form.cbChitDiv_oncloseup === 'function') {
                    form.cbChitDiv_oncloseup(cb, {});
                }

                return {
                    success: true,
                    index: targetIdx,
                    value: cb.value,
                    text: cb.text,
                    dsSearch_chitDiv: dsSearch ? dsSearch.getColumn(0, 'strChitDiv') : 'N/A'
                };
            } catch(e) {
                return {error: e.message};
            }
        """, FRAME_ID)

        print(f"  필터 설정: {filter_result}")
        time.sleep(1)

        # 조회 실행
        search_result = driver.execute_script("""
            var fid = arguments[0];
            try {
                var app = nexacro.getApplication();
                var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;

                if (typeof form.fn_commBtn_10 === 'function') {
                    form.fn_commBtn_10();
                    return {method: 'fn_commBtn_10', success: true};
                }

                var cmmbtn = form.div_cmmbtn?.form;
                if (cmmbtn && cmmbtn.F_10) {
                    cmmbtn.F_10.click();
                    return {method: 'F_10', success: true};
                }

                return {success: false};
            } catch(e) {
                return {error: e.message};
            }
        """, FRAME_ID)

        print(f"  조회: {search_result}")
        time.sleep(3)

        # ============================================================
        # 4. 폐기 전표 목록 전체 덤프
        # ============================================================
        print(f"\n{'='*70}")
        print("  [4] 폐기 전표 목록 (dsList)")
        print(f"{'='*70}")

        waste_list = driver.execute_script("""
            var fid = arguments[0];
            try {
                var app = nexacro.getApplication();
                var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                var ds = form.div_workForm.form.dsList;
                if (!ds) return {error: 'dsList not found'};

                var rows = [];
                var cols = ['CHIT_FLAG', 'CHIT_ID', 'CHIT_ID_NM', 'CHIT_YMD', 'CHIT_NO',
                            'ITEM_CNT', 'CENTER_CD', 'CENTER_NM', 'WONGA_AMT', 'MAEGA_AMT',
                            'NAP_PLAN_YMD', 'CRE_YMDHMS'];

                for (var r = 0; r < ds.getRowCount(); r++) {
                    var row = {ROW_IDX: r};
                    for (var c = 0; c < cols.length; c++) {
                        var val = ds.getColumn(r, cols[c]);
                        if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                        row[cols[c]] = val;
                    }
                    rows.push(row);
                }

                return {rows: rows, count: ds.getRowCount()};
            } catch(e) {
                return {error: e.message};
            }
        """, FRAME_ID)

        results["waste_slips"] = waste_list
        if waste_list.get("rows"):
            print(f"  총 {waste_list['count']}건")
            for row in waste_list["rows"][:10]:
                ymd = row.get("CHIT_YMD", "")
                date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}" if len(ymd) == 8 else ymd
                print(f"  [{row['ROW_IDX']:3d}] {date_str} {row.get('CHIT_ID_NM', ''):8s} "
                      f"전표={row.get('CHIT_NO', '')} items={row.get('ITEM_CNT', 0)} "
                      f"센터={row.get('CENTER_NM', '')} 원가={row.get('WONGA_AMT', 0)}")
            if waste_list["count"] > 10:
                print(f"  ... ({waste_list['count'] - 10}건 더)")
        else:
            print(f"  {waste_list}")

        # ============================================================
        # 5. 첫 번째 폐기 전표 행 더블클릭 → 상세 페이지
        # ============================================================
        print(f"\n{'='*70}")
        print("  [5] 폐기 전표 행 더블클릭 → 상세 페이지 탐색")
        print(f"{'='*70}")

        if waste_list.get("rows") and len(waste_list["rows"]) > 0:
            first_row = waste_list["rows"][0]
            print(f"  선택 행: idx={first_row['ROW_IDX']} 전표={first_row.get('CHIT_NO')} items={first_row.get('ITEM_CNT')}")

            # fn_moveDetailPage 호출 (이벤트 핸들러에서 발견됨)
            detail_result = driver.execute_script("""
                var fid = arguments[0];
                var rowIdx = arguments[1];
                try {
                    var app = nexacro.getApplication();
                    var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                    var wf = form.div_workForm.form;
                    var ds = wf.dsList;

                    // 행 선택
                    ds.set_rowposition(rowIdx);

                    // fn_moveDetailPage 호출
                    if (typeof form.fn_moveDetailPage === 'function') {
                        form.fn_moveDetailPage();
                        return {method: 'fn_moveDetailPage', success: true, rowIdx: rowIdx};
                    }

                    // 대안: 그리드 더블클릭 이벤트
                    var grid = wf.div2?.form?.gdList;
                    if (grid && grid.oncelldblclick) {
                        var evt = {
                            cell: 0,
                            col: 0,
                            row: rowIdx,
                            clickitem: 'body'
                        };
                        grid.oncelldblclick._fireEvent(grid, evt);
                        return {method: 'grid_dblclick', success: true};
                    }

                    return {success: false, reason: 'no detail method found'};
                } catch(e) {
                    return {error: e.message};
                }
            """, FRAME_ID, first_row["ROW_IDX"])

            print(f"  상세 이동: {detail_result}")
            time.sleep(3)

            # 새로 로딩된 프레임 확인
            new_frames = driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    var fs = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
                    var frames = [];
                    for (var key in fs) {
                        if (fs[key] && fs[key].form && key.match(/^[A-Z]/)) {
                            frames.push(key);
                        }
                    }
                    return frames;
                } catch(e) {
                    return ['error: ' + e.message];
                }
            """)
            print(f"\n  현재 프레임: {new_frames}")
            results["frames_after_detail"] = new_frames

            # 탭 목록 확인
            tabs = driver.execute_script("""
                var tabs = [];
                var els = document.querySelectorAll('[id*="tab_openList"][id*="tabbuttonitemtext"]');
                for (var i = 0; i < els.length; i++) {
                    var text = (els[i].innerText || '').trim();
                    if (text) tabs.push({id: els[i].id, text: text});
                }
                return tabs;
            """)
            print(f"  열린 탭: {[t['text'] for t in tabs]}")
            results["tabs_after_detail"] = tabs

            # 새 프레임의 데이터셋 탐색
            detail_frame_id = None
            known = {"WorkFrame", FRAME_ID}
            for f in new_frames:
                if f not in known:
                    detail_frame_id = f
                    break

            if detail_frame_id:
                print(f"\n  *** 상세 프레임: {detail_frame_id}")

                detail_ds = driver.execute_script("""
                    var fid = arguments[0];
                    try {
                        var app = nexacro.getApplication();
                        var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid]?.form;
                        if (!form) return {error: 'form not found for ' + fid};

                        var datasets = [];

                        function findDs(f, prefix) {
                            for (var key in f) {
                                var obj = f[key];
                                if (obj && obj.getRowCount && obj.getColumn) {
                                    var info = {name: key, path: prefix + '.' + key, rowCount: obj.getRowCount()};
                                    var cols = [];
                                    try {
                                        var cc = obj.getColCount ? obj.getColCount() : 0;
                                        for (var c = 0; c < cc; c++) cols.push(obj.getColID(c));
                                    } catch(e) {}
                                    info.columns = cols;

                                    if (info.rowCount > 0 && cols.length > 0) {
                                        var samples = [];
                                        for (var r = 0; r < Math.min(info.rowCount, 5); r++) {
                                            var row = {};
                                            for (var c = 0; c < Math.min(cols.length, 25); c++) {
                                                var val = obj.getColumn(r, cols[c]);
                                                if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                                                row[cols[c]] = val;
                                            }
                                            samples.push(row);
                                        }
                                        info.samples = samples;
                                    }

                                    datasets.push(info);
                                }

                                if (f[key] && f[key].form && key.startsWith('div')) {
                                    findDs(f[key].form, prefix + '.' + key + '.form');
                                }
                            }
                        }

                        findDs(form, 'form');
                        return {frame_id: fid, datasets: datasets};
                    } catch(e) {
                        return {error: e.message};
                    }
                """, detail_frame_id)

                results["detail_datasets"] = detail_ds
                if detail_ds.get("datasets"):
                    for ds in detail_ds["datasets"]:
                        print(f"\n  [{ds['name']}] rows={ds['rowCount']} path={ds['path']}")
                        print(f"    cols: {ds.get('columns', [])}")
                        if ds.get("samples"):
                            for i, s in enumerate(ds["samples"][:3]):
                                print(f"    row[{i}]: {json.dumps(s, ensure_ascii=False, default=str)}")
                else:
                    print(f"  {detail_ds}")
            else:
                print("  상세 프레임을 찾지 못했습니다. 같은 프레임에서 데이터 변경 확인...")

                # 같은 프레임의 dsList 변경 확인
                same_frame_check = driver.execute_script("""
                    var fid = arguments[0];
                    try {
                        var app = nexacro.getApplication();
                        var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                        var wf = form.div_workForm?.form;
                        if (!wf) return {error: 'workform not found'};

                        var datasets = {};
                        for (var key in wf) {
                            var obj = wf[key];
                            if (obj && obj.getRowCount && obj.getColumn) {
                                var info = {rowCount: obj.getRowCount()};
                                var cols = [];
                                try {
                                    var cc = obj.getColCount ? obj.getColCount() : 0;
                                    for (var c = 0; c < cc; c++) cols.push(obj.getColID(c));
                                } catch(e){}
                                info.columns = cols;
                                if (info.rowCount > 0) {
                                    var sample = {};
                                    for (var c = 0; c < Math.min(cols.length, 25); c++) {
                                        var val = obj.getColumn(0, cols[c]);
                                        if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                                        sample[cols[c]] = val;
                                    }
                                    info.sample = sample;
                                }
                                datasets[key] = info;
                            }
                        }
                        return datasets;
                    } catch(e) {
                        return {error: e.message};
                    }
                """, FRAME_ID)

                results["same_frame_after_detail"] = same_frame_check
                for k, v in same_frame_check.items():
                    if isinstance(v, dict) and v.get("rowCount", 0) > 0:
                        print(f"\n  [{k}] rows={v['rowCount']} cols={v.get('columns', [])}")
                        if v.get("sample"):
                            print(f"    sample: {json.dumps(v['sample'], ensure_ascii=False, default=str)}")

        # JSON 저장
        output_path = PROJECT_ROOT / "data" / "waste_slip_filter_detail.json"
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
    discover_filter_and_detail(store_id=args.store_id)
