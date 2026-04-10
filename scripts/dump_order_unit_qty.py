# -*- coding: utf-8 -*-
"""
발주입수(order_unit_qty) 전수 덤프 + DB 대조

1단계: fetch_all_details_direct.py 방식으로 Direct API 전수 수집
2단계: 수집 결과와 기존 DB 대조 → 불일치 리포트 출력
3단계: --apply 시 DB 갱신

사용법:
  python scripts/dump_order_unit_qty.py --max 20          # 20개 테스트
  python scripts/dump_order_unit_qty.py --dry-run         # 대조만 (DB 변경 없음)
  python scripts/dump_order_unit_qty.py --apply           # 불일치 DB 갱신
"""
import sys
import io

if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

import os
import time
import json
import argparse
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.utils.logger import get_logger
from src.settings.timing import SA_LOGIN_WAIT, SA_POPUP_CLOSE_WAIT

logger = get_logger("dump_order_unit_qty")

REPORT_PATH = project_root / "data" / "reports" / "order_unit_mismatch.json"


# ═══════════════════════════════════════════════
# 1. 수집 대상
# ═══════════════════════════════════════════════

def get_target_items() -> List[Dict[str, Any]]:
    """DB에서 전체 상품 + 기존 order_unit_qty 조회"""
    db_path = project_root / "data" / "common.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT p.item_cd, p.item_nm, p.mid_cd,
                   pd.order_unit_qty as db_unit,
                   pd.case_unit_qty as db_case,
                   pd.order_unit_name as db_unit_name
            FROM products p
            LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
            ORDER BY p.mid_cd, p.item_cd
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ═══════════════════════════════════════════════
# 2. Direct API 수집
# ═══════════════════════════════════════════════

