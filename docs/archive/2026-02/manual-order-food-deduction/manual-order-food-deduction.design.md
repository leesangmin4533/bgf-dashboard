# Design: 일반(수동) 발주 수집 + 푸드 차감 반영

## 1. 개요

7시 자동 발주 실행 전, 사용자가 수동으로 발주한 상품(일반 탭)을 수집하여:
- **푸드 카테고리(001~005, 012)**: 예측 발주량에서 수동 발주량을 차감 (총량 유지)
- **비푸드 카테고리**: DB 기록만 (참고용)

### 핵심 규칙
- 스마트발주는 **제외 대상이 아님** (자동발주만 필터 후 제외)
- 일반 탭 조회 시 **ORD_CNT > 0 (실제 발주된 건)만** 수집

## 2. 사이트 탐색 결과 (2026-02-26 실측)

### 2-1. 탭별 행 수
| 탭 | rdGubun | 행 수 | 비고 |
|----|---------|-------|------|
| 전체 | 0 | 580 | |
| 일반 | 1 | 513 | 발주가능 전체 품목 (미발주 포함) |
| 자동 | 2 | 29 | |
| 스마트 | 3 | 38 | |
| **합계** | | **580** | **전체 = 일반 + 자동 + 스마트 (정확 일치)** |

### 2-2. dsResult 컬럼 구조 (39개)

**발주 수량 관련 (핵심)**:
| 컬럼 | 타입 | 샘플값 | 의미 |
|------|------|--------|------|
| `ORD_CNT` | BIGDECIMAL | 0, 1, 2... | **발주 배수** (0=미발주, 1+=발주됨) |
| `ORD_UNIT_QTY` | BIGDECIMAL | 1, 6, 10... | 단위 수량 |
| `PYUN_QTY` | BIGDECIMAL | 0, 1... | 편수 (ORD_CNT와 동일 확인) |
| `ORD_AMT` | BIGDECIMAL | 0, 5500... | 발주금액 (0=미발주) |
| `WONGA_AMT` | BIGDECIMAL | 0, 3640... | 원가금액 |

**상품 정보**:
| 컬럼 | 타입 | 샘플값 |
|------|------|--------|
| `ITEM_CD` | STRING(13) | "8809196617779" |
| `ITEM_NM` | STRING(36) | "도)동원리챔앤참치김치1" |
| `MID_CD` | STRING(3) | "001" |
| `MID_NM` | STRING(30) | "도시락" |
| `MID_CD_GROUP` | STRING(7) | "001 1회차" |
| `ITEM_SPEC` | STRING(18) | "425G" |
| `ITEM_WONGA` | BIGDECIMAL | 3650 |
| `HQ_MAEGA_SET` | BIGDECIMAL | 5500 |

**발주 상태/메타**:
| 컬럼 | 타입 | 샘플값 | 의미 |
|------|------|--------|------|
| `ORD_YMD` | STRING(8) | "20260226" | 발주일 (YYYYMMDD) |
| `ORD_INPUT_ID` | STRING(75) | "단품별(재택)" | **발주 방식** |
| `ORD_PSS_ID` | STRING(1) | "0" | 발주 상태 |
| `MODIFY_FLAG` | BIGDECIMAL | 1 | 수정 가능 여부 |
| `ORD_MULT_LLMT` | BIGDECIMAL | 1 | 발주 배수 하한 |
| `ORD_MULT_ULMT` | BIGDECIMAL | 99 | 발주 배수 상한 |
| `NOW_QTY` | BIGDECIMAL | 0 | 현 재고 |
| `NAP_NEXTORD` | STRING(6) | "28(27)" | 납품/차회발주 |

**기타**:
| 컬럼 | 의미 |
|------|------|
| `ORD_TURN_HMS` | 발주 마감 시간 ("100000") |
| `PYUNSU_ID` | 편수 ID ("1") |
| `JIP_ITEM_CD` | 입고 상품코드 |
| `CUT_ITEM_YN` | CUT 여부 ("0"/"1") |
| `CT_ITEM_YN` | CT 여부 |
| `STOP_PLAN_YMD` | 중지 예정일 |
| `PITEM_ID` / `PITEM_ID_NM` | 상품 속성 ("2", "5"=신상품) |
| `EVT_DC_YMD` / `EVT_DC_RATE` | 행사/할인 |
| `RB_AMT` / `RB_CON` / `RB_YMD` | 리베이트 |
| `IMG_CHK` / `IMG_URL` | 상품 이미지 |

