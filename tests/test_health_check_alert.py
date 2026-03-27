"""헬스 체크 + 에러 알림 + 커스텀 예외 + SHA256 검증 테스트.

Design Reference: docs/02-design/features/health-check-alert.design.md
테스트 항목:
1. AppException 계층 + context (5)
2. /api/health 응답 형식 (3)
3. /api/health/detail 상세 (2)
4. AlertingHandler 중복 억제 (4)
5. AlertingHandler 시간당 제한 (2)
6. SHA256 해시 계산 (2)
7. sync_all SHA256 포함 확인 (2)
"""

import hashlib
import logging
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# =====================================================
# 1. 커스텀 예외 계층 (5 tests)
# =====================================================

class TestCustomExceptions:
    """src/core/exceptions.py 예외 계층 검증."""

    def test_app_exception_basic(self):
        """AppException 기본 생성."""
        from src.core.exceptions import AppException

        exc = AppException("기본 에러")
        assert str(exc) == "기본 에러"
        assert exc.context == {}

    def test_app_exception_with_context(self):
        """AppException context 파라미터 포매팅."""
        from src.core.exceptions import AppException

        exc = AppException("DB 실패", store_id="46513", table="products")
        assert "store_id=46513" in str(exc)
        assert "table=products" in str(exc)
        assert exc.context["store_id"] == "46513"

    def test_exception_hierarchy(self):
        """모든 커스텀 예외가 AppException을 상속."""
        from src.core.exceptions import (
            AppException, DBException, ScrapingException,
            ValidationException, PredictionException,
            OrderException, ConfigException, AlertException,
        )

        for exc_cls in [
            DBException, ScrapingException, ValidationException,
            PredictionException, OrderException, ConfigException,
            AlertException,
        ]:
            exc = exc_cls("test error")
            assert isinstance(exc, AppException)
            assert isinstance(exc, Exception)

    def test_db_exception_context(self):
        """DBException context가 올바르게 전달."""
        from src.core.exceptions import DBException

        exc = DBException("쿼리 실패", store_id="46513", query="SELECT *")
        assert exc.context["store_id"] == "46513"
        assert exc.context["query"] == "SELECT *"
        assert "쿼리 실패" in str(exc)

    def test_exception_can_be_caught_by_parent(self):
        """자식 예외를 부모 타입으로 catch 가능."""
        from src.core.exceptions import AppException, OrderException

        with pytest.raises(AppException):
            raise OrderException("발주 실패", item_cd="A001")


# =====================================================
# 2. /api/health 응답 형식 (3 tests)
# =====================================================

class TestHealthEndpoint:
    """/api/health 기본 헬스 체크 테스트."""

    @pytest.fixture
    def health_app(self):
        """테스트용 Flask 앱 (health_bp만 등록)."""
        from flask import Flask
        from src.web.routes.api_health import health_bp

        app = Flask(__name__)
        app.register_blueprint(health_bp, url_prefix="/api/health")
        app.config["TESTING"] = True
        return app

    def test_health_returns_200(self, health_app):
        """/api/health가 200 OK 반환."""
        with health_app.test_client() as client:
            with patch("src.web.routes.api_health._check_database", return_value={"status": "ok"}), \
                 patch("src.web.routes.api_health._check_scheduler", return_value={"status": "running"}), \
                 patch("src.web.routes.api_health._check_recent_errors", return_value={"last_24h": 0}):
                resp = client.get("/api/health")

        assert resp.status_code == 200

    def test_health_response_fields(self, health_app):
        """필수 응답 필드: status, timestamp, version, uptime_seconds."""
        with health_app.test_client() as client:
            with patch("src.web.routes.api_health._check_database", return_value={"status": "ok"}), \
                 patch("src.web.routes.api_health._check_scheduler", return_value={"status": "running"}), \
                 patch("src.web.routes.api_health._check_recent_errors", return_value={"last_24h": 0}):
                resp = client.get("/api/health")

        data = resp.get_json()
        assert "status" in data
        assert "timestamp" in data
        assert "version" in data
        assert "uptime_seconds" in data

    def test_health_status_values(self, health_app):
        """status가 healthy/degraded/unhealthy 중 하나."""
        with health_app.test_client() as client:
            with patch("src.web.routes.api_health._check_database", return_value={"status": "ok"}), \
                 patch("src.web.routes.api_health._check_scheduler", return_value={"status": "running"}), \
                 patch("src.web.routes.api_health._check_recent_errors", return_value={"last_24h": 0}):
                resp = client.get("/api/health")

        data = resp.get_json()
        assert data["status"] in ("healthy", "degraded", "unhealthy")


# =====================================================
# 3. /api/health/detail 상세 (2 tests)
# =====================================================

