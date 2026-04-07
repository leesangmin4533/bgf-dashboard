# PDCA Report: k4-non-food-sentinel-filter

> 완료일: 2026-04-07
> Match Rate: 100% — PASS
> 이슈체인: scheduling.md#k4-expiry-time-mismatch-31일-not-met

---

## 핵심 요약

K4(`expiry_time_mismatch`)가 31일 연속 NOT_MET이던 진짜 원인은 **비식품 92.4%가 K4 노이즈**였음. `check_expiry_time_mismatch`를 식품 카테고리(001~005, 012) 전용 + 임계값 1→7일로 재정의해 mismatch **8,328 → 444건 (94.7% 감소)**. 진짜 식품 정합성 신호 444건이 가시화됨.

---

## PDCA 사이클 요약

| 단계 | 결과 |
|------|------|
| **Plan** | sentinel 필터 → D(식품 전용)+C(7일 임계값) 조합으로 범위 확장 |
| **Design** | 4곳 변경 (커넥션/JOIN/필터/임계값), 회귀 테스트 3 케이스 |
| **Do** | 코드 1메서드 수정 + 테스트 3개 + 4매장 라이브 검증 |
| **Check** | Match Rate 100% (Gap 없음) |
| **Act** | 불필요 |

---

## 발견 과정 (PDCA 선순환)

1. **04-07 14:00**: 사용자가 도시락 02:00 만료 → 14:00 폐기 발견
2. **K4 데이터 조사**: 처음 5건만 보고 sentinel(2053년) 발견 → `k4-non-food-sentinel-filter`로 작업 명명
3. **조사 심화**: 8,328건 전체 분석 → sentinel은 2.8%만, 92.4%가 비식품 노이즈 발견
4. **Plan 재설정**: D(식품 전용) + C(7일 임계값) 조합으로 변경
5. **결과**: 8,328 → 444 (예상 457과 13건 오차)

---

## 변경 사항

### 코드
- `src/infrastructure/database/repos/integrity_check_repo.py:249-288` `check_expiry_time_mismatch`
  - 커넥션: `self._get_conn()` → `DBRouter.get_store_connection_with_common()`
  - SQL: `JOIN common.products p ON wsi.item_cd = p.item_cd` 추가
  - 필터: `p.mid_cd IN ('001','002','003','004','005','012')` 추가
  - 임계값: `> 1` → `> 7`

### 테스트
- `tests/test_integrity_check_k4.py` (신규) — `TestExpiryTimeMismatchK4Filter` 3개

---

## 검증 결과

### 4매장 라이브
| 매장 | Before | After | 감소율 |
|---|---:|---:|---:|
| 46513 | 4,192 | 220 | 94.8% |
| 46704 | 2,440 | 90 | 96.3% |
| 47863 | 1,078 | 104 | 90.4% |
| 49965 | 618 | 30 | 95.1% |
| **합계** | **8,328** | **444** | **94.7%** |

### 자동 테스트
- 3/3 통과

---

## 교훈

1. **첫 5건만 보지 마라**: 정렬 순서에 따라 가장 큰 차이값(sentinel 9997일)이 보이지만 그게 다수가 아닐 수 있음. 조사 단계에서 distribution + GROUP BY로 전체 그림 파악 필수
2. **KPI 의도와 측정 대상 일치**: K4 의도는 식품 폐기 정합성인데 측정은 모든 카테고리 → 노이즈가 진짜 신호 묻음. KPI 정의 시 측정 범위 명시 필요
3. **임계값은 시작점**: 7일은 보수적 시작점, 1주 운영 후 실제 식품 mismatch 패턴 보고 재조정. constants 분리는 미루고 매직 넘버 + 주석으로 충분 (YAGNI)
4. **PDCA 선순환 입증**: 사용자 한 가지 폐기 사례 → K4 조사 → 데이터 심화 → 작업 방향 전환 → 4매장 동시 정상화. claude-auto-respond + PDCA + 직접 조사가 같이 작동

---

## 후속 작업 후보
- `expiry-2am-alert`: 식품 02:00 만료 상품 새벽 알림 슬롯 추가 (사용자 원래 제기 문제)
- `k4-food-mismatch-investigation`: 444건 식품 mismatch 7~30일 312건의 진짜 원인 분석 (OT/IB 산정 로직 차이)
- `nonfood-mismatch-metric`: 비식품 mismatch 별도 메트릭으로 분리 (3차 목표)

---

## 관련 문서
- Plan: `docs/01-plan/features/k4-non-food-sentinel-filter.plan.md`
- Design: `docs/02-design/features/k4-non-food-sentinel-filter.design.md`
- Analysis: `docs/03-analysis/k4-non-food-sentinel-filter.analysis.md`
- Issue: `docs/05-issues/scheduling.md#k4-expiry-time-mismatch-31일-not-met`
