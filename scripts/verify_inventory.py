"""
재고 검증 스크립트: DB realtime_inventory vs BGF NOW_QTY 비교

DB에 저장된 재고(stock_qty)와 BGF 단품별 발주 화면(STBJ030_M0)의
실시간 재고(NOW_QTY)를 비교하여 차이를 진단합니다.

읽기 전용: OrderPrepCollector를 save_to_db=False로 사용하여 DB 변경 없음

Usage:
    python scripts/verify_inventory.py                      # 50개 샘플
    python scripts/verify_inventory.py --max-items 100      # 100개 샘플
    python scripts/verify_inventory.py --item-cd 123 456    # 특정 상품
    python scripts/verify_inventory.py --all --csv          # 전체 + CSV 저장
"""

import sys
import io

# Windows CP949 콘솔 -> UTF-8 래핑 (한글 깨짐 방지)
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

import argparse
import csv
import random
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# 프로젝트 루트를 path에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.infrastructure.database.repos import RealtimeInventoryRepository
from src.settings.constants import DEFAULT_STORE_ID

# =============================================================================
# 상수
# =============================================================================
OUTPUT_DIR = project_root / "data" / "reports"
DEFAULT_MAX_ITEMS = 50
STALE_HOURS = 36
SEC_PER_ITEM = 3.5  # 상품당 예상 소요 시간 (초)

# 상태
STATUS_MATCH = "MATCH"
STATUS_MISMATCH = "MISMATCH"
STATUS_QUERY_FAIL = "FAIL"


# =============================================================================
# 샘플링
# =============================================================================
def _build_sample_list(
    db_items: List[Dict[str, Any]],
    max_items: Optional[int],
    item_codes: Optional[List[str]],
    check_all: bool,
) -> List[Dict[str, Any]]:
    """검증 대상 상품 목록 구성"""
    if item_codes:
        code_set = set(item_codes)
        return [it for it in db_items if it["item_cd"] in code_set]

    if check_all:
        return list(db_items)

    # 우선순위: stock_qty > 0 먼저
    with_stock = [it for it in db_items if (it.get("stock_qty") or 0) > 0]
    without_stock = [it for it in db_items if (it.get("stock_qty") or 0) == 0]
    random.shuffle(with_stock)
    random.shuffle(without_stock)

    combined = with_stock + without_stock
    limit = max_items or DEFAULT_MAX_ITEMS
    return combined[:limit]


def _is_stale(queried_at: Optional[str]) -> bool:
    """queried_at이 STALE_HOURS보다 오래됐는지"""
    if not queried_at:
        return True
    try:
        queried_dt = datetime.fromisoformat(queried_at)
        return queried_dt < datetime.now() - timedelta(hours=STALE_HOURS)
    except (ValueError, TypeError):
        return True


# =============================================================================
# 검증 루프
# =============================================================================
def _verify_items(
    collector: Any,
    sample: List[Dict[str, Any]],
    threshold: int,
) -> List[Dict[str, Any]]:
    """각 상품에 대해 BGF NOW_QTY 조회 후 DB와 비교"""
    results = []
    total = len(sample)

    for i, item in enumerate(sample, 1):
        item_cd = item["item_cd"]
        item_nm = item.get("item_nm") or ""
        db_qty = item.get("stock_qty") or 0
        queried_at = item.get("queried_at") or ""
        stale = _is_stale(queried_at)

        try:
            data = collector.collect_for_item(item_cd)
        except Exception as e:
            print(f"  [{i:>{len(str(total))}}/{total}] {item_cd} {item_nm[:16]}: 조회 오류 - {e}")
            results.append({
                "item_cd": item_cd,
                "item_nm": item_nm,
                "db_stock_qty": db_qty,
                "bgf_now_qty": None,
                "gap": None,
                "status": STATUS_QUERY_FAIL,
                "queried_at": queried_at,
                "is_stale": stale,
            })
            continue

        if not data or not data.get("success"):
            tag = "[STALE]" if stale else ""
            print(f"  [{i:>{len(str(total))}}/{total}] {item_cd} {item_nm[:16]}: DB={db_qty}, BGF=?, FAIL {tag}")
            results.append({
                "item_cd": item_cd,
                "item_nm": item_nm,
                "db_stock_qty": db_qty,
                "bgf_now_qty": None,
                "gap": None,
                "status": STATUS_QUERY_FAIL,
                "queried_at": queried_at,
                "is_stale": stale,
            })
            continue

        bgf_qty = data.get("current_stock", 0)
        gap = bgf_qty - db_qty
        status = STATUS_MISMATCH if abs(gap) >= threshold else STATUS_MATCH

        gap_str = f"+{gap}" if gap > 0 else str(gap)
        tag = f" [{status}]" if status == STATUS_MISMATCH else ""
        stale_tag = " [STALE]" if stale else ""
        print(f"  [{i:>{len(str(total))}}/{total}] {item_cd} {item_nm[:16]}: DB={db_qty}, BGF={bgf_qty}, Gap={gap_str}{tag}{stale_tag}")

        results.append({
            "item_cd": item_cd,
            "item_nm": item_nm,
            "db_stock_qty": db_qty,
            "bgf_now_qty": bgf_qty,
            "gap": gap,
            "status": status,
            "queried_at": queried_at,
            "is_stale": stale,
        })

    return results


