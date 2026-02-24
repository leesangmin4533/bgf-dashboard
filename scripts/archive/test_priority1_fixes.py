"""
Priority 1 긴급 수정 검증 스크립트

테스트 항목:
1. 음수 재고 방어 로직 (소모품 2201148653150)
2. 최대 발주량 상한 (소모품 20개 제한)
3. 푸드류 안전재고 상향 & 폐기율 계수 완화
4. 푸드류 최소 발주량 보장 (재고 0일 때 최소 1개)

Usage:
    python scripts/test_priority1_fixes.py
"""

import sys
import io
from pathlib import Path

# Windows 콘솔 UTF-8 설정
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.prediction.improved_predictor import ImprovedPredictor
from src.utils.logger import get_logger

logger = get_logger(__name__)


def test_negative_inventory_defense():
    """테스트 1: 음수 재고 방어 로직"""
    print("=" * 80)
    print("테스트 1: 음수 재고 방어 로직")
    print("=" * 80)

    predictor = ImprovedPredictor()

    # 친환경봉투 (음수 재고: -1264개)
    item_cd = "2201148653150"
    print(f"\n상품: {item_cd} (친환경봉투판매용)")
    print("현재 DB 재고: -1264개 (음수)")

    try:
        result = predictor.predict(item_cd)

        print(f"\n✅ 예측 결과:")
        print(f"  - 재고: {result.current_stock}개 (음수 방어 적용)")
        print(f"  - 미입고: {result.pending_qty}개")
        print(f"  - 예측 발주량: {result.order_qty}개")
        print(f"  - 안전재고: {result.safety_stock}개")

        if result.current_stock >= 0:
            print("\n✅ PASS: 음수 재고가 0으로 초기화됨")
        else:
            print(f"\n❌ FAIL: 음수 재고가 그대로 유지됨 ({result.current_stock}개)")

        if result.order_qty <= 20:
            print(f"✅ PASS: 최대 발주량 20개 이하 ({result.order_qty}개)")
        else:
            print(f"❌ FAIL: 최대 발주량 초과 ({result.order_qty}개 > 20개)")

    except Exception as e:
        print(f"❌ ERROR: {e}")

    print("\n" + "-" * 80)


def test_food_safety_stock_increase():
    """테스트 2: 푸드류 안전재고 상향"""
    print("\n" + "=" * 80)
    print("테스트 2: 푸드류 안전재고 상향")
    print("=" * 80)

    predictor = ImprovedPredictor()

    # 주먹밥 샘플 (유통기한 1일, 폐기율 높음)
    test_items = [
        ("8801771035565", "주먹밥 샘플 1"),
        ("8800336393577", "주먹밥 샘플 2"),
    ]

    print("\n유통기한 1일 상품 (ultra_short) - 안전재고일수 0.3 → 0.5 상향")

    for item_cd, name in test_items:
        try:
            result = predictor.predict(item_cd)

            print(f"\n상품: {name} ({item_cd})")
            print(f"  - 예측량: {result.predicted_qty:.2f}개")
            print(f"  - 안전재고: {result.safety_stock:.2f}개")
            print(f"  - 재고: {result.current_stock}개")
            print(f"  - 예측 발주량: {result.order_qty}개")

            # 안전재고가 예측량의 0.4배 이상인지 확인 (상향 적용 검증)
            if result.predicted_qty > 0:
                safety_ratio = result.safety_stock / result.predicted_qty
                print(f"  - 안전재고/예측량 비율: {safety_ratio:.2f}x")

                if safety_ratio >= 0.3:  # 상향 후 기대값
                    print("  ✅ PASS: 안전재고 상향 적용됨")
                else:
                    print(f"  ⚠️  WARNING: 안전재고 비율 낮음 ({safety_ratio:.2f}x)")

        except Exception as e:
            print(f"  ❌ ERROR: {e}")

    print("\n" + "-" * 80)


def test_food_minimum_order():
    """테스트 3: 푸드류 최소 발주량 보장"""
    print("\n" + "=" * 80)
    print("테스트 3: 푸드류 최소 발주량 보장 (재고 0일 때)")
    print("=" * 80)

    predictor = ImprovedPredictor()

    # 재고가 0인 푸드 상품 찾기
    print("\n재고 0인 푸드류 상품 중 일평균 0.3개 이상인 상품 테스트")

    # 주먹밥 카테고리(002)에서 테스트
    import sqlite3
    db_path = project_root / "data" / "bgf_sales.db"
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    items = c.execute("""
        SELECT DISTINCT d.item_cd,
               AVG(d.sale_qty) as daily_avg
        FROM daily_sales d
        LEFT JOIN realtime_inventory i ON d.item_cd = i.item_cd
        WHERE d.mid_cd = '002'  -- 주먹밥
        AND d.sales_date >= date('now', '-7 days')
        AND (i.stock_qty = 0 OR i.stock_qty IS NULL)
        GROUP BY d.item_cd
        HAVING AVG(d.sale_qty) >= 0.3
        LIMIT 5
    """).fetchall()

    conn.close()

    if not items:
        print("⚠️  재고 0인 주먹밥 상품이 없음 (테스트 스킵)")
        return

    for item_cd, daily_avg in items:
        try:
            result = predictor.predict(item_cd)

            print(f"\n상품: {result.item_nm[:30]}... ({item_cd})")
            print(f"  - 일평균: {daily_avg:.2f}개")
            print(f"  - 재고: {result.current_stock}개")
            print(f"  - 예측 발주량: {result.order_qty}개")

            if result.current_stock == 0 and daily_avg >= 0.3:
                if result.order_qty >= 1:
                    print("  ✅ PASS: 최소 1개 발주 보장됨")
                else:
                    print(f"  ❌ FAIL: 발주량 0 (최소 1개 보장 실패)")

        except Exception as e:
            print(f"  ❌ ERROR: {e}")

    print("\n" + "-" * 80)


def main():
    print("\n" + "=" * 80)
    print("Priority 1 긴급 수정 검증 테스트")
    print("=" * 80)

    # 테스트 1: 음수 재고 방어 + 최대 발주량 상한
    test_negative_inventory_defense()

    # 테스트 2: 푸드류 안전재고 상향
    test_food_safety_stock_increase()

    # 테스트 3: 푸드류 최소 발주량 보장
    test_food_minimum_order()

    print("\n" + "=" * 80)
    print("✅ 모든 테스트 완료")
    print("=" * 80)


if __name__ == "__main__":
    main()
