# Design: unified-category-dashboard (통합 카테고리 판단 대시보드)

> 버전 1.0 | 2026-03-05 | BGF리테일 (CU) 자동발주 시스템

## 1. 개요

기존 "Dessert" 단일 탭을 **디저트 + 음료를 통합하는 "Category" 탭**으로 확장합니다.
향후 간편식/과자 등 추가 카테고리에도 플러그인 방식으로 확장 가능한 구조를 설계합니다.

### 1.1 현재 상태

```
탭 구조: [오늘 요약] [매출/분석] [발주 관리] [푸드 모니터] [Dessert] [설정]
                                                            ↑ 단일 탭
파일:
  - static/js/dessert.js (852줄) — DessertDashboard 객체
  - static/css/dessert.css — dessert- 접두사 스타일
  - api_dessert_decision.py — 6개 엔드포인트
  - DessertDecisionRepository — category_type='dessert' 필터

신규 완료:
  - beverage_decision_repo.py — category_type='beverage' 필터
  - BeverageDecisionService — 음료 판단 서비스
  - beverage_decision_flow.py — Use Case
```

### 1.2 목표

| 항목 | 변경 |
|------|------|
| 탭 이름 | Dessert → **Category** |
| 서브탭 | 없음 → **[디저트] [음료]** 서브탭 |
| API | dessert 전용 → **통합 + 개별** 엔드포인트 |
| JS | DessertDashboard 단일 → **CategoryDashboard** + 서브탭 전환 |
| CSS | dessert- 접두사 → **cat-** 접두사 (공통) + **cat-bev-** (음료 전용) |

### 1.3 설계 원칙

1. **dessert.js 비파괴**: 기존 DessertDashboard 로직 유지, CategoryDashboard가 위임
2. **API 분리**: 디저트/음료 각각 별도 엔드포인트 (공통 패턴, 별도 URL)
3. **점진적 확장**: 서브탭 추가만으로 간편식 등 확장 가능
4. **최소 수정**: index.html 탭 이름 변경 + 서브탭 UI 추가만

## 2. UI 구조

### 2.1 탭 레이아웃

```
┌─────────────────────────────────────────────────────────┐
│ [오늘 요약] [매출/분석] [발주 관리] [푸드 모니터] [Category ②] [설정] │
├─────────────────────────────────────────────────────────┤
│ [🍰 디저트 134] [🥤 음료 972]                             │  ← 서브탭
├─────────────────────────────────────────────────────────┤
│                                                         │
│   (서브탭에 따라 DessertDashboard 또는 BeverageDashboard)    │
│                                                         │
└─────────────────────────────────────────────────────────┘

② = 디저트 + 음료 미확인 STOP 합산 뱃지
```

### 2.2 서브탭 전환 로직

```
Category 탭 클릭
  → CategoryDashboard.init()
    → 활성 서브탭 확인 (기본: 'dessert')
    → if 'dessert': DessertDashboard.init()  (기존 그대로)
    → if 'beverage': BeverageDashboard.init() (신규)

서브탭 클릭 시:
  → 이전 서브탭 콘텐츠 display:none
  → 새 서브탭 콘텐츠 display:block
  → 해당 Dashboard.init() 호출
```

### 2.3 음료 서브탭 UI (BeverageDashboard)

디저트 대시보드와 동일한 레이아웃 구조를 재사용하되, 음료 전용 필드를 반영합니다.

```
┌─────────────────────────────────────────────────┐
│ ⚠ 미확인 음료 정지 권고 N건                           │  ← 알림 배너
├─────────────────────────────────────────────────┤
│ [전체 972] [KEEP 890] [WATCH 45] [STOP 12] [SKIP 25] │  ← 요약 카드 5개
├──────────────────────┬──────────────────────────┤
│ 카테고리별 판단 분포    │ 주간 판단 추이              │  ← Chart.js 2개
│ (스택바 A/B/C/D)      │ (라인 8주)                │
├──────────────────────┴──────────────────────────┤
│ [전체] [A 유제품] [B 냉장중기] [C 상온장기] [D 생수/얼음] 🔍│  ← 카테고리 필터
├─────────────────────────────────────────────────┤
│ ☐│상품│카테고리│생애주기│판매율│매대효율│추세│판단│사유│확인│  ← 상품 테이블
│   │    │      │      │     │ ★NEW │    │   │   │   │
├─────────────────────────────────────────────────┤
│       ┌─────────────────────────────────┐       │
│       │ 선택 N건 │🛑일괄정지│✅일괄유지│해제│   │  ← 플로팅 바
│       └─────────────────────────────────┘       │
└─────────────────────────────────────────────────┘
```

