"""
넥사크로(Nexacro) 웹 자동화 헬퍼 모듈
넥사크로 기반 웹사이트의 특수한 구조를 처리하기 위한 유틸리티
"""

from typing import Any, Dict, List, Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.remote.webelement import WebElement
import time

from src.utils.logger import get_logger

logger = get_logger(__name__)


class NexacroHelper:
    """넥사크로 컴포넌트 조작을 위한 헬퍼 클래스"""

    def __init__(self, driver: Any, timeout: int = 10) -> None:
        self.driver = driver
        self.timeout = timeout

    def wait_for_nexacro_load(self, wait_time: int = 5) -> bool:
        """넥사크로 프레임워크 로딩 완료 대기"""
        time.sleep(wait_time)  # 초기 로딩 대기

        # nexacro 객체 존재 확인
        try:
            self.driver.execute_script("""
                return typeof nexacro !== 'undefined';
            """)
            logger.info("넥사크로 프레임워크 로딩 완료")
            return True
        except Exception as e:
            logger.warning(f"넥사크로 로딩 확인 실패: {e}")
            return False

    def find_nexacro_component(self, component_id: str) -> Optional[WebElement]:
        """넥사크로 컴포넌트 ID로 요소 찾기"""
        try:
            # 넥사크로 컴포넌트는 보통 id 속성에 컴포넌트명이 포함됨
            element = self.driver.find_element(By.CSS_SELECTOR, f"[id*='{component_id}']")
            return element
        except NoSuchElementException:
            # div 내부의 input 요소 찾기 시도
            try:
                element = self.driver.find_element(By.CSS_SELECTOR, f"[id*='{component_id}'] input")
                return element
            except NoSuchElementException:
                return None

    def click_nexacro_button(self, button_id: str) -> bool:
        """넥사크로 버튼 클릭"""
        try:
            # JavaScript를 통한 클릭 (넥사크로 컴포넌트는 JS 클릭이 더 안정적)
            script = f"""
                var btn = document.querySelector("[id*='{button_id}']");
                if (btn) {{
                    btn.click();
                    return true;
                }}
                return false;
            """
            result = self.driver.execute_script(script)
            if result:
                logger.info(f"버튼 클릭 성공: {button_id}")
            return result
        except Exception as e:
            logger.error(f"버튼 클릭 실패: {button_id}, {e}")
            return False

    def input_nexacro_text(self, component_id: str, text: str) -> bool:
        """넥사크로 입력 필드에 텍스트 입력"""
        try:
            # 먼저 input 요소 찾기
            element = self.driver.find_element(By.CSS_SELECTOR, f"[id*='{component_id}'] input")
            element.clear()
            element.send_keys(text)
            logger.info(f"텍스트 입력 성공: {component_id}")
            return True
        except NoSuchElementException:
            # 직접 컴포넌트에 값 설정 시도
            try:
                script = f"""
                    var comp = document.querySelector("[id*='{component_id}']");
                    if (comp) {{
                        var input = comp.querySelector('input') || comp;
                        input.value = '{text}';
                        input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        return true;
                    }}
                    return false;
                """
                return self.driver.execute_script(script)
            except Exception as e:
                logger.error(f"텍스트 입력 실패: {component_id}, {e}")
                return False

    def get_nexacro_value(self, component_id: str) -> Optional[str]:
        """넥사크로 컴포넌트의 값 가져오기"""
        try:
            script = f"""
                var comp = document.querySelector("[id*='{component_id}']");
                if (comp) {{
                    var input = comp.querySelector('input');
                    return input ? input.value : comp.textContent;
                }}
                return null;
            """
            return self.driver.execute_script(script)
        except Exception as e:
            logger.error(f"값 가져오기 실패: {component_id}, {e}")
            return None

    def wait_for_element(self, selector: str, timeout: Optional[int] = None) -> Optional[WebElement]:
        """요소가 나타날 때까지 대기"""
        if timeout is None:
            timeout = self.timeout
        try:
            element = WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
            )
            return element
        except TimeoutException:
            logger.warning(f"요소 대기 시간 초과: {selector}")
            return None

    def execute_nexacro_script(self, script: str) -> Any:
        """넥사크로 환경에서 스크립트 실행"""
        try:
            return self.driver.execute_script(script)
        except Exception as e:
            logger.error(f"스크립트 실행 실패: {e}")
            return None

    def get_all_components(self) -> List[str]:
        """페이지의 모든 넥사크로 컴포넌트 ID 목록 가져오기 (디버깅용)"""
        script = """
            var elements = document.querySelectorAll('[id]');
            var ids = [];
            elements.forEach(function(el) {
                if (el.id) ids.push(el.id);
            });
            return ids;
        """
        return self.driver.execute_script(script)

    def capture_network_requests(self) -> bool:
        """네트워크 요청 캡처 설정 (API 분석용)"""
        script = """
            window._capturedRequests = [];
            var originalXHR = window.XMLHttpRequest;
            function newXHR() {
                var xhr = new originalXHR();
                var originalOpen = xhr.open;
                xhr.open = function() {
                    window._capturedRequests.push({
                        method: arguments[0],
                        url: arguments[1],
                        timestamp: new Date().toISOString()
                    });
                    originalOpen.apply(this, arguments);
                };
                return xhr;
            }
            window.XMLHttpRequest = newXHR;
            return true;
        """
        return self.driver.execute_script(script)

    def get_captured_requests(self) -> List[Dict[str, str]]:
        """캡처된 네트워크 요청 가져오기"""
        script = "return window._capturedRequests || [];"
        return self.driver.execute_script(script)
