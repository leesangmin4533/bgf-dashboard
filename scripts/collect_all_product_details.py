# -*- coding: utf-8 -*-
"""
벌크 상품 상세 수집 스크립트
- DB의 활성 상품 중 유통기한(expiration_days) 미등록 상품을 일괄 수집
- OrderPrepCollector.collect_for_item()을 활용하여 유통기한 + 행사 정보 수집
- 배치 단위 진행률 저장, 중단 후 재개(resume) 지원

사용법:
  python scripts/collect_all_product_details.py              # 전체 수집
  python scripts/collect_all_product_details.py --dry-run    # 대상만 출력
  python scripts/collect_all_product_details.py --max 10     # 10개만 수집
  python scripts/collect_all_product_details.py --resume     # 중단 지점부터 재개
  python scripts/collect_all_product_details.py --force      # 기존 데이터도 재수집
"""

import sys
import io

# Windows CP949 콘솔 → UTF-8 래핑 (한글·특수문자 깨짐 방지)
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

import os
import json
import time
import argparse
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

# 프로젝트 루트를 path에 추가
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.sales_analyzer import SalesAnalyzer
from src.collectors.order_prep_collector import OrderPrepCollector
from src.infrastructure.database.repos import ProductDetailRepository

from src.settings.timing import (
    BULK_COLLECT_BATCH_SIZE,
    BULK_COLLECT_MENU_REFRESH,
    BULK_COLLECT_ITEM_DELAY,
    ALERT_MAX_RETRIES,
    ALERT_RETRY_DELAY,
    PROGRESS_LOG_INTERVAL,
    SA_LOGIN_WAIT,
    SA_POPUP_CLOSE_WAIT,
)

# 진행 상태 파일 경로
PROGRESS_FILE = project_root / "data" / "bulk_collect_progress.json"


def parse_args() -> argparse.Namespace:
    """CLI 인자 파싱"""
    parser = argparse.ArgumentParser(
        description="활성 상품의 유통기한/행사 정보 벌크 수집"
    )
    parser.add_argument(
        "--max", type=int, default=0,
        help="최대 수집 개수 (0=전체, 기본: 0)"
    )
    parser.add_argument(
        "--batch", type=int, default=BULK_COLLECT_BATCH_SIZE,
        help=f"배치 크기 (기본: {BULK_COLLECT_BATCH_SIZE})"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="이미 수집된 상품도 재수집"
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="이전 중단 지점부터 재개"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="수집 대상만 출력 (실행 안 함)"
    )
    return parser.parse_args()


def load_progress() -> Dict[str, Any]:
    """진행 상태 파일 로드"""
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARN] 진행 상태 파일 손상됨 ({PROGRESS_FILE}): {e}")
    return {"completed": [], "failed": [], "last_index": 0}


def save_progress(progress: Dict[str, Any]) -> None:
    """진행 상태 파일 저장 (원자적 쓰기)"""
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # 임시 파일에 쓰고 원자적으로 교체
    temp_file = PROGRESS_FILE.with_suffix(".json.tmp")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)
        # 원자적 교체 (Windows에서도 안전)
        temp_file.replace(PROGRESS_FILE)
    except Exception as e:
        print(f"[ERROR] 진행 상태 저장 실패: {e}")
        if temp_file.exists():
            temp_file.unlink()


def get_target_items(force: bool = False) -> List[str]:
    """수집 대상 상품 코드 목록 조회

    Args:
        force: True면 전체 활성 상품, False면 유통기한 미등록만

    Returns:
        상품코드 목록
    """
    repo = ProductDetailRepository()
    if force:
        # 전체 활성 상품 (Repository 사용)
        return repo.get_all_active_items(days=30)
    else:
        return repo.get_active_items_missing_expiration(days=30)


def clear_alerts(driver: Any) -> None:
    """남아있는 Alert 모두 처리"""
    for _ in range(ALERT_MAX_RETRIES):
        try:
            alert = driver.switch_to.alert
            alert.accept()
            time.sleep(ALERT_RETRY_DELAY)
        except Exception:
            break


