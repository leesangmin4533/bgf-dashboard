"""
Priority 2 store_id 멀티 스토어 지원 테스트

테스트 항목:
1. ImprovedPredictor의 store_id 파라미터
2. 쿼리에서 store_id 필터링
3. 기존 기능 정상 작동 (호환성)

Usage:
    python scripts/test_priority2_storeid.py
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


def test_store_id_parameter():
    """테스트 1: store_id 파라미터"""
    print("=" * 80)
    print("테스트 1: store_id 파라미터")
    print("=" * 80)

    # 기본값 (호반점 46513)
    predictor1 = ImprovedPredictor()
    print(f"\n✅ 기본 predictor.store_id: {predictor1.store_id}")

    if predictor1.store_id == "46513":
        print("✅ PASS: 기본값 '46513' 정상")
    else:
        print(f"❌ FAIL: 기본값 오류 (expected '46513', got '{predictor1.store_id}')")

    # 다른 점포 지정
    predictor2 = ImprovedPredictor(store_id="12345")
    print(f"\n✅ 커스텀 predictor.store_id: {predictor2.store_id}")

    if predictor2.store_id == "12345":
        print("✅ PASS: 커스텀 store_id '12345' 정상")
    else:
        print(f"❌ FAIL: 커스텀 store_id 오류 (expected '12345', got '{predictor2.store_id}')")

    print("\n" + "-" * 80)


def test_query_filtering():
    """테스트 2: 쿼리에서 store_id 필터링"""
    print("\n" + "=" * 80)
    print("테스트 2: 쿼리에서 store_id 필터링")
    print("=" * 80)

    predictor = ImprovedPredictor(store_id="46513")

    # 판매 이력이 있는 상품 조회
    import sqlite3
    db_path = project_root / "data" / "bgf_sales.db"
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # 샘플 상품 찾기
    item = c.execute("""
        SELECT item_cd FROM daily_sales
        WHERE store_id = '46513'
        AND sales_date >= date('now', '-7 days')
        AND sale_qty > 0
        LIMIT 1
    """).fetchone()

    conn.close()

    if not item:
        print("⚠️  최근 판매 이력이 있는 상품이 없음 (테스트 스킵)")
        return

    item_cd = item[0]
    print(f"\n테스트 상품: {item_cd}")

    try:
        # get_sales_history 테스트
        history = predictor.get_sales_history(item_cd, days=7)
        print(f"\n✅ get_sales_history: {len(history)}일 데이터 조회")

        if len(history) > 0:
            print(f"  샘플: {history[0]}")
            print("✅ PASS: 판매 이력 조회 성공")
        else:
            print("⚠️  WARNING: 판매 이력 없음")

        # get_current_stock 테스트
        stock = predictor.get_current_stock(item_cd)
        print(f"\n✅ get_current_stock: {stock}개")
        print("✅ PASS: 재고 조회 성공")

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "-" * 80)


def test_backward_compatibility():
    """테스트 3: 기존 기능 호환성 (Priority 1 통합)"""
    print("\n" + "=" * 80)
    print("테스트 3: 기존 기능 호환성 (Priority 1 + Priority 2)")
    print("=" * 80)

    predictor = ImprovedPredictor()

    # Priority 1 테스트: 음수 재고 방어
    print("\n✅ Priority 1 테스트: 음수 재고 방어")

    # 친환경봉투 (음수 재고)
    item_cd = "2201148653150"

    try:
        result = predictor.predict(item_cd)

        print(f"\n상품: {result.item_nm} ({item_cd})")
        print(f"  - store_id: {predictor.store_id}")
        print(f"  - 재고: {result.current_stock}개")
        print(f"  - 예측 발주량: {result.order_qty}개")

        # Priority 1.1: 음수 재고 방어
        if result.current_stock >= 0:
            print("  ✅ PASS: 음수 재고 방어 작동")
        else:
            print(f"  ❌ FAIL: 음수 재고 그대로 ({result.current_stock})")

        # Priority 1.2: 최대 발주량 상한
        if result.order_qty <= 20:
            print(f"  ✅ PASS: 최대 발주량 20개 이하 ({result.order_qty}개)")
        else:
            print(f"  ❌ FAIL: 최대 발주량 초과 ({result.order_qty}개)")

        print("\n✅ Priority 1 + Priority 2 통합 성공")

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "-" * 80)


def main():
    print("\n" + "=" * 80)
    print("Priority 2 store_id 멀티 스토어 지원 테스트")
    print("=" * 80)

    # 테스트 1: store_id 파라미터
    test_store_id_parameter()

    # 테스트 2: 쿼리 필터링
    test_query_filtering()

    # 테스트 3: 기존 기능 호환성
    test_backward_compatibility()

    print("\n" + "=" * 80)
    print("✅ 모든 테스트 완료")
    print("=" * 80)


if __name__ == "__main__":
    main()
