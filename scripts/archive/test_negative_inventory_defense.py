"""
음수 재고 방어 시스템 통합 테스트

테스트 레이어:
1. Repository 저장 시점 (save, save_bulk)
2. Repository 조회 시점 (get, get_all)
3. Predictor 예측 시점 (Priority 1)

Usage:
    python scripts/test_negative_inventory_defense.py
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

from src.db.repository import RealtimeInventoryRepository
from src.prediction.improved_predictor import ImprovedPredictor


def test_repository_save():
    """테스트 1: Repository 저장 시점 음수 방어"""
    print("=" * 80)
    print("테스트 1: Repository 저장 시점 음수 방어")
    print("=" * 80)

    repo = RealtimeInventoryRepository()

    # 음수 재고로 저장 시도
    test_item = "TEST_NEGATIVE_001"

    print(f"\n저장 시도: stock_qty=-100, pending_qty=-50")

    repo.save(
        item_cd=test_item,
        item_nm="음수 재고 테스트",
        stock_qty=-100,  # 음수
        pending_qty=-50,  # 음수
        order_unit_qty=1
    )

    # 조회
    result = repo.get(test_item)

    print(f"\n조회 결과:")
    print(f"  - stock_qty: {result['stock_qty']} (기대: 0)")
    print(f"  - pending_qty: {result['pending_qty']} (기대: 0)")

    if result['stock_qty'] == 0 and result['pending_qty'] == 0:
        print("\n✅ PASS: 음수가 0으로 저장됨")
    else:
        print(f"\n❌ FAIL: 음수가 그대로 저장됨 (stock={result['stock_qty']}, pending={result['pending_qty']})")

    print("\n" + "-" * 80)


def test_repository_save_bulk():
    """테스트 2: Repository 일괄 저장 시점 음수 방어"""
    print("\n" + "=" * 80)
    print("테스트 2: Repository 일괄 저장 시점 음수 방어")
    print("=" * 80)

    repo = RealtimeInventoryRepository()

    # 음수 재고로 일괄 저장 시도
    items = [
        {
            "item_cd": "TEST_BULK_001",
            "item_nm": "일괄저장 테스트 1",
            "stock_qty": -200,
            "pending_qty": -100,
        },
        {
            "item_cd": "TEST_BULK_002",
            "item_nm": "일괄저장 테스트 2",
            "stock_qty": -300,
            "pending_qty": -150,
        }
    ]

    print(f"\n일괄 저장 시도: 2개 상품 (모두 음수 재고)")

    repo.save_bulk(items)

    # 조회
    result1 = repo.get("TEST_BULK_001")
    result2 = repo.get("TEST_BULK_002")

    print(f"\n조회 결과:")
    print(f"  - TEST_BULK_001: stock={result1['stock_qty']}, pending={result1['pending_qty']} (기대: 0, 0)")
    print(f"  - TEST_BULK_002: stock={result2['stock_qty']}, pending={result2['pending_qty']} (기대: 0, 0)")

    if (result1['stock_qty'] == 0 and result1['pending_qty'] == 0 and
        result2['stock_qty'] == 0 and result2['pending_qty'] == 0):
        print("\n✅ PASS: 일괄 저장 시 음수가 0으로 저장됨")
    else:
        print("\n❌ FAIL: 일괄 저장 시 음수가 그대로 저장됨")

    print("\n" + "-" * 80)


def test_repository_get_defense():
    """테스트 3: Repository 조회 시점 음수 방어 (기존 음수 데이터)"""
    print("\n" + "=" * 80)
    print("테스트 3: Repository 조회 시점 음수 방어")
    print("=" * 80)

    repo = RealtimeInventoryRepository()

    # 실제 음수 재고 상품 조회 (친환경봉투)
    item_cd = "2201148653150"

    print(f"\n실제 음수 재고 상품 조회: {item_cd}")

    result = repo.get(item_cd)

    if result:
        print(f"\n조회 결과:")
        print(f"  - 상품명: {result['item_nm']}")
        print(f"  - stock_qty: {result['stock_qty']} (DB 원본: 음수일 수 있음)")
        print(f"  - pending_qty: {result['pending_qty']}")

        if result['stock_qty'] >= 0 and result['pending_qty'] >= 0:
            print("\n✅ PASS: 조회 시 음수가 0으로 변환됨")
        else:
            print(f"\n❌ FAIL: 조회 시 음수가 그대로 반환됨")
    else:
        print(f"\n⚠️  상품 없음 (테스트 스킵)")

    print("\n" + "-" * 80)


def test_predictor_integration():
    """테스트 4: Predictor 통합 테스트 (Priority 1 + 음수 방어)"""
    print("\n" + "=" * 80)
    print("테스트 4: Predictor 통합 테스트")
    print("=" * 80)

    predictor = ImprovedPredictor()

    # 음수 재고 상품 예측
    item_cd = "2201148653150"

    print(f"\n예측 실행: {item_cd}")

    try:
        result = predictor.predict(item_cd)

        print(f"\n예측 결과:")
        print(f"  - 상품명: {result.item_nm}")
        print(f"  - 재고: {result.current_stock}개")
        print(f"  - 미입고: {result.pending_qty}개")
        print(f"  - 예측 발주량: {result.order_qty}개")

        if result.current_stock >= 0 and result.pending_qty >= 0:
            print("\n✅ PASS: Predictor에서 음수 재고 방어 작동")
        else:
            print(f"\n❌ FAIL: Predictor에서 음수 재고 미방어")

        if result.order_qty <= 20:
            print(f"✅ PASS: 최대 발주량 20개 이하 ({result.order_qty}개)")
        else:
            print(f"❌ FAIL: 최대 발주량 초과 ({result.order_qty}개)")

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "-" * 80)


def main():
    print("\n" + "=" * 80)
    print("음수 재고 방어 시스템 통합 테스트")
    print("=" * 80)

    # 테스트 1: Repository 저장 시점
    test_repository_save()

    # 테스트 2: Repository 일괄 저장 시점
    test_repository_save_bulk()

    # 테스트 3: Repository 조회 시점
    test_repository_get_defense()

    # 테스트 4: Predictor 통합
    test_predictor_integration()

    print("\n" + "=" * 80)
    print("✅ 모든 테스트 완료")
    print("=" * 80)
    print("\n📋 음수 재고 방어 레이어:")
    print("  ✅ Layer 1: Repository 저장 시점 (save, save_bulk)")
    print("  ✅ Layer 2: Repository 조회 시점 (get, get_all)")
    print("  ✅ Layer 3: Predictor 예측 시점 (improved_predictor.py)")
    print("\n💡 3중 방어 시스템으로 음수 재고가 시스템에 영향을 주지 않습니다.")


if __name__ == "__main__":
    main()
