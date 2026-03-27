"""
신상품 도입 현황 수집 라이브 드라이런

Chrome Remote Debugging으로 기존 브라우저에 연결하여
BGF 로그인 없이 바로 신상품 수집 테스트.

사용법:
  1. Chrome 완전 종료 (Ctrl+Shift+Q 또는 작업관리자)
  2. 이 스크립트 실행 → Chrome이 디버깅 모드로 자동 시작
  3. BGF 사이트 로그인 (크롬 확장 또는 수동)
  4. 로그인 확인 후 Enter 입력 → 신상품 수집 시작

또는:
  이미 디버깅 모드 Chrome이 실행 중이면:
  python scripts/run_new_product_live.py --attach
"""

import sys
import os
import time
import subprocess
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.logger import get_logger

logger = get_logger("new_product_live")

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
USER_DATA_DIR = r"C:\Users\kanur\AppData\Local\Google\Chrome\User Data"
DEBUG_PORT = 9222
BGF_URL = "https://store.bgfretail.com/websrc/deploy/index.html"


def check_port_open(port: int) -> bool:
    """디버깅 포트가 열려있는지 확인"""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect(("localhost", port))
        sock.close()
        return True
    except (ConnectionRefusedError, OSError):
        return False


def start_chrome_debug():
    """Chrome을 디버깅 모드로 시작"""
    if check_port_open(DEBUG_PORT):
        logger.info(f"Chrome 디버깅 포트 {DEBUG_PORT} 이미 활성")
        return True

    logger.info("Chrome 디버깅 모드로 시작...")
    cmd = [
        CHROME_PATH,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={USER_DATA_DIR}",
        BGF_URL,
    ]

    try:
        subprocess.Popen(cmd)
        # 포트 열릴 때까지 대기
        for i in range(15):
            time.sleep(1)
            if check_port_open(DEBUG_PORT):
                logger.info(f"Chrome 시작 완료 ({i+1}초)")
                return True
        logger.error("Chrome 시작 타임아웃 (15초)")
        return False
    except FileNotFoundError:
        logger.error(f"Chrome 실행 파일 없음: {CHROME_PATH}")
        return False


def attach_selenium():
    """디버깅 포트로 Selenium 연결"""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    chrome_options = Options()
    chrome_options.add_experimental_option("debuggerAddress", f"localhost:{DEBUG_PORT}")

    try:
        driver = webdriver.Chrome(options=chrome_options)
        logger.info(f"Selenium 연결 성공, 현재 URL: {driver.current_url}")
        return driver
    except Exception as e:
        logger.error(f"Selenium 연결 실패: {e}")
        return None


def check_bgf_login(driver) -> bool:
    """BGF 로그인 상태 확인"""
    try:
        result = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var channelType = app.getGlobalVariable("GV_CHANNELTYPE");
                var storeCode = app.getGlobalVariable("GV_STORE_CD");
                return {
                    logged_in: channelType === 'HOME',
                    channel: channelType,
                    store: storeCode
                };
            } catch(e) {
                return {logged_in: false, error: e.message};
            }
        """)
        if result and result.get("logged_in"):
            logger.info(f"BGF 로그인 확인: store={result.get('store')}")
            return True
        else:
            logger.warning(f"BGF 미로그인: {result}")
            return False
    except Exception as e:
        logger.warning(f"로그인 확인 실패: {e}")
        return False


def find_bgf_tab(driver) -> bool:
    """BGF 탭 찾기 및 전환"""
    try:
        handles = driver.window_handles
        logger.info(f"열린 탭: {len(handles)}개")

        for handle in handles:
            driver.switch_to.window(handle)
            url = driver.current_url
            logger.debug(f"  탭: {url[:60]}...")
            if "bgfretail" in url or "store.bgf" in url:
                logger.info(f"BGF 탭 발견: {url[:80]}")
                return True

        logger.warning("BGF 탭을 찾을 수 없음")
        return False
    except Exception as e:
        logger.error(f"탭 전환 실패: {e}")
        return False


def run_new_product_collection(driver):
    """신상품 수집 실행"""
    from src.collectors.new_product_collector import NewProductCollector

    store_id = "46513"
    collector = NewProductCollector(driver=driver, store_id=store_id)

    # 1. 메뉴 이동
    logger.info("=" * 50)
    logger.info("신상품 도입 현황 메뉴로 이동...")
    logger.info("=" * 50)

    if not collector.navigate_to_menu():
        logger.error("메뉴 이동 실패")
        return None

    time.sleep(2)

    # 2. 수집
    logger.info("=" * 50)
    logger.info("데이터 수집 시작...")
    logger.info("=" * 50)

    try:
        result = collector.collect_and_save()
        logger.info(f"수집 완료: {result}")
        return result
    except Exception as e:
        logger.error(f"수집 실패: {e}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        try:
            collector.close_menu()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="신상품 수집 라이브 드라이런")
    parser.add_argument("--attach", action="store_true",
                       help="이미 실행 중인 Chrome에 연결만 시도")
    parser.add_argument("--skip-login-check", action="store_true",
                       help="로그인 확인 건너뛰기")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("신상품 도입 현황 수집 라이브 드라이런")
    logger.info("=" * 60)

    # 1. Chrome 시작/연결
    if args.attach:
        if not check_port_open(DEBUG_PORT):
            logger.error(
                f"Chrome 디버깅 포트 {DEBUG_PORT} 없음.\n"
                "Chrome을 다음 명령으로 시작하세요:\n"
                f'  "{CHROME_PATH}" --remote-debugging-port={DEBUG_PORT} '
                f'--user-data-dir="{USER_DATA_DIR}"'
            )
            return 1
    else:
        if not start_chrome_debug():
            return 1

    # 2. Selenium 연결
    driver = attach_selenium()
    if not driver:
        return 1

    # 3. BGF 탭 찾기
    if not find_bgf_tab(driver):
        logger.info(f"BGF 사이트로 이동합니다: {BGF_URL}")
        driver.get(BGF_URL)
        time.sleep(5)

    # 4. 로그인 확인
    if not args.skip_login_check:
        if not check_bgf_login(driver):
            logger.info("")
            logger.info("⚠️  BGF에 로그인되지 않았습니다.")
            logger.info("    브라우저에서 로그인 후 Enter를 눌러주세요...")
            input("    [Enter 입력 대기] ")

            if not check_bgf_login(driver):
                logger.error("로그인 확인 실패. 종료합니다.")
                return 1

    # 5. 신상품 수집
    result = run_new_product_collection(driver)

    if result:
        logger.info("")
        logger.info("=" * 60)
        logger.info("✅ 수집 완료!")
        logger.info(f"   주차 수: {result.get('weekly_count', 0)}")
        logger.info(f"   결과: {result}")
        logger.info("=" * 60)

        # 로그에서 method 확인
        logger.info("")
        logger.info("📋 [팝업수집] 로그에서 method=dom_handle_idx 확인하세요:")
        logger.info("   grep '[팝업수집]' logs/*.log | tail -20")
    else:
        logger.error("수집 실패")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
