"""푸드 발주 최적화 모니터링 대시보드 API"""

import sqlite3
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from src.settings.constants import DEFAULT_STORE_ID
from src.infrastructure.database.connection import DBRouter
from src.utils.logger import get_logger

logger = get_logger(__name__)

food_monitor_bp = Blueprint("food_monitor", __name__)

DEPLOY_DATE = "2026-03-03"

CATEGORY_NAMES = {
    "001": "도시락", "002": "삼각김밥", "003": "김밥",
    "004": "샌드위치", "005": "햄버거", "012": "빵",
}

CATEGORY_PRICES = {
    "001": {"sell": 5579, "cost": 4155},
    "002": {"sell": 1670, "cost": 1099},
    "003": {"sell": 3384, "cost": 2309},
    "004": {"sell": 3089, "cost": 2172},
    "005": {"sell": 3695, "cost": 2808},
    "012": {"sell": 2500, "cost": 1704},
}

BURDEN_RATE = 0.63

MID_CDS = "('001','002','003','004','005','012')"


def _get_store_conn(store_id):
    db_path = DBRouter.get_store_db_path(store_id)
    return sqlite3.connect(str(db_path))


def _query_stockout(conn, date_from):
    sql = f"""SELECT mid_cd,
      COUNT(*) as total,
      SUM(CASE WHEN stock_qty=0 AND sale_qty>0 THEN 1 ELSE 0 END) as stockout,
      ROUND(100.0*SUM(CASE WHEN stock_qty=0 AND sale_qty>0 THEN 1 ELSE 0 END)/COUNT(*),1) as stockout_pct
    FROM daily_sales
    WHERE mid_cd IN {MID_CDS} AND sales_date >= ?
    GROUP BY mid_cd ORDER BY mid_cd"""
    return conn.execute(sql, (date_from,)).fetchall()


def _query_waste(conn, date_from):
    sql = f"""SELECT mid_cd,
      SUM(sale_qty) as sold, SUM(disuse_qty) as waste,
      ROUND(100.0*SUM(disuse_qty)/(SUM(sale_qty)+SUM(disuse_qty)+0.001),1) as waste_pct
    FROM daily_sales
    WHERE mid_cd IN {MID_CDS} AND sales_date >= ?
    GROUP BY mid_cd ORDER BY mid_cd"""
    return conn.execute(sql, (date_from,)).fetchall()


def _query_bias(conn, date_from):
    sql = f"""SELECT pl.mid_cd,
      COUNT(*) as cnt,
      ROUND(AVG(pl.predicted_qty - ds.sale_qty),2) as avg_error,
      ROUND(AVG(CASE WHEN ds.sale_qty>0 THEN (pl.predicted_qty - ds.sale_qty)*1.0/ds.sale_qty END),2) as bias
    FROM prediction_logs pl
    JOIN daily_sales ds ON pl.item_cd=ds.item_cd AND pl.prediction_date=ds.sales_date
    WHERE pl.mid_cd IN {MID_CDS} AND pl.prediction_date >= ?
    GROUP BY pl.mid_cd ORDER BY pl.mid_cd"""
    return conn.execute(sql, (date_from,)).fetchall()


def _query_conflict(conn, date_from):
    sql = f"""WITH day_cat AS (
      SELECT sales_date, mid_cd,
        SUM(disuse_qty) as total_disuse,
        SUM(CASE WHEN stock_qty=0 AND sale_qty>0 THEN 1 ELSE 0 END) as stockout_items
      FROM daily_sales
      WHERE mid_cd IN {MID_CDS} AND sales_date >= ?
      GROUP BY sales_date, mid_cd
    )
    SELECT mid_cd,
      COUNT(*) as total_days,
      SUM(CASE WHEN total_disuse > 0 AND stockout_items > 0 THEN 1 ELSE 0 END) as conflict_days,
      ROUND(100.0*SUM(CASE WHEN total_disuse > 0 AND stockout_items > 0 THEN 1 ELSE 0 END)/COUNT(*),1) as conflict_pct
    FROM day_cat
    GROUP BY mid_cd ORDER BY mid_cd"""
    return conn.execute(sql, (date_from,)).fetchall()


