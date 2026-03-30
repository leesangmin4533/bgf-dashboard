"""
카테고리 총량 예측 및 개별 발주 보충

신선식품(001~005) 및 빵(012)처럼 품목 로테이션이 심한 카테고리에서
개별 품목 예측 합이 실제 카테고리 총 수요에 크게 미달하는 문제를 해결한다.

카테고리 일별 총매출의 WMA를 구하고, 개별 예측 합산과 비교하여
부족분을 최근 판매 이력이 있는 품목에 분배한다.
"""

import logging
import math
from typing import Any, Dict, List, Optional, Set, Tuple

from src.infrastructure.database.connection import DBRouter
from src.prediction.prediction_config import PREDICTION_PARAMS
from src.settings.store_context import StoreContext

logger = logging.getLogger(__name__)


class CategoryDemandForecaster:
    """카테고리 총량 예측 기반 발주 보충"""

    def __init__(self, store_id: Optional[str] = None):
        self.store_id = store_id or StoreContext.get_store_id()
        self._config = PREDICTION_PARAMS.get("category_floor", {})

    @property
    def enabled(self) -> bool:
        return self._config.get("enabled", False)

    @property
    def target_mid_cds(self) -> Set[str]:
        return set(self._config.get(
            "target_mid_cds", ["001", "002", "003", "004", "005", "012"]
        ))

    def supplement_orders(
        self,
        order_list: List[Dict[str, Any]],
        eval_results: Optional[Dict[str, Any]] = None,
        cut_items: Optional[Set[str]] = None,
        site_order_counts: Optional[Dict[str, int]] = None,
    ) -> List[Dict[str, Any]]:
        """
        카테고리 총량 대비 부족분 보충

        Args:
            order_list: 기존 발주 추천 목록
            eval_results: pre_order_evaluator 결과
            cut_items: 발주중지(CUT) 상품 set
            site_order_counts: {mid_cd: int} site 발주 수량 (이미 채워진 것으로 간주)

        Returns:
            보충된 발주 목록
        """
        if not self.enabled:
            return order_list

        threshold = self._config.get("threshold", 0.7)
        max_add = self._config.get("max_add_per_item", 1)

        # 기존 발주 목록의 mid_cd별 합산
        existing_by_mid = {}
        existing_items = set()
        for item in order_list:
            mid_cd = item.get("mid_cd", "")
            qty = item.get("final_order_qty", 0) or item.get("order_qty", 0)
            if mid_cd in self.target_mid_cds:
                existing_by_mid[mid_cd] = existing_by_mid.get(mid_cd, 0) + qty
                existing_items.add(item.get("item_cd", ""))

        total_supplemented = 0
        self._last_added_by_mid: Dict[str, int] = {}

        for mid_cd in self.target_mid_cds:
            # 1. 카테고리 총량 WMA
            daily_totals = self._get_category_daily_totals(mid_cd)
            if not daily_totals:
                continue

            category_forecast = self._calculate_category_forecast(daily_totals)
            if category_forecast <= 0:
                continue

            # 2. 현재 발주 합산 (site 발주 포함)
            current_sum = existing_by_mid.get(mid_cd, 0)
            site_count = (site_order_counts or {}).get(mid_cd, 0)
            current_sum += site_count
            floor_qty = category_forecast * threshold

            # 3. 부족 여부 판단
            if current_sum >= floor_qty:
                logger.debug(
                    f"[CatFloor] {mid_cd}: 충분 "
                    f"(현재={current_sum}, floor={floor_qty:.1f}, "
                    f"forecast={category_forecast:.1f})"
                )
                continue

            shortage = max(0, int(floor_qty) - current_sum)
            if shortage <= 0:
                continue

            # 4. 보충 후보 선정
            candidates = self._get_supplement_candidates(
                mid_cd, existing_items, eval_results, cut_items
            )
            if not candidates:
                logger.info(
                    f"[CatFloor] {mid_cd}: 부족 {shortage}개이나 "
                    f"보충 후보 없음"
                )
                continue

            # 5. 분배
            supplements = self._distribute_shortage(
                shortage, candidates, max_add
            )

            # 6. order_list에 병합
            existing_item_map = {
                item.get("item_cd"): item for item in order_list
            }
            for sup in supplements:
                item_cd = sup["item_cd"]
                add_qty = sup["add_qty"]
                order_unit = sup.get("order_unit_qty", 1) or 1
                if item_cd in existing_item_map:
                    # 기존 항목 수량 증가 + 배수 재정렬
                    prev_qty = existing_item_map[item_cd].get(
                        "final_order_qty", 0
                    )
                    new_qty = prev_qty + add_qty
                    if order_unit > 1:
                        new_qty = math.ceil(new_qty / order_unit) * order_unit
                    existing_item_map[item_cd]["final_order_qty"] = new_qty
                    actual_add = new_qty - prev_qty
                    if actual_add != add_qty:
                        logger.info(
                            f"[CatFloor] {item_cd} 배수정렬: "
                            f"{prev_qty}+{add_qty}→{new_qty} "
                            f"(unit={order_unit})"
                        )
                else:
                    # 새 항목: 배수 정렬 적용
                    aligned_qty = add_qty
                    if order_unit > 1:
                        aligned_qty = math.ceil(
                            add_qty / order_unit
                        ) * order_unit
                    aligned_qty = max(1, aligned_qty)  # qty=0 방지
                    order_list.append({
                        "item_cd": item_cd,
                        "item_nm": sup.get("item_nm", ""),
                        "mid_cd": mid_cd,
                        "final_order_qty": aligned_qty,
                        "predicted_qty": 0,
                        "order_qty": aligned_qty,
                        "current_stock": 0,
                        "data_days": sup.get("data_days", 0),
                        "order_unit_qty": order_unit,
                        "source": "category_floor",
                        "model_type": "category_floor",
                    })
                    existing_items.add(item_cd)
                    actual_add = aligned_qty

                total_supplemented += actual_add

            added_qty = sum(s['add_qty'] for s in supplements)
            self._last_added_by_mid[mid_cd] = added_qty

            logger.info(
                f"[CatFloor] {mid_cd}: forecast={category_forecast:.1f}, "
                f"현재={current_sum}, floor={floor_qty:.1f}, "
                f"보충={added_qty}개 "
                f"({len(supplements)}품목)"
            )

        if total_supplemented > 0:
            logger.info(
                f"[CatFloor] 총 보충: {total_supplemented}개"
            )

        return order_list

    def get_last_supplement_added(self) -> Dict[str, int]:
        """직전 supplement_orders()에서 mid_cd별 보충 수량 반환 (large floor 연동용)"""
        return getattr(self, '_last_added_by_mid', {})

    def _get_category_daily_totals(
        self, mid_cd: str
    ) -> List[Tuple[str, int]]:
        """카테고리별 일별 총매출 시계열 조회"""
        days = self._config.get("wma_days", 7)
        conn = DBRouter.get_store_connection(self.store_id)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ds.sales_date, SUM(ds.sale_qty) as total_sale
                FROM daily_sales ds
                WHERE ds.mid_cd = ?
                  AND ds.sales_date >= date('now', '-' || ? || ' days')
                  AND ds.sales_date < date('now')
                GROUP BY ds.sales_date
                ORDER BY ds.sales_date DESC
            """, (mid_cd, days))
            return [(row[0], row[1] or 0) for row in cursor.fetchall()]
        finally:
            conn.close()

    def _calculate_category_forecast(
        self, daily_totals: List[Tuple[str, int]]
    ) -> float:
        """카테고리 총량 WMA 계산 (최근일 가중)"""
        if not daily_totals:
            return 0.0

        n = len(daily_totals)
        # 선형 가중: 최신=n, 가장오래된=1
        weights = list(range(n, 0, -1))
        total_weight = sum(weights)

        wma = sum(
            daily_totals[i][1] * weights[i]
            for i in range(n)
        ) / total_weight

        return round(wma, 2)

    def _get_supplement_candidates(
        self,
        mid_cd: str,
        existing_items: Set[str],
        eval_results: Optional[Dict[str, Any]] = None,
        cut_items: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """보충 대상 품목 선정 (최근 판매 빈도순, 재고 0 우선)"""
        min_sell_days = self._config.get("min_candidate_sell_days", 1)
        conn = DBRouter.get_store_connection(self.store_id)
        try:
            cursor = conn.cursor()
            # is_available=0 필터 로깅용 사전 카운트
            try:
                unavail_cnt = cursor.execute("""
                    SELECT COUNT(DISTINCT ds.item_cd)
                    FROM daily_sales ds
                    JOIN realtime_inventory ri ON ds.item_cd = ri.item_cd
                    WHERE ds.mid_cd = ? AND ri.is_available = 0
                      AND ds.sales_date >= date('now', '-7 days')
                      AND ds.sales_date < date('now')
                """, (mid_cd,)).fetchone()[0]
                if unavail_cnt > 0:
                    logger.info(f"[Floor] {mid_cd} is_available=0 후보 {unavail_cnt}개 제외")
            except Exception:
                pass

            # ★ sell_days 정의: "판매 발생일" (stock_qty 무관 — 보충 대상 선정용)
            # demand_classifier와 의도적으로 다름. 변경 시 주의
            cursor.execute("""
                SELECT ds.item_cd,
                       COUNT(DISTINCT ds.sales_date) as appear_days,
                       SUM(CASE WHEN ds.sale_qty > 0 THEN 1 ELSE 0 END) as sell_days,
                       SUM(ds.sale_qty) as total_sale
                FROM daily_sales ds
                LEFT JOIN realtime_inventory ri
                    ON ds.item_cd = ri.item_cd
                WHERE ds.mid_cd = ?
                  AND ds.sales_date >= date('now', '-7 days')
                  AND ds.sales_date < date('now')
                  AND COALESCE(ri.is_available, 1) = 1
                  AND COALESCE(ri.is_cut_item, 0) = 0
                GROUP BY ds.item_cd
                HAVING sell_days >= ?
                ORDER BY sell_days DESC, total_sale DESC
            """, (mid_cd, min_sell_days))

            skip_codes = set()
            if eval_results:
                from src.prediction.pre_order_evaluator import EvalDecision
                skip_codes = {
                    cd for cd, r in eval_results.items()
                    if r.decision == EvalDecision.SKIP
                }
            cut_codes = cut_items or set()

            candidates = []
            for row in cursor.fetchall():
                item_cd = row[0]
                if item_cd in skip_codes or item_cd in cut_codes:
                    continue
                if item_cd in existing_items:
                    continue  # 이미 발주 목록에 있는 품목은 제외
                candidates.append({
                    "item_cd": item_cd,
                    "appear_days": row[1],
                    "sell_days": row[2],
                    "total_sale": row[3],
                })

            # 상품명 + 발주배수 조회 (common.db)
            if candidates:
                item_cds = [c["item_cd"] for c in candidates]
                common_conn = DBRouter.get_common_connection()
                try:
                    placeholders = ",".join("?" * len(item_cds))
                    common_cursor = common_conn.cursor()
                    rows = common_cursor.execute(
                        f"SELECT item_cd, item_nm FROM products "
                        f"WHERE item_cd IN ({placeholders})",
                        item_cds
                    ).fetchall()
                    name_map = {r[0]: r[1] for r in rows}
                    unit_rows = common_cursor.execute(
                        f"SELECT item_cd, COALESCE(order_unit_qty, 1) "
                        f"FROM product_details "
                        f"WHERE item_cd IN ({placeholders})",
                        item_cds
                    ).fetchall()
                    unit_map = {r[0]: r[1] for r in unit_rows}
                    for cand in candidates:
                        cand["item_nm"] = name_map.get(
                            cand["item_cd"], ""
                        )
                        cand["order_unit_qty"] = unit_map.get(
                            cand["item_cd"], 1
                        ) or 1
                finally:
                    common_conn.close()

            return candidates
        finally:
            conn.close()

    def _distribute_shortage(
        self,
        shortage: int,
        candidates: List[Dict[str, Any]],
        max_per_item: int = 1,
    ) -> List[Dict[str, Any]]:
        """부족분을 후보 품목에 분배 (판매 빈도순)"""
        result = []
        remaining = shortage

        for cand in candidates:
            if remaining <= 0:
                break
            add_qty = min(max_per_item, remaining)
            result.append({
                "item_cd": cand["item_cd"],
                "item_nm": cand.get("item_nm", ""),
                "add_qty": add_qty,
                "data_days": cand.get("appear_days", 0),
                "order_unit_qty": cand.get("order_unit_qty", 1),
            })
            remaining -= add_qty

        return result
