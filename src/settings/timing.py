"""
타이밍 및 재시도 설정
- Selenium 대기 시간
- 메뉴 탐색 딜레이
- 재시도 횟수
- 발주/수집/로그인 관련 타이밍

정식 경로: from src.settings.timing import ...
호환 경로: from src.config.timing import ...  (역방향 shim)
"""

# =====================================================================
# 페이지/화면 로딩 대기
# =====================================================================
PAGE_LOAD_TIMEOUT = 60          # 페이지 로딩 최대 대기 (초)
IMPLICIT_WAIT = 10              # Selenium 암시적 대기 (초)
FRAME_LOAD_MAX_CHECKS = 10     # 프레임 로딩 확인 최대 횟수
FRAME_LOAD_INTERVAL = 0.5      # 프레임 로딩 확인 간격 (초)
DATA_LOAD_WAIT = 2.0           # 데이터 로딩 대기 (초)
DATA_STABILITY_WAIT = 0.5      # 데이터 안정화 확인 대기 (초)

# =====================================================================
# 메뉴 탐색 딜레이
# =====================================================================
MENU_CLICK_DELAY = 1.0         # 메뉴 클릭 후 대기 (초)
SUBMENU_CLICK_DELAY = 2.0      # 서브메뉴 클릭 후 대기 (초)
TAB_CLOSE_DELAY = 1.0          # 탭 닫기 후 대기 (초)

# =====================================================================
# 상품 입력/조회
# =====================================================================
PRODUCT_INPUT_DELAY = 0.3      # 상품코드 입력 후 대기 (초)
PRODUCT_SEARCH_WAIT = 1.5      # 상품 검색 결과 대기 (초)
INTER_ITEM_DELAY = 0.3         # 상품 간 처리 간격 (초)

# =====================================================================
# 재시도 설정
# =====================================================================
LOGIN_MAX_RETRIES = 3          # 로그인 최대 재시도
ALERT_MAX_RETRIES = 5          # Alert 닫기 최대 재시도
ALERT_RETRY_DELAY = 0.2        # Alert 재시도 간격 (초)
POPUP_WAIT_MAX_CHECKS = 10    # 팝업 대기 최대 횟수
POPUP_WAIT_INTERVAL = 1.0     # 팝업 대기 간격 (초)

# =====================================================================
# HTTP 요청
# =====================================================================
HTTP_REQUEST_TIMEOUT = 10      # HTTP 요청 타임아웃 (초)

# =====================================================================
# 데이터 수집기 (Collectors)
# =====================================================================
RECEIVING_DATE_SELECT_WAIT = 2.0    # 입고 날짜 선택 후 대기 (초)
RECEIVING_DATA_LOAD_WAIT = 1.5      # 입고 데이터 로딩 대기 (초)
COLLECTION_RETRY_WAIT = 1.0         # 수집 재시도 간격 (초)
COLLECTION_MAX_RETRIES = 3          # 수집 최대 재시도 횟수

# =====================================================================
# DOM/UI 공통 대기
# =====================================================================
DOM_SETTLE_WAIT = 0.2          # DOM 요소 안정화 대기 (초)
AFTER_ACTION_WAIT = 0.1        # 액션(클릭/입력) 후 짧은 대기 (초) [최적화: 0.3→0.1]
INTER_REQUEST_DELAY = 0.5      # 요청 간 간격 (초)
POPUP_OPEN_WAIT = 2.0          # 팝업 열림 대기 (초)
POPUP_CLOSE_WAIT = 0.3         # 팝업 닫기 후 대기 (초)

# =====================================================================
# Alert 처리
# =====================================================================
ALERT_CLEAR_MAX_ATTEMPTS = 10  # Alert 일괄 처리 최대 횟수
ALERT_AFTER_CLEAR_WAIT = 0.3   # Alert 처리 후 대기 (초)

