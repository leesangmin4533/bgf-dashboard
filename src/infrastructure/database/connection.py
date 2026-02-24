"""
DB 커넥션 라우터

매장별 DB와 공통 DB를 자동 라우팅합니다.
- 공통 DB (common.db): products, mid_categories, product_details 등
- 매장별 DB (stores/{store_id}.db): daily_sales, order_tracking 등
"""

import sqlite3
from pathlib import Path
from typing import Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

# 프로젝트 루트 기준 data 디렉토리
DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"

# 공통 DB에 저장되는 테이블 목록
COMMON_TABLES = frozenset({
    'products',
    'mid_categories',
    'product_details',
    'external_factors',
    'schema_version',
    'stores',
    'store_eval_params',
})

# 매장별 DB에 저장되는 테이블 목록
STORE_TABLES = frozenset({
    'daily_sales',
    'order_tracking',
    'order_history',
    'inventory_batches',
    'realtime_inventory',
    'prediction_logs',
    'eval_outcomes',
    'promotions',
    'promotion_stats',
    'promotion_changes',
    'receiving_history',
    'auto_order_items',
    'smart_order_items',
    'collection_logs',
    'order_fail_reasons',
    'calibration_history',
    'validation_log',
    'association_rules',
    'new_product_status',
    'new_product_items',
    'new_product_monthly',
    'app_settings',
})


class DBRouter:
    """매장별 DB와 공통 DB를 자동 라우팅

    Usage:
        # 공통 DB 연결
        conn = DBRouter.get_common_connection()

        # 매장별 DB 연결
        conn = DBRouter.get_store_connection("46513")

        # 자동 라우팅
        conn = DBRouter.get_connection(store_id="46513", table="daily_sales")
    """

    @staticmethod
    def get_common_db_path() -> Path:
        """공통 DB 경로"""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return DATA_DIR / "common.db"

    @staticmethod
    def get_store_db_path(store_id: str) -> Path:
        """매장별 DB 경로"""
        store_dir = DATA_DIR / "stores"
        store_dir.mkdir(parents=True, exist_ok=True)
        return store_dir / f"{store_id}.db"

    @staticmethod
    def get_legacy_db_path() -> Path:
        """기존 단일 DB 경로 (마이그레이션 전 호환용)"""
        return DATA_DIR / "bgf_sales.db"

    @staticmethod
    def get_common_connection() -> sqlite3.Connection:
        """공통 DB 연결"""
        db_path = DBRouter.get_common_db_path()
        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def get_store_connection(store_id: str) -> sqlite3.Connection:
        """매장별 DB 연결"""
        db_path = DBRouter.get_store_db_path(store_id)
        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def get_store_connection_with_common(store_id: str) -> sqlite3.Connection:
        """매장별 DB 연결 + 공통 DB ATTACH

        공통 테이블 JOIN이 필요한 경우 사용:
            SELECT ds.*, p.item_nm
            FROM daily_sales ds
            JOIN common.products p ON ds.item_cd = p.item_cd
        """
        conn = DBRouter.get_store_connection(store_id)
        common_path = DBRouter.get_common_db_path()
        conn.execute(f"ATTACH DATABASE '{common_path}' AS common")
        return conn

    @staticmethod
    def get_connection(
        store_id: Optional[str] = None,
        table: Optional[str] = None,
    ) -> sqlite3.Connection:
        """테이블 종류에 따라 적절한 DB에 자동 연결

        Args:
            store_id: 매장 코드. 매장별 테이블 접근 시 필수.
            table: 접근할 테이블명. 지정 시 공통/매장 자동 판별.

        Returns:
            sqlite3.Connection
        """
        # 테이블이 지정된 경우: 공통/매장 자동 판별
        if table and table in COMMON_TABLES:
            return DBRouter.get_common_connection()

        # store_id가 지정된 경우: 매장별 DB
        if store_id:
            return DBRouter.get_store_connection(store_id)

        # 기본: 공통 DB
        return DBRouter.get_common_connection()

    @staticmethod
    def is_common_table(table: str) -> bool:
        """공통 테이블 여부 확인"""
        return table in COMMON_TABLES

    @staticmethod
    def is_store_table(table: str) -> bool:
        """매장별 테이블 여부 확인"""
        return table in STORE_TABLES

    @staticmethod
    def store_db_exists(store_id: str) -> bool:
        """매장 DB 파일 존재 여부"""
        return DBRouter.get_store_db_path(store_id).exists()

    @staticmethod
    def get_all_store_db_paths() -> list:
        """존재하는 모든 매장 DB 경로 반환"""
        store_dir = DATA_DIR / "stores"
        if not store_dir.exists():
            return []
        return sorted(store_dir.glob("*.db"))


# ── 공통 DB ATTACH 헬퍼 ──

# temp VIEW로 노출할 공통 테이블 (접두사 없이 접근 가능하게)
_COMMON_VIEW_TABLES = (
    "products", "mid_categories", "product_details",
    "external_factors", "app_settings",
)


def attach_common_with_views(
    conn: sqlite3.Connection,
    store_id: Optional[str] = None,
) -> sqlite3.Connection:
    """매장 DB 커넥션에 common.db ATTACH + 공통 테이블 temp VIEW 생성

    - 이미 ATTACH된 경우 스킵 (중복 방지)
    - 메인 스키마에 이미 존재하는 테이블은 VIEW 생성 안 함 (테스트 DB 보호)
    - SQL 쿼리에서 'products' 등을 접두사 없이 그대로 사용 가능

    Args:
        conn: 오픈된 SQLite 커넥션 (보통 매장 DB)
        store_id: 매장 코드 (None이면 ATTACH 안 함)

    Returns:
        동일 커넥션 (ATTACH + temp VIEW 적용됨)
    """
    if not store_id:
        return conn
    try:
        # 이미 ATTACH 되어 있는지 확인
        attached = {r[1] for r in conn.execute("PRAGMA database_list").fetchall()}
        if "common" in attached:
            return conn

        common_path = str(DBRouter.get_common_db_path())
        conn.execute(f"ATTACH DATABASE '{common_path}' AS common")

        # 메인 스키마에 없는 공통 테이블만 temp VIEW 생성
        existing = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for tbl in _COMMON_VIEW_TABLES:
            if tbl not in existing:
                try:
                    conn.execute(
                        f"CREATE TEMP VIEW IF NOT EXISTS {tbl} "
                        f"AS SELECT * FROM common.{tbl}"
                    )
                except Exception:
                    pass
    except Exception:
        pass  # common.db 없으면 무시 (테스트 환경 등)
    return conn


# ── 하위 호환 함수 (기존 코드가 models.py에서 import하던 것들) ──

def get_db_path(db_name: Optional[str] = None) -> Path:
    """기존 호환: DB 파일 경로 반환

    새 코드에서는 DBRouter.get_common_db_path() 또는
    DBRouter.get_store_db_path(store_id) 사용 권장
    """
    if db_name:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return DATA_DIR / db_name
    # 기존 단일 DB가 있으면 그것을, 없으면 common.db
    legacy = DBRouter.get_legacy_db_path()
    if legacy.exists():
        return legacy
    return DBRouter.get_common_db_path()


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """기존 호환: DB 연결 반환

    새 코드에서는 DBRouter.get_connection() 사용 권장
    """
    if db_path is None:
        db_path = get_db_path()
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn
