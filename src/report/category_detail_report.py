"""
카테고리 심층 분석 HTML 리포트

특정 카테고리의 요일계수 비교, 회전율 분포, 상품별 sparkline,
안전재고 설정 정보를 시각화한 HTML을 생성한다.
"""

import sqlite3
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path

from .base_report import BaseReport
from src.prediction.categories.default import (
    CATEGORY_NAMES,
    WEEKDAY_COEFFICIENTS,
    DEFAULT_WEEKDAY_COEFFICIENTS,
    SHELF_LIFE_CONFIG,
    SAFETY_STOCK_MULTIPLIER,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


class CategoryDetailReport(BaseReport):
    """카테고리 심층 분석 HTML"""

    REPORT_SUB_DIR = "category"
    TEMPLATE_NAME = "category_detail.html"

    def __init__(self, db_path: str = None, store_id: Optional[str] = None):
        super().__init__(db_path)
        self.store_id = store_id

    def generate(self, mid_cd: str) -> Path:
        """특정 카테고리 심층 분석 리포트 생성

        Args:
            mid_cd: 중분류 코드

        Returns:
            생성된 HTML 파일 경로
        """
        cat_name = CATEGORY_NAMES.get(mid_cd, mid_cd)

        overview = self._query_overview(mid_cd)
        weekday_coefs = self._get_weekday_comparison(mid_cd)
        turnover_dist = self._query_turnover_distribution(mid_cd)
        sparklines = self._query_sparklines(mid_cd)
        safety_config = self._get_safety_config()

        context = {
            "mid_cd": mid_cd,
            "cat_name": cat_name,
            "overview": overview,
            "weekday_coefs": weekday_coefs,
            "turnover_dist": turnover_dist,
            "sparklines": sparklines,
            "safety_config": safety_config,
        }

        html = self.render(context)
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"category_{mid_cd}_{cat_name}_{date_str}.html"
        return self.save(html, filename)

    def _query_overview(self, mid_cd: str) -> Dict[str, Any]:
        """카테고리 개요"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()

            cursor.execute(f"""
                SELECT COUNT(DISTINCT item_cd) as item_count,
                       SUM(sale_qty) as total_sales,
                       COUNT(DISTINCT sales_date) as data_days
                FROM daily_sales
                WHERE mid_cd = ?
                AND sales_date >= date('now', '-30 days')
                {store_filter}
            """, (mid_cd,) + store_params)
            row = cursor.fetchone()
            conn.close()
        except Exception:
            row = (0, 0, 1)

        item_count = row[0] or 0
        total_sales = row[1] or 0
        data_days = row[2] or 1
        daily_avg = total_sales / data_days if data_days > 0 else 0

        return {
            "item_count": item_count,
            "total_sales": total_sales,
            "data_days": data_days,
            "daily_avg": round(daily_avg, 1),
            "avg_per_item": round(daily_avg / max(item_count, 1), 2),
        }

    def _get_weekday_comparison(self, mid_cd: str) -> Dict[str, Any]:
        """요일 계수: 설정값 vs 기본값"""
        weekday_labels = ["월", "화", "수", "목", "금", "토", "일"]

        # WEEKDAY_COEFFICIENTS는 [일, 월, 화, 수, 목, 금, 토] 순서
        if mid_cd in WEEKDAY_COEFFICIENTS:
            raw = WEEKDAY_COEFFICIENTS[mid_cd]
        else:
            raw = DEFAULT_WEEKDAY_COEFFICIENTS

        # → 월~일 순서로 변환
        config_coefs = [raw[1], raw[2], raw[3], raw[4], raw[5], raw[6], raw[0]]

        default_raw = DEFAULT_WEEKDAY_COEFFICIENTS
        default_coefs = [
            default_raw[1], default_raw[2], default_raw[3],
            default_raw[4], default_raw[5], default_raw[6], default_raw[0],
        ]

        return {
            "labels": weekday_labels,
            "config": config_coefs,
            "default": default_coefs,
        }

    def _query_turnover_distribution(self, mid_cd: str) -> Dict[str, Any]:
        """회전율 분포"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()

            cursor.execute(f"""
                SELECT item_cd,
                       SUM(sale_qty) as total,
                       COUNT(DISTINCT sales_date) as days
                FROM daily_sales
                WHERE mid_cd = ?
                AND sales_date >= date('now', '-30 days')
                {store_filter}
                GROUP BY item_cd
            """, (mid_cd,) + store_params)
            rows = cursor.fetchall()
            conn.close()
        except Exception:
            rows = []

        high, medium, low = 0, 0, 0
        for _, total, days in rows:
            daily = total / max(days, 1)
            if daily >= 5.0:
                high += 1
            elif daily >= 2.0:
                medium += 1
            else:
                low += 1

        return {
            "labels": ["고회전 (5+/일)", "중회전 (2~5/일)", "저회전 (<2/일)"],
            "counts": [high, medium, low],
        }

    def _query_sparklines(self, mid_cd: str, days: int = 7) -> List[Dict[str, Any]]:
        """상품별 7일 판매 sparkline"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            cursor = conn.cursor()

            store_filter = "AND ds.store_id = ?" if self.store_id else ""
            store_filter_plain = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()

            cursor.execute(f"""
                SELECT DISTINCT ds.item_cd, p.item_nm
                FROM daily_sales ds
                LEFT JOIN products p ON ds.item_cd = p.item_cd
                WHERE ds.mid_cd = ?
                AND ds.sales_date >= date('now', '-' || ? || ' days')
                AND ds.sale_qty > 0
                {store_filter}
                ORDER BY ds.item_cd
                LIMIT 30
            """, (mid_cd, days) + store_params)
            items = cursor.fetchall()

            sparklines = []
            for item_cd, item_nm in items:
                cursor.execute(f"""
                    SELECT sales_date, sale_qty
                    FROM daily_sales
                    WHERE item_cd = ?
                    AND sales_date >= date('now', '-' || ? || ' days')
                    {store_filter_plain}
                    ORDER BY sales_date
                """, (item_cd, days) + store_params)
                rows = cursor.fetchall()
                data = [r[1] for r in rows]
                sparklines.append({
                    "item_cd": item_cd,
                    "item_nm": item_nm or item_cd,
                    "data": data,
                    "total": sum(data),
                    "avg": round(sum(data) / max(len(data), 1), 1),
                    "max_val": max(data) if data else 0,
                })

            conn.close()
        except Exception:
            sparklines = []

        return sorted(sparklines, key=lambda x: x["total"], reverse=True)

    def _get_safety_config(self) -> Dict[str, Any]:
        """현재 안전재고 설정"""
        return {
            "shelf_life": [
                {
                    "group": group,
                    "range": f"{cfg['min_days']}~{cfg['max_days']}",
                    "safety_days": cfg["safety_stock_days"],
                }
                for group, cfg in SHELF_LIFE_CONFIG.items()
            ],
            "turnover": [
                {
                    "level": level,
                    "min_daily": cfg["min_daily_avg"],
                    "multiplier": cfg["multiplier"],
                }
                for level, cfg in SAFETY_STOCK_MULTIPLIER.items()
            ],
        }
