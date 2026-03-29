# Plan: tab-switch-fix

## 개요
| 항목 | 내용 |
|------|------|
| Feature | tab-switch-fix |
| 날짜 | 2026-03-29 |
| 우선순위 | P0 (운영 장애) |
| 예상 공수 | 0.5일 |

## 문제 정의

### 현상
49965 원삼휴게소점에서 발주현황(OT) 조회 후 단품별발주로 돌아올 때, OT 탭이 닫히지 않은 상태에서 스크립트가 진행되어 **홈화면에서 발주가 실행**됨. 빈 상품코드 저장 시도 → 오류 팝업 → Selenium 폴백 이중 입력.

### 근본 원인
`close_tab_by_frame_id()`가 JS 실행 후 **즉시 성공을 반환**하지만, 넥사크로 내부 탭 닫기는 **비동기적**(100~500ms). DOM에서 프레임 소멸을 검증하지 않아 "닫힌 줄 알고" 다음 단계로 진행.

### 왜 46513/46704에서는 미발생
Phase 1.2에서 exclusion 캐시가 성공하여 `skip_exclusion_fetch=True` → OT 탭 자체를 안 열었음.

## 목표
1. 탭 닫기 후 **실제 DOM 소멸을 검증**하는 `close_tab_verified()` 도입
2. Direct API 발주 직전 **잔여 탭 스캔 + 정리** (`_verify_no_stale_tabs()`)
3. 활성 프레임이 **단품별발주인지 검증** (`_verify_active_frame_is_order()`)

## 구현 계획

### Task 1: `close_tab_verified()` + `_is_frame_alive()` 추가
- **파일**: `src/utils/nexacro_helpers.py`
- **내용**:
  - `_is_frame_alive(driver, frame_id)`: tab DOM + FrameSet 객체 2단계 생존 확인
  - `close_tab_verified(driver, frame_id, max_retries=3, poll_timeout=3.0, poll_interval=0.3)`: 기존 `close_tab_by_frame_id` 래핑 + DOM 폴링으로 소멸 검증
  - 재시도 간 팝업/Alert 자동 정리
- **총 최악 소요**: 3회 × (3초 poll + 0.5초 대기) = 10.5초

### Task 2: OT 탭 닫기 호출부 교체
- **파일 1**: `src/collectors/order_status_collector.py` — `close_menu()`
  - 기존 3회 retry for-loop 제거 → `close_tab_verified()` 단순 위임
- **파일 2**: `src/order/order_data_loader.py` — `load_auto_order_items()`
  - `close_tab_by_frame_id()` → `close_tab_verified()` 교체 (정상 + except 블록)

### Task 3: `_verify_no_stale_tabs()` + 활성 프레임 검증 추가
- **파일**: `src/order/order_executor.py`
  - `_verify_no_stale_tabs()`: FrameSet 전체 동적 스캔 → STBJ030_M0 외 닫기
  - `_verify_active_frame_is_order()`: selected 탭 CSS/visible 3단계 확인
  - `_activate_order_tab()`: 불일치 시 STBJ030_M0 탭 클릭
- **호출 위치**: `execute_orders()` 내부, Direct API 호출 직전
- **정책**: Non-blocking (실패해도 발주 시도 계속, 경고 로그)

### Task 4: timing.py 상수 추가
- **파일**: `src/settings/timing.py` + `src/config/timing.py`
  - `VERIFIED_TAB_CLOSE_POLL_TIMEOUT = 3.0`
  - `VERIFIED_TAB_CLOSE_POLL_INTERVAL = 0.3`

### Task 5: 테스트 작성 + 기존 테스트 통과 확인
- `close_tab_verified` 단위 테스트 (T1~T7)
- `_verify_no_stale_tabs` 단위 테스트 (T8~T11)
- 기존 전체 테스트 통과 확인

## 변경 대상 파일 (5개)
| 파일 | 변경 유형 |
|------|----------|
| `src/utils/nexacro_helpers.py` | 함수 2개 추가 |
| `src/collectors/order_status_collector.py` | `close_menu()` 교체 |
| `src/order/order_data_loader.py` | 폴백 교체 |
| `src/order/order_executor.py` | 메서드 3개 추가 + 호출점 |
| `src/settings/timing.py` | 상수 2개 추가 |

## 설계 원칙
1. **기존 `close_tab_by_frame_id()` 변경하지 않음** — 새 함수로 래핑만
2. **Non-blocking 방어** — 검증 실패해도 발주 차단하지 않음 (운영 손실 방지)
3. **2중 방어선** — close_tab_verified (1차) + _verify_no_stale_tabs (2차)

## 참조 토론
- `data/discussions/20260329-tab-switch-fix/03-최종-리포트.md` — 원인 분석
- `data/discussions/20260329-tab-switch-design/03-최종-리포트.md` — 설계 상세
