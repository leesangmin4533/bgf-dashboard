"""
발주 차이 데이터 기반 예측 피드백 조정기

order_analysis.db의 diff 데이터를 분석하여:
- 반복 제거 상품 → 수량 감소 페널티 (precision 향상)
- 반복 추가 상품 → 발주 후보 주입 (recall 향상)

연동:
- ImprovedPredictor.__init__() 에서 초기화
- predict(): 제거 페널티 적용 (order_qty × penalty)
- get_order_candidates(): 반복 추가 상품 주입
"""

from typing import Any, Dict, List, Optional

from src.settings.constants import (
    DIFF_FEEDBACK_ENABLED,
    DIFF_FEEDBACK_LOOKBACK_DAYS,
    DIFF_FEEDBACK_REMOVAL_THRESHOLDS,
    DIFF_FEEDBACK_ADDITION_MIN_COUNT,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DiffFeedbackAdjuster:
    """발주 차이 데이터 기반 예측 조정기

    order_analysis.db에서 제거/추가 패턴을 분석하여
    예측 수량에 페널티(제거 빈도↑ → 수량↓) 또는
    부스트(추가 빈도↑ → 수량↑/주입)를 적용한다.
    """

    def __init__(self, store_id: str, lookback_days: int = 0):
        """
        Args:
            store_id: 매장 코드
            lookback_days: 분석 기간 (0이면 상수값 사용)
        """
        self.store_id = store_id
        self._lookback_days = lookback_days or DIFF_FEEDBACK_LOOKBACK_DAYS
        self._removal_cache: Dict[str, Dict[str, Any]] = {}   # item_cd → stats
        self._addition_cache: Dict[str, Dict[str, Any]] = {}   # item_cd → stats
        self._cache_loaded = False

    @property
    def enabled(self) -> bool:
        return DIFF_FEEDBACK_ENABLED

    def load_feedback(self) -> None:
        """order_analysis.db에서 최근 N일 diff 통계 로드

        캐시에 item_cd별 제거/추가 횟수를 저장.
        """
        if self._cache_loaded:
            return

        try:
            from src.infrastructure.database.repos.order_analysis_repo import (
                OrderAnalysisRepository,
            )

            repo = OrderAnalysisRepository()

            # 제거 통계
            removal_stats = repo.get_item_removal_stats(
                self.store_id, days=self._lookback_days
            )
            for row in removal_stats:
                ic = row["item_cd"]
                self._removal_cache[ic] = {
                    "item_nm": row.get("item_nm", ""),
                    "mid_cd": row.get("mid_cd", ""),
                    "removal_count": row["removal_count"],
                    "total_appearances": row["total_appearances"],
                }

            # 추가 통계
            addition_stats = repo.get_item_addition_stats(
                self.store_id, days=self._lookback_days
            )
            for row in addition_stats:
                ic = row["item_cd"]
                self._addition_cache[ic] = {
                    "item_nm": row.get("item_nm", ""),
                    "mid_cd": row.get("mid_cd", ""),
                    "addition_count": row["addition_count"],
                    "avg_qty": row.get("avg_qty", 1),
                }

            self._cache_loaded = True
            logger.info(
                f"[Diff피드백] 로드 완료: "
                f"제거 {len(self._removal_cache)}건, "
                f"추가 {len(self._addition_cache)}건 "
                f"(최근 {self._lookback_days}일)"
            )

        except Exception as e:
            logger.warning(f"[Diff피드백] 로드 실패 (비활성): {e}")
            self._cache_loaded = True  # 재시도 방지

    def get_removal_penalty(self, item_cd: str) -> float:
        """제거 페널티 계수 반환 (0.3~1.0, 1.0=페널티 없음)

        수량 감소 방식: order_qty × penalty (목록에서 제외하지 않음)
        결과 order_qty는 호출 측에서 최소 1 보장.

        Args:
            item_cd: 상품 코드

        Returns:
            페널티 계수 (0.3 ~ 1.0)
        """
        if not self._cache_loaded:
            self.load_feedback()

        stats = self._removal_cache.get(item_cd)
        if not stats:
            return 1.0

        removal_count = stats["removal_count"]

        # 임계값을 내림차순으로 확인하여 가장 높은 임계값 적용
        penalty = 1.0
        for threshold in sorted(DIFF_FEEDBACK_REMOVAL_THRESHOLDS.keys()):
            if removal_count >= threshold:
                penalty = DIFF_FEEDBACK_REMOVAL_THRESHOLDS[threshold]

        return penalty

    def get_addition_boost(self, item_cd: str) -> Dict[str, Any]:
        """추가 부스트 정보 반환

        Args:
            item_cd: 상품 코드

        Returns:
            {
                'should_inject': bool,  - 강제 주입 대상 여부
                'boost_qty': int,       - 권장 발주 수량
                'addition_count': int,  - 추가 횟수
            }
        """
        if not self._cache_loaded:
            self.load_feedback()

        stats = self._addition_cache.get(item_cd)
        if not stats:
            return {
                "should_inject": False,
                "boost_qty": 0,
                "addition_count": 0,
            }

        count = stats["addition_count"]
        avg_qty = stats.get("avg_qty", 1)

        return {
            "should_inject": count >= DIFF_FEEDBACK_ADDITION_MIN_COUNT,
            "boost_qty": max(1, int(round(avg_qty))),
            "addition_count": count,
        }

    def get_frequently_added_items(
        self, min_count: int = 0
    ) -> List[Dict[str, Any]]:
        """반복 추가 상품 목록 (인사이트 5: 자동 주입용)

        Args:
            min_count: 최소 추가 횟수 (0이면 상수값 사용)

        Returns:
            [{item_cd, item_nm, mid_cd, count, avg_qty}, ...]
        """
        if not self._cache_loaded:
            self.load_feedback()

        threshold = min_count or DIFF_FEEDBACK_ADDITION_MIN_COUNT
        result = []
        for ic, stats in self._addition_cache.items():
            if stats["addition_count"] >= threshold:
                result.append({
                    "item_cd": ic,
                    "item_nm": stats.get("item_nm", ""),
                    "mid_cd": stats.get("mid_cd", ""),
                    "count": stats["addition_count"],
                    "avg_qty": max(1, int(round(stats.get("avg_qty", 1)))),
                })

        # 횟수 내림차순
        result.sort(key=lambda x: x["count"], reverse=True)
        return result
