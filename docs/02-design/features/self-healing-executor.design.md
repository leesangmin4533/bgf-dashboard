# Design: 자전 시스템 실행+검증 고리

> Plan: `docs/01-plan/features/self-healing-executor.plan.md`

## 1. Step 1: 중복 제안 방지 + CHECK_DELIVERY_TYPE 조정

### 1.1 중복 제안 방지 (`action_proposal_service.py`)

**위치**: `generate()` 메서드 시작부

```python
def generate(self, check_results):
    # === 추가 ===
    # 오늘 이미 PENDING인 action_type 조회 → 중복 방지
    existing = self.proposal_repo.get_pending(self.store_id, self.today)
    existing_types = {p["action_type"] for p in existing}
    # _analyze() 후 기존 타입 필터링
    proposals = [p for p in self._analyze(check_results)
                 if p["action_type"] not in existing_types]
```

### 1.2 CHECK_DELIVERY_TYPE site 제외 (`integrity_check_repo.py:290`)

**위치**: `check_missing_delivery_type()` SQL

```python
# Before
WHERE store_id = ?
  AND status NOT IN ('expired', 'disposed', 'cancelled')
  AND (delivery_type IS NULL OR delivery_type = '')

# After — order_source='site' 수집 발주는 delivery_type 빈값이 정상
WHERE store_id = ?
  AND status NOT IN ('expired', 'disposed', 'cancelled')
  AND (delivery_type IS NULL OR delivery_type = '')
  AND order_source != 'site'
```

---

## 2. Step 2: DB 마이그레이션 (v70)

**파일**: `src/settings/constants.py`, `src/db/models.py`

```python
# constants.py
DB_SCHEMA_VERSION = 70  # v70: action_proposals 실행/검증 컬럼

# models.py SCHEMA_MIGRATIONS[70]
70: """
ALTER TABLE action_proposals ADD COLUMN executed_at TIMESTAMP;
ALTER TABLE action_proposals ADD COLUMN verified_at TIMESTAMP;
ALTER TABLE action_proposals ADD COLUMN verified_result TEXT;
"""
```

**action_proposal_repo.py 메서드 추가:**

```python
def mark_executed(self, proposal_id: int) -> None:
    """EXECUTED 상태 + executed_at 기록"""
    UPDATE action_proposals
    SET status = 'EXECUTED', executed_at = datetime('now', 'localtime')
    WHERE id = ?

def mark_verified(self, proposal_id: int, result: str) -> None:
    """검증 결과 기록 (success/failed)"""
    UPDATE action_proposals
    SET verified_at = datetime('now', 'localtime'), verified_result = ?
    WHERE id = ?

def get_executed_yesterday(self, store_id: str) -> List[Dict]:
    """전날 EXECUTED 건 조회 (검증 대상)"""
    SELECT * FROM action_proposals
    WHERE store_id = ? AND status = 'EXECUTED'
    AND date(executed_at) = date('now', '-1 day', 'localtime')
```

---

## 3. Step 3: AutoExecutor (`src/application/services/auto_executor.py`)

### 분류 상수 (`constants.py`)

```python
# 자전 시스템 실행 분류
AUTO_EXEC_HIGH = frozenset({"CLEAR_EXPIRED_BATCH"})
AUTO_EXEC_LOW = frozenset({
    "CLEAR_GHOST_STOCK", "RESTORE_IS_AVAILABLE", "DEACTIVATE_EXPIRED"
})
AUTO_EXEC_INFO = frozenset({"FIX_EXPIRY_TIME", "MANUAL_CHECK_REQUIRED"})
AUTO_EXEC_SKIP = frozenset({"CHECK_DELIVERY_TYPE"})
```

### AutoExecutor 클래스

```python
class AutoExecutor:
    """action_proposals PENDING → 분류별 처리"""

    def __init__(self, store_id: str):
        self.store_id = store_id
        self.repo = ActionProposalRepository(store_id=store_id)

    def run(self) -> Dict[str, Any]:
        """PENDING 조회 → HIGH 자동실행 + LOW 승인요청 + INFO 알림"""
        pending = self.repo.get_pending(self.store_id, date.today().isoformat())
        result = {"executed": 0, "approval_requested": 0, "notified": 0, "skipped": 0}

        high = [p for p in pending if p["action_type"] in AUTO_EXEC_HIGH]
        low = [p for p in pending if p["action_type"] in AUTO_EXEC_LOW]
        info = [p for p in pending if p["action_type"] in AUTO_EXEC_INFO]

        for p in high:
            if self._execute_action(p):
                self.repo.mark_executed(p["id"])
                result["executed"] += 1

        if low:
            self._request_approval(low)
            for p in low:
                self.repo.mark_resolved(p["id"], status="PENDING_APPROVAL")
            result["approval_requested"] += len(low)

        if info:
            self._notify_info(info)
            for p in info:
                self.repo.mark_resolved(p["id"], status="NOTIFIED")
            result["notified"] += len(info)

        return result

    def _execute_action(self, proposal: Dict) -> bool:
        """HIGH 항목 자동 실행"""
        action_type = proposal["action_type"]
        try:
            if action_type == "CLEAR_EXPIRED_BATCH":
                return self._clear_expired_batch(proposal)
            return False
        except Exception as e:
            logger.error(f"[AutoExec] {action_type} 실행 실패: {e}")
            return False

    def _clear_expired_batch(self, proposal: Dict) -> bool:
        """만료 배치 remaining_qty → 0 보정"""
        conn = DBRouter.get_store_connection(self.store_id)
        try:
            updated = conn.execute("""
                UPDATE inventory_batches
                SET remaining_qty = 0
                WHERE store_id = ? AND status = 'expired' AND remaining_qty > 0
            """, (self.store_id,)).rowcount
            conn.commit()
            logger.info(f"[AutoExec] CLEAR_EXPIRED_BATCH: {updated}건 보정")
            return updated > 0
        finally:
            conn.close()

    def _request_approval(self, proposals: List[Dict]) -> None:
        """LOW 항목 카카오 승인 요청"""
        from src.application.services.kakao_proposal_formatter import KakaoProposalFormatter
        from src.notification.kakao_notifier import KakaoNotifier, DEFAULT_REST_API_KEY
        msg = KakaoProposalFormatter().format(self.store_id, proposals)
        msg = "[승인 필요]\n" + msg
        KakaoNotifier(DEFAULT_REST_API_KEY).send_message(msg)

    def _notify_info(self, proposals: List[Dict]) -> None:
        """INFO 항목 알림만"""
        logger.info(f"[AutoExec] {self.store_id} INFO {len(proposals)}건 알림 처리")
```

