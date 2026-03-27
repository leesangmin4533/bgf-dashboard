#!/usr/bin/env python3
"""
골든 스냅샷 생성기 — 리팩토링 전후 검증용 정답지

사용법:
    python scripts/create_golden_snapshot.py                  # 3매장 전체
    python scripts/create_golden_snapshot.py --store 46704    # 특정 매장만
    python scripts/create_golden_snapshot.py --date 2026-03-22  # 날짜 지정
    python scripts/create_golden_snapshot.py --use-prefetch   # DB pending 주입

출력:
    tests/golden/golden_{store_id}_{YYYYMMDD}.json
"""
import sys
import os
import json
import argparse
from datetime import datetime
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.logger import get_logger

logger = get_logger(__name__)

# 대상 매장
DEFAULT_STORES = ["46704", "46513", "47863"]

# 출력 디렉토리
GOLDEN_DIR = PROJECT_ROOT / "tests" / "golden"


def _make_stages_serializable(stages: dict) -> dict:
    """stages 값을 JSON 직렬화 가능하게 변환"""
    if not stages:
        return {}
    result = {}
    for k, v in stages.items():
        if v is None:
            result[k] = "추적불가"
        elif isinstance(v, (int, float)):
            result[k] = v
        else:
            result[k] = str(v)
    return result


def _determine_decided_by(stages: dict) -> str:
    """어느 단계에서 최종 발주량이 결정됐는지 판단"""
    if not stages:
        return "unknown"

    final = stages.get("after_manual_deduct")
    if final is None:
        final = stages.get("after_food_cap")
    if final is None:
        final = stages.get("final")
    if final is None or final == "추적불가":
        return "unknown"

    # 역순으로 변경 추적: 가장 마지막으로 값을 바꾼 단계
    ordered_stages = [
        ("manual_deduct", "after_manual_deduct"),
        ("food_cap", "after_food_cap"),
        ("floor", "after_floor"),
        ("round", "after_round"),
        ("category_cap", "after_cap"),
        ("substitution", "after_sub"),
        ("promo_floor", "after_promo_floor"),
        ("diff_feedback", "after_diff"),
        ("ML", "after_ml"),
        ("promo", "after_promo"),
        ("ROP", "after_rop"),
        ("rule", "after_rule"),
    ]

    prev_val = None
    decided_by = "rule"  # 기본값
    for stage_name, key in reversed(ordered_stages):
        val = stages.get(key)
        if val is not None and val != "추적불가":
            if prev_val is not None and val != prev_val:
                decided_by = stage_name
            prev_val = val

    # 최종값과 직전 단계 값이 다른 가장 마지막 단계
    for stage_name, key in ordered_stages:
        val = stages.get(key)
        prev_key_idx = [k for _, k in ordered_stages]
        idx = prev_key_idx.index(key)
        if idx + 1 < len(ordered_stages):
            prev_stage_key = ordered_stages[idx + 1][1]
            prev_val = stages.get(prev_stage_key)
            if val is not None and prev_val is not None and val != prev_val and val != "추적불가":
                decided_by = stage_name
                break

    return decided_by


def _inject_db_pending(auto_order, store_id: str) -> int:
    """DB confirmed pending을 predictor 캐시에 주입 (실제 발주와 동일한 pending 반영)"""
    try:
        pending_data = auto_order._get_confirmed_pending_from_db()
        if pending_data and auto_order.improved_predictor:
            auto_order.improved_predictor.set_pending_cache(pending_data)
            logger.info(
                f"[prefetch] DB pending {len(pending_data)}건 주입 "
                f"(store={store_id})"
            )
            return len(pending_data)
    except Exception as e:
        logger.warning(f"[prefetch] DB pending 주입 실패 (무시): {e}")
    return 0


