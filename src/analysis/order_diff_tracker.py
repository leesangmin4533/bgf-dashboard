"""
발주 차이 추적 오케스트레이터

자동발주 스냅샷 저장과 입고 데이터 비교를 연결한다.
모든 메서드는 내부에서 예외를 catch하여 메인 플로우를 방해하지 않는다.

Usage:
    tracker = OrderDiffTracker(store_id="46513")

    # 자동발주 직후
    tracker.save_snapshot(order_date, order_list, results, eval_results)

    # 센터매입 수집 직후
    tracker.compare_and_save(order_date, receiving_data)
"""

from typing import Any, Dict, List, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)


class OrderDiffTracker:
    """발주 차이 추적 오케스트레이터"""

    def __init__(self, store_id: str):
        self.store_id = store_id
        # lazy init — import 시점이 아닌 사용 시점에 생성
        self._repo = None
        self._analyzer = None

    @property
    def repo(self):
        if self._repo is None:
            from src.infrastructure.database.repos.order_analysis_repo import (
                OrderAnalysisRepository,
            )
            self._repo = OrderAnalysisRepository()
        return self._repo

    @property
    def analyzer(self):
        if self._analyzer is None:
            from src.analysis.order_diff_analyzer import OrderDiffAnalyzer
            self._analyzer = OrderDiffAnalyzer()
        return self._analyzer

    def save_snapshot(
        self,
        order_date: str,
        order_list: List[Dict[str, Any]],
        results: List[Dict[str, Any]],
        eval_results: Optional[Dict] = None,
    ) -> int:
        """자동발주 스냅샷 저장

        order_list(예측 기반 발주 목록)와 results(실행 결과)를 결합하여
        상품별 스냅샷 레코드를 생성한다.

        Args:
            order_date: 발주일 (YYYY-MM-DD)
            order_list: 최종 발주 목록 (ImprovedPredictor 출력)
            results: OrderExecutor 실행 결과 리스트
            eval_results: 사전 평가 결과 {item_cd: EvalResult} (선택)

        Returns:
            저장된 건수 (실패 시 0)
        """
        try:
            # results를 item_cd 기준으로 인덱싱
            result_map: Dict[str, Dict] = {}
            for r in (results or []):
                ic = r.get("item_cd")
                if ic:
                    result_map[ic] = r

            # delivery_type 판별용 import
            from src.alert.config import ALERT_CATEGORIES
            from src.alert.delivery_utils import get_delivery_type

            snapshot_items: List[Dict[str, Any]] = []
            for item in order_list:
                item_cd = item.get("item_cd")
                if not item_cd:
                    continue

                exec_result = result_map.get(item_cd, {})
                eval_decision = None
                if eval_results and item_cd in eval_results:
                    er = eval_results[item_cd]
                    eval_decision = (
                        er.decision.name if hasattr(er, "decision") else str(er)
                    )

                # actual_qty가 있으면 그것을 사용, 없으면 final_order_qty
                final_qty = exec_result.get(
                    "actual_qty", item.get("final_order_qty", 0)
                )

                # delivery_type: auto_order.py와 동일한 로직
                mid_cd = item.get("mid_cd", "")
                item_nm = item.get("item_nm", "")
                if mid_cd in ALERT_CATEGORIES:
                    delivery_type = get_delivery_type(item_nm) or "1차"
                else:
                    delivery_type = "일반"

                snapshot_items.append({
                    "item_cd": item_cd,
                    "item_nm": item_nm,
                    "mid_cd": mid_cd,
                    "predicted_qty": item.get("predicted_sales", 0)
                        or item.get("predicted_qty", 0),
                    "recommended_qty": item.get("recommended_qty", 0),
                    "final_order_qty": final_qty,
                    "current_stock": item.get("current_stock", 0),
                    "pending_qty": item.get("pending_receiving_qty", 0)
                        or item.get("pending_qty", 0),
                    "eval_decision": eval_decision,
                    "order_unit_qty": item.get("order_unit_qty", 1),
                    "order_success": 1 if exec_result.get("success") else 0,
                    "confidence": item.get("confidence", ""),
                    "delivery_type": delivery_type,
                    "data_days": item.get("data_days"),
                })

            count = self.repo.save_order_snapshot(
                store_id=self.store_id,
                order_date=order_date,
                items=snapshot_items,
            )
            return count

        except Exception as e:
            logger.debug(f"[발주분석] 스냅샷 저장 실패: {e}")
            return 0

    def compare_and_save(
        self,
        order_date: str,
        receiving_data: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """입고 데이터와 스냅샷 비교 후 diff 저장

        Args:
            order_date: 발주일 (receiving_data의 order_date 필드와 일치)
            receiving_data: ReceivingCollector가 수집한 입고 레코드 리스트

        Returns:
            비교 결과 {diffs, summary, unchanged_count} 또는 None
        """
        try:
            # 1) 해당 발주일의 스냅샷 조회
            snapshot = self.repo.get_snapshot_by_date(self.store_id, order_date)

            if not snapshot and not receiving_data:
                return None

            # 2) 입고일 결정 (receiving_data에서 첫 번째 receiving_date 사용)
            receiving_date = ""
            for r in receiving_data:
                rd = r.get("receiving_date")
                if rd:
                    receiving_date = rd
                    break

            # 3) 비교 분석
            result = self.analyzer.compare(
                auto_snapshot=snapshot,
                receiving_data=receiving_data,
                store_id=self.store_id,
                order_date=order_date,
                receiving_date=receiving_date,
            )

            # 4) diff가 있으면 저장
            if result.get("diffs"):
                self.repo.save_diffs(result["diffs"])

            # 5) 요약 저장 (diff 유무와 관계없이)
            if result.get("summary"):
                self.repo.save_summary(result["summary"])

            return result

        except Exception as e:
            logger.debug(f"[발주분석] 비교 분석 실패 ({order_date}): {e}")
            return None

    def compare_for_date(
        self,
        order_date: str,
        receiving_date: str,
    ) -> Optional[Dict[str, Any]]:
        """이미 DB에 있는 데이터로 비교 (백필/재분석용)

        order_snapshots와 receiving_history를 각각 DB에서 읽어 비교한다.
        receiving_history는 운영 DB에 있으므로 별도 조회가 필요하다.

        Args:
            order_date: 발주일
            receiving_date: 입고일

        Returns:
            비교 결과 또는 None
        """
        try:
            from src.infrastructure.database.repos import ReceivingRepository

            snapshot = self.repo.get_snapshot_by_date(self.store_id, order_date)
            if not snapshot:
                return None

            recv_repo = ReceivingRepository(store_id=self.store_id)
            receiving_rows = recv_repo.get_receiving_by_date(
                receiving_date, store_id=self.store_id
            )

            # sqlite3.Row → dict 변환
            receiving_data = []
            for row in receiving_rows:
                receiving_data.append({
                    "item_cd": row["item_cd"],
                    "item_nm": row.get("item_nm"),
                    "mid_cd": row.get("mid_cd"),
                    "order_date": row.get("order_date"),
                    "order_qty": row.get("order_qty", 0),
                    "receiving_qty": row.get("receiving_qty", 0),
                    "receiving_date": row.get("receiving_date", receiving_date),
                })

            # order_date가 일치하는 것만 필터
            filtered = [
                r for r in receiving_data
                if r.get("order_date") == order_date
            ]

            result = self.analyzer.compare(
                auto_snapshot=snapshot,
                receiving_data=filtered,
                store_id=self.store_id,
                order_date=order_date,
                receiving_date=receiving_date,
            )

            if result.get("diffs"):
                self.repo.save_diffs(result["diffs"])
            if result.get("summary"):
                self.repo.save_summary(result["summary"])

            return result

        except Exception as e:
            logger.debug(f"[발주분석] 백필 비교 실패 ({order_date}): {e}")
            return None
