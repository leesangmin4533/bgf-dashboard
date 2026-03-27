# Plan: new-product-detail-fetch

## 1. 개요

### 배경

신제품 입고 감지(Phase 1.1) 시 `products`와 `product_details`에 등록하지만, **BGF 발주사이트의 정확한 정보가 아닌 추정값/기본값**으로 저장하고 있음.

| 필드 | 현재 소스 | 문제 |
|------|----------|------|
| `mid_cd` (카테고리) | 이름 패턴 추정 (~15개 규칙) | 매칭 안 되면 "999" |
| `expiration_days` | 추정된 mid_cd 기반 | mid_cd 오류 시 연쇄 오류 |
| `orderable_day` | "일월화수목금토" 고정 | 실제 발주가능요일 무시 |
| `sell_price` | NULL | 미수집 |
| `margin_rate` | NULL | 미수집 |
| `lead_time_days` | 1 고정 | 실제와 다를 수 있음 |

### 목표

1. **기존 신제품 감지 로직은 그대로 유지** (ReceivingCollector 변경 없음)
2. 별도 시간에 **CallItemDetailPopup**을 통해 정확한 정보 일괄 수집
3. 수집된 카테고리(mid_cd) 정보를 **common.db**에 저장
4. (후속 작업) 카테고리 기반 상세 분류 모듈 별도 설계

### 접근 방식

```
[기존] Phase 1.1 입고감지 → 추정값 등록 (변경 없음)
                ↓
[신규] 별도 스케줄 → CallItemDetailPopup 일괄 조회 → common.db 업데이트
```

## 2. 현재 상태 분석

### 2-1. 이미 있는 것 (재활용 가능)

| 자산 | 파일 | 역할 |
|------|------|------|
| `CallItemDetailPopup` 접근 패턴 | `fail_reason_collector.py` | 팝업 열기/대기/추출/닫기 4단계 폴백 |
| `dsItemDetail` 데이터셋 접근 | `fail_reason_collector.py:620` | `dsVal(ds, 0, 'COL_NAME')` 패턴 |
| `dsItemDetailOrd` 데이터셋 접근 | `fail_reason_collector.py:675` | 발주요일 등 추가 정보 |
| `ProductDetailRepository` | `product_detail_repo.py` | UPSERT, bulk_update 메서드 |
| `edt_pluSearch` 바코드 입력 | `fail_reason_collector.py:440` | 4단계 폴백 입력 패턴 |
| 팝업 열기/폴링/닫기 | `fail_reason_collector.py:450-740` | FR_POPUP_* 타이밍 상수 |
| `ProductInfoCollector` | `product_info_collector.py` | `dsItemDetail` 팝업 추출 (1100-1260행) |

### 2-2. CallItemDetailPopup 데이터셋 구조 (코드에서 확인된 컬럼)

#### dsItemDetail (메인)
```
확인된 컬럼:
- ITEM_NM          ✅ 상품명
- ITEM_CD / PLU_CD ✅ 상품코드
- ORD_PSS_ID_NM    ✅ 발주가능상태
- ORD_STOP_YMD     ✅ 발주정지일
- EXPIRE_DAY       ✅ 유통기한(일)
- ORD_UNIT_QTY     ✅ 발주단위수량
- ORD_UNIT_NM      ✅ 발주단위명
- CASE_UNIT_QTY    ✅ 케이스입수
- EVT_NM / PROMO_NM ✅ 행사 유형
- EVT_START_DATE    ✅ 행사 시작일
- EVT_END_DATE      ✅ 행사 종료일
```

#### dsItemDetailOrd (발주 정보)
```
확인된 컬럼:
- ORD_ADAY          ✅ 발주가능요일
- ORD_PSS_CHK_NM    ✅ 발주상태(대체)
- ITEM_NM           ✅ 상품명(대체)
- ORD_UNIT_QTY      ✅ 발주단위(대체)
```

### 2-3. ⚠️ 코드만으로 확인 불가능한 항목

> **아래 컬럼들은 dsItemDetail/dsItemDetailOrd에 실제로 있는지 확인이 필요합니다.**
> **Phase 0(디스커버리)에서 팝업 전체 컬럼을 덤프하여 확인 후, 없는 항목은 알려드리겠습니다.**

