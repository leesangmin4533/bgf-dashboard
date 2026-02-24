"""
Phase 3 실제 시스템 테스트

실제 BGF 시스템에서 소량(10개)의 상품으로 비교 모드를 테스트합니다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sales_analyzer import SalesAnalyzer
from src.collectors.order_prep_collector import OrderPrepCollector
from src.db.repository import ProductDetailRepository
from src.config.constants import PENDING_COMPARISON_MODE
from src.utils.logger import get_logger

logger = get_logger(__name__)

print("=" * 72)
print("Phase 3 실제 시스템 테스트")
print("=" * 72)
print()

# 설정 확인
print("[1] 설정 확인")
print(f"  PENDING_COMPARISON_MODE: {PENDING_COMPARISON_MODE}")
if not PENDING_COMPARISON_MODE:
    print("  [경고] 비교 모드가 비활성화되어 있습니다!")
    print("  constants.py에서 PENDING_COMPARISON_MODE = True로 설정하세요.")
    sys.exit(1)
print("  [OK] 비교 모드 활성화됨")
print()

# 테스트할 상품 선택
print("[2] 테스트 상품 선택")
product_repo = ProductDetailRepository()

# 최근 판매 이력이 있는 상품 10개 선택
from src.db.repository import SalesRepository
sales_repo = SalesRepository()

# 최근 7일 판매량이 많은 상품 10개 선택
test_items = sales_repo.get_top_selling_items(days=7, limit=10)

if not test_items:
    print("  [경고] 테스트할 상품이 없습니다.")
    print("  먼저 판매 데이터를 수집하세요.")
    sys.exit(1)

print(f"  선택된 상품: {len(test_items)}개")
for i, item in enumerate(test_items[:5], 1):
    print(f"    {i}. {item['item_cd']} - {item.get('item_nm', '상품명 없음')}")
if len(test_items) > 5:
    print(f"    ... 외 {len(test_items) - 5}개")
print()

# BGF 로그인
print("[3] BGF 시스템 로그인")
analyzer = SalesAnalyzer()

try:
    analyzer.login()
    print("  [OK] 로그인 성공")
except Exception as e:
    print(f"  [오류] 로그인 실패: {e}")
    sys.exit(1)

print()

# 미입고 조회 시작
print("[4] 미입고 수량 조회 (비교 모드)")
print()

collector = OrderPrepCollector(analyzer.driver, save_to_db=True)

try:
    # 메뉴 이동
    if not collector.navigate_to_menu():
        print("  [오류] 메뉴 이동 실패")
        sys.exit(1)

    # 날짜 선택
    if not collector.select_order_date():
        print("  [오류] 날짜 선택 실패")
        sys.exit(1)

    print("  [OK] 메뉴 준비 완료")
    print()

    # 상품별 조회
    item_codes = [item['item_cd'] for item in test_items]

    collector._pending_log_entries = []
    collector._comparison_stats = {
        'total': 0, 'matches': 0, 'differences': 0,
        'simple_higher': 0, 'complex_higher': 0,
        'max_diff': 0, 'total_diff': 0,
        'cross_pattern_cases': 0, 'multiple_order_cases': 0,
    }

    print(f"  조회 시작: {len(item_codes)}개 상품")
    print()

    results = {}
    for i, item_cd in enumerate(item_codes, 1):
        print(f"  [{i}/{len(item_codes)}] {item_cd} 조회 중...")
        result = collector.collect_for_item(item_cd)
        results[item_cd] = result

        if result.get('success'):
            print(f"    -> 미입고: {result.get('pending_qty', 0)}개, "
                  f"재고: {result.get('current_stock', 0)}개")
        else:
            print(f"    -> 조회 실패")

        import time
        time.sleep(0.5)  # 요청 간 간격

    print()
    print("[5] 조회 결과 요약")
    print()

    success_count = sum(1 for r in results.values() if r.get('success'))
    print(f"  성공: {success_count}/{len(item_codes)}건")
    print()

    # 통계 출력
    stats = collector._comparison_stats
    if stats['total'] > 0:
        print("[6] 비교 통계")
        print()
        print(f"  총 조회: {stats['total']}건")
        print(f"  결과 일치: {stats['matches']}건 ({stats['matches']/stats['total']*100:.1f}%)")
        print(f"  결과 불일치: {stats['differences']}건 ({stats['differences']/stats['total']*100:.1f}%)")

        if stats['differences'] > 0:
            print()
            print("  [차이 분석]")
            print(f"    복잡 > 단순: {stats['complex_higher']}건")
            print(f"    단순 > 복잡: {stats['simple_higher']}건")
            print(f"    최대 차이: {stats['max_diff']}개")
            print(f"    평균 차이: {stats['total_diff']/stats['differences']:.1f}개")

            print()
            print("  [패턴 감지]")
            print(f"    교차날짜: {stats['cross_pattern_cases']}건")
            print(f"    다중 발주: {stats['multiple_order_cases']}건")

        print()

    # 비교 로그 저장
    print("[7] 비교 로그 저장")
    collector._write_comparison_log()

    from datetime import datetime
    log_path = Path(__file__).parent.parent / "data" / "logs" / f"pending_comparison_{datetime.now().strftime('%Y-%m-%d')}.txt"
    if log_path.exists():
        print(f"  [OK] 로그 저장 완료: {log_path}")
        print(f"  파일 크기: {log_path.stat().st_size} bytes")
    else:
        print(f"  [경고] 로그 파일이 생성되지 않음")

    print()

except Exception as e:
    logger.error(f"테스트 실행 오류: {e}", exc_info=True)
    print(f"  [오류] {e}")

finally:
    # 정리
    try:
        collector.close_menu()
    except Exception as e:
        pass

    try:
        analyzer.close()
    except Exception as e:
        pass

print()
print("=" * 72)
print("Phase 3 실제 시스템 테스트 완료")
print("=" * 72)
print()
print("다음 단계:")
print("  1. 로그 확인: cat data/logs/pending_comparison_$(date +%Y-%m-%d).txt")
print("  2. 시스템 로그 확인: grep '미입고차이' logs/bgf_system_*.log")
print("  3. 매일 07:00 자동 실행되므로 2주간 모니터링")
print()
