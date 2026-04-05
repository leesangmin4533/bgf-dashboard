#!/bin/bash
# Hook: PreToolUse(Write) — 새 파일 생성 시 기존 코드 확인 리마인더
#
# 동작:
#   1. Write 대상 파일이 이미 존재하면 통과 (기존 파일 수정은 OK)
#   2. 새 파일 생성��면:
#      - src/ 하�� .py 파일인 경우 → 경고 메시지 출력
#      - tests/ 하위는 통과 (테스트 파일은 새로 만드는 것이 정상)
#      - docs/ 하위는 통과
#   3. 경고만 출력 (exit 0) — 차단하지 않음
#
# 목적:
#   "기존에 유사 구현이 있는데 새로 만드는" 실수 방지
#   함수명/메서드명 추측 대신 실제 코드 확인을 유도

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | python -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" 2>/dev/null)

# 파일 경로가 비어있으면 통과
if [ -z "$FILE_PATH" ]; then
    exit 0
fi

# 이미 존재하는 파일이면 통과 (수정은 OK)
if [ -f "$FILE_PATH" ]; then
    exit 0
fi

# tests/, docs/ 하위는 통과
if echo "$FILE_PATH" | grep -qE '(tests/|docs/|\.md$)'; then
    exit 0
fi

# src/ 하위 .py 파일 새로 생성 시 경고
if echo "$FILE_PATH" | grep -qE 'src/.*\.py$'; then
    # 파일명에서 모듈명 추출
    MODULE_NAME=$(basename "$FILE_PATH" .py)

    echo "" >&2
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
    echo "[Pre-Write Check] 새 모듈 생성 감지: $MODULE_NAME.py" >&2
    echo "" >&2
    echo "  다음을 확인했는지 점검하세요:" >&2
    echo "  1. 기존 코드에 유사 구현이 없는가? (grep/glob)" >&2
    echo "  2. 호출할 함수/메서드명을 실제 코드에서 확인했는가?" >&2
    echo "  3. DB 테이��/컬럼명을 schema.py 또는 models.py에서 확인했는가?" >&2
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
    echo "" >&2
fi

exit 0
