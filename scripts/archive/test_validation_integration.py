"""
Phase 3 검증 통합 테스트

Repository 레이어에 통합된 검증 시스템 테스트:
1. save_daily_sales() 호출 시 자동 검증 실행
2. validation_log 테이블에 결과 저장
3. 검증 실패 시 경고 로그 출력
"""

import sys
import io
from pathlib import Path
from datetime import datetime

# Windows 콘솔 인코딩 문제 해결
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 프로젝트 루트를 sys.path에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.db.repository import SalesRepository, ValidationRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


def test_validation_integration():
    """검증 통합 테스트"""

    print("=" * 60)
    print("Phase 3: 검증 통합 테스트")
    print("=" * 60)

    repo = SalesRepository()
    validation_repo = ValidationRepository()

    # 테스트 데이터 세트
    test_cases = [
        {
            "name": "정상 데이터",
            "data": [
                {
                    "ITEM_CD": "8801234567890",
                    "ITEM_NM": "테스트상품1",
                    "MID_CD": "001",
                    "MID_NM": "도시락",
                    "SALE_QTY": 10,
                    "ORD_QTY": 5,
                    "BUY_QTY": 5,
                    "DISUSE_QTY": 0,
                    "STOCK_QTY": 15,
                }
            ],
            "expected_valid": True,
        },
        {
            "name": "잘못된 상품코드 (10자리)",
            "data": [
                {
                    "ITEM_CD": "8801234567",  # 10자리 (13자리여야 함)
                    "ITEM_NM": "테스트상품2",
                    "MID_CD": "002",
                    "MID_NM": "주먹밥",
                    "SALE_QTY": 5,
                    "ORD_QTY": 3,
                    "BUY_QTY": 3,
                    "DISUSE_QTY": 0,
                    "STOCK_QTY": 8,
                }
            ],
            "expected_valid": False,
        },
        {
            "name": "음수 수량",
            "data": [
                {
                    "ITEM_CD": "8809876543210",
                    "ITEM_NM": "테스트상품3",
                    "MID_CD": "003",
                    "MID_NM": "김밥",
                    "SALE_QTY": -5,  # 음수 (0 이상이어야 함)
                    "ORD_QTY": 2,
                    "BUY_QTY": 2,
                    "DISUSE_QTY": 0,
                    "STOCK_QTY": 10,
                }
            ],
            "expected_valid": False,
        },
        {
            "name": "수량 범위 초과",
            "data": [
                {
                    "ITEM_CD": "8801111222333",
                    "ITEM_NM": "테스트상품4",
                    "MID_CD": "004",
                    "MID_NM": "샌드위치",
                    "SALE_QTY": 600,  # 500 초과
                    "ORD_QTY": 1500,  # 1000 초과
                    "BUY_QTY": 100,
                    "DISUSE_QTY": 0,
                    "STOCK_QTY": 2500,  # 2000 초과
                }
            ],
            "expected_valid": False,
        },
    ]

    sales_date = datetime.now().strftime("%Y-%m-%d")
    store_id = "46704"

    results = []

    for i, test_case in enumerate(test_cases, 1):
        print(f"\n[테스트 {i}] {test_case['name']}")
        print("-" * 60)

        try:
            # 데이터 저장 (검증 자동 실행)
            stats = repo.save_daily_sales(
                sales_data=test_case["data"],
                sales_date=sales_date,
                store_id=store_id,
                enable_validation=True  # 검증 활성화
            )

            print(f"[OK] 저장 완료: {stats}")

            # 검증 로그 확인
            recent_errors = validation_repo.get_recent_errors(days=1, limit=10)

            if recent_errors:
                print(f"\n검증 오류 발견:")
                for error in recent_errors[-3:]:  # 최근 3개만 표시
                    print(f"  - {error['error_code']}: {error['error_message']}")
                    print(f"    상품: {error['affected_items']}")
            else:
                print(f"\n[OK] 검증 통과 (오류 없음)")

            # 검증 통계
            summary = validation_repo.get_validation_summary(days=1, store_id=store_id)
            print(f"\n검증 통계: {summary}")

            # 예상 결과와 비교
            has_errors = len(recent_errors) > 0
            is_valid = not has_errors

            if is_valid == test_case["expected_valid"]:
                print(f"\n[OK] 테스트 성공 (예상: {test_case['expected_valid']}, 실제: {is_valid})")
                results.append(True)
            else:
                print(f"\n[FAIL] 테스트 실패 (예상: {test_case['expected_valid']}, 실제: {is_valid})")
                results.append(False)

        except Exception as e:
            print(f"[FAIL] 오류 발생: {e}")
            results.append(False)

    # 최종 결과
    print("\n" + "=" * 60)
    print("최종 결과")
    print("=" * 60)
    print(f"성공: {sum(results)}/{len(results)}")
    print(f"실패: {len(results) - sum(results)}/{len(results)}")

    if all(results):
        print("\n[OK] 모든 테스트 통과!")
    else:
        print("\n[WARN] 일부 테스트 실패")

    return all(results)


if __name__ == "__main__":
    success = test_validation_integration()
    sys.exit(0 if success else 1)
