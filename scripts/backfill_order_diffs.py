"""
발주 차이 분석 — 과거 데이터 백필 스크립트

운영 DB(eval_outcomes + order_tracking + receiving_history)에서
과거 발주 스냅샷을 재구성하고 입고 데이터와 비교하여
order_analysis.db에 diff 데이터를 소급 생성한다.

Usage:
    python scripts/backfill_order_diffs.py                      # 전체 기간, 전 매장
    python scripts/backfill_order_diffs.py --store 46513        # 특정 매장
    python scripts/backfill_order_diffs.py --days 7             # 최근 7일
    python scripts/backfill_order_diffs.py --start 2026-02-01 --end 2026-02-15
    python scripts/backfill_order_diffs.py --date 2026-02-15    # 특정 날짜
    python scripts/backfill_order_diffs.py --info               # 날짜 범위만 확인
"""

import argparse
import io
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Windows CP949 콘솔 → UTF-8 래핑
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (
            getattr(_s, "encoding", "") or ""
        ).lower().replace("-", "") != "utf8":
            setattr(
                sys, _sn,
                io.TextIOWrapper(
                    _s.buffer, encoding="utf-8",
                    errors="replace", line_buffering=True,
                ),
            )

# 프로젝트 루트를 path에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def get_store_ids(args_store: str = None) -> list:
    """활성 매장 목록 조회"""
    if args_store:
        return [args_store]
    try:
        from src.settings.store_context import StoreContext
        stores = StoreContext.get_all_active()
        return [s.store_id for s in stores]
    except Exception:
        # fallback: stores 디렉토리의 DB 파일로 추정
        store_dir = project_root / "data" / "stores"
        if store_dir.exists():
            return [
                p.stem for p in store_dir.glob("*.db")
                if p.stem.isdigit()
            ]
        return ["46513"]


def show_info(store_ids: list):
    """각 매장의 백필 가능 날짜 범위 출력"""
    from src.analysis.order_diff_backfill import OrderDiffBackfiller

    for sid in store_ids:
        print(f"\n{'='*50}")
        print(f"  매장: {sid}")
        print(f"{'='*50}")

        backfiller = OrderDiffBackfiller(store_id=sid)
        ranges = backfiller.get_available_date_range()

        print(f"  eval_outcomes : {ranges.get('eval_min', '-')} ~ {ranges.get('eval_max', '-')}")
        print(f"  order_tracking: {ranges.get('tracking_min', '-')} ~ {ranges.get('tracking_max', '-')}")
        print(f"  receiving_hist: {ranges.get('receiving_min', '-')} ~ {ranges.get('receiving_max', '-')}")

        # 교집합 (3개 소스 모두 있는 범위)
        mins = [
            v for k, v in ranges.items()
            if k.endswith("_min") and v
        ]
        maxs = [
            v for k, v in ranges.items()
            if k.endswith("_max") and v
        ]
        if mins and maxs:
            overlap_start = max(mins)
            overlap_end = min(maxs)
            if overlap_start <= overlap_end:
                print(f"  -> 백필 가능 범위: {overlap_start} ~ {overlap_end}")
            else:
                print(f"  -> 교집합 없음")
        else:
            print(f"  -> 데이터 부족")


def run_backfill(store_ids: list, start_date: str, end_date: str):
    """백필 실행"""
    from src.analysis.order_diff_backfill import OrderDiffBackfiller

    grand_total = {
        "stores": 0,
        "dates_processed": 0,
        "dates_skipped": 0,
        "snapshots": 0,
        "diffs": 0,
    }

    for sid in store_ids:
        print(f"\n{'='*60}")
        print(f"  매장 {sid} 백필 시작: {start_date} ~ {end_date}")
        print(f"{'='*60}")

        backfiller = OrderDiffBackfiller(store_id=sid)

        # 날짜 범위 자동 결정
        actual_start = start_date
        actual_end = end_date

        if not actual_start or not actual_end:
            ranges = backfiller.get_available_date_range()
            if not actual_start:
                # eval과 tracking 중 늦은 시작일
                candidates = [
                    v for k, v in ranges.items()
                    if k.endswith("_min") and v
                ]
                actual_start = max(candidates) if candidates else None
            if not actual_end:
                # 어제까지
                actual_end = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        if not actual_start:
            print(f"  -> 데이터 없음, 건너뜀")
            continue

        print(f"  실제 범위: {actual_start} ~ {actual_end}")

        result = backfiller.backfill_range(actual_start, actual_end)

        grand_total["stores"] += 1
        grand_total["dates_processed"] += result["processed"]
        grand_total["dates_skipped"] += result["skipped"]
        grand_total["snapshots"] += result["total_snapshots"]
        grand_total["diffs"] += result["total_diffs"]

        print(f"\n  결과: {result['processed']}일 처리 / {result['skipped']}일 스킵")
        print(f"  스냅샷: {result['total_snapshots']}건, diff: {result['total_diffs']}건")

    print(f"\n{'='*60}")
    print(f"  전체 백필 완료")
    print(f"{'='*60}")
    print(f"  매장: {grand_total['stores']}개")
    print(f"  처리: {grand_total['dates_processed']}일, 스킵: {grand_total['dates_skipped']}일")
    print(f"  스냅샷: {grand_total['snapshots']}건")
    print(f"  차이(diff): {grand_total['diffs']}건")


def main():
    parser = argparse.ArgumentParser(
        description="발주 차이 분석 - 과거 데이터 백필",
    )
    parser.add_argument(
        "--store", type=str, default=None,
        help="특정 매장 코드 (기본: 전체 활성 매장)",
    )
    parser.add_argument(
        "--days", type=int, default=None,
        help="최근 N일 백필",
    )
    parser.add_argument(
        "--date", type=str, default=None,
        help="특정 날짜 백필 (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--start", type=str, default=None,
        help="시작일 (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end", type=str, default=None,
        help="종료일 (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--info", action="store_true",
        help="백필 가능 날짜 범위만 확인",
    )

    args = parser.parse_args()

    store_ids = get_store_ids(args.store)
    print(f"대상 매장: {store_ids}")

    if args.info:
        show_info(store_ids)
        return

    # 날짜 범위 결정
    start_date = args.start
    end_date = args.end

    if args.date:
        start_date = args.date
        end_date = args.date
    elif args.days:
        end_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        start_date = (
            datetime.now() - timedelta(days=args.days)
        ).strftime("%Y-%m-%d")

    run_backfill(store_ids, start_date, end_date)


if __name__ == "__main__":
    main()
