"""폐기 원인 분석 REST API"""

import sqlite3
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from src.infrastructure.database.repos import WasteCauseRepository
from src.settings.constants import DEFAULT_STORE_ID, FOOD_CATEGORIES
from src.utils.logger import get_logger

logger = get_logger(__name__)

waste_bp = Blueprint("waste", __name__)


@waste_bp.route("/causes", methods=["GET"])
def get_waste_causes():
    """최근 N일 폐기 원인 분석 결과 조회

    Query params:
        store_id: 매장 코드 (기본: DEFAULT_STORE_ID)
        days: 조회 기간 (기본: 14)
        start_date: 시작일 (YYYY-MM-DD, days보다 우선)
        end_date: 종료일 (YYYY-MM-DD, days보다 우선)
    """
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)
    days = int(request.args.get("days", 14))
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    if not start_date:
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")

    try:
        repo = WasteCauseRepository(store_id=store_id)
        causes = repo.get_causes_for_period(start_date, end_date, store_id=store_id)
        return jsonify({
            "store_id": store_id,
            "start_date": start_date,
            "end_date": end_date,
            "total": len(causes),
            "causes": causes,
        })
    except Exception as e:
        logger.error(f"폐기 원인 조회 실패: {e}")
        return jsonify({"error": "처리에 실패했습니다"}), 500


@waste_bp.route("/summary", methods=["GET"])
def get_waste_summary():
    """원인별 집계 (대시보드 위젯용)

    Query params:
        store_id: 매장 코드 (기본: DEFAULT_STORE_ID)
        days: 집계 기간 (기본: 14)
    """
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)
    days = int(request.args.get("days", 14))

    try:
        repo = WasteCauseRepository(store_id=store_id)
        summary = repo.get_cause_summary(days=days, store_id=store_id)
        total_count = sum(v["count"] for v in summary.values())
        total_qty = sum(v["total_qty"] for v in summary.values())
        return jsonify({
            "store_id": store_id,
            "days": days,
            "total_count": total_count,
            "total_qty": total_qty,
            "by_cause": summary,
        })
    except Exception as e:
        logger.error(f"폐기 원인 집계 실패: {e}")
        return jsonify({"error": "처리에 실패했습니다"}), 500


@waste_bp.route("/feedback/<item_cd>", methods=["GET"])
def get_item_feedback(item_cd):
    """특정 상품의 현재 활성 피드백 조회

    Query params:
        store_id: 매장 코드 (기본: DEFAULT_STORE_ID)
    """
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        repo = WasteCauseRepository(store_id=store_id)
        feedback = repo.get_active_feedback(item_cd, today, store_id=store_id)
        if feedback:
            return jsonify({
                "item_cd": item_cd,
                "has_feedback": True,
                "feedback": feedback,
            })
        return jsonify({
            "item_cd": item_cd,
            "has_feedback": False,
            "feedback": None,
        })
    except Exception as e:
        logger.error(f"피드백 조회 실패 ({item_cd}): {e}")
        return jsonify({"error": "처리에 실패했습니다"}), 500


@waste_bp.route("/calibration", methods=["GET"])
def get_waste_calibration():
    """푸드 폐기율 자동 보정 현황 (전체 카테고리)

    Query params:
        store_id: 매장 코드 (기본: DEFAULT_STORE_ID)
        days: 이력 조회 기간 (기본: 30)
    """
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)
    days = int(request.args.get("days", 30))

    try:
        from src.prediction.food_waste_calibrator import get_effective_params
        from src.infrastructure.database.connection import DBRouter

        conn = DBRouter.get_store_connection(store_id) if store_id else None
        if conn is None:
            return jsonify({"error": "store_id required"}), 400

        cursor = conn.cursor()
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        results = {}
        for mid_cd in FOOD_CATEGORIES:
            params = get_effective_params(mid_cd, store_id)

            # 최근 보정 이력
            try:
                cursor.execute("""
                    SELECT calibration_date, actual_waste_rate, target_waste_rate,
                           error, param_name, old_value, new_value
                    FROM food_waste_calibration
                    WHERE mid_cd = ? AND store_id = ?
                    AND calibration_date >= ?
                    ORDER BY calibration_date DESC
                    LIMIT 10
                """, (mid_cd, store_id, start_date))
                history = [
                    {
                        "date": r[0], "actual": r[1], "target": r[2],
                        "error": r[3], "param": r[4],
                        "old": r[5], "new": r[6],
                    }
                    for r in cursor.fetchall()
                ]
            except sqlite3.OperationalError:
                history = []

            results[mid_cd] = {
                "current_params": {
                    "safety_days": params.safety_days,
                    "gap_coefficient": params.gap_coefficient,
                    "waste_buffer": params.waste_buffer,
                },
                "history": history,
            }

        conn.close()
        return jsonify({
            "store_id": store_id,
            "categories": results,
        })
    except Exception as e:
        logger.error(f"폐기율 보정 현황 조회 실패: {e}")
        return jsonify({"error": "처리에 실패했습니다"}), 500


@waste_bp.route("/calibration/<mid_cd>", methods=["GET"])
def get_waste_calibration_detail(mid_cd):
    """특정 카테고리의 폐기율 보정 상세 이력

    Query params:
        store_id: 매장 코드 (기본: DEFAULT_STORE_ID)
        days: 이력 조회 기간 (기본: 60)
    """
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)
    days = int(request.args.get("days", 60))

    try:
        from src.prediction.food_waste_calibrator import get_effective_params
        from src.infrastructure.database.connection import DBRouter

        conn = DBRouter.get_store_connection(store_id) if store_id else None
        if conn is None:
            return jsonify({"error": "store_id required"}), 400

        params = get_effective_params(mid_cd, store_id)
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT calibration_date, actual_waste_rate, target_waste_rate,
                       error, sample_days, total_order_qty, total_waste_qty,
                       total_sold_qty, param_name, old_value, new_value,
                       current_params
                FROM food_waste_calibration
                WHERE mid_cd = ? AND store_id = ?
                AND calibration_date >= ?
                ORDER BY calibration_date DESC
            """, (mid_cd, store_id, start_date))
            history = [
                {
                    "date": r[0], "actual": r[1], "target": r[2],
                    "error": r[3], "sample_days": r[4],
                    "total_order": r[5], "total_waste": r[6],
                    "total_sold": r[7], "param": r[8],
                    "old": r[9], "new": r[10],
                }
                for r in cursor.fetchall()
            ]
        except sqlite3.OperationalError:
            history = []

        conn.close()
        return jsonify({
            "store_id": store_id,
            "mid_cd": mid_cd,
            "current_params": {
                "safety_days": params.safety_days,
                "gap_coefficient": params.gap_coefficient,
                "waste_buffer": params.waste_buffer,
            },
            "history": history,
        })
    except Exception as e:
        logger.error(f"폐기율 보정 이력 조회 실패 ({mid_cd}): {e}")
        return jsonify({"error": "처리에 실패했습니다"}), 500
