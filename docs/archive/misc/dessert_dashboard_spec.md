# 디저트 대시보드 — 구현 명세서

**버전 1.0 | 2026-03-04**
**대상**: 기존 CU 자동발주 대시보드에 "Dessert" 탭 추가
**설계 원본**: `dessert_dashboard_prototype.html`

---

## Context

디저트 발주 판단 시스템(v1.2)의 결과를 운영자가 확인하고 조치할 수 있는 대시보드 탭이 필요합니다. 운영자는 월요일 밤 22시에 생성된 판단 결과를 화요일 아침 발주(07:00) 전까지 확인해야 합니다.

기존 대시보드: Vanilla JS + Jinja2, Chart.js 4, CSS Variables 디자인 시스템, DOM 기반 SPA 라우팅, Flask Blueprint API

---

## 1. 신규 파일

| 파일 | 설명 |
|---|---|
| `static/js/dessert.js` | 디저트 탭 전체 로직 (렌더링, 필터, 차트, 체크박스, 모달) |
| `static/css/dessert.css` | 디저트 탭 전용 스타일 (기존 CSS Variables 활용) |

## 2. 수정 파일

| 파일 | 변경 |
|---|---|
| `templates/index.html` | 탭 버튼 + 콘텐츠 div 추가, `dessert.js`/`dessert.css` 로드 |
| `static/js/app.js` | SPA 라우팅에 'dessert' 탭 등록 |
| `src/web/routes/api_dessert_decision.py` | 일괄처리 엔드포인트 추가 |
| `src/infrastructure/database/repos/dessert_decision_repo.py` | 일괄 업데이트 메서드 추가 |

---

## 3. UI 구조

### 3.1 페이지 레이아웃 (위→아래)

```
┌─────────────────────────────────────────────────┐
│ ⚠ 미확인 알림 배너 (STOP_RECOMMEND 미처리 N건)       │
├─────────────────────────────────────────────────┤
│ [전체 134] [KEEP 98] [WATCH 29] [STOP 7] [SKIP 40]  │  ← 요약 카드 5개 (클릭=필터)
├──────────────────────┬──────────────────────────┤
│ 카테고리별 판단 분포   │ 주간 판단 추이             │  ← Chart.js 차트 2개
│ (스택바)              │ (라인)                    │
├──────────────────────┴──────────────────────────┤
│ [전체] [A 냉장] [B 상온단기] [C 상온장기] [D 젤리]  🔍  │  ← 필터 바
├─────────────────────────────────────────────────┤
│ ☐ │상품│카테고리│생애주기│판매율│추세│폐기/판매│판단│사유│확인 │  ← 상품 테이블
│ ☐ │... │  A    │ 정착기 │ 92% │ ▲12│ 1.2/18 │KEEP│ ...│ — │
│ ☑ │... │  A    │ 성장기 │ 35% │ ▼52│⚠8.4/4.2│STOP│ ...│정지│유지│
├─────────────────────────────────────────────────┤
│        ┌─────────────────────────────────┐       │
│        │ 선택 3건 │🛑일괄정지│✅일괄유지│해제│ │  ← 플로팅 액션 바
│        └─────────────────────────────────┘       │
└─────────────────────────────────────────────────┘

[상품 행 클릭 → 모달]
┌─────────────────────────────┐
│ 베어스)망곰밀크푸딩            │
│ 카테고리 A │유통기한 3일│24주  │
│ ┌───┬───┬───┬───┐          │
│ │판매│폐기│판매율│전주비│       │  ← 성과 4칸
│ │ 23│  2│ 92%│+12%│       │
│ └───┴───┴───┴───┘          │
│ [주간 판매/폐기 바차트 8주]     │  ← Chart.js
│ 판단 이력                     │
│ 03-03 KEEP 판매율 92%         │
│ 02-24 KEEP 판매율 87%         │
│ 02-17 WATCH 판매율 48%        │
│ [🛑 정지확정] [✅ 유지(재정)]    │  ← STOP_RECOMMEND만 표시
└─────────────────────────────┘
```

### 3.2 요약 카드 (Summary Cards)

