# Plan — order-unit-qty-integrity-v2

**Feature**: order-unit-qty-integrity-v2
**Priority**: P1 (실물 과발주 발생 중)
**Created**: 2026-04-07
**Related Issue Chain**: `docs/05-issues/order-execution.md` — [OPEN] product_details order_unit_qty 불일치 → 과발주 (시도 2)
**Triggering Incident**: 2026-04-07 07:00 발주 과발주 3건
- 46513 `8801056251895` 롯데)칠성사이다라임캔355 — PYUN_QTY=3, BGF 해석 3묶음
- 46704 `8801094962104` 코카)스프라이트제로P500 — PYUN_QTY=16, BGF 해석 16묶음
- 미상 매장 `8801094962104` — PYUN_QTY=2 (숨은 건)

---

## 1. 배경 (Why)

이슈 체인 `[OPEN] product_details order_unit_qty 불일치`의 **시도 1**(04-06)이 `direct_api_fetcher.py:140` + `order_prep_collector.py:526/703` 3곳의 `or 1` 폴백을 제거했으나, 같은 파일 **`order_prep_collector.py:795`와 `:959`의 `or 1` 폴백 2곳을 놓쳤다**.

결과: BGF API가 `ORD_UNIT_QTY`를 빈값/0으로 내려주는 상품에서
1. `direct_api_fetcher`가 `None`으로 받음 (올바름)
2. `order_prep_collector._collect_one_item_via_api` (line 959)에서 `api_data.get('order_unit_qty', 1) or 1` → **`1`로 복원**
3. 복원된 `1`이 반환 dict에 실리고 `product_detail_repo.save()`로 **DB에 1 저장** (시도 1의 NULL 리셋 무효화)
4. 발주 시 `auto_order._finalize_order_unit_qty`가 DB 재조회 → 1 → 변화 없음, 조용히 통과
5. **L1 Direct API 경로** (`order_executor._calc_order_result`, line 2351) 선택 — Selenium 그리드 실시간 검증 없음
6. `unit = item.get("order_unit_qty", 1) or 1` → 1 그대로 사용
7. `multiplier = ceil(need / 1) = need`, `actual_qty = need`
8. BGF에 `PYUN_QTY=need, ORD_UNIT_QTY=1` 전송
9. BGF 서버는 **내부 상품 마스터의 실제 입수**(예: 24)를 적용 → `need × 24` 실물 입고 예정

핵심 구조 결함: **L1 Direct API 경로가 "DB=진실" 가정 위에 서 있음**. DB가 오염되면 전 경로가 오염을 신뢰.

## 2. 목표 (What)

**`order_unit_qty` 값에 대한 신뢰성 3중 방어선 구축 + "모르는 값(None)"을 "안전한 1"로 가장하는 모든 경로 차단.**

### DoD
- [ ] `order_prep_collector.py:795, :959` 의 `or 1` 폴백 제거 — None 유지
- [ ] `auto_order._finalize_order_unit_qty` 가 DB NULL/0/1 + 카테고리(음료/주류/과자 등 묶음 우세) 조합을 **발주 차단 사유**로 인식
- [ ] `_calc_order_result` (L1 Direct API)가 `unit <= 1` + 의심 카테고리 조합 시 **발주 거부 + 카톡 알림**
- [ ] 음료(010,039,043,045,048) 포함 **의심 상품 전수 NULL 리셋** 후 재수집
- [ ] 신규 가드: `product_details.order_unit_qty` 가 NULL인 상품은 **L1/L2 경로에서 자동 스킵**, L3 Selenium 강제 (그리드에서 실시간 값 확보)
- [ ] 유닛 테스트로 "None 폴백 금지" 회귀 방지 — 이번과 같은 수정 누락 재발 차단

### 비목표 (Out of Scope)
- BGF API가 왜 특정 상품에 빈값을 내려주는가 (BGF 내부 문제, 우리 쪽에서 해결 불가)
- `manual_order_detector.add_batch` 버그 (별 feature)
- 숨은 3번째 매장 과발주 1건의 실물 처리 (사용자 수동)

## 3. 범위 (Scope)

### 포함 (4레이어 전부 방어)

| # | 레이어 | 파일 | 수정 |
|---|---|---|---|
| L1 | 수집 | `src/collectors/order_prep_collector.py:795` | `or 1` → None 유지 |
| L2 | 수집 | `src/collectors/order_prep_collector.py:959` | `or 1` → None 유지 |
| L3 | 수집 | `src/collectors/order_prep_collector.py:943` | CUT 상품 `order_unit_qty: 1` → 유지 (CUT은 발주 안 되므로 OK, 재검토) |
| L4 | 발주준비 | `src/order/auto_order.py:2078` (`_finalize_order_unit_qty`) | NULL/0/1 + 의심카테고리 → 차단 플래그 |
| L5 | 발주실행 | `src/order/order_executor.py:2364` (`_calc_order_result`) | 차단 플래그 체크, 거부 + 알림 |
| L6 | DB | 음료 + 주류 + 과자 오염 상품 전수 NULL 리셋 |
| L7 | 가드 | 신규 유닛 테스트 (회귀 방지) |
| L8 | 모니터 | 일일 `DataIntegrityService` 에 "order_unit_qty=1 의심 상품 개수" 체크 추가 |

