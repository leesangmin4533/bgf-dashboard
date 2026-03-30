"""
스케줄러 실행기 (Use Case 아키텍처)

- 매일 아침 7시: 전날+당일 데이터 수집 -> 자동 발주
- 매일 11시: 벌크 상품 상세 수집 (유통기한 + 행사 정보)
- 폐기 알림 전 수집: 21:00(22시), 13:00(14시), 09:00(10시), 01:00(02시), 22:00(빵)
- 폐기 30분 전 알림: 21:30(22시), 13:30(14시), 09:30(10시), 01:30(02시)
- 빵 유통기한 1시간 전 알림: 23:00(자정 만료)
- 정밀 폐기 확정 (3단계): 10분 전 수집 → 판정(정시) → 10분 후 수집+확정
  폐기 시간대: 02:00, 10:00, 14:00, 22:00, 00:00
- 매일 10:30: 발주 확정 pending 동기화 (BGF 발주현황 → pending 마킹)
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
    python run_scheduler.py --fetch-detail       # 상품 상세 정보 일괄 수집 즉시 실행
    python run_scheduler.py --pending-sync         # 발주 pending 동기화 즉시 실행
    python run_scheduler.py --sync-waste-backfill --days 20  # 폐기전표→daily_sales 동기화 백필
"""

import sys
import os
import io
import time
import argparse
import atexit
import subprocess
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

# .env 로드 (kakao_notifier 등 모듈 레벨 환경변수 참조 전에 실행)
from dotenv import load_dotenv
load_dotenv(project_root / ".env")

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
from alert.config import EXPIRY_CONFIRM_SCHEDULE, DELIVERY_CONFIG
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
                    print("[WARN] 오래된 락 파일 발견. 삭제합니다.")
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
        result = job.run_optimized(run_auto_order=True, use_improved_predictor=True)

        # Phase별 시간 로깅
        timings = result.get("phase_timings", {})
        if timings:
            logger.info(
                "[%s] Phase timings: %s (total=%.1fs)",
                ctx.store_id,
                " | ".join(f"{k}={v}s" for k, v in timings.items()),
                result.get("duration", 0),
            )
        return result

    _runner.run_parallel(
        task_fn=_run_daily_order,
        task_name="daily_order",
    )

    # PythonAnywhere DB 동기화 (멀티매장 완료 후)
    _try_cloud_sync()


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

        # PythonAnywhere DB 동기화 (발주 성공 시)
        _try_cloud_sync()
    else:
        logger.error(f"Job failed: {result.get('error', 'Unknown')}")


