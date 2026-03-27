"""
두 점포 푸드 예측 비교 스크립트
- 46513, 46704 점포의 푸드 카테고리(001~005, 012) 예측
- DB 데이터 기반 (로그인 불필요)
"""

import sys
import io

if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime
from src.settings.store_context import StoreContext
from src.application.services.prediction_service import PredictionService
from src.settings.constants import FOOD_CATEGORIES

STORE_IDS = ["46513", "46704"]
FOOD_CATS = set(FOOD_CATEGORIES)  # {"001","002","003","004","005","012"}

def run_food_prediction(store_id: str):
    """한 점포의 푸드 예측 실행"""
    print(f"\n{'='*70}")
    print(f"  점포 {store_id} 푸드 예측")
    print(f"  날짜: {datetime.now().strftime('%Y-%m-%d')} (배송일 기준)")
    print(f"  대상: {', '.join(sorted(FOOD_CATS))}")
    print(f"{'='*70}")

    ctx = StoreContext.from_store_id(store_id)
    service = PredictionService(store_ctx=ctx)

    results = service.predict_all(min_order_qty=0, categories=list(FOOD_CATS))

    # 중분류별 그룹핑
    by_mid = {}
    for r in results:
        mid = r.get("mid_cd", "???")
        by_mid.setdefault(mid, []).append(r)

    MID_NAMES = {
        "001": "도시락", "002": "주먹밥", "003": "김밥",
        "004": "샌드위치", "005": "햄버거", "012": "빵",
    }

    total_items = 0
    total_order = 0

    for mid in sorted(by_mid.keys()):
        items = by_mid[mid]
        name = MID_NAMES.get(mid, mid)
        print(f"\n── {mid} {name} ({len(items)}개) ──")
        print(f"  {'상품코드':<16} {'상품명':<28} {'예측':>5} {'재고':>5} {'발주':>5} {'판정'}")
        print(f"  {'-'*16} {'-'*28} {'-'*5} {'-'*5} {'-'*5} {'-'*10}")

        items.sort(key=lambda x: x.get("final_order_qty", 0), reverse=True)
        for item in items:
            item_cd = item.get("item_cd", "")
            item_nm = item.get("item_nm", "")[:26]
            predicted = item.get("predicted_qty", 0)
            stock = item.get("current_stock", 0)
            order_qty = item.get("final_order_qty", 0)
            decision = item.get("eval_decision", "")

            if isinstance(predicted, float):
                predicted = round(predicted, 1)

            total_items += 1
            total_order += max(0, order_qty)

            print(f"  {item_cd:<16} {item_nm:<28} {predicted:>5} {stock:>5} {order_qty:>5} {decision}")

    print(f"\n{'─'*70}")
    print(f"  합계: {total_items}개 상품, 발주 수량 {total_order}개")
    print(f"{'─'*70}")

    return results


if __name__ == "__main__":
    print("=" * 70)
    print("  두 점포 푸드 예측 비교")
    print(f"  실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    all_results = {}
    for sid in STORE_IDS:
        all_results[sid] = run_food_prediction(sid)

    # 비교 요약
    print(f"\n\n{'='*70}")
    print("  점포 간 비교 요약")
    print(f"{'='*70}")
    print(f"  {'항목':<20} {'46513':>12} {'46704':>12}")
    print(f"  {'-'*20} {'-'*12} {'-'*12}")

    for sid in STORE_IDS:
        results = all_results[sid]
        total = len(results)
        order_items = sum(1 for r in results if r.get("final_order_qty", 0) > 0)
        total_qty = sum(max(0, r.get("final_order_qty", 0)) for r in results)
        total_pred = sum(r.get("predicted_qty", 0) for r in results)

        if sid == STORE_IDS[0]:
            row_total = f"{total}"
            row_order = f"{order_items}"
            row_qty = f"{total_qty}"
            row_pred = f"{round(total_pred, 1)}"
        else:
            print(f"  {'전체 상품 수':<20} {len(all_results[STORE_IDS[0]]):>12} {total:>12}")
            print(f"  {'발주 대상 수':<20} {sum(1 for r in all_results[STORE_IDS[0]] if r.get('final_order_qty',0)>0):>12} {order_items:>12}")
            print(f"  {'총 발주 수량':<20} {sum(max(0, r.get('final_order_qty',0)) for r in all_results[STORE_IDS[0]]):>12} {total_qty:>12}")
            print(f"  {'총 예측 수량':<20} {round(sum(r.get('predicted_qty',0) for r in all_results[STORE_IDS[0]]),1):>12} {round(total_pred,1):>12}")

    print(f"\n완료: {datetime.now().strftime('%H:%M:%S')}")
