#!/usr/bin/env python3
"""Fix 4: expiry_time 빈값 재계산 스크립트"""
import sqlite3
import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMON_DB = os.path.join(BASE_DIR, "data", "common.db")
STORES = ["46513", "46704"]


def fix_store(store_id: str):
    db_path = os.path.join(BASE_DIR, "data", "stores", f"{store_id}.db")
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # ATTACH common.db
    c.execute(f"ATTACH DATABASE ? AS common", (COMMON_DB,))

    # Step 0: 현재 상태
    c.execute("""
        SELECT
            SUM(CASE WHEN (expiry_time IS NULL OR expiry_time = '')
                      AND (arrival_time IS NOT NULL AND arrival_time != '') THEN 1 ELSE 0 END) as fixable,
            SUM(CASE WHEN (expiry_time IS NULL OR expiry_time = '')
                      AND (arrival_time IS NULL OR arrival_time = '') THEN 1 ELSE 0 END) as unfixable,
            SUM(CASE WHEN expiry_time IS NOT NULL AND expiry_time != '' THEN 1 ELSE 0 END) as already_ok,
            COUNT(*) as total
        FROM order_tracking
    """)
    fixable, unfixable, ok, total = c.fetchone()
    print(f"[{store_id}] Before: fixable={fixable}, unfixable={unfixable}, ok={ok}, total={total}")

    # Step 1: 비푸드류 — arrival_time + expiration_days
    c.execute("""
        UPDATE order_tracking
        SET expiry_time = datetime(arrival_time,
            '+' || (SELECT pd.expiration_days FROM common.product_details pd
                    WHERE pd.item_cd = order_tracking.item_cd) || ' days')
        WHERE (expiry_time IS NULL OR expiry_time = '')
          AND (arrival_time IS NOT NULL AND arrival_time != '')
          AND mid_cd NOT IN ('001','002','003','004','005','012')
          AND EXISTS (
            SELECT 1 FROM common.product_details pd
            WHERE pd.item_cd = order_tracking.item_cd
              AND pd.expiration_days IS NOT NULL AND pd.expiration_days > 0
          )
    """)
    non_food = c.rowcount
    print(f"[{store_id}] Step 1 (non-food): {non_food} rows fixed")

    # Step 2: 푸드류 — TTL 로직 (mid_cd 기반)
    # 012 빵: arrival_date + (expiration_days+1) 일
    # 001~005: 오후입고(1차) +30h, 오전입고(2차) +31h
    c.execute("""
        UPDATE order_tracking
        SET expiry_time = CASE
            WHEN mid_cd = '012' THEN
                datetime(arrival_time,
                    '+' || COALESCE(
                        (SELECT pd.expiration_days + 1
                         FROM common.product_details pd
                         WHERE pd.item_cd = order_tracking.item_cd
                           AND pd.expiration_days > 0),
                        4
                    ) || ' days')
            WHEN CAST(strftime('%H', arrival_time) AS INTEGER) >= 12 THEN
                datetime(arrival_time, '+30 hours')
            ELSE
                datetime(arrival_time, '+31 hours')
        END
        WHERE (expiry_time IS NULL OR expiry_time = '')
          AND (arrival_time IS NOT NULL AND arrival_time != '')
          AND mid_cd IN ('001','002','003','004','005','012')
    """)
    food = c.rowcount
    print(f"[{store_id}] Step 2 (food TTL): {food} rows fixed")

    conn.commit()

    # Step 3: 검증
    c.execute("""
        SELECT
            SUM(CASE WHEN expiry_time IS NULL OR expiry_time = '' THEN 1 ELSE 0 END) as still_empty,
            SUM(CASE WHEN expiry_time IS NOT NULL AND expiry_time != '' THEN 1 ELSE 0 END) as has_value,
            COUNT(*) as total
        FROM order_tracking
    """)
    empty, has, total = c.fetchone()
    print(f"[{store_id}] After: still_empty={empty}, has_value={has}, total={total}")

    c.execute("DETACH DATABASE common")
    conn.close()
    print()


if __name__ == "__main__":
    for store in STORES:
        fix_store(store)
    print("Fix 4 COMPLETE")
