# 스케줄링 이슈 체인

> 최종 갱신: 2026-04-11
> 현재 상태: scheduler-job-guard 구현 중 + scheduler-wrapper-restart RESOLVED

---

## [OPEN] 07시 스케줄 인터넷 장애 + 중복 실행 (04-11)

**문제**: 07:00 daily_order 실행 중 인터넷 장애 발생 → 오류 종료. 
수동 재실행 시 기존 프로세스와 중복 실행 → 동일 매장 중복 로그인/발주 위험.

**근본 원인**:
1. 작업 수준 lock 없음 — scheduler.lock은 프로세스 수준이라 같은 프로세스 내 job 중복 못 막음
2. 실패 시 재시도 없음 — 인터넷 복구돼도 다음날 07:00까지 발주 불가
3. 매장별 완료 추적 없음 — 재시도 시 성공한 매장도 다시 실행
4. Phase 체크포인트 없음 — 수집 완료 후 발주 실패 시 수집부터 다시

**3단계 해결 계획**:
- 1단계: 작업 락 (중복 실행 방지) — `job_guard.py`
- 2단계: 실패 매장 15분 재시도 (3회) — `job_retry.py`
- 3단계: Phase 체크포인트 (실패 Phase부터 재개) — 향후

Issue-Chain: scheduling#daily-order-retry-guard

---

## [RESOLVED] scheduler-wrapper-restart (04-07 18:58 ~ 20:08)

**문제**: 04-07 18:58 SrcWatcher가 src 변경 감지 → 18:59:05 graceful exit(0) → wrapper 미가동으로 약 1시간 스케줄러 완전 중단. 04-07 23:30~23:58 야간 잡 미실행 위험.

**원인** (2단):
1. 사용자가 `python run_scheduler.py` 직접 실행 → wrapper(`scripts/start_scheduler_loop.bat`) 우회 → exit 0 시 재기동 주체 없음
2. wrapper bat 자체가 UTF-8 저장이지만 `chcp 65001` 미선언 → cmd 기본 cp949가 한글 주석/echo를 명령으로 오인 (`'hon' 은(는) 내부 또는 외부 명령... 아닙니다`)

### 시도 1: bat에 chcp 65001 추가 + 한글 주석 영문화 (04-07)
- **왜**: cp949 fallback에서도 안전하게 동작하도록
- **변경**: `scripts/start_scheduler_loop.bat` 첫 줄 `chcp 65001 >nul`, REM/echo 영문화
- **결과**: 더블클릭 시 한글 깨짐 0건. 본문 loop 로직 무변경.

### 운영 복구
- 20:03 직접 python 백그라운드 임시 기동 (PID 39264)
- 20:08 fix 적용 후 새 백그라운드 기동 (PID 39120)
- heartbeat 정상 갱신 확인

### 교훈
- **Windows bat은 UTF-8 저장 시 반드시 `chcp 65001 >nul`로 시작** — 한글 주석/echo가 명령으로 오인되는 것을 방지
- **wrapper 우회 금지**: SrcWatcher의 auto-reload는 wrapper 재기동 사슬을 전제. 직접 python 실행은 단발성으로만 사용
- 운영 매뉴얼(CLAUDE.md 빠른 시작)에 wrapper 사용을 명시적으로 강제

### 해결
- 검증:
  - [x] bat 더블클릭 시 한글 에러 0건
  - [ ] 다음 src 변경 시 wrapper가 5초 내 재기동 (실측 대기)
  - [ ] 사용자가 시작 프로그램 등록 (수동 작업)

---

## [WATCHING] action_proposals v70 컬럼 매장 DB 미적용 (04-05 ~ )

**문제**: v70 마이그레이션(executed_at, verified_at, verified_result)이 common.db SCHEMA_MIGRATIONS에만 있고 _STORE_COLUMN_PATCHES에 누락 → 매장 DB에 미적용
**영향**: ExecutionVerifier.verify_previous_day()가 매일 4매장에서 "no such column: executed_at" 오류 → 자전 시스템 4고리(검증) 전체 무력화
**설계 의도**: 매장 DB 스키마 변경은 반드시 _STORE_COLUMN_PATCHES에도 동기화. SCHEMA_MIGRATIONS(common.db)와 이중 관리 필요.

