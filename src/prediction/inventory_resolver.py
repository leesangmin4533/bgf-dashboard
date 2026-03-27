"""
InventoryResolver — 재고/미입고 조회 + TTL 검증

ImprovedPredictor에서 추출된 단일 책임 클래스.
재고 캐시, 실시간 재고(realtime_inventory), daily_sales 폴백,
미입고 교차검증 로직을 담당한다.

god-class-decomposition PDCA Step 3
"""

from src.utils.logger import get_logger

logger = get_logger(__name__)


class InventoryResolver:
    """재고/미입고 조회 + TTL 검증

    ImprovedPredictor로부터 추출된 클래스.
    재고 조회 우선순위:
      1. _stock_cache (외부에서 직접 설정한 캐시)
      2. realtime_inventory (BGF 사이트 실시간 조회) — 유통기한 기반 TTL 이내만
      3. daily_sales 최근 stock_qty (폴백)
    """

    def __init__(self, data_provider, store_id):
        """
        Args:
            data_provider: PredictionDataProvider 인스턴스 (캐시/repo 접근)
            store_id: 점포 코드
        """
        self._data = data_provider
        self.store_id = store_id

    def resolve(self, item_cd, pending_qty, get_current_stock_fn, ot_pending_cache):
        """현재 재고 및 미입고 수량 조회 (캐시/DB 우선순위)

        Args:
            item_cd: 상품코드
            pending_qty: 미입고 수량 (None이면 캐시/DB에서 조회)
            get_current_stock_fn: daily_sales 기반 재고 조회 콜백
            ot_pending_cache: order_tracking 기반 교차검증 캐시 (None이면 미사용)

        Returns:
            (current_stock, pending_qty, stock_source, pending_source, is_stale)
        """
        inv_data = None
        stock_source = ""
        pending_source = ""
        is_stale = False

        if self._data._use_db_inventory and self._data._inventory_repo:
            if (item_cd not in self._data._stock_cache
                    or (pending_qty is None and item_cd not in self._data._pending_cache)):
                inv_data = self._data._inventory_repo.get(item_cd)

        # --- 재고 조회 ---
        if item_cd in self._data._stock_cache:
            current_stock = self._data._stock_cache[item_cd]
            stock_source = "cache"
        elif inv_data and inv_data.get('stock_qty') is not None:
            if inv_data.get('_stale', False):
                is_stale = True
                ds_stock = get_current_stock_fn(item_cd)
                ri_stock = inv_data['stock_qty']

                if ri_stock == 0 and ds_stock > 0:
                    logger.debug(
                        f"[{item_cd}] stale RI=0, ds={ds_stock} "
                        f"(queried={inv_data.get('queried_at', '?')}) "
                        f"-> daily_sales 값 사용 (RI 무효)"
                    )
                    current_stock = ds_stock
                    stock_source = "ri_stale_ds_nonzero"
                elif ds_stock < ri_stock:
                    logger.debug(
                        f"[{item_cd}] 오래된 재고 데이터 감지 "
                        f"(ri={ri_stock}, ds={ds_stock}, queried={inv_data.get('queried_at', '?')}) "
                        f"-> daily_sales 값 사용"
                    )
                    current_stock = ds_stock
                    stock_source = "ri_stale_ds"
                else:
                    current_stock = ri_stock
                    stock_source = "ri_stale_ri"
            else:
                ri_stock = inv_data['stock_qty']
                if ri_stock == 0:
                    # RI=0이지만 fresh → daily_sales 교차검증
                    ds_stock = get_current_stock_fn(item_cd)
                    if ds_stock > 0:
                        logger.warning(
                            f"[{item_cd}] fresh RI=0 but DS={ds_stock} "
                            f"(queried={inv_data.get('queried_at', '?')}) "
                            f"-> daily_sales 값 사용 (RI 제로재고 의심)"
                        )
                        current_stock = ds_stock
                        stock_source = "ri_zero_ds_fallback"
                    else:
                        current_stock = 0
                        stock_source = "ri"
                else:
                    current_stock = ri_stock
                    stock_source = "ri"
        else:
            current_stock = get_current_stock_fn(item_cd)
            stock_source = "ds"

        # 음수 재고 방어
        if current_stock < 0:
            logger.warning(f"[{item_cd}] 음수 재고 감지: {current_stock}개 -> 0으로 초기화")
            current_stock = 0

        # --- 미입고 조회 ---
        if pending_qty is None:
            if item_cd in self._data._pending_cache:
                pending_qty = self._data._pending_cache[item_cd]
                pending_source = "cache"
            elif inv_data:
                if inv_data.get('_stale', False):
                    ri_pending = inv_data.get('pending_qty', 0)
                    if ri_pending > 0:
                        logger.debug(
                            f"[{item_cd}] 오래된 미입고 데이터 무시 "
                            f"(pending={ri_pending}, queried={inv_data.get('queried_at', '?')})"
                        )
                    pending_qty = 0
                    pending_source = "ri_stale_zero"
                else:
                    pending_qty = inv_data.get('pending_qty', 0)
                    pending_source = "ri_fresh"
            else:
                pending_qty = 0
                pending_source = "none"
        else:
            pending_source = "cache" if item_cd in self._data._pending_cache else "param"

        # --- order_tracking 보완 (올리는 방향만) ---
        # RI pending=0인데 OT에 pending이 있으면 → OT 값으로 보완 (중복발주 방지)
        # RI > OT인 경우: RI 신뢰 (수동 발주는 order_tracking에 미기록)
        if (
            ot_pending_cache is not None
            and pending_source == "ri_fresh"
        ):
            ot_pending = ot_pending_cache.get(item_cd, 0)
            if ot_pending > pending_qty:
                logger.warning(
                    f"[미입고보완] {item_cd}: "
                    f"RI pending={pending_qty} < OT pending={ot_pending} "
                    f"-> OT값({ot_pending}) 사용 (BGF 미반영 발주 보완)"
                )
                pending_qty = ot_pending
                pending_source = "ot_fill"

        # 음수 미입고 방어
        if pending_qty < 0:
            logger.warning(f"[{item_cd}] 음수 미입고 감지: {pending_qty}개 -> 0으로 초기화")
            pending_qty = 0

        return current_stock, pending_qty, stock_source, pending_source, is_stale
