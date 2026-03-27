# -*- coding: utf-8 -*-
"""
전상품 상세정보 1회성 수집 (Direct API)

홈 화면에서 XHR 인터셉터 설치 → CallItemDetailPopup 1회 Selenium 오픈 →
stbjz00 body 캡처 → DirectPopupFetcher 배치 수집.

사용법:
  python scripts/fetch_all_details_direct.py              # 전체 수집
  python scripts/fetch_all_details_direct.py --dry-run    # 대상만 출력
  python scripts/fetch_all_details_direct.py --max 50     # 50개만 테스트
  python scripts/fetch_all_details_direct.py --force      # sell_price 덮어쓰기

예상 소요: ~5,000개 × 0.15초 ≈ 12~15분
"""

import sys
import io

# Windows CP949 → UTF-8
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

import os
import time
import argparse
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# 프로젝트 루트
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.sales_analyzer import SalesAnalyzer
from src.collectors.product_detail_batch_collector import ProductDetailBatchCollector
from src.collectors.direct_popup_fetcher import DirectPopupFetcher
from src.infrastructure.database.repos import ProductDetailRepository
from src.settings.timing import SA_LOGIN_WAIT, SA_POPUP_CLOSE_WAIT


def parse_args():
    parser = argparse.ArgumentParser(description="전상품 상세정보 Direct API 수집")
    parser.add_argument("--max", type=int, default=0, help="최대 수집 개수 (0=전체)")
    parser.add_argument("--dry-run", action="store_true", help="대상만 출력")
    parser.add_argument("--force", action="store_true",
                        help="기존 sell_price도 덮어쓰기")
    parser.add_argument("--concurrency", type=int, default=5,
                        help="동시 요청 수 (기본: 5)")
    parser.add_argument("--batch-size", type=int, default=500,
                        help="JS 배치 크기 (기본: 500)")
    return parser.parse_args()


