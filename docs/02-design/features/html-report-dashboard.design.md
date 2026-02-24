# Design: HTML 리포트 대시보드 시스템

> **Feature**: html-report-dashboard
> **Plan Reference**: `docs/01-plan/features/html-report-dashboard.plan.md`
> **Created**: 2026-02-02
> **Status**: Draft

---

## 1. 파일 구조

```
bgf_auto/
├── src/
│   └── report/                          # 신규 패키지
│       ├── __init__.py                  # 패키지 초기화 + public API
│       ├── base_report.py               # Jinja2 환경 설정, 공통 렌더링
│       ├── daily_order_report.py        # 일일 발주 리포트
│       ├── safety_impact_report.py      # 안전재고 영향도 리포트
│       ├── weekly_trend_report.py       # 주간 트렌드 리포트
│       ├── category_detail_report.py    # 카테고리 심층 분석
│       └── templates/                   # Jinja2 HTML 템플릿
│           ├── base.html                # 공통 레이아웃
│           ├── daily_order.html
│           ├── safety_impact.html
│           ├── weekly_trend.html
│           └── category_detail.html
│
├── data/
│   └── reports/                         # 생성된 HTML 파일
│       ├── daily/
│       ├── weekly/
│       ├── impact/
│       └── category/
│
└── scripts/
    └── run_report.py                    # CLI 진입점
```

---

## 2. 모듈 상세 설계

### 2-1. `src/report/__init__.py`

```python
"""HTML 리포트 패키지"""
from .daily_order_report import DailyOrderReport
from .safety_impact_report import SafetyImpactReport
from .weekly_trend_report import WeeklyTrendReportHTML
from .category_detail_report import CategoryDetailReport

__all__ = [
    "DailyOrderReport",
    "SafetyImpactReport",
    "WeeklyTrendReportHTML",
    "CategoryDetailReport",
]
```

### 2-2. `src/report/base_report.py` — 공통 기반 클래스

```python
"""공통 리포트 생성기"""
import json
from pathlib import Path
from datetime import datetime
from jinja2 import Environment, FileSystemLoader

from src.utils.logger import get_logger

logger = get_logger(__name__)


class BaseReport:
    """Jinja2 기반 HTML 리포트 공통 클래스"""

    # 출력 디렉토리 (data/reports/ 하위)
    REPORT_BASE_DIR: Path   # 서브클래스에서 정의
    TEMPLATE_NAME: str      # 서브클래스에서 정의

    def __init__(self, db_path: str = None):
        self.db_path = db_path or self._default_db_path()
        self.template_dir = Path(__file__).parent / "templates"
        self.env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=True,
        )
        # 공통 Jinja2 필터
        self.env.filters["number_format"] = self._number_format
        self.env.filters["percent"] = self._percent_format
        self.env.filters["to_json"] = self._to_json

    def render(self, context: dict) -> str:
        """템플릿 렌더링 → HTML 문자열"""
        template = self.env.get_template(self.TEMPLATE_NAME)
        context["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        return template.render(**context)

    def save(self, html: str, filename: str) -> Path:
        """HTML 파일 저장 → 경로 반환"""
        output_dir = Path(__file__).parent.parent.parent / "data" / "reports" / self.REPORT_BASE_DIR
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / filename
        output_path.write_text(html, encoding="utf-8")
        logger.info(f"[리포트] 저장 완료: {output_path}")
        return output_path

    def generate(self, **kwargs) -> Path:
        """서브클래스에서 구현: 데이터 조회 → 렌더링 → 저장"""
        raise NotImplementedError

    # === 유틸 ===
    @staticmethod
    def _default_db_path() -> str:
        return str(Path(__file__).parent.parent.parent / "data" / "bgf_sales.db")

    @staticmethod
    def _number_format(value) -> str:
        """숫자 천단위 콤마"""
        if value is None:
            return "0"
        return f"{value:,.0f}" if isinstance(value, (int, float)) else str(value)

    @staticmethod
    def _percent_format(value, decimals=1) -> str:
        """퍼센트 포맷 (예: 23.5%)"""
        if value is None:
            return "0%"
        return f"{value:.{decimals}f}%"

    @staticmethod
    def _to_json(value) -> str:
        """Chart.js용 JSON 직렬화"""
        return json.dumps(value, ensure_ascii=False)
```

---

### 2-3. `src/report/daily_order_report.py` — 일일 발주 리포트

#### 클래스 설계

