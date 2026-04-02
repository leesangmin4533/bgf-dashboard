"""카테고리 Strategy 공용 DB 커넥션 헬퍼

15개 카테고리 파일의 중복 _get_db_path() + sqlite3.connect() 패턴을
DBRouter로 통합. WAL + busy_timeout + row_factory 자동 적용.
"""
import sqlite3
from typing import Optional

from src.infrastructure.database.connection import DBRouter


def get_conn(
    store_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> sqlite3.Connection:
    """매장/공통 DB 연결 (WAL + busy_timeout 자동 적용)

    Args:
        store_id: 매장 ID → stores/{id}.db
        db_path: 명시적 경로 (테스트/오버라이드) → 직접 연결

    Returns:
        sqlite3.Connection (WAL + busy_timeout=30s + row_factory=Row)
    """
    if db_path:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn
    if store_id:
        return DBRouter.get_store_connection(store_id)
    return DBRouter.get_common_connection()
