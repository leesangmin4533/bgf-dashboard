# Plan: 발주 차이 분석 & 피드백 (order-diff-feedback)

> **생성일**: 2026-02-05
> **상태**: Completed
> **우선순위**: High

---

## 1. 개요

### 1.1 문제 정의

자동발주 시스템이 예측한 발주량과 사용자가 실제 확정한 발주량 사이의 차이를 추적하지 않아,
시스템이 사용자 행동 패턴을 학습할 수 없었음.

```
[기존 시스템 Gap]

예측 10개 ──→ 발주 실행 ──→ 사용자 8개로 수정 ──→ (추적 없음)
                                                    │
                                                    └→ 다음날도 10개 예측 (반복)
```

### 1.2 영향 범위

| 영향 항목 | 현재 | 문제 |
|----------|------|------|
| 사용자 수정 추적 | 미수집 | 반복 수정에도 예측 불변 |
| 예측 정확도 피드백 | 없음 | 자동 학습 불가 |
| 발주 적중률 측정 | 불가 | 개선 여부 판단 불가 |

### 1.3 목표

- 자동발주 스냅샷(예측 결과)을 발주 시점에 저장
- 입고 데이터와 비교하여 사용자 수정 패턴 분석
- 반복 제거 상품 → 수량 자동 감소 (precision 향상)
- 반복 추가 상품 → 발주 후보 자동 주입 (recall 향상)

---

## 2. 요구사항

### 2.1 기능 요구사항 (FR)

| ID | 요구사항 | 우선순위 |
|----|---------|---------|
| FR-01 | 자동발주 실행 직후 스냅샷 저장 (상품별 예측량, 발주량, 재고, 평가) | P0 |
| FR-02 | 입고 데이터 수집 후 스냅샷과 비교하여 차이 분류 (5가지 diff_type) | P0 |
| FR-03 | 일별 요약 (match_rate, 변경/추가/삭제 건수) 저장 | P0 |
| FR-04 | 반복 제거 상품 페널티 적용 (예측 수량 감소) | P0 |
| FR-05 | 반복 추가 상품 부스트 (발주 후보 자동 주입) | P1 |
| FR-06 | 분석 쿼리 제공 (가장 많이 수정된 상품, 일별 추이, 카테고리별 통계) | P1 |

### 2.2 비기능 요구사항 (NFR)

