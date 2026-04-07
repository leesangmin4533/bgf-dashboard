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
# 대분류(large_cd) → 중분류(mid_cd) 매핑
# (mid_categories.large_cd 미등록 시 fallback)
# =====================================================================
LARGE_CD_TO_MID_CD = {
    "01": ["001", "002", "003", "004", "005"],           # 간편식사
    "02": ["006", "007", "008", "009", "010", "011", "012"],  # 즉석식품
    "11": ["013", "014"],                                # 빙과/아이스크림
    "12": ["015", "016", "017", "020", "021"],           # 과자류
    "13": ["018", "019"],                                # 캔디/초콜릿
    "14": ["022", "023"],                                # 원두/차류
    "21": ["024", "025"],                                # 라면/면류
    "22": ["026", "027", "028", "029"],                  # 가공식품
    "23": ["030", "031", "032", "033"],                  # 유제품/건강식품
    "31": ["034", "035", "036", "037"],                  # 일반음료
    "33": ["038", "039", "040", "041"],                  # 주류
    "34": ["042", "043"],                                # RTD/커피
    "41": ["044", "045", "046"],                         # 위생/세제
    "42": ["047", "048"],                                # 화장품
    "43": ["049", "050", "051"],                         # 잡화
    "44": ["052", "053", "054", "055", "056"],           # 담배
    "45": ["057", "058"],                                # 선불카드
    "91": ["059", "060"],                                # 기타
}

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
TOBACCO_MAX_STOCK = 41            # 담배 최대 재고 상한선 (보루형 상한, 패턴 실패 시 폴백)

# ── 담배 보루/낱개/LIL 분류 (tobacco-strategy-improvement) ──
TOBACCO_DISPLAY_MAX = 11            # 진열대 최대 용량 (낱개 기준)
TOBACCO_BOURU_UNIT = 10             # 보루 단위 (1보루 = 10개)
TOBACCO_BOURU_THRESHOLD = 10        # 보루 판별 기준: 하루 판매 이 값 이상이면 보루 구매 있었음
TOBACCO_BOURU_LOOKBACK_DAYS = 60    # 보루 이력 조회 기간 (일)
TOBACCO_BOURU_FREQ_MULTIPLIER = 0.5 # 보루 빈도 보수적 계수 (3회 → 1.5보루)
TOBACCO_BOURU_DECAY_DAYS = 30       # 감쇠 기준: 최근 이 일수 내 이력 없으면 감쇠 적용
TOBACCO_BOURU_DECAY_RATIO = 0.5     # 감쇠 비율 (목표 재고 절반으로 축소)
TOBACCO_BOURU_MAX_STOCK = 41        # 보루형 최대 재고 상한 (진열대11 + 보루3개)
TOBACCO_SINGLE_URGENT_THRESHOLD = 3 # 낱개형 URGENT 기준 재고 수
TOBACCO_BOURU_URGENT_THRESHOLD = 10 # 보루형 URGENT 기준 재고 수 (보루 1개 미만)
TOBACCO_FORCE_MIN_DAILY_AVG = 2.0   # FORCE_ORDER 기준 일평균 판매량
LIL_BOURU_THRESHOLD = 20            # LIL류(액상카트리지) 보루 판별 기준

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
FORCE_MIN_DAILY_AVG = 0.3  # 0.1→0.3 상향 (2026-03-06): daily_avg<0.3 FORCE 판매율 10.7%로 불필요 발주 유발
# FORCE_ORDER 발주량 상한 (일평균 예측 × N일). 품절 복구 시 과대발주 방지
FORCE_MAX_DAYS = 1.5  # 2→1.5 하향 (need-qty-fix: 재고0 시 과잉발주 방지)
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