### 제외
- BGF API 재시도 로직 (빈값 반환 상품 재조회)
- 카테고리별 표준 입수 추정 (수량 예측은 위험)

## 4. 접근 (4가지 방어선)

### 방어선 1: 수집 폴백 제거 (시도 1의 미완성 부분 완성)
`order_prep_collector.py:795, 959`의 `or 1` 제거. `order_unit_qty`가 `None`이면 하류 로직(`_calculate_pending_*`)에 **명시적 예외** — "입수 미확정 상품은 미입고 계산 스킵, 경고 로그".

### 방어선 2: `_finalize_order_unit_qty` 강화
현재:
```python
unit = int(row[1] or 1) if row[1] else 1  # NULL → 1 폴백
```
수정:
```python
raw = row[1]  # DB 원본
if raw is None or raw <= 0:
    item["_unit_qty_missing"] = True  # 플래그만 세팅, 폴백 금지
    item["order_unit_qty"] = None
elif raw == 1:
    # 의심 카테고리면 차단, 아니면 신뢰
    if _is_bundle_suspect_category(item.get("mid_cd")):
        item["_unit_qty_suspect"] = True
        item["order_unit_qty"] = None
    else:
        item["order_unit_qty"] = 1
else:
    item["order_unit_qty"] = int(raw)
```

`_is_bundle_suspect_category`: 음료(010, 039, 043, 045, 048), 주류(049~053), 과자/제과(014~020, 029, 030), 라면(006, 032) — 묶음 발주가 표준인 카테고리.

### 방어선 3: L1/L2 발주 실행 차단
`_calc_order_result` (line 2351):
```python
if item.get("_unit_qty_missing") or item.get("_unit_qty_suspect"):
    # 차단 + 알림
    logger.error(f"[BLOCK] {item_cd} order_unit_qty 의심 → 발주 거부")
    NotificationDispatcher().send(
        "ORDER BLOCKED: order_unit_qty 오염",
        f"{item_cd} {item_nm} mid={mid_cd} unit=None/1 (의심 카테고리)",
        level="ERROR",
    )
    return {..., "success": False, "message": "unit_qty 오염 차단"}
```

이 시점에서 **차단된 상품은 대안으로 L3 Selenium 경로 재시도** — BGF 그리드에서 실시간 값 확보하여 발주.

### 방어선 4: DB 리셋 + 재수집
이번 사고 직접 원인 상품 + 음료 카테고리 전체 + 시도 1에서 리셋됐던 면류/주류 중 현재 `order_unit_qty=1`로 돌아온 상품:
```sql
UPDATE product_details SET order_unit_qty = NULL
WHERE (order_unit_qty IS NULL OR order_unit_qty <= 1)
  AND small_nm IN ('일반탄산음료','이온음료','커피음료','기능성음료','생수','주스','우유','라면','맥주','소주','위스키','와인');
```
이후 `BulkCollect`(11:00) 시 재수집. 방어선 1로 인해 `None`이면 `None` 유지.

## 5. 대안 검토

| 대안 | 장점 | 단점 | 채택 |
|---|---|---|:---:|
| A. **현재안 (4방어선)** | 수집부터 발주까지 완전 차단 + DB 리셋 + 가드 | 구현량 중간 | ✅ |
| B. 방어선 2+3만 (DB 수정 없음) | 빠른 수정 | 기존 오염 DB는 그대로, 다음 발주까지 과발주 지속 가능 | ❌ |
| C. L1 Direct API 경로 전면 폐지, L3 Selenium만 사용 | 가장 안전 | 발주 속도 5~10배 느려짐, 07:00 스케줄 시간 초과 가능 | ❌ |
| D. 카테고리별 표준 입수 하드코딩 (음료=24 등) | 즉시 해결 | 예외 상품(P500=16 등) 처리 불가, 관리 부담 | ❌ |
| E. BGF API 재시도 + 1시간 간격 갱신 | 근본 원인 공략 | BGF가 언제 채워줄지 모름, 결정론 없음 | ❌ |

**A 채택**: 방어선 1(수집)은 시도 1 완성, 방어선 2+3(발주)은 차단, 방어선 4(DB)는 오염 제거, 가드(테스트)는 재발 방지. 4개가 맞물려야 의미 있음.

## 6. 리스크

