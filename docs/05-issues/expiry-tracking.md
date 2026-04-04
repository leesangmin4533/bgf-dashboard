# 폐기 추적 이슈 체인

> 최종 갱신: 2026-04-05
> 현재 상태: confirmed_orders + delivery_match 기반 (9aca17d)

---

## [RESOLVED] remaining_qty 미갱신 → 입고 기반 재설계 (2026-03-29 ~ 04-05)

**문제**: order_tracking.remaining_qty가 판매 후에도 안 줄어듦
**영향**: 이미 팔린 상품에 폐기 알림 발송 (동양점 14시 12건→1건이 정상)

### 시도 1: update_receiving remaining_qty 덮어쓰기 수정 (599ac29, 03-29)
- 원인: 입고 확인 시 remaining_qty를 receiving_qty로 리셋 → FR-01 FIFO 차감 무효화
- 수정: actual_receiving_qty만 기록, remaining_qty는 FR-01만 관리
- **결과**: 덮어쓰기는 해결했지만, FR-01 자체가 sale_qty_diff=0이면 차감 안 하는 문제 남음 ✗

### 시도 2: realtime_inventory 기준 일괄 보정 (3c22b9a, 03-30)
- 수정: arrived 과다분 FIFO 차감 (4매장 ~1400개)
- **결과**: 일회성 스크립트. 매일 자동 보정 안 됨 → 다시 drift ✗

### 시도 3: inventory_batches 기반 전환 (d5cc02b, 03-30)
- 수정: expiry_checker에 batches 우선 조회 추가
- **결과**: expiry_date 99.9%가 date-only(10자) → length>10 필터로 전부 스킵 ✗
- 후속: date-only 파싱 + shelf_life_hours 시간 보충 (68164b9)

### 시도 4: OT 교차검증 패치 (52d24ae, 03-31)
- 수정: OT remaining_qty > 0이지만 batches+stock으로 이미 팔린 것 걸러냄
- **결과**: 12건→1건 개선. 하지만 패치일 뿐 근본 해결 아님 ✗

### 시도 5: receiving_history 1순위 전환 (442b1d8, 04-02)
- 동기: 수동 발주 상품이 OT에 expiry_time='' → 알림 누락 (46513 04-02 14시 3건)
- 수정: receiving_history에서 입고일+배송차수→폐기시간 역산
- **결과**: 수동 발주 감지 OK ✓, 하지만:
  - 소진된 배치 구분 불가 → FIFO 교차검증 패치 추가 (269c426) ✗
  - stock_qty 시차 → recv_qty 폴백 추가 (d5733f3) → 수개월 전 데이터 통과 → 폴백 제거 (1e0a1b5) ✗
  - 센터매입 교차오염 → DB 수동 정리 (32b4caf) ✗

### 교훈
- OT remaining_qty 자체가 신뢰 불가 (사용자 변경 미반영 + FR-01 보정 없음)
- receiving_history는 이력만 있고 잔량 추적 없음 → 배치 소진 구분 불가
- stock_qty 기반 보정은 "전체 재고"라 발주건별 매핑 불가

### 해결: confirmed_orders + delivery_match (9aca17d, 04-05)
- 10:30 pending_sync에서 BGF 확정 수량 스냅샷 (사용자 수정 반영)
- 20:30/07:00 입고 수집 후 스냅샷과 매칭 → 입고 확인된 것만 배치 생성
- expiry_checker: OT/receiving_history 폴백 제거 → batches only
- **검증 예정**: 4/7 Gap 분석 (스케줄 태스크 expiry-tracking-gap-analysis)

---

## [OPEN] 과도기 알림 누락 가능성 (2026-04-05 ~)

**문제**: OT/receiving_history 폴백 제거로 FR-03이 배치 못 만든 상품의 알림 누락 가능
**영향**: D~D+1 기간 일부 상품
**대응**: 기존 FR-03 + receiving_collector 배치 생성이 폴백 역할. 4/7 Gap 분석에서 확인 예정.