| 필드 | 추정 컬럼명 | 확인 필요 이유 |
|------|------------|---------------|
| **mid_cd (중분류코드)** | `MID_CD`, `MCLS_CD`, `M_CATE_CD` | 팝업에서 추출 시도한 코드가 어디에도 없음 |
| **mid_nm (중분류명)** | `MID_NM`, `MCLS_NM` | 동일 |
| **lrg_cd (대분류코드)** | `LRG_CD`, `LCLS_CD`, `L_CATE_CD` | 동일 |
| **sell_price (판매가)** | `SELL_PRC`, `SELL_PRICE`, `MAEGA_AMT` | 팝업에서 시도한 적 없음 |
| **cost_price (원가)** | `WONGA_AMT`, `COST_PRC`, `BUGA_AMT` | 동일 |
| **margin_rate** | `MARGIN_RATE`, `IYUL` | 동일 |
| **lead_time_days** | `LEAD_TIME`, `NAIP_TERM` | 동일 |
| **vendor (거래처)** | `CUST_CD`, `CUST_NM` | 부분 확인 (receiving에서만) |

## 3. 수정 계획 (2단계)

### Phase 0: 디스커버리 (팝업 컬럼 전수 조사) — 최우선

**목적**: dsItemDetail + dsItemDetailOrd의 **모든 컬럼명**을 실제 BGF 사이트에서 덤프

**방법**: 임의 상품 1개로 팝업 열고, 데이터셋의 전체 컬럼 목록 + 값 추출

```python
# 스크립트: scripts/discover_popup_columns.py
# 실행: python scripts/discover_popup_columns.py --item-cd 8801234567890

# JS에서 dsItemDetail 전체 컬럼 덤프:
for (let i = 0; i < ds.colcount; i++) {
    const colId = ds.getColID(i);
    const val = ds.getColumn(0, colId);
    columns.push({name: colId, value: val, type: typeof val});
}
```

**결과물**: 컬럼 목록 JSON → Plan 업데이트 → 없는 필드는 사용자에게 보고

### Phase 1: 일괄 수집기 구현

> Phase 0 결과에 따라 수집 범위 확정

**새 파일**: `src/collectors/product_detail_batch_collector.py`

**역할**: 정보 미비 상품들의 CallItemDetailPopup 일괄 조회 + common.db 업데이트

#### 수집 대상 선별 기준
```sql
-- product_details에서 정보가 부족한 상품 (fetched_at이 NULL이거나 핵심 필드 누락)
SELECT pd.item_cd
FROM product_details pd
WHERE pd.fetched_at IS NULL                        -- BGF 미조회
   OR pd.expiration_days IS NULL                    -- 유통기한 누락
   OR pd.orderable_day = '일월화수목금토'           -- 기본값 그대로
-- 추가: products.mid_cd가 '999' 또는 detected_new_products.mid_cd_source = 'fallback'
```

#### 수집 플로우
```
1. 수집 대상 목록 생성 (위 SQL)
2. 발주 화면(STBJ030_M0)의 edt_pluSearch에 바코드 입력
3. Enter → Quick Search 드롭다운 클릭 → CallItemDetailPopup 열림
4. dsItemDetail + dsItemDetailOrd 전체 추출
5. common.db 업데이트:
   - products.mid_cd (Phase 0에서 컬럼명 확정된 경우)
   - product_details: expiration_days, orderable_day, orderable_status,
     order_unit_qty, sell_price, margin_rate (가용 필드에 따라)
6. 팝업 닫기
7. 2초 대기 후 다음 상품
```

#### 기존 코드 재활용 전략
```
FailReasonCollector 패턴 복사:
├── _input_barcode()          → 그대로 재활용
├── _wait_for_popup()         → 그대로 재활용
├── _extract_product_detail() → ★ 신규 (추출 범위 확대)
├── _close_popup()            → 그대로 재활용
└── 타이밍 상수               → FR_* 상수 재활용
```

### Phase 1 수정 파일 목록

| # | 파일 | 유형 | 내용 |
|---|------|------|------|
| 1 | `scripts/discover_popup_columns.py` | **신규** | Phase 0 디스커버리 스크립트 |
| 2 | `src/collectors/product_detail_batch_collector.py` | **신규** | 일괄 수집기 (메인) |
| 3 | `src/infrastructure/database/repos/product_detail_repo.py` | 수정 | `bulk_update_from_popup()` 메서드 추가 |
| 4 | `src/infrastructure/database/repos/product_repo.py` | 수정 | `update_mid_cd()` 메서드 추가 (common.db) |
| 5 | `run_scheduler.py` | 수정 | 별도 스케줄 등록 (예: 매일 01:00) |
| 6 | `src/settings/ui_config.py` | 수정 | BATCH_DETAIL_UI 상수 추가 |
| 7 | `src/settings/timing.py` | 수정 | BD_* 타이밍 상수 추가 |
| 8 | `tests/test_product_detail_batch_collector.py` | **신규** | 테스트 |

