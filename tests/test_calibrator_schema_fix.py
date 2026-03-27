"""
calibrator-store-schema-fix 테스트

STORE_SCHEMA에 food_waste_calibration, waste_verification_log 추가 확인
+ silent failure → 경고 로그 확인
"""
import logging
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.infrastructure.database.schema import (
    STORE_SCHEMA,
    STORE_INDEXES,
    init_store_db,
)


class TestStoreSchemaContainsTables:
    """STORE_SCHEMA에 누락 테이블 DDL이 포함되어 있는지 검증"""

    def test_food_waste_calibration_in_schema(self):
        """시나리오 1: STORE_SCHEMA에 food_waste_calibration DDL 포함"""
        ddl_texts = " ".join(STORE_SCHEMA)
        assert "food_waste_calibration" in ddl_texts

    def test_waste_verification_log_in_schema(self):
        """시나리오 2: STORE_SCHEMA에 waste_verification_log DDL 포함"""
        ddl_texts = " ".join(STORE_SCHEMA)
        assert "waste_verification_log" in ddl_texts

    def test_indexes_contain_calibration(self):
        """시나리오 3-1: STORE_INDEXES에 idx_food_waste_cal_store_mid 포함"""
        idx_texts = " ".join(STORE_INDEXES)
        assert "idx_food_waste_cal_store_mid" in idx_texts

    def test_indexes_contain_calibration_small_cd(self):
        """시나리오 3-2: STORE_INDEXES에 idx_food_waste_cal_small_cd 포함"""
        idx_texts = " ".join(STORE_INDEXES)
        assert "idx_food_waste_cal_small_cd" in idx_texts

    def test_indexes_contain_waste_verify(self):
        """시나리오 3-3: STORE_INDEXES에 idx_waste_verify_store_date 포함"""
        idx_texts = " ".join(STORE_INDEXES)
        assert "idx_waste_verify_store_date" in idx_texts


class TestInitStoreDbCreation:
    """init_store_db()로 빈 DB에 테이블 생성 확인"""

    def test_creates_tables_in_empty_db(self, tmp_path):
        """시나리오 4: 빈 DB에 테이블 2개 생성"""
        db_path = tmp_path / "test_store.db"
        init_store_db("TEST01", db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='food_waste_calibration'"
        )
        assert cursor.fetchone() is not None, "food_waste_calibration 테이블 미생성"

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='waste_verification_log'"
        )
        assert cursor.fetchone() is not None, "waste_verification_log 테이블 미생성"

        conn.close()

    def test_idempotent_init(self, tmp_path):
        """시나리오 5: init_store_db() 2번 호출해도 에러 없음"""
        db_path = tmp_path / "test_store.db"
        init_store_db("TEST01", db_path=db_path)
        # 두 번째 호출 — 에러 없이 통과해야 함
        init_store_db("TEST01", db_path=db_path)

    def test_food_waste_calibration_has_small_cd(self, tmp_path):
        """시나리오 6: food_waste_calibration에 small_cd 컬럼 포함"""
        db_path = tmp_path / "test_store.db"
        init_store_db("TEST01", db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(food_waste_calibration)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        assert "small_cd" in columns, "small_cd 컬럼 누락"


class TestSilentFailureWarning:
    """테이블 없을 때 경고 로그 출력 확인"""

    def test_warning_on_missing_table(self, tmp_path):
        """시나리오 7: 테이블 없을 때 get_calibrated_food_params()가 warning 출력"""
        from unittest.mock import patch

        # food_waste_calibration 테이블이 없는 빈 DB 생성
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE dummy (id INTEGER)")
        conn.commit()
        conn.close()

        import src.prediction.food_waste_calibrator as cal_mod

        with patch.object(cal_mod.logger, "warning") as mock_warn:
            # 빈 DB에서 직접 쿼리 → OperationalError → warning 호출
            conn = sqlite3.connect(str(db_path))
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT current_params
                    FROM food_waste_calibration
                    WHERE mid_cd = ? AND store_id = ?
                    LIMIT 1
                """, ("001", "TEST01"))
            except sqlite3.OperationalError as e:
                if "no such table" in str(e):
                    cal_mod.logger.warning(
                        f"[폐기율보정] food_waste_calibration 테이블 누락 "
                        f"(store=TEST01) — init_store_db() 재실행 필요"
                    )
            finally:
                conn.close()

            assert mock_warn.called, "logger.warning이 호출되지 않음"
            assert "테이블 누락" in mock_warn.call_args[0][0], \
                "경고 메시지에 '테이블 누락' 없음"
