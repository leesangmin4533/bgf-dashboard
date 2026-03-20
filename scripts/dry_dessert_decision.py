"""디저트 판단 dry-run — 변경 전후 비교"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.infrastructure.database.connection import DBRouter
from src.prediction.categories.dessert_decision.classifier import classify_dessert_category
from src.prediction.categories.dessert_decision.lifecycle import determine_lifecycle
from src.prediction.categories.dessert_decision.judge import judge_item, calc_waste_rate
from src.prediction.categories.dessert_decision.enums import (
    DessertCategory, DessertLifecycle, DessertDecisionType, FirstReceivingSource,
)
from datetime import datetime, timedelta

STORE_ID = "46513"
REF_DATE = "2026-03-20"

def get_current_decision(conn, item_cd):
    """DB에서 현재 판단 결과 조회"""
    try:
        cursor = conn.execute("""
            SELECT decision, operator_action
            FROM dessert_decisions
            WHERE store_id = ? AND item_cd = ? AND category_type = 'dessert'
            ORDER BY judgment_period_end DESC LIMIT 1
        """, (STORE_ID, item_cd))
        row = cursor.fetchone()
        if row:
            return row[0], row[1]
    except:
        pass
    return "N/A", None

def calc_metrics_simple(conn, item_cd, days=7):
    """간단 메트릭 계산"""
    end = REF_DATE
    start = (datetime.strptime(REF_DATE, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    cursor = conn.execute("""
        SELECT COALESCE(SUM(sale_qty),0), COALESCE(SUM(disuse_qty),0), COALESCE(SUM(ord_qty),0)
        FROM daily_sales WHERE item_cd=? AND sales_date>=? AND sales_date<=?
    """, (item_cd, start, end))
    row = cursor.fetchone()
    return row[0], row[1], row[2]

def main():
    common_conn = DBRouter.get_connection(table="products")
    store_conn = DBRouter.get_store_connection(STORE_ID)

    # 디저트 상품 로드
    cursor = common_conn.execute("""
        SELECT p.item_cd, p.item_nm, pd.small_nm, pd.expiration_days, pd.sell_price
        FROM products p
        LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
        WHERE p.mid_cd = '014'
        ORDER BY p.item_nm
    """)
    items = cursor.fetchall()

    print(f"=== 디저트 판단 dry-run: 매장 {STORE_ID}, 기준일 {REF_DATE} ===")
    print(f"총 디저트 상품: {len(items)}개\n")

    # 헤더
    print(f"{'바코드':<16} {'상품명':<30} {'Cat':>3} {'현재판정':<16} {'변경후판정':<16} {'폐기율':>8} {'판매':>4} {'폐기':>4} {'비고'}")
    print("-" * 140)

    reduce_items = []
    stop_items = []
    confirmed_items = []
    keep_items = []
    changed_items = []

    for item_cd, item_nm, small_nm, exp_days, sell_price in items:
        category = classify_dessert_category(small_nm, exp_days)

        # 현재 판정
        cur_decision, cur_action = get_current_decision(store_conn, item_cd)
        cur_display = cur_action if cur_action else cur_decision

        # 간단 메트릭
        sale_qty, disuse_qty, ord_qty = calc_metrics_simple(store_conn, item_cd, days=7 if category == DessertCategory.A else 14 if category == DessertCategory.B else 30)
        waste_rate = calc_waste_rate(sale_qty, disuse_qty)

        # 생애주기 (간단 - first sale date 기반)
        try:
            fc = store_conn.execute("SELECT MIN(sales_date) FROM daily_sales WHERE item_cd=? AND sale_qty>0", (item_cd,))
            frow = fc.fetchone()
            first_date = frow[0] if frow and frow[0] else None
        except:
            first_date = None

        if first_date:
            lifecycle, weeks = determine_lifecycle(first_date, REF_DATE, category)
        else:
            lifecycle = DessertLifecycle.ESTABLISHED
            weeks = 999

        # 새 판단
        from src.prediction.categories.dessert_decision.models import DessertSalesMetrics

        # 주별 판매율 (최근 4주)
        weekly_rates = []
        ref_dt = datetime.strptime(REF_DATE, "%Y-%m-%d")
        for w in range(4):
            we = ref_dt - timedelta(days=7*w)
            ws = we - timedelta(days=7)
            wc = store_conn.execute("""
                SELECT COALESCE(SUM(sale_qty),0), COALESCE(SUM(disuse_qty),0)
                FROM daily_sales WHERE item_cd=? AND sales_date>? AND sales_date<=?
            """, (item_cd, ws.strftime("%Y-%m-%d"), we.strftime("%Y-%m-%d")))
            wr = wc.fetchone()
            ws_qty, wd_qty = wr[0], wr[1]
            total = ws_qty + wd_qty
            weekly_rates.append(round(ws_qty / total, 4) if total > 0 else 0.0)

        # 카테고리 평균 (간단 10으로 가정)
        metrics = DessertSalesMetrics(
            total_sale_qty=sale_qty, total_disuse_qty=disuse_qty,
            total_order_qty=ord_qty,
            sale_amount=sale_qty * (sell_price or 0),
            disuse_amount=disuse_qty * (sell_price or 0),
            sale_rate=round(sale_qty / (sale_qty + disuse_qty), 4) if (sale_qty + disuse_qty) > 0 else 0.0,
            category_avg_sale_qty=10.0,
            prev_period_sale_qty=0,
            sale_trend_pct=0.0,
            weekly_sale_rates=weekly_rates,
            consecutive_zero_months=0,
        )

        new_decision, reason, warning = judge_item(category, lifecycle, metrics)
        new_display = new_decision.value

        # 비고
        note = ""
        if item_cd == "8809692959410":
            note = "★ 생딸기사각케이크"
        elif lifecycle == DessertLifecycle.NEW:
            note = f"NEW({weeks}w)"

        changed = cur_decision != new_display
        if changed:
            note += " [변경]" if note else "[변경]"
            changed_items.append(item_cd)

        waste_str = f"{waste_rate:.0%}" if waste_rate < 999 else "∞"

        print(f"{item_cd:<16} {(item_nm or '')[:28]:<30} {category.value:>3} {cur_display:<16} {new_display:<16} {waste_str:>8} {sale_qty:>4} {disuse_qty:>4} {note}")

        if new_decision == DessertDecisionType.REDUCE_ORDER:
            reduce_items.append((item_cd, item_nm, waste_str))
        elif new_decision == DessertDecisionType.STOP_RECOMMEND:
            stop_items.append((item_cd, item_nm, waste_str))
        elif new_decision == DessertDecisionType.KEEP:
            keep_items.append(item_cd)

    # 30일 무판매 확인 (CONFIRMED_STOP 후보)
    print(f"\n=== 요약 ===")
    print(f"KEEP: {len(keep_items)}개")
    print(f"REDUCE_ORDER: {len(reduce_items)}개")
    print(f"STOP_RECOMMEND: {len(stop_items)}개")
    print(f"판정 변경: {len(changed_items)}개")

    if reduce_items:
        print(f"\n--- REDUCE_ORDER 상품 (폐기율 100~150%) ---")
        for ic, nm, wr in reduce_items:
            print(f"  {ic} {nm} (폐기율 {wr})")

    if stop_items:
        print(f"\n--- STOP_RECOMMEND 상품 ---")
        for ic, nm, wr in stop_items:
            print(f"  {ic} {nm} (폐기율 {wr})")

if __name__ == "__main__":
    main()
