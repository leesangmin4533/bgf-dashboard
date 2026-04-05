#!/bin/bash
# Hook: PostToolUse(Bash) — 커밋 후 이슈 체인 갱신 확인 + 상태 전환 리마인더
#
# 동작:
#   1. git commit이 방금 실행됐는지 확인 (git log -1 타임스탬프)
#   2. fix() 커밋이면 docs/05-issues/ 포함 여부 확인 → 미포함 시 경고
#   3. feat() 커밋 + docs/05-issues/ 포함 시 → 상태 전환 리마인더
#   4. docs/05-issues/ 변경 감지 시 → sync_issue_table.py 자동 실행

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | python -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null)

# git commit 명령이 아니면 통과
if ! echo "$COMMAND" | grep -q 'git commit'; then
    exit 0
fi

# 최근 커밋이 10초 이내인지 확인 (방금 커밋 성공 여부)
LAST_COMMIT_EPOCH=$(git log -1 --format=%ct 2>/dev/null || echo 0)
NOW_EPOCH=$(date +%s)
DIFF=$((NOW_EPOCH - LAST_COMMIT_EPOCH))

if [ "$DIFF" -gt 10 ]; then
    # 커밋이 실패했거나 오래된 것 → 무시
    exit 0
fi

LAST_MSG=$(git log -1 --format=%s 2>/dev/null)
CHANGED_FILES=$(git diff HEAD~1 --name-only 2>/dev/null || echo "")
HAS_ISSUES=$(echo "$CHANGED_FILES" | grep 'docs/05-issues/' || true)

# --- fix() 커밋인데 이슈 체인 미포함 → 경고 ---
if echo "$LAST_MSG" | grep -q 'fix('; then
    if [ -z "$HAS_ISSUES" ]; then
        echo "[Hook] fix() 커밋 감지: $LAST_MSG" >&2
        echo "[Hook] docs/05-issues/ 이슈 체인이 포함되지 않았습니다." >&2
        echo "[Hook] 이슈 체인을 갱신하고 별도 커밋하세요." >&2
    fi
fi

# --- 상태 전환 리마인더 ---
if [ -n "$HAS_ISSUES" ]; then
    if echo "$LAST_MSG" | grep -q 'feat('; then
        echo "[Hook] feat() + 이슈 체인 감지. [PLANNED]→[OPEN] 상태 전환이 필요한지 확인하세요." >&2
    fi
    if echo "$LAST_MSG" | grep -q 'fix('; then
        echo "[Hook] fix() + 이슈 체인 감지. [OPEN]→[WATCHING] 상태 전환이 필요한지 확인하세요." >&2
    fi

    # sync_issue_table.py 자동 실행
    SYNC_SCRIPT="$CLAUDE_PROJECT_DIR/scripts/sync_issue_table.py"
    if [ -f "$SYNC_SCRIPT" ]; then
        python "$SYNC_SCRIPT" 2>/dev/null
    fi
fi

exit 0
