# Design — order-unit-guard

**Feature**: order-unit-guard (발주입수 실시간 교차검증)
**Plan**: `docs/01-plan/features/order-unit-guard.plan.md`
**Created**: 2026-04-10

---

## 1. 아키텍처 개요

```
[예측 완료]
     ↓
[order_prep 상품정보 수집] ← BGF dsItem.ORD_UNIT_QTY 읽기
     ↓
  site_unit을 발주 dict에 보존 (_site_order_unit_qty)
     ↓
[발주 제출 직전]
     ↓
  _verify_order_unit() 호출
     ↓
  ┌─ db_unit == site_unit ─→ ✅ 정상 발주
  │
  ├─ db_unit != site_unit ─→ ⚠️ site값 채택 + DB 갱신 + WARNING 로그
  │
  ├─ site_unit 빈값 + db_unit > 1 ─→ ✅ DB값 신뢰 (기존 정상값)
  │
  └─ site_unit 빈값 + db_unit <= 1 ─→ ❌ 발주 보류 + ERROR 로그 + 알림
```

## 2. 입수 분류 체계

```python
# constants.py에 추가

# 푸드 카테고리 (검증 제외 - 낱개 발주가 정상)
FOOD_MID_CDS = {"001", "002", "003", "004", "005", "012"}

# 입수 분류
UNIT_TYPE_INDIVIDUAL = "individual"   # 낱개: unit = 1
UNIT_TYPE_BUNDLE = "bundle"           # 묶음: 2 <= unit <= 30
UNIT_TYPE_BOX = "box"                 # 박스: unit > 30

def classify_unit_type(unit_qty: int) -> str:
    if unit_qty <= 1:
        return UNIT_TYPE_INDIVIDUAL
    elif unit_qty <= 30:
        return UNIT_TYPE_BUNDLE
    else:
        return UNIT_TYPE_BOX
```

## 3. 데이터 흐름 상세

### 3-1. site_unit 수집 (order_prep_collector.py)

**현재**: `collect_for_item()` → dsItem에서 `ORD_UNIT_QTY` 읽음 → 반환 dict의 `order_unit_qty`에 저장

**변경**: 반환 dict에 `_site_order_unit_qty` 필드를 **별도 추가**

```python
# order_prep_collector.py collect_for_item() 반환부 (line ~832)

result = {
    ...
    'order_unit_qty': order_unit_qty,         # 기존: DB 저장/갱신용
    '_site_order_unit_qty': order_unit_qty,    # 신규: 실시간 교차검증용 (원본 보존)
    ...
}
```

**DirectAPI 배치 경로** (`_parse_direct_api_result`, line ~964)도 동일:

```python
result = {
    ...
    'order_unit_qty': order_unit_qty,
    '_site_order_unit_qty': order_unit_qty,    # 신규
    ...
}
```

> **핵심**: `order_unit_qty`는 DB 저장 시 기존 로직에 의해 변형될 수 있지만,
> `_site_order_unit_qty`는 BGF에서 읽은 원본을 그대로 보존.

### 3-2. 발주 dict 전달 (auto_order.py)

**현재**: `apply_pending_and_stock()` 내에서 order_prep 결과의 재고/미입고만 발주 dict에 반영

**변경**: `_site_order_unit_qty`도 발주 dict에 전달

```python
# auto_order.py apply_pending_and_stock() 내부
# order_prep 결과 → 발주 dict 매핑 부분

item["_site_order_unit_qty"] = prep_result.get("_site_order_unit_qty")
```

### 3-3. 교차검증 함수 (신규)

**위치**: `src/order/order_unit_verifier.py` (신규 파일)

