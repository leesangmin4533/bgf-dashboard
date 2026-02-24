# -*- coding: utf-8 -*-
"""
inventory_batches DB 보정 스크립트

1. 중복 배치 삭제
2. 누락 배치 보충 (daily_sales.buy_qty > 0인데 배치 없는 건)
3. FIFO 소비 동기화 (현재 재고 기준)
4. 만료 배치 처리 (expiry_date <= 오늘)
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

from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent

CATEGORY_EXPIRY_DAYS = {
    "001": 1, "002": 1, "003": 1, "004": 1, "005": 1,
    "012": 2, "013": 3, "014": 7, "023": 7, "026": 3,
    "027": 5, "028": 3, "031": 3, "035": 5, "046": 14, "047": 10,
}
FOOD_CATS = {"001", "002", "003", "004", "005"}


def patch_store(store_id: str) -> None:
    print("=" * 70)
    print(f"{store_id} DB Patch")
    print("=" * 70)

    db_path = BASE_DIR / "data" / "stores" / f"{store_id}.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ── Step 1: 중복 active 배치 삭제 ──
    cur.execute(
        "SELECT item_cd, receiving_date, store_id, COUNT(*) as cnt "
        "FROM inventory_batches WHERE status = 'active' "
        "GROUP BY item_cd, receiving_date, store_id HAVING cnt > 1"
    )
    dups = cur.fetchall()
    dup_del = 0
    for d in dups:
        cur.execute(
            "DELETE FROM inventory_batches WHERE id NOT IN "
            "(SELECT MIN(id) FROM inventory_batches "
            "WHERE item_cd=? AND receiving_date=? AND store_id=? AND status='active') "
            "AND item_cd=? AND receiving_date=? AND store_id=? AND status='active'",
            (d["item_cd"], d["receiving_date"], d["store_id"],
             d["item_cd"], d["receiving_date"], d["store_id"]),
        )
        dup_del += cur.rowcount
    print(f"[Step 1] 중복 배치 삭제: {dup_del}건")

    # ── Step 2: 누락 배치 보충 ──
    # common.db에서 유통기한 + 상품명 조회
    common_path = BASE_DIR / "data" / "common.db"
    common = sqlite3.connect(str(common_path))
    common.row_factory = sqlite3.Row
    cc = common.cursor()
    cc.execute("SELECT item_cd, expiration_days FROM product_details WHERE expiration_days > 0")
    pd_expiry = {r["item_cd"]: r["expiration_days"] for r in cc.fetchall()}

    # 상품명: common.db products 테이블
    cc.execute("SELECT item_cd, item_nm FROM products")
    item_names = {r["item_cd"]: r["item_nm"] for r in cc.fetchall()}
    common.close()

    # 상품명 보충: 매장 DB realtime_inventory에서 추가 (common.db에 없는 상품)
    cur.execute("SELECT item_cd, item_nm FROM realtime_inventory WHERE item_nm IS NOT NULL AND item_nm != ''")
    for r in cur.fetchall():
        if r["item_cd"] not in item_names:
            item_names[r["item_cd"]] = r["item_nm"]

    # 누락 건 조회
    cur.execute(
        "SELECT ds.item_cd, ds.mid_cd, ds.sales_date, ds.buy_qty, ds.store_id "
        "FROM daily_sales ds "
        "WHERE ds.buy_qty > 0 "
        "AND NOT EXISTS ("
        "SELECT 1 FROM inventory_batches ib "
        "WHERE ib.item_cd = ds.item_cd AND ib.receiving_date = ds.sales_date "
        "AND ib.store_id = ds.store_id) "
        "ORDER BY ds.sales_date ASC"
    )
    missing = cur.fetchall()

    now = datetime.now().isoformat()
    created = 0
    for m in missing:
        icd = m["item_cd"]
        mid = m["mid_cd"]
        sdate = m["sales_date"]
        bqty = m["buy_qty"]
        sid = m["store_id"] or store_id
        inm = item_names.get(icd, "")

        # 유통기한 결정 순서: product_details > 카테고리 기본값 > 푸드/비푸드 기본값
        if icd in pd_expiry:
            exp = pd_expiry[icd]
        elif mid in CATEGORY_EXPIRY_DAYS:
            exp = CATEGORY_EXPIRY_DAYS[mid]
        elif mid in FOOD_CATS:
            exp = 1
        else:
            exp = 30

        try:
            rdt = datetime.strptime(sdate, "%Y-%m-%d")
            edt = rdt + timedelta(days=exp)
            edate = edt.strftime("%Y-%m-%d")
        except ValueError:
            continue

        cur.execute(
            "INSERT INTO inventory_batches "
            "(store_id, item_cd, item_nm, mid_cd, receiving_date, receiving_id, "
            "expiration_days, expiry_date, initial_qty, remaining_qty, "
            "status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,NULL,?,?,?,?,'active',?,?)",
            (sid, icd, inm, mid, sdate, exp, edate, bqty, bqty, now, now),
        )
        created += 1
    print(f"[Step 2] 누락 배치 생성: {created}건")

    # ── Step 3: FIFO 소비 동기화 ──
    cur.execute(
        "SELECT item_cd, store_id, SUM(remaining_qty) as bt "
        "FROM inventory_batches WHERE status='active' "
        "GROUP BY item_cd, store_id"
    )
    sync_items = cur.fetchall()

    cur.execute("SELECT item_cd, stock_qty, store_id FROM realtime_inventory")
    smap = {}
    for r in cur.fetchall():
        key = (r["item_cd"], r["store_id"] or "")
        smap[key] = r["stock_qty"] or 0

    fifo_total = 0
    for si in sync_items:
        icd = si["item_cd"]
        sid = si["store_id"] or store_id
        bt = si["bt"]
        cs = smap.get((icd, sid), 0)

        if bt > cs and bt > 0:
            to_consume = bt - cs
            cur.execute(
                "SELECT id, remaining_qty FROM inventory_batches "
                "WHERE item_cd=? AND store_id=? AND status='active' AND remaining_qty>0 "
                "ORDER BY receiving_date ASC, id ASC",
                (icd, sid),
            )
            rem = to_consume
            for br in cur.fetchall():
                if rem <= 0:
                    break
                bid = br["id"]
                brq = br["remaining_qty"]
                ded = min(brq, rem)
                nrq = brq - ded
                ns = "consumed" if nrq == 0 else "active"
                cur.execute(
                    "UPDATE inventory_batches SET remaining_qty=?, status=?, updated_at=? WHERE id=?",
                    (nrq, ns, now, bid),
                )
                rem -= ded
                fifo_total += ded
    print(f"[Step 3] FIFO 소비 동기화: {fifo_total}개 차감")

    # ── Step 4: 만료 배치 처리 ──
    cur.execute(
        "UPDATE inventory_batches SET status='expired', updated_at=? "
        "WHERE status='active' AND expiry_date <= date('now') AND remaining_qty > 0",
        (now,),
    )
    exp_cnt = cur.rowcount

    cur.execute(
        "UPDATE inventory_batches SET status='consumed', updated_at=? "
        "WHERE status='active' AND expiry_date <= date('now') AND remaining_qty = 0",
        (now,),
    )
    con_cnt = cur.rowcount
    print(f"[Step 4] 만료 처리: {exp_cnt}건, 전량소진+만료: {con_cnt}건")

    conn.commit()

    # ── 최종 현황 ──
    cur.execute(
        "SELECT status, COUNT(*) as c, SUM(initial_qty) as ti, SUM(remaining_qty) as tr "
        "FROM inventory_batches GROUP BY status"
    )
    print("\n[최종 현황]")
    for r in cur.fetchall():
        print(f"  {r['status']}: {r['c']}건, 초기={r['ti']}, 잔여={r['tr']}")

    # 푸드/디저트 폐기율
    cur.execute(
        "SELECT mid_cd, SUM(initial_qty) as ti, "
        "SUM(CASE WHEN status='expired' THEN remaining_qty ELSE 0 END) as w "
        "FROM inventory_batches "
        "WHERE mid_cd IN ('001','002','003','004','005','012','014') "
        "AND status IN ('consumed','expired') "
        "GROUP BY mid_cd ORDER BY mid_cd"
    )
    rows = cur.fetchall()
    if rows:
        print("\n[보정 후 푸드/디저트 폐기율]")
        for r in rows:
            ti = r["ti"] or 0
            w = r["w"] or 0
            rate = (w / ti * 100) if ti > 0 else 0
            print(f"  {r['mid_cd']}: 입고={ti}, 폐기={w}, 폐기율={rate:.1f}%")

    conn.close()
    print()


if __name__ == "__main__":
    patch_store("46513")
    patch_store("46704")
    print("Done!")
