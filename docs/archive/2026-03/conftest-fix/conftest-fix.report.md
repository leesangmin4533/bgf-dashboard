# PDCA Report: conftest-fix

## 개요

| 항목 | 내용 |
|------|------|
| Feature | conftest-fix |
| 시작일 | 2026-03-01 |
| Match Rate | 100% |
| 수정 파일 | 4개 (모두 테스트 파일) |
| 테스트 | 15개 실패 → **0개 실패** (2838 전부 통과) |

## 근본 원인 3가지 해결

### 1. conftest.py + test_improved_predictor.py product_details 컬럼 누락 (10개 복구)
- `large_cd TEXT`, `small_cd TEXT`, `class_nm TEXT` 추가 (conftest + test_improved_predictor)
- `sell_price INTEGER`, `margin_rate REAL`, `lead_time_days INTEGER` 추가 (conftest만)

### 2. _substitution_detector 속성 미초기화 (4개 복구)
- `test_prediction_redesign_integration.py`: _make_predictor에 `_substitution_detector = None` 추가
- `test_pending_cross_validation.py`: predictor stub에 `_substitution_detector = None` 추가

### 3. daily_sales 컬럼명 불일치 (1개 복구)
- `test_new_product_lifecycle.py`: `sell_qty` → `sale_qty` (프로덕션 스키마 일치)

## 결과
- 프로덕션 코드 변경: 0건
- 테스트 코드 변경: 4파일
- 전체 테스트: **2838 passed, 0 failed** (이전: 2800+ passed, 15 failed)
