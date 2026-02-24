# PDCA Archive: stock-discrepancy

## Context

stock-discrepancy 피처의 PDCA 사이클이 완료되었다 (Match Rate 100%, Report 생성 완료).
완료된 문서를 `docs/archive/2026-02/stock-discrepancy/`로 아카이브하고 `.pdca-status.json`을 업데이트한다.

## 전제 조건 확인

- [x] phase = "completed" ✅
- [x] matchRate = 100% ✅
- [x] Report 존재: `docs/04-report/features/stock-discrepancy.report.md` ✅
- [x] Analysis 존재: `docs/03-analysis/stock-discrepancy.analysis.md` ✅

## 아카이브 작업

### Step 1: 아카이브 폴더 생성
```
docs/archive/2026-02/stock-discrepancy/
```

### Step 2: 문서 이동 (3개)
| 원본 | 대상 |
|------|------|
| `docs/03-analysis/stock-discrepancy.analysis.md` | `docs/archive/2026-02/stock-discrepancy/stock-discrepancy.analysis.md` |
| `docs/04-report/features/stock-discrepancy.report.md` | `docs/archive/2026-02/stock-discrepancy/stock-discrepancy.report.md` |
| `.claude/plans/joyful-tickling-hollerith.md` (Plan) | `docs/archive/2026-02/stock-discrepancy/stock-discrepancy.plan.md` |

### Step 3: `docs/archive/2026-02/_INDEX.md` 업데이트
stock-discrepancy 항목 추가

### Step 4: `.pdca-status.json` 업데이트
stock-discrepancy → `phase: "archived"` + summary 형태로 변환:
```json
{
  "stock-discrepancy": {
    "phase": "archived",
    "matchRate": 100,
    "iterationCount": 0,
    "startedAt": "2026-02-23T00:00:00Z",
    "archivedAt": "2026-02-23T...",
    "archivedTo": "docs/archive/2026-02/stock-discrepancy/"
  }
}
```

## 검증
- 아카이브 폴더에 3개 파일 존재 확인
- 원본 위치에서 파일 삭제 확인
- .pdca-status.json phase = "archived" 확인
