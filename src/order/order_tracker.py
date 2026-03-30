"""
OrderTracker -- 발주 추적 저장 담당

AutoOrderSystem에서 추출된 단일 책임 클래스.
발주 성공 상품의 order_tracking 저장,
eval_outcomes 업데이트를 담당한다.

god-class-decomposition PDCA Step 8
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.utils.logger import get_logger
from src.settings.constants import FOOD_CATEGORIES
from src.alert.config import ALERT_CATEGORIES
from src.alert.delivery_utils import (
    get_delivery_type,
    calculate_shelf_life_after_arrival
)

logger = get_logger(__name__)


class OrderTracker:
    """발주 추적 저장 담당

    AutoOrderSystem._save_to_order_tracking(),
    _update_eval_order_results() 로직을 담당.
    """

    def __init__(self, tracking_repo, product_repo, store_id: str):
        self.tracking_repo = tracking_repo
        self.product_repo = product_repo
        self.store_id = store_id

    def save_to_order_tracking(
        self,
        order_list: List[Dict[str, Any]],
        results: List[Dict[str, Any]],
    ) -> int:
        """발주 성공한 상품들을 order_tracking에 저장

        Returns:
            저장된 건수
        """
        from src.infrastructure.database.repos import (
            ProductDetailRepository,
            InventoryBatchRepository,
        )

        order_dict = {item['item_cd']: item for item in order_list}
        saved_count = 0

        for res in results:
            if not res.get('success'):
                continue

            item_cd = res.get('item_cd')
            order_date = res.get('order_date')
            actual_qty = res.get('actual_qty', 0)

            if not item_cd:
                continue

            order_info = order_dict.get(item_cd, {})

            # cancel_smart(qty=0)도 tracking에 기록 (추적 가능하도록)
            if actual_qty <= 0:
                if order_info.get('cancel_smart'):
                    try:
                        self.tracking_repo.save_order(
                            order_date=order_date,
                            item_cd=item_cd,
                            item_nm=order_info.get('item_nm', item_cd),
                            mid_cd=order_info.get('mid_cd', ''),
                            delivery_type='스마트취소',
                            order_qty=0,
                            arrival_time=None,
                            expiry_time=None,
                            store_id=self.store_id,
                            order_source='smart_cancel'
                        )
                        saved_count += 1
                        logger.info(f"Tracking: {order_info.get('item_nm', item_cd)[:15]} → 스마트취소(qty=0)")
                    except Exception as e:
                        logger.warning(f"cancel_smart tracking 저장 실패: {e}")
                continue

            item_nm = order_info.get('item_nm', item_cd)
            mid_cd = order_info.get('mid_cd', '')

            # 배송 차수 판별
            try:
                order_datetime = datetime.strptime(order_date, "%Y-%m-%d")
            except ValueError:
                order_datetime = datetime.strptime(order_date, "%Y%m%d")

            if mid_cd in ALERT_CATEGORIES:
                delivery_type = get_delivery_type(item_nm, item_cd=item_cd) or "1차"
                exp_days = None
                cat_cfg = ALERT_CATEGORIES.get(mid_cd, {})
                # 유통기한이 카테고리 기본값과 다를 수 있으므로 항상 조회
                pd_info = self.product_repo.get(item_cd)
                exp_days = pd_info.get('expiration_days') if pd_info else None
                shelf_hours, arrival_time, expiry_time = calculate_shelf_life_after_arrival(
                    item_nm, mid_cd, order_datetime, expiration_days=exp_days
                )
            else:
                delivery_type = "일반"
                # [원본 보존] 기존: arrival_time = order_datetime
                # 수정: 입고일 = 발주일 + 1일 (D+1), IB expiry_date와 기준 통일
                arrival_time = order_datetime + timedelta(days=1)
                try:
                    pd_repo = ProductDetailRepository()
                    pd_info = pd_repo.get(item_cd)
                    exp_days = pd_info.get('expiration_days') if pd_info else None
                    if exp_days and exp_days > 0:
                        # [원본 보존] 기존: expiry_time = arrival_time(order_date) + exp_days
                        # 수정: expiry_time = receiving_date(D+1) + exp_days
                        # → IB의 expiry_date = receiving_date + expiration_days와 동일 기준
                        expiry_time = arrival_time + timedelta(days=exp_days)
                    else:
                        logger.debug(f"비푸드 유통기한 미등록, tracking 스킵: {item_cd}")
                        continue
                except Exception:
                    logger.debug(f"비푸드 유통기한 조회 실패, tracking 스킵: {item_cd}")
                    continue

            try:
                self.tracking_repo.save_order(
                    order_date=order_date,
                    item_cd=item_cd,
                    item_nm=item_nm,
                    mid_cd=mid_cd,
                    delivery_type=delivery_type,
                    order_qty=actual_qty,
                    arrival_time=arrival_time.strftime("%Y-%m-%d %H:%M"),
                    expiry_time=expiry_time.strftime("%Y-%m-%d %H:%M"),
                    store_id=self.store_id,
                    order_source=order_info.get('source', 'auto')
                )
                saved_count += 1
                if arrival_time and expiry_time:
                    logger.info(f"Tracking: {item_nm[:15]} → 도착:{arrival_time.strftime('%m/%d %H:%M')} 폐기:{expiry_time.strftime('%m/%d %H:%M')}")
                else:
                    logger.info(f"Tracking: {item_nm[:15]} → 발주:{actual_qty}개")

                # 비푸드류 inventory_batches 배치 생성
                if mid_cd not in ALERT_CATEGORIES and exp_days and exp_days > 0:
                    try:
                        batch_repo = InventoryBatchRepository(store_id=self.store_id)
                        batch_repo.create_batch(
                            item_cd=item_cd,
                            item_nm=item_nm,
                            mid_cd=mid_cd,
                            receiving_date=arrival_time.strftime("%Y-%m-%d"),
                            expiration_days=exp_days,
                            initial_qty=actual_qty,
                            store_id=self.store_id
                        )
                        logger.debug(f"inventory_batches 생성: {item_nm} (발주일: {order_date}, {actual_qty}개)")
                    except Exception as e:
                        logger.warning(f"inventory_batches 생성 실패 ({item_cd}): {e}")

            except Exception as e:
                logger.warning(f"Tracking 저장 실패 ({item_cd}): {e}")

        if saved_count > 0:
            logger.info(f"발주 추적 등록: {saved_count}건")

        return saved_count

    @staticmethod
    def update_eval_order_results(
        order_list: List[Dict[str, Any]],
        results: List[Dict[str, Any]],
        eval_calibrator,
    ) -> int:
        """eval_outcomes에 predicted_qty, actual_order_qty, order_status 업데이트

        Returns:
            업데이트된 건수
        """
        today = datetime.now().strftime("%Y-%m-%d")
        order_dict = {item['item_cd']: item for item in order_list}
        updated = 0

        try:
            for res in results:
                item_cd = res.get('item_cd')
                if not item_cd:
                    continue

                order_info = order_dict.get(item_cd, {})
                predicted_qty = order_info.get('final_order_qty')
                actual_qty = res.get('actual_qty', 0) if res.get('success') else 0
                order_status = 'success' if res.get('success') else 'fail'

                if res.get('dry_run'):
                    order_status = 'pending'
                    actual_qty = predicted_qty

                eval_calibrator.outcome_repo.update_order_result(
                    eval_date=today,
                    item_cd=item_cd,
                    predicted_qty=predicted_qty,
                    actual_order_qty=actual_qty,
                    order_status=order_status
                )
                updated += 1

            if updated > 0:
                logger.info(f"eval_outcomes 발주 결과 업데이트: {updated}건")
        except Exception as e:
            logger.warning(f"eval_outcomes 발주 결과 업데이트 실패: {e}")

        return updated
