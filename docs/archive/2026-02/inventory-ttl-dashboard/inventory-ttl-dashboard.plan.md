# Plan: 재고 수명 대시보드

> **Feature**: inventory-ttl-dashboard
> **Created**: 2026-02-25
> **Priority**: P1 (Phase 1 즉시 개선)
> **Status**: Draft

---

## 1. 배경

realtime_inventory TTL 시스템이 가동 중이나 (유통기한별 18h/36h/54h), 시각화 UI가 없어서:
- 유령 재고(stale)가 얼마나 있는지 모름
- inventory_batches의 만료 임박 배치 파악 불가
- 재고 신선도 현황을 한눈에 볼 수 없음

## 2. 목표

1. **스테일 재고 경고** — TTL 초과 유령 재고 카운트 + 상품 목록
2. **배치 만료 타임라인** — 향후 3일 만료 예정 배치 차트
3. **재고 신선도 분포** — TTL 대비 재고 나이 분포 히스토그램
4. **카테고리별 TTL 현황** — 푸드(001~005,012) 카테고리 드릴다운

## 3. 범위

- **IN**: 새 API 2개 + 프론트엔드 서브탭 + JS 차트
- **OUT**: 기존 TTL 로직 수정, DB 스키마 변경, 백엔드 비즈니스 로직

## 4. 의존성

- realtime_inventory 테이블 (store DB)
- inventory_batches 테이블 (store DB)
- product_details.expiration_days (common DB)
- FOOD_EXPIRY_FALLBACK (food.py)
- Chart.js 4 (CDN, 이미 로드됨)

## 5. 구현 계획

| # | 작업 | 파일 | 유형 |
|---|------|------|------|
| 1 | 재고 TTL API 엔드포인트 | `src/web/routes/api_inventory.py` | 신규 |
| 2 | Blueprint 등록 | `src/web/routes/__init__.py` | 수정 |
| 3 | 대시보드 서브탭 HTML | `src/web/templates/index.html` | 수정 |
| 4 | 차트 JS | `src/web/static/js/inventory.js` | 신규 |
| 5 | CSS 스타일 | `src/web/static/css/dashboard.css` | 수정 |
| 6 | 테스트 | `tests/test_inventory_ttl_dashboard.py` | 신규 |

## 6. 테스트 계획

- API 엔드포인트 테스트: 5개
- 데이터 집계 로직: 5개
- 합계: ~10개

## 7. 리스크

- realtime_inventory에 데이터가 없으면 빈 화면 → 빈 상태 안내 필요
- inventory_batches가 없는 환경 → graceful fallback
