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
    """이슈 체인 파일에서 활성 항목 추출 + 전체 이력(RESOLVED 포함) 밀도 계산"""
    items = []
    # 영역별 전체 이슈 수 (RESOLVED 포함) — 밀도 계산용
    area_total_count = {}

    for f in sorted(ISSUES_DIR.glob("*.md")):
        if f.name == "_TEMPLATE.md":
            continue

        text = f.read_text(encoding="utf-8")

        # 전체 이슈 블록 수 카운트 (RESOLVED/DEFERRED 포함)
        all_issues = re.findall(r"^## \[(\w+)\]", text, re.MULTILINE)
        area_total_count[f.name] = len(all_issues)

        # 활성 항목만 추출
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

    # 영역별 밀도 기반 자동 승격
    items = _auto_promote(items, area_total_count)

    # 정렬: 상태 → 우선순위 → 제목
    items.sort(key=lambda x: (
        STATUS_ORDER.get(x["status"], 9),
        PRIORITY_ORDER.get(x["priority"], 9),
        x["title"],
    ))

    return items


# ── 영역별 밀도 기반 자동 승격 ──────────────────────────────────────

# 이슈 밀도 임계값: 이 이상이면 해당 영역의 PLANNED P3→P2 자동 승격
DENSITY_THRESHOLD = 3


def _auto_promote(items, area_total_count):
    """같은 영역에 이슈가 DENSITY_THRESHOLD건 이상이면 P3→P2 자동 승격

    승격 조건:
      1. 해당 영역(파일)의 전체 이슈 수(RESOLVED 포함) >= DENSITY_THRESHOLD
      2. 항목이 [PLANNED] 상태이고 P3인 경우
    승격 시 비고에 "(밀도 승격)" 표시
    """
    # 밀도가 높은 영역 식별
    hot_areas = {f for f, cnt in area_total_count.items() if cnt >= DENSITY_THRESHOLD}

    if not hot_areas:
        return items

    promoted = 0
    for item in items:
        if (item["file"] in hot_areas
                and item["status"] == "PLANNED"
                and item["priority"] == "P3"):
            item["priority"] = "P2"
            item["note"] = f"(밀도 승격) {item['note']}".strip()
            promoted += 1

    if promoted > 0:
        print(f"밀도 승격: {promoted}건 P3→P2 ({', '.join(sorted(hot_areas))})")

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
