"""
폐기 전표 상세 페이지 진입 (fn_moveDetailPage 분석 기반)

핵심 발견:
- gdList_oncelldblclick -> fn_moveDetailPage() 호출
- fn_moveDetailPage()는 gvVar04~08에 전표 정보 설정 후 다른 메뉴(상세 페이지)로 이동
- 팝업이 아니라 새 프레임(탭)으로 상세 페이지 열림

이 스크립트:
1) fn_moveDetailPage 전체 소스코드 확인
2) 실제로 fn_moveDetailPage() 호출
3) 새로 열린 프레임에서 상세 품목 데이터 추출
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
        print(f"\n[1] 통합 전표 조회 이동 + 폐기 필터 + 조회")
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
            wf.dsSearch.setColumn(0, 'strFromDt', '20260218');
            wf.dsSearch.setColumn(0, 'strToDt', '20260219');
        """, FRAME_ID)
        time.sleep(1)

        driver.execute_script("""
            var fid = arguments[0];
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
            if (typeof form.fn_commBtn_10 === 'function') form.fn_commBtn_10();
            else form.div_cmmbtn.form.F_10.click();
        """, FRAME_ID)
        time.sleep(4)

        row_count = driver.execute_script(f"""
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
            return form.div_workForm.form.dsList.getRowCount();
        """)
        print(f"  전표 {row_count}건 조회됨")

        # ============================================================
        # [2] fn_moveDetailPage 전체 소스코드
        # ============================================================
        print(f"\n[2] fn_moveDetailPage 전체 소스코드")

        source = driver.execute_script(f"""
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
            var wf = form.div_workForm.form;
            if (typeof wf.fn_moveDetailPage === 'function') {{
                return wf.fn_moveDetailPage.toString();
            }}
            return 'NOT FOUND';
        """)
        print(f"  {source}")

        # ============================================================
        # [3] 현재 FrameSet 키 목록 (이동 전)
        # ============================================================
        before_frames = driver.execute_script("""
            var fs = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet;
            var keys = [];
            for (var key in fs) {
                if (fs[key] && key.match(/^[A-Z]/)) keys.push(key);
            }
            return keys;
        """)
        print(f"\n[3] 이동 전 FrameSet: {before_frames}")

        # ============================================================
        # [4] 첫 번째 전표에 대해 fn_moveDetailPage() 직접 호출
        # ============================================================
        print(f"\n[4] fn_moveDetailPage() 호출")

        move_result = driver.execute_script(f"""
            try {{
                var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
                var wf = form.div_workForm.form;

                // rowposition = 0 (첫 번째 전표)
                wf.dsList.set_rowposition(0);

                // 현재 행 정보
                var nRow = wf.dsList.rowposition;
                var info = {{
                    CHIT_ID: String(wf.dsList.getColumn(nRow, 'CHIT_ID') || ''),
                    CHIT_ID_NM: String(wf.dsList.getColumn(nRow, 'CHIT_ID_NM') || ''),
                    CHIT_NO: String(wf.dsList.getColumn(nRow, 'CHIT_NO') || ''),
                    CHIT_YMD: String(wf.dsList.getColumn(nRow, 'CHIT_YMD') || ''),
                    CENTER_CD: String(wf.dsList.getColumn(nRow, 'CENTER_CD') || ''),
                    CENTER_NM: String(wf.dsList.getColumn(nRow, 'CENTER_NM') || '')
                }};

                // fn_moveDetailPage 호출
                wf.fn_moveDetailPage();

                return {{success: true, info: info}};
            }} catch(e) {{
                return {{error: e.message, stack: e.stack ? e.stack.substring(0, 500) : ''}};
            }}
        """)
        print(f"  결과: {json.dumps(move_result, ensure_ascii=False, default=str)}")

        # 상세 페이지 로딩 대기
        time.sleep(5)

        # 스크린샷
        driver.save_screenshot(str(PROJECT_ROOT / "data" / "waste_detail_page.png"))
        print(f"  스크린샷: data/waste_detail_page.png")

        # ============================================================
        # [5] 이동 후 FrameSet 키 확인 (새 프레임?)
        # ============================================================
        after_frames = driver.execute_script("""
            var fs = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet;
            var keys = [];
            for (var key in fs) {
                if (fs[key] && key.match(/^[A-Z]/)) keys.push(key);
            }
            return keys;
        """)
        print(f"\n[5] 이동 후 FrameSet: {after_frames}")

        # 새로 추가된 프레임 찾기
        new_frames = [f for f in after_frames if f not in before_frames]
        print(f"  새 프레임: {new_frames}")

        # ============================================================
        # [6] 새 프레임 또는 기존 프레임의 상세 데이터 탐색
        # ============================================================
        print(f"\n[6] 상세 데이터 탐색")

        # 모든 프레임의 데이터셋 전수 조사
        detail_data = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var fs = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
                var result = {};

                for (var fkey in fs) {
                    if (!fs[fkey] || !fs[fkey].form || !fkey.match(/^[A-Z]/)) continue;
                    var form = fs[fkey].form;

                    function searchDs(obj, prefix, depth) {
                        if (depth > 5) return;
                        for (var key in obj) {
                            try {
                                var item = obj[key];
                                if (!item || typeof item !== 'object') continue;

                                if (item.getRowCount && item.getColumn) {
                                    var rc = item.getRowCount();
                                    var cols = [];
                                    try {
                                        var cc = item.getColCount ? item.getColCount() : 0;
                                        for (var c = 0; c < cc; c++) cols.push(item.getColID(c));
                                    } catch(e) {}

                                    var hasItem = false;
                                    for (var c = 0; c < cols.length; c++) {
                                        if (cols[c].indexOf('ITEM') >= 0 || cols[c].indexOf('PLU') >= 0 ||
                                            cols[c].indexOf('item') >= 0 || cols[c].indexOf('BARCODE') >= 0) {
                                            hasItem = true;
                                            break;
                                        }
                                    }

                                    var dsKey = fkey + '.' + prefix + '.' + key;
                                    if (hasItem || rc > 0) {
                                        var samples = [];
                                        for (var r = 0; r < Math.min(rc, 30); r++) {
                                            var row = {};
                                            for (var c = 0; c < Math.min(cols.length, 30); c++) {
                                                var val = item.getColumn(r, cols[c]);
                                                if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                                                row[cols[c]] = val;
                                            }
                                            samples.push(row);
                                        }
                                        result[dsKey] = {rows: rc, cols: cols, hasItem: hasItem, samples: samples};
                                    }
                                }

                                if (item.form && (key.startsWith('div') || key.startsWith('Div') ||
                                    key.startsWith('tab') || key.startsWith('Tab'))) {
                                    searchDs(item.form, prefix + '.' + key + '.form', depth + 1);
                                }
                            } catch(e) {}
                        }
                    }

                    searchDs(form, 'form', 0);
                    if (form.div_workForm && form.div_workForm.form) {
                        searchDs(form.div_workForm.form, 'wf', 0);
                    }
                }
                return result;
            } catch(e) {
                return {error: e.message};
            }
        """)

        if detail_data and not detail_data.get("error"):
            # ITEM 관련 데이터셋 우선 출력
            item_datasets = {k: v for k, v in detail_data.items() if v.get("hasItem")}
            other_datasets = {k: v for k, v in detail_data.items() if not v.get("hasItem")}

            if item_datasets:
                print(f"\n  *** ITEM 데이터셋 ({len(item_datasets)}개) ***")
                for dsKey, dsInfo in sorted(item_datasets.items()):
                    print(f"\n  {dsKey}: rows={dsInfo['rows']}")
                    print(f"    cols: {dsInfo['cols']}")
                    if dsInfo.get("samples"):
                        for i, s in enumerate(dsInfo["samples"]):
                            print(f"    [{i}] {json.dumps(s, ensure_ascii=False, default=str)[:400]}")
            else:
                print(f"\n  ITEM 데이터셋 없음")

            print(f"\n  기타 데이터셋 ({len(other_datasets)}개):")
            for dsKey, dsInfo in sorted(other_datasets.items()):
                cols_str = str(dsInfo['cols'][:15])
                if len(dsInfo['cols']) > 15:
                    cols_str += '...'
                print(f"    {dsKey}: rows={dsInfo['rows']} cols={cols_str}")
                # 새 프레임의 데이터셋은 샘플도 출력
                if new_frames and any(nf in dsKey for nf in new_frames):
                    for i, s in enumerate(dsInfo.get("samples", [])[:3]):
                        print(f"      [{i}] {json.dumps(s, ensure_ascii=False, default=str)[:300]}")
        else:
            print(f"  오류: {detail_data}")

        # ============================================================
        # [7] dsGs 값 확인 (상세 페이지 파라미터)
        # ============================================================
        print(f"\n\n[7] dsGs 파라미터 확인 (모든 프레임)")

        dsgs_all = driver.execute_script("""
            try {
                var fs = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet;
                var result = {};
                for (var fkey in fs) {
                    if (!fs[fkey] || !fs[fkey].form || !fkey.match(/^[A-Z]/)) continue;
                    var form = fs[fkey].form;

                    // form 레벨 dsGs
                    if (form.dsGs && form.dsGs.getRowCount && form.dsGs.getRowCount() > 0) {
                        var row = {};
                        var cc = form.dsGs.getColCount();
                        for (var c = 0; c < cc; c++) {
                            var colId = form.dsGs.getColID(c);
                            var val = form.dsGs.getColumn(0, colId);
                            row[colId] = val !== null && val !== undefined ? String(val) : null;
                        }
                        result[fkey + '.form.dsGs'] = row;
                    }

                    // div_workForm 레벨 dsGs
                    if (form.div_workForm && form.div_workForm.form) {
                        var wf = form.div_workForm.form;
                        if (wf.dsGs && wf.dsGs.getRowCount && wf.dsGs.getRowCount() > 0) {
                            var row = {};
                            var cc = wf.dsGs.getColCount();
                            for (var c = 0; c < cc; c++) {
                                var colId = wf.dsGs.getColID(c);
                                var val = wf.dsGs.getColumn(0, colId);
                                row[colId] = val !== null && val !== undefined ? String(val) : null;
                            }
                            result[fkey + '.wf.dsGs'] = row;
                        }
                    }
                }
                return result;
            } catch(e) {
                return {error: e.message};
            }
        """)

        for key, val in (dsgs_all or {}).items():
            print(f"  {key}: {json.dumps(val, ensure_ascii=False, default=str)}")

        # ============================================================
        # [8] gvVar 값 확인
        # ============================================================
        print(f"\n[8] gvVar 값 확인")
        gvvars = driver.execute_script(f"""
            try {{
                var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
                var wf = form.div_workForm.form;
                var result = {{}};
                for (var i = 1; i <= 20; i++) {{
                    var key = 'gvVar' + (i < 10 ? '0' + i : i);
                    if (wf[key] !== undefined && wf[key] !== null) {{
                        result[key] = String(wf[key]);
                    }}
                }}
                return result;
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)
        for key, val in (gvvars or {}).items():
            print(f"  {key}: {val}")

        # ============================================================
        # [9] 탭 목록 확인
        # ============================================================
        print(f"\n[9] MDI 탭 확인")
        tab_info = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var mf = app.mainframe;
                var result = {};

                // afrm_MdiFrame (탭 관리)
                if (typeof afrm_MdiFrame !== 'undefined' && afrm_MdiFrame && afrm_MdiFrame.form) {
                    var mdiForm = afrm_MdiFrame.form;
                    // 열린 탭 목록
                    var tabList = [];
                    if (typeof mdiForm.gfn_formAddList !== 'undefined') {
                        result.hasMdiFormAddList = true;
                    }

                    // 탭 관련 데이터셋/객체
                    for (var key in mdiForm) {
                        if (mdiForm[key] && typeof mdiForm[key] === 'object') {
                            var type = mdiForm[key]._type_name || '';
                            if (type === 'Tab' || type === 'TabPage' ||
                                key.indexOf('tab') >= 0 || key.indexOf('Tab') >= 0) {
                                tabList.push({key: key, type: type});
                            }
                        }
                    }
                    result.mdi_tabs = tabList;
                }

                // 탭 DOM 요소
                var tabElements = document.querySelectorAll('[id*="tab_openList"]');
                var tabs = [];
                for (var i = 0; i < tabElements.length; i++) {
                    var r = tabElements[i].getBoundingClientRect();
                    tabs.push({
                        id: tabElements[i].id,
                        text: tabElements[i].textContent ? tabElements[i].textContent.trim().substring(0, 30) : '',
                        w: Math.round(r.width),
                        h: Math.round(r.height),
                        visible: r.width > 0
                    });
                }
                result.tab_dom = tabs;

                return result;
            } catch(e) {
                return {error: e.message};
            }
        """)
        print(f"  {json.dumps(tab_info, ensure_ascii=False, default=str)}")

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
