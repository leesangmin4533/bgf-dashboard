"""
BGF 자동 발주 시스템 - 비즈니스 상수
- 카테고리 코드
- 기본값 / 상한선
- 상태 문자열
- 데이터 기간 / 로그 포맷

정식 경로: from src.settings.constants import ...
호환 경로: from src.config.constants import ...  (역방향 shim)
"""

# =====================================================================
# 중분류 카테고리 코드 (mid_cd)
# =====================================================================

# 푸드류 (유통기한 기반 안전재고)
MID_DOSIRAK = "001"       # 도시락
MID_JUMEOKBAP = "002"     # 주먹밥
MID_GIMBAP = "003"        # 김밥
MID_SANDWICH = "004"      # 샌드위치
MID_HAMBURGER = "005"     # 햄버거
MID_BREAD = "012"         # 빵

# 면류 (회전율 기반)
MID_RAMEN_COOKED = "006"  # 조리면
MID_RAMEN_NOODLE = "032"  # 면류

# 주류 (요일 패턴)
MID_BEER = "049"          # 맥주
MID_SOJU = "050"          # 소주

# 담배 (보루/소진 패턴)
MID_TOBACCO = "072"       # 담배
MID_E_TOBACCO = "073"     # 전자담배

# 기타
MID_SUPPLIES = "900"      # 소모품

# 카테고리 그룹
FOOD_CATEGORIES = [
    MID_DOSIRAK, MID_JUMEOKBAP, MID_GIMBAP,
    MID_SANDWICH, MID_HAMBURGER, MID_BREAD,
]
RAMEN_CATEGORIES = [MID_RAMEN_COOKED, MID_RAMEN_NOODLE]
ALCOHOL_CATEGORIES = [MID_BEER, MID_SOJU]
TOBACCO_CATEGORIES = [MID_TOBACCO, MID_E_TOBACCO]

# 푸드류 중분류 코드 Set (배송 차수 판별용)
FOOD_MID_CODES = {
    MID_DOSIRAK, MID_JUMEOKBAP, MID_GIMBAP,
    MID_SANDWICH, MID_HAMBURGER,
}

# 전체 관리 대상 카테고리
ALL_MANAGED_CATEGORIES = (
    FOOD_CATEGORIES + RAMEN_CATEGORIES +
    ALCOHOL_CATEGORIES + TOBACCO_CATEGORIES + [MID_SUPPLIES]
)

# =====================================================================
# 기본값 (Default Values)
# =====================================================================
DEFAULT_STORE_ID = "46513"                    # 기본 매장 코드 (호반점)
DEFAULT_ORDERABLE_DAYS = "일월화수목금토"   # 발주 가능 요일 (전체)
SNACK_DEFAULT_ORDERABLE_DAYS = "월화수목금토"  # 스낵류(015~030) 기본 발주가능요일 (일요일 제외)
RAMEN_DEFAULT_ORDERABLE_DAYS = "월화수목금토"  # 라면(006, 032) 기본 발주가능요일 (일요일 제외)
DEFAULT_ORDER_UNIT_QTY = 1                  # 기본 발주 단위 (낱개)
DEFAULT_ORDER_UNIT_NAME = "낱개"            # 기본 발주 단위명
DEFAULT_MIN_ORDER_QTY = 1                   # 최소 발주 수량
DEFAULT_SHELF_LIFE_DAYS = None              # 유통기한 기본값 (없음)

# 카테고리별 유통기한 (일)
CATEGORY_EXPIRY_DAYS = {
    # 푸드류 (유통기한 1~3일)
    MID_DOSIRAK: 1,      # 도시락
    MID_JUMEOKBAP: 1,    # 주먹밥
    MID_GIMBAP: 1,       # 김밥
    MID_SANDWICH: 2,     # 샌드위치
    MID_HAMBURGER: 1,    # 햄버거
    MID_BREAD: 3,        # 빵
    # 중간 유통기한 카테고리
    "006": 7,            # 조리면
    "013": 7,            # 유제품
    "026": 3,            # 반찬
    "027": 14,           # 즉석밥
    "028": 14,           # 즉석국
    "031": 7,            # 즉석조리
    "033": 7,            # 즉석조리
    "034": 90,           # 아이스크림
    "035": 7,            # 즉석조리
    "046": 5,            # 신선식품
}
DEFAULT_EXPIRY_DAYS_FOOD = 1        # 푸드류 기본 유통기한
DEFAULT_EXPIRY_DAYS_NON_FOOD = 30   # 비푸드류 기본 유통기한

