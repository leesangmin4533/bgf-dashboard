# Plan: 자동발주제외 (Auto-Order Exclusion)

> 발주 현황 조회 > 자동 탭의 상품 목록을 조회하여 예측 발주 목록에서 제외

## 1. 배경 및 목적

BGF 스토어 시스템의 "발주 > 발주 현황 조회" 화면에서 **"자동"** 탭에 등록된 상품은 BGF 본부에서 자동 발주를 관리하는 상품이다. 이 상품들에 대해 우리 시스템이 중복으로 발주하면 과잉발주가 발생한다.

**목표**: 자동발주 대상 상품 목록을 사전에 조회하여 예측 발주 목록에서 제외함으로써 중복 발주를 원천 차단한다.

## 2. 현재 상태 분석

### 2-1. 기존 인프라 (활용 가능)

| 구성 요소 | 현재 상태 | 활용 방안 |
|-----------|----------|----------|
| `OrderStatusCollector` | `src/collectors/order_status_collector.py` 존재 | 자동 탭 조회 메서드 추가 |
| Frame ID | `STBJ070_M0` (`ui_config.py` 등록 완료) | 그대로 사용 |
| DS Path | `div_workForm.form.div_work.form` 등록 완료 | 그대로 사용 |
| 메뉴 이동 | `navigate_to_order_status_menu()` 구현 완료 | 그대로 사용 |
| `nexacro_helpers` | `click_menu_by_text()`, `wait_for_frame()` | 그대로 사용 |
| `auto_order.py` | `_unavailable_items`, `_cut_items` 제외 패턴 존재 | 동일 패턴으로 `_auto_order_items` 추가 |

### 2-2. 미구현 부분 (신규 필요)

| 항목 | 설명 |
|------|------|
| "자동" 라디오 버튼 클릭 | 발주 현황 조회 화면 진입 후 "자동" 탭 선택 필요 |
| 자동 탭 상품 목록 파싱 | dsResult에서 자동발주 상품코드 추출 |
| `auto_order.py` 제외 로직 | `_auto_order_items` set 기반 필터링 |
| DB 캐시 (선택) | 매번 사이트 조회 대신 DB에 자동발주 상품 캐싱 |

## 3. 사이트 화면 구조 분석

### 3-1. 발주 현황 조회 화면 (STBJ070_M0)

```
발주 > 발주 현황 조회
┌──────────────────────────────────────────┐
│  [수동]  [자동]  [전체]                    │  ← 라디오 버튼 영역
├──────────────────────────────────────────┤
│  날짜 선택 (dsWeek)                       │
├──────────────────────────────────────────┤
│  상품 목록 그리드 (dsResult)               │
│  - ITEM_CD, ITEM_NM, MID_CD, ORD_CNT...  │
└──────────────────────────────────────────┘
```

### 3-2. "자동" 라디오 버튼 클릭 방법

사용자 제공 정보: 라디오 버튼 이미지는 `nexaiconitem` 클래스의 `<img>` 태그.

```
<img class="nexaiconitem" src="...rdo_WF_Radio_S.png?...">
```

**클릭 전략**: 넥사크로 라디오 버튼은 DOM에서 `nexaiconitem` img 또는 그 부모 요소를 클릭. "자동" 텍스트와 연관된 라디오 컴포넌트를 찾아 클릭해야 함.

**탐색 필요 사항** (Design 단계에서 실제 DOM 확인):
1. "자동" 텍스트 요소 위치 (text 노드 or `id*=":text"` 패턴)
2. 라디오 버튼과 텍스트의 DOM 관계 (sibling / parent-child)
3. 클릭 후 데이터 갱신 대기 시간
4. dsResult가 자동 탭 전환 후 자동 갱신되는지 또는 별도 조회 버튼 필요한지

## 4. 구현 계획

### Phase 1: 자동 탭 상품 조회 (OrderStatusCollector 확장)

**파일**: `src/collectors/order_status_collector.py`

#### 추가 메서드:

```
click_auto_radio_button() → bool
    - "자동" 라디오 버튼 클릭
    - 데이터 갱신 대기
    - dsResult 행 수 > 0 확인

collect_auto_order_items() → Set[str]
    - click_auto_radio_button() 호출
    - dsResult에서 ITEM_CD 전체 추출
    - 상품코드 set 반환
```

### Phase 2: 자동발주 제외 로직 (AutoOrderSystem 확장)

**파일**: `src/order/auto_order.py`

#### 변경 사항:

1. `__init__()`: `self._auto_order_items: Set[str] = set()` 추가
2. 신규 메서드: `load_auto_order_items_from_site(driver)` → OrderStatusCollector 호출
3. `get_recommendations()`: 기존 `_unavailable_items`, `_cut_items` 제외 패턴과 동일하게 `_auto_order_items` 제외 추가
4. `execute()`: 발주 전 자동발주 상품 조회 호출 (옵션)

#### 제외 시점 (execute 흐름):

```
execute()
  ├─ load_unavailable_from_db()      # 기존
  ├─ load_cut_items_from_db()        # 기존
  ├─ load_auto_order_items()         # ★ 신규
  ├─ get_recommendations()           # 내부에서 _auto_order_items 제외
  ├─ prefetch_pending_quantities()
  └─ executor.execute_orders()
```

### Phase 3: DB 캐시 (선택적)

**테이블**: `auto_order_items` (선택 — 매번 사이트 조회로 충분하면 불필요)

```sql
CREATE TABLE IF NOT EXISTS auto_order_items (
    item_cd TEXT PRIMARY KEY,
    item_nm TEXT,
    mid_cd TEXT,
    detected_at TEXT DEFAULT (datetime('now')),
    active INTEGER DEFAULT 1
);
```

- 사이트 조회 후 DB 저장 → 다음 실행 시 사이트 접속 실패해도 캐시 사용 가능
- 매 실행 시 사이트 조회 성공하면 캐시 갱신

## 5. 주요 리스크 및 대응

| 리스크 | 확률 | 대응 |
|--------|------|------|
| "자동" 라디오 버튼 DOM 구조가 예상과 다름 | 중 | Design 단계에서 실제 DOM 캡처하여 확인 |
| 자동 탭 전환 후 데이터 갱신 지연 | 저 | time.sleep(2~3) + dsResult 행 수 확인 루프 |
| 자동발주 상품이 날짜별로 다름 | 중 | 전체 발주일에 대해 조회하거나, 오늘 날짜 기준 조회 |
| 발주 현황 조회 후 단품별 발주로 메뉴 전환 | 저 | 기존 close_menu() + navigate 패턴 사용 |

## 6. 변경 파일 요약

| # | 파일 | 변경 유형 | 설명 |
|---|------|----------|------|
| 1 | `src/collectors/order_status_collector.py` | 수정 | `click_auto_radio_button()`, `collect_auto_order_items()` 추가 |
| 2 | `src/order/auto_order.py` | 수정 | `_auto_order_items` 필드, 로드/제외 로직 |
| 3 | `src/db/models.py` | 수정 (선택) | `auto_order_items` 테이블 스키마 |
| 4 | `src/db/repository.py` | 수정 (선택) | `AutoOrderItemRepository` 클래스 |

## 7. 검증 계획

1. **DOM 탐색 테스트**: 발주 현황 조회 화면에서 "자동" 라디오 버튼 DOM 구조 확인
2. **자동 상품 조회 테스트**: `collect_auto_order_items()` → 상품코드 set 반환 확인
3. **제외 로직 테스트**: get_recommendations() 결과에서 자동발주 상품이 빠져 있는지 확인
4. **전체 플로우 테스트**: `--preview` 모드에서 자동발주 상품 제외 후 발주 목록 비교

## 8. 다음 단계

> `/pdca design 자동발주제외` → 실제 DOM 구조 확인 후 상세 설계
>
> 특히 "자동" 라디오 버튼의 정확한 클릭 방법은 실제 사이트 DOM을 보고 확정해야 함