def get_all_product_codes() -> List[str]:
    """common.db에서 전체 상품코드 조회"""
    db_path = project_root / "data" / "common.db"
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT item_cd FROM products ORDER BY updated_at DESC").fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def get_sell_price_status() -> Dict[str, Any]:
    """현재 매가 등록 현황"""
    db_path = project_root / "data" / "common.db"
    conn = sqlite3.connect(str(db_path))
    try:
        r = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN pd.sell_price IS NOT NULL AND pd.sell_price > 0 THEN 1 ELSE 0 END) as has_price,
                SUM(CASE WHEN pd.item_cd IS NULL THEN 1 ELSE 0 END) as no_detail_row
            FROM products p
            LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
        """).fetchone()
        return {"total": r[0], "has_price": r[1], "no_detail_row": r[2]}
    finally:
        conn.close()


def install_popup_interceptor(driver) -> bool:
    """stbjz00 (CallItemDetailPopup) XHR 인터셉터 설치

    이 인터셉터는 __popupCaptures에 body를 캡처합니다.
    DirectPopupFetcher.capture_template()이 이 데이터를 사용합니다.
    """
    try:
        driver.execute_script(r"""
            if (!window.__popupCaptures) {
                window.__popupCaptures = [];
            }
            if (!window.__popupInterceptorInstalled) {
                window.__popupInterceptorInstalled = true;
                var origOpen = XMLHttpRequest.prototype.open;
                var origSend = XMLHttpRequest.prototype.send;

                XMLHttpRequest.prototype.open = function(method, url) {
                    this.__captureUrl = url;
                    this.__captureMethod = method;
                    return origOpen.apply(this, arguments);
                };

                XMLHttpRequest.prototype.send = function(body) {
                    var url = this.__captureUrl || '';
                    if (url.indexOf('stbjz00') >= 0 ||
                        url.indexOf('selItemDetail') >= 0) {
                        window.__popupCaptures.push({
                            url: url,
                            method: this.__captureMethod,
                            bodyPreview: body ? String(body).substring(0, 5000) : '',
                            timestamp: new Date().toISOString()
                        });
                    }
                    return origSend.apply(this, arguments);
                };
                console.log('[Interceptor] stbjz00 popup capture installed');
            }
        """)
        return True
    except Exception as e:
        print(f"[ERROR] 인터셉터 설치 실패: {e}")
        return False


def save_results_to_db(results: Dict[str, Dict], force_sell_price: bool = False):
    """Direct API 결과를 DB에 저장"""
    repo = ProductDetailRepository()
    saved = 0
    errors = 0

    for item_cd, data in results.items():
        try:
            # products.mid_cd 업데이트
            mid_cd = data.get("mid_cd")
            if mid_cd and mid_cd.strip():
                repo.update_product_mid_cd(item_cd, mid_cd)
                mid_nm = data.get("mid_nm")
                large_cd = data.get("large_cd")
                large_nm = data.get("large_nm")
                if mid_nm or large_cd or large_nm:
                    repo.upsert_mid_category_detail(
                        mid_cd=mid_cd, mid_nm=mid_nm,
                        large_cd=large_cd, large_nm=large_nm,
                    )

            # product_details 업데이트
            detail_data = {
                "item_nm": data.get("item_nm"),
                "expiration_days": data.get("expiration_days"),
                "orderable_day": data.get("orderable_day"),
                "orderable_status": data.get("orderable_status"),
                "order_unit_qty": data.get("order_unit_qty"),
                "order_unit_name": data.get("order_unit_name"),
                "case_unit_qty": data.get("case_unit_qty"),
                "sell_price": data.get("sell_price"),
                "large_cd": data.get("large_cd"),
                "small_cd": data.get("small_cd"),
                "small_nm": data.get("small_nm"),
                "class_nm": data.get("class_nm"),
                "fetched_at": datetime.now().isoformat(),
            }

            repo.bulk_update_from_popup(item_cd, detail_data)

            if force_sell_price and data.get("sell_price"):
                _force_update_sell_price(repo, item_cd, data["sell_price"])

            saved += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  [DB ERROR] {item_cd}: {e}")

    return saved, errors


def _force_update_sell_price(repo, item_cd: str, sell_price):
    """sell_price 강제 업데이트"""
    conn = repo._get_conn()
    try:
        price = int(str(sell_price).replace(",", "")) if sell_price else None
        if price and price > 0:
            conn.execute(
                "UPDATE product_details SET sell_price = ?, updated_at = ? WHERE item_cd = ?",
                (price, datetime.now().isoformat(), item_cd)
            )
            conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def main():
    args = parse_args()

    print("=" * 70)
    print("전상품 상세정보 Direct API 수집")
    print(f"시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 1. 수집 전 현황
    before = get_sell_price_status()
    print(f"\n[수집 전 현황]")
    print(f"  전체 상품: {before['total']}개")
    print(f"  매가 등록: {before['has_price']}개 ({before['has_price']/before['total']*100:.1f}%)")
    print(f"  product_details 없음: {before['no_detail_row']}개")

    # 2. 수집 대상
    all_items = get_all_product_codes()
    if args.max > 0:
        all_items = all_items[:args.max]

    total = len(all_items)
    print(f"\n수집 대상: {total}개")
    print(f"예상 소요: ~{total * 0.15 / 60:.0f}분 (Direct API {args.concurrency} concurrent)")

    if args.dry_run:
        print("\n[DRY-RUN] 실제 수집 생략")
        return

    # 3. BGF 로그인
    print("\n[1/6] BGF 사이트 로그인...")
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

        # 4. XHR 인터셉터 설치 (stbjz00 캡처용)
        print("\n[2/6] XHR 인터셉터 설치 (stbjz00 popup)...")
        if not install_popup_interceptor(analyzer.driver):
            print("[ERROR] 인터셉터 설치 실패!")
            return
        print("[OK] 인터셉터 설치 완료")

        # 5. ProductDetailBatchCollector로 Selenium 1건 (CallItemDetailPopup 오픈 → 캡처)
        print("\n[3/6] Selenium으로 CallItemDetailPopup 1건 (템플릿 캡처)...")
        batch_collector = ProductDetailBatchCollector(
            driver=analyzer.driver, store_id=None
        )

        sample = all_items[0]
        print(f"  상품: {sample}")

        # _fetch_single_item: 홈화면 edt_pluSearch → Quick Search → CallItemDetailPopup
        result = batch_collector._fetch_single_item(sample)

        if result and result.get("item_nm"):
            print(f"  [OK] {result.get('item_nm')} (exp={result.get('expiration_days')})")
            # DB에도 저장
            try:
                batch_collector._save_to_db(sample, result)
            except Exception:
                pass
        else:
            print(f"  [WARN] 첫 상품 실패, 두 번째 상품 시도...")
            if len(all_items) > 1:
                sample = all_items[1]
                print(f"  상품: {sample}")
                result = batch_collector._fetch_single_item(sample)
                if result and result.get("item_nm"):
                    print(f"  [OK] {result.get('item_nm')}")
                    try:
                        batch_collector._save_to_db(sample, result)
                    except Exception:
                        pass
                else:
                    print("[ERROR] CallItemDetailPopup 열기 실패 — 스크립트 종료")
                    return

        time.sleep(1)

        # 캡처 확인
        cap_count = analyzer.driver.execute_script(
            "return (window.__popupCaptures || []).length;"
        )
        print(f"  __popupCaptures: {cap_count}개 캡처됨")

        if cap_count == 0:
            print("[ERROR] stbjz00 XHR 캡처 0건 — Direct API 불가")
            print("  Selenium 배치로 전환합니다...")
            _run_selenium_batch(batch_collector, all_items, args.force)
            _print_after_status(before)
            return

        # 6. DirectPopupFetcher로 템플릿 확인
        fetcher = DirectPopupFetcher(
            analyzer.driver,
            concurrency=args.concurrency,
            timeout_ms=10000,
        )

        if not fetcher.capture_template():
            print("[ERROR] DirectPopupFetcher 템플릿 파싱 실패")
            # 캡처 내용 디버그
            caps = analyzer.driver.execute_script(
                "return JSON.stringify((window.__popupCaptures || []).map(c => ({url:c.url, len:c.bodyPreview?.length||0})));"
            )
            print(f"  캡처 내용: {caps}")
            print("  Selenium 배치로 전환합니다...")
            _run_selenium_batch(batch_collector, all_items, args.force)
            _print_after_status(before)
            return

        print(f"[OK] Direct API 템플릿 준비 완료!")

        # 7. Direct API 배치 수집
        remaining = [x for x in all_items if x != sample]
        total_remaining = len(remaining)

        print(f"\n[4/6] Direct API 배치 수집 ({total_remaining}개)...")
        print("-" * 70)

        batch_size = args.batch_size
        all_api_success = 0
        start_time = time.time()

        for batch_start in range(0, total_remaining, batch_size):
            batch_end = min(batch_start + batch_size, total_remaining)
            batch = remaining[batch_start:batch_end]

            elapsed = time.time() - start_time
            if batch_start > 0 and elapsed > 0:
                rate = batch_start / elapsed
                eta_sec = (total_remaining - batch_start) / rate
                eta = f", ETA {int(eta_sec // 60)}분 {int(eta_sec % 60)}초"
            else:
                eta = ""

            print(f"\n  배치 [{batch_start+1}~{batch_end}/{total_remaining}]{eta}")

            batch_results = fetcher.fetch_product_details(
                batch,
                on_progress=lambda done, tot: print(
                    f"    진행: {done}/{tot}"
                ),
            )

            all_api_success += len(batch_results)
            print(f"    API 성공: {len(batch_results)}/{len(batch)}개")

            # 배치마다 DB 저장
            saved, errors = save_results_to_db(batch_results, args.force)
            print(f"    DB 저장: {saved}개, 오류: {errors}개")

            # 서버 부하 방지
            if batch_end < total_remaining:
                time.sleep(2)

        elapsed = time.time() - start_time

        # 결과
        print("\n" + "=" * 70)
        print("[5/6] 수집 완료")
        print("=" * 70)
        print(f"전체 대상: {total}개")
        print(f"  Selenium: 1건 ({sample})")
        print(f"  Direct API: {all_api_success}/{total_remaining}건")
        print(f"  API 실패: {total_remaining - all_api_success}건")
        print(f"소요 시간: {int(elapsed // 60)}분 {int(elapsed % 60)}초")
        if total_remaining > 0 and elapsed > 0:
            print(f"성공률: {(all_api_success + 1) / total * 100:.1f}%")
            print(f"속도: {total_remaining / elapsed:.1f}개/초")

        _print_after_status(before)

    finally:
        try:
            analyzer.close()
            print("\n브라우저 종료")
        except Exception:
            pass


def _run_selenium_batch(batch_collector: ProductDetailBatchCollector,
                        items: List[str], force: bool):
    """Selenium 폴백: ProductDetailBatchCollector._fetch_single_item() 루프"""
    repo = ProductDetailRepository()
    success = 0
    fail = 0
    total = len(items)
    start_time = time.time()

    print(f"  Selenium 배치 수집 시작: {total}개 (~3초/개, 예상 {total * 3 // 60}분)")

    for i, item_cd in enumerate(items):
        try:
            result = batch_collector._fetch_single_item(item_cd)
            if result and result.get("item_nm"):
                batch_collector._save_to_db(item_cd, result)
                if force and result.get("sell_price"):
                    _force_update_sell_price(repo, item_cd, result["sell_price"])
                success += 1
            else:
                fail += 1
        except Exception:
            fail += 1

        if (i + 1) % 50 == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            remaining = (total - i - 1) / rate if rate > 0 else 0
            print(
                f"  [{i+1}/{total}] 성공={success}, 실패={fail}, "
                f"속도={rate:.1f}개/초, ETA={int(remaining // 60)}분 {int(remaining % 60)}초"
            )

        time.sleep(0.3)

    elapsed = time.time() - start_time
    print(f"\n  Selenium 완료: {success}/{total}개, {int(elapsed // 60)}분 {int(elapsed % 60)}초")


def _print_after_status(before: Dict):
    """수집 후 현황 출력"""
    after = get_sell_price_status()
    print(f"\n[6/6] 수집 후 현황")
    print(f"  전체 상품: {after['total']}개")
    print(f"  매가 등록: {after['has_price']}개 ({after['has_price']/after['total']*100:.1f}%)")
    print(f"  product_details 없음: {after['no_detail_row']}개")
    print(f"\n  [변화]")
    print(f"  매가 등록 증가: +{after['has_price'] - before['has_price']}개")
    print(f"  product_details 채움: +{before['no_detail_row'] - after['no_detail_row']}개")


if __name__ == "__main__":
    main()