# =====================================================================
# 발주 상한선 / 제한
# =====================================================================
MAX_ORDER_MULTIPLIER = 99         # 최대 발주 배수 (BGF 시스템 제한)
MAX_PENDING_ITEMS = 200           # 미입고 조회 최대 상품 수
MAX_PREFETCH_ITEMS = 50           # 상품 상세 사전 수집 최대 건수
MAX_DAILY_ORDER_ITEMS = 50        # 일일 자동 발주 기본 최대 상품 수
TOBACCO_MAX_STOCK = 30            # 담배 최대 재고 상한선 (개)

# =====================================================================
# CUT(발주중지) 상태 신선도
# =====================================================================
CUT_STATUS_STALE_DAYS = 3         # CUT 상태 신선도 기준일 (이 기간 이상 미조회 → 미확인 취급)
CUT_STALE_PRIORITY_CHECK = 100    # 발주 전 우선 CUT 재조회 최대 건수

# =====================================================================
# 과잉발주 보정 (over-order-correction)
# =====================================================================
# PASS 상품 발주량 상한 (PASS = 예측기 위임이지만 과잉 방지)
PASS_MAX_ORDER_QTY = 3
# FORCE_ORDER 최소 일평균 판매량 (미만이면 NORMAL로 다운그레이드)
FORCE_MIN_DAILY_AVG = 0.1
# FORCE_ORDER 발주량 상한 (일평균 예측 × N일). 품절 복구 시 과대발주 방지
FORCE_MAX_DAYS = 2
# Feature flags (False로 변경하면 즉시 롤백)
ENABLE_PASS_SUPPRESSION = True
ENABLE_FORCE_DOWNGRADE = True
# 간헐수요 유통기한1일 상품 FORCE_ORDER 억제 (폐기 사이클 방지)
ENABLE_FORCE_INTERMITTENT_SUPPRESSION = True
FORCE_INTERMITTENT_SELL_RATIO = 0.5    # 판매빈도 50% 미만 → 간헐수요
FORCE_INTERMITTENT_MAX_EXPIRY = 1      # 유통기한 1일 이하만 대상
FORCE_INTERMITTENT_COOLDOWN_DAYS = 1   # 최근 N일 이내 발주 시 추가 사유
# 보정 루프 1회 최대 파라미터 변경 수
MAX_PARAMS_PER_CALIBRATION = 3

# 행사 타입별 최소 재고 단위
PROMO_MIN_STOCK_UNITS = {
    "1+1": 2,
    "2+1": 3,
}
INVENTORY_CLEANUP_HOURS = 24      # 오래된 인벤토리 데이터 삭제 기준 (시간)

# 단기유통 푸드류 미입고 할인율 (유통기한 1일 이하)
# 미입고 수량의 50%만 내일 발주 차감에 반영
# 근거: 유통기한 1일 미입고 = 오늘 배송분 → 오늘 소진 예상 → 내일 재고에 미반영
FOOD_SHORT_EXPIRY_PENDING_DISCOUNT = 0.5

# =====================================================================
# 예측/분석 기간
# =====================================================================
PREDICTION_DATA_DAYS = 31         # 예측기 사용 데이터 기간 (일)
WEEKLY_REPORT_DAYS = 7            # 주간 리포트 기간 (일)
STOCKOUT_LOOKBACK_DAYS = 30       # 품절 빈도 계산 기간 (일)
EVAL_DAILY_AVG_DAYS = 14          # 사전 평가 일평균 기간 (기본값)

# =====================================================================
# 신뢰도 등급
# =====================================================================
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

# =====================================================================
# 발주 상태
# =====================================================================
ORDER_STATUS_PENDING = "pending"
ORDER_STATUS_SUCCESS = "success"
ORDER_STATUS_FAIL = "fail"

# 발주 불가 상태 키워드
ORDERABLE_STATUS_BLOCKED = ["발주불가", "단종", "품절"]

