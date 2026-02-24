# PDCA Completion Report: 자동발주제외-DB캐시

> **Feature**: 자동발주제외 DB 캐시 (Auto-Order Exclusion DB Cache)
> **Date**: 2026-02-03
> **Match Rate**: 97%
> **Status**: COMPLETED

---

## 1. Summary

사이트 접속 실패 시에도 자동발주 상품 제외가 동작하도록 DB 캐시 레이어를 추가하였다. Schema v15, AutoOrderItemRepository, collect_auto_order_items_detail(), load_auto_order_items() DB fallback 통합까지 5개 파일 수정 완료. 라이브 사이트 테스트에서 자동발주 53건 수집 및 DB 캐시 저장 검증 완료.

---

## 2. PDCA Cycle Summary

| Phase | 상태 | 산출물 |
|-------|:----:|--------|
| Plan | DONE | `docs/01-plan/features/자동발주제외-DB캐시.plan.md` |
| Design | DONE | `docs/02-design/features/자동발주제외-DB캐시.design.md` |
| Do | DONE | 5개 파일 구현 (아래 상세) |
| Check | DONE (97%) | `docs/03-analysis/자동발주제외-DB캐시.analysis.md` |
| Report | DONE | 현재 문서 |

```
[Plan] --> [Design] --> [Do] --> [Check 97%] --> [Report]
```

---

## 3. What Was Built

### 3.1 변경 파일

| # | 파일 | 변경 | 핵심 내용 |
|---|------|------|----------|
| 1 | `src/config/constants.py` | 수정 | DB_SCHEMA_VERSION 14 → 15 |
| 2 | `src/db/models.py` | 수정 | SCHEMA_MIGRATIONS[15] — auto_order_items 테이블 |
| 3 | `src/db/repository.py` | 추가 | AutoOrderItemRepository (5개 메서드) |
| 4 | `src/collectors/order_status_collector.py` | 추가+수정 | collect_auto_order_items_detail() + click_auto_radio() 수정 |
| 5 | `src/order/auto_order.py` | 수정 | load_auto_order_items() DB fallback 통합 |

### 3.2 DB Schema v15

```sql
CREATE TABLE auto_order_items (
    item_cd TEXT PRIMARY KEY,
    item_nm TEXT,
    mid_cd TEXT,
    detected_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_auto_order_items_updated ON auto_order_items(updated_at);
```

### 3.3 AutoOrderItemRepository

| 메서드 | 기능 |
|--------|------|
| `get_all_item_codes()` | 캐싱된 상품코드 목록 (List[str]) |
| `get_all_detail()` | 상세 정보 포함 전체 목록 |
| `refresh(items)` | 사이트 데이터로 전체 교체 (0건 보호) |
| `get_count()` | 캐시 상품 수 |
| `get_last_updated()` | 마지막 갱신 시각 |

### 3.4 발주 흐름 (변경 후)

```
execute()
  ├─ load_auto_order_items()
  │   ├─ [사이트 성공] → _auto_order_items + DB 캐시 갱신
  │   └─ [사이트 실패] → DB 캐시 fallback
  │
  ├─ get_recommendations()
  │   └─ _auto_order_items 에서 53개 상품 제외
  │
  └─ execute_orders() → 제외된 목록으로 발주
```

---

## 4. Key Decisions & Lessons

### 4.1 click_auto_radio() 디버깅

**문제**: 초기 구현에서 라디오 버튼 클릭이 실패 — "전체" 뷰(105행)가 표시되고 "자동" 필터가 적용되지 않음.

**원인 분석** (DOM 디버깅 3라운드):
- 라디오 컴포넌트명: `rdGubun` (추측한 `rdo_OrdType` 등이 아님)
- 컴포넌트 경로: `div_work.form.Div21.form.rdGubun` (wf 직접이 아닌 Div21 내부)
- Value 매핑: 시각 순서(전체/일반/스토어/자동)와 값 매핑이 불일치
  - value `'2'` = 자동 (53건), value `'3'` = 스토어 (43건)

