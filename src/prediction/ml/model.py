"""
ML 예측 모델
- sklearn RandomForest + LightGBM 앙상블
- 카테고리 그룹별 개별 모델
- 모델 메타데이터(model_meta.json)로 버전/feature 호환성 관리
"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from src.utils.logger import get_logger

logger = get_logger(__name__)


def _json_default(obj: Any) -> Any:
    """numpy 타입을 Python 네이티브로 변환 (JSON 직렬화용)"""
    if hasattr(obj, "item"):  # numpy scalar (float32, int64 등)
        return obj.item()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# 모델 저장 기본 경로 (매장별: data/models/{store_id}/, 글로벌: data/models/)
MODEL_BASE_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"

# 카테고리 그룹 (feature_builder.py와 동기화)
CATEGORY_GROUPS = {
    "food_group": ["001", "002", "003", "004", "005", "012"],
    "alcohol_group": ["049", "050"],
    "tobacco_group": ["072", "073"],
    "perishable_group": ["013", "026", "046"],
    "general_group": [],  # 나머지
}

_GROUP_LOOKUP = {}
for _group_name, _codes in CATEGORY_GROUPS.items():
    for _code in _codes:
        _GROUP_LOOKUP[_code] = _group_name


def get_category_group(mid_cd: str) -> str:
    """중분류 코드로 카테고리 그룹 반환"""
    return _GROUP_LOOKUP.get(mid_cd, "general_group")


class MLPredictor:
    """ML 기반 수요 예측기

    카테고리 그룹별 개별 모델을 관리:
    - food_group: 001~005, 012 (유통기한 단기)
    - alcohol_group: 049, 050 (요일 패턴)
    - tobacco_group: 072, 073 (보루 패턴)
    - perishable_group: 013, 026, 046 (신선)
    - general_group: 나머지 전체
    """

    def __init__(self, model_dir: Optional[str] = None, store_id: Optional[str] = None) -> None:
        if model_dir:
            self.model_dir = Path(model_dir)
        elif store_id:
            self.model_dir = MODEL_BASE_DIR / store_id
        else:
            self.model_dir = MODEL_BASE_DIR  # 글로벌 폴백
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.store_id = store_id
        self.models: Dict[str, Any] = {}
        self._loaded = False

    def _load_from_dir(self, target_dir: Path) -> int:
        """지정 디렉토리에서 모델 로드 (feature 호환성 검증 포함)

        Args:
            target_dir: 모델 디렉토리

        Returns:
            로드된 모델 수
        """
        try:
            import joblib
        except ImportError:
            logger.debug("joblib 미설치. ML 모델 로드 불가.")
            return 0

        # 메타데이터에서 feature 호환성 확인
        meta_path = target_dir / "model_meta.json"
        current_hash = self._feature_hash()
        stale_groups: set = set()
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                for gname, ginfo in meta.get("groups", {}).items():
                    saved_hash = ginfo.get("feature_hash", "")
                    if saved_hash and saved_hash != current_hash:
                        stale_groups.add(gname)
            except Exception:
                pass

        loaded_count = 0
        for group_name in CATEGORY_GROUPS:
            if group_name in self.models:
                continue  # 이미 로드된 그룹은 건너뜀

            if group_name in stale_groups:
                logger.warning(
                    f"[{group_name}] feature 구조 변경 감지 → "
                    f"재학습 필요 (모델 스킵)"
                )
                continue

            model_path = target_dir / f"model_{group_name}.joblib"
            if model_path.exists():
                try:
                    self.models[group_name] = joblib.load(model_path)
                    loaded_count += 1
                except Exception as e:
                    logger.warning(f"모델 로드 실패 ({group_name}): {e}")
        return loaded_count

    def load_models(self) -> bool:
        """저장된 모델 로드 (매장별 우선, 글로벌 폴백)

        Returns:
            로드 성공 여부 (하나라도 로드되면 True)
        """
        if self._loaded:
            return bool(self.models)

        # 1차: 매장별 디렉토리에서 로드
        loaded_count = self._load_from_dir(self.model_dir)

        # 2차: 매장별 모델 부족 시 글로벌 폴백
        if self.store_id and self.model_dir != MODEL_BASE_DIR:
            global_loaded = self._load_from_dir(MODEL_BASE_DIR)
            if global_loaded > 0:
                if loaded_count == 0:
                    logger.info(
                        f"매장 {self.store_id} 전용 모델 없음 → "
                        f"글로벌 모델 사용 ({global_loaded}개)"
                    )
                else:
                    logger.info(
                        f"매장 {self.store_id}: 전용 {loaded_count}개 + "
                        f"글로벌 폴백 {global_loaded}개"
                    )
            loaded_count += global_loaded

        self._loaded = True

        if loaded_count > 0:
            store_label = f" (store={self.store_id})" if self.store_id else ""
            logger.info(f"ML 모델 로드 완료{store_label}: {loaded_count}개 그룹")
            return True

        logger.debug("로드된 ML 모델 없음")
        return False

    def save_model(
        self,
        group_name: str,
        model: Any,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """모델 저장 (이전 모델 백업 + 메타데이터 기록)

        Args:
            group_name: 카테고리 그룹명
            model: 학습된 모델
            metrics: 학습 지표 (mae, rmse, mape 등, 선택)

        Returns:
            저장 성공 여부
        """
        try:
            import joblib
        except ImportError:
            logger.warning("joblib 미설치. ML 모델 저장 불가.")
            return False

        try:
            model_path = self.model_dir / f"model_{group_name}.joblib"
            tmp_path = self.model_dir / f"model_{group_name}.tmp.joblib"

            # 1. 임시 파일에 새 모델 저장 (원자적 쓰기)
            joblib.dump(model, tmp_path)

            # 2. 기존 모델이 있으면 _prev로 이동 (백업)
            if model_path.exists():
                prev_path = self.model_dir / f"model_{group_name}_prev.joblib"
                try:
                    os.replace(str(model_path), str(prev_path))
                except OSError:
                    shutil.copy2(model_path, prev_path)

            # 3. 임시 파일을 정식 위치로 이동
            os.replace(str(tmp_path), str(model_path))
            self.models[group_name] = model

            # 4. 메타데이터 업데이트
            self._update_meta(group_name, metrics)

            logger.info(f"모델 저장 완료: {model_path}")
            return True
        except Exception as e:
            logger.warning(f"모델 저장 실패 ({group_name}): {e}")
            # 임시 파일 정리
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            return False

    def _get_meta_path(self) -> Path:
        """메타데이터 파일 경로"""
        return self.model_dir / "model_meta.json"

    def _load_meta(self) -> Dict[str, Any]:
        """메타데이터 로드"""
        meta_path = self._get_meta_path()
        if meta_path.exists():
            try:
                return json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _update_meta(
        self, group_name: str, metrics: Optional[Dict[str, Any]] = None
    ) -> None:
        """그룹 모델의 메타데이터 업데이트"""
        from .feature_builder import MLFeatureBuilder

        meta = self._load_meta()
        now = datetime.now().isoformat()

        meta.setdefault("groups", {})[group_name] = {
            "trained_at": now,
            "feature_count": len(MLFeatureBuilder.FEATURE_NAMES),
            "feature_hash": self._feature_hash(),
            "metrics": metrics or {},
        }
        meta["last_updated"] = now
        meta["store_id"] = self.store_id

        try:
            self._get_meta_path().write_text(
                json.dumps(meta, ensure_ascii=False, indent=2, default=_json_default),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"메타데이터 저장 실패: {e}")

    @staticmethod
    def _feature_hash() -> str:
        """현재 FEATURE_NAMES의 해시 (feature 변경 감지용)"""
        from .feature_builder import MLFeatureBuilder
        import hashlib

        names_str = ",".join(MLFeatureBuilder.FEATURE_NAMES)
        return hashlib.md5(names_str.encode()).hexdigest()[:8]

    def predict(self, features: np.ndarray, mid_cd: str) -> Optional[float]:
        """
        ML 예측 수행

        Args:
            features: feature 배열 (1D)
            mid_cd: 중분류 코드

        Returns:
            예측 판매량 또는 None (모델 없음)
        """
        if not self.models:
            if not self.load_models():
                return None

        group = get_category_group(mid_cd)
        model = self.models.get(group)

        if model is None:
            return None

        try:
            X = features.reshape(1, -1)
            pred = model.predict(X)[0]
            return max(0.0, float(pred))
        except Exception as e:
            logger.warning(f"ML 예측 실패 (mid_cd={mid_cd}): {e}")
            return None

    def is_available(self) -> bool:
        """ML 모델 사용 가능 여부"""
        if self.models:
            return True
        return self.load_models()

    def get_model_info(self) -> Dict[str, Any]:
        """모델 정보 조회 (메타데이터 포함)"""
        meta = self._load_meta()
        current_hash = self._feature_hash()

        info = {
            "available": self.is_available(),
            "model_dir": str(self.model_dir),
            "store_id": self.store_id,
            "feature_hash": current_hash,
            "groups": {},
        }

        for group_name in CATEGORY_GROUPS:
            model_path = self.model_dir / f"model_{group_name}.joblib"
            prev_path = self.model_dir / f"model_{group_name}_prev.joblib"
            group_meta = meta.get("groups", {}).get(group_name, {})

            saved_hash = group_meta.get("feature_hash", "")
            compatible = (not saved_hash) or (saved_hash == current_hash)

            info["groups"][group_name] = {
                "has_model": model_path.exists(),
                "has_prev": prev_path.exists(),
                "model_file": str(model_path),
                "trained_at": group_meta.get("trained_at"),
                "feature_compatible": compatible,
                "metrics": group_meta.get("metrics", {}),
                "modified": (
                    datetime.fromtimestamp(model_path.stat().st_mtime).isoformat()
                    if model_path.exists() else None
                ),
            }

        return info
