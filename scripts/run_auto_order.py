"""
자동 발주 실행 스크립트
- BGF 로그인 -> 예측 -> 발주 실행
"""

import sys
import io
from pathlib import Path

# Windows CP949 콘솔 → UTF-8 래핑 (한글·특수문자 깨짐 방지)
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

# 프로젝트 루트를 path에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import time
import argparse
from datetime import datetime
from typing import Any, Dict, List

from src.sales_analyzer import SalesAnalyzer
from src.order.auto_order import AutoOrderSystem
from src.settings.constants import DEFAULT_STORE_ID


def run_auto_order(
    dry_run: bool = True,
    min_order_qty: int = 1,
    max_items: int = 10,
    categories: list = None,
    store_id: str = DEFAULT_STORE_ID,
) -> Dict[str, Any]:
    """
    자동 발주 실행

    Args:
        dry_run: True면 실제 발주 안함 (테스트)
        min_order_qty: 최소 발주량
        max_items: 최대 상품 수
        categories: 발주 대상 카테고리 코드 목록 (None이면 전체)
        store_id: 점포 코드
    """
    print("\n" + "="*70)
    print("BGF 자동 발주 시스템")
    print(f"시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"모드: {'테스트 (dry_run)' if dry_run else '실제 발주'}")
    print(f"설정: min_qty={min_order_qty}, max_items={max_items}")
    print(f"점포: {store_id}")
    if categories:
        print(f"카테고리 필터: {', '.join(categories)} ({len(categories)}개)")
    print("="*70)

    analyzer = None
    system = None

    try:
        # 1. BGF 로그인
        print("\n[1단계] BGF 시스템 로그인...")
        analyzer = SalesAnalyzer()
        analyzer.setup_driver()
        analyzer.connect()

        if not analyzer.do_login():
            print("[ERROR] 로그인 실패")
            return {"success": False, "message": "login failed"}

        print("[OK] 로그인 성공")
        time.sleep(2)

        # 2. 자동 발주 시스템 초기화
        print("\n[2단계] 자동 발주 시스템 초기화...")
        system = AutoOrderSystem(driver=analyzer.driver, store_id=store_id)

        # 3. 발주 실행
        if categories:
            # 부분 발주: 카테고리 필터링
            print(f"\n[3단계] 부분 발주 실행 (카테고리: {', '.join(categories)})...")
            cat_set = set(categories)
            order_list = system.get_recommendations(
                min_order_qty=min_order_qty,
                max_items=None,  # 전체 가져와서 카테고리로 필터링
            )
            order_list = [item for item in order_list if item.get("mid_cd") in cat_set]
            print(f"[카테고리 필터] {len(cat_set)}개 카테고리, {len(order_list)}개 상품")
            result = system.execute(
                order_list=order_list,
                min_order_qty=min_order_qty,
                max_items=None,
                dry_run=dry_run,
                margin_collect_categories=cat_set,
            )
        else:
            print("\n[3단계] 발주 실행...")
            result = system.run_daily_order(
                min_order_qty=min_order_qty,
                max_items=max_items,
                dry_run=dry_run
            )

        # 4. 발주 실패 사유 수집 (실패 건이 있을 때)
        fail_reason_result = None
        fail_count = result.get('fail_count', 0)
        if fail_count > 0 and analyzer and analyzer.driver:
            print(f"\n[4단계] 발주 실패 사유 수집 ({fail_count}건)")
            try:
                from src.collectors.fail_reason_collector import FailReasonCollector
                fr_collector = FailReasonCollector(driver=analyzer.driver, store_id=store_id)
                fail_reason_result = fr_collector.collect_all()
                print(f"조회 결과: {fail_reason_result.get('success', 0)}성공 / "
                      f"{fail_reason_result.get('failed', 0)}실패 / "
                      f"{fail_reason_result.get('checked', 0)}건 저장")
            except Exception as e:
                print(f"실패 사유 수집 오류: {e}")

        print("\n" + "="*70)
        print("자동 발주 완료")
        print(f"성공: {result.get('success_count', 0)}건")
        print(f"실패: {result.get('fail_count', 0)}건")
        if fail_reason_result:
            print(f"실패 사유 확인: {fail_reason_result.get('checked', 0)}건")
        print("="*70)

        result['fail_reasons'] = fail_reason_result
        return result

    except Exception as e:
        print(f"\n[ERROR] 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "message": str(e)}

    finally:
        if system:
            system.close()
        if analyzer:
            # 브라우저 유지 (결과 확인용)
            try:
                input("\n브라우저를 닫으려면 Enter를 누르세요...")
            except EOFError:
                pass
            analyzer.close()


def preview_only(
    min_order_qty: int = 1,
    max_items: int = 20,
    categories: list = None,
    store_id: str = DEFAULT_STORE_ID,
) -> List[Dict[str, Any]]:
    """
    예측 미리보기만 (로그인 없이)

    Args:
        min_order_qty: 최소 발주량
        max_items: 최대 상품 수
        categories: 카테고리 필터 (None이면 전체)
        store_id: 점포 코드
    """
    print("\n" + "="*70)
    print("발주 예측 미리보기 (로그인 없이)")
    if categories:
        print(f"카테고리 필터: {', '.join(categories)} ({len(categories)}개)")
    print("="*70)

    system = AutoOrderSystem(driver=None, store_id=store_id)

    try:
        # 발주 추천 목록 생성
        order_list = system.get_recommendations(
            min_order_qty=min_order_qty,
            max_items=0 if categories else max_items,
        )

        # 카테고리 필터링
        if categories:
            cat_set = set(categories)
            order_list = [item for item in order_list if item.get("mid_cd") in cat_set]
            if max_items and max_items > 0:
                order_list = order_list[:max_items]
            print(f"[카테고리 필터] {len(order_list)}개 상품")

        # 출력
        system.print_recommendations(order_list, top_n=max_items)

        return order_list

    finally:
        system.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BGF 자동 발주 시스템")
    parser.add_argument("--run", action="store_true", help="실제 발주 실행 (dry_run=False)")
    parser.add_argument("--preview", action="store_true", help="예측 미리보기만 (로그인 없이)")
    parser.add_argument("--min-qty", "-q", type=int, default=1, help="최소 발주량 (기본: 1)")
    parser.add_argument("--max-items", "-n", type=int, default=10, help="최대 상품 수 (기본: 10)")
    parser.add_argument("--categories", type=str, default="",
                        help="발주 대상 카테고리 (콤마 구분, 예: 001,002,003)")
    parser.add_argument("--store-id", type=str, default=DEFAULT_STORE_ID,
                        help="점포 코드 (예: 46513)")
    args = parser.parse_args()

    # 카테고리 파싱
    cats = [c.strip() for c in args.categories.split(",") if c.strip()] if args.categories else None

    if args.preview:
        # 미리보기만
        preview_only(
            min_order_qty=args.min_qty,
            max_items=args.max_items,
            categories=cats,
            store_id=args.store_id,
        )
    else:
        # 자동 발주 (기본: dry_run=True)
        run_auto_order(
            dry_run=not args.run,
            min_order_qty=args.min_qty,
            max_items=args.max_items,
            categories=cats,
            store_id=args.store_id,
        )
