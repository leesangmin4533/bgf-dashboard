"""
NewProductMonitor — 신제품 라이프사이클 모니터링 서비스

Phase 1.35에서 실행. 신제품 일별 판매/재고/발주 추적 및 상태 전환 관리.

상태 전환:
    detected → monitoring (첫 추적 시)
    monitoring → stable (14일 + sold_days >= 3)
    monitoring → no_demand (14일 + sold_days == 0)
    monitoring → slow_start (14일 + 0 < sold_days < 3)
    stable → normal (monitoring_start + 30일 경과)
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional
import statistics

from src.infrastructure.database.repos import (
    DetectedNewProductRepository,
    NewProductDailyTrackingRepository,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


class NewProductMonitor:
    """신제품 라이프사이클 모니터링 서비스"""

    MONITORING_DAYS = 14
    STABLE_THRESHOLD_DAYS = 3
    NORMAL_DAYS_AFTER_STABLE = 30

    def __init__(self, store_id: str):
        self.store_id = store_id
        self.detect_repo = DetectedNewProductRepository(store_id=store_id)
        self.tracking_repo = NewProductDailyTrackingRepository(store_id=store_id)

    def run(self) -> Dict:
        """일일 모니터링 실행 (Phase 1.35)

        Returns:
            stats: {
                "active_items": int,
                "tracking_saved": int,
                "status_changes": List[Dict],
                "settlement_results": List[Dict],
            }
        """
        today = datetime.now().strftime("%Y-%m-%d")

        items = self.get_active_monitoring_items()
        if not items:
            return {
                "active_items": 0, "tracking_saved": 0,
                "status_changes": [], "settlement_results": [],
            }

        tracking_saved = self.collect_daily_tracking(items, today)
        status_changes = self.update_lifecycle_status(items, today)

        # 안착 판정 (Phase 3 트리거)
        settlement_results = self._run_settlement()

        return {
            "active_items": len(items),
            "tracking_saved": tracking_saved,
            "status_changes": status_changes,
            "settlement_results": settlement_results,
        }

    def get_active_monitoring_items(self) -> List[Dict]:
        """모니터링 대상 상품 조회 (detected + monitoring + stable 상태)"""
        return self.detect_repo.get_by_lifecycle_status(
            ["detected", "monitoring", "stable"], store_id=self.store_id
        )

    def collect_daily_tracking(self, items: List[Dict], today: str) -> int:
        """일별 판매/재고/발주 데이터 수집 및 저장

        Sources:
            - daily_sales: sale_qty (해당일 판매수량)
            - realtime_inventory: stock_qty (현재 재고)
            - auto_order_items: order_qty (해당일 발주수량)
        """
        sales_map = self._get_daily_sales(today)
        stock_map = self._get_stock_map()
        order_map = self._get_order_map(today)

        saved = 0
        for item in items:
            item_cd = item["item_cd"]
            sales_qty = sales_map.get(item_cd, 0)
            stock_qty = stock_map.get(item_cd, 0)
            order_qty = order_map.get(item_cd, 0)

            self.tracking_repo.save(
                item_cd=item_cd,
                tracking_date=today,
                sales_qty=sales_qty,
                stock_qty=stock_qty,
                order_qty=order_qty,
                store_id=self.store_id,
            )
            saved += 1

        return saved

    def update_lifecycle_status(self, items: List[Dict], today: str) -> List[Dict]:
        """상태 전환 로직 실행"""
        changes = []

        for item in items:
            item_cd = item["item_cd"]
            current_status = item.get("lifecycle_status", "detected")
            monitoring_start = item.get("monitoring_start_date")

            if current_status == "detected":
                self.detect_repo.update_lifecycle(
                    item_cd=item_cd,
                    status="monitoring",
                    monitoring_start=today,
                    store_id=self.store_id,
                )
                changes.append({
                    "item_cd": item_cd,
                    "from": "detected",
                    "to": "monitoring",
                })
                logger.info(f"[신제품모니터] {item_cd}: detected -> monitoring")

            elif current_status == "monitoring" and monitoring_start:
                start_dt = datetime.strptime(monitoring_start, "%Y-%m-%d")
                elapsed = (datetime.strptime(today, "%Y-%m-%d") - start_dt).days

                if elapsed >= self.MONITORING_DAYS:
                    sold_days = self.tracking_repo.get_sold_days_count(
                        item_cd, store_id=self.store_id
                    )
                    total_sold = self.tracking_repo.get_total_sold_qty(
                        item_cd, store_id=self.store_id
                    )

                    if sold_days >= self.STABLE_THRESHOLD_DAYS:
                        new_status = "stable"
                        small_cd = self._get_small_cd(item_cd)
                        similar_avg = self.calculate_similar_avg(
                            item_cd, item.get("mid_cd", "999"),
                            small_cd=small_cd,
                        )
                    elif sold_days == 0:
                        new_status = "no_demand"
                        similar_avg = None
                    else:
                        new_status = "slow_start"
                        similar_avg = None

                    monitoring_end = today
                    self.detect_repo.update_lifecycle(
                        item_cd=item_cd,
                        status=new_status,
                        monitoring_end=monitoring_end,
                        total_sold_qty=total_sold,
                        sold_days=sold_days,
                        similar_item_avg=similar_avg,
                        store_id=self.store_id,
                    )
                    changes.append({
                        "item_cd": item_cd,
                        "from": "monitoring",
                        "to": new_status,
                        "sold_days": sold_days,
                        "total_sold": total_sold,
                    })
                    logger.info(
                        f"[신제품모니터] {item_cd}: monitoring -> {new_status} "
                        f"(sold_days={sold_days}, total={total_sold})"
                    )

            elif current_status == "stable" and monitoring_start:
                start_dt = datetime.strptime(monitoring_start, "%Y-%m-%d")
                elapsed = (datetime.strptime(today, "%Y-%m-%d") - start_dt).days

                if elapsed >= self.NORMAL_DAYS_AFTER_STABLE:
                    self.detect_repo.update_lifecycle(
                        item_cd=item_cd,
                        status="normal",
                        store_id=self.store_id,
                    )
                    changes.append({
                        "item_cd": item_cd,
                        "from": "stable",
                        "to": "normal",
                    })
                    logger.info(f"[신제품모니터] {item_cd}: stable -> normal")

        return changes

    # 소분류 기반 유사상품 매칭 최소 상품 수
    SMALL_CD_MIN_SIMILAR = 3

    def calculate_similar_avg(
        self, item_cd: str, mid_cd: str, small_cd: Optional[str] = None
    ) -> Optional[float]:
        """유사 상품 일평균 판매량 계산 (small_cd 우선, mid_cd 폴백)

        Logic:
            1. small_cd가 유효하면 같은 mid_cd+small_cd 상품 우선 조회
            2. small_cd 내 상품 수 >= SMALL_CD_MIN_SIMILAR(3) → 중위값 반환
            3. 부족하면 mid_cd 전체로 폴백
            4. mid_cd가 '999' 또는 유사 상품 없으면 None
        """
        if mid_cd == "999":
            return None

        conn = None
        try:
            from src.infrastructure.database.connection import DBRouter
            conn = DBRouter.get_store_connection(self.store_id)
            from src.infrastructure.database.connection import attach_common_with_views
            conn = attach_common_with_views(conn, self.store_id)

            cursor = conn.cursor()

            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

            # 1단계: small_cd 기반 조회 시도
            if small_cd and small_cd.strip():
                result = self._query_similar_avg_by_smallcd(
                    cursor, start_date, end_date, mid_cd, small_cd, item_cd
                )
                if result is not None:
                    logger.info(
                        f"[유사상품] {item_cd}: small_cd={small_cd} 기반 "
                        f"매칭 (avg={result:.2f})"
                    )
                    return result
                logger.debug(
                    f"[유사상품] {item_cd}: small_cd={small_cd} 부족 "
                    f"(< {self.SMALL_CD_MIN_SIMILAR}개), mid_cd 폴백"
                )

            # 2단계: mid_cd 전체 폴백
            return self._query_similar_avg_by_midcd(
                cursor, start_date, end_date, mid_cd, item_cd
            )

        except Exception as e:
            logger.warning(f"유사 상품 평균 계산 실패 ({item_cd}, {mid_cd}): {e}")
            return None
        finally:
            if conn:
                conn.close()

    def _query_similar_avg_by_smallcd(
        self, cursor, start_date: str, end_date: str,
        mid_cd: str, small_cd: str, item_cd: str
    ) -> Optional[float]:
        """small_cd 기반 유사상품 중위값 조회

        같은 mid_cd + small_cd 상품 중 판매 실적이 있는 상품의 일평균 중위값.
        유사상품 수가 SMALL_CD_MIN_SIMILAR 미만이면 None 반환 (폴백 유도).
        """
        cursor.execute(
            """
            SELECT p.item_cd,
                   COALESCE(SUM(ds.sale_qty), 0) as total_sales,
                   COUNT(DISTINCT ds.sales_date) as data_days
            FROM common.products p
            JOIN common.product_details pd ON p.item_cd = pd.item_cd
            LEFT JOIN daily_sales ds
                ON p.item_cd = ds.item_cd
                AND ds.sales_date BETWEEN ? AND ?
            WHERE p.mid_cd = ?
              AND pd.small_cd = ?
              AND p.item_cd != ?
            GROUP BY p.item_cd
            HAVING data_days > 0
            """,
            (start_date, end_date, mid_cd, small_cd, item_cd),
        )
        rows = cursor.fetchall()

        if not rows or len(rows) < self.SMALL_CD_MIN_SIMILAR:
            return None

        daily_avgs = []
        for row in rows:
            total = row["total_sales"]
            days = row["data_days"]
            if days > 0:
                daily_avgs.append(total / days)

        if not daily_avgs or len(daily_avgs) < self.SMALL_CD_MIN_SIMILAR:
            return None

        return round(statistics.median(daily_avgs), 2)

    def _query_similar_avg_by_midcd(
        self, cursor, start_date: str, end_date: str,
        mid_cd: str, item_cd: str
    ) -> Optional[float]:
        """mid_cd 전체 기반 유사상품 중위값 조회 (기존 로직)"""
        cursor.execute(
            """
            SELECT p.item_cd,
                   COALESCE(SUM(ds.sale_qty), 0) as total_sales,
                   COUNT(DISTINCT ds.sales_date) as data_days
            FROM common.products p
            LEFT JOIN daily_sales ds
                ON p.item_cd = ds.item_cd
                AND ds.sales_date BETWEEN ? AND ?
            WHERE p.mid_cd = ?
              AND p.item_cd != ?
            GROUP BY p.item_cd
            HAVING data_days > 0
            """,
            (start_date, end_date, mid_cd, item_cd),
        )
        rows = cursor.fetchall()

        if not rows:
            return None

        daily_avgs = []
        for row in rows:
            total = row["total_sales"]
            days = row["data_days"]
            if days > 0:
                daily_avgs.append(total / days)

        if not daily_avgs:
            return None

        return round(statistics.median(daily_avgs), 2)

    def _get_small_cd(self, item_cd: str) -> Optional[str]:
        """상품의 소분류 코드 조회 (product_details.small_cd)

        common.db의 product_details에서 해당 item_cd의 small_cd를 반환.
        미등록이면 None.
        """
        conn = None
        try:
            from src.infrastructure.database.connection import DBRouter
            conn = DBRouter.get_common_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT small_cd FROM product_details WHERE item_cd = ?",
                (item_cd,),
            )
            row = cursor.fetchone()
            if row and row["small_cd"]:
                return row["small_cd"]
            return None
        except Exception as e:
            logger.debug(f"small_cd 조회 실패 ({item_cd}): {e}")
            return None
        finally:
            if conn:
                conn.close()

    # ── settlement 연동 (v56) ────────────────────────────

    def _run_settlement(self) -> list:
        """판정 기한 도달한 제품의 안착 판정 실행"""
        try:
            from src.application.services.new_product_settlement_service import (
                NewProductSettlementService,
            )
            service = NewProductSettlementService(store_id=self.store_id)
            results = service.run_due_settlements()

            output = []
            for r in results:
                logger.info(
                    f"[안착판정] {r.item_cd} → {r.verdict} "
                    f"(점수:{r.score:.0f} velocity:{r.velocity_ratio:.2f} "
                    f"CV:{r.cv:.0f}% expiry:{r.expiry_risk})"
                    if r.cv is not None else
                    f"[안착판정] {r.item_cd} → {r.verdict} "
                    f"(점수:{r.score:.0f} velocity:{r.velocity_ratio:.2f} "
                    f"CV:N/A expiry:{r.expiry_risk})"
                )
                output.append({
                    "item_cd": r.item_cd,
                    "item_nm": r.item_nm,
                    "verdict": r.verdict,
                    "score": r.score,
                })

            return output
        except Exception as e:
            logger.warning(f"[안착판정] 실행 실패 (무시): {e}")
            return []

    # ── private helpers ──────────────────────────────────

    def _get_daily_sales(self, date: str) -> Dict[str, int]:
        """해당일 판매수량 배치 조회"""
        conn = None
        try:
            from src.infrastructure.database.connection import DBRouter
            conn = DBRouter.get_store_connection(self.store_id)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT item_cd, sale_qty FROM daily_sales WHERE sales_date = ?",
                (date,),
            )
            return {row["item_cd"]: row["sale_qty"] for row in cursor.fetchall()}
        except Exception:
            return {}
        finally:
            if conn:
                conn.close()

    def _get_stock_map(self) -> Dict[str, int]:
        """현재 재고 배치 조회"""
        conn = None
        try:
            from src.infrastructure.database.connection import DBRouter
            conn = DBRouter.get_store_connection(self.store_id)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT item_cd, stock_qty FROM realtime_inventory WHERE is_available = 1"
            )
            return {row["item_cd"]: row["stock_qty"] for row in cursor.fetchall()}
        except Exception:
            return {}
        finally:
            if conn:
                conn.close()

    def _get_order_map(self, date: str) -> Dict[str, int]:
        """해당일 발주수량 배치 조회"""
        conn = None
        try:
            from src.infrastructure.database.connection import DBRouter
            conn = DBRouter.get_store_connection(self.store_id)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT item_cd, SUM(order_qty) as order_qty FROM order_tracking WHERE order_date = ? GROUP BY item_cd",
                (date,),
            )
            return {row["item_cd"]: row["order_qty"] for row in cursor.fetchall()}
        except Exception:
            return {}
        finally:
            if conn:
                conn.close()
