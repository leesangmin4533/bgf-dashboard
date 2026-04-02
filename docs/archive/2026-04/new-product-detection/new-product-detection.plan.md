# Plan: 신제품 감지 보완 (new-product-detection)

> 작성일: 2026-04-02
> 상태: Plan

---

## 1. 문제 정의

### 현상
- `8803622102594` (마른안주류 mid_cd=022) 상품이 신제품으로 인식되지 않음
- `detected_new_products`에 미등록 → 신제품 보정(boost) 미적용 → SLOW 판정 → 발주량 0 → 품절 반복

### 근본 원인
`ReceivingCollector`의 신제품 감지 기준이 **"products 테이블에 없는가?"** 단일 조건에 의존.

**타임라인 (8803622102594 실제 사례):**
```
02-15 14:27  NewProductCollector → new_product_items에 midoip 수집
02-16 03:08  SalesCollector → sales_repo._upsert_product() → products에 등록 ★
02-19        실제 입고 발생 (2개)
02-20 23:54  ReceivingCollector → products 조회 → "이미 있음" → 신제품 후보 제외 ★
```

**판매 수집이 입고 수집보다 먼저 products에 등록하면, 신제품 감지 경로를 완전히 우회해버림.**

### 영향 범위
- 4개 매장 전체 (46513, 46704, 47863, 49965)
- 판매 데이터가 입고보다 먼저 수집되는 모든 신제품에 해당
- common.db `detected_new_products` 테이블 존재하나 **0건** (미활용)

---

## 2. 해결 방향

### 핵심 아이디어
`ReceivingCollector`의 신제품 감지에 **보조 조건 추가**:
products에 이미 있더라도 → `detected_new_products`에 없고 → 첫 입고가 최근이면 → **신제품으로 등록**

### 사용자 요구사항
1. 신제품 등록 시 **common.db에도 등록** (매장 간 공유)
2. 신제품 등록 과정에서 common.db를 **참조** (다른 매장에서 이미 등록된 신제품 정보 활용)
3. **루프 방지 필수**: common.db에 미등록 → 신제품 제외 → 다시 미등록... 순환 금지

---

## 3. 변경 대상 파일

| 파일 | 변경 내용 |
|------|-----------|
| `src/collectors/receiving_collector.py` | 신제품 감지 조건 보완 (핵심) |
| `src/infrastructure/database/repos/detected_new_product_repo.py` | common.db 등록/조회 메서드 추가 |
| `scripts/migrate_missing_new_products.py` | 1회성 소급 등록 스크립트 (기존 누락분 복구) |

---

## 4. 상세 설계

### 4.1 감지 로직 변경 (`receiving_collector.py`)

**현재** (`_check_mid_cd` line 642-672):
```python
# products에서 조회
row = cursor.execute("SELECT mid_cd FROM products WHERE item_cd=?", ...)
if row:
    return row[0]          # ← 여기서 리턴, 신제품 후보 추가 안 됨

# products 미등록 시에만 신제품 후보
self._new_product_candidates.append(...)
```

**변경 후**:
```python
# products에서 조회
row = cursor.execute("SELECT mid_cd FROM products WHERE item_cd=?", ...)
if row:
    # ★ 추가: products에 있어도 detected_new_products에 없으면 신제품 후보 추가
    if not self._is_already_detected(item_cd):
        self._new_product_candidates.append({
            "item_cd": item_cd,
            "item_nm": item_nm,
            "cust_nm": cust_nm,
            "mid_cd": row[0],
            "mid_cd_source": "products",       # ← products에서 가져온 것 표시
            "already_in_products": True,        # ← 루프 방지 플래그
        })
    return row[0]
```

**`_is_already_detected` 신규 메서드**:
```python
def _is_already_detected(self, item_cd: str) -> bool:
    """이미 신제품으로 감지된 상품인지 확인 (store DB + common.db)"""
    # 1순위: store DB detected_new_products
    # 2순위: common.db detected_new_products (다른 매장에서 등록된 경우 참조)
```

### 4.2 common.db 등록 (`detected_new_product_repo.py`)

