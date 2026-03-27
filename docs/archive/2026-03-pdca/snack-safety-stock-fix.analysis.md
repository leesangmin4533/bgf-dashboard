# Gap Analysis: snack-safety-stock-fix (Phase 1.68 포함)

> **Date**: 2026-03-24 | **Match Rate**: 100% | **Status**: PASS

## 변경 전체 (4건)

### Fix A: daily_avg 정상화 (snack_confection.py)

| Design | Implementation | Status |
|--------|----------------|:------:|
| divisor: data_days → analysis_days(30) | L316+L321 | PASS |
| has_enough_data 판정 유지 | L315 보존 | PASS |
| daily_avg: 3.5 → 0.23 (시뮬레이션) | 검증 완료 | PASS |

### Fix B: active 배치 보호 (inventory_repo.py cleanup_stale_stock)

| Design | Implementation | Status |
|--------|----------------|:------:|
| active 배치(remaining_qty>0) 보호 | L554-560 | PASS |
| stale 판정 시 보호 skip | L586-595 | PASS |
| 보호 건수 로깅 | L600-601 | PASS |

### Fix C: get_stale_stock_item_codes (inventory_repo.py)

| Design | Implementation | Status |
|--------|----------------|:------:|
| TTL 기반 stale 판정 (cleanup_stale_stock 동일 기준) | L652-694 | PASS |
| stock_qty > 0 대상 | SQL WHERE 조건 | PASS |
| 3매장 검증 (46513:0, 46704:3, 47863:1) | 라이브 확인 | PASS |

### Fix D: Phase 1.68 DirectAPI Stock Refresh (daily_job.py)

| Design | Implementation | Status |
|--------|----------------|:------:|
| Phase 1.67 뒤, 1.7 전 삽입 | L751-L797 | PASS |
| stale_items 추출 → DirectAPI 배치 조회 | _collect_via_direct_api() 호출 | PASS |
| DirectAPI 실패 시 Selenium 20건 폴백 | L783-789 | PASS |
| 전체 예외 시 발주 플로우 계속 | except + warning | PASS |
| save_to_db=True (RI 자동 갱신) | OrderPrepCollector 파라미터 | PASS |

## 실증 테스트 (BGF 사이트 라이브)

| 단계 | 결과 |
|------|------|
| 로그인 (47863) | OK |
| 템플릿 캡처 (Selenium 1건) | 5초, 성공 |
| DirectAPI 배치 조회 (5건) | 5/5 성공 |
| NOW_QTY 정확성 | 46513 기준 4/4 일치 확인 |

## Test Results

| Category | Count | Result |
|----------|:-----:|:------:|
| snack 관련 | 95 | All Pass |
| inventory/stale/cleanup | 117 | All Pass |
| snack+prediction+adjuster | 256 | All Pass |

## Match Rate: 100%
