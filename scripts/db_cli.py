"""
BGF 리테일 SQLite CLI

프로젝트 DB를 대화형으로 쿼리하는 도구.
매장 DB + common.db 자동 ATTACH로 크로스 조인 지원.

Usage:
    python scripts/db_cli.py                                         # 매장 선택기
    python scripts/db_cli.py --store 47863                           # 대화형 REPL
    python scripts/db_cli.py --store 47863 -c "SELECT COUNT(*) FROM daily_sales"
    python scripts/db_cli.py --common -c "SELECT * FROM products LIMIT 5"
    python scripts/db_cli.py --store 46513 --csv -c "SELECT * FROM daily_sales LIMIT 100" > out.csv
"""

import sys
import io

# Windows CP949 -> UTF-8
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (
            getattr(_s, "encoding", "") or ""
        ).lower().replace("-", "") != "utf8":
            setattr(
                sys, _sn,
                io.TextIOWrapper(
                    _s.buffer, encoding="utf-8",
                    errors="replace", line_buffering=True,
                ),
            )

from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import argparse
import csv as csv_mod
import json
import sqlite3
import textwrap
import time
import unicodedata
from typing import Optional, List, Tuple

from src.infrastructure.database.connection import (
    DBRouter,
    COMMON_TABLES,
    STORE_TABLES,
    attach_common_with_views,
)
from src.prediction.categories.default import CATEGORY_NAMES


# ── CJK 너비 유틸 ──────────────────────────────────────────────

def _display_width(s: str) -> int:
    """한글 등 CJK 문자를 2칸으로 계산한 표시 너비."""
    w = 0
    for ch in s:
        if unicodedata.east_asian_width(ch) in ("F", "W"):
            w += 2
        else:
            w += 1
    return w


def _pad(s: str, target: int) -> str:
    """CJK 너비 기준 오른쪽 패딩."""
    diff = target - _display_width(s)
    return s + " " * max(diff, 0)


def _truncate(s: str, max_w: int) -> str:
    """CJK 너비 기준 문자열 자르기."""
    if _display_width(s) <= max_w:
        return s
    w = 0
    for i, ch in enumerate(s):
        cw = 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
        if w + cw > max_w - 2:
            return s[:i] + ".."
        w += cw
    return s


# ── 테이블 포매터 ──────────────────────────────────────────────

class TableFormatter:
    """쿼리 결과를 정렬된 테이블로 출력."""

    def __init__(self, max_col_width: int = 40, max_rows: int = 500):
        self.max_col_width = max_col_width
        self.max_rows = max_rows

    def format(self, headers: List[str], rows: list) -> str:
        if not rows:
            return "(0 rows)"

        # 셀 문자열 변환
        str_rows: List[List[str]] = []
        for row in rows[: self.max_rows]:
            str_rows.append([self._cell(v) for v in row])

        # 컬럼 너비 계산
        widths = [_display_width(h) for h in headers]
        for row in str_rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], _display_width(cell))
        widths = [min(w, self.max_col_width) for w in widths]

        lines: List[str] = []
        # 헤더
        lines.append(
            " | ".join(_pad(_truncate(h, w), w) for h, w in zip(headers, widths))
        )
        lines.append("-+-".join("-" * w for w in widths))
        # 데이터
        for row in str_rows:
            parts = []
            for cell, w in zip(row, widths):
                parts.append(_pad(_truncate(cell, w), w))
            lines.append(" | ".join(parts))

        total = len(rows)
        if total > self.max_rows:
            lines.append(f"({total} rows, 처음 {self.max_rows}개만 표시)")
        else:
            lines.append(f"({total} rows)")
        return "\n".join(lines)

    @staticmethod
    def _cell(v) -> str:
        if v is None:
            return "NULL"
        return str(v)


# ── 매장 목록 로드 ─────────────────────────────────────────────

def _load_stores() -> List[dict]:
    """config/stores.json에서 매장 목록 로드."""
    path = project_root / "config" / "stores.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [s for s in data.get("stores", []) if s.get("is_active")]


# ── REPL ───────────────────────────────────────────────────────

