# Gap Analysis: promo-end-reduction (행사 종료 임박 감량 D-5 확장)

> 분석일: 2026-04-05
> Design: `docs/02-design/features/promo-end-reduction.design.md`

---

## 전체 결과

| 설계 항목 | 상태 | 검증 |
|----------|:----:|------|
| 3.1 constants.py `PROMO_END_REDUCTION_DAYS = 5` | MATCH | constants.py:633 |
| 3.2A END_ADJUSTMENT `5: 0.85, 4: 0.70` 추가 | MATCH | promotion_adjuster.py:47-48 |
| 3.2A END_ADJUSTMENT 기존 D-3~D-0 값 유지 | MATCH | promotion_adjuster.py:49-52 |
| 3.2B 케이스 1 조건 `<= PROMO_END_REDUCTION_DAYS` | MATCH | promotion_adjuster.py:106 |
| 3.2B import 추가 | MATCH | promotion_adjuster.py:12 |
| 3.2C get_adjustment_summary 조건 변경 | MATCH | promotion_adjuster.py:297 |
| 3.3 ctx `_promo_days_until_end` 추가 | MATCH | improved_predictor.py:2611 |
| 5. test D-5 감량 확인 | MATCH | test_end_d5_factor |
| 5. test D-4 감량 확인 | MATCH | test_end_d4_factor |
| 5. test D-6 미적용 | MATCH | test_d6_no_reduction |
| 5. test 행사 연장 스킵 | MATCH | test_d5_extended_promo_skips |
| 5. test 동일 행사 연속 | MATCH | test_d5_next_promo_same_type |
| 5. test promo_avg 정밀 계산 | MATCH | test_d5_with_promo_stats (설계에 없던 보너스) |
| 6. 하위호환: D-3 이하 유지 | MATCH | 기존 31개 테스트 전부 통과 |

**Match Rate: 100% (13/13 + 1 EXTRA)**

---

## Gap: 0건

설계 13개 항목 전부 구현 완료.

## EXTRA: 1건

| 항목 | 내용 |
|------|------|
| test_d5_with_promo_stats | D-5 + promo_avg 있을 때 정밀 계산 테스트 (설계 미명시) |

---

## 테스트 현황

- 전체: **37개 통과** (기존 31 + 신규 6)
- 신규 6개: D-5 factor, D-4 factor, D-6 미적용, 연장 스킵, 동일행사, promo_avg 정밀
- 기존 31개: 변경 없이 전부 통과 (하위호환 검증)
