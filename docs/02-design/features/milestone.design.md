# Design: 3단계 마일스톤 — 자동 측정 + 리포트 (milestone)

> 작성일: 2026-04-05
> Plan 참조: `docs/01-plan/features/milestone.plan.md`

---

## 1. 구현 개요

기존 `OpsIssueDetector`(악화 감지)에 **마일스톤 트래커**(목표 달성 감지)를 추가.
매주 일요일 KPI 4개를 측정하고, 달성 여부를 카카오로 리포트.

### 기존 시스템과의 관계

```
기존 (매일 23:55)                       신규 (매주 일요일 00:00)
─────────────────                       ──────────────────────
OpsMetrics.collect_all()                MilestoneTracker.evaluate()
  → 5개 지표 원시 데이터 수집              → 같은 데이터를 KPI 관점에서 판정
  ↓                                       ↓
detect_anomalies()                      _judge_kpis()
  → "나빠졌나?" (비율 기반)               → "목표에 도달했나?" (절대값 기반)
  ↓                                       ↓
IssueChainWriter                        milestone_snapshots 테이블 저장
  → [PLANNED] 이슈 등록                   → 주간 스냅샷 누적
  ↓                                       ↓
카카오: "[운영 이상 감지]"               카카오: "[마일스톤 주간 리포트]"
```

**핵심 원칙**: `OpsMetrics`의 데이터를 **재사용**. 새 DB 쿼리 최소화.

---

## 2. 파일 구조

```
변경 파일:
  src/settings/constants.py          # 마일스톤 상수 추가
  src/analysis/ops_metrics.py        # K3용 total_order_count 추가
  src/notification/kakao_notifier.py # ALLOWED_CATEGORIES에 "milestone" 추가
  run_scheduler.py                   # 주간 스케줄 등록
  src/infrastructure/database/schema.py  # milestone_snapshots DDL

신규 파일:
  src/analysis/milestone_tracker.py  # ★ 핵심: KPI 측정 + 판정 + 리포트
```

---

## 3. 상세 설계

### 3.1 constants.py — 마일스톤 상수

```python
# === 마일스톤 KPI 목표 (2주 실측 후 조정) ===
MILESTONE_TARGETS = {
    "K1_prediction_stability": 1.05,    # mae_7d/mae_14d 비율 상한
    "K2_waste_rate_food": 0.03,         # food 폐기율 상한 (3%)
    "K2_waste_rate_total": 0.02,        # 전체 폐기율 상한 (2%)
    "K3_order_failure_rate": 0.05,      # 발주 실패율 상한 (5%)
    "K4_integrity_max_consecutive": 3,  # 무결성 연속 이상일 상한
}

MILESTONE_APPROACHING_RATIO = 1.2  # 목표의 120% 이내면 APPROACHING
MILESTONE_COMPLETION_WEEKS = 2     # 연속 달성 주 수 (2주 연속이면 완료)
```

### 3.2 milestone_tracker.py — 핵심 모듈

**위치**: `src/analysis/milestone_tracker.py`
**계층**: Analysis (순수 로직 + DB 읽기)

```python
class MilestoneTracker:
    """주간 마일스톤 KPI 측정 + 판정"""

    def evaluate(self) -> dict:
        """전체 매장 KPI 측정 → 판정 → 저장 → 리포트 메시지 반환"""
        # 1. 전 매장 지표 수집 (기존 OpsMetrics 재사용)
        # 2. KPI별 판정 (ACHIEVED / APPROACHING / NOT_MET)
        # 3. milestone_snapshots에 저장
        # 4. 연속 달성 주 계산
        # 5. 카카오 리포트 텍스트 생성
```

#### 메서드 구조

```python
def evaluate(self) -> dict:
    """메인 진입점. 4매장 평균 KPI 계산 → 판정 → 저장"""

def _collect_all_stores(self) -> dict:
    """전 매장 OpsMetrics 수집 → 매장별 원시 데이터 dict"""

def _calculate_k1(self, all_metrics: dict) -> dict:
    """K1 예측 안정성: 전 매장 카테고리별 mae_7d/mae_14d 평균"""
    # 기존 OpsMetrics._prediction_accuracy() 데이터 재사용
    # 반환: {"value": 1.08, "status": "APPROACHING", "detail": "..."}

def _calculate_k2(self, all_metrics: dict) -> dict:
    """K2 폐기율: 전 매장 food/전체 폐기율"""
    # 기존 OpsMetrics._waste_rate() 데이터 재사용
    # food (001~005) 별도 집계
    # 반환: {"food": 0.028, "total": 0.015, "status": "ACHIEVED"}

def _calculate_k3(self, all_metrics: dict) -> dict:
    """K3 발주 실패율: fail_count_7d / total_order_7d"""
    # 기존 order_failure 데이터의 recent_7d + 신규 total_order 쿼리
    # 반환: {"value": 0.042, "status": "ACHIEVED"}

def _calculate_k4(self, all_metrics: dict) -> dict:
    """K4 무결성: max(consecutive_anomaly_days) 전 체크"""
    # 기존 OpsMetrics._integrity_unresolved() 데이터 재사용
    # 반환: {"value": 2, "status": "ACHIEVED"}

def _judge(self, value: float, target: float, lower_is_better: bool = True) -> str:
    """단일 KPI 판정 → ACHIEVED / APPROACHING / NOT_MET"""
    # lower_is_better=True: value <= target → ACHIEVED
    # APPROACHING: value <= target * MILESTONE_APPROACHING_RATIO

def _save_snapshot(self, kpis: dict) -> None:
    """milestone_snapshots 테이블에 주간 스냅샷 저장"""

def _count_consecutive_achieved(self) -> int:
    """milestone_snapshots에서 연속 전체 달성 주 수 계산"""

def _build_report_message(self, kpis: dict, consecutive: int) -> str:
    """카카오 리포트 텍스트 생성"""
```