---

## 4. Step 4: ExecutionVerifier (`src/application/services/execution_verifier.py`)

```python
class ExecutionVerifier:
    """전날 EXECUTED 건 검증"""

    def __init__(self, store_id: str):
        self.store_id = store_id
        self.repo = ActionProposalRepository(store_id=store_id)

    def verify_previous_day(self) -> Dict[str, Any]:
        """전날 EXECUTED 건 조회 → 검증 → verified_result 업데이트"""
        executed = self.repo.get_executed_yesterday(self.store_id)
        result = {"verified": 0, "success": 0, "failed": 0}

        for p in executed:
            try:
                vr = self._verify(p)
                self.repo.mark_verified(p["id"], vr)
                result["verified"] += 1
                result[vr] += 1
            except Exception as e:
                logger.warning(f"[Verify] {p['id']} 검증 실패: {e}")
                self.repo.mark_verified(p["id"], "failed")
                result["verified"] += 1
                result["failed"] += 1

        if result["failed"] > 0:
            self._alert_failures(result)

        return result

    def _verify(self, proposal: Dict) -> str:
        """action_type별 검증"""
        if proposal["action_type"] == "CLEAR_EXPIRED_BATCH":
            return self._verify_expired_batch()
        return "skipped"

    def _verify_expired_batch(self) -> str:
        """만료 배치 잔여 재확인"""
        conn = DBRouter.get_store_connection(self.store_id)
        try:
            cnt = conn.execute("""
                SELECT COUNT(*) FROM inventory_batches
                WHERE store_id = ? AND status = 'expired' AND remaining_qty > 0
            """, (self.store_id,)).fetchone()[0]
            return "success" if cnt == 0 else "failed"
        finally:
            conn.close()

    def _alert_failures(self, result: Dict) -> None:
        """검증 실패 시 카카오 알림"""
        from src.notification.kakao_notifier import KakaoNotifier, DEFAULT_REST_API_KEY
        msg = (f"[자전시스템 검증]\n"
               f"매장 {self.store_id}: 전날 실행 {result['verified']}건 중 "
               f"{result['failed']}건 실패\n확인 필요")
        KakaoNotifier(DEFAULT_REST_API_KEY).send_message(msg)
```

---

## 5. Step 5: calibration.py Phase 1.67 통합

**위치**: `src/scheduler/phases/calibration.py:281-302`

```python
# Phase 1.67 수정 후 실행 순서:

# 1. 전날 실행 건 검증 (Step 4)
try:
    from src.application.services.execution_verifier import ExecutionVerifier
    verify_result = ExecutionVerifier(store_id).verify_previous_day()
    if verify_result["verified"] > 0:
        logger.info(
            f"전날 실행 검증: {verify_result['success']}건 성공, "
            f"{verify_result['failed']}건 실패"
        )
except Exception as e:
    logger.warning(f"[Phase 1.67] 전날 검증 실패 (계속): {e}")

# 2. 데이터 무결성 검증 (기존)
integrity_svc = DataIntegrityService(store_id=store_id)
integrity_result = integrity_svc.run_all_checks(store_id)

# 3. 자동 실행 (Step 3)
try:
    from src.application.services.auto_executor import AutoExecutor
    exec_result = AutoExecutor(store_id).run()
    if exec_result["executed"] > 0:
        logger.info(f"자동 실행: {exec_result['executed']}건")
except Exception as e:
    logger.warning(f"[Phase 1.67] 자동 실행 실패 (계속): {e}")
```

---

## 6. 파일 변경 목록

| 파일 | 변경 유형 | 내용 |
|------|----------|------|
| `src/settings/constants.py` | 수정 | DB_SCHEMA_VERSION 69→70, AUTO_EXEC 상수 |
| `src/db/models.py` | 수정 | SCHEMA_MIGRATIONS[70] 추가 |
| `src/application/services/action_proposal_service.py` | 수정 | 중복 방지 로직 |
| `src/infrastructure/database/repos/integrity_check_repo.py` | 수정 | site 발주 제외 |
| `src/infrastructure/database/repos/action_proposal_repo.py` | 수정 | mark_executed/verified/get_executed_yesterday |
| `src/application/services/auto_executor.py` | **신규** | AutoExecutor |
| `src/application/services/execution_verifier.py` | **신규** | ExecutionVerifier |
| `src/scheduler/phases/calibration.py` | 수정 | Phase 1.67 통합 |

---

## 7. status 흐름도

```
PENDING → (AutoExecutor)
  ├─ HIGH → EXECUTED → (Verifier 다음날) → success/failed
  ├─ LOW  → PENDING_APPROVAL → (수동 대시보드) → APPROVED/DISMISSED
  ├─ INFO → NOTIFIED
  └─ SKIP → (재생성 안 됨)
```
