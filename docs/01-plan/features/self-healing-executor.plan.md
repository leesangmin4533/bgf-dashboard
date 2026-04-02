# Plan: bgf_auto 자전 시스템 — 실행+검증 고리 완성

## 1. 개요

### 현재 상태
```
감지 ✅ → 판단 ✅ → 보고 ✅ → 실행 ❌ → 검증 ❌
```

- **감지**: DataIntegrityService.run_all_checks() — 6개 체크, Phase 1.67
- **판단**: ActionProposalService.generate() — DB 저장 (action_proposals)
- **보고**: _send_alert() + _run_action_proposals() → 카카오 발송 (이미 동작)
- **실행**: 없음 — PENDING 상태로 방치
- **검증**: 없음 — 실행 결과 확인 로직 없음

### 추가 문제 (토론 결과)
1. **PENDING 재생성**: 매일 동일 이상에 대해 새 proposal 생성 → 중복 누적
2. **CHECK_DELIVERY_TYPE 과감지**: 2060건 전부 site 수집 발주 → 정상 데이터에 경고
3. **CLEAR_GHOST_STOCK**: 오탐률 미검증 → 자동 실행 위험

### 목표
1. 중복 제안 방지 (PENDING 재생성 차단)
2. CHECK_DELIVERY_TYPE 체크 조정 (site 발주 제외)
3. AutoExecutor 구현 (LOW: 승인 요청만, HIGH: CLEAR_EXPIRED_BATCH만)
4. DB 마이그레이션 (executed_at, verified_at, verified_result)
5. 다음날 검증 고리 (Phase 1.67 시작 시 전날 실행 건 확인)

---

## 2. 토론 결론 반영

### ActionType 분류 (확정)

| ActionType | 분류 | 동작 |
|-----------|------|------|
| CLEAR_EXPIRED_BATCH | HIGH | 자동 실행 (만료 배치 remaining=0 보정) |
| CLEAR_GHOST_STOCK | **LOW** | 승인 요청 (2주 후 오탐률 확인 후 HIGH 승격 검토) |
| RESTORE_IS_AVAILABLE | LOW | 승인 요청 |
| DEACTIVATE_EXPIRED | LOW | 승인 요청 |
| CHECK_DELIVERY_TYPE | **SKIP** | 체크 로직 조정 (site 발주 제외) |
| FIX_EXPIRY_TIME | INFO | 알림만 |
| MANUAL_CHECK_REQUIRED | INFO | 알림만 |

### 검증 시점
- ~~동일 세션 30초 후~~ → **다음날 Phase 1.67 시작 시**
- 전날 `status=EXECUTED` 건 조회 → BGF 재수집 데이터와 비교

---

## 3. 구현 순서

### Step 1: 중복 제안 방지 + CHECK_DELIVERY_TYPE 조정

**파일**: `action_proposal_service.py`, `integrity_check_repo.py`

- `generate()` 시작 시 오늘 PENDING 건 조회 → 이미 있는 action_type은 스킵
- `check_missing_delivery_type()`: `WHERE order_source != 'site'` 조건 추가

### Step 2: DB 마이그레이션

**파일**: `src/db/models.py`, `src/settings/constants.py`

```sql
ALTER TABLE action_proposals ADD COLUMN executed_at TIMESTAMP;
ALTER TABLE action_proposals ADD COLUMN verified_at TIMESTAMP;
ALTER TABLE action_proposals ADD COLUMN verified_result TEXT;  -- success/failed/skipped
```

`action_proposal_repo.py`에 `mark_executed()`, `mark_verified()` 메서드 추가.

### Step 3: AutoExecutor 구현

**파일**: `src/application/services/auto_executor.py` (신규)

```python
class AutoExecutor:
    def run(self, store_id: str) -> Dict[str, Any]:
        """PENDING 제안 조회 → 분류별 처리"""
    def _execute_high(self, proposal) -> bool:
        """HIGH: CLEAR_EXPIRED_BATCH 자동 실행"""
    def _request_approval(self, proposals) -> None:
        """LOW: 카카오 승인 요청 메시지"""
    def _notify_info(self, proposals) -> None:
        """INFO: 알림만"""
```

**constants.py에 분류 상수 추가:**
```python
AUTO_EXEC_HIGH = {"CLEAR_EXPIRED_BATCH"}
AUTO_EXEC_LOW = {"CLEAR_GHOST_STOCK", "RESTORE_IS_AVAILABLE", "DEACTIVATE_EXPIRED"}
AUTO_EXEC_INFO = {"FIX_EXPIRY_TIME", "MANUAL_CHECK_REQUIRED"}
AUTO_EXEC_SKIP = {"CHECK_DELIVERY_TYPE"}
```

### Step 4: ExecutionVerifier 구현

**파일**: `src/application/services/execution_verifier.py` (신규)

```python
class ExecutionVerifier:
    def verify_previous_day(self, store_id: str) -> Dict[str, Any]:
        """전날 EXECUTED 건 검증 → verified_result 업데이트"""
    def _verify_clear_expired_batch(self, proposal) -> str:
        """만료 배치 잔여 재확인 → 'success' or 'failed'"""
```

### Step 5: calibration.py Phase 1.67 통합

```python
# Phase 1.67 실행 순서
1. ExecutionVerifier.verify_previous_day()  ← Step 4 (전날 검증 먼저)
2. DataIntegrityService.run_all_checks()    ← 기존
3. AutoExecutor.run()                        ← Step 3
```

---

## 4. 테스트 계획

- `test_auto_executor.py`: HIGH/LOW/INFO 분류 + 실행 결과
- `test_execution_verifier.py`: 검증 성공/실패 시나리오
- `test_duplicate_proposal.py`: 중복 방지 검증
- 기존 테스트 1,560개 유지

---

## 5. 위험 분석

| 위험 | 완화 |
|------|------|
| AUTO_EXEC_HIGH 오탐 | CLEAR_EXPIRED_BATCH만 (가장 안전한 것만) |
| GHOST_STOCK 오탐 | LOW로 시작, 2주 승인 데이터 수집 후 검토 |
| CHECK_DELIVERY_TYPE 과감지 해소 | site 발주 제외 조건 추가 |
| 중복 알림 | 오늘 PENDING 있으면 재생성 안 함 |
| 검증 누락 | 다음날 자동 검증 + 실패 시 카카오 알림 |
