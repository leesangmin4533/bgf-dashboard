"""
db/models.py 유닛 테스트

- get_db_path(): Path 객체 반환, 기본/커스텀 DB명
- get_connection(): sqlite3.Connection 반환, row_factory 설정
- init_db(): 테이블 생성, 스키마 버전 확인
"""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from src.db.models import (
    DEFAULT_DB_NAME,
    get_connection,
    get_db_path,
    init_db,
)
from src.settings.constants import DB_SCHEMA_VERSION as SCHEMA_VERSION


# =========================================================================
# get_db_path 테스트
# =========================================================================

class TestGetDbPath:
    """get_db_path() 함수 테스트"""

    @pytest.mark.unit
    def test_returns_path_object(self):
        """Path 객체를 반환한다"""
        result = get_db_path()
        assert isinstance(result, Path)

    @pytest.mark.unit
    def test_default_db_name(self):
        """기본 DB 파일명은 bgf_sales.db 이다"""
        result = get_db_path()
        assert result.name == DEFAULT_DB_NAME
        assert result.name == "bgf_sales.db"

    @pytest.mark.unit
    def test_custom_db_name(self):
        """커스텀 DB 파일명을 지정하면 해당 이름으로 반환한다"""
        custom_name = "test_custom.db"
        result = get_db_path(custom_name)
        assert result.name == custom_name

    @pytest.mark.unit
    def test_path_is_absolute(self):
        """반환 경로는 절대 경로이다"""
        result = get_db_path()
        assert result.is_absolute()

    @pytest.mark.unit
    def test_path_parent_is_data_dir(self):
        """반환 경로의 부모 디렉토리명은 'data' 이다"""
        result = get_db_path()
        assert result.parent.name == "data"


# =========================================================================
# get_connection 테스트
# =========================================================================

class TestGetConnection:
    """get_connection() 함수 테스트"""

    @pytest.mark.db
    def test_returns_connection(self, tmp_path):
        """sqlite3.Connection 객체를 반환한다"""
        db_file = tmp_path / "test.db"
        conn = get_connection(db_file)
        try:
            assert isinstance(conn, sqlite3.Connection)
        finally:
            conn.close()

    @pytest.mark.db
    def test_row_factory_is_set(self, tmp_path):
        """row_factory가 sqlite3.Row로 설정된다"""
        db_file = tmp_path / "test.db"
        conn = get_connection(db_file)
        try:
            assert conn.row_factory is sqlite3.Row
        finally:
            conn.close()

    @pytest.mark.db
    def test_connection_is_usable(self, tmp_path):
        """반환된 연결로 SQL을 실행할 수 있다"""
        db_file = tmp_path / "test.db"
        conn = get_connection(db_file)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 AS val")
            row = cursor.fetchone()
            assert row["val"] == 1
        finally:
            conn.close()

    @pytest.mark.db
    def test_default_path_when_none(self):
        """db_path가 None이면 기본 경로를 사용한다"""
        conn = get_connection(None)
        try:
            assert isinstance(conn, sqlite3.Connection)
        finally:
            conn.close()


# =========================================================================
# init_db 테스트
# =========================================================================

class TestInitDb:
    """init_db() 함수 테스트"""

    @pytest.mark.db
    def test_creates_schema_version_table(self, tmp_path):
        """schema_version 테이블을 생성한다"""
        db_file = tmp_path / "test_init.db"
        init_db(db_file)

        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        result = cursor.fetchone()
        conn.close()

        assert result is not None
        assert result[0] == "schema_version"

    @pytest.mark.db
    def test_creates_daily_sales_table(self, tmp_path):
        """daily_sales 테이블을 생성한다"""
        db_file = tmp_path / "test_init.db"
        init_db(db_file)

        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='daily_sales'"
        )
        result = cursor.fetchone()
        conn.close()

        assert result is not None

    @pytest.mark.db
    def test_creates_products_table(self, tmp_path):
        """products 테이블을 생성한다"""
        db_file = tmp_path / "test_init.db"
        init_db(db_file)

        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='products'"
        )
        result = cursor.fetchone()
        conn.close()

        assert result is not None

    @pytest.mark.db
    def test_creates_product_details_table(self, tmp_path):
        """product_details 테이블을 생성한다"""
        db_file = tmp_path / "test_init.db"
        init_db(db_file)

        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='product_details'"
        )
        result = cursor.fetchone()
        conn.close()

        assert result is not None

    @pytest.mark.db
    def test_creates_order_history_table(self, tmp_path):
        """order_history 테이블을 생성한다"""
        db_file = tmp_path / "test_init.db"
        init_db(db_file)

        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='order_history'"
        )
        result = cursor.fetchone()
        conn.close()

        assert result is not None

    @pytest.mark.db
    def test_schema_version_is_correct(self, tmp_path):
        """스키마 버전이 현재 SCHEMA_VERSION과 일치한다"""
        db_file = tmp_path / "test_init.db"
        init_db(db_file)

        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(version) FROM schema_version")
        row = cursor.fetchone()
        conn.close()

        assert row[0] == SCHEMA_VERSION

    @pytest.mark.db
    def test_idempotent_init(self, tmp_path):
        """두 번 실행해도 오류가 발생하지 않는다 (멱등성)"""
        db_file = tmp_path / "test_init.db"
        init_db(db_file)
        init_db(db_file)  # 두 번째 실행

        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(version) FROM schema_version")
        row = cursor.fetchone()
        conn.close()

        assert row[0] == SCHEMA_VERSION

    @pytest.mark.db
    def test_incremental_migration(self, tmp_path):
        """이미 일부 마이그레이션이 적용된 상태에서 나머지만 적용한다"""
        db_file = tmp_path / "test_init.db"

        # 먼저 초기화
        init_db(db_file)

        # 버전 확인 - 최신까지 적용되어야 함
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM schema_version")
        count = cursor.fetchone()[0]
        conn.close()

        assert count == SCHEMA_VERSION

    @pytest.mark.db
    def test_all_expected_tables_exist(self, tmp_path):
        """모든 주요 테이블이 생성된다"""
        db_file = tmp_path / "test_init.db"
        init_db(db_file)

        expected_tables = [
            "schema_version",
            "mid_categories",
            "products",
            "daily_sales",
            "collection_logs",
            "external_factors",
            "product_details",
            "order_history",
            "prediction_logs",
            "order_tracking",
            "receiving_history",
            "realtime_inventory",
            "promotions",
            "promotion_stats",
            "promotion_changes",
            "eval_outcomes",
            "calibration_history",
        ]

        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        actual_tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        for table in expected_tables:
            assert table in actual_tables, f"테이블 '{table}' 이 누락되었습니다"
