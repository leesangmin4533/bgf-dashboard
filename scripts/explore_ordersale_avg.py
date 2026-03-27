"""
dsOrderSaleBind.AVG 탐구 스크립트 (v3)

Grid 셀 입력 + Enter 방식으로 실제 상품 검색 후
dsOrderSale + dsOrderSaleBind 데이터를 읽어옵니다.

핵심: fn_search()가 아닌, collect_for_item 패턴
(gdList 마지막행 → 상품코드 입력 → Enter → selSearch 트리거)

Usage:
    python scripts/explore_ordersale_avg.py
    python scripts/explore_ordersale_avg.py --items 8801043015653
"""

import sys
import os
import json
import time
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.sales_analyzer import SalesAnalyzer
from src.order.order_executor import OrderExecutor
from src.settings.timing import SA_LOGIN_WAIT, SA_POPUP_CLOSE_WAIT
from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_ITEMS = [
    "8801043015653",  # 농심 육개장사발면 (라면 032)
    "8800279670889",  # 3XL베이컨햄마요김치 (도시락 002)
    "8801056191115",  # 코카콜라600ML (음료 043)
]

FRAME_ID = "STBJ030_M0"


def search_item_via_grid(driver, item_cd: str) -> dict:
    """
    gdList 그리드 셀 입력 + Enter로 상품 검색 (collect_for_item 패턴)

    Returns:
        {'success': True, 'targetRow': N} 또는 {'error': '...'}
    """
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.common.action_chains import ActionChains

    # 1. 마지막 행 상품명 셀 활성화
    result = driver.execute_script("""
        try {
            const app = nexacro.getApplication?.();
            const stbjForm = app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet?.[arguments[0]]?.form;
            const workForm = stbjForm?.div_workForm?.form?.div_work_01?.form;

            if (!workForm?.gdList?._binddataset) return {error: 'no dataset'};

            const ds = workForm.gdList._binddataset;
            const grid = workForm.gdList;
            let rowCount = ds.getRowCount();
            let targetRow = 0;

            if (rowCount === 0) {
                ds.addRow();
                targetRow = 0;
            } else {
                const lastRow = rowCount - 1;
                const existingCd = ds.getColumn(lastRow, 'ITEM_CD') || '';
                if (existingCd && existingCd.length > 0) {
                    ds.addRow();
                    targetRow = ds.getRowCount() - 1;
                } else {
                    targetRow = lastRow;
                }
            }

            ds.set_rowposition(targetRow);
            if (grid.setFocus) grid.setFocus();
            if (grid.setCellPos) grid.setCellPos(1);
            if (grid.showEditor) grid.showEditor(true);

            // column 1 더블클릭
            const cellId = 'gridrow_' + targetRow + '.cell_' + targetRow + '_1';
            const cell = document.querySelector('[id*="gdList"][id*="' + cellId + '"]');
            if (cell) {
                const r = cell.getBoundingClientRect();
                const o = {bubbles: true, cancelable: true, view: window,
                    clientX: r.left + r.width/2, clientY: r.top + r.height/2};
                cell.dispatchEvent(new MouseEvent('mousedown', o));
                cell.dispatchEvent(new MouseEvent('mouseup', o));
                cell.dispatchEvent(new MouseEvent('click', o));
                cell.dispatchEvent(new MouseEvent('dblclick', o));
            }

            return {success: true, targetRow: targetRow};
        } catch(e) {
            return {error: e.toString()};
        }
    """, FRAME_ID)

    if not result or result.get('error'):
        return result or {'error': 'js_failed'}

    time.sleep(0.5)

    # 팝업 닫기
    driver.execute_script("""
        const popups = document.querySelectorAll('[id*="CallItem"][id*="Popup"], [id*="fn_Item"]');
        for (const popup of popups) {
            if (popup.offsetParent !== null) {
                const closeBtn = popup.querySelector('[id*="btn_close"], [id*="Close"]');
                if (closeBtn) closeBtn.click();
                else document.dispatchEvent(new KeyboardEvent('keydown',
                    {key: 'Escape', keyCode: 27, bubbles: true}));
            }
        }
    """)
    time.sleep(0.3)

    # column 1 input에 포커스
    target_row = result.get('targetRow', 0)
    driver.execute_script("""
        const pattern = 'cell_' + arguments[0] + '_1';
        const inputs = document.querySelectorAll(
            '[id*="gdList"][id*="' + pattern + '"][id*="celledit:input"]');
        if (inputs.length > 0) inputs[0].focus();
    """, target_row)
    time.sleep(0.3)

    # 2. 상품코드 입력
    actions = ActionChains(driver)
    actions.key_down(Keys.CONTROL).send_keys('a').key_up(Keys.CONTROL)
    actions.send_keys(Keys.DELETE)
    actions.send_keys(item_cd)
    actions.perform()
    time.sleep(0.5)

    # 3. Enter 키로 상품 검색
    actions = ActionChains(driver)
    actions.send_keys(Keys.ENTER)
    actions.perform()
    time.sleep(4)  # selSearch 로딩 대기

    # Alert 처리 (발주 불가여도 데이터 읽기 계속)
    alert_text = None
    try:
        alert = driver.switch_to.alert
        alert_text = alert.text
        alert.accept()
        print(f"    ⚠️ Alert: {alert_text}")
        time.sleep(1)
    except Exception:
        pass

    # Alert 후에도 데이터 읽기 시도 (발주 불가여도 이력은 있을 수 있음)
    if alert_text and '상품정보가 없습니다' in alert_text:
        return {'error': f'item_not_found: {alert_text}'}

    return {'success': True, 'targetRow': target_row, 'alert': alert_text}


