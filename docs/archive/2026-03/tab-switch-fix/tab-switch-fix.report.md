# Completion Report: tab-switch-fix

## 개요

| 항목 | 값 |
|------|-----|
| Feature | tab-switch-fix |
| 기간 | 2026-03-29 (당일 완료) |
| Match Rate | 96.0% |
| 우선순위 | P0 (운영 장애) |

## 문제 → 해결

### Before
발주현황(OT) 탭 닫기 후 DOM 소멸 미검증 → 넥사크로 비동기 닫기가 완료되기 전에 다음 단계 진행 → 49965에서 홈화면에서 발주 실행 → 빈 상품코드 → 저장 실패 → Selenium 폴백 이중 입력

### After
2중 방어선 도입:
1. **`close_tab_verified()`**: 탭 닫기 + DOM 폴링 소멸 검증 (3회 재시도, 3초 타임아웃)
2. **`_verify_no_stale_tabs()`**: 발주 직전 FrameSet 동적 스캔 → 잔여 탭 정리 + 활성 프레임 확인

## 변경 파일 (5개)

| 파일 | 변경 내용 |
|------|----------|
| `src/utils/nexacro_helpers.py` | `_is_frame_alive()`, `close_tab_verified()` 추가 |
| `src/collectors/order_status_collector.py` | `close_menu()` → verified 교체 |
| `src/order/order_data_loader.py` | 폴백 → verified 교체 |
| `src/order/order_executor.py` | `_verify_no_stale_tabs()`, `_verify_active_frame_is_order()`, `_activate_order_tab()` 추가 |
| `src/settings/timing.py` | `VERIFIED_TAB_CLOSE_POLL_TIMEOUT/INTERVAL` 상수 |

## 설계 원칙 준수

| 원칙 | 준수 |
|------|------|
| 기존 `close_tab_by_frame_id()` 미변경 | ✅ 래핑만 |
| Non-blocking 방어 (실패해도 발주 계속) | ✅ 경고 로그만 |
| 2중 방어선 | ✅ close_tab_verified + _verify_no_stale_tabs |
| 매직 넘버 제거 (timing.py) | ✅ |

## Gap 분석 결과

| 카테고리 | 점수 |
|----------|:----:|
| _is_frame_alive() | 100% |
| close_tab_verified() | 87.5% |
| close_menu() 교체 | 100% |
| order_data_loader 폴백 | 100% |
| _verify_no_stale_tabs() | 100% |
| _verify_active_frame_is_order() | 87.5% |
| _activate_order_tab() | 100% |
| 호출점 + timing + 미변경 확인 | 100% |
| **전체 Match Rate** | **96.0%** |

### 발견 이슈
- **G-1 (P0)**: `import time` 누락 → 즉시 수정 완료
- G-2 (Low): Method 2 생략 → 조치 불필요
- G-3 (Low): 로깅 형식 차이 → 무시

### 긍정적 차이 (설계 대비 개선)
- FrameSet 동적 스캔 (하드코딩 대신)
- execute_script 예외 처리 추가
- timing.py None 기본값 → 중앙 설정
- close_tab_by_frame_id 예외 처리 추가

## PDCA 사이클 이력

| Phase | 상태 | 비고 |
|-------|------|------|
| Plan | ✅ | `docs/01-plan/features/tab-switch-fix.plan.md` |
| Design | ✅ | `data/discussions/20260329-tab-switch-design/03-최종-리포트.md` |
| Do | ✅ | 5개 파일 수정, 2265 tests passed |
| Check | ✅ | Match Rate 96.0%, P0 버그 수정 |
| Report | ✅ | 본 문서 |

## 관련 토론
- `data/discussions/20260329-tab-switch-fix/` — 원인 분석
- `data/discussions/20260329-tab-switch-design/` — 설계 상세
- `data/discussions/20260329-structural-rca/` — 구조적 근본 원인
- `data/discussions/20260329-incubator-review/` — 신규 매장 인큐베이터 검토