### 시도 1: _STORE_COLUMN_PATCHES에 3개 컬럼 ADD 추가 + 기존 DB 직접 보정 (2bd24bf, 04-05)
- **왜**: 근본 원인이 _STORE_COLUMN_PATCHES 누락이므로 직접 추가
- **결과**: 4매장 DB 컬럼 추가 확인, 테스트 13개 통과

> 변경 내용은 `git show 2bd24bf`로 확인.

### 교훈
- **매장 DB 스키마 변경 시 2곳 동기화 필수**: `models.py SCHEMA_MIGRATIONS` + `schema.py _STORE_COLUMN_PATCHES`
- SCHEMA_MIGRATIONS은 common.db(레거시)에만 적용됨. 매장 store DB는 _STORE_COLUMN_PATCHES 경로로만 컬럼이 추가됨
- schema_version이 72까지 올라가도 실제 컬럼이 없을 수 있음 (version 숫자와 실제 스키마 불일치 가능)

### 해결: _STORE_COLUMN_PATCHES 동기화 (2bd24bf, 04-05)
- 검증:
  - [ ] 내일(04-06) 07:00 로그에서 [Verify] executed_at 오류 소멸 확인 (스케줄: eb6c54ca)

---

## [RESOLVED] claude-auto-respond Claude CLI 호출 실패 (04-06 ~ 04-09)

**문제**: 2026-04-06 23:58 스케줄(claude-auto-respond)이 `claude exit=1: (no stderr)`로 실패. stderr가 비어있어 원인 추적 불가.
**영향**: 이상 감지 시 L1 자동 원인 분석 무력화 → DailyChainReport에 분석 결과 누락 → 운영자가 수동 분석 필요
**설계 의도**: 이상 감지(23:55 OpsIssueDetector) → pending_issues.json 저장 → 23:58 ClaudeResponder가 claude -p 읽기전용 호출 → 분석 결과를 pending에 병합 → 00:02 DailyChainReport가 통합 발송

**재현 로그**:
```
2026-04-06 23:55:02 [OpsIssueDetector] pending_issues.json 저장 (5건)
2026-04-07 00:00:06 [AutoRespond] Claude 호출 실패: claude exit=1: (no stderr)
```

**가설 5가지**:
1. stdout에 에러 메시지 출력 (CLI가 stderr 대신 stdout 사용)
2. Windows 스케줄러 컨텍스트에서 `claude.cmd` shim PATH 미인식
3. `--allowed-tools "Read Grep Glob"` 플래그 형식 변경 (최신 CLI에서 콤마/반복 옵션 요구)
4. 비대화형 환경에서 OAuth 재인증 트리거
5. Windows cp949 ↔ UTF-8 prompt 인코딩 충돌

**관련 파일**: `src/infrastructure/claude_responder.py:92-118` `_call_claude()`

### 시도 1: 진단 로깅 강화 + max_turns 10→30 상향 (04-07)
- **왜**: 기존 코드는 stderr만 출력했는데 claude CLI는 stdout으로 에러 출력 → 원인 불명
- **조치**:
  1. `_call_claude()` stdout fallback + `_dump_failure_context()`로 debug 파일 덤프
  2. 재현 결과 실제 원인 = `Error: Reached max turns (10)` (분석 작업에 Read/Grep 다수 호출)
  3. `CLAUDE_AUTO_RESPOND_MAX_TURNS 10→30`, `TIMEOUT 300→600` 상향
- **결과**: ✓ 성공 — `data/auto_respond/2026-04-07_response.md` 생성 확인
- **실패 패턴**: (신규) stderr-only-error-reporting