### 2.4 디저트 vs 음료 테이블 컬럼 차이

| 컬럼 | 디저트 | 음료 | 비고 |
|------|-------|------|------|
| 판매율 | ✅ | ✅ | 동일 계산식 |
| 폐기/판매 | ✅ (금액) | ❌ | 음료는 폐기율 낮아 불필요 |
| 매대효율 | ❌ | ✅ | 소분류 중위값 대비 비율 |
| 주간추세 | ✅ | ✅ | 동일 |
| 행사보호 | ❌ | ✅ (태그) | 행사 종료 후 보호기간 표시 |
| 계절비수기 | ❌ | ✅ (태그) | 비수기 완화 표시 |
| 카테고리 | A/B/C/D | A/B/C/D | 설명 라벨만 다름 |

### 2.5 음료 테이블 상세

| 컬럼 | 내용 | 비고 |
|------|------|------|
| ☐ | 체크박스 | STOP_RECOMMEND + 미처리만 |
| 상품 | 상품명 + 코드 + NEW/행사보호/비수기 태그 | 말줄임 180px |
| 카테고리 | A/B/C/D 뱃지 + 카테고리 설명 | 왼쪽 색상 보더 |
| 생애주기 | 신상품/성장하락/정착기 + N주 | |
| 판매율 | 프로그레스 바 + % | 색상 기준 동일 |
| 매대효율 | 수치 + 바 | ≥1.0 녹, 0.2~1.0 황, <0.2 적 |
| 주간추세 | ▲/▼ + % | |
| 판단 | KEEP/WATCH/STOP 뱃지 | |
| 사유 | 판단 사유 텍스트 | 말줄임 140px |
| 확인 | 정지확정/유지 버튼 또는 처리 결과 | |

### 2.6 음료 모달 (행 클릭)

```
┌─────────────────────────────┐
│ 매일)바리스타 아메리카노       │
│ 카테고리 C │유통기한 365일│12주 │
│ 소분류: 캔/병커피 │ 행사: 1+1   │
│ ┌───┬───┬───┬────┐         │
│ │판매│폐기│판매율│매대효율│      │
│ │ 38│  0│100%│ 0.65│      │
│ └───┴───┴───┴────┘         │
│ [주간 판매 바차트 8주]          │
│ 판단 이력                     │
│ 03-05 KEEP 정상               │
│ 02-05 KEEP 정상               │
│ [🛑 정지확정] [✅ 유지(재정)]    │
└─────────────────────────────┘
```

디저트 모달과 구조 동일, **매대효율 칸 추가 + 소분류/행사 정보 추가**.

## 3. API 설계

### 3.1 음료 전용 엔드포인트 (신규)

기존 디저트 API와 동일한 패턴, URL만 다름.

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/beverage-decision/latest` | 최신 판단 결과 |
| GET | `/api/beverage-decision/history/<item_cd>` | 상품별 이력 |
| GET | `/api/beverage-decision/summary?history=8w` | 카테고리별 집계 + 주간 추이 |
| POST | `/api/beverage-decision/action/<decision_id>` | 개별 운영자 확인 |
| POST | `/api/beverage-decision/action/batch` | 일괄 운영자 확인 |
| POST | `/api/beverage-decision/run` | 수동 실행 |

### 3.2 통합 배너용 엔드포인트 (신규)

```
GET /api/category-decision/pending-count
```

Response:
```json
{
  "success": true,
  "data": {
    "dessert": 7,
    "beverage": 12,
    "total": 19
  }
}
```

Category 탭 뱃지에 total 표시. 서브탭 뱃지에 개별 건수 표시.

### 3.3 음료 API 응답 포맷

`GET /api/beverage-decision/latest` 응답:

```json
{
  "success": true,
  "data": [
    {
      "id": 1,
      "item_cd": "8801234003",
      "item_nm": "매일)바리스타아메리카노",
      "mid_cd": "042",
      "dessert_category": "C",
      "small_nm": "캔/병커피",
      "expiration_days": 365,
      "lifecycle_phase": "established",
      "weeks_since_intro": 52,
      "total_sale_qty": 38,
      "total_disuse_qty": 0,
      "sale_rate": 1.0,
      "category_avg_sale_qty": 20.0,
      "sale_trend_pct": 5.0,
      "decision": "KEEP",
      "decision_reason": "정상",
      "is_rapid_decline_warning": 0,
      "operator_action": null,
      "judgment_cycle": "monthly",
      "category_type": "beverage"
    }
  ],
  "total": 972
}
```

> `dessert_category` 컬럼은 DB 컬럼명 그대로 반환. JS에서는 `item.dessert_category`로 접근.
> 향후 DB 리네이밍 시 `item_category` alias 추가 가능.

### 3.4 BeverageDecisionRepository 추가 메서드

음료 API를 위해 repo에 추가 필요한 메서드:

```python
# 이미 구현됨
save_decisions_batch()         # ✅
get_latest_decisions()         # ✅
get_confirmed_stop_items()     # ✅
get_pending_stop_count()       # ✅
get_decision_summary()         # ✅
batch_update_operator_action() # ✅
get_weekly_trend()             # ✅

