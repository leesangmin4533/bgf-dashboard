"""
CategoryStrategy — 카테고리별 예측 전략 인터페이스

15개 카테고리의 if/elif 체인을 Strategy 패턴으로 대체합니다.
각 카테고리 Strategy 클래스는 기존 함수를 래핑합니다 (알고리즘 불변).
"""

from abc import ABC, abstractmethod
from typing import Optional, Tuple, Any, Dict


class CategoryStrategy(ABC):
    """카테고리별 예측 전략 추상 인터페이스

    Usage:
        strategy = registry.get_strategy(mid_cd)
        safety_stock, pattern = strategy.calculate_safety_stock(...)
        weekday_coef = strategy.get_weekday_coefficient(weekday)
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """전략 이름 (예: 'beer', 'tobacco')"""

    @abstractmethod
    def matches(self, mid_cd: str) -> bool:
        """이 전략이 주어진 mid_cd를 처리하는지 여부"""

    @abstractmethod
    def calculate_safety_stock(
        self,
        mid_cd: str,
        daily_avg: float,
        expiration_days: int,
        item_cd: str,
        current_stock: int,
        pending_qty: int,
        store_id: str = "",
        **kwargs,
    ) -> Tuple[float, Optional[Any]]:
        """안전재고 및 카테고리 패턴 계산

        Args:
            mid_cd: 중분류 코드
            daily_avg: 일평균 판매량
            expiration_days: 유통기한 (일)
            item_cd: 상품 코드
            current_stock: 현재 재고
            pending_qty: 미입고 수량
            store_id: 매장 코드

        Returns:
            (safety_stock, pattern_result) 튜플
            pattern_result는 카테고리별 추가 정보 (None 가능)
        """

    def get_weekday_coefficient(self, weekday: int) -> Optional[float]:
        """요일별 계수 반환

        Args:
            weekday: 요일 (0=월, 6=일)

        Returns:
            계수 (None이면 기본값 사용)
        """
        return None

    def enrich_prediction_result(
        self,
        result: Dict[str, Any],
        pattern: Any,
    ) -> Dict[str, Any]:
        """예측 결과에 카테고리별 추가 정보 반영

        Args:
            result: 기본 예측 결과 딕셔너리
            pattern: calculate_safety_stock에서 반환된 패턴 정보

        Returns:
            카테고리 정보가 추가된 예측 결과
        """
        return result

    def get_max_stock(
        self,
        mid_cd: str,
        daily_avg: float,
        expiration_days: int,
        **kwargs,
    ) -> Optional[int]:
        """최대 재고 한도 반환

        Returns:
            최대 재고 (None이면 카테고리 기본 로직 사용)
        """
        return None
