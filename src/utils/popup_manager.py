"""
팝업 자동 정리 유틸리티
- 넥사크로 기반 BGF 시스템에서 예기치 않게 남아있는 팝업 처리
- 데코레이터를 통한 자동 정리
"""

import time
from functools import wraps
from typing import Any, Callable, Optional, Literal

from selenium.common.exceptions import (
    NoAlertPresentException,
    WebDriverException,
    JavascriptException,
    TimeoutException
)

from src.utils.logger import get_logger

logger = get_logger(__name__)

# 타임아웃 및 대기 시간 상수
POPUP_CLOSE_TIMEOUT = 5.0  # JavaScript 실행 타임아웃 (초)
POPUP_CLOSE_SETTLE_TIME = 0.3  # 팝업 닫힌 후 DOM 안정화 대기 (초)
ALERT_CLOSE_SETTLE_TIME = 0.1  # Alert 닫힌 후 대기 (초)


def close_all_popups(driver: Any, silent: bool = False) -> int:
    """
    현재 열려있는 모든 팝업 닫기 (Selenium 물리 클릭 사용)

    Args:
        driver: Selenium WebDriver
        silent: True면 로그 레벨을 debug로 낮춤

    Returns:
        닫은 팝업 개수
    """
    if not driver:
        return 0

    try:
        from selenium.webdriver.common.by import By
        from selenium.common.exceptions import NoSuchElementException, ElementNotInteractableException

        # 1단계: JavaScript로 닫기 버튼의 ID 목록 수집
        button_ids = driver.execute_script("""
            var buttonIds = [];
            var diagnostics = [];

            try {
                var allButtons = document.querySelectorAll('.Button');
                diagnostics.push('전체 .Button: ' + allButtons.length + '개');

                var visibleButtons = 0;

                for (var i = 0; i < allButtons.length; i++) {
                    var btn = allButtons[i];
                    if (!btn.offsetParent) continue;
                    visibleButtons++;

                    var className = btn.className || '';
                    var btnId = btn.id || '';
                    var btnText = (btn.textContent || btn.innerText || '').trim();

                    // 팝업 닫기 버튼 패턴 감지
                    var isCloseButton = false;

                    // 패턴 1: btn_PF_close (팝업 X 버튼)
                    if (className.indexOf('btn_PF_close') >= 0) {
                        isCloseButton = true;
                    }
                    // 패턴 2: btn_WF_action + 정확히 "닫기" 텍스트만
                    else if (className.indexOf('btn_WF_action') >= 0) {
                        // 공백 제거 후 정확히 "닫기"인지 확인
                        if (btnText === '닫기') {
                            isCloseButton = true;
                        }
                    }
                    // 패턴 3: ID에 btn_close, btn_closeTop 포함
                    else if (btnId.indexOf('btn_close') >= 0 ||
                             btnId.indexOf('btn_Close') >= 0 ||
                             btnId.indexOf('btn_closeTop') >= 0) {
                        isCloseButton = true;
                    }

                    if (isCloseButton && btnId) {
                        buttonIds.push(btnId);
                    }
                }

                diagnostics.push('보이는 버튼: ' + visibleButtons + '개');
                diagnostics.push('닫기 버튼: ' + buttonIds.length + '개');

            } catch(e) {
                diagnostics.push('오류: ' + e.message);
            }

            return {
                ids: buttonIds,
                diagnostics: diagnostics
            };
        """)

        # JavaScript 실행 결과 검증
        if button_ids is None:
            logger.warning("팝업 조회 실패 (None 반환)")
            return 0

        # JavaScript 결과 처리
        ids_to_click = button_ids.get('ids', [])
        diagnostics = button_ids.get('diagnostics', [])

        # 진단 정보 로깅 (debug 레벨로 변경 - 반복 호출 시 로그 과다 방지)
        if not silent and diagnostics:
            for diag in diagnostics:
                logger.debug(f"[진단] {diag}")

        # 2단계: Selenium 물리 클릭으로 버튼 클릭
        # ★ implicitly_wait를 일시적으로 0으로 설정하여 요소 찾기 지연 방지
        # (기본 10초 대기가 버튼 개수만큼 누적되어 40~60초 지연 발생 방지)
        original_wait = 10  # 기본값
        try:
            # Selenium 4: timeouts 객체에서 현재 implicit_wait 값 가져오기 (초 단위)
            original_wait = driver.timeouts.implicit_wait
        except Exception:
            pass  # Selenium 3 또는 오류 시 기본값 사용

        closed_count = 0
        try:
            driver.implicitly_wait(0)  # 즉시 반환 (요소 없으면 바로 예외)

            for btn_id in ids_to_click:
                try:
                    # ID로 요소 찾기 (implicitly_wait=0이므로 즉시 반환)
                    element = driver.find_element(By.ID, btn_id)

                    # 실제 물리 클릭
                    element.click()
                    closed_count += 1

                    if not silent:
                        short_id = btn_id.split('.')[-1] if '.' in btn_id else btn_id
                        logger.debug(f"팝업 버튼 클릭: {short_id}")

                except NoSuchElementException:
                    if not silent:
                        logger.debug(f"버튼 요소 없음: {btn_id[:50]}")
                except ElementNotInteractableException:
                    if not silent:
                        logger.debug(f"버튼 클릭 불가: {btn_id[:50]}")
                except Exception as e:
                    if not silent:
                        logger.debug(f"버튼 클릭 오류: {btn_id[:50]} - {e}")
        finally:
            # 원래 implicit_wait 값 복원
            driver.implicitly_wait(original_wait)

        if closed_count > 0:
            log_fn = logger.debug if silent else logger.info
            log_fn(f"팝업 {closed_count}개 닫음")
            time.sleep(POPUP_CLOSE_SETTLE_TIME)  # 팝업 닫힌 후 DOM 안정화 대기

        return closed_count

    except JavascriptException as e:
        logger.error(f"JavaScript 실행 오류 (팝업 닫기): {e}")
        return 0
    except TimeoutException as e:
        logger.warning(f"팝업 닫기 타임아웃 ({POPUP_CLOSE_TIMEOUT}초): {e}")
        return 0
    except WebDriverException as e:
        logger.warning(f"드라이버 오류 (팝업 닫기): {e}")
        return 0
    except Exception as e:
        logger.error(f"예기치 않은 오류 (팝업 닫기): {e}")
        return 0


