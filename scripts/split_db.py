"""
DB 분할 마이그레이션 스크립트

기존 단일 DB (bgf_sales.db)를 공통 DB + 매장별 DB로 분할합니다.

Usage:
    python scripts/split_db.py              # 분할 실행
    python scripts/split_db.py --verify     # 분할 결과 검증
    python scripts/split_db.py --dry-run    # 시뮬레이션 (실제 분할하지 않음)

실행 순서:
    1. 기존 DB를 data/archive/에 백업
    2. 공통 DB (common.db) 생성: products, mid_categories 등
    3. 매장별 DB (stores/{store_id}.db) 생성: daily_sales, order_tracking 등
    4. 데이터 무결성 검증
"""

import argparse
import io
import sqlite3
import shutil
import sys
from pathlib import Path
from datetime import datetime

# Windows CP949 콘솔 → UTF-8 래핑 (한글·특수문자 깨짐 방지)
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

# 프로젝트 루트
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LEGACY_DB = DATA_DIR / "bgf_sales.db"
COMMON_DB = DATA_DIR / "common.db"
STORE_DB_DIR = DATA_DIR / "stores"
ARCHIVE_DIR = DATA_DIR / "archive"

# 공통 테이블 (매장 무관, 전역 참조 데이터)
COMMON_TABLES = [
    'products',
    'mid_categories',
    'product_details',
    'external_factors',
    'app_settings',
    'schema_version',
    'stores',
    'store_eval_params',
]

# 매장별 테이블 (store_id로 필터링하여 분배)
STORE_TABLES = [
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
]


def backup_legacy_db():
    """기존 DB 백업"""
    if not LEGACY_DB.exists():
        print(f"[ERROR] 기존 DB가 없습니다: {LEGACY_DB}")
        return False

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = ARCHIVE_DIR / f"bgf_sales_{timestamp}.db.bak"
    shutil.copy2(LEGACY_DB, backup_path)
    size_mb = backup_path.stat().st_size / 1024 / 1024
    print(f"  백업 완료: {backup_path} ({size_mb:.1f} MB)")
    return True


def get_store_ids_from_db() -> list:
    """기존 DB에서 store_id 목록 추출"""
    conn = sqlite3.connect(str(LEGACY_DB))
    try:
        cursor = conn.cursor()
        # daily_sales가 가장 큰 테이블 — 여기서 store_id 추출
        cursor.execute(
            "SELECT DISTINCT store_id FROM daily_sales "
            "WHERE store_id IS NOT NULL AND store_id != '' "
            "ORDER BY store_id"
        )
        store_ids = [row[0] for row in cursor.fetchall()]
        print(f"  발견된 매장: {store_ids} ({len(store_ids)}개)")
        return store_ids
    finally:
        conn.close()


def _table_exists(cursor, table_name: str) -> bool:
    """테이블 존재 여부 확인"""
    cursor.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone()[0] > 0


def _get_columns(cursor, table_name: str) -> list:
    """테이블의 컬럼 목록 반환"""
    cursor.execute(f"PRAGMA table_info({table_name})")
    return [col[1] for col in cursor.fetchall()]


def _copy_indexes(src_cursor, dst_cursor, table_list: list):
    """지정된 테이블 목록에 해당하는 인덱스 복사"""
    src_cursor.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='index' AND sql IS NOT NULL"
    )
    for idx_name, idx_sql in src_cursor.fetchall():
        # 인덱스가 어느 테이블에 속하는지 확인
        # CREATE INDEX ... ON table_name(...)
        for table in table_list:
            if f" ON {table}(" in idx_sql or f" ON {table} (" in idx_sql:
                try:
                    dst_cursor.execute(idx_sql)
                except sqlite3.OperationalError:
                    pass  # 이미 존재
                break


