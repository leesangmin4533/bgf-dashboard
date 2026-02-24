"""
store_id 필터 중앙 헬퍼

매장별 DB 분리 후에는 store_filter가 불필요해지지만,
마이그레이션 과도기 동안 호환성을 위해 유지합니다.

매장별 DB 완전 전환 후 이 모듈은 제거됩니다.
"""

from typing import Optional, Tuple

# store_id 필터가 필요한 테이블 목록 (레거시, 단일 DB용)
STORE_SCOPED_TABLES = frozenset({
    'daily_sales',
    'order_tracking',
    'collection_logs',
    'order_fail_reasons',
    'realtime_inventory',
    'inventory_batches',
    'prediction_logs',
    'order_history',
    'eval_outcomes',
    'promotions',
    'promotion_stats',
    'promotion_changes',
    'receiving_history',
    'auto_order_items',
    'smart_order_items',
})


def store_filter(alias: str = "", store_id: Optional[str] = None) -> Tuple[str, tuple]:
    """store_id 필터 SQL 조각과 파라미터를 반환

    매장별 DB 분리 완료 후에는 항상 빈 필터를 반환하도록 변경 예정.

    Args:
        alias: 테이블 별칭 (예: "ds", "ot")
        store_id: 매장 코드. None이면 빈 필터 반환.

    Returns:
        (sql_fragment, params) 튜플.
    """
    if store_id is None:
        return ("", ())
    prefix = f"{alias}." if alias else ""
    return (f"AND {prefix}store_id = ?", (store_id,))