# =====================================================================
# 발주 실행 (order_executor)
# =====================================================================
ORDER_MENU_AFTER_CLICK = 1.0       # 발주 메뉴 클릭 후 대기 (초)
ORDER_SUBMENU_AFTER_CLICK = 1.5    # 단품별발주 서브메뉴 클릭 후 대기 (초)
ORDER_TAB_CLOSE_WAIT = 0.5        # 기존 발주 탭 닫기 후 대기 (초)
ORDER_DATE_POPUP_INTERVAL = 1.0   # 날짜 팝업 확인 간격 (초)
ORDER_DATE_POPUP_MAX_CHECKS = 10  # 날짜 팝업 확인 최대 횟수
ORDER_AFTER_DATE_SELECT = 0.5     # 날짜 더블클릭 후 대기 (초)
ORDER_AFTER_POPUP_CLOSE = 1.5     # 팝업 닫기 후 대기 (초)
ORDER_INPUT_FIELD_INIT = 0.5      # 입력 필드 초기화 대기 (초)
ORDER_BEFORE_ENTER = 0.3          # Enter 전 대기 (초)
ORDER_AFTER_ENTER = 1.0           # Enter 후 상품정보 로딩 (초)
ORDER_AFTER_ENTER_EXTRA = 0.0     # Enter 후 추가 대기 (초) [최적화: 1.0→0.0 중복 제거]
ORDER_MULTIPLIER_AFTER_INPUT = 0.2  # 배수 입력 후 대기 (초)
ORDER_MULTIPLIER_AFTER_ENTER = 0.3  # 배수 Enter 후 대기 (초)
ORDER_SAVE_AFTER_CLICK = 1.0      # 저장 버튼 클릭 후 대기 (초)
ORDER_SAVE_ALERT_WAIT = 0.5       # 저장 확인 Alert 대기 (초)
ORDER_BETWEEN_ITEMS = 0.5         # 상품 간 대기 (초) [최적화: 1.0→0.5]
ORDER_LAST_ITEM_EXTRA = 0.5       # 마지막 상품 입력 후 추가 대기 (초) [최적화: 2.0→0.5]
ORDER_BEFORE_SAVE = 1.0           # 전체 저장 전 안정화 대기 (초)
ORDER_BETWEEN_DATES = 1.0         # 날짜 그룹 간 대기 (초)
ORDER_DATE_BUTTON_AFTER = 2.0     # 발주일자 버튼 클릭 후 대기 (초)
ORDER_AFTER_DAY_SELECT = 1.0      # 요일 선택 후 대기 (초)
ORDER_CELL_ACTIVATE_WAIT = 0.3    # 셀 활성화 후 대기 (초)
ORDER_NEW_ROW_DOM_WAIT = 0.3      # 새 행 추가 후 DOM 업데이트 대기 (초)
ORDER_CLOSE_POPUP_WAIT = 0.2      # 상품검색 팝업 닫기 후 대기 (초)
ORDER_INPUT_RETRY_WAIT = 0.3      # 상품코드 입력 재시도 대기 (초)
ORDER_INPUT_VERIFY_WAIT = 0.2     # 입력값 확인 대기 (초)
ORDER_SCREEN_CLEANUP_WAIT = 0.5   # 화면 상태 초기화 후 대기 (초)

# =====================================================================
# 발주 입력 최적화 (Phase 1) - order-input-optimization
# =====================================================================
ORDER_AFTER_ENTER_MIN = 0.3          # 상품정보 로딩 최소 대기 (초)
ORDER_AFTER_ENTER_MAX = 1.5          # 상품정보 로딩 최대 대기 (초)
ORDER_LOADING_CHECK_INTERVAL = 0.1   # 로딩 완료 확인 간격 (초)
ORDER_BETWEEN_ITEMS_FAST = 0.2       # 행 간 대기 (최적화, 초)
ORDER_BETWEEN_ITEMS_SAFE = 0.5       # 행 간 대기 (안전 모드, 초)

# =====================================================================
# 미입고/재고 조회 (order_prep_collector)
# =====================================================================
PREP_ITEM_QUERY_DELAY = 0.5       # 상품별 조회 간격 (초)
PREP_CELL_ACTIVATE_WAIT = 0.3     # 셀 활성화 대기 (초)
PREP_POPUP_CLOSE_WAIT = 0.2       # 팝업 닫기 후 대기 (초)
PREP_FOCUS_WAIT = 0.2             # 포커스 이동 대기 (초)
PREP_INPUT_WAIT = 0.3             # 입력 후 대기 (초)
PREP_SEARCH_LOAD = 1.5            # 상품 검색 결과 로딩 (초)
PREP_DATE_SELECT_WAIT = 0.5       # 날짜 선택 후 대기 (초)
PREP_DATE_BUTTON_WAIT = 1.5       # 선택 버튼 후 대기 (초)
PREP_MENU_CLOSE_WAIT = 0.5        # 메뉴 탭 닫기 후 대기 (초)

