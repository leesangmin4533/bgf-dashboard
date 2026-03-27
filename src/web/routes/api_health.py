"""시스템 헬스 체크 API.

GET /api/health          -- 간단 상태 (인증 불필요, 외부 모니터링용)
GET /api/health/detail   -- DB, 스케줄러, 디스크, 에러 상세 (인증 필요)
"""

import os
import time
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify

from src.settings.constants import DB_SCHEMA_VERSION, DEFAULT_STORE_ID
from src.utils.logger import get_logger, LOG_DIR

logger = get_logger(__name__)

health_bp = Blueprint("health", __name__)

# 앱 시작 시각 (uptime 계산용)
_APP_START_TIME = time.time()

# 프로젝트 루트
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def _check_database() -> dict:
    """DB 연결 + 크기 확인."""
    result = {"status": "ok"}
    try:
        from src.infrastructure.database.connection import DBRouter

        # common.db 체크
        common_path = PROJECT_ROOT / "data" / "common.db"
        if common_path.exists():
            result["common_db_size_mb"] = round(
                common_path.stat().st_size / (1024 * 1024), 1
            )

        # common.db 연결 테스트
        conn = DBRouter.get_common_connection()
        cursor = conn.execute("SELECT 1")
        cursor.fetchone()
        conn.close()

        # store DB 체크
        store_path = PROJECT_ROOT / "data" / "stores" / f"{DEFAULT_STORE_ID}.db"
        if store_path.exists():
            result["store_db_size_mb"] = round(
                store_path.stat().st_size / (1024 * 1024), 1
            )

        result["schema_version"] = DB_SCHEMA_VERSION
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)[:200]
    return result


def _check_scheduler() -> dict:
    """스케줄러 상태 확인 (lock 파일 기반)."""
    result = {"status": "stopped"}
    lock_file = PROJECT_ROOT / "data" / "scheduler.lock"

    if not lock_file.exists():
        return result

    try:
        pid_str = lock_file.read_text().strip()
        pid = int(pid_str)

        # Windows: PID 존재 확인
        if os.name == "nt":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                result["status"] = "running"
                result["pid"] = pid
            else:
                result["status"] = "stale_lock"
        else:
            os.kill(pid, 0)  # POSIX: 프로세스 존재 확인
            result["status"] = "running"
            result["pid"] = pid

        result["lock_modified"] = datetime.fromtimestamp(
            lock_file.stat().st_mtime
        ).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, ProcessLookupError, OSError):
        result["status"] = "stale_lock"

    return result


def _check_disk() -> dict:
    """디스크 사용량 (로그 + 데이터)."""
    result = {}

    # 로그 디렉토리
    if LOG_DIR.exists():
        log_size = sum(f.stat().st_size for f in LOG_DIR.iterdir() if f.is_file())
        result["log_dir_size_mb"] = round(log_size / (1024 * 1024), 1)

    # 데이터 디렉토리
    data_dir = PROJECT_ROOT / "data"
    if data_dir.exists():
        data_size = sum(
            f.stat().st_size
            for f in data_dir.rglob("*")
            if f.is_file()
        )
        result["data_dir_size_mb"] = round(data_size / (1024 * 1024), 1)

    return result


def _check_recent_errors() -> dict:
    """최근 에러 로그 분석."""
    result = {"last_24h": 0, "last_error": None}
    error_log = LOG_DIR / "error.log"
    if not error_log.exists():
        return result

    try:
        now = datetime.now()
        count = 0
        last_line = None

        with open(error_log, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "| ERROR" in line:
                    # 타임스탬프 파싱 시도
                    try:
                        ts_str = line[:19]
                        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                        if (now - ts).total_seconds() < 86400:
                            count += 1
                            last_line = line.strip()
                    except (ValueError, IndexError):
                        pass

        result["last_24h"] = count
        if last_line:
            result["last_error"] = last_line[:200]
    except Exception:
        pass

    return result


def _check_cloud_sync() -> dict:
    """클라우드 동기화 상태."""
    result = {"status": "unknown"}
    config_path = PROJECT_ROOT / "config" / "pythonanywhere.json"
    if not config_path.exists():
        result["status"] = "not_configured"
        return result

    # sync 로그에서 마지막 성공 찾기
    main_log = LOG_DIR / "bgf_auto.log"
    if main_log.exists():
        try:
            with open(main_log, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            for line in reversed(lines[-500:]):
                if "[CloudSync] 완료:" in line:
                    result["status"] = "ok"
                    result["last_sync"] = line[:19]
                    break
        except Exception:
            pass

    return result


def _determine_status(db_check, scheduler_check, error_check) -> str:
    """전체 상태 결정."""
    if db_check.get("status") == "error":
        return "unhealthy"
    if scheduler_check.get("status") != "running" or error_check.get("last_24h", 0) > 10:
        return "degraded"
    return "healthy"


# ========================================
# 엔드포인트
# ========================================

@health_bp.route("", methods=["GET"])
@health_bp.route("/", methods=["GET"])
def health_check():
    """간단 헬스 체크 (인증 불필요).

    외부 모니터링 서비스(UptimeRobot 등)에서 사용.
    """
    db = _check_database()
    sched = _check_scheduler()
    errors = _check_recent_errors()
    status = _determine_status(db, sched, errors)

    return jsonify({
        "status": status,
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "version": str(DB_SCHEMA_VERSION),
        "uptime_seconds": int(time.time() - _APP_START_TIME),
    })


@health_bp.route("/detail", methods=["GET"])
def health_detail():
    """상세 헬스 체크 (인증 필요 -- before_request에서 처리)."""
    db = _check_database()
    sched = _check_scheduler()
    disk = _check_disk()
    errors = _check_recent_errors()
    sync = _check_cloud_sync()
    status = _determine_status(db, sched, errors)

    return jsonify({
        "status": status,
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "checks": {
            "database": db,
            "scheduler": sched,
            "disk": disk,
            "recent_errors": errors,
            "cloud_sync": sync,
        },
    })
