# Analysis: 발주 차이 분석 & 피드백 (order-diff-feedback)

> **분석일**: 2026-02-19
> **Design 참조**: [order-diff-feedback.design.md](order-diff-feedback.design.md)
> **상태**: Completed

---

## 1. Gap Analysis 결과

### 1.1 Match Rate

| 항목 | 설계 | 구현 | 일치 |
|------|------|------|------|
| OrderDiffAnalyzer.compare() | 순수 비교 로직 | `src/analysis/order_diff_analyzer.py` | ✅ |
| OrderDiffAnalyzer.classify_diff() | 5가지 diff_type 분류 | 구현됨 (unchanged/qty_changed/added/removed/receiving_diff) | ✅ |
| 1차/2차/신선 비교 제외 | _NON_COMPARABLE 필터 | `{"1차", "2차", "신선"}` set 필터 | ✅ |
| 동일 상품 다전표 합산 | recv_map 합산 로직 | order_qty + receiving_qty 합산 | ✅ |
| OrderDiffTracker.save_snapshot() | 발주 후 스냅샷 저장 | `src/analysis/order_diff_tracker.py:49-137` | ✅ |
| OrderDiffTracker.compare_and_save() | 입고 후 비교 분석 | `src/analysis/order_diff_tracker.py:139-189` | ✅ |
| OrderDiffTracker.compare_for_date() | 백필/재분석 | `src/analysis/order_diff_tracker.py:191-257` | ✅ |
| 예외 내부 catch (메인 플로우 비차단) | try/except + logger.debug | 3개 메서드 모두 적용 | ✅ |
| DiffFeedbackAdjuster 초기화 | ImprovedPredictor.__init__() | `improved_predictor.py:249-256` | ✅ |
| get_removal_penalty() | 제거 페널티 계수 반환 | `diff_feedback.py:104-131` | ✅ |
| get_addition_boost() | 추가 부스트 정보 반환 | `diff_feedback.py:133-164` | ✅ |
| get_frequently_added_items() | 반복 추가 상품 목록 | `diff_feedback.py:166-194` | ✅ |
| 제거 페널티 적용 위치 | predict() 12-1 단계 | `improved_predictor.py:1540-1548` | ✅ |
| 추가 주입 적용 위치 | get_order_candidates() | `improved_predictor.py:2370-2382` | ✅ |
| order_analysis.db 스키마 | 3개 테이블 (snapshots/diffs/summary) | `order_analysis_repo.py:25-103` | ✅ |
| 분석 전용 DB 분리 | data/order_analysis.db | BaseRepository 미상속, 독립 관리 | ✅ |
| Lazy Init | 프로퍼티 기반 지연 로딩 | Tracker: _repo/_analyzer, Adjuster: _cache_loaded | ✅ |
| AutoOrderSystem 연동 | 발주 후 save_snapshot() | `auto_order.py:1115-1129` | ✅ |
| ReceivingCollector 연동 | 입고 후 compare_and_save() | `receiving_collector.py:664-683` | ✅ |
| DIFF_FEEDBACK 상수 | 6개 상수 정의 | `constants.py:265-278` | ✅ |
| 분석 쿼리 6개 | most_modified, trend, category, removal, addition, match_rate | `order_analysis_repo.py:369-550` | ✅ |

**Match Rate: 100%** (21/21 완전 일치)

---

## 2. 구현 완료 항목

### 2.1 핵심 모듈 (4개)

| 모듈 | 파일 | 줄 수 | 상태 |
|------|------|-------|------|
| OrderDiffAnalyzer | `src/analysis/order_diff_analyzer.py` | 228 | ✅ |
| OrderDiffTracker | `src/analysis/order_diff_tracker.py` | 257 | ✅ |
| DiffFeedbackAdjuster | `src/prediction/diff_feedback.py` | 195 | ✅ |
| OrderAnalysisRepository | `src/infrastructure/database/repos/order_analysis_repo.py` | 551 | ✅ |

### 2.2 연동 수정 (3개 파일)

| 파일 | 수정 위치 | 역할 | 상태 |
|------|----------|------|------|
| `auto_order.py` | 1115-1129 | 스냅샷 저장 트리거 | ✅ |
| `receiving_collector.py` | 664-683 | 비교 분석 트리거 | ✅ |
| `improved_predictor.py` | 249-256, 1540-1548, 2370-2382 | 피드백 적용 | ✅ |

### 2.3 설정 상수

| 상수 | 값 | 상태 |
|------|------|------|
| DIFF_FEEDBACK_ENABLED | True | ✅ |
| DIFF_FEEDBACK_LOOKBACK_DAYS | 14 | ✅ |
| DIFF_FEEDBACK_REMOVAL_THRESHOLDS | {3: 0.7, 6: 0.5, 10: 0.3} | ✅ |
| DIFF_FEEDBACK_ADDITION_MIN_COUNT | 3 | ✅ |
| DIFF_FEEDBACK_ADDITION_MIN_QTY | 1 | ✅ |

---

## 3. 설계 특징 검증

### 3.1 안전성

| 항목 | 설계 의도 | 구현 확인 |
|------|----------|----------|
| 메인 플로우 비차단 | 모든 예외 catch | ✅ save_snapshot, compare_and_save, compare_for_date 전부 try/except |
| 최소 발주량 보장 | max(1, ...) | ✅ `order_qty = max(1, int(order_qty * penalty))` |
| 피드백 모듈 미설치 허용 | import 실패 시 무시 | ✅ `except Exception: pass` |
| 캐시 재시도 방지 | _cache_loaded = True | ✅ 로드 실패 시에도 True 설정 |

### 3.2 DB 격리

| 항목 | 확인 |
|------|------|
| 운영 DB (stores/*.db) 무영향 | ✅ 별도 order_analysis.db 사용 |
| BaseRepository 미상속 | ✅ 독립 커넥션 관리 |
| 스키마 자동 생성 | ✅ CREATE TABLE IF NOT EXISTS |
| 중복 방지 | ✅ INSERT OR REPLACE + UNIQUE 제약 |

---

## 4. 테스트 검증 상태

| 테스트 대상 | 상태 |
|------------|------|
| OrderDiffAnalyzer.compare() | ✅ 단위 테스트 통과 |
| OrderDiffAnalyzer.classify_diff() | ✅ 5가지 케이스 검증 |
| OrderDiffTracker.save_snapshot() | ✅ 통합 테스트 통과 |
| OrderDiffTracker.compare_and_save() | ✅ 통합 테스트 통과 |
| DiffFeedbackAdjuster.get_removal_penalty() | ✅ 임계값별 검증 |
| DiffFeedbackAdjuster.get_frequently_added_items() | ✅ 필터/정렬 검증 |
| OrderAnalysisRepository CRUD | ✅ 저장/조회 검증 |
| 전체 테스트 스위트 | ✅ 1312개 통과 (2026-02-19) |

---

## 5. 결론

### Match Rate: **100%**

설계와 구현이 완전히 일치. 4개 신규 모듈 + 3개 기존 파일 수정으로
자동발주 → 사용자 수정 추적 → 예측 피드백의 학습 루프가 완성됨.

---

**분석 완료**: 2026-02-19
**아카이브 완료**: 2026-02-19
