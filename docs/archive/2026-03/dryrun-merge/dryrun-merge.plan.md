# Plan: dryrun-merge

## 개요
3개의 드라이런 스크립트를 `run_full_flow.py` 하나로 병합하고 나머지 2개 파일을 삭제한다.

## 현황 (AS-IS)
| 파일 | 역할 | 상태 |
|------|------|------|
| `scripts/run_full_flow.py` | 메인 진입점 (로그인→수집→발주→Excel) | Active |
| `scripts/export_dryrun_excel.py` | 29컬럼 Excel 생성 (`create_dryrun_excel`) | Active (import 전용) |
| `scripts/dry_order.py` | 구 20컬럼 리포트 (deprecated) | Dead code |

## 목표 (TO-BE)
- `run_full_flow.py` 하나에 모든 기능 통합
- `dry_order.py`, `export_dryrun_excel.py` 삭제
- 기존 CLI 인터페이스 100% 유지

## 의존성 분석
```
run_full_flow.py
  └─ from scripts.export_dryrun_excel import create_dryrun_excel   (Step 5 Excel)
  └─ from scripts.export_dryrun_excel import run_dryrun_and_export (--export-excel)

dry_order.py       → 외부 import 없음 (삭제 안전)
export_dryrun_excel.py → run_full_flow.py에서만 import (이동 후 삭제 안전)
```

## 이동 대상 함수 (export_dryrun_excel.py → run_full_flow.py)
1. **상수/설정**: SECTION_A~E, ALL_SECTIONS, SECTION_STYLES, COLUMN_DESCRIPTIONS, COL_WIDTHS, FLOAT_COLS, INT_COLS, TOTAL_COLS
2. **헬퍼**: `_safe_get()`, `_compute_bgf_fields()`, `_get_cell_value()`
3. **Excel 생성**: `create_dryrun_excel()`
4. **요약 시트**: `_create_summary_sheet()`
5. **오프라인 드라이런**: `run_dryrun_and_export()`

## 삭제 대상
- `scripts/dry_order.py` (전체)
- `scripts/export_dryrun_excel.py` (전체)

## CLI 인터페이스 (변경 없음)
```bash
# 온라인 드라이런 (로그인 + 발주 시뮬 + Excel)
python scripts/run_full_flow.py --no-collect --max-items 999

# 오프라인 드라이런 (DB만, Excel만)
python scripts/run_full_flow.py --export-excel

# 실제 발주
python scripts/run_full_flow.py --run
```

## 검증 기준
- [ ] `python scripts/run_full_flow.py --export-excel` 정상 실행
- [ ] `python scripts/run_full_flow.py --no-collect --max-items 3` 정상 실행 (Excel 29컬럼)
- [ ] `dry_order.py`, `export_dryrun_excel.py` 삭제 후 import 에러 없음
- [ ] 기존 테스트 전부 통과

## 리스크
- 낮음: 코드 이동만, 로직 변경 없음
- 파일 크기 증가 (~400줄 추가) → 수용 가능
