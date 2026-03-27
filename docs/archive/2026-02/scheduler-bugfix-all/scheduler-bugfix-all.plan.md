# Plan: scheduler-bugfix-all

> 2026-02-26 스케줄러 로그 분석에서 발견된 3개 버그 통합 수정

## 1. 개요

| 항목 | 내용 |
|------|------|
| Feature | scheduler-bugfix-all |
| 우선순위 | High (발주 플로우 자체는 정상이나, 보조 기능 3건 장애) |
| 영향 범위 | 카카오 알림, 수요 패턴 분류, 폐기 리포트 |
| 예상 수정 파일 | 4개 |

## 2. 이슈 목록

### Bug 1: Phase 1.61 DemandClassifier — `no such table: daily_sales`

| 항목 | 내용 |
|------|------|
| 심각도 | Medium (발주 플로우 계속 진행, 수요 패턴 분류만 스킵) |
| 파일 | `src/prediction/demand_classifier.py` |
| 원인 | `_query_sell_stats()` / `_query_sell_stats_batch()` 메서드에서 SQL에 불필요한 `WHERE store_id = ?` 필터 포함. 매장 DB는 이미 store_id별로 분리되어 있어 `daily_sales` 테이블에 `store_id` 컬럼이 없음 |
| 수정 방법 | 두 메서드의 SQL에서 `store_id` 필터 제거 + 파라미터에서 `store_id` 제거 |

**수정 대상 메서드:**
- `_query_sell_stats()` (line ~150): `WHERE item_cd = ? AND store_id = ?` → `WHERE item_cd = ?`
- `_query_sell_stats_batch()` (line ~176): 동일 패턴 수정

### Bug 2: KakaoNotifier 토큰 갱신 실패 — `Not exist client_id []`

| 항목 | 내용 |
|------|------|
| 심각도 | Low (발주에 영향 없음, 알림만 미발송) |
| 파일 | `src/utils/alerting.py` (line ~107) |
| 원인 | `AlertingHandler._send_kakao_alert()`에서 `KakaoNotifier()`를 인자 없이 생성 → `rest_api_key`가 빈 문자열 → 토큰 갱신 시 Kakao API가 `invalid_client` 반환 |
| 수정 방법 | `KakaoNotifier()` → `KakaoNotifier(DEFAULT_REST_API_KEY)` 변경 |

**참고:** `.env`에 `KAKAO_REST_API_KEY` 설정 완료, `config/kakao_token.json` 존재. 대부분의 호출처(daily_job, run_scheduler, expiry_checker 등)는 정상. `alerting.py`만 누락.

### Bug 3: WasteReport 생성 실패 — store=46513

| 항목 | 내용 |
|------|------|
| 심각도 | Medium (매장 46513 폐기 리포트만 영향) |
| 파일 | `src/analysis/waste_report.py` (line ~650) |
| 원인 | 예외 발생 시 `exc_info=True` 없이 로깅 → 상세 traceback 소실. 실제 원인 파악 불가 |
| 수정 방법 | (1) `exc_info=True` 추가로 상세 에러 확인 → (2) 근본 원인 파악 후 추가 수정 |

**단계별 접근:**
1. 에러 로깅 강화: `logger.error(f"...: {e}")` → `logger.error(f"...: {e}", exc_info=True)`
2. 시트별 try-except 추가: 한 시트 실패 시 나머지 시트는 계속 생성 (부분 리포트)
3. 실제 traceback 확인 후 근본 원인 수정

## 3. 수정 파일 목록

| # | 파일 | 수정 내용 |
|---|------|----------|
| 1 | `src/prediction/demand_classifier.py` | SQL에서 store_id 필터 제거 |
| 2 | `src/utils/alerting.py` | KakaoNotifier에 DEFAULT_REST_API_KEY 전달 |
| 3 | `src/analysis/waste_report.py` | exc_info=True 추가 + 시트별 에러 격리 |
| 4 | `tests/` | 각 수정에 대한 테스트 추가/수정 |

## 4. 구현 순서

1. **Bug 1 수정** — DemandClassifier SQL 수정 (가장 명확, 즉시 수정 가능)
2. **Bug 2 수정** — alerting.py KakaoNotifier 인자 추가 (1줄 수정)
3. **Bug 3 수정** — WasteReport 에러 로깅 강화 + 시트별 에러 격리
4. **테스트** — 기존 테스트 통과 확인 + 새 테스트 추가

## 5. 테스트 계획

| 대상 | 테스트 내용 |
|------|------------|
| DemandClassifier | store DB에서 daily_sales 조회 성공 확인, store_id 필터 없이 정상 동작 |
| AlertingHandler | KakaoNotifier가 rest_api_key를 받아 생성되는지 확인 |
| WasteReport | 일부 시트 실패 시 부분 리포트 생성 확인, exc_info 로깅 확인 |

## 6. 리스크

- Bug 3은 진단 단계(exc_info 추가)이므로 근본 원인이 다음 실행 로그에서 드러남
- DemandClassifier SQL 수정 시 기존 테스트 파라미터도 함께 수정 필요
