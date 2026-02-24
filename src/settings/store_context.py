"""
StoreContext — 매장 식별 및 설정 값 객체

3개의 StoreManager를 하나로 통합:
- src/config/store_config.py (StoreConfigLoader)
- src/config/store_manager.py (StoreManager - CRUD)
- src/utils/store_manager.py (StoreManager - 유틸리티)

StoreContext는 frozen dataclass로 멀티스레드 안전합니다.
"""

import os
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Any

from src.utils.logger import get_logger

logger = get_logger(__name__)

# 프로젝트 경로
PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
STORES_JSON = CONFIG_DIR / "stores.json"


@dataclass(frozen=True)
class StoreContext:
    """매장 컨텍스트 (불변 값 객체)

    매장의 식별 정보와 인증 정보를 담습니다.
    frozen=True이므로 멀티스레드 환경에서 안전합니다.

    Usage:
        ctx = StoreContext.from_store_id("46513")
        conn = DBRouter.get_store_connection(ctx.store_id)
    """
    store_id: str
    store_name: str
    location: str = ""
    store_type: str = "일반점"
    is_active: bool = True
    bgf_user_id: str = ""
    bgf_password: str = ""
    description: str = ""
    added_date: str = ""

    @property
    def eval_params_path(self) -> Path:
        """매장별 eval_params.json 경로"""
        store_path = CONFIG_DIR / "stores" / f"{self.store_id}_eval_params.json"
        if store_path.exists():
            return store_path
        return CONFIG_DIR / "eval_params.json"

    @property
    def db_path(self) -> Path:
        """매장별 DB 경로"""
        return DATA_DIR / "stores" / f"{self.store_id}.db"

    def load_eval_params(self) -> Dict[str, Any]:
        """매장별 eval_params 로드"""
        path = self.eval_params_path
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"eval_params 로드 실패: {path} - {e}")
            return {}

    @classmethod
    def from_store_id(cls, store_id: str) -> "StoreContext":
        """store_id로 StoreContext 생성

        stores.json에서 메타데이터 로드 + 환경변수에서 인증 정보 로드

        Args:
            store_id: 매장 코드

        Returns:
            StoreContext 인스턴스

        Raises:
            ValueError: 매장을 찾을 수 없는 경우
        """
        store_data = _find_store_in_config(store_id)
        if store_data is None:
            raise ValueError(f"매장 {store_id}를 찾을 수 없습니다 (stores.json)")

        # 환경변수에서 인증 정보 로드 (없으면 stores.json의 값 사용)
        bgf_user_id = (
            os.getenv(f"BGF_USER_ID_{store_id}")
            or store_data.get("bgf_user_id", "")
        )
        bgf_password = (
            os.getenv(f"BGF_PASSWORD_{store_id}")
            or store_data.get("bgf_password", "")
        )

        return cls(
            store_id=store_data["store_id"],
            store_name=store_data["store_name"],
            location=store_data.get("location", ""),
            store_type=store_data.get("type", "일반점"),
            is_active=store_data.get("is_active", True),
            bgf_user_id=bgf_user_id,
            bgf_password=bgf_password,
            description=store_data.get("description", ""),
            added_date=store_data.get("added_date", ""),
        )

    @classmethod
    def default(cls) -> "StoreContext":
        """기본 매장 컨텍스트 (첫 번째 활성 매장)"""
        stores = _load_stores_json()
        active = [s for s in stores if s.get("is_active", True)]
        if not active:
            # 폴백: 하드코딩 기본값
            return cls(store_id="46513", store_name="기본매장")
        return cls.from_store_id(active[0]["store_id"])

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StoreContext":
        """딕셔너리에서 StoreContext 생성 (스케줄러 호환)"""
        return cls(
            store_id=data["store_id"],
            store_name=data.get("store_name", ""),
            location=data.get("location", ""),
            store_type=data.get("type", "일반점"),
            is_active=data.get("is_active", True),
            bgf_user_id=data.get("bgf_user_id", ""),
            bgf_password=data.get("bgf_password", ""),
            description=data.get("description", ""),
            added_date=data.get("added_date", ""),
        )

    @classmethod
    def get_all_active(cls) -> List["StoreContext"]:
        """모든 활성 매장 컨텍스트 목록"""
        stores = _load_stores_json()
        active_contexts = []
        for store_data in stores:
            if store_data.get("is_active", True):
                try:
                    ctx = cls.from_store_id(store_data["store_id"])
                    active_contexts.append(ctx)
                except ValueError as e:
                    logger.warning(f"매장 로드 실패: {e}")
        return active_contexts


# ── 내부 헬퍼 함수 ──

def _load_stores_json() -> List[Dict[str, Any]]:
    """stores.json 로드"""
    try:
        if not STORES_JSON.exists():
            logger.warning(f"stores.json 없음: {STORES_JSON}")
            return []
        with open(STORES_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get("stores", [])
    except Exception as e:
        logger.error(f"stores.json 로드 실패: {e}")
        return []


def _find_store_in_config(store_id: str) -> Optional[Dict[str, Any]]:
    """stores.json에서 매장 찾기"""
    for store in _load_stores_json():
        if store["store_id"] == store_id:
            return store
    return None
