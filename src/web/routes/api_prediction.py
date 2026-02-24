"""예측분석 탭 REST API"""
import time
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, current_app

from src.infrastructure.database.connection import DBRouter
from src.utils.logger import get_logger

logger = get_logger(__name__)

prediction_bp = Blueprint("prediction", __name__)

# API 응답 캐시 (store_id별 TTL 기반)
_summary_cache = {}  # {store_id: {"data": ..., "expires": ...}}
_SUMMARY_CACHE_TTL = 60  # 초


def _get_store_db_path(store_id):
    """store_id에 따른 DB 경로 반환"""
    if store_id:
        return str(DBRouter.get_store_db_path(store_id))
    return current_app.config["DB_PATH"]


@prediction_bp.route("/summary", methods=["GET"])
def prediction_summary():
    """예측분석 요약 카드 4개 데이터 (60초 캐시, 매장별)"""
    now = time.time()
    store_id = request.args.get('store_id')
    cache_key = store_id or "_all"

    cached = _summary_cache.get(cache_key)
    if cached and cached["data"] is not None and now < cached["expires"]:
        return jsonify(cached["data"])

    data = {
        "eval_accuracy": _get_eval_accuracy(store_id=store_id),
        "qty_accuracy": _get_qty_accuracy(store_id=store_id),
        "ml_status": _get_ml_status(),
        "model_type_dist": _get_model_type_dist(store_id=store_id),
    }

    _summary_cache[cache_key] = {"data": data, "expires": now + _SUMMARY_CACHE_TTL}

    return jsonify(data)


@prediction_bp.route("/accuracy-detail", methods=["GET"])
def accuracy_detail():
    """적중률 상세 서브뷰 (lazy load)"""
    store_id = request.args.get('store_id')
    db_path = _get_store_db_path(store_id)

    data = {
        "daily_mape_trend": [],
        "category_accuracy": [],
        "worst_items": [],
        "best_items": [],
    }

    try:
        from src.prediction.accuracy.tracker import AccuracyTracker
        tracker = AccuracyTracker(db_path=db_path, store_id=store_id)

        data["daily_mape_trend"] = tracker.get_daily_mape_trend(14)

        cat_list = tracker.get_accuracy_by_category(7)
        data["category_accuracy"] = [
            {
                "mid_cd": c.mid_cd,
                "mid_nm": c.mid_nm,
                "mape": round(c.metrics.mape, 1),
                "mae": round(c.metrics.mae, 2),
                "accuracy_within_2": round(c.metrics.accuracy_within_2, 1),
                "total": c.metrics.total_predictions,
            }
            for c in cat_list
        ]

        data["worst_items"] = tracker.get_worst_items(7, limit=10)
        data["best_items"] = tracker.get_best_items(7, limit=10)

    except Exception as e:
        logger.warning(f"적중률 상세 조회 실패: {e}")

    return jsonify(data)


@prediction_bp.route("/blending-detail", methods=["GET"])
def blending_detail():
    """블렌딩 비중 서브뷰 (lazy load)"""
    store_id = request.args.get('store_id')

    data = {
        "blending_rules": _get_blending_rules(),
        "group_quantile_alpha": _get_group_quantile_alpha(),
        "eval_config_params": _get_eval_config_params(),
        "calibration_history": _get_calibration_history(store_id=store_id),
        "model_type_dist": _get_model_type_dist(store_id=store_id),
    }

    return jsonify(data)


# === 내부 헬퍼 함수 ===

def _get_eval_accuracy(store_id=None):
    """최근 7일 평가 적중률 통계 (eval_outcomes 기반, 미평가 건 제외)"""
    sf = "AND store_id = ?" if store_id else ""
    sp = (store_id,) if store_id else ()

    conn = DBRouter.get_store_connection(store_id) if store_id else DBRouter.get_connection("store")
    try:
        # 평가 완료건만 집계 (outcome IS NOT NULL)
        row = conn.execute(f"""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN outcome = 'CORRECT' THEN 1 ELSE 0 END) AS hits,
                   SUM(CASE WHEN outcome = 'OVER_ORDER' THEN 1 ELSE 0 END) AS overs
            FROM eval_outcomes
            WHERE created_at >= datetime('now', '-7 days')
              AND outcome IS NOT NULL
              {sf}
        """, sp).fetchone()

        by_decision = conn.execute(f"""
            SELECT decision,
                   COUNT(*) AS cnt,
                   SUM(CASE WHEN outcome = 'CORRECT' THEN 1 ELSE 0 END) AS hits
            FROM eval_outcomes
            WHERE created_at >= datetime('now', '-7 days')
              AND outcome IS NOT NULL
              {sf}
            GROUP BY decision
        """, sp).fetchall()

        # 미평가 건수도 별도 조회 (UI 참고용)
        pending_row = conn.execute(f"""
            SELECT COUNT(*) FROM eval_outcomes
            WHERE created_at >= datetime('now', '-7 days')
              AND outcome IS NULL
              {sf}
        """, sp).fetchone()
    finally:
        conn.close()

    total = row[0] or 0
    hits = row[1] or 0
    overs = row[2] or 0
    pending = pending_row[0] or 0
    accuracy = round(hits / total * 100, 1) if total > 0 else 0

    decision_stats = {}
    for r in by_decision:
        dec_total = r[1] or 0
        dec_hits = r[2] or 0
        decision_stats[r[0]] = {
            "total": dec_total,
            "hits": dec_hits,
            "accuracy": round(dec_hits / dec_total * 100, 1) if dec_total > 0 else 0,
        }

    return {
        "total": total,
        "hits": hits,
        "overs": overs,
        "accuracy": accuracy,
        "pending": pending,
        "by_decision": decision_stats,
    }


