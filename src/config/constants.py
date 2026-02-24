"""
비즈니스 상수 — 역방향 호환 shim

실제 정의: src/settings/constants.py
이 파일은 기존 import 경로 호환을 위한 re-export입니다.

    from src.config.constants import X  → OK (이 shim 경유)
    from src.settings.constants import X  → OK (정식 경로)
"""

import warnings
warnings.warn("Deprecated: use src.settings.constants", DeprecationWarning, stacklevel=2)

from src.settings.constants import *  # noqa: F401, F403
