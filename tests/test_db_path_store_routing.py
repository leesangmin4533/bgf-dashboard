"""
_get_db_path(store_id) DB 라우팅 검증 테스트

카테고리 모듈의 _get_db_path(store_id)가 올바른 매장 DB로 라우팅하는지 확인.
- store_id 지정 시 stores/{store_id}.db 경로 반환
- store_id=None 시 레거시/폴백 경로 반환
- 각 카테고리 모듈이 store_id를 _get_db_path에 전달하는지 검증
"""

import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# =============================================================================
# resolve_db_path 라우팅 테스트
# =============================================================================
class TestResolveDbPath:
    """resolve_db_path()의 store_id 기반 라우팅 검증."""

    def test_store_id_46513_routes_to_store_db(self, tmp_path):
        """store_id='46513' → stores/46513.db 경로 포함 확인."""
        store_db = tmp_path / "stores" / "46513.db"
        store_db.parent.mkdir(parents=True, exist_ok=True)
        # 빈 DB 파일 생성
        conn = sqlite3.connect(str(store_db))
        conn.close()

        with patch("src.infrastructure.database.connection.DBRouter") as mock_router:
            mock_router.get_store_db_path.return_value = store_db
            mock_router.get_legacy_db_path.return_value = tmp_path / "bgf_sales.db"
            mock_router.get_common_db_path.return_value = tmp_path / "common.db"

            from src.infrastructure.database.connection import resolve_db_path
            result = resolve_db_path(store_id="46513")

        assert "46513" in result
        assert result == str(store_db)

    def test_store_id_46704_routes_to_store_db(self, tmp_path):
        """store_id='46704' → stores/46704.db 경로 포함 확인."""
        store_db = tmp_path / "stores" / "46704.db"
        store_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(store_db))
        conn.close()

        with patch("src.infrastructure.database.connection.DBRouter") as mock_router:
            mock_router.get_store_db_path.return_value = store_db
            mock_router.get_legacy_db_path.return_value = tmp_path / "bgf_sales.db"
            mock_router.get_common_db_path.return_value = tmp_path / "common.db"

            from src.infrastructure.database.connection import resolve_db_path
            result = resolve_db_path(store_id="46704")

        assert "46704" in result
        assert result == str(store_db)

    def test_store_id_none_returns_fallback(self, tmp_path):
        """store_id=None → 레거시 DB 또는 common.db 폴백 반환."""
        legacy_db = tmp_path / "bgf_sales.db"
        conn = sqlite3.connect(str(legacy_db))
        conn.close()

        with patch("src.infrastructure.database.connection.DBRouter") as mock_router:
            mock_router.get_legacy_db_path.return_value = legacy_db
            mock_router.get_common_db_path.return_value = tmp_path / "common.db"

            from src.infrastructure.database.connection import resolve_db_path
            result = resolve_db_path(store_id=None)

        # store_id 없으면 레거시 또는 common 경로
        assert "46513" not in result
        assert "46704" not in result

    def test_db_path_explicit_overrides_store_id(self, tmp_path):
        """db_path 명시 시 store_id와 무관하게 해당 경로 반환."""
        explicit_path = str(tmp_path / "custom.db")

        from src.infrastructure.database.connection import resolve_db_path
        result = resolve_db_path(db_path=explicit_path, store_id="46513")

        assert result == explicit_path


