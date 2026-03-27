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
import os
import requests

from src.utils.logger import get_logger

logger = get_logger(__name__)


def _hash_password(password: str) -> str:
    """SHA-256 + salt 해싱"""
    salt = os.urandom(16).hex()
    hashed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}${hashed}"


def get_coordinates(address: str):
    """카카오 로컬 API로 주소 → (lat, lng) 변환.

    실패 시 (None, None) 반환 — 가입 차단 금지.
    """
    try:
        api_key = os.environ.get("KAKAO_REST_API_KEY", "")
        if not api_key:
            logger.warning("KAKAO_REST_API_KEY 미설정, 좌표 변환 건너뜀")
            return None, None

        url = "https://dapi.kakao.com/v2/local/search/address.json"
        headers = {"Authorization": f"KakaoAK {api_key}"}
        params = {"query": address}
        res = requests.get(url, headers=headers, params=params, timeout=3)
        data = res.json()
        docs = data.get("documents", [])
        if docs:
            lat = float(docs[0]["y"])
            lng = float(docs[0]["x"])
            logger.info("주소 좌표 변환 성공: %s → (%s, %s)", address, lat, lng)
            return lat, lng
        logger.warning("주소 좌표 변환 결과 없음: %s", address)
    except Exception as e:
        logger.warning("주소 좌표 변환 실패: %s — %s", address, e)
    return None, None


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
        lat: float = None,
        lng: float = None,
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
            bgf_user_id, bgf_password, lat=lat, lng=lng
        )

        # 3. 매장 DB 생성
        db_path = self.ensure_store_db(store_id)

        # 4. eval_params 기본값 초기화 (실패해도 add_store 자체는 성공)
        try:
            self._init_eval_params(store_id)
        except Exception as e:
            logger.warning("eval_params 초기화 실패 (폴백 사용): %s — %s", store_id, e)

        logger.info(
            "매장 추가 완료: %s (%s) — stores.json ✓, common.db ✓, %s ✓, eval_params ✓",
            store_id, store_name, db_path,
        )

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

        entry = {
            "store_id": store_id,
            "store_name": store_name,
            "location": location,
            "type": store_type,
            "is_active": True,
            "bgf_user_id": bgf_user_id,
            "added_date": datetime.now().strftime("%Y-%m-%d"),
        }
        # bgf_password가 있으면 stores.json에도 저장 (SalesAnalyzer 폴백용)
        if bgf_password:
            entry["bgf_password"] = bgf_password

        data.setdefault("stores", []).append(entry)

        stores_json.parent.mkdir(parents=True, exist_ok=True)
        with open(stores_json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # 13개 eval_params 기본값 (DB의 default_value 기준)
    _DEFAULT_EVAL_PARAMS = [
        # (param_name, default_value, min_value, max_value, max_delta, description)
        ("daily_avg_days",            14.0, 7.0,  30.0, 7.0,  "일평균 판매량 계산 기간 (일)"),
        ("weight_daily_avg",          0.4,  0.2,  0.55, 0.05, "인기도 가중치: 일평균판매량"),
        ("weight_sell_day_ratio",     0.35, 0.15, 0.55, 0.05, "인기도 가중치: 판매일 비율"),
        ("weight_trend",              0.25, 0.1,  0.45, 0.05, "인기도 가중치: 트렌드"),
        ("popularity_high_percentile", 70.0, 50.0, 90.0, 5.0, "고인기 상한 (상위 N%)"),
        ("popularity_low_percentile",  35.0, 15.0, 50.0, 5.0, "저인기 하한 (하위 N%)"),
        ("exposure_urgent",           1.0,  0.3,  2.0,  0.3,  "긴급 진열 임계값 (일)"),
        ("exposure_normal",           2.0,  1.0,  3.5,  0.3,  "일반 진열 임계값 (일)"),
        ("exposure_sufficient",       3.0,  2.5,  5.0,  0.5,  "충분 진열 임계값 (일)"),
        ("stockout_freq_threshold",   0.15, 0.05, 0.25, 0.03, "품절빈도 임계값 (30일 중 비율)"),
        ("target_accuracy",           0.6,  0.4,  0.85, 0.05, "예측 목표 정확률"),
        ("calibration_decay",         0.7,  0.3,  1.0,  0.1,  "보정 감쇠율 (1.0=즉시 반영)"),
        ("calibration_reversion_rate", 0.1, 0.0,  0.3,  0.05, "기본값 복귀 속도 (0=복귀 안 함)"),
    ]

    def _init_eval_params(self, store_id: str) -> None:
        """store_eval_params에 기본값 13개 행 INSERT OR IGNORE

        이미 존재하는 행은 스킵합니다.
        eval_calibrator가 매일 실행되면서 매장별 최적값으로 자동 보정됩니다.

        Args:
            store_id: 매장 코드
        """
        conn = DBRouter.get_common_connection()
        try:
            now = datetime.now().isoformat()
            inserted = 0
            for (name, default, min_v, max_v, max_d, desc) in self._DEFAULT_EVAL_PARAMS:
                result = conn.execute(
                    """
                    INSERT OR IGNORE INTO store_eval_params
                    (store_id, param_name, param_value, default_value,
                     min_value, max_value, max_delta, description, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (store_id, name, default, default,
                     min_v, max_v, max_d, desc, now),
                )
                inserted += result.rowcount
            conn.commit()
            logger.info(
                "eval_params 초기화: store=%s, %d/%d 행 INSERT",
                store_id, inserted, len(self._DEFAULT_EVAL_PARAMS),
            )
        finally:
            conn.close()

    def _add_to_stores_table(
        self, store_id, store_name, location, store_type,
        bgf_user_id, bgf_password, lat=None, lng=None
    ):
        """common.db의 stores 테이블에 매장 추가 (비밀번호 해싱)"""
        conn = DBRouter.get_common_connection()
        try:
            now = datetime.now().isoformat()
            hashed_pw = _hash_password(bgf_password) if bgf_password else ""
            conn.execute(
                """
                INSERT OR IGNORE INTO stores
                (store_id, store_name, location, lat, lng, type, is_active,
                 bgf_user_id, bgf_password, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (store_id, store_name, location, lat, lng, store_type,
                 bgf_user_id, hashed_pw, now, now)
            )
            conn.commit()
        finally:
            conn.close()
