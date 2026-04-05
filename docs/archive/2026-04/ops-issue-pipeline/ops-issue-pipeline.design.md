# ops-issue-pipeline Design

> Plan 참조: `docs/01-plan/features/ops-issue-pipeline.plan.md`

## 구현 순서

```
1. ops_anomaly.py (Domain)       — 순수 함수, 테스트 먼저
2. ops_metrics.py (Analysis)     — DB 쿼리, 테스트 가능
3. issue_chain_writer.py (Infra) — .md 파일 I/O
4. ops_issue_detector.py (App)   — 오케스트레이션
5. run_scheduler.py 연동          — 23:55 스케줄 등록
6. 통합 테스트                     — 전체 파이프라인 검증
```

---

## 1. ops_anomaly.py (Domain 계층)

**위치**: `src/domain/ops_anomaly.py`
**원칙**: 순수 함수만, I/O 없음, 테스트 용이

```python
"""운영 지표 이상 판정 (순수 함수)"""

from dataclasses import dataclass
from typing import List, Optional

@dataclass
class OpsAnomaly:
    """감지된 이상 항목"""
    metric_name: str          # 지표명 (prediction_accuracy, order_failure, ...)
    issue_chain_file: str     # 등록 대상 파일 (prediction.md, ...)
    title: str                # 이슈 제목
    priority: str             # P1, P2, P3
    description: str          # 목표/동기 텍스트
    evidence: dict            # 감지 근거 수치

# 임계값 상수
THRESHOLDS = {
    "prediction_accuracy": {"ratio": 1.2, "multi_category_p1": 3},
    "order_failure": {"ratio": 1.5},
    "waste_rate": {"ratio": 1.5, "food_mids": ("001","002","003","004","005")},
    "collection_failure": {"consecutive_days": 3},
    "integrity_unresolved": {"consecutive_days": 7, "p1_days": 14},
}

# 영역 매핑
METRIC_TO_FILE = {
    "prediction_accuracy": "prediction.md",
    "order_failure": "order-execution.md",
    "waste_rate": "expiry-tracking.md",
    "collection_failure": "data-collection.md",
    "integrity_unresolved": "scheduling.md",
}

def detect_anomalies(metrics: dict) -> List[OpsAnomaly]:
    """5개 지표 데이터를 받아 이상 항목 리스트 반환"""

def _check_prediction_accuracy(data: dict) -> Optional[OpsAnomaly]:
    """카테고리별 7d MAE vs 14d MAE 비교 → 20% 이상 악화 감지"""

def _check_order_failure(data: dict) -> Optional[OpsAnomaly]:
    """최근 7d 실패건수 vs 이전 7d → 50% 이상 증가 감지"""

def _check_waste_rate(data: dict) -> List[OpsAnomaly]:
    """카테고리별 7d 폐기율 vs 30d 평균 → 1.5배 이상 감지 (복수 가능)"""

def _check_collection_failure(data: dict) -> Optional[OpsAnomaly]:
    """동일 수집 유형 3일 연속 실패 감지"""

def _check_integrity_unresolved(data: dict) -> Optional[OpsAnomaly]:
    """특정 check_name 7일 연속 anomaly > 0 감지"""
```

### 우선순위 결정 로직

```python
def _determine_priority(metric_name: str, data: dict) -> str:
    """기본 우선순위 + 승격 조건"""
    # order_failure, collection_failure → 항상 P1
    # prediction_accuracy → 3개+ 카테고리 동시면 P1, 아니면 P2
    # waste_rate → 푸드(001~005)면 P1, 아니면 P2
    # integrity_unresolved → 14일 연속이면 P1, 아니면 P2
```

---

## 2. ops_metrics.py (Analysis 계층)

**위치**: `src/analysis/ops_metrics.py`
**원칙**: DB 조회만, 판정 없음. dict 반환.