class TestHealthDetailEndpoint:
    """/api/health/detail 상세 헬스 체크 테스트."""

    @pytest.fixture
    def health_app(self):
        from flask import Flask
        from src.web.routes.api_health import health_bp

        app = Flask(__name__)
        app.register_blueprint(health_bp, url_prefix="/api/health")
        app.config["TESTING"] = True
        return app

    def test_detail_has_checks_section(self, health_app):
        """detail 응답에 checks 섹션 포함."""
        with health_app.test_client() as client:
            with patch("src.web.routes.api_health._check_database", return_value={"status": "ok"}), \
                 patch("src.web.routes.api_health._check_scheduler", return_value={"status": "running"}), \
                 patch("src.web.routes.api_health._check_disk", return_value={}), \
                 patch("src.web.routes.api_health._check_recent_errors", return_value={"last_24h": 0}), \
                 patch("src.web.routes.api_health._check_cloud_sync", return_value={"status": "ok"}):
                resp = client.get("/api/health/detail")

        data = resp.get_json()
        assert "checks" in data
        checks = data["checks"]
        assert "database" in checks
        assert "scheduler" in checks
        assert "disk" in checks
        assert "recent_errors" in checks
        assert "cloud_sync" in checks

    def test_detail_unhealthy_when_db_error(self, health_app):
        """DB 오류 시 unhealthy 반환."""
        with health_app.test_client() as client:
            with patch("src.web.routes.api_health._check_database", return_value={"status": "error", "error": "Connection refused"}), \
                 patch("src.web.routes.api_health._check_scheduler", return_value={"status": "running"}), \
                 patch("src.web.routes.api_health._check_disk", return_value={}), \
                 patch("src.web.routes.api_health._check_recent_errors", return_value={"last_24h": 0}), \
                 patch("src.web.routes.api_health._check_cloud_sync", return_value={"status": "ok"}):
                resp = client.get("/api/health/detail")

        data = resp.get_json()
        assert data["status"] == "unhealthy"


# =====================================================
# 4. AlertingHandler 중복 억제 (4 tests)
# =====================================================

class TestAlertingDuplication:
    """AlertingHandler 중복 억제 로직."""

    def test_first_alert_passes(self, tmp_path):
        """첫 번째 알림은 정상 발송."""
        from src.utils.alerting import AlertingHandler

        handler = AlertingHandler(alert_log_path=tmp_path / "alerts.log")
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord(
            "test", logging.ERROR, "", 0, "DB 연결 실패", (), None
        )
        handler.emit(record)

        content = (tmp_path / "alerts.log").read_text(encoding="utf-8")
        assert "DB 연결 실패" in content
        assert handler._hourly_count == 1

    def test_duplicate_within_cooldown_suppressed(self, tmp_path):
        """COOLDOWN 내 동일 메시지는 억제."""
        from src.utils.alerting import AlertingHandler

        handler = AlertingHandler(alert_log_path=tmp_path / "alerts.log")
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord(
            "test", logging.ERROR, "", 0, "같은 에러", (), None
        )

        handler.emit(record)  # 1st: 통과
        handler.emit(record)  # 2nd: 억제 (동일 메시지)

        assert handler._hourly_count == 1  # 1개만 카운트됨

    def test_different_messages_not_suppressed(self, tmp_path):
        """서로 다른 메시지는 각각 발송."""
        from src.utils.alerting import AlertingHandler

        handler = AlertingHandler(alert_log_path=tmp_path / "alerts.log")
        handler.setFormatter(logging.Formatter("%(message)s"))

        record1 = logging.LogRecord(
            "test", logging.ERROR, "", 0, "에러 A", (), None
        )
        record2 = logging.LogRecord(
            "test", logging.ERROR, "", 0, "에러 B", (), None
        )

        handler.emit(record1)
        handler.emit(record2)

        assert handler._hourly_count == 2
        content = (tmp_path / "alerts.log").read_text(encoding="utf-8")
        assert "에러 A" in content
        assert "에러 B" in content

    def test_alert_after_cooldown_passes(self, tmp_path):
        """COOLDOWN 경과 후 동일 메시지 재발송 가능."""
        from src.utils.alerting import AlertingHandler

        handler = AlertingHandler(alert_log_path=tmp_path / "alerts.log")
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord(
            "test", logging.ERROR, "", 0, "쿨다운 테스트", (), None
        )

        handler.emit(record)  # 1st
        assert handler._hourly_count == 1

        # 쿨다운 시간을 과거로 조작
        msg_key = hash("쿨다운 테스트"[:100])
        handler._recent_alerts[msg_key] = time.time() - 600  # 10분 전

        handler.emit(record)  # 2nd: 쿨다운 경과, 통과
        assert handler._hourly_count == 2


# =====================================================
# 5. AlertingHandler 시간당 제한 (2 tests)
# =====================================================

