# Plan: 대시보드 디자인 리팩토링 (2025-2026 트렌드)

## Context

현재 BGF 발주 시스템 대시보드는 bkit-inspired 모노크롬 디자인 시스템 기반으로 잘 동작하지만,
2025-2026 대시보드 트렌드(Bento Grid, Glassmorphism, 마이크로 인터랙션, Progress Ring 등)를
반영하여 시각적 완성도와 사용성을 향상시키려 한다.

**제약사항**: 기존 5탭 구조, Flask + Chart.js + 바닐라 JS 스택, 33개 API 엔드포인트 유지.
리팩토링 수준으로 기존 기능을 깨뜨리지 않으면서 단계적으로 적용.

## 현재 문제점

1. 차트 색상이 JS에 하드코딩 (report.js 18곳, order.js 2곳, prediction.js 3곳)
2. 홈 탭이 3개 카드만으로 비어 보임 (수직 중앙 정렬로 공간 낭비)
3. 로딩 상태가 단순 텍스트 (`-`, `로딩중...`)
4. Toast 알림 없음 (`alert()` 사용)
5. 모달 접근성 부족 (focus trap 없음, ESC가 일부만 지원)
6. 테이블 페이지네이션 없음
7. KPI 카드에 트렌드 시각화 없음

---

## Phase 1: CSS 인프라 + Quick Wins
> **영향: 높음 | 노력: 낮음 | 위험: 최소**

### 1-1. 차트 색상 CSS 변수화

**`dashboard.css`** - `:root` 블록에 차트 색상 토큰 추가:
```css
--chart-blue: #7090ff;    --chart-blue-a: rgba(112,144,255,0.7);
--chart-red: #f87171;     --chart-red-a: rgba(248,113,113,0.7);
--chart-green: #4ade80;   --chart-green-a: rgba(74,222,128,0.7);
--chart-yellow: #fbbf24;  --chart-purple: #c084fc;
--chart-orange: #fb923c;  --chart-cyan: #67c9f2;
```

**`app.js`** - 차트 색상 읽기 헬퍼 추가:
```javascript
function getChartColors() { /* CSS 변수에서 읽기 */ }
```

**`report.js`, `order.js`, `prediction.js`** - 하드코딩 색상 23곳 → CSS 변수 참조로 교체

### 1-2. Toast 알림 시스템

**`dashboard.css`** - `.toast-container`, `.toast`, 애니메이션 추가 (~30줄)
**`index.html`** - `</body>` 앞에 `<div class="toast-container" id="toastContainer"></div>`
**`app.js`** - `showToast(message, type, duration)` 함수 추가
**`order.js`, `home.js`** - `alert()` 호출 → `showToast()` 교체

### 1-3. 인라인 스타일 잔여분 정리

**`index.html`** line 439 (`#partialStatus`) - 인라인 스타일 → `.order-status-bar` 클래스
**`index.html`** line 780-786 (impact 섹션) - 인라인 스타일 → CSS 클래스

---

## Phase 2: 홈 탭 Bento Grid 레이아웃
> **영향: 높음 | 노력: 중간 | 위험: 낮음**

### 2-1. 수직 중앙 정렬 제거 → Bento Grid

**`dashboard.css`** line 2053-2059:
- `#tab-home.active`의 `display: flex; align-items: center; justify-content: center; min-height: calc(100vh - 70px)` 제거
- `display: block; padding: 32px;`로 변경

**`dashboard.css`** - 홈 레이아웃을 12열 Bento Grid로:
```css
.home-bento {
    display: grid;
    grid-template-columns: repeat(12, 1fr);
    gap: 16px;
    grid-template-areas:
        "sc sc sc sc  or or or or  ex ex ex ex"
        "pp pp pp pp  pp ev ev ev  ev ev ev ev"
        "fr fr fr fr  fr fr fr fr  fr fr fr fr";
}
```

**`index.html`** - `<div class="metrics-grid">` → `<div class="home-bento">`로 교체,
각 카드와 패널에 `grid-area` 지정

### 2-2. 메트릭 카드에 미니 스파크라인

**`index.html`** - 각 `.metric-body` 안에 `<div class="metric-spark" id="homeOrderSpark"></div>` 추가
**`dashboard.css`** - `.metric-spark` 스타일 (높이 24px, flexbox bar chart)
**`home.js`** - `renderSparkline(containerId, values)` 함수 추가
**`api_home.py`** - `/status` 응답에 `order_trend_7d` 배열 추가 (DashboardService 확장)

### 2-3. 스켈레톤 로딩 상태

**`dashboard.css`** - `.skeleton`, `.skeleton-text`, `.skeleton-value` + shimmer 애니메이션
**`home.js`** - `resetHomeCards()`에서 텍스트 대신 스켈레톤 HTML 삽입
**`prediction.js`** - 요약 카드 로딩 시 스켈레톤 적용

### 2-4. 숫자 카운터 애니메이션

**`app.js`** - `animateValue(el, start, end, duration)` (easeOutCubic)
**`home.js`** - `renderOrderCard`, `renderExpiryCard`에서 직접 textContent 대신 애니메이션 호출

---

## Phase 3: 마이크로 인터랙션 + 모달 UX
> **영향: 중간 | 노력: 낮음 | 위험: 최소**

### 3-1. 전역 ESC 핸들러 통합