**`_register_single_new_product`에서 기존 4단계에 5단계 추가:**
```
1) products (common.db)         ← 기존
2) product_details (common.db)  ← 기존
3) realtime_inventory (store DB) ← 기존
4) detected_new_products (store DB) ← 기존
5) detected_new_products (common.db) ← ★ 신규
```

common.db 등록 시 store_id 포함하여 어느 매장에서 최초 감지됐는지 추적.

### 4.3 루프 방지 설계

**위험 시나리오:**
```
감지 → common.db 등록 실패 → 다음 실행 시 common.db에 없음
→ "신제품 아님"으로 판단 → 영원히 미등록
```

**방지 전략:**
1. `_is_already_detected()`는 **store DB를 1순위**로 확인 → common.db는 참조용(2순위)
2. `_register_single_new_product()`에서 common.db 등록 실패해도 **store DB 등록은 독립적으로 진행** (현재와 동일)
3. 감지 판단 = store DB 기준, common.db = 보조 정보 제공용
4. `already_in_products=True` 플래그로 products 재등록 스킵 (INSERT OR IGNORE 이미 적용이지만 명시적 구분)

**흐름도:**
```
입고 수집 시 상품 발견
  │
  ├─ products에 없음 → 신제품 후보 (기존 로직 그대로)
  │
  └─ products에 있음
       │
       ├─ store DB detected_new_products에 있음 → 기존 상품 (스킵)
       │
       ├─ common.db detected_new_products에 있음 → 다른 매장에서 이미 감지
       │   └─ store DB에도 등록 (+ common.db 정보 참조)
       │
       └─ 어디에도 없음 + 첫 입고가 최근(30일 이내)
           └─ 신제품 후보로 추가 → store DB + common.db 양쪽 등록
```

---

## 5. 주의사항

### 루프 위험 체크리스트
- [ ] common.db 등록 실패가 store DB 등록을 막지 않는가?
- [ ] store DB 등록 실패가 다음 실행에서 재감지를 막지 않는가?
- [ ] 감지 판단 기준이 common.db **독립적**인가? (common.db는 참조만)

### 후행 덮어쓰기 체크
- [ ] `_check_mid_cd` 변경이 기존 `_new_product_candidates` 로직을 깨지 않는가?
- [ ] `already_in_products=True` 상품이 `_detect_and_register_new_products`에서 정상 처리되는가?

### 성능
- `_is_already_detected`가 상품마다 호출 → **배치 조회로 최적화** 필요
  - 입고 수집 시작 시 detected_new_products 전체를 set으로 캐싱

---

## 6. 테스트 시나리오

| # | 시나리오 | 기대 결과 |
|---|----------|-----------|
| 1 | products에 없는 상품 입고 | 기존대로 신제품 감지 + common.db에도 등록 |
| 2 | products에 있지만 detected에 없는 상품 입고 (핵심 케이스) | 신제품으로 감지 + 양쪽 DB 등록 |
| 3 | detected에 이미 있는 상품 재입고 | 스킵 (중복 등록 안 함) |
| 4 | 다른 매장에서 이미 common.db에 등록된 상품 | common.db 참조하여 store DB에 등록 |
| 5 | common.db 등록 실패 | store DB 등록은 정상 진행 (루프 없음) |
| 6 | 30일 이전 첫 입고 상품 | 신제품 감지 안 함 (오래된 상품 제외) |

---

## 7. 소급 마이그레이션 스크립트

`scripts/migrate_missing_new_products.py`:

**대상**: receiving_history에 입고 기록이 있지만 detected_new_products에 없는 상품
**기간 제한**: 첫 입고 30일 이내 (선택된 기준과 동일)
**동작**:
1. 각 매장 DB에서 receiving_history의 item_cd 중 detected_new_products에 없는 것 조회
2. 첫 입고일이 30일 이내인 것만 필터
3. store DB + common.db 양쪽에 등록
4. `--dry-run` 옵션으로 미리보기 가능

---

## 8. 기대 효과

- `8803622102594` 같은 상품이 신제품으로 인식 → monitoring 상태 → 유사상품 기반 boost 적용
- SLOW 판정으로 발주 0이 되는 문제 해결
- 매장 간 신제품 정보 공유 (common.db)로 후발 매장 감지 속도 향상
- 소급 등록으로 기존 누락 상품 일괄 복구
