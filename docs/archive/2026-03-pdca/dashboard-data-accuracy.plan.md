# Plan: dashboard-data-accuracy

## Feature Overview
- **Feature Name**: 대시보드 데이터 정확성 검증
- **Created**: 2026-03-03
- **Phase**: Plan
- **Priority**: High

## Problem Statement
대시보드(Flask 웹 UI)에 표시되는 데이터가 실제 DB 데이터 및 비즈니스 로직과 정확히 매치되는지 종합 검증이 필요하다. API 응답 필드와 프론트엔드 렌더링 간 불일치, DB 쿼리 오류, 데이터 변환 누락 등의 잠재적 문제를 사전에 발견한다.

## Scope

### 검증 대상 (13개 API 모듈, ~50개 엔드포인트)

| # | 모듈 | 엔드포인트 수 | 핵심 검증 항목 |
|---|------|------------|--------------|
| 1 | api_home | 5 | 스케줄러 상태, 파이프라인 타임스탬프, 7일 트렌드, 폐기위험 목록 |
| 2 | api_order | 10 | 예측 결과 summary vs items 합계, 카테고리 필터링, 제외 설정 동기화 |
| 3 | api_report | 5 | 일일/주간 리포트 수치 일관성, 카테고리 분석 정확도 |
| 4 | api_prediction | 3 | 적중률 계산, ML 상태, 모델 타입 분포 합계 |
| 5 | api_waste | 6 | 폐기 원인 분류 정확성, 워터폴 데이터 정합성, 보정 이력 |
| 6 | api_inventory | 2 | TTL 계산 로직, 신선도 분류 기준, 배치 만료 범위 |
| 7 | api_receiving | 4 | 리드타임 계산, 지연 상품 조건, 신제품 라이프사이클 |
| 8 | api_category | 3 | 드릴다운 트리 구조, 매출/폐기/재고 집계 |
| 9 | api_food_monitor | 2 | before/after 비교 정확성, 로그 카운팅 |
| 10 | api_settings | 5 | eval_params 읽기/쓰기 동기화, feature flags 반영 |
| 11 | api_logs | 4 | 세션 파싱, 에러 필터링, 검색 결과 |
| 12 | api_new_product | 3 | 도입 현황, 미달성 목록, 시뮬레이션 점수 |
| 13 | api_health | 2 | 상태 판정 로직, DB 연결 검증 |

### 검증 레벨

1. **API 내부 일관성**: API 응답 내 필드 간 수학적 일관성 (예: total = success + fail)
2. **API-DB 정합성**: API가 반환하는 데이터가 실제 DB 쿼리 결과와 일치
3. **API-Frontend 매칭**: 프론트엔드가 기대하는 응답 필드와 실제 API 응답 구조 일치
4. **Frontend 렌더링 정확성**: DOM에 표시되는 값이 API 응답과 일치

## Approach

### Phase 1: 정적 코드 분석 (API-Frontend 필드 매칭)
- 각 API 엔드포인트의 응답 JSON 키를 추출
- 대응하는 JS 코드에서 참조하는 키를 추출
- **불일치 필드 목록** 생성 (API에는 있지만 JS에서 미사용, 또는 JS에서 참조하지만 API 미반환)

### Phase 2: API 내부 일관성 검증
- summary 필드의 합계 검증 (예: `total_items == ordered_count + skipped_count`)
- 비율 계산 검증 (예: `accuracy = hits / total * 100`)
- 날짜/시간 범위 필터링 정확성

### Phase 3: DB 쿼리 정합성 검증
- Repository 메서드의 SQL 쿼리가 올바른 테이블/컬럼 참조
- store DB vs common DB 라우팅 정확성
- ATTACH 패턴 사용 시 테이블 접근 정확성
- NULL 처리 및 기본값 폴백

### Phase 4: 프론트엔드 렌더링 검증
- DOM element ID 존재 여부
- Chart.js 데이터셋 형식 호환성
- 숫자 포맷팅 (fmt 함수 적용 여부)
- 에러 핸들링 (API 실패 시 UI 동작)

## Success Criteria
- 모든 API 엔드포인트의 응답 필드가 프론트엔드 기대와 100% 매칭
- API 내부 수치 일관성 검증 통과
- DB 쿼리 정합성 검증 통과
- 발견된 불일치 항목 0건 또는 수정 완료

## Out of Scope
- 성능 최적화 (쿼리 속도, 캐시 효율)
- UI/UX 디자인 개선
- 새 기능 추가
- 라이브 DB 데이터의 실제 값 검증 (코드 레벨 검증만 수행)

## Risk
- 일부 API가 인메모리 캐시(LAST_PREDICTIONS 등)에 의존하여 정적 분석만으로는 불충분할 수 있음
- 프론트엔드에서 조건부 렌더링(특정 탭 활성 시에만 호출)되는 API는 테스트 누락 가능

## Timeline
- Plan → Design → Do(코드 분석 실행) → Check(Gap Analysis) → Report