### 시도 2: 04-07 23:58 야간 스케줄 자동 실행 — OAuth 토큰 만료 (04-08 검토)
- **왜**: 시도 1 수정(max_turns 상향) 후 첫 야간 자동 실행에서 다른 에러 발생
- **결과**: ✗ 실패 — `claude exit=1: OAuth token has expired` (401 인증 오류)
  - `debug_20260407_235835.log`: `returncode=1`, stdout에 401 에러 메시지 확인
  - 이는 코드 버그가 아닌 **Claude CLI OAuth 세션 만료** 문제 — 사용자 수동 재인증 필요
- **실패 패턴**: 인증 만료 (코드 수정으로 해결 불가, 사용자 액션 필요)

**조치 필요**: `claude auth login` 실행하여 OAuth 세션 갱신 → 이후 23:58 스케줄 자동 복구

### 교훈
- **서브프로세스 에러는 stdout/stderr 둘 다 확인**: claude CLI는 사용량/turn 제한 에러를 stdout으로 출력
- **max_turns는 실제 분석 복잡도 기준**: 읽기 전용 도구(Read/Grep/Glob)를 많이 쓰는 분석 작업은 10 turn으로 부족
- **진단 덤프는 실패 재현의 핵심**: stderr만 찍으면 1차 조사에서 헤맨다. cmd/cwd/env/stdout 전체 덤프 표준화 필요
- **OAuth 세션 만료는 별도 실패 유형**: max_turns 수정 후에도 OAuth 세션 단절로 재실패 가능 — 주기적 세션 상태 점검 필요

### 검증 체크포인트
- [x] 진단 로깅 강화 후 수동 재현 (04-07 13:25 debug_20260407_132510.log)
- [x] 원인 식별: max_turns 초과 (stdout만 출력)
- [x] 수정 후 재실행 성공 (04-07 13:29, 2026-04-07_response.md 생성)
- [x] 회귀 테스트 3개 추가 (TestCallClaudeFailureDiagnostics, 14/14 통과)
- [x] 04-07 23:58 야간 스케줄: OAuth 만료로 실패 (debug_20260407_235835.log 확인)
- [x] `claude auth login` 갱신 후 04-08 23:58 스케줄에서 자동 실행 성공 확인 (04-09 00:02 완료, duration 233.8초, anomaly_count 39, status=ok, output: 2026-04-09_response.md — 자정 이후 완료로 날짜 +1)

**관련 Plan**: [docs/01-plan/features/claude-respond-fix.plan.md](../01-plan/features/claude-respond-fix.plan.md)

**해결 (04-09)**: `claude auth login` 재인증 후 04-08 23:58 스케줄 자동 실행 성공. pipeline_status.json status=ok, duration=233.8s, anomaly_count=39. 신규 debug 로그 없음. max_turns=30 조정 + OAuth 세션 유지로 안정화.

---

## [WATCHING] ops_metrics waste_rate mid_cd 컬럼 부재 (04-07 ~ )

**문제**: `_waste_rate()` 쿼리가 `waste_slip_items.mid_cd` GROUP BY를 시도하지만 해당 컬럼 없음 (실제: `large_cd`만 존재). 4개 매장 전부 매일 23:55 `OperationalError: no such column: mid_cd` → `insufficient_data` 반환 → **K2 마일스톤 NO_DATA 지속**.
**영향**: K2 KPI 완전 마비, 폐기율 기반 이상 감지 불가, DailyChainReport K2 섹션 공백.
**설계 의도**: mid_cd별 카테고리 폐기율 집계는 products의 mid_cd를 단일 원천으로 사용해야 함 (waste_slip_items는 item_cd만 확실).

**발견 경로**: claude-auto-respond 분석 보고서 (2026-04-07) — 진단 로깅 강화 후 첫 자동 감지.

**관련 파일**: `src/analysis/ops_metrics.py:134-206` `_waste_rate()`

### 해결 방향
- **옵션 A (권장)**: `waste_slip_items` + `common.products` JOIN으로 mid_cd 도출
- 옵션 B: waste_slip_items에 mid_cd 컬럼 추가 (스키마+수집기+백필 필요, 과대)
- 옵션 C: large_cd로 집계 (K2 정의 변경, 부적절)

