"""기존 누락 신제품 소급 등록

receiving_history에 입고 기록이 있지만 detected_new_products에 없는 상품을
store DB + common.db 양쪽에 등록합니다.

Usage:
    python scripts/migrate_missing_new_products.py --dry-run  # 미리보기
    python scripts/migrate_missing_new_products.py            # 실행
"""

import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.infrastructure.database.connection import DBRouter
from src.infrastructure.database.repos import DetectedNewProductRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)

STORE_IDS = ["46513", "46704", "47863", "49965"]
DAYS_LIMIT = 30


def find_missing_new_products(store_id: str, days_limit: int) -> list:
    """receiving_history에 있지만 detected_new_products에 없는 상품 찾기"""
    conn = DBRouter.get_store_connection(store_id)
    try:
        conn.row_factory = __import__("sqlite3").Row
        cursor = conn.cursor()

        cutoff_date = (datetime.now() - timedelta(days=days_limit)).strftime("%Y-%m-%d")

        # common.db ATTACH (products 테이블 참조)
        import pathlib
        base_dir = pathlib.Path(__file__).resolve().parent.parent / "data"
        common_path = str(base_dir / "common.db").replace("\\", "/")
        cursor.execute(f"ATTACH DATABASE '{common_path}' AS common")

        # receiving_history에서 최초 입고일 + detected_new_products에 없는 것
        # + products.created_at이 30일 이내 (기존 상품 제외)
        cursor.execute(
            """
            SELECT
                rh.item_cd,
                rh.item_nm,
                rh.mid_cd,
                MIN(rh.receiving_date) as first_receiving_date,
                SUM(rh.receiving_qty) as total_receiving_qty,
                rh.order_qty,
                rh.center_cd,
                rh.center_nm
            FROM receiving_history rh
            LEFT JOIN detected_new_products dnp
                ON rh.item_cd = dnp.item_cd AND dnp.store_id = ?
            INNER JOIN common.products p
                ON rh.item_cd = p.item_cd
            WHERE dnp.item_cd IS NULL
              AND rh.store_id = ?
              AND rh.receiving_date >= ?
              AND p.created_at >= ?
            GROUP BY rh.item_cd
            ORDER BY first_receiving_date DESC
            """,
            (store_id, store_id, cutoff_date, cutoff_date),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def register_missing(store_id: str, missing: list, dry_run: bool) -> int:
    """누락 상품 등록"""
    count = 0
    repo = DetectedNewProductRepository(store_id=store_id)

    for item in missing:
        item_cd = item["item_cd"]
        item_nm = item.get("item_nm", "")
        mid_cd = item.get("mid_cd", "")
        first_date = item["first_receiving_date"]
        recv_qty = item.get("total_receiving_qty", 0)
        center_cd = item.get("center_cd")
        center_nm = item.get("center_nm")

        if dry_run:
            print(f"  [DRY-RUN] {item_cd} ({item_nm}) mid={mid_cd} "
                  f"first={first_date} qty={recv_qty}")
            count += 1
            continue

        # store DB 등록
        try:
            repo.save(
                item_cd=item_cd,
                item_nm=item_nm,
                mid_cd=mid_cd,
                mid_cd_source="migration",
                first_receiving_date=first_date,
                receiving_qty=recv_qty or 0,
                order_unit_qty=1,
                center_cd=center_cd,
                center_nm=center_nm,
                registered_to_products=True,
                registered_to_details=True,
                registered_to_inventory=True,
                store_id=store_id,
            )
        except Exception as e:
            logger.warning(f"  store DB 등록 실패 ({item_cd}): {e}")
            continue

        # common.db 등록
        try:
            repo.save_to_common(
                item_cd=item_cd,
                item_nm=item_nm,
                mid_cd=mid_cd,
                mid_cd_source="migration",
                first_receiving_date=first_date,
                receiving_qty=recv_qty or 0,
                order_unit_qty=1,
                center_cd=center_cd,
                center_nm=center_nm,
                store_id=store_id,
            )
        except Exception as e:
            logger.warning(f"  common.db 등록 실패 ({item_cd}): {e}")

        count += 1
        logger.info(f"  등록 완료: {item_cd} ({item_nm})")

    return count


def main():
    parser = argparse.ArgumentParser(description="누락 신제품 소급 등록")
    parser.add_argument("--dry-run", action="store_true", help="미리보기만")
    parser.add_argument("--days", type=int, default=DAYS_LIMIT, help=f"기간 제한 (기본 {DAYS_LIMIT}일)")
    parser.add_argument("--store", type=str, help="특정 매장만 (예: 46513)")
    args = parser.parse_args()

    stores = [args.store] if args.store else STORE_IDS
    total = 0

    print(f"{'[DRY-RUN] ' if args.dry_run else ''}누락 신제품 소급 등록")
    print(f"기간 제한: {args.days}일 이내 첫 입고")
    print(f"대상 매장: {stores}")
    print("=" * 60)

    for store_id in stores:
        print(f"\n[매장 {store_id}]")
        missing = find_missing_new_products(store_id, args.days)
        print(f"  누락 상품: {len(missing)}건")

        if missing:
            registered = register_missing(store_id, missing, args.dry_run)
            total += registered
            print(f"  등록: {registered}건")

    print(f"\n{'=' * 60}")
    print(f"총 {'미리보기' if args.dry_run else '등록'}: {total}건")


if __name__ == "__main__":
    main()
