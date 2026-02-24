# -*- coding: utf-8 -*-
"""
특정 카테고리 상품 정보 수집 스크립트
- BGF 사이트에서 유통기한 정보 수집
"""

import sys
import time
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sales_analyzer import SalesAnalyzer
from src.collectors.product_info_collector import ProductInfoCollector
import sqlite3


def get_category_products(mid_cd: str) -> List[Tuple[str, str]]:
    """특정 카테고리 상품 목록 조회"""
    db_path = Path(__file__).parent.parent / "data" / "bgf_sales.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT p.item_cd, p.item_nm
        FROM products p
        LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
        WHERE p.mid_cd = ?
        AND (pd.expiration_days IS NULL OR pd.item_cd IS NULL)
        ORDER BY p.item_nm
    ''', (mid_cd,))
    products = cursor.fetchall()
    conn.close()
    return products


def main() -> None:
    parser = argparse.ArgumentParser(description="카테고리별 상품 정보 수집")
    parser.add_argument("--mid", "-m", type=str, required=True, help="중분류 코드 (예: 023)")
    parser.add_argument("--max", type=int, default=100, help="최대 수집 건수")
    args = parser.parse_args()

    mid_cd = args.mid
    max_items = args.max

    print("=" * 70)
    print(f"카테고리 {mid_cd} 상품 정보 수집")
    print(f"시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 수집 대상 상품
    products = get_category_products(mid_cd)

    if not products:
        print(f"\n카테고리 {mid_cd}에 수집할 상품이 없습니다.")
        return

    if len(products) > max_items:
        print(f"\n{len(products)}개 중 {max_items}개만 수집합니다.")
        products = products[:max_items]

    print(f"\n수집 대상: {len(products)}개 상품")

    # BGF 로그인
    print("\n[1] BGF 사이트 로그인 중...")
    analyzer = SalesAnalyzer()
    analyzer.setup_driver()
    analyzer.connect()
    time.sleep(1)

    if not analyzer.do_login():
        print("로그인 실패!")
        analyzer.close()
        return

    print("로그인 성공!")
    time.sleep(2)

    # 팝업 닫기
    analyzer.close_popup()
    time.sleep(1)

    # 상품 정보 수집
    print("\n[2] 상품 정보 수집 시작...")
    collector = ProductInfoCollector(analyzer.driver)

    item_codes = [p[0] for p in products]

    # prefetch_product_details 사용
    results = collector.prefetch_product_details(
        item_codes=item_codes,
        use_order_screen=False
    )

    # 결과 확인
    print("\n" + "=" * 70)
    print("[3] 수집 결과")
    print("=" * 70)

    # DB에서 결과 확인
    db_path = Path(__file__).parent.parent / "data" / "bgf_sales.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT p.item_cd, p.item_nm, pd.expiration_days
        FROM products p
        LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
        WHERE p.mid_cd = ?
        ORDER BY pd.expiration_days DESC NULLS LAST
    ''', (mid_cd,))
    all_products = cursor.fetchall()
    conn.close()

    with_exp = [p for p in all_products if p[2] is not None]
    without_exp = [p for p in all_products if p[2] is None]

    print(f"유통기한 있음: {len(with_exp)}개")
    print(f"유통기한 없음: {len(without_exp)}개")

    # 결과 파일 저장
    output_path = Path(__file__).parent.parent / "data" / f"category_{mid_cd}_info.txt"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"카테고리 {mid_cd} 상품 정보 수집 결과\n")
        f.write(f"수집 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 70 + "\n\n")

        f.write(f"유통기한 있음: {len(with_exp)}개\n")
        f.write("-" * 50 + "\n")
        for item_cd, item_nm, exp_days in with_exp:
            nm = (item_nm or '')[:40]
            f.write(f"  {nm}: {exp_days}일\n")

        if without_exp:
            f.write(f"\n유통기한 없음: {len(without_exp)}개\n")
            f.write("-" * 50 + "\n")
            for item_cd, item_nm, _ in without_exp:
                nm = (item_nm or '')[:40]
                f.write(f"  {nm}\n")

    print(f"\n결과 저장: {output_path}")

    analyzer.close()
    print("\n완료!")


if __name__ == "__main__":
    main()
