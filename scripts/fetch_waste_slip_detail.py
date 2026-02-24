"""
2/18~2/19 폐기 전표 상세 품목 탐색

각 전표를 더블클릭하여 상세 페이지의 품목(ITEM_CD, ITEM_NM) 데이터 추출 시도
- 방법1: 그리드 행 더블클릭 -> 새 프레임/탭 탐색
- 방법2: 같은 프레임 내 상세 데이터셋(dsDetail 등) 탐색
- 방법3: dsGs 파라미터로 서버 트랜잭션 직접 호출
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

        # 메뉴 이동
        print(f"\n[1] 통합 전표 조회 이동")
        success = navigate_menu(driver, "검수전표", "통합 전표 조회", FRAME_ID)
        print(f"  이동: {success}")
        time.sleep(2)

        # 필터 설정 + 조회
        print(f"\n[2] 필터: 폐기(10), {FROM_DATE}~{TO_DATE}")
        driver.execute_script("""
            var fid = arguments[0], fromDt = arguments[1], toDt = arguments[2];
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
            var wf = form.div_workForm.form;
            var ds = wf.dsChitDiv;
            for (var r = 0; r < ds.getRowCount(); r++) {
                if (ds.getColumn(r, 'CODE') === '10') {
                    wf.div2.form.divSearch.form.cbChitDiv.set_index(r);
                    break;
                }
            }
            var dsSearch = wf.dsSearch;
            dsSearch.setColumn(0, 'strChitDiv', '10');
            dsSearch.setColumn(0, 'strFromDt', fromDt);
            dsSearch.setColumn(0, 'strToDt', toDt);
        """, FRAME_ID, FROM_DATE, TO_DATE)
        time.sleep(1)

        # 조회 실행
        driver.execute_script("""
            var fid = arguments[0];
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
            if (typeof form.fn_commBtn_10 === 'function') form.fn_commBtn_10();
            else form.div_cmmbtn.form.F_10.click();
        """, FRAME_ID)
        time.sleep(4)
        print("  조회 완료")

        # 전표 목록 확인
        slip_list = driver.execute_script("""
            var fid = arguments[0];
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
            var ds = form.div_workForm.form.dsList;
            var rows = [];
            for (var r = 0; r < ds.getRowCount(); r++) {
                var row = {};
                var cols = ['CHIT_YMD','CHIT_NO','CHIT_ID_NO','ITEM_CNT','CENTER_NM','WONGA_AMT','MAEGA_AMT','CHIT_FLAG'];
                for (var c = 0; c < cols.length; c++) {
                    var val = ds.getColumn(r, cols[c]);
                    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                    row[cols[c]] = val;
                }
                rows.push(row);
            }
            return rows;
        """, FRAME_ID)

        print(f"\n  전표 {len(slip_list)}건")
        for i, s in enumerate(slip_list):
            ymd = s.get("CHIT_YMD", "")
            date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}" if len(ymd) == 8 else ymd
            print(f"  [{i}] {date_str} 전표={s.get('CHIT_NO')} 품목={s.get('ITEM_CNT')}건 센터={s.get('CENTER_NM')}")

        # ============================================================
        # 각 전표 더블클릭 -> 상세 품목 탐색
        # ============================================================
        all_details = []

        for idx in range(len(slip_list)):
            slip = slip_list[idx]
            ymd = slip.get("CHIT_YMD", "")
            date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}" if len(ymd) == 8 else ymd
            chit_no = slip.get("CHIT_NO", "")
            item_cnt = slip.get("ITEM_CNT", 0)

            print(f"\n{'='*70}")
            print(f"  전표 [{idx}] {date_str} {chit_no} (품목 {item_cnt}건) 상세 진입")
            print(f"{'='*70}")

            # 행 선택 + rowposition
            driver.execute_script(f"""
                var fid = arguments[0];
                var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                var ds = form.div_workForm.form.dsList;
                ds.set_rowposition({idx});
            """, FRAME_ID)
            time.sleep(0.5)

            # ActionChains 더블클릭으로 상세 진입
            cell_info = driver.execute_script(f"""
                var fid = arguments[0];
                try {{
                    var cells = document.querySelectorAll('[id*="' + fid + '"][id*="gdList"][id*="body"] div[id*="cell_{idx}_"]');
                    var result = [];
                    for (var i = 0; i < cells.length; i++) {{
                        var r = cells[i].getBoundingClientRect();
                        if (r.width > 50 && r.height > 20 && r.height < 40) {{
                            result.push({{id: cells[i].id, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), w: r.width, h: r.height}});
                        }}
                    }}
                    return result;
                }} catch(e) {{
                    return [{{error: e.message}}];
                }}
            """, FRAME_ID)

            if cell_info and len(cell_info) > 0 and not cell_info[0].get("error"):
                target = cell_info[0]
                print(f"  셀 좌표: x={target['x']}, y={target['y']}")

                # ActionChains 더블클릭
                from selenium.webdriver.common.action_chains import ActionChains
                body = driver.find_element("tag name", "body")
                ActionChains(driver).move_to_element_with_offset(
                    body, target["x"], target["y"]
                ).double_click().perform()
                time.sleep(3)
            else:
                print(f"  셀 좌표 못찾음: {cell_info}")
                # JS 이벤트 대안
                driver.execute_script(f"""
                    var fid = arguments[0];
                    var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                    var wf = form.div_workForm.form;
                    var grid = wf.div2.form.gdList;
                    if (grid && grid.oncelldblclick) {{
                        grid.oncelldblclick._fireEvent(grid, {{cell:0, col:0, row:{idx}, clickitem:'body'}});
                    }}
                """, FRAME_ID)
                time.sleep(3)

            # 상세 데이터 탐색: 모든 프레임의 모든 데이터셋 검색
            detail_data = driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    var fs = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
                    var result = {};

                    for (var fkey in fs) {
                        if (!fs[fkey] || !fs[fkey].form || !fkey.match(/^[A-Z]/)) continue;
                        var form = fs[fkey].form;

                        function searchDs(obj, prefix) {
                            for (var key in obj) {
                                try {
                                    var item = obj[key];
                                    if (!item) continue;

                                    // 데이터셋 확인
                                    if (item.getRowCount && item.getColumn) {
                                        var rc = item.getRowCount();
                                        if (rc > 0) {
                                            var cols = [];
                                            try {
                                                var cc = item.getColCount ? item.getColCount() : 0;
                                                for (var c = 0; c < cc; c++) cols.push(item.getColID(c));
                                            } catch(e) {}

                                            // ITEM_CD 또는 ITEM_NM 컬럼이 있는 데이터셋만
                                            var hasItem = false;
                                            for (var c = 0; c < cols.length; c++) {
                                                if (cols[c].indexOf('ITEM') >= 0 || cols[c].indexOf('PLU') >= 0 ||
                                                    cols[c].indexOf('item') >= 0 || cols[c].indexOf('BARCODE') >= 0) {
                                                    hasItem = true;
                                                    break;
                                                }
                                            }

                                            var dsKey = fkey + '.' + prefix + '.' + key;
                                            if (hasItem || rc <= 50) {
                                                var samples = [];
                                                for (var r = 0; r < Math.min(rc, 20); r++) {
                                                    var row = {};
                                                    for (var c = 0; c < Math.min(cols.length, 30); c++) {
                                                        var val = item.getColumn(r, cols[c]);
                                                        if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                                                        row[cols[c]] = val;
                                                    }
                                                    samples.push(row);
                                                }
                                                result[dsKey] = {rows: rc, cols: cols, hasItem: hasItem, samples: samples};
                                            } else {
                                                result[dsKey] = {rows: rc, cols: cols, hasItem: hasItem};
                                            }
                                        }
                                    }

                                    // div 하위 탐색
                                    if (item.form && key.startsWith('div')) {
                                        searchDs(item.form, prefix + '.' + key + '.form');
                                    }
                                } catch(e) {}
                            }
                        }

                        searchDs(form, 'form');

                        // div_workForm 하위도 탐색
                        if (form.div_workForm && form.div_workForm.form) {
                            searchDs(form.div_workForm.form, 'form.div_workForm.form');
                        }
                    }
                    return result;
                } catch(e) {
                    return {error: e.message};
                }
            """)

            if detail_data and not detail_data.get("error"):
                found_items = False
                for dsKey, dsInfo in detail_data.items():
                    if dsInfo.get("hasItem"):
                        found_items = True
                        print(f"\n  *** ITEM 데이터셋 발견: {dsKey}")
                        print(f"      rows={dsInfo['rows']} cols={dsInfo['cols']}")
                        if dsInfo.get("samples"):
                            for si, sample in enumerate(dsInfo["samples"]):
                                print(f"      [{si}] {json.dumps(sample, ensure_ascii=False, default=str)}")

                            all_details.append({
                                "slip_date": date_str,
                                "slip_no": chit_no,
                                "slip_item_cnt": item_cnt,
                                "dataset": dsKey,
                                "items": dsInfo["samples"],
                            })

                if not found_items:
                    # ITEM 없더라도 새로 나타난 데이터셋 출력
                    print(f"  ITEM 데이터셋 없음. 전체 데이터셋:")
                    for dsKey, dsInfo in detail_data.items():
                        rc = dsInfo.get("rows", 0)
                        cols = dsInfo.get("cols", [])
                        print(f"    {dsKey}: rows={rc} cols={cols[:10]}{'...' if len(cols)>10 else ''}")
                        if dsInfo.get("samples") and rc <= 5:
                            for s in dsInfo["samples"][:2]:
                                print(f"      {json.dumps(s, ensure_ascii=False, default=str)[:200]}")
            else:
                print(f"  탐색 오류: {detail_data}")

            # 탭 닫기 (상세 페이지가 열렸을 수 있으므로)
            driver.execute_script("""
                var tabs = document.querySelectorAll('[id*="tab_openList"][id*="closebutton"]');
                if (tabs.length > 1) {
                    tabs[tabs.length - 1].click();
                }
            """)
            time.sleep(1)

        # 최종 결과
        print(f"\n\n{'='*80}")
        print(f"  최종 결과: 상세 품목 데이터")
        print(f"{'='*80}")
        if all_details:
            for detail in all_details:
                print(f"\n  전표 {detail['slip_date']} {detail['slip_no']} ({detail['slip_item_cnt']}건)")
                print(f"  데이터셋: {detail['dataset']}")
                for item in detail["items"]:
                    print(f"    {json.dumps(item, ensure_ascii=False, default=str)}")
        else:
            print("  상세 품목 데이터를 찾지 못했습니다.")

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