# eval-cycle-verdict: NORMAL_ORDER 판정 기준
MIN_DISPLAY_QTY = 2                    # 비행사 최소 진열 개수
EVAL_CYCLE_MAX_DAYS = 7                # 판매주기 상한 (일)
LOW_TURNOVER_THRESHOLD = 1.0           # 저회전 기준 (일평균)

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
DB_SCHEMA_VERSION = 74  # v74: job_runs 스케줄 잡 실행 로그 (job-health-monitor)

# =====================================================================
# Order Unit Qty Integrity v2 (order-unit-qty-integrity-v2)
# =====================================================================
# 묶음 발주가 표준인 카테고리 — order_unit_qty<=1 이면 BGF 빈값 응답 의심 → 차단
# 음료/주류/과자/라면 계열 중분류
BUNDLE_SUSPECT_MID_CDS = {
    # 음료
    "010", "039", "043", "045", "048",
    # 주류
    "049", "050", "052", "053",
    # 과자/제과류
    "014", "015", "016", "017", "018", "019", "020", "029", "030",
    # 라면/면류
    "006", "032",
}
# 긴급 가드 활성화 플래그 (문제 발생 시 즉시 비활성화)
ORDER_UNIT_QTY_GUARD_ENABLED = True

# =====================================================================
# Job Health Monitor (job-health-monitor feature)
# =====================================================================
JOB_HEALTH_TRACKER_ENABLED = True        # 긴급 비활성화용 피처 플래그
JOB_HEALTH_GRACE_PERIOD_MIN = 10         # 예정 시각 + 이 시간 이내 success 없으면 missed
JOB_HEALTH_CHECKER_INTERVAL_SEC = 300    # JobHealthChecker 실행 주기 (5분)
JOB_HEALTH_ALERT_COOLDOWN_HOUR = 1       # 같은 잡 반복 알림 방지 (시간)
JOB_RUNS_RETENTION_DAYS = 30             # job_runs 자동 퍼지 기준
HEARTBEAT_INTERVAL_SEC = 60              # HeartbeatWriter 쓰기 주기
HEARTBEAT_STALE_THRESHOLD_SEC = 600      # Watchdog이 "Scheduler Dead" 판정 임계 (10분)
WATCHDOG_INTERVAL_MIN = 5                # Windows Task Scheduler 주기

# =====================================================================
# 소진율 곡선 설정 (Phase 1.06 / 3단계)
# =====================================================================
DEPLETION_CURVE_ENABLED = True           # 소진율 곡선 기반 보정 활성화
DEPLETION_CURVE_MIN_SAMPLE_DAYS = 20     # 최소 샘플 수 (미만 시 기존 로직 폴백)
DEPLETION_CURVE_FOOD_ONLY = True         # 푸드 카테고리 한정
DEPLETION_CURVE_EMA_ALPHA = 0.1          # EMA 블렌딩 계수 (안정적 이동평균)

# =====================================================================
# 자전 시스템 (self-healing) 실행 분류
# =====================================================================
AUTO_EXEC_HIGH = frozenset({"CLEAR_EXPIRED_BATCH"})
AUTO_EXEC_LOW = frozenset({
    "CLEAR_GHOST_STOCK", "RESTORE_IS_AVAILABLE", "DEACTIVATE_EXPIRED"
})
AUTO_EXEC_INFO = frozenset({"FIX_EXPIRY_TIME", "MANUAL_CHECK_REQUIRED"})
AUTO_EXEC_SKIP = frozenset({"CHECK_DELIVERY_TYPE"})

# =====================================================================
# 디저트 판단 시스템
# =====================================================================
DESSERT_DECISION_ENABLED = True

# 디저트 대량 일괄등록 날짜 — products.created_at가 이 날짜면 실제 입고일로 간주 불가
# TODO: 다매장 확장 시 매장별 일괄등록일 감지 로직으로 대체 (같은 날 N개 이상 등록 → 자동 감지)
DESSERT_BULK_REGISTRATION_DATES = frozenset({'2026-01-25', '2026-01-26', '2026-02-06'})