# =====================================================================
# 스케줄러
# =====================================================================
DAILY_JOB_SCHEDULE_TIME = "07:00"     # 일일 자동 실행 시간
WEEKLY_REPORT_SCHEDULE_TIME = "08:00"  # 주간 리포트 시간 (월요일)

# =====================================================================
# 요일 매핑
# =====================================================================
WEEKDAY_KR = {
    0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"
}

WEEKDAY_KR_TO_NUM = {
    "월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6
}

# =====================================================================
# 로그 포맷
# =====================================================================
LOG_SEPARATOR_THIN = "-" * 40
LOG_SEPARATOR_NORMAL = "-" * 60
LOG_SEPARATOR_WIDE = "=" * 60
LOG_SEPARATOR_EXTRA = "=" * 70
LOG_SEPARATOR_FULL = "=" * 80

# =====================================================================
# 수집 관련 (타이밍 상수는 timing.py 참조)
# =====================================================================
COLLECTION_RETRY_BASE_WAIT = 30   # 수집 재시도 대기 기본값 (초, attempt * base)

# =====================================================================
# =====================================================================
# 재고 배치 추적 (FIFO 폐기 관리)
# =====================================================================
BATCH_STATUS_ACTIVE = "active"          # 추적중
BATCH_STATUS_CONSUMED = "consumed"      # 전량 소진
BATCH_STATUS_EXPIRED = "expired"        # 폐기 확정
BATCH_EXPIRY_ALERT_DAYS = 1             # 폐기 N일 전 알림

# DB 스키마 버전
# =====================================================================
DB_SCHEMA_VERSION = 41  # v41: dashboard_users phone 컬럼 추가

# =====================================================================
# 수동 발주 추적
# =====================================================================
ORDER_SOURCE_AUTO = 'auto'         # 자동발주
ORDER_SOURCE_MANUAL = 'manual'     # 수동발주
MANUAL_ORDER_DETECT_TIMES = ['08:00', '21:00']  # 수동 발주 감지 스케줄 (1차: 20:00 입고 후, 2차: 07:00 입고 후)

# =====================================================================
# 발주 실패 사유 조회
# =====================================================================
FAIL_REASON_UNKNOWN = "알수없음"
FAIL_REASON_MAX_ITEMS = 100

# =====================================================================
# 발주 입력 최적화 설정 - order-input-optimization
# =====================================================================
ORDER_INPUT_OPTIMIZATION_PHASE = 2         # 0: 비활성화, 1: Phase1, 2: Phase2, 3: Phase3
ORDER_INPUT_OPTIMIZATION_AUTO_ROLLBACK = True  # 실패율 5% 초과 시 자동 롤백

# 디버그 스크린샷 설정
DEBUG_SCREENSHOT_ENABLED = False           # 전체 스크린샷 활성화 여부
DEBUG_SCREENSHOT_ON_ERROR = True           # 에러 시에만 스크린샷
DEBUG_SCREENSHOT_SAMPLE_RATE = 0.0         # 샘플링 비율 (0.1 = 10%)
DEBUG_SCREENSHOT_MAX_PER_SESSION = 5       # 세션당 최대 스크린샷 수

# =====================================================================
# 미입고 계산 방식
# =====================================================================
USE_SIMPLIFIED_PENDING = False             # 단순화된 미입고 계산 사용 여부 (기본: 기존 방식)
PENDING_LOOKBACK_DAYS = 3                  # 단순화 방식 조회 기간 (일)
PENDING_COMPARISON_MODE = True             # ✅ Phase 3 활성화 (2026-02-07): 비교 모드 실행 중

# =====================================================================
# 카테고리별 최대 발주량 상한 (과다 예측 방지) - Priority 1.2
# =====================================================================
MAX_ORDER_QTY_BY_CATEGORY = {
    "900": 20,  # 소모품: 최대 20개
    "060": 15,  # 홈/주방용품: 최대 15개
    "053": 10,  # 와인: 최대 10개
    "032": 30,  # 면류: 최대 30개 (과예측 방지)
    "020": 25,  # 캔디: 최대 25개 (과예측 방지)
    "016": 25,  # 스낵류: 최대 25개 (과예측 방지)
}

