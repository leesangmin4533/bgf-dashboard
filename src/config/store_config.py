"""점포별 설정 통합 로더

환경변수에서 인증 정보를 로드하고 stores.json의 메타데이터와 결합
"""
import os
import json
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class StoreConfig:
    """점포 설정"""
    store_id: str
    store_name: str
    location: str
    store_type: str
    is_active: bool
    bgf_user_id: str      # 환경변수에서 로드
    bgf_password: str     # 환경변수에서 로드
    description: str
    added_date: str


class StoreConfigLoader:
    """점포 설정 로더 (환경변수 + stores.json 통합)"""

    def __init__(self, stores_json_path: Optional[Path] = None):
        """
        Args:
            stores_json_path: stores.json 파일 경로 (기본값: config/stores.json)
        """
        if stores_json_path is None:
            stores_json_path = Path(__file__).parent.parent.parent / "config" / "stores.json"
        self.stores_json_path = stores_json_path

    def get_store_config(self, store_id: str) -> StoreConfig:
        """점포 설정 조회

        stores.json에서 메타데이터 로드 + 환경변수에서 인증 정보 로드

        Args:
            store_id: 점포 코드 (예: "46513")

        Returns:
            StoreConfig 객체

        Raises:
            ValueError: 점포를 찾을 수 없거나 환경변수가 설정되지 않은 경우
        """
        # stores.json 로드
        with open(self.stores_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 점포 찾기
        store_data = None
        for store in data.get('stores', []):
            if store['store_id'] == store_id:
                store_data = store
                break

        if not store_data:
            raise ValueError(f"점포 {store_id}를 찾을 수 없습니다")

        # 환경변수에서 인증 정보 로드
        env_user_key = f"BGF_USER_ID_{store_id}"
        env_pass_key = f"BGF_PASSWORD_{store_id}"

        bgf_user_id = os.getenv(env_user_key)
        bgf_password = os.getenv(env_pass_key)

        if not bgf_user_id or not bgf_password:
            raise ValueError(
                f"환경변수 {env_user_key}, {env_pass_key}가 설정되지 않았습니다.\n"
                f".env 파일에 다음을 추가하세요:\n"
                f"{env_user_key}=your_user_id\n"
                f"{env_pass_key}=your_password"
            )

        return StoreConfig(
            store_id=store_data['store_id'],
            store_name=store_data['store_name'],
            location=store_data['location'],
            store_type=store_data['type'],
            is_active=store_data['is_active'],
            bgf_user_id=bgf_user_id,
            bgf_password=bgf_password,
            description=store_data.get('description', ''),
            added_date=store_data.get('added_date', ''),
        )

    def get_active_stores(self) -> List[StoreConfig]:
        """활성 점포 목록 조회

        Returns:
            활성 점포 목록 (is_active=true이고 환경변수가 설정된 점포만)
        """
        with open(self.stores_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        active_stores = []
        for store in data.get('stores', []):
            if store.get('is_active', False):
                try:
                    config = self.get_store_config(store['store_id'])
                    active_stores.append(config)
                except ValueError as e:
                    logger.warning(f"점포 {store['store_id']} 로드 실패: {e}")

        return active_stores

    def get_all_store_ids(self) -> List[str]:
        """모든 점포 ID 조회

        Returns:
            점포 ID 리스트
        """
        with open(self.stores_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        return [store['store_id'] for store in data.get('stores', [])]
