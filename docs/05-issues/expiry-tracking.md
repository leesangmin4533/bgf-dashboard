# 폐기 추적 이슈 체인

> 최종 갱신: 2026-04-07
> 현재 상태: confirmed_orders + delivery_match 기반 (9aca17d) — Gap 분석 완료

---

## [RESOLVED] remaining_qty 미갱신 → 입고 기반 재설계 (03-29 ~ 04-05)

**문제**: order_tracking.remaining_qty가 판매 후에도 안 줄어듦
**영향**: 이미 팔린 상품에 폐기 알림 발송 (동양점 14시 12건→1건이 정상)
**설계 의도**: 유통기한 임박 상품을 정확히 감지하여 폐기 전 알림. 발주 주체(AI/수동) 무관하게 입고된 모든 상품 대상.
**연관**: → order-execution.md#수동발주-OT-미등록

### 시도 1: remaining_qty 덮어쓰기 수정 (599ac29, 03-29)
- **왜**: update_receiving이 입고 시 remaining_qty를 리셋 → FR-01 차감 무효화
- **결과**: ✗ 덮어쓰기는 해결, FR-01 자체가 sale_qty_diff=0이면 차감 안 하는 문제 남음
- **실패 패턴**: #one-shot-fix

### 시도 2: realtime_inventory 일괄 보정 (3c22b9a, 03-30)
- **왜**: arrived 과다분을 수동 FIFO 차감하면 정합성 회복 가능
- **결과**: ✗ 일회성 스크립트. 매일 자동 보정 안 됨 → 다시 drift
- **실패 패턴**: #one-shot-fix

### 시도 3: inventory_batches 기반 전환 (d5cc02b, 03-30)
- **왜**: batches에 expiry_date가 있으니 OT 대신 사용 가능
- **결과**: ✗ expiry_date 99.9%가 date-only → length>10 필터로 전부 스킵. 후속 수정 (68164b9)
- **실패 패턴**: #data-format

### 시도 4: OT 교차검증 패치 (52d24ae, 03-31)
- **왜**: remaining_qty 자체를 못 고치니 batches+stock으로 걸러내기
- **결과**: ✗ 12건→1건 개선. 패치일 뿐 근본 해결 아님
- **실패 패턴**: #patch-on-patch

### 시도 5: receiving_history 1순위 전환 (442b1d8, 04-02)
- **왜**: 수동 발주가 OT에 expiry_time='' → receiving_history에는 입고 데이터 존재
- **결과**: ✗ 수동 발주 감지 OK, 하지만 3가지 후속 문제:
  - 배치 소진 구분 불가 (269c426) #no-consumption-tracking
  - stock_qty 시차 (d5733f3→1e0a1b5) #stale-data
  - 센터매입 교차오염 (32b4caf) #cross-contamination
- **실패 패턴**: #patch-on-patch

### 의도 점검 (5회 실패 후, 04-05 수행)
- [x] 원래 설계 의도를 아직 따르고 있는가? → **아니오.** "발주 건별 잔량 추적"에서 "입고 이력 역산"으로 drift
- [x] 패치가 아닌 구조 재설계가 필요한가? → **예.** 5단계 패치 누적 = #patch-on-patch
- [x] 현재 접근의 근본 가정이 여전히 유효한가? → **아니오.** "OT remaining_qty가 정확하다" 가정 틀림
> 점검 결과: **의도 전환** — "발주 건별 추적" → "입고 확인 기반 배치 추적"

### 교훈
- OT remaining_qty: 사용자 변경 미반영 + FR-01 보정 없음 → 신뢰 불가
- receiving_history: 이력만 있고 잔량 추적 없음 → 배치 소진 구분 불가
- stock_qty 보정: "전체 재고"라 발주건별 매핑 불가
- 폴백 3단계 이상 = #patch-on-patch 징후 → 구조 재설계 검토

### 해결: confirmed_orders + delivery_match (9aca17d, 04-05)
- 검증:
  - [x] 4매장 스냅샷 저장 확인 (완료: 04-05, 314건)
  - [x] D+2 첫 정확한 알림 확인 — 001~003 (완료: 04-07 02:00, 46513 1건·49965 1건 발송, 46704·47863 재고 없어 알림 불필요)
  - [x] Gap 분석 (완료: 04-07, Match Rate 90% — 상세 아래)
  - [ ] 1주일 운영 후 matched/unmatched 비율 (예정: 04-12, 스케줄: expiry-1week-match-ratio)

### Gap 분석 결과 (04-07, expiry-tracking-gap-analysis)

| 검증 포인트 | 결과 | 상세 |
|------------|:----:|------|
| confirmed_orders 스냅샷 저장률 | ✅ PASS | 4매장 × 2일(04-05~06) 전체 저장. 04-05: 314건, 04-06: 921건 |
| delivery_match 매칭률 | ⚠️ PARTIAL | 04-05: 84/72/42/83%. 04-06: 73/12/12/16% (타이밍 이슈 영향, WATCHING) |
| inventory_batches 확정 배치 정확도 | ✅ PASS | expiry_datetime 100% 설정. FOOD_EXPIRY_CONFIG 계산 정확 |
| expiry_checker OT 폴백 제거 | ✅ PASS | line 238-240 확인: inventory_batches → None, OT/receiving_history 완전 제거 |
| D+2 첫 정확한 알림 | ✅ PASS | 46513·49965 02:00 발송 성공. 46704·47863 active 재고 없어 정상 비발송 |
| 과도기 알림 누락 | ✅ PASS | 0건 누락. graceful degradation(FR-03 폴백) 정상 작동 |

**Match Rate: 90%** (delivery_match 매칭률은 WATCHING 이슈 영향, 재설계 자체는 정상 동작)