```python
"""운영 지표 수집 — 5개 지표 DB 조회"""

from src.infrastructure.database.connection import DBRouter

class OpsMetrics:
    """매장별 운영 지표 수집"""

    def __init__(self, store_id: str):
        self.store_id = store_id

    def collect_all(self) -> dict:
        """5개 지표 전부 수집 → dict 반환"""
        return {
            "prediction_accuracy": self._prediction_accuracy(),
            "order_failure": self._order_failure(),
            "waste_rate": self._waste_rate(),
            "collection_failure": self._collection_failure(),
            "integrity_unresolved": self._integrity_unresolved(),
        }

    def _prediction_accuracy(self) -> dict:
        """eval_outcomes에서 카테고리별 7d/14d MAE 집계"""
        # SELECT mid_cd, AVG(ABS(predicted_qty - actual_qty)) as mae
        # FROM eval_outcomes
        # WHERE eval_date >= date('now', '-7 days') ...
        # → {"categories": [{"mid_cd": "001", "mae_7d": 2.3, "mae_14d": 1.8}, ...]}

    def _order_failure(self) -> dict:
        """order_fail_reasons에서 최근 7d vs 이전 7d 실패건수"""
        # → {"recent_7d": 15, "prev_7d": 8}

    def _waste_rate(self) -> dict:
        """waste_slips + daily_sales에서 카테고리별 폐기율"""
        # → {"categories": [{"mid_cd": "001", "rate_7d": 0.05, "rate_30d": 0.03}, ...]}

    def _collection_failure(self) -> dict:
        """collection_logs에서 수집 유형별 연속 실패일수"""
        # → {"types": [{"type": "sales", "consecutive_fails": 0}, ...]}

    def _integrity_unresolved(self) -> dict:
        """integrity_checks에서 check_name별 연속 anomaly일수"""
        # → {"checks": [{"name": "food_ghost_stock", "consecutive_days": 3}, ...]}
```

### DB 접근 패턴

```python
conn = DBRouter.get_store_connection(self.store_id)
try:
    cursor = conn.cursor()
    cursor.execute(...)
    return ...
finally:
    conn.close()
```

### 데이터 부족 처리

- 각 메서드에서 데이터 일수 확인
- 7일 미만이면 `{"insufficient_data": True}` 반환
- `ops_anomaly.py`에서 `insufficient_data=True`이면 판정 스킵

---

## 3. issue_chain_writer.py (Infrastructure 계층)

**위치**: `src/infrastructure/issue_chain_writer.py`
**원칙**: 파일 I/O만, 판정 없음

```python
"""이슈 체인 .md 파일 자동 갱신"""

import re
from pathlib import Path
from datetime import date
from typing import List, Optional

ISSUES_DIR = Path("docs/05-issues")

class IssueChainWriter:
    """이슈 체인 파일에 [PLANNED] 블록 자동 삽입"""

    def write_anomalies(self, anomalies: List) -> int:
        """이상 항목 리스트를 해당 이슈 체인 파일에 등록. 등록 건수 반환."""

    def _is_duplicate(self, filepath: Path, title_keywords: List[str]) -> bool:
        """기존 [PLANNED]/[OPEN]/[WATCHING] 제목에서 핵심 키워드 매칭"""

    def _is_recently_resolved(self, filepath: Path, title_keywords: List[str], days: int = 14) -> bool:
        """최근 14일 내 [RESOLVED]로 전환된 동일 패턴 확인"""

    def _insert_planned_block(self, filepath: Path, block: str) -> bool:
        """마지막 --- 구분자 위에 [PLANNED] 블록 안전 삽입"""

    def _build_block(self, anomaly) -> str:
        """[PLANNED] 블록 마크다운 텍스트 생성"""
```

### 삽입 위치 전략

```
파일 내용:
  ## [WATCHING] 기존 이슈 ...
  ---
  ## [PLANNED] 기존 계획 ...
  ---              ← 여기 위에 삽입
  <!-- 주석 -->
```

- 파일 끝의 마지막 `---` 구분자 바로 위에 삽입
- `---`가 없으면 파일 끝에 추가
- 삽입 후 `---`로 구분

### 중복 방지 키워드 매칭

```python
# 예: "음료류 폐기율 상승 조사" → 키워드: ["음료류", "폐기율"]
# 기존 이슈 "음료류 폐기율 급등 분석" → "음료류"+"폐기율" 2개 매칭 → 중복
DUPLICATE_THRESHOLD = 2  # 2개 이상 키워드 매칭 시 중복으로 판정
```

### 자동 감지 태그

```markdown
**동기**: 자동 감지 (2026-04-05) — {metric_name}
```

수동 등록과 구분하기 위해 `자동 감지` 접두사 사용.

---

## 4. ops_issue_detector.py (Application 계층)

**위치**: `src/application/services/ops_issue_detector.py`
**원칙**: 오케스트레이션만

