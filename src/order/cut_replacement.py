"""
CutReplacementService -- CUT(발주중지) 상품 대체 보충

CUT 탈락 상품의 수요를 동일 mid_cd 내 대체 상품으로 보충한다.
OrderAdjuster(순수 계산 클래스)와 분리하여 DB 조회 로직을 별도 서비스로 관리.

food-cut-replacement PDCA
"""

import math
from typing import Any, Dict, List, Optional, Set

from src.infrastructure.database.connection import DBRouter
from src.prediction.prediction_config import PREDICTION_PARAMS
from src.utils.logger import get_logger

logger = get_logger(__name__)


class CutReplacementService:
    """CUT 대체 보충 서비스 — DB 조회 + 후보 선정 + 분배 담당"""

    def __init__(self, store_id: str):
        self.store_id = store_id
        self._config = PREDICTION_PARAMS.get("cut_replacement", {})

    @property
    def enabled(self) -> bool:
        return self._config.get("enabled", False)

    @property
    def target_mid_cds(self) -> Set[str]:
        return set(self._config.get(
            "target_mid_cds", ["001", "002", "003", "004", "005", "012"]
        ))

    def supplement_cut_shortage(
        self,
        order_list: List[Dict[str, Any]],
        cut_lost_items: List[Dict[str, Any]],
        eval_results: Optional[Dict] = None,
    ) -> List[Dict[str, Any]]:
        """CUT 탈락 상품의 수요를 동일 mid_cd 내 대체 상품으로 보충

        Args:
            order_list: 현재 발주 목록
            cut_lost_items: CUT 탈락 상품 목록
                [{item_cd, mid_cd, predicted_sales, item_nm}, ...]
            eval_results: 사전 평가 결과 (SKIP 제외용)

        Returns:
            보충 반영된 발주 목록
        """
        if not self.enabled:
            logger.debug("[CUT보충] 비활성 (cut_replacement.enabled=False)")
            return order_list

        if not cut_lost_items:
            return order_list

        replacement_ratio = self._config.get("replacement_ratio", 0.8)
        max_add_per_item = self._config.get("max_add_per_item", 2)
        max_candidates = self._config.get("max_candidates", 5)
        min_sell_days = self._config.get("min_sell_days", 1)

        # Step 1: mid_cd별 손실 수요 집계
        lost_demand = {}  # {mid_cd: float}
        for item in cut_lost_items:
            mid_cd = item.get("mid_cd", "")
            if mid_cd not in self.target_mid_cds:
                continue
            pred = item.get("predicted_sales", 0) or item.get("final_order_qty", 0)
            if pred > 0:
                lost_demand[mid_cd] = lost_demand.get(mid_cd, 0) + pred

        if not lost_demand:
            return order_list

        # CUT 상품코드 집합 (후보에서 제외용)
        cut_item_cds = {item.get("item_cd") for item in cut_lost_items}

        # SKIP 평가 상품 제외용
        skip_codes = set()
        if eval_results:
            from src.prediction.pre_order_evaluator import EvalDecision
            skip_codes = {
                cd for cd, r in eval_results.items()
                if r.decision == EvalDecision.SKIP
            }

        # 기존 발주 목록 item_cd 맵
        existing_items = {item.get("item_cd") for item in order_list}
        item_map = {item.get("item_cd"): item for item in order_list}

        total_supplemented = 0
        mid_cd_summary = {}

        for mid_cd, demand in lost_demand.items():
            # CUT 건수
            cut_count = sum(
                1 for item in cut_lost_items
                if item.get("mid_cd") == mid_cd
            )

            # Step 2: 후보 선정
            candidates = self._get_candidates(
                mid_cd=mid_cd,
                existing_items=existing_items,
                cut_item_cds=cut_item_cds,
                skip_codes=skip_codes,
                min_sell_days=min_sell_days,
                max_candidates=max_candidates,
            )

            if not candidates:
                logger.info(
                    f"[CUT보충] mid={mid_cd}: CUT {cut_count}건"
                    f"(수요합={demand:.2f}) → 후보 0건 (대체 불가)"
                )
                mid_cd_summary[mid_cd] = 0
                continue

            # Step 3: 스코어 계산 (정규화)
            self._calculate_scores(candidates)

            # Step 4: 보충 분배
            candidates.sort(key=lambda c: -c["score"])
            remaining = demand * replacement_ratio
            added_items = []

            for cand in candidates:
                if remaining <= 0:
                    break

                max_add = min(
                    max_add_per_item,
                    max(1, round(cand["daily_avg"])),
                )
                add_qty = min(max_add, max(1, int(remaining + 0.5)))

                if add_qty <= 0:
                    continue

                remaining -= add_qty

                # order_list에 병합
                item_cd = cand["item_cd"]
                order_unit = cand.get("order_unit_qty", 1) or 1
                existing = item_map.get(item_cd)
                if existing:
                    # 기존 항목 수량 증가 + 배수 재정렬
                    prev_qty = existing.get("final_order_qty", 0)
                    new_qty = prev_qty + add_qty
                    if order_unit > 1:
                        new_qty = math.ceil(
                            new_qty / order_unit
                        ) * order_unit
                    existing["final_order_qty"] = new_qty
                    actual_add = new_qty - prev_qty
                    if actual_add != add_qty:
                        logger.info(
                            f"[CUT보충] {item_cd} 배수정렬: "
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
                    new_item = {
                        "item_cd": item_cd,
                        "item_nm": cand.get("item_nm", ""),
                        "mid_cd": mid_cd,
                        "final_order_qty": aligned_qty,
                        "predicted_qty": 0,
                        "predicted_sales": 0,
                        "order_qty": aligned_qty,
                        "current_stock": cand.get("current_stock", 0),
                        "data_days": cand.get("sell_days", 0),
                        "order_unit_qty": order_unit,
                        "replaced_mid_cd": mid_cd,
                        "source": "cut_replacement",
                        "model_type": "cut_replacement",
                    }
                    order_list.append(new_item)
                    existing_items.add(item_cd)
                    item_map[item_cd] = new_item
                    actual_add = aligned_qty

                remaining -= (actual_add - add_qty)  # 정렬 증가분 반영
                total_supplemented += actual_add
                added_items.append((cand, actual_add))

            # 로깅
            total_added = sum(qty for _, qty in added_items)
            logger.info(
                f"[CUT보충] mid={mid_cd}: CUT {cut_count}건"
                f"(수요합={demand:.2f}) → 후보 {len(candidates)}건 → "
                f"보충 {len(added_items)}건"
                f"({total_added}/{demand:.2f}, "
                f"{total_added / demand * 100:.0f}%)"
            )
            for cand, qty in added_items:
                logger.info(
                    f"[CUT보충]   {cand.get('item_nm', cand['item_cd'])[:20]}: "
                    f"+{qty} (score={cand['score']:.2f}, "
                    f"daily={cand['daily_avg']:.2f}, "
                    f"sell={cand['sell_days']}/7, "
                    f"stk={cand.get('current_stock', 0)})"
                )
            mid_cd_summary[mid_cd] = total_added

        if total_supplemented > 0:
            summary_str = ", ".join(
                f"{mid}={cnt}" for mid, cnt in sorted(mid_cd_summary.items())
            )
            logger.info(f"[CUT보충] 총 보충: +{total_supplemented}개 ({summary_str})")

        return order_list

    def _get_candidates(
        self,
        mid_cd: str,
        existing_items: Set[str],
        cut_item_cds: Set[str],
        skip_codes: Set[str],
        min_sell_days: int = 1,
        max_candidates: int = 5,
    ) -> List[Dict[str, Any]]:
        """mid_cd별 대체 후보 선정 (DB 조회)"""
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
                    logger.info(f"[CUT보충] {mid_cd} is_available=0 후보 {unavail_cnt}개 제외")
            except Exception:
                pass

            cursor.execute("""
                SELECT ds.item_cd,
                       COUNT(DISTINCT CASE WHEN ds.sale_qty > 0
                             THEN ds.sales_date END) as sell_days,
                       SUM(ds.sale_qty) as total_sale,
                       COALESCE(ri.stock_qty, 0) as current_stock,
                       COALESCE(ri.pending_qty, 0) as pending_qty
                FROM daily_sales ds
                LEFT JOIN realtime_inventory ri
                    ON ds.item_cd = ri.item_cd
                WHERE ds.mid_cd = ?
                  AND ds.sales_date >= date('now', '-7 days')
                  AND ds.sales_date < date('now')
                  AND COALESCE(ri.is_available, 1) = 1
                GROUP BY ds.item_cd
                HAVING sell_days >= ?
                ORDER BY sell_days DESC, total_sale DESC
            """, (mid_cd, min_sell_days))

            candidates = []
            for row in cursor.fetchall():
                item_cd = row[0]
                if item_cd in cut_item_cds:
                    continue
                if item_cd in skip_codes:
                    continue
                # 이미 발주 목록에 있는 상품은 수량 증가 대상이므로 후보에 포함
                sell_days = row[1] or 0
                total_sale = row[2] or 0
                current_stock = row[3] or 0
                pending_qty = row[4] or 0

                daily_avg = total_sale / 7.0 if total_sale > 0 else 0

                candidates.append({
                    "item_cd": item_cd,
                    "sell_days": sell_days,
                    "sell_day_ratio": sell_days / 7.0,
                    "total_sale": total_sale,
                    "daily_avg": daily_avg,
                    "current_stock": current_stock,
                    "pending_qty": pending_qty,
                })

                if len(candidates) >= max_candidates * 2:
                    break  # 충분한 후보 확보

            # 상품명 + 유통기한 조회 (배치)
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

                    # 유통기한 + 발주배수 조회
                    exp_rows = common_cursor.execute(
                        f"SELECT item_cd, expiration_days, "
                        f"COALESCE(order_unit_qty, 1) "
                        f"FROM product_details "
                        f"WHERE item_cd IN ({placeholders})",
                        item_cds
                    ).fetchall()
                    exp_map = {r[0]: r[1] for r in exp_rows}
                    unit_map = {r[0]: r[2] for r in exp_rows}

                    for cand in candidates:
                        cand["item_nm"] = name_map.get(cand["item_cd"], "")
                        cand["expiration_days"] = exp_map.get(
                            cand["item_cd"], 99
                        )
                        cand["order_unit_qty"] = unit_map.get(
                            cand["item_cd"], 1
                        ) or 1
                finally:
                    common_conn.close()

            return candidates[:max_candidates]
        finally:
            conn.close()

    def _calculate_scores(
        self, candidates: List[Dict[str, Any]]
    ) -> None:
        """후보 풀 내 min-max 정규화 스코어 계산"""
        if not candidates:
            return

        daily_avgs = [c["daily_avg"] for c in candidates]
        min_avg = min(daily_avgs)
        max_avg = max(daily_avgs)

        for c in candidates:
            # 정규화: 0~1 범위
            norm_daily = (
                (c["daily_avg"] - min_avg) / (max_avg - min_avg + 1e-9)
            )
            norm_sell = c["sell_day_ratio"]  # 이미 0~1

            # 유통기한 1일 이하: 어제 재고 = 폐기 → effective_stock=0
            exp_days = c.get("expiration_days", 99)
            if not isinstance(exp_days, (int, float)):
                exp_days = 99
            if exp_days <= 1:
                effective_stock = 0
            else:
                effective_stock = c["current_stock"]

            stock_ratio = effective_stock / max(1, 2)  # 안전재고 기본 2
            norm_stock = 1 - min(1.0, stock_ratio)

            c["score"] = norm_daily * 0.5 + norm_sell * 0.3 + norm_stock * 0.2