def collect_via_direct_api(driver, item_codes, concurrency=5, batch_size=500):
    """DirectPopupFetcher 배치 수집"""
    from src.collectors.direct_popup_fetcher import (
        DirectPopupFetcher, extract_product_detail
    )

    fetcher = DirectPopupFetcher(driver, concurrency=concurrency, timeout_ms=10000)

    if not fetcher.capture_template():
        logger.warning("DirectPopupFetcher 템플릿 캡처 실패")
        return None

    results = {}
    total = len(item_codes)

    for i in range(0, total, batch_size):
        batch = item_codes[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        logger.info(f"배치 {batch_num}/{total_batches}: {len(batch)}개 수집 중...")

        raw = fetcher.fetch_items_batch(batch, include_ord=True, delay_ms=50)

        for item_cd, data in raw.items():
            detail = data.get("detail")
            ord_data = data.get("ord")
            if detail or ord_data:
                info = extract_product_detail(detail, ord_data, item_cd)
                if info:
                    results[item_cd] = {
                        "site_unit": info.get("order_unit_qty"),
                        "site_case": info.get("case_unit_qty"),
                        "site_unit_name": info.get("order_unit_name"),
                        "item_nm": info.get("item_nm", ""),
                    }

        logger.info(f"  누적: {len(results)}/{min(i + batch_size, total)}건")

    return results


def trigger_popup_for_template(driver):
    """홈 화면에서 CallItemDetailPopup 1회 트리거 (템플릿 캡처용)"""
    from scripts.fetch_all_details_direct import install_popup_interceptor
    from src.collectors.product_detail_batch_collector import ProductDetailBatchCollector

    install_popup_interceptor(driver)

    batch_collector = ProductDetailBatchCollector(driver=driver, store_id=None)

    # 아무 상품 1개로 팝업 트리거
    sample_codes = ["8801094013004", "8801043036016", "8801062475711"]
    for code in sample_codes:
        try:
            result = batch_collector._fetch_single_item(code)
            if result and result.get("item_nm"):
                logger.info(f"템플릿 트리거 성공: {result.get('item_nm')}")
                return True
        except Exception as e:
            logger.debug(f"트리거 실패 ({code}): {e}")
            continue

    return False


# ═══════════════════════════════════════════════
# 3. DB 대조
# ═══════════════════════════════════════════════

def compare_results(targets, site_results):
    """기존 DB vs 수집 결과 대조"""

    stats = {
        "total": len(targets),
        "collected": 0,
        "not_collected": 0,
        "match": 0,
        "mismatch_db1_site_gt1": [],    # DB=1인데 site>1 (과발주 위험)
        "mismatch_db_gt1_site_diff": [], # DB>1인데 site와 다름
        "mismatch_db_null_site_gt1": [], # DB=null인데 site>1
        "site_null_db_gt1": [],          # site=빈값인데 DB>1
        "site_null_db_1": [],            # site=빈값, DB=1 (확인 불가)
        "both_1": 0,                     # 양쪽 모두 1 (정상 낱개)
    }

    for item in targets:
        item_cd = item["item_cd"]
        db_unit = item["db_unit"]
        mid_cd = item["mid_cd"] or ""

        site = site_results.get(item_cd)
        if not site:
            stats["not_collected"] += 1
            continue

        stats["collected"] += 1
        site_unit = site.get("site_unit")

        record = {
            "item_cd": item_cd,
            "item_nm": site.get("item_nm", item.get("item_nm", "")),
            "mid_cd": mid_cd,
            "db_unit": db_unit,
            "site_unit": site_unit,
            "site_case": site.get("site_case"),
            "site_unit_name": site.get("site_unit_name"),
        }

        # 분류
        if site_unit is None or site_unit == 0:
            if db_unit and db_unit > 1:
                stats["site_null_db_gt1"].append(record)
            else:
                stats["site_null_db_1"].append(record)
        elif db_unit == site_unit:
            if db_unit == 1:
                stats["both_1"] += 1
            else:
                stats["match"] += 1
        elif (db_unit is None or db_unit == 0 or db_unit == 1) and site_unit > 1:
            stats["mismatch_db1_site_gt1"].append(record)
        elif db_unit and db_unit > 1 and db_unit != site_unit:
            stats["mismatch_db_gt1_site_diff"].append(record)
        elif (db_unit is None or db_unit == 0) and site_unit > 1:
            stats["mismatch_db_null_site_gt1"].append(record)
        else:
            stats["match"] += 1

    return stats


def print_report(stats):
    """대조 결과 리포트 출력"""
    print("\n" + "=" * 80)
    print("발주입수 대조 결과")
    print("=" * 80)

    print(f"\n전체: {stats['total']}개")
    print(f"수집 성공: {stats['collected']}개")
    print(f"수집 실패: {stats['not_collected']}개")
    print(f"일치 (unit>1): {stats['match']}개")
    print(f"양쪽 모두 1 (낱개): {stats['both_1']}개")

    # 과발주 위험 (DB=1, site>1)
    danger = stats["mismatch_db1_site_gt1"]
    print(f"\n{'='*80}")
    print(f"[위험] DB=1인데 실제 묶음 (과발주 위험): {len(danger)}건")
    print(f"{'='*80}")
    if danger:
        print(f"{'상품코드':<16} {'DB':>4} {'SITE':>4} {'단위':>6} {'mid':>4} 상품명")
        print("-" * 80)
        for r in danger[:50]:
            nm = (r.get("site_unit_name") or "")[:4]
            print(f"{r['item_cd']:<16} {r['db_unit'] or 0:>4} {r['site_unit']:>4} {nm:>6} {r['mid_cd']:>4} {r['item_nm'][:30]}")
        if len(danger) > 50:
            print(f"  ... 외 {len(danger) - 50}건")

    # DB>1인데 site와 다름
    diff = stats["mismatch_db_gt1_site_diff"]
    if diff:
        print(f"\n[주의] DB>1인데 site와 불일치: {len(diff)}건")
        print(f"{'상품코드':<16} {'DB':>4} {'SITE':>4} {'mid':>4} 상품명")
        print("-" * 80)
        for r in diff[:20]:
            print(f"{r['item_cd']:<16} {r['db_unit']:>4} {r['site_unit']:>4} {r['mid_cd']:>4} {r['item_nm'][:30]}")

    # site 빈값
    null_gt1 = stats["site_null_db_gt1"]
    null_1 = stats["site_null_db_1"]
    if null_gt1:
        print(f"\n[참고] site=빈값, DB>1 (BGF 미반환): {len(null_gt1)}건")
    if null_1:
        print(f"[참고] site=빈값, DB=1 (확인 불가): {len(null_1)}건")

    total_mismatch = len(danger) + len(diff) + len(stats.get("mismatch_db_null_site_gt1", []))
    print(f"\n총 불일치: {total_mismatch}건")

    return total_mismatch


def save_report(stats):
    """대조 결과 JSON 저장"""
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total": stats["total"],
            "collected": stats["collected"],
            "match": stats["match"],
            "both_1": stats["both_1"],
            "mismatch_db1_site_gt1": len(stats["mismatch_db1_site_gt1"]),
            "mismatch_db_gt1_site_diff": len(stats["mismatch_db_gt1_site_diff"]),
        },
        "mismatch_db1_site_gt1": stats["mismatch_db1_site_gt1"],
        "mismatch_db_gt1_site_diff": stats["mismatch_db_gt1_site_diff"],
    }

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n리포트 저장: {REPORT_PATH}")