### 2-3. ORD_INPUT_ID 분포 (일반 탭 513건)
| ORD_INPUT_ID | 건수 | 의미 |
|-------------|------|------|
| `단품별(재택)` | 262 | 단품별 발주 메뉴에서 수동 입력 |
| `발주수정(재택)` | 234 | 기존 발주 수정 |
| `분류별발주(재택)` | 9 | 분류별 일괄 발주 |
| `신상품사전안내(재택)` | 7 | 신상품 사전 안내 |
| `품절상품발주(재택)` | 1 | 품절 상품 발주 |

> **핵심**: ORD_INPUT_ID는 발주 방식을 나타냄. 모든 유형이 수동 발주에 해당.
> ORD_CNT > 0인 건만 실제 발주된 것.

### 2-4. ORD_PSS_NM
- **존재하지 않는 컬럼** (NULL 반환). ORD_PSS_ID만 존재 (모두 "0")

### 2-5. dsOrderSale (참고, 본 기능에 미사용)
- 행 수: 2265 (과거 7일 이력)
- 컬럼: ORD_YMD, ITEM_CD, JIP_ITEM_CD, ORD_QTY, BUY_QTY, SALE_QTY, DISUSE_QTY, SUM_UNIT_ID
- ORD_QTY/BUY_QTY 대부분 빈값 → **dsResult의 ORD_CNT 사용이 정확**

## 3. 수정 파일 목록

| 파일 | 변경 유형 | 내용 |
|------|----------|------|
| `src/collectors/order_status_collector.py` | 수정 | `click_normal_radio()`, `collect_normal_order_items()` 추가 |
| `src/infrastructure/database/repos/manual_order_repo.py` | 신규 | ManualOrderItemRepository |
| `src/infrastructure/database/repos/__init__.py` | 수정 | ManualOrderItemRepository export 추가 |
| `src/scheduler/daily_job.py` | 수정 | Phase 1.2에 일반 탭 수집 추가 |
| `src/order/auto_order.py` | 수정 | 푸드 수동발주 차감 로직 + 스마트발주 제외 기본값 변경 |
| `src/settings/constants.py` | 수정 | `EXCLUDE_SMART_ORDER` 기본값 False, 관련 상수 |
| `tests/test_manual_order_food_deduction.py` | 신규 | 테스트 |

## 4. OrderStatusCollector 변경

### 4-1. `click_normal_radio()` 신규

기존 `click_auto_radio()` (line 404) 패턴과 동일. rdGubun.set_value('1')

```python
def click_normal_radio(self) -> bool:
    """'일반' 라디오 버튼 클릭

    라디오: rdGubun (div_work.form.Div21.form)
    항목: 전체(0), 일반(1), 자동(2), 스마트(3)

    3단계 폴백 전략:
    1. 넥사크로 Radio API - rdGubun.set_value('1')
    2. "일반" 텍스트 부모 요소 클릭
    3. rdGubun 영역 내 텍스트 검색
    """
    # click_auto_radio()와 동일 구조, value='1', 텍스트='일반'
```

### 4-2. `collect_normal_order_items()` 신규

기존 `collect_auto_order_items_detail()` (line 529) 패턴 기반.
**dsResult에서 ORD_CNT > 0인 건만 수집.**

```python
def collect_normal_order_items(self) -> Optional[List[Dict]]:
    """일반(수동) 발주 상품 목록 수집

    일반 탭(rdGubun='1')의 dsResult에서 실제 발주된 건(ORD_CNT > 0)만 수집.

    Returns:
        [{
            "item_cd": "8800336394352",
            "item_nm": "도)밥반찬반돈까스1",
            "mid_cd": "001",
            "mid_nm": "도시락",
            "ord_ymd": "20260226",       # YYYYMMDD
            "ord_cnt": 1,                # dsResult.ORD_CNT (발주배수)
            "ord_unit_qty": 1,           # dsResult.ORD_UNIT_QTY
            "order_qty": 1,              # 실제 수량 = ord_cnt * ord_unit_qty
            "ord_input_id": "단품별(재택)", # 발주 방식
            "ord_amt": 5500,             # 발주금액
        }, ...]
        실패 시 None, 발주된 건 0건 시 빈 리스트
    """
```

