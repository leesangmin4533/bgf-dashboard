"""NotificationDispatcher — 1차 Kakao + 2차 fallback 통합 알림.

Kakao 토큰 만료 등으로 1차 실패 시 logs/alerts.log + winsound.Beep + stderr 로 fallback.
이 feature(job-health-monitor)의 본래 가치가 "조용한 장애 감지"이므로
감지 결과가 단일 채널에 의존하지 않도록 설계됨.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

# 전용 알림 로그 (logs/job_health_alerts.log — 기존 alerts.log와 분리)
_ALERTS_LOG_PATH = Path(__file__).resolve().parents[3] / "logs" / "job_health_alerts.log"
_alerts_logger: Optional[logging.Logger] = None


def _get_alerts_logger() -> logging.Logger:
    global _alerts_logger
    if _alerts_logger is not None:
        return _alerts_logger
    lg = logging.getLogger("bgf_auto.alerts")
    lg.setLevel(logging.WARNING)
    lg.propagate = False
    _ALERTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        _ALERTS_LOG_PATH,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    )
    lg.addHandler(handler)
    _alerts_logger = lg
    return lg


class NotificationDispatcher:
    """1차 Kakao + 2차 fallback.

    fallback 3경로 동시 작동:
        1. logs/alerts.log (파일)
        2. winsound.Beep (Windows)
        3. stderr ANSI red
    """

    def __init__(self, kakao_notifier=None):
        self._kakao = kakao_notifier  # None이면 lazy import

    def send(self, subject: str, body: str, level: str = "WARNING") -> bool:
        """1차 시도 → 실패 시 2차. 1차 성공 여부 반환."""
        kakao_ok = self._try_kakao(subject, body)
        if not kakao_ok:
            self._fallback(subject, body, level)
        return kakao_ok

    def _try_kakao(self, subject: str, body: str) -> bool:
        try:
            notifier = self._kakao
            if notifier is None:
                from src.notification.kakao_notifier import (
                    KakaoNotifier,
                    DEFAULT_REST_API_KEY,
                )
                notifier = KakaoNotifier(rest_api_key=DEFAULT_REST_API_KEY)
            message = f"[{subject}]\n{body}"
            # KakaoNotifier.send_message는 None/False/True 혼용 가능 → 예외만 아니면 시도 성공
            result = notifier.send_message(message)
            if result is False:
                return False
            return True
        except Exception as e:
            logger.warning(f"[Dispatcher] Kakao 1차 실패: {e}")
            return False

    def _fallback(self, subject: str, body: str, level: str) -> None:
        """2차 채널: alerts.log + winsound + stderr."""
        # 1. 전용 로그 파일
        try:
            al = _get_alerts_logger()
            al.warning(f"[{subject}] {body}")
        except Exception as e:
            logger.warning(f"[Dispatcher] alerts.log 실패: {e}")

        # 2. Windows 콘솔 beep
        try:
            import winsound
            winsound.Beep(1000, 500)
        except Exception:
            pass  # 플랫폼/콘솔 없음은 침묵

        # 3. stderr ANSI red
        try:
            msg = f"\033[91m[ALERT/{level}/{subject}] {body}\033[0m"
            print(msg, file=sys.stderr, flush=True)
        except Exception:
            pass