```python
"""발주입수 실시간 교차검증 모듈"""

from src.utils.logger import get_logger
from src.settings.constants import FOOD_MID_CDS

logger = get_logger(__name__)


class OrderUnitVerifyResult:
    """검증 결과"""
    def __init__(self, verified_unit: int, status: str,
                 db_unit: int, site_unit, unit_type: str,
                 detail: str = ""):
        self.verified_unit = verified_unit
        self.status = status        # match | corrected | blocked | food_skip
        self.db_unit = db_unit
        self.site_unit = site_unit
        self.unit_type = unit_type   # individual | bundle | box
        self.detail = detail

    @property
    def is_blocked(self) -> bool:
        return self.status == "blocked"

    @property
    def is_corrected(self) -> bool:
        return self.status == "corrected"


def verify_order_unit(item: dict) -> OrderUnitVerifyResult:
    """발주 직전 입수 교차검증

    Args:
        item: 발주 dict (order_unit_qty, _site_order_unit_qty, mid_cd 필수)

    Returns:
        OrderUnitVerifyResult
    """
    mid_cd = str(item.get("mid_cd", "")).zfill(3)
    item_cd = item.get("item_cd", "")
    item_nm = item.get("item_nm", "")
    db_unit = int(item.get("order_unit_qty") or 1)
    site_unit = item.get("_site_order_unit_qty")

    # ── 푸드 카테고리 제외 ──
    if mid_cd in FOOD_MID_CDS:
        return OrderUnitVerifyResult(
            verified_unit=db_unit, status="food_skip",
            db_unit=db_unit, site_unit=site_unit,
            unit_type="individual", detail="푸드 카테고리 검증 제외"
        )

    # ── site_unit 정규화 ──
    site_int = None
    if site_unit is not None:
        try:
            site_int = int(site_unit)
            if site_int <= 0:
                site_int = None
        except (ValueError, TypeError):
            site_int = None

    # ── Case 1: site값 있음 + 일치 ──
    if site_int and db_unit == site_int:
        unit_type = _classify(db_unit)
        logger.debug(
            f"[UNIT_VERIFY] {item_nm}({item_cd}) "
            f"db={db_unit} == site={site_int} [{unit_type}] OK"
        )
        return OrderUnitVerifyResult(
            verified_unit=db_unit, status="match",
            db_unit=db_unit, site_unit=site_int,
            unit_type=unit_type
        )

    # ── Case 2: site값 있음 + 불일치 ──
    if site_int and db_unit != site_int:
        unit_type = _classify(site_int)
        logger.warning(
            f"[UNIT_MISMATCH] {item_nm}({item_cd}) mid={mid_cd} "
            f"db={db_unit} != site={site_int} [{unit_type}] "
            f"→ site값 채택, DB 갱신 예정"
        )
        return OrderUnitVerifyResult(
            verified_unit=site_int, status="corrected",
            db_unit=db_unit, site_unit=site_int,
            unit_type=unit_type,
            detail=f"db={db_unit}→site={site_int} 보정"
        )

    # ── Case 3: site값 없음 + DB > 1 ──
    if not site_int and db_unit > 1:
        unit_type = _classify(db_unit)
        logger.info(
            f"[UNIT_VERIFY] {item_nm}({item_cd}) "
            f"site=빈값, db={db_unit} [{unit_type}] → DB값 신뢰"
        )
        return OrderUnitVerifyResult(
            verified_unit=db_unit, status="match",
            db_unit=db_unit, site_unit=None,
            unit_type=unit_type, detail="site 빈값, DB>1 신뢰"
        )

    # ── Case 4: site값 없음 + DB <= 1 → 판단 불가 ──
    logger.error(
        f"[UNIT_BLOCKED] {item_nm}({item_cd}) mid={mid_cd} "
        f"db={db_unit}, site=빈값 → 입수 확인 불가, 발주 보류"
    )
    return OrderUnitVerifyResult(
        verified_unit=1, status="blocked",
        db_unit=db_unit, site_unit=None,
        unit_type="unknown",
        detail="DB=1 + site=빈값, 입수 확인 불가"
    )


def _classify(unit: int) -> str:
    """입수 분류"""
    if unit <= 1:
        return "individual"
    elif unit <= 30:
        return "bundle"
    else:
        return "box"
```

### 3-4. 검증 호출 지점

#### L1 Direct API 경로 (`direct_api_saver.py`)