# =====================================================================
# 음료 판단 시스템
# =====================================================================
BEVERAGE_DECISION_ENABLED = True

# =====================================================================
# 행사 사전 동기화 (Phase 1.68)
# product_details에 행사가 있지만 promotions 테이블에 미등록인 상품을
# Phase 1.68 DirectAPI 조회 대상에 포함하여 promotions 사전 수집
# =====================================================================
PROMO_SYNC_ENABLED = True

# =====================================================================
# 수동 발주 추적
# =====================================================================
ORDER_SOURCE_AUTO = 'auto'         # 자동발주
ORDER_SOURCE_MANUAL = 'manual'     # 수동발주
MANUAL_ORDER_DETECT_TIMES = ['08:00', '21:00']  # 수동 발주 감지 스케줄 (1차: 20:00 입고 후, 2차: 07:00 입고 후)
MANUAL_ORDER_FOOD_DEDUCTION = True  # 푸드 수동발주 차감 활성화 (일반 탭 ORD_CNT>0 → 예측량 차감)
CATEGORY_SITE_BUDGET_ENABLED = True  # 카테고리 총량 예산에서 site(사용자) 발주 차감

# =====================================================================
# 발주 실패 사유 조회
# =====================================================================
FAIL_REASON_UNKNOWN = "알수없음"
FAIL_REASON_MAX_ITEMS = 100
FAIL_REASON_STOP_DATE_INFER = "일시공급불가"  # 정지사유 미기재 + 발주정지일 존재 시

# =====================================================================
# 전 점포 영구 발주 제외 상품 (글로벌)
# - 여기에 등록된 상품은 모든 점포에서 발주 대상에서 제외됩니다
# - 사유: 봉투류 등 발주 불필요 품목
# =====================================================================
GLOBAL_EXCLUDE_ITEMS: dict[str, str] = {
    "2201148653150": "친환경봉투판매용",  # 전 점포 발주 불가
    "2200000079985": "종량(가정)75L",     # 전 점포 발주 불가
}

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
# Direct API 설정 (넥사크로 SSV 직접 호출)
# =====================================================================
USE_DIRECT_API = True                      # Direct API 사용 여부 (False → Selenium 폴백)
DIRECT_API_CONCURRENCY = 5                 # 동시 요청 수 (서버 부하 방지)
DIRECT_API_TIMEOUT_MS = 5000               # 개별 요청 타임아웃 (밀리초)
DIRECT_API_DELAY_MS = 30                   # 요청 간 딜레이 (밀리초)

# =====================================================================
# Direct API 발주 저장 설정 (direct-api-order)
# =====================================================================
DIRECT_API_ORDER_ENABLED = True            # Direct API 발주 저장 (2026-02-27 캡처 검증 완료)
BATCH_GRID_INPUT_ENABLED = True            # Hybrid 배치 그리드 입력 활성화
DIRECT_API_ORDER_MAX_BATCH = 200           # 1회 최대 상품 수 (68개 라이브 검증 완료, 50→200)
DIRECT_API_ORDER_VERIFY = True             # 저장 후 검증 활성화
DIRECT_API_ORDER_DRY_RUN_LOG = True        # dry-run 시 SSV body 로그 출력

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
NEW_PRODUCT_DS_FORCE_REMAINING_DAYS = 5  # 기간 잔여일 이하면 미달성 강제 발주 (D-5 보험)
NEW_PRODUCT_DS_NO_SALE_INTERVAL_DAYS = 3  # 판매/폐기 없을 때 재발주 대기 간격 (일)
NEW_PRODUCT_FOOD_MID_CDS = ["001", "002", "003", "004", "005", "014"]  # 3일발주 대상 중분류 (도시락/주먹밥/김밥/샌드위치/햄버거/디저트)