# =====================================================================
# 신상품 도입 현황 (상생지원제도) [부분 활성]
# =====================================================================
NEW_PRODUCT_MODULE_ENABLED = True         # 모듈 ON (수집 + 3일발주 후속 관리)
NEW_PRODUCT_AUTO_INTRO_ENABLED = False    # [보류] 미도입 자동 발주 — 유통기한 매핑 미구현
NEW_PRODUCT_TARGET_TOTAL_SCORE = 95       # 목표 종합점수 (95+ → 16만원 구간)
NEW_PRODUCT_MIN_DOIP_RATE = 95.0          # 목표 도입률 (%) → 도입 점수 80점 확보
NEW_PRODUCT_MIN_DS_RATE = 70.0            # 목표 3일발주 달성률 (%) → 달성 점수 20점 확보
NEW_PRODUCT_INTRO_ORDER_QTY = 1           # 신상품 도입 발주 수량 (최소 1개)
NEW_PRODUCT_DS_MIN_ORDERS = 3             # 3일발주 달성 기준: 기간 내 최소 발주 횟수
NEW_PRODUCT_DS_OVERSTOCK_RATIO = 1.5      # 과발주 방지: 재고 > 유통기한×일평균×이 비율이면 스킵
NEW_PRODUCT_POPUP_FRAME_ID = "STBJ460_P0" # 미도입 상품 상세 팝업 프레임 ID

# =====================================================================
# 발주 차이 피드백 설정 (order_analysis.db 기반)
# =====================================================================
DIFF_FEEDBACK_ENABLED = True              # 피드백 시스템 활성화
DIFF_FEEDBACK_LOOKBACK_DAYS = 14          # 분석 기간 (최근 N일)

# 제거 페널티: {최소_횟수: 페널티_계수}
# order_qty × penalty (1.0=없음, 0.3=70% 감소)
DIFF_FEEDBACK_REMOVAL_THRESHOLDS = {
    3: 0.7,    # 3회 이상 제거 → 수량 30% 감소
    6: 0.5,    # 6회 이상 제거 → 수량 50% 감소
    10: 0.3,   # 10회 이상 제거 → 수량 70% 감소
}

# 반복 추가 부스트
DIFF_FEEDBACK_ADDITION_MIN_COUNT = 3      # 최소 3회 추가 시 부스트
DIFF_FEEDBACK_ADDITION_MIN_QTY = 1        # 부스트 최소 발주 수량

# =====================================================================
# 폐기 원인 분석 (Waste Cause Analysis)
# =====================================================================

# 원인 분류 레이블
WASTE_CAUSE_OVER_ORDER = "OVER_ORDER"              # 과잉 발주
WASTE_CAUSE_EXPIRY_MGMT = "EXPIRY_MISMANAGEMENT"   # 유통기한 관리 실패
WASTE_CAUSE_DEMAND_DROP = "DEMAND_DROP"             # 갑작스런 수요 감소
WASTE_CAUSE_MIXED = "MIXED"                         # 복합/불명

# 피드백 액션 레이블
WASTE_FEEDBACK_REDUCE_SAFETY = "REDUCE_SAFETY"       # OVER_ORDER → 안전재고 감소
WASTE_FEEDBACK_SUPPRESS = "SUPPRESS_ORDER"           # EXPIRY_MGMT → 발주 억제
WASTE_FEEDBACK_TEMP_REDUCE = "TEMP_REDUCE"           # DEMAND_DROP → 임시 감소
WASTE_FEEDBACK_DEFAULT = "DEFAULT"                   # MIXED / 알 수 없음

# 분류 임계값
WASTE_CAUSE_LOOKBACK_DAYS = 14           # 피드백 조회 기간 (일)
WASTE_CAUSE_OVER_ORDER_RATIO = 1.5       # 발주량 / (일평균 x 유통기한) 기준
WASTE_CAUSE_WASTE_RATIO_HIGH = 0.5       # 폐기율 high 기준 (50% 이상)
WASTE_CAUSE_DEMAND_DROP_TREND = 0.6      # DEMAND_DROP 트렌드 비율 기준
WASTE_CAUSE_DEMAND_DROP_SOLD = 0.5       # 예측 대비 실제 판매량 비율 기준
WASTE_CAUSE_SELL_DAY_LOW = 0.5           # EXPIRY_MGMT sell_day_ratio 기준
WASTE_CAUSE_TEMP_CHANGE_THRESHOLD = 10.0 # 급격한 기온 변화 기준 (deg C)
WASTE_CAUSE_PROMO_ENDED_DAYS = 3         # 행사 종료 후 감안 기간 (일)

