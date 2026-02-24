"""
외부 API 연동

Usage:
    from src.infrastructure.external import KakaoNotifier
"""

import warnings
warnings.warn("Deprecated: use src.notification.kakao_notifier", DeprecationWarning, stacklevel=2)

from src.notification.kakao_notifier import KakaoNotifier  # noqa: F401