```python
"""일일 발주 리포트 생성"""
import sqlite3
from dataclasses import asdict
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from .base_report import BaseReport
from src.prediction.improved_predictor import PredictionResult
from src.prediction.categories.default import CATEGORY_NAMES
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DailyOrderReport(BaseReport):
    """일일 발주 대시보드 HTML 생성"""

    REPORT_BASE_DIR = "daily"
    TEMPLATE_NAME = "daily_order.html"

    def generate(
        self,
        predictions: List[PredictionResult],
        target_date: str = None,
    ) -> Path:
        """
        일일 발주 리포트 생성

        Args:
            predictions: ImprovedPredictor.get_recommendations() 결과
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

        # 1. 요약 통계
        summary = self._calc_summary(predictions)

        # 2. 카테고리별 집계 (Chart.js 데이터)
        category_data = self._group_by_category(predictions)

        # 3. 상품별 상세 테이블 (order_qty 내림차순)
        items = self._build_item_table(predictions)

        # 4. 스킵 상품 목록
        skipped = self._build_skipped_list(predictions)

        # 5. 안전재고 분포 데이터
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
        skipped = [p for p in predictions if p.order_qty == 0]
        categories = set(p.mid_cd for p in predictions)

        return {
            "total_items": total,
            "ordered_count": len(ordered),
            "skipped_count": len(skipped),
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
                groups[cat_name] = {"count": 0, "order_qty": 0, "safety_stock": 0}
            groups[cat_name]["count"] += 1
            groups[cat_name]["order_qty"] += p.order_qty
            groups[cat_name]["safety_stock"] += p.safety_stock

        # order_qty 내림차순 정렬
        sorted_cats = sorted(groups.items(), key=lambda x: x[1]["order_qty"], reverse=True)

        return {
            "labels": [c[0] for c in sorted_cats],
            "order_qty": [c[1]["order_qty"] for c in sorted_cats],
            "count": [c[1]["count"] for c in sorted_cats],
            "safety_stock": [round(c[1]["safety_stock"], 1) for c in sorted_cats],
        }

    def _build_item_table(
        self, predictions: List[PredictionResult]
    ) -> List[Dict[str, Any]]:
        """상품별 상세 테이블 데이터"""
        items = []
        for p in sorted(predictions, key=lambda x: x.order_qty, reverse=True):
            cat_name = CATEGORY_NAMES.get(p.mid_cd, p.mid_cd)
            items.append({
                "item_cd": p.item_cd,
                "item_nm": p.item_nm,
                "category": cat_name,
                "mid_cd": p.mid_cd,
                "daily_avg": round(p.predicted_qty, 1),
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
                "item_nm": p.item_nm,
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
            return f"담배 상한선 초과: {p.tobacco_skip_reason}"
        if p.ramen_skip_order:
            return "라면 상한선 초과"
        if p.beer_skip_order:
            return f"맥주: {p.beer_skip_reason}"
        if p.soju_skip_order:
            return f"소주: {p.soju_skip_reason}"
        if p.current_stock + p.pending_qty >= p.safety_stock + p.adjusted_qty:
            return "재고+미입고 충분"
        return "발주량 0 (예측 수요 없음)"

    def _build_safety_distribution(
        self, predictions: List[PredictionResult]
    ) -> Dict[str, Any]:
        """카테고리별 안전재고 일수 분포 (histogram 데이터)"""
        safety_days_by_cat: Dict[str, List[float]] = {}
        for p in predictions:
            cat = CATEGORY_NAMES.get(p.mid_cd, p.mid_cd)
            daily_avg = p.predicted_qty if p.predicted_qty > 0 else 1
            safety_days = p.safety_stock / daily_avg
            if cat not in safety_days_by_cat:
                safety_days_by_cat[cat] = []
            safety_days_by_cat[cat].append(round(safety_days, 2))

        # 전체 안전재고 일수 히스토그램 (0~5일 범위, 0.5일 단위)
        all_days = []
        for days_list in safety_days_by_cat.values():
            all_days.extend(days_list)

        bins = [0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 5.0]
        bin_labels = ["0~0.5", "0.5~1.0", "1.0~1.5", "1.5~2.0", "2.0~2.5", "2.5~3.0", "3.0+"]
        counts = [0] * len(bin_labels)
        for d in all_days:
            for i in range(len(bins) - 1):
                if bins[i] <= d < bins[i + 1]:
                    counts[i] += 1
                    break
            else:
                counts[-1] += 1  # 5.0 이상

        return {
            "labels": bin_labels,
            "counts": counts,
            "by_category": {cat: days for cat, days in safety_days_by_cat.items()},
        }
```

