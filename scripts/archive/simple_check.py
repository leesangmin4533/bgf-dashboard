#!/usr/bin/env python3
"""간단한 데이터 확인"""

import sqlite3

db_path = "C:/Users/kanur/OneDrive/바탕 화면/bbb/bgf_auto/data/bgf_sales.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 샘플 상품 하나 선택해서 상세 조회
cursor.execute('''
    SELECT item_cd, sales_date, sale_qty, stock_qty, ord_qty
    FROM daily_sales
    WHERE store_id = '46513'
      AND sales_date >= date('now', '-182 days')
    ORDER BY RANDOM()
    LIMIT 1
''')
sample = cursor.fetchone()

if sample:
    item_cd = sample[0]
    print(f"\n샘플 상품: {item_cd}")

    # 이 상품의 판매 패턴 확인
    cursor.execute('''
        SELECT
            COUNT(DISTINCT sales_date) as total_days,
            COUNT(DISTINCT CASE WHEN sale_qty > 0 THEN sales_date END) as sell_days,
            SUM(sale_qty) as total_sales,
            AVG(sale_qty) as avg_sales,
            MIN(sales_date) as min_date,
            MAX(sales_date) as max_date
        FROM daily_sales
        WHERE item_cd = ?
          AND store_id = '46513'
          AND sales_date >= date('now', '-182 days')
    ''', (item_cd,))
    result = cursor.fetchone()

    print(f"  전체 일수: {result[0]}일")
    print(f"  판매 일수: {result[1]}일")
    print(f"  총 판매량: {result[2]}개")
    print(f"  일평균: {result[3]:.2f}개")
    print(f"  판매비율: {result[1]/result[0]*100:.1f}%")
    print(f"  기간: {result[4]} ~ {result[5]}")

    # products 테이블에서 이름 조회
    cursor.execute('SELECT item_nm FROM products WHERE item_cd = ?', (item_cd,))
    name = cursor.fetchone()
    if name:
        print(f"  상품명: {name[0]}")

# 최근 7일 판매가 0인 상품 조회
print("\n\n최근 7일 판매가 없는 상품 (10개):")
cursor.execute('''
    WITH recent_sales AS (
        SELECT
            item_cd,
            SUM(sale_qty) as week_sales
        FROM daily_sales
        WHERE sales_date >= date('now', '-7 days')
          AND store_id = '46513'
        GROUP BY item_cd
    )
    SELECT
        ds.item_cd,
        p.item_nm,
        COALESCE(rs.week_sales, 0) as week_sales,
        SUM(ds.sale_qty) as total_sales,
        ds.stock_qty,
        ds.ord_qty
    FROM daily_sales ds
    LEFT JOIN recent_sales rs ON ds.item_cd = rs.item_cd
    LEFT JOIN products p ON ds.item_cd = p.item_cd
    WHERE ds.store_id = '46513'
      AND (ds.item_cd, ds.sales_date) IN (
          SELECT item_cd, MAX(sales_date)
          FROM daily_sales
          WHERE store_id = '46513'
          GROUP BY item_cd
      )
      AND COALESCE(rs.week_sales, 0) = 0
    GROUP BY ds.item_cd
    LIMIT 10
''')

print(f"{'상품코드':<15} {'상품명':<25} {'7일판매':<10} {'재고':<8} {'미입고'}")
print("-" * 75)
for row in cursor.fetchall():
    print(f"{row[0]:<15} {(row[1] or 'Unknown')[:23]:<25} {row[2]:>8}개  {row[4]:>6}개  {row[5]:>6}개")

conn.close()
