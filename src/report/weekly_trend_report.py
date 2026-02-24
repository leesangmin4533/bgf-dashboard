"""
주간 트렌드 HTML 리포트

기존 WeeklyTrendReport의 데이터를 활용하여 7일 판매 추이,
예측 정확도, 요일별 히트맵을 HTML 대시보드로 생성한다.
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path

from .base_report import BaseReport
from src.prediction.categories.default import CATEGORY_NAMES
from src.utils.logger import get_logger

logger = get_logger(__name__)


class WeeklyTrendReportHTML(BaseReport):
    """주간 트렌드 HTML 대시보드"""

    REPORT_SUB_DIR = "weekly"
    TEMPLATE_NAME = "weekly_trend.html"

    def __init__(self, db_path: str = None, store_id: Optional[str] = None):
        super().__init__(db_path)
        self.store_id = store_id

    def generate(self, end_date: str = None) -> Path:
        """주간 트렌드 HTML 생성

        Args:
            end_date: 기준 종료일 (기본: 어제)

        Returns:
            생성된 HTML 파일 경로
        """
        if end_date is None:
            end_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        # DB에서 직접 데이터 조회
        weekly_summary = self._query_weekly_summary(end_date)
        daily_trend = self._query_daily_category_sales(end_date)
        heatmap = self._query_weekday_heatmap()
        top_items = self._query_top_items(end_date)
        accuracy = self._query_accuracy(end_date)

        context = {
            "end_date": end_date,
            "weekly_summary": weekly_summary,
            "daily_trend": daily_trend,
            "heatmap": heatmap,
            "top_items": top_items,
            "accuracy": accuracy,
        }

        html = self.render(context)
        d = datetime.strptime(end_date, "%Y-%m-%d")
        week_str = d.strftime("%Y-W%W")
        filename = f"weekly_trend_{week_str}.html"
        return self.save(html, filename)

    def _query_weekly_summary(self, end_date: str) -> Dict[str, Any]:
        """주간 요약: 이번 주 vs 지난 주"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()

            # 이번 주 (end_date 기준 7일)
            cursor.execute(f"""
                SELECT COUNT(DISTINCT item_cd) as items,
                       SUM(sale_qty) as sales,
                       SUM(ord_qty) as orders,
                       SUM(disuse_qty) as disuse
                FROM daily_sales
                WHERE sales_date BETWEEN date(?, '-6 days') AND ?
                {store_filter}
            """, (end_date, end_date) + store_params)
            this_week = cursor.fetchone()

            # 지난 주
            cursor.execute(f"""
                SELECT SUM(sale_qty) as sales
                FROM daily_sales
                WHERE sales_date BETWEEN date(?, '-13 days') AND date(?, '-7 days')
                {store_filter}
            """, (end_date, end_date) + store_params)
            prev_week = cursor.fetchone()
            conn.close()

            this_sales = this_week[1] or 0
            prev_sales = prev_week[0] or 0
            growth = ((this_sales - prev_sales) / prev_sales * 100) if prev_sales > 0 else 0

            return {
                "total_items": this_week[0] or 0,
                "total_sales": this_sales,
                "total_orders": this_week[2] or 0,
                "total_disuse": this_week[3] or 0,
                "prev_sales": prev_sales,
                "growth_pct": round(growth, 1),
            }
        except Exception:
            return {
                "total_items": 0, "total_sales": 0, "total_orders": 0,
                "total_disuse": 0, "prev_sales": 0, "growth_pct": 0,
            }

    def _query_daily_category_sales(self, end_date: str, days: int = 7) -> Dict[str, Any]:
        """최근 7일 카테고리별 일별 판매량"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()

            cursor.execute(f"""
                SELECT sales_date, mid_cd, SUM(sale_qty) as total_qty
                FROM daily_sales
                WHERE sales_date BETWEEN date(?, '-' || ? || ' days') AND ?
                {store_filter}
                GROUP BY sales_date, mid_cd
                ORDER BY sales_date, mid_cd
            """, (end_date, days - 1, end_date) + store_params)
            rows = cursor.fetchall()
            conn.close()
        except Exception:
            rows = []

        dates = sorted(set(r[0] for r in rows))
        cats: Dict[str, Dict[str, int]] = {}
        for date_str, mid_cd, qty in rows:
            cat = CATEGORY_NAMES.get(mid_cd, mid_cd)
            if cat not in cats:
                cats[cat] = {}
            cats[cat][date_str] = qty

        top_cats = sorted(cats.items(), key=lambda x: sum(x[1].values()), reverse=True)[:10]
        datasets = []
        colors = [
            '#00d2ff', '#ff6b6b', '#69f0ae', '#ffd600', '#ce93d8',
            '#ff8a65', '#4fc3f7', '#aed581', '#f48fb1', '#90a4ae',
        ]
        for i, (cat, date_qty) in enumerate(top_cats):
            datasets.append({
                "label": cat,
                "data": [date_qty.get(d, 0) for d in dates],
                "borderColor": colors[i % len(colors)],
                "backgroundColor": 'transparent',
                "tension": 0.3,
            })

        return {"labels": dates, "datasets": datasets}

    def _query_weekday_heatmap(self, days: int = 28) -> Dict[str, Any]:
        """카테고리 x 요일 히트맵"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            cursor = conn.cursor()
            date_filter = f"-{days} days"

            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()

            cursor.execute(f"""
                SELECT mid_cd,
                       CAST(strftime('%w', sales_date) AS INTEGER) as dow,
                       ROUND(AVG(sale_qty), 1) as avg_qty
                FROM daily_sales
                WHERE sales_date >= date('now', ?)
                AND sale_qty > 0
                {store_filter}
                GROUP BY mid_cd, dow
                ORDER BY mid_cd, dow
            """, (date_filter,) + store_params)
            rows = cursor.fetchall()
            conn.close()
        except Exception:
            rows = []

        heatmap: Dict[str, List[float]] = {}
        for mid_cd, sqlite_dow, avg_qty in rows:
            cat = CATEGORY_NAMES.get(mid_cd, mid_cd)
            if cat not in heatmap:
                heatmap[cat] = [0.0] * 7
            py_dow = (sqlite_dow - 1) % 7
            heatmap[cat][py_dow] = avg_qty

        sorted_cats = sorted(heatmap.items(), key=lambda x: sum(x[1]), reverse=True)[:15]

        return {
            "categories": [c[0] for c in sorted_cats],
            "weekdays": ["월", "화", "수", "목", "금", "토", "일"],
            "data": [c[1] for c in sorted_cats],
        }

    def _query_top_items(self, end_date: str, limit: int = 10) -> Dict[str, Any]:
        """상위/하위 판매 상품"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            cursor = conn.cursor()

            store_filter = "AND ds.store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()

            cursor.execute(f"""
                SELECT ds.item_cd, p.item_nm, ds.mid_cd,
                       SUM(ds.sale_qty) as total_sales
                FROM daily_sales ds
                LEFT JOIN products p ON ds.item_cd = p.item_cd
                WHERE ds.sales_date BETWEEN date(?, '-6 days') AND ?
                {store_filter}
                GROUP BY ds.item_cd
                ORDER BY total_sales DESC
                LIMIT ?
            """, (end_date, end_date) + store_params + (limit,))
            top = cursor.fetchall()
            conn.close()
        except Exception:
            top = []

        return {
            "top_sellers": [
                {
                    "item_cd": r[0],
                    "item_nm": r[1] or r[0],
                    "category": CATEGORY_NAMES.get(r[2], r[2]),
                    "total_sales": r[3],
                }
                for r in top
            ]
        }

    def _query_accuracy(self, end_date: str) -> Dict[str, Any]:
        """예측 정확도 (eval_outcomes 테이블)"""
        sf = "AND store_id = ?" if self.store_id else ""
        sp = (self.store_id,) if self.store_id else ()

        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT eval_date,
                       COUNT(*) as total,
                       SUM(CASE WHEN outcome = 'CORRECT' THEN 1 ELSE 0 END) as correct,
                       AVG(CASE WHEN actual_sold_qty > 0
                           THEN ABS(daily_avg - actual_sold_qty) * 100.0 / actual_sold_qty
                           ELSE NULL END) as mape
                FROM eval_outcomes
                WHERE eval_date BETWEEN date(?, '-6 days') AND ?
                AND verified_at IS NOT NULL
                {sf}
                GROUP BY eval_date
                ORDER BY eval_date
            """, (end_date, end_date) + sp)
            rows = cursor.fetchall()
            conn.close()
        except Exception:
            rows = []

        return {
            "dates": [r[0] for r in rows],
            "mape_values": [round(r[3], 1) if r[3] else 0 for r in rows],
            "accuracy_values": [
                round(r[2] / r[1] * 100, 1) if r[1] > 0 else 0 for r in rows
            ],
        }
