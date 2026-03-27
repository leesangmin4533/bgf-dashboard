"""입고 분석 API.

GET /api/receiving/summary          -- 입고 요약 (리드타임, pending_age 분포)
GET /api/receiving/trend            -- 일별 리드타임 추이
GET /api/receiving/slow-items       -- 지연 상위 상품 목록
GET /api/receiving/new-products     -- 신제품 감지 이력 (최근 N일)
GET /api/receiving/new-products/unregistered  -- 등록 미완료 신제품
GET /api/receiving/new-products/monitoring    -- 모니터링 중인 신제품 현황
GET /api/receiving/new-products/<item_cd>/tracking  -- 신제품 일별 추적 데이터
"""

import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

from flask import Blueprint, jsonify, request

from src.settings.constants import DEFAULT_STORE_ID
from src.utils.logger import get_logger

logger = get_logger(__name__)

receiving_bp = Blueprint("receiving", __name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def _get_store_db_path(store_id: str) -> Path:
    return PROJECT_ROOT / "data" / "stores" / f"{store_id}.db"


def _get_common_db_path() -> Path:
    return PROJECT_ROOT / "data" / "common.db"


def _get_store_conn(store_id: str) -> Optional[sqlite3.Connection]:
    """매장 DB + common ATTACH 연결."""
    db_path = _get_store_db_path(store_id)
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    common_path = _get_common_db_path()
    if common_path.exists():
        conn.execute(f"ATTACH DATABASE '{common_path}' AS common")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row[0] > 0


@receiving_bp.route("/summary")
def receiving_summary():
    """입고 요약 통계."""
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)
    conn = _get_store_conn(store_id)
    if not conn:
        return jsonify({"error": "store not found"}), 404

    try:
        result = {
            "avg_lead_time": 0.0,
            "max_lead_time": 0.0,
            "short_delivery_rate": 0.0,
            "total_items_tracked": 0,
            "pending_items_count": 0,
            "pending_age_distribution": {"0-1": 0, "2-3": 0, "4-7": 0, "8+": 0},
        }

        # 리드타임 통계 (receiving_history)
        if _table_exists(conn, "receiving_history"):
            row = conn.execute("""
                SELECT
                    AVG(julianday(receiving_date) - julianday(order_date)) as avg_lt,
                    MAX(julianday(receiving_date) - julianday(order_date)) as max_lt,
                    SUM(CASE WHEN receiving_qty < order_qty AND order_qty > 0 THEN 1 ELSE 0 END) as short_cnt,
                    COUNT(*) as total_cnt
                FROM receiving_history
                WHERE receiving_date >= date('now', '-30 days')
            """).fetchone()

            if row and row["total_cnt"] and row["total_cnt"] > 0:
                result["avg_lead_time"] = round(float(row["avg_lt"] or 0), 1)
                result["max_lead_time"] = round(float(row["max_lt"] or 0), 1)
                result["short_delivery_rate"] = round(
                    float(row["short_cnt"] or 0) / row["total_cnt"], 3
                )
                result["total_items_tracked"] = row["total_cnt"]

        # pending_age 분포 (order_tracking)
        if _table_exists(conn, "order_tracking"):
            today = datetime.now().strftime("%Y-%m-%d")
            rows = conn.execute("""
                SELECT
                    item_cd,
                    MIN(order_date) as oldest_order
                FROM order_tracking
                WHERE status IN ('ordered', 'arrived')
                    AND remaining_qty > 0
                GROUP BY item_cd
            """).fetchall()

            dist = {"0-1": 0, "2-3": 0, "4-7": 0, "8+": 0}
            for r in rows:
                if r["oldest_order"]:
                    try:
                        delta = (datetime.strptime(today, "%Y-%m-%d")
                                 - datetime.strptime(r["oldest_order"], "%Y-%m-%d")).days
                        delta = max(0, delta)
                        if delta <= 1:
                            dist["0-1"] += 1
                        elif delta <= 3:
                            dist["2-3"] += 1
                        elif delta <= 7:
                            dist["4-7"] += 1
                        else:
                            dist["8+"] += 1
                    except (ValueError, TypeError):
                        pass

            result["pending_items_count"] = len(rows)
            result["pending_age_distribution"] = dist

        return jsonify(result)
    except Exception as e:
        logger.error(f"[입고분석] summary 오류: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@receiving_bp.route("/trend")
def receiving_trend():
    """일별 리드타임 추이."""
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)
    days = int(request.args.get("days", 30))
    conn = _get_store_conn(store_id)
    if not conn:
        return jsonify({"error": "store not found"}), 404

    try:
        dates = []
        avg_lead_times = []
        delivery_counts = []

        if _table_exists(conn, "receiving_history"):
            rows = conn.execute("""
                SELECT
                    receiving_date,
                    AVG(julianday(receiving_date) - julianday(order_date)) as avg_lt,
                    COUNT(*) as cnt
                FROM receiving_history
                WHERE receiving_date >= date('now', '-' || ? || ' days')
                GROUP BY receiving_date
                ORDER BY receiving_date
            """, (days,)).fetchall()

            for r in rows:
                dates.append(r["receiving_date"])
                avg_lead_times.append(round(float(r["avg_lt"] or 0), 2))
                delivery_counts.append(r["cnt"])

        return jsonify({
            "dates": dates,
            "avg_lead_times": avg_lead_times,
            "delivery_counts": delivery_counts,
        })
    except Exception as e:
        logger.error(f"[입고분석] trend 오류: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@receiving_bp.route("/slow-items")
def receiving_slow_items():
    """지연 상위 상품 목록."""
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)
    limit = int(request.args.get("limit", 20))
    conn = _get_store_conn(store_id)
    if not conn:
        return jsonify({"error": "store not found"}), 404

    try:
        items = []

        if not _table_exists(conn, "order_tracking"):
            return jsonify({"items": []})

        today = datetime.now().strftime("%Y-%m-%d")

        # pending 상품 (미입고)
        pending_rows = conn.execute("""
            SELECT
                item_cd,
                MIN(order_date) as oldest_order
            FROM order_tracking
            WHERE status IN ('ordered', 'arrived')
                AND remaining_qty > 0
            GROUP BY item_cd
        """).fetchall()

        pending_map = {}
        for r in pending_rows:
            if r["oldest_order"]:
                try:
                    delta = (datetime.strptime(today, "%Y-%m-%d")
                             - datetime.strptime(r["oldest_order"], "%Y-%m-%d")).days
                    pending_map[r["item_cd"]] = max(0, delta)
                except (ValueError, TypeError):
                    pass

        if not pending_map:
            return jsonify({"items": []})

        # 리드타임 통계 (receiving_history)
        lt_map: Dict[str, Dict] = {}
        if _table_exists(conn, "receiving_history"):
            item_cds = list(pending_map.keys())
            placeholders = ",".join("?" * len(item_cds))
            lt_rows = conn.execute(f"""
                SELECT
                    item_cd,
                    AVG(julianday(receiving_date) - julianday(order_date)) as avg_lt,
                    COUNT(*) as cnt,
                    SUM(CASE WHEN receiving_qty < order_qty AND order_qty > 0 THEN 1 ELSE 0 END) as short_cnt
                FROM receiving_history
                WHERE item_cd IN ({placeholders})
                    AND receiving_date >= date('now', '-30 days')
                GROUP BY item_cd
            """, item_cds).fetchall()

            for r in lt_rows:
                cnt = r["cnt"] or 1
                lt_map[r["item_cd"]] = {
                    "lead_time_avg": round(float(r["avg_lt"] or 0), 1),
                    "short_delivery_rate": round(float(r["short_cnt"] or 0) / cnt, 2),
                }

        # 상품명 조회 (common.products)
        nm_map = {}
        try:
            all_cds = list(pending_map.keys())
            placeholders = ",".join("?" * len(all_cds))
            nm_rows = conn.execute(
                f"SELECT item_cd, item_nm, mid_cd FROM common.products WHERE item_cd IN ({placeholders})",
                all_cds,
            ).fetchall()
            for r in nm_rows:
                nm_map[r["item_cd"]] = {"item_nm": r["item_nm"], "mid_cd": r["mid_cd"]}
        except Exception:
            pass

        # 결합 + 정렬
        for item_cd, age in pending_map.items():
            info = nm_map.get(item_cd, {})
            lt_info = lt_map.get(item_cd, {})
            items.append({
                "item_cd": item_cd,
                "item_nm": info.get("item_nm", item_cd),
                "mid_cd": info.get("mid_cd", ""),
                "pending_age": age,
                "lead_time_avg": lt_info.get("lead_time_avg", 0.0),
                "short_delivery_rate": lt_info.get("short_delivery_rate", 0.0),
            })

        # pending_age 내림차순 정렬
        items.sort(key=lambda x: x["pending_age"], reverse=True)
        items = items[:limit]

        return jsonify({"items": items})
    except Exception as e:
        logger.error(f"[입고분석] slow-items 오류: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@receiving_bp.route("/new-products")
def detected_new_products():
    """신제품 감지 이력 조회 (최근 N일)

    Query params:
        days: 조회 기간 (기본 30)
        store_id: 매장 코드 (기본 DEFAULT_STORE_ID)
    """
    days = request.args.get("days", 30, type=int)
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)

    try:
        from src.infrastructure.database.repos import DetectedNewProductRepository
        repo = DetectedNewProductRepository(store_id=store_id)
        items = repo.get_recent(days=days, store_id=store_id)
        return jsonify({"items": items, "count": len(items), "days": days})
    except Exception as e:
        logger.error(f"[신제품] 감지 이력 조회 오류: {e}")
        return jsonify({"error": str(e)}), 500


