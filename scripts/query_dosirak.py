# -*- coding: utf-8 -*-
"""도시락 분석 쿼리 3종"""
import sqlite3, os, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
common_db = os.path.join(BASE, "data", "common.db")

stores = [("46513", "CU동양대점"), ("47863", "CU세번째점"), ("46704", "CU호반점")]

# === 1. 동양대점 2월말~3월초 도시락 일별 현황 ===
print("=" * 60)
print("1. 동양대점(46513) 2월말~3월초 도시락 일별 현황")
print("=" * 60)
conn = sqlite3.connect(os.path.join(BASE, "data/stores/46513.db"))
c = conn.cursor()
c.execute("""
SELECT sales_date, COUNT(*), SUM(stock_qty), SUM(sale_qty), SUM(disuse_qty)
FROM daily_sales WHERE mid_cd='001' AND sales_date BETWEEN '2026-02-25' AND '2026-03-03'
GROUP BY sales_date ORDER BY sales_date
""")
print(f"{'날짜':12} {'SKU수':>5} {'재고합':>6} {'판매합':>6} {'폐기합':>6}")
for r in c.fetchall():
    print(f"{r[0]:12} {r[1]:5} {r[2]:6} {r[3]:6} {r[4]:6}")
conn.close()

# === 2. 세번째점 3/12~13 폐기 ===
print()
print("=" * 60)
print("2. 세번째점(47863) 3/12~3/13 도시락 폐기 상품")
print("=" * 60)
conn = sqlite3.connect(os.path.join(BASE, "data/stores/47863.db"))
conn.execute(f"ATTACH DATABASE '{common_db}' AS common")
c = conn.cursor()
c.execute("""
SELECT ds.sales_date, COALESCE(p.item_nm, ds.item_cd),
       ds.sale_qty, ds.buy_qty, ds.disuse_qty, ds.stock_qty
FROM daily_sales ds LEFT JOIN common.products p ON ds.item_cd = p.item_cd
WHERE ds.mid_cd='001' AND ds.sales_date IN ('2026-03-12','2026-03-13') AND ds.disuse_qty > 0
ORDER BY ds.sales_date, ds.disuse_qty DESC
""")
print(f"{'날짜':12} {'상품명':30} {'판매':>4} {'매입':>4} {'폐기':>4} {'재고':>4}")
for r in c.fetchall():
    print(f"{r[0]:12} {str(r[1])[:28]:30} {r[2]:4} {r[3]:4} {r[4]:4} {r[5]:4}")
conn.close()

# === 3. 3매장 폐기 TOP 10 ===
for sid, sname in stores:
    print()
    print("=" * 60)
    print(f"3. {sname}({sid}) 3월 도시락 폐기 TOP 10")
    print("=" * 60)
    conn = sqlite3.connect(os.path.join(BASE, f"data/stores/{sid}.db"))
    conn.execute(f"ATTACH DATABASE '{common_db}' AS common")
    c = conn.cursor()
    c.execute("""
    SELECT COALESCE(p.item_nm, ds.item_cd),
           SUM(ds.sale_qty), SUM(ds.buy_qty), SUM(ds.disuse_qty),
           ROUND(CAST(SUM(ds.disuse_qty) AS FLOAT)/NULLIF(SUM(ds.buy_qty),0)*100,1)
    FROM daily_sales ds LEFT JOIN common.products p ON ds.item_cd = p.item_cd
    WHERE ds.mid_cd='001' AND ds.sales_date >= '2026-03-01'
    GROUP BY ds.item_cd HAVING SUM(ds.disuse_qty)>0
    ORDER BY SUM(ds.disuse_qty) DESC LIMIT 10
    """)
    print(f"{'상품명':30} {'판매':>5} {'매입':>5} {'폐기':>5} {'폐기율':>7}")
    for r in c.fetchall():
        wr = f"{r[4]}%" if r[4] else "-"
        print(f"{str(r[0])[:28]:30} {r[1]:5} {r[2]:5} {r[3]:5} {wr:>7}")
    conn.close()
