"""
BGF 리테일 자동발주 성과 검증 데이터 추출 스크립트
- 점포별 자동발주 시작 시점 파악
- 폐기 데이터 Before/After 비교 (폐기율 기준)
- 발주 정확도 데이터 추출
- CSV 저장 + 터미널 요약 출력

주의:
- waste_slips(폐기전표)는 시스템 구축 후에만 존재 → 금액 Before 비교 불가
- daily_sales.disuse_qty는 Before/After 모두 존재 → 폐기율로 비교
- 미완료 월(3월)은 일평균으로 정규화
"""

import sqlite3
import csv
import os
from datetime import datetime, timedelta
from collections import defaultdict
import calendar

TODAY = "2026-03-07"
TODAY_DT = datetime.strptime(TODAY, "%Y-%m-%d")
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(DATA_DIR, "validation")
COMMON_DB = os.path.join(DATA_DIR, "common.db")

STORES = [
    {"id": "46513", "db": os.path.join(DATA_DIR, "stores", "46513.db")},
    {"id": "46704", "db": os.path.join(DATA_DIR, "stores", "46704.db")},
    {"id": "47863", "db": os.path.join(DATA_DIR, "stores", "47863.db")},
]

os.makedirs(OUTPUT_DIR, exist_ok=True)


def get_store_name(store_id):
    conn = sqlite3.connect(COMMON_DB)
    cur = conn.cursor()
    cur.execute("SELECT store_name FROM stores WHERE store_id=?", (store_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else store_id


def get_auto_order_start(conn):
    cur = conn.cursor()
    cur.execute("SELECT MIN(order_date) FROM order_tracking WHERE order_source='auto'")
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def get_order_source_stats(conn):
    cur = conn.cursor()
    cur.execute("SELECT order_source, COUNT(*) FROM order_tracking GROUP BY order_source ORDER BY COUNT(*) DESC")
    return cur.fetchall()


def days_in_month_actual(year_month_str):
    """해당 월의 실제 수집일수 또는 달력일수"""
    y, m = map(int, year_month_str.split("-"))
    if y == TODAY_DT.year and m == TODAY_DT.month:
        return TODAY_DT.day  # 현재 진행 중인 월
    return calendar.monthrange(y, m)[1]


def extract_disuse_monthly(conn, store_id, auto_start):
    """폐기 데이터: 월별 집계 + 폐기율 (Before/After)"""
    cur = conn.cursor()
    cur.execute("""
        SELECT strftime('%Y-%m', sales_date) AS month,
               SUM(sale_qty) AS total_sale,
               SUM(disuse_qty) AS total_disuse,
               SUM(ord_qty) AS total_ord,
               COUNT(DISTINCT CASE WHEN disuse_qty > 0 THEN item_cd END) AS disuse_items,
               COUNT(DISTINCT sales_date) AS data_days
        FROM daily_sales
        GROUP BY month
        ORDER BY month
    """)
    rows = cur.fetchall()
    results = []
    for month, sale, disuse, ord_qty, disuse_items, data_days in rows:
        period = "after" if month >= auto_start[:7] else "before"
        disuse_rate = round(disuse * 100.0 / sale, 3) if sale > 0 else 0
        cal_days = days_in_month_actual(month)
        daily_avg_disuse = round(disuse / max(data_days, 1), 2)
        monthly_est_disuse = round(daily_avg_disuse * cal_days, 1)

        results.append({
            "store_id": store_id,
            "month": month,
            "period": period,
            "total_sale_qty": sale,
            "total_disuse_qty": disuse,
            "total_ord_qty": ord_qty or 0,
            "disuse_rate_pct": disuse_rate,
            "disuse_item_count": disuse_items,
            "data_days": data_days,
            "daily_avg_disuse": daily_avg_disuse,
            "monthly_est_disuse": monthly_est_disuse,
        })
    return results


def extract_top_disuse_items(conn, common_conn, store_id, auto_start):
    """상위 폐기 품목 Top 10 (Before/After)"""
    cur = conn.cursor()
    c_cur = common_conn.cursor()

    results = []
    for period_label, date_cond in [
        ("before", f"sales_date < '{auto_start}'"),
        ("after", f"sales_date >= '{auto_start}'"),
    ]:
        cur.execute(f"""
            SELECT item_cd, mid_cd, SUM(disuse_qty) AS total_disuse,
                   SUM(sale_qty) AS total_sale,
                   COUNT(DISTINCT sales_date) AS days
            FROM daily_sales
            WHERE disuse_qty > 0 AND {date_cond}
            GROUP BY item_cd
            ORDER BY total_disuse DESC
            LIMIT 10
        """)
        rows = cur.fetchall()
        for rank, (item_cd, mid_cd, total_disuse, total_sale, days) in enumerate(rows, 1):
            c_cur.execute("SELECT item_nm FROM products WHERE item_cd=?", (item_cd,))
            name_row = c_cur.fetchone()
            item_nm = name_row[0] if name_row else "Unknown"
            results.append({
                "store_id": store_id,
                "period": period_label,
                "rank": rank,
                "item_cd": item_cd,
                "item_nm": item_nm,
                "mid_cd": mid_cd or "",
                "total_disuse_qty": total_disuse,
                "total_sale_qty": total_sale or 0,
                "disuse_days": days,
            })
    return results


def extract_waste_slips_monthly(conn, store_id, auto_start):
    """폐기 전표 월별 집계 + waste_slip_items 상세"""
    cur = conn.cursor()

    # 전표 헤더 기반
    cur.execute("""
        SELECT strftime('%Y-%m', chit_date) AS month,
               COUNT(*) AS slip_count,
               COALESCE(SUM(wonga_amt), 0) AS total_wonga,
               COALESCE(SUM(maega_amt), 0) AS total_maega,
               COALESCE(SUM(item_cnt), 0) AS total_item_cnt
        FROM waste_slips
        GROUP BY month
        ORDER BY month
    """)
    rows = cur.fetchall()
    results = []
    for month, count, wonga, maega, item_cnt in rows:
        period = "after" if month >= auto_start[:7] else "before"
        cal_days = days_in_month_actual(month)
        daily_wonga = round(wonga / cal_days, 0) if cal_days > 0 else 0
        results.append({
            "store_id": store_id,
            "month": month,
            "period": period,
            "slip_count": count,
            "wonga_amt": round(wonga, 0),
            "maega_amt": round(maega, 0),
            "item_cnt": item_cnt,
            "daily_avg_wonga": daily_wonga,
            "monthly_est_wonga": round(daily_wonga * calendar.monthrange(*map(int, month.split("-")))[1], 0),
        })
    return results


def extract_prediction_accuracy(conn, store_id, auto_start):
    """예측 정확도: prediction_logs 기반 월별 (완료월만)"""
    cur = conn.cursor()
    cur.execute("""
        SELECT strftime('%Y-%m', prediction_date) AS month,
               COUNT(*) AS total_count,
               SUM(CASE WHEN actual_qty IS NOT NULL THEN 1 ELSE 0 END) AS has_actual_count,
               AVG(CASE WHEN actual_qty IS NOT NULL AND actual_qty > 0
                   THEN 1.0 - MIN(ABS(predicted_qty - actual_qty) * 1.0 / actual_qty, 1.0)
                   ELSE NULL END) AS avg_accuracy,
               SUM(CASE WHEN actual_qty IS NOT NULL AND predicted_qty > actual_qty * 1.2 AND actual_qty > 0 THEN 1 ELSE 0 END) AS over_order,
               SUM(CASE WHEN actual_qty IS NOT NULL AND predicted_qty < actual_qty * 0.8 AND actual_qty > 0 THEN 1 ELSE 0 END) AS under_order,
               SUM(CASE WHEN actual_qty IS NOT NULL AND actual_qty > 0
                        AND ABS(predicted_qty - actual_qty) <= actual_qty * 0.2 THEN 1 ELSE 0 END) AS accurate_count,
               AVG(CASE WHEN confidence IN ('high', 'medium', 'low') THEN
                   CASE confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 END
                   ELSE NULL END) AS avg_confidence_score
        FROM prediction_logs
        GROUP BY month
        ORDER BY month
    """)
    rows = cur.fetchall()
    results = []
    for month, total, has_actual, acc, over, under, accurate, conf in rows:
        period = "after" if month >= auto_start[:7] else "before"
        conf_label = "N/A"
        if conf:
            if conf >= 2.5:
                conf_label = "high"
            elif conf >= 1.5:
                conf_label = "medium"
            else:
                conf_label = "low"
        results.append({
            "store_id": store_id,
            "month": month,
            "period": period,
            "total_predictions": total,
            "has_actual_count": has_actual or 0,
            "actual_coverage_pct": round((has_actual or 0) * 100.0 / max(total, 1), 1),
            "avg_accuracy_pct": round(acc * 100, 1) if acc else 0,
            "accurate_count": accurate or 0,
            "over_order_count": over or 0,
            "under_order_count": under or 0,
            "avg_confidence": conf_label,
        })
    return results


def extract_model_type_accuracy(conn, store_id):
    """model_type별 예측 정확도"""
    cur = conn.cursor()
    cur.execute("""
        SELECT model_type,
               strftime('%Y-%m', prediction_date) AS month,
               COUNT(*) AS cnt,
               AVG(CASE WHEN actual_qty IS NOT NULL AND actual_qty > 0
                   THEN 1.0 - MIN(ABS(predicted_qty - actual_qty) * 1.0 / actual_qty, 1.0)
                   ELSE NULL END) AS avg_accuracy
        FROM prediction_logs
        WHERE actual_qty IS NOT NULL
        GROUP BY model_type, month
        ORDER BY model_type, month
    """)
    rows = cur.fetchall()
    results = []
    for model, month, cnt, acc in rows:
        results.append({
            "store_id": store_id,
            "model_type": model or "unknown",
            "month": month,
            "count": cnt,
            "avg_accuracy_pct": round(acc * 100, 1) if acc else 0,
        })
    return results


def save_csv(filepath, rows, fieldnames=None):
    if not rows:
        return
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return filepath


def compute_summary(store_id, store_name, auto_start, disuse_monthly, waste_slips_monthly, pred_accuracy):
    """핵심 지표 요약 계산"""
    # 폐기율 Before/After (핵심 지표: 판매 대비 폐기 비율)
    before_disuse = [r for r in disuse_monthly if r["period"] == "before"]
    after_disuse = [r for r in disuse_monthly if r["period"] == "after"]

    # 폐기율 가중평균 (총폐기/총판매)
    before_total_sale = sum(r["total_sale_qty"] for r in before_disuse)
    before_total_disuse = sum(r["total_disuse_qty"] for r in before_disuse)
    after_total_sale = sum(r["total_sale_qty"] for r in after_disuse)
    after_total_disuse = sum(r["total_disuse_qty"] for r in after_disuse)

    before_disuse_rate = round(before_total_disuse * 100.0 / max(before_total_sale, 1), 3)
    after_disuse_rate = round(after_total_disuse * 100.0 / max(after_total_sale, 1), 3)

    # 일평균 폐기수량 비교 (월별 불균형 보정)
    before_days = sum(r["data_days"] for r in before_disuse)
    after_days = sum(r["data_days"] for r in after_disuse)
    before_daily_disuse = round(before_total_disuse / max(before_days, 1), 2)
    after_daily_disuse = round(after_total_disuse / max(after_days, 1), 2)

    # 폐기금액 (waste_slips - after만 존재할 수 있음)
    after_wonga = [r for r in waste_slips_monthly if r["period"] == "after"]
    after_total_wonga = sum(r["wonga_amt"] for r in after_wonga)
    after_wonga_days = sum(days_in_month_actual(r["month"]) for r in after_wonga)
    after_daily_wonga = round(after_total_wonga / max(after_wonga_days, 1), 0)
    after_monthly_est_wonga = round(after_daily_wonga * 30, 0)

    # 발주 정확도 (prediction_logs - after만 존재)
    after_acc = [r for r in pred_accuracy if r["period"] == "after" and r["avg_accuracy_pct"] > 0]
    # 완료 월만 (실측 커버리지 > 80%)
    complete_acc = [r for r in after_acc if r["actual_coverage_pct"] > 80]
    avg_accuracy = round(sum(r["avg_accuracy_pct"] for r in complete_acc) / max(len(complete_acc), 1), 1) if complete_acc else 0

    total_over = sum(r["over_order_count"] for r in after_acc)
    total_under = sum(r["under_order_count"] for r in after_acc)
    total_accurate = sum(r["accurate_count"] for r in after_acc)

    # 분석 기간 (일)
    s = datetime.strptime(auto_start, "%Y-%m-%d")
    analysis_days = (TODAY_DT - s).days

    disuse_rate_change = after_disuse_rate - before_disuse_rate
    daily_disuse_change = ((after_daily_disuse - before_daily_disuse) / max(before_daily_disuse, 0.01)) * 100

    return {
        "store_id": store_id,
        "store_name": store_name,
        "auto_start": auto_start,
        "analysis_days": analysis_days,
        "before_months": len(before_disuse),
        "after_months": len(after_disuse),
        "before_data_days": before_days,
        "after_data_days": after_days,
        # 폐기율
        "before_disuse_rate_pct": before_disuse_rate,
        "after_disuse_rate_pct": after_disuse_rate,
        "disuse_rate_change_pp": round(disuse_rate_change, 3),
        # 일평균 폐기
        "before_daily_disuse": before_daily_disuse,
        "after_daily_disuse": after_daily_disuse,
        "daily_disuse_change_pct": round(daily_disuse_change, 1),
        # 폐기금액 (after만)
        "after_daily_wonga": after_daily_wonga,
        "after_monthly_est_wonga": after_monthly_est_wonga,
        # 발주 정확도
        "avg_accuracy_pct": avg_accuracy,
        "total_over_order": total_over,
        "total_under_order": total_under,
        "total_accurate": total_accurate,
        # 참고
        "note": "waste_slips는 시스템 구축 후만 존재. 폐기율은 daily_sales 기준."
    }


def print_summary(s):
    disuse_direction = "악화" if s["daily_disuse_change_pct"] > 0 else "개선"

    print(f"""
=============================
 점포 {s['store_id']} ({s['store_name']}) 성과 요약
=============================
자동발주 시작일: {s['auto_start']}
분석 기간: {s['analysis_days']}일 (Before {s['before_data_days']}일 / After {s['after_data_days']}일)

[폐기 지표 - 폐기율 (판매 대비)]
  도입 전 폐기율: {s['before_disuse_rate_pct']:.3f}%
  도입 후 폐기율: {s['after_disuse_rate_pct']:.3f}%
  변화: {s['disuse_rate_change_pp']:+.3f}%p

[폐기 지표 - 일평균 폐기수량]
  도입 전 일평균: {s['before_daily_disuse']}개/일
  도입 후 일평균: {s['after_daily_disuse']}개/일
  변화: {s['daily_disuse_change_pct']:+.1f}% ({disuse_direction})

[폐기금액 (시스템 구축 후)]
  일평균 폐기원가: {s['after_daily_wonga']:,.0f}원/일
  월 추정 폐기원가: {s['after_monthly_est_wonga']/10000:,.1f}만원/월

[발주 정확도 (완료월 기준)]
  평균 예측 정확도: {s['avg_accuracy_pct']:.1f}%
  적중 (오차 ≤20%): {s['total_accurate']:,}건
  과잉발주 (>120%): {s['total_over_order']:,}건
  부족발주 (<80%):  {s['total_under_order']:,}건

* {s['note']}
=============================
""")


def print_monthly_detail(store_id, store_name, auto_start, disuse_monthly, waste_slips_monthly, pred_accuracy):
    """월별 상세 테이블 출력"""
    print(f"\n--- 점포 {store_id} 월별 상세 ---")
    print(f"{'월':>8} {'구간':>6} {'판매':>7} {'폐기':>5} {'폐기율':>7} {'일평균폐기':>9} {'품목수':>5} | {'전표수':>5} {'원가(만)':>8} | {'정확도':>6} {'과잉':>5} {'부족':>5}")
    print("-" * 110)

    waste_map = {r["month"]: r for r in waste_slips_monthly}
    acc_map = {r["month"]: r for r in pred_accuracy}

    for r in disuse_monthly:
        m = r["month"]
        period = "AFTER" if r["period"] == "after" else "BEFORE"
        w = waste_map.get(m, {})
        a = acc_map.get(m, {})

        slip_cnt = w.get("slip_count", "-")
        wonga = f"{w['wonga_amt']/10000:.1f}" if w.get("wonga_amt") else "-"
        acc_pct = f"{a['avg_accuracy_pct']:.1f}%" if a.get("avg_accuracy_pct") else "-"
        over = a.get("over_order_count", "-")
        under = a.get("under_order_count", "-")

        marker = " <<" if m == auto_start[:7] else ""
        print(f"{m:>8} {period:>6} {r['total_sale_qty']:>7,} {r['total_disuse_qty']:>5} {r['disuse_rate_pct']:>6.3f}% {r['daily_avg_disuse']:>9.2f} {r['disuse_item_count']:>5} | {slip_cnt:>5} {wonga:>8} | {acc_pct:>6} {over:>5} {under:>5}{marker}")


def main():
    common_conn = sqlite3.connect(COMMON_DB)
    all_summaries = []
    all_disuse = []
    all_waste_slips = []
    all_pred_accuracy = []
    all_top_disuse = []
    all_model_accuracy = []

    for store in STORES:
        store_id = store["id"]
        db_path = store["db"]
        store_name = get_store_name(store_id)

        print(f"\n{'='*60}")
        print(f"  처리 중: 매장 {store_id} ({store_name})")
        print(f"{'='*60}")

        conn = sqlite3.connect(db_path)
        auto_start = get_auto_order_start(conn)

        if not auto_start:
            print(f"  [SKIP] 자동발주 이력 없음")
            conn.close()
            continue

        # order_source 통계
        source_stats = get_order_source_stats(conn)
        print(f"  자동발주 시작일: {auto_start}")
        print(f"  order_source 분포:")
        for src, cnt in source_stats:
            print(f"    {src or 'NULL'}: {cnt}건")

        # 1. 폐기 데이터
        disuse_monthly = extract_disuse_monthly(conn, store_id, auto_start)
        all_disuse.extend(disuse_monthly)

        top_disuse = extract_top_disuse_items(conn, common_conn, store_id, auto_start)
        all_top_disuse.extend(top_disuse)

        waste_slips = extract_waste_slips_monthly(conn, store_id, auto_start)
        all_waste_slips.extend(waste_slips)

        # 2. 발주 정확도
        pred_accuracy = extract_prediction_accuracy(conn, store_id, auto_start)
        all_pred_accuracy.extend(pred_accuracy)

        model_accuracy = extract_model_type_accuracy(conn, store_id)
        all_model_accuracy.extend(model_accuracy)

        # 3. 점포별 CSV 저장
        csv_rows = []
        for r in disuse_monthly:
            csv_rows.append({
                "category": "disuse_monthly",
                "month": r["month"],
                "period": r["period"],
                "metric_1": f"sale={r['total_sale_qty']}",
                "metric_2": f"disuse={r['total_disuse_qty']}",
                "metric_3": f"rate={r['disuse_rate_pct']}%",
                "metric_4": f"daily_avg={r['daily_avg_disuse']}",
                "detail": f"품목수={r['disuse_item_count']}, 수집일수={r['data_days']}",
            })
        for r in waste_slips:
            csv_rows.append({
                "category": "waste_slips",
                "month": r["month"],
                "period": r["period"],
                "metric_1": f"wonga={r['wonga_amt']}",
                "metric_2": f"maega={r['maega_amt']}",
                "metric_3": f"slips={r['slip_count']}",
                "metric_4": f"items={r['item_cnt']}",
                "detail": f"일평균원가={r['daily_avg_wonga']}",
            })
        for r in pred_accuracy:
            csv_rows.append({
                "category": "prediction_accuracy",
                "month": r["month"],
                "period": r["period"],
                "metric_1": f"accuracy={r['avg_accuracy_pct']}%",
                "metric_2": f"over={r['over_order_count']}",
                "metric_3": f"under={r['under_order_count']}",
                "metric_4": f"accurate={r['accurate_count']}",
                "detail": f"total={r['total_predictions']}, actual={r['has_actual_count']}, coverage={r['actual_coverage_pct']}%",
            })
        for r in top_disuse:
            csv_rows.append({
                "category": f"top_disuse_{r['period']}",
                "month": "",
                "period": r["period"],
                "metric_1": f"rank={r['rank']}",
                "metric_2": f"disuse={r['total_disuse_qty']}",
                "metric_3": f"sale={r['total_sale_qty']}",
                "metric_4": "",
                "detail": f"{r['item_nm']} ({r['item_cd']}) mid:{r['mid_cd']}",
            })

        csv_path = os.path.join(OUTPUT_DIR, f"{store_id}_before_after_{TODAY}.csv")
        save_csv(csv_path, csv_rows, ["category", "month", "period", "metric_1", "metric_2", "metric_3", "metric_4", "detail"])
        print(f"  CSV 저장: {csv_path}")

        # 4. 월별 상세 출력
        print_monthly_detail(store_id, store_name, auto_start, disuse_monthly, waste_slips, pred_accuracy)

        # 5. 상위 폐기 품목 출력
        print(f"\n--- 점포 {store_id} 상위 폐기 품목 Top 10 ---")
        for period_label in ["before", "after"]:
            items = [r for r in top_disuse if r["period"] == period_label]
            if items:
                print(f"\n  [{period_label.upper()}]")
                for r in items:
                    print(f"    {r['rank']:>2}. {r['item_nm'][:20]:<20} (mid:{r['mid_cd']}) 폐기={r['total_disuse_qty']}개 판매={r['total_sale_qty']}개")

        # 6. 요약 계산
        summary = compute_summary(store_id, store_name, auto_start, disuse_monthly, waste_slips, pred_accuracy)
        all_summaries.append(summary)

        conn.close()

    # 통합 요약 CSV
    summary_path = os.path.join(OUTPUT_DIR, f"summary_all_stores_{TODAY}.csv")
    save_csv(summary_path, all_summaries)
    print(f"\n통합 요약 CSV: {summary_path}")

    # 상세 데이터 CSV도 저장
    save_csv(os.path.join(OUTPUT_DIR, f"all_disuse_monthly_{TODAY}.csv"), all_disuse)
    save_csv(os.path.join(OUTPUT_DIR, f"all_waste_slips_{TODAY}.csv"), all_waste_slips)
    save_csv(os.path.join(OUTPUT_DIR, f"all_prediction_accuracy_{TODAY}.csv"), all_pred_accuracy)
    save_csv(os.path.join(OUTPUT_DIR, f"all_top_disuse_items_{TODAY}.csv"), all_top_disuse)
    save_csv(os.path.join(OUTPUT_DIR, f"all_model_accuracy_{TODAY}.csv"), all_model_accuracy)

    # 터미널 최종 요약 출력
    print("\n" + "=" * 60)
    print("  전체 점포 성과 요약")
    print("=" * 60)

    for s in all_summaries:
        print_summary(s)

    # 3개 점포 비교 테이블
    print("\n" + "=" * 60)
    print("  3개 점포 비교 요약")
    print("=" * 60)
    print(f"{'지표':<25} ", end="")
    for s in all_summaries:
        print(f"{'점포 '+s['store_id']:>15}", end="")
    print()
    print("-" * (25 + 15 * len(all_summaries)))

    metrics = [
        ("자동발주 시작일", "auto_start", ""),
        ("분석 기간 (일)", "analysis_days", "일"),
        ("Before 폐기율", "before_disuse_rate_pct", "%"),
        ("After 폐기율", "after_disuse_rate_pct", "%"),
        ("폐기율 변화", "disuse_rate_change_pp", "%p"),
        ("Before 일평균폐기", "before_daily_disuse", "개"),
        ("After 일평균폐기", "after_daily_disuse", "개"),
        ("일평균폐기 변화", "daily_disuse_change_pct", "%"),
        ("월 추정 폐기원가", "after_monthly_est_wonga", "만원"),
        ("예측 정확도", "avg_accuracy_pct", "%"),
        ("과잉발주 건수", "total_over_order", "건"),
        ("부족발주 건수", "total_under_order", "건"),
    ]
    for label, key, unit in metrics:
        print(f"{label:<25} ", end="")
        for s in all_summaries:
            val = s[key]
            if key == "after_monthly_est_wonga":
                val = f"{val/10000:.1f}"
            elif isinstance(val, float):
                val = f"{val:.3f}" if "rate" in key or "change_pp" in key else f"{val:.1f}"
            print(f"{str(val)+unit:>15}", end="")
        print()

    common_conn.close()
    print(f"\n전체 CSV 파일 저장 위치: {OUTPUT_DIR}")
    print("완료!")


if __name__ == "__main__":
    main()