---

## [WATCHING] delivery_match 타이밍 불일치 → 2차 매칭 실패 (04-06 ~)

**문제**: 2차 매칭(07:00 실행)이 receiving_history 수집(20:30) 이전에 실행 → 매칭률 0%
**영향**: 49965 2차 53건 전부 미매칭, 3매장도 유사. 46513만 1차 84% (유일하게 1차 수집 타이밍 맞음)
**설계 의도**: confirmed_orders와 receiving_history 교차 매칭으로 정확한 배치 생성

### 04-06 실측 데이터

| 매장 | 1차 matched | 1차 미매칭 | 2차 matched | 2차 미매칭 |
|------|:----------:|:---------:|:----------:|:---------:|
| 46513 | 44 | 9 | 3 | 0 |
| 46704 | 15 | 60 | 0 | 12 |
| 47863 | 4 | 29 | 0 | 17 |
| 49965 | 9 | 59 | 0 | 53 |

### 원인
```
04-05 07:00  발주 실행
04-05 08:43  confirmed_orders 스냅샷 저장
04-05 20:30  receiving 수집 (1차 입고) → 1차 매칭 실행 ← 여기서 일부 매칭
04-06 07:00  2차 매칭 실행 ← receiving_date=04-06인데 04-06 receiving 미수집
04-06 20:30  receiving 수집 (04-06 입고) ← 이 시점이면 매칭 가능하지만 이미 07:00에 실패
```

### 원인 상세 (04-06 추가 조사)
- **receiving_qty=0**: 2차 배송 상품이 07:00에는 아직 미도착 → BGF에서 qty=0 반환
- 매칭 로직은 `actual_qty > 0`일 때만 매칭 → qty=0이면 "미입고" 판정
- 수집 자체는 성공 (165건), 하지만 입고 수량이 0

### 시도 1: rematch_unmatched 추가 (커밋 대기, 04-06)
- **왜**: 20:30에는 2차 배송 도착 완료 → receiving_qty > 0 → 재매칭 가능
- **수정**: `delivery_match_flow.py`에 `rematch_unmatched()` 추가, 20:30 wrapper에서 호출
- **결과**: 검증 대기

### 검증
- [x] 오늘(04-06) 20:30 재매칭 로그 확인 (완료: 04-06 20:32, 스케줄: delivery-rematch-verify)
  - rematch 실행 확인: 46704 재매칭=38, 49965 재매칭=42, 46513·47863 재매칭=0
  - **04-05 발주 matched 비율**: 46513 84% ✓, 49965 83% ✓, 46704 72%, 47863 42%
  - 04-06 발주는 아직 배송 미도착으로 낮은 것 정상 (46513 70%, 나머지 12~16%)
  - 46704·47863 낮은 원인: 미입고 항목 없는 게 아니라 receiving_history에 해당 item_cd 자체 없음 → 아직 미배송
  - 47863 상세: 29개 미매칭 중 28개는 receiving_history order_date=04-05에 없음 (미배송)
- [x] 04-07 09:00 expiry-tracking-gap-analysis에서 matched 비율 확인
  - 04-05 발주: 46513=84%, 46704=72%, 47863=42%, 49965=83%
  - 04-06 발주: 46513=73%, 46704=12%, 47863=12%, 49965=16% (2차 배송 지연 정상)
  - rematch_unmatched 20:30 재매칭: 46704 +38, 49965 +42 보완

---

## [RESOLVED] 과도기 알림 누락 가능성 (04-05 ~ 04-07)

**문제**: OT/receiving_history 폴백 제거로 FR-03이 배치 못 만든 상품의 알림 누락 가능
**영향**: D~D+1 기간 일부 상품
**설계 의도**: 기존 동작을 깨지 않으면서 새 구조로 전환 (graceful degradation)
**연관**: → expiry-tracking.md#remaining_qty-미갱신

### 대응: 기존 배치 생성 폴백 유지 (9aca17d, 04-05)
- **왜**: FR-03 + receiving_collector가 계속 동작하므로 대부분 커버
- **결과**: 4/7 확인 예정
- 검증:
  - [x] 4/7 Gap 분석에서 누락 건수 확인 → **0건 누락 확인** (FR-03 폴백 정상 작동)
  - [x] 누락 0건 확인 → **[RESOLVED]로 전환**

---

## [OPEN] collection.py 구문 오류 → 07:00 daily_job 전체 실패 (04-07)

**문제**: `collection.py` line 60의 `with` 블록 구문 오류로 07:00 4개 매장 daily_job 전부 실패 (1.5s 내 종료)
**영향**: 2차 delivery_match 미실행 (07:04 retry에서 자동 복구됨), 발주 실행 지연
**원인**: `expected an indented block after 'with' statement on line 60 (collection.py, line 63)` — 현재 코드는 정상이므로 .pyc 캐시 불일치 가능성
**조치**: 07:04 HealthChecker retry로 복구 완료. 재발 방지를 위해 `__pycache__` 정리 또는 원인 파일 확인 필요
**추적**: 폐기 추적 재설계와 직접 관련 없으나 delivery_match 실행에 영향

---

## [PLANNED] 폐기 알림 OT 폴백 완전 제거 (P3)

**목표**: confirmed_orders + delivery_match 기반 안정화 확인 후 레거시 OT/receiving_history 폴백 코드 제거
**동기**: 현재 FR-03 배치 생성에 OT 폴백이 남아있어 코드 복잡도 증가. 입고 기반 전환(04-02) 완료 후 과도기 코드 정리 필요
**선행조건**: [WATCHING] 과도기 알림 누락 → [RESOLVED] 전환 후
**예상 영향**: alert/expiry_checker.py, collectors/receiving_collector.py
