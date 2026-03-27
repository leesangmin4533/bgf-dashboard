"""
BGF 리테일 매출 vs 폐기 금액 분석 스크립트

데이터 소스:
- daily_sales: sale_qty, disuse_qty (sale_amt 없음)
- product_details: sell_price, margin_rate → 매출/폐기 금액 추정
- waste_slips: wonga_amt (실제 폐기 원가, After만 존재)
- eval_outcomes: 품절/재고부족 판정
- order_fail_reasons: 발주 실패 건수

매출 추정 = sale_qty × sell_price
폐기 원가 추정 = disuse_qty × sell_price × (1 - margin_rate/100)
  (margin_rate 없으면 폐기 원가 = disuse_qty × sell_price × 0.55 추정)
"""

import sqlite3
import csv
import os
import calendar
from datetime import datetime

TODAY = "2026-03-07"
TODAY_DT = datetime.strptime(TODAY, "%Y-%m-%d")
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(DATA_DIR, "validation")
COMMON_DB = os.path.join(DATA_DIR, "common.db")

STORES = [
    {"id": "46513", "db": os.path.join(DATA_DIR, "stores", "46513.db"), "auto_start": "2026-01-26"},
    {"id": "46704", "db": os.path.join(DATA_DIR, "stores", "46704.db"), "auto_start": "2026-02-24"},
    {"id": "47863", "db": os.path.join(DATA_DIR, "stores", "47863.db"), "auto_start": "2026-03-05"},
]

DEFAULT_COST_RATIO = 0.55  # margin_rate 없을 때 원가율 추정

os.makedirs(OUTPUT_DIR, exist_ok=True)


