# 디저트 대시보드 API 연동 — 수정 명세서

**이슈**: dessert.js가 Mock 데이터를 사용 중 → 실제 API 호출로 교체
**수정 파일**: `static/js/dessert.js` (1개)

---

## 현재 상태

```
dessert.js 내부에 mockProducts = [...] 하드코딩
→ renderTable(mockProducts) 로 직접 렌더링
→ 실제 dessert_decisions 테이블 데이터가 화면에 안 나옴
```

---

## 수정 내용

### 1. Mock 데이터 삭제 → API 호출로 교체

**삭제 대상**: `const mockProducts = [...]` 전체 배열

**교체 로직**:

```javascript
// ========== 데이터 로드 ==========
async function loadDessertData() {
  try {
    const [latestRes, summaryRes] = await Promise.all([
      api('/api/dessert-decision/latest'),
      api('/api/dessert-decision/summary?history=8w')
    ]);

    // latest 응답 → 테이블 데이터
    dessertData = latestRes.data || latestRes;  // 응답 구조에 따라 조정

    // summary 응답 → 요약 카드 + 차트 데이터
    summaryData = summaryRes.data || summaryRes;

    renderAll();
  } catch (err) {
    console.error('[디저트 대시보드] 데이터 로드 실패:', err);
  }
}
```

### 2. 응답 데이터 → 화면 매핑

API 응답 필드명과 화면 렌더링에서 사용하는 필드명을 맞춰야 합니다.

**`GET /api/dessert-decision/latest` 응답 구조 (예상)**:

```json
[
  {
    "id": 1,
    "item_cd": "8801234003",
    "item_nm": "베어스)망곰밀크푸딩",
    "dessert_category": "A",
    "lifecycle_phase": "growth_decline",
    "weeks_since_intro": 6,
    "sale_rate": 0.35,
    "sale_trend_pct": -52.0,
    "disuse_amount": 8400,
    "sale_amount": 4200,
    "decision": "STOP_RECOMMEND",
    "decision_reason": "폐기>판매 즉시정지",
    "is_rapid_decline_warning": 1,
    "operator_action": null,
    "total_sale_qty": 12,
    "total_disuse_qty": 8
  }
]
```

**renderTable 내 필드 매핑 변경**:

```javascript
// 기존 (Mock 필드명)          →  변경 (API 필드명)
p.cat                         →  p.dessert_category
p.lifecycle                   →  p.lifecycle_phase
p.weeks                       →  p.weeks_since_intro
p.sale_rate                   →  p.sale_rate            (동일)
p.trend                       →  p.sale_trend_pct
p.disuse_amt                  →  p.disuse_amount
p.sale_amt                    →  p.sale_amount
p.decision                    →  p.decision             (동일)
p.reason                      →  p.decision_reason
p.rapid                       →  p.is_rapid_decline_warning  (1/0 → boolean 변환)
p.operator                    →  p.operator_action
```

**변환 헬퍼 (선택)**:

API 응답을 그대로 쓰거나, 변환 함수를 한번 거치는 방식 중 택1.

```javascript
// 방법 A: 렌더링 코드에서 직접 API 필드명 사용 (권장 — 변환 레이어 없음)
// renderTable 내에서 p.dessert_category, p.lifecycle_phase 등으로 직접 접근

// 방법 B: 변환 함수로 통일 (Mock 시절 필드명 유지하고 싶으면)
function mapApiToView(item) {
  return {
    item_cd: item.item_cd,
    item_nm: item.item_nm,
    cat: item.dessert_category,
    lifecycle: item.lifecycle_phase,
    weeks: item.weeks_since_intro,
    sale_rate: item.sale_rate,
    trend: item.sale_trend_pct,
    disuse_amt: item.disuse_amount,
    sale_amt: item.sale_amount,
    decision: item.decision,
    reason: item.decision_reason,
    rapid: item.is_rapid_decline_warning === 1,
    operator: item.operator_action,
    decision_id: item.id,
    total_sale_qty: item.total_sale_qty,
    total_disuse_qty: item.total_disuse_qty,
  };
}

// 로드 후 변환
dessertData = latestRes.map(mapApiToView);
```

### 3. 요약 카드 — API 데이터로 교체

**기존**: 카드 숫자가 HTML에 하드코딩 (98, 29, 7 등)

**변경**: `summaryData.current`에서 동적 렌더링

```javascript
function renderSummaryCards() {
  const c = summaryData.current;
  const total = c.KEEP + c.WATCH + c.STOP_RECOMMEND;
  const skip = c.SKIP || 0;
  const pending = dessertData.filter(p => p.decision === 'STOP_RECOMMEND' && !p.operator_action).length;

  document.getElementById('cardTotal').textContent = total;
  document.getElementById('cardTotalSub').textContent = `${total + skip}개 중 (${skip}개 SKIP)`;

  document.getElementById('cardKeep').textContent = c.KEEP;
  document.getElementById('cardKeepSub').textContent = `${(c.KEEP / total * 100).toFixed(1)}%`;

  document.getElementById('cardWatch').textContent = c.WATCH;
  // 급락경고 건수는 dessertData에서 카운트
  const rapidCount = dessertData.filter(p => p.is_rapid_decline_warning === 1).length;
  document.getElementById('cardWatchSub').textContent = `${(c.WATCH / total * 100).toFixed(1)}% · 급락경고 ${rapidCount}건`;

  document.getElementById('cardStop').textContent = c.STOP_RECOMMEND;
  document.getElementById('cardStopSub').textContent = `미확인 ${pending}건`;

  document.getElementById('cardSkip').textContent = skip;
}
```

### 4. 차트 — API 데이터로 교체

**카테고리별 분포 차트**:

