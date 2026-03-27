# PDCA Completion Report: 7AM Schedule Order Flow Optimization

## Project Summary
- **Feature**: order (7AM 스케줄 플로우 최적화)
- **PDCA Cycle**: Plan → Design → Do → Check → Report
- **Start Date**: 2026-03-25
- **Completion Date**: 2026-03-25
- **Match Rate**: 100%
- **Test Results**: 2171 passed / 5 pre-existing failures (unrelated)

---

## 1. Background (배경)

7시 자동 스케줄 실행 중 불필요한 지연이 발견됨:
- 폐기전표 상세 수집: 팝업을 1건씩 열어 7.8초/건 소요
- 발주현황 메뉴 2회 중복 진입 (Phase 1.2 + 1.95)
- 예측 로직 2회 중복 실행 (Phase 1.7 + Phase 2)
- 과도한 sleep 대기 시간 (검수/시간대별 매출)

크롬 확장 프로그램으로 실제 BGF 사이트 동작을 검증하며 원인을 특정.

## 2. Implementation (구현)

### #1 폐기전표 상세 Direct API (CRITICAL — 40~80초 절약)

| 항목 | Before | After |
|------|--------|-------|
| 방식 | 팝업 열기/닫기 반복 | `/stgj020/searchDetailType1` Direct API |
| 소요시간/건 | ~7.8초 | ~0.5초 |
| 10건 기준 | ~78초 | ~5초 |

**핵심 발견**: 크롬 네트워크 캡처로 팝업의 `fn_selSearch`가 호출하는 XHR 엔드포인트 `/stgj020/searchDetailType1` 특정.

**구현 파일**:
- `src/collectors/direct_frame_fetcher.py`: `DirectWasteSlipDetailFetcher` 클래스, `parse_waste_slip_detail()` 함수
- `src/collectors/waste_slip_collector.py`: `_try_direct_api_details()`, `_trigger_first_popup_for_capture()`

**패턴**: 첫 번째 전표는 Selenium 팝업으로 XHR 템플릿 캡처 → 나머지 전표는 Direct API 일괄 호출.

### #2 발주현황 메뉴 중복 진입 병합 (5~8초 절약)

- Phase 1.2에서 제외상품 수집 후 **같은 메뉴 세션**에서 OT 동기화까지 수행
- `ot_sync_done` 플래그로 Phase 1.95 스킵 여부 결정
- 실패 시 Phase 1.95가 폴백으로 재시도

### #3 예측 중복 실행 스킵 (5~10초 절약)

- `run_auto_order=True`이면 Phase 1.7 `predict_and_log()` 스킵
- Phase 2의 `AutoOrderSystem`이 동일 예측을 수행하므로 중복 제거

### #4 검수 날짜선택 대기시간 축소 (3초 절약)

- `RECEIVING_DATE_SELECT_WAIT + 2.0` → `+ 0.5` (4.0초 → 2.5초)

### #5 시간대별 상세매출 API delay 축소 (10~15초 절약)

- Direct API 요청 간 delay: 0.5초 → 0.25초 (48회 × 0.25초 = 12초 절약)

## 3. Quality Metrics (품질 지표)

| Metric | Value |
|--------|-------|
| Match Rate | **100%** |
| Tests Passed | **2171** |
| New Tests Added | **14** |
| Files Modified | **5** |
| Gaps Found | **0** |

## 4. Performance Impact (성능 영향)

| Optimization | Estimated Savings |
|-------------|:-----------------:|
| #1 Waste Slip Direct API | 30~60초 |
| #2 Menu Merge | 5~8초 |
| #3 Prediction Skip | 5~10초 |
| #4 Receiving Wait | 3초 |
| #5 Hourly Delay | 10~15초 |
| **Total per store** | **~55~100초** |
| **4 stores parallel** | **~55~100초 (동시)** |

## 5. Files Changed

| File | Changes |
|------|---------|
| `src/collectors/direct_frame_fetcher.py` | +DirectWasteSlipDetailFetcher, +parse_waste_slip_detail() |
| `src/collectors/waste_slip_collector.py` | +_try_direct_api_details(), +_trigger_first_popup_for_capture() |
| `src/scheduler/daily_job.py` | Phase 1.2+1.95 병합, Phase 1.7 스킵, delay=0.25 |
| `src/collectors/receiving_collector.py` | wait +2.0 → +0.5 |
| `src/collectors/hourly_sales_detail_collector.py` | delay=0.5 → 0.25 |
| `tests/test_direct_frame_fetcher.py` | +14 new tests |

## 6. Lessons Learned

1. **크롬 네트워크 캡처가 핵심**: 넥사크로 팝업의 내부 XHR을 직접 확인하여 Direct API 엔드포인트 특정
2. **"첫 1건 Selenium + 나머지 Direct API" 패턴**: OrderPrepCollector에서 이미 검증된 패턴을 폐기전표에도 적용
3. **중복 메뉴 진입 제거**: 같은 화면을 2번 여는 것은 항상 병합 가능
4. **sleep 시간은 주기적으로 재검토**: 안전 마진이 누적되면 불필요한 지연이 됨