# 피드백 승수 (cause별)
WASTE_FEEDBACK_OVER_ORDER_MULT = 0.75         # OVER_ORDER: 25% 감소
WASTE_FEEDBACK_EXPIRY_MGMT_MULT = 0.85        # EXPIRY_MGMT: 15% 감소
WASTE_FEEDBACK_DEMAND_DROP_MULT_START = 0.80   # DEMAND_DROP: 초기 20% 감소
WASTE_FEEDBACK_DEMAND_DROP_DECAY_DAYS = 7      # DEMAND_DROP: 7일에 걸쳐 회복

# 피드백 만료 기간 (cause별, 일)
WASTE_FEEDBACK_OVER_ORDER_EXPIRY_DAYS = 14
WASTE_FEEDBACK_EXPIRY_MGMT_EXPIRY_DAYS = 21
WASTE_FEEDBACK_DEMAND_DROP_EXPIRY_DAYS = 7

# =====================================================================
# 푸드 폐기율 자동 보정 (FoodWasteRateCalibrator)
# =====================================================================
# 중분류별 목표 폐기율 (차등 설정: 유통기한 짧을수록 높게)
FOOD_WASTE_RATE_TARGETS = {
    "001": 0.20,  # 도시락: 20% (1일 유통)
    "002": 0.20,  # 주먹밥: 20%
    "003": 0.20,  # 김밥: 20%
    "004": 0.15,  # 샌드위치: 15% (2일 유통)
    "005": 0.18,  # 햄버거: 18%
    "012": 0.12,  # 빵: 12% (3일 유통)
}
FOOD_WASTE_CAL_ENABLED = True             # 자동 보정 활성화
FOOD_WASTE_CAL_MIN_DAYS = 14              # 최소 데이터 기간 (일)
FOOD_WASTE_CAL_LOOKBACK_DAYS = 21         # 폐기율 계산 기간 (일)
FOOD_WASTE_CAL_DEADBAND = 0.02            # ±2%p 불감대 (이내면 조정 안 함)
FOOD_WASTE_CAL_STEP_SMALL = 0.02          # 미세 조정 단위
FOOD_WASTE_CAL_STEP_LARGE = 0.05          # 큰 조정 단위
FOOD_WASTE_CAL_ERROR_LARGE = 0.05         # 큰 오차 기준 (5%p)

# 파라미터별 안전 범위 (하한, 상한)
FOOD_WASTE_CAL_SAFETY_DAYS_RANGE = {
    "ultra_short": (0.35, 0.8),  # 1일 유통 (하한 0.2->0.35: 품절 방어)
    "short": (0.45, 1.0),        # 2-3일 유통 (하한 0.3->0.45)
    "medium": (0.5, 1.5),        # 4-7일 유통
    "long": (0.8, 2.0),          # 8-30일 유통
    "very_long": (1.0, 3.0),     # 31일+ 유통
}
FOOD_WASTE_CAL_GAP_COEF_RANGE = {
    "ultra_short": (0.2, 0.7),   # 하한 0.1->0.2 (배송갭 최소 반영)
    "short": (0.3, 0.8),         # 하한 0.2->0.3
    "medium": (0.4, 1.0),
    "long": (0.6, 1.0),
    "very_long": (0.6, 1.0),
}
FOOD_WASTE_CAL_WASTE_BUFFER_RANGE = (1, 5)  # waste_buffer 범위

# ── 동적 폐기 계수 파라미터 (get_dynamic_disuse_coefficient) ──
DISUSE_COEF_FLOOR = 0.65             # 최소 계수 (최대 35% 감량, was 0.5=50%)
DISUSE_COEF_MULTIPLIER = 1.2         # 폐기율 승수 (was 1.5)
DISUSE_MIN_BATCH_COUNT = 14          # 상품별 블렌딩 최소 배치 수 (was 7)
DISUSE_IB_LOOKBACK_DAYS = 30         # inventory_batches 조회 기간 (일)
