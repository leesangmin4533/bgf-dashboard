# Plan: 카테고리 3단계 드릴다운 대시보드

> **Feature**: category-drilldown
> **Created**: 2026-03-01
> **Status**: Draft

---

## 1. 목적

웹 대시보드에 카테고리 계층 구조(대분류 - 중분류 - 소분류 - 개별상품)를 3단계로 탐색할 수 있는 드릴다운 UI용 REST API를 추가한다. 각 레벨에서 매출/폐기/재고 요약 정보를 제공하여, 운영자가 카테고리별 현황을 빠르게 파악할 수 있도록 한다.

---

## 2. 현재 상태

- 기존 리포트 API (`/api/report/category/<mid_cd>`)는 중분류 단위 분석만 제공
- 대분류(18종) -> 중분류(72종) -> 소분류(198종) 계층 탐색 불가
- 카테고리 데이터는 이미 DB에 존재:
  - `mid_categories`: mid_cd, mid_nm, large_cd, large_nm (common.db, 72개)
  - `product_details`: large_cd, small_cd, small_nm, class_nm (common.db, 5,214개)
  - `daily_sales`: item_cd, sale_qty, disuse_qty (store DB)
  - `realtime_inventory`: item_cd, stock_qty (store DB)

---

## 3. 목표 상태

### API 엔드포인트 (3개)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/categories/tree` | 대분류 -> 중분류 -> 소분류 트리 구조 |
| GET | `/api/categories/<level>/<code>/summary` | 해당 카테고리 매출/폐기/재고 요약 |
| GET | `/api/categories/<level>/<code>/products` | 해당 카테고리 상품 목록 |

### 레벨 구분

| level | 설명 | code 예시 |
|-------|------|-----------|
| `large` | 대분류 (18종) | `01`, `02`, ... |
| `mid` | 중분류 (72종) | `001`, `049`, ... |
| `small` | 소분류 (198종) | `00101`, `04901`, ... |

---

## 4. 구현 범위

### 포함
- Blueprint: `api_category.py` (신규)
- 3개 REST API 엔드포인트
- common.db + store DB ATTACH 쿼리
- 테스트 (pytest)

### 제외
- 프론트엔드 UI (별도 작업)
- 캐시 레이어 (1차에서는 미구현, 필요시 추가)

---

## 5. 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| large_cd/small_cd NULL 데이터 | 일부 상품 분류 누락 | NULL 처리 + "미분류" 그룹화 |
| store DB ATTACH 실패 | 매출 데이터 조회 불가 | try/except + 빈 결과 반환 |
| 대용량 상품 목록 | 응답 지연 | 페이지네이션 지원 (limit/offset) |

---

## 6. 성공 지표

| 지표 | 목표 |
|------|------|
| API 응답 시간 | < 500ms |
| 테스트 통과율 | 100% |
| Match Rate (설계 대비) | >= 95% |
