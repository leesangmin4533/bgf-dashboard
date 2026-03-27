# Plan: 소분류 내 상품 대체/잠식(Cannibalization) 감지

> **Feature**: substitution-detect
> **Created**: 2026-03-01
> **Status**: Approved

---

## 1. 배경

같은 소분류(small_cd) 내에서 신상품 출시 시 기존 상품의 수요가 감소하는 현상(잠식, cannibalization)이 빈번히 발생한다.
예를 들어, small_cd=005(삼각김밥) 내에서 A상품의 매출이 급증하면서 B상품의 매출이 급감하는 경우,
B상품의 발주량을 기존 예측 기반으로 유지하면 과잉 재고와 폐기로 이어진다.

현재 시스템은 개별 상품의 판매 트렌드만 추적하며, 같은 소분류 내 상품 간 상호 영향을 고려하지 않는다.

### 문제 정의

1. 같은 소분류 내 신상품 출시 시 기존 상품 수요 감소 미감지
2. 잠식된 상품의 과잉 발주 -> 폐기 증가
3. 소분류 내 총 수요량은 유지되나 상품별 비율이 변동하는 상황 미대응

## 2. 목표

| # | 항목 | 목표 |
|---|------|------|
| 1 | 잠식 감지 | 같은 small_cd 내 상품 간 수요 이동 자동 감지 |
| 2 | 발주 조정 | 잠식된 상품의 예측 발주량 자동 감소 (계수 0.7~0.9) |
| 3 | 이벤트 기록 | substitution_events 테이블에 감지 이력 저장 |
| 4 | 파이프라인 통합 | improved_predictor.py의 후처리 단계에 잠식 계수 적용 |

## 3. 범위

### In Scope
- `src/analysis/substitution_detector.py` 신규 모듈
- `src/infrastructure/database/repos/substitution_repo.py` Repository
- `src/infrastructure/database/schema.py` 및 `src/db/models.py` 스키마 확장 (v48)
- `src/prediction/improved_predictor.py` 잠식 계수 적용 단계 추가
- `src/settings/constants.py` 관련 상수 정의
- `src/infrastructure/database/repos/__init__.py` re-export
- 테스트

### Out of Scope
- 웹 대시보드 시각화 (추후)
- 대분류/중분류 레벨 잠식 분석
- 실시간 알림 (카카오 알림 연동)

## 4. 알고리즘 개요

### 4.1 잠식 감지 로직
1. 같은 small_cd 내 상품 목록 조회 (product_details JOIN products)
2. 각 상품의 최근 14일 이동평균 vs 이전 14일 이동평균 비교
3. 수요 증가 상품(gainer)과 수요 감소 상품(loser) 식별
4. gainer의 증가량과 loser의 감소량의 상관관계 분석
5. 상관 관계가 유의미하면 잠식으로 판정

### 4.2 판정 기준
- 감소 상품: 후반 14일 평균 / 전반 14일 평균 < 0.7 (30% 이상 감소)
- 증가 상품: 후반 14일 평균 / 전반 14일 평균 > 1.3 (30% 이상 증가)
- 소분류 총량 변화: |총량 변화율| < 20% (총 수요는 유지되어야 잠식)

### 4.3 발주 조정 계수
- 감소율 30~50%: 계수 0.9 (10% 감소)
- 감소율 50~70%: 계수 0.8 (20% 감소)
- 감소율 70% 이상: 계수 0.7 (30% 감소)

## 5. 예상 수정/신규 파일

| 파일 | 유형 | 내용 |
|------|------|------|
| `src/analysis/substitution_detector.py` | 신규 | SubstitutionDetector 클래스 |
| `src/infrastructure/database/repos/substitution_repo.py` | 신규 | SubstitutionEventRepository |
| `src/infrastructure/database/repos/__init__.py` | 수정 | re-export 추가 |
| `src/infrastructure/database/schema.py` | 수정 | STORE_SCHEMA에 테이블 추가 |
| `src/db/models.py` | 수정 | SCHEMA_MIGRATIONS v49 추가 |
| `src/settings/constants.py` | 수정 | DB_SCHEMA_VERSION=49, 잠식 상수 |
| `src/prediction/improved_predictor.py` | 수정 | 잠식 계수 적용 단계 추가 |
| `tests/test_substitution_detector.py` | 신규 | 테스트 |

## 6. 의존성

- product_details.small_cd (TEXT, 198종, 5,214개 수집 완료)
- daily_sales (store DB): item_cd, sale_qty, sales_date
- products (common DB): item_cd, mid_cd
- 기존 분석 모듈 패턴: `src/analysis/waste_cause_analyzer.py`
- 예측 파이프라인: `src/prediction/improved_predictor.py`

## 7. 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| small_cd 미수집 상품 | 분석 불가 | small_cd가 없는 상품은 건너뜀 |
| 소분류 내 상품 수 1개 | 비교 불가 | 2개 미만이면 스킵 |
| 계절성 수요 변화와 혼동 | 오탐 | 소분류 총량 변화 20% 초과 시 잠식 아닌 것으로 판정 |
