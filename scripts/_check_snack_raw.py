"""과자 카테고리 원본 데이터 확인"""
import sys, sqlite3
sys.stdout.reconfigure(encoding='utf-8')

DB = r"C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\data\order_analysis.db"
SNACK_MIDS = ('015','016','017','018','019','020','029','030')
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# 1. 최근 날짜 확인
print("=" * 80)
print("1. 전체 날짜별 데이터 현황")
print("=" * 80)
rows = conn.execute("""
    SELECT store_id, order_date,
           COUNT(*) as cnt,
           SUM(final_order_qty) as total_qty,
           SUM(CASE WHEN final_order_qty > 0 THEN 1 ELSE 0 END) as has_qty
    FROM order_snapshots
    GROUP BY store_id, order_date
    ORDER BY order_date DESC, store_id
    LIMIT 20
""").fetchall()
print(f"{'매장':>6} {'발주일':>12} {'스냅샷수':>8} {'총발주량':>8} {'발주>0':>8}")
for r in rows:
    print(f"{r['store_id']:>6} {r['order_date']:>12} {r['cnt']:>8} {r['total_qty']:>8} {r['has_qty']:>8}")

# 2. 과자 카테고리 스냅샷 상세 (최근 2일)
print("\n" + "=" * 80)
print("2. 과자 카테고리 스냅샷 상세 (최근 2일)")
print("=" * 80)
for store in ['46513', '46704']:
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT order_date FROM order_snapshots WHERE store_id=? ORDER BY order_date DESC LIMIT 2",
        (store,)
    ).fetchall()]

    for dt in dates:
        rows = conn.execute("""
            SELECT item_cd, item_nm, mid_cd, predicted_qty, recommended_qty,
                   final_order_qty, current_stock, pending_qty, eval_decision,
                   order_success, delivery_type
            FROM order_snapshots
            WHERE store_id=? AND order_date=? AND mid_cd IN ({})
            ORDER BY final_order_qty DESC
        """.format(','.join('?'*len(SNACK_MIDS))), (store, dt, *SNACK_MIDS)).fetchall()

        if not rows:
            print(f"\n[{store}] {dt}: 과자 스냅샷 없음")
            continue

        print(f"\n[{store}] {dt}: 과자 스냅샷 {len(rows)}건")
        print(f"{'상품코드':>15} {'상품명':<25} {'mid':>4} {'예측':>4} {'추천':>4} {'최종':>4} {'재고':>4} {'미입고':>4} {'평가':>15} {'성공':>3} {'배송':>4}")
        for r in rows:
            nm = (r['item_nm'] or '')[:22]
            print(f"{r['item_cd']:>15} {nm:<25} {r['mid_cd']:>4} {r['predicted_qty']:>4} {r['recommended_qty']:>4} "
                  f"{r['final_order_qty']:>4} {r['current_stock']:>4} {r['pending_qty']:>4} "
                  f"{(r['eval_decision'] or ''):>15} {r['order_success']:>3} {(r['delivery_type'] or ''):>4}")

# 3. 과자 카테고리 diff 상세 (최근 2일)
print("\n" + "=" * 80)
print("3. 과자 카테고리 Diff 상세 (최근 2일)")
print("=" * 80)
for store in ['46513', '46704']:
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT order_date FROM order_diffs WHERE store_id=? ORDER BY order_date DESC LIMIT 2",
        (store,)
    ).fetchall()]

    for dt in dates:
        rows = conn.execute("""
            SELECT item_cd, item_nm, mid_cd, diff_type,
                   auto_order_qty, confirmed_order_qty, receiving_qty, qty_diff, receiving_diff
            FROM order_diffs
            WHERE store_id=? AND order_date=? AND mid_cd IN ({})
            ORDER BY diff_type, item_cd
        """.format(','.join('?'*len(SNACK_MIDS))), (store, dt, *SNACK_MIDS)).fetchall()

        if not rows:
            print(f"\n[{store}] {dt}: 과자 diff 없음")
            continue

        print(f"\n[{store}] {dt}: 과자 diff {len(rows)}건")
        # diff_type별 요약
        from collections import Counter
        type_cnt = Counter(r['diff_type'] for r in rows)
        print(f"  분포: {dict(type_cnt)}")

        print(f"  {'상품코드':>15} {'상품명':<22} {'mid':>4} {'유형':<14} {'자동':>4} {'확정':>4} {'입고':>4} {'차이':>5}")
        for r in rows:
            nm = (r['item_nm'] or '')[:20]
            print(f"  {r['item_cd']:>15} {nm:<22} {r['mid_cd']:>4} {r['diff_type']:<14} "
                  f"{r['auto_order_qty']:>4} {r['confirmed_order_qty']:>4} {r['receiving_qty']:>4} {r['qty_diff']:>5}")

# 4. 과자 카테고리 summary
print("\n" + "=" * 80)
print("4. 과자 포함 전체 Diff Summary (최근 2일)")
print("=" * 80)
for store in ['46513', '46704']:
    rows = conn.execute("""
        SELECT order_date, receiving_date, total_auto_items, total_confirmed_items,
               items_unchanged, items_qty_changed, items_added, items_removed,
               match_rate, has_receiving_data
        FROM order_diff_summary
        WHERE store_id=?
        ORDER BY order_date DESC LIMIT 4
    """, (store,)).fetchall()
    print(f"\n[{store}] Summary:")
    for r in rows:
        print(f"  {r['order_date']} -> {r['receiving_date']}: "
              f"자동={r['total_auto_items']}, 확정={r['total_confirmed_items']}, "
              f"동일={r['items_unchanged']}, 변경={r['items_qty_changed']}, "
              f"추가={r['items_added']}, 제거={r['items_removed']}, "
              f"일치율={r['match_rate']:.1%}, 입고={r['has_receiving_data']}")

conn.close()
