# Plan: 슬롯 기반 폐기 추적 검증 (waste-verification-slot-based)

> 작성일: 2026-04-07
> 상태: Plan
> 이슈체인: expiry-tracking.md (등록 예정)
> 마일스톤 기여: 폐기 추적 정확도 측정 — HIGH

---

## 1. 문제

현재 폐기 검증(`waste_verification_service`)은 **일자(date) 단위** 매칭만 함:
- waste_slip 상품 집합 vs (order_tracking + inventory_batches expired) 집합
- 결과: matched / slip_only / tracking_only

**한계**:
1. **1차/2차 슬롯 구분 없음**: 같은 상품이 02:00 만료(1차)와 14:00 만료(2차) 둘 다 있을 때 어느 박스 폐기인지 모름
2. **사각지대**: tracking 측 base가 `status='expired'`만 → 04-07 사건처럼 `consumed`로 잘못 마킹된 배치는 검증조차 안 됨 (46513 04-07 81건 누락)
3. **시간 정밀도 0**: 매칭만 보고 "언제 처리됐는가" 측정 안 함

### 사용자 요청
"폐기 시간 기준 다음 폐기 시간 전까지를 윈도우로" — 즉:
- **02:00 슬롯**: BGF 입력 시각 `02:00 ~ 13:59` = 1차 폐기로 인정
- **14:00 슬롯**: BGF 입력 시각 `14:00 ~ 다음날 01:59` = 2차 폐기로 인정

### 데이터 가용성 (확인 완료)
`waste_slips.cre_ymdhms` 필드가 BGF 점주 입력 시각을 14자리 문자열(`YYYYMMDDHHMMSS`)로 이미 저장 중. **추가 수집 불필요**.

| 매장 | 최근 7일 헤더 | cre_ymdhms 채워진 비율 |
|---|---:|:---:|
| 46513 | 41 | 100% |
| 46704 | 31 | 100% |
| 47863 | 1,070 | 100% |
| 49965 | 54 | 100% |

---

## 2. 목표

### 1차
**슬롯 기반 매칭 검증** 메서드 추가:
- 02:00 슬롯 / 14:00 슬롯 별로 따로 매칭
- BGF 입력 시각(`cre_ymdhms`)으로 슬롯 자동 분류
- tracking base에 `status != 'active'` 적용 (사각지대 해소)

### 2차
새 메트릭:
- `slot_match_rate` (슬롯별 추적 정확도)
- `slot_only` / `tracking_only` (슬롯별)
- `unclassified` (윈도우 밖 폐기, 운영 신호)

### 3차
일자 검증과 병행 (현재 매일 14:00 검증 자동 호출에 슬롯 검증 추가)

### 비목표
- BGF 수집기 변경 (이미 cre_ymdhms 수집 중)
- 별도 KPI 추가 (메트릭은 검증 보고서에 포함)
- 슬롯 ±10분 좁은 윈도우 (사용자가 "다음 폐기 시간 전까지"로 명시)

---

## 3. 슬롯 정의

| 슬롯 이름 | 만료 시각 | BGF 입력 윈도우 (cre_ymdhms 기준) | 1차/2차 |
|---|---|---|---|
| **slot_2am** | 02:00 (도시락 1차) | `02:00 ~ 13:59` | 1차 박스 |
| **slot_2pm** | 14:00 (도시락 2차) | `14:00 ~ 다음날 01:59` | 2차 박스 |

### 윈도우 경계 처리
- `cre_ymdhms` 형식: `20260407034306` → datetime `2026-04-07 03:43:06`
- HH 부분이 02~13이면 slot_2am, 14~23 또는 00~01이면 slot_2pm
- **00:00~01:59** = 전날 14:00 슬롯의 연장선 (2차)

### 검증 base 정의
| 슬롯 | tracking base 조건 |
|---|---|
| slot_2am (1차) | `expiry_date의 시각 = '02:00:00'` AND `status != 'active'` |
| slot_2pm (2차) | `expiry_date의 시각 = '14:00:00'` AND `status != 'active'` |

---

## 4. 결과 데이터 구조