---

### 2-4. `src/report/safety_impact_report.py` — 안전재고 영향도 리포트

```python
"""안전재고 변경 영향도 리포트"""
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path

from .base_report import BaseReport
from src.prediction.improved_predictor import ImprovedPredictor, PredictionResult
from src.prediction.categories.default import CATEGORY_NAMES
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SafetyImpactReport(BaseReport):
    """안전재고 파라미터 변경 영향도 분석"""

    REPORT_BASE_DIR = "impact"
    TEMPLATE_NAME = "safety_impact.html"

    def save_baseline(self, predictions: List[PredictionResult]) -> Path:
        """
        변경 전 baseline 저장 (JSON)

        Args:
            predictions: 현재 파라미터의 예측 결과

        Returns:
            baseline JSON 파일 경로
        """
        baseline = {}
        for p in predictions:
            baseline[p.item_cd] = {
                "item_nm": p.item_nm,
                "mid_cd": p.mid_cd,
                "safety_stock": round(p.safety_stock, 2),
                "order_qty": p.order_qty,
                "predicted_qty": round(p.predicted_qty, 2),
                "current_stock": p.current_stock,
                "pending_qty": p.pending_qty,
            }

        output_dir = Path(__file__).parent.parent.parent / "data" / "reports" / "impact"
        output_dir.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now().strftime("%Y-%m-%d")
        filepath = output_dir / f"baseline_{date_str}.json"
        filepath.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"[리포트] Baseline 저장: {filepath}")
        return filepath

    def generate(
        self,
        current_predictions: List[PredictionResult],
        baseline_path: str,
        change_date: str = None,
    ) -> Path:
        """
        영향도 리포트 생성

        Args:
            current_predictions: 변경 후 예측 결과
            baseline_path: baseline JSON 경로
            change_date: 변경 적용일 (기본: 오늘)

        Returns:
            생성된 HTML 파일 경로
        """
        if change_date is None:
            change_date = datetime.now().strftime("%Y-%m-%d")

        baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
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

        # 1. 상품별 비교 (baseline에 있는 상품만)
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
                "item_nm": p.item_nm,
                "mid_cd": p.mid_cd,
                "category": CATEGORY_NAMES.get(p.mid_cd, p.mid_cd),
                "old_safety": old_safety,
                "new_safety": new_safety,
                "delta": round(delta, 2),
                "pct_change": round(pct, 1),
                "old_order": b["order_qty"],
                "new_order": p.order_qty,
            })

        # 2. 카테고리별 집계
        cat_summary = self._aggregate_by_category(comparisons)

        # 3. 전체 요약
        summary = self._calc_overall_summary(comparisons)

        # 4. 품절 추적 데이터 (변경일 전후 14일)
        stockout_trend = self._query_stockout_trend(change_date)

        return {
            "change_date": change_date,
            "summary": summary,
            "comparisons": sorted(comparisons, key=lambda x: x["pct_change"]),
            "cat_summary": cat_summary,
            "stockout_trend": stockout_trend,
        }

    def _aggregate_by_category(self, comparisons: List[Dict]) -> Dict[str, Any]:
        """카테고리별 안전재고 감소율 집계"""
        groups: Dict[str, Dict] = {}
        for c in comparisons:
            cat = c["category"]
            if cat not in groups:
                groups[cat] = {"old_total": 0, "new_total": 0, "count": 0}
            groups[cat]["old_total"] += c["old_safety"]
            groups[cat]["new_total"] += c["new_safety"]
            groups[cat]["count"] += 1

        result = []
        for cat, g in groups.items():
            pct = ((g["new_total"] - g["old_total"]) / g["old_total"] * 100) if g["old_total"] > 0 else 0
            result.append({
                "category": cat,
                "old_total": round(g["old_total"], 1),
                "new_total": round(g["new_total"], 1),
                "pct_change": round(pct, 1),
                "count": g["count"],
            })
        return {
            "labels": [r["category"] for r in sorted(result, key=lambda x: x["pct_change"])],
            "pct_changes": [r["pct_change"] for r in sorted(result, key=lambda x: x["pct_change"])],
            "items": sorted(result, key=lambda x: x["pct_change"]),
        }

    def _calc_overall_summary(self, comparisons: List[Dict]) -> Dict[str, Any]:
        """전체 요약 통계"""
        if not comparisons:
            return {"total_items": 0, "avg_change": 0, "total_old": 0, "total_new": 0}
        total_old = sum(c["old_safety"] for c in comparisons)
        total_new = sum(c["new_safety"] for c in comparisons)
        pct = ((total_new - total_old) / total_old * 100) if total_old > 0 else 0
        return {
            "total_items": len(comparisons),
            "total_old": round(total_old, 1),
            "total_new": round(total_new, 1),
            "total_change_pct": round(pct, 1),
            "decreased_count": sum(1 for c in comparisons if c["delta"] < 0),
            "increased_count": sum(1 for c in comparisons if c["delta"] > 0),
            "unchanged_count": sum(1 for c in comparisons if c["delta"] == 0),
        }

    def _query_stockout_trend(self, change_date: str, days_before=14, days_after=14) -> Dict[str, Any]:
        """변경일 전후 품절 추이 조회"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT sales_date, COUNT(*) as stockout_count
            FROM daily_sales
            WHERE stock_qty = 0 AND sale_qty > 0
            AND sales_date BETWEEN date(?, '-' || ? || ' days') AND date(?, '+' || ? || ' days')
            GROUP BY sales_date
            ORDER BY sales_date
        """, (change_date, days_before, change_date, days_after))
        rows = cursor.fetchall()
        conn.close()

        return {
            "labels": [r[0] for r in rows],
            "counts": [r[1] for r in rows],
            "change_date": change_date,
        }
```

