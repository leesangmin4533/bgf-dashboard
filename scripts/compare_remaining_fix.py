"""
remaining_qty 보정 효과 비교 스크립트

3/30(보정 전) vs 3/31(보정 후) 발주 건수 및 pending 차단율 비교.

Usage:
    python scripts/compare_remaining_fix.py
    python scripts/compare_remaining_fix.py --date 2026-03-31
"""
import sqlite3
import sys
import os
import json
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(str(PROJECT_ROOT))
sys.stdout.reconfigure(encoding='utf-8')


def load_baseline():
    path = PROJECT_ROOT / "data" / "snapshots" / "remaining_fix_baseline.json"
    if not path.exists():
        print("baseline 없음. 먼저 baseline을 저장하세요.")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_stats(store_id: str, target_date: str):
    db_path = PROJECT_ROOT / "data" / "stores" / f"{store_id}.db"
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # 발주 건수
    c.execute("SELECT COUNT(*) FROM order_tracking WHERE store_id=? AND order_date=?",
              (store_id, target_date))
    order_count = c.fetchone()[0]

    # pending_source별 차단율
    c.execute("""
        SELECT pending_source, COUNT(*) as total,
               SUM(CASE WHEN order_qty > 0 THEN 1 ELSE 0 END) as ordered,
               SUM(CASE WHEN order_qty = 0 THEN 1 ELSE 0 END) as blocked
        FROM prediction_logs
        WHERE store_id=? AND prediction_date=?
        GROUP BY pending_source
    """, (store_id, target_date))
    pending_stats = {}
    for r in c.fetchall():
        src = r['pending_source'] or 'NULL'
        pending_stats[src] = {'total': r['total'], 'ordered': r['ordered'], 'blocked': r['blocked']}

    conn.close()
    return {'order_count': order_count, 'pending_stats': pending_stats}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-03-31", help="비교 대상 날짜")
    args = parser.parse_args()

    baseline = load_baseline()
    stores = ['46513', '46704', '47863', '49965']

    print(f"{'='*60}")
    print(f"  remaining_qty 보정 효과 비교")
    print(f"  기준일: 3/30 (보정 전) vs {args.date} (보정 후)")
    print(f"{'='*60}")

    for store_id in stores:
        base = baseline.get(store_id, {})
        current = get_stats(store_id, args.date)
        if not current:
            continue

        base_count = base.get('order_count', 0)
        cur_count = current['order_count']
        diff = cur_count - base_count

        # ot_fill 차단율
        base_ot = base.get('pending_stats', {}).get('ot_fill', {'total': 0, 'blocked': 0})
        cur_ot = current['pending_stats'].get('ot_fill', {'total': 0, 'blocked': 0})
        base_rate = base_ot['blocked'] / base_ot['total'] * 100 if base_ot['total'] > 0 else 0
        cur_rate = cur_ot['blocked'] / cur_ot['total'] * 100 if cur_ot['total'] > 0 else 0

        print(f"\n[{store_id}]")
        print(f"  발주 건수: {base_count} → {cur_count} ({'+' if diff >= 0 else ''}{diff})")
        print(f"  ot_fill 차단율: {base_rate:.1f}% → {cur_rate:.1f}%")
        print(f"  ot_fill 상세: {base_ot['blocked']}/{base_ot['total']} → {cur_ot['blocked']}/{cur_ot['total']}")

    if current and current['order_count'] == 0:
        print(f"\n⚠️ {args.date} 발주 데이터 없음 — 아직 실행 전일 수 있습니다.")


if __name__ == "__main__":
    main()
