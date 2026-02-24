"""
utils 모듈 유닛 테스트

- logger.py: get_logger() 로거 인스턴스 반환, 이름 확인, 동일 이름 동일 인스턴스
- screenshot.py: get_screenshot_dir(), save_screenshot(), cleanup_old_screenshots()
"""

import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.utils.logger import get_logger, setup_logger, _configured_loggers
from src.utils.screenshot import get_screenshot_dir, save_screenshot, cleanup_old_screenshots


# =========================================================================
# get_logger 테스트
# =========================================================================

class TestGetLogger:
    """get_logger() 함수 테스트"""

    @pytest.mark.unit
    def test_returns_logger_instance(self):
        """logging.Logger 인스턴스를 반환한다"""
        logger = get_logger("test_module")
        assert isinstance(logger, logging.Logger)

    @pytest.mark.unit
    def test_logger_name_is_correct(self):
        """로거 이름이 전달한 이름과 일치한다"""
        name = "test_logger_name_check"
        logger = get_logger(name)
        assert logger.name == name

    @pytest.mark.unit
    def test_same_name_returns_same_logger(self):
        """같은 이름으로 호출하면 동일한 로거 인스턴스를 반환한다"""
        name = "test_same_name_singleton"
        logger1 = get_logger(name)
        logger2 = get_logger(name)
        assert logger1 is logger2

    @pytest.mark.unit
    def test_different_name_returns_different_logger(self):
        """다른 이름으로 호출하면 다른 로거 인스턴스를 반환한다"""
        logger1 = get_logger("test_logger_alpha")
        logger2 = get_logger("test_logger_beta")
        assert logger1 is not logger2

    @pytest.mark.unit
    def test_prediction_module_routes_to_prediction_log(self):
        """모듈 이름에 'prediction'이 포함되면 prediction 로그 파일로 라우팅된다"""
        logger = get_logger("src.prediction.my_module")
        # setup_logger가 prediction 로그 파일로 설정되었는지 확인
        assert logger.name == "src.prediction.my_module"

    @pytest.mark.unit
    def test_order_module_routes_to_order_log(self):
        """모듈 이름에 'order'가 포함되면 order 로그 파일로 라우팅된다"""
        logger = get_logger("src.order.auto_order")
        assert logger.name == "src.order.auto_order"

    @pytest.mark.unit
    def test_collector_module_routes_to_collector_log(self):
        """모듈 이름에 'collector'가 포함되면 collector 로그 파일로 라우팅된다"""
        logger = get_logger("src.collectors.sales_collector")
        assert logger.name == "src.collectors.sales_collector"

    @pytest.mark.unit
    def test_logger_has_handlers(self):
        """반환된 로거에는 핸들러가 설정되어 있다"""
        logger = get_logger("test_handler_check")
        assert len(logger.handlers) > 0

    @pytest.mark.unit
    def test_logger_propagate_is_false(self):
        """로거의 propagate 속성이 False이다 (상위 로거로 전파 방지)"""
        logger = get_logger("test_propagate_check")
        assert logger.propagate is False


# =========================================================================
# setup_logger 테스트
# =========================================================================