# =====================================================================
# 매출 수집 (sales_analyzer)
# =====================================================================
SA_LOGIN_WAIT = 0.3               # 로그인 입력 후 대기 (초)
SA_POPUP_CLOSE_WAIT = 0.5         # 팝업 닫기 후 대기 (초)
SA_MENU_LOAD_WAIT = 1.5           # 메뉴 로딩 대기 (초)
SA_SUBMENU_WAIT = 1.0             # 서브메뉴 클릭 후 대기 (초)
SA_DATA_LOAD_WAIT = 2.0           # 데이터 로딩 대기 (초)
SA_CATEGORY_SWITCH_WAIT = 0.3     # 중분류 전환 간 대기 (초)
SA_DATE_INPUT_WAIT = 0.3          # 날짜 입력 후 대기 (초)
SA_TAB_CLOSE_WAIT = 0.5           # 탭 닫기 후 대기 (초)
SA_FRAME_CHECK_WAIT = 0.5         # 프레임 존재 확인 대기 (초)

# =====================================================================
# 상품 정보 수집 (product_info_collector)
# =====================================================================
PI_SEARCH_RESULT_WAIT = 1.5       # 검색 결과 로딩 대기 (초)
PI_POPUP_LOAD_WAIT = 1.5          # 팝업 로딩 대기 (초)
PI_POPUP_OPEN_WAIT = 2.0          # 팝업 열림 대기 (초)
PI_AFTER_CLICK_WAIT = 0.5         # 클릭 후 대기 (초)
PI_INPUT_WAIT = 0.3               # 입력 후 대기 (초)
PI_BETWEEN_ITEMS = 1.0            # 상품 간 처리 대기 (초)

# =====================================================================
# 로그인/스케줄러
# =====================================================================
LOGIN_RETRY_DELAY = 10            # 로그인 재시도 간격 (초)
SCHEDULER_RETRY_DELAY = 30        # 스케줄러 오류 시 재시도 간격 (초)
BACKFILL_BETWEEN_DATES = 5        # 백필 수집 날짜 간 대기 (초)
DAILY_JOB_AFTER_CLOSE_TAB = 1.0   # 매출 탭 닫기 후 발주 전환 대기 (초)

# =====================================================================
# 입고/발주현황 수집
# =====================================================================
RC_DATA_LOAD_WAIT = 1.0           # 데이터 로딩 대기 (초)
RC_AFTER_CLICK_WAIT = 0.5         # 클릭 후 대기 (초)
OS_MENU_CLICK_WAIT = 1.0          # 메뉴 클릭 후 대기 (초)
OS_DATA_LOAD_WAIT = 2.0           # 데이터 로딩 대기 (초)
OS_RADIO_CLICK_WAIT = 2.0         # 라디오 버튼 클릭 후 데이터 갱신 대기 (초)
OS_MENU_CLOSE_WAIT = 1.0          # 발주 현황 조회 탭 닫기 후 대기 (초)

# =====================================================================
# 벌크 상품 상세 수집
# =====================================================================
BULK_COLLECT_BATCH_SIZE = 50      # 배치 크기 (진행률 저장 단위)
BULK_COLLECT_MENU_REFRESH = 200   # 메뉴 리프레시 간격 (상품 수)
BULK_COLLECT_ITEM_DELAY = 0.5     # 상품 간 딜레이 (초)

# =====================================================================
# 발주 실패 사유 조회 (fail_reason_collector)
# =====================================================================
FR_BARCODE_INPUT_WAIT = 0.3       # 바코드 입력 후 대기 (초)
FR_POPUP_OPEN_WAIT = 2.0          # 팝업 열림 대기 (초)
FR_POPUP_MAX_CHECKS = 10          # 팝업 대기 최대 확인 횟수
FR_POPUP_CHECK_INTERVAL = 0.5     # 팝업 대기 확인 간격 (초)
FR_POPUP_CLOSE_WAIT = 0.5         # 팝업 닫기 후 대기 (초)
FR_BETWEEN_ITEMS = 1.0            # 상품 간 처리 간격 (초)
FR_DATA_LOAD_MAX_CHECKS = 10      # 팝업 데이터 로딩 대기 최대 확인 횟수
FR_DATA_LOAD_CHECK_INTERVAL = 0.3  # 팝업 데이터 로딩 확인 간격 (초)

# =====================================================================
# 폐기 전표 상세 품목 수집 (waste_slip_collector popup)
# =====================================================================
WS_DSGS_SETUP_WAIT = 0.3         # dsGs 파라미터 설정 후 대기 (초)
WS_POPUP_CLOSE_WAIT = 0.5        # 기존 팝업 닫기 후 대기 (초)
WS_POPUP_OPEN_WAIT = 3.0         # 팝업 열기 후 대기 (초, 렌더링+onload)
WS_SEARCH_WAIT = 4.0             # fn_selSearch 서버 응답 대기 (초, 캐시 완화)

# =====================================================================
# 진행 로그 출력 간격
# =====================================================================
PROGRESS_LOG_INTERVAL = 10        # 진행 상황 출력 간격 (건수)