def create_common_db():
    """공통 DB 생성 — 매장 무관 전역 참조 데이터"""
    print(f"\n--- 공통 DB 생성: {COMMON_DB} ---")

    if COMMON_DB.exists():
        COMMON_DB.unlink()
        print("  기존 common.db 삭제")

    src_conn = sqlite3.connect(str(LEGACY_DB))
    dst_conn = sqlite3.connect(str(COMMON_DB))

    total_rows = 0

    try:
        src_cursor = src_conn.cursor()
        dst_cursor = dst_conn.cursor()

        for table in COMMON_TABLES:
            if not _table_exists(src_cursor, table):
                print(f"  [SKIP] {table}: 기존 DB에 없음")
                continue

            # CREATE TABLE 스키마 복사
            src_cursor.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,)
            )
            create_sql = src_cursor.fetchone()[0]
            dst_cursor.execute(f"DROP TABLE IF EXISTS {table}")
            dst_cursor.execute(create_sql)

            # 데이터 전체 복사 (공통 테이블은 필터링 없음)
            src_cursor.execute(f"SELECT * FROM {table}")
            rows = src_cursor.fetchall()

            if rows:
                placeholders = ",".join(["?" for _ in rows[0]])
                dst_cursor.executemany(
                    f"INSERT INTO {table} VALUES ({placeholders})", rows
                )

            total_rows += len(rows)
            print(f"  [OK] {table}: {len(rows):,}행 복사")

        # 인덱스 복사
        _copy_indexes(src_cursor, dst_cursor, COMMON_TABLES)

        dst_conn.commit()
        size_kb = COMMON_DB.stat().st_size / 1024
        print(f"  총 {total_rows:,}행, 크기: {size_kb:.1f} KB")

    finally:
        src_conn.close()
        dst_conn.close()


def create_store_db(store_id: str):
    """매장별 DB 생성 — store_id로 필터링하여 데이터 분배"""
    store_db_path = STORE_DB_DIR / f"{store_id}.db"
    print(f"\n--- 매장 DB 생성: {store_db_path} (store_id={store_id}) ---")

    STORE_DB_DIR.mkdir(parents=True, exist_ok=True)

    if store_db_path.exists():
        store_db_path.unlink()
        print("  기존 DB 삭제")

    src_conn = sqlite3.connect(str(LEGACY_DB))
    dst_conn = sqlite3.connect(str(store_db_path))

    total_rows = 0

    try:
        src_cursor = src_conn.cursor()
        dst_cursor = dst_conn.cursor()

        for table in STORE_TABLES:
            if not _table_exists(src_cursor, table):
                print(f"  [SKIP] {table}: 기존 DB에 없음")
                continue

            # CREATE TABLE 스키마 복사 (store_id 컬럼 포함한 기존 스키마 그대로)
            src_cursor.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,)
            )
            create_sql = src_cursor.fetchone()[0]
            dst_cursor.execute(f"DROP TABLE IF EXISTS {table}")
            dst_cursor.execute(create_sql)

            # store_id로 필터링하여 데이터 복사
            columns = _get_columns(src_cursor, table)

            if 'store_id' in columns:
                # store_id가 NULL인 행도 포함 (초기 데이터)
                src_cursor.execute(
                    f"SELECT * FROM {table} WHERE store_id = ? OR store_id IS NULL",
                    (store_id,)
                )
            else:
                # store_id 컬럼 없는 테이블은 전체 복사
                src_cursor.execute(f"SELECT * FROM {table}")

            rows = src_cursor.fetchall()

            if rows:
                placeholders = ",".join(["?" for _ in rows[0]])
                dst_cursor.executemany(
                    f"INSERT INTO {table} VALUES ({placeholders})", rows
                )

            total_rows += len(rows)
            print(f"  [OK] {table}: {len(rows):,}행 복사")

        # 인덱스 복사
        _copy_indexes(src_cursor, dst_cursor, STORE_TABLES)

        dst_conn.commit()
        size_kb = store_db_path.stat().st_size / 1024
        print(f"  총 {total_rows:,}행, 크기: {size_kb:.1f} KB")

    finally:
        src_conn.close()
        dst_conn.close()


