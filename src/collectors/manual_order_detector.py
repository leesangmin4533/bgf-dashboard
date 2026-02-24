"""
수동 발주 감지기

입고 데이터에서 자동발주와 매칭되지 않는 항목을 수동발주로 식별하여
order_tracking에 등록한다.
"""

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.infrastructure.database.connection import get_connection
from src.infrastructure.database.repos import (
    ReceivingRepository,
    OrderRepository,
    OrderTrackingRepository,
    ProductDetailRepository,
    InventoryBatchRepository,
)
from src.alert.config import ALERT_CATEGORIES
from src.alert.delivery_utils import (
    get_delivery_type,
    get_arrival_time,
    get_expiry_time_for_delivery,
)
from src.utils.logger import get_logger
from src.settings.constants import ORDER_SOURCE_MANUAL

logger = get_logger(__name__)


# 푸드 카테고리 (시간 기반 폐기)
FOOD_CATEGORIES = {'001', '002', '003', '004', '005', '006', '012'}


class ManualOrderDetector:
    """수동 발주 감지기"""

    def __init__(self, store_id: Optional[str] = None) -> None:
        self.store_id = store_id
        self.receiving_repo = ReceivingRepository(store_id=self.store_id)
        self.order_repo = OrderRepository(store_id=self.store_id)
        self.tracking_repo = OrderTrackingRepository(store_id=self.store_id)
        self.product_repo = ProductDetailRepository(store_id=self.store_id)
        self.batch_repo = InventoryBatchRepository(store_id=self.store_id)

    def detect_and_save(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """
        수동 발주 감지 및 저장 (메인 진입점)

        Args:
            target_date: 입고일 (기본: 오늘, YYYY-MM-DD)

        Returns:
            {
                'detected': int,      # 감지된 수동발주 수
                'saved': int,         # 저장된 수
                'skipped': int,       # 스킵된 수 (이미 존재 등)
                'items': List[Dict]   # 감지된 항목 상세
            }
        """
        if target_date is None:
            target_date = datetime.now().strftime("%Y-%m-%d")

        logger.info(f"수동 발주 감지 시작: {target_date}")

        # 1. 수동 발주 감지
        manual_orders = self._detect_manual_orders(target_date)

        if not manual_orders:
            logger.info("수동 발주 없음")
            return {'detected': 0, 'saved': 0, 'skipped': 0, 'items': []}

        logger.info(f"수동 발주 감지: {len(manual_orders)}건")

        # 2. order_tracking 저장
        saved = 0
        skipped = 0

        for item in manual_orders:
            try:
                result = self._save_manual_order(item, target_date)
                if result:
                    saved += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.warning(f"저장 실패 ({item['item_cd']}): {e}")
                skipped += 1

        logger.info(f"수동 발주 저장 완료: {saved}건 저장, {skipped}건 스킵")

        return {
            'detected': len(manual_orders),
            'saved': saved,
            'skipped': skipped,
            'items': manual_orders
        }

    def _detect_manual_orders(self, receiving_date: str) -> List[Dict[str, Any]]:
        """
        수동 발주 감지

        배송 스케줄:
        - 발주 마감: 당일 10:00
        - 1차 입고: 당일 20:00 (발주 당일)
        - 2차 입고: 익일 07:00 (발주 다음날)

        Args:
            receiving_date: 입고일 (YYYY-MM-DD)

        Returns:
            수동 발주 목록
        """
        # 1. 오늘 입고된 상품 조회
        received_items = self.receiving_repo.get_receiving_by_date(receiving_date)

        if not received_items:
            logger.debug(f"입고 데이터 없음: {receiving_date}")
            return []

        # 2. 자동발주 기록 조회
        # 1차 입고(당일 20:00)의 발주일 = 입고일 당일
        # 2차 입고(익일 07:00)의 발주일 = 입고일 전일
        recv_dt = datetime.strptime(receiving_date, "%Y-%m-%d")
        order_date_same_day = receiving_date  # 1차용 (당일 발주 → 당일 20시 입고)
        order_date_prev_day = (recv_dt - timedelta(days=1)).strftime("%Y-%m-%d")  # 2차용

        # 두 날짜의 자동발주 기록 모두 조회
        auto_orders_same = self.order_repo.get_orders_by_date(order_date_same_day)
        auto_orders_prev = self.order_repo.get_orders_by_date(order_date_prev_day)
        auto_order_items = {o['item_cd'] for o in auto_orders_same}
        auto_order_items.update({o['item_cd'] for o in auto_orders_prev})

        # 3. order_tracking에 이미 등록된 항목 조회 (중복 방지)
        existing_tracking = self.tracking_repo.get_existing_tracking_items(receiving_date)

        # 4. 매칭되지 않는 항목 = 수동 발주
        manual_orders = []
        for recv in received_items:
            item_cd = recv['item_cd']

            # 이미 자동발주로 등록됨
            if item_cd in auto_order_items:
                continue

            # 이미 tracking에 등록됨
            if item_cd in existing_tracking:
                continue

            manual_orders.append({
                'item_cd': item_cd,
                'item_nm': recv.get('item_nm', ''),
                'mid_cd': recv.get('mid_cd', ''),
                'order_qty': recv.get('receiving_qty', 0),
                'receiving_date': receiving_date,
                'delivery_type': recv.get('delivery_type') or self._infer_delivery_type(recv),
                'chit_no': recv.get('chit_no', ''),
            })

        return manual_orders

    def _infer_delivery_type(self, recv: Dict[str, Any]) -> str:
        """배송 차수 추론 (상품명 또는 센터명 기반)"""
        item_nm = recv.get('item_nm', '')
        center_nm = recv.get('center_nm', '')

        # 상품명 끝자리로 추론
        delivery = get_delivery_type(item_nm)
        if delivery:
            return delivery

        # 센터명으로 추론
        if '저온' in center_nm:
            return '1차'  # 저온 = 1차 기본
        elif '상온' in center_nm:
            return '상온'

        return '1차'  # 기본값

    def _save_manual_order(self, item: Dict[str, Any], receiving_date: str) -> bool:
        """
        수동 발주 order_tracking 저장

        Args:
            item: 수동 발주 항목
            receiving_date: 입고일

        Returns:
            저장 성공 여부
        """
        item_cd = item['item_cd']
        mid_cd = item['mid_cd']

        # 도착/폐기 시간 계산
        arrival_time, expiry_time = self._calculate_times(item, mid_cd, receiving_date)

        if arrival_time is None or expiry_time is None:
            logger.debug(f"시간 계산 실패, 스킵: {item_cd}")
            return False

        # order_tracking 저장
        order_date = (
            datetime.strptime(receiving_date, "%Y-%m-%d") - timedelta(days=1)
        ).strftime("%Y-%m-%d")

        try:
            self.tracking_repo.save_order_manual(
                order_date=order_date,
                item_cd=item_cd,
                item_nm=item['item_nm'],
                mid_cd=mid_cd,
                delivery_type=item['delivery_type'],
                order_qty=item['order_qty'],
                arrival_time=arrival_time.strftime("%Y-%m-%d %H:%M"),
                expiry_time=expiry_time.strftime("%Y-%m-%d %H:%M"),
                order_source=ORDER_SOURCE_MANUAL
            )

            logger.info(
                f"수동발주 등록: {item['item_nm'][:15]} "
                f"도착:{arrival_time.strftime('%m/%d %H:%M')} "
                f"폐기:{expiry_time.strftime('%m/%d %H:%M')}"
            )

            # 비푸드: inventory_batches에도 등록
            if mid_cd not in FOOD_CATEGORIES:
                self._save_inventory_batch(item, receiving_date, expiry_time)

            return True

        except ValueError as e:
            # 중복 등록 시도
            logger.debug(f"중복 등록 스킵 ({item_cd}): {e}")
            return False
        except Exception as e:
            logger.warning(f"order_tracking 저장 실패 ({item_cd}): {e}")
            return False

    def _calculate_times(
        self,
        item: Dict[str, Any],
        mid_cd: str,
        receiving_date: str
    ) -> Tuple[Optional[datetime], Optional[datetime]]:
        """
        도착시간/폐기시간 계산

        배송 스케줄:
        - 1차 입고: 당일 20:00 (발주 당일)
        - 2차 입고: 익일 07:00 (발주 다음날)

        Args:
            item: 발주 항목
            mid_cd: 중분류 코드
            receiving_date: 입고일

        Returns:
            (arrival_time, expiry_time) 튜플
        """
        recv_dt = datetime.strptime(receiving_date, "%Y-%m-%d")
        delivery_type = item.get('delivery_type', '1차')

        # 푸드류 (시간 기반)
        if mid_cd in FOOD_CATEGORIES:
            # 도착시간 계산
            if delivery_type == '1차':
                # 1차: 당일 20:00 입고
                arrival_time = recv_dt.replace(hour=20, minute=0)
            else:
                # 2차: 익일 07:00 입고 (receiving_date 기준으로는 당일 07:00)
                arrival_time = recv_dt.replace(hour=7, minute=0)

            # 폐기시간: 카테고리별 유통시간 적용
            expiry_time = get_expiry_time_for_delivery(delivery_type, mid_cd, arrival_time)
            return arrival_time, expiry_time

        # 비-푸드류 (일 기반)
        else:
            # 도착시간: 입고일 기준
            if delivery_type == '1차':
                arrival_time = recv_dt.replace(hour=20, minute=0)
            else:
                arrival_time = recv_dt.replace(hour=7, minute=0)

            # 유통기한 조회
            product_info = self.product_repo.get(item['item_cd'])
            exp_days = product_info.get('expiration_days') if product_info else None
            if not exp_days or exp_days <= 0:
                # 유통기한 정보 없으면 스킵
                logger.debug(f"유통기한 미등록: {item['item_cd']}")
                return None, None

            expiry_time = arrival_time + timedelta(days=exp_days)
            return arrival_time, expiry_time

    def _save_inventory_batch(
        self,
        item: Dict[str, Any],
        receiving_date: str,
        expiry_time: datetime
    ) -> None:
        """비푸드 inventory_batches 저장"""
        try:
            exp_days = (expiry_time - datetime.strptime(receiving_date, "%Y-%m-%d")).days
            self.batch_repo.add_batch(
                item_cd=item['item_cd'],
                item_nm=item['item_nm'],
                mid_cd=item['mid_cd'],
                receiving_date=receiving_date,
                expiration_days=exp_days,
                initial_qty=item['order_qty']
            )
            logger.debug(f"배치 등록: {item['item_cd']}")
        except Exception as e:
            logger.warning(f"배치 등록 실패 ({item['item_cd']}): {e}")


def run_manual_order_detection(target_date: Optional[str] = None, store_id: Optional[str] = None) -> Dict[str, Any]:
    """
    수동 발주 감지 실행 (스케줄러용)

    Args:
        target_date: 입고일 (기본: 오늘)
        store_id: 매장 ID (기본: None)

    Returns:
        실행 결과
    """
    detector = ManualOrderDetector(store_id=store_id)
    return detector.detect_and_save(target_date)


# 테스트용
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    print("=== 수동 발주 감지 테스트 ===")
    result = run_manual_order_detection()
    print(f"결과: {result}")
