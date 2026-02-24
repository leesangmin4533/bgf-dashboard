# 미입고 계산 방식 전환 가이드

## 빠른 시작

### 현재 상태 확인

```python
from src.config.constants import USE_SIMPLIFIED_PENDING, PENDING_LOOKBACK_DAYS

print(f"현재 방식: {'단순화' if USE_SIMPLIFIED_PENDING else '복잡(기본)'}")
print(f"조회 기간: {PENDING_LOOKBACK_DAYS}일")
```

### 단순화 방식으로 전환

**1. 설정 파일 수정**

```bash
# 파일: bgf_auto/src/config/constants.py
USE_SIMPLIFIED_PENDING = True   # False → True 변경
```

**2. 시스템 재시작**

```bash
# 스케줄러 재시작
python run_scheduler.py --now
```

**3. 로그 확인**

```bash
# 미입고 디버그 로그
cat data/logs/pending_debug_YYYY-MM-DD.txt

# 시스템 로그
tail -f logs/bgf_system_YYYY-MM-DD.log | grep "미입고"
```

### 기존 방식으로 롤백

```python
# constants.py
USE_SIMPLIFIED_PENDING = False  # 즉시 복귀
```

---

## 두 방식 비교

| 특성 | 복잡한 방식 (기본) | 단순화 방식 |
|------|------------------|-----------|
| **메서드** | `_calculate_pending_complex()` | `_calculate_pending_simplified()` |
| **알고리즘** | 전체 이력 집계 (7~14개 날짜) | 최근 1건만 확인 (3일 내) |
| **교차날짜 보정** | ✅ 있음 | ❌ 없음 |
| **코드 라인** | ~50줄 | ~20줄 |
| **성능** | 기준 | 10-20% 빠름 |
| **정확도** | 높음 (교차날짜 처리) | 중간 (단순 차감) |
| **유지보수** | 어려움 | 쉬움 |

---

## 파라미터 조정

### 조회 기간 변경

```python
# constants.py
PENDING_LOOKBACK_DAYS = 3  # 기본값

# 더 짧게 (빠르지만 누락 위험)
PENDING_LOOKBACK_DAYS = 2

# 더 길게 (느리지만 안전)
PENDING_LOOKBACK_DAYS = 5
```

**권장값**:
- 일반 상품: 3일
- 신선식품: 2일 (빠른 회전)
- 저회전 상품: 5일

---

## 검증 절차

### Step 1: 단위 테스트 실행

```bash
pytest tests/test_pending_simplified.py -v
```

**기대 결과**: 12/12 통과 ✅

### Step 2: 통합 테스트 (소량)

```bash
# 10개 상품으로 미입고 조회
python scripts/run_auto_order.py --preview --max-items 10
```

**확인 사항**:
- 미입고 수량이 합리적인가?
- 에러 로그 없는가?
- 발주량이 정상인가?

### Step 3: 전체 플로우 테스트

```bash
# 전체 플로우 (발주 제외)
python scripts/run_full_flow.py --no-collect --max-items 30
```

**확인 사항**:
- 발주 목록 생성 정상
- 미입고 차감 정상
- 로그 출력 정상

### Step 4: 실제 발주 테스트 (주의!)

```bash
# 소량 실제 발주 (3개)
python scripts/run_auto_order.py --run --max-items 3
```

**⚠️ 주의**: 실제 발주가 실행됩니다!

---

## 모니터링 지표

### 1. 미입고 정확도

**로그 확인**:
```bash
grep "미입고차이" logs/bgf_system_*.log
```

**정상 기준**:
- 차이 발생률 < 10%
- 평균 차이 < 5개

### 2. 발주 성능

**DB 쿼리**:
```sql
-- 최근 7일 발주 통계
SELECT
    DATE(ordered_at) as date,
    COUNT(*) as order_count,
    AVG(order_qty) as avg_qty,
    AVG(pending_qty) as avg_pending
FROM order_tracking
WHERE ordered_at >= DATE('now', '-7 days')
GROUP BY DATE(ordered_at);
```

**정상 기준**:
- 발주 건수: 기존 대비 ±10%
- 평균 발주량: 기존 대비 ±15%

