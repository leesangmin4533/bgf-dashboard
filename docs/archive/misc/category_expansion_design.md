# 42개 미설정 카테고리 예측 모듈 확장 설계

## 설계 원칙

### 매장 독립성 (Store-Independence)
- **고정**: 예측 TYPE (어떤 알고리즘을 쓸지) → 상품 특성으로 결정
- **적응**: 파라미터 (요일계수, 안전재고일수) → 각 매장 데이터에서 학습
- **폴백**: 데이터 부족 시 → 카테고리 유형별 합리적 기본값 사용

### 기존 패턴 준수
- 기존 5개 모듈(food/beer/soju/tobacco/ramen)의 구조를 동일하게 따름
- 각 모듈: 상수 → dataclass → is_category() → analyze_pattern() → calculate_dynamic_safety() → get_safety_stock_with_pattern()
- improved_predictor.py의 elif 체인 확장

---

## 신규 모듈 8개

### 1. perishable.py (소멸성 상품)
- **대상**: 013(떡), 026(과일/채소), 046(요구르트)
- **핵심 로직**: 유통기한 기반 안전재고 최소화 + 폐기 방지
- **차별점**: food.py와 유사하나 비도시락/김밥류. 유통기한 3~14일 범위
- **안전재고**: 0.3~1.0일치 (유통기한별)
- **상한선**: 유통기한일 x 일평균
- **요일계수**: 데이터 학습 (기본: 평일 안정, 주말 미세 증가)

### 2. beverage.py (음료류)
- **대상**: 010(제조음료), 039(과일야채음료), 043(차음료), 045(아이스드링크), 048(얼음)
- **핵심 로직**: 주말 소폭 증가 + 계절 인식 + 회전율 기반
- **차별점**: 장기유통이지만 소비 패턴에 주말/계절 영향
- **안전재고**: 1.5~2.5일치 (회전율별)
- **상한선**: 7일치
- **요일계수**: 데이터 학습 (기본: 토 1.15, 일 1.10)

### 3. frozen_ice.py (냉동/아이스크림)
- **대상**: 021(일반아이스크림), 034(냉동즉석식), 100(RI아이스크림)
- **핵심 로직**: 계절 가중치 + 주말 증가 + 장기유통
- **차별점**: 여름/겨울 판매량 2~3배 차이. 재고 부담 낮음(장기유통)
- **안전재고**: 2.0~3.0일치
- **상한선**: 7일치
- **계절계수**: 월별 학습 (기본: 여름 1.5, 겨울 0.6)
- **요일계수**: 데이터 학습 (기본: 토 1.30, 일 1.40)

### 4. instant_meal.py (즉석식품)
- **대상**: 027(농산식재료), 028(축수산식재료), 031(반찬류), 033(상온즉석식), 035(냉장즉석식)
- **핵심 로직**: 유통기한 범위가 넓어 상품별 유통기한 기반 분기
- **차별점**: 같은 카테고리 내 단기(7일)~장기(480일) 상품 혼재
- **안전재고**: 상품별 유통기한에 따라 0.5~2.0일치
- **상한선**: min(5일치, 유통기한-1일)
- **요일계수**: 데이터 학습 (기본: 안정형)

### 5. snack_confection.py (과자/간식/디저트)
- **대상**: 014(디저트), 017(시리얼), 018(껌), 029(조미료류), 030(커피차류)
- **핵심 로직**: 안정적 수요 + 장기유통 + 보수적 발주
- **차별점**: 수요 변동 적음, 폐기 리스크 낮음
- **안전재고**: 1.5~2.0일치
- **상한선**: 7일치
- **요일계수**: 데이터 학습 (기본: 거의 균일 1.00)

### 6. alcohol_general.py (일반주류)
- **대상**: 052(양주), 053(와인)
- **핵심 로직**: 주말 집중 패턴 (beer/soju 프레임워크 재사용)
- **차별점**: 매우 낮은 판매량, 높은 단가, 주말 패턴 강함
- **안전재고**: 2.0~3.0일치 (평일 2, 금토 3)
- **상한선**: 14일치
- **요일계수**: 데이터 학습 (기본: 금 1.50, 토 2.00)

