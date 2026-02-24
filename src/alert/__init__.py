"""
폐기 위험 알림 모듈
- 유통기한 임박 상품 감지
- 카카오톡 알림 발송 (기존 notification.kakao_notifier 사용)
"""

from .expiry_checker import ExpiryChecker, run_expiry_check_and_alert

__all__ = ['ExpiryChecker', 'run_expiry_check_and_alert']
