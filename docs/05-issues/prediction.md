# 예측 이슈 체인

> 최종 갱신: 2026-04-11
> 현재 상태: ML is_payday DB 반영 완료, 효과 검증 대기. 04-10 계절전환 MAE 급등 이슈 3건 신규 등록

---

## [PLANNED] mid_cd 048/049 MAE 동시 악화 — 봄 계절전환 계절계수 지연 (P2)

**목표**: mid=048(음료류)와 mid=049(맥주)가 2개 매장에서 동시에 MAE 악화. 봄 계절전환 시 BeverageStrategy/BeerStrategy(음료/맥주 예측 전략)의 계절계수(season_coef)가 실수요 변화 속도를 2~3주 지연 반영하는 구조적 문제 의심.
**동기**: 자동 감지 (2026-04-10) — 048: mae_7d=11.6 vs mae_14d=9.3 / 049: mae_7d=6.2 vs mae_14d=4.9. AI 분석(2026-04-11_response.md): 2개 매장 동시 악화 = 전략 공통 원인.
**선행조건**: 04-17 1주 관측 완료 후 조정 여부 결정 (급격한 상수 변경은 데이터 오염 우려)
**예상 영향**: `src/domain/prediction/strategies/beverage.py`, `beer.py`, `constants.py` 계절계수 설정

---

## [PLANNED] mid_cd 073 전자담배 MAE 급등 — TobaccoStrategy 파라미터 불일치 (P2)

**목표**: mid=073(전자담배) mae_7d=9.0 vs mae_14d=5.4 (+67%). 072(담배)와 동일 TobaccoStrategy(담배 예측 전략) 적용 중이나 보루/소진 패턴이 상이. 전자담배는 개당 판매로 단위가 다름.
**동기**: 자동 감지 (2026-04-10) — AI 분석(2026-04-11_response.md): TobaccoStrategy 073 전용 파라미터 분리 또는 서브클래스 검토 필요
**선행조건**: 없음
**예상 영향**: `strategy_registry.py`, `src/domain/prediction/strategies/tobacco.py`

---

## [PLANNED] mid_cd 032 면류 MAE 악화 + 폐기율 동시 상승 — RamenStrategy 과예측 전환 의심 (P2)

**목표**: mid=032(면류) mae_7d=7.4 vs mae_14d=5.6, waste_rate_7d=17.7% vs rate_30d=6.4%. 예측 오류와 폐기율 동시 상승 = 과예측 방향 전환 의심. food-underprediction Phase A(04-10 배포) 또는 STOP_RECOMMEND 자동확정 처리 이후 간섭 가능성.
**동기**: 자동 감지 (2026-04-10) — AI 분석(2026-04-11_response.md)
**선행조건**: 없음
**예상 영향**: `src/domain/prediction/strategies/ramen.py`, Phase A 보정 로직

---

## [PLANNED] ML is_payday DB 반영 효과 검증 (P2)

**목표**: PaydayAnalyzer DB 결과를 ML 피처에 반영한 후 예측 정확도 변화 측정
**동기**: is_payday 중요도 22%인 핵심 피처가 하드코딩→DB 기반으로 변경됨 (f0657a8). 매장별 패턴이 다른데 하드코딩은 동일 날짜를 사용해 정확도 손실 가능성
**선행조건**: f0657a8 커밋 반영 후 최소 2주 운영 데이터 필요
**예상 영향**: improved_predictor.py, ml/trainer.py 로그 분석

---

## [PLANNED] PaydayAnalyzer 결과를 ML 학습 데이터에도 반영 (P3)

**목표**: 현재 학습 데이터의 is_payday 피처도 DB 기반으로 소급 적용
**동기**: 예측 시에는 DB 기반 값을 사용하지만, 학습 데이터는 과거 하드코딩 기준으로 생성됨 → 학습/예측 간 불일치
**선행조건**: P2 효과 검증 완료 후 양수 효과 확인 시
**예상 영향**: ml/feature_builder.py build_training_data(), ml/trainer.py

## [PLANNED] 예측 정확도 하락 조사 (4개 카테고리) (P1)

**목표**: 카테고리 015, 039, 040, 048 7일 MAE가 14일 평균 대비 악화. 최대 61% 상승 (mid 048)
**동기**: 자동 감지 (2026-04-06) -- prediction_accuracy
**선행조건**: 없음
**예상 영향**: prediction_accuracy 관련 파일


## [PLANNED] mid_cd 605 DefaultStrategy 오분류 — 카테고리 매핑 누락 (P2)

**목표**: mid_cd 605가 strategy_registry에 미등록되어 DefaultStrategy 적용 중. MAE 7d=6.0 vs 14d=4.3으로 악화. 카테고리 정의 확인 후 적절한 Strategy 배정 필요. 영향 파일: strategy_registry.py, constants.py
**동기**: 자동 감지 (2026-04-09) -- prediction_accuracy
**선행조건**: 없음
**예상 영향**: prediction_accuracy 관련 파일


## [PLANNED] mid_cd 016 과자류 MAE 20 이상 이상 급등 — 행사/수집 원인 미규명 (P2)

**목표**: mid_cd 016(과자/제과, SnackConfectionStrategy) 7d MAE=20.0 vs 14d=15.3. 절대값이 비정상적으로 큼. over_order 방향 및 최근 7일 내 행사 변화 여부 확인 필요. 행사 종료 임박 자동화(order-execution.md WATCHING) 우선 대응 고려.
**동기**: 자동 감지 (2026-04-09) -- prediction_accuracy
**선행조건**: 없음
**예상 영향**: prediction_accuracy 관련 파일


---
