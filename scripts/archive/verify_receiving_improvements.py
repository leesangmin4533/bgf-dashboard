"""
입고 데이터 추적 개선 사항 검증 스크립트

코드 리뷰에서 발견된 Critical 이슈들이 수정되었는지 확인합니다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.repository import OrderTrackingRepository, InventoryBatchRepository
from src.collectors.receiving_collector import ReceivingCollector
from src.utils.logger import get_logger

logger = get_logger(__name__)


def verify_repository_methods() -> bool:
    """필수 Repository 메서드 존재 확인"""
    logger.info("=" * 60)
    logger.info("1. Repository 메서드 존재 확인")
    logger.info("=" * 60)

    all_ok = True

    # OrderTrackingRepository 메서드 확인
    tracking_repo = OrderTrackingRepository()

    required_methods = [
        'save_order',
        'get_by_item_and_order_date',
        'update_receiving'
    ]

    for method_name in required_methods:
        if hasattr(tracking_repo, method_name):
            logger.info(f"  ✓ OrderTrackingRepository.{method_name} 존재")
        else:
            logger.error(f"  ✗ OrderTrackingRepository.{method_name} 없음")
            all_ok = False

    # InventoryBatchRepository 메서드 확인
    batch_repo = InventoryBatchRepository()

    if hasattr(batch_repo, 'create_batch'):
        logger.info(f"  ✓ InventoryBatchRepository.create_batch 존재")

        # 메서드 시그니처 확인
        import inspect
        sig = inspect.signature(batch_repo.create_batch)
        params = list(sig.parameters.keys())

        expected_params = [
            'self', 'item_cd', 'item_nm', 'mid_cd',
            'receiving_date', 'expiration_days', 'initial_qty', 'receiving_id'
        ]

        if params == expected_params:
            logger.info(f"    ✓ 메서드 시그니처 정확")
        else:
            logger.warning(f"    ! 메서드 시그니처 차이:")
            logger.warning(f"      예상: {expected_params}")
            logger.warning(f"      실제: {params}")
    else:
        logger.error(f"  ✗ InventoryBatchRepository.create_batch 없음")
        all_ok = False

    return all_ok


def verify_collector_methods() -> bool:
    """ReceivingCollector 메서드 존재 확인"""
    logger.info("\n" + "=" * 60)
    logger.info("2. ReceivingCollector 메서드 확인")
    logger.info("=" * 60)

    all_ok = True

    collector = ReceivingCollector()

    required_methods = [
        '_get_mid_cd',
        '_fallback_mid_cd',
        '_get_expiration_days'
    ]

    for method_name in required_methods:
        if hasattr(collector, method_name):
            logger.info(f"  ✓ ReceivingCollector.{method_name} 존재")
        else:
            logger.error(f"  ✗ ReceivingCollector.{method_name} 없음")
            all_ok = False

    return all_ok


def verify_fallback_mid_cd() -> bool:
    """_fallback_mid_cd 로직 확인"""
    logger.info("\n" + "=" * 60)
    logger.info("3. _fallback_mid_cd 로직 확인")
    logger.info("=" * 60)

    collector = ReceivingCollector()
    all_ok = True

    test_cases = [
        ("BGF푸드도시락", "도시락", "001"),
        ("BGF푸드", "주먹밥", "002"),
        ("BGF푸드", "해)게딱지장삼각2", "003"),
        ("BGF푸드", "샌드위치", "004"),
        ("BGF푸드", "햄버거", "005"),
        ("", "버거", "005"),
        ("BGF푸드", "빵", "012"),
    ]

    for cust_nm, item_nm, expected in test_cases:
        result = collector._fallback_mid_cd(cust_nm, item_nm)
        if result == expected:
            logger.info(f"  ✓ '{item_nm}' → {result}")
        else:
            logger.error(f"  ✗ '{item_nm}' → {result} (예상: {expected})")
            all_ok = False

    return all_ok


def verify_expiration_days() -> bool:
    """_get_expiration_days 로직 확인"""
    logger.info("\n" + "=" * 60)
    logger.info("4. _get_expiration_days 로직 확인")
    logger.info("=" * 60)

    collector = ReceivingCollector()
    all_ok = True

    test_cases = [
        ("001", 1),   # 도시락
        ("002", 1),   # 주먹밥
        ("003", 1),   # 김밥
        ("004", 2),   # 샌드위치
        ("005", 1),   # 햄버거
        ("012", 3),   # 빵
        ("049", 30),  # 맥주 (비푸드)
        ("072", 30),  # 담배 (비푸드)
        ("", 30),     # 빈 값 (비푸드 기본값)
    ]

    for mid_cd, expected in test_cases:
        result = collector._get_expiration_days(mid_cd)
        if result == expected:
            logger.info(f"  ✓ mid_cd={mid_cd or '(빈값)'} → {result}일")
        else:
            logger.error(f"  ✗ mid_cd={mid_cd or '(빈값)'} → {result}일 (예상: {expected}일)")
            all_ok = False

    return all_ok


def verify_constants() -> bool:
    """상수 정의 확인"""
    logger.info("\n" + "=" * 60)
    logger.info("5. 상수 정의 확인")
    logger.info("=" * 60)

    all_ok = True

    try:
        from src.config.constants import (
            CATEGORY_EXPIRY_DAYS,
            DEFAULT_EXPIRY_DAYS_FOOD,
            DEFAULT_EXPIRY_DAYS_NON_FOOD
        )
        logger.info(f"  ✓ CATEGORY_EXPIRY_DAYS 정의됨 ({len(CATEGORY_EXPIRY_DAYS)}개)")
        logger.info(f"  ✓ DEFAULT_EXPIRY_DAYS_FOOD = {DEFAULT_EXPIRY_DAYS_FOOD}")
        logger.info(f"  ✓ DEFAULT_EXPIRY_DAYS_NON_FOOD = {DEFAULT_EXPIRY_DAYS_NON_FOOD}")

    except ImportError as e:
        logger.error(f"  ✗ 상수 import 실패: {e}")
        all_ok = False

    try:
        from src.config.timing import (
            RECEIVING_DATE_SELECT_WAIT,
            RECEIVING_DATA_LOAD_WAIT,
            COLLECTION_RETRY_WAIT,
            COLLECTION_MAX_RETRIES
        )
        logger.info(f"  ✓ RECEIVING_DATE_SELECT_WAIT = {RECEIVING_DATE_SELECT_WAIT}")
        logger.info(f"  ✓ RECEIVING_DATA_LOAD_WAIT = {RECEIVING_DATA_LOAD_WAIT}")
        logger.info(f"  ✓ COLLECTION_RETRY_WAIT = {COLLECTION_RETRY_WAIT}")
        logger.info(f"  ✓ COLLECTION_MAX_RETRIES = {COLLECTION_MAX_RETRIES}")

    except ImportError as e:
        logger.error(f"  ✗ 타이밍 상수 import 실패: {e}")
        all_ok = False

    return all_ok


def main():
    """전체 검증 실행"""
    logger.info("입고 데이터 추적 개선 검증 시작\n")

    results = []

    results.append(("Repository 메서드", verify_repository_methods()))
    results.append(("Collector 메서드", verify_collector_methods()))
    results.append(("fallback_mid_cd 로직", verify_fallback_mid_cd()))
    results.append(("expiration_days 로직", verify_expiration_days()))
    results.append(("상수 정의", verify_constants()))

    logger.info("\n" + "=" * 60)
    logger.info("검증 결과 요약")
    logger.info("=" * 60)

    all_passed = True
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        logger.info(f"{status}: {name}")
        if not passed:
            all_passed = False

    if all_passed:
        logger.info("\n모든 검증 통과!")
        return 0
    else:
        logger.error("\n일부 검증 실패")
        return 1


if __name__ == "__main__":
    exit(main())
