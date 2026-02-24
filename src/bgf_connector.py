"""
BGF 리테일 자동 접속 프로그램
넥사크로 기반 웹사이트 자동화
"""

from typing import Any, Dict, List, Optional

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import time
import os

from config import BASE_URL, BROWSER_OPTIONS, NEXACRO_CONFIG
from nexacro_helper import NexacroHelper
from src.utils.logger import get_logger

logger = get_logger(__name__)


class BGFConnector:
    """BGF 리테일 스토어 자동 접속 클래스"""

    def __init__(self) -> None:
        self.driver: Optional[webdriver.Chrome] = None
        self.nexacro: Optional[NexacroHelper] = None
        self.is_connected: bool = False

    def setup_driver(self) -> webdriver.Chrome:
        """크롬 드라이버 설정"""
        chrome_options = Options()

        # 브라우저 옵션 설정
        if BROWSER_OPTIONS.get("headless"):
            chrome_options.add_argument("--headless=new")

        # 창 크기 설정
        width, height = BROWSER_OPTIONS.get("window_size", (1920, 1080))
        chrome_options.add_argument(f"--window-size={width},{height}")

        # 추가 옵션 (안정성 향상)
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--allow-running-insecure-content")

        # 자동화 감지 우회
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

        # 드라이버 생성
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=chrome_options)

        # 타임아웃 설정
        self.driver.implicitly_wait(BROWSER_OPTIONS.get("implicit_wait", 10))
        self.driver.set_page_load_timeout(BROWSER_OPTIONS.get("page_load_timeout", 30))

        # 자동화 감지 우회 스크립트
        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """
        })

        # 넥사크로 헬퍼 초기화
        self.nexacro = NexacroHelper(self.driver)

        logger.info("크롬 드라이버 설정 완료")
        return self.driver

    def connect(self) -> bool:
        """BGF 리테일 스토어에 접속"""
        if not self.driver:
            self.setup_driver()

        try:
            logger.info(f"접속 시도: {BASE_URL}")
            self.driver.get(BASE_URL)

            # 넥사크로 로딩 대기
            logger.info("넥사크로 프레임워크 로딩 대기 중...")
            self.nexacro.wait_for_nexacro_load(NEXACRO_CONFIG.get("load_wait_time", 5))

            # 네트워크 요청 캡처 설정 (API 분석용)
            self.nexacro.capture_network_requests()

            self.is_connected = True
            logger.info("BGF 리테일 스토어 접속 성공")

            # 페이지 정보 출력
            self.print_page_info()

            return True

        except Exception as e:
            logger.error(f"접속 실패: {e}")
            return False

    def print_page_info(self) -> None:
        """현재 페이지 정보 출력"""
        logger.info("페이지 정보")
        logger.info(f"제목: {self.driver.title}")
        logger.info(f"URL: {self.driver.current_url}")

    def login(self, user_id: str, password: str) -> bool:
        """로그인 수행"""
        if not self.is_connected:
            logger.error("먼저 connect()를 호출하세요")
            return False

        try:
            logger.info("로그인 시도 중...")

            # 로그인 필드 찾기 및 입력 (실제 컴포넌트 ID는 페이지 분석 후 수정 필요)
            # 넥사크로 컴포넌트 ID는 보통 'edt_', 'btn_' 등의 접두사 사용

            # 아이디 입력
            if self.nexacro.input_nexacro_text("edt_id", user_id):
                logger.info("아이디 입력 완료")

            # 비밀번호 입력
            if self.nexacro.input_nexacro_text("edt_pw", password):
                logger.info("비밀번호 입력 완료")

            time.sleep(1)

            # 로그인 버튼 클릭
            if self.nexacro.click_nexacro_button("btn_login"):
                logger.info("로그인 버튼 클릭 완료")

            # 로그인 완료 대기
            time.sleep(NEXACRO_CONFIG.get("component_wait_time", 3))

            logger.info("로그인 처리 완료")
            return True

        except Exception as e:
            logger.error(f"로그인 실패: {e}")
            return False

    def analyze_page_structure(self) -> List[str]:
        """페이지 구조 분석 (개발/디버깅용)"""
        logger.info("페이지 구조 분석")

        # 모든 컴포넌트 ID 가져오기
        component_ids = self.nexacro.get_all_components()

        logger.info(f"발견된 요소 ID ({len(component_ids)}개):")
        for comp_id in component_ids[:50]:  # 처음 50개만 출력
            logger.info(f"  - {comp_id}")

        if len(component_ids) > 50:
            logger.info(f"  ... 외 {len(component_ids) - 50}개")

        # 캡처된 네트워크 요청 출력
        requests = self.nexacro.get_captured_requests()
        if requests:
            logger.info(f"캡처된 네트워크 요청 ({len(requests)}개):")
            for req in requests[:20]:
                logger.info(f"  [{req['method']}] {req['url']}")

        return component_ids

    def get_screenshot(self, filename: str = "screenshot.png") -> str:
        """스크린샷 저장 (data/screenshots/ 디렉토리에 통합 저장)"""
        from src.utils.screenshot import get_screenshot_dir
        filepath = str(get_screenshot_dir() / filename)
        self.driver.save_screenshot(filepath)
        logger.info(f"스크린샷 저장: {filepath}")
        return filepath

    def wait(self, seconds: float) -> None:
        """지정된 시간만큼 대기"""
        time.sleep(seconds)

    def close(self) -> None:
        """브라우저 종료"""
        if self.driver:
            self.driver.quit()
            self.driver = None
            self.is_connected = False
            logger.info("브라우저 종료")

    def __enter__(self) -> "BGFConnector":
        """Context manager 진입"""
        self.setup_driver()
        return self

    def __exit__(self, exc_type: Optional[type], exc_val: Optional[BaseException], exc_tb: Any) -> None:
        """Context manager 종료"""
        self.close()


def main() -> None:
    """메인 실행 함수"""
    logger.info("BGF 리테일 자동 접속 프로그램")

    # Context manager 사용
    with BGFConnector() as bgf:
        # 접속
        if bgf.connect():
            # 페이지 구조 분석 (개발용)
            bgf.analyze_page_structure()

            # 스크린샷 저장
            bgf.get_screenshot("bgf_main.png")

            # 로그인이 필요한 경우 (실제 사용시 환경변수나 설정 파일에서 읽어오기)
            # bgf.login("your_id", "your_password")

            # 수동 조작을 위해 대기 (개발/테스트용)
            logger.info("브라우저를 수동으로 조작할 수 있습니다.")
            logger.info("종료하려면 Enter 키를 누르세요...")
            input()
        else:
            logger.error("접속에 실패했습니다.")


if __name__ == "__main__":
    main()
