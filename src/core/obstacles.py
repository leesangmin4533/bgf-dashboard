"""
BGF 시스템 방해 요소 (팝업, 알림 등) 정의

각 방해 요소의 감지 방법과 처리 방법을 정의합니다.
넥사크로 기반 시스템이므로 JavaScript로 팝업을 처리합니다.
"""

from dataclasses import dataclass
from typing import Optional, List
from enum import Enum


class ObstaclePriority(Enum):
    """방해 요소 처리 우선순위"""
    CRITICAL = 0   # 최우선 (세션 만료 등)
    HIGH = 1       # 높음 (에러 팝업)
    MEDIUM = 2     # 중간 (공지 팝업)
    LOW = 3        # 낮음 (안내 메시지)


class ObstacleAction(Enum):
    """방해 요소 처리 방법"""
    CLICK_CLOSE = "click_close"           # 닫기 버튼 클릭
    CLICK_CONFIRM = "click_confirm"       # 확인 버튼 클릭
    CLICK_CANCEL = "click_cancel"         # 취소 버튼 클릭
    PRESS_ESCAPE = "press_escape"         # ESC 키
    RE_LOGIN = "re_login"                 # 재로그인
    EXECUTE_JS = "execute_js"             # JavaScript 실행
    WAIT_AND_RETRY = "wait_and_retry"     # 대기 후 재시도
    IGNORE = "ignore"                     # 무시


@dataclass
class Obstacle:
    """방해 요소 정의"""
    id: str                                # 고유 ID
    name: str                              # 한글 이름
    description: str                       # 설명

    # 감지 방법 (JavaScript 코드, 결과가 true면 방해 요소 존재)
    detect_js: str

    # 처리 방법
    action: ObstacleAction = ObstacleAction.CLICK_CLOSE
    action_js: Optional[str] = None        # 처리할 JavaScript 코드

    # 우선순위
    priority: ObstaclePriority = ObstaclePriority.MEDIUM

    # 옵션
    wait_after_ms: int = 300               # 처리 후 대기 시간
    max_retry: int = 3                     # 최대 재시도 횟수
    log_when_found: bool = True            # 발견 시 로그 기록


# =============================================================================
# BGF 넥사크로 팝업 감지/처리 JS
# =============================================================================

# 공통: 모든 팝업 프레임 닫기
CLOSE_ALL_POPUPS_JS = """
(function() {
    var closed = 0;
    try {
        var app = nexacro.getApplication();
        var frames = app.popupframes;
        if (frames) {
            var keys = Object.keys(frames);
            for (var i = keys.length - 1; i >= 0; i--) {
                var frame = frames[keys[i]];
                if (frame && frame.form) {
                    // 닫기 버튼 찾기
                    var closeBtn = frame.form.btn_close || frame.form.Button00;
                    if (closeBtn) {
                        closeBtn.click();
                        closed++;
                    } else {
                        // 버튼 없으면 직접 닫기
                        frame.close();
                        closed++;
                    }
                }
            }
        }
    } catch(e) {}
    return closed;
})()
"""

# 팝업 존재 여부 확인
CHECK_POPUP_EXISTS_JS = """
(function() {
    try {
        var app = nexacro.getApplication();
        var frames = app.popupframes;
        if (frames) {
            var keys = Object.keys(frames);
            return keys.length > 0;
        }
        return false;
    } catch(e) { return false; }
})()
"""

# 특정 텍스트 포함 팝업 확인
def get_popup_with_text_js(text: str) -> str:
    return f"""
(function() {{
    try {{
        var app = nexacro.getApplication();
        var frames = app.popupframes;
        if (frames) {{
            var keys = Object.keys(frames);
            for (var i = 0; i < keys.length; i++) {{
                var frame = frames[keys[i]];
                if (frame && frame.form) {{
                    var formStr = JSON.stringify(frame.form);
                    if (formStr.indexOf('{text}') >= 0) return true;
                }}
            }}
        }}
        return false;
    }} catch(e) {{ return false; }}
}})()
"""

