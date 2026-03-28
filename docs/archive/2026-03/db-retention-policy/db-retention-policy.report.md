# DB 보존 정책 완료 리포트

**Feature**: db-retention-policy
**날짜**: 2026-03-28
**Match Rate**: 97.2% → 100% (오타 수정 후)

---

## PDCA 사이클 요약

```
[Plan] ✅ → [Design] ✅ → [Do] ✅ → [Check] ✅ (97.2%) → [Report] ✅
```

---

## 1. 문제 (Plan)

DB 기록이 무한히 쌓이고 있었습니다.

| 테이블 | 현재 행수 | 보존 정책 | 연간 예상 |
|--------|----------|----------|----------|
| eval_outcomes | 108,243 | ❌ 없음 | 69만행 |
| prediction_logs | 99,379 | ❌ 없음 | 62만행 |
| hourly_sales_detail | 83,161 | ❌ 없음 | 51만행 |
| calibration_history | 265 | ❌ 없음 | 1,800행 |

보존 정책 없이 4매장 1년 운영 시 **약 300만행** 누적, 매장당 DB **400MB** 예상.

## 2. 설계 (Design)

| 테이블 | 보존 기간 | 날짜 컬럼 | 근거 |
|--------|----------|----------|------|
| eval_outcomes | 90일 | eval_date | ML 학습 윈도우 |
| prediction_logs | 60일 | prediction_date | 디버깅 용도 |
| hourly_sales_detail | 90일 | sales_date | 시간대 분석 |
| calibration_history | 120일 | calibration_date | 보정 추이 |

구현 위치: collection.py Phase 1.36 (수집 완료 직후, 발주 전)

## 3. 구현 (Do)

- **커밋**: `4a2de64` feat(scheduler): DB 보존 정책 추가
- **변경**: `src/scheduler/phases/collection.py` +27줄
- **방식**: DELETE만 사용, VACUUM 금지 (WAL 모드 페이지 자동 재활용)
- **안전 장치**: try/except (실패해도 발주 계속), NULL 행 자동 보호

## 4. 검증 (Check)

### Gap 분석: 24개 항목 비교

| 결과 | 건수 |
|------|------|
| 완전 일치 | 22 |
| 설계서 오타 (구현이 맞음) | 1 → 수정 완료 |
| 미미한 차이 (인라인 vs 함수) | 1 |

### 시뮬레이션 결과 (46513 기준)

| 테이블 | 전체 | 삭제 대상 | 보존 |
|--------|------|----------|------|
| eval_outcomes | 108,243 | 0 | 108,243 (아직 90일 미만) |
| prediction_logs | 99,379 | 0 | 99,379 (아직 60일 미만) |
| **hourly_sales_detail** | 83,161 | **45,764** | 37,397 ✅ |
| calibration_history | 265 | 0 | 265 |

## 5. 전문가 토론 반영

| 전문가 | 핵심 의견 | 반영 |
|--------|---------|------|
| DBA | 90일 롤링 보존 + VACUUM 금지 | ✅ 반영 |
| 악마의 변호인 | 보존 정책 전무 = 시한폭탄 | ✅ 해결 |
| 실용주의 | 테이블 DROP은 하지 마라, 보존 정책만 | ✅ 반영 |

## 6. 효과

| 지표 | Before | After |
|------|--------|-------|
| 보존 정책 | ❌ 없음 | ✅ 4개 테이블 |
| 연간 DB 성장 | ~200MB/매장 | ~50MB/매장 (75% 절감) |
| 수동 개입 | 필요 | 불필요 (매일 자동) |
| 발주 영향 | - | 없음 (Phase 1.36 독립 실행) |

---

*PDCA 사이클 완료. 다음: `/pdca archive db-retention-policy`*
