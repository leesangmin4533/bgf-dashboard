"""
store_id 필터 중앙 헬퍼

store_id가 필요한 테이블(daily_sales, order_tracking, collection_logs,
order_fail_reasons, realtime_inventory, inventory_batches) 쿼리 시
일관된 필터 생성을 보장합니다.

Usage:
    from src.db.store_query import store_filter
    sf, sp = store_filter("ds", store_id)
    query = f"SELECT ... FROM daily_sales ds WHERE ds.sales_date = ? {sf}"
    cursor.execute(query, (date,) + sp)
"""

from typing import Optional, Tuple

# store_id 필터가 필요한 테이블 목록
STORE_SCOPED_TABLES = frozenset({
    'daily_sales',
    'order_tracking',
    'collection_logs',
    'order_fail_reasons',
    'realtime_inventory',
    'inventory_batches',
    # v26 추가
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

    Args:
        alias: 테이블 별칭 (예: "ds", "ot"). 빈 문자열이면 별칭 없이 생성.
        store_id: 매장 코드. None이면 빈 필터 반환.

    Returns:
        (sql_fragment, params) 튜플.
        - store_id가 있으면: ("AND ds.store_id = ?", ("46513",))
        - store_id가 None이면: ("", ())
    """
    if store_id is None:
        return ("", ())
    prefix = f"{alias}." if alias else ""
    return (f"AND {prefix}store_id = ?", (store_id,))
