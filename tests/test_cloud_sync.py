"""PythonAnywhere DB 동기화 테스트."""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

# 프로젝트 루트
PROJECT_ROOT = Path(__file__).parent.parent


class TestCloudSyncer:
    """CloudSyncer 클래스 테스트."""

    def _make_config(self, tmp_path):
        """테스트용 설정 파일 생성."""
        config = {
            "username": "testuser",
            "api_token": "test-token-12345",
            "domain": "testuser.pythonanywhere.com",
            "remote_base": "/home/testuser/myapp",
            "sync_files": [
                {"local": "data/common.db", "remote": "data/common.db"},
                {"local": "data/stores/46513.db", "remote": "data/stores/46513.db"},
            ],
        }
        config_path = tmp_path / "pythonanywhere.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        return config_path

    def test_load_config_success(self, tmp_path):
        """설정 파일 로드 성공."""
        from scripts.sync_to_cloud import CloudSyncer

        config_path = self._make_config(tmp_path)
        syncer = CloudSyncer(config_path=config_path)

        assert syncer.config["username"] == "testuser"
        assert syncer.config["api_token"] == "test-token-12345"
        assert syncer.config["domain"] == "testuser.pythonanywhere.com"

    def test_load_config_missing_file(self, tmp_path):
        """설정 파일 없으면 CloudSyncError."""
        from scripts.sync_to_cloud import CloudSyncer, CloudSyncError

        syncer = CloudSyncer(config_path=tmp_path / "nonexistent.json")

        with pytest.raises(CloudSyncError, match="설정 파일 없음"):
            _ = syncer.config

    def test_load_config_missing_fields(self, tmp_path):
        """필수 필드 누락 시 CloudSyncError."""
        from scripts.sync_to_cloud import CloudSyncer, CloudSyncError

        config_path = tmp_path / "bad.json"
        config_path.write_text('{"username": "test"}', encoding="utf-8")
        syncer = CloudSyncer(config_path=config_path)

        with pytest.raises(CloudSyncError, match="설정 필드 누락"):
            _ = syncer.config

    def test_is_configured_true(self, tmp_path):
        """설정 파일 존재 시 True."""
        from scripts.sync_to_cloud import CloudSyncer

        config_path = self._make_config(tmp_path)
        syncer = CloudSyncer(config_path=config_path)
        assert syncer.is_configured is True

    def test_is_configured_false(self, tmp_path):
        """설정 파일 없으면 False."""
        from scripts.sync_to_cloud import CloudSyncer

        syncer = CloudSyncer(config_path=tmp_path / "nope.json")
        assert syncer.is_configured is False

    @patch("scripts.sync_to_cloud.PROJECT_ROOT", new_callable=lambda: MagicMock)
    def test_upload_file_not_found(self, mock_root, tmp_path):
        """로컬 파일 없으면 실패 반환."""
        from scripts.sync_to_cloud import CloudSyncer

        config_path = self._make_config(tmp_path)
        syncer = CloudSyncer(config_path=config_path)

        # PROJECT_ROOT를 tmp_path로 설정 (파일 없는 상태)
        with patch("scripts.sync_to_cloud.PROJECT_ROOT", tmp_path):
            result = syncer.upload_file("data/missing.db", "data/missing.db")

        assert result["success"] is False
        assert "파일 없음" in result["error"]

    @patch("requests.Session")
    def test_upload_file_success(self, MockSession, tmp_path):
        """업로드 성공 테스트."""
        from scripts.sync_to_cloud import CloudSyncer

        config_path = self._make_config(tmp_path)
        syncer = CloudSyncer(config_path=config_path)

        # 테스트용 DB 파일 생성
        db_dir = tmp_path / "data"
        db_dir.mkdir()
        db_file = db_dir / "common.db"
        db_file.write_bytes(b"test db content" * 100)

        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_session = MagicMock()
        mock_session.post.return_value = mock_response

        syncer._session = mock_session

        with patch("scripts.sync_to_cloud.PROJECT_ROOT", tmp_path):
            result = syncer.upload_file("data/common.db", "data/common.db")

        assert result["success"] is True
        assert result["file"] == "data/common.db"
        assert result["size_kb"] >= 0
        assert "elapsed" in result

    @patch("requests.Session")
    def test_upload_file_retry_on_failure(self, MockSession, tmp_path):
        """업로드 실패 시 재시도."""
        from scripts.sync_to_cloud import CloudSyncer

        config_path = self._make_config(tmp_path)
        syncer = CloudSyncer(config_path=config_path)

        # 테스트용 파일
        db_dir = tmp_path / "data"
        db_dir.mkdir()
        (db_dir / "common.db").write_bytes(b"test")

        # 처음 2번 실패, 3번째 성공
        mock_responses = [
            MagicMock(status_code=500, text="Server Error"),
            MagicMock(status_code=500, text="Server Error"),
            MagicMock(status_code=200),
        ]
        mock_session = MagicMock()
        mock_session.post.side_effect = mock_responses
        syncer._session = mock_session

        with patch("scripts.sync_to_cloud.PROJECT_ROOT", tmp_path), \
             patch("scripts.sync_to_cloud.RETRY_DELAYS", [0, 0, 0]):
            result = syncer.upload_file("data/common.db", "data/common.db")

        assert result["success"] is True
        assert mock_session.post.call_count == 3

    @patch("requests.Session")
    def test_upload_file_all_retries_fail(self, MockSession, tmp_path):
        """모든 재시도 실패."""
        from scripts.sync_to_cloud import CloudSyncer

        config_path = self._make_config(tmp_path)
        syncer = CloudSyncer(config_path=config_path)

        db_dir = tmp_path / "data"
        db_dir.mkdir()
        (db_dir / "common.db").write_bytes(b"test")

        mock_session = MagicMock()
        mock_session.post.return_value = MagicMock(status_code=500, text="Error")
        syncer._session = mock_session

        with patch("scripts.sync_to_cloud.PROJECT_ROOT", tmp_path), \
             patch("scripts.sync_to_cloud.RETRY_DELAYS", [0, 0, 0]):
            result = syncer.upload_file("data/common.db", "data/common.db")

        assert result["success"] is False
        assert mock_session.post.call_count == 3

    @patch("requests.Session")
    def test_reload_webapp_success(self, MockSession, tmp_path):
        """웹앱 리로드 성공."""
        from scripts.sync_to_cloud import CloudSyncer

        config_path = self._make_config(tmp_path)
        syncer = CloudSyncer(config_path=config_path)

        mock_session = MagicMock()
        mock_session.post.return_value = MagicMock(status_code=200)
        syncer._session = mock_session

        result = syncer.reload_webapp()

        assert result["success"] is True
        assert result["domain"] == "testuser.pythonanywhere.com"

    @patch("requests.Session")
    def test_reload_webapp_failure(self, MockSession, tmp_path):
        """웹앱 리로드 실패."""
        from scripts.sync_to_cloud import CloudSyncer

        config_path = self._make_config(tmp_path)
        syncer = CloudSyncer(config_path=config_path)

        mock_session = MagicMock()
        mock_session.post.return_value = MagicMock(status_code=403, text="Forbidden")
        syncer._session = mock_session

        result = syncer.reload_webapp()

        assert result["success"] is False

    @patch("requests.Session")
    def test_sync_all_success(self, MockSession, tmp_path):
        """전체 동기화 성공."""
        from scripts.sync_to_cloud import CloudSyncer

        config_path = self._make_config(tmp_path)
        syncer = CloudSyncer(config_path=config_path)

        # 테스트용 파일
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "stores").mkdir()
        (tmp_path / "data" / "common.db").write_bytes(b"common" * 100)
        (tmp_path / "data" / "stores" / "46513.db").write_bytes(b"store" * 100)

        mock_session = MagicMock()
        mock_session.post.return_value = MagicMock(status_code=200)
        syncer._session = mock_session

        with patch("scripts.sync_to_cloud.PROJECT_ROOT", tmp_path):
            result = syncer.sync_all()

        assert result["success"] is True
        assert len(result["uploaded"]) == 2
        assert len(result["failed"]) == 0
        assert result["reload"]["success"] is True

    def test_sync_all_no_config(self, tmp_path):
        """설정 없으면 skip."""
        from scripts.sync_to_cloud import CloudSyncer

        syncer = CloudSyncer(config_path=tmp_path / "nope.json")
        result = syncer.sync_all()

        assert result["success"] is False
        assert result["skipped"] is True

    @patch("requests.Session")
    def test_sync_all_partial_failure(self, MockSession, tmp_path):
        """일부 파일만 업로드 실패."""
        from scripts.sync_to_cloud import CloudSyncer

        config_path = self._make_config(tmp_path)
        syncer = CloudSyncer(config_path=config_path)

        # common.db만 존재 (46513.db 없음)
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "common.db").write_bytes(b"common" * 100)

        mock_session = MagicMock()
        mock_session.post.return_value = MagicMock(status_code=200)
        syncer._session = mock_session

        with patch("scripts.sync_to_cloud.PROJECT_ROOT", tmp_path):
            result = syncer.sync_all()

        assert result["success"] is False  # 1개 실패
        assert len(result["uploaded"]) == 1
        assert len(result["failed"]) == 1


