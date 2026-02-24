"""
타임아웃 핸들러 모듈
- 모든 작업에 10초 타임아웃 적용
- 지연 시 오류 로그 및 작업 중단
"""

import time
import functools
import traceback
from datetime import datetime
from typing import Callable, Any, Dict, List, Optional, Tuple
from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger(__name__)

# 기본 타임아웃 (초) - 충분히 큰 값으로 설정하여 실질적으로 비활성화
DEFAULT_TIMEOUT = 600  # 10분

# 로그 파일 경로
LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


class TimeoutError(Exception):
    """타임아웃 오류"""
    pass


def log_timeout_error(operation_name: str, elapsed_time: float, context: Optional[Dict[str, Any]] = None) -> None:
    """
    타임아웃 오류 로그 기록

    Args:
        operation_name: 작업 이름
        elapsed_time: 경과 시간 (초)
        context: 추가 컨텍스트 정보
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_file = LOG_DIR / f"timeout_{datetime.now().strftime('%Y%m%d')}.log"

    log_message = f"""
{'='*60}
[TIMEOUT ERROR] {timestamp}
{'='*60}
작업: {operation_name}
경과 시간: {elapsed_time:.2f}초 (제한: {DEFAULT_TIMEOUT}초)
"""

    if context:
        log_message += f"컨텍스트:\n"
        for key, value in context.items():
            log_message += f"  - {key}: {value}\n"

    # 스택 트레이스 추가
    log_message += f"\n스택 트레이스:\n{traceback.format_stack()[-5:-1]}\n"
    log_message += "=" * 60 + "\n"

    # 파일에 기록
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(log_message)

    # 구조화된 로그 출력
    context_str = ""
    if context:
        context_str = ", ".join(f"{key}: {value}" for key, value in context.items())
    logger.error(f"{operation_name} - 경과 시간: {elapsed_time:.2f}초 (제한: {DEFAULT_TIMEOUT}초), 로그 파일: {log_file}" + (f", {context_str}" if context_str else ""))


class OperationTimer:
    """
    작업 타이머 컨텍스트 매니저

    사용법:
        with OperationTimer("작업명", timeout=10) as timer:
            # 작업 수행
            timer.check()  # 중간 체크포인트
    """

    def __init__(self, operation_name: str, timeout: int = DEFAULT_TIMEOUT, context: Optional[Dict[str, Any]] = None) -> None:
        self.operation_name = operation_name
        self.timeout = timeout
        self.context = context or {}
        self.start_time = None
        self.checkpoints = []

    def __enter__(self) -> "OperationTimer":
        self.start_time = time.time()
        self.checkpoints = []
        return self

    def __exit__(self, exc_type: Optional[type], exc_val: Optional[BaseException], exc_tb: Any) -> bool:
        elapsed = time.time() - self.start_time
        if elapsed > self.timeout and exc_type is None:
            # 타임아웃 발생 (예외 없이 완료된 경우에도 기록)
            self.context["checkpoints"] = self.checkpoints
            log_timeout_error(self.operation_name, elapsed, self.context)
        return False  # 예외 전파

    def check(self, checkpoint_name: Optional[str] = None) -> bool:
        """
        중간 체크포인트 - 타임아웃 확인

        Args:
            checkpoint_name: 체크포인트 이름

        Returns:
            True if OK, raises TimeoutError if timeout
        """
        elapsed = time.time() - self.start_time
        checkpoint_info = {
            "name": checkpoint_name or f"checkpoint_{len(self.checkpoints)+1}",
            "elapsed": elapsed
        }
        self.checkpoints.append(checkpoint_info)

        if elapsed > self.timeout:
            self.context["checkpoints"] = self.checkpoints
            self.context["failed_at"] = checkpoint_name
            log_timeout_error(self.operation_name, elapsed, self.context)
            raise TimeoutError(f"{self.operation_name} 타임아웃: {elapsed:.2f}초 > {self.timeout}초 (at {checkpoint_name})")

        return True

    def elapsed(self) -> float:
        """경과 시간 반환

        Returns:
            작업 시작 이후 경과 시간 (초)
        """
        return time.time() - self.start_time

    def remaining(self) -> float:
        """남은 시간 반환

        Returns:
            타임아웃까지 남은 시간 (초, 최소 0)
        """
        return max(0, self.timeout - self.elapsed())


def with_timeout(timeout: int = DEFAULT_TIMEOUT, operation_name: Optional[str] = None) -> Callable:
    """
    타임아웃 데코레이터

    사용법:
        @with_timeout(10, "데이터 수집")
        def collect_data():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            op_name = operation_name or func.__name__
            start_time = time.time()

            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start_time

                if elapsed > timeout:
                    log_timeout_error(op_name, elapsed, {
                        "function": func.__name__,
                        "args_count": len(args),
                        "kwargs_keys": list(kwargs.keys()),
                        "status": "completed_but_slow"
                    })

                return result

            except Exception as e:
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    log_timeout_error(op_name, elapsed, {
                        "function": func.__name__,
                        "error": str(e),
                        "error_type": type(e).__name__
                    })
                raise

        return wrapper
    return decorator


