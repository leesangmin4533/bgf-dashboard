# PDCA Completion Report: harness-engineering

> 하네스 엔지니어링 — SKIP 사유 저장 + AI 요약 + integrity 연동

## 1. 개요

| 항목 | 내용 |
|------|------|
| Feature | harness-engineering |
| 기간 | 2026-03-28 (Week 1~3) |
| Commits | `34a756e` (W1) → `b204a27` (W2) → `5d949bb` (W3) |
| Match Rate | **98.6%** (73/74 항목) |
| 테스트 | 21개 PASS (12 + 9) |
| DB 버전 | v67 → v68 |

## 2. 해결한 문제

| 문제 | 해결 |
|------|------|
| SKIP 사유 미저장 (Python 객체에서 소실) | eval_outcomes에 reason/skip_reason/skip_detail 영구 저장 |
| integrity 체크 후 인사이트 부재 | 규칙 기반 자동 요약 생성 (AI API 전환 가능) |
| AI가 볼 수 있는 구조화 데이터 없음 | ai_summaries 테이블에 정형화된 요약 저장 |

## 3. 구현 내역

### Module 1: SKIP 사유 저장 (Week 1+2)

| 파일 | 변경 |
|------|------|
| `src/prediction/skip_reason.py` | NEW: 4개 코드 (SKIP_LOW_POPULARITY, SKIP_UNAVAILABLE, PASS_STOCK_SUFFICIENT, PASS_DATA_INSUFFICIENT) |
| `src/prediction/pre_order_evaluator.py` | PreOrderEvalResult에 skip_reason/skip_detail 필드 + SKIP/PASS 판정 시 기록 |
| `src/prediction/eval_calibrator.py` | batch 조립에 reason/skip_reason/skip_detail 추가 (getattr 패턴) |
| `src/infrastructure/database/repos/eval_outcome_repo.py` | INSERT 3컬럼 + get_skip_stats_by_reason() + get_dangerous_skips() |

**저장 경로**: PreOrderEvaluator → EvalCalibrator batch → eval_outcome_repo → DB

### Module 2: AI 요약 서비스 (Week 3)

| 파일 | 변경 |
|------|------|
| `src/infrastructure/database/repos/ai_summary_repo.py` | NEW: common DB CRUD (upsert, 조회, 비용추적) |
| `src/application/services/ai_summary_service.py` | NEW: rule_based 요약 + API 폴백 + 비용 상한 |

**요약 내용**: integrity 이상 건수 + 어제 대비 트렌드 + 위험 SKIP(판매 중인데 발주 제외) + SKIP 통계

### Module 3: integrity 연동 (Week 3)

| 파일 | 변경 |
|------|------|
| `src/application/services/data_integrity_service.py` | _run_ai_summary(store_id, results) 추가 (try/except 안전) |

**실행 흐름**: Phase 1.67 → 6개 체크 → anomaly > 0? → AI 요약 → ai_summaries 저장

### DB 마이그레이션 (Week 1)

| 대상 | 변경 |
|------|------|
| 매장 DB (3개) | eval_outcomes에 reason/skip_reason/skip_detail + 인덱스 |
| 공용 DB | ai_summaries 테이블 + 인덱스 |
| DB_SCHEMA_VERSION | 67 → 68 |

## 4. 설계 검증 과정

| 단계 | 내용 |
|------|------|
| 설계서 v1.0 | 클로드 AI 챗에서 작성 |
| 코드 대조 1차 | 불일치 7건 발견 (치명 4건) |
| 설계서 v2.0 | 불일치 반영 |
| 코드 대조 2차 | 잔여 불일치 7건 (치명 4건) |
| 설계서 v2.1 | 잔여 반영 |
| 코드 대조 3차 | 잔여 1건 (eval_calibrator 누락) |
| **설계서 v2.2 (최종)** | **불일치 0건** |

## 5. Gap 분석 결과

| Gap | 심각도 | 내용 | 영향 |
|-----|--------|------|------|
| G-1 | Low | __all__ 리스트 누락 | 런타임 영향 없음 |
| I-1 | 의도적 | 마이그레이션 방식 (SCHEMA_MIGRATIONS → _STORE_COLUMN_PATCHES) | 프로젝트 표준, 더 안전 |

## 6. 테스트

| 파일 | 건수 | 내용 |
|------|:----:|------|
| test_harness_skip_reason.py | 12 | SkipReason 코드, PreOrderEvalResult 필드, batch 포함, DB 저장 |
| test_harness_ai_summary.py | 9 | repo UPSERT, 요약 생성, anomaly=0 스킵, 실패 무해성, 연동 호출 |
| **합계** | **21** | 기존 eval 153개와 함께 전부 PASS |

## 7. 에러 처리

모든 AI/요약 코드는 발주 파이프라인을 **절대 중단하지 않음**:
- AISummaryService: 내부 try/except → None 반환
- DataIntegrityService._run_ai_summary: try/except → logger.error
- API 실패/미설치/비용 초과: 자동 rule_based 폴백

## 8. 보안 및 비용

| 항목 | 내용 |
|------|------|
| 기본 모드 | rule_based ($0/월) |
| API 모드 | claude-haiku (~$0.30/월, BGF 외부전송 확인 후) |
| 비용 상한 | $0.5/일 초과 시 자동 rule_based 전환 |
| 토글 | AI_SUMMARY_ENABLED (.env) |

## 9. 수정 파일 총 10개

```
NEW (3):  skip_reason.py, ai_summary_repo.py, ai_summary_service.py
수정 (7): schema.py, constants.py, pre_order_evaluator.py,
          eval_calibrator.py, eval_outcome_repo.py,
          data_integrity_service.py, repos/__init__.py
```

## 10. 학습 사항

- **설계서 코드 대조 3회**: v1.0에서 발견한 불일치(컬럼명, 경로, 패턴)를 v2.2까지 반복 수정하여 0건 달성
- **eval_calibrator가 핵심 허브**: PreOrderEvalResult → DB 저장 경로에서 calibrator의 batch 조립이 병목
- **_STORE_COLUMN_PATCHES 패턴**: SCHEMA_MIGRATIONS보다 멱등성이 보장되어 운영 안전성 높음
- **rule_based 우선 전략**: 외부 API 의존 없이 즉시 사용 가능, BGF 계약 확인 후 전환