```javascript
function renderCategoryChart() {
  const bc = summaryData.by_category;
  // bc = { A: {KEEP:68, WATCH:22, STOP:5}, B: {...}, ... }
  const cats = ['A', 'B', 'C', 'D'];
  const keepData = cats.map(c => bc[c]?.KEEP || 0);
  const watchData = cats.map(c => bc[c]?.WATCH || 0);
  const stopData = cats.map(c => bc[c]?.STOP_RECOMMEND || bc[c]?.STOP || 0);

  // Chart.js 데이터 업데이트 (기존 차트 destroy → recreate 또는 .update())
}
```

**주간 추이 차트**:

```javascript
function renderTrendChart() {
  const wt = summaryData.weekly_trend;
  // wt = [{week:'W4', KEEP:102, WATCH:18, STOP:2}, ...]
  const labels = wt.map(w => w.week);
  const keepData = wt.map(w => w.KEEP);
  const watchData = wt.map(w => w.WATCH);
  const stopData = wt.map(w => w.STOP || w.STOP_RECOMMEND);

  // Chart.js 데이터 업데이트
}
```

### 5. 개별 액션 — API 호출 추가

```javascript
// 기존 (Mock 직접 수정)
function confirmStop(itemCd, e) {
  e.stopPropagation();
  const p = mockProducts.find(x => x.item_cd === itemCd);
  if (p) { p.operator = 'CONFIRMED_STOP'; applyFilters(); updateAlert(); }
}

// 변경 (API 호출)
async function confirmStop(itemCd, e) {
  e.stopPropagation();
  const item = dessertData.find(x => x.item_cd === itemCd);
  if (!item) return;
  try {
    await api(`/api/dessert-decision/action/${item.decision_id}`, {
      method: 'POST',
      body: JSON.stringify({ action: 'CONFIRMED_STOP' })
    });
    await loadDessertData();  // 데이터 리로드 + 리렌더
  } catch (err) {
    console.error('[디저트] 정지확정 실패:', err);
  }
}

// overrideKeep도 동일 패턴
```

### 6. 일괄 액션 — API 호출 추가

```javascript
// 기존 (Mock 직접 수정)
function batchConfirmStop() {
  selectedItems.forEach(itemCd => {
    const p = mockProducts.find(x => x.item_cd === itemCd);
    if (p) p.operator = 'CONFIRMED_STOP';
  });
  // ...
}

// 변경 (API 호출)
async function batchConfirmStop() {
  if (selectedItems.size === 0) return;
  try {
    await api('/api/dessert-decision/action/batch', {
      method: 'POST',
      body: JSON.stringify({
        item_cds: Array.from(selectedItems),
        action: 'CONFIRMED_STOP'
      })
    });
    selectedItems.clear();
    await loadDessertData();  // 리로드
  } catch (err) {
    console.error('[디저트] 일괄 정지 실패:', err);
  }
}

// batchOverrideKeep도 동일 패턴
```

### 7. 모달 — API 호출 추가

```javascript
// 기존: Mock 데이터에서 찾아서 표시
// 변경: 이력 API 호출 추가

async function openModal(itemCd) {
  const item = dessertData.find(x => x.item_cd === itemCd);
  if (!item) return;

  // 기본 정보 렌더링 (dessertData에서)
  renderModalHeader(item);
  renderModalStats(item);

  // 이력 API 호출
  try {
    const history = await api(`/api/dessert-decision/history/${itemCd}`);
    renderModalHistory(history);
  } catch (err) {
    console.error('[디저트] 이력 로드 실패:', err);
  }

  // 주간 추세 차트 — 이력 데이터로 렌더링
  renderModalTrendChart(history);

  // 모달 오픈
  document.getElementById('modalOverlay').classList.add('open');
}
```

### 8. 초기화 시점

```javascript
// 기존
renderTable(mockProducts);
initCharts();

// 변경
loadDessertData();  // async — 데이터 로드 후 자동으로 renderAll() 호출
```

탭 전환 시에도 `loadDessertData()` 호출하여 최신 데이터 반영:

```javascript
// app.js의 탭 전환 핸들러에서
if (tabName === 'dessert') {
  loadDessertData();
}
```

---

## 수정 요약

| # | 위치 | 변경 |
|---|---|---|
| 1 | `const mockProducts = [...]` | 삭제 → `loadDessertData()` async 함수로 교체 |
| 2 | `renderTable()` 내 필드 참조 | Mock 필드명 → API 필드명 (또는 mapApiToView 변환) |
| 3 | 요약 카드 숫자 | 하드코딩 → `summaryData.current`에서 동적 |
| 4 | 차트 데이터 | 하드코딩 → `summaryData.by_category` + `weekly_trend` |
| 5 | `confirmStop()` / `overrideKeep()` | Mock 직접 수정 → `POST /action/{id}` API 호출 |
| 6 | `batchConfirmStop()` / `batchOverrideKeep()` | Mock 직접 수정 → `POST /action/batch` API 호출 |
| 7 | `openModal()` | Mock 참조 → `GET /history/{item_cd}` API 호출 |
| 8 | 초기화 | `renderTable(mockProducts)` → `loadDessertData()` |
| 9 | 모든 액션 후 | 수동 상태 변경 → `await loadDessertData()` 리로드 |

---

## 주의사항

**API 응답 필드명 확인 필수**: 위 매핑은 `dessert_decisions` 테이블 컬럼명 기준 예상입니다. 실제 API 응답이 snake_case인지 camelCase인지, 중첩 구조인지 확인 후 매핑을 조정하세요.

확인 방법:
```bash
curl -s http://localhost:5000/api/dessert-decision/latest | python -m json.tool | head -30
curl -s http://localhost:5000/api/dessert-decision/summary?history=8w | python -m json.tool
```