class TestRunCloudSync:
    """run_cloud_sync() 함수 테스트."""

    def test_no_config_graceful_skip(self, tmp_path):
        """설정 없으면 에러 없이 skip."""
        from scripts.sync_to_cloud import run_cloud_sync

        with patch("scripts.sync_to_cloud.CONFIG_PATH", tmp_path / "nope.json"):
            result = run_cloud_sync()

        assert result["success"] is False
        assert result.get("skipped") is True

    @patch("scripts.sync_to_cloud.CloudSyncer")
    def test_unexpected_error_caught(self, MockSyncer):
        """예외 발생해도 안전하게 처리."""
        from scripts.sync_to_cloud import run_cloud_sync

        MockSyncer.side_effect = RuntimeError("Unexpected!")

        result = run_cloud_sync()

        assert result["success"] is False
        assert "Unexpected!" in result.get("error", "")


class TestTryCloudSync:
    """_try_cloud_sync() 래퍼 테스트."""

    @patch("scripts.sync_to_cloud.run_cloud_sync")
    def test_success_logs_info(self, mock_sync):
        """동기화 성공 시 로그 기록."""
        # import here to avoid circular
        import importlib
        import run_scheduler
        importlib.reload(run_scheduler)

        mock_sync.return_value = {"success": True}
        run_scheduler._try_cloud_sync()
        mock_sync.assert_called_once()

    @patch("scripts.sync_to_cloud.run_cloud_sync")
    def test_exception_does_not_propagate(self, mock_sync):
        """예외 발생해도 전파되지 않음."""
        import importlib
        import run_scheduler
        importlib.reload(run_scheduler)

        mock_sync.side_effect = Exception("Network down!")
        # 예외 없이 정상 종료
        run_scheduler._try_cloud_sync()
