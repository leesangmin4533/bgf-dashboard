"""
폐기 전표 상세 품목 추출 (STGJ020_P1 팝업 기반)

발견된 구조:
- fn_moveDetailPage()에서 CHIT_ID="04"(폐기)는 STGJ020_P1 팝업 호출
- gfn_openPopup("STGJ020_P1", "GJ::STGJ020_P1.xfdl", oArg, ...)
- 팝업 내부에서 전표 상세 품목(ITEM_CD, ITEM_NM 등) 로딩
- Selenium에서는 모달 팝업이 안 열릴 수 있으므로, 팝업 로딩 후 데이터 추출

전략:
1) fn_moveDetailPage() 호출 (dsGs 파라미터 설정)
2) gfn_openPopup 직접 호출하여 팝업 열기
3) 팝업 프레임에서 데이터셋 탐색
4) 또는 팝업 내부 서버 트랜잭션을 직접 호출하여 데이터 추출
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
FROM_DATE = "20260218"
TO_DATE = "20260219"


def main():
    analyzer = SalesAnalyzer(store_id=DEFAULT_STORE_ID)

    try:
        # 로그인
        print("[0] 로그인...")
        analyzer.setup_driver()
        analyzer.connect()
        if not analyzer.do_login():
            print("[ERROR] 로그인 실패")
            return
        analyzer.close_popup()
        time.sleep(2)
        print("[OK] 로그인 성공")

        driver = analyzer.driver

        # 메뉴 이동 + 필터 + 조회
        print(f"\n[1] 통합 전표 조회 + 폐기 필터 + 조회")
        navigate_menu(driver, "검수전표", "통합 전표 조회", FRAME_ID)
        time.sleep(2)

        driver.execute_script("""
            var fid = arguments[0];
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
            var wf = form.div_workForm.form;
            var ds = wf.dsChitDiv;
            for (var r = 0; r < ds.getRowCount(); r++) {
                if (ds.getColumn(r, 'CODE') === '10') {
                    wf.div2.form.divSearch.form.cbChitDiv.set_index(r);
                    break;
                }
            }
            wf.dsSearch.setColumn(0, 'strChitDiv', '10');
            wf.dsSearch.setColumn(0, 'strFromDt', arguments[1]);
            wf.dsSearch.setColumn(0, 'strToDt', arguments[2]);
        """, FRAME_ID, FROM_DATE, TO_DATE)
        time.sleep(1)

        driver.execute_script("""
            var fid = arguments[0];
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
            if (typeof form.fn_commBtn_10 === 'function') form.fn_commBtn_10();
            else form.div_cmmbtn.form.F_10.click();
        """, FRAME_ID)
        time.sleep(4)

        # 전표 목록
        slip_list = driver.execute_script(f"""
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
            var ds = form.div_workForm.form.dsList;
            var rows = [];
            for (var r = 0; r < ds.getRowCount(); r++) {{
                var row = {{}};
                var cols = ['CHIT_YMD','CHIT_NO','CHIT_ID','CHIT_ID_NM','CHIT_ID_NO',
                            'ITEM_CNT','CENTER_CD','CENTER_NM','WONGA_AMT','MAEGA_AMT',
                            'NAP_PLAN_YMD','CHIT_FLAG','RET_CHIT_NO','MAEIP_CHIT_NO',
                            'LARGE_CD','LSTORE_NM','RSTORE_NM'];
                for (var c = 0; c < cols.length; c++) {{
                    var val = ds.getColumn(r, cols[c]);
                    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                    row[cols[c]] = val;
                }}
                rows.push(row);
            }}
            return rows;
        """)
        print(f"  전표 {len(slip_list)}건")

        # ============================================================
        # 각 전표에 대해 팝업 열기 + 품목 추출
        # ============================================================
        all_items = []

        for idx in range(len(slip_list)):
            slip = slip_list[idx]
            ymd = slip.get("CHIT_YMD", "")
            date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}" if len(ymd) == 8 else ymd
            chit_no = slip.get("CHIT_NO", "")
            chit_id = slip.get("CHIT_ID", "")
            item_cnt = slip.get("ITEM_CNT", 0) or 0

            print(f"\n{'='*70}")
            print(f"  [{idx}] {date_str} 전표={chit_no} (CHIT_ID={chit_id}) 품목={item_cnt}건")
            print(f"{'='*70}")

            # rowposition 설정 + dsGs 파라미터 설정 + 팝업 호출
            popup_result = driver.execute_script(f"""
                try {{
                    var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
                    var wf = form.div_workForm.form;

                    // rowposition 설정
                    wf.dsList.set_rowposition({idx});

                    // fn_moveDetailPage에서 하는 것처럼 gvVar 설정
                    var nRow = {idx};
                    wf.gvVar04 = wf.dsList.getColumn(nRow, "CHIT_ID");
                    wf.gvVar05 = wf.dsList.getColumn(nRow, "CHIT_ID_NM");
                    wf.gvVar06 = wf.dsList.getColumn(nRow, "NAP_PLAN_YMD");
                    wf.gvVar07 = wf.dsList.getColumn(nRow, "CHIT_NO");
                    wf.gvVar08 = wf.dsList.getColumn(nRow, "CENTER_NM");
                    wf.gvVar09 = wf.dsList.getColumn(nRow, "CHIT_ID_NO");
                    wf.gvVar10 = wf.dsList.getColumn(nRow, "LSTORE_NM") || '';
                    wf.gvVar11 = wf.dsList.getColumn(nRow, "RSTORE_NM") || '';
                    wf.gvVar12 = wf.dsList.getColumn(nRow, "LARGE_CD") || '';
                    wf.gvVar13 = wf.dsList.getColumn(nRow, "CHIT_FLAG");
                    wf.gvVar14 = wf.dsList.getColumn(nRow, "RET_CHIT_NO") || '';
                    wf.gvVar15 = wf.dsList.getColumn(nRow, "MAEIP_CHIT_NO") || '';
                    wf.gvVar18 = wf.dsList.getColumn(nRow, "CHIT_YMD");

                    // dsGs 파라미터 설정
                    wf.dsGs.setColumn(0, "gvVar04", wf.gvVar04);
                    wf.dsGs.setColumn(0, "gvVar05", wf.gvVar05);
                    wf.dsGs.setColumn(0, "gvVar06", wf.gvVar06);
                    wf.dsGs.setColumn(0, "gvVar07", wf.gvVar07);
                    wf.dsGs.setColumn(0, "gvVar08", wf.gvVar08);
                    wf.dsGs.setColumn(0, "gvVar09", wf.gvVar09);
                    wf.dsGs.setColumn(0, "gvVar10", wf.gvVar10);
                    wf.dsGs.setColumn(0, "gvVar11", wf.gvVar11);
                    wf.dsGs.setColumn(0, "gvVar12", wf.gvVar12);
                    wf.dsGs.setColumn(0, "gvVar13", wf.gvVar13);
                    wf.dsGs.setColumn(0, "gvVar14", wf.gvVar14);
                    wf.dsGs.setColumn(0, "gvVar15", wf.gvVar15);
                    wf.dsGs.setColumn(0, "gvVar18", wf.gvVar18);
                    wf.gvVar20 = "Y";

                    // 팝업 호출 (CHIT_ID=04는 STGJ020_P1)
                    var popupId = wf.gvVar04 === "09" || wf.gvVar04 === "10" ? "STGJ020_P2" : "STGJ020_P1";
                    var popupUrl = wf.gvVar04 === "09" || wf.gvVar04 === "10" ? "GJ::STGJ020_P2.xfdl" : "GJ::STGJ020_P1.xfdl";

                    var oArg = {{}};
                    oArg.dsArg = wf.dsGs;
                    oArg.strStoreCd = wf.strStoreCd;
                    oArg.strStoreNm = wf.strStoreNm;

                    wf.gfn_openPopup(popupId, popupUrl, oArg, "fn_popupCallback", {{}});

                    return {{success: true, popupId: popupId, chitId: String(wf.gvVar04)}};
                }} catch(e) {{
                    return {{error: e.message, stack: e.stack ? e.stack.substring(0, 300) : ''}};
                }}
            """)

            print(f"  팝업 호출: {json.dumps(popup_result, ensure_ascii=False, default=str)}")
            time.sleep(5)  # 팝업 로딩 + 서버 트랜잭션 대기

            # 팝업 프레임 / 데이터 탐색
            popup_data = driver.execute_script(f"""
                try {{
                    var app = nexacro.getApplication();
                    var result = {{}};

                    // 1) app._popupframes에서 팝업 찾기
                    var popupFrame = null;
                    var popupKey = null;
                    if (app._popupframes) {{
                        for (var key in app._popupframes) {{
                            popupFrame = app._popupframes[key];
                            popupKey = key;
                        }}
                    }}

                    // 2) nexacro._popupframes 확인
                    if (!popupFrame && typeof nexacro !== 'undefined') {{
                        var pfs = nexacro._popupframes || nexacro.getPopupFrames() || [];
                        if (pfs.length > 0) {{
                            popupFrame = pfs[pfs.length - 1];
                            popupKey = 'nexacro_last';
                        }}
                    }}

                    // 3) mainframe 직계 자식에서 ChildFrame 찾기
                    if (!popupFrame) {{
                        var mf = app.mainframe;
                        for (var key in mf) {{
                            try {{
                                if (mf[key] && typeof mf[key] === 'object' &&
                                    mf[key]._type_name === 'ChildFrame' &&
                                    key.indexOf('STGJ020') >= 0) {{
                                    popupFrame = mf[key];
                                    popupKey = 'mf.' + key;
                                }}
                            }} catch(e) {{}}
                        }}
                    }}

                    // 4) 전역 변수로 팝업 찾기
                    if (!popupFrame) {{
                        try {{
                            var win = app.mainframe._getWindow();
                            if (win && win._modal_frame_stack && win._modal_frame_stack.length > 0) {{
                                var modalInfo = win._modal_frame_stack[win._modal_frame_stack.length - 1];
                                if (modalInfo) {{
                                    popupFrame = modalInfo.frame || modalInfo;
                                    popupKey = 'modal_stack';
                                }}
                            }}
                        }} catch(e) {{}}
                    }}

                    result.popupKey = popupKey;

                    if (popupFrame && popupFrame.form) {{
                        result.found = true;

                        // 팝업 form 내 모든 데이터셋 탐색
                        var datasets = {{}};
                        function searchInForm(obj, prefix, depth) {{
                            if (depth > 5) return;
                            for (var key in obj) {{
                                try {{
                                    var item = obj[key];
                                    if (!item || typeof item !== 'object') continue;

                                    if (item.getRowCount && item.getColumn) {{
                                        var rc = item.getRowCount();
                                        var cols = [];
                                        try {{
                                            var cc = item.getColCount ? item.getColCount() : 0;
                                            for (var c = 0; c < cc; c++) cols.push(item.getColID(c));
                                        }} catch(e) {{}}

                                        if (rc > 0 || cols.length > 0) {{
                                            var samples = [];
                                            for (var r = 0; r < Math.min(rc, 50); r++) {{
                                                var row = {{}};
                                                for (var c = 0; c < cols.length; c++) {{
                                                    var val = item.getColumn(r, cols[c]);
                                                    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                                                    row[cols[c]] = val;
                                                }}
                                                samples.push(row);
                                            }}
                                            datasets[prefix + '.' + key] = {{rows: rc, cols: cols, samples: samples}};
                                        }}
                                    }}

                                    if (item.form && (key.startsWith('div') || key.startsWith('Div') ||
                                        key.startsWith('tab') || key.startsWith('Tab'))) {{
                                        searchInForm(item.form, prefix + '.' + key + '.form', depth + 1);
                                    }}
                                }} catch(e) {{}}
                            }}
                        }}

                        searchInForm(popupFrame.form, 'popup.form', 0);
                        result.datasets = datasets;
                    }} else {{
                        result.found = false;

                        // 대안: DOM에서 팝업 div 찾기
                        var popupDivs = document.querySelectorAll('div[id*="STGJ020_P"]');
                        var divList = [];
                        for (var i = 0; i < popupDivs.length; i++) {{
                            var r = popupDivs[i].getBoundingClientRect();
                            divList.push({{id: popupDivs[i].id, w: r.width, h: r.height}});
                        }}
                        result.popup_divs = divList;

                        // 대안2: 모든 큰 visible div 중 새로운 것
                        var allDivs = document.querySelectorAll('div');
                        var newLargeDivs = [];
                        for (var i = 0; i < allDivs.length; i++) {{
                            var r = allDivs[i].getBoundingClientRect();
                            if (r.width > 400 && r.height > 300 && r.x >= 0 && r.y >= 0) {{
                                var style = window.getComputedStyle(allDivs[i]);
                                var z = parseInt(style.zIndex) || 0;
                                if (z > 500) {{
                                    newLargeDivs.push({{
                                        id: allDivs[i].id,
                                        w: Math.round(r.width), h: Math.round(r.height),
                                        x: Math.round(r.x), y: Math.round(r.y), z: z
                                    }});
                                }}
                            }}
                        }}
                        result.high_z_divs = newLargeDivs;
                    }}

                    return result;
                }} catch(e) {{
                    return {{error: e.message, stack: e.stack ? e.stack.substring(0, 300) : ''}};
                }}
            """)

            if popup_data.get("error"):
                print(f"  팝업 탐색 오류: {popup_data['error']}")
            elif popup_data.get("found"):
                print(f"  팝업 발견! key={popup_data.get('popupKey')}")
                datasets = popup_data.get("datasets", {})
                for dsKey, dsInfo in sorted(datasets.items()):
                    is_item = any('ITEM' in c or 'PLU' in c or 'BARCODE' in c for c in dsInfo.get('cols', []))
                    marker = " ***" if is_item else ""
                    print(f"\n  {marker} {dsKey}: rows={dsInfo['rows']}")
                    print(f"    cols: {dsInfo['cols']}")
                    if dsInfo.get("samples"):
                        for si, s in enumerate(dsInfo["samples"]):
                            print(f"    [{si}] {json.dumps(s, ensure_ascii=False, default=str)[:400]}")
                            if is_item:
                                all_items.append({
                                    "slip_date": date_str,
                                    "slip_no": chit_no,
                                    **s
                                })
            else:
                print(f"  팝업 미발견")
                if popup_data.get("popup_divs"):
                    print(f"  STGJ020_P div: {popup_data['popup_divs']}")
                if popup_data.get("high_z_divs"):
                    print(f"  높은 z-index div: {popup_data['high_z_divs']}")

            # 팝업 닫기
            driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    // 모달 팝업 닫기
                    var win = app.mainframe._getWindow();
                    if (win && win._modal_frame_stack && win._modal_frame_stack.length > 0) {
                        var modalInfo = win._modal_frame_stack[win._modal_frame_stack.length - 1];
                        if (modalInfo && modalInfo.frame && modalInfo.frame.close) {
                            modalInfo.frame.close();
                        }
                    }
                    // app._popupframes 닫기
                    if (app._popupframes) {
                        for (var key in app._popupframes) {
                            try { app._popupframes[key].close(); } catch(e) {}
                        }
                    }
                    // DOM 팝업 제거
                    var popupDivs = document.querySelectorAll('div[id*="STGJ020_P"]');
                    for (var i = 0; i < popupDivs.length; i++) {
                        try { popupDivs[i].parentNode.removeChild(popupDivs[i]); } catch(e) {}
                    }
                } catch(e) {}
            """)
            time.sleep(1)

        # ============================================================
        # 최종 결과
        # ============================================================
        print(f"\n\n{'='*80}")
        print(f"  최종 결과: 폐기 전표 상세 품목")
        print(f"{'='*80}")

        if all_items:
            for item in all_items:
                print(f"  {json.dumps(item, ensure_ascii=False, default=str)}")
        else:
            print(f"  상세 품목 데이터를 추출하지 못함")
            print(f"\n  대안: 스크린샷 확인 -> data/waste_detail_popup_final.png")

        # 스크린샷
        driver.save_screenshot(str(PROJECT_ROOT / "data" / "waste_detail_popup_final.png"))

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()

    finally:
        try:
            if analyzer and analyzer.driver:
                analyzer.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
