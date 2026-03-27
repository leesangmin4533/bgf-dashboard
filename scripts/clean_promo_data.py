"""
오염된 행사(promo_type) 데이터 정리 스크립트

order_prep_collector.py가 gdList 컬럼 인덱스 11/12(ORD_UNIT/ORD_UNIT_QTY)를
행사 정보로 착각하여 '낱개','묶음','BOX','지함' 등 발주단위명을
promo_type으로 저장한 데이터를 정리합니다.

사용법:
    python scripts/clean_promo_data.py          # dry-run (미리보기)
    python scripts/clean_promo_data.py --execute # 실제 실행
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.infrastructure.database.connection import DBRouter

INVALID_VALUES = {'낱개', '묶음', 'BOX', '지함'}

# SQL: 무효 promo_type 조건 (발주단위명 + 순수 숫자)
INVALID_PROMO_SQL = """
    (promo_type IN ('낱개','묶음','BOX','지함')
     OR (promo_type IS NOT NULL
         AND promo_type != ''
         AND promo_type NOT LIKE '%+%'
         AND promo_type NOT IN ('할인','덤')
         AND CAST(promo_type AS INTEGER) > 0
         AND CAST(promo_type AS INTEGER) = promo_type))
"""


def is_invalid_promo(value: str) -> bool:
    if not value:
        return False
    if value in INVALID_VALUES:
        return True
    if value.isdigit():
        return True
    return False


def table_exists(cursor, table_name: str) -> bool:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cursor.fetchone() is not None


def show_distribution(cursor, table: str, column: str = 'promo_type', where: str = ''):
    """promo_type 분포를 출력하고 무효 건수 반환"""
    extra = f"WHERE {where}" if where else f"WHERE {column} IS NOT NULL AND {column} != ''"
    cursor.execute(f"SELECT {column}, COUNT(*) as cnt FROM {table} {extra} GROUP BY {column} ORDER BY cnt DESC")
    rows = cursor.fetchall()
    invalid_count = 0
    for row in rows:
        pt = row[0] or '(NULL)'
        cnt = row[1]
        inv = is_invalid_promo(row[0] or '')
        marker = " ** 무효" if inv else ""
        print(f"  {pt}: {cnt}건{marker}")
        if inv:
            invalid_count += cnt
    return invalid_count


def clean_store_db(store_id: str, execute: bool = False):
    conn = DBRouter.get_store_connection(store_id)
    cursor = conn.cursor()

    print(f"\n{'='*60}")
    print(f"매장 DB: {store_id}")
    print(f"{'='*60}")

    # 1. promotions
    if table_exists(cursor, 'promotions'):
        print(f"\n[promotions]")
        inv = show_distribution(cursor, 'promotions')
        if inv > 0:
            print(f"  -> 삭제 대상: {inv}건")
            if execute:
                cursor.execute(f"DELETE FROM promotions WHERE {INVALID_PROMO_SQL}")
                print(f"  -> {cursor.rowcount}건 삭제 완료")
    else:
        print("\n[promotions] 테이블 없음")

    # 2. promotion_changes
    if table_exists(cursor, 'promotion_changes'):
        print(f"\n[promotion_changes]")
        cursor.execute("""
            SELECT prev_promo_type, next_promo_type, COUNT(*) as cnt
            FROM promotion_changes
            GROUP BY prev_promo_type, next_promo_type
            ORDER BY cnt DESC
        """)
        rows = cursor.fetchall()
        inv_count = 0
        for row in rows:
            prev, nxt, cnt = row[0] or '(NULL)', row[1] or '(NULL)', row[2]
            both_inv = is_invalid_promo(row[0] or '') and is_invalid_promo(row[1] or '')
            marker = " ** 양쪽무효" if both_inv else ""
            print(f"  {prev} -> {nxt}: {cnt}건{marker}")
            if both_inv:
                inv_count += cnt
        if inv_count > 0:
            print(f"  -> 삭제 대상: {inv_count}건")
            if execute:
                cursor.execute("""
                    DELETE FROM promotion_changes
                    WHERE (prev_promo_type IN ('낱개','묶음','BOX','지함')
                           OR (prev_promo_type IS NOT NULL AND prev_promo_type != ''
                               AND CAST(prev_promo_type AS INTEGER) > 0
                               AND CAST(prev_promo_type AS INTEGER) = prev_promo_type))
                      AND (next_promo_type IN ('낱개','묶음','BOX','지함','')
                           OR next_promo_type IS NULL
                           OR (CAST(next_promo_type AS INTEGER) > 0
                               AND CAST(next_promo_type AS INTEGER) = next_promo_type))
                """)
                print(f"  -> {cursor.rowcount}건 삭제 완료")
    else:
        print("\n[promotion_changes] 테이블 없음")

    # 3. daily_sales.promo_type
    if table_exists(cursor, 'daily_sales'):
        print(f"\n[daily_sales.promo_type]")
        inv = show_distribution(cursor, 'daily_sales')
        if inv > 0:
            print(f"  -> NULL 처리 대상: {inv}건")
            if execute:
                cursor.execute(f"UPDATE daily_sales SET promo_type = NULL WHERE {INVALID_PROMO_SQL}")
                print(f"  -> {cursor.rowcount}건 NULL 처리 완료")
    else:
        print("\n[daily_sales] 테이블 없음")

    if execute:
        conn.commit()
    conn.close()


def clean_common_db(execute: bool = False):
    conn = DBRouter.get_common_connection()
    cursor = conn.cursor()

    print(f"\n{'='*60}")
    print(f"공통 DB (common.db)")
    print(f"{'='*60}")

    if table_exists(cursor, 'product_details'):
        print(f"\n[product_details.promo_type]")
        inv = show_distribution(cursor, 'product_details')
        if inv > 0:
            print(f"  -> NULL 처리 대상: {inv}건")
            if execute:
                cursor.execute(f"UPDATE product_details SET promo_type = NULL WHERE {INVALID_PROMO_SQL}")
                print(f"  -> {cursor.rowcount}건 NULL 처리 완료")
    else:
        print("\n[product_details] 테이블 없음")

    if execute:
        conn.commit()
    conn.close()


def main():
    execute = '--execute' in sys.argv

    mode = "실행 모드" if execute else "DRY-RUN 모드 (미리보기)"
    print(f"{'='*60}")
    print(f"{mode}")
    if not execute:
        print("실제 실행: python scripts/clean_promo_data.py --execute")
    print(f"{'='*60}")

    stores_dir = Path(__file__).parent.parent / "data" / "stores"
    for db_file in sorted(stores_dir.glob("*.db")):
        store_id = db_file.stem
        if store_id.isdigit():
            clean_store_db(store_id, execute)

    clean_common_db(execute)

    if not execute:
        print(f"\n{'='*60}")
        print("실행하려면: python scripts/clean_promo_data.py --execute")
        print(f"{'='*60}")
    else:
        print(f"\n{'='*60}")
        print("정리 완료!")
        print(f"{'='*60}")


if __name__ == '__main__':
    main()
