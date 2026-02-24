"""
점포 관리 모듈

중앙 집중식 점포 정보 관리:
- config/stores.json 파일 기반
- DB 자동 동기화
- 점포 추가/수정/삭제
- 활성 점포 조회
"""

import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

from src.utils.logger import get_logger

logger = get_logger(__name__)

# 설정 파일 경로
PROJECT_ROOT = Path(__file__).parent.parent.parent
STORES_CONFIG_FILE = PROJECT_ROOT / "config" / "stores.json"


class StoreManager:
    """점포 관리 클래스"""

    def __init__(self, config_path: Path = None):
        """초기화

        Args:
            config_path: 점포 설정 파일 경로 (기본: config/stores.json)
        """
        self.config_path = config_path or STORES_CONFIG_FILE
        self._stores_cache = None
        self._load_config()

    def _load_config(self) -> None:
        """설정 파일 로드"""
        try:
            if not self.config_path.exists():
                logger.warning(f"점포 설정 파일이 없습니다: {self.config_path}")
                self._stores_cache = {"stores": [], "_metadata": {}}
                return

            with open(self.config_path, 'r', encoding='utf-8') as f:
                self._stores_cache = json.load(f)

            logger.info(f"점포 설정 로드 완료: {len(self._stores_cache.get('stores', []))}개")

        except Exception as e:
            logger.error(f"점포 설정 로드 실패: {e}")
            self._stores_cache = {"stores": [], "_metadata": {}}

    def _save_config(self) -> bool:
        """설정 파일 저장

        Returns:
            저장 성공 여부
        """
        try:
            # 메타데이터 업데이트
            self._stores_cache["_metadata"] = {
                "version": "1.0",
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total_stores": len(self._stores_cache["stores"]),
                "active_stores": sum(1 for s in self._stores_cache["stores"] if s.get("is_active", True))
            }

            # 파일 저장
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self._stores_cache, f, indent=2, ensure_ascii=False)

            logger.info(f"점포 설정 저장 완료: {self.config_path}")
            return True

        except Exception as e:
            logger.error(f"점포 설정 저장 실패: {e}")
            return False

    def get_all_stores(self) -> List[Dict[str, Any]]:
        """전체 점포 목록 조회

        Returns:
            점포 목록
        """
        return self._stores_cache.get("stores", [])

    def get_active_stores(self) -> List[Dict[str, Any]]:
        """활성 점포 목록 조회

        Returns:
            활성 점포 목록
        """
        return [
            store for store in self._stores_cache.get("stores", [])
            if store.get("is_active", True)
        ]

    def get_store(self, store_id: str) -> Optional[Dict[str, Any]]:
        """특정 점포 조회

        Args:
            store_id: 점포 코드

        Returns:
            점포 정보 (없으면 None)
        """
        for store in self._stores_cache.get("stores", []):
            if store["store_id"] == store_id:
                return store
        return None

    def add_store(
        self,
        store_id: str,
        store_name: str,
        location: str = "",
        store_type: str = "일반점",
        bgf_user_id: str = None,
        bgf_password: str = None,
        description: str = "",
        is_active: bool = True
    ) -> bool:
        """점포 추가

        Args:
            store_id: 점포 코드
            store_name: 점포명
            location: 위치
            store_type: 점포 유형
            bgf_user_id: BGF 로그인 ID
            bgf_password: BGF 로그인 패스워드
            description: 설명
            is_active: 활성 여부

        Returns:
            추가 성공 여부
        """
        # 중복 확인
        if self.get_store(store_id):
            logger.warning(f"점포 {store_id}가 이미 존재합니다.")
            return False

        # 점포 정보 생성
        store_info = {
            "store_id": store_id,
            "store_name": store_name,
            "location": location,
            "type": store_type,
            "is_active": is_active,
            "bgf_user_id": bgf_user_id,
            "bgf_password": bgf_password,
            "description": description,
            "added_date": datetime.now().strftime("%Y-%m-%d")
        }

        # 추가
        self._stores_cache["stores"].append(store_info)

        # 저장
        if self._save_config():
            logger.info(f"점포 추가 완료: {store_id} ({store_name})")
            return True
        else:
            # 실패 시 롤백
            self._stores_cache["stores"].pop()
            return False

    def update_store(
        self,
        store_id: str,
        **kwargs
    ) -> bool:
        """점포 정보 수정

        Args:
            store_id: 점포 코드
            **kwargs: 수정할 필드

        Returns:
            수정 성공 여부
        """
        store = self.get_store(store_id)
        if not store:
            logger.warning(f"점포 {store_id}를 찾을 수 없습니다.")
            return False

        # 수정
        for key, value in kwargs.items():
            if key in store:
                store[key] = value

        # 저장
        if self._save_config():
            logger.info(f"점포 수정 완료: {store_id}")
            return True
        else:
            # 실패 시 재로드
            self._load_config()
            return False

    def deactivate_store(self, store_id: str) -> bool:
        """점포 비활성화

        Args:
            store_id: 점포 코드

        Returns:
            비활성화 성공 여부
        """
        return self.update_store(store_id, is_active=False)

    def activate_store(self, store_id: str) -> bool:
        """점포 활성화

        Args:
            store_id: 점포 코드

        Returns:
            활성화 성공 여부
        """
        return self.update_store(store_id, is_active=True)

    def delete_store(self, store_id: str) -> bool:
        """점포 삭제 (실제 삭제, 권장하지 않음)

        Args:
            store_id: 점포 코드

        Returns:
            삭제 성공 여부
        """
        stores = self._stores_cache.get("stores", [])
        original_count = len(stores)

        # 삭제
        self._stores_cache["stores"] = [
            s for s in stores if s["store_id"] != store_id
        ]

        if len(self._stores_cache["stores"]) < original_count:
            # 저장
            if self._save_config():
                logger.info(f"점포 삭제 완료: {store_id}")
                return True
            else:
                # 실패 시 롤백
                self._stores_cache["stores"] = stores
                return False
        else:
            logger.warning(f"점포 {store_id}를 찾을 수 없습니다.")
            return False

    def sync_to_db(self) -> Dict[str, int]:
        """설정 파일의 점포 정보를 DB에 동기화

        Returns:
            동기화 결과 (added, updated, skipped)
        """
        from src.infrastructure.database.schema import init_db
        from src.db.repository_multi_store import StoreRepository, StoreEvalParamsRepository

        # DB 초기화
        init_db()

        store_repo = StoreRepository()
        params_repo = StoreEvalParamsRepository()

        result = {
            "added": 0,
            "updated": 0,
            "skipped": 0
        }

        for store in self.get_all_stores():
            store_id = store["store_id"]

            # DB에 존재 확인
            existing = store_repo.get_store(store_id)

            if existing:
                # 업데이트 (BGF 로그인 정보만)
                if store.get("bgf_user_id") or store.get("bgf_password"):
                    store_repo.update_store_credentials(
                        store_id,
                        bgf_user_id=store.get("bgf_user_id"),
                        bgf_password=store.get("bgf_password")
                    )
                    result["updated"] += 1
                else:
                    result["skipped"] += 1
            else:
                # 신규 추가
                success = store_repo.add_store(
                    store_id=store_id,
                    store_name=store["store_name"],
                    location=store.get("location", ""),
                    store_type=store.get("type", "일반점"),
                    bgf_user_id=store.get("bgf_user_id"),
                    bgf_password=store.get("bgf_password")
                )

                if success:
                    # 파라미터 생성
                    default_params = params_repo.load_default_template()
                    if default_params:
                        params_repo.save_params_bulk(store_id, default_params)
                        params_repo.save_to_file(store_id, default_params)

                    result["added"] += 1

            # 활성 상태 동기화
            if not store.get("is_active", True):
                store_repo.deactivate_store(store_id)

        logger.info(f"DB 동기화 완료: added={result['added']}, updated={result['updated']}, skipped={result['skipped']}")
        return result

    def sync_from_db(self) -> int:
        """DB의 점포 정보를 설정 파일로 동기화

        Returns:
            동기화된 점포 수
        """
        from src.infrastructure.database.schema import init_db
        from src.db.repository_multi_store import StoreRepository

        # DB 초기화
        init_db()

        store_repo = StoreRepository()
        db_stores = store_repo.get_active_stores()

        synced = 0
        for db_store in db_stores:
            store_id = db_store["store_id"]

            # 설정 파일에 없으면 추가
            if not self.get_store(store_id):
                self.add_store(
                    store_id=store_id,
                    store_name=db_store["store_name"],
                    location=db_store.get("location", ""),
                    store_type=db_store.get("type", "일반점"),
                    bgf_user_id=db_store.get("bgf_user_id"),
                    bgf_password=db_store.get("bgf_password"),
                    description="DB에서 동기화"
                )
                synced += 1

        logger.info(f"설정 파일 동기화 완료: {synced}개 추가")
        return synced

    def print_summary(self) -> None:
        """점포 목록 요약 출력 (logger.info로 통합 로깅)"""
        all_stores = self.get_all_stores()
        active_stores = self.get_active_stores()

        lines = []
        lines.append("=" * 60)
        lines.append("점포 관리 요약")
        lines.append("=" * 60)
        lines.append(f"총 점포: {len(all_stores)}개")
        lines.append(f"활성 점포: {len(active_stores)}개")
        lines.append(f"비활성 점포: {len(all_stores) - len(active_stores)}개")
        lines.append("")
        lines.append("활성 점포 목록:")
        lines.append("-" * 60)
        for store in active_stores:
            bgf_status = "설정됨" if store.get("bgf_user_id") else "미설정"
            lines.append(f"  {store['store_id']}: {store['store_name']}")
            lines.append(f"    위치: {store.get('location', '미정')}")
            lines.append(f"    BGF 로그인: {bgf_status}")
            lines.append("")
        lines.append("=" * 60)
        logger.info("\n".join(lines))


# 전역 인스턴스
_store_manager = None


def get_store_manager() -> StoreManager:
    """StoreManager 싱글톤 인스턴스 반환

    Returns:
        StoreManager 인스턴스
    """
    global _store_manager
    if _store_manager is None:
        _store_manager = StoreManager()
    return _store_manager


# 편의 함수
def get_active_store_ids() -> List[str]:
    """활성 점포 ID 목록 반환

    Returns:
        점포 ID 리스트
    """
    manager = get_store_manager()
    return [store["store_id"] for store in manager.get_active_stores()]


def get_store_info(store_id: str) -> Optional[Dict[str, Any]]:
    """점포 정보 조회

    Args:
        store_id: 점포 코드

    Returns:
        점포 정보
    """
    manager = get_store_manager()
    return manager.get_store(store_id)


if __name__ == "__main__":
    # 테스트
    manager = StoreManager()
    manager.print_summary()

    # DB 동기화
    print("\n[DB 동기화]")
    result = manager.sync_to_db()
    print(f"Added: {result['added']}, Updated: {result['updated']}, Skipped: {result['skipped']}")
