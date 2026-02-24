"""
음수 재고 데이터 일괄 정정 스크립트

Priority 2.1: 음수 재고/미입고를 0으로 초기화하여 과다 예측 방지

Usage:
    python scripts/fix_negative_inventory.py          # 조회만 (Dry-run)
    python scripts/fix_negative_inventory.py --fix    # 실제 정정
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

# Windows 콘솔 UTF-8 설정
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


def get_db_path() -> str:
    """DB 경로 반환"""
    return str(Path(__file__).parent.parent / "data" / "bgf_sales.db")


def fix_negative_inventory(dry_run: bool = True) -> None:
    """음수 재고 데이터 정정

    Args:
        dry_run: True면 조회만, False면 실제 정정
    """
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    print("=" * 80)
    print("🔍 음수 재고/미입고 데이터 조회")
    print("=" * 80)

    # 음수 재고 조회
    negative_stock = c.execute("""
        SELECT item_cd, item_nm, stock_qty, pending_qty, queried_at
        FROM realtime_inventory
        WHERE stock_qty < 0 OR pending_qty < 0
        ORDER BY stock_qty ASC, pending_qty ASC
    """).fetchall()

    if not negative_stock:
        print("\n✅ 음수 재고/미입고 데이터 없음 (정상)")
        conn.close()
        return

    print(f"\n⚠️  음수 재고/미입고 상품: {len(negative_stock)}건\n")

    stock_count = 0
    pending_count = 0

    for item_cd, item_nm, stock, pending, queried_at in negative_stock:
        issues = []
        if stock < 0:
            issues.append(f"재고={stock}")
            stock_count += 1
        if pending < 0:
            issues.append(f"미입고={pending}")
            pending_count += 1

        print(f"  [{item_cd}] {item_nm}")
        print(f"    문제: {', '.join(issues)}")
        print(f"    조회시각: {queried_at}")
        print()

    print("-" * 80)
    print(f"📊 요약:")
    print(f"  - 음수 재고: {stock_count}건")
    print(f"  - 음수 미입고: {pending_count}건")
    print(f"  - 총 영향 상품: {len(negative_stock)}건")
    print("-" * 80)

    if dry_run:
        print("\n💡 Dry-run 모드: 데이터 변경 없음")
        print("   실제 정정하려면 --fix 옵션 사용:")
        print("   python scripts/fix_negative_inventory.py --fix")
        conn.close()
        return

    # 실제 정정
    print("\n🔧 음수 재고/미입고 정정 중...")

    # 음수 재고 → 0
    c.execute("""
        UPDATE realtime_inventory
        SET stock_qty = 0
        WHERE stock_qty < 0
    """)
    updated_stock = c.rowcount

    # 음수 미입고 → 0
    c.execute("""
        UPDATE realtime_inventory
        SET pending_qty = 0
        WHERE pending_qty < 0
    """)
    updated_pending = c.rowcount

    conn.commit()

    print(f"✅ 정정 완료:")
    print(f"  - 재고 초기화: {updated_stock}건")
    print(f"  - 미입고 초기화: {updated_pending}건")
    print(f"  - 정정 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 정정 후 확인
    remaining = c.execute("""
        SELECT COUNT(*)
        FROM realtime_inventory
        WHERE stock_qty < 0 OR pending_qty < 0
    """).fetchone()[0]

    if remaining == 0:
        print(f"\n✅ 모든 음수 재고/미입고 정정됨 (잔여: 0건)")
    else:
        print(f"\n⚠️  주의: 잔여 음수 데이터 {remaining}건 (재확인 필요)")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="음수 재고 데이터 정정")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="실제 정정 (기본: Dry-run)"
    )

    args = parser.parse_args()

    fix_negative_inventory(dry_run=not args.fix)


if __name__ == "__main__":
    main()
