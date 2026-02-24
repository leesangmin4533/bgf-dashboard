"""
StateGuard - 화면 상태 보장 모듈

로직 진입 전 화면 상태를 확인하고 보장합니다.
Selenium WebDriver 기반 (BGF 넥사크로 시스템용)

사용법:
    from src.core.state_guard import StateGuard

    guard = StateGuard(driver)

    # 홈화면 상태 보장
    if guard.ensure_state("home"):
        # 안전하게 다음 작업 수행
        enter_single_order()

    # 데코레이터 사용
    @require_state("home")
    def my_function(self):
        pass
"""

import time
from typing import Any, Optional, List, Callable
from dataclasses import dataclass
from functools import wraps

from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import JavascriptException, TimeoutException

from .state_config import (
    ScreenState, ScreenSignature,
    SCREEN_STATES, get_screen_state
)
from .obstacles import (
    Obstacle, ObstacleAction, ObstaclePriority,
    get_obstacles_by_priority, CLOSE_ALL_POPUPS_JS
)

try:
    from src.utils.logger import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


# =============================================================================
# 예외 클래스
# =============================================================================

class StateGuardError(Exception):
    """StateGuard 관련 예외"""
    pass


class NavigationError(StateGuardError):
    """화면 이동 실패"""
    pass


class ObstacleClearError(StateGuardError):
    """방해 요소 제거 실패"""
    pass


class SessionExpiredError(StateGuardError):
    """세션 만료"""
    pass


# =============================================================================
# 결과 데이터 클래스
# =============================================================================

@dataclass
class StateCheckResult:
    """상태 확인 결과"""
    current_state: str          # 현재 상태 ID
    current_state_name: str     # 현재 상태 이름
    is_target: bool             # 목표 상태인지
    obstacles_found: int        # 발견된 방해 요소 수
    obstacles_cleared: int      # 제거된 방해 요소 수
    confidence: float           # 확신도 (0.0 ~ 1.0)


@dataclass
class NavigationResult:
    """화면 이동 결과"""
    success: bool
    from_state: str
    to_state: str
    steps_executed: int
    error_message: Optional[str] = None


# =============================================================================
# StateGuard 메인 클래스
# =============================================================================

