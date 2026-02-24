"""신상품 도입 현황 REST API

/api/new-product/ 하위 엔드포인트:
  - GET /status: 현재 월 도입 현황
  - GET /missing-items: 미도입/미달성 상품 목록
  - GET /simulation: 추가 발주 시 예상 점수
"""

from datetime import datetime

from flask import Blueprint, jsonify, request

from src.infrastructure.database.repos import NewProductStatusRepository
from src.domain.new_product.score_calculator import (
    estimate_score_after_orders,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

new_product_bp = Blueprint("new_product", __name__)


@new_product_bp.route("/status", methods=["GET"])
def status():
    """현재 월 도입 현황"""
    store_id = request.args.get("store_id")
    month_ym = request.args.get("month_ym", datetime.now().strftime("%Y%m"))

    repo = NewProductStatusRepository(store_id=store_id)

    weekly = repo.get_weekly_status(store_id, month_ym)
    monthly = repo.get_monthly_summary(store_id, month_ym)

    return jsonify({
        "month_ym": month_ym,
        "weekly": weekly,
        "monthly": monthly,
    })


@new_product_bp.route("/missing-items", methods=["GET"])
def missing_items():
    """미도입/미달성 상품 목록"""
    store_id = request.args.get("store_id")
    month_ym = request.args.get("month_ym", datetime.now().strftime("%Y%m"))
    week_no = request.args.get("week_no", type=int)
    item_type = request.args.get("type", "midoip")
    orderable_only = request.args.get("orderable_only", "false").lower() == "true"

    repo = NewProductStatusRepository(store_id=store_id)
    items = repo.get_missing_items(
        store_id, month_ym,
        week_no=week_no,
        item_type=item_type,
        orderable_only=orderable_only,
    )

    return jsonify({
        "month_ym": month_ym,
        "week_no": week_no,
        "item_type": item_type,
        "count": len(items),
        "items": items,
    })


@new_product_bp.route("/simulation", methods=["GET"])
def simulation():
    """추가 발주 시 예상 점수 시뮬레이션"""
    store_id = request.args.get("store_id")
    month_ym = request.args.get("month_ym", datetime.now().strftime("%Y%m"))
    add_doip = request.args.get("add_doip", 0, type=int)
    add_ds = request.args.get("add_ds", 0, type=int)

    repo = NewProductStatusRepository(store_id=store_id)
    monthly = repo.get_monthly_summary(store_id, month_ym)

    if not monthly:
        return jsonify({"error": "해당 월 데이터 없음"}), 404

    result = estimate_score_after_orders(
        current_doip_cnt=monthly.get("doip_cnt", 0),
        current_ds_cnt=monthly.get("ds_cnt", 0),
        total_doip_items=monthly.get("doip_item_cnt", 1),
        total_ds_items=monthly.get("ds_item_cnt", 1),
        new_doip_orders=add_doip,
        new_ds_orders=add_ds,
    )

    return jsonify({
        "month_ym": month_ym,
        "current": {
            "doip_rate": monthly.get("doip_rate"),
            "ds_rate": monthly.get("ds_rate"),
            "tot_score": monthly.get("tot_score"),
            "supp_pay_amt": monthly.get("supp_pay_amt"),
        },
        "estimated": result,
    })
