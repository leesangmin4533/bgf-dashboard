# waste-lightweight-46704-missing

> **Plan**: 46704 매장 폐기 검증 보고서가 정밀폐기 경량화 세션(10:00/14:00/22:00)에서 정시 생성되지 못하고 당일 오후 13:52에 지연 생성되는 문제 해결

## 1. 배경 (Context)

### 발생 현상
- 4매장 중 **46704만** `waste_verification_46704_{date}.txt` 가 정시(10:11)에 생성되지 않고 **13:52에 지연 생성**됨
- 04-07, 04-08 이틀 연속 동일 패턴 재현
- 다른 3매장(46513, 47863, 49965)은 10:10:44~10:11:34 구간에 순차 정상 생성
- `ops_metrics(운영 자동 감지 지표)` 23:55 실행이 "어제 파일 없음"으로 오인하고 **P3 `verification_log_files_missing` 알림을 false-positive** 로 발생시킴

### 타임라인 증거 (logs/bgf_auto.log, 04-08)
| 시각 | run_id | store | 수집 결과 | 보고서 저장 |
|---|---|---|---|---|
| 10:10:44 | 56d99166 | 46513 | 8건 | ✅ |
| 10:10:53 | ab1ffca5 | 47863 | 10건 | ✅ |
| 10:11:?? | ae474637 | 49965 | 8건 | ✅ |
| **10:11** | — | **46704** | **배치에 없음** | ❌ |
| 13:52:04 | 011654de | 46704 | 2건 | ✅ (지연) |

모든 run이 `Lightweight mode: collect_only=['waste_slip']` — 즉 `expiry_confirm_wrapper` (`run_scheduler.py:509`) 경로로 도는 정밀폐기 3단계.

### 관련 기존 이슈
- 메모리 활성 이슈: `46704 04-07 검증 로그 파일 누락` (P3, `expiry-tracking.md`)
- 메모리 활성 이슈: `scheduler 모듈 캐시 — 코드 fix 무력화` (동일 매장 영향권)
- 메모리 04-08 신설 지표: `verification_log_files_missing` (이 버그로 false-positive 발동)

## 2. 목표 (Goal)

1. 46704의 폐기 검증 보고서가 **다른 3매장과 동일한 시각(정밀폐기 경량화 세션)** 에 정상 생성되도록 복구
2. `ops_metrics` false-positive 알림 제거
3. 지연/실패 시 **stack trace 가시화** (현재 try/except가 warning만 남기고 예외 삼킴)
4. 회귀 방지: 매장별 성공/실패를 `[VerifyDeep]` 라인에 `store_id` 명시

## 3. 범위 (Scope)

### 포함
- `expiry_confirm_wrapper` → `_run_task` → `MultiStoreRunner` 경로에서 46704가 왜 배치에서 빠지는지 확정
- `waste_verification_reporter.py:497` `logger.warning` → `exc_info=True` 추가
- `waste_verification_service.py:238` 동일 조치
- `[VerifyDeep]` INFO 로그에 `store_id` 포함
- `ops_metrics.collect_system()` 의 23:55 검사 타이밍을 08:00으로 이동(retry 반영 후 판정) — **선택**

### 제외
- `src/analysis/waste_report.py` 레거시 non-suffix 경로 전면 제거 (별도 작업)
- `scheduler 모듈 캐시` 이슈(P1) 의 근본 해결 — 본 작업은 영향 확인만
- `ops_metrics` 전체 지표 구조 변경

## 4. 원인 가설 우선순위

| 순위 | 가설 | 확정 방법 |
|---|---|---|
| **A** | `_run_task`의 매장 순회가 46704에서 Selenium 드라이버 세션 생성 실패 또는 로그인 실패로 조용히 스킵 | 10:10~10:11 구간 `grep "46704"` 에서 에러/경고 라인 확인 |
| B | 정밀폐기 경량화 세션의 `active_store_ids` 리스트에 46704가 누락(config 드리프트) | `config/stores.json` + `_run_task` 진입점의 매장 리스트 인자 확인 |
| C | 46704만 `waste_slip_collector`의 날짜 필터에서 0건 반환 → `if waste_slip_stats.get("success"):` 가드 탈락 | logs의 46704 collect_waste_slips 결과 직접 확인 |
| D | `generate_daily_report` 내부 silent fail (reporter 내 예외를 warning만 남기고 None return) | exc_info=True 추가 후 04-09 재측정 |

## 5. 작업 항목 (Tasks)

