# Design: 발주 차이 분석 & 피드백 (order-diff-feedback)

> **생성일**: 2026-02-05
> **Plan 참조**: [order-diff-feedback.plan.md](order-diff-feedback.plan.md)
> **상태**: Completed

---

## 1. 아키텍처 개요

### 1.1 시스템 컨텍스트

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      발주 차이 분석 & 피드백 시스템                        │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  [Phase 2] 발주 실행 완료                                                │
│       │                                                                  │
│       ▼                                                                  │
│  ┌──────────────────────────────────────┐                                │
│  │         OrderDiffTracker             │ ← 오케스트레이터               │
│  │  ────────────────────────────────── │                                │
│  │  .save_snapshot()                    │ ← Day N: 발주 직후             │
│  │  .compare_and_save()                 │ ← Day N+1: 입고 수집 후       │
│  └───────────┬──────────────┬───────────┘                                │
│              │              │                                            │
│              ▼              ▼                                            │
│  ┌─────────────────┐  ┌─────────────────┐                               │
│  │ OrderDiffAnalyzer│  │order_analysis.db│ ← 분석 전용 DB               │
│  │ ───────────────  │  ├─────────────────┤                               │
│  │ .compare()       │  │ order_snapshots │                               │
│  │ .classify_diff() │  │ order_diffs     │                               │
│  │ (순수 로직)      │  │ order_diff_     │                               │
│  └─────────────────┘  │   summary       │                               │
│                        └────────┬────────┘                               │
│                                 │                                        │
│  [Day N+2~] 예측 시             │                                        │
│       │                         │                                        │
│       ▼                         ▼                                        │
│  ┌──────────────────────────────────────┐                                │
│  │       DiffFeedbackAdjuster           │                                │
│  │  ────────────────────────────────── │                                │
│  │  .get_removal_penalty(item_cd)       │ → order_qty 감소              │
│  │  .get_frequently_added_items()       │ → 발주 후보 주입              │
│  └──────────────────────────────────────┘                                │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 데이터 흐름 타임라인

```
[Day N - 07:00] 자동발주 실행
         │
         ├─ ImprovedPredictor.predict() → 예측량 산출
         │     └─ DiffFeedbackAdjuster.get_removal_penalty() → 페널티 적용
         │     └─ DiffFeedbackAdjuster.get_frequently_added_items() → 추가 주입
         │
         ├─ AutoOrderSystem.run_daily_order() → BGF 사이트 발주 입력
         │
         └─ OrderDiffTracker.save_snapshot() → order_snapshots 저장
              - order_list (예측 기반 발주 목록)
              - results (실행 결과: 성공/실패, 실제 수량)
              - eval_results (사전 평가: FORCE/URGENT/NORMAL/PASS/SKIP)

[Day N+1 ~ 08:00] 입고 데이터 수집
         │
         ├─ ReceivingCollector.collect_and_save() → receiving_history 저장
         │
         └─ OrderDiffTracker.compare_and_save()
              ├─ repo.get_snapshot_by_date() → 전일 스냅샷 조회
              ├─ OrderDiffAnalyzer.compare() → diff 생성
              ├─ repo.save_diffs() → order_diffs 저장
              └─ repo.save_summary() → order_diff_summary 저장
```

---

## 2. 컴포넌트 상세

### 2.1 OrderDiffAnalyzer (순수 로직)

**파일**: `src/analysis/order_diff_analyzer.py` (228줄)

I/O 없이 두 데이터 세트를 비교하여 차이를 생성하는 순수 함수.

**핵심 메서드**:

| 메서드 | 입력 | 출력 |
|--------|------|------|
| `compare()` | auto_snapshot, receiving_data | `{diffs, summary, unchanged_count}` |
| `classify_diff()` | auto_qty, confirmed_qty, recv_qty, in_auto, in_recv | diff_type 문자열 |

**비교 제외**: 1차, 2차, 신선 배송 상품 (receiving_history에 미기록)

**동일 상품 다전표 처리**: item_cd 기준으로 order_qty, receiving_qty 합산

**summary 계산**:
- `match_rate = unchanged_count / (total_auto_items + items_added)`
- `has_receiving_data = 1 if total_confirmed_items > 0 else 0`

### 2.2 OrderDiffTracker (오케스트레이터)

**파일**: `src/analysis/order_diff_tracker.py` (257줄)

Analyzer와 Repository를 연결. 모든 메서드는 내부에서 예외를 catch하여
메인 플로우(발주/입고 수집)를 절대 방해하지 않는다.

