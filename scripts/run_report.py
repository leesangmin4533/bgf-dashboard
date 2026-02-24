"""
HTML 리포트 생성 CLI

사용법:
    python scripts/run_report.py --daily              # 일일 발주 리포트
    python scripts/run_report.py --weekly              # 주간 트렌드 리포트
    python scripts/run_report.py --category 049        # 카테고리 심층 분석
    python scripts/run_report.py --save-baseline       # 안전재고 baseline 저장
    python scripts/run_report.py --impact baseline.json # 영향도 리포트
    python scripts/run_report.py --all                 # 전체 리포트 생성
"""

import sys
import io

# Windows CP949 콘솔 → UTF-8 래핑 (한글·특수문자 깨짐 방지)
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

import os
import argparse
from pathlib import Path
from datetime import datetime

# 프로젝트 루트와 src 폴더를 path에 추가
project_root = Path(__file__).parent.parent
src_root = project_root / "src"
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(src_root))

os.chdir(str(src_root))

from prediction.improved_predictor import ImprovedPredictor
from prediction.categories.default import CATEGORY_NAMES
from report import (
    DailyOrderReport,
    SafetyImpactReport,
    WeeklyTrendReportHTML,
    CategoryDetailReport,
)
from utils.logger import get_logger

logger = get_logger(__name__)

DB_PATH = str(project_root / "data" / "bgf_sales.db")


def get_predictions(max_items: int = 0):
    """예측 결과 조회"""
    predictor = ImprovedPredictor()
    candidates = predictor.get_order_candidates(min_order_qty=0)
    if max_items > 0:
        candidates = candidates[:max_items]
    return candidates


def run_daily(max_items: int = 0):
    """일일 발주 리포트 생성"""
    print("[일일 발주 리포트 생성 중...]")
    predictions = get_predictions(max_items)
    report = DailyOrderReport(DB_PATH)
    path = report.generate(predictions)
    print(f"  생성 완료: {path}")
    return path


def run_weekly(end_date: str = None):
    """주간 트렌드 리포트 생성"""
    print("[주간 트렌드 리포트 생성 중...]")
    report = WeeklyTrendReportHTML(DB_PATH)
    path = report.generate(end_date)
    print(f"  생성 완료: {path}")
    return path


def run_category(mid_cd: str):
    """카테고리 심층 분석 리포트 생성"""
    cat_name = CATEGORY_NAMES.get(mid_cd, mid_cd)
    print(f"[카테고리 분석 리포트 생성 중...] {mid_cd} ({cat_name})")
    report = CategoryDetailReport(DB_PATH)
    path = report.generate(mid_cd)
    print(f"  생성 완료: {path}")
    return path


def run_save_baseline(max_items: int = 0):
    """안전재고 baseline 저장"""
    print("[안전재고 baseline 저장 중...]")
    predictions = get_predictions(max_items)
    report = SafetyImpactReport(DB_PATH)
    path = report.save_baseline(predictions)
    print(f"  저장 완료: {path}")
    print(f"  상품 수: {len(predictions)}")
    return path


def run_impact(baseline_path: str, max_items: int = 0, change_date: str = None):
    """안전재고 영향도 리포트 생성"""
    print(f"[영향도 리포트 생성 중...] baseline: {baseline_path}")
    if not Path(baseline_path).exists():
        print(f"  오류: baseline 파일 없음 - {baseline_path}")
        return None
    predictions = get_predictions(max_items)
    report = SafetyImpactReport(DB_PATH)
    path = report.generate(predictions, baseline_path, change_date)
    print(f"  생성 완료: {path}")
    return path


def main():
    parser = argparse.ArgumentParser(description="HTML 리포트 생성")
    parser.add_argument("--daily", action="store_true", help="일일 발주 리포트")
    parser.add_argument("--weekly", action="store_true", help="주간 트렌드 리포트")
    parser.add_argument("--category", type=str, metavar="MID_CD", help="카테고리 심층 분석 (중분류 코드)")
    parser.add_argument("--save-baseline", action="store_true", help="안전재고 baseline 저장")
    parser.add_argument("--impact", type=str, metavar="BASELINE_JSON", help="영향도 리포트 (baseline JSON 경로)")
    parser.add_argument("--all", action="store_true", help="전체 리포트 생성 (daily + weekly)")
    parser.add_argument("--max-items", type=int, default=0, help="최대 상품 수 (0=전체)")
    parser.add_argument("--end-date", type=str, help="주간 리포트 기준일 (YYYY-MM-DD)")
    parser.add_argument("--change-date", type=str, help="영향도 리포트 변경일 (YYYY-MM-DD)")
    parser.add_argument("--list-categories", action="store_true", help="카테고리 코드 목록 출력")

    args = parser.parse_args()

    # 카테고리 목록 출력
    if args.list_categories:
        print("중분류 코드 목록:")
        for code, name in sorted(CATEGORY_NAMES.items()):
            print(f"  {code}: {name}")
        return

    # 아무 옵션도 없으면 도움말
    if not any([args.daily, args.weekly, args.category, args.save_baseline, args.impact, args.all]):
        parser.print_help()
        return

    results = []
    start = datetime.now()

    if args.all or args.daily:
        path = run_daily(args.max_items)
        if path:
            results.append(("일일 발주", path))

    if args.all or args.weekly:
        path = run_weekly(args.end_date)
        if path:
            results.append(("주간 트렌드", path))

    if args.category:
        path = run_category(args.category)
        if path:
            results.append(("카테고리 분석", path))

    if args.save_baseline:
        path = run_save_baseline(args.max_items)
        if path:
            results.append(("Baseline", path))

    if args.impact:
        path = run_impact(args.impact, args.max_items, args.change_date)
        if path:
            results.append(("영향도", path))

    # 결과 요약
    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n{'='*50}")
    print(f"리포트 생성 완료 ({len(results)}건)")
    print(f"{'='*50}")
    for name, path in results:
        print(f"  [{name}] {path}")
    print(f"소요: {elapsed:.1f}초")


if __name__ == "__main__":
    main()
