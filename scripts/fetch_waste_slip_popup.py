"""
폐기 전표 더블클릭 -> 팝업 구조 탐색

이전 탐색에서 프레임/탭만 찾았으나, 실제로는 팝업(Popup/Dialog)이 열림.
넥사크로 팝업 패턴:
  - form 하위 popup/dialog 객체
  - mainframe 하위 ChildFrame
  - DOM에서 popup 관련 div
"""

import sys
import io
import json
import time
from pathlib import Path
from selenium.webdriver.common.action_chains import ActionChains

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

        # 전표 목록 확인
        row_count = driver.execute_script(f"""
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
            return form.div_workForm.form.dsList.getRowCount();
        """)
        print(f"  전표 {row_count}건 조회됨")

        # ============================================================
        # 더블클릭 전 상태 스냅샷
        # ============================================================
        print(f"\n[2] 더블클릭 전 상태 스냅샷")

        before_state = driver.execute_script("""
            try {
                var result = {};
                var app = nexacro.getApplication();
                var mf = app.mainframe;

                // 1) mainframe 직계 자식 (ChildFrame, popup 등)
                var mfChildren = [];
                for (var key in mf) {
                    if (mf[key] && typeof mf[key] === 'object' && key !== 'all') {
                        var type = mf[key]._type_name || mf[key].constructor?.name || typeof mf[key];
                        if (type !== 'string' && type !== 'number' && type !== 'boolean' && type !== 'function') {
                            mfChildren.push({key: key, type: type, visible: mf[key].visible});
                        }
                    }
                }
                result.mainframe_children = mfChildren;

                // 2) STGJ020_M0 프레임 직계 자식
                var frame = mf.HFrameSet00.VFrameSet00.FrameSet[arguments[0]];
                var frameChildren = [];
                for (var key in frame) {
                    if (frame[key] && typeof frame[key] === 'object' && key !== 'all' && key !== 'form') {
                        var type = frame[key]._type_name || '';
                        if (type) frameChildren.push({key: key, type: type});
                    }
                }
                result.frame_children = frameChildren;

                // 3) form 하위 popup/dialog 관련
                var form = frame.form;
                var formPopups = [];
                for (var key in form) {
                    if (form[key] && typeof form[key] === 'object') {
                        var type = form[key]._type_name || '';
                        if (type && (type.indexOf('opup') >= 0 || type.indexOf('ialog') >= 0 ||
                                     key.indexOf('opup') >= 0 || key.indexOf('ialog') >= 0 ||
                                     key.indexOf('Popup') >= 0 || key.indexOf('Dialog') >= 0 ||
                                     key.indexOf('Call') >= 0 || key.indexOf('Detail') >= 0)) {
                            formPopups.push({key: key, type: type, visible: form[key].visible});
                        }
                    }
                }
                result.form_popups = formPopups;

                // 4) DOM에서 popup 관련 요소
                var domPopups = [];
                var els = document.querySelectorAll('[id*="Popup"], [id*="popup"], [id*="Dialog"], [id*="dialog"], [id*="Detail"], [id*="detail"]');
                for (var i = 0; i < Math.min(els.length, 30); i++) {
                    var r = els[i].getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        domPopups.push({id: els[i].id, w: r.width, h: r.height, visible: r.width > 10});
                    }
                }
                result.dom_popups = domPopups;

                return result;
            } catch(e) {
                return {error: e.message};
            }
        """, FRAME_ID)

        print(f"  mainframe 자식: {len(before_state.get('mainframe_children', []))}개")
        print(f"  frame 자식: {len(before_state.get('frame_children', []))}개")
        print(f"  form 팝업: {before_state.get('form_popups', [])}")
        print(f"  DOM 팝업: {len(before_state.get('dom_popups', []))}개")

        # ============================================================
        # 첫 번째 전표 행 더블클릭 (ActionChains)
        # ============================================================
        print(f"\n[3] 첫 번째 전표 행 더블클릭")

        # rowposition 설정
        driver.execute_script(f"""
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
            form.div_workForm.form.dsList.set_rowposition(0);
        """)
        time.sleep(0.5)

        # 셀 좌표 획득
        cell_info = driver.execute_script(f"""
            try {{
                var cells = document.querySelectorAll('[id*="{FRAME_ID}"][id*="gdList"][id*="body"] div[id*="cell_0_"]');
                var result = [];
                for (var i = 0; i < cells.length; i++) {{
                    var r = cells[i].getBoundingClientRect();
                    if (r.width > 50 && r.height > 15 && r.height < 40) {{
                        result.push({{id: cells[i].id, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)}});
                    }}
                }}
                return result;
            }} catch(e) {{
                return [{{error: e.message}}];
            }}
        """)

        if cell_info and len(cell_info) > 0 and not cell_info[0].get("error"):
            target = cell_info[0]
            print(f"  셀: {target['id']} at ({target['x']}, {target['y']})")

            body = driver.find_element("tag name", "body")
            ActionChains(driver).move_to_element_with_offset(
                body, target["x"], target["y"]
            ).double_click().perform()
            print("  더블클릭 실행!")
        else:
            print(f"  셀 못찾음, JS 이벤트 대안 사용")
            driver.execute_script(f"""
                var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
                var wf = form.div_workForm.form;
                var grid = wf.div2.form.gdList;
                if (grid && grid.oncelldblclick) {{
                    grid.oncelldblclick._fireEvent(grid, {{cell:0, col:5, row:0, clickitem:'body'}});
                }}
            """)

        time.sleep(5)  # 팝업 로딩 대기

        # ============================================================
        # 더블클릭 후 팝업 탐색
        # ============================================================
        print(f"\n[4] 더블클릭 후 상태 탐색")

        after_state = driver.execute_script("""
            try {
                var result = {};
                var app = nexacro.getApplication();
                var mf = app.mainframe;

                // 1) mainframe 직계 자식 변화
                var mfChildren = [];
                for (var key in mf) {
                    if (mf[key] && typeof mf[key] === 'object' && key !== 'all') {
                        var type = mf[key]._type_name || '';
                        if (type) mfChildren.push({key: key, type: type, visible: mf[key].visible});
                    }
                }
                result.mainframe_children = mfChildren;

                // 2) STGJ020_M0 하위 전체 탐색 (팝업 ChildFrame 등)
                var frame = mf.HFrameSet00.VFrameSet00.FrameSet[arguments[0]];
                var form = frame.form;
                var allFormChildren = [];
                for (var key in form) {
                    if (form[key] && typeof form[key] === 'object') {
                        var type = form[key]._type_name || '';
                        if (type) {
                            var info = {key: key, type: type};
                            if (form[key].visible !== undefined) info.visible = form[key].visible;
                            if (form[key].form) {
                                // 하위 form 있으면 데이터셋 검색
                                var subDs = [];
                                for (var sk in form[key].form) {
                                    if (form[key].form[sk] && form[key].form[sk].getRowCount) {
                                        subDs.push({name: sk, rows: form[key].form[sk].getRowCount()});
                                    }
                                }
                                if (subDs.length > 0) info.datasets = subDs;
                            }
                            allFormChildren.push(info);
                        }
                    }
                }
                result.form_all_children = allFormChildren;

                // 3) DOM 팝업/다이얼로그 검색 (넓은 범위)
                var domPopups = [];
                var selectors = [
                    '[id*="Popup"]', '[id*="popup"]',
                    '[id*="Dialog"]', '[id*="dialog"]',
                    '[id*="Detail"]', '[id*="detail"]',
                    '[id*="Call"]',
                    '[id*="STGJ020"][id*="Chit"]',
                    '[class*="popup"]', '[class*="dialog"]',
                    '[id*="modal"]', '[id*="Modal"]'
                ];
                var seen = {};
                for (var s = 0; s < selectors.length; s++) {
                    var els = document.querySelectorAll(selectors[s]);
                    for (var i = 0; i < els.length; i++) {
                        var r = els[i].getBoundingClientRect();
                        if (r.width > 50 && r.height > 50 && !seen[els[i].id]) {
                            seen[els[i].id] = true;
                            domPopups.push({
                                id: els[i].id,
                                w: Math.round(r.width),
                                h: Math.round(r.height),
                                x: Math.round(r.x),
                                y: Math.round(r.y),
                                tag: els[i].tagName
                            });
                        }
                    }
                }
                result.dom_popups = domPopups;

                // 4) 새로 나타난 iframe 확인
                var iframes = document.querySelectorAll('iframe');
                var iframeList = [];
                for (var i = 0; i < iframes.length; i++) {
                    var r = iframes[i].getBoundingClientRect();
                    if (r.width > 50) {
                        iframeList.push({id: iframes[i].id, src: iframes[i].src, w: r.width, h: r.height});
                    }
                }
                result.iframes = iframeList;

                // 5) 넥사크로 열린 팝업 목록 (앱 레벨)
                var openPopups = [];
                if (app._popupframes) {
                    for (var key in app._popupframes) {
                        openPopups.push({key: key, type: app._popupframes[key]._type_name || ''});
                    }
                }
                result.app_popups = openPopups;

                // 6) frame 하위 ChildFrame/popup
                var framePopups = [];
                for (var key in frame) {
                    if (frame[key] && typeof frame[key] === 'object' && key !== 'form' && key !== 'all') {
                        var type = frame[key]._type_name || '';
                        if (type) framePopups.push({key: key, type: type, visible: frame[key].visible});
                    }
                }
                result.frame_popups = framePopups;

                // 7) FrameSet 하위 새 프레임
                var fs = mf.HFrameSet00.VFrameSet00.FrameSet;
                var fsKeys = [];
                for (var key in fs) {
                    if (fs[key] && fs[key].form && key.match(/^[A-Z]/)) {
                        fsKeys.push(key);
                    }
                }
                result.frameset_keys = fsKeys;

                return result;
            } catch(e) {
                return {error: e.message};
            }
        """, FRAME_ID)

        if after_state.get("error"):
            print(f"  오류: {after_state['error']}")
        else:
            print(f"\n  --- mainframe 자식 ---")
            for c in after_state.get("mainframe_children", []):
                print(f"    {c['key']}: {c['type']} visible={c.get('visible')}")

            print(f"\n  --- form 자식 (STGJ020_M0) ---")
            for c in after_state.get("form_all_children", []):
                ds_info = f" datasets={c['datasets']}" if c.get('datasets') else ""
                print(f"    {c['key']}: {c['type']} visible={c.get('visible')}{ds_info}")

            print(f"\n  --- frame 팝업 ---")
            for c in after_state.get("frame_popups", []):
                print(f"    {c['key']}: {c['type']} visible={c.get('visible')}")

            print(f"\n  --- DOM 팝업 ({len(after_state.get('dom_popups', []))}개) ---")
            for p in after_state.get("dom_popups", []):
                print(f"    {p['id']}: {p['w']}x{p['h']} at ({p['x']},{p['y']})")

            print(f"\n  --- 앱 팝업 ---")
            for p in after_state.get("app_popups", []):
                print(f"    {p['key']}: {p['type']}")

            print(f"\n  --- FrameSet 키 ---")
            print(f"    {after_state.get('frameset_keys', [])}")

            print(f"\n  --- iframes ---")
            for f in after_state.get("iframes", []):
                print(f"    {f['id']}: {f['w']}x{f['h']} src={f.get('src','')[:80]}")

        # 스크린샷
        driver.save_screenshot(str(PROJECT_ROOT / "data" / "waste_popup_after_dblclick.png"))
        print(f"\n  스크린샷: data/waste_popup_after_dblclick.png")

        # ============================================================
        # 팝업 내 데이터셋 전수 조사
        # ============================================================
        print(f"\n[5] 팝업 데이터셋 전수 조사")

        popup_ds = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var mf = app.mainframe;
                var frame = mf.HFrameSet00.VFrameSet00.FrameSet[arguments[0]];
                var form = frame.form;
                var result = {};

                // form 전체 재귀 탐색
                function deepSearch(obj, prefix, depth) {
                    if (depth > 5) return;
                    for (var key in obj) {
                        try {
                            var item = obj[key];
                            if (!item || typeof item !== 'object') continue;

                            // 데이터셋
                            if (item.getRowCount && item.getColumn) {
                                var rc = item.getRowCount();
                                var cols = [];
                                try {
                                    var cc = item.getColCount ? item.getColCount() : 0;
                                    for (var c = 0; c < cc; c++) cols.push(item.getColID(c));
                                } catch(e) {}

                                var dsInfo = {rows: rc, cols: cols};
                                if (rc > 0 && cols.length > 0) {
                                    var samples = [];
                                    for (var r = 0; r < Math.min(rc, 10); r++) {
                                        var row = {};
                                        for (var c = 0; c < cols.length; c++) {
                                            var val = item.getColumn(r, cols[c]);
                                            if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                                            row[cols[c]] = val;
                                        }
                                        samples.push(row);
                                    }
                                    dsInfo.samples = samples;
                                }
                                result[prefix + '.' + key] = dsInfo;
                            }

                            // form 하위
                            if (item.form && (key.startsWith('div') || key.startsWith('Div') ||
                                              key.indexOf('Popup') >= 0 || key.indexOf('popup') >= 0 ||
                                              key.indexOf('Call') >= 0 || key.indexOf('Detail') >= 0 ||
                                              key.indexOf('Dialog') >= 0)) {
                                deepSearch(item.form, prefix + '.' + key + '.form', depth + 1);
                            }
                        } catch(e) {}
                    }
                }

                deepSearch(form, 'form', 0);

                return result;
            } catch(e) {
                return {error: e.message};
            }
        """, FRAME_ID)

        if popup_ds and not popup_ds.get("error"):
            for dsPath, dsInfo in sorted(popup_ds.items()):
                rc = dsInfo.get("rows", 0)
                cols = dsInfo.get("cols", [])
                marker = " *** " if any(c for c in cols if 'ITEM' in c or 'PLU' in c or 'BARCODE' in c) else ""
                print(f"\n  {marker}{dsPath}: rows={rc}")
                print(f"    cols: {cols}")
                if dsInfo.get("samples"):
                    for i, s in enumerate(dsInfo["samples"][:5]):
                        print(f"    [{i}] {json.dumps(s, ensure_ascii=False, default=str)[:300]}")
        else:
            print(f"  오류: {popup_ds}")

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
