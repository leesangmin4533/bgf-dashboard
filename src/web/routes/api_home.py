"""홈 대시보드 REST API

DashboardService를 통해 데이터를 제공합니다.
"""
import os
import sys
import subprocess
import time
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, current_app

from src.application.services.dashboard_service import DashboardService
from src.utils.logger import get_logger
from src.web.routes.api_auth import admin_required

logger = get_logger(__name__)

home_bp = Blueprint("home", __name__)

# API 응답 캐시 (store_id별 TTL 기반)
_status_cache = {}  # {cache_key: {"data": ..., "expires": ...}}
_STATUS_CACHE_TTL = 5  # 초


def _is_pid_running(pid):
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


@home_bp.route("/status", methods=["GET"])
def status():
    """홈 대시보드 통합 데이터 (5초 캐시)"""
    now = time.time()
    store_id = request.args.get('store_id')
    cache_key = f"store:{store_id or 'all'}"

    cached = _status_cache.get(cache_key)
    if cached and cached["data"] is not None and now < cached["expires"]:
        return jsonify(cached["data"])

    project_root = current_app.config["PROJECT_ROOT"]
    if store_id:
        svc = DashboardService(store_id=store_id)
    else:
        db_path = current_app.config.get("DB_PATH")
        svc = DashboardService(db_path=db_path)

    pred_cache = current_app.config.get("LAST_PREDICTIONS", {})
    predictions = pred_cache.get(store_id) if store_id else None

    data = {
        "scheduler": _get_scheduler_status(project_root),
        "last_order": svc.get_last_order(),
        "today_summary": svc.get_today_summary(cached_predictions=predictions),
        "expiry_risk": svc.get_expiry_risk(),
        "pipeline": svc.get_pipeline_status(
            script_task=current_app.config.get("SCRIPT_TASK")
        ),
        "recent_events": svc.get_recent_events(),
        "fail_reasons": svc.get_fail_reasons(),
        "order_trend_7d": svc.get_order_trend_7d(),
        "sales_trend_7d": svc.get_sales_trend_7d(),
        "waste_trend_7d": svc.get_waste_trend_7d(),
    }

    _status_cache[cache_key] = {"data": data, "expires": now + _STATUS_CACHE_TTL}

    return jsonify(data)


@home_bp.route("/store-comparison", methods=["GET"])
def store_comparison():
    """매장 간 비교 요약 API"""
    svc = DashboardService()
    data = svc.get_store_comparison()
    return jsonify({"stores": data, "count": len(data)})


