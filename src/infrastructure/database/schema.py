"""
DB 스키마 정의 및 마이그레이션

공통 DB (common.db)와 매장별 DB (stores/{store_id}.db)의
스키마를 분리 관리합니다.
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════
# 공통 DB 스키마 (common.db)
# ═══════════════════════════════════════════════════════

COMMON_SCHEMA = [
    # schema_version
    """CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    )""",

    # products
    """CREATE TABLE IF NOT EXISTS products (
        item_cd TEXT PRIMARY KEY,
        item_nm TEXT NOT NULL,
        mid_cd TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (mid_cd) REFERENCES mid_categories(mid_cd)
    )""",

    # mid_categories
    """CREATE TABLE IF NOT EXISTS mid_categories (
        mid_cd TEXT PRIMARY KEY,
        mid_nm TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",

    # product_details
    """CREATE TABLE IF NOT EXISTS product_details (
        item_cd TEXT PRIMARY KEY,
        item_nm TEXT,
        expiration_days INTEGER,
        orderable_day TEXT DEFAULT '일월화수목금토',
        orderable_status TEXT,
        order_unit_name TEXT DEFAULT '낱개',
        order_unit_qty INTEGER DEFAULT 1,
        case_unit_qty INTEGER DEFAULT 1,
        lead_time_days INTEGER DEFAULT 1,
        fetched_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        promo_type TEXT,
        promo_name TEXT,
        promo_start TEXT,
        promo_end TEXT,
        promo_updated TEXT,
        sell_price INTEGER,
        margin_rate REAL,
        store_id TEXT,
        FOREIGN KEY (item_cd) REFERENCES products(item_cd)
    )""",

    # external_factors
    """CREATE TABLE IF NOT EXISTS external_factors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        factor_date TEXT NOT NULL,
        factor_type TEXT NOT NULL,
        factor_key TEXT NOT NULL,
        factor_value TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(factor_date, factor_type, factor_key)
    )""",

    # app_settings
    """CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT DEFAULT (datetime('now'))
    )""",

    # stores
    """CREATE TABLE IF NOT EXISTS stores (
        store_id TEXT PRIMARY KEY,
        store_name TEXT NOT NULL,
        location TEXT,
        type TEXT,
        is_active INTEGER DEFAULT 1,
        bgf_user_id TEXT,
        bgf_password TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",

    # store_eval_params
    """CREATE TABLE IF NOT EXISTS store_eval_params (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        param_name TEXT NOT NULL,
        param_value REAL NOT NULL,
        default_value REAL,
        min_value REAL,
        max_value REAL,
        max_delta REAL,
        description TEXT,
        updated_at TEXT NOT NULL,
        UNIQUE(store_id, param_name),
        FOREIGN KEY (store_id) REFERENCES stores(store_id) ON DELETE CASCADE
    )""",

    # stopped_items (발주정지 상품 — 공용)
    """CREATE TABLE IF NOT EXISTS stopped_items (
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        stop_reason TEXT,
        first_detected_at TEXT NOT NULL,
        last_detected_at TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        UNIQUE(item_cd)
    )""",

    # dashboard_users (대시보드 인증 — v38)
    """CREATE TABLE IF NOT EXISTS dashboard_users (
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
    )""",

    # signup_requests (회원가입 요청 — v39)
    """CREATE TABLE IF NOT EXISTS signup_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        phone TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        reject_reason TEXT,
        created_at TEXT NOT NULL,
        reviewed_at TEXT,
        reviewed_by INTEGER
    )""",
]

COMMON_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_products_mid ON products(mid_cd)",
    "CREATE INDEX IF NOT EXISTS idx_external_factors_date ON external_factors(factor_date)",
    "CREATE INDEX IF NOT EXISTS idx_external_factors_type ON external_factors(factor_type)",
    "CREATE INDEX IF NOT EXISTS idx_product_details_item ON product_details(item_cd)",
    "CREATE INDEX IF NOT EXISTS idx_product_details_promo ON product_details(promo_type)",
    "CREATE INDEX IF NOT EXISTS idx_stores_active ON stores(is_active)",
    "CREATE INDEX IF NOT EXISTS idx_store_eval_params_store ON store_eval_params(store_id)",
    "CREATE INDEX IF NOT EXISTS idx_stopped_items_active ON stopped_items(is_active)",
    "CREATE INDEX IF NOT EXISTS idx_dashboard_users_username ON dashboard_users(username)",
    "CREATE INDEX IF NOT EXISTS idx_dashboard_users_store ON dashboard_users(store_id)",
    "CREATE INDEX IF NOT EXISTS idx_signup_requests_status ON signup_requests(status)",
    "CREATE INDEX IF NOT EXISTS idx_signup_requests_store ON signup_requests(store_id)",
]


# ═══════════════════════════════════════════════════════
# 매장별 DB 스키마 (stores/{store_id}.db)
# ═══════════════════════════════════════════════════════

STORE_SCHEMA = [
    # daily_sales (store_id 컬럼 유지 — 호환용)
    """CREATE TABLE IF NOT EXISTS daily_sales (
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
        store_id TEXT,
        UNIQUE(sales_date, item_cd)
    )""",

    # order_tracking
    """CREATE TABLE IF NOT EXISTS order_tracking (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT,
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
        UNIQUE(order_date, item_cd)
    )""",

    # order_history
    """CREATE TABLE IF NOT EXISTS order_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_date TEXT NOT NULL,
        item_cd TEXT NOT NULL,
        mid_cd TEXT,
        predicted_qty INTEGER DEFAULT 0,
        recommended_qty INTEGER DEFAULT 0,
        actual_order_qty INTEGER,
        current_stock INTEGER DEFAULT 0,
        order_unit TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT NOT NULL,
        store_id TEXT
    )""",

    # inventory_batches
    """CREATE TABLE IF NOT EXISTS inventory_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        mid_cd TEXT,
        receiving_date TEXT NOT NULL,
        receiving_id INTEGER,
        expiration_days INTEGER NOT NULL,
        expiry_date TEXT NOT NULL,
        initial_qty INTEGER NOT NULL,
        remaining_qty INTEGER NOT NULL,
        status TEXT DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        store_id TEXT
    )""",

    # realtime_inventory
    """CREATE TABLE IF NOT EXISTS realtime_inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT,
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
    )""",

    # prediction_logs
    """CREATE TABLE IF NOT EXISTS prediction_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prediction_date TEXT NOT NULL,
        target_date TEXT NOT NULL,
        item_cd TEXT NOT NULL,
        mid_cd TEXT,
        predicted_qty INTEGER,
        actual_qty INTEGER,
        model_type TEXT,
        created_at TEXT NOT NULL,
        adjusted_qty REAL,
        weekday_coef REAL,
        confidence TEXT,
        safety_stock REAL,
        current_stock INTEGER,
        order_qty INTEGER,
        store_id TEXT,
        stock_source TEXT,
        pending_source TEXT,
        is_stock_stale INTEGER DEFAULT 0
    )""",

    # eval_outcomes
    """CREATE TABLE IF NOT EXISTS eval_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT,
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
        UNIQUE(eval_date, item_cd)
    )""",

    # promotions
    """CREATE TABLE IF NOT EXISTS promotions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT,
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        promo_type TEXT NOT NULL,
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        collected_at TEXT NOT NULL,
        updated_at TEXT,
        UNIQUE(item_cd, promo_type, start_date)
    )""",

    # promotion_stats
    """CREATE TABLE IF NOT EXISTS promotion_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT,
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
        UNIQUE(item_cd, promo_type)
    )""",

    # promotion_changes
    """CREATE TABLE IF NOT EXISTS promotion_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT,
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
        UNIQUE(item_cd, change_date, change_type)
    )""",

    # receiving_history
    """CREATE TABLE IF NOT EXISTS receiving_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT,
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
        UNIQUE(receiving_date, item_cd, chit_no)
    )""",

    # auto_order_items
    """CREATE TABLE IF NOT EXISTS auto_order_items (
        store_id TEXT,
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        mid_cd TEXT,
        detected_at TEXT,
        updated_at TEXT,
        PRIMARY KEY(item_cd)
    )""",

    # smart_order_items
    """CREATE TABLE IF NOT EXISTS smart_order_items (
        store_id TEXT,
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        mid_cd TEXT,
        detected_at TEXT,
        updated_at TEXT,
        PRIMARY KEY(item_cd)
    )""",

    # collection_logs
    """CREATE TABLE IF NOT EXISTS collection_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT,
        collected_at TEXT NOT NULL,
        sales_date TEXT,
        total_items INTEGER,
        new_items INTEGER,
        updated_items INTEGER,
        status TEXT,
        error_message TEXT,
        duration_seconds REAL,
        created_at TEXT,
        UNIQUE(collected_at)
    )""",

    # order_fail_reasons
    """CREATE TABLE IF NOT EXISTS order_fail_reasons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT,
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
        UNIQUE(eval_date, item_cd)
    )""",

    # calibration_history
    """CREATE TABLE IF NOT EXISTS calibration_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        calibration_date TEXT NOT NULL,
        param_name TEXT NOT NULL,
        old_value REAL NOT NULL,
        new_value REAL NOT NULL,
        reason TEXT,
        accuracy_before REAL,
        accuracy_after REAL,
        sample_size INTEGER,
        created_at TEXT NOT NULL,
        store_id TEXT
    )""",

    # validation_log
    """CREATE TABLE IF NOT EXISTS validation_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        validated_at TEXT NOT NULL,
        sales_date TEXT NOT NULL,
        store_id TEXT,
        validation_type TEXT NOT NULL,
        is_passed BOOLEAN NOT NULL,
        error_code TEXT,
        error_message TEXT,
        affected_items TEXT,
        metadata TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""",

    # new_product_status (신상품 도입 현황 - 주차별)
    """CREATE TABLE IF NOT EXISTS new_product_status (
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
    )""",

    # new_product_items (미도입/미달성 개별 상품)
    """CREATE TABLE IF NOT EXISTS new_product_items (
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
    )""",

    # new_product_monthly (월별 합계)
    """CREATE TABLE IF NOT EXISTS new_product_monthly (
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
    )""",

    # association_rules (연관 상품 분석 규칙)
    """CREATE TABLE IF NOT EXISTS association_rules (
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
    )""",

    # app_settings (매장별 설정)
    """CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT DEFAULT (datetime('now'))
    )""",

    # waste_cause_analysis (폐기 원인 분석)
    """CREATE TABLE IF NOT EXISTS waste_cause_analysis (
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
    )""",

    # bayesian_optimization_log (v40)
    """CREATE TABLE IF NOT EXISTS bayesian_optimization_log (
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
    )""",
]

STORE_INDEXES = [
    # daily_sales
    "CREATE INDEX IF NOT EXISTS idx_daily_sales_date ON daily_sales(sales_date)",
    "CREATE INDEX IF NOT EXISTS idx_daily_sales_item ON daily_sales(item_cd)",
    "CREATE INDEX IF NOT EXISTS idx_daily_sales_mid ON daily_sales(mid_cd)",
    "CREATE INDEX IF NOT EXISTS idx_daily_sales_promo ON daily_sales(promo_type)",
    # order_tracking
    "CREATE INDEX IF NOT EXISTS idx_order_tracking_date ON order_tracking(order_date)",
    # order_history
    "CREATE INDEX IF NOT EXISTS idx_order_history_date ON order_history(order_date)",
    "CREATE INDEX IF NOT EXISTS idx_order_history_item ON order_history(item_cd)",
    "CREATE INDEX IF NOT EXISTS idx_order_history_status ON order_history(status)",
    # inventory_batches
    "CREATE INDEX IF NOT EXISTS idx_inv_batch_item ON inventory_batches(item_cd)",
    "CREATE INDEX IF NOT EXISTS idx_inv_batch_expiry ON inventory_batches(expiry_date)",
    "CREATE INDEX IF NOT EXISTS idx_inv_batch_status ON inventory_batches(status)",
    "CREATE INDEX IF NOT EXISTS idx_inv_batch_item_status ON inventory_batches(item_cd, status)",
    # realtime_inventory
    "CREATE INDEX IF NOT EXISTS idx_realtime_inventory_available ON realtime_inventory(is_available)",
    "CREATE INDEX IF NOT EXISTS idx_realtime_inventory_cut ON realtime_inventory(is_cut_item)",
    # prediction_logs
    "CREATE INDEX IF NOT EXISTS idx_prediction_logs_item ON prediction_logs(item_cd)",
    "CREATE INDEX IF NOT EXISTS idx_prediction_logs_target ON prediction_logs(target_date)",
    # eval_outcomes
    "CREATE INDEX IF NOT EXISTS idx_eval_outcomes_date ON eval_outcomes(eval_date)",
    "CREATE INDEX IF NOT EXISTS idx_eval_outcomes_item ON eval_outcomes(item_cd)",
    "CREATE INDEX IF NOT EXISTS idx_eval_outcomes_decision ON eval_outcomes(decision)",
    "CREATE INDEX IF NOT EXISTS idx_eval_outcomes_outcome ON eval_outcomes(outcome)",
    # promotions
    "CREATE INDEX IF NOT EXISTS idx_promotions_item ON promotions(item_cd)",
    "CREATE INDEX IF NOT EXISTS idx_promotions_active ON promotions(is_active, end_date)",
    "CREATE INDEX IF NOT EXISTS idx_promotions_dates ON promotions(start_date, end_date)",
    # promotion_stats
    "CREATE INDEX IF NOT EXISTS idx_promo_stats_item ON promotion_stats(item_cd)",
    # promotion_changes
    "CREATE INDEX IF NOT EXISTS idx_promo_changes_date ON promotion_changes(change_date)",
    "CREATE INDEX IF NOT EXISTS idx_promo_changes_unprocessed ON promotion_changes(is_processed, change_date)",
    # receiving_history
    "CREATE INDEX IF NOT EXISTS idx_receiving_date ON receiving_history(receiving_date)",
    "CREATE INDEX IF NOT EXISTS idx_receiving_item ON receiving_history(item_cd)",
    "CREATE INDEX IF NOT EXISTS idx_receiving_order_date ON receiving_history(order_date)",
    # collection_logs
    "CREATE INDEX IF NOT EXISTS idx_collection_logs_date ON collection_logs(collected_at)",
    # order_fail_reasons
    "CREATE INDEX IF NOT EXISTS idx_order_fail_reasons_date ON order_fail_reasons(eval_date)",
    # calibration_history
    "CREATE INDEX IF NOT EXISTS idx_calibration_date ON calibration_history(calibration_date)",
    "CREATE INDEX IF NOT EXISTS idx_calibration_param ON calibration_history(param_name)",
    # validation_log
    "CREATE INDEX IF NOT EXISTS idx_validation_log_date ON validation_log(sales_date)",
    "CREATE INDEX IF NOT EXISTS idx_validation_log_type_passed ON validation_log(validation_type, is_passed)",
    # new_product_status
    "CREATE INDEX IF NOT EXISTS idx_new_product_status_month ON new_product_status(store_id, month_ym)",
    # new_product_items
    "CREATE INDEX IF NOT EXISTS idx_new_product_items_type ON new_product_items(store_id, month_ym, item_type)",
    "CREATE INDEX IF NOT EXISTS idx_new_product_items_item ON new_product_items(item_cd)",
    "CREATE INDEX IF NOT EXISTS idx_new_product_items_ordered ON new_product_items(is_ordered)",
    # new_product_monthly
    "CREATE INDEX IF NOT EXISTS idx_new_product_monthly_month ON new_product_monthly(store_id, month_ym)",
    # association_rules
    "CREATE INDEX IF NOT EXISTS idx_assoc_item_b ON association_rules(item_b, rule_level)",
    "CREATE INDEX IF NOT EXISTS idx_assoc_lift ON association_rules(lift DESC)",
    # waste_cause_analysis
    "CREATE INDEX IF NOT EXISTS idx_waste_cause_date ON waste_cause_analysis(store_id, waste_date)",
    "CREATE INDEX IF NOT EXISTS idx_waste_cause_item ON waste_cause_analysis(item_cd)",
    "CREATE INDEX IF NOT EXISTS idx_waste_cause_cause ON waste_cause_analysis(primary_cause)",
    "CREATE INDEX IF NOT EXISTS idx_waste_cause_feedback ON waste_cause_analysis(is_applied, feedback_expiry_date)",
    # bayesian_optimization_log
    "CREATE INDEX IF NOT EXISTS idx_bayesian_log_store_date ON bayesian_optimization_log(store_id, optimization_date)",
]


# ═══════════════════════════════════════════════════════
# 초기화 함수
# ═══════════════════════════════════════════════════════

def init_common_db(db_path: Optional[Path] = None) -> None:
    """공통 DB 초기화"""
    from src.infrastructure.database.connection import DBRouter

    if db_path is None:
        db_path = DBRouter.get_common_db_path()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.cursor()
        for sql in COMMON_SCHEMA:
            cursor.execute(sql)
        for sql in COMMON_INDEXES:
            cursor.execute(sql)
        conn.commit()
        logger.info(f"공통 DB 초기화 완료: {db_path}")
    finally:
        conn.close()


def init_store_db(store_id: str, db_path: Optional[Path] = None) -> None:
    """매장별 DB 초기화"""
    from src.infrastructure.database.connection import DBRouter

    if db_path is None:
        db_path = DBRouter.get_store_db_path(store_id)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.cursor()
        for sql in STORE_SCHEMA:
            cursor.execute(sql)
        for sql in STORE_INDEXES:
            cursor.execute(sql)
        conn.commit()
        logger.info(f"매장 DB 초기화 완료: {store_id} → {db_path}")
    finally:
        conn.close()


def init_db(db_path: Optional[Path] = None) -> None:
    """기존 호환: 레거시 단일 DB 초기화 (마이그레이션 포함)

    새 코드에서는 init_common_db() + init_store_db() 사용 권장
    """
    from src.db.models import SCHEMA_MIGRATIONS, DEFAULT_DB_DIR, DEFAULT_DB_NAME
    from src.settings.constants import DB_SCHEMA_VERSION

    if db_path is None:
        DEFAULT_DB_DIR.mkdir(parents=True, exist_ok=True)
        db_path = DEFAULT_DB_DIR / DEFAULT_DB_NAME

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT MAX(version) FROM schema_version")
        row = cursor.fetchone()
        current_version = row[0] if row and row[0] else 0
    except sqlite3.OperationalError:
        current_version = 0

    for version in range(current_version + 1, DB_SCHEMA_VERSION + 1):
        if version in SCHEMA_MIGRATIONS:
            logger.info(f"Applying migration v{version}...")
            script = SCHEMA_MIGRATIONS[version]
            raw_stmts = [s.strip() for s in script.split(';') if s.strip()]
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
                    if "duplicate column" in err_msg or "already exists" in err_msg:
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
