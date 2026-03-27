"""카테고리 3단계 드릴다운 REST API.

대분류(18종) -> 중분류(72종) -> 소분류(198종) -> 개별상품 탐색.
각 레벨에서 매출/폐기/재고 요약 표시.

GET /api/categories/tree                      -- 카테고리 트리 구조
GET /api/categories/<level>/<code>/summary    -- 매출/폐기/재고 요약
GET /api/categories/<level>/<code>/products   -- 상품 목록 (페이지네이션)
"""

import re
import sqlite3
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

from src.settings.constants import DEFAULT_STORE_ID
from src.utils.logger import get_logger

logger = get_logger(__name__)

category_bp = Blueprint("category", __name__)

# 프로젝트 루트
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

# 유효 레벨 목록
VALID_LEVELS = ("large", "mid", "small")

# store_id 유효성 패턴 (숫자 5자리)
STORE_ID_PATTERN = re.compile(r"^\d{5}$")

# 정렬 허용 컬럼 (SQL injection 방지)
ALLOWED_SORT_COLUMNS = {
    "sale_qty": "sale_qty",
    "disuse_qty": "disuse_qty",
    "stock_qty": "stock_qty",
    "item_nm": "p.item_nm",
    "item_cd": "p.item_cd",
}


def _validate_store_id(store_id: str) -> bool:
    """store_id 유효성 검증 (SQL injection 방지)."""
    return bool(STORE_ID_PATTERN.match(store_id))


def _get_store_db_path(store_id: str) -> Path:
    """매장 DB 경로."""
    return PROJECT_ROOT / "data" / "stores" / f"{store_id}.db"


def _get_common_db_path() -> Path:
    """공통 DB 경로."""
    return PROJECT_ROOT / "data" / "common.db"


def _validate_level(level: str) -> Optional[str]:
    """level 유효성 검증. 유효하면 None, 무효하면 에러 메시지."""
    if level not in VALID_LEVELS:
        return f"유효하지 않은 level입니다. {'/'.join(VALID_LEVELS)} 중 선택하세요"
    return None


