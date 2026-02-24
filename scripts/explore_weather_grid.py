"""
BGF TopFrame 날씨 예보 그리드(grd_weather) 구조 탐색 스크립트

사용법:
    cd bgf_auto
    python scripts/explore_weather_grid.py [--store-id 46513]

출력:
    TopFrame의 모든 Dataset/Grid 객체와 날씨 관련 데이터 구조를 출력합니다.
    이 정보를 바탕으로 weather_utils.py의 컬럼명 매칭을 조정할 수 있습니다.
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.sales_analyzer import SalesAnalyzer
from src.settings.constants import DEFAULT_STORE_ID


def explore_weather_grid(store_id: str = DEFAULT_STORE_ID) -> dict:
    """BGF TopFrame에서 날씨 관련 객체 구조를 탐색"""

    analyzer = SalesAnalyzer(store_id=store_id)

    try:
        analyzer.setup_driver()
        analyzer.connect()

        if not analyzer.do_login():
            print("[ERROR] Login failed")
            return {}

        analyzer.close_popup()

        import time

        # 로그인 후 메인 프레임 로드 대기
        print("\n메인 프레임 로드 대기 중...")
        for wait_i in range(10):
            check = analyzer.driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    var tf = app.mainframe.HFrameSet00.VFrameSet00.TopFrame.form;
                    return tf ? 'ready' : 'not_ready';
                } catch(e) {
                    return 'error: ' + e.message;
                }
            """)
            print(f"  [{wait_i+1}/10] {check}")
            if check == 'ready':
                break
            time.sleep(2)

        time.sleep(1)

        # 1단계: TopFrame + pdiv_weather 구조 탐색
        print("\n" + "=" * 70)
        print("  BGF TopFrame 날씨 객체 탐색")
        print("=" * 70)

        result = analyzer.driver.execute_script("""
            var report = {};

            try {
                var app = nexacro.getApplication();
                var topForm = app.mainframe.HFrameSet00.VFrameSet00.TopFrame.form;
                report.topframe_found = true;

                // === TopFrame.form 레벨 객체 탐색 ===
                report.topform_objects = [];
                try {
                    var objs = topForm.objects;
                    if (objs) {
                        for (var i = 0; i < objs.length; i++) {
                            var o = objs[i];
                            if (o && o.getClassName) {
                                report.topform_objects.push({
                                    name: o.name,
                                    className: o.getClassName()
                                });
                            }
                        }
                    }
                } catch(e1) { report.topform_objects_error = e1.message; }

                // === pdiv_weather 접근 ===
                report.pdiv_weather = {};
                var weatherForm = null;
                try {
                    var pdiv = topForm.pdiv_weather;
                    if (pdiv) {
                        report.pdiv_weather.found = true;
                        report.pdiv_weather.className = pdiv.getClassName ? pdiv.getClassName() : 'unknown';
                        weatherForm = pdiv.form;
                        report.pdiv_weather.has_form = !!weatherForm;
                    } else {
                        report.pdiv_weather.found = false;
                    }
                } catch(e2) {
                    report.pdiv_weather.found = false;
                    report.pdiv_weather.error = e2.message;
                }

                // === pdiv_weather.form 내부 객체 탐색 ===
                if (weatherForm) {
                    report.weather_form_objects = [];
                    report.weather_datasets = [];
                    report.weather_grids = [];

                    try {
                        var wObjs = weatherForm.objects;
                        if (wObjs) {
                            for (var i = 0; i < wObjs.length; i++) {
                                var o = wObjs[i];
                                if (!o || !o.getClassName) continue;
                                var cn = o.getClassName();
                                var info = { name: o.name, className: cn };

                                report.weather_form_objects.push(info);

                                if (cn === 'Dataset') {
                                    var dsInfo = {
                                        name: o.name,
                                        rowCount: 0,
                                        colCount: 0,
                                        columns: [],
                                        sampleRows: []
                                    };
                                    try {
                                        dsInfo.rowCount = o.getRowCount();
                                        dsInfo.colCount = o.getColCount();
                                        for (var c = 0; c < dsInfo.colCount; c++) {
                                            dsInfo.columns.push({
                                                id: o.getColID(c),
                                                type: o.getColType(c) || '',
                                                size: o.getColSize(c) || 0
                                            });
                                        }
                                        for (var r = 0; r < Math.min(dsInfo.rowCount, 10); r++) {
                                            var row = {};
                                            for (var ci = 0; ci < dsInfo.colCount; ci++) {
                                                var colId = o.getColID(ci);
                                                row[colId] = o.getColumn(r, colId);
                                            }
                                            dsInfo.sampleRows.push(row);
                                        }
                                    } catch(ed) { dsInfo.error = ed.message; }
                                    report.weather_datasets.push(dsInfo);
                                }

                                if (cn === 'Grid') {
                                    var gInfo = {
                                        name: o.name,
                                        binddataset: ''
                                    };
                                    try {
                                        gInfo.binddataset = o.binddataset || '';
                                        gInfo.visible = o.visible;
                                    } catch(eg) {}
                                    report.weather_grids.push(gInfo);
                                }
                            }
                        }
                    } catch(e3) { report.weather_form_error = e3.message; }

                    // grd_weather에서 직접 셀 데이터 읽기 시도
                    report.grd_weather_cells = {};
                    try {
                        var gw = weatherForm.grd_weather;
                        if (gw) {
                            report.grd_weather_cells.found = true;
                            report.grd_weather_cells.binddataset = gw.binddataset || '';
                            // 바인딩된 Dataset 직접 접근
                            var dsName = gw.binddataset;
                            if (dsName) {
                                var ds = weatherForm[dsName];
                                if (ds && ds.getRowCount) {
                                    report.grd_weather_cells.dataset_name = dsName;
                                    report.grd_weather_cells.rowCount = ds.getRowCount();
                                    report.grd_weather_cells.colCount = ds.getColCount();
                                    var cols = [];
                                    for (var c = 0; c < ds.getColCount(); c++) {
                                        cols.push(ds.getColID(c));
                                    }
                                    report.grd_weather_cells.columns = cols;
                                    // 전체 데이터 (보통 3일*8시간=24행 이내)
                                    report.grd_weather_cells.allRows = [];
                                    for (var r = 0; r < Math.min(ds.getRowCount(), 100); r++) {
                                        var row = {};
                                        for (var ci = 0; ci < cols.length; ci++) {
                                            row[cols[ci]] = ds.getColumn(r, cols[ci]);
                                        }
                                        report.grd_weather_cells.allRows.push(row);
                                    }
                                }
                            }
                        } else {
                            report.grd_weather_cells.found = false;
                        }
                    } catch(e4) {
                        report.grd_weather_cells.error = e4.message;
                    }

                    // sta_degree, sta_dateTop 등 Static 텍스트 읽기
                    report.weather_statics = {};
                    try {
                        var staticNames = ['sta_degree', 'sta_dateTop', 'sta_date', 'sta_storeNm'];
                        for (var si = 0; si < staticNames.length; si++) {
                            var sn = staticNames[si];
                            var sObj = weatherForm[sn];
                            if (sObj) {
                                report.weather_statics[sn] = {
                                    text: sObj.text || '',
                                    value: sObj.value || '',
                                    visible: sObj.visible
                                };
                            }
                        }
                    } catch(e5) {}
                }

                // === TopFrame.form 레벨 Dataset도 체크 (예비) ===
                report.topform_datasets = [];
                try {
                    var topObjs = topForm.objects;
                    if (topObjs) {
                        for (var i = 0; i < topObjs.length; i++) {
                            var o = topObjs[i];
                            if (o && o.getClassName && o.getClassName() === 'Dataset') {
                                var di = { name: o.name, rowCount: 0 };
                                try { di.rowCount = o.getRowCount(); } catch(x) {}
                                report.topform_datasets.push(di);
                            }
                        }
                    }
                } catch(e6) {}

            } catch(eMain) {
                report.topframe_found = false;
                report.main_error = eMain.message;
            }

            return report;
        """)

        # 결과 출력
        if not result:
            print("[ERROR] JS returned null")
            return {}

        if result.get("topframe_found"):
            print("\n[OK] TopFrame 접근 성공")
        else:
            print(f"\n[FAIL] TopFrame 접근 실패: {result.get('main_error')}")
            return result

        # TopFrame.form 레벨 객체
        top_objs = result.get("topform_objects", [])
        print(f"\n--- TopFrame.form 객체 ({len(top_objs)}개) ---")
        for o in top_objs:
            print(f"  [{o['name']}] {o['className']}")

        # pdiv_weather
        pw = result.get("pdiv_weather", {})
        print(f"\n--- pdiv_weather ---")
        if pw.get("found"):
            print(f"  [OK] className={pw.get('className')}, has_form={pw.get('has_form')}")
        else:
            print(f"  [NOT FOUND] {pw.get('error', '')}")

        # weather form 내부 객체
        wf_objs = result.get("weather_form_objects", [])
        if wf_objs:
            print(f"\n--- pdiv_weather.form 내부 객체 ({len(wf_objs)}개) ---")
            for o in wf_objs:
                print(f"  [{o['name']}] {o['className']}")

        # weather Datasets
        w_datasets = result.get("weather_datasets", [])
        if w_datasets:
            print(f"\n--- pdiv_weather.form Datasets ({len(w_datasets)}개) ---")
            for ds in w_datasets:
                print(f"\n  [{ds['name']}]")
                print(f"    rows={ds.get('rowCount', '?')}, cols={ds.get('colCount', '?')}")
                if ds.get("columns"):
                    col_names = [c["id"] for c in ds["columns"]]
                    print(f"    columns: {col_names}")
                if ds.get("sampleRows"):
                    print(f"    sample rows:")
                    for ri, row in enumerate(ds["sampleRows"]):
                        short = {k: (str(v)[:50] if v and len(str(v)) > 50 else v)
                                 for k, v in row.items()}
                        print(f"      [{ri}]: {short}")

        # weather Grids
        w_grids = result.get("weather_grids", [])
        if w_grids:
            print(f"\n--- pdiv_weather.form Grids ({len(w_grids)}개) ---")
            for g in w_grids:
                print(f"  [{g['name']}] binddataset={g.get('binddataset', '?')} visible={g.get('visible', '?')}")

        # grd_weather 셀 데이터
        gwc = result.get("grd_weather_cells", {})
        print(f"\n--- grd_weather 그리드 데이터 ---")
        if gwc.get("found"):
            print(f"  [OK] binddataset={gwc.get('binddataset')}")
            print(f"  dataset: {gwc.get('dataset_name')}")
            print(f"  rows={gwc.get('rowCount', 0)}, cols={gwc.get('colCount', 0)}")
            print(f"  columns: {gwc.get('columns', [])}")
            all_rows = gwc.get("allRows", [])
            if all_rows:
                print(f"  ALL DATA ({len(all_rows)} rows):")
                for ri, row in enumerate(all_rows):
                    print(f"    [{ri}]: {row}")
        else:
            print(f"  [NOT FOUND] {gwc.get('error', '')}")

        # Static 텍스트
        statics = result.get("weather_statics", {})
        if statics:
            print(f"\n--- 날씨 Static 텍스트 ---")
            for name, info in statics.items():
                print(f"  [{name}] text='{info.get('text', '')}' value='{info.get('value', '')}'")

        # TopFrame 레벨 Dataset
        tf_ds = result.get("topform_datasets", [])
        if tf_ds:
            print(f"\n--- TopFrame.form 레벨 Datasets ({len(tf_ds)}개) ---")
            for d in tf_ds:
                print(f"  [{d['name']}] rows={d.get('rowCount', 0)}")

        print("\n" + "=" * 70)

        # JSON 파일로 저장
        output_path = PROJECT_ROOT / "data" / "weather_grid_structure.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False, default=str)
        print(f"\n[SAVED] {output_path}")

        return result

    finally:
        analyzer.close()