---

### 2-5. `src/report/weekly_trend_report.py` — 주간 트렌드 리포트

```python
"""주간 트렌드 HTML 리포트"""
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path

from .base_report import BaseReport
from src.analysis.trend_report import WeeklyTrendReport
from src.prediction.accuracy.reporter import AccuracyReporter
from src.prediction.categories.default import CATEGORY_NAMES
from src.utils.logger import get_logger

logger = get_logger(__name__)


class WeeklyTrendReportHTML(BaseReport):
    """주간 트렌드 HTML 대시보드"""

    REPORT_BASE_DIR = "weekly"
    TEMPLATE_NAME = "weekly_trend.html"

    def generate(self, end_date: str = None) -> Path:
        """
        주간 트렌드 HTML 생성

        Args:
            end_date: 기준 종료일 (기본: 어제)

        Returns:
            생성된 HTML 파일 경로
        """
        # 기존 리포트 모듈 활용
        trend = WeeklyTrendReport(self.db_path)
        trend_data = trend.generate(end_date)

        # 정확도 데이터
        accuracy = AccuracyReporter()
        accuracy_report = accuracy.generate_weekly_report()

        # 요일별 판매 히트맵
        heatmap = self._query_weekday_heatmap()

        # 7일 카테고리별 판매 추이
        daily_trend = self._query_daily_category_sales()

        context = {
            "trend": trend_data,
            "accuracy": {
                "mape": accuracy_report.overall_metrics.mape if accuracy_report.overall_metrics else None,
                "mae": accuracy_report.overall_metrics.mae if accuracy_report.overall_metrics else None,
                "daily_trend": [
                    {"date": d["date"], "mape": d["mape"]}
                    for d in (accuracy_report.daily_mape_trend or [])
                ],
                "category_breakdown": [
                    {
                        "category": CATEGORY_NAMES.get(c.mid_cd, c.mid_cd),
                        "mape": c.mape,
                        "count": c.count,
                    }
                    for c in (accuracy_report.category_breakdown or [])
                ],
            },
            "heatmap": heatmap,
            "daily_trend": daily_trend,
        }

        html = self.render(context)

        # 주차 계산
        if end_date:
            d = datetime.strptime(end_date, "%Y-%m-%d")
        else:
            d = datetime.now() - timedelta(days=1)
        week_str = d.strftime("%Y-W%W")
        filename = f"weekly_trend_{week_str}.html"
        return self.save(html, filename)

    def _query_weekday_heatmap(self, days: int = 28) -> Dict[str, Any]:
        """카테고리 × 요일 판매량 히트맵 데이터"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()

        # SQLite %w: 0=일, 1=월, ..., 6=토
        cursor.execute("""
            SELECT mid_cd,
                   CAST(strftime('%w', sales_date) AS INTEGER) as dow,
                   ROUND(AVG(sale_qty), 1) as avg_qty
            FROM daily_sales
            WHERE sales_date >= date('now', '-' || ? || ' days')
            AND sale_qty > 0
            GROUP BY mid_cd, dow
            ORDER BY mid_cd, dow
        """, (days,))
        rows = cursor.fetchall()
        conn.close()

        # 변환: {카테고리: [일, 월, 화, 수, 목, 금, 토]}
        # SQLite dow → Python weekday: 0(일)→6, 1(월)→0, ..., 6(토)→5
        heatmap: Dict[str, List[float]] = {}
        for mid_cd, sqlite_dow, avg_qty in rows:
            cat = CATEGORY_NAMES.get(mid_cd, mid_cd)
            if cat not in heatmap:
                heatmap[cat] = [0.0] * 7
            py_dow = (sqlite_dow - 1) % 7  # 월=0, ..., 일=6
            heatmap[cat][py_dow] = avg_qty

        # 상위 15개 카테고리만 (총 판매량 기준)
        sorted_cats = sorted(heatmap.items(), key=lambda x: sum(x[1]), reverse=True)[:15]

        return {
            "categories": [c[0] for c in sorted_cats],
            "weekdays": ["월", "화", "수", "목", "금", "토", "일"],
            "data": [c[1] for c in sorted_cats],
        }

    def _query_daily_category_sales(self, days: int = 7) -> Dict[str, Any]:
        """최근 7일 카테고리별 일별 판매량"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT sales_date, mid_cd, SUM(sale_qty) as total_qty
            FROM daily_sales
            WHERE sales_date >= date('now', '-' || ? || ' days')
            GROUP BY sales_date, mid_cd
            ORDER BY sales_date, mid_cd
        """, (days,))
        rows = cursor.fetchall()
        conn.close()

        # {date: {category: qty}}
        dates = sorted(set(r[0] for r in rows))
        cats: Dict[str, Dict[str, int]] = {}
        for date, mid_cd, qty in rows:
            cat = CATEGORY_NAMES.get(mid_cd, mid_cd)
            if cat not in cats:
                cats[cat] = {}
            cats[cat][date] = qty

        # 상위 10개 카테고리
        top_cats = sorted(cats.items(), key=lambda x: sum(x[1].values()), reverse=True)[:10]

        datasets = []
        for cat, date_qty in top_cats:
            datasets.append({
                "label": cat,
                "data": [date_qty.get(d, 0) for d in dates],
            })

        return {"labels": dates, "datasets": datasets}
```