```python
# _calc_order_result() 상단, 기존 묶음 가드 위치를 대체

from src.order.order_unit_verifier import verify_order_unit

def _calc_order_result(self, item, order_date, method):
    # ── 입수 교차검증 (order-unit-guard) ──
    verify = verify_order_unit(item)

    if verify.is_blocked:
        logger.error(
            f"[BLOCK/unit-guard] {item_cd} {item_nm} mid={mid_cd} "
            f"db={verify.db_unit} site={verify.site_unit} "
            f"→ 발주 거부 (입수 확인 불가)"
        )
        self._send_unit_block_notification(item, verify)
        return {
            "item_cd": item_cd, "target_qty": qty,
            "actual_qty": 0, "multiplier": 0,
            "order_unit_qty": verify.db_unit,
            "order_date": order_date,
            "success": False, "method": method,
            "message": "unit_guard_blocked",
        }

    if verify.is_corrected:
        # site값으로 보정 + DB 갱신
        item["order_unit_qty"] = verify.verified_unit
        self._update_product_detail_unit(item_cd, verify.verified_unit)

    # 검증된 unit으로 배수 계산
    unit = verify.verified_unit
    mult = max(1, (qty + unit - 1) // unit) if qty > 0 else 0
    actual = mult * unit

    logger.info(
        f"[AUDIT] {item_cd} PYUN_QTY={mult} ORD_UNIT_QTY={unit} "
        f"TOT_QTY={actual} (need={qty}) method={method} "
        f"unit_verify={verify.status}/{verify.unit_type}"
    )
    ...
```

#### L3 Selenium 경로 (`order_executor.py`)

```python
# input_product() 내, 그리드 읽기 후

verify = verify_order_unit({
    **item,
    "_site_order_unit_qty": grid_data.get("order_unit_qty") if grid_data else None,
})

if verify.is_blocked:
    logger.error(f"[BLOCK/unit-guard L3] {item_cd} → 발주 거부")
    return None

actual_order_unit_qty = verify.verified_unit
```

### 3-5. DB 자동 갱신

불일치 보정(`corrected`) 시 product_details를 즉시 갱신:

```python
# direct_api_saver.py 또는 공통 유틸

def _update_product_detail_unit(self, item_cd: str, new_unit: int):
    """입수 불일치 보정 시 DB 즉시 갱신"""
    try:
        from src.infrastructure.database.repos import ProductDetailRepository
        repo = ProductDetailRepository()
        repo.update_field(item_cd, "order_unit_qty", new_unit)
        logger.info(f"[UNIT_FIX] {item_cd} product_details.order_unit_qty → {new_unit} 갱신")
    except Exception as e:
        logger.warning(f"[UNIT_FIX] DB 갱신 실패 (발주는 site값으로 진행): {e}")
```

## 4. 검증 매트릭스

| db_unit | site_unit | mid_cd | 판정 | 사용값 | 로그 |
|---------|-----------|--------|------|--------|------|
| 6 | 6 | 044 | **match** | 6 | DEBUG |
| 1 | 6 | 044 | **corrected** | 6 (site) | WARNING + DB갱신 |
| 6 | 빈값 | 044 | **match** | 6 (DB신뢰) | INFO |
| 1 | 빈값 | 044 | **blocked** | - | ERROR + 알림 |
| 1 | 1 | 044 | **match** | 1 | DEBUG |
| 1 | 빈값 | 001 | **food_skip** | 1 | - (검증안함) |
| 100 | 100 | 900 | **match** | 100 | DEBUG |
| 1 | 100 | 900 | **corrected** | 100 (site) | WARNING + DB갱신 |

## 5. 기존 묶음 가드와의 관계

| 항목 | v2 (BUNDLE_SUSPECT) | v3 (order-unit-guard) |
|------|---------------------|----------------------|
| 적용 범위 | 특정 mid_cd만 | **전 상품** (푸드 제외) |
| 검증 방식 | DB값=1이면 의심 | DB vs **BGF 실시간값** 비교 |
| 카테고리 관리 | 수동 목록 관리 필요 | 불필요 |
| 자동 보정 | 없음 (차단만) | **site값으로 DB 자동 갱신** |
| 공존 | - | v2 가드는 폴백으로 유지 (site_unit 없을 때) |

