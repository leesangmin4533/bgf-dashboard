"""
동양점(46704) 6개월 중분류 매출 데이터 수집

호반점과 동일한 DB 구조로 데이터 수집 및 저장
- daily_sales 테이블에 store_id='46704'로 저장
- 중분류별 판매 데이터 수집
- 수집 후 구성비 자동 계산 및 출력
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.collectors.historical_data_collector import HistoricalDataCollector
from src.db.models import get_connection
from src.utils.logger import get_logger
from datetime import datetime, timedelta

logger = get_logger(__name__)


def collect_6months_data():
    """동양점 6개월 데이터 수집"""

    print("=" * 80)
    print("동양점(46704) 6개월 중분류 매출 데이터 수집")
    print("=" * 80)

    # HistoricalDataCollector 초기화
    collector = HistoricalDataCollector(
        store_id="46704",
        store_name="이천동양점"
    )

    # 6개월 데이터 수집 실행
    result = collector.collect_historical_data(
        months=6,
        batch_size=30,
        force=False,  # 이미 수집된 날짜는 건너뛰기
        auto_confirm=True  # 자동 진행
    )

    return result


def calculate_mid_category_composition(months: int = 6):
    """중분류별 매출 구성비 계산 및 출력

    Args:
        months: 분석할 개월 수
    """
    conn = get_connection()
    cursor = conn.cursor()

    # 6개월 날짜 범위 계산
    end_date = datetime.now().date() - timedelta(days=1)
    start_date = end_date - timedelta(days=30 * months)

    print("\n" + "=" * 80)
    print(f"동양점(46704) 중분류별 매출 구성비 ({start_date} ~ {end_date})")
    print("=" * 80)

    # 중분류별 판매량 합계 조회
    cursor.execute('''
        SELECT
            mc.mid_cd,
            mc.mid_nm,
            COUNT(DISTINCT ds.item_cd) as item_count,
            SUM(ds.sale_qty) as total_sale_qty,
            COUNT(DISTINCT ds.sales_date) as sales_days
        FROM daily_sales ds
        JOIN mid_categories mc ON ds.mid_cd = mc.mid_cd
        WHERE ds.store_id = '46704'
          AND ds.sales_date BETWEEN ? AND ?
        GROUP BY mc.mid_cd, mc.mid_nm
        ORDER BY total_sale_qty DESC
    ''', (start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")))

    rows = cursor.fetchall()

    if not rows:
        print("\n[경고] 동양점 데이터가 없습니다. 먼저 데이터 수집을 실행하세요.")
        conn.close()
        return

    # 전체 판매량 계산
    total_sales = sum(row['total_sale_qty'] for row in rows)

    # 결과 출력
    print(f"\n기간: {start_date} ~ {end_date} ({months}개월)")
    print(f"전체 판매량: {total_sales:,}개")
    print(f"중분류 수: {len(rows)}개")
    print("\n" + "-" * 80)
    print(f"{'중분류':^10} | {'중분류명':^15} | {'상품수':>6} | {'판매량':>10} | {'구성비':>8} | {'일평균':>8}")
    print("-" * 80)

    for row in rows:
        mid_cd = row['mid_cd']
        mid_nm = row['mid_nm']
        item_count = row['item_count']
        sale_qty = row['total_sale_qty']
        sales_days = row['sales_days']

        # 구성비 계산
        ratio = (sale_qty / total_sales * 100) if total_sales > 0 else 0

        # 일평균 판매량
        daily_avg = sale_qty / sales_days if sales_days > 0 else 0

        print(f"{mid_cd:^10} | {mid_nm:^15} | {item_count:>6} | {sale_qty:>10,} | {ratio:>7.2f}% | {daily_avg:>8.1f}")

    print("-" * 80)
    print(f"{'합계':^10} | {' ':^15} | {sum(r['item_count'] for r in rows):>6} | {total_sales:>10,} | {100.0:>7.2f}% | {' ':>8}")
    print("=" * 80)

    # 일별 데이터 수 확인
    cursor.execute('''
        SELECT
            COUNT(DISTINCT sales_date) as date_count,
            MIN(sales_date) as first_date,
            MAX(sales_date) as last_date
        FROM daily_sales
        WHERE store_id = '46704'
          AND sales_date BETWEEN ? AND ?
    ''', (start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")))

    stats = cursor.fetchone()

    print(f"\n[수집 현황]")
    print(f"  수집 일수: {stats['date_count']}일")
    print(f"  최초 날짜: {stats['first_date']}")
    print(f"  최근 날짜: {stats['last_date']}")
    print(f"  예상 일수: {(end_date - start_date).days + 1}일")

    expected_days = (end_date - start_date).days + 1
    completion_rate = (stats['date_count'] / expected_days * 100) if expected_days > 0 else 0
    print(f"  완성률: {completion_rate:.1f}%")

    conn.close()


def compare_stores(months: int = 6):
    """호반점과 동양점 비교

    Args:
        months: 비교할 개월 수
    """
    conn = get_connection()
    cursor = conn.cursor()

    end_date = datetime.now().date() - timedelta(days=1)
    start_date = end_date - timedelta(days=30 * months)

    print("\n" + "=" * 80)
    print(f"점포별 매출 비교 ({start_date} ~ {end_date})")
    print("=" * 80)

    for store_id, store_name in [("46513", "호반점"), ("46704", "동양점")]:
        cursor.execute('''
            SELECT
                COUNT(DISTINCT sales_date) as date_count,
                COUNT(DISTINCT mid_cd) as mid_count,
                COUNT(DISTINCT item_cd) as item_count,
                SUM(sale_qty) as total_sale_qty
            FROM daily_sales
            WHERE store_id = ?
              AND sales_date BETWEEN ? AND ?
        ''', (store_id, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")))

        row = cursor.fetchone()

        print(f"\n[{store_name} ({store_id})]")
        print(f"  수집 일수: {row['date_count']:3d}일")
        print(f"  중분류 수: {row['mid_count']:3d}개")
        print(f"  상품 수:   {row['item_count']:3d}개")
        print(f"  총 판매량: {row['total_sale_qty']:,}개")

        if row['date_count'] > 0:
            daily_avg = row['total_sale_qty'] / row['date_count']
            print(f"  일평균:    {daily_avg:.1f}개")

    print("\n" + "=" * 80)
    conn.close()


def main():
    """메인 실행 함수"""
    import argparse

    parser = argparse.ArgumentParser(
        description="동양점(46704) 6개월 중분류 매출 데이터 수집 및 분석",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예제:
  # 6개월 데이터 수집 + 구성비 계산
  python scripts/collect_dongyang_6months.py

  # 구성비만 계산 (수집 생략)
  python scripts/collect_dongyang_6months.py --analysis-only

  # 호반점과 비교
  python scripts/collect_dongyang_6months.py --compare

  # 3개월 데이터만 분석
  python scripts/collect_dongyang_6months.py --analysis-only --months 3
        """
    )

    parser.add_argument(
        "--analysis-only",
        action="store_true",
        help="데이터 수집 없이 구성비 분석만 실행"
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="호반점과 동양점 비교 분석"
    )
    parser.add_argument(
        "--months",
        type=int,
        default=6,
        help="분석할 개월 수 (기본: 6개월)"
    )

    args = parser.parse_args()

    try:
        # 1. 데이터 수집 (--analysis-only가 아닐 때만)
        if not args.analysis_only:
            result = collect_6months_data()

            if not result.get('success'):
                print("\n[경고] 데이터 수집 실패 또는 부분 실패")
                if result.get('failed_dates'):
                    print(f"실패한 날짜: {len(result['failed_dates'])}일")

        # 2. 중분류 구성비 계산
        calculate_mid_category_composition(months=args.months)

        # 3. 점포 비교 (--compare 옵션)
        if args.compare:
            compare_stores(months=args.months)

        print("\n[완료] 스크립트 실행 완료")
        return 0

    except KeyboardInterrupt:
        print("\n\n[취소] 사용자가 중단했습니다.")
        return 1
    except Exception as e:
        print(f"\n[오류] {e}")
        logger.exception("스크립트 실행 중 오류 발생")
        return 1


if __name__ == "__main__":
    sys.exit(main())
