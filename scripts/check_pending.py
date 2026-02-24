"""
단품별 발주 화면에서 특정 상품의 미입고 데이터 실시간 조회
- 로그인 후 팝업 완전 정리 → 메뉴 이동 → 발주일 선택 → 상품 조회
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

from src.sales_analyzer import SalesAnalyzer
from src.collectors.order_prep_collector import OrderPrepCollector
from src.utils.popup_manager import close_alerts, close_all_popups
from src.utils.nexacro_helpers import navigate_menu
from src.settings.ui_config import FRAME_IDS, DS_PATHS, MENU_TEXT, SUBMENU_TEXT

STORE_ID = "46513"
ITEM_CD = "8800336392136"
FRAME_ID = FRAME_IDS["SINGLE_ORDER"]
DS_PATH = DS_PATHS["SINGLE_ORDER"]


def clear_all_obstacles(driver, label=""):
    """모든 Alert, 팝업, 장애물 제거"""
    print(f"  [{label}] 팝업/Alert 정리 중...")
    total = 0

    # Alert 반복 처리
    for _ in range(10):
        try:
            alert = driver.switch_to.alert
            txt = alert.text
            alert.accept()
            print(f"    Alert 닫음: '{txt[:50]}'")
            total += 1
            time.sleep(0.3)
        except:
            break

    # close_alerts
    try:
        cnt = close_alerts(driver, max_attempts=5, silent=True)
        if cnt > 0:
            print(f"    close_alerts: {cnt}개")
            total += cnt
    except:
        pass

    # close_all_popups
    try:
        cnt = close_all_popups(driver, silent=True)
        if cnt > 0:
            print(f"    close_all_popups: {cnt}개")
            total += cnt
    except:
        pass

    # 넥사크로 팝업 JS로 닫기
    try:
        cnt = driver.execute_script("""
            let closed = 0;
            // 모든 보이는 팝업 닫기
            const popups = document.querySelectorAll('[id*="Popup"], [id*="popup"], [id*="POPUP"]');
            for (const p of popups) {
                if (p.offsetParent !== null && p.style.display !== 'none') {
                    const btn = p.querySelector('[id*="btn_close"], [id*="Close"], [id*="close"]');
                    if (btn) { btn.click(); closed++; }
                }
            }
            // Escape 키 보내기
            document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', keyCode:27, bubbles:true}));
            return closed;
        """) or 0
        if cnt > 0:
            print(f"    JS 팝업 닫기: {cnt}개")
            total += cnt
    except:
        pass

    time.sleep(0.5)
    if total > 0:
        print(f"    총 {total}개 장애물 제거")
    else:
        print(f"    깨끗함")


def main():
    print(f"=" * 70)
    print(f"상품 미입고 실시간 조회: {ITEM_CD}")
    print(f"매장: {STORE_ID} | 시간: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"=" * 70)

    # 1. 로그인
    print("\n[STEP 1] 로그인...")
    analyzer = SalesAnalyzer(store_id=STORE_ID)
    analyzer.setup_driver()
    analyzer.connect()

    if not analyzer.do_login():
        print("[ERROR] 로그인 실패!")
        analyzer.driver.quit()
        return

    print("[OK] 로그인 성공")
    driver = analyzer.driver

    # 2. 로그인 후 팝업 완전 정리
    print("\n[STEP 2] 로그인 후 팝업 정리...")
    time.sleep(3)  # 로그인 후 팝업 뜨는 시간 대기
    clear_all_obstacles(driver, "로그인후")
    time.sleep(2)
    clear_all_obstacles(driver, "2차정리")
    time.sleep(1)

    # 3. 메뉴 이동
    print("\n[STEP 3] 단품별 발주 메뉴 이동...")
    success = navigate_menu(
        driver, MENU_TEXT["ORDER"], SUBMENU_TEXT["SINGLE_ORDER"], FRAME_ID
    )
    if not success:
        print("[ERROR] 메뉴 이동 실패!")
        driver.quit()
        return
    print("[OK] 단품별 발주 화면 진입")
    time.sleep(2)
    clear_all_obstacles(driver, "메뉴진입후")
    time.sleep(1)

    # 4. 발주일 선택
    print("\n[STEP 4] 발주일 선택...")
    collector = OrderPrepCollector(driver=driver, save_to_db=False, store_id=STORE_ID)
    collector._menu_navigated = True  # 이미 메뉴 이동했으므로

    date_ok = collector.select_order_date(row_index=0)
    if date_ok:
        print("[OK] 발주일 선택 완료")
    else:
        print("[WARNING] 발주일 선택 실패")

    time.sleep(1)
    clear_all_obstacles(driver, "발주일선택후")
    time.sleep(1)

    # 5. 상품 조회 (collect_for_item 사용)
    print(f"\n[STEP 5] 상품 조회: {ITEM_CD}")
    result = collector.collect_for_item(ITEM_CD)

    if result and result.get('success'):
        print("[OK] 조회 성공!")
    else:
        print(f"[WARNING] collect_for_item 실패: {result}")
        print("직접 JS로 데이터 읽기 시도...")

    # 6. dsOrderSale 직접 읽기
    print(f"\n[STEP 6] dsOrderSale 데이터 읽기...")
    data = driver.execute_script("""
        try {
            const app = nexacro.getApplication();
            const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[arguments[0]].form;
            const parts = arguments[1].split('.');
            let wf = form;
            for (const p of parts) { wf = wf?.[p]; }
            if (!wf) return {error: 'workForm not found'};

            function getVal(ds, row, col) {
                let val = ds.getColumn(row, col);
                if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                return val;
            }

            const result = { itemInfo: null, history: [] };

            if (wf.dsItem && wf.dsItem.getRowCount() > 0) {
                result.itemInfo = {
                    item_cd: getVal(wf.dsItem, 0, 'ITEM_CD'),
                    item_nm: getVal(wf.dsItem, 0, 'ITEM_NM'),
                    now_qty: parseInt(getVal(wf.dsItem, 0, 'NOW_QTY')) || 0,
                    order_unit_qty: parseInt(getVal(wf.dsItem, 0, 'ORD_UNIT_QTY')) || 1,
                    expire_day: parseInt(getVal(wf.dsItem, 0, 'EXPIRE_DAY')) || null,
                    ord_pss_nm: getVal(wf.dsItem, 0, 'ORD_PSS_NM') || ''
                };
            }

            if (wf.dsOrderSale) {
                for (let i = 0; i < wf.dsOrderSale.getRowCount(); i++) {
                    result.history.push({
                        date: getVal(wf.dsOrderSale, i, 'ORD_YMD'),
                        ord_qty: parseInt(getVal(wf.dsOrderSale, i, 'ORD_QTY')) || 0,
                        buy_qty: parseInt(getVal(wf.dsOrderSale, i, 'BUY_QTY')) || 0,
                        sale_qty: parseInt(getVal(wf.dsOrderSale, i, 'SALE_QTY')) || 0,
                        disuse_qty: parseInt(getVal(wf.dsOrderSale, i, 'DISUSE_QTY')) || 0
                    });
                }
            }

            return result;
        } catch(e) {
            return {error: e.message};
        }
    """, FRAME_ID, DS_PATH)

    if not data or data.get('error'):
        print(f"  [ERROR] 데이터 읽기 실패: {data}")

        # collect_for_item 결과라도 있으면 출력
        if result and result.get('success'):
            data = {'itemInfo': {
                'item_cd': result.get('item_cd'),
                'item_nm': result.get('item_nm'),
                'now_qty': result.get('current_stock', 0),
                'order_unit_qty': result.get('order_unit_qty', 1),
                'expire_day': result.get('expiration_days'),
            }, 'history': result.get('history', [])}
        else:
            try:
                ss = str(Path(__file__).parent.parent / "data" / "screenshots" / "debug_pending.png")
                driver.save_screenshot(ss)
                print(f"  스크린샷: {ss}")
            except:
                pass
            driver.quit()
            return

    # 7. 결과 출력
    item_info = data.get('itemInfo')
    history = data.get('history', [])

    print(f"\n{'=' * 70}")
    print(f"상품 정보")
    print(f"{'=' * 70}")
    if item_info:
        print(f"  상품코드: {item_info.get('item_cd')}")
        print(f"  상품명:   {item_info.get('item_nm')}")
        print(f"  현재재고: {item_info.get('now_qty')}개")
        print(f"  발주단위: {item_info.get('order_unit_qty')}개")
        print(f"  유통기한: {item_info.get('expire_day')}일")
        print(f"  발주상태: {item_info.get('ord_pss_nm', '')}")
    else:
        print("  (데이터 없음)")

    print(f"\n{'=' * 70}")
    print(f"발주/입고 이력 (dsOrderSale) - {len(history)}행")
    print(f"{'=' * 70}")
    if history:
        print(f"{'날짜':<12} {'발주배수':>8} {'입고(개)':>8} {'판매':>6} {'폐기':>6}")
        print(f"{'-' * 50}")
        for h in history:
            d = str(h.get('date', ''))
            d_fmt = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d
            print(f"{d_fmt:<12} {h.get('ord_qty',0):>8} {h.get('buy_qty',0):>8} {h.get('sale_qty',0):>6} {h.get('disuse_qty',0):>6}")

        # 미입고 계산
        unit = item_info.get('order_unit_qty', 1) if item_info else 1
        today = time.strftime("%Y%m%d")
        past_ord, past_buy, today_ord, today_buy = 0, 0, 0, 0

        print(f"\n{'=' * 70}")
        print(f"미입고 계산 (입수={unit}, today={today})")
        print(f"{'=' * 70}")

        for h in history:
            actual = h.get('ord_qty', 0) * unit
            buy = h.get('buy_qty', 0)
            d = str(h.get('date', ''))
            tag = "오늘" if d >= today else "과거"
            if d >= today:
                today_ord += actual
                today_buy += buy
            else:
                past_ord += actual
                past_buy += buy
            if actual > 0 or buy > 0:
                d_fmt = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d
                diff = max(0, actual - buy)
                print(f"  {d_fmt} [{tag}] 발주={actual}개 입고={buy}개 차이={diff}개")

        past_p = max(0, past_ord - past_buy)
        today_p = max(0, today_ord - today_buy)
        pending = past_p + today_p
        stock = item_info.get('now_qty', 0) if item_info else 0

        print(f"\n  과거: 발주합계={past_ord} - 입고합계={past_buy} = {past_p}")
        print(f"  오늘: 발주합계={today_ord} - 입고합계={today_buy} = {today_p}")
        print(f"  {'=' * 40}")
        print(f"  ★ 총 미입고 = {pending}개")
        print(f"  ★ 현재재고 = {stock}개")
        print(f"  ★ 가용재고 = {stock + pending}개")

        if stock + pending > 0:
            print(f"\n  → 가용재고 {stock + pending}개 → 추가 발주 불필요")
        else:
            print(f"\n  → 가용재고 0 → 발주 필요")

        # collect_for_item 결과와 비교
        if result and result.get('success'):
            sys_pending = result.get('pending_qty', 0)
            print(f"\n  [비교] 시스템 계산 미입고: {sys_pending}개 vs 직접 계산: {pending}개")
    else:
        print("  (이력 없음)")

    print(f"\n{'=' * 70}")
    print(f"조회 완료!")
    print(f"{'=' * 70}")

    driver.quit()


if __name__ == "__main__":
    main()
