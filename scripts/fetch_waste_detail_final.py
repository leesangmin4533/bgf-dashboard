"""
폐기 전표 상세 품목 추출 (최종 버전)

문제 해결:
- 팝업이 닫히지 않고 재사용되어 dsSearch가 갱신 안됨
- 해결: 팝업을 확실히 닫고(close + destroy), dsGs를 올바르게 갱신 후 새로 open
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
        # 각 전표 상세 품목 추출
        # ============================================================
        all_items = []

        for idx in range(len(slip_list)):
            slip = slip_list[idx]
            ymd = slip.get("CHIT_YMD", "")
            date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}" if len(ymd) == 8 else ymd
            chit_no = slip.get("CHIT_NO", "")
            chit_id = slip.get("CHIT_ID", "")
            item_cnt = slip.get("ITEM_CNT", 0) or 0

            print(f"\n  [{idx}] {date_str} 전표={chit_no} 품목={item_cnt}건", end=" ")

            # 1) dsGs 파라미터를 정확히 설정 + 팝업 열기
            items = driver.execute_script(f"""
                try {{
                    var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
                    var wf = form.div_workForm.form;
                    var idx = {idx};

                    // rowposition 설정
                    wf.dsList.set_rowposition(idx);
                    var nRow = idx;

                    // gvVar 설정
                    wf.gvVar04 = String(wf.dsList.getColumn(nRow, "CHIT_ID") || '');
                    wf.gvVar05 = String(wf.dsList.getColumn(nRow, "CHIT_ID_NM") || '');
                    wf.gvVar06 = String(wf.dsList.getColumn(nRow, "NAP_PLAN_YMD") || '');
                    wf.gvVar07 = String(wf.dsList.getColumn(nRow, "CHIT_NO") || '');
                    wf.gvVar08 = String(wf.dsList.getColumn(nRow, "CENTER_NM") || '');
                    wf.gvVar09 = String(wf.dsList.getColumn(nRow, "CHIT_ID_NO") || '');
                    wf.gvVar10 = String(wf.dsList.getColumn(nRow, "LSTORE_NM") || '');
                    wf.gvVar11 = String(wf.dsList.getColumn(nRow, "RSTORE_NM") || '');
                    wf.gvVar12 = String(wf.dsList.getColumn(nRow, "LARGE_CD") || '');
                    wf.gvVar13 = String(wf.dsList.getColumn(nRow, "CHIT_FLAG") || '');
                    wf.gvVar14 = String(wf.dsList.getColumn(nRow, "RET_CHIT_NO") || '');
                    wf.gvVar15 = String(wf.dsList.getColumn(nRow, "MAEIP_CHIT_NO") || '');
                    wf.gvVar18 = String(wf.dsList.getColumn(nRow, "CHIT_YMD") || '');
                    wf.gvVar20 = "Y";

                    // dsGs 파라미터 갱신
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

                    return {{
                        gvVar07: wf.gvVar07,
                        gvVar09: wf.gvVar09,
                        gvVar18: wf.gvVar18,
                        gvVar04: wf.gvVar04
                    }};
                }} catch(e) {{
                    return {{error: e.message}};
                }}
            """)

            if items and items.get("error"):
                print(f"dsGs 설정 오류: {items['error']}")
                continue

            # 2) 기존 팝업 확실히 닫기
            driver.execute_script("""
                try {
                    // nexacro 팝업 프레임 닫기
                    var popupframes = nexacro.getPopupFrames(nexacro.getApplication().mainframe);
                    if (popupframes) {
                        for (var i = popupframes.length - 1; i >= 0; i--) {
                            try {
                                popupframes[i].close();
                                popupframes[i].destroy();
                            } catch(e) {}
                        }
                    }

                    // _modal_frame_stack 비우기
                    var win = nexacro.getApplication().mainframe._getWindow();
                    if (win && win._modal_frame_stack) {
                        while (win._modal_frame_stack.length > 0) {
                            try {
                                var modal = win._modal_frame_stack.pop();
                                if (modal && modal.frame) {
                                    modal.frame.close();
                                    modal.frame.destroy();
                                }
                            } catch(e) {}
                        }
                    }
                } catch(e) {}
            """)
            time.sleep(0.5)

            # 3) 팝업 열기
            driver.execute_script(f"""
                var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
                var wf = form.div_workForm.form;
                var oArg = {{}};
                oArg.dsArg = wf.dsGs;
                oArg.strStoreCd = wf.strStoreCd;
                oArg.strStoreNm = wf.strStoreNm;
                wf.gfn_openPopup("STGJ020_P1", "GJ::STGJ020_P1.xfdl", oArg, "fn_popupCallback", {{}});
            """)
            time.sleep(4)  # 서버 트랜잭션 대기

            # 4) 팝업에서 dsListType1 데이터 추출
            detail = driver.execute_script("""
                try {
                    var popupframes = nexacro.getPopupFrames(nexacro.getApplication().mainframe);
                    if (!popupframes || popupframes.length === 0) return {error: 'no popup'};

                    var popup = popupframes[popupframes.length - 1];
                    if (!popup || !popup.form) return {error: 'no popup form'};

                    var pf = popup.form;
                    var result = {datasets: {}};

                    // dsListType0~4 모두 확인
                    for (var t = 0; t <= 4; t++) {
                        var dsName = 'dsListType' + t;
                        var ds = pf[dsName];
                        if (ds && ds.getRowCount && ds.getRowCount() > 0) {
                            var rows = [];
                            var cc = ds.getColCount();
                            var cols = [];
                            for (var c = 0; c < cc; c++) cols.push(ds.getColID(c));

                            for (var r = 0; r < ds.getRowCount(); r++) {
                                var row = {};
                                for (var c = 0; c < cols.length; c++) {
                                    var val = ds.getColumn(r, cols[c]);
                                    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                                    row[cols[c]] = val;
                                }
                                rows.push(row);
                            }
                            result.datasets[dsName] = {rows: rows, cols: cols};
                        }
                    }

                    // dsSearch 확인 (디버깅)
                    if (pf.dsSearch && pf.dsSearch.getRowCount() > 0) {
                        result.dsSearch = {};
                        var cc = pf.dsSearch.getColCount();
                        for (var c = 0; c < cc; c++) {
                            var colId = pf.dsSearch.getColID(c);
                            result.dsSearch[colId] = pf.dsSearch.getColumn(0, colId);
                        }
                    }

                    return result;
                } catch(e) {
                    return {error: e.message};
                }
            """)

            if detail and not detail.get("error"):
                datasets = detail.get("datasets", {})
                ds_search = detail.get("dsSearch", {})

                for dsName, dsData in datasets.items():
                    rows = dsData.get("rows", [])
                    if rows:
                        print(f"-> {dsName}: {len(rows)}건")
                        for r in rows:
                            item_cd = r.get("ITEM_CD", "")
                            item_nm = r.get("ITEM_NM", "")
                            qty = r.get("QTY", r.get("NAP_QTY", ""))
                            wonga = r.get("WONGA_AMT", 0) or 0
                            maega = r.get("MAEGA_AMT", 0) or 0
                            cust = r.get("CUST_NM", "")
                            large_nm = r.get("LARGE_NM", "")
                            print(f"      ITEM_CD={item_cd} ITEM_NM={item_nm} QTY={qty} "
                                  f"원가={wonga} 매가={maega} 분류={large_nm} 업체={cust}")
                            all_items.append({
                                "slip_date": date_str,
                                "slip_no": chit_no,
                                "item_cd": item_cd,
                                "item_nm": item_nm,
                                "qty": qty,
                                "wonga_amt": wonga,
                                "maega_amt": maega,
                                "large_nm": large_nm,
                                "cust_nm": cust,
                            })

                if not datasets:
                    print(f"-> 데이터 없음 (dsSearch: {ds_search})")
            else:
                print(f"-> 오류: {detail}")

            # 5) 팝업 닫기
            driver.execute_script("""
                try {
                    var popupframes = nexacro.getPopupFrames(nexacro.getApplication().mainframe);
                    if (popupframes) {
                        for (var i = popupframes.length - 1; i >= 0; i--) {
                            try {
                                popupframes[i].close();
                                popupframes[i].destroy();
                            } catch(e) {}
                        }
                    }
                } catch(e) {}
            """)
            time.sleep(1)

        # ============================================================
        # 최종 결과
        # ============================================================
        print(f"\n\n{'='*80}")
        print(f"  최종 결과: 2/{FROM_DATE[6:8]}~2/{TO_DATE[6:8]} 폐기 전표 상세 품목")
        print(f"{'='*80}")

        if all_items:
            # 날짜별 그룹
            by_date = {}
            for item in all_items:
                d = item["slip_date"]
                if d not in by_date:
                    by_date[d] = []
                by_date[d].append(item)

            for date_str in sorted(by_date.keys()):
                items = by_date[date_str]
                total_wonga = sum(i.get("wonga_amt", 0) or 0 for i in items)
                total_maega = sum(i.get("maega_amt", 0) or 0 for i in items)
                print(f"\n  === {date_str} === {len(items)}건 | 원가 {total_wonga:,.0f}원 | 매가 {total_maega:,.0f}원")
                for i, item in enumerate(items):
                    print(f"    {i+1}. [{item['item_cd']}] {item['item_nm']} "
                          f"x{item['qty']} 원가={item['wonga_amt']:,.0f} 매가={item['maega_amt']:,.0f} "
                          f"({item['large_nm']}) 전표={item['slip_no']}")

            print(f"\n  총 {len(all_items)}건 품목")
            print(f"  총 원가: {sum(i.get('wonga_amt',0) or 0 for i in all_items):,.0f}원")
            print(f"  총 매가: {sum(i.get('maega_amt',0) or 0 for i in all_items):,.0f}원")
        else:
            print(f"  품목 없음")

        print(f"{'='*80}")

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
