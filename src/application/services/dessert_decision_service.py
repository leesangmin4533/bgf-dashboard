"""
DessertDecisionService -- 디저트 발주 유지/정지 판단 오케스트레이션

카테고리 분류 → 생애주기 판별 → 판매 집계 → 판단 → 저장
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple, Any

from src.infrastructure.database.connection import DBRouter, attach_common_with_views
from src.infrastructure.database.repos import (
    DessertDecisionRepository,
    ProductDetailRepository,
)
from src.prediction.categories.dessert_decision.enums import (
    DessertCategory,
    DessertLifecycle,
    DessertDecisionType,
    JudgmentCycle,
    FirstReceivingSource,
    CATEGORY_JUDGMENT_CYCLE,
)
from src.settings.constants import (
    DESSERT_BULK_REGISTRATION_DATES,
    DESSERT_CONFIRMED_STOP_WASTE_WEEKS,
    DESSERT_CONFIRMED_STOP_WASTE_DAYS,
    DESSERT_WASTE_RATE_STOP_THRESHOLD,
)
from src.prediction.categories.dessert_decision.classifier import classify_dessert_category
from src.prediction.categories.dessert_decision.lifecycle import determine_lifecycle
from src.prediction.categories.dessert_decision.judge import (
    judge_item,
    calc_sale_rate,
    calc_waste_rate,
    count_consecutive_low_weeks,
    count_consecutive_zero_months,
)
from src.prediction.categories.dessert_decision.models import (
    DessertItemContext,
    DessertSalesMetrics,
    DessertDecisionResult,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DessertDecisionService:
    """디저트 발주 유지/정지 판단 서비스"""

    def __init__(self, store_id: str):
        self.store_id = store_id
        self.decision_repo = DessertDecisionRepository(store_id=store_id)

    def run(
        self,
        target_categories: Optional[List[str]] = None,
        reference_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """판단 실행

        Args:
            target_categories: 대상 카테고리 ["A","B","C","D"] (None이면 전체)
            reference_date: 기준일 YYYY-MM-DD (None이면 오늘)
        """
        ref_date = reference_date or datetime.now().strftime("%Y-%m-%d")
        logger.info(f"[디저트판단] 시작 store={self.store_id} date={ref_date} "
                     f"categories={target_categories or 'ALL'}")

        # 1. 디저트 상품 로드 + 분류
        items = self._load_dessert_items()
        if not items:
            logger.warning("[디저트판단] mid_cd='014' 상품 없음")
            return {"total_items": 0, "decisions": {}, "results": []}

        # 2. 카테고리별 필터링
        target_set = set(target_categories) if target_categories else None
        results: List[Dict[str, Any]] = []
        counts = {"KEEP": 0, "WATCH": 0, "REDUCE_ORDER": 0, "STOP_RECOMMEND": 0}
        cat_counts = {"A": 0, "B": 0, "C": 0, "D": 0}

        for ctx in items:
            category = classify_dessert_category(ctx.small_nm, ctx.expiration_days)

            if target_set and category.value not in target_set:
                continue

            cat_counts[category.value] = cat_counts.get(category.value, 0) + 1

            # 3. 생애주기 판별
            first_date, source = self._resolve_first_receiving_date(ctx.item_cd)
            ctx.first_receiving_date = first_date
            ctx.first_receiving_source = source.value  # Enum → str (DB 저장용)

            # SKIP: 첫 입고일 판별 불가
            if source == FirstReceivingSource.NONE:
                logger.info(
                    "[디저트판단][SKIP] %s (%s): 첫 입고일 판별 불가, 판단 보류",
                    ctx.item_cd, ctx.item_nm,
                )
                continue

            lifecycle, weeks = determine_lifecycle(
                first_date, ref_date, category, source,
            )

            # v2w 프로모션 보호: 활성 프로모션 상품은 NEW 유지
            try:
                from src.settings.constants import DESSERT_PROMO_PROTECTION_ENABLED
                if DESSERT_PROMO_PROTECTION_ENABLED and lifecycle != DessertLifecycle.NEW:
                    if self._has_active_promotion(ctx.item_cd):
                        logger.info(
                            "[디저트판단][프로모션보호] %s (%s): 활성 행사 → NEW 유지",
                            ctx.item_cd, ctx.item_nm,
                        )
                        lifecycle = DessertLifecycle.NEW
            except ImportError:
                pass

            # SKIP: 생애주기 판별 불가
            if lifecycle is None:
                logger.info(
                    "[디저트판단][SKIP] %s (%s): 생애주기 판별 불가 (source=%s)",
                    ctx.item_cd, ctx.item_nm, source.value,
                )
                continue

            # 4. 판단 기간 계산
            cycle = CATEGORY_JUDGMENT_CYCLE[category]
            period_start, period_end = self._get_judgment_period(category, ref_date)

            # 5. 판매 집계
            metrics = self._aggregate_metrics(
                ctx.item_cd, category, period_start, period_end, ctx.sell_price, ref_date
            )

            # 6. 판단
            decision, reason, is_warning = judge_item(category, lifecycle, metrics)

            counts[decision.value] = counts.get(decision.value, 0) + 1

            results.append({
                "store_id": self.store_id,
                "item_cd": ctx.item_cd,
                "item_nm": ctx.item_nm,
                "mid_cd": "014",
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

        # 7. 저장
        saved = self.decision_repo.save_decisions_batch(results)
        logger.info(f"[디저트판단] 완료: {saved}건 저장 — "
                     f"KEEP={counts['KEEP']} WATCH={counts['WATCH']} "
                     f"REDUCE={counts['REDUCE_ORDER']} STOP={counts['STOP_RECOMMEND']}")

        # 8. 30일 미판매 STOP_RECOMMEND → 자동 CONFIRMED_STOP
        auto_confirmed = self._auto_confirm_zero_sales(results)

        # 9. 폐기율 150%+ 2주 연속 STOP_RECOMMEND → 자동 CONFIRMED_STOP
        auto_confirmed_waste = self._auto_confirm_high_waste(results)

        # 10. CONFIRMED_STOP 카카오 알림
        all_confirmed = auto_confirmed + auto_confirmed_waste
        if all_confirmed:
            self._notify_confirmed_stop(all_confirmed, results)

        return {
            "total_items": len(results),
            "by_category": cat_counts,
            "decisions": counts,
            "auto_confirmed": len(all_confirmed),
            "results": results,
        }

    # =========================================================================
    # Internal methods
    # =========================================================================

    def _auto_confirm_zero_sales(
        self, results: List[Dict[str, Any]], days: int = 30,
    ) -> List[str]:
        """30일 미판매 STOP_RECOMMEND 상품을 자동 CONFIRMED_STOP 처리.

        Args:
            results: save_decisions_batch()에 전달된 판단 결과 리스트
            days: 미판매 기준 일수 (기본 30일)

        Returns:
            자동 확인된 item_cd 리스트
        """
        stop_items = [
            r["item_cd"] for r in results
            if r["decision"] == "STOP_RECOMMEND"
        ]
        if not stop_items:
            return []

        conn = self.decision_repo._get_conn()
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        # 30일 내 판매 있는 상품 조회 (제외 대상)
        placeholders = ",".join("?" * len(stop_items))
        cursor = conn.execute(f"""
            SELECT DISTINCT item_cd
            FROM daily_sales
            WHERE item_cd IN ({placeholders})
              AND sales_date >= ?
              AND sale_qty > 0
        """, stop_items + [cutoff])
        has_sales = {row[0] for row in cursor.fetchall()}

        # 30일간 판매 0건 = 자동 확인 대상
        zero_sales_items = [ic for ic in stop_items if ic not in has_sales]
        if not zero_sales_items:
            return []

        confirmed = self.decision_repo.batch_update_operator_action(
            item_cds=zero_sales_items,
            action="CONFIRMED_STOP",
            operator_note=f"auto: {days}일 미판매 자동확인",
            store_id=self.store_id,
        )

        confirmed_cds = [c["item_cd"] for c in confirmed]
        if confirmed_cds:
            logger.info(
                f"[디저트판단] 자동확인 {len(confirmed_cds)}건 "
                f"({days}일 미판매 → CONFIRMED_STOP)"
            )
        return confirmed_cds

    def _auto_confirm_high_waste(
        self, results: List[Dict[str, Any]],
    ) -> List[str]:
        """폐기율 150%+ 2주 연속 STOP_RECOMMEND → 자동 CONFIRMED_STOP.

        최근 DESSERT_CONFIRMED_STOP_WASTE_DAYS일 내 주별 폐기율을 검사하여
        DESSERT_CONFIRMED_STOP_WASTE_WEEKS주 연속 150% 이상이면 자동 확인.
        """
        stop_items = [
            r["item_cd"] for r in results
            if r["decision"] == "STOP_RECOMMEND"
        ]
        if not stop_items:
            return []

        conn = DBRouter.get_store_connection(self.store_id)
        ref = datetime.now()
        confirmed_cds = []

        for item_cd in stop_items:
            # 최근 N주 각각의 폐기율 계산
            high_waste_weeks = 0
            for w in range(DESSERT_CONFIRMED_STOP_WASTE_WEEKS):
                end = ref - timedelta(days=7 * w)
                start = end - timedelta(days=7)
                cursor = conn.execute("""
                    SELECT COALESCE(SUM(sale_qty), 0),
                           COALESCE(SUM(disuse_qty), 0)
                    FROM daily_sales
                    WHERE item_cd = ?
                      AND sales_date > ? AND sales_date <= ?
                """, (item_cd, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))
                row = cursor.fetchone()
                sale, disuse = (row[0] or 0), (row[1] or 0)
                wr = calc_waste_rate(sale, disuse)
                if wr >= DESSERT_WASTE_RATE_STOP_THRESHOLD:
                    high_waste_weeks += 1
                else:
                    break

            if high_waste_weeks >= DESSERT_CONFIRMED_STOP_WASTE_WEEKS:
                confirmed_cds.append(item_cd)

        if not confirmed_cds:
            return []

        confirmed = self.decision_repo.batch_update_operator_action(
            item_cds=confirmed_cds,
            action="CONFIRMED_STOP",
            operator_note=f"auto: 폐기율 {DESSERT_WASTE_RATE_STOP_THRESHOLD:.0%} "
                          f"{DESSERT_CONFIRMED_STOP_WASTE_WEEKS}주 연속 자동확인",
            store_id=self.store_id,
        )

        result_cds = [c["item_cd"] for c in confirmed]
        if result_cds:
            logger.info(
                f"[디저트판단] 폐기율 자동확인 {len(result_cds)}건 "
                f"(폐기율 {DESSERT_WASTE_RATE_STOP_THRESHOLD:.0%} "
                f"{DESSERT_CONFIRMED_STOP_WASTE_WEEKS}주 연속 → CONFIRMED_STOP)"
            )
        return result_cds

    def _notify_confirmed_stop(
        self,
        confirmed_cds: List[str],
        results: List[Dict[str, Any]],
    ) -> None:
        """CONFIRMED_STOP 상품 카카오 알림 발송"""
        try:
            from src.notification.kakao_notifier import KakaoNotifier
            notifier = KakaoNotifier()

            item_lines = []
            for item_cd in confirmed_cds[:10]:  # 최대 10개까지만 표시
                r = next((r for r in results if r["item_cd"] == item_cd), None)
                if r:
                    item_lines.append(
                        f"  - {r.get('item_nm', item_cd)} "
                        f"(Cat {r.get('dessert_category', '?')}, "
                        f"사유: {r.get('decision_reason', '?')})"
                    )
                else:
                    item_lines.append(f"  - {item_cd}")

            if len(confirmed_cds) > 10:
                item_lines.append(f"  ... 외 {len(confirmed_cds) - 10}건")

            msg = (
                f"[디저트 발주정지 확정] 매장 {self.store_id}\n"
                f"자동 CONFIRMED_STOP {len(confirmed_cds)}건:\n"
                + "\n".join(item_lines)
            )
            notifier.send_message(msg)
        except Exception as e:
            logger.warning(f"[디저트판단] 카카오 알림 실패: {e}")

    def _load_dessert_items(self) -> List[DessertItemContext]:
        """common.db에서 mid_cd='014' 상품 로드"""
        conn = DBRouter.get_connection(table="products")
        cursor = conn.execute("""
            SELECT p.item_cd, p.item_nm, p.mid_cd,
                   pd.expiration_days, pd.small_nm, pd.sell_price
            FROM products p
            LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
            WHERE p.mid_cd = '014'
            ORDER BY p.item_nm
        """)

        items = []
        for row in cursor.fetchall():
            items.append(DessertItemContext(
                item_cd=row[0],
                item_nm=row[1] or "",
                mid_cd=row[2] or "014",
                small_nm=row[4],
                expiration_days=row[3],
                sell_price=row[5] or 0,
            ))
        return items

    def _resolve_first_receiving_date(
        self, item_cd: str,
    ) -> Tuple[Optional[str], FirstReceivingSource]:
        """5소스 우선순위로 첫 입고일 판별.

        Sources (priority order):
            1.   detected_new_products.first_receiving_date → DETECTED
            2.   MIN(sales_date) WHERE sale_qty > 0         → DAILY_SALES_SOLD
            2-1. MIN(sales_date) WHERE buy_qty > 0          → DAILY_SALES_BOUGHT
            3.   products.created_at (bulk date detection)  → PRODUCTS / PRODUCTS_BULK
            4.   Fallback                                   → (None, NONE)
        """
        conn = DBRouter.get_store_connection(self.store_id)

        # 1순위: detected_new_products
        try:
            cursor = conn.execute(
                "SELECT first_receiving_date FROM detected_new_products "
                "WHERE item_cd = ? ORDER BY first_receiving_date ASC LIMIT 1",
                (item_cd,)
            )
            row = cursor.fetchone()
            if row and row[0]:
                return row[0], FirstReceivingSource.DETECTED
        except Exception:
            pass

        # 2순위: 첫 판매일 (sale_qty > 0)
        try:
            cursor = conn.execute(
                "SELECT MIN(sales_date) FROM daily_sales "
                "WHERE item_cd = ? AND sale_qty > 0",
                (item_cd,)
            )
            row = cursor.fetchone()
            if row and row[0]:
                return row[0], FirstReceivingSource.DAILY_SALES_SOLD
        except Exception:
            pass

        # 2-1순위: 첫 입고일 (buy_qty > 0, 판매 없는 상품만 여기 도달)
        try:
            first_buy = self._query_first_buy_date(item_cd)
            if first_buy:
                return first_buy, FirstReceivingSource.DAILY_SALES_BOUGHT
        except Exception:
            pass

        # 3순위: products.created_at (일괄등록 감지)
        try:
            common_conn = DBRouter.get_connection(table="products")
            cursor = common_conn.execute(
                "SELECT created_at FROM products WHERE item_cd = ?",
                (item_cd,)
            )
            row = cursor.fetchone()
            if row and row[0]:
                date_str = row[0][:10]  # ISO datetime → date
                if date_str in DESSERT_BULK_REGISTRATION_DATES:
                    return date_str, FirstReceivingSource.PRODUCTS_BULK
                return date_str, FirstReceivingSource.PRODUCTS
        except Exception:
            pass

        return None, FirstReceivingSource.NONE

    def _query_first_buy_date(self, item_cd: str) -> Optional[str]:
        """daily_sales에서 buy_qty > 0인 최초 날짜 조회.

        이 메서드는 2순위(sale_qty > 0)에서 잡히지 않은 상품만 대상으로 호출됨.
        즉, "입고는 됐으나 판매가 한 번도 없는 상품"의 첫 입고일 추정용.
        편의점에서는 입고 당일 판매가 흔하므로 이 케이스에 해당하는 상품은 소수.
        """
        conn = DBRouter.get_store_connection(self.store_id)
        cursor = conn.execute(
            "SELECT MIN(sales_date) FROM daily_sales "
            "WHERE item_cd = ? AND mid_cd = '014' AND buy_qty > 0",
            (item_cd,)
        )
        row = cursor.fetchone()
        return row[0] if row and row[0] else None

    def _has_active_promotion(self, item_cd: str) -> bool:
        """promotions 테이블에서 현재 활성 행사가 있는지 확인"""
        try:
            from src.infrastructure.database.connection import DBRouter
            conn = DBRouter.get_store_connection(self.store_id)
            cursor = conn.cursor()
            today = datetime.now().strftime("%Y-%m-%d")
            row = cursor.execute(
                """
                SELECT 1 FROM promotions
                WHERE item_cd = ? AND start_date <= ? AND end_date >= ?
                LIMIT 1
                """,
                (item_cd, today, today),
            ).fetchone()
            return row is not None
        except Exception:
            return False

    def _get_judgment_period(
        self,
        category: DessertCategory,
        reference_date: str,
    ) -> Tuple[str, str]:
        """카테고리별 판단 기간 계산"""
        ref = datetime.strptime(reference_date, "%Y-%m-%d")

        if category == DessertCategory.A:
            days = 7
        elif category == DessertCategory.B:
            days = 14
        else:  # C, D
            days = 30

        start = (ref - timedelta(days=days)).strftime("%Y-%m-%d")
        end = reference_date
        return start, end

    def _aggregate_metrics(
        self,
        item_cd: str,
        category: DessertCategory,
        period_start: str,
        period_end: str,
        sell_price: int,
        reference_date: str,
    ) -> DessertSalesMetrics:
        """daily_sales에서 판단 기간의 판매/폐기 집계"""
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

        # 이전 기간 (추세 계산)
        ref = datetime.strptime(reference_date, "%Y-%m-%d")
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
        prev_row = cursor.fetchone()
        prev_sale = prev_row[0] if prev_row else 0

        trend_pct = 0.0
        if prev_sale > 0:
            trend_pct = round((total_sale - prev_sale) / prev_sale * 100, 1)

        # 카테고리 평균
        cat_avg = self._get_category_avg_sale(category, period_start, period_end)

        # 주별 판매율 (연속 미달 역산) — 최근 8주
        weekly_rates = self._aggregate_weekly_rates(item_cd, reference_date, weeks=8)

        # 월별 판매수량 (Cat D 연속 무판매) — 최근 3개월
        monthly_qtys = self._aggregate_monthly_qtys(item_cd, reference_date, months=3)

        metrics = DessertSalesMetrics(
            period_start=period_start,
            period_end=period_end,
            total_order_qty=total_order,
            total_sale_qty=total_sale,
            total_disuse_qty=total_disuse,
            sale_amount=sale_amount,
            disuse_amount=disuse_amount,
            sale_rate=sale_rate,
            category_avg_sale_qty=cat_avg,
            prev_period_sale_qty=prev_sale,
            sale_trend_pct=trend_pct,
            consecutive_low_weeks=count_consecutive_low_weeks(weekly_rates, 0.5),
            consecutive_zero_months=count_consecutive_zero_months(monthly_qtys),
            weekly_sale_rates=weekly_rates,
        )
        return metrics

    def _get_category_avg_sale(
        self,
        category: DessertCategory,
        period_start: str,
        period_end: str,
    ) -> float:
        """같은 카테고리 상품들의 평균 판매수량"""
        conn = DBRouter.get_store_connection(self.store_id)

        # 먼저 같은 카테고리 상품 목록 필요 → common.db에서 mid_cd='014' 전체 로드
        common_conn = DBRouter.get_connection(table="products")
        cursor = common_conn.execute("""
            SELECT p.item_cd, pd.small_nm, pd.expiration_days
            FROM products p
            LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
            WHERE p.mid_cd = '014'
        """)

        same_cat_items = []
        for row in cursor.fetchall():
            cat = classify_dessert_category(row[1], row[2])
            if cat == category:
                same_cat_items.append(row[0])

        if not same_cat_items:
            return 0.0

        # 같은 카테고리 상품들의 기간 내 판매합계
        placeholders = ",".join("?" * len(same_cat_items))
        cursor = conn.execute(f"""
            SELECT COALESCE(SUM(sale_qty), 0), COUNT(DISTINCT item_cd)
            FROM daily_sales
            WHERE item_cd IN ({placeholders})
              AND sales_date >= ? AND sales_date <= ?
        """, same_cat_items + [period_start, period_end])

        row = cursor.fetchone()
        total = row[0] if row else 0
        item_count = row[1] if row else 0

        if item_count == 0:
            return 0.0
        return round(total / item_count, 2)

    def _aggregate_weekly_rates(
        self,
        item_cd: str,
        reference_date: str,
        weeks: int = 8,
    ) -> List[float]:
        """최근 N주 각각의 판매율 (인덱스 0 = 가장 최신 주)"""
        conn = DBRouter.get_store_connection(self.store_id)
        ref = datetime.strptime(reference_date, "%Y-%m-%d")
        rates = []

        for w in range(weeks):
            end = ref - timedelta(days=7 * w)
            start = end - timedelta(days=7)

            cursor = conn.execute("""
                SELECT COALESCE(SUM(sale_qty), 0),
                       COALESCE(SUM(disuse_qty), 0)
                FROM daily_sales
                WHERE item_cd = ?
                  AND sales_date > ? AND sales_date <= ?
            """, (item_cd, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))

            row = cursor.fetchone()
            sale = row[0] if row else 0
            disuse = row[1] if row else 0
            rates.append(calc_sale_rate(sale, disuse))

        return rates

    def _aggregate_monthly_qtys(
        self,
        item_cd: str,
        reference_date: str,
        months: int = 3,
    ) -> List[int]:
        """최근 N개월 각각의 판매수량 (인덱스 0 = 가장 최신 월)"""
        conn = DBRouter.get_store_connection(self.store_id)
        ref = datetime.strptime(reference_date, "%Y-%m-%d")
        qtys = []

        for m in range(months):
            end = ref - timedelta(days=30 * m)
            start = end - timedelta(days=30)

            cursor = conn.execute("""
                SELECT COALESCE(SUM(sale_qty), 0)
                FROM daily_sales
                WHERE item_cd = ?
                  AND sales_date > ? AND sales_date <= ?
            """, (item_cd, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))

            row = cursor.fetchone()
            qtys.append(row[0] if row else 0)

        return qtys
