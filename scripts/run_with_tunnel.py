"""웹 서버 + Cloudflare Quick Tunnel 동시 실행 스크립트.

사용법:
    python scripts/run_with_tunnel.py

별도 CMD 창에서 실행하세요.
서버(포트 5000) + Cloudflare 터널이 동시에 시작됩니다.
URL은 재시작마다 변경되지만, 가입/카드 불필요.
Ctrl+C로 종료.
"""

import sys
import os
import subprocess
import threading
import time
import re

# 프로젝트 루트를 path에 추가
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# cloudflared 경로
CLOUDFLARED = os.path.join(
    os.path.expanduser("~"), "AppData", "Local", "Programs", "cloudflared.exe"
)

# URL 저장 파일 (바탕화면)
URL_FILE = os.path.join(PROJECT_ROOT, "PUBLIC_URL.txt")


def copy_to_clipboard(text):
    """URL을 클립보드에 복사."""
    try:
        subprocess.run(
            ["clip"],
            input=text.encode("utf-8"),
            check=True,
        )
        return True
    except Exception:
        return False


def save_url_to_file(url):
    """URL을 파일에 저장."""
    try:
        with open(URL_FILE, "w", encoding="utf-8") as f:
            f.write(f"BGF Dashboard Public URL\n")
            f.write(f"========================\n\n")
            f.write(f"URL: {url}\n\n")
            f.write(f"Updated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"* PC or script restart will change this URL\n")
            f.write(f"* Share this URL with users who need access\n")
        return True
    except Exception:
        return False


def run_server():
    """waitress로 Flask 서버 실행."""
    from src.web.app import create_app
    import waitress

    app = create_app()
    print("[SERVER] Starting on 0.0.0.0:5000 ...", flush=True)
    waitress.serve(app, host="0.0.0.0", port=5000, _quiet=True)


def run_tunnel():
    """Cloudflare Quick Tunnel 시작."""
    time.sleep(3)  # 서버가 먼저 시작되도록 대기

    if not os.path.exists(CLOUDFLARED):
        print(f"[TUNNEL] cloudflared not found: {CLOUDFLARED}", flush=True)
        print("[TUNNEL] Local only: http://localhost:5000", flush=True)
        return

    print("[TUNNEL] Starting Cloudflare Tunnel...", flush=True)

    try:
        proc = subprocess.Popen(
            [CLOUDFLARED, "tunnel", "--url", "http://localhost:5000"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        url_found = False
        for line in proc.stdout:
            line = line.strip()
            # URL 추출
            match = re.search(r"(https://[a-z0-9\-]+\.trycloudflare\.com)", line)
            if match and not url_found:
                url = match.group(1)
                url_found = True

                # 클립보드에 복사
                copied = copy_to_clipboard(url)
                # 파일에 저장
                saved = save_url_to_file(url)

                print("", flush=True)
                print("=" * 60, flush=True)
                print("", flush=True)
                print(f"  PUBLIC URL: {url}", flush=True)
                print("", flush=True)
                if copied:
                    print("  [OK] Clipboard copied!", flush=True)
                if saved:
                    print(f"  [OK] Saved: PUBLIC_URL.txt", flush=True)
                print("", flush=True)
                print("  -> Share this URL with other users", flush=True)
                print("  -> No signup needed, direct access", flush=True)
                print("  -> Press Ctrl+C to stop", flush=True)
                print("", flush=True)
                print("=" * 60, flush=True)
                print("", flush=True)

        proc.wait()
    except Exception as e:
        print(f"[TUNNEL] Error: {e}", flush=True)


if __name__ == "__main__":
    print("", flush=True)
    print("  BGF Dashboard Server + Tunnel", flush=True)
    print("  ==============================", flush=True)
    print("", flush=True)

    # 터널을 별도 스레드로 실행
    tunnel_thread = threading.Thread(target=run_tunnel, daemon=True)
    tunnel_thread.start()

    # 서버는 메인 스레드에서 실행 (Ctrl+C로 종료 가능)
    try:
        run_server()
    except KeyboardInterrupt:
        print("\n[SERVER] Shutting down...", flush=True)
        # URL 파일 정리
        try:
            if os.path.exists(URL_FILE):
                os.remove(URL_FILE)
        except Exception:
            pass
