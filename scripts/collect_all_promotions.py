"""
전체 상품 행사(1+1, 2+1) 수집 스크립트

CDP Performance Log를 사용하여 넥사크로 /stbj030/selSearch 요청 템플릿을 캡처한 후,
DirectApiFetcher로 전체 상품의 행사 정보를 배치 수집합니다.

사용법:
    python scripts/collect_all_promotions.py
    python scripts/collect_all_promotions.py --store 46513
    python scripts/collect_all_promotions.py --dry-run
"""

import sys
import os
import json
import time
import argparse

# 프로젝트 루트 설정
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from webdriver_manager.chrome import ChromeDriverManager

from src.collectors.direct_api_fetcher import (
    DirectApiFetcher, parse_full_ssv_response, extract_item_data,
)
from src.collectors.order_prep_collector import OrderPrepCollector
from src.infrastructure.database.repos.product_detail_repo import ProductDetailRepository
from src.infrastructure.database.repos.promotion_repo import PromotionRepository
from src.utils.logger import get_logger
from src.utils.popup_manager import close_all_popups

logger = get_logger(__name__)

STORES = ["46513", "46704"]
CHUNK_SIZE = 300


def create_driver_with_perf_logging():
    """CDP Performance 로그 활성화된 Chrome 드라이버 생성"""
    chrome_options = Options()
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")

    # 자동화 감지 우회
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    # Performance 로그 활성화 (CDP 네트워크 이벤트 캡처용)
    chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.implicitly_wait(10)
    driver.set_page_load_timeout(60)
    driver.set_script_timeout(300)

    # 자동화 감지 우회 스크립트
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    })

    return driver


def do_login(driver, store_id):
    """BGF 로그인 (SalesAnalyzer 로직 재사용)"""
    from src.sales_analyzer import SalesAnalyzer
    analyzer = SalesAnalyzer(store_id=store_id)

    # 기존 드라이버 사용
    analyzer.driver = driver
    analyzer.connect()

    if not analyzer.do_login():
        return False

    time.sleep(3)
    dismiss_popups(driver)
    return True


def dismiss_popups(driver, max_attempts=5):
    """로그인 후 팝업 닫기"""
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import NoAlertPresentException

    for attempt in range(max_attempts):
        closed = 0

        try:
            n = close_all_popups(driver, silent=True)
            closed += n
        except Exception:
            pass

        xpaths = [
            "//*[text()='닫기']",
            "//*[text()='확인']",
            "//*[@id[contains(., 'btn_close')]]",
        ]
        for xpath in xpaths:
            try:
                elements = driver.find_elements(By.XPATH, xpath)
                for elem in elements:
                    if elem.is_displayed():
                        try:
                            elem.click()
                            closed += 1
                            time.sleep(0.3)
                        except Exception:
                            pass
            except Exception:
                pass

        try:
            from selenium.webdriver.common.alert import Alert
            alert = Alert(driver)
            alert.accept()
            closed += 1
        except (NoAlertPresentException, Exception):
            pass

        if closed == 0:
            break
        print(f"  팝업 {closed}개 닫음 (attempt {attempt+1})")
        time.sleep(0.5)


def flush_perf_logs(driver):
    """기존 performance 로그 비우기"""
    try:
        driver.get_log('performance')
    except Exception:
        pass


def capture_template_from_perf_logs(driver):
    """CDP Performance 로그에서 selSearch 요청 body 추출

    넥사크로는 내부 통신 객체를 사용하므로 JS 인터셉터로 캡처 불가.
    CDP 네트워크 레벨에서 모든 요청을 캡처합니다.
    """
    try:
        logs = driver.get_log('performance')
    except Exception as e:
        print(f"  Performance 로그 조회 실패: {e}")
        return None

    for entry in logs:
        try:
            msg = json.loads(entry['message'])['message']
            if msg['method'] == 'Network.requestWillBeSent':
                params = msg.get('params', {})
                request = params.get('request', {})
                url = request.get('url', '')
                if 'selSearch' in url:
                    body = request.get('postData', '')
                    if body and 'strItemCd' in body:
                        print(f"  CDP 캡처 성공: {url[:80]}... body={len(body)}B")
                        return body
        except (json.JSONDecodeError, KeyError):
            continue

    return None


