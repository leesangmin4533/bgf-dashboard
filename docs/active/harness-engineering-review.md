# 하네스 엔지니어링 설계 검토

> 3가지 모듈(SKIP 로그, AI 요약, integrity_checker 연동) 도입 전 설계 검토

## 1. 현재 상태 분석

### 1-1. SKIP 관련 기존 시스템

| 구분 | 현재 상태 | 위치 |
|------|----------|------|
| SKIP 판정 | PreOrderEvaluator에서 5단계 판정 (FORCE→URGENT→NORMAL→PASS→SKIP) | pre_order_evaluator.py |
| SKIP 저장 | eval_outcomes.decision='SKIP'으로 저장 | eval_outcome_repo.py |
| SKIP 사유 | PreOrderEvalResult.reason에 있지만 **DB에 미저장** (Python 객체에서 소실) | - |
| SKIP 분석 | `get_skip_stockout_stats()` (SKIP→품절 상관분석) 존재 | eval_outcome_repo.py |
| 제외 추적 | order_exclusions 테이블 (10가지 ExclusionType) | order_exclusion_repo.py |

**핵심 갭**: SKIP 사유(reason)가 DB에 저장되지 않아 "왜 스킵했는지" 사후 분석 불가

### 1-2. integrity_checker 현재 상태

| 항목 | 내용 |
|------|------|
| 서비스 | DataIntegrityService |
| 실행 시점 | Phase 1.67 (daily_job.py) |
| 저장 테이블 | integrity_checks (store DB) |
| 체크 항목 | 6개 (유령배치, 유령재고, 만료불일치, 누락배송, 좀비OT, 미취급+판매) |
| 상태 코드 | OK, WARN, FAIL, RESTORED, ERROR |
| 자동 복원 | check 6: is_available=0 → 자동 1 복원 |
| 알림 | Kakao 발송 (anomaly>0) |
| 출력 | integrity_checks.details (JSON) |

### 1-3. DB 아키텍처 패턴

```
BaseRepository
├── db_type = "store"  → data/stores/{store_id}.db
├── db_type = "common" → data/common.db
└── db_type = "legacy" → data/bgf_sales.db (deprecated)

1 Repository = 1 Table, try/finally 커넥션 관리
현재 DB_SCHEMA_VERSION = 67
```

---

## 2. 설계 초안별 검토

### 2-1. SKIP 로그 시스템 (order_skip_logs) → 매장별 DB

#### 찬성 근거
- SKIP은 매장별 판매/재고 데이터에 의존 → 매장 DB가 자연스러움
- eval_outcomes와 JOIN 용이 (같은 DB)
- 기존 order_exclusions 패턴과 일관

#### 충돌 가능성

| 항목 | 위험도 | 상세 |
|------|--------|------|
| eval_outcomes 중복 | **중** | eval_outcomes.decision='SKIP'과 order_skip_logs가 동일 정보 이중저장 가능 |
| order_exclusions 역할 겹침 | **중** | SKIP vs Exclusion 경계가 모호 (CUT→ExclusionType.CUT vs CUT→SKIP?) |
| 스키마 버전 | 낮음 | v68로 마이그레이션 필요 (기존 패턴 그대로) |

#### 놓친 엣지케이스

1. **SKIP + 이후 FORCE_ORDER 복원**: 같은 날 Phase 1.5에서 SKIP → Phase 2에서 stock=0 감지 → FORCE_ORDER. 두 결과가 모두 기록되어야 함
2. **PASS vs SKIP 경계**: stock=1인 상품이 PASS(발주불필요)인지 SKIP(의도적 제외)인지 구분 필요
3. **다중 SKIP 사유**: 한 상품이 여러 이유로 SKIP 가능 (예: 저회전 + 미취급). 주 사유 1개만 저장? 전체?
4. **SKIP 사유의 스냅샷**: 판정 시점의 threshold 값(예: exposure_days < 0.5일 때 SKIP)도 함께 기록해야 사후 감사 가능

#### 대안 검토

| 방안 | 장점 | 단점 |
|------|------|------|
| **A. 새 테이블 order_skip_logs** | 전용 구조, 상세 분석 가능 | eval_outcomes와 이중저장 |
| **B. eval_outcomes에 skip_reason 컬럼 추가** | 기존 테이블 활용, 스키마 변경 최소 | 컬럼 비대화, SKIP 외 행은 NULL |
| **C. eval_outcomes.details JSON 컬럼** | 유연한 구조 | 쿼리 불편, 인덱스 불가 |

**권장**: **방안 B** (eval_outcomes에 `skip_reason TEXT` + `skip_detail TEXT` 추가)
- 이유: 이미 eval_outcomes가 SKIP 판정을 저장하고 있음. 사유만 추가하면 이중저장 없이 완성
- v68 마이그레이션: `ALTER TABLE eval_outcomes ADD COLUMN skip_reason TEXT`

---

### 2-2. AI 요약 루틴 (AISummaryService) → 공용 DB

#### 찬성 근거
- 요약은 매장 간 비교/집계가 필요 → 공용 DB가 적합
- 한 번 생성된 요약은 모든 매장 대시보드에서 공유