**핵심 메서드**:

| 메서드 | 호출 시점 | 역할 |
|--------|----------|------|
| `save_snapshot()` | 발주 실행 직후 | order_list + results + eval_results → order_snapshots |
| `compare_and_save()` | 입고 수집 직후 | snapshot vs receiving → order_diffs + summary |
| `compare_for_date()` | 백필/재분석 | DB에 있는 데이터로 재비교 (receiving_history 직접 조회) |

**Lazy Init**: `_repo`와 `_analyzer`는 프로퍼티로 첫 접근 시에만 생성.

**스냅샷 저장 시 delivery_type 판별**:
- `mid_cd in ALERT_CATEGORIES` → `get_delivery_type(item_nm)` (1차/2차/신선)
- 그 외 → "일반"

### 2.3 DiffFeedbackAdjuster (피드백 조정기)

**파일**: `src/prediction/diff_feedback.py` (195줄)

order_analysis.db의 diff 데이터를 분석하여 예측 수량을 조정.

**초기화 시점**: `ImprovedPredictor.__init__()` (line 249-256)

**피드백 적용 위치**:
- 제거 페널티: `improved_predictor.py:1540-1548` (12-1 단계)
- 추가 주입: `improved_predictor.py:2370-2382` (get_order_candidates)

**캐시 구조**:
```python
_removal_cache: {item_cd: {item_nm, mid_cd, removal_count, total_appearances}}
_addition_cache: {item_cd: {item_nm, mid_cd, addition_count, avg_qty}}
```

**핵심 메서드**:

| 메서드 | 역할 | 반환값 |
|--------|------|--------|
| `load_feedback()` | DB에서 14일 통계 로드 (1회) | void |
| `get_removal_penalty(item_cd)` | 제거 페널티 계수 | 0.3 ~ 1.0 |
| `get_addition_boost(item_cd)` | 추가 부스트 정보 | `{should_inject, boost_qty, count}` |
| `get_frequently_added_items()` | 반복 추가 상품 목록 | `[{item_cd, count, avg_qty}, ...]` |

### 2.4 OrderAnalysisRepository (DB 저장소)

**파일**: `src/infrastructure/database/repos/order_analysis_repo.py` (551줄)

BaseRepository를 상속하지 않고 독립 커넥션 관리.
`data/order_analysis.db` 파일은 첫 사용 시 자동 생성.

**CRUD 메서드**:

| 메서드 | 테이블 | 역할 |
|--------|--------|------|
| `save_order_snapshot()` | order_snapshots | 스냅샷 저장 (INSERT OR REPLACE) |
| `get_snapshot_by_date()` | order_snapshots | 발주일 기준 스냅샷 조회 |
| `save_diffs()` | order_diffs | 차이 상세 저장 |
| `save_summary()` | order_diff_summary | 일별 요약 저장 |

**분석 쿼리**:

| 메서드 | 용도 |
|--------|------|
| `get_most_modified_items()` | 가장 많이 수정된 상품 Top N |
| `get_modification_trend()` | 일별 수정 추이 |
| `get_category_modification_stats()` | 중분류별 수정 통계 |
| `get_item_removal_stats()` | 상품별 제거 통계 (피드백 페널티용) |
| `get_item_addition_stats()` | 상품별 추가 통계 (피드백 부스트용) |
| `get_filtered_match_rate()` | 유효 날짜만 평균 적중률 |

---

## 3. DB 스키마 상세

### 3.1 order_snapshots (17 컬럼)

```sql
CREATE TABLE IF NOT EXISTS order_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    order_date TEXT NOT NULL,
    item_cd TEXT NOT NULL,
    item_nm TEXT,
    mid_cd TEXT,
    predicted_qty INTEGER DEFAULT 0,
    recommended_qty INTEGER DEFAULT 0,
    final_order_qty INTEGER DEFAULT 0,
    current_stock INTEGER DEFAULT 0,
    pending_qty INTEGER DEFAULT 0,
    eval_decision TEXT,
    order_unit_qty INTEGER DEFAULT 1,
    order_success INTEGER DEFAULT 0,
    confidence TEXT,
    delivery_type TEXT,
    data_days INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(store_id, order_date, item_cd)
);
```

### 3.2 order_diffs (15 컬럼)

