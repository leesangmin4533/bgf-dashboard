"""
샌드위치(004), 햄버거(005), 디저트/빵(012) 카테고리 발주 과정 심층 분석
매장별 DB + 공통 DB 기반
"""
import sqlite3
import json
import io
import sys
import os
from collections import defaultdict
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE_IDS = ["46513", "46704"]
STORE_NAMES = {"46513": "CU 동양대점", "46704": "CU 호반점"}
TARGET_MID_CDS = ("004", "005", "012")
MID_CD_NAMES = {"004": "샌드위치", "005": "햄버거", "012": "빵/디저트"}
COMMON_DB = os.path.join(BASE_DIR, "data", "common.db")


def get_store_db(store_id):
    return os.path.join(BASE_DIR, "data", "stores", f"{store_id}.db")


def connect_store(store_id):
    """매장 DB에 연결하고 common.db를 ATTACH"""
    conn = sqlite3.connect(get_store_db(store_id))
    conn.row_factory = sqlite3.Row
    conn.execute(f"ATTACH DATABASE '{COMMON_DB}' AS common")
    return conn


def analysis_1_basic_status(conn, store_id):
    """1. 기본 현황 (최근 30일)"""
    results = {}
    for mid_cd in TARGET_MID_CDS:
        row = conn.execute("""
            SELECT
                COUNT(DISTINCT ds.item_cd) as active_items,
                COALESCE(SUM(ds.sale_qty), 0) as total_sales,
                COALESCE(SUM(ds.disuse_qty), 0) as total_disuse,
                COALESCE(SUM(ds.ord_qty), 0) as total_ord,
                COALESCE(SUM(ds.buy_qty), 0) as total_buy,
                COUNT(DISTINCT ds.sales_date) as active_days,
                ROUND(CAST(COALESCE(SUM(ds.sale_qty), 0) AS REAL) / MAX(COUNT(DISTINCT ds.sales_date), 1), 2) as daily_avg_sales,
                ROUND(CAST(COALESCE(SUM(ds.disuse_qty), 0) AS REAL) / MAX(COUNT(DISTINCT ds.sales_date), 1), 2) as daily_avg_disuse,
                ROUND(
                    CASE WHEN (COALESCE(SUM(ds.sale_qty),0) + COALESCE(SUM(ds.disuse_qty),0)) > 0
                    THEN CAST(COALESCE(SUM(ds.disuse_qty),0) AS REAL) / (COALESCE(SUM(ds.sale_qty),0) + COALESCE(SUM(ds.disuse_qty),0)) * 100
                    ELSE 0 END, 2
                ) as disuse_rate
            FROM daily_sales ds
            WHERE ds.mid_cd = ?
              AND ds.sales_date >= date((SELECT MAX(sales_date) FROM daily_sales), '-30 days')
        """, (mid_cd,)).fetchone()
        results[mid_cd] = {
            "category_name": MID_CD_NAMES[mid_cd],
            "active_items": row["active_items"],
            "total_sales": row["total_sales"],
            "total_disuse": row["total_disuse"],
            "disuse_rate_pct": row["disuse_rate"],
            "total_ord_qty": row["total_ord"],
            "total_buy_qty": row["total_buy"],
            "active_days": row["active_days"],
            "daily_avg_sales": row["daily_avg_sales"],
            "daily_avg_disuse": row["daily_avg_disuse"],
        }
    return results


