"""
OrderAdjuster -- 미입고/재고 반영 발주량 조정 담당

AutoOrderSystem에서 추출된 단일 책임 클래스.
실시간 재고/미입고를 반영하여 need_qty 재계산,
발주 목록 직접 업데이트를 담당한다.

god-class-decomposition PDCA Step 7
"""

from typing import Any, Dict, List, Optional

from src.utils.logger import get_logger
from src.infrastructure.database.repos.order_exclusion_repo import ExclusionType
from src.settings.constants import (
    FOOD_CATEGORIES,
    FOOD_SHORT_EXPIRY_PENDING_DISCOUNT,
)

logger = get_logger(__name__)


class OrderAdjuster:
    """미입고/재고 반영 발주량 조정 담당

    AutoOrderSystem._recalculate_need_qty(),
    _apply_pending_and_stock_to_order_list() 로직을 담당.
    """

    def recalculate_need_qty(
        self,
        predicted_sales: float,
        safety_stock: float,
        new_stock: int,
        new_pending: int,
        daily_avg: float,
        order_unit_qty: int = 1,
        promo_type: str = "",
        expiration_days: Optional[int] = None,
        mid_cd: str = ""
    ) -> int:
        """실시간 재고/미입고를 반영하여 need_qty를 재계산"""
        # 단기유통 푸드류 미입고 할인
        effective_pending = new_pending
        if (expiration_days is not None and expiration_days <= 1
                and mid_cd in FOOD_CATEGORIES
                and new_pending > 0):
            effective_pending = max(0, int(new_pending * FOOD_SHORT_EXPIRY_PENDING_DISCOUNT))
            if effective_pending != new_pending:
                logger.debug(
                    f"[단기유통할인] mid_cd={mid_cd}, 유통기한={expiration_days}일: "
                    f"미입고 {new_pending} → {effective_pending} "
                    f"(할인율={FOOD_SHORT_EXPIRY_PENDING_DISCOUNT})"
                )

        # 유통기한 1일 이하 푸드류: 어제 재고는 오늘 폐기 대상이므로 재고 차감 무시
        if (expiration_days is not None and expiration_days <= 1
                and mid_cd in FOOD_CATEGORIES):
            effective_stock = 0
            if new_stock > 0:
                logger.debug(
                    f"[단기유통재고무시] mid_cd={mid_cd}, 유통기한={expiration_days}일: "
                    f"재고 {new_stock}개 무시 (폐기 예정)"
                )
        else:
            effective_stock = new_stock

        # 음수 클램프: 예측값/안전재고가 음수이면 0으로 보정
        if predicted_sales < 0:
            logger.warning(f"[음수보정] predicted_sales={predicted_sales} → 0")
            predicted_sales = 0
        if safety_stock < 0:
            logger.warning(f"[음수보정] safety_stock={safety_stock} → 0")
            safety_stock = 0

        need = predicted_sales + safety_stock - effective_stock - effective_pending
        if need <= 0:
            return 0

        # 올림 규칙
        from src.prediction.prediction_config import PREDICTION_PARAMS
        min_threshold = PREDICTION_PARAMS.get("min_order_threshold", 0.5)
        round_up_threshold = PREDICTION_PARAMS.get("round_up_threshold", 0.3)

        if need < min_threshold:
            return 0
        elif need < 1.0:
            order_qty = 1
        else:
            if need - int(need) >= round_up_threshold:
                order_qty = int(need) + 1
            else:
                order_qty = int(need)

        # 발주 배수 단위 적용
        if order_unit_qty > 1 and order_qty > 0:
            order_qty = max(order_unit_qty, ((order_qty + order_unit_qty - 1) // order_unit_qty) * order_unit_qty)

        # 행사 배수 보정
        if promo_type and order_qty > 0:
            from src.settings.constants import PROMO_MIN_STOCK_UNITS
            promo_unit = PROMO_MIN_STOCK_UNITS.get(promo_type, 1)
            if order_qty < promo_unit:
                order_qty = promo_unit

        return order_qty

    def apply_pending_and_stock(
        self,
        order_list: List[Dict[str, Any]],
        pending_data: Dict[str, int],
        stock_data: Dict[str, int],
        min_order_qty: int = 1,
        cut_items: set = None,
        unavailable_items: set = None,
        exclusion_records: list = None,
        last_eval_results: dict = None,
        expired_stock_overrides: Optional[Dict[str, int]] = None,
    ) -> tuple:
        """기존 발주 목록에 미입고/실시간재고 데이터를 직접 반영

        Args:
            expired_stock_overrides: {item_cd: expiring_qty} — 오늘 만료 배치 잔여수량.
                해당 SKU는 stock_changed 여부와 무관하게 재계산 대상이 된다.
                재고에서 만료분을 차감하되 실제 DB는 수정하지 않는다.

        Returns:
            (adjusted_list, stock_discrepancies)
        """
        from src.prediction.pre_order_evaluator import EvalDecision

        if cut_items is None:
            cut_items = set()
        if unavailable_items is None:
            unavailable_items = set()
        if exclusion_records is None:
            exclusion_records = []
        if last_eval_results is None:
            last_eval_results = {}

        adjusted_list = []
        cut_excluded = 0
        unavailable_excluded = 0
        recalculated_count = 0
        stock_discrepancies = []

        logger.info(f"[미입고조정 시작] 원본 발주 목록: {len(order_list)}개 상품")

        for item in order_list:
            item_cd = item.get('item_cd')
            if not item_cd:
                continue

            # 전 점포 영구 제외 상품 스킵
            from src.settings.constants import GLOBAL_EXCLUDE_ITEMS
            if item_cd in GLOBAL_EXCLUDE_ITEMS:
                continue

            if item_cd in cut_items:
                cut_excluded += 1
                continue

            if item_cd in unavailable_items:
                unavailable_excluded += 1
                continue

            adjusted_item = item.copy()

            original_qty = item.get('final_order_qty') or 0
            original_stock = item.get('current_stock') or 0
            original_pending = item.get('pending_receiving_qty') or 0

            new_pending = pending_data.get(item_cd, original_pending) or 0
            new_stock = (stock_data.get(item_cd, original_stock) if stock_data else original_stock) or 0

            item_name = item.get('item_nm', item_cd)
            stock_changed = (new_stock != original_stock) or (new_pending != original_pending)

            # 유통기한 만료 재고 override: 만료분만큼 재고 차감 (DB 수정 없음)
            _expiry_override_applied = False
            if expired_stock_overrides and item_cd in expired_stock_overrides:
                expiring_qty = expired_stock_overrides[item_cd]
                if expiring_qty > 0 and new_stock > 0:
                    before_stock = new_stock
                    new_stock = max(0, new_stock - expiring_qty)
                    _expiry_override_applied = True
                    if new_stock != before_stock:
                        stock_changed = True  # 만료 재고 차감으로 재계산 강제
                        logger.info(
                            f"[Q5] 유통기한 만료 재고 차감: "
                            f"product={item_cd} {item_name[:20]} "
                            f"expiring_qty={expiring_qty} "
                            f"stock_before={before_stock} stock_after={new_stock}"
                        )

            if stock_changed:
                predicted_sales = item.get('predicted_sales') or 0
                safety_stock = item.get('safety_stock') or 0
                daily_avg = item.get('daily_avg') or 0
                order_unit_qty = item.get('order_unit_qty') or 1
                promo_type = item.get('promo_type') or ''
                exp_days = item.get('expiration_days')
                mid_cd = item.get('mid_cd', '')

                new_qty = self.recalculate_need_qty(
                    predicted_sales=predicted_sales,
                    safety_stock=safety_stock,
                    new_stock=max(0, new_stock),
                    new_pending=new_pending,
                    daily_avg=daily_avg,
                    order_unit_qty=order_unit_qty,
                    promo_type=promo_type,
                    expiration_days=exp_days,
                    mid_cd=mid_cd
                )
                recalculated_count += 1

                stock_discrepancies.append({
                    "item_cd": item_cd,
                    "item_nm": item_name,
                    "mid_cd": item.get("mid_cd", ""),
                    "stock_at_prediction": original_stock,
                    "pending_at_prediction": original_pending,
                    "stock_at_order": new_stock,
                    "pending_at_order": new_pending,
                    "stock_source": item.get("stock_source", ""),
                    "is_stock_stale": item.get("is_stock_stale", False),
                    "original_order_qty": original_qty,
                    "recalculated_order_qty": new_qty,
                })

                logger.info(
                    f"[미입고조정] {item_name[:20]}: "
                    f"원발주={original_qty}, 원재고={original_stock}, 원미입고={original_pending} → "
                    f"신재고={new_stock}, 신미입고={new_pending} → "
                    f"재계산(pred={predicted_sales}+safe={safety_stock:.1f}-stk={max(0,new_stock)}-pnd={new_pending})={new_qty}"
                )
            else:
                new_qty = original_qty

            # 최소 발주량 미만이면 제외
            if new_qty < min_order_qty:
                # 단기유통 푸드 보호
                item_exp_days = adjusted_item.get('expiration_days')
                item_mid_cd = adjusted_item.get('mid_cd', '')
                if (item_exp_days is not None and item_exp_days <= 1
                        and item_mid_cd in FOOD_CATEGORIES
                        and original_qty > 0
                        and stock_changed
                        and new_pending < original_pending):
                    new_qty = original_qty
                    logger.info(
                        f"[단기유통보호] {item_name[:20]}: "
                        f"미입고 {original_pending}->{new_pending} 감소로 발주 소멸 → "
                        f"유통기한 {item_exp_days}일 → 원발주 {original_qty}개 유지"
                    )
                else:
                    eval_result = last_eval_results.get(item_cd)
                    if eval_result and eval_result.decision in (
                        EvalDecision.FORCE_ORDER, EvalDecision.URGENT_ORDER
                    ):
                        if new_stock + new_pending > 0:
                            logger.info(
                                f"[보호생략] {item_name[:20]}: {eval_result.decision.name}이지만 "
                                f"가용재고={new_stock}+미입고={new_pending}={new_stock + new_pending} → 제외"
                            )
                            exclusion_records.append({
                                "item_cd": item_cd,
                                "item_nm": item_name,
                                "mid_cd": item.get("mid_cd"),
                                "exclusion_type": ExclusionType.STOCK_SUFFICIENT,
                                "predicted_qty": item.get("predicted_sales", 0),
                                "current_stock": new_stock,
                                "pending_qty": new_pending,
                                "detail": f"need={new_qty}, stock+pending={new_stock+new_pending} 충분",
                            })
                            continue
                        new_qty = 1
                        logger.info(
                            f"[보호] {item_name[:20]}: {eval_result.decision.name} → 최소 1개 유지"
                        )
                    else:
                        exclusion_records.append({
                            "item_cd": item_cd,
                            "item_nm": item_name,
                            "mid_cd": item.get("mid_cd"),
                            "exclusion_type": ExclusionType.STOCK_SUFFICIENT,
                            "predicted_qty": item.get("predicted_sales", 0),
                            "current_stock": new_stock,
                            "pending_qty": new_pending,
                            "detail": f"need={new_qty}, 재고/미입고 충분",
                        })
                        continue

            adjusted_item['final_order_qty'] = new_qty
            adjusted_item['recommended_qty'] = new_qty
            adjusted_item['current_stock'] = new_stock
            adjusted_item['pending_receiving_qty'] = new_pending
            adjusted_item['expected_stock'] = new_stock + new_pending

            adjusted_list.append(adjusted_item)

        if cut_excluded > 0:
            logger.info(f"발주중지(CUT) {cut_excluded}개 상품 제외 (재고/미입고 조정 단계)")
        if unavailable_excluded > 0:
            logger.info(f"미취급/조회실패 {unavailable_excluded}개 상품 제외 (재고/미입고 조정 단계)")
        if recalculated_count > 0:
            logger.info(f"[v11] need 재계산: {recalculated_count}개 상품 (실시간 재고 반영)")

        adjusted_list.sort(key=lambda x: (x.get("mid_cd", ""), -x.get("final_order_qty", 0)))

        if not adjusted_list and order_list:
            logger.warning(
                f"[전량소멸] 원본 {len(order_list)}개 상품이 조정 후 전부 제외됨! "
                f"(CUT={cut_excluded}, 미취급={unavailable_excluded}, "
                f"조정소멸={len(order_list) - cut_excluded - unavailable_excluded})"
            )

        return adjusted_list, stock_discrepancies