class StateGuard:
    """
    화면 상태 보장 모듈

    주요 기능:
    1. 현재 화면 상태 감지
    2. 방해 요소(팝업 등) 자동 제거
    3. 목표 화면으로 안전하게 이동
    4. 상태 보장 후 로직 실행
    """

    def __init__(
        self,
        driver: WebDriver,
        login_handler: Optional[Callable] = None,
        timeout: float = 5.0,
        retry_count: int = 3
    ):
        """
        Args:
            driver: Selenium WebDriver 객체
            login_handler: 재로그인 함수 (세션 만료 시 호출)
            timeout: 요소 대기 타임아웃 (초)
            retry_count: 재시도 횟수
        """
        self.driver = driver
        self.login_handler = login_handler
        self.timeout = timeout
        self.retry_count = retry_count

        # 상태 기록
        self._last_known_state: Optional[str] = None
        self._obstacle_history: List[str] = []

    # =========================================================================
    # 핵심 메서드
    # =========================================================================

    def ensure_state(
        self,
        target_state: str,
        clear_obstacles: bool = True,
        force_navigate: bool = False
    ) -> bool:
        """
        ⭐ 핵심: 목표 상태 보장

        Args:
            target_state: 목표 상태 ID (예: "home", "single_order", "logged_in")
            clear_obstacles: 방해 요소 자동 제거 여부
            force_navigate: 현재 상태와 같아도 강제 이동

        Returns:
            성공 여부

        동작:
            1. 방해 요소 제거 (옵션)
            2. 현재 상태 확인
            3. 목표와 다르면 이동
            4. 최종 확인

        사용 예시:
            guard = StateGuard(driver)

            if guard.ensure_state("home"):
                # 이제 확실히 홈화면
                click_menu("단품별발주")
            else:
                raise Exception("홈화면 진입 실패")
        """
        logger.info(f"상태 보장 시작: 목표={target_state}")

        for attempt in range(self.retry_count):
            try:
                # 1. 방해 요소 제거
                if clear_obstacles:
                    cleared = self.clear_all_obstacles()
                    if cleared > 0:
                        logger.info(f"방해 요소 {cleared}개 제거됨")

                # 2. 현재 상태 확인
                current = self.detect_current_state()
                logger.debug(f"현재 상태: {current}")

                # 3. 이미 목표 상태면 성공
                if current == target_state and not force_navigate:
                    logger.info(f"이미 목표 상태: {target_state}")
                    self._last_known_state = target_state
                    return True

                # 4. 특수 케이스: logged_in 체크
                if target_state == "logged_in":
                    if self._check_logged_in():
                        self._last_known_state = "logged_in"
                        return True
                    else:
                        # 재로그인 시도
                        if self._handle_relogin():
                            return True
                        return False

                # 5. 목표로 이동
                nav_result = self.navigate_to(target_state, from_state=current)

                if nav_result.success:
                    # 6. 최종 확인
                    final_state = self.detect_current_state()
                    if final_state == target_state:
                        logger.info(f"상태 보장 성공: {target_state}")
                        self._last_known_state = target_state
                        return True
                    else:
                        logger.warning(f"이동 후 상태 불일치: 기대={target_state}, 실제={final_state}")

            except SessionExpiredError:
                logger.warning("세션 만료 감지, 재로그인 시도")
                if self._handle_relogin():
                    continue  # 재로그인 후 다시 시도
                return False

            except Exception as e:
                logger.warning(f"상태 보장 시도 {attempt + 1} 실패: {e}")
                time.sleep(1)

        logger.error(f"상태 보장 실패: {target_state}")
        return False

    def detect_current_state(self) -> str:
        """
        현재 화면 상태 감지

        Returns:
            상태 ID (예: "home", "single_order", "unknown")
        """
        best_match = "unknown"
        best_score = 0.0

        for state_id, state in SCREEN_STATES.items():
            score = self._calculate_state_match_score(state)

            if score > best_score:
                best_score = score
                best_match = state_id

        # 최소 신뢰도 체크
        if best_score < 0.5:
            logger.debug(f"상태 감지 신뢰도 낮음: {best_match} ({best_score:.2f})")
            # 팝업만 확인해서 홈인지 체크
            if self._is_home_state():
                return "home"
            return "unknown"

        logger.debug(f"상태 감지: {best_match} (신뢰도: {best_score:.2f})")
        return best_match

    def _calculate_state_match_score(self, state: ScreenState) -> float:
        """화면 상태 매칭 점수 계산 (0.0 ~ 1.0)"""
        if not state.signatures:
            return 0.0

        matched = 0
        total = len(state.signatures)

        for sig in state.signatures:
            try:
                result = self._check_signature(sig)
                if result == sig.expected:
                    matched += 1
            except Exception:
                pass

        return matched / total if total > 0 else 0.0

    def _check_signature(self, sig: ScreenSignature) -> Any:
        """시그니처 확인"""
        if sig.check_type == "js_var":
            return self._execute_js(f"return {sig.check_value};")
        elif sig.check_type == "js_func":
            return self._execute_js(f"return {sig.check_value};")
        elif sig.check_type == "url":
            return sig.check_value in self.driver.current_url
        else:
            return None

    def _is_home_state(self) -> bool:
        """홈 상태인지 간단 체크 (탭 없음)"""
        try:
            result = self._execute_js("""
                (function() {
                    try {
                        var tabs = nexacro.getApplication().mainframe.HFrameSet00.LeftFrame.form.tab_openList;
                        return tabs.tabcount === 0;
                    } catch(e) { return false; }
                })()
            """)
            return result == True
        except Exception:
            return False

    def _check_logged_in(self) -> bool:
        """로그인 상태 확인"""
        try:
            result = self._execute_js("""
                (function() {
                    try {
                        var app = nexacro.getApplication();
                        var channelType = app.gvars.getVariable('GV_CHANNELTYPE');
                        return channelType === 'HOME';
                    } catch(e) { return false; }
                })()
            """)
            return result == True
        except Exception:
            return False

    # =========================================================================
    # 방해 요소 처리
    # =========================================================================

    def clear_all_obstacles(self, max_iterations: int = 10) -> int:
        """
        모든 방해 요소 제거

        Args:
            max_iterations: 최대 반복 횟수 (무한 루프 방지)

        Returns:
            제거된 방해 요소 수
        """
        total_cleared = 0
        obstacles = get_obstacles_by_priority()

        for iteration in range(max_iterations):
            cleared_this_round = 0

            for obstacle in obstacles:
                retry = 0
                while self._is_obstacle_present(obstacle) and retry < obstacle.max_retry:
                    success = self._handle_obstacle(obstacle)
                    if success:
                        cleared_this_round += 1
                        total_cleared += 1
                        self._obstacle_history.append(obstacle.id)
                    else:
                        break  # 처리 실패하면 다음 장애물로

                    time.sleep(obstacle.wait_after_ms / 1000)
                    retry += 1

            # 이번 라운드에서 제거한 게 없으면 종료
            if cleared_this_round == 0:
                break

        return total_cleared

    def clear_popups(self) -> int:
        """팝업만 빠르게 닫기"""
        try:
            result = self._execute_js(CLOSE_ALL_POPUPS_JS)
            return int(result) if result else 0
        except Exception:
            return 0

    def _is_obstacle_present(self, obstacle: Obstacle) -> bool:
        """방해 요소 존재 여부 확인"""
        try:
            result = self._execute_js(f"return {obstacle.detect_js};")
            return result == True
        except Exception:
            return False

    def _handle_obstacle(self, obstacle: Obstacle) -> bool:
        """
        방해 요소 처리

        Returns:
            처리 성공 여부
        """
        if obstacle.log_when_found:
            logger.info(f"방해 요소 발견: {obstacle.name}")

        try:
            if obstacle.action == ObstacleAction.EXECUTE_JS:
                if obstacle.action_js:
                    self._execute_js(obstacle.action_js)
                    return True

            elif obstacle.action == ObstacleAction.CLICK_CLOSE:
                return self._click_close_button()

            elif obstacle.action == ObstacleAction.CLICK_CONFIRM:
                return self._click_confirm_button()

            elif obstacle.action == ObstacleAction.PRESS_ESCAPE:
                self.driver.find_element("tag name", "body").send_keys(Keys.ESCAPE)
                return True

            elif obstacle.action == ObstacleAction.RE_LOGIN:
                return self._handle_relogin()

            elif obstacle.action == ObstacleAction.WAIT_AND_RETRY:
                time.sleep(obstacle.wait_after_ms / 1000)
                return True

            elif obstacle.action == ObstacleAction.IGNORE:
                return True

            else:
                logger.warning(f"알 수 없는 처리 방법: {obstacle.action}")
                return False

        except Exception as e:
            logger.error(f"방해 요소 처리 실패 ({obstacle.name}): {e}")
            return False

    def _click_close_button(self) -> bool:
        """닫기 버튼 클릭"""
        js = """
        (function() {
            try {
                var app = nexacro.getApplication();
                var frames = app.popupframes;
                if (frames) {
                    var keys = Object.keys(frames);
                    for (var i = keys.length - 1; i >= 0; i--) {
                        var frame = frames[keys[i]];
                        if (frame && frame.form) {
                            var btnNames = ['btn_close', 'btn_cancel', 'Button01', 'btnClose'];
                            for (var j = 0; j < btnNames.length; j++) {
                                var btn = frame.form[btnNames[j]];
                                if (btn && btn.click) {
                                    btn.click();
                                    return true;
                                }
                            }
                            frame.close();
                            return true;
                        }
                    }
                }
                return false;
            } catch(e) { return false; }
        })()
        """
        try:
            result = self._execute_js(js)
            return result == True
        except Exception:
            return False

    def _click_confirm_button(self) -> bool:
        """확인 버튼 클릭"""
        js = """
        (function() {
            try {
                var app = nexacro.getApplication();
                var frames = app.popupframes;
                if (frames) {
                    var keys = Object.keys(frames);
                    for (var i = keys.length - 1; i >= 0; i--) {
                        var frame = frames[keys[i]];
                        if (frame && frame.form) {
                            var btnNames = ['btn_ok', 'btn_confirm', 'Button00', 'btnOK'];
                            for (var j = 0; j < btnNames.length; j++) {
                                var btn = frame.form[btnNames[j]];
                                if (btn && btn.click) {
                                    btn.click();
                                    return true;
                                }
                            }
                        }
                    }
                }
                return false;
            } catch(e) { return false; }
        })()
        """
        try:
            result = self._execute_js(js)
            return result == True
        except Exception:
            return False

    def _handle_relogin(self) -> bool:
        """재로그인 처리"""
        if self.login_handler:
            logger.info("재로그인 시도")
            try:
                self.login_handler()
                time.sleep(1)
                return self._check_logged_in()
            except Exception as e:
                logger.error(f"재로그인 실패: {e}")
                return False
        else:
            logger.error("로그인 핸들러가 설정되지 않음")
            raise SessionExpiredError("세션 만료 - 로그인 핸들러 없음")

    # =========================================================================
    # 화면 이동
    # =========================================================================

    def navigate_to(
        self,
        target_state: str,
        from_state: Optional[str] = None
    ) -> NavigationResult:
        """
        목표 화면으로 이동

        Args:
            target_state: 목표 상태 ID
            from_state: 현재 상태 (None이면 자동 감지)

        Returns:
            NavigationResult
        """
        if from_state is None:
            from_state = self.detect_current_state()

        target = get_screen_state(target_state)
        if not target:
            return NavigationResult(
                success=False,
                from_state=from_state,
                to_state=target_state,
                steps_executed=0,
                error_message=f"알 수 없는 상태: {target_state}"
            )

        # 이미 목표 상태면 성공
        if from_state == target_state:
            return NavigationResult(
                success=True,
                from_state=from_state,
                to_state=target_state,
                steps_executed=0
            )

        # 홈으로 가는 경우
        if target_state == "home":
            return self._navigate_to_home(from_state)

        # 다른 화면으로 가는 경우: 홈 경유
        if from_state != "home":
            home_result = self._navigate_to_home(from_state)
            if not home_result.success:
                return NavigationResult(
                    success=False,
                    from_state=from_state,
                    to_state=target_state,
                    steps_executed=home_result.steps_executed,
                    error_message="홈 이동 실패"
                )
            from_state = "home"
            time.sleep(0.5)

        # 홈에서 목표로 이동
        if target.navigation_js:
            try:
                self._execute_js(target.navigation_js)
                time.sleep(1)
                return NavigationResult(
                    success=True,
                    from_state=from_state,
                    to_state=target_state,
                    steps_executed=1
                )
            except Exception as e:
                return NavigationResult(
                    success=False,
                    from_state=from_state,
                    to_state=target_state,
                    steps_executed=0,
                    error_message=str(e)
                )

        return NavigationResult(
            success=False,
            from_state=from_state,
            to_state=target_state,
            steps_executed=0,
            error_message="네비게이션 방법 없음"
        )

    def _navigate_to_home(self, from_state: str) -> NavigationResult:
        """홈화면으로 이동 (모든 탭 닫기)"""
        try:
            # 방법 1: 해당 화면의 back_to_home 사용
            source = get_screen_state(from_state)
            if source and source.back_to_home_js:
                self._execute_js(source.back_to_home_js)
                time.sleep(0.5)

            # 방법 2: 모든 탭 닫기
            js = """
            (function() {
                try {
                    var app = nexacro.getApplication();
                    var tabs = app.mainframe.HFrameSet00.LeftFrame.form.tab_openList;
                    var closed = 0;
                    while (tabs.tabcount > 0) {
                        var closeBtn = tabs.tabbuttons[0].extrabutton;
                        if (closeBtn) {
                            closeBtn.click();
                            closed++;
                        } else {
                            break;
                        }
                    }
                    return closed;
                } catch(e) { return 0; }
            })()
            """
            closed = self._execute_js(js)
            time.sleep(0.3)

            # 확인
            if self._is_home_state():
                return NavigationResult(
                    success=True,
                    from_state=from_state,
                    to_state="home",
                    steps_executed=int(closed) if closed else 1
                )

            # 방법 3: ESC 여러 번
            for _ in range(5):
                try:
                    self.driver.find_element("tag name", "body").send_keys(Keys.ESCAPE)
                    time.sleep(0.3)
                    if self._is_home_state():
                        return NavigationResult(
                            success=True,
                            from_state=from_state,
                            to_state="home",
                            steps_executed=1
                        )
                except Exception:
                    pass

            return NavigationResult(
                success=False,
                from_state=from_state,
                to_state="home",
                steps_executed=0,
                error_message="홈 이동 실패"
            )

        except Exception as e:
            return NavigationResult(
                success=False,
                from_state=from_state,
                to_state="home",
                steps_executed=0,
                error_message=str(e)
            )

    # =========================================================================
    # JavaScript 실행
    # =========================================================================

    def _execute_js(self, js_code: str) -> Any:
        """JavaScript 실행"""
        try:
            return self.driver.execute_script(js_code)
        except JavascriptException as e:
            logger.debug(f"JS 실행 실패: {e}")
            raise

    # =========================================================================
    # 유틸리티
    # =========================================================================

    def get_obstacle_history(self) -> List[str]:
        """처리한 방해 요소 이력"""
        return self._obstacle_history.copy()

    def get_last_known_state(self) -> Optional[str]:
        """마지막으로 확인된 상태"""
        return self._last_known_state

    def reset_history(self) -> None:
        """이력 초기화"""
        self._obstacle_history.clear()
        self._last_known_state = None

    def wait_for_loading(self, timeout: float = 30.0) -> bool:
        """로딩 완료 대기"""
        start = time.time()
        while time.time() - start < timeout:
            try:
                loading = self._execute_js("""
                    (function() {
                        try {
                            var app = nexacro.getApplication();
                            if (app.gvars && app.gvars.getVariable('GV_LOADING') === 'Y') return true;
                            if (document.body.style.cursor === 'wait') return true;
                            return false;
                        } catch(e) { return false; }
                    })()
                """)
                if not loading:
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False


