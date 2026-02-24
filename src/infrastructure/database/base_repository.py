"""
BaseRepository — 모든 Repository의 기반 클래스

DB 라우팅을 내장하여 공통 DB와 매장별 DB를 자동 선택합니다.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.infrastructure.database.connection import DBRouter, get_db_path, get_connection
from src.utils.logger import get_logger

logger = get_logger(__name__)


class BaseRepository:
    """기본 저장소 클래스

    DB 라우팅 모드:
    - db_type="common": 항상 공통 DB 사용 (products, mid_categories 등)
    - db_type="store": 항상 매장별 DB 사용 (daily_sales, order_tracking 등)
    - db_type="legacy": 기존 단일 DB 사용 (마이그레이션 전 호환)

    Usage:
        class SalesRepository(BaseRepository):
            db_type = "store"  # 매장별 DB 사용

        repo = SalesRepository(store_id="46513")
        conn = repo._get_conn()  # → data/stores/46513.db
    """

    # 서브클래스에서 오버라이드
    db_type: str = "legacy"  # "common" | "store" | "legacy"

    def __init__(
        self,
        db_path: Optional[Path] = None,
        store_id: Optional[str] = None,
    ):
        """초기화

        Args:
            db_path: 직접 DB 경로 지정 (테스트용). 지정 시 db_type 무시.
            store_id: 매장 코드. db_type="store"일 때 필수.
        """
        self._db_path = db_path
        self.store_id = store_id

        # 기존 호환: db_path가 지정된 경우 (테스트 등)
        if db_path and not db_path.exists():
            from src.infrastructure.database.schema import init_db
            init_db(db_path)

    def _get_conn(self) -> sqlite3.Connection:
        """DB 연결 반환 (db_type에 따라 자동 라우팅)"""
        # 직접 경로가 지정된 경우 (테스트, 마이그레이션)
        if self._db_path:
            return get_connection(self._db_path)

        # DB 타입에 따라 라우팅
        if self.db_type == "common":
            return DBRouter.get_common_connection()
        elif self.db_type == "store":
            if not self.store_id:
                # store_id 없이 매장 DB 접근 시 기존 단일 DB 사용 (호환)
                return get_connection()
            conn = DBRouter.get_store_connection(self.store_id)
            from src.infrastructure.database.connection import attach_common_with_views
            return attach_common_with_views(conn, self.store_id)
        else:
            # legacy: 기존 단일 DB
            return get_connection()

    def _get_conn_with_common(self) -> sqlite3.Connection:
        """매장 DB + 공통 DB ATTACH 연결

        Usage:
            conn = self._get_conn_with_common()
            cursor.execute('''
                SELECT ds.*, p.item_nm
                FROM daily_sales ds
                JOIN common.products p ON ds.item_cd = p.item_cd
            ''')
        """
        if self._db_path:
            return get_connection(self._db_path)

        if self.db_type == "store" and self.store_id:
            return DBRouter.get_store_connection_with_common(self.store_id)
        return self._get_conn()

    def _now(self) -> str:
        """현재 시각 ISO 포맷"""
        return datetime.now().isoformat()

    def _to_int(self, value: Any) -> int:
        """값을 정수로 변환"""
        if value is None or value == "":
            return 0
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0

    def _to_positive_int(self, value: Any) -> int:
        """값을 양의 정수로 변환 (음수 → 0)"""
        result = self._to_int(value)
        return max(0, result)

    def _to_price(self, value: Any) -> Optional[int]:
        """가격 문자열을 정수로 변환 ('3,500' → 3500)"""
        if value is None or value == "":
            return None
        try:
            if isinstance(value, str):
                value = value.replace(",", "")
            return int(float(value))
        except (ValueError, TypeError):
            return None

    def _store_filter(self, alias: str = "", store_id: Optional[str] = None):
        """store_id 필터 SQL 조각 생성 헬퍼"""
        from src.db.store_query import store_filter
        return store_filter(alias, store_id)

    def _to_float(self, value: Any) -> Optional[float]:
        """값을 실수로 변환"""
        if value is None or value == "":
            return None
        try:
            if isinstance(value, str):
                value = value.replace(",", "")
            return float(value)
        except (ValueError, TypeError):
            return None
