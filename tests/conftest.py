"""
공유 테스트 픽스처

- in-memory SQLite DB (테스트 간 격리)
- 테스트용 판매 데이터 생성 헬퍼
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def in_memory_db():
    """in-memory SQLite DB (테스트 격리용)"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # daily_sales 테이블 (schema.py STORE_SCHEMA 기준)
    conn.execute("""
        CREATE TABLE daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at TEXT,
            sales_date TEXT NOT NULL,
            item_cd TEXT NOT NULL,
            mid_cd TEXT,
            sale_qty INTEGER DEFAULT 0,
            ord_qty INTEGER DEFAULT 0,
            buy_qty INTEGER DEFAULT 0,
            disuse_qty INTEGER DEFAULT 0,
            stock_qty INTEGER DEFAULT 0,
            created_at TEXT,
            promo_type TEXT DEFAULT '',
            store_id TEXT DEFAULT '46513',
            UNIQUE(sales_date, item_cd)
        )
    """)

    # realtime_inventory 테이블 (schema.py STORE_SCHEMA 기준)
    conn.execute("""
        CREATE TABLE realtime_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT DEFAULT '46513',
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            stock_qty INTEGER DEFAULT 0,
            pending_qty INTEGER DEFAULT 0,
            order_unit_qty INTEGER DEFAULT 1,
            is_available INTEGER DEFAULT 1,
            is_cut_item INTEGER DEFAULT 0,
            queried_at TEXT,
            created_at TEXT,
            UNIQUE(store_id, item_cd)
        )
    """)

    # order_history 테이블
    conn.execute("""
        CREATE TABLE order_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT NOT NULL,
            order_date TEXT NOT NULL,
            order_qty INTEGER DEFAULT 0,
            predicted_qty REAL DEFAULT 0,
            status TEXT DEFAULT 'pending',
            store_id TEXT DEFAULT '46513'
        )
    """)

    # product_details 테이블
    conn.execute("""
        CREATE TABLE product_details (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            mid_cd TEXT,
            order_unit_qty INTEGER DEFAULT 1,
            expiration_days INTEGER,
            orderable_day TEXT DEFAULT '일월화수목금토'
        )
    """)

    # stopped_items 테이블 (common.db)
    conn.execute("""
        CREATE TABLE stopped_items (
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            stop_reason TEXT,
            first_detected_at TEXT NOT NULL,
            last_detected_at TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            UNIQUE(item_cd)
        )
    """)

    # dashboard_users 테이블 (v38 + v41 phone)
    conn.execute("""
        CREATE TABLE dashboard_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            store_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'viewer',
            is_active INTEGER DEFAULT 1,
            full_name TEXT,
            phone TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_login_at TEXT
        )
    """)

    # signup_requests 테이블 (v39)
    conn.execute("""
        CREATE TABLE signup_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            phone TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            reject_reason TEXT,
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            reviewed_by INTEGER
        )
    """)

    # bayesian_optimization_log 테이블 (v40)
    conn.execute("""
        CREATE TABLE bayesian_optimization_log (
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
        )
    """)

    # association_rules 테이블
    conn.execute("""
        CREATE TABLE association_rules (
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
        )
    """)

    # waste_cause_analysis 테이블
    conn.execute("""
        CREATE TABLE waste_cause_analysis (
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
        )
    """)

    # food_waste_calibration 테이블
    conn.execute("""
        CREATE TABLE food_waste_calibration (
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
            UNIQUE(store_id, mid_cd, calibration_date)
        )
    """)

    # waste_slips 테이블
    conn.execute("""
        CREATE TABLE waste_slips (
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
        )
    """)

    # waste_verification_log 테이블
    conn.execute("""
        CREATE TABLE waste_verification_log (
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
        )
    """)

    # waste_slip_items 테이블
    conn.execute("""
        CREATE TABLE waste_slip_items (
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
        )
    """)

    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def sample_sales_data(in_memory_db):
    """30일치 샘플 판매 데이터 생성"""
    today = datetime.now()
    items = [
        ("8801234567890", "049", 5),   # 맥주 - 일평균 5개
        ("8801234567891", "050", 3),   # 소주 - 일평균 3개
        ("8801234567892", "072", 8),   # 담배 - 일평균 8개
        ("8801234567893", "006", 2),   # 라면 - 일평균 2개
        ("8801234567894", "001", 10),  # 도시락 - 일평균 10개
    ]

    for item_cd, mid_cd, avg_qty in items:
        for days_ago in range(30):
            date = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            # 요일에 따른 변동 추가
            weekday = (today - timedelta(days=days_ago)).weekday()
            variation = 1.0 + (weekday - 3) * 0.1  # 목요일 기준 ±10%
            qty = max(1, int(avg_qty * variation))

            in_memory_db.execute(
                "INSERT OR REPLACE INTO daily_sales (item_cd, sales_date, sale_qty, stock_qty, mid_cd) "
                "VALUES (?, ?, ?, ?, ?)",
                (item_cd, date, qty, avg_qty * 2, mid_cd)
            )

    in_memory_db.commit()
    return in_memory_db