## 4. 실행 시간 및 스케줄

| 시간 | 작업 | 비고 |
|------|------|------|
| 00:00 | 발주단위 수집 (기존) | order_unit_collect |
| **01:00** | **상품상세 일괄수집 (신규)** | product_detail_batch |
| 07:00 | 메인 플로우 (기존) | daily_job |
| 23:45 | ML 증분학습 (기존) | ml_daily_training |

- **01:00 선택 이유**: 00:00 발주단위 수집 완료 후, 07:00 메인 플로우 전에 충분한 시간
- **예상 소요**: 상품당 ~3초 (입력 0.5s + 팝업대기 0.8s + 추출 0.2s + 닫기 0.5s + 대기 1s)
- **100개 상품 = ~5분**, **500개 상품 = ~25분**
- **일일 한도**: 최대 200개 (안전마진, 07:00 전 완료 보장)

## 5. DB 저장 전략

### common.db products 테이블 (카테고리)
```sql
-- Phase 0에서 MID_CD 컬럼 확인 시:
UPDATE products
SET mid_cd = ?, updated_at = ?
WHERE item_cd = ?
AND (mid_cd = '999' OR mid_cd = '')  -- 기존 추정값만 덮어쓰기
```

### common.db product_details 테이블 (상세정보)
```sql
-- UPSERT: 기존 값 보존하면서 NULL/기본값만 갱신
UPDATE product_details
SET
    expiration_days = COALESCE(?, expiration_days),
    orderable_day = CASE WHEN orderable_day = '일월화수목금토' THEN ? ELSE orderable_day END,
    orderable_status = COALESCE(?, orderable_status),
    sell_price = COALESCE(?, sell_price),
    margin_rate = COALESCE(?, margin_rate),
    fetched_at = ?,
    updated_at = ?
WHERE item_cd = ?
```

**핵심**: 이미 정확한 값이 있는 필드는 덮어쓰지 않음 (COALESCE + 조건)

## 6. 진행 순서

```
Phase 0: 디스커버리
  ├─ discover_popup_columns.py 작성
  ├─ 실제 BGF 사이트에서 실행 (사용자)
  ├─ 결과 분석 → 사용 가능 컬럼 확정
  └─ 못 찾은 필드 → 사용자에게 보고 → Plan 재수립
      │
      ▼
Phase 1: 구현 (Design → Do → Check)
  ├─ product_detail_batch_collector.py 작성
  ├─ Repository 메서드 추가
  ├─ 스케줄러 등록
  └─ 테스트
```

## 7. Phase 0 결과에 따른 분기

### 시나리오 A: MID_CD가 dsItemDetail에 있음
→ 바로 products.mid_cd 업데이트 가능 → Phase 1 그대로 진행

### 시나리오 B: MID_CD가 팝업에 없음
→ 대안 경로 탐색 필요:
  1. 발주현황조회(STBJ070_M0) dsResult에 MID_CD가 있는지 확인
  2. 상품조회 화면(STIT010_M0)으로 별도 네비게이션 필요
  3. 사용자에게 보고 후 Plan 재수립

### 시나리오 C: sell_price/margin_rate가 팝업에 없음
→ 해당 필드는 제외하고 나머지만 수집 (영향 최소화)

## 8. 테스트 계획

- 디스커버리 스크립트 단위 테스트 (JS 파싱, 컬럼 매핑)
- 수집기 단위 테스트 (대상 선별 SQL, 추출 로직, DB 저장)
- 기존 FailReasonCollector 패턴과의 호환성
- 스케줄러 등록 및 실행 확인
- common.db 업데이트 후 기존 플로우 영향 없음 확인

## 9. 제외 범위

- ReceivingCollector 수정 (기존 유지)
- 상세 카테고리 분류 모듈 (후속 작업으로 별도 설계)
- ML 모델 재학습 (카테고리 변경 후 자동 반영 기대)
- 대시보드 UI (카테고리 정보 표시는 별도)

## 10. 위험 요소

| 위험 | 영향 | 대응 |
|------|------|------|
| 팝업에 MID_CD 없음 | 카테고리 수집 불가 | 시나리오 B → 대안 경로 |
| 서버 부하로 팝업 안 열림 | 수집 실패 | 재시도 3회 + 건너뛰기 + 다음날 재시도 |
| 01:00 세션 만료 | 로그인 필요 | 수집 시작 전 로그인 상태 확인 → 재로그인 |
| 수집 도중 사이트 점검 | 중단 | graceful shutdown + 진행상황 저장 |