# =============================================================================
# 리포트 출력
# =============================================================================
def _print_report(results: List[Dict[str, Any]], total_db_items: int) -> None:
    """콘솔 리포트 테이블 + 요약 통계"""
    sep = "-" * 100

    print(f"\n{'=' * 100}")
    print("[상세 결과]")
    print(sep)
    print(f"{'No.':>4}  {'상품코드':<16} {'상품명':<22} {'DB재고':>6} {'BGF재고':>7} {'차이':>6} {'상태':<10} {'조회시각':<18}")
    print(sep)

    for i, r in enumerate(results, 1):
        bgf_str = str(r["bgf_now_qty"]) if r["bgf_now_qty"] is not None else "-"
        gap_str = "-"
        if r["gap"] is not None:
            gap_str = f"+{r['gap']}" if r["gap"] > 0 else str(r["gap"])

        # 조회시각 짧게
        qa = r.get("queried_at") or ""
        qa_short = ""
        if qa:
            try:
                dt = datetime.fromisoformat(qa)
                qa_short = dt.strftime("%m-%d %H:%M")
            except (ValueError, TypeError):
                qa_short = qa[:16]

        stale_mark = " [STALE]" if r["is_stale"] else ""

        print(
            f"{i:>4}  {r['item_cd']:<16} {r['item_nm'][:20]:<22} "
            f"{r['db_stock_qty']:>6} {bgf_str:>7} {gap_str:>6} "
            f"{r['status']:<10} {qa_short}{stale_mark}"
        )

    print(sep)

    # 요약 통계
    total = len(results)
    matches = sum(1 for r in results if r["status"] == STATUS_MATCH)
    mismatches = sum(1 for r in results if r["status"] == STATUS_MISMATCH)
    fails = sum(1 for r in results if r["status"] == STATUS_QUERY_FAIL)
    stales = sum(1 for r in results if r["is_stale"])

    match_pct = (matches / total * 100) if total else 0
    mismatch_pct = (mismatches / total * 100) if total else 0
    fail_pct = (fails / total * 100) if total else 0

    mismatch_items = [r for r in results if r["status"] == STATUS_MISMATCH and r["gap"] is not None]
    bgf_higher = [r for r in mismatch_items if r["gap"] > 0]
    bgf_lower = [r for r in mismatch_items if r["gap"] < 0]

    print(f"\n{'=' * 100}")
    print("[검증 요약]")
    print(sep)
    print(f"  DB 전체 상품: {total_db_items}개")
    print(f"  검증 대상:    {total}개")
    print(f"  일치 (MATCH):     {matches}개 ({match_pct:.1f}%)")
    print(f"  불일치 (MISMATCH): {mismatches}개 ({mismatch_pct:.1f}%)")
    print(f"  조회실패 (FAIL):   {fails}개 ({fail_pct:.1f}%)")

    if mismatch_items:
        gaps = [abs(r["gap"]) for r in mismatch_items]
        avg_gap = sum(gaps) / len(gaps)
        max_gap_item = max(mismatch_items, key=lambda r: abs(r["gap"]))

        print()
        print(f"  불일치 상세:")
        if bgf_higher:
            total_pos = sum(r["gap"] for r in bgf_higher)
            print(f"    BGF > DB (DB 과소): {len(bgf_higher)}개, 총 갭 +{total_pos}")
        if bgf_lower:
            total_neg = sum(r["gap"] for r in bgf_lower)
            print(f"    BGF < DB (DB 과다): {len(bgf_lower)}개, 총 갭 {total_neg}")
        gap_val = max_gap_item["gap"]
        gap_display = f"+{gap_val}" if gap_val > 0 else str(gap_val)
        print(f"    최대 갭: {gap_display} ({max_gap_item['item_cd']} {max_gap_item['item_nm'][:16]})")
        print(f"    평균 갭: {avg_gap:.1f}")

    if stales:
        print(f"\n  경고: DB 데이터 스테일 (>{STALE_HOURS}h): {stales}개")

    print(f"{'=' * 100}")


