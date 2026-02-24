# 부분발주 수정 내용

## 1. collect_for_item() (order_prep_collector.py)
- ❌ 제거: @auto_close_popups 데코레이터
- ✅ 추가: return 직전 명시적 팝업 정리
- ✅ 추가: 예외 발생 시에도 팝업 정리

## 2. close_menu() (order_prep_collector.py)
- ✅ 추가: Alert 처리 (최대 5회 시도)
- ✅ 추가: 팝업 처리 (카운트 로깅)
- ✅ 추가: 단계별 진행 로그

## 3. _ensure_clean_screen_state() (auto_order.py)
- ✅ 개선: Alert/팝업 카운트 표시
- ✅ 추가: 구분선 및 단계별 체크마크
- ✅ 개선: silent=False로 변경 (로그 출력)

## 4. navigate_to_single_order() (order_executor.py)
- ❌ 제거: @auto_close_popups 데코레이터
- ✅ 추가: 명시적 Alert + 팝업 정리
- ✅ 추가: 정리 카운트 로그

## 테스트 방법
bash
cd bgf_auto
python scripts/run_auto_order.py --categories 001,002 --max-items 3


## 확인 포인트
1. "미입고 조회 메뉴 닫기 시작..." 로그 출력
2. "화면 상태 초기화 시작" 로그 출력
3. "단품별 발주 메뉴 이동 시작" 로그 출력
4. Alert/팝업 카운트 표시
5. 발주가 실제로 시작되는지 확인