@pytest.fixture
def db_path_patch(in_memory_db, tmp_path):
    """카테고리 모듈의 DB 경로를 임시 DB로 패치"""
    db_file = tmp_path / "test_bgf_sales.db"

    # in-memory에서 파일로 복사
    file_conn = sqlite3.connect(str(db_file))
    in_memory_db.backup(file_conn)
    file_conn.close()

    return str(db_file)


@pytest.fixture
def flask_app(in_memory_db, tmp_path):
    """Flask 테스트 앱 (테스트용 DB)"""
    db_file = tmp_path / "test_bgf_sales.db"

    # in-memory에서 파일로 복사
    file_conn = sqlite3.connect(str(db_file))
    in_memory_db.backup(file_conn)

    # app_settings 테이블 추가
    file_conn.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
    """)

    # products 테이블 추가
    file_conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            mid_cd TEXT
        )
    """)

    # collection_logs 테이블 (schema.py STORE_SCHEMA 기준)
    file_conn.execute("""
        CREATE TABLE IF NOT EXISTS collection_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT DEFAULT '46513',
            collected_at TEXT,
            sales_date TEXT,
            total_items INTEGER DEFAULT 0,
            new_items INTEGER,
            updated_items INTEGER,
            status TEXT DEFAULT 'success',
            error_message TEXT,
            duration_seconds REAL,
            created_at TEXT,
            UNIQUE(collected_at)
        )
    """)

    # order_tracking 테이블 (schema.py STORE_SCHEMA 기준)
    file_conn.execute("""
        CREATE TABLE IF NOT EXISTS order_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT DEFAULT '46513',
            order_date TEXT,
            item_cd TEXT,
            item_nm TEXT,
            mid_cd TEXT,
            delivery_type TEXT,
            order_qty INTEGER DEFAULT 0,
            remaining_qty INTEGER DEFAULT 0,
            arrival_time TEXT,
            expiry_time TEXT,
            status TEXT DEFAULT 'pending',
            alert_sent INTEGER,
            created_at TEXT,
            updated_at TEXT,
            actual_receiving_qty INTEGER,
            actual_arrival_time TEXT,
            order_source TEXT,
            UNIQUE(order_date, item_cd)
        )
    """)

    # prediction_logs 테이블
    file_conn.execute("""
        CREATE TABLE IF NOT EXISTS prediction_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_date TEXT,
            target_date TEXT,
            item_cd TEXT,
            mid_cd TEXT,
            predicted_qty REAL,
            actual_qty REAL,
            model_type TEXT,
            created_at TEXT,
            store_id TEXT DEFAULT '46513',
            adjusted_qty REAL,
            weekday_coef REAL,
            confidence TEXT,
            safety_stock REAL,
            current_stock INTEGER,
            order_qty INTEGER,
            stock_source TEXT,
            pending_source TEXT,
            is_stock_stale INTEGER DEFAULT 0
        )
    """)

    # eval_outcomes 테이블
    file_conn.execute("""
        CREATE TABLE IF NOT EXISTS eval_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT,
            eval_date TEXT,
            decision TEXT,
            outcome TEXT,
            order_status TEXT,
            created_at TEXT,
            store_id TEXT DEFAULT '46513',
            UNIQUE(store_id, eval_date, item_cd)
        )
    """)

    # inventory_batches 테이블 (schema.py STORE_SCHEMA 기준)
    file_conn.execute("""
        CREATE TABLE IF NOT EXISTS inventory_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            mid_cd TEXT,
            receiving_date TEXT,
            receiving_id INTEGER,
            expiration_days INTEGER,
            expiry_date TEXT,
            initial_qty INTEGER,
            remaining_qty INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at TEXT,
            updated_at TEXT,
            store_id TEXT DEFAULT '46513'
        )
    """)

    # order_fail_reasons 테이블 (schema.py STORE_SCHEMA 기준)
    file_conn.execute("""
        CREATE TABLE IF NOT EXISTS order_fail_reasons (
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
            UNIQUE(eval_date, item_cd)
        )
    """)

    # promotions 테이블
    file_conn.execute("""
        CREATE TABLE IF NOT EXISTS promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT,
            promo_type TEXT,
            start_date TEXT,
            end_date TEXT,
            status TEXT DEFAULT 'active',
            store_id TEXT DEFAULT '46513',
            is_active INTEGER DEFAULT 1,
            UNIQUE(store_id, item_cd, promo_type, start_date)
        )
    """)

    # auto_order_items 테이블
    file_conn.execute("""
        CREATE TABLE IF NOT EXISTS auto_order_items (
            store_id TEXT DEFAULT '46513',
            item_cd TEXT,
            item_nm TEXT,
            mid_cd TEXT,
            detected_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY(store_id, item_cd)
        )
    """)

    # smart_order_items 테이블
    file_conn.execute("""
        CREATE TABLE IF NOT EXISTS smart_order_items (
            store_id TEXT DEFAULT '46513',
            item_cd TEXT,
            item_nm TEXT,
            mid_cd TEXT,
            detected_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY(store_id, item_cd)
        )
    """)

    # promotion_stats 테이블
    file_conn.execute("""
        CREATE TABLE IF NOT EXISTS promotion_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT,
            promo_type TEXT,
            avg_daily_sales REAL,
            total_days INTEGER,
            total_sales INTEGER,
            multiplier REAL,
            last_calculated TEXT,
            store_id TEXT DEFAULT '46513',
            UNIQUE(store_id, item_cd, promo_type)
        )
    """)

    # promotion_changes 테이블
    file_conn.execute("""
        CREATE TABLE IF NOT EXISTS promotion_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT,
            change_date TEXT,
            change_type TEXT,
            old_promo TEXT,
            new_promo TEXT,
            detected_at TEXT,
            store_id TEXT DEFAULT '46513',
            UNIQUE(store_id, item_cd, change_date, change_type)
        )
    """)

    # receiving_history 테이블
    file_conn.execute("""
        CREATE TABLE IF NOT EXISTS receiving_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receiving_date TEXT,
            item_cd TEXT,
            chit_no TEXT,
            order_qty INTEGER DEFAULT 0,
            received_qty INTEGER DEFAULT 0,
            store_id TEXT DEFAULT '46513',
            UNIQUE(store_id, receiving_date, item_cd, chit_no)
        )
    """)

    # order_history 테이블
    file_conn.execute("""
        CREATE TABLE IF NOT EXISTS order_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT,
            order_date TEXT,
            order_qty INTEGER DEFAULT 0,
            predicted_qty REAL DEFAULT 0,
            status TEXT DEFAULT 'pending',
            store_id TEXT DEFAULT '46513'
        )
    """)

    # new_product_status 테이블
    file_conn.execute("""
        CREATE TABLE IF NOT EXISTS new_product_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL, month_ym TEXT NOT NULL,
            week_no INTEGER NOT NULL, period TEXT,
            doip_rate REAL, item_cnt INTEGER DEFAULT 0,
            item_ad_cnt INTEGER DEFAULT 0, doip_cnt INTEGER DEFAULT 0,
            midoip_cnt INTEGER DEFAULT 0, ds_rate REAL,
            ds_item_cnt INTEGER DEFAULT 0, ds_cnt INTEGER DEFAULT 0,
            mids_cnt INTEGER DEFAULT 0, doip_score REAL, ds_score REAL,
            tot_score REAL, supp_pay_amt INTEGER DEFAULT 0,
            sta_dd TEXT, end_dd TEXT, week_cont TEXT,
            collected_at TEXT NOT NULL,
            UNIQUE(store_id, month_ym, week_no)
        )
    """)

    # new_product_items 테이블
    file_conn.execute("""
        CREATE TABLE IF NOT EXISTS new_product_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL, month_ym TEXT NOT NULL,
            week_no INTEGER NOT NULL, item_type TEXT NOT NULL,
            item_cd TEXT NOT NULL, item_nm TEXT, small_nm TEXT,
            ord_pss_nm TEXT, week_cont TEXT, ds_yn TEXT,
            is_ordered INTEGER DEFAULT 0, ordered_at TEXT,
            collected_at TEXT NOT NULL,
            UNIQUE(store_id, month_ym, week_no, item_type, item_cd)
        )
    """)

    # new_product_monthly 테이블
    file_conn.execute("""
        CREATE TABLE IF NOT EXISTS new_product_monthly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL, month_ym TEXT NOT NULL,
            doip_item_cnt INTEGER DEFAULT 0, doip_cnt INTEGER DEFAULT 0,
            doip_rate REAL, doip_score REAL,
            ds_item_cnt INTEGER DEFAULT 0, ds_cnt INTEGER DEFAULT 0,
            ds_rate REAL, ds_score REAL,
            tot_score REAL, supp_pay_amt INTEGER DEFAULT 0,
            next_min_score REAL, next_max_score REAL,
            next_supp_pay_amt INTEGER DEFAULT 0,
            collected_at TEXT NOT NULL,
            UNIQUE(store_id, month_ym)
        )
    """)

    # association_rules 테이블
    file_conn.execute("""
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
        )
    """)
    file_conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_assoc_item_b
        ON association_rules(item_b, rule_level)
    """)

    # waste_slips 테이블
    file_conn.execute("""
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
        )
    """)

    # waste_verification_log 테이블
    file_conn.execute("""
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
        )
    """)

    # waste_slip_items 테이블
    file_conn.execute("""
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
        )
    """)

    file_conn.commit()
    file_conn.close()

    from src.web.app import create_app
    app = create_app()
    app.config["DB_PATH"] = str(db_file)
    app.config["TESTING"] = True

    return app


@pytest.fixture
def client(flask_app):
    """Flask 테스트 클라이언트 (admin 세션 자동 주입)"""
    client = flask_app.test_client()
    # 기존 테스트가 인증 미들웨어를 통과하도록 admin 세션 주입
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["username"] = "admin"
        sess["store_id"] = "46513"
        sess["role"] = "admin"
        sess["full_name"] = "테스트관리자"
    return client
