#!/bin/bash
# Hook: PreToolUse(Edit) — 기존 파일 수정 전 체크리스트
#
# 동작:
#   1. 수정 대상 파일 경로를 분석
#   2. 핵심 파일(DB 스키마, 업무 규칙, 핵심 로직) 수정 시 체크리스트 출력
#   3. 경고만 출력 (exit 0) — 차단하지 않음
#
# 목적:
#   - DB 스키마 변경 전 관련 PDCA 문서 확인 유도
#   - 업무 규칙 변경 전 사용자 확인 유도
#   - 핵심 로직 수정 전 기존 설계 확인 유도
#   - 2026-04-12 사건: orderable-day-all 설계가 이미 존재했는데 읽지 않고
#     PK 변경 + 허용목록 하드코딩으로 3회 실패

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | python -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" 2>/dev/null)

if [ -z "$FILE_PATH" ]; then
    exit 0
fi

# tests/, docs/ 하위는 통과
if echo "$FILE_PATH" | grep -qE '(tests/|docs/)'; then
    exit 0
fi

BASENAME=$(basename "$FILE_PATH")
TRIGGERED=0

# ──────��───────────────────────────────────
# 1. DB 스키마 변경 감지
# ──────────────────────────────────────────
if echo "$BASENAME" | grep -qE '^(models\.py|schema\.py)$'; then
    echo "" >&2
    echo "━━━━━━━━━━━━━━━━━━━━━━��━━━━━━━━━��━━━━━━" >&2
    echo "⚠️  [DB 스키마 변경 감지] $BASENAME" >&2
    echo "" >&2
    echo "  체크리스트:" >&2
    echo "  □ 관련 PDCA 설계 문서를 docs/archive/에서 검색했는가?" >&2
    echo "  □ 변경 가설을 사용자에게 확인했는가?" >&2
    echo "  □ PK/UNIQUE 변경 같은 비가역 변경인가? → 더 신중" >&2
    echo "  □ 기존 데이터 마이그레이션 계획이 있는가?" >&2
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
    TRIGGERED=1
fi

# ────────────────────────────���─────────────
# 2. 업무 규칙/상수 변경 감지
# ──────────────────────────────────────────
if echo "$BASENAME" | grep -qE '^constants\.py$'; then
    echo "" >&2
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
    echo "⚠️  [업무 규칙 변경 감지] $BASENAME" >&2
    echo "" >&2
    echo "  체크리스트:" >&2
    echo "  □ 변경할 규칙을 사용자에게 확인했는가?" >&2
    echo "  □ 데이터 패턴으로 추론한 규칙인가? → 사용자만 업무 규칙을 안다" >&2
    echo "  □ 하드코딩 허용목록 추가인가? → 예외 카테고리 사용자 확인 필수" >&2
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
    TRIGGERED=1
fi

# ──────────────────────────────────────────
# 3. 핵심 로직 파일 변경 감지
# ──────────────────────────────────────────
if echo "$FILE_PATH" | grep -qE '(auto_order\.py|order_executor\.py|improved_predictor\.py|daily_job\.py)'; then
    echo "" >&2
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
    echo "⚠️  [핵심 로직 변경 감지] $BASENAME" >&2
    echo "" >&2
    echo "  체크리스트:" >&2
    echo "  □ 관련 기존 설계(docs/archive/)를 확인했는가?" >&2
    echo "  □ 이 변경이 앞 단계의 의도를 깨지 않는가?" >&2
    echo "  □ 변경 범위를 사용자에게 설명했는가?" >&2
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
    TRIGGERED=1
fi

# ──────────────────────────────────────────
# 4. Repository 파일 변경 감지 (DB 접근 패턴)
# ─────────────────────────────────���────────
if echo "$FILE_PATH" | grep -qE '_repo\.py$|repository\.py$'; then
    echo "" >&2
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
    echo "⚠️  [Repository 변경 감지] $BASENAME" >&2
    echo "" >&2
    echo "  체크리스트:" >&2
    echo "  □ SQL 쿼리의 WHERE 조건/PK가 현재 스키마와 일치하는가?" >&2
    echo "  □ ON CONFLICT 절이 현재 PK와 일치하는가?" >&2
    echo "  □ 호출부(caller) 파급 범위를 확인했는가?" >&2
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
    TRIGGERED=1
fi

exit 0
