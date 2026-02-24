"""웹 서버 + ngrok 터널 동시 실행 스크립트.

사용법:
    python scripts/run_with_ngrok.py

별도 CMD 창에서 실행하세요.
서버(포트 5000) + ngrok 터널이 동시에 시작됩니다.
Ctrl+C로 종료.
"""

import sys
import os
import threading
import time

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_server():
    """waitress로 Flask 서버 실행."""
    from src.web.app import create_app
    import waitress

    app = create_app()
    print("[SERVER] Starting on 0.0.0.0:5000 ...", flush=True)
    waitress.serve(app, host="0.0.0.0", port=5000, _quiet=True)


def run_ngrok():
    """ngrok 터널 시작."""
    time.sleep(3)  # 서버가 먼저 시작되도록 대기
    try:
        from pyngrok import ngrok

        tunnel = ngrok.connect(5000, "http")
        print("", flush=True)
        print("=" * 50, flush=True)
        print(f"  PUBLIC URL: {tunnel.public_url}", flush=True)
        print("=" * 50, flush=True)
        print("", flush=True)
        print("  -> 이 URL을 다른 사용자에게 공유하세요.", flush=True)
        print("  -> 처음 접속 시 'Visit Site' 클릭", flush=True)
        print("  -> Ctrl+C 로 종료", flush=True)
        print("", flush=True)
    except Exception as e:
        print(f"[NGROK] Error: {e}", flush=True)
        print("[NGROK] ngrok 없이 로컬(http://localhost:5000)만 사용 가능", flush=True)


if __name__ == "__main__":
    # ngrok을 별도 스레드로 실행
    ngrok_thread = threading.Thread(target=run_ngrok, daemon=True)
    ngrok_thread.start()

    # 서버는 메인 스레드에서 실행 (Ctrl+C로 종료 가능)
    try:
        run_server()
    except KeyboardInterrupt:
        print("\n[SERVER] Shutting down...", flush=True)
        try:
            from pyngrok import ngrok
            ngrok.kill()
        except Exception:
            pass
