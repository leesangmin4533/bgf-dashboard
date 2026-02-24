# Plan: 자동발주제외 DB 캐시 (Auto-Order Exclusion DB Cache)

> 사이트 조회 실패 시에도 자동발주 상품 제외가 동작하도록 DB 캐시 레이어 추가

## 1. 배경 및 목적

### 1-1. 선행 작업

`auto-order-exclude` 기능(PDCA 완료, 98% match rate)에서 BGF 사이트의 "발주 현황 조회 > 자동" 탭에서 자동발주 상품을 실시간 조회하여 발주 제외하는 로직이 구현되었다.

### 1-2. 현재 문제

현재 `load_auto_order_items()`는 **사이트 실시간 조회만** 사용한다:
- 사이트 접속 실패 → `_auto_order_items`가 빈 set → 자동발주 상품이 발주에 포함됨
- 드라이버 없는 환경(preview, 테스트) → 자동발주 제외 불가
- 조회 이력 없음 → 자동발주 상품 변경 추적 불가

### 1-3. 목표

사이트 조회 성공 시 DB에 캐싱하고, 사이트 접속 실패 시 DB 캐시를 fallback으로 사용하여 자동발주 제외의 안정성을 높인다.

```
현재:  사이트 조회 성공 → 제외 적용
       사이트 조회 실패 → 제외 없음 (위험!)

변경:  사이트 조회 성공 → 제외 적용 + DB 캐시 갱신
       사이트 조회 실패 → DB 캐시에서 로드 → 제외 적용
```

## 2. 현재 상태 분석

### 2-1. 기존 인프라

| 구성 요소 | 현재 상태 | 활용 방안 |
|-----------|----------|----------|
| `collect_auto_order_items()` | Set[str] 반환 (ITEM_CD만) | 상세 데이터 반환하도록 확장 |
| `load_auto_order_items()` | 사이트 전용, 실패 시 빈 set | DB fallback 추가 |
| `RealtimeInventoryRepository` | `get_unavailable_items()`, `get_cut_items()` 패턴 | 동일 패턴 적용 |
| DB Schema v14 | `SCHEMA_MIGRATIONS` 딕셔너리 기반 | v15에 테이블 추가 |
| `BaseRepository` | `_get_conn()`, `_now()` | 상속 사용 |

### 2-2. 참고 패턴 (기존 코드)

**미취급/발주중지 로드 패턴** (`auto_order.py`):
```python
def load_cut_items_from_db(self) -> None:
    cut_items = self._inventory_repo.get_cut_items()
    self._cut_items.update(cut_items)
    if cut_items:
        logger.info(f"DB에서 발주중지 상품 {len(cut_items)}개 로드됨")
```

→ 동일 패턴으로 `load_auto_order_items_from_db()` 구현

## 3. 구현 계획

### Phase 1: DB 스키마 (Schema v15)

**파일**: `src/db/models.py`, `src/config/constants.py`

```sql
-- Schema v15: 자동발주 상품 캐시 테이블
CREATE TABLE IF NOT EXISTS auto_order_items (
    item_cd TEXT PRIMARY KEY,
    item_nm TEXT,
    mid_cd TEXT,
    detected_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_auto_order_items_updated
    ON auto_order_items(updated_at);
```

- `active` 컬럼 불필요 — 매 사이트 조회 성공 시 전체 교체 (DELETE + INSERT)
- `detected_at`: 최초 감지 시점
- `updated_at`: 마지막 사이트 조회에서 확인된 시점

### Phase 2: Repository 클래스

**파일**: `src/db/repository.py`

```python
class AutoOrderItemRepository(BaseRepository):
    """자동발주 상품 캐시 저장소"""

    def get_all_item_codes(self) -> List[str]:
        """캐싱된 자동발주 상품코드 목록 반환"""

    def get_all_detail(self) -> List[Dict[str, Any]]:
        """상세 정보 포함 전체 목록"""

    def refresh(self, items: List[Dict[str, str]]) -> None:
        """사이트 조회 결과로 전체 교체 (트랜잭션)
        1. 기존 데이터 DELETE
        2. 새 데이터 INSERT
        """

    def get_count(self) -> int:
        """캐시된 상품 수"""

    def get_last_updated(self) -> Optional[str]:
        """마지막 캐시 갱신 시각"""
```

### Phase 3: Collector 확장