---

### 2-6. `src/report/category_detail_report.py` — 카테고리 심층 분석

```python
"""카테고리 심층 분석 리포트"""
import sqlite3
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path

from .base_report import BaseReport
from src.prediction.categories.default import (
    CATEGORY_NAMES, WEEKDAY_COEFFICIENTS, DEFAULT_WEEKDAY_COEFFICIENTS,
    SHELF_LIFE_CONFIG, SAFETY_STOCK_MULTIPLIER,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


class CategoryDetailReport(BaseReport):
    """카테고리 심층 분석 HTML"""

    REPORT_BASE_DIR = "category"
    TEMPLATE_NAME = "category_detail.html"

    def generate(self, mid_cd: str) -> Path:
        """
        특정 카테고리 심층 분석 리포트 생성

        Args:
            mid_cd: 중분류 코드 (예: "049")

        Returns:
            생성된 HTML 파일 경로
        """
        cat_name = CATEGORY_NAMES.get(mid_cd, mid_cd)

        # 1. 카테고리 개요
        overview = self._query_overview(mid_cd)

        # 2. 요일 계수 비교 (기본값 vs 설정값)
        weekday_coefs = self._get_weekday_comparison(mid_cd)

        # 3. 회전율 분포
        turnover_dist = self._query_turnover_distribution(mid_cd)

        # 4. 상품별 7일 판매 sparkline
        sparklines = self._query_sparklines(mid_cd)

        # 5. 안전재고 설정 정보
        safety_config = self._get_safety_config(mid_cd)

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
        """카테고리 개요 (상품수, 총판매, 평균회전율)"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(DISTINCT item_cd) as item_count,
                   SUM(sale_qty) as total_sales,
                   COUNT(DISTINCT sales_date) as data_days
            FROM daily_sales
            WHERE mid_cd = ?
            AND sales_date >= date('now', '-30 days')
        """, (mid_cd,))
        row = cursor.fetchone()
        conn.close()

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

        # default.py에 설정된 값 (0=일, 1=월, ..., 6=토 → Python weekday 변환)
        if mid_cd in WEEKDAY_COEFFICIENTS:
            # WEEKDAY_COEFFICIENTS는 [일, 월, 화, 수, 목, 금, 토] 순서
            raw = WEEKDAY_COEFFICIENTS[mid_cd]
            config_coefs = [raw[1], raw[2], raw[3], raw[4], raw[5], raw[6], raw[0]]  # 월~일
        else:
            raw = DEFAULT_WEEKDAY_COEFFICIENTS
            config_coefs = [raw[1], raw[2], raw[3], raw[4], raw[5], raw[6], raw[0]]

        default_coefs = [
            DEFAULT_WEEKDAY_COEFFICIENTS[1],  # 월
            DEFAULT_WEEKDAY_COEFFICIENTS[2],  # 화
            DEFAULT_WEEKDAY_COEFFICIENTS[3],  # 수
            DEFAULT_WEEKDAY_COEFFICIENTS[4],  # 목
            DEFAULT_WEEKDAY_COEFFICIENTS[5],  # 금
            DEFAULT_WEEKDAY_COEFFICIENTS[6],  # 토
            DEFAULT_WEEKDAY_COEFFICIENTS[0],  # 일
        ]

        return {
            "labels": weekday_labels,
            "config": config_coefs,
            "default": default_coefs,
        }

    def _query_turnover_distribution(self, mid_cd: str) -> Dict[str, Any]:
        """회전율 분포 (고/중/저)"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT item_cd,
                   SUM(sale_qty) as total,
                   COUNT(DISTINCT sales_date) as days
            FROM daily_sales
            WHERE mid_cd = ?
            AND sales_date >= date('now', '-30 days')
            GROUP BY item_cd
        """, (mid_cd,))
        rows = cursor.fetchall()
        conn.close()

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
        """상품별 7일 판매 sparkline 데이터"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()

        # 해당 카테고리 상품 목록 (최근 판매 있는 것만)
        cursor.execute("""
            SELECT DISTINCT ds.item_cd, p.item_nm
            FROM daily_sales ds
            LEFT JOIN products p ON ds.item_cd = p.item_cd
            WHERE ds.mid_cd = ?
            AND ds.sales_date >= date('now', '-' || ? || ' days')
            AND ds.sale_qty > 0
            ORDER BY ds.item_cd
            LIMIT 30
        """, (mid_cd, days))
        items = cursor.fetchall()

        sparklines = []
        for item_cd, item_nm in items:
            cursor.execute("""
                SELECT sales_date, sale_qty
                FROM daily_sales
                WHERE item_cd = ?
                AND sales_date >= date('now', '-' || ? || ' days')
                ORDER BY sales_date
            """, (item_cd, days))
            rows = cursor.fetchall()
            data = [r[1] for r in rows]
            sparklines.append({
                "item_cd": item_cd,
                "item_nm": item_nm or item_cd,
                "data": data,
                "total": sum(data),
                "avg": round(sum(data) / max(len(data), 1), 1),
            })

        conn.close()
        return sorted(sparklines, key=lambda x: x["total"], reverse=True)

    def _get_safety_config(self, mid_cd: str) -> Dict[str, Any]:
        """현재 안전재고 설정 정보"""
        return {
            "shelf_life_config": {
                group: {
                    "range": f"{cfg['min_days']}~{cfg['max_days']}일",
                    "safety_days": cfg["safety_stock_days"],
                }
                for group, cfg in SHELF_LIFE_CONFIG.items()
            },
            "turnover_multiplier": {
                level: {
                    "min_daily": cfg["min_daily_avg"],
                    "multiplier": cfg["multiplier"],
                }
                for level, cfg in SAFETY_STOCK_MULTIPLIER.items()
            },
        }
```

