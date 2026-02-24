"""규칙 현황판 REST API"""

import time as _time

from flask import Blueprint, jsonify, request

from src.utils.logger import get_logger

logger = get_logger(__name__)

rules_bp = Blueprint("rules", __name__)

# ---------------------------------------------------------------------------
# 캐시 (60초 TTL)
# ---------------------------------------------------------------------------
_rules_cache: dict = {}
_RULES_CACHE_TTL = 60


def _get_cached(key: str):
    cached = _rules_cache.get(key)
    if cached and _time.time() < cached["expires"]:
        return cached["data"]
    return None


def _set_cached(key: str, data):
    _rules_cache[key] = {"data": data, "expires": _time.time() + _RULES_CACHE_TTL}


# ---------------------------------------------------------------------------
# GET /api/rules/all — 전체 규칙 (그룹별)
# ---------------------------------------------------------------------------
@rules_bp.route("/all", methods=["GET"])
def rules_all():
    """전체 규칙 목록 (그룹별 정렬, 60초 캐시)"""
    store_id = request.args.get("store_id", "")
    cache_key = f"rules_all_{store_id}" if store_id else "rules_all"
    cached = _get_cached(cache_key)
    if cached:
        return jsonify(cached)

    try:
        from src.web.services.rule_registry import get_rules_by_group, get_rule_summary

        groups = get_rules_by_group()
        summary = get_rule_summary()

        result = {"groups": groups, "summary": summary}
        _set_cached(cache_key, result)
        return jsonify(result)
    except Exception as e:
        logger.error(f"규칙 조회 실패: {e}")
        return jsonify({"error": "규칙 데이터 조회에 실패했습니다"}), 500


# ---------------------------------------------------------------------------
# GET /api/rules/trace?item_cd=XXX&store_id=YYY — 상품별 규칙 추적
# ---------------------------------------------------------------------------
@rules_bp.route("/trace", methods=["GET"])
def rules_trace():
    """상품별 규칙 적용 추적"""
    item_cd = request.args.get("item_cd", "").strip()
    store_id = request.args.get("store_id", "")

    if not item_cd:
        return jsonify({"error": "item_cd 파라미터가 필요합니다"}), 400

    try:
        from src.web.services.rule_registry import trace_product_rules

        result = trace_product_rules(item_cd, store_id or None)
        return jsonify(result)
    except Exception as e:
        logger.error(f"규칙 추적 실패 ({item_cd}): {e}")
        return jsonify({"error": "규칙 추적에 실패했습니다"}), 500