def verify_split():
    """분할 결과 검증 — 원본과 분할된 DB의 행 수 비교"""
    print("\n" + "=" * 60)
    print("DB 분할 검증")
    print("=" * 60)

    if not LEGACY_DB.exists():
        print("[WARN] 기존 DB 없음 — 검증 스킵")
        return True

    if not COMMON_DB.exists():
        print("[FAIL] common.db 없음")
        return False

    legacy_conn = sqlite3.connect(str(LEGACY_DB))
    common_conn = sqlite3.connect(str(COMMON_DB))

    all_ok = True
    mismatch_details = []

    try:
        legacy_cursor = legacy_conn.cursor()
        common_cursor = common_conn.cursor()

        # ── 공통 테이블 검증 ──
        print("\n[공통 DB 검증]")
        for table in COMMON_TABLES:
            if not _table_exists(legacy_cursor, table):
                print(f"  {table}: 원본에 없음 (SKIP)")
                continue

            legacy_cursor.execute(f"SELECT COUNT(*) FROM {table}")
            legacy_count = legacy_cursor.fetchone()[0]

            if not _table_exists(common_cursor, table):
                print(f"  {table}: common.db에 없음 [FAIL]")
                all_ok = False
                continue

            common_cursor.execute(f"SELECT COUNT(*) FROM {table}")
            common_count = common_cursor.fetchone()[0]

            status = "OK" if legacy_count == common_count else "MISMATCH"
            if status == "MISMATCH":
                all_ok = False
                mismatch_details.append(
                    f"  공통/{table}: 원본={legacy_count}, common={common_count}"
                )
            print(f"  {table}: 원본={legacy_count:,}, common={common_count:,} [{status}]")

        # ── 매장별 테이블 검증 ──
        store_dbs = sorted(STORE_DB_DIR.glob("*.db"))
        if not store_dbs:
            print("\n[WARN] 매장 DB 없음")
            return all_ok

        # 매장 ID 목록 (검증용)
        all_store_ids = [p.stem for p in store_dbs]
        print(f"\n[매장별 DB 검증] 매장: {all_store_ids}")

        for store_db_path in store_dbs:
            store_id = store_db_path.stem
            store_conn = sqlite3.connect(str(store_db_path))
            store_cursor = store_conn.cursor()

            print(f"\n  ── 매장 {store_id} ({store_db_path.stat().st_size / 1024:.0f} KB) ──")

            for table in STORE_TABLES:
                if not _table_exists(legacy_cursor, table):
                    continue

                # 원본에서 해당 store_id 행 수
                columns = _get_columns(legacy_cursor, table)

                if 'store_id' in columns:
                    legacy_cursor.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE store_id = ? OR store_id IS NULL",
                        (store_id,)
                    )
                else:
                    legacy_cursor.execute(f"SELECT COUNT(*) FROM {table}")
                legacy_count = legacy_cursor.fetchone()[0]

                if not _table_exists(store_cursor, table):
                    print(f"    {table}: 매장 DB에 없음 [FAIL]")
                    all_ok = False
                    continue

                store_cursor.execute(f"SELECT COUNT(*) FROM {table}")
                store_count = store_cursor.fetchone()[0]

                status = "OK" if legacy_count == store_count else "MISMATCH"
                if status == "MISMATCH":
                    all_ok = False
                    mismatch_details.append(
                        f"  매장/{store_id}/{table}: 원본={legacy_count}, 매장={store_count}"
                    )
                print(f"    {table}: 원본={legacy_count:,}, 매장={store_count:,} [{status}]")

            store_conn.close()

        # ── 교차 검증: 매장별 합계 = 원본 전체 ──
        print(f"\n[교차 검증] 매장별 합계 vs 원본 전체")
        for table in STORE_TABLES:
            if not _table_exists(legacy_cursor, table):
                continue

            columns = _get_columns(legacy_cursor, table)
            if 'store_id' not in columns:
                continue  # store_id 없는 테이블은 교차검증 불가

            legacy_cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE store_id IS NOT NULL")
            legacy_total = legacy_cursor.fetchone()[0]

            store_sum = 0
            for store_db_path in store_dbs:
                s_conn = sqlite3.connect(str(store_db_path))
                s_cursor = s_conn.cursor()
                if _table_exists(s_cursor, table):
                    s_cursor.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE store_id IS NOT NULL"
                    )
                    store_sum += s_cursor.fetchone()[0]
                s_conn.close()

            # NULL store_id 행은 모든 매장에 복사되므로 합계가 클 수 있음
            legacy_cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE store_id IS NULL")
            null_count = legacy_cursor.fetchone()[0]

            if null_count > 0:
                expected_max = legacy_total + null_count * len(store_dbs)
                status = "OK (NULL 행 포함)" if store_sum <= expected_max else "MISMATCH"
            else:
                status = "OK" if legacy_total == store_sum else "MISMATCH"

            if "MISMATCH" in status:
                all_ok = False
            print(f"  {table}: 원본(non-null)={legacy_total:,}, 합계={store_sum:,}, null행={null_count:,} [{status}]")

        # ── 결과 요약 ──
        print(f"\n{'=' * 60}")
        if all_ok:
            print("검증 결과: ✓ PASS — 모든 데이터 무결성 확인")
        else:
            print("검증 결과: ✗ FAIL — 불일치 발견:")
            for detail in mismatch_details:
                print(detail)
        print("=" * 60)

        return all_ok

    finally:
        legacy_conn.close()
        common_conn.close()