def trigger_selenium_search(driver, item_cd, frame_id):
    """OrderPrepCollector.collect_for_item() 동일 방식으로 바코드 검색

    그리드 셀 활성화 → ActionChains 바코드 입력 → Enter 전송
    """
    from src.settings.ui_config import FRAME_IDS

    # 1. 마지막 행 상품명 셀 활성화 (OrderPrepCollector.collect_for_item 로직)
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
                const existingCd = ds.getColumn(lastRow, 'ITEM_CD') || ds.getColumn(lastRow, 'PLU_CD') || '';
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
    """, frame_id)

    if not result or result.get('error'):
        print(f"  셀 활성화 실패: {result}")
        return False

    target_row = result.get('targetRow', 0)
    time.sleep(0.5)

    # 2. 상품검색 팝업 닫기
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

    # 3. column 1 input 포커스
    driver.execute_script("""
        const pattern = 'cell_' + arguments[0] + '_1';
        const inputs = document.querySelectorAll(
            '[id*="gdList"][id*="' + pattern + '"][id*="celledit:input"]');
        if (inputs.length > 0) inputs[0].focus();
    """, target_row)
    time.sleep(0.3)

    # 4. ActionChains로 바코드 입력 + Enter
    actions = ActionChains(driver)
    actions.key_down(Keys.CONTROL).send_keys('a').key_up(Keys.CONTROL)
    actions.send_keys(Keys.DELETE)
    actions.send_keys(item_cd)
    actions.perform()
    time.sleep(0.5)

    actions = ActionChains(driver)
    actions.send_keys(Keys.ENTER)
    actions.perform()

    print(f"  바코드 검색: {item_cd}")
    time.sleep(3)  # API 응답 대기

    # Alert 처리
    try:
        alert = driver.switch_to.alert
        alert_text = alert.text
        alert.accept()
        print(f"  Alert: {alert_text}")
        return False
    except Exception:
        pass

    return True


def collect_promotions_for_store(store_id, dry_run=False):
    """한 매장의 전체 행사 수집"""
    print(f"\n{'='*60}")
    print(f"매장 {store_id} 행사 수집 시작")
    print(f"{'='*60}")

    # 1. Performance 로그 활성화된 드라이버 생성 + 로그인
    print("\n[1/6] 로그인 (CDP Performance 로그 활성)...")
    driver = create_driver_with_perf_logging()

    if not do_login(driver, store_id):
        print("  로그인 실패!")
        driver.quit()
        return 0

    # 2. 단품별 발주 메뉴 이동 + 날짜 선택
    print("\n[2/6] 단품별 발주 메뉴 이동...")
    opc = OrderPrepCollector(driver=driver, store_id=store_id, save_to_db=False)

    if not opc.navigate_to_menu():
        print("  메뉴 이동 실패!")
        driver.quit()
        return 0

    if not opc.select_order_date():
        print("  날짜 선택 실패!")
        driver.quit()
        return 0

    time.sleep(2)

    # 3. 활성 상품 목록 조회
    print("\n[3/6] 활성 상품 목록 조회...")
    pd_repo = ProductDetailRepository()
    item_codes = pd_repo.get_all_active_items(days=30, store_id=store_id)

    if not item_codes:
        print("  활성 상품 없음!")
        driver.quit()
        return 0

    print(f"  활성 상품: {len(item_codes)}개")

    # 4. CDP 템플릿 캡처: 기존 로그 비우고 → Selenium 검색 → 캡처
    print("\n[4/6] 템플릿 캡처 (CDP Performance Log)...")
    flush_perf_logs(driver)  # 기존 로그 비우기

    from src.settings.ui_config import FRAME_IDS
    frame_id = FRAME_IDS["SINGLE_ORDER"]

    # 첫 번째 상품으로 Selenium 검색 (이때 넥사크로가 API 호출)
    template = None
    for try_idx in range(min(3, len(item_codes))):
        sample_cd = item_codes[try_idx]
        print(f"  시도 {try_idx+1}: {sample_cd}")

        if trigger_selenium_search(driver, sample_cd, frame_id):
            template = capture_template_from_perf_logs(driver)
            if template:
                break
        else:
            # 검색 실패해도 로그 확인
            template = capture_template_from_perf_logs(driver)
            if template:
                break

        time.sleep(1)

    if not template:
        print("  CDP 템플릿 캡처 실패!")
        print("  → Selenium 폴백 모드로 전환 (느림)")

        # Selenium 폴백: OrderPrepCollector로 전체 수집
        return _fallback_selenium_collect(driver, store_id, item_codes, dry_run)

    print(f"  템플릿 캡처 성공! ({len(template)}B)")

    # 5. DirectApiFetcher로 배치 수집
    print(f"\n[5/6] Direct API 배치 수집 (청크 {CHUNK_SIZE}개씩)...")
    fetcher = DirectApiFetcher(driver, concurrency=5, timeout_ms=5000)
    fetcher.set_request_template(template)

    promo_repo = PromotionRepository(store_id=store_id)
    total_promo = 0
    total_success = 0
    total_error = 0

    num_chunks = (len(item_codes) + CHUNK_SIZE - 1) // CHUNK_SIZE

    for chunk_idx in range(num_chunks):
        start = chunk_idx * CHUNK_SIZE
        end = min(start + CHUNK_SIZE, len(item_codes))
        chunk = item_codes[start:end]

        print(f"  청크 {chunk_idx+1}/{num_chunks}: {len(chunk)}개 조회...", end="", flush=True)
        chunk_start = time.time()

        try:
            results = fetcher.fetch_items_batch(chunk, delay_ms=30)
        except Exception as e:
            print(f" 실패: {e}")
            total_error += len(chunk)
            continue

        chunk_elapsed = time.time() - chunk_start
        chunk_success = len(results)
        chunk_error = len(chunk) - chunk_success
        total_success += chunk_success
        total_error += chunk_error

        # 행사 정보 추출 및 저장
        chunk_promo = 0
        for item_cd, data in results.items():
            current = data.get('current_month_promo', '')
            next_m = data.get('next_month_promo', '')
            item_nm = data.get('item_nm', '')

            if current or next_m:
                chunk_promo += 1
                if not dry_run:
                    try:
                        promo_repo.save_monthly_promo(
                            item_cd=item_cd,
                            item_nm=item_nm,
                            current_month_promo=current,
                            next_month_promo=next_m,
                            store_id=store_id,
                        )
                    except Exception as e:
                        logger.warning(f"행사 저장 실패 {item_cd}: {e}")

        total_promo += chunk_promo
        rate = len(chunk) / chunk_elapsed if chunk_elapsed > 0 else 0
        print(f" {chunk_success}건 성공, 행사 {chunk_promo}건 ({chunk_elapsed:.1f}초, {rate:.0f}건/초)")

    # 6. 결과 요약
    _print_summary(store_id, len(item_codes), total_success, total_error, total_promo, dry_run)

    driver.quit()
    return total_promo


def _fallback_selenium_collect(driver, store_id, item_codes, dry_run):
    """Selenium 폴백: OrderPrepCollector를 사용한 순차 수집

    DirectAPI 템플릿 캡처 실패 시 사용. ~3초/상품, save_to_db=True로
    미입고/재고/행사 정보를 모두 DB에 저장합니다.
    """
    print(f"\n[Selenium 폴백] {len(item_codes)}개 상품 순차 수집 시작...")
    print(f"  예상 소요: ~{len(item_codes) * 3 / 60:.0f}분")

    opc = OrderPrepCollector(
        driver=driver,
        store_id=store_id,
        save_to_db=(not dry_run),
    )
    opc._menu_navigated = True  # 이미 메뉴 이동됨
    opc._date_selected = True   # 이미 날짜 선택됨

    # DirectAPI 비활성화하고 Selenium으로만 수집
    results = opc.collect_for_items(item_codes, use_direct_api=False)

    total_promo = 0
    total_success = 0
    for item_cd, result in results.items():
        if result.get('success'):
            total_success += 1

    # Selenium 모드는 _save_item_to_db에서 행사도 자동 저장
    # 행사 갯수 확인
    if not dry_run:
        promo_repo = PromotionRepository(store_id=store_id)
        from datetime import date
        today = date.today().isoformat()
        try:
            active = promo_repo.get_active_promotions(today)
            total_promo = len(active)
        except Exception:
            total_promo = 0

    _print_summary(store_id, len(item_codes), total_success,
                   len(item_codes) - total_success, total_promo, dry_run)

    driver.quit()
    return total_promo


def _print_summary(store_id, total, success, error, promo, dry_run):
    """결과 요약 출력"""
    print(f"\n{'─'*60}")
    print(f"매장 {store_id} 수집 완료:")
    print(f"  전체 상품: {total}개")
    print(f"  성공: {success}개")
    print(f"  실패: {error}개")
    print(f"  행사 상품: {promo}개")
    if dry_run:
        print(f"  (dry-run 모드: DB 저장 안 함)")
    print(f"{'─'*60}")


def main():
    parser = argparse.ArgumentParser(description="전체 상품 행사(1+1, 2+1) 수집")
    parser.add_argument("--store", type=str, help="특정 매장만 수집")
    parser.add_argument("--dry-run", action="store_true", help="DB 저장 없이 테스트")
    args = parser.parse_args()

    stores = [args.store] if args.store else STORES

    total = 0
    for store_id in stores:
        count = collect_promotions_for_store(store_id, dry_run=args.dry_run)
        total += count

    print(f"\n{'='*60}")
    print(f"전체 완료: {total}건 행사 수집")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
