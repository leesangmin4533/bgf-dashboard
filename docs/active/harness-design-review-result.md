# 하네스 엔지니어링 설계서 vs 실제 코드 대조 결과

> 검토일: 2026-03-28
> 대상: 설계서 v1.0 vs bgf_auto 코드베이스

## 총평: 불일치 7건 (치명 4건, 주의 3건)

설계서의 전체 구조와 방향은 적합하나, **실제 코드의 클래스명/경로/패턴을 다수 잘못 참조**하고 있어 그대로 구현하면 오류 발생.

---

## 불일치 상세

### 1. PreOrderEvaluator 파일 경로 (치명)

| 항목 | 설계서 | 실제 코드 |
|------|--------|----------|
| 경로 | `src/application/services/pre_order_evaluator.py` | **`src/prediction/pre_order_evaluator.py`** |

설계서 전체에서 경로 수정 필요.

### 2. PreOrderEvalResult.decision 타입 (치명)

| 항목 | 설계서 | 실제 코드 |
|------|--------|----------|
| 타입 | `str` ("SKIP") | **`EvalDecision` enum** (`.value`로 문자열 접근) |
| 비교 | `decision == "SKIP"` | **`decision == EvalDecision.SKIP`** |

설계서의 모든 문자열 비교를 enum 비교로 변경 필요.

### 3. eval_outcomes 컬럼명 (치명)

| 항목 | 설계서 | 실제 코드 |
|------|--------|----------|
| 상품코드 | `barcode` | **`item_cd`** |
| 매장코드 | `store_cd` | **`store_id`** |
| UNIQUE | `(barcode, store_cd, eval_date)` | **`(store_id, eval_date, item_cd)`** |

설계서의 모든 SQL/코드에서 컬럼명 수정 필요.

### 4. Repository 커넥션 패턴 (치명)

| 항목 | 설계서 | 실제 코드 |
|------|--------|----------|
| 패턴 | `with self._get_connection() as conn:` | **`conn = self._get_conn(); try: ... finally: conn.close()`** |
| 메서드명 | `_get_connection()` | **`_get_conn()`** |

설계서의 모든 Repository 코드에서 패턴 수정 필요.

### 5. DataIntegrityService 메서드 (주의)

| 항목 | 설계서 | 실제 코드 |
|------|--------|----------|
| 호출 방식 | `self.db`, `self._get_shared_db()` | **repo 패턴 사용** (`IntegrityCheckRepository(store_id=)`) |
| `_get_shared_db()` | 존재한다고 가정 | **존재하지 않음** |
| Kakao 발송 | 외부에서 호출 | **`run_all_checks()` 내부에서 자체 발송** |

AISummaryService의 DB 접근 방식을 repo 패턴으로 변경 필요.

### 6. 마이그레이션 구조 (주의)

| 항목 | 설계서 | 실제 코드 |
|------|--------|----------|
| 구조 | `src/infrastructure/database/migrations/migration_v68.py` | **`schema.py` 내 `SCHEMA_MIGRATIONS` dict** |
| 형식 | 별도 파일 | **`SCHEMA_MIGRATIONS[68] = "ALTER TABLE ..."` 형태** |

별도 migration 파일 생성이 아니라 `schema.py`의 dict에 추가해야 함.

### 7. 파일/디렉토리 구조 (주의)

| 항목 | 설계서 | 실제 코드 |
|------|--------|----------|
| `src/domain/enums/` | 새 디렉토리 | **존재하지 않음** (domain/models.py 단일 파일) |
| `src/domain/models/` | 디렉토리 | **`src/domain/models.py`** (파일) |
| `tools/add_store.py` | 존재 | **존재 확인** (단, 실제 구조 다를 수 있음) |

SkipReason은 별도 디렉토리 대신 기존 패턴에 맞게 배치 필요 (예: `src/prediction/pre_order_evaluator.py` 내부 또는 `src/domain/evaluation/` 하위).

---

## SKIP 판정 로직 실제 구조

설계서가 가정하는 SKIP 조건과 실제 코드의 차이:

### 실제 SKIP 판정 (pre_order_evaluator.py)

```
exposure_days >= threshold_sufficient AND popularity_level == "low"
→ decision = EvalDecision.SKIP, reason = "재고충분+저인기"
```

- **SKIP 조건은 1개뿐** (재고충분+저인기)
- 설계서의 IS_UNAVAILABLE, IS_CUT_ITEM 등은 **PreOrderEvaluator에서 SKIP이 아님**
  - is_available=0 → 아예 평가 대상에서 제외 (eval_outcomes에 SKIP으로 기록되지만 사전 필터)
  - is_cut_item → OrderFilter에서 제외 (ExclusionType.CUT)
- 설계서의 SkipReason 코드 중 실제 SKIP에 해당하는 것은 `LOW_EXPOSURE`/`LOW_POPULARITY` 정도

### 수정 제안

SkipReason 코드 체계를 실제 코드 흐름에 맞게 재분류:

| 단계 | 처리 | 현재 기록 위치 | 설계서 SkipReason |
|------|------|-------------|-----------------|
| 사전 필터 (is_available=0) | eval에서 제외 | eval_outcomes.decision='SKIP' | IS_UNAVAILABLE |
| 사전 필터 (is_cut) | eval에서 제외 | order_exclusions | IS_CUT_ITEM → 이미 ExclusionType |
| PreOrderEvaluator | SKIP 판정 | eval_outcomes.decision='SKIP' | LOW_EXPOSURE + LOW_POPULARITY |
| OrderFilter | 제외 | order_exclusions | 이미 ExclusionType으로 커버 |

---

## 수정 필요 사항 요약 (설계서 → 구현 시)

### 필수 수정 (그대로 구현하면 오류)

1. **모든 `barcode` → `item_cd`**, **`store_cd` → `store_id`**
2. **모든 `self._get_connection()` → `self._get_conn()`**, `conn.close()` 패턴
3. **PreOrderEvaluator 경로**: `src/prediction/pre_order_evaluator.py`
4. **decision 비교**: `EvalDecision.SKIP` (enum), `result.decision.value` (문자열 필요 시)
5. **마이그레이션**: `schema.py`의 `SCHEMA_MIGRATIONS[68]`에 SQL 추가
6. **AISummaryService DB 접근**: `_get_shared_db()` 제거, `AISummaryRepository(db_type="common")` 사용

### 권장 수정 (설계 개선)

7. **SkipReason 배치**: `src/domain/enums/` 디렉토리 생성 대신 `src/prediction/skip_reason.py` 또는 `pre_order_evaluator.py` 내부
8. **SkipReason 범위 재조정**: 실제 SKIP 조건(1개)에 맞게 간소화하거나, eval_outcomes의 모든 판정(PASS 포함)에 사유를 기록할지 결정
9. **AISummaryService 생성자**: `store_db_conn`, `shared_db_conn` 파라미터 대신 `store_id` 받아서 내부에서 repo 생성 (기존 Service 패턴)