def main():
    parser = argparse.ArgumentParser(description="BGF DB 분할 마이그레이션")
    parser.add_argument("--verify", action="store_true", help="분할 결과만 검증")
    parser.add_argument("--dry-run", action="store_true", help="시뮬레이션 (실제 분할하지 않음)")
    args = parser.parse_args()

    if args.verify:
        ok = verify_split()
        sys.exit(0 if ok else 1)

    if not LEGACY_DB.exists():
        print(f"[ERROR] 기존 DB가 없습니다: {LEGACY_DB}")
        sys.exit(1)

    print("=" * 60)
    print("BGF DB 분할 마이그레이션")
    print("=" * 60)
    size_mb = LEGACY_DB.stat().st_size / 1024 / 1024
    print(f"원본: {LEGACY_DB} ({size_mb:.1f} MB)")

    if args.dry_run:
        print("\n[DRY-RUN] 시뮬레이션 모드 — 실제 분할하지 않습니다")
        store_ids = get_store_ids_from_db()
        print(f"분할 대상: common.db + {len(store_ids)}개 매장 DB ({', '.join(store_ids)})")

        # 예상 행 수 출력
        conn = sqlite3.connect(str(LEGACY_DB))
        cursor = conn.cursor()
        print(f"\n공통 테이블:")
        for table in COMMON_TABLES:
            if _table_exists(cursor, table):
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                print(f"  {table}: {cursor.fetchone()[0]:,}행")
        print(f"\n매장별 테이블:")
        for table in STORE_TABLES:
            if _table_exists(cursor, table):
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                total = cursor.fetchone()[0]
                columns = _get_columns(cursor, table)
                if 'store_id' in columns:
                    for sid in store_ids:
                        cursor.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE store_id = ? OR store_id IS NULL",
                            (sid,)
                        )
                        print(f"  {table} [{sid}]: {cursor.fetchone()[0]:,}행 (전체 {total:,})")
                else:
                    print(f"  {table}: {total:,}행 (store_id 없음, 전체 복사)")
        conn.close()
        return

    # 1. 백업
    print("\n[1/4] 기존 DB 백업...")
    if not backup_legacy_db():
        sys.exit(1)

    # 2. store_id 목록 추출
    print("\n[2/4] 매장 목록 추출...")
    store_ids = get_store_ids_from_db()
    if not store_ids:
        print("[ERROR] store_id를 찾을 수 없습니다")
        sys.exit(1)

    # 3. 공통 DB 생성
    print("\n[3/4] 공통 DB 생성...")
    create_common_db()

    # 4. 매장별 DB 생성
    print(f"\n[4/4] 매장별 DB 생성 ({len(store_ids)}개)...")
    for store_id in store_ids:
        create_store_db(store_id)

    # 검증
    print("\n[검증 단계]")
    ok = verify_split()

    if ok:
        print("\n✓ 분할 완료! 파일 목록:")
        print(f"  공통: {COMMON_DB} ({COMMON_DB.stat().st_size / 1024:.1f} KB)")
        for store_id in store_ids:
            p = STORE_DB_DIR / f"{store_id}.db"
            print(f"  매장 {store_id}: {p} ({p.stat().st_size / 1024:.1f} KB)")
        print(f"\n기존 DB는 유지됩니다: {LEGACY_DB}")
        print("Phase 9에서 data/archive/로 이동 예정")
    else:
        print("\n✗ 분할 중 문제 발견. 위의 MISMATCH 항목을 확인하세요.")
        sys.exit(1)


if __name__ == "__main__":
    main()
