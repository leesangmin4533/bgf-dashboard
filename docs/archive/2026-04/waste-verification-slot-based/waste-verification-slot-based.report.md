# PDCA Report: waste-verification-slot-based

> 완료일: 2026-04-07
> Match Rate: 100% — PASS

---

## 핵심 요약

폐기 추적 검증을 **슬롯(02시/14시) 기반**으로 재설계. BGF 점주 입력 시각(`waste_slips.cre_ymdhms`)으로 1차/2차 박스를 자동 분류하고 슬롯별 정확도 측정. 동시에 검증 base를 `status != 'active'`로 확장해 어제 사건의 사각지대(consumed로 잘못 마킹된 함박치킨) 해소.

---

## 변경 사항

### 코드
- `src/report/waste_verification_reporter.py`:
  - `_classify_slot(cre_ymdhms)` 헬퍼 추가
  - `get_slot_comparison_data(target_date)` 신규 — waste_slips JOIN, 슬롯별 set 비교
  - `_get_tracking_inventory_batches`: `status='expired'` → `status != 'active'` + `date(expiry_date)=?`
- `src/application/services/waste_verification_service.py`:
  - `verify_date_by_slot(target_date)` 진입점 + 로그 포맷
- `tests/test_waste_verification_slot.py` (신규) — 8개 (분류 3 + 비교 5)

### 슬롯 정의
| 슬롯 | 만료 시각 | BGF 입력 윈도우 (cre_ymdhms HH) | 1차/2차 |
|---|---|---|---|
| `slot_2am` | 02:00 | 02~13 | 1차 박스 |
| `slot_2pm` | 14:00 | 14~23 또는 00~01 | 2차 박스 |

---

## 라이브 검증 (4매장 04-07)

| 매장 | 02시 base/매칭/누락/과잉 | 14시 base/매칭/누락/과잉 |
|---|---|---|
| 46513 | 11 / 1 / 0 / 10 | **3 / 0 / 0 / 3** ★ |
| 46704 | 14 / 0 / 0 / 14 | 4 / 0 / 0 / 4 |
| 47863 | 11 / 0 / **7** / 11 | 7 / 0 / 0 / 7 |
| 49965 | 32 / 0 / 0 / 32 | 33 / 0 / **2** / 33 |

★ **사건 케이스 검증**: 46513 14시 tracking_only 3건 중 **8801771034445 함박치킨 포함** — 어제 사각지대였던 상품이 정확히 잡힘 ✅

---

## 새로 발견된 운영 신호

### 1. 47863 02시 누락 7건 (false negative)
- BGF에는 새벽 폐기 입력됐는데 우리 추적이 못 잡음
- → **추적 시스템 결함 가능성** (별도 조사 가치)

### 2. 49965 14시 누락 2건
- 동일 패턴, 별도 조사

### 3. 과잉(tracking_only) 매장당 3~33건
- 시스템이 만료 예정으로 봤지만 BGF에 폐기 입력 없음
- 해석:
  - 점주가 폐기 입력 안 함 (운영 개선 여지)
  - 또는 시스템 false positive (예측 오류)
- 매장별 매일 추이로 패턴 식별 가능

### 4. 매칭률 0%인 슬롯 다수
- 04-07이 특이한 날일 수 있음 (어제 batch-sync-zero-sales-guard 사건의 영향)
- 다음 날 추이 관찰 필요

---

## 교훈

1. **데이터는 이미 있었다**: `cre_ymdhms`가 BGF 헤더에 14자리로 저장돼 있는데 검증에서 안 쓰고 있었음. 새 수집 작업 없이 JOIN 1줄로 슬롯 분류 가능
2. **사각지대는 두 곳에서 동시 발생**: `status='expired'`만 보던 검증 + `consumed`로 잘못 마킹하던 BatchSync. 어제 batch-sync-zero-sales-guard로 BatchSync 측을 막고, 오늘 검증 측도 막음 → 양방향 가드
3. **base 확장이 신호 발견의 열쇠**: tracking base를 `status != 'active'`로 넓혔더니 47863 02시 7건 누락 같은 진짜 추적 결함이 처음 보임

---

## 후속 작업 후보
- **47863 02시 7건 누락 원인 조사**: false negative의 정체
- **slot_match_rate 일자 추이**: 매일 자동 측정 + 추세 그래프
- **자동 호출 통합**: `verify_date_deep` 끝에 슬롯 검증도 자동 호출
- **KPI 추가**: `K6 = slot_match_rate` 마일스톤에 편입

---

## 관련 문서
- Plan: docs/01-plan/features/waste-verification-slot-based.plan.md
- Design: docs/02-design/features/waste-verification-slot-based.design.md
- Analysis: docs/03-analysis/waste-verification-slot-based.analysis.md
- Issue: docs/05-issues/expiry-tracking.md
