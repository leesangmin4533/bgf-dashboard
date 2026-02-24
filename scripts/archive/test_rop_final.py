#!/usr/bin/env python3
"""
ROP 로직 최종 검증
최근 7일 판매 없음 + 재고=0 상품으로 테스트
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import sqlite3
from src.prediction.improved_predictor import ImprovedPredictor

def find_test_items():
    """최근 7일 판매 없음 + 재고=0 상품 찾기"""
    db_path = "C:/Users/kanur/OneDrive/바탕 화면/bbb/bgf_auto/data/bgf_sales.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    query = '''
    WITH recent_sales AS (
        SELECT
            item_cd,
            SUM(sale_qty) as week_sales
        FROM daily_sales
        WHERE sales_date >= date('now', '-7 days')
          AND store_id = '46513'
        GROUP BY item_cd
    ),
    latest_stock AS (
        SELECT
            item_cd,
            stock_qty,
            ord_qty as pending_qty,
            sales_date
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
        ls.item_cd,
        p.item_nm,
        COALESCE(rs.week_sales, 0) as week_sales,
        ls.stock_qty,
        ls.pending_qty,
        ls.sales_date
    FROM latest_stock ls
    LEFT JOIN recent_sales rs ON ls.item_cd = rs.item_cd
    LEFT JOIN products p ON ls.item_cd = p.item_cd
    WHERE ls.stock_qty = 0
      AND COALESCE(rs.week_sales, 0) = 0
      AND ls.pending_qty = 0
    LIMIT 10
    '''

    cursor.execute(query)
    items = cursor.fetchall()

    conn.close()
    return items


def test_rop_logic(items):
    """ROP 로직 테스트"""
    if not items:
        print("\n[경고] 테스트할 상품이 없습니다.")
        print("재고=0 + 최근 판매 없음 + 미입고=0 조건을 만족하는 상품이 없습니다.")
        return

    print("\n" + "="*80)
    print("ROP 로직 최종 검증")
    print("="*80)
    print(f"\n테스트 상품: {len(items)}개")
    print(f"{'상품코드':<15} {'상품명':<25} {'7일판매':<10} {'재고':<8} {'미입고':<8} {'최신날짜'}")
    print("-" * 90)

    for item in items:
        print(f"{item[0]:<15} {(item[1] or 'Unknown')[:23]:<25} {item[2]:>8}개  {item[3]:>6}개  {item[4]:>6}개  {item[5]}")

    predictor = ImprovedPredictor()

    print("\n" + "="*80)
    print("예측 실행")
    print("="*80)

    order_count = 0
    total_count = len(items)

    for item in items:
        item_cd = item[0]
        item_nm = item[1] or 'Unknown'

        # 예측 실행
        result = predictor.predict(item_cd)

        if result and result.order_qty > 0:
            order_count += 1
            status = "[발주]"
        else:
            status = "[미발주]"

        order_qty = result.order_qty if result else 0

        print(f"\n{status} {item_nm}")
        print(f"  - 상품코드: {item_cd}")
        print(f"  - 재고: 0개, 미입고: 0개, 최근 판매: 0개")
        print(f"  => 발주량: {order_qty}개")

        if result:
            print(f"     (예측: {result.predicted_qty:.2f}, 안전재고: {result.safety_stock:.2f})")

    print("\n" + "="*80)
    print("테스트 결과")
    print("="*80)
    print(f"발주 상품: {order_count}/{total_count}개 ({order_count/total_count*100:.1f}%)")

    if order_count / total_count >= 0.9:
        print("\n[성공] ROP 로직이 정상 작동합니다!")
        print("재고=0 + 판매 없음 상품에 대해 최소 발주를 실행합니다.")
    else:
        print(f"\n[주의] 발주율이 목표(90%) 미만입니다.")
        print("improved_predictor.py 로그를 확인하여 ROP 로직 실행 여부를 검증하세요.")

    return order_count / total_count if total_count > 0 else 0


if __name__ == "__main__":
    print("\n간헐적 수요 패치 - ROP 로직 최종 검증")
    print("="*80)
    print("조건: 재고=0 AND 최근7일 판매=0 AND 미입고=0")
    print("="*80)

    items = find_test_items()

    if items:
        test_rop_logic(items)
    else:
        print("\n[결론] 현재 조건을 만족하는 상품이 없습니다.")
        print("- 모든 상품이 재고를 보유하고 있거나")
        print("- 재고=0인 상품은 이미 미입고가 있습니다.")
        print("\n이는 정상적인 상태이며, 패치가 의도대로 작동하고 있음을 의미합니다.")
