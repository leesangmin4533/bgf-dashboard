"""설정 관리 웹 UI 테스트.

Design Reference: docs/02-design/features/settings-web-ui.design.md
테스트 항목:
1. eval-params GET (1)
2. eval-params POST (1)
3. eval-params 범위 검증 (1)
4. eval-params 개별 리셋 (1)
5. eval-params 전체 리셋 (1)
6. feature-flags GET (1)
7. feature-flags POST (1)
8. audit-log 기록 (1)
9. audit-log 조회 (1)
10. Blueprint 등록 (1)
"""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask


@pytest.fixture
def settings_app(tmp_path):
    """테스트용 Flask 앱 + eval_params + common.db."""
    # eval_params.json
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    params = {
        "daily_avg_days": {
            "value": 14.0,
            "default": 14.0,
            "min": 7.0,
            "max": 30.0,
            "description": "일평균 판매량 계산 기간",
        },
        "weight_daily_avg": {
            "value": 0.5,
            "default": 0.4,
            "min": 0.2,
            "max": 0.55,
            "description": "가중치: 일평균",
        },
        "cost_optimization": {
            "enabled": True,
            "cost_ratio_high": 0.5,
        },
    }
    (config_dir / "eval_params.json").write_text(json.dumps(params), encoding="utf-8")
    (config_dir / "eval_params.default.json").write_text(json.dumps(params), encoding="utf-8")

    # common.db
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = sqlite3.connect(str(data_dir / "common.db"))
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
    conn.commit()
    conn.close()

    with patch("src.web.routes.api_settings.PROJECT_ROOT", tmp_path), \
         patch("src.web.routes.api_settings.CONFIG_DIR", config_dir), \
         patch("src.web.routes.api_settings.EVAL_PARAMS_PATH", config_dir / "eval_params.json"), \
         patch("src.web.routes.api_settings.EVAL_PARAMS_DEFAULT_PATH", config_dir / "eval_params.default.json"):

        from src.web.routes.api_settings import settings_bp
        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.register_blueprint(settings_bp, url_prefix="/api/settings")

        # admin 세션 주입
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = 1
                sess["username"] = "admin"
                sess["role"] = "admin"
            yield client


# ──────────────────────────────────────────────────────────────
# 1. eval-params GET
# ──────────────────────────────────────────────────────────────

class TestGetEvalParams:
    def test_get_eval_params_format(self, settings_app):
        """eval-params GET 응답에 params, nested, groups 존재."""
        resp = settings_app.get("/api/settings/eval-params")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "params" in data
        assert "nested" in data
        assert "groups" in data
        # 단순 파라미터 확인
        assert "daily_avg_days" in data["params"]
        assert data["params"]["daily_avg_days"]["value"] == 14.0
        # 중첩 객체는 nested에
        assert "cost_optimization" in data["nested"]


# ──────────────────────────────────────────────────────────────
# 2. eval-params POST
# ──────────────────────────────────────────────────────────────

