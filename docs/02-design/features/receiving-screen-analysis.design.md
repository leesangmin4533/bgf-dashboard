# DB 데이터 품질 검증 체계 - 상세 설계

**Feature ID**: receiving-screen-analysis  
**작성일**: 2026-02-06  
**상태**: Design  
**우선순위**: Critical  
**Plan 참조**: [receiving-screen-analysis.plan.md](../../01-plan/features/receiving-screen-analysis.plan.md)

---

## 1. 아키텍처 개요

```
Data Collection → Repository.save() → DataValidator → ValidationResult → Log/Alert
```

---

## 2. 핵심 클래스 설계

### 2.1 ValidationResult

```python
@dataclass
class ValidationResult:
    is_valid: bool
    total_count: int
    passed_count: int
    failed_count: int
    errors: List[ValidationError]
    warnings: List[ValidationWarning]
    sales_date: str
    store_id: str
```

### 2.2 DataValidator

```python
class DataValidator:
    def validate_sales_data(data, sales_date, store_id) -> ValidationResult:
        # 1. 상품코드 형식 (13자리)
        # 2. 수량 범위 (0~500)
        # 3. 중복 수집 감지
        # 4. 이상치 탐지 (3σ)
```

### 2.3 ValidationRules

```python
class ValidationRules:
    # JSON 설정 파일 로드
    # config/validation_rules.json
```

---

## 3. DB 스키마 (v19 → v20)

```sql
CREATE TABLE validation_log (
    id INTEGER PRIMARY KEY,
    validated_at TEXT,
    sales_date TEXT,
    store_id TEXT,
    validation_type TEXT,
    is_passed BOOLEAN,
    error_code TEXT,
    error_message TEXT,
    affected_items TEXT,  -- JSON
    metadata TEXT         -- JSON
);
```

---

## 4. 통합 지점

### 4.1 SalesRepository 후크

```python
def save_daily_sales(..., enable_validation=True):
    stats = _save_to_db(...)
    
    if enable_validation:
        validator = DataValidator()
        result = validator.validate_sales_data(data, date, store_id)
        ValidationRepository().log_validation_result(result)
```

---

## 5. 환경 분리

```python
# config.py
BGF_DB_MODE = os.getenv('BGF_DB_MODE', 'production')
BGF_DB_NAME = 'bgf_sales_test.db' if BGF_DB_MODE == 'test' else 'bgf_sales.db'
```

---

## 6. DataQualityReport

```python
class DataQualityReport(BaseReport):
    def generate_daily_report(days=7) -> Path:
        # 검증 통계 조회
        # HTML 리포트 생성
        # 카카오톡 알림
```

---

## 7. 구현 파일

| 파일 | 설명 |
|------|------|
| `src/validation/validation_result.py` | ValidationResult, ValidationError, ValidationWarning |
| `src/validation/validation_rules.py` | ValidationRules (JSON 설정 관리) |
| `src/validation/data_validator.py` | DataValidator (메인 검증기) |
| `src/db/repository.py` | ValidationRepository 추가 |
| `src/db/models.py` | v20 마이그레이션 추가 |
| `src/report/data_quality_report.py` | DataQualityReport |
| `scripts/cleanup_test_data.py` | 테스트 데이터 정리 |
| `config/validation_rules.json` | 검증 규칙 설정 |

---

## 8. 검증 규칙 (config/validation_rules.json)

```json
{
  "item_code": {
    "length": 13,
    "pattern": "^\d{13}$",
    "exclude_patterns": ["^88\d{2}\d{5}1$"]
  },
  "quantity": {
    "sale_qty": {"min": 0, "max": 500},
    "ord_qty": {"min": 0, "max": 1000},
    "stock_qty": {"min": 0, "max": 2000}
  },
  "anomaly": {
    "method": "3sigma",
    "window_days": 30,
    "min_samples": 7
  }
}
```

---

## 9. 에러 코드 정의

| 코드 | 설명 | 심각도 |
|------|------|--------|
| `INVALID_ITEM_CD` | 상품코드 형식 오류 | Error |
| `NEGATIVE_QTY` | 음수 수량 | Error |
| `EXCESSIVE_QTY` | 수량 범위 초과 | Error |
| `DUPLICATE_COLLECTION` | 중복 수집 | Error |
| `ANOMALY_3SIGMA` | 이상치 (3σ) | Warning |

---

**승인 후 다음 단계**: `/pdca do receiving-screen-analysis`
