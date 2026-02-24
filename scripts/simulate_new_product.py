#!/usr/bin/env python3
"""
신상품 도입 현황 시뮬레이션 스크립트

BGF 사이트에서 수집된 데이터를 기반으로 주차별 발주 계획을 시뮬레이션.

Usage:
    python scripts/simulate_new_product.py --month 2026-01
    python scripts/simulate_new_product.py --month 2026-02 --store-id 46513
    python scripts/simulate_new_product.py --collect  # BGF 사이트에서 실시간 수집 후 시뮬레이션
"""

import argparse
import sys
import io

# Windows CP949 콘솔 → UTF-8 래핑 (한글·특수문자 깨짐 방지)
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

from datetime import datetime
from pathlib import Path

# 프로젝트 루트 설정
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.settings.constants import (
    DEFAULT_STORE_ID,
    NEW_PRODUCT_TARGET_TOTAL_SCORE,
    NEW_PRODUCT_MIN_DOIP_RATE,
    NEW_PRODUCT_MIN_DS_RATE,
    NEW_PRODUCT_INTRO_ORDER_QTY,
)
from src.infrastructure.database.repos import NewProductStatusRepository
from src.domain.new_product.score_calculator import (
    calculate_total_score,
    score_to_subsidy,
    calculate_needed_items,
    estimate_score_after_orders,
)
from src.domain.new_product.convenience_order_scheduler import plan_3day_orders
from src.domain.new_product.replacement_strategy import (
    prioritize_missing_items,
    group_new_items_by_category,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

SEP = "=" * 70
SEP_THIN = "-" * 60


def run_simulation(store_id: str, month_ym: str) -> None:
    """시뮬레이션 실행"""
    repo = NewProductStatusRepository(store_id=store_id)

    # 1. 주차별 현황 조회
    weekly = repo.get_weekly_status(store_id, month_ym)
    monthly = repo.get_monthly_summary(store_id, month_ym)

    if not weekly:
        print(f"\n[오류] {month_ym} 데이터가 없습니다.")
        print("  먼저 --collect 옵션으로 BGF 사이트에서 데이터를 수집하세요.")
        return

    # 포맷: YYYYMM → YYYY년 MM월
    display_month = f"{month_ym[:4]}년 {month_ym[4:]}월"

    print(f"\n{SEP}")
    print(f" 신상품 도입 현황 시뮬레이션 ({display_month})")
    print(f" 매장: {store_id} | 목표: 종합 {NEW_PRODUCT_TARGET_TOTAL_SCORE}점+")
    print(SEP)

    # ■ 1. 신상품(전체) 도입률
    print(f"\n■ 1. 신상품(전체) 도입률")
    print(SEP_THIN)
    for w in weekly:
        wk = w.get("week_no", "?")
        sta = w.get("sta_dd", "")
        end = w.get("end_dd", "")
        item_cnt = w.get("item_cnt", 0) or 0
        doip_cnt = w.get("doip_cnt", 0) or 0
        midoip = w.get("midoip_cnt", 0) or 0
        rate = w.get("doip_rate", 0) or 0
        week_cont = w.get("week_cont", "")
        print(
            f"  {month_ym[4:]}월{wk}주차 | 대상기간: {sta}~{end} | "
            f"대상 {item_cnt}개 | 도입 {doip_cnt}개 | "
            f"미도입 {midoip}개 | 도입률 {rate}%"
        )

    # ■ 2. 간편식/디저트 3일 발주 달성률
    print(f"\n■ 2. 간편식/디저트 3일 발주 달성률")
    print(SEP_THIN)
    for w in weekly:
        wk = w.get("week_no", "?")
        ds_item = w.get("ds_item_cnt", 0) or 0
        ds_cnt = w.get("ds_cnt", 0) or 0
        mids = w.get("mids_cnt", 0) or 0
        ds_rate = w.get("ds_rate", 0) or 0
        sta = w.get("sta_dd", "")
        end = w.get("end_dd", "")
        print(
            f"  {month_ym[4:]}월{wk}주차 | 대상기간: {sta}~{end} | "
            f"대상 {ds_item}개 | 달성 {ds_cnt}개 | "
            f"미달성 {mids}개 | 달성률 {ds_rate}%"
        )

    # ■ 3. 현재 종합점수
    print(f"\n■ 3. 현재 종합점수")
    print(SEP_THIN)
    if monthly:
        tot = monthly.get("tot_score", 0) or 0
        amt = monthly.get("supp_pay_amt", 0) or 0
        doip_r = monthly.get("doip_rate", 0) or 0
        ds_r = monthly.get("ds_rate", 0) or 0
        doip_s = monthly.get("doip_score", 0) or 0
        ds_s = monthly.get("ds_score", 0) or 0
        print(f"  도입률 {doip_r}% ({doip_s}점) + 달성률 {ds_r}% ({ds_s}점)")
        print(f"  = 종합 {tot}점 -> 예상지원금 {amt:,}원")
    else:
        print("  월별 합계 데이터 없음")

    # ■ 4. 주차별 발주 계획 (미도입 상품)
    print(f"\n■ 4. 주차별 발주 계획 (미도입 상품)")
    print(SEP_THIN)

    total_new_orders = 0
    for w in weekly:
        wk = w.get("week_no", "?")
        midoip = w.get("midoip_cnt", 0) or 0
        doip_cnt = w.get("doip_cnt", 0) or 0
        item_cnt = w.get("item_cnt", 0) or 0

        items = repo.get_missing_items(store_id, month_ym, wk, "midoip")
        orderable = [i for i in items if i.get("ord_pss_nm") in ("발주가능", "가능")]
        not_orderable = [i for i in items if i.get("ord_pss_nm") not in ("발주가능", "가능")]

        print(f"\n  [{month_ym[4:]}월{wk}주차] 미도입 {midoip}개 -> 발주가능 {len(orderable)}개:")

        prioritized = prioritize_missing_items(items, 0)
        for idx, item in enumerate(prioritized[:20], 1):
            ord_status = item.get("ord_pss_nm", "")
            cd = item.get("item_cd", "")
            nm = item.get("item_nm", "")[:25]
            sm = item.get("small_nm", "")
            marker = "O" if ord_status in ("발주가능", "가능") else "X"
            print(f"    {idx:3}. {marker} {cd} | {nm} | 소분류: {sm} | {ord_status} | qty={NEW_PRODUCT_INTRO_ORDER_QTY}")

        if len(prioritized) > 20:
            print(f"    ... 외 {len(prioritized)-20}개")

        if orderable and item_cnt > 0:
            est_rate = ((doip_cnt + len(orderable)) / item_cnt * 100) if item_cnt > 0 else 0
            cur_rate = w.get("doip_rate", 0) or 0
            print(f"  -> 발주 후 예상 도입률: {est_rate:.1f}% (현재 {cur_rate}% -> +{est_rate-cur_rate:.1f}%p)")
            total_new_orders += len(orderable)

        if not_orderable:
            print(f"  * 발주불가 {len(not_orderable)}개 (발주정지/단종)")

    # ■ 5. 주차별 3일 발주 스케줄 (간편식/디저트)
    print(f"\n■ 5. 주차별 3일 발주 스케줄 (간편식/디저트)")
    print(SEP_THIN)

    total_ds_orders = 0
    for w in weekly:
        wk = w.get("week_no", "?")
        mids = w.get("mids_cnt", 0) or 0
        sta = w.get("sta_dd", "")
        end = w.get("end_dd", "")

        if mids == 0:
            print(f"\n  [{month_ym[4:]}월{wk}주차] 미달성 없음 (전수 달성)")
            continue

        ds_items = repo.get_missing_items(store_id, month_ym, wk, "mids")
        print(f"\n  [{month_ym[4:]}월{wk}주차] 대상기간: {sta}~{end} | 미달성 {mids}개:")

        for item in ds_items[:15]:
            cd = item.get("item_cd", "")
            nm = item.get("item_nm", "")[:20]

            plan = plan_3day_orders(cd, sta, end)
            plan_str = ", ".join(plan) if plan else "계획 없음"
            achieved = "달성" if len(plan) >= 3 else f"미달성 ({len(plan)}/3회)"
            print(f"    {cd}: {nm} -> 발주일 {plan_str} ({achieved})")
            total_ds_orders += 1

        if len(ds_items) > 15:
            print(f"    ... 외 {len(ds_items)-15}개")

    # ■ 6. 비인기상품 교체 대상
    print(f"\n■ 6. 비인기상품 교체 대상")
    print(SEP_THIN)

    all_missing = repo.get_missing_items(store_id, month_ym, item_type="midoip")
    orderable_items = [i for i in all_missing if i.get("ord_pss_nm") in ("발주가능", "가능")]
    groups = group_new_items_by_category(orderable_items)

    if groups:
        print("  신상품 도입 -> 동일 카테고리 비인기상품 발주정지 대상:")
        for small_nm, items in sorted(groups.items()):
            print(f"    [{small_nm}] 신상품 {len(items)}개 도입 -> 비인기상품 {len(items)}개 정지 대상")
    else:
        print("  교체 대상 없음 (발주가능 미도입 상품 없음)")

    # ■ 7. 시뮬레이션 결과
    print(f"\n■ 7. 시뮬레이션 결과")
    print(SEP_THIN)

    if monthly:
        cur_tot = monthly.get("tot_score", 0) or 0
        cur_amt = monthly.get("supp_pay_amt", 0) or 0
        cur_doip_r = monthly.get("doip_rate", 0) or 0
        cur_ds_r = monthly.get("ds_rate", 0) or 0
        cur_doip_s = monthly.get("doip_score", 0) or 0
        cur_ds_s = monthly.get("ds_score", 0) or 0

        print(f"  현재: 종합 {cur_tot}점 -> 예상지원금 {cur_amt:,}원")

        if total_new_orders > 0 or total_ds_orders > 0:
            # 추가 발주가 있을 때만 시뮬레이션
            sim = estimate_score_after_orders(
                current_doip_cnt=monthly.get("doip_cnt", 0) or 0,
                current_ds_cnt=monthly.get("ds_cnt", 0) or 0,
                total_doip_items=monthly.get("doip_item_cnt", 1) or 1,
                total_ds_items=monthly.get("ds_item_cnt", 1) or 1,
                new_doip_orders=total_new_orders,
                new_ds_orders=total_ds_orders,
            )

            print(f"  발주 후 예상:")
            print(f"    도입률 {sim['doip_rate']}% ({sim['doip_score']}점) + "
                  f"달성률 {sim['ds_rate']}% ({sim['ds_score']}점)")
            print(f"    = 종합 {sim['total_score']}점 -> 예상지원금 {sim['subsidy']:,}원")

            diff = sim['subsidy'] - cur_amt
            if diff > 0:
                print(f"    -> 지원금 증가: +{diff:,}원")
            elif diff < 0:
                print(f"    -> 지원금 감소: {diff:,}원")
            else:
                print(f"    -> 지원금 변동 없음")

            gap = NEW_PRODUCT_TARGET_TOTAL_SCORE - sim['total_score']
        else:
            print(f"  (미도입 상품 상세 데이터 없음 - 추가 발주 시뮬레이션 생략)")
            gap = NEW_PRODUCT_TARGET_TOTAL_SCORE - cur_tot

        if gap > 0:
            needed = calculate_needed_items(
                cur_doip_r, NEW_PRODUCT_MIN_DOIP_RATE,
                monthly.get("doip_item_cnt", 1) or 1,
                monthly.get("doip_cnt", 0) or 0,
            )
            print(f"\n  * 목표 {NEW_PRODUCT_TARGET_TOTAL_SCORE}점까지 {gap}점 부족")
            if needed > 0:
                print(f"  * 도입률 {NEW_PRODUCT_MIN_DOIP_RATE}% 달성에 {needed}개 추가 도입 필요")
        else:
            print(f"\n  >> 목표 {NEW_PRODUCT_TARGET_TOTAL_SCORE}점 달성!")
    else:
        print("  월별 합계 데이터 없음")

    print(f"\n{SEP}\n")


def collect_and_simulate(store_id: str) -> None:
    """BGF 사이트에서 수집 후 시뮬레이션"""
    from src.sales_analyzer import initialize_driver_for_manual

    print("\n[1/3] BGF 사이트 로그인 중...")
    driver = initialize_driver_for_manual(store_id=store_id)
    if not driver:
        print("[오류] 로그인 실패")
        return

    print("[2/3] 신상품 도입 현황 수집 중...")
    from src.collectors.new_product_collector import NewProductCollector

    collector = NewProductCollector(driver=driver, store_id=store_id)
    try:
        if not collector.navigate_to_menu():
            print("[오류] 메뉴 이동 실패")
            return

        result = collector.collect_and_save()
        if not result.get("success"):
            print("[오류] 수집 실패")
            return

        print(f"  수집 완료: {result.get('weekly_count', 0)}주차")
    finally:
        collector.close_menu()

    print("[3/3] 시뮬레이션 실행...")
    month_ym = datetime.now().strftime("%Y%m")
    run_simulation(store_id, month_ym)


def main():
    parser = argparse.ArgumentParser(description="신상품 도입 현황 시뮬레이션")
    parser.add_argument(
        "--month", type=str,
        help="시뮬레이션 대상 월 (YYYY-MM 또는 YYYYMM, 기본: 현재 월)"
    )
    parser.add_argument(
        "--store-id", type=str, default=DEFAULT_STORE_ID,
        help=f"매장 코드 (기본: {DEFAULT_STORE_ID})"
    )
    parser.add_argument(
        "--collect", action="store_true",
        help="BGF 사이트에서 실시간 수집 후 시뮬레이션"
    )

    args = parser.parse_args()

    if args.collect:
        collect_and_simulate(args.store_id)
    else:
        month_ym = args.month or datetime.now().strftime("%Y%m")
        # YYYY-MM → YYYYMM 변환
        month_ym = month_ym.replace("-", "")
        run_simulation(args.store_id, month_ym)


if __name__ == "__main__":
    main()