def run_bulk_collect(
    max_items: int = 0,
    batch_size: int = BULK_COLLECT_BATCH_SIZE,
    force: bool = False,
    resume: bool = True,
    store_id: Optional[str] = None,
) -> Dict[str, Any]:
    """벌크 상품 상세 수집 실행

    스케줄러 또는 CLI에서 호출 가능한 핵심 수집 함수.

    Args:
        max_items: 최대 수집 개수 (0=전체)
        batch_size: 배치 크기 (진행률 저장 단위)
        force: True면 기존 데이터도 재수집
        resume: True면 이전 중단 지점부터 재개
        store_id: 매장 ID (기본: None)

    Returns:
        {"success": bool, "total": int, "collected": int, "failed": int}
    """
    print("=" * 70)
    print("벌크 상품 상세 수집 (유통기한 + 행사 정보)")
    print(f"시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 1. 수집 대상 조회
    print("\n[1] 수집 대상 상품 조회 중...")
    target_items = get_target_items(force=force)

    if max_items > 0:
        target_items = target_items[:max_items]

    # resume 모드: 이미 완료된 상품 제외
    progress = load_progress()
    if resume and progress["completed"]:
        completed_set = set(progress["completed"])
        before_count = len(target_items)
        target_items = [cd for cd in target_items if cd not in completed_set]
        skipped = before_count - len(target_items)
        print(f"  이전 진행: {len(completed_set)}개 완료, {len(progress['failed'])}개 실패")
        print(f"  {skipped}개 건너뜀 → {len(target_items)}개 남음")
    else:
        progress = {"completed": [], "failed": [], "last_index": 0}

    total = len(target_items)
    print(f"\n수집 대상: {total}개 상품")

    if total == 0:
        print("수집할 상품이 없습니다.")
        return {"success": True, "total": 0, "collected": 0, "failed": 0}

    # 2. BGF 로그인
    print("\n[2] BGF 사이트 로그인 중...")
    analyzer = SalesAnalyzer()

    success_count = 0
    fail_count = 0
    retry_success = 0

    try:
        analyzer.setup_driver()
        analyzer.connect()
        time.sleep(SA_LOGIN_WAIT)

        if not analyzer.do_login():
            print("[ERROR] 로그인 실패!")
            return {"success": False, "total": total, "collected": 0, "failed": 0,
                    "error": "로그인 실패"}

        print("[OK] 로그인 성공")
        time.sleep(SA_POPUP_CLOSE_WAIT * 2)
        analyzer.close_popup()
        time.sleep(SA_POPUP_CLOSE_WAIT)

        # 3. OrderPrepCollector 초기화 + 메뉴 이동
        print("\n[3] 단품별 발주 화면 이동 중...")
        collector = OrderPrepCollector(driver=analyzer.driver, save_to_db=True)

        if not collector.navigate_to_menu():
            print("[ERROR] 메뉴 이동 실패!")
            return {"success": False, "total": total, "collected": 0, "failed": 0,
                    "error": "메뉴 이동 실패"}

        if not collector.select_order_date():
            print("[ERROR] 발주일 선택 실패!")
            return {"success": False, "total": total, "collected": 0, "failed": 0,
                    "error": "발주일 선택 실패"}

        print("[OK] 화면 준비 완료")

        # 4. 배치 단위 수집
        print(f"\n[4] 수집 시작 (배치: {batch_size}개, 리프레시: {BULK_COLLECT_MENU_REFRESH}개 간격)")
        print("-" * 70)

        fail_items: List[str] = list(progress.get("failed", []))
        start_time = time.time()
        items_since_refresh = 0
        last_processed = 0

        try:
            for i, item_cd in enumerate(target_items):
                # 메뉴 리프레시 (200개마다)
                if items_since_refresh >= BULK_COLLECT_MENU_REFRESH:
                    print(f"\n  [리프레시] 메뉴 재이동 ({items_since_refresh}개 처리 후)...")
                    clear_alerts(analyzer.driver)
                    collector.reset_navigation_state()
                    if not collector.navigate_to_menu():
                        print("  [WARN] 메뉴 재이동 실패, 재시도...")
                        time.sleep(SA_POPUP_CLOSE_WAIT * 2)
                        clear_alerts(analyzer.driver)
                        if not collector.navigate_to_menu():
                            print("  [ERROR] 메뉴 재이동 완전 실패, 중단")
                            break
                    if not collector.select_order_date():
                        print("  [WARN] 발주일 재선택 실패")
                        break
                    items_since_refresh = 0
                    print("  [OK] 리프레시 완료")

                # 상품 수집
                try:
                    result = collector.collect_for_item(item_cd)

                    if result.get("success"):
                        success_count += 1
                        progress["completed"].append(item_cd)
                        exp = result.get("expiration_days", "?")
                        promo = ""
                        if result.get("current_month_promo") or result.get("next_month_promo"):
                            promo = f", 행사={result.get('current_month_promo', '') or '없음'}/{result.get('next_month_promo', '') or '없음'}"
                        if i < 5 or (i + 1) % PROGRESS_LOG_INTERVAL == 0:
                            print(f"  [{i+1}/{total}] {item_cd} -> 유통기한={exp}일{promo}")
                    else:
                        fail_count += 1
                        fail_items.append(item_cd)
                        if i < 5 or (i + 1) % PROGRESS_LOG_INTERVAL == 0:
                            print(f"  [{i+1}/{total}] {item_cd} -> [FAIL]")

                except Exception as e:
                    fail_count += 1
                    fail_items.append(item_cd)
                    print(f"  [{i+1}/{total}] {item_cd} -> [ERROR] {e}")
                    clear_alerts(analyzer.driver)

                items_since_refresh += 1
                last_processed = i + 1

                # 배치마다 진행 상태 저장
                if (i + 1) % batch_size == 0:
                    progress["failed"] = fail_items
                    progress["last_index"] = i + 1
                    save_progress(progress)
                    elapsed = time.time() - start_time
                    rate = (i + 1) / elapsed if elapsed > 0 else 0
                    remaining = (total - i - 1) / rate if rate > 0 else 0
                    print(
                        f"\n  --- 배치 저장 [{i+1}/{total}] "
                        f"성공={success_count}, 실패={fail_count}, "
                        f"속도={rate:.1f}개/초, "
                        f"남은시간={int(remaining // 60)}분 {int(remaining % 60)}초 ---\n"
                    )

                time.sleep(BULK_COLLECT_ITEM_DELAY)

        except KeyboardInterrupt:
            print("\n\n[!] 사용자 중단 (Ctrl+C)")
        finally:
            progress["failed"] = fail_items
            progress["last_index"] = last_processed
            save_progress(progress)

        # 5. 실패 상품 재시도 (1회)
        retry_items = [cd for cd in fail_items if cd not in progress["completed"]]
        if retry_items:
            print(f"\n[5] 실패 상품 재시도 ({len(retry_items)}개)...")
            clear_alerts(analyzer.driver)
            collector.reset_navigation_state()
            if collector.navigate_to_menu() and collector.select_order_date():
                for item_cd in retry_items:
                    try:
                        result = collector.collect_for_item(item_cd)
                        if result.get("success"):
                            retry_success += 1
                            progress["completed"].append(item_cd)
                            if item_cd in fail_items:
                                fail_items.remove(item_cd)
                    except Exception:
                        clear_alerts(analyzer.driver)
                    time.sleep(BULK_COLLECT_ITEM_DELAY)

            if retry_success > 0:
                print(f"  재시도 성공: {retry_success}개")
                success_count += retry_success
                fail_count -= retry_success

        # 최종 진행 상태 저장
        progress["failed"] = [cd for cd in fail_items if cd not in progress["completed"]]
        save_progress(progress)

        # 6. 결과 출력
        elapsed = time.time() - start_time
        print("\n" + "=" * 70)
        print("수집 완료")
        print("=" * 70)
        print(f"전체 대상: {total}개")
        print(f"성공: {success_count}개")
        print(f"실패: {fail_count}개")
        if retry_success > 0:
            print(f"재시도 성공: {retry_success}개")
        print(f"소요 시간: {int(elapsed // 60)}분 {int(elapsed % 60)}초")
        if total > 0:
            print(f"성공률: {success_count / total * 100:.1f}%")

        # DB 커버리지 확인
        repo = ProductDetailRepository()
        all_missing = repo.get_active_items_missing_expiration(days=30)
        all_items = repo.get_items_without_details()
        print(f"\n[DB 현황]")
        print(f"  유통기한 미등록 (활성): {len(all_missing)}개")
        print(f"  product_details 미등록: {len(all_items)}개")

        if progress["failed"]:
            print(f"\n[실패 상품 목록] ({len(progress['failed'])}개)")
            for cd in progress["failed"][:20]:
                print(f"  - {cd}")
            if len(progress["failed"]) > 20:
                print(f"  ... 외 {len(progress['failed']) - 20}개")

        print(f"\n진행 상태 파일: {PROGRESS_FILE}")

    finally:
        try:
            analyzer.close()
            print("브라우저 종료")
        except Exception as e:
            print(f"[WARN] 브라우저 종료 실패: {e}")

    return {
        "success": True,
        "total": total,
        "collected": success_count,
        "failed": fail_count,
        "retry_success": retry_success,
    }


def main() -> None:
    """CLI 진입점"""
    args = parse_args()

    # dry-run 모드
    if args.dry_run:
        target_items = get_target_items(force=args.force)
        if args.max > 0:
            target_items = target_items[:args.max]

        total = len(target_items)
        print(f"\n[DRY-RUN] 수집 대상 상품 목록 ({total}개):")
        print("-" * 50)
        for i, item_cd in enumerate(target_items):
            print(f"  {i+1:4d}. {item_cd}")
            if i >= 99 and total > 100:
                print(f"  ... 외 {total - 100}개")
                break
        print(f"\n예상 소요: 약 {total * 3 // 60}분 (상품당 ~3초)")
        return

    run_bulk_collect(
        max_items=args.max,
        batch_size=args.batch,
        force=args.force,
        resume=args.resume,
    )


if __name__ == "__main__":
    main()
