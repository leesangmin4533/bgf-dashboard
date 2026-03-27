# -*- coding: utf-8 -*-
"""
카테고리 대규모 업데이트 — 일회성 벌크 스크립트

Direct API (/stbjz00/selItemDetailSearch)로 전체 DB 상품의
카테고리 3단계(대/중/소)를 일괄 수집하여 강제 갱신합니다.

사용법:
  python scripts/category_bulk_update.py --test           # 10개만 테스트
  python scripts/category_bulk_update.py --missing-only   # 미수집 상품만
  python scripts/category_bulk_update.py --force-all      # 전체 강제 갱신
  python scripts/category_bulk_update.py --dry-run        # 대상만 출력
  python scripts/category_bulk_update.py --verify         # 결과 검증만

프로세스:
  1. BGF 로그인 (Selenium)
  2. XHR 인터셉터 설치 → 팝업 1회 트리거 → body 템플릿 캡처
  3. Direct API 배치 호출 (concurrency=5, 500개씩)
  4. SSV 파싱 → 카테고리 정보 추출
  5. DB 강제 갱신 (product_details, products, mid_categories)
"""

import sys
import io

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

import os
import time
import argparse
from pathlib import Path
from datetime import datetime

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.utils.logger import get_logger

logger = get_logger("category_bulk_update")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="카테고리 대규모 업데이트 (일회성)"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--test", action="store_true",
        help="10개만 테스트 수집",
    )
    group.add_argument(
        "--missing-only", action="store_true",
        help="카테고리 미수집 상품만 (large_cd IS NULL)",
    )
    group.add_argument(
        "--force-all", action="store_true",
        help="전체 상품 카테고리 강제 갱신",
    )
    group.add_argument(
        "--dry-run", action="store_true",
        help="대상 상품만 출력 (수집 안 함)",
    )
    group.add_argument(
        "--verify", action="store_true",
        help="DB 카테고리 현황 검증만",
    )
    parser.add_argument(
        "--batch-size", type=int, default=500,
        help="배치 크기 (기본: 500)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=5,
        help="API 동시 요청 수 (기본: 5)",
    )
    parser.add_argument(
        "--delay-ms", type=int, default=50,
        help="요청 간 딜레이 ms (기본: 50)",
    )
    parser.add_argument(
        "--cooldown", type=int, default=3,
        help="배치 간 쿨다운 초 (기본: 3)",
    )
    return parser.parse_args()


def verify_db():
    """DB 카테고리 현황 검증"""
    from src.infrastructure.database.repos.product_detail_repo import (
        ProductDetailRepository,
    )

    repo = ProductDetailRepository()
    conn = repo._get_conn()
    try:
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM products")
        total = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM product_details")
        pd_total = c.fetchone()[0]

        c.execute(
            "SELECT COUNT(*) FROM product_details WHERE large_cd IS NOT NULL"
        )
        has_large = c.fetchone()[0]

        c.execute(
            "SELECT COUNT(*) FROM product_details WHERE small_cd IS NOT NULL"
        )
        has_small = c.fetchone()[0]

        c.execute(
            "SELECT COUNT(*) FROM product_details WHERE class_nm IS NOT NULL"
        )
        has_class = c.fetchone()[0]

        c.execute(
            "SELECT COUNT(*) FROM product_details WHERE large_cd IS NULL"
        )
        null_large = c.fetchone()[0]

        c.execute("""
            SELECT COUNT(*) FROM products p
            WHERE NOT EXISTS (
                SELECT 1 FROM product_details pd WHERE pd.item_cd = p.item_cd
            )
        """)
        no_pd = c.fetchone()[0]

        print("=" * 50)
        print("  카테고리 현황 검증")
        print("=" * 50)
        print(f"  전체 상품 (products):      {total:>6}")
        print(f"  product_details 존재:      {pd_total:>6}")
        print(f"  large_cd 있음:             {has_large:>6}  "
              f"({has_large / pd_total * 100:.1f}%)" if pd_total else "")
        print(f"  small_cd 있음:             {has_small:>6}")
        print(f"  class_nm 있음:             {has_class:>6}")
        print(f"  large_cd NULL:             {null_large:>6}")
        print(f"  product_details 없음:      {no_pd:>6}")
        print("=" * 50)

        # 대분류 분포
        c.execute("""
            SELECT pd.large_cd,
                   (SELECT large_nm FROM mid_categories mc
                    WHERE mc.large_cd = pd.large_cd LIMIT 1) as nm,
                   COUNT(*) as cnt
            FROM product_details pd
            WHERE pd.large_cd IS NOT NULL
            GROUP BY pd.large_cd
            ORDER BY pd.large_cd
        """)
        print("\n  대분류 분포:")
        for row in c.fetchall():
            print(f"    {row[0]}: {row[1] or '?':<15} ({row[2]})")

        # 중분류 수
        c.execute("SELECT COUNT(*) FROM mid_categories")
        mc = c.fetchone()[0]
        c.execute(
            "SELECT COUNT(*) FROM mid_categories WHERE large_cd IS NOT NULL"
        )
        mc_large = c.fetchone()[0]
        print(f"\n  중분류 (mid_categories): {mc}개 (대분류 있음: {mc_large})")

        return null_large + no_pd  # 미수집 총수

    finally:
        conn.close()


