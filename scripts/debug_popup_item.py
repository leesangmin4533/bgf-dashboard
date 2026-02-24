"""2/6 전표2(10043710011) 팝업 상세 데이터 확인"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.sales_analyzer import SalesAnalyzer
from src.collectors.waste_slip_collector import WasteSlipCollector

# 로그인
analyzer = SalesAnalyzer(store_id='46513')
analyzer.setup_driver()
analyzer.connect()
analyzer.do_login()
analyzer.close_popup()
driver = analyzer.driver

ws = WasteSlipCollector(driver=driver, store_id='46513')

# 메뉴 이동
ws.navigate_to_waste_slip_menu()
time.sleep(2)

# 날짜 설정 + 조회
ws._set_date_range_and_filter('20260206', '20260206')
time.sleep(1)
ws._execute_search()
time.sleep(3)

# dsList 헤더 수집
slips = ws._collect_waste_slips()
print(f"전표 {len(slips)}건")
for i, s in enumerate(slips):
    print(f"  [{i}] CHIT_NO={s.get('CHIT_NO')} ITEM_CNT={s.get('ITEM_CNT')} CENTER_NM={s.get('CENTER_NM')}")

# 전표별 상세 품목 수집
for idx, slip in enumerate(slips):
    chit_no = slip.get("CHIT_NO", "")
    print(f"\n--- 전표 [{idx}] {chit_no} 상세 ---")

    # dsGs 파라미터 설정
    ws._set_dsgs_params(idx)

    # 기존 팝업 닫기
    ws._close_existing_popup()

    # 팝업 열기
    ws._open_detail_popup()

    # dsGsTmp 갱신 + 서버 트랜잭션
    ws._update_dsgs_tmp_and_search(slip)
    time.sleep(2)  # 충분히 대기

    # 팝업 스크린샷
    driver.save_screenshot(f'data/screenshots/2026-02-19/waste_0206_popup_{idx}.png')
    print(f"  팝업 스크린샷 저장: waste_0206_popup_{idx}.png")

    # 팝업 데이터 추출 (모든 컬럼)
    items = ws._extract_popup_items()
    print(f"  추출 품목: {len(items)}건")
    for j, item in enumerate(items):
        item_cd = item.get("ITEM_CD", "?")
        item_nm = item.get("ITEM_NM", "?")
        qty = item.get("QTY") or item.get("JISI_QTY") or item.get("JUMON_QTY") or "?"
        print(f"    [{j}] ITEM_CD={item_cd}  ITEM_NM={item_nm}  dsType={item.get('_dsType')}")
        # 모든 키 출력
        for k, v in item.items():
            if k not in ('_dsType',) and v is not None and v != '' and v != 0:
                print(f"         {k}={v}")

ws._close_existing_popup()
driver.quit()
print("\n완료")