**JS 추출 로직:**
```javascript
// dsResult에서 ORD_CNT > 0 필터링
const items = [];
for (let i = 0; i < ds.getRowCount(); i++) {
    const ordCnt = parseInt(getVal(i, 'ORD_CNT')) || 0;
    if (ordCnt <= 0) continue;  // 미발주 건 스킵

    const cd = getVal(i, 'ITEM_CD');
    if (!cd) continue;

    const unitQty = parseInt(getVal(i, 'ORD_UNIT_QTY')) || 1;
    items.push({
        item_cd: cd,
        item_nm: getVal(i, 'ITEM_NM'),
        mid_cd: getVal(i, 'MID_CD'),
        mid_nm: getVal(i, 'MID_NM'),
        ord_ymd: getVal(i, 'ORD_YMD'),
        ord_cnt: ordCnt,
        ord_unit_qty: unitQty,
        order_qty: ordCnt * unitQty,
        ord_input_id: getVal(i, 'ORD_INPUT_ID'),
        ord_amt: parseInt(getVal(i, 'ORD_AMT')) || 0
    });
}
return {items: items, total: ds.getRowCount(), ordered: items.length};
```

> **날짜 필터 불필요**: 일반 탭 dsResult는 이미 당일 발주 화면만 표시.
> 탐색 결과 모든 행의 ORD_YMD가 "20260226"(오늘)으로 확인됨.

## 5. DB 테이블 + Repository

### 5-1. manual_order_items 테이블 (매장별 DB)

```sql
CREATE TABLE IF NOT EXISTS manual_order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_cd TEXT NOT NULL,
    item_nm TEXT,
    mid_cd TEXT,
    mid_nm TEXT,
    order_qty INTEGER NOT NULL,         -- 실제 발주 수량 (ord_cnt * ord_unit_qty)
    ord_cnt INTEGER,                    -- 발주 배수
    ord_unit_qty INTEGER DEFAULT 1,     -- 단위 수량
    ord_input_id TEXT,                  -- 발주 방식 (단품별/발주수정/분류별 등)
    ord_amt INTEGER DEFAULT 0,          -- 발주금액
    order_date TEXT NOT NULL,           -- YYYY-MM-DD
    collected_at TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(item_cd, order_date)
);
```

### 5-2. ManualOrderItemRepository

```python
class ManualOrderItemRepository(BaseRepository):
    db_type = "store"

    def refresh(self, items: List[Dict], order_date: str, store_id: str) -> int:
        """당일 수동 발주 데이터 갱신 (DELETE + INSERT)
        해당 order_date의 기존 데이터 삭제 후 재삽입.
        """

    def get_today_food_orders(self, store_id: str) -> Dict[str, int]:
        """당일 푸드 카테고리(001~005, 012) 수동 발주 반환
        Returns: {item_cd: order_qty, ...}
        mid_cd IN ('001','002','003','004','005','012') 필터
        """

    def get_today_orders(self, store_id: str) -> List[Dict]:
        """당일 전체 수동 발주 반환 (기록 조회용)"""

    def get_today_summary(self, store_id: str) -> Dict[str, Any]:
        """당일 요약: 총 건수, 푸드 건수, 비푸드 건수, 총 금액"""
```

## 6. daily_job.py Phase 1.2 변경

### 현재 `_collect_exclusion_items()` (line 621~667)

```
자동 탭 수집 → DB 저장
스마트 탭 수집 → DB 저장
```

### 변경 후 `_collect_exclusion_items()`

```
자동 탭 수집 → DB 저장 (제외용)
스마트 탭 수집 → DB 저장 (기록용, 제외하지 않음)
일반 탭 수집 → DB 저장 (푸드 차감용 + 비푸드 기록용)  ← 추가
```

**추가 코드 위치**: 기존 스마트발주 수집 블록(line 650~657) 뒤

```python
# 일반(수동) 발주 수집
from src.infrastructure.database.repos import ManualOrderItemRepository

normal_items = collector.collect_normal_order_items()
if normal_items is not None:
    today_str = datetime.now().strftime("%Y-%m-%d")
    saved = ManualOrderItemRepository(store_id=self.store_id).refresh(
        normal_items, order_date=today_str, store_id=self.store_id
    )
    result["normal_count"] = saved
    food_count = sum(1 for it in normal_items if it.get("mid_cd", "") in
                     ("001","002","003","004","005","012"))
    logger.info(
        f"일반(수동) 발주 {len(normal_items)}개 수집 "
        f"(푸드: {food_count}, 비푸드: {len(normal_items) - food_count}), "
        f"DB {saved}건 갱신"
    )
else:
    logger.warning("일반(수동) 발주 사이트 조회 실패")
```