def analysis_2_item_detail(conn, store_id):
    """2. 상품별 상세 (최근 30일)"""
    results = {}
    for mid_cd in TARGET_MID_CDS:
        # 상품별 집계
        rows = conn.execute("""
            SELECT
                ds.item_cd,
                COALESCE(p.item_nm, '(이름없음)') as item_nm,
                ROUND(CAST(SUM(ds.sale_qty) AS REAL) / MAX(COUNT(DISTINCT ds.sales_date), 1), 2) as daily_avg_sales,
                SUM(ds.disuse_qty) as total_disuse,
                ROUND(
                    CASE WHEN (SUM(ds.sale_qty) + SUM(ds.disuse_qty)) > 0
                    THEN CAST(SUM(ds.disuse_qty) AS REAL) / (SUM(ds.sale_qty) + SUM(ds.disuse_qty)) * 100
                    ELSE 0 END, 2
                ) as disuse_rate,
                SUM(CASE WHEN ds.sale_qty > 0 THEN 1 ELSE 0 END) as sale_days,
                SUM(CASE WHEN ds.sale_qty = 0 THEN 1 ELSE 0 END) as zero_sale_days,
                SUM(ds.sale_qty) as total_sales,
                SUM(ds.ord_qty) as total_ord
            FROM daily_sales ds
            LEFT JOIN common.products p ON ds.item_cd = p.item_cd
            WHERE ds.mid_cd = ?
              AND ds.sales_date >= date((SELECT MAX(sales_date) FROM daily_sales), '-30 days')
            GROUP BY ds.item_cd
            ORDER BY disuse_rate DESC
        """, (mid_cd,)).fetchall()

        all_items = []
        high_disuse_top10 = []
        over_order_items = []

        for r in rows:
            item = {
                "item_cd": r["item_cd"],
                "item_nm": r["item_nm"],
                "daily_avg_sales": r["daily_avg_sales"],
                "total_disuse": r["total_disuse"],
                "disuse_rate_pct": r["disuse_rate"],
                "sale_days": r["sale_days"],
                "zero_sale_days": r["zero_sale_days"],
                "total_sales": r["total_sales"],
                "total_ord": r["total_ord"],
            }
            all_items.append(item)

            total_volume = (r["total_sales"] or 0) + (r["total_disuse"] or 0)
            if total_volume >= 5 and r["disuse_rate"] > 0:
                high_disuse_top10.append(item)

            if r["disuse_rate"] > 20 and total_volume >= 5:
                over_order_items.append(item)

        results[mid_cd] = {
            "category_name": MID_CD_NAMES[mid_cd],
            "all_items_count": len(all_items),
            "all_items": all_items,
            "high_disuse_top10": high_disuse_top10[:10],
            "over_order_suspected": over_order_items,
            "over_order_count": len(over_order_items),
        }
    return results


def analysis_3_prediction_accuracy(conn, store_id):
    """3. 예측 vs 실제 정확도"""
    results = {}
    for mid_cd in TARGET_MID_CDS:
        rows = conn.execute("""
            SELECT
                pl.item_cd,
                pl.predicted_qty,
                ds.sale_qty,
                ABS(pl.predicted_qty - ds.sale_qty) as error,
                CASE WHEN pl.predicted_qty > ds.sale_qty THEN 1 ELSE 0 END as over_predict,
                CASE WHEN pl.predicted_qty < ds.sale_qty THEN 1 ELSE 0 END as under_predict,
                CASE WHEN pl.predicted_qty = ds.sale_qty THEN 1 ELSE 0 END as exact_match
            FROM prediction_logs pl
            JOIN daily_sales ds ON pl.item_cd = ds.item_cd AND pl.prediction_date = ds.sales_date
            WHERE ds.mid_cd = ?
              AND pl.prediction_date >= date((SELECT MAX(sales_date) FROM daily_sales), '-30 days')
        """, (mid_cd,)).fetchall()

        if not rows:
            results[mid_cd] = {
                "category_name": MID_CD_NAMES[mid_cd],
                "total_predictions": 0,
                "message": "No prediction data"
            }
            continue

        total = len(rows)
        total_error = sum(r["error"] for r in rows)
        over_count = sum(r["over_predict"] for r in rows)
        under_count = sum(r["under_predict"] for r in rows)
        exact_count = sum(r["exact_match"] for r in rows)

        results[mid_cd] = {
            "category_name": MID_CD_NAMES[mid_cd],
            "total_predictions": total,
            "avg_error": round(total_error / total, 2) if total > 0 else 0,
            "over_predict_count": over_count,
            "over_predict_pct": round(over_count / total * 100, 2) if total > 0 else 0,
            "under_predict_count": under_count,
            "under_predict_pct": round(under_count / total * 100, 2) if total > 0 else 0,
            "exact_match_count": exact_count,
            "exact_match_pct": round(exact_count / total * 100, 2) if total > 0 else 0,
        }
    return results


def analysis_4_eval_decisions(conn, store_id):
    """4. 평가 판정 분포"""
    results = {}
    for mid_cd in TARGET_MID_CDS:
        rows = conn.execute("""
            SELECT decision, COUNT(*) as cnt
            FROM eval_outcomes
            WHERE mid_cd = ?
              AND eval_date >= date((SELECT MAX(eval_date) FROM eval_outcomes), '-30 days')
            GROUP BY decision
            ORDER BY cnt DESC
        """, (mid_cd,)).fetchall()

        total = sum(r["cnt"] for r in rows)
        decision_dist = {}
        for r in rows:
            decision_dist[r["decision"]] = {
                "count": r["cnt"],
                "pct": round(r["cnt"] / total * 100, 2) if total > 0 else 0
            }

        results[mid_cd] = {
            "category_name": MID_CD_NAMES[mid_cd],
            "total_evaluations": total,
            "decisions": decision_dist,
        }
    return results