**`app.js`** - 모든 `.modal` 대상 ESC 키 핸들러 추가 (1개로 통합)
**`home.js`** line 568-583 - 기존 중복 ESC 핸들러 제거

### 3-2. 모달 Focus Trap

**`app.js`** - `trapFocus(modalEl)` 유틸 함수
모달 열기 함수들에서 호출: `openExpiryModal`, `openSchedulerModal`

### 3-3. 네비게이션 탭 언더라인 인디케이터

**`dashboard.css`** - `.nav-tab::after` pseudo-element로 활성 탭 하단 바 애니메이션
현재 `.nav-tab.active`의 `background` 스타일은 유지하면서 하단 인디케이터 추가

### 3-4. 카드/버튼 마이크로 인터랙션

**`dashboard.css`**:
- `.metric-card:active { transform: scale(0.98) }` (눌림 피드백)
- `.btn-order:active` subtle press effect
- `.report-table tbody tr` hover 시 약간의 좌측 이동 (2px translateX)

---

## Phase 4: 데이터 시각화 강화
> **영향: 중간 | 노력: 중간 | 위험: 낮음**

### 4-1. 테이블 페이지네이션

**`app.js`** - `initPagination(tableId, pageSize)` 유틸 함수 (클라이언트 사이드, 25행 단위)
**`dashboard.css`** - `.table-pagination`, `.pagination-btn` 스타일
**적용 대상**: `dailyTable`, `predCategoryTable`, `homeFailTable`, `orderTable`

### 4-2. 예측 탭 Progress Ring

**`prediction.js`** - `renderPredHitRate()`에서 숫자 대신 SVG Progress Ring 렌더링
**`dashboard.css`** - `.progress-ring-container`, `.progress-ring-fill` (stroke-dashoffset 애니메이션)

### 4-3. 히트맵 테마 인식

**`report.js`** `renderHeatmapTable()` - 인라인 RGB 계산 → CSS 변수 기반
**`dashboard.css`** - `--heatmap-low`, `--heatmap-high` 다크/라이트 각각 정의

---

## Phase 5: Glassmorphism + 폰트 계층
> **영향: 낮음-중간 | 노력: 낮음 | 위험: 낮음**

### 5-1. 미묘한 Frosted Glass 효과

**`dashboard.css`** - 다크 모드에서 `.metric-card`, `.modal-content`에
`backdrop-filter: blur(16px)` + 반투명 배경 적용

### 5-2. 변수 폰트 가중치 세분화

**`dashboard.css`** - Inter의 가변 축 활용:
- `.metric-value { font-variation-settings: 'wght' 800 }`
- `.chart-title { font-variation-settings: 'wght' 620 }`

### 5-3. 그래디언트 악센트

**`dashboard.css`** - 활성 메트릭 카드 좌측 보더에 미묘한 그래디언트:
`.metric-card.active { border-left: 3px solid; border-image: linear-gradient(...) 1 }`

---

## Phase 6: 태블릿 최적화
> **영향: 중간 | 노력: 중간 | 위험: 낮음**

### 6-1. 1024px 브레이크포인트 확장

**`dashboard.css`** - 포괄적 `@media (max-width: 1024px)` 규칙:
- Bento Grid → 6열 재배치
- 리포트 차트 그리드 → 1열
- 예측 요약 카드 → 2열

### 6-2. 모바일 테이블 카드 뷰 (768px 이하)

**`dashboard.css`** - `<thead>` 숨김, `<tr>` → 카드 변환, `<td>::before { content: attr(data-label) }`
**`report.js`, `prediction.js`** - `<td>` 생성 시 `data-label` 속성 추가

---

## 수정 대상 파일 요약

| 파일 | Phase | 주요 변경 |
|------|-------|----------|
| `src/web/static/css/dashboard.css` | 1-6 | 차트 토큰, Toast, Bento Grid, 스켈레톤, 마이크로 인터랙션, Progress Ring, Glass, 반응형 |
| `src/web/static/js/app.js` | 1,2,3,4 | `getChartColors()`, `showToast()`, `animateValue()`, ESC핸들러, Focus Trap, `initPagination()` |
| `src/web/static/js/home.js` | 1,2,3 | Toast 적용, `renderSparkline()`, 스켈레톤, ESC 핸들러 제거 |
| `src/web/static/js/report.js` | 1,4,6 | 차트 색상 변수화, 히트맵 테마인식, data-label |
| `src/web/static/js/order.js` | 1 | 차트 색상 변수화, alert→Toast |
| `src/web/static/js/prediction.js` | 1,2,4 | 차트 색상, 스켈레톤, Progress Ring |
| `src/web/templates/index.html` | 1,2 | Toast 컨테이너, Bento Grid 구조, 스파크라인 placeholder, 인라인 스타일 정리 |
| `src/web/routes/api_home.py` | 2 | `/status` 응답에 7일 트렌드 배열 추가 |
| `src/application/services/dashboard_service.py` | 2 | `get_order_trend_7d()` 메서드 추가 |

## 검증 방법

1. 각 Phase 완료 후 `python -m pytest tests/ -x -q` 전체 테스트 통과 확인
2. Flask 서버 실행 (`python -m src.web.app`) 후 브라우저에서 각 탭 시각 확인
3. 다크/라이트 테마 전환 시 차트 색상 동기화 확인
4. 1024px, 768px 뷰포트에서 레이아웃 확인
5. ESC 키로 모든 모달 닫기 확인
