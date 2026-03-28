# BGF Auto 기술 부채 이슈 목록

---

## [ISSUE-001] realtime_inventory 테이블 소유권 분산으로 인한 재고 오염 반복 발생

**발견일**: 2026-03-28
**심각도**: 높음 (발주 정확도에 직접 영향)
**유형**: 구조적 기술 부채
**상태**: 임시 조치 완료 / 근본 해결 미완

---

### 현상

발주 직전 prefetch가 BGF에서 정확한 재고를 가져와 DB에 저장해도,
이후 다른 모듈이 같은 테이블을 덮어써서 재고 불일치 발생.

### 확인된 오염 경로

- **SalesCollector** (`sales_repo.py:219-231`): daily_sales 저장 시 어제 재고(stock_qty)와 queried_at을 realtime_inventory에 덮어씀 → **임시 조치 완료** (2026-03-28)
- **Phase 1.65 Stale Stock Cleanup**: stale 상품 stock=0 초기화 → 미조치
- 향후 신규 모듈 추가 시 동일 문제 재발 가능성 있음

### 임시 조치 완료 내용 (2026-03-28)

`sales_repo.py` ON CONFLICT 구문에서 stock_qty, queried_at 갱신 제거.

Before:
```sql
ON CONFLICT(store_id, item_cd) DO UPDATE SET
    item_nm   = COALESCE(excluded.item_nm, realtime_inventory.item_nm),
    stock_qty = excluded.stock_qty,     -- 과거 재고로 덮어쓰기
    queried_at = excluded.queried_at    -- 시각도 오염
```

After:
```sql
ON CONFLICT(store_id, item_cd) DO UPDATE SET
    item_nm = COALESCE(excluded.item_nm, realtime_inventory.item_nm)
```

테스트: `test_sales_repo_stock_protection.py` 5개 + 기존 28개 = 33개 PASSED

### 근본 원인 (미해결)

realtime_inventory 테이블을 여러 모듈이 직접 WRITE하며
서로의 갱신 시점과 값을 모름. 단일 책임 원칙(SRP) 위반.

### 목표 구조

realtime_inventory에 대한 모든 WRITE를 `RealtimeInventoryRepository` 단일 경로로 일원화.

**현재:**
```
SalesCollector → realtime_inventory 직접 WRITE (stock_qty 제거됐으나 구조는 유지)
Phase 1.65     → realtime_inventory 직접 WRITE
prefetch       → RealtimeInventoryRepository → realtime_inventory
```

**목표:**
```
SalesCollector → RealtimeInventoryRepository.update_from_sales()
Phase 1.65     → RealtimeInventoryRepository.cleanup_stale()
prefetch       → RealtimeInventoryRepository.update_from_bgf()
                              ↓
               "queried_at 기준 최신 값만 갱신" 규칙을 Repository 내부에서 일괄 적용
                              ↓
                       realtime_inventory
```

### 근본 해결 구현 시 작업 범위

1. `RealtimeInventoryRepository`에 `update_from_sales()`, `cleanup_stale()` 메서드 추가
2. `sales_repo.py`에서 realtime_inventory 직접 WRITE 코드 완전 제거
3. Phase 1.65 cleanup 코드를 Repository 경유로 변경
4. realtime_inventory 테이블에 `last_source` 컬럼 추가 (`bgf_prefetch` / `sales_collector` / `cleanup`)
5. 외부 모듈의 realtime_inventory 직접 INSERT/UPDATE 금지 규칙 CLAUDE.md에 추가
6. 관련 테스트 추가
