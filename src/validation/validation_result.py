"""
검증 결과 데이터 클래스

ValidationResult, ValidationError, ValidationWarning을 정의합니다.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime


@dataclass
class ValidationError:
    """검증 오류 정보

    치명적인 오류로 데이터 품질에 심각한 문제가 있음을 나타냅니다.
    """
    error_code: str                              # 'INVALID_ITEM_CD', 'NEGATIVE_QTY', etc.
    error_message: str                           # 사람이 읽을 수 있는 오류 메시지
    affected_item: str                           # 영향받은 상품코드
    metadata: Optional[Dict[str, Any]] = None    # 추가 정보 (expected, actual 등)

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환"""
        return {
            'code': self.error_code,
            'message': self.error_message,
            'item': self.affected_item,
            'metadata': self.metadata
        }


@dataclass
class ValidationWarning:
    """검증 경고 정보

    치명적이지는 않지만 주의가 필요한 이상 징후를 나타냅니다.
    """
    warning_code: str                                    # 'ANOMALY_3SIGMA', etc.
    warning_message: str                                 # 경고 메시지
    affected_item: str                                   # 영향받은 상품코드
    metadata: Dict[str, Any] = field(default_factory=dict)  # 추가 정보 (mean, stddev 등)

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환"""
        return {
            'code': self.warning_code,
            'message': self.warning_message,
            'item': self.affected_item,
            'metadata': self.metadata
        }


@dataclass
class ValidationResult:
    """검증 결과

    데이터 검증의 전체 결과를 담는 컨테이너입니다.
    """
    # 전체 결과
    is_valid: bool                               # 검증 통과 여부 (error가 없으면 True)

    # 통계
    total_count: int                             # 검증한 레코드 수
    passed_count: int                            # 통과한 레코드 수
    failed_count: int                            # 실패한 레코드 수

    # 상세 정보
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationWarning] = field(default_factory=list)

    # 메타데이터
    sales_date: str = ""                         # 검증 대상 날짜
    store_id: str = "46704"                      # 점포 ID
    validated_at: datetime = field(default_factory=datetime.now)

    def add_error(
        self,
        error_code: str,
        error_message: str,
        affected_item: str,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """오류 추가

        Args:
            error_code: 오류 코드
            error_message: 오류 메시지
            affected_item: 영향받은 상품코드
            metadata: 추가 정보
        """
        self.errors.append(ValidationError(
            error_code=error_code,
            error_message=error_message,
            affected_item=affected_item,
            metadata=metadata
        ))
        self.is_valid = False
        self.failed_count += 1

    def add_warning(
        self,
        warning_code: str,
        warning_message: str,
        affected_item: str,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """경고 추가

        Args:
            warning_code: 경고 코드
            warning_message: 경고 메시지
            affected_item: 영향받은 상품코드
            metadata: 추가 정보
        """
        self.warnings.append(ValidationWarning(
            warning_code=warning_code,
            warning_message=warning_message,
            affected_item=affected_item,
            metadata=metadata or {}
        ))

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환 (JSON 직렬화용)

        Returns:
            Dict: 검증 결과 딕셔너리
        """
        return {
            'is_valid': self.is_valid,
            'total_count': self.total_count,
            'passed_count': self.passed_count,
            'failed_count': self.failed_count,
            'errors': [e.to_dict() for e in self.errors],
            'warnings': [w.to_dict() for w in self.warnings],
            'sales_date': self.sales_date,
            'store_id': self.store_id,
            'validated_at': self.validated_at.isoformat()
        }

    def __str__(self) -> str:
        """문자열 표현"""
        status = "PASSED" if self.is_valid else "FAILED"
        return (
            f"ValidationResult({status}): "
            f"{self.passed_count}/{self.total_count} passed, "
            f"{len(self.errors)} errors, {len(self.warnings)} warnings"
        )