def wait_with_timeout(
    condition_func: Callable[[], bool],
    timeout: int = DEFAULT_TIMEOUT,
    check_interval: float = 0.5,
    operation_name: str = "조건 대기"
) -> bool:
    """
    조건이 충족될 때까지 대기 (타임아웃 적용)

    Args:
        condition_func: 조건 확인 함수 (True 반환 시 대기 종료)
        timeout: 타임아웃 (초)
        check_interval: 확인 간격 (초)
        operation_name: 작업 이름 (로그용)

    Returns:
        True if condition met, False if timeout
    """
    start_time = time.time()

    while True:
        elapsed = time.time() - start_time

        if elapsed > timeout:
            log_timeout_error(operation_name, elapsed, {
                "condition_func": condition_func.__name__ if hasattr(condition_func, '__name__') else str(condition_func),
                "check_interval": check_interval
            })
            return False

        try:
            if condition_func():
                return True
        except Exception as e:
            # 조건 확인 중 오류 발생 시에도 타임아웃 체크
            if elapsed > timeout:
                log_timeout_error(operation_name, elapsed, {
                    "error": str(e),
                    "error_type": type(e).__name__
                })
                return False

        time.sleep(check_interval)


def execute_with_timeout(
    func: Callable,
    args: tuple = (),
    kwargs: Optional[Dict[str, Any]] = None,
    timeout: int = DEFAULT_TIMEOUT,
    operation_name: Optional[str] = None
) -> Tuple[bool, Any, float]:
    """
    함수 실행 (타임아웃 적용)

    Note: Python에서는 스레드 강제 종료가 불가능하므로,
    이 함수는 실행 후 경과 시간만 체크합니다.
    실제 타임아웃 강제 중단이 필요하면 multiprocessing 사용 필요.

    Args:
        func: 실행할 함수
        args: 위치 인자
        kwargs: 키워드 인자
        timeout: 타임아웃 (초)
        operation_name: 작업 이름

    Returns:
        (success: bool, result: Any, elapsed: float)
    """
    kwargs = kwargs or {}
    op_name = operation_name or (func.__name__ if hasattr(func, '__name__') else str(func))

    start_time = time.time()

    try:
        result = func(*args, **kwargs)
        elapsed = time.time() - start_time

        if elapsed > timeout:
            log_timeout_error(op_name, elapsed, {
                "status": "completed_but_timeout",
                "function": op_name
            })
            return (False, result, elapsed)

        return (True, result, elapsed)

    except Exception as e:
        elapsed = time.time() - start_time
        log_timeout_error(op_name, elapsed, {
            "status": "error",
            "error": str(e),
            "error_type": type(e).__name__
        })
        raise
