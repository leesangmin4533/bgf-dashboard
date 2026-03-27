"""
예측 모델 실행 결과 vs DB 평균값 비교 테스트
- 실제 DB에서 샘플 상품을 선택
- ImprovedPredictor로 예측 실행
- DB의 7일/14일/60일 평균 판매량과 비교
- 수요 패턴별(daily/frequent/intermittent/slow) 분류 확인
"""

import sys
import io
from pathlib import Path

# Windows UTF-8
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer"):
            setattr(sys, _sn, io.TextIOWrapper(
                _s.buffer, encoding="utf-8", errors="replace", line_buffering=True
            ))

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import sqlite3
from datetime import datetime, timedelta
from src.settings.constants import DEFAULT_STORE_ID
from src.infrastructure.database.connection import DBRouter


def get_sample_products(store_id: str, limit_per_category: int = 3):
    """카테고리별 샘플 상품 선택 (최근 7일 판매 있는 상품)"""
    conn = DBRouter.get_store_connection(store_id)
    common_path = DBRouter.get_common_db_path()
    conn.execute(f"ATTACH DATABASE '{common_path}' AS common_db")
    conn.execute("CREATE TEMP VIEW IF NOT EXISTS products AS SELECT * FROM common_db.products")
    conn.execute("CREATE TEMP VIEW IF NOT EXISTS product_details AS SELECT * FROM common_db.product_details")

    cursor = conn.cursor()
    # demand_pattern 컬럼 존재 여부 확인
    has_demand_pattern = False
    try:
        cursor.execute("PRAGMA common_db.table_info(product_details)")
        cols = [r[1] for r in cursor.fetchall()]
        has_demand_pattern = "demand_pattern" in cols
    except Exception:
        pass

    dp_col = "pd.demand_pattern," if has_demand_pattern else "NULL as demand_pattern,"

    cursor.execute(f"""
        SELECT
            ds.item_cd,
            p.item_nm,
            p.mid_cd,
            {dp_col}
            ROUND(AVG(ds.sale_qty), 2) as avg_7d,
            SUM(ds.sale_qty) as total_7d,
            COUNT(*) as days_count
        FROM daily_sales ds
        JOIN products p ON ds.item_cd = p.item_cd
        LEFT JOIN product_details pd ON ds.item_cd = pd.item_cd
        WHERE ds.store_id = ?
        AND ds.sales_date >= date('now', '-7 days')
        AND ds.sale_qty > 0
        GROUP BY ds.item_cd
        ORDER BY p.mid_cd, avg_7d DESC
    """, (store_id,))

    rows = cursor.fetchall()
    conn.close()

    # 카테고리별 상위 N개씩 선택
    by_mid = {}
    for row in rows:
        mid = row[2]
        if mid not in by_mid:
            by_mid[mid] = []
        if len(by_mid[mid]) < limit_per_category:
            by_mid[mid].append({
                "item_cd": row[0],
                "item_nm": row[1],
                "mid_cd": row[2],
                "demand_pattern": row[3] or "N/A",
                "avg_7d": row[4],
                "total_7d": row[5],
                "days_count": row[6],
            })
    return by_mid


def get_db_averages(store_id: str, item_cd: str):
    """DB에서 7일/14일/60일 평균 판매량 조회

    avg = 전체일 평균 (0일 포함, 예측기와 동일 기준)
    avg_sell_only = 판매일만 평균 (참고용)
    """
    conn = DBRouter.get_store_connection(store_id)
    cursor = conn.cursor()

    results = {}
    for days, label in [(7, "avg_7d"), (14, "avg_14d"), (60, "avg_60d")]:
        cursor.execute("""
            SELECT
                ROUND(AVG(sale_qty), 3) as avg_sale,
                SUM(sale_qty) as total_sale,
                COUNT(*) as record_count,
                SUM(CASE WHEN sale_qty > 0 THEN 1 ELSE 0 END) as sell_days,
                ROUND(CAST(SUM(sale_qty) AS FLOAT) /
                    NULLIF(SUM(CASE WHEN sale_qty > 0 THEN 1 ELSE 0 END), 0), 3) as avg_sell_only
            FROM daily_sales
            WHERE item_cd = ? AND store_id = ?
            AND sales_date >= date('now', '-' || ? || ' days')
        """, (item_cd, store_id, days))
        row = cursor.fetchone()
        results[label] = {
            "avg": row[0] or 0.0,
            "total": row[1] or 0,
            "records": row[2] or 0,
            "sell_days": row[3] or 0,
            "avg_sell_only": row[4] or 0.0,
        }

    # 달력 기준 7일 평균 (레코드 없는 날도 0으로 간주)
    cursor.execute("""
        SELECT SUM(sale_qty)
        FROM daily_sales
        WHERE item_cd = ? AND store_id = ?
        AND sales_date >= date('now', '-7 days')
    """, (item_cd, store_id))
    total_7d = cursor.fetchone()[0] or 0
    results["calendar_avg_7d"] = total_7d / 7.0

    conn.close()
    return results


