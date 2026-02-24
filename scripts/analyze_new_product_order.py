#!/usr/bin/env python3
"""
신상품 도입 현황 분석 및 발주목록 산출

DB에 저장된 신상품 데이터를 비즈니스 로직으로 처리하여:
1. 현재 현황 요약
2. 미도입 상품 발주 우선순위 목록
3. 3일발주 미달성 스케줄 (오늘 발주 여부)
4. 소분류별 교체 대상 (참고용)
5. 전량 발주 시 예상 점수 시뮬레이션

Usage:
    python scripts/analyze_new_product_order.py --month 2026-02
    python scripts/analyze_new_product_order.py  # 기본: 현재월
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

from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.infrastructure.database.repos import NewProductStatusRepository
from src.domain.new_product.score_calculator import (
    calculate_total_score,
    score_to_subsidy,
    estimate_score_after_orders,
    calculate_needed_items,
    DEFAULT_SUBSIDY_TABLE,
)
from src.domain.new_product.replacement_strategy import (
    prioritize_missing_items,
    group_new_items_by_category,
)
from src.domain.new_product.convenience_order_scheduler import (
    plan_3day_orders,
    should_order_today,
    get_remaining_orders_needed,
)
from src.settings.constants import (
    DEFAULT_STORE_ID,
    NEW_PRODUCT_TARGET_TOTAL_SCORE,
    NEW_PRODUCT_INTRO_ORDER_QTY,
    NEW_PRODUCT_DS_MIN_ORDERS,
)


def parse_args():
    parser = argparse.ArgumentParser(description="신상품 발주 분석")
    parser.add_argument(
        "--month", "-m",
        default=None,
        help="분석 대상 월 (YYYY-MM 또는 YYYYMM, 기본: 현재월)",
    )
    parser.add_argument(
        "--store", "-s",
        default=DEFAULT_STORE_ID,
        help=f"매장 코드 (기본: {DEFAULT_STORE_ID})",
    )
    parser.add_argument(
        "--today", "-t",
        default=None,
        help="기준일 (YYYY-MM-DD, 기본: 오늘)",
    )
    return parser.parse_args()


def normalize_month(month_str):
    """YYYY-MM 또는 YYYYMM -> YYYYMM"""
    if month_str is None:
        return datetime.now().strftime("%Y%m")
    return month_str.replace("-", "")


def format_month(ym):
    """YYYYMM -> YYYY-MM"""
    return f"{ym[:4]}-{ym[4:]}"


def parse_ds_yn(ds_yn):
    """DS_YN 파싱: '1/3(미달성)' -> (placed=1, required=3)"""
    if not ds_yn:
        return 0, NEW_PRODUCT_DS_MIN_ORDERS
    try:
        parts = ds_yn.split("(")[0].split("/")
        placed = int(parts[0])
        required = int(parts[1]) if len(parts) > 1 else NEW_PRODUCT_DS_MIN_ORDERS
        return placed, required
    except (ValueError, IndexError):
        return 0, NEW_PRODUCT_DS_MIN_ORDERS


def section_separator(title, width=70):
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}")


def main():
    args = parse_args()
    month_ym = normalize_month(args.month)
    store_id = args.store
    today = args.today or datetime.now().strftime("%Y-%m-%d")

    print(f"[신상품 발주 분석] 매장={store_id}, 월={format_month(month_ym)}, 기준일={today}")
    print(f"{'=' * 70}")

    repo = NewProductStatusRepository(store_id=store_id)

    # ============================================================
    # Section 1: 현재 현황
    # ============================================================
    section_separator("1. 현재 현황")

    monthly = repo.get_monthly_summary(store_id, month_ym)
    weekly = repo.get_weekly_status(store_id, month_ym)

    if not monthly:
        print(f"  [!] {format_month(month_ym)} 월별 합계 데이터 없음")
        print("  먼저 insert_new_product_data 스크립트를 실행하세요.")
        return

    doip_rate = monthly.get("doip_rate", 0)
    doip_score = monthly.get("doip_score", 0)
    ds_rate = monthly.get("ds_rate", 0)
    ds_score = monthly.get("ds_score", 0)
    tot_score = monthly.get("tot_score", 0)
    supp_pay_amt = monthly.get("supp_pay_amt", 0)
    doip_item_cnt = monthly.get("doip_item_cnt", 0)
    doip_cnt = monthly.get("doip_cnt", 0)
    ds_item_cnt = monthly.get("ds_item_cnt", 0)
    ds_cnt = monthly.get("ds_cnt", 0)

    print(f"\n  [도입률]  {doip_rate}% ({doip_cnt}/{doip_item_cnt}) -> {doip_score}점/80")
    print(f"  [달성률]  {ds_rate}% ({ds_cnt}/{ds_item_cnt}) -> {ds_score}점/20")
    print(f"  [종합]    {tot_score}점/100 -> 지원금 {supp_pay_amt:,}원")

    # 목표까지 격차
    target_score = NEW_PRODUCT_TARGET_TOTAL_SCORE
    score_gap = max(0, target_score - tot_score)
    print(f"\n  목표: {target_score}점 (격차: {score_gap}점)")

    # 지원금 구간표
    print(f"\n  [지원금 구간표]")
    for s_min, s_max, amt in DEFAULT_SUBSIDY_TABLE:
        marker = " <<< 현재" if s_min <= tot_score <= s_max else ""
        print(f"    {s_min:3d}~{s_max:3d}점 -> {amt:>8,}원{marker}")

    # 주차별 현황
    if weekly:
        print(f"\n  [주차별 현황] ({len(weekly)}주차)")
        print(f"  {'주차':<12} {'도입률':>8} {'미도입':>6} {'달성률':>8} {'미달성':>6}")
        print(f"  {'-' * 48}")
        for w in weekly:
            print(f"  {w.get('week_cont', ''):<12} "
                  f"{w.get('doip_rate', 0):>7.1f}% "
                  f"{w.get('midoip_cnt', 0):>5}개 "
                  f"{w.get('ds_rate', 0):>7.1f}% "
                  f"{w.get('mids_cnt', 0):>5}개")

    # ============================================================
    # Section 2: 미도입 상품 발주 우선순위
    # ============================================================
    section_separator("2. 미도입 상품 발주 우선순위")

    unordered = repo.get_unordered_items(store_id, month_ym, item_type="midoip")
    all_midoip = repo.get_missing_items(store_id, month_ym, item_type="midoip")

    print(f"\n  전체 미도입: {len(all_midoip)}개")
    print(f"  발주가능 & 미발주: {len(unordered)}개")

    if unordered:
        # 비즈니스 로직: 우선순위 정렬
        prioritized = prioritize_missing_items(unordered, score_gap)

        # 발주가능/불가 분류
        orderable = [
            item for item in prioritized
            if item.get("ord_pss_nm") in ("발주가능", "가능")
        ]
        not_orderable = [
            item for item in prioritized
            if item.get("ord_pss_nm") not in ("발주가능", "가능")
        ]

        print(f"  발주가능: {len(orderable)}개, 발주불가: {len(not_orderable)}개")
        print(f"  발주수량: 각 {NEW_PRODUCT_INTRO_ORDER_QTY}개")

        if orderable:
            print(f"\n  {'No':>3} {'상품코드':<15} {'상품명':<24} {'소분류':<18} {'주차':<10} {'발주':<4}")
            print(f"  {'-' * 78}")
            for idx, item in enumerate(orderable, 1):
                item_cd = item.get("item_cd", "")
                item_nm = item.get("item_nm", "")[:22]
                small_nm = item.get("small_nm", "")[:16]
                week_cont = item.get("week_cont", "")[:8]
                print(f"  {idx:>3} {item_cd:<15} {item_nm:<24} {small_nm:<18} {week_cont:<10} {NEW_PRODUCT_INTRO_ORDER_QTY}개")

        # 소분류별 분포
        by_category = group_new_items_by_category(orderable)
        print(f"\n  [소분류별 분포] ({len(by_category)}개 카테고리)")
        for cat_name, cat_items in sorted(by_category.items(), key=lambda x: -len(x[1])):
            print(f"    {cat_name:<20} {len(cat_items)}개")

        if not_orderable:
            print(f"\n  [발주불가 상품] ({len(not_orderable)}개)")
            for item in not_orderable:
                print(f"    {item.get('item_cd', ''):<15} {item.get('item_nm', ''):<24} "
                      f"({item.get('ord_pss_nm', 'N/A')})")

    # ============================================================
    # Section 3: 3일발주 미달성 스케줄
    # ============================================================
    section_separator("3. 3일발주 미달성 스케줄")

    mids_items = repo.get_missing_items(store_id, month_ym, item_type="mids")
    print(f"\n  3일발주 미달성: {len(mids_items)}개")

    if mids_items:
        # 주차별 기간 정보 매핑
        week_periods = {}
        for w in weekly:
            wno = w.get("week_no")
            week_periods[wno] = {
                "sta_dd": w.get("sta_dd", ""),
                "end_dd": w.get("end_dd", ""),
                "period": w.get("period", ""),
            }

        print(f"\n  {'No':>3} {'상품코드':<15} {'상품명':<22} {'소분류':<14} {'DS현황':>10} "
              f"{'기간':<23} {'오늘발주':>8} {'사유'}")
        print(f"  {'-' * 110}")

        today_orders = []   # 오늘 발주해야 할 상품
        skip_orders = []    # 스킵
        future_orders = []  # 미도래

        for idx, item in enumerate(mids_items, 1):
            item_cd = item.get("item_cd", "")
            item_nm = item.get("item_nm", "")[:20]
            small_nm = item.get("small_nm", "")[:12]
            ds_yn = item.get("ds_yn", "")
            week_no = item.get("week_no", 0)

            placed, required = parse_ds_yn(ds_yn)
            remaining = get_remaining_orders_needed(placed)

            # 해당 주차의 기간 가져오기
            wp = week_periods.get(week_no, {})
            period_start = wp.get("sta_dd", "")
            period_end = wp.get("end_dd", "")
            period_display = f"{period_start}~{period_end}" if period_start else "N/A"

            # 3일발주 계획 수립
            if period_start and period_end:
                plan = plan_3day_orders(item_cd, period_start, period_end)
                should, reason = should_order_today(
                    item_cd=item_cd,
                    today=today,
                    order_plan=plan,
                    current_stock=0,       # DB에 재고 정보 없음 -> 보수적으로 0
                    daily_avg_sales=0.5,   # 신상품 기본 예상
                    shelf_life_days=3,
                    orders_placed=placed,
                    last_order_sold=True,   # 판매 정보 없으므로 True 가정
                )
            else:
                should = False
                reason = "기간 정보 없음"
                plan = []

            order_marker = "-> YES" if should else "   -"

            ds_display = f"{placed}/{required}"
            print(f"  {idx:>3} {item_cd:<15} {item_nm:<22} {small_nm:<14} {ds_display:>10} "
                  f"{period_display:<23} {order_marker:>8} {reason}")

            if should:
                today_orders.append(item)
            elif period_end and today > period_end:
                skip_orders.append(item)
            else:
                future_orders.append(item)

        print(f"\n  [요약]")
        print(f"    오늘 발주 대상: {len(today_orders)}개")
        print(f"    기간 경과/스킵: {len(skip_orders)}개")
        print(f"    미도래/대기:    {len(future_orders)}개")

        if today_orders:
            print(f"\n  [오늘 발주해야 할 3일발주 상품]")
            for item in today_orders:
                print(f"    -> {item.get('item_cd', '')} {item.get('item_nm', '')}")

    # ============================================================
    # Section 4: 소분류별 교체 대상 (참고용)
    # ============================================================
    section_separator("4. 소분류별 교체 대상 (참고용)")

    if unordered:
        by_cat = group_new_items_by_category(
            [i for i in unordered if i.get("ord_pss_nm") in ("발주가능", "가능")]
        )
        print(f"\n  신상품 도입 시 동일 소분류의 비인기 기존상품 정지 검토 권장")
        print(f"  (실제 교체는 기존 판매 데이터 기반으로 AutoOrderSystem에서 자동 처리)")
        print(f"\n  {'소분류':<20} {'신상품수':>6} {'도입 예정 상품'}")
        print(f"  {'-' * 70}")
        for cat_name, cat_items in sorted(by_cat.items(), key=lambda x: -len(x[1])):
            first_item = cat_items[0].get("item_nm", "")[:30]
            extra = f" 외 {len(cat_items)-1}개" if len(cat_items) > 1 else ""
            print(f"  {cat_name:<20} {len(cat_items):>5}개  {first_item}{extra}")
    else:
        print(f"\n  미도입 상품 없음 (교체 대상 없음)")

    # ============================================================
    # Section 5: 시뮬레이션 (전량 발주 시 예상 점수)
    # ============================================================
    section_separator("5. 시뮬레이션: 전량 발주 시 예상 점수")

    orderable_count = len([
        i for i in unordered
        if i.get("ord_pss_nm") in ("발주가능", "가능")
    ]) if unordered else 0

    # 3일발주 중 오늘 발주 가능한 건수
    ds_orderable = len(today_orders) if mids_items else 0

    print(f"\n  현재 도입: {doip_cnt}/{doip_item_cnt}")
    print(f"  현재 달성: {ds_cnt}/{ds_item_cnt}")
    print(f"  추가 도입 가능: {orderable_count}개")
    print(f"  추가 달성 가능: {ds_orderable}개 (오늘 발주분)")

    # 시뮬레이션 1: 미도입 전량 발주
    est1 = estimate_score_after_orders(
        current_doip_cnt=doip_cnt,
        current_ds_cnt=ds_cnt,
        total_doip_items=doip_item_cnt,
        total_ds_items=ds_item_cnt,
        new_doip_orders=orderable_count,
        new_ds_orders=0,
    )
    print(f"\n  [시나리오 A] 미도입 {orderable_count}개 전량 발주")
    print(f"    도입률: {doip_rate}% -> {est1['doip_rate']}% ({doip_score}pt -> {est1['doip_score']}pt)")
    print(f"    달성률: {ds_rate}% (변동 없음)")
    print(f"    종합:   {tot_score}pt -> {est1['total_score']}pt")
    print(f"    지원금: {supp_pay_amt:,}원 -> {est1['subsidy']:,}원 "
          f"({'+' if est1['subsidy'] - supp_pay_amt >= 0 else ''}{est1['subsidy'] - supp_pay_amt:,}원)")

    # 시뮬레이션 2: 미도입 + 3일발주 동시
    est2 = estimate_score_after_orders(
        current_doip_cnt=doip_cnt,
        current_ds_cnt=ds_cnt,
        total_doip_items=doip_item_cnt,
        total_ds_items=ds_item_cnt,
        new_doip_orders=orderable_count,
        new_ds_orders=ds_orderable,
    )
    print(f"\n  [시나리오 B] 미도입 {orderable_count}개 + 3일발주 {ds_orderable}개 동시")
    print(f"    도입률: {doip_rate}% -> {est2['doip_rate']}% ({doip_score}pt -> {est2['doip_score']}pt)")
    print(f"    달성률: {ds_rate}% -> {est2['ds_rate']}% ({ds_score}pt -> {est2['ds_score']}pt)")
    print(f"    종합:   {tot_score}pt -> {est2['total_score']}pt")
    print(f"    지원금: {supp_pay_amt:,}원 -> {est2['subsidy']:,}원 "
          f"({'+' if est2['subsidy'] - supp_pay_amt >= 0 else ''}{est2['subsidy'] - supp_pay_amt:,}원)")

    # 목표 달성 필요 상품 수
    needed_doip = calculate_needed_items(
        current_rate=doip_rate,
        target_rate=95.0,
        total_cnt=doip_item_cnt,
        current_cnt=doip_cnt,
    )
    needed_ds = calculate_needed_items(
        current_rate=ds_rate,
        target_rate=90.0,  # DS 90% -> 20점 확보
        total_cnt=ds_item_cnt,
        current_cnt=ds_cnt,
    )
    print(f"\n  [목표 달성 필요]")
    print(f"    도입률 95%+ 필요: 추가 {needed_doip}개 (보유 발주가능: {orderable_count}개) "
          f"{'-> 달성 가능!' if orderable_count >= needed_doip else '-> 부족'}")
    print(f"    달성률 90%+ 필요: 추가 {needed_ds}개")

    # ============================================================
    # 최종 발주 요약
    # ============================================================
    section_separator("최종 발주 요약")

    total_orders = orderable_count + ds_orderable
    print(f"\n  [오늘 발주 대상 총 {total_orders}건]")
    print(f"\n  A. 미도입 상품 발주 ({orderable_count}건, 각 {NEW_PRODUCT_INTRO_ORDER_QTY}개)")
    if unordered:
        orderable_list = [
            i for i in prioritize_missing_items(unordered, score_gap)
            if i.get("ord_pss_nm") in ("발주가능", "가능")
        ]
        # 상위 10개만 표시
        display_count = min(10, len(orderable_list))
        for idx, item in enumerate(orderable_list[:display_count], 1):
            print(f"    {idx:>2}. {item.get('item_cd', '')} {item.get('item_nm', '')} "
                  f"[{item.get('small_nm', '')}]")
        if len(orderable_list) > display_count:
            print(f"    ... 외 {len(orderable_list) - display_count}건")

    if ds_orderable > 0:
        print(f"\n  B. 3일발주 달성 ({ds_orderable}건, 각 {NEW_PRODUCT_INTRO_ORDER_QTY}개)")
        for item in today_orders:
            print(f"    -> {item.get('item_cd', '')} {item.get('item_nm', '')} "
                  f"[{item.get('small_nm', '')}]")
    else:
        print(f"\n  B. 3일발주: 오늘 발주 대상 없음")

    print(f"\n  [예상 결과]")
    best = est2 if ds_orderable > 0 else est1
    print(f"    {tot_score}점 -> {best['total_score']}점 "
          f"({supp_pay_amt:,}원 -> {best['subsidy']:,}원)")

    print(f"\n{'=' * 70}")
    print(f"  분석 완료! ({today})")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