| 리스크 | 확률 | 영향 | 대응 |
|---|:---:|:---:|---|
| 차단이 너무 적극적이어서 **정상 낱개 발주 상품**(칠성사이다라임캔 과거 3/10~3/13 정상 6개 발주)도 막힘 | 중 | 중 | `_is_bundle_suspect_category` 화이트리스트 보수적으로. 의심 시 카톡만 보내고 발주 스킵 (다음날 자동 재시도) |
| DB 리셋 후 BGF API가 여전히 빈값 반환 → 상품이 영원히 발주 안 됨 | 중 | 높 | L3 Selenium 강제 경로에서 BGF 그리드 읽기 성공률 확인, 실패 시 카톡 |
| manual_order_detector 버그와 간섭 | 낮 | 중 | 별 feature로 분리 처리, 이번 스코프 아님 |
| `_is_bundle_suspect_category` 오탐 누적으로 카톡 스팸 | 중 | 중 | NotificationDispatcher cooldown 1시간 적용 |

## 7. 마일스톤

| 단계 | 산출물 |
|---|---|
| M1 | 방어선 1: order_prep_collector 795/959 `or 1` 제거 + pending 계산 예외 처리 |
| M2 | 방어선 2: `_finalize_order_unit_qty` `_unit_qty_missing`/`_suspect` 플래그 |
| M3 | 방어선 3: `_calc_order_result` 차단 + L3 Selenium 자동 fallback 시도 |
| M4 | 방어선 4: 오염 상품 DB NULL 리셋 SQL 실행 |
| M5 | 가드: 유닛 테스트 — "None 폴백 금지" 회귀 방지 8~10건 |
| M6 | 모니터: DataIntegrityService에 `SUSPECT_ORDER_UNIT_QTY` 체크 추가 |
| M7 | 이슈 체인 갱신: 시도 2 기록 + 시도 1 교훈 추가 |

## 8. 검증 계획

### 유닛
- [ ] `test_order_prep_collector.py` — `order_unit_qty`가 None/빈값/0인 API 응답에서 반환 dict가 **None 유지**
- [ ] `test_finalize_order_unit_qty.py` — DB NULL + 음료 카테고리 → `_unit_qty_missing=True`, `order_unit_qty=None`
- [ ] `test_calc_order_result.py` — 차단 플래그 설정 시 `success=False`, `NotificationDispatcher.send` 호출
- [ ] `test_no_or_1_regression.py` — AST 기반 검사로 `order_prep_collector.py`에 `or 1` 문자열 0건

### 통합
- [ ] 테스트 DB에 음료 상품 + unit=NULL 데이터 주입 → 발주 플로우 실행 → L1에서 차단, L3 fallback 시도
- [ ] job-health-monitor 와 크로스: 차단 발생 시 `job_runs` 에 failed 기록되지 **않아야** (정상 비즈니스 차단이지 잡 실패 아님)

### 회귀 (2주)
- [ ] 매일 오염 상품 개수 모니터 (DataIntegrityService 카톡 리포트)
- [ ] false block (정상 상품 차단) 발생 시 화이트리스트 조정

### 운영 검증 (다음 발주 = 04-08 07:00)
- [ ] AUDIT 로그에서 `8801094962104`, `8801056251895` **미발주** or `ORD_UNIT_QTY>1` 확인
- [ ] 차단 알림 카톡 수신 확인
- [ ] 정상 음료 상품(order_unit_qty>1) 발주 영향 없음

## 9. 연관 이슈

- **시도 1과의 관계**: 같은 [OPEN] 이슈 블록 내 시도 2로 이어짐. 이슈 체인 `order-execution.md`에 시도 2 블록 추가 + **교훈 섹션**에 "수집 레이어 수정 시 collector 내부 호출 체인 전부 grep 필요" 추가
- `manual_order_detector.add_batch` 메서드 부재 버그 → 별 feature `manual-order-batch-fix`
- 숨은 3번째 매장 과발주 건 → 사용자 수동 대응 후 공유
- job-health-monitor 와 시너지: 다음 발주 차단 사건이 job_runs 에 기록되지 않고(의도대로), 대신 NotificationDispatcher 알림만

## 10. 메타 교훈 (시도 1에서 배운 것)

**시도 1 실패 패턴**: `#patch-on-patch` — 수집 레이어 입구(direct_api_fetcher)만 보고 바로 하류 호출자(order_prep_collector 내부의 2개 다른 경로)를 확인 안 함. 조사 당시 "3단계 잠금 패턴" 이라고 정리했지만 실제는 **5단계**였고 그 중 2단계가 누락.

**재발 방지**: 이번 feature의 구현 단계에서 **`grep -n "or 1"`** 와 **`grep -n "order_unit_qty.*or"`** 를 repo 전체에서 수행하고 **모든 매치를 문서에 기록 후 건건이 의도 확인**.

## 11. 참조

- 로그 증거: `logs/order.log` 2026-04-07 07:14:33 / 07:16:30 / 07:19:39
- 코드: `src/collectors/order_prep_collector.py`, `src/order/auto_order.py`, `src/order/order_executor.py`
- 이슈 체인: `docs/05-issues/order-execution.md` [OPEN]
- 이슈 체인 시도 1 커밋: (이슈 체인에서 확인 필요)
- 시너지: `docs/archive/2026-04/job-health-monitor/` (이번 차단이 job_runs 경로와 겹침)