def run_prediction_test(store_id: str = DEFAULT_STORE_ID, max_items: int = 30):
    """예측 실행 및 DB 평균 비교"""
    from src.prediction.improved_predictor import ImprovedPredictor

    print("\n" + "=" * 90)
    print("   예측 모델 vs DB 평균값 비교 테스트")
    print(f"   Store: {store_id} | Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 90)

    # 1. 예측기 초기화
    print("\n[1] ImprovedPredictor 초기화...")
    predictor = ImprovedPredictor(store_id=store_id)
    predictor.load_inventory_to_cache()
    print("    OK")

    # 2. 샘플 상품 선택
    print("\n[2] 샘플 상품 선택 (카테고리별 최대 3개)...")
    by_mid = get_sample_products(store_id, limit_per_category=3)
    all_items = []
    for mid, items in sorted(by_mid.items()):
        all_items.extend(items)

    if len(all_items) > max_items:
        all_items = all_items[:max_items]
    print(f"    {len(by_mid)}개 카테고리, {len(all_items)}개 상품 선택")

    # 3. 예측 실행 + DB 비교
    print("\n[3] 예측 실행 및 비교...")
    target_date = datetime.now() + timedelta(days=1)

    results = []
    pattern_stats = {"daily": [], "frequent": [], "intermittent": [], "slow": [], "N/A": []}

    for item in all_items:
        item_cd = item["item_cd"]

        # 예측 실행
        pred = predictor.predict(item_cd, target_date=target_date)
        if pred is None:
            continue

        # DB 평균 조회
        db_avgs = get_db_averages(store_id, item_cd)

        # 비교
        predicted_base = pred.predicted_qty  # WMA/Croston base
        adjusted = pred.adjusted_qty         # 계수 적용 후
        order_qty = pred.order_qty           # 최종 발주량
        db_avg_7d = db_avgs["avg_7d"]["avg"]  # 레코드 평균 (판매 0일도 레코드 있으면 포함)
        db_avg_14d = db_avgs["avg_14d"]["avg"]
        cal_avg_7d = db_avgs["calendar_avg_7d"]  # 달력 7일 기준 (total/7)
        sell_only_7d = db_avgs["avg_7d"]["avg_sell_only"]  # 판매일만 평균
        sell_ratio_7d = (db_avgs["avg_7d"]["sell_days"] / db_avgs["avg_7d"]["records"]
                         if db_avgs["avg_7d"]["records"] > 0 else 0)

        # 차이율 (달력 7일 평균 대비 — 예측기의 WMA와 동일한 기준)
        if cal_avg_7d > 0:
            diff_pct = ((adjusted - cal_avg_7d) / cal_avg_7d) * 100
        else:
            diff_pct = 0.0 if adjusted == 0 else 100.0

        # WMA base vs 달력 평균 차이 (계수 적용 전)
        if cal_avg_7d > 0:
            base_diff_pct = ((predicted_base - cal_avg_7d) / cal_avg_7d) * 100
        else:
            base_diff_pct = 0.0 if predicted_base == 0 else 100.0

        pattern = pred.demand_pattern or item["demand_pattern"]
        entry = {
            "item_cd": item_cd,
            "item_nm": item["item_nm"][:12],
            "mid_cd": item["mid_cd"],
            "pattern": pattern,
            "db_avg_7d": db_avg_7d,
            "cal_avg_7d": cal_avg_7d,
            "sell_only_7d": sell_only_7d,
            "db_avg_14d": db_avg_14d,
            "predicted_base": predicted_base,
            "adjusted": adjusted,
            "order_qty": order_qty,
            "stock": pred.current_stock,
            "pending": pred.pending_qty,
            "safety": pred.safety_stock,
            "diff_pct": diff_pct,
            "base_diff_pct": base_diff_pct,
            "sell_ratio_7d": sell_ratio_7d,
            "model_type": pred.model_type,
        }
        results.append(entry)

        p_key = pattern if pattern in pattern_stats else "N/A"
        pattern_stats[p_key].append(entry)

    # 4. 결과 출력
    print("\n" + "=" * 90)
    print("   [결과] 예측값 vs DB 7일 평균 비교")
    print("=" * 90)

    # 헤더
    print(f"\n{'상품명':<14} {'mid':>3} {'패턴':<12} "
          f"{'Cal7d':>6} {'Sell7d':>6} {'WMA':>6} {'Adj':>6} {'발주':>4} "
          f"{'재고':>4} {'미입':>4} {'안전':>5} {'WMA%':>7} {'Adj%':>7} {'모델':>6}")
    print("-" * 130)

    current_mid = None
    for r in sorted(results, key=lambda x: (x["mid_cd"], -x["cal_avg_7d"])):
        if r["mid_cd"] != current_mid:
            if current_mid is not None:
                print("-" * 130)
            current_mid = r["mid_cd"]

        # 차이율 표시 (Adj vs Calendar avg)
        diff_str = f"{r['diff_pct']:>+6.1f}%"
        base_diff_str = f"{r['base_diff_pct']:>+6.1f}%"

        print(f"{r['item_nm']:<14} {r['mid_cd']:>3} {r['pattern']:<12} "
              f"{r['cal_avg_7d']:>6.2f} {r['sell_only_7d']:>6.2f} "
              f"{r['predicted_base']:>6.2f} {r['adjusted']:>6.2f} {r['order_qty']:>4} "
              f"{r['stock']:>4} {r['pending']:>4} {r['safety']:>5.1f} "
              f"{base_diff_str:>8} {diff_str:>8} {r['model_type']:>6}")

    # 5. 패턴별 통계
    print("\n" + "=" * 90)
    print("   [통계] 수요 패턴별 요약")
    print("=" * 90)
    print(f"\n{'패턴':<14} {'상품수':>5} {'Cal7d_avg':>10} {'WMA_avg':>10} {'Adj_avg':>10} {'WMA%_avg':>10} {'Adj%_avg':>10}")
    print("-" * 80)

    for pattern in ["daily", "frequent", "intermittent", "slow", "N/A"]:
        items = pattern_stats[pattern]
        if not items:
            continue
        n = len(items)
        avg_cal = sum(i["cal_avg_7d"] for i in items) / n
        avg_wma = sum(i["predicted_base"] for i in items) / n
        avg_adj = sum(i["adjusted"] for i in items) / n
        base_diffs = sorted([i["base_diff_pct"] for i in items])
        adj_diffs = sorted([i["diff_pct"] for i in items])
        avg_base_diff = sum(base_diffs) / n
        avg_adj_diff = sum(adj_diffs) / n

        print(f"{pattern:<14} {n:>5} {avg_cal:>10.2f} {avg_wma:>10.2f} {avg_adj:>10.2f} "
              f"{avg_base_diff:>+9.1f}% {avg_adj_diff:>+9.1f}%")

    # 6. 전체 통계
    print("\n" + "=" * 90)
    print("   [전체 통계]")
    print("=" * 90)

    if results:
        all_diffs = [r["diff_pct"] for r in results]
        all_diffs_sorted = sorted(all_diffs)
        n = len(all_diffs)

        over_30 = sum(1 for d in all_diffs if d > 30)
        under_30 = sum(1 for d in all_diffs if d < -30)
        within_30 = n - over_30 - under_30

        abs_diffs = [abs(d) for d in all_diffs]
        mape = sum(abs_diffs) / n

        print(f"  총 상품수: {n}")
        print(f"  평균 차이: {sum(all_diffs)/n:+.1f}%")
        print(f"  중앙값 차이: {all_diffs_sorted[n//2]:+.1f}%")
        print(f"  MAPE (평균 절대 차이): {mape:.1f}%")
        print(f"  +30% 이상 (과잉): {over_30}개 ({over_30/n*100:.0f}%)")
        print(f"  -30% 이하 (과소): {under_30}개 ({under_30/n*100:.0f}%)")
        print(f"  +/-30% 이내 (적정): {within_30}개 ({within_30/n*100:.0f}%)")

        # 과잉 TOP 5
        print(f"\n  --- 과잉예측 TOP 5 (Adj > Cal7d) ---")
        for r in sorted(results, key=lambda x: -x["diff_pct"])[:5]:
            print(f"  {r['item_nm']:<14} mid={r['mid_cd']} "
                  f"Cal7d={r['cal_avg_7d']:.2f} WMA={r['predicted_base']:.2f} "
                  f"Adj={r['adjusted']:.2f} adj_diff={r['diff_pct']:+.1f}% "
                  f"pattern={r['pattern']}")

        # 과소 TOP 5
        print(f"\n  --- 과소예측 TOP 5 (Adj < Cal7d) ---")
        for r in sorted(results, key=lambda x: x["diff_pct"])[:5]:
            print(f"  {r['item_nm']:<14} mid={r['mid_cd']} "
                  f"Cal7d={r['cal_avg_7d']:.2f} WMA={r['predicted_base']:.2f} "
                  f"Adj={r['adjusted']:.2f} adj_diff={r['diff_pct']:+.1f}% "
                  f"pattern={r['pattern']}")

    print("\n" + "=" * 90)
    print("   테스트 완료")
    print("=" * 90)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="예측 모델 vs DB 평균 비교 테스트")
    parser.add_argument("--store", default=DEFAULT_STORE_ID, help="매장 코드")
    parser.add_argument("--max-items", type=int, default=50, help="최대 테스트 상품 수")
    args = parser.parse_args()

    run_prediction_test(store_id=args.store, max_items=args.max_items)
