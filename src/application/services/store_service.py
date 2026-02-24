"""
StoreService -- 매장 관리 서비스

3개 StoreManager를 통합한 단일 매장 관리 서비스:
- 매장 CRUD (config/stores.json + DB)
- 매장 DB 자동 생성
- 매장별 설정 관리
"""

import hashlib
import json
import os
from typing import Optional, List, Dict, Any
from pathlib import Path
from datetime import datetime

from src.settings.store_context import StoreContext
from src.infrastructure.database.connection import DBRouter
from src.infrastructure.database.schema import init_store_db
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _hash_password(password: str) -> str:
    """SHA-256 + salt 해싱"""
    salt = os.urandom(16).hex()
    hashed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}${hashed}"


def _verify_password(password: str, stored: str) -> bool:
    """해싱된 비밀번호 검증. 레거시 평문도 호환."""
    if '$' not in stored:
        return password == stored
    salt, hashed = stored.split('$', 1)
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest() == hashed


class StoreService:
    """매장 관리 서비스

    Usage:
        service = StoreService()
        stores = service.get_active_stores()
        service.add_store("99999", "테스트매장", location="서울")
    """

    def get_active_stores(self) -> List[StoreContext]:
        """활성 매장 목록"""
        return StoreContext.get_all_active()

    def get_store(self, store_id: str) -> Optional[StoreContext]:
        """매장 조회"""
        try:
            return StoreContext.from_store_id(store_id)
        except ValueError:
            return None

    def get_all_store_ids(self) -> List[str]:
        """모든 활성 매장 ID 목록"""
        stores = self.get_active_stores()
        return [s.store_id for s in stores]

    def add_store(
        self,
        store_id: str,
        store_name: str,
        location: str = "",
        store_type: str = "일반점",
        bgf_user_id: str = "",
        bgf_password: str = "",
    ) -> StoreContext:
        """매장 추가 + DB 자동 생성

        1. config/stores.json에 매장 정보 추가
        2. common.db의 stores 테이블에 레코드 추가
        3. data/stores/{store_id}.db 생성 (스키마 초기화)

        Args:
            store_id: 매장 코드 (예: "46513")
            store_name: 매장명 (예: "CU 동양대점")
            location: 위치
            store_type: 매장 유형
            bgf_user_id: BGF 로그인 ID
            bgf_password: BGF 로그인 PW

        Returns:
            생성된 StoreContext

        Raises:
            ValueError: 이미 존재하는 store_id
        """
        # 중복 확인
        existing = self.get_store(store_id)
        if existing:
            raise ValueError(f"매장이 이미 존재합니다: {store_id}")

        # 1. stores.json 업데이트
        self._add_to_stores_json(
            store_id, store_name, location, store_type,
            bgf_user_id, bgf_password
        )

        # 2. common.db의 stores 테이블에 추가
        self._add_to_stores_table(
            store_id, store_name, location, store_type,
            bgf_user_id, bgf_password
        )

        # 3. 매장 DB 생성
        db_path = self.ensure_store_db(store_id)
        logger.info(f"매장 추가 완료: {store_id} ({store_name}), DB: {db_path}")

        return StoreContext.from_store_id(store_id)

    def ensure_store_db(self, store_id: str) -> Path:
        """매장 DB 존재 보장 (없으면 생성)

        Args:
            store_id: 매장 코드

        Returns:
            매장 DB 경로
        """
        db_path = DBRouter.get_store_db_path(store_id)

        if not db_path.exists():
            logger.info(f"매장 DB 생성: {store_id} -> {db_path}")
            init_store_db(store_id, db_path)

        return db_path

    def _add_to_stores_json(
        self, store_id, store_name, location, store_type,
        bgf_user_id, bgf_password
    ):
        """config/stores.json에 매장 추가"""
        from src.settings.app_config import CONFIG_DIR

        stores_json = CONFIG_DIR / "stores.json"

        if stores_json.exists():
            with open(stores_json, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {"stores": []}

        # 이미 존재하면 스킵
        for s in data.get("stores", []):
            if s.get("store_id") == store_id:
                return

        data.setdefault("stores", []).append({
            "store_id": store_id,
            "store_name": store_name,
            "location": location,
            "type": store_type,
            "is_active": True,
            "bgf_user_id": bgf_user_id,
            # bgf_password는 환경변수(BGF_PASSWORD_{store_id})로만 관리
        })

        stores_json.parent.mkdir(parents=True, exist_ok=True)
        with open(stores_json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _add_to_stores_table(
        self, store_id, store_name, location, store_type,
        bgf_user_id, bgf_password
    ):
        """common.db의 stores 테이블에 매장 추가 (비밀번호 해싱)"""
        conn = DBRouter.get_common_connection()
        try:
            now = datetime.now().isoformat()
            hashed_pw = _hash_password(bgf_password) if bgf_password else ""
            conn.execute(
                """
                INSERT OR IGNORE INTO stores
                (store_id, store_name, location, type, is_active,
                 bgf_user_id, bgf_password, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (store_id, store_name, location, store_type,
                 bgf_user_id, hashed_pw, now, now)
            )
            conn.commit()
        finally:
            conn.close()
