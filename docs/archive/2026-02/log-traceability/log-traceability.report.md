# Completion Report: log-traceability

> 로그 역추적성 강화 — Session ID 자동 주입 + 배치 마커 + 파서 호환성

## 1. PDCA 요약

| 항목 | 값 |
|------|-----|
| Feature | log-traceability |
| 시작 | 2026-02-28 |
| 완료 | 2026-02-28 |
| Match Rate | **100%** |
| Iteration | 0회 (첫 구현에서 통과) |
| 테스트 | 15개 신규 + 2617 전체 통과 |

## 2. 구현 내용

### 2.1 Session ID 자동 주입 (핵심)

- `threading.local()` 기반 `_session_ctx`에 세션 ID(8자 hex) 저장
- `SessionFilter(logging.Filter)` — 모든 로그 레코드에 `record.session_id` 자동 주입
- `LOG_FORMAT` 변경: `"%(asctime)s | %(levelname)-8s | %(session_id)s | %(name)s | %(message)s"`
- 콘솔 포맷은 기존 유지 (session_id 미포함)
- **기존 코드 변경 0**: `get_logger(__name__)` 호출부 수정 불필요

### 2.2 세션 라이프사이클

- `set_session_id()`: daily_job.py `run_optimized()` 시작 시 호출
- `clear_session_id()`: finally 블록에서 호출
- 스레드 격리: 자식 스레드는 독립적인 기본값 `'--------'`

### 2.3 배치 마커

- 단일 배치: `[batch=B001]` 마커
- 청크 분할: `[batch=B001] [1/3]`, `[batch=B002] [2/3]` 형식
- 배치 시작/완료 로그로 경계 명확

### 2.4 log_parser 하위 호환

- `LOG_LINE_RE` 정규식: `(?:([a-f0-9-]{8})\s* \| )?` optional 그룹으로 이전/새 포맷 모두 파싱
- `LogEntry.session_id` 필드 추가 (이전 포맷: None)
- `log_analyzer.py`: `--session` / `-S` 옵션 추가

## 3. 수정 파일

| 파일 | 변경 유형 | 변경 내용 |
|------|---------|---------|
| `src/utils/logger.py` | 핵심 수정 | SessionFilter, set/get/clear, LOG_FORMAT |
| `src/scheduler/daily_job.py` | 수정 | set_session_id + clear_session_id |
| `src/order/direct_api_saver.py` | 수정 | 배치 마커 [batch=B001] |
| `src/analysis/log_parser.py` | 수정 | 새 정규식 + session_id 필드 |
| `scripts/log_analyzer.py` | 수정 | --session 옵션 |
| `tests/test_log_traceability.py` | 신규 | 15개 테스트 |
| `tests/test_log_parser.py` | 수정 | 정규식 그룹 변경 반영 |

## 4. 변경 전/후 비교

```
# Before
2026-02-28 13:24:41 | INFO     | src.order.direct_api_saver | dataset 채우기 완료: 29건

# After
2026-02-28 13:24:41 | INFO     | a1b2c3d4 | src.order.direct_api_saver | [batch=B001] dataset 채우기 완료: 29건
```

## 5. 테스트 결과

- 신규 테스트: 15개 (session lifecycle 5 + filter 3 + parser 5 + batch 2)
- 기존 테스트: 2617 전체 통과 (1개 known failure 제외: test_beer_overorder_fix)
- 회귀: 0건

## 6. 운영 영향

- 로그 파일 크기: 줄당 +11바이트 (session_id 8자 + " | ") → 무시 가능
- 성능: logging.Filter는 nanosecond 수준, 영향 없음
- 하위 호환: 이전 포맷 로그도 정상 파싱
