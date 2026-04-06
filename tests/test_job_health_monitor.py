"""job-health-monitor feature 유닛 테스트."""

import sqlite3
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════

@pytest.fixture
def tmp_job_runs_db(tmp_path):
    """테스트용 임시 DB (job_runs 테이블만)."""
    db_path = tmp_path / "test_job_runs.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE job_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            job_name        TEXT NOT NULL,
            store_id        TEXT,
            scheduled_for   TEXT NOT NULL,
            started_at      TEXT NOT NULL,
            ended_at        TEXT,
            status          TEXT NOT NULL,
            error_message   TEXT,
            error_type      TEXT,
            duration_sec    REAL,
            alerted         INTEGER DEFAULT 0,
            created_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX idx_job_runs_missed_unique
            ON job_runs(job_name, COALESCE(store_id, ''), scheduled_for)
            WHERE status = 'missed';
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def repo(tmp_job_runs_db):
    from src.infrastructure.database.repos.job_run_repo import JobRunRepository
    return JobRunRepository(db_path=tmp_job_runs_db)


# ═══════════════════════════════════════════════════
# compute_scheduled_for
# ═══════════════════════════════════════════════════

class TestComputeScheduledFor:
    def test_daily_future_becomes_yesterday(self):
        from src.infrastructure.job_health.job_run_tracker import compute_scheduled_for
        now = datetime.now()
        future_hour = (now.hour + 3) % 24
        result = compute_scheduled_for(schedule_str=f"{future_hour:02d}:00")
        parsed = datetime.fromisoformat(result)
        assert parsed <= now, "미래 시각이면 하루 전으로 밀려야 함"

    def test_daily_past_is_today(self):
        from src.infrastructure.job_health.job_run_tracker import compute_scheduled_for
        now = datetime.now()
        if now.hour < 1:
            pytest.skip("00시대는 테스트 안정성 낮음")
        past_hour = now.hour - 1
        result = compute_scheduled_for(schedule_str=f"{past_hour:02d}:00")
        parsed = datetime.fromisoformat(result)
        assert parsed.date() == now.date()

    def test_weekly_picks_latest_matching_weekday(self):
        from src.infrastructure.job_health.job_run_tracker import compute_scheduled_for
        result = compute_scheduled_for(schedule_str="Mon 08:00")
        parsed = datetime.fromisoformat(result)
        assert parsed.weekday() == 0, "Mon=0"

    def test_multiple_schedules_picks_most_recent(self):
        from src.infrastructure.job_health.job_run_tracker import compute_scheduled_for
        result = compute_scheduled_for(schedules=["21:00", "13:00", "09:00", "01:00"])
        parsed = datetime.fromisoformat(result)
        assert parsed <= datetime.now()


# ═══════════════════════════════════════════════════
# JobRunRepository
# ═══════════════════════════════════════════════════

class TestJobRunRepo:
    def test_insert_update_roundtrip(self, repo):
        rid = repo.insert_running("daily_order", "46513", "2026-04-07T07:00:00", "2026-04-07T07:00:01")
        assert rid > 0
        repo.update_end(rid, "success", "2026-04-07T07:10:00", 599.0)
        row = repo.get_last_success("daily_order", "46513", "2026-04-07T07:00:00")
        assert row is not None
        assert row["status"] == "success"
        assert row["duration_sec"] == 599.0

    def test_missed_dedup(self, repo):
        first = repo.insert_missed("daily_order", "46513", "2026-04-07T07:00:00")
        second = repo.insert_missed("daily_order", "46513", "2026-04-07T07:00:00")
        assert first is not None
        assert second is None, "부분 UNIQUE 인덱스로 중복 차단"

    def test_mark_alerted_atomic(self, repo):
        rid = repo.insert_running("daily_order", "46513", "2026-04-07T07:00:00", "2026-04-07T07:00:01")
        repo.update_end(rid, "failed", "2026-04-07T07:00:02", 1.0, error_message="boom", error_type="ValueError")
        assert repo.mark_alerted(rid) is True
        assert repo.mark_alerted(rid) is False, "두 번째는 False (이미 alerted)"

    def test_get_recent_failed_unalerted(self, repo):
        rid = repo.insert_running("daily_order", "46513", "2026-04-07T07:00:00", datetime.now().isoformat())
        repo.update_end(rid, "failed", datetime.now().isoformat(), 1.0, error_message="x", error_type="Err")
        rows = repo.get_recent_failed_unalerted(hours=1)
        assert len(rows) == 1
        assert rows[0]["job_name"] == "daily_order"

    def test_purge_old(self, repo):
        # 60일 전 레코드를 직접 주입
        conn = sqlite3.connect(repo._db_path)
        old_ts = (datetime.now() - timedelta(days=60)).isoformat()
        conn.execute(
            "INSERT INTO job_runs (job_name, store_id, scheduled_for, started_at, status) VALUES (?,?,?,?,?)",
            ("old_job", None, old_ts, old_ts, "success"),
        )
        conn.commit()
        conn.close()
        deleted = repo.purge_old(retention_days=30)
        assert deleted == 1