def _write_csv(results: List[Dict[str, Any]], output_path: Path) -> None:
    """CSV 파일 저장"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "item_cd", "item_nm", "db_stock_qty", "bgf_now_qty",
            "gap", "status", "queried_at", "is_stale",
        ])
        writer.writeheader()
        writer.writerows(results)
    print(f"\nCSV 저장: {output_path}")


# =============================================================================
# 메인 실행
# =============================================================================
def run_verification(
    max_items: Optional[int] = DEFAULT_MAX_ITEMS,
    item_codes: Optional[List[str]] = None,
    check_all: bool = False,
    store_id: str = DEFAULT_STORE_ID,
    save_csv: bool = False,
    threshold: int = 1,
) -> None:
    """재고 검증 실행"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("\n" + "=" * 100)
    print("재고 검증: DB realtime_inventory vs BGF NOW_QTY")
    print(f"시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"점포: {store_id}")
    print(f"불일치 임계값: {threshold}")
    print("=" * 100)

    # 1. DB 재고 로드
    print("\n[1/4] DB 재고 로드...")
    repo = RealtimeInventoryRepository(store_id=store_id)
    db_items = repo.get_all(available_only=True, exclude_cut=True, store_id=store_id)
    total_db = len(db_items)
    print(f"  취급 상품: {total_db}개")

    if total_db == 0:
        print("[ERROR] DB에 취급 상품이 없습니다.")
        return

    # 2. 샘플 구성
    sample = _build_sample_list(db_items, max_items, item_codes, check_all)
    n = len(sample)
    print(f"\n[2/4] 검증 대상: {n}개 / 전체 {total_db}개")

    if n == 0:
        print("[ERROR] 검증 대상 상품이 없습니다.")
        return

    est_min = n * SEC_PER_ITEM / 60
    print(f"  예상 소요: {est_min:.1f}분 ({n}개 x {SEC_PER_ITEM}초)")

    # --all 안전장치
    if check_all and n > 100:
        confirm = input(f"\n전체 {n}개 상품 검증, 약 {est_min:.0f}분 소요. 계속? (y/N): ")
        if confirm.lower() != "y":
            print("취소됨.")
            return

    # 3. BGF 로그인 + 검증 루프
    analyzer = None
    collector = None

    try:
        print(f"\n[3/4] BGF 로그인...")
        from src.sales_analyzer import SalesAnalyzer
        from src.collectors.order_prep_collector import OrderPrepCollector

        analyzer = SalesAnalyzer()
        analyzer.setup_driver()
        analyzer.connect()

        if not analyzer.do_login():
            print("[ERROR] 로그인 실패")
            return
        print("  로그인 성공")
        time.sleep(2)

        driver = analyzer.driver

        # OrderPrepCollector (읽기 전용)
        collector = OrderPrepCollector(driver=driver, save_to_db=False, store_id=store_id)

        print("  메뉴 이동: 발주 > 단품별 발주...")
        if not collector.navigate_to_menu():
            print("[ERROR] 메뉴 이동 실패")
            return

        print("  발주일 선택...")
        if not collector.select_order_date():
            print("[ERROR] 날짜 선택 실패")
            return

        # 검증 루프
        print(f"\n  검증 시작 ({n}개)...")
        start_time = time.time()
        results = _verify_items(collector, sample, threshold)
        elapsed = time.time() - start_time

        # 4. 리포트
        print(f"\n[4/4] 리포트 생성 (소요: {elapsed:.0f}초, {elapsed/n:.1f}초/건)")
        _print_report(results, total_db)

        if save_csv:
            csv_path = OUTPUT_DIR / f"verify_inventory_{timestamp}.csv"
            _write_csv(results, csv_path)

    except Exception as e:
        print(f"\n[ERROR] {e}")
        traceback.print_exc()

    finally:
        if collector:
            try:
                collector.close_menu()
            except Exception:
                pass
        if analyzer:
            try:
                analyzer.close()
            except Exception:
                pass


# =============================================================================
# CLI
# =============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="재고 검증: DB realtime_inventory vs BGF NOW_QTY 비교"
    )
    parser.add_argument(
        "--max-items", type=int, default=DEFAULT_MAX_ITEMS,
        help=f"검증할 최대 상품 수 (기본: {DEFAULT_MAX_ITEMS})"
    )
    parser.add_argument(
        "--item-cd", nargs="+", type=str,
        help="특정 상품코드만 검증 (공백 구분)"
    )
    parser.add_argument(
        "--all", action="store_true", dest="check_all",
        help="전체 상품 검증 (경고: 수백개 x 3-5초)"
    )
    parser.add_argument(
        "--store-id", type=str, default=DEFAULT_STORE_ID,
        help=f"점포 코드 (기본: {DEFAULT_STORE_ID})"
    )
    parser.add_argument(
        "--csv", action="store_true",
        help="CSV 파일로 결과 저장"
    )
    parser.add_argument(
        "--threshold", type=int, default=1,
        help="불일치 판정 임계값 (기본: 1)"
    )

    args = parser.parse_args()

    run_verification(
        max_items=args.max_items,
        item_codes=args.item_cd,
        check_all=args.check_all,
        store_id=args.store_id,
        save_csv=args.csv,
        threshold=args.threshold,
    )
