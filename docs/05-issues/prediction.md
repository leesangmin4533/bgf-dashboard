# 예측 이슈 체인

> 최종 갱신: 2026-04-05
> 현재 상태: ML is_payday DB 반영 완료, 효과 검증 대기

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

---
