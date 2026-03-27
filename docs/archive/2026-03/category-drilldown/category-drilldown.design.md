# Design: 카테고리 3단계 드릴다운 API

> **Feature**: category-drilldown
> **Created**: 2026-03-01
> **Plan**: `docs/01-plan/features/category-drilldown.plan.md`

---

## 1. 아키텍처

```
Presentation (Flask Blueprint)
  └─ api_category.py  ←── 신규
       ├─ GET /api/categories/tree
       ├─ GET /api/categories/<level>/<code>/summary
       └─ GET /api/categories/<level>/<code>/products

Data Sources (common.db + store DB)
  ├─ mid_categories (large_cd, large_nm, mid_cd, mid_nm)
  ├─ product_details (item_cd, large_cd, small_cd, small_nm, class_nm)
  ├─ products (item_cd, item_nm, mid_cd)
  ├─ daily_sales (item_cd, sale_qty, disuse_qty)        [store DB]
  └─ realtime_inventory (item_cd, stock_qty, pending_qty) [store DB]
```

---

## 2. API 상세 설계

### 2-1. GET /api/categories/tree

카테고리 전체 트리 구조를 반환한다.

**쿼리 파라미터**:
- `store_id` (선택): 매장 코드 (기본: DEFAULT_STORE_ID)

**응답**:
```json
{
  "tree": [
    {
      "large_cd": "01",
      "large_nm": "식품",
      "mid_count": 5,
      "children": [
        {
          "mid_cd": "001",
          "mid_nm": "도시락",
          "small_count": 3,
          "children": [
            {
              "small_cd": "00101",
              "small_nm": "일반도시락",
              "product_count": 15
            }
          ]
        }
      ]
    }
  ],
  "summary": {
    "large_count": 18,
    "mid_count": 72,
    "small_count": 198,
    "product_count": 5214
  }
}
```

**쿼리 로직**:
1. common.db에서 mid_categories + product_details JOIN
2. large_cd 기준 그룹핑 -> mid_cd 그룹핑 -> small_cd 그룹핑
3. NULL large_cd는 "기타(00)" 으로 처리

### 2-2. GET /api/categories/<level>/<code>/summary

특정 카테고리의 매출/폐기/재고 요약을 반환한다.

**경로 파라미터**:
- `level`: `large` | `mid` | `small`
- `code`: 카테고리 코드 (예: `01`, `001`, `00101`)

**쿼리 파라미터**:
- `store_id` (선택): 매장 코드 (기본: DEFAULT_STORE_ID)
- `days` (선택): 조회 기간 일수 (기본: 7)

**응답**:
```json
{
  "level": "mid",
  "code": "001",
  "name": "도시락",
  "period_days": 7,
  "sales": {
    "total_qty": 150,
    "total_amount": 750000,
    "daily_avg": 21.4,
    "item_count": 12
  },
  "waste": {
    "total_qty": 8,
    "waste_rate": 5.3,
    "daily_avg": 1.1
  },
  "inventory": {
    "total_stock": 45,
    "total_pending": 10,
    "item_count": 12
  }
}
```

**쿼리 로직**:
1. level에 따라 상품 목록 필터:
   - `large`: products.mid_cd IN (SELECT mid_cd FROM mid_categories WHERE large_cd = ?)
   - `mid`: products.mid_cd = ?
   - `small`: product_details.small_cd = ?
2. store DB ATTACH 후 daily_sales JOIN (최근 N일)
3. realtime_inventory JOIN (현재 재고)

### 2-3. GET /api/categories/<level>/<code>/products

특정 카테고리의 상품 목록을 반환한다.

**경로 파라미터**:
- `level`: `large` | `mid` | `small`
- `code`: 카테고리 코드

**쿼리 파라미터**:
- `store_id` (선택): 매장 코드 (기본: DEFAULT_STORE_ID)
- `days` (선택): 매출 집계 기간 (기본: 7)
- `limit` (선택): 최대 항목 수 (기본: 100)
- `offset` (선택): 시작 위치 (기본: 0)
- `sort` (선택): 정렬 기준 (`sale_qty` | `disuse_qty` | `stock_qty` | `item_nm`, 기본: `sale_qty`)
- `order` (선택): 정렬 방향 (`desc` | `asc`, 기본: `desc`)

**응답**:
```json
{
  "level": "mid",
  "code": "001",
  "name": "도시락",
  "total_count": 42,
  "products": [
    {
      "item_cd": "8801234567890",
      "item_nm": "참치마요 도시락",
      "mid_cd": "001",
      "small_cd": "00101",
      "small_nm": "일반도시락",
      "sale_qty": 25,
      "sale_amount": 125000,
      "disuse_qty": 2,
      "stock_qty": 5,
      "pending_qty": 3
    }
  ],
  "pagination": {
    "limit": 100,
    "offset": 0,
    "total": 42,
    "has_more": false
  }
}
```

---

## 3. Blueprint 구조

```python
# src/web/routes/api_category.py

category_bp = Blueprint("category", __name__)

# 헬퍼 함수
_get_store_db_path(store_id)  -> Path
_get_common_db_path()         -> Path
_get_category_name(conn, level, code) -> str
_build_item_filter(level, code)       -> (sql_where, params)

# 엔드포인트
@category_bp.route("/tree")                         -> tree()
@category_bp.route("/<level>/<code>/summary")       -> summary(level, code)
@category_bp.route("/<level>/<code>/products")      -> products(level, code)
```

---

## 4. Blueprint 등록

```python
# src/web/routes/__init__.py 수정
from .api_category import category_bp
app.register_blueprint(category_bp, url_prefix="/api/categories")
```

---

## 5. 테스트 설계

| # | 카테고리 | 테스트 내용 | 검증 |
|---|---------|-----------|------|
| 1 | tree | 빈 DB에서 빈 트리 반환 | tree=[], summary.large_count=0 |
| 2 | tree | 트리 응답 구조 검증 | large_cd, children, mid_cd, small_cd 존재 |
| 3 | tree | 집계 수 정확성 | mid_count, small_count 일치 |
| 4 | summary | large 레벨 요약 | sales, waste, inventory 필드 존재 |
| 5 | summary | mid 레벨 요약 | daily_avg 계산 정확 |
| 6 | summary | small 레벨 요약 | item_count 정확 |
| 7 | summary | 잘못된 level 400 반환 | error 메시지 |
| 8 | summary | 존재하지 않는 코드 | 빈 결과(0) |
| 9 | products | 기본 상품 목록 | products 배열 + pagination |
| 10 | products | 페이지네이션 (limit/offset) | has_more, total_count |
| 11 | products | 정렬 (sort/order) | 정렬 순서 검증 |
| 12 | products | 잘못된 level 400 반환 | error 메시지 |

---

## 6. 에러 처리

| 상황 | HTTP 코드 | 응답 |
|------|----------|------|
| 잘못된 level 값 | 400 | `{"error": "유효하지 않은 level입니다. large/mid/small 중 선택하세요"}` |
| DB 접속 실패 | 500 | `{"error": "카테고리 데이터 조회에 실패했습니다"}` |
| store DB 미존재 | 200 | 매출/재고 = 0 (common 데이터만 반환) |

---

## 7. 구현 순서

1. `api_category.py` 생성 (Blueprint + 3개 엔드포인트)
2. `routes/__init__.py`에 Blueprint 등록
3. `tests/test_api_category.py` 작성 (12개 테스트)
4. 테스트 실행 및 검증
