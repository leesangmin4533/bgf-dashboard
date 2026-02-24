"""
간헐적 수요 상품 분석 스크립트

실제 판매 데이터에서 간헐적 수요 상품(월 1-2회 또는 주 1-2회 판매)을 식별하고 분석
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
import argparse
from datetime import datetime, timedelta
from collections import defaultdict


def analyze_intermittent_items(store_id=None, days=182, min_records=10):
    """
    간헐적 수요 상품 분석

    Args:
        store_id: 점포 코드 (None이면 전체)
        days: 분석 기간 (일)
        min_records: 최소 레코드 수 (이보다 적으면 제외)
    """
    db_path = Path(__file__).parent.parent / "data" / "bgf_sales.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 분석 기간 계산
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    print("=" * 80)
    print("간헐적 수요 상품 분석")
    print("=" * 80)
    print(f"분석 기간: {start_date} ~ {end_date} ({days}일)")
    if store_id:
        store_names = {
            "46513": "이천호반베르디움점",
            "46704": "이천동양점"
        }
        store_name = store_names.get(store_id, "Unknown")
        print(f"점포: {store_name} ({store_id})")
    else:
        print("점포: 전체")
    print("-" * 80)

    # 상품별 판매일 비율 계산
    query = """
    SELECT
        ds.item_cd,
        ds.mid_cd,
        COUNT(DISTINCT ds.sales_date) as sell_days,
        COUNT(*) as total_records,
        SUM(ds.sale_qty) as total_sales,
        AVG(ds.sale_qty) as avg_sales
    FROM daily_sales ds
    WHERE ds.sales_date >= ? AND ds.sales_date <= ?
    """

    params = [start_date, end_date]

    if store_id:
        query += " AND ds.store_id = ?"
        params.append(store_id)

    query += """
    GROUP BY ds.item_cd, ds.mid_cd
    HAVING COUNT(*) >= ?
    ORDER BY sell_days ASC
    """
    params.append(min_records)

    cursor.execute(query, params)
    results = cursor.fetchall()

    # 실제 거래일 수 계산 (주말 포함)
    cursor.execute(f"""
        SELECT COUNT(DISTINCT sales_date)
        FROM daily_sales
        WHERE sales_date >= ? AND sales_date <= ?
        {' AND store_id = ?' if store_id else ''}
    """, params[:2] + ([store_id] if store_id else []))

    actual_days = cursor.fetchone()[0]

    # 간헐적 수요 상품 분류
    very_intermittent = []  # < 30% (월 1-2회 수준)
    intermittent = []       # 30-60% (주 2-4회 수준)
    normal = []            # >= 60%

    for row in results:
        item_cd, mid_cd, sell_days, total_records, total_sales, avg_sales = row
        sell_ratio = sell_days / actual_days if actual_days > 0 else 0

        item_data = {
            'item_cd': item_cd,
            'mid_cd': mid_cd,
            'sell_days': sell_days,
            'total_records': total_records,
            'total_sales': int(total_sales),
            'avg_sales': avg_sales,
            'sell_ratio': sell_ratio
        }

        if sell_ratio < 0.3:
            very_intermittent.append(item_data)
        elif sell_ratio < 0.6:
            intermittent.append(item_data)
        else:
            normal.append(item_data)

    total_items = len(results)

    # 결과 출력
    print(f"\n[전체 요약]")
    print(f"총 상품 수: {total_items:,}개")
    print(f"실제 거래일 수: {actual_days}일")
    print()
    print(f"일반 상품 (판매일 >= 60%):          {len(normal):>5}개 ({len(normal)/total_items*100:5.1f}%)")
    print(f"간헐적 수요 (판매일 30-60%):       {len(intermittent):>5}개 ({len(intermittent)/total_items*100:5.1f}%)")
    print(f"매우 간헐적 (판매일 < 30%):        {len(very_intermittent):>5}개 ({len(very_intermittent)/total_items*100:5.1f}%)")

    # 매우 간헐적 수요 상품 상세 (Top 20)
    if very_intermittent:
        print("\n" + "=" * 80)
        print("[매우 간헐적 수요 상품 Top 20] (판매일 < 30%)")
        print("=" * 80)
        print(f"{'상품코드':<12} | {'중분류':^8} | {'판매일':>6} | {'판매율':>7} | {'총판매':>8} | {'일평균':>8}")
        print("-" * 80)

        for i, item in enumerate(very_intermittent[:20], 1):
            print(f"{item['item_cd']:<12} | {item['mid_cd']:^8} | "
                  f"{item['sell_days']:>6}일 | {item['sell_ratio']:>6.1%} | "
                  f"{item['total_sales']:>8}개 | {item['avg_sales']:>8.2f}개")

        if len(very_intermittent) > 20:
            print(f"... (외 {len(very_intermittent) - 20}개)")

    # 간헐적 수요 상품 상세 (Top 20)
    if intermittent:
        print("\n" + "=" * 80)
        print("[간헐적 수요 상품 Top 20] (판매일 30-60%)")
        print("=" * 80)
        print(f"{'상품코드':<12} | {'중분류':^8} | {'판매일':>6} | {'판매율':>7} | {'총판매':>8} | {'일평균':>8}")
        print("-" * 80)

        for i, item in enumerate(intermittent[:20], 1):
            print(f"{item['item_cd']:<12} | {item['mid_cd']:^8} | "
                  f"{item['sell_days']:>6}일 | {item['sell_ratio']:>6.1%} | "
                  f"{item['total_sales']:>8}개 | {item['avg_sales']:>8.2f}개")

        if len(intermittent) > 20:
            print(f"... (외 {len(intermittent) - 20}개)")

    # 카테고리별 간헐적 수요 분포
    print("\n" + "=" * 80)
    print("[카테고리별 간헐적 수요 분포]")
    print("=" * 80)

    category_stats = defaultdict(lambda: {'very': 0, 'normal': 0, 'total': 0})

    for item in very_intermittent:
        category_stats[item['mid_cd']]['very'] += 1
        category_stats[item['mid_cd']]['total'] += 1

    for item in intermittent:
        category_stats[item['mid_cd']]['normal'] += 1
        category_stats[item['mid_cd']]['total'] += 1

    for item in normal:
        category_stats[item['mid_cd']]['total'] += 1

    # 간헐적 수요 비율로 정렬
    sorted_categories = sorted(
        category_stats.items(),
        key=lambda x: (x[1]['very'] + x[1]['normal']) / x[1]['total'] if x[1]['total'] > 0 else 0,
        reverse=True
    )

    print(f"{'중분류':^8} | {'총 상품':>8} | {'매우 간헐':>10} | {'간헐':>8} | {'간헐 비율':>10}")
    print("-" * 80)

    for mid_cd, stats in sorted_categories[:20]:
        intermittent_count = stats['very'] + stats['normal']
        intermittent_ratio = intermittent_count / stats['total'] if stats['total'] > 0 else 0
        print(f"{mid_cd:^8} | {stats['total']:>8} | {stats['very']:>10} | "
              f"{stats['normal']:>8} | {intermittent_ratio:>9.1%}")

    # 권장 사항
    print("\n" + "=" * 80)
    print("[분석 결과 및 권장 사항]")
    print("=" * 80)

    very_ratio = len(very_intermittent) / total_items * 100 if total_items > 0 else 0
    inter_ratio = len(intermittent) / total_items * 100 if total_items > 0 else 0

    print(f"\n1. 간헐적 수요 상품 비율: {very_ratio + inter_ratio:.1f}%")
    print(f"   - 매우 간헐적 (< 30%): {very_ratio:.1f}%")
    print(f"   - 간헐적 (30-60%): {inter_ratio:.1f}%")

    if very_ratio > 10:
        print(f"\n2. [경고] 매우 간헐적 수요 상품이 {very_ratio:.1f}%로 높습니다!")
        print("   현재 알고리즘은 이러한 상품에 대해 발주를 거의 하지 않습니다.")
        print("   → 최소 안전재고(ROP) 정책 도입 필요")

    if inter_ratio > 20:
        print(f"\n3. [주의] 간헐적 수요 상품이 {inter_ratio:.1f}%입니다.")
        print("   간헐적 보정 알고리즘이 적용되지만, 품절 위험이 있습니다.")
        print("   → 카테고리별 최소 발주량 설정 권장")

    print("\n4. 권장 개선 사항:")
    print("   [1] 최소 재고 정책: 판매일 < 30%면 안전재고 1개 유지")
    print("   [2] 재주문점 방식: 재고 0개면 무조건 1개 발주")
    print("   [3] 간헐적 수요 특별 처리: 판매일 < 20%는 별도 알고리즘")
    print("   [4] 최소 발주량: 예측이 0.1개 이상이면 1개 발주")

    conn.close()
    print("\n" + "=" * 80)
    print("[완료] 분석 완료")
    print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="간헐적 수요 상품 분석")
    parser.add_argument("--store-id", type=str, help="점포 코드 (예: 46513)")
    parser.add_argument("--days", type=int, default=182, help="분석 기간 (일, 기본값: 182)")
    parser.add_argument("--min-records", type=int, default=10, help="최소 레코드 수 (기본값: 10)")

    args = parser.parse_args()

    analyze_intermittent_items(
        store_id=args.store_id,
        days=args.days,
        min_records=args.min_records
    )