| 카드 | 데이터 소스 | 색상 |
|---|---|---|
| 전체 판단 | 총 판단 건수 (SKIP 제외) / 전체 상품 수 | `--primary` |
| KEEP | decision='KEEP' 건수 + 비율 | `--success` |
| WATCH | decision='WATCH' 건수 + 급락경고 건수 | `--warning` |
| STOP | decision='STOP_RECOMMEND' 건수 + 미확인 건수 | `--danger` |
| SKIP | 판단 보류 건수 (source='none') | `--text-muted` |

카드 클릭 시 해당 decision으로 테이블 필터링. 활성 카드는 `--primary-soft` 배경 + `--primary` 테두리.

### 3.3 차트 (Chart.js 4)

**카테고리별 판단 분포** (Stacked Bar)
- X축: A, B, C, D 카테고리
- Y축: 상품 수
- 스택: KEEP(녹), WATCH(황), STOP(적)
- 데이터: `GET /api/dessert-decision/summary`

**주간 판단 추이** (Line, fill)
- X축: 최근 8주 (W4~W11)
- Y축: 건수
- 3개 라인: KEEP, WATCH, STOP
- 데이터: `GET /api/dessert-decision/summary?history=8w`

차트 인스턴스는 `_charts` 전역 객체에 캐시 (기존 패턴).

### 3.4 필터 바

**카테고리 필터**: 전체 / A 냉장 / B 상온단기 / C 상온장기 / D 젤리/푸딩 — 토글 버튼
**상품 검색**: 상품명 실시간 필터 (keyup 디바운스 300ms)
**이중 필터**: 요약 카드(decision) × 카테고리 필터 조합 가능

### 3.5 상품 테이블

| 컬럼 | 내용 | 비고 |
|---|---|---|
| ☐ | 체크박스 | STOP_RECOMMEND + 미처리만 표시 |
| 상품 | 상품명 + 상품코드 + NEW 태그 | 상품명 말줄임 180px |
| 카테고리 | A/B/C/D 뱃지 | 왼쪽 색상 보더 |
| 생애주기 | 신상품/성장하락/정착기 + N주 | |
| 판매율 | 프로그레스 바 + 퍼센트 | 50%↑ 녹, 30~50% 황, 30%↓ 적 |
| 주간추세 | ▲/▼ + 퍼센트 | 상승=녹, 하락=적 |
| 폐기/판매 | 금액(k) | 폐기>판매 시 ⚠ 빨간 강조 |
| 판단 | KEEP/WATCH/STOP 뱃지 + 급락경고 태그 | 급락=⚡ 깜빡임 |
| 사유 | 판단 사유 텍스트 | 말줄임 140px |
| 운영자 확인 | 정지확정/유지 버튼 또는 처리 결과 | 아래 상세 |

**운영자 확인 컬럼 상태별 표시:**

| 상태 | 표시 |
|---|---|
| STOP_RECOMMEND + 미처리 | `[정지확정]` `[유지]` 버튼 |
| CONFIRMED_STOP | "🛑 정지됨" 텍스트 |
| OVERRIDE_KEEP | "✅ 유지(재정)" 텍스트 |
| KEEP/WATCH/SKIP | "—" |

### 3.6 체크박스 + 일괄처리

**체크박스 규칙:**
- STOP_RECOMMEND 상태이면서 operator_action이 NULL인 상품에만 체크박스 표시
- KEEP, WATCH, SKIP, 이미 처리된 상품에는 체크박스 없음 (빈 셀)
- 헤더 전체선택 체크박스: 현재 필터에 보이는 미처리 STOP 상품만 대상
- 일부만 선택 시 헤더 체크박스 indeterminate 상태 (─)
- 선택된 행은 `--primary-soft` 배경 하이라이트

**플로팅 액션 바:**
- 1건 이상 선택 시 화면 하단에서 슬라이드업 (bottom: -80px → 24px, cubic-bezier 애니메이션)
- 구성: "선택 N건" 카운터 + `[🛑 일괄 정지확정]` + `[✅ 일괄 유지(재정)]` + `[선택 해제]`
- 0건이면 자동 숨김
- z-index: 150 (모달 200보다 아래)

