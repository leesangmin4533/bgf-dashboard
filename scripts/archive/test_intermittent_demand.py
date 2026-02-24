"""
간헐적 수요 상품 테스트

한 달에 한 번 또는 두 달에 한 번 팔리는 상품의 예측값 계산
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def calculate_weighted_avg(sales_history):
    """가중 이동평균 계산"""
    weights = {
        "day_1": 0.4,
        "day_2": 0.3,
        "day_3": 0.2,
        "day_4_7": 0.1
    }

    total_weight = 0.0
    weighted_sum = 0.0

    for i, qty in enumerate(sales_history):
        if i == 0:
            w = weights["day_1"]
        elif i == 1:
            w = weights["day_2"]
        elif i == 2:
            w = weights["day_3"]
        else:
            w = weights["day_4_7"] / max(len(sales_history) - 3, 1)

        weighted_sum += qty * w
        total_weight += w

    return weighted_sum / total_weight if total_weight > 0 else 0.0


def simulate_intermittent(sales_history, description):
    """간헐적 수요 시뮬레이션"""
    print(f"\n[{description}]")
    print("-" * 70)

    # 판매일 비율 계산
    sell_days = sum(1 for qty in sales_history if qty > 0)
    total_days = len(sales_history)
    sell_day_ratio = sell_days / total_days

    # 가중 평균
    avg = calculate_weighted_avg(sales_history)

    # 간헐적 보정 (판매일 비율 < 60%)
    threshold = 0.6
    if sell_day_ratio < threshold:
        adjusted = avg * sell_day_ratio
        intermittent_applied = True
    else:
        adjusted = avg
        intermittent_applied = False

    # 발주 판단
    if adjusted < 0.5:
        order_qty = 0
        order_status = "발주 안 함 (품절 위험!)"
    else:
        order_qty = 1
        order_status = "발주 1개"

    print(f"판매 패턴: {sales_history[:7]}... (총 {total_days}일)")
    print(f"판매일 수: {sell_days}/{total_days}일 ({sell_day_ratio:.1%})")
    print(f"가중 평균: {avg:.3f}개/일")

    if intermittent_applied:
        print(f"간헐적 보정: {avg:.3f} x {sell_day_ratio:.3f} = {adjusted:.3f}개/일")
    else:
        print(f"간헐적 보정: 적용 안 됨 (판매일 >= {threshold:.0%})")

    print(f"최종 예측: {adjusted:.3f}개/일")
    print(f"발주 결정: {order_status}")

    return {
        'avg': avg,
        'adjusted': adjusted,
        'order_qty': order_qty,
        'sell_day_ratio': sell_day_ratio
    }


print("=" * 70)
print("간헐적 수요 상품 예측 시뮬레이션")
print("=" * 70)

# 시나리오 1: 한 달에 한 번 (30일 중 1일)
# 14일 데이터: 13일 0개, 1일 1개
history_monthly = [0] * 13 + [1]
result1 = simulate_intermittent(history_monthly, "한 달에 한 번 팔리는 상품")

# 시나리오 2: 두 달에 한 번 (60일 중 1일)
# 14일 데이터: 모두 0개
history_bimonthly = [0] * 14
result2 = simulate_intermittent(history_bimonthly, "두 달에 한 번 팔리는 상품")

# 시나리오 3: 일주일에 한 번 (7일 중 1일)
# 14일 데이터: 2일 1개, 12일 0개
history_weekly = [0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1]
result3 = simulate_intermittent(history_weekly, "일주일에 한 번 팔리는 상품")

# 시나리오 4: 3일에 한 번 (정상적인 간헐적 수요)
# 14일 데이터: 4~5일 1개, 나머지 0개
history_every3days = [1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0]
result4 = simulate_intermittent(history_every3days, "3일에 한 번 팔리는 상품")

# 시나리오 5: 격일 (2일에 한 번)
# 14일 데이터: 7일 1개, 7일 0개
history_alternate = [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0]
result5 = simulate_intermittent(history_alternate, "격일(2일에 한 번) 팔리는 상품")

print("\n" + "=" * 70)
print("결론 및 문제점")
print("=" * 70)

print("\n[문제점]")
print("- 한 달에 한 번: 발주 안 함 -> 다음에 품절!")
print("- 두 달에 한 번: 발주 안 함 -> 품절!")
print("- 일주일에 한 번: 발주 안 함 -> 품절 위험")
print("- 3일에 한 번: 간신히 발주 0.16개 -> 발주 안 함")

print("\n[권장 개선 사항]")
print("1. 최소 재고 정책: 판매일 < 30%면 안전재고 1개 유지")
print("2. 재주문점 방식: 재고 0개면 무조건 1개 발주")
print("3. 간헐적 수요 특별 처리: 판매일 < 20%는 별도 알고리즘")
print("4. 최소 발주량: 예측이 0.1개 이상이면 1개 발주")
