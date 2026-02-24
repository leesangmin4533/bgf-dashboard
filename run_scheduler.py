"""
스케줄러 실행기 (Use Case 아키텍처)

- 매일 아침 7시: 전날+당일 데이터 수집 -> 자동 발주
- 매일 11시: 벌크 상품 상세 수집 (유통기한 + 행사 정보)
- 폐기 알림 전 수집: 21:00(22시), 13:00(14시), 09:00(10시), 01:00(02시), 22:00(빵)
- 폐기 30분 전 알림: 21:30(22시), 13:30(14시), 09:30(10시), 01:30(02시)
- 빵 유통기한 1시간 전 알림: 23:00(자정 만료)
- 정밀 폐기 확정 (3단계): 10분 전 수집 → 판정(정시) → 10분 후 수집+확정
  폐기 시간대: 02:00, 10:00, 14:00, 22:00, 00:00
- 배송 도착 후 배치 동기화 (07:30 2차, 20:30 1차)
- 매일 23:00: 폐기 보고서 생성 (엑셀)
- 매일 23:30: 배치 유통기한 만료 처리 (정밀 폐기 미처리 잔여분 폴백)
- 매주 월요일 08:00: 주간 종합 리포트 (카테고리 + 상품 트렌드 + 예측 정확도)
- 중복 실행 방지 (락 파일)

모든 작업은 Use Case Flow + MultiStoreRunner를 통해 실행됩니다.

Usage:
    python run_scheduler.py                  # 스케줄러 시작
    python run_scheduler.py --now            # 즉시 실행 (데이터 수집 + 발주)
    python run_scheduler.py --bulk-collect   # 벌크 상품 상세 수집 즉시 실행
    python run_scheduler.py --expiry 22      # 22:00 폐기 알림 즉시 발송
    python run_scheduler.py --weekly-report  # 주간 종합 리포트 즉시 발송
    python run_scheduler.py --waste-report   # 폐기 보고서 즉시 생성
    python run_scheduler.py --batch-expire   # 배치 만료 처리 즉시 실행
    python run_scheduler.py --collect-order-unit  # 전체 품목 발주단위 수집 즉시 실행
    python run_scheduler.py --sync-waste-backfill --days 20  # 폐기전표→daily_sales 동기화 백필
"""

import sys
import os
import io
import time
import argparse
import atexit
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict

# Windows CP949 콘솔 → UTF-8 래핑 (한글·특수문자 깨짐 방지)
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

# 경로 설정
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root))

# 로그 정리 (30일 초과 삭제, 50MB 초과 잘라내기)
from src.utils.logger import cleanup_old_logs
cleanup_old_logs(max_age_days=30, max_file_mb=50)


def _is_pid_running(pid: int) -> bool:
    """PID가 실행 중인지 확인 (Windows/Unix 호환)"""
    if sys.platform == 'win32':
        import ctypes
        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


try:
    import schedule
except ImportError:
    print("[ERROR] 'schedule' 패키지가 필요합니다.")
    print("        pip install schedule")
    sys.exit(1)

from db.models import init_db
from alert.config import EXPIRY_ALERT_SCHEDULE, EXPIRY_CONFIRM_SCHEDULE, DELIVERY_CONFIG
from notification.kakao_notifier import KakaoNotifier, DEFAULT_REST_API_KEY
from utils.logger import get_logger

from src.application.scheduler.job_scheduler import MultiStoreRunner
from src.settings.store_context import StoreContext

# scripts/ 경로 추가 (벌크 수집 스크립트 import용)
sys.path.insert(0, str(project_root / "scripts"))

logger = get_logger(__name__)

# 락 파일 경로
LOCK_FILE = project_root / "data" / "scheduler.lock"


def acquire_lock() -> bool:
    """락 파일 생성 (중복 실행 방지)"""
    try:
        # data 폴더 생성
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

        # 락 파일 존재 확인
        if LOCK_FILE.exists():
            # 락 파일 내용 확인 (PID)
            try:
                with open(LOCK_FILE, 'r') as f:
                    old_pid = int(f.read().strip())

                # 해당 프로세스가 실행 중인지 확인
                if _is_pid_running(old_pid):
                    print(f"[ERROR] 스케줄러가 이미 실행 중입니다 (PID: {old_pid})")
                    print(f"[INFO] 강제 종료하려면: taskkill /PID {old_pid} /F")
                    return False
                else:
                    # 프로세스가 없으면 오래된 락 파일 삭제
                    print(f"[WARN] 오래된 락 파일 발견. 삭제합니다.")
                    LOCK_FILE.unlink()
            except (ValueError, FileNotFoundError):
                LOCK_FILE.unlink()

        # 새 락 파일 생성
        with open(LOCK_FILE, 'w') as f:
            f.write(str(os.getpid()))

        return True
    except Exception as e:
        print(f"[ERROR] 락 파일 생성 실패: {e}")
        return False


def release_lock() -> None:
    """락 파일 삭제"""
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
            print("[INFO] 락 파일 삭제됨")
    except Exception as e:
        print(f"[WARN] 락 파일 삭제 실패: {e}")


# ── 멀티 매장 모드 설정 ──

# 멀티 매장 모드 플래그 (기본: True — 두 점포 병렬 실행)
_MULTI_STORE = True

# 기본 매장 정보 (단일 매장 모드용)
# Note: config.constants 직접 import 불가 (bgf_auto/config/ 디렉토리와 이름 충돌)
_DEFAULT_STORE = {"store_id": "46513", "store_name": "기본매장"}

# MultiStoreRunner 인스턴스
# stagger_seconds=5: 매장 간 5초 시차로 ChromeDriver 파일 잠금 충돌 방지
_runner = MultiStoreRunner(max_workers=4, stagger_seconds=5)


# ── 헬퍼 함수 ──

