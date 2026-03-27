"""디저트 발주 유지/정지 판단 REST API.

GET  /api/dessert-decision/latest              -- 최신 판단 결과
GET  /api/dessert-decision/history/<item_cd>    -- 상품별 이력
GET  /api/dessert-decision/summary             -- 카테고리별 집계 (+history 파라미터)
POST /api/dessert-decision/action/<decision_id> -- 운영자 액션
POST /api/dessert-decision/action/batch        -- 일괄 운영자 확인
POST /api/dessert-decision/run                 -- 수동 실행
"""

from flask import Blueprint, jsonify, request, session

from src.settings.constants import DEFAULT_STORE_ID
from src.utils.logger import get_logger

logger = get_logger(__name__)

dessert_decision_bp = Blueprint("dessert_decision", __name__)


def _get_store_id() -> str:
    return request.args.get("store_id", DEFAULT_STORE_ID)


@dessert_decision_bp.route("/latest", methods=["GET"])
def get_latest_decisions():
    """최신 판단 결과 조회"""
    store_id = _get_store_id()
    category = request.args.get("category")  # A/B/C/D 필터 (선택)

    try:
        from src.infrastructure.database.repos import DessertDecisionRepository
        repo = DessertDecisionRepository(store_id=store_id)
        decisions = repo.get_latest_decisions()

        if category:
            decisions = [d for d in decisions if d.get("dessert_category") == category]

        return jsonify({"success": True, "data": decisions, "total": len(decisions)})
    except Exception as e:
        logger.error(f"[DessertDecisionAPI] latest 조회 실패: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@dessert_decision_bp.route("/history/<item_cd>", methods=["GET"])
def get_item_history(item_cd: str):
    """상품별 판단 이력"""
    store_id = _get_store_id()
    limit = request.args.get("limit", 20, type=int)

    try:
        from src.infrastructure.database.repos import DessertDecisionRepository
        repo = DessertDecisionRepository(store_id=store_id)
        history = repo.get_item_decision_history(item_cd, limit=limit)
        return jsonify({"success": True, "item_cd": item_cd, "data": history})
    except Exception as e:
        logger.error(f"[DessertDecisionAPI] history 조회 실패: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@dessert_decision_bp.route("/summary", methods=["GET"])
def get_summary():
    """카테고리별 KEEP/WATCH/STOP 집계.

    Query params:
        history: 주간 추이 데이터 포함 (예: '8w' → 최근 8주)
    """
    store_id = _get_store_id()

    try:
        from src.infrastructure.database.repos import DessertDecisionRepository
        repo = DessertDecisionRepository(store_id=store_id)
        summary = repo.get_decision_summary()
        result = {"success": True, "data": summary}

        # history 파라미터: 주간 추이 데이터 추가
        history = request.args.get("history")
        if history and history.endswith("w"):
            try:
                weeks = int(history.replace("w", ""))
                weeks = min(weeks, 52)  # 최대 52주
                result["data"]["weekly_trend"] = repo.get_weekly_trend(
                    store_id=store_id, weeks=weeks
                )
            except (ValueError, TypeError):
                pass

        return jsonify(result)
    except Exception as e:
        logger.error(f"[DessertDecisionAPI] summary 조회 실패: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@dessert_decision_bp.route("/action/<int:decision_id>", methods=["POST"])
def update_action(decision_id: int):
    """운영자 액션 (CONFIRMED_STOP / OVERRIDE_KEEP)"""
    store_id = _get_store_id()
    data = request.get_json(silent=True) or {}

    action = data.get("action")
    note = data.get("note", "")

    if action not in ("CONFIRMED_STOP", "OVERRIDE_KEEP"):
        return jsonify({"success": False, "error": "action must be CONFIRMED_STOP or OVERRIDE_KEEP"}), 400

    try:
        from src.infrastructure.database.repos import DessertDecisionRepository
        repo = DessertDecisionRepository(store_id=store_id)
        updated = repo.update_operator_action(decision_id, action, note)
        if updated:
            return jsonify({"success": True, "decision_id": decision_id, "action": action})
        else:
            return jsonify({"success": False, "error": "decision not found"}), 404
    except Exception as e:
        logger.error(f"[DessertDecisionAPI] action 업데이트 실패: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@dessert_decision_bp.route("/action/batch", methods=["POST"])
def batch_action():
    """일괄 운영자 확인.

    Request body:
        item_cds: 상품코드 리스트 (1~50개)
        action: 'CONFIRMED_STOP' 또는 'OVERRIDE_KEEP'
        note: 운영자 메모 (선택)
    """
    data = request.get_json(silent=True) or {}
    item_cds = data.get("item_cds", [])
    action = data.get("action")

    if action not in ("CONFIRMED_STOP", "OVERRIDE_KEEP"):
        return jsonify({"success": False, "error": "action must be CONFIRMED_STOP or OVERRIDE_KEEP"}), 400
    if not item_cds or len(item_cds) > 50:
        return jsonify({"success": False, "error": "item_cds: 1~50개"}), 400

    store_id = _get_store_id()

    try:
        from src.infrastructure.database.repos import DessertDecisionRepository
        repo = DessertDecisionRepository(store_id=store_id)
        results = repo.batch_update_operator_action(
            item_cds=item_cds,
            action=action,
            operator_note=data.get("note", "batch"),
            store_id=store_id,
        )
        return jsonify({
            "success": True,
            "updated_count": len(results),
            "items": results,
        })
    except Exception as e:
        logger.error(f"[DessertDecisionAPI] batch action 실패: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@dessert_decision_bp.route("/run", methods=["POST"])
def run_decision():
    """수동 실행"""
    store_id = _get_store_id()
    data = request.get_json(silent=True) or {}

    target_categories = data.get("categories")  # ["A","B"] 등
    reference_date = data.get("reference_date")

    try:
        from src.application.use_cases.dessert_decision_flow import DessertDecisionFlow
        flow = DessertDecisionFlow(store_id=store_id)
        result = flow.run(
            target_categories=target_categories,
            reference_date=reference_date,
        )
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"[DessertDecisionAPI] 수동 실행 실패: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
