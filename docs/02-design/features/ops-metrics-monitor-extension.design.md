# Design: 운영 지표 자동 감지 항목 확장 (ops-metrics-monitor-extension)

> 작성일: 2026-04-08
> Plan: `docs/01-plan/features/ops-metrics-monitor-extension.plan.md`
> 상태: Design

---

## 1. Plan 미해결 질문 결정

| # | 질문 | 결정 |
|---|---|---|
| Q1 | false_consumed 임계값 0건 vs 5건 | **0건 엄격 + 일 1회 묶음 알림** (가드 직후이므로 1건도 신호 가치, 단 동일 사유 묶어 알림 피로도 차단) |
| Q2 | 알림 채널 분리 vs 통합 | **기존 카카오 채널 통합** |
| Q3 | 검증 로그 missing 판정 시각 | **23:55 단일 잡 시점** (다른 5개 지표와 동일) |

---

## 2. 아키텍처 (변경 최소화)

기존 3계층 구조 그대로 유지:

```
analysis/ops_metrics.py        ← OpsMetrics: 매장별 DB 조회 (지표 #6 추가)
                                   + collect_system() 신규 (지표 #7 시스템 전역)
domain/ops_anomaly.py          ← detect_anomalies: 순수 함수 (체커 2개 추가)
services/ops_issue_detector.py ← run_all_stores: 매장 루프 + 시스템 1회 (1줄 추가)
```