def _try_cloud_sync() -> None:
    """PythonAnywhere DB 동기화 시도 (실패해도 발주 결과에 영향 없음)."""
    try:
        from scripts.sync_to_cloud import run_cloud_sync
        sync_result = run_cloud_sync()
        if sync_result.get("skipped"):
            logger.debug("[CloudSync] 설정 없음 - 건너뜀")
        elif sync_result.get("success"):
            logger.info("[CloudSync] PythonAnywhere DB 동기화 완료")
        else:
            logger.warning("[CloudSync] 동기화 실패 (발주 결과에 영향 없음)")
    except Exception as e:
        logger.warning(f"[CloudSync] 동기화 오류 (무시됨): {e}")


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
    """[1단계] 폐기 10분 전: 판매 수집 + 예고 알림 발송

    폐기 시점 직전의 최신 stock을 확보하고,
    수집 직후 최신 데이터로 예고 알림을 발송한다.
    (기존 PRE_ALERT_COLLECTION + EXPIRY_ALERT 합류)
    """
    def wrapper() -> None:
        logger.info("=" * 60)
        logger.info(f"[정밀폐기 1/3] Pre-collect + Alert ({expiry_hour:02d}:00 폐기용) "
                    f"at {datetime.now().isoformat()}")
        logger.info("=" * 60)

        def collect_and_alert_task(ctx):
            # 1) BGF 사이트 수집 (기존)
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

            # 3) 배치 FIFO 재동기화 + 만료 처리
            try:
                from src.infrastructure.database.repos import InventoryBatchRepository

                batch_repo = InventoryBatchRepository(store_id=ctx.store_id)
                sync_result = batch_repo.sync_remaining_with_stock(store_id=ctx.store_id)
                adj = sync_result.get("adjusted", 0)
                ghost = sync_result.get("ghost_cleared", 0)
                if adj > 0 or ghost > 0:
                    logger.info(f"[{ctx.store_id}] 배치 FIFO 재동기화: "
                                f"보정 {adj}건, consumed {sync_result.get('consumed', 0)}건, "
                                f"푸드 유령재고 {ghost}건 정리")
                expired = batch_repo.check_and_expire_batches(store_id=ctx.store_id)
                if expired:
                    logger.info(f"[{ctx.store_id}] pre-alert 만료 배치: {len(expired)}건")
            except Exception as e:
                logger.warning(f"[{ctx.store_id}] 배치 동기화/만료 실패: {e}")

            # 4) 예고 알림 발송 (수집 직후, 최신 데이터)
            try:
                from src.alert.expiry_checker import ExpiryChecker

                checker = ExpiryChecker(store_id=ctx.store_id, store_name=ctx.store_name)
                try:
                    alert_result = checker.send_expiry_alert(expiry_hour)
                    if alert_result:
                        logger.info(f"[{ctx.store_id}] {expiry_hour:02d}:00 예고 알림 발송 완료")
                    else:
                        logger.info(f"[{ctx.store_id}] {expiry_hour:02d}:00 폐기 대상 없음")
                finally:
                    checker.close()
            except Exception as e:
                logger.error(f"[{ctx.store_id}] 예고 알림 발송 실패: {e}")

            return {"success": True}

        _run_task(collect_and_alert_task, f"ExpiryPreCollect+Alert({expiry_hour:02d}:00)")

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
    """[3단계] 폐기 10분 후: 폐기전표 수집 → 미폐기 상품 경고

    1) 폐기전표 수집 (BGF 통합전표 > 전표구분=폐기)
    2) step1 예고 대상과 폐기전표 대조
    3) 폐기전표에 없는 상품 = 미폐기 → 경고 알림
    4) 폐기전표에 있는 상품 = 폐기 완료 → 확인 알림
    """
    def wrapper() -> None:
        logger.info("=" * 60)
        logger.info(f"[정밀폐기 3/3] Confirm + WasteSlip ({expiry_hour:02d}:00) "
                    f"at {datetime.now().isoformat()}")
        logger.info("=" * 60)

        def confirm_task(ctx):
            # 1) 폐기전표 수집 (BGF 사이트)
            waste_item_codes = set()
            try:
                from src.collectors.waste_slip_collector import WasteSlipCollector
                from src.scheduler.daily_job import DailyCollectionJob

                job = DailyCollectionJob(store_id=ctx.store_id)
                driver = job.collector.get_driver()
                if driver:
                    today_str = datetime.now().strftime("%Y%m%d")
                    ws_collector = WasteSlipCollector(driver=driver, store_id=ctx.store_id)
                    ws_result = ws_collector.collect_waste_slips(
                        from_date=today_str, to_date=today_str, save_to_db=True
                    )
                    logger.info(f"[{ctx.store_id}] 폐기전표 수집: {ws_result.get('count', 0)}건")

                    # 수집된 폐기전표에서 item_cd 추출
                    from src.infrastructure.database.repos.waste_slip_repo import WasteSlipRepository
                    ws_repo = WasteSlipRepository(store_id=ctx.store_id)
                    today_dash = datetime.now().strftime("%Y-%m-%d")
                    waste_items = ws_repo.get_waste_slip_items(today_dash, store_id=ctx.store_id)
                    waste_item_codes = {item.get('item_cd') for item in waste_items if item.get('item_cd')}
                    logger.info(f"[{ctx.store_id}] 폐기전표 품목: {len(waste_item_codes)}개")
                else:
                    logger.warning(f"[{ctx.store_id}] 드라이버 없음, 폐기전표 수집 스킵")
            except Exception as e:
                logger.error(f"[{ctx.store_id}] 폐기전표 수집 실패: {e}")

            # 2) step1 예고 대상 조회
            from src.alert.expiry_checker import ExpiryChecker
            checker = ExpiryChecker(store_id=ctx.store_id, store_name=ctx.store_name)
            try:
                pre_items = checker.get_items_expiring_at(expiry_hour)
            finally:
                checker.close()

            if not pre_items:
                logger.info(f"[{ctx.store_id}] 예고 대상 0건, 스킵")
                return {"success": True, "disposed": 0, "not_disposed": 0}

            # 3) 폐기전표 대조: 폐기됨 vs 미폐기
            disposed = [i for i in pre_items if i['item_cd'] in waste_item_codes]
            not_disposed = [i for i in pre_items if i['item_cd'] not in waste_item_codes]

            logger.info(f"[{ctx.store_id}] 폐기전표 대조: "
                        f"폐기완료 {len(disposed)}건, 미폐기 {len(not_disposed)}건")

            # 4) 기존 배치 confirm도 실행 (DB 정합성 유지)
            judged = _expiry_judge_results.get(expiry_hour, {}).get(ctx.store_id, [])
            if judged:
                from src.infrastructure.database.repos import InventoryBatchRepository
                batch_repo = InventoryBatchRepository()
                batch_repo.confirm_expiry_batches(judged_batches=judged, store_id=ctx.store_id)

            # 5) 컨펌 알림 발송
            _send_confirm_alert(ctx, expiry_hour, disposed, not_disposed)

            # 판정 결과 정리
            _expiry_judge_results.get(expiry_hour, {}).pop(ctx.store_id, None)

            return {"success": True, "disposed": len(disposed), "not_disposed": len(not_disposed)}

        _run_task(confirm_task, f"ExpiryConfirm+WasteSlip({expiry_hour:02d}:00)")

    return wrapper


def _send_confirm_alert(ctx, expiry_hour: int, disposed: list, not_disposed: list) -> None:
    """폐기전표 대조 결과 알림 (나에게 + 해당 매장 단톡방)"""
    if not disposed and not not_disposed:
        return

    try:
        from src.notification.kakao_notifier import KakaoNotifier, DEFAULT_REST_API_KEY

        msg = _format_confirm_message(ctx, expiry_hour, disposed, not_disposed)

        notifier = KakaoNotifier(DEFAULT_REST_API_KEY)
        if notifier.access_token:
            notifier.send_message(msg)
            notifier.send_to_group(msg, store_id=ctx.store_id)
            logger.info(f"[{ctx.store_id}] {expiry_hour:02d}:00 컨펌 알림 발송 완료")

    except Exception as e:
        logger.error(f"[{ctx.store_id}] 컨펌 알림 발송 실패: {e}")


