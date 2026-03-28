"""
ActionType — integrity 이상에 대한 액션 제안 코드 정의

자동화 2단계: 이상 감지 → 원인 분석 → 액션 제안 → 카카오 발송
"""


class ActionType:
    """action_proposals.action_type 컬럼에 저장되는 코드"""

    # unavailable_with_sales 대응
    RESTORE_IS_AVAILABLE = "RESTORE_IS_AVAILABLE"

    # food_ghost_stock 대응
    CLEAR_GHOST_STOCK = "CLEAR_GHOST_STOCK"

    # expiry_time_mismatch 대응
    FIX_EXPIRY_TIME = "FIX_EXPIRY_TIME"

    # expired_batch_remaining 대응
    CLEAR_EXPIRED_BATCH = "CLEAR_EXPIRED_BATCH"

    # missing_delivery_type 대응
    CHECK_DELIVERY_TYPE = "CHECK_DELIVERY_TYPE"

    # past_expiry_active 대응
    DEACTIVATE_EXPIRED = "DEACTIVATE_EXPIRED"

    # 복합 이상 / 분류 불가
    MANUAL_CHECK_REQUIRED = "MANUAL_CHECK_REQUIRED"
