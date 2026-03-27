"""라면류 배수 발주 위반 건 조회"""
import sqlite3

conn = sqlite3.connect('data/stores/46513.db')
conn.row_factory = sqlite3.Row
conn.execute("ATTACH DATABASE 'data/common.db' AS common")

cur = conn.cursor()

# 최근 발주에서 order_unit_qty 배수가 아닌 건
cur.execute("""
    SELECT ot.order_date, ot.item_cd, ot.item_nm, ot.order_qty, ot.mid_cd,
           ri.order_unit_qty as ri_unit,
           pd.order_unit_qty as pd_unit
    FROM order_tracking ot
    LEFT JOIN realtime_inventory ri ON ot.item_cd = ri.item_cd AND ot.store_id = ri.store_id
    LEFT JOIN common.product_details pd ON ot.item_cd = pd.item_cd
    WHERE ot.order_date >= '2026-03-01'
    AND ot.mid_cd IN ('031','032','033','034')
    AND ot.order_qty > 0
    AND ri.order_unit_qty > 1
    AND (ot.order_qty % ri.order_unit_qty) != 0
    ORDER BY ot.order_date DESC, ot.item_cd
""")
rows = cur.fetchall()
print(f"배수 미정렬 발주 건수: {len(rows)}")
for row in rows:
    d = dict(row)
    print(f"  {d['order_date']} | {d['item_cd']} | qty={d['order_qty']} | ri_unit={d['ri_unit']} pd_unit={d['pd_unit']} | mid={d['mid_cd']}")

print("\n--- 라면류 전체 최근 발주 ---")
cur.execute("""
    SELECT ot.order_date, ot.item_cd, ot.item_nm, ot.order_qty, ot.mid_cd,
           ri.order_unit_qty as ri_unit,
           pd.order_unit_qty as pd_unit,
           ot.order_source
    FROM order_tracking ot
    LEFT JOIN realtime_inventory ri ON ot.item_cd = ri.item_cd AND ot.store_id = ri.store_id
    LEFT JOIN common.product_details pd ON ot.item_cd = pd.item_cd
    WHERE ot.order_date >= '2026-03-03'
    AND ot.mid_cd IN ('031','032','033','034')
    AND ot.order_qty > 0
    ORDER BY ot.order_date DESC, ot.item_cd
""")
rows = cur.fetchall()
for row in rows:
    d = dict(row)
    is_aligned = "OK" if d['ri_unit'] and d['ri_unit'] > 1 and d['order_qty'] % d['ri_unit'] == 0 else "WARN"
    print(f"  {d['order_date']} | {d['item_cd']} | qty={d['order_qty']} | ri_unit={d['ri_unit']} pd_unit={d['pd_unit']} | {is_aligned} | src={d['order_source']}")

# 8801043022262 상세 확인
print("\n--- 8801043022262 prediction_logs 상세 ---")
cur.execute("""
    SELECT prediction_date, predicted_qty, adjusted_qty, safety_stock,
           current_stock, order_qty, model_type, confidence
    FROM prediction_logs
    WHERE item_cd = '8801043022262'
    AND prediction_date >= '2026-03-01'
    ORDER BY prediction_date DESC
""")
for row in cur.fetchall():
    d = dict(row)
    print(f"  {d['prediction_date']} | pred={d['predicted_qty']} adj={d['adjusted_qty']} safety={d['safety_stock']} stock={d['current_stock']} order={d['order_qty']} model={d['model_type']}")

# eval_results 확인
print("\n--- 8801043022262 eval_results ---")
cur.execute("""
    SELECT * FROM eval_results
    WHERE item_cd = '8801043022262'
    ORDER BY eval_date DESC LIMIT 3
""")
for row in cur.fetchall():
    print(f"  {dict(row)}")

conn.close()
