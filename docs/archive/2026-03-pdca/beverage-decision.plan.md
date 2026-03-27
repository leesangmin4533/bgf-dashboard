# Plan: beverage-decision (음료 발주 유지/정지 판단 시스템)

## 문제
음료 카테고리(mid_cd 039~048, 972개 상품)에 대한 발주 유지/정지 판단 시스템이 없어,
안 팔리는 상품이 매대를 차지하는 비효율이 발생. 디저트 판단 시스템(v1.2)을 확장하되,
음료 고유 특성(10개 중분류, 22% 소분류 누락, 0.26% 낮은 폐기율, 높은 계절성/행사 영향)을 반영해야 함.

## 핵심 차이 (디저트 vs 음료)

| 항목 | 디저트 (014) | 음료 (039~048) |
|------|-------------|----------------|
| 상품 수 | 174개 | 972개 (5.6배) |
| 중분류 | 1개 | 10개 |
| 소분류 커버리지 | 99.4% | 77.8% (22% 누락) |
| 유통기한 커버리지 | 98.3% | 78% (22% NULL) |
| 전체 폐기율 | ~1% | 0.26% |
| 핵심 리스크 | 폐기 비용 | 매대 비효율 |
| 행사 비율 | 낮음 | 49% (474/972) |

## 해결 방향

1. **분류**: 중분류 1차(100% 커버) → 소분류 2차 → 유통기한 NULL 폴백 2.5차 → 안전장치 3차
2. **판단 지표**: 폐기율 + 매대효율지표(소분류 중위값 대비 판매 비율) 복합 기준
3. **4카테고리**: A(냉장단기/주1회), B(냉장중기/격주), C(상온장기/월1회), D(생수얼음/월1회)
4. **보호 규칙**: 행사 종료 후 보호(유형별 차등), 계절 비수기 완화
5. **DB**: dessert_decisions → category_decisions 리네이밍 + category_type 컬럼 (v53)

## 수정 파일 (예상)

| 파일 | 변경 |
|------|------|
| `src/prediction/categories/beverage_decision/` | 신규 모듈 (classifier, judge, constants, enums) |
| `src/application/services/beverage_decision_service.py` | 서비스 오케스트레이션 |
| `src/application/use_cases/beverage_decision_flow.py` | Use Case 래퍼 |
| `src/infrastructure/database/repos/beverage_decision_repo.py` | Repository |
| `src/infrastructure/database/schema.py` | v53 마이그레이션 (테이블 리네이밍) |
| `src/order/order_filter.py` | BEVERAGE_STOP 필터 추가 |
| `src/web/routes/api_beverage_decision.py` | 웹 API |
| `run_scheduler.py` | 스케줄러 등록 |

## 테스트 계획
- 분류기 테스트: 4카테고리 분류 정확성, 소분류 없는 상품, 유통기한 NULL 폴백, 안전장치
- 판단 엔진 테스트: A/B/C/D 각 카테고리별 판단 기준, 행사 보호, 계절 비수기
- 서비스 통합 테스트: 전체 플로우, auto_confirm 차등
- OrderFilter 연동 테스트: CONFIRMED_STOP만 필터링

## 영향 분석

| 항목 | 영향 |
|------|------|
| DB 스키마 | v53: dessert_decisions → category_decisions 리네이밍 + category_type 추가 |
| 기존 디저트 시스템 | 테이블 리네이밍으로 import 경로 변경 필요 |
| OrderFilter | BEVERAGE_STOP 추가 (기존 DESSERT_STOP과 병렬) |
| 스케줄러 | 디저트 뒤 순차 실행 (22:30/22:45/23:00) |
