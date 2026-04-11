# Plan — scheduler-job-guard

**Feature**: scheduler-job-guard (스케줄러 작업 락 + 재시도 + 체크포인트)
**Priority**: P1 (07시 발주 장애 즉시 대응)
**Created**: 2026-04-11
**Issue-Chain**: scheduling#daily-order-retry-guard

---

## 1. 배경 (Why)

04-11 07:00 스케줄 실행 중 인터넷 장애 → 오류 종료 → 수동 재실행 시 중복 실행.
현재 `scheduler.lock`은 프로세스 수준이라 같은 프로세스 내 동일 job 중복을 못 막음.
실패 시 재시도도 없어 인터넷 복구 후에도 다음날까지 발주 불가.

## 2. 목표 (What)

**3단계 방어선**: 작업 락 → 실패 재시도 → Phase 체크포인트

### DoD
- [ ] 1단계: 동일 `daily_order` job 중복 실행 차단 (lock 파일 기반)
- [ ] 2단계: 실패 매장만 15분 간격 3회 재시도 + 3회 실패 시 카카오 알림
- [ ] 3단계: Phase별 체크포인트 저장 → 재시도 시 완료 Phase 스킵

## 3. 범위 (Scope)

### 1단계: 작업 락

| 파일 | 변경 |
|------|------|
| `src/application/scheduler/job_guard.py` | **신규** — JobGuard 클래스 (lock/unlock/is_locked) |
| `run_scheduler.py` | `job_wrapper_multi_store()`에 guard 적용 |

### 2단계: 실패 재시도

| 파일 | 변경 |
|------|------|
| `src/application/scheduler/job_guard.py` | RetryManager 추가 (상태 파일 + 재시도 스케줄) |
| `run_scheduler.py` | 실패 매장 수집 → 15분 후 one-shot 재시도 등록 |

### 3단계: Phase 체크포인트

| 파일 | 변경 |
|------|------|
| `src/application/scheduler/job_guard.py` | PhaseCheckpoint 추가 |
| `src/scheduler/daily_job.py` | 각 Phase 완료 시 체크포인트 저장 |

## 4. 접근

### 1단계 작업 락 설계
```
data/job_locks/daily_order.lock
  내용: {"pid": 12345, "started_at": "07:00:05", "stores": ["46513","46704",...]}
  진입 시: lock 존재 + PID 살아있음 → SKIP + WARNING 로그
  lock 2시간 초과 → stale 판정 → 강제 해제 + 진입
  정상 종료 시: lock 삭제
```

### 2단계 재시도 설계
```
data/job_state/daily_order_2026-04-11.json
  {"attempt": 1, "stores": {
    "46513": {"status": "completed"},
    "46704": {"status": "failed", "error": "ConnectionError"},
    "47863": {"status": "pending"},
    "49965": {"status": "pending"}
  }}

실패 감지 → 15분 후 schedule.every().day.at("07:15").do(retry_wrapper) 등록
재시도 시 → failed/pending 매장만 처리
최대 3회 → 최종 실패 시 카카오 알림
```

### 3단계 체크포인트 설계
```
data/job_state/daily_order_2026-04-11_46704.json
  {"phases": {
    "collection": {"status": "completed", "finished_at": "07:22:08"},
    "calibration": {"status": "completed", "finished_at": "07:25:26"},
    "preparation": {"status": "failed", "error": "ConnectionError"},
    "execution": {"status": "pending"}
  }}

재시도 시 → completed Phase 스킵 → failed Phase부터 재개
단, Selenium 세션은 새로 열어야 함 (기존 세션 재사용 불가)
```

## 5. 리스크

| 리스크 | 대응 |
|--------|------|
| lock 삭제 안 되고 프로세스 죽음 → 영구 락 | stale 판정 (2시간 초과) |
| 재시도 중 스케줄러 재시작 → 상태 파일 무효 | 날짜 기반 파일명으로 당일만 유효 |
| Phase 재개 시 DB 상태 불일치 | 수집 Phase는 idempotent(중복 OK), 발주는 site_ordered 체크로 중복 방지 |
