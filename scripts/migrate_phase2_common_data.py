"""
Phase 2: Common Data Optimization Migration

공용 데이터 최적화 마이그레이션:
1. DB 백업 생성
2. 중복 데이터 분석 리포트
3. product_details 스키마 변경 및 병합
4. promotions 스키마 변경 및 병합
5. external_factors 스키마 변경 및 병합
6. 검증

롤백 기능 포함
"""

import sqlite3
import sys
import io

# Windows CP949 콘솔 → UTF-8 래핑 (한글·특수문자 깨짐 방지)
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Tuple
import json

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logger import get_logger
from src.db.models import get_db_path, get_connection

logger = get_logger(__name__)


class Phase2Migration:
    """Phase 2: Common Data Optimization Migration"""

    MIGRATION_VERSION = "phase2_common_data"

    def __init__(self, db_path: Path = None):
        self.db_path = db_path or get_db_path()
        self.conn = None
        self.backup_path = None
        self.analysis_report = {}
        logger.info(f"Phase 2 Migration initialized: {self.db_path}")

    def backup_database(self) -> Path:
        """DB 백업 생성

        Returns:
            백업 파일 경로
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.db_path.parent / f"bgf_sales_backup_phase2_{timestamp}.db"

        import shutil
        shutil.copy2(self.db_path, backup_path)
        logger.info(f"[백업 완료] {backup_path}")
        self.backup_path = backup_path
        return backup_path

    def analyze_duplicates(self) -> Dict[str, Any]:
        """중복 데이터 분석

        Returns:
            분석 리포트
        """
        logger.info("[분석 시작] 중복 데이터 분석 중...")

        self.conn = get_connection(self.db_path)
        cursor = self.conn.cursor()

        report = {
            'timestamp': datetime.now().isoformat(),
            'product_details': {},
            'promotions': {},
            'external_factors': {}
        }

        # 1. product_details 분석
        try:
            # 총 행 수
            cursor.execute("SELECT COUNT(*) FROM product_details")
            total_rows = cursor.fetchone()[0]

            # 고유 item_cd 수
            cursor.execute("SELECT COUNT(DISTINCT item_cd) FROM product_details")
            unique_items = cursor.fetchone()[0]

            # 중복 item_cd 목록
            cursor.execute("""
                SELECT item_cd, COUNT(DISTINCT store_id) as store_count
                FROM product_details
                GROUP BY item_cd
                HAVING store_count > 1
                ORDER BY store_count DESC
                LIMIT 10
            """)
            duplicates = cursor.fetchall()

            report['product_details'] = {
                'total_rows': total_rows,
                'unique_items': unique_items,
                'duplicate_count': total_rows - unique_items,
                'duplicate_rate': f"{((total_rows - unique_items) / total_rows * 100):.1f}%" if total_rows > 0 else "0%",
                'top_duplicates': [{'item_cd': row[0], 'store_count': row[1]} for row in duplicates]
            }

            logger.info(f"  product_details: {total_rows}행 → {unique_items}개 (중복 {total_rows - unique_items}행)")

        except Exception as e:
            logger.warning(f"  product_details 분석 실패: {e}")
            report['product_details'] = {'error': str(e)}

        # 2. promotions 분석
        try:
            cursor.execute("SELECT COUNT(*) FROM promotions")
            total_rows = cursor.fetchone()[0]

            cursor.execute("""
                SELECT COUNT(DISTINCT item_cd || promo_type || start_date)
                FROM promotions
            """)
            unique_promos = cursor.fetchone()[0]

            report['promotions'] = {
                'total_rows': total_rows,
                'unique_promotions': unique_promos,
                'duplicate_count': total_rows - unique_promos,
                'duplicate_rate': f"{((total_rows - unique_promos) / total_rows * 100):.1f}%" if total_rows > 0 else "0%"
            }

            logger.info(f"  promotions: {total_rows}행 → {unique_promos}개 (중복 {total_rows - unique_promos}행)")

        except Exception as e:
            logger.warning(f"  promotions 분석 실패: {e}")
            report['promotions'] = {'error': str(e)}

        # 3. external_factors 분석
        try:
            cursor.execute("SELECT COUNT(*) FROM external_factors")
            total_rows = cursor.fetchone()[0]

            cursor.execute("""
                SELECT COUNT(DISTINCT factor_date || factor_type || factor_key)
                FROM external_factors
            """)
            unique_factors = cursor.fetchone()[0]

            report['external_factors'] = {
                'total_rows': total_rows,
                'unique_factors': unique_factors,
                'duplicate_count': total_rows - unique_factors,
                'duplicate_rate': f"{((total_rows - unique_factors) / total_rows * 100):.1f}%" if total_rows > 0 else "0%"
            }

            logger.info(f"  external_factors: {total_rows}행 → {unique_factors}개 (중복 {total_rows - unique_factors}행)")

        except Exception as e:
            logger.warning(f"  external_factors 분석 실패: {e}")
            report['external_factors'] = {'error': str(e)}

        self.analysis_report = report
        return report

    def save_analysis_report(self, report: Dict[str, Any]):
        """분석 리포트 저장"""
        report_path = self.db_path.parent / "migration_analysis_phase2.json"

        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        logger.info(f"[리포트 저장] {report_path}")

    def migrate_product_details(self):
        """product_details 테이블 마이그레이션"""
        logger.info("[Step 1/3] product_details 마이그레이션 중...")

        cursor = self.conn.cursor()

        # Step 1: 새 테이블 생성
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS product_details_new (
                item_cd TEXT PRIMARY KEY,
                item_nm TEXT,
                expiration_days INTEGER,
                orderable_day TEXT DEFAULT '일월화수목금토',
                orderable_status TEXT,
                order_unit_name TEXT DEFAULT '낱개',
                order_unit_qty INTEGER DEFAULT 1,
                case_unit_qty INTEGER DEFAULT 1,
                lead_time_days INTEGER DEFAULT 1,
                promo_type TEXT,
                promo_name TEXT,
                promo_start TEXT,
                promo_end TEXT,
                promo_updated TEXT,
                sell_price INTEGER,
                margin_rate REAL,
                fetched_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Step 2: 병합 데이터 삽입 (최신 fetched_at 우선, NULL 병합)
        cursor.execute("""
            INSERT INTO product_details_new
            SELECT
                item_cd,
                MAX(item_nm) as item_nm,
                MAX(expiration_days) as expiration_days,
                MAX(orderable_day) as orderable_day,
                MAX(orderable_status) as orderable_status,
                MAX(order_unit_name) as order_unit_name,
                MAX(order_unit_qty) as order_unit_qty,
                MAX(case_unit_qty) as case_unit_qty,
                MAX(lead_time_days) as lead_time_days,
                MAX(promo_type) as promo_type,
                MAX(promo_name) as promo_name,
                MAX(promo_start) as promo_start,
                MAX(promo_end) as promo_end,
                MAX(promo_updated) as promo_updated,
                MAX(sell_price) as sell_price,
                MAX(margin_rate) as margin_rate,
                MAX(fetched_at) as fetched_at,
                MIN(created_at) as created_at,
                MAX(updated_at) as updated_at
            FROM product_details
            GROUP BY item_cd
        """)

        rows_inserted = cursor.rowcount
        logger.info(f"  [OK] {rows_inserted}개 상품 병합 완료")

        # Step 3: 테이블 교체
        cursor.execute("DROP TABLE product_details")
        cursor.execute("ALTER TABLE product_details_new RENAME TO product_details")

        # Step 4: 인덱스 생성
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_product_details_item
            ON product_details(item_cd)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_product_details_promo
            ON product_details(promo_type)
        """)

        self.conn.commit()
        logger.info("  [OK] product_details 마이그레이션 완료")

    def migrate_promotions(self):
        """promotions 테이블 마이그레이션"""
        logger.info("[Step 2/3] promotions 마이그레이션 중...")

        cursor = self.conn.cursor()

        # Step 1: 새 테이블 생성
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS promotions_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_cd TEXT NOT NULL,
                item_nm TEXT,
                promo_type TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                collected_at TEXT NOT NULL,
                updated_at TEXT,
                UNIQUE(item_cd, promo_type, start_date)
            )
        """)

        # Step 2: 중복 제거하여 삽입 (최신 collected_at 우선)
        cursor.execute("""
            INSERT INTO promotions_new
            SELECT
                MIN(id) as id,
                item_cd,
                MAX(item_nm) as item_nm,
                promo_type,
                start_date,
                end_date,
                MAX(is_active) as is_active,
                MAX(collected_at) as collected_at,
                MAX(updated_at) as updated_at
            FROM promotions
            GROUP BY item_cd, promo_type, start_date
        """)

        rows_inserted = cursor.rowcount
        logger.info(f"  [OK] {rows_inserted}개 행사 병합 완료")

        # Step 3: 테이블 교체
        cursor.execute("DROP TABLE promotions")
        cursor.execute("ALTER TABLE promotions_new RENAME TO promotions")

        # Step 4: 인덱스 생성
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_promotions_item
            ON promotions(item_cd)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_promotions_dates
            ON promotions(start_date, end_date)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_promotions_active
            ON promotions(is_active, end_date)
        """)

        self.conn.commit()
        logger.info("  [OK] promotions 마이그레이션 완료")

    def migrate_external_factors(self):
        """external_factors 테이블 마이그레이션"""
        logger.info("[Step 3/3] external_factors 마이그레이션 중...")

        cursor = self.conn.cursor()

        # Step 1: 새 테이블 생성
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS external_factors_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                factor_date TEXT NOT NULL,
                factor_type TEXT NOT NULL,
                factor_key TEXT NOT NULL,
                factor_value TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(factor_date, factor_type, factor_key)
            )
        """)

        # Step 2: 중복 제거하여 삽입
        cursor.execute("""
            INSERT INTO external_factors_new
            SELECT
                MIN(id) as id,
                factor_date,
                factor_type,
                factor_key,
                MAX(factor_value) as factor_value,
                MAX(created_at) as created_at
            FROM external_factors
            GROUP BY factor_date, factor_type, factor_key
        """)

        rows_inserted = cursor.rowcount
        logger.info(f"  [OK] {rows_inserted}개 외부 요인 병합 완료")

        # Step 3: 테이블 교체
        cursor.execute("DROP TABLE external_factors")
        cursor.execute("ALTER TABLE external_factors_new RENAME TO external_factors")

        # Step 4: 인덱스 생성
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_external_factors_date
            ON external_factors(factor_date)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_external_factors_type
            ON external_factors(factor_type)
        """)

        self.conn.commit()
        logger.info("  [OK] external_factors 마이그레이션 완료")

    def verify_migration(self) -> bool:
        """마이그레이션 검증

        Returns:
            검증 성공 여부
        """
        logger.info("[검증 시작] 마이그레이션 결과 검증 중...")

        cursor = self.conn.cursor()
        issues = []

        # 1. product_details 검증
        try:
            # store_id 컬럼이 없어야 함
            cursor.execute("PRAGMA table_info(product_details)")
            columns = [row[1] for row in cursor.fetchall()]

            if 'store_id' in columns:
                issues.append("product_details에 store_id 컬럼이 여전히 존재함")
            else:
                logger.info("  [OK] product_details.store_id 제거 확인")

            # item_cd가 PRIMARY KEY여야 함
            cursor.execute("""
                SELECT sql FROM sqlite_master
                WHERE type='table' AND name='product_details'
            """)
            schema = cursor.fetchone()[0]

            if 'item_cd TEXT PRIMARY KEY' in schema:
                logger.info("  [OK] product_details.item_cd PRIMARY KEY 확인")
            else:
                issues.append("product_details.item_cd가 PRIMARY KEY가 아님")

        except Exception as e:
            issues.append(f"product_details 검증 실패: {e}")

        # 2. promotions 검증
        try:
            cursor.execute("PRAGMA table_info(promotions)")
            columns = [row[1] for row in cursor.fetchall()]

            if 'store_id' in columns:
                issues.append("promotions에 store_id 컬럼이 여전히 존재함")
            else:
                logger.info("  [OK] promotions.store_id 제거 확인")

        except Exception as e:
            issues.append(f"promotions 검증 실패: {e}")

        # 3. external_factors 검증
        try:
            cursor.execute("PRAGMA table_info(external_factors)")
            columns = [row[1] for row in cursor.fetchall()]

            if 'store_id' in columns:
                issues.append("external_factors에 store_id 컬럼이 여전히 존재함")
            else:
                logger.info("  [OK] external_factors.store_id 제거 확인")

        except Exception as e:
            issues.append(f"external_factors 검증 실패: {e}")

        # 결과
        if issues:
            logger.error("[검증 실패] 다음 문제가 발견되었습니다:")
            for issue in issues:
                logger.error(f"  - {issue}")
            return False
        else:
            logger.info("[검증 성공] 모든 검사 통과!")
            return True

    def rollback(self, backup_path: Path):
        """백업으로 롤백

        Args:
            backup_path: 백업 파일 경로
        """
        logger.warning(f"[롤백 시작] {backup_path}로 복원 중...")

        if self.conn:
            self.conn.close()

        import shutil
        shutil.copy2(backup_path, self.db_path)

        logger.info(f"[롤백 완료] DB 복원 완료")

    def run(self, backup_only: bool = False, analyze_only: bool = False) -> bool:
        """마이그레이션 실행

        Args:
            backup_only: True면 백업만 수행
            analyze_only: True면 분석만 수행

        Returns:
            성공 시 True
        """
        try:
            # 1. 백업
            backup_path = self.backup_database()

            if backup_only:
                logger.info("[완료] 백업만 수행됨")
                return True

            # 2. 분석
            report = self.analyze_duplicates()
            self.save_analysis_report(report)

            if analyze_only:
                logger.info("[완료] 분석만 수행됨")
                logger.info(f"리포트: {self.db_path.parent}/migration_analysis_phase2.json")
                return True

            # 3. 마이그레이션 실행
            logger.info("")
            logger.info("=" * 80)
            logger.info("마이그레이션 시작")
            logger.info("=" * 80)

            self.migrate_product_details()
            self.migrate_promotions()
            self.migrate_external_factors()

            # 4. 검증
            logger.info("")
            if not self.verify_migration():
                raise Exception("검증 실패")

            # 5. 완료
            logger.info("")
            logger.info("=" * 80)
            logger.info("마이그레이션 성공!")
            logger.info("=" * 80)
            logger.info(f"백업 파일: {backup_path}")
            logger.info(f"분석 리포트: {self.db_path.parent}/migration_analysis_phase2.json")

            return True

        except Exception as e:
            logger.error(f"마이그레이션 실패: {e}")
            logger.error("롤백을 수행합니다...")
            self.rollback(backup_path)
            return False

        finally:
            if self.conn:
                self.conn.close()


def main():
    """메인 실행 함수"""
    import argparse

    parser = argparse.ArgumentParser(description='Phase 2: Common Data Optimization Migration')
    parser.add_argument('--backup-only', action='store_true', help='백업만 수행')
    parser.add_argument('--analyze-only', action='store_true', help='분석만 수행')
    parser.add_argument('--db-path', type=str, help='DB 파일 경로 (기본값: data/bgf_sales.db)')

    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None

    migration = Phase2Migration(db_path)
    success = migration.run(
        backup_only=args.backup_only,
        analyze_only=args.analyze_only
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
