"""
일별 리포트 생성기
- 매일 수집된 데이터 요약
- 카테고리별 통계
- 이전 대비 변화 분석
- 날씨/요일 상관관계

Usage:
    python daily_report.py                    # 어제 리포트
    python daily_report.py --date 2026-01-24  # 특정 날짜 리포트
    python daily_report.py --week             # 주간 요약
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

from src.infrastructure.database.repos import SalesRepository, ExternalFactorRepository
from src.db.models import get_connection
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DailyReport:
    """일별 리포트 생성기"""

    def __init__(self, store_id: Optional[str] = None) -> None:
        self.store_id = store_id
        self.sales_repo = SalesRepository(store_id=self.store_id)
        self.factor_repo = ExternalFactorRepository()

    def _get_conn(self):
        """DB 연결 (매장 DB일 경우 common.db ATTACH)"""
        if self.store_id:
            from src.infrastructure.database.connection import (
                DBRouter, attach_common_with_views,
            )
            conn = DBRouter.get_store_connection(self.store_id)
            return attach_common_with_views(conn, self.store_id)
        return get_connection()

    def generate(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """
        일별 리포트 생성

        Args:
            target_date: 대상 날짜 (YYYY-MM-DD), 기본값: 어제

        Returns:
            리포트 데이터
        """
        if target_date is None:
            yesterday = datetime.now() - timedelta(days=1)
            target_date = yesterday.strftime("%Y-%m-%d")

        report = {
            "date": target_date,
            "generated_at": datetime.now().isoformat(),
            "summary": self._get_summary(target_date),
            "categories": self._get_category_stats(target_date),
            "top_items": self._get_top_items(target_date),
            "weather": self._get_weather_info(target_date),
            "calendar": self._get_calendar_info(target_date),
            "comparison": self._get_comparison(target_date),
        }

        return report

    def _get_summary(self, target_date: str) -> Dict[str, Any]:
        """전체 요약 통계"""
        conn = self._get_conn()
        cursor = conn.cursor()

        store_filter = "AND store_id = ?" if self.store_id else ""
        store_params = (self.store_id,) if self.store_id else ()

        # 기본 통계
        cursor.execute(f"""
            SELECT
                COUNT(DISTINCT item_cd) as total_items,
                COUNT(DISTINCT mid_cd) as total_categories,
                SUM(sale_qty) as total_sales,
                SUM(ord_qty) as total_orders,
                SUM(stock_qty) as total_stock
            FROM daily_sales
            WHERE sales_date = ?
            {store_filter}
        """, (target_date,) + store_params)

        row = cursor.fetchone()
        conn.close()

        if row and row[0]:
            return {
                "total_items": row[0],
                "total_categories": row[1],
                "total_sales": row[2] or 0,
                "total_orders": row[3] or 0,
                "total_stock": row[4] or 0,
            }
        return {
            "total_items": 0,
            "total_categories": 0,
            "total_sales": 0,
            "total_orders": 0,
            "total_stock": 0,
        }

    def _get_category_stats(self, target_date: str) -> List[Dict[str, Any]]:
        """카테고리별 통계"""
        conn = self._get_conn()
        cursor = conn.cursor()

        store_filter = "AND ds.store_id = ?" if self.store_id else ""
        store_params = (self.store_id,) if self.store_id else ()

        cursor.execute(f"""
            SELECT
                ds.mid_cd,
                mc.mid_nm,
                COUNT(DISTINCT ds.item_cd) as item_count,
                SUM(ds.sale_qty) as total_sales,
                SUM(ds.ord_qty) as total_orders,
                AVG(ds.stock_qty) as avg_stock
            FROM daily_sales ds
            LEFT JOIN mid_categories mc ON ds.mid_cd = mc.mid_cd
            WHERE ds.sales_date = ?
            {store_filter}
            GROUP BY ds.mid_cd, mc.mid_nm
            ORDER BY total_sales DESC
        """, (target_date,) + store_params)

        rows = cursor.fetchall()
        conn.close()

        return [
            {
                "mid_cd": row[0],
                "mid_nm": row[1] or row[0],
                "item_count": row[2],
                "total_sales": row[3] or 0,
                "total_orders": row[4] or 0,
                "avg_stock": round(row[5], 1) if row[5] else 0,
            }
            for row in rows
        ]

    def _get_top_items(self, target_date: str, limit: int = 10) -> List[Dict[str, Any]]:
        """상위 판매 상품"""
        conn = self._get_conn()
        cursor = conn.cursor()

        store_filter = "AND ds.store_id = ?" if self.store_id else ""
        store_params = (self.store_id,) if self.store_id else ()

        cursor.execute(f"""
            SELECT
                ds.item_cd,
                p.item_nm,
                mc.mid_nm,
                ds.sale_qty,
                ds.ord_qty,
                ds.stock_qty
            FROM daily_sales ds
            LEFT JOIN products p ON ds.item_cd = p.item_cd
            LEFT JOIN mid_categories mc ON ds.mid_cd = mc.mid_cd
            WHERE ds.sales_date = ?
            {store_filter}
            ORDER BY ds.sale_qty DESC
            LIMIT ?
        """, (target_date,) + store_params + (limit,))

        rows = cursor.fetchall()
        conn.close()

        return [
            {
                "item_cd": row[0],
                "item_nm": row[1] or row[0],
                "mid_nm": row[2] or "",
                "sale_qty": row[3] or 0,
                "ord_qty": row[4] or 0,
                "stock_qty": row[5] or 0,
            }
            for row in rows
        ]

    def _get_weather_info(self, target_date: str) -> Dict[str, Any]:
        """날씨 정보"""
        factors = self.factor_repo.get_factors(target_date, "weather")

        weather = {}
        for f in factors:
            weather[f["factor_key"]] = f["factor_value"]

        return weather

    def _get_calendar_info(self, target_date: str) -> Dict[str, Any]:
        """캘린더 정보"""
        factors = self.factor_repo.get_factors(target_date, "calendar")

        calendar = {}
        for f in factors:
            value = f["factor_value"]
            # boolean 문자열 변환
            if value == "true":
                value = True
            elif value == "false":
                value = False
            calendar[f["factor_key"]] = value

        return calendar

    def _get_comparison(self, target_date: str) -> Dict[str, Any]:
        """전일 대비 비교"""
        target_dt = datetime.strptime(target_date, "%Y-%m-%d")
        prev_date = (target_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        week_ago = (target_dt - timedelta(days=7)).strftime("%Y-%m-%d")

        current = self._get_summary(target_date)
        prev = self._get_summary(prev_date)
        week = self._get_summary(week_ago)

        def calc_change(current_val: int, prev_val: int) -> Optional[float]:
            if prev_val == 0:
                return None
            return round((current_val - prev_val) / prev_val * 100, 1)

        return {
            "vs_prev_day": {
                "date": prev_date,
                "sales_change": calc_change(current["total_sales"], prev["total_sales"]),
                "orders_change": calc_change(current["total_orders"], prev["total_orders"]),
            },
            "vs_week_ago": {
                "date": week_ago,
                "sales_change": calc_change(current["total_sales"], week["total_sales"]),
                "orders_change": calc_change(current["total_orders"], week["total_orders"]),
            }
        }

    def print_report(self, report: Dict[str, Any]) -> None:
        """리포트 출력 (logger.info로 통합 로깅)"""
        lines = []
        lines.append("=" * 70)
        lines.append(f"[DAILY REPORT] {report['date']}")
        lines.append("=" * 70)

        # 요약
        s = report["summary"]
        lines.append("[SUMMARY]")
        lines.append(f"  Total Items:      {s['total_items']:,}")
        lines.append(f"  Total Categories: {s['total_categories']}")
        lines.append(f"  Total Sales:      {s['total_sales']:,}")
        lines.append(f"  Total Orders:     {s['total_orders']:,}")
        lines.append(f"  Total Stock:      {s['total_stock']:,}")

        # 날씨/캘린더
        w = report.get("weather", {})
        c = report.get("calendar", {})
        lines.append("[EXTERNAL FACTORS]")
        if w:
            lines.append(f"  Temperature: {w.get('temperature', 'N/A')}도")
            lines.append(f"  Weather:     {w.get('weather_type', 'N/A')}")
        if c:
            lines.append(f"  Day:         {c.get('day_of_week', 'N/A')}요일")
            lines.append(f"  Weekend:     {'Yes' if c.get('is_weekend') else 'No'}")
            lines.append(f"  Holiday:     {c.get('holiday_name') or 'No'}")

        # 전일 대비
        comp = report.get("comparison", {})
        if comp:
            vs_prev = comp.get("vs_prev_day", {})
            vs_week = comp.get("vs_week_ago", {})
            lines.append("[COMPARISON]")
            if vs_prev.get("sales_change") is not None:
                sign = "+" if vs_prev["sales_change"] >= 0 else ""
                lines.append(f"  vs Prev Day: {sign}{vs_prev['sales_change']}% sales")
            if vs_week.get("sales_change") is not None:
                sign = "+" if vs_week["sales_change"] >= 0 else ""
                lines.append(f"  vs Week Ago: {sign}{vs_week['sales_change']}% sales")

        # 카테고리별 통계 (상위 10개)
        cats = report.get("categories", [])[:10]
        if cats:
            lines.append("[TOP CATEGORIES by Sales]")
            lines.append(f"  {'Category':<20} {'Items':>6} {'Sales':>8} {'Orders':>8}")
            lines.append("  " + "-" * 50)
            for cat in cats:
                name = cat["mid_nm"][:18] if cat["mid_nm"] else ""
                lines.append(f"  {name:<20} {cat['item_count']:>6} {cat['total_sales']:>8} {cat['total_orders']:>8}")

        # 상위 판매 상품
        items = report.get("top_items", [])[:10]
        if items:
            lines.append("[TOP 10 ITEMS by Sales]")
            lines.append(f"  {'Item':<30} {'Category':<15} {'Sale':>6}")
            lines.append("  " + "-" * 55)
            for item in items:
                name = item["item_nm"][:28] if item["item_nm"] else ""
                cat = item["mid_nm"][:13] if item["mid_nm"] else ""
                lines.append(f"  {name:<30} {cat:<15} {item['sale_qty']:>6}")

        lines.append("=" * 70)
        lines.append(f"Generated at: {report['generated_at']}")
        lines.append("=" * 70)

        logger.info("\n".join(lines))


class WeeklyReport:
    """주간 리포트 생성기"""

    def __init__(self, store_id: Optional[str] = None) -> None:
        self.store_id = store_id
        self.daily = DailyReport(store_id=store_id)

    def generate(self, end_date: Optional[str] = None) -> Dict[str, Any]:
        """주간 리포트 생성"""
        if end_date is None:
            yesterday = datetime.now() - timedelta(days=1)
            end_date = yesterday.strftime("%Y-%m-%d")

        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=6)

        dates = []
        current = start_dt
        while current <= end_dt:
            dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

        # 일별 데이터 수집
        daily_data = []
        for date in dates:
            summary = self.daily._get_summary(date)
            calendar = self.daily._get_calendar_info(date)
            daily_data.append({
                "date": date,
                "day_of_week": calendar.get("day_of_week", ""),
                "is_weekend": calendar.get("is_weekend", False),
                "is_holiday": calendar.get("is_holiday", False),
                **summary
            })

        # 주간 합계
        total_sales = sum(d["total_sales"] for d in daily_data)
        total_orders = sum(d["total_orders"] for d in daily_data)
        avg_sales = total_sales / len(daily_data) if daily_data else 0

        return {
            "period": f"{dates[0]} ~ {dates[-1]}",
            "generated_at": datetime.now().isoformat(),
            "daily_data": daily_data,
            "summary": {
                "total_sales": total_sales,
                "total_orders": total_orders,
                "avg_daily_sales": round(avg_sales, 1),
                "days_collected": len([d for d in daily_data if d["total_items"] > 0]),
            }
        }

    def print_report(self, report: Dict[str, Any]) -> None:
        """주간 리포트 출력 (logger.info로 통합 로깅)"""
        lines = []
        lines.append("=" * 70)
        lines.append(f"[WEEKLY REPORT] {report['period']}")
        lines.append("=" * 70)

        # 일별 데이터
        lines.append("[DAILY BREAKDOWN]")
        lines.append(f"  {'Date':<12} {'Day':<4} {'Items':>6} {'Sales':>8} {'Orders':>8} {'Note':<10}")
        lines.append("  " + "-" * 60)

        for d in report["daily_data"]:
            note = ""
            if d.get("is_holiday"):
                note = "Holiday"
            elif d.get("is_weekend"):
                note = "Weekend"

            items = d["total_items"] or "-"
            sales = d["total_sales"] if d["total_sales"] else "-"
            orders = d["total_orders"] if d["total_orders"] else "-"

            lines.append(f"  {d['date']:<12} {d.get('day_of_week', ''):<4} {items:>6} {sales:>8} {orders:>8} {note:<10}")

        # 주간 요약
        s = report["summary"]
        lines.append("[WEEKLY SUMMARY]")
        lines.append(f"  Total Sales:     {s['total_sales']:,}")
        lines.append(f"  Total Orders:    {s['total_orders']:,}")
        lines.append(f"  Avg Daily Sales: {s['avg_daily_sales']:,.1f}")
        lines.append(f"  Days Collected:  {s['days_collected']}/7")

        lines.append("=" * 70)
        logger.info("\n".join(lines))


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Daily Sales Report Generator")
    parser.add_argument(
        "--date", "-d",
        type=str,
        default=None,
        help="Target date (YYYY-MM-DD). Default: yesterday"
    )
    parser.add_argument(
        "--week", "-w",
        action="store_true",
        help="Generate weekly report"
    )

    args = parser.parse_args()

    if args.week:
        reporter = WeeklyReport()
        report = reporter.generate(args.date)
        reporter.print_report(report)
    else:
        reporter = DailyReport()
        report = reporter.generate(args.date)
        reporter.print_report(report)


if __name__ == "__main__":
    main()
