# Report: eval-cycle-verdict

> NORMAL_ORDER 판정 기준 개선 완료 보고서

## 1. 요약

| 항목 | 값 |
|------|-----|
| 기능명 | eval-cycle-verdict |
| 시작일 | 2026-03-02 |
| 완료일 | 2026-03-02 |
| Match Rate | **97%** |
| 테스트 | **2936개 전체 통과** (기존 2904 + 신규 32) |
| 수정 파일 | 3개 |
| 신규 파일 | 1개 (테스트) |

## 2. 문제 → 해결

### Before
- NORMAL_ORDER 적중률 **7.3%** (30일 누적 1,105건)
- 판정 기준: `actual_sold > 0` (당일 판매 필수)
- 저회전 상품 (일평균 < 1.0): 판매=0이 정상인데 "과소/과잉" 판정
- 행사 최소 진열 미반영, 안전재고 미반영

### After
- 3계층 판정 로직:
  1. **푸드류 제외** (001~005, 012): 기존 로직 유지
  2. **저회전** (daily_avg < 1.0): 판매주기 기반 (cycle = ceil(1/avg), 최대 7일)
  3. **고회전** (daily_avg >= 1.0): 재고 유지 기반 (품절 없으면 적중)
- 최소 진열: 행사 1+1→2개, 2+1→3개, 비행사→2개

## 3. 수정 파일

| 파일 | 변경 내용 |
|------|----------|
| `src/settings/constants.py` | `MIN_DISPLAY_QTY=2`, `EVAL_CYCLE_MAX_DAYS=7`, `LOW_TURNOVER_THRESHOLD=1.0` 추가 |
| `src/prediction/eval_calibrator.py` | `_judge_normal_order()`, `_get_min_display_qty()`, `_get_recent_sales_sum()` 신규, `_judge_outcome()` NORMAL_ORDER 분기 위임 |
| `src/infrastructure/database/repos/eval_outcome_repo.py` | `get_by_item_date_range()` 메서드 추가 |
| `tests/test_eval_cycle_verdict.py` | **32개 테스트** (푸드제외 7, 저회전주기 6, 고회전 4, 최소진열 9, 엣지 6) |

## 4. 설계 적합도

| 카테고리 | 점수 |
|----------|------|
| Design Match | 97% |
| Architecture Compliance | 100% |
| Convention Compliance | 100% |
| Test Coverage | 100% (32/20) |

### Minor Gaps (코드 변경 불필요)
1. `NORMAL_ORDER_EXCLUDE_MID_CDS` 별도 상수 대신 `FOOD_CATEGORIES` 직접 사용 (기능 동일)
2. 최소진열 체크에 방어적 가드 추가 (설계보다 안전)
3. 고회전 3분기→2분기 단순화 (논리 동일)

## 5. 예상 효과

| 상품 유형 | 현재 적중률 | 예상 적중률 |
|-----------|-----------|-----------|
| 저회전 (avg<1.0) | ~10% | **50~70%** |
| 고회전 (avg>=1.0) | ~25% | **60~80%** |
| **NORMAL_ORDER 전체** | **7.3%** | **55~75%** |

## 6. 리스크 평가

| 리스크 | 상태 |
|--------|------|
| 기존 테스트 호환 | 2936개 전체 통과 |
| 푸드류 영향 | 없음 (제외 처리) |
| DB 성능 | eval_outcomes 인덱스 활용, 최대 7일 룩백 |
| 자동보정 충돌 | 없음 (판정 기준만 변경) |
