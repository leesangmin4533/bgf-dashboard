"""46704 매장 2026-02-19 발주 실행 결과 확인"""
import sys, sqlite3, os
sys.stdout.reconfigure(encoding='utf-8')

# 1. order_analysis.db에서 46704의 스냅샷 전체 현황
DB = r"C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\data\order_analysis.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print("=" * 80)
print("1. 46704 매장 날짜별 스냅샷 현황 (order_success 분포)")
print("=" * 80)
rows = conn.execute("""
    SELECT order_date,
           COUNT(*) as total,
           SUM(CASE WHEN order_success=1 THEN 1 ELSE 0 END) as success,
           SUM(CASE WHEN order_success=0 THEN 1 ELSE 0 END) as fail,
           SUM(CASE WHEN final_order_qty > 0 THEN 1 ELSE 0 END) as has_qty,
           SUM(final_order_qty) as total_qty
    FROM order_snapshots
    WHERE store_id='46704'
    GROUP BY order_date
    ORDER BY order_date DESC
    LIMIT 10
""").fetchall()
print(f"{'발주일':>12} {'총건수':>6} {'성공':>6} {'실패':>6} {'발주>0':>6} {'총발주량':>8}")
for r in rows:
    print(f"{r['order_date']:>12} {r['total']:>6} {r['success']:>6} {r['fail']:>6} {r['has_qty']:>6} {r['total_qty']:>8}")

# 2. diff에서 46704 removed 상세 — auto_order_qty가 0인 removed가 있는가?
print("\n" + "=" * 80)
print("2. 46704 2/19 diff: removed 건의 auto_order_qty 분포")
print("=" * 80)
rows = conn.execute("""
    SELECT auto_order_qty, COUNT(*) as cnt
    FROM order_diffs
    WHERE store_id='46704' AND order_date='2026-02-19' AND diff_type='removed'
    GROUP BY auto_order_qty
    ORDER BY auto_order_qty
""").fetchall()
for r in rows:
    print(f"  auto_order_qty={r['auto_order_qty']}: {r['cnt']}건")

# 3. 46704 2/19 diff: qty_changed 건의 auto_order_qty 분포
print("\n" + "=" * 80)
print("3. 46704 2/19 diff: qty_changed 건의 auto_order_qty")
print("=" * 80)
rows = conn.execute("""
    SELECT item_cd, item_nm, auto_order_qty, confirmed_order_qty, qty_diff
    FROM order_diffs
    WHERE store_id='46704' AND order_date='2026-02-19' AND diff_type='qty_changed'
    ORDER BY qty_diff DESC
""").fetchall()
for r in rows:
    nm = (r['item_nm'] or '')[:25]
    print(f"  {r['item_cd']} {nm:<25} 자동={r['auto_order_qty']:>4} 확정={r['confirmed_order_qty']:>4} 차이={r['qty_diff']:>+5}")

conn.close()

# 4. 매장 DB에서 실제 order_tracking 확인
STORE_DB = r"C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\data\stores\46704.db"
if os.path.exists(STORE_DB):
    conn2 = sqlite3.connect(STORE_DB)
    conn2.row_factory = sqlite3.Row

    print("\n" + "=" * 80)
    print("4. 46704 매장 DB: order_tracking (2/19)")
    print("=" * 80)
    rows = conn2.execute("""
        SELECT order_date, COUNT(*) as cnt, SUM(order_qty) as total_qty,
               order_source
        FROM order_tracking
        WHERE order_date >= '2026-02-18'
        GROUP BY order_date, order_source
        ORDER BY order_date DESC
    """).fetchall()
    for r in rows:
        print(f"  {r['order_date']} source={r['order_source']}: {r['cnt']}건, 총량={r['total_qty']}")

    # 5. receiving_history 확인
    print("\n" + "=" * 80)
    print("5. 46704 매장 DB: receiving_history (order_date=2/19)")
    print("=" * 80)
    rows = conn2.execute("""
        SELECT order_date, receiving_date, COUNT(*) as cnt,
               SUM(order_qty) as total_order, SUM(receiving_qty) as total_recv
        FROM receiving_history
        WHERE order_date >= '2026-02-18'
        GROUP BY order_date, receiving_date
        ORDER BY order_date DESC
    """).fetchall()
    for r in rows:
        print(f"  발주일={r['order_date']} 입고일={r['receiving_date']}: {r['cnt']}건, "
              f"발주량={r['total_order']}, 입고량={r['total_recv']}")

    conn2.close()
else:
    print(f"\n매장 DB 없음: {STORE_DB}")
