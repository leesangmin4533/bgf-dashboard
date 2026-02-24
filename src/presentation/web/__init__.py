"""
웹 표현 계층 -- Flask 앱 + 라우트

기존 src/web/ 모듈을 새 경로에서 접근 가능하도록 re-export합니다.

Usage:
    from src.presentation.web import create_app
"""

# 기존 Flask 앱 re-export
try:
    from src.web.app import create_app  # noqa: F401
except ImportError:
    pass
