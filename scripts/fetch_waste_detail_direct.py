"""
폐기 전표 상세 품목 추출 - 서버 트랜잭션 직접 호출

이전 시도 문제: 팝업을 열어도 첫 번째 전표 데이터만 반복 (캐시/재사용)
해결: 팝업 내부의 서버 트랜잭션(fn_search 등)을 직접 호출하여 전표별 데이터 로드

1) 첫 번째 팝업을 열어서 트랜잭션 함수/패턴 분석
2) 팝업 form의 fn_search/fn_init 등의 서비스 호출 패턴 파악
3) 각 전표에 대해 직접 트랜잭션 호출
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
                            'NAP_PLAN_YMD','CHIT_FLAG'];
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
        # [2] 첫 번째 전표에 대해 팝업 열고 함수 분석
        # ============================================================
        print(f"\n[2] 팝업 열고 초기화 함수 분석")

        # dsGs 설정
        driver.execute_script(f"""
            var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
            var wf = form.div_workForm.form;
            wf.dsList.set_rowposition(0);
            wf.fn_moveDetailPage();
        """)
        time.sleep(5)

        # 팝업 form의 함수 목록 확인
        popup_funcs = driver.execute_script("""
            try {
                var popupframes = nexacro.getPopupFrames(nexacro.getApplication().mainframe);
                if (!popupframes || popupframes.length === 0) return {error: 'no popup'};
                var popup = popupframes[popupframes.length - 1];
                if (!popup || !popup.form) return {error: 'no popup form'};

                var pf = popup.form;
                var result = {funcs: [], form_id: popup.id || ''};

                // 모든 함수 목록
                for (var key in pf) {
                    if (typeof pf[key] === 'function') {
                        var name = key;
                        var src = '';
                        try { src = pf[key].toString().substring(0, 500); } catch(e) {}

                        // 관련 함수만 필터
                        if (name.indexOf('fn_') === 0 || name.indexOf('init') >= 0 ||
                            name.indexOf('Init') >= 0 || name.indexOf('search') >= 0 ||
                            name.indexOf('Search') >= 0 || name.indexOf('load') >= 0 ||
                            name.indexOf('Load') >= 0 || name.indexOf('trans') >= 0 ||
                            name.indexOf('Trans') >= 0 || name.indexOf('callback') >= 0 ||
                            name.indexOf('Callback') >= 0) {
                            result.funcs.push({name: name, src: src});
                        }
                    }
                }

                return result;
            } catch(e) {
                return {error: e.message};
            }
        """)

        if popup_funcs.get("error"):
            print(f"  오류: {popup_funcs['error']}")
            return

        print(f"  팝업 ID: {popup_funcs.get('form_id')}")
        print(f"  관련 함수 {len(popup_funcs.get('funcs', []))}개:")
        for f in popup_funcs.get('funcs', []):
            print(f"\n  === {f['name']} ===")
            # 소스 줄바꿈
            for line in f['src'].split(';')[:10]:
                line = line.strip()
                if line:
                    print(f"    {line};")

        # 팝업 닫기
        driver.execute_script("""
            var popupframes = nexacro.getPopupFrames(nexacro.getApplication().mainframe);
            if (popupframes) {
                for (var i = popupframes.length - 1; i >= 0; i--) {
                    try { popupframes[i].close(); popupframes[i].destroy(); } catch(e) {}
                }
            }
        """)
        time.sleep(1)

        # ============================================================
        # [3] 각 전표에 대해 직접 서버 트랜잭션 호출
        # ============================================================
        print(f"\n\n[3] 각 전표에 대해 팝업 서버 트랜잭션 직접 호출")

        all_items = []

        for idx in range(len(slip_list)):
            slip = slip_list[idx]
            ymd = slip.get("CHIT_YMD", "")
            date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}" if len(ymd) == 8 else ymd
            chit_no = slip.get("CHIT_NO", "")
            item_cnt = slip.get("ITEM_CNT", 0) or 0

            print(f"\n  [{idx}] {date_str} 전표={chit_no} 품목={item_cnt}건", end=" ")

            # dsGs 설정 후 fn_moveDetailPage 호출 (매번 새로 설정)
            driver.execute_script(f"""
                var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
                var wf = form.div_workForm.form;
                wf.dsList.set_rowposition({idx});
                // dsGs 직접 설정 (fn_moveDetailPage 로직 재현)
                var nRow = {idx};
                var fields = ['CHIT_ID','CHIT_ID_NM','NAP_PLAN_YMD','CHIT_NO','CENTER_NM',
                              'CHIT_ID_NO','LSTORE_NM','RSTORE_NM','LARGE_CD','CHIT_FLAG',
                              'RET_CHIT_NO','MAEIP_CHIT_NO','CHIT_YMD'];
                var gvNums = ['04','05','06','07','08','09','10','11','12','13','14','15','18'];
                for (var i = 0; i < fields.length; i++) {{
                    var val = String(wf.dsList.getColumn(nRow, fields[i]) || '');
                    wf['gvVar' + gvNums[i]] = val;
                    wf.dsGs.setColumn(0, 'gvVar' + gvNums[i], val);
                }}
                wf.gvVar20 = "Y";
            """)
            time.sleep(0.3)

            # 팝업 열기
            driver.execute_script(f"""
                var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
                var wf = form.div_workForm.form;
                var oArg = {{}};
                oArg.dsArg = wf.dsGs;
                oArg.strStoreCd = wf.strStoreCd;
                oArg.strStoreNm = wf.strStoreNm;
                wf.gfn_openPopup("STGJ020_P1", "GJ::STGJ020_P1.xfdl", oArg, "fn_popupCallback", {{}});
            """)
            time.sleep(4)

            # 팝업에서 dsSearch를 확인하고 필요시 fn_search 직접 호출
            detail = driver.execute_script(f"""
                try {{
                    var popupframes = nexacro.getPopupFrames(nexacro.getApplication().mainframe);
                    if (!popupframes || popupframes.length === 0) return {{error: 'no popup'}};
                    var popup = popupframes[popupframes.length - 1];
                    if (!popup || !popup.form) return {{error: 'no form'}};
                    var pf = popup.form;
                    var result = {{}};

                    // dsSearch 확인
                    if (pf.dsSearch) {{
                        result.dsSearch = {{}};
                        var cc = pf.dsSearch.getColCount();
                        for (var c = 0; c < cc; c++) {{
                            var colId = pf.dsSearch.getColID(c);
                            result.dsSearch[colId] = String(pf.dsSearch.getColumn(0, colId) || '');
                        }}
                    }}

                    // dsSearch의 strChitNoList가 현재 전표번호와 다르면 수정 + 재조회
                    var currentChitNo = '{chit_no}';
                    var currentList = pf.dsSearch ? String(pf.dsSearch.getColumn(0, 'strChitNoList') || '') : '';

                    if (currentList.indexOf(currentChitNo) < 0) {{
                        // 전표번호 업데이트
                        pf.dsSearch.setColumn(0, 'strChitNoList', "('" + currentChitNo + "')");
                        pf.dsSearch.setColumn(0, 'strChitYmd', '{ymd}');

                        // fn_search 호출 (팝업 내 조회 함수)
                        if (typeof pf.fn_search === 'function') {{
                            pf.fn_search();
                            result.requeried = 'fn_search';
                        }} else if (typeof pf.fn_init === 'function') {{
                            pf.fn_init();
                            result.requeried = 'fn_init';
                        }}
                    }}

                    return result;
                }} catch(e) {{
                    return {{error: e.message}};
                }}
            """)

            # 재조회 했으면 추가 대기
            if detail and detail.get("requeried"):
                print(f"(재조회: {detail['requeried']})", end=" ")
                time.sleep(3)

            # 데이터 추출
            items = driver.execute_script("""
                try {
                    var popupframes = nexacro.getPopupFrames(nexacro.getApplication().mainframe);
                    if (!popupframes || popupframes.length === 0) return {error: 'no popup'};
                    var popup = popupframes[popupframes.length - 1];
                    if (!popup || !popup.form) return {error: 'no form'};
                    var pf = popup.form;

                    var result = {items: [], dsSearch: {}};

                    // dsSearch
                    if (pf.dsSearch) {
                        var cc = pf.dsSearch.getColCount();
                        for (var c = 0; c < cc; c++) {
                            var colId = pf.dsSearch.getColID(c);
                            result.dsSearch[colId] = String(pf.dsSearch.getColumn(0, colId) || '');
                        }
                    }

                    // dsListType0~4
                    for (var t = 0; t <= 4; t++) {
                        var ds = pf['dsListType' + t];
                        if (ds && ds.getRowCount && ds.getRowCount() > 0) {
                            var cc = ds.getColCount();
                            var cols = [];
                            for (var c = 0; c < cc; c++) cols.push(ds.getColID(c));

                            for (var r = 0; r < ds.getRowCount(); r++) {
                                var row = {_dsType: t};
                                for (var c = 0; c < cols.length; c++) {
                                    var val = ds.getColumn(r, cols[c]);
                                    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                                    row[cols[c]] = val;
                                }
                                result.items.push(row);
                            }
                        }
                    }

                    return result;
                } catch(e) {
                    return {error: e.message};
                }
            """)

            if items and not items.get("error"):
                ds_search = items.get("dsSearch", {})
                item_list = items.get("items", [])

                if item_list:
                    print(f"-> {len(item_list)}건 (dsSearch.chitNoList={ds_search.get('strChitNoList','')})")
                    for r in item_list:
                        item_cd = r.get("ITEM_CD", "")
                        item_nm = r.get("ITEM_NM", "")
                        qty = r.get("QTY", r.get("NAP_QTY", ""))
                        wonga = r.get("WONGA_AMT", 0) or 0
                        maega = r.get("MAEGA_AMT", 0) or 0
                        large_nm = r.get("LARGE_NM", "")
                        cust = r.get("CUST_NM", "")
                        print(f"      [{item_cd}] {item_nm} x{qty} 원가={wonga} 매가={maega} ({large_nm})")
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
                else:
                    print(f"-> 품목 0건 (dsSearch: {ds_search})")
            else:
                print(f"-> 오류: {items}")

            # 팝업 닫기
            driver.execute_script("""
                var popupframes = nexacro.getPopupFrames(nexacro.getApplication().mainframe);
                if (popupframes) {
                    for (var i = popupframes.length - 1; i >= 0; i--) {
                        try { popupframes[i].close(); popupframes[i].destroy(); } catch(e) {}
                    }
                }
            """)
            time.sleep(1)

        # ============================================================
        # 최종 결과
        # ============================================================
        print(f"\n\n{'='*80}")
        print(f"  최종 결과: 2/{FROM_DATE[6:8]}~2/{TO_DATE[6:8]} 폐기 전표 상세 품목")
        print(f"{'='*80}")

        if all_items:
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
                    w = item.get('wonga_amt', 0) or 0
                    m = item.get('maega_amt', 0) or 0
                    print(f"    {i+1}. [{item['item_cd']}] {item['item_nm']} "
                          f"x{item['qty']} 원가={w:,.0f} 매가={m:,.0f} "
                          f"({item['large_nm']}) 전표={item['slip_no']}")

            total_items = len(all_items)
            total_wonga = sum(i.get('wonga_amt', 0) or 0 for i in all_items)
            total_maega = sum(i.get('maega_amt', 0) or 0 for i in all_items)
            unique_items = len(set(i['item_cd'] for i in all_items))
            print(f"\n  총 {total_items}건 품목 (고유 상품 {unique_items}종)")
            print(f"  총 원가: {total_wonga:,.0f}원")
            print(f"  총 매가: {total_maega:,.0f}원")
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
