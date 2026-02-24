"""
통합 로깅 모듈

사용법:
    from src.utils.logger import get_logger

    logger = get_logger(__name__)
    logger.info("발주 시작")
    logger.warning("재고 부족")
    logger.error("DB 연결 실패", exc_info=True)
"""

import logging
import sys
import io
from pathlib import Path
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from typing import Any, Callable, Optional, Set


def _ensure_utf8_stdout():
    """Windows CP949 콘솔에서 UTF-8 출력을 보장한다.

    sys.stdout/stderr 가 CP949(또는 비-UTF-8) 인코딩일 때
    io.TextIOWrapper 로 UTF-8 래핑하여 한글·특수문자 깨짐을 방지한다.
    이미 UTF-8 이면 아무 작업도 하지 않는다.
    """
    if sys.platform == "win32":
        for stream_name in ("stdout", "stderr"):
            stream = getattr(sys, stream_name, None)
            if stream and hasattr(stream, "encoding"):
                if (stream.encoding or "").lower().replace("-", "") != "utf8":
                    try:
                        wrapped = io.TextIOWrapper(
                            stream.buffer,
                            encoding="utf-8",
                            errors="replace",
                            line_buffering=True,
                        )
                        setattr(sys, stream_name, wrapped)
                    except Exception:
                        pass  # buffer 없는 환경(IDLE 등) 무시


# 모듈 임포트 시 1회 실행
_ensure_utf8_stdout()


# 로그 디렉토리
LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


class SafeRotatingFileHandler(RotatingFileHandler):
    """Windows 파일 잠금에 안전한 RotatingFileHandler

    Windows에서 OneDrive 동기화 등으로 로그 파일이 잠겨있을 때
    PermissionError를 무시하고 기존 파일에 계속 쓴다.
    """

    def doRollover(self):
        try:
            super().doRollover()
        except PermissionError:
            # 로테이션 실패 → stream이 닫혀있으면 다시 열기
            if self.stream is None and not self.delay:
                self.stream = self._open()


# 로그 포맷
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_FORMAT_SIMPLE = "%(asctime)s | %(levelname)-8s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


# 로그 레벨별 파일
LOG_FILES = {
    "main": LOG_DIR / "bgf_auto.log",           # 전체 로그
    "prediction": LOG_DIR / "prediction.log",    # 예측 관련
    "order": LOG_DIR / "order.log",              # 발주 관련
    "collector": LOG_DIR / "collector.log",      # 수집 관련
    "error": LOG_DIR / "error.log",              # 에러만
}

# 이미 설정된 로거 추적
_configured_loggers = set()


