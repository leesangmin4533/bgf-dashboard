# Plan: bundle-suspect-dynamic-master

## 문제 정의

`BUNDLE_SUSPECT_MID_CDS` (constants.py:262) 가 정적 set 으로 관리되어 **반응형 패치 사이클**에 갇혀 있다. 04-06 ~ 04-08 5단계 패치(fa0e731 → 190b24f) 모두 사고가 난 카테고리만 추가하는 패턴.

### 사건 누적 (data/discussions/20260408-bundle-analysis/정밀분석.md §3)

| # | 커밋 | 일시 | 추가된 카테고리 | 누락된 카테고리 |
|---|---|---|---|---|
| 1 | fa0e731 | 04-06 | 면류(032) 51건 발견 | — |
| 2 | 0eadb89 | 04-06 | direct_api_fetcher or 1 제거 | — |
| 3 | 06a27fb | 04-06 | 519건 NULL 리셋, 15개 카테고리 | **023(햄/소시지)** |
| 4 | 51cd670 | 04-07 | 음/주/과/면 20개 BUNDLE_SUSPECT 신규 도입 | **023, 024, 025** |
| 5 | 190b24f | 04-08 | 023~025 추가 + L3 가드 | (다음 누락 후보 미상) |

각 패치는 직전 사고만 막고, 다음 사고는 다시 발생.

---

## 📊 결정적 증거 (04-08 product_details 실측)

```sql
SELECT mid_cd, COUNT(*) total, SUM(CASE WHEN order_unit_qty>1 THEN 1 ELSE 0 END) bundle_n,
       ROUND(100.0*SUM(CASE WHEN order_unit_qty>1 THEN 1 ELSE 0 END)/COUNT(*),1) pct
FROM product_details pd LEFT JOIN products p USING(item_cd)
GROUP BY mid_cd HAVING COUNT(*)>=5 ORDER BY pct DESC
```

### bundle_pct >= 50% 인 22개 mid 중 현재 BUNDLE_SUSPECT 미포함 (= 잠재 사고 후보)

| mid_cd | bundle_pct | total | bundle_n | unit=1 | 카테고리 | 위험 |
|---|---|---|---|---|---|---|
| **021** | **88.5%** | 130 | 115 | 0 | 냉동식품 | 🔴 |
| **605** | **86.2%** | 65 | 56 | 3 | 하이볼/캔주 | 🔴 |
| **037** | **81.7%** | 82 | 67 | 2 | 위생용품 | 🔴 |
| **044** | **78.8%** | 132 | 104 | 18 | (확인필요) | 🔴 |
| **040** | **77.7%** | 188 | 146 | 19 | (확인필요) | 🔴 |
| **064** | **75.9%** | 29 | 22 | 0 | (확인필요) | 🔴 |
| **072** | **73.4%** | 203 | 149 | 20 | **담배** | 🔴 |
| **073** | **71.8%** | 110 | 79 | 6 | **전자담배** | 🔴 |
| **041** | **67.7%** | 31 | 21 | 3 | (확인필요) | 🟠 |
| **051** | **55.9%** | 34 | 19 | 2 | (확인필요) | 🟠 |
| **900** | **61.5%** | 13 | 8 | 1 | 소모품 | 🟠 |

→ **11개 mid 가 즉시 사고 후보**. 특히 담배(072)/전자담배(073)는 매장 매출 큰 비중.

### 현재 BUNDLE_SUSPECT(190b24f) 인데 bundle_pct < 50% 인 mid (= 오탐 후보)

| mid_cd | bundle_pct | total | unit=1 | 비고 |
|---|---|---|---|---|
| 010 | 34.6% | 26 | 5 | 음료 (null 12개로 통계 약함) |
| 048 | 46.7% | 15 | 1 | 음료 (샘플 적음) |
| 030 | 32.3% | 62 | 0 | 간식 (null 36개로 통계 약함) |
| 050 | 64.9% | 37 | 3 | 50%↑ → 정상 |
| (006 라면 등 추가 확인 필요) |

→ NULL 비율이 높은 카테고리는 통계 신뢰도 낮으므로 동적 산출 시 제외 또는 보수적 처리 필요.

---

## 수정 범위 (가설)

### 변경 후보 파일
1. `src/settings/constants.py` — `BUNDLE_SUSPECT_MID_CDS` 정적 set 제거 또는 fallback 으로만 유지
2. `src/order/order_executor.py` — `_calc_order_result` 가드에서 동적 set 호출
3. `src/order/order_executor.py` — `input_product` (L3) 가드도 동일 호출
4. **신규 모듈** `src/order/bundle_suspect_resolver.py` — 동적 산출 로직 + 캐싱
5. (선택) `src/scheduler/jobs/bundle_master_audit.py` — 매일 정기 점검 + 카톡 리포트

