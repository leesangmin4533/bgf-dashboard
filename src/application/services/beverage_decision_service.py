"""
BeverageDecisionService -- 음료 발주 유지/정지 판단 오케스트레이션

카테고리 분류 → 행사 보호 → 생애주기 판별 → 판매 집계 → 판단 → 저장
"""

import statistics
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple, Any

from src.infrastructure.database.connection import DBRouter
from src.infrastructure.database.repos import (
    BeverageDecisionRepository,
    ProductDetailRepository,
)
from src.prediction.categories.beverage_decision.enums import (
    BeverageCategory,
    BeverageLifecycle,
    BeverageDecisionType,
    JudgmentCycle,
    FirstReceivingSource,
    CATEGORY_JUDGMENT_CYCLE,
)
from src.prediction.categories.beverage_decision.classifier import classify_beverage_category
from src.prediction.categories.beverage_decision.lifecycle import determine_lifecycle
from src.prediction.categories.beverage_decision.judge import (
    judge_item,
    calc_sale_rate,
    count_consecutive_low_weeks,
    count_consecutive_zero_months,
)
from src.prediction.categories.beverage_decision.models import (
    BeverageItemContext,
    BeverageSalesMetrics,
)
from src.prediction.categories.beverage_decision.constants import (
    BEVERAGE_MID_CDS,
    PROMO_PROTECTION_WEEKS,
    SEASONAL_OFF_PEAK,
    SEASONAL_THRESHOLD_FACTOR,
    CAT_C_SHELF_EFFICIENCY_THRESHOLD,
    AUTO_CONFIRM_DAYS,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


class BeverageDecisionService:
    """음료 발주 유지/정지 판단 서비스"""

    def __init__(self, store_id: str):
        self.store_id = store_id
        self.decision_repo = BeverageDecisionRepository(store_id=store_id)

    def run(
        self,
        target_categories: Optional[List[str]] = None,
        reference_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """판단 실행"""
        ref_date = reference_date or datetime.now().strftime("%Y-%m-%d")
        logger.info(f"[음료판단] 시작 store={self.store_id} date={ref_date} "
                     f"categories={target_categories or 'ALL'}")

        # 1. 음료 상품 로드 + 분류
        items = self._load_beverage_items()
        if not items:
            logger.warning("[음료판단] 음료 상품 없음")
            return {"total_items": 0, "decisions": {}, "results": []}

        # 2. 카테고리별 필터링
        target_set = set(target_categories) if target_categories else None
        results: List[Dict[str, Any]] = []
        counts = {"KEEP": 0, "WATCH": 0, "STOP_RECOMMEND": 0}
        cat_counts = {"A": 0, "B": 0, "C": 0, "D": 0}
        ref_month = datetime.strptime(ref_date, "%Y-%m-%d").month

        for ctx in items:
            category = classify_beverage_category(
                ctx.mid_cd, ctx.small_nm, ctx.expiration_days, ctx.item_nm,
            )

            if target_set and category.value not in target_set:
                continue

            cat_counts[category.value] = cat_counts.get(category.value, 0) + 1

            # 행사 보호 체크 (§4.0)
            if self._is_promo_protected(ctx.item_cd, ref_date):
                results.append(self._build_result(
                    ctx, category, BeverageLifecycle.ESTABLISHED, 0,
                    ref_date, "KEEP", "행사 보호 기간 (판단 유예)",
                ))
                counts["KEEP"] += 1
                continue

            # 3. 생애주기 판별
            first_date, source = self._resolve_first_receiving_date(ctx.item_cd)
            ctx.first_receiving_date = first_date
            ctx.first_receiving_source = source.value

            if source == FirstReceivingSource.NONE:
                continue

            lifecycle, weeks = determine_lifecycle(
                first_date, ref_date, category, source,
            )
            if lifecycle is None:
                continue

            # 4. 판단 기간 계산
            cycle = CATEGORY_JUDGMENT_CYCLE[category]
            period_start, period_end = self._get_judgment_period(category, ref_date)

            # 5. 판매 집계 (매대효율 포함)
            metrics = self._aggregate_metrics(
                ctx.item_cd, ctx.mid_cd, ctx.small_nm, category,
                period_start, period_end, ctx.sell_price, ref_date,
            )

            # 6. 계절 비수기 보정 (§4.3)
            shelf_threshold = None
            if ctx.mid_cd in SEASONAL_OFF_PEAK:
                if ref_month in SEASONAL_OFF_PEAK[ctx.mid_cd]:
                    shelf_threshold = CAT_C_SHELF_EFFICIENCY_THRESHOLD * SEASONAL_THRESHOLD_FACTOR

            # 7. 판단
            decision, reason, is_warning = judge_item(
                category, lifecycle, metrics,
                sell_price=ctx.sell_price,
                margin_rate=ctx.margin_rate,
                shelf_threshold_override=shelf_threshold,
            )

            counts[decision.value] = counts.get(decision.value, 0) + 1

            results.append({
                "store_id": self.store_id,
                "item_cd": ctx.item_cd,
                "item_nm": ctx.item_nm,
                "mid_cd": ctx.mid_cd,
                "dessert_category": category.value,
                "expiration_days": ctx.expiration_days,
                "small_nm": ctx.small_nm,
                "lifecycle_phase": lifecycle.value,
                "first_receiving_date": first_date,
                "first_receiving_source": source,
                "weeks_since_intro": weeks,
                "judgment_period_start": period_start,
                "judgment_period_end": period_end,
                "total_order_qty": metrics.total_order_qty,
                "total_sale_qty": metrics.total_sale_qty,
                "total_disuse_qty": metrics.total_disuse_qty,
                "sale_amount": metrics.sale_amount,
                "disuse_amount": metrics.disuse_amount,
                "sell_price": ctx.sell_price,
                "sale_rate": metrics.sale_rate,
                "category_avg_sale_qty": metrics.category_avg_sale_qty,
                "prev_period_sale_qty": metrics.prev_period_sale_qty,
                "sale_trend_pct": metrics.sale_trend_pct,
                "consecutive_low_weeks": metrics.consecutive_low_weeks,
                "consecutive_zero_months": metrics.consecutive_zero_months,
                "decision": decision.value,
                "decision_reason": reason,
                "is_rapid_decline_warning": is_warning,
                "judgment_cycle": cycle.value,
            })

        # 8. 저장
        saved = self.decision_repo.save_decisions_batch(results)
        logger.info(f"[음료판단] 완료: {saved}건 저장 — "
                     f"KEEP={counts['KEEP']} WATCH={counts['WATCH']} "
                     f"STOP={counts['STOP_RECOMMEND']}")

        # 9. auto_confirm (카테고리별 차등)
        auto_confirmed = self._auto_confirm_zero_sales(results)

        return {
            "total_items": len(results),
            "by_category": cat_counts,
            "decisions": counts,
            "auto_confirmed": len(auto_confirmed),
            "results": results,
        }

    # =========================================================================
    # Internal methods
    # =========================================================================

    def _build_result(
        self, ctx: BeverageItemContext, category: BeverageCategory,
        lifecycle: BeverageLifecycle, weeks: int,
        ref_date: str, decision: str, reason: str,
    ) -> Dict[str, Any]:
        """행사 보호 등 단축 결과 생성"""
        cycle = CATEGORY_JUDGMENT_CYCLE[category]
        period_start, period_end = self._get_judgment_period(category, ref_date)
        return {
            "store_id": self.store_id,
            "item_cd": ctx.item_cd,
            "item_nm": ctx.item_nm,
            "mid_cd": ctx.mid_cd,
            "dessert_category": category.value,
            "expiration_days": ctx.expiration_days,
            "small_nm": ctx.small_nm,
            "lifecycle_phase": lifecycle.value,
            "first_receiving_date": ctx.first_receiving_date,
            "first_receiving_source": ctx.first_receiving_source,
            "weeks_since_intro": weeks,
            "judgment_period_start": period_start,
            "judgment_period_end": period_end,
            "total_order_qty": 0,
            "total_sale_qty": 0,
            "total_disuse_qty": 0,
            "sale_amount": 0,
            "disuse_amount": 0,
            "sell_price": ctx.sell_price,
            "sale_rate": 0.0,
            "category_avg_sale_qty": 0.0,
            "prev_period_sale_qty": 0,
            "sale_trend_pct": 0.0,
            "consecutive_low_weeks": 0,
            "consecutive_zero_months": 0,
            "decision": decision,
            "decision_reason": reason,
            "is_rapid_decline_warning": False,
            "judgment_cycle": cycle.value,
        }

    def _is_promo_protected(self, item_cd: str, ref_date: str) -> bool:
        """행사 종료 후 보호 기간 체크 (§4.0)"""
        conn = DBRouter.get_store_connection(self.store_id)
        try:
            cursor = conn.execute("""
                SELECT promo_type, MAX(sales_date) as last_promo_date
                FROM daily_sales
                WHERE item_cd = ? AND promo_type IS NOT NULL AND promo_type != ''
                GROUP BY promo_type
                ORDER BY last_promo_date DESC
                LIMIT 1
            """, (item_cd,))
            row = cursor.fetchone()
            if not row or not row[1]:
                return False

            promo_type = row[0]
            last_promo_date = datetime.strptime(row[1], "%Y-%m-%d")
            ref = datetime.strptime(ref_date, "%Y-%m-%d")

            protection_weeks = PROMO_PROTECTION_WEEKS.get(promo_type, 0)
            if protection_weeks == 0:
                return False

            protection_end = last_promo_date + timedelta(weeks=protection_weeks)
            return ref <= protection_end
        except Exception:
            return False

    def _auto_confirm_zero_sales(
        self, results: List[Dict[str, Any]],
    ) -> List[str]:
        """카테고리별 차등 무판매 기간 초과 시 자동 CONFIRMED_STOP (§4.5)"""
        stop_items = [
            (r["item_cd"], r["dessert_category"])
            for r in results
            if r["decision"] == "STOP_RECOMMEND"
        ]
        if not stop_items:
            return []

        conn = self.decision_repo._get_conn()
        confirmed_cds = []

        for item_cd, cat in stop_items:
            days = AUTO_CONFIRM_DAYS.get(cat, 30)
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

            cursor = conn.execute("""
                SELECT COUNT(*) FROM daily_sales
                WHERE item_cd = ? AND sales_date >= ? AND sale_qty > 0
            """, (item_cd, cutoff))

            if cursor.fetchone()[0] == 0:
                confirmed_cds.append(item_cd)

        if not confirmed_cds:
            return []

        confirmed = self.decision_repo.batch_update_operator_action(
            item_cds=confirmed_cds,
            action="CONFIRMED_STOP",
            operator_note=f"auto: 카테고리별 무판매 자동확인",
            store_id=self.store_id,
        )

        if confirmed:
            logger.info(f"[음료판단] 자동확인 {len(confirmed)}건 (무판매 → CONFIRMED_STOP)")
        return [c["item_cd"] for c in confirmed]

    def _load_beverage_items(self) -> List[BeverageItemContext]:
        """common.db에서 음료 상품 로드 (mid_cd 039~048)"""
        conn = DBRouter.get_connection(table="products")
        placeholders = ",".join("?" * len(BEVERAGE_MID_CDS))
        cursor = conn.execute(f"""
            SELECT p.item_cd, p.item_nm, p.mid_cd,
                   pd.expiration_days, pd.small_nm, pd.sell_price, pd.margin_rate
            FROM products p
            LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
            WHERE p.mid_cd IN ({placeholders})
            ORDER BY p.mid_cd, p.item_nm
        """, BEVERAGE_MID_CDS)

        items = []
        for row in cursor.fetchall():
            items.append(BeverageItemContext(
                item_cd=row[0],
                item_nm=row[1] or "",
                mid_cd=row[2] or "",
                small_nm=row[4],
                expiration_days=row[3],
                sell_price=row[5] or 0,
                margin_rate=row[6],
            ))
        return items

    def _resolve_first_receiving_date(
        self, item_cd: str,
    ) -> Tuple[Optional[str], FirstReceivingSource]:
        """5소스 우선순위로 첫 입고일 판별 (디저트와 동일)"""
        conn = DBRouter.get_store_connection(self.store_id)

        # 1순위: detected_new_products
        try:
            cursor = conn.execute(
                "SELECT first_receiving_date FROM detected_new_products "
                "WHERE item_cd = ? ORDER BY first_receiving_date ASC LIMIT 1",
                (item_cd,),
            )
            row = cursor.fetchone()
            if row and row[0]:
                return row[0], FirstReceivingSource.DETECTED
        except Exception:
            pass

        # 2순위: 첫 판매일
        try:
            cursor = conn.execute(
                "SELECT MIN(sales_date) FROM daily_sales "
                "WHERE item_cd = ? AND sale_qty > 0",
                (item_cd,),
            )
            row = cursor.fetchone()
            if row and row[0]:
                return row[0], FirstReceivingSource.DAILY_SALES_SOLD
        except Exception:
            pass

        # 2-1순위: 첫 입고일
        try:
            cursor = conn.execute(
                "SELECT MIN(sales_date) FROM daily_sales "
                "WHERE item_cd = ? AND buy_qty > 0",
                (item_cd,),
            )
            row = cursor.fetchone()
            if row and row[0]:
                return row[0], FirstReceivingSource.DAILY_SALES_BOUGHT
        except Exception:
            pass

        # 3순위: products.created_at
        try:
            common_conn = DBRouter.get_connection(table="products")
            cursor = common_conn.execute(
                "SELECT created_at FROM products WHERE item_cd = ?",
                (item_cd,),
            )
            row = cursor.fetchone()
            if row and row[0]:
                date_str = row[0][:10]
                return date_str, FirstReceivingSource.PRODUCTS
        except Exception:
            pass

        return None, FirstReceivingSource.NONE

    def _get_judgment_period(
        self,
        category: BeverageCategory,
        reference_date: str,
    ) -> Tuple[str, str]:
        """카테고리별 판단 기간 계산"""
        ref = datetime.strptime(reference_date, "%Y-%m-%d")

        if category == BeverageCategory.A:
            days = 7
        elif category == BeverageCategory.B:
            days = 14
        else:  # C, D
            days = 30

        start = (ref - timedelta(days=days)).strftime("%Y-%m-%d")
        return start, reference_date

    def _aggregate_metrics(
        self,
        item_cd: str,
        mid_cd: str,
        small_nm: Optional[str],
        category: BeverageCategory,
        period_start: str,
        period_end: str,
        sell_price: int,
        reference_date: str,
    ) -> BeverageSalesMetrics:
        """판매 집계 + 매대효율지표 계산"""
        conn = DBRouter.get_store_connection(self.store_id)

        # 현재 기간 집계
        cursor = conn.execute("""
            SELECT COALESCE(SUM(sale_qty), 0),
                   COALESCE(SUM(disuse_qty), 0),
                   COALESCE(SUM(ord_qty), 0)
            FROM daily_sales
            WHERE item_cd = ?
              AND sales_date >= ? AND sales_date <= ?
        """, (item_cd, period_start, period_end))

        row = cursor.fetchone()
        total_sale = row[0] if row else 0
        total_disuse = row[1] if row else 0
        total_order = row[2] if row else 0

        sale_rate = calc_sale_rate(total_sale, total_disuse)
        sale_amount = total_sale * sell_price
        disuse_amount = total_disuse * sell_price

        # 이전 기간 (추세)
        period_days = (datetime.strptime(period_end, "%Y-%m-%d") -
                       datetime.strptime(period_start, "%Y-%m-%d")).days
        prev_end = (datetime.strptime(period_start, "%Y-%m-%d") -
                    timedelta(days=1)).strftime("%Y-%m-%d")
        prev_start = (datetime.strptime(period_start, "%Y-%m-%d") -
                      timedelta(days=period_days)).strftime("%Y-%m-%d")

        cursor = conn.execute("""
            SELECT COALESCE(SUM(sale_qty), 0)
            FROM daily_sales
            WHERE item_cd = ? AND sales_date >= ? AND sales_date <= ?
        """, (item_cd, prev_start, prev_end))
        prev_sale = cursor.fetchone()[0]

        trend_pct = 0.0
        if prev_sale > 0:
            trend_pct = round((total_sale - prev_sale) / prev_sale * 100, 1)

        # 매대효율지표 (§2.6): 소분류 중위값 기반
        median_sale, shelf_eff = self._calc_shelf_efficiency(
            item_cd, mid_cd, small_nm, total_sale, period_start, period_end,
        )

        # 카테고리 평균 (Cat A 정착기용)
        cat_avg = self._get_small_cd_avg_sale(
            mid_cd, small_nm, period_start, period_end,
        )

        # 주별 판매율 (연속 미달 역산)
        weekly_rates = self._aggregate_weekly_rates(item_cd, reference_date, weeks=8)

        # 월별 판매수량 (Cat D 연속 무판매)
        monthly_qtys = self._aggregate_monthly_qtys(item_cd, reference_date, months=4)

        return BeverageSalesMetrics(
            period_start=period_start,
            period_end=period_end,
            total_order_qty=total_order,
            total_sale_qty=total_sale,
            total_disuse_qty=total_disuse,
            sale_amount=sale_amount,
            disuse_amount=disuse_amount,
            sale_rate=sale_rate,
            shelf_efficiency=shelf_eff,
            small_cd_median_sale_qty=median_sale,
            category_avg_sale_qty=cat_avg,
            prev_period_sale_qty=prev_sale,
            sale_trend_pct=trend_pct,
            consecutive_low_weeks=count_consecutive_low_weeks(weekly_rates, 0.5),
            consecutive_zero_months=count_consecutive_zero_months(monthly_qtys),
            weekly_sale_rates=weekly_rates,
        )

    def _calc_shelf_efficiency(
        self,
        item_cd: str,
        mid_cd: str,
        small_nm: Optional[str],
        item_sale_qty: int,
        period_start: str,
        period_end: str,
    ) -> Tuple[float, float]:
        """매대효율지표 계산.

        Returns:
            (소분류 중위 판매량, 매대효율지표)
        """
        conn = DBRouter.get_store_connection(self.store_id)
        common_conn = DBRouter.get_connection(table="products")

        # 같은 소분류 상품 찾기
        if small_nm:
            cursor = common_conn.execute("""
                SELECT p.item_cd FROM products p
                JOIN product_details pd ON p.item_cd = pd.item_cd
                WHERE pd.small_nm = ? AND p.mid_cd = ?
            """, (small_nm, mid_cd))
        else:
            # 소분류 없으면 중분류 전체
            cursor = common_conn.execute("""
                SELECT item_cd FROM products WHERE mid_cd = ?
            """, (mid_cd,))

        peer_items = [r[0] for r in cursor.fetchall()]

        if len(peer_items) <= 1:
            # 동일 소분류 1개뿐 → 중분류 폴백
            cursor = common_conn.execute(
                "SELECT item_cd FROM products WHERE mid_cd = ?", (mid_cd,),
            )
            peer_items = [r[0] for r in cursor.fetchall()]

        if not peer_items:
            return 0.0, 0.0

        # 각 상품의 기간 내 판매량
        placeholders = ",".join("?" * len(peer_items))
        cursor = conn.execute(f"""
            SELECT item_cd, COALESCE(SUM(sale_qty), 0) as total_sale
            FROM daily_sales
            WHERE item_cd IN ({placeholders})
              AND sales_date >= ? AND sales_date <= ?
            GROUP BY item_cd
        """, peer_items + [period_start, period_end])

        peer_sales = [row[1] for row in cursor.fetchall()]

        if not peer_sales:
            return 0.0, 0.0

        median_sale = statistics.median(peer_sales)

        if median_sale <= 0:
            return 0.0, 0.0

        shelf_eff = round(item_sale_qty / median_sale, 4)
        return median_sale, shelf_eff

    def _get_small_cd_avg_sale(
        self,
        mid_cd: str,
        small_nm: Optional[str],
        period_start: str,
        period_end: str,
    ) -> float:
        """소분류(또는 중분류) 평균 판매량"""
        conn = DBRouter.get_store_connection(self.store_id)
        common_conn = DBRouter.get_connection(table="products")

        if small_nm:
            cursor = common_conn.execute("""
                SELECT p.item_cd FROM products p
                JOIN product_details pd ON p.item_cd = pd.item_cd
                WHERE pd.small_nm = ? AND p.mid_cd = ?
            """, (small_nm, mid_cd))
        else:
            cursor = common_conn.execute(
                "SELECT item_cd FROM products WHERE mid_cd = ?", (mid_cd,),
            )

        same_items = [r[0] for r in cursor.fetchall()]
        if not same_items:
            return 0.0

        placeholders = ",".join("?" * len(same_items))
        cursor = conn.execute(f"""
            SELECT COALESCE(SUM(sale_qty), 0), COUNT(DISTINCT item_cd)
            FROM daily_sales
            WHERE item_cd IN ({placeholders})
              AND sales_date >= ? AND sales_date <= ?
        """, same_items + [period_start, period_end])

        row = cursor.fetchone()
        total = row[0] if row else 0
        item_count = row[1] if row else 0

        if item_count == 0:
            return 0.0
        return round(total / item_count, 2)

    def _aggregate_weekly_rates(
        self, item_cd: str, reference_date: str, weeks: int = 8,
    ) -> List[float]:
        """최근 N주 각각의 판매율"""
        conn = DBRouter.get_store_connection(self.store_id)
        ref = datetime.strptime(reference_date, "%Y-%m-%d")
        rates = []

        for w in range(weeks):
            end = ref - timedelta(days=7 * w)
            start = end - timedelta(days=7)
            cursor = conn.execute("""
                SELECT COALESCE(SUM(sale_qty), 0), COALESCE(SUM(disuse_qty), 0)
                FROM daily_sales
                WHERE item_cd = ? AND sales_date > ? AND sales_date <= ?
            """, (item_cd, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))
            row = cursor.fetchone()
            rates.append(calc_sale_rate(row[0], row[1]))

        return rates

    def _aggregate_monthly_qtys(
        self, item_cd: str, reference_date: str, months: int = 4,
    ) -> List[int]:
        """최근 N개월 각각의 판매수량"""
        conn = DBRouter.get_store_connection(self.store_id)
        ref = datetime.strptime(reference_date, "%Y-%m-%d")
        qtys = []

        for m in range(months):
            end = ref - timedelta(days=30 * m)
            start = end - timedelta(days=30)
            cursor = conn.execute("""
                SELECT COALESCE(SUM(sale_qty), 0)
                FROM daily_sales
                WHERE item_cd = ? AND sales_date > ? AND sales_date <= ?
            """, (item_cd, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))
            qtys.append(cursor.fetchone()[0])

        return qtys
