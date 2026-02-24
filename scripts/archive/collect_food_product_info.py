# -*- coding: utf-8 -*-
"""
푸드류(001~005) 상품 정보 수집 스크립트
- BGF 사이트에서 유통기한 정보 수집
"""

import sys
import time
from pathlib import Path
from datetime import datetime
from typing import List, Tuple

# 상위 경로 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sales_analyzer import SalesAnalyzer
from src.collectors.product_info_collector import ProductInfoCollector
import sqlite3


def get_food_products() -> List[Tuple[str, str, str]]:
    """푸드류 상품 목록 조회"""
    db_path = Path(__file__).parent.parent / "data" / "bgf_sales.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT p.item_cd, p.item_nm, p.mid_cd
        FROM products p
        WHERE p.mid_cd IN ('001', '002', '003', '004', '005')
        ORDER BY p.mid_cd, p.item_nm
    ''')
    products = cursor.fetchall()
    conn.close()
    return products


def main() -> None:
    print("=" * 70)
    print("푸드류(001~005) 상품 정보 수집")
    print(f"시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 푸드류 상품 목록
    products = get_food_products()
    item_codes = [p[0] for p in products]
    cat_names = {'001': '도시락', '002': '주먹밥', '003': '김밥', '004': '샌드위치', '005': '햄버거'}

    print(f"\n수집 대상: {len(products)}개 상품")
    for mid_cd in ['001', '002', '003', '004', '005']:
        cnt = len([p for p in products if p[2] == mid_cd])
        print(f"  {mid_cd} ({cat_names[mid_cd]}): {cnt}개")

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

    # 상품 정보 수집기
    print("\n[2] 상품 정보 수집 시작...")
    collector = ProductInfoCollector(analyzer.driver)

    # prefetch_product_details 메서드 사용 (강제 수집을 위해 DB 데이터 삭제하지 않고 진행)
    # 메서드가 DB에 자동으로 저장함
    results = collector.prefetch_product_details(
        item_codes=item_codes,
        use_order_screen=False  # 메인 화면 팝업 방식 사용
    )

    # 결과 확인
    print("\n" + "=" * 70)
    print("[3] 수집 결과 확인")
    print("=" * 70)

    # DB에서 수집된 결과 확인
    db_path = Path(__file__).parent.parent / "data" / "bgf_sales.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT p.mid_cd, p.item_cd, p.item_nm, pd.expiration_days
        FROM products p
        LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
        WHERE p.mid_cd IN ('001', '002', '003', '004', '005')
        ORDER BY p.mid_cd, pd.expiration_days DESC
    ''')
    all_products = cursor.fetchall()
    conn.close()

    # 결과 파일 저장
    output_path = Path(__file__).parent.parent / "data" / "food_product_info_collected.txt"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("푸드류 상품 정보 수집 결과\n")
        f.write(f"수집 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 80 + "\n\n")

        for mid_cd in ['001', '002', '003', '004', '005']:
            items = [p for p in all_products if p[0] == mid_cd]
            with_exp = [p for p in items if p[3] is not None]
            without_exp = [p for p in items if p[3] is None]

            f.write(f"\n[{mid_cd} {cat_names[mid_cd]}] 총 {len(items)}개\n")
            f.write("-" * 60 + "\n")
            f.write(f"  유통기한 있음: {len(with_exp)}개\n")
            f.write(f"  유통기한 없음: {len(without_exp)}개\n")

            if with_exp:
                f.write("\n  상품별 유통기한:\n")
                for _, item_cd, item_nm, exp_days in with_exp[:20]:  # 상위 20개만
                    nm = (item_nm or '')[:35]
                    f.write(f"    {nm}: {exp_days}일\n")
                if len(with_exp) > 20:
                    f.write(f"    ... 외 {len(with_exp) - 20}개\n")

        # 전체 요약
        total = len(all_products)
        with_exp_total = len([p for p in all_products if p[3] is not None])
        f.write("\n" + "=" * 80 + "\n")
        f.write(f"전체 요약: {total}개 중 {with_exp_total}개 유통기한 정보 보유\n")

    print(f"\n결과 저장: {output_path}")

    # 종료
    analyzer.close()
    print("\n완료!")


if __name__ == "__main__":
    main()
