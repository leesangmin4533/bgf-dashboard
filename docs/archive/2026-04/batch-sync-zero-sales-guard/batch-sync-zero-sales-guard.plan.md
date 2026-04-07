# Plan: BatchSync Zero-Sales 가드 (batch-sync-zero-sales-guard)

> 작성일: 2026-04-07
> 상태: Plan
> 이슈체인: expiry-tracking.md (등록 예정)
> 마일스톤 기여: **K2 (폐기율) + 점주 폐기 알림** — HIGH (P1)
> 발견 경로: 46513 8801771034445 04-07 14:00 폐기 누락 역추적

---

## 1. 문제 정의

### 사건 (2026-04-07 46513)
| 시각 | 이벤트 |
|---|---|
| 04-06 06:49 | 도)한끼만족뉴함박치킨2 1개 입고 (id=28505, expiry=04-07 14:00) |
| 04-06 종일 | **0개 판매** (BGF 응답에 sales row 없음, daily_sales 04-06 미생성) |
| 04-07 07:00 | daily_job collection 시작 |
| 04-07 07:08 | **BatchSync sync_remaining_with_stock**: stock_qty=0 → batch_total(1) > stock(0) → **active → consumed** ⚠️ |
| 04-07 13:52 | ExpiryChecker: active 배치만 검색 → 28505 누락 → "14:00 폐기 대상 없음" |
| 04-07 14:00 | **알림 없음 + 폐기 전표 미수집** (BGF에는 폐기=1 기록) |

### 근본 원인
`src/infrastructure/database/repos/inventory_batch_repo.py:sync_remaining_with_stock` (L1036-1158)

```python
if batch_total <= stock_qty:
    continue
to_consume = batch_total - stock_qty   # 1 - 0 = 1
# ... FIFO 차감 → consumed 마킹
```

**잘못된 가정**: `stock_qty == 0 → 모두 팔린 것`
**실제**: 0판매 + 임박 만료 상품도 stock=0이 될 수 있음 (BGF가 0판매 행을 응답에 포함 안 함 + ExpiryChecker가 미처리한 만료 상품)

→ 결과: **만료 직전 active 배치가 "팔렸다"고 잘못 마킹** → 폐기 인지 실패 → 알림/통계/K2 모두 마비

### 영향 범위
- **0판매 + 1일 유통기한 상품 모두 위험** (도시락 001~005, 빵 012)
- 매장당 매일 0~수개씩 무인지 폐기 가능
- K2 KPI(폐기율) 통계 왜곡
- 점주 폐기 알림 누락 → BGF에서 직접 발견해야 인지

---

## 2. 목표

### 1차
`sync_remaining_with_stock`이 **만료 임박(24h 이내) 배치를 함부로 consumed 마킹하지 못하게** 가드 추가.

### 2차
잘못 consumed된 배치도 ExpiryChecker가 폐기 후보로 회수할 수 있도록 보강 (안전망).

### 3차
같은 패턴이 발생한 과거 사례 일괄 조회 + 원복 (선택, 운영 결정).

### 비목표
- BGF API 응답 형식 변경 요청 (외부 의존)
- daily_sales 0판매 명시 INSERT (별도 작업 후보)
- ExpiryChecker 전체 재설계

---

## 3. 해결 방향 비교

| # | 옵션 | 동작 | 효과 | 비용 |
|---|---|---|---|---|
| **A** | **만료 임박 가드** (권장) | active 배치 중 expiry_date - now < 24h이면 FIFO 차감 보류 | 핵심 케이스 차단 | 1쿼리 + 분기 |
| B | daily_sales 누락 가드 | 해당 상품의 daily_sales row가 0건이면 consumed 보류 | 0판매 누락만 차단 | 추가 쿼리 |
| C | ExpiryChecker가 consumed 회수 | 24h 내 만료 + remaining=0 + 4h 내 consumed → 폐기 후보 | 안전망 | 별도 쿼리 |
| D | 0판매 명시 INSERT | sales 수집기가 BGF 응답 누락 상품 0으로 채움 | 근본 원인 일부 | 수집기 변경 |
| **A+C** | 가드 + 안전망 | A 우선, C 백업 | 신뢰성 ↑ | 중간 |

→ **A+C 채택**. A가 핵심 가드, C는 이중 안전망.

---

## 4. 범위

### 대상 파일
- `src/infrastructure/database/repos/inventory_batch_repo.py:1036-1158` `sync_remaining_with_stock` — 가드 1개 추가
- `src/alert/expiry_checker.py` — 24h 이내 만료 + 최근 4h 내 consumed 배치를 폐기 후보로 회수 (안전망)
- `tests/test_batch_sync_zero_sales_guard.py` (신규) — 회귀 테스트
- `docs/05-issues/expiry-tracking.md` — 이슈 등록

