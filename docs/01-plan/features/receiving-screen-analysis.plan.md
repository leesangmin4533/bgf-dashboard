# DB 데이터 품질 검증 체계 구축

**Feature ID**: receiving-screen-analysis  
**작성일**: 2026-02-06  
**상태**: Plan  
**우선순위**: Critical

---

## 1. 개요

### 1.1 배경

BGF 리테일 자동 발주 시스템에서 **심각한 데이터 품질 문제**가 발견되었습니다:

**발견된 문제:**
- 테스트 데이터 생성 스크립트(`insert_mid_category_sales.py`)가 프로덕션 DB에 실행됨
- 182일 × 13개 카테고리 = **2,353개 테스트 레코드** 오염 (전체의 14.09%)
- 실제 BGF 수집 데이터와 테스트 데이터가 혼재
- 쿼리 시 `collected_at` 필터 없이 조회하면 테스트 데이터가 반환될 수 있음

**실제 사례 (2026-02-05 주먹밥):**
```
X 사용자가 본 데이터: 상품 1개, 판매 34개 (테스트 데이터)
O 실제 판매 데이터: 상품 12개, 판매 12개 (BGF 수집)
```

**영향 범위:**
- 발주량 예측 알고리즘 왜곡
- 일일/주간 리포트 부정확
- 폐기 알림 오작동
- 재고 분석 신뢰도 저하

### 1.2 근본 원인

1. **환경 분리 부재**: 테스트와 프로덕션 DB가 동일 (`bgf_sales.db`)
2. **데이터 검증 부재**: 수집 데이터의 품질을 검증하는 체계 없음
3. **구별 메커니즘 부재**: 실제/테스트 데이터를 구별할 메타데이터 없음
4. **모니터링 부재**: 데이터 이상 징후를 감지하는 시스템 없음

### 1.3 목표

**DB 데이터 품질을 보장하는 자동 검증 체계 구축**

1. **즉시 조치**: 현재 DB의 테스트 데이터 정리
2. **단기 목표**: 데이터 품질 검증 모듈 구현
3. **장기 목표**: 지속적인 품질 모니터링 시스템 구축

### 1.4 범위

**포함 사항:**
- 테스트 데이터 정리 스크립트
- 데이터 검증 모듈 (`src/validation/`)
- Repository 통합
- 일일 품질 리포트
- 환경 분리 (프로덕션/테스트 DB)

**제외 사항:**
- 자동 수정 (이상 데이터 발견 시 수동 확인 필요)
- 머신러닝 기반 이상 탐지 (통계 기반만)
- 히스토리 추적 (데이터 변경 이력)
- UI 대시보드 (CLI 리포트만)

---

## 2. 요구사항

### 2.1 기능 요구사항

#### FR-1: 테스트 데이터 정리
- **우선순위**: Critical
- **설명**: 현재 DB의 모든 테스트 데이터 삭제
- **검증 기준**:
  - `item_cd GLOB '88[0-9][0-9][0-9]00001'` 패턴 삭제
  - `collected_at = '2026-02-06 11:40:21'` 일괄 삭제
  - 삭제 후 레코드 수: 14,351개 (85.91%)
- **안전 장치**:
  - 삭제 전 전체 DB 백업
  - Dry-run 모드 (삭제 예상 결과 미리보기)
  - 수동 확인 후 실행

#### FR-2: 데이터 품질 검증기
- **우선순위**: High
- **설명**: 수집 데이터의 품질을 자동으로 검증하는 모듈
- **검증 항목**:
  1. **상품코드 형식**: 13자리 바코드 (8801234567890)
  2. **판매 수량**: 비음수, 합리적 범위 (0~500)
  3. **재고 일관성**: 재고 = 전일재고 + 입고 - 판매 - 폐기
  4. **중복 수집**: 동일 날짜+상품의 중복 `collected_at` 감지
  5. **이상치 탐지**: 평균 대비 3σ 초과 판매량

#### FR-3: 환경 분리
- **우선순위**: High
- **설명**: 프로덕션/테스트 DB 분리
- **구현**:
  - `data/bgf_sales.db` → 프로덕션 전용
  - `data/bgf_sales_test.db` → 테스트 전용
  - 환경변수 `BGF_DB_MODE` (production/test)

#### FR-4: 실시간 모니터링
- **우선순위**: Medium
- **설명**: 수집 직후 데이터 품질 검증
- **통합 지점**:
  - `SalesRepository.save_daily_sales()` 후크
  - `SalesCollector.collect_multiple_dates()` 콜백

#### FR-5: 일일 검증 리포트
- **우선순위**: Medium
- **설명**: 전체 DB 대상 일일 품질 검증
- **스케줄**: 매일 06:00 (데이터 수집 전)
- **리포트 항목**:
  - 검증된 레코드 수 (총/정상/이상)
  - 발견된 이상 데이터 (카테고리별)
  - 권장 조치 (삭제/수정/재수집)

### 2.2 비기능 요구사항

#### NFR-1: 성능
- 전체 DB 검증 시간: < 10초 (16,000+ 레코드)
- 단일 날짜 검증: < 1초
- 메모리 사용량: < 100MB

#### NFR-2: 신뢰성
- 검증 로직 단위 테스트 커버리지: > 90%
- False Positive Rate: < 1%
- False Negative Rate: < 0.1%