#### 판정 로직

```python
def _judge(self, value, target, lower_is_better=True):
    if lower_is_better:
        if value <= target:
            return "ACHIEVED"
        elif value <= target * MILESTONE_APPROACHING_RATIO:
            return "APPROACHING"
        else:
            return "NOT_MET"
    else:  # higher_is_better (현재 미사용, 4단계 대비)
        if value >= target:
            return "ACHIEVED"
        elif value >= target / MILESTONE_APPROACHING_RATIO:
            return "APPROACHING"
        else:
            return "NOT_MET"
```

### 3.3 ops_metrics.py — K3 데이터 보강

현재 `_order_failure()`는 실패 건수만 반환. K3 계산에는 **총 발주 건수**도 필요.

```python
# 기존
def _order_failure(self) -> dict:
    return {"recent_7d": recent_7d, "prev_7d": prev_7d}

# 변경: total_order_7d 추가
def _order_failure(self) -> dict:
    # ... 기존 로직 유지 ...
    # 추가: 최근 7일 총 발주 건수 (order_history 테이블)
    cursor.execute("""
        SELECT COUNT(DISTINCT item_cd) as cnt
        FROM order_history
        WHERE order_date >= date('now', '-7 days')
    """)
    total_7d = cursor.fetchone()["cnt"]

    return {"recent_7d": recent_7d, "prev_7d": prev_7d, "total_order_7d": total_7d}
```

**하위호환**: `detect_anomalies()`는 `recent_7d`, `prev_7d`만 사용하므로 영향 없음.

### 3.4 DB 스키마 — milestone_snapshots

**위치**: common.db (매장 무관, 전체 평균)

```sql
CREATE TABLE IF NOT EXISTS milestone_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL,           -- '2026-04-13'
    stage TEXT NOT NULL DEFAULT '3',       -- 현재 단계 ('3', '4', ...)
    k1_value REAL,                         -- mae_7d/mae_14d 평균
    k1_status TEXT,                         -- ACHIEVED/APPROACHING/NOT_MET
    k2_food_value REAL,                    -- food 폐기율
    k2_total_value REAL,                   -- 전체 폐기율
    k2_status TEXT,
    k3_value REAL,                         -- 발주 실패율
    k3_status TEXT,
    k4_value REAL,                         -- max consecutive days
    k4_status TEXT,
    all_achieved INTEGER DEFAULT 0,        -- K1~K4 전부 ACHIEVED면 1
    consecutive_weeks INTEGER DEFAULT 0,   -- 연속 전체 달성 주 수
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(snapshot_date, stage)
);
```

**SCHEMA_MIGRATIONS에 추가**: `DB_SCHEMA_VERSION` + 1

### 3.5 kakao_notifier.py — 카테고리 추가

```python
# 변경 전
ALLOWED_CATEGORIES = {"food_expiry", "promotion", "receiving_mismatch", "test"}

# 변경 후
ALLOWED_CATEGORIES = {"food_expiry", "promotion", "receiving_mismatch", "milestone", "test"}
```

### 3.6 run_scheduler.py — 주간 스케줄

```python
# 17. 마일스톤 주간 리포트 (매주 일요일 00:00)
schedule.every().sunday.at("00:00").do(milestone_report_wrapper)

def milestone_report_wrapper() -> None:
    """마일스톤 KPI 측정 → 판정 → 카카오 리포트"""
    try:
        from src.analysis.milestone_tracker import MilestoneTracker
        result = MilestoneTracker().evaluate()

        if result.get("all_achieved") and result.get("consecutive_weeks", 0) >= MILESTONE_COMPLETION_WEEKS:
            logger.info(f"[Milestone] 3단계 완료! 연속 {result['consecutive_weeks']}주 달성")
        else:
            logger.info(f"[Milestone] 달성 {result.get('achieved_count', 0)}/4")

        # 카카오 리포트 발송
        message = result.get("report_message", "")
        if message:
            from src.notification.kakao_notifier import KakaoNotifier
            notifier = KakaoNotifier()
            notifier.send_message(message, category="milestone")
    except Exception as e:
        logger.warning(f"[Milestone] 실패 (무시): {e}")
```

---

## 4. 리포트 메시지 형식

### 일반 주간 리포트

