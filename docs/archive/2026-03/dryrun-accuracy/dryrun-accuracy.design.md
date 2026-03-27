# dryrun-accuracy Design Document

> **Summary**: 드라이런 Excel에 데이터 신선도 표시 + stale 경고 + 스케줄러 차이 요약 추가
>
> **Project**: BGF 자동 발주 시스템
> **Author**: AI
> **Date**: 2026-03-09
> **Status**: Draft
> **Plan**: [dryrun-accuracy.plan.md](../../01-plan/features/dryrun-accuracy.plan.md)

---

## 1. Architecture Overview

### 1.1 변경 범위

```
scripts/run_full_flow.py      ← 유일한 수정 파일
  ├ SECTION_C 컬럼 추가        (FR-04: RI조회시각 컬럼)
  ├ run_dryrun_and_export()    (FR-01: stale 판정, FR-05: 차이 경고)
  └ create_dryrun_excel()      (FR-04: stale 셀 빨간 배경)
```

**변경하지 않는 파일**: `auto_order.py`, `order_adjuster.py`, `order_data_loader.py` (기존 로직 보존)

### 1.2 데이터 흐름 (변경 후)

```
run_dryrun_and_export()
  │
  ├─ [기존] inv_repo.get_all() → all_inv
  │    └─ pending_data, stock_data 구성
  │
  ├─ [NEW-1] RI freshness 분석 (FR-01)
  │    ├─ all_inv 순회: queried_at → stale 판정
  │    ├─ ri_freshness_map = {item_cd: {queried_at, is_stale, hours_ago}}
  │    └─ 콘솔 경고 출력
  │
  ├─ [NEW-2] order_list에 freshness 정보 주입 (FR-04)
  │    └─ 각 item에 ri_queried_at, ri_stale 필드 추가
  │
  ├─ [기존] _apply_pending_and_stock_to_order_list()
  │
  ├─ [NEW-3] 차이 경고 요약 (FR-05)
  │    └─ stale 상품수, CUT 미확인수, DB캐시 사용 여부 출력
  │
  └─ create_dryrun_excel()
       └─ [NEW-4] SECTION_C에 "RI조회시각" 컬럼 추가 + stale 빨간배경
```

---

## 2. Detailed Design

### 2.1 Fix A: RI stale 판정 (FR-01)

**위치**: `run_dryrun_and_export()` L613 직후 (all_inv 조회 후)

**상수 정의** (파일 상단, 기존 상수 영역):
```python
# ─── 드라이런 데이터 신선도 기준 ───
DRYRUN_STALE_HOURS = {
    "food": 6,      # 001~005, 012, 014: 6시간
    "default": 24,   # 기타: 24시간
}
FOOD_MID_CDS = {"001", "002", "003", "004", "005", "012", "014"}
```

**로직**:
```python
# all_inv 순회 후 ri_freshness_map 구성
ri_freshness_map = {}   # {item_cd: {"queried_at": str, "is_stale": bool, "hours_ago": float}}
stale_count = 0
now = datetime.now()

for item in all_inv:
    item_cd = item.get("item_cd", "")
    queried_at = item.get("queried_at")
    is_stale = True
    hours_ago = -1.0

    if queried_at:
        try:
            qt = datetime.fromisoformat(queried_at)
            hours_ago = (now - qt).total_seconds() / 3600
            # mid_cd는 RI에 없으므로, order_list에서 판정
            is_stale = False  # 기본값, 후에 order_list 매칭 시 재판정
        except (ValueError, TypeError):
            pass

    ri_freshness_map[item_cd] = {
        "queried_at": queried_at or "",
        "is_stale": is_stale,
        "hours_ago": round(hours_ago, 1),
    }
```

**mid_cd 기반 stale 재판정**: order_list 생성 후 (L583 직후):
```python
# order_list에 freshness 주입 + mid_cd 기반 stale 재판정
for item in order_list:
    item_cd = item.get("item_cd", "")
    mid_cd = item.get("mid_cd", "")
    fresh = ri_freshness_map.get(item_cd, {})

    hours_ago = fresh.get("hours_ago", -1)
    threshold_h = DRYRUN_STALE_HOURS["food"] if mid_cd in FOOD_MID_CDS else DRYRUN_STALE_HOURS["default"]
    is_stale = hours_ago < 0 or hours_ago > threshold_h

    item["ri_queried_at"] = fresh.get("queried_at", "")
    item["ri_stale"] = is_stale
```

