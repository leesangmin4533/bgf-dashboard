"""
Schema v24 마이그레이션 실행 스크립트

realtime_inventory에 store_id 추가 (멀티 스토어 지원)

Usage:
    python scripts/run_migration_v24.py
"""

import sys
import io
from pathlib import Path

# Windows CP949 콘솔 → UTF-8 래핑 (한글·특수문자 깨짐 방지)
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.db.models import init_db


def main():
    print("=" * 80)
    print("Schema v24 마이그레이션 실행")
    print("=" * 80)
    print("\n변경 사항:")
    print("  - realtime_inventory에 store_id 컬럼 추가")
    print("  - UNIQUE(item_cd) → UNIQUE(store_id, item_cd)")
    print("  - 기존 데이터: store_id='46513'으로 설정")
    print("\n" + "-" * 80)

    try:
        print("\n🔧 마이그레이션 실행 중...")
        init_db()
        print("\n✅ 마이그레이션 완료!")

        # 검증
        import sqlite3
        db_path = project_root / "data" / "bgf_sales.db"
        conn = sqlite3.connect(db_path)
        c = conn.cursor()

        # 스키마 버전 확인
        version = c.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
        print(f"\n📊 현재 스키마 버전: {version[0]}")

        # realtime_inventory 구조 확인
        columns = c.execute("PRAGMA table_info(realtime_inventory)").fetchall()
        print("\n📋 realtime_inventory 테이블 구조:")
        for col in columns:
            print(f"  - {col[1]}: {col[2]}")

        # 인덱스 확인
        indexes = c.execute("""
            SELECT name FROM sqlite_master
            WHERE type='index' AND tbl_name='realtime_inventory'
        """).fetchall()
        print("\n🔍 인덱스:")
        for idx in indexes:
            print(f"  - {idx[0]}")

        # 데이터 샘플 확인
        sample = c.execute("""
            SELECT store_id, item_cd, stock_qty, pending_qty
            FROM realtime_inventory
            LIMIT 3
        """).fetchall()
        print("\n📦 데이터 샘플:")
        for row in sample:
            print(f"  - store_id={row[0]}, item_cd={row[1]}, stock={row[2]}, pending={row[3]}")

        conn.close()

        print("\n" + "=" * 80)
        print("✅ 마이그레이션 및 검증 완료")
        print("=" * 80)

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