def _format_confirm_message(ctx, expiry_hour: int, disposed: list, not_disposed: list) -> str:
    """폐기전표 대조 결과 메시지 포맷"""
    store_name = ctx.store_name or ctx.store_id
    now_str = datetime.now().strftime('%m/%d %H:%M')

    lines = [f"[{store_name}] {expiry_hour:02d}:00 폐기 확인 ({now_str})", ""]

    # 미폐기 경고 (핵심 - 상단 배치)
    if not_disposed:
        lines.append(f"!! 미폐기 {len(not_disposed)}개 - 폐기 처리 필요 !!")
        for item in not_disposed[:10]:
            nm = item.get('item_nm', '')[:15]
            qty = item.get('remaining_qty', 0)
            cat = item.get('category_name', '')
            lines.append(f"  {nm}  {qty}개  ({cat})")
        if len(not_disposed) > 10:
            lines.append(f"  ...외 {len(not_disposed) - 10}개")
        lines.append("")

    # 폐기 완료 확인
    if disposed:
        lines.append(f"[폐기 완료] {len(disposed)}개")
        for item in disposed[:5]:
            nm = item.get('item_nm', '')[:15]
            qty = item.get('remaining_qty', 0)
            lines.append(f"  {nm}  {qty}개")
        if len(disposed) > 5:
            lines.append(f"  ...외 {len(disposed) - 5}개")
        lines.append("")

    if not not_disposed:
        lines.append("전체 폐기 완료")
    else:
        total_not = sum(i.get('remaining_qty', 0) for i in not_disposed)
        lines.append(f"미폐기 {len(not_disposed)}개 ({total_not}개) 즉시 처리 필요")

    return "\n".join(lines)


def receiving_collect_wrapper(delivery_type: str) -> Callable[[], None]:
    """배송 도착 후 센터매입 데이터 즉시 수집

    1차 입고(20:00) 후 센터매입 데이터를 즉시 수집하여
    delivery_confirm, 폐기 알림(21:30), 수동발주 감지(21:00) 등이
    정확한 재고 기준으로 동작하게 함.
    BGF 사이트 Selenium 세션을 새로 열어 ReceivingCollector로 오늘 입고분 수집.
    """
    def wrapper() -> None:
        config = DELIVERY_CONFIG.get(delivery_type, {})
        arrival_hour = config.get("arrival_hour", "?")

        logger.info("=" * 60)
        logger.info(f"Receiving collect ({delivery_type}, "
                    f"도착 {arrival_hour:02d}:00) at {datetime.now().isoformat()}")
        logger.info("=" * 60)

        def collect_task(ctx):
            try:
                from src.sales_analyzer import SalesAnalyzer
                from src.collectors.receiving_collector import ReceivingCollector
                from src.settings.timing import SA_LOGIN_WAIT, SA_POPUP_CLOSE_WAIT

                today_str = datetime.now().strftime("%Y%m%d")

                analyzer = SalesAnalyzer()
                try:
                    analyzer.setup_driver()
                    analyzer.connect()
                    time.sleep(SA_LOGIN_WAIT)

                    if not analyzer.do_login():
                        logger.error(f"[{ctx.store_id}] BGF 로그인 실패")
                        return {"success": False, "error": "login_failed"}

                    time.sleep(SA_POPUP_CLOSE_WAIT * 2)
                    analyzer.close_popup()
                    time.sleep(SA_POPUP_CLOSE_WAIT)

                    recv_collector = ReceivingCollector(
                        driver=analyzer.driver,
                        store_id=ctx.store_id,
                    )

                    if recv_collector.navigate_to_receiving_menu():
                        stats = recv_collector.collect_and_save(today_str)
                        recv_collector.close_receiving_menu()
                        logger.info(f"[{ctx.store_id}] 입고 수집 완료: {stats}")
                        return {"success": True, "stats": stats}
                    else:
                        logger.warning(f"[{ctx.store_id}] 센터매입 메뉴 이동 실패")
                        return {"success": False, "error": "navigate_failed"}
                finally:
                    analyzer.close()
            except Exception as e:
                logger.error(f"[{ctx.store_id}] Receiving collect error: {e}")
                return {"success": False, "error": str(e)}

        _run_task(collect_task, f"ReceivingCollect({delivery_type})")

    return wrapper


