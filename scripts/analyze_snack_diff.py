"""
과자 카테고리 최근 2일간 예측발주 vs 사용자 변경 차이 분석
mid_cd: 015, 016, 017, 018, 019, 020, 029, 030
매장: 46513, 46704
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import sqlite3
from collections import defaultdict

DB_PATH = r"C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\data\order_analysis.db"
SNACK_MIDS = ("015", "016", "017", "018", "019", "020", "029", "030")
STORES = ("46513", "46704")

MID_NAMES = {
    "014": "디저트",
    "015": "스낵",
    "016": "비스킷/쿠키",
    "017": "초콜릿",
    "018": "캔디/젤리",
    "019": "껌/사탕",
    "020": "견과/건과",
    "029": "기타간식",
    "030": "전통과자",
}


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # ============================================================
    # 0. DB에 데이터가 있는지 확인
    # ============================================================
    row = conn.execute("SELECT COUNT(*) as cnt FROM order_snapshots").fetchone()
    print(f"=== order_snapshots 총 레코드: {row['cnt']} ===")

    row = conn.execute("SELECT COUNT(*) as cnt FROM order_diffs").fetchone()
    print(f"=== order_diffs 총 레코드: {row['cnt']} ===")

    row = conn.execute("SELECT COUNT(*) as cnt FROM order_diff_summary").fetchone()
    print(f"=== order_diff_summary 총 레코드: {row['cnt']} ===")

    # ============================================================
    # 1. 최근 날짜 확인
    # ============================================================
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT order_date FROM order_snapshots ORDER BY order_date DESC LIMIT 5"
    ).fetchall()]
    print(f"\n=== 최근 발주일 (최대5): {dates} ===")

    if len(dates) < 1:
        print("데이터가 없습니다.")
        conn.close()
        return

    recent_2_dates = dates[:2]
    print(f"분석 대상 2일: {recent_2_dates}")

    # ============================================================
    # 2. 매장별/날짜별 과자 카테고리 스냅샷 분석
    # ============================================================
    placeholders_mid = ",".join(["?"] * len(SNACK_MIDS))
    placeholders_dates = ",".join(["?"] * len(recent_2_dates))

    for store_id in STORES:
        print(f"\n{'='*80}")
        print(f"  매장: {store_id}")
        print(f"{'='*80}")

        for order_date in recent_2_dates:
            print(f"\n--- [{store_id}] 발주일: {order_date} ---")

            # 2-1. 스냅샷 상세 (자동발주 시스템이 계산한 값)
            snap_rows = conn.execute(f"""
                SELECT item_cd, item_nm, mid_cd,
                       predicted_qty, recommended_qty, final_order_qty,
                       current_stock, pending_qty, eval_decision,
                       order_unit_qty, order_success, confidence,
                       delivery_type, data_days
                FROM order_snapshots
                WHERE store_id = ? AND order_date = ?
                  AND mid_cd IN ({placeholders_mid})
                ORDER BY mid_cd, item_cd
            """, (store_id, order_date, *SNACK_MIDS)).fetchall()

            snap_count = len(snap_rows)
            print(f"  [스냅샷] 과자 카테고리 자동발주 상품 수: {snap_count}")

            if snap_count > 0:
                print(f"  {'상품코드':<12} {'상품명':<25} {'mid':>4} {'예측':>5} {'추천':>5} {'최종발주':>7} {'재고':>5} {'미입고':>5} {'eval':>10} {'배수':>4} {'성공':>4} {'배송':>6}")
                print(f"  {'-'*12} {'-'*25} {'-'*4} {'-'*5} {'-'*5} {'-'*7} {'-'*5} {'-'*5} {'-'*10} {'-'*4} {'-'*4} {'-'*6}")
                total_predicted = 0
                total_recommended = 0
                total_final = 0
                mid_stats = defaultdict(lambda: {"count": 0, "predicted": 0, "final": 0})
                for r in snap_rows:
                    d = dict(r)
                    item_nm_short = (d["item_nm"] or "")[:24]
                    print(f"  {d['item_cd']:<12} {item_nm_short:<25} {d['mid_cd']:>4} "
                          f"{d['predicted_qty']:>5} {d['recommended_qty']:>5} {d['final_order_qty']:>7} "
                          f"{d['current_stock']:>5} {d['pending_qty']:>5} "
                          f"{(d['eval_decision'] or '-'):>10} {d['order_unit_qty']:>4} "
                          f"{d['order_success']:>4} {(d['delivery_type'] or '-'):>6}")
                    total_predicted += d["predicted_qty"] or 0
                    total_recommended += d["recommended_qty"] or 0
                    total_final += d["final_order_qty"] or 0
                    mid = d["mid_cd"] or "?"
                    mid_stats[mid]["count"] += 1
                    mid_stats[mid]["predicted"] += d["predicted_qty"] or 0
                    mid_stats[mid]["final"] += d["final_order_qty"] or 0

                print(f"\n  [소계] 예측합계={total_predicted}, 추천합계={total_recommended}, 최종발주합계={total_final}")
                print(f"  [중분류별 요약]")
                for mid, st in sorted(mid_stats.items()):
                    mn = MID_NAMES.get(mid, mid)
                    print(f"    {mid} ({mn}): {st['count']}개 상품, 예측={st['predicted']}, 최종발주={st['final']}")

            # 2-2. Diff 상세 (변경된 상품)
            diff_rows = conn.execute(f"""
                SELECT item_cd, item_nm, mid_cd, diff_type,
                       auto_order_qty, predicted_qty, eval_decision,
                       confirmed_order_qty, receiving_qty,
                       qty_diff, receiving_diff
                FROM order_diffs
                WHERE store_id = ? AND order_date = ?
                  AND mid_cd IN ({placeholders_mid})
                ORDER BY diff_type, item_cd
            """, (store_id, order_date, *SNACK_MIDS)).fetchall()

            diff_count = len(diff_rows)
            print(f"\n  [Diff] 변경된 과자 상품 수: {diff_count}")

            if diff_count > 0:
                # diff_type별 집계
                type_counts = defaultdict(int)
                type_qty_sum = defaultdict(int)
                for r in diff_rows:
                    d = dict(r)
                    type_counts[d["diff_type"]] += 1
                    type_qty_sum[d["diff_type"]] += d["qty_diff"] or 0

                print(f"  [Diff 타입 분포]")
                for dt in ["qty_changed", "added", "removed", "receiving_diff"]:
                    if type_counts[dt] > 0:
                        print(f"    {dt}: {type_counts[dt]}건 (수량차이 합계: {type_qty_sum[dt]:+d})")

                # 수량변경 상세
                qty_changed = [dict(r) for r in diff_rows if dict(r)["diff_type"] == "qty_changed"]
                if qty_changed:
                    print(f"\n  [수량 변경 상세] ({len(qty_changed)}건)")
                    print(f"  {'상품코드':<12} {'상품명':<25} {'mid':>4} {'자동발주':>7} {'확정발주':>7} {'차이':>5} {'입고':>5} {'방향':>6}")
                    print(f"  {'-'*12} {'-'*25} {'-'*4} {'-'*7} {'-'*7} {'-'*5} {'-'*5} {'-'*6}")
                    for d in qty_changed:
                        item_nm_short = (d["item_nm"] or "")[:24]
                        direction = "증가" if d["qty_diff"] > 0 else "감소"
                        print(f"  {d['item_cd']:<12} {item_nm_short:<25} {d['mid_cd']:>4} "
                              f"{d['auto_order_qty']:>7} {d['confirmed_order_qty']:>7} "
                              f"{d['qty_diff']:>+5} {d['receiving_qty']:>5} {direction:>6}")

                # 추가된 상품 (사용자가 직접 추가)
                added = [dict(r) for r in diff_rows if dict(r)["diff_type"] == "added"]
                if added:
                    print(f"\n  [사용자 추가 상품] ({len(added)}건) -- 시스템이 발주 안 했으나 사용자가 추가")
                    for d in added:
                        item_nm_short = (d["item_nm"] or "")[:24]
                        print(f"    {d['item_cd']} {item_nm_short} (mid={d['mid_cd']}) "
                              f"확정={d['confirmed_order_qty']}, 입고={d['receiving_qty']}")

                # 제거된 상품 (사용자가 삭제)
                removed = [dict(r) for r in diff_rows if dict(r)["diff_type"] == "removed"]
                if removed:
                    print(f"\n  [사용자 제거 상품] ({len(removed)}건) -- 시스템이 발주했으나 사용자가 삭제")
                    for d in removed:
                        item_nm_short = (d["item_nm"] or "")[:24]
                        print(f"    {d['item_cd']} {item_nm_short} (mid={d['mid_cd']}) "
                              f"자동발주={d['auto_order_qty']}개 -> 삭제됨")

                # 입고차이 (발주 OK 했으나 입고가 다른 경우)
                recv_diff = [dict(r) for r in diff_rows if dict(r)["diff_type"] == "receiving_diff"]
                if recv_diff:
                    print(f"\n  [입고 차이] ({len(recv_diff)}건) -- 발주=확정 이지만 입고가 다름")
                    for d in recv_diff:
                        item_nm_short = (d["item_nm"] or "")[:24]
                        print(f"    {d['item_cd']} {item_nm_short} "
                              f"발주={d['auto_order_qty']}, 입고={d['receiving_qty']} "
                              f"(차이={d['receiving_diff']:+d})")

    # ============================================================
    # 3. 전체 요약 (order_diff_summary)
    # ============================================================
    print(f"\n{'='*80}")
    print(f"  전체 일별 요약 (order_diff_summary)")
    print(f"{'='*80}")

    for store_id in STORES:
        for order_date in recent_2_dates:
            summary = conn.execute("""
                SELECT * FROM order_diff_summary
                WHERE store_id = ? AND order_date = ?
            """, (store_id, order_date)).fetchone()

            if summary:
                s = dict(summary)
                print(f"\n  [{store_id}] {order_date}:")
                print(f"    자동발주 상품수: {s['total_auto_items']}, 확정 상품수: {s['total_confirmed_items']}")
                print(f"    변경없음: {s['items_unchanged']}, 수량변경: {s['items_qty_changed']}, "
                      f"추가: {s['items_added']}, 제거: {s['items_removed']}, "
                      f"비교불가: {s['items_not_comparable']}")
                print(f"    자동발주 총수량: {s['total_auto_qty']}, 확정 총수량: {s['total_confirmed_qty']}, "
                      f"입고 총수량: {s['total_receiving_qty']}")
                print(f"    일치율: {s['match_rate']:.1%}")
                print(f"    입고데이터 유무: {'있음' if s['has_receiving_data'] else '없음'}")
            else:
                print(f"\n  [{store_id}] {order_date}: 요약 데이터 없음")

    # ============================================================
    # 4. 과자 카테고리 vs 전체: 사용자 수정 비율 비교
    # ============================================================
    print(f"\n{'='*80}")
    print(f"  과자 카테고리 vs 전체: 변경 비율 비교")
    print(f"{'='*80}")

    for store_id in STORES:
        for order_date in recent_2_dates:
            # 전체 스냅샷 수
            total_all = conn.execute("""
                SELECT COUNT(*) FROM order_snapshots
                WHERE store_id = ? AND order_date = ?
            """, (store_id, order_date)).fetchone()[0]

            # 과자 스냅샷 수
            total_snack = conn.execute(f"""
                SELECT COUNT(*) FROM order_snapshots
                WHERE store_id = ? AND order_date = ?
                  AND mid_cd IN ({placeholders_mid})
            """, (store_id, order_date, *SNACK_MIDS)).fetchone()[0]

            # 전체 diff 수
            diff_all = conn.execute("""
                SELECT COUNT(*) FROM order_diffs
                WHERE store_id = ? AND order_date = ?
                  AND diff_type IN ('qty_changed', 'added', 'removed')
            """, (store_id, order_date)).fetchone()[0]

            # 과자 diff 수
            diff_snack = conn.execute(f"""
                SELECT COUNT(*) FROM order_diffs
                WHERE store_id = ? AND order_date = ?
                  AND mid_cd IN ({placeholders_mid})
                  AND diff_type IN ('qty_changed', 'added', 'removed')
            """, (store_id, order_date, *SNACK_MIDS)).fetchone()[0]

            total_ratio = (diff_all / total_all * 100) if total_all > 0 else 0
            snack_ratio = (diff_snack / total_snack * 100) if total_snack > 0 else 0

            print(f"\n  [{store_id}] {order_date}:")
            print(f"    전체: 스냅샷 {total_all}건, 변경 {diff_all}건 ({total_ratio:.1f}%)")
            print(f"    과자: 스냅샷 {total_snack}건, 변경 {diff_snack}건 ({snack_ratio:.1f}%)")
            if total_ratio > 0:
                print(f"    과자 변경비율/전체 변경비율 = {snack_ratio/total_ratio:.2f}x" if total_ratio > 0 else "")

    # ============================================================
    # 5. 과자 카테고리에서 수량이 증가/감소된 통계
    # ============================================================
    print(f"\n{'='*80}")
    print(f"  과자 카테고리 수량 변경 방향 분석 (qty_changed만)")
    print(f"{'='*80}")

    for store_id in STORES:
        for order_date in recent_2_dates:
            rows = conn.execute(f"""
                SELECT qty_diff, auto_order_qty, confirmed_order_qty
                FROM order_diffs
                WHERE store_id = ? AND order_date = ?
                  AND mid_cd IN ({placeholders_mid})
                  AND diff_type = 'qty_changed'
            """, (store_id, order_date, *SNACK_MIDS)).fetchall()

            if not rows:
                print(f"\n  [{store_id}] {order_date}: 수량변경 없음")
                continue

            increased = [r for r in rows if r["qty_diff"] > 0]
            decreased = [r for r in rows if r["qty_diff"] < 0]
            inc_sum = sum(r["qty_diff"] for r in increased)
            dec_sum = sum(r["qty_diff"] for r in decreased)

            print(f"\n  [{store_id}] {order_date}:")
            print(f"    증가: {len(increased)}건 (총 +{inc_sum}개)")
            print(f"    감소: {len(decreased)}건 (총 {dec_sum}개)")
            print(f"    순변경: {inc_sum + dec_sum:+d}개")

    conn.close()
    print(f"\n{'='*80}")
    print("분석 완료.")


if __name__ == "__main__":
    main()
