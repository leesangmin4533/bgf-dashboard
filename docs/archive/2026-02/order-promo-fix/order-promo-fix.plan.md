# 단품별 발주 행사 정보 수집 수정 계획

## Context

`order_prep_collector.py`가 gdList 그리드 컬럼 인덱스 11/12를 행사로 착각하여 **발주단위명(낱개/묶음/BOX)** 을 promo_type으로 저장하고 있음. 이로 인해 promotions 테이블에 "1+1"/"2+1" 데이터가 0건이고, 행사 조정 로직(PromotionAdjuster, PROMO_MIN_STOCK_UNITS 등)이 전부 비활성 상태.

## BGF 사이트 실제 확인 결과 (2026-02-27)

단품별 발주(STBJ030_M0) 화면의 gdList/dsItem 데이터셋 **55개 컬럼** 확인:

| 인덱스 | 컬럼 ID | 설명 | 비고 |
|--------|---------|------|------|
| 11 | `ORD_UNIT` | 발주단위명 | ← **현재 코드가 당월행사로 착각** |
| 12 | `ORD_UNIT_QTY` | 발주단위수량 | ← **현재 코드가 익월행사로 착각** |
| **34** | **`MONTH_EVT`** | **당월행사** | ← **올바른 컬럼** |
| **35** | **`NEXT_MONTH_EVT`** | **익월행사** | ← **올바른 컬럼** |
| 38 | `EVT_DC_YN` | 원가DC 여부 | 참고 |
| 46 | `EVT_DC_YMD` | 원가DC 기간 | 참고 |

## 수정 파일 및 변경 내용

### 1. `src/collectors/order_prep_collector.py`

#### (A) gdList 행사 컬럼 수정 (라인 652~660) — 핵심 수정

**Before:**
```javascript
curPromo = ds.getColumn(lastRow, ds.getColID(11)) || '';   // ORD_UNIT (발주단위)
nextPromo = ds.getColumn(lastRow, ds.getColID(12)) || '';  // ORD_UNIT_QTY (발주단위수량)
```

**After:**
```javascript
curPromo = ds.getColumn(lastRow, 'MONTH_EVT') || '';       // 당월행사
nextPromo = ds.getColumn(lastRow, 'NEXT_MONTH_EVT') || ''; // 익월행사
```

컬럼 인덱스 대신 **컬럼명 기반 조회**로 변경. 간결하고 확실함.

#### (B) 유효성 검증 함수 추가 (파일 상단, import 영역 근처)

```python
import re

_VALID_PROMO_RE = re.compile(r'^\d+\+\d+$')
_INVALID_UNIT_NAMES = {'낱개', '묶음', 'BOX', '지함'}

def _is_valid_promo(value: str) -> bool:
    """행사 유형이 유효한지 검증 (발주단위명 오염 방지)"""
    if not value or value in _INVALID_UNIT_NAMES or value.isdigit():
        return False
    return bool(_VALID_PROMO_RE.match(value)) or value in {'할인', '덤'}
```

#### (C) collect_for_item 데이터 추출 후 검증 추가 (라인 755~758 부근)

promoInfo 추출 후 검증 게이트:
```python
promo_info = data.get('promoInfo') or {}
current_month_promo = promo_info.get('current_month_promo', '')
next_month_promo = promo_info.get('next_month_promo', '')

# 유효성 검증 — 발주단위명이 혼입된 경우 빈값 처리
if not _is_valid_promo(current_month_promo):
    current_month_promo = ''
if not _is_valid_promo(next_month_promo):
    next_month_promo = ''
```

### 2. `src/infrastructure/database/repos/promotion_repo.py`

`save_monthly_promo()` (라인 77, 95) 저장 전 검증 추가:
- `_is_valid_promo()` import 또는 동일 로직으로 promo_type 검증
- 무효값(`낱개`, `묶음`, `BOX`, 순수 숫자)이면 저장 건너뛰기 + warning 로그
- product_details.promo_type 업데이트도 동일 검증

### 3. 기존 오염 데이터 정리 (일회성 스크립트)

`scripts/clean_promo_data.py` 생성:

```sql
-- promotions 테이블: 잘못된 promo_type 삭제
DELETE FROM promotions WHERE promo_type IN ('낱개','묶음','BOX','지함')
   OR (promo_type GLOB '[0-9]*' AND promo_type NOT LIKE '%+%');

-- product_details: promo_type NULL 처리
UPDATE product_details SET promo_type = NULL
 WHERE promo_type IN ('낱개','묶음','BOX','지함')
    OR (promo_type GLOB '[0-9]*' AND promo_type NOT LIKE '%+%');

-- daily_sales: promo_type NULL 처리
UPDATE daily_sales SET promo_type = NULL
 WHERE promo_type IN ('낱개','묶음','BOX','지함')
    OR (promo_type GLOB '[0-9]*' AND promo_type NOT LIKE '%+%');

-- promotion_changes: 잘못된 레코드 삭제
DELETE FROM promotion_changes WHERE old_promo IN ('낱개','묶음','BOX','지함')
   AND new_promo IN ('낱개','묶음','BOX','지함','');
```

store DB와 common DB 모두 대상. DBRouter 패턴 사용.

## 수정하지 않는 것

- CallItemDetailPopup DOM 읽기: 팝업이 열리지 않는 흐름이므로 **불필요**. 데이터셋 컬럼 `MONTH_EVT`/`NEXT_MONTH_EVT`에서 직접 조회하면 충분.
- PromotionAdjuster/PromotionManager: 기존 코드 정상. 올바른 데이터가 들어오면 자동 활성화됨.

## 검증

1. **DB 확인**: `SELECT promo_type, count(*) FROM promotions GROUP BY promo_type` — "1+1"/"2+1" 존재 확인
2. **로그 확인**: `python scripts/log_analyzer.py --search "행사|promo|MONTH_EVT" --last 24h`
3. **단위 테스트**: `_is_valid_promo()` 검증 테스트 (유효: "1+1","2+1","할인","덤" / 무효: "낱개","BOX","12","")
4. **오염 정리 후**: `SELECT promo_type, count(*) FROM promotions GROUP BY promo_type` — 발주단위명 0건 확인