**일괄처리 API 호출:**

```javascript
// 일괄 정지확정
async function batchConfirmStop() {
  const itemCds = Array.from(selectedItems);
  await api('/api/dessert-decision/action/batch', {
    method: 'POST',
    body: JSON.stringify({
      item_cds: itemCds,
      action: 'CONFIRMED_STOP'
    })
  });
  // 성공 시 테이블 리렌더 + 알림 배너 업데이트
}
```

### 3.7 상품 상세 모달

행 클릭 시 모달 오픈. 체크박스 클릭은 `event.stopPropagation()`으로 모달 방지.

**모달 구성:**
- 상품명 + 메타정보 (카테고리, 유통기한, 첫 판매일, 경과 주수)
- 성과 카드 4개 (판매수량, 폐기수량, 판매율, 전주 대비)
- 주간 판매/폐기 바차트 8주 (Chart.js, 매번 destroy → recreate)
- 판단 이력 리스트 (최근 N건, 날짜 + 뱃지 + 사유)
- 액션 버튼 (STOP_RECOMMEND 미처리일 때만 표시)

**모달 데이터:**
- `GET /api/dessert-decision/history/{item_cd}` → 이력
- `GET /api/dessert-decision/latest?item_cd={item_cd}` → 최신 성과

### 3.8 미확인 알림 배너

```
⚠ 확인 대기 중인 정지 권고 상품이 있습니다                [7건]
```

- STOP_RECOMMEND + operator_action=NULL 건수
- 0건이면 배너 숨김
- 개별/일괄 처리할 때마다 실시간 업데이트
- `pulse-border` 애니메이션으로 주의 유도

---

## 4. API 엔드포인트

### 4.1 기존 엔드포인트 (이미 구현됨)

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/dessert-decision/latest` | 최신 판단 결과 (decision 필터 가능) |
| GET | `/api/dessert-decision/history/{item_cd}` | 상품별 판단 이력 |
| GET | `/api/dessert-decision/summary` | 카테고리별 집계 |
| POST | `/api/dessert-decision/action/{decision_id}` | 개별 운영자 확인 |
| POST | `/api/dessert-decision/run` | 수동 실행 |

### 4.2 신규 엔드포인트

**`POST /api/dessert-decision/action/batch`** — 일괄 운영자 확인

```python
# Request
{
  "item_cds": ["8801234003", "8801234005", "8801234006"],
  "action": "CONFIRMED_STOP"  # 또는 "OVERRIDE_KEEP"
}

# Response (200)
{
  "success": true,
  "updated_count": 3,
  "items": [
    {"item_cd": "8801234003", "action": "CONFIRMED_STOP", "action_taken_at": "2026-03-04T06:30:00"},
    {"item_cd": "8801234005", "action": "CONFIRMED_STOP", "action_taken_at": "2026-03-04T06:30:00"},
    {"item_cd": "8801234006", "action": "CONFIRMED_STOP", "action_taken_at": "2026-03-04T06:30:00"}
  ]
}

# Error (400)
{ "success": false, "error": "action must be CONFIRMED_STOP or OVERRIDE_KEEP" }
```

**권한**: admin 역할만 실행 가능 (기존 `@require_admin` 데코레이터)

**`GET /api/dessert-decision/summary?history=8w`** — 주간 추이 데이터 추가

기존 summary에 `history` 파라미터 추가 시 주차별 KEEP/WATCH/STOP 건수 반환:

```python
# Response (history=8w)
{
  "current": { "KEEP": 98, "WATCH": 29, "STOP_RECOMMEND": 7, "SKIP": 40 },
  "by_category": { "A": {"KEEP": 68, "WATCH": 22, "STOP": 5}, ... },
  "weekly_trend": [
    {"week": "W4", "KEEP": 102, "WATCH": 18, "STOP": 2},
    {"week": "W5", "KEEP": 100, "WATCH": 22, "STOP": 3},
    ...
  ]
}
```

---

## 5. 백엔드 수정

### 5.1 api_dessert_decision.py — batch 엔드포인트 추가

```python
@bp.route('/action/batch', methods=['POST'])
@require_admin
def batch_action():
    data = request.get_json()
    item_cds = data.get('item_cds', [])
    action = data.get('action')
    
    if action not in ('CONFIRMED_STOP', 'OVERRIDE_KEEP'):
        return jsonify(success=False, error='invalid action'), 400
    if not item_cds or len(item_cds) > 50:
        return jsonify(success=False, error='item_cds: 1~50개'), 400
    
    results = repo.batch_update_operator_action(
        store_id=g.store_id,
        item_cds=item_cds,
        action=action,
        operator_note=data.get('note', 'batch')
    )
    return jsonify(success=True, updated_count=len(results), items=results)