# =============================================================================
# 카테고리 모듈 store_id 전달 검증 (Mock으로 _get_db_path 호출 캡처)
# =============================================================================
class TestCategoryStoreIdPassthrough:
    """각 카테고리 분석 함수가 store_id를 _get_db_path에 전달하는지 검증."""

    def _make_mock_db(self, tmp_path, item_cd="TEST001", store_id=None):
        """테스트용 DB 생성 (daily_sales 테이블 포함)."""
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_sales (
                sales_date TEXT, item_cd TEXT, sale_qty INTEGER,
                stock_qty INTEGER, store_id TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS product_details (
                item_cd TEXT, expiration_days INTEGER, order_unit_qty INTEGER
            )
        """)
        conn.execute("""
            INSERT INTO daily_sales (sales_date, item_cd, sale_qty, stock_qty, store_id)
            VALUES (date('now', '-1 day'), ?, 5, 10, ?)
        """, (item_cd, store_id or "46513"))
        conn.commit()
        conn.close()
        return db_path

    def test_ramen_passes_store_id(self):
        """ramen.py analyze_ramen_pattern()이 store_id를 get_conn에 전달."""
        with patch("src.prediction.categories.ramen.get_conn") as mock_conn_fn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (5, 25, 10)
            mock_conn.cursor.return_value = mock_cursor
            mock_conn_fn.return_value = mock_conn

            from src.prediction.categories.ramen import analyze_ramen_pattern
            analyze_ramen_pattern(
                item_cd="TEST001",
                db_path=None,
                current_stock=0,
                pending_qty=0,
                store_id="46513"
            )

            mock_conn_fn.assert_called_once_with(store_id="46513", db_path=None)

    def test_beverage_passes_store_id(self):
        """beverage.py analyze_beverage_pattern()이 store_id를 get_conn에 전달."""
        with patch("src.prediction.categories.beverage.get_conn") as mock_conn_fn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (5, 25, 10)
            mock_conn.cursor.return_value = mock_cursor
            mock_conn_fn.return_value = mock_conn

            from src.prediction.categories.beverage import analyze_beverage_pattern
            analyze_beverage_pattern(
                item_cd="TEST001",
                mid_cd="039",
                db_path=None,
                current_stock=0,
                pending_qty=0,
                store_id="46513"
            )

            # beverage는 _learn + analyze에서 2회 호출
            mock_conn_fn.assert_any_call(store_id="46513", db_path=None)

    def test_tobacco_passes_store_id(self):
        """tobacco.py analyze_tobacco_pattern()이 store_id를 get_conn에 전달."""
        with patch("src.prediction.categories.tobacco.get_conn") as mock_conn_fn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (10, 50, 20)
            mock_conn.cursor.return_value = mock_cursor
            mock_conn_fn.return_value = mock_conn

            from src.prediction.categories.tobacco import analyze_tobacco_pattern
            analyze_tobacco_pattern(
                item_cd="TEST001",
                db_path=None,
                current_stock=0,
                pending_qty=0,
                store_id="46513"
            )

            mock_conn_fn.assert_called_once_with(store_id="46513", db_path=None)

    def test_dessert_passes_store_id(self):
        """dessert.py analyze_dessert_pattern()이 store_id를 get_conn에 전달."""
        with patch("src.prediction.categories.dessert.get_conn") as mock_conn_fn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.side_effect = [
                (3,),       # expiration_days
                (7, 35, 5)  # data_days, total_sales, latest_stock
            ]
            mock_conn.cursor.return_value = mock_cursor
            mock_conn_fn.return_value = mock_conn

            from src.prediction.categories.dessert import analyze_dessert_pattern
            analyze_dessert_pattern(
                item_cd="TEST001",
                db_path=None,
                current_stock=0,
                pending_qty=0,
                store_id="46513"
            )

            # get_conn이 최소 1회 store_id="46513"으로 호출
            calls = mock_conn_fn.call_args_list
            store_id_calls = [c for c in calls if c[1].get("store_id") == "46513"]
            assert len(store_id_calls) >= 1, f"store_id='46513' 호출 없음: {calls}"

    def test_beer_passes_store_id(self):
        """beer.py analyze_beer_pattern()이 store_id를 get_conn에 전달."""
        with patch("src.prediction.categories.beer.get_conn") as mock_conn_fn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (5, 25, 10)
            mock_cursor.fetchall.return_value = []
            mock_conn.cursor.return_value = mock_cursor
            mock_conn_fn.return_value = mock_conn

            from src.prediction.categories.beer import analyze_beer_pattern
            analyze_beer_pattern(
                item_cd="TEST001",
                db_path=None,
                current_stock=0,
                pending_qty=0,
                store_id="46513"
            )

            mock_conn_fn.assert_called_once_with(store_id="46513", db_path=None)

    def test_soju_passes_store_id(self):
        """soju.py analyze_soju_pattern()이 store_id를 get_conn에 전달."""
        with patch("src.prediction.categories.soju.get_conn") as mock_conn_fn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (5, 25, 10)
            mock_conn.cursor.return_value = mock_cursor
            mock_conn_fn.return_value = mock_conn

            from src.prediction.categories.soju import analyze_soju_pattern
            analyze_soju_pattern(
                item_cd="TEST001",
                db_path=None,
                current_stock=0,
                pending_qty=0,
                store_id="46513"
            )

            mock_conn_fn.assert_called_once_with(store_id="46513", db_path=None)

    def test_store_id_none_passes_none(self):
        """store_id=None이면 get_conn(store_id=None, db_path=None) 호출."""
        with patch("src.prediction.categories.ramen.get_conn") as mock_conn_fn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (5, 25, 10)
            mock_conn.cursor.return_value = mock_cursor
            mock_conn_fn.return_value = mock_conn

            from src.prediction.categories.ramen import analyze_ramen_pattern
            analyze_ramen_pattern(
                item_cd="TEST001",
                db_path=None,
                current_stock=0,
                pending_qty=0,
                store_id=None
            )

            mock_conn_fn.assert_called_once_with(store_id=None, db_path=None)

    def test_explicit_db_path_passed_to_get_conn(self):
        """db_path 명시 시 get_conn(db_path=...) 전달."""
        with patch("src.prediction.categories.ramen.get_conn") as mock_conn_fn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (5, 25, 10)
            mock_conn.cursor.return_value = mock_cursor
            mock_conn_fn.return_value = mock_conn

            from src.prediction.categories.ramen import analyze_ramen_pattern
            analyze_ramen_pattern(
                item_cd="TEST001",
                db_path="/tmp/explicit.db",
                current_stock=0,
                pending_qty=0,
                store_id="46513"
            )

            mock_conn_fn.assert_called_once_with(store_id="46513", db_path="/tmp/explicit.db")
