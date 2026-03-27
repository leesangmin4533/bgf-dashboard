# Design: dryrun-merge

## 참조
- Plan: `docs/01-plan/features/dryrun-merge.plan.md`

## 파일 구조 (병합 후)

### `scripts/run_full_flow.py` — 최종 구조
```
[기존] 임포트 + UTF-8 래핑 (1~31행)
[기존] from src.* 임포트 (32~36행)

[NEW]  ═══ Excel 생성 관련 상수 ═══
       SECTION_A ~ SECTION_E (5개 섹션 정의)
       ALL_SECTIONS, SECTION_STYLES
       COLUMN_DESCRIPTIONS (29개)
       COL_WIDTHS (29개)
       FLOAT_COLS, INT_COLS, TOTAL_COLS

[NEW]  ═══ Excel 헬퍼 함수 ═══
       _safe_get(item, key, default)
       _compute_bgf_fields(item) → dict
       _get_cell_value(item, key, computed)

[NEW]  ═══ Excel 생성 함수 ═══
       _create_summary_sheet(wb, order_list)
       create_dryrun_excel(order_list, output_path, delivery_date, store_id) → str

[NEW]  ═══ 오프라인 드라이런 ═══
       run_dryrun_and_export(store_id, max_items) → str

[기존] ═══ 메인 플로우 ═══
       run_full_flow(dry_run, collect_sales, ...) → dict
         Step 5에서 create_dryrun_excel() 직접 호출 (import 제거)

[기존] ═══ CLI ═══
       if __name__ == "__main__":
         --export-excel → run_dryrun_and_export() 직접 호출 (import 제거)
```

## 수정 상세

### 1. 임포트 추가 (run_full_flow.py 상단)
```python
import math                          # _compute_bgf_fields에서 사용
from collections import defaultdict, Counter  # 요약 시트에서 사용
```
기존 `from collections import defaultdict`는 없으므로 신규 추가.
`openpyxl` 임포트는 함수 내부(lazy)로 유지.

### 2. Step 5 Excel 생성 변경
```python
# AS-IS
from scripts.export_dryrun_excel import create_dryrun_excel

# TO-BE (같은 파일 내 함수 직접 호출)
xlsx_path = create_dryrun_excel(...)
```

### 3. --export-excel 변경
```python
# AS-IS
from scripts.export_dryrun_excel import run_dryrun_and_export

# TO-BE
path = run_dryrun_and_export(...)
```

### 4. run_dryrun_and_export() 내부 임포트 조정
- `from src.order.auto_order import AutoOrderSystem` — 이미 run_full_flow.py 상단에 없으므로 함수 내부 lazy import 유지
- `from src.infrastructure.database.repos import RealtimeInventoryRepository` — 함수 내부 유지

## 삭제 파일
1. `scripts/dry_order.py` — 완전 삭제
2. `scripts/export_dryrun_excel.py` — 완전 삭제

## 구현 순서
1. `export_dryrun_excel.py`에서 상수+함수 복사 → `run_full_flow.py`에 삽입
2. `run_full_flow.py` 내부 import 경로 수정 (외부→내부)
3. 구문 검증 (`py_compile`)
4. `dry_order.py` 삭제
5. `export_dryrun_excel.py` 삭제
6. import 에러 검증
7. `--export-excel` 실행 테스트