class SQLiteREPL:
    """대화형 SQL 실행 환경."""

    def __init__(self, store_id: Optional[str] = None):
        self.store_id = store_id
        self.formatter = TableFormatter()
        self.conn: Optional[sqlite3.Connection] = None
        self.history: List[str] = []
        self._last_headers: List[str] = []
        self._last_rows: list = []
        self._connect()

    # ── 접속 ──

    def _connect(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
        if self.store_id:
            self.conn = DBRouter.get_store_connection(self.store_id)
            attach_common_with_views(self.conn, self.store_id)
        else:
            self.conn = DBRouter.get_common_connection()

    # ── 대화형 루프 ──

    def run(self):
        self._print_banner()
        buf: List[str] = []
        while True:
            prompt = self._prompt(bool(buf))
            try:
                line = input(prompt)
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                buf.clear()
                continue

            stripped = line.strip()

            # dot 명령 (버퍼 비어있을 때만)
            if not buf and stripped.startswith("."):
                self._dispatch_dot(stripped)
                continue

            buf.append(line)
            joined = " ".join(b.strip() for b in buf).strip()

            # SQL은 ;로 끝나야 실행
            if joined.endswith(";"):
                self._exec(joined)
                buf.clear()
                continue

            # 빈 줄로 버퍼 초기화
            if not stripped and len(buf) == 1:
                buf.clear()

    # ── 1회 실행 ──

    def execute_one(self, sql: str, csv_mode: bool = False):
        sql = sql.strip()
        if sql.startswith("."):
            self._dispatch_dot(sql)
            return
        if not sql.endswith(";"):
            sql += ";"
        self._exec(sql, csv_mode=csv_mode)

    # ── SQL 실행 ──

    def _exec(self, sql: str, csv_mode: bool = False):
        sql = sql.strip().rstrip(";").strip()
        if not sql:
            return
        self.history.append(sql)
        t0 = time.perf_counter()
        try:
            cursor = self.conn.execute(sql)
        except sqlite3.Error as e:
            print(f"ERROR: {e}")
            return

        elapsed = time.perf_counter() - t0

        if cursor.description:
            headers = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
            # sqlite3.Row → tuple
            rows = [tuple(r) for r in rows]
            self._last_headers = headers
            self._last_rows = rows

            if csv_mode:
                self._print_csv(headers, rows)
            else:
                print(self.formatter.format(headers, rows))
                print(f"Time: {elapsed:.3f}s")
        else:
            self.conn.commit()
            print(f"OK ({cursor.rowcount} rows affected, {elapsed:.3f}s)")

    # ── CSV 출력 ──

    @staticmethod
    def _print_csv(headers: List[str], rows: list):
        writer = csv_mod.writer(sys.stdout)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)

    # ── 프롬프트 ──

    def _prompt(self, continuation: bool) -> str:
        if continuation:
            return "     ...> "
        tag = self.store_id or "common"
        return f"bgf({tag})> "

    # ── 배너 ──

    def _print_banner(self):
        if self.store_id:
            stores = _load_stores()
            name = next(
                (s["store_name"] for s in stores if s["store_id"] == self.store_id),
                "",
            )
            db_path = DBRouter.get_store_db_path(self.store_id)
            mb = db_path.stat().st_size / 1024 / 1024 if db_path.exists() else 0
            print(f"Connected: store {self.store_id} ({name}) [{mb:.1f} MB]")
            print(f"common.db auto-attached. '.help' for commands, '.quit' to exit.")
        else:
            db_path = DBRouter.get_common_db_path()
            mb = db_path.stat().st_size / 1024 / 1024 if db_path.exists() else 0
            print(f"Connected: common.db [{mb:.1f} MB]")
            print(f"'.help' for commands, '.quit' to exit.")
        print()

    # ── Dot 명령 디스패치 ──

    def _dispatch_dot(self, line: str):
        parts = line.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        handlers = {
            ".help": self._cmd_help,
            ".quit": self._cmd_quit,
            ".exit": self._cmd_quit,
            ".q": self._cmd_quit,
            ".tables": self._cmd_tables,
            ".schema": self._cmd_schema,
            ".stores": self._cmd_stores,
            ".use": self._cmd_use,
            ".cat": self._cmd_cat,
            ".count": self._cmd_count,
            ".recent": self._cmd_recent,
            ".info": self._cmd_info,
            ".export": self._cmd_export,
            ".history": self._cmd_history,
        }

        handler = handlers.get(cmd)
        if handler:
            handler(arg)
        else:
            print(f"Unknown command: {cmd}  ('.help' for list)")

    # ── Dot 명령 구현 ──

    def _cmd_help(self, _arg: str):
        cmds = [
            (".tables", "테이블 목록 (main + common)"),
            (".schema TABLE", "CREATE 문 표시"),
            (".stores", "매장 목록"),
            (".use STORE_ID", "매장 전환"),
            (".cat [MID_CD]", "카테고리 이름 조회"),
            (".count TABLE", "행 수"),
            (".recent TABLE [N]", "최근 N행 (기본 10)"),
            (".info", "접속 정보"),
            (".export FILE", "마지막 결과 CSV 저장"),
            (".history", "쿼리 이력"),
            (".quit", "종료"),
        ]
        for c, desc in cmds:
            print(f"  {c:<22} {desc}")

    def _cmd_quit(self, _arg: str):
        if self.conn:
            self.conn.close()
        sys.exit(0)

    def _cmd_tables(self, _arg: str):
        # main 테이블
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        main_tables = [r[0] for r in cur.fetchall()]
        if main_tables:
            tag = f"main (store {self.store_id})" if self.store_id else "main (common)"
            print(f"\n  === {tag} ===")
            self._print_columns(main_tables)

        # common 테이블 (ATTACH된 경우)
        if self.store_id:
            try:
                cur = self.conn.execute(
                    "SELECT name FROM common.sqlite_master "
                    "WHERE type='table' ORDER BY name"
                )
                common_tables = [r[0] for r in cur.fetchall()]
                if common_tables:
                    print(f"\n  === common (attached) ===")
                    self._print_columns(common_tables)
            except sqlite3.Error:
                pass
        print()

    @staticmethod
    def _print_columns(items: List[str], cols: int = 3, width: int = 28):
        """아이템을 여러 컬럼으로 출력."""
        for i in range(0, len(items), cols):
            chunk = items[i : i + cols]
            print("  " + "".join(f"{t:<{width}}" for t in chunk))

    def _cmd_schema(self, arg: str):
        if not arg:
            print("Usage: .schema TABLE_NAME")
            return
        table = arg.strip()
        # main에서 먼저
        cur = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        row = cur.fetchone()
        if row:
            print(row[0] + ";")
            return
        # common에서
        if self.store_id:
            try:
                cur = self.conn.execute(
                    "SELECT sql FROM common.sqlite_master "
                    "WHERE type='table' AND name=?",
                    (table,),
                )
                row = cur.fetchone()
                if row:
                    print(f"-- (common.db)")
                    print(row[0] + ";")
                    return
            except sqlite3.Error:
                pass
        print(f"Table not found: {table}")

    def _cmd_stores(self, _arg: str):
        stores = _load_stores()
        if not stores:
            print("No stores found in config/stores.json")
            return
        print()
        for s in stores:
            sid = s["store_id"]
            name = s.get("store_name", "")
            loc = s.get("location", "")
            exists = "OK" if DBRouter.store_db_exists(sid) else "NO DB"
            marker = " <--" if sid == self.store_id else ""
            print(f"  {sid}  {name:<16} {loc:<12} [{exists}]{marker}")
        print()

    def _cmd_use(self, arg: str):
        if not arg:
            print("Usage: .use STORE_ID  (e.g. .use 47863)")
            return
        new_id = arg.strip()
        if new_id.lower() == "common":
            self.store_id = None
            self._connect()
            print(f"Switched to common.db")
            return
        if not DBRouter.store_db_exists(new_id):
            print(f"DB not found: data/stores/{new_id}.db")
            return
        self.store_id = new_id
        self._connect()
        stores = _load_stores()
        name = next(
            (s["store_name"] for s in stores if s["store_id"] == new_id), ""
        )
        print(f"Switched to store {new_id} ({name})")

    def _cmd_cat(self, arg: str):
        if arg:
            name = CATEGORY_NAMES.get(arg, "UNKNOWN")
            print(f"  {arg}: {name}")
        else:
            for mid in sorted(CATEGORY_NAMES.keys()):
                print(f"  {mid}: {CATEGORY_NAMES[mid]}")
            print(f"  ({len(CATEGORY_NAMES)} categories)")

    def _cmd_count(self, arg: str):
        if not arg:
            print("Usage: .count TABLE_NAME")
            return
        table = arg.strip()
        # common. 접두사 지원
        try:
            cur = self.conn.execute(f"SELECT COUNT(*) FROM {table}")
            cnt = cur.fetchone()[0]
            print(f"  {table}: {cnt:,} rows")
        except sqlite3.Error as e:
            print(f"ERROR: {e}")

    def _cmd_recent(self, arg: str):
        parts = arg.split()
        if not parts:
            print("Usage: .recent TABLE [N]")
            return
        table = parts[0]
        n = int(parts[1]) if len(parts) > 1 else 10
        try:
            cur = self.conn.execute(
                f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT ?", (n,)
            )
            headers = [d[0] for d in cur.description]
            rows = [tuple(r) for r in cur.fetchall()]
            print(self.formatter.format(headers, rows))
        except sqlite3.Error as e:
            print(f"ERROR: {e}")

    def _cmd_info(self, _arg: str):
        if self.store_id:
            db_path = DBRouter.get_store_db_path(self.store_id)
            mb = db_path.stat().st_size / 1024 / 1024 if db_path.exists() else 0
            print(f"  Store:   {self.store_id}")
            print(f"  DB:      {db_path} [{mb:.1f} MB]")
            common_path = DBRouter.get_common_db_path()
            cmb = common_path.stat().st_size / 1024 / 1024
            print(f"  Common:  {common_path} [{cmb:.1f} MB] (attached)")
        else:
            db_path = DBRouter.get_common_db_path()
            mb = db_path.stat().st_size / 1024 / 1024 if db_path.exists() else 0
            print(f"  DB:      {db_path} [{mb:.1f} MB]")

    def _cmd_export(self, arg: str):
        if not arg:
            print("Usage: .export filename.csv")
            return
        if not self._last_headers:
            print("No previous query result to export.")
            return
        filepath = Path(arg.strip())
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv_mod.writer(f)
            writer.writerow(self._last_headers)
            for row in self._last_rows:
                writer.writerow(row)
        print(f"Exported {len(self._last_rows)} rows to {filepath}")

    def _cmd_history(self, _arg: str):
        if not self.history:
            print("(no history)")
            return
        for i, sql in enumerate(self.history[-20:], 1):
            short = sql[:80] + "..." if len(sql) > 80 else sql
            print(f"  {i:>3}. {short}")


