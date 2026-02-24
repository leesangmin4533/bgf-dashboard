"""
6개월 분량 중분류별 매출 데이터 생성 스크립트

동양점 DB에 테스트용 중분류 매출 구성비 데이터 삽입
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
from datetime import datetime, timedelta
import random

from src.config.constants import (
    MID_DOSIRAK, MID_JUMEOKBAP, MID_GIMBAP, MID_SANDWICH, MID_HAMBURGER, MID_BREAD,
    MID_RAMEN_COOKED, MID_RAMEN_NOODLE,
    MID_BEER, MID_SOJU,
    MID_TOBACCO, MID_E_TOBACCO,
    MID_SUPPLIES
)

# 중분류별 일평균 판매 기준 (동양점 규모 가정)
MID_DAILY_AVG = {
    MID_DOSIRAK: 45,      # 도시락 - 고회전
    MID_JUMEOKBAP: 30,    # 주먹밥
    MID_GIMBAP: 25,       # 김밥
    MID_SANDWICH: 20,     # 샌드위치
    MID_HAMBURGER: 15,    # 햄버거
    MID_BREAD: 35,        # 빵
    MID_RAMEN_COOKED: 40, # 조리면
    MID_RAMEN_NOODLE: 25, # 면류
    MID_BEER: 60,         # 맥주
    MID_SOJU: 50,         # 소주
    MID_TOBACCO: 80,      # 담배 - 최고회전
    MID_E_TOBACCO: 30,    # 전자담배
    MID_SUPPLIES: 10,     # 소모품
}

# 요일별 판매 계수 (월=0, 일=6)
WEEKDAY_FACTOR = {
    0: 0.9,   # 월요일
    1: 0.95,  # 화요일
    2: 1.0,   # 수요일
    3: 1.05,  # 목요일
    4: 1.15,  # 금요일
    5: 1.2,   # 토요일
    6: 1.1,   # 일요일
}

def generate_sales_data(months: int = 6):
    """
    중분류별 6개월 매출 데이터 생성

    Args:
        months: 생성할 개월 수 (기본 6개월)

    Returns:
        List[Dict]: 생성된 매출 데이터
    """
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=30 * months)

    sales_data = []
    current_date = start_date

    print(f"데이터 생성 기간: {start_date} ~ {end_date}")
    print(f"총 {(end_date - start_date).days}일")
    print("=" * 60)

    while current_date <= end_date:
        weekday = current_date.weekday()
        weekday_factor = WEEKDAY_FACTOR.get(weekday, 1.0)

        for mid_cd, base_qty in MID_DAILY_AVG.items():
            # 요일 계수 적용
            expected_qty = int(base_qty * weekday_factor)

            # 랜덤 변동 ±20%
            variation = random.uniform(0.8, 1.2)
            sale_qty = max(0, int(expected_qty * variation))

            # 발주량 (판매량의 80~120%)
            ord_qty = int(sale_qty * random.uniform(0.8, 1.2))

            # 가상 상품코드 (중분류코드 + 일련번호)
            item_cd = f"88{mid_cd}00001"

            # 입고/재고는 판매량과 유사하게 설정
            buy_qty = ord_qty
            stock_qty = random.randint(0, int(base_qty * 2))  # 평균의 2배 이내

            sales_data.append({
                'sales_date': current_date.strftime('%Y-%m-%d'),
                'item_cd': item_cd,
                'mid_cd': mid_cd,
                'sale_qty': sale_qty,
                'ord_qty': ord_qty,
                'buy_qty': buy_qty,
                'disuse_qty': 0,
                'stock_qty': stock_qty,
                'promo_type': '',
                'store_id': '46704',  # 동양점
            })

        current_date += timedelta(days=1)

    print(f"총 {len(sales_data)}건 데이터 생성 완료")
    return sales_data

def insert_to_db(sales_data: list, db_path: str = "data/bgf_sales.db"):
    """
    DB에 데이터 삽입

    Args:
        sales_data: 생성된 매출 데이터
        db_path: DB 파일 경로
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # 1. 기존 데이터 삭제 (선택)
        print("\n기존 데이터 삭제 여부를 선택하세요:")
        print("1. 기존 데이터 유지 (추가만)")
        print("2. 기존 데이터 삭제 후 삽입")
        choice = input("선택 (1/2): ").strip()

        if choice == '2':
            cursor.execute("DELETE FROM daily_sales")
            print(f"[OK] 기존 데이터 {cursor.rowcount}건 삭제")

        # 2. 데이터 삽입
        insert_sql = """
            INSERT OR REPLACE INTO daily_sales
            (collected_at, sales_date, item_cd, mid_cd, sale_qty, ord_qty,
             buy_qty, disuse_qty, stock_qty, created_at, promo_type, store_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        inserted = 0
        for data in sales_data:
            cursor.execute(insert_sql, (
                now,                      # collected_at
                data['sales_date'],
                data['item_cd'],
                data['mid_cd'],
                data['sale_qty'],
                data['ord_qty'],
                data['buy_qty'],
                data['disuse_qty'],
                data['stock_qty'],
                now,                      # created_at
                data['promo_type'],
                data['store_id']
            ))
            inserted += 1

            if inserted % 1000 == 0:
                print(f"진행 중... {inserted}/{len(sales_data)}건")

        conn.commit()
        print(f"\n[OK] 총 {inserted}건 데이터 삽입 완료")

        # 3. 중분류별 통계 출력
        print("\n" + "=" * 60)
        print("중분류별 판매 구성비 (최근 30일)")
        print("=" * 60)

        cursor.execute("""
            SELECT
                mid_cd,
                SUM(sale_qty) as total_qty,
                COUNT(DISTINCT sales_date) as days
            FROM daily_sales
            WHERE sales_date >= date('now', '-30 days')
            GROUP BY mid_cd
            ORDER BY total_qty DESC
        """)

        total_qty_all = sum(row[1] for row in cursor.fetchall())
        cursor.execute("""
            SELECT
                mid_cd,
                SUM(sale_qty) as total_qty,
                COUNT(DISTINCT sales_date) as days
            FROM daily_sales
            WHERE sales_date >= date('now', '-30 days')
            GROUP BY mid_cd
            ORDER BY total_qty DESC
        """)

        for row in cursor.fetchall():
            mid_cd, total_qty, days = row
            ratio = (total_qty / total_qty_all * 100) if total_qty_all > 0 else 0
            avg_qty = total_qty / days if days > 0 else 0
            print(f"{mid_cd}: {total_qty:,}개 ({ratio:.1f}%) | 일평균 {avg_qty:.1f}개")

    except Exception as e:
        conn.rollback()
        print(f"[ERROR] 오류 발생: {e}")
        raise
    finally:
        conn.close()

def main():
    """메인 실행 함수"""
    print("=" * 60)
    print("6개월 중분류 매출 데이터 생성기 (동양점)")
    print("=" * 60)
    print()

    # 1. 데이터 생성
    sales_data = generate_sales_data(months=6)

    # 2. DB 삽입
    insert_to_db(sales_data)

    print("\n" + "=" * 60)
    print("완료!")
    print("=" * 60)

if __name__ == "__main__":
    main()
