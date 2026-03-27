"""BGF 프로젝트 커스텀 예외 계층.

사용법:
    from src.core.exceptions import DBException, ScrapingException

    try:
        conn = get_connection()
    except sqlite3.Error as e:
        raise DBException("DB 연결 실패", store_id=store_id) from e
"""


class AppException(Exception):
    """애플리케이션 최상위 예외. 모든 커스텀 예외의 부모.

    Args:
        message: 에러 메시지
        **context: 추가 컨텍스트 (store_id, item_cd, phase 등)
    """

    def __init__(self, message: str, **context):
        self.context = context
        super().__init__(message)

    def __str__(self):
        base = super().__str__()
        if self.context:
            ctx = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{base} [{ctx}]"
        return base


class DBException(AppException):
    """DB 연결/쿼리/마이그레이션 오류."""
    pass


class ScrapingException(AppException):
    """BGF 넥사크로 사이트 스크래핑 오류."""
    pass


class ValidationException(AppException):
    """입력 데이터 검증 오류."""
    pass


class PredictionException(AppException):
    """예측 로직 오류."""
    pass


class OrderException(AppException):
    """발주 실행 오류."""
    pass


class ConfigException(AppException):
    """설정 파일 로드/검증 오류."""
    pass


class AlertException(AppException):
    """알림 발송 오류 (비즈니스 로직에 영향 없음)."""
    pass