def _get_qty_accuracy(store_id=None):
    """최근 7일 예측량 정확도 (AccuracyTracker)"""
    try:
        from src.prediction.accuracy.tracker import AccuracyTracker
        db_path = _get_store_db_path(store_id)
        tracker = AccuracyTracker(db_path=db_path, store_id=store_id)
        metrics = tracker.get_accuracy_by_period(days=7)

        return {
            "total": metrics.total_predictions,
            "mape": round(metrics.mape, 1),
            "mae": round(metrics.mae, 2),
            "accuracy_within_2": round(metrics.accuracy_within_2, 1),
            "over_rate": round(metrics.over_prediction_rate, 1),
            "under_rate": round(metrics.under_prediction_rate, 1),
        }
    except Exception as e:
        logger.warning(f"예측 정확도 조회 실패: {e}")
        return {
            "total": 0, "mape": 0, "mae": 0,
            "accuracy_within_2": 0, "over_rate": 0, "under_rate": 0,
        }


def _get_ml_status():
    """ML 모델 현황"""
    try:
        from src.prediction.ml.model import MLPredictor
        predictor = MLPredictor()
        info = predictor.get_model_info()

        groups = info.get("groups", {})
        active_groups = sum(1 for g in groups.values() if g.get("has_model"))
        total_groups = len(groups)

        # 최신 학습 시각
        latest_training = None
        for g in groups.values():
            mod = g.get("modified")
            if mod and (latest_training is None or mod > latest_training):
                latest_training = mod

        return {
            "available": info.get("available", False),
            "active_groups": active_groups,
            "total_groups": total_groups,
            "latest_training": latest_training,
            "groups": groups,
        }
    except Exception as e:
        logger.warning(f"ML 모델 상태 조회 실패: {e}")
        return {
            "available": False,
            "active_groups": 0,
            "total_groups": 5,
            "latest_training": None,
            "groups": {},
        }


def _get_model_type_dist(store_id=None):
    """최근 7일 예측 방식별 분포 (prediction_logs.model_type)"""
    sf = "AND store_id = ?" if store_id else ""
    sp = (store_id,) if store_id else ()

    conn = DBRouter.get_store_connection(store_id) if store_id else DBRouter.get_connection("store")
    try:
        rows = conn.execute(f"""
            SELECT COALESCE(model_type, 'rule') AS mt, COUNT(*) AS cnt
            FROM prediction_logs
            WHERE prediction_date >= date('now', '-7 days')
              {sf}
            GROUP BY mt
        """, sp).fetchall()
    finally:
        conn.close()

    dist = {"rule": 0, "ensemble_30": 0, "ensemble_50": 0}
    total = 0
    for r in rows:
        mt = r[0]
        cnt = r[1] or 0
        if mt in dist:
            dist[mt] = cnt
        else:
            dist[mt] = cnt
        total += cnt

    dist["total"] = total
    return dist


def _get_blending_rules():
    """블렌딩 규칙 정의 (정적)"""
    return {
        "rule": {
            "label": "규칙 기반 (100%)",
            "condition": "데이터 30일 미만 또는 ML 모델 없음",
            "ml_weight": 0.0,
        },
        "ensemble_30": {
            "label": "앙상블 30%",
            "condition": "데이터 30~60일",
            "ml_weight": 0.3,
        },
        "ensemble_50": {
            "label": "앙상블 50%",
            "condition": "데이터 60일 이상",
            "ml_weight": 0.5,
        },
    }


def _get_group_quantile_alpha():
    """카테고리 그룹별 quantile alpha 설정"""
    return {
        "food_group": 0.6,
        "alcohol_group": 0.55,
        "tobacco_group": 0.5,
        "perishable_group": 0.6,
        "general_group": 0.5,
    }


def _get_eval_config_params():
    """평가 파라미터 현재 설정값"""
    try:
        from src.prediction.eval_config import EvalConfig
        config = EvalConfig.load()
        params_dict = config.to_dict()

        result = []
        for name, info in params_dict.items():
            result.append({
                "name": name,
                "value": info.get("value"),
                "default": info.get("default"),
                "desc": info.get("description", ""),
            })
        return result
    except Exception as e:
        logger.warning(f"EvalConfig 조회 실패: {e}")
        return []


def _get_calibration_history(store_id=None):
    """최근 보정 이력 (calibration_history 테이블)"""
    sf = "AND store_id = ?" if store_id else ""
    sp = (store_id,) if store_id else ()

    conn = DBRouter.get_store_connection(store_id) if store_id else DBRouter.get_connection("store")
    try:
        rows = conn.execute(f"""
            SELECT calibration_date, param_name, old_value, new_value, reason
            FROM calibration_history
            WHERE 1=1 {sf}
            ORDER BY id DESC
            LIMIT 20
        """, sp).fetchall()
    except Exception as e:
        logger.warning(f"보정 이력 조회 실패: {e}")
        return []
    finally:
        conn.close()

    return [
        {
            "date": r[0],
            "param": r[1],
            "old": r[2],
            "new": r[3],
            "reason": r[4],
        }
        for r in rows
    ]
