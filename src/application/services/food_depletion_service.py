"""
FoodDepletionService — 소진율 곡선 계산 서비스

매일 Phase 1.06에서 호출되어 전일 발주 건의 시간대별 소진율을 계산하고
food_popularity_curve 테이블에 EMA 증분 갱신합니다.

소진율 = 입고 후 경과시간별 누적 판매량 / 발주수량 (cap 1.0)
기준시각: 1차배송=07:00, 2차배송=15:00
추적범위: 1차=24h, 2차=16h
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.utils.logger import get_logger
from src.settings.constants import DEPLETION_CURVE_EMA_ALPHA

logger = get_logger(__name__)

# 푸드 카테고리 코드
FOOD_MID_CDS = ('001', '002', '003', '004', '005', '012')

# 배송차수별 기준시각 (시)
DELIVERY_BASE_HOUR = {"1차": 7, "2차": 15}

# 배송차수별 추적 범위 (시간)
DELIVERY_TRACK_HOURS = {"1차": 24, "2차": 16}


class FoodDepletionService:
    """소진율 곡선 계산 및 갱신 서비스"""

    def __init__(self, store_id: str):
        self.store_id = store_id

    def update_curves_daily(self) -> Dict[str, int]:
        """전일 발주 건 기준으로 소진율 곡선 EMA 갱신

        Returns:
            {'updated': N, 'skipped': N, 'errors': N}
        """
        from src.infrastructure.database.repos.food_popularity_curve_repo import (
            FoodPopularityCurveRepository,
        )
        from src.infrastructure.database.repos.hourly_sales_detail_repo import (
            HourlySalesDetailRepository,
        )
        from src.infrastructure.database.connection import DBRouter

        curve_repo = FoodPopularityCurveRepository(store_id=self.store_id)
        curve_repo.ensure_table()
        hsd_repo = HourlySalesDetailRepository(store_id=self.store_id)

        # 전일(D-1) 날짜
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

        # 전일 푸드 발주 목록 조회
        orders = self._get_food_orders(yesterday)
        if not orders:
            logger.debug(f"[DepletionCurve] {yesterday}: 푸드 발주 없음")
            return {"updated": 0, "skipped": 0, "errors": 0}

        updated = 0
        skipped = 0
        errors = 0

        for order in orders:
            try:
                item_cd = order["item_cd"]
                order_qty = order["order_qty"]
                delivery_type = order["delivery_type"]

                if not delivery_type or delivery_type not in ("1차", "2차"):
                    skipped += 1
                    logger.debug(
                        f"[DepletionCurve] {item_cd} 스킵: "
                        f"delivery_type='{delivery_type}' (유효하지 않음)"
                    )
                    continue

                if order_qty <= 0:
                    skipped += 1
                    logger.debug(
                        f"[DepletionCurve] {item_cd} 스킵: order_qty={order_qty}"
                    )
                    continue

                # 시간대별 판매 데이터에서 소진율 계산
                hourly_rates = self._calculate_depletion_rates(
                    hsd_repo, item_cd, yesterday, delivery_type, order_qty
                )

                if not hourly_rates:
                    skipped += 1
                    logger.debug(
                        f"[DepletionCurve] {item_cd} 스킵: "
                        f"hourly 판매 데이터 없음 ({yesterday}, {delivery_type})"
                    )
                    continue

                # EMA 갱신
                curve_repo.upsert_curves_bulk(
                    item_cd, delivery_type, hourly_rates,
                    alpha=DEPLETION_CURVE_EMA_ALPHA,
                )
                updated += 1

                # 최종 소진율 로그 (역추적용)
                max_h = max(hourly_rates.keys())
                final_rate = hourly_rates[max_h]
                logger.debug(
                    f"[DepletionCurve] 갱신 {item_cd} "
                    f"{delivery_type} qty={order_qty}: "
                    f"final_rate={final_rate:.3f} "
                    f"(경과{max_h}h, α={DEPLETION_CURVE_EMA_ALPHA})"
                )

            except Exception as e:
                logger.debug(f"[DepletionCurve] {order.get('item_cd', '?')} 오류: {e}")
                errors += 1

        result = {"updated": updated, "skipped": skipped, "errors": errors}
        logger.info(
            f"[DepletionCurve] {yesterday}: "
            f"갱신={updated}, 스킵={skipped}, 에러={errors}"
        )
        return result

    def _get_food_orders(self, order_date: str) -> List[Dict[str, Any]]:
        """특정일 푸드 카테고리 발주 목록 조회"""
        from src.infrastructure.database.connection import DBRouter

        conn = DBRouter.get_store_connection(self.store_id)
        try:
            placeholders = ','.join(['?' for _ in FOOD_MID_CDS])
            rows = conn.execute(f"""
                SELECT item_cd, item_nm, order_qty, delivery_type, mid_cd
                FROM order_tracking
                WHERE order_date = ?
                  AND mid_cd IN ({placeholders})
                  AND order_qty > 0
            """, (order_date, *FOOD_MID_CDS)).fetchall()

            result = []
            for r in rows:
                item_nm = r[1] or ''
                delivery_type = r[3]
                # delivery_type이 NULL이면 상품명 끝자리로 판별
                if not delivery_type:
                    last_char = item_nm.strip()[-1] if item_nm.strip() else ''
                    delivery_type = "2차" if last_char == "2" else "1차"

                result.append({
                    "item_cd": r[0],
                    "item_nm": item_nm,
                    "order_qty": r[2],
                    "delivery_type": delivery_type,
                    "mid_cd": r[4],
                })
            return result
        finally:
            conn.close()

    def _calculate_depletion_rates(
        self,
        hsd_repo: Any,
        item_cd: str,
        order_date: str,
        delivery_type: str,
        order_qty: int,
    ) -> Dict[int, float]:
        """상품의 입고 후 경과시간별 누적 소진율 계산

        Args:
            hsd_repo: HourlySalesDetailRepository
            item_cd: 상품코드
            order_date: 발주일 (YYYY-MM-DD)
            delivery_type: '1차' 또는 '2차'
            order_qty: 발주수량 (분모)

        Returns:
            {경과시간: 소진율} (예: {1: 0.0, 2: 0.1, ..., 24: 0.72})
            빈 딕셔너리 = hourly 데이터 없음
        """
        base_hour = DELIVERY_BASE_HOUR.get(delivery_type, 7)
        track_hours = DELIVERY_TRACK_HOURS.get(delivery_type, 24)

        # hourly_sales_detail에서 해당 상품의 판매 데이터 조회
        # 추적 범위: order_date 기준시각 ~ 다음날 06:59
        conn = hsd_repo._get_conn()
        try:
            # order_date의 base_hour 이후 + 다음날 06시까지
            next_date = (
                datetime.strptime(order_date, '%Y-%m-%d') + timedelta(days=1)
            ).strftime('%Y-%m-%d')

            rows = conn.execute("""
                SELECT sales_date, hour, sale_qty
                FROM hourly_sales_detail
                WHERE item_cd = ?
                  AND (
                      (sales_date = ? AND hour >= ?)
                      OR
                      (sales_date = ? AND hour < 7)
                  )
                  AND sale_qty > 0
                ORDER BY sales_date, hour
            """, (item_cd, order_date, base_hour, next_date)).fetchall()
        finally:
            conn.close()

        if not rows:
            return {}

        # 경과시간별 판매량 매핑
        hourly_sales = {}  # {경과시간: 판매량}
        for sales_date, hour, qty in rows:
            elapsed = self._calc_elapsed(sales_date, hour, order_date, base_hour)
            if 1 <= elapsed <= track_hours:
                hourly_sales[elapsed] = hourly_sales.get(elapsed, 0) + qty

        if not hourly_sales:
            return {}

        # 누적 소진율 계산
        cumulative = 0.0
        result = {}
        for h in range(1, track_hours + 1):
            cumulative += hourly_sales.get(h, 0)
            # 소진율 = 누적판매 / 발주수량, cap 1.0
            rate = min(cumulative / order_qty, 1.0)
            result[h] = round(rate, 4)

        return result

    def _calc_elapsed(
        self,
        sales_date: str,
        hour: int,
        order_date: str,
        base_hour: int,
    ) -> int:
        """판매 시간을 입고 기준시각 대비 경과시간으로 변환

        예: base_hour=7, sales_date=order_date, hour=9 → 경과 3시간
            base_hour=7, sales_date=next_day, hour=3 → 경과 21시간
        """
        if sales_date == order_date:
            return hour - base_hour + 1
        else:
            # 다음날
            return (24 - base_hour) + hour + 1

    def bootstrap_from_history(self, lookback_days: int = 60) -> Dict[str, int]:
        """기존 hourly_sales_detail 데이터로 소진율 곡선 일괄 초기화

        Phase 1.06 배포 직후 1회성으로 실행하여
        기존 데이터로 sample_count를 즉시 축적합니다.

        Args:
            lookback_days: 소급 기간 (기본 60일)

        Returns:
            {'total_orders': N, 'updated': N, 'skipped': N}
        """
        from src.infrastructure.database.repos.food_popularity_curve_repo import (
            FoodPopularityCurveRepository,
        )
        from src.infrastructure.database.repos.hourly_sales_detail_repo import (
            HourlySalesDetailRepository,
        )

        curve_repo = FoodPopularityCurveRepository(store_id=self.store_id)
        curve_repo.ensure_table()
        hsd_repo = HourlySalesDetailRepository(store_id=self.store_id)

        total_orders = 0
        updated = 0
        skipped = 0

        # lookback 기간 내 모든 푸드 발주 조회
        start_date = (
            datetime.now() - timedelta(days=lookback_days)
        ).strftime('%Y-%m-%d')

        from src.infrastructure.database.connection import DBRouter
        conn = DBRouter.get_store_connection(self.store_id)
        try:
            placeholders = ','.join(['?' for _ in FOOD_MID_CDS])
            orders = conn.execute(f"""
                SELECT DISTINCT order_date, item_cd, item_nm, order_qty,
                       delivery_type, mid_cd
                FROM order_tracking
                WHERE order_date >= ?
                  AND mid_cd IN ({placeholders})
                  AND order_qty > 0
                ORDER BY order_date
            """, (start_date, *FOOD_MID_CDS)).fetchall()
        finally:
            conn.close()

        logger.info(
            f"[DepletionCurve] bootstrap: {len(orders)}건 발주, "
            f"{start_date}~{datetime.now().strftime('%Y-%m-%d')}"
        )

        for r in orders:
            total_orders += 1
            order_date, item_cd, item_nm, order_qty = r[0], r[1], r[2] or '', r[3]
            delivery_type = r[4]

            if not delivery_type:
                last_char = item_nm.strip()[-1] if item_nm.strip() else ''
                delivery_type = "2차" if last_char == "2" else "1차"

            hourly_rates = self._calculate_depletion_rates(
                hsd_repo, item_cd, order_date, delivery_type, order_qty
            )
            if hourly_rates:
                # bootstrap은 alpha=0.2로 약간 빠르게 수렴
                curve_repo.upsert_curves_bulk(
                    item_cd, delivery_type, hourly_rates, alpha=0.2
                )
                updated += 1
            else:
                skipped += 1

            if total_orders % 100 == 0:
                logger.info(
                    f"[DepletionCurve] bootstrap 진행: "
                    f"{total_orders}/{len(orders)}"
                )

        result = {
            "total_orders": total_orders,
            "updated": updated,
            "skipped": skipped,
        }
        logger.info(f"[DepletionCurve] bootstrap 완료: {result}")
        return result
