# Plan: 운영 지표 자동 감지 항목 확장 (ops-metrics-monitor-extension)

> 작성일: 2026-04-08
> 상태: Plan
> 관련 이슈: expiry-tracking.md#BatchSync-FR-02-우회

---

## 1. 문제 정의

### 현상
2026-04-08 47863 매장 BatchSync FR-02 우회 사건 조사·수정 직후, 내일(04-09) 검증 체크포인트 3개 중 **2개가 자동 감지 사각지대**임이 드러남.

| 체크포인트 | 시각 | 자동 감지? | 사각지대 원인 |
|---|---|:---:|---|
| C1: 47863 false consumed 0건 | 04-08 23:00 | ❌ 수동 | `ops_issue_pipeline`에 batch consumed 비율 지표 없음 |
| C2: 매장별 검증 로그 4파일 생성 | 04-09 07:00 | ❌ 수동 | `daily_chain_report`가 파일 생성 여부만 보고 매장별 분리는 미체크 |
| C3: `waste_slips` 중복 재발 0건 | 04-09 23:00 | ✅ 자동 | UNIQUE 제약 위반이 `job_runs.error_message`로 자동 캡쳐 |

→ **C1·C2를 사람이 매번 봐야 한다면 가드의 효과 자체가 운영 시야에 들어오지 않음**. 검증 자동화 갭.

### 근본 원인
`ops_issue_pipeline.collect_metrics()`(매일 23:55 실행)는 현재 5개 지표만 수집:

1. `waste_rate`: 폐기율 임계 초과
2. `order_failed`: 발주 실패 패턴
3. `prediction_mae`: 예측 정확도 하락
4. `delivery_match_rate`: 배송 매칭률 하락
5. `schema_migration_error`: 스키마 마이그레이션 실패

이 5개에 다음 2개 누락:
- **batch consumed/expired 비율 비정상** (false consumed 감지 신호)
- **매장별 검증 로그 파일 누락** (waste_verification_{store}_{date}.txt 4개 모두 존재 여부)

### 해결 방향
`ops_issue_pipeline.collect_metrics()`에 신규 지표 2개 추가:
- 지표 #6: `false_consumed_post_guard` — 가드 배포 후 잘못 consumed 마킹된 건수
- 지표 #7: `verification_log_files_missing` — 매장별 검증 로그 파일 누락 카운트

각 지표 임계 초과 시:
- `pending_issues.json`에 자동 등록
- `expiry-tracking.md`에 갱신 후크 (선택)
- 카카오 알림 발송

---

## 2. 목표 (KPI)

### 성공 기준
- [ ] 04-09 23:55 `ops_issue_pipeline` 실행 시 신규 2개 지표 정상 수집 (로그에 항목 표시)
- [ ] 47863 매장에서 false_consumed_post_guard 측정값 = 0 → 정상 / 1+ → 알림 발생
- [ ] 매장별 검증 로그 4개 미만일 때 알림 발생
- [ ] 기존 5개 지표 회귀 영향 0건 (pre-existing 테스트 통과 유지)

### 회귀 보호
- `tests/test_ops_issue_pipeline.py`에 신규 2개 지표 회귀 테스트 추가
- 기존 지표 5개 테스트는 그대로 통과해야 함

---

## 3. 범위 (Scope)

### In Scope
- `src/application/services/ops_issue_pipeline.py` (또는 동급 위치)에 지표 수집 함수 2개 추가
- `pending_issues.json` 머지 로직에 신규 카테고리 등록
- 회귀 테스트 2개 신규 작성
- `expiry-tracking.md` 검증 체크포인트 1·2를 자동 항목으로 전환 표기
- 카카오 알림 메시지 템플릿에 신규 2개 지표 추가

### Out of Scope (별도 작업)
- `daily_chain_report` 자체 리팩토링 (현재 구조 유지)
- false_consumed 임계값을 사람이 튜닝 가능하도록 설정 외부화 (1차에서는 하드코딩 = 0)
- 매장별 검증 로그 missing의 자동 복구 (감지만, 복구는 수동)

---

## 4. 핵심 가설

| ID | 가설 | 검증 방법 |
|---|---|---|
| H1 | 신규 2개 지표 추가가 기존 23:55 잡 실행 시간을 30초 이상 늘리지 않는다 | 04-09 23:55 실행 후 `job_runs.duration_sec` 비교 |
| H2 | false_consumed_post_guard 측정 SQL 1회 실행이 4매장 합산 1초 이내 | 단위 테스트에서 실제 DB로 timing 측정 |
| H3 | 검증 로그 파일 누락 감지가 04-09 07:00 스케줄 직후 → 23:55 잡 사이에 정확히 작동 | 04-09 23:55 알림 메시지 확인 |

