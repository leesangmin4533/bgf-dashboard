"""
폐기전표 -> daily_sales 동기화 서비스 (WasteDisuseSyncService)

waste_slip_items(공식 폐기전표)의 품목별 폐기 수량을
daily_sales.disuse_qty에 동기화한다.

배경:
    - daily_sales.disuse_qty는 매출분석 화면(DISUSE_QTY)에서 수집되지만 약 16%만 반영
    - waste_slip_items는 검수전표 > 통합 전표 조회에서 수집되며 정확한 데이터
    - 기존 WasteVerificationService는 비교/로그만 수행, 동기화 없음
    - 본 서비스가 Phase 1.16에서 실행되어 갭을 해소

동기화 규칙:
    A) daily_sales 행 있고 disuse_qty < slip_qty -> UPDATE (전표가 더 정확)
    B) daily_sales 행 있고 disuse_qty >= slip_qty -> SKIP
    C) daily_sales 행 없음 -> INSERT (폐기만 있고 판매 없는 상품)
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.infrastructure.database.repos import SalesRepository
from src.infrastructure.database.repos.inventory_repo import RealtimeInventoryRepository
from src.infrastructure.database.repos.waste_slip_repo import WasteSlipRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class WasteDisuseSyncService:
    """waste_slip_items -> daily_sales.disuse_qty 동기화 서비스"""

    def __init__(self, store_id: Optional[str] = None) -> None:
        self.store_id = store_id
        self.slip_repo = WasteSlipRepository(store_id=store_id)
        self.sales_repo = SalesRepository(store_id=store_id)
        self.inventory_repo = RealtimeInventoryRepository(store_id=store_id)

    def sync_date(self, target_date: str) -> Dict[str, Any]:
        """특정 날짜의 폐기전표 데이터를 daily_sales에 동기화

        1. waste_slip_items에서 item_cd별 합산 qty 조회
        2. 각 item_cd에 대해 daily_sales.disuse_qty 업데이트/삽입
        3. 통계 반환

        Args:
            target_date: 대상 날짜 (YYYY-MM-DD)

        Returns:
            {
                "date": str,
                "updated": int,   # 기존 행 업데이트
                "inserted": int,  # 신규 행 삽입 (폐기만 있는 상품)
                "skipped": int,   # 이미 정확 (skip)
                "total": int,     # 전체 처리 건수
            }
        """
        stats = {
            "date": target_date,
            "updated": 0,
            "inserted": 0,
            "skipped": 0,
            "total": 0,
            "stock_decremented": 0,
        }

        # 1. waste_slip_items에서 item_cd별 합산
        try:
            items_summary = self.slip_repo.get_waste_slip_items_summary(
                target_date, store_id=self.store_id
            )
        except Exception as e:
            logger.warning(f"[폐기동기화] slip_items 조회 실패 ({target_date}): {e}")
            return stats

        if not items_summary:
            logger.debug(f"[폐기동기화] {target_date}: waste_slip_items 없음")
            return stats

        # 2. 각 item_cd 동기화
        for item in items_summary:
            item_cd = item.get("item_cd", "")
            total_qty = item.get("total_qty", 0) or 0
            mid_cd = self._extract_mid_cd(item)

            if not item_cd or total_qty <= 0:
                continue

            stats["total"] += 1

            try:
                result = self.sales_repo.update_disuse_qty_from_slip(
                    sales_date=target_date,
                    item_cd=item_cd,
                    slip_qty=total_qty,
                    mid_cd=mid_cd,
                    store_id=self.store_id,
                )
                stats[result] = stats.get(result, 0) + 1

                # realtime_inventory 재고 차감 (유령 재고 방지)
                try:
                    decremented = self.inventory_repo.decrement_stock(
                        item_cd=item_cd,
                        qty=total_qty,
                        store_id=self.store_id or "",
                    )
                    if decremented:
                        stats["stock_decremented"] += 1
                except Exception as dec_e:
                    logger.debug(f"[폐기동기화] 재고 차감 실패 ({item_cd}): {dec_e}")
            except Exception as e:
                logger.debug(
                    f"[폐기동기화] 개별 동기화 실패 "
                    f"({target_date}, {item_cd}): {e}"
                )

        if stats["updated"] > 0 or stats["inserted"] > 0:
            logger.info(
                f"[폐기동기화] {target_date}: "
                f"updated={stats['updated']}, inserted={stats['inserted']}, "
                f"skipped={stats['skipped']}, stock_dec={stats['stock_decremented']} "
                f"(총 {stats['total']}건)"
            )
        else:
            logger.debug(
                f"[폐기동기화] {target_date}: 변경 없음 "
                f"(skipped={stats['skipped']}/{stats['total']})"
            )

        return stats

    def sync_date_range(
        self, from_date: str, to_date: str
    ) -> Dict[str, Any]:
        """날짜 범위 동기화 (과거 누락분 일괄 보정용)

        Args:
            from_date: 시작일 (YYYY-MM-DD)
            to_date: 종료일 (YYYY-MM-DD, 포함)

        Returns:
            {
                "from_date", "to_date", "days_processed",
                "total_updated", "total_inserted", "total_skipped",
                "daily_stats": [...]
            }
        """
        total_stats = {
            "from_date": from_date,
            "to_date": to_date,
            "days_processed": 0,
            "total_updated": 0,
            "total_inserted": 0,
            "total_skipped": 0,
            "daily_stats": [],
        }

        start = datetime.strptime(from_date, "%Y-%m-%d")
        end = datetime.strptime(to_date, "%Y-%m-%d")
        current = start

        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            day_stats = self.sync_date(date_str)

            total_stats["days_processed"] += 1
            total_stats["total_updated"] += day_stats["updated"]
            total_stats["total_inserted"] += day_stats["inserted"]
            total_stats["total_skipped"] += day_stats["skipped"]
            total_stats["daily_stats"].append(day_stats)

            current += timedelta(days=1)

        logger.info(
            f"[폐기동기화] 범위 완료 ({from_date} ~ {to_date}): "
            f"{total_stats['days_processed']}일, "
            f"updated={total_stats['total_updated']}, "
            f"inserted={total_stats['total_inserted']}, "
            f"skipped={total_stats['total_skipped']}"
        )

        return total_stats

    def backfill(self, days: int = 30) -> Dict[str, Any]:
        """최근 N일 과거 데이터 일괄 동기화

        Args:
            days: 백필 기간 (기본 30일)

        Returns:
            sync_date_range 결과
        """
        today = datetime.now()
        from_date = (today - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")

        logger.info(f"[폐기동기화] 백필 시작: 최근 {days}일 ({from_date} ~ {to_date})")
        return self.sync_date_range(from_date, to_date)

    def _extract_mid_cd(self, item: Dict[str, Any]) -> str:
        """waste_slip_items summary에서 mid_cd 추출

        waste_slip_items에는 large_cd/large_nm만 있고 mid_cd가 없으므로
        products(common.db) 테이블에서 item_cd 기반으로 mid_cd를 조회한다.
        """
        mid_cd = item.get("mid_cd", "") or ""
        if mid_cd:
            return mid_cd

        item_cd = item.get("item_cd", "")
        if not item_cd:
            return ""

        try:
            from src.infrastructure.database.connection import DBRouter

            conn = DBRouter.get_common_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT mid_cd FROM products WHERE item_cd = ?",
                    (item_cd,),
                )
                row = cur.fetchone()
                return row[0] if row and row[0] else ""
            finally:
                conn.close()
        except Exception:
            return ""
