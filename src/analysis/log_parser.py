"""
BGF 로그 분석 코어 모듈

대용량 로그 파일(54~113MB)을 스트리밍 파싱하여
세션, Phase, 에러를 추출한다.
메모리에 전체 파일을 로드하지 않고 줄 단위로 처리한다.

Usage:
    from src.analysis.log_parser import LogParser

    parser = LogParser()
    session = parser.extract_session("2026-02-21")
    errors = parser.filter_by_level("ERROR", last_hours=24)
"""

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Generator, List, Optional, Dict, Any, Tuple


# ──────────────────────────────────────────────────────────────
# 상수 (logger.py 와 동일한 경로 구조)
# ──────────────────────────────────────────────────────────────

LOG_DIR = Path(__file__).parent.parent.parent / "logs"

LOG_FILES: Dict[str, str] = {
    "main": "bgf_auto.log",
    "prediction": "prediction.log",
    "order": "order.log",
    "collector": "collector.log",
    "error": "error.log",
}

# 로그 라인 정규식
# Format: "2026-02-21 07:00:04 | INFO     | src.scheduler.daily_job | message"
LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"  # group(1): timestamp
    r" \| "
    r"(\w+)\s*"                                    # group(2): level
    r" \| "
    r"([\w.]+)"                                    # group(3): module
    r" \| "
    r"(.*)$"                                       # group(4): message
)

# Phase 감지 정규식
PHASE_RE = re.compile(r"\[Phase ([\d.]+)\]\s*(.*)")

# 세션 시작 마커
SESSION_START_MARKERS = [
    "Optimized flow started",
    "Multi-Store Daily job triggered",
    "Daily job triggered",
]

# Phase 이름 매핑 (daily_job.py 기준)
KNOWN_PHASES: Dict[str, str] = {
    "1": "Data Collection",
    "1.1": "Receiving Collection",
    "1.15": "Waste Slip Collection",
    "1.16": "Waste Slip -> Sales Sync",
    "1.2": "Exclusion Items",
    "1.3": "New Product",
    "1.5": "Eval Calibration",
    "1.55": "Waste Cause Analysis",
    "1.56": "Food Waste Rate Calibration",
    "1.6": "Prediction Actual Update",
    "1.65": "Stale Stock Cleanup",
    "1.7": "Prediction Logging",
    "2": "Auto Order",
    "3": "Fail Reason Collection",
}

DATE_FMT = "%Y-%m-%d %H:%M:%S"


# ──────────────────────────────────────────────────────────────
# 데이터 클래스
# ──────────────────────────────────────────────────────────────

@dataclass
class LogEntry:
    """파싱된 로그 라인 1건"""
    timestamp: datetime
    level: str
    module: str
    message: str
    raw_line: str
    line_number: int
    phase: Optional[str] = None
    phase_name: Optional[str] = None


@dataclass
class PhaseInfo:
    """Phase 1건의 요약"""
    phase_id: str
    phase_name: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_seconds: float = 0.0
    status: str = "unknown"
    error_count: int = 0
    warning_count: int = 0
    log_count: int = 0
    key_messages: List[str] = field(default_factory=list)


@dataclass
class SessionInfo:
    """스케줄러 실행 1회분"""
    session_id: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_seconds: float = 0.0
    store_id: Optional[str] = None
    phases: List[PhaseInfo] = field(default_factory=list)
    total_errors: int = 0
    total_warnings: int = 0
    overall_status: str = "unknown"
    log_count: int = 0


# ──────────────────────────────────────────────────────────────
# 파싱 함수
# ──────────────────────────────────────────────────────────────

