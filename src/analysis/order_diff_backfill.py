"""
과거 데이터 백필 — eval_outcomes + order_tracking → snapshot 재구성 → diff 생성

운영 DB(stores/*.db)에 이미 저장된 과거 데이터를 사용하여
order_analysis.db에 스냅샷과 차이(diff)를 소급 생성한다.

데이터 소스:
  - eval_outcomes: predicted_qty, actual_order_qty, decision, current_stock, pending_qty
  - order_tracking: order_qty, item_nm, mid_cd (eval_outcomes에 없는 필드 보충)
  - receiving_history: order_date 컬럼으로 매칭, order_qty(확정), receiving_qty(실입고)

Usage:
    backfiller = OrderDiffBackfiller(store_id="46513")
    result = backfiller.backfill_range("2026-01-26", "2026-02-17")
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)


class OrderDiffBackfiller:
    """과거 데이터 백필"""

    def __init__(self, store_id: str):
        self.store_id = store_id
        self._analysis_repo = None

    @property
    def analysis_repo(self):
        if self._analysis_repo is None:
            from src.infrastructure.database.repos.order_analysis_repo import (
                OrderAnalysisRepository,
            )
            self._analysis_repo = OrderAnalysisRepository()
        return self._analysis_repo

    def _get_store_connection(self):
        """매장 DB 커넥션 (order_tracking, receiving_history 직접 SQL용)

        common.db를 ATTACH하여 products 등 공통 테이블 접근 가능하게 한다.
        """
        from src.infrastructure.database.connection import (
            DBRouter, attach_common_with_views,
        )
        conn = DBRouter.get_store_connection(self.store_id)
        attach_common_with_views(conn, self.store_id)
        return conn

    # ── 메인 API ─────────────────────────────────────────

    def backfill_date(self, order_date: str) -> Optional[Dict[str, Any]]:
        """특정 날짜 백필

        Args:
            order_date: 발주일 (YYYY-MM-DD)

        Returns:
            비교 결과 {diffs, summary, unchanged_count, snapshot_count} 또는 None
        """
        try:
            # 1) eval_outcomes 조회 (직접 SQL — ATTACH된 products JOIN 보장)
            eval_data = self._query_eval_outcomes(order_date)

            # 2) order_tracking 조회
            tracking_data = self._query_order_tracking(order_date)

            if not eval_data and not tracking_data:
                logger.debug(f"[백필] {order_date}: eval/tracking 데이터 없음 → 건너뜀")
                return None

            # 3) snapshot 재구성
            snapshot_items = self._build_snapshot_from_history(
                eval_data, tracking_data
            )

            if not snapshot_items:
                logger.debug(f"[백필] {order_date}: 유효 스냅샷 0건 → 건너뜀")
                return None

            # 4) snapshot 저장
            snapshot_count = self.analysis_repo.save_order_snapshot(
                store_id=self.store_id,
                order_date=order_date,
                items=snapshot_items,
            )

            # 5) receiving_history 조회 (order_date로)
            receiving_data = self._query_receiving_by_order_date(order_date)

            # 6) 비교
            from src.analysis.order_diff_analyzer import OrderDiffAnalyzer

            # receiving_date 결정
            receiving_date = ""
            for r in receiving_data:
                rd = r.get("receiving_date")
                if rd:
                    receiving_date = rd
                    break

            result = OrderDiffAnalyzer.compare(
                auto_snapshot=snapshot_items,
                receiving_data=receiving_data,
                store_id=self.store_id,
                order_date=order_date,
                receiving_date=receiving_date,
            )

            # 7) diff + summary 저장
            if result.get("diffs"):
                self.analysis_repo.save_diffs(result["diffs"])
            if result.get("summary"):
                self.analysis_repo.save_summary(result["summary"])

            result["snapshot_count"] = snapshot_count
            return result

        except Exception as e:
            logger.warning(f"[백필] {order_date} 실패: {e}")
            return None

    def backfill_range(
        self, start_date: str, end_date: str
    ) -> Dict[str, Any]:
        """기간 백필

        Args:
            start_date: 시작일 (YYYY-MM-DD)
            end_date: 종료일 (YYYY-MM-DD)

        Returns:
            {
                'total_dates': int,
                'processed': int,
                'skipped': int,
                'total_snapshots': int,
                'total_diffs': int,
                'results_by_date': {date: summary_dict}
            }
        """
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        total_dates = 0
        processed = 0
        skipped = 0
        total_snapshots = 0
        total_diffs = 0
        results_by_date = {}

        current = start
        while current <= end:
            order_date = current.strftime("%Y-%m-%d")
            total_dates += 1

            result = self.backfill_date(order_date)

            if result:
                processed += 1
                sc = result.get("snapshot_count", 0)
                dc = len(result.get("diffs", []))
                total_snapshots += sc
                total_diffs += dc
                results_by_date[order_date] = result.get("summary", {})

                summary = result.get("summary", {})
                logger.info(
                    f"[백필] {order_date}: "
                    f"스냅샷={sc}, "
                    f"변경={summary.get('items_qty_changed', 0)}, "
                    f"추가={summary.get('items_added', 0)}, "
                    f"제거={summary.get('items_removed', 0)}, "
                    f"일치율={summary.get('match_rate', 0):.1%}"
                )
            else:
                skipped += 1

            current += timedelta(days=1)

        return {
            "total_dates": total_dates,
            "processed": processed,
            "skipped": skipped,
            "total_snapshots": total_snapshots,
            "total_diffs": total_diffs,
            "results_by_date": results_by_date,
        }

    def get_available_date_range(self) -> Dict[str, Optional[str]]:
        """백필 가능한 날짜 범위 조회

        Returns:
            {'eval_min': date, 'eval_max': date,
             'tracking_min': date, 'tracking_max': date,
             'receiving_min': date, 'receiving_max': date}
        """
        conn = self._get_store_connection()
        try:
            result = {}

            # eval_outcomes
            cursor = conn.execute(
                """SELECT MIN(eval_date) as min_d, MAX(eval_date) as max_d
                FROM eval_outcomes
                WHERE store_id = ? AND predicted_qty IS NOT NULL""",
                (self.store_id,),
            )
            row = cursor.fetchone()
            result["eval_min"] = row["min_d"] if row else None
            result["eval_max"] = row["max_d"] if row else None

            # order_tracking
            cursor = conn.execute(
                """SELECT MIN(order_date) as min_d, MAX(order_date) as max_d
                FROM order_tracking
                WHERE store_id = ?""",
                (self.store_id,),
            )
            row = cursor.fetchone()
            result["tracking_min"] = row["min_d"] if row else None
            result["tracking_max"] = row["max_d"] if row else None

            # receiving_history
            cursor = conn.execute(
                """SELECT MIN(order_date) as min_d, MAX(order_date) as max_d
                FROM receiving_history
                WHERE store_id = ? AND order_date IS NOT NULL
                AND order_date != ''""",
                (self.store_id,),
            )
            row = cursor.fetchone()
            result["receiving_min"] = row["min_d"] if row else None
            result["receiving_max"] = row["max_d"] if row else None

            return result
        finally:
            conn.close()

    # ── 내부 메서드 ──────────────────────────────────────

    def _query_eval_outcomes(self, order_date: str) -> List[Dict[str, Any]]:
        """eval_outcomes에서 특정 날짜의 평가 결과 조회 (products JOIN 포함)"""
        conn = self._get_store_connection()
        try:
            cursor = conn.execute(
                """SELECT eo.*, p.item_nm
                FROM eval_outcomes eo
                LEFT JOIN products p ON eo.item_cd = p.item_cd
                WHERE eo.eval_date = ? AND eo.store_id = ?
                ORDER BY eo.decision, eo.item_cd""",
                (order_date, self.store_id),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def _query_order_tracking(self, order_date: str) -> List[Dict[str, Any]]:
        """order_tracking에서 특정 발주일 데이터 조회"""
        conn = self._get_store_connection()
        try:
            cursor = conn.execute(
                """SELECT item_cd, item_nm, mid_cd, order_qty,
                    delivery_type, order_source, status,
                    actual_receiving_qty
                FROM order_tracking
                WHERE order_date = ? AND store_id = ?""",
                (order_date, self.store_id),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def _query_receiving_by_order_date(
        self, order_date: str
    ) -> List[Dict[str, Any]]:
        """receiving_history를 order_date로 조회 (커스텀 SQL)"""
        conn = self._get_store_connection()
        try:
            cursor = conn.execute(
                """SELECT item_cd, item_nm, mid_cd,
                    order_date, receiving_date,
                    order_qty, receiving_qty,
                    delivery_type, chit_no
                FROM receiving_history
                WHERE order_date = ? AND store_id = ?""",
                (order_date, self.store_id),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def _build_snapshot_from_history(
        self,
        eval_data: List[Dict[str, Any]],
        tracking_data: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """eval_outcomes + order_tracking → snapshot 레코드 생성

        - eval_outcomes: 예측/평가 정보 (predicted_qty, decision, current_stock 등)
        - order_tracking: 실제 발주 정보 (order_qty, item_nm, mid_cd)
        - item_cd로 JOIN, order_tracking의 order_qty를 final_order_qty로 우선 사용
        """
        from src.alert.config import ALERT_CATEGORIES
        from src.alert.delivery_utils import get_delivery_type

        # eval_outcomes 인덱싱
        eval_map: Dict[str, Dict] = {}
        for e in eval_data:
            ic = e.get("item_cd")
            if ic and e.get("predicted_qty") is not None:
                eval_map[ic] = e

        # order_tracking 인덱싱
        tracking_map: Dict[str, Dict] = {}
        for t in tracking_data:
            ic = t.get("item_cd")
            if ic:
                tracking_map[ic] = t

        # 양쪽 item_cd 합집합
        all_items = set(eval_map.keys()) | set(tracking_map.keys())

        snapshot_items: List[Dict[str, Any]] = []
        for ic in sorted(all_items):
            ev = eval_map.get(ic, {})
            tr = tracking_map.get(ic, {})

            # final_order_qty: order_tracking 우선, 없으면 eval의 actual_order_qty
            final_qty = tr.get("order_qty") or ev.get("actual_order_qty") or 0
            if final_qty <= 0:
                # 발주량 0이면 건너뜀 (SKIP/PASS 등)
                continue

            item_nm = tr.get("item_nm") or ev.get("item_nm") or ""
            mid_cd = tr.get("mid_cd") or ev.get("mid_cd") or ""

            # delivery_type: tracking 우선, 없으면 mid_cd 기반 추론
            delivery_type = tr.get("delivery_type", "")
            if not delivery_type:
                if mid_cd in ALERT_CATEGORIES:
                    delivery_type = get_delivery_type(item_nm) or "1차"
                else:
                    delivery_type = "일반"

            snapshot_items.append({
                "item_cd": ic,
                "item_nm": item_nm,
                "mid_cd": mid_cd,
                "predicted_qty": ev.get("predicted_qty", 0),
                "recommended_qty": 0,  # 과거 데이터에 없음
                "final_order_qty": final_qty,
                "current_stock": ev.get("current_stock", 0),
                "pending_qty": ev.get("pending_qty", 0),
                "eval_decision": ev.get("decision"),
                "order_unit_qty": 1,  # 과거 데이터에 없음
                "order_success": 1 if ev.get("order_status") == "success"
                    or tr.get("order_qty", 0) > 0 else 0,
                "confidence": "",
                "delivery_type": delivery_type,
                "data_days": None,
            })

        return snapshot_items
