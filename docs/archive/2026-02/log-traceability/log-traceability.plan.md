# Plan: log-traceability

> 로그 역추적성 강화 — Session ID, 배치 마커, 상품별 발주 요약 로그

## 1. 배경 (Why)

### 현재 문제
- **Session ID 없음**: 같은 시간대에 여러 실행이 겹치면 로그 라인을 특정 세션에 귀속시킬 수 없음
- **배치 경계 없음**: Direct API 50개 청크의 시작/완료 마커가 없어 배치 단위 추적 불가
- **성공 건 로깅 부재**: 실패만 기록되고 성공한 상품은 DB에만 저장 → "108건 성공" 한 줄로 끝남
- **Cross-Phase 연결 없음**: prediction.log의 예측값과 order.log의 발주값을 수동 매칭해야 함
- **`log_with_context()` 미사용**: 이미 구현되어 있지만 실제 코드에서 호출 0회 (logger.py 정의부만 2회)

### 발견 경위
- 이전 세션에서 order.log의 "전략1 실패" 73건 분석 시, 테스트 로그와 라이브 로그를 구분하는 데 2시간 소요
- verify_save 실패 원인 추적도 세션 ID가 없어 시간대 기반 수동 매칭으로만 가능했음

## 2. 목표 (What)

| 항목 | 현재 | 목표 |
|------|------|------|
| 세션 격리 | 시간대 추정 | Session ID로 즉시 필터 |
| 배치 추적 | 불가 | `[batch=B001]` 마커로 경계 명확 |
| 상품 추적 | grep + 수동 | Phase별 한줄 요약 로그 |
| log_with_context | 미사용 | 핵심 모듈에 적용 |

### 비-목표 (Non-Goals)
- 구조화된 JSON 로그 파일 추가 (과도한 변경)
- ELK/Grafana 등 외부 로그 시스템 연동
- 기존 로그 포맷 변경 (호환성 유지)

## 3. 설계 개요 (How)

### 3.1 Session ID 도입

**범위**: `daily_job.py` 세션 시작 시 UUID[:8] 생성 → 모든 하위 모듈에 전파

```
# 생성
session_id = uuid.uuid4().hex[:8]  # "a1b2c3d4"

# 전파 방식: threading.local() 또는 LoggerAdapter
# → logging.Filter 기반 자동 주입 (기존 코드 변경 최소화)

# 로그 포맷 변경
기존: "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
변경: "%(asctime)s | %(levelname)-8s | %(session_id)s | %(name)s | %(message)s"
```

**전파 메커니즘**:
- `_session_context = threading.local()` → `_session_context.session_id`
- Custom `logging.Filter`가 record에 `session_id` 자동 주입
- 기존 `get_logger()` 호출 코드 변경 불필요

### 3.2 배치 마커 (Direct API 발주)

**범위**: `direct_api_saver.py` — `save_orders()` 및 `_save_single_batch()`

```
# 배치 시작
logger.info(f"[batch={batch_label}] 시작: {len(chunk)}건, 날짜={date_str}")

# 배치 완료
logger.info(f"[batch={batch_label}] 완료: {success}/{total}건 성공 ({elapsed_ms}ms)")

# 배치 라벨 형식: "B001", "B002", ... (1회차당 리셋)
```

### 3.3 상품별 발주 결과 로그

**범위**: `order_executor.py` 또는 `auto_order.py` — 발주 결정 시점

```
# DEBUG 레벨 (운영 시 파일 크기 부담 없음, 필요시 INFO로 전환)
logger.debug(
    f"[발주결정] {item_cd} | 예측={pred_qty} 보정={adj_qty} "
    f"발주={order_qty} 배수={unit_qty} ROP={rop}"
)
```

### 3.4 `log_with_context()` 활용

**범위**: WARNING/ERROR 레벨 로그에 구조화된 컨텍스트 추가

```python
# 기존
logger.warning(f"[발주현황동기화] {item_cd} {date} 저장 실패: {e}")

# 개선
log_with_context(logger, "warning", "발주현황동기화 저장 실패",
                 item_cd=item_cd, date=date, error=str(e))
# → "발주현황동기화 저장 실패 | item_cd=880... | date=2026-02-28 | error=UNIQUE..."
```

### 3.5 log_analyzer.py CLI 확장

```bash
# Session ID로 필터
python scripts/log_analyzer.py --session a1b2c3d4

# 배치 ID로 필터
python scripts/log_analyzer.py --search "batch=B001" --file order
```

## 4. 영향 범위

### 수정 파일
| 파일 | 변경 내용 | 위험도 |
|------|---------|--------|
| `src/utils/logger.py` | SessionFilter, set_session_id(), 포맷 변경 | 중 |
| `src/scheduler/daily_job.py` | 세션 시작 시 set_session_id() 호출 | 낮 |
| `src/order/direct_api_saver.py` | 배치 마커 추가 | 낮 |
| `src/order/auto_order.py` | 상품별 발주 결과 DEBUG 로그 | 낮 |
| `src/analysis/log_parser.py` | session_id 파싱 지원 | 낮 |
| `scripts/log_analyzer.py` | --session 옵션 추가 | 낮 |

### 변경 없는 파일
- 각 모듈의 `get_logger(__name__)` 호출부 → Filter가 자동 주입하므로 변경 불필요
- conftest.py → 이전 세션에서 이미 FileHandler 억제 처리 완료

## 5. 리스크

| 리스크 | 확률 | 대응 |
|--------|------|------|
| 로그 파일 크기 증가 | 중 | session_id 8자 추가 → 줄당 +10바이트, 무시 가능 |
| 기존 log_parser 호환성 | 중 | LOG_LINE_RE 정규식 업데이트, 이전 포맷도 파싱 가능하게 |
| threading.local 멀티스레드 | 낮 | 현재 단일 스레드 실행, 향후 멀티스레드 시 ContextVar 전환 |

## 6. 테스트 계획

- [ ] Session ID가 로그에 포함되는지 확인
- [ ] 배치 마커 시작/완료 쌍이 일치하는지 확인
- [ ] log_parser가 새 포맷을 정상 파싱하는지 확인
- [ ] log_analyzer --session 필터가 동작하는지 확인
- [ ] 기존 테스트 1466개 통과 확인
- [ ] 이전 포맷 로그도 파싱 가능한지 (하위 호환성)

## 7. 일정

- **예상 소요**: 1시간
- **Phase 순서**: logger.py (핵심) → daily_job (세션 시작) → direct_api_saver (배치) → auto_order (상품) → log_parser/analyzer (CLI) → 테스트
