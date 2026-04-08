# PDCA Report — waste-lightweight-46704-missing

**기간**: 2026-04-08 (Plan → Do → Check → Report 동일일 완료)
**상태**: WATCHING (04-09 운영 검증 대기)
**Match Rate**: 100%
**커밋**: `b5d458a` (fix), `22dee96` (docs)

## 요약

46704 매장만 폐기 검증 보고서(`waste_verification_46704_{date}.txt`)가 정밀폐기 경량화 세션(10:11)에서 누락되고 13:52에 지연 생성되던 문제를 **가드 완화 + 4지점 로그 가시화**로 해결.

## 배경

- 04-07/04-08 이틀 연속 46704만 10:11 세션 파일 누락
- 다른 3매장(46513·47863·49965)은 10:10:44~10:11:34 정상 생성
- `ops_metrics` 23:55 자동 감지 지표 `verification_log_files_missing` 이 **false-positive P3 알림** 을 이틀 발동

## 원인

`logs/bgf_auto.log` 타임라인 추적으로 확정:

1. **Silent exception**: `waste_slip_collector.collect_waste_slips`의 외부 `except`가 `traceback.print_exc()`만 호출 → 파일 로그에 stack trace 안 남고 `{"success": False}` 반환
2. **가드 커플링**: `collection.py:322` `if waste_slip_stats.get("success"):` 가 탈락하여 DB 기반 `verify_date_deep`까지 함께 스킵 → Repository에 저장된 슬립조차 검증되지 않음
3. **지연 복구**: 13:52 재실행 세션에서 우연히 수집 성공 → 그제서야 파일 생성

증거:
- `run=47693385` (10:10:48, 46704, lightweight): 수집 "0건" → Phase 1.15 완료 1.1초 → VerifyDeep/reporter 라인 없음
- `run=011654de` (13:52:04, 46704): 수집 "2건" → VerifyDeep + 파일 생성 ✅

## 수정

### 핵심 설계 변경
**"수집 성공"과 "DB 기반 검증"의 분리**. Repository를 소스 오브 트루스로 인정하고 수집 실패여도 검증 경로 독립 실행.

### 파일별 변경
| 파일 | 변경 |
|---|---|
| `src/scheduler/phases/collection.py` | 가드 `success → is not None` 완화, 수집 결과 success/fail 분기 로깅(store_id 포함) |
| `src/collectors/waste_slip_collector.py` | `traceback.print_exc()` → `logger.error(exc_info=True)`, store/date 포함 |
| `src/report/waste_verification_reporter.py` | try/except에 `exc_info=True`, store/date 포함 |
| `src/application/services/waste_verification_service.py` | 2곳 `exc_info=True`, `[VerifyDeep]` INFO 라인에 store_id 명시 |

### 코드 볼륨
- 5 files changed, 161 insertions(+), 9 deletions(-)

## 검증

### 즉시 충족 (코드 확정)
- ✅ AC3: `[VerifyDeep]` 라인에 store_id 명시
- ✅ AC4: reporter try/except stack trace 출력

### 운영 검증 대기
- ⏳ AC1: 04-09 정밀폐기 세션 3회(10:00/14:00/22:00) 4매장 정시 생성
- ⏳ AC2: 04-09 23:55 `ops_metrics` false-positive 재발 0건
- ⏳ AC5: 2주 연속(04-09 ~ 04-22) 재발 0건

### 전제 조건
운영자 수동 조치 필요: **`scripts/start_scheduler_loop.bat` 재기동** — 메모리 활성 이슈 `scheduler 모듈 캐시 — 코드 fix 무력화`에 따라 재기동 없이는 본 수정이 활성화되지 않을 수 있음.

## 교훈

1. **`traceback.print_exc()`는 파일 로그에 안 남는다** — 프로젝트 전역 `logger.error(..., exc_info=True)` 패턴으로 통일 필요
2. **가드 커플링 주의** — "선행 단계 성공"을 "후행 단계 실행 가능"의 전제로 삼을 때, 후행 단계가 다른 데이터 소스(예: DB)에서 독립적으로 동작 가능한지 재검토
3. **Silent exception은 false-positive 알림을 낳는다** — 자동 감지 지표의 신뢰도는 "진실된 실패 신호"에 의존. 예외를 삼키는 순간 지표가 거짓말을 시작

## 관련 이슈

- `docs/05-issues/expiry-tracking.md#46704-폐기-검증-보고서-정시-생성-실패` (WATCHING, 04-08)
- `docs/05-issues/scheduling.md#scheduler 모듈 캐시` (관련, 재기동 의존)
- `docs/05-issues/scheduling.md#verification_log_files_missing` (false-positive 원인 제거)

## 지표

| 지표 | Before | After (예상) |
|---|---|---|
| 46704 10:11 파일 생성 | 0/2일 | 3/3 세션/일 |
| ops_metrics false-positive | 2/2일 | 0/14일 |
| silent exception stack trace | 0건 로그 | 실패 시 100% 로그 |
| `[VerifyDeep]` 매장 식별성 | run_id 역추적 필요 | INFO 라인 즉시 식별 |

## Phase 진행

```
[Plan] ✅ → [Design] ⏭️(skip, Plan 상세) → [Do] ✅ → [Check] ✅(100%) → [Report] ✅
```

**완료**: 2026-04-08
