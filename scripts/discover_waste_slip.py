"""
BGF 검수전표 > 통합 전표 조회 화면 구조 탐색 스크립트

사용법:
    cd bgf_auto
    python scripts/discover_waste_slip.py [--store-id 46513]

목적:
    1. "검수전표" 메뉴의 전체 서브메뉴 목록 수집
    2. "통합 전표 조회" (또는 유사 이름) 클릭
    3. 로딩된 프레임 ID 식별
    4. 해당 화면의 데이터셋(ds*), 콤보(cb*), 그리드(grd*) 전수 조사
    5. 전표구분 필터, 날짜 컨트롤 식별
    6. 데이터 컬럼 매핑
"""

import sys
import io
import json
import time
import argparse
from pathlib import Path

# Windows CP949 콘솔 -> UTF-8 래핑
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.sales_analyzer import SalesAnalyzer
from src.settings.constants import DEFAULT_STORE_ID


def discover_waste_slip_frame(store_id: str = DEFAULT_STORE_ID) -> dict:
    """BGF 검수전표 > 통합 전표 조회 화면 구조 탐색"""

    analyzer = SalesAnalyzer(store_id=store_id)
    results = {}

    try:
        # ============================================================
        # 0. 로그인
        # ============================================================
        print("\n" + "=" * 70)
        print("  [0] BGF 사이트 로그인")
        print("=" * 70)

        analyzer.setup_driver()
        analyzer.connect()

        if not analyzer.do_login():
            print("[ERROR] 로그인 실패")
            return {}

        analyzer.close_popup()
        time.sleep(2)
        print("[OK] 로그인 성공")

        driver = analyzer.driver

        # ============================================================
        # 1. 현재 FrameSet의 모든 프레임 ID 기록 (비교용)
        # ============================================================
        print("\n" + "=" * 70)
        print("  [1] 현재 로드된 프레임 ID 목록 (기준선)")
        print("=" * 70)

        before_frames = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var fs = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
                var frames = [];
                if (fs && fs.frames) {
                    for (var i = 0; i < fs.frames.length; i++) {
                        frames.push(fs.frames[i].name || fs.frames[i].id);
                    }
                }
                // 대안: 속성 직접 순회
                if (frames.length === 0) {
                    for (var key in fs) {
                        if (fs[key] && fs[key].form) {
                            frames.push(key);
                        }
                    }
                }
                return frames;
            } catch(e) {
                return ['error: ' + e.message];
            }
        """)
        print(f"  기존 프레임: {before_frames}")
        results["before_frames"] = before_frames

        # ============================================================
        # 2. 상단 메뉴 전체 목록 수집
        # ============================================================
        print("\n" + "=" * 70)
        print("  [2] 상단 메뉴 전체 목록")
        print("=" * 70)

        menus = driver.execute_script("""
            var result = [];
            var menuEls = document.querySelectorAll('[id*="div_topMenu"] [id*=":icontext"]');
            for (var i = 0; i < menuEls.length; i++) {
                var el = menuEls[i];
                result.push({
                    id: el.id,
                    text: (el.innerText || '').trim(),
                    visible: el.offsetParent !== null
                });
            }
            return result;
        """)
        for m in menus:
            marker = " *" if "검수" in m.get("text", "") else ""
            print(f"  [{m['text']}] id={m['id']} visible={m['visible']}{marker}")
        results["top_menus"] = menus

        # ============================================================
        # 3. "검수전표" 메뉴 클릭 → 서브메뉴 수집
        # ============================================================
        print("\n" + "=" * 70)
        print("  [3] '검수전표' 메뉴 클릭 + 서브메뉴 수집")
        print("=" * 70)

        # 검수전표 메뉴 클릭
        click_result = driver.execute_script("""
            function clickEl(el) {
                if (!el || el.offsetParent === null) return false;
                el.scrollIntoView({block: 'center'});
                var r = el.getBoundingClientRect();
                var o = {bubbles:true, cancelable:true, view:window,
                         clientX:r.left+r.width/2, clientY:r.top+r.height/2};
                el.dispatchEvent(new MouseEvent('mousedown', o));
                el.dispatchEvent(new MouseEvent('mouseup', o));
                el.dispatchEvent(new MouseEvent('click', o));
                return true;
            }

            var menuEls = document.querySelectorAll('[id*="div_topMenu"] [id*=":icontext"]');
            for (var i = 0; i < menuEls.length; i++) {
                var text = (menuEls[i].innerText || '').trim();
                if (text === '검수전표') {
                    clickEl(menuEls[i]);
                    return {clicked: true, id: menuEls[i].id, text: text};
                }
            }
            return {clicked: false};
        """)
        print(f"  메뉴 클릭: {click_result}")

        time.sleep(1.5)

        # 서브메뉴 항목 수집
        submenus = driver.execute_script("""
            var result = [];

            // 패턴 1: pdiv_topMenu 팝업 하위
            var els = document.querySelectorAll('[id*="pdiv_topMenu"] [id*=":text"]');
            for (var i = 0; i < els.length; i++) {
                var el = els[i];
                if (el.offsetParent !== null) {
                    result.push({
                        id: el.id,
                        text: (el.innerText || '').trim(),
                        visible: true,
                        method: 'pdiv_topMenu'
                    });
                }
            }

            // 패턴 2: pdiv로 시작하는 모든 팝업 메뉴
            if (result.length === 0) {
                var allEls = document.querySelectorAll('[id*="pdiv_"] [id*=":text"]');
                for (var i = 0; i < allEls.length; i++) {
                    var el = allEls[i];
                    if (el.offsetParent !== null) {
                        result.push({
                            id: el.id,
                            text: (el.innerText || '').trim(),
                            visible: true,
                            method: 'pdiv_any'
                        });
                    }
                }
            }

            // 패턴 3: 현재 보이는 모든 메뉴 텍스트 (넓은 범위)
            if (result.length === 0) {
                var allVisible = document.querySelectorAll('[id*="Menu"][id*=":text"], [id*="menu"][id*=":text"]');
                for (var i = 0; i < allVisible.length; i++) {
                    var el = allVisible[i];
                    if (el.offsetParent !== null && (el.innerText || '').trim()) {
                        result.push({
                            id: el.id,
                            text: (el.innerText || '').trim(),
                            visible: true,
                            method: 'broad_search'
                        });
                    }
                }
            }

            return result;
        """)
        print(f"\n  서브메뉴 ({len(submenus)}개):")
        for s in submenus:
            marker = " *** TARGET" if "통합" in s.get("text", "") or "전표" in s.get("text", "") else ""
            print(f"    [{s['text']}] id={s['id']} method={s['method']}{marker}")
        results["submenus"] = submenus

        # ============================================================
        # 4. "통합 전표 조회" (또는 유사) 서브메뉴 클릭
        # ============================================================
        print("\n" + "=" * 70)
        print("  [4] 통합 전표 조회 서브메뉴 클릭")
        print("=" * 70)

        # 서브메뉴에서 "통합" 또는 "전표 조회" 텍스트 포함 항목 찾기
        target_submenu = None
        for s in submenus:
            text = s.get("text", "")
            if "통합" in text and ("전표" in text or "조회" in text):
                target_submenu = s
                break

        # 못 찾으면 "전표" 키워드로 재시도
        if not target_submenu:
            for s in submenus:
                text = s.get("text", "")
                if "전표" in text and "조회" in text and "센터" not in text:
                    target_submenu = s
                    break

        # 그래도 못 찾으면 모든 서브메뉴 출력 후 종료
        if not target_submenu:
            print("  [WARN] '통합 전표 조회' 서브메뉴를 찾지 못했습니다.")
            print("  사용 가능한 서브메뉴:")
            for s in submenus:
                print(f"    - {s['text']} (id: {s['id']})")
            results["target_found"] = False

            # 센터매입이 아닌 다른 서브메뉴 전부 시도
            for s in submenus:
                if "센터" not in s.get("text", ""):
                    target_submenu = s
                    print(f"\n  센터매입 외 첫 번째 서브메뉴 시도: {s['text']}")
                    break

        if target_submenu:
            print(f"  타겟 서브메뉴: [{target_submenu['text']}] id={target_submenu['id']}")

            # 서브메뉴 클릭
            sub_click = driver.execute_script("""
                function clickEl(el) {
                    if (!el || el.offsetParent === null) return false;
                    el.scrollIntoView({block: 'center'});
                    var r = el.getBoundingClientRect();
                    var o = {bubbles:true, cancelable:true, view:window,
                             clientX:r.left+r.width/2, clientY:r.top+r.height/2};
                    el.dispatchEvent(new MouseEvent('mousedown', o));
                    el.dispatchEvent(new MouseEvent('mouseup', o));
                    el.dispatchEvent(new MouseEvent('click', o));
                    return true;
                }

                var el = document.getElementById(arguments[0]);
                if (el) {
                    clickEl(el);
                    return {clicked: true, id: arguments[0]};
                }
                return {clicked: false, id: arguments[0]};
            """, target_submenu["id"])
            print(f"  서브메뉴 클릭: {sub_click}")

            time.sleep(3)

            # ============================================================
            # 5. 새로 로딩된 프레임 ID 찾기
            # ============================================================
            print("\n" + "=" * 70)
            print("  [5] 새로 로딩된 프레임 ID 식별")
            print("=" * 70)

            after_frames = driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    var fs = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
                    var frames = [];

                    // 방법 1: frames 배열
                    if (fs && fs.frames) {
                        for (var i = 0; i < fs.frames.length; i++) {
                            frames.push(fs.frames[i].name || fs.frames[i].id);
                        }
                    }

                    // 방법 2: 속성 직접 순회
                    if (frames.length === 0) {
                        for (var key in fs) {
                            if (fs[key] && fs[key].form && key.match(/^[A-Z]/)) {
                                frames.push(key);
                            }
                        }
                    }

                    return frames;
                } catch(e) {
                    return ['error: ' + e.message];
                }
            """)
            print(f"  이후 프레임: {after_frames}")
            results["after_frames"] = after_frames

            # 새로 추가된 프레임 식별
            before_set = set(before_frames or [])
            after_set = set(after_frames or [])
            new_frames = list(after_set - before_set)
            print(f"\n  *** 새로 로딩된 프레임: {new_frames}")
            results["new_frames"] = new_frames

            # 탭 목록에서도 확인
            tab_info = driver.execute_script("""
                var tabs = [];
                var tabEls = document.querySelectorAll('[id*="tab_openList"][id*="tabbuttonitemtext"]');
                for (var i = 0; i < tabEls.length; i++) {
                    tabs.push({
                        id: tabEls[i].id,
                        text: (tabEls[i].innerText || '').trim()
                    });
                }
                return tabs;
            """)
            print(f"\n  열린 탭 목록:")
            for t in tab_info:
                print(f"    [{t['text']}] id={t['id']}")
            results["open_tabs"] = tab_info

            # ============================================================
            # 6. 프레임 내부 구조 탐색
            # ============================================================
            target_frame_id = new_frames[0] if new_frames else None

            # 새 프레임이 없으면 탭 텍스트에서 프레임 ID 추출 시도
            if not target_frame_id:
                for t in tab_info:
                    # 탭 ID에서 프레임 ID 추출: "xxx.STGJ020_M0" 패턴
                    tid = t.get("id", "")
                    import re
                    match = re.search(r'(ST\w+_M\d+)', tid)
                    if match:
                        fid = match.group(1)
                        if fid not in before_set:
                            target_frame_id = fid
                            print(f"\n  탭에서 프레임 ID 추출: {target_frame_id}")
                            break

            if target_frame_id:
                print(f"\n" + "=" * 70)
                print(f"  [6] 프레임 [{target_frame_id}] 내부 구조")
                print("=" * 70)

                frame_structure = driver.execute_script("""
                    var report = {};
                    var fid = arguments[0];

                    try {
                        var app = nexacro.getApplication();
                        var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid]?.form;

                        if (!form) {
                            report.error = 'form not found for ' + fid;
                            return report;
                        }

                        report.frame_id = fid;
                        report.form_found = true;

                        // 전체 객체 탐색 (재귀)
                        function exploreForm(f, prefix, depth) {
                            if (depth > 5) return;
                            var items = [];

                            var objs = f.objects || [];
                            for (var i = 0; i < objs.length; i++) {
                                var obj = objs[i];
                                var name = obj.name || obj.id || 'unnamed_' + i;
                                var type = obj.constructor?.name || typeof obj;
                                var fullPath = prefix + '.' + name;

                                var info = {
                                    name: name,
                                    type: type,
                                    path: fullPath
                                };

                                // Dataset인 경우 컬럼 정보
                                if (type === 'Dataset' || (obj.getRowCount && obj.getColumn)) {
                                    info.type = 'Dataset';
                                    info.rowCount = obj.getRowCount();
                                    var cols = [];
                                    try {
                                        for (var c = 0; c < obj.colcount; c++) {
                                            cols.push(obj.getColID(c));
                                        }
                                    } catch(e) {
                                        // colinfo 사용
                                        try {
                                            var colInfos = obj.colinfos;
                                            if (colInfos) {
                                                for (var c = 0; c < colInfos.length; c++) {
                                                    cols.push(colInfos[c].id);
                                                }
                                            }
                                        } catch(e2) {}
                                    }
                                    info.columns = cols;

                                    // 샘플 데이터 (첫 행)
                                    if (info.rowCount > 0 && cols.length > 0) {
                                        var sample = {};
                                        for (var c = 0; c < Math.min(cols.length, 20); c++) {
                                            var val = obj.getColumn(0, cols[c]);
                                            if (val && typeof val === 'object' && val.hi !== undefined) {
                                                val = val.hi;
                                            }
                                            sample[cols[c]] = val;
                                        }
                                        info.sample_row = sample;
                                    }
                                }

                                // Combo인 경우
                                if (type === 'Combo' || name.startsWith('cb') || name.startsWith('cmb')) {
                                    info.type = info.type || 'Combo';
                                    try {
                                        info.value = obj.value;
                                        info.text = obj.text;
                                        info.index = obj.index;
                                    } catch(e) {}
                                }

                                // Calendar/Edit인 경우
                                if (type === 'Calendar' || name.startsWith('cal') || name.startsWith('dt')) {
                                    info.type = info.type || 'Calendar';
                                    try {
                                        info.value = obj.value;
                                        info.text = obj.text;
                                    } catch(e) {}
                                }

                                // Grid인 경우
                                if (type === 'Grid' || name.startsWith('grd') || name.startsWith('Grid')) {
                                    info.type = info.type || 'Grid';
                                    try {
                                        info.binddataset = obj.binddataset;
                                    } catch(e) {}
                                }

                                // Button인 경우
                                if (type === 'Button' || name.startsWith('btn') || name.startsWith('F_')) {
                                    info.type = info.type || 'Button';
                                    try {
                                        info.text = obj.text;
                                    } catch(e) {}
                                }

                                items.push(info);

                                // div 하위 재귀
                                if (obj.form && (name.startsWith('div') || name.startsWith('tab'))) {
                                    var children = exploreForm(obj.form, fullPath + '.form', depth + 1);
                                    if (children.length > 0) {
                                        info.children = children;
                                    }
                                }
                            }
                            return items;
                        }

                        report.objects = exploreForm(form, 'form', 0);
                        return report;

                    } catch(e) {
                        report.error = e.message;
                        return report;
                    }
                """, target_frame_id)

                results["frame_structure"] = frame_structure

                # 구조 출력
                def print_structure(items, indent=0):
                    for item in items:
                        prefix = "  " * indent
                        type_str = item.get("type", "?")
                        name = item.get("name", "?")
                        extra = ""

                        if type_str == "Dataset":
                            cols = item.get("columns", [])
                            rows = item.get("rowCount", 0)
                            extra = f" rows={rows} cols={cols}"
                            if item.get("sample_row"):
                                extra += f"\n{prefix}    sample: {json.dumps(item['sample_row'], ensure_ascii=False, default=str)}"

                        elif type_str in ("Combo", "Calendar"):
                            extra = f" value={item.get('value')} text={item.get('text')}"

                        elif type_str == "Grid":
                            extra = f" binddataset={item.get('binddataset')}"

                        elif type_str == "Button":
                            extra = f" text={item.get('text')}"

                        print(f"{prefix}  [{type_str}] {name}{extra}")

                        if item.get("children"):
                            print_structure(item["children"], indent + 1)

                if frame_structure.get("error"):
                    print(f"  [ERROR] {frame_structure['error']}")
                elif frame_structure.get("objects"):
                    print_structure(frame_structure["objects"])
                else:
                    print("  객체 탐색 결과 없음")

                # ============================================================
                # 7. 이벤트 핸들러 탐색 (콤보 변경, 그리드 클릭 등)
                # ============================================================
                print(f"\n" + "=" * 70)
                print(f"  [7] 이벤트 핸들러 탐색")
                print("=" * 70)

                handlers = driver.execute_script("""
                    var fid = arguments[0];
                    var result = [];

                    try {
                        var app = nexacro.getApplication();
                        var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid]?.form;
                        if (!form) return result;

                        // div_workForm 하위 탐색
                        var wf = form.div_workForm?.form || form;

                        for (var key in wf) {
                            if (typeof wf[key] === 'function' && key.match(/^(div|grd|cb|btn|cal|fn_)/)) {
                                result.push({
                                    name: key,
                                    type: 'handler/function'
                                });
                            }
                        }

                        // 그리드 이벤트 직접 확인
                        var objs = wf.objects || [];
                        for (var i = 0; i < objs.length; i++) {
                            var obj = objs[i];
                            var name = obj.name || '';
                            if (name.startsWith('grd') || name.startsWith('Grid')) {
                                if (obj.oncelldblclick) {
                                    result.push({name: name + '.oncelldblclick', type: 'grid_event', value: 'defined'});
                                }
                                if (obj.oncellclick) {
                                    result.push({name: name + '.oncellclick', type: 'grid_event', value: 'defined'});
                                }
                            }
                        }
                    } catch(e) {
                        result.push({name: 'error', type: 'error', value: e.message});
                    }

                    return result;
                """, target_frame_id)

                for h in handlers:
                    print(f"  {h['type']}: {h['name']}")
                results["handlers"] = handlers

            else:
                print("  [WARN] 새로운 프레임 ID를 식별하지 못했습니다.")
                print("  DOM에서 직접 프레임 ID 패턴 검색...")

                # DOM에서 STGJ 패턴 검색
                dom_frames = driver.execute_script("""
                    var found = [];
                    var els = document.querySelectorAll('[id*="STGJ"]');
                    for (var i = 0; i < Math.min(els.length, 30); i++) {
                        found.push({id: els[i].id, tag: els[i].tagName});
                    }
                    return found;
                """)
                for f in dom_frames:
                    print(f"  DOM: {f['tag']} id={f['id']}")
                results["dom_stgj_elements"] = dom_frames

        # ============================================================
        # 결과 요약
        # ============================================================
        print("\n" + "=" * 70)
        print("  [결과 요약]")
        print("=" * 70)
        print(f"  서브메뉴 수: {len(submenus)}")
        print(f"  새 프레임: {results.get('new_frames', [])}")
        print(f"  열린 탭: {[t.get('text') for t in results.get('open_tabs', [])]}")

        if results.get("frame_structure", {}).get("objects"):
            datasets = []
            combos = []
            grids = []

            def collect_types(items):
                for item in items:
                    t = item.get("type", "")
                    if t == "Dataset":
                        datasets.append(item)
                    elif t == "Combo":
                        combos.append(item)
                    elif t == "Grid":
                        grids.append(item)
                    if item.get("children"):
                        collect_types(item["children"])

            collect_types(results["frame_structure"]["objects"])

            print(f"\n  데이터셋 ({len(datasets)}개):")
            for ds in datasets:
                print(f"    {ds['name']}: rows={ds.get('rowCount', 0)}, cols={ds.get('columns', [])}")

            print(f"\n  콤보 ({len(combos)}개):")
            for cb in combos:
                print(f"    {cb['name']}: value={cb.get('value')} text={cb.get('text')}")

            print(f"\n  그리드 ({len(grids)}개):")
            for grd in grids:
                print(f"    {grd['name']}: binddataset={grd.get('binddataset')}")

        # JSON 저장
        output_path = PROJECT_ROOT / "data" / "waste_slip_structure.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n  결과 저장: {output_path}")

        return results

    except Exception as e:
        print(f"\n[ERROR] 탐색 실패: {e}")
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
    parser = argparse.ArgumentParser(description="BGF 폐기 전표 화면 구조 탐색")
    parser.add_argument("--store-id", default=DEFAULT_STORE_ID, help="매장 ID")
    args = parser.parse_args()

    discover_waste_slip_frame(store_id=args.store_id)
