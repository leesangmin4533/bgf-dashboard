#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
발주단위(order_unit_qty) Direct API 덤프 수집 테스트

DirectPopupFetcher로 /stbjz00/selItemDetailSearch API를 직접 호출하여
dsItemDetail.ORD_UNIT_QTY를 배치 수집. Selenium 팝업 조작 없이 빠른 수집.

실행:
    python scripts/test_order_unit_collect.py
    python scripts/test_order_unit_collect.py --store 46704
"""
import sys
import io
import time
import argparse
from pathlib import Path

# Windows CP949 -> UTF-8
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.utils.logger import get_logger
logger = get_logger("test_order_unit_collect")


# -- 테스트 대상 --
TEST_ITEMS = [
    # (item_cd, db_unit, expected, label)
    # 사고 상품 (DB=1, 실제 다름)
    ("8801094962104", 1, 6, "코카)스프라이트제로P500"),
    ("2202000037309", 1, 100, "친환경)배달전용봉투특대"),
    # DB>1 정상 (교차 검증 기준)
    ("8801094023218", 6, 6, "코카)코카콜라제로제로350"),
    ("8801094013004", 6, 6, "코카)코카콜라캔250ml"),
    ("8594404005782", 4, 4, "코젤라거캔500ml"),
    ("8809618910303", 10, 10, "엉덩이탐정)복숭아아이스"),
    ("8809505348301", 10, 10, "이천)쌀라떼파우더"),
    # DB=1 의심 (비푸드 카테고리)
    ("8809027552224", 1, None, "다이닝)bhc뿌링팝콘"),
    ("8801111961677", 1, None, "크라운)신짱가마솥누룽지"),
]


def step_login(store_id: str):
    """BGF 사이트 로그인"""
    print("\n" + "=" * 60)
    print(f"[Step 1] BGF 로그인 (store={store_id})")
    print("=" * 60)

    from src.sales_analyzer import SalesAnalyzer
    analyzer = SalesAnalyzer(store_id=store_id)
    analyzer.setup_driver()
    analyzer.connect()

    if not analyzer.do_login():
        raise RuntimeError("로그인 실패")

    print("[OK] 로그인 성공")
    time.sleep(2)
    return analyzer


def step_navigate_and_capture(analyzer, store_id):
    """단품별발주 화면 이동 + Direct API 템플릿 캡처"""
    print("\n" + "=" * 60)
    print("[Step 2] 단품별발주 화면 이동 + 템플릿 캡처")
    print("=" * 60)

    driver = analyzer.driver

    # 팝업 닫기 (로그인 후 공지 등)
    try:
        analyzer.close_popup()
        time.sleep(1)
    except Exception:
        pass

    # OrderPrepCollector로 단품별발주 화면 진입
    from src.collectors.order_prep_collector import OrderPrepCollector
    prep_collector = OrderPrepCollector(driver=driver, save_to_db=False)

    print("  단품별발주 메뉴 이동...")
    if not prep_collector.navigate_to_menu():
        print("[FAIL] 메뉴 이동 실패")
        return None

    print("  발주일 선택...")
    if not prep_collector.select_order_date():
        print("[WARN] 발주일 선택 실패 (계속 진행)")

    time.sleep(1)

    # XHR 인터셉터 설치
    print("  XHR 인터셉터 설치...")
    driver.execute_script("""
        if (!window.__popupCaptures) {
            window.__popupCaptures = [];
            var origFetch = window.fetch;
            window.fetch = function(url, opts) {
                if (url && (url.indexOf('selItemDetail') >= 0)) {
                    window.__popupCaptures.push({
                        url: url,
                        bodyPreview: (opts && opts.body) ? opts.body.substring(0, 2000) : ''
                    });
                }
                return origFetch.apply(this, arguments);
            };
        }
    """)

    # 첫 번째 상품으로 collect_for_item 호출 → 팝업 XHR 캡처
    trigger_item = TEST_ITEMS[2][0]  # 코카콜라제로제로350
    print(f"  트리거 상품 조회: {trigger_item}...")
    try:
        prep_collector.collect_for_item(trigger_item)
    except Exception as e:
        print(f"  [WARN] 트리거 조회 오류 (무시): {e}")
    time.sleep(1)

    # DirectPopupFetcher 템플릿 캡처
    from src.collectors.direct_popup_fetcher import DirectPopupFetcher
    fetcher = DirectPopupFetcher(driver=driver, concurrency=5, timeout_ms=8000)

    if fetcher.capture_template():
        print(f"[OK] 템플릿 캡처 성공")
        return fetcher

    print("[FAIL] 템플릿 캡처 실패 — OrderPrepCollector 개별 수집으로 폴백")
    return prep_collector


def step_collect_via_direct_api(fetcher, item_codes):
    """Direct API 배치 수집"""
    print("\n" + "=" * 60)
    print(f"[Step 3] Direct API 배치 수집 ({len(item_codes)}개)")
    print("=" * 60)

    from src.collectors.direct_popup_fetcher import extract_product_detail

    results = fetcher.fetch_items_batch(
        item_codes=item_codes,
        include_ord=True,
        delay_ms=100,
    )

    parsed = {}
    for item_cd, raw in results.items():
        detail_row = raw.get("detail")
        ord_row = raw.get("ord")

        if detail_row or ord_row:
            info = extract_product_detail(detail_row, ord_row, item_cd)
            if info:
                unit = info.get("order_unit_qty")
                parsed[item_cd] = {
                    "item_cd": item_cd,
                    "item_nm": info.get("item_nm", ""),
                    "order_unit_qty": unit,
                    "source": "direct_api",
                }
                print(f"  {item_cd}: unit={unit} ({info.get('item_nm', '')[:25]})")
            else:
                print(f"  {item_cd}: 파싱 실패 (detail/ord 비어있음)")
        else:
            print(f"  {item_cd}: API 응답 없음")

    print(f"\n[OK] 수집 완료: {len(parsed)}/{len(item_codes)}건")
    return parsed


def step_collect_via_prep(prep_collector, item_codes):
    """OrderPrepCollector 개별 수집 폴백"""
    print("\n" + "=" * 60)
    print(f"[Step 3] OrderPrepCollector 개별 수집 ({len(item_codes)}개)")
    print("=" * 60)

    parsed = {}
    for i, item_cd in enumerate(item_codes):
        try:
            result = prep_collector.collect_for_item(item_cd)
            if result and result.get("success"):
                unit = result.get("order_unit_qty")
                parsed[item_cd] = {
                    "item_cd": item_cd,
                    "item_nm": result.get("item_nm", ""),
                    "order_unit_qty": unit,
                    "source": "order_prep",
                }
                print(f"  [{i+1}/{len(item_codes)}] {item_cd}: unit={unit}")
            else:
                print(f"  [{i+1}/{len(item_codes)}] {item_cd}: 수집 실패")
        except Exception as e:
            print(f"  [{i+1}/{len(item_codes)}] {item_cd}: 오류 - {e}")
        time.sleep(0.5)

    print(f"\n[OK] 수집 완료: {len(parsed)}/{len(item_codes)}건")
    return parsed


def step_compare(site_map):
    """DB값 vs Direct API 수집값 비교"""
    print("\n" + "=" * 60)
    print("[Step 4] 결과 비교 (DB vs Direct API)")
    print("=" * 60)

    print(f"\n{'상품코드':<16} {'DB':>4} {'API':>4} {'기대':>4} {'판정':<18} 상품명")
    print("-" * 90)

    match_cnt = 0
    mismatch_cnt = 0
    fail_cnt = 0
    discovered = 0

    for item_cd, db_unit, expected, label in TEST_ITEMS:
        site_data = site_map.get(item_cd)
        if not site_data:
            print(f"{item_cd:<16} {db_unit:>4} {'FAIL':>4} {str(expected or '?'):>4} {'수집실패':<18} {label}")
            fail_cnt += 1
            continue

        site_unit = site_data.get("order_unit_qty")
        site_str = str(site_unit) if site_unit is not None else "None"

        if expected is not None:
            if site_unit == expected:
                if db_unit == expected:
                    verdict = "OK (DB=API)"
                    match_cnt += 1
                else:
                    verdict = "MISMATCH(DB오류!)"
                    mismatch_cnt += 1
            else:
                verdict = f"UNEXPECTED(api={site_str})"
                mismatch_cnt += 1
        else:
            if site_unit is not None and site_unit != db_unit:
                verdict = f"DISCOVERED(api={site_str})"
                discovered += 1
            elif site_unit is None:
                verdict = "API=None (빈값)"
                fail_cnt += 1
            else:
                verdict = "OK (둘다 동일)"
                match_cnt += 1

        print(f"{item_cd:<16} {db_unit:>4} {site_str:>4} {str(expected or '?'):>4} {verdict:<18} {label}")

    print("-" * 90)
    print(f"일치: {match_cnt}, 불일치: {mismatch_cnt}, 발견: {discovered}, 실패: {fail_cnt}")

    return mismatch_cnt, discovered


def main():
    parser = argparse.ArgumentParser(description="발주단위 Direct API 덤프 수집 테스트")
    parser.add_argument("--store", default="46513", help="점포 코드 (기본: 46513)")
    args = parser.parse_args()

    analyzer = None
    try:
        # Step 1: 로그인
        analyzer = step_login(args.store)

        # Step 2: 화면 이동 + 템플릿 캡처
        result = step_navigate_and_capture(analyzer, args.store)
        if not result:
            print("\n[ERROR] 화면 이동 실패 - 테스트 중단")
            return

        item_codes = [t[0] for t in TEST_ITEMS]

        # Step 3: Direct API 배치 수집 또는 개별 수집 폴백
        from src.collectors.direct_popup_fetcher import DirectPopupFetcher
        if isinstance(result, DirectPopupFetcher):
            site_map = step_collect_via_direct_api(result, item_codes)
        else:
            # OrderPrepCollector 개별 수집 폴백
            site_map = step_collect_via_prep(result, item_codes)

        if not site_map:
            print("\n[ERROR] 수집 결과 0건")
            return

        # Step 4: 비교
        mismatch, discovered = step_compare(site_map)

        print("\n" + "=" * 60)
        if mismatch > 0:
            print(f"[ALERT] DB 불일치 {mismatch}건! product_details 갱신 필요")
        if discovered > 0:
            print(f"[INFO] 의심 상품 중 {discovered}건 실제 불일치 발견")
        if mismatch == 0 and discovered == 0:
            print("[OK] 전체 일치 - Direct API 수집 로직 정상")
        print("=" * 60)

    except Exception as e:
        logger.error(f"테스트 실패: {e}", exc_info=True)
    finally:
        if analyzer and analyzer.driver:
            try:
                analyzer.driver.quit()
            except Exception:
                pass

    print("\n=== 테스트 완료 ===")


if __name__ == "__main__":
    main()
