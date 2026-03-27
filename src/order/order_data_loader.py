"""
OrderDataLoader -- 발주 데이터 로딩 담당

AutoOrderSystem에서 추출된 단일 책임 클래스.
미취급/CUT/자동발주/재고 캐시 로딩, 미입고 사전 조회를 담당한다.

god-class-decomposition PDCA Step 5
"""

import time
from typing import Any, Dict, List, Optional, Set

from src.utils.logger import get_logger
from src.infrastructure.database.repos.order_exclusion_repo import ExclusionType

logger = get_logger(__name__)


class OrderDataLoader:
    """발주 데이터 로딩 담당

    AutoOrderSystem.load_*() 및 prefetch_pending_quantities() 로직을 담당.
    각 메서드는 결과를 반환하여 호출자(Facade)가 적절히 저장한다.
    """

    def __init__(self, store_id: str):
        self.store_id = store_id

    def load_unavailable(self, inventory_repo) -> set:
        """DB에서 미취급 상품 목록 로드

        Returns:
            미취급 상품코드 set
        """
        unavailable = inventory_repo.get_unavailable_items(store_id=self.store_id)
        if unavailable:
            logger.info(f"DB에서 미취급 상품 {len(unavailable)}개 로드됨")
        return set(unavailable) if unavailable else set()

    def load_cut_items(self, inventory_repo) -> set:
        """DB에서 발주중지(CUT) 상품 목록 로드

        Returns:
            CUT 상품코드 set
        """
        cut_items = inventory_repo.get_cut_items(store_id=self.store_id)
        if cut_items:
            logger.info(f"DB에서 발주중지 상품 {len(cut_items)}개 로드됨")
        return set(cut_items) if cut_items else set()

    def load_auto_order_items(
        self,
        driver,
        auto_order_repo,
        smart_order_repo,
        skip_site_fetch: bool = False,
    ) -> tuple:
        """자동발주 + 스마트발주 상품 목록 조회

        Returns:
            (auto_order_items: set, smart_order_items: set)
        """
        auto_items: Set[str] = set()
        smart_items: Set[str] = set()
        auto_site_ok = False
        smart_site_ok = False

        # --- 사이트 조회 시도 ---
        if skip_site_fetch:
            logger.info("사이트 조회 건너뜀 (Phase 1.2에서 DB 캐시 갱신 완료)")
        elif driver:
            try:
                from src.collectors.order_status_collector import OrderStatusCollector

                collector = OrderStatusCollector(driver, store_id=self.store_id)

                if not collector.navigate_to_order_status_menu():
                    logger.warning("발주 현황 조회 메뉴 이동 실패")
                else:
                    # (1) 자동발주 수집
                    auto_detail = collector.collect_auto_order_items_detail()
                    if auto_detail is not None:
                        auto_items = {
                            item["item_cd"] for item in auto_detail
                        }
                        saved = auto_order_repo.refresh(auto_detail, store_id=self.store_id)
                        logger.info(
                            f"자동발주 상품 {len(auto_items)}개 "
                            f"사이트 조회 완료 (DB 캐시 {saved}건 갱신)"
                        )
                        auto_site_ok = True
                    else:
                        logger.warning("자동발주 상품 사이트 조회 실패")

                    # (2) 스마트발주 수집
                    smart_detail = collector.collect_smart_order_items_detail()
                    if smart_detail is not None:
                        smart_items = {
                            item["item_cd"] for item in smart_detail
                        }
                        saved = smart_order_repo.refresh(smart_detail, store_id=self.store_id)
                        logger.info(
                            f"스마트발주 상품 {len(smart_items)}개 "
                            f"사이트 조회 완료 (DB 캐시 {saved}건 갱신)"
                        )
                        smart_site_ok = True
                    else:
                        logger.warning("스마트발주 상품 사이트 조회 실패")

                    # 발주현황조회 탭 닫기
                    if not collector.close_menu():
                        logger.warning("collector.close_menu() 실패 - 직접 탭 닫기 시도")
                        from src.utils.nexacro_helpers import close_tab_by_frame_id
                        from src.settings.ui_config import FRAME_IDS
                        close_tab_by_frame_id(driver, FRAME_IDS["ORDER_STATUS"])
                        time.sleep(0.5)

            except Exception as e:
                logger.warning(f"발주 제외 상품 사이트 조회 실패: {e}")
                try:
                    from src.utils.nexacro_helpers import close_tab_by_frame_id
                    from src.settings.ui_config import FRAME_IDS
                    close_tab_by_frame_id(driver, FRAME_IDS["ORDER_STATUS"])
                    time.sleep(0.5)
                except Exception as e:
                    logger.warning(f"발주 현황 탭 닫기 실패: {e}")

        # --- DB 캐시 fallback (자동) ---
        if not auto_site_ok:
            cached = auto_order_repo.get_all_item_codes(store_id=self.store_id)
            if cached:
                auto_items = set(cached)
                logger.info(
                    f"자동발주 상품 DB 캐시 {len(cached)}개 사용 "
                    f"(마지막 갱신: {auto_order_repo.get_last_updated(store_id=self.store_id)})"
                )
            else:
                logger.info("자동발주 상품: 사이트 조회 실패 + DB 캐시 없음 - 제외 없이 진행")

        # --- DB 캐시 fallback (스마트) ---
        if not smart_site_ok:
            cached = smart_order_repo.get_all_item_codes(store_id=self.store_id)
            if cached:
                smart_items = set(cached)
                logger.info(
                    f"스마트발주 상품 DB 캐시 {len(cached)}개 사용 "
                    f"(마지막 갱신: {smart_order_repo.get_last_updated(store_id=self.store_id)})"
                )
            else:
                logger.info("스마트발주 상품: 사이트 조회 실패 + DB 캐시 없음 - 제외 없이 진행")

        return auto_items, smart_items

    def load_inventory_cache(
        self,
        inventory_repo,
        predictor,
        improved_predictor,
        use_improved: bool,
    ) -> None:
        """DB에서 재고/미입고 데이터를 예측기 캐시에 로드"""
        all_data = inventory_repo.get_all(available_only=True, store_id=self.store_id)
        if not all_data:
            logger.info("DB에 인벤토리 데이터 없음")
            return

        stock_cache = {}
        pending_cache = {}

        for item in all_data:
            item_cd = item.get('item_cd')
            if item_cd:
                stock_cache[item_cd] = item.get('stock_qty', 0)
                pending_cache[item_cd] = item.get('pending_qty', 0)

        if use_improved and improved_predictor:
            improved_predictor.set_stock_cache(stock_cache)
            improved_predictor.set_pending_cache(pending_cache)
        else:
            predictor.set_pending_qty_cache(pending_cache)

        logger.info(f"DB에서 인벤토리 캐시 로드: {len(stock_cache)}개 상품")

    def prefetch_pending(
        self,
        pending_collector,
        item_codes: List[str],
        max_items: int = 500,
    ) -> tuple:
        """미입고 수량 및 실시간 재고 사전 조회

        Returns:
            (pending_data, stock_data, new_cut_items, new_unavailable, new_exclusions)
        """
        if not pending_collector:
            logger.warning("드라이버 없음 - 미입고 수량 조회 건너뜀")
            return {}, {}, set(), set(), []

        # 제한된 수만 조회
        if len(item_codes) > max_items:
            logger.info(f"{len(item_codes)}개 중 {max_items}개만 조회")
            item_codes = item_codes[:max_items]

        logger.info(f"미입고 수량 및 실시간 재고 조회: {len(item_codes)}개 상품...")

        all_results = pending_collector.collect_for_items(item_codes)

        pending_data = {}
        stock_data = {}
        new_cut_items: Set[str] = set()
        new_unavailable: Set[str] = set()
        new_exclusions: List[Dict[str, Any]] = []
        success_count = 0
        unavailable_count = 0
        cut_count = 0

        for item_cd in item_codes:
            result = all_results.get(item_cd)
            if result and result.get('success'):
                pending_data[item_cd] = result.get('pending_qty', 0)
                if 'current_stock' in result and result['current_stock'] is not None:
                    stock_data[item_cd] = result['current_stock']
                if result.get('is_cut_item'):
                    new_cut_items.add(item_cd)
                    cut_count += 1
                    new_exclusions.append({
                        "item_cd": item_cd,
                        "exclusion_type": ExclusionType.CUT,
                        "detail": "BGF 시스템 발주중지(CUT) 판정",
                    })
                success_count += 1
            else:
                new_unavailable.add(item_cd)
                unavailable_count += 1
                new_exclusions.append({
                    "item_cd": item_cd,
                    "exclusion_type": ExclusionType.NOT_CARRIED,
                    "detail": "BGF 사이트 점포 미취급",
                })

        logger.info(f"조회 완료: {success_count}/{len(item_codes)}건")
        logger.info(f"  - 미입고 수량: {len(pending_data)}개 상품")
        logger.info(f"  - 실시간 재고: {len(stock_data)}개 상품")
        if unavailable_count > 0:
            logger.info(f"  - 점포 미취급: {unavailable_count}개 상품 (발주 제외됨)")
        if cut_count > 0:
            logger.info(f"  - 발주중지(CUT): {cut_count}개 상품 (발주 제외됨)")

        # 미입고 있는 상품 출력
        pending_items = {k: v for k, v in pending_data.items() if v > 0}
        if pending_items:
            logger.info(f"미입고 상품: {len(pending_items)}개")
            for item_cd, qty in list(pending_items.items())[:10]:
                stock = stock_data.get(item_cd, '?')
                logger.info(f"  - {item_cd}: 미입고 {qty}개, 재고 {stock}개")

        # 메뉴 탭 닫기
        pending_collector.close_menu()

        return pending_data, stock_data, new_cut_items, new_unavailable, new_exclusions

    @staticmethod
    def parse_ds_yn(ds_yn: str):
        """DS_YN 파싱: '1/3(미달성)' -> (placed=1, required=3)"""
        from src.settings.constants import NEW_PRODUCT_DS_MIN_ORDERS
        if not ds_yn:
            return 0, NEW_PRODUCT_DS_MIN_ORDERS
        try:
            parts = ds_yn.split("(")[0].split("/")
            placed = int(parts[0])
            required = int(parts[1]) if len(parts) > 1 else NEW_PRODUCT_DS_MIN_ORDERS
            return placed, required
        except (ValueError, IndexError):
            return 0, NEW_PRODUCT_DS_MIN_ORDERS