---

## 3. HTML 템플릿 설계

### 3-1. `templates/base.html` — 공통 레이아웃

```html
구조:
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}BGF 리포트{% endblock %}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
    <style>
        /* 인라인 CSS: 다크 테마 기반 대시보드 */
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               background: #1a1a2e; color: #e0e0e0; margin: 0; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { text-align: center; margin-bottom: 30px; border-bottom: 1px solid #333; }
        .card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; }
        .card { background: #16213e; border-radius: 12px; padding: 20px; text-align: center; }
        .card .value { font-size: 2em; font-weight: bold; color: #00d2ff; }
        .card .label { font-size: 0.85em; color: #999; margin-top: 4px; }
        .chart-container { background: #16213e; border-radius: 12px; padding: 20px; margin: 20px 0; }
        table { width: 100%; border-collapse: collapse; }
        th { background: #0f3460; padding: 10px; text-align: left; position: sticky; top: 0; }
        td { padding: 8px 10px; border-bottom: 1px solid #222; }
        tr:hover { background: #1a1a3e; }
        .positive { color: #4caf50; }
        .negative { color: #f44336; }
        .neutral { color: #999; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; }
        .badge-high { background: #1b5e20; color: #81c784; }
        .badge-medium { background: #e65100; color: #ffb74d; }
        .badge-low { background: #b71c1c; color: #ef9a9a; }
        .search-box { width: 100%; padding: 10px; background: #0f3460; border: 1px solid #333;
                      border-radius: 8px; color: #e0e0e0; margin-bottom: 10px; }
        .table-wrapper { max-height: 600px; overflow-y: auto; border-radius: 8px; }
        .footer { text-align: center; color: #666; margin-top: 40px; font-size: 0.85em; }
    </style>
    {% block extra_css %}{% endblock %}
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{% block header %}BGF 리포트{% endblock %}</h1>
            <p>생성: {{ generated_at }}</p>
        </div>
        {% block content %}{% endblock %}
        <div class="footer">BGF 리테일 자동 발주 시스템 | {{ generated_at }}</div>
    </div>
    {% block scripts %}{% endblock %}
</body>
</html>
```