# 디저트 REDUCE_ORDER 관련 상수
DESSERT_REDUCE_ORDER_MULTIPLIER = 0.5  # REDUCE_ORDER 발주량 승수 (50% 감량)
DESSERT_WASTE_RATE_REDUCE_THRESHOLD = 1.0  # 폐기율 100% 이상 → REDUCE_ORDER
DESSERT_WASTE_RATE_STOP_THRESHOLD = 1.5  # 폐기율 150% 이상 → STOP_RECOMMEND
DESSERT_CONFIRMED_STOP_WASTE_WEEKS = 2  # 폐기율 기준 CONFIRMED_STOP 연속 주 수
DESSERT_CONFIRMED_STOP_WASTE_DAYS = 14  # 폐기율 기준 CONFIRMED_STOP 판단 기간 (일)
DESSERT_CONFIRMED_STOP_CUMULATIVE_WASTE_RATE = 1.0  # 14일 누적 폐기율 100% → 자동 CONFIRMED_STOP
DESSERT_CONFIRMED_STOP_CUMULATIVE_DAYS = 14  # 누적 폐기율 산출 기간

# 디저트 2주 평가 시스템 (v2w)
DESSERT_2WEEK_EVALUATION_ENABLED = True  # False → 기존 보호기간(4주/3주) 복구
DESSERT_PROMO_PROTECTION_ENABLED = True  # 프로모션 진행 중 상품 보호기간 리셋

# 예측 Quick Win (prediction-quick-wins)
ROLLING_BIAS_ENABLED = True              # QW-1: 매장×카테고리 편향 자동보정
BEVERAGE_TEMP_PRIORITY_ENABLED = True    # QW-3: 음료 기온 계수 강화 (25도+)

# 음료 기온 민감도 (QW-3)
# mid_cd: {temp_range: multiplier} — 25도 이상 구간만 적용
BEVERAGE_TEMP_SENSITIVITY = {
    "010": {"25_30": 1.15, "30_plus": 1.30},  # 제조음료
    "039": {"25_30": 1.10, "30_plus": 1.25},  # 과일야채음료
    "043": {"25_30": 1.10, "30_plus": 1.20},  # 차음료
    "046": {"25_30": 1.10, "30_plus": 1.20},  # 요구르트
    "049": {"25_30": 1.20, "30_plus": 1.40},  # 맥주 (가장 민감)
}

# =====================================================================
# 스마트발주 오버라이드 (스마트→수동 전환)
# =====================================================================
SMART_ORDER_OVERRIDE_ENABLED = False      # 기본 OFF (라이브 검증 후 ON)
SMART_OVERRIDE_MIN_QTY = 0               # 예측 qty=0인 상품은 발주 배치에서 제외 (스마트 목록에 유지)

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
DIFF_FEEDBACK_STOCKOUT_EXCLUSION_DAYS = 7  # 제거 후 N일 내 품절 시 페널티 제외

# =====================================================================
# 소분류 내 상품 대체/잠식 감지 (Substitution/Cannibalization Detection)
# =====================================================================
SUBSTITUTION_LOOKBACK_DAYS = 30           # 분석 기간 (일)
SUBSTITUTION_RECENT_WINDOW = 14           # 최근 기간 (일) — 이동평균 비교 윈도우
SUBSTITUTION_DECLINE_THRESHOLD = 0.7      # 감소 판정 임계값 (recent/prior < 0.7)
SUBSTITUTION_GROWTH_THRESHOLD = 1.3       # 증가 판정 임계값 (recent/prior > 1.3)
SUBSTITUTION_TOTAL_CHANGE_LIMIT = 0.20    # 소분류 총량 변화 한도 (20%)
SUBSTITUTION_MIN_DAILY_AVG = 0.3          # 최소 일평균 (이하면 분석 제외)
SUBSTITUTION_MIN_ITEMS_IN_GROUP = 2       # 소분류 내 최소 상품 수
SUBSTITUTION_COEF_MILD = 0.9             # 감소율 30~50%
SUBSTITUTION_COEF_MODERATE = 0.8         # 감소율 50~70%
SUBSTITUTION_COEF_SEVERE = 0.7           # 감소율 70% 이상
SUBSTITUTION_FEEDBACK_EXPIRY_DAYS = 14   # 잠식 피드백 유효기간 (일)

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
    "004": 0.20,  # 샌드위치: 15%→20% 상향 (2026-03-06): 1~2일 유통, 73% 품절, compound floor 도달
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