def _simulate_optimal(daily_sales_list, avg_sell, avg_cost):
    margin = avg_sell - avg_cost
    n = len(daily_sales_list)
    if n == 0:
        return {"opt_qty": 0, "opt_profit": 0, "opt_waste": 0}
    best_qty, best_profit, best_waste = 0, -999999, 0
    for q in range(0, max(daily_sales_list) + 6):
        total_profit, total_sold, total_waste = 0, 0, 0
        for demand in daily_sales_list:
            sold = min(q, demand)
            waste = max(0, q - demand)
            total_profit += sold * margin - waste * avg_cost * BURDEN_RATE
            total_sold += sold
            total_waste += waste
        avg_daily_profit = total_profit / n
        waste_rate = total_waste / (total_sold + total_waste) * 100 if (total_sold + total_waste) > 0 else 0
        if avg_daily_profit > best_profit:
            best_profit = avg_daily_profit
            best_qty = q
            best_waste = waste_rate
    return {"opt_qty": best_qty, "opt_profit": round(best_profit), "opt_waste": round(best_waste, 1)}


def _get_simulation(conn, date_from, date_to=None):
    results = {}
    date_filter = "sales_date >= ?"
    params = [date_from]
    if date_to:
        date_filter += " AND sales_date < ?"
        params.append(date_to)

    for mid_cd, price in CATEGORY_PRICES.items():
        sql = f"""SELECT sales_date, SUM(sale_qty) as daily_total
                  FROM daily_sales
                  WHERE mid_cd = ? AND {date_filter}
                  GROUP BY sales_date ORDER BY sales_date"""
        rows = conn.execute(sql, (mid_cd, *params)).fetchall()
        if not rows:
            continue
        daily_sales = [r[1] for r in rows]
        margin = price["sell"] - price["cost"]

        # actual
        actual_sql = f"""SELECT SUM(sale_qty), SUM(disuse_qty)
                        FROM daily_sales
                        WHERE mid_cd = ? AND {date_filter}"""
        actual = conn.execute(actual_sql, (mid_cd, *params)).fetchone()
        actual_sold = actual[0] or 0
        actual_waste = actual[1] or 0
        n = len(daily_sales)
        actual_profit = round((actual_sold * margin - actual_waste * price["cost"] * BURDEN_RATE) / n) if n > 0 else 0

        # avg order
        order_sql = f"""SELECT AVG(total_order) FROM (
            SELECT sales_date, SUM(ord_qty) as total_order
            FROM daily_sales
            WHERE mid_cd = ? AND {date_filter}
            GROUP BY sales_date)"""
        avg_order = conn.execute(order_sql, (mid_cd, *params)).fetchone()[0] or 0

        opt = _simulate_optimal(daily_sales, price["sell"], price["cost"])

        results[mid_cd] = {
            "name": CATEGORY_NAMES[mid_cd],
            "days": n,
            "avg_demand": round(sum(daily_sales) / n, 1),
            "avg_order": round(avg_order, 1),
            "actual_profit": actual_profit,
            "profit_gap": opt["opt_profit"] - actual_profit,
            **opt,
        }
    return results