def main():
    parser = argparse.ArgumentParser(description="BGF TopFrame 날씨 그리드 구조 탐색")
    parser.add_argument("--store-id", default=DEFAULT_STORE_ID, help="매장 코드")
    args = parser.parse_args()

    result = explore_weather_grid(store_id=args.store_id)

    # 핵심 요약
    print("\n" + "=" * 70)
    print("  요약")
    print("=" * 70)

    gwc = result.get("grd_weather_cells", {})
    if gwc.get("found") and gwc.get("columns"):
        print(f"\n  >> grd_weather Dataset: {gwc.get('dataset_name')}")
        print(f"     columns: {gwc.get('columns')}")
        print(f"     rows: {gwc.get('rowCount', 0)}")
        print(f"\n  [ACTION] weather_utils.py의 temp_col/date_col 후보 리스트에")
        print(f"           위 컬럼명을 추가하세요.")
    else:
        w_datasets = result.get("weather_datasets", [])
        if w_datasets:
            print(f"\n  pdiv_weather.form Datasets 발견:")
            for ds in w_datasets:
                cols = [c["id"] for c in ds.get("columns", [])]
                print(f"    [{ds['name']}] rows={ds.get('rowCount', 0)} columns={cols}")
        else:
            print("\n  [INFO] 날씨 Dataset을 찾지 못했습니다.")
            print("         pdiv_weather.form 내부 객체:")
            for o in result.get("weather_form_objects", []):
                print(f"           - {o['name']} ({o['className']})")


if __name__ == "__main__":
    main()