def close_alerts(driver: Any, max_attempts: int = 3, silent: bool = False) -> int:
    """
    현재 열려있는 Alert 모두 처리 (Accept)

    Args:
        driver: Selenium WebDriver
        max_attempts: 최대 시도 횟수
        silent: True면 로그 레벨을 debug로 낮춤

    Returns:
        처리한 Alert 개수
    """
    if not driver:
        return 0

    closed_count = 0
    log_fn = logger.debug if silent else logger.info

    for _ in range(max_attempts):
        try:
            alert = driver.switch_to.alert
            alert_text = alert.text
            alert.accept()
            closed_count += 1
            log_fn(f"Alert 처리: {alert_text[:50]}")
            time.sleep(ALERT_CLOSE_SETTLE_TIME)
        except NoAlertPresentException:
            # Alert이 없으면 정상 종료
            break
        except WebDriverException as e:
            logger.warning(f"Alert 처리 중 드라이버 오류: {e}")
            break
        except Exception as e:
            logger.error(f"Alert 처리 중 예기치 않은 오류: {e}")
            break

    return closed_count


def _cleanup_driver(driver: Any, include_alerts: bool, silent: bool) -> None:
    """
    내부 헬퍼: 팝업과 Alert 정리

    Args:
        driver: Selenium WebDriver
        include_alerts: Alert도 처리할지 여부
        silent: 로그 레벨을 debug로 낮출지 여부
    """
    if not driver:
        return
    if include_alerts:
        close_alerts(driver, silent=silent)
    close_all_popups(driver, silent=silent)


def auto_close_popups(
    when: Literal["before", "after", "both"] = "both",
    include_alerts: bool = True,
    silent: bool = False
) -> Callable:
    """
    메서드 실행 전/후 자동으로 팝업 닫기 데코레이터

    Args:
        when: "before" (실행 전), "after" (실행 후), "both" (전후)
        include_alerts: True면 Alert도 함께 처리
        silent: True면 로그 레벨을 debug로 낮춤

    Usage:
        @auto_close_popups(when="after")
        def collect_for_item(self, item_cd: str):
            # 작업 수행
            return result

    Example:
        @auto_close_popups(when="both", include_alerts=True)
        def navigate_to_menu(self) -> bool:
            # 메뉴 이동 전 팝업 정리
            # 작업...
            # 메뉴 이동 후 팝업 정리
            return True
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # driver 추출 (여러 방식 지원)
            driver = None

            # 1. self.driver 우선 (클래스 메서드)
            if args and hasattr(args[0], 'driver'):
                driver = args[0].driver
            # 2. kwargs에서 driver 찾기
            elif 'driver' in kwargs:
                driver = kwargs['driver']
            # 3. 첫 번째 인자가 WebDriver인 경우 (함수 스타일)
            elif args and hasattr(args[0], 'execute_script'):
                driver = args[0]

            # Before: 메서드 실행 전 팝업 닫기
            if when in ("before", "both") and driver:
                _cleanup_driver(driver, include_alerts, silent)

            # 실제 메서드 실행
            try:
                result = func(*args, **kwargs)
            except Exception as e:
                # 예외 발생 시에도 팝업 정리 (after인 경우)
                if when in ("after", "both") and driver:
                    _cleanup_driver(driver, include_alerts, silent)
                raise e

            # After: 메서드 실행 후 팝업 닫기
            if when in ("after", "both") and driver:
                _cleanup_driver(driver, include_alerts, silent)

            return result

        return wrapper
    return decorator


class PopupCleaner:
    """
    컨텍스트 매니저로 팝업 자동 정리

    Usage:
        with PopupCleaner(driver):
            # 이 블록 내에서 팝업 걱정 없이 작업
            result = some_operation()
    """

    def __init__(
        self,
        driver: Any,
        clean_on_enter: bool = True,
        clean_on_exit: bool = True,
        include_alerts: bool = True,
        silent: bool = False
    ):
        """
        Args:
            driver: Selenium WebDriver
            clean_on_enter: 진입 시 팝업 닫기 여부
            clean_on_exit: 종료 시 팝업 닫기 여부
            include_alerts: Alert도 처리할지 여부
            silent: 로그 레벨을 debug로 낮출지 여부
        """
        self.driver = driver
        self.clean_on_enter = clean_on_enter
        self.clean_on_exit = clean_on_exit
        self.include_alerts = include_alerts
        self.silent = silent

    def __enter__(self):
        if self.clean_on_enter:
            _cleanup_driver(self.driver, self.include_alerts, self.silent)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.clean_on_exit:
            _cleanup_driver(self.driver, self.include_alerts, self.silent)
        return False  # 예외를 다시 발생시킴


# 편의 함수: 드라이버에서 즉시 팝업 정리
def clean_screen(driver: Any, include_alerts: bool = True, silent: bool = False) -> None:
    """
    화면 정리: Alert + 팝업 모두 닫기

    Args:
        driver: Selenium WebDriver
        include_alerts: Alert도 처리할지 여부
        silent: 로그 레벨을 debug로 낮출지 여부
    """
    _cleanup_driver(driver, include_alerts, silent)