# ═══════════════════════════════════════════════════
# JobRunTracker context manager
# ═══════════════════════════════════════════════════

class TestJobRunTracker:
    def test_success_path(self, repo, monkeypatch):
        from src.infrastructure.job_health import job_run_tracker as jrt
        monkeypatch.setattr(jrt, "JobRunRepository", lambda: repo)

        with jrt.JobRunTracker("test_job", "46513", "2026-04-07T07:00:00"):
            pass

        row = repo.get_last_success("test_job", "46513", "2026-04-07T07:00:00")
        assert row["status"] == "success"

    def test_failure_path_propagates(self, repo, monkeypatch):
        from src.infrastructure.job_health import job_run_tracker as jrt
        monkeypatch.setattr(jrt, "JobRunRepository", lambda: repo)
        # Dispatcher는 실패 경로에서 호출되므로 mock
        mock_dispatcher = MagicMock()
        with patch("src.application.services.notification_dispatcher.NotificationDispatcher", return_value=mock_dispatcher):
            with pytest.raises(ValueError, match="boom"):
                with jrt.JobRunTracker("test_fail", "46513", "2026-04-07T07:00:00"):
                    raise ValueError("boom")

        # failed로 기록 + dispatcher 호출
        conn = sqlite3.connect(repo._db_path)
        row = conn.execute("SELECT status, error_type, error_message FROM job_runs WHERE job_name='test_fail'").fetchone()
        conn.close()
        assert row[0] == "failed"
        assert row[1] == "ValueError"
        assert "boom" in row[2]
        mock_dispatcher.send.assert_called_once()

    def test_feature_flag_off_transparent_call(self, monkeypatch):
        from src.infrastructure.job_health import job_run_tracker as jrt

        # 피처 플래그 OFF 시 wrap이 원본 반환
        monkeypatch.setattr(jrt, "_is_enabled", lambda: False)

        class Ctx:
            store_id = "46513"

        calls = []
        def raw(ctx):
            calls.append(ctx.store_id)
            return "ok"

        wrapped = jrt.wrap_multistore_task("test", raw, "2026-04-07T07:00:00")
        # 피처 플래그 OFF → wrapped is raw (동일 객체)
        assert wrapped is raw
        assert wrapped(Ctx()) == "ok"
        assert calls == ["46513"]

    def test_feature_flag_on_wraps(self, repo, monkeypatch):
        from src.infrastructure.job_health import job_run_tracker as jrt
        monkeypatch.setattr(jrt, "_is_enabled", lambda: True)
        monkeypatch.setattr(jrt, "JobRunRepository", lambda: repo)

        class Ctx:
            store_id = "46513"

        def raw(ctx):
            return "ran"

        wrapped = jrt.wrap_multistore_task("test_wrap", raw, "2026-04-07T07:00:00")
        assert wrapped is not raw  # 래퍼
        result = wrapped(Ctx())
        assert result == "ran"

        row = repo.get_last_success("test_wrap", "46513", "2026-04-07T07:00:00")
        assert row is not None


# ═══════════════════════════════════════════════════
# NotificationDispatcher fallback
# ═══════════════════════════════════════════════════

