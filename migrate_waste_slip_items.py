"""
migrate_waste_slip_items.py
───────────────────────────
47863 매장 DB의 waste_slip_items 테이블을 현행 스키마(schema.py:823 기준)로 마이그레이션.

변경 내용 (47863 구버전 → 현행):
  - wonga_price, maega_price, cust_nm, center_nm, updated_at 컬럼 추가
  - wonga_amt, maega_amt 타입 INTEGER → REAL
  - item_cd NOT NULL 적용
  - UNIQUE(store_id, chit_date, chit_no, item_cd) 제약 추가
  - created_at NOT NULL + DEFAULT 명시

실행:
  python migrate_waste_slip_items.py
  python migrate_waste_slip_items.py --store-id 47863   # 특정 매장만
  python migrate_waste_slip_items.py --dry-run           # 시뮬레이션만
  python migrate_waste_slip_items.py --all               # stores/ 폴더 전체
"""

import sqlite3
import shutil
import argparse
import logging
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── 경로 설정 ───────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DB_DIR   = BASE_DIR / "data" / "stores"

# 마이그레이션 대상 매장 (구버전 스키마가 확인된 곳)
TARGET_STORES = ["47863"]

# ── 현행 스키마 (schema.py:823 기준, 46513 DB와 동일) ────────
NEW_SCHEMA = """
CREATE TABLE waste_slip_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id    TEXT    NOT NULL,
    chit_date   TEXT    NOT NULL,
    chit_no     TEXT    NOT NULL,
    chit_seq    INTEGER,
    item_cd     TEXT    NOT NULL,
    item_nm     TEXT,
    large_cd    TEXT,
    large_nm    TEXT,
    qty         INTEGER DEFAULT 0,
    wonga_price REAL    DEFAULT 0,
    wonga_amt   REAL    DEFAULT 0,
    maega_price REAL    DEFAULT 0,
    maega_amt   REAL    DEFAULT 0,
    cust_nm     TEXT,
    center_nm   TEXT,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT,
    UNIQUE (store_id, chit_date, chit_no, item_cd)
)
"""

# 현행 스키마 컬럼 (id 제외, 복사 대상)
NEW_COLS = [
    "store_id", "chit_date", "chit_no", "chit_seq",
    "item_cd", "item_nm", "large_cd", "large_nm",
    "qty", "wonga_price", "wonga_amt", "maega_price", "maega_amt",
    "cust_nm", "center_nm", "created_at", "updated_at",
]


# ── 유틸리티 ────────────────────────────────────────────────

def get_existing_columns(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute("PRAGMA table_info(waste_slip_items)")
    return [row[1] for row in cur.fetchall()]


def table_exists(conn: sqlite3.Connection) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='waste_slip_items'"
    )
    return cur.fetchone() is not None


def backup_db(db_path: Path) -> Path:
    ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db_path.with_name(f"{db_path.stem}_backup_{ts}.db")
    shutil.copy2(db_path, backup)
    log.info(f"백업 완료: {backup.name}")
    return backup


def needs_migration(conn: sqlite3.Connection) -> dict:
    """마이그레이션 필요 여부 + 상세 사유 반환"""
    if not table_exists(conn):
        return {"needed": True, "reason": "테이블 없음"}

    existing = get_existing_columns(conn)
    issues = []

    # 누락 컬럼
    missing = [c for c in NEW_COLS if c not in existing]
    if missing:
        issues.append(f"누락 컬럼: {missing}")

    # NOT NULL / UNIQUE 확인 (PRAGMA로는 한계 있으므로 CREATE SQL 비교)
    cur = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='waste_slip_items'"
    )
    row = cur.fetchone()
    if row:
        create_sql = row[0] or ""
        if "UNIQUE" not in create_sql.upper():
            issues.append("UNIQUE 제약 없음")
        # item_cd NOT NULL 확인
        if "item_cd TEXT," in create_sql or "item_cd TEXT\n" in create_sql:
            # NOT NULL 키워드 없는 경우
            if "item_cd TEXT NOT NULL" not in create_sql.upper().replace("  ", " "):
                issues.append("item_cd NOT NULL 없음")

    if not issues:
        return {"needed": False, "reason": "이미 현행 스키마"}

    return {"needed": True, "reason": "; ".join(issues)}


