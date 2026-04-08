# Plan: harness-week3-ai-summary

> 작성일: 2026-04-08
> 상태: Plan
> 이슈체인: docs/05-issues/scheduling.md#하네스-엔지니어링-week-3
> 진행 방식: 로컬 작성 → 사용자 검토 후 `/ultraplan` 클라우드 세션에서 비교/병합
> 보드: ACTIVE `harness-week3-ai-summary`

---

## 1. 목적

매장별 자동 발주 시스템의 일일 운영 결과(발주, 폐기, 예측, 자전 시스템)를 LLM으로 자동 요약하여
**비개발자 운영자가 5줄로 어제 무슨 일이 있었는지 파악**할 수 있게 한다.

## 2. 문제 정의

| 현재 | 문제 |
|---|---|
| KakaoNotifier 단편 알림 (폐기/리포트/행사) | "어제 자동 시스템이 뭘 했는지" 종합 시야 없음 |
| `docs/05-issues/`, `milestone_snapshots`, `ops_metrics`, `action_proposals`, `prediction_logs` 등 데이터 풍부 | 운영자가 직접 못 읽음. 200+ 행/일 |
| 주간 리포트(`weekly_report_flow`)는 존재 | 주 1회 → 일별 의사결정 지연 |

## 3. 산출물 (Phase 1 MVP)

1. **카카오톡 5줄 요약** (매일 23:50 자동)
   ```
   📊 2026-04-08 CU 동양대점 요약
   ✅ 발주 132건 / 폐기 8건 (-2)
   ⚠️ 003 김밥 -22% 과소예측 지속 (3일째)
   🔴 BatchSync false consumed 5건 → 자전 시스템 자동 정정
   📈 K1~K3 ACHIEVED, K4 NOT_MET (expiry_time_mismatch 31일)
   💡 내일 액션: 49965 묶음 가드 검증
   ```

2. **데이터 소스 → 카테고리 매핑** (Phase 1 범위)

   | 카테고리 | 소스 테이블 | 윈도우 | 추출 신호 |
   |---|---|---|---|
   | 발주 결과 | `auto_order_items`, `order_history` | 당일 | 건수, 카테고리별 합, 실패 사유 |
   | 폐기 | `waste_slips`, `inventory_batches(disposed)` | 당일 | 건수, 전일 대비, top 3 카테고리 |
   | 예측 정확도 | `prediction_logs`, `eval_outcomes` | 어제 발주 → 오늘 판매 | bias, 카테고리별 |
   | 자전 시스템 | `action_proposals`, `integrity_checks` | 당일 | 감지/실행/검증 카운트 |
   | 마일스톤 | `milestone_snapshots` | 최신 1행 | K1~K4 상태 |
   | 활성 이슈 | `docs/05-issues/*.md` 파싱 | OPEN/WATCHING | 이슈 수, 신규 OPEN |

3. **Plan/Design 문서 + 프롬프트 템플릿 + 비용 추정**

## 4. 비범위 (Phase 2~3)

- **Phase 2**: 웹 대시보드 섹션별 요약 (Flask `/api/summary/{date}`)
- **Phase 3**: 주간 리포트 첨부 (기존 `weekly_report_flow` 통합)
- **제외**: 예측 모듈 직접 변경 (food-underprediction Phase B 04-10~04-16 관측 보호)

## 5. 아키텍처 개요

```
[scheduler 23:50]
    │
    ▼
SummaryOrchestrator                            ┌─ KakaoNotifier (5줄)
  │                                            │
  ├─ DataCollector ── 매장별 DB + 공통 DB ───┐ │
  │   (DBRouter 경유, 카테고리별 압축)         │ │
  │                                          ▼ │
  ├─ PromptBuilder (한국어 템플릿) ──── LLMClient ──┤
  │                                          │ │   ├─ Claude API (Haiku 우선)
  └─ SummaryRepo ── ai_summaries 테이블 ─────┘ │   └─ 오프라인 폴백 (규칙 기반)
                                                │
                                                └─ 웹 대시보드 (Phase 2)
```

### 모듈 구조 (신규, 무간섭)

```
src/application/services/ai_summary/
├── __init__.py
├── orchestrator.py         # SummaryOrchestrator — 진입점
├── data_collector.py       # 카테고리별 raw → 압축 dict
├── prompt_builder.py       # 한국어 템플릿 + 컨텍스트 주입
├── llm_client.py           # Claude API 추상화 + 비용/지연 가드
├── fallback_summary.py     # 오프라인 규칙 기반 폴백
└── models.py               # SummaryContext, SummaryResult dataclass
```

### DB 추가 (1개 테이블, schema v77)