@receiving_bp.route("/new-products/unregistered")
def unregistered_new_products():
    """등록 미완료 신제품 조회

    Query params:
        store_id: 매장 코드 (기본 DEFAULT_STORE_ID)
    """
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)

    try:
        from src.infrastructure.database.repos import DetectedNewProductRepository
        repo = DetectedNewProductRepository(store_id=store_id)
        items = repo.get_unregistered(store_id=store_id)
        return jsonify({"items": items, "count": len(items)})
    except Exception as e:
        logger.error(f"[신제품] 미등록 목록 조회 오류: {e}")
        return jsonify({"error": str(e)}), 500


@receiving_bp.route("/new-products/monitoring")
def monitoring_new_products():
    """모니터링 중인 신제품 현황

    Query params:
        store_id: 매장 코드 (기본 DEFAULT_STORE_ID)
    """
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)

    try:
        from src.infrastructure.database.repos import DetectedNewProductRepository
        repo = DetectedNewProductRepository(store_id=store_id)
        summary = repo.get_monitoring_summary(store_id=store_id)
        items = repo.get_by_lifecycle_status(
            ["detected", "monitoring", "stable", "slow_start", "no_demand"],
            store_id=store_id,
        )
        return jsonify({
            "summary": summary,
            "items": items,
            "count": len(items),
        })
    except Exception as e:
        logger.error(f"[신제품] 모니터링 현황 조회 오류: {e}")
        return jsonify({"error": str(e)}), 500


@receiving_bp.route("/new-products/<item_cd>/tracking")
def new_product_tracking(item_cd):
    """신제품 일별 판매/재고/발주 추이

    Query params:
        store_id: 매장 코드 (기본 DEFAULT_STORE_ID)
    """
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)

    try:
        from src.infrastructure.database.repos import NewProductDailyTrackingRepository
        repo = NewProductDailyTrackingRepository(store_id=store_id)
        tracking = repo.get_tracking_history(item_cd, store_id=store_id)
        return jsonify({
            "item_cd": item_cd,
            "tracking": tracking,
        })
    except Exception as e:
        logger.error(f"[신제품] 추적 데이터 조회 오류 ({item_cd}): {e}")
        return jsonify({"error": str(e)}), 500
