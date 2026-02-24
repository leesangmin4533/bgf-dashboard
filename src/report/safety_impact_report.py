"""
안전재고 변경 영향도 HTML 리포트

baseline JSON(변경 전)과 현재 예측 결과를 비교하여
상품별/카테고리별 안전재고 변화량과 품절 추이를 시각화한다.
"""

import json
import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from .base_report import BaseReport
from src.prediction.improved_predictor import PredictionResult
from src.prediction.categories.default import CATEGORY_NAMES
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SafetyImpactReport(BaseReport):
    """안전재고 파라미터 변경 영향도 분석 HTML"""

    REPORT_SUB_DIR = "impact"
    TEMPLATE_NAME = "safety_impact.html"

    def __init__(self, db_path: str = None, store_id: Optional[str] = None):
        super().__init__(db_path)
        self.store_id = store_id

    def save_baseline(self, predictions: List[PredictionResult]) -> Path:
        """변경 전 baseline JSON 저장

        Args:
            predictions: 현재 파라미터의 예측 결과

        Returns:
            baseline JSON 파일 경로
        """
        baseline = {}
        for p in predictions:
            baseline[p.item_cd] = {
                "item_nm": p.item_nm or p.item_cd,
                "mid_cd": p.mid_cd,
                "safety_stock": round(p.safety_stock, 2),
                "order_qty": p.order_qty,
                "predicted_qty": round(p.predicted_qty, 2),
                "current_stock": p.current_stock,
                "pending_qty": p.pending_qty,
            }

        output_dir = (
            Path(__file__).parent.parent.parent / "data" / "reports" / "impact"
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now().strftime("%Y-%m-%d")
        filepath = output_dir / f"baseline_{date_str}.json"
        filepath.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"[리포트] Baseline 저장: {filepath}")
        return filepath

    def generate(
        self,
        current_predictions: List[PredictionResult],
        baseline_path: str,
        change_date: str = None,
    ) -> Path:
        """영향도 리포트 생성

        Args:
            current_predictions: 변경 후 예측 결과
            baseline_path: baseline JSON 경로
            change_date: 변경 적용일 (기본: 오늘)

        Returns:
            생성된 HTML 파일 경로
        """
        if change_date is None:
            change_date = datetime.now().strftime("%Y-%m-%d")

        baseline = json.loads(
            Path(baseline_path).read_text(encoding="utf-8")
        )
        context = self._build_context(current_predictions, baseline, change_date)
        html = self.render(context)
        filename = f"safety_impact_{change_date}.html"
        return self.save(html, filename)

    def _build_context(
        self,
        predictions: List[PredictionResult],
        baseline: Dict,
        change_date: str,
    ) -> Dict[str, Any]:
        """컨텍스트 빌드"""
        comparisons = self._build_comparisons(predictions, baseline)
        cat_summary = self._aggregate_by_category(comparisons)
        summary = self._calc_overall_summary(comparisons)
        stockout_trend = self._query_stockout_trend(change_date)

        return {
            "change_date": change_date,
            "summary": summary,
            "comparisons": sorted(comparisons, key=lambda x: x["pct_change"]),
            "cat_summary": cat_summary,
            "stockout_trend": stockout_trend,
        }

    def _build_comparisons(
        self, predictions: List[PredictionResult], baseline: Dict
    ) -> List[Dict[str, Any]]:
        """상품별 비교 데이터 생성"""
        comparisons = []
        for p in predictions:
            if p.item_cd not in baseline:
                continue
            b = baseline[p.item_cd]
            old_safety = b["safety_stock"]
            new_safety = round(p.safety_stock, 2)
            delta = new_safety - old_safety
            pct = (delta / old_safety * 100) if old_safety > 0 else 0

            comparisons.append({
                "item_cd": p.item_cd,
                "item_nm": p.item_nm or p.item_cd,
                "mid_cd": p.mid_cd,
                "category": CATEGORY_NAMES.get(p.mid_cd, p.mid_cd),
                "old_safety": old_safety,
                "new_safety": new_safety,
                "delta": round(delta, 2),
                "pct_change": round(pct, 1),
                "old_order": b["order_qty"],
                "new_order": p.order_qty,
            })
        return comparisons

    def _aggregate_by_category(self, comparisons: List[Dict]) -> Dict[str, Any]:
        """카테고리별 안전재고 감소율"""
        groups: Dict[str, Dict] = {}
        for c in comparisons:
            cat = c["category"]
            if cat not in groups:
                groups[cat] = {"old_total": 0.0, "new_total": 0.0, "count": 0}
            groups[cat]["old_total"] += c["old_safety"]
            groups[cat]["new_total"] += c["new_safety"]
            groups[cat]["count"] += 1

        items = []
        for cat, g in groups.items():
            pct = (
                (g["new_total"] - g["old_total"]) / g["old_total"] * 100
                if g["old_total"] > 0
                else 0
            )
            items.append({
                "category": cat,
                "old_total": round(g["old_total"], 1),
                "new_total": round(g["new_total"], 1),
                "pct_change": round(pct, 1),
                "count": g["count"],
            })

        sorted_items = sorted(items, key=lambda x: x["pct_change"])
        return {
            "labels": [r["category"] for r in sorted_items],
            "pct_changes": [r["pct_change"] for r in sorted_items],
            "items": sorted_items,
        }

    def _calc_overall_summary(self, comparisons: List[Dict]) -> Dict[str, Any]:
        """전체 요약"""
        if not comparisons:
            return {
                "total_items": 0, "total_old": 0, "total_new": 0,
                "total_change_pct": 0, "decreased_count": 0,
                "increased_count": 0, "unchanged_count": 0,
            }
        total_old = sum(c["old_safety"] for c in comparisons)
        total_new = sum(c["new_safety"] for c in comparisons)
        pct = (total_new - total_old) / total_old * 100 if total_old > 0 else 0
        return {
            "total_items": len(comparisons),
            "total_old": round(total_old, 1),
            "total_new": round(total_new, 1),
            "total_change_pct": round(pct, 1),
            "decreased_count": sum(1 for c in comparisons if c["delta"] < -0.01),
            "increased_count": sum(1 for c in comparisons if c["delta"] > 0.01),
            "unchanged_count": sum(
                1 for c in comparisons if -0.01 <= c["delta"] <= 0.01
            ),
        }

    def _query_stockout_trend(
        self, change_date: str, days_before: int = 14, days_after: int = 14
    ) -> Dict[str, Any]:
        """변경일 전후 품절 추이"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()

            cursor.execute(f"""
                SELECT sales_date, COUNT(*) as stockout_count
                FROM daily_sales
                WHERE stock_qty = 0 AND sale_qty > 0
                AND sales_date BETWEEN date(?, '-' || ? || ' days')
                                    AND date(?, '+' || ? || ' days')
                {store_filter}
                GROUP BY sales_date
                ORDER BY sales_date
            """, (change_date, days_before, change_date, days_after) + store_params)
            rows = cursor.fetchall()
            conn.close()
        except Exception:
            rows = []

        return {
            "labels": [r[0] for r in rows],
            "counts": [r[1] for r in rows],
            "change_date": change_date,
        }
