#!/bin/bash
# Hook: 세션 시작 시 활성 이슈 체인 확인 → 컨텍스트 주입
#
# docs/05-issues/ 에서 [OPEN], [WATCHING] 상태 이슈를 찾아
# Claude 컨텍스트에 주입한다. 작업 시작 전 기존 이슈 인지 강제.

ISSUES_DIR="$CLAUDE_PROJECT_DIR/docs/05-issues"

if [ ! -d "$ISSUES_DIR" ]; then
    exit 0
fi

ACTIVE=""
for f in "$ISSUES_DIR"/*.md; do
    [ -f "$f" ] || continue
    basename=$(basename "$f")
    [ "$basename" = "_TEMPLATE.md" ] && continue

    # [OPEN] 또는 [WATCHING] 블록 추출
    matches=$(grep -n '\[OPEN\]\|\[WATCHING\]' "$f" 2>/dev/null || true)
    if [ -n "$matches" ]; then
        ACTIVE="${ACTIVE}
--- ${basename} ---
${matches}"
    fi
done

if [ -n "$ACTIVE" ]; then
    echo "[이슈 체인 활성 항목] 작업 시작 전 아래 이슈와 관련 있는지 확인하세요:
${ACTIVE}

관련 이슈가 있으면 해당 이슈 체인 문서를 먼저 읽고 이전 시도를 참고하세요."
fi

exit 0
