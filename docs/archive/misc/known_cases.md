# 발주 이상 재현 케이스 목록

테스트, diagnose_order.py, 트러블슈팅 문서의 공통 기준

## 케이스 목록

| ID | 점포 | 상품 | 날짜 | 원인 체인 | 관련 테스트 | 상태 |
|----|------|------|------|-----------|-------------|------|
| C-01 | 46513 | 8801043022262 | 2026-03-04~06 | Fix B 미적용 → promo C분기 order 0→3 → round A floor=0 → 16 발주 | P-01, R-01, I-01 | ✅ 수정완료 (25f5a6a) |
| C-02 | 47863 | - | 2026-03-07 | Phase 1.2 nav 실패 → manual_order_items 빈 상태 → deduct 미작동 → 수동+예측 별개 발주 | I-06 | ✅ 수정완료 (25f5a6a) |
| C-03 | 전체 | - | 2026-03-05~06 | navigate_to_single_order 대기 없음 → 넥사크로 렌더링 전 0.02초 즉시 실패 → execute_orders 3회 재시도 모두 실패 → 발주 미제출 | - | ✅ 수정완료 |
| C-04 | 전체 | - | 2026-03-08 발견 | order_prep_collector save_monthly_promo() store_id 미전달 → NULL 저장 → ON CONFLICT 매칭 실패 → 행사 데이터 저장 누락 | - | ✅ 수정완료 |

### C-03 상세
- 증상: 발주 미제출 (3개 매장 연속)
- 로그 패턴: `menu_click_failed elapsed=0.00~0.02초`
- 원인: 넥사크로 상단 메뉴 렌더링 전 즉시 클릭 시도
- 수정: navigate_to_single_order() 내부에 최대 10초 폴링 대기 추가 (MENU_WAIT_TIMEOUT=10, MENU_POLL_INTERVAL=0.5)
- 부가 수정: log_timeout_error()에 store_id/session_id 파라미터 추가, OrderExecutor에 store_id 전달
- 참고: execute_orders()의 3회 상위 재시도는 Alert/팝업 정리 목적으로 유지

### C-04 상세
- 증상: promotion_repo UPSERT 에러 반복, 행사 데이터 저장 누락
- 로그 패턴: `ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint`
- 원인: order_prep_collector.py:1092 에서 save_monthly_promo() 호출 시 store_id 미전달 → NULL → SQLite UNIQUE 제약 매칭 불가
- 수정: store_id=self.store_id 인자 추가 (1줄)
- 데이터 정리: NULL 오염 데이터 5,705건 삭제 (46513: 3,686건 / 46704: 1,983건 / 47863: 36건)
- 영향: 행사보정 로직이 오래된 행사 정보로 판단할 수 있었던 잠재 버그

## 파이프라인 테스트 커버리지

리팩토링 Phase 4 사전 검증을 위한 테스트 현황.

### 게이트 테스트 (test_order_pipeline_gates.py — 12개)

| TC-ID | 대상 | 시나리오 | 상태 |
|-------|------|----------|------|
| TC-01A | stock_gate_overstock | stock_days(4.67) < threshold(5) → 비발동 | ✅ PASS |
| TC-01B | stock_gate_overstock | stock_days(5.0) >= threshold(5) → 발동 | ✅ PASS |
| TC-01B+ | overstock→promo | overstock→0 이후 promo 재고부족 시 양수 복원 | ✅ PASS |
| TC-02 | stock_gate_surplus | entry 통과 → surplus(13)>=safety(4) → 차단 | ✅ PASS |
| TC-02반전 | stock_gate_surplus | surplus(6) < safety(10) → 차단 안 됨 | ✅ PASS |
| TC-03 | stock_gate_entry | pending=0 통과 vs pending=5 차단 (산술) | ✅ PASS |
| TC-03실제 | stock_gate_entry | pending 포함 promo 메서드 검증 | ✅ PASS |
| Proposal-1 | OrderProposal | set() 이력 기록 정확성 | ✅ PASS |
| Proposal-2 | OrderProposal | stock_gate_summary() 첫 번째 gate 반환 | ✅ PASS |
| Proposal-3 | OrderProposal | gate 없으면 None | ✅ PASS |
| Proposal-4 | OrderProposal | changed_stages() 필터링 | ✅ PASS |
| (I-01~I-07) | 기존 회귀 | test_order_regression_low_turnover_box.py | ✅ 7개 PASS |

### 정책 보정 테스트 (test_order_pipeline_policy.py — 5개)

| TC-ID | 대상 | 시나리오 | 상태 |
|-------|------|----------|------|
| TC-04 | order_rules→ROP→promo | need=0.05→0 → ROP→1 → promo 증가 | ✅ PASS |
| TC-04반전 | ROP 스킵 | pending>0 → ROP 스킵 | ✅ PASS |
| TC-05A | cap→round floor | cap=18 → round floor=16 (days_cover=2.67) | ✅ PASS |
| TC-05B | cap→round ceil | cap=18 → needs_ceil(0.2) → ceil=32 | ✅ PASS |
| TC-05보충 | cap 미초과 | 12<18 → floor=0+surplus부족 → ceil=16 | ✅ PASS |

### 미커버 분기 (Phase 4에서 추가 예정)

| 분기 | 위치 | 사유 |
|------|------|------|
| A-max_stock | _round_to_order_unit L2143-2184 | 카테고리별 max_stock 분기 (tobacco/beer/soju/ramen/food) |
| C-tobacco | _round_to_order_unit L2208-2210 | 담배 올림 유지 (99% 서비스레벨) |
| D-else | _round_to_order_unit L2213-2224 | 기타 카테고리 내림 우선 |
| food_minimum | _apply_order_rules L2260-2263 | 푸드류 재고 0 최소 1개 |
| friday_boost | _apply_order_rules L2266-2268 | 금요일 주류/담배 15% 부스트 |
| disuse_prev | _apply_order_rules L2271-2273 | 초단기 상품 감량 |

## 케이스 추가 규칙

새 장애 발생 시 아래 형식으로 추가:

- **ID**: C-{순번}
- **원인 체인**: 최대 한 줄로 압축
- **관련 테스트**: 회귀 테스트 케이스 ID
- **상태**: 수정완료 커밋 해시 또는 조사중
