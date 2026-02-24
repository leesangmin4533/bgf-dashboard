"""
폐기 전표 더블클릭 팝업 구조 재탐색 (v2)

핵심 개선:
1) oncelldblclick 이벤트 핸들러 함수 소스코드 분석
2) 그리드 전체 셀(모든 컬럼) 좌표 확인
3) 전표번호 컬럼 더블클릭 시도
4) 팝업 감지 범위 확대: window.open, ChildFrame, PopupDiv, 전체 DOM 변화 추적
5) dsGs 파라미터 체크 + 새 프레임 탐색
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

        row_count = driver.execute_script(f"""
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
            return form.div_workForm.form.dsList.getRowCount();
        """)
        print(f"  전표 {row_count}건 조회됨")

        # ============================================================
        # [2] 그리드 이벤트 핸들러 분석
        # ============================================================
        print(f"\n[2] 그리드 이벤트 핸들러 분석")

        event_info = driver.execute_script(f"""
            try {{
                var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
                var wf = form.div_workForm.form;
                var result = {{}};

                // gdList 그리드 찾기
                var grid = null;
                var gridPath = '';

                // div2.form 하위에서 그리드 검색
                if (wf.div2 && wf.div2.form) {{
                    for (var key in wf.div2.form) {{
                        if (wf.div2.form[key] && wf.div2.form[key]._type_name === 'Grid') {{
                            grid = wf.div2.form[key];
                            gridPath = 'div2.form.' + key;
                        }}
                    }}
                }}

                // wf 바로 아래에서도 검색
                if (!grid) {{
                    for (var key in wf) {{
                        if (wf[key] && wf[key]._type_name === 'Grid') {{
                            grid = wf[key];
                            gridPath = 'wf.' + key;
                        }}
                    }}
                }}

                result.gridPath = gridPath;
                result.gridId = grid ? grid.id : null;

                if (grid) {{
                    // 이벤트 핸들러 확인
                    var events = ['oncelldblclick', 'oncellclick', 'onrowdblclick', 'onrowclick'];
                    for (var e = 0; e < events.length; e++) {{
                        var evName = events[e];
                        var handler = grid[evName];
                        if (handler) {{
                            var handlerStr = '';
                            if (handler._userhandler) {{
                                for (var h in handler._userhandler) {{
                                    handlerStr += h + ': ' + (typeof handler._userhandler[h]) + '; ';
                                }}
                            }}
                            if (handler._event_list) {{
                                for (var i = 0; i < handler._event_list.length; i++) {{
                                    handlerStr += 'list[' + i + ']=' + handler._event_list[i] + '; ';
                                }}
                            }}
                            // 핸들러 이름 추출
                            var hName = '';
                            try {{
                                if (handler._default_handler) hName = handler._default_handler.toString().substring(0, 200);
                                else if (handler.handler) hName = handler.handler.toString().substring(0, 200);
                            }} catch(err) {{}}

                            result[evName] = {{
                                exists: true,
                                handlerInfo: handlerStr,
                                handlerName: hName,
                                type: handler._type_name || typeof handler
                            }};
                        }} else {{
                            result[evName] = {{exists: false}};
                        }}
                    }}

                    // 그리드 컬럼/헤더 정보
                    var cols = [];
                    try {{
                        var formatCols = grid._curFormat ? grid._curFormat._cols : null;
                        if (formatCols) {{
                            for (var c = 0; c < formatCols.length; c++) {{
                                cols.push({{idx: c, id: formatCols[c].id || c}});
                            }}
                        }}
                    }} catch(e) {{}}
                    result.grid_cols = cols;

                    // 그리드 헤더 텍스트 (바인드 컬럼명)
                    var headers = [];
                    try {{
                        var hCells = grid._curFormat ? grid._curFormat._headcells : null;
                        if (hCells) {{
                            for (var c = 0; c < hCells.length; c++) {{
                                headers.push({{
                                    idx: c,
                                    text: hCells[c].text || '',
                                    col: hCells[c].col,
                                    displaytype: hCells[c].displaytype || ''
                                }});
                            }}
                        }}
                    }} catch(e) {{}}
                    result.grid_headers = headers;

                    // 그리드 바디 셀 (바인드 컬럼)
                    var bodyCells = [];
                    try {{
                        var bCells = grid._curFormat ? grid._curFormat._bodycells : null;
                        if (bCells) {{
                            for (var c = 0; c < bCells.length; c++) {{
                                bodyCells.push({{
                                    idx: c,
                                    text: bCells[c].text || '',
                                    col: bCells[c].col,
                                    displaytype: bCells[c].displaytype || ''
                                }});
                            }}
                        }}
                    }} catch(e) {{}}
                    result.body_cells = bodyCells;
                }}

                // form 레벨 함수들 (dblclick 관련)
                var formFuncs = [];
                var searchForms = [form, wf];
                var searchNames = ['form', 'wf'];
                for (var si = 0; si < searchForms.length; si++) {{
                    var sf = searchForms[si];
                    for (var key in sf) {{
                        if (typeof sf[key] === 'function' &&
                            (key.toLowerCase().indexOf('dbl') >= 0 ||
                             key.toLowerCase().indexOf('detail') >= 0 ||
                             key.toLowerCase().indexOf('popup') >= 0 ||
                             key.toLowerCase().indexOf('open') >= 0 ||
                             key.toLowerCase().indexOf('cell') >= 0)) {{
                            var src = '';
                            try {{ src = sf[key].toString().substring(0, 300); }} catch(e) {{}}
                            formFuncs.push({{
                                path: searchNames[si] + '.' + key,
                                source: src
                            }});
                        }}
                    }}
                }}
                result.form_functions = formFuncs;

                return result;
            }} catch(e) {{
                return {{error: e.message, stack: e.stack}};
            }}
        """)

        if event_info.get("error"):
            print(f"  오류: {event_info['error']}")
        else:
            print(f"  그리드 경로: {event_info.get('gridPath')}")
            print(f"  그리드 ID: {event_info.get('gridId')}")

            # 이벤트 핸들러
            for evName in ['oncelldblclick', 'oncellclick', 'onrowdblclick', 'onrowclick']:
                ev = event_info.get(evName, {})
                if ev.get('exists'):
                    print(f"\n  {evName}: EXISTS")
                    print(f"    handlerInfo: {ev.get('handlerInfo', '')}")
                    print(f"    handlerName: {ev.get('handlerName', '')[:200]}")

            # 헤더
            print(f"\n  그리드 헤더:")
            for h in event_info.get('grid_headers', []):
                print(f"    [{h['idx']}] col={h.get('col')} text={h.get('text')}")

            # 바디 셀
            print(f"\n  바디 셀 바인딩:")
            for b in event_info.get('body_cells', []):
                print(f"    [{b['idx']}] col={b.get('col')} text={b.get('text')}")

            # 관련 함수
            print(f"\n  관련 함수 ({len(event_info.get('form_functions', []))}개):")
            for f in event_info.get('form_functions', []):
                print(f"    {f['path']}:")
                print(f"      {f['source'][:300]}")

        # ============================================================
        # [3] 이벤트 핸들러 함수 소스코드 상세 분석
        # ============================================================
        print(f"\n\n[3] oncelldblclick 핸들러 함수 소스코드 상세 추출")

        handler_source = driver.execute_script(f"""
            try {{
                var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
                var wf = form.div_workForm.form;
                var result = {{}};

                // 그리드 찾기
                var grid = null;
                if (wf.div2 && wf.div2.form) {{
                    for (var key in wf.div2.form) {{
                        if (wf.div2.form[key] && wf.div2.form[key]._type_name === 'Grid') {{
                            grid = wf.div2.form[key];
                            result.gridId = key;
                            break;
                        }}
                    }}
                }}

                if (!grid) {{
                    for (var key in wf) {{
                        if (wf[key] && wf[key]._type_name === 'Grid') {{
                            grid = wf[key];
                            result.gridId = key;
                            break;
                        }}
                    }}
                }}

                if (!grid) return {{error: 'Grid not found'}};

                // oncelldblclick 이벤트의 실제 핸들러 함수 찾기
                var ev = grid.oncelldblclick;
                if (ev) {{
                    // _userhandler에서 함수명 추출
                    if (ev._userhandler) {{
                        for (var funcName in ev._userhandler) {{
                            result.handler_name = funcName;
                            // form/wf에서 해당 함수 찾기
                            var func = null;
                            if (typeof wf[funcName] === 'function') {{
                                func = wf[funcName];
                                result.handler_location = 'wf.' + funcName;
                            }} else if (typeof form[funcName] === 'function') {{
                                func = form[funcName];
                                result.handler_location = 'form.' + funcName;
                            }}
                            if (func) {{
                                result.handler_source = func.toString();
                            }}
                        }}
                    }}

                    // 이벤트 객체 자체 덤프
                    var evProps = [];
                    for (var key in ev) {{
                        try {{
                            var val = ev[key];
                            var type = typeof val;
                            if (type === 'function') {{
                                evProps.push({{key: key, type: 'function', src: val.toString().substring(0, 100)}});
                            }} else if (type !== 'object') {{
                                evProps.push({{key: key, type: type, value: String(val).substring(0, 100)}});
                            }}
                        }} catch(e) {{}}
                    }}
                    result.event_props = evProps;
                }}

                // 추가: 핸들러 이름으로 직접 검색
                var funcNames = ['gdList_oncelldblclick', 'fn_gdList_oncelldblclick',
                                 'div2_gdList_oncelldblclick', 'gdList_oncelldbclick',
                                 'fn_cellDblClick', 'fn_dblclick', 'fn_openDetail',
                                 'fn_openPopup', 'fn_chitDetail', 'fn_viewDetail'];
                var foundFuncs = {{}};
                for (var i = 0; i < funcNames.length; i++) {{
                    if (typeof wf[funcNames[i]] === 'function') {{
                        foundFuncs[funcNames[i]] = wf[funcNames[i]].toString().substring(0, 500);
                    }}
                    if (typeof form[funcNames[i]] === 'function') {{
                        foundFuncs['form.' + funcNames[i]] = form[funcNames[i]].toString().substring(0, 500);
                    }}
                }}
                result.direct_search = foundFuncs;

                return result;
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        if handler_source.get("error"):
            print(f"  오류: {handler_source['error']}")
        else:
            print(f"  그리드 ID: {handler_source.get('gridId')}")
            print(f"  핸들러 이름: {handler_source.get('handler_name')}")
            print(f"  핸들러 위치: {handler_source.get('handler_location')}")
            if handler_source.get('handler_source'):
                print(f"\n  === 핸들러 소스코드 ===")
                src = handler_source['handler_source']
                # 줄 단위 출력
                for line in src.split('\n'):
                    print(f"    {line}")
            else:
                print(f"  핸들러 소스코드 없음")

            if handler_source.get('event_props'):
                print(f"\n  이벤트 프로퍼티:")
                for p in handler_source['event_props']:
                    print(f"    {p['key']}: {p['type']} = {p.get('value', p.get('src', ''))}")

            if handler_source.get('direct_search'):
                print(f"\n  직접 검색 결과:")
                for fname, fsrc in handler_source['direct_search'].items():
                    print(f"\n    {fname}:")
                    for line in fsrc.split('\n'):
                        print(f"      {line}")

        # ============================================================
        # [4] 더블클릭 - 모든 셀 좌표 확보 후 전표번호 컬럼에 시도
        # ============================================================
        print(f"\n\n[4] 그리드 셀 전체 좌표 확인")

        all_cells = driver.execute_script(f"""
            try {{
                var result = [];
                // 0행의 모든 셀
                for (var col = 0; col < 20; col++) {{
                    var cells = document.querySelectorAll('[id*="{FRAME_ID}"][id*="gdList"][id*="body"] div[id*="cell_0_' + col + '"]');
                    for (var i = 0; i < cells.length; i++) {{
                        var r = cells[i].getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) {{
                            result.push({{
                                id: cells[i].id,
                                col: col,
                                x: Math.round(r.x + r.width/2),
                                y: Math.round(r.y + r.height/2),
                                w: Math.round(r.width),
                                h: Math.round(r.height),
                                text: cells[i].textContent ? cells[i].textContent.trim().substring(0, 30) : ''
                            }});
                        }}
                    }}
                }}
                return result;
            }} catch(e) {{
                return [{{error: e.message}}];
            }}
        """)

        if all_cells and not all_cells[0].get("error"):
            print(f"  셀 {len(all_cells)}개:")
            for c in all_cells:
                print(f"    col={c['col']} ({c['w']}x{c['h']}) at ({c['x']},{c['y']}) text='{c['text']}' id={c['id']}")
        else:
            print(f"  셀 검색 실패: {all_cells}")

        # ============================================================
        # [5] 더블클릭 전 DOM 요소 카운트 기록
        # ============================================================
        before_dom_count = driver.execute_script("""
            return {
                allDivs: document.querySelectorAll('div').length,
                allIframes: document.querySelectorAll('iframe').length,
                visibleElements: document.querySelectorAll('div[style*="visible"], div[style*="block"], div[style*="display"]').length
            };
        """)
        print(f"\n  더블클릭 전 DOM: divs={before_dom_count['allDivs']}, iframes={before_dom_count['allIframes']}")

        # ============================================================
        # [6] 전표번호 컬럼 또는 가장 넓은 텍스트 셀에 더블클릭
        # ============================================================
        print(f"\n[5] 전표번호 컬럼 더블클릭 시도")

        # 전표번호가 있는 셀 또는 숫자 텍스트가 있는 셀 찾기
        target_cell = None
        if all_cells and not all_cells[0].get("error"):
            # 텍스트가 숫자(전표번호)인 셀 찾기
            for c in all_cells:
                txt = c.get('text', '')
                # 전표번호 패턴: 숫자 8~12자리 또는 하이픈 포함
                if txt and (txt.replace('-', '').replace(' ', '').isdigit() and len(txt) >= 4):
                    target_cell = c
                    print(f"  전표번호 셀 발견: col={c['col']} text='{c['text']}' at ({c['x']},{c['y']})")
                    break

            # 못찾으면 두번째 이후 셀 중 텍스트 있는 것
            if not target_cell:
                for c in all_cells:
                    if c['col'] >= 1 and c.get('text'):
                        target_cell = c
                        print(f"  대안 셀: col={c['col']} text='{c['text']}' at ({c['x']},{c['y']})")
                        break

            # 그래도 못찾으면 첫번째
            if not target_cell and all_cells:
                target_cell = all_cells[0]
                print(f"  첫번째 셀: col={target_cell['col']} at ({target_cell['x']},{target_cell['y']})")

        if target_cell:
            # rowposition 설정
            driver.execute_script(f"""
                var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
                form.div_workForm.form.dsList.set_rowposition(0);
            """)
            time.sleep(0.3)

            # ActionChains 더블클릭
            body = driver.find_element("tag name", "body")
            ActionChains(driver).move_to_element_with_offset(
                body, target_cell["x"], target_cell["y"]
            ).double_click().perform()
            print(f"  더블클릭 실행: ({target_cell['x']}, {target_cell['y']})")
            time.sleep(5)  # 팝업 로딩 충분히 대기
        else:
            print(f"  셀을 찾을 수 없음!")
            return

        # ============================================================
        # [7] 팝업 감지 (광범위)
        # ============================================================
        print(f"\n[6] 팝업 감지 (광범위)")

        # 스크린샷 먼저
        driver.save_screenshot(str(PROJECT_ROOT / "data" / "waste_popup_v2.png"))
        print(f"  스크린샷: data/waste_popup_v2.png")

        popup_detect = driver.execute_script(f"""
            try {{
                var result = {{}};
                var app = nexacro.getApplication();
                var mf = app.mainframe;

                // 1) DOM 변화: div 카운트
                result.dom_divs = document.querySelectorAll('div').length;
                result.dom_iframes = document.querySelectorAll('iframe').length;

                // 2) 새로 나타난 큰 div (팝업일 가능성)
                var newBigDivs = [];
                var allDivs = document.querySelectorAll('div');
                for (var i = 0; i < allDivs.length; i++) {{
                    var r = allDivs[i].getBoundingClientRect();
                    // 팝업 크기 범위: 300x200 이상, 화면 전체보다 작은
                    if (r.width > 300 && r.height > 200 && r.width < 1200 && r.height < 800) {{
                        var style = window.getComputedStyle(allDivs[i]);
                        var zIndex = parseInt(style.zIndex) || 0;
                        if (zIndex > 100 || style.position === 'absolute' || style.position === 'fixed') {{
                            newBigDivs.push({{
                                id: allDivs[i].id,
                                w: Math.round(r.width),
                                h: Math.round(r.height),
                                x: Math.round(r.x),
                                y: Math.round(r.y),
                                zIndex: zIndex,
                                position: style.position,
                                className: allDivs[i].className ? allDivs[i].className.substring(0, 50) : ''
                            }});
                        }}
                    }}
                }}
                result.big_divs_with_zindex = newBigDivs;

                // 3) 넥사크로 팝업 탐색
                // app._popupframes
                var appPopups = [];
                if (app._popupframes) {{
                    for (var key in app._popupframes) {{
                        appPopups.push(key);
                    }}
                }}
                result.app_popupframes = appPopups;

                // nexacro._popupframes
                var nxPopups = [];
                if (typeof nexacro !== 'undefined' && nexacro._popupframes) {{
                    for (var key in nexacro._popupframes) {{
                        nxPopups.push(key);
                    }}
                }}
                result.nexacro_popupframes = nxPopups;

                // 4) FrameSet 변화
                var fs = mf.HFrameSet00.VFrameSet00.FrameSet;
                var fsKeys = [];
                for (var key in fs) {{
                    if (fs[key] && key.match(/^[A-Z]/)) {{
                        fsKeys.push(key);
                    }}
                }}
                result.frameset_keys = fsKeys;

                // 5) mainframe 직계 자식 중 ChildFrame 타입
                var childFrames = [];
                for (var key in mf) {{
                    try {{
                        if (mf[key] && typeof mf[key] === 'object' && key !== 'all') {{
                            var type = mf[key]._type_name || '';
                            if (type === 'ChildFrame' || type === 'Frame' || type === 'PopupFrame') {{
                                childFrames.push({{key: key, type: type, visible: mf[key].visible}});
                            }}
                        }}
                    }} catch(e) {{}}
                }}
                result.mainframe_childframes = childFrames;

                // 6) 전체 window 객체에서 팝업 관련 변수
                var windowPopups = [];
                try {{
                    if (window._popup) windowPopups.push('window._popup');
                    if (window.popupWindow) windowPopups.push('window.popupWindow');
                    if (window.openedWindow) windowPopups.push('window.openedWindow');
                }} catch(e) {{}}
                result.window_popups = windowPopups;

                // 7) dsGs 파라미터 확인
                var dsGsData = null;
                try {{
                    var form = mf.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
                    var dsGs = form.dsGs || form.div_workForm.form.dsGs;
                    if (dsGs && dsGs.getRowCount() > 0) {{
                        var gsRow = {{}};
                        var cc = dsGs.getColCount();
                        for (var c = 0; c < cc; c++) {{
                            var colId = dsGs.getColID(c);
                            var val = dsGs.getColumn(0, colId);
                            if (val !== null && val !== undefined && val !== '') {{
                                gsRow[colId] = String(val);
                            }}
                        }}
                        dsGsData = gsRow;
                    }}
                }} catch(e) {{
                    dsGsData = {{error: e.message}};
                }}
                result.dsGs = dsGsData;

                // 8) 브라우저 탭/윈도우 핸들 수
                // (Selenium에서만 가능, JS에서는 제한적)

                return result;
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        if popup_detect.get("error"):
            print(f"  오류: {popup_detect['error']}")
        else:
            print(f"\n  DOM divs: {popup_detect.get('dom_divs')} (이전: {before_dom_count['allDivs']})")
            print(f"  DOM iframes: {popup_detect.get('dom_iframes')} (이전: {before_dom_count['allIframes']})")

            big_divs = popup_detect.get('big_divs_with_zindex', [])
            print(f"\n  높은 z-index 큰 div ({len(big_divs)}개):")
            for d in big_divs[:20]:
                print(f"    id={d['id']} {d['w']}x{d['h']} at ({d['x']},{d['y']}) z={d['zIndex']} pos={d['position']}")

            print(f"\n  app._popupframes: {popup_detect.get('app_popupframes', [])}")
            print(f"  nexacro._popupframes: {popup_detect.get('nexacro_popupframes', [])}")
            print(f"  FrameSet keys: {popup_detect.get('frameset_keys', [])}")
            print(f"  mainframe ChildFrames: {popup_detect.get('mainframe_childframes', [])}")
            print(f"  window popups: {popup_detect.get('window_popups', [])}")
            print(f"  dsGs: {popup_detect.get('dsGs')}")

        # ============================================================
        # [8] 브라우저 윈도우 핸들 확인 (새 창?)
        # ============================================================
        print(f"\n[7] 브라우저 윈도우 핸들 확인")
        handles = driver.window_handles
        print(f"  윈도우 핸들: {len(handles)}개")
        for i, h in enumerate(handles):
            print(f"    [{i}] {h}")
            if i > 0:
                # 새 창이 열렸을 수 있음
                driver.switch_to.window(h)
                print(f"      title: {driver.title}")
                print(f"      url: {driver.current_url[:100]}")
                driver.switch_to.window(handles[0])  # 원래 창으로

        # ============================================================
        # [9] 대안: oncelldblclick 이벤트를 JS로 직접 fire
        # ============================================================
        print(f"\n\n[8] JS 이벤트 직접 fire 시도")

        # 먼저 현재 그리드 oncelldblclick 핸들러 함수를 직접 호출
        js_fire_result = driver.execute_script(f"""
            try {{
                var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
                var wf = form.div_workForm.form;
                var result = {{}};

                // 그리드 찾기
                var grid = null;
                if (wf.div2 && wf.div2.form) {{
                    for (var key in wf.div2.form) {{
                        if (wf.div2.form[key] && wf.div2.form[key]._type_name === 'Grid') {{
                            grid = wf.div2.form[key];
                            break;
                        }}
                    }}
                }}
                if (!grid) {{
                    for (var key in wf) {{
                        if (wf[key] && wf[key]._type_name === 'Grid') {{ grid = wf[key]; break; }}
                    }}
                }}

                if (!grid) return {{error: 'Grid not found'}};

                // oncelldblclick 핸들러 함수명 찾기
                var handlerName = null;
                if (grid.oncelldblclick && grid.oncelldblclick._userhandler) {{
                    for (var fn in grid.oncelldblclick._userhandler) {{
                        handlerName = fn;
                        break;
                    }}
                }}
                result.handlerName = handlerName;

                if (handlerName) {{
                    // wf에서 해당 함수 직접 호출
                    var handler = wf[handlerName] || form[handlerName];
                    if (handler) {{
                        // 이벤트 객체 모의
                        var evt = {{
                            cell: 0,
                            col: 0,
                            row: 0,
                            clickitem: 'body',
                            fromobject: grid,
                            fromreferenceobject: grid
                        }};
                        handler.call(wf, grid, evt);
                        result.called = true;
                    }} else {{
                        result.error = 'Handler function not found: ' + handlerName;
                    }}
                }} else {{
                    result.error = 'No handler name found';
                }}

                return result;
            }} catch(e) {{
                return {{error: e.message, stack: e.stack ? e.stack.substring(0, 300) : ''}};
            }}
        """)

        print(f"  JS fire 결과: {js_fire_result}")
        time.sleep(5)

        # 다시 팝업 확인
        after_js_fire = driver.execute_script(f"""
            try {{
                var app = nexacro.getApplication();
                var mf = app.mainframe;
                var result = {{}};

                // DOM 변화
                result.dom_divs = document.querySelectorAll('div').length;

                // 큰 div (z-index 높은)
                var bigDivs = [];
                var allDivs = document.querySelectorAll('div');
                for (var i = 0; i < allDivs.length; i++) {{
                    var r = allDivs[i].getBoundingClientRect();
                    if (r.width > 300 && r.height > 200 && r.width < 1200 && r.height < 800) {{
                        var style = window.getComputedStyle(allDivs[i]);
                        var zIndex = parseInt(style.zIndex) || 0;
                        if (zIndex > 100 || style.position === 'absolute' || style.position === 'fixed') {{
                            bigDivs.push({{id: allDivs[i].id, w: Math.round(r.width), h: Math.round(r.height), z: zIndex}});
                        }}
                    }}
                }}
                result.big_divs = bigDivs;

                // FrameSet 키
                var fs = mf.HFrameSet00.VFrameSet00.FrameSet;
                var fsKeys = [];
                for (var key in fs) {{
                    if (fs[key] && key.match(/^[A-Z]/)) fsKeys.push(key);
                }}
                result.frameset_keys = fsKeys;

                // app._popupframes
                var appPopups = [];
                if (app._popupframes) {{
                    for (var key in app._popupframes) appPopups.push(key);
                }}
                result.app_popups = appPopups;

                // 윈도우 핸들
                result.windowCount = 'check selenium';

                // dsGs
                try {{
                    var form = mf.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
                    var dsGs = form.dsGs || form.div_workForm.form.dsGs;
                    if (dsGs && dsGs.getRowCount() > 0) {{
                        var gsRow = {{}};
                        var cc = dsGs.getColCount();
                        for (var c = 0; c < cc; c++) {{
                            var colId = dsGs.getColID(c);
                            var val = dsGs.getColumn(0, colId);
                            if (val !== null && val !== undefined && val !== '') {{
                                gsRow[colId] = String(val);
                            }}
                        }}
                        result.dsGs = gsRow;
                    }}
                }} catch(e) {{}}

                return result;
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        print(f"\n  JS fire 후 상태:")
        print(f"    DOM divs: {after_js_fire.get('dom_divs')}")
        print(f"    큰 div: {after_js_fire.get('big_divs', [])}")
        print(f"    FrameSet: {after_js_fire.get('frameset_keys', [])}")
        print(f"    app popups: {after_js_fire.get('app_popups', [])}")
        print(f"    dsGs: {after_js_fire.get('dsGs')}")

        # 윈도우 핸들 재확인
        handles2 = driver.window_handles
        print(f"    윈도우 핸들: {len(handles2)}개")
        if len(handles2) > len(handles):
            print(f"    *** 새 윈도우 감지! ***")
            for i, h in enumerate(handles2):
                if h not in handles:
                    driver.switch_to.window(h)
                    print(f"      새 창 title: {driver.title}")
                    print(f"      새 창 url: {driver.current_url[:200]}")
                    # 새 창의 구조 탐색
                    new_window_content = driver.execute_script("""
                        try {
                            var result = {};
                            result.title = document.title;
                            result.bodyText = document.body ? document.body.innerText.substring(0, 500) : '';
                            result.divCount = document.querySelectorAll('div').length;
                            // 넥사크로 존재 확인
                            if (typeof nexacro !== 'undefined') {
                                result.nexacro = true;
                                var app = nexacro.getApplication();
                                if (app) {
                                    result.appId = app.id;
                                }
                            }
                            return result;
                        } catch(e) {
                            return {error: e.message};
                        }
                    """)
                    print(f"      새 창 내용: {json.dumps(new_window_content, ensure_ascii=False, default=str)}")
                    driver.switch_to.window(handles[0])

        # 최종 스크린샷
        driver.save_screenshot(str(PROJECT_ROOT / "data" / "waste_popup_v2_after_jsfire.png"))
        print(f"\n  최종 스크린샷: data/waste_popup_v2_after_jsfire.png")

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
