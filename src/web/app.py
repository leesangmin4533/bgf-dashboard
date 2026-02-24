"""Flask 앱 생성"""
import os
import secrets
import sys
from datetime import timedelta
from pathlib import Path

from flask import Flask, jsonify, request

from src.utils.logger import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = str(PROJECT_ROOT / "data" / "bgf_sales.db")

# PROJECT_ROOT가 sys.path에 있어야 from src.xxx import 가능
_root_path = str(PROJECT_ROOT)
if _root_path not in sys.path:
    sys.path.insert(0, _root_path)


def create_app() -> Flask:
    """Flask 앱 팩토리"""
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.config["DB_PATH"] = DB_PATH
    app.config["PROJECT_ROOT"] = str(PROJECT_ROOT)
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    # 마지막 예측 결과 캐시 (매장별: {store_id: predictions_list})
    app.config["LAST_PREDICTIONS"] = {}

    try:
        from .routes import register_blueprints
    except ImportError:
        from routes import register_blueprints

    register_blueprints(app)

    # Rate Limiter + 인증 미들웨어
    from src.web.middleware import RateLimiter, check_auth_and_store_access
    rate_limiter = RateLimiter(default_limit=60, window_seconds=60)

    @app.before_request
    def check_rate_limit():
        """Rate Limiting 체크"""
        result = rate_limiter.check()
        if result:
            return result

    @app.before_request
    def check_authentication():
        """인증 + 매장 접근 권한 체크"""
        result = check_auth_and_store_access()
        if result:
            return result

    @app.before_request
    def log_request():
        """접근 로깅"""
        if not request.path.startswith('/static'):
            logger.info(f"[API] {request.method} {request.path} from {request.remote_addr}")

    @app.after_request
    def add_security_headers(response):
        """보안 헤더 추가"""
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'"
        )
        return response

    # 전역 에러 핸들러 (일관된 JSON 응답)
    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "요청한 리소스를 찾을 수 없습니다", "code": "NOT_FOUND"}), 404

    @app.errorhandler(500)
    def internal_error(e):
        return jsonify({"error": "서버 내부 오류가 발생했습니다", "code": "INTERNAL_ERROR"}), 500

    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({"error": "잘못된 요청입니다", "code": "BAD_REQUEST"}), 400

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"error": "허용되지 않는 HTTP 메서드입니다", "code": "METHOD_NOT_ALLOWED"}), 405

    return app


if __name__ == "__main__":
    app = create_app()
    print("BGF Dashboard starting on http://localhost:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
