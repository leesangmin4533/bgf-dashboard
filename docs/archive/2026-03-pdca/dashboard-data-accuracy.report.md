# PDCA Report: dashboard-data-accuracy

## Overview
- **Feature**: 대시보드 데이터 정확성 검증
- **Date**: 2026-03-03
- **Match Rate**: 100% (수정 후)
- **Phase**: Completed

## PDCA Cycle Summary

### Plan
- 13개 API 모듈, ~50개 엔드포인트의 데이터 정확성 종합 검증
- 4단계 검증 레벨 정의: API 내부 일관성 → API-DB 정합성 → API-Frontend 매칭 → 렌더링 정확성

### Design
- 모듈별 검증 체크리스트 작성
- 필드 매칭 매트릭스 설계
- 에러 폴백 응답 구조 검증 기준 정의

### Do (검증 + 수정)
4건의 버그 발견 및 수정 완료:

| # | 심각도 | 파일 | 내용 | 영향 |
|---|--------|------|------|------|
| 1 | CRITICAL | order.js:626-628 | summary 필드명 3개 불일치 | 발주 결과 모달 수치 항상 0 |
| 2 | CRITICAL | order.js:637-638 | item 필드명 2개 불일치 | 발주 테이블 상품명/카테고리 빈칸 |
| 3 | MINOR | api_order.py:159 | categories() 중복 store_id 필터 | 불필요한 SQL 조건 (기능 정상) |
| 4 | MINOR | api_prediction.py:200-204 | fallback 응답 필드 4개 누락 | 예외 시 일부 필드 undefined |

### Check (Gap Analysis)
- 13개 모듈 전수 검사 완료
- 수정 후 전체 API-Frontend 필드 매칭 100%
- 에러 응답 구조 일관성 확인
- Chart.js 데이터셋 호환성 확인

## Modified Files

| File | Changes |
|------|---------|
| `src/web/static/js/order.js` | showOrderResult() 필드명 5개 수정 |
| `src/web/routes/api_order.py` | categories() 중복 store_id 필터 제거 |
| `src/web/routes/api_prediction.py` | _get_qty_accuracy() fallback 필드 4개 추가 |

## Key Findings
1. `runPredict()`와 `showOrderResult()`가 다른 시점에 작성되어 필드명 불일치 발생
2. per-store DB 아키텍처에서 `store_id = ?` 필터는 중복이나 기능적 영향 없음
3. 나머지 10개 API 모듈은 전부 정확한 필드 매칭 확인
4. 모든 API의 에러 응답이 `{"error": "..."}` 패턴으로 일관됨

## Metrics
- **검증 모듈**: 13/13 (100%)
- **검증 엔드포인트**: ~50/50 (100%)
- **발견 버그**: 4건 (CRITICAL 2, MINOR 2)
- **수정 버그**: 4/4 (100%)
- **잔여 불일치**: 0건
