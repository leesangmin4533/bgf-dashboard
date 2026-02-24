# 웹 대시보드 스킬

## When to Use

- 대시보드에 새 탭, 패널, 모달을 추가할 때
- CSS 클래스명, HTML ID, data attribute 규칙을 확인할 때
- JS 파일 역할 분담이나 전역 변수를 파악할 때
- API 엔드포인트를 프론트엔드에 연결할 때
- 다크/라이트 테마 대응 스타일을 작성할 때

## Common Pitfalls

- ❌ CSS에 색상값 직접 하드코딩 (`color: #fff`) → 테마 전환 시 깨짐
- ✅ CSS 변수 사용 (`color: var(--text-primary)`)

- ❌ 새 탭 JS를 `app.js`에 합치기 → 파일이 비대해짐
- ✅ 탭별 독립 JS 파일 생성 (예: `home.js`, `order.js` 등)

- ❌ `document.getElementById`로 탭 전환 직접 구현 → 중복 로직
- ✅ `app.js`의 탭 전환이 `data-tab` + `tab-{name}` 패턴으로 자동 처리

- ❌ 모듈별 색상을 CSS 변수 없이 JS에서 인라인 적용 → 테마 미대응
- ✅ CSS에서 `.{탭}-phase-{이름}` 클래스로 색상 지정, JS는 구조만 담당

- ❌ `<script>` 태그를 `<head>`에 배치 → DOM 미로딩 오류
- ✅ `</body>` 직전에 배치 (기존 패턴 준수)

## Troubleshooting

| 증상 | 원인 | 해결 |
|------|------|------|
| 탭 클릭해도 콘텐츠 안 보임 | `data-tab` 값과 `id="tab-{값}"` 불일치 | nav-tab의 data-tab과 main의 id 매칭 확인 |
| 다크모드에서 텍스트 안 보임 | 색상 하드코딩 | `var(--text-primary)` 등 CSS 변수로 교체 |
| 차트 테마 안 바뀜 | `_charts` 객체에 미등록 | `getOrCreateChart(canvasId, config)` 사용 |
| 호버 툴팁 잘림 | 부모에 `overflow: hidden` | 부모에 `overflow: visible` 또는 tooltip에 `z-index: 50` |
| 모바일에서 레이아웃 깨짐 | 반응형 미적용 | `@media (max-width: 768px)` 추가 |

---

## 1. 파일 구조 + 역할 분담

```
src/web/
├── app.py                    # Flask 앱 팩토리 (create_app)
├── routes/                   # Blueprint별 라우트
│   ├── pages.py              # pages_bp       → GET /
│   ├── api_home.py           # home_bp        → /api/home/*
│   ├── api_order.py          # order_bp       → /api/order/*
│   ├── api_report.py         # report_bp      → /api/report/*
│   └── api_prediction.py     # prediction_bp  → /api/prediction/*
│
├── templates/
│   └── index.html            # SPA 단일 페이지 (모든 탭 포함)
│
└── static/
    ├── css/
    │   └── dashboard.css     # 전체 스타일 (단일 파일)
    └── js/
        ├── app.js            # 공통: 탭전환, 테마, fetch헬퍼, 차트관리
        ├── home.js           # 홈 탭: 상태카드, 파이프라인, 타임라인
        ├── order.js          # 발주 컨트롤 탭: 파라미터, 예측, 테스트
        └── report.js         # 리포트 탭: 일일/주간/카테고리/영향도
```

### JS 파일별 전역 변수

| 파일 | 전역 변수 | 용도 |
|------|----------|------|
| app.js | `_charts` | Chart.js 인스턴스 맵 (canvasId → Chart) |
| app.js | `THEME_KEY` | localStorage 키 (`'bgf-theme'`) |
| report.js | `window._weeklyLoaded` | 주간 리포트 lazy load 플래그 |
| report.js | `_categoryCache` | 카테고리 분석 캐시 |

### JS 공통 함수 (app.js에서 제공)

| 함수 | 인자 | 용도 |
|------|------|------|
| `api(url, options)` | url, {method, body} | fetch + JSON 래퍼 |
| `fmt(n)` | 숫자 | 한국어 천단위 콤마 포맷 |
| `renderCards(containerId, cards)` | ID, [{value, label, type}] | 요약 카드 그리드 렌더링 |
| `initTableSearch(inputId, tableId)` | input ID, table ID | 테이블 실시간 검색 |
| `initTableSort(tableId)` | table ID | 헤더 클릭 정렬 (data-sort="num\|text") |
| `badgeHtml(confidence)` | 'high'\|'medium'\|'low' | 신뢰도 배지 HTML |
| `getOrCreateChart(canvasId, config)` | canvas ID, Chart.js config | 차트 생성/교체 (테마 자동 적용) |

---

## 2. 탭 구조 + DOM 패턴

### 메인 탭 전환 규칙

```
nav-tabs 안에:  <a class="nav-tab" data-tab="{name}">표시명</a>
콘텐츠:         <main id="tab-{name}" class="tab-content">...</main>
```