def read_nexacro_datasets(driver) -> dict:
    """dsItem + dsOrderSale + dsOrderSaleBind 한꺼번에 읽기"""
    raw = driver.execute_script("""
    try {
        var app = nexacro.getApplication();
        var sf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[arguments[0]].form;
        var wf = sf.div_workForm.form.div_work_01.form;

        var R = {};

        // ── dsItem (현재 검색된 상품) ──
        var ds = wf.gdList ? wf.gdList._binddataset : (wf.dsItem || wf.dsGeneralGrid);
        if (ds) {
            var cnt = ds.getRowCount();
            var lastRow = cnt > 0 ? cnt - 1 : -1;
            R.dsItem = {rowCount: cnt};
            if (lastRow >= 0) {
                R.currentItem = {};
                var fields = ['ITEM_CD','ITEM_NM','NOW_QTY','PYUN_QTY','ORD_UNIT_QTY',
                    'ORD_MUL_QTY','PITEM_ID','EXPIRE_DAY','MID_NM'];
                for (var fi=0; fi<fields.length; fi++) {
                    try {
                        var v = ds.getColumn(lastRow, fields[fi]);
                        R.currentItem[fields[fi]] = v!==null&&v!==undefined ? String(v) : '';
                    } catch(e) { R.currentItem[fields[fi]] = ''; }
                }
            }
        }

        // ── dsOrderSale (일별 이력) ──
        var dsOS = wf.dsOrderSale;
        if (dsOS) {
            var osCols = [];
            for (var c=0; c<dsOS.getColCount(); c++) osCols.push(dsOS.getColID(c));
            var osRows = [];
            for (var r=0; r<Math.min(dsOS.getRowCount(), 100); r++) {
                var row = {};
                for (var c=0; c<osCols.length; c++) {
                    var v = dsOS.getColumn(r, osCols[c]);
                    row[osCols[c]] = v!==null&&v!==undefined ? String(v) : null;
                }
                osRows.push(row);
            }
            R.dsOrderSale = {columns: osCols, rowCount: dsOS.getRowCount(), rows: osRows};
        } else {
            R.dsOrderSale = {error: 'not_found', note: 'wf.dsOrderSale 없음'};
        }

        // ── dsOrderSaleBind (91일 피벗 + AVG) ── ★ 핵심
        var dsB = wf.dsOrderSaleBind;
        if (dsB) {
            var bCols = [];
            for (var c=0; c<dsB.getColCount(); c++) bCols.push(dsB.getColID(c));

            var bRows = [];
            for (var r=0; r<dsB.getRowCount(); r++) {
                var row = {};
                // AVG
                try { var av=dsB.getColumn(r,'AVG');
                    row.AVG = av!==null&&av!==undefined ? String(av) : null; } catch(e){ row.AVG=null; }
                // DAY1~DAY14 샘플
                for (var d=1; d<=14; d++) {
                    try { var dv=dsB.getColumn(r,'DAY'+d);
                        row['DAY'+d] = dv!==null&&dv!==undefined ? String(dv) : null;
                    } catch(e){ row['DAY'+d]=null; }
                }
                // 91일 통계
                var sum=0, cnt=0, nonZero=0;
                for (var d=1; d<=91; d++) {
                    try { var v=dsB.getColumn(r,'DAY'+d);
                        if(v!==null&&v!==undefined&&v!==''){
                            cnt++;
                            var n=parseFloat(v);
                            if(!isNaN(n)){ sum+=n; if(n!==0) nonZero++; }
                        }
                    } catch(e){}
                }
                row._sum=sum; row._dataDays=cnt; row._nonZeroDays=nonZero;
                row._avg91 = cnt>0 ? (sum/91).toFixed(2) : 'N/A';
                row._avgNonZero = nonZero>0 ? (sum/nonZero).toFixed(2) : 'N/A';
                bRows.push(row);
            }
            R.dsOrderSaleBind = {columns: bCols, columnCount: dsB.getColCount(),
                rowCount: dsB.getRowCount(), rows: bRows};
        } else {
            R.dsOrderSaleBind = {error: 'not_found'};
        }

        return JSON.stringify(R);
    } catch(e) {
        return JSON.stringify({error: e.message});
    }
    """, FRAME_ID)
    return json.loads(raw) if isinstance(raw, str) else raw