def setup_logger(
    name: str,
    level: int = logging.INFO,
    log_file: str = "main",
    console: bool = True,
    max_bytes: int = 20 * 1024 * 1024,  # 20MB
    backup_count: int = 10
) -> logging.Logger:
    """
    로거 설정

    Args:
        name: 로거 이름 (보통 __name__)
        level: 로그 레벨
        log_file: 로그 파일 키 ("main", "prediction", "order", "error")
        console: 콘솔 출력 여부
        max_bytes: 파일당 최대 크기
        backup_count: 백업 파일 수

    Returns:
        설정된 Logger
    """
    logger = logging.getLogger(name)

    # 이미 설정된 로거면 반환
    if name in _configured_loggers:
        return logger

    logger.setLevel(level)

    # 이미 핸들러가 있으면 스킵
    if logger.handlers:
        _configured_loggers.add(name)
        return logger

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
    simple_formatter = logging.Formatter(LOG_FORMAT_SIMPLE, DATE_FORMAT)

    # 파일 핸들러 (로테이션)
    file_path = LOG_FILES.get(log_file, LOG_FILES["main"])
    try:
        file_handler = SafeRotatingFileHandler(
            file_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"[WARN] 로그 파일 핸들러 설정 실패: {e}")

    # 에러 전용 파일 핸들러
    try:
        error_handler = SafeRotatingFileHandler(
            LOG_FILES["error"],
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        error_handler.setFormatter(formatter)
        error_handler.setLevel(logging.ERROR)
        logger.addHandler(error_handler)
    except Exception as e:
        print(f"[WARN] 에러 로그 파일 핸들러 설정 실패: {e}")

    # 콘솔 핸들러 (UTF-8 래핑된 stdout 사용)
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(simple_formatter)
        console_handler.setLevel(level)
        logger.addHandler(console_handler)

    # 상위 로거로 전파 방지
    logger.propagate = False

    _configured_loggers.add(name)
    return logger


def get_logger(name: str) -> logging.Logger:
    """
    로거 가져오기 (편의 함수)

    모듈별 자동 분류:
        - src.prediction.* → prediction.log
        - src.order.* → order.log
        - src.collectors.* → collector.log
        - 그 외 → main.log

    Args:
        name: 모듈 이름 (보통 __name__)

    Returns:
        모듈에 맞게 설정된 Logger 인스턴스
    """
    if "prediction" in name:
        return setup_logger(name, log_file="prediction")
    elif "order" in name:
        return setup_logger(name, log_file="order")
    elif "collector" in name:
        return setup_logger(name, log_file="collector")
    return setup_logger(name, log_file="main")


class LoggerMixin:
    """
    클래스용 로거 믹스인

    사용법:
        class OrderPredictor(LoggerMixin):
            def predict(self):
                self.logger.info("예측 시작")
    """

    @property
    def logger(self) -> logging.Logger:
        """클래스 전용 로거 인스턴스 반환

        Returns:
            해당 클래스 모듈에 맞게 설정된 Logger 인스턴스
        """
        if not hasattr(self, '_logger'):
            self._logger = get_logger(self.__class__.__module__)
        return self._logger


def log_with_context(
    _logger: logging.Logger,
    level: str,
    msg: str,
    exc_info: bool = False,
    **ctx: Any,
) -> None:
    """컨텍스트 키워드를 자동 포맷하는 로깅 헬퍼

    Args:
        _logger: 로거 인스턴스
        level: 로그 레벨 ("debug", "info", "warning", "error")
        msg: 로그 메시지
        exc_info: True면 예외 스택 트레이스 포함
        **ctx: 컨텍스트 키=값 쌍 (item_cd, store_id, phase 등)

    Usage:
        log_with_context(logger, "warning", "DB 조회 실패",
                        item_cd="123", store_id="S001", phase="1.55")
        # Output: "DB 조회 실패 | item_cd=123 | store_id=S001 | phase=1.55"
    """
    if ctx:
        ctx_str = " | ".join(f"{k}={v}" for k, v in ctx.items() if v is not None)
        if ctx_str:
            msg = f"{msg} | {ctx_str}"

    log_fn = getattr(_logger, level, None) or _logger.info
    log_fn(msg, exc_info=exc_info)


def log_function_call(func: Callable) -> Callable:
    """함수 호출 로깅 데코레이터

    Args:
        func: 로깅을 적용할 대상 함수

    Returns:
        로깅이 추가된 래퍼 함수
    """
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        logger = get_logger(func.__module__)
        logger.debug(f"함수 호출: {func.__name__}")
        try:
            result = func(*args, **kwargs)
            logger.debug(f"함수 완료: {func.__name__}")
            return result
        except Exception as e:
            logger.error(f"함수 오류: {func.__name__} - {e}", exc_info=True)
            raise
    return wrapper


# 전역 로거 (간단한 사용을 위해)
_root_logger: Optional[logging.Logger] = None

def info(msg: str) -> None:
    """전역 INFO 로그

    Args:
        msg: 로그 메시지
    """
    global _root_logger
    if _root_logger is None:
        _root_logger = get_logger("bgf_auto")
    _root_logger.info(msg)

def warning(msg: str) -> None:
    """전역 WARNING 로그

    Args:
        msg: 로그 메시지
    """
    global _root_logger
    if _root_logger is None:
        _root_logger = get_logger("bgf_auto")
    _root_logger.warning(msg)

def error(msg: str, exc_info: bool = False) -> None:
    """전역 ERROR 로그

    Args:
        msg: 로그 메시지
        exc_info: True면 예외 스택 트레이스 포함
    """
    global _root_logger
    if _root_logger is None:
        _root_logger = get_logger("bgf_auto")
    _root_logger.error(msg, exc_info=exc_info)

def debug(msg: str) -> None:
    """전역 DEBUG 로그

    Args:
        msg: 로그 메시지
    """
    global _root_logger
    if _root_logger is None:
        _root_logger = get_logger("bgf_auto")
    _root_logger.debug(msg)


def cleanup_old_logs(max_age_days: int = 30, max_file_mb: int = 50) -> None:
    """앱 시작 시 오래된 로그 파일 정리 + 비대해진 로그 파일 잘라내기

    OneDrive 파일 잠금으로 인해 RotatingFileHandler의 doRollover()가
    실패하여 로그 파일이 무한 성장하는 문제를 보완한다.

    Args:
        max_age_days: 이 일수보다 오래된 .log.N 파일 삭제
        max_file_mb: 이 크기(MB) 초과 시 로그 파일 잘라내기 (마지막 2MB 유지)
    """
    if not LOG_DIR.exists():
        return

    cutoff = datetime.now() - timedelta(days=max_age_days)
    max_bytes_limit = max_file_mb * 1024 * 1024

    for log_file in LOG_DIR.iterdir():
        if not log_file.is_file():
            continue

        try:
            # 1. 오래된 백업 파일(.log.1, .log.2 등) 삭제
            if log_file.suffix and log_file.suffix[1:].isdigit():
                mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                if mtime < cutoff:
                    log_file.unlink()
                    continue

            # 2. 메인 로그 파일이 max_file_mb 초과 시 잘라내기
            if log_file.suffix == ".log" and log_file.stat().st_size > max_bytes_limit:
                _truncate_log_file(log_file, keep_bytes=2 * 1024 * 1024)

        except PermissionError:
            pass  # OneDrive 잠금 시 건너뜀
        except Exception:
            pass  # 기타 OS 에러 무시


def _truncate_log_file(file_path: Path, keep_bytes: int = 2 * 1024 * 1024) -> None:
    """로그 파일을 잘라서 마지막 keep_bytes만 유지

    Args:
        file_path: 잘라낼 로그 파일 경로
        keep_bytes: 유지할 끝부분 바이트 수 (기본 2MB)
    """
    try:
        size = file_path.stat().st_size
        if size <= keep_bytes:
            return

        with open(file_path, 'rb') as f:
            f.seek(size - keep_bytes)
            # 줄 경계까지 이동 (잘린 줄 방지)
            f.readline()
            tail = f.read()

        with open(file_path, 'wb') as f:
            f.write(b"[LOG TRUNCATED - previous content removed]\n")
            f.write(tail)
    except PermissionError:
        pass  # OneDrive lock