# 확인 버튼 클릭
CLICK_CONFIRM_JS = """
(function() {
    try {
        var app = nexacro.getApplication();
        var frames = app.popupframes;
        if (frames) {
            var keys = Object.keys(frames);
            for (var i = keys.length - 1; i >= 0; i--) {
                var frame = frames[keys[i]];
                if (frame && frame.form) {
                    // 확인 버튼 찾기 (다양한 이름 시도)
                    var btnNames = ['btn_ok', 'btn_confirm', 'Button00', 'btn_yes', 'btnOK', 'btnConfirm'];
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

# 닫기 버튼 클릭
CLICK_CLOSE_JS = """
(function() {
    try {
        var app = nexacro.getApplication();
        var frames = app.popupframes;
        if (frames) {
            var keys = Object.keys(frames);
            for (var i = keys.length - 1; i >= 0; i--) {
                var frame = frames[keys[i]];
                if (frame && frame.form) {
                    // 닫기 버튼 찾기
                    var btnNames = ['btn_close', 'btn_cancel', 'Button01', 'btnClose', 'btnCancel'];
                    for (var j = 0; j < btnNames.length; j++) {
                        var btn = frame.form[btnNames[j]];
                        if (btn && btn.click) {
                            btn.click();
                            return true;
                        }
                    }
                    // 버튼 못 찾으면 프레임 직접 닫기
                    frame.close();
                    return true;
                }
            }
        }
        return false;
    } catch(e) { return false; }
})()
"""

# 로딩 중 확인
CHECK_LOADING_JS = """
(function() {
    try {
        // 로딩 인디케이터 확인
        var app = nexacro.getApplication();
        if (app.gvars && app.gvars.getVariable('GV_LOADING') === 'Y') return true;

        // 대기 커서 확인
        if (document.body.style.cursor === 'wait') return true;

        return false;
    } catch(e) { return false; }
})()
"""

# 세션 만료 확인
CHECK_SESSION_EXPIRED_JS = """
(function() {
    try {
        var app = nexacro.getApplication();
        // 세션 만료 시 로그인 화면으로 돌아감
        var loginFrame = app.mainframe.HFrameSet00.LoginFrame;
        if (loginFrame && loginFrame.visible) return true;

        // 세션 만료 팝업 확인
        var frames = app.popupframes;
        if (frames) {
            var keys = Object.keys(frames);
            for (var i = 0; i < keys.length; i++) {
                var frame = frames[keys[i]];
                if (frame && frame.form) {
                    var str = JSON.stringify(frame.form);
                    if (str.indexOf('세션') >= 0 || str.indexOf('만료') >= 0 ||
                        str.indexOf('session') >= 0 || str.indexOf('timeout') >= 0) {
                        return true;
                    }
                }
            }
        }
        return false;
    } catch(e) { return false; }
})()
"""


# =============================================================================
# 방해 요소 목록 (우선순위 순)
# =============================================================================

KNOWN_OBSTACLES: List[Obstacle] = [
    # -------------------------------------------------------------------------
    # CRITICAL: 최우선 처리
    # -------------------------------------------------------------------------
    Obstacle(
        id="session_expired",
        name="세션 만료",
        description="로그인 세션이 만료되어 재로그인 필요",
        detect_js=CHECK_SESSION_EXPIRED_JS,
        action=ObstacleAction.RE_LOGIN,
        priority=ObstaclePriority.CRITICAL,
        wait_after_ms=1000,
    ),

    # -------------------------------------------------------------------------
    # HIGH: 에러/경고 팝업
    # -------------------------------------------------------------------------
    Obstacle(
        id="error_popup",
        name="에러 팝업",
        description="시스템 에러 메시지",
        detect_js=get_popup_with_text_js("오류"),
        action=ObstacleAction.EXECUTE_JS,
        action_js=CLICK_CONFIRM_JS,
        priority=ObstaclePriority.HIGH,
        wait_after_ms=500,
        log_when_found=True,
    ),

    Obstacle(
        id="warning_popup",
        name="경고 팝업",
        description="경고 메시지",
        detect_js=get_popup_with_text_js("경고"),
        action=ObstacleAction.EXECUTE_JS,
        action_js=CLICK_CONFIRM_JS,
        priority=ObstaclePriority.HIGH,
        wait_after_ms=500,
    ),

    # -------------------------------------------------------------------------
    # MEDIUM: 일반 팝업/공지
    # -------------------------------------------------------------------------
    Obstacle(
        id="notice_popup",
        name="공지 팝업",
        description="시스템 공지사항",
        detect_js=get_popup_with_text_js("공지"),
        action=ObstacleAction.EXECUTE_JS,
        action_js=CLICK_CLOSE_JS,
        priority=ObstaclePriority.MEDIUM,
        wait_after_ms=300,
    ),

    Obstacle(
        id="confirm_dialog",
        name="확인 다이얼로그",
        description="확인/취소 선택 창",
        detect_js=get_popup_with_text_js("확인"),
        action=ObstacleAction.EXECUTE_JS,
        action_js=CLICK_CONFIRM_JS,
        priority=ObstaclePriority.MEDIUM,
        wait_after_ms=300,
    ),

    Obstacle(
        id="loading_overlay",
        name="로딩 중",
        description="데이터 로딩 중",
        detect_js=CHECK_LOADING_JS,
        action=ObstacleAction.WAIT_AND_RETRY,
        priority=ObstaclePriority.MEDIUM,
        wait_after_ms=1000,
        max_retry=30,  # 최대 30초 대기
        log_when_found=False,  # 로딩은 로그 안 남김
    ),

    Obstacle(
        id="any_popup",
        name="일반 팝업",
        description="기타 모든 팝업",
        detect_js=CHECK_POPUP_EXISTS_JS,
        action=ObstacleAction.EXECUTE_JS,
        action_js=CLOSE_ALL_POPUPS_JS,
        priority=ObstaclePriority.LOW,
        wait_after_ms=300,
    ),
]


# =============================================================================
# 헬퍼 함수
# =============================================================================

def get_obstacles_by_priority() -> List[Obstacle]:
    """우선순위 순으로 정렬된 방해 요소 목록"""
    return sorted(KNOWN_OBSTACLES, key=lambda x: x.priority.value)


def get_obstacle_by_id(obstacle_id: str) -> Optional[Obstacle]:
    """ID로 방해 요소 조회"""
    for obs in KNOWN_OBSTACLES:
        if obs.id == obstacle_id:
            return obs
    return None


def add_custom_obstacle(obstacle: Obstacle) -> None:
    """커스텀 방해 요소 추가 (런타임)"""
    # 우선순위 순서 유지하면서 추가
    for i, obs in enumerate(KNOWN_OBSTACLES):
        if obstacle.priority.value < obs.priority.value:
            KNOWN_OBSTACLES.insert(i, obstacle)
            return
    KNOWN_OBSTACLES.append(obstacle)


def remove_obstacle(obstacle_id: str) -> bool:
    """방해 요소 제거"""
    for i, obs in enumerate(KNOWN_OBSTACLES):
        if obs.id == obstacle_id:
            del KNOWN_OBSTACLES[i]
            return True
    return False