### 시도 1: common.products INNER JOIN + 매칭률 경고 (04-07)
- **왜**: waste_slip_items는 item_cd만 확실. products가 mid_cd 단일 원천. 스키마 변경 없이 1곳만 수정.
- **조치**:
  1. `DBRouter.get_store_connection_with_common()` 사용 (common.db ATTACH)
  2. 쿼리에 `JOIN common.products p ON wsi.item_cd = p.item_cd`, `GROUP BY p.mid_cd`
  3. 매칭률 경고 쿼리 추가 (5% 초과 시 신제품 동기화 경고)
- **결과**: ✓ 4매장 전부 성공 — mid_cd 001~005 폐기율 정상 집계. 17/17 테스트 통과.
- **실패 패턴**: (신규) schema-column-assumption

### 교훈
- **정규화 원칙**: mid_cd는 products가 단일 원천. 다른 테이블은 JOIN으로 얻는다
- **스키마 확인 먼저**: 쿼리 작성 전 `waste_slip_items` 실제 컬럼을 schema.py에서 확인했다면 처음부터 방지 가능
- **매칭률 경고 부수효과**: products에 없는 폐기 item_cd 발견 시 신제품 동기화 이슈 조기 감지

### 검증 체크포인트
- [x] _waste_rate() 4매장 전부 `categories` 반환 (04-07 13:58 수동 재현)
- [x] 회귀 테스트 3개 추가 (`test_ops_metrics_waste_rate.py`, 3/3 통과)
- [ ] 다음 23:55 OpsMetricsCollector 실행에서 `waste_rate 실패` 로그 소멸
- [ ] 다음 milestone_snapshots에서 K2 NO_DATA 탈출 (ACHIEVED/NOT_MET 전환)

**관련 Plan**: [docs/01-plan/features/ops-metrics-waste-query-fix.plan.md](../01-plan/features/ops-metrics-waste-query-fix.plan.md)

---

## [WATCHING] K4 expiry_time_mismatch 31일 NOT_MET — 식품 전용 재정의 (04-07 수정)

**문제**: K4 마일스톤이 31일 연속 NOT_MET. `check_expiry_time_mismatch`가 모든 카테고리의 OT vs IB 1일 초과 차이를 mismatch로 잡지만, 92.4%(7,691/8,328)가 비식품(담배/맥주/우산 등)이고 이 중 일부는 sentinel 값(2053-08, 2028-12)으로 9,997일 차이를 만든다. K4 의도(식품 폐기 정합성)와 측정 대상 불일치.
**영향**: K4 KPI가 가짜 alarm으로 31일 연속 실패 → 진짜 식품 폐기 사고를 가림. 마일스톤 단계3 ACHIEVED 차단.
**설계 의도**: K4 = 식품(도시락/김밥/빵 등)의 OT.expiry_time과 IB.expiry_date 정합성. 폐기 알림 시점 정확도 측정.

**4매장 mismatch 분포**:
| 분류 | 건수 | 비율 |
|---|---:|---:|
| 비식품 (072 담배, 049 맥주 등) | 7,691 | 92.4% |
| 식품 (001~005, 012) | 637 | 7.6% |
| sentinel (year≥2030) | 230 | 2.8% |

**식품 diff_days 분포 (637건)**:
- 1~3일: 75
- 3~7일: 143
- 7~30일: 312 (진짜 위험 신호)
- 30~365일: 145 (진짜 위험 신호)

### 해결 방향
- **D+C 조합**: 식품 카테고리(001~005, 012) 전용 + 임계값 1일 → 7일 완화
- 비식품 mismatch는 K4에서 제외, 별도 메트릭으로 분리 (후속)
- products JOIN 추가 (common.db ATTACH)

### 시도 1: 식품 전용 + 7일 임계값 (D+C 조합) (04-07)
- **왜**: K4 의도(식품 폐기 정합성)와 측정 대상(전체) 불일치, 비식품 92.4%가 노이즈
- **조치**:
  1. `check_expiry_time_mismatch` 커넥션 → `get_store_connection_with_common`
  2. JOIN common.products + `mid_cd IN ('001'~'005','012')` 필터
  3. 임계값 1일 → 7일
