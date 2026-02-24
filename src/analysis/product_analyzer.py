# -*- coding: utf-8 -*-
"""
상품 분석 모듈
- 상품별 성장률 분석
- 카테고리 내 판매 순위
- 급상승/급하락 상품 감지
- 신규 인기 상품 발굴

Usage:
    from product_analyzer import ProductAnalyzer
    analyzer = ProductAnalyzer()
    result = analyzer.analyze()
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

# 상위 경로 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import get_connection
from db.store_query import store_filter


class ProductAnalyzer:
    """
    상품 분석기
    - 상품별 성장률 (전주 대비)
    - 카테고리 내 판매 순위
    - 급상승/급하락 상품 감지 (±30% 이상)
    - 신규 인기 상품 발굴
    """

    def __init__(self, threshold_pct: float = 30.0, store_id: Optional[str] = None) -> None:
        """
        Args:
            threshold_pct: 급상승/급하락 기준 (기본 30%)
            store_id: 매장 코드 (None이면 전체)
        """
        self.threshold_pct = threshold_pct
        self.store_id = store_id

    def _get_conn(self):
        """DB 연결 (매장 DB일 경우 common.db ATTACH)"""
        if self.store_id:
            from src.infrastructure.database.connection import (
                DBRouter, attach_common_with_views,
            )
            conn = DBRouter.get_store_connection(self.store_id)
            return attach_common_with_views(conn, self.store_id)
        return get_connection()

    def analyze(self, end_date: Optional[str] = None) -> Dict[str, Any]:
        """
        전체 분석 실행

        Args:
            end_date: 기준 종료일 (YYYY-MM-DD), 기본값: 어제

        Returns:
            분석 결과 dict
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

        result = {
            "period": {
                "this_week": f"{this_week_start.strftime('%Y-%m-%d')} ~ {this_week_end.strftime('%Y-%m-%d')}",
                "prev_week": f"{prev_week_start.strftime('%Y-%m-%d')} ~ {prev_week_end.strftime('%Y-%m-%d')}",
            },
            "analyzed_at": datetime.now().isoformat(),
            "product_growth": self.get_product_growth(
                this_week_start.strftime("%Y-%m-%d"),
                this_week_end.strftime("%Y-%m-%d"),
                prev_week_start.strftime("%Y-%m-%d"),
                prev_week_end.strftime("%Y-%m-%d")
            ),
            "category_rankings": self.get_category_rankings(
                this_week_start.strftime("%Y-%m-%d"),
                this_week_end.strftime("%Y-%m-%d")
            ),
            "surge_products": [],  # 급상승 상품
            "plunge_products": [],  # 급하락 상품
            "new_popular": self.get_new_popular_products(
                this_week_start.strftime("%Y-%m-%d"),
                this_week_end.strftime("%Y-%m-%d"),
                prev_week_start.strftime("%Y-%m-%d"),
                prev_week_end.strftime("%Y-%m-%d")
            ),
        }

        # 급상승/급하락 상품 분류
        for product in result["product_growth"]:
            growth = product.get("growth_rate")
            if growth is not None:
                if growth >= self.threshold_pct:
                    result["surge_products"].append(product)
                elif growth <= -self.threshold_pct:
                    result["plunge_products"].append(product)

        # 급상승은 성장률 내림차순, 급하락은 성장률 오름차순
        result["surge_products"].sort(key=lambda x: x["growth_rate"], reverse=True)
        result["plunge_products"].sort(key=lambda x: x["growth_rate"])

        return result

    def get_product_growth(
        self,
        this_start: str,
        this_end: str,
        prev_start: str,
        prev_end: str
    ) -> List[Dict[str, Any]]:
        """
        상품별 성장률 (전주 대비)

        Args:
            this_start: 이번 주 시작일
            this_end: 이번 주 종료일
            prev_start: 전주 시작일
            prev_end: 전주 종료일

        Returns:
            상품별 성장률 리스트
        """
        sf, sp = store_filter("ds", self.store_id)
        conn = self._get_conn()
        cursor = conn.cursor()

        # 이번 주 상품별 판매량
        cursor.execute(f"""
            SELECT
                ds.item_cd,
                p.item_nm,
                ds.mid_cd,
                mc.mid_nm,
                SUM(ds.sale_qty) as total_sales,
                COUNT(DISTINCT ds.sales_date) as days_sold
            FROM daily_sales ds
            LEFT JOIN products p ON ds.item_cd = p.item_cd
            LEFT JOIN mid_categories mc ON ds.mid_cd = mc.mid_cd
            WHERE ds.sales_date BETWEEN ? AND ?
            {sf}
            GROUP BY ds.item_cd, p.item_nm, ds.mid_cd, mc.mid_nm
        """, (this_start, this_end) + sp)

        this_week = {}
        for row in cursor.fetchall():
            this_week[row[0]] = {
                "item_nm": row[1],
                "mid_cd": row[2],
                "mid_nm": row[3],
                "sales": row[4] or 0,
                "days_sold": row[5] or 0,
            }

        # 전주 상품별 판매량
        cursor.execute(f"""
            SELECT
                ds.item_cd,
                SUM(ds.sale_qty) as total_sales
            FROM daily_sales ds
            WHERE ds.sales_date BETWEEN ? AND ?
            {sf}
            GROUP BY ds.item_cd
        """, (prev_start, prev_end) + sp)

        prev_week = {row[0]: row[1] or 0 for row in cursor.fetchall()}
        conn.close()

        # 성장률 계산
        results = []
        for item_cd, data in this_week.items():
            this_sales = data["sales"]
            prev_sales = prev_week.get(item_cd, 0)

            if prev_sales > 0:
                growth_rate = round((this_sales - prev_sales) / prev_sales * 100, 1)
            elif this_sales > 0:
                growth_rate = 100.0  # 전주 0 → 이번주 판매 = 신규/복귀
            else:
                growth_rate = 0.0

            results.append({
                "item_cd": item_cd,
                "item_nm": data["item_nm"] or item_cd,
                "mid_cd": data["mid_cd"],
                "mid_nm": data["mid_nm"] or data["mid_cd"],
                "this_week_sales": this_sales,
                "prev_week_sales": prev_sales,
                "growth_rate": growth_rate,
                "days_sold": data["days_sold"],
            })

        # 이번 주 판매량 내림차순 정렬
        results.sort(key=lambda x: x["this_week_sales"], reverse=True)
        return results

    def get_category_rankings(
        self,
        start_date: str,
        end_date: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        카테고리 내 판매 순위

        Args:
            start_date: 시작일
            end_date: 종료일

        Returns:
            카테고리별 상품 순위 {mid_cd: [{item_cd, item_nm, rank, sales}, ...]}
        """
        sf, sp = store_filter("ds", self.store_id)
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT
                ds.mid_cd,
                mc.mid_nm,
                ds.item_cd,
                p.item_nm,
                SUM(ds.sale_qty) as total_sales
            FROM daily_sales ds
            LEFT JOIN products p ON ds.item_cd = p.item_cd
            LEFT JOIN mid_categories mc ON ds.mid_cd = mc.mid_cd
            WHERE ds.sales_date BETWEEN ? AND ?
            {sf}
            GROUP BY ds.mid_cd, mc.mid_nm, ds.item_cd, p.item_nm
            ORDER BY ds.mid_cd, total_sales DESC
        """, (start_date, end_date) + sp)

        rows = cursor.fetchall()
        conn.close()

        # 카테고리별 그룹핑
        rankings = {}
        current_mid_cd = None
        rank = 0

        for row in rows:
            mid_cd = row[0]
            mid_nm = row[1] or mid_cd

            if mid_cd != current_mid_cd:
                current_mid_cd = mid_cd
                rank = 0
                if mid_cd not in rankings:
                    rankings[mid_cd] = {
                        "mid_nm": mid_nm,
                        "products": []
                    }

            rank += 1
            rankings[mid_cd]["products"].append({
                "rank": rank,
                "item_cd": row[2],
                "item_nm": row[3] or row[2],
                "total_sales": row[4] or 0,
            })

        return rankings

    def get_surge_products(
        self,
        this_start: str,
        this_end: str,
        prev_start: str,
        prev_end: str,
        threshold_pct: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        급상승 상품 감지 (전주 대비 +threshold% 이상)

        Args:
            this_start: 이번 주 시작일
            this_end: 이번 주 종료일
            prev_start: 전주 시작일
            prev_end: 전주 종료일
            threshold_pct: 기준 퍼센트 (기본: self.threshold_pct)

        Returns:
            급상승 상품 리스트
        """
        if threshold_pct is None:
            threshold_pct = self.threshold_pct

        all_products = self.get_product_growth(this_start, this_end, prev_start, prev_end)

        surge = [
            p for p in all_products
            if p["growth_rate"] is not None and p["growth_rate"] >= threshold_pct
        ]

        # 성장률 내림차순
        surge.sort(key=lambda x: x["growth_rate"], reverse=True)
        return surge

    def get_plunge_products(
        self,
        this_start: str,
        this_end: str,
        prev_start: str,
        prev_end: str,
        threshold_pct: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        급하락 상품 감지 (전주 대비 -threshold% 이하)

        Args:
            this_start: 이번 주 시작일
            this_end: 이번 주 종료일
            prev_start: 전주 시작일
            prev_end: 전주 종료일
            threshold_pct: 기준 퍼센트 (기본: self.threshold_pct)

        Returns:
            급하락 상품 리스트
        """
        if threshold_pct is None:
            threshold_pct = self.threshold_pct

        all_products = self.get_product_growth(this_start, this_end, prev_start, prev_end)

        plunge = [
            p for p in all_products
            if p["growth_rate"] is not None and p["growth_rate"] <= -threshold_pct
        ]

        # 성장률 오름차순 (가장 많이 떨어진 순)
        plunge.sort(key=lambda x: x["growth_rate"])
        return plunge

    def get_new_popular_products(
        self,
        this_start: str,
        this_end: str,
        prev_start: str,
        prev_end: str,
        min_sales: int = 5
    ) -> List[Dict[str, Any]]:
        """
        신규 인기 상품 발굴 (최근 7일 판매 급증)

        조건:
        - 전주 판매량 0 또는 매우 적음 (3개 이하)
        - 이번 주 판매량 min_sales 이상

        Args:
            this_start: 이번 주 시작일
            this_end: 이번 주 종료일
            prev_start: 전주 시작일
            prev_end: 전주 종료일
            min_sales: 최소 판매량

        Returns:
            신규 인기 상품 리스트
        """
        sf, sp = store_filter("ds", self.store_id)
        conn = self._get_conn()
        cursor = conn.cursor()

        # 이번 주 판매 상품
        cursor.execute(f"""
            SELECT
                ds.item_cd,
                p.item_nm,
                ds.mid_cd,
                mc.mid_nm,
                SUM(ds.sale_qty) as total_sales,
                COUNT(DISTINCT ds.sales_date) as days_sold
            FROM daily_sales ds
            LEFT JOIN products p ON ds.item_cd = p.item_cd
            LEFT JOIN mid_categories mc ON ds.mid_cd = mc.mid_cd
            WHERE ds.sales_date BETWEEN ? AND ?
            {sf}
            GROUP BY ds.item_cd, p.item_nm, ds.mid_cd, mc.mid_nm
            HAVING total_sales >= ?
        """, (this_start, this_end) + sp + (min_sales,))

        this_week = {}
        for row in cursor.fetchall():
            this_week[row[0]] = {
                "item_nm": row[1],
                "mid_cd": row[2],
                "mid_nm": row[3],
                "sales": row[4] or 0,
                "days_sold": row[5] or 0,
            }

        # 전주 판매량
        cursor.execute(f"""
            SELECT ds.item_cd, SUM(ds.sale_qty) as total_sales
            FROM daily_sales ds
            WHERE ds.sales_date BETWEEN ? AND ?
            {sf}
            GROUP BY ds.item_cd
        """, (prev_start, prev_end) + sp)

        prev_week = {row[0]: row[1] or 0 for row in cursor.fetchall()}
        conn.close()

        # 신규 인기 상품 필터링
        new_popular = []
        for item_cd, data in this_week.items():
            prev_sales = prev_week.get(item_cd, 0)

            # 전주 판매 3개 이하 & 이번 주 급증
            if prev_sales <= 3:
                new_popular.append({
                    "item_cd": item_cd,
                    "item_nm": data["item_nm"] or item_cd,
                    "mid_cd": data["mid_cd"],
                    "mid_nm": data["mid_nm"] or data["mid_cd"],
                    "this_week_sales": data["sales"],
                    "prev_week_sales": prev_sales,
                    "days_sold": data["days_sold"],
                    "is_new": prev_sales == 0,  # 완전 신규 여부
                })

        # 판매량 내림차순
        new_popular.sort(key=lambda x: x["this_week_sales"], reverse=True)
        return new_popular

    def get_top_products_by_category(
        self,
        mid_cd: str,
        start_date: str,
        end_date: str,
        top_n: int = 10
    ) -> List[Dict[str, Any]]:
        """
        특정 카테고리의 TOP N 상품

        Args:
            mid_cd: 중분류 코드
            start_date: 시작일
            end_date: 종료일
            top_n: 상위 N개

        Returns:
            TOP N 상품 리스트
        """
        sf, sp = store_filter("ds", self.store_id)
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT
                ds.item_cd,
                p.item_nm,
                SUM(ds.sale_qty) as total_sales,
                AVG(ds.stock_qty) as avg_stock,
                SUM(ds.disuse_qty) as total_disuse
            FROM daily_sales ds
            LEFT JOIN products p ON ds.item_cd = p.item_cd
            WHERE ds.mid_cd = ?
            AND ds.sales_date BETWEEN ? AND ?
            {sf}
            GROUP BY ds.item_cd, p.item_nm
            ORDER BY total_sales DESC
            LIMIT ?
        """, (mid_cd, start_date, end_date) + sp + (top_n,))

        rows = cursor.fetchall()
        conn.close()

        results = []
        for i, row in enumerate(rows, 1):
            sales = row[2] or 0
            disuse = row[4] or 0
            total = sales + disuse

            results.append({
                "rank": i,
                "item_cd": row[0],
                "item_nm": row[1] or row[0],
                "total_sales": sales,
                "avg_stock": round(row[3], 1) if row[3] else 0,
                "total_disuse": disuse,
                "disuse_rate": round(disuse / total * 100, 1) if total > 0 else 0,
            })

        return results

    def print_analysis(self, result: Dict[str, Any]) -> None:
        """분석 결과 콘솔 출력"""
        print("\n" + "=" * 70)
        print("[PRODUCT ANALYSIS]")
        print(f"이번 주: {result['period']['this_week']}")
        print(f"전주: {result['period']['prev_week']}")
        print("=" * 70)

        # 급상승 상품
        surge = result.get("surge_products", [])[:10]
        if surge:
            print(f"\n[급상승 상품 TOP 10] (전주 대비 +{self.threshold_pct}% 이상)")
            print(f"  {'상품명':<30} {'카테고리':<12} {'이번주':>8} {'전주':>8} {'성장률':>10}")
            print("  " + "-" * 75)
            for p in surge:
                name = (p["item_nm"] or "")[:28]
                cat = (p["mid_nm"] or "")[:10]
                growth = f"+{p['growth_rate']}%"
                print(f"  {name:<30} {cat:<12} {p['this_week_sales']:>8} {p['prev_week_sales']:>8} {growth:>10}")

        # 급하락 상품
        plunge = result.get("plunge_products", [])[:10]
        if plunge:
            print(f"\n[급하락 상품 TOP 10] (전주 대비 -{self.threshold_pct}% 이하)")
            print(f"  {'상품명':<30} {'카테고리':<12} {'이번주':>8} {'전주':>8} {'성장률':>10}")
            print("  " + "-" * 75)
            for p in plunge:
                name = (p["item_nm"] or "")[:28]
                cat = (p["mid_nm"] or "")[:10]
                growth = f"{p['growth_rate']}%"
                print(f"  {name:<30} {cat:<12} {p['this_week_sales']:>8} {p['prev_week_sales']:>8} {growth:>10}")

        # 신규 인기 상품
        new_popular = result.get("new_popular", [])[:10]
        if new_popular:
            print(f"\n[신규 인기 상품] (전주 0~3개 → 이번 주 급증)")
            print(f"  {'상품명':<30} {'카테고리':<12} {'이번주':>8} {'전주':>8} {'상태':<8}")
            print("  " + "-" * 75)
            for p in new_popular:
                name = (p["item_nm"] or "")[:28]
                cat = (p["mid_nm"] or "")[:10]
                status = "신규" if p["is_new"] else "복귀"
                print(f"  {name:<30} {cat:<12} {p['this_week_sales']:>8} {p['prev_week_sales']:>8} {status:<8}")

        # 카테고리별 TOP 3 요약
        rankings = result.get("category_rankings", {})
        if rankings:
            print(f"\n[카테고리별 TOP 3]")
            for mid_cd, data in list(rankings.items())[:10]:
                mid_nm = data["mid_nm"]
                products = data["products"][:3]
                top_names = ", ".join([p["item_nm"][:15] for p in products])
                print(f"  {mid_nm[:15]:<15}: {top_names}")

        print("\n" + "=" * 70)


# =============================================================================
# 헬퍼 함수
# =============================================================================

def analyze_products(end_date: Optional[str] = None, threshold_pct: float = 30.0, store_id: Optional[str] = None) -> Dict[str, Any]:
    """
    상품 분석 실행 헬퍼 함수

    Args:
        end_date: 기준 종료일
        threshold_pct: 급상승/급하락 기준 퍼센트
        store_id: 매장 코드

    Returns:
        분석 결과 dict
    """
    analyzer = ProductAnalyzer(threshold_pct=threshold_pct, store_id=store_id)
    return analyzer.analyze(end_date)


def get_surge_products(end_date: Optional[str] = None, threshold_pct: float = 30.0) -> List[Dict[str, Any]]:
    """급상승 상품 조회 헬퍼 함수"""
    result = analyze_products(end_date, threshold_pct)
    return result.get("surge_products", [])


def get_plunge_products(end_date: Optional[str] = None, threshold_pct: float = 30.0) -> List[Dict[str, Any]]:
    """급하락 상품 조회 헬퍼 함수"""
    result = analyze_products(end_date, threshold_pct)
    return result.get("plunge_products", [])


def get_new_popular_products(end_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """신규 인기 상품 조회 헬퍼 함수"""
    result = analyze_products(end_date)
    return result.get("new_popular", [])


# =============================================================================
# CLI
# =============================================================================

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Product Analyzer")
    parser.add_argument("--date", "-d", type=str, help="End date (YYYY-MM-DD)")
    parser.add_argument("--threshold", "-t", type=float, default=30.0,
                       help="Surge/plunge threshold percent (default: 30)")
    parser.add_argument("--surge", action="store_true", help="Show surge products only")
    parser.add_argument("--plunge", action="store_true", help="Show plunge products only")
    parser.add_argument("--new", action="store_true", help="Show new popular products only")

    args = parser.parse_args()

    analyzer = ProductAnalyzer(threshold_pct=args.threshold)
    result = analyzer.analyze(args.date)

    if args.surge:
        print("\n[급상승 상품]")
        for p in result["surge_products"][:20]:
            print(f"  {p['item_nm'][:30]}: +{p['growth_rate']}%")
    elif args.plunge:
        print("\n[급하락 상품]")
        for p in result["plunge_products"][:20]:
            print(f"  {p['item_nm'][:30]}: {p['growth_rate']}%")
    elif args.new:
        print("\n[신규 인기 상품]")
        for p in result["new_popular"][:20]:
            status = "신규" if p["is_new"] else "복귀"
            print(f"  [{status}] {p['item_nm'][:30]}: {p['this_week_sales']}개")
    else:
        analyzer.print_analysis(result)


if __name__ == "__main__":
    main()
