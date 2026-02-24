"""Flask 보안 미들웨어"""
import time
import threading
from collections import defaultdict

from flask import request, jsonify, session, redirect


class RateLimiter:
    """인메모리 슬라이딩 윈도우 Rate Limiter

    외부 의존성 없이 Flask before_request에서 사용.
    localhost(127.0.0.1)는 제한 제외.
    """

    def __init__(self, default_limit: int = 60, window_seconds: int = 60):
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()
        self.default_limit = default_limit
        self.window_seconds = window_seconds
        # 무거운 엔드포인트별 제한
        self.endpoint_limits = {
            '/api/order/run-script': 5,
            '/api/order/predict': 10,
            '/api/report/baseline': 5,
        }

    def check(self):
        """Rate limit 체크. 초과 시 429 응답 반환, 정상이면 None."""
        ip = request.remote_addr
        if ip == '127.0.0.1':
            return None

        limit = self.endpoint_limits.get(request.path, self.default_limit)
        now = time.time()
        cutoff = now - self.window_seconds

        with self._lock:
            # 만료된 요청 제거
            self._requests[ip] = [t for t in self._requests[ip] if t > cutoff]
            if len(self._requests[ip]) >= limit:
                return jsonify({"error": "요청 빈도 제한 초과", "code": "RATE_LIMITED"}), 429
            self._requests[ip].append(now)

        return None


# ── 인증 + 매장 접근 권한 체크 ──────────────────────────────

# 인증 불필요 경로
_PUBLIC_PREFIXES = ("/api/auth/login", "/api/auth/logout", "/api/auth/signup", "/login", "/static/")


def check_auth_and_store_access():
    """인증 + 매장 데이터 접근 권한 체크.

    Returns:
        None: 통과
        Response: 인증 실패 또는 권한 부족
    """
    path = request.path

    # 공개 경로 제외
    for prefix in _PUBLIC_PREFIXES:
        if path.startswith(prefix):
            return None

    # 로그인 확인
    if "user_id" not in session:
        # API 요청이면 401 JSON, 페이지 요청이면 리다이렉트
        if path.startswith("/api/"):
            return jsonify({"error": "로그인이 필요합니다", "code": "UNAUTHORIZED"}), 401
        return redirect("/login")

    # admin은 모든 매장 접근 가능
    if session.get("role") == "admin":
        return None

    # viewer: 자기 매장 데이터만 접근 가능
    requested_store = request.args.get("store_id")
    if not requested_store:
        # POST body에서 store_id 확인
        if request.is_json and request.content_length and request.content_length < 10000:
            body = request.get_json(silent=True)
            if body and isinstance(body, dict):
                requested_store = body.get("store_id")

    user_store = session.get("store_id")
    if requested_store and requested_store != user_store:
        return jsonify({"error": "해당 매장 데이터에 접근 권한이 없습니다", "code": "FORBIDDEN"}), 403

    return None