- **결과**: ✓ 4매장 mismatch 8,328 → **444건 (94.7% 감소)**, Plan 예상치(457)와 일치
- **실패 패턴**: (신규) kpi-intent-vs-measurement-mismatch

### 교훈
- **첫 5건 정렬 함정**: 가장 큰 차이값(sentinel 9997일)이 보였지만 2.8%에 불과. distribution + GROUP BY로 전체 그림 파악 필수
- **KPI 정의 시 측정 범위 명시**: K4 의도(식품)와 측정(전체) 불일치 → 진짜 신호 가림
- **임계값은 시작점**: 7일은 보수적, 1주 운영 후 재조정 가능

### 검증 체크포인트
- [x] check_expiry_time_mismatch 식품 전용 + 7일 임계값 (04-07)
- [x] 4매장 mismatch 합계 444건 (목표 100~500 범위)
- [x] 회귀 테스트 3/3 통과 (test_integrity_check_k4.py)
- [ ] 다음 milestone_snapshots K4 NOT_MET → ACHIEVED 전환

**관련 작업 (archive)**: docs/archive/2026-04/k4-non-food-sentinel-filter/

**관련 Plan**: [docs/01-plan/features/k4-non-food-sentinel-filter.plan.md](../01-plan/features/k4-non-food-sentinel-filter.plan.md)
**발견 경로**: claude-auto-respond 분석 보고서(2026-04-07) K4 PLANNED 지목 → 폐기 추적 점검 → K4 데이터 직접 조사

---

## [WATCHING] scheduler 모듈 캐시 — 코드 fix 무력화 (04-06 ~ 04-07 수정)

**문제**: long-running `run_scheduler.py` 프로세스가 sys.modules에 옛 모듈을 캐시. 코드 수정/커밋해도 수동 재시작 없이는 fix 미반영.
**영향**: 오늘(04-07) PDCA 4건(claude-respond, ops-metrics, d1-bgf, k4) 모두 동일 운영 이슈 보유. 04-06 bgf-collector-import-fix 1차 fix가 무력화돼 d1 작업에서 진짜 원인 디버깅에 시간 소모.
**설계 의도**: 코드 변경이 자동 반영되어야 운영 신뢰성 확보. 수동 재시작 의식 = 0이 목표.

### 해결 방향 (옵션 C 채택)
- file mtime watch 데몬 스레드 (60초 폴) → src/ 변경 감지 → graceful exit
- 외부 wrapper script (`run_scheduler_loop.ps1`)로 무한 재시작 루프
- 작업 실행 중에는 exit 보류 → 완료 후 exit
- import 에러 시 backoff (5s → 30s → 60s) + 1회 알림

### 시도 1: SrcWatcher + start_scheduler_loop.bat (04-07)
- **왜**: long-running scheduler 모듈 캐시는 운영 의식(수동 재시작)으로만 해소 가능 → 자동화로 제거
- **조치**:
  1. `src/infrastructure/scheduler/src_watcher.py` 신규 (mtime+size 시그니처 daemon)
  2. `run_scheduler.py` 무한 루프에 reload_event 체크 + sys.exit(0) 추가
  3. `scripts/start_scheduler_loop.bat` 외부 wrapper (exit code 분기 + backoff)
  4. CLAUDE.md 사용법 갱신
- **결과**: ✓ 5/5 회귀 테스트 통과, MVP 완성
- **실패 패턴**: (신규) long-running-process-cache

### 교훈
- 운영 의식 자동화는 fix 신뢰성의 핵심 — "scheduler 재시작" 같은 1분 작업이 잊혀지면 fix 무효화
- importlib.reload는 transitive 종속성/객체 정체성 문제로 위험 → 프로세스 재시작이 안전
- mtime + size 조합이 hash보다 가벼우면서 OneDrive 신뢰성도 보강

