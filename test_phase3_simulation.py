"""
Phase 3 시뮬레이션 테스트

드라이버 없이 비교 모드 동작을 시뮬레이션합니다.
실제 데이터와 유사한 시나리오로 테스트합니다.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent))

from src.collectors.order_prep_collector import OrderPrepCollector
from src.config.constants import PENDING_COMPARISON_MODE

print("=" * 72)
print("Phase 3 비교 모드 시뮬레이션 테스트")
print("=" * 72)
print()

# 설정 확인
print("[1] 설정 확인")
print(f"  PENDING_COMPARISON_MODE: {PENDING_COMPARISON_MODE}")
if not PENDING_COMPARISON_MODE:
    print("  [!] 경고: 비교 모드가 비활성화되어 있습니다!")
    print("  constants.py에서 PENDING_COMPARISON_MODE = True로 설정하세요.")
else:
    print("  [OK] 비교 모드 활성화됨")
print()

# Collector 초기화
collector = OrderPrepCollector(driver=None, save_to_db=False)

# 통계 초기화
collector._comparison_stats = {
    'total': 0, 'matches': 0, 'differences': 0,
    'simple_higher': 0, 'complex_higher': 0,
    'max_diff': 0, 'total_diff': 0,
    'cross_pattern_cases': 0, 'multiple_order_cases': 0,
}

print("[2] 테스트 시나리오 준비")
print()

# 시나리오 1: 일치하는 경우
today = datetime.now().strftime("%Y%m%d")
scenario1 = {
    'name': '시나리오 1: 단순 일치',
    'item_cd': 'TEST001',
    'item_nm': '도시락A',
    'history': [{'date': today, 'ord_qty': 2, 'buy_qty': 10}],
    'unit_qty': 10,
}

# 시나리오 2: 교차날짜 패턴
yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
scenario2 = {
    'name': '시나리오 2: 교차날짜 패턴',
    'item_cd': 'TEST002',
    'item_nm': '김밥B',
    'history': [
        {'date': today, 'ord_qty': 3, 'buy_qty': 0},      # 오늘 발주, 미입고
        {'date': yesterday, 'ord_qty': 0, 'buy_qty': 15}, # 어제 입고
    ],
    'unit_qty': 5,
}

# 시나리오 3: 다중 발주
two_days_ago = (datetime.now() - timedelta(days=2)).strftime("%Y%m%d")
scenario3 = {
    'name': '시나리오 3: 다중 발주',
    'item_cd': 'TEST003',
    'item_nm': '샌드위치C',
    'history': [
        {'date': today, 'ord_qty': 2, 'buy_qty': 0},          # 오늘 발주
        {'date': yesterday, 'ord_qty': 1, 'buy_qty': 5},      # 어제 발주
        {'date': two_days_ago, 'ord_qty': 1, 'buy_qty': 10},  # 2일 전 발주
    ],
    'unit_qty': 10,
}

# 시나리오 4: 완전 입고
scenario4 = {
    'name': '시나리오 4: 완전 입고',
    'item_cd': 'TEST004',
    'item_nm': '음료D',
    'history': [{'date': today, 'ord_qty': 5, 'buy_qty': 50}],
    'unit_qty': 10,
}

# 시나리오 5: 부분 입고
scenario5 = {
    'name': '시나리오 5: 부분 입고',
    'item_cd': 'TEST005',
    'item_nm': '라면E',
    'history': [{'date': yesterday, 'ord_qty': 3, 'buy_qty': 25}],
    'unit_qty': 10,
}

scenarios = [scenario1, scenario2, scenario3, scenario4, scenario5]

print("[3] 테스트 시나리오 실행")
print()

for i, scenario in enumerate(scenarios, 1):
    print(f"--- {scenario['name']} ---")
    print(f"  상품: {scenario['item_cd']} - {scenario['item_nm']}")
    print(f"  입수: {scenario['unit_qty']}개")
    print(f"  이력 건수: {len(scenario['history'])}건")

    # 비교 실행
    result_qty, _ = collector._calculate_pending_with_comparison(
        scenario['history'],
        scenario['unit_qty'],
        scenario['item_cd'],
        scenario['item_nm']
    )

    # 단순/복잡 방식 개별 실행 (비교용)
    simple_qty = collector._calculate_pending_simplified(
        scenario['history'],
        scenario['unit_qty']
    )

    print(f"  결과:")
    print(f"    단순화: {simple_qty}개")
    print(f"    복잡한: {result_qty}개")
    print(f"    차이: {result_qty - simple_qty:+d}개")
    print()

print("[4] 통계 요약")
print()
stats = collector._comparison_stats
print(f"  총 조회: {stats['total']}건")
print(f"  결과 일치: {stats['matches']}건 ({stats['matches']/stats['total']*100:.1f}%)")
print(f"  결과 불일치: {stats['differences']}건 ({stats['differences']/stats['total']*100:.1f}%)")
print()

if stats['differences'] > 0:
    print("[5] 차이 분석")
    print()
    print(f"  복잡 > 단순: {stats['complex_higher']}건")
    print(f"  단순 > 복잡: {stats['simple_higher']}건")
    print(f"  최대 차이: {stats['max_diff']}개")
    print(f"  평균 차이: {stats['total_diff']/stats['differences']:.1f}개")
    print()

    print("[6] 패턴 감지")
    print()
    print(f"  교차날짜 패턴: {stats['cross_pattern_cases']}건")
    print(f"  다중 발주 패턴: {stats['multiple_order_cases']}건")
    print()

print("[7] 권장 사항")
print()
match_rate = stats['matches'] / stats['total'] * 100 if stats['total'] > 0 else 0

if match_rate >= 90:
    print("  [OK] 일치율 90% 이상 -> 단순화 방식 적용 가능")
    print("  -> 2주 모니터링 후 Phase 4 (A/B 테스트) 진행")
elif match_rate >= 80:
    print("  [!] 일치율 80~90% -> 추가 모니터링 필요")
    print("  -> 1주일 더 비교 후 재평가")
else:
    print("  [X] 일치율 80% 미만 -> 단순화 방식 보류")
    print("  -> 복잡한 방식 유지 또는 lookback_days 조정")
print()

# 비교 로그 작성 시뮬레이션
print("[8] 비교 로그 작성 테스트")
print()

try:
    collector._write_comparison_log()

    # 로그 파일 확인
    from pathlib import Path
    log_dir = Path(__file__).parent / "data" / "logs"
    today_str = datetime.now().strftime("%Y-%m-%d")
    log_path = log_dir / f"pending_comparison_{today_str}.txt"

    if log_path.exists():
        print(f"  [OK] 비교 로그 생성 성공: {log_path}")
        print(f"  파일 크기: {log_path.stat().st_size} bytes")
        print()
        print("  [로그 내용 미리보기]")
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            for line in lines[:20]:  # 처음 20줄만
                print(f"  {line.rstrip()}")
    else:
        print(f"  [!] 로그 파일이 생성되지 않음: {log_path}")
except Exception as e:
    print(f"  [X] 로그 작성 실패: {e}")

print()
print("=" * 72)
print("Phase 3 시뮬레이션 테스트 완료")
print("=" * 72)
print()
print("다음 단계:")
print("  1. 실제 시스템에서 테스트: python scripts/run_auto_order.py --preview --max-items 10")
print("  2. 로그 확인: cat data/logs/pending_comparison_$(date +%Y-%m-%d).txt")
print("  3. 2주 모니터링 시작")
print()
