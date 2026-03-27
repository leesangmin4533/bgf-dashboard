"""재고 수명(TTL) 대시보드 API.

GET /api/inventory/ttl-summary   -- 재고 신선도 + 스테일 현황
GET /api/inventory/batch-expiry  -- 배치 만료 타임라인 (3일)
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

from flask import Blueprint, jsonify, request

from src.settings.constants import DEFAULT_STORE_ID
from src.utils.logger import get_logger

logger = get_logger(__name__)

inventory_bp = Blueprint("inventory", __name__)

# 프로젝트 루트
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def _get_store_db_path(store_id: str) -> Path:
    """매장 DB 경로."""
    return PROJECT_ROOT / "data" / "stores" / f"{store_id}.db"


def _get_common_db_path() -> Path:
    """공통 DB 경로."""
    return PROJECT_ROOT / "data" / "common.db"


def _get_stale_hours(expiry_days: Optional[int], mid_cd: Optional[str] = None) -> int:
    """유통기한 → stale_hours 변환 (inventory_repo 로직 재사용).

    1일→18h, 2일→36h, 3일→54h, 4일+→36h
    """
    from src.prediction.categories.food import FOOD_EXPIRY_FALLBACK

    eff_days = None

    if expiry_days is not None and expiry_days > 0:
        eff_days = expiry_days

    if eff_days is None and mid_cd:
        fallback = FOOD_EXPIRY_FALLBACK.get(mid_cd)
        if fallback is not None:
            eff_days = fallback

    if eff_days is not None and eff_days <= 3:
        return eff_days * 18

    return 36


def _ttl_label(hours: int) -> str:
    """TTL 시간 → 라벨."""
    if hours == 18:
        return "18h"
    elif hours == 36:
        return "36h"
    elif hours == 54:
        return "54h"
    return "default"


# ========================================
# 엔드포인트
# ========================================

@inventory_bp.route("/ttl-summary", methods=["GET"])
def ttl_summary():
    """재고 TTL 현황 요약.

    쿼리 파라미터:
        store_id: 매장 코드 (기본: DEFAULT_STORE_ID)
    """
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)
    store_db = _get_store_db_path(store_id)
    common_db = _get_common_db_path()

    if not store_db.exists():
        return jsonify({
            "store_id": store_id,
            "total_items": 0,
            "stale_items": 0,
            "stale_stock_qty": 0,
            "freshness_distribution": {"fresh": 0, "warning": 0, "stale": 0},
            "ttl_distribution": {},
            "category_breakdown": [],
            "stale_items_list": [],
        })

    try:
        conn = sqlite3.connect(str(store_db))
        conn.row_factory = sqlite3.Row

        # common.db ATTACH
        if common_db.exists():
            conn.execute(f"ATTACH DATABASE '{common_db}' AS common")

        prefix = "common." if common_db.exists() else ""

        # 재고 + 유통기한 + 카테고리 조회
        cursor = conn.execute(f"""
            SELECT ri.item_cd, ri.item_nm, ri.stock_qty, ri.pending_qty,
                   ri.queried_at, ri.is_available,
                   pd.expiration_days, p.mid_cd, mc.mid_nm
            FROM realtime_inventory ri
            LEFT JOIN {prefix}product_details pd ON ri.item_cd = pd.item_cd
            LEFT JOIN {prefix}products p ON ri.item_cd = p.item_cd
            LEFT JOIN {prefix}mid_categories mc ON p.mid_cd = mc.mid_cd
            WHERE ri.store_id = ? AND ri.is_available = 1
        """, (store_id,))

        rows = cursor.fetchall()
        now = datetime.now()

        total_items = 0
        stale_count = 0
        stale_stock = 0
        freshness = {"fresh": 0, "warning": 0, "stale": 0}
        ttl_dist: Dict[str, int] = {}
        category_data: Dict[str, Dict] = {}
        stale_list: List[Dict] = []

        for row in rows:
            item_cd = row["item_cd"]
            stock_qty = row["stock_qty"] or 0
            queried_at = row["queried_at"]
            expiry_days = row["expiration_days"]
            mid_cd = row["mid_cd"]
            mid_nm = row["mid_nm"] or mid_cd or "기타"

            if stock_qty <= 0:
                continue

            total_items += 1
            ttl_hours = _get_stale_hours(expiry_days, mid_cd)
            ttl_key = _ttl_label(ttl_hours)
            ttl_dist[ttl_key] = ttl_dist.get(ttl_key, 0) + 1

            # 경과시간 계산
            hours_since = 0.0
            is_stale = False
            is_warning = False
            try:
                queried_dt = datetime.fromisoformat(queried_at)
                hours_since = (now - queried_dt).total_seconds() / 3600
                if hours_since >= ttl_hours:
                    is_stale = True
                elif hours_since >= ttl_hours * 0.5:
                    is_warning = True
            except (ValueError, TypeError):
                is_stale = True

            # 분류
            if is_stale:
                freshness["stale"] += 1
                stale_count += 1
                stale_stock += stock_qty
                stale_list.append({
                    "item_cd": item_cd,
                    "item_nm": row["item_nm"] or item_cd,
                    "stock_qty": stock_qty,
                    "queried_at": queried_at,
                    "hours_since_query": round(hours_since, 1),
                    "ttl_hours": ttl_hours,
                    "mid_cd": mid_cd or "",
                })
            elif is_warning:
                freshness["warning"] += 1
            else:
                freshness["fresh"] += 1

            # 카테고리 집계
            if mid_cd:
                if mid_cd not in category_data:
                    category_data[mid_cd] = {
                        "mid_cd": mid_cd,
                        "mid_nm": mid_nm,
                        "total": 0,
                        "stale": 0,
                        "ttl_hours": ttl_hours,
                        "expiry_days": expiry_days or 0,
                    }
                category_data[mid_cd]["total"] += 1
                if is_stale:
                    category_data[mid_cd]["stale"] += 1

        conn.close()

        # 스테일 목록: 경과시간 내림차순 정렬, 상위 20개
        stale_list.sort(key=lambda x: x["hours_since_query"], reverse=True)
        stale_list = stale_list[:20]

        # 카테고리: 스테일 수 내림차순
        cat_list = sorted(
            category_data.values(),
            key=lambda c: c["stale"],
            reverse=True
        )

        return jsonify({
            "store_id": store_id,
            "total_items": total_items,
            "stale_items": stale_count,
            "stale_stock_qty": stale_stock,
            "freshness_distribution": freshness,
            "ttl_distribution": ttl_dist,
            "category_breakdown": cat_list,
            "stale_items_list": stale_list,
        })

    except Exception as e:
        logger.error(f"[Inventory TTL] ttl-summary 오류: {e}")
        return jsonify({"error": str(e)[:200]}), 500


@inventory_bp.route("/batch-expiry", methods=["GET"])
def batch_expiry():
    """배치 만료 타임라인 (향후 3일).

    쿼리 파라미터:
        store_id: 매장 코드 (기본: DEFAULT_STORE_ID)
        days: 조회 일수 (기본: 3)
    """
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)
    days = int(request.args.get("days", "3"))
    store_db = _get_store_db_path(store_id)
    common_db = _get_common_db_path()

    empty_result = {
        "store_id": store_id,
        "days_ahead": days,
        "batches": [],
        "summary": {"total_expiring_qty": 0, "total_expiring_items": 0},
    }

    if not store_db.exists():
        return jsonify(empty_result)

    try:
        conn = sqlite3.connect(str(store_db))
        conn.row_factory = sqlite3.Row

        # inventory_batches 테이블 존재 확인
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='inventory_batches'"
        )
        if not cursor.fetchone():
            conn.close()
            return jsonify(empty_result)

        # common.db ATTACH
        if common_db.exists():
            conn.execute(f"ATTACH DATABASE '{common_db}' AS common")

        prefix = "common." if common_db.exists() else ""

        today = datetime.now().strftime("%Y-%m-%d")
        end_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

        cursor = conn.execute(f"""
            SELECT ib.expiry_date, ib.item_cd, ib.item_nm, ib.remaining_qty,
                   ib.mid_cd, ib.receiving_date,
                   p.item_nm AS p_item_nm
            FROM inventory_batches ib
            LEFT JOIN {prefix}products p ON ib.item_cd = p.item_cd
            WHERE ib.status = 'active'
              AND ib.remaining_qty > 0
              AND ib.expiry_date >= ?
              AND ib.expiry_date <= ?
              AND ib.store_id = ?
            ORDER BY ib.expiry_date, ib.remaining_qty DESC
        """, (today, end_date, store_id))

        rows = cursor.fetchall()
        conn.close()

        # 날짜별 그룹핑
        day_labels = ["오늘", "내일", "모레"]
        today_dt = datetime.now().date()
        batches_by_date: Dict[str, Dict] = {}

        for row in rows:
            expiry_date = row["expiry_date"]
            if expiry_date not in batches_by_date:
                try:
                    exp_dt = datetime.strptime(expiry_date, "%Y-%m-%d").date()
                    delta = (exp_dt - today_dt).days
                    label = day_labels[delta] if 0 <= delta < len(day_labels) else f"+{delta}일"
                except (ValueError, IndexError):
                    label = expiry_date

                batches_by_date[expiry_date] = {
                    "expiry_date": expiry_date,
                    "label": label,
                    "items": [],
                    "total_qty": 0,
                    "item_count": 0,
                }

            item_nm = row["p_item_nm"] or row["item_nm"] or row["item_cd"]
            remaining = row["remaining_qty"] or 0

            batches_by_date[expiry_date]["items"].append({
                "item_cd": row["item_cd"],
                "item_nm": item_nm,
                "remaining_qty": remaining,
                "mid_cd": row["mid_cd"] or "",
                "receiving_date": row["receiving_date"] or "",
            })
            batches_by_date[expiry_date]["total_qty"] += remaining
            batches_by_date[expiry_date]["item_count"] += 1

        batches_list = sorted(batches_by_date.values(), key=lambda b: b["expiry_date"])

        total_qty = sum(b["total_qty"] for b in batches_list)
        total_items = sum(b["item_count"] for b in batches_list)

        return jsonify({
            "store_id": store_id,
            "days_ahead": days,
            "batches": batches_list,
            "summary": {
                "total_expiring_qty": total_qty,
                "total_expiring_items": total_items,
            },
        })

    except Exception as e:
        logger.error(f"[Inventory TTL] batch-expiry 오류: {e}")
        return jsonify({"error": str(e)[:200]}), 500
