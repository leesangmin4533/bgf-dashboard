# Gap Analysis: k4-non-food-sentinel-filter

> 분석일: 2026-04-07
> Design: docs/02-design/features/k4-non-food-sentinel-filter.design.md
> 이슈체인: scheduling.md#k4-expiry-time-mismatch-31일-not-met
> **Match Rate: 100% — PASS**

---

## 종합 점수

| 항목 | 결과 |
|---|:---:|
| 커넥션 교체 | ✅ |
| products JOIN 추가 | ✅ |
| 식품 mid_cd 필터 | ✅ |
| 임계값 1→7일 | ✅ |
| 회귀 테스트 3개 | ✅ (3/3) |
| 4매장 라이브 검증 | ✅ |
| **Match Rate** | **100%** |

---

## 라이브 검증 결과 (4매장)

| 매장 | Before (1일 임계, 전체) | After (7일 임계, 식품) | 감소율 |
|---|---:|---:|---:|
| 46513 | 4,192 | 220 | 94.8% |
| 46704 | 2,440 | 90 | 96.3% |
| 47863 | 1,078 | 104 | 90.4% |
| 49965 | 618 | 30 | 95.1% |
| **합계** | **8,328** | **444** | **94.7%** |

Plan 예상치(457건)와 거의 일치 (-13건).

## Design 항목 검증

### ✅ 1. 커넥션 교체
`integrity_check_repo.py:255` `self._get_conn()` → `DBRouter.get_store_connection_with_common(store_id)`

### ✅ 2. JOIN 추가
`JOIN common.products p ON ot.item_cd = p.item_cd`

### ✅ 3. 식품 필터
`AND p.mid_cd IN ('001','002','003','004','005','012')`

### ✅ 4. 임계값 1→7일
`> 1` → `> 7`

### ✅ 5. 회귀 테스트 3개 (3/3 통과)
- `test_food_diff_14days_counted` (식품 14일 → 카운트)
- `test_food_diff_5days_skipped` (식품 5일 → 스킵)
- `test_nonfood_huge_diff_skipped` (비식품 9997일 → 스킵)

---

## Gap 목록

### Missing
없음.

### Added
- patch target 디버깅: `IntegrityCheckRepository(store_id=...)` 키워드 인자 사용 인지

### Changed
없음.

---

## 결론
**Match Rate 100% — PASS.** 모든 Design 항목 일치. K4 anomaly count 8,328 → 444 (94.7% 감소). 진짜 식품 정합성 신호 444건이 가시화됨.

## 잔여 검증 (운영)
- [ ] 다음 23:55 OpsMetricsCollector 또는 daily integrity check에서 K4 전환 확인
- [ ] milestone_snapshots K4 NOT_MET → ACHIEVED 여부 (anomaly 임계치 따라)

## 다음 단계
`/pdca report k4-non-food-sentinel-filter` (≥90%, iterate 불필요)
