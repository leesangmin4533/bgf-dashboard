# Completion Report: dryrun-merge

## 개요
3개의 드라이런 스크립트를 `run_full_flow.py` 하나로 병합하고 나머지 2개 파일을 삭제.

## PDCA 요약
| 단계 | 상태 | 비고 |
|------|------|------|
| Plan | 완료 | 의존성 분석, 삭제 안전성 확인 |
| Design | 완료 | 함수 이동 순서, import 수정 설계 |
| Do | 완료 | 코드 이동 + 파일 삭제 |
| Check | 완료 | Match Rate **100%** (14/14) |

## 변경 파일
| 파일 | 변경 | 내용 |
|------|------|------|
| `scripts/run_full_flow.py` | 수정 | Excel 생성 코드 통합 (311행 → ~700행) |
| `scripts/dry_order.py` | **삭제** | deprecated, 외부 import 0건 |
| `scripts/export_dryrun_excel.py` | **삭제** | 코드가 run_full_flow.py로 이동 |

## 이동된 함수 (export_dryrun_excel.py → run_full_flow.py)
1. 상수: SECTION_A~E, SECTION_STYLES, COLUMN_DESCRIPTIONS, COL_WIDTHS, FLOAT_COLS, INT_COLS
2. `_safe_get()`, `_compute_bgf_fields()`, `_get_cell_value()`
3. `_create_summary_sheet()`
4. `create_dryrun_excel()` — 29컬럼 3행헤더 Excel 생성
5. `run_dryrun_and_export()` — 오프라인 드라이런

## CLI 호환성
```bash
# 모든 기존 CLI 100% 유지
python scripts/run_full_flow.py --no-collect --max-items 999     # 온라인 드라이런
python scripts/run_full_flow.py --export-excel                    # 오프라인 드라이런
python scripts/run_full_flow.py --run                             # 실제 발주
python scripts/run_full_flow.py --no-report                       # Excel 스킵
```

## 검증 결과
- 구문 검증: PASS
- `--export-excel` 실행: 9개 상품, Excel 생성 성공
- 38개 기존 테스트: 전부 PASS
- 삭제 파일 import 잔존: 0건

## 효과
- 파일 3개 → 1개 (관리 포인트 67% 감소)
- 코드 중복 제거 (dry_order.py의 20컬럼 Excel 완전 폐기)
- import 경로 단순화 (외부 스크립트 참조 → 같은 파일 내 함수 호출)