def get_target_items(mode: str, limit: int = None):
    """수집 대상 상품 코드 목록"""
    from src.infrastructure.database.repos.product_detail_repo import (
        ProductDetailRepository,
    )

    repo = ProductDetailRepository()

    if mode == "missing":
        items = repo.get_items_needing_detail_fetch(limit or 9999)
        logger.info(f"미수집 상품: {len(items)}개")
    elif mode == "all":
        items = repo.get_all_product_codes()
        logger.info(f"전체 상품: {len(items)}개")
    elif mode == "test":
        all_items = repo.get_all_product_codes()
        items = all_items[:10]
        logger.info(f"테스트 상품: {len(items)}개 (전체 {len(all_items)}개 중)")
    else:
        items = []

    return items


def setup_driver():
    """Selenium 드라이버 초기화 + BGF 로그인 + 발주 화면 진입"""
    from src.sales_analyzer import SalesAnalyzer
    from src.collectors.order_prep_collector import OrderPrepCollector

    logger.info("Selenium 드라이버 초기화...")
    sa = SalesAnalyzer()
    sa.setup_driver()
    sa.connect()
    time.sleep(2)

    logger.info("BGF 로그인...")
    if not sa.do_login():
        raise RuntimeError("BGF 로그인 실패")
    time.sleep(2)

    logger.info("발주 화면 진입 (팝업 인터셉터 설치)...")
    collector = OrderPrepCollector(driver=sa.driver, save_to_db=False)
    if not collector.navigate_to_menu():
        raise RuntimeError("발주 화면 진입 실패")
    time.sleep(2)

    # XHR 인터셉터 설치
    sa.driver.execute_script("""
        if (!window.__popupCaptures) {
            window.__popupCaptures = [];
            var origOpen = XMLHttpRequest.prototype.open;
            var origSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(method, url) {
                this.__captureUrl = url;
                return origOpen.apply(this, arguments);
            };
            XMLHttpRequest.prototype.send = function(body) {
                if (this.__captureUrl &&
                    (this.__captureUrl.indexOf('selItemDetail') >= 0)) {
                    window.__popupCaptures.push({
                        url: this.__captureUrl,
                        bodyPreview: typeof body === 'string'
                            ? body.substring(0, 5000) : ''
                    });
                }
                return origSend.apply(this, arguments);
            };
            console.log('[CategoryBulk] XHR 인터셉터 설치 완료');
        }
    """)

    # OrderPrepCollector도 반환 (팝업 트리거용)
    sa._order_collector = collector
    return sa


def trigger_popup_for_template(sa):
    """팝업 1회 트리거하여 API 템플릿 캡처"""
    from src.collectors.product_detail_batch_collector import (
        ProductDetailBatchCollector,
    )

    batch_collector = ProductDetailBatchCollector(sa.driver)

    # DB에서 아무 상품 하나 가져오기
    from src.infrastructure.database.repos.product_detail_repo import (
        ProductDetailRepository,
    )
    repo = ProductDetailRepository()
    all_items = repo.get_all_product_codes()
    if not all_items:
        logger.error("DB에 상품이 없습니다")
        return False

    test_item = all_items[0]
    logger.info(f"템플릿 캡처용 팝업 트리거: {test_item}")

    # 팝업 열기 시도
    try:
        batch_collector._fetch_single_item(test_item)
    except Exception as e:
        logger.warning(f"팝업 트리거 중 오류 (무시): {e}")
    time.sleep(1)

    # 캡처 확인
    caps = sa.driver.execute_script(
        "return (window.__popupCaptures || []).length;"
    )
    logger.info(f"캡처된 요청 수: {caps}")

    return caps >= 1


