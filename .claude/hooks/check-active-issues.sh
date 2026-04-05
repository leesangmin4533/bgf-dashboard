#!/bin/bash
# Hook: 세션 시작 시 활성 이슈 체인 확인 → 컨텍스트 주입
#
# docs/05-issues/ 에서 [OPEN], [WATCHING], [PLANNED] 상태 이슈를 찾아
# 상태별 그룹핑 + 우선순위순으로 Claude 컨텍스트에 주입한다.

ISSUES_DIR="$CLAUDE_PROJECT_DIR/docs/05-issues"

if [ ! -d "$ISSUES_DIR" ]; then
    exit 0
fi

OPEN_ITEMS=""
WATCHING_ITEMS=""
PLANNED_P1=""
PLANNED_P2=""
PLANNED_P3=""

for f in "$ISSUES_DIR"/*.md; do
    [ -f "$f" ] || continue
    basename=$(basename "$f")
    [ "$basename" = "_TEMPLATE.md" ] && continue

    # ## [STATUS] 제목 패턴 추출
    while IFS= read -r line; do
        status=$(echo "$line" | grep -oE '\[(OPEN|WATCHING|PLANNED)\]' | tr -d '[]')
        [ -z "$status" ] && continue

        # 제목 추출: ## [STATUS] 이후 텍스트
        title=$(echo "$line" | sed 's/^## \[[A-Z]*\] //')

        entry="  [$status] $title ($basename)"

        case "$status" in
            OPEN)     OPEN_ITEMS="${OPEN_ITEMS}${entry}\n" ;;
            WATCHING) WATCHING_ITEMS="${WATCHING_ITEMS}${entry}\n" ;;
            PLANNED)
                if echo "$title" | grep -q '(P1)'; then
                    PLANNED_P1="${PLANNED_P1}${entry}\n"
                elif echo "$title" | grep -q '(P3)'; then
                    PLANNED_P3="${PLANNED_P3}${entry}\n"
                else
                    PLANNED_P2="${PLANNED_P2}${entry}\n"
                fi
                ;;
        esac
    done < <(grep '^## \[' "$f" 2>/dev/null || true)
done

OUTPUT=""

if [ -n "$OPEN_ITEMS" ]; then
    OUTPUT="${OUTPUT}[진행 중]\n${OPEN_ITEMS}"
fi
if [ -n "$WATCHING_ITEMS" ]; then
    OUTPUT="${OUTPUT}[검증 대기]\n${WATCHING_ITEMS}"
fi
if [ -n "$PLANNED_P1" ]; then
    OUTPUT="${OUTPUT}[계획 - 긴급]\n${PLANNED_P1}"
fi
if [ -n "$PLANNED_P2" ]; then
    OUTPUT="${OUTPUT}[계획 - 중요]\n${PLANNED_P2}"
fi
if [ -n "$PLANNED_P3" ]; then
    OUTPUT="${OUTPUT}[계획 - 개선]\n${PLANNED_P3}"
fi

if [ -n "$OUTPUT" ]; then
    echo -e "[이슈 체인 로드맵] 작업 시작 전 관련 이슈 확인:\n"
    echo -e "$OUTPUT"
    echo "관련 이슈가 있으면 해당 이슈 체인 문서를 먼저 읽고 이전 시도를 참고하세요."
fi

exit 0