`app.js`가 `data-tab` 값으로 `tab-{name}` 엘리먼트를 자동 매칭.
새 탭 추가 시 이 규칙만 지키면 JS 수정 없이 동작함.

### 현재 탭 목록

| data-tab | main id | JS 파일 | 설명 |
|----------|---------|---------|------|
| `home` | `tab-home` | home.js | 상태카드, 파이프라인, 빠른액션, 타임라인 |
| `order` | `tab-order` | order.js | 안전재고 파라미터, 발주 테스트, 발주 제외 |
| `report` | `tab-report` | report.js | 일일/주간/카테고리/영향도 리포트 |

### 서브탭 패턴

**리포트 탭** (report.js):
```
.sub-nav > .sub-tab[data-report="{name}"]   →   #report-{name}.report-view
```

**발주 컨트롤 탭** (order.js):
```
.order-sub-nav > .order-sub-tab[data-order-view="{name}"]   →   #{name}.order-view
```

---

## 3. CSS 네이밍 규칙

### 접두사 체계

| 접두사 | 탭/영역 | 예시 |
|--------|---------|------|
| `home-` | 홈 탭 | `.home-card`, `.home-steps`, `.home-event` |
| `order-` | 발주 컨트롤 | `.order-sub-nav`, `.order-view` |
| `test-` | 발주 테스트 | `.test-card`, `.test-safe`, `.test-danger` |
| `partial-` | 부분 발주 | `.partial-order-grid`, `.partial-order-item` |
| (없음) | 공통 | `.panel`, `.card`, `.btn`, `.badge`, `.table-wrapper` |

### 새 탭 CSS 클래스 설계 규칙

```
.{탭명}-{컴포넌트}              예: .flow-timeline, .arch-pipeline
.{탭명}-{컴포넌트}-{요소}       예: .flow-phase-header, .arch-arrow-line
.{탭명}-{컴포넌트}-{변형}       예: .flow-phase-collect, .test-safe
```

### ID 규칙

| 용도 | 패턴 | 예시 |
|------|------|------|
| 탭 콘텐츠 | `tab-{name}` | `tab-home`, `tab-flow` |
| 서브뷰 | `{name}` 또는 `report-{name}` | `order-params`, `report-weekly` |
| 상태 표시 요소 | `{탭}{역할}{부위}` (camelCase) | `homeCardScheduler`, `homeSchedulerValue` |
| 버튼 | `btn{동작}` | `btnPredict`, `btnConfirmOrder` |
| 입력 | `{탭}{역할}` | `maxItems`, `orderSearch` |

---

## 4. CSS 변수 (디자인 토큰)

### 배경

| 변수 | 다크 | 라이트 | 용도 |
|------|------|--------|------|
| `--bg-base` | `#0a0a0a` | `#ffffff` | 페이지 배경 |
| `--bg-surface` | `#111111` | `#ffffff` | 카드/패널 배경 |
| `--bg-elevated` | `#1a1a1a` | `#f5f5f5` | 헤더, 입력 배경 |
| `--bg-input` | `#141414` | `#f5f5f5` | 입력 필드 |
| `--bg-hover` | `rgba(255,255,255,0.04)` | `rgba(0,0,0,0.02)` | 호버 |
| `--bg-code` | `#161616` | `#f5f5f5` | 코드/로그 배경 |

### 텍스트

| 변수 | 다크 | 라이트 | 용도 |
|------|------|--------|------|
| `--text-primary` | `#fafafa` | `#0a0a0a` | 주요 텍스트 |
| `--text-secondary` | `#a0a0a0` | `#666666` | 보조 텍스트 |
| `--text-muted` | `#666666` | `#999999` | 비활성/힌트 |

### 시맨틱 색상

| 변수 | 다크 | 용도 |
|------|------|------|
| `--success` | `#22c55e` | 완료, 정상, 발주 |
| `--warning` | `#eab308` | 주의, 진행중 |
| `--danger` | `#ef4444` | 위험, 에러 |
| `--info` | `#3b82f6` | 정보, 수집 |

### 모듈별 색상 배정표

새 모듈 추가 시 이 테이블을 참조하여 색상 배정:

| 영역 | 색상 | HEX | 사용처 |
|------|------|-----|--------|
| 스케줄러/트리거 | gray | `var(--text-secondary)` | 비활성/보조 |
| 데이터 수집 | blue | `#3b82f6` / `var(--info)` | 정보, 수집 관련 |
| 알림 | amber | `#f59e0b` | 카카오 발송 |
| 예측/발주 | green | `#22c55e` / `var(--success)` | 완료, 정상 |
| 실패/경고 | orange | `#f97316` | 경고 |
| 결과/위험 | red | `#ef4444` / `var(--danger)` | 위험, 에러 |

---

## 5. 공통 UI 컴포넌트 패턴

### 패널 (기본 컨테이너)

```html
<section class="panel">
    <h2 class="panel-title">제목</h2>
    <!-- 내용 -->
</section>
```