def create_snapshot_for_store(
    store_id: str,
    date_str: str,
    use_prefetch: bool = False
) -> dict:
    """단일 매장의 골든 스냅샷 생성 (dry_run 모드, DB 쓰기 없음)"""
    from src.order.auto_order import AutoOrderSystem

    logger.info(f"=== 매장 {store_id} 스냅샷 생성 시작 ===")

    # AutoOrderSystem 초기화 (driver 없이 — dry_run 전용)
    auto_order = AutoOrderSystem(
        store_id=store_id,
        driver=None,  # dry_run이므로 WebDriver 불필요
        use_improved_predictor=True,
    )

    # C: prefetch 캐시 주입 (--use-prefetch 옵션)
    if use_prefetch:
        _inject_db_pending(auto_order, store_id)

    # 예측 + 추천 목록 생성 (A/B: skip_db_write=True로 DB 쓰기 방지)
    try:
        order_list = auto_order.get_recommendations(
            min_order_qty=0,  # 0도 포함하여 모든 SKU 수집
            skip_db_write=True,  # eval_outcomes, prediction_logs 쓰기 스킵
        )
    except Exception as e:
        logger.error(f"매장 {store_id} 예측 실패: {e}")
        return {
            "store_id": store_id,
            "date": date_str,
            "error": str(e),
            "products": [],
        }

    # 수동발주 차감 시뮬레이션 (execute 내부에서 일어나는 단계)
    try:
        order_list = auto_order._deduct_manual_food_orders(order_list, min_order_qty=0)
    except Exception as e:
        logger.warning(f"수동발주 차감 실패 (무시): {e}")

    # 수동차감 후 stages 기록
    for _item in order_list:
        _ss = _item.get("_snapshot_stages")
        if _ss is not None:
            _ss["after_manual_deduct"] = _item.get("final_order_qty", 0)

    # 결과 변환
    products = []
    for item in order_list:
        stages = item.get("_snapshot_stages")
        if stages is None:
            # stages 없는 항목 (Floor/CUT 보충 등으로 추가된 항목)
            stages = {
                "after_rule": "추적불가",
                "after_rop": "추적불가",
                "after_promo": "추적불가",
                "after_ml": "추적불가",
                "after_diff": "추적불가",
                "after_promo_floor": "추적불가",
                "after_sub": "추적불가",
                "after_cap": "추적불가",
                "after_round": "추적불가",
                "final": item.get("final_order_qty", 0),
                "before_floor": "추적불가",
                "after_floor": item.get("final_order_qty", 0),
                "after_food_cap": item.get("final_order_qty", 0),
                "after_manual_deduct": item.get("final_order_qty", 0),
            }

        safe_stages = _make_stages_serializable(stages)

        product_entry = {
            "product_code": item.get("item_cd", ""),
            "product_name": item.get("item_nm", ""),
            "category_mid": item.get("mid_cd", ""),
            "category_large": "",  # large_cd는 별도 조회 필요 — 현재 dict에 없음
            "final_qty": item.get("final_order_qty", 0),
            "order_unit": item.get("order_unit_qty", 1),
            "decided_by": _determine_decided_by(safe_stages),
            "stages": safe_stages,
            # 추가 진단 정보
            "demand_pattern": item.get("demand_pattern", ""),
            "model_type": item.get("model_type", "rule"),
            "current_stock": item.get("current_stock", 0),
            "pending_qty": item.get("pending_receiving_qty", 0),
        }
        products.append(product_entry)

    snapshot = {
        "store_id": store_id,
        "date": date_str,
        "generated_at": datetime.now().isoformat(),
        "generator_version": "1.1.0",
        "use_prefetch": use_prefetch,
        "total_products": len(products),
        "total_qty": sum(p["final_qty"] for p in products),
        "products": products,
    }

    logger.info(
        f"매장 {store_id}: {len(products)}개 SKU, "
        f"총 발주량 {snapshot['total_qty']}개"
    )
    return snapshot


def save_snapshot(snapshot: dict, store_id: str, date_str: str) -> Path:
    """스냅샷을 JSON 파일로 저장"""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"golden_{store_id}_{date_str.replace('-', '')}.json"
    filepath = GOLDEN_DIR / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    logger.info(f"저장 완료: {filepath}")
    return filepath


def main():
    parser = argparse.ArgumentParser(description="골든 스냅샷 생성기")
    parser.add_argument(
        "--store", type=str, default=None,
        help="특정 매장만 실행 (예: 46704)"
    )
    parser.add_argument(
        "--date", type=str, default=None,
        help="날짜 지정 (YYYY-MM-DD, 기본: 오늘)"
    )
    parser.add_argument(
        "--use-prefetch", action="store_true", default=False,
        help="DB pending 캐시 주입 (실제 발주와 동일한 미입고 반영)"
    )
    args = parser.parse_args()

    date_str = args.date or datetime.now().strftime("%Y-%m-%d")
    stores = [args.store] if args.store else DEFAULT_STORES

    logger.info(
        f"골든 스냅샷 생성 시작: {date_str}, 매장: {stores}"
        f"{', prefetch=ON' if args.use_prefetch else ''}"
    )

    results = {}
    for store_id in stores:
        try:
            snapshot = create_snapshot_for_store(
                store_id, date_str,
                use_prefetch=args.use_prefetch
            )
            filepath = save_snapshot(snapshot, store_id, date_str)
            results[store_id] = {
                "success": True,
                "file": str(filepath),
                "products": snapshot["total_products"],
                "qty": snapshot["total_qty"],
            }
        except Exception as e:
            logger.error(f"매장 {store_id} 실패: {e}", exc_info=True)
            results[store_id] = {
                "success": False,
                "error": str(e),
            }

    # 요약
    logger.info("\n=== 골든 스냅샷 생성 결과 ===")
    for store_id, result in results.items():
        if result["success"]:
            logger.info(
                f"  {store_id}: ✓ {result['products']}개 SKU, "
                f"{result['qty']}개 발주, {result['file']}"
            )
        else:
            logger.error(f"  {store_id}: ✗ {result['error']}")

    # 전체 성공 여부
    all_ok = all(r["success"] for r in results.values())
    if all_ok:
        logger.info("모든 매장 스냅샷 생성 완료 ✓")
    else:
        logger.warning("일부 매장 실패 — 위 로그 확인")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