```
[마일스톤 주간 리포트] 04-13

K1 예측 안정성: 1.08 / 1.05  ⚠ 근접
K2 폐기율: food 2.8%/3% ✓ | 전체 1.5%/2% ✓
K3 발주 실패: 4.2% / 5%  ✓
K4 무결성: 2일 / 3일  ✓

달성: 3/4 | 연속: 0주/2주
→ 다음: ML is_payday 검증 (K1)
```

### 3단계 완료 리포트

```
[마일스톤] 3단계 완료!

K1~K4 전부 ACHIEVED 2주 연속 달성.
3단계 '발주 품질 개선' 완료.
→ 4단계 '수익성 최적화' 계획 수립 필요
```

---

## 5. 데이터 흐름

```
매주 일요일 00:00
  │
  ├─ MilestoneTracker.evaluate()
  │    │
  │    ├─ 1. _collect_all_stores()
  │    │    └─ 4매장 × OpsMetrics.collect_all()  ← 기존 코드 100% 재사용
  │    │
  │    ├─ 2. _calculate_k1 ~ k4()
  │    │    └─ 원시 데이터 → KPI 값 + 판정
  │    │    └─ K3만 order_history 추가 쿼리 (total_order_7d)
  │    │
  │    ├─ 3. _save_snapshot()
  │    │    └─ common.db milestone_snapshots INSERT
  │    │
  │    ├─ 4. _count_consecutive_achieved()
  │    │    └─ milestone_snapshots에서 역순 조회
  │    │
  │    └─ 5. _build_report_message()
  │         └─ 리포트 텍스트 생성
  │
  └─ run_scheduler.py
       └─ KakaoNotifier.send_message(category="milestone")
```

---

## 6. 구현 순서

| 순서 | 작업 | 파일 | 예상 규모 |
|------|------|------|----------|
| 1 | 상수 추가 | constants.py | 10줄 |
| 2 | DB 스키마 추가 | schema.py + models.py | 20줄 |
| 3 | ops_metrics 보강 | ops_metrics.py | 10줄 (total_order_7d) |
| 4 | **milestone_tracker.py 신규** | src/analysis/ | ~150줄 |
| 5 | 카카오 카테고리 추가 | kakao_notifier.py | 1줄 |
| 6 | 스케줄러 등록 | run_scheduler.py | 20줄 |
| 7 | 테스트 | tests/test_milestone_tracker.py | ~100줄 |

**총 변경량**: 신규 ~250줄 + 기존 수정 ~40줄

---

## 7. 테스트 전략

### 단위 테스트 (test_milestone_tracker.py)

```python
# K1~K4 각 판정 로직
def test_judge_achieved():     # value <= target → ACHIEVED
def test_judge_approaching():  # value <= target * 1.2 → APPROACHING
def test_judge_not_met():      # value > target * 1.2 → NOT_MET

# KPI 계산
def test_calculate_k1_stable():     # mae 비율 1.03 → ACHIEVED
def test_calculate_k1_unstable():   # mae 비율 1.15 → APPROACHING
def test_calculate_k2_food_high():  # food 폐기율 4% → NOT_MET
def test_calculate_k3_low_failure():  # 실패율 3% → ACHIEVED
def test_calculate_k4_clean():      # 연속 0일 → ACHIEVED

# 연속 달성 주 카운트
def test_consecutive_weeks_two():    # 2주 연속 → 완료 조건 충족
def test_consecutive_weeks_broken(): # 중간에 실패 → 0으로 리셋

# 리포트 메시지
def test_report_message_format():    # 카카오 메시지 형식 검증
def test_completion_message():       # 3단계 완료 메시지

# insufficient_data 처리
def test_skip_store_with_no_data():  # 데이터 부족 매장 제외
```

### 통합 테스트

```python
def test_evaluate_full_flow():  # collect → calculate → save → report 전체 흐름
def test_snapshot_saved_to_db(): # milestone_snapshots 정합성
```

---

## 8. 목표값 조정 절차 (04-19 실행)

1. 04-06 ~ 04-19 (2주간) 매일 수동으로 `MilestoneTracker.evaluate()` 실행 (dry-run, 저장만 하고 카카오 미발송)
2. 04-19에 `milestone_snapshots` 14건의 실측값 확인
3. 각 KPI의 **중앙값**을 기준으로 목표 재설정:
   - 중앙값보다 **10~20% 개선**된 값을 목표로 설정
   - 이미 달성 중인 KPI는 현재 수준 유지
4. `constants.py` `MILESTONE_TARGETS` 갱신
5. 이슈체인 `scheduling.md`에 조정 근거 기록

---

## 9. 하위호환 보장

| 변경 | 기존 동작 영향 |
|------|---------------|
| ops_metrics.py `total_order_7d` 추가 | `detect_anomalies()`는 이 필드 무시 → 영향 없음 |
| constants.py 상수 추가 | 기존 상수 변경 없음 → 영향 없음 |
| ALLOWED_CATEGORIES 추가 | 기존 카테고리 유지 → 영향 없음 |
| common.db 테이블 추가 | 기존 테이블 변경 없음 → 영향 없음 |
| 스케줄러 작업 추가 | 기존 작업 변경 없음 → 영향 없음 |