def analysis_5_order_vs_sales(conn, store_id):
    """5. 발주량 vs 판매량 비교"""
    results = {}
    for mid_cd in TARGET_MID_CDS:
        rows = conn.execute("""
            SELECT
                sales_date, item_cd, ord_qty, sale_qty, disuse_qty, stock_qty
            FROM daily_sales
            WHERE mid_cd = ?
              AND sales_date >= date((SELECT MAX(sales_date) FROM daily_sales), '-30 days')
              AND ord_qty > 0
            ORDER BY sales_date DESC
        """, (mid_cd,)).fetchall()

        total_orders = len(rows)
        if total_orders == 0:
            results[mid_cd] = {
                "category_name": MID_CD_NAMES[mid_cd],
                "total_order_records": 0,
                "message": "No order data"
            }
            continue

        total_ord = sum(r["ord_qty"] for r in rows)
        total_sale = sum(r["sale_qty"] for r in rows)
        zero_sale_count = sum(1 for r in rows if r["sale_qty"] == 0)
        over_order_count = sum(1 for r in rows if r["ord_qty"] > (r["sale_qty"] or 0) * 2 and r["sale_qty"] > 0)
        # ord_qty > 0이고 sale_qty == 0인 건도 과잉으로 카운트
        extreme_over = sum(1 for r in rows if r["sale_qty"] == 0)

        sale_to_ord_ratios = []
        for r in rows:
            if r["ord_qty"] > 0:
                ratio = round(r["sale_qty"] / r["ord_qty"], 2) if r["ord_qty"] > 0 else 0
                sale_to_ord_ratios.append(ratio)

        avg_ratio = round(sum(sale_to_ord_ratios) / len(sale_to_ord_ratios), 2) if sale_to_ord_ratios else 0

        # 과잉 발주 상세 (상위 10건)
        over_order_detail = []
        for r in sorted(rows, key=lambda x: (x["ord_qty"] - x["sale_qty"]), reverse=True)[:10]:
            over_order_detail.append({
                "date": r["sales_date"],
                "item_cd": r["item_cd"],
                "ord_qty": r["ord_qty"],
                "sale_qty": r["sale_qty"],
                "disuse_qty": r["disuse_qty"],
                "stock_qty": r["stock_qty"],
                "excess": r["ord_qty"] - r["sale_qty"],
            })

        results[mid_cd] = {
            "category_name": MID_CD_NAMES[mid_cd],
            "total_order_records": total_orders,
            "total_ord_qty": total_ord,
            "total_sale_qty_on_order_days": total_sale,
            "avg_sale_to_ord_ratio": avg_ratio,
            "zero_sale_after_order_count": zero_sale_count,
            "zero_sale_after_order_pct": round(zero_sale_count / total_orders * 100, 2),
            "over_order_2x_count": over_order_count,
            "over_order_2x_pct": round(over_order_count / total_orders * 100, 2) if total_orders > 0 else 0,
            "worst_excess_top10": over_order_detail,
        }
    return results


def analysis_6_weekday_pattern(conn, store_id):
    """6. 요일별 패턴"""
    weekday_names = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}
    results = {}
    for mid_cd in TARGET_MID_CDS:
        rows = conn.execute("""
            SELECT
                CAST(strftime('%w', sales_date) AS INTEGER) as dow,
                COUNT(DISTINCT sales_date) as day_count,
                COALESCE(SUM(sale_qty), 0) as total_sales,
                COALESCE(SUM(disuse_qty), 0) as total_disuse,
                COALESCE(SUM(ord_qty), 0) as total_ord
            FROM daily_sales
            WHERE mid_cd = ?
              AND sales_date >= date((SELECT MAX(sales_date) FROM daily_sales), '-30 days')
            GROUP BY dow
            ORDER BY dow
        """, (mid_cd,)).fetchall()

        weekday_data = {}
        for r in rows:
            # SQLite strftime('%w') returns 0=Sunday
            dow_py = (r["dow"] - 1) % 7  # Convert to 0=Monday
            day_name = weekday_names.get(dow_py, str(dow_py))
            day_count = r["day_count"] if r["day_count"] > 0 else 1
            total_vol = (r["total_sales"] + r["total_disuse"])
            disuse_rate = round(r["total_disuse"] / total_vol * 100, 2) if total_vol > 0 else 0
            weekday_data[day_name] = {
                "day_count": r["day_count"],
                "avg_sales": round(r["total_sales"] / day_count, 2),
                "avg_disuse": round(r["total_disuse"] / day_count, 2),
                "avg_ord": round(r["total_ord"] / day_count, 2),
                "disuse_rate_pct": disuse_rate,
            }

        results[mid_cd] = {
            "category_name": MID_CD_NAMES[mid_cd],
            "weekday_pattern": weekday_data,
        }
    return results