def run_bulk_update(sa, item_codes, args):
    """Direct API로 카테고리 배치 수집 + DB 갱신"""
    from src.collectors.direct_popup_fetcher import (
        DirectPopupFetcher,
        extract_product_detail,
    )
    from src.infrastructure.database.repos.product_detail_repo import (
        ProductDetailRepository,
    )

    repo = ProductDetailRepository()
    fetcher = DirectPopupFetcher(
        sa.driver,
        concurrency=args.concurrency,
        timeout_ms=8000,
    )

    # 템플릿 캡처
    if not fetcher.capture_template():
        logger.error("Direct API 템플릿 캡처 실패")
        logger.info("팝업 트리거로 템플릿 확보 시도...")
        trigger_popup_for_template(sa)
        time.sleep(1)
        if not fetcher.capture_template():
            logger.error("템플릿 확보 실패 — 중단")
            return {"total": len(item_codes), "success": 0, "fail": 0}

    total = len(item_codes)
    batch_size = args.batch_size
    total_success = 0
    total_fail = 0
    start_time = time.time()

    logger.info(f"배치 수집 시작: {total}개, batch={batch_size}, "
                f"concurrency={args.concurrency}")

    for batch_idx in range(0, total, batch_size):
        batch = item_codes[batch_idx:batch_idx + batch_size]
        batch_num = batch_idx // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size

        logger.info(
            f"=== 배치 {batch_num}/{total_batches} "
            f"({len(batch)}개, {batch_idx+1}~{batch_idx+len(batch)}) ==="
        )

        # Direct API 배치 호출 (카테고리만 필요 → ord 생략)
        raw_results = fetcher.fetch_items_batch(
            batch,
            include_ord=False,
            delay_ms=args.delay_ms,
            on_progress=lambda done, t: logger.info(
                f"  API 진행: {done}/{t}"
            ) if done % 100 == 0 else None,
        )

        # 파싱 + DB 저장
        batch_items = []
        batch_fail = 0

        for item_cd, data in raw_results.items():
            extracted = extract_product_detail(
                data.get("detail"), None, item_cd
            )
            if extracted and extracted.get("mid_cd"):
                batch_items.append(extracted)
            else:
                batch_fail += 1

        # API 응답 없는 건
        no_response = set(batch) - set(raw_results.keys())
        batch_fail += len(no_response)
        if no_response:
            logger.warning(
                f"  API 응답 없음: {len(no_response)}건 "
                f"(예: {list(no_response)[:3]})"
            )

        # DB 강제 갱신
        if batch_items:
            saved = repo.bulk_update_category_force(batch_items)
            total_success += saved
            logger.info(f"  DB 저장: {saved}건")
        total_fail += batch_fail

        elapsed = time.time() - start_time
        processed = batch_idx + len(batch)
        rate = processed / elapsed if elapsed > 0 else 0
        eta = (total - processed) / rate if rate > 0 else 0
        logger.info(
            f"  누적: {total_success}성공, {total_fail}실패 | "
            f"{elapsed:.0f}초 경과, {rate:.0f}건/초, "
            f"ETA {eta:.0f}초"
        )

        # 배치 간 쿨다운
        if batch_idx + batch_size < total:
            logger.info(f"  쿨다운 {args.cooldown}초...")
            time.sleep(args.cooldown)

    total_elapsed = time.time() - start_time
    logger.info(
        f"\n{'=' * 50}\n"
        f"  완료: {total_success}/{total}건 성공, "
        f"{total_fail}건 실패\n"
        f"  소요: {total_elapsed:.1f}초 "
        f"({total / total_elapsed:.0f}건/초)\n"
        f"{'=' * 50}"
    )

    return {
        "total": total,
        "success": total_success,
        "fail": total_fail,
        "elapsed": total_elapsed,
    }


def main():
    args = parse_args()

    # 검증만
    if args.verify:
        missing = verify_db()
        if missing == 0:
            print("\n  모든 상품 카테고리 수집 완료!")
        else:
            print(f"\n  미수집 상품: {missing}개")
        return

    # 대상 결정
    if args.test:
        mode = "test"
    elif args.missing_only:
        mode = "missing"
    elif args.force_all:
        mode = "all"
    elif args.dry_run:
        mode = "all"
    else:
        mode = "missing"

    item_codes = get_target_items(mode)

    if not item_codes:
        print("수집 대상 없음")
        return

    # dry-run
    if args.dry_run:
        print(f"\n대상 상품: {len(item_codes)}개")
        for i, code in enumerate(item_codes[:20]):
            print(f"  {i+1}. {code}")
        if len(item_codes) > 20:
            print(f"  ... 외 {len(item_codes) - 20}개")
        return

    # 실행
    sa = None
    try:
        sa = setup_driver()
        result = run_bulk_update(sa, item_codes, args)

        # 결과 검증
        print("\n--- 갱신 후 검증 ---")
        verify_db()

    except KeyboardInterrupt:
        logger.info("사용자 중단")
    except Exception as e:
        logger.error(f"실행 오류: {e}", exc_info=True)
    finally:
        if sa and hasattr(sa, "driver") and sa.driver:
            try:
                sa.driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    main()
