"""
AssociationMiner — 일별 판매 동시발생 패턴 분석

편의점 상품 간 연관 규칙을 채굴한다.
트랜잭션 레벨 데이터가 없으므로 일별 집계(daily_sales) 기반으로
"같은 날 평균 이상으로 팔린 상품 쌍"의 패턴을 분석한다.

- mid 레벨 (중분류, ~30개): 통계적으로 안정적, 전체 카테고리 쌍 분석
- item 레벨 (상품, 고빈도만): 일평균 ≥ 1.0인 상품만 (노이즈 방지)

Usage:
    miner = AssociationMiner(store_id="46513")
    result = miner.mine_all()
    # → {"mid_rules": 45, "item_rules": 120, "total": 165}
"""

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np

from src.infrastructure.database.connection import DBRouter
from src.utils.logger import get_logger

logger = get_logger(__name__)


# =====================================================================
# 도메인 모델
# =====================================================================

@dataclass(frozen=True)
class AssociationRule:
    """연관 규칙 값 객체"""
    item_a: str           # 선행 상품/카테고리 코드
    item_b: str           # 후행 상품/카테고리 코드
    rule_level: str       # 'item' or 'mid'
    support: float        # 지지도
    confidence: float     # 신뢰도
    lift: float           # 리프트
    correlation: float    # 피어슨 상관계수
    sample_days: int      # 분석 일수


# =====================================================================
# 부스트 계수 계산 (순수 함수)
# =====================================================================

def calculate_association_boost(
    rules: List[AssociationRule],
    recent_triggers: Dict[str, float],
    max_boost: float = 1.15,
    min_lift: float = 1.3,
    min_confidence: float = 0.5,
) -> float:
    """
    연관 상품의 최근 판매 실적으로 부스트 계수 계산

    Args:
        rules: item_b에 대한 연관 규칙 목록
        recent_triggers: {item_a: 최근판매/평균 비율} (>1.0이면 평균 이상)
        max_boost: 최대 부스트 비율 (예: 1.15 = 15%)
        min_lift: 유효 규칙의 최소 lift
        min_confidence: 유효 규칙의 최소 confidence

    Returns:
        부스트 계수 (1.0 = 변화 없음, >1.0 = 상향 조정)
    """
    if not rules or not recent_triggers:
        return 1.0

    weighted_boost = 0.0
    total_weight = 0.0

    for rule in rules:
        if rule.lift < min_lift or rule.confidence < min_confidence:
            continue

        trigger_ratio = recent_triggers.get(rule.item_a, 0.0)
        if trigger_ratio <= 1.0:
            continue  # 평균 이하면 트리거 안 됨

        # 가중치: lift × confidence (품질 지표)
        weight = rule.lift * rule.confidence
        # 기여: 선행 상품의 초과 비율에 비례 (최대 0.5)
        contribution = min(trigger_ratio - 1.0, 0.5) * rule.confidence
        weighted_boost += weight * contribution
        total_weight += weight

    if total_weight == 0:
        return 1.0

    raw_boost = 1.0 + (weighted_boost / total_weight)
    return min(raw_boost, max_boost)


# =====================================================================
# 채굴 설정
# =====================================================================

DEFAULT_MINING_PARAMS = {
    "analysis_days": 60,         # 분석 기간
    "min_support": 0.05,         # 최소 지지도 (5%)
    "min_confidence": 0.4,       # 최소 신뢰도 (40%)
    "min_lift": 1.2,             # 최소 리프트
    "min_item_daily_avg": 1.0,   # item 레벨 최소 일평균
    "min_data_days": 14,         # 최소 데이터 일수
    "max_rules_per_item": 5,     # item_b당 최대 규칙 수
    "mid_level_enabled": True,
    "item_level_enabled": True,
}


# =====================================================================
# 채굴 엔진
# =====================================================================