def analysis_7_inventory_turnover(conn, store_id):
    """7. 재고 회전 분석"""
    results = {}
    for mid_cd in TARGET_MID_CDS:
        rows = conn.execute("""
            SELECT
                ds.item_cd,
                COALESCE(p.item_nm, '(이름없음)') as item_nm,
                ROUND(AVG(ds.stock_qty), 2) as avg_stock,
                ROUND(AVG(ds.sale_qty), 2) as avg_daily_sale,
                ROUND(
                    CASE WHEN AVG(ds.stock_qty) > 0
                    THEN AVG(ds.sale_qty) / AVG(ds.stock_qty)
                    ELSE 0 END, 3
                ) as turnover_ratio,
                COUNT(*) as data_days
            FROM daily_sales ds
            LEFT JOIN common.products p ON ds.item_cd = p.item_cd
            WHERE ds.mid_cd = ?
              AND ds.sales_date >= date((SELECT MAX(sales_date) FROM daily_sales), '-30 days')
            GROUP BY ds.item_cd
            HAVING data_days >= 3
            ORDER BY turnover_ratio ASC
        """, (mid_cd,)).fetchall()

        items = []
        low_turnover = []
        for r in rows:
            item = {
                "item_cd": r["item_cd"],
                "item_nm": r["item_nm"],
                "avg_stock": r["avg_stock"],
                "avg_daily_sale": r["avg_daily_sale"],
                "turnover_ratio": r["turnover_ratio"],
                "data_days": r["data_days"],
            }
            items.append(item)
            if r["avg_stock"] > 0 and r["turnover_ratio"] < 0.2:
                low_turnover.append(item)

        # 재고 증가 추세 상품
        stock_trend_rows = conn.execute("""
            SELECT
                ds.item_cd,
                COALESCE(p.item_nm, '(이름없음)') as item_nm,
                AVG(CASE WHEN ds.sales_date >= date((SELECT MAX(sales_date) FROM daily_sales), '-7 days')
                    THEN ds.stock_qty END) as recent_avg_stock,
                AVG(CASE WHEN ds.sales_date < date((SELECT MAX(sales_date) FROM daily_sales), '-7 days')
                    AND ds.sales_date >= date((SELECT MAX(sales_date) FROM daily_sales), '-30 days')
                    THEN ds.stock_qty END) as earlier_avg_stock
            FROM daily_sales ds
            LEFT JOIN common.products p ON ds.item_cd = p.item_cd
            WHERE ds.mid_cd = ?
              AND ds.sales_date >= date((SELECT MAX(sales_date) FROM daily_sales), '-30 days')
            GROUP BY ds.item_cd
            HAVING recent_avg_stock IS NOT NULL AND earlier_avg_stock IS NOT NULL
                AND recent_avg_stock > earlier_avg_stock * 1.3
            ORDER BY (recent_avg_stock - earlier_avg_stock) DESC
        """, (mid_cd,)).fetchall()

        stock_increasing = []
        for r in stock_trend_rows:
            stock_increasing.append({
                "item_cd": r["item_cd"],
                "item_nm": r["item_nm"],
                "recent_7d_avg_stock": round(r["recent_avg_stock"], 1) if r["recent_avg_stock"] else 0,
                "earlier_avg_stock": round(r["earlier_avg_stock"], 1) if r["earlier_avg_stock"] else 0,
                "increase_pct": round((r["recent_avg_stock"] - r["earlier_avg_stock"]) / max(r["earlier_avg_stock"], 0.1) * 100, 1),
            })

        results[mid_cd] = {
            "category_name": MID_CD_NAMES[mid_cd],
            "total_items_analyzed": len(items),
            "low_turnover_items_count": len(low_turnover),
            "low_turnover_items": low_turnover[:10],
            "stock_increasing_items_count": len(stock_increasing),
            "stock_increasing_items": stock_increasing[:10],
        }
    return results


