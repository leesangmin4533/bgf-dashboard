#!/usr/bin/env python3
"""
캡 선별 정렬 비교 — 현재(data_days) vs 소진율(안 B) 차이 분석

현재 classify_items의 정렬 기준(data_days 내림차순)과
안 B(data_days 우선 + 소진율 tiebreaker)의 선별 결과를 비교합니다.

Usage:
    python scripts/compare_cap_sorting.py                    # 전매장
    python scripts/compare_cap_sorting.py --store 46513      # 특정 매장
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def compare_for_store(store_id: str):
    """매장별 캡 선별 비교"""
    import sqlite3
    from src.infrastructure.database.connection import DBRouter
    from src.infrastructure.database.repos.food_popularity_curve_repo import (
        FoodPopularityCurveRepository,
    )
    from src.settings.constants import DEPLETION_CURVE_MIN_SAMPLE_DAYS

    print(f"\n{'='*70}")
    print(f"  매장 {store_id} — 캡 선별 정렬 비교")
    print(f"{'='*70}")

    # 소진율 데이터 현황
    curve_repo = FoodPopularityCurveRepository(store_id=store_id)
    conn = DBRouter.get_store_connection(store_id)
    try:
        stats = conn.execute("""
            SELECT COUNT(DISTINCT item_cd),
                   SUM(CASE WHEN sample_count >= 30 THEN 1 ELSE 0 END),
                   MAX(sample_count),
                   ROUND(AVG(sample_count), 1)
            FROM food_popularity_curve
            WHERE elapsed_hours IN (16, 24)
        """).fetchone()
        print(f"\n  소진율 데이터: {stats[0]}상품, "
              f"sample≥30: {stats[1]}개, "
              f"max={stats[2]}, avg={stats[3]}")

        if not stats[1] or stats[1] == 0:
            print(f"  ⚠️ sample≥30인 상품 없음 → 비교 불가 (bootstrap 필요)")
            return

        # 푸드 카테고리별 비교
        food_mids = ['001', '002', '003', '004', '005', '012']
        mid_names = {'001': '도시락', '002': '주먹밥', '003': '김밥',
                     '004': '샌드위치', '005': '햄버거', '012': '빵'}

        conn.execute(f"ATTACH DATABASE '{DBRouter.get_common_db_path()}' AS common")

        for mid_cd in food_mids:
            # 최근 발주 후보 상품 목록 (최근 7일 발주 기록 있는 상품)
            items = conn.execute("""
                SELECT ot.item_cd, ot.item_nm,
                       COUNT(DISTINCT ot.order_date) as data_days,
                       SUM(ot.order_qty) as total_ordered
                FROM order_tracking ot
                WHERE ot.mid_cd = ?
                  AND ot.order_date >= date('now', '-14 days')
                  AND ot.order_qty > 0
                GROUP BY ot.item_cd
                HAVING data_days >= 1
                ORDER BY data_days DESC
            """, (mid_cd,)).fetchall()

            if len(items) < 3:
                continue

            # 소진율 조회
            item_rates = {}
            for item_cd, _, _, _ in items:
                rate, sample = curve_repo.get_final_rate(item_cd, "1차")
                if rate is None:
                    rate, sample = curve_repo.get_final_rate(item_cd, "2차")
                item_rates[item_cd] = (rate, sample or 0)

            # 정렬 A: 현재 (data_days 내림차순)
            sorted_current = sorted(items, key=lambda x: -x[2])

            # 정렬 B: 안 B (data_days 우선 + 소진율 tiebreaker)
            sorted_b = sorted(items, key=lambda x: (
                -x[2],
                -(item_rates.get(x[0], (0, 0))[0] or 0)
            ))

            # 차이 분석
            current_order = [r[0] for r in sorted_current]
            b_order = [r[0] for r in sorted_b]

            # 캡 적용 (상위 N개만)
            cap = min(13, len(items))  # exploit 슬롯 가정
            current_set = set(current_order[:cap])
            b_set = set(b_order[:cap])

            added = b_set - current_set
            removed = current_set - b_set

            if not added and not removed:
                continue

            print(f"\n  ■ {mid_names.get(mid_cd, mid_cd)} "
                  f"(후보 {len(items)}개, 캡 {cap}개)")

            if removed:
                print(f"    탈락 ({len(removed)}개):")
                for item_cd in removed:
                    nm = next((r[1] for r in items if r[0] == item_cd), '?')
                    dd = next((r[2] for r in items if r[0] == item_cd), 0)
                    rate, sample = item_rates.get(item_cd, (None, 0))
                    rate_s = f'{rate:.3f}' if rate else 'N/A'
                    nm_s = (nm[:20] + '..') if nm and len(nm) > 22 else (nm or '?')
                    print(f"      - {nm_s:>22} "
                          f"data={dd}, rate={rate_s}, sample={sample}")

            if added:
                print(f"    진입 ({len(added)}개):")
                for item_cd in added:
                    nm = next((r[1] for r in items if r[0] == item_cd), '?')
                    dd = next((r[2] for r in items if r[0] == item_cd), 0)
                    rate, sample = item_rates.get(item_cd, (None, 0))
                    rate_s = f'{rate:.3f}' if rate else 'N/A'
                    nm_s = (nm[:20] + '..') if nm and len(nm) > 22 else (nm or '?')
                    print(f"      + {nm_s:>22} "
                          f"data={dd}, rate={rate_s}, sample={sample}")

    finally:
        conn.close()


def main():
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    parser = argparse.ArgumentParser(description="캡 선별 정렬 비교")
    parser.add_argument("--store", type=str, help="특정 매장")
    args = parser.parse_args()

    print("캡 선별 정렬 비교: 현재(data_days) vs 안B(data_days+소진율)")
    print("안 B: data_days 동률 내에서 소진율 높은 상품 우선")

    if args.store:
        store_ids = [args.store]
    else:
        store_ids = ['46513', '46704', '47863']

    for sid in store_ids:
        try:
            compare_for_store(sid)
        except Exception as e:
            print(f"\n  매장 {sid} 오류: {e}")

    print(f"\n{'='*70}")
    print("  비교 완료")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