class TestNotificationDispatcher:
    def test_kakao_success_no_fallback(self, tmp_path, monkeypatch):
        from src.application.services import notification_dispatcher as nd
        # 임시 alerts log
        monkeypatch.setattr(nd, "_ALERTS_LOG_PATH", tmp_path / "alerts.log")
        monkeypatch.setattr(nd, "_alerts_logger", None)

        mock_kakao = MagicMock()
        mock_kakao.send_message.return_value = True

        dispatcher = nd.NotificationDispatcher(kakao_notifier=mock_kakao)
        ok = dispatcher.send("SUBJ", "BODY", level="WARNING")
        assert ok is True
        mock_kakao.send_message.assert_called_once()
        # fallback 파일 미생성
        assert not (tmp_path / "alerts.log").exists()

    def test_kakao_failure_triggers_fallback(self, tmp_path, monkeypatch, capsys):
        from src.application.services import notification_dispatcher as nd
        monkeypatch.setattr(nd, "_ALERTS_LOG_PATH", tmp_path / "alerts.log")
        monkeypatch.setattr(nd, "_alerts_logger", None)

        mock_kakao = MagicMock()
        mock_kakao.send_message.side_effect = Exception("token expired")

        dispatcher = nd.NotificationDispatcher(kakao_notifier=mock_kakao)
        ok = dispatcher.send("SUBJ", "BODY", level="CRITICAL")
        assert ok is False
        # fallback log file 생성
        assert (tmp_path / "alerts.log").exists()
        content = (tmp_path / "alerts.log").read_text(encoding="utf-8")
        assert "SUBJ" in content and "BODY" in content

    def test_kakao_returns_false_triggers_fallback(self, tmp_path, monkeypatch):
        from src.application.services import notification_dispatcher as nd
        monkeypatch.setattr(nd, "_ALERTS_LOG_PATH", tmp_path / "alerts.log")
        monkeypatch.setattr(nd, "_alerts_logger", None)

        mock_kakao = MagicMock()
        mock_kakao.send_message.return_value = False  # 명시적 실패

        dispatcher = nd.NotificationDispatcher(kakao_notifier=mock_kakao)
        ok = dispatcher.send("SUBJ", "BODY")
        assert ok is False
        assert (tmp_path / "alerts.log").exists()


# ═══════════════════════════════════════════════════
# JobHealthChecker missed detection
# ═══════════════════════════════════════════════════

class TestJobHealthChecker:
    def test_ensure_missed_not_double_recorded(self, repo, monkeypatch):
        from src.application.services.job_health_checker import JobHealthChecker
        checker = JobHealthChecker()
        monkeypatch.setattr(checker, "_repo", repo)

        # 첫 호출 → insert
        checker._ensure_missed_recorded("daily_order", "46513", "2026-04-07T07:00:00")
        # 두 번째 호출 → 중복 차단
        checker._ensure_missed_recorded("daily_order", "46513", "2026-04-07T07:00:00")

        conn = sqlite3.connect(repo._db_path)
        count = conn.execute("SELECT COUNT(*) FROM job_runs WHERE status='missed'").fetchone()[0]
        conn.close()
        assert count == 1

    def test_ensure_missed_skipped_when_success_exists(self, repo, monkeypatch):
        from src.application.services.job_health_checker import JobHealthChecker
        # success 기록 선행
        rid = repo.insert_running("daily_order", "46513", "2026-04-07T07:00:00", "2026-04-07T07:00:01")
        repo.update_end(rid, "success", "2026-04-07T07:05:00", 299.0)

        checker = JobHealthChecker()
        monkeypatch.setattr(checker, "_repo", repo)
        checker._ensure_missed_recorded("daily_order", "46513", "2026-04-07T07:00:00")

        conn = sqlite3.connect(repo._db_path)
        count = conn.execute("SELECT COUNT(*) FROM job_runs WHERE status='missed'").fetchone()[0]
        conn.close()
        assert count == 0, "success 있으면 missed 기록 안 함"

    def test_dispatch_dedup_across_tracker_and_checker(self, repo, monkeypatch):
        """크로스 레이어: Tracker가 failed 기록 + 알림 마킹 → Checker가 다시 알림 안 함."""
        from src.application.services.job_health_checker import JobHealthChecker

        # Tracker가 failed + alerted=1 기록
        rid = repo.insert_running("daily_order", "46513", "2026-04-07T07:00:00", datetime.now().isoformat())
        repo.update_end(rid, "failed", datetime.now().isoformat(), 1.0, error_message="x", error_type="Err")
        repo.mark_alerted(rid)  # Tracker가 알림 마킹

        # Checker가 dispatch 시도
        checker = JobHealthChecker()
        monkeypatch.setattr(checker, "_repo", repo)
        with patch("src.application.services.notification_dispatcher.NotificationDispatcher") as MockDisp:
            checker._dispatch_failed()
            # alerted=1 이므로 호출 없음
            MockDisp.return_value.send.assert_not_called()