def parse_line(raw_line: str, line_number: int = 0) -> Optional[LogEntry]:
    """로그 라인 1줄 파싱 -> LogEntry 또는 None"""
    raw_line = raw_line.rstrip("\n\r")
    m = LOG_LINE_RE.match(raw_line)
    if not m:
        return None

    try:
        ts = datetime.strptime(m.group(1), DATE_FMT)
    except ValueError:
        return None

    level = m.group(2).strip().upper()
    module = m.group(3).strip()
    message = m.group(4)

    # Phase 감지
    phase = None
    phase_name = None
    pm = PHASE_RE.search(message)
    if pm:
        phase = pm.group(1)
        phase_name = pm.group(2).strip() or KNOWN_PHASES.get(phase, "")

    return LogEntry(
        timestamp=ts,
        level=level,
        module=module,
        message=message,
        raw_line=raw_line,
        line_number=line_number,
        phase=phase,
        phase_name=phase_name,
    )


def _resolve_log_path(log_file: str, log_dir: Path) -> Path:
    """로그 파일 키 -> 절대 경로"""
    filename = LOG_FILES.get(log_file, log_file)
    return log_dir / filename


def stream_log_file(
    log_file: str = "main",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    log_dir: Optional[Path] = None,
) -> Generator[LogEntry, None, None]:
    """로그 파일을 스트리밍 파싱 (날짜 범위 필터링)

    대용량 파일(100MB+)도 메모리 문제 없이 처리.
    날짜 범위 밖의 라인은 빠르게 스킵.
    """
    log_dir = log_dir or LOG_DIR
    path = _resolve_log_path(log_file, log_dir)

    if not path.exists():
        return

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_no, raw_line in enumerate(f, 1):
            # 빠른 날짜 prefix 체크 (정규식보다 훨씬 빠름)
            if len(raw_line) < 19:
                continue

            date_prefix = raw_line[:10]
            if start_date and date_prefix < start_date:
                continue
            if end_date and date_prefix > end_date:
                # 로그는 시간순이므로 날짜가 초과하면 중단
                break

            entry = parse_line(raw_line, line_no)
            if entry:
                yield entry


# ──────────────────────────────────────────────────────────────
# LogParser 메인 클래스
# ──────────────────────────────────────────────────────────────