class AssociationMiner:
    """일별 판매 동시발생 패턴 채굴기"""

    def __init__(self, store_id: str, db_path: Optional[str] = None,
                 params: Optional[Dict] = None):
        self.store_id = store_id
        self.params = {**DEFAULT_MINING_PARAMS, **(params or {})}

        if db_path:
            self._db_path = db_path
        else:
            self._db_path = str(DBRouter.get_store_db_path(store_id))

        self._item_mid_map: Dict[str, str] = {}

    def mine_all(self) -> Dict[str, int]:
        """전체 연관 규칙 채굴 (mid + item 레벨)

        Returns:
            {"mid_rules": N, "item_rules": M, "total": N+M}
        """
        result = {"mid_rules": 0, "item_rules": 0, "total": 0}

        # 1. 일별 판매 데이터 로드
        daily_data = self._load_daily_sales()
        if not daily_data:
            logger.warning("[연관분석] 판매 데이터 없음")
            return result

        logger.info(f"[연관분석] {len(daily_data)}일 데이터 로드 완료")

        # 2. 중분류 레벨 분석
        if self.params["mid_level_enabled"]:
            mid_rules = self._mine_mid_level(daily_data)
            self._save_rules(mid_rules)
            result["mid_rules"] = len(mid_rules)
            logger.info(f"[연관분석] 중분류 레벨: {len(mid_rules)}개 규칙")

        # 3. 상품 레벨 분석
        if self.params["item_level_enabled"]:
            item_rules = self._mine_item_level(daily_data)
            self._save_rules(item_rules)
            result["item_rules"] = len(item_rules)
            logger.info(f"[연관분석] 상품 레벨: {len(item_rules)}개 규칙")

        result["total"] = result["mid_rules"] + result["item_rules"]

        # 4. 오래된 규칙 정리
        self._clear_old_rules(keep_days=7)

        logger.info(f"[연관분석] 완료: 총 {result['total']}개 규칙 저장")
        return result

    # -----------------------------------------------------------------
    # 데이터 로드
    # -----------------------------------------------------------------

    def _load_daily_sales(self) -> Dict[str, Dict[str, float]]:
        """일별 판매 데이터 로드

        Returns:
            {date_str: {item_cd: sale_qty, ...}, ...}
        """
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute("""
                SELECT sales_date, item_cd, mid_cd, sale_qty
                FROM daily_sales
                WHERE sales_date >= date('now', '-' || ? || ' days')
                  AND store_id = ?
                  AND sale_qty > 0
                ORDER BY sales_date
            """, (self.params["analysis_days"], self.store_id))

            daily_data: Dict[str, Dict[str, float]] = defaultdict(dict)
            for row in cursor:
                daily_data[row["sales_date"]][row["item_cd"]] = row["sale_qty"]
                self._item_mid_map[row["item_cd"]] = row["mid_cd"]

            return dict(daily_data)
        finally:
            conn.close()

    # -----------------------------------------------------------------
    # 중분류 레벨 채굴
    # -----------------------------------------------------------------

    def _mine_mid_level(self, daily_data: Dict) -> List[AssociationRule]:
        """중분류 레벨 연관 분석"""
        mid_daily: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for date_str, items in daily_data.items():
            for item_cd, qty in items.items():
                mid_cd = self._item_mid_map.get(item_cd, "999")
                mid_daily[mid_cd][date_str] += qty

        return self._compute_rules(dict(mid_daily), rule_level="mid")

    # -----------------------------------------------------------------
    # 상품 레벨 채굴 (고빈도만)
    # -----------------------------------------------------------------

    def _mine_item_level(self, daily_data: Dict) -> List[AssociationRule]:
        """상품 레벨 연관 분석 (고빈도 상품만)"""
        item_daily: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for date_str, items in daily_data.items():
            for item_cd, qty in items.items():
                item_daily[item_cd][date_str] = qty

        # 고빈도 필터
        total_days = len(daily_data)
        min_avg = self.params["min_item_daily_avg"]
        high_volume = {
            item: dict(sales)
            for item, sales in item_daily.items()
            if sum(sales.values()) / max(total_days, 1) >= min_avg
        }

        logger.debug(f"[연관분석] 고빈도 상품: {len(high_volume)}개 / 전체 {len(item_daily)}개")
        return self._compute_rules(high_volume, rule_level="item")

    # -----------------------------------------------------------------
    # 연관 규칙 계산 (공통)
    # -----------------------------------------------------------------

    def _compute_rules(
        self,
        entity_daily: Dict[str, Dict[str, float]],
        rule_level: str,
    ) -> List[AssociationRule]:
        """연관 규칙 계산

        Args:
            entity_daily: {entity_id: {date: sale_qty}}
            rule_level: "item" or "mid"

        Returns:
            필터링된 AssociationRule 목록
        """
        entities = list(entity_daily.keys())
        if len(entities) < 2:
            return []

        all_dates = sorted(set(
            d for sales in entity_daily.values() for d in sales.keys()
        ))
        total_days = len(all_dates)

        if total_days < self.params["min_data_days"]:
            return []

        # 각 entity의 평균 및 "above average" 날짜 집합
        entity_avg: Dict[str, float] = {}
        entity_above: Dict[str, set] = {}

        for entity, date_sales in entity_daily.items():
            values = [date_sales.get(d, 0) for d in all_dates]
            avg = sum(values) / total_days
            entity_avg[entity] = avg
            entity_above[entity] = {d for d in all_dates if date_sales.get(d, 0) > avg}

        # 모든 (A, B) 쌍에 대해 규칙 계산
        min_support = self.params["min_support"]
        min_confidence = self.params["min_confidence"]
        min_lift = self.params["min_lift"]
        max_per_item = self.params["max_rules_per_item"]

        rules_by_b: Dict[str, List[AssociationRule]] = defaultdict(list)

        for a in entities:
            a_above = entity_above[a]
            if not a_above:
                continue
            p_a = len(a_above) / total_days

            for b in entities:
                if a == b:
                    continue
                b_above = entity_above[b]
                if not b_above:
                    continue
                p_b = len(b_above) / total_days

                # Support
                both_above = a_above & b_above
                support = len(both_above) / total_days
                if support < min_support:
                    continue

                # Confidence
                confidence = len(both_above) / len(a_above)
                if confidence < min_confidence:
                    continue

                # Lift
                lift = confidence / p_b if p_b > 0 else 0
                if lift < min_lift:
                    continue

                # Pearson correlation
                corr = 0.0
                try:
                    a_series = np.array([entity_daily[a].get(d, 0) for d in all_dates])
                    b_series = np.array([entity_daily[b].get(d, 0) for d in all_dates])
                    if np.std(a_series) > 0 and np.std(b_series) > 0:
                        corr = float(np.corrcoef(a_series, b_series)[0, 1])
                except Exception:
                    pass

                rule = AssociationRule(
                    item_a=a, item_b=b, rule_level=rule_level,
                    support=round(support, 4),
                    confidence=round(confidence, 4),
                    lift=round(lift, 4),
                    correlation=round(corr, 4),
                    sample_days=total_days,
                )
                rules_by_b[b].append(rule)

        # B별 상위 N개만 유지
        final_rules = []
        for b, b_rules in rules_by_b.items():
            sorted_rules = sorted(b_rules, key=lambda r: r.lift, reverse=True)
            final_rules.extend(sorted_rules[:max_per_item])

        return final_rules

    # -----------------------------------------------------------------
    # DB 저장/정리
    # -----------------------------------------------------------------

    def _save_rules(self, rules: List[AssociationRule]) -> int:
        """규칙 일괄 UPSERT"""
        if not rules:
            return 0

        conn = sqlite3.connect(self._db_path, timeout=30)
        try:
            now = datetime.now().isoformat()
            for rule in rules:
                conn.execute("""
                    INSERT OR REPLACE INTO association_rules
                    (item_a, item_b, rule_level, support, confidence, lift,
                     correlation, sample_days, computed_at, store_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    rule.item_a, rule.item_b, rule.rule_level,
                    rule.support, rule.confidence, rule.lift,
                    rule.correlation, rule.sample_days,
                    now, self.store_id,
                ))
            conn.commit()
            return len(rules)
        finally:
            conn.close()

    def _clear_old_rules(self, keep_days: int = 7) -> int:
        """오래된 규칙 정리"""
        conn = sqlite3.connect(self._db_path, timeout=30)
        try:
            cursor = conn.execute("""
                DELETE FROM association_rules
                WHERE computed_at < datetime('now', '-' || ? || ' days')
            """, (keep_days,))
            conn.commit()
            cleaned = cursor.rowcount
            if cleaned > 0:
                logger.info(f"[연관분석] 오래된 규칙 {cleaned}건 정리")
            return cleaned
        finally:
            conn.close()
