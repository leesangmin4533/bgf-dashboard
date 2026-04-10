"""
OrderFilter -- 발주 목록 필터링 담당

AutoOrderSystem에서 추출된 단일 책임 클래스.
전역제외/미취급/CUT/자동발주/스마트발주/발주정지 상품 제외,
수동발주 차감, stale CUT 경고를 담당한다.

god-class-decomposition PDCA Step 6
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Set

from src.utils.logger import get_logger
from src.infrastructure.database.repos.order_exclusion_repo import ExclusionType

logger = get_logger(__name__)


class OrderFilter:
    """발주 목록 필터링 담당

    AutoOrderSystem._exclude_filtered_items(),
    _deduct_manual_food_orders(), _warn_stale_cut_items() 로직을 담당.
    """

    def __init__(self, store_id: str):
        self.store_id = store_id

    def exclude_filtered_items(
        self,
        order_list: List[Dict[str, Any]],
        unavailable_items: set,
        cut_items: set,
        auto_order_items: set,
        smart_order_items: set,
        exclusion_records: list,
    ) -> List[Dict[str, Any]]:
        """발주 목록에서 전역제외/미취급/CUT/자동발주/스마트발주/발주정지 상품 제외"""
        # 전 점포 영구 제외 상품
        from src.settings.constants import GLOBAL_EXCLUDE_ITEMS
        if GLOBAL_EXCLUDE_ITEMS:
            global_set = set(GLOBAL_EXCLUDE_ITEMS.keys())
            before_count = len(order_list)
            order_list = [item for item in order_list
                          if item.get("item_cd") not in global_set]
            excluded = before_count - len(order_list)
            if excluded > 0:
                logger.info(f"전 점포 영구 제외 {excluded}개 상품 제외")

        # 점포 미취급 상품 제외
        if unavailable_items:
            before_count = len(order_list)
            order_list = [item for item in order_list if item.get("item_cd") not in unavailable_items]
            excluded = before_count - len(order_list)
            if excluded > 0:
                logger.info(f"점포 미취급 {excluded}개 상품 제외")

        # 발주중지(CUT) 상품 제외
        if cut_items:
            before_count = len(order_list)
            order_list = [item for item in order_list if item.get("item_cd") not in cut_items]
            excluded = before_count - len(order_list)
            if excluded > 0:
                logger.info(f"발주중지(CUT) {excluded}개 상품 제외")

        # 자동발주 상품 제외
        from src.infrastructure.database.repos import AppSettingsRepository
        settings_repo = AppSettingsRepository(store_id=self.store_id)
        if settings_repo.get("EXCLUDE_AUTO_ORDER", True) and auto_order_items:
            before_count = len(order_list)
            excluded_items = [item for item in order_list if item.get("item_cd") in auto_order_items]
            order_list = [item for item in order_list if item.get("item_cd") not in auto_order_items]
            excluded = before_count - len(order_list)
            if excluded > 0:
                logger.info(f"자동발주(본부관리) {excluded}개 상품 제외")
                for ei in excluded_items:
                    exclusion_records.append({
                        "item_cd": ei.get("item_cd"),
                        "item_nm": ei.get("item_nm"),
                        "mid_cd": ei.get("mid_cd"),
                        "exclusion_type": ExclusionType.AUTO_ORDER,
                        "predicted_qty": ei.get("order_qty", 0),
                        "detail": "본부 자동발주 대상",
                    })
        elif not settings_repo.get("EXCLUDE_AUTO_ORDER", True):
            logger.info("자동발주 상품 제외 OFF (대시보드 설정) - 제외 안 함")

        # 스마트발주 상품 제외
        if settings_repo.get("EXCLUDE_SMART_ORDER", False) and smart_order_items:
            before_count = len(order_list)
            excluded_items = [item for item in order_list if item.get("item_cd") in smart_order_items]
            order_list = [item for item in order_list if item.get("item_cd") not in smart_order_items]
            excluded = before_count - len(order_list)
            if excluded > 0:
                logger.info(f"스마트발주(본부관리) {excluded}개 상품 제외")
                for ei in excluded_items:
                    exclusion_records.append({
                        "item_cd": ei.get("item_cd"),
                        "item_nm": ei.get("item_nm"),
                        "mid_cd": ei.get("mid_cd"),
                        "exclusion_type": ExclusionType.SMART_ORDER,
                        "predicted_qty": ei.get("order_qty", 0),
                        "detail": "본부 스마트발주 대상",
                    })
        elif not settings_repo.get("EXCLUDE_SMART_ORDER", False):
            logger.info("스마트발주 상품 제외 OFF (기본값) - 제외 안 함")

        # 발주정지 상품 제외
        try:
            from src.infrastructure.database.repos import StoppedItemRepository
            stopped_repo = StoppedItemRepository()
            stopped_items = stopped_repo.get_active_item_codes()
            if stopped_items:
                before_count = len(order_list)
                excluded_items = [item for item in order_list
                                  if item.get("item_cd") in stopped_items]
                order_list = [item for item in order_list
                              if item.get("item_cd") not in stopped_items]
                excluded = before_count - len(order_list)
                if excluded > 0:
                    logger.info(f"발주정지 {excluded}개 상품 제외")
                    for ei in excluded_items:
                        exclusion_records.append({
                            "item_cd": ei.get("item_cd"),
                            "item_nm": ei.get("item_nm"),
                            "mid_cd": ei.get("mid_cd"),
                            "exclusion_type": ExclusionType.STOPPED,
                            "predicted_qty": ei.get("order_qty", 0),
                            "detail": "stopped_items 등록 상품",
                        })
        except Exception as e:
            logger.warning(f"발주정지 상품 조회 실패: {e}")

        # 디저트 판단 정지 상품 제외 (CONFIRMED_STOP + STOP_RECOMMEND)
        try:
            from src.settings.constants import DESSERT_DECISION_ENABLED
            if DESSERT_DECISION_ENABLED:
                from src.infrastructure.database.repos import DessertDecisionRepository
                dessert_repo = DessertDecisionRepository(store_id=self.store_id)
                dessert_stop_items = dessert_repo.get_confirmed_stop_items()
                dessert_recommend_items = dessert_repo.get_stop_recommended_items()
                dessert_all_stop = dessert_stop_items | dessert_recommend_items
                if dessert_all_stop:
                    before_count = len(order_list)
                    excluded_items = [item for item in order_list
                                      if item.get("item_cd") in dessert_all_stop]
                    order_list = [item for item in order_list
                                  if item.get("item_cd") not in dessert_all_stop]
                    excluded = before_count - len(order_list)
                    if excluded > 0:
                        confirmed_cnt = sum(1 for ei in excluded_items if ei.get("item_cd") in dessert_stop_items)
                        recommend_cnt = excluded - confirmed_cnt
                        logger.info(
                            f"디저트 발주정지 {excluded}개 상품 제외 "
                            f"(CONFIRMED={confirmed_cnt}, STOP_RECOMMEND={recommend_cnt})"
                        )
                        for ei in excluded_items:
                            stop_type = "CONFIRMED_STOP" if ei.get("item_cd") in dessert_stop_items else "STOP_RECOMMEND"
                            exclusion_records.append({
                                "item_cd": ei.get("item_cd"),
                                "item_nm": ei.get("item_nm"),
                                "mid_cd": ei.get("mid_cd"),
                                "exclusion_type": ExclusionType.DESSERT_STOP,
                                "predicted_qty": ei.get("order_qty", 0),
                                "detail": f"디저트 판단 정지({stop_type})",
                            })
        except Exception as e:
            logger.warning(f"디저트 발주정지 조회 실패: {e}")

        # 음료 판단 정지 상품 제외 (CONFIRMED_STOP만 — 운영자 확인 필수)
        try:
            from src.settings.constants import BEVERAGE_DECISION_ENABLED
            if BEVERAGE_DECISION_ENABLED:
                from src.infrastructure.database.repos import BeverageDecisionRepository
                beverage_repo = BeverageDecisionRepository(store_id=self.store_id)
                beverage_stop_items = beverage_repo.get_confirmed_stop_items()
                if beverage_stop_items:
                    before_count = len(order_list)
                    excluded_items = [item for item in order_list
                                      if item.get("item_cd") in beverage_stop_items]
                    order_list = [item for item in order_list
                                  if item.get("item_cd") not in beverage_stop_items]
                    excluded = before_count - len(order_list)
                    if excluded > 0:
                        logger.info(f"음료 발주정지(CONFIRMED_STOP) {excluded}개 상품 제외")
                        for ei in excluded_items:
                            exclusion_records.append({
                                "item_cd": ei.get("item_cd"),
                                "item_nm": ei.get("item_nm"),
                                "mid_cd": ei.get("mid_cd"),
                                "exclusion_type": ExclusionType.BEVERAGE_STOP,
                                "predicted_qty": ei.get("order_qty", 0),
                                "detail": "음료 판단 정지 확정(CONFIRMED_STOP)",
                            })
        except Exception as e:
            logger.warning(f"음료 발주정지 조회 실패: {e}")

        return order_list

    def exclude_site_ordered_items(
        self,
        order_list: List[Dict[str, Any]],
        order_date: str,
        exclusion_records: list,
    ) -> List[Dict[str, Any]]:
        """site(사용자) 기발주 상품을 auto 발주에서 제외

        BGF saveOrd는 UPSERT이므로 auto가 제출하면 site 수량을 덮어씀.
        사용자가 의도적으로 입력한 수량이 AI 예측값으로 교체되는 것을 방지.
        """
        try:
            from src.infrastructure.database.repos import OrderTrackingRepository
            tracking_repo = OrderTrackingRepository(store_id=self.store_id)
            site_orders = tracking_repo.get_site_ordered_items(order_date)
        except Exception as e:
            logger.warning(f"site 기발주 조회 실패 (필터 건너뜀): {e}")
            return order_list

        if not site_orders:
            return order_list

        before_count = len(order_list)
        filtered_list = []
        excluded_items = []

        for item in order_list:
            item_cd = item.get("item_cd", "")
            # cancel_smart 항목은 site 필터 면제 (PYUN_QTY=0 전송 보존)
            if item.get("cancel_smart"):
                filtered_list.append(item)
                continue

            if item_cd in site_orders:
                site_qty = site_orders[item_cd]
                excluded_items.append(item)
                exclusion_records.append({
                    "item_cd": item_cd,
                    "item_nm": item.get("item_nm"),
                    "mid_cd": item.get("mid_cd"),
                    "exclusion_type": ExclusionType.SITE_ORDERED,
                    "predicted_qty": item.get("final_order_qty", item.get("order_qty", 0)),
                    "detail": f"site 기발주 존재 (site_qty={site_qty})",
                })
            else:
                filtered_list.append(item)

        excluded = before_count - len(filtered_list)
        if excluded > 0:
            logger.info(
                f"site 기발주 {excluded}개 상품 제외 "
                f"(site 총 {len(site_orders)}건 중 겹침 {excluded}건)"
            )
            # 샘플 로그 (최대 5건)
            for ei in excluded_items[:5]:
                item_cd = ei.get("item_cd", "")
                site_qty = site_orders.get(item_cd, 0)
                auto_qty = ei.get("final_order_qty", ei.get("order_qty", 0))
                logger.info(
                    f"  SKIP: {ei.get('item_nm', item_cd)[:25]} "
                    f"(site_qty={site_qty}, auto_qty={auto_qty})"
                )

        return filtered_list

    def deduct_manual_food_orders(
        self,
        order_list: List[Dict[str, Any]],
        min_order_qty: int = 1,
        exclusion_records: list = None,
    ) -> List[Dict[str, Any]]:
        """푸드 카테고리 수동 발주분 차감"""
        from src.settings.constants import MANUAL_ORDER_FOOD_DEDUCTION

        if not MANUAL_ORDER_FOOD_DEDUCTION:
            return order_list

        try:
            from src.infrastructure.database.repos import ManualOrderItemRepository
            manual_repo = ManualOrderItemRepository(store_id=self.store_id)
            yesterday = date.today() - timedelta(days=1)
            manual_food_orders = manual_repo.get_today_food_orders(
                store_id=self.store_id, target_date=yesterday,
            )
        except Exception as e:
            logger.warning(f"수동 발주 조회 실패 (차감 건너뜀): {e}")
            return order_list

        if not manual_food_orders:
            return order_list

        from src.prediction.categories.food import is_food_category

        logger.info(f"[수동발주 차감] 푸드 수동발주 {len(manual_food_orders)}개 확인")

        if exclusion_records is None:
            exclusion_records = []

        deducted_list = []
        total_deducted = 0
        removed_count = 0

        for item in order_list:
            # cancel_smart 항목은 차감 없이 통과 (PYUN_QTY=0 전송 보존)
            if item.get("cancel_smart"):
                deducted_list.append(item)
                continue

            item_cd = item.get("item_cd", "")
            mid_cd = item.get("mid_cd", "")

            if not is_food_category(mid_cd) or item_cd not in manual_food_orders:
                deducted_list.append(item)
                continue

            manual_qty = manual_food_orders[item_cd]
            original_qty = item.get("final_order_qty", 0)
            adjusted_qty = max(0, original_qty - manual_qty)

            # [D-1 Fix] smart_override 상품 식별 태그 추가
            smart_tag = " [스마트→수동전환]" if item.get("smart_override") else ""

            if adjusted_qty >= min_order_qty:
                item = dict(item)
                item["final_order_qty"] = adjusted_qty
                item["manual_deducted_qty"] = manual_qty
                deducted_list.append(item)
                total_deducted += (original_qty - adjusted_qty)
                logger.info(
                    f"  {item.get('item_nm', item_cd)}{smart_tag}: "
                    f"{original_qty} - {manual_qty}(수동) = {adjusted_qty}"
                )
            else:
                removed_count += 1
                total_deducted += original_qty
                exclusion_records.append({
                    "item_cd": item_cd,
                    "item_nm": item.get("item_nm"),
                    "mid_cd": mid_cd,
                    "exclusion_type": "MANUAL_ORDER",
                    "predicted_qty": original_qty,
                    "detail": f"수동발주 {manual_qty}개 >= 예측 {original_qty}개{smart_tag}",
                })
                logger.info(
                    f"  {item.get('item_nm', item_cd)}{smart_tag}: "
                    f"{original_qty} - {manual_qty}(수동) = {adjusted_qty} -> 제거"
                )

        if total_deducted > 0 or removed_count > 0:
            logger.info(
                f"[수동발주 차감 완료] "
                f"차감 수량: {total_deducted}개, 제거 상품: {removed_count}개, "
                f"잔여 목록: {len(deducted_list)}개"
            )

        return deducted_list

    @staticmethod
    def warn_stale_cut_items(
        order_list: List[Dict[str, Any]],
        inventory_repo,
    ) -> None:
        """발주 목록 내 CUT 상태 미검증(stale) 상품 경고"""
        try:
            from src.settings.constants import CUT_STATUS_STALE_DAYS
            from datetime import datetime, timedelta

            threshold = datetime.now() - timedelta(days=CUT_STATUS_STALE_DAYS)
            stale_items = []

            for item in order_list:
                item_cd = item.get("item_cd")
                if not item_cd:
                    continue
                inv = inventory_repo.get_by_item(item_cd)
                if not inv or not inv.get("queried_at"):
                    stale_items.append((item_cd, "never"))
                    continue
                try:
                    queried_dt = datetime.fromisoformat(inv["queried_at"])
                    if queried_dt < threshold:
                        days_old = (datetime.now() - queried_dt).days
                        stale_items.append((item_cd, f"{days_old}d"))
                except (ValueError, TypeError):
                    stale_items.append((item_cd, "parse_err"))

            if stale_items:
                logger.warning(
                    f"[CUT 미검증 경고] {len(stale_items)}개 상품의 CUT 상태가 "
                    f"{CUT_STATUS_STALE_DAYS}일 이상 미갱신: "
                    f"{stale_items[:10]}"
                )
        except Exception as e:
            logger.debug(f"stale CUT 경고 실패 (무시): {e}")
