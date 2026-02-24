"""
Multi-Store Architecture Repositories

매장 마스터 및 매장별 평가 파라미터 관리
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)

# config/stores/ 디렉토리 경로
CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
STORES_DIR = CONFIG_DIR / "stores"


class StoreRepository(BaseRepository):
    """매장 마스터 관리 Repository"""

    def get_active_stores(self) -> List[Dict[str, Any]]:
        """활성 매장 목록 조회 (stores.json 기반)

        Returns:
            활성 매장 리스트
        """
        # stores.json에서 활성 매장 로드
        try:
            from src.config.store_config import StoreConfigLoader
            loader = StoreConfigLoader()
            configs = loader.get_active_stores()

            # StoreConfig → dict 변환
            stores = []
            for config in configs:
                stores.append({
                    'store_id': config.store_id,
                    'store_name': config.store_name,
                    'location': config.location,
                    'type': config.store_type,
                    'is_active': config.is_active,
                    'description': config.description,
                    # 주의: 인증 정보는 반환하지 않음 (보안)
                })

            logger.info(f"[StoreRepository] 활성 매장: {len(stores)}개")
            return stores

        except Exception as e:
            logger.error(f"[StoreRepository] 활성 매장 로드 실패: {e}")
            return []

    def get_store(self, store_id: str) -> Optional[Dict[str, Any]]:
        """특정 매장 정보 조회

        Args:
            store_id: 매장 코드

        Returns:
            매장 정보 dict 또는 None
        """
        conn = self._get_conn()
        try:
            cursor = conn.execute("""
                SELECT store_id, store_name, location, type,
                       is_active, bgf_user_id, bgf_password,
                       created_at, updated_at
                FROM stores
                WHERE store_id = ?
            """, (store_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def add_store(
        self,
        store_id: str,
        store_name: str,
        location: Optional[str] = None,
        store_type: Optional[str] = None,
        bgf_user_id: Optional[str] = None,
        bgf_password: Optional[str] = None
    ) -> bool:
        """신규 매장 등록

        Args:
            store_id: 매장 코드
            store_name: 매장명
            location: 지역
            store_type: 매장 유형
            bgf_user_id: BGF 로그인 ID
            bgf_password: BGF 로그인 PW

        Returns:
            성공 여부
        """
        conn = self._get_conn()
        now = self._now()

        try:
            conn.execute("""
                INSERT INTO stores (
                    store_id, store_name, location, type,
                    is_active, bgf_user_id, bgf_password,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
            """, (store_id, store_name, location, store_type,
                  bgf_user_id, bgf_password, now, now))
            conn.commit()

            logger.info(f"[StoreRepository] 매장 등록: {store_id} ({store_name})")
            return True

        except Exception as e:
            logger.error(f"[StoreRepository] 매장 등록 실패 ({store_id}): {e}")
            return False
        finally:
            conn.close()

    def update_store(self, store_id: str, **kwargs) -> bool:
        """매장 정보 수정

        Args:
            store_id: 매장 코드
            **kwargs: 수정할 필드들 (store_name, location, type, bgf_user_id, bgf_password, is_active)

        Returns:
            성공 여부
        """
        allowed_fields = ['store_name', 'location', 'type', 'bgf_user_id', 'bgf_password', 'is_active']
        update_fields = {k: v for k, v in kwargs.items() if k in allowed_fields}

        if not update_fields:
            logger.warning(f"[StoreRepository] 수정할 필드가 없습니다: {store_id}")
            return False

        conn = self._get_conn()
        now = self._now()

        try:
            # SET 절 생성
            set_clause = ", ".join([f"{field} = ?" for field in update_fields.keys()])
            set_clause += ", updated_at = ?"

            # VALUES 리스트
            values = list(update_fields.values()) + [now, store_id]

            conn.execute(f"""
                UPDATE stores
                SET {set_clause}
                WHERE store_id = ?
            """, values)
            conn.commit()

            logger.info(f"[StoreRepository] 매장 정보 수정: {store_id} ({list(update_fields.keys())})")
            return True

        except Exception as e:
            logger.error(f"[StoreRepository] 매장 정보 수정 실패 ({store_id}): {e}")
            return False
        finally:
            conn.close()

    def deactivate_store(self, store_id: str) -> bool:
        """매장 비활성화

        Args:
            store_id: 매장 코드

        Returns:
            성공 여부
        """
        return self.update_store(store_id, is_active=0)

    def activate_store(self, store_id: str) -> bool:
        """매장 활성화

        Args:
            store_id: 매장 코드

        Returns:
            성공 여부
        """
        return self.update_store(store_id, is_active=1)


class StoreEvalParamsRepository(BaseRepository):
    """매장별 평가 파라미터 관리 Repository"""

    def get_all_params(self, store_id: str) -> Dict[str, Any]:
        """매장의 전체 평가 파라미터 조회

        Args:
            store_id: 매장 코드

        Returns:
            파라미터 dict (param_name → {value, default, min, max, max_delta, description})
        """
        conn = self._get_conn()
        try:
            cursor = conn.execute("""
                SELECT param_name, param_value, default_value,
                       min_value, max_value, max_delta, description
                FROM store_eval_params
                WHERE store_id = ?
            """, (store_id,))

            params = {}
            for row in cursor.fetchall():
                params[row['param_name']] = {
                    'value': row['param_value'],
                    'default': row['default_value'],
                    'min': row['min_value'],
                    'max': row['max_value'],
                    'max_delta': row['max_delta'],
                    'description': row['description']
                }

            return params

        finally:
            conn.close()

    def get_param(self, store_id: str, param_name: str) -> Optional[float]:
        """특정 파라미터 값 조회

        Args:
            store_id: 매장 코드
            param_name: 파라미터명

        Returns:
            파라미터 값 또는 None
        """
        conn = self._get_conn()
        try:
            cursor = conn.execute("""
                SELECT param_value
                FROM store_eval_params
                WHERE store_id = ? AND param_name = ?
            """, (store_id, param_name))

            row = cursor.fetchone()
            return row['param_value'] if row else None

        finally:
            conn.close()

    def save_param(self, store_id: str, param_name: str, value: float,
                   default_value: Optional[float] = None,
                   min_value: Optional[float] = None,
                   max_value: Optional[float] = None,
                   max_delta: Optional[float] = None,
                   description: Optional[str] = None) -> bool:
        """파라미터 저장 (INSERT OR REPLACE)

        Args:
            store_id: 매장 코드
            param_name: 파라미터명
            value: 파라미터 값
            default_value: 기본값
            min_value: 최소값
            max_value: 최대값
            max_delta: 최대 변화량
            description: 설명

        Returns:
            성공 여부
        """
        conn = self._get_conn()
        now = self._now()

        try:
            conn.execute("""
                INSERT OR REPLACE INTO store_eval_params (
                    store_id, param_name, param_value,
                    default_value, min_value, max_value, max_delta,
                    description, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (store_id, param_name, value,
                  default_value, min_value, max_value, max_delta,
                  description, now))
            conn.commit()
            return True

        except Exception as e:
            logger.error(f"[StoreEvalParamsRepository] 파라미터 저장 실패 ({store_id}.{param_name}): {e}")
            return False
        finally:
            conn.close()

    def save_params_bulk(self, store_id: str, params: Dict[str, Any]) -> bool:
        """파라미터 일괄 저장

        Args:
            store_id: 매장 코드
            params: 파라미터 dict (param_name → {value, default, min, max, max_delta, description})

        Returns:
            성공 여부
        """
        conn = self._get_conn()
        now = self._now()

        try:
            for param_name, param_data in params.items():
                conn.execute("""
                    INSERT OR REPLACE INTO store_eval_params (
                        store_id, param_name, param_value,
                        default_value, min_value, max_value, max_delta,
                        description, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    store_id,
                    param_name,
                    param_data.get('value'),
                    param_data.get('default'),
                    param_data.get('min'),
                    param_data.get('max'),
                    param_data.get('max_delta'),
                    param_data.get('description'),
                    now
                ))

            conn.commit()
            logger.info(f"[StoreEvalParamsRepository] 파라미터 일괄 저장: {store_id} ({len(params)}개)")
            return True

        except Exception as e:
            logger.error(f"[StoreEvalParamsRepository] 파라미터 일괄 저장 실패 ({store_id}): {e}")
            return False
        finally:
            conn.close()

    def load_from_file(self, store_id: str) -> Dict[str, Any]:
        """매장별 파라미터 파일에서 로드

        Args:
            store_id: 매장 코드

        Returns:
            파라미터 dict 또는 빈 dict
        """
        file_path = STORES_DIR / f"{store_id}_eval_params.json"

        if not file_path.exists():
            logger.warning(f"[StoreEvalParamsRepository] 파일 없음: {file_path}")
            return {}

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                params = json.load(f)
            logger.info(f"[StoreEvalParamsRepository] 파일 로드 성공: {file_path}")
            return params

        except Exception as e:
            logger.error(f"[StoreEvalParamsRepository] 파일 로드 실패 ({file_path}): {e}")
            return {}

    def save_to_file(self, store_id: str, params: Dict[str, Any]) -> bool:
        """매장별 파라미터 파일로 저장

        Args:
            store_id: 매장 코드
            params: 파라미터 dict

        Returns:
            성공 여부
        """
        # stores 디렉토리 생성
        STORES_DIR.mkdir(parents=True, exist_ok=True)

        file_path = STORES_DIR / f"{store_id}_eval_params.json"

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(params, f, indent=2, ensure_ascii=False)

            logger.info(f"[StoreEvalParamsRepository] 파일 저장 성공: {file_path}")
            return True

        except Exception as e:
            logger.error(f"[StoreEvalParamsRepository] 파일 저장 실패 ({file_path}): {e}")
            return False

    def load_default_template(self) -> Dict[str, Any]:
        """기본 템플릿 파일 로드

        Returns:
            기본 파라미터 dict
        """
        template_path = CONFIG_DIR / "eval_params.default.json"

        if not template_path.exists():
            logger.warning(f"[StoreEvalParamsRepository] 기본 템플릿 없음: {template_path}")
            return {}

        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                params = json.load(f)
            logger.info(f"[StoreEvalParamsRepository] 기본 템플릿 로드 성공")
            return params

        except Exception as e:
            logger.error(f"[StoreEvalParamsRepository] 기본 템플릿 로드 실패: {e}")
            return {}