# ═══════════════════════════════════════════════
# 4. DB 갱신
# ═══════════════════════════════════════════════

def apply_fixes(stats):
    """불일치 상품 DB 갱신"""
    from src.infrastructure.database.repos import ProductDetailRepository
    repo = ProductDetailRepository()

    all_fixes = stats["mismatch_db1_site_gt1"] + stats["mismatch_db_gt1_site_diff"]
    if not all_fixes:
        print("갱신 대상 없음")
        return 0

    updated = 0
    for r in all_fixes:
        try:
            repo.update_field(r["item_cd"], "order_unit_qty", r["site_unit"])
            updated += 1
        except Exception as e:
            logger.warning(f"DB 갱신 실패 ({r['item_cd']}): {e}")

    print(f"\nDB 갱신 완료: {updated}/{len(all_fixes)}건")
    return updated


# ═══════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="발주입수 전수 덤프 + DB 대조")
    parser.add_argument("--max", type=int, default=0, help="최대 수집 개수 (0=전체)")
    parser.add_argument("--dry-run", action="store_true", help="대조만 (DB 변경 없음)")
    parser.add_argument("--apply", action="store_true", help="불일치 DB 갱신")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    print("=" * 70)
    print("발주입수(order_unit_qty) 전수 덤프 + DB 대조")
    print(f"시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 1. 수집 대상 로드
    targets = get_target_items()
    if args.max > 0:
        targets = targets[:args.max]

    item_codes = [t["item_cd"] for t in targets]
    print(f"\n수집 대상: {len(item_codes)}개")
    print(f"예상 소요: ~{len(item_codes) * 0.15 / 60:.0f}분")

    if args.dry_run and args.max == 0:
        print("\n[DRY-RUN] --max 없이 전수 dry-run은 수집 없이 종료")
        return

    # 2. BGF 로그인
    print("\n[1/5] BGF 로그인...")
    from src.sales_analyzer import SalesAnalyzer
    analyzer = SalesAnalyzer()

    try:
        analyzer.setup_driver()
        analyzer.connect()
        time.sleep(SA_LOGIN_WAIT)

        if not analyzer.do_login():
            print("[ERROR] 로그인 실패!")
            return

        print("[OK] 로그인 성공")
        time.sleep(SA_POPUP_CLOSE_WAIT * 2)
        analyzer.close_popup()
        time.sleep(SA_POPUP_CLOSE_WAIT)

        # 3. 템플릿 캡처
        print("\n[2/5] CallItemDetailPopup 템플릿 캡처...")
        if not trigger_popup_for_template(analyzer.driver):
            print("[ERROR] 팝업 트리거 실패!")
            return

        time.sleep(1)
        cap_count = analyzer.driver.execute_script(
            "return (window.__popupCaptures || []).length;"
        )
        print(f"  캡처: {cap_count}건")

        # 4. Direct API 배치 수집
        print(f"\n[3/5] Direct API 배치 수집...")
        site_results = collect_via_direct_api(
            analyzer.driver, item_codes,
            concurrency=args.concurrency,
            batch_size=args.batch_size,
        )

        if not site_results:
            print("[ERROR] 수집 결과 0건!")
            return

        print(f"\n수집 완료: {len(site_results)}건")

        # 5. DB 대조
        print(f"\n[4/5] DB 대조...")
        stats = compare_results(targets, site_results)
        total_mismatch = print_report(stats)
        save_report(stats)

        # 6. DB 갱신
        if args.apply and total_mismatch > 0:
            print(f"\n[5/5] DB 갱신 ({total_mismatch}건)...")
            apply_fixes(stats)
        elif total_mismatch > 0:
            print(f"\n[5/5] --apply 미지정. DB 갱신하려면:")
            print(f"  python scripts/dump_order_unit_qty.py --apply")
        else:
            print(f"\n[5/5] 불일치 없음. DB 갱신 불필요")

    except Exception as e:
        logger.error(f"실패: {e}", exc_info=True)
    finally:
        if analyzer and analyzer.driver:
            try:
                analyzer.driver.quit()
            except Exception:
                pass

    print(f"\n완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
