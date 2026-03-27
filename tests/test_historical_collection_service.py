"""
HistoricalCollectionService 테스트

BGF 스크래핑 의존 함수(_collect_in_batches)는 Mock 처리.
단위 테스트 가능한 함수만 직접 테스트.
"""

import re
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from src.application.services.historical_collection_service import (
    _generate_date_list,
    collect_historical_sales,
)


# =============================================================================
# _generate_date_list 테스트
# =============================================================================
class TestGenerateDateList:
    """날짜 리스트 생성 검증."""

    def test_one_month_returns_about_30_days(self):
        """months=1 → 약 30일치 반환."""
        dates = _generate_date_list(months=1)
        # 30*1 + 1 = 31일 (start~end 포함)
        assert 28 <= len(dates) <= 32

    def test_date_format_yyyy_mm_dd(self):
        """날짜 형식이 YYYY-MM-DD인지 확인."""
        dates = _generate_date_list(months=1)
        pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        for d in dates:
            assert pattern.match(d), f"잘못된 형식: {d}"

    def test_dates_in_ascending_order(self):
        """오름차순 정렬 확인 (과거 → 현재)."""
        dates = _generate_date_list(months=1)
        assert dates == sorted(dates)

    def test_last_date_is_yesterday(self):
        """마지막 날짜가 어제인지 확인."""
        dates = _generate_date_list(months=1)
        yesterday = (datetime.now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
        assert dates[-1] == yesterday

    def test_six_months_returns_about_180_days(self):
        """months=6 → 약 180일치 반환."""
        dates = _generate_date_list(months=6)
        # 30*6 + 1 = 181일
        assert 175 <= len(dates) <= 185

    def test_no_duplicates(self):
        """중복 날짜 없음."""
        dates = _generate_date_list(months=3)
        assert len(dates) == len(set(dates))


# =============================================================================
# _filter_already_collected 테스트
# =============================================================================
class TestFilterAlreadyCollected:
    """이미 수집된 날짜 제외 검증."""

    def test_excludes_collected_dates(self):
        """수집 완료 날짜가 결과에서 제외됨."""
        all_dates = ["2026-03-10", "2026-03-11", "2026-03-12", "2026-03-13"]
        collected = {"2026-03-10", "2026-03-12"}  # 2일 수집 완료

        mock_repo = MagicMock()
        mock_repo.get_collected_dates.return_value = list(collected)

        with patch(
            "src.infrastructure.database.repos.sales_repo.SalesRepository",
            return_value=mock_repo,
        ):
            from src.application.services.historical_collection_service import (
                _filter_already_collected,
            )
            result = _filter_already_collected(all_dates, "46513")

        assert result == ["2026-03-11", "2026-03-13"]

    def test_all_collected_returns_empty(self):
        """전부 수집 완료이면 빈 리스트."""
        all_dates = ["2026-03-10", "2026-03-11"]

        mock_repo = MagicMock()
        mock_repo.get_collected_dates.return_value = all_dates

        with patch(
            "src.infrastructure.database.repos.sales_repo.SalesRepository",
            return_value=mock_repo,
        ):
            from src.application.services.historical_collection_service import (
                _filter_already_collected,
            )
            result = _filter_already_collected(all_dates, "46513")

        assert result == []

    def test_none_collected_returns_all(self):
        """하나도 수집 안 됐으면 전체 반환."""
        all_dates = ["2026-03-10", "2026-03-11", "2026-03-12"]

        mock_repo = MagicMock()
        mock_repo.get_collected_dates.return_value = []

        with patch(
            "src.infrastructure.database.repos.sales_repo.SalesRepository",
            return_value=mock_repo,
        ):
            from src.application.services.historical_collection_service import (
                _filter_already_collected,
            )
            result = _filter_already_collected(all_dates, "46513")

        assert result == all_dates


# =============================================================================
# collect_historical_sales 테스트
# =============================================================================
class TestCollectHistoricalSales:
    """collect_historical_sales() 통합 동작 검증 (내부 함수 Mock)."""

    def test_all_already_collected(self):
        """이미 전부 수집된 경우 → success=0, skipped=전체, success_rate=100.0."""
        with patch(
            "src.application.services.historical_collection_service._generate_date_list",
            return_value=["2026-03-10", "2026-03-11", "2026-03-12"],
        ):
            with patch(
                "src.application.services.historical_collection_service._filter_already_collected",
                return_value=[],  # 전부 수집 완료
            ):
                result = collect_historical_sales("46513", months=1)

        assert result["success"] == 0
        assert result["failed"] == 0
        assert result["skipped"] == 3
        assert result["total_dates"] == 3
        assert result["success_rate"] == 100.0

    def test_batch_collection_exception(self):
        """BGF 수집 실패 → error 키 포함, 예외 전파 없음."""
        with patch(
            "src.application.services.historical_collection_service._generate_date_list",
            return_value=["2026-03-10", "2026-03-11"],
        ):
            with patch(
                "src.application.services.historical_collection_service._filter_already_collected",
                return_value=["2026-03-10", "2026-03-11"],
            ):
                with patch(
                    "src.application.services.historical_collection_service._collect_in_batches",
                    side_effect=Exception("BGF 로그인 실패"),
                ):
                    result = collect_historical_sales("46513", months=1)

        assert "error" in result
        assert "BGF" in result["error"]
        assert result["success"] == 0

    def test_normal_collection(self):
        """정상 수집 → success=5, failed=1, success_rate 계산."""
        all_dates = [f"2026-03-{i:02d}" for i in range(1, 11)]  # 10일

        with patch(
            "src.application.services.historical_collection_service._generate_date_list",
            return_value=all_dates,
        ):
            with patch(
                "src.application.services.historical_collection_service._filter_already_collected",
                return_value=all_dates[4:],  # 6일 미수집
            ):
                with patch(
                    "src.application.services.historical_collection_service._collect_in_batches",
                    return_value=(5, 1),  # 5 성공, 1 실패
                ):
                    result = collect_historical_sales("46513", months=1)

        assert result["success"] == 5
        assert result["failed"] == 1
        assert result["skipped"] == 4  # 10 - 6
        assert result["total_dates"] == 10
        # success_rate = 5/6 * 100 ≈ 83.3
        assert 83.0 <= result["success_rate"] <= 84.0
        assert "elapsed_minutes" in result

    def test_result_has_all_required_keys(self):
        """결과 dict에 필수 키 포함."""
        with patch(
            "src.application.services.historical_collection_service._generate_date_list",
            return_value=["2026-03-10"],
        ):
            with patch(
                "src.application.services.historical_collection_service._filter_already_collected",
                return_value=[],
            ):
                result = collect_historical_sales("46513", months=1)

        required_keys = {"total_dates", "success", "failed", "skipped", "success_rate", "elapsed_minutes"}
        assert required_keys.issubset(set(result.keys()))
