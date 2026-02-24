"""
BGF 스토어 시스템 UI 설정
- 프레임 ID
- 데이터셋 경로
- 메뉴 텍스트

정식 경로: from src.settings.ui_config import ...
호환 경로: from src.config.ui_config import ...  (역방향 shim)
"""

# 화면별 프레임 ID
FRAME_IDS = {
    "SALES_HOURLY": "STAJ001_M0",       # 매출분석 > 시간대별 매출
    "SINGLE_ORDER": "STBJ030_M0",       # 발주 > 단품별 발주
    "CATEGORY_ORDER": "STBJ010_M0",     # 발주 > 카테고리 발주
    "ORDER_STATUS": "STBJ070_M0",       # 발주 > 발주 현황 조회
    "RECEIVING": "STGJ010_M0",          # 검수전표 > 센터매입 조회/확정
    "NEW_PRODUCT_STATUS": "SS_STBJ460_M0",  # 점주관리 > 신상품 도입 현황
    "WASTE_SLIP": "STGJ020_M0",             # 검수전표 > 통합 전표 조회
}

# 데이터셋 접근 경로
DS_PATHS = {
    "SINGLE_ORDER": "div_workForm.form.div_work_01.form",
    "ORDER_STATUS": "div_workForm.form.div_work.form",
    "NEW_PRODUCT_STATUS": "div_workForm.form",
}

# 넥사크로 공통 접근 경로
NEXACRO_BASE_PATH = "app.mainframe.HFrameSet00.VFrameSet00.FrameSet"

# 메뉴 텍스트
MENU_TEXT = {
    "SALES_ANALYSIS": "매출분석",
    "ORDER": "발주",
    "RECEIVING": "검수전표",
    "STORE_MANAGEMENT": "점주관리",
}

SUBMENU_TEXT = {
    "HOURLY_SALES": "시간대별 매출",
    "SINGLE_ORDER": "단품별 발주",
    "ORDER_STATUS": "발주 현황 조회",
    "CATEGORY_ORDER": "카테고리별 발주",
    "RECEIVING_CENTER": "센터매입",
    "NEW_PRODUCT_STATUS": "신상품 도입 현황",
    "WASTE_SLIP": "통합 전표 조회",
}

# 발주 실패 사유 조회 UI
FAIL_REASON_UI = {
    "BARCODE_INPUT": "edt_pluSearch",
    "BARCODE_INPUT_DOM": "mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame.form.edt_pluSearch:input",
    "POPUP_ID": "CallItemDetailPopup",
    "STOP_REASON_DOM": "CallItemDetailPopup.form.divInfo01.form.stStopReason:text",
    "POPUP_CLOSE_BTN": "mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame.CallItemDetailPopup.form.btn_close",
}

# 기본값
DEFAULT_ORDERABLE_DAYS = "일월화수목금토"
DEFAULT_ORDER_UNIT_QTY = 1
DEFAULT_ORDER_UNIT_NAME = "낱개"
