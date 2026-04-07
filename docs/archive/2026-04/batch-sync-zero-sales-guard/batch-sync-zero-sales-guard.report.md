# PDCA Report: batch-sync-zero-sales-guard

> 완료일: 2026-04-07
> Match Rate: 95% — PASS

---

## 핵심 요약

`sync_remaining_with_stock`이 stock=0 보고로 active 배치를 consumed 마킹하던 로직에 **만료 24h 이내 배치 보호 가드** 추가. 0판매 + 1일 유통기한 상품의 무인지 폐기를 차단. 5/5 회귀 테스트 통과.

**발견 경로**: 46513 8801771034445 04-07 14:00 폐기 누락 역추적 → BatchSync `sync_remaining_with_stock` L1109 분석

---

## 변경 사항

### 코드
- `src/infrastructure/database/repos/inventory_batch_repo.py` `sync_remaining_with_stock`:
  - normal_qty(만료 24h 이상 배치 합) 조회 추가
  - normal_qty == 0 → skip + protected_skipped 카운트
  - to_consume = min(to_consume, normal_qty) → 정상 배치 양 한도
  - FIFO 정렬에 만료 임박 후순위 (CASE WHEN)
  - 로그에 "만료임박 보호 N건" 추가
- `tests/test_batch_sync_zero_sales_guard.py` (신규) — TestBatchSyncZeroSalesGuard 5개

---

## 사건 인과 사슬 (재현)

```
04-06 06:49 입고 (1개, expiry 04-07 14:00)
   ↓ 0판매 (BGF 응답 누락)
04-07 07:08 BatchSync sync_remaining_with_stock:
   batch_total=1, stock_qty=0, to_consume=1
   ↓ (가드 없음)
   FIFO 차감 → active → consumed ⚠️
   ↓
13:52 ExpiryChecker: active만 검색 → 28505 누락
   ↓
14:00 알림 없음 + 폐기 미수집
```

### 가드 적용 후
```
04-07 07:08 BatchSync:
   batch_total=1, stock_qty=0, to_consume=1
   normal_qty = 0 (만료 12h 남았으므로)
   ↓ skip + protected_skipped += 1
   active 유지 ✅
   ↓
13:52 ExpiryChecker: active 검색 → 28505 잡음
   ↓
14:00 폐기 알림 + waste_slip 수집 정상 ✅
```

---

## 검증

### 자동 테스트 (5/5)
1. 정상 판매 → consumed
2. **0판매 + 만료 임박 → 보호** (핵심 회귀, 사건 케이스)
3. 부분 판매 + 여유 → 1 consumed
4. 부분 판매 + 일부 임박 → 임박 보호
5. stock=0 + 혼재 → 정상 consumed, 임박 보호

### 잔여 라이브
- [ ] scheduler-auto-reload 덕분에 자동 적용 (수동 재시작 불요)
- [ ] 다음 14:00 ExpiryChecker가 0판매 만료 상품 인지 확인
- [ ] BatchSync 로그에 "만료임박 보호 N건" 출현

---

## 교훈

1. **stock=0 ≠ 모두 팔림**: 0판매 + 만료 임박도 stock=0이 될 수 있음. BGF API가 0판매 행을 응답에 포함 안 하기 때문
2. **TDD 효과**: 첫 구현(`protected_qty >= to_consume`)이 test 4에서 실패 → 즉시 더 정확한 패턴(`min(to_consume, normal_qty)`)으로 수정. 회귀 테스트가 알고리즘 결함을 컴파일 단계에서 잡음
3. **부정 vs 긍정 변수**: 보호 양(protected_qty) 대신 정상 양(normal_qty)으로 표현하는 게 FIFO 차감 한도와 자연 일치
4. **scheduler-auto-reload 효과 입증**: 이 fix도 수동 재시작 없이 자동 적용 가능 → 오늘 5건 → 6건 PDCA 작업의 모든 잔여가 자동 보장

---

## 후속 작업 후보
- **option C**: ExpiryChecker가 24h 내 만료된 consumed 배치 회수 (안전망, 비범위)
- **0판매 명시 INSERT**: sales 수집기가 BGF 응답 누락 상품을 0으로 채움 (근본 원인 일부)
- **과거 잘못 consumed된 배치 원복**: 운영 결정 (4매장 일괄 조사 가치)

---

## 관련 문서
- Plan: `docs/01-plan/features/batch-sync-zero-sales-guard.plan.md`
- Design: `docs/02-design/features/batch-sync-zero-sales-guard.design.md`
- Analysis: `docs/03-analysis/batch-sync-zero-sales-guard.analysis.md`
- Issue: `docs/05-issues/expiry-tracking.md#batchsync-0판매-만료-임박-잘못된-consumed-마킹`