---

## 5. 위험 요소 (Risks)

| 위험 | 영향 | 완화 |
|---|---|---|
| 신규 SQL이 큰 매장 DB(47863, 50만+ rows)에서 느려질 수 있음 | 23:55 잡 시간 초과 | `expiry_date` 인덱스 활용 + 최근 7일만 조회 |
| 임계값(false_consumed = 0건)이 너무 엄격해 noise 알림 폭주 | 알림 피로도 | 하루 첫 1건만 알림, 이후 동일 사유 묶어 묶음 처리 |
| 4매장 외 신규 매장 추가 시 검증 로그 missing 오탐 | 잘못된 알림 | `active_stores` 리스트 기준으로 동적 매장 수 체크 |

---

## 6. 의존성 (Dependencies)

### Upstream (이 작업 전에 완료돼야 함)
- ✅ `fce1594` 매장별 로그 파일명 분리 (이미 머지)
- ✅ `ae9d05f` FR-02 가드 이식 (이미 머지)
- ✅ v75 마이그레이션 적용 (이미 4매장 완료)

### Downstream (이 작업이 끝나야 가능한 것)
- `expiry-tracking.md` WATCHING → RESOLVED 자동 전환 (지표 2건 모두 정상이면)
- 향후 다른 가드 작업 시 동일 패턴 재사용 (가드 + 자동 검증 지표 페어)

---

## 7. 작업 순서 (대략)

1. **Design 단계** (`/pdca design`)
   - 지표 2개의 정확한 SQL 쿼리 작성
   - `pending_issues.json` 신규 카테고리 키 명세
   - 카카오 알림 메시지 한국어 템플릿
   - 회귀 테스트 케이스 정의

2. **Do 단계** (`/pdca do`)
   - `ops_issue_pipeline.py`에 `_collect_false_consumed_post_guard()`, `_collect_verification_log_missing()` 추가
   - `collect_metrics()`에서 두 함수 호출 + 임계 비교
   - `pending_issues.json` 머지 함수에 신규 카테고리 등록
   - 회귀 테스트 2개 작성 (in-memory DB + 픽스처)

3. **Check 단계** (`/pdca analyze`)
   - gap-detector로 Design ↔ 구현 비교
   - 회귀 테스트 통과 확인
   - 04-09 23:55 라이브 실행 결과 검증

4. **Report 단계** (`/pdca report`)
   - 04-09~10 자동 감지 결과 캡쳐
   - 가드 효과 + 자동 검증 효과 종합 보고

---

## 8. 추정 작업량

| 단계 | 예상 시간 | 비고 |
|---|---|---|
| Design | 15분 | SQL + 알림 템플릿 + 테스트 케이스 정의 |
| Do | 25분 | 함수 2개 추가 + 머지 로직 + 테스트 2개 |
| Check | 5분 | gap-detector + 회귀 테스트 실행 |
| Live 검증 | 04-09 23:55 자동 | 사람 개입 0 |
| Report | 10분 | 결과 정리 |
| **합계** | **약 55분** (사람 작업 시간) | 라이브 검증은 자동 대기 |

---

## 9. 미해결 질문 (Open Questions)

1. **임계값 정책**: false_consumed_post_guard = 0건이 정답인가, 아니면 1~2건은 허용해야 하나?
   - 1차 안: 0건 엄격 (가드가 있으니 단 1건도 보고 가치)
   - 대안: 일별 임계 = 매장당 5건 이상일 때만 알림

2. **알림 채널**: 기존 5개 지표와 같은 카카오 채널? 별도 채널?
   - 1차 안: 동일 채널 (운영 알림은 1개 채널 유지)

3. **검증 로그 missing 판정 시각**: 07:00 스케줄 직후 vs 23:55 잡 시점?
   - 1차 안: 23:55 시점 (다른 지표와 동일 시각, 단일 잡)

→ Design 단계에서 결정.

---

## 10. 관련 문서

- 이슈 체인: `docs/05-issues/expiry-tracking.md#BatchSync-FR-02-우회`
- 선행 작업: `docs/archive/2026-04/batch-sync-zero-sales-guard/`
- 운영 지표 파이프라인: `src/application/services/ops_issue_pipeline.py` (Design에서 정확한 경로 확인)
- 메모리 인덱스: `MEMORY.md` "ops-issue-pipeline" 항목 (04-05 도입)
