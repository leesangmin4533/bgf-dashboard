"""
검증 규칙 관리

JSON 파일에서 검증 규칙을 로드하여 코드 수정 없이 규칙 변경 가능
"""
from typing import Dict, Any, Optional
from pathlib import Path
import json

from src.utils.logger import get_logger

logger = get_logger(__name__)


class ValidationRules:
    """검증 규칙 중앙 관리

    JSON 파일에서 검증 규칙을 로드하여 관리합니다.
    규칙 변경 시 코드 수정 없이 JSON 파일만 수정하면 됩니다.
    """

    DEFAULT_RULES = {
        # 상품코드 형식
        'item_code': {
            'length': 13,                        # 13자리 바코드
            'pattern': r'^\d{13}$',              # 숫자만
            'exclude_patterns': [                # 제외할 패턴 (테스트 데이터)
                r'^88\d{2}\d{5}1$'               # 88{mid_cd}00001
            ]
        },

        # 수량 검증
        'quantity': {
            'sale_qty': {'min': 0, 'max': 500},  # 판매수량 범위
            'ord_qty': {'min': 0, 'max': 1000},  # 발주수량 범위
            'stock_qty': {'min': 0, 'max': 2000} # 재고수량 범위
        },

        # 이상치 탐지
        'anomaly': {
            'method': '3sigma',                  # 3시그마 기준
            'window_days': 30,                   # 최근 30일 기준
            'min_samples': 7                     # 최소 7일 데이터 필요
        },

        # 재고 일관성
        'inventory_consistency': {
            'enabled': True,
            'tolerance': 0                       # 허용 오차 (0 = 정확히 일치)
        },

        # 중복 수집 감지
        'duplicate_detection': {
            'enabled': True,
            'check_window_days': 1               # 최근 1일 내 중복 체크
        }
    }

    def __init__(self, config_path: Optional[Path] = None):
        """초기화

        Args:
            config_path: JSON 설정 파일 경로 (기본값: config/validation_rules.json)
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent.parent / "config" / "validation_rules.json"

        self.config_path = config_path
        self.rules = self._load_rules()

    def _load_rules(self) -> Dict[str, Any]:
        """설정 파일에서 규칙 로드

        Returns:
            Dict: 검증 규칙 딕셔너리
        """
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    rules = json.load(f)
                logger.info(f"검증 규칙 로드: {self.config_path}")
                return rules
            except Exception as e:
                logger.warning(f"검증 규칙 로드 실패 ({e}), 기본값 사용")
                return self.DEFAULT_RULES.copy()
        else:
            # 설정 파일이 없으면 기본값 사용하고 생성
            logger.info(f"검증 규칙 파일 없음, 기본값으로 생성: {self.config_path}")
            self.save_rules(self.DEFAULT_RULES)
            return self.DEFAULT_RULES.copy()

    def save_rules(self, rules: Dict[str, Any]):
        """설정 파일 저장

        Args:
            rules: 저장할 규칙 딕셔너리
        """
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(rules, f, indent=2, ensure_ascii=False)
        logger.info(f"검증 규칙 저장: {self.config_path}")

    def get_rule(self, key: str, default: Any = None) -> Any:
        """규칙 값 조회

        Args:
            key: 규칙 키 (점으로 구분된 경로, 예: 'item_code.length')
            default: 기본값

        Returns:
            Any: 규칙 값
        """
        keys = key.split('.')
        value = self.rules
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
        return value if value is not None else default

    def update_rule(self, key: str, value: Any):
        """규칙 값 업데이트

        Args:
            key: 규칙 키 (점으로 구분된 경로)
            value: 새로운 값
        """
        keys = key.split('.')
        target = self.rules
        for k in keys[:-1]:
            if k not in target:
                target[k] = {}
            target = target[k]
        target[keys[-1]] = value
        self.save_rules(self.rules)
        logger.info(f"검증 규칙 업데이트: {key} = {value}")