def _get_category_name(conn: sqlite3.Connection, level: str, code: str) -> str:
    """카테고리 이름 조회."""
    try:
        if level == "large":
            cursor = conn.execute(
                "SELECT large_nm FROM mid_categories WHERE large_cd = ? LIMIT 1",
                (code,)
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else f"대분류 {code}"

        elif level == "mid":
            cursor = conn.execute(
                "SELECT mid_nm FROM mid_categories WHERE mid_cd = ? LIMIT 1",
                (code,)
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else f"중분류 {code}"

        elif level == "small":
            cursor = conn.execute(
                "SELECT small_nm FROM product_details WHERE small_cd = ? LIMIT 1",
                (code,)
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else f"소분류 {code}"

    except Exception as e:
        logger.warning(f"카테고리 이름 조회 실패 ({level}/{code}): {e}")

    return code


def _get_item_cds_for_level(
    conn: sqlite3.Connection, level: str, code: str
) -> Tuple[str, List[str]]:
    """레벨과 코드에 해당하는 item_cd 목록을 위한 SQL 서브쿼리 조건 반환.

    Returns:
        (where_clause, params) - 상품 필터링을 위한 WHERE 절과 파라미터
    """
    if level == "large":
        return (
            """p.item_cd IN (
                SELECT p2.item_cd FROM products p2
                JOIN mid_categories mc2 ON p2.mid_cd = mc2.mid_cd
                WHERE mc2.large_cd = ?
            )""",
            [code],
        )
    elif level == "mid":
        return (
            "p.mid_cd = ?",
            [code],
        )
    elif level == "small":
        return (
            """p.item_cd IN (
                SELECT pd2.item_cd FROM product_details pd2
                WHERE pd2.small_cd = ?
            )""",
            [code],
        )
    else:
        return "1=0", []


# ========================================
# 엔드포인트
# ========================================


@category_bp.route("/tree", methods=["GET"])
def tree():
    """카테고리 트리 구조 반환.

    쿼리 파라미터:
        store_id: 매장 코드 (기본: DEFAULT_STORE_ID, 현재 미사용이나 향후 확장용)
    """
    common_db = _get_common_db_path()

    if not common_db.exists():
        return jsonify({
            "tree": [],
            "summary": {
                "large_count": 0,
                "mid_count": 0,
                "small_count": 0,
                "product_count": 0,
            },
        })

    conn = None
    try:
        conn = sqlite3.connect(str(common_db))
        conn.row_factory = sqlite3.Row

        # 1) 대분류 -> 중분류 매핑 조회
        mid_rows = conn.execute("""
            SELECT mid_cd, mid_nm,
                   COALESCE(large_cd, '00') AS large_cd,
                   COALESCE(large_nm, '미분류') AS large_nm
            FROM mid_categories
            ORDER BY large_cd, mid_cd
        """).fetchall()

        # 2) 중분류 -> 소분류 매핑 + 상품 수 조회
        small_rows = conn.execute("""
            SELECT p.mid_cd,
                   COALESCE(pd.small_cd, '기타') AS small_cd,
                   COALESCE(pd.small_nm, '미분류') AS small_nm,
                   COUNT(*) AS product_count
            FROM products p
            LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
            GROUP BY p.mid_cd, pd.small_cd
            ORDER BY p.mid_cd, pd.small_cd
        """).fetchall()

        # 3) 소분류 데이터를 mid_cd별 dict로 구성
        small_by_mid: Dict[str, List[Dict]] = {}
        for sr in small_rows:
            mid_cd = sr["mid_cd"]
            if mid_cd not in small_by_mid:
                small_by_mid[mid_cd] = []
            small_by_mid[mid_cd].append({
                "small_cd": sr["small_cd"],
                "small_nm": sr["small_nm"],
                "product_count": sr["product_count"],
            })

        # 4) 대분류 -> 중분류 -> 소분류 트리 구성
        large_dict: Dict[str, Dict[str, Any]] = OrderedDict()
        total_mid = 0
        total_small = 0
        total_products = 0

        for mr in mid_rows:
            lc = mr["large_cd"]
            if lc not in large_dict:
                large_dict[lc] = {
                    "large_cd": lc,
                    "large_nm": mr["large_nm"],
                    "mid_count": 0,
                    "children": [],
                }

            smalls = small_by_mid.get(mr["mid_cd"], [])
            small_count = len(smalls)
            prod_count = sum(s["product_count"] for s in smalls)

            large_dict[lc]["children"].append({
                "mid_cd": mr["mid_cd"],
                "mid_nm": mr["mid_nm"],
                "small_count": small_count,
                "children": smalls,
            })
            large_dict[lc]["mid_count"] += 1

            total_mid += 1
            total_small += small_count
            total_products += prod_count

        tree_list = list(large_dict.values())

        return jsonify({
            "tree": tree_list,
            "summary": {
                "large_count": len(tree_list),
                "mid_count": total_mid,
                "small_count": total_small,
                "product_count": total_products,
            },
        })

    except Exception as e:
        logger.error(f"[Category] tree 조회 오류: {e}")
        return jsonify({"error": "카테고리 트리 조회에 실패했습니다"}), 500
    finally:
        if conn:
            conn.close()


@category_bp.route("/<level>/<code>/summary", methods=["GET"])
def summary(level: str, code: str):
    """카테고리별 매출/폐기/재고 요약.

    경로 파라미터:
        level: large | mid | small
        code: 카테고리 코드

    쿼리 파라미터:
        store_id: 매장 코드 (기본: DEFAULT_STORE_ID)
        days: 조회 기간 (기본: 7)
    """
    # level 유효성 검증
    err = _validate_level(level)
    if err:
        return jsonify({"error": err}), 400

    store_id = request.args.get("store_id", DEFAULT_STORE_ID)
    if not _validate_store_id(store_id):
        return jsonify({"error": "유효하지 않은 store_id입니다"}), 400

    try:
        days = int(request.args.get("days", "7"))
    except ValueError:
        return jsonify({"error": "days는 정수여야 합니다"}), 400

    common_db = _get_common_db_path()
    store_db = _get_store_db_path(store_id)

    empty_result = {
        "level": level,
        "code": code,
        "name": code,
        "period_days": days,
        "sales": {
            "total_qty": 0,
            "daily_avg": 0.0,
            "item_count": 0,
        },
        "waste": {
            "total_qty": 0,
            "waste_rate": 0.0,
            "daily_avg": 0.0,
        },
        "inventory": {
            "total_stock": 0,
            "total_pending": 0,
            "item_count": 0,
        },
    }

    if not common_db.exists():
        return jsonify(empty_result)

    conn = None
    try:
        conn = sqlite3.connect(str(common_db))
        conn.row_factory = sqlite3.Row

        # 카테고리 이름 조회
        cat_name = _get_category_name(conn, level, code)
        empty_result["name"] = cat_name

        # 상품 필터 조건 생성
        where_clause, params = _get_item_cds_for_level(conn, level, code)

        # 해당 카테고리 상품 수 조회
        cursor = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM products p WHERE {where_clause}",
            params,
        )
        item_count = cursor.fetchone()["cnt"]

        if item_count == 0:
            return jsonify(empty_result)

        # store DB 매출/재고 조회 (ATTACH)
        has_store = store_db.exists()
        if has_store:
            conn.execute(
                f"ATTACH DATABASE '{store_db}' AS store"
            )

        # 매출 집계 (최근 N일)
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        if has_store:
            cursor = conn.execute(f"""
                SELECT COALESCE(SUM(ds.sale_qty), 0) AS total_qty,
                       COALESCE(SUM(ds.disuse_qty), 0) AS disuse_qty
                FROM store.daily_sales ds
                JOIN products p ON ds.item_cd = p.item_cd
                WHERE {where_clause}
                  AND ds.sales_date >= ?
            """, params + [start_date])
            sales_row = cursor.fetchone()

            total_sale_qty = sales_row["total_qty"]
            total_disuse_qty = sales_row["disuse_qty"]
        else:
            total_sale_qty = 0
            total_disuse_qty = 0

        # 재고 집계
        if has_store:
            cursor = conn.execute(f"""
                SELECT COALESCE(SUM(ri.stock_qty), 0) AS total_stock,
                       COALESCE(SUM(ri.pending_qty), 0) AS total_pending,
                       COUNT(*) AS inv_count
                FROM store.realtime_inventory ri
                JOIN products p ON ri.item_cd = p.item_cd
                WHERE {where_clause}
                  AND ri.is_available = 1
                  AND ri.stock_qty > 0
            """, params)
            inv_row = cursor.fetchone()

            total_stock = inv_row["total_stock"]
            total_pending = inv_row["total_pending"]
            inv_item_count = inv_row["inv_count"]
        else:
            total_stock = 0
            total_pending = 0
            inv_item_count = 0

        # 일평균, 폐기율 계산
        daily_avg_sale = round(total_sale_qty / days, 1) if days > 0 else 0.0
        daily_avg_waste = round(total_disuse_qty / days, 1) if days > 0 else 0.0
        waste_rate = round(
            (total_disuse_qty / (total_sale_qty + total_disuse_qty)) * 100, 1
        ) if (total_sale_qty + total_disuse_qty) > 0 else 0.0

        return jsonify({
            "level": level,
            "code": code,
            "name": cat_name,
            "period_days": days,
            "sales": {
                "total_qty": total_sale_qty,
                "daily_avg": daily_avg_sale,
                "item_count": item_count,
            },
            "waste": {
                "total_qty": total_disuse_qty,
                "waste_rate": waste_rate,
                "daily_avg": daily_avg_waste,
            },
            "inventory": {
                "total_stock": total_stock,
                "total_pending": total_pending,
                "item_count": inv_item_count,
            },
        })

    except Exception as e:
        logger.error(f"[Category] summary 조회 오류 ({level}/{code}): {e}")
        return jsonify({"error": "카테고리 요약 조회에 실패했습니다"}), 500
    finally:
        if conn:
            conn.close()


@category_bp.route("/<level>/<code>/products", methods=["GET"])
def products(level: str, code: str):
    """카테고리별 상품 목록 (페이지네이션).

    경로 파라미터:
        level: large | mid | small
        code: 카테고리 코드

    쿼리 파라미터:
        store_id: 매장 코드 (기본: DEFAULT_STORE_ID)
        days: 매출 집계 기간 (기본: 7)
        limit: 최대 항목 수 (기본: 100)
        offset: 시작 위치 (기본: 0)
        sort: 정렬 기준 (sale_qty | disuse_qty | stock_qty | item_nm, 기본: sale_qty)
        order: 정렬 방향 (desc | asc, 기본: desc)
    """
    # level 유효성 검증
    err = _validate_level(level)
    if err:
        return jsonify({"error": err}), 400

    store_id = request.args.get("store_id", DEFAULT_STORE_ID)
    if not _validate_store_id(store_id):
        return jsonify({"error": "유효하지 않은 store_id입니다"}), 400

    try:
        days = int(request.args.get("days", "7"))
        limit = min(int(request.args.get("limit", "100")), 500)
        offset = int(request.args.get("offset", "0"))
    except ValueError:
        return jsonify({"error": "days/limit/offset는 정수여야 합니다"}), 400

    sort_col = request.args.get("sort", "sale_qty")
    sort_order = request.args.get("order", "desc").upper()

    # 정렬 컬럼 검증
    if sort_col not in ALLOWED_SORT_COLUMNS:
        sort_col = "sale_qty"
    sql_sort = ALLOWED_SORT_COLUMNS[sort_col]

    if sort_order not in ("ASC", "DESC"):
        sort_order = "DESC"

    common_db = _get_common_db_path()
    store_db = _get_store_db_path(store_id)

    empty_result = {
        "level": level,
        "code": code,
        "name": code,
        "total_count": 0,
        "products": [],
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": 0,
            "has_more": False,
        },
    }

    if not common_db.exists():
        return jsonify(empty_result)

    conn = None
    try:
        conn = sqlite3.connect(str(common_db))
        conn.row_factory = sqlite3.Row

        # 카테고리 이름
        cat_name = _get_category_name(conn, level, code)

        # 상품 필터 조건
        where_clause, params = _get_item_cds_for_level(conn, level, code)

        # 총 건수
        cursor = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM products p WHERE {where_clause}",
            params,
        )
        total_count = cursor.fetchone()["cnt"]

        if total_count == 0:
            empty_result["name"] = cat_name
            return jsonify(empty_result)

        # store DB ATTACH
        has_store = store_db.exists()
        if has_store:
            conn.execute(
                f"ATTACH DATABASE '{store_db}' AS store"
            )

        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        if has_store:
            # 매출 + 재고 JOIN 상품 목록
            cursor = conn.execute(f"""
                SELECT p.item_cd, p.item_nm, p.mid_cd,
                       pd.small_cd, pd.small_nm,
                       COALESCE(s.sale_qty, 0) AS sale_qty,
                       COALESCE(s.disuse_qty, 0) AS disuse_qty,
                       COALESCE(ri.stock_qty, 0) AS stock_qty,
                       COALESCE(ri.pending_qty, 0) AS pending_qty
                FROM products p
                LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
                LEFT JOIN (
                    SELECT item_cd,
                           SUM(sale_qty) AS sale_qty,
                           SUM(disuse_qty) AS disuse_qty
                    FROM store.daily_sales
                    WHERE sales_date >= ?
                    GROUP BY item_cd
                ) s ON p.item_cd = s.item_cd
                LEFT JOIN (
                    SELECT item_cd, stock_qty, pending_qty
                    FROM store.realtime_inventory
                    WHERE is_available = 1
                ) ri ON p.item_cd = ri.item_cd
                WHERE {where_clause}
                ORDER BY {sql_sort} {sort_order}
                LIMIT ? OFFSET ?
            """, [start_date] + params + [limit, offset])
        else:
            # store DB 없으면 common 데이터만
            cursor = conn.execute(f"""
                SELECT p.item_cd, p.item_nm, p.mid_cd,
                       pd.small_cd, pd.small_nm,
                       0 AS sale_qty,
                       0 AS disuse_qty,
                       0 AS stock_qty,
                       0 AS pending_qty
                FROM products p
                LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
                WHERE {where_clause}
                ORDER BY {sql_sort} {sort_order}
                LIMIT ? OFFSET ?
            """, params + [limit, offset])

        rows = cursor.fetchall()

        product_list = []
        for row in rows:
            product_list.append({
                "item_cd": row["item_cd"],
                "item_nm": row["item_nm"] or row["item_cd"],
                "mid_cd": row["mid_cd"] or "",
                "small_cd": row["small_cd"] or "",
                "small_nm": row["small_nm"] or "",
                "sale_qty": row["sale_qty"],
                "disuse_qty": row["disuse_qty"],
                "stock_qty": row["stock_qty"],
                "pending_qty": row["pending_qty"],
            })

        has_more = (offset + limit) < total_count

        return jsonify({
            "level": level,
            "code": code,
            "name": cat_name,
            "total_count": total_count,
            "products": product_list,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total_count,
                "has_more": has_more,
            },
        })

    except Exception as e:
        logger.error(f"[Category] products 조회 오류 ({level}/{code}): {e}")
        return jsonify({"error": "카테고리 상품 목록 조회에 실패했습니다"}), 500
    finally:
        if conn:
            conn.close()
