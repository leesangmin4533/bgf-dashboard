# -*- coding: utf-8 -*-
"""
realtime_inventory stock_qty 보정 패치

문제: realtime_inventory의 stock_qty가 실제 재고보다 과대 계상됨
원인:
  - 배치 만료 시 stock_qty가 차감되지 않았음
  - daily_sales가 수집 안 되는 날에도 재고가 그대로 유지됨
  - 유통기한 1일 상품(주먹밥 등)이 만료돼도 재고가 줄지 않음

보정 방법:
  1. 만료된 배치(expired) 처리 확인
  2. inventory_batches active 잔량 기준으로 stock_qty 하향 보정
  3. daily_sales 최근 stock_qty 기준으로 추가 보정
"""
import sys
import io

# Windows CP949 콘솔 → UTF-8 래핑 (한글·특수문자 깨짐 방지)
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

import os
import sqlite3
from pathlib import Path

# 프로젝트 루트 설정
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)

from src.utils.logger import get_logger

logger = get_logger("patch_realtime_inventory")


def patch_store(store_id: str):
    """매장 DB의 realtime_inventory stock_qty 보정"""
    db_path = f"data/stores/{store_id}.db"
    if not os.path.exists(db_path):
        print(f"  DB 없음: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print(f"\n{'='*60}")
    print(f"매장 {store_id} realtime_inventory 보정")
    print(f"{'='*60}")

    # 1. 먼저 만료 배치 처리 (혹시 미처리된 배치가 있을 수 있음)
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    cur.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(remaining_qty), 0)
        FROM inventory_batches
        WHERE status = 'active' AND expiry_date <= ? AND remaining_qty > 0
        """,
        (today,)
    )
    row = cur.fetchone()
    pending_expire_count = row[0]
    pending_expire_qty = row[1]

    if pending_expire_count > 0:
        print(f"\n[1단계] 미처리 만료 배치: {pending_expire_count}건, {pending_expire_qty}개")
        cur.execute(
            """
            UPDATE inventory_batches
            SET status = 'expired', updated_at = ?
            WHERE status = 'active' AND expiry_date <= ? AND remaining_qty > 0
            """,
            (datetime.now().isoformat(), today)
        )
        print(f"  -> {cur.rowcount}건 expired 처리 완료")
    else:
        print(f"\n[1단계] 미처리 만료 배치 없음")

    # 2. inventory_batches active 잔량 기준으로 realtime_inventory 보정
    cur.execute(
        """
        SELECT
            ri.store_id,
            ri.item_cd,
            ri.item_nm,
            ri.stock_qty as ri_stock,
            COALESCE(bs.active_total, 0) as batch_total
        FROM realtime_inventory ri
        LEFT JOIN (
            SELECT store_id, item_cd, SUM(remaining_qty) as active_total
            FROM inventory_batches
            WHERE status = 'active'
            GROUP BY store_id, item_cd
        ) bs ON bs.store_id = ri.store_id AND bs.item_cd = ri.item_cd
        WHERE ri.stock_qty > 0
        AND bs.active_total IS NOT NULL
        AND ri.stock_qty > COALESCE(bs.active_total, 0)
        ORDER BY (ri.stock_qty - COALESCE(bs.active_total, 0)) DESC
        """
    )
    overstock_rows = cur.fetchall()

    if overstock_rows:
        print(f"\n[2단계] 배치 기준 과대 재고 보정: {len(overstock_rows)}건")
        total_reduced = 0
        now = datetime.now().isoformat()
        for r in overstock_rows:
            diff = r['ri_stock'] - r['batch_total']
            nm = r['item_nm'] or r['item_cd']
            if diff >= 2:  # 차이가 큰 것만 출력
                print(f"  {nm:35s} | {r['ri_stock']} -> {r['batch_total']} (-{diff})")
            cur.execute(
                """
                UPDATE realtime_inventory
                SET stock_qty = ?, queried_at = ?
                WHERE store_id = ? AND item_cd = ?
                """,
                (max(0, r['batch_total']), now, r['store_id'], r['item_cd'])
            )
            total_reduced += diff
        print(f"  -> 총 {len(overstock_rows)}건 보정, -{total_reduced}개 차감")
    else:
        print(f"\n[2단계] 배치 기준 과대 재고 없음")

    # 3. daily_sales 최근 stock_qty 기준 보정
    # daily_sales에 기록된 stock_qty가 realtime_inventory보다 작으면 보정
    cur.execute(
        """
        SELECT ri.item_cd, ri.item_nm, ri.stock_qty as ri_stock,
               ds_latest.stock_qty as ds_stock, ds_latest.sales_date
        FROM realtime_inventory ri
        JOIN (
            SELECT item_cd, stock_qty, sales_date
            FROM daily_sales ds1
            WHERE sales_date = (
                SELECT MAX(sales_date) FROM daily_sales ds2
                WHERE ds2.item_cd = ds1.item_cd AND ds2.store_id = ds1.store_id
            )
            AND store_id = ?
        ) ds_latest ON ds_latest.item_cd = ri.item_cd
        WHERE ri.store_id = ?
        AND ri.stock_qty > ds_latest.stock_qty
        ORDER BY (ri.stock_qty - ds_latest.stock_qty) DESC
        """,
        (store_id, store_id)
    )
    ds_overstock = cur.fetchall()

    if ds_overstock:
        print(f"\n[3단계] daily_sales 기준 과대 재고 보정: {len(ds_overstock)}건")
        total_reduced2 = 0
        now = datetime.now().isoformat()
        for r in ds_overstock:
            diff = r['ri_stock'] - r['ds_stock']
            nm = r['item_nm'] or r['item_cd']
            if diff >= 2:
                print(f"  {nm:35s} | RI:{r['ri_stock']} -> DS:{r['ds_stock']} (-{diff}) [{r['sales_date']}]")
            cur.execute(
                """
                UPDATE realtime_inventory
                SET stock_qty = ?, queried_at = ?
                WHERE store_id = ? AND item_cd = ?
                """,
                (max(0, r['ds_stock']), now, store_id, r['item_cd'])
            )
            total_reduced2 += diff
        print(f"  -> 총 {len(ds_overstock)}건 보정, -{total_reduced2}개 차감")
    else:
        print(f"\n[3단계] daily_sales 기준 과대 재고 없음")

    # 4. 테스트 상품 확인
    cur.execute(
        "SELECT item_cd, item_nm, stock_qty FROM realtime_inventory WHERE item_cd = '8801234567'"
    )
    test_item = cur.fetchone()
    if test_item:
        print(f"\n[참고] 테스트상품 발견: {test_item['item_nm']} (재고:{test_item['stock_qty']})")

    # 5. 보정 후 주먹밥 재고 확인
    common_conn = sqlite3.connect("data/common.db")
    cc = common_conn.cursor()
    cc.execute("SELECT item_cd FROM products WHERE mid_cd='002'")
    jumok_items = set(r[0] for r in cc.fetchall())
    common_conn.close()

    cur.execute("SELECT item_cd, item_nm, stock_qty FROM realtime_inventory WHERE stock_qty > 0")
    jumok_stock = 0
    jumok_count = 0
    for r in cur.fetchall():
        if r['item_cd'] in jumok_items and r['item_cd'] != '8801234567':
            jumok_stock += r['stock_qty']
            jumok_count += 1

    print(f"\n[보정 후] 주먹밥 재고: {jumok_stock}개 ({jumok_count} SKU)")

    conn.commit()
    conn.close()
    print(f"\n매장 {store_id} 보정 완료!")


if __name__ == "__main__":
    stores = ["46513", "46704"]
    for sid in stores:
        patch_store(sid)
