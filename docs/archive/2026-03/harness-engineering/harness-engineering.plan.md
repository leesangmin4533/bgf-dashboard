# Plan: harness-engineering

> 하네스 엔지니어링 — SKIP 사유 저장 + AI 요약 + integrity 연동
> 설계서: v2.2 (최종, 코드 대조 3회 완료, 불일치 0건)

## 1. 문제 정의

| 문제 | 증상 | 영향 |
|------|------|------|
| SKIP 사유 미저장 | PreOrderEvalResult.reason이 Python 객체에서 소실 | "왜 발주 안 됐지?" 추적 불가 |
| integrity 인사이트 부재 | 숫자만 출력 ("유령재고 3건") | 어떤 상품인지, 조치는? |
| 구조화 데이터 부재 | 버그 제보 시 수동 쿼리 필요 | 디버깅 속도 저하 |

## 2. 수정 범위

### 수정 대상 파일 8개

| 파일 | 변경 내용 | 모듈 |
|------|----------|------|
| `src/prediction/skip_reason.py` | NEW: SkipReason 코드 정의 | 1 |
| `src/prediction/pre_order_evaluator.py` | PreOrderEvalResult 필드 추가 + SKIP/PASS 판정에 reason 기록 | 1 |
| `src/prediction/eval_calibrator.py` | batch 조립부에 reason/skip_reason/skip_detail 추가 | 1 |
| `src/infrastructure/database/repos/eval_outcome_repo.py` | reason/skip 컬럼 저장 + 조회 메서드 추가 | 1 |
| `src/infrastructure/database/schema.py` | SCHEMA_MIGRATIONS[68] + COMMON_SCHEMA ai_summaries | 1+2 |
| `src/infrastructure/database/repos/ai_summary_repo.py` | NEW: AISummaryRepository (db_type="common") | 2 |
| `src/application/services/ai_summary_service.py` | NEW: AISummaryService (규칙기반 + API 폴백) | 2 |
| `src/application/services/data_integrity_service.py` | _run_ai_summary(store_id, results) 추가 | 3 |

### 변경하지 않는 파일

- improved_predictor.py, auto_order.py, promotion_manager.py, order_filter.py
- daily_job.py (Phase 1.67은 DataIntegrityService 내부에서 처리)

## 3. 모듈별 설계 요약

### 모듈 1: SKIP 사유 저장

- eval_outcomes에 `reason TEXT`, `skip_reason TEXT`, `skip_detail TEXT` 추가 (v68)
- SkipReason 코드: SKIP_LOW_POPULARITY, SKIP_UNAVAILABLE, PASS_STOCK_SUFFICIENT, PASS_DATA_INSUFFICIENT
- 저장 경로: PreOrderEvaluator → EvalCalibrator.save_eval_results() → batch dict 조립 → eval_outcome_repo INSERT
- **핵심**: eval_calibrator.py의 batch 조립부에 3필드 추가 (v2.2에서 발견된 치명 항목)

### 모듈 2: AI 요약 서비스

- ai_summaries 테이블 (공용 DB, COMMON_SCHEMA 리스트에 추가)
- AISummaryService: 기본 rule_based (외부 API 없음), BGF 확인 후 claude-haiku 전환 가능
- anomaly > 0일 때만 실행 (비용 절감)
- AI_SUMMARY_ENABLED 토글 (.env)

### 모듈 3: integrity_checker 연동

- DataIntegrityService.run_all_checks(store_id) 내부에 _run_ai_summary() 추가
- KakaoNotifier(DEFAULT_REST_API_KEY).send_message() 패턴 (동적 생성)
- 실패 시 try/except로 무시 (발주 플로우 보호)

## 4. DB 마이그레이션 (v68)

### 매장별 DB

```sql
-- SCHEMA_MIGRATIONS[68] (단일 SQL 문자열)
ALTER TABLE eval_outcomes ADD COLUMN reason      TEXT;
ALTER TABLE eval_outcomes ADD COLUMN skip_reason TEXT;
ALTER TABLE eval_outcomes ADD COLUMN skip_detail TEXT;
CREATE INDEX IF NOT EXISTS idx_eval_outcomes_skip_reason
    ON eval_outcomes(store_id, eval_date, skip_reason);
```

### 공용 DB

```sql
-- COMMON_SCHEMA 리스트에 추가 → ensure_common_schema() 자동 적용
CREATE TABLE IF NOT EXISTS ai_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_date TEXT NOT NULL,
    summary_type TEXT NOT NULL,
    store_id TEXT,
    summary_text TEXT,
    anomaly_count INTEGER DEFAULT 0,
    model_used TEXT,
    token_count INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(summary_date, summary_type, store_id)
);
```

## 5. 코드 패턴 준수 사항

| 항목 | 패턴 |
|------|------|
| Repository 커넥션 | `conn = self._get_conn(); try: ... finally: conn.close()` |
| 컬럼명 | `item_cd` (barcode 아님), `store_id` (store_cd 아님) |
| decision 타입 | `EvalDecision` enum (`.value`로 문자열 변환) |
| PreOrderEvalResult 위치 | `src/prediction/pre_order_evaluator.py` 내부 |
| 마이그레이션 형식 | `SCHEMA_MIGRATIONS[68] = "SQL 문자열"` |
| Service 생성자 | `store_id`만 파라미터, 내부에서 repo 생성 |
| Kakao 발송 | `KakaoNotifier(DEFAULT_REST_API_KEY)` 동적 생성 + `send_message()` |

## 6. 테스트 계획

| 테스트 파일 | 내용 | 예상 건수 |
|------------|------|----------|
| test_skip_reason.py | SkipReason 저장/조회, enum 비교, batch 포함 확인 | ~9개 |
| test_ai_summary_service.py | 규칙기반 요약, anomaly=0 스킵, 실패 무해성, UPSERT | ~5개 |
| test_data_integrity_ai.py | store_id 파라미터, KakaoNotifier 동적 생성 | ~3개 |
| test_schema_v68.py | 컬럼 추가, ai_summaries 생성, 멱등성 | ~4개 |
| **합계** | | **~21개** |

## 7. 에러 처리 원칙

모든 AI/요약 관련 코드는 발주 파이프라인을 절대 중단하지 않음:
```python
try:
    service = AISummaryService(store_id=store_id)
    service.summarize_integrity(check_results)
except Exception as e:
    logger.error(f"[AI요약] 실패: {e}")
# ← 예외 재발생 없음
```

## 8. 롤백 계획

- `AI_SUMMARY_ENABLED=false` (.env) → AI 요약 전체 비활성
- skip_reason/skip_detail은 NULL 허용이므로 기존 로직에 영향 없음
- SCHEMA_MIGRATIONS[68]은 ALTER TABLE ADD COLUMN이라 롤백 시 컬럼만 무시

## 9. 구현 순서

```
Week 1: 기반 (v68 마이그레이션 + SkipReason 정의)
Week 2: 모듈 1 (SKIP 사유 저장)
Week 3: 모듈 2+3 (AI 요약 + integrity 연동)
Week 4: 안정화 (전체 테스트 + BGF 외부전송 확인)
```

## 10. 참조 문서

- 설계서: `docs/active/harness-engineering-review.md`
- 코드 대조: `docs/active/harness-design-review-result.md`
- 상세 설계: v2.2 최종 (클로드 AI 챗에서 작성, 대조 3회 완료)
