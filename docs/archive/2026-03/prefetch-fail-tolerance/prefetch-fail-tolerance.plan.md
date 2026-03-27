# prefetch-fail-tolerance Plan

> **Feature**: 조회 실패 내성 강화 — 1회 실패 즉시 미취급 마킹 → 3회 연속 실패 시에만 마킹
>
> **Priority**: Critical
> **Created**: 2026-03-04

---

## 1. 문제 정의

### 1.1 현상
- 상품 8801073311305(삼양 로제불닭납작당면)이 BGF 단품별 발주 조회 1회 실패로 `is_available=0` 마킹
- 예측(predict_batch) + 발주(order_filter) 모두에서 **완전 제외**
- 실제로는 발주 가능한 상품 (재고 있음, CUT 아님, 운영정지 아님)

### 1.2 현재 코드 (order_prep_collector.py:901-909)
```python
except Exception as e:
    logger.error(f"상품 조회 실패: {e}")
    # 조회 실패 시 미취급으로 표시
    if self._save_to_db and self._repo:
        self._repo.mark_unavailable(item_cd, store_id=self.store_id)
    return {'item_cd': item_cd, 'success': False}
```

### 1.3 영향 범위
- 현재 `is_available=0` 상품: **151개** (전부 조회 실패 원인)
- 이 중 **재고 있는 상품 다수 포함** (stock=4~6)
- CUT 상품(90개)과 **구분 불가** — 둘 다 예측/발주 제외이지만 원인이 다름

### 1.4 기존 발주 제외 체계 (3가지 별개)

| 구분 | DB 필드 | 마킹 기준 | 구분 가능 여부 |
|------|---------|----------|:---:|
| CUT (발주중지) | `is_cut_item=1` | BGF `CUT_ITEM_YN='1'` | ✅ |
| 운영정지/단종 | `orderable_status` | BGF `ORD_PSS_ID_NM` 팝업 | ✅ |
| 조회 실패 | `is_available=0` | Exception 발생 | ❌ 구분 안됨 |

---

## 2. 수정 목표

1. **3회 연속 실패 시에만 `is_available=0` 마킹** (현재: 1회 즉시)
2. **조회 실패 vs CUT vs 운영정지 구분 가능하도록** 사유 필드 추가
3. 성공 조회 시 실패 카운트 초기화

---

## 3. 수정 방안

### 3.1 DB 스키마 변경 (realtime_inventory)

| 컬럼 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `query_fail_count` | INTEGER | 0 | 연속 조회 실패 횟수 |
| `unavail_reason` | TEXT | NULL | 미취급 사유 ('query_fail', 'manual', NULL) |

### 3.2 inventory_repo.py 수정

#### 신규: `increment_fail_count(item_cd, store_id)`
```python
UNAVAILABLE_FAIL_THRESHOLD = 3

def increment_fail_count(self, item_cd, store_id):
    # fail_count += 1
    # fail_count >= 3 → is_available=0, unavail_reason='query_fail'
```

#### 수정: `save()` 메서드
```python
def save(self, item_cd, ..., is_available=True, ...):
    # 성공 조회 시 query_fail_count=0 리셋, unavail_reason=NULL
```

#### 기존 유지: `mark_unavailable()`
```python
def mark_unavailable(self, item_cd, store_id):
    # 기존 동작 유지 (즉시 is_available=0)
    # unavail_reason='manual' 설정
```

### 3.3 order_prep_collector.py:909 수정
```python
# Before:
self._repo.mark_unavailable(item_cd, store_id=self.store_id)

# After:
self._repo.increment_fail_count(item_cd, store_id=self.store_id)
```

### 3.4 schema.py 마이그레이션
- ALTER TABLE로 `query_fail_count`, `unavail_reason` 추가

---

## 4. 수정 파일

| 파일 | 변경 내용 |
|------|----------|
| `src/infrastructure/database/repos/inventory_repo.py` | `increment_fail_count()` 추가, `save()`에 fail_count 리셋, `mark_unavailable()`에 unavail_reason 추가 |
| `src/collectors/order_prep_collector.py:909` | `mark_unavailable` → `increment_fail_count` |
| `src/infrastructure/database/schema.py` | `query_fail_count`, `unavail_reason` 마이그레이션 |
| `tests/test_prefetch_fail_tolerance.py` | 테스트 작성 |

---

## 5. 테스트 계획

| # | 테스트명 | 시나리오 | 기대 결과 |
|---|---------|---------|----------|
| 1 | test_first_fail_stays_available | 1회 실패 | is_available=1, fail_count=1 |
| 2 | test_second_fail_stays_available | 2회 실패 | is_available=1, fail_count=2 |
| 3 | test_third_fail_marks_unavailable | 3회 연속 실패 | is_available=0, fail_count=3, unavail_reason='query_fail' |
| 4 | test_success_resets_fail_count | 성공 조회 | fail_count=0, unavail_reason=NULL |
| 5 | test_intermittent_fail_resets | 실패→성공→실패 | fail_count=1 (리셋 후 재시작) |
| 6 | test_mark_unavailable_still_works | 명시적 mark_unavailable | 즉시 is_available=0, unavail_reason='manual' |
| 7 | test_save_resets_after_threshold | 3회 실패 후 성공 조회 | is_available=1, fail_count=0, unavail_reason=NULL |
| 8 | test_unavail_reason_distinguishes | query_fail vs manual 구분 | 사유 필드로 구분 가능 |

---

## 6. 검증

```bash
pytest tests/test_prefetch_fail_tolerance.py -v
pytest tests/ -k "unavailable or fail_count or cut_stale" --tb=short
```
