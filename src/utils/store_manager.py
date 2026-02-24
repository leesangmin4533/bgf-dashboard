"""
점포 관리 유틸리티

멀티 점포 환경에서 점포별 설정, 정보, 파라미터를 관리합니다.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class StoreInfo:
    """점포 정보"""
    store_id: str
    store_name: str
    location: str
    store_type: str
    is_active: bool
    description: str
    added_date: str
    bgf_user_id: Optional[str] = None
    bgf_password: Optional[str] = None


class StoreManager:
    """점포 관리자

    점포 정보 조회, 설정 로드, 점포별 파라미터 관리를 담당합니다.
    """

    def __init__(self, config_dir: Optional[Path] = None):
        """초기화

        Args:
            config_dir: 설정 디렉토리 경로 (기본: project_root/config)
        """
        if config_dir is None:
            config_dir = Path(__file__).parent.parent.parent / "config"

        self.config_dir = Path(config_dir)
        self.stores_config_path = self.config_dir / "stores.json"
        self.stores_dir = self.config_dir / "stores"

        # 점포 정보 캐시
        self._stores_cache: Optional[Dict[str, StoreInfo]] = None

    def get_all_stores(self, active_only: bool = True) -> List[StoreInfo]:
        """모든 점포 정보 조회

        Args:
            active_only: True면 활성 점포만 반환

        Returns:
            점포 정보 리스트
        """
        stores = self._load_stores_config()

        if active_only:
            stores = [s for s in stores if s.is_active]

        return stores

    def get_store_info(self, store_id: str) -> Optional[StoreInfo]:
        """특정 점포 정보 조회

        Args:
            store_id: 점포 ID

        Returns:
            점포 정보 (없으면 None)
        """
        if self._stores_cache is None:
            self._stores_cache = {
                store.store_id: store
                for store in self._load_stores_config()
            }

        return self._stores_cache.get(store_id)

    def get_store_eval_params_path(self, store_id: str) -> Path:
        """점포별 eval_params.json 경로 반환

        Args:
            store_id: 점포 ID

        Returns:
            설정 파일 경로
        """
        store_config_path = self.stores_dir / f"{store_id}_eval_params.json"

        # 점포별 설정이 없으면 기본 설정 경로 반환
        if not store_config_path.exists():
            logger.warning(
                f"점포별 설정 파일 없음: {store_config_path}, "
                f"기본 설정 사용"
            )
            return self.config_dir / "eval_params.json"

        return store_config_path

    def load_store_eval_params(self, store_id: str) -> Dict[str, Any]:
        """점포별 eval_params 로드

        Args:
            store_id: 점포 ID

        Returns:
            eval_params 딕셔너리
        """
        config_path = self.get_store_eval_params_path(store_id)

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                params = json.load(f)

            logger.info(f"점포 설정 로드: {store_id} from {config_path}")
            return params

        except Exception as e:
            logger.error(f"설정 파일 로드 실패: {config_path} - {e}")
            return {}

    def get_active_store_ids(self) -> List[str]:
        """활성 점포 ID 목록 반환

        Returns:
            점포 ID 리스트 (예: ['46513', '46704'])
        """
        stores = self.get_all_stores(active_only=True)
        return [store.store_id for store in stores]

    def is_valid_store(self, store_id: str) -> bool:
        """유효한 점포 ID인지 확인

        Args:
            store_id: 점포 ID

        Returns:
            유효하면 True
        """
        store = self.get_store_info(store_id)
        return store is not None and store.is_active

    def get_default_store_id(self) -> str:
        """기본 점포 ID 반환 (첫 번째 활성 점포)

        Returns:
            점포 ID
        """
        stores = self.get_all_stores(active_only=True)

        if not stores:
            raise ValueError("활성 점포가 없습니다")

        return stores[0].store_id

    def _load_stores_config(self) -> List[StoreInfo]:
        """stores.json 파일 로드

        Returns:
            점포 정보 리스트
        """
        try:
            with open(self.stores_config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)

            stores = []
            for store_data in config.get('stores', []):
                store = StoreInfo(
                    store_id=store_data['store_id'],
                    store_name=store_data['store_name'],
                    location=store_data['location'],
                    store_type=store_data['type'],
                    is_active=store_data['is_active'],
                    description=store_data['description'],
                    added_date=store_data['added_date'],
                    bgf_user_id=store_data.get('bgf_user_id'),
                    bgf_password=store_data.get('bgf_password')
                )
                stores.append(store)

            return stores

        except Exception as e:
            logger.error(f"점포 설정 파일 로드 실패: {e}")
            return []

    def print_stores_summary(self) -> None:
        """점포 현황 출력 (디버깅용)"""
        stores = self.get_all_stores(active_only=False)

        print("=" * 80)
        print("점포 현황")
        print("=" * 80)
        print(f"전체 점포: {len(stores)}개")
        print(f"활성 점포: {len([s for s in stores if s.is_active])}개")
        print()

        for store in stores:
            status = "활성" if store.is_active else "비활성"
            print(f"[{store.store_id}] {store.store_name}")
            print(f"  위치: {store.location}")
            print(f"  타입: {store.store_type}")
            print(f"  상태: {status}")
            print(f"  설명: {store.description}")

            # 설정 파일 존재 여부
            config_path = self.get_store_eval_params_path(store.store_id)
            config_exists = config_path.exists()
            print(f"  설정: {'존재' if config_exists else '없음'} ({config_path.name})")
            print()

        print("=" * 80)


# 전역 인스턴스 (싱글톤 패턴)
_store_manager: Optional[StoreManager] = None


def get_store_manager() -> StoreManager:
    """StoreManager 싱글톤 인스턴스 반환

    Returns:
        StoreManager 인스턴스
    """
    global _store_manager

    if _store_manager is None:
        _store_manager = StoreManager()

    return _store_manager


# 편의 함수들
def get_active_stores() -> List[StoreInfo]:
    """활성 점포 목록 반환"""
    return get_store_manager().get_all_stores(active_only=True)


def get_store_info(store_id: str) -> Optional[StoreInfo]:
    """점포 정보 조회"""
    return get_store_manager().get_store_info(store_id)


def is_valid_store(store_id: str) -> bool:
    """유효한 점포인지 확인"""
    return get_store_manager().is_valid_store(store_id)


def get_store_config_path(store_id: str) -> Path:
    """점포별 설정 파일 경로"""
    return get_store_manager().get_store_eval_params_path(store_id)


if __name__ == "__main__":
    # 테스트
    manager = StoreManager()
    manager.print_stores_summary()
