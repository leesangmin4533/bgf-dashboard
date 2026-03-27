"""에러 알림 핸들러.

logging.Handler를 상속하여 ERROR 레벨 로그 발생 시
자동으로 알림을 발송합니다.

기능:
1. 중복 억제 (같은 메시지 5분 내 재발송 방지)
2. 알림 파일 저장 (logs/alerts.log)
3. Kakao 알림 (설정 시, 선택적)
4. 시간당 최대 알림 수 제한

사용법:
    # logger.py에서 자동 연결됨
    # 또는 수동 설정:
    from src.utils.alerting import AlertingHandler
    handler = AlertingHandler()
    logger.addHandler(handler)
"""

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.utils.logger import LOG_DIR

logger = logging.getLogger("bgf_auto.alerting")

# 알림 로그 파일
ALERT_LOG_PATH = LOG_DIR / "alerts.log"


class AlertingHandler(logging.Handler):
    """ERROR 로그 발생 시 알림을 발송하는 핸들러.

    특징:
    - COOLDOWN_SECONDS: 같은 메시지 해시에 대해 5분간 중복 억제
    - MAX_ALERTS_PER_HOUR: 시간당 최대 알림 수 (폭주 방지)
    - alert_log: logs/alerts.log에 기록 (항상)
    - Kakao 알림: kakao_enabled=True일 때만
    """

    COOLDOWN_SECONDS = 300  # 같은 메시지 5분간 중복 억제
    MAX_ALERTS_PER_HOUR = 20  # 시간당 최대 알림 수

    def __init__(
        self,
        alert_log_path: Optional[Path] = None,
        kakao_enabled: bool = False,
    ):
        super().__init__(level=logging.ERROR)
        self._alert_log = alert_log_path or ALERT_LOG_PATH
        self._kakao_enabled = kakao_enabled
        self._recent_alerts: dict = {}  # message_hash -> last_sent_time
        self._hourly_count = 0
        self._hourly_reset = time.time()

    def emit(self, record: logging.LogRecord):
        """ERROR 로그 발생 시 호출."""
        try:
            msg = self.format(record)
            msg_key = hash(record.getMessage()[:100])

            now = time.time()

            # 중복 억제: 같은 메시지 COOLDOWN_SECONDS 내 재발송 방지
            if msg_key in self._recent_alerts:
                if now - self._recent_alerts[msg_key] < self.COOLDOWN_SECONDS:
                    return

            # 시간당 제한: MAX_ALERTS_PER_HOUR 초과 시 무시
            if now - self._hourly_reset > 3600:
                self._hourly_count = 0
                self._hourly_reset = now
            if self._hourly_count >= self.MAX_ALERTS_PER_HOUR:
                return

            # 1) 알림 로그 파일에 기록 (항상)
            self._write_alert_log(msg)

            # 2) Kakao 알림 (설정 시)
            if self._kakao_enabled:
                self._send_kakao_alert(record)

            # 카운터 갱신
            self._recent_alerts[msg_key] = now
            self._hourly_count += 1

        except Exception:
            self.handleError(record)

    def _write_alert_log(self, formatted_msg: str):
        """알림 로그 파일에 기록."""
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self._alert_log, "a", encoding="utf-8") as f:
                f.write(f"[ALERT] {timestamp} | {formatted_msg}\n")
        except Exception:
            pass  # 알림 로그 실패는 무시 (메인 로직에 영향 없음)

    def _send_kakao_alert(self, record: logging.LogRecord):
        """Kakao Talk 알림 발송 (최선 노력, 실패 시 무시)."""
        try:
            from src.notification.kakao_notifier import KakaoNotifier, DEFAULT_REST_API_KEY

            notifier = KakaoNotifier(DEFAULT_REST_API_KEY)
            message = (
                f"[BGF 에러 알림]\n"
                f"시각: {datetime.now().strftime('%H:%M:%S')}\n"
                f"모듈: {record.name}\n"
                f"내용: {record.getMessage()[:200]}"
            )
            notifier.send_text(message)
        except Exception:
            pass  # Kakao 실패는 비즈니스 로직에 영향 없음


def is_kakao_configured() -> bool:
    """Kakao 알림 설정 여부 확인."""
    config_path = Path(__file__).parent.parent.parent / "config" / "kakao_token.json"
    return config_path.exists()


def create_alerting_handler() -> AlertingHandler:
    """AlertingHandler 팩토리. logger.py에서 호출."""
    return AlertingHandler(kakao_enabled=is_kakao_configured())
