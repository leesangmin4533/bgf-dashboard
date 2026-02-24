# -*- coding: utf-8 -*-
"""
트렌드 리포트 모듈
- 주간/월간/분기별 판매 트렌드 분석
- 카카오톡 자동 발송

Usage:
    python trend_report.py --weekly              # 주간 리포트
    python trend_report.py --monthly             # 월간 리포트
    python trend_report.py --quarterly           # 분기 리포트
    python trend_report.py --schedule            # 스케줄러 시작
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import defaultdict

# 상위 경로 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import get_connection
from db.store_query import store_filter
from notification.kakao_notifier import KakaoNotifier, DEFAULT_REST_API_KEY
from prediction.improved_predictor import PredictionLogger
from analysis.product_analyzer import ProductAnalyzer
from utils.logger import get_logger

logger = get_logger(__name__)


class WeeklyTrendReport:
    """
    주간 트렌드 리포트 (통합)
    - 카테고리별 전주 대비 성장률
    - 급상승/하락 카테고리 TOP 3
    - 급상승/하락 상품 TOP 3 (±30% 이상)
    - 신규 인기 상품
    - 예측 정확도 (MAPE)
    """

    def __init__(self, store_id: Optional[str] = None) -> None:
        self.store_id = store_id
        self.prediction_logger = PredictionLogger()
        self.product_analyzer = ProductAnalyzer(threshold_pct=30.0, store_id=store_id)

    def generate(self, end_date: Optional[str] = None) -> Dict[str, Any]:
        """
        주간 트렌드 리포트 생성

        Args:
            end_date: 기준 종료일 (YYYY-MM-DD), 기본값: 어제

        Returns:
            리포트 데이터
        """
        if end_date is None:
            yesterday = datetime.now() - timedelta(days=1)
            end_date = yesterday.strftime("%Y-%m-%d")

        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        # 이번 주: end_date 기준 최근 7일
        this_week_start = end_dt - timedelta(days=6)
        this_week_end = end_dt

        # 전주: 그 이전 7일
        prev_week_start = this_week_start - timedelta(days=7)
        prev_week_end = this_week_start - timedelta(days=1)

        # 상품 분석
        product_analysis = self.product_analyzer.analyze(end_date)

        report = {
            "report_type": "weekly",
            "period": {
                "this_week": f"{this_week_start.strftime('%Y-%m-%d')} ~ {this_week_end.strftime('%Y-%m-%d')}",
                "prev_week": f"{prev_week_start.strftime('%Y-%m-%d')} ~ {prev_week_end.strftime('%Y-%m-%d')}",
            },
            "generated_at": datetime.now().isoformat(),
            "category_growth": self._get_category_growth(
                this_week_start.strftime("%Y-%m-%d"),
                this_week_end.strftime("%Y-%m-%d"),
                prev_week_start.strftime("%Y-%m-%d"),
                prev_week_end.strftime("%Y-%m-%d")
            ),
            "top_gainers": [],  # 카테고리 급상승
            "top_losers": [],   # 카테고리 급하락
            "prediction_accuracy": self._get_prediction_accuracy(7),
            # 상품 분석 결과 추가
            "surge_products": product_analysis.get("surge_products", [])[:5],  # 급상승 상품 TOP 5
            "plunge_products": product_analysis.get("plunge_products", [])[:5],  # 급하락 상품 TOP 5
            "new_popular": product_analysis.get("new_popular", [])[:5],  # 신규 인기 상품 TOP 5
        }

        # 카테고리 급상승/하락 TOP 3 추출
        growth_list = report["category_growth"]
        sorted_by_growth = sorted(
            [c for c in growth_list if c["growth_rate"] is not None],
            key=lambda x: x["growth_rate"],
            reverse=True
        )

        report["top_gainers"] = sorted_by_growth[:3]
        report["top_losers"] = sorted_by_growth[-3:][::-1] if len(sorted_by_growth) >= 3 else []

        return report

    def _get_category_growth(
        self,
        this_start: str,
        this_end: str,
        prev_start: str,
        prev_end: str
    ) -> List[Dict[str, Any]]:
        """카테고리별 전주 대비 성장률"""
        sf, sp = store_filter("ds", self.store_id)
        conn = get_connection()
        cursor = conn.cursor()

        # 이번 주 카테고리별 판매량
        cursor.execute(f"""
            SELECT
                ds.mid_cd,
                mc.mid_nm,
                SUM(ds.sale_qty) as total_sales,
                COUNT(DISTINCT ds.item_cd) as item_count
            FROM daily_sales ds
            LEFT JOIN mid_categories mc ON ds.mid_cd = mc.mid_cd
            WHERE ds.sales_date BETWEEN ? AND ?
            {sf}
            GROUP BY ds.mid_cd, mc.mid_nm
        """, (this_start, this_end) + sp)

        this_week = {row[0]: {"mid_nm": row[1], "sales": row[2], "items": row[3]} for row in cursor.fetchall()}

        # 전주 카테고리별 판매량
        cursor.execute(f"""
            SELECT
                ds.mid_cd,
                mc.mid_nm,
                SUM(ds.sale_qty) as total_sales
            FROM daily_sales ds
            LEFT JOIN mid_categories mc ON ds.mid_cd = mc.mid_cd
            WHERE ds.sales_date BETWEEN ? AND ?
            {sf}
            GROUP BY ds.mid_cd, mc.mid_nm
        """, (prev_start, prev_end) + sp)

        prev_week = {row[0]: row[2] for row in cursor.fetchall()}
        conn.close()

        # 성장률 계산
        results = []
        for mid_cd, data in this_week.items():
            prev_sales = prev_week.get(mid_cd, 0) or 0
            this_sales = data["sales"] or 0

            if prev_sales > 0:
                growth_rate = round((this_sales - prev_sales) / prev_sales * 100, 1)
            else:
                growth_rate = None  # 전주 데이터 없음

            results.append({
                "mid_cd": mid_cd,
                "mid_nm": data["mid_nm"] or mid_cd,
                "this_week_sales": this_sales,
                "prev_week_sales": prev_sales,
                "growth_rate": growth_rate,
                "item_count": data["items"],
            })

        # 판매량 내림차순 정렬
        results.sort(key=lambda x: x["this_week_sales"], reverse=True)
        return results

    def _get_prediction_accuracy(self, days: int = 7) -> Dict[str, Any]:
        """예측 정확도 (MAPE)"""
        try:
            accuracy = self.prediction_logger.calculate_accuracy(days)
            return accuracy
        except Exception as e:
            return {"error": str(e)}

    def format_message(self, report: Dict[str, Any]) -> str:
        """카카오톡 메시지 형식으로 변환"""
        lines = []
        lines.append("[BGF 주간 리포트]")
        lines.append(f"기간: {report['period']['this_week']}")
        lines.append("")

        # 카테고리 급상승 TOP 3
        lines.append("[UP] 카테고리 급상승")
        for i, cat in enumerate(report["top_gainers"][:3], 1):
            growth = cat["growth_rate"]
            sign = "+" if growth >= 0 else ""
            lines.append(f"  {i}. {cat['mid_nm'][:10]} {sign}{growth}%")

        lines.append("")

        # 카테고리 급하락 TOP 3
        lines.append("[DN] 카테고리 급하락")
        for i, cat in enumerate(report["top_losers"][:3], 1):
            growth = cat["growth_rate"]
            sign = "+" if growth >= 0 else ""
            lines.append(f"  {i}. {cat['mid_nm'][:10]} {sign}{growth}%")

        lines.append("")

        # 급상승 상품 TOP 3
        surge = report.get("surge_products", [])[:3]
        if surge:
            lines.append("[급등] 급상승 상품 (30%+)")
            for i, p in enumerate(surge, 1):
                lines.append(f"  {i}. {p['item_nm'][:12]} +{p['growth_rate']}%")
            lines.append("")

        # 급하락 상품 TOP 3
        plunge = report.get("plunge_products", [])[:3]
        if plunge:
            lines.append("[주의] 급하락 상품 (30%-)")
            for i, p in enumerate(plunge, 1):
                lines.append(f"  {i}. {p['item_nm'][:12]} {p['growth_rate']}%")
            lines.append("")

        # 신규 인기 상품
        new_popular = report.get("new_popular", [])[:3]
        if new_popular:
            lines.append("[NEW] 신규 인기 상품")
            for i, p in enumerate(new_popular, 1):
                status = "신규" if p.get("is_new") else "복귀"
                lines.append(f"  {i}. [{status}] {p['item_nm'][:10]} {p['this_week_sales']}개")
            lines.append("")

        # 예측 정확도
        acc = report.get("prediction_accuracy", {})
        if "message" in acc:
            lines.append(f"[예측]: {acc['message']}")
        elif "mape" in acc:
            lines.append(f"[예측] 정확도: {acc.get('accuracy_pct', 0):.1f}%")
            lines.append(f"  (MAPE {acc['mape']:.1f}%, {acc.get('total_predictions', 0)}건)")

        lines.append("")
        lines.append(f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        return "\n".join(lines)

    def print_report(self, report: Dict[str, Any]) -> None:
        """콘솔 출력 (logger.info로 통합 로깅)"""
        lines = []
        lines.append("=" * 70)
        lines.append("[WEEKLY TREND REPORT]")
        lines.append(f"이번 주: {report['period']['this_week']}")
        lines.append(f"전주: {report['period']['prev_week']}")
        lines.append("=" * 70)

        lines.append("[카테고리별 전주 대비 성장률]")
        lines.append(f"  {'카테고리':<20} {'이번주':>10} {'전주':>10} {'성장률':>10}")
        lines.append("  " + "-" * 55)
        for cat in report["category_growth"][:15]:
            name = (cat["mid_nm"] or "")[:18]
            growth = cat["growth_rate"]
            growth_str = f"{'+' if growth >= 0 else ''}{growth}%" if growth is not None else "N/A"
            lines.append(f"  {name:<20} {cat['this_week_sales']:>10} {cat['prev_week_sales']:>10} {growth_str:>10}")

        lines.append("[카테고리 급상승 TOP 3]")
        for i, cat in enumerate(report["top_gainers"][:3], 1):
            lines.append(f"  {i}. {cat['mid_nm']} (+{cat['growth_rate']}%)")

        lines.append("[카테고리 급하락 TOP 3]")
        for i, cat in enumerate(report["top_losers"][:3], 1):
            lines.append(f"  {i}. {cat['mid_nm']} ({cat['growth_rate']}%)")

        surge = report.get("surge_products", [])
        if surge:
            lines.append("[급상승 상품 TOP 5] (전주 대비 +30% 이상)")
            lines.append(f"  {'상품명':<28} {'카테고리':<10} {'이번주':>6} {'전주':>6} {'성장률':>8}")
            lines.append("  " + "-" * 65)
            for p in surge[:5]:
                name = (p["item_nm"] or "")[:26]
                cat = (p["mid_nm"] or "")[:8]
                lines.append(f"  {name:<28} {cat:<10} {p['this_week_sales']:>6} {p['prev_week_sales']:>6} +{p['growth_rate']:>6}%")

        plunge = report.get("plunge_products", [])
        if plunge:
            lines.append("[급하락 상품 TOP 5] (전주 대비 -30% 이하)")
            lines.append(f"  {'상품명':<28} {'카테고리':<10} {'이번주':>6} {'전주':>6} {'성장률':>8}")
            lines.append("  " + "-" * 65)
            for p in plunge[:5]:
                name = (p["item_nm"] or "")[:26]
                cat = (p["mid_nm"] or "")[:8]
                lines.append(f"  {name:<28} {cat:<10} {p['this_week_sales']:>6} {p['prev_week_sales']:>6} {p['growth_rate']:>7}%")

        new_popular = report.get("new_popular", [])
        if new_popular:
            lines.append("[신규 인기 상품 TOP 5] (전주 0~3개 -> 급증)")
            lines.append(f"  {'상품명':<28} {'카테고리':<10} {'이번주':>6} {'전주':>6} {'상태':<6}")
            lines.append("  " + "-" * 65)
            for p in new_popular[:5]:
                name = (p["item_nm"] or "")[:26]
                cat = (p["mid_nm"] or "")[:8]
                status = "신규" if p.get("is_new") else "복귀"
                lines.append(f"  {name:<28} {cat:<10} {p['this_week_sales']:>6} {p['prev_week_sales']:>6} {status:<6}")

        lines.append("[예측 정확도]")
        acc = report.get("prediction_accuracy", {})
        if "message" in acc:
            lines.append(f"  {acc['message']}")
        elif "mape" in acc:
            lines.append(f"  MAPE: {acc['mape']:.1f}%")
            lines.append(f"  정확도: {acc.get('accuracy_pct', 0):.1f}%")
            lines.append(f"  예측 건수: {acc.get('total_predictions', 0)}건")

        lines.append("=" * 70)
        logger.info("\n".join(lines))


class MonthlyTrendReport:
    """
    월간 트렌드 리포트
    - 카테고리별 판매 비중
    - 폐기율, 재고 회전일
    - 요일별 패턴 요약
    """

    def __init__(self, store_id: Optional[str] = None) -> None:
        self.store_id = store_id

    def generate(self, year: Optional[int] = None, month: Optional[int] = None) -> Dict[str, Any]:
        """
        월간 트렌드 리포트 생성

        Args:
            year: 연도, 기본값: 현재 연도
            month: 월, 기본값: 전월

        Returns:
            리포트 데이터
        """
        if year is None or month is None:
            # 전월 기준
            today = datetime.now()
            first_of_this_month = today.replace(day=1)
            last_of_prev_month = first_of_this_month - timedelta(days=1)
            year = last_of_prev_month.year
            month = last_of_prev_month.month

        # 해당 월의 시작일/종료일
        start_date = f"{year}-{month:02d}-01"
        if month == 12:
            end_date = f"{year + 1}-01-01"
        else:
            end_date = f"{year}-{month + 1:02d}-01"

        # 종료일 전날까지
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=1)
        end_date = end_dt.strftime("%Y-%m-%d")

        report = {
            "report_type": "monthly",
            "period": f"{year}년 {month}월",
            "start_date": start_date,
            "end_date": end_date,
            "generated_at": datetime.now().isoformat(),
            "category_share": self._get_category_share(start_date, end_date),
            "disuse_rate": self._get_disuse_rate(start_date, end_date),
            "inventory_turnover": self._get_inventory_turnover(start_date, end_date),
            "weekday_pattern": self._get_weekday_pattern(start_date, end_date),
            "summary": self._get_monthly_summary(start_date, end_date),
        }

        return report

    def _get_category_share(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """카테고리별 판매 비중"""
        sf, sp = store_filter("ds", self.store_id)
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT
                ds.mid_cd,
                mc.mid_nm,
                SUM(ds.sale_qty) as total_sales,
                COUNT(DISTINCT ds.item_cd) as item_count,
                COUNT(DISTINCT ds.sales_date) as days_count
            FROM daily_sales ds
            LEFT JOIN mid_categories mc ON ds.mid_cd = mc.mid_cd
            WHERE ds.sales_date BETWEEN ? AND ?
            {sf}
            GROUP BY ds.mid_cd, mc.mid_nm
            ORDER BY total_sales DESC
        """, (start_date, end_date) + sp)

        rows = cursor.fetchall()
        conn.close()

        # 총 판매량
        total = sum(row[2] or 0 for row in rows)

        results = []
        for row in rows:
            sales = row[2] or 0
            share = round(sales / total * 100, 1) if total > 0 else 0

            results.append({
                "mid_cd": row[0],
                "mid_nm": row[1] or row[0],
                "total_sales": sales,
                "share_pct": share,
                "item_count": row[3],
                "days_count": row[4],
            })

        return results

    def _get_disuse_rate(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """카테고리별 폐기율"""
        sf, sp = store_filter("ds", self.store_id)
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT
                ds.mid_cd,
                mc.mid_nm,
                SUM(ds.sale_qty) as total_sales,
                SUM(ds.disuse_qty) as total_disuse
            FROM daily_sales ds
            LEFT JOIN mid_categories mc ON ds.mid_cd = mc.mid_cd
            WHERE ds.sales_date BETWEEN ? AND ?
            {sf}
            GROUP BY ds.mid_cd, mc.mid_nm
            HAVING total_sales > 0 OR total_disuse > 0
            ORDER BY total_disuse DESC
        """, (start_date, end_date) + sp)

        rows = cursor.fetchall()
        conn.close()

        results = []
        for row in rows:
            sales = row[2] or 0
            disuse = row[3] or 0
            total = sales + disuse

            if total > 0:
                disuse_rate = round(disuse / total * 100, 1)
            else:
                disuse_rate = 0

            if disuse > 0:  # 폐기가 있는 카테고리만
                results.append({
                    "mid_cd": row[0],
                    "mid_nm": row[1] or row[0],
                    "total_sales": sales,
                    "total_disuse": disuse,
                    "disuse_rate": disuse_rate,
                })

        # 폐기율 내림차순
        results.sort(key=lambda x: x["disuse_rate"], reverse=True)
        return results

    def _get_inventory_turnover(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """카테고리별 재고 회전일"""
        sf, sp = store_filter("ds", self.store_id)
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT
                ds.mid_cd,
                mc.mid_nm,
                SUM(ds.sale_qty) as total_sales,
                AVG(ds.stock_qty) as avg_stock,
                COUNT(DISTINCT ds.sales_date) as days_count
            FROM daily_sales ds
            LEFT JOIN mid_categories mc ON ds.mid_cd = mc.mid_cd
            WHERE ds.sales_date BETWEEN ? AND ?
            {sf}
            GROUP BY ds.mid_cd, mc.mid_nm
            HAVING avg_stock > 0
            ORDER BY total_sales DESC
        """, (start_date, end_date) + sp)

        rows = cursor.fetchall()
        conn.close()

        results = []
        for row in rows:
            sales = row[2] or 0
            avg_stock = row[3] or 0
            days = row[4] or 1

            # 일평균 판매량
            daily_avg = sales / days if days > 0 else 0

            # 재고 회전일 = 평균재고 / 일평균판매량
            if daily_avg > 0:
                turnover_days = round(avg_stock / daily_avg, 1)
            else:
                turnover_days = None

            results.append({
                "mid_cd": row[0],
                "mid_nm": row[1] or row[0],
                "avg_stock": round(avg_stock, 1),
                "daily_avg_sales": round(daily_avg, 1),
                "turnover_days": turnover_days,
            })

        # 회전일 오름차순 (빠른 회전 = 좋음)
        results.sort(key=lambda x: x["turnover_days"] if x["turnover_days"] else 999)
        return results

    def _get_weekday_pattern(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """요일별 판매 패턴"""
        sf, sp = store_filter("ds", self.store_id)
        conn = get_connection()
        cursor = conn.cursor()

        # SQLite에서 요일 추출 (0=일, 6=토)
        cursor.execute(f"""
            SELECT
                CAST(strftime('%w', ds.sales_date) AS INTEGER) as weekday,
                SUM(ds.sale_qty) as total_sales,
                COUNT(DISTINCT ds.sales_date) as days_count
            FROM daily_sales ds
            WHERE ds.sales_date BETWEEN ? AND ?
            {sf}
            GROUP BY weekday
            ORDER BY weekday
        """, (start_date, end_date) + sp)

        rows = cursor.fetchall()
        conn.close()

        weekday_names = ["일", "월", "화", "수", "목", "금", "토"]
        pattern = {}

        total_sales = sum(row[1] or 0 for row in rows)

        for row in rows:
            weekday = row[0]
            sales = row[1] or 0
            days = row[2] or 1

            avg_sales = sales / days if days > 0 else 0
            share = round(sales / total_sales * 100, 1) if total_sales > 0 else 0

            pattern[weekday_names[weekday]] = {
                "total_sales": sales,
                "days_count": days,
                "avg_daily_sales": round(avg_sales, 1),
                "share_pct": share,
            }

        # 판매량 기준 최고/최저 요일
        if pattern:
            best_day = max(pattern.items(), key=lambda x: x[1]["avg_daily_sales"])
            worst_day = min(pattern.items(), key=lambda x: x[1]["avg_daily_sales"])
        else:
            best_day = ("N/A", {"avg_daily_sales": 0})
            worst_day = ("N/A", {"avg_daily_sales": 0})

        return {
            "by_weekday": pattern,
            "best_day": {"day": best_day[0], **best_day[1]},
            "worst_day": {"day": worst_day[0], **worst_day[1]},
        }

    def _get_monthly_summary(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """월간 요약"""
        sf, sp = store_filter("", self.store_id)
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT
                COUNT(DISTINCT item_cd) as total_items,
                COUNT(DISTINCT mid_cd) as total_categories,
                SUM(sale_qty) as total_sales,
                SUM(ord_qty) as total_orders,
                SUM(disuse_qty) as total_disuse,
                AVG(stock_qty) as avg_stock,
                COUNT(DISTINCT sales_date) as days_collected
            FROM daily_sales
            WHERE sales_date BETWEEN ? AND ?
            {sf}
        """, (start_date, end_date) + sp)

        row = cursor.fetchone()
        conn.close()

        if row and row[0]:
            total_sales = row[2] or 0
            total_disuse = row[4] or 0
            total = total_sales + total_disuse

            return {
                "total_items": row[0],
                "total_categories": row[1],
                "total_sales": total_sales,
                "total_orders": row[3] or 0,
                "total_disuse": total_disuse,
                "disuse_rate": round(total_disuse / total * 100, 1) if total > 0 else 0,
                "avg_stock": round(row[5], 1) if row[5] else 0,
                "days_collected": row[6],
            }

        return {
            "total_items": 0,
            "total_categories": 0,
            "total_sales": 0,
            "total_orders": 0,
            "total_disuse": 0,
            "disuse_rate": 0,
            "avg_stock": 0,
            "days_collected": 0,
        }

    def format_message(self, report: Dict[str, Any]) -> str:
        """카카오톡 메시지 형식으로 변환"""
        lines = []
        lines.append(f"[BGF 월간 트렌드 리포트]")
        lines.append(f"기간: {report['period']}")
        lines.append("")

        # 요약
        s = report["summary"]
        lines.append("[통계] 월간 요약")
        lines.append(f"  총 판매: {s['total_sales']:,}개")
        lines.append(f"  총 폐기: {s['total_disuse']:,}개 ({s['disuse_rate']}%)")
        lines.append(f"  수집일: {s['days_collected']}일")
        lines.append("")

        # 카테고리 TOP 5
        lines.append("[TOP] 카테고리 TOP 5")
        for i, cat in enumerate(report["category_share"][:5], 1):
            lines.append(f"  {i}. {cat['mid_nm'][:8]} {cat['share_pct']}%")
        lines.append("")

        # 폐기율 TOP 3
        if report["disuse_rate"]:
            lines.append("[주의] 폐기율 TOP 3")
            for i, cat in enumerate(report["disuse_rate"][:3], 1):
                lines.append(f"  {i}. {cat['mid_nm'][:8]} {cat['disuse_rate']}%")
            lines.append("")

        # 요일별 패턴
        wp = report["weekday_pattern"]
        lines.append("[요일] 요일별 패턴")
        lines.append(f"  최고: {wp['best_day']['day']}요일 ({wp['best_day']['avg_daily_sales']:,.0f}개/일)")
        lines.append(f"  최저: {wp['worst_day']['day']}요일 ({wp['worst_day']['avg_daily_sales']:,.0f}개/일)")

        lines.append("")
        lines.append(f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        return "\n".join(lines)

    def print_report(self, report: Dict[str, Any]) -> None:
        """콘솔 출력 (logger.info로 통합 로깅)"""
        lines = []
        lines.append("=" * 70)
        lines.append(f"[MONTHLY TREND REPORT] {report['period']}")
        lines.append(f"기간: {report['start_date']} ~ {report['end_date']}")
        lines.append("=" * 70)

        s = report["summary"]
        lines.append("[월간 요약]")
        lines.append(f"  총 상품: {s['total_items']:,}개")
        lines.append(f"  총 판매: {s['total_sales']:,}개")
        lines.append(f"  총 폐기: {s['total_disuse']:,}개 ({s['disuse_rate']}%)")
        lines.append(f"  평균 재고: {s['avg_stock']:,.1f}개")
        lines.append(f"  수집일: {s['days_collected']}일")

        lines.append("[카테고리별 판매 비중]")
        lines.append(f"  {'카테고리':<20} {'판매량':>10} {'비중':>8} {'상품수':>8}")
        lines.append("  " + "-" * 50)
        for cat in report["category_share"][:10]:
            name = (cat["mid_nm"] or "")[:18]
            lines.append(f"  {name:<20} {cat['total_sales']:>10,} {cat['share_pct']:>7.1f}% {cat['item_count']:>8}")

        if report["disuse_rate"]:
            lines.append("[폐기율 TOP 10]")
            lines.append(f"  {'카테고리':<20} {'판매':>10} {'폐기':>8} {'폐기율':>8}")
            lines.append("  " + "-" * 50)
            for cat in report["disuse_rate"][:10]:
                name = (cat["mid_nm"] or "")[:18]
                lines.append(f"  {name:<20} {cat['total_sales']:>10,} {cat['total_disuse']:>8} {cat['disuse_rate']:>7.1f}%")

        lines.append("[재고 회전일]")
        lines.append(f"  {'카테고리':<20} {'평균재고':>10} {'일판매':>8} {'회전일':>8}")
        lines.append("  " + "-" * 50)
        for cat in report["inventory_turnover"][:10]:
            name = (cat["mid_nm"] or "")[:18]
            turnover = f"{cat['turnover_days']:.1f}일" if cat["turnover_days"] else "N/A"
            lines.append(f"  {name:<20} {cat['avg_stock']:>10.1f} {cat['daily_avg_sales']:>8.1f} {turnover:>8}")

        lines.append("[요일별 판매 패턴]")
        wp = report["weekday_pattern"]["by_weekday"]
        lines.append(f"  {'요일':<4} {'총판매':>10} {'일수':>6} {'일평균':>10} {'비중':>8}")
        lines.append("  " + "-" * 45)
        for day in ["일", "월", "화", "수", "목", "금", "토"]:
            if day in wp:
                d = wp[day]
                lines.append(f"  {day}요일  {d['total_sales']:>10,} {d['days_count']:>6} {d['avg_daily_sales']:>10,.1f} {d['share_pct']:>7.1f}%")

        best = report["weekday_pattern"]["best_day"]
        worst = report["weekday_pattern"]["worst_day"]
        lines.append(f"  최고: {best['day']}요일 (일평균 {best['avg_daily_sales']:,.1f}개)")
        lines.append(f"  최저: {worst['day']}요일 (일평균 {worst['avg_daily_sales']:,.1f}개)")

        lines.append("=" * 70)
        logger.info("\n".join(lines))


class QuarterlyTrendReport:
    """
    분기별 트렌드 리포트
    - 전분기 대비 비교
    - 시즌 트렌드
    - 발주 전략 제안
    """

    def __init__(self, store_id: Optional[str] = None) -> None:
        self.store_id = store_id

    def generate(self, year: Optional[int] = None, quarter: Optional[int] = None) -> Dict[str, Any]:
        """
        분기별 트렌드 리포트 생성

        Args:
            year: 연도, 기본값: 현재 연도
            quarter: 분기 (1-4), 기본값: 전분기

        Returns:
            리포트 데이터
        """
        if year is None or quarter is None:
            # 전분기 기준
            today = datetime.now()
            current_quarter = (today.month - 1) // 3 + 1

            if current_quarter == 1:
                year = today.year - 1
                quarter = 4
            else:
                year = today.year
                quarter = current_quarter - 1

        # 분기 시작/종료일
        quarter_months = {
            1: (1, 3),
            2: (4, 6),
            3: (7, 9),
            4: (10, 12),
        }

        start_month, end_month = quarter_months[quarter]
        start_date = f"{year}-{start_month:02d}-01"

        if end_month == 12:
            end_date = f"{year + 1}-01-01"
        else:
            end_date = f"{year}-{end_month + 1:02d}-01"

        end_dt = datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=1)
        end_date = end_dt.strftime("%Y-%m-%d")

        # 전분기 계산
        if quarter == 1:
            prev_year = year - 1
            prev_quarter = 4
        else:
            prev_year = year
            prev_quarter = quarter - 1

        prev_start_month, prev_end_month = quarter_months[prev_quarter]
        prev_start_date = f"{prev_year}-{prev_start_month:02d}-01"

        if prev_end_month == 12:
            prev_end_date = f"{prev_year + 1}-01-01"
        else:
            prev_end_date = f"{prev_year}-{prev_end_month + 1:02d}-01"

        prev_end_dt = datetime.strptime(prev_end_date, "%Y-%m-%d") - timedelta(days=1)
        prev_end_date = prev_end_dt.strftime("%Y-%m-%d")

        report = {
            "report_type": "quarterly",
            "period": f"{year}년 {quarter}분기",
            "start_date": start_date,
            "end_date": end_date,
            "prev_period": f"{prev_year}년 {prev_quarter}분기",
            "prev_start_date": prev_start_date,
            "prev_end_date": prev_end_date,
            "generated_at": datetime.now().isoformat(),
            "quarter_comparison": self._get_quarter_comparison(
                start_date, end_date, prev_start_date, prev_end_date
            ),
            "seasonal_trend": self._get_seasonal_trend(year, quarter),
            "category_growth": self._get_category_quarterly_growth(
                start_date, end_date, prev_start_date, prev_end_date
            ),
            "order_strategy": self._generate_order_strategy(
                start_date, end_date, year, quarter
            ),
            "summary": self._get_quarterly_summary(start_date, end_date),
        }

        return report

    def _get_quarter_comparison(
        self,
        this_start: str,
        this_end: str,
        prev_start: str,
        prev_end: str
    ) -> Dict[str, Any]:
        """전분기 대비 비교"""
        sf, sp = store_filter("", self.store_id)
        conn = get_connection()
        cursor = conn.cursor()

        # 이번 분기
        cursor.execute(f"""
            SELECT
                SUM(sale_qty) as total_sales,
                SUM(ord_qty) as total_orders,
                SUM(disuse_qty) as total_disuse,
                AVG(stock_qty) as avg_stock,
                COUNT(DISTINCT sales_date) as days_count
            FROM daily_sales
            WHERE sales_date BETWEEN ? AND ?
            {sf}
        """, (this_start, this_end) + sp)

        this_row = cursor.fetchone()

        # 전분기
        cursor.execute(f"""
            SELECT
                SUM(sale_qty) as total_sales,
                SUM(ord_qty) as total_orders,
                SUM(disuse_qty) as total_disuse,
                AVG(stock_qty) as avg_stock,
                COUNT(DISTINCT sales_date) as days_count
            FROM daily_sales
            WHERE sales_date BETWEEN ? AND ?
            {sf}
        """, (prev_start, prev_end) + sp)

        prev_row = cursor.fetchone()
        conn.close()

        def calc_growth(this_val: int, prev_val: int) -> Optional[float]:
            if prev_val and prev_val > 0:
                return round((this_val - prev_val) / prev_val * 100, 1)
            return None

        this_sales = this_row[0] or 0 if this_row else 0
        this_orders = this_row[1] or 0 if this_row else 0
        this_disuse = this_row[2] or 0 if this_row else 0

        prev_sales = prev_row[0] or 0 if prev_row else 0
        prev_orders = prev_row[1] or 0 if prev_row else 0
        prev_disuse = prev_row[2] or 0 if prev_row else 0

        return {
            "this_quarter": {
                "total_sales": this_sales,
                "total_orders": this_orders,
                "total_disuse": this_disuse,
                "days_collected": this_row[4] if this_row else 0,
            },
            "prev_quarter": {
                "total_sales": prev_sales,
                "total_orders": prev_orders,
                "total_disuse": prev_disuse,
                "days_collected": prev_row[4] if prev_row else 0,
            },
            "growth": {
                "sales_growth": calc_growth(this_sales, prev_sales),
                "orders_growth": calc_growth(this_orders, prev_orders),
                "disuse_growth": calc_growth(this_disuse, prev_disuse),
            }
        }

    def _get_seasonal_trend(self, year: int, quarter: int) -> Dict[str, Any]:
        """시즌 트렌드"""
        season_names = {
            1: "봄 (1~3월)",
            2: "여름 (4~6월)",
            3: "가을 (7~9월)",
            4: "겨울 (10~12월)",
        }

        season_characteristics = {
            1: ["신학기 시즌", "화이트데이", "봄나들이"],
            2: ["여름 음료 시즌", "아이스크림 성수기", "휴가철"],
            3: ["추석 시즌", "가을 나들이", "수능 시즌"],
            4: ["연말 시즌", "크리스마스", "설날 준비"],
        }

        season_recommendations = {
            1: ["음료류 재고 확대", "간식류 프로모션", "신제품 테스트"],
            2: ["아이스크림 재고 2배", "음료 냉장고 확대", "야외 간식 비축"],
            3: ["선물세트 비축", "추석 관련 상품 확대", "따뜻한 음료 준비"],
            4: ["연말 선물 세트", "핫초코/커피 확대", "설날 상품 준비"],
        }

        return {
            "season": season_names.get(quarter, "Unknown"),
            "characteristics": season_characteristics.get(quarter, []),
            "recommendations": season_recommendations.get(quarter, []),
        }

    def _get_category_quarterly_growth(
        self,
        this_start: str,
        this_end: str,
        prev_start: str,
        prev_end: str
    ) -> List[Dict[str, Any]]:
        """카테고리별 분기 성장률"""
        sf, sp = store_filter("ds", self.store_id)
        conn = get_connection()
        cursor = conn.cursor()

        # 이번 분기
        cursor.execute(f"""
            SELECT
                ds.mid_cd,
                mc.mid_nm,
                SUM(ds.sale_qty) as total_sales
            FROM daily_sales ds
            LEFT JOIN mid_categories mc ON ds.mid_cd = mc.mid_cd
            WHERE ds.sales_date BETWEEN ? AND ?
            {sf}
            GROUP BY ds.mid_cd, mc.mid_nm
        """, (this_start, this_end) + sp)

        this_quarter = {row[0]: {"mid_nm": row[1], "sales": row[2]} for row in cursor.fetchall()}

        # 전분기
        cursor.execute(f"""
            SELECT ds.mid_cd, SUM(ds.sale_qty) as total_sales
            FROM daily_sales ds
            WHERE ds.sales_date BETWEEN ? AND ?
            {sf}
            GROUP BY ds.mid_cd
        """, (prev_start, prev_end) + sp)

        prev_quarter = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()

        results = []
        for mid_cd, data in this_quarter.items():
            this_sales = data["sales"] or 0
            prev_sales = prev_quarter.get(mid_cd, 0) or 0

            if prev_sales > 0:
                growth = round((this_sales - prev_sales) / prev_sales * 100, 1)
            else:
                growth = None

            results.append({
                "mid_cd": mid_cd,
                "mid_nm": data["mid_nm"] or mid_cd,
                "this_quarter_sales": this_sales,
                "prev_quarter_sales": prev_sales,
                "growth_rate": growth,
            })

        results.sort(key=lambda x: x["this_quarter_sales"], reverse=True)
        return results

    def _generate_order_strategy(
        self,
        start_date: str,
        end_date: str,
        year: int,
        quarter: int
    ) -> List[Dict[str, Any]]:
        """발주 전략 제안"""
        sf, sp = store_filter("ds", self.store_id)
        conn = get_connection()
        cursor = conn.cursor()

        # 폐기율 높은 카테고리 (10% 이상)
        cursor.execute(f"""
            SELECT
                ds.mid_cd,
                mc.mid_nm,
                SUM(ds.sale_qty) as total_sales,
                SUM(ds.disuse_qty) as total_disuse
            FROM daily_sales ds
            LEFT JOIN mid_categories mc ON ds.mid_cd = mc.mid_cd
            WHERE ds.sales_date BETWEEN ? AND ?
            {sf}
            GROUP BY ds.mid_cd, mc.mid_nm
            HAVING total_disuse > 0
        """, (start_date, end_date) + sp)

        high_disuse = []
        for row in cursor.fetchall():
            sales = row[2] or 0
            disuse = row[3] or 0
            total = sales + disuse

            if total > 0:
                rate = disuse / total * 100
                if rate >= 10:
                    high_disuse.append({
                        "mid_cd": row[0],
                        "mid_nm": row[1] or row[0],
                        "disuse_rate": round(rate, 1),
                    })

        conn.close()

        strategies = []

        # 폐기율 관련 전략
        for cat in high_disuse[:5]:
            strategies.append({
                "category": cat["mid_nm"],
                "issue": f"폐기율 {cat['disuse_rate']}%",
                "action": "발주량 10~20% 감소 권장",
                "priority": "높음" if cat["disuse_rate"] >= 20 else "중간",
            })

        # 시즌 관련 전략
        next_quarter = quarter + 1 if quarter < 4 else 1
        next_year = year if quarter < 4 else year + 1

        season_strategies = {
            1: {"category": "음료/아이스크림", "action": "여름 대비 재고 확대 준비", "priority": "중간"},
            2: {"category": "추석 관련 상품", "action": "선물세트 사전 발주", "priority": "높음"},
            3: {"category": "따뜻한 음료", "action": "핫초코/커피 재고 확대", "priority": "중간"},
            4: {"category": "봄 시즌 상품", "action": "신학기/화이트데이 상품 준비", "priority": "중간"},
        }

        if next_quarter in season_strategies:
            s = season_strategies[next_quarter]
            strategies.append({
                "category": s["category"],
                "issue": f"다음 분기({next_year}년 {next_quarter}분기) 대비",
                "action": s["action"],
                "priority": s["priority"],
            })

        return strategies

    def _get_quarterly_summary(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """분기 요약"""
        sf, sp = store_filter("", self.store_id)
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT
                COUNT(DISTINCT item_cd) as total_items,
                COUNT(DISTINCT mid_cd) as total_categories,
                SUM(sale_qty) as total_sales,
                SUM(ord_qty) as total_orders,
                SUM(disuse_qty) as total_disuse,
                AVG(stock_qty) as avg_stock,
                COUNT(DISTINCT sales_date) as days_collected
            FROM daily_sales
            WHERE sales_date BETWEEN ? AND ?
            {sf}
        """, (start_date, end_date) + sp)

        row = cursor.fetchone()
        conn.close()

        if row and row[0]:
            total_sales = row[2] or 0
            total_disuse = row[4] or 0
            total = total_sales + total_disuse

            return {
                "total_items": row[0],
                "total_categories": row[1],
                "total_sales": total_sales,
                "total_orders": row[3] or 0,
                "total_disuse": total_disuse,
                "disuse_rate": round(total_disuse / total * 100, 1) if total > 0 else 0,
                "avg_stock": round(row[5], 1) if row[5] else 0,
                "days_collected": row[6],
            }

        return {}

    def format_message(self, report: Dict[str, Any]) -> str:
        """카카오톡 메시지 형식으로 변환"""
        lines = []
        lines.append(f"[BGF 분기 트렌드 리포트]")
        lines.append(f"기간: {report['period']}")
        lines.append("")

        # 전분기 대비
        comp = report["quarter_comparison"]
        growth = comp["growth"]
        lines.append("[통계] 전분기 대비")
        if growth["sales_growth"] is not None:
            sign = "+" if growth["sales_growth"] >= 0 else ""
            lines.append(f"  판매: {sign}{growth['sales_growth']}%")
        if growth["disuse_growth"] is not None:
            sign = "+" if growth["disuse_growth"] >= 0 else ""
            lines.append(f"  폐기: {sign}{growth['disuse_growth']}%")
        lines.append("")

        # 시즌 트렌드
        season = report["seasonal_trend"]
        lines.append(f"🌸 시즌: {season['season']}")
        for char in season["characteristics"][:2]:
            lines.append(f"  - {char}")
        lines.append("")

        # 발주 전략
        strategies = report["order_strategy"][:3]
        if strategies:
            lines.append("[전략] 발주 전략 제안")
            for s in strategies:
                lines.append(f"  [{s['priority']}] {s['category']}")
                lines.append(f"    → {s['action']}")

        lines.append("")
        lines.append(f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        return "\n".join(lines)

    def print_report(self, report: Dict[str, Any]) -> None:
        """콘솔 출력 (logger.info로 통합 로깅)"""
        lines = []
        lines.append("=" * 70)
        lines.append(f"[QUARTERLY TREND REPORT] {report['period']}")
        lines.append(f"기간: {report['start_date']} ~ {report['end_date']}")
        lines.append(f"전분기: {report['prev_period']}")
        lines.append("=" * 70)

        s = report.get("summary", {})
        lines.append("[분기 요약]")
        lines.append(f"  총 판매: {s.get('total_sales', 0):,}개")
        lines.append(f"  총 폐기: {s.get('total_disuse', 0):,}개 ({s.get('disuse_rate', 0)}%)")
        lines.append(f"  수집일: {s.get('days_collected', 0)}일")

        comp = report["quarter_comparison"]
        growth = comp["growth"]
        lines.append("[전분기 대비]")
        lines.append(f"  {'항목':<10} {'이번분기':>12} {'전분기':>12} {'성장률':>10}")
        lines.append("  " + "-" * 50)

        this_q = comp["this_quarter"]
        prev_q = comp["prev_quarter"]

        def fmt_growth(val: Optional[float]) -> str:
            if val is None:
                return "N/A"
            sign = "+" if val >= 0 else ""
            return f"{sign}{val}%"

        lines.append(f"  {'판매':<10} {this_q['total_sales']:>12,} {prev_q['total_sales']:>12,} {fmt_growth(growth['sales_growth']):>10}")
        lines.append(f"  {'발주':<10} {this_q['total_orders']:>12,} {prev_q['total_orders']:>12,} {fmt_growth(growth['orders_growth']):>10}")
        lines.append(f"  {'폐기':<10} {this_q['total_disuse']:>12,} {prev_q['total_disuse']:>12,} {fmt_growth(growth['disuse_growth']):>10}")

        lines.append("[카테고리 성장률]")
        lines.append(f"  {'카테고리':<20} {'이번분기':>10} {'전분기':>10} {'성장률':>10}")
        lines.append("  " + "-" * 55)
        for cat in report["category_growth"][:10]:
            name = (cat["mid_nm"] or "")[:18]
            growth_str = fmt_growth(cat["growth_rate"])
            lines.append(f"  {name:<20} {cat['this_quarter_sales']:>10,} {cat['prev_quarter_sales']:>10,} {growth_str:>10}")

        season = report["seasonal_trend"]
        lines.append("[시즌 트렌드]")
        lines.append(f"  시즌: {season['season']}")
        lines.append("  특징:")
        for char in season["characteristics"]:
            lines.append(f"    - {char}")
        lines.append("  권장사항:")
        for rec in season["recommendations"]:
            lines.append(f"    - {rec}")

        if report["order_strategy"]:
            lines.append("[발주 전략 제안]")
            for strat in report["order_strategy"]:
                lines.append(f"  [{strat['priority']}] {strat['category']}")
                lines.append(f"    이슈: {strat['issue']}")
                lines.append(f"    조치: {strat['action']}")

        lines.append("=" * 70)
        logger.info("\n".join(lines))


class ReportScheduler:
    """
    리포트 스케줄러
    - 주간: 매주 월요일 08:00
    - 월간: 매월 1일 09:00
    - 분기: 1/4/7/10월 1일 10:00
    """

    def __init__(self, store_id: Optional[str] = None) -> None:
        self.store_id = store_id
        self.weekly_reporter = WeeklyTrendReport(store_id=store_id)
        self.monthly_reporter = MonthlyTrendReport(store_id=store_id)
        self.quarterly_reporter = QuarterlyTrendReport(store_id=store_id)
        self.notifier: Optional[KakaoNotifier] = None

    def _get_notifier(self) -> Optional[KakaoNotifier]:
        """카카오 노티파이어 가져오기"""
        if self.notifier is None:
            self.notifier = KakaoNotifier(DEFAULT_REST_API_KEY)

        if not self.notifier.access_token:
            logger.warning("[ReportScheduler] 카카오 토큰 없음")
            return None

        return self.notifier

    def send_weekly_report(self) -> Dict[str, Any]:
        """주간 리포트 발송"""
        logger.info("[ReportScheduler] 주간 트렌드 리포트 생성 중...")

        try:
            report = self.weekly_reporter.generate()
            message = self.weekly_reporter.format_message(report)

            logger.info("[ReportScheduler] 메시지 생성 완료")

            notifier = self._get_notifier()
            if notifier:
                notifier.send_message(message)
                logger.info("[ReportScheduler] 카카오톡 발송 완료")
                return {"success": True, "report": report}
            else:
                logger.warning("[ReportScheduler] 카카오톡 발송 실패 (토큰 없음)")
                return {"success": False, "error": "No Kakao token", "report": report}

        except Exception as e:
            logger.error(f"[ReportScheduler] 오류: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def send_monthly_report(self) -> Dict[str, Any]:
        """월간 리포트 발송"""
        logger.info("[ReportScheduler] 월간 트렌드 리포트 생성 중...")

        try:
            report = self.monthly_reporter.generate()
            message = self.monthly_reporter.format_message(report)

            logger.info("[ReportScheduler] 메시지 생성 완료")

            notifier = self._get_notifier()
            if notifier:
                notifier.send_message(message)
                logger.info("[ReportScheduler] 카카오톡 발송 완료")
                return {"success": True, "report": report}
            else:
                logger.warning("[ReportScheduler] 카카오톡 발송 실패 (토큰 없음)")
                return {"success": False, "error": "No Kakao token", "report": report}

        except Exception as e:
            logger.error(f"[ReportScheduler] 오류: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def send_quarterly_report(self) -> Dict[str, Any]:
        """분기 리포트 발송"""
        logger.info("[ReportScheduler] 분기 트렌드 리포트 생성 중...")

        try:
            report = self.quarterly_reporter.generate()
            message = self.quarterly_reporter.format_message(report)

            logger.info("[ReportScheduler] 메시지 생성 완료")

            notifier = self._get_notifier()
            if notifier:
                notifier.send_message(message)
                logger.info("[ReportScheduler] 카카오톡 발송 완료")
                return {"success": True, "report": report}
            else:
                logger.warning("[ReportScheduler] 카카오톡 발송 실패 (토큰 없음)")
                return {"success": False, "error": "No Kakao token", "report": report}

        except Exception as e:
            logger.error(f"[ReportScheduler] 오류: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def check_and_send_scheduled_reports(self) -> Dict[str, Any]:
        """
        스케줄 확인 및 리포트 발송

        - 주간: 매주 월요일 08:00
        - 월간: 매월 1일 09:00
        - 분기: 1/4/7/10월 1일 10:00

        Returns:
            발송 결과
        """
        now = datetime.now()
        results = {}

        # 주간 리포트: 월요일 08:00
        if now.weekday() == 0 and now.hour == 8:
            results["weekly"] = self.send_weekly_report()

        # 월간 리포트: 매월 1일 09:00
        if now.day == 1 and now.hour == 9:
            results["monthly"] = self.send_monthly_report()

        # 분기 리포트: 1/4/7/10월 1일 10:00
        if now.month in [1, 4, 7, 10] and now.day == 1 and now.hour == 10:
            results["quarterly"] = self.send_quarterly_report()

        return results

    def get_schedule_info(self) -> Dict[str, str]:
        """스케줄 정보 반환"""
        return {
            "weekly": "매주 월요일 08:00",
            "monthly": "매월 1일 09:00",
            "quarterly": "1/4/7/10월 1일 10:00",
        }


# =============================================================================
# 헬퍼 함수
# =============================================================================

def send_weekly_trend_report() -> Dict[str, Any]:
    """주간 트렌드 리포트 발송 헬퍼 함수"""
    scheduler = ReportScheduler()
    return scheduler.send_weekly_report()


def send_monthly_trend_report() -> Dict[str, Any]:
    """월간 트렌드 리포트 발송 헬퍼 함수"""
    scheduler = ReportScheduler()
    return scheduler.send_monthly_report()


def send_quarterly_trend_report() -> Dict[str, Any]:
    """분기 트렌드 리포트 발송 헬퍼 함수"""
    scheduler = ReportScheduler()
    return scheduler.send_quarterly_report()


# =============================================================================
# CLI
# =============================================================================

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Trend Report Generator")
    parser.add_argument("--weekly", "-w", action="store_true", help="Generate weekly report")
    parser.add_argument("--monthly", "-m", action="store_true", help="Generate monthly report")
    parser.add_argument("--quarterly", "-q", action="store_true", help="Generate quarterly report")
    parser.add_argument("--send", "-s", action="store_true", help="Send via KakaoTalk")
    parser.add_argument("--date", "-d", type=str, help="End date for weekly (YYYY-MM-DD)")
    parser.add_argument("--year", "-y", type=int, help="Year for monthly/quarterly")
    parser.add_argument("--month", type=int, help="Month for monthly report")
    parser.add_argument("--quarter", type=int, choices=[1, 2, 3, 4], help="Quarter for quarterly report")

    args = parser.parse_args()

    if args.weekly:
        reporter = WeeklyTrendReport()
        report = reporter.generate(args.date)
        reporter.print_report(report)

        if args.send:
            scheduler = ReportScheduler()
            scheduler.send_weekly_report()

    elif args.monthly:
        reporter = MonthlyTrendReport()
        report = reporter.generate(args.year, args.month)
        reporter.print_report(report)

        if args.send:
            scheduler = ReportScheduler()
            scheduler.send_monthly_report()

    elif args.quarterly:
        reporter = QuarterlyTrendReport()
        report = reporter.generate(args.year, args.quarter)
        reporter.print_report(report)

        if args.send:
            scheduler = ReportScheduler()
            scheduler.send_quarterly_report()

    else:
        # 기본: 주간 리포트
        reporter = WeeklyTrendReport()
        report = reporter.generate()
        reporter.print_report(report)


if __name__ == "__main__":
    main()
