# Report: 발주 차이 분석 & 피드백 (order-diff-feedback)

> **완료일**: 2026-02-19
> **PDCA 주기**: Plan → Design → Do → Check → Act → Report → Archive
> **Match Rate**: 100%

---

## 1. 프로젝트 개요

### 1.1 배경

자동발주 시스템이 예측한 발주량과 사용자가 실제 확정한 발주량 간의 차이를
추적하지 않아, 시스템이 사용자의 수정 패턴을 학습할 수 없었음.

```
[Before]
예측 10개 → 발주 → 사용자 8개로 수정 → (추적 없음) → 다음날도 10개 예측

[After]
예측 10개 → 발주 → 사용자 8개로 수정 → diff 기록 → 페널티 0.7 → 다음날 7개 예측
```

### 1.2 목표 달성

| 목표 | 달성 여부 |
|------|----------|
| 자동발주 스냅샷 저장 | ✅ order_snapshots 테이블 |
| 입고 데이터와 비교하여 차이 분석 | ✅ OrderDiffAnalyzer (5가지 diff_type) |
| 반복 제거 상품 수량 자동 감소 | ✅ 제거 페널티 (0.3~1.0) |
| 반복 추가 상품 발주 후보 주입 | ✅ get_frequently_added_items() |
| 분석 쿼리 (대시보드/리포트용) | ✅ 6개 분석 쿼리 |
| 운영 DB 무영향 | ✅ 별도 order_analysis.db |

---

## 2. 구현 요약

### 2.1 아키텍처

```
┌───────────────────────────────────────────────────┐
│                 피드백 루프 시스템                   │
├───────────────────────────────────────────────────┤
│                                                     │
│  [Day N] 발주 실행 → OrderDiffTracker               │
│                       .save_snapshot()              │
│                       → order_snapshots 저장        │
│                                                     │
│  [Day N+1] 입고 수집 → OrderDiffTracker             │
│                         .compare_and_save()         │
│                         → OrderDiffAnalyzer         │
│                           .compare()                │
│                         → order_diffs 저장          │
│                         → order_diff_summary 저장   │
│                                                     │
│  [Day N+2~] 예측 → DiffFeedbackAdjuster             │
│                     .get_removal_penalty()          │
│                     → order_qty 감소                │
│                     .get_frequently_added_items()   │
│                     → 발주 후보 주입                 │
│                                                     │
└───────────────────────────────────────────────────┘
```

### 2.2 구현 규모

| 항목 | 수치 |
|------|------|
| 신규 모듈 | 4개 (Analyzer, Tracker, Adjuster, Repository) |
| 수정 파일 | 3개 (auto_order, receiving_collector, improved_predictor) |
| 총 코드량 | ~1,231줄 |
| 분석 전용 DB 테이블 | 3개 |
| 분석 쿼리 | 6개 |
| 설정 상수 | 5개 |

### 2.3 diff_type 분류 체계

| 유형 | 의미 | 피드백 |
|------|------|--------|
| unchanged | 시스템 발주 = 사용자 확정 | - (정상) |
| qty_changed | 사용자가 수량 변경 | 향후 qty_changed 기반 조정 가능 |
| added | 사용자가 상품 추가 | 3회 이상 → 발주 후보 자동 주입 |
| removed | 사용자가 상품 삭제 | 3회 이상 → 수량 페널티 적용 |
| receiving_diff | 확정 수량 ≠ 실제 입고량 | 입고 차이 모니터링 |

---

## 3. PDCA 이력

### 3.1 Phase Timeline

| Phase | 일시 | 산출물 | 결과 |
|-------|------|--------|------|
| Plan | 2026-02-05 | order-diff-feedback.plan.md | 요구사항 정의 완료 |
| Design | 2026-02-05 | order-diff-feedback.design.md | 상세 설계 완료 |
| Do | 2026-02-05 | 7개 파일 생성/수정 | 구현 완료 |
| Check | 2026-02-19 | order-diff-feedback.analysis.md | Match Rate 100% |
| Act | - | - | 수정 불필요 |
| Report | 2026-02-19 | 본 문서 | 완료 |
| Archive | 2026-02-19 | docs/archive/2026-02/ | 아카이브 완료 |

---

## 4. 핵심 설계 결정

### 4.1 분석 전용 DB 분리

운영 DB(stores/*.db)와 완전 분리된 `data/order_analysis.db` 사용.
- 운영 DB 성능/안정성에 영향 없음
- BaseRepository 미상속, 독립 커넥션 관리
- 스키마 자동 생성 (CREATE TABLE IF NOT EXISTS)

### 4.2 비차단 설계

모든 diff 관련 코드는 try/except로 감싸져 있어,
분석 DB 오류/파일 손상이 발생해도 메인 발주 플로우에 영향 없음.

### 4.3 Lazy Loading

DiffFeedbackAdjuster는 첫 접근 시에만 DB에서 데이터를 로드.
캐시 로드 실패 시에도 `_cache_loaded = True`로 설정하여 재시도 방지.

---

## 5. 결론

### 5.1 성과

- **문제 해결**: 자동발주 → 사용자 수정 → 예측 피드백의 학습 루프 구축
- **Match Rate**: 100% 달성
- **안전성**: 메인 플로우 비차단, 최소 발주량 보장
- **확장성**: 분석 쿼리 6개로 대시보드/리포트 연동 준비 완료

### 5.2 피드백 루프 효과

```
[학습 전]
사용자: 매일 진라면 10개 → 8개로 수정 (반복)

[학습 후 (14일 경과)]
시스템: 진라면 제거 5회 감지 → penalty=0.7
        10개 × 0.7 = 7개 발주 → 사용자 수정 빈도 감소
```

---

**보고서 작성 완료**: 2026-02-19
**아카이브 완료**: 2026-02-19