### 3. 품절/폐기율

**DB 쿼리**:
```sql
-- 품절 발생 (판매량 > 재고+미입고)
SELECT COUNT(*) as stockout_count
FROM daily_sales
WHERE sale_qty > 0
  AND sale_qty >= stock_qty + pending_qty
  AND sales_date >= DATE('now', '-7 days');

-- 폐기 발생
SELECT SUM(disuse_qty) as total_waste
FROM daily_sales
WHERE disuse_qty > 0
  AND sales_date >= DATE('now', '-7 days');
```

**정상 기준**:
- 품절 건수: 기존 대비 +5% 이내
- 폐기량: 기존 대비 +10% 이내

---

## 문제 해결

### Q1: 미입고가 0으로 나옴

**원인**: 조회 기간(3일) 내 발주 없음

**해결**:
```python
# constants.py
PENDING_LOOKBACK_DAYS = 5  # 3 → 5일로 확대
```

### Q2: 중복 발주 발생

**원인**: 최근 발주만 봐서 과거 발주 누락

**해결**:
```python
# 기존 방식으로 롤백
USE_SIMPLIFIED_PENDING = False
```

### Q3: 교차날짜 패턴 오류

**증상**:
```
2월 7일: 발주 10개, 입고 0
2월 6일: 발주 0, 입고 10
→ 단순화 방식: 미입고 10개 (잘못됨)
→ 복잡한 방식: 미입고 0개 (정확)
```

**해결**: 기존 방식 유지 또는 lookback_days 조정

---

## A/B 테스트 방법

### 상품별 분할 (50%)

```python
# order_prep_collector.py::collect_for_item()
# Line ~701 수정

# 해시 기반 분할
import hashlib
use_simplified = int(hashlib.md5(item_cd.encode()).hexdigest(), 16) % 2 == 0

if use_simplified:
    pending_qty = self._calculate_pending_simplified(history, order_unit_qty)
    logger.info(f"[A/B] {item_cd}: 단순화 방식")
else:
    pending_qty, pending_detail = self._calculate_pending_complex(history, order_unit_qty, item_cd)
    logger.info(f"[A/B] {item_cd}: 복잡한 방식")
```

### 로그 분석

```bash
# A그룹 (단순화)
grep "\[A/B\].*단순화" logs/bgf_system_*.log | wc -l

# B그룹 (복잡)
grep "\[A/B\].*복잡" logs/bgf_system_*.log | wc -l
```

---

## 체크리스트

### 전환 전 확인

- [ ] 단위 테스트 통과 (`pytest tests/test_pending_simplified.py`)
- [ ] 통합 테스트 정상 (`--preview --max-items 10`)
- [ ] 백업 완료 (DB: `data/bgf_sales.db`)
- [ ] 롤백 계획 수립
- [ ] 모니터링 도구 준비

### 전환 후 모니터링 (1주일)

- [ ] 일일 미입고 정확도 확인 (`data/logs/pending_debug_*.txt`)
- [ ] 발주 건수/수량 추이 확인 (DB 쿼리)
- [ ] 품절 발생 여부 확인 (카카오 알림)
- [ ] 폐기량 추이 확인 (`waste_report.py`)
- [ ] 성능 개선 확인 (로그 타임스탬프)

### 안정화 후 정리 (2주 후)

- [ ] 지표 분석 리포트 작성
- [ ] 복잡한 방식 삭제 여부 결정
- [ ] 디버그 로그 정리
- [ ] 문서 업데이트
- [ ] 팀 공유

---

## 참조

- **구현 요약**: `/bbb/IMPLEMENTATION_SUMMARY.md`
- **상세 가이드**: `bgf_auto/.claude/skills/bgf-order-flow.md`
- **테스트 코드**: `bgf_auto/tests/test_pending_simplified.py`
- **핵심 로직**: `bgf_auto/src/collectors/order_prep_collector.py`
- **설정 파일**: `bgf_auto/src/config/constants.py`

---

**마지막 업데이트**: 2026-02-07
**작성자**: Claude Code (Sonnet 4.5)