**v2 `BUNDLE_SUSPECT_MID_CDS` 가드는 제거하지 않고 유지** — site_unit이 빈값일 때의 2차 방어선으로 활용.

## 6. 로그 설계

### 로그 레벨 기준

| 상황 | 레벨 | 태그 | 예시 |
|------|------|------|------|
| 일치 (정상) | DEBUG | `[UNIT_VERIFY]` | `db=6 == site=6 [bundle] OK` |
| 불일치 보정 | WARNING | `[UNIT_MISMATCH]` | `db=1 != site=6 [bundle] → site값 채택` |
| 차단 (판단불가) | ERROR | `[UNIT_BLOCKED]` | `db=1, site=빈값 → 발주 보류` |
| DB 갱신 | INFO | `[UNIT_FIX]` | `product_details.order_unit_qty → 6 갱신` |
| AUDIT 확장 | INFO | `[AUDIT]` | 기존 + `unit_verify=corrected/bundle` 추가 |

### AUDIT 로그 확장

```
기존: [AUDIT] 8801094962104 PYUN_QTY=14 ORD_UNIT_QTY=1 TOT_QTY=14 (need=14) method=direct_api
신규: [AUDIT] 8801094962104 PYUN_QTY=3 ORD_UNIT_QTY=6 TOT_QTY=18 (need=14) method=direct_api unit_verify=corrected/bundle
```

## 7. 영향받는 파일

| 파일 | 변경 유형 | 변경 내용 |
|------|----------|----------|
| `src/order/order_unit_verifier.py` | **신규** | 교차검증 함수 + 결과 클래스 |
| `src/collectors/order_prep_collector.py` | 수정 | `_site_order_unit_qty` 필드 추가 (2곳: collect_for_item, _parse_direct_api_result) |
| `src/order/auto_order.py` | 수정 | apply_pending_and_stock에서 site_unit 전달 |
| `src/order/direct_api_saver.py` | 수정 | `_calc_order_result()`에 verify_order_unit() 호출 |
| `src/order/order_executor.py` | 수정 | L3 input_product()에 verify_order_unit() 호출 |
| `src/settings/constants.py` | 수정 | `FOOD_MID_CDS`, `ORDER_UNIT_VERIFY_ENABLED` 추가 |

## 8. 테스트 시나리오

| # | 시나리오 | 입력 | 기대 결과 |
|---|---------|------|----------|
| T1 | 정상 묶음 일치 | db=6, site=6, mid=044 | match, unit=6 발주 |
| T2 | DB=1 site=6 불일치 | db=1, site=6, mid=044 | corrected, unit=6 발주 + DB갱신 |
| T3 | DB=1 site=100 Box | db=1, site=100, mid=900 | corrected, unit=100 발주 + DB갱신 |
| T4 | site 빈값 + DB > 1 | db=6, site=None, mid=044 | match, unit=6 (DB신뢰) |
| T5 | site 빈값 + DB=1 | db=1, site=None, mid=044 | **blocked**, 발주 안 함 |
| T6 | 양쪽 모두 1 (진짜 낱개) | db=1, site=1, mid=072 | match, unit=1 발주 |
| T7 | 푸드 카테고리 | db=1, site=None, mid=001 | food_skip, 검증 안 함 |
| T8 | DB=24 site=6 (DB가 더 큼) | db=24, site=6, mid=010 | corrected, unit=6 발주 + DB갱신 |

## 9. 구현 순서

```
1. constants.py — FOOD_MID_CDS, ORDER_UNIT_VERIFY_ENABLED 추가
2. order_unit_verifier.py — 신규 모듈 작성 + 단위 테스트
3. order_prep_collector.py — _site_order_unit_qty 전달 (2곳)
4. auto_order.py — site_unit 발주 dict 전달
5. direct_api_saver.py — L1 검증 호출
6. order_executor.py — L3 검증 호출
7. 통합 테스트 (dry_run으로 검증)
```