### 3-2. `templates/daily_order.html` — 주요 섹션

```
{% extends "base.html" %}
{% block title %}일일 발주 대시보드 - {{ target_date }}{% endblock %}
{% block header %}일일 발주 대시보드 ({{ target_date }}){% endblock %}

{% block content %}
  <!-- 섹션 1: 요약 카드 (4개) -->
  .card-grid > .card × 4: 총 발주건수, 스킵건수, 총 발주수량, 카테고리수

  <!-- 섹션 2: 카테고리별 발주량 Bar Chart -->
  <canvas id="categoryChart"></canvas>
  Chart.js: 가로 bar chart, labels=category_data.labels, data=category_data.order_qty

  <!-- 섹션 3: 상품별 발주 상세 테이블 -->
  검색 input (JS 필터) + 테이블 (item_nm, category, daily_avg, safety_stock, current_stock, pending_qty, order_qty, confidence)
  confidence → badge (high=green, medium=orange, low=red)

  <!-- 섹션 4: 발주 스킵 목록 -->
  테이블: item_nm, category, current_stock, pending_qty, safety_stock, reason

  <!-- 섹션 5: 안전재고 분포 Histogram -->
  <canvas id="safetyHistogram"></canvas>
  Chart.js: bar chart, labels=safety_dist.labels, data=safety_dist.counts
{% endblock %}

{% block scripts %}
  <script>
    // Chart.js 초기화 코드
    // 테이블 검색/정렬 JS
  </script>
{% endblock %}
```

### 3-3. `templates/safety_impact.html` — 주요 섹션

```
{% extends "base.html" %}
섹션:
1. 요약 카드: 분석 상품수, 전체 안전재고 변화율, 감소 건수, 증가 건수
2. 카테고리별 변화율 수평 Bar Chart (음수=감소=파란, 양수=증가=빨간)
3. 상품별 비교 테이블 (old_safety, new_safety, delta, pct_change, old_order, new_order)
4. 품절 추이 Line Chart (변경일에 수직선 마커)
```

### 3-4. `templates/weekly_trend.html` — 주요 섹션

```
{% extends "base.html" %}
섹션:
1. 주간 요약 카드: 총판매량, 전주대비 증감, MAPE
2. 카테고리별 7일 판매 추이 Multi-line Chart
3. 예측 정확도 일별 MAPE Line Chart
4. 요일 × 카테고리 히트맵 (CSS grid, 색상 강도로 표현)
5. 급상승/급하락 상품 테이블
```

### 3-5. `templates/category_detail.html` — 주요 섹션

```
{% extends "base.html" %}
섹션:
1. 카테고리 개요 카드: 상품수, 총판매량, 일평균, 상품당 평균
2. 요일 계수 비교 Radar Chart (설정값 vs 기본값)
3. 회전율 분포 Doughnut Chart
4. 상품별 sparkline 테이블 (inline mini bar chart via CSS)
5. 안전재고 설정 정보 테이블
```

---

