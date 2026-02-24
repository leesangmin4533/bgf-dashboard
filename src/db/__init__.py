"""
데이터베이스 모듈 (호환 유지용)

새 코드: src.infrastructure.database.repos 사용 권장
"""

from .models import init_db, get_db_path
from src.infrastructure.database.repos import SalesRepository

__all__ = ["init_db", "get_db_path", "SalesRepository"]