**해결**: 3단계 폴백 전략
1. **API** (최우선): `rdGubun.set_value('2')` + onitemchanged 이벤트
2. **IMG 클릭**: `imgs[3]` (시각적 "자동" 위치)
3. **텍스트 검색**: "자동" 텍스트 부모 클릭 (최후 수단)

**교훈**: 넥사크로 라디오의 시각적 배치 순서와 내부 value 매핑이 다를 수 있음. 반드시 라이브 사이트에서 각 value별 결과를 확인해야 함.

### 4.2 0건 보호 설계

사이트 조회 결과가 0건일 때 기존 DB 캐시를 삭제하지 않는 보호 로직. 사이트 오류로 인한 일시적 0건 반환 시 캐시 손실을 방지.

### 4.3 refresh() 반환값

설계의 `cursor.rowcount` 대신 `len(valid_items)` 구현. SQLite의 executemany 후 cursor.rowcount가 드라이버 버전에 따라 비결정적일 수 있어 더 안정적인 방식 채택.

---

## 5. Test Results

### 5.1 단위 테스트

| 테스트 | 결과 |
|--------|------|
| py_compile (5개 파일) | ALL PASS |
| Schema v15 마이그레이션 | PASS |
| Repository refresh() | 53건 저장 OK |
| Repository get_all_item_codes() | 53건 조회 OK |
| 0건 보호 | PASS (빈 리스트 → 기존 캐시 유지) |

### 5.2 라이브 사이트 테스트 (2026-02-03)

| 단계 | 결과 |
|------|------|
| BGF 로그인 | 성공 |
| 발주 현황 조회 메뉴 진입 | 성공 |
| rdGubun 라디오 클릭 전 | value='0' (전체), 105행 |
| click_auto_radio() | value='2' (자동), method=api |
| 클릭 후 dsResult | 53행, ORD_INPUT_ID="자동자동발주" 100% |
| collect_auto_order_items_detail() | 53건, 15개 중분류 |
| DB 캐시 저장 | 53건, updated_at=2026-02-03T15:18:56 |
| DB 캐시 재조회 | 53건 정상 |

### 5.3 라디오 값별 필터링 검증

| value | text | dsResult 행 | ORD_INPUT_ID |
|-------|------|:-----------:|-------------|
| 0 | 전체 | 105 | 혼합 (자동53+스토어43+일반9) |
| 1 | 일반 | 9 | 상품마스터, 신상품, 품절복원 |
| **2** | **자동** | **53** | **자동자동발주 53건** |
| 3 | 스토어 | 43 | 스토어자동발주 43건 |

---

## 6. Gap Analysis Summary

**Match Rate: 97%** (63 MATCH / 2 CHANGED / 0 MISSING)

| Category | Score |
|----------|:-----:|
| Schema (constants + models) | 100% |
| Repository (5 methods) | 95% |
| Collector (detail method) | 96% |
| auto_order.py (integration) | 100% |
| Error Handling Policy | 100% |
| Convention Compliance | 100% |

**차이 2건** (모두 의도적 개선, 기능 영향 없음):
1. `refresh()` 반환값: `cursor.rowcount` → `len(valid_items)`
2. 실패 로그 메시지: "(detail)" suffix 추가

---

## 7. Impact

### Before (자동발주제외 v1)
- 사이트 접속 실패 → 자동발주 상품이 발주에 포함됨 (위험)
- 드라이버 없는 환경 → 제외 불가
- 조회 이력 없음

### After (자동발주제외-DB캐시 v2)
- 사이트 실패 시 DB 캐시 53건으로 fallback → 제외 유지
- 드라이버 없는 환경(preview) → DB 캐시로 제외 가능
- auto_order_items 테이블에 이력 보존
- 어떤 실패도 발주 프로세스를 차단하지 않음

---

## 8. Related Features

| Feature | 관계 | 상태 |
|---------|------|:----:|
| auto-order-exclude | 선행 기능 (사이트 조회 기반) | Archived (98%) |
| 자동발주제외-DB캐시 | 현재 기능 (DB 캐시 추가) | Completed (97%) |

---

| Version | Date | Author |
|---------|------|--------|
| 1.0 | 2026-02-03 | Claude Code (report-generator) |
