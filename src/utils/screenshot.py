"""
디버그 스크린샷 관리 유틸리티

모든 디버그/진단 스크린샷을 data/screenshots/ 폴더에 통합 저장한다.
날짜별 하위 폴더로 자동 분류되며, 오래된 파일 자동 정리를 지원한다.

사용법:
    from src.utils.screenshot import save_screenshot, get_screenshot_dir

    # WebDriver 스크린샷 저장
    save_screenshot(driver, "login_after")
    save_screenshot(driver, "order_input", item_cd="8801858011024")

    # 경로만 얻기
    path = get_screenshot_dir() / "my_file.png"
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

# 스크린샷 루트 디렉토리
SCREENSHOT_DIR = Path(__file__).parent.parent.parent / "data" / "screenshots"


def get_screenshot_dir(daily_subfolder: bool = True) -> Path:
    """
    스크린샷 저장 디렉토리 반환 (없으면 자동 생성)

    Args:
        daily_subfolder: True면 날짜별 하위 폴더 사용 (data/screenshots/2026-01-30/)

    Returns:
        디렉토리 Path 객체
    """
    if daily_subfolder:
        today = datetime.now().strftime("%Y-%m-%d")
        directory = SCREENSHOT_DIR / today
    else:
        directory = SCREENSHOT_DIR

    directory.mkdir(parents=True, exist_ok=True)
    return directory


def save_screenshot(
    driver: Any,
    name: str,
    item_cd: Optional[str] = None,
    daily_subfolder: bool = True
) -> str:
    """
    WebDriver 스크린샷을 중앙 디렉토리에 저장

    Args:
        driver: Selenium WebDriver
        name: 파일명 접두사 (예: "login_after", "order_input")
        item_cd: 상품코드 (있으면 파일명에 포함)
        daily_subfolder: True면 날짜별 하위 폴더 사용

    Returns:
        저장된 파일 경로 (실패 시 빈 문자열)
    """
    try:
        directory = get_screenshot_dir(daily_subfolder)
        timestamp = datetime.now().strftime("%H%M%S")

        if item_cd:
            filename = f"{name}_{item_cd[:8]}_{timestamp}.png"
        else:
            filename = f"{name}_{timestamp}.png"

        filepath = directory / filename
        driver.save_screenshot(str(filepath))
        logger.debug(f"스크린샷 저장: {filepath}")
        return str(filepath)

    except Exception as e:
        logger.debug(f"스크린샷 저장 실패: {e}")
        return ""


def cleanup_old_screenshots(days: int = 7) -> int:
    """
    오래된 스크린샷 자동 삭제

    Args:
        days: 보관 기간 (기본 7일, 이전 파일 삭제)

    Returns:
        삭제된 파일 수
    """
    if not SCREENSHOT_DIR.exists():
        return 0

    cutoff = datetime.now() - timedelta(days=days)
    deleted = 0

    # 날짜별 하위 폴더 정리
    for subfolder in SCREENSHOT_DIR.iterdir():
        if not subfolder.is_dir():
            # 루트의 개별 파일도 정리
            if subfolder.suffix == ".png":
                try:
                    file_date = datetime.fromtimestamp(subfolder.stat().st_mtime)
                    if file_date < cutoff:
                        subfolder.unlink()
                        deleted += 1
                except Exception:
                    pass
            continue

        # 폴더명이 날짜 형식인 경우 (YYYY-MM-DD)
        try:
            folder_date = datetime.strptime(subfolder.name, "%Y-%m-%d")
            if folder_date < cutoff:
                for f in subfolder.glob("*.png"):
                    f.unlink()
                    deleted += 1
                # 빈 폴더 삭제
                if not any(subfolder.iterdir()):
                    subfolder.rmdir()
        except ValueError:
            pass

    if deleted > 0:
        logger.info(f"스크린샷 정리: {deleted}개 파일 삭제 ({days}일 이전)")

    return deleted