### 요약 카드 그리드

```html
<div class="card-grid" id="summaryCards"></div>
```
```javascript
renderCards('summaryCards', [
    { value: '123', label: '총 상품수' },
    { value: '45', label: '발주 대상', type: 'good' },  // good=green, warn=red, accent=yellow
]);
```

### 테이블 (검색 + 정렬)

```html
<input type="text" id="mySearch" class="search-box" placeholder="검색...">
<div class="table-wrapper">
    <div class="table-scroll">
        <table id="myTable">
            <thead>
                <tr>
                    <th data-sort="text">이름</th>
                    <th data-sort="num" class="text-right">수량</th>
                </tr>
            </thead>
            <tbody id="myTableBody"></tbody>
        </table>
    </div>
</div>
```
```javascript
initTableSearch('mySearch', 'myTable');
initTableSort('myTable');
```

### 모달

```html
<div id="myModal" class="modal-overlay" style="display:none" onclick="closeMyModal(event)">
    <div class="modal-content" onclick="event.stopPropagation()">
        <div class="modal-header">
            <h2 class="panel-title" style="margin:0">제목</h2>
            <button class="modal-close" onclick="closeMyModal()">&times;</button>
        </div>
        <div class="modal-body" id="myModalBody"></div>
    </div>
</div>
```

### 배지

```javascript
badgeHtml('high')   // → <span class="badge badge-high">높음</span>   (green)
badgeHtml('medium') // → <span class="badge badge-medium">보통</span> (yellow)
badgeHtml('low')    // → <span class="badge badge-low">낮음</span>    (red)
```

---

## 6. 새 탭 추가 체크리스트

1. **index.html**: `.nav-tabs`에 `<a class="nav-tab" data-tab="{name}">{표시명}</a>` 추가
2. **index.html**: `<main id="tab-{name}" class="tab-content">` 콘텐츠 패널 추가
3. **dashboard.css**: `.{name}-` 접두사로 스타일 추가 (CSS 변수 사용, 하드코딩 금지)
4. **{name}.js**: 탭 전용 JS 파일 생성 (IIFE 패턴 권장)
5. **index.html**: `</body>` 직전에 `<script src="{name}.js">` 추가
6. **반응형**: `@media (max-width: 768px)` 블록 추가
7. **(선택)** API 필요 시 Flask Blueprint route 추가 → `routes/{name}.py`

### JS 파일 템플릿

```javascript
/* BGF 발주 시스템 - {탭명} 탭 */

(function() {
    'use strict';

    function init() {
        // DOM 초기화, 이벤트 바인딩
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
```

---

## 7. API 엔드포인트 요약

### pages_bp (프리픽스 없음)

| URL | 메서드 | 설명 |
|-----|--------|------|
| `/` | GET | index.html 렌더링 |

### home_bp (`/api/home`)

| URL | 메서드 | 설명 |
|-----|--------|------|
| `/api/home/status` | GET | 통합 상태 (스케줄러, 마지막발주, 폐기위험, 파이프라인, 이벤트) |
| `/api/home/scheduler/start` | POST | 스케줄러 시작 |
| `/api/home/scheduler/stop` | POST | 스케줄러 중지 |

### order_bp (`/api/order`)

| URL | 메서드 | 설명 |
|-----|--------|------|
| `/api/order/predict` | POST | 발주 예측 실행 |
| `/api/order/adjust` | POST | 발주량 수동 조정 |
| `/api/order/categories` | GET | 카테고리 목록 |
| `/api/order/partial-summary` | POST | 선택 카테고리 발주 요약 |
| `/api/order/run-script` | POST | 스크립트 실행 (preview/dry-run/real-order 등) |
| `/api/order/script-status` | GET | 실행중 스크립트 상태+로그 |
| `/api/order/stop-script` | POST | 스크립트 중단 |
| `/api/order/exclusions` | GET | 발주 제외 설정 조회 |
| `/api/order/exclusions/toggle` | POST | 제외 설정 토글 |

### report_bp (`/api/report`)

| URL | 메서드 | 설명 |
|-----|--------|------|
| `/api/report/daily` | GET | 일일 발주 리포트 |
| `/api/report/weekly` | GET | 주간 트렌드 리포트 |
| `/api/report/category/{mid_cd}` | GET | 카테고리별 분석 |
| `/api/report/impact` | GET | 안전재고 영향도 |
| `/api/report/baseline` | POST | Baseline 저장 |

---

## 8. `<script>` 로드 순서

```html
<script src="js/app.js"></script>      <!-- 1. 공통 (탭전환, 테마, fetch, 차트관리) -->
<script src="js/order.js"></script>    <!-- 2. 발주 컨트롤 -->
<script src="js/report.js"></script>   <!-- 3. 리포트 -->
<script src="js/home.js"></script>     <!-- 4. 홈 (app.js의 api() 의존) -->
```

`app.js`가 반드시 첫 번째. 나머지는 `app.js`의 `api()`, `fmt()`, `getOrCreateChart()` 등에 의존.