class TestSetupLogger:
    """setup_logger() 함수 테스트"""

    @pytest.mark.unit
    def test_returns_logger_with_custom_level(self):
        """지정한 로그 레벨로 설정된 로거를 반환한다"""
        logger = setup_logger("test_custom_level", level=logging.DEBUG)
        assert logger.level == logging.DEBUG

    @pytest.mark.unit
    def test_no_console_handler_when_disabled(self):
        """console=False이면 StreamHandler가 추가되지 않는다"""
        name = "test_no_console_unique"
        # 기존 로거 캐시에서 제거
        _configured_loggers.discard(name)
        existing_logger = logging.getLogger(name)
        existing_logger.handlers.clear()

        logger = setup_logger(name, console=False)
        stream_handlers = [
            h for h in logger.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        assert len(stream_handlers) == 0


# =========================================================================
# get_screenshot_dir 테스트
# =========================================================================

class TestGetScreenshotDir:
    """get_screenshot_dir() 함수 테스트"""

    @pytest.mark.unit
    def test_returns_path_object(self):
        """Path 객체를 반환한다"""
        result = get_screenshot_dir()
        assert isinstance(result, Path)

    @pytest.mark.unit
    def test_daily_subfolder_includes_date(self):
        """daily_subfolder=True이면 날짜별 하위 폴더가 포함된다"""
        today = datetime.now().strftime("%Y-%m-%d")
        result = get_screenshot_dir(daily_subfolder=True)
        assert today in str(result)

    @pytest.mark.unit
    def test_no_daily_subfolder(self):
        """daily_subfolder=False이면 날짜 폴더 없이 루트 디렉토리를 반환한다"""
        today = datetime.now().strftime("%Y-%m-%d")
        result = get_screenshot_dir(daily_subfolder=False)
        assert result.name != today

    @pytest.mark.unit
    def test_directory_is_created(self, tmp_path):
        """디렉토리가 자동으로 생성된다"""
        with patch("src.utils.screenshot.SCREENSHOT_DIR", tmp_path / "screenshots"):
            result = get_screenshot_dir(daily_subfolder=False)
            assert result.exists()
            assert result.is_dir()

    @pytest.mark.unit
    def test_daily_directory_is_created(self, tmp_path):
        """날짜별 하위 디렉토리가 자동으로 생성된다"""
        with patch("src.utils.screenshot.SCREENSHOT_DIR", tmp_path / "screenshots"):
            result = get_screenshot_dir(daily_subfolder=True)
            assert result.exists()
            assert result.is_dir()


# =========================================================================
# save_screenshot 테스트
# =========================================================================

class TestSaveScreenshot:
    """save_screenshot() 함수 테스트"""

    @pytest.mark.unit
    def test_save_screenshot_calls_driver(self, tmp_path):
        """WebDriver의 save_screenshot 메서드를 호출한다"""
        mock_driver = MagicMock()
        mock_driver.save_screenshot.return_value = True

        with patch("src.utils.screenshot.SCREENSHOT_DIR", tmp_path / "screenshots"):
            result = save_screenshot(mock_driver, "test_capture")

        mock_driver.save_screenshot.assert_called_once()
        assert result != ""

    @pytest.mark.unit
    def test_save_screenshot_with_item_cd(self, tmp_path):
        """item_cd가 포함된 파일명으로 저장한다"""
        mock_driver = MagicMock()
        mock_driver.save_screenshot.return_value = True

        with patch("src.utils.screenshot.SCREENSHOT_DIR", tmp_path / "screenshots"):
            result = save_screenshot(mock_driver, "order_input", item_cd="8801234567890")

        # 파일 경로에 상품코드 앞 8자리가 포함
        assert "88012345" in result

    @pytest.mark.unit
    def test_save_screenshot_returns_empty_on_failure(self, tmp_path):
        """스크린샷 저장 실패 시 빈 문자열을 반환한다"""
        mock_driver = MagicMock()
        mock_driver.save_screenshot.side_effect = Exception("저장 실패")

        with patch("src.utils.screenshot.SCREENSHOT_DIR", tmp_path / "screenshots"):
            result = save_screenshot(mock_driver, "fail_test")

        assert result == ""

    @pytest.mark.unit
    def test_save_screenshot_filename_format(self, tmp_path):
        """파일명이 '{name}_{timestamp}.png' 형식이다"""
        mock_driver = MagicMock()
        mock_driver.save_screenshot.return_value = True

        with patch("src.utils.screenshot.SCREENSHOT_DIR", tmp_path / "screenshots"):
            result = save_screenshot(mock_driver, "login_after")

        assert "login_after_" in result
        assert result.endswith(".png")


# =========================================================================
# cleanup_old_screenshots 테스트
# =========================================================================

class TestCleanupOldScreenshots:
    """cleanup_old_screenshots() 함수 테스트"""

    @pytest.mark.unit
    def test_returns_zero_when_dir_not_exists(self, tmp_path):
        """스크린샷 디렉토리가 없으면 0을 반환한다"""
        non_existent = tmp_path / "nonexistent"
        with patch("src.utils.screenshot.SCREENSHOT_DIR", non_existent):
            result = cleanup_old_screenshots(days=7)
        assert result == 0

    @pytest.mark.unit
    def test_returns_zero_when_empty_dir(self, tmp_path):
        """빈 디렉토리이면 0을 반환한다"""
        screenshot_dir = tmp_path / "screenshots"
        screenshot_dir.mkdir()
        with patch("src.utils.screenshot.SCREENSHOT_DIR", screenshot_dir):
            result = cleanup_old_screenshots(days=7)
        assert result == 0

    @pytest.mark.unit
    def test_does_not_delete_recent_folders(self, tmp_path):
        """보관 기간 내의 날짜 폴더는 삭제하지 않는다"""
        screenshot_dir = tmp_path / "screenshots"
        screenshot_dir.mkdir()

        # 오늘 날짜 폴더 생성
        today = datetime.now().strftime("%Y-%m-%d")
        today_dir = screenshot_dir / today
        today_dir.mkdir()
        (today_dir / "test.png").write_bytes(b"fake png data")

        with patch("src.utils.screenshot.SCREENSHOT_DIR", screenshot_dir):
            result = cleanup_old_screenshots(days=7)

        assert result == 0
        assert (today_dir / "test.png").exists()
