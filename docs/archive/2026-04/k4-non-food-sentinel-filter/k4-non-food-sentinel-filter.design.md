# Design: K4 식품 전용 + 임계값 재정의 (k4-non-food-sentinel-filter)

> 작성일: 2026-04-07
> 상태: Design
> Plan: docs/01-plan/features/k4-non-food-sentinel-filter.plan.md
> 이슈체인: scheduling.md#k4-expiry-time-mismatch-31일-not-met

---

## 1. 설계 개요

`check_expiry_time_mismatch` 1개 메서드만 변경. common.products JOIN으로 mid_cd 도출 후 식품 필터 + 임계값 7일 적용.

---

## 2. Design 결정

### 결정 1: 식품 카테고리 = `('001','002','003','004','005','012')`
- 도시락(001), 주먹밥(002), 김밥(003), 샌드위치(004), 햄버거(005), 빵(012)
- 모두 유통기한 1~3일짜리 → K4 의도(폐기 정합성)와 일치
- 013 유제품, 026 반찬은 PerishableStrategy이지만 유통기한 길어 mismatch 노이즈 ↑ → 1차 범위 제외

### 결정 2: 임계값 1일 → 7일
- 식품 BGF 표기 오차 + 시각 처리 차이(02:00 만료의 날짜 비교 등) 흡수
- 1주 운영 후 재조정 가능 (constants 분리는 과한 추상화 → 매직 넘버로 두고 주석)

### 결정 3: products JOIN 패턴
- 기존 ops_metrics waste_rate 수정 패턴 재사용 (`get_store_connection_with_common` + `JOIN common.products`)
- INNER JOIN: products에 없는 item_cd는 자동 제외 (어차피 식품 필터링 대상 아니므로 영향 없음)

### 결정 4: API 시그니처 / 반환값 동일 유지
- `data_integrity_service` 호출부 무수정
- `action_proposal_service:92` `expiry_time_mismatch` 핸들러도 무수정
- 반환 dict 구조(`check_name`, `status`, `count`, `details`) 유지

### 결정 5: 회귀 테스트 3개
1. **정상**: 식품(002 주먹밥) 10일 차이 → mismatch 카운트
2. **임계 통과**: 식품(001) 5일 차이 → 카운트 안 함
3. **비식품 제외**: 비식품(072 담배) 1000일 차이 → 카운트 안 함

---

## 3. 변경 코드

### Before (`integrity_check_repo.py:249-288`)
```python
def check_expiry_time_mismatch(self, store_id: str) -> Dict[str, Any]:
    conn = self._get_conn()
    try:
        cursor = conn.cursor()
        _mismatch_sql = """
            FROM order_tracking ot
            JOIN inventory_batches ib
                ON ot.item_cd = ib.item_cd AND ot.store_id = ib.store_id
            WHERE ot.store_id = ?
              AND ot.status NOT IN ('expired', 'disposed', 'cancelled')
              AND ib.status = 'active' AND ib.remaining_qty > 0
              AND ABS(julianday(date(ot.expiry_time)) - julianday(ib.expiry_date)) > 1
        """
```

### After
```python
def check_expiry_time_mismatch(self, store_id: str) -> Dict[str, Any]:
    """Check 3: 식품(001~005, 012) OT vs IB 유통기한 7일 초과 차이

    K4 의도(식품 폐기 정합성)에 맞춰 비식품 제외 + 임계값 1→7일 완화.
    (k4-non-food-sentinel-filter, 2026-04-07)
    """
    from src.infrastructure.database.connection import DBRouter
    conn = DBRouter.get_store_connection_with_common(store_id)
    try:
        cursor = conn.cursor()
        _mismatch_sql = """
            FROM order_tracking ot
            JOIN inventory_batches ib
                ON ot.item_cd = ib.item_cd AND ot.store_id = ib.store_id
            JOIN common.products p ON ot.item_cd = p.item_cd
            WHERE ot.store_id = ?
              AND ot.status NOT IN ('expired', 'disposed', 'cancelled')
              AND ib.status = 'active' AND ib.remaining_qty > 0
              AND p.mid_cd IN ('001','002','003','004','005','012')
              AND ABS(julianday(date(ot.expiry_time)) - julianday(ib.expiry_date)) > 7
        """
        # ... 나머지 동일
```

**핵심 diff**: 4곳
1. 커넥션: `self._get_conn()` → `DBRouter.get_store_connection_with_common(store_id)`
2. JOIN 추가: `JOIN common.products p ON ot.item_cd = p.item_cd`
3. 식품 필터: `AND p.mid_cd IN ('001','002','003','004','005','012')`
4. 임계값: `> 1` → `> 7`

---

## 4. 회귀 테스트

`tests/test_integrity_check_repo_k4.py` (신규)

```python
class TestExpiryTimeMismatchK4Filter:
    def test_food_diff_10days_counted(fake_dbs):
        """식품 002 주먹밥 10일 차이 → mismatch"""
    def test_food_diff_5days_skipped(fake_dbs):
        """식품 001 도시락 5일 차이 → 7일 임계 미만 → 카운트 안 함"""
    def test_nonfood_huge_diff_skipped(fake_dbs):
        """비식품 072 담배 1000일 차이 → 식품 필터로 제외"""
```

---

## 5. 구현 순서

| 순서 | 작업 |
|---|---|
| 1 | `check_expiry_time_mismatch` 4곳 수정 |
| 2 | 회귀 테스트 3개 작성 |
| 3 | pytest 통과 |
| 4 | 4매장 수동 호출 → mismatch count 100~500 범위 확인 |
| 5 | 이슈체인 [WATCHING] 전환 + 시도 1 기록 |
| 6 | 커밋 + 푸시 |

---

## 6. 성공 기준
1. 4매장 수동 mismatch count ≤ 500 (식품만, 7일 초과)
2. 회귀 테스트 3개 통과
3. 다음 milestone_snapshots K4 NOT_MET → ACHIEVED 잠재 (anomaly 기준에 따라)

## 7. 다음 단계
`/pdca do k4-non-food-sentinel-filter`