**파일**: `src/collectors/order_status_collector.py`

현재 `collect_auto_order_items()`는 `Set[str]` (ITEM_CD만) 반환. DB에 저장하려면 ITEM_NM, MID_CD도 필요.

```python
# 기존
def collect_auto_order_items(self) -> Set[str]:

# 신규 추가
def collect_auto_order_items_detail(self) -> List[Dict[str, str]]:
    """자동발주 상품 상세 목록 수집
    Returns: [{"item_cd": "...", "item_nm": "...", "mid_cd": "..."}, ...]
    """
```

JS에서 ITEM_CD뿐 아니라 ITEM_NM, MID_CD도 함께 추출.

### Phase 4: auto_order.py 통합

**파일**: `src/order/auto_order.py`

변경 흐름:

```
load_auto_order_items()  # 변경
  ├─ 사이트 조회 시도
  │   ├─ 성공 → _auto_order_items = site_items
  │   │         DB 캐시 갱신 (auto_order_repo.refresh)
  │   │         logger.info("사이트에서 N개 조회 + DB 캐시 갱신")
  │   │
  │   └─ 실패 → DB 캐시에서 로드 (fallback)
  │             _auto_order_items = db_items
  │             logger.warning("사이트 조회 실패 — DB 캐시 N개 사용")
  │
  └─ 드라이버 없음 → DB 캐시에서 로드
                       logger.info("드라이버 없음 — DB 캐시 N개 사용")
```

## 4. 변경 파일 요약

| # | 파일 | 변경 유형 | 설명 |
|---|------|----------|------|
| 1 | `src/config/constants.py` | 수정 | `DB_SCHEMA_VERSION` 14 → 15 |
| 2 | `src/db/models.py` | 수정 | `SCHEMA_MIGRATIONS[15]` 추가 |
| 3 | `src/db/repository.py` | 수정 | `AutoOrderItemRepository` 클래스 추가 |
| 4 | `src/collectors/order_status_collector.py` | 수정 | `collect_auto_order_items_detail()` 추가 |
| 5 | `src/order/auto_order.py` | 수정 | DB fallback + 캐시 갱신 로직 |

## 5. 주요 리스크 및 대응

| 리스크 | 확률 | 대응 |
|--------|------|------|
| DB 마이그레이션 실패 (기존 DB) | 저 | `IF NOT EXISTS` 사용, 기존 테이블에 영향 없음 |
| 사이트 조회 성공 + DB 저장 실패 | 극저 | 사이트 데이터는 이미 메모리에 있으므로 발주 진행 |
| 캐시 데이터가 오래됨 (사이트 장기 미접속) | 저 | `updated_at` 기록 + 로그 경고 (예: 3일 이상 미갱신 시 경고) |
| 자동발주 상품이 0건인 경우 DB 전체 삭제 | 중 | 사이트 조회 결과가 0건이면 DELETE 스킵 (기존 캐시 유지) |

## 6. 핵심 설계 원칙

1. **사이트 우선**: 사이트 조회 성공 시 항상 사이트 데이터 사용 (DB는 보조)
2. **안전한 fallback**: 사이트 실패 → DB 캐시 → 빈 set (3단계 fallback)
3. **전체 교체**: 부분 업데이트 대신 DELETE + INSERT로 단순화 (상품이 자동에서 빠지는 케이스 처리)
4. **0건 보호**: 사이트 조회 결과 0건이면 캐시 삭제하지 않음 (사이트 오류 가능성)
5. **기존 동작 유지**: DB 캐시 추가로 인해 기존 사이트 조회 로직이 변경되면 안 됨

## 7. 검증 계획

1. **스키마 마이그레이션**: v14 → v15 자동 마이그레이션 확인
2. **Repository 단위 테스트**: refresh(), get_all_item_codes(), get_count()
3. **사이트 성공 시나리오**: 조회 → DB 저장 → DB 확인
4. **사이트 실패 시나리오**: DB에 캐시 있는 상태에서 사이트 실패 → DB fallback 작동 확인
5. **0건 보호**: 사이트 0건 반환 시 기존 캐시 유지 확인
6. **preview 모드**: 드라이버 없이 DB 캐시만으로 제외 동작 확인

## 8. 다음 단계

> `/pdca design 자동발주제외-DB캐시` → Repository 메서드 상세 설계, JS 확장 스크립트, 마이그레이션 SQL 확정