```python
"""운영 지표 → 이슈 자동 등록 파이프라인 오케스트레이터"""

from src.settings.store_context import StoreContext
from src.analysis.ops_metrics import OpsMetrics
from src.domain.ops_anomaly import detect_anomalies
from src.infrastructure.issue_chain_writer import IssueChainWriter

class OpsIssueDetector:
    """매장별 운영 지표 수집 → 이상 판정 → 이슈 등록 → 알림"""

    def run_all_stores(self) -> dict:
        """전체 활성 매장 순회"""
        active_stores = StoreContext.get_all_active()
        all_anomalies = []
        for ctx in active_stores:
            anomalies = self._detect_for_store(ctx.store_id)
            all_anomalies.extend(anomalies)

        # 매장 간 중복 제거 (동일 지표가 여러 매장에서 감지)
        unique = self._deduplicate_cross_store(all_anomalies)

        # 이슈 체인 등록
        writer = IssueChainWriter()
        registered = writer.write_anomalies(unique)

        # CLAUDE.md 테이블 동기화
        if registered > 0:
            self._sync_table()
            self._send_alert(unique)

        return {"total_anomalies": len(all_anomalies), "registered": registered}

    def _detect_for_store(self, store_id: str) -> list:
        """단일 매장 지표 수집 → 판정"""
        metrics = OpsMetrics(store_id).collect_all()
        return detect_anomalies(metrics)

    def _deduplicate_cross_store(self, anomalies: list) -> list:
        """여러 매장에서 동일 지표 감지 시 1건만 등록"""
        # metric_name + issue_chain_file 기준으로 중복 제거
        # 가장 심각한 것(P1 > P2 > P3) 유지

    def _sync_table(self):
        """sync_issue_table.py 호출"""
        import subprocess
        subprocess.run(["python", "scripts/sync_issue_table.py"], ...)

    def _send_alert(self, anomalies: list):
        """카카오 알림"""
        from src.notification.kakao_notifier import KakaoNotifier, DEFAULT_REST_API_KEY
        # "[운영 이상 감지]\n{anomaly 요약}" 발송
```

---

## 5. run_scheduler.py 연동

```python
# run_scheduler.py에 추가

def ops_issue_detect_wrapper() -> None:
    """운영 지표 이상 감지 → 이슈 자동 등록"""
    try:
        from src.application.services.ops_issue_detector import OpsIssueDetector
        result = OpsIssueDetector().run_all_stores()
        if result["registered"] > 0:
            logger.info(f"[OpsIssue] {result['registered']}건 이슈 자동 등록")
        else:
            logger.info(f"[OpsIssue] 이상 없음 (검사: {result['total_anomalies']}건)")
    except Exception as e:
        logger.warning(f"[OpsIssue] 실패 (무시): {e}")

# 스케줄 등록 — 매일 23:55
schedule.every().day.at("23:55").do(ops_issue_detect_wrapper)
logger.info("[Schedule] Ops issue detection: 23:55")
```

---

## 6. 테스트 설계

### 단위 테스트

| 파일 | 대상 | 테스트 내용 |
|------|------|-----------|
| `test_ops_anomaly.py` | `detect_anomalies()` | 각 지표별 정상/이상 경계값 테스트, 우선순위 결정, 데이터 부족 스킵 |
| `test_ops_metrics.py` | `OpsMetrics` | 각 쿼리 정상 반환, 빈 테이블 처리, 날짜 범위 정확성 |
| `test_issue_chain_writer.py` | `IssueChainWriter` | 중복 방지, 안전 삽입, 쿨다운 체크, 파일 없을 때 처리 |
| `test_ops_issue_detector.py` | `OpsIssueDetector` | 매장 간 중복 제거, 전체 플로우 mock |

### 통합 테스트

```python
def test_full_pipeline():
    """DB에 이상 데이터 삽입 → 감지 → .md 파일 등록 확인"""
```

---

## 에러 처리

| 상황 | 처리 |
|------|------|
| DB 연결 실패 | 해당 매장 스킵, 로그 경고 |
| .md 파일 없음 | 해당 영역 스킵, 로그 경고 |
| .md 파일 파싱 실패 | 해당 영역 스킵, 로그 경고 |
| 카카오 알림 실패 | 로그 경고, 이슈 등록은 완료 |
| sync_issue_table.py 실패 | 로그 경고, 다음 커밋 시 hook이 재실행 |

모든 에러는 `try/except` + `logger.warning()` — 발주 플로우에 영향 없음.

---

## 상수/설정

`src/settings/constants.py`에 추가:

```python
# 운영 이상 감지 임계값
OPS_PREDICTION_ACCURACY_RATIO = 1.2      # MAE 20% 악화
OPS_ORDER_FAILURE_RATIO = 1.5            # 실패 50% 증가
OPS_WASTE_RATE_RATIO = 1.5               # 폐기율 1.5배
OPS_COLLECTION_CONSECUTIVE_DAYS = 3      # 수집 3일 연속 실패
OPS_INTEGRITY_CONSECUTIVE_DAYS = 7       # 자전 7일 연속 미해결
OPS_COOLDOWN_DAYS = 14                   # RESOLVED 후 재감지 쿨다운
OPS_DUPLICATE_KEYWORD_THRESHOLD = 2      # 중복 키워드 매칭 임계값
```