# 신규 필요
get_item_decision_history(item_cd, limit=20)  # 모달용 상품별 이력
update_operator_action(decision_id, action, note)  # 개별 운영자 확인

# 통합 배너용 (별도 경량 함수)
get_stop_recommended_items()  # STOP_RECOMMEND item_cd set
```

## 4. 파일 구조

### 4.1 신규 파일

| 파일 | 설명 |
|------|------|
| `static/js/category.js` | CategoryDashboard — 서브탭 전환 컨트롤러 (~60줄) |
| `static/js/beverage.js` | BeverageDashboard — 음료 전용 로직 (~900줄, dessert.js 복제+수정) |
| `static/css/category.css` | 서브탭 스타일 + 공통 스타일 (~50줄) |
| `web/routes/api_beverage_decision.py` | 음료 REST API Blueprint (~180줄) |

### 4.2 수정 파일

| 파일 | 변경 |
|------|------|
| `templates/index.html` | 탭 이름 Dessert→Category, 서브탭 HTML 추가, beverage.js/category.js 로드 |
| `static/js/app.js` | `'dessert'` → `'category'` 탭 전환 로직, CategoryDashboard.init() 호출 |
| `static/js/dessert.js` | 수정 없음 (기존 유지) |
| `static/css/dessert.css` | 수정 없음 (기존 유지) |
| `web/app.py` | beverage_decision_bp 등록 |
| `beverage_decision_repo.py` | `get_item_decision_history()`, `update_operator_action()` 추가 |

### 4.3 JS 모듈 관계

```
app.js
  └─ switchTab('category')
       └─ CategoryDashboard.init()
            ├─ 서브탭='dessert' → DessertDashboard.init()  (기존 dessert.js)
            └─ 서브탭='beverage' → BeverageDashboard.init() (신규 beverage.js)
```

## 5. category.js 설계

```javascript
var CategoryDashboard = {
    _activeSubTab: 'dessert',  // 기본 서브탭
    _pendingCounts: { dessert: 0, beverage: 0 },

    async init() {
        await this.loadPendingCounts();
        this.renderSubTabs();
        this.switchSubTab(this._activeSubTab);
    },

    async loadPendingCounts() {
        var result = await api('/api/category-decision/pending-count' + storeParam());
        if (result && result.data) {
            this._pendingCounts = result.data;
        }
        this.updateMainBadge();
    },

    renderSubTabs() {
        var container = document.getElementById('categorySubTabs');
        if (!container) return;
        container.innerHTML =
            '<button class="cat-subtab active" data-sub="dessert">' +
              '🍰 디저트' + this._badge('dessert') +
            '</button>' +
            '<button class="cat-subtab" data-sub="beverage">' +
              '🥤 음료' + this._badge('beverage') +
            '</button>';

        var self = this;
        container.querySelectorAll('.cat-subtab').forEach(function(btn) {
            btn.addEventListener('click', function() {
                self.switchSubTab(this.dataset.sub);
            });
        });
    },

    switchSubTab(sub) {
        this._activeSubTab = sub;

        // 서브탭 활성 상태 변경
        document.querySelectorAll('.cat-subtab').forEach(function(btn) {
            btn.classList.toggle('active', btn.dataset.sub === sub);
        });

        // 콘텐츠 전환
        var dessertEl = document.getElementById('dessertContent');
        var beverageEl = document.getElementById('beverageContent');

        if (sub === 'dessert') {
            if (dessertEl) dessertEl.style.display = '';
            if (beverageEl) beverageEl.style.display = 'none';
            if (typeof DessertDashboard !== 'undefined') DessertDashboard.init();
        } else {
            if (dessertEl) dessertEl.style.display = 'none';
            if (beverageEl) beverageEl.style.display = '';
            if (typeof BeverageDashboard !== 'undefined') BeverageDashboard.init();
        }
    },

    updateMainBadge() {
        var total = (this._pendingCounts.dessert || 0) + (this._pendingCounts.beverage || 0);
        var badge = document.getElementById('categoryBadge');
        if (badge) {
            badge.textContent = total;
            badge.style.display = total > 0 ? 'inline' : 'none';
        }
    },

    _badge(type) {
        var n = this._pendingCounts[type] || 0;
        return n > 0 ? '<span class="cat-subtab-badge">' + n + '</span>' : '';
    }
};
```

## 6. beverage.js 설계

DessertDashboard 구조를 복제하되 음료 전용 필드 반영.

### 6.1 주요 차이점

| 항목 | DessertDashboard | BeverageDashboard |
|------|-----------------|-------------------|
| API 경로 | `/api/dessert-decision/` | `/api/beverage-decision/` |
| 컨테이너 | `dessertContent` | `beverageContent` |
| DOM ID 접두사 | `dessert` | `beverage` |
| 카테고리 라벨 | 냉장/상온단기/상온장기/젤리 | 유제품/냉장중기/상온장기/생수얼음 |
| 테이블 컬럼 | 폐기/판매 금액 | **매대효율** |
| 모달 | 판매/폐기/판매율/전주비 | 판매/폐기/판매율/**매대효율** |
| 태그 | 급락경고 | 급락경고 + **행사보호** + **비수기** |

### 6.2 카테고리 라벨 매핑

```javascript
var BEVERAGE_CATEGORY_LABELS = {
    'A': '유제품 (주간)',
    'B': '냉장중기 (격주)',
    'C': '상온장기 (월간)',
    'D': '생수/얼음 (월간)'
};