def _run_task(
    task_fn: Callable[[Any], Dict[str, Any]],
    task_name: str,
) -> None:
    """멀티/단일 매장 모드에 따라 실행

    _MULTI_STORE=True: MultiStoreRunner.run_parallel()로 모든 활성 매장 병렬 실행
    _MULTI_STORE=False: 기본 매장 1개만 실행

    Args:
        task_fn: StoreContext를 받는 작업 함수
        task_name: 작업 이름 (로깅용)
    """
    if _MULTI_STORE:
        _runner.run_parallel(task_fn=task_fn, task_name=task_name)
    else:
        ctx = StoreContext.from_store_id(_DEFAULT_STORE["store_id"])
        try:
            result = task_fn(ctx)
            logger.info(f"[{task_name}] 완료: {result}")
        except Exception as e:
            logger.error(f"[{task_name}] 실패: {e}")
            import traceback
            traceback.print_exc()


# ── 스케줄 작업 래퍼 함수 ──

def job_wrapper_multi_store() -> None:
    """멀티 매장 일일 수집+발주 (DailyCollectionJob.run_optimized 사용)

    BGF 사이트 로그인 → 데이터 수집 → 입고/제외 수집 →
    평가 보정 → 예측 로깅 → 자동 발주 실행 → 실패 사유 수집
    전체 플로우를 DailyCollectionJob이 처리합니다.
    """
    logger.info("=" * 80)
    logger.info(f"Multi-Store Daily job triggered at {datetime.now().isoformat()}")
    logger.info("=" * 80)

    def _run_daily_order(ctx):
        from src.scheduler.daily_job import DailyCollectionJob

        job = DailyCollectionJob(store_id=ctx.store_id)
        return job.run_optimized(run_auto_order=True, use_improved_predictor=True)

    _runner.run_parallel(
        task_fn=_run_daily_order,
        task_name="daily_order",
    )