```sql
CREATE TABLE IF NOT EXISTS ai_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_date DATE NOT NULL,
    summary_type TEXT NOT NULL,        -- 'daily' | 'weekly'
    output_text TEXT NOT NULL,
    output_5line TEXT,                  -- 카카오 5줄
    raw_context_json TEXT,              -- 디버깅용 입력 스냅샷
    llm_model TEXT,                     -- 'claude-haiku-4-5-...' or 'fallback'
    token_input INTEGER,
    token_output INTEGER,
    cost_usd REAL,
    latency_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(summary_date, summary_type)
);
CREATE INDEX idx_ai_summaries_date ON ai_summaries(summary_date);
```

> **스키마 드리프트 방지**: `_STORE_COLUMN_PATCHES`에도 동일 컬럼 ALTER 추가 (food-underprediction Phase A 교훈).

## 6. LLM 비용/지연 가드

- **모델**: Claude Haiku 4.5 (저비용·저지연 우선)
- **컨텍스트 상한**: 입력 토큰 8K, 출력 1K
- **타임아웃**: 10초 → 폴백 트리거
- **일일 비용 추정**: 1매장당 입력 ~6K + 출력 ~500 = ~$0.005/일/매장. 2매장 × 30일 = **~$0.30/월**
- **API 키 부재**: 환경변수 `ANTHROPIC_API_KEY` 미설정 시 자동 폴백

## 7. 폴백 (오프라인 규칙 기반)

LLM 실패 시 동일 raw context로 템플릿 채우기 — 의미 손실은 있지만 운영자에게 수치는 도달.

```
📊 {date} {store_name}
✅ 발주 {n_order}건 / 폐기 {n_waste}건 ({delta:+})
⚠️ {top_bias_category} {bias:+%} ({streak}일째)
🔴 자전 시스템 감지 {n_detect}건 → 실행 {n_exec}건
📈 마일스톤 K1~K4 {kpi_status}
```

## 8. 스케줄 통합

`src/application/scheduler/job_definitions.py` `SCHEDULED_JOBS`에 추가:
```python
("daily_ai_summary", "23:50", run_daily_ai_summary)
```

`run_daily_ai_summary` 는 `MultiStoreRunner`로 매장별 병렬 실행.

## 9. 단계별 분해

| Phase | 산출물 | 완료 조건 |
|---|---|---|
| **Phase 1 (MVP, 1주)** | 카카오 5줄 + ai_summaries 테이블 + 폴백 | 04-15부터 매일 23:50 카카오 도착 + 14일 비용 < $0.50 |
| **Phase 2 (대시보드, +1주)** | Flask `/api/summary/{date}` + 섹션별 마크다운 | 웹에서 일자 선택 → 섹션 4개 표시 |
| **Phase 3 (주간 리포트, +1주)** | 기존 `weekly_report_flow` 에 첨부 | 일요일 주간 리포트에 7일 요약 헤더 추가 |

## 10. 의존성 / 선행 조건

- **선행**: action_proposals `executed_at` 검증 완료 (현재 `WATCHING`) → 04-09 07:00 검증 후 진행
- **무관**: food-underprediction Phase B 관측 (예측 모듈 미수정 → 충돌 0)
- **신규 환경변수**: `ANTHROPIC_API_KEY`

## 11. 위험 / 미정

| 위험 | 완화 |
|---|---|
| LLM이 hallucination으로 잘못된 수치 보고 | output_5line 생성 후 raw_context의 핵심 수치(발주/폐기 건수)를 정규식 대조, 불일치 시 폴백 |
| API 비용 폭증 | 일일 한도(`MAX_DAILY_USD = 0.05`) 초과 시 자동 폴백 |
| 5줄에 담기 어려운 복합 이슈 | Phase 2 대시보드에서 보강. MVP는 의도적 단순화 |
| 매장별 데이터 편차 | 매장별 독립 호출 (병렬), 매장 컨텍스트 명시적 주입 |

## 12. 검증 계획

1. **단위**: data_collector 카테고리별 압축 9개 케이스 (빈 데이터/정상/극단값)
2. **통합**: 폴백 경로 (API 키 unset) → 5줄 정상 생성
3. **운영**: 04-15부터 7일 카카오 수신 + 비용 추적 + hallucination 0건 확인

## 13. 롤백

- 스케줄 job 비활성화: `job_definitions.py` 한 줄 코멘트
- DB 테이블 drop 불필요 (read-only로 분리되어 있어 운영 무영향)

---

## 14. /ultraplan 비교 체크포인트

본 Plan은 로컬에서 단일 세션으로 작성됨. 사용자가 동일 prompt로 `/ultraplan` 실행 시 다음 항목 비교 권장:

1. **데이터 소스 누락 여부** — 본 Plan은 8개 소스, ultraplan이 추가로 발견하는지
2. **프롬프트 템플릿 형식** — JSON vs 마크다운, 시스템 프롬프트 분리 여부
3. **비용 추정 정확도** — Haiku 토큰 단가 최신 반영
4. **단계 분해 입도** — Phase 1 MVP 범위가 1주에 가능한지

비교 후 충돌 시 `/ultraplan` 결과 우선 (다중 가설 탐색이 강점), 본 Plan은 fallback.