### 검증 체크포인트
- [x] SrcWatcher 모듈 + 5/5 테스트 통과
- [x] run_scheduler.py 통합
- [x] 외부 wrapper batch
- [x] CLAUDE.md 운영 가이드
- [ ] 운영자가 start_scheduler_loop.bat으로 전환 후 다음 코드 변경에서 자동 재시작 확인

**관련 작업 (archive)**: docs/archive/2026-04/scheduler-auto-reload/

**관련 Plan**: [docs/01-plan/features/scheduler-auto-reload.plan.md](../01-plan/features/scheduler-auto-reload.plan.md)
**선행 사례**: docs/archive/2026-04/{claude-respond-fix, d1-bgf-collector-import-fix} (수동 재시작 필요 사례)

---

## [PLANNED] CLEAR_GHOST_STOCK 자동실행 승격 검토 (P2)

**목표**: 유령 재고 보정(CLEAR_GHOST_STOCK)을 LOW(승인 필요) → HIGH(자동 실행)로 승격
**동기**: 현재 LOW 분류라 매번 카카오 승인 필요. 2주 오탐률 확인 후 안전하면 자동화
**선행조건**: integrity_checks 2주 누적 데이터에서 food_ghost_stock 오탐률 < 5%
**예상 영향**: constants.py AUTO_EXEC_HIGH/LOW, auto_executor.py _execute_action()

---

## [PLANNED] 하네스 엔지니어링 Week 3 — AI 요약 서비스 (P2)

**목표**: integrity 체크 + 발주 결과를 규칙 기반 템플릿으로 요약해서 카카오 리포트에 포함
**동기**: 매일 카카오 알림이 원시 데이터만 전달 → 의사결정에 필요한 요약 부족
**선행조건**: executed_at 검증 완료 (WATCHING 이슈 해결)
**예상 영향**: schema.py (ai_summaries DDL), notification/summary_report_service.py (신규), daily_job.py Phase 3, kakao_notifier.py

## [PLANNED] 자전 시스템 미해결 항목 (expiry_time_mismatch) (P1)

**목표**: 체크 expiry_time_mismatch이(가) 31일 연속 anomaly 발생 중 (1개 항목)
**동기**: 자동 감지 (2026-04-06) -- integrity_unresolved
**선행조건**: 없음
**예상 영향**: integrity_unresolved 관련 파일

---

## [WATCHING] 스케줄러 SrcWatcher auto-reload 후 장기 다운 (04-09)

**문제**: 04-09 14:41:50 SrcWatcher가 src 변경 감지 → 14:42:31 graceful exit(0) → 04-10 00:05:46 OpsMetrics 재개까지 약 9시간 다운. 22:00 정밀폐기 세션 + 23:55 ops_issue_pipeline 미실행.

**원인**:
- 패턴은 04-07 scheduler-wrapper-restart와 동일. wrapper가 재기동은 했으나 다음 scheduled job 실행까지 오랜 지연이 있었거나, 혹은 wrapper 재기동 자체가 늦었음.
- 04-09 14:42~04-10 00:05 사이 bgf_auto.log에 아무 엔트리 없음 (약 9.4시간 공백).

**영향**:
- 22:00 정밀폐기 세션 미실행 (4매장 × 22:00 슬롯 폐기 확인 불가)
- 04-09 23:55 ops_issue_pipeline 미실행 (verification_log_files_missing 검증 불가)
- waste-lightweight-46704-missing 이슈의 22:00/23:55 체크포인트 미검증 상태

**현황**: 04-10 00:05 OpsMetrics가 재개 — 스케줄러는 복구됨

### 검증 체크포인트
- [ ] 04-10 22:00 정밀폐기 세션 정상 실행 확인 (4매장 파일 생성)
- [ ] 04-10 23:55 ops_metrics 정상 실행 확인 (verification_log_files_missing = 0)
- [ ] 04-09 이후 1주간 동일 패턴(낮 시간대 auto-reload → 야간 잡 미실행) 재발 모니터링


---
