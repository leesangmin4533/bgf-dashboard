"""
데이터베이스 모델 및 스키마 정의
- 확장 가능한 테이블 구조
- 마이그레이션 지원
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional

from src.utils.logger import get_logger
from src.settings.constants import DB_SCHEMA_VERSION

logger = get_logger(__name__)

# 기본 DB 경로
DEFAULT_DB_DIR = Path(__file__).parent.parent.parent / "data"
DEFAULT_DB_NAME = "bgf_sales.db"


def get_db_path(db_name: Optional[str] = None) -> Path:
    """DB 파일 경로 반환 — DBRouter로 위임

    Args:
        db_name: DB 파일명 (기본값: bgf_sales.db)
    Returns:
        DB 파일의 절대 경로
    """
    from src.infrastructure.database.connection import DBRouter
    if db_name:
        DEFAULT_DB_DIR.mkdir(parents=True, exist_ok=True)
        return DEFAULT_DB_DIR / db_name
    legacy = DBRouter.get_legacy_db_path()
    if legacy.exists():
        return legacy
    return DBRouter.get_common_db_path()


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """DB 연결 반환 — DBRouter로 위임

    Args:
        db_path: DB 파일 경로 (기본값: 기본 DB 경로)
    Returns:
        Row 팩토리가 설정된 SQLite 연결 객체
    """
    if db_path is None:
        db_path = get_db_path()
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# =============================================================================
# 스키마 정의 (버전 관리)
# =============================================================================

SCHEMA_MIGRATIONS = {
    1: """
    -- 스키마 버전 관리 테이블
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    );

    -- 중분류 마스터 테이블
    CREATE TABLE IF NOT EXISTS mid_categories (
        mid_cd TEXT PRIMARY KEY,
        mid_nm TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    -- 상품 마스터 테이블
    CREATE TABLE IF NOT EXISTS products (
        item_cd TEXT PRIMARY KEY,
        item_nm TEXT NOT NULL,
        mid_cd TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (mid_cd) REFERENCES mid_categories(mid_cd)
    );

    -- 일별 판매 데이터 (핵심 테이블)
    CREATE TABLE IF NOT EXISTS daily_sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        collected_at TEXT NOT NULL,          -- 수집 시점 (타임스탬프)
        sales_date TEXT NOT NULL,            -- 판매 일자 (YYYY-MM-DD)
        item_cd TEXT NOT NULL,
        mid_cd TEXT NOT NULL,
        sale_qty INTEGER DEFAULT 0,          -- 판매수량
        ord_qty INTEGER DEFAULT 0,           -- 발주수량
        buy_qty INTEGER DEFAULT 0,           -- 입고수량
        disuse_qty INTEGER DEFAULT 0,        -- 폐기수량
        stock_qty INTEGER DEFAULT 0,         -- 재고수량
        created_at TEXT NOT NULL,
        UNIQUE(sales_date, item_cd)
    );

    -- 인덱스 생성
    CREATE INDEX IF NOT EXISTS idx_daily_sales_date ON daily_sales(sales_date);
    CREATE INDEX IF NOT EXISTS idx_daily_sales_item ON daily_sales(item_cd);
    CREATE INDEX IF NOT EXISTS idx_daily_sales_mid ON daily_sales(mid_cd);
    CREATE INDEX IF NOT EXISTS idx_products_mid ON products(mid_cd);

    -- 수집 로그 테이블
    CREATE TABLE IF NOT EXISTS collection_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        collected_at TEXT NOT NULL,
        sales_date TEXT NOT NULL,
        total_items INTEGER DEFAULT 0,
        new_items INTEGER DEFAULT 0,
        updated_items INTEGER DEFAULT 0,
        status TEXT NOT NULL,                -- success, failed, partial
        error_message TEXT,
        duration_seconds REAL,
        created_at TEXT NOT NULL
    );

    -- 외부 요인 테이블 (날씨 등 - 추후 확장용)
    CREATE TABLE IF NOT EXISTS external_factors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        factor_date TEXT NOT NULL,
        factor_type TEXT NOT NULL,           -- weather, holiday, event, promotion
        factor_key TEXT NOT NULL,            -- temperature, precipitation, etc.
        factor_value TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(factor_date, factor_type, factor_key)
    );

    CREATE INDEX IF NOT EXISTS idx_external_factors_date ON external_factors(factor_date);
    CREATE INDEX IF NOT EXISTS idx_external_factors_type ON external_factors(factor_type);
    """,

    2: """
    -- 상품 상세 정보 테이블 (발주 관련)
    CREATE TABLE IF NOT EXISTS product_details (
        item_cd TEXT PRIMARY KEY,
        item_nm TEXT,                           -- 상품명
        expiration_days INTEGER,                -- 유통기한 (일)
        orderable_day TEXT DEFAULT '일월화수목금토', -- 발주 가능 요일
        orderable_status TEXT,                  -- 발주 가능 상태
        order_unit_name TEXT DEFAULT '낱개',    -- 발주 단위명 (낱개, 묶음, 박스)
        order_unit_qty INTEGER DEFAULT 1,       -- 발주 단위 수량
        case_unit_qty INTEGER DEFAULT 1,        -- 박스 단위 수량
        lead_time_days INTEGER DEFAULT 1,       -- 리드타임 (발주→입고)
        fetched_at TEXT,                        -- BGF에서 조회한 시점
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (item_cd) REFERENCES products(item_cd)
    );

    CREATE INDEX IF NOT EXISTS idx_product_details_item ON product_details(item_cd);

    -- 발주 이력 테이블
    CREATE TABLE IF NOT EXISTS order_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_date TEXT NOT NULL,               -- 발주일
        item_cd TEXT NOT NULL,
        mid_cd TEXT,                            -- 중분류 코드
        predicted_qty INTEGER DEFAULT 0,        -- 예측 판매량
        recommended_qty INTEGER DEFAULT 0,      -- 추천 발주량
        actual_order_qty INTEGER,               -- 실제 발주량
        current_stock INTEGER DEFAULT 0,        -- 발주 시점 재고
        order_unit TEXT,                        -- 발주 단위
        status TEXT DEFAULT 'pending',          -- pending, ordered, cancelled
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_order_history_date ON order_history(order_date);
    CREATE INDEX IF NOT EXISTS idx_order_history_item ON order_history(item_cd);
    CREATE INDEX IF NOT EXISTS idx_order_history_status ON order_history(status);

    -- 예측 로그 테이블 (모델 성능 추적용)
    CREATE TABLE IF NOT EXISTS prediction_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prediction_date TEXT NOT NULL,          -- 예측 수행일
        target_date TEXT NOT NULL,              -- 예측 대상일
        item_cd TEXT NOT NULL,
        mid_cd TEXT,
        predicted_qty INTEGER,                  -- 예측 판매량
        actual_qty INTEGER,                     -- 실제 판매량 (나중에 업데이트)
        model_type TEXT,                        -- rule_based, ml_xgboost 등
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_prediction_logs_target ON prediction_logs(target_date);
    CREATE INDEX IF NOT EXISTS idx_prediction_logs_item ON prediction_logs(item_cd);
    """,

    3: """
    -- 발주 추적 테이블 (폐기 관리용)
    CREATE TABLE IF NOT EXISTS order_tracking (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_date TEXT NOT NULL,               -- 발주일 (YYYY-MM-DD)
        item_cd TEXT NOT NULL,
        item_nm TEXT,                           -- 상품명
        mid_cd TEXT,                            -- 중분류 코드
        delivery_type TEXT NOT NULL,            -- 배송 차수 (1차/2차)
        order_qty INTEGER DEFAULT 0,            -- 발주 수량
        remaining_qty INTEGER DEFAULT 0,        -- 남은 수량 (판매/폐기로 감소)
        arrival_time TEXT NOT NULL,             -- 도착 예정 시간 (YYYY-MM-DD HH:MM)
        expiry_time TEXT NOT NULL,              -- 폐기 예정 시간 (YYYY-MM-DD HH:MM)
        status TEXT DEFAULT 'ordered',          -- ordered(발주), arrived(도착), selling(판매중), expired(폐기대상), disposed(폐기완료)
        alert_sent INTEGER DEFAULT 0,           -- 폐기 알림 발송 여부 (0/1)
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_order_tracking_expiry ON order_tracking(expiry_time);
    CREATE INDEX IF NOT EXISTS idx_order_tracking_status ON order_tracking(status);
    CREATE INDEX IF NOT EXISTS idx_order_tracking_item ON order_tracking(item_cd);
    CREATE INDEX IF NOT EXISTS idx_order_tracking_date ON order_tracking(order_date);
    """,

    4: """
    -- 입고 이력 테이블 (실제 입고 데이터)
    CREATE TABLE IF NOT EXISTS receiving_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        receiving_date TEXT NOT NULL,           -- 입고일 (YYYY-MM-DD)
        receiving_time TEXT,                    -- 입고시간 (HH:MM)
        chit_no TEXT,                           -- 전표번호
        item_cd TEXT NOT NULL,                  -- 상품코드
        item_nm TEXT,                           -- 상품명
        mid_cd TEXT,                            -- 중분류 코드
        order_date TEXT,                        -- 발주일 (YYYY-MM-DD)
        order_qty INTEGER DEFAULT 0,            -- 발주수량
        receiving_qty INTEGER DEFAULT 0,        -- 입고수량
        delivery_type TEXT,                     -- 배송타입 (cold_1, cold_2, ambient)
        center_nm TEXT,                         -- 센터명 (저온/상온 구분)
        center_cd TEXT,                         -- 센터코드
        created_at TEXT NOT NULL,
        UNIQUE(receiving_date, item_cd, chit_no)
    );

    CREATE INDEX IF NOT EXISTS idx_receiving_date ON receiving_history(receiving_date);
    CREATE INDEX IF NOT EXISTS idx_receiving_item ON receiving_history(item_cd);
    CREATE INDEX IF NOT EXISTS idx_receiving_order_date ON receiving_history(order_date);

    -- order_tracking 테이블에 실제 입고 정보 컬럼 추가
    ALTER TABLE order_tracking ADD COLUMN actual_receiving_qty INTEGER DEFAULT 0;
    ALTER TABLE order_tracking ADD COLUMN actual_arrival_time TEXT;
    """,

    5: """
    -- product_details 테이블에 행사 정보 컬럼 추가
    ALTER TABLE product_details ADD COLUMN promo_type TEXT;
    ALTER TABLE product_details ADD COLUMN promo_name TEXT;
    ALTER TABLE product_details ADD COLUMN promo_start TEXT;
    ALTER TABLE product_details ADD COLUMN promo_end TEXT;
    ALTER TABLE product_details ADD COLUMN promo_updated TEXT;

    CREATE INDEX IF NOT EXISTS idx_product_details_promo ON product_details(promo_type);
    """,

    6: """
    -- prediction_logs 테이블에 개선된 예측기 컬럼 추가
    ALTER TABLE prediction_logs ADD COLUMN adjusted_qty REAL;
    ALTER TABLE prediction_logs ADD COLUMN weekday_coef REAL;
    ALTER TABLE prediction_logs ADD COLUMN confidence TEXT;
    ALTER TABLE prediction_logs ADD COLUMN safety_stock REAL;
    ALTER TABLE prediction_logs ADD COLUMN current_stock INTEGER;
    ALTER TABLE prediction_logs ADD COLUMN order_qty INTEGER;
    """,

    7: """
    -- 실시간 재고/미입고 테이블 (발주 전 조회 데이터 저장)
    CREATE TABLE IF NOT EXISTS realtime_inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_cd TEXT NOT NULL,                  -- 상품코드
        item_nm TEXT,                           -- 상품명 (편의용)
        stock_qty INTEGER DEFAULT 0,            -- 현재 재고 (NOW_QTY)
        pending_qty INTEGER DEFAULT 0,          -- 미입고 수량
        order_unit_qty INTEGER DEFAULT 1,       -- 발주 단위 수량 (입수)
        is_available INTEGER DEFAULT 1,         -- 점포 취급 여부 (1=취급, 0=미취급)
        queried_at TEXT NOT NULL,               -- 조회 시점
        created_at TEXT NOT NULL,
        UNIQUE(item_cd)
    );

    CREATE INDEX IF NOT EXISTS idx_realtime_inventory_item ON realtime_inventory(item_cd);
    CREATE INDEX IF NOT EXISTS idx_realtime_inventory_queried ON realtime_inventory(queried_at);
    CREATE INDEX IF NOT EXISTS idx_realtime_inventory_available ON realtime_inventory(is_available);
    """,

    8: """
    -- 행사 정보 테이블
    CREATE TABLE IF NOT EXISTS promotions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_cd TEXT NOT NULL,                  -- 상품코드
        item_nm TEXT,                           -- 상품명
        promo_type TEXT NOT NULL,               -- 행사 유형: '1+1', '2+1'
        start_date TEXT NOT NULL,               -- 행사 시작일 (YYYY-MM-DD)
        end_date TEXT NOT NULL,                 -- 행사 종료일 (YYYY-MM-DD)
        is_active INTEGER DEFAULT 1,            -- 현재 활성 여부
        collected_at TEXT NOT NULL,             -- 수집 일시
        updated_at TEXT,                        -- 수정 일시
        UNIQUE(item_cd, promo_type, start_date)
    );

    CREATE INDEX IF NOT EXISTS idx_promotions_item ON promotions(item_cd);
    CREATE INDEX IF NOT EXISTS idx_promotions_dates ON promotions(start_date, end_date);
    CREATE INDEX IF NOT EXISTS idx_promotions_active ON promotions(is_active, end_date);

    -- 행사별 판매 통계 테이블
    CREATE TABLE IF NOT EXISTS promotion_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_cd TEXT NOT NULL,                  -- 상품코드
        promo_type TEXT NOT NULL,               -- 'normal', '1+1', '2+1'
        avg_daily_sales REAL,                   -- 일평균 판매량
        total_days INTEGER,                     -- 집계 일수
        total_sales INTEGER,                    -- 총 판매량
        min_daily_sales INTEGER,                -- 최소 일판매
        max_daily_sales INTEGER,                -- 최대 일판매
        std_daily_sales REAL,                   -- 표준편차
        multiplier REAL,                        -- normal 대비 배율
        last_calculated TEXT,                   -- 마지막 계산일
        UNIQUE(item_cd, promo_type)
    );

    CREATE INDEX IF NOT EXISTS idx_promo_stats_item ON promotion_stats(item_cd);

    -- 행사 변경 이력 테이블
    CREATE TABLE IF NOT EXISTS promotion_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_cd TEXT NOT NULL,                  -- 상품코드
        item_nm TEXT,                           -- 상품명
        change_type TEXT NOT NULL,              -- 'start', 'end', 'change'
        change_date TEXT NOT NULL,              -- 변경 적용일
        prev_promo_type TEXT,                   -- 이전 행사 (NULL이면 없었음)
        next_promo_type TEXT,                   -- 이후 행사 (NULL이면 종료)
        expected_sales_change REAL,             -- 예상 판매량 변화율
        is_processed INTEGER DEFAULT 0,         -- 발주 조정 처리 여부
        processed_at TEXT,                      -- 처리 시점
        detected_at TEXT NOT NULL,              -- 감지 시점
        UNIQUE(item_cd, change_date, change_type)
    );

    CREATE INDEX IF NOT EXISTS idx_promo_changes_date ON promotion_changes(change_date);
    CREATE INDEX IF NOT EXISTS idx_promo_changes_unprocessed ON promotion_changes(is_processed, change_date);
    """,

    9: """
    -- 사전 발주 평가 결과 추적 테이블
    CREATE TABLE IF NOT EXISTS eval_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        eval_date TEXT NOT NULL,               -- 평가일 (YYYY-MM-DD)
        item_cd TEXT NOT NULL,                 -- 상품코드
        mid_cd TEXT,                           -- 중분류 코드
        decision TEXT NOT NULL,                -- FORCE_ORDER / URGENT_ORDER / NORMAL_ORDER / PASS / SKIP
        exposure_days REAL,                    -- 예측 노출일수
        popularity_score REAL,                 -- 인기도 점수
        daily_avg REAL,                        -- 일평균 판매량
        current_stock INTEGER,                 -- 평가 시점 재고
        pending_qty INTEGER,                   -- 미입고 수량
        -- 사후 검증 필드 (다음날 데이터 수집 시 기록)
        actual_sold_qty INTEGER,               -- 실제 판매량 (평가일)
        next_day_stock INTEGER,                -- 다음날 재고
        was_stockout INTEGER,                  -- 다음날 품절 여부 (0/1)
        was_waste INTEGER DEFAULT 0,           -- 폐기 발생 여부 (0/1)
        outcome TEXT,                          -- CORRECT / UNDER_ORDER / OVER_ORDER / MISS
        verified_at TEXT,                      -- 검증 시각
        created_at TEXT NOT NULL,
        UNIQUE(eval_date, item_cd)
    );

    CREATE INDEX IF NOT EXISTS idx_eval_outcomes_date ON eval_outcomes(eval_date);
    CREATE INDEX IF NOT EXISTS idx_eval_outcomes_item ON eval_outcomes(item_cd);
    CREATE INDEX IF NOT EXISTS idx_eval_outcomes_decision ON eval_outcomes(decision);
    CREATE INDEX IF NOT EXISTS idx_eval_outcomes_outcome ON eval_outcomes(outcome);

    -- 파라미터 보정 이력 테이블
    CREATE TABLE IF NOT EXISTS calibration_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        calibration_date TEXT NOT NULL,         -- 보정 실행일 (YYYY-MM-DD)
        param_name TEXT NOT NULL,               -- 파라미터 이름
        old_value REAL NOT NULL,                -- 이전 값
        new_value REAL NOT NULL,                -- 새 값
        reason TEXT,                            -- 보정 사유
        accuracy_before REAL,                   -- 보정 전 적중률
        accuracy_after REAL,                    -- 보정 후 기대 적중률
        sample_size INTEGER,                    -- 검증 샘플 수
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_calibration_date ON calibration_history(calibration_date);
    CREATE INDEX IF NOT EXISTS idx_calibration_param ON calibration_history(param_name);
    """,

    10: """
    -- 발주중지(CUT) 상품 플래그 추가
    ALTER TABLE realtime_inventory ADD COLUMN is_cut_item INTEGER DEFAULT 0;
    CREATE INDEX IF NOT EXISTS idx_realtime_inventory_cut ON realtime_inventory(is_cut_item);
    """,

    11: """
    -- 재고 배치 추적 테이블 (FIFO 폐기 관리용)
    -- 비-푸드 상품의 입고 배치별 유통기한 추적
    -- 판매 시 FIFO(선입선출)로 차감, 유통기한 도래 시 잔여수량 = 폐기
    CREATE TABLE IF NOT EXISTS inventory_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_cd TEXT NOT NULL,                  -- 상품코드
        item_nm TEXT,                           -- 상품명
        mid_cd TEXT,                            -- 중분류 코드
        receiving_date TEXT NOT NULL,           -- 입고일 (YYYY-MM-DD)
        receiving_id INTEGER,                   -- receiving_history FK (선택)
        expiration_days INTEGER NOT NULL,       -- 유통기한 (일)
        expiry_date TEXT NOT NULL,              -- 폐기 예정일 (입고일 + 유통기한, YYYY-MM-DD)
        initial_qty INTEGER NOT NULL,           -- 입고 수량
        remaining_qty INTEGER NOT NULL,         -- 잔여 수량 (FIFO 차감)
        status TEXT DEFAULT 'active',           -- active(추적중) / consumed(전량소진) / expired(폐기확정)
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_inv_batch_item ON inventory_batches(item_cd);
    CREATE INDEX IF NOT EXISTS idx_inv_batch_expiry ON inventory_batches(expiry_date);
    CREATE INDEX IF NOT EXISTS idx_inv_batch_status ON inventory_batches(status);
    CREATE INDEX IF NOT EXISTS idx_inv_batch_item_status ON inventory_batches(item_cd, status);
    """,

    12: """
    -- daily_sales에 행사 타입 컬럼 추가 (1+1, 2+1, 빈값=비행사)
    ALTER TABLE daily_sales ADD COLUMN promo_type TEXT DEFAULT '';
    CREATE INDEX IF NOT EXISTS idx_daily_sales_promo ON daily_sales(promo_type);
    """,

    13: """
    -- 상품 상세 정보에 매가/이익율 컬럼 추가
    ALTER TABLE product_details ADD COLUMN sell_price INTEGER;
    ALTER TABLE product_details ADD COLUMN margin_rate REAL;
    """,

    14: """
    -- eval_outcomes ML 컬럼 확장 (과잉발주 보정 Phase 3)
    ALTER TABLE eval_outcomes ADD COLUMN predicted_qty INTEGER;
    ALTER TABLE eval_outcomes ADD COLUMN actual_order_qty INTEGER;
    ALTER TABLE eval_outcomes ADD COLUMN order_status TEXT;
    ALTER TABLE eval_outcomes ADD COLUMN weekday INTEGER;
    ALTER TABLE eval_outcomes ADD COLUMN delivery_batch TEXT;
    ALTER TABLE eval_outcomes ADD COLUMN sell_price INTEGER;
    ALTER TABLE eval_outcomes ADD COLUMN margin_rate REAL;
    ALTER TABLE eval_outcomes ADD COLUMN disuse_qty INTEGER;
    ALTER TABLE eval_outcomes ADD COLUMN promo_type TEXT;
    ALTER TABLE eval_outcomes ADD COLUMN trend_score REAL;
    ALTER TABLE eval_outcomes ADD COLUMN stockout_freq REAL;
    """,

    15: """
    -- 자동발주 상품 캐시 테이블
    CREATE TABLE IF NOT EXISTS auto_order_items (
        item_cd TEXT PRIMARY KEY,
        item_nm TEXT,
        mid_cd TEXT,
        detected_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_auto_order_items_updated
        ON auto_order_items(updated_at);
    """,

    16: """
    -- 스마트발주 상품 캐시 테이블
    CREATE TABLE IF NOT EXISTS smart_order_items (
        item_cd TEXT PRIMARY KEY,
        item_nm TEXT,
        mid_cd TEXT,
        detected_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_smart_order_items_updated
        ON smart_order_items(updated_at);
    """,

    17: """
    -- 앱 설정 테이블 (대시보드 토글 등 프로세스 간 공유)
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT DEFAULT (datetime('now'))
    );

    -- 기본값: 자동발주/스마트발주 상품 제외 ON
    INSERT OR IGNORE INTO app_settings (key, value) VALUES ('EXCLUDE_AUTO_ORDER', 'true');
    INSERT OR IGNORE INTO app_settings (key, value) VALUES ('EXCLUDE_SMART_ORDER', 'true');
    """,

    18: """
    -- 발주 실패 사유 테이블
    CREATE TABLE IF NOT EXISTS order_fail_reasons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        eval_date TEXT NOT NULL,
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        mid_cd TEXT,
        stop_reason TEXT,
        orderable_status TEXT,
        orderable_day TEXT,
        order_status TEXT DEFAULT 'fail',
        checked_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(eval_date, item_cd)
    );
    CREATE INDEX IF NOT EXISTS idx_fail_reasons_date ON order_fail_reasons(eval_date);
    CREATE INDEX IF NOT EXISTS idx_fail_reasons_item ON order_fail_reasons(item_cd);
    CREATE INDEX IF NOT EXISTS idx_fail_reasons_reason ON order_fail_reasons(stop_reason);
    """,

    19: """
    -- order_tracking 테이블에 발주 소스 컬럼 추가
    -- 'auto' = 자동발주 (기본값), 'manual' = 수동발주
    ALTER TABLE order_tracking ADD COLUMN order_source TEXT DEFAULT 'auto';

    CREATE INDEX IF NOT EXISTS idx_order_tracking_source ON order_tracking(order_source);
    """,

    20: """
    -- 데이터 품질 검증 로그 테이블
    CREATE TABLE IF NOT EXISTS validation_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        validated_at TEXT NOT NULL,           -- 검증 시각 (ISO 8601)
        sales_date TEXT NOT NULL,             -- 검증 대상 날짜 (YYYY-MM-DD)
        store_id TEXT NOT NULL DEFAULT '46704',  -- 점포 ID
        validation_type TEXT NOT NULL,        -- 'format', 'duplicate', 'consistency', 'anomaly', 'comprehensive'
        is_passed BOOLEAN NOT NULL,           -- 0=failed, 1=passed
        error_code TEXT,                      -- 'INVALID_ITEM_CD', 'NEGATIVE_QTY', 'DUPLICATE_COLLECTION', 'ANOMALY_3SIGMA'
        error_message TEXT,                   -- 사람이 읽을 수 있는 오류 메시지
        affected_items TEXT,                  -- JSON array: ["8801234567890", ...]
        metadata TEXT,                        -- JSON: {threshold: 3, mean: 30, stddev: 10, value: 120}
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_validation_log_date ON validation_log(sales_date);
    CREATE INDEX IF NOT EXISTS idx_validation_log_type_passed ON validation_log(validation_type, is_passed);
    CREATE INDEX IF NOT EXISTS idx_validation_log_store ON validation_log(store_id);
    """,

    21: """
    -- 멀티 점포 지원: daily_sales 테이블 UNIQUE 제약 수정
    -- UNIQUE(sales_date, item_cd) → UNIQUE(store_id, sales_date, item_cd)

    -- 1. 새 테이블 생성 (올바른 UNIQUE 제약)
    CREATE TABLE daily_sales_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        collected_at TEXT NOT NULL,
        sales_date TEXT NOT NULL,
        item_cd TEXT NOT NULL,
        mid_cd TEXT NOT NULL,
        sale_qty INTEGER DEFAULT 0,
        ord_qty INTEGER DEFAULT 0,
        buy_qty INTEGER DEFAULT 0,
        disuse_qty INTEGER DEFAULT 0,
        stock_qty INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        promo_type TEXT DEFAULT '',
        store_id TEXT DEFAULT '46513',
        UNIQUE(store_id, sales_date, item_cd)
    );

    -- 2. 기존 데이터 복사
    INSERT INTO daily_sales_new
        (id, collected_at, sales_date, item_cd, mid_cd, sale_qty, ord_qty,
         buy_qty, disuse_qty, stock_qty, created_at, promo_type, store_id)
    SELECT
        id, collected_at, sales_date, item_cd, mid_cd, sale_qty, ord_qty,
        buy_qty, disuse_qty, stock_qty, created_at,
        COALESCE(promo_type, ''),
        '46513'
    FROM daily_sales;

    -- 3. 기존 테이블 삭제
    DROP TABLE daily_sales;

    -- 4. 새 테이블 이름 변경
    ALTER TABLE daily_sales_new RENAME TO daily_sales;

    -- 5. 인덱스 재생성
    CREATE INDEX idx_daily_sales_date ON daily_sales(sales_date);
    CREATE INDEX idx_daily_sales_item ON daily_sales(item_cd);
    CREATE INDEX idx_daily_sales_mid ON daily_sales(mid_cd);
    CREATE INDEX idx_daily_sales_promo ON daily_sales(promo_type);
    CREATE INDEX idx_daily_sales_store ON daily_sales(store_id, sales_date);
    """,
    22: """
    -- 멀티 점포 지원: order_tracking, collection_logs에 store_id 추가 (v22)

    -- 1. order_tracking 마이그레이션
    CREATE TABLE order_tracking_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT DEFAULT '46513',
        order_date TEXT,
        item_cd TEXT,
        item_nm TEXT,
        mid_cd TEXT,
        delivery_type TEXT,
        order_qty INTEGER,
        remaining_qty INTEGER,
        arrival_time TEXT,
        expiry_time TEXT,
        status TEXT,
        alert_sent INTEGER,
        created_at TEXT,
        updated_at TEXT,
        actual_receiving_qty INTEGER,
        actual_arrival_time TEXT,
        order_source TEXT,
        UNIQUE(store_id, order_date, item_cd)
    );

    INSERT INTO order_tracking_new
    SELECT
        id, '46513', order_date, item_cd, item_nm, mid_cd, delivery_type,
        order_qty, remaining_qty, arrival_time, expiry_time, status, alert_sent,
        created_at, updated_at, actual_receiving_qty, actual_arrival_time, order_source
    FROM order_tracking;

    DROP TABLE order_tracking;
    ALTER TABLE order_tracking_new RENAME TO order_tracking;
    CREATE INDEX idx_order_tracking_store ON order_tracking(store_id, order_date);

    -- 2. collection_logs 마이그레이션
    CREATE TABLE collection_logs_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT DEFAULT '46513',
        collected_at TEXT NOT NULL,
        sales_date TEXT,
        total_items INTEGER,
        new_items INTEGER,
        updated_items INTEGER,
        status TEXT,
        error_message TEXT,
        duration_seconds REAL,
        created_at TEXT,
        UNIQUE(store_id, collected_at)
    );

    INSERT INTO collection_logs_new
    SELECT
        id, '46513', collected_at, sales_date, total_items, new_items, updated_items,
        status, error_message, duration_seconds, created_at
    FROM collection_logs;

    DROP TABLE collection_logs;
    ALTER TABLE collection_logs_new RENAME TO collection_logs;
    CREATE INDEX idx_collection_logs_store ON collection_logs(store_id, collected_at);
    """,
    23: """
    -- 멀티 점포 지원: order_fail_reasons에 store_id 추가 및 UNIQUE 제약 수정 (v23)

    -- order_fail_reasons 마이그레이션
    CREATE TABLE order_fail_reasons_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT DEFAULT '46513',
        eval_date TEXT NOT NULL,
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        mid_cd TEXT,
        stop_reason TEXT,
        orderable_status TEXT,
        orderable_day TEXT,
        order_status TEXT,
        checked_at TEXT,
        created_at TEXT,
        UNIQUE(store_id, eval_date, item_cd)
    );

    INSERT INTO order_fail_reasons_new
    SELECT
        id, '46513', eval_date, item_cd, item_nm, mid_cd, stop_reason,
        orderable_status, orderable_day, order_status, checked_at, created_at
    FROM order_fail_reasons;

    DROP TABLE order_fail_reasons;
    ALTER TABLE order_fail_reasons_new RENAME TO order_fail_reasons;
    CREATE INDEX idx_order_fail_reasons_store ON order_fail_reasons(store_id, eval_date);
    """,

    24: """
    -- 멀티 스토어 지원: realtime_inventory에 store_id 추가 (v24)

    -- realtime_inventory 마이그레이션
    CREATE TABLE realtime_inventory_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT DEFAULT '46513',
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        stock_qty INTEGER DEFAULT 0,
        pending_qty INTEGER DEFAULT 0,
        order_unit_qty INTEGER DEFAULT 1,
        is_available INTEGER DEFAULT 1,
        is_cut_item INTEGER DEFAULT 0,
        queried_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(store_id, item_cd)
    );

    INSERT INTO realtime_inventory_new (
        id, store_id, item_cd, item_nm, stock_qty, pending_qty,
        order_unit_qty, is_available, is_cut_item, queried_at, created_at
    )
    SELECT
        id, '46513', item_cd, item_nm, stock_qty, pending_qty,
        order_unit_qty, is_available, is_cut_item, queried_at, created_at
    FROM realtime_inventory;

    DROP TABLE realtime_inventory;
    ALTER TABLE realtime_inventory_new RENAME TO realtime_inventory;

    CREATE INDEX idx_realtime_inventory_store ON realtime_inventory(store_id, item_cd);
    CREATE INDEX idx_realtime_inventory_queried ON realtime_inventory(queried_at);
    CREATE INDEX idx_realtime_inventory_available ON realtime_inventory(is_available);
    CREATE INDEX idx_realtime_inventory_cut ON realtime_inventory(is_cut_item);
    """,

    25: """
    -- 멀티 스토어 병렬화: inventory_batches에 store_id 추가 (v25)
    -- (order_tracking은 v22에서 이미 store_id 추가됨)

    ALTER TABLE inventory_batches ADD COLUMN store_id TEXT DEFAULT '46513';
    CREATE INDEX IF NOT EXISTS idx_inventory_batches_store ON inventory_batches(store_id);
    """,

    26: """
    -- 멀티 스토어 지원: 9개 테이블에 store_id 추가 (v26)

    -- 1. prediction_logs — 단순 ALTER (PK=AUTOINCREMENT, UNIQUE 없음)
    ALTER TABLE prediction_logs ADD COLUMN store_id TEXT DEFAULT '46513';
    CREATE INDEX IF NOT EXISTS idx_prediction_logs_store ON prediction_logs(store_id, prediction_date);

    -- 2. order_history — 단순 ALTER (PK=AUTOINCREMENT, UNIQUE 없음)
    ALTER TABLE order_history ADD COLUMN store_id TEXT DEFAULT '46513';
    CREATE INDEX IF NOT EXISTS idx_order_history_store ON order_history(store_id, order_date);

    -- 3. eval_outcomes — UNIQUE(eval_date, item_cd) → UNIQUE(store_id, eval_date, item_cd)
    CREATE TABLE eval_outcomes_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT DEFAULT '46513',
        eval_date TEXT NOT NULL,
        item_cd TEXT NOT NULL,
        mid_cd TEXT,
        decision TEXT NOT NULL,
        exposure_days REAL,
        popularity_score REAL,
        daily_avg REAL,
        current_stock INTEGER,
        pending_qty INTEGER,
        actual_sold_qty INTEGER,
        next_day_stock INTEGER,
        was_stockout INTEGER,
        was_waste INTEGER DEFAULT 0,
        outcome TEXT,
        verified_at TEXT,
        created_at TEXT NOT NULL,
        predicted_qty INTEGER,
        actual_order_qty INTEGER,
        order_status TEXT,
        weekday INTEGER,
        delivery_batch TEXT,
        sell_price INTEGER,
        margin_rate REAL,
        disuse_qty INTEGER,
        promo_type TEXT,
        trend_score REAL,
        stockout_freq REAL,
        UNIQUE(store_id, eval_date, item_cd)
    );
    INSERT INTO eval_outcomes_new
        (id, store_id, eval_date, item_cd, mid_cd, decision, exposure_days,
         popularity_score, daily_avg, current_stock, pending_qty,
         actual_sold_qty, next_day_stock, was_stockout, was_waste, outcome, verified_at, created_at,
         predicted_qty, actual_order_qty, order_status, weekday, delivery_batch,
         sell_price, margin_rate, disuse_qty, promo_type, trend_score, stockout_freq)
    SELECT
        id, '46513', eval_date, item_cd, mid_cd, decision, exposure_days,
        popularity_score, daily_avg, current_stock, pending_qty,
        actual_sold_qty, next_day_stock, was_stockout, was_waste, outcome, verified_at, created_at,
        predicted_qty, actual_order_qty, order_status, weekday, delivery_batch,
        sell_price, margin_rate, disuse_qty, promo_type, trend_score, stockout_freq
    FROM eval_outcomes;
    DROP TABLE eval_outcomes;
    ALTER TABLE eval_outcomes_new RENAME TO eval_outcomes;
    CREATE INDEX idx_eval_outcomes_date ON eval_outcomes(eval_date);
    CREATE INDEX idx_eval_outcomes_item ON eval_outcomes(item_cd);
    CREATE INDEX idx_eval_outcomes_decision ON eval_outcomes(decision);
    CREATE INDEX idx_eval_outcomes_outcome ON eval_outcomes(outcome);
    CREATE INDEX idx_eval_outcomes_store ON eval_outcomes(store_id, eval_date);

    -- 4. promotions — UNIQUE(item_cd, promo_type, start_date) → UNIQUE(store_id, item_cd, promo_type, start_date)
    CREATE TABLE promotions_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT DEFAULT '46513',
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        promo_type TEXT NOT NULL,
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        collected_at TEXT NOT NULL,
        updated_at TEXT,
        UNIQUE(store_id, item_cd, promo_type, start_date)
    );
    INSERT INTO promotions_new
        (id, store_id, item_cd, item_nm, promo_type, start_date, end_date, is_active, collected_at, updated_at)
    SELECT
        id, '46513', item_cd, item_nm, promo_type, start_date, end_date, is_active, collected_at, updated_at
    FROM promotions;
    DROP TABLE promotions;
    ALTER TABLE promotions_new RENAME TO promotions;
    CREATE INDEX idx_promotions_item ON promotions(item_cd);
    CREATE INDEX idx_promotions_dates ON promotions(start_date, end_date);
    CREATE INDEX idx_promotions_active ON promotions(is_active, end_date);
    CREATE INDEX idx_promotions_store ON promotions(store_id, item_cd);

    -- 5. promotion_stats — UNIQUE(item_cd, promo_type) → UNIQUE(store_id, item_cd, promo_type)
    CREATE TABLE promotion_stats_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT DEFAULT '46513',
        item_cd TEXT NOT NULL,
        promo_type TEXT NOT NULL,
        avg_daily_sales REAL,
        total_days INTEGER,
        total_sales INTEGER,
        min_daily_sales INTEGER,
        max_daily_sales INTEGER,
        std_daily_sales REAL,
        multiplier REAL,
        last_calculated TEXT,
        UNIQUE(store_id, item_cd, promo_type)
    );
    INSERT INTO promotion_stats_new
        (id, store_id, item_cd, promo_type, avg_daily_sales, total_days, total_sales,
         min_daily_sales, max_daily_sales, std_daily_sales, multiplier, last_calculated)
    SELECT
        id, '46513', item_cd, promo_type, avg_daily_sales, total_days, total_sales,
        min_daily_sales, max_daily_sales, std_daily_sales, multiplier, last_calculated
    FROM promotion_stats;
    DROP TABLE promotion_stats;
    ALTER TABLE promotion_stats_new RENAME TO promotion_stats;
    CREATE INDEX idx_promo_stats_item ON promotion_stats(item_cd);
    CREATE INDEX idx_promo_stats_store ON promotion_stats(store_id, item_cd);

    -- 6. promotion_changes — UNIQUE(item_cd, change_date, change_type) → UNIQUE(store_id, item_cd, change_date, change_type)
    CREATE TABLE promotion_changes_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT DEFAULT '46513',
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        change_type TEXT NOT NULL,
        change_date TEXT NOT NULL,
        prev_promo_type TEXT,
        next_promo_type TEXT,
        expected_sales_change REAL,
        is_processed INTEGER DEFAULT 0,
        processed_at TEXT,
        detected_at TEXT NOT NULL,
        UNIQUE(store_id, item_cd, change_date, change_type)
    );
    INSERT INTO promotion_changes_new
        (id, store_id, item_cd, item_nm, change_type, change_date,
         prev_promo_type, next_promo_type, expected_sales_change,
         is_processed, processed_at, detected_at)
    SELECT
        id, '46513', item_cd, item_nm, change_type, change_date,
        prev_promo_type, next_promo_type, expected_sales_change,
        is_processed, processed_at, detected_at
    FROM promotion_changes;
    DROP TABLE promotion_changes;
    ALTER TABLE promotion_changes_new RENAME TO promotion_changes;
    CREATE INDEX idx_promo_changes_date ON promotion_changes(change_date);
    CREATE INDEX idx_promo_changes_unprocessed ON promotion_changes(is_processed, change_date);
    CREATE INDEX idx_promo_changes_store ON promotion_changes(store_id, item_cd);

    -- 7. receiving_history — UNIQUE(receiving_date, item_cd, chit_no) → UNIQUE(store_id, receiving_date, item_cd, chit_no)
    CREATE TABLE receiving_history_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT DEFAULT '46513',
        receiving_date TEXT NOT NULL,
        receiving_time TEXT,
        chit_no TEXT,
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        mid_cd TEXT,
        order_date TEXT,
        order_qty INTEGER DEFAULT 0,
        receiving_qty INTEGER DEFAULT 0,
        delivery_type TEXT,
        center_nm TEXT,
        center_cd TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(store_id, receiving_date, item_cd, chit_no)
    );
    INSERT INTO receiving_history_new
        (id, store_id, receiving_date, receiving_time, chit_no, item_cd, item_nm, mid_cd,
         order_date, order_qty, receiving_qty, delivery_type, center_nm, center_cd, created_at)
    SELECT
        id, '46513', receiving_date, receiving_time, chit_no, item_cd, item_nm, mid_cd,
        order_date, order_qty, receiving_qty, delivery_type, center_nm, center_cd, created_at
    FROM receiving_history;
    DROP TABLE receiving_history;
    ALTER TABLE receiving_history_new RENAME TO receiving_history;
    CREATE INDEX idx_receiving_date ON receiving_history(receiving_date);
    CREATE INDEX idx_receiving_item ON receiving_history(item_cd);
    CREATE INDEX idx_receiving_order_date ON receiving_history(order_date);
    CREATE INDEX idx_receiving_store ON receiving_history(store_id, receiving_date);

    -- 8. auto_order_items — PRIMARY KEY(item_cd) → PRIMARY KEY(store_id, item_cd)
    CREATE TABLE auto_order_items_new (
        store_id TEXT DEFAULT '46513',
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        mid_cd TEXT,
        detected_at TEXT,
        updated_at TEXT,
        PRIMARY KEY(store_id, item_cd)
    );
    INSERT INTO auto_order_items_new (store_id, item_cd, item_nm, mid_cd, detected_at, updated_at)
    SELECT '46513', item_cd, item_nm, mid_cd, detected_at, updated_at
    FROM auto_order_items;
    DROP TABLE auto_order_items;
    ALTER TABLE auto_order_items_new RENAME TO auto_order_items;
    CREATE INDEX idx_auto_order_items_updated ON auto_order_items(updated_at);
    CREATE INDEX idx_auto_order_items_store ON auto_order_items(store_id);

    -- 9. smart_order_items — PRIMARY KEY(item_cd) → PRIMARY KEY(store_id, item_cd)
    CREATE TABLE smart_order_items_new (
        store_id TEXT DEFAULT '46513',
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        mid_cd TEXT,
        detected_at TEXT,
        updated_at TEXT,
        PRIMARY KEY(store_id, item_cd)
    );
    INSERT INTO smart_order_items_new (store_id, item_cd, item_nm, mid_cd, detected_at, updated_at)
    SELECT '46513', item_cd, item_nm, mid_cd, detected_at, updated_at
    FROM smart_order_items;
    DROP TABLE smart_order_items;
    ALTER TABLE smart_order_items_new RENAME TO smart_order_items;
    CREATE INDEX idx_smart_order_items_updated ON smart_order_items(updated_at);
    CREATE INDEX idx_smart_order_items_store ON smart_order_items(store_id);
    """,

    27: """
    -- v27: v26 불완전 마이그레이션 수리
    -- v26의 executescript가 'duplicate column' 에러로 중단되어
    -- eval_outcomes, promotions 테이블의 UNIQUE 재생성이 누락됨

    -- 1. eval_outcomes — UNIQUE(eval_date, item_cd) → UNIQUE(store_id, eval_date, item_cd)
    CREATE TABLE IF NOT EXISTS eval_outcomes_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT DEFAULT '46513',
        eval_date TEXT NOT NULL,
        item_cd TEXT NOT NULL,
        mid_cd TEXT,
        decision TEXT NOT NULL,
        exposure_days REAL,
        popularity_score REAL,
        daily_avg REAL,
        current_stock INTEGER,
        pending_qty INTEGER,
        actual_sold_qty INTEGER,
        next_day_stock INTEGER,
        was_stockout INTEGER,
        was_waste INTEGER DEFAULT 0,
        outcome TEXT,
        verified_at TEXT,
        created_at TEXT NOT NULL,
        predicted_qty INTEGER,
        actual_order_qty INTEGER,
        order_status TEXT,
        weekday INTEGER,
        delivery_batch TEXT,
        sell_price INTEGER,
        margin_rate REAL,
        disuse_qty INTEGER,
        promo_type TEXT,
        trend_score REAL,
        stockout_freq REAL,
        UNIQUE(store_id, eval_date, item_cd)
    );
    INSERT OR IGNORE INTO eval_outcomes_new
        (id, store_id, eval_date, item_cd, mid_cd, decision, exposure_days,
         popularity_score, daily_avg, current_stock, pending_qty,
         actual_sold_qty, next_day_stock, was_stockout, was_waste, outcome, verified_at, created_at,
         predicted_qty, actual_order_qty, order_status, weekday, delivery_batch,
         sell_price, margin_rate, disuse_qty, promo_type, trend_score, stockout_freq)
    SELECT
        id, COALESCE(store_id, '46513'), eval_date, item_cd, mid_cd, decision, exposure_days,
        popularity_score, daily_avg, current_stock, pending_qty,
        actual_sold_qty, next_day_stock, was_stockout, was_waste, outcome, verified_at, created_at,
        predicted_qty, actual_order_qty, order_status, weekday, delivery_batch,
        sell_price, margin_rate, disuse_qty, promo_type, trend_score, stockout_freq
    FROM eval_outcomes;
    DROP TABLE eval_outcomes;
    ALTER TABLE eval_outcomes_new RENAME TO eval_outcomes;
    CREATE INDEX IF NOT EXISTS idx_eval_outcomes_date ON eval_outcomes(eval_date);
    CREATE INDEX IF NOT EXISTS idx_eval_outcomes_item ON eval_outcomes(item_cd);
    CREATE INDEX IF NOT EXISTS idx_eval_outcomes_decision ON eval_outcomes(decision);
    CREATE INDEX IF NOT EXISTS idx_eval_outcomes_outcome ON eval_outcomes(outcome);
    CREATE INDEX IF NOT EXISTS idx_eval_outcomes_store ON eval_outcomes(store_id, eval_date);

    -- 2. promotions — UNIQUE(item_cd, promo_type, start_date) → UNIQUE(store_id, item_cd, promo_type, start_date)
    CREATE TABLE IF NOT EXISTS promotions_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT DEFAULT '46513',
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        promo_type TEXT NOT NULL,
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        collected_at TEXT NOT NULL,
        updated_at TEXT,
        UNIQUE(store_id, item_cd, promo_type, start_date)
    );
    INSERT OR IGNORE INTO promotions_new
        (id, store_id, item_cd, item_nm, promo_type, start_date, end_date, is_active, collected_at, updated_at)
    SELECT
        id, COALESCE(store_id, '46513'), item_cd, item_nm, promo_type, start_date, end_date, is_active, collected_at, updated_at
    FROM promotions;
    DROP TABLE promotions;
    ALTER TABLE promotions_new RENAME TO promotions;
    CREATE INDEX IF NOT EXISTS idx_promotions_item ON promotions(item_cd);
    CREATE INDEX IF NOT EXISTS idx_promotions_dates ON promotions(start_date, end_date);
    CREATE INDEX IF NOT EXISTS idx_promotions_active ON promotions(is_active, end_date);
    CREATE INDEX IF NOT EXISTS idx_promotions_store ON promotions(store_id, item_cd);

    -- 3. prediction_logs 누락 인덱스
    CREATE INDEX IF NOT EXISTS idx_prediction_logs_store ON prediction_logs(store_id, prediction_date);
    """,

    28: """
    -- v28: 신상품 도입 현황 3개 테이블 추가

    CREATE TABLE IF NOT EXISTS new_product_status (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        month_ym TEXT NOT NULL,
        week_no INTEGER NOT NULL,
        period TEXT,
        doip_rate REAL,
        item_cnt INTEGER DEFAULT 0,
        item_ad_cnt INTEGER DEFAULT 0,
        doip_cnt INTEGER DEFAULT 0,
        midoip_cnt INTEGER DEFAULT 0,
        ds_rate REAL,
        ds_item_cnt INTEGER DEFAULT 0,
        ds_cnt INTEGER DEFAULT 0,
        mids_cnt INTEGER DEFAULT 0,
        doip_score REAL,
        ds_score REAL,
        tot_score REAL,
        supp_pay_amt INTEGER DEFAULT 0,
        sta_dd TEXT,
        end_dd TEXT,
        week_cont TEXT,
        collected_at TEXT NOT NULL,
        UNIQUE(store_id, month_ym, week_no)
    );

    CREATE TABLE IF NOT EXISTS new_product_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        month_ym TEXT NOT NULL,
        week_no INTEGER NOT NULL,
        item_type TEXT NOT NULL,
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        small_nm TEXT,
        ord_pss_nm TEXT,
        week_cont TEXT,
        ds_yn TEXT,
        is_ordered INTEGER DEFAULT 0,
        ordered_at TEXT,
        collected_at TEXT NOT NULL,
        UNIQUE(store_id, month_ym, week_no, item_type, item_cd)
    );

    CREATE TABLE IF NOT EXISTS new_product_monthly (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        month_ym TEXT NOT NULL,
        doip_item_cnt INTEGER DEFAULT 0,
        doip_cnt INTEGER DEFAULT 0,
        doip_rate REAL,
        doip_score REAL,
        ds_item_cnt INTEGER DEFAULT 0,
        ds_cnt INTEGER DEFAULT 0,
        ds_rate REAL,
        ds_score REAL,
        tot_score REAL,
        supp_pay_amt INTEGER DEFAULT 0,
        next_min_score REAL,
        next_max_score REAL,
        next_supp_pay_amt INTEGER DEFAULT 0,
        collected_at TEXT NOT NULL,
        UNIQUE(store_id, month_ym)
    );

    CREATE INDEX IF NOT EXISTS idx_new_product_status_month ON new_product_status(store_id, month_ym);
    CREATE INDEX IF NOT EXISTS idx_new_product_items_type ON new_product_items(store_id, month_ym, item_type);
    CREATE INDEX IF NOT EXISTS idx_new_product_items_item ON new_product_items(item_cd);
    CREATE INDEX IF NOT EXISTS idx_new_product_items_ordered ON new_product_items(is_ordered);
    CREATE INDEX IF NOT EXISTS idx_new_product_monthly_month ON new_product_monthly(store_id, month_ym);
    """,

    29: """
    -- v29: 연관 상품 분석 (association_rules) 테이블 추가

    CREATE TABLE IF NOT EXISTS association_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_a TEXT NOT NULL,
        item_b TEXT NOT NULL,
        rule_level TEXT NOT NULL,
        support REAL NOT NULL,
        confidence REAL NOT NULL,
        lift REAL NOT NULL,
        correlation REAL DEFAULT 0,
        sample_days INTEGER NOT NULL,
        computed_at TEXT NOT NULL,
        store_id TEXT,
        UNIQUE(item_a, item_b, rule_level)
    );

    CREATE INDEX IF NOT EXISTS idx_assoc_item_b ON association_rules(item_b, rule_level);
    CREATE INDEX IF NOT EXISTS idx_assoc_lift ON association_rules(lift DESC);
    """,

    30: """
    -- v30: 폐기 원인 분석 (waste_cause_analysis) 테이블 추가

    CREATE TABLE IF NOT EXISTS waste_cause_analysis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        analysis_date TEXT NOT NULL,
        waste_date TEXT NOT NULL,
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        mid_cd TEXT,
        waste_qty INTEGER NOT NULL DEFAULT 0,
        waste_source TEXT NOT NULL,
        primary_cause TEXT NOT NULL,
        secondary_cause TEXT,
        confidence REAL DEFAULT 0.0,
        order_qty INTEGER,
        daily_avg REAL,
        predicted_qty INTEGER,
        actual_sold_qty INTEGER,
        expiration_days INTEGER,
        trend_ratio REAL,
        sell_day_ratio REAL,
        weather_factor TEXT,
        promo_factor TEXT,
        holiday_factor TEXT,
        feedback_action TEXT NOT NULL DEFAULT 'DEFAULT',
        feedback_multiplier REAL DEFAULT 1.0,
        feedback_expiry_date TEXT,
        is_applied INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        UNIQUE(store_id, waste_date, item_cd)
    );

    CREATE INDEX IF NOT EXISTS idx_waste_cause_store_date
        ON waste_cause_analysis(store_id, waste_date);
    CREATE INDEX IF NOT EXISTS idx_waste_cause_item
        ON waste_cause_analysis(item_cd);
    CREATE INDEX IF NOT EXISTS idx_waste_cause_cause
        ON waste_cause_analysis(primary_cause);
    CREATE INDEX IF NOT EXISTS idx_waste_cause_feedback
        ON waste_cause_analysis(is_applied, feedback_expiry_date);
    """,

    31: """
    -- v31: app_settings를 매장별 DB로 이동 (매장별 독립 설정)
    -- 레거시 단일 DB에서는 이미 app_settings가 존재하므로 무시됨
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT DEFAULT (datetime('now'))
    );

    INSERT OR IGNORE INTO app_settings (key, value) VALUES ('EXCLUDE_AUTO_ORDER', 'true');
    INSERT OR IGNORE INTO app_settings (key, value) VALUES ('EXCLUDE_SMART_ORDER', 'true');
    """,

    32: """
    -- v32: 푸드 폐기율 자동 보정 이력 테이블
    CREATE TABLE IF NOT EXISTS food_waste_calibration (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        mid_cd TEXT NOT NULL,
        calibration_date TEXT NOT NULL,

        -- 관측값
        actual_waste_rate REAL NOT NULL,
        target_waste_rate REAL NOT NULL,
        error REAL NOT NULL,
        sample_days INTEGER NOT NULL,
        total_order_qty INTEGER,
        total_waste_qty INTEGER,
        total_sold_qty INTEGER,

        -- 조정된 파라미터
        param_name TEXT,
        old_value REAL,
        new_value REAL,

        -- 현재 파라미터 스냅샷 (JSON)
        current_params TEXT,

        created_at TEXT NOT NULL,
        UNIQUE(store_id, mid_cd, calibration_date)
    );
    """,

    33: """
    -- v33: 폐기 전표 수집 + 검증 로그 테이블

    CREATE TABLE IF NOT EXISTS waste_slips (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        chit_date TEXT NOT NULL,
        chit_no TEXT NOT NULL,
        chit_flag TEXT,
        chit_id TEXT,
        chit_id_nm TEXT,
        item_cnt INTEGER DEFAULT 0,
        center_cd TEXT,
        center_nm TEXT,
        wonga_amt REAL DEFAULT 0,
        maega_amt REAL DEFAULT 0,
        nap_plan_ymd TEXT,
        conf_id TEXT,
        cre_ymdhms TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        UNIQUE(store_id, chit_date, chit_no)
    );

    CREATE INDEX IF NOT EXISTS idx_waste_slips_store_date
        ON waste_slips(store_id, chit_date);

    CREATE TABLE IF NOT EXISTS waste_verification_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        verification_date TEXT NOT NULL,
        slip_count INTEGER DEFAULT 0,
        slip_item_count INTEGER DEFAULT 0,
        daily_sales_disuse_count INTEGER DEFAULT 0,
        gap INTEGER DEFAULT 0,
        gap_percentage REAL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'UNKNOWN',
        details TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(store_id, verification_date)
    );

    CREATE INDEX IF NOT EXISTS idx_waste_verify_store_date
        ON waste_verification_log(store_id, verification_date);
    """,
    34: """
    -- v34: 폐기 전표 상세 품목 테이블

    CREATE TABLE IF NOT EXISTS waste_slip_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        chit_date TEXT NOT NULL,
        chit_no TEXT NOT NULL,
        chit_seq INTEGER,
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        large_cd TEXT,
        large_nm TEXT,
        qty INTEGER DEFAULT 0,
        wonga_price REAL DEFAULT 0,
        wonga_amt REAL DEFAULT 0,
        maega_price REAL DEFAULT 0,
        maega_amt REAL DEFAULT 0,
        cust_nm TEXT,
        center_nm TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        UNIQUE(store_id, chit_date, chit_no, item_cd)
    );

    CREATE INDEX IF NOT EXISTS idx_wsi_store_date
        ON waste_slip_items(store_id, chit_date);
    CREATE INDEX IF NOT EXISTS idx_wsi_item
        ON waste_slip_items(item_cd);
    """,

    35: """
    -- v35: stores 테이블 비밀번호 평문 제거 (환경변수로 이관)
    UPDATE stores SET bgf_password = 'MIGRATED_TO_ENV'
    WHERE bgf_password IS NOT NULL
      AND bgf_password != ''
      AND bgf_password NOT LIKE '%$%'
      AND bgf_password != 'MIGRATED_TO_ENV';
    """,

    36: """
    -- v36: prediction_logs 재고 소스 추적 컬럼 추가 (재고 불일치 진단용)
    ALTER TABLE prediction_logs ADD COLUMN stock_source TEXT;
    ALTER TABLE prediction_logs ADD COLUMN pending_source TEXT;
    ALTER TABLE prediction_logs ADD COLUMN is_stock_stale INTEGER DEFAULT 0;
    """,

    37: """
    -- v37: 발주정지 상품 테이블 (common.db)
    CREATE TABLE IF NOT EXISTS stopped_items (
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        stop_reason TEXT,
        first_detected_at TEXT NOT NULL,
        last_detected_at TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        UNIQUE(item_cd)
    );
    CREATE INDEX IF NOT EXISTS idx_stopped_items_active ON stopped_items(is_active);
    """,

    38: """
    -- v38: 대시보드 인증 시스템 (dashboard_users)
    CREATE TABLE IF NOT EXISTS dashboard_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        store_id TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'viewer',
        is_active INTEGER DEFAULT 1,
        full_name TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_login_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_dashboard_users_username ON dashboard_users(username);
    CREATE INDEX IF NOT EXISTS idx_dashboard_users_store ON dashboard_users(store_id);
    """,

    39: """
    -- v39: 회원가입 요청 (signup_requests)
    CREATE TABLE IF NOT EXISTS signup_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        phone TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        reject_reason TEXT,
        created_at TEXT NOT NULL,
        reviewed_at TEXT,
        reviewed_by INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_signup_requests_status ON signup_requests(status);
    CREATE INDEX IF NOT EXISTS idx_signup_requests_store ON signup_requests(store_id);
    """,

    40: """
    -- v40: 베이지안 파라미터 최적화 이력
    CREATE TABLE IF NOT EXISTS bayesian_optimization_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        optimization_date TEXT NOT NULL,
        iteration INTEGER DEFAULT 0,
        objective_value REAL,
        accuracy_error REAL,
        waste_rate_error REAL,
        stockout_rate REAL,
        over_order_ratio REAL,
        params_before TEXT,
        params_after TEXT,
        params_delta TEXT,
        algorithm TEXT DEFAULT 'gp',
        n_trials INTEGER DEFAULT 30,
        best_trial INTEGER DEFAULT 0,
        eval_period_start TEXT,
        eval_period_end TEXT,
        applied INTEGER DEFAULT 0,
        rolled_back INTEGER DEFAULT 0,
        rollback_date TEXT,
        rollback_reason TEXT,
        created_at TEXT DEFAULT (datetime('now', 'localtime')),
        UNIQUE(store_id, optimization_date)
    );
    CREATE INDEX IF NOT EXISTS idx_bayesian_log_store_date
        ON bayesian_optimization_log(store_id, optimization_date);
    """,

    41: """
    -- v41: dashboard_users에 phone 컬럼 추가
    ALTER TABLE dashboard_users ADD COLUMN phone TEXT;
    """,
}


def init_db(db_path: Optional[Path] = None) -> None:
    """데이터베이스 초기화 및 마이그레이션

    현재 스키마 버전을 확인하고 누락된 마이그레이션을 순차 적용

    Args:
        db_path: DB 파일 경로 (기본값: 기본 DB 경로)
    """
    if db_path is None:
        db_path = get_db_path()

    conn = get_connection(db_path)
    cursor = conn.cursor()

    # 현재 스키마 버전 확인
    try:
        cursor.execute("SELECT MAX(version) FROM schema_version")
        row = cursor.fetchone()
        current_version = row[0] if row and row[0] else 0
    except sqlite3.OperationalError:
        current_version = 0

    # 마이그레이션 실행
    for version in range(current_version + 1, DB_SCHEMA_VERSION + 1):
        if version in SCHEMA_MIGRATIONS:
            logger.info(f"Applying migration v{version}...")
            script = SCHEMA_MIGRATIONS[version]
            # 개별 문장 실행: executescript는 duplicate column에서 전체 중단되므로
            # 각 문장을 분리하여 실행하고, 무해한 에러만 건너뜀
            raw_stmts = [s.strip() for s in script.split(';') if s.strip()]
            # 주석만으로 구성된 블록 제거 (SQL 포함 블록의 선행 주석은 유지)
            statements = []
            for s in raw_stmts:
                lines = [l for l in s.split('\n') if l.strip() and not l.strip().startswith('--')]
                if lines:
                    statements.append(s)
            for stmt in statements:
                try:
                    cursor.execute(stmt)
                except sqlite3.OperationalError as e:
                    err_msg = str(e).lower()
                    if any(k in err_msg for k in ("duplicate column", "already exists", "no such table")):
                        logger.warning(f"Migration v{version}: 이미 존재 (무시): {e}")
                    else:
                        raise
            cursor.execute(
                "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, datetime.now().isoformat())
            )
            conn.commit()
            logger.info(f"Migration v{version} applied successfully")

    conn.close()
    logger.info(f"Database initialized at {db_path}")


if __name__ == "__main__":
    init_db()
