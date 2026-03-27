# Design: log-traceability

> 로그 역추적성 강화 — Session ID 자동 주입 + 배치 마커 + 상품 발주 요약

## 1. 아키텍처

### 1.1 Session ID 자동 주입 (핵심)

```
┌─ daily_job.py ─────────────────────────────────────┐
│  set_session_id()  →  _session_ctx.sid = "a1b2c3d4" │
└────────────────────────────────────────────────────┘
         │
         ▼
┌─ logger.py ────────────────────────────────────────┐
│  SessionFilter.filter(record)                       │
│    record.session_id = _session_ctx.sid or "--------"│
│                                                     │
│  LOG_FORMAT (변경):                                  │
│    "%(asctime)s | %(levelname)-8s | %(session_id)s   │
│     | %(name)s | %(message)s"                        │
└────────────────────────────────────────────────────┘
         │
         ▼
┌─ 모든 모듈 ────────────────────────────────────────┐
│  logger = get_logger(__name__)                      │
│  logger.info("...")  ← 코드 변경 없이 sid 자동 포함  │
└────────────────────────────────────────────────────┘
```

### 1.2 로그 출력 포맷

```
# 변경 전
2026-02-28 13:24:41 | INFO     | src.order.direct_api_saver | dataset 채우기 완료: 29건

# 변경 후
2026-02-28 13:24:41 | INFO     | a1b2c3d4 | src.order.direct_api_saver | dataset 채우기 완료: 29건
```

- 콘솔 포맷 (간략): session_id 포함하지 않음 (기존 유지)
- 파일 포맷: session_id 8자 추가

## 2. 상세 구현 명세

### 2.1 `src/utils/logger.py` 변경

#### 추가: SessionFilter + set/get/clear

```python
import threading
import uuid

_session_ctx = threading.local()

def set_session_id(sid: str = None) -> str:
    """세션 ID 설정. None이면 자동 생성."""
    if sid is None:
        sid = uuid.uuid4().hex[:8]
    _session_ctx.sid = sid
    return sid

def get_session_id() -> str:
    """현재 세션 ID 반환."""
    return getattr(_session_ctx, 'sid', '--------')

def clear_session_id():
    """세션 ID 초기화."""
    _session_ctx.sid = '--------'

class SessionFilter(logging.Filter):
    """로그 레코드에 session_id 필드를 자동 주입."""
    def filter(self, record):
        record.session_id = get_session_id()
        return True
```

#### 변경: LOG_FORMAT

```python
# 변경 전
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

# 변경 후
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(session_id)s | %(name)s | %(message)s"
# 콘솔 포맷은 기존 유지 (session_id 미포함)
```

#### 변경: setup_logger()

```python
def setup_logger(name, ...):
    ...
    # SessionFilter 추가 (모든 핸들러에)
    session_filter = SessionFilter()
    logger.addFilter(session_filter)
    ...
```

### 2.2 `src/scheduler/daily_job.py` 변경

```python
from src.utils.logger import set_session_id, clear_session_id

def daily_order_flow(...):
    sid = set_session_id()
    logger.info(f"Optimized flow started | session={sid}")
    try:
        ...  # 기존 로직
    finally:
        clear_session_id()
```

### 2.3 `src/order/direct_api_saver.py` — 배치 마커

```python
def _save_chunked(self, orders, date_str, start_time):
    ...
    for idx, chunk in enumerate(chunks):
        batch_label = f"B{idx+1:03d}"
        logger.info(
            f"[DirectApiSaver] [batch={batch_label}] 시작: "
            f"{len(chunk)}건, 날짜={date_str}"
        )
        result = self._save_via_transaction(chunk, date_str)
        if result and result.success:
            logger.info(
                f"[DirectApiSaver] [batch={batch_label}] 완료: "
                f"{result.saved_count}건 ({result.elapsed_ms:.0f}ms)"
            )
        ...
```

단일 배치도 `[batch=B001]` 마커 추가:

```python
def _save_single_batch(self, orders, date_str, start_time):
    logger.info(f"[DirectApiSaver] [batch=B001] 단일배치: {len(orders)}건")
    ...
```

### 2.4 `src/analysis/log_parser.py` — 새 포맷 파싱

```python
# 변경: LOG_LINE_RE — session_id 그룹 추가 (이전 포맷도 호환)
LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"   # group(1): timestamp
    r" \| "
    r"(\w+)\s*"                                     # group(2): level
    r" \| "
    r"(?:([a-f0-9-]{8})\s* \| )?"                   # group(3): session_id (optional)
    r"([\w.]+)"                                      # group(4): module
    r" \| "
    r"(.*)$"                                         # group(5): message
)
```

LogEntry에 session_id 필드 추가:
```python
@dataclass
class LogEntry:
    timestamp: datetime
    level: str
    module: str
    message: str
    raw_line: str
    line_number: int
    phase: Optional[str] = None
    phase_name: Optional[str] = None
    session_id: Optional[str] = None      # 신규
```

### 2.5 `scripts/log_analyzer.py` — --session 옵션

```python
# 인자 추가
p.add_argument('--session', '-S', help='Session ID로 필터링 (8자 hex)')

# 검색 시 적용
if args.session:
    entries = parser.search(args.session, log_file=args.file, ...)
```

## 3. 구현 순서

| 순서 | 파일 | 작업 |
|------|------|------|
| 1 | `src/utils/logger.py` | SessionFilter, set/get/clear, LOG_FORMAT |
| 2 | `src/scheduler/daily_job.py` | set_session_id() 호출 |
| 3 | `src/order/direct_api_saver.py` | 배치 마커 |
| 4 | `src/analysis/log_parser.py` | 새 포맷 파싱 + session_id |
| 5 | `scripts/log_analyzer.py` | --session 옵션 |
| 6 | `tests/` | 테스트 작성 |

## 4. 테스트 명세

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | `test_session_id_injection` | SessionFilter가 record에 sid 주입 확인 |
| 2 | `test_session_id_lifecycle` | set → get → clear 라이프사이클 |
| 3 | `test_log_format_with_sid` | 포맷된 로그에 session_id 포함 확인 |
| 4 | `test_default_session_id` | set 없이 '--------' 기본값 |
| 5 | `test_batch_marker_chunked` | 배치 분할 시 B001, B002 마커 |
| 6 | `test_batch_marker_single` | 단일 배치 B001 마커 |
| 7 | `test_log_parser_new_format` | 새 포맷 정규식 파싱 |
| 8 | `test_log_parser_old_format` | 이전 포맷 하위 호환성 |
| 9 | `test_cli_session_filter` | --session 옵션 동작 |
| 10 | `test_existing_tests_pass` | 기존 테스트 전체 통과 |

## 5. 하위 호환성

- **이전 포맷 로그**: `(?:...)? ` optional 그룹으로 session_id 없는 로그도 정상 파싱
- **기존 코드**: `get_logger()` / `logger.info()` 호출부 변경 없음 (Filter가 자동 주입)
- **콘솔 출력**: LOG_FORMAT_SIMPLE은 변경 없음 (터미널 가독성 유지)
