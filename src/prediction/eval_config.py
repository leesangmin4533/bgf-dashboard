"""
사전 발주 평가 설정 (EvalConfig)

모든 조정 가능한 파라미터를 중앙 관리한다.
- 기본값 / 현재값 / 허용 범위 정의
- JSON 파일로 저장/로드 (자동 보정 결과 반영)
- 1회 보정 폭 제한으로 극단적 변경 방지
"""

import json
import shutil
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.logger import get_logger
from src.settings.constants import DEFAULT_STORE_ID

logger = get_logger(__name__)

# 설정 파일 경로
CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
EVAL_CONFIG_PATH = CONFIG_DIR / "eval_params.json"


@dataclass
class ParamSpec:
    """파라미터 정의 (현재값, 기본값, 허용 범위, 1회 최대 변경폭)"""
    value: float
    default: float
    min_val: float
    max_val: float
    max_delta: float  # 1회 보정 시 최대 변경 폭
    description: str = ""
    locked: bool = False  # True면 Bayesian 최적화에서 제외

    def clamp(self, new_value: float) -> float:
        """허용 범위 내로 제한

        Args:
            new_value: 제한할 값

        Returns:
            min_val ~ max_val 범위로 클램핑된 값
        """
        return max(self.min_val, min(self.max_val, new_value))

    def apply_delta(self, delta: float) -> float:
        """변경폭 제한 적용 후 새 값 반환

        Args:
            delta: 변경할 값 (양수/음수)

        Returns:
            변경폭 제한 및 범위 클램핑이 적용된 최종 값
        """
        clamped_delta = max(-self.max_delta, min(self.max_delta, delta))
        new_value = self.value + clamped_delta
        return self.clamp(new_value)


