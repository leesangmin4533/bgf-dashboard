# Gap Analysis: ops-metrics-waste-query-fix

> 분석일: 2026-04-07
> Design: docs/02-design/features/ops-metrics-waste-query-fix.design.md
> 이슈체인: scheduling.md#ops-metrics-waste-rate-mid-cd-컬럼-부재
> **Match Rate: 98% — PASS**

---

## 종합 점수

| 항목 | 값 |
|------|---|
| Design 항목 수 | 5 |
| 일치 | 5 |
| Gap | 2 (cosmetic) |
| **Match Rate** | **98%** |
| 판정 | **PASS** |

---

## Design 항목 검증

### 1. 커넥션 변경 (get_store_connection_with_common) ✅
- `ops_metrics.py:140` — `conn = DBRouter.get_store_connection_with_common(self.store_id)` — MATCH

### 2. JOIN 쿼리 + GROUP BY p.mid_cd ✅
- `ops_metrics.py:154-162`:
  ```sql
  SELECT p.mid_cd, ...
  FROM waste_slip_items wsi
  JOIN common.products p ON wsi.item_cd = p.item_cd
  WHERE wsi.chit_date >= date('now', '-30 days')
  GROUP BY p.mid_cd
  ```
- MATCH

### 3. 매칭률 경고 (LEFT JOIN + warning) ✅
- `ops_metrics.py:170-187` — LEFT JOIN + 5% 임계치 + `logger.warning("products 미매칭 ...")` — MATCH

### 4. 회귀 테스트 3개 ✅
- `TestWasteRateProductsJoin` in `tests/test_ops_metrics_waste_rate.py`:
  1. `test_waste_rate_joins_products_for_mid_cd`
  2. `test_waste_rate_unmatched_items_trigger_warning`
  3. `test_waste_rate_no_such_column_regression`
- tmp_path file-based DB + ATTACH 패턴으로 Design §4 권장사항 충실 구현
- **3/3 통과** (전체 17/17)

### 5. daily_sales 쿼리 미수정 ✅
- `ops_metrics.py:190-197` 변경 없음 (mid_cd 컬럼 native 존재) — MATCH

---

## Gap 목록

### 🟡 G-1 (Cosmetic) — Design 예시 코드 클래스명 오기
- **Design**: `OpsMetricsCollector` 로 표기
- **실제**: 클래스명 `OpsMetrics` (ops_metrics.py:15)
- **영향**: 없음 (테스트는 실제 클래스로 정상 작성)
- **권장**: Design 문서 정정 (선택사항)

### 🟡 G-2 (Cosmetic) — `wsi_rows COUNT(*)` 컬럼 생략
- **Design**: `COUNT(*) as wsi_rows` 포함
- **구현**: 생략
- **영향**: 없음 (사용처 없는 디버그용 컬럼)

### 🟢 Positive 추가
- tmp_path 파일 DB ATTACH 패턴으로 in-memory 한계 우회 → Design 권장사항 정확히 반영
- 4매장 실데이터 수동 재현 성공 확인

---

## 검증 기준 충족도

| Design §6 성공 기준 | 상태 |
|---|:---:|
| 1. 4매장 categories 반환 | ✅ 수동 재현 완료 |
| 2. `waste_rate 실패` 로그 소멸 | ✅ 코드상 충족 |
| 3. 회귀 테스트 3개 통과 | ✅ 3/3 통과 |
| 4. 매칭률 경고 로그 | ✅ test로 검증 |
| 5. K2 NO_DATA 탈출 | ⏳ 다음 23:55 검증 대기 |

---

## 결론

**Match Rate 98% — PASS.** Design의 모든 핵심 변경이 구현에 정확히 반영됨. Gap 2건은 모두 cosmetic이며 기능적 영향 없음.

## 잔여 검증
- [ ] 오늘 23:55 OpsMetricsCollector 실행 후 K2 상태 전환 확인
- [ ] 이슈체인 `[WATCHING] → [RESOLVED]` 전환 (검증 완료 후)

## 다음 단계
`/pdca report ops-metrics-waste-query-fix` — Match Rate 90% 이상 → iterate 불필요
