# Completion Report: promo-end-reduction (행사 종료 감량 D-5 확장)

> 완료일: 2026-04-05
> Match Rate: 100%

---

## 1. 요약

| 항목 | 내용 |
|------|------|
| 기능 | 행사 종료 감량 범위 D-3 → D-5 확장 |
| 동기 | D-3은 재고 소진 시간 부족 → 행사 종료 후 폐기 |
| 결과 | END_ADJUSTMENT에 D-5(85%), D-4(70%) 추가 + 조건 상수화 |
| Match Rate | 100% (13/13) |
| 테스트 | 37개 전부 통과 (기존 31 + 신규 6) |
| 마일스톤 기여 | K2 (폐기율) — HIGH |

---

## 2. 변경 내용

| 파일 | 변경 | 줄 |
|------|------|---|
| constants.py | `PROMO_END_REDUCTION_DAYS = 5` | 1줄 |
| promotion_adjuster.py | END_ADJUSTMENT 확장 + `<= 3` → `<= PROMO_END_REDUCTION_DAYS` (2곳) + import | 5줄 |
| improved_predictor.py | `ctx["_promo_days_until_end"]` 추가 | 3줄 |
| test_promotion_adjuster.py | D-5/D-4/D-6/연장/동일행사/정밀계산 6개 테스트 | ~80줄 |

**총 코드 변경: 9줄** (테스트 제외)

---

## 3. 설계 판단

| 판단 | 이유 |
|------|------|
| Stage 7 PROMO FLOOR 수정 불필요 | 코드 확인 결과 사실상 미동작 (qty>0 and qty<1 = 정수 불가) |
| D-5=85%, D-4=70% | 점진적 감량으로 급격한 품절 방지 |
| `PROMO_END_REDUCTION_DAYS` 상수화 | 매직넘버 제거, 향후 조정 용이 |
| `_promo_days_until_end` ctx 추가 | 디버깅 + 향후 Stage 7 활용 대비 |

---

## 4. PDCA 이력

| 단계 | 날짜 | 결과 |
|------|------|------|
| Plan | 04-05 | 문제 정의, 3단계 변경 계획, PROMO FLOOR 덮어쓰기 가설 |
| Design | 04-05 | 코드 확인 후 FLOOR 수정 불필요 판단 → 변경 3건으로 축소 |
| Do | 04-05 | 9줄 코드 변경 + 6개 테스트, 37개 전부 통과 |
| Check | 04-05 | 100% — Gap 0건 |

---

## 5. 후속 작업

- [ ] 다음 행사 종료 상품에서 D-5~D-4 감량 로그 확인 (이슈체인 WATCHING)
- [ ] 1주 운영 후 폐기 건수 비교
- [ ] Stage 8(CategoryFloor) 충돌 여부 모니터링