# ── 소분류(small_cd)별 목표 폐기율 (mid_cd 기준 +-3%p 범위) ──
# 키: (mid_cd, small_cd) 튜플 → 목표 폐기율 (0.0~1.0)
SMALL_CD_TARGET_RATES = {
    # mid_cd=001 (도시락, 기본 20%)
    ("001", "001"): 0.18,   # 정식도시락: 안정수요
    ("001", "268"): 0.22,   # 단품요리: 변동수요
    ("001", "269"): 0.20,   # 세트도시락: 기본 유지
    ("001", "273"): 0.23,   # 프리미엄도시락: 고단가 허용

    # mid_cd=002 (주먹밥, 기본 20%)
    ("002", "002"): 0.18,   # 삼각김밥: 안정수요

    # mid_cd=003 (김밥, 기본 20%)
    ("003", "003"): 0.19,   # 일반김밥

    # mid_cd=004 (샌드위치, 기본 20%)
    ("004", "004"): 0.18,   # 일반샌드위치: 14%→18% (2026-03-06)

    # mid_cd=005 (햄버거, 기본 18%)
    ("005", "005"): 0.17,   # 일반햄버거

    # mid_cd=012 (빵, 기본 12%)
    ("012", "012"): 0.11,   # 식빵류
    ("012", "270"): 0.13,   # 조리빵
}
SMALL_CD_MIN_PRODUCTS = 5  # 소분류 내 상품 수 이 미만이면 mid_cd 폴백

# ── Phase A-1: 강수량 구간 세분화 ──
RAIN_QTY_INTENSITY_ENABLED = True     # 강수량 4단계 (drizzle/light/moderate/heavy)

# ── Phase A-2: 날씨유형(weather_cd_nm) 계수 ──
SKY_CONDITION_ENABLED = True          # 흐림/안개/황사 외출감소 계수

# ── Phase A-3: 급여일 효과 계수 ──
PAYDAY_ENABLED = True                # 10일/25일 급여일 부스트 + 월말 감소

# ── Phase A-4: 미세먼지 예보 계수 ──────────────────────────────────
DUST_PREDICTION_ENABLED = True    # 미세먼지 예보 기반 수요 감소 계수

# 미세먼지 등급 심각도 점수 (높을수록 나쁨)
DUST_GRADE_SCORE = {
    "좋음": 1,
    "보통": 2,
    "한때나쁨": 3,
    "나쁨": 4,
    "매우나쁨": 5,
}

# 미세먼지 등급별 × 카테고리별 수요 감소 계수
# 판정: dust_grade, fine_dust_grade 중 높은 점수 기준
# "나쁨" 이상(점수 ≥ 4)부터 적용, "한때나쁨"(3)은 영향 없음
DUST_COEFFICIENTS = {
    # 나쁨 (score 4): 외출 약간 감소
    "bad": {
        "food":      0.95,   # 001~005, 012
        "beverage":  0.93,   # 039~048
        "ice":       0.90,   # 027~030
        "default":   0.97,   # 기타
    },
    # 매우나쁨 (score 5): 외출 크게 감소
    "very_bad": {
        "food":      0.90,
        "beverage":  0.87,
        "ice":       0.83,
        "default":   0.93,
    },
}

# 미세먼지 대상 카테고리 매핑
DUST_CATEGORY_MAP = {
    "food": ["001", "002", "003", "004", "005", "012"],
    "beverage": [
        "039", "040", "041", "042", "043",
        "044", "045", "046", "047", "048",
    ],
    "ice": ["027", "028", "029", "030"],
}