var BEVERAGE_CATEGORY_COLORS = {
    'A': '#ef4444',  // 적 (폐기 리스크 최대)
    'B': '#f59e0b',  // 황
    'C': '#3b82f6',  // 청
    'D': '#6b7280'   // 회
};
```

### 6.3 매대효율 컬럼 렌더링

```javascript
function renderShelfEfficiency(value) {
    // value: 0.0 ~ 2.0+
    var pct = Math.min(value * 100, 200);  // 표시용
    var color = value >= 1.0 ? 'var(--success)' :
                value >= 0.2 ? 'var(--warning)' : 'var(--danger)';

    return '<div class="cat-rate-bar">' +
        '<div class="cat-rate-fill" style="width:' + Math.min(pct, 100) + '%;background:' + color + '"></div>' +
        '</div>' +
        '<span class="cat-rate-text">' + value.toFixed(2) + '</span>';
}
```

## 7. index.html 변경

### 7.1 탭 버튼

```html
<!-- 변경: Dessert → Category -->
<a href="#" class="nav-tab" data-tab="category">
  Category
  <span class="cat-tab-badge" id="categoryBadge" style="display:none">0</span>
</a>
```

### 7.2 탭 콘텐츠

```html
<!-- 변경: tab-dessert → tab-category -->
<main id="tab-category" class="tab-content" style="padding:24px;">
    <!-- 서브탭 -->
    <div id="categorySubTabs" class="cat-subtab-bar"></div>

    <!-- 디저트 콘텐츠 (기존 dessertContent 재사용) -->
    <div id="dessertContent"></div>

    <!-- 음료 콘텐츠 (신규) -->
    <div id="beverageContent" style="display:none;"></div>
</main>
```

### 7.3 JS/CSS 로드

```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/dessert.css') }}?v=1">
<link rel="stylesheet" href="{{ url_for('static', filename='css/category.css') }}?v=1">
<script src="{{ url_for('static', filename='js/dessert.js') }}?v=2"></script>
<script src="{{ url_for('static', filename='js/beverage.js') }}?v=1"></script>
<script src="{{ url_for('static', filename='js/category.js') }}?v=1"></script>
```

## 8. app.js 변경

```javascript
// 기존: tab === 'dessert' → DessertDashboard.init()
// 변경:
if (tab === 'category') {
    if (typeof CategoryDashboard !== 'undefined') CategoryDashboard.init();
}

// 매장 변경 시 초기화
if (typeof CategoryDashboard !== 'undefined') {
    CategoryDashboard._activeSubTab = 'dessert';
    // dessert/beverage 각각 _loaded = false
    if (typeof DessertDashboard !== 'undefined') DessertDashboard._loaded = false;
    if (typeof BeverageDashboard !== 'undefined') BeverageDashboard._loaded = false;
}
```

## 9. category.css 설계

```css
/* 서브탭 바 */
.cat-subtab-bar {
    display: flex;
    gap: 8px;
    margin-bottom: 20px;
    border-bottom: 2px solid var(--border);
    padding-bottom: 0;
}

.cat-subtab {
    padding: 10px 20px;
    border: none;
    background: transparent;
    color: var(--text-muted);
    font-size: 0.95rem;
    font-weight: 500;
    cursor: pointer;
    border-bottom: 3px solid transparent;
    margin-bottom: -2px;
    transition: all 0.2s;
}

