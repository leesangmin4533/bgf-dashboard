"""
DemandClassifier 변경 영향 확인 스크립트

demand_classifier.py 수정 전후로 실행하여 분류 결과 변화를 감지.
DEMAND_PATTERN_EXEMPT_MIDS, sell_days 쿼리, 임계값 변경 시 필수 실행.

Usage:
    python scripts/impact_check.py                    # 46513 기준
    python scripts/impact_check.py --store 46704      # 특정 매장
    python scripts/impact_check.py --all              # 전 매장
"""

import sys
import os
import argparse
from pathlib import Path

# 프로젝트 루트 설정
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(str(PROJECT_ROOT))


def get_current_classifications(store_id: str) -> dict:
    """현재 코드 기준으로 전 상품 수요 패턴 분류 실행"""
    import sqlite3

    conn = sqlite3.connect(f"data/stores/{store_id}.db", timeout=10)
    conn.execute("ATTACH DATABASE 'data/common.db' AS common_db")

    # 면제 목록 로드
    from src.prediction.demand_classifier import DEMAND_PATTERN_EXEMPT_MIDS

    # 전 상품의 sell_stats 조회
    rows = conn.execute("""
        SELECT
            ds.item_cd, p.item_nm, p.mid_cd,
            COUNT(*) as total_days,
            SUM(CASE WHEN ds.stock_qty > 0 THEN 1 ELSE 0 END) as available_days,
            SUM(CASE WHEN ds.stock_qty > 0 AND ds.sale_qty > 0
                THEN 1 ELSE 0 END) as sell_days,
            SUM(ds.sale_qty) as total_sale
        FROM daily_sales ds
        JOIN common_db.products p ON ds.item_cd = p.item_cd
        WHERE ds.sales_date >= date('now', '-60 days')
        GROUP BY ds.item_cd
        HAVING total_days >= 14
    """).fetchall()
    conn.close()

    results = {}
    for r in rows:
        item_cd, item_nm, mid_cd = r[0], r[1], r[2]
        total, avail, sell, total_sale = r[3], r[4], r[5], r[6]

        # 면제 상품
        if mid_cd in DEMAND_PATTERN_EXEMPT_MIDS:
            pattern = "daily"
            source = "exempt"
        elif avail == 0:
            pattern = "slow"
            source = "no_stock"
        else:
            ratio = sell / avail
            if ratio >= 0.70:
                pattern = "daily"
            elif ratio >= 0.40:
                pattern = "frequent"
            elif ratio >= 0.15:
                pattern = "intermittent"
            else:
                pattern = "slow"
            source = f"ratio={ratio:.2f}"

        results[item_cd] = {
            "name": item_nm,
            "mid_cd": mid_cd,
            "pattern": pattern,
            "source": source,
            "total": total,
            "avail": avail,
            "sell": sell,
            "total_sale": total_sale or 0,
        }

    return results


def load_snapshot(store_id: str) -> dict:
    """이전 스냅샷 로드"""
    import json
    snapshot_path = PROJECT_ROOT / "data" / "snapshots" / f"demand_pattern_{store_id}.json"
    if snapshot_path.exists():
        with open(snapshot_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_snapshot(store_id: str, results: dict):
    """현재 분류 결과를 스냅샷으로 저장"""
    import json
    snapshot_dir = PROJECT_ROOT / "data" / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / f"demand_pattern_{store_id}.json"

    # 저장할 데이터 축소 (item_cd → pattern만)
    slim = {k: v["pattern"] for k, v in results.items()}
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, indent=2)
    print(f"  스냅샷 저장: {snapshot_path}")


def compare_and_report(store_id: str, current: dict, previous: dict) -> int:
    """현재 vs 이전 비교, 변경사항 리포트"""
    changes = []
    for item_cd, cur in current.items():
        prev_pattern = previous.get(item_cd)
        if prev_pattern and prev_pattern != cur["pattern"]:
            changes.append({
                "item_cd": item_cd,
                "name": cur["name"],
                "mid_cd": cur["mid_cd"],
                "old": prev_pattern,
                "new": cur["pattern"],
                "source": cur["source"],
                "sale": cur["total_sale"],
            })

    # 패턴별 집계
    pattern_counts = {}
    for item in current.values():
        p = item["pattern"]
        pattern_counts[p] = pattern_counts.get(p, 0) + 1

    print(f"\n=== {store_id} 분류 현황 ===")
    for p in ["daily", "frequent", "intermittent", "slow"]:
        cnt = pattern_counts.get(p, 0)
        bar = "█" * min(cnt // 5, 40)
        print(f"  {p:<13}: {cnt:>4}개 {bar}")
    print(f"  합계: {len(current)}개 상품")

    if not previous:
        print(f"\n  첫 실행 — 스냅샷 저장됨 (다음 실행 시 비교 가능)")
        return 0

    if not changes:
        print(f"\n  ✅ 분류 변경 없음 (이전 스냅샷 대비)")
        return 0

    # 변경사항 보고
    print(f"\n  ⚠️ 분류 변경 {len(changes)}건 감지!")
    print(f"  {'상품명':<20} | {'mid':>4} | {'이전':>10} → {'현재':>10} | {'판매':>5} | {'근거':>12}")
    print("  " + "-" * 75)

    critical = 0
    for c in sorted(changes, key=lambda x: x["sale"], reverse=True)[:20]:
        # 판매 있는 상품의 STOP화는 critical
        is_critical = c["sale"] > 0 and c["new"] == "slow"
        marker = "🔴" if is_critical else "🟡"
        if is_critical:
            critical += 1
        print(
            f"  {marker} {c['name'][:18]:<18} | {c['mid_cd']:>4} | "
            f"{c['old']:>10} → {c['new']:>10} | {c['sale']:>5} | {c['source']:>12}"
        )

    if critical > 0:
        print(f"\n  🔴 Critical: 판매 실적 있는 상품 {critical}개가 slow로 변경됨!")
        print(f"     → 이 변경이 의도적인지 확인하세요.")
        return 1

    return 0


def main():
    parser = argparse.ArgumentParser(description="DemandClassifier 변경 영향 확인")
    parser.add_argument("--store", default="46513", help="매장 코드")
    parser.add_argument("--all", action="store_true", help="전 매장 실행")
    parser.add_argument("--save", action="store_true", default=True,
                        help="스냅샷 저장 (기본: True)")
    parser.add_argument("--no-save", action="store_true", help="스냅샷 저장 안 함")
    args = parser.parse_args()

    stores = ["46513", "46704", "47863", "49965"] if args.all else [args.store]
    exit_code = 0

    for sid in stores:
        print(f"\n{'=' * 60}")
        print(f"  Impact Check: {sid}")
        print(f"{'=' * 60}")

        try:
            current = get_current_classifications(sid)
            previous = load_snapshot(sid)
            result = compare_and_report(sid, current, previous)

            if result != 0:
                exit_code = 1

            if args.save and not args.no_save:
                save_snapshot(sid, current)

        except Exception as e:
            print(f"  ❌ 에러: {e}")
            exit_code = 1

    if exit_code == 0:
        print(f"\n✅ 전체 OK — 안전하게 수정 가능")
    else:
        print(f"\n⚠️ 변경 감지됨 — 의도한 변경인지 확인하세요")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
