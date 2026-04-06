# Report: schedule-optimization

## 요약
7시 메인 스케줄 제외, BGF 사이트 접속 스케줄 4건 최적화.

## 성과

| OPT | 항목 | 절감 |
|:---:|------|------|
| 0 | 정밀폐기 confirm/pre_collect 경량화 | ~50분/일 (8회 풀수집 → 선별수집) |
| 1 | 00:00+01:00 세션 통합 | BGF 로그인 1회 + ChromeDriver 1회/일 |
| 2 | 11:00 벌크 수집 조건부 | ~3-10분×4매장/일 (대상 0건 시 스킵) |
| 3 | 03:00(수) 재고 검증 병렬화 | ~12분→~5분 (순차→병렬) |

## 구현 내용

### OPT-0: collect_only 파라미터
- `run_optimized(collect_only=["waste_slip"])` → Phase 1.15+1.16만
- `run_optimized(collect_only=["sales","waste_slip"])` → Phase 1.0+1.15+1.16
- `_full` 가드 + `_SkipPhase` 패턴으로 Phase 게이팅

### OPT-1: 야간 통합 수집
- `consolidated_nightly_collect_wrapper`: Phase A(STBJ070) + Phase B(CallItemDetailPopup) 단일 세션
- 01:00 스케줄 제거, 기존 wrapper는 CLI용 보존

### OPT-2: 벌크 조건부
- `get_target_items(force=False)` 사전 체크 → 0건이면 Selenium 미진입

### OPT-3: 재고 검증 병렬화
- `_run_task` 패턴 + `run_verification_single` 매장별 병렬
- 완료 후 `_write_excel` 통합 엑셀

## 테스트
- 320건 통과, 0건 신규 실패
- Match Rate: 100%
