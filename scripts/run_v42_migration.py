"""DB v42 마이그레이션 + DemandClassifier 실행

1. common.db에 demand_pattern 컬럼 추가 (v42)
2. DemandClassifier로 전체 상품 패턴 분류
3. 분류 결과를 product_details에 저장
"""

import sys
import io
from pathlib import Path

# Windows UTF-8
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer"):
            setattr(sys, _sn, io.TextIOWrapper(
                _s.buffer, encoding="utf-8", errors="replace", line_buffering=True
            ))

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import sqlite3
from datetime import datetime
from src.infrastructure.database.connection import DBRouter
from src.settings.constants import DEFAULT_STORE_ID


def run_migration():
    """common.db에 v42 마이그레이션 적용"""
    common_path = DBRouter.get_common_db_path()
    print(f"\n[1] DB v42 마이그레이션: {common_path}")

    conn = sqlite3.connect(str(common_path))
    cursor = conn.cursor()

    # 현재 버전 확인
    try:
        cursor.execute("SELECT MAX(version) FROM schema_version")
        row = cursor.fetchone()
        current_version = row[0] if row and row[0] else 0
    except sqlite3.OperationalError:
        # schema_version 테이블 없으면 생성
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT
            )
        """)
        current_version = 0

    print(f"    현재 스키마 버전: v{current_version}")

    # demand_pattern 컬럼 존재 여부 확인
    cursor.execute("PRAGMA table_info(product_details)")
    cols = [r[1] for r in cursor.fetchall()]
    has_dp = "demand_pattern" in cols

    if has_dp:
        print("    demand_pattern 컬럼 이미 존재 - 마이그레이션 스킵")
        # 버전 기록만 업데이트
        if current_version < 42:
            cursor.execute(
                "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (42, datetime.now().isoformat())
            )
            conn.commit()
            print("    스키마 버전 42로 업데이트")
    else:
        print("    demand_pattern 컬럼 추가 중...")
        try:
            cursor.execute("ALTER TABLE product_details ADD COLUMN demand_pattern TEXT DEFAULT NULL")
            print("    OK - demand_pattern 컬럼 추가됨")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print(f"    이미 존재 (무시): {e}")
            else:
                raise

        try:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pd_demand_pattern ON product_details(demand_pattern)")
            print("    OK - 인덱스 생성됨")
        except sqlite3.OperationalError as e:
            print(f"    인덱스 경고 (무시): {e}")

        # 버전 기록
        cursor.execute(
            "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?, ?)",
            (42, datetime.now().isoformat())
        )
        conn.commit()
        print("    스키마 버전 42 기록 완료")

    conn.close()
    return True


def run_demand_classification(store_id: str = DEFAULT_STORE_ID):
    """DemandClassifier로 전체 상품 패턴 분류"""
    from src.prediction.demand_classifier import DemandClassifier

    print(f"\n[2] DemandClassifier 실행 (store: {store_id})...")

    # 전체 상품 목록 조회
    conn = DBRouter.get_store_connection(store_id)
    common_path = DBRouter.get_common_db_path()
    conn.execute(f"ATTACH DATABASE '{common_path}' AS common_db")
    conn.execute("CREATE TEMP VIEW IF NOT EXISTS products AS SELECT * FROM common_db.products")
    conn.execute("CREATE TEMP VIEW IF NOT EXISTS product_details AS SELECT * FROM common_db.product_details")

    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT p.item_cd, p.mid_cd
        FROM products p
        JOIN daily_sales ds ON p.item_cd = ds.item_cd
        WHERE ds.store_id = ?
        AND ds.sales_date >= date('now', '-60 days')
    """, (store_id,))

    items = [{"item_cd": row[0], "mid_cd": row[1]} for row in cursor.fetchall()]
    conn.close()

    print(f"    {len(items)}개 상품 발견 (최근 60일 판매 이력)")

    # 분류 실행
    classifier = DemandClassifier(store_id=store_id)
    results = classifier.classify_batch(items)

    # 패턴별 통계
    stats = {"daily": 0, "frequent": 0, "intermittent": 0, "slow": 0}
    for r in results.values():
        stats[r.pattern.value] += 1

    print(f"\n    분류 결과:")
    print(f"      daily     (>=70%): {stats['daily']:>5}개")
    print(f"      frequent  (40~69%): {stats['frequent']:>5}개")
    print(f"      intermittent(15~39%): {stats['intermittent']:>5}개")
    print(f"      slow      (<15%): {stats['slow']:>5}개")
    print(f"      합계:              {sum(stats.values()):>5}개")

    # product_details에 저장
    print(f"\n[3] product_details.demand_pattern 업데이트...")
    common_conn = sqlite3.connect(str(DBRouter.get_common_db_path()))
    common_cursor = common_conn.cursor()

    updated = 0
    for item_cd, result in results.items():
        common_cursor.execute("""
            UPDATE product_details
            SET demand_pattern = ?
            WHERE item_cd = ?
        """, (result.pattern.value, item_cd))
        if common_cursor.rowcount > 0:
            updated += 1

    common_conn.commit()
    common_conn.close()

    print(f"    {updated}/{len(results)}개 상품 패턴 업데이트 완료")

    return results


if __name__ == "__main__":
    print("=" * 70)
    print("   DB v42 마이그레이션 + 수요 패턴 분류")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # Step 1: 마이그레이션
    run_migration()

    # Step 2: 분류 실행
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", default=DEFAULT_STORE_ID)
    args = parser.parse_args()

    run_demand_classification(store_id=args.store)

    print("\n" + "=" * 70)
    print("   완료! test_prediction_vs_db.py를 재실행하세요.")
    print("=" * 70)
