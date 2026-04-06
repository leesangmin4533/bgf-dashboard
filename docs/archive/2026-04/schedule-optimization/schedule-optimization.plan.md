# Plan: schedule-optimization

> 7시 메인 스케줄 제외, BGF 사이트 접속 스케줄 전체 최적화 (4건)

## 최적화 항목

### OPT-0: 정밀폐기 confirm/pre_collect 경량화
- **문제**: 하루 8회(4시간대×2) `run_optimized` 풀 수집 → 목적(폐기전표)에 불필요한 Phase 실행
- **해결**: `run_optimized(collect_only=...)` 파라미터로 Phase 선별 실행
- **절감**: ~50분/일

### OPT-1: 00:00 + 01:00 세션 통합
- **문제**: 연속 2회 BGF 로그인 (00:00 발주단위 + 01:00 상품상세), 같은 계정·Home 화면
- **해결**: 단일 세션으로 통합, 01:00 스케줄 제거
- **절감**: 로그인 1회 + ChromeDriver 1회

### OPT-2: 11:00 벌크 수집 조건부 실행
- **문제**: 00:00 상품상세 수집과 대상 겹침 (유통기한 NULL 등)
- **해결**: 수집 대상 0건이면 Selenium 생략
- **절감**: 01:00 성공 시 ~3-10분×4매장

### OPT-3: 03:00(수) 재고 검증 병렬화
- **문제**: 매장 순차 실행 (3매장×~4분 = ~12분)
- **해결**: `_run_task` 패턴으로 매장별 병렬 전환
- **절감**: ~12분→~5분

## 변경 파일

| 파일 | OPT | 변경 |
|------|:---:|------|
| `src/scheduler/daily_job.py` | 0 | `collect_only` 파라미터 + Phase 분기 |
| `src/scheduler/phases/collection.py` | 0 | 로그인 전용 경로 + Phase 게이팅 |
| `run_scheduler.py` | 0,1,2,3 | confirm/pre_collect 경량화, 통합 wrapper, 조건부 벌크, 병렬 검증 |

## 구현 순서
1. OPT-0 (daily_job.py + collection.py + run_scheduler.py)
2. OPT-1 (run_scheduler.py 통합 wrapper + 스케줄 변경)
3. OPT-2 (run_scheduler.py bulk_collect 조건부)
4. OPT-3 (run_scheduler.py inventory_verify 병렬화)
