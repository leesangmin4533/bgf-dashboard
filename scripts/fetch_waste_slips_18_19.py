"""
2/18~2/19 폐기 전표 수집 테스트
BGF 사이트 로그인 -> 통합 전표 조회 -> 전표구분=폐기(10) -> 조회 -> 전체 덤프
"""

import sys
import io
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

        # 메뉴 이동
        print(f"\n[1] 통합 전표 조회 이동")
        success = navigate_menu(driver, "검수전표", "통합 전표 조회", FRAME_ID)
        print(f"  이동: {success}")
        time.sleep(2)

        # 전표구분=폐기(10) + 날짜 설정 + 조회
        print(f"\n[2] 필터 설정: 폐기(10), {FROM_DATE}~{TO_DATE}")
        filter_result = driver.execute_script("""
            var fid = arguments[0];
            var fromDt = arguments[1];
            var toDt = arguments[2];
            try {
                var app = nexacro.getApplication();
                var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                var wf = form.div_workForm.form;

                // 폐기 CODE=10 인덱스 찾기
                var ds = wf.dsChitDiv;
                var targetIdx = -1;
                for (var r = 0; r < ds.getRowCount(); r++) {
                    if (ds.getColumn(r, 'CODE') === '10') {
                        targetIdx = r;
                        break;
                    }
                }
                if (targetIdx < 0) return {error: 'CODE 10 not found'};

                // 콤보 설정
                var cb = wf.div2.form.divSearch.form.cbChitDiv;
                cb.set_index(targetIdx);

                // dsSearch 파라미터
                var dsSearch = wf.dsSearch;
                dsSearch.setColumn(0, 'strChitDiv', '10');
                dsSearch.setColumn(0, 'strFromDt', fromDt);
                dsSearch.setColumn(0, 'strToDt', toDt);

                return {
                    success: true,
                    comboText: cb.text,
                    fromDt: fromDt,
                    toDt: toDt
                };
            } catch(e) {
                return {error: e.message};
            }
        """, FRAME_ID, FROM_DATE, TO_DATE)
        print(f"  결과: {filter_result}")

        # 조회 실행
        print("\n[3] 조회 실행")
        search_result = driver.execute_script("""
            var fid = arguments[0];
            try {
                var form = nexacro.getApplication()
                    .mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                if (typeof form.fn_commBtn_10 === 'function') {
                    form.fn_commBtn_10();
                    return {method: 'fn_commBtn_10', success: true};
                }
                var cmmbtn = form.div_cmmbtn;
                if (cmmbtn && cmmbtn.form && cmmbtn.form.F_10) {
                    cmmbtn.form.F_10.click();
                    return {method: 'F_10', success: true};
                }
                return {error: 'no search method'};
            } catch(e) {
                return {error: e.message};
            }
        """, FRAME_ID)
        print(f"  결과: {search_result}")
        time.sleep(4)

        # 전체 폐기 전표 목록 덤프
        print(f"\n{'='*80}")
        print(f"  [4] 폐기 전표 전체 목록 ({FROM_DATE}~{TO_DATE})")
        print(f"{'='*80}")

        waste_list = driver.execute_script("""
            var fid = arguments[0];
            try {
                var form = nexacro.getApplication()
                    .mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
                var ds = form.div_workForm.form.dsList;
                if (!ds) return {error: 'dsList not found'};

                var cols = ['CHIT_FLAG', 'CHIT_ID', 'CHIT_ID_NM', 'CHIT_YMD', 'CHIT_NO',
                            'CHIT_ID_NO', 'ITEM_CNT', 'CENTER_CD', 'CENTER_NM',
                            'WONGA_AMT', 'MAEGA_AMT', 'NAP_PLAN_YMD', 'CRE_YMDHMS', 'CONF_ID'];

                var rows = [];
                for (var r = 0; r < ds.getRowCount(); r++) {
                    var row = {};
                    for (var c = 0; c < cols.length; c++) {
                        var val = ds.getColumn(r, cols[c]);
                        if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                        row[cols[c]] = val;
                    }
                    rows.push(row);
                }
                return {rows: rows, count: ds.getRowCount()};
            } catch(e) {
                return {error: e.message};
            }
        """, FRAME_ID)

        if not waste_list or waste_list.get("error"):
            print(f"  오류: {waste_list}")
            return

        rows = waste_list.get("rows", [])
        print(f"\n  총 {waste_list['count']}건\n")

        # 날짜별 그룹핑
        by_date = {}
        for row in rows:
            ymd = row.get("CHIT_YMD", "")
            if ymd not in by_date:
                by_date[ymd] = []
            by_date[ymd].append(row)

        for ymd in sorted(by_date.keys()):
            date_rows = by_date[ymd]
            date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}" if len(ymd) == 8 else ymd
            total_items = sum(r.get("ITEM_CNT") or 0 for r in date_rows)
            total_wonga = sum(r.get("WONGA_AMT") or 0 for r in date_rows)
            total_maega = sum(r.get("MAEGA_AMT") or 0 for r in date_rows)

            print(f"  === {date_str} === 전표 {len(date_rows)}건 | 품목 {total_items}건 | 원가 {total_wonga:,.0f}원 | 매가 {total_maega:,.0f}원")

            for i, row in enumerate(date_rows):
                chit_no = row.get("CHIT_NO", "")
                items = row.get("ITEM_CNT", 0) or 0
                center = row.get("CENTER_NM", "")
                wonga = row.get("WONGA_AMT", 0) or 0
                maega = row.get("MAEGA_AMT", 0) or 0
                conf = row.get("CONF_ID", "")
                flag = row.get("CHIT_FLAG", "")
                cre = row.get("CRE_YMDHMS", "")

                print(f"    {i+1:2d}. 전표번호={chit_no} 품목={items}건 "
                      f"센터={center} 원가={wonga:,.0f} 매가={maega:,.0f} "
                      f"구분={flag} 확정={conf}")

            print()

        # 요약
        print(f"{'='*80}")
        print(f"  요약: 총 {len(rows)}건 전표")
        total_all_items = sum(r.get("ITEM_CNT") or 0 for r in rows)
        total_all_wonga = sum(r.get("WONGA_AMT") or 0 for r in rows)
        total_all_maega = sum(r.get("MAEGA_AMT") or 0 for r in rows)
        print(f"  총 품목: {total_all_items}건")
        print(f"  총 원가: {total_all_wonga:,.0f}원")
        print(f"  총 매가: {total_all_maega:,.0f}원")
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
