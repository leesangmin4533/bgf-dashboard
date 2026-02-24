"""
기본 수집기 클래스
- 모든 수집기의 공통 인터페이스 정의
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional
import time

from src.utils.logger import get_logger

logger = get_logger(__name__)


class BaseCollector(ABC):
    """데이터 수집기 기본 클래스"""

    def __init__(self, name: str = "BaseCollector") -> None:
        self.name = name
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None

    @abstractmethod
    def collect(self, target_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        데이터 수집 실행

        Args:
            target_date: 수집 대상 날짜 (YYYY-MM-DD)

        Returns:
            수집된 데이터 리스트
        """
        pass

    @abstractmethod
    def validate(self, data: List[Dict[str, Any]]) -> bool:
        """
        수집된 데이터 유효성 검증

        Args:
            data: 수집된 데이터

        Returns:
            유효성 여부
        """
        pass

    def run(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """
        수집 실행 및 결과 반환

        Args:
            target_date: 수집 대상 날짜

        Returns:
            {
                "success": bool,
                "data": List,
                "collected_at": str,
                "target_date": str,
                "duration": float,
                "error": str (optional)
            }
        """
        self.start_time = time.time()
        collected_at = datetime.now().isoformat()

        result = {
            "success": False,
            "data": [],
            "collected_at": collected_at,
            "target_date": target_date,
            "duration": 0,
            "error": None
        }

        try:
            logger.info(f"[{self.name}] Starting collection for {target_date}...")
            data = self.collect(target_date)

            if self.validate(data):
                result["success"] = True
                result["data"] = data
                logger.info(f"[{self.name}] Collection successful: {len(data)} items")
            else:
                result["error"] = "Validation failed"
                logger.warning(f"[{self.name}] Validation failed")

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"[{self.name}] Collection failed: {e}")

        finally:
            self.end_time = time.time()
            result["duration"] = self.end_time - self.start_time

        return result

    def get_duration(self) -> float:
        """수집 소요 시간 반환"""
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return 0
