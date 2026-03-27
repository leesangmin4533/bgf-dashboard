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
        large_cd TEXT,
        large_nm TEXT,
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
        large_cd TEXT,
        small_cd TEXT,
        small_nm TEXT,
        class_nm TEXT,
        FOREIGN KEY (item_cd) REFERENCES products(item_cd)
    )""",

    # external_factors
    """CREATE TABLE IF NOT EXISTS external_factors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        factor_date TEXT NOT NULL,
        factor_type TEXT NOT NULL,
        factor_key TEXT NOT NULL,
        factor_value TEXT,
        store_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE(factor_date, factor_type, factor_key, store_id)
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
        lat REAL,
        lng REAL,
        type TEXT,
        is_active INTEGER DEFAULT 1,
        bgf_user_id TEXT,
        bgf_password TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",

    # store_analysis (상권 분석)
    """CREATE TABLE IF NOT EXISTS store_analysis (
        store_id TEXT PRIMARY KEY,
        competitor_count INTEGER,
        school_count INTEGER,
        hospital_count INTEGER,
        restaurant_count INTEGER,
        cafe_count INTEGER,
        subway_count INTEGER,
        office_count INTEGER,
        daycare_count INTEGER DEFAULT 0,
        traffic_score REAL,
        competition_score REAL,
        area_type TEXT,
        analyzed_at TEXT,
        radius_m INTEGER DEFAULT 500
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
    "CREATE INDEX IF NOT EXISTS idx_external_factors_store ON external_factors(store_id)",
    "CREATE INDEX IF NOT EXISTS idx_mid_categories_large ON mid_categories(large_cd)",
    "CREATE INDEX IF NOT EXISTS idx_product_details_large ON product_details(large_cd)",
    "CREATE INDEX IF NOT EXISTS idx_product_details_small ON product_details(small_cd)",
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
        store_id TEXT,
        delivery_type TEXT DEFAULT NULL
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
        query_fail_count INTEGER DEFAULT 0,
        unavail_reason TEXT,
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
        is_stock_stale INTEGER DEFAULT 0,
        rule_order_qty INTEGER,
        ml_order_qty INTEGER,
        ml_weight_used REAL
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
        UNIQUE(store_id, eval_date, item_cd)
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
        UNIQUE(store_id, item_cd, promo_type, start_date)
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
        UNIQUE(store_id, item_cd, change_date, change_type)
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

    # manual_order_items (수동 발주 상품)
    """CREATE TABLE IF NOT EXISTS manual_order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT,
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        mid_cd TEXT,
        mid_nm TEXT,
        order_qty INTEGER NOT NULL DEFAULT 0,
        ord_cnt INTEGER DEFAULT 0,
        ord_unit_qty INTEGER DEFAULT 1,
        ord_input_id TEXT,
        ord_amt INTEGER DEFAULT 0,
        order_date TEXT NOT NULL,
        collected_at TEXT DEFAULT (datetime('now', 'localtime')),
        UNIQUE(item_cd, order_date)
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
        UNIQUE(store_id, eval_date, item_cd)
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

    # new_product_3day_tracking (신상품 3일발주 분산 추적, v60 그룹핑)
    """CREATE TABLE IF NOT EXISTS new_product_3day_tracking (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        week_label TEXT NOT NULL,
        week_start DATE NOT NULL,
        week_end DATE NOT NULL,
        product_code TEXT NOT NULL,
        product_name TEXT,
        sub_category TEXT,
        base_name TEXT DEFAULT '',
        product_codes TEXT DEFAULT '',
        selected_code TEXT DEFAULT '',
        bgf_order_count INTEGER DEFAULT 0,
        our_order_count INTEGER DEFAULT 0,
        order_interval_days INTEGER,
        next_order_date DATE,
        skip_count INTEGER DEFAULT 0,
        last_sale_after_order INTEGER DEFAULT 0,
        last_checked_at DATETIME,
        last_ordered_at DATETIME,
        is_completed INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(store_id, week_label, base_name)
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
        mid_cd TEXT DEFAULT '',
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

    # detected_new_products (입고 시 감지된 신제품 이력 — v45, lifecycle 확장 v46)
    """CREATE TABLE IF NOT EXISTS detected_new_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        mid_cd TEXT,
        mid_cd_source TEXT DEFAULT 'fallback',
        first_receiving_date TEXT NOT NULL,
        receiving_qty INTEGER DEFAULT 0,
        order_unit_qty INTEGER DEFAULT 1,
        center_cd TEXT,
        center_nm TEXT,
        cust_nm TEXT,
        registered_to_products INTEGER DEFAULT 0,
        registered_to_details INTEGER DEFAULT 0,
        registered_to_inventory INTEGER DEFAULT 0,
        detected_at TEXT NOT NULL,
        store_id TEXT,
        lifecycle_status TEXT DEFAULT 'detected',
        monitoring_start_date TEXT,
        monitoring_end_date TEXT,
        total_sold_qty INTEGER DEFAULT 0,
        sold_days INTEGER DEFAULT 0,
        similar_item_avg REAL,
        status_changed_at TEXT,
        analysis_window_days INTEGER,
        extension_count INTEGER DEFAULT 0,
        settlement_score REAL,
        settlement_verdict TEXT,
        settlement_date TEXT,
        settlement_checked_at TEXT,
        UNIQUE(item_cd, first_receiving_date)
    )""",

    # new_product_daily_tracking (신제품 일별 판매/재고/발주 추적 — v46)
    """CREATE TABLE IF NOT EXISTS new_product_daily_tracking (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_cd TEXT NOT NULL,
        tracking_date TEXT NOT NULL,
        sales_qty INTEGER DEFAULT 0,
        stock_qty INTEGER DEFAULT 0,
        order_qty INTEGER DEFAULT 0,
        store_id TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(item_cd, tracking_date, store_id)
    )""",

    # substitution_events (소분류 내 잠식 감지 — v49)
    """CREATE TABLE IF NOT EXISTS substitution_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        detection_date TEXT NOT NULL,
        small_cd TEXT NOT NULL,
        small_nm TEXT,
        gainer_item_cd TEXT NOT NULL,
        gainer_item_nm TEXT,
        gainer_prior_avg REAL,
        gainer_recent_avg REAL,
        gainer_growth_rate REAL,
        loser_item_cd TEXT NOT NULL,
        loser_item_nm TEXT,
        loser_prior_avg REAL,
        loser_recent_avg REAL,
        loser_decline_rate REAL,
        adjustment_coefficient REAL NOT NULL DEFAULT 1.0,
        total_change_rate REAL,
        confidence REAL DEFAULT 0.0,
        is_active INTEGER DEFAULT 1,
        expires_at TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(store_id, detection_date, loser_item_cd, gainer_item_cd)
    )""",

    # food_waste_calibration (폐기율 자동 보정 — v32, small_cd v48)
    """CREATE TABLE IF NOT EXISTS food_waste_calibration (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        mid_cd TEXT NOT NULL,
        calibration_date TEXT NOT NULL,
        actual_waste_rate REAL NOT NULL,
        target_waste_rate REAL NOT NULL,
        error REAL NOT NULL,
        sample_days INTEGER NOT NULL,
        total_order_qty INTEGER,
        total_waste_qty INTEGER,
        total_sold_qty INTEGER,
        param_name TEXT,
        old_value REAL,
        new_value REAL,
        current_params TEXT,
        created_at TEXT NOT NULL,
        small_cd TEXT DEFAULT '',
        UNIQUE(store_id, mid_cd, small_cd, calibration_date)
    )""",

    # dessert_decisions (디저트 발주 유지/정지 판단 — v52)
    """CREATE TABLE IF NOT EXISTS dessert_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        mid_cd TEXT DEFAULT '014',
        dessert_category TEXT NOT NULL,
        expiration_days INTEGER,
        small_nm TEXT,
        lifecycle_phase TEXT NOT NULL,
        first_receiving_date TEXT,
        first_receiving_source TEXT,
        weeks_since_intro INTEGER DEFAULT 0,
        judgment_period_start TEXT NOT NULL,
        judgment_period_end TEXT NOT NULL,
        total_order_qty INTEGER DEFAULT 0,
        total_sale_qty INTEGER DEFAULT 0,
        total_disuse_qty INTEGER DEFAULT 0,
        sale_amount INTEGER DEFAULT 0,
        disuse_amount INTEGER DEFAULT 0,
        sell_price INTEGER DEFAULT 0,
        sale_rate REAL DEFAULT 0.0,
        category_avg_sale_qty REAL DEFAULT 0.0,
        prev_period_sale_qty INTEGER DEFAULT 0,
        sale_trend_pct REAL DEFAULT 0.0,
        consecutive_low_weeks INTEGER DEFAULT 0,
        consecutive_zero_months INTEGER DEFAULT 0,
        decision TEXT NOT NULL,
        decision_reason TEXT,
        is_rapid_decline_warning INTEGER DEFAULT 0,
        operator_action TEXT,
        operator_note TEXT,
        action_taken_at TEXT,
        judgment_cycle TEXT NOT NULL,
        category_type TEXT DEFAULT 'dessert',
        created_at TEXT NOT NULL,
        UNIQUE(store_id, item_cd, judgment_period_end)
    )""",

    # waste_slips (폐기 전표 — v33)
    """CREATE TABLE IF NOT EXISTS waste_slips (
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
    )""",

    # waste_slip_items (폐기 전표 상세 품목 — v34)
    """CREATE TABLE IF NOT EXISTS waste_slip_items (
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
    )""",

    # waste_verification_log (폐기 전표 검증 — v33)
    """CREATE TABLE IF NOT EXISTS waste_verification_log (
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
    )""",

    # order_exclusions (발주 제외 사유 추적 — v43)
    """CREATE TABLE IF NOT EXISTS order_exclusions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        eval_date TEXT NOT NULL,
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        mid_cd TEXT,
        exclusion_type TEXT NOT NULL,
        predicted_qty INTEGER DEFAULT 0,
        current_stock INTEGER DEFAULT 0,
        pending_qty INTEGER DEFAULT 0,
        detail TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(store_id, eval_date, item_cd)
    )""",

    # user_order_tendency — 카테고리별 점주 발주 개입 성향 (v63: order_diffs 기반)
    """CREATE TABLE IF NOT EXISTS user_order_tendency (
        store_id TEXT NOT NULL,
        mid_cd TEXT NOT NULL,
        period_days INTEGER DEFAULT 90,
        removed_count INTEGER DEFAULT 0,
        added_count INTEGER DEFAULT 0,
        qty_changed_count INTEGER DEFAULT 0,
        qty_up_count INTEGER DEFAULT 0,
        qty_down_count INTEGER DEFAULT 0,
        remove_rate REAL,
        add_rate REAL,
        qty_up_rate REAL,
        tendency TEXT,
        zero_stock_rate REAL,
        updated_at TEXT,
        PRIMARY KEY (store_id, mid_cd)
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
    # new_product_3day_tracking
    "CREATE INDEX IF NOT EXISTS idx_np3day_store_week ON new_product_3day_tracking(store_id, week_label)",
    "CREATE INDEX IF NOT EXISTS idx_np3day_product ON new_product_3day_tracking(product_code)",
    "CREATE INDEX IF NOT EXISTS idx_np3day_completed ON new_product_3day_tracking(is_completed)",
    "CREATE INDEX IF NOT EXISTS idx_np3day_base_name ON new_product_3day_tracking(base_name)",
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
    # detected_new_products
    "CREATE INDEX IF NOT EXISTS idx_detected_new_products_date ON detected_new_products(first_receiving_date)",
    "CREATE INDEX IF NOT EXISTS idx_detected_new_products_item ON detected_new_products(item_cd)",
    "CREATE INDEX IF NOT EXISTS idx_detected_new_products_lifecycle ON detected_new_products(lifecycle_status)",
    # new_product_daily_tracking
    "CREATE INDEX IF NOT EXISTS idx_np_tracking_item ON new_product_daily_tracking(item_cd)",
    "CREATE INDEX IF NOT EXISTS idx_np_tracking_date ON new_product_daily_tracking(tracking_date)",
    # substitution_events
    "CREATE INDEX IF NOT EXISTS idx_substitution_store_date ON substitution_events(store_id, detection_date)",
    "CREATE INDEX IF NOT EXISTS idx_substitution_loser ON substitution_events(loser_item_cd, is_active)",
    "CREATE INDEX IF NOT EXISTS idx_substitution_small_cd ON substitution_events(small_cd)",
    # food_waste_calibration
    "CREATE INDEX IF NOT EXISTS idx_food_waste_cal_store_mid ON food_waste_calibration(store_id, mid_cd, calibration_date)",
    "CREATE INDEX IF NOT EXISTS idx_food_waste_cal_small_cd ON food_waste_calibration(store_id, mid_cd, small_cd, calibration_date)",
    # waste_slips
    "CREATE INDEX IF NOT EXISTS idx_waste_slips_store_date ON waste_slips(store_id, chit_date)",
    # waste_slip_items
    "CREATE INDEX IF NOT EXISTS idx_wsi_store_date ON waste_slip_items(store_id, chit_date)",
    "CREATE INDEX IF NOT EXISTS idx_wsi_item ON waste_slip_items(item_cd)",
    # waste_verification_log
    "CREATE INDEX IF NOT EXISTS idx_waste_verify_store_date ON waste_verification_log(store_id, verification_date)",
    # dessert_decisions
    "CREATE INDEX IF NOT EXISTS idx_dessert_dec_store_date ON dessert_decisions(store_id, judgment_period_end)",
    "CREATE INDEX IF NOT EXISTS idx_dessert_dec_item ON dessert_decisions(item_cd)",
    "CREATE INDEX IF NOT EXISTS idx_dessert_dec_category ON dessert_decisions(dessert_category, decision)",
    "CREATE INDEX IF NOT EXISTS idx_dessert_dec_decision ON dessert_decisions(decision, created_at)",
    # order_exclusions
    "CREATE INDEX IF NOT EXISTS idx_oe_date ON order_exclusions(eval_date)",
    "CREATE INDEX IF NOT EXISTS idx_oe_type ON order_exclusions(exclusion_type)",
    # user_order_tendency
    "CREATE INDEX IF NOT EXISTS idx_uot_tendency ON user_order_tendency(tendency)",
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
        # 1) 테이블 생성 (이미 있으면 스킵)
        for sql in STORE_SCHEMA:
            cursor.execute(sql)
        # 2) 기존 테이블에 누락된 컬럼 보정 (인덱스보다 먼저!)
        _apply_store_column_patches(cursor)
        # 3) 인덱스 생성 (컬럼 보정 후 실행해야 에러 방지)
        for sql in STORE_INDEXES:
            cursor.execute(sql)
        conn.commit()
        logger.info(f"매장 DB 초기화 완료: {store_id} → {db_path}")
    finally:
        conn.close()


# 매장 DB 컬럼 보정 (기존 테이블에 누락된 컬럼 추가)
_STORE_COLUMN_PATCHES = [
    # v48: food_waste_calibration small_cd 세분화
    "ALTER TABLE food_waste_calibration ADD COLUMN small_cd TEXT DEFAULT ''",
    # v51: 조회 실패 내성
    "ALTER TABLE realtime_inventory ADD COLUMN query_fail_count INTEGER DEFAULT 0",
    "ALTER TABLE realtime_inventory ADD COLUMN unavail_reason TEXT",
    # v53: 디저트/음료 판단 category_type
    "ALTER TABLE dessert_decisions ADD COLUMN category_type TEXT DEFAULT 'dessert'",
    # waste_slips 누락 컬럼 보정 (v33 스키마 불완전한 매장)
    "ALTER TABLE waste_slips ADD COLUMN nap_plan_ymd TEXT",
    "ALTER TABLE waste_slips ADD COLUMN conf_id TEXT",
    "ALTER TABLE waste_slips ADD COLUMN cre_ymdhms TEXT",
    "ALTER TABLE waste_slips ADD COLUMN updated_at TEXT",
    # waste_slips 타입 보정 (INTEGER → REAL)
    # Note: SQLite는 ALTER COLUMN 미지원이므로 새 DB에서만 REAL 적용됨
    # v55: ML 가중치 개선 인프라 — Rule vs ML 분리 추적
    "ALTER TABLE prediction_logs ADD COLUMN rule_order_qty INTEGER",
    "ALTER TABLE prediction_logs ADD COLUMN ml_order_qty INTEGER",
    "ALTER TABLE prediction_logs ADD COLUMN ml_weight_used REAL",
    # v56: 신제품 안착 판정 컬럼
    "ALTER TABLE detected_new_products ADD COLUMN analysis_window_days INTEGER",
    "ALTER TABLE detected_new_products ADD COLUMN extension_count INTEGER DEFAULT 0",
    "ALTER TABLE detected_new_products ADD COLUMN settlement_score REAL",
    "ALTER TABLE detected_new_products ADD COLUMN settlement_verdict TEXT",
    "ALTER TABLE detected_new_products ADD COLUMN settlement_date TEXT",
    "ALTER TABLE detected_new_products ADD COLUMN settlement_checked_at TEXT",
    # v64: 발주 확정 pending 컬럼
    "ALTER TABLE order_tracking ADD COLUMN pending_confirmed INTEGER DEFAULT 0",
    "ALTER TABLE order_tracking ADD COLUMN pending_confirmed_at TEXT",
    # v65: 발주방법 (ORD_INPUT_ID: 단품별(재택), 자동발주, 스마트발주 등)
    "ALTER TABLE order_tracking ADD COLUMN ord_input_id TEXT",
    # v66: order_behavior_log (AI vs 실제 발주 비교)
    """CREATE TABLE IF NOT EXISTS order_behavior_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        order_date TEXT NOT NULL,
        item_cd TEXT NOT NULL,
        mid_cd TEXT,
        ai_predicted_qty REAL,
        ai_recommended_qty INTEGER,
        ai_final_qty INTEGER,
        ai_eval_decision TEXT,
        actual_qty INTEGER,
        ord_input_id TEXT,
        already_received_qty INTEGER,
        diff INTEGER,
        diff_ratio REAL,
        action TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(store_id, order_date, item_cd)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_obl_store_date ON order_behavior_log(store_id, order_date)",
    "CREATE INDEX IF NOT EXISTS idx_obl_mid_action ON order_behavior_log(mid_cd, action)",
    # v67: 발주정지 예정 (STOP_PLAN_YMD) + 정지 사유
    "ALTER TABLE realtime_inventory ADD COLUMN stop_plan_ymd TEXT",
    "ALTER TABLE realtime_inventory ADD COLUMN cut_reason TEXT",
]


def _fix_promotions_unique(cursor) -> None:
    """promotions 테이블 UNIQUE 제약을 (store_id, item_cd, promo_type, start_date)로 보정.

    기존 UNIQUE가 3컬럼(item_cd, promo_type, start_date)인 경우에만 재생성한다.
    """
    try:
        row = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='promotions'"
        ).fetchone()
        if not row:
            return
        create_sql = row[0]
        # 이미 store_id가 UNIQUE에 포함되어 있으면 스킵
        if "store_id, item_cd, promo_type, start_date" in create_sql:
            return
        # 3컬럼 UNIQUE → 4컬럼 UNIQUE로 재생성
        logger.info("promotions 테이블 UNIQUE 보정: 3컬럼 → 4컬럼")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS promotions_fixed (
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
                UNIQUE(store_id, item_cd, promo_type, start_date)
            )
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO promotions_fixed
                (id, store_id, item_cd, item_nm, promo_type,
                 start_date, end_date, is_active, collected_at, updated_at)
            SELECT id, store_id, item_cd, item_nm, promo_type,
                   start_date, end_date, is_active, collected_at, updated_at
            FROM promotions
        """)
        cursor.execute("DROP TABLE promotions")
        cursor.execute("ALTER TABLE promotions_fixed RENAME TO promotions")
        logger.info("promotions 테이블 UNIQUE 보정 완료")
    except Exception as e:
        logger.warning(f"promotions UNIQUE 보정 실패 (무시): {e}")


def _fix_calibration_unique(cursor) -> None:
    """food_waste_calibration UNIQUE 제약을
    (store_id, mid_cd, calibration_date) → (store_id, mid_cd, small_cd, calibration_date)로 보정.

    기존 UNIQUE에 small_cd가 없는 경우에만 재생성한다.
    오염 데이터(Phase2가 Phase1을 덮어쓴 행)도 함께 정리한다.
    """
    try:
        row = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='food_waste_calibration'"
        ).fetchone()
        if not row:
            return
        create_sql = row[0]
        # 이미 small_cd가 UNIQUE에 포함되어 있으면 스킵
        if "store_id, mid_cd, small_cd, calibration_date" in create_sql:
            return
        logger.info("food_waste_calibration 테이블 UNIQUE 보정: small_cd 추가")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS food_waste_calibration_fixed (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id TEXT NOT NULL,
                mid_cd TEXT NOT NULL,
                calibration_date TEXT NOT NULL,
                actual_waste_rate REAL NOT NULL,
                target_waste_rate REAL NOT NULL,
                error REAL NOT NULL,
                sample_days INTEGER NOT NULL,
                total_order_qty INTEGER,
                total_waste_qty INTEGER,
                total_sold_qty INTEGER,
                param_name TEXT,
                old_value REAL,
                new_value REAL,
                current_params TEXT,
                created_at TEXT NOT NULL,
                small_cd TEXT DEFAULT '',
                UNIQUE(store_id, mid_cd, small_cd, calibration_date)
            )
        """)
        # 오염 행 제외하면서 복사 (Phase2가 Phase1을 덮어쓴 무의미한 행)
        cursor.execute("""
            INSERT OR IGNORE INTO food_waste_calibration_fixed
                (store_id, mid_cd, calibration_date,
                 actual_waste_rate, target_waste_rate, error,
                 sample_days, total_order_qty, total_waste_qty, total_sold_qty,
                 param_name, old_value, new_value,
                 current_params, created_at, small_cd)
            SELECT store_id, mid_cd, calibration_date,
                   actual_waste_rate, target_waste_rate, error,
                   sample_days, total_order_qty, total_waste_qty, total_sold_qty,
                   param_name, old_value, new_value,
                   current_params, created_at, COALESCE(small_cd, '')
            FROM food_waste_calibration
            WHERE NOT (small_cd != '' AND sample_days = 0 AND actual_waste_rate = 0)
        """)
        cursor.execute("DROP TABLE food_waste_calibration")
        cursor.execute("ALTER TABLE food_waste_calibration_fixed RENAME TO food_waste_calibration")
        logger.info("food_waste_calibration 테이블 UNIQUE 보정 완료")
    except Exception as e:
        logger.warning(f"food_waste_calibration UNIQUE 보정 실패 (무시): {e}")


def _fix_eval_outcomes_unique(cursor) -> None:
    """eval_outcomes UNIQUE 제약을 (eval_date, item_cd) → (store_id, eval_date, item_cd)로 보정.

    코드의 ON CONFLICT(store_id, eval_date, item_cd)와 DDL이 불일치하면 저장 실패.
    47863 등 2컬럼 UNIQUE인 매장만 재생성.
    """
    try:
        row = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='eval_outcomes'"
        ).fetchone()
        if not row:
            return
        create_sql = row[0]
        # 이미 store_id가 UNIQUE에 포함되어 있으면 스킵
        if "store_id, eval_date, item_cd" in create_sql:
            return
        logger.info("eval_outcomes 테이블 UNIQUE 보정: 2컬럼 → 3컬럼 (store_id 추가)")
        cursor.execute("ALTER TABLE eval_outcomes RENAME TO eval_outcomes_old")
        cursor.execute("""
            CREATE TABLE eval_outcomes (
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
                UNIQUE(store_id, eval_date, item_cd)
            )
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO eval_outcomes
                (id, store_id, eval_date, item_cd, mid_cd, decision,
                 exposure_days, popularity_score, daily_avg, current_stock,
                 pending_qty, actual_sold_qty, next_day_stock, was_stockout,
                 was_waste, outcome, verified_at, created_at, predicted_qty,
                 actual_order_qty, order_status, weekday, delivery_batch,
                 sell_price, margin_rate, disuse_qty, promo_type,
                 trend_score, stockout_freq)
            SELECT id, store_id, eval_date, item_cd, mid_cd, decision,
                   exposure_days, popularity_score, daily_avg, current_stock,
                   pending_qty, actual_sold_qty, next_day_stock, was_stockout,
                   was_waste, outcome, verified_at, created_at, predicted_qty,
                   actual_order_qty, order_status, weekday, delivery_batch,
                   sell_price, margin_rate, disuse_qty, promo_type,
                   trend_score, stockout_freq
            FROM eval_outcomes_old
        """)
        cursor.execute("DROP TABLE eval_outcomes_old")
        # 인덱스 재생성
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_eval_outcomes_date ON eval_outcomes(eval_date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_eval_outcomes_item ON eval_outcomes(item_cd)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_eval_outcomes_decision ON eval_outcomes(decision)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_eval_outcomes_outcome ON eval_outcomes(outcome)")
        logger.info("eval_outcomes 테이블 UNIQUE 보정 완료")
    except Exception as e:
        logger.warning(f"eval_outcomes UNIQUE 보정 실패 (무시): {e}")


def _fix_order_fail_reasons_unique(cursor) -> None:
    """order_fail_reasons UNIQUE 제약을 (eval_date, item_cd) → (store_id, eval_date, item_cd)로 보정."""
    try:
        row = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='order_fail_reasons'"
        ).fetchone()
        if not row:
            return
        create_sql = row[0]
        if "store_id, eval_date, item_cd" in create_sql:
            return
        logger.info("order_fail_reasons 테이블 UNIQUE 보정: 2컬럼 → 3컬럼 (store_id 추가)")
        cursor.execute("ALTER TABLE order_fail_reasons RENAME TO order_fail_reasons_old")
        cursor.execute("""
            CREATE TABLE order_fail_reasons (
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
                UNIQUE(store_id, eval_date, item_cd)
            )
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO order_fail_reasons
                (id, store_id, eval_date, item_cd, item_nm, mid_cd,
                 stop_reason, orderable_status, orderable_day,
                 order_status, checked_at, created_at)
            SELECT id, store_id, eval_date, item_cd, item_nm, mid_cd,
                   stop_reason, orderable_status, orderable_day,
                   order_status, checked_at, created_at
            FROM order_fail_reasons_old
        """)
        cursor.execute("DROP TABLE order_fail_reasons_old")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_fail_reasons_date ON order_fail_reasons(eval_date)")
        logger.info("order_fail_reasons 테이블 UNIQUE 보정 완료")
    except Exception as e:
        logger.warning(f"order_fail_reasons UNIQUE 보정 실패 (무시): {e}")


def _apply_store_column_patches(cursor) -> None:
    """기존 매장 DB 테이블에 누락된 컬럼을 안전하게 추가.

    이미 존재하는 컬럼은 'duplicate column name' 에러를 무시한다.
    """
    for stmt in _STORE_COLUMN_PATCHES:
        try:
            cursor.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                pass  # 이미 존재 → 무시
            elif "no such table" in str(e):
                pass  # 테이블 자체가 없으면 CREATE TABLE에서 이미 포함됨
            else:
                logger.warning(f"Store DB 컬럼 보정 실패 (무시): {stmt} → {e}")
    # UNIQUE 제약 보정
    _fix_promotions_unique(cursor)
    _fix_calibration_unique(cursor)
    _fix_eval_outcomes_unique(cursor)
    _fix_order_fail_reasons_unique(cursor)


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
