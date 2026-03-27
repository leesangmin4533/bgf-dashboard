# Design: 헬스 체크 + 에러 알림 + 커스텀 예외 + DB 백업 확장

> **Feature**: health-check-alert
> **Plan Reference**: `docs/01-plan/features/health-check-alert.plan.md`
> **Created**: 2026-02-25
> **Status**: Draft

---

## 1. 파일 구조

```
bgf_auto/
├── src/
│   ├── core/
│   │   └── exceptions.py              # [신규] 커스텀 예외 계층
│   ├── utils/
│   │   ├── logger.py                  # [수정] AlertingHandler 연결
│   │   └── alerting.py                # [신규] 에러 알림 핸들러
│   └── web/
│       └── routes/
│           ├── __init__.py            # [수정] health_bp 등록
│           └── api_health.py          # [신규] 헬스 체크 API
├── scripts/
│   └── sync_to_cloud.py              # [수정] SHA256 무결성 검증 추가
└── tests/
    └── test_health_check_alert.py     # [신규] 통합 테스트
```

**신규 파일: 4개** (`exceptions.py`, `alerting.py`, `api_health.py`, `test_health_check_alert.py`)
**수정 파일: 3개** (`logger.py`, `__init__.py`, `sync_to_cloud.py`)

---

## 2. 커스텀 예외 계층

### 2-1. `src/core/exceptions.py`

```python
"""BGF 프로젝트 커스텀 예외 계층.

사용법:
    from src.core.exceptions import DBException, ScrapingException

    try:
        conn = get_connection()
    except sqlite3.Error as e:
        raise DBException("DB 연결 실패", store_id=store_id) from e
"""

class AppException(Exception):
    """애플리케이션 최상위 예외. 모든 커스텀 예외의 부모."""
    def __init__(self, message: str, **context):
        self.context = context
        super().__init__(message)

    def __str__(self):
        base = super().__str__()
        if self.context:
            ctx = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{base} [{ctx}]"
        return base


class DBException(AppException):
    """DB 연결/쿼리/마이그레이션 오류."""
    pass

class ScrapingException(AppException):
    """BGF 넥사크로 사이트 스크래핑 오류."""
    pass

class ValidationException(AppException):
    """입력 데이터 검증 오류."""
    pass

class PredictionException(AppException):
    """예측 로직 오류."""
    pass

class OrderException(AppException):
    """발주 실행 오류."""
    pass

class ConfigException(AppException):
    """설정 파일 로드/검증 오류."""
    pass

class AlertException(AppException):
    """알림 발송 오류 (비즈니스 로직에 영향 없음)."""
    pass
```

### 2-2. `src/core/__init__.py` 업데이트

```python
# 기존 import 유지 + 예외 추가
from .exceptions import (
    AppException, DBException, ScrapingException,
    ValidationException, PredictionException,
    OrderException, ConfigException, AlertException,
)
```

---

## 3. 헬스 체크 API

### 3-1. `src/web/routes/api_health.py`

```python
"""시스템 헬스 체크 API.

인증 불필요 (외부 모니터링 서비스에서 호출 가능).
단, 상세 정보는 인증 필요.
"""

health_bp = Blueprint("health", __name__)
```

**엔드포인트:**

| Method | Path | Auth | 역할 |
|--------|------|------|------|
| GET | `/api/health` | 없음 | 간단 상태 (UP/DOWN) |
| GET | `/api/health/detail` | 필요 | DB, 스케줄러, 디스크, 최근 실행 상세 |

### 3-2. `GET /api/health` 응답

```json
{
  "status": "healthy",
  "timestamp": "2026-02-25T07:15:00",
  "version": "42",
  "uptime_seconds": 3600
}
```

상태 판정 로직:
- `healthy`: DB 연결 OK + 스케줄러 실행중
- `degraded`: DB OK but 스케줄러 중지, 또는 최근 24h 에러 > 10
- `unhealthy`: DB 연결 실패

### 3-3. `GET /api/health/detail` 응답

```json
{
  "status": "healthy",
  "timestamp": "2026-02-25T07:15:00",
  "checks": {
    "database": {
      "status": "ok",
      "common_db_size_mb": 3.2,
      "store_db_size_mb": 55.1,
      "schema_version": 42
    },
    "scheduler": {
      "status": "running",
      "pid": 12345,
      "last_run": "2026-02-25T07:00:00",
      "next_run": "2026-02-26T07:00:00"
    },
    "disk": {
      "log_dir_size_mb": 45.2,
      "data_dir_size_mb": 58.3
    },
    "recent_errors": {
      "last_24h": 3,
      "last_error": "2026-02-25T06:45:00 | DB timeout"
    },
    "cloud_sync": {
      "last_sync": "2026-02-25T07:10:00",
      "status": "ok"
    }
  }
}
```

---

