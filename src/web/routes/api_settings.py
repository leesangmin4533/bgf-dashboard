"""설정 관리 API.

GET  /api/settings/eval-params       -- eval_params.json 조회
POST /api/settings/eval-params       -- 파라미터 수정 (admin)
POST /api/settings/eval-params/reset -- 기본값 복원 (admin)
GET  /api/settings/feature-flags     -- 기능 토글 목록
POST /api/settings/feature-flags     -- 토글 전환 (admin)
GET  /api/settings/audit-log         -- 변경 이력 조회
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, session

from src.utils.logger import get_logger
from .api_auth import admin_required, login_required

logger = get_logger(__name__)

settings_bp = Blueprint("settings", __name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
EVAL_PARAMS_PATH = CONFIG_DIR / "eval_params.json"
EVAL_PARAMS_DEFAULT_PATH = CONFIG_DIR / "eval_params.default.json"

# 기능 토글 정의 (constants.py에서 관리하는 플래그)
FEATURE_FLAG_DEFS = [
    {"key": "ENABLE_PASS_SUPPRESSION", "description": "패스 억제 (발주불요 판정 억제)"},
    {"key": "ENABLE_FORCE_DOWNGRADE", "description": "강제 다운그레이드"},
    {"key": "ENABLE_FORCE_INTERMITTENT_SUPPRESSION", "description": "간헐수요 FORCE 억제"},
    {"key": "NEW_PRODUCT_MODULE_ENABLED", "description": "신상품 모듈 활성화"},
    {"key": "NEW_PRODUCT_AUTO_INTRO_ENABLED", "description": "미도입 자동 발주 (보류)"},
    {"key": "DIFF_FEEDBACK_ENABLED", "description": "차이 피드백 시스템"},
    {"key": "FOOD_WASTE_CAL_ENABLED", "description": "푸드 폐기율 자동 보정"},
]

# eval_params 그룹 분류
PARAM_GROUPS = {
    "prediction": [
        "daily_avg_days", "weight_daily_avg", "weight_sell_day_ratio",
        "weight_trend", "popularity_high_percentile", "popularity_low_percentile",
        "exposure_urgent", "exposure_normal", "exposure_sufficient",
        "stockout_freq_threshold", "target_accuracy", "force_max_days",
    ],
    "calibration": [
        "calibration_decay", "calibration_reversion_rate",
    ],
}


def _load_eval_params() -> dict:
    """eval_params.json 로드."""
    if not EVAL_PARAMS_PATH.exists():
        return {}
    with open(EVAL_PARAMS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_eval_params(params: dict) -> None:
    """eval_params.json 저장."""
    with open(EVAL_PARAMS_PATH, "w", encoding="utf-8") as f:
        json.dump(params, f, ensure_ascii=False, indent=2)


def _load_defaults() -> dict:
    """eval_params.default.json 로드."""
    if not EVAL_PARAMS_DEFAULT_PATH.exists():
        return {}
    with open(EVAL_PARAMS_DEFAULT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_common_db_path() -> Path:
    return PROJECT_ROOT / "data" / "common.db"


def _ensure_audit_table(conn: sqlite3.Connection):
    """settings_audit_log 테이블 생성."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setting_key TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            changed_by TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            category TEXT NOT NULL
        )
    """)


def _log_audit(key: str, old_val, new_val, category: str):
    """감사 로그 기록."""
    try:
        db_path = _get_common_db_path()
        if not db_path.exists():
            return
        conn = sqlite3.connect(str(db_path), timeout=10)
        try:
            _ensure_audit_table(conn)
            user = session.get("username", "system")
            conn.execute(
                "INSERT INTO settings_audit_log (setting_key, old_value, new_value, changed_by, changed_at, category) VALUES (?,?,?,?,?,?)",
                (key, str(old_val), str(new_val), user, datetime.now().isoformat(), category),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[설정] 감사 로그 기록 실패: {e}")


# ── eval-params ──────────────────────────────────────────────

@settings_bp.route("/eval-params")
@login_required
def get_eval_params():
    """eval_params.json 조회."""
    try:
        params = _load_eval_params()
        # 단순 파라미터만 필터 (중첩 객체 제외)
        simple_params = {}
        nested_params = {}
        for k, v in params.items():
            if isinstance(v, dict) and "value" in v:
                simple_params[k] = v
            else:
                nested_params[k] = v

        return jsonify({
            "params": simple_params,
            "nested": nested_params,
            "groups": PARAM_GROUPS,
        })
    except Exception as e:
        logger.error(f"[설정] eval-params 조회 오류: {e}")
        return jsonify({"error": str(e)}), 500


@settings_bp.route("/eval-params", methods=["POST"])
@admin_required
def update_eval_param():
    """eval_params 수정."""
    try:
        body = request.get_json()
        key = body.get("key")
        new_value = body.get("value")

        if not key or new_value is None:
            return jsonify({"error": "key와 value 필수"}), 400

        params = _load_eval_params()
        if key not in params:
            return jsonify({"error": f"알 수 없는 파라미터: {key}"}), 404

        entry = params[key]
        if not isinstance(entry, dict) or "value" not in entry:
            return jsonify({"error": "중첩 파라미터는 직접 수정 불가"}), 400

        # 범위 검증
        try:
            new_value = float(new_value)
        except (TypeError, ValueError):
            return jsonify({"error": "숫자만 입력 가능"}), 400

        min_val = entry.get("min")
        max_val = entry.get("max")
        if min_val is not None and new_value < min_val:
            return jsonify({"error": f"최소값 {min_val} 이상이어야 합니다"}), 400
        if max_val is not None and new_value > max_val:
            return jsonify({"error": f"최대값 {max_val} 이하이어야 합니다"}), 400

        old_value = entry["value"]
        entry["value"] = new_value
        _save_eval_params(params)
        _log_audit(key, old_value, new_value, "eval_params")

        return jsonify({"ok": True, "old_value": old_value, "new_value": new_value})
    except Exception as e:
        logger.error(f"[설정] eval-params 수정 오류: {e}")
        return jsonify({"error": str(e)}), 500


@settings_bp.route("/eval-params/reset", methods=["POST"])
@admin_required
def reset_eval_param():
    """eval_params 기본값 복원."""
    try:
        body = request.get_json()
        key = body.get("key", "__all__")

        params = _load_eval_params()
        defaults = _load_defaults()
        reset_count = 0

        if key == "__all__":
            # 전체 리셋
            for k, entry in params.items():
                if isinstance(entry, dict) and "value" in entry and "default" in entry:
                    old = entry["value"]
                    entry["value"] = entry["default"]
                    if old != entry["default"]:
                        _log_audit(k, old, entry["default"], "eval_params_reset")
                        reset_count += 1
        else:
            if key not in params:
                return jsonify({"error": f"알 수 없는 파라미터: {key}"}), 404
            entry = params[key]
            if isinstance(entry, dict) and "value" in entry and "default" in entry:
                old = entry["value"]
                entry["value"] = entry["default"]
                if old != entry["default"]:
                    _log_audit(key, old, entry["default"], "eval_params_reset")
                    reset_count += 1

        _save_eval_params(params)
        return jsonify({"ok": True, "reset_count": reset_count})
    except Exception as e:
        logger.error(f"[설정] eval-params 리셋 오류: {e}")
        return jsonify({"error": str(e)}), 500


# ── feature-flags ──────────────────────────────────────────

@settings_bp.route("/feature-flags")
@login_required
def get_feature_flags():
    """기능 토글 목록 조회."""
    try:
        import src.settings.constants as const

        flags = []
        for fdef in FEATURE_FLAG_DEFS:
            val = getattr(const, fdef["key"], None)
            flags.append({
                "key": fdef["key"],
                "value": bool(val) if val is not None else False,
                "description": fdef["description"],
            })
        return jsonify({"flags": flags})
    except Exception as e:
        logger.error(f"[설정] feature-flags 조회 오류: {e}")
        return jsonify({"error": str(e)}), 500


@settings_bp.route("/feature-flags", methods=["POST"])
@admin_required
def toggle_feature_flag():
    """기능 토글 전환."""
    try:
        import src.settings.constants as const

        body = request.get_json()
        key = body.get("key")
        new_value = body.get("value")

        if not key or new_value is None:
            return jsonify({"error": "key와 value 필수"}), 400

        # 유효한 토글 키인지 확인
        valid_keys = [f["key"] for f in FEATURE_FLAG_DEFS]
        if key not in valid_keys:
            return jsonify({"error": f"알 수 없는 토글: {key}"}), 404

        old_value = getattr(const, key, None)
        new_value = bool(new_value)
        setattr(const, key, new_value)
        _log_audit(key, old_value, new_value, "feature_flag")

        return jsonify({
            "ok": True,
            "key": key,
            "old_value": old_value,
            "new_value": new_value,
        })
    except Exception as e:
        logger.error(f"[설정] feature-flag 토글 오류: {e}")
        return jsonify({"error": str(e)}), 500


# ── audit-log ──────────────────────────────────────────────

@settings_bp.route("/audit-log")
@login_required
def get_audit_log():
    """설정 변경 이력 조회."""
    try:
        hours = int(request.args.get("hours", 168))  # 기본 7일
        db_path = _get_common_db_path()
        if not db_path.exists():
            return jsonify({"logs": []})

        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            _ensure_audit_table(conn)
            rows = conn.execute("""
                SELECT * FROM settings_audit_log
                WHERE changed_at >= datetime('now', '-' || ? || ' hours')
                ORDER BY id DESC
                LIMIT 100
            """, (hours,)).fetchall()

            logs = [dict(r) for r in rows]
            return jsonify({"logs": logs})
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"[설정] audit-log 조회 오류: {e}")
        return jsonify({"error": str(e)}), 500


# ── 점주 대시보드용 설정 API ──────────────────────────────────

# 사용자 설정 저장 경로
_USER_SETTINGS_TABLE = "user_dashboard_settings"


def _ensure_user_settings_table(conn):
    """user_dashboard_settings 테이블 생성."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_dashboard_settings (
            user_id INTEGER NOT NULL,
            setting_key TEXT NOT NULL,
            setting_value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, setting_key)
        )
    """)


def _get_user_setting(conn, user_id, key, default=""):
    """사용자 설정 값 조회."""
    row = conn.execute(
        "SELECT setting_value FROM user_dashboard_settings WHERE user_id = ? AND setting_key = ?",
        (user_id, key)
    ).fetchone()
    return row[0] if row else default


def _set_user_setting(conn, user_id, key, value):
    """사용자 설정 값 저장."""
    conn.execute("""
        INSERT INTO user_dashboard_settings (user_id, setting_key, setting_value, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, setting_key) DO UPDATE SET
            setting_value = excluded.setting_value,
            updated_at = excluded.updated_at
    """, (user_id, key, str(value), datetime.now().isoformat()))
    conn.commit()


@settings_bp.route("/user", methods=["GET"])
@login_required
def get_user_settings():
    """점주 대시보드 사용자 설정 조회."""
    try:
        user_id = session.get("user_id")
        db_path = _get_common_db_path()
        if not db_path.exists():
            return jsonify({
                "order_time": "07:00",
                "notifications": {
                    "order_complete": True,
                    "waste_alert": True,
                    "sales_report": False,
                },
                "categories": ["001", "002", "003", "004", "005", "012"],
                "order_mode": "selected",
                "store_name": session.get("full_name", ""),
                "username": session.get("username", ""),
            })

        conn = sqlite3.connect(str(db_path), timeout=10)
        try:
            _ensure_user_settings_table(conn)
            order_time = _get_user_setting(conn, user_id, "order_time", "07:00")
            notif_json = _get_user_setting(
                conn, user_id, "notifications",
                '{"order_complete":true,"waste_alert":true,"sales_report":false}'
            )
            cat_json = _get_user_setting(
                conn, user_id, "categories",
                '["001","002","003","004","005","012"]'
            )
            order_mode = _get_user_setting(conn, user_id, "order_mode", "selected")

            try:
                notifications = json.loads(notif_json)
            except Exception:
                notifications = {"order_complete": True, "waste_alert": True, "sales_report": False}

            try:
                categories = json.loads(cat_json)
            except Exception:
                categories = ["001", "002", "003", "004", "005", "012"]

            return jsonify({
                "order_time": order_time,
                "notifications": notifications,
                "categories": categories,
                "order_mode": order_mode,
                "store_name": session.get("full_name", ""),
                "username": session.get("username", ""),
            })
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"[설정] user-settings 조회 오류: {e}")
        return jsonify({"error": str(e)}), 500


@settings_bp.route("/order-time", methods=["POST"])
@login_required
def set_order_time():
    """발주 시간 설정."""
    try:
        body = request.get_json()
        time_val = body.get("time", "07:00")
        user_id = session.get("user_id")

        db_path = _get_common_db_path()
        conn = sqlite3.connect(str(db_path), timeout=10)
        try:
            _ensure_user_settings_table(conn)
            _set_user_setting(conn, user_id, "order_time", time_val)
        finally:
            conn.close()

        return jsonify({"ok": True, "time": time_val})
    except Exception as e:
        logger.error(f"[설정] order-time 저장 오류: {e}")
        return jsonify({"error": str(e)}), 500


@settings_bp.route("/notifications", methods=["POST"])
@login_required
def set_notifications():
    """알림 설정."""
    try:
        body = request.get_json()
        user_id = session.get("user_id")

        db_path = _get_common_db_path()
        conn = sqlite3.connect(str(db_path), timeout=10)
        try:
            _ensure_user_settings_table(conn)
            _set_user_setting(conn, user_id, "notifications", json.dumps(body))
        finally:
            conn.close()

        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"[설정] notifications 저장 오류: {e}")
        return jsonify({"error": str(e)}), 500


@settings_bp.route("/categories", methods=["POST"])
@login_required
def set_categories():
    """카테고리 설정."""
    try:
        body = request.get_json()
        codes = body.get("codes", [])
        user_id = session.get("user_id")

        db_path = _get_common_db_path()
        conn = sqlite3.connect(str(db_path), timeout=10)
        try:
            _ensure_user_settings_table(conn)
            _set_user_setting(conn, user_id, "categories", json.dumps(codes))
        finally:
            conn.close()

        return jsonify({"ok": True, "codes": codes})
    except Exception as e:
        logger.error(f"[설정] categories 저장 오류: {e}")
        return jsonify({"error": str(e)}), 500


@settings_bp.route("/order-mode", methods=["POST"])
@login_required
def set_order_mode():
    """발주 모드 설정 (all: 전체발주, selected: 카테고리선택)."""
    try:
        body = request.get_json()
        mode = body.get("mode", "selected")
        if mode not in ("all", "selected"):
            return jsonify({"error": "mode는 'all' 또는 'selected'만 허용"}), 400

        user_id = session.get("user_id")

        db_path = _get_common_db_path()
        conn = sqlite3.connect(str(db_path), timeout=10)
        try:
            _ensure_user_settings_table(conn)
            _set_user_setting(conn, user_id, "order_mode", mode)
        finally:
            conn.close()

        return jsonify({"ok": True, "mode": mode})
    except Exception as e:
        logger.error(f"[설정] order-mode 저장 오류: {e}")
        return jsonify({"error": str(e)}), 500


# ── store-info ──────────────────────────────────────────────

@settings_bp.route("/store-info", methods=["GET"])
@login_required
def get_store_info():
    """매장 정보 조회."""
    try:
        store_id = session.get("store_id")
        if not store_id:
            return jsonify({"store_name": "", "location": "", "type": ""}), 200

        db_path = _get_common_db_path()
        if not db_path.exists():
            return jsonify({"store_name": "", "location": "", "type": ""}), 200

        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT store_name, location, type FROM stores WHERE store_id = ?",
                (store_id,),
            ).fetchone()
            if row:
                return jsonify({
                    "store_name": row["store_name"] or "",
                    "location": row["location"] or "",
                    "type": row["type"] or "",
                })
            return jsonify({"store_name": "", "location": "", "type": ""}), 200
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"[설정] store-info 조회 오류: {e}")
        return jsonify({"error": str(e)}), 500


@settings_bp.route("/store-info", methods=["POST"])
@login_required
def update_store_info():
    """매장 정보 수정."""
    try:
        body = request.get_json()
        store_name = (body.get("store_name") or "").strip()
        location = (body.get("location") or "").strip()

        if not store_name:
            return jsonify({"error": "매장명은 필수입니다"}), 400

        store_id = session.get("store_id")
        if not store_id:
            return jsonify({"error": "store_id를 찾을 수 없습니다"}), 400

        db_path = _get_common_db_path()
        conn = sqlite3.connect(str(db_path), timeout=10)
        try:
            conn.execute(
                "UPDATE stores SET store_name = ?, location = ?, updated_at = ? WHERE store_id = ?",
                (store_name, location, datetime.now().isoformat(), store_id),
            )
            conn.commit()
        finally:
            conn.close()

        session["full_name"] = store_name
        _log_audit("store_info", f"{store_id}", f"{store_name}|{location}", "store_info")

        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"[설정] store-info 저장 오류: {e}")
        return jsonify({"error": str(e)}), 500


# ── store-analysis ─────────────────────────────────────────

@settings_bp.route("/store-analysis", methods=["GET"])
@login_required
def get_store_analysis_api():
    """상권 분석 결과 조회."""
    try:
        store_id = session.get("store_id")
        if not store_id:
            return jsonify({}), 200

        from src.application.services.analysis_service import get_store_analysis
        data = get_store_analysis(store_id)
        return jsonify(data)
    except Exception as e:
        logger.error(f"[설정] store-analysis 조회 오류: {e}")
        return jsonify({"error": str(e)}), 500


@settings_bp.route("/reanalyze", methods=["POST"])
@login_required
def reanalyze_store():
    """상권 재분석 실행."""
    try:
        store_id = session.get("store_id")
        if not store_id:
            return jsonify({"error": "store_id를 찾을 수 없습니다"}), 400

        import threading
        from src.application.services.analysis_service import run_store_analysis, get_store_analysis

        # 쿨다운 체크 (60초)
        last = get_store_analysis(store_id)
        if last and last.get("analyzed_at"):
            from datetime import datetime
            last_time = datetime.fromisoformat(last["analyzed_at"])
            elapsed = (datetime.now() - last_time).total_seconds()
            if elapsed < 60:
                return jsonify({
                    "error": f"분석은 60초 후 다시 요청할 수 있습니다. ({int(60 - elapsed)}초 남음)"
                }), 429

        # 비동기 실행 (즉시 응답)
        thread = threading.Thread(
            target=run_store_analysis,
            args=(store_id,),
            daemon=True,
            name=f"reanalyze-{store_id}",
        )
        thread.start()

        return jsonify({"ok": True, "message": "분석이 시작되었습니다."})
    except Exception as e:
        logger.error(f"[설정] reanalyze 오류: {e}")
        return jsonify({"error": str(e)}), 500