# ── 마이그레이션 ────────────────────────────────────────────

def migrate_store(db_path: Path, dry_run: bool = False) -> bool:
    store_id = db_path.stem
    log.info(f"[{store_id}] 마이그레이션 시작: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")

    try:
        # 테이블 없으면 신규 생성
        if not table_exists(conn):
            log.info(f"[{store_id}] waste_slip_items 테이블 없음 → 신규 생성")
            if not dry_run:
                conn.execute(NEW_SCHEMA)
                conn.commit()
            return True

        # 마이그레이션 필요 여부 확인
        status = needs_migration(conn)
        if not status["needed"]:
            log.info(f"[{store_id}] 이미 현행 스키마 → 건너뜀")
            return True

        existing_cols = get_existing_columns(conn)
        log.info(f"[{store_id}] 현재 컬럼 ({len(existing_cols)}개): {existing_cols}")
        log.info(f"[{store_id}] 마이그레이션 사유: {status['reason']}")

        if dry_run:
            log.info(f"[{store_id}] (dry-run) 테이블 재생성 예정")
            return True

        # ── 테이블 재생성 (rename → create → copy → drop) ────
        conn.execute("BEGIN EXCLUSIVE")

        # 1. 기존 테이블 임시 이름
        conn.execute("ALTER TABLE waste_slip_items RENAME TO _wsi_old")

        # 2. 현행 스키마로 신규 생성
        conn.execute(NEW_SCHEMA)

        # 3. 데이터 복사 — old 컬럼과 new 컬럼의 교집합 + 변환
        #    old에 없는 컬럼은 DEFAULT 적용 (INSERT에서 생략)
        common_cols = [c for c in NEW_COLS if c in existing_cols]

        # SELECT 절: 타입 변환 + NOT NULL 보정
        select_exprs = []
        for col in common_cols:
            if col == "item_cd":
                select_exprs.append("COALESCE(item_cd, 'UNKNOWN')")
            elif col == "created_at":
                select_exprs.append("COALESCE(created_at, datetime('now', 'localtime'))")
            elif col in ("wonga_amt", "maega_amt"):
                # INTEGER → REAL 변환
                select_exprs.append(f"CAST({col} AS REAL)")
            else:
                select_exprs.append(col)

        insert_cols_str = ", ".join(common_cols)
        select_cols_str = ", ".join(select_exprs)

        sql = (
            f"INSERT OR IGNORE INTO waste_slip_items ({insert_cols_str}) "
            f"SELECT {select_cols_str} FROM _wsi_old"
        )
        log.info(f"  복사 SQL: {sql[:200]}...")
        conn.execute(sql)

        rows_old    = conn.execute("SELECT COUNT(*) FROM _wsi_old").fetchone()[0]
        rows_copied = conn.execute("SELECT COUNT(*) FROM waste_slip_items").fetchone()[0]
        log.info(f"  데이터 이전: {rows_old}건 → {rows_copied}건")
        if rows_old != rows_copied:
            log.info(f"  UNIQUE 중복 제거: {rows_old - rows_copied}건")

        # 4. 구 테이블 삭제
        conn.execute("DROP TABLE _wsi_old")

        # 5. 인덱스 재생성
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wsi_store_date "
            "ON waste_slip_items(store_id, chit_date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wsi_item "
            "ON waste_slip_items(item_cd)"
        )

        conn.execute("COMMIT")

        log.info(f"[{store_id}] 마이그레이션 완료")
        return True

    except Exception as e:
        log.error(f"[{store_id}] 마이그레이션 실패: {e}")
        try:
            conn.execute("ROLLBACK")
            # 롤백 후 old 테이블 복원 시도
            if conn.execute(
                "SELECT name FROM sqlite_master WHERE name='_wsi_old'"
            ).fetchone():
                conn.execute("DROP TABLE IF EXISTS waste_slip_items")
                conn.execute("ALTER TABLE _wsi_old RENAME TO waste_slip_items")
                conn.commit()
                log.info(f"[{store_id}] 롤백 복원 완료")
        except Exception as re:
            log.error(f"[{store_id}] 롤백 실패: {re}")
        return False

    finally:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.close()


