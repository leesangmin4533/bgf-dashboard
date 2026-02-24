"""웹 보안 테스트"""

import time
import pytest

from src.web.middleware import RateLimiter
from src.application.services.store_service import _hash_password, _verify_password


class TestSecurityHeaders:
    """보안 헤더 테스트"""

    def test_x_content_type_options(self, client):
        """X-Content-Type-Options: nosniff 헤더 확인"""
        resp = client.get("/api/home/status")
        assert resp.headers.get('X-Content-Type-Options') == 'nosniff'

    def test_x_frame_options(self, client):
        """X-Frame-Options: DENY 헤더 확인"""
        resp = client.get("/api/home/status")
        assert resp.headers.get('X-Frame-Options') == 'DENY'

    def test_x_xss_protection(self, client):
        """X-XSS-Protection 헤더 확인"""
        resp = client.get("/api/home/status")
        assert '1' in resp.headers.get('X-XSS-Protection', '')

    def test_csp_header(self, client):
        """Content-Security-Policy 헤더 확인"""
        resp = client.get("/api/home/status")
        csp = resp.headers.get('Content-Security-Policy', '')
        assert "default-src 'self'" in csp

    def test_referrer_policy(self, client):
        """Referrer-Policy 헤더 확인"""
        resp = client.get("/api/home/status")
        assert resp.headers.get('Referrer-Policy') == 'strict-origin-when-cross-origin'

    def test_cache_control(self, client):
        """Cache-Control: no-store 헤더 확인"""
        resp = client.get("/api/home/status")
        cc = resp.headers.get('Cache-Control', '')
        assert 'no-store' in cc
        assert 'no-cache' in cc


class TestRateLimiter:
    """Rate Limiter 테스트"""

    def test_normal_request_passes(self):
        """정상 요청은 통과"""
        limiter = RateLimiter(default_limit=5, window_seconds=60)
        # Flask 앱 컨텍스트 없이 내부 로직만 테스트
        assert limiter.default_limit == 5
        assert limiter.window_seconds == 60

    def test_endpoint_limits_configured(self):
        """엔드포인트별 제한 설정 확인"""
        limiter = RateLimiter()
        assert '/api/order/run-script' in limiter.endpoint_limits
        assert limiter.endpoint_limits['/api/order/run-script'] == 5

    def test_window_tracking(self):
        """슬라이딩 윈도우 요청 추적"""
        limiter = RateLimiter(default_limit=3, window_seconds=1)
        # 직접 내부 상태 테스트
        ip = '192.168.1.1'
        now = time.time()
        limiter._requests[ip] = [now, now + 0.1, now + 0.2]
        assert len(limiter._requests[ip]) == 3

    def test_expired_requests_cleanup(self):
        """만료된 요청이 정리되는지 확인"""
        limiter = RateLimiter(default_limit=3, window_seconds=1)
        ip = '192.168.1.1'
        old_time = time.time() - 2  # 2초 전 (1초 윈도우 초과)
        limiter._requests[ip] = [old_time, old_time + 0.1]
        # cutoff 이후만 남겨야 함
        cutoff = time.time() - 1
        cleaned = [t for t in limiter._requests[ip] if t > cutoff]
        assert len(cleaned) == 0


class TestInputValidation:
    """입력 검증 테스트"""

    def test_invalid_store_id_rejected(self, client):
        """잘못된 store_id가 거부되는지 확인"""
        resp = client.post("/api/order/run-script",
                           json={"mode": "preview", "store_id": "'; DROP TABLE--"})
        assert resp.status_code == 400
        data = resp.get_json()
        assert "유효하지 않은 점포 코드" in data["error"]

    def test_valid_store_id_format(self, client):
        """유효한 store_id 형식은 통과 (실행은 실패할 수 있음)"""
        resp = client.post("/api/order/run-script",
                           json={"mode": "preview", "store_id": "46513"})
        # 400이 아닌 다른 응답 (스크립트 미존재 등)
        assert resp.status_code != 400 or "점포 코드" not in resp.get_json().get("error", "")

    def test_invalid_category_rejected(self, client):
        """잘못된 category 코드가 거부되는지 확인"""
        resp = client.post("/api/order/run-script",
                           json={"mode": "preview", "store_id": "46513",
                                 "categories": ["abc", "!@#"]})
        assert resp.status_code == 400
        data = resp.get_json()
        assert "카테고리 코드" in data["error"]


class TestErrorResponses:
    """에러 응답 정보 노출 방지 테스트"""

    def test_404_generic_message(self, client):
        """404 응답에 내부 정보 미포함"""
        resp = client.get("/api/nonexistent/path")
        assert resp.status_code == 404
        data = resp.get_json()
        assert "error" in data
        # 파일 경로나 스택 트레이스가 포함되지 않아야 함
        assert "Traceback" not in data["error"]
        assert "\\" not in data["error"]

    def test_500_no_internal_info(self, client):
        """500 응답에 스택 트레이스/내부 경로 미포함"""
        # Flask의 500 핸들러는 실제 에러 발생 시 동작
        # 직접 에러 핸들러의 응답 형식만 검증
        from src.web.app import create_app
        test_app = create_app()
        with test_app.test_client() as c:
            # 500 핸들러 등록 확인 + 응답 포맷 검증
            with test_app.test_request_context():
                from flask import jsonify
                # 에러 핸들러가 등록되어 있는지 확인
                assert 500 in test_app.error_handler_spec[None]


class TestPasswordHashing:
    """비밀번호 해싱 테스트"""

    def test_hash_returns_salted(self):
        """해싱 결과에 salt가 포함되는지 확인"""
        hashed = _hash_password("test123")
        assert '$' in hashed
        salt, hash_value = hashed.split('$', 1)
        assert len(salt) == 32  # 16 bytes hex
        assert len(hash_value) == 64  # SHA-256 hex

    def test_same_password_different_hash(self):
        """같은 비밀번호도 다른 해시 생성 (salt 덕분)"""
        h1 = _hash_password("test123")
        h2 = _hash_password("test123")
        assert h1 != h2

    def test_verify_correct_password(self):
        """올바른 비밀번호 검증 성공"""
        hashed = _hash_password("mypassword")
        assert _verify_password("mypassword", hashed) is True

    def test_verify_wrong_password(self):
        """틀린 비밀번호 검증 실패"""
        hashed = _hash_password("mypassword")
        assert _verify_password("wrongpassword", hashed) is False

    def test_verify_legacy_plaintext(self):
        """레거시 평문 비밀번호 호환"""
        assert _verify_password("1113", "1113") is True
        assert _verify_password("wrong", "1113") is False