```python
{
  "date": "2026-04-07",
  "store_id": "46513",
  "slot_2am": {
    "tracking_base": 3,        # 02시 만료 예정 추적 대상
    "slip_in_window": 1,       # 윈도우 내 BGF 폐기
    "matched": 1,              # 매칭 성공
    "slip_only": 0,            # BGF에는 폐기 / 추적 없음
    "tracking_only": 2,        # 추적 / BGF 폐기 없음
    "match_rate": 33.3,        # matched / tracking_base
    "ontime_rate": 33.3        # slip_in_window / tracking_base
  },
  "slot_2pm": { ... },
  "unclassified": 0,           # 어느 슬롯에도 안 들어간 폐기
  "summary": {
    "overall_match_rate": ...,
    "false_negative": 0,       # 추적이 못 잡은 BGF 폐기 (slip_only 합)
    "false_positive": 2        # 추적했지만 폐기 안 됨 (tracking_only 합)
  }
}
```

---

## 5. 변경 대상

| 파일 | 변경 |
|---|---|
| `src/application/services/waste_verification_service.py` | 새 메서드 `verify_date_by_slot()` 추가 |
| `src/report/waste_verification_reporter.py` | 슬롯 분류 헬퍼 + tracking base 쿼리에 `status != 'active'` 변경 |
| `tests/test_waste_verification_slot.py` | 신규 회귀 테스트 (5개) |
| `docs/05-issues/expiry-tracking.md` | 이슈 등록 |

### 비범위 (이번 작업 내)
- `verify_date_deep` 자동 호출 통합 (별도 follow-up)
- KPI/대시보드 통합
- ExpiryChecker 자체 변경

---

## 6. 회귀 테스트 케이스

| # | 시나리오 | 검증 |
|---|---|---|
| 1 | 02시 만료 1개, 새벽 3시 BGF 입력 | slot_2am.matched=1, ontime_rate=100% |
| 2 | 14시 만료 1개, 15시 BGF 입력 | slot_2pm.matched=1 |
| 3 | 02시 만료 추적 / BGF 입력 없음 | slot_2am.tracking_only=1, match_rate=0 |
| 4 | 14시 만료 추적 / 새벽 1시 BGF 입력 (전날 슬롯) | slot_2pm 매칭 성공 (00~01도 2차 윈도우) |
| 5 | tracking base에 consumed 포함 | consumed도 검증 대상 (사각지대 해소) |

---

## 7. 단계

| # | 작업 |
|---|---|
| 1 | `waste_verification_reporter.py`: tracking base 쿼리 `status != 'active'` 변경 |
| 2 | `waste_verification_service.py`: `verify_date_by_slot()` 메서드 추가 |
| 3 | 슬롯 분류 헬퍼 (`_classify_slot(cre_ymdhms) -> 'slot_2am'/'slot_2pm'/'unclassified'`) |
| 4 | 회귀 테스트 5개 |
| 5 | pytest 통과 |
| 6 | 04-07 데이터로 수동 재실행 (4매장) |
| 7 | 이슈체인 [WATCHING] + 시도 1 |
| 8 | 커밋 + 푸시 (scheduler-auto-reload로 자동 적용) |

---

## 8. 성공 조건

- [ ] verify_date_by_slot이 4매장 04-07 데이터에서 정상 동작
- [ ] 46513 8801771034445 사건 케이스: slot_2pm.tracking_only로 잡힘 (사각지대 해소 확인)
- [ ] 회귀 테스트 5/5 통과
- [ ] 슬롯별 추적 정확도 측정 가능 (1차 vs 2차 분리)

---

## 9. 리스크

- **cre_ymdhms 14자리 외 형식**: NULL/길이 다른 값 → 안전한 파싱 (try/except, 무효는 unclassified)
- **타임존**: BGF는 KST 기준 → cre_ymdhms도 KST로 가정 (시스템과 일치)
- **0건 슬롯**: tracking_base=0이면 match_rate 계산 0으로 디폴트

---

## 10. 다음 단계

`/pdca design waste-verification-slot-based`
