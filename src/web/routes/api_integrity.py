"""데이터 무결성 검증 REST API"""

from flask import Blueprint, jsonify, request

from src.application.services.data_integrity_service import DataIntegrityService
from src.infrastructure.database.repos.integrity_check_repo import IntegrityCheckRepository
from src.settings.constants import DEFAULT_STORE_ID
from src.utils.logger import get_logger

logger = get_logger(__name__)

integrity_bp = Blueprint("integrity", __name__)


@integrity_bp.route("/latest", methods=["GET"])
def get_latest_checks():
    """최근 검증 결과 조회

    Query params:
        store_id: 매장 코드 (기본: DEFAULT_STORE_ID)
        days: 조회 기간 (기본: 7)
    """
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)
    days = int(request.args.get("days", 7))

    try:
        repo = IntegrityCheckRepository(store_id=store_id)
        results = repo.get_latest_results(store_id, days=days)
        return jsonify({
            "store_id": store_id,
            "days": days,
            "total": len(results),
            "checks": results,
        })
    except Exception as e:
        logger.error(f"무결성 검증 결과 조회 실패: {e}")
        return jsonify({"error": "처리에 실패했습니다"}), 500


@integrity_bp.route("/run", methods=["POST"])
def run_integrity_checks():
    """수동 무결성 검증 실행

    Query params:
        store_id: 매장 코드 (기본: DEFAULT_STORE_ID)
    """
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)

    try:
        svc = DataIntegrityService(store_id=store_id)
        result = svc.run_all_checks(store_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f"무결성 검증 실행 실패: {e}")
        return jsonify({"error": "처리에 실패했습니다"}), 500


@integrity_bp.route("/trend/<check_name>", methods=["GET"])
def get_anomaly_trend(check_name: str):
    """특정 검증 항목 이상치 추이 (차트용)

    Query params:
        store_id: 매장 코드 (기본: DEFAULT_STORE_ID)
        days: 조회 기간 (기본: 30)
    """
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)
    days = int(request.args.get("days", 30))

    try:
        repo = IntegrityCheckRepository(store_id=store_id)
        trend = repo.get_anomaly_trend(store_id, check_name, days=days)
        return jsonify({
            "store_id": store_id,
            "check_name": check_name,
            "days": days,
            "trend": trend,
        })
    except Exception as e:
        logger.error(f"이상치 추이 조회 실패: {e}")
        return jsonify({"error": "처리에 실패했습니다"}), 500