```

### 5.2 dessert_decision_repo.py — batch 메서드 추가

```python
def batch_update_operator_action(self, store_id, item_cds, action, operator_note=None):
    """여러 상품의 operator_action을 일괄 업데이트.
    
    대상: 해당 store_id의 최신 판단 레코드 중 decision='STOP_RECOMMEND'이고
          operator_action이 NULL인 것만.
    """
    now = datetime.now().isoformat()
    placeholders = ','.join('?' * len(item_cds))
    sql = f"""
        UPDATE dessert_decisions 
        SET operator_action = ?, operator_note = ?, action_taken_at = ?
        WHERE store_id = ? 
          AND item_cd IN ({placeholders})
          AND decision = 'STOP_RECOMMEND'
          AND operator_action IS NULL
          AND id IN (
            SELECT MAX(id) FROM dessert_decisions
            WHERE store_id = ? GROUP BY item_cd
          )
    """
    params = [action, operator_note, now, store_id] + item_cds + [store_id]
    # execute + return affected rows
```

### 5.3 summary 엔드포인트 — history 파라미터 추가

기존 `GET /api/dessert-decision/summary`에 `history` 쿼리 파라미터 지원:

```python
@bp.route('/summary')
def summary():
    # 기존 current + by_category 로직 유지
    result = { "current": ..., "by_category": ... }
    
    history = request.args.get('history')
    if history:  # e.g., '8w'
        weeks = int(history.replace('w', ''))
        result["weekly_trend"] = repo.get_weekly_trend(store_id, weeks)
    
    return jsonify(result)
```

---

## 6. 프론트엔드 구현 상세

### 6.1 index.html 변경

```html
<!-- 탭 버튼 추가 (기존 탭 뒤에) -->
<button class="tab-btn" data-tab="dessert">
  <span class="tab-icon">🍰</span>
  <span>Dessert</span>
  <span class="tab-badge" id="dessertBadge" style="display:none">0</span>
</button>

<!-- 콘텐츠 div 추가 -->
<div class="tab-content" id="tab-dessert" style="display:none">
  <!-- dessert.js가 여기에 렌더링 -->
</div>

<!-- JS/CSS 로드 -->
<link rel="stylesheet" href="/static/css/dessert.css">
<script src="/static/js/dessert.js"></script>
```

탭 버튼에 미확인 건수 뱃지 표시 (다른 탭에 있을 때도 보임).

### 6.2 app.js 변경

```javascript
// 기존 탭 목록에 추가
const TABS = ['home', 'analytics', 'order', 'food-monitor', 'dessert', 'settings'];