**콘솔 경고**:
```python
stale_in_order = [it for it in order_list if it.get("ri_stale")]
if stale_in_order:
    food_stale = sum(1 for it in stale_in_order if it.get("mid_cd", "") in FOOD_MID_CDS)
    other_stale = len(stale_in_order) - food_stale
    print(f"  [stale경고] RI 데이터 오래됨: 푸드 {food_stale}개(>{DRYRUN_STALE_HOURS['food']}h), "
          f"기타 {other_stale}개(>{DRYRUN_STALE_HOURS['default']}h)")
```

### 2.2 Fix B: Excel freshness 컬럼 (FR-04)

**SECTION_C 변경** (L72~80):

```python
# 기존:
SECTION_C = {
    "name": "C. 재고/필요량",
    "columns": [
        ("현재재고",    "current_stock"),
        ("미입고",      "pending_receiving_qty"),
        ("안전재고",    "safety_stock"),
        ("필요량",      "need_qty"),
    ],
}

# 변경:
SECTION_C = {
    "name": "C. 재고/필요량",
    "columns": [
        ("현재재고",    "current_stock"),
        ("미입고",      "pending_receiving_qty"),
        ("안전재고",    "safety_stock"),
        ("필요량",      "need_qty"),
        ("RI조회시각",  "ri_queried_at"),   # NEW
    ],
}
```

**파급 변경 (컬럼 수 29→30)**:
```
COLUMN_DESCRIPTIONS: 16번째 위치("실제로 더 필요한 수량" 뒤)에 추가
  → "재고 데이터 조회 시각"

COL_WIDTHS: SECTION_C 영역에 16 추가
  → [10, 10, 10, 10, 16]  (기존 [10, 10, 10, 10])

TOTAL_COLS: 29 → 30

FLOAT_COLS, INT_COLS: 기존 컬럼 인덱스 +1 (Q열 이후 전부 1칸씩 이동)
  → FLOAT_COLS = set(range(8, 21)) | {23}   (22→23)
  → INT_COLS = set(range(24, 30))            (23~28 → 24~30 중 정수)

COL_S(ML가중치): 19 → 20
COL_AB(TOT_QTY): 28 → 29
COL_AC(모델타입): 29 → 30

AutoFilter: "A2:AC2" → "A2:AD2"
```

**stale 셀 빨간 배경** (create_dryrun_excel 조건부 서식):
```python
# 규칙 E (NEW): RI stale → RI조회시각 셀 배경 연빨강(FFC7CE)
COL_RI = 17  # RI조회시각 (Q열)
for r in range(DATA_START, data_end + 1):
    ri_val = ws.cell(row=r, column=COL_RI).value
    # order_list에서 ri_stale 확인
    item_idx = r - DATA_START
    if item_idx < len(order_list) and order_list[item_idx].get("ri_stale"):
        ws.cell(row=r, column=COL_RI).fill = PatternFill(
            start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
```

### 2.3 Fix C: 차이 경고 요약 (FR-05)

**위치**: `run_dryrun_and_export()` 말미, Excel 저장 직전 (L695 부근)

```python
# ─── 스케줄러 차이 경고 ───
print()
print("─" * 60)
print("[스케줄러 차이 경고] 실제 7시 발주와 다를 수 있는 항목:")
stale_cnt = sum(1 for it in order_list if it.get("ri_stale"))
if stale_cnt > 0:
    print(f"  - RI stale 상품: {stale_cnt}개 (재고/미입고가 실제와 다를 수 있음)")
cut_cnt = len(system._cut_items)
if cut_cnt > 0:
    print(f"  - CUT 상품: {cut_cnt}개 (DB 기준, 실제 CUT 해제 미반영 가능)")
print(f"  - 자동/스마트발주: DB 캐시 사용 (사이트 최신 목록과 다를 수 있음)")
print(f"  - 미입고 소스: DB(RI+OT) 캐시 (실시간 BGF 조회 아님)")
print("─" * 60)
```