### 비범위
- sales 수집기 0판매 INSERT 로직
- BGF API 응답 형식 조정

---

## 5. 핵심 변경 미리보기

### A. sync_remaining_with_stock 가드
```python
# 변경 전 (L1112-1149)
to_consume = batch_total - stock_qty
adjusted += 1
# ... FIFO 차감

# 변경 후
to_consume = batch_total - stock_qty

# 가드: 만료 24h 이내 active 배치는 차감 대상에서 제외
cursor.execute("""
    SELECT SUM(remaining_qty) as protected_qty
    FROM inventory_batches
    WHERE item_cd = ? AND store_id = ? AND status = ?
      AND remaining_qty > 0
      AND expiry_date IS NOT NULL
      AND julianday(expiry_date) - julianday('now') < 1.0
""", (item_cd, store_id, BATCH_STATUS_ACTIVE))
protected = int(cursor.fetchone()["protected_qty"] or 0)

if protected >= to_consume:
    # 차감 대상이 모두 만료 임박 → 보류 (ExpiryChecker가 처리)
    logger.info(f"[BatchSync] {item_cd} 만료 임박 보호: skip {to_consume}개")
    continue

# 만료 임박분은 남기고 나머지만 차감
to_consume -= protected
# ... 기존 FIFO 차감 (만료 임박 배치는 ORDER BY 끝으로 밀리도록 expiry_date DESC)
```

### C. ExpiryChecker 안전망
```python
# get_alert_items / get_batch_expiring_items에 추가
# 24h 이내 만료된 consumed 배치 중 최근 4h 내 마킹된 것은 폐기 후보로 회수
SELECT * FROM inventory_batches
WHERE expiry_date BETWEEN datetime('now') AND datetime('now','+24 hours')
  AND status = 'consumed'
  AND updated_at > datetime('now','-4 hours')
  AND remaining_qty = 0  -- 잔량 0이지만 만료 임박이면 폐기 의심
```

---

## 6. 회귀 테스트 케이스

| # | 케이스 | 검증 |
|---|---|---|
| 1 | 정상 판매 (stock=0, expiry > 24h) | consumed 마킹 ✅ |
| 2 | **0판매 + 만료 24h 내** (이번 사례) | consumed 보류 ✅ |
| 3 | 부분 판매 (batch=2, stock=1, expiry > 24h) | 1개 consumed |
| 4 | 부분 판매 + 일부 만료 임박 (batch=2 중 1개 24h내) | 만료 임박분 보호, 1개만 consumed |
| 5 | 만료 24h 내 + 다른 배치 정상 | 정상 배치만 consumed |

---

## 7. 단계

| # | 작업 |
|---|---|
| 1 | `sync_remaining_with_stock` 가드 추가 (옵션 A) |
| 2 | `expiry_checker` 안전망 추가 (옵션 C) |
| 3 | 회귀 테스트 5개 작성 |
| 4 | pytest 통과 |
| 5 | 4매장 수동 재현: 임의 active 배치 1개 + stock=0 시나리오 |
| 6 | 이슈체인 등록 + 커밋 + 푸시 |
| 7 | scheduler-auto-reload 덕분에 자동 적용 (수동 재시작 불요) |
| 8 | 다음 14:00 작업에서 효과 검증 |

---

## 8. 성공 조건

- [ ] 가드 추가 후 시뮬레이션: stock=0 + expiry 24h 내 배치는 consumed 안 됨
- [ ] 회귀 테스트 5개 통과
- [ ] 4매장 수동 호출에서 active 배치 부당 consumed 0건
- [ ] 다음 14:00 ExpiryChecker가 0판매 만료 상품을 폐기 후보로 잡음
- [ ] 이슈체인 [WATCHING] 전환 + 검증 일자

---

## 9. 리스크

- **가드가 과보호로 변할 위험**: 정상 판매도 consumed 안 되면 stock 정합성 깨짐 → 24h 임계가 적절. 24h 초과 분은 정상 차감
- **C의 false positive**: 진짜로 팔린 상품도 expiry 24h 내면 폐기 후보로 잡힐 수 있음 → 알림만 보내고 점주가 BGF에서 확인하므로 안전
- **과거 잘못 consumed된 배치 원복**: 비범위 (운영 결정)

---

## 10. 다음 단계

`/pdca design batch-sync-zero-sales-guard` — 가드 SQL/안전망 SQL/테스트 케이스 확정
