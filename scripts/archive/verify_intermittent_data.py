#!/usr/bin/env python3
"""
간헐적 상품 데이터 확인
- 실제로 간헐적 상품이 존재하는지 확인
- 재고 상태 분포 확인
"""

import sqlite3

db_path = "C:/Users/kanur/OneDrive/바탕 화면/bbb/bgf_auto/data/bgf_sales.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 1. 전체 데이터 범위 확인
cursor.execute('''
    SELECT
        MIN(sales_date) as min_date,
        MAX(sales_date) as max_date,
        COUNT(DISTINCT sales_date) as date_count,
        COUNT(DISTINCT item_cd) as item_count
    FROM daily_sales
    WHERE store_id = '46513'
''')
result = cursor.fetchone()
print("\n데이터 범위:")
print(f"  기간: {result[0]} ~ {result[1]}")
print(f"  일수: {result[2]}일")
print(f"  상품수: {result[3]}개")

# 2. 간헐적 상품 분포 (threshold별)
print("\n간헐적 상품 분포:")
for threshold in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]:
    cursor.execute(f'''
        WITH item_stats AS (
            SELECT
                item_cd,
                COUNT(DISTINCT CASE WHEN sale_qty > 0 THEN sales_date END) as sell_days,
                COUNT(DISTINCT sales_date) as total_days
            FROM daily_sales
            WHERE sales_date >= date('now', '-182 days')
              AND store_id = '46513'
            GROUP BY item_cd
            HAVING total_days >= 90
        )
        SELECT COUNT(*) as cnt
        FROM item_stats
        WHERE CAST(sell_days AS REAL) / total_days < {threshold}
    ''')
    count = cursor.fetchone()[0]
    print(f"  판매비율 < {threshold*100:3.0f}%: {count:4d}개")

# 3. 재고 상태 분포
print("\n재고 상태 분포 (최신 데이터):")
cursor.execute('''
    WITH latest_stock AS (
        SELECT
            item_cd,
            stock_qty,
            ord_qty as pending_qty
        FROM daily_sales
        WHERE store_id = '46513'
          AND (item_cd, sales_date) IN (
              SELECT item_cd, MAX(sales_date)
              FROM daily_sales
              WHERE store_id = '46513'
              GROUP BY item_cd
          )
    )
    SELECT
        CASE
            WHEN stock_qty = 0 THEN '재고 = 0'
            WHEN stock_qty <= 3 THEN '재고 1~3'
            WHEN stock_qty <= 10 THEN '재고 4~10'
            ELSE '재고 > 10'
        END as stock_range,
        COUNT(*) as cnt
    FROM latest_stock
    GROUP BY stock_range
    ORDER BY
        CASE stock_range
            WHEN '재고 = 0' THEN 1
            WHEN '재고 1~3' THEN 2
            WHEN '재고 4~10' THEN 3
            ELSE 4
        END
''')
for row in cursor.fetchall():
    print(f"  {row[0]:<12}: {row[1]:4d}개")

# 4. 간헐적(30% 미만) + 재고 상태
print("\n간헐적 상품(판매비율<30%)의 재고 상태:")
cursor.execute('''
    WITH item_stats AS (
        SELECT
            item_cd,
            COUNT(DISTINCT CASE WHEN sale_qty > 0 THEN sales_date END) as sell_days,
            COUNT(DISTINCT sales_date) as total_days
        FROM daily_sales
        WHERE sales_date >= date('now', '-182 days')
          AND store_id = '46513'
        GROUP BY item_cd
        HAVING total_days >= 90
          AND CAST(sell_days AS REAL) / total_days < 0.3
    ),
    latest_stock AS (
        SELECT
            item_cd,
            stock_qty,
            ord_qty as pending_qty
        FROM daily_sales
        WHERE store_id = '46513'
          AND (item_cd, sales_date) IN (
              SELECT item_cd, MAX(sales_date)
              FROM daily_sales
              WHERE store_id = '46513'
              GROUP BY item_cd
          )
    )
    SELECT
        CASE
            WHEN ls.stock_qty = 0 THEN '재고 = 0'
            WHEN ls.stock_qty <= 3 THEN '재고 1~3'
            WHEN ls.stock_qty <= 10 THEN '재고 4~10'
            ELSE '재고 > 10'
        END as stock_range,
        COUNT(*) as cnt
    FROM item_stats s
    LEFT JOIN latest_stock ls ON s.item_cd = ls.item_cd
    GROUP BY stock_range
    ORDER BY
        CASE stock_range
            WHEN '재고 = 0' THEN 1
            WHEN '재고 1~3' THEN 2
            WHEN '재고 4~10' THEN 3
            ELSE 4
        END
''')
rows = cursor.fetchall()
if rows:
    for row in rows:
        print(f"  {row[0]:<12}: {row[1]:4d}개")
else:
    print("  데이터 없음")

# 5. 샘플 조회 (간헐적 + 재고 적음)
print("\n간헐적 + 재고 적은 상품 샘플 (TOP 5):")
cursor.execute('''
    WITH item_stats AS (
        SELECT
            ds.item_cd,
            COUNT(DISTINCT CASE WHEN ds.sale_qty > 0 THEN ds.sales_date END) as sell_days,
            COUNT(DISTINCT ds.sales_date) as total_days,
            SUM(ds.sale_qty) as total_sales
        FROM daily_sales ds
        WHERE ds.sales_date >= date('now', '-182 days')
          AND ds.store_id = '46513'
        GROUP BY ds.item_cd
        HAVING total_days >= 90
    ),
    latest_stock AS (
        SELECT
            item_cd,
            stock_qty,
            ord_qty as pending_qty
        FROM daily_sales
        WHERE store_id = '46513'
          AND (item_cd, sales_date) IN (
              SELECT item_cd, MAX(sales_date)
              FROM daily_sales
              WHERE store_id = '46513'
              GROUP BY item_cd
          )
    )
    SELECT
        s.item_cd,
        COALESCE(p.item_nm, 'Unknown') as item_nm,
        CAST(s.sell_days AS REAL) / s.total_days as sell_ratio,
        COALESCE(ls.stock_qty, 0) as stock,
        COALESCE(ls.pending_qty, 0) as pending
    FROM item_stats s
    LEFT JOIN latest_stock ls ON s.item_cd = ls.item_cd
    LEFT JOIN products p ON s.item_cd = p.item_cd
    WHERE CAST(s.sell_days AS REAL) / s.total_days < 0.3
    ORDER BY COALESCE(ls.stock_qty, 0) ASC, s.total_sales DESC
    LIMIT 5
''')
rows = cursor.fetchall()
if rows:
    print(f"{'상품코드':<15} {'상품명':<25} {'판매비율':<10} {'재고':<8} {'미입고'}")
    print("-" * 75)
    for row in rows:
        print(f"{row[0]:<15} {row[1][:23]:<25} {row[2]:>8.1%}  {row[3]:>6}개  {row[4]:>6}개")
else:
    print("  데이터 없음")

conn.close()
