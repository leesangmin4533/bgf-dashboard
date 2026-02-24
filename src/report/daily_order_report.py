"""
일일 발주 대시보드 HTML 리포트

PredictionResult 리스트를 받아 카테고리별 요약, 상품별 상세 테이블,
스킵 목록, 안전재고 분포를 시각화한 HTML을 생성한다.
"""

from datetime import datetime
from typing import List, Dict, Any
from pathlib import Path

from .base_report import BaseReport
from src.prediction.improved_predictor import PredictionResult
from src.prediction.categories.default import CATEGORY_NAMES
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DailyOrderReport(BaseReport):
    """일일 발주 대시보드 HTML 생성"""

    REPORT_SUB_DIR = "daily"
    TEMPLATE_NAME = "daily_order.html"

    def generate(
        self,
        predictions: List[PredictionResult],
        target_date: str = None,
    ) -> Path:
        """일일 발주 리포트 생성

        Args:
            predictions: ImprovedPredictor.get_order_candidates() 결과
            target_date: 대상 날짜 (기본: 오늘)

        Returns:
            생성된 HTML 파일 경로
        """
        if target_date is None:
            target_date = datetime.now().strftime("%Y-%m-%d")

        context = self._build_context(predictions, target_date)
        html = self.render(context)
        filename = f"daily_order_{target_date}.html"
        return self.save(html, filename)

    def _build_context(
        self,
        predictions: List[PredictionResult],
        target_date: str,
    ) -> Dict[str, Any]:
        """Jinja2 템플릿 컨텍스트 생성"""
        summary = self._calc_summary(predictions)
        category_data = self._group_by_category(predictions)
        items = self._build_item_table(predictions)
        skipped = self._build_skipped_list(predictions)
        safety_dist = self._build_safety_distribution(predictions)

        return {
            "target_date": target_date,
            "summary": summary,
            "category_data": category_data,
            "items": items,
            "skipped": skipped,
            "safety_dist": safety_dist,
        }

    def _calc_summary(self, predictions: List[PredictionResult]) -> Dict[str, Any]:
        """요약 카드용 통계"""
        total = len(predictions)
        ordered = [p for p in predictions if p.order_qty > 0]
        skipped_list = [p for p in predictions if p.order_qty <= 0]
        categories = set(p.mid_cd for p in predictions)

        return {
            "total_items": total,
            "ordered_count": len(ordered),
            "skipped_count": len(skipped_list),
            "total_order_qty": sum(p.order_qty for p in ordered),
            "category_count": len(categories),
            "avg_safety_stock": round(
                sum(p.safety_stock for p in predictions) / max(total, 1), 1
            ),
        }

    def _group_by_category(
        self, predictions: List[PredictionResult]
    ) -> Dict[str, Any]:
        """카테고리별 집계 → Chart.js bar chart 데이터"""
        groups: Dict[str, Dict] = {}
        for p in predictions:
            cat_name = CATEGORY_NAMES.get(p.mid_cd, p.mid_cd)
            if cat_name not in groups:
                groups[cat_name] = {"count": 0, "order_qty": 0, "safety_stock": 0.0}
            groups[cat_name]["count"] += 1
            groups[cat_name]["order_qty"] += p.order_qty
            groups[cat_name]["safety_stock"] += p.safety_stock

        sorted_cats = sorted(
            groups.items(), key=lambda x: x[1]["order_qty"], reverse=True
        )

        return {
            "labels": [c[0] for c in sorted_cats],
            "order_qty": [c[1]["order_qty"] for c in sorted_cats],
            "count": [c[1]["count"] for c in sorted_cats],
            "safety_stock": [round(c[1]["safety_stock"], 1) for c in sorted_cats],
        }

    def _build_item_table(
        self, predictions: List[PredictionResult]
    ) -> List[Dict[str, Any]]:
        """상품별 상세 테이블 (order_qty 내림차순)"""
        items = []
        for p in sorted(predictions, key=lambda x: x.order_qty, reverse=True):
            cat_name = CATEGORY_NAMES.get(p.mid_cd, p.mid_cd)
            daily_avg = p.predicted_qty if p.predicted_qty else 0
            items.append({
                "item_cd": p.item_cd,
                "item_nm": p.item_nm or p.item_cd,
                "category": cat_name,
                "mid_cd": p.mid_cd,
                "daily_avg": round(daily_avg, 1),
                "weekday_coef": round(p.weekday_coef, 2),
                "adjusted_qty": round(p.adjusted_qty, 1),
                "safety_stock": round(p.safety_stock, 1),
                "current_stock": p.current_stock,
                "pending_qty": p.pending_qty,
                "order_qty": p.order_qty,
                "confidence": p.confidence,
                "data_days": p.data_days,
            })
        return items

    def _build_skipped_list(
        self, predictions: List[PredictionResult]
    ) -> List[Dict[str, Any]]:
        """발주 스킵 상품 목록"""
        skipped = []
        for p in predictions:
            if p.order_qty > 0:
                continue
            reason = self._determine_skip_reason(p)
            skipped.append({
                "item_cd": p.item_cd,
                "item_nm": p.item_nm or p.item_cd,
                "category": CATEGORY_NAMES.get(p.mid_cd, p.mid_cd),
                "current_stock": p.current_stock,
                "pending_qty": p.pending_qty,
                "safety_stock": round(p.safety_stock, 1),
                "reason": reason,
            })
        return skipped

    def _determine_skip_reason(self, p: PredictionResult) -> str:
        """스킵 사유 판별"""
        if p.tobacco_skip_order:
            return f"담배 상한선: {p.tobacco_skip_reason}"
        if p.ramen_skip_order:
            return "라면 상한선 초과"
        if p.beer_skip_order:
            return f"맥주: {p.beer_skip_reason}"
        if p.soju_skip_order:
            return f"소주: {p.soju_skip_reason}"
        if p.current_stock + p.pending_qty >= p.safety_stock + p.adjusted_qty:
            return "재고+미입고 충분"
        return "발주량 0 (수요 없음)"

    def _build_safety_distribution(
        self, predictions: List[PredictionResult]
    ) -> Dict[str, Any]:
        """안전재고 일수 분포 히스토그램"""
        all_days = []
        for p in predictions:
            daily_avg = p.predicted_qty if p.predicted_qty > 0 else 0.001
            safety_days = p.safety_stock / daily_avg
            all_days.append(round(safety_days, 2))

        bins = [0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 100]
        bin_labels = ["0~0.5", "0.5~1.0", "1.0~1.5", "1.5~2.0", "2.0~2.5", "2.5~3.0", "3.0+"]
        counts = [0] * len(bin_labels)
        for d in all_days:
            placed = False
            for i in range(len(bins) - 1):
                if bins[i] <= d < bins[i + 1]:
                    counts[i] += 1
                    placed = True
                    break
            if not placed:
                counts[-1] += 1

        return {"labels": bin_labels, "counts": counts}