def print_item_results(data: dict):
    """상품 결과 출력"""
    # 상품 정보
    ci = data.get('currentItem')
    if ci:
        print(f"  📦 {ci.get('ITEM_NM','?')} [{ci.get('MID_NM','')}]")
        print(f"     코드: {ci.get('ITEM_CD','')}, 재고: {ci.get('NOW_QTY','')}, "
              f"배수: {ci.get('PYUN_QTY','')}, 유통기한: {ci.get('EXPIRE_DAY','')}일")

    # dsOrderSale
    ds_os = data.get('dsOrderSale', {})
    os_count = ds_os.get('rowCount', 0)
    print(f"\n  📊 dsOrderSale: {os_count}행")
    if os_count > 0:
        rows = ds_os.get('rows', [])
        # 마지막 7일
        recent = rows[-7:] if len(rows) > 7 else rows
        print(f"     최근 {len(recent)}일:")
        print(f"     {'날짜':<12s} {'발주':>5s} {'입고':>5s} {'판매':>5s} {'폐기':>5s}")
        for r in recent:
            print(f"     {r.get('ORD_YMD','?'):<12s} "
                  f"{r.get('ORD_QTY',''):>5s} "
                  f"{r.get('BUY_QTY',''):>5s} "
                  f"{r.get('SALE_QTY',''):>5s} "
                  f"{r.get('DISUSE_QTY',''):>5s}")

        # 전체 통계
        sale_vals = [int(r.get('SALE_QTY', '0') or '0') for r in rows]
        total = sum(sale_vals)
        nonzero = [v for v in sale_vals if v > 0]
        print(f"     총 판매: {total}개, 판매일: {len(nonzero)}/{len(rows)}일")
        if nonzero:
            print(f"     전체평균: {total/len(rows):.2f}, 판매일평균: {total/len(nonzero):.2f}")

    # dsOrderSaleBind ★
    ds_bind = data.get('dsOrderSaleBind', {})
    bind_count = ds_bind.get('rowCount', 0)
    print(f"\n  ⭐ dsOrderSaleBind: {bind_count}행, {ds_bind.get('columnCount',0)}컬럼")
    if bind_count > 0:
        labels = ['발주', '입고', '판매', '폐기']
        for ri, row in enumerate(ds_bind.get('rows', [])):
            lbl = labels[ri] if ri < len(labels) else f'Row{ri}'
            print(f"     [{lbl}]")
            print(f"       ★ AVG = {row.get('AVG', 'null')}")
            print(f"       91일합={row.get('_sum',0):.0f}, "
                  f"데이터일수={row.get('_dataDays',0)}, "
                  f"nonZero={row.get('_nonZeroDays',0)}일")
            print(f"       avg(합/91)={row.get('_avg91','?')}, "
                  f"avg(nonZero)={row.get('_avgNonZero','?')}")
            # DAY1~7 샘플
            days = [str(row.get(f'DAY{d}','') or '-') for d in range(1, 8)]
            print(f"       DAY1~7: {', '.join(days)}")
    else:
        print("     (데이터 없음 — dsOrderSaleBind는 상품 검색 완료 후 피벗 생성)")


