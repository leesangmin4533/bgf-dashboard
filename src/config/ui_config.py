"""
UI 설정 — 역방향 호환 shim

실제 정의: src/settings/ui_config.py
이 파일은 기존 import 경로 호환을 위한 re-export입니다.
"""

import warnings
warnings.warn("Deprecated: use src.settings.ui_config", DeprecationWarning, stacklevel=2)

from src.settings.ui_config import *  # noqa: F401, F403
