# Plan: 폐기 원인 시각화 강화

> **Feature**: waste-cause-viz
> **Created**: 2026-02-25
> **Status**: Approved

---

## 1. 배경

- WasteCauseAnalyzer + API(`/api/waste/causes`, `/api/waste/summary`) 이미 구현 완료
- 백엔드 데이터는 풍부하나 대시보드에서 시각화가 부재
- 현재 홈탭에 "폐기 주의" 카드(숫자)와 상위 5개 인라인 리스트만 존재

## 2. 목표

1. **파이 차트**: 폐기 원인별(DEMAND_DROP, OVER_ORDER, EXPIRY_MISMANAGEMENT, MIXED) 비율 시각화
2. **워터폴 차트**: 상품별 발주 → 판매 → 폐기 경로 흐름 시각화
3. 기존 매출/분석 탭의 서브탭으로 "폐기 분석" 추가

## 3. 범위

### In Scope
- 매출/분석 탭에 "폐기 분석" 서브탭 추가 (HTML + JS + CSS)
- 파이 차트: `/api/waste/summary` 활용
- 워터폴 차트: `/api/waste/causes` 활용 (상품별 order_qty → actual_sold → waste_qty)
- 기간 선택기 (7일 / 14일 / 30일)
- 상품별 상세 테이블

### Out of Scope
- 백엔드 로직 변경 (WasteCauseAnalyzer 자체)
- 새로운 DB 테이블
- 모바일 네이티브 앱

## 4. 기술 스택

- Chart.js 4 (이미 CDN 로드됨)
- 기존 dashboard.css 디자인 토큰 활용
- 신규 JS 파일: `waste.js`

## 5. 예상 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| `src/web/templates/index.html` | 서브탭 버튼 + 폐기 분석 뷰 HTML |
| `src/web/static/js/waste.js` | 파이차트 + 워터폴차트 + 테이블 렌더링 (신규) |
| `src/web/static/css/dashboard.css` | 워터폴 차트용 스타일 추가 |
| `src/web/routes/api_waste.py` | 워터폴용 집계 엔드포인트 추가 |

## 6. 의존성

- Chart.js 4 (CDN, 이미 로드)
- `/api/waste/summary` (구현 완료)
- `/api/waste/causes` (구현 완료)