def job_wrapper() -> None:
    """단일 매장 일일 수집+발주 (DailyCollectionJob.run_optimized 사용)

    BGF 사이트 로그인 → 데이터 수집 → 입고/제외 수집 →
    평가 보정 → 예측 로깅 → 자동 발주 실행 → 실패 사유 수집
    """
    logger.info("=" * 60)
    logger.info(f"Daily job triggered at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    from src.scheduler.daily_job import DailyCollectionJob

    job = DailyCollectionJob(store_id=_DEFAULT_STORE["store_id"])
    result = job.run_optimized(run_auto_order=True, use_improved_predictor=True)

    if result.get("success"):
        logger.info("Job completed successfully")
        logger.info(f"  - Items collected: {result.get('total_items', 0)}")
        order = result.get("order")
        if order:
            logger.info(f"  - Order: {order.get('success_count', 0)} success, "
                        f"{order.get('fail_count', 0)} fail")
        fail_reasons = result.get("fail_reasons")
        if fail_reasons:
            logger.info(f"  - Fail reasons: {fail_reasons.get('checked', 0)} collected")
    else:
        logger.error(f"Job failed: {result.get('error', 'Unknown')}")


def expiry_alert_wrapper(expiry_hour: int) -> Callable[[], None]:
    """폐기 30분 전 알림 래퍼

    ExpiryAlertFlow를 통해 배치 만료 처리 + 임박 상품 조회 + 카카오 알림.
    기존 ExpiryChecker.send_expiry_alert(expiry_hour)과 동일 기능.
    """
    def wrapper() -> None:
        logger.info("=" * 60)
        logger.info(f"Expiry alert ({expiry_hour:02d}:00) at {datetime.now().isoformat()}")
        logger.info("=" * 60)

        from src.application.use_cases.expiry_alert_flow import ExpiryAlertFlow

        def alert_task(ctx):
            """ExpiryAlertFlow 기반 알림 + 기존 send_expiry_alert 폴백"""
            try:
                # 기존 ExpiryChecker.send_expiry_alert(expiry_hour) 직접 호출
                # ExpiryAlertFlow.run()은 범용이므로 expiry_hour 전달이 필요한
                # 시간별 알림은 기존 방식 유지
                from src.alert.expiry_checker import ExpiryChecker

                checker = ExpiryChecker(store_id=ctx.store_id)
                try:
                    result = checker.send_expiry_alert(expiry_hour)
                    if result:
                        logger.info(f"[{ctx.store_id}] Expiry alert sent")
                    else:
                        logger.info(f"[{ctx.store_id}] No items to alert")
                    return {"success": True, "sent": bool(result)}
                finally:
                    checker.close()
            except Exception as e:
                logger.error(f"[{ctx.store_id}] Expiry alert error: {e}")
                return {"success": False, "error": str(e)}

        _run_task(alert_task, f"ExpiryAlert({expiry_hour:02d}:00)")

    return wrapper


def weekly_report_wrapper() -> None:
    """주간 종합 리포트 래퍼 (카테고리 + 상품 트렌드 + 예측 정확도)"""
    logger.info("=" * 60)
    logger.info(f"Weekly trend report at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    from src.application.use_cases.weekly_report_flow import WeeklyReportFlow

    _run_task(
        task_fn=lambda ctx: WeeklyReportFlow(store_ctx=ctx).run(),
        task_name="WeeklyReport",
    )


def batch_expire_wrapper() -> None:
    """배치 유통기한 만료 체크 및 폐기 처리 (일괄 폴백용)

    매일 23:30 실행: 정밀 폐기에서 처리되지 않은 잔여 배치를 일괄 expired 처리.
    정밀 폐기(3단계)가 이미 처리한 배치는 status가 expired/consumed이므로 중복 없음.
    """
    logger.info("=" * 60)
    logger.info(f"Batch expire check (fallback) at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    def expire_task(ctx):
        from src.infrastructure.database.repos import InventoryBatchRepository

        batch_repo = InventoryBatchRepository()
        expired = batch_repo.check_and_expire_batches(store_id=ctx.store_id)
        logger.info(f"[{ctx.store_id}] Expired batches (fallback): {len(expired)}")
        for item in expired:
            logger.info(f"  - {item.get('item_cd', '')} {item.get('item_nm', '')}: "
                        f"잔여 {item.get('remaining_qty', 0)}개 폐기")
        return {"success": True, "expired_count": len(expired)}

    _run_task(expire_task, "BatchExpire")


# ── 정밀 폐기 3단계 (10분전 수집 → 판정 → 10분후 수집+확정) ──

# 판정 결과 임시 저장 {expiry_hour: {store_id: [judged_batches]}}
_expiry_judge_results: Dict[int, Dict[str, list]] = {}


def expiry_pre_collect_wrapper(expiry_hour: int) -> Callable[[], None]:
    """[1단계] 폐기 10분 전 판매 수집

    폐기 시점 직전의 최신 stock을 확보하여
    판정 시 정확한 remaining_qty를 만든다.
    """
    def wrapper() -> None:
        logger.info("=" * 60)
        logger.info(f"[정밀폐기 1/3] Pre-collect ({expiry_hour:02d}:00 폐기용) "
                    f"at {datetime.now().isoformat()}")
        logger.info("=" * 60)

        def collect_task(ctx):
            try:
                from src.scheduler.daily_job import DailyCollectionJob

                job = DailyCollectionJob(store_id=ctx.store_id)
                result = job.run_optimized(run_auto_order=False)

                if result["success"]:
                    total = result.get("total_items", 0)
                    logger.info(f"[{ctx.store_id}] 폐기 전 수집 완료: {total}건")
                else:
                    logger.warning(f"[{ctx.store_id}] 폐기 전 수집 실패: "
                                   f"{result.get('error', 'Unknown')}")
            except Exception as e:
                logger.error(f"[{ctx.store_id}] 폐기 전 수집 error: {e}")

            return {"success": True}

        _run_task(collect_task, f"ExpiryPreCollect({expiry_hour:02d}:00)")

    return wrapper


def expiry_judge_wrapper(expiry_hour: int) -> Callable[[], None]:
    """[2단계] 폐기 판정 (정시)

    만료 배치 목록 + 상품별 stock 스냅샷을 저장.
    status는 아직 변경하지 않음 (3단계 확정에서 처리).
    """
    def wrapper() -> None:
        logger.info("=" * 60)
        logger.info(f"[정밀폐기 2/3] Judge ({expiry_hour:02d}:00) "
                    f"at {datetime.now().isoformat()}")
        logger.info("=" * 60)

        # 이 시간대 판정 결과 초기화
        _expiry_judge_results[expiry_hour] = {}

        def judge_task(ctx):
            from src.infrastructure.database.repos import InventoryBatchRepository

            batch_repo = InventoryBatchRepository()
            judged = batch_repo.judge_expiry_batches(store_id=ctx.store_id)

            # 판정 결과를 글로벌에 저장 (10분 후 confirm에서 사용)
            _expiry_judge_results[expiry_hour][ctx.store_id] = judged

            logger.info(f"[{ctx.store_id}] 폐기 판정: {len(judged)}건, "
                        f"총 {sum(b['remaining_qty'] for b in judged)}개")
            return {"success": True, "judged_count": len(judged)}

        _run_task(judge_task, f"ExpiryJudge({expiry_hour:02d}:00)")

    return wrapper


def expiry_confirm_wrapper(expiry_hour: int) -> Callable[[], None]:
    """[3단계] 폐기 10분 후 수집 + 확정

    1) 판매 수집 (10분간 변동 반영)
    2) 판정 시 stock vs 현재 stock 비교
    3) 10분간 판매분 추가 차감 → 실제 폐기량 결정
    """
    def wrapper() -> None:
        logger.info("=" * 60)
        logger.info(f"[정밀폐기 3/3] Confirm ({expiry_hour:02d}:00) "
                    f"at {datetime.now().isoformat()}")
        logger.info("=" * 60)

        def confirm_task(ctx):
            # 1) 판매 수집 (10분간 변동 반영)
            try:
                from src.scheduler.daily_job import DailyCollectionJob

                job = DailyCollectionJob(store_id=ctx.store_id)
                result = job.run_optimized(run_auto_order=False)

                if result["success"]:
                    logger.info(f"[{ctx.store_id}] 폐기 후 수집 완료: "
                                f"{result.get('total_items', 0)}건")
                else:
                    logger.warning(f"[{ctx.store_id}] 폐기 후 수집 실패")
            except Exception as e:
                logger.error(f"[{ctx.store_id}] 폐기 후 수집 error: {e}")

            # 2) 판정 결과 가져오기
            judged = _expiry_judge_results.get(expiry_hour, {}).get(ctx.store_id, [])

            if not judged:
                logger.info(f"[{ctx.store_id}] 폐기 대상 없음 (판정 0건)")
                return {"success": True, "expired_count": 0}

            # 3) 폐기 확정 (stock 비교 → 판매분 차감 → 최종 폐기)
            from src.infrastructure.database.repos import InventoryBatchRepository

            batch_repo = InventoryBatchRepository()
            confirmed = batch_repo.confirm_expiry_batches(
                judged_batches=judged,
                store_id=ctx.store_id
            )

            logger.info(f"[{ctx.store_id}] 폐기 확정: {len(confirmed)}건")
            for item in confirmed:
                logger.info(
                    f"  - {item.get('item_cd', '')} {item.get('item_nm', '')}: "
                    f"{item.get('adjusted_qty', 0)}개 폐기"
                )

            # 판정 결과 정리
            _expiry_judge_results.get(expiry_hour, {}).pop(ctx.store_id, None)

            return {"success": True, "expired_count": len(confirmed)}

        _run_task(confirm_task, f"ExpiryConfirm({expiry_hour:02d}:00)")

    return wrapper


def delivery_confirm_wrapper(delivery_type: str) -> Callable[[], None]:
    """배송 도착 후 배치 동기화 래퍼

    1차 도착(20:00) -> 20:30 실행
    2차 도착(07:00) -> 07:30 실행

    배치 만료 체크 + 만료 임박 상품 로깅
    """
    def wrapper() -> None:
        config = DELIVERY_CONFIG.get(delivery_type, {})
        arrival_hour = config.get("arrival_hour", "?")

        logger.info("=" * 60)
        logger.info(f"Delivery confirm ({delivery_type}, "
                    f"도착 {arrival_hour:02d}:00) at {datetime.now().isoformat()}")
        logger.info("=" * 60)

        def confirm_task(ctx):
            from src.infrastructure.database.repos import InventoryBatchRepository

            batch_repo = InventoryBatchRepository()

            # 만료 체크
            expired = batch_repo.check_and_expire_batches(store_id=ctx.store_id)
            if expired:
                logger.info(f"[{ctx.store_id}] {delivery_type} 만료 배치: {len(expired)}건")

            # 만료 임박 상품 조회 (1일 이내)
            expiring_soon = batch_repo.get_expiring_soon(days_ahead=1, store_id=ctx.store_id)
            if expiring_soon:
                logger.info(f"[{ctx.store_id}] {delivery_type} 만료 임박: {len(expiring_soon)}건")
                for item in expiring_soon[:5]:
                    logger.info(f"  - {item.get('item_cd', '')} {item.get('item_nm', '')}: "
                                f"만료 {item.get('expiry_date', '')}")
            else:
                logger.info(f"[{ctx.store_id}] {delivery_type} 만료 임박 상품 없음")

            return {"success": True}

        _run_task(confirm_task, f"DeliveryConfirm({delivery_type})")

    return wrapper


def waste_report_wrapper() -> None:
    """일일 폐기 보고서 엑셀 생성

    매일 23:00 실행: data/expiry_reports/YYYY-MM-DD_폐기보고서.xlsx
    리포트 전에 order_tracking 상태 전이 + 배치 만료를 선행 처리하여
    당일 폐기 데이터가 보고서에 포함되도록 함.
    """
    logger.info("=" * 60)
    logger.info(f"Waste report generation at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    from src.application.use_cases.waste_report_flow import WasteReportFlow

    def waste_task(ctx):
        # [C-2] order_tracking 상태 전이 (ordered->arrived->expired)
        try:
            from src.infrastructure.database.repos import OrderTrackingRepository

            tracking_repo = OrderTrackingRepository()
            status_result = tracking_repo.auto_update_statuses(store_id=ctx.store_id)
            logger.info(f"[{ctx.store_id}] order_tracking 상태 전이: "
                        f"arrived={status_result.get('arrived', 0)}, "
                        f"expired={status_result.get('expired', 0)}")
        except Exception as e:
            logger.warning(f"[{ctx.store_id}] order_tracking 상태 전이 실패: {e}")

        # [M-1] 배치 만료 처리 (리포트 전에 실행하여 당일분 포함)
        try:
            from src.infrastructure.database.repos import InventoryBatchRepository

            batch_repo = InventoryBatchRepository()
            expired = batch_repo.check_and_expire_batches(store_id=ctx.store_id)
            if expired:
                logger.info(f"[{ctx.store_id}] 배치 만료 처리: {len(expired)}건")
        except Exception as e:
            logger.warning(f"[{ctx.store_id}] 배치 만료 처리 실패: {e}")

        # [M-3] 폐기 보고서 생성
        return WasteReportFlow(store_ctx=ctx).run()

    _run_task(waste_task, "WasteReport")


def pre_alert_collection_wrapper(expiry_hour: int) -> Callable[[], None]:
    """폐기 알림 직전 데이터 수집 래퍼

    폐기 알림 30분 전에 최신 판매/재고 데이터를 수집하여
    정확한 잔여 재고 기반으로 알림을 보낼 수 있게 함.

    21:00 -> 22:00 폐기(1차 샌드위치/햄버거) 알림용 수집
    01:00 -> 02:00 폐기(2차) 알림용 수집
    """
    def wrapper() -> None:
        logger.info("=" * 60)
        logger.info(f"Pre-alert collection ({expiry_hour:02d}:00 폐기용) "
                    f"at {datetime.now().isoformat()}")
        logger.info("=" * 60)

        def pre_alert_task(ctx):
            # 1) 판매 데이터 수집 (DailyCollectionJob으로 수집만, 발주 안 함)
            try:
                from src.scheduler.daily_job import DailyCollectionJob

                job = DailyCollectionJob(store_id=ctx.store_id)
                result = job.run_optimized(run_auto_order=False)

                if result["success"]:
                    total = result.get("total_items", 0)
                    logger.info(f"[{ctx.store_id}] Pre-alert collection 완료: {total}건")
                else:
                    logger.warning(f"[{ctx.store_id}] Pre-alert collection 실패: "
                                   f"{result.get('error', 'Unknown')}")
            except Exception as e:
                logger.error(f"[{ctx.store_id}] Pre-alert collection error: {e}")

            # 2) order_tracking 상태 전이
            try:
                from src.infrastructure.database.repos import OrderTrackingRepository

                tracking_repo = OrderTrackingRepository()
                status_result = tracking_repo.auto_update_statuses(store_id=ctx.store_id)
                logger.info(f"[{ctx.store_id}] order_tracking 상태 전이: "
                            f"arrived={status_result.get('arrived', 0)}, "
                            f"expired={status_result.get('expired', 0)}")
            except Exception as e:
                logger.warning(f"[{ctx.store_id}] order_tracking 상태 전이 실패: {e}")

            # 3) inventory_batches FIFO 재동기화 + 만료 처리
            # 최신 판매 데이터 반영 후 배치 잔여수량과 실제 재고 정합성 맞춤
            try:
                from src.infrastructure.database.repos import InventoryBatchRepository

                batch_repo = InventoryBatchRepository()

                # 3-a) FIFO 재동기화 (stock 기반)
                sync_result = batch_repo.sync_remaining_with_stock(
                    store_id=ctx.store_id
                )
                if sync_result.get("adjusted", 0) > 0:
                    logger.info(
                        f"[{ctx.store_id}] 배치 FIFO 재동기화: "
                        f"보정 {sync_result['adjusted']}건, "
                        f"consumed {sync_result['consumed']}건"
                    )

                # 3-b) 만료 배치 처리 (재동기화 후 정확한 remaining_qty 기반)
                expired = batch_repo.check_and_expire_batches(
                    store_id=ctx.store_id
                )
                if expired:
                    logger.info(
                        f"[{ctx.store_id}] pre-alert 만료 배치: "
                        f"{len(expired)}건"
                    )
            except Exception as e:
                logger.warning(f"[{ctx.store_id}] 배치 동기화/만료 실패: {e}")

            return {"success": True}

        _run_task(pre_alert_task, f"Pre-alert({expiry_hour:02d}:00)")

    return wrapper


def token_refresh_wrapper() -> None:
    """카카오 토큰 사전 갱신 (매일 06:30)

    access_token을 사전에 갱신하여 만료를 방지.
    refresh_token은 만료 30일 전부터 갱신 시 자동 재발급되므로,
    매일 실행하면 사실상 영구 유지됨.
    갱신 실패 시 Selenium 자동 재인증 시도.
    """
    logger.info("=" * 60)
    logger.info(f"Token refresh at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    if not DEFAULT_REST_API_KEY:
        logger.warning("KAKAO_REST_API_KEY 미설정. 토큰 갱신 건너뜀.")
        return

    try:
        notifier = KakaoNotifier(DEFAULT_REST_API_KEY)

        if not notifier.access_token and not notifier.refresh_token:
            logger.warning("저장된 토큰 없음. 초기 인증 필요.")
            # Selenium 자동 인증 시도
            if notifier.selenium_auto_authorize():
                logger.info("Selenium 초기 인증 성공")
            else:
                logger.error("초기 인증 실패. 수동 --auth 필요.")
            return

        # access_token 갱신 (refresh_token 갱신도 자동 포함)
        if notifier.refresh_access_token():
            logger.info("토큰 사전 갱신 완료")
        else:
            logger.warning("토큰 갱신 실패. Selenium 재인증 시도...")
            if notifier.selenium_auto_authorize():
                logger.info("Selenium 재인증 성공")
            else:
                logger.error("모든 토큰 복구 실패. 카카오 알림 불가.")

    except Exception as e:
        logger.error(f"Token refresh error: {e}")


def manual_order_detect_wrapper() -> None:
    """수동 발주 감지 래퍼

    배송 스케줄:
    - 1차 입고: 당일 20:00 -> 21:00에 감지 실행
    - 2차 입고: 익일 07:00 -> 08:00에 감지 실행
    """
    logger.info("=" * 60)
    logger.info(f"Manual order detection at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    def detect_task(ctx):
        try:
            from src.scheduler.daily_job import run_manual_order_detect

            result = run_manual_order_detect(store_id=ctx.store_id)

            if result.get('detected', 0) > 0:
                logger.info(f"[{ctx.store_id}] 수동 발주 감지: "
                            f"{result['detected']}건 감지, "
                            f"{result['saved']}건 저장")
            else:
                logger.info(f"[{ctx.store_id}] 수동 발주 없음")

            return {"success": True, "result": result}
        except Exception as e:
            logger.error(f"[{ctx.store_id}] Manual order detection error: {e}")
            return {"success": False, "error": str(e)}

    _run_task(detect_task, "ManualOrderDetect")


def bulk_collect_wrapper() -> None:
    """벌크 상품 상세 수집 (유통기한 + 행사 정보)

    매일 11:00 실행: 유통기한 미등록 활성 상품을 일괄 수집.
    이전 중단 지점부터 자동 재개 (resume=True).
    """
    logger.info("=" * 60)
    logger.info(f"Bulk product detail collection at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    from src.application.use_cases.batch_collect_flow import BatchCollectFlow

    _run_task(
        task_fn=lambda ctx: BatchCollectFlow(store_ctx=ctx).run(),
        task_name="BulkCollect",
    )


def ml_train_wrapper() -> None:
    """ML 모델 주간 재학습 (매주 월요일 03:00) -- 매장별 병렬"""
    logger.info("=" * 60)
    logger.info(f"ML model training at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    from src.application.use_cases.ml_training_flow import MLTrainingFlow

    _run_task(
        task_fn=lambda ctx: MLTrainingFlow(store_ctx=ctx).run(),
        task_name="MLTrain",
    )


def association_mining_wrapper() -> None:
    """연관 규칙 채굴 (매일 05:00) -- 매장별 병렬

    07:00 발주 전에 완료하여, 발주 예측 시 연관 부스트를 반영한다.
    일별 동시 판매 패턴(temporal co-occurrence)을 분석하여
    mid(중분류) 레벨 + item(상품) 레벨 연관 규칙을 갱신한다.
    """
    logger.info("=" * 60)
    logger.info(f"Association mining at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    from src.prediction.association.association_miner import AssociationMiner

    def _mine(ctx):
        miner = AssociationMiner(store_id=ctx.store_id)
        return miner.mine_all()

    _run_task(
        task_fn=_mine,
        task_name="AssociationMining",
    )


def bayesian_optimize_wrapper() -> None:
    """주간 베이지안 최적화 (일요일 23:00)"""
    logger.info("=" * 60)
    logger.info(f"Bayesian Optimization at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    def task(ctx):
        from src.prediction.bayesian_optimizer import (
            BayesianParameterOptimizer,
            BAYESIAN_ENABLED,
        )
        if not BAYESIAN_ENABLED:
            return {"skipped": True, "reason": "disabled"}

        optimizer = BayesianParameterOptimizer(store_id=ctx.store_id)
        result = optimizer.optimize()
        return result.to_dict()

    _run_task(task, "BayesianOptimize")


def order_unit_collect_wrapper() -> None:
    """전체 품목 발주단위 수집 (매일 00:00)

    BGF 사이트 로그인 -> 홈 화면 바코드 검색 ->
    CallItemDetailPopup에서 ORD_UNIT_QTY 수집 ->
    common.db product_details 갱신 -> 로그아웃

    BGF 계정은 매장별이 아닌 단일 계정이므로
    _run_task(매장별 병렬)가 아닌 직접 실행.
    """
    logger.info("=" * 60)
    logger.info(f"Order unit qty collection at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    try:
        from src.sales_analyzer import SalesAnalyzer
        from src.collectors.order_status_collector import OrderStatusCollector
        from src.infrastructure.database.repos import ProductDetailRepository
        from src.settings.timing import SA_LOGIN_WAIT, SA_POPUP_CLOSE_WAIT

        # 1. 수집 대상: 최근 30일 판매 실적이 있는 활성 상품만
        from src.infrastructure.database.connection import DBRouter

        repo = ProductDetailRepository()
        store_id = _DEFAULT_STORE["store_id"]
        store_conn = DBRouter.get_store_connection(store_id)
        try:
            cursor = store_conn.cursor()
            cursor.execute("""
                SELECT DISTINCT item_cd FROM daily_sales
                WHERE sales_date >= date('now', '-30 days')
                ORDER BY item_cd
            """)
            item_codes = [row[0] for row in cursor.fetchall()]
        finally:
            store_conn.close()

        if not item_codes:
            logger.warning("수집 대상 상품이 없습니다")
            return

        logger.info(f"수집 대상: {len(item_codes)}개 상품")

        # 2. BGF 사이트 로그인
        analyzer = SalesAnalyzer()
        try:
            analyzer.setup_driver()
            analyzer.connect()
            time.sleep(SA_LOGIN_WAIT)

            if not analyzer.do_login():
                logger.error("BGF 로그인 실패")
                return

            time.sleep(SA_POPUP_CLOSE_WAIT * 2)
            analyzer.close_popup()
            time.sleep(SA_POPUP_CLOSE_WAIT)

            # 3. 홈 화면 바코드 검색으로 발주단위 수집
            collector = OrderStatusCollector(
                driver=analyzer.driver,
                store_id=_DEFAULT_STORE["store_id"],
            )

            items = collector.collect_order_unit_via_home(item_codes)

            if not items:
                logger.warning("수집된 발주단위 데이터 없음")
                return

            # 4. common.db 저장
            updated = repo.bulk_update_order_unit_qty(items)

            logger.info(
                f"발주단위 수집 완료: {len(items)}개 수집, {updated}개 갱신"
            )
        finally:
            analyzer.close()
    except Exception as e:
        logger.error(f"Order unit collection error: {e}")
        import traceback
        traceback.print_exc()


# ── 스케줄러 메인 ──

def run_scheduler(schedule_time: str = "07:00", multi_store: bool = True) -> None:
    """
    스케줄러 실행

    Args:
        schedule_time: 실행 시간 (HH:MM 형식)
        multi_store: 멀티 스토어 모드 활성화 여부
    """
    global _MULTI_STORE
    _MULTI_STORE = multi_store

    # 중복 실행 방지
    if not acquire_lock():
        sys.exit(1)

    # 프로그램 종료 시 락 파일 삭제
    atexit.register(release_lock)

    logger.info("=" * 60)
    logger.info("BGF Auto Sales Collector - Scheduler")
    if multi_store:
        logger.info("[MULTI-STORE MODE] 모든 작업이 활성 매장별 병렬 실행됩니다")
    logger.info("=" * 60)
    logger.info(f"[Scheduler] Started at: {datetime.now().isoformat()}")
    logger.info(f"[Scheduler] PID: {os.getpid()}")
    logger.info("[Scheduler] Press Ctrl+C to stop")
    logger.info("=" * 60)

    # DB 초기화
    init_db()

    # 0. 카카오 토큰 사전 갱신 (매일 06:30)
    schedule.every().day.at("06:30").do(token_refresh_wrapper)
    logger.info("[Schedule] Kakao token refresh: 06:30")

    # 1. 매일 지정 시간에 데이터 수집 + 자동 발주 실행
    if multi_store:
        schedule.every().day.at(schedule_time).do(job_wrapper_multi_store)
        logger.info(f"[Schedule] Multi-Store collection + auto-order: {schedule_time}")
    else:
        schedule.every().day.at(schedule_time).do(job_wrapper)
        logger.info(f"[Schedule] Daily collection + auto-order: {schedule_time}")

    # 2. 폐기 알림 + 정밀 폐기 3단계 스케줄
    #    기존 알림: 수집(1시간 전) -> 알림(30분 전) -> 폐기
    #    정밀 폐기: 수집(10분 전) -> 판정(정시) -> 수집+확정(10분 후)
    PRE_ALERT_COLLECTION_SCHEDULE = {
        22: "21:00",  # 22:00 폐기(1차 샌드위치/햄버거) -> 21:00 수집 -> 21:30 알림
        14: "13:00",  # 14:00 폐기(2차 도시락/주먹밥/김밥) -> 13:00 수집 -> 13:30 알림
        10: "09:00",  # 10:00 폐기(2차 샌드위치/햄버거) -> 09:00 수집 -> 09:30 알림
        2: "01:00",   # 02:00 폐기(1차 도시락/주먹밥/김밥) -> 01:00 수집 -> 01:30 알림
        0: "22:00",   # 00:00 만료(빵) -> 22:00 수집 -> 23:00 알림
    }
    logger.info("[Schedule] Pre-alert collection + Expiry alerts:")
    for expiry_hour, collect_time in PRE_ALERT_COLLECTION_SCHEDULE.items():
        schedule.every().day.at(collect_time).do(pre_alert_collection_wrapper(expiry_hour))
        logger.info(f"  - {collect_time} -> {expiry_hour:02d}:00 폐기 전 수집 (알림용)")

    for expiry_hour, alert_time in EXPIRY_ALERT_SCHEDULE.items():
        schedule.every().day.at(alert_time).do(expiry_alert_wrapper(expiry_hour))
        logger.info(f"  - {alert_time} -> {expiry_hour:02d}:00 폐기 알림")

    # 2.5 정밀 폐기 3단계 (10분전 수집 → 판정 → 10분후 수집+확정)
    logger.info("[Schedule] Precise expiry confirmation (3-step):")
    for expiry_hour, times in EXPIRY_CONFIRM_SCHEDULE.items():
        schedule.every().day.at(times["pre_collect"]).do(
            expiry_pre_collect_wrapper(expiry_hour)
        )
        schedule.every().day.at(times["judge"]).do(
            expiry_judge_wrapper(expiry_hour)
        )
        schedule.every().day.at(times["post_collect"]).do(
            expiry_confirm_wrapper(expiry_hour)
        )
        logger.info(
            f"  - {expiry_hour:02d}:00 폐기: "
            f"{times['pre_collect']} 수집 -> {times['judge']} 판정 -> "
            f"{times['post_collect']} 수집+확정"
        )

    # 3. 벌크 상품 상세 수집 (매일 11:00)
    # 유통기한 미등록 활성 상품 일괄 수집 (resume 모드)
    schedule.every().day.at("11:00").do(bulk_collect_wrapper)
    logger.info("[Schedule] Bulk product detail collection: 11:00")

    # 3.5 수동 발주 감지 (08:00, 21:00)
    # 배송 스케줄: 발주마감 10:00, 1차 입고 당일 20:00, 2차 입고 익일 07:00
    # 08:00: 2차 입고(07:00) 완료 후 수동발주 감지
    # 21:00: 1차 입고(20:00) 완료 후 수동발주 감지
    schedule.every().day.at("08:00").do(manual_order_detect_wrapper)
    schedule.every().day.at("21:00").do(manual_order_detect_wrapper)
    logger.info("[Schedule] Manual order detection: 08:00, 21:00")

    # 4. 주간 종합 리포트 (매주 월요일 08:00)
    # 포함: 카테고리 트렌드 + 상품 급등/급락 + 신규 인기 + 예측 정확도
    schedule.every().monday.at("08:00").do(weekly_report_wrapper)
    logger.info("[Schedule] Weekly trend report: Monday 08:00")

    # 5. 배송 도착 후 배치 동기화
    # 2차 배송 도착(07:00) -> 07:30 배치 체크
    schedule.every().day.at("07:30").do(delivery_confirm_wrapper("2차"))
    logger.info("[Schedule] Delivery confirm (2차): 07:30")
    # 1차 배송 도착(20:00) -> 20:30 배치 체크
    schedule.every().day.at("20:30").do(delivery_confirm_wrapper("1차"))
    logger.info("[Schedule] Delivery confirm (1차): 20:30")

    # 6. 일일 폐기 보고서 (매일 23:00)
    schedule.every().day.at("23:00").do(waste_report_wrapper)
    logger.info("[Schedule] Daily waste report: 23:00")

    # 7. 배치 유통기한 만료 처리 (매일 23:30)
    schedule.every().day.at("23:30").do(batch_expire_wrapper)
    logger.info("[Schedule] Batch expire check: 23:30")

    # 8. ML 모델 재학습 (매주 월요일 03:00)
    schedule.every().monday.at("03:00").do(ml_train_wrapper)
    logger.info("[Schedule] ML model training: Monday 03:00")

    # 9. 연관 규칙 채굴 (매일 05:00)
    schedule.every().day.at("05:00").do(association_mining_wrapper)
    logger.info("[Schedule] Association rule mining: 05:00")

    # 10. 전체 품목 발주단위 수집 (매일 00:00)
    # BGF 사이트 발주현황조회 "전체" 탭에서 ORD_UNIT_QTY 수집 -> common.db 갱신
    schedule.every().day.at("00:00").do(order_unit_collect_wrapper)
    logger.info("[Schedule] Order unit qty collection: 00:00")

    # 11. 베이지안 파라미터 최적화 (매주 일요일 23:00)
    schedule.every().sunday.at("23:00").do(bayesian_optimize_wrapper)
    logger.info("[Schedule] Bayesian parameter optimization: Sunday 23:00")

    logger.info("=" * 60)

    # 등록된 작업 수 표시
    logger.info(f"[Scheduler] Total jobs: {len(schedule.jobs)}")

    # 다음 실행 시간 표시
    next_run = schedule.next_run()
    logger.info(f"[Scheduler] Next run: {next_run}")

    # 무한 루프
    while True:
        schedule.run_pending()
        time.sleep(60)  # 1분마다 체크


def run_now(multi_store: bool = True) -> None:
    """즉시 실행 (데이터 수집 + 자동 발주)"""
    global _MULTI_STORE
    _MULTI_STORE = multi_store

    init_db()
    if multi_store:
        logger.info("Running multi-store job immediately...")
        job_wrapper_multi_store()
    else:
        logger.info("Running job immediately...")
        job_wrapper()


def run_expiry_alert_now(expiry_hour: int) -> None:
    """특정 폐기 시간 알림 즉시 실행 (하위 호환용)"""
    logger.info(f"Running expiry alert for {expiry_hour:02d}:00...")
    init_db()

    def alert_task(ctx):
        from src.alert.expiry_checker import ExpiryChecker

        checker = ExpiryChecker(store_id=ctx.store_id)
        try:
            result = checker.send_expiry_alert(expiry_hour)
            if result:
                logger.info(f"[{ctx.store_id}] Expiry alert sent")
            else:
                logger.warning(f"[{ctx.store_id}] No items to alert")
            return {"success": True, "sent": bool(result)}
        finally:
            checker.close()

    _run_task(alert_task, f"ExpiryAlert({expiry_hour:02d}:00)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="BGF Auto Sales Collector Scheduler"
    )
    parser.add_argument(
        "--now", "-n",
        action="store_true",
        help="Run collection + auto-order immediately"
    )
    parser.add_argument(
        "--time",
        type=str,
        default="07:00",
        help="Schedule time in HH:MM format (default: 07:00)"
    )
    parser.add_argument(
        "--expiry",
        type=int,
        choices=[0, 2, 10, 14, 22],
        help="Send expiry alert for specific hour (0=빵만료, 2=02:00, 10=10:00, 14=14:00, 22=22:00)"
    )
    parser.add_argument(
        "--weekly-report",
        action="store_true",
        help="Send weekly trend report immediately (category + product + accuracy)"
    )
    parser.add_argument(
        "--waste-report",
        action="store_true",
        help="Generate waste report (Excel) immediately"
    )
    parser.add_argument(
        "--batch-expire",
        action="store_true",
        help="Run batch expiry check immediately"
    )
    parser.add_argument(
        "--token-refresh",
        action="store_true",
        help="Refresh Kakao token immediately"
    )
    parser.add_argument(
        "--bulk-collect",
        action="store_true",
        help="Run bulk product detail collection immediately"
    )
    parser.add_argument(
        "--detect-manual",
        action="store_true",
        help="Run manual order detection immediately"
    )
    parser.add_argument(
        "--association-mine",
        action="store_true",
        help="Run association rule mining immediately"
    )
    parser.add_argument(
        "--collect-order-unit",
        action="store_true",
        help="Collect order unit qty from BGF site immediately"
    )
    parser.add_argument(
        "--bayesian-optimize",
        action="store_true",
        help="Run Bayesian parameter optimization immediately"
    )
    parser.add_argument(
        "--multi-store",
        action="store_true",
        default=True,
        help="Enable multi-store mode (default: enabled)"
    )
    parser.add_argument(
        "--single-store",
        action="store_true",
        help="Disable multi-store mode (run single store only)"
    )
    parser.add_argument(
        "--sync-waste-backfill",
        action="store_true",
        help="Backfill waste_slip_items -> daily_sales.disuse_qty sync"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days for backfill (default: 30, used with --sync-waste-backfill)"
    )
    parser.add_argument(
        "--store",
        type=str,
        default=None,
        help="Run for specific store ID only (e.g., --store 46513)"
    )

    args = parser.parse_args()

    # --single-store면 멀티 비활성, 아니면 기본 멀티
    if args.single_store:
        _MULTI_STORE = False
    elif args.multi_store:
        _MULTI_STORE = True

    try:
        if args.sync_waste_backfill:
            init_db()
            if args.store:
                store_ids = [args.store]
            else:
                store_ids = _runner.get_active_store_ids() if _MULTI_STORE else [_DEFAULT_STORE["store_id"]]
            for sid in store_ids:
                logger.info(f"[Waste Backfill] Store {sid}: {args.days} days")
                from src.application.services.waste_disuse_sync_service import (
                    WasteDisuseSyncService,
                )
                syncer = WasteDisuseSyncService(store_id=sid)
                result = syncer.backfill(days=args.days)
                logger.info(
                    f"[Waste Backfill] Store {sid} done: "
                    f"updated={result['total_updated']}, "
                    f"inserted={result['total_inserted']}, "
                    f"skipped={result['total_skipped']}"
                )
        elif args.collect_order_unit:
            init_db()
            order_unit_collect_wrapper()
        elif args.bayesian_optimize:
            init_db()
            bayesian_optimize_wrapper()
        elif args.association_mine:
            init_db()
            association_mining_wrapper()
        elif args.detect_manual:
            init_db()
            manual_order_detect_wrapper()
        elif args.bulk_collect:
            init_db()
            bulk_collect_wrapper()
        elif args.token_refresh:
            token_refresh_wrapper()
        elif args.weekly_report:
            init_db()
            weekly_report_wrapper()
        elif args.waste_report:
            init_db()
            waste_report_wrapper()
        elif args.batch_expire:
            init_db()
            batch_expire_wrapper()
        elif args.expiry is not None:
            init_db()
            run_expiry_alert_now(args.expiry)
        elif args.now:
            if args.store:
                logger.info(f"Running job for store {args.store} immediately...")
                init_db()
                from src.scheduler.daily_job import DailyCollectionJob
                job = DailyCollectionJob(store_id=args.store)
                result = job.run_optimized(
                    run_auto_order=True, use_improved_predictor=True
                )
                if result.get("success"):
                    logger.info("Job completed successfully")
                    logger.info(f"  - Items: {result.get('total_items', 0)}")
                    order = result.get("order")
                    if order:
                        logger.info(
                            f"  - Order: {order.get('success_count', 0)} success, "
                            f"{order.get('fail_count', 0)} fail"
                        )
                else:
                    logger.error(f"Job failed: {result.get('error', 'Unknown')}")
            else:
                run_now(multi_store=args.multi_store)
        else:
            run_scheduler(args.time, multi_store=args.multi_store)

    except KeyboardInterrupt:
        print("\n[Scheduler] Stopped by user")
        release_lock()
        sys.exit(0)