def verify_schema(db_path: Path) -> bool:
    """마이그레이션 후 검증"""
    store_id = db_path.stem
    conn = sqlite3.connect(db_path)
    all_ok = True

    try:
        # 컬럼 확인
        cur  = conn.execute("PRAGMA table_info(waste_slip_items)")
        cols = {row[1]: {"type": row[2], "notnull": row[3], "default": row[4]}
                for row in cur.fetchall()}

        log.info(f"[{store_id}] 검증 결과:")

        for col in NEW_COLS:
            ok = col in cols
            if not ok:
                all_ok = False
            log.info(f"  {'OK' if ok else 'NG'} {col}")

        # UNIQUE 인덱스 확인
        cur = conn.execute("PRAGMA index_list(waste_slip_items)")
        has_unique = any(idx[2] == 1 for idx in cur.fetchall())
        if not has_unique:
            all_ok = False
        log.info(f"  {'OK' if has_unique else 'NG'} UNIQUE 제약")

        # item_cd NOT NULL
        item_notnull = cols.get("item_cd", {}).get("notnull", 0)
        if not item_notnull:
            all_ok = False
        log.info(f"  {'OK' if item_notnull else 'NG'} item_cd NOT NULL")

        # wonga_amt 타입
        wonga_type = cols.get("wonga_amt", {}).get("type", "")
        type_ok = "REAL" in wonga_type.upper()
        if not type_ok:
            all_ok = False
        log.info(f"  {'OK' if type_ok else 'NG'} wonga_amt REAL (현재: {wonga_type})")

        # 행수
        cnt = conn.execute("SELECT COUNT(*) FROM waste_slip_items").fetchone()[0]
        log.info(f"  총 {cnt}건")

    finally:
        conn.close()

    return all_ok


# ── 메인 ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="waste_slip_items 스키마 마이그레이션")
    parser.add_argument("--store-id", help="특정 매장만 (예: 47863)")
    parser.add_argument("--all",      action="store_true", help="stores/ 폴더 전체 스캔")
    parser.add_argument("--dry-run",  action="store_true", help="시뮬레이션만 (DB 변경 없음)")
    args = parser.parse_args()

    # 대상 결정
    if args.store_id:
        targets = [args.store_id]
    elif args.all:
        targets = [p.stem for p in DB_DIR.glob("*.db") if not p.stem.endswith("_backup")]
    else:
        targets = TARGET_STORES

    if args.dry_run:
        log.info("=== DRY-RUN 모드: 실제 변경 없음 ===")

    log.info(f"대상 매장: {targets}")

    results = {}
    for store_id in targets:
        db_path = DB_DIR / f"{store_id}.db"
        if not db_path.exists():
            log.error(f"[{store_id}] DB 파일 없음: {db_path}")
            results[store_id] = False
            continue

        # 마이그레이션 필요 여부 사전 체크
        conn = sqlite3.connect(db_path)
        status = needs_migration(conn)
        conn.close()

        if not status["needed"]:
            log.info(f"[{store_id}] {status['reason']} → 건너뜀")
            results[store_id] = True
            continue

        # 백업 후 마이그레이션
        if not args.dry_run:
            backup_db(db_path)

        ok = migrate_store(db_path, dry_run=args.dry_run)
        results[store_id] = ok

        if ok and not args.dry_run:
            verify_schema(db_path)

    # 결과 요약
    log.info("=== 완료 ===")
    for sid, ok in results.items():
        log.info(f"  {'OK' if ok else 'NG'} {sid}")


if __name__ == "__main__":
    main()