#### 충돌 가능성

| 항목 | 위험도 | 상세 |
|------|--------|------|
| API 키 관리 | **높음** | Claude/OpenAI API 키를 어디에 저장? .env? app_settings? |
| 비용 통제 | **높음** | 매일 3매장 × N개 체크 요약 → 토큰 비용 누적 |
| 지연/실패 | **중** | API 타임아웃 시 daily_job 블로킹 위험 |
| 요약 품질 | **중** | 할루시네이션으로 잘못된 인사이트 생성 가능 |

#### 놓친 엣지케이스

1. **API 실패 시 폴백**: Claude API 다운 시 daily_job이 멈추면 안 됨. 비동기 or 별도 스케줄?
2. **요약 대상 범위**: integrity_check 결과만? eval_outcomes 요약도? order 실패 요약도?
3. **요약 갱신 주기**: 하루 1회? integrity 실행 직후? 수동 트리거도 가능?
4. **이전 요약 참조**: AI가 어제 요약과 오늘 요약을 비교하여 트렌드를 말할 수 있어야 함?
5. **다국어/인코딩**: 한글 상품명이 AI 프롬프트에 포함될 때 토큰 효율
6. **민감 정보**: 매장 매출/재고 데이터를 외부 API로 보내도 되는지 (보안/계약)

#### 테이블 설계 검토

```sql
-- 공용 DB에 저장
CREATE TABLE ai_summaries (
    id INTEGER PRIMARY KEY,
    summary_date TEXT NOT NULL,
    summary_type TEXT NOT NULL,     -- 'integrity', 'daily_order', 'skip_analysis'
    store_id TEXT,                  -- NULL이면 전체 매장 요약
    input_data TEXT,               -- 요약에 사용된 원본 데이터 (JSON)
    summary_text TEXT,             -- AI 생성 요약
    model_used TEXT,               -- 'claude-sonnet-4-6' 등
    token_count INTEGER,           -- 사용 토큰 수
    cost_usd REAL,                 -- 비용 추적
    created_at TEXT,
    UNIQUE(summary_date, summary_type, store_id)
);
```

**문제점**:
- `input_data`가 커지면 common.db 비대화 → 별도 DB 또는 파일 저장 고려
- `store_id`가 있으면 사실상 매장별 데이터 → 공용 DB에 넣는 의미 약화

**대안**:
- 요약 텍스트만 공용 DB, 원본 데이터는 매장 DB에 유지
- 또는 `data/reports/ai_summaries/` 디렉토리에 JSON/MD 파일로 저장 (DB 부담 없음)

---

### 2-3. integrity_checker 연동 (실행 직후 AI 요약)

#### 찬성 근거
- integrity_check 결과가 JSON으로 이미 구조화되어 있음 → AI 입력으로 적합
- 이상 발견 시 즉시 요약하면 조치 속도 향상

#### 충돌 가능성

| 항목 | 위험도 | 상세 |
|------|--------|------|
| Phase 순서 | **중** | Phase 1.67(integrity) → AI요약 → Phase 1.68(DirectAPI). AI가 느리면 Phase 1.68 지연 |
| 멱등성 | **중** | 같은 날 재실행 시 요약이 중복 생성되지 않아야 함 |
| 부분 실패 | **낮음** | 3매장 중 1매장만 integrity 실패 시 요약 범위? |

#### 놓친 엣지케이스

1. **integrity check가 0건 이상일 때만 요약**: 정상(OK)이면 요약 불필요 → 비용 절감
2. **RESTORED 상태 포함 여부**: 자동 복원된 건도 요약에 포함? (복원 성공이니 생략?)
3. **이전 체크와 비교**: "어제는 유령재고 5건이었는데 오늘은 3건으로 줄었다" 같은 트렌드
4. **요약의 actionable 여부**: "유령재고 3건 발견"만 말하면 의미 없음. "item_cd XXX, YYY를 확인하세요" 수준?

#### 실행 흐름 제안

```
Phase 1.67: DataIntegrityService.run_all_checks()
  → integrity_checks 테이블 저장
  → anomaly_count > 0?
    → YES: AISummaryService.summarize_integrity(check_results)
           → ai_summaries 테이블 저장
           → Kakao 알림에 AI 요약 첨부 (선택)
    → NO: 스킵 (비용 절감)
```

---

## 3. 종합 설계 제안

### 3-1. 추천 테이블 설계

```
[매장 DB 변경 - v68]
eval_outcomes:
  + skip_reason TEXT          -- SKIP 사유 코드 (예: LOW_EXPOSURE, LOW_POPULARITY)
  + skip_detail TEXT          -- 상세 정보 (JSON: {threshold: 0.5, actual: 0.3})

[공용 DB 변경 - v68]
ai_summaries:
  id INTEGER PRIMARY KEY
  summary_date TEXT NOT NULL
  summary_type TEXT NOT NULL   -- 'integrity' | 'skip_analysis' | 'daily_report'
  store_id TEXT               -- NULL = 전체 요약
  summary_text TEXT
  anomaly_count INTEGER       -- integrity 결과 이상 건수
  model_used TEXT
  token_count INTEGER
  created_at TEXT
  UNIQUE(summary_date, summary_type, store_id)
```

