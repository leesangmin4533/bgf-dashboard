"""연관 패턴 효과 분석 REST API"""

from flask import Blueprint, jsonify, request

from src.application.services.association_stats_service import AssociationStatsService
from src.utils.logger import get_logger

logger = get_logger(__name__)

association_bp = Blueprint("association", __name__)


@association_bp.route("/status", methods=["GET"])
def association_status():
    """데이터 수집 현황 및 분석 준비 여부"""
    store_id = request.args.get("store_id")
    try:
        svc = AssociationStatsService(store_id=store_id)
        data = svc.get_status()
        return jsonify(data)
    except Exception as e:
        logger.error(f"[연관분석 API] status 실패: {e}")
        return jsonify({"error": str(e)}), 500


@association_bp.route("/analysis", methods=["GET"])
def association_analysis():
    """부스트 효과 분석 결과"""
    store_id = request.args.get("store_id")
    try:
        svc = AssociationStatsService(store_id=store_id)
        data = svc.get_analysis()
        return jsonify(data)
    except Exception as e:
        logger.error(f"[연관분석 API] analysis 실패: {e}")
        return jsonify({"error": str(e)}), 500
