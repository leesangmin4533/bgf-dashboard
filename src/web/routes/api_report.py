"""리포트 데이터 REST API"""
import json
import time as _time
from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, request, jsonify, current_app

from src.utils.logger import get_logger
from src.infrastructure.database.connection import DBRouter
from src.prediction.improved_predictor import ImprovedPredictor
from src.report.daily_order_report import DailyOrderReport
from src.report.weekly_trend_report import WeeklyTrendReportHTML
from src.report.category_detail_report import CategoryDetailReport
from src.report.safety_impact_report import SafetyImpactReport

logger = get_logger(__name__)

report_bp = Blueprint("report", __name__)

# 리포트 예측 캐시 TTL
_PREDICTION_CACHE_TTL = 300  # 5분


def _get_store_db_path(store_id):
    """store_id에 따른 DB 경로 반환"""
    if store_id:
        return str(DBRouter.get_store_db_path(store_id))
    return current_app.config["DB_PATH"]


def _get_or_run_predictions(max_items: int = 0, store_id: str = None):
    """캐시된 예측 결과 반환, 없으면 새로 실행 (store_id별 캐시, TTL 5분)"""
    from src.settings.constants import DEFAULT_STORE_ID
    sid = store_id or DEFAULT_STORE_ID
    now = _time.time()

    cache_key = f"LAST_PREDICTIONS_{sid}"
    cache_entry = current_app.config.get(cache_key)

    # TTL 기반 캐시 확인
    if isinstance(cache_entry, dict) and cache_entry.get("data"):
        if now < cache_entry.get("expires", 0):
            return cache_entry["data"]
    elif isinstance(cache_entry, list) and cache_entry:
        # 레거시 호환: list가 직접 저장된 경우 (만료 없이 사용)
        return cache_entry

    predictor = ImprovedPredictor(store_id=sid)
    candidates = predictor.get_order_candidates(min_order_qty=0)
    if max_items and max_items > 0:
        candidates = candidates[:max_items]

    current_app.config[cache_key] = {
        "data": candidates,
        "expires": now + _PREDICTION_CACHE_TTL,
    }
    return candidates


@report_bp.route("/daily", methods=["GET"])
def daily():
    """일일 발주 데이터 (predict 결과 재활용)"""
    store_id = request.args.get("store_id")
    try:
        predictions = _get_or_run_predictions(store_id=store_id)
        report = DailyOrderReport()
        return jsonify({
            "summary": report._calc_summary(predictions),
            "category_data": report._group_by_category(predictions),
            "items": report._build_item_table(predictions),
            "skipped": report._build_skipped_list(predictions),
            "safety_dist": report._build_safety_distribution(predictions),
        })
    except Exception as e:
        logger.error(f"일일 리포트 조회 실패: {e}")
        return jsonify({"error": "일일 리포트 데이터 조회에 실패했습니다"}), 500


@report_bp.route("/weekly", methods=["GET"])
def weekly():
    """주간 트렌드 데이터"""
    store_id = request.args.get("store_id")
    end_date = request.args.get("end_date")
    if not end_date:
        end_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        db_path = _get_store_db_path(store_id)
        rpt = WeeklyTrendReportHTML(db_path=db_path, store_id=store_id)
        return jsonify({
            "weekly_summary": rpt._query_weekly_summary(end_date),
            "daily_trend": rpt._query_daily_category_sales(end_date),
            "heatmap": rpt._query_weekday_heatmap(),
            "top_items": rpt._query_top_items(end_date),
            "accuracy": rpt._query_accuracy(end_date),
        })
    except Exception as e:
        logger.error(f"주간 리포트 조회 실패: {e}")
        return jsonify({"error": "주간 리포트 데이터 조회에 실패했습니다"}), 500


@report_bp.route("/category/<mid_cd>", methods=["GET"])
def category(mid_cd):
    """카테고리 분석 데이터"""
    store_id = request.args.get("store_id")
    try:
        db_path = _get_store_db_path(store_id)
        rpt = CategoryDetailReport(db_path=db_path, store_id=store_id)

        from src.prediction.categories.default import CATEGORY_NAMES
        cat_name = CATEGORY_NAMES.get(mid_cd, mid_cd)

        return jsonify({
            "mid_cd": mid_cd,
            "cat_name": cat_name,
            "overview": rpt._query_overview(mid_cd),
            "weekday_coefs": rpt._get_weekday_comparison(mid_cd),
            "turnover_dist": rpt._query_turnover_distribution(mid_cd),
            "sparklines": rpt._query_sparklines(mid_cd),
            "safety_config": rpt._get_safety_config(),
        })
    except Exception as e:
        logger.error(f"카테고리 리포트 조회 실패 ({mid_cd}): {e}")
        return jsonify({"error": "카테고리 리포트 데이터 조회에 실패했습니다"}), 500


@report_bp.route("/impact", methods=["GET"])
def impact():
    """영향도 데이터"""
    store_id = request.args.get("store_id")
    baseline_param = request.args.get("baseline")

    if not baseline_param:
        # 가장 최근 baseline 자동 탐색
        project_root = current_app.config["PROJECT_ROOT"]
        impact_dir = Path(project_root) / "data" / "reports" / "impact"
        if impact_dir.exists():
            baselines = sorted(impact_dir.glob("baseline_*.json"), reverse=True)
            if baselines:
                baseline_param = str(baselines[0])

    if not baseline_param or not Path(baseline_param).exists():
        return jsonify({"error": "Baseline 파일이 없습니다. 먼저 Baseline을 저장하세요."}), 404

    try:
        predictions = _get_or_run_predictions(store_id=store_id)
        db_path = _get_store_db_path(store_id)
        rpt = SafetyImpactReport(db_path=db_path, store_id=store_id)

        baseline = json.loads(Path(baseline_param).read_text(encoding="utf-8"))
        change_date = request.args.get("change_date", datetime.now().strftime("%Y-%m-%d"))

        comparisons = rpt._build_comparisons(predictions, baseline)
        cat_summary = rpt._aggregate_by_category(comparisons)
        summary = rpt._calc_overall_summary(comparisons)
        stockout_trend = rpt._query_stockout_trend(change_date)

        return jsonify({
            "change_date": change_date,
            "summary": summary,
            "comparisons": sorted(comparisons, key=lambda x: x["pct_change"]),
            "cat_summary": cat_summary,
            "stockout_trend": stockout_trend,
        })
    except Exception as e:
        logger.error(f"영향도 리포트 조회 실패: {e}")
        return jsonify({"error": "영향도 리포트 데이터 조회에 실패했습니다"}), 500


@report_bp.route("/baseline", methods=["POST"])
def save_baseline():
    """Baseline 저장"""
    store_id = request.args.get("store_id")
    data = request.get_json(force=True) or {}
    max_items = data.get("max_items", 0)

    try:
        predictions = _get_or_run_predictions(max_items, store_id=store_id)
        db_path = _get_store_db_path(store_id)
        rpt = SafetyImpactReport(db_path=db_path, store_id=store_id)
        filepath = rpt.save_baseline(predictions)

        return jsonify({
            "status": "ok",
            "path": str(filepath),
            "item_count": len(predictions),
        })
    except Exception as e:
        logger.error(f"Baseline 저장 실패: {e}")
        return jsonify({"error": "Baseline 저장에 실패했습니다"}), 500
