"""
호반점(46513) 6개월 데이터 수집 스크립트

동양점과 동일한 구조로 데이터 수집
DB에 이미 있는 날짜는 자동으로 건너뜀
"""

import sys
from pathlib import Path

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.collectors.historical_data_collector import HistoricalDataCollector
from src.db.models import get_connection
from datetime import datetime


def check_existing_data(store_id: str):
    """기존 수집된 데이터 확인"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*) as count,
               MIN(sales_date) as first_date,
               MAX(sales_date) as last_date,
               COUNT(DISTINCT sales_date) as date_count
        FROM daily_sales
        WHERE store_id = ?
    """, (store_id,))

    row = cursor.fetchone()
    conn.close()

    return {
        'total_records': row['count'],
        'first_date': row['first_date'],
        'last_date': row['last_date'],
        'unique_dates': row['date_count']
    }


def collect_6months_data():
    """6개월 데이터 수집 실행"""

    store_id = "46513"
    store_name = "이천호반베르디움점"

    print("=" * 80)
    print(f"{store_name} 6개월 데이터 수집")
    print("=" * 80)
    print()

    # 기존 데이터 확인
    existing = check_existing_data(store_id)
    print(f"[기존 데이터 확인]")
    print(f"  총 레코드: {existing['total_records']:,}건")
    print(f"  기간: {existing['first_date']} ~ {existing['last_date']}")
    print(f"  수집된 날짜 수: {existing['unique_dates']}일")
    print()

    # 수집기 생성
    collector = HistoricalDataCollector(
        store_id=store_id,
        store_name=store_name
    )

    # 6개월 데이터 수집
    # force=False: 이미 수집된 날짜는 건너뜀 (중복 방지)
    print(f"[수집 시작] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  점포: {store_name} ({store_id})")
    print(f"  기간: 6개월")
    print(f"  모드: 중복 제외 (force=False)")
    print()

    result = collector.collect_historical_data(
        months=6,
        batch_size=30,  # 30일씩 수집
        force=False,    # 이미 수집된 날짜는 건너뜀
        auto_confirm=True  # 자동 확인
    )

    print()
    print("=" * 80)
    print("[수집 완료]")
    print("=" * 80)
    print(f"  성공: {result.get('success', 0)}일")
    print(f"  실패: {result.get('failed', 0)}일")
    print(f"  건너뜀: {result.get('skipped', 0)}일 (이미 존재)")
    print(f"  성공률: {result.get('success_rate', 0):.1f}%")

    # 수집 후 데이터 확인
    after = check_existing_data(store_id)
    print()
    print(f"[수집 후 데이터]")
    print(f"  총 레코드: {after['total_records']:,}건")
    print(f"  기간: {after['first_date']} ~ {after['last_date']}")
    print(f"  수집된 날짜 수: {after['unique_dates']}일")
    print(f"  신규 추가: {after['total_records'] - existing['total_records']:,}건")
    print()

    # 중분류별 구성비 출력
    print_mid_category_composition(store_id, store_name, after['first_date'], after['last_date'])

    return result


def print_mid_category_composition(store_id: str, store_name: str, start_date: str, end_date: str):
    """중분류별 매출 구성비 출력"""

    conn = get_connection()
    cursor = conn.cursor()

    # 중분류별 집계
    cursor.execute("""
        SELECT
            p.mid_cd,
            COUNT(DISTINCT p.item_cd) as item_count,
            SUM(ds.sale_qty) as total_sales
        FROM daily_sales ds
        JOIN products p ON ds.item_cd = p.item_cd
        WHERE ds.store_id = ?
        AND ds.sale_qty > 0
        GROUP BY p.mid_cd
        ORDER BY total_sales DESC
    """, (store_id,))

    rows = cursor.fetchall()
    total_sales = sum(row['total_sales'] for row in rows)

    print("=" * 80)
    print(f"{store_name}({store_id}) 중분류별 매출 구성비 ({start_date} ~ {end_date})")
    print("=" * 80)
    print()
    print(f"기간: {start_date} ~ {end_date}")
    print(f"총 판매량: {total_sales:,}개")
    print(f"중분류 수: {len(rows)}개")
    print()
    print("-" * 80)
    print(f"{'중분류':^10} | {'상품수':^8} | {'판매량':^12} | {'구성비':^10} | {'일평균':^8}")
    print("-" * 80)

    # 날짜 수 계산
    from datetime import datetime
    start = datetime.strptime(start_date, '%Y-%m-%d')
    end = datetime.strptime(end_date, '%Y-%m-%d')
    days = (end - start).days + 1

    for row in rows:
        mid_cd = row['mid_cd']
        item_count = row['item_count']
        sales = row['total_sales']
        ratio = (sales / total_sales * 100) if total_sales > 0 else 0
        daily_avg = sales / days if days > 0 else 0

        print(f"{mid_cd:^10} | {item_count:>8} | {sales:>12,} | {ratio:>9.2f}% | {daily_avg:>8.1f}")

    print("-" * 80)
    print(f"{'합계':^10} | {sum(r['item_count'] for r in rows):>8} | {total_sales:>12,} | {'100.00%':>10} | {' ':^8}")
    print("=" * 80)
    print()

    conn.close()


if __name__ == "__main__":
    try:
        result = collect_6months_data()

        if result.get('success', 0) > 0:
            print()
            print("[완료] 데이터 수집 완료")
            print()
            print("다음 단계:")
            print("  1. 동양점과 중분류 구성비 비교")
            print("  2. 멀티 점포 환경 검증: python scripts/verify_multi_store_env.py")
            print("  3. ML 모델 수정 (Phase 2)")
            sys.exit(0)
        else:
            print()
            print("[실패] 데이터 수집 실패")
            sys.exit(1)

    except KeyboardInterrupt:
        print()
        print("[중단] 사용자에 의해 중단됨")
        sys.exit(1)
    except Exception as e:
        print()
        print(f"[오류] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