def analysis_8_expiry_analysis(conn, store_id):
    """8. 유통기한별 분석"""
    results = {}
    for mid_cd in TARGET_MID_CDS:
        # 유통기한 분포
        exp_rows = conn.execute("""
            SELECT
                pd.expiration_days,
                COUNT(*) as item_count
            FROM common.product_details pd
            JOIN common.products p ON pd.item_cd = p.item_cd
            WHERE p.mid_cd = ?
              AND pd.expiration_days IS NOT NULL
              AND pd.expiration_days > 0
            GROUP BY pd.expiration_days
            ORDER BY pd.expiration_days
        """, (mid_cd,)).fetchall()

        expiry_dist = {}
        for r in exp_rows:
            expiry_dist[str(r["expiration_days"]) + "일"] = r["item_count"]

        # 유통기한별 폐기율
        exp_disuse_rows = conn.execute("""
            SELECT
                CASE
                    WHEN pd.expiration_days <= 1 THEN '1일이하'
                    WHEN pd.expiration_days <= 2 THEN '2일'
                    WHEN pd.expiration_days <= 3 THEN '3일'
                    WHEN pd.expiration_days <= 5 THEN '4-5일'
                    ELSE '6일이상'
                END as exp_group,
                pd.expiration_days,
                SUM(ds.sale_qty) as total_sales,
                SUM(ds.disuse_qty) as total_disuse,
                ROUND(
                    CASE WHEN (SUM(ds.sale_qty) + SUM(ds.disuse_qty)) > 0
                    THEN CAST(SUM(ds.disuse_qty) AS REAL) / (SUM(ds.sale_qty) + SUM(ds.disuse_qty)) * 100
                    ELSE 0 END, 2
                ) as disuse_rate,
                COUNT(DISTINCT ds.item_cd) as item_count
            FROM daily_sales ds
            JOIN common.product_details pd ON ds.item_cd = pd.item_cd
            WHERE ds.mid_cd = ?
              AND ds.sales_date >= date((SELECT MAX(sales_date) FROM daily_sales), '-30 days')
              AND pd.expiration_days IS NOT NULL
              AND pd.expiration_days > 0
            GROUP BY exp_group
            ORDER BY MIN(pd.expiration_days)
        """, (mid_cd,)).fetchall()

        exp_disuse = []
        for r in exp_disuse_rows:
            exp_disuse.append({
                "expiry_group": r["exp_group"],
                "item_count": r["item_count"],
                "total_sales": r["total_sales"],
                "total_disuse": r["total_disuse"],
                "disuse_rate_pct": r["disuse_rate"],
            })

        results[mid_cd] = {
            "category_name": MID_CD_NAMES[mid_cd],
            "expiration_days_distribution": expiry_dist,
            "disuse_rate_by_expiry_group": exp_disuse,
        }
    return results


def run_all():
    """전체 분석 실행"""
    full_results = {}

    for store_id in STORE_IDS:
        store_name = STORE_NAMES[store_id]
        print(f"\n{'='*60}")
        print(f"  Analyzing store: {store_id} ({store_name})")
        print(f"{'='*60}")

        conn = connect_store(store_id)
        store_results = {}

        try:
            print("  [1/8] Basic status...")
            store_results["1_basic_status"] = analysis_1_basic_status(conn, store_id)

            print("  [2/8] Item details...")
            store_results["2_item_detail"] = analysis_2_item_detail(conn, store_id)

            print("  [3/8] Prediction accuracy...")
            store_results["3_prediction_accuracy"] = analysis_3_prediction_accuracy(conn, store_id)

            print("  [4/8] Eval decisions...")
            store_results["4_eval_decisions"] = analysis_4_eval_decisions(conn, store_id)

            print("  [5/8] Order vs sales...")
            store_results["5_order_vs_sales"] = analysis_5_order_vs_sales(conn, store_id)

            print("  [6/8] Weekday patterns...")
            store_results["6_weekday_pattern"] = analysis_6_weekday_pattern(conn, store_id)

            print("  [7/8] Inventory turnover...")
            store_results["7_inventory_turnover"] = analysis_7_inventory_turnover(conn, store_id)

            print("  [8/8] Expiry analysis...")
            store_results["8_expiry_analysis"] = analysis_8_expiry_analysis(conn, store_id)

        finally:
            conn.close()

        full_results[f"store_{store_id}_{store_name}"] = store_results

    # JSON 출력
    output = json.dumps(full_results, ensure_ascii=False, indent=2, default=str)
    print("\n" + "=" * 60)
    print("  FULL ANALYSIS RESULTS (JSON)")
    print("=" * 60)
    print(output)

    # 결과 파일 저장
    output_path = os.path.join(BASE_DIR, "data", "reports", "category_analysis_004_005_012.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output)
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    run_all()
