"""
대분류(large_cd) 기반 카테고리 총량 예측 및 mid_cd 배분 보충

기존 CategoryDemandForecaster(mid_cd level)를 보완하는 상위 계층 로직:
1. large_cd별 daily_sales 합계 → WMA 총량 예측
2. 하위 mid_cd별 최근 매출 비율 계산
3. 총량 × mid_cd 비율 = mid_cd별 예상 수요
4. 개별 상품 예측 합계 < mid_cd 예상 수요면 부족분 floor 보충

기존 category_floor(mid_cd) 뒤에 실행되어 상위 수준 보정 역할을 한다.
"""

import math
from typing import Any, Dict, List, Optional, Set, Tuple

from src.infrastructure.database.connection import DBRouter
from src.prediction.prediction_config import PREDICTION_PARAMS
from src.settings.constants import LARGE_CD_TO_MID_CD
from src.utils.logger import get_logger

logger = get_logger(__name__)


class LargeCategoryForecaster:
    """대분류(large_cd) 기반 카테고리 총량 예측 + mid_cd 배분 보충"""

    def __init__(self, store_id: str):
        self.store_id = store_id
        self._config = PREDICTION_PARAMS.get("large_category_floor", {})
        self._mid_floor_added: Dict[str, int] = {}  # mid Floor 보충량 (이중 보충 방지)

    @property
    def enabled(self) -> bool:
        """활성화 여부"""
        return self._config.get("enabled", False)

    @property
    def target_large_cds(self) -> List[str]:
        """적용 대상 대분류 코드 목록"""
        return self._config.get("target_large_cds", ["01", "02", "12"])

    def supplement_orders(
        self,
        order_list: List[Dict[str, Any]],
        eval_results: Optional[Dict[str, Any]] = None,
        cut_items: Optional[Set[str]] = None,
        site_order_counts: Optional[Dict[str, int]] = None,
        mid_floor_added: Optional[Dict[str, int]] = None,
    ) -> List[Dict[str, Any]]:
        """
        대분류 총량 대비 mid_cd별 부족분 보충 (메인 진입점)

        Args:
            order_list: 기존 발주 추천 목록
            eval_results: pre_order_evaluator 결과
            cut_items: 발주중지(CUT) 상품 set
            site_order_counts: {mid_cd: int} site 발주 수량 (이미 채워진 것으로 간주)
            mid_floor_added: {mid_cd: int} mid Floor에서 보충한 수량
                             (이중 보충 방지: large shortage에서 차감)

        Returns:
            보충된 발주 목록
        """
        if not self.enabled:
            return order_list

        self._mid_floor_added = mid_floor_added or {}
        total_supplemented = 0

        for large_cd in self.target_large_cds:
            # 1. large_cd 총량 WMA 예측
            total_forecast = self.forecast_large_cd_total(large_cd)
            if total_forecast <= 0:
                continue

            # 2. mid_cd 비율 계산
            ratios = self.get_mid_cd_ratios(large_cd)
            if not ratios:
                continue

            # 3. mid_cd별 예상 수요 배분
            targets = self.distribute_to_mid_cd(total_forecast, ratios)

            # 4. 부족분 보충 (mid 보충분 고려)
            supplemented = self._apply_floor_correction(
                order_list, targets, eval_results, cut_items,
                site_order_counts=site_order_counts
            )
            total_supplemented += supplemented

        if total_supplemented > 0:
            logger.info(
                f"[LargeCatFloor] 총 보충: {total_supplemented}개"
            )

        return order_list

    def forecast_large_cd_total(
        self, large_cd: str, days: Optional[int] = None
    ) -> float:
        """
        large_cd에 속하는 모든 상품의 daily_sales 합계로 WMA 총량 예측

        Args:
            large_cd: 대분류 코드
            days: WMA 기간 (기본: 설정값)

        Returns:
            WMA 총량 예측값
        """
        if days is None:
            days = self._config.get("wma_days", 14)

        daily_totals = self._get_large_cd_daily_totals(large_cd, days)
        if not daily_totals:
            return 0.0

        return self._calculate_wma(daily_totals)

    def get_mid_cd_ratios(
        self, large_cd: str, days: Optional[int] = None
    ) -> Dict[str, float]:
        """
        large_cd 내 각 mid_cd의 매출 비율 계산

        Args:
            large_cd: 대분류 코드
            days: 비율 계산 기간 (기본: 설정값)

        Returns:
            {mid_cd: 비율} dict. 비율 합 = 1.0
        """
        if days is None:
            days = self._config.get("ratio_days", 14)

        mid_cd_totals = self._get_mid_cd_totals(large_cd, days)
        if not mid_cd_totals:
            return {}

        grand_total = sum(mid_cd_totals.values())
        if grand_total <= 0:
            return {}

        return {
            mid_cd: round(total / grand_total, 4)
            for mid_cd, total in mid_cd_totals.items()
        }

    def distribute_to_mid_cd(
        self, total_forecast: float, ratios: Dict[str, float]
    ) -> Dict[str, float]:
        """
        총량을 mid_cd 비율로 배분

        Args:
            total_forecast: large_cd 총량 예측
            ratios: {mid_cd: 비율}

        Returns:
            {mid_cd: 예상수요}
        """
        return {
            mid_cd: round(total_forecast * ratio, 2)
            for mid_cd, ratio in ratios.items()
        }

    def _apply_floor_correction(
        self,
        order_list: List[Dict[str, Any]],
        mid_cd_targets: Dict[str, float],
        eval_results: Optional[Dict[str, Any]] = None,
        cut_items: Optional[Set[str]] = None,
        site_order_counts: Optional[Dict[str, int]] = None,
    ) -> int:
        """
        mid_cd별 부족분 보충

        Args:
            order_list: 발주 목록 (in-place 수정)
            mid_cd_targets: {mid_cd: 예상수요}
            eval_results: 사전평가 결과
            cut_items: CUT 상품 set
            site_order_counts: {mid_cd: int} site 발주 수량 (이미 채워진 것으로 간주)

        Returns:
            보충된 총 수량
        """
        threshold = self._config.get("threshold", 0.75)
        max_add = self._config.get("max_add_per_item", 2)
        total_supplemented = 0

        # 기존 발주 목록의 mid_cd별 합산
        existing_by_mid: Dict[str, int] = {}
        existing_items: Set[str] = set()
        for item in order_list:
            mid_cd = item.get("mid_cd", "")
            qty = item.get("final_order_qty", 0) or item.get("order_qty", 0)
            if mid_cd in mid_cd_targets:
                existing_by_mid[mid_cd] = existing_by_mid.get(mid_cd, 0) + qty
                existing_items.add(item.get("item_cd", ""))

        for mid_cd, target in mid_cd_targets.items():
            current_sum = existing_by_mid.get(mid_cd, 0)
            site_count = (site_order_counts or {}).get(mid_cd, 0)
            current_sum += site_count
            floor_qty = target * threshold

            if current_sum >= floor_qty:
                logger.debug(
                    f"[LargeCatFloor] {mid_cd}: 충분 "
                    f"(현재={current_sum}, floor={floor_qty:.1f}, "
                    f"target={target:.1f})"
                )
                continue

            raw_shortage = max(0, int(floor_qty) - current_sum)
            if raw_shortage <= 0:
                continue

            # mid Floor에서 이미 보충한 양을 차감 (이중 보충 방지)
            mid_added = self._mid_floor_added.get(mid_cd, 0)
            shortage = max(0, raw_shortage - mid_added)
            if shortage <= 0:
                logger.info(
                    f"[LargeCatFloor] {mid_cd}: mid Floor 보충({mid_added}개)으로 "
                    f"large 부족분({raw_shortage}개) 이미 충족 → 추가 보충 생략"
                )
                continue

            # 보충 후보 선정
            candidates = self._get_supplement_candidates(
                mid_cd, existing_items, eval_results, cut_items
            )
            if not candidates:
                logger.info(
                    f"[LargeCatFloor] {mid_cd}: 부족 {shortage}개이나 "
                    f"보충 후보 없음"
                )
                continue

            # 분배
            supplements = self._distribute_shortage(
                shortage, candidates, max_add
            )

            # order_list에 병합
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
                            f"[LargeCatFloor] {item_cd} 배수정렬: "
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
                        "source": "large_category_floor",
                        "model_type": "large_category_floor",
                    })
                    existing_items.add(item_cd)
                    actual_add = aligned_qty

                total_supplemented += actual_add

            # 보충 후 existing_by_mid 업데이트
            added = sum(s["add_qty"] for s in supplements)
            existing_by_mid[mid_cd] = existing_by_mid.get(mid_cd, 0) + added

            logger.info(
                f"[LargeCatFloor] {mid_cd}: target={target:.1f}, "
                f"현재={current_sum}, floor={floor_qty:.1f}, "
                f"보충={added}개 ({len(supplements)}품목)"
            )

        return total_supplemented

    def _get_large_cd_daily_totals(
        self, large_cd: str, days: int
    ) -> List[Tuple[str, int]]:
        """large_cd별 일별 총매출 시계열 조회 (store DB + common ATTACH)"""
        try:
            conn = DBRouter.get_store_connection_with_common(self.store_id)
            try:
                cursor = conn.cursor()
                # mid_categories에서 large_cd → mid_cd 매핑 시도
                cursor.execute(
                    "SELECT mid_cd FROM common.mid_categories WHERE large_cd = ?",
                    (large_cd,)
                )
                mid_cds_from_db = [row[0] for row in cursor.fetchall()]

                if not mid_cds_from_db:
                    # Fallback: 상수 매핑
                    mid_cds_from_db = LARGE_CD_TO_MID_CD.get(large_cd, [])
                    if not mid_cds_from_db:
                        return []

                placeholders = ",".join("?" * len(mid_cds_from_db))
                cursor.execute(f"""
                    SELECT ds.sales_date, SUM(ds.sale_qty) as total_sale
                    FROM daily_sales ds
                    WHERE ds.mid_cd IN ({placeholders})
                      AND ds.sales_date >= date('now', '-' || ? || ' days')
                      AND ds.sales_date < date('now')
                    GROUP BY ds.sales_date
                    ORDER BY ds.sales_date DESC
                """, (*mid_cds_from_db, days))

                return [(row[0], row[1] or 0) for row in cursor.fetchall()]
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[LargeCatFloor] daily_totals 조회 실패 ({large_cd}): {e}")
            return []

    def _get_mid_cd_totals(
        self, large_cd: str, days: int
    ) -> Dict[str, int]:
        """large_cd 내 mid_cd별 매출 합계 조회"""
        try:
            conn = DBRouter.get_store_connection_with_common(self.store_id)
            try:
                cursor = conn.cursor()
                # mid_categories에서 large_cd → mid_cd 매핑 시도
                cursor.execute(
                    "SELECT mid_cd FROM common.mid_categories WHERE large_cd = ?",
                    (large_cd,)
                )
                mid_cds_from_db = [row[0] for row in cursor.fetchall()]

                if not mid_cds_from_db:
                    mid_cds_from_db = LARGE_CD_TO_MID_CD.get(large_cd, [])
                    if not mid_cds_from_db:
                        return {}

                placeholders = ",".join("?" * len(mid_cds_from_db))
                cursor.execute(f"""
                    SELECT ds.mid_cd, SUM(ds.sale_qty) as mid_total
                    FROM daily_sales ds
                    WHERE ds.mid_cd IN ({placeholders})
                      AND ds.sales_date >= date('now', '-' || ? || ' days')
                      AND ds.sales_date < date('now')
                    GROUP BY ds.mid_cd
                """, (*mid_cds_from_db, days))

                return {row[0]: row[1] or 0 for row in cursor.fetchall()}
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[LargeCatFloor] mid_cd_totals 조회 실패 ({large_cd}): {e}")
            return {}

    def _calculate_wma(
        self, daily_totals: List[Tuple[str, int]]
    ) -> float:
        """WMA(Weighted Moving Average) 계산 — 최근일 가중"""
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
        min_sell_days = self._config.get("min_candidate_sell_days", 2)
        try:
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
                        logger.info(f"[LargeFloor] {mid_cd} is_available=0 후보 {unavail_cnt}개 제외")
                except Exception:
                    pass

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

                skip_codes: Set[str] = set()
                if eval_results:
                    try:
                        from src.prediction.pre_order_evaluator import EvalDecision
                        skip_codes = {
                            cd for cd, r in eval_results.items()
                            if r.decision == EvalDecision.SKIP
                        }
                    except Exception:
                        pass
                cut_codes = cut_items or set()

                candidates = []
                for row in cursor.fetchall():
                    item_cd = row[0]
                    if item_cd in skip_codes or item_cd in cut_codes:
                        continue
                    if item_cd in existing_items:
                        continue
                    candidates.append({
                        "item_cd": item_cd,
                        "appear_days": row[1],
                        "sell_days": row[2],
                        "total_sale": row[3],
                    })
            finally:
                conn.close()

            # 상품명 + 발주배수 조회 (common.db)
            if candidates:
                try:
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
                except Exception:
                    pass

            return candidates
        except Exception as e:
            logger.warning(f"[LargeCatFloor] 후보 조회 실패 ({mid_cd}): {e}")
            return []

    def _distribute_shortage(
        self,
        shortage: int,
        candidates: List[Dict[str, Any]],
        max_per_item: int = 2,
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
