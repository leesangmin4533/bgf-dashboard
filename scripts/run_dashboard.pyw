"""Flask 대시보드 서버 시작 + 브라우저 자동 열기"""
import sys
import os
import webbrowser
import threading
import socket
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))
os.chdir(str(PROJECT_ROOT))

from src.web.app import create_app


DEFAULT_PORT = 8050


def is_dashboard_running(port=DEFAULT_PORT):
    """대시보드가 이미 실행 중인지 확인"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0


def open_browser(port):
    """브라우저 자동 열기"""
    webbrowser.open(f'http://127.0.0.1:{port}')


if __name__ == '__main__':
    # 이미 실행 중이면 브라우저만 열고 종료
    if is_dashboard_running():
        open_browser(DEFAULT_PORT)
        sys.exit(0)

    app = create_app()
    threading.Timer(1.5, open_browser, args=[DEFAULT_PORT]).start()
    app.run(host='127.0.0.1', port=DEFAULT_PORT, debug=False, use_reloader=False)
