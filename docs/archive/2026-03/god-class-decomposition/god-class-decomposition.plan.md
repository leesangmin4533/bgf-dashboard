# Plan: god-class-decomposition

## 개요
코드리뷰(종합 5.1/10)에서 가장 심각한 구조적 문제로 식별된 God Class 2개를 분해한다.
- **ImprovedPredictor** (3,470줄, 48개 메서드) — 예측+계수+재고+ML+캐싱+로깅 전부 담당
- **AutoOrderSystem** (2,609줄, 39개 메서드) — 데이터로딩+필터+예측+조정+실행+추적 전부 담당

## 목표
- 각 클래스를 500줄 이하의 단일 책임 클래스로 분해
- 외부 Public API 변경 없음 (Facade 패턴 유지)
- 프로덕션 동작 변경 없음 (순수 리팩터링)
- 전체 테스트 2838개 통과 유지

## 분해 대상

### ImprovedPredictor → 4개 클래스 추출 + Facade
| 추출 클래스 | 책임 | 예상 크기 |
|------------|------|----------|
| BasePredictor | WMA/Croston/Feature 기본 예측 | ~550줄 |
| CoefficientAdjuster | 연휴/기온/요일/계절/연관 계수 적용 | ~600줄 |
| InventoryResolver | 재고/미입고 조회+TTL+캐시 | ~250줄 |
| PredictionCacheManager | 7개 배치 캐시 통합 관리 | ~300줄 |

### AutoOrderSystem → 4개 클래스 추출 + Facade
| 추출 클래스 | 책임 | 예상 크기 |
|------------|------|----------|
| OrderDataLoader | 미취급/CUT/자동발주 데이터 로드 | ~250줄 |
| OrderFilter | 제외 필터+수동발주 차감+CUT 모니터링 | ~230줄 |
| OrderAdjuster | 미입고/재고 반영 발주량 조정 | ~280줄 |
| OrderTracker | 발주 추적 DB 저장+평가 업데이트 | ~170줄 |

## 수정하지 않는 것
- 외부 Public API 시그니처
- 기존 테스트 파일 (Mock 대상 = Facade 유지)
- 카테고리별 Strategy 파일
- DB 스키마, config 파일
- 프로덕션 동작

## 검증
- 각 Step 후 `pytest tests/` — 2838개 전부 통과
- 최종 improved_predictor.py ~700줄, auto_order.py ~600줄
