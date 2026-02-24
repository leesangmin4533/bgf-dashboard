"""
비용 최적화 모듈 (Cost Optimizer)
- 품절비용 vs 폐기비용 밸런스 기반 마진 인식 발주 조정
- sell_price, margin_rate 데이터를 활용하여 상품별 안전재고/폐기/SKIP 계수 산출
- 마진 × 회전율 2D 매트릭스 + 판매비중 보너스
"""

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from src.infrastructure.database.repos import ProductDetailRepository
from src.db.store_query import store_filter
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 설정 파일 경로
CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
COST_CONFIG_PATH = CONFIG_DIR / "eval_params.json"
DB_PATH = Path(__file__).parent.parent.parent / "data" / "bgf_sales.db"

# 기본 설정값 (eval_params.json에 cost_optimization 섹션이 없을 때 사용)
DEFAULT_COST_CONFIG = {
    "enabled": True,
    "cost_ratio_high": 0.5,
    "cost_ratio_mid": 0.3,
    "cost_ratio_low": 0.15,
    "safety_multiplier_high": 1.3,
    "safety_multiplier_mid": 1.1,
    "safety_multiplier_low": 0.85,
    "disuse_modifier_high": 1.15,
    "disuse_modifier_low": 0.85,
    "skip_threshold_high_offset": 0.5,
    "skip_threshold_low_offset": -0.5,
    "turnover_high_threshold": 5.0,
    "turnover_mid_threshold": 2.0,
    "category_share_bonus_threshold": 0.10,
    "category_share_bonus_value": 0.05,
}

# 기본 2D 매트릭스 (margin_level × turnover_level)
DEFAULT_MARGIN_MULTIPLIER_MATRIX = {
    "high": {"high": 1.35, "mid": 1.30, "low": 1.15},
    "mid":  {"high": 1.20, "mid": 1.10, "low": 1.00},
    "base": {"high": 1.10, "mid": 1.00, "low": 0.90},
    "low":  {"high": 0.95, "mid": 0.85, "low": 0.80},
}

DEFAULT_DISUSE_MODIFIER_MATRIX = {
    "high": {"high": 1.20, "mid": 1.15, "low": 1.05},
    "mid":  {"high": 1.10, "mid": 1.00, "low": 0.95},
    "base": {"high": 1.00, "mid": 1.00, "low": 0.90},
    "low":  {"high": 0.90, "mid": 0.85, "low": 0.80},
}

DEFAULT_SKIP_OFFSET_MATRIX = {
    "high": {"high": 0.7, "mid": 0.5, "low": 0.3},
    "mid":  {"high": 0.3, "mid": 0.0, "low": -0.2},
    "base": {"high": 0.0, "mid": 0.0, "low": -0.3},
    "low":  {"high": -0.3, "mid": -0.5, "low": -0.7},
}


@dataclass
class CostInfo:
    """상품별 비용 최적화 정보"""
    sell_price: Optional[int] = None
    margin_rate: Optional[float] = None
    cost_ratio: float = 0.0
    margin_multiplier: float = 1.0
    disuse_modifier: float = 1.0
    skip_offset: float = 0.0
    enabled: bool = False
    turnover_level: str = "unknown"
    category_share: float = 0.0
    share_bonus: float = 0.0
    composite_score: float = 0.0