```sql
CREATE TABLE IF NOT EXISTS order_diffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    order_date TEXT NOT NULL,
    receiving_date TEXT NOT NULL,
    item_cd TEXT NOT NULL,
    item_nm TEXT,
    mid_cd TEXT,
    diff_type TEXT NOT NULL,
    auto_order_qty INTEGER DEFAULT 0,
    predicted_qty INTEGER DEFAULT 0,
    eval_decision TEXT,
    confirmed_order_qty INTEGER DEFAULT 0,
    receiving_qty INTEGER DEFAULT 0,
    qty_diff INTEGER DEFAULT 0,
    receiving_diff INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(store_id, order_date, item_cd, receiving_date)
);
```

### 3.3 order_diff_summary (16 컬럼)

```sql
CREATE TABLE IF NOT EXISTS order_diff_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    order_date TEXT NOT NULL,
    receiving_date TEXT NOT NULL,
    total_auto_items INTEGER DEFAULT 0,
    total_confirmed_items INTEGER DEFAULT 0,
    items_unchanged INTEGER DEFAULT 0,
    items_qty_changed INTEGER DEFAULT 0,
    items_added INTEGER DEFAULT 0,
    items_removed INTEGER DEFAULT 0,
    items_not_comparable INTEGER DEFAULT 0,
    total_auto_qty INTEGER DEFAULT 0,
    total_confirmed_qty INTEGER DEFAULT 0,
    total_receiving_qty INTEGER DEFAULT 0,
    match_rate REAL DEFAULT 0,
    has_receiving_data INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(store_id, order_date, receiving_date)
);
```

---

## 4. 연동 포인트

### 4.1 AutoOrderSystem (스냅샷 저장)

**파일**: `src/order/auto_order.py:1115-1129`

```python
# 발주 성공 후 (dry_run이 아닐 때)
if not dry_run and result.get('results'):
    from src.analysis.order_diff_tracker import OrderDiffTracker
    diff_tracker = OrderDiffTracker(store_id=self.store_id)
    snapshot_count = diff_tracker.save_snapshot(
        order_date=datetime.now().strftime("%Y-%m-%d"),
        order_list=order_list,
        results=result.get('results', []),
        eval_results=self._last_eval_results
    )
```

### 4.2 ReceivingCollector (비교 분석)

**파일**: `src/collectors/receiving_collector.py:664-683`

```python
# 입고 데이터 수집 완료 후
from src.analysis.order_diff_tracker import OrderDiffTracker
diff_tracker = OrderDiffTracker(store_id=self.store_id)
order_dates = {r['order_date'] for r in data if r.get('order_date')}
for od in order_dates:
    od_data = [r for r in data if r.get('order_date') == od]
    diff_result = diff_tracker.compare_and_save(
        order_date=od, receiving_data=od_data
    )
```

### 4.3 ImprovedPredictor (피드백 적용)

**파일**: `src/prediction/improved_predictor.py`

**초기화** (line 249-256):
```python
self._diff_feedback = None
if DIFF_FEEDBACK_ENABLED:
    from src.prediction.diff_feedback import DiffFeedbackAdjuster
    self._diff_feedback = DiffFeedbackAdjuster(store_id=self.store_id)
```

**제거 페널티 적용** (line 1540-1548):
```python
if self._diff_feedback and self._diff_feedback.enabled and order_qty > 0:
    penalty = self._diff_feedback.get_removal_penalty(item_cd)
    if penalty < 1.0:
        old_qty = order_qty
        order_qty = max(1, int(order_qty * penalty))
```

**추가 상품 주입** (line 2370-2382):
```python
if self._diff_feedback and self._diff_feedback.enabled:
    freq_added = self._diff_feedback.get_frequently_added_items()
    # 기존 발주 목록에 없는 상품만 주입
```

---

## 5. 설정 상수

**파일**: `src/settings/constants.py:265-278`

```python
DIFF_FEEDBACK_ENABLED = True              # 피드백 시스템 활성화
DIFF_FEEDBACK_LOOKBACK_DAYS = 14          # 분석 기간 (최근 N일)

DIFF_FEEDBACK_REMOVAL_THRESHOLDS = {      # 제거 페널티
    3: 0.7,    # 3회 이상 제거 → 수량 30% 감소
    6: 0.5,    # 6회 이상 제거 → 수량 50% 감소
    10: 0.3,   # 10회 이상 제거 → 수량 70% 감소
}

DIFF_FEEDBACK_ADDITION_MIN_COUNT = 3      # 최소 3회 추가 시 부스트
DIFF_FEEDBACK_ADDITION_MIN_QTY = 1        # 부스트 최소 발주 수량
```

---

**아카이브 완료**: 2026-02-19