class TestAlertingRateLimit:
    """AlertingHandler 시간당 발송 제한."""

    def test_hourly_limit_blocks_excess(self, tmp_path):
        """MAX_ALERTS_PER_HOUR 초과 시 알림 차단."""
        from src.utils.alerting import AlertingHandler

        handler = AlertingHandler(alert_log_path=tmp_path / "alerts.log")
        handler.setFormatter(logging.Formatter("%(message)s"))

        # hourly_count를 최대치로 설정
        handler._hourly_count = AlertingHandler.MAX_ALERTS_PER_HOUR

        record = logging.LogRecord(
            "test", logging.ERROR, "", 0, "제한 초과 에러", (), None
        )
        handler.emit(record)

        # 카운트 불변 (차단됨)
        assert handler._hourly_count == AlertingHandler.MAX_ALERTS_PER_HOUR

    def test_hourly_counter_resets_after_1h(self, tmp_path):
        """1시간 경과 후 카운터 리셋."""
        from src.utils.alerting import AlertingHandler

        handler = AlertingHandler(alert_log_path=tmp_path / "alerts.log")
        handler.setFormatter(logging.Formatter("%(message)s"))

        # 카운터를 최대치로 설정하고 리셋 시간을 과거로
        handler._hourly_count = AlertingHandler.MAX_ALERTS_PER_HOUR
        handler._hourly_reset = time.time() - 3700  # 1시간+ 경과

        record = logging.LogRecord(
            "test", logging.ERROR, "", 0, "리셋 후 에러", (), None
        )
        handler.emit(record)

        # 리셋 후 새로운 알림 카운트
        assert handler._hourly_count == 1


# =====================================================
# 6. SHA256 해시 계산 (2 tests)
# =====================================================

class TestSHA256Computation:
    """CloudSyncer.compute_sha256() 검증."""

    def test_sha256_known_value(self, tmp_path):
        """알려진 내용에 대해 SHA256 정확성 확인."""
        from scripts.sync_to_cloud import CloudSyncer

        test_file = tmp_path / "test.db"
        test_content = b"hello world test data for sha256"
        test_file.write_bytes(test_content)

        result = CloudSyncer.compute_sha256(test_file)
        expected = hashlib.sha256(test_content).hexdigest()

        assert result == expected
        assert len(result) == 64  # SHA256 hex length

    def test_sha256_different_files_differ(self, tmp_path):
        """서로 다른 파일은 다른 해시."""
        from scripts.sync_to_cloud import CloudSyncer

        file_a = tmp_path / "a.db"
        file_b = tmp_path / "b.db"
        file_a.write_bytes(b"content A")
        file_b.write_bytes(b"content B")

        hash_a = CloudSyncer.compute_sha256(file_a)
        hash_b = CloudSyncer.compute_sha256(file_b)

        assert hash_a != hash_b


# =====================================================
# 7. sync_all SHA256 포함 확인 (2 tests)
# =====================================================

class TestSyncSHA256Integration:
    """sync_to_cloud 결과에 SHA256 해시 포함 확인."""

    def test_upload_result_contains_sha256(self, tmp_path):
        """upload_file() 성공 시 sha256 필드 포함."""
        from scripts.sync_to_cloud import CloudSyncer

        # 테스트 파일 생성
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        test_db = data_dir / "common.db"
        test_db.write_bytes(b"test db content for sha256 check")

        syncer = CloudSyncer()
        syncer._config = {
            "username": "test",
            "api_token": "fake-token",
            "domain": "test.pythonanywhere.com",
            "remote_base": "/home/test",
        }

        # requests.Session mock
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_session = MagicMock()
        mock_session.post.return_value = mock_response
        mock_session.delete.return_value = MagicMock(status_code=204)
        syncer._session = mock_session

        with patch("scripts.sync_to_cloud.PROJECT_ROOT", tmp_path):
            result = syncer.upload_file("data/common.db", "data/common.db")

        assert result["success"] is True
        assert "sha256" in result
        assert len(result["sha256"]) == 64

    def test_upload_sha256_matches_file(self, tmp_path):
        """upload_file() sha256이 실제 파일 해시와 일치."""
        from scripts.sync_to_cloud import CloudSyncer

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        test_db = data_dir / "test.db"
        test_content = b"verify sha256 consistency"
        test_db.write_bytes(test_content)

        expected_hash = hashlib.sha256(test_content).hexdigest()

        syncer = CloudSyncer()
        syncer._config = {
            "username": "test",
            "api_token": "fake-token",
            "domain": "test.pythonanywhere.com",
            "remote_base": "/home/test",
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_session = MagicMock()
        mock_session.post.return_value = mock_response
        mock_session.delete.return_value = MagicMock(status_code=204)
        syncer._session = mock_session

        with patch("scripts.sync_to_cloud.PROJECT_ROOT", tmp_path):
            result = syncer.upload_file("data/test.db", "data/test.db")

        assert result["sha256"] == expected_hash
