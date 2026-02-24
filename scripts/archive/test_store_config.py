"""점포 설정 로더 테스트 스크립트"""
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.store_config import StoreConfigLoader
from src.utils.logger import get_logger

logger = get_logger(__name__)


def test_store_config():
    """점포 설정 로더 테스트"""
    print("=" * 60)
    print("점포 설정 로더 테스트")
    print("=" * 60)

    loader = StoreConfigLoader()

    # 1. 개별 점포 조회 테스트
    print("\n[1] 개별 점포 조회 테스트")
    for store_id in ["46513", "46704"]:
        try:
            config = loader.get_store_config(store_id)
            print(f"\n점포 ID: {config.store_id}")
            print(f"점포명: {config.store_name}")
            print(f"위치: {config.location}")
            print(f"타입: {config.store_type}")
            print(f"활성: {config.is_active}")
            print(f"사용자 ID: {config.bgf_user_id[:3]}*** (보안)")
            print(f"비밀번호: {'*' * len(config.bgf_password)} (보안)")
            print(f"설명: {config.description}")
            print(f"추가일: {config.added_date}")
        except ValueError as e:
            print(f"\n✗ 점포 {store_id} 로드 실패: {e}")

    # 2. 활성 점포 목록 조회 테스트
    print("\n[2] 활성 점포 목록 조회 테스트")
    active_stores = loader.get_active_stores()
    print(f"\n활성 점포 수: {len(active_stores)}")
    for config in active_stores:
        print(f"  - {config.store_id}: {config.store_name}")

    # 3. 모든 점포 ID 조회 테스트
    print("\n[3] 모든 점포 ID 조회 테스트")
    all_ids = loader.get_all_store_ids()
    print(f"전체 점포 ID: {all_ids}")

    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)


if __name__ == "__main__":
    test_store_config()