class LogParser:
    """로그 분석 메인 클래스"""

    def __init__(self, log_dir: Optional[Path] = None):
        self.log_dir = log_dir or LOG_DIR

    # ── 세션 ──

    def extract_session(
        self,
        date: str,
        time_hint: str = "07:00",
        log_file: str = "main",
    ) -> SessionInfo:
        """특정 날짜/시간의 세션(실행 1회분) 추출"""
        sessions = self.list_sessions(date, log_file)
        if not sessions:
            return SessionInfo(session_id=f"{date}_{time_hint}", overall_status="not_found")

        # time_hint에 가장 가까운 세션 선택 (Phase가 있는 세션 우선)
        try:
            hint_parts = time_hint.split(":")
            hint_hour = int(hint_parts[0])
            hint_min = int(hint_parts[1]) if len(hint_parts) > 1 else 0
            hint_dt = datetime.strptime(f"{date} {hint_hour:02d}:{hint_min:02d}:00", DATE_FMT)
        except (ValueError, IndexError):
            hint_dt = datetime.strptime(f"{date} 07:00:00", DATE_FMT)

        def session_score(s):
            time_diff = abs((s.start_time - hint_dt).total_seconds()) if s.start_time else 999999
            # 같은 시간대(1시간 이내) 세션 중에서는 Phase가 많은 것 우선
            # 시간대가 다르면(>1h) 시간 차이가 우선
            if time_diff <= 3600:
                phase_penalty = max(0, 5 - len(s.phases)) * 1000
            else:
                phase_penalty = 0
            return time_diff + phase_penalty

        best = min(sessions, key=session_score)
        return best

    def list_sessions(
        self,
        date: str,
        log_file: str = "main",
    ) -> List[SessionInfo]:
        """특정 날짜의 모든 세션 목록"""
        # 1단계: 세션 경계 감지
        all_entries: List[LogEntry] = []
        session_starts: List[int] = []  # all_entries 내 인덱스

        for entry in stream_log_file(log_file, start_date=date, end_date=date, log_dir=self.log_dir):
            idx = len(all_entries)
            all_entries.append(entry)

            for marker in SESSION_START_MARKERS:
                if marker in entry.message:
                    session_starts.append(idx)
                    break

        if not all_entries:
            return []

        # 세션 시작이 감지되지 않으면 날짜 전체를 하나의 세션으로
        if not session_starts:
            session = self._build_session(all_entries, date, "00:00")
            return [session]

        # 2단계: 각 세션별 엔트리 분리 + SessionInfo 생성
        sessions: List[SessionInfo] = []
        for i, start_idx in enumerate(session_starts):
            end_idx = session_starts[i + 1] if i + 1 < len(session_starts) else len(all_entries)
            entries = all_entries[start_idx:end_idx]
            if entries:
                time_str = entries[0].timestamp.strftime("%H:%M")
                session = self._build_session(entries, date, time_str)
                sessions.append(session)

        return sessions

    def _build_session(self, entries: List[LogEntry], date: str, time_str: str) -> SessionInfo:
        """엔트리 리스트로 SessionInfo 생성"""
        session = SessionInfo(session_id=f"{date}_{time_str}")
        if not entries:
            return session

        session.start_time = entries[0].timestamp
        session.end_time = entries[-1].timestamp
        session.duration_seconds = (session.end_time - session.start_time).total_seconds()
        session.log_count = len(entries)

        # store_id 추출 시도
        for e in entries[:20]:
            if "store=" in e.message:
                import re as _re
                sm = _re.search(r"store[=:](\d+)", e.message)
                if sm:
                    session.store_id = sm.group(1)
                    break

        # Phase 분류
        phases = self._extract_phases(entries)
        session.phases = phases

        # 에러/경고 집계
        for e in entries:
            if e.level == "ERROR":
                session.total_errors += 1
            elif e.level == "WARNING":
                session.total_warnings += 1

        # 전체 상태
        if session.total_errors == 0:
            session.overall_status = "SUCCESS"
        elif session.total_errors <= 3:
            session.overall_status = "PARTIAL"
        else:
            session.overall_status = "FAILED"

        return session

    def _extract_phases(self, entries: List[LogEntry]) -> List[PhaseInfo]:
        """엔트리 리스트에서 Phase 경계를 감지하여 PhaseInfo 리스트 생성"""
        phases: List[PhaseInfo] = []
        current_phase: Optional[PhaseInfo] = None

        for entry in entries:
            if entry.phase:
                # 이전 Phase 마무리
                if current_phase:
                    current_phase.end_time = entry.timestamp
                    current_phase.duration_seconds = (
                        current_phase.end_time - current_phase.start_time
                    ).total_seconds() if current_phase.start_time else 0

                # 새 Phase 시작
                current_phase = PhaseInfo(
                    phase_id=entry.phase,
                    phase_name=entry.phase_name or KNOWN_PHASES.get(entry.phase, ""),
                    start_time=entry.timestamp,
                )
                phases.append(current_phase)

            # 현재 Phase에 로그 카운트
            if current_phase:
                current_phase.log_count += 1
                if entry.level == "ERROR":
                    current_phase.error_count += 1
                    if len(current_phase.key_messages) < 3:
                        current_phase.key_messages.append(entry.message[:120])
                elif entry.level == "WARNING":
                    current_phase.warning_count += 1

        # 마지막 Phase 마무리
        if current_phase and entries:
            current_phase.end_time = entries[-1].timestamp
            current_phase.duration_seconds = (
                current_phase.end_time - current_phase.start_time
            ).total_seconds() if current_phase.start_time else 0

        # 상태 결정
        for p in phases:
            if p.error_count > 0:
                p.status = f"{p.error_count} ERRORS"
            else:
                p.status = "OK"

        return phases

    # ── Phase 관련 ──

    def get_phase_summary(
        self,
        date: str,
        time_hint: str = "07:00",
        log_file: str = "main",
    ) -> List[PhaseInfo]:
        """Phase별 요약 리스트"""
        session = self.extract_session(date, time_hint, log_file)
        return session.phases

    def get_phase_logs(
        self,
        date: str,
        phase_id: str,
        time_hint: str = "07:00",
        log_file: str = "main",
    ) -> List[LogEntry]:
        """특정 Phase의 전체 로그 라인 추출"""
        sessions = self.list_sessions(date, log_file)
        if not sessions:
            return []

        # time_hint로 세션 선택
        try:
            hint_parts = time_hint.split(":")
            hint_hour = int(hint_parts[0])
            hint_min = int(hint_parts[1]) if len(hint_parts) > 1 else 0
            hint_dt = datetime.strptime(f"{date} {hint_hour:02d}:{hint_min:02d}:00", DATE_FMT)
        except (ValueError, IndexError):
            hint_dt = datetime.strptime(f"{date} 07:00:00", DATE_FMT)

        # 해당 세션의 Phase 시간 범위 조회
        all_entries: List[LogEntry] = list(
            stream_log_file(log_file, start_date=date, end_date=date, log_dir=self.log_dir)
        )

        # Phase 경계 찾기
        in_target_phase = False
        result: List[LogEntry] = []

        for entry in all_entries:
            if entry.phase:
                if entry.phase == phase_id:
                    in_target_phase = True
                elif in_target_phase:
                    break  # 다음 Phase 시작 -> 종료

            if in_target_phase:
                result.append(entry)

        return result

    # ── 필터링 ──

    def filter_by_level(
        self,
        level: str = "ERROR",
        log_file: str = "main",
        last_hours: Optional[int] = None,
        last_days: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_results: int = 200,
    ) -> List[LogEntry]:
        """로그 레벨로 필터링"""
        levels = {lv.strip().upper() for lv in level.split(",")}

        sd, ed = self._resolve_date_range(last_hours, last_days, start_date, end_date)

        results: List[LogEntry] = []
        files = self._resolve_files(log_file)

        for f in files:
            for entry in stream_log_file(f, start_date=sd, end_date=ed, log_dir=self.log_dir):
                if entry.level in levels:
                    # last_hours의 시간 필터 (날짜만으로는 부족)
                    if last_hours and not self._within_hours(entry.timestamp, last_hours):
                        continue
                    results.append(entry)
                    if len(results) >= max_results:
                        return results

        return results

    def search(
        self,
        keyword: str,
        log_file: str = "main",
        last_hours: Optional[int] = None,
        last_days: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        regex: bool = False,
        case_sensitive: bool = False,
        max_results: int = 100,
    ) -> List[LogEntry]:
        """키워드 검색"""
        if regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            pattern = re.compile(keyword, flags)
            match_fn = lambda msg: pattern.search(msg)
        else:
            if case_sensitive:
                match_fn = lambda msg: keyword in msg
            else:
                kw_lower = keyword.lower()
                match_fn = lambda msg: kw_lower in msg.lower()

        sd, ed = self._resolve_date_range(last_hours, last_days, start_date, end_date)
        results: List[LogEntry] = []
        files = self._resolve_files(log_file)

        for f in files:
            for entry in stream_log_file(f, start_date=sd, end_date=ed, log_dir=self.log_dir):
                if match_fn(entry.message) or match_fn(entry.module):
                    if last_hours and not self._within_hours(entry.timestamp, last_hours):
                        continue
                    results.append(entry)
                    if len(results) >= max_results:
                        return results

        return results

    # ── 통계 ──

    def get_file_stats(self, log_file: str = "main") -> Dict[str, Any]:
        """로그 파일 기본 통계"""
        path = _resolve_log_path(log_file, self.log_dir)
        if not path.exists():
            return {
                "file": log_file,
                "path": str(path),
                "exists": False,
            }

        size_bytes = path.stat().st_size
        size_mb = size_bytes / (1024 * 1024)

        first_date = None
        last_date = None
        line_count = 0
        error_count = 0
        warning_count = 0

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw_line in f:
                line_count += 1
                if len(raw_line) >= 10:
                    dp = raw_line[:10]
                    if first_date is None and dp[:4].isdigit():
                        first_date = dp
                    if dp[:4].isdigit():
                        last_date = dp

                # 레벨 빠른 체크 (정규식 없이)
                if "| ERROR" in raw_line:
                    error_count += 1
                elif "| WARNING" in raw_line:
                    warning_count += 1

        return {
            "file": log_file,
            "filename": path.name,
            "path": str(path),
            "exists": True,
            "size_mb": round(size_mb, 1),
            "line_count": line_count,
            "first_date": first_date,
            "last_date": last_date,
            "error_count": error_count,
            "warning_count": warning_count,
        }

    def get_all_stats(self) -> List[Dict[str, Any]]:
        """모든 로그 파일 통계"""
        return [self.get_file_stats(key) for key in LOG_FILES]

    # ── tail ──

    def tail(self, log_file: str = "main", n: int = 50) -> List[LogEntry]:
        """최근 N줄 (파일 끝에서부터, 유효 로그 라인만)"""
        path = _resolve_log_path(log_file, self.log_dir)
        if not path.exists():
            return []

        # traceback 등 비로그 줄이 많으므로 넉넉히 읽기
        multiplier = 5
        raw_lines = _tail_file(path, n * multiplier)
        results: List[LogEntry] = []
        for raw in raw_lines:
            entry = parse_line(raw, line_number=0)
            if entry:
                results.append(entry)
        return results[-n:]

    # ── 헬퍼 ──

    def _resolve_date_range(
        self,
        last_hours: Optional[int],
        last_days: Optional[int],
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> Tuple[Optional[str], Optional[str]]:
        """시간 파라미터 -> (start_date, end_date) 문자열"""
        now = datetime.now()
        if last_hours:
            start_dt = now - timedelta(hours=last_hours)
            return start_dt.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")
        if last_days:
            start_dt = now - timedelta(days=last_days)
            return start_dt.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")
        return start_date, end_date

    @staticmethod
    def _within_hours(ts: datetime, hours: int) -> bool:
        """타임스탬프가 최근 N시간 이내인지"""
        cutoff = datetime.now() - timedelta(hours=hours)
        return ts >= cutoff

    def _resolve_files(self, log_file: str) -> List[str]:
        """'all'이면 전체 파일 리스트, 아니면 단일"""
        if log_file == "all":
            return list(LOG_FILES.keys())
        return [log_file]


# ──────────────────────────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────────────────────────

def _tail_file(path: Path, n: int) -> List[str]:
    """파일 끝에서 N줄 읽기 (메모리 효율적)"""
    with open(path, "rb") as f:
        # 파일 끝으로 이동
        f.seek(0, 2)
        file_size = f.tell()
        if file_size == 0:
            return []

        # 청크 단위로 역방향 읽기
        lines: List[str] = []
        chunk_size = min(8192, file_size)
        position = file_size

        while position > 0 and len(lines) < n + 1:
            read_size = min(chunk_size, position)
            position -= read_size
            f.seek(position)
            chunk = f.read(read_size)
            try:
                text = chunk.decode("utf-8", errors="replace")
            except Exception:
                text = chunk.decode("latin-1", errors="replace")
            chunk_lines = text.split("\n")

            if lines:
                # 이전 청크의 첫 줄과 현재 청크의 마지막 줄을 병합
                lines[0] = chunk_lines[-1] + lines[0]
                lines = chunk_lines[:-1] + lines
            else:
                lines = chunk_lines

        # 마지막 n줄만
        result = [l for l in lines[-n:] if l.strip()]
        return result


def format_duration(seconds: float) -> str:
    """초 -> 사람이 읽기 쉬운 시간"""
    if seconds < 0:
        return "0s"
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s" if secs else f"{minutes}m"
    hours = int(minutes // 60)
    mins = minutes % 60
    return f"{hours}h{mins:02d}m"


def format_entries(
    entries: List[LogEntry],
    max_lines: int = 200,
    truncate_message: int = 200,
    show_line_numbers: bool = False,
) -> str:
    """LogEntry 리스트 -> 텍스트"""
    if not entries:
        return "(no results)"

    lines: List[str] = []
    for entry in entries[:max_lines]:
        ts = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        msg = entry.message
        if truncate_message and len(msg) > truncate_message:
            msg = msg[:truncate_message] + "..."

        if show_line_numbers:
            lines.append(f"[L:{entry.line_number}] {ts} | {entry.level:<8s} | {entry.module} | {msg}")
        else:
            lines.append(f"{ts} | {entry.level:<8s} | {entry.module} | {msg}")

    result = "\n".join(lines)
    if len(entries) > max_lines:
        result += f"\n\n[... truncated, showing {max_lines}/{len(entries)} lines]"
    return result


def format_session_summary(session: SessionInfo) -> str:
    """SessionInfo -> 요약 텍스트"""
    if session.overall_status == "not_found":
        return f"=== Session not found: {session.session_id} ==="

    lines: List[str] = []

    # 헤더
    start_str = session.start_time.strftime("%Y-%m-%d %H:%M:%S") if session.start_time else "?"
    store_str = f" (store={session.store_id})" if session.store_id else ""
    duration_str = format_duration(session.duration_seconds)

    lines.append(f"=== Session: {start_str}{store_str} ===")
    lines.append(
        f"Duration: {duration_str} | "
        f"Status: {session.overall_status} | "
        f"Errors: {session.total_errors} | "
        f"Warnings: {session.total_warnings} | "
        f"Lines: {session.log_count}"
    )
    lines.append("")

    # Phase 타임라인
    if session.phases:
        lines.append("Phase Timeline:")
        for p in session.phases:
            start_t = p.start_time.strftime("%H:%M:%S") if p.start_time else "??:??:??"
            dur = format_duration(p.duration_seconds)
            name = p.phase_name or KNOWN_PHASES.get(p.phase_id, "?")
            lines.append(
                f"  {start_t} [{p.phase_id:<5s}] {name:<35s} {dur:>7s}  {p.status}"
            )
    else:
        lines.append("(no phases detected)")

    return "\n".join(lines)


def format_phase_timeline(phases: List[PhaseInfo]) -> str:
    """Phase 리스트 -> 타임라인 텍스트"""
    if not phases:
        return "(no phases detected)"

    lines: List[str] = []
    for p in phases:
        start_t = p.start_time.strftime("%H:%M:%S") if p.start_time else "??:??:??"
        dur = format_duration(p.duration_seconds)
        name = p.phase_name or KNOWN_PHASES.get(p.phase_id, "?")
        lines.append(
            f"  {start_t} [{p.phase_id:<5s}] {name:<35s} {dur:>7s}  {p.status}"
        )
    return "\n".join(lines)


def format_file_stats(stats_list: List[Dict[str, Any]]) -> str:
    """파일 통계 리스트 -> 텍스트"""
    lines = ["=== Log File Statistics ==="]
    for s in stats_list:
        if not s.get("exists"):
            lines.append(f"  {s['file']:<12s} (not found)")
            continue

        line_k = s["line_count"] / 1000
        date_range = ""
        if s.get("first_date") and s.get("last_date"):
            date_range = f"  {s['first_date']} ~ {s['last_date']}"

        lines.append(
            f"  {s['file']:<12s} {s['filename']:<20s} "
            f"{s['size_mb']:>6.1f} MB  "
            f"{line_k:>7.0f}K lines"
            f"{date_range}"
        )
    return "\n".join(lines)
