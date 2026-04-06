# Plan: SLOW 주기 판매 상품 ROP 유지 (slow-periodic-rop)

> 작성일: 2026-04-06
> 상태: Plan
> 이슈체인: order-execution.md (신규 등록)
> 마일스톤 기여: K2 (폐기율), K3 (발주 실패율)

---

## 1. 문제 정의

### 현상
46513점 하림)참맛후랑크4입: 2주마다 1회 판매, stock=0인데 AI가 발주 안 함.
BGF "판매분 자동 채우기"에는 보이지만 AI가 스킵.

### 근본 원인
SLOW 분류 상품은 `pred=0, safety=0`으로 설정 → ROP에 의존.
하지만 ROP 스킵 조건이 주기적 판매를 사장상품으로 오판:

```python
# ROP 스킵 (improved_predictor.py:2005-2007)
if data_days < 7 and not _is_new_product and not _has_recent_sales:
    → ROP 생략 → order=0
```

### 해결 방향
SLOW를 두 종류로 세분화:
- **slow_periodic**: 60일 전체에서 2회+ 판매 → stock=0이면 ROP=1
- **slow_dead**: 60일 전체에서 0~1회 판매 → ROP=0

---

## 2. 변경 범위

| # | 파일 | 변경 |
|---|------|------|
| C1 | `improved_predictor.py` | ROP 스킵 조건에 60일 판매 횟수 확인 추가 |
| C2 | `constants.py` | `SLOW_PERIODIC_MIN_SALES = 2` 상수 |

**변경하지 않는 것**:
- DemandClassifier 분류 로직 (SLOW 임계값 15% 유지)
- safety_stock=0 (SLOW의 기본 설정 유지)
- 예측값 pred=0 (SLOW의 기본 예측 유지)
- ROP 외 다른 파이프라인 단계

---

## 3. 상세 로직

### ROP 스킵 조건 변경

```python
# 현재 (line 2005-2007)
if (data_days < 7
        and not _is_new_product
        and not _has_recent_sales):  # 30일 내 판매 0
    → ROP 스킵

# 변경: 60일 내 판매 2회+ 있으면 주기적 판매 → ROP 발동
_has_periodic_sales = False
if not _has_recent_sales:
    # 30일 내 판매 없어도, 60일 내 2회+ 판매면 주기적 판매
    _sales_60d = self._count_sales_days(item_cd, days=60)
    _has_periodic_sales = _sales_60d >= SLOW_PERIODIC_MIN_SALES

if (data_days < 7
        and not _is_new_product
        and not _has_recent_sales
        and not _has_periodic_sales):  # 추가 조건
    → ROP 스킵
else:
    order_qty = 1  # ROP=1
```

---

## 4. 검증

- 하림)참맛후랑크4입: 60일 내 4-5회 판매 → `_has_periodic_sales=True` → ROP=1
- 진짜 사장상품(60일 판매 0): → `_has_periodic_sales=False` → ROP 스킵 유지
