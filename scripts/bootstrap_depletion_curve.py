#!/usr/bin/env python3
"""
소진율 곡선 부트스트랩 — 기존 hourly_sales_detail 데이터로 초기화

기존 81일+ hourly_sales_detail 데이터를 사용하여
food_popularity_curve 테이블을 즉시 축적합니다.

Usage:
    python scripts/bootstrap_depletion_curve.py                    # 전매장
    python scripts/bootstrap_depletion_curve.py --store 46513      # 특정 매장
    python scripts/bootstrap_depletion_curve.py --days 90          # 90일 소급
"""

import argparse
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.utils.logger import get_logger

logger = get_logger("bootstrap_depletion_curve")


def main():
    parser = argparse.ArgumentParser(description="소진율 곡선 부트스트랩")
    parser.add_argument("--store", type=str, help="특정 매장만 실행")
    parser.add_argument("--days", type=int, default=60, help="소급 기간 (기본 60일)")
    args = parser.parse_args()

    from src.application.services.food_depletion_service import FoodDepletionService

    if args.store:
        store_ids = [args.store]
    else:
        # 전매장 조회
        try:
            from src.infrastructure.database.connection import DBRouter
            import json
            stores_path = ROOT / "config" / "stores.json"
            if stores_path.exists():
                with open(stores_path) as f:
                    stores = json.load(f)
                store_ids = [s["store_id"] for s in stores if s.get("active", True)]
            else:
                store_ids = ["46513", "46704", "47863", "49965"]
        except Exception:
            store_ids = ["46513", "46704", "47863", "49965"]

    print(f"소진율 곡선 부트스트랩 시작 (매장: {store_ids}, 기간: {args.days}일)")

    for sid in store_ids:
        print(f"\n{'='*60}")
        print(f"  매장 {sid}")
        print(f"{'='*60}")

        try:
            service = FoodDepletionService(store_id=sid)
            result = service.bootstrap_from_history(lookback_days=args.days)
            print(f"  결과: {result}")
        except Exception as e:
            print(f"  오류: {e}")
            logger.error(f"매장 {sid} 부트스트랩 실패: {e}")

    print("\n부트스트랩 완료!")


if __name__ == "__main__":
    main()
