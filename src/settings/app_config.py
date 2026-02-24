"""
통합 설정 진입점

기존 5개 설정 위치를 하나로 통합:
- config.py (루트) → BASE_URL, BROWSER_OPTIONS, NEXACRO_CONFIG
- src/config/constants.py → 비즈니스 상수
- src/config/timing.py → 타이밍 상수
- src/config/ui_config.py → 넥사크로 UI ID

Usage:
    from src.settings.app_config import BASE_URL, BROWSER_OPTIONS
    from src.settings.constants import DEFAULT_STORE_ID
    from src.settings.timing import WAIT_AFTER_LOGIN
"""

from pathlib import Path

# ── 프로젝트 경로 ──
PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"

# ── BGF 리테일 사이트 설정 (기존 config.py에서 이동) ──
BASE_URL = "https://store.bgfretail.com/websrc/deploy/index.html"

BROWSER_OPTIONS = {
    "headless": False,
    "window_size": (1920, 1080),
    "implicit_wait": 10,
    "page_load_timeout": 30,
}

NEXACRO_CONFIG = {
    "load_wait_time": 5,
    "component_wait_time": 3,
}
