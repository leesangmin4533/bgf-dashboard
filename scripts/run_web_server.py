"""프로덕션 웹 서버 (waitress)

Usage:
    python scripts/run_web_server.py
    python scripts/run_web_server.py --host 0.0.0.0 --port 8080
    python scripts/run_web_server.py --port 5000   # 로컬 전용

ngrok 연동:
    1) python scripts/run_web_server.py --port 8080
    2) ngrok http 8080
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main():
    parser = argparse.ArgumentParser(description="BGF 대시보드 웹 서버")
    parser.add_argument("--host", default="0.0.0.0", help="바인딩 호스트 (기본: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="포트 번호 (기본: 8080)")
    parser.add_argument("--threads", type=int, default=4, help="워커 스레드 수 (기본: 4)")
    args = parser.parse_args()

    from src.web.app import create_app

    app = create_app()

    print(f"BGF Dashboard starting on http://{args.host}:{args.port}")
    print(f"  ngrok: ngrok http {args.port}")
    print("  Ctrl+C to stop")

    try:
        from waitress import serve
        serve(app, host=args.host, port=args.port, threads=args.threads)
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