#### NFR-3: 유지보수성
- 검증 규칙 외부 설정 파일로 관리 (`config/validation_rules.json`)
- 새로운 검증 규칙 추가 시 기존 코드 수정 불필요
- 로깅: 구조화된 JSON 로그 (디버깅 용이)

---

## 3. 설계 방향

### 3.1 핵심 컴포넌트

#### 1. `DataValidator` (메인 검증기)
```python
class DataValidator:
    """데이터 품질 검증기"""

    def validate_sales_data(
        self,
        data: List[Dict],
        sales_date: str,
        store_id: str
    ) -> ValidationResult:
        """판매 데이터 검증 (메인 엔트리포인트)"""
```

#### 2. `AnomalyDetector` (이상치 탐지)
```python
class AnomalyDetector:
    """통계 기반 이상치 탐지"""

    def detect_sales_anomaly(
        self,
        item_cd: str,
        sale_qty: int,
        window_days: int = 30
    ) -> Optional[AnomalyAlert]:
        """판매량 이상치 탐지 (3σ 기준)"""
```

#### 3. `DataQualityReport` (품질 리포트)
```python
class DataQualityReport(BaseReport):
    """일일 데이터 품질 리포트"""

    def generate_daily_report(self) -> str:
        """전체 DB 대상 검증 리포트 생성"""
```

### 3.2 데이터베이스 스키마 변경

#### 신규 테이블: `validation_log`

```sql
CREATE TABLE validation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    validated_at TEXT NOT NULL,           -- 검증 시각
    sales_date TEXT NOT NULL,             -- 검증 대상 날짜
    store_id TEXT NOT NULL,               -- 점포 ID
    validation_type TEXT NOT NULL,        -- 검증 유형
    is_passed BOOLEAN NOT NULL,           -- 통과 여부
    error_code TEXT,                      -- 오류 코드
    error_message TEXT,                   -- 오류 메시지
    affected_items TEXT,                  -- JSON: 영향받은 상품
    metadata TEXT,                        -- JSON: 추가 정보
    created_at TEXT DEFAULT (datetime('now'))
);
```

**Schema Version**: v19 → v20

---

## 4. 구현 계획

### 4.1 Phase 1: 긴급 정리 (Day 1 - 1 hour)

| Task | Description | Estimate |
|------|-------------|----------|
| 1.1 | DB 백업 생성 | 10 min |
| 1.2 | `cleanup_test_data.py` 작성 | 30 min |
| 1.3 | 테스트 데이터 삭제 실행 | 5 min |
| 1.4 | 검증 쿼리 실행 | 10 min |

**Deliverable**:
- `data/backup/bgf_sales_before_cleanup_20260206.db`
- `scripts/cleanup_test_data.py`
- 삭제 로그

### 4.2 Phase 2: 검증 모듈 (Day 1-2 - 8 hours)

| Task | Description | Estimate |
|------|-------------|----------|
| 2.1 | `DataValidator` 클래스 구현 | 2 hours |
| 2.2 | `ValidationRules` 설정 구조 | 1 hour |
| 2.3 | `AnomalyDetector` 구현 | 2 hours |
| 2.4 | DB 스키마 마이그레이션 (v20) | 1 hour |
| 2.5 | 단위 테스트 작성 | 2 hours |

**Deliverable**:
- `src/validation/data_validator.py`
- `src/validation/validation_rules.py`
- `src/validation/anomaly_detector.py`
- `src/db/models.py` (v20 마이그레이션)

### 4.3 Phase 3: 통합 (Day 2-3 - 4 hours)

| Task | Description | Estimate |
|------|-------------|----------|
| 3.1 | `SalesRepository` 후크 추가 | 1 hour |
| 3.2 | `SalesCollector` 콜백 통합 | 1 hour |
| 3.3 | 환경 분리 (DB 모드) | 1 hour |
| 3.4 | 통합 테스트 | 1 hour |

**Deliverable**:
- `src/db/repository.py` (updated)
- `src/collectors/sales_collector.py` (updated)
- `config.py` (DB_MODE 설정)

### 4.4 Phase 4: 모니터링 (Day 3 - 5 hours)

| Task | Description | Estimate |
|------|-------------|----------|
| 4.1 | `DataQualityReport` 구현 | 2 hours |
| 4.2 | 일일 스케줄러 작업 추가 | 1 hour |
| 4.3 | 카카오톡 알림 템플릿 | 1 hour |
| 4.4 | E2E 테스트 | 1 hour |

**Deliverable**:
- `src/report/data_quality_report.py`
- `src/scheduler/daily_job.py` (updated)

---

## 5. 테스트 계획

### 5.1 단위 테스트

| 테스트 케이스 | 입력 | 예상 출력 |
|--------------|------|-----------|
| 정상 상품코드 | `8801234567890` | `is_valid=True` |
| 테스트 패턴 코드 | `8800200001` | `is_valid=False` |
| 음수 판매량 | `sale_qty=-5` | `is_valid=False` |
| 3σ 초과 판매 | `sale_qty=500` (평균 30) | `warning=ANOMALY` |

---

## 6. 성공 기준

- [ ] 테스트 데이터 0개 (100% 제거)
- [ ] 검증 로직 테스트 커버리지 > 90%
- [ ] 전체 DB 검증 시간 < 10초
- [ ] False Positive Rate < 1%

---

**승인 후 다음 단계**: `/pdca design receiving-screen-analysis`
