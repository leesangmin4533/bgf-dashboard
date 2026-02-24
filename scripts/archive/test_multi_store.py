"""
Phase 5: Multi-Store Architecture Integration Testing

테스트 시나리오:
1. 신규 매장 등록
2. 매장별 파라미터 생성 확인
3. 멀티 스토어 스케줄러 테스트 (dry-run)
4. 데이터 격리 검증
"""

import sys
from pathlib import Path
from datetime import datetime

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.models import init_db
from src.db.repository_multi_store import StoreRepository, StoreEvalParamsRepository
from src.prediction.eval_config import EvalConfig
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MultiStoreIntegrationTest:
    """멀티 스토어 통합 테스트"""

    def __init__(self):
        self.store_repo = StoreRepository()
        self.params_repo = StoreEvalParamsRepository()

    def test_1_add_test_store(self):
        """Test 1: 신규 매장 등록"""
        logger.info("=" * 80)
        logger.info("Test 1: 신규 매장 등록")
        logger.info("=" * 80)

        store_id = "99999"
        store_name = "테스트매장"

        # 기존 매장 확인
        existing = self.store_repo.get_store(store_id)
        if existing:
            logger.info(f"  [SKIP] 매장 {store_id}가 이미 존재합니다.")
            return True

        # 신규 매장 등록
        success = self.store_repo.add_store(
            store_id=store_id,
            store_name=store_name,
            location="서울 테스트구",
            store_type="테스트점"
        )

        if success:
            logger.info(f"  [OK] 매장 등록 성공: {store_id} ({store_name})")

            # 등록 확인
            store = self.store_repo.get_store(store_id)
            logger.info(f"  [확인] {store}")
            return True
        else:
            logger.error(f"  [ERROR] 매장 등록 실패")
            return False

    def test_2_store_params(self):
        """Test 2: 매장별 파라미터 생성"""
        logger.info("=" * 80)
        logger.info("Test 2: 매장별 파라미터 생성")
        logger.info("=" * 80)

        store_id = "99999"

        # 기본 템플릿에서 파라미터 로드
        default_params = self.params_repo.load_default_template()

        if not default_params:
            logger.warning("  [WARN] 기본 템플릿이 없습니다.")
            # 코드 기본값 사용
            config = EvalConfig()
            default_params = config.to_dict()

        logger.info(f"  [OK] 기본 파라미터 로드: {len(default_params)}개")

        # DB에 저장
        success = self.params_repo.save_params_bulk(store_id, default_params)

        if success:
            logger.info(f"  [OK] 매장 {store_id} 파라미터 DB 저장 완료")
        else:
            logger.error(f"  [ERROR] 파라미터 DB 저장 실패")
            return False

        # 파일로 저장
        success = self.params_repo.save_to_file(store_id, default_params)

        if success:
            logger.info(f"  [OK] 매장 {store_id} 파라미터 파일 저장 완료")
        else:
            logger.error(f"  [ERROR] 파라미터 파일 저장 실패")
            return False

        # EvalConfig로 로드 테스트
        config = EvalConfig.load(store_id=store_id)
        logger.info(f"  [OK] EvalConfig.load('{store_id}') 성공")
        logger.info(f"       - daily_avg_days: {config.daily_avg_days.value}")
        logger.info(f"       - weight_daily_avg: {config.weight_daily_avg.value}")

        return True

    def test_3_list_active_stores(self):
        """Test 3: 활성 매장 목록 조회"""
        logger.info("=" * 80)
        logger.info("Test 3: 활성 매장 목록 조회")
        logger.info("=" * 80)

        stores = self.store_repo.get_active_stores()

        logger.info(f"  [OK] 활성 매장: {len(stores)}개")
        for store in stores:
            logger.info(f"       - {store['store_id']}: {store['store_name']} ({store['location']})")

        return len(stores) > 0

    def test_4_parameter_isolation(self):
        """Test 4: 매장별 파라미터 격리 검증"""
        logger.info("=" * 80)
        logger.info("Test 4: 매장별 파라미터 격리 검증")
        logger.info("=" * 80)

        # 매장 46513 파라미터 로드
        config_46513 = EvalConfig.load(store_id="46513")
        logger.info("  [OK] 매장 46513 파라미터 로드")
        logger.info(f"       - daily_avg_days: {config_46513.daily_avg_days.value}")

        # 매장 99999 파라미터 로드
        config_99999 = EvalConfig.load(store_id="99999")
        logger.info("  [OK] 매장 99999 파라미터 로드")
        logger.info(f"       - daily_avg_days: {config_99999.daily_avg_days.value}")

        # 매장 99999 파라미터 수정
        config_99999.update_param("daily_avg_days", 21.0)
        config_99999.save(store_id="99999")
        logger.info("  [OK] 매장 99999 파라미터 수정 (daily_avg_days: 21.0)")

        # 다시 로드하여 격리 확인
        config_46513_reload = EvalConfig.load(store_id="46513")
        config_99999_reload = EvalConfig.load(store_id="99999")

        logger.info("  [검증] 파라미터 격리:")
        logger.info(f"       - 46513: {config_46513_reload.daily_avg_days.value}")
        logger.info(f"       - 99999: {config_99999_reload.daily_avg_days.value}")

        if config_46513_reload.daily_avg_days.value != config_99999_reload.daily_avg_days.value:
            logger.info("  [OK] 매장별 파라미터 격리 확인!")
            return True
        else:
            logger.error("  [ERROR] 파라미터 격리 실패 (동일한 값)")
            return False

    def test_5_cleanup_test_store(self):
        """Test 5: 테스트 매장 정리 (선택)"""
        logger.info("=" * 80)
        logger.info("Test 5: 테스트 매장 정리")
        logger.info("=" * 80)

        store_id = "99999"

        # 비활성화
        success = self.store_repo.deactivate_store(store_id)

        if success:
            logger.info(f"  [OK] 매장 {store_id} 비활성화 완료")
            logger.info(f"       활성 매장 목록에서 제외됩니다.")
            return True
        else:
            logger.error(f"  [ERROR] 매장 비활성화 실패")
            return False

    def run_all_tests(self):
        """전체 테스트 실행"""
        logger.info("")
        logger.info("=" * 80)
        logger.info("Multi-Store Architecture Integration Testing")
        logger.info("=" * 80)
        logger.info("")

        results = {}

        # Test 1: 신규 매장 등록
        try:
            results["test_1"] = self.test_1_add_test_store()
        except Exception as e:
            logger.error(f"Test 1 실패: {e}")
            results["test_1"] = False

        # Test 2: 매장별 파라미터 생성
        try:
            results["test_2"] = self.test_2_store_params()
        except Exception as e:
            logger.error(f"Test 2 실패: {e}")
            results["test_2"] = False

        # Test 3: 활성 매장 목록 조회
        try:
            results["test_3"] = self.test_3_list_active_stores()
        except Exception as e:
            logger.error(f"Test 3 실패: {e}")
            results["test_3"] = False

        # Test 4: 파라미터 격리 검증
        try:
            results["test_4"] = self.test_4_parameter_isolation()
        except Exception as e:
            logger.error(f"Test 4 실패: {e}")
            results["test_4"] = False

        # Test 5: 테스트 매장 정리
        try:
            results["test_5"] = self.test_5_cleanup_test_store()
        except Exception as e:
            logger.error(f"Test 5 실패: {e}")
            results["test_5"] = False

        # 결과 요약
        logger.info("")
        logger.info("=" * 80)
        logger.info("테스트 결과 요약")
        logger.info("=" * 80)

        total = len(results)
        passed = sum(1 for r in results.values() if r)
        failed = total - passed

        logger.info(f"총 테스트: {total}개")
        logger.info(f"  - 통과: {passed}개")
        logger.info(f"  - 실패: {failed}개")
        logger.info("")

        for test_name, result in results.items():
            status = "[OK]" if result else "[FAIL]"
            logger.info(f"  {status} {test_name}")

        logger.info("=" * 80)

        return failed == 0


def main():
    """메인 실행 함수"""
    # DB 초기화
    init_db()

    # 테스트 실행
    tester = MultiStoreIntegrationTest()
    success = tester.run_all_tests()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