@dataclass
class EvalConfig:
    """사전 발주 평가 전체 설정"""

    # --- 일평균 판매량 ---
    daily_avg_days: ParamSpec = field(default_factory=lambda: ParamSpec(
        value=14.0, default=14.0, min_val=7.0, max_val=30.0, max_delta=7.0,
        description="일평균 판매량 계산 기간 (일)"
    ))

    # --- 인기도 가중치 (합계 = 1.0, 수요 지표만) ---
    weight_daily_avg: ParamSpec = field(default_factory=lambda: ParamSpec(
        value=0.40, default=0.40, min_val=0.20, max_val=0.55, max_delta=0.05,
        description="인기도 가중치: 가용일평균 판매량"
    ))
    weight_sell_day_ratio: ParamSpec = field(default_factory=lambda: ParamSpec(
        value=0.35, default=0.35, min_val=0.15, max_val=0.55, max_delta=0.05,
        description="인기도 가중치: 판매일 비율"
    ))
    weight_trend: ParamSpec = field(default_factory=lambda: ParamSpec(
        value=0.25, default=0.25, min_val=0.10, max_val=0.45, max_delta=0.05,
        description="인기도 가중치: 트렌드 (7일/30일)"
    ))

    # --- 인기도 등급 임계값 (백분위 기반) ---
    popularity_high_percentile: ParamSpec = field(default_factory=lambda: ParamSpec(
        value=70.0, default=70.0, min_val=50.0, max_val=90.0, max_delta=5.0,
        description="고인기 백분위 기준 (상위 N%)"
    ))
    popularity_low_percentile: ParamSpec = field(default_factory=lambda: ParamSpec(
        value=35.0, default=35.0, min_val=15.0, max_val=50.0, max_delta=5.0,
        description="저인기 백분위 기준 (하위 N%)"
    ))

    # --- 노출시간 임계값 (일) ---
    exposure_urgent: ParamSpec = field(default_factory=lambda: ParamSpec(
        value=1.0, default=1.0, min_val=0.3, max_val=2.0, max_delta=0.3,
        description="긴급 발주 노출 임계값 (일)"
    ))
    exposure_normal: ParamSpec = field(default_factory=lambda: ParamSpec(
        value=2.0, default=2.0, min_val=1.0, max_val=3.5, max_delta=0.3,
        description="일반 발주 노출 임계값 (일)"
    ))
    exposure_sufficient: ParamSpec = field(default_factory=lambda: ParamSpec(
        value=3.0, default=3.0, min_val=2.5, max_val=5.0, max_delta=0.5,
        description="재고 충분 노출 임계값 (일)"
    ))

    # --- 품절 빈도 임계값 ---
    stockout_freq_threshold: ParamSpec = field(default_factory=lambda: ParamSpec(
        value=0.15, default=0.15, min_val=0.05, max_val=0.25, max_delta=0.03,
        description="품절 빈도 업그레이드 임계값 (30일 중 비율)"
    ))

    # --- 보정 목표 적중률 ---
    target_accuracy: ParamSpec = field(default_factory=lambda: ParamSpec(
        value=0.60, default=0.60, min_val=0.40, max_val=0.85, max_delta=0.05,
        description="보정 기준 목표 적중률"
    ))

    # --- 보정 루프 안전장치 (Mean Reversion) ---
    calibration_decay: ParamSpec = field(default_factory=lambda: ParamSpec(
        value=0.7, default=0.7, min_val=0.3, max_val=1.0, max_delta=0.1,
        description="보정 강도 감쇄율 (1.0=감쇄 없음)"
    ))
    calibration_reversion_rate: ParamSpec = field(default_factory=lambda: ParamSpec(
        value=0.1, default=0.1, min_val=0.0, max_val=0.3, max_delta=0.05,
        description="기본값 복원 속도 (0=복원 없음)"
    ))

    def get_popularity_weights(self) -> Dict[str, float]:
        """인기도 가중치 dict 반환 (합계 1.0 정규화, 수요 지표만)

        Returns:
            {"daily_avg": float, "sell_day_ratio": float, "trend": float}
        """
        raw = {
            "daily_avg": self.weight_daily_avg.value,
            "sell_day_ratio": self.weight_sell_day_ratio.value,
            "trend": self.weight_trend.value,
        }
        total = sum(raw.values())
        if total <= 0:
            return {"daily_avg": 0.40, "sell_day_ratio": 0.35, "trend": 0.25}
        return {k: v / total for k, v in raw.items()}

    def to_dict(self) -> Dict[str, Dict[str, object]]:
        """직렬화용 dict 반환

        Returns:
            {파라미터명: {"value", "default", "min", "max", "max_delta", "description"}} 딕셔너리
        """
        result = {}
        for name in self._param_names():
            spec: ParamSpec = getattr(self, name)
            result[name] = {
                "value": spec.value,
                "default": spec.default,
                "min": spec.min_val,
                "max": spec.max_val,
                "max_delta": spec.max_delta,
                "description": spec.description,
                "locked": spec.locked,
            }
        return result

    def _param_names(self) -> List[str]:
        """모든 ParamSpec 필드 이름 (dataclass fields에서 자동 추출)"""
        return [f.name for f in fields(self) if isinstance(getattr(self, f.name), ParamSpec)]

    def save(self, store_id: str = DEFAULT_STORE_ID, path: Optional[Path] = None, save_to_db: bool = True) -> str:
        """
        현재 설정을 JSON 파일 및 DB에 저장

        Args:
            store_id: 매장 코드
            path: 파일 경로 (지정 시 store_id 무시하고 해당 파일만 저장)
            save_to_db: DB에도 저장할지 여부 (기본값: True)

        Returns:
            저장된 파일 경로
        """
        # 1. path가 명시적으로 지정된 경우 (레거시 호환성)
        if path is not None:
            filepath = path
            filepath.parent.mkdir(parents=True, exist_ok=True)
            # 변경 전 백업 자동 생성
            if filepath.exists():
                backup = filepath.with_suffix(".json.bak")
                try:
                    shutil.copy2(filepath, backup)
                except Exception as e:
                    logger.warning(f"설정 백업 실패: {e}")
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info(f"평가 설정 저장: {filepath}")
            return str(filepath)

        # 2. 매장별 파일로 저장
        store_dir = CONFIG_DIR / "stores"
        store_dir.mkdir(parents=True, exist_ok=True)
        filepath = store_dir / f"{store_id}_eval_params.json"

        # 백업
        if filepath.exists():
            backup = filepath.with_suffix(".json.bak")
            try:
                shutil.copy2(filepath, backup)
            except Exception as e:
                logger.warning(f"[{store_id}] 설정 백업 실패: {e}")

        # 파일 저장
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"[{store_id}] 평가 설정 저장: {filepath}")

        # 3. DB에도 저장
        if save_to_db:
            try:
                from src.db.repository_multi_store import StoreEvalParamsRepository
                repo = StoreEvalParamsRepository()
                repo.save_params_bulk(store_id, self.to_dict())
                logger.info(f"[{store_id}] 평가 설정 DB 저장 완료")
            except Exception as e:
                logger.warning(f"[{store_id}] 평가 설정 DB 저장 실패: {e}")

        return str(filepath)

    @classmethod
    def load(cls, store_id: str = DEFAULT_STORE_ID, path: Optional[Path] = None) -> "EvalConfig":
        """
        매장별 평가 설정 로드 (3-tier: DB → 파일 → 기본값)

        Args:
            store_id: 매장 코드
            path: 파일 경로 (지정 시 store_id 무시하고 해당 파일만 로드)

        Returns:
            EvalConfig 인스턴스
        """
        config = cls()

        # 1. path가 명시적으로 지정된 경우 (레거시 호환성)
        if path is not None:
            return cls._load_from_path(config, path)

        # 2. DB에서 로드 시도
        db_params = cls._load_from_db(store_id)
        if db_params:
            cls._apply_params(config, db_params)
            logger.info(f"[{store_id}] 평가 설정 로드: DB")
            return config

        # 3. 매장별 파일에서 로드 시도
        store_file = CONFIG_DIR / "stores" / f"{store_id}_eval_params.json"
        if store_file.exists():
            try:
                with open(store_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                cls._apply_params(config, data)
                logger.info(f"[{store_id}] 평가 설정 로드: {store_file}")
                return config
            except Exception as e:
                logger.warning(f"[{store_id}] 매장 파일 로드 실패: {e}")

        # 4. 기본값 템플릿에서 로드 시도
        default_file = CONFIG_DIR / "eval_params.default.json"
        if default_file.exists():
            try:
                with open(default_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                cls._apply_params(config, data)
                logger.info(f"[{store_id}] 평가 설정 로드: 기본 템플릿")
                return config
            except Exception as e:
                logger.warning(f"[{store_id}] 기본 템플릿 로드 실패: {e}")

        # 5. 모두 실패 시 코드 내 기본값 사용
        logger.info(f"[{store_id}] 평가 설정: 코드 기본값 사용")
        return config

    @classmethod
    def _load_from_db(cls, store_id: str) -> Optional[Dict[str, Any]]:
        """DB에서 매장별 파라미터 로드

        Args:
            store_id: 매장 코드

        Returns:
            파라미터 dict 또는 None
        """
        try:
            from src.db.repository_multi_store import StoreEvalParamsRepository
            repo = StoreEvalParamsRepository()
            params = repo.get_all_params(store_id)
            return params if params else None
        except Exception as e:
            logger.debug(f"[{store_id}] DB 로드 실패 (정상, 다음 단계로): {e}")
            return None

    @classmethod
    def _load_from_path(cls, config: "EvalConfig", filepath: Path) -> "EvalConfig":
        """특정 파일 경로에서 로드 (레거시 호환성)

        Args:
            config: 기본 config 인스턴스
            filepath: 파일 경로

        Returns:
            EvalConfig 인스턴스
        """
        if not filepath.exists():
            logger.info(f"평가 설정 파일 없음: {filepath} → 기본값 사용")
            return config

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            cls._apply_params(config, data)
            logger.info(f"평가 설정 로드: {filepath}")
        except Exception as e:
            logger.warning(f"평가 설정 로드 실패 (기본값 사용): {e}")

        return config

    @classmethod
    def _apply_params(cls, config: "EvalConfig", data: Dict[str, Any]) -> None:
        """파라미터 dict를 config 인스턴스에 적용

        Args:
            config: 적용할 config 인스턴스
            data: 파라미터 dict
        """
        for name in config._param_names():
            if name in data:
                spec: ParamSpec = getattr(config, name)
                saved_value = data[name].get("value", spec.default)
                spec.value = float(spec.clamp(saved_value))
                spec.locked = data[name].get("locked", False)

    def update_param(self, name: str, new_value: float) -> Optional[float]:
        """
        파라미터 값 업데이트 (변경폭 제한 적용)

        Args:
            name: 파라미터 이름
            new_value: 새 값

        Returns:
            적용된 최종 값, 또는 None (잘못된 이름)
        """
        if not hasattr(self, name):
            return None

        spec: ParamSpec = getattr(self, name)
        delta = new_value - spec.value
        final_value = spec.apply_delta(delta)
        old_value = spec.value
        spec.value = final_value

        if old_value != final_value:
            logger.info(f"파라미터 변경: {name} {old_value:.4f} → {final_value:.4f} (요청: {new_value:.4f})")

        return final_value

    def normalize_weights(self) -> None:
        """인기도 가중치 합계를 1.0으로 정규화

        weight_daily_avg, weight_sell_day_ratio, weight_trend의
        합이 1.0이 되도록 비례 조정한다.
        """
        total = (
            self.weight_daily_avg.value
            + self.weight_sell_day_ratio.value
            + self.weight_trend.value
        )
        if total > 0:
            self.weight_daily_avg.value = round(self.weight_daily_avg.value / total, 4)
            self.weight_sell_day_ratio.value = round(self.weight_sell_day_ratio.value / total, 4)
            self.weight_trend.value = round(
                1.0 - self.weight_daily_avg.value - self.weight_sell_day_ratio.value,
                4
            )

    def diff_from_default(self) -> Dict[str, Dict[str, float]]:
        """기본값 대비 변경된 파라미터 반환

        Returns:
            {파라미터명: {"current", "default", "delta"}} 딕셔너리 (변경된 것만)
        """
        changes = {}
        for name in self._param_names():
            spec: ParamSpec = getattr(self, name)
            if abs(spec.value - spec.default) > 1e-6:
                changes[name] = {
                    "current": spec.value,
                    "default": spec.default,
                    "delta": round(spec.value - spec.default, 6),
                }
        return changes
