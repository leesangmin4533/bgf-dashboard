"""
AssociationAdjuster — 예측 시점에서 연관 상품 부스트 적용

예측 시 호출되며, 사전 채굴된 연관 규칙과 최근 판매 실적을
참조하여 발주량 조정 계수를 반환한다.

- 규칙 캐시: DB에서 한 번 로드 후 TTL 동안 재사용 (성능)
- 트리거 캐시: 최근 N일 판매/평균 비율 (공유)
- mid 레벨 우선: 통계적 안정성 확보

Usage:
    adjuster = AssociationAdjuster(store_id="46513")
    boost = adjuster.get_association_boost("8800279676348", "002")
    # → 1.08 (8% 부스트)
"""

import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

from src.prediction.association.association_miner import (
    AssociationRule,
    calculate_association_boost,
)
from src.infrastructure.database.connection import DBRouter
from src.utils.logger import get_logger

logger = get_logger(__name__)


class AssociationAdjuster:
    """연관 상품 기반 발주량 조정기"""

    def __init__(
        self,
        store_id: str,
        db_path: Optional[str] = None,
        params: Optional[Dict] = None,
    ):
        self.store_id = store_id

        if db_path:
            self._db_path = db_path
        else:
            self._db_path = str(DBRouter.get_store_db_path(store_id))

        # 설정 (PREDICTION_PARAMS["association"]에서 주입)
        defaults = {
            "enabled": True,
            "max_boost": 1.15,
            "min_lift": 1.3,
            "min_confidence": 0.5,
            "trigger_lookback_days": 2,
            "cache_ttl_seconds": 300,
        }
        self.params = {**defaults, **(params or {})}

        # 캐시
        self._rule_cache: Dict[str, List[AssociationRule]] = {}
        self._cache_loaded_at: Optional[datetime] = None
        self._trigger_cache: Dict[str, float] = {}
        self._trigger_loaded_at: Optional[datetime] = None

    def get_association_boost(self, item_cd: str, mid_cd: str) -> float:
        """
        연관 규칙 기반 부스트 계수 반환

        Args:
            item_cd: 예측 대상 상품 코드
            mid_cd: 예측 대상 중분류 코드

        Returns:
            부스트 계수 (1.0 = 변화 없음)
        """
        if not self.params["enabled"]:
            return 1.0

        try:
            self._ensure_caches()
        except Exception as e:
            logger.debug(f"[연관분석] 캐시 로드 실패: {e}")
            return 1.0

        # mid 레벨 규칙 우선 조회
        mid_rules = self._rule_cache.get(mid_cd, [])
        mid_boost = 1.0
        if mid_rules:
            mid_boost = calculate_association_boost(
                rules=mid_rules,
                recent_triggers=self._trigger_cache,
                max_boost=self.params["max_boost"],
                min_lift=self.params["min_lift"],
                min_confidence=self.params["min_confidence"],
            )

        # mid 부스트가 없으면 item 레벨 시도
        item_boost = 1.0
        if mid_boost <= 1.0:
            item_rules = self._rule_cache.get(item_cd, [])
            if item_rules:
                item_boost = calculate_association_boost(
                    rules=item_rules,
                    recent_triggers=self._trigger_cache,
                    max_boost=self.params["max_boost"],
                    min_lift=self.params["min_lift"],
                    min_confidence=self.params["min_confidence"],
                )

        return max(mid_boost, item_boost)

    def get_association_score(self, item_cd: str, mid_cd: str) -> float:
        """ML feature용 연관 점수 (0.0~1.0 정규화)"""
        boost = self.get_association_boost(item_cd, mid_cd)
        max_boost = self.params["max_boost"]
        if max_boost <= 1.0:
            return 0.0
        return min(1.0, (boost - 1.0) / (max_boost - 1.0))

    # -----------------------------------------------------------------
    # 캐시 관리
    # -----------------------------------------------------------------

    def _ensure_caches(self):
        """캐시 유효성 확인 및 갱신"""
        now = datetime.now()
        ttl = self.params["cache_ttl_seconds"]

        if (self._cache_loaded_at is None or
                (now - self._cache_loaded_at).total_seconds() > ttl):
            self._load_rule_cache()

        if (self._trigger_loaded_at is None or
                (now - self._trigger_loaded_at).total_seconds() > ttl):
            self._load_trigger_cache()

    def _load_rule_cache(self):
        """모든 규칙을 item_b 기준으로 캐시에 로드"""
        conn = sqlite3.connect(self._db_path, timeout=10)
        try:
            cursor = conn.execute("""
                SELECT item_a, item_b, rule_level, support, confidence,
                       lift, correlation, sample_days
                FROM association_rules
                WHERE lift >= ?
                ORDER BY lift DESC
            """, (self.params["min_lift"],))

            cache: Dict[str, List[AssociationRule]] = defaultdict(list)
            for row in cursor:
                rule = AssociationRule(
                    item_a=row[0], item_b=row[1], rule_level=row[2],
                    support=row[3], confidence=row[4], lift=row[5],
                    correlation=row[6], sample_days=row[7],
                )
                cache[rule.item_b].append(rule)

            self._rule_cache = dict(cache)
            self._cache_loaded_at = datetime.now()
            total = sum(len(v) for v in cache.values())
            if total > 0:
                logger.debug(f"[연관분석] 규칙 캐시 로드: {total}건")
        except sqlite3.OperationalError:
            # 테이블 미존재 (마이그레이션 전) → 빈 캐시
            self._rule_cache = {}
            self._cache_loaded_at = datetime.now()
        finally:
            conn.close()

    def _load_trigger_cache(self):
        """최근 N일 판매 실적으로 트리거 비율 계산

        각 item/mid의 최근 판매량 / 60일 일평균 비율.
        비율 > 1.0이면 "최근 잘 팔림" (트리거 가능).
        """
        lookback = self.params["trigger_lookback_days"]
        conn = sqlite3.connect(self._db_path, timeout=10)
        try:
            cursor = conn.execute("""
                WITH recent AS (
                    SELECT item_cd, mid_cd, AVG(sale_qty) as recent_avg
                    FROM daily_sales
                    WHERE sales_date >= date('now', '-' || ? || ' days')
                      AND store_id = ?
                      AND sale_qty > 0
                    GROUP BY item_cd
                ),
                baseline AS (
                    SELECT item_cd, mid_cd, AVG(sale_qty) as base_avg
                    FROM daily_sales
                    WHERE sales_date >= date('now', '-60 days')
                      AND store_id = ?
                      AND sale_qty > 0
                    GROUP BY item_cd
                )
                SELECT r.item_cd, r.mid_cd,
                       r.recent_avg / NULLIF(b.base_avg, 0) as ratio
                FROM recent r
                JOIN baseline b ON r.item_cd = b.item_cd
                WHERE b.base_avg > 0
            """, (lookback, self.store_id, self.store_id))

            trigger: Dict[str, float] = {}
            mid_ratios: Dict[str, List[float]] = defaultdict(list)

            for row in cursor:
                item_cd, mid_cd, ratio = row
                if ratio and ratio > 0:
                    trigger[item_cd] = ratio
                    mid_ratios[mid_cd].append(ratio)

            # mid 레벨 집계 (평균)
            for mid_cd, ratios in mid_ratios.items():
                trigger[mid_cd] = sum(ratios) / len(ratios)

            self._trigger_cache = trigger
            self._trigger_loaded_at = datetime.now()
            logger.debug(f"[연관분석] 트리거 캐시 로드: {len(trigger)}건")
        except sqlite3.OperationalError:
            self._trigger_cache = {}
            self._trigger_loaded_at = datetime.now()
        finally:
            conn.close()