// 탭 전환 시 dessert 초기화
function switchTab(tabName) {
  // 기존 로직 ...
  if (tabName === 'dessert') {
    DessertDashboard.init();
  }
}
```

### 6.3 dessert.js 구조

```javascript
const DessertDashboard = {
  _charts: {},       // Chart.js 인스턴스 캐시
  _data: [],         // 현재 상품 데이터
  _selected: new Set(),  // 체크박스 선택 상태
  _filter: { decision: 'all', category: 'all', search: '' },

  async init() {
    await this.loadData();
    this.renderAlertBanner();
    this.renderSummaryCards();
    this.renderCharts();
    this.renderFilters();
    this.renderTable();
    this.updateTabBadge();
  },

  async loadData() {
    const [latest, summary] = await Promise.all([
      api('/api/dessert-decision/latest'),
      api('/api/dessert-decision/summary?history=8w')
    ]);
    this._data = latest;
    this._summary = summary;
  },

  // 렌더링 메서드들...
  renderAlertBanner() { /* 미확인 STOP 건수 */ },
  renderSummaryCards() { /* 5개 카드 */ },
  renderCharts() { /* 2개 Chart.js, _charts에 캐시 */ },
  renderFilters() { /* 카테고리 버튼 + 검색 */ },
  renderTable() { /* 체크박스 포함 테이블 */ },
  renderModal(itemCd) { /* 모달 오픈 + 이력 API 호출 */ },

  // 체크박스 관련
  toggleSelectAll(checked) { /* 전체선택 */ },
  toggleSelectItem(itemCd, checked) { /* 개별선택 */ },
  updateBatchBar() { /* 플로팅 바 표시/숨김 */ },

  // 일괄처리
  async batchAction(action) {
    const itemCds = Array.from(this._selected);
    await api('/api/dessert-decision/action/batch', {
      method: 'POST',
      body: JSON.stringify({ item_cds: itemCds, action })
    });
    this._selected.clear();
    await this.loadData();
    this.renderAll();
  },

  // 개별처리
  async singleAction(decisionId, action) {
    await api(`/api/dessert-decision/action/${decisionId}`, {
      method: 'POST',
      body: JSON.stringify({ action })
    });
    await this.loadData();
    this.renderAll();
  },

  renderAll() {
    this.renderAlertBanner();
    this.renderSummaryCards();
    this.renderTable();
    this.updateBatchBar();
    this.updateTabBadge();
  },

  updateTabBadge() {
    const pending = this._data.filter(p => 
      p.decision === 'STOP_RECOMMEND' && !p.operator_action
    ).length;
    const badge = document.getElementById('dessertBadge');
    badge.textContent = pending;
    badge.style.display = pending > 0 ? 'inline' : 'none';
  }
};
```

### 6.4 dessert.css

기존 CSS Variables 활용. 추가로 필요한 스타일만 정의:

```css
/* 디저트 탭 전용 — 기존 디자인 시스템 위에 추가 */

/* 알림 배너 */
.dessert-alert { /* ... */ }

/* 요약 카드 */
.dessert-summary-grid { /* 5열 그리드 */ }

/* 테이블 체크박스 */
.dessert-cb-cell { /* ... */ }
.dessert-row-selected { background: var(--primary-soft) !important; }

/* 플로팅 액션 바 */
.dessert-batch-bar { /* 하단 고정, 슬라이드 애니메이션 */ }

/* 모달 */
.dessert-modal { /* ... */ }

/* 판매율 바 */
.dessert-rate-bar { /* ... */ }

/* 급락 경고 태그 */
.dessert-rapid-decline { animation: dessert-blink 1.5s ease-in-out infinite; }
```

모든 클래스에 `dessert-` 접두사 사용하여 기존 스타일과 충돌 방지.

---

## 7. 테스트

| 구분 | 수량 | 대상 |
|---|---|---|
| API 테스트 | ~3개 | batch 엔드포인트 (정상, 빈 목록, 권한 없음) |
| Repository 테스트 | ~3개 | batch_update_operator_action (정상, 이미 처리된 건 무시, 50개 제한) |
| summary history 테스트 | ~2개 | weekly_trend 데이터 형태, 빈 데이터 |
| 전체 회귀 | - | 기존 테스트 + 디저트 테스트 전부 통과 |

---

## 8. 향후 확장 고려사항

| 항목 | 설명 |
|---|---|
| 탭 구조 확장 | 향후 간편식/음료 등 추가 시 "Dessert" 탭을 "Category" 탭으로 변경, 하위 서브탭으로 카테고리 선택 |
| 미확인 리마인더 | 운영자가 확인 안 하고 발주 시간이 다가오면 강조 알림 (06:00 이후 배너 색상 변경 등) |
| 판단 기준값 조정 UI | 카테고리별 판매율 기준, 연속 미달 주수 등을 대시보드에서 직접 변경하는 설정 패널 |
| 모바일 대응 | 현재 반응형 기본 지원 (그리드 축소), 추후 모바일 전용 레이아웃 검토 |
