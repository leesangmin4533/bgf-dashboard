# Design: 재고 수명 대시보드

> **Feature**: inventory-ttl-dashboard
> **Plan Reference**: `docs/01-plan/features/inventory-ttl-dashboard.plan.md`
> **Created**: 2026-02-25
> **Status**: Draft

---

## 1. 파일 구조

```
bgf_auto/
├── src/web/
│   ├── routes/
│   │   ├── __init__.py              # [수정] inventory_bp 등록
│   │   └── api_inventory.py         # [신규] 재고 TTL API
│   ├── templates/
│   │   └── index.html               # [수정] 재고 서브탭 추가
│   └── static/
│       ├── js/inventory.js          # [신규] 차트 렌더링
│       └── css/dashboard.css        # [수정] 재고 뱃지 스타일
└── tests/
    └── test_inventory_ttl_dashboard.py  # [신규] API 테스트
```

**신규 파일: 3개** (`api_inventory.py`, `inventory.js`, `test_inventory_ttl_dashboard.py`)
**수정 파일: 3개** (`__init__.py`, `index.html`, `dashboard.css`)

---

## 2. API 엔드포인트

### 2-1. `src/web/routes/api_inventory.py`

```python
inventory_bp = Blueprint("inventory", __name__)
```

| Method | Path | Auth | 역할 |
|--------|------|------|------|
| GET | `/api/inventory/ttl-summary` | 필요 | TTL 현황 요약 (카드 데이터) |
| GET | `/api/inventory/batch-expiry` | 필요 | 배치 만료 타임라인 |

### 2-2. `GET /api/inventory/ttl-summary` 응답

```json
{
  "store_id": "46513",
  "total_items": 850,
  "stale_items": 12,
  "stale_stock_qty": 45,
  "freshness_distribution": {
    "fresh": 780,
    "warning": 58,
    "stale": 12
  },
  "ttl_distribution": {
    "18h": 120,
    "36h": 600,
    "54h": 80,
    "default": 50
  },
  "category_breakdown": [
    {
      "mid_cd": "001",
      "mid_nm": "도시락",
      "total": 45,
      "stale": 3,
      "ttl_hours": 18,
      "expiry_days": 1
    }
  ],
  "stale_items_list": [
    {
      "item_cd": "8801234",
      "item_nm": "삼각김밥 참치마요",
      "stock_qty": 5,
      "queried_at": "2026-02-25T05:00:00",
      "hours_since_query": 26.5,
      "ttl_hours": 18,
      "mid_cd": "002"
    }
  ]
}
```

**로직:**
1. `inventory_repo.get_all()` — 전체 재고 조회
2. `product_details` JOIN — 유통기한/중분류 조회
3. `_get_stale_hours_for_expiry()` — 상품별 TTL 계산
4. `queried_at` 비교 — fresh/warning/stale 분류
   - `fresh`: TTL의 50% 미만 경과
   - `warning`: TTL의 50~100% 경과
   - `stale`: TTL 초과

### 2-3. `GET /api/inventory/batch-expiry` 응답

```json
{
  "store_id": "46513",
  "days_ahead": 3,
  "batches": [
    {
      "expiry_date": "2026-02-25",
      "label": "오늘",
      "items": [
        {
          "item_cd": "8801234",
          "item_nm": "삼각김밥 참치",
          "remaining_qty": 3,
          "mid_cd": "002",
          "receiving_date": "2026-02-24"
        }
      ],
      "total_qty": 15,
      "item_count": 5
    },
    {
      "expiry_date": "2026-02-26",
      "label": "내일",
      "items": [...],
      "total_qty": 22,
      "item_count": 8
    },
    {
      "expiry_date": "2026-02-27",
      "label": "모레",
      "items": [...],
      "total_qty": 18,
      "item_count": 6
    }
  ],
  "summary": {
    "total_expiring_qty": 55,
    "total_expiring_items": 19
  }
}
```

**로직:**
1. `inventory_batches WHERE status='active' AND expiry_date BETWEEN today AND today+3` 조회
2. 날짜별 그룹핑 + products JOIN (item_nm, mid_cd)
3. 테이블 없으면 빈 배열 반환 (graceful fallback)