def delivery_confirm_wrapper(delivery_type: str) -> Callable[[], None]:
    """배송 도착 후 배치 동기화 래퍼

    1차 도착(20:00) -> 20:40 실행 (receiving_collect 후)
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

                batch_repo = InventoryBatchRepository(store_id=ctx.store_id)

                # 3-a) FIFO 재동기화 (stock 기반)
                sync_result = batch_repo.sync_remaining_with_stock(
                    store_id=ctx.store_id
                )
                adj = sync_result.get("adjusted", 0)
                ghost = sync_result.get("ghost_cleared", 0)
                if adj > 0 or ghost > 0:
                    logger.info(
                        f"[{ctx.store_id}] 배치 FIFO 재동기화: "
                        f"보정 {adj}건, consumed {sync_result.get('consumed', 0)}건, "
                        f"푸드 유령재고 {ghost}건 정리"
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


def promotion_alert_wrapper() -> None:
    """행사 변경 알림 (매일 08:30)

    send_daily_alert: 3일 내 행사 시작/종료 알림
    send_critical_alert: 종료 D-1 이내 긴급 알림
    """
    logger.info("=" * 60)
    logger.info(f"Promotion alert at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    def alert_task(ctx):
        try:
            from src.notification.kakao_notifier import KakaoNotifier, DEFAULT_REST_API_KEY
            from src.alert.promotion_alert import PromotionAlert

            notifier = KakaoNotifier(DEFAULT_REST_API_KEY)
            if not notifier.ensure_valid_token():
                logger.warning(f"[{ctx.store_id}] 카카오 토큰 갱신 실패 — 행사 알림 건너뜀")
                return {"success": False, "error": "token_failed"}

            alert = PromotionAlert(kakao_notifier=notifier, store_id=ctx.store_id)
            daily_msg = alert.send_daily_alert(send_kakao=True)
            critical_msg = alert.send_critical_alert(send_kakao=True)

            logger.info(f"[{ctx.store_id}] 행사 알림 완료 — "
                        f"일일: {'발송' if daily_msg else '없음'}, "
                        f"긴급: {'발송' if critical_msg else '없음'}")
            return {"success": True}
        except Exception as e:
            logger.error(f"[{ctx.store_id}] Promotion alert error: {e}")
            return {"success": False, "error": str(e)}

    _run_task(alert_task, "PromotionAlert")


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


def ml_train_wrapper(incremental: bool = False) -> None:
    """ML 모델 학습 -- 매장별 병렬

    Args:
        incremental: True면 증분학습(30일), False면 전체학습(90일)
    """
    # 일요일 증분 스킵: 03:00 전체학습(90일)이 증분(30일)을 포함하므로 중복
    if incremental and datetime.now().weekday() == 6:  # 0=월, 6=일
        logger.info("[MLTrain] 일요일 전체학습 완료 — 증분 건너뜀")
        return

    mode = "증분(30일)" if incremental else "전체(90일)"
    logger.info("=" * 60)
    logger.info(f"ML model training ({mode}) at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    from src.application.use_cases.ml_training_flow import MLTrainingFlow

    _run_task(
        task_fn=lambda ctx: MLTrainingFlow(store_ctx=ctx).run(incremental=incremental),
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


def payday_analyze_wrapper() -> None:
    """주간 급여일 패턴 분석 (일요일 03:30)

    매장별 daily_sales 90일 데이터에서 고매출/저매출 구간을 통계적으로 감지하여
    external_factors(factor_type='payday')에 boost_days/decline_days를 저장한다.
    get_payday_coefficient()가 DB 우선 조회로 동적 구간을 활용하게 된다.
    """
    logger.info("=" * 60)
    logger.info(f"Payday pattern analysis at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    def task(ctx):
        from src.settings.constants import PAYDAY_ENABLED
        if not PAYDAY_ENABLED:
            return {"skipped": True, "reason": "PAYDAY_ENABLED=False"}

        from src.prediction.payday_analyzer import PaydayAnalyzer
        analyzer = PaydayAnalyzer(
            store_id=ctx.store_id,
            db_path=str(ctx.db_path),
        )
        return analyzer.analyze()

    _run_task(task, "PaydayAnalyze")


def inventory_verify_wrapper() -> None:
    """주간 재고 검증 (수요일 02:00)

    전 매장 BGF 실재고 vs DB 비교 + 불일치 동기화 + 엑셀 리포트.
    data/reports/inventory_verify_YYYYMMDD_HHMMSS.xlsx 저장.
    """
    logger.info("=" * 60)
    logger.info(f"Weekly inventory verification at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    try:
        scripts_path = str(Path(__file__).parent / "scripts")
        if scripts_path not in sys.path:
            sys.path.insert(0, scripts_path)
        from scripts.verify_inventory_direct_api import run_verification_all_stores

        excel_path = run_verification_all_stores(
            threshold=1,
            sync_db=True,  # 불일치 → BGF 값으로 DB 동기화
        )
        logger.info(f"[InventoryVerify] 엑셀 리포트: {excel_path}")
    except Exception as e:
        logger.error(f"[InventoryVerify] 실패: {e}")
        import traceback
        traceback.print_exc()


def order_unit_collect_wrapper() -> None:
    """전체 품목 발주단위 수집 (매일 00:00)

    STBJ070 발주현황조회 Direct API 우선 → 홈 바코드 폴백.

    1단계: STBJ070 "전체" 라디오 → Direct API 1회 호출 (~30초)
    2단계: (1단계 실패 시) 홈 화면 바코드 1개씩 검색 (기존 방식)

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
        from src.utils.nexacro_helpers import close_tab_by_frame_id

        repo = ProductDetailRepository()
        store_id = _DEFAULT_STORE["store_id"]

        # 1. BGF 사이트 로그인
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

            collector = OrderStatusCollector(
                driver=analyzer.driver,
                store_id=store_id,
            )

            items = None

            # ★ 1단계: STBJ070 Direct API (전체 라디오 → 1회 조회)
            try:
                if collector.navigate_to_order_status_menu():
                    items = collector.collect_all_order_unit_qty()
                    if items:
                        logger.info(
                            f"[STBJ070] Direct API 발주단위 수집 성공: "
                            f"{len(items)}개"
                        )
                    else:
                        logger.warning("[STBJ070] 수집 결과 없음 - 홈 폴백")

                    # STBJ070 탭 닫기
                    close_tab_by_frame_id(
                        analyzer.driver,
                        OrderStatusCollector.FRAME_ID,
                    )
                    time.sleep(0.5)
                else:
                    logger.warning("[STBJ070] 메뉴 이동 실패 - 홈 폴백")
            except Exception as e:
                logger.warning(f"[STBJ070] 1단계 실패: {e} - 홈 폴백")

            # ★ 2단계: 홈 바코드 폴백 (1단계 실패 시)
            if not items:
                from src.infrastructure.database.connection import DBRouter

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

                logger.info(f"[홈 폴백] 수집 대상: {len(item_codes)}개 상품")
                items = collector.collect_order_unit_via_home(item_codes)

            if not items:
                logger.warning("수집된 발주단위 데이터 없음")
                return

            # 3. common.db 저장
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