## 4. `scripts/run_report.py` CLI 설계

```python
"""리포트 생성 CLI"""
import argparse
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.report import DailyOrderReport, SafetyImpactReport, WeeklyTrendReportHTML, CategoryDetailReport
from src.prediction.improved_predictor import ImprovedPredictor
from src.utils.logger import get_logger

logger = get_logger(__name__)


def main():
    parser = argparse.ArgumentParser(description="BGF 리포트 생성")
    parser.add_argument("--daily", action="store_true", help="일일 발주 리포트")
    parser.add_argument("--weekly", action="store_true", help="주간 트렌드 리포트")
    parser.add_argument("--impact", action="store_true", help="안전재고 영향도 리포트")
    parser.add_argument("--category", type=str, help="카테고리 심층 분석 (mid_cd)")
    parser.add_argument("--all", action="store_true", help="전체 리포트 생성")
    parser.add_argument("--save-baseline", action="store_true", help="안전재고 baseline 저장")
    parser.add_argument("--baseline", type=str, help="baseline JSON 경로 (--impact용)")

    args = parser.parse_args()

    if args.save_baseline or args.daily or args.impact:
        # 예측 결과 필요
        predictor = ImprovedPredictor()
        predictions = predictor.get_all_predictions()

    if args.save_baseline:
        report = SafetyImpactReport()
        path = report.save_baseline(predictions)
        print(f"Baseline 저장: {path}")

    if args.daily or args.all:
        report = DailyOrderReport()
        path = report.generate(predictions)
        print(f"일일 발주 리포트: {path}")

    if args.impact:
        if not args.baseline:
            print("--baseline 경로를 지정하세요")
            sys.exit(1)
        report = SafetyImpactReport()
        path = report.generate(predictions, args.baseline)
        print(f"안전재고 영향도 리포트: {path}")

    if args.weekly or args.all:
        report = WeeklyTrendReportHTML()
        path = report.generate()
        print(f"주간 트렌드 리포트: {path}")

    if args.category:
        report = CategoryDetailReport()
        path = report.generate(args.category)
        print(f"카테고리 심층 분석: {path}")

    if args.all and not args.category:
        # 주요 카테고리 자동 생성
        for mid_cd in ["049", "050", "072", "044", "016"]:
            report = CategoryDetailReport()
            path = report.generate(mid_cd)
            print(f"카테고리 심층 분석 ({mid_cd}): {path}")


if __name__ == "__main__":
    main()
```

---

## 5. 구현 순서 체크리스트

| # | 파일 | 설명 | 의존성 |
|---|------|------|--------|
| 1 | `src/report/__init__.py` | 패키지 초기화 | 없음 |
| 2 | `src/report/base_report.py` | 공통 기반 (Jinja2 렌더링) | jinja2 |
| 3 | `src/report/templates/base.html` | 공통 레이아웃 + CSS + Chart.js | 없음 |
| 4 | `src/report/daily_order_report.py` | 일일 발주 리포트 | #2 |
| 5 | `src/report/templates/daily_order.html` | 일일 발주 템플릿 | #3 |
| 6 | `scripts/run_report.py` | CLI 진입점 | #4 |
| 7 | `src/report/safety_impact_report.py` | 안전재고 영향도 | #2 |
| 8 | `src/report/templates/safety_impact.html` | 영향도 템플릿 | #3 |
| 9 | `src/report/weekly_trend_report.py` | 주간 트렌드 | #2 |
| 10 | `src/report/templates/weekly_trend.html` | 주간 트렌드 템플릿 | #3 |
| 11 | `src/report/category_detail_report.py` | 카테고리 심층 | #2 |
| 12 | `src/report/templates/category_detail.html` | 카테고리 템플릿 | #3 |

---

## 6. 검증 기준

- [ ] `python scripts/run_report.py --daily` → `data/reports/daily/daily_order_YYYY-MM-DD.html` 생성
- [ ] 생성된 HTML을 브라우저에서 열면 차트/테이블 정상 표시
- [ ] 테이블 검색/정렬 동작
- [ ] `--save-baseline` → JSON 저장 → `--impact --baseline` → 비교 리포트 생성
- [ ] `--weekly` → 7일 트렌드 차트 + 히트맵 표시
- [ ] `--category 049` → 맥주 심층 분석 생성
- [ ] 단일 HTML 파일 (CDN 외 외부 의존 없음)
- [ ] 한글 인코딩 정상 (UTF-8)