# =============================================================================
# 데코레이터
# =============================================================================

def require_state(required_state: str, clear_obstacles: bool = True):
    """
    상태 보장 데코레이터

    사용법:
        class OrderExecutor:
            def __init__(self, driver):
                self.guard = StateGuard(driver)

            @require_state("home")
            def enter_single_order(self):
                # 홈화면 상태가 보장된 후 실행됨
                click_menu("단품별발주")
    """
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            # self.guard가 있어야 함
            if not hasattr(self, 'guard'):
                raise StateGuardError("클래스에 'guard' 속성이 필요합니다")

            if not self.guard.ensure_state(required_state, clear_obstacles):
                raise NavigationError(f"상태 보장 실패: {required_state}")

            return func(self, *args, **kwargs)

        return wrapper
    return decorator


def with_popup_cleared(func):
    """
    팝업 제거 후 실행 데코레이터

    사용법:
        @with_popup_cleared
        def my_function(self):
            pass
    """
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if hasattr(self, 'guard'):
            self.guard.clear_all_obstacles()
        return func(self, *args, **kwargs)
    return wrapper


# =============================================================================
# 테스트
# =============================================================================

if __name__ == "__main__":
    print("StateGuard 모듈 로드 테스트")
    print("=" * 60)

    # 클래스 및 함수 임포트 확인
    print(f"StateGuard: {StateGuard}")
    print(f"require_state: {require_state}")
    print(f"StateGuardError: {StateGuardError}")
    print(f"SCREEN_STATES: {list(SCREEN_STATES.keys())}")
    print(f"Obstacles: {len(get_obstacles_by_priority())}개")
    print("\n모듈 로드 성공!")
