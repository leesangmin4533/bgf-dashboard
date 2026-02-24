"""
BGF STGJ020_M0 (통합 전표 조회) 상세 구조 탐색 - Phase 2

이미 프레임 ID(STGJ020_M0)는 확인됨.
이번에는:
  1. div_workForm 하위 전체 객체 (Dataset, Combo, Grid, Calendar 등)
  2. 전표구분(cbChitDiv) 콤보의 옵션 목록
  3. 날짜 컨트롤 식별
  4. 조회 실행 후 데이터셋 컬럼/데이터 확인
  5. 행 더블클릭 후 상세 데이터 확인
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


def discover_detail(store_id: str = DEFAULT_STORE_ID) -> dict:
    analyzer = SalesAnalyzer(store_id=store_id)
    results = {}

    try:
        # 로그인
        print("\n[0] 로그인...")
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
        print(f"\n[1] 검수전표 > 통합 전표 조회 이동 (FRAME: {FRAME_ID})")
        success = navigate_menu(driver, "검수전표", "통합 전표 조회", FRAME_ID)
        print(f"  메뉴 이동: {success}")
        time.sleep(2)

        # ============================================================
        # 2. form 전체 객체 탐색 (깊은 탐색)
        # ============================================================
        print(f"\n{'='*70}")
        print(f"  [2] form 전체 객체 탐색 (깊이 우선)")
        print(f"{'='*70}")

        full_structure = driver.execute_script("""
            var fid = arguments[0];
            var report = {};

            try {
                var app = nexacro.getApplication();
                var frame = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid];
                if (!frame || !frame.form) {
                    report.error = 'frame or form not found';
                    return report;
                }

                var form = frame.form;
                report.form_found = true;

                // form 직속 속성 탐색
                var topLevel = [];
                for (var key in form) {
                    var obj = form[key];
                    if (!obj || typeof obj === 'function' || typeof obj === 'string'
                        || typeof obj === 'number' || typeof obj === 'boolean') continue;

                    // 넥사크로 컴포넌트인지 확인
                    if (obj.name || obj.id || (obj.form && typeof obj.form === 'object')) {
                        var type = '';
                        if (obj.getRowCount && obj.getColumn) type = 'Dataset';
                        else if (obj.binddataset !== undefined && obj.getCellCount) type = 'Grid';
                        else if (obj.index !== undefined && obj.innerdataset) type = 'Combo';
                        else if (obj.calendarpopup !== undefined || key.startsWith('cal') || key.startsWith('dt')) type = 'Calendar';
                        else if (obj.onclick !== undefined && !obj.form) type = 'Button';
                        else if (obj.value !== undefined && obj.text !== undefined && !obj.form) type = 'Edit';
                        else if (obj.form) type = 'Div';
                        else type = obj.constructor?.name || 'unknown';

                        var info = {name: key, type: type};

                        if (type === 'Dataset') {
                            info.rowCount = obj.getRowCount();
                            var cols = [];
                            try {
                                var ci = obj.getColCount ? obj.getColCount() : 0;
                                for (var c = 0; c < ci; c++) {
                                    cols.push(obj.getColID(c));
                                }
                            } catch(e) {}
                            info.columns = cols;
                        }
                        else if (type === 'Combo') {
                            try {
                                info.value = obj.value;
                                info.text = obj.text;
                                info.index = obj.index;
                                // 내부 데이터셋에서 옵션 목록
                                var ids = obj.innerdataset;
                                if (ids) {
                                    var dsName = ids.replace(':', '').replace('@', '');
                                    var dsObj = null;
                                    // form에서 데이터셋 찾기
                                    for (var dk in form) {
                                        if (dk === dsName || (form[dk] && form[dk].name === dsName)) {
                                            dsObj = form[dk];
                                            break;
                                        }
                                    }
                                    if (!dsObj && form.div_workForm && form.div_workForm.form) {
                                        var wf = form.div_workForm.form;
                                        for (var dk in wf) {
                                            if (dk === dsName || (wf[dk] && wf[dk].name === dsName)) {
                                                dsObj = wf[dk];
                                                break;
                                            }
                                        }
                                    }
                                }
                            } catch(e) { info.combo_error = e.message; }
                        }
                        else if (type === 'Calendar') {
                            try {
                                info.value = obj.value;
                                info.text = obj.text;
                            } catch(e) {}
                        }
                        else if (type === 'Grid') {
                            try {
                                info.binddataset = obj.binddataset;
                            } catch(e) {}
                        }

                        topLevel.push(info);
                    }
                }
                report.form_objects = topLevel;

                // div_workForm 탐색
                var wf = form.div_workForm?.form;
                if (!wf) {
                    // 다른 패턴 시도
                    for (var key in form) {
                        if (key.startsWith('div') && form[key] && form[key].form) {
                            wf = form[key].form;
                            report.workform_key = key;
                            break;
                        }
                    }
                }

                if (wf) {
                    report.workform_found = true;
                    var wfObjs = [];

                    // div_workForm 내 재귀 탐색
                    function scan(f, prefix, depth) {
                        if (depth > 6) return;
                        for (var key in f) {
                            var obj = f[key];
                            if (!obj || typeof obj === 'function' || typeof obj === 'string'
                                || typeof obj === 'number' || typeof obj === 'boolean') continue;

                            var type = '';
                            if (obj.getRowCount && obj.getColumn) type = 'Dataset';
                            else if (obj.binddataset !== undefined && obj.getCellCount) type = 'Grid';
                            else if (obj.index !== undefined && obj.innerdataset) type = 'Combo';
                            else if (obj.calendarpopup !== undefined || key.startsWith('cal')) type = 'Calendar';
                            else if (obj.onclick !== undefined && !obj.form) type = 'Button';
                            else if (obj.value !== undefined && key.startsWith('edt')) type = 'Edit';
                            else if (obj.form && key.startsWith('div')) type = 'Div';
                            else continue;

                            var info = {name: key, type: type, path: prefix + '.' + key};

                            if (type === 'Dataset') {
                                info.rowCount = obj.getRowCount();
                                var cols = [];
                                try {
                                    var ci = obj.getColCount ? obj.getColCount() : 0;
                                    for (var c = 0; c < ci; c++) cols.push(obj.getColID(c));
                                } catch(e) {}
                                info.columns = cols;
                                if (info.rowCount > 0 && cols.length > 0) {
                                    var sample = {};
                                    for (var c = 0; c < Math.min(cols.length, 25); c++) {
                                        var val = obj.getColumn(0, cols[c]);
                                        if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                                        sample[cols[c]] = val;
                                    }
                                    info.sample = sample;
                                }
                            }
                            else if (type === 'Combo') {
                                try { info.value = obj.value; info.text = obj.text; } catch(e){}
                            }
                            else if (type === 'Calendar') {
                                try { info.value = obj.value; } catch(e){}
                            }
                            else if (type === 'Grid') {
                                try { info.binddataset = obj.binddataset; } catch(e){}
                            }
                            else if (type === 'Button') {
                                try { info.text = obj.text; } catch(e){}
                            }

                            wfObjs.push(info);

                            if (type === 'Div' && obj.form) {
                                scan(obj.form, prefix + '.' + key + '.form', depth + 1);
                            }
                        }
                    }

                    scan(wf, 'div_workForm.form', 0);
                    report.workform_objects = wfObjs;
                } else {
                    report.workform_found = false;
                }

                return report;

            } catch(e) {
                report.error = e.message;
                report.stack = e.stack;
                return report;
            }
        """, FRAME_ID)

        results["structure"] = full_structure

        if full_structure.get("error"):
            print(f"  [ERROR] {full_structure['error']}")
        else:
            # form 직속 객체
            print(f"\n  form 직속 객체 ({len(full_structure.get('form_objects', []))}):")
            for obj in full_structure.get("form_objects", []):
                extra = ""
                if obj["type"] == "Dataset":
                    extra = f" rows={obj.get('rowCount', 0)} cols={obj.get('columns', [])}"
                elif obj["type"] == "Combo":
                    extra = f" value={obj.get('value')} text={obj.get('text')}"
                elif obj["type"] == "Calendar":
                    extra = f" value={obj.get('value')}"
                elif obj["type"] == "Grid":
                    extra = f" bind={obj.get('binddataset')}"
                elif obj["type"] == "Button":
                    extra = f" text={obj.get('text')}"
                print(f"    [{obj['type']:10s}] {obj['name']}{extra}")

            # workForm 객체
            wf_key = full_structure.get("workform_key", "div_workForm")
            print(f"\n  {wf_key} 객체 ({len(full_structure.get('workform_objects', []))}):")
            for obj in full_structure.get("workform_objects", []):
                extra = ""
                if obj["type"] == "Dataset":
                    extra = f" rows={obj.get('rowCount', 0)} cols={obj.get('columns', [])}"
                    if obj.get("sample"):
                        extra += f"\n      sample: {json.dumps(obj['sample'], ensure_ascii=False, default=str)}"
                elif obj["type"] == "Combo":
                    extra = f" value={obj.get('value')} text={obj.get('text')}"
                elif obj["type"] == "Calendar":
                    extra = f" value={obj.get('value')}"
                elif obj["type"] == "Grid":
                    extra = f" bind={obj.get('binddataset')}"
                elif obj["type"] == "Button":
                    extra = f" text={obj.get('text')}"
                print(f"    [{obj['type']:10s}] {obj['path']:50s}{extra}")

        # ============================================================
        # 3. cbChitDiv (전표구분) 콤보 옵션 목록
        # ============================================================
        print(f"\n{'='*70}")
        print(f"  [3] 전표구분(cbChitDiv) 콤보 옵션 목록")
        print(f"{'='*70}")

        combo_info = driver.execute_script("""
            var fid = arguments[0];
            try {
                var app = nexacro.getApplication();
                var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;

                // cbChitDiv 찾기: form 직속 또는 div_workForm 하위
                var combo = null;

                // 재귀 탐색
                function findCombo(f, name) {
                    if (f[name]) return f[name];
                    for (var key in f) {
                        if (f[key] && f[key].form) {
                            var found = findCombo(f[key].form, name);
                            if (found) return found;
                        }
                    }
                    return null;
                }

                combo = findCombo(form, 'cbChitDiv');

                if (!combo) {
                    // cb로 시작하는 모든 콤보 탐색
                    var allCombos = [];
                    function findAllCombos(f, prefix) {
                        for (var key in f) {
                            if (key.startsWith('cb') || key.startsWith('cmb')) {
                                if (f[key] && f[key].index !== undefined) {
                                    allCombos.push({
                                        name: key,
                                        path: prefix + '.' + key,
                                        value: f[key].value,
                                        text: f[key].text
                                    });
                                }
                            }
                            if (f[key] && f[key].form && key.startsWith('div')) {
                                findAllCombos(f[key].form, prefix + '.' + key + '.form');
                            }
                        }
                    }
                    findAllCombos(form, 'form');
                    return {found: false, all_combos: allCombos};
                }

                var result = {
                    found: true,
                    value: combo.value,
                    text: combo.text,
                    index: combo.index,
                    innerdataset: combo.innerdataset
                };

                // 내부 데이터셋에서 전체 옵션 읽기
                var idName = (combo.innerdataset || '').replace('@', '');
                var ids = null;

                // form 및 하위에서 데이터셋 찾기
                function findDs(f, name) {
                    if (f[name] && f[name].getRowCount) return f[name];
                    for (var key in f) {
                        if (f[key] && f[key].form && key.startsWith('div')) {
                            var found = findDs(f[key].form, name);
                            if (found) return found;
                        }
                    }
                    return null;
                }

                if (idName) {
                    ids = findDs(form, idName);
                }

                if (ids) {
                    var options = [];
                    for (var r = 0; r < ids.getRowCount(); r++) {
                        var opt = {};
                        var cc = ids.getColCount ? ids.getColCount() : 0;
                        for (var c = 0; c < cc; c++) {
                            var colName = ids.getColID(c);
                            opt[colName] = ids.getColumn(r, colName);
                        }
                        options.push(opt);
                    }
                    result.options = options;
                } else {
                    result.dataset_not_found = idName;
                }

                return result;
            } catch(e) {
                return {error: e.message};
            }
        """, FRAME_ID)

        results["combo_chitdiv"] = combo_info
        if combo_info.get("found"):
            print(f"  현재값: value={combo_info.get('value')}, text={combo_info.get('text')}")
            print(f"  innerdataset: {combo_info.get('innerdataset')}")
            if combo_info.get("options"):
                print(f"  옵션 목록 ({len(combo_info['options'])}):")
                for opt in combo_info["options"]:
                    print(f"    {opt}")
            else:
                print(f"  데이터셋 미발견: {combo_info.get('dataset_not_found')}")
        elif combo_info.get("all_combos"):
            print(f"  cbChitDiv 미발견. 발견된 콤보:")
            for cb in combo_info["all_combos"]:
                print(f"    {cb['path']}: value={cb.get('value')} text={cb.get('text')}")
        else:
            print(f"  [ERROR] {combo_info.get('error', 'unknown')}")

        # ============================================================
        # 4. 날짜 컨트롤 탐색
        # ============================================================
        print(f"\n{'='*70}")
        print(f"  [4] 날짜 컨트롤 탐색")
        print(f"{'='*70}")

        date_controls = driver.execute_script("""
            var fid = arguments[0];
            try {
                var app = nexacro.getApplication();
                var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                var controls = [];

                function findDateControls(f, prefix) {
                    for (var key in f) {
                        var obj = f[key];
                        if (!obj || typeof obj !== 'object') continue;

                        // Calendar 컨트롤
                        if (key.startsWith('cal') || key.startsWith('dt')
                            || (obj.calendarpopup !== undefined)) {
                            controls.push({
                                name: key,
                                path: prefix + '.' + key,
                                type: 'Calendar',
                                value: obj.value || '',
                                text: obj.text || ''
                            });
                        }

                        // Edit 중 날짜 관련
                        if (key.startsWith('edt') && (key.toLowerCase().includes('date')
                            || key.toLowerCase().includes('ymd')
                            || key.toLowerCase().includes('from')
                            || key.toLowerCase().includes('to'))) {
                            controls.push({
                                name: key,
                                path: prefix + '.' + key,
                                type: 'Edit',
                                value: obj.value || '',
                                text: obj.text || ''
                            });
                        }

                        // div 재귀
                        if (obj.form && key.startsWith('div')) {
                            findDateControls(obj.form, prefix + '.' + key + '.form');
                        }
                    }
                }

                findDateControls(form, 'form');
                return controls;
            } catch(e) {
                return [{error: e.message}];
            }
        """, FRAME_ID)

        results["date_controls"] = date_controls
        for dc in date_controls:
            print(f"  [{dc.get('type', '?')}] {dc.get('path', '?')}: value={dc.get('value')} text={dc.get('text')}")

        # ============================================================
        # 5. 조회 실행 (F_10 버튼 또는 fn_search)
        # ============================================================
        print(f"\n{'='*70}")
        print(f"  [5] 조회 실행 (fn_search / F_10)")
        print(f"{'='*70}")

        search_result = driver.execute_script("""
            var fid = arguments[0];
            try {
                var app = nexacro.getApplication();
                var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;

                // fn_search 또는 F_10 조회 버튼
                // 방법 1: fn_search() 직접 호출
                if (typeof form.fn_search === 'function') {
                    form.fn_search();
                    return {method: 'fn_search', success: true};
                }

                // 방법 2: div_cmmbtn.F_10 클릭
                var wf = form.div_workForm?.form || form;
                var cmmbtn = form.div_cmmbtn?.form || wf.div_cmmbtn?.form;
                if (cmmbtn && cmmbtn.F_10) {
                    cmmbtn.F_10.click();
                    return {method: 'F_10_click', success: true};
                }

                // 방법 3: fn_commBtn_10 호출
                if (typeof form.fn_commBtn_10 === 'function') {
                    form.fn_commBtn_10();
                    return {method: 'fn_commBtn_10', success: true};
                }

                return {success: false, reason: 'no_search_method_found'};
            } catch(e) {
                return {error: e.message};
            }
        """, FRAME_ID)

        print(f"  조회 결과: {search_result}")
        results["search"] = search_result

        # 데이터 로딩 대기
        time.sleep(3)

        # ============================================================
        # 6. 조회 후 데이터셋 상태 확인
        # ============================================================
        print(f"\n{'='*70}")
        print(f"  [6] 조회 후 데이터셋 상태")
        print(f"{'='*70}")

        post_search = driver.execute_script("""
            var fid = arguments[0];
            try {
                var app = nexacro.getApplication();
                var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                var datasets = [];

                function findDatasets(f, prefix) {
                    for (var key in f) {
                        var obj = f[key];
                        if (obj && obj.getRowCount && obj.getColumn) {
                            var info = {
                                name: key,
                                path: prefix + '.' + key,
                                rowCount: obj.getRowCount()
                            };

                            // 컬럼 정보
                            var cols = [];
                            try {
                                var cc = obj.getColCount ? obj.getColCount() : 0;
                                for (var c = 0; c < cc; c++) {
                                    cols.push(obj.getColID(c));
                                }
                            } catch(e) {}
                            info.columns = cols;

                            // 샘플 데이터 (최대 3행)
                            if (info.rowCount > 0 && cols.length > 0) {
                                var samples = [];
                                for (var r = 0; r < Math.min(info.rowCount, 3); r++) {
                                    var row = {};
                                    for (var c = 0; c < Math.min(cols.length, 20); c++) {
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

                        // div 재귀
                        if (f[key] && f[key].form && key.startsWith('div')) {
                            findDatasets(f[key].form, prefix + '.' + key + '.form');
                        }
                    }
                }

                findDatasets(form, 'form');
                return datasets;
            } catch(e) {
                return [{error: e.message}];
            }
        """, FRAME_ID)

        results["post_search_datasets"] = post_search
        for ds in post_search:
            if ds.get("error"):
                print(f"  [ERROR] {ds['error']}")
                continue
            print(f"\n  [{ds['name']}] rows={ds['rowCount']} path={ds['path']}")
            print(f"    columns: {ds.get('columns', [])}")
            if ds.get("samples"):
                for i, sample in enumerate(ds["samples"]):
                    print(f"    row[{i}]: {json.dumps(sample, ensure_ascii=False, default=str)}")

        # JSON 저장
        output_path = PROJECT_ROOT / "data" / "waste_slip_detail_structure.json"
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
    discover_detail(store_id=args.store_id)
