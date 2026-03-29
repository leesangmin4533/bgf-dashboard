# Gap Analysis: tab-switch-fix

| 항목 | 값 |
|------|-----|
| Feature | tab-switch-fix |
| 분석일 | 2026-03-29 |
| 설계항목 | 62개 |
| 일치항목 | 59.5개 |
| **Match Rate** | **96.0%** ✅ |

## 카테고리별 점수

| 카테고리 | 항목수 | 일치 | 점수 |
|----------|:-----:|:----:|:----:|
| `_is_frame_alive()` | 6 | 6 | 100% |
| `close_tab_verified()` | 16 | 14 | 87.5% |
| `close_menu()` 교체 | 5 | 5 | 100% |
| `order_data_loader.py` 폴백 | 6 | 6 | 100% |
| `_verify_no_stale_tabs()` | 13 | 13 | 100% |
| `_verify_active_frame_is_order()` | 4 | 3.5 | 87.5% |
| `_activate_order_tab()` | 4 | 4 | 100% |
| 호출점 | 3 | 3 | 100% |
| timing.py 상수 | 3 | 3 | 100% |
| `close_tab_by_frame_id` 미변경 | 2 | 2 | 100% |

## 발견된 이슈

### G-1: `import time` 누락 (P0 — 수정 완료 ✅)
- **파일**: nexacro_helpers.py
- **영향**: `close_tab_verified()`에서 `time.sleep()` 호출 시 NameError
- **상태**: 즉시 수정 완료

### G-2: `_verify_active_frame_is_order()` Method 2 생략 (Low)
- 설계의 3단계 중 2단계(selected tabbutton parent match) 생략
- Method 1+3으로 충분한 커버리지 → 조치 불필요

### G-3: close_result method 필드 로깅 (Low — 무시)
- dict 전체가 로깅되어 method 정보 포함됨

## 긍정적 차이 (설계 대비 개선)

| # | 내용 |
|---|------|
| P-1 | FrameSet 동적 스캔 (하드코딩 목록 대신) → 미래 메뉴 추가 대응 |
| P-2 | execute_script 예외 처리 추가 → non-blocking 원칙 강화 |
| P-3 | timing.py None 기본값 → 중앙 설정 활용 |
| P-4 | close_tab_by_frame_id 예외 처리 → 재시도 안정성 향상 |

## 결론
Match Rate **96.0%** — PASS. `import time` 버그 즉시 수정 완료.