### 7. daily_necessity.py (생활용품)
- **대상**: 036(의약외품), 037(건강기능), 056(목욕세면), 057(위생용품), 086(안전상비의약품)
- **핵심 로직**: 결품 방지 우선 + 안정적 보충
- **차별점**: 비식품, 필수 재고 유지 중요, 판매 불규칙하나 결품 시 고객 이탈
- **안전재고**: 2.0일치 (고정)
- **최소재고**: 1개 (결품 방지)
- **상한선**: 14일치
- **요일계수**: 데이터 학습 (기본: 균일 1.00)

### 8. general_merchandise.py (잡화/비식품)
- **대상**: 054, 055, 058, 059, 061, 062, 063, 064, 066, 067, 068, 069, 070, 071
- **핵심 로직**: 최소 재고 유지 + 판매 시에만 보충
- **차별점**: 극소량 판매, 진열 목적, 과잉재고 = 자본 낭비
- **안전재고**: 1.0일치
- **최소재고**: 1개
- **상한선**: 3일치 (엄격)
- **요일계수**: 적용 안 함 (데이터 부족으로 노이즈만 발생)

---

## 매장 독립적 데이터 학습 메커니즘

### _learn_weekday_pattern(mid_cd, db_path)
각 모듈에 포함되는 공통 학습 함수:
1. 최근 30일 판매 데이터 조회
2. 요일별 평균 판매량 계산
3. 전체 평균 대비 비율로 요일계수 산출
4. min_data_days(14일) 미만이면 기본값 반환
5. 이상치 제거: 계수 0.5~2.5 범위로 클램프

### 계절계수 (frozen_ice, beverage만)
- 월별 판매량 비율 학습
- 데이터 3개월 미만이면 기본 계절 패턴 사용

---

## improved_predictor.py 변경

### 라우팅 순서 (기존 + 신규)
```
9-1.  ramen           → 006, 032
9-2.  tobacco         → 072, 073
9-3.  beer            → 049
9-4.  soju            → 050
9-5.  food            → 001, 002, 003, 004, 005, 012
9-6.  perishable      → 013, 026, 046        [NEW]
9-7.  beverage        → 010, 039, 043, 045, 048  [NEW]
9-8.  frozen_ice      → 021, 034, 100        [NEW]
9-9.  instant_meal    → 027, 028, 031, 033, 035  [NEW]
9-10. snack_confection → 014, 017, 018, 029, 030  [NEW]
9-11. alcohol_general → 052, 053             [NEW]
9-12. daily_necessity → 036, 037, 056, 057, 086  [NEW]
9-13. general_merchandise → 054-071          [NEW]
9-14. default          → 나머지 (이전 기존 요일계수 있는 카테고리 포함)
```

### default.py 변경
- WEEKDAY_COEFFICIENTS에 42개 카테고리 기본 요일계수 추가
- CATEGORY_NAMES에 42개 카테고리 한글명 추가

---

## 파일 목록

### 신규 생성 (8개)
- src/prediction/categories/perishable.py
- src/prediction/categories/beverage.py
- src/prediction/categories/frozen_ice.py
- src/prediction/categories/instant_meal.py
- src/prediction/categories/snack_confection.py
- src/prediction/categories/alcohol_general.py
- src/prediction/categories/daily_necessity.py
- src/prediction/categories/general_merchandise.py

### 수정 (2개)
- src/prediction/improved_predictor.py (라우팅 확장)
- src/prediction/categories/default.py (요일계수 + 카테고리명 추가)

### 테스트 신규 (8개)
- tests/test_perishable_category.py
- tests/test_beverage_category.py
- tests/test_frozen_ice_category.py
- tests/test_instant_meal_category.py
- tests/test_snack_confection_category.py
- tests/test_alcohol_general_category.py
- tests/test_daily_necessity_category.py
- tests/test_general_merchandise_category.py
