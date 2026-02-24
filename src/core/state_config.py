"""
BGF 시스템 화면 상태 정의

각 화면의 특징(시그니처)과 진입 방법을 정의합니다.
넥사크로 기반 시스템이므로 JavaScript 실행으로 상태 확인합니다.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class ScreenSignature:
    """화면 식별 시그니처"""
    name: str                          # 시그니처 이름
    check_type: str                    # 확인 방식: "js_var", "js_func", "element", "url"
    check_value: str                   # 확인할 값 (변수명, 함수, 선택자, URL 패턴)
    expected: Any = True               # 기대값 (True, False, 특정 문자열 등)
    description: str = ""              # 설명


@dataclass
class ScreenState:
    """화면 상태 정의"""
    id: str                            # 상태 ID (예: "home", "single_order")
    name: str                          # 한글 이름 (예: "홈화면", "단품별발주")

    # 식별 방법 (여러 시그니처 중 하나라도 매칭되면 해당 화면)
    signatures: List[ScreenSignature] = field(default_factory=list)

    # 이 화면으로 가는 JS 코드
    navigation_js: Optional[str] = None

    # 이 화면에서 홈으로 가는 JS 코드
    back_to_home_js: Optional[str] = None

    # 메뉴 경로 (예: ["발주관리", "단품별 발주"])
    menu_path: Optional[List[str]] = None


# =============================================================================
# BGF 넥사크로 화면 정의
# =============================================================================

SCREEN_STATES: Dict[str, ScreenState] = {
    # -------------------------------------------------------------------------
    # 로그인 상태 확인
    # -------------------------------------------------------------------------
    "logged_in": ScreenState(
        id="logged_in",
        name="로그인됨",
        signatures=[
            ScreenSignature(
                name="GV_CHANNELTYPE_HOME",
                check_type="js_var",
                check_value="nexacro.getApplication().gvars.getVariable('GV_CHANNELTYPE')",
                expected="HOME",
                description="로그인 후 GV_CHANNELTYPE이 'HOME'으로 설정됨"
            ),
        ],
    ),

    "login_page": ScreenState(
        id="login_page",
        name="로그인화면",
        signatures=[
            ScreenSignature(
                name="로그인_폼_존재",
                check_type="js_func",
                check_value="""
                    (function() {
                        try {
                            var loginFrame = nexacro.getApplication().mainframe.HFrameSet00.LoginFrame;
                            return loginFrame && loginFrame.form ? true : false;
                        } catch(e) { return false; }
                    })()
                """,
                expected=True,
                description="로그인 프레임 존재 여부"
            ),
        ],
    ),

    # -------------------------------------------------------------------------
    # 홈화면 (메인)
    # -------------------------------------------------------------------------
    "home": ScreenState(
        id="home",
        name="홈화면",
        signatures=[
            ScreenSignature(
                name="탭_없음",
                check_type="js_func",
                check_value="""
                    (function() {
                        try {
                            var tabs = nexacro.getApplication().mainframe.HFrameSet00.LeftFrame.form.tab_openList;
                            return tabs.tabcount === 0;
                        } catch(e) { return false; }
                    })()
                """,
                expected=True,
                description="열린 탭이 없으면 홈화면"
            ),
        ],
        navigation_js=None,  # 홈은 다른 곳에서 이동
        back_to_home_js=None  # 이미 홈
    ),

    # -------------------------------------------------------------------------
    # 발주 관련
    # -------------------------------------------------------------------------
    "single_order": ScreenState(
        id="single_order",
        name="단품별발주",
        signatures=[
            ScreenSignature(
                name="단품별발주_화면코드",
                check_type="js_func",
                check_value="""
                    (function() {
                        try {
                            var tabs = nexacro.getApplication().mainframe.HFrameSet00.LeftFrame.form.tab_openList;
                            for (var i = 0; i < tabs.tabcount; i++) {
                                var btn = tabs.tabbuttons[i];
                                if (btn && btn.text && btn.text.indexOf('단품별') >= 0) return true;
                            }
                            return false;
                        } catch(e) { return false; }
                    })()
                """,
                expected=True,
                description="단품별발주 탭이 열려있음"
            ),
        ],
        menu_path=["발주관리", "단품별 발주"],
        navigation_js="""
            (function() {
                // 발주관리 > 단품별 발주 메뉴 클릭
                var app = nexacro.getApplication();
                var menuTree = app.mainframe.HFrameSet00.LeftFrame.form.Tree00;
                // 메뉴 트리에서 '단품별 발주' 찾아서 클릭
                // 실제 구현 필요
                return true;
            })()
        """,
        back_to_home_js="""
            (function() {
                try {
                    // 현재 탭 닫기
                    var tabs = nexacro.getApplication().mainframe.HFrameSet00.LeftFrame.form.tab_openList;
                    if (tabs.tabcount > 0) {
                        var closeBtn = tabs.tabbuttons[0].extrabutton;
                        if (closeBtn) {
                            closeBtn.click();
                            return true;
                        }
                    }
                    return false;
                } catch(e) { return false; }
            })()
        """
    ),

    "order_status": ScreenState(
        id="order_status",
        name="발주현황",
        signatures=[
            ScreenSignature(
                name="발주현황_화면",
                check_type="js_func",
                check_value="""
                    (function() {
                        try {
                            var tabs = nexacro.getApplication().mainframe.HFrameSet00.LeftFrame.form.tab_openList;
                            for (var i = 0; i < tabs.tabcount; i++) {
                                var btn = tabs.tabbuttons[i];
                                if (btn && btn.text && btn.text.indexOf('발주현황') >= 0) return true;
                            }
                            return false;
                        } catch(e) { return false; }
                    })()
                """,
                expected=True
            ),
        ],
        menu_path=["발주관리", "발주현황"],
    ),

    # -------------------------------------------------------------------------
    # 매출 관련
    # -------------------------------------------------------------------------
    "sales_analysis": ScreenState(
        id="sales_analysis",
        name="매출분석",
        signatures=[
            ScreenSignature(
                name="매출분석_화면",
                check_type="js_func",
                check_value="""
                    (function() {
                        try {
                            var tabs = nexacro.getApplication().mainframe.HFrameSet00.LeftFrame.form.tab_openList;
                            for (var i = 0; i < tabs.tabcount; i++) {
                                var btn = tabs.tabbuttons[i];
                                if (btn && btn.text && (btn.text.indexOf('매출') >= 0 || btn.text.indexOf('판매') >= 0)) return true;
                            }
                            return false;
                        } catch(e) { return false; }
                    })()
                """,
                expected=True
            ),
        ],
        menu_path=["매출분석", "중분류별 상품"],
    ),

    # -------------------------------------------------------------------------
    # 입고 관련
    # -------------------------------------------------------------------------
    "receiving": ScreenState(
        id="receiving",
        name="입고조회",
        signatures=[
            ScreenSignature(
                name="입고_화면",
                check_type="js_func",
                check_value="""
                    (function() {
                        try {
                            var tabs = nexacro.getApplication().mainframe.HFrameSet00.LeftFrame.form.tab_openList;
                            for (var i = 0; i < tabs.tabcount; i++) {
                                var btn = tabs.tabbuttons[i];
                                if (btn && btn.text && btn.text.indexOf('입고') >= 0) return true;
                            }
                            return false;
                        } catch(e) { return false; }
                    })()
                """,
                expected=True
            ),
        ],
        menu_path=["검수전표", "센터매입 조회/확정"],
    ),
}


# =============================================================================
# 헬퍼 함수
# =============================================================================

def get_screen_state(state_id: str) -> Optional[ScreenState]:
    """상태 ID로 ScreenState 조회"""
    return SCREEN_STATES.get(state_id)


def get_all_states() -> Dict[str, ScreenState]:
    """모든 상태 반환"""
    return SCREEN_STATES


def add_screen_state(state: ScreenState) -> None:
    """런타임에 화면 상태 추가"""
    SCREEN_STATES[state.id] = state
