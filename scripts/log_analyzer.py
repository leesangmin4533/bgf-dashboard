"""
BGF 로그 분석 CLI 도구

Claude(AI 에이전트)가 Bash 도구로 실행하여 대용량 로그를 분석한다.
전체 로그 파일을 읽지 않고 필요한 부분만 스트리밍 추출한다.

Usage:
    python scripts/log_analyzer.py --date 2026-02-21 --summary
    python scripts/log_analyzer.py --date 2026-02-21 --timeline
    python scripts/log_analyzer.py --date 2026-02-21 --phase 2
    python scripts/log_analyzer.py --errors --last 24h
    python scripts/log_analyzer.py --search "8800279678694" --last 7d
    python scripts/log_analyzer.py --stats
    python scripts/log_analyzer.py --tail 50
"""

import sys
import io
from pathlib import Path

# Windows CP949 -> UTF-8
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

# 프로젝트 루트 path 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import argparse
from datetime import datetime

from src.analysis.log_parser import (
    LogParser,
    format_entries,
    format_session_summary,
    format_phase_timeline,
    format_file_stats,
)


def parse_duration(s: str):
    """'24h' -> (24, 'hours'), '7d' -> (7, 'days')"""
    s = s.strip().lower()
    if s.endswith("h"):
        return int(s[:-1]), "hours"
    if s.endswith("d"):
        return int(s[:-1]), "days"
    # 숫자만이면 시간으로 간주
    return int(s), "hours"


def cmd_summary(parser: LogParser, args):
    """세션 요약"""
    sessions = parser.list_sessions(args.date, args.file)
    if not sessions:
        print(f"=== No sessions found for {args.date} in {args.file} ===")
        return

    for session in sessions:
        print(format_session_summary(session))
        print()


def cmd_timeline(parser: LogParser, args):
    """Phase별 타임라인"""
    phases = parser.get_phase_summary(args.date, args.time, args.file)
    if not phases:
        print(f"=== No phases found for {args.date} ===")
        return

    print(f"=== Phase Timeline: {args.date} ===")
    print(format_phase_timeline(phases))


def cmd_phase(parser: LogParser, args):
    """특정 Phase 로그"""
    entries = parser.get_phase_logs(args.date, args.phase, args.time, args.file)
    if not entries:
        print(f"=== No logs found for Phase {args.phase} on {args.date} ===")
        return

    # Phase 내 에러만 보기
    if args.errors:
        levels = {"ERROR"}
        if args.warnings:
            levels.add("WARNING")
        entries = [e for e in entries if e.level in levels]

    print(f"=== Phase {args.phase} logs ({args.date}): {len(entries)} lines ===")
    print(format_entries(entries, max_lines=args.max_lines, show_line_numbers=True))


def cmd_errors(parser: LogParser, args):
    """에러/경고 필터"""
    level = "ERROR"
    if args.warnings:
        level = "ERROR,WARNING"

    last_hours = None
    last_days = None
    if args.last:
        val, unit = parse_duration(args.last)
        if unit == "hours":
            last_hours = val
        else:
            last_days = val

    entries = parser.filter_by_level(
        level=level,
        log_file=args.file,
        last_hours=last_hours,
        last_days=last_days,
        start_date=args.date if not args.last else None,
        end_date=args.date if not args.last else None,
        max_results=args.max_lines,
    )

    period = args.last or args.date or "all"
    print(f"=== {level} (period: {period}, file: {args.file}) ===")
    print(f"Found: {len(entries)}")
    if entries:
        print()
        print(format_entries(entries, max_lines=args.max_lines, show_line_numbers=True))


def cmd_search(parser: LogParser, args):
    """키워드 검색"""
    last_hours = None
    last_days = None
    if args.last:
        val, unit = parse_duration(args.last)
        if unit == "hours":
            last_hours = val
        else:
            last_days = val

    entries = parser.search(
        keyword=args.search,
        log_file=args.file,
        last_hours=last_hours,
        last_days=last_days,
        start_date=args.date if not args.last else None,
        end_date=args.date if not args.last else None,
        regex=args.regex,
        max_results=args.max_lines,
    )

    period = args.last or args.date or "all"
    print(f"=== Search: \"{args.search}\" (period: {period}, file: {args.file}) ===")
    print(f"Found: {len(entries)}")
    if entries:
        print()
        print(format_entries(entries, max_lines=args.max_lines, show_line_numbers=True))


