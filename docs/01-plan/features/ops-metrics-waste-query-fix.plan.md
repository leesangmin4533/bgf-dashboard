# Plan: ops_metrics waste_rate 쿼리 버그 수정 (ops-metrics-waste-query-fix)

> 작성일: 2026-04-07
> 상태: Plan
> 이슈체인: scheduling.md#ops-metrics-waste-rate-mid-cd-컬럼-부재
> 마일스톤 기여: **K2 (폐기율)** — HIGH (NO_DATA 원인 해소)
> 발견 경로: claude-auto-respond 분석 보고서 (2026-04-07)

---

## 1. 문제 정의

### 현상
매일 23:55 `OpsMetricsCollector._waste_rate()`가 4개 매장 전부 실패:

```
2026-04-06 23:55:01 WARNING [OpsMetrics] 46513 waste_rate 실패: no such column: mid_cd
2026-04-06 23:55:01 WARNING [OpsMetrics] 46704 waste_rate 실패: no such column: mid_cd
2026-04-06 23:55:01 WARNING [OpsMetrics] 47863 waste_rate 실패: no such column: mid_cd
2026-04-06 23:55:01 WARNING [OpsMetrics] 49965 waste_rate 실패: no such column: mid_cd
```

### 근본 원인
`src/analysis/ops_metrics.py:150-157` `_waste_rate()` 쿼리가 `waste_slip_items`에서 `mid_cd`로 GROUP BY:

```python
SELECT mid_cd,
       SUM(CASE WHEN chit_date >= date('now', '-7 days') THEN qty ELSE 0 END) as waste_7d,
       SUM(qty) as waste_30d
FROM waste_slip_items
WHERE chit_date >= date('now', '-30 days')
GROUP BY mid_cd
```

그러나 `waste_slip_items` 스키마(schema.py:928-948)에는 **`mid_cd` 컬럼이 없고** `large_cd`만 있음:

```
waste_slip_items 컬럼: store_id, chit_date, chit_no, chit_seq, item_cd, item_nm,
                       large_cd, large_nm, qty, wonga_*, maega_*, cust_nm, center_nm, ...
```

→ `OperationalError: no such column: mid_cd` → `except` 블록에서 `{"insufficient_data": True}` 반환 → **K2 마일스톤 NO_DATA 상태 지속**

### 영향
- **K2 KPI 완전 마비**: 2026-04-06 기준 K2 = NO_DATA (milestone_snapshots)
- OpsMetricsCollector 매일 23:55 경고 4회 누적
- 폐기율 기반 이상 감지 불가 → 폐기 급증해도 알림 없음
- DailyChainReport K2 섹션 공백

---

## 2. 목표

### 1차 목표 (쿼리 수정)
`_waste_rate()`가 정상 동작하여 `waste_rate.categories` 배열에 mid_cd별 폐기율 반환

### 2차 목표 (검증)
- K2 KPI가 NO_DATA → ACHIEVED/NOT_MET 중 하나로 판정
- OpsMetricsCollector 경고 로그 소멸

### 비목표
- `waste_slip_items` 수집기(waste_slip_collector) 개편
- K2 공식 자체 변경 (현재: 폐기수량/(판매+폐기) mid_cd별)

---

## 3. 해결 방향 비교

### 옵션 A: products JOIN으로 mid_cd 도출 (권장)
`waste_slip_items.item_cd` → `products.item_cd`로 JOIN → `products.mid_cd` 사용

```sql
SELECT p.mid_cd,
       SUM(CASE WHEN wsi.chit_date >= date('now', '-7 days') THEN wsi.qty ELSE 0 END) as waste_7d,
       SUM(wsi.qty) as waste_30d
FROM waste_slip_items wsi
JOIN common.products p ON wsi.item_cd = p.item_cd
WHERE wsi.chit_date >= date('now', '-30 days')
GROUP BY p.mid_cd
```

**장점**:
- 스키마 변경 없음
- products가 mid_cd 단일 원천(single source of truth)
- daily_sales도 동일 패턴 가능 (현재 daily_sales.mid_cd 컬럼 사용 여부 확인 필요)

**단점**:
- `ATTACH common.db` 필요 (DBRouter.attach_common_with_views 사용)
- products에 없는 item_cd는 누락 (LEFT JOIN으로 `mid_cd IS NULL` 바구니 처리 가능)

### 옵션 B: waste_slip_items에 mid_cd 컬럼 추가
스키마 v75 마이그레이션 + 수집기 수정 + 기존 데이터 백필

**장점**: 쿼리 간단, JOIN 불필요
**단점**: 변경 범위 큼 (스키마+수집기+백필+테스트 전부), 중복 데이터(denormalization)

### 옵션 C: large_cd로 집계
`GROUP BY large_cd`로 변경

**장점**: 1줄 수정
**단점**: K2 정의 변경 (mid → large), 타 지표와 불일치, 대분류는 너무 거친 집계

**→ 옵션 A 채택**

---

## 4. 범위

### 대상 파일
- `src/analysis/ops_metrics.py:134-206` `_waste_rate()` — 쿼리 수정 + ATTACH
- `tests/test_ops_metrics.py` (있는지 확인 필요) — 회귀 테스트 추가
- `docs/05-issues/scheduling.md` — 이슈 등록

### 연관 확인 필요
- `daily_sales.mid_cd` 컬럼이 존재하는가? (현재 쿼리 L167에서 `SELECT mid_cd` 중 — 성공하는지 로그 확인)
  - 동일한 실패가 daily_sales 쿼리에도 있다면 쿼리 2개 모두 수정 필요
- `DBRouter.attach_common_with_views()` 호출 위치

---

## 5. 성공 조건

- [ ] `_waste_rate()`가 4개 매장에서 `{"categories": [...]}` 반환 (insufficient_data 아님)
- [ ] `[OpsMetrics] {store} waste_rate 실패` 로그 소멸
- [ ] 다음 OpsMetricsCollector 실행 시 K2 NO_DATA 탈출
- [ ] 회귀 테스트: products JOIN 성공 케이스 + item_cd mismatch 케이스 2개
- [ ] 이슈체인 `[OPEN] → [WATCHING]` 갱신

---

## 6. 리스크

- **products 커버리지**: waste_slip_items.item_cd 중 products에 없는 코드 비율 확인 필요. 많으면 K2가 왜곡됨
- **daily_sales 쿼리**: 만약 daily_sales.mid_cd 컬럼도 없으면 쿼리 2개 전부 수정 필요 (현재 로그에서 명시적 실패는 없으나 첫 쿼리에서 예외 → 두번째 쿼리 도달 못함)
- **ATTACH 성능**: common.db ATTACH 오버헤드 미미 (이미 다른 모듈에서 사용 중)

---

## 7. 다음 단계

`/pdca design ops-metrics-waste-query-fix` — Design 문서 작성 (products JOIN 상세 + daily_sales 쿼리 확인)