## 7. auto_order.py 변경

### 7-1. 스마트발주 제외 기본값 변경

**현재** (line 360): `settings_repo.get("EXCLUDE_SMART_ORDER", True)`
**변경**: `settings_repo.get("EXCLUDE_SMART_ORDER", False)`

기본값이 False이므로, 기존 대시보드에서 True로 설정하지 않은 매장은 자동으로 스마트발주 비제외.

### 7-2. 푸드 수동발주 차감 로직

**삽입 위치**: `execute()` 메서드 내, 미입고+재고 반영 완료 후 ~ orderable-day split 전
(현재 line 1269 캐시 초기화 ~ line 1291 발주가능요일 분리 사이)

구체적으로: `print_recommendations()` (line 1283) 직전

```python
# ===== 푸드 수동발주 차감 =====
order_list = self._deduct_manual_food_orders(order_list, min_order_qty)
```

### 7-3. `_deduct_manual_food_orders()` 신규 메서드

```python
def _deduct_manual_food_orders(
    self,
    order_list: List[Dict[str, Any]],
    min_order_qty: int = 1
) -> List[Dict[str, Any]]:
    """푸드 카테고리 수동 발주분 차감

    Phase 1.2에서 수집된 일반 탭 수동 발주 중 푸드(001~005, 012) 항목의
    발주 수량을 예측 발주량에서 차감합니다.

    일반 탭 dsResult에서 ORD_CNT > 0인 건 = 실제 수동 발주된 건.
    order_qty = ORD_CNT * ORD_UNIT_QTY.

    예시: 예측=8, 수동발주=5 → 자동발주=3
    예시: 예측=3, 수동발주=5 → 자동발주=0 (제거)

    비푸드 카테고리는 차감하지 않음 (DB 기록만).

    Args:
        order_list: 현재 발주 목록
        min_order_qty: 최소 발주량 (차감 후 이 미만이면 제거)

    Returns:
        차감 반영된 발주 목록
    """
    from src.infrastructure.database.repos import ManualOrderItemRepository
    from src.prediction.categories.food import is_food_category

    try:
        manual_repo = ManualOrderItemRepository(store_id=self.store_id)
        manual_food_orders = manual_repo.get_today_food_orders(store_id=self.store_id)
    except Exception as e:
        logger.warning(f"수동 발주 조회 실패 (차감 건너뜀): {e}")
        return order_list

    if not manual_food_orders:
        return order_list

    logger.info(f"[수동발주 차감] 푸드 수동발주 {len(manual_food_orders)}개 확인")

    deducted_list = []
    total_deducted = 0
    removed_count = 0

    for item in order_list:
        item_cd = item.get("item_cd", "")
        mid_cd = item.get("mid_cd", "")

        if not is_food_category(mid_cd) or item_cd not in manual_food_orders:
            deducted_list.append(item)
            continue

        manual_qty = manual_food_orders[item_cd]
        original_qty = item.get("final_order_qty", 0)
        adjusted_qty = max(0, original_qty - manual_qty)

        if adjusted_qty >= min_order_qty:
            item = dict(item)  # shallow copy
            item["final_order_qty"] = adjusted_qty
            item["manual_deducted_qty"] = manual_qty
            deducted_list.append(item)
            total_deducted += (original_qty - adjusted_qty)
            logger.info(
                f"  {item.get('item_nm', item_cd)}: "
                f"{original_qty} - {manual_qty}(수동) = {adjusted_qty}"
            )
        else:
            removed_count += 1
            total_deducted += original_qty
            self._exclusion_records.append({
                "item_cd": item_cd,
                "item_nm": item.get("item_nm"),
                "mid_cd": mid_cd,
                "exclusion_type": "MANUAL_ORDER",
                "predicted_qty": original_qty,
                "detail": f"수동발주 {manual_qty}개 >= 예측 {original_qty}개",
            })
            logger.info(
                f"  {item.get('item_nm', item_cd)}: "
                f"{original_qty} - {manual_qty}(수동) = {adjusted_qty} -> 제거"
            )

    if total_deducted > 0 or removed_count > 0:
        logger.info(
            f"[수동발주 차감 완료] "
            f"차감 수량: {total_deducted}개, 제거 상품: {removed_count}개, "
            f"잔여 목록: {len(deducted_list)}개"
        )

    return deducted_list
```