def cmd_stats(parser: LogParser, args):
    """로그 파일 통계"""
    stats = parser.get_all_stats()
    print(format_file_stats(stats))


def cmd_list_sessions(parser: LogParser, args):
    """세션 목록"""
    sessions = parser.list_sessions(args.date, args.file)
    if not sessions:
        print(f"=== No sessions found for {args.date} ===")
        return

    print(f"=== Sessions on {args.date} ({args.file}): {len(sessions)} ===")
    for s in sessions:
        start = s.start_time.strftime("%H:%M:%S") if s.start_time else "?"
        dur = f"{s.duration_seconds:.0f}s"
        store = f" store={s.store_id}" if s.store_id else ""
        phases = len(s.phases)
        print(
            f"  {start}  {dur:>8s}  "
            f"phases={phases}  errors={s.total_errors}  "
            f"warnings={s.total_warnings}  "
            f"{s.overall_status}{store}"
        )


def cmd_tail(parser: LogParser, args):
    """최근 N줄"""
    entries = parser.tail(args.file, args.tail)
    if not entries:
        print(f"=== No entries in {args.file} ===")
        return

    print(f"=== Last {len(entries)} lines ({args.file}) ===")
    print(format_entries(entries, max_lines=args.tail, show_line_numbers=False))


def main():
    p = argparse.ArgumentParser(
        prog="log_analyzer",
        description="BGF 로그 분석 도구 (Claude AI 에이전트용)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  %(prog)s --date 2026-02-21 --summary          Session summary
  %(prog)s --date 2026-02-21 --timeline          Phase timeline
  %(prog)s --date 2026-02-21 --phase 2           Phase 2 logs
  %(prog)s --errors --last 24h                   Errors in last 24h
  %(prog)s --search "8800279678694" --last 7d     Search by item code
  %(prog)s --stats                               Log file statistics
  %(prog)s --tail 50                             Last 50 lines
        """,
    )

    # 날짜/시간 필터
    p.add_argument("--date", "-d", type=str, default=None,
                   help="Target date (YYYY-MM-DD, default: today)")
    p.add_argument("--time", "-t", type=str, default="07:00",
                   help="Session time hint (HH:MM, default: 07:00)")
    p.add_argument("--last", type=str, default=None,
                   help="Recent period (24h, 7d, etc.)")

    # 명령
    p.add_argument("--summary", "-s", action="store_true",
                   help="Session summary with phase timeline")
    p.add_argument("--timeline", action="store_true",
                   help="Phase timeline only")
    p.add_argument("--phase", "-p", type=str, default=None,
                   help="Show logs for specific phase (1, 1.1, 2, etc.)")
    p.add_argument("--errors", "-e", action="store_true",
                   help="Show ERROR level only")
    p.add_argument("--warnings", "-w", action="store_true",
                   help="Include WARNING (with --errors)")
    p.add_argument("--search", "-q", type=str, default=None,
                   help="Keyword search")
    p.add_argument("--regex", action="store_true",
                   help="Treat search keyword as regex")
    p.add_argument("--stats", action="store_true",
                   help="Log file statistics")
    p.add_argument("--list-sessions", action="store_true",
                   help="List all sessions for date")
    p.add_argument("--tail", type=int, default=None,
                   help="Show last N lines")

    # 로그 파일 선택
    p.add_argument("--file", "-f", type=str, default="main",
                   choices=["main", "prediction", "order", "collector", "error", "all"],
                   help="Log file (default: main)")

    # 출력 제어
    p.add_argument("--max-lines", type=int, default=200,
                   help="Max output lines (default: 200)")

    args = p.parse_args()

    # 날짜 기본값
    if args.date is None and not args.stats and args.last is None and args.tail is None:
        args.date = datetime.now().strftime("%Y-%m-%d")

    parser = LogParser()

    # 명령 디스패치
    if args.stats:
        cmd_stats(parser, args)
    elif args.list_sessions:
        cmd_list_sessions(parser, args)
    elif args.tail:
        cmd_tail(parser, args)
    elif args.search:
        cmd_search(parser, args)
    elif args.phase:
        cmd_phase(parser, args)
    elif args.timeline:
        cmd_timeline(parser, args)
    elif args.errors:
        cmd_errors(parser, args)
    elif args.summary or args.date:
        cmd_summary(parser, args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
