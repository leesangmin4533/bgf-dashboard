# 발주 이상 트러블슈팅 가이드

## 1. 첫 번째로 확인할 것: 실행 버전

발주 이상 신고 시 가장 먼저 해당 날짜의 실행 버전을 확인합니다.

```bash
# [BUILD] 로그에서 커밋 해시 확인
grep "commit" logs/bgf_auto.log | grep "2026-03-07"

# 또는 diagnose_order.py로 한번에 확인
python diagnose_order.py --store 46513 --item 8801043022262 --date 2026-03-07
```

출력 예시 (`[10] 빌드 / 스케줄러` 섹션):
```
  commit     : 7e0acbb
  sched_start: 10:10:22
```

> 수정 커밋 이전 버전이면 이미 알려진 버그일 수 있음 → `docs/known_cases.md` 참조

---

## 2. 원인 체인 빠르게 보기: diagnose_order.py

### 사용법

```bash
# 매장+날짜로 진단
python diagnose_order.py --store 46513 --item 8801043022262 --date 2026-03-07

# 세션 ID로 진단 (로그에서 자동 추출)
python diagnose_order.py --session e690a029 --item 8801043022262
```

### 옵션 설명

| 옵션 | 필수 | 설명 |
|------|------|------|
| `--store` | `--date` 사용 시 필수 | 매장 코드 (예: 46513) |
| `--item` | 필수 | 상품 바코드 |
| `--date` | `--session` 없을 때 필수 | 조회 날짜 (YYYY-MM-DD) |
| `--session` | `--date` 대체 | 세션 ID (로그에서 날짜/매장 자동 추출) |

### 출력 해석

10개 섹션으로 발주 파이프라인 전 구간을 역추적합니다:

| 섹션 | 내용 | 핵심 확인 포인트 |
|------|------|-----------------|
| [1] 상품 기본 정보 | unit, expiry, orderable | unit이 1이 아닌지, orderable 상태 |
| [2] 재고 현황 | stock, pending, available | stock=0인데 available=1이면 stale 의심 |
| [3] 미입고 현황 | 미입고 건수 | pending 반영 여부 |
| [4] 행사 정보 | promo_type, 기간 | 행사 중 과잉발주 여부 |
| [5] 예측 결과 | adj_pred, safety, order_qty | 시스템 계산값 확인 |
| [6] 사전 평가 | decision, daily_avg | SKIP 판정 여부 |
| [7] 실제 발주 | order_qty, source | auto vs manual 구분 |
| [8] 실패 사유 | stop_reason | 발주 차단 원인 |
| [9] 최근 7일 판매 | 판매/재고/폐기 추이 | 수요 패턴 파악 |
| [10] 빌드 정보 | commit, 스케줄러 시작 | 실행 버전 확인 |

### N/A / UNKNOWN / 0 구분

| 표시 | 의미 | 대응 |
|------|------|------|
| `0` | 실제 값이 0 | 정상 (재고 없음, 발주 없음 등) |
| `N/A` | 데이터 자체가 없음 (행 없음) | 수집 누락 또는 해당 없는 항목 |
| `UNKNOWN` | 조회 중 예외 발생 | DB 스키마 불일치, 권한 문제 등 |

---

## 3. 상세 추적: trace_id grep

diagnose_order.py로 대략적인 원인을 파악한 후, 상세 계수/분기를 확인하려면 로그를 직접 검색합니다.

```bash
# trace_id 형식: {store_id}:{item_cd}:{session_id}
grep "46513:8801043022262" logs/prediction.log
```

### 주요 로그 라인 해석

| 키워드 | 위치 | 설명 |
|--------|------|------|
| `NEED` | improved_predictor | need_qty 계산 (adj_pred + safety - stock - pending) |
| `PROMO` | promotion_manager | 행사 보정 분기 (branch=A/B/C, skip 여부) |
| `ROUND` | _round_to_order_unit | 발주단위 올림 (ceil/floor, surplus 체크) |
| `SUBMIT` | order_executor | 최종 발주 제출 (qty, unit, method) |

```bash
# 행사 보정 상세
grep "46513:8801043022262.*PROMO" logs/prediction.log

# 발주단위 올림 상세
grep "46513:8801043022262.*ROUND" logs/prediction.log

# 최종 제출 확인
grep "46513:8801043022262.*SUBMIT" logs/order.log
```

---

## 4. 자주 나오는 원인 패턴

| 패턴 | 증상 | 확인 방법 | 수정 위치 |
|------|------|-----------|-----------|
| promo override | 재고 충분한데 발주 발생 | PROMO 로그 branch=C, skip 없음 | `_apply_promotion_adjustment` Fix B |
| floor=0 1박스 강제 | 소량 수요인데 1박스 발주 | ROUND 로그 floor=0, result=ceil | `_round_to_order_unit` Fix A |
| 수동발주 미수집 | 수동+예측 각각 발주 | manual_order_items 0건 확인 | navigate 재시도 로직 |

### 패턴별 상세

#### promo override (C-01)
- **증상**: 재고 14개, 발주단위 16개인데 16개 발주
- **원인**: 행사 안정기(days_until_end=10)에서 promo 보정이 order_qty를 0→3으로 올림
- **확인**: `[5] 예측 결과`에서 order_qty > 0이고 `[2] 재고`에서 stock >= safety
- **수정**: Fix B가 stock+pending >= promo_avg*factor 시 보정 스킵

#### floor=0 1박스 강제 (C-01 연쇄)
- **증상**: order_qty=3인데 최종 16개 발주 (발주단위=16)
- **원인**: floor=0, surplus 체크 없이 ceil로 올림
- **확인**: ROUND 로그에서 floor=0, result=ceil(16)
- **수정**: Fix A가 floor=0일 때 surplus >= safety 체크 → 발주 취소

#### 수동발주 미수집 (C-02)
- **증상**: 일반탭 수동발주 5개 + 예측발주 10개 = 총 15개 발주
- **원인**: Phase 1.2에서 navigate 실패 → manual_order_items 빈 상태
- **확인**: `[8] 실패 사유`에서 수동발주 관련 오류, 또는 DB에서 manual_order_items 0건
- **수정**: navigate 재시도 + I-06 회귀 테스트

---

## 5. known_cases.md 참조

이미 알려진 장애와 수정 내역은 아래 문서에서 관리합니다:

- [재현 케이스 목록](known_cases.md)

새 장애 발생 시 위 문서에 케이스를 추가하고, 회귀 테스트를 작성하세요.
