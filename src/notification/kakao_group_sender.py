"""
카카오톡 PC 단톡방 메시지 전송 모듈
- Win32API로 카카오톡 PC 앱의 채팅방에 메시지 전송
- KakaoNotifier와 함께 사용하여 나에게 보내기 + 단톡방 동시 발송
"""

import time
import ctypes
from ctypes import wintypes
from typing import Optional, List, Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Win32API lazy import (Windows 전용)
try:
    import win32gui
    import win32con
    import win32api
    import win32clipboard
    import win32process
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False
    logger.warning("pywin32 미설치. 단톡방 전송 비활성화.")


def _get_kakao_pids() -> set:
    """카카오톡 프로세스 PID 목록 조회"""
    import subprocess
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq KakaoTalk.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    pids = set()
    for line in result.stdout.strip().split("\n"):
        if "KakaoTalk" in line:
            parts = line.split(",")
            pids.add(int(parts[1].strip('"')))
    return pids


def _set_foreground_safe(hwnd: int) -> None:
    """SetForegroundWindow 권한 우회"""
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    cur = kernel32.GetCurrentThreadId()
    fg_hwnd = user32.GetForegroundWindow()
    fg_t = user32.GetWindowThreadProcessId(fg_hwnd, ctypes.byref(wintypes.DWORD()))
    attached = False
    if cur != fg_t:
        user32.AttachThreadInput(cur, fg_t, True)
        attached = True
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        win32api.keybd_event(0x12, 0, 0, 0)  # Alt 트릭
        win32api.keybd_event(0x12, 0, win32con.KEYEVENTF_KEYUP, 0)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception as e:
            logger.warning(f"SetForegroundWindow 실패: {e}")
    if attached:
        user32.AttachThreadInput(cur, fg_t, False)


def _copy_to_clipboard(text: str) -> None:
    """유니코드 텍스트를 클립보드에 복사"""
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
    win32clipboard.CloseClipboard()


class KakaoGroupSender:
    """카카오톡 PC 단톡방 메시지 전송기

    Args:
        room_names: 전송할 채팅방 이름 목록 (윈도우 타이틀에 포함되면 매칭)
    """

    def __init__(self, room_names: Optional[List[str]] = None) -> None:
        self.room_names = room_names or []
        self.enabled = WIN32_AVAILABLE and len(self.room_names) > 0

    def _find_chat_windows(self) -> List[Tuple[int, str]]:
        """열려있는 카카오톡 채팅방 창 찾기

        Returns:
            [(hwnd, title), ...] 매칭된 채팅방 목록
        """
        if not WIN32_AVAILABLE:
            return []

        kakao_pids = _get_kakao_pids()
        if not kakao_pids:
            logger.warning("카카오톡 프로세스를 찾을 수 없음")
            return []

        matched = []

        def callback(hwnd, _):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid not in kakao_pids:
                return
            cls = win32gui.GetClassName(hwnd)
            if cls != "EVA_Window_Dblclk":
                return
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return
            for room_name in self.room_names:
                if room_name in title:
                    matched.append((hwnd, title))
                    break

        win32gui.EnumWindows(callback, None)
        return matched

    def _find_edit_control(self, chat_hwnd: int) -> Optional[int]:
        """채팅방 내 RICHEDIT50W 입력창 찾기"""
        edit_hwnd = None

        def callback(hwnd, _):
            nonlocal edit_hwnd
            cls = win32gui.GetClassName(hwnd)
            if cls == "RICHEDIT50W":
                edit_hwnd = hwnd

        win32gui.EnumChildWindows(chat_hwnd, callback, None)
        return edit_hwnd

    def _send_to_window(self, chat_hwnd: int, edit_hwnd: int, text: str) -> bool:
        """특정 채팅방 창에 메시지 전송"""
        try:
            # 1. 창 복원 + 포그라운드
            _set_foreground_safe(chat_hwnd)
            time.sleep(0.3)

            # 2. 입력창 클릭 (포커스)
            rect = win32gui.GetWindowRect(edit_hwnd)
            cx = (rect[0] + rect[2]) // 2
            cy = (rect[1] + rect[3]) // 2
            win32api.SetCursorPos((cx, cy))
            time.sleep(0.05)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, cx, cy, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, cx, cy, 0, 0)
            time.sleep(0.2)

            # 3. 클립보드에 메시지 복사 + Ctrl+V
            _copy_to_clipboard(text)
            time.sleep(0.1)
            win32api.keybd_event(0x11, 0, 0, 0)  # Ctrl
            win32api.keybd_event(0x56, 0, 0, 0)  # V
            win32api.keybd_event(0x56, 0, win32con.KEYEVENTF_KEYUP, 0)
            win32api.keybd_event(0x11, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.2)

            # 4. Enter 전송
            win32api.keybd_event(0x0D, 0, 0, 0)
            win32api.keybd_event(0x0D, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.2)

            return True
        except Exception as e:
            logger.error(f"단톡방 메시지 전송 실패: {e}")
            return False

    def send(self, text: str) -> dict:
        """모든 등록된 단톡방에 메시지 전송

        Args:
            text: 전송할 메시지

        Returns:
            {"sent": [...성공 채팅방], "failed": [...실패 채팅방], "not_found": [...못찾은 채팅방]}
        """
        if not self.enabled:
            return {"sent": [], "failed": [], "not_found": self.room_names[:]}

        result = {"sent": [], "failed": [], "not_found": []}

        # 열려있는 채팅방 찾기
        windows = self._find_chat_windows()
        found_names = {title for _, title in windows}

        # 못 찾은 채팅방
        for room_name in self.room_names:
            if not any(room_name in title for title in found_names):
                result["not_found"].append(room_name)
                logger.warning(f"채팅방 '{room_name}' 창이 열려있지 않음 (최소화 OK, 닫힘 X)")

        # 전송
        for chat_hwnd, title in windows:
            edit_hwnd = self._find_edit_control(chat_hwnd)
            if not edit_hwnd:
                logger.error(f"'{title}' 입력창 없음")
                result["failed"].append(title)
                continue

            if self._send_to_window(chat_hwnd, edit_hwnd, text):
                logger.info(f"단톡방 전송 성공: {title}")
                result["sent"].append(title)
            else:
                result["failed"].append(title)

        return result