def dessert_decision_wrapper(target_categories: list) -> None:
    """디저트 발주 유지/정지 판단

    Args:
        target_categories: 대상 카테고리 리스트 (["A"], ["B"], ["C","D"])
    """
    logger.info("=" * 60)
    logger.info(f"Dessert decision ({target_categories}) at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    from src.settings.constants import DESSERT_DECISION_ENABLED
    if not DESSERT_DECISION_ENABLED:
        logger.info("[DessertDecision] 비활성 (DESSERT_DECISION_ENABLED=False)")
        return

    from src.application.use_cases.dessert_decision_flow import DessertDecisionFlow

    def task(ctx):
        flow = DessertDecisionFlow(store_id=ctx.store_id)
        return flow.run(target_categories=target_categories)

    _run_task(task, f"DessertDecision({','.join(target_categories)})")


def dessert_weekly_wrapper() -> None:
    """디저트 카테고리 A 주간 판단 (매주 월요일 22:00)"""
    dessert_decision_wrapper(["A"])


def dessert_biweekly_wrapper() -> None:
    """디저트 카테고리 B 격주 판단 (매주 월요일 22:15, ISO 짝수주만 실행)"""
    iso_week = datetime.now().isocalendar()[1]
    if iso_week % 2 != 0:
        logger.info(f"[DessertDecision B] ISO 주차 {iso_week} (홀수) - 건너뜀")
        return
    dessert_decision_wrapper(["B"])


def dessert_monthly_wrapper() -> None:
    """디저트 카테고리 C/D 월간 판단 (매일 22:25, 매월 1일만 실행)"""
    if datetime.now().day != 1:
        return
    dessert_decision_wrapper(["C", "D"])


def beverage_decision_wrapper(target_categories: list) -> None:
    """음료 발주 유지/정지 판단

    Args:
        target_categories: 대상 카테고리 리스트 (["A"], ["B"], ["C","D"])
    """
    from src.settings.constants import BEVERAGE_DECISION_ENABLED
    if not BEVERAGE_DECISION_ENABLED:
        logger.info("[BeverageDecision] 비활성 (BEVERAGE_DECISION_ENABLED=False)")
        return

    from src.application.use_cases.beverage_decision_flow import BeverageDecisionFlow

    def task(ctx):
        flow = BeverageDecisionFlow(store_id=ctx.store_id)
        return flow.run(target_categories=target_categories)

    _run_task(task, f"BeverageDecision({','.join(target_categories)})")


def beverage_weekly_wrapper() -> None:
    """음료 카테고리 A 주간 판단 (매주 월요일 22:30)"""
    beverage_decision_wrapper(["A"])


def beverage_biweekly_wrapper() -> None:
    """음료 카테고리 B 격주 판단 (매주 월요일 22:45, ISO 짝수주만 실행)"""
    iso_week = datetime.now().isocalendar()[1]
    if iso_week % 2 != 0:
        logger.info(f"[BeverageDecision B] ISO 주차 {iso_week} (홀수) - 건너뜀")
        return
    beverage_decision_wrapper(["B"])


def beverage_monthly_wrapper() -> None:
    """음료 카테고리 C/D 월간 판단 (매일 23:00, 매월 1일만 실행)"""
    if datetime.now().day != 1:
        return
    beverage_decision_wrapper(["C", "D"])


def monthly_store_analysis_wrapper() -> None:
    """매월 1일 전체 매장 상권 재분석 (매일 04:00, 1일에만 실제 동작)"""
    if datetime.now().day != 1:
        return
    logger.info("[스케줄] 월간 상권 분석 시작")
    try:
        from src.infrastructure.database.connection import DBRouter
        conn = DBRouter.get_common_connection()
        try:
            rows = conn.execute(
                "SELECT store_id FROM stores WHERE lat IS NOT NULL AND lng IS NOT NULL"
            ).fetchall()
        finally:
            conn.close()

        from src.application.services.analysis_service import run_store_analysis
        for row in rows:
            try:
                run_store_analysis(row[0])
                logger.info("[스케줄] 상권분석 완료: %s", row[0])
            except Exception as e:
                logger.warning("[스케줄] 상권분석 실패: %s — %s", row[0], e)

        logger.info("[스케줄] 월간 상권 분석 완료 (%d개 매장)", len(rows))
    except Exception as e:
        logger.error("[스케줄] 월간 상권분석 오류: %s", e)


def pending_sync_wrapper() -> None:
    """발주 확정 pending 동기화 (매일 10:30)

    BGF 로그인 → 발주현황 조회 → pending 마킹 → 만료 클리어
    07:00 발주 완료 후 BGF 반영 확인용 독립 세션.
    """
    logger.info("=" * 60)
    logger.info(f"Pending sync at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    from src.application.use_cases.pending_sync_flow import PendingSyncFlow

    _run_task(
        task_fn=lambda ctx: PendingSyncFlow(store_ctx=ctx).run(),
        task_name="PendingSync"
    )


def second_delivery_adjustment_wrapper() -> None:
    """D-1: 2차 배송 보정 (매일 14:00)

    Phase 1: DB 판단 (부스트 대상 결정, Selenium 불필요)
    Phase 2: 부스트 대상 있을 때만 Selenium 세션 시작 → 추가 발주
    """
    logger.info("=" * 60)
    logger.info(f"D-1 Second Delivery Adjustment at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    try:
        from src.scheduler.daily_job import run_second_delivery_adjustment
        result = run_second_delivery_adjustment()
        logger.info(f"[D-1] 완료: {result}")
    except Exception as e:
        logger.error(f"[D-1] 실행 실패: {e}")
        import traceback
        traceback.print_exc()


def detail_fetch_wrapper() -> None:
    """상품 상세 정보 일괄 수집 (매일 01:00)

    BGF 사이트 로그인 -> CallItemDetailPopup 일괄 조회 ->
    common.db products + product_details 갱신 -> 로그아웃

    order_unit_collect_wrapper()와 동일한 패턴:
    BGF 계정 단일이므로 _run_task가 아닌 직접 실행.
    """
    logger.info("=" * 60)
    logger.info(f"Product detail batch fetch at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    try:
        from src.sales_analyzer import SalesAnalyzer
        from src.collectors.product_detail_batch_collector import (
            ProductDetailBatchCollector,
        )
        from src.settings.timing import SA_LOGIN_WAIT, SA_POPUP_CLOSE_WAIT

        # 1. BGF 사이트 로그인
        analyzer = SalesAnalyzer()
        try:
            analyzer.setup_driver()
            analyzer.connect()
            time.sleep(SA_LOGIN_WAIT)

            if not analyzer.do_login():
                logger.error("[DetailFetch] BGF 로그인 실패")
                return

            time.sleep(SA_POPUP_CLOSE_WAIT * 2)
            analyzer.close_popup()
            time.sleep(SA_POPUP_CLOSE_WAIT)

            # 2. 일괄 수집 (홈 화면에서 바로 edt_pluSearch 사용)
            collector = ProductDetailBatchCollector(
                driver=analyzer.driver,
                store_id=None,  # common.db 대상
            )

            stats = collector.collect_all()

            logger.info(f"[DetailFetch] 결과: {stats}")

        finally:
            analyzer.close()

    except Exception as e:
        logger.error(f"[DetailFetch] 실패: {e}", exc_info=True)


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
    # BUILD 정보: git commit + branch + PID + 시작 시각
    try:
        _commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(project_root), stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        _commit = "unknown"
    try:
        _branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(project_root), stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        _branch = "unknown"
    logger.info(
        f"[BUILD] commit={_commit} branch={_branch} "
        f"pid={os.getpid()} started_at={datetime.now().isoformat()}"
    )
    logger.info("[Scheduler] Press Ctrl+C to stop")
    logger.info("=" * 60)

    # DB 초기화
    init_db()

    # 0. 카카오 토큰 사전 갱신 (매일 06:30, 18:00)
    #    access_token 유효기간 6시간 → 06:30 갱신분이 ~12:30 만료
    #    18:00 추가 갱신으로 저녁 알림(21:30, 23:00 등) 401 재시도 방지
    schedule.every().day.at("06:30").do(token_refresh_wrapper)
    schedule.every().day.at("18:00").do(token_refresh_wrapper)
    logger.info("[Schedule] Kakao token refresh: 06:30, 18:00")

    # 1. 매일 지정 시간에 데이터 수집 + 자동 발주 실행
    if multi_store:
        schedule.every().day.at(schedule_time).do(job_wrapper_multi_store)
        logger.info(f"[Schedule] Multi-Store collection + auto-order: {schedule_time}")
    else:
        schedule.every().day.at(schedule_time).do(job_wrapper)
        logger.info(f"[Schedule] Daily collection + auto-order: {schedule_time}")

    # 2. 폐기 정밀 3단계 (수집+예고알림 → 판정 → 수집+확정+컨펌알림)
    #    step1: 10분 전 수집 + 예고 알림 (PRE_ALERT_COLLECTION + EXPIRY_ALERT 합류)
    #    step2: 정각 판정
    #    step3: 10분 후 수집 + 폐기 확정 + 컨펌 알림
    logger.info("[Schedule] Expiry 3-step (collect+alert → judge → confirm+alert):")
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
            f"{times['pre_collect']} 수집+예고알림 -> {times['judge']} 판정 -> "
            f"{times['post_collect']} 확정+컨펌알림"
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

    # 4.5 행사 변경 알림 (매일 08:30)
    schedule.every().day.at("08:30").do(promotion_alert_wrapper)
    logger.info("[Schedule] Promotion alert: 08:30")

    # 5. 배송 도착 후 배치 동기화
    # 2차 배송 도착(07:00) -> 07:30 배치 체크
    schedule.every().day.at("07:30").do(delivery_confirm_wrapper("2차"))
    logger.info("[Schedule] Delivery confirm (2차): 07:30")
    # 1차 배송 도착(20:00) -> 20:30 입고 수집 -> 20:40 배치 체크
    schedule.every().day.at("20:30").do(receiving_collect_wrapper("1차"))
    logger.info("[Schedule] Receiving collect (1차): 20:30")
    schedule.every().day.at("20:40").do(delivery_confirm_wrapper("1차"))
    logger.info("[Schedule] Delivery confirm (1차): 20:40")

    # 6. 일일 폐기 보고서 (매일 23:00)
    schedule.every().day.at("23:00").do(waste_report_wrapper)
    logger.info("[Schedule] Daily waste report: 23:00")

    # 7. 배치 유통기한 만료 처리 (매일 23:30)
    schedule.every().day.at("23:30").do(batch_expire_wrapper)
    logger.info("[Schedule] Batch expire check: 23:30")

    # 8. ML 모델 학습
    # 8-1. 매일 증분학습 (23:45) — 30일 윈도우, 성능 보호 게이트
    schedule.every().day.at("23:45").do(ml_train_wrapper, incremental=True)
    logger.info("[Schedule] ML incremental training: daily 23:45")
    # 8-2. 주간 전체학습 (일요일 03:00) — 90일 윈도우, 기준선 갱신
    schedule.every().sunday.at("03:00").do(ml_train_wrapper, incremental=False)
    logger.info("[Schedule] ML full training: Sunday 03:00")

    # 9. 연관 규칙 채굴 (매일 05:00)
    schedule.every().day.at("05:00").do(association_mining_wrapper)
    logger.info("[Schedule] Association rule mining: 05:00")

    # 10. 전체 품목 발주단위 수집 (매일 00:00)
    # BGF 사이트 발주현황조회 "전체" 탭에서 ORD_UNIT_QTY 수집 -> common.db 갱신
    schedule.every().day.at("00:00").do(order_unit_collect_wrapper)
    logger.info("[Schedule] Order unit qty collection: 00:00")

    # 11. 상품 상세 일괄 수집 (매일 01:00)
    # CallItemDetailPopup 일괄 조회 -> common.db products + product_details 갱신
    schedule.every().day.at("01:00").do(detail_fetch_wrapper)
    logger.info("[Schedule] Product detail batch fetch: 01:00")

    # 12. 베이지안 파라미터 최적화 (매주 일요일 23:00)
    schedule.every().sunday.at("23:00").do(bayesian_optimize_wrapper)
    logger.info("[Schedule] Bayesian parameter optimization: Sunday 23:00")

    # 16. 급여일 패턴 분석 (매주 일요일 03:30)
    # ML 전체학습(03:00) 직후, daily_sales 90일 분석 → boost/decline 구간 감지
    schedule.every().sunday.at("03:30").do(payday_analyze_wrapper)
    logger.info("[Schedule] Payday pattern analysis: Sunday 03:30")

    # 14. 디저트 발주 유지/정지 판단
    # Cat A: 매주 월요일 22:00
    schedule.every().monday.at("22:00").do(dessert_weekly_wrapper)
    logger.info("[Schedule] Dessert decision (Cat A weekly): Monday 22:00")
    # Cat B: 매주 월요일 22:15 (ISO 짝수주만 실행)
    schedule.every().monday.at("22:15").do(dessert_biweekly_wrapper)
    logger.info("[Schedule] Dessert decision (Cat B biweekly): Monday 22:15")
    # Cat C/D: 매일 22:25 (매월 1일만 실행)
    #    22:30 beverage_weekly(A)와 충돌 방지를 위해 5분 선행
    schedule.every().day.at("22:25").do(dessert_monthly_wrapper)
    logger.info("[Schedule] Dessert decision (Cat C/D monthly): daily 22:25 (1st only)")

    # 15. 음료 발주 유지/정지 판단
    # Cat A: 매주 월요일 22:30
    schedule.every().monday.at("22:30").do(beverage_weekly_wrapper)
    logger.info("[Schedule] Beverage decision (Cat A weekly): Monday 22:30")
    # Cat B: 매주 월요일 22:45 (ISO 짝수주만 실행)
    schedule.every().monday.at("22:45").do(beverage_biweekly_wrapper)
    logger.info("[Schedule] Beverage decision (Cat B biweekly): Monday 22:45")
    # Cat C/D: 매일 23:00 (매월 1일만 실행)
    schedule.every().day.at("23:00").do(beverage_monthly_wrapper)
    logger.info("[Schedule] Beverage decision (Cat C/D monthly): daily 23:00 (1st only)")

    # 13. 발주 확정 pending 동기화 (매일 10:30)
    # 07:00 발주 후 BGF 반영 확인 → order_tracking pending 마킹
    schedule.every().day.at("10:30").do(pending_sync_wrapper)
    logger.info("[Schedule] Pending sync: 10:30")

    # 14. D-1 2차 배송 보정 (매일 14:00)
    schedule.every().day.at("14:00").do(second_delivery_adjustment_wrapper)
    logger.info("[Schedule] D-1 Second delivery adjustment: daily 14:00")

    # 14. 주간 재고 검증 (매주 수요일 03:00)
    #    02:00 → 03:00 이동: 정밀 폐기 3단계(01:50→02:00→02:10)와 충돌 방지
    #    일요일 ML 전체학습(03:00)과는 요일이 달라 안전
    schedule.every().wednesday.at("03:00").do(inventory_verify_wrapper)
    logger.info("[Schedule] Weekly inventory verification: Wednesday 03:00")

    # 15. 월간 상권 분석 (매일 04:00, 매월 1일에만 실행)
    schedule.every().day.at("04:00").do(monthly_store_analysis_wrapper)
    logger.info("[Schedule] Monthly store analysis: daily 04:00 (1st only)")

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
        "--fetch-detail",
        action="store_true",
        help="Batch fetch product detail info from CallItemDetailPopup immediately"
    )
    parser.add_argument(
        "--bayesian-optimize",
        action="store_true",
        help="Run Bayesian parameter optimization immediately"
    )
    parser.add_argument(
        "--dessert-decision",
        type=str,
        nargs="*",
        default=None,
        help="Run dessert decision immediately (e.g., --dessert-decision A B or --dessert-decision for all)"
    )
    parser.add_argument(
        "--beverage-decision",
        type=str,
        nargs="*",
        default=None,
        help="Run beverage decision immediately (e.g., --beverage-decision A B or --beverage-decision for all)"
    )
    parser.add_argument(
        "--pending-sync",
        action="store_true",
        help="Run pending sync immediately"
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
        "--sync-cloud",
        action="store_true",
        help="Sync DB files to PythonAnywhere immediately"
    )
    parser.add_argument(
        "--store",
        type=str,
        default=None,
        help="Run for specific store ID only (e.g., --store 46513)"
    )
    parser.add_argument(
        "--order-date",
        type=str,
        action="append",
        default=None,
        help="Filter auto-order to specific date(s) only (YYYY-MM-DD). "
             "Can be specified multiple times. (e.g., --order-date 2026-03-01)"
    )
    parser.add_argument(
        "--backfill-hourly-detail",
        type=int,
        nargs='?',
        const=180,
        default=None,
        metavar="DAYS",
        help="Backfill hourly sales detail data (default: 180 days = 6 months)"
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Start date for backfill (YYYY-MM-DD, used with --backfill-hourly-detail)"
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="End date for backfill (YYYY-MM-DD, used with --backfill-hourly-detail)"
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
        elif args.sync_cloud:
            from scripts.sync_to_cloud import run_cloud_sync
            result = run_cloud_sync()
            if result.get("success"):
                logger.info("[CloudSync] DB 동기화 완료")
            elif result.get("skipped"):
                logger.warning(f"[CloudSync] 건너뜀: {result.get('reason')}")
            else:
                logger.error(f"[CloudSync] 실패: {result.get('error', result.get('failed', []))}")
        elif args.backfill_hourly_detail is not None:
            init_db()
            days = args.backfill_hourly_detail
            start_date = getattr(args, 'start_date', None)
            end_date = getattr(args, 'end_date', None)
            range_desc = f"{days} days"
            if start_date:
                range_desc = f"from {start_date}"
            if end_date:
                range_desc += f" to {end_date}"
            logger.info(f"[HSD Backfill] Starting hourly sales detail backfill ({range_desc})...")
            from src.application.services.hourly_detail_service import HourlyDetailService
            if args.store:
                store_ids = [args.store]
            else:
                store_ids = _runner.get_active_store_ids() if _MULTI_STORE else [_DEFAULT_STORE["store_id"]]
            from src.sales_analyzer import SalesAnalyzer
            for sid in store_ids:
                logger.info(f"[HSD Backfill] Store {sid}: {range_desc}")
                analyzer = SalesAnalyzer(store_id=sid)
                try:
                    logger.info("[HSD Backfill] BGF 사이트 로그인 중...")
                    analyzer.setup_driver()
                    analyzer.connect()
                    if not analyzer.do_login():
                        logger.error(f"[HSD Backfill] Store {sid}: 로그인 실패")
                        continue
                    driver = analyzer.driver
                    if not driver:
                        logger.error(f"[HSD Backfill] Store {sid}: 드라이버 연결 실패")
                        continue
                    # 로그인 후 팝업 닫기 + 메인 페이지 로딩 대기
                    import time as _time
                    _time.sleep(2)
                    analyzer.close_popup()
                    _time.sleep(1)
                    logger.info("[HSD Backfill] 메인 페이지 로딩 대기...")
                    for _wait_i in range(20):
                        try:
                            _ready = driver.execute_script("""
                                try {
                                    var app = nexacro.getApplication();
                                    var top = app.mainframe.HFrameSet00.VFrameSet00.TopFrame;
                                    return top && top.form ? true : false;
                                } catch(e) { return false; }
                            """)
                            if _ready:
                                logger.info("[HSD Backfill] 메인 페이지 로딩 완료")
                                break
                        except Exception:
                            pass
                        _time.sleep(0.5)
                    else:
                        logger.warning("[HSD Backfill] 메인 페이지 로딩 타임아웃 — 계속 진행")
                    service = HourlyDetailService(store_id=sid, driver=driver)
                    stats = service.backfill(
                        days=days,
                        driver=driver,
                        start_date=start_date,
                        end_date=end_date,
                    )
                    logger.info(f"[HSD Backfill] Store {sid} done: {stats}")
                except KeyboardInterrupt:
                    logger.info("[HSD Backfill] 사용자 중단")
                    break
                except Exception as e:
                    logger.error(f"[HSD Backfill] Store {sid} 실패: {e}")
                finally:
                    try:
                        analyzer.close()
                    except Exception:
                        pass
        elif args.collect_order_unit:
            init_db()
            order_unit_collect_wrapper()
        elif args.fetch_detail:
            init_db()
            detail_fetch_wrapper()
        elif args.dessert_decision is not None:
            init_db()
            cats = args.dessert_decision if args.dessert_decision else ["A", "B", "C", "D"]
            dessert_decision_wrapper(cats)
        elif args.beverage_decision is not None:
            init_db()
            cats = args.beverage_decision if args.beverage_decision else ["A", "B", "C", "D"]
            beverage_decision_wrapper(cats)
        elif args.pending_sync:
            init_db()
            pending_sync_wrapper()
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
                if args.order_date:
                    logger.info(f"Order date filter: {args.order_date}")
                init_db()
                from src.scheduler.daily_job import DailyCollectionJob
                job = DailyCollectionJob(store_id=args.store)
                result = job.run_optimized(
                    run_auto_order=True, use_improved_predictor=True,
                    target_dates=args.order_date,
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
                    _try_cloud_sync()
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
