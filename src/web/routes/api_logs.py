"""로그 분석 REST API"""

from datetime import datetime

from flask import Blueprint, jsonify, request

from src.analysis.log_parser import (
    LogParser,
    format_session_summary,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

logs_bp = Blueprint("logs", __name__)

_parser = LogParser()


@logs_bp.route("/stats", methods=["GET"])
def get_log_stats():
    """로그 파일 통계

    GET /api/logs/stats
    GET /api/logs/stats?file=prediction
    """
    log_file = request.args.get("file")
    if log_file:
        stats = _parser.get_file_stats(log_file)
        return jsonify(stats)
    return jsonify(_parser.get_all_stats())


@logs_bp.route("/sessions", methods=["GET"])
def get_sessions():
    """특정 날짜의 세션 목록

    GET /api/logs/sessions?date=2026-02-21
    """
    date = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    log_file = request.args.get("file", "main")

    sessions = _parser.list_sessions(date, log_file)
    result = []
    for s in sessions:
        result.append({
            "session_id": s.session_id,
            "start_time": s.start_time.isoformat() if s.start_time else None,
            "end_time": s.end_time.isoformat() if s.end_time else None,
            "duration_seconds": s.duration_seconds,
            "store_id": s.store_id,
            "status": s.overall_status,
            "errors": s.total_errors,
            "warnings": s.total_warnings,
            "log_count": s.log_count,
            "phase_count": len(s.phases),
        })
    return jsonify({"date": date, "sessions": result})


@logs_bp.route("/session/<date>", methods=["GET"])
def get_session_detail(date):
    """특정 세션 상세

    GET /api/logs/session/2026-02-21?time=07:00
    """
    time_hint = request.args.get("time", "07:00")
    log_file = request.args.get("file", "main")

    session = _parser.extract_session(date, time_hint, log_file)
    phases = []
    for p in session.phases:
        phases.append({
            "phase_id": p.phase_id,
            "phase_name": p.phase_name,
            "start_time": p.start_time.isoformat() if p.start_time else None,
            "end_time": p.end_time.isoformat() if p.end_time else None,
            "duration_seconds": p.duration_seconds,
            "status": p.status,
            "error_count": p.error_count,
            "warning_count": p.warning_count,
            "log_count": p.log_count,
            "key_messages": p.key_messages,
        })

    return jsonify({
        "session_id": session.session_id,
        "start_time": session.start_time.isoformat() if session.start_time else None,
        "end_time": session.end_time.isoformat() if session.end_time else None,
        "duration_seconds": session.duration_seconds,
        "store_id": session.store_id,
        "status": session.overall_status,
        "errors": session.total_errors,
        "warnings": session.total_warnings,
        "log_count": session.log_count,
        "phases": phases,
        "summary_text": format_session_summary(session),
    })


@logs_bp.route("/errors", methods=["GET"])
def get_errors():
    """에러/경고 필터

    GET /api/logs/errors?hours=24&level=ERROR,WARNING&file=main
    """
    hours = request.args.get("hours", 24, type=int)
    level = request.args.get("level", "ERROR")
    log_file = request.args.get("file", "main")
    limit = request.args.get("limit", 100, type=int)

    entries = _parser.filter_by_level(
        level=level,
        log_file=log_file,
        last_hours=hours,
        max_results=limit,
    )

    result = []
    for e in entries:
        result.append({
            "timestamp": e.timestamp.isoformat(),
            "level": e.level,
            "module": e.module,
            "message": e.message,
            "line_number": e.line_number,
        })

    return jsonify({
        "hours": hours,
        "level": level,
        "file": log_file,
        "count": len(result),
        "entries": result,
    })


@logs_bp.route("/search", methods=["GET"])
def search_logs():
    """키워드 검색

    GET /api/logs/search?q=8800279678694&hours=168&file=main
    """
    keyword = request.args.get("q", "")
    if not keyword:
        return jsonify({"error": "q parameter required"}), 400

    hours = request.args.get("hours", 24, type=int)
    log_file = request.args.get("file", "main")
    limit = request.args.get("limit", 50, type=int)
    regex = request.args.get("regex", "false").lower() == "true"

    entries = _parser.search(
        keyword=keyword,
        log_file=log_file,
        last_hours=hours,
        regex=regex,
        max_results=limit,
    )

    result = []
    for e in entries:
        result.append({
            "timestamp": e.timestamp.isoformat(),
            "level": e.level,
            "module": e.module,
            "message": e.message,
            "line_number": e.line_number,
        })

    return jsonify({
        "keyword": keyword,
        "hours": hours,
        "file": log_file,
        "count": len(result),
        "entries": result,
    })