@food_monitor_bp.route("/overview", methods=["GET"])
def get_overview():
    """배포 전/후 종합 비교 데이터"""
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)

    before_baseline = {
        "46513": {
            "001": {"stockout": 61.2, "waste": 21.1, "bias": -0.13},
            "002": {"stockout": 58.3, "waste": 12.0, "bias": -0.08},
            "003": {"stockout": 49.7, "waste": 25.4, "bias": -0.14},
            "004": {"stockout": 55.4, "waste": 18.8, "bias": -0.18},
            "005": {"stockout": 52.6, "waste": 14.8, "bias": -0.17},
            "012": {"stockout": 38.5, "waste": 10.1, "bias": -0.21},
        },
        "46704": {
            "001": {"stockout": 72.8, "waste": 6.9, "bias": -0.42},
            "002": {"stockout": 72.1, "waste": 3.8, "bias": -0.35},
            "003": {"stockout": 77.1, "waste": 7.4, "bias": -0.33},
            "004": {"stockout": 85.9, "waste": 0.5, "bias": -0.54},
            "005": {"stockout": 76.6, "waste": 6.0, "bias": -0.56},
            "012": {"stockout": 45.1, "waste": 5.8, "bias": -0.47},
        },
    }

    try:
        conn = _get_store_conn(store_id)
        after_stockout = {r[0]: r[3] for r in _query_stockout(conn, DEPLOY_DATE)}
        after_waste = {r[0]: r[3] for r in _query_waste(conn, DEPLOY_DATE)}
        after_bias_rows = _query_bias(conn, DEPLOY_DATE)
        after_bias = {r[0]: r[3] for r in after_bias_rows}
        after_conflict = {r[0]: {"days": r[1], "conflict": r[2], "pct": r[3]} for r in _query_conflict(conn, DEPLOY_DATE)}

        # days since deploy
        days_since = (datetime.now() - datetime.strptime(DEPLOY_DATE, "%Y-%m-%d")).days

        categories = []
        baseline = before_baseline.get(store_id, {})

        for mid_cd in ["001", "002", "003", "004", "005", "012"]:
            b = baseline.get(mid_cd, {})
            cat = {
                "mid_cd": mid_cd,
                "name": CATEGORY_NAMES[mid_cd],
                "before": {
                    "stockout": b.get("stockout"),
                    "waste": b.get("waste"),
                    "bias": b.get("bias"),
                },
                "after": {
                    "stockout": after_stockout.get(mid_cd),
                    "waste": after_waste.get(mid_cd),
                    "bias": after_bias.get(mid_cd),
                    "conflict_pct": after_conflict.get(mid_cd, {}).get("pct"),
                    "conflict_days": after_conflict.get(mid_cd, {}).get("conflict"),
                    "total_days": after_conflict.get(mid_cd, {}).get("days"),
                },
            }
            # compute changes
            for key in ["stockout", "waste"]:
                bv = cat["before"][key]
                av = cat["after"][key]
                if bv is not None and av is not None:
                    cat[f"{key}_change"] = round(av - bv, 1)
                else:
                    cat[f"{key}_change"] = None
            if cat["before"]["bias"] is not None and cat["after"]["bias"] is not None:
                cat["bias_change"] = round(cat["after"]["bias"] - cat["before"]["bias"], 2)
            else:
                cat["bias_change"] = None

            categories.append(cat)

        # simulation
        sim_before = _get_simulation(conn, "2026-02-01", DEPLOY_DATE)
        sim_after = _get_simulation(conn, DEPLOY_DATE)

        conn.close()

        return jsonify({
            "store_id": store_id,
            "deploy_date": DEPLOY_DATE,
            "days_since": days_since,
            "categories": categories,
            "simulation": {"before": sim_before, "after": sim_after},
        })
    except Exception as e:
        logger.error(f"푸드 모니터링 조회 실패: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@food_monitor_bp.route("/log-counts", methods=["GET"])
def get_log_counts():
    """부스트/면제 로그 발생 횟수"""
    import os
    log_path = os.path.join("data", "logs", "prediction.log")
    keywords = {
        "exempt": "폐기계수면제",
        "relax": "폐기계수완화",
        "floor": "최종하한",
        "boost": "품절부스트",
    }
    counts = {}
    try:
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            for key, kw in keywords.items():
                counts[key] = content.count(kw)
        else:
            counts = {k: 0 for k in keywords}
    except Exception:
        counts = {k: 0 for k in keywords}

    return jsonify(counts)