def get_store_name(store_id):
    conn = sqlite3.connect(COMMON_DB)
    cur = conn.cursor()
    cur.execute("SELECT store_name FROM stores WHERE store_id=?", (store_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else store_id


def build_price_map():
    """product_details에서 sell_price, margin_rate 맵 구축"""
    conn = sqlite3.connect(COMMON_DB)
    cur = conn.cursor()
    cur.execute("SELECT item_cd, sell_price, margin_rate FROM product_details WHERE sell_price IS NOT NULL AND sell_price > 0")
    price_map = {}
    for item_cd, price, margin in cur.fetchall():
        cost_ratio = (1.0 - margin / 100.0) if margin and margin > 0 else DEFAULT_COST_RATIO
        price_map[item_cd] = {"sell_price": price, "cost_ratio": cost_ratio}
    conn.close()
    return price_map


def days_in_month_actual(year_month_str):
    y, m = map(int, year_month_str.split("-"))
    if y == TODAY_DT.year and m == TODAY_DT.month:
        return TODAY_DT.day
    return calendar.monthrange(y, m)[1]


def extract_revenue_waste(conn, store_id, auto_start, price_map):
    """매출 및 폐기 금액 월별 추출 (sale_qty × sell_price 방식)"""
    cur = conn.cursor()
    cur.execute("""
        SELECT sales_date, item_cd, sale_qty, disuse_qty
        FROM daily_sales
    """)
    rows = cur.fetchall()

    monthly = {}  # month -> {revenue, waste_cost, sale_qty, disuse_qty, matched, unmatched, days}

    for sales_date, item_cd, sale_qty, disuse_qty in rows:
        month = sales_date[:7]
        if month not in monthly:
            monthly[month] = {
                "revenue": 0, "waste_cost": 0, "waste_sell": 0,
                "sale_qty": 0, "disuse_qty": 0,
                "matched": 0, "unmatched": 0, "days": set(),
            }
        m = monthly[month]
        m["sale_qty"] += (sale_qty or 0)
        m["disuse_qty"] += (disuse_qty or 0)
        m["days"].add(sales_date)

        p = price_map.get(item_cd)
        if p:
            m["matched"] += 1
            sell_price = p["sell_price"]
            cost_ratio = p["cost_ratio"]
            m["revenue"] += (sale_qty or 0) * sell_price
            m["waste_cost"] += (disuse_qty or 0) * sell_price * cost_ratio
            m["waste_sell"] += (disuse_qty or 0) * sell_price
        else:
            m["unmatched"] += 1

    results = []
    for month in sorted(monthly.keys()):
        m = monthly[month]
        period = "after" if month >= auto_start[:7] else "before"
        data_days = len(m["days"])
        cal_days = days_in_month_actual(month)
        total_rows = m["matched"] + m["unmatched"]
        match_rate = round(m["matched"] * 100.0 / max(total_rows, 1), 1)

        # 일평균으로 정규화
        daily_revenue = round(m["revenue"] / max(data_days, 1))
        daily_waste = round(m["waste_cost"] / max(data_days, 1))
        est_monthly_revenue = daily_revenue * cal_days
        est_monthly_waste = daily_waste * cal_days

        waste_rate = round(m["waste_cost"] * 100.0 / max(m["revenue"], 1), 3) if m["revenue"] > 0 else 0

        results.append({
            "store_id": store_id,
            "month": month,
            "period": period,
            "revenue": m["revenue"],
            "waste_cost": round(m["waste_cost"]),
            "waste_sell_price": round(m["waste_sell"]),
            "sale_qty": m["sale_qty"],
            "disuse_qty": m["disuse_qty"],
            "data_days": data_days,
            "cal_days": cal_days,
            "daily_revenue": daily_revenue,
            "daily_waste_cost": daily_waste,
            "est_monthly_revenue": est_monthly_revenue,
            "est_monthly_waste": est_monthly_waste,
            "waste_rate_pct": waste_rate,
            "price_match_rate": match_rate,
        })
    return results


def extract_waste_slips_monthly(conn, store_id, auto_start):
    """waste_slips 실제 폐기 원가"""
    cur = conn.cursor()
    cur.execute("""
        SELECT strftime('%Y-%m', chit_date) AS month,
               COUNT(*) AS slip_count,
               COALESCE(SUM(wonga_amt), 0) AS total_wonga,
               COALESCE(SUM(maega_amt), 0) AS total_maega,
               COALESCE(SUM(item_cnt), 0) AS total_items
        FROM waste_slips
        GROUP BY month ORDER BY month
    """)
    results = []
    for month, cnt, wonga, maega, items in cur.fetchall():
        period = "after" if month >= auto_start[:7] else "before"
        cal_days = days_in_month_actual(month)
        results.append({
            "store_id": store_id,
            "month": month,
            "period": period,
            "slip_count": cnt,
            "wonga_amt": round(wonga),
            "maega_amt": round(maega),
            "item_cnt": items,
            "daily_wonga": round(wonga / max(cal_days, 1)),
        })
    return results


def extract_stockout_analysis(conn, store_id, auto_start):
    """품절 기회손실 분석"""
    cur = conn.cursor()

    # eval_outcomes: FORCE_ORDER (재고부족 긴급발주) + URGENT_ORDER (품절 임박)
    cur.execute("""
        SELECT strftime('%Y-%m', eval_date) AS month,
               decision,
               COUNT(*) AS cnt
        FROM eval_outcomes
        WHERE decision IN ('FORCE_ORDER', 'URGENT_ORDER')
        GROUP BY month, decision
        ORDER BY month, decision
    """)
    eval_rows = cur.fetchall()

    # order_fail_reasons: 발주 실패
    cur.execute("""
        SELECT strftime('%Y-%m', eval_date) AS month,
               COUNT(*) AS fail_count
        FROM order_fail_reasons
        GROUP BY month
        ORDER BY month
    """)
    fail_rows = cur.fetchall()
    fail_map = {m: c for m, c in fail_rows}

    monthly = {}
    for month, decision, cnt in eval_rows:
        if month not in monthly:
            monthly[month] = {"FORCE_ORDER": 0, "URGENT_ORDER": 0, "order_fail": 0}
        monthly[month][decision] = cnt

    for month in monthly:
        monthly[month]["order_fail"] = fail_map.get(month, 0)

    results = []
    for month in sorted(monthly.keys()):
        m = monthly[month]
        period = "after" if month >= auto_start[:7] else "before"
        results.append({
            "store_id": store_id,
            "month": month,
            "period": period,
            "force_order_count": m["FORCE_ORDER"],
            "urgent_order_count": m["URGENT_ORDER"],
            "order_fail_count": m["order_fail"],
            "total_stockout_events": m["FORCE_ORDER"] + m["URGENT_ORDER"],
        })
    return results


def estimate_opportunity_loss(conn, store_id, auto_start, price_map):
    """품절 기회손실 금액 추정
    eval_outcomes에서 was_stockout=1인 상품의 일평균 매출 × 품절일수"""
    cur = conn.cursor()
    cur.execute("""
        SELECT strftime('%Y-%m', eval_date) AS month,
               item_cd,
               COUNT(*) AS stockout_days,
               AVG(daily_avg) AS avg_daily
        FROM eval_outcomes
        WHERE was_stockout = 1 AND daily_avg > 0
        GROUP BY month, item_cd
    """)
    rows = cur.fetchall()

    monthly_loss = {}
    for month, item_cd, days, avg_daily in rows:
        p = price_map.get(item_cd)
        if p:
            loss = days * avg_daily * p["sell_price"]
            if month not in monthly_loss:
                monthly_loss[month] = {"loss_amt": 0, "stockout_items": 0, "stockout_days": 0}
            monthly_loss[month]["loss_amt"] += loss
            monthly_loss[month]["stockout_items"] += 1
            monthly_loss[month]["stockout_days"] += days

    results = []
    for month in sorted(monthly_loss.keys()):
        m = monthly_loss[month]
        period = "after" if month >= auto_start[:7] else "before"
        results.append({
            "store_id": store_id,
            "month": month,
            "period": period,
            "opportunity_loss_amt": round(m["loss_amt"]),
            "stockout_items": m["stockout_items"],
            "stockout_item_days": m["stockout_days"],
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


def compute_summary(store_id, store_name, auto_start, revenue_data, waste_slips, stockout_data, opp_loss_data):
    """점포별 핵심 요약"""
    before = [r for r in revenue_data if r["period"] == "before"]
    after = [r for r in revenue_data if r["period"] == "after"]

    # 완전한 월만 사용 (data_days >= 25)
    before_full = [r for r in before if r["data_days"] >= 25]
    after_full = [r for r in after if r["data_days"] >= 25]

    # 일평균 기반으로 월 추정
    before_days = sum(r["data_days"] for r in before)
    after_days = sum(r["data_days"] for r in after)
    before_total_rev = sum(r["revenue"] for r in before)
    after_total_rev = sum(r["revenue"] for r in after)
    before_total_waste = sum(r["waste_cost"] for r in before)
    after_total_waste = sum(r["waste_cost"] for r in after)

    before_daily_rev = before_total_rev / max(before_days, 1)
    after_daily_rev = after_total_rev / max(after_days, 1)
    before_daily_waste = before_total_waste / max(before_days, 1)
    after_daily_waste = after_total_waste / max(after_days, 1)

    before_monthly_rev = round(before_daily_rev * 30)
    after_monthly_rev = round(after_daily_rev * 30)
    before_monthly_waste = round(before_daily_waste * 30)
    after_monthly_waste = round(after_daily_waste * 30)

    rev_change_pct = round((after_daily_rev - before_daily_rev) / max(before_daily_rev, 1) * 100, 1) if before_daily_rev > 0 else 0
    waste_saving_monthly = before_monthly_waste - after_monthly_waste

    before_waste_rate = round(before_total_waste * 100 / max(before_total_rev, 1), 3)
    after_waste_rate = round(after_total_waste * 100 / max(after_total_rev, 1), 3)

    # 폐기전표 기반 (실제 원가, after만)
    after_slips = [r for r in waste_slips if r["period"] == "after"]
    slip_daily_wonga = sum(r["wonga_amt"] for r in after_slips) / max(sum(days_in_month_actual(r["month"]) for r in after_slips), 1) if after_slips else 0
    slip_monthly_wonga = round(slip_daily_wonga * 30)

    # 품절 기회손실
    before_opp = [r for r in opp_loss_data if r["period"] == "before"]
    after_opp = [r for r in opp_loss_data if r["period"] == "after"]
    before_opp_days = sum(r["data_days"] for r in before if r["month"] in {o["month"] for o in before_opp}) or before_days
    after_opp_days = sum(r["data_days"] for r in after if r["month"] in {o["month"] for o in after_opp}) or after_days

    before_total_opp = sum(r["opportunity_loss_amt"] for r in before_opp)
    after_total_opp = sum(r["opportunity_loss_amt"] for r in after_opp)
    before_daily_opp = before_total_opp / max(before_opp_days, 1)
    after_daily_opp = after_total_opp / max(after_opp_days, 1)

    # 품절 이벤트
    before_stockout = [r for r in stockout_data if r["period"] == "before"]
    after_stockout = [r for r in stockout_data if r["period"] == "after"]
    before_monthly_stockout = sum(r["total_stockout_events"] for r in before_stockout) / max(len(before_stockout), 1) if before_stockout else 0
    after_monthly_stockout = sum(r["total_stockout_events"] for r in after_stockout) / max(len(after_stockout), 1) if after_stockout else 0

    analysis_days = (TODAY_DT - datetime.strptime(auto_start, "%Y-%m-%d")).days

    return {
        "store_id": store_id,
        "store_name": store_name,
        "auto_start": auto_start,
        "analysis_days": analysis_days,
        "before_data_days": before_days,
        "after_data_days": after_days,
        # 매출
        "before_daily_revenue": round(before_daily_rev),
        "after_daily_revenue": round(after_daily_rev),
        "before_monthly_revenue": before_monthly_rev,
        "after_monthly_revenue": after_monthly_rev,
        "revenue_change_pct": rev_change_pct,
        # 폐기 (추정)
        "before_daily_waste": round(before_daily_waste),
        "after_daily_waste": round(after_daily_waste),
        "before_monthly_waste": before_monthly_waste,
        "after_monthly_waste": after_monthly_waste,
        "waste_saving_monthly": waste_saving_monthly,
        "waste_saving_annual": waste_saving_monthly * 12,
        # 폐기율
        "before_waste_rate_pct": before_waste_rate,
        "after_waste_rate_pct": after_waste_rate,
        "waste_rate_change_pp": round(after_waste_rate - before_waste_rate, 3),
        # 폐기전표 실제
        "slip_monthly_wonga": slip_monthly_wonga,
        # 기회손실
        "before_daily_opp_loss": round(before_daily_opp),
        "after_daily_opp_loss": round(after_daily_opp),
        "opp_loss_change_pct": round((after_daily_opp - before_daily_opp) / max(before_daily_opp, 1) * 100, 1) if before_daily_opp > 0 else 0,
        # 품절
        "before_monthly_stockout": round(before_monthly_stockout),
        "after_monthly_stockout": round(after_monthly_stockout),
    }


def print_summary(s):
    print(f"""
=============================
 점포 {s['store_id']} ({s['store_name']}) 수익성 분석
=============================
자동발주 시작일: {s['auto_start']}
분석 기간: {s['analysis_days']}일 (Before {s['before_data_days']}일 / After {s['after_data_days']}일)
  * 매출 = sale_qty × sell_price (추정), 폐기원가 = disuse_qty × sell_price × 원가율

[매출 변화]
  도입 전 일평균 매출: {s['before_daily_revenue']:>12,}원  (월추정 {s['before_monthly_revenue']/10000:,.1f}만원)
  도입 후 일평균 매출: {s['after_daily_revenue']:>12,}원  (월추정 {s['after_monthly_revenue']/10000:,.1f}만원)
  변화율: {s['revenue_change_pct']:+.1f}%

[폐기 금액 변화 (추정 원가)]
  도입 전 일평균 폐기: {s['before_daily_waste']:>12,}원  (월추정 {s['before_monthly_waste']/10000:,.1f}만원)
  도입 후 일평균 폐기: {s['after_daily_waste']:>12,}원  (월추정 {s['after_monthly_waste']/10000:,.1f}만원)
  월간 절감액: {s['waste_saving_monthly']/10000:+,.1f}만원/월
  연간 절감 추정: {s['waste_saving_annual']/10000:+,.1f}만원/년

[폐기전표 실제 원가 (시스템 구축 후)]
  월 추정 폐기원가: {s['slip_monthly_wonga']/10000:,.1f}만원/월

[폐기율 (폐기원가 / 매출)]
  도입 전: {s['before_waste_rate_pct']:.3f}%
  도입 후: {s['after_waste_rate_pct']:.3f}%
  변화: {s['waste_rate_change_pp']:+.3f}%p

[품절 기회손실]
  도입 전 일평균 기회손실: {s['before_daily_opp_loss']:>10,}원
  도입 후 일평균 기회손실: {s['after_daily_opp_loss']:>10,}원
  변화율: {s['opp_loss_change_pct']:+.1f}%

[품절/긴급발주 이벤트]
  도입 전 월평균: {s['before_monthly_stockout']:,}건 (FORCE_ORDER + URGENT_ORDER)
  도입 후 월평균: {s['after_monthly_stockout']:,}건
=============================
""")


def main():
    price_map = build_price_map()
    print(f"단가 맵 구축: {len(price_map)}개 상품")

    all_revenue = []
    all_waste_slips = []
    all_stockout = []
    all_opp_loss = []
    all_summaries = []

    for store in STORES:
        store_id = store["id"]
        auto_start = store["auto_start"]
        store_name = get_store_name(store_id)

        print(f"\n{'='*60}")
        print(f"  처리 중: 매장 {store_id} ({store_name})")
        print(f"{'='*60}")

        conn = sqlite3.connect(store["db"])

        # 1. 매출/폐기 금액
        revenue_data = extract_revenue_waste(conn, store_id, auto_start, price_map)
        all_revenue.extend(revenue_data)

        # 2. 폐기전표
        waste_slips = extract_waste_slips_monthly(conn, store_id, auto_start)
        all_waste_slips.extend(waste_slips)

        # 3. 품절 분석
        stockout_data = extract_stockout_analysis(conn, store_id, auto_start)
        all_stockout.extend(stockout_data)

        # 4. 기회손실 추정
        opp_loss = estimate_opportunity_loss(conn, store_id, auto_start, price_map)
        all_opp_loss.extend(opp_loss)

        # 월별 상세 출력
        print(f"\n--- 매장 {store_id} 월별 매출/폐기 ---")
        print(f"{'월':>8} {'구간':>6} {'일평균매출':>12} {'일평균폐기원가':>14} {'폐기율':>7} {'판매수':>7} {'폐기수':>5} {'수집일':>4} {'단가매칭':>6}")
        print("-" * 90)

        for r in revenue_data:
            marker = " <<" if r["month"] == auto_start[:7] else ""
            print(f"{r['month']:>8} {r['period'].upper():>6} "
                  f"{r['daily_revenue']:>12,} {r['daily_waste_cost']:>14,} "
                  f"{r['waste_rate_pct']:>6.3f}% {r['sale_qty']:>7,} {r['disuse_qty']:>5} "
                  f"{r['data_days']:>4} {r['price_match_rate']:>5.1f}%{marker}")

        # 폐기전표 출력
        if waste_slips:
            print(f"\n--- 매장 {store_id} 폐기전표 실제 원가 ---")
            for r in waste_slips:
                print(f"  {r['month']}: 전표 {r['slip_count']}건, "
                      f"원가 {r['wonga_amt']:,}원, 매가 {r['maega_amt']:,}원, "
                      f"품목 {r['item_cnt']}개, 일평균 {r['daily_wonga']:,}원")

        # 품절 분석 출력
        if stockout_data:
            print(f"\n--- 매장 {store_id} 품절/긴급발주 ---")
            for r in stockout_data:
                print(f"  {r['month']} [{r['period'].upper()}]: "
                      f"FORCE={r['force_order_count']}, URGENT={r['urgent_order_count']}, "
                      f"발주실패={r['order_fail_count']}")

        # 기회손실 출력
        if opp_loss:
            print(f"\n--- 매장 {store_id} 품절 기회손실 추정 ---")
            for r in opp_loss:
                print(f"  {r['month']} [{r['period'].upper()}]: "
                      f"손실추정 {r['opportunity_loss_amt']:,}원, "
                      f"품절품목 {r['stockout_items']}개, 품절일수 {r['stockout_item_days']}일")

        # 요약
        summary = compute_summary(store_id, store_name, auto_start, revenue_data, waste_slips, stockout_data, opp_loss)
        all_summaries.append(summary)

        conn.close()

    # CSV 저장
    csv_fields = [
        "store_id", "month", "period", "revenue", "waste_cost", "waste_sell_price",
        "waste_rate_pct", "daily_revenue", "daily_waste_cost",
        "est_monthly_revenue", "est_monthly_waste",
        "sale_qty", "disuse_qty", "data_days", "cal_days", "price_match_rate",
    ]
    main_csv = os.path.join(OUTPUT_DIR, f"revenue_vs_waste_{TODAY}.csv")
    save_csv(main_csv, all_revenue, csv_fields)
    print(f"\n메인 CSV: {main_csv}")

    save_csv(os.path.join(OUTPUT_DIR, f"stockout_analysis_{TODAY}.csv"), all_stockout)
    save_csv(os.path.join(OUTPUT_DIR, f"opportunity_loss_{TODAY}.csv"), all_opp_loss)

    summary_csv = os.path.join(OUTPUT_DIR, f"revenue_summary_{TODAY}.csv")
    save_csv(summary_csv, all_summaries)
    print(f"요약 CSV: {summary_csv}")

    # 터미널 최종 요약
    print("\n" + "=" * 60)
    print("  전체 점포 수익성 분석 요약")
    print("=" * 60)

    for s in all_summaries:
        print_summary(s)

    # 3개 점포 비교
    print("\n" + "=" * 60)
    print("  3개 점포 비교 요약")
    print("=" * 60)
    print(f"{'지표':<30} ", end="")
    for s in all_summaries:
        print(f"{'점포 '+s['store_id']:>16}", end="")
    print()
    print("-" * (30 + 16 * len(all_summaries)))

    metrics = [
        ("자동발주 시작일", "auto_start", "", "s"),
        ("분석 기간", "analysis_days", "일", "d"),
        ("도입 전 월추정매출", "before_monthly_revenue", "만원", "m"),
        ("도입 후 월추정매출", "after_monthly_revenue", "만원", "m"),
        ("매출 변화율", "revenue_change_pct", "%", "f1"),
        ("도입 전 월추정폐기원가", "before_monthly_waste", "만원", "m"),
        ("도입 후 월추정폐기원가", "after_monthly_waste", "만원", "m"),
        ("월간 폐기 절감액", "waste_saving_monthly", "만원", "m"),
        ("연간 폐기 절감 추정", "waste_saving_annual", "만원", "m"),
        ("도입 전 폐기율", "before_waste_rate_pct", "%", "f3"),
        ("도입 후 폐기율", "after_waste_rate_pct", "%", "f3"),
        ("폐기율 변화", "waste_rate_change_pp", "%p", "f3"),
        ("폐기전표 월추정원가", "slip_monthly_wonga", "만원", "m"),
        ("도입 전 월 품절이벤트", "before_monthly_stockout", "건", "d"),
        ("도입 후 월 품절이벤트", "after_monthly_stockout", "건", "d"),
        ("도입 전 일평균기회손실", "before_daily_opp_loss", "원", "c"),
        ("도입 후 일평균기회손실", "after_daily_opp_loss", "원", "c"),
    ]
    for label, key, unit, fmt in metrics:
        print(f"{label:<30} ", end="")
        for s in all_summaries:
            val = s[key]
            if fmt == "s":
                v = str(val)
            elif fmt == "m":
                v = f"{val/10000:,.1f}"
            elif fmt == "f1":
                v = f"{val:+.1f}"
            elif fmt == "f3":
                v = f"{val:.3f}"
            elif fmt == "c":
                v = f"{val:,}"
            elif fmt == "d":
                v = f"{val:,}"
            else:
                v = str(val)
            print(f"{v+unit:>16}", end="")
        print()

    print(f"\nCSV 저장 위치: {OUTPUT_DIR}")
    print("완료!")


if __name__ == "__main__":
    main()