class TestUpdateEvalParam:
    def test_update_eval_param(self, settings_app):
        """파라미터 수정 동작."""
        resp = settings_app.post(
            "/api/settings/eval-params",
            json={"key": "daily_avg_days", "value": 21.0},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["old_value"] == 14.0
        assert data["new_value"] == 21.0

        # 실제 변경 확인
        resp2 = settings_app.get("/api/settings/eval-params")
        assert resp2.get_json()["params"]["daily_avg_days"]["value"] == 21.0


# ──────────────────────────────────────────────────────────────
# 3. eval-params 범위 검증
# ──────────────────────────────────────────────────────────────

class TestEvalParamRange:
    def test_reject_out_of_range(self, settings_app):
        """범위 밖 값 거부."""
        # max=30 초과
        resp = settings_app.post(
            "/api/settings/eval-params",
            json={"key": "daily_avg_days", "value": 50.0},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "최대값" in resp.get_json()["error"]

        # min=7 미만
        resp2 = settings_app.post(
            "/api/settings/eval-params",
            json={"key": "daily_avg_days", "value": 3.0},
            content_type="application/json",
        )
        assert resp2.status_code == 400
        assert "최소값" in resp2.get_json()["error"]


# ──────────────────────────────────────────────────────────────
# 4. eval-params 개별 리셋
# ──────────────────────────────────────────────────────────────

class TestResetEvalParam:
    def test_reset_single_param(self, settings_app):
        """개별 파라미터 리셋."""
        # 먼저 값 변경
        settings_app.post(
            "/api/settings/eval-params",
            json={"key": "weight_daily_avg", "value": 0.3},
            content_type="application/json",
        )
        # 리셋
        resp = settings_app.post(
            "/api/settings/eval-params/reset",
            json={"key": "weight_daily_avg"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["reset_count"] == 1

        # 기본값 복원 확인
        resp2 = settings_app.get("/api/settings/eval-params")
        assert resp2.get_json()["params"]["weight_daily_avg"]["value"] == 0.4


# ──────────────────────────────────────────────────────────────
# 5. eval-params 전체 리셋
# ──────────────────────────────────────────────────────────────

class TestResetAllEvalParams:
    def test_reset_all_params(self, settings_app):
        """전체 리셋."""
        # 값 변경
        settings_app.post(
            "/api/settings/eval-params",
            json={"key": "daily_avg_days", "value": 21.0},
            content_type="application/json",
        )
        settings_app.post(
            "/api/settings/eval-params",
            json={"key": "weight_daily_avg", "value": 0.3},
            content_type="application/json",
        )
        # 전체 리셋
        resp = settings_app.post(
            "/api/settings/eval-params/reset",
            json={"key": "__all__"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["reset_count"] >= 2


# ──────────────────────────────────────────────────────────────
# 6. feature-flags GET
# ──────────────────────────────────────────────────────────────

class TestGetFeatureFlags:
    def test_get_feature_flags_format(self, settings_app):
        """feature-flags GET 응답 형식."""
        resp = settings_app.get("/api/settings/feature-flags")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "flags" in data
        assert len(data["flags"]) > 0
        flag = data["flags"][0]
        assert "key" in flag
        assert "value" in flag
        assert "description" in flag


# ──────────────────────────────────────────────────────────────
# 7. feature-flags POST
# ──────────────────────────────────────────────────────────────

class TestToggleFeatureFlag:
    def test_toggle_feature_flag(self, settings_app):
        """토글 전환 동작."""
        resp = settings_app.post(
            "/api/settings/feature-flags",
            json={"key": "DIFF_FEEDBACK_ENABLED", "value": False},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["key"] == "DIFF_FEEDBACK_ENABLED"
        assert data["new_value"] is False


# ──────────────────────────────────────────────────────────────
# 8. audit-log 기록
# ──────────────────────────────────────────────────────────────

class TestAuditLogRecorded:
    def test_change_creates_audit_log(self, settings_app):
        """파라미터 변경 시 감사 로그 기록."""
        settings_app.post(
            "/api/settings/eval-params",
            json={"key": "daily_avg_days", "value": 20.0},
            content_type="application/json",
        )
        resp = settings_app.get("/api/settings/audit-log?hours=1")
        data = resp.get_json()
        logs = data["logs"]
        assert len(logs) >= 1
        found = any(l["setting_key"] == "daily_avg_days" for l in logs)
        assert found


# ──────────────────────────────────────────────────────────────
# 9. audit-log 조회
# ──────────────────────────────────────────────────────────────

class TestAuditLogQuery:
    def test_audit_log_format(self, settings_app):
        """감사 로그 응답 형식."""
        resp = settings_app.get("/api/settings/audit-log?hours=168")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "logs" in data
        assert isinstance(data["logs"], list)


# ──────────────────────────────────────────────────────────────
# 10. Blueprint 등록
# ──────────────────────────────────────────────────────────────

class TestBlueprintRegistered:
    def test_settings_blueprint_in_register(self):
        """register_blueprints에 settings_bp 등록."""
        import importlib
        mod = importlib.import_module("src.web.routes")
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "settings_bp" in source
        assert "/api/settings" in source
