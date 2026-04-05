# ops-issue-pipeline Analysis Report

> **Analysis Type**: Gap Analysis (Design vs Implementation)
> **Date**: 2026-04-05
> **Design Doc**: [ops-issue-pipeline.design.md](../02-design/features/ops-issue-pipeline.design.md)

---

## 1. Overall Score

```
+---------------------------------------------+
|  Match Rate: 100% (85/85 items)  -- PASS    |
+---------------------------------------------+
|  Design Match:        100% (85/85)           |
|  Architecture:        100% (4/4 layers)      |
|  Error Handling:      100% (5/5 patterns)    |
|  Constants:           100% (7/7 values)      |
|  Tests (scope):       100% (38 passed)       |
|  Positive Additions:  10 items               |
+---------------------------------------------+
```

---

## 2. Category Scores

| Category | Items | Matched | Score | Status |
|----------|:-----:|:-------:|:-----:|:------:|
| ops_anomaly.py (Domain) | 20 | 20 | 100% | PASS |
| ops_metrics.py (Analysis) | 15 | 15 | 100% | PASS |
| issue_chain_writer.py (Infra) | 15 | 15 | 100% | PASS |
| ops_issue_detector.py (App) | 12 | 12 | 100% | PASS |
| run_scheduler.py 연동 | 5 | 5 | 100% | PASS |
| constants.py 상수 | 7 | 7 | 100% | PASS |
| 테스트 (2파일) | 6 | 6 | 100% | PASS |
| 에러 처리 | 5 | 5 | 100% | PASS |
| **Overall** | **85** | **85** | **100%** | **PASS** |

---

## 3. Gap Analysis Detail

### 3.1 ops_anomaly.py (Domain) -- 20/20

| # | Design 항목 | Status | 비고 |
|:-:|------------|:------:|------|
| 1 | 파일 위치 `src/domain/ops_anomaly.py` | MATCH | |
| 2 | OpsAnomaly dataclass | MATCH | + store_id 필드 추가 (긍정적) |
| 3-8 | 6개 필드 (metric_name~evidence) | MATCH | evidence에 default_factory 추가 |
| 9 | METRIC_TO_FILE 매핑 5개 | MATCH | 동일 |
| 10 | THRESHOLDS 상수 | MATCH | constants.py 7개 분리 (구조 개선) |
| 11 | detect_anomalies() 5개 checker | MATCH | |
| 12 | insufficient_data 스킵 | MATCH | |
| 13-17 | 5개 _check 함수 | MATCH | 임계값 동일 |
| 18 | _determine_priority() | MATCH | 각 _check 내부 inline (동작 동일) |
| 19-20 | P1 승격 조건 | MATCH | prediction 3개+, food 001~005 |

### 3.2 ops_metrics.py (Analysis) -- 15/15

| # | Design 항목 | Status | 비고 |
|:-:|------------|:------:|------|
| 21-23 | 클래스/생성자/collect_all | MATCH | |
| 24-29 | 5개 지표 메서드 | MATCH | waste: waste_slip_items 사용 (정확) |
| 30-31 | DB 접근 패턴 | MATCH | DBRouter + try/finally |
| 32-35 | 데이터 부족 처리 | MATCH | _MIN_DATA_DAYS = 7 |

### 3.3 issue_chain_writer.py (Infra) -- 15/15

| # | Design 항목 | Status | 비고 |
|:-:|------------|:------:|------|
| 36-39 | 클래스/상수/write_anomalies | MATCH | issues_dir DI 추가 (테스트 용이) |
| 40-43 | 4개 메서드 | MATCH | content:str 시그니처 (효율 개선) |
| 44-46 | 중복/쿨다운 로직 | MATCH | constants.py 상수 참조 |
| 47-50 | 삽입 위치/블록 형식 | MATCH | 자동 감지 태그 포함 |

### 3.4 ops_issue_detector.py (App) -- 12/12

| # | Design 항목 | Status | 비고 |
|:-:|------------|:------:|------|
| 51-55 | 클래스/메서드 5개 | MATCH | |
| 56 | 매장 간 중복 제거 | MATCH | title도 키에 포함 (개선) |
| 57-62 | 등록/동기화/알림/에러처리 | MATCH | |

### 3.5 run_scheduler.py -- 5/5

| # | Design 항목 | Status |
|:-:|------------|:------:|
| 63-67 | wrapper/스케줄/로그 | MATCH |

### 3.6 constants.py -- 7/7 (값 정확 일치)

### 3.7 에러 처리 -- 5/5 (모든 패턴 구현)

---

## 4. Positive Additions (설계 외 긍정적 추가)

| # | 항목 | 파일 | 효과 |
|:-:|------|------|------|
| A-1 | store_id 필드 | ops_anomaly.py | 매장 간 중복 제거 지원 |
| A-2 | --ops-detect CLI | run_scheduler.py | 수동 즉시 실행 가능 |
| A-3 | prev_7d=0 경계 처리 | ops_anomaly.py | 이전 0건 시 최근 3건+ 감지 |
| A-4 | _extract_keywords + 불용어 | issue_chain_writer.py | 중복 판정 정확도 향상 |
| A-5 | 최종 갱신일 자동 업데이트 | issue_chain_writer.py | 파일 헤더 날짜 자동 갱신 |
| A-6 | unique_anomalies 반환 | ops_issue_detector.py | 디버깅 용이 |
| A-7 | issues_dir 생성자 주입 | issue_chain_writer.py | 테스트 DI |
| A-8 | logger 추가 | ops_anomaly.py | 판정 실패 로그 |
| A-9 | evidence default_factory | ops_anomaly.py | 안전한 기본값 |
| A-10 | dedup 키에 title 포함 | ops_issue_detector.py | 세밀한 중복 제거 |

---

## 5. Intentional Deviations

| # | Design | Implementation | 판단 |
|:-:|--------|----------------|:----:|
| I-1 | THRESHOLDS 단일 dict | constants.py 7개 분리 | 구조 개선 |
| I-2 | _determine_priority 별도 함수 | 각 _check 내부 inline | 동작 동일 |
| I-3 | _is_duplicate(filepath) | _is_duplicate(content) | 효율 개선 |
| I-4 | collection 복수 유형 | 단일 유형 "sales" | DB 스키마 적응 |
| I-5 | dedup 키: metric+file | metric+file+title | 정확도 개선 |

---

## 6. Architecture Compliance

| Layer | File | I/O 제약 | Status |
|-------|------|---------|:------:|
| Domain | ops_anomaly.py | I/O 없음 | PASS |
| Analysis | ops_metrics.py | DB 조회만 | PASS |
| Infrastructure | issue_chain_writer.py | 파일 I/O만 | PASS |
| Application | ops_issue_detector.py | 오케스트레이션만 | PASS |

의존 방향 위반: 없음

---

## 7. Test Results

- **38개 전체 통과** (test_ops_anomaly 28개 + test_issue_chain_writer 10개)
- 커밋: `2de38e2`

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-04-05 | Initial gap analysis — Match Rate 100% |
