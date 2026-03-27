"""통합 카테고리 판단 API — 디저트 + 음료 합산 pending count.

GET /api/category-decision/pending-count
"""

from flask import Blueprint, jsonify, request

from src.settings.constants import DEFAULT_STORE_ID
from src.utils.logger import get_logger

logger = get_logger(__name__)

category_decision_bp = Blueprint("category_decision", __name__)


def _get_store_id() -> str:
    return request.args.get("store_id", DEFAULT_STORE_ID)


@category_decision_bp.route("/pending-count", methods=["GET"])
def pending_count():
    """디저트 + 음료 미확인 STOP_RECOMMEND 건수 합산."""
    store_id = _get_store_id()

    dessert_count = 0
    beverage_count = 0

    try:
        from src.infrastructure.database.repos import DessertDecisionRepository
        dessert_count = DessertDecisionRepository(store_id=store_id).get_pending_stop_count()
    except Exception as e:
        logger.warning(f"[CategoryDecisionAPI] dessert pending count 실패: {e}")

    try:
        from src.infrastructure.database.repos import BeverageDecisionRepository
        beverage_count = BeverageDecisionRepository(store_id=store_id).get_pending_stop_count()
    except Exception as e:
        logger.warning(f"[CategoryDecisionAPI] beverage pending count 실패: {e}")

    return jsonify({
        "success": True,
        "data": {
            "dessert": dessert_count,
            "beverage": beverage_count,
            "total": dessert_count + beverage_count,
        }
    })
