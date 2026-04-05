#!/bin/bash
# Hook: fix() 커밋 시 이슈 체인 갱신 여부 확인
#
# 동작:
#   1. git commit 명령인지 확인
#   2. 커밋 메시지에 fix( 가 포함되어 있는지 확인
#   3. docs/05-issues/ 파일이 staged 되어 있는지 확인
#   4. 없으면 exit 2 (차단) + 경고 메시지

INPUT=$(cat)

# jq 없는 환경 대비: python으로 JSON 파싱
COMMAND=$(echo "$INPUT" | python -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null)

# git commit 명령이 아니면 통과
if ! echo "$COMMAND" | grep -q 'git commit'; then
    exit 0
fi

# fix( 타입 커밋이 아니면 통과
if ! echo "$COMMAND" | grep -q 'fix('; then
    exit 0
fi

# docs/05-issues/ 파일이 staged 되어 있는지 확인
ISSUES_STAGED=$(git diff --cached --name-only 2>/dev/null | grep 'docs/05-issues/' || true)

if [ -z "$ISSUES_STAGED" ]; then
    echo "fix() 커밋인데 docs/05-issues/ 이슈 체인이 staged 되지 않았습니다. 이슈 체인을 먼저 작성/갱신하고 git add 한 뒤 다시 커밋하세요." >&2
    exit 2
fi

exit 0
