"""
이슈 체인 → CLAUDE.md 활성 이슈 테이블 자동 동기화

docs/05-issues/*.md 파일을 파싱하여 [PLANNED]/[OPEN]/[WATCHING] 항목을
CLAUDE.md의 마커 영역에 자동 갱신한다.

사용:
  python scripts/sync_issue_table.py          # 실행 후 CLAUDE.md 갱신
  python scripts/sync_issue_table.py --dry    # 미리보기만 (파일 수정 안 함)
"""

import re
import sys
import io
from pathlib import Path

# Windows cp949 인코딩 문제 방지
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ISSUES_DIR = PROJECT_ROOT / "docs" / "05-issues"
CLAUDE_MD = PROJECT_ROOT / "CLAUDE.md"

START_MARKER = "<!-- ISSUE_TABLE_START -->"
END_MARKER = "<!-- ISSUE_TABLE_END -->"

# 상태별 정렬 순서 (긴급한 것 먼저)
STATUS_ORDER = {"OPEN": 0, "WATCHING": 1, "PLANNED": 2, "DEFERRED": 3}
# 우선순위 정렬
PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2, "-": 3}


def parse_issue_files():
    """이슈 체인 파일에서 활성 항목 추출"""
    items = []

    for f in sorted(ISSUES_DIR.glob("*.md")):
        if f.name == "_TEMPLATE.md":
            continue

        text = f.read_text(encoding="utf-8")

        # ## [STATUS] 제목 (P숫자) 또는 ## [STATUS] 제목 (날짜~)
        pattern = r"^## \[(\w+)\]\s+(.+?)$"
        for match in re.finditer(pattern, text, re.MULTILINE):
            status = match.group(1)
            title_raw = match.group(2).strip()

            if status not in ("PLANNED", "OPEN", "WATCHING"):
                continue

            # 우선순위 추출: (P1), (P2), (P3)
            prio_match = re.search(r"\(P([1-3])\)", title_raw)
            priority = f"P{prio_match.group(1)}" if prio_match else "-"

            # 제목 정리: (P2) 제거, 날짜 범위 제거
            title = re.sub(r"\s*\(P[1-3]\)\s*", " ", title_raw).strip()
            title = re.sub(r"\s*\(\d{2}-\d{2}\s*~.*?\)\s*$", "", title).strip()

            # 비고: 선행조건 또는 검증 날짜 추출
            block_start = match.start()
            block_end = text.find("\n---", block_start + 1)
            if block_end == -1:
                block_end = len(text)
            block = text[block_start:block_end]

            note = ""
            prereq = re.search(r"\*\*선행조건\*\*:\s*(.+)", block)
            if prereq:
                note = prereq.group(1).strip()[:40]
            elif status == "WATCHING":
                check = re.search(r"- \[ \]\s+(.+?)(?:\n|$)", block)
                if check:
                    note = check.group(1).strip()[:40]

            items.append({
                "status": status,
                "priority": priority,
                "title": title,
                "file": f.name,
                "note": note,
            })

    # 정렬: 상태 → 우선순위 → 제목
    items.sort(key=lambda x: (
        STATUS_ORDER.get(x["status"], 9),
        PRIORITY_ORDER.get(x["priority"], 9),
        x["title"],
    ))

    return items


def build_table(items):
    """마크다운 테이블 문자열 생성"""
    lines = [
        "| 상태 | 우선순위 | 이슈 | 파일 | 비고 |",
        "|:---:|:---:|------|------|------|",
    ]
    for item in items:
        lines.append(
            f"| {item['status']} | {item['priority']} "
            f"| {item['title']} | {item['file']} "
            f"| {item['note']} |"
        )
    return "\n".join(lines)


def update_claude_md(table_str, dry_run=False):
    """CLAUDE.md의 마커 영역을 테이블로 교체"""
    content = CLAUDE_MD.read_text(encoding="utf-8")

    start_idx = content.find(START_MARKER)
    end_idx = content.find(END_MARKER)

    if start_idx == -1 or end_idx == -1:
        print(f"ERROR: CLAUDE.md에 마커가 없습니다. 수동으로 추가하세요:")
        print(f"  {START_MARKER}")
        print(f"  {END_MARKER}")
        return False

    replacement = f"{START_MARKER}\n{table_str}\n{END_MARKER}"
    new_content = content[:start_idx] + replacement + content[end_idx + len(END_MARKER):]

    if dry_run:
        print("[DRY RUN] 아래 테이블로 갱신됩니다:\n")
        print(table_str)
        return True

    CLAUDE_MD.write_text(new_content, encoding="utf-8")
    print(f"CLAUDE.md 활성 이슈 테이블 갱신 완료 ({len(items)}건)")
    return True


if __name__ == "__main__":
    dry_run = "--dry" in sys.argv
    items = parse_issue_files()

    if not items:
        print("활성 이슈 없음")
        sys.exit(0)

    table = build_table(items)
    update_claude_md(table, dry_run=dry_run)