### 2.4 CUT stale 경고 (FR-03)

**위치**: `run_dryrun_and_export()` L571 직후 (CUT 로드 후)

```python
# CUT 상품의 RI 조회 시점 확인
if system._cut_items:
    old_cut = 0
    for cd in system._cut_items:
        fresh = ri_freshness_map.get(cd, {})
        if fresh.get("hours_ago", -1) > 72:  # 3일 이상 미확인
            old_cut += 1
    if old_cut > 0:
        print(f"  [경고] CUT 상품 중 {old_cut}개가 72h+ 미확인 "
              f"(실제 CUT 해제 가능성)")
```

**주의**: CUT stale 경고는 ri_freshness_map 구성 이후에 실행해야 하므로, 실제 코드에서는 all_inv 처리 후로 위치 조정.

---

## 3. Implementation Order

```
Step 1: SECTION_C 컬럼 추가 + 파급 상수 변경
  └ SECTION_C, COLUMN_DESCRIPTIONS, COL_WIDTHS, TOTAL_COLS,
    FLOAT_COLS, INT_COLS, COL_S, COL_AB, COL_AC, AutoFilter 범위

Step 2: run_dryrun_and_export() — ri_freshness_map 구성
  └ DRYRUN_STALE_HOURS, FOOD_MID_CDS 상수 정의
  └ all_inv 순회 → ri_freshness_map 구성

Step 3: run_dryrun_and_export() — order_list에 freshness 주입
  └ ri_queried_at, ri_stale 필드 추가
  └ 콘솔 stale 경고

Step 4: create_dryrun_excel() — stale 조건부 서식
  └ 규칙 E: RI조회시각 stale 빨간배경

Step 5: run_dryrun_and_export() — 차이 경고 요약
  └ 말미에 스케줄러 차이 경고 출력

Step 6: CUT stale 경고 (위치 조정)

Step 7: 테스트 작성
```

---

## 4. Test Plan

### 4.1 단위 테스트

| # | 테스트 | 검증 내용 |
|---|---|---|
| T-01 | stale 판정: 푸드 6h 초과 | queried_at이 7h 전 + mid_cd="001" → is_stale=True |
| T-02 | stale 판정: 비푸드 24h 이내 | queried_at이 20h 전 + mid_cd="040" → is_stale=False |
| T-03 | stale 판정: queried_at 없음 | queried_at=None → is_stale=True |
| T-04 | SECTION_C 컬럼 수 | len(SECTION_C["columns"]) == 5 |
| T-05 | TOTAL_COLS 정합 | sum(len(s["columns"]) for s in ALL_SECTIONS) == TOTAL_COLS |
| T-06 | Excel 컬럼 수 일치 | len(COLUMN_DESCRIPTIONS) == TOTAL_COLS |
| T-07 | COL_WIDTHS 수 일치 | len(COL_WIDTHS) == TOTAL_COLS |

### 4.2 통합 테스트

| # | 테스트 | 검증 내용 |
|---|---|---|
| T-08 | 드라이런 실행 후 Excel 생성 | output 파일 존재 + 시트 2개 |
| T-09 | Excel RI조회시각 컬럼 존재 | Q열 헤더 = "RI조회시각" |
| T-10 | 차이 경고 콘솔 출력 | stdout에 "스케줄러 차이 경고" 포함 |

---

## 5. Risks

| Risk | Mitigation |
|---|---|
| 기존 Excel 파싱 스크립트가 29컬럼 하드코딩 | TOTAL_COLS 상수로 관리, 외부 참조 없음 확인 |
| FLOAT_COLS/INT_COLS 인덱스 밀림으로 서식 깨짐 | Step 1에서 모든 인덱스 일괄 변경 |
| ri_freshness_map 메모리 (5190개) | dict 5K개: ~500KB, 문제없음 |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-03-09 | Initial design | AI |
