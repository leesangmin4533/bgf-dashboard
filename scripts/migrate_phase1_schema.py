"""
Phase 1: DB Schema Extension for Multi-Store Architecture

마이그레이션 내용:
1. stores 테이블 생성 (매장 마스터)
2. store_eval_params 테이블 생성 (매장별 파라미터)
3. 기존 12개 테이블에 store_id 컬럼 추가
4. 복합 인덱스 생성
5. 기본 매장(46513) 데이터 삽입

롤백 기능 포함
"""

import sqlite3
from pathlib import Path
from datetime import datetime
import sys
import io

# Windows CP949 콘솔 → UTF-8 래핑 (한글·특수문자 깨짐 방지)
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logger import get_logger
from src.db.models import get_db_path, get_connection

logger = get_logger(__name__)


class Phase1Migration:
    """Phase 1: DB Schema Extension"""

    # 마이그레이션 버전
    MIGRATION_VERSION = "phase1_multi_store"

    # store_id를 추가할 테이블 목록 (12개)
    TABLES_TO_MIGRATE = [
        "daily_sales",
        "orders",
        "prediction_logs",
        "eval_outcomes",
        "calibration_history",
        "order_history",
        "product_details",
        "promotions",
        "realtime_inventory",
        "inventory_batches",
        "fail_reasons",
        "holidays"
    ]

    def __init__(self, db_path: Path = None):
        self.db_path = db_path or get_db_path()
        self.conn = None
        logger.info(f"Phase 1 Migration initialized: {self.db_path}")

    def backup_database(self) -> Path:
        """DB 백업 생성

        Returns:
            백업 파일 경로
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.db_path.parent / f"bgf_sales_backup_{timestamp}.db"

        import shutil
        shutil.copy2(self.db_path, backup_path)
        logger.info(f"[백업 완료] {backup_path}")
        return backup_path

    def check_migration_status(self) -> bool:
        """마이그레이션 실행 여부 확인

        Returns:
            이미 실행된 경우 True
        """
        try:
            self.conn = get_connection(self.db_path)
            cursor = self.conn.cursor()

            # stores 테이블 존재 여부 확인
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='stores'
            """)

            if cursor.fetchone():
                logger.info("[확인] 이미 Phase 1 마이그레이션이 실행되었습니다.")
                return True

            return False

        except Exception as e:
            logger.error(f"마이그레이션 상태 확인 실패: {e}")
            return False

    def create_stores_table(self):
        """stores 테이블 생성"""
        logger.info("[Step 1/5] stores 테이블 생성 중...")

        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stores (
                store_id TEXT PRIMARY KEY,
                store_name TEXT NOT NULL,
                location TEXT,
                type TEXT,
                is_active INTEGER DEFAULT 1,
                bgf_user_id TEXT,
                bgf_password TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # 인덱스 생성
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_stores_active
            ON stores(is_active)
        """)

        self.conn.commit()
        logger.info("[OK] stores 테이블 생성 완료")

    def create_store_eval_params_table(self):
        """store_eval_params 테이블 생성"""
        logger.info("[Step 2/5] store_eval_params 테이블 생성 중...")

        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS store_eval_params (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id TEXT NOT NULL,
                param_name TEXT NOT NULL,
                param_value REAL NOT NULL,
                default_value REAL,
                min_value REAL,
                max_value REAL,
                max_delta REAL,
                description TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(store_id, param_name),
                FOREIGN KEY (store_id) REFERENCES stores(store_id) ON DELETE CASCADE
            )
        """)

        # 인덱스 생성
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_store_eval_params_store
            ON store_eval_params(store_id)
        """)

        self.conn.commit()
        logger.info("[OK] store_eval_params 테이블 생성 완료")

    def add_store_id_columns(self):
        """기존 테이블에 store_id 컬럼 추가"""
        logger.info("[Step 3/5] 기존 테이블에 store_id 컬럼 추가 중...")

        cursor = self.conn.cursor()

        for table_name in self.TABLES_TO_MIGRATE:
            try:
                # 테이블 존재 여부 확인
                cursor.execute(f"""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='{table_name}'
                """)

                if not cursor.fetchone():
                    logger.warning(f"  [SKIP] {table_name} 테이블이 존재하지 않습니다.")
                    continue

                # store_id 컬럼 이미 있는지 확인
                cursor.execute(f"PRAGMA table_info({table_name})")
                columns = [row[1] for row in cursor.fetchall()]

                if 'store_id' in columns:
                    logger.info(f"  [SKIP] {table_name}.store_id 이미 존재")
                    continue

                # store_id 컬럼 추가 (기본값: '46513')
                cursor.execute(f"""
                    ALTER TABLE {table_name}
                    ADD COLUMN store_id TEXT DEFAULT '46513'
                """)

                logger.info(f"  [OK] {table_name}.store_id 추가 완료")

            except Exception as e:
                logger.error(f"  [ERROR] {table_name} 컬럼 추가 실패: {e}")
                raise

        self.conn.commit()
        logger.info("[OK] store_id 컬럼 추가 완료")

    def create_indexes(self):
        """복합 인덱스 생성"""
        logger.info("[Step 4/5] 복합 인덱스 생성 중...")

        cursor = self.conn.cursor()

        # 인덱스 정의: (테이블명, 인덱스명, 컬럼 리스트)
        indexes = [
            ("daily_sales", "idx_daily_sales_store", ["store_id", "sales_date"]),
            ("orders", "idx_orders_store", ["store_id", "order_date"]),
            ("prediction_logs", "idx_prediction_logs_store", ["store_id", "predicted_at"]),
            ("eval_outcomes", "idx_eval_outcomes_store", ["store_id", "decision_date"]),
            ("calibration_history", "idx_calibration_history_store", ["store_id", "calibrated_at"]),
            ("order_history", "idx_order_history_store", ["store_id", "order_date"]),
            ("product_details", "idx_product_details_store", ["store_id", "item_cd"]),
            ("promotions", "idx_promotions_store", ["store_id", "promo_month"]),
            ("realtime_inventory", "idx_realtime_inventory_store", ["store_id", "item_cd"]),
            ("inventory_batches", "idx_inventory_batches_store", ["store_id", "item_cd"]),
        ]

        for table_name, index_name, columns in indexes:
            try:
                # 테이블 존재 확인
                cursor.execute(f"""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='{table_name}'
                """)

                if not cursor.fetchone():
                    logger.warning(f"  [SKIP] {table_name} 테이블이 존재하지 않습니다.")
                    continue

                # 인덱스 생성
                columns_str = ", ".join(columns)
                cursor.execute(f"""
                    CREATE INDEX IF NOT EXISTS {index_name}
                    ON {table_name}({columns_str})
                """)

                logger.info(f"  [OK] {index_name} 생성 완료")

            except Exception as e:
                logger.error(f"  [ERROR] {index_name} 생성 실패: {e}")
                # 인덱스 생성 실패는 치명적이지 않으므로 계속 진행

        self.conn.commit()
        logger.info("[OK] 인덱스 생성 완료")

    def insert_default_store(self):
        """기본 매장(46513) 데이터 삽입"""
        logger.info("[Step 5/5] 기본 매장 데이터 삽입 중...")

        cursor = self.conn.cursor()
        now = datetime.now().isoformat()

        # 이미 존재하는지 확인
        cursor.execute("SELECT store_id FROM stores WHERE store_id = ?", ("46513",))
        if cursor.fetchone():
            logger.info("  [SKIP] 매장 46513 이미 존재")
            return

        # 기본 매장 삽입
        cursor.execute("""
            INSERT INTO stores (
                store_id, store_name, location, type, is_active,
                bgf_user_id, bgf_password, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "46513",
            "씨제이오산냉장2점",
            "경기 오산시",
            "일반점",
            1,
            None,  # bgf_user_id는 나중에 설정
            None,  # bgf_password는 나중에 설정
            now,
            now
        ))

        self.conn.commit()
        logger.info("[OK] 기본 매장 삽입 완료")

    def run(self) -> bool:
        """마이그레이션 실행

        Returns:
            성공 시 True
        """
        try:
            # 1. 이미 실행되었는지 확인
            if self.check_migration_status():
                return True

            # 2. 백업 생성
            backup_path = self.backup_database()

            # 3. 마이그레이션 실행
            logger.info("=" * 80)
            logger.info("Phase 1 마이그레이션 시작")
            logger.info("=" * 80)

            self.conn = get_connection(self.db_path)

            self.create_stores_table()
            self.create_store_eval_params_table()
            self.add_store_id_columns()
            self.create_indexes()
            self.insert_default_store()

            logger.info("=" * 80)
            logger.info("[OK] Phase 1 마이그레이션 완료!")
            logger.info("=" * 80)
            logger.info(f"백업 파일: {backup_path}")

            return True

        except Exception as e:
            logger.error(f"[ERROR] 마이그레이션 실패: {e}")
            logger.error(f"롤백 방법: 백업 파일을 원본으로 복원하세요.")
            return False

        finally:
            if self.conn:
                self.conn.close()

    def rollback(self, backup_path: Path):
        """마이그레이션 롤백

        Args:
            backup_path: 백업 파일 경로
        """
        import shutil

        try:
            if self.conn:
                self.conn.close()

            shutil.copy2(backup_path, self.db_path)
            logger.info(f"[롤백 완료] {backup_path} → {self.db_path}")

        except Exception as e:
            logger.error(f"롤백 실패: {e}")


def main():
    """메인 실행 함수"""
    print("=" * 80)
    print("Phase 1: DB Schema Extension for Multi-Store Architecture")
    print("=" * 80)
    print()
    print("이 스크립트는 다음 작업을 수행합니다:")
    print("1. stores 테이블 생성")
    print("2. store_eval_params 테이블 생성")
    print("3. 12개 테이블에 store_id 컬럼 추가")
    print("4. 복합 인덱스 생성")
    print("5. 기본 매장(46513) 데이터 삽입")
    print()
    print("[주의] DB가 자동으로 백업됩니다.")
    print()

    response = input("계속하시겠습니까? (yes/no): ").strip().lower()

    if response != "yes":
        print("마이그레이션이 취소되었습니다.")
        return

    # 마이그레이션 실행
    migration = Phase1Migration()
    success = migration.run()

    if success:
        print()
        print("[성공] 마이그레이션이 성공적으로 완료되었습니다!")
        print()
        print("다음 단계:")
        print("  - Phase 2: python scripts/migrate_phase2_params.py")
    else:
        print()
        print("[실패] 마이그레이션이 실패했습니다.")
        print("   로그를 확인하고 백업 파일로 복원하세요.")
        sys.exit(1)


if __name__ == "__main__":
    main()