.cat-subtab.active {
    color: var(--primary);
    border-bottom-color: var(--primary);
}

.cat-subtab:hover:not(.active) {
    color: var(--text);
    background: var(--bg-hover);
}

/* 서브탭 뱃지 */
.cat-subtab-badge {
    display: inline-block;
    min-width: 18px;
    height: 18px;
    line-height: 18px;
    text-align: center;
    font-size: 0.7rem;
    font-weight: 600;
    background: var(--danger);
    color: white;
    border-radius: 9px;
    margin-left: 6px;
    padding: 0 5px;
}

/* Category 탭 뱃지 */
.cat-tab-badge {
    display: inline-block;
    min-width: 18px;
    height: 18px;
    line-height: 18px;
    text-align: center;
    font-size: 0.7rem;
    font-weight: 600;
    background: var(--danger);
    color: white;
    border-radius: 9px;
    margin-left: 6px;
    padding: 0 5px;
}
```

## 10. api_beverage_decision.py 설계

`api_dessert_decision.py`와 동일한 구조, 음료 전용 Repository 사용.

```python
beverage_decision_bp = Blueprint("beverage_decision", __name__)

# 6개 엔드포인트 (dessert API와 1:1 대응)
GET  /latest              → BeverageDecisionRepository.get_latest_decisions()
GET  /history/<item_cd>   → BeverageDecisionRepository.get_item_decision_history()
GET  /summary?history=8w  → BeverageDecisionRepository.get_decision_summary() + get_weekly_trend()
POST /action/<id>         → BeverageDecisionRepository.update_operator_action()
POST /action/batch        → BeverageDecisionRepository.batch_update_operator_action()
POST /run                 → BeverageDecisionFlow.run()
```

### 10.1 통합 pending-count 엔드포인트

```python
# web/routes/api_category_decision.py (신규, 경량)
category_decision_bp = Blueprint("category_decision", __name__)

@category_decision_bp.route("/pending-count", methods=["GET"])
def pending_count():
    store_id = _get_store_id()
    dessert_count = DessertDecisionRepository(store_id).get_pending_stop_count()
    beverage_count = BeverageDecisionRepository(store_id).get_pending_stop_count()
    return jsonify({
        "success": True,
        "data": {
            "dessert": dessert_count,
            "beverage": beverage_count,
            "total": dessert_count + beverage_count,
        }
    })
```

### 10.2 web/app.py Blueprint 등록

```python
from src.web.routes.api_beverage_decision import beverage_decision_bp
from src.web.routes.api_category_decision import category_decision_bp

app.register_blueprint(beverage_decision_bp, url_prefix="/api/beverage-decision")
app.register_blueprint(category_decision_bp, url_prefix="/api/category-decision")
```

## 11. 구현 순서

| 단계 | 작업 | 파일 |
|------|------|------|
| 1 | BeverageDecisionRepository 메서드 추가 | beverage_decision_repo.py |
| 2 | 음료 API Blueprint | api_beverage_decision.py |
| 3 | 통합 pending-count API | api_category_decision.py |
| 4 | Blueprint 등록 | web/app.py |
| 5 | category.js — 서브탭 컨트롤러 | static/js/category.js |
| 6 | beverage.js — 음료 대시보드 | static/js/beverage.js |
| 7 | category.css — 서브탭 스타일 | static/css/category.css |
| 8 | index.html — 탭 이름 + 서브탭 + 로드 | templates/index.html |
| 9 | app.js — 탭 전환 로직 변경 | static/js/app.js |
| 10 | 테스트 | tests/ |

## 12. 테스트

| 구분 | 수량 | 대상 |
|------|------|------|
| 음료 API | ~8개 | latest, history, summary, action, batch, run |
| pending-count API | ~2개 | 정상, 빈 데이터 |
| BeverageRepo 추가 메서드 | ~4개 | get_item_decision_history, update_operator_action |
| 기존 디저트 회귀 | - | 기존 테스트 전부 통과 확인 |

## 13. 향후 확장

| 항목 | 설명 |
|------|------|
| 간편식 탭 추가 | 서브탭에 `[🍱 간편식]` 추가, api_snack_decision.py + snack.js 생성 |
| 통합 집계 뷰 | 서브탭 사이에 "전체" 탭 → 디저트+음료 통합 요약 |
| DB 테이블 리네이밍 | `dessert_decisions` → `category_decisions` (SQLite 재생성) |
| 모바일 대응 | 서브탭 스크롤 처리, 테이블 카드 뷰 전환 |