# ── 동적 폐기 계수 파라미터 (get_dynamic_disuse_coefficient) ──
DISUSE_COEF_FLOOR = 0.65             # 최소 계수 (최대 35% 감량, was 0.5=50%)
DISUSE_COEF_MULTIPLIER = 1.2         # 폐기율 승수 (was 1.5)
DISUSE_MIN_BATCH_COUNT = 14          # 상품별 블렌딩 최소 배치 수 (was 7)
DISUSE_IB_LOOKBACK_DAYS = 30         # inventory_batches 조회 기간 (일)

# ── 운영 이상 감지 임계값 (ops_anomaly.py) ──
OPS_PREDICTION_ACCURACY_RATIO = 1.2      # MAE 20% 악화
OPS_ORDER_FAILURE_RATIO = 1.5            # 실패 50% 증가
OPS_WASTE_RATE_RATIO = 1.5               # 폐기율 1.5배
OPS_COLLECTION_CONSECUTIVE_DAYS = 3      # 수집 3일 연속 실패
OPS_INTEGRITY_CONSECUTIVE_DAYS = 7       # 자전 7일 연속 미해결
OPS_COOLDOWN_DAYS = 14                   # RESOLVED 후 재감지 쿨다운
OPS_DUPLICATE_KEYWORD_THRESHOLD = 2      # 중복 키워드 매칭 임계값

# ── 마일스톤 KPI 목표 (milestone_tracker.py) ──
# 2주 실측 후 조정 예정 (04-19 기준)
MILESTONE_TARGETS = {
    "K1_prediction_stability": 1.05,    # mae_7d/mae_14d 비율 상한
    "K2_waste_rate_food": 0.03,         # food 폐기율 상한 (3%)
    "K2_waste_rate_total": 0.02,        # 전체 폐기율 상한 (2%)
    "K3_order_failure_rate": 0.05,      # 발주 실패율 상한 (5%)
    "K4_integrity_max_consecutive": 3,  # 무결성 연속 이상일 상한
}
MILESTONE_APPROACHING_RATIO = 1.2      # 목표의 120% 이내면 APPROACHING
MILESTONE_COMPLETION_WEEKS = 2         # 연속 달성 주 수 (완료 조건)

# ── 행사 종료 감량 (promotion_adjuster.py) ──
PROMO_END_REDUCTION_DAYS = 5           # 행사 종료 N일 전부터 감량 시작

# ── SLOW 주기 판매 ROP (improved_predictor.py) ──
SLOW_PERIODIC_MIN_SALES = 2            # 60일 내 N회 이상 판매 → 주기적 SLOW (ROP 유지)

# ── food 요일계수 범위 (food.py get_food_weekday_coefficient) ──
FOOD_WEEKDAY_COEF_MIN = 0.60           # 최소 계수 (기존 0.80 → 0.60)
FOOD_WEEKDAY_COEF_MAX = 1.50           # 최대 계수 (기존 1.25 → 1.50)

# ── Claude 자동 대응 (claude_responder.py) ──
CLAUDE_AUTO_RESPOND_ENABLED = True         # False로 즉시 비활성화
CLAUDE_AUTO_RESPOND_MODEL = "sonnet"       # 비용 절감 (sonnet 사용)
CLAUDE_AUTO_RESPOND_MAX_TURNS = 30         # 분석 작업에 Read/Grep 다수 호출 (04-07 max turns 초과로 10→30 상향)
CLAUDE_AUTO_RESPOND_TIMEOUT = 600          # 10분 타임아웃 (max_turns 상향에 맞춰 확대)

# ── 일일 체인 리포트 (daily_chain_report.py) ──
DAILY_CHAIN_REPORT_ENABLED = True          # False로 즉시 비활성화