### Phase 1 — 원인 확정 (조사, 코드 변경 없음)
- [ ] T1: 10:10~10:11 구간에서 46704가 정밀폐기 배치에 들어가긴 했는지 확인 (grep `10:1[01].*46704`)
- [ ] T2: 46704의 `Optimized flow started | store=46704 | session=XXX | Lightweight mode` 라인 유무 확인
- [ ] T3: `_run_task` (run_scheduler.py) 코드 읽어 매장 순회/드라이버 관리 방식 파악
- [ ] T4: `config/stores.json` 또는 `active_store_ids` 설정에서 46704 상태 확인
- [ ] T5: 가설 A/B/C/D 중 하나로 원인 확정

### Phase 2 — 로그 가시화 개선 (저위험 선행 수정)
- [ ] T6: `waste_verification_reporter.py:497` `logger.warning(..., exc_info=True)` 로 변경
- [ ] T7: `waste_verification_service.py:238` 동일 조치
- [ ] T8: `[VerifyDeep]` INFO 로그 포맷에 `store_id={self.store_id}` 추가

### Phase 3 — 근본 원인 수정
- [ ] T9: Phase 1에서 확정된 원인에 따라 수정 (가설별 대응 분기)
  - 가설 A: 드라이버 세션 재시도 로직 또는 실패 시 명시적 에러 전파
  - 가설 B: `config/stores.json` 복구 + config 드리프트 방지 검증 추가
  - 가설 C: 0건도 reporter 저장하도록 가드 완화 (`if waste_slip_stats is not None`)
  - 가설 D: reporter 내부 예외 원인 수정

### Phase 4 — 검증
- [ ] T10: `scripts/start_scheduler_loop.bat` 재기동 (모듈 캐시 배제)
- [ ] T11: 04-09 10:00/14:00/22:00 정밀폐기 세션에서 46704 포함 4매장 파일 생성 확인
- [ ] T12: 04-09 23:55 `ops_metrics` false-positive 재발 없음 확인
- [ ] T13: `docs/05-issues/expiry-tracking.md` 이슈 체인 갱신 (OPEN → RESOLVED)

## 6. 성공 기준 (Acceptance Criteria)

- [ ] AC1: 04-09 정밀폐기 경량화 세션 **3회 모두** 46704 포함 4매장 보고서 파일이 정시(세션 시작 ±2분) 생성
- [ ] AC2: 04-09 23:55 `ops_metrics` 실행에서 `verification_log_files_missing = 0`
- [ ] AC3: 로그에서 `[VerifyDeep]` 라인에 `store_id` 식별 가능 (run_id 역추적 불필요)
- [ ] AC4: `waste_verification_reporter.py` 의 try/except가 실패 시 stack trace 출력
- [ ] AC5: 2주 연속(04-09~04-22) 동일 패턴 재발 0건

## 7. 리스크 & 완화

| 리스크 | 영향 | 완화 |
|---|---|---|
| 원인이 `scheduler 모듈 캐시` P1 이슈와 얽혀 있음 | 본 작업 수정 후에도 재발 가능 | T10에서 재기동 후 테스트, 필요 시 P1 작업과 통합 |
| 0건 가드 완화 시 빈 파일 누적 | 운영 로그 오염 | 0건은 헤더만 쓰고 `[NO_DATA]` 마커 명시 |
| `exc_info=True` 로 로그 볼륨 증가 | 디스크 | 실패 경로만 stack trace — 정상 경로 영향 없음 |

## 8. 참고 파일

- `src/scheduler/phases/collection.py:302~351` — Phase 1.15 폐기 전표 수집 + 검증
- `src/application/services/waste_verification_service.py:191~258` — verify_date_deep
- `src/report/waste_verification_reporter.py:434~499` — generate_daily_report, 파일명 생성
- `run_scheduler.py:509~580` — expiry_confirm_wrapper (정밀폐기 3단계)
- `src/analysis/ops_metrics.py:42~68` — verification_log_files_missing 감지
- `logs/bgf_auto.log` 라인 237727~243522 — 04-08 타임라인 증거
- 메모리: `expiry-confirm-lightweight` (04-06), `schedule-optimization` (04-07), `ops-metrics-monitor-extension` (04-08)

## 9. 예상 난이도 / 일정

- Phase 1 (조사): 15분
- Phase 2 (로그 가시화): 10분
- Phase 3 (근본 수정): 가설별 상이, 30분~2시간
- Phase 4 (검증): 04-09 하루 대기

**총 소요**: 1일 (검증 포함)