---

## 3. 프론트엔드

### 3-1. 서브탭 위치

분석 탭(analytics-tab-selector) 내 "재고" 서브탭 버튼 추가:
```
일일 | 주간 | 카테고리 | 예측 정확도 | 폐기 분석 | [재고] ← 신규
```

### 3-2. `analytics-inventory` 뷰 구성

```
┌─────────────────────────────────────────────────────┐
│  [요약 카드 4개]                                       │
│  총 재고 상품 | 스테일 경고 | 오늘 만료 배치 | TTL 분포   │
├─────────────────────────────────────────────────────┤
│  [재고 신선도 도넛 차트]        [TTL 분포 바 차트]        │
├─────────────────────────────────────────────────────┤
│  [배치 만료 타임라인 — 수평 스택 바]  (3일)              │
├─────────────────────────────────────────────────────┤
│  [스테일 상품 테이블] (검색 + 정렬)                      │
└─────────────────────────────────────────────────────┘
```

### 3-3. 차트 상세

**1) 신선도 도넛 차트 (inventoryFreshnessChart)**
- 3구간: Fresh(초록), Warning(노랑), Stale(빨강)
- cutout: 60% (가운데에 총 상품 수)

**2) TTL 분포 바 차트 (inventoryTtlChart)**
- X축: "18h (1일)", "36h (2일)", "54h (3일)", "기본"
- Y축: 상품 수
- 수직 바 차트

**3) 배치 만료 타임라인 (inventoryBatchChart)**
- X축: 오늘, 내일, 모레
- Y축: 수량
- 스택 바: 카테고리별 색상 구분

**4) 스테일 상품 테이블**
- 컬럼: 상품명, 재고, 경과시간, TTL, 카테고리
- 경과시간 > TTL → 빨간 뱃지
- 검색 필터

### 3-4. `inventory.js` 함수 구조

```javascript
loadInventoryDashboard()          // 진입점
├─ fetchTtlSummary()             // GET /api/inventory/ttl-summary
│  ├─ renderInventorySummaryCards() // 4개 요약 카드
│  ├─ renderFreshnessChart()      // 도넛 차트
│  ├─ renderTtlDistChart()        // 바 차트
│  └─ renderStaleTable()          // 스테일 상품 테이블
└─ fetchBatchExpiry()             // GET /api/inventory/batch-expiry
   └─ renderBatchTimeline()       // 배치 만료 타임라인
```

---

## 4. Blueprint 등록

### 4-1. `src/web/routes/__init__.py` 수정

```python
from .api_inventory import inventory_bp

app.register_blueprint(inventory_bp, url_prefix="/api/inventory")
```

---

## 5. CSS 추가

```css
.inventory-status-badge { ... }
.inventory-status-badge.fresh { background: var(--success); }
.inventory-status-badge.warning { background: var(--warning); }
.inventory-status-badge.stale { background: var(--danger); }
```

---

## 6. 구현 순서

| # | 작업 | 파일 |
|---|------|------|
| 1 | API 엔드포인트 구현 | `src/web/routes/api_inventory.py` |
| 2 | Blueprint 등록 | `src/web/routes/__init__.py` |
| 3 | HTML 서브탭 + 뷰 추가 | `src/web/templates/index.html` |
| 4 | 차트 JS 구현 | `src/web/static/js/inventory.js` |
| 5 | CSS 스타일 추가 | `src/web/static/css/dashboard.css` |
| 6 | 테스트 작성 | `tests/test_inventory_ttl_dashboard.py` |

---

## 7. 테스트 계획

| # | 테스트 대상 | 방법 | 건수 |
|---|------------|------|------|
| 1 | ttl-summary 응답 형식 | Flask test client | 2 |
| 2 | ttl-summary 스테일 분류 | mock + unit | 3 |
| 3 | batch-expiry 응답 형식 | Flask test client | 2 |
| 4 | batch-expiry 빈 테이블 | Flask test client | 1 |
| 5 | TTL 계산 검증 | unit | 2 |
| **합계** | | | **10** |
