# 폐기 추적 이슈 체인

> 최종 갱신: 2026-04-05
> 현재 상태: confirmed_orders + delivery_match 기반 (9aca17d)

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
  - [ ] D+2 첫 정확한 알림 확인 — 001~003 (예정: 04-07, 스케줄: expiry-d2-alert-verify)
  - [ ] Gap 분석 (예정: 04-07, 스케줄: expiry-tracking-gap-analysis)
  - [ ] 1주일 운영 후 matched/unmatched 비율 (예정: 04-12, 스케줄: expiry-1week-match-ratio)

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
- [ ] 오늘(04-06) 20:30 재매칭 로그 확인 (스케줄: delivery-rematch-verify)
- [ ] 04-07 09:00 expiry-tracking-gap-analysis에서 matched 비율 확인

---

## [WATCHING] 과도기 알림 누락 가능성 (04-05 ~)

**문제**: OT/receiving_history 폴백 제거로 FR-03이 배치 못 만든 상품의 알림 누락 가능
**영향**: D~D+1 기간 일부 상품
**설계 의도**: 기존 동작을 깨지 않으면서 새 구조로 전환 (graceful degradation)
**연관**: → expiry-tracking.md#remaining_qty-미갱신

### 대응: 기존 배치 생성 폴백 유지 (9aca17d, 04-05)
- **왜**: FR-03 + receiving_collector가 계속 동작하므로 대부분 커버
- **결과**: 4/7 확인 예정
- 검증:
  - [ ] 4/7 Gap 분석에서 누락 건수 확인
  - [ ] 누락 0건 확인 시 [RESOLVED]로 전환

---

## [PLANNED] 폐기 알림 OT 폴백 완전 제거 (P3)

**목표**: confirmed_orders + delivery_match 기반 안정화 확인 후 레거시 OT/receiving_history 폴백 코드 제거
**동기**: 현재 FR-03 배치 생성에 OT 폴백이 남아있어 코드 복잡도 증가. 입고 기반 전환(04-02) 완료 후 과도기 코드 정리 필요
**선행조건**: [WATCHING] 과도기 알림 누락 → [RESOLVED] 전환 후
**예상 영향**: alert/expiry_checker.py, collectors/receiving_collector.py
