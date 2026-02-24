"""
멀티 점포 환경 검증 스크립트

Phase 1에서 구축한 멀티 점포 환경이 올바르게 설정되었는지 검증합니다.
"""

import sys
from pathlib import Path

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.store_manager import get_store_manager, get_active_stores


def verify_environment():
    """환경 검증 실행"""

    print("=" * 80)
    print("멀티 점포 환경 검증")
    print("=" * 80)
    print()

    manager = get_store_manager()
    all_passed = True

    # 1. 점포 정보 확인
    print("[1] 점포 정보 확인")
    print("-" * 80)

    stores = get_active_stores()

    if len(stores) < 2:
        print(f"[FAIL] 활성 점포가 2개 미만입니다: {len(stores)}개")
        all_passed = False
    else:
        print(f"[PASS] 활성 점포: {len(stores)}개")

    for store in stores:
        print(f"  - [{store.store_id}] {store.store_name} ({store.location})")

    print()

    # 2. 점포별 설정 파일 확인
    print("[2] 점포별 설정 파일 확인")
    print("-" * 80)

    for store in stores:
        config_path = manager.get_store_eval_params_path(store.store_id)

        if not config_path.exists():
            print(f"[FAIL] {store.store_id} 설정 파일 없음: {config_path}")
            all_passed = False
        else:
            print(f"[PASS] {store.store_id} 설정 파일: {config_path.name}")

            # 설정 로드 테스트
            try:
                params = manager.load_store_eval_params(store.store_id)
                print(f"       파라미터 수: {len(params)}개")
            except Exception as e:
                print(f"[FAIL] 설정 로드 실패: {e}")
                all_passed = False

    print()

    # 3. 점포 ID 유효성 확인
    print("[3] 점포 ID 유효성 확인")
    print("-" * 80)

    test_ids = ["46513", "46704", "99999"]

    for test_id in test_ids:
        is_valid = manager.is_valid_store(test_id)
        expected = test_id in ["46513", "46704"]

        if is_valid == expected:
            status = "[PASS]"
        else:
            status = "[FAIL]"
            all_passed = False

        print(f"{status} {test_id}: {'유효' if is_valid else '무효'}")

    print()

    # 4. 기본 점포 ID 확인
    print("[4] 기본 점포 ID 확인")
    print("-" * 80)

    try:
        default_store_id = manager.get_default_store_id()
        print(f"[PASS] 기본 점포: {default_store_id}")
    except Exception as e:
        print(f"[FAIL] 기본 점포 조회 실패: {e}")
        all_passed = False

    print()

    # 5. DB 데이터 확인 (점포별 데이터 존재 여부)
    print("[5] DB 데이터 확인")
    print("-" * 80)

    from src.db.models import get_connection

    conn = get_connection()
    cursor = conn.cursor()

    for store in stores:
        cursor.execute("""
            SELECT COUNT(*) as count, MIN(sales_date) as first_date, MAX(sales_date) as last_date
            FROM daily_sales
            WHERE store_id = ?
        """, (store.store_id,))

        row = cursor.fetchone()

        if row['count'] > 0:
            print(f"[PASS] {store.store_id} 데이터: {row['count']:,}건")
            print(f"       기간: {row['first_date']} ~ {row['last_date']}")
        else:
            print(f"[WARN] {store.store_id} 데이터 없음 (수집 중일 수 있음)")

    conn.close()
    print()

    # 최종 결과
    print("=" * 80)
    if all_passed:
        print("✅ 모든 검증 통과!")
        print()
        print("다음 단계: Phase 2 (ML 모델 수정)")
        print("  - ImprovedPredictor에 store_id 추가")
        print("  - SQL 쿼리 수정")
        print("  - AutoOrderSystem 수정")
        return 0
    else:
        print("❌ 일부 검증 실패")
        print()
        print("확인 필요:")
        print("  - 점포 설정 파일 존재 여부")
        print("  - stores.json 설정")
        return 1


if __name__ == "__main__":
    sys.exit(verify_environment())
