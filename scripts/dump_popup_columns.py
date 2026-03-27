"""
CallItemDetailPopup 전체 컬럼 덤프 스크립트

팝업의 dsItemDetail, dsItemDetailOrd 데이터셋 컬럼명과 샘플값,
divInfo/divInfo01/divInfo02 UI 컴포넌트를 열거한다.

Usage:
    python scripts/dump_popup_columns.py
    python scripts/dump_popup_columns.py --items 8801043015653,8800279670889,0000088014463
"""

import sys
import os
import time
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.sales_analyzer import SalesAnalyzer
from src.collectors.product_detail_batch_collector import ProductDetailBatchCollector
from src.settings.ui_config import FAIL_REASON_UI
from src.settings.timing import (
    BD_BARCODE_INPUT_WAIT,
    BD_POPUP_CLOSE_WAIT,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 카테고리별 대표 상품 (푸드/음료/담배)
DEFAULT_ITEMS = [
    "8801043015653",  # 농심 육개장사발면 (라면 032)
    "8800279670889",  # 3XL베이컨햄마요김치 (도시락 002)
    "0000088014463",  # 팔리아멘트아쿠아3mg (담배 072)
]


def dump_all_columns(driver, popup_id: str) -> dict:
    """팝업 내 모든 데이터셋 컬럼 + UI 컴포넌트 덤프"""
    return driver.execute_script("""
        var popupId = arguments[0];

        function getPopupForm() {
            try {
                var app = nexacro.getApplication();
                var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                if (wf[popupId] && wf[popupId].form)
                    return wf[popupId].form;
                if (wf.popupframes && wf.popupframes[popupId])
                    return wf.popupframes[popupId].form;
                if (wf.form && wf.form[popupId])
                    return wf.form[popupId].form;
            } catch(e) {}
            return null;
        }

        var popupForm = getPopupForm();
        if (!popupForm) return {error: 'popup not found'};

        var result = {
            datasets: {},
            ui_components: {},
            divs: {}
        };

        // ─── 1. 데이터셋 열거 ───
        // popupForm의 모든 속성 중 Dataset(getColumn 메서드 보유)을 찾음
        var dsNames = [];
        try {
            // 방법1: objects에서 찾기
            if (popupForm.objects) {
                for (var i = 0; i < popupForm.objects.length; i++) {
                    var obj = popupForm.objects[i];
                    if (obj && typeof obj.getColumn === 'function') {
                        dsNames.push(obj.name || obj.id || ('obj_' + i));
                    }
                }
            }
            // 방법2: 직접 속성 탐색
            var knownDs = [
                'dsItemDetail', 'dsItemDetailOrd', 'dsDetail', 'dsOrd',
                'dsItem', 'dsResult', 'dsCategory', 'dsClass',
                'dsMidCategory', 'dsLclCategory', 'dsSclCategory'
            ];
            for (var k = 0; k < knownDs.length; k++) {
                if (popupForm[knownDs[k]] && typeof popupForm[knownDs[k]].getColumn === 'function') {
                    if (dsNames.indexOf(knownDs[k]) === -1) dsNames.push(knownDs[k]);
                }
            }
        } catch(e) {
            result.ds_enum_error = String(e);
        }

        // 각 데이터셋의 컬럼 + 샘플값 추출
        for (var d = 0; d < dsNames.length; d++) {
            var dsName = dsNames[d];
            var ds = popupForm[dsName];
            if (!ds) continue;

            var colInfo = [];
            try {
                var colCount = ds.getColCount ? ds.getColCount() : 0;
                var rowCount = ds.getRowCount ? ds.getRowCount() : 0;

                for (var c = 0; c < colCount; c++) {
                    var colId = ds.getColID ? ds.getColID(c) : null;
                    var colType = ds.getColumnInfo ?
                        (ds.getColumnInfo(c) ? ds.getColumnInfo(c).type : null) : null;
                    var sampleVal = null;
                    if (rowCount > 0 && colId) {
                        try {
                            var v = ds.getColumn(0, colId);
                            if (v && typeof v === 'object' && v.hi !== undefined)
                                sampleVal = String(v.hi);
                            else
                                sampleVal = v != null ? String(v) : null;
                        } catch(e2) {}
                    }
                    colInfo.push({
                        index: c,
                        id: colId,
                        type: colType,
                        sample: sampleVal
                    });
                }

                result.datasets[dsName] = {
                    rowCount: rowCount,
                    colCount: colCount,
                    columns: colInfo
                };
            } catch(e) {
                result.datasets[dsName] = {error: String(e)};
            }
        }

        // ─── 2. div 컴포넌트 열거 ───
        var divNames = [
            'divInfo', 'divInfo01', 'divInfo02', 'divInfo03',
            'divWork', 'divDetail', 'divCategory', 'divClass'
        ];
        for (var dv = 0; dv < divNames.length; dv++) {
            var divName = divNames[dv];
            var div = popupForm[divName];
            if (!div) continue;

            var compList = [];
            try {
                var divForm = div.form || div;
                if (divForm.components || divForm.objects) {
                    var comps = divForm.components || divForm.objects || [];
                    for (var ci = 0; ci < comps.length; ci++) {
                        var comp = comps[ci];
                        var compInfo = {
                            name: comp.name || comp.id || ('comp_' + ci),
                            type: comp.typeName || comp.constructor?.name || 'unknown',
                            text: null,
                            value: null
                        };
                        // text 추출
                        try { compInfo.text = comp.text || null; } catch(e) {}
                        try { compInfo.value = comp.value || null; } catch(e) {}
                        compList.push(compInfo);
                    }
                }
            } catch(e) {
                compList = [{error: String(e)}];
            }
            result.divs[divName] = compList;
        }

        // ─── 3. popupForm 직접 속성 중 st*/edt*/cbo* 컴포넌트 ───
        try {
            var uiComps = [];
            var formComps = popupForm.components || popupForm.objects || [];
            for (var fi = 0; fi < formComps.length; fi++) {
                var fc = formComps[fi];
                var fcName = fc.name || fc.id || '';
                var fcInfo = {
                    name: fcName,
                    type: fc.typeName || 'unknown',
                    text: null,
                    value: null
                };
                try { fcInfo.text = fc.text || null; } catch(e) {}
                try { fcInfo.value = fc.value || null; } catch(e) {}
                uiComps.push(fcInfo);
            }
            result.ui_components = uiComps;
        } catch(e) {
            result.ui_components_error = String(e);
        }

        return result;
    """, popup_id)


def main():
    parser = argparse.ArgumentParser(description="CallItemDetailPopup 컬럼 덤프")
    parser.add_argument(
        "--items", type=str, default=None,
        help="덤프할 상품 바코드 (쉼표 구분)"
    )
    args = parser.parse_args()

    items = args.items.split(",") if args.items else DEFAULT_ITEMS

    # 1. BGF 사이트 로그인 (detail_fetch_wrapper와 동일 플로우)
    from src.settings.timing import SA_LOGIN_WAIT, SA_POPUP_CLOSE_WAIT

    analyzer = SalesAnalyzer()
    analyzer.setup_driver()
    analyzer.connect()
    time.sleep(SA_LOGIN_WAIT)

    if not analyzer.do_login():
        print("로그인 실패")
        return

    time.sleep(SA_POPUP_CLOSE_WAIT * 2)
    try:
        analyzer.close_popup()
    except Exception:
        pass
    time.sleep(SA_POPUP_CLOSE_WAIT)

    popup_id = FAIL_REASON_UI["POPUP_ID"]
    collector = ProductDetailBatchCollector(
        driver=analyzer.driver, store_id=None
    )

    all_results = {}

    for item_cd in items:
        print(f"\n{'='*60}")
        print(f"상품: {item_cd}")
        print(f"{'='*60}")

        # 바코드 입력 → Enter → Quick Search → 팝업
        collector._clear_alerts()
        if not collector._input_barcode(item_cd):
            print(f"  바코드 입력 실패")
            continue

        time.sleep(BD_BARCODE_INPUT_WAIT)

        if not collector._trigger_enter():
            print(f"  Enter 트리거 실패")
            continue

        time.sleep(0.5)
        collector._click_quick_search_item()
        time.sleep(0.5)

        if not collector._wait_for_popup(popup_id):
            alert = collector._clear_alerts()
            print(f"  팝업 미출현 (alert: {alert})")
            continue

        if not collector._wait_for_data_load(popup_id):
            print(f"  데이터 로딩 타임아웃")
            collector._close_popup(popup_id)
            continue

        # 컬럼 덤프
        dump = dump_all_columns(analyzer.driver, popup_id)
        all_results[item_cd] = dump

        # 출력
        if dump.get("error"):
            print(f"  오류: {dump['error']}")
        else:
            for ds_name, ds_info in dump.get("datasets", {}).items():
                if "error" in ds_info:
                    print(f"\n  [{ds_name}] 오류: {ds_info['error']}")
                    continue
                print(f"\n  [{ds_name}] rows={ds_info['rowCount']}, cols={ds_info['colCount']}")
                for col in ds_info.get("columns", []):
                    sample = col.get("sample", "")
                    if sample and len(sample) > 50:
                        sample = sample[:50] + "..."
                    print(f"    {col['index']:3d}. {col['id']:<30} type={col.get('type','?'):<10} sample={sample}")

            for div_name, comps in dump.get("divs", {}).items():
                print(f"\n  [UI: {div_name}]")
                for comp in comps:
                    if "error" in comp:
                        print(f"    오류: {comp['error']}")
                    else:
                        print(f"    {comp['name']:<30} type={comp['type']:<15} text={comp.get('text','')}")

            print(f"\n  [UI: form-level components]")
            for comp in dump.get("ui_components", []):
                if isinstance(comp, dict) and "name" in comp:
                    txt = comp.get('text', '') or ''
                    if len(txt) > 40:
                        txt = txt[:40] + "..."
                    print(f"    {comp['name']:<30} type={comp['type']:<15} text={txt}")

        # 팝업 닫기
        collector._close_popup(popup_id)
        time.sleep(BD_POPUP_CLOSE_WAIT)

    # JSON 저장
    output_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "popup_column_dump.json"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: {output_path}")

    analyzer.close()
    print("완료")


if __name__ == "__main__":
    main()
