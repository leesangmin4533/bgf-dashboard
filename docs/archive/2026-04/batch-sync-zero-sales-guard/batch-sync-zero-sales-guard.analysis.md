# Gap Analysis: batch-sync-zero-sales-guard

> 분석일: 2026-04-07
> Design: docs/02-design/features/batch-sync-zero-sales-guard.design.md
> **Match Rate: 95% — PASS**

---

## 종합 점수

| 항목 | 결과 |
|---|:---:|
| 가드 SQL (normal_qty 조회) | ✅ |
| 보호 로직 (normal_qty == 0 → skip) | ✅ |
| 부분 보호 (to_consume = min(to_consume, normal_qty)) | ✅ (Design 변형) |
| FIFO 정렬 (임박 후순위) | ✅ |
| 로그 (만료임박 보호 N건) | ✅ |
| 회귀 테스트 5개 | ✅ (5/5) |
| **Match Rate** | **95%** |

---

## Design 항목 검증

### ✅ 1. 가드 SQL
`inventory_batch_repo.py:1118-1131`:
```sql
SELECT COALESCE(SUM(remaining_qty), 0) AS normal_qty
FROM inventory_batches
WHERE ... AND (expiry_date IS NULL OR julianday(expiry_date) - julianday('now') >= 1.0)
```
- Design은 "protected_qty(임박 양)" 조회였으나 구현은 "normal_qty(정상 양)"로 dual 변경
- 의미: 정상 배치 양만큼만 차감 → 임박은 자연 보호

### ✅ 2. 보호 분기 (수정됨)
- **Design**: `protected_qty >= to_consume` 시 skip
- **구현**: `normal_qty == 0` 시 skip + `to_consume = min(to_consume, normal_qty)`
- **변경 이유**: 첫 구현에서 test 4 (1+1, stock=1) 실패 → "정상 배치만큼만 차감" 패턴이 더 정확

### ✅ 3. FIFO 정렬 보강
- 만료 임박 배치를 ORDER BY 끝으로 (`CASE WHEN ... THEN 1 ELSE 0 END`)
- 정상 배치 우선 차감 → 임박 자연 보호

### ✅ 4. 로그
- `protected_skipped` 카운트 추가
- `[BatchSync] {store}: 점검 N건, 보정 N건, consumed N건, 만료임박 보호 N건`

### ✅ 5. 회귀 테스트 5개 (5/5 통과)
1. 정상 판매 → consumed
2. **0판매 + 만료 임박 → 보호** (핵심 회귀)
3. 부분 판매 + 여유 → 1개 consumed
4. 부분 판매 + 일부 임박 → 임박 보호, 정상만 consumed
5. stock=0 + 혼재 → 정상 consumed, 임박 보호

---

## Gap 목록

### Missing
없음.

### Changed (Design ≠ 구현)
| 항목 | Design | 구현 | 영향 |
|---|---|---|---|
| 보호 변수 | `protected_qty` | `normal_qty` | 동일 의미 (반대 방향), 구현이 더 정확 |
| 부분 보호 | `to_consume -= protected_qty` | `to_consume = min(to_consume, normal_qty)` | 구현이 정상 배치 양 한도로 자연스러움 |

→ 실제 구현이 Design 의도를 더 명확히 표현. **5% 차감은 Design 문서가 코드 상세 알고리즘과 약간 다른 표현 사용**.

### Added
- 첫 구현 실패 → 회귀 테스트가 잡음 → 즉시 수정 (TDD 효과)

---

## 검증 결과

### 자동 테스트
- 5/5 통과 (`pytest tests/test_batch_sync_zero_sales_guard.py`)

### 잔여 라이브
- [ ] scheduler-auto-reload가 자동 적용 (코드 변경 감지 → 재시작)
- [ ] 다음 14:00 ExpiryChecker가 0판매 만료 상품을 폐기 후보로 잡음
- [ ] BatchSync 로그에 "만료임박 보호 N건" 출현 확인

---

## 결론

**Match Rate 95% — PASS.** Design의 핵심 의도가 모두 구현됨. Cosmetic gap 1건(변수명/표현)만 있고 기능적 영향 없음. TDD로 첫 구현 실수를 즉시 잡음.

## 다음 단계
`/pdca report batch-sync-zero-sales-guard`