### 변경하지 않는 것
- 1차 가드 메커니즘 (BLOCK 분기 자체) — 검증 끝난 로직
- 알림 채널 (NotificationDispatcher)
- 데이터 수집기

---

## 핵심 설계 원칙 (사전)

### 원칙 1: "동적 + fallback" 이중 안전망
```
order_executor 가드 호출 시:
  1. dynamic resolver 호출 (캐시 5분, 실패 시 fallback)
  2. fallback = 정적 BUNDLE_SUSPECT_MID_CDS (현재 23개)
```
→ DB 장애 시에도 최소 안전 보장.

### 원칙 2: 카테고리 분류 임계값
```
bundle_pct >= 70%: 강한 의심 → 즉시 BLOCK
bundle_pct 50~70%: 약한 의심 → BLOCK + 디버그 로그 추가
bundle_pct < 50%: 비의심 → 가드 미적용 (오탐 방지)
NULL 비율 > 30%: 통계 신뢰도 낮음 → 별도 알림 후 보수적 BLOCK
total < 5: 샘플 부족 → 가드 미적용
```

### 원칙 3: 캐싱 전략
- 동적 산출은 매 상품마다 SQL 실행하면 발주 전체 지연
- 5분 메모리 캐시 + 매일 07:00 daily_job 시작 시 강제 refresh
- product_details 변경 시 자동 무효화는 단순 시간 기반으로 충분

### 원칙 4: 변경 가시성
- daily_job 시작 시 현재 동적 BUNDLE_SUSPECT set 을 INFO 로그
- 추가/삭제된 mid 가 있으면 카톡 리포트
- "오늘부터 mid=072(담배)도 가드 적용 시작" 같은 알림

---

## Design 단계 결정 필요 항목

| # | 결정 항목 | 옵션 |
|---|---|---|
| 1 | bundle_pct 임계값 | 70%/50% 이중 vs 60% 단일 vs ML 분류 |
| 2 | NULL 비율 처리 | 통계 신뢰도 낮음으로 제외 vs 보수적 BLOCK vs 별도 카테고리 |
| 3 | 캐시 만료 시간 | 5분 vs 1시간 vs daily_job 1회 |
| 4 | resolver 위치 | infrastructure (DB I/O) vs domain (정적 입력만) |
| 5 | 정적 fallback 운명 | 영구 유지 vs 첫 검증 후 제거 vs 운영자 토글 |
| 6 | 정기 점검 잡 | daily_job 통합 vs 별도 스케줄 vs 옵션 |

→ Design 단계 또는 토론(/discuss) 으로 확정.

---

## 검증 계획 (Check 단계 사전)

### 회귀 방지
- 현재 BLOCK 발사 중인 22개 mid (51cd670 + 190b24f) 는 전부 동적 set 에도 포함되어야 함
- 단위 테스트: 각 mid 에 대해 통과/차단 결과가 정적 vs 동적에서 일치 확인

### 신규 효과
- bundle_pct ≥ 70% 미포함 11개 mid 추가 → **04-09 ~ 04-15 1주 모니터링**:
  - 신규 BLOCK 발생 건수
  - 카테고리별 발주 변화 (담배/전자담배 정상 발주는 unit>1 이라 영향 없어야 함)
  - 회귀 알림 (false positive)

### 통계 모니터링
- daily_job 종료 시 동적 BUNDLE_SUSPECT 크기 + 변화량 카톡 리포트

### Match Rate 목표
- 90% (회귀 0건 + 11개 추가 mid 모두 정상 가드 작동)

---

## 기여 KPI

- K3 (발주 실패율): 직접 — 신규 카테고리 사고 사전 차단
- K2 (폐기율): 간접 — 과발주로 인한 폐기 감소

---

## 관련 이슈

- 1차 발견: order-execution#product_details-order_unit_qty-불일치 (fa0e731~06a27fb)
- 2차 발견: order-execution#order-unit-qty-fallback-v2 (51cd670)
- 3차 발견: order-execution#bundle-guard-bypass-49965 (190b24f, [WATCHING])
- 본 메타: order-execution#bundle-suspect-dynamic-master

## 자료 참조

- 정밀분석: data/discussions/20260408-bundle-analysis/정밀분석.md
- 실측 SQL 결과: 위 §"결정적 증거" 표

## Issue-Chain
order-execution#bundle-suspect-dynamic-master
