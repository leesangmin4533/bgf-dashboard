# Gap Analysis: auto-order-exclude

> **Feature**: auto-order-exclude (자동발주제외)
> **Design Reference**: `docs/02-design/features/auto-order-exclude.design.md`
> **Analyzed**: 2026-02-03
> **Match Rate**: 98%

---

## 1. 파일 구조 비교

| # | 설계 파일 | 구현 | 상태 |
|---|----------|------|------|
| 1 | `src/config/timing.py` (상수 2개 추가) | 완료 | OK |
| 2 | `src/collectors/order_status_collector.py` (3메서드 추가) | 완료 | OK |
| 3 | `src/order/auto_order.py` (필드+메서드+필터+호출) | 완료 | OK |

**파일 구조 일치율: 3/3 (100%)**

---

## 2. 기능별 상세 비교

### 2-1. timing.py — 타이밍 상수

| 설계 상수명 | 구현 상수명 | 값 | 상태 | 비고 |
|------------|-----------|---|------|------|
| `ORDER_STATUS_RADIO_CLICK_WAIT` | `OS_RADIO_CLICK_WAIT` | 2.0 | OK | 네이밍 차이 (기존 `OS_` 접두사 컨벤션 따름) |
| `ORDER_STATUS_MENU_CLOSE_WAIT` | `OS_MENU_CLOSE_WAIT` | 1.0 | OK | 네이밍 차이 (기존 `OS_` 접두사 컨벤션 따름) |

**일치율: 2/2 (100%)** — 기능 동일, 네이밍만 기존 코드 컨벤션에 맞게 변경

### 2-2. order_status_collector.py — 신규 메서드

| 설계 항목 | 구현 | 상태 | 비고 |
|----------|------|------|------|
| `click_auto_radio()` → 3단계 폴백 | 구현 완료 | OK | Strategy A/B/C 모두 포함 |
| Strategy A: "자동" 텍스트 부모 클릭 | 동일 | OK | `querySelectorAll('span, div, [class*="nexacontentsitem"]')` |
| Strategy B: img 인덱스 기반 클릭 | 동일 | OK | `imgs[1]` (0=수동, 1=자동) |
| Strategy C: 넥사크로 API `set_value('1')` | 동일 | OK | 4개 라디오 컴포넌트명 폴백 |
| `JS_CLICK_HELPER` 사용 | 구현 완료 | OK | `nexacro_helpers.py`에서 import |
| `collect_auto_order_items()` → Set[str] | 구현 완료 | OK | dsResult ITEM_CD 추출 |
| dsResult 폴링 대기 | 구현 완료 | OK | 최대 6회 × 0.5초 |
| `close_menu()` | 구현 완료 | OK | `close_tab_by_frame_id` 사용 |

**일치율: 8/8 (100%)**

### 2-3. auto_order.py — 확장

| 설계 항목 | 구현 | 상태 | 비고 |
|----------|------|------|------|
| `_auto_order_items: set` 필드 추가 | 구현 완료 | OK | `__init__()`에서 초기화 |
| `load_auto_order_items()` 메서드 | 구현 완료 | OK | OrderStatusCollector 호출 |
| 메뉴 이동 → 수집 → 탭 닫기 흐름 | 동일 | OK | |
| 실패 시 빈 set 유지 (발주 진행) | 동일 | OK | `except Exception` 처리 |
| `get_recommendations()` 필터 (improved_predictor 분기) | 구현 완료 | OK | `_cut_items` 뒤에 추가 |
| `get_recommendations()` 필터 (기존 predictor 분기) | 구현 완료 | OK | 동일 패턴으로 2곳 |
| `execute()` 호출 추가 | 구현 완료 | OK | `load_cut_items_from_db()` 뒤 |

**일치율: 7/7 (100%)**

---

## 3. 에러 핸들링 정책 비교

| 실패 시나리오 | 설계 동작 | 구현 동작 | 일치 |
|-------------|----------|----------|------|
| 드라이버 없음 | info 로그, 스킵 | `logger.info("드라이버 없음")` → return | OK |
| 메뉴 이동 실패 | warning 로그, 빈 set | `logger.warning(...)` → return | OK |
| 라디오 클릭 3단계 모두 실패 | warning 로그, 빈 set | `logger.warning(...)` → return set() | OK |
| dsResult 데이터 없음 | info 로그, 전체 발주 | `logger.info("자동발주 상품 없음")` | OK |
| 메뉴 닫기 실패 | warning 로그 | `except Exception` → warning | OK |

**일치율: 5/5 (100%)**

---

## 4. 네이밍 차이 (Non-functional Gap)

| 구분 | 설계 | 구현 | 이유 |
|------|------|------|------|
| 타이밍 상수 접두사 | `ORDER_STATUS_` | `OS_` | 기존 timing.py의 `OS_MENU_CLICK_WAIT`, `OS_DATA_LOAD_WAIT` 등과 일관성 유지 |

이 차이는 기존 코드 컨벤션을 따른 의도적 변경이며, 기능에 영향 없음.

---

## 5. 종합 평가

| 평가 항목 | 점수 |
|----------|------|
| 파일 구조 일치 | 100% |
| 기능 구현 완전성 | 100% |
| 에러 핸들링 | 100% |
| 네이밍 일관성 | 98% (의도적 차이 1건) |
| **종합 Match Rate** | **98%** |

### 미진행 사항

- **실 사이트 테스트**: BGF 로그인 필요, 현재 세션에서 불가
- **설계서 네이밍 업데이트**: `ORDER_STATUS_*` → `OS_*` 반영 (선택, 낮은 우선도)

### 결론

설계 대비 구현 일치율 **98%**. 기능적 갭 없음. 네이밍 차이 1건은 기존 코드 컨벤션을 따른 합리적 변경.
PDCA 기준 90% 이상 달성 — **Report 단계 진행 가능**.