## 4. 에러 알림 핸들러

### 4-1. `src/utils/alerting.py`

```python
"""에러 알림 핸들러.

logging.Handler를 상속하여 ERROR 레벨 로그 발생 시
자동으로 알림을 발송합니다.

기능:
1. 중복 억제 (같은 메시지 5분 내 재발송 방지)
2. 알림 파일 저장 (logs/alerts.log)
3. Kakao 알림 (설정 시)
"""

class AlertingHandler(logging.Handler):
    """ERROR 로그 발생 시 알림을 발송하는 핸들러."""

    COOLDOWN_SECONDS = 300  # 같은 메시지 5분간 중복 억제
    MAX_ALERTS_PER_HOUR = 20  # 시간당 최대 알림 수

    def __init__(self, alert_log_path=None, kakao_enabled=False):
        super().__init__(level=logging.ERROR)
        self._alert_log = alert_log_path or LOG_DIR / "alerts.log"
        self._kakao_enabled = kakao_enabled
        self._recent_alerts = {}  # message_hash → last_sent_time
        self._hourly_count = 0
        self._hourly_reset = time.time()

    def emit(self, record):
        """ERROR 로그 발생 시 호출."""
        try:
            msg = self.format(record)
            msg_hash = hash(record.getMessage()[:100])

            # 중복 억제
            now = time.time()
            if msg_hash in self._recent_alerts:
                if now - self._recent_alerts[msg_hash] < self.COOLDOWN_SECONDS:
                    return

            # 시간당 제한
            if now - self._hourly_reset > 3600:
                self._hourly_count = 0
                self._hourly_reset = now
            if self._hourly_count >= self.MAX_ALERTS_PER_HOUR:
                return

            # 알림 파일에 기록
            self._write_alert_log(msg)

            # Kakao 알림 (설정 시)
            if self._kakao_enabled:
                self._send_kakao_alert(record)

            self._recent_alerts[msg_hash] = now
            self._hourly_count += 1

        except Exception:
            self.handleError(record)
```

### 4-2. logger.py 수정

`setup_logger()` 함수 끝에 AlertingHandler 추가:

```python
# AlertingHandler (ERROR 이상만, 선택적 Kakao 연동)
from src.utils.alerting import AlertingHandler

alerting = AlertingHandler(kakao_enabled=_kakao_alert_enabled())
alerting.setFormatter(formatter)
logger.addHandler(alerting)
```

`_kakao_alert_enabled()`: config/kakao_token.json 존재 여부로 판단.

---

## 5. DB 백업 SHA256 검증

### 5-1. sync_to_cloud.py 수정

`upload_file()` 메서드 내에서 업로드 전 SHA256 해시 계산, 업로드 후 로그 기록:

```python
import hashlib

def _compute_sha256(file_path: Path) -> str:
    """파일 SHA256 해시 계산."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()
```

`upload_file()` 반환 값에 `sha256` 필드 추가:

```python
return {
    "success": True,
    "file": remote_path,
    "size_kb": file_size_kb,
    "elapsed": round(elapsed, 1),
    "sha256": file_hash,  # 추가
}
```

`sync_all()` 결과 로그에 해시 포함.

---

## 6. Blueprint 등록

### 6-1. `src/web/routes/__init__.py` 수정

```python
from .api_health import health_bp

app.register_blueprint(health_bp, url_prefix="/api/health")
```

---

## 7. 구현 순서

| # | 작업 | 파일 |
|---|------|------|
| 1 | 커스텀 예외 계층 정의 | `src/core/exceptions.py` |
| 2 | core/__init__.py 업데이트 | `src/core/__init__.py` |
| 3 | 헬스 체크 API | `src/web/routes/api_health.py` |
| 4 | Blueprint 등록 | `src/web/routes/__init__.py` |
| 5 | AlertingHandler 구현 | `src/utils/alerting.py` |
| 6 | logger.py에 AlertingHandler 연결 | `src/utils/logger.py` |
| 7 | sync_to_cloud.py SHA256 추가 | `scripts/sync_to_cloud.py` |
| 8 | 테스트 작성 | `tests/test_health_check_alert.py` |

---

## 8. 테스트 계획

| # | 테스트 대상 | 방법 | 건수 |
|---|------------|------|------|
| 1 | AppException 계층 + context | pytest (unit) | 5 |
| 2 | `/api/health` 응답 형식 | Flask test client | 3 |
| 3 | `/api/health/detail` 인증 필요 | Flask test client | 2 |
| 4 | AlertingHandler 중복 억제 | mock + unit | 4 |
| 5 | AlertingHandler 시간당 제한 | mock + unit | 2 |
| 6 | SHA256 해시 계산 | unit | 2 |
| 7 | sync_all SHA256 포함 확인 | mock + unit | 2 |
| **합계** | | | **20** |