class CostOptimizer:
    """마진 × 회전율 2D 매트릭스 기반 발주 비용 최적화"""

    def __init__(self, store_id: Optional[str] = None):
        self._config = self._load_config()
        self._product_repo = ProductDetailRepository()
        self._cache: Dict[str, CostInfo] = {}
        self.store_id = store_id
        self._category_share_cache: Optional[Dict[str, float]] = None
        if self._config["enabled"]:
            logger.info("[비용최적화] 모듈 활성화됨")

    def _load_config(self) -> dict:
        """eval_params.json에서 cost_optimization 섹션 로드"""
        config = DEFAULT_COST_CONFIG.copy()
        try:
            if COST_CONFIG_PATH.exists():
                with open(COST_CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                saved = data.get("cost_optimization", {})
                for key, default_val in DEFAULT_COST_CONFIG.items():
                    if key in saved:
                        config[key] = saved[key]
                # 2D 매트릭스 로드 (있으면 사용)
                for matrix_key in ("margin_multiplier_matrix", "disuse_modifier_matrix", "skip_offset_matrix"):
                    if matrix_key in saved:
                        config[matrix_key] = saved[matrix_key]
        except Exception as e:
            logger.warning(f"[비용최적화] 설정 로드 실패 (기본값 사용): {e}")
        return config

    @property
    def enabled(self) -> bool:
        """비용 최적화 활성 여부"""
        return self._config["enabled"]

    def get_item_cost_info(self, item_cd: str, daily_avg: float = 0.0, mid_cd: str = "") -> CostInfo:
        """상품별 비용 정보 조회 및 계수 산출

        Args:
            item_cd: 상품코드
            daily_avg: 일평균 판매량 (회전율 판정용, 0이면 unknown)
            mid_cd: 중분류 코드 (판매비중 조회용)

        Returns:
            CostInfo (margin_rate NULL이면 enabled=False)
        """
        if not self._config["enabled"]:
            return CostInfo()

        cache_key = f"{item_cd}:{round(daily_avg)}:{mid_cd}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        cost_info = self._calculate_cost_info(item_cd, daily_avg, mid_cd)
        self._cache[cache_key] = cost_info
        return cost_info

    def get_bulk_cost_info(self, item_codes: List[str]) -> Dict[str, CostInfo]:
        """다수 상품 비용 정보 일괄 조회 (1D 호환)

        Args:
            item_codes: 상품코드 리스트

        Returns:
            {item_cd: CostInfo, ...}
        """
        if not self._config["enabled"]:
            return {ic: CostInfo() for ic in item_codes}

        result = {}
        uncached = []
        for ic in item_codes:
            cache_key = f"{ic}:0:"
            if cache_key in self._cache:
                result[ic] = self._cache[cache_key]
            else:
                uncached.append(ic)

        if uncached:
            for ic in uncached:
                cost_info = self._calculate_cost_info(ic)
                cache_key = f"{ic}:0:"
                self._cache[cache_key] = cost_info
                result[ic] = cost_info

        return result

    def clear_cache(self):
        """캐시 초기화 (세션 시작 시 호출)"""
        self._cache.clear()
        self._category_share_cache = None

    def _classify_turnover(self, daily_avg: float) -> str:
        """회전율 분류: high(≥5), mid(2~5), low(<2)"""
        cfg = self._config
        if daily_avg >= cfg.get("turnover_high_threshold", 5.0):
            return "high"
        elif daily_avg >= cfg.get("turnover_mid_threshold", 2.0):
            return "mid"
        else:
            return "low"

    def _get_margin_level(self, cost_ratio: float) -> str:
        """마진 구간 분류"""
        cfg = self._config
        if cost_ratio >= cfg["cost_ratio_high"]:
            return "high"
        elif cost_ratio >= cfg["cost_ratio_mid"]:
            return "mid"
        elif cost_ratio >= cfg["cost_ratio_low"]:
            return "base"
        else:
            return "low"

    def _lookup_matrix(self, matrix: Dict, margin_level: str, turnover_level: str, default: float = 1.0) -> float:
        """2D 매트릭스에서 값 조회"""
        row = matrix.get(margin_level, {})
        return row.get(turnover_level, default)

    def _get_margin_multiplier_2d(self, cost_ratio: float, turnover_level: str) -> float:
        """2D 안전재고 계수"""
        margin_level = self._get_margin_level(cost_ratio)
        matrix = self._config.get("margin_multiplier_matrix", DEFAULT_MARGIN_MULTIPLIER_MATRIX)

        if turnover_level == "unknown":
            # 1D 호환: unknown이면 기존 1D 로직 사용
            return self._get_margin_multiplier_1d(cost_ratio)

        m = self._lookup_matrix(matrix, margin_level, turnover_level, 1.0)
        return max(0.7, min(1.5, m))

    def _get_disuse_modifier_2d(self, cost_ratio: float, turnover_level: str) -> float:
        """2D 폐기계수 보정"""
        margin_level = self._get_margin_level(cost_ratio)
        matrix = self._config.get("disuse_modifier_matrix", DEFAULT_DISUSE_MODIFIER_MATRIX)

        if turnover_level == "unknown":
            return self._get_disuse_modifier_1d(cost_ratio)

        return self._lookup_matrix(matrix, margin_level, turnover_level, 1.0)

    def _get_skip_offset_2d(self, cost_ratio: float, turnover_level: str) -> float:
        """2D SKIP 임계값 오프셋"""
        margin_level = self._get_margin_level(cost_ratio)
        matrix = self._config.get("skip_offset_matrix", DEFAULT_SKIP_OFFSET_MATRIX)

        if turnover_level == "unknown":
            return self._get_skip_offset_1d(cost_ratio)

        return self._lookup_matrix(matrix, margin_level, turnover_level, 0.0)

    # --- 1D 폴백 (기존 로직, unknown turnover 시 사용) ---

    def _get_margin_multiplier_1d(self, cost_ratio: float) -> float:
        """안전재고 계수: cost_ratio 구간별 (0.85~1.3) — 1D 폴백"""
        cfg = self._config
        if cost_ratio >= cfg["cost_ratio_high"]:
            m = cfg["safety_multiplier_high"]
        elif cost_ratio >= cfg["cost_ratio_mid"]:
            m = cfg["safety_multiplier_mid"]
        elif cost_ratio >= cfg["cost_ratio_low"]:
            m = 1.0
        else:
            m = cfg["safety_multiplier_low"]
        return max(0.7, min(1.5, m))

    def _get_disuse_modifier_1d(self, cost_ratio: float) -> float:
        """폐기계수 보정: 고마진→완화, 저마진→강화 — 1D 폴백"""
        cfg = self._config
        if cost_ratio >= cfg["cost_ratio_high"]:
            return cfg["disuse_modifier_high"]
        elif cost_ratio < cfg["cost_ratio_low"]:
            return cfg["disuse_modifier_low"]
        return 1.0

    def _get_skip_offset_1d(self, cost_ratio: float) -> float:
        """SKIP 임계값 오프셋 (일) — 1D 폴백"""
        cfg = self._config
        if cost_ratio >= cfg["cost_ratio_high"]:
            return cfg["skip_threshold_high_offset"]
        elif cost_ratio < cfg["cost_ratio_low"]:
            return cfg["skip_threshold_low_offset"]
        return 0.0

    # --- 판매비중 ---

    def _calculate_category_shares(self) -> Dict[str, float]:
        """중분류별 판매비중 계산 (30일 기준, store_id 필터)"""
        if self._category_share_cache is not None:
            return self._category_share_cache

        shares: Dict[str, float] = {}
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                sf, sp = store_filter("ds", self.store_id)
                cursor = conn.cursor()
                cursor.execute(f"""
                    SELECT p.mid_cd, SUM(ds.sale_qty) as total_qty
                    FROM daily_sales ds
                    JOIN products p ON ds.item_cd = p.item_cd
                    WHERE ds.sale_date >= date('now', '-30 days')
                    {sf}
                    GROUP BY p.mid_cd
                """, sp)

                rows = cursor.fetchall()
                grand_total = sum(r["total_qty"] for r in rows) or 1
                shares = {r["mid_cd"]: r["total_qty"] / grand_total for r in rows}
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[비용최적화] 판매비중 계산 실패: {e}")

        self._category_share_cache = shares
        return shares

    def _calculate_cost_info(self, item_cd: str, daily_avg: float = 0.0, mid_cd: str = "") -> CostInfo:
        """상품별 비용 정보 계산"""
        try:
            detail = self._product_repo.get(item_cd)
        except Exception as e:
            logger.warning(f"[비용최적화] DB 조회 실패 {item_cd}: {e}")
            return CostInfo()

        if not detail:
            return CostInfo()

        sell_price = detail.get("sell_price")
        margin_rate = detail.get("margin_rate")

        # margin_rate가 없거나 비정상이면 기본값
        if margin_rate is None or margin_rate <= 0 or margin_rate >= 100:
            return CostInfo(sell_price=sell_price, margin_rate=margin_rate)

        cost_ratio = margin_rate / (100.0 - margin_rate)

        # 회전율 분류
        turnover_level = self._classify_turnover(daily_avg) if daily_avg > 0 else "unknown"

        # 2D 매트릭스 조회 (unknown이면 1D 폴백)
        margin_multiplier = self._get_margin_multiplier_2d(cost_ratio, turnover_level)
        disuse_modifier = self._get_disuse_modifier_2d(cost_ratio, turnover_level)
        skip_offset = self._get_skip_offset_2d(cost_ratio, turnover_level)

        # 판매비중 보너스
        category_share = 0.0
        share_bonus = 0.0
        if mid_cd and self.store_id:
            shares = self._calculate_category_shares()
            category_share = shares.get(mid_cd, 0.0)
            share_threshold = self._config.get("category_share_bonus_threshold", 0.10)
            bonus_value = self._config.get("category_share_bonus_value", 0.05)
            if category_share >= share_threshold:
                share_bonus = bonus_value

        composite_score = margin_multiplier + share_bonus

        return CostInfo(
            sell_price=sell_price,
            margin_rate=margin_rate,
            cost_ratio=cost_ratio,
            margin_multiplier=margin_multiplier,
            disuse_modifier=disuse_modifier,
            skip_offset=skip_offset,
            enabled=True,
            turnover_level=turnover_level,
            category_share=category_share,
            share_bonus=share_bonus,
            composite_score=composite_score,
        )