@home_bp.route("/scheduler/start", methods=["POST"])
@admin_required
def scheduler_start():
    """스케줄러 백그라운드 시작 (admin only)"""
    project_root = current_app.config["PROJECT_ROOT"]
    sched_status = _get_scheduler_status(project_root)
    if sched_status["running"]:
        return jsonify({"error": "이미 동작중입니다", "pid": sched_status["pid"]}), 409

    scheduler_script = Path(project_root) / "run_scheduler.py"
    if not scheduler_script.exists():
        return jsonify({"error": "run_scheduler.py를 찾을 수 없습니다"}), 404

    try:
        proc = subprocess.Popen(
            [sys.executable, str(scheduler_script)],
            cwd=str(project_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return jsonify({"ok": True, "pid": proc.pid})
    except Exception as e:
        logger.error(f"스케줄러 시작 실패: {e}")
        return jsonify({"error": "스케줄러 시작에 실패했습니다"}), 500


@home_bp.route("/scheduler/stop", methods=["POST"])
@admin_required
def scheduler_stop():
    """스케줄러 중지 (admin only)"""
    project_root = current_app.config["PROJECT_ROOT"]
    sched_status = _get_scheduler_status(project_root)
    if not sched_status["running"]:
        return jsonify({"error": "스케줄러가 동작중이 아닙니다"}), 409

    pid = sched_status["pid"]
    try:
        if sys.platform == "win32":
            import ctypes
            PROCESS_TERMINATE = 0x0001
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if handle:
                kernel32.TerminateProcess(handle, 0)
                kernel32.CloseHandle(handle)
        else:
            import signal
            os.kill(pid, signal.SIGTERM)

        lock_file = Path(project_root) / "data" / "scheduler.lock"
        if lock_file.exists():
            try:
                lock_file.unlink()
            except OSError:
                pass

        return jsonify({"ok": True, "pid": pid})
    except Exception as e:
        logger.error(f"스케줄러 중지 실패: {e}")
        return jsonify({"error": "스케줄러 중지에 실패했습니다"}), 500


# 스케줄러 작업 목록 (run_scheduler.py 기반 정적 정의)
_SCHEDULER_JOBS = [
    {"time": "06:30", "freq": "매일", "name": "토큰 갱신", "desc": "카카오 API 토큰 사전 갱신"},
    {"time": "07:00", "freq": "매일", "name": "데이터 수집 + 자동 발주", "desc": "판매 데이터 수집 및 발주 실행"},
    {"time": "07:30", "freq": "매일", "name": "배송 확인 (2차)", "desc": "2차 배송 도착 후 배치 동기화"},
    {"time": "09:00", "freq": "매일", "name": "폐기 전 수집", "desc": "10:00 폐기 알림용 데이터 수집"},
    {"time": "09:30", "freq": "매일", "name": "폐기 알림 (10시)", "desc": "10:00 폐기 30분 전 알림 발송"},
    {"time": "11:00", "freq": "매일", "name": "상품 상세 수집", "desc": "유통기한 미등록 상품 일괄 수집"},
    {"time": "13:00", "freq": "매일", "name": "폐기 전 수집", "desc": "14:00 폐기 알림용 데이터 수집"},
    {"time": "13:30", "freq": "매일", "name": "폐기 알림 (14시)", "desc": "14:00 폐기 30분 전 알림 발송"},
    {"time": "20:30", "freq": "매일", "name": "배송 확인 (1차)", "desc": "1차 배송 도착 후 배치 동기화"},
    {"time": "21:00", "freq": "매일", "name": "폐기 전 수집", "desc": "22:00 폐기 알림용 데이터 수집"},
    {"time": "21:30", "freq": "매일", "name": "폐기 알림 (22시)", "desc": "22:00 폐기 30분 전 알림 발송"},
    {"time": "22:00", "freq": "매일", "name": "폐기 전 수집", "desc": "00:00 만료(빵) 알림용 수집"},
    {"time": "23:00", "freq": "매일", "name": "폐기 보고서 + 만료 알림", "desc": "일일 폐기 보고서(엑셀) + 자정 만료 알림"},
    {"time": "23:30", "freq": "매일", "name": "배치 만료 처리", "desc": "유통기한 만료 배치 폐기 처리"},
    {"time": "01:00", "freq": "매일", "name": "폐기 전 수집", "desc": "02:00 폐기 알림용 데이터 수집"},
    {"time": "01:30", "freq": "매일", "name": "폐기 알림 (02시)", "desc": "02:00 폐기 30분 전 알림 발송"},
    {"time": "08:00", "freq": "매주 월", "name": "주간 리포트", "desc": "카테고리 트렌드 + 예측 정확도 분석"},
]


@home_bp.route("/scheduler/jobs", methods=["GET"])
def scheduler_jobs():
    """스케줄러 등록 작업 목록"""
    project_root = current_app.config["PROJECT_ROOT"]
    sched_status = _get_scheduler_status(project_root)

    now = datetime.now()
    now_hm = now.strftime("%H:%M")
    today_dow = now.weekday()  # 0=월요일

    jobs = []
    for j in _SCHEDULER_JOBS:
        # 다음 실행 여부 판별
        is_weekly = "주" in j["freq"]
        if is_weekly:
            done_today = today_dow != 0 or now_hm > j["time"]
        else:
            done_today = now_hm > j["time"]

        jobs.append({
            "time": j["time"],
            "freq": j["freq"],
            "name": j["name"],
            "desc": j["desc"],
            "done_today": done_today,
        })

    return jsonify({
        "running": sched_status["running"],
        "pid": sched_status["pid"],
        "total_jobs": len(jobs),
        "jobs": jobs,
    })


def _get_scheduler_status(project_root):
    """스케줄러 동작 상태 확인 (stale lock 자동 정리)"""
    lock_file = Path(project_root) / "data" / "scheduler.lock"
    running = False
    pid = None

    if lock_file.exists():
        try:
            pid = int(lock_file.read_text().strip())
            running = _is_pid_running(pid)
        except (ValueError, FileNotFoundError):
            running = False

        if not running:
            try:
                lock_file.unlink()
            except OSError:
                pass
            pid = None

    return {
        "running": running,
        "pid": pid,
    }
