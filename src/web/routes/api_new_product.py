"""신상품 도입 현황 REST API

/api/new-product/ 하위 엔드포인트:
  - GET /status: 현재 월 도입 현황
  - GET /missing-items: 미도입/미달성 상품 목록
  - GET /simulation: 추가 발주 시 예상 점수
  - GET /settlement: 안착 판정 목록 (모니터링 중 + KPI)
  - GET /settlement/<item_cd>: 단일 제품 안착 상세
  - POST /settlement/run: 수동 안착 판정 실행
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


# ── 안착 판정 API (v56) ──────────────────────────────────


@new_product_bp.route("/settlement", methods=["GET"])
def settlement_list():
    """모니터링 중인 신제품 목록 + KPI 요약 + 판정 상태"""
    store_id = request.args.get("store_id")
    if not store_id:
        return jsonify({"error": "store_id 필수"}), 400

    try:
        from src.application.services.new_product_settlement_service import (
            NewProductSettlementService,
        )
        service = NewProductSettlementService(store_id=store_id)
        summaries = service.get_settlement_summary()
        return jsonify({
            "store_id": store_id,
            "count": len(summaries),
            "items": summaries,
        })
    except Exception as e:
        logger.error(f"[API] settlement list 실패: {e}")
        return jsonify({"error": str(e)}), 500


@new_product_bp.route("/settlement/<item_cd>", methods=["GET"])
def settlement_detail(item_cd):
    """단일 제품 안착 상세 분석 (일별 트렌드 + KPI 5개)"""
    store_id = request.args.get("store_id")
    if not store_id:
        return jsonify({"error": "store_id 필수"}), 400

    try:
        from src.application.services.new_product_settlement_service import (
            NewProductSettlementService,
        )
        from src.infrastructure.database.repos import NewProductDailyTrackingRepository

        service = NewProductSettlementService(store_id=store_id)
        result = service.analyze(item_cd)
        if not result:
            return jsonify({"error": "제품 정보 없음"}), 404

        tracking_repo = NewProductDailyTrackingRepository(store_id=store_id)
        tracking = tracking_repo.get_tracking_history(item_cd, store_id=store_id)

        return jsonify({
            "item_cd": result.item_cd,
            "item_nm": result.item_nm,
            "verdict": result.verdict,
            "score": result.score,
            "kpis": {
                "velocity": round(result.velocity, 2),
                "velocity_ratio": round(result.velocity_ratio, 2),
                "cv": round(result.cv, 1) if result.cv is not None else None,
                "sellthrough_rate": round(result.sellthrough_rate, 1),
                "expiry_risk": result.expiry_risk,
                "rank": round(result.rank, 3),
            },
            "extension_count": result.extension_count,
            "analysis_window_days": result.analysis_window_days,
            "daily_tracking": tracking,
        })
    except Exception as e:
        logger.error(f"[API] settlement detail 실패: {e}")
        return jsonify({"error": str(e)}), 500


@new_product_bp.route("/settlement/run", methods=["POST"])
def settlement_run():
    """수동 안착 판정 실행 (대시보드 '지금 분석' 버튼용)"""
    store_id = request.args.get("store_id") or request.json.get("store_id") if request.json else None
    if not store_id:
        return jsonify({"error": "store_id 필수"}), 400

    try:
        from src.application.services.new_product_settlement_service import (
            NewProductSettlementService,
        )
        service = NewProductSettlementService(store_id=store_id)
        results = service.run_due_settlements()

        return jsonify({
            "store_id": store_id,
            "processed": len(results),
            "results": [
                {
                    "item_cd": r.item_cd,
                    "item_nm": r.item_nm,
                    "verdict": r.verdict,
                    "score": round(r.score, 1),
                    "velocity_ratio": round(r.velocity_ratio, 2),
                }
                for r in results
            ],
        })
    except Exception as e:
        logger.error(f"[API] settlement run 실패: {e}")
        return jsonify({"error": str(e)}), 500