| ID | 요구사항 | 기준 |
|----|---------|------|
| NFR-01 | 메인 플로우 비차단 | 모든 예외 내부 catch, 실패해도 발주 정상 진행 |
| NFR-02 | 운영 DB 분리 | 별도 order_analysis.db 사용 (stores/*.db 무영향) |
| NFR-03 | Lazy Loading | DB 연결/데이터 로드는 실제 사용 시점에만 |

---

## 3. 구현 방안

### 3.1 3단계 피드백 루프

```
[Day N] 발주 실행
         │
         ▼
┌─────────────────────────────┐
│ 1. 스냅샷 저장              │
│    AutoOrderSystem          │
│    → OrderDiffTracker       │
│      .save_snapshot()       │
│    → order_snapshots 테이블 │
└──────────────┬──────────────┘
               │
[Day N+1] 입고 데이터 수집
               │
               ▼
┌─────────────────────────────┐
│ 2. 비교 분석                │
│    ReceivingCollector       │
│    → OrderDiffTracker       │
│      .compare_and_save()    │
│    → OrderDiffAnalyzer      │
│      .compare()             │
│    → order_diffs 테이블     │
│    → order_diff_summary     │
└──────────────┬──────────────┘
               │
[Day N+2~] 다음 예측 시
               │
               ▼
┌─────────────────────────────┐
│ 3. 피드백 적용              │
│    ImprovedPredictor        │
│    → DiffFeedbackAdjuster   │
│      .get_removal_penalty() │
│      .get_frequently_added()│
│    → order_qty 조정         │
└─────────────────────────────┘
```

### 3.2 수정 대상 파일

```
bgf_auto/
├── src/
│   ├── analysis/
│   │   ├── order_diff_analyzer.py    # 신규: 순수 비교 로직
│   │   └── order_diff_tracker.py     # 신규: 오케스트레이터
│   │
│   ├── prediction/
│   │   ├── diff_feedback.py          # 신규: 피드백 조정기
│   │   └── improved_predictor.py     # 수정: 피드백 적용
│   │
│   ├── infrastructure/database/repos/
│   │   └── order_analysis_repo.py    # 신규: 분석 전용 DB 저장소
│   │
│   ├── order/
│   │   └── auto_order.py             # 수정: 스냅샷 저장 트리거
│   │
│   ├── collectors/
│   │   └── receiving_collector.py    # 수정: 비교 분석 트리거
│   │
│   └── settings/
│       └── constants.py              # 수정: DIFF_FEEDBACK 상수 추가
│
└── data/
    └── order_analysis.db             # 신규: 분석 전용 DB (자동 생성)
```

---

## 4. DB 스키마

### 4.1 분석 전용 DB (data/order_analysis.db)

운영 DB(stores/*.db)와 완전 분리. 첫 사용 시 자동 생성.

#### order_snapshots

| 컬럼 | 타입 | 설명 |
|------|------|------|
| store_id | TEXT | 매장 코드 |
| order_date | TEXT | 발주일 |
| item_cd | TEXT | 상품 코드 |
| item_nm | TEXT | 상품명 |
| mid_cd | TEXT | 중분류 |
| predicted_qty | INTEGER | WMA 예측량 |
| recommended_qty | INTEGER | 권장 발주량 |
| final_order_qty | INTEGER | 최종 발주량 |
| current_stock | INTEGER | 현재 재고 |
| pending_qty | INTEGER | 미입고 수량 |
| eval_decision | TEXT | 사전평가 결과 |
| delivery_type | TEXT | 배송 차수 |
| data_days | INTEGER | 판매 데이터 일수 |
| **UNIQUE** | | **(store_id, order_date, item_cd)** |

#### order_diffs

| 컬럼 | 타입 | 설명 |
|------|------|------|
| store_id | TEXT | 매장 코드 |
| order_date | TEXT | 발주일 |
| receiving_date | TEXT | 입고일 |
| item_cd | TEXT | 상품 코드 |
| diff_type | TEXT | 차이 유형 (5가지) |
| auto_order_qty | INTEGER | 시스템 발주량 |
| confirmed_order_qty | INTEGER | 확정 발주량 |
| receiving_qty | INTEGER | 실제 입고량 |
| qty_diff | INTEGER | confirmed - auto (양수=증가, 음수=감소) |
| **UNIQUE** | | **(store_id, order_date, item_cd, receiving_date)** |

#### order_diff_summary

| 컬럼 | 타입 | 설명 |
|------|------|------|
| store_id | TEXT | 매장 코드 |
| order_date | TEXT | 발주일 |
| items_unchanged | INTEGER | 변경 없는 상품 수 |
| items_qty_changed | INTEGER | 수량 변경 상품 수 |
| items_added | INTEGER | 사용자 추가 상품 수 |
| items_removed | INTEGER | 사용자 삭제 상품 수 |
| match_rate | REAL | 적중률 (unchanged / total) |
| has_receiving_data | INTEGER | 입고 데이터 유무 (0/1) |
| **UNIQUE** | | **(store_id, order_date, receiving_date)** |

---

## 5. 핵심 알고리즘

### 5.1 차이 분류 (classify_diff)

```
auto_snapshot    receiving_data    결과
─────────────    ──────────────    ──────────────
있음 (10개)      없음              removed (사용자 삭제)
없음             있음 (5개)        added (사용자 추가)
10개             8개               qty_changed (수량 변경)
10개             10개 (입고 8개)   receiving_diff (입고 차이)
10개             10개 (입고 10개)  unchanged (일치)
```

### 5.2 비교 제외 대상

1차, 2차, 신선 배송 상품은 receiving_history에 기록되지 않으므로 비교 대상에서 제외.
"일반"(비푸드 센터매입)과 "ambient"만 비교.

### 5.3 피드백 페널티

| 14일 내 제거 횟수 | 페널티 | 효과 |
|-------------------|--------|------|
| 3~5회 | 0.7 | 30% 감소 |
| 6~9회 | 0.5 | 50% 감소 |
| 10+회 | 0.3 | 70% 감소 |

적용: `order_qty = max(1, int(order_qty * penalty))`

### 5.4 피드백 부스트

14일 내 3회 이상 사용자가 추가한 상품 → 발주 후보에 자동 주입.
권장 수량: 과거 추가 평균 수량.

---

## 6. 설정 상수

```python
DIFF_FEEDBACK_ENABLED = True                # 피드백 시스템 활성화
DIFF_FEEDBACK_LOOKBACK_DAYS = 14            # 분석 기간 (최근 N일)
DIFF_FEEDBACK_REMOVAL_THRESHOLDS = {
    3: 0.7,    # 3회 이상 제거 → 수량 30% 감소
    6: 0.5,    # 6회 이상 제거 → 수량 50% 감소
    10: 0.3,   # 10회 이상 제거 → 수량 70% 감소
}
DIFF_FEEDBACK_ADDITION_MIN_COUNT = 3        # 최소 3회 추가 시 부스트
DIFF_FEEDBACK_ADDITION_MIN_QTY = 1          # 부스트 최소 발주 수량
```

---

## 7. 리스크 및 대응

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 입고 데이터 누락 | 비교 불가 | has_receiving_data 플래그로 유효 데이터만 분석 |
| 분석 DB 파일 손상 | 피드백 중단 | lazy init + 예외 전부 catch → 메인 플로우 무영향 |
| 과도한 페널티 | 발주 부족 | max(1, ...) 보장, 14일 rolling window |

---

## 8. 승인

- [x] 기획 검토 완료
- [x] 기술 검토 완료
- [x] 구현 착수 승인

---

**아카이브 완료**: 2026-02-19