### 3-2. 추천 클래스 구조

```
src/application/services/
├── ai_summary_service.py     -- AISummaryService (공용 DB)
│   ├── summarize_integrity(check_results) → str
│   ├── summarize_skip_decisions(eval_date, store_id) → str
│   └── summarize_daily_order(order_results) → str
│
src/infrastructure/database/repos/
├── ai_summary_repo.py        -- AISummaryRepository (db_type="common")
│   ├── save_summary(date, type, store_id, text, ...) → int
│   ├── get_latest(type, store_id) → Dict
│   └── get_summaries_by_date(date) → List[Dict]
```

### 3-3. 실행 흐름

```
Phase 1.5:  PreOrderEvaluator → eval_outcomes (skip_reason 포함)
Phase 1.67: DataIntegrityService → integrity_checks
Phase 1.67a: AISummaryService.summarize_integrity() (anomaly>0일 때만)
Phase 2:    AutoOrder → 발주 실행
Phase 2a:   AISummaryService.summarize_daily_order() (발주 완료 후)
```

---

## 4. 더 나은 대안 검토

### 4-1. SKIP 로그: 새 테이블 vs eval_outcomes 확장

| 기준 | 새 테이블 (order_skip_logs) | eval_outcomes 확장 |
|------|---------------------------|-------------------|
| 이중저장 | YES (eval_outcomes + skip_logs) | NO (한 곳) |
| 쿼리 편의성 | skip 전용 인덱스 가능 | WHERE decision='SKIP' 필터 필요 |
| 스키마 변경량 | CREATE TABLE 1개 | ALTER TABLE 2컬럼 |
| 기존 코드 변경 | 새 repo 필요 | eval_outcome_repo만 수정 |
| 메모리에 메타 저장 | 추후 SKIP이 늘어도 본 테이블 부담 없음 | eval_outcomes 테이블 비대화 |

**결론**: 소규모(3매장)에서는 eval_outcomes 확장이 효율적. 대규모 확장 시 분리 고려.

### 4-2. AI 요약: DB 저장 vs 파일 저장

| 기준 | DB (ai_summaries) | 파일 (data/reports/ai/) |
|------|-------------------|----------------------|
| 검색/집계 | SQL로 편리 | 파일명 규칙으로 가능 |
| 대시보드 연동 | API 바로 연결 | 파일 읽기 추가 필요 |
| DB 크기 | 일일 ~2KB × 3매장 = 미미 | 0 영향 |
| 백업 | DB 백업에 포함 | 별도 관리 |

**결론**: DB 저장 추천 (API 연동 편의성).

### 4-3. AI 호출 타이밍: 동기 vs 비동기

| 기준 | 동기 (Phase 1.67 직후) | 비동기 (별도 스케줄) |
|------|----------------------|-------------------|
| 즉시성 | 바로 요약 가능 | 수 분 지연 |
| 블로킹 | API 실패 시 Phase 1.68 지연 | daily_job 영향 없음 |
| 구현 복잡도 | 낮음 (try/except 감싸기) | 중간 (별도 job 정의) |

**결론**: **동기 + try/except** (실패 시 로그만 남기고 계속).
이유: integrity 요약은 ~1-2초 (짧은 입력). API 실패해도 발주 플로우에 영향 없도록.

---

## 5. 주의사항 체크리스트

- [ ] API 키 저장: `.env` 파일 (BGF_USER_ID 패턴 따르기), 하드코딩 금지
- [ ] 비용 상한: 일일 토큰 한도 설정 (예: 10K 토큰/일)
- [ ] 프롬프트 최적화: 구조화된 JSON 입력 → 간결한 요약 출력 (토큰 절약)
- [ ] 민감 정보: 상품명/매출 데이터가 외부 API로 전송됨 → 계약 확인 필요
- [ ] 멱등성: 같은 날 재실행 시 UPSERT (중복 방지)
- [ ] 롤백: AI_SUMMARY_ENABLED 토글 추가
- [ ] 모니터링: token_count 누적 대시보드 or 로그

---

## 6. 클로드 AI 챗 토론 포인트

1. **SKIP 사유 분류 체계**: 어떤 reason 코드를 정의할 것인가? (LOW_EXPOSURE, LOW_POPULARITY, STOCK_SUFFICIENT, DATA_INSUFFICIENT, ...)
2. **AI 요약 프롬프트 설계**: 어떤 형식/톤/길이로 요약할 것인가?
3. **AI 요약 범위**: integrity만? daily order도? skip 분석도?
4. **대시보드 UI**: AI 요약을 어디에 표시할 것인가? (홈 탭? 별도 탭?)
5. **비용 모델**: 어떤 AI 모델 사용? (haiku=저렴, sonnet=균형, opus=고품질)
6. **보안**: BGF 데이터를 외부 AI API에 보내도 되는 계약 조건?
