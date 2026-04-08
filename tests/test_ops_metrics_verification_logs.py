"""ops_metrics.collect_system() — verification_log_files 회귀 테스트
   (ops-metrics-monitor-extension, 2026-04-08)

waste_verification_reporter 매장별 분리 로직(fce1594) 회귀 감지.
"""

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.analysis.ops_metrics import OpsMetrics


@pytest.fixture
def fake_log_dir(tmp_path, monkeypatch):
    """data/logs를 tmp_path로 리다이렉트"""
    log_dir = tmp_path / "data" / "logs"
    log_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)  # collect_system이 Path("data/logs")를 사용
    return log_dir


def _make_active_ctx(store_id):
    ctx = MagicMock()
    ctx.store_id = store_id
    return ctx


@pytest.fixture
def patched_active_stores():
    """4매장 픽스처로 StoreContext.get_all_active() patch"""
    stores = [_make_active_ctx(s) for s in ["46513", "46704", "47863", "49965"]]
    with patch(
        "src.settings.store_context.StoreContext.get_all_active",
        return_value=stores,
    ):
        yield stores


class TestVerificationLogFiles:
    """collect_system() — verification_log_files_missing 회귀"""

    def test_all_4_files_present_returns_zero(
        self, fake_log_dir, patched_active_stores
    ):
        """4매장 어제 로그 모두 존재 → missing 0"""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        for sid in ["46513", "46704", "47863", "49965"]:
            (fake_log_dir / f"waste_verification_{sid}_{yesterday}.txt").write_text(
                "dummy", encoding="utf-8"
            )

        result = OpsMetrics.collect_system()
        info = result["verification_log_files"]
        assert info["missing_count"] == 0
        assert info["expected_count"] == 4
        assert info["missing_stores"] == []

    def test_missing_47863_detected(self, fake_log_dir, patched_active_stores):
        """47863만 누락 → missing_count=1, missing_stores=['47863']"""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        # 47863 빼고 3개만 생성
        for sid in ["46513", "46704", "49965"]:
            (fake_log_dir / f"waste_verification_{sid}_{yesterday}.txt").write_text(
                "dummy", encoding="utf-8"
            )

        result = OpsMetrics.collect_system()
        info = result["verification_log_files"]
        assert info["missing_count"] == 1
        assert info["missing_stores"] == ["47863"]
        assert info["expected_count"] == 4

    def test_all_missing_returns_full_count(
        self, fake_log_dir, patched_active_stores
    ):
        """모든 매장 누락 → missing_count=4"""
        # 파일 1개도 안 만듦
        result = OpsMetrics.collect_system()
        info = result["verification_log_files"]
        assert info["missing_count"] == 4
        assert set(info["missing_stores"]) == {"46513", "46704", "47863", "49965"}

    def test_old_file_not_counted_as_yesterday(
        self, fake_log_dir, patched_active_stores
    ):
        """그저께 파일은 어제 파일로 인정 안 됨"""
        day_before_yesterday = (date.today() - timedelta(days=2)).isoformat()
        for sid in ["46513", "46704", "47863", "49965"]:
            (
                fake_log_dir
                / f"waste_verification_{sid}_{day_before_yesterday}.txt"
            ).write_text("dummy", encoding="utf-8")

        result = OpsMetrics.collect_system()
        info = result["verification_log_files"]
        assert info["missing_count"] == 4  # 어제 기준으론 모두 missing