**변경 원칙**:
- 매장별 지표(#6)는 `OpsMetrics.collect_all()`에 추가 → 4매장 × 1쿼리
- 시스템 지표(#7)는 매장 루프 외부 1회 → `OpsMetrics.collect_system()` 신설
- 도메인 체커는 순수 함수 패턴 유지 (테스트 용이)

---

## 3. 신규 지표 #6: `false_consumed_post_guard`

### 3.1 정의
"consumed 마킹된 시점에 이미 만료 24시간 이내였던 단기유통기한(≤7일) 배치 건수"

→ 가드 정의 그대로의 위반 건수. 가드가 정상 작동하면 0이 되어야 함.

### 3.2 SQL (매장당 1회)
```sql
SELECT COUNT(*) AS cnt,
       MAX(updated_at) AS latest_at,
       GROUP_CONCAT(item_cd, ',') AS sample_items
FROM inventory_batches
WHERE store_id = ?
  AND status = 'consumed'
  AND expiration_days <= 7
  AND updated_at >= datetime('now', '-24 hours')
  AND expiry_date IS NOT NULL
  AND julianday(expiry_date) - julianday(updated_at) < 1.0
```

**왜 24시간 윈도우?**: 가드 배포 시각(2026-04-07 16:28:52) 하드코딩 대신 "최근 24시간"이 더 일반적이고 의미 유지. 23:55 잡이 매일 도는 동안 항상 직전 24h를 본다.

**왜 `expiration_days <= 7`?**: 백필 노이즈(장기유통기한 historical) 차단. 검증 reporter와 같은 가드.

### 3.3 임계 + 알림 정책
| 조건 | 액션 |
|---|---|
| `cnt == 0` | 정상, 알림 없음 |
| `cnt >= 1` | **P2 이슈 등록 + 일 1회 알림 (사유 묶음)** |

**일 1회 묶음**: 동일 매장에서 24h 내 첫 발생만 알림. `pending_issues.json`에 이미 같은 metric_name+store_id가 있으면 evidence만 갱신, 알림은 skip. 기존 detector의 dedup 로직 재사용.

### 3.4 도메인 판정 함수
```python
# domain/ops_anomaly.py
def _check_false_consumed_post_guard(data: dict) -> Optional[OpsAnomaly]:
    cnt = data.get("cnt", 0)
    if cnt == 0:
        return None
    return OpsAnomaly(
        metric_name="false_consumed_post_guard",
        issue_chain_file="expiry-tracking.md",
        title=f"BatchSync 가드 우회 의심 {cnt}건 (24h 내)",
        priority="P2",
        description=(
            f"단기유통기한(≤7일) 배치 {cnt}건이 만료 24h 이내에 consumed 마킹됨. "
            f"가드 우회 경로 또는 신규 false consumed 재발 의심. "
            f"최근 발생: {data.get('latest_at')}, 샘플: {data.get('sample_items', '')[:60]}"
        ),
        evidence={
            "count": cnt,
            "latest_at": data.get("latest_at"),
            "sample_items": data.get("sample_items"),
        },
    )
```

---

## 4. 신규 지표 #7: `verification_log_files_missing`

### 4.1 정의
"어제 날짜 기준, 활성 매장별 검증 로그 파일이 모두 존재하지 않는 누락 카운트"

→ `waste_verification_reporter` 분리 로직(fce1594) 회귀 감지.

### 4.2 측정 (Python, SQL 아님)
```python
# analysis/ops_metrics.py — 신규 정적 메서드
@staticmethod
def collect_system() -> dict:
    """시스템 전역 지표 (매장 무관)"""
    from datetime import date, timedelta
    from pathlib import Path
    from src.settings.store_context import StoreContext

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    log_dir = Path("data/logs")
    active_store_ids = [c.store_id for c in StoreContext.get_all_active()]

    expected = len(active_store_ids)
    missing = []
    for sid in active_store_ids:
        fname = f"waste_verification_{sid}_{yesterday}.txt"
        if not (log_dir / fname).exists():
            missing.append(sid)

    return {
        "verification_log_files": {
            "expected_count": expected,
            "missing_count": len(missing),
            "missing_stores": missing,
            "yesterday": yesterday,
        }
    }
```

### 4.3 임계 + 알림 정책
| 조건 | 액션 |
|---|---|
| `missing_count == 0` | 정상 |
| `missing_count >= 1` | **P3 이슈 등록 + 알림 1회** |

**왜 P3?**: 시스템 회귀 신호이지만 즉시 운영 영향은 없음(폐기는 다른 경로로 추적됨). 일일 점검 수준.

### 4.4 도메인 판정 함수
```python
def _check_verification_log_files(data: dict) -> Optional[OpsAnomaly]:
    info = data.get("verification_log_files", {})
    missing_count = info.get("missing_count", 0)
    if missing_count == 0:
        return None
    return OpsAnomaly(
        metric_name="verification_log_files_missing",
        issue_chain_file="expiry-tracking.md",
        title=f"매장별 검증 로그 {missing_count}개 누락 ({info.get('yesterday')})",
        priority="P3",
        description=(
            f"어제({info.get('yesterday')}) 매장별 폐기 검증 로그 파일이 "
            f"{missing_count}개 누락. 예상 {info.get('expected_count')}, "
            f"누락 매장: {', '.join(info.get('missing_stores', []))}. "
            f"waste_verification_reporter 분리 로직 또는 23:00 waste_report_flow 회귀 의심."
        ),
        evidence=info,
    )
```

---

## 5. detector 통합 (1줄 추가 + 1줄 호출)

```python
# services/ops_issue_detector.py — run_all_stores() 마지막 부분
def run_all_stores(self) -> dict:
    # ... (기존 매장 루프 그대로)

    # ★ 신규: 시스템 전역 지표 (매장 무관) 1회 수집
    system_metrics = OpsMetrics.collect_system()
    system_anomalies = detect_anomalies(system_metrics)
    all_anomalies.extend(system_anomalies)

    # ... (기존 dedup + 등록 + 알림 그대로)
```

→ **detector 변경 라인 = 3줄**. 나머지는 OpsMetrics + ops_anomaly에 흡수.

---

## 6. 도메인 매핑 등록

```python
# domain/ops_anomaly.py
METRIC_TO_FILE = {
    "prediction_accuracy": "prediction.md",
    "order_failure": "order-execution.md",
    "waste_rate": "expiry-tracking.md",
    "collection_failure": "data-collection.md",
    "integrity_unresolved": "scheduling.md",
    # 신규 (2026-04-08)
    "false_consumed_post_guard": "expiry-tracking.md",
    "verification_log_files_missing": "expiry-tracking.md",
}

# detect_anomalies() checkers 리스트에 추가
checkers = [
    ("prediction_accuracy", _check_prediction_accuracy),
    ("order_failure", _check_order_failure),
    ("waste_rate", _check_waste_rate),
    ("collection_failure", _check_collection_failure),
    ("integrity_unresolved", _check_integrity_unresolved),
    # 신규
    ("false_consumed", _check_false_consumed_post_guard),
    ("verification_log_files", _check_verification_log_files),
]
```

→ `OpsMetrics.collect_all()` 반환 dict에 `"false_consumed": {...}` 키 추가.

---

## 7. OpsMetrics.collect_all() 변경

```python
def collect_all(self) -> dict:
    return {
        "prediction_accuracy": self._prediction_accuracy(),
        "order_failure": self._order_failure(),
        "waste_rate": self._waste_rate(),
        "collection_failure": self._collection_failure(),
        "integrity_unresolved": self._integrity_unresolved(),
        # 신규 (2026-04-08)
        "false_consumed": self._false_consumed_post_guard(),
    }

def _false_consumed_post_guard(self) -> dict:
    """가드 우회 의심 - 만료 24h 이내 시점에 consumed 마킹된 단기유통기한 배치"""
    conn = DBRouter.get_store_connection(self.store_id)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) AS cnt,
                   MAX(updated_at) AS latest_at,
                   GROUP_CONCAT(item_cd, ',') AS sample_items
            FROM inventory_batches
            WHERE store_id = ?
              AND status = 'consumed'
              AND expiration_days <= 7
              AND updated_at >= datetime('now', '-24 hours')
              AND expiry_date IS NOT NULL
              AND julianday(expiry_date) - julianday(updated_at) < 1.0
        """, (self.store_id,))
        row = cursor.fetchone()
        if not row or row[0] == 0:
            return {"cnt": 0}
        return {
            "cnt": int(row[0]),
            "latest_at": row[1],
            "sample_items": row[2] or "",
        }
    except Exception as e:
        logger.warning(f"[OpsMetrics] {self.store_id} false_consumed 실패: {e}")
        return {"cnt": 0}  # 실패는 정상 취급(과알림 방지)
    finally:
        conn.close()
```

---

## 8. 회귀 테스트 (2개 신규)

### 8.1 `tests/test_ops_metrics_false_consumed.py`
```python
class TestFalseConsumedPostGuard:
    def test_no_false_consumed_returns_zero(self, in_memory_store_db):
        """모든 consumed가 정상적으로 만료 24h 이전에 처리됐으면 0건"""
        # 픽스처: status=consumed, updated_at=어제 12:00, expiry_date=오늘+1
        # → julianday 차이 = 약 +1.5일 → 정상 (가드 위반 아님)
        result = OpsMetrics("99999")._false_consumed_post_guard()
        assert result["cnt"] == 0

    def test_false_consumed_within_24h_detected(self, in_memory_store_db):
        """만료 24h 이내 시점에 consumed 마킹 → 1건 감지"""
        # 픽스처: status=consumed, updated_at=오늘 01:00, expiry_date=오늘
        # → julianday 차이 = 약 -0.04일 → < 1.0 → 위반
        result = OpsMetrics("99999")._false_consumed_post_guard()
        assert result["cnt"] == 1
        assert result["latest_at"] is not None

    def test_long_expiration_excluded(self, in_memory_store_db):
        """expiration_days > 7 (장기유통기한)은 카운트 제외"""
        # 픽스처: expiration_days=30, 나머지 위반 조건 동일
        result = OpsMetrics("99999")._false_consumed_post_guard()
        assert result["cnt"] == 0
```

### 8.2 `tests/test_ops_metrics_verification_logs.py`
```python
class TestVerificationLogFiles:
    def test_all_4_files_exist_returns_zero(self, tmp_path, monkeypatch):
        """4매장 로그 파일 모두 존재 → missing 0"""
        # 픽스처: tmp_path에 4개 파일 생성, StoreContext mock
        result = OpsMetrics.collect_system()
        assert result["verification_log_files"]["missing_count"] == 0

    def test_missing_47863_log_detected(self, tmp_path, monkeypatch):
        """47863만 누락 → missing_count=1, missing_stores=['47863']"""
        # 픽스처: 3개만 생성
        result = OpsMetrics.collect_system()
        info = result["verification_log_files"]
        assert info["missing_count"] == 1
        assert "47863" in info["missing_stores"]
```

### 8.3 도메인 체커 단위 테스트 (선택, 가벼움)
`tests/test_ops_anomaly.py`에 2개 추가:
```python
def test_check_false_consumed_zero_returns_none():
    assert _check_false_consumed_post_guard({"cnt": 0}) is None

def test_check_false_consumed_one_returns_p2():
    a = _check_false_consumed_post_guard({"cnt": 3, "latest_at": "...", "sample_items": "ABC,DEF"})
    assert a.priority == "P2"
    assert "3건" in a.title
```

---

## 9. 파일별 변경 요약

| 파일 | 변경 | LOC |
|---|---|:---:|
| `src/analysis/ops_metrics.py` | `_false_consumed_post_guard()` 추가, `collect_all()` 1줄, `collect_system()` 신규 정적 | +50 |
| `src/domain/ops_anomaly.py` | `METRIC_TO_FILE` 2줄, checkers 2줄, 신규 함수 2개 | +40 |
| `src/application/services/ops_issue_detector.py` | `run_all_stores()` 시스템 호출 3줄 | +3 |
| `tests/test_ops_metrics_false_consumed.py` | 신규 파일 | +60 |
| `tests/test_ops_metrics_verification_logs.py` | 신규 파일 | +50 |
| `tests/test_ops_anomaly.py` | 단위 테스트 2개 추가 | +20 |
| **합계** | | **+223 LOC** |

---

## 10. 회귀 영향 분석

| 영역 | 영향 | 완화 |
|---|---|---|
| 기존 5개 지표 collect_all 호출 | ✅ 무영향 (dict 키 추가만) | - |
| detect_anomalies 순수 함수 시그니처 | ✅ 무영향 (checkers 리스트만 확장) | - |
| 23:55 잡 실행 시간 | 매장당 +1 SQL (~50ms), 시스템 1회 (~10ms) → 총 +200ms | 여유 충분 |
| `pending_issues.json` 스키마 | ✅ 신규 metric_name 키 자동 호환 | - |
| 카카오 알림 메시지 | 동일 채널, 동일 포맷 | - |

---

## 11. 검증 시나리오 (Do 후 Check 단계)

### 11.1 단위 회귀
```bash
python -m pytest tests/test_ops_metrics_false_consumed.py tests/test_ops_metrics_verification_logs.py tests/test_ops_anomaly.py -v
```
→ 6개 신규 테스트 모두 통과 + 기존 ops_anomaly 테스트 회귀 0

### 11.2 라이브 1회 실행
```bash
python -c "from src.application.services.ops_issue_detector import OpsIssueDetector; print(OpsIssueDetector().run_all_stores())"
```
→ 47863에서 false_consumed 0건 확인 (오늘 14건 정정 후 상태)
→ 04-07 검증 로그 4매장 분리 안 돼 있으니 missing 발견될 수 있음 (오늘 fix 이후 첫 실행이므로 historical)

### 11.3 04-09 23:55 자동 라이브
- false_consumed_post_guard: 4매장 모두 0건 → 정상
- verification_log_files: 4파일 모두 존재 → 정상
- 둘 중 하나라도 발생 시 카카오 알림 + `pending_issues.json` 등록 + `expiry-tracking.md` 자동 갱신

---

## 12. Plan 가설 검증 매핑

| Plan 가설 | Design 검증 |
|---|---|
| H1: 잡 시간 +30s 이내 | ✅ 신규 SQL 2개 + 파일 IO ≈ 200ms (안전 마진 충분) |
| H2: SQL 1회 1초 이내 | ✅ 단순 COUNT(*) + WHERE 인덱스(expiry_date) 활용 |
| H3: 23:55 잡에서 검증 로그 missing 정확 감지 | ✅ collect_system() 호출 1회로 처리 |

---

## 13. 다음 단계 (Do)

```bash
/pdca do ops-metrics-monitor-extension
```

**Do 작업 순서**:
1. `ops_metrics.py` 수정 (지표 함수 + collect_system)
2. `ops_anomaly.py` 수정 (체커 2개 + 매핑)
3. `ops_issue_detector.py` 3줄 추가
4. 회귀 테스트 3개 파일 작성
5. 단위 테스트 실행 → 6개 통과 확인
6. 라이브 1회 실행 → 47863 false_consumed 0건 확인
7. 커밋 + push
8. `expiry-tracking.md` 검증 체크포인트 자동 항목으로 표기 변경