# ── 매장 선택기 ────────────────────────────────────────────────

def _store_picker() -> Optional[str]:
    """인터랙티브 매장 선택. 반환값: store_id 또는 None(common)."""
    stores = _load_stores()
    print("\nAvailable stores:")
    for i, s in enumerate(stores, 1):
        sid = s["store_id"]
        name = s.get("store_name", "")
        loc = s.get("location", "")
        exists = "OK" if DBRouter.store_db_exists(sid) else "NO DB"
        print(f"  {i}. {sid} - {name} ({loc}) [{exists}]")
    print(f"  c. common.db only")
    print()

    while True:
        try:
            choice = input("Select store [1-{}, c]: ".format(len(stores))).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if choice.lower() == "c":
            return "__common__"
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(stores):
                return stores[idx]["store_id"]
        except ValueError:
            # store_id 직접 입력도 허용
            if DBRouter.store_db_exists(choice):
                return choice
        print(f"Invalid choice. Enter 1-{len(stores)} or 'c'.")


# ── main ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="db_cli",
        description="BGF 리테일 SQLite CLI — 프로젝트 DB 대화형 쿼리 도구",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        examples:
          %(prog)s --store 46513                  Interactive REPL
          %(prog)s --store 46513 -c "SELECT count(*) FROM daily_sales"
          %(prog)s --common                       common.db only
          %(prog)s --store 46513 --csv -c "..."   CSV output
        """),
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--store", "-s", type=str, default=None,
        help="매장 코드 (e.g. 46513, 46704, 47863)",
    )
    group.add_argument(
        "--common", action="store_true",
        help="common.db만 접속",
    )

    parser.add_argument(
        "-c", "--command", type=str, default=None,
        help="SQL 또는 dot-command 1회 실행 후 종료",
    )
    parser.add_argument(
        "--max-rows", type=int, default=500,
        help="최대 표시 행 수 (기본 500)",
    )
    parser.add_argument(
        "--max-col-width", type=int, default=40,
        help="최대 컬럼 너비 (기본 40)",
    )
    parser.add_argument(
        "--csv", action="store_true",
        help="CSV 형식 출력 (-c와 함께)",
    )

    args = parser.parse_args()

    # 매장 결정
    store_id = args.store
    if not store_id and not args.common:
        if args.command or not sys.stdin.isatty():
            # 비대화형인데 매장 미지정 → common
            store_id = None
        else:
            # 대화형 → 매장 선택기
            picked = _store_picker()
            if picked is None:
                return
            store_id = None if picked == "__common__" else picked

    if args.common:
        store_id = None

    # DB 존재 확인
    if store_id and not DBRouter.store_db_exists(store_id):
        print(f"ERROR: data/stores/{store_id}.db not found")
        stores = _load_stores()
        if stores:
            print("Available stores:")
            for s in stores:
                print(f"  {s['store_id']} - {s.get('store_name', '')}")
        return

    repl = SQLiteREPL(store_id=store_id)
    repl.formatter.max_rows = args.max_rows
    repl.formatter.max_col_width = args.max_col_width

    # 비대화형: -c 또는 stdin 파이프
    if args.command:
        repl.execute_one(args.command, csv_mode=args.csv)
        return

    if not sys.stdin.isatty():
        sql = sys.stdin.read().strip()
        if sql:
            repl.execute_one(sql, csv_mode=args.csv)
        return

    # 대화형 REPL
    repl.run()


if __name__ == "__main__":
    main()