## 8. 파이프라인 흐름 (변경 후)

```
Phase 1.2: 발주 현황 조회 메뉴 진입
  ├─ 자동 탭(rdGubun='2') -> 자동발주 상품 수집 -> DB 저장 (제외용)
  ├─ 스마트 탭(rdGubun='3') -> 스마트발주 상품 수집 -> DB 저장 (기록용)
  └─ 일반 탭(rdGubun='1') -> ORD_CNT>0 필터 -> DB 저장 (차감용) <-- NEW

Phase 2: execute()
  ├─ get_recommendations()         # 예측 기반 발주 목록 생성
  ├─ _exclude_filtered_items()     # 미취급/CUT/자동발주 제외 (스마트 미제외)
  ├─ prefetch + pending 반영       # 미입고/재고 차감
  ├─ _deduct_manual_food_orders()  # 푸드 수동발주 차감 <-- NEW
  ├─ print_recommendations()       # 최종 목록 출력
  ├─ orderable-day split           # 발주가능요일 분리
  └─ execute_orders()              # 실제 발주 실행
```

## 9. 엣지 케이스 처리

| 케이스 | 처리 |
|--------|------|
| 수동발주 > 예측량 | adjusted_qty=0 -> 목록에서 제거 + exclusion_record 기록 |
| 일반 탭 수집 실패 | 경고 로그, 차감 없이 전체 예측량 발주 (안전 방향) |
| 수동발주 0건 (ORD_CNT=0만) | 빈 리스트 반환, 차감 로직 스킵 |
| 비푸드 수동발주 | DB 기록만, 차감 안 함 |
| 동일 상품 중복 행 | dsResult에서 ITEM_CD 기준 ORD_CNT 합산 (JS에서 처리) |
| 스마트발주 상품 | 제외 안 함 (EXCLUDE_SMART_ORDER 기본=False) |
| ORD_CNT=0, ORD_AMT>0 | 수집 대상 아님 (ORD_CNT 기준으로 필터) |
| MODIFY_FLAG=0 | 수정 불가 상태이나, 이미 발주된 건이므로 수집 대상 |

## 10. 설정 (constants.py)

```python
# 스마트발주 제외 기본값 변경
# EXCLUDE_SMART_ORDER 기본값: True -> False
# (대시보드에서 매장별 개별 설정 가능)

# 수동발주 차감 활성화
MANUAL_ORDER_FOOD_DEDUCTION = True
```

## 11. 테스트 계획

| # | 테스트 | 설명 |
|---|--------|------|
| 1 | click_normal_radio 성공 | rdGubun.set_value('1') 호출 확인 |
| 2 | collect_normal_order_items 정상 | ORD_CNT>0 필터, 수량 계산 |
| 3 | collect_normal_order_items 전부 미발주 | ORD_CNT=0만 -> 빈 리스트 |
| 4 | collect_normal_order_items 라디오 실패 | None 반환 |
| 5 | ManualOrderItemRepository.refresh | DELETE+INSERT 동작 |
| 6 | ManualOrderItemRepository.get_today_food_orders | 푸드(001~005,012)만 필터 |
| 7 | ManualOrderItemRepository.get_today_orders | 전체 반환 |
| 8 | Phase 1.2 일반 탭 수집 통합 | daily_job에서 정상 수집+로깅 |
| 9 | 푸드 차감: 기본 | 예측8-수동5=3 |
| 10 | 푸드 차감: 초과 | 예측3-수동5=0 (제거+exclusion 기록) |
| 11 | 푸드 차감: 정확 일치 | 예측5-수동5=0 (제거) |
| 12 | 푸드 차감: 비푸드 미차감 | 맥주 수동발주 -> 차감 안 함 |
| 13 | 푸드 차감: 수집 실패 시 | 차감 건너뜀, 전체 발주 |
| 14 | 푸드 차감: 빈 수동발주 | 변경 없음 |
| 15 | 스마트발주 기본 비제외 | EXCLUDE_SMART_ORDER=False 확인 |
| 16 | 스마트발주 대시보드 ON | 설정 True -> 제외 동작 |
| 17 | ORD_CNT * ORD_UNIT_QTY | 배수 6, cnt 2 -> order_qty=12 |
| 18 | 발주방식 기록 | ord_input_id DB 저장 확인 |