def main():
    parser = argparse.ArgumentParser(description='dsOrderSaleBind.AVG 탐구 v3')
    parser.add_argument('--items', type=str, default=None)
    parser.add_argument('--save', action='store_true')
    args = parser.parse_args()

    item_codes = args.items.split(',') if args.items else DEFAULT_ITEMS

    print("=" * 70)
    print("dsOrderSaleBind.AVG 탐구 v3")
    print("  방식: gdList 그리드 셀 입력 + Enter (collect_for_item 패턴)")
    print("=" * 70)
    print(f"대상 상품: {len(item_codes)}개")
    print()

    # ── 로그인 ──
    sa = SalesAnalyzer()
    sa.setup_driver()
    sa.connect()
    time.sleep(SA_LOGIN_WAIT)

    if not sa.do_login():
        print("❌ 로그인 실패")
        return
    print("✅ 로그인 성공")

    time.sleep(SA_POPUP_CLOSE_WAIT * 2)
    try:
        sa.close_popup()
    except Exception:
        pass
    time.sleep(SA_POPUP_CLOSE_WAIT)

    # ── STBJ030 이동 ──
    oe = OrderExecutor(sa.driver, store_id=sa.store_id)
    if not oe.navigate_to_single_order():
        print("❌ 단품별 발주 메뉴 이동 실패")
        sa.driver.quit()
        return
    print("✅ 단품별 발주 화면 이동 성공")
    time.sleep(3)

    all_results = {
        'timestamp': datetime.now().isoformat(),
        'store_id': sa.store_id,
        'items': {},
    }

    # ── 상품별 검색 ──
    for idx, item_cd in enumerate(item_codes, 1):
        print(f"\n{'═' * 60}")
        print(f"[{idx}/{len(item_codes)}] 상품: {item_cd}")
        print(f"{'═' * 60}")

        # Grid 검색
        search_result = search_item_via_grid(sa.driver, item_cd)
        if search_result.get('error'):
            print(f"  ❌ 검색 실패: {search_result['error']}")
            all_results['items'][item_cd] = {'error': search_result['error']}
            continue

        print(f"  ✅ 검색 완료 (row={search_result.get('targetRow')})")

        # 데이터 읽기
        data = read_nexacro_datasets(sa.driver)
        if 'error' in data and isinstance(data.get('error'), str):
            print(f"  ❌ 데이터 읽기 실패: {data['error']}")
            all_results['items'][item_cd] = data
            continue

        print_item_results(data)
        all_results['items'][item_cd] = data

        if idx < len(item_codes):
            time.sleep(1)

    # ── 최종 요약 ──
    print("\n" + "=" * 70)
    print("★ 최종 요약: dsOrderSaleBind.AVG vs 계산값")
    print("=" * 70)
    print(f"{'상품코드':<16s} │ {'BGF AVG':>8s} │ {'calc avg91':>10s} │ {'avg(nonZ)':>10s} │ "
          f"{'dsOS행':>6s} │ {'Bind행':>6s}")
    print("─" * 75)

    for item_cd, data in all_results['items'].items():
        if 'error' in data and isinstance(data.get('error'), str):
            print(f"{item_cd:<16s} │ {'ERROR':>8s}")
            continue

        bind = data.get('dsOrderSaleBind', {})
        os_count = data.get('dsOrderSale', {}).get('rowCount', 0)
        bind_count = bind.get('rowCount', 0)

        if bind_count > 0 and bind.get('rows'):
            # 판매행 (Row 2)
            sale_row = bind['rows'][2] if len(bind['rows']) > 2 else bind['rows'][0]
            bgf_avg = sale_row.get('AVG', '-')
            avg91 = sale_row.get('_avg91', '-')
            avg_nz = sale_row.get('_avgNonZero', '-')
        else:
            bgf_avg = '-'
            avg91 = '-'
            avg_nz = '-'

        print(f"{item_cd:<16s} │ {bgf_avg:>8s} │ {avg91:>10s} │ {avg_nz:>10s} │ "
              f"{str(os_count):>6s} │ {str(bind_count):>6s}")

    # ── 저장 ──
    if args.save:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'captures',
            f'ordersale_avg_{ts}.json',
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)
        print(f"\n💾 저장: {path}")

    sa.driver.quit()
    print("\n✅ 완료")


if __name__ == '__main__':
    main()
