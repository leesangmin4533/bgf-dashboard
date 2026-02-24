"""
발주 차이 피드백 조정기 테스트

- DiffFeedbackAdjuster: 제거 페널티 + 추가 부스트 로직
- OrderAnalysisRepository: removal/addition 통계 쿼리
- 통합: improved_predictor와의 연동
"""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.prediction.diff_feedback import DiffFeedbackAdjuster
from src.infrastructure.database.repos.order_analysis_repo import (
    OrderAnalysisRepository,
)


# ── 테스트 픽스처 ─────────────────────────────────────

@pytest.fixture
def analysis_repo(tmp_path):
    """임시 order_analysis.db"""
    db_path = str(tmp_path / "order_analysis.db")
    return OrderAnalysisRepository(db_path=db_path)


@pytest.fixture
def feedback_with_data(analysis_repo):
    """diff 데이터가 있는 DiffFeedbackAdjuster"""
    # 제거 데이터: A001 = 5회 제거, A002 = 11회 제거, A003 = 2회 제거
    for i in range(5):
        analysis_repo.save_diffs([{
            "store_id": "46513",
            "order_date": f"2026-02-{10+i:02d}",
            "receiving_date": "2026-02-18",
            "item_cd": "A001", "item_nm": "진라면",
            "mid_cd": "032", "diff_type": "removed",
            "auto_order_qty": 6, "confirmed_order_qty": 0,
        }])
    # A001: 1회 unchanged (총 출현 6회)
    analysis_repo.save_diffs([{
        "store_id": "46513",
        "order_date": "2026-02-15",
        "receiving_date": "2026-02-16",
        "item_cd": "A001", "item_nm": "진라면",
        "mid_cd": "032", "diff_type": "unchanged",
        "auto_order_qty": 6, "confirmed_order_qty": 6,
    }])

    for i in range(11):
        analysis_repo.save_diffs([{
            "store_id": "46513",
            "order_date": f"2026-02-{(i % 17) + 1:02d}",
            "receiving_date": "2026-02-18",
            "item_cd": "A002", "item_nm": "캔디왕",
            "mid_cd": "020", "diff_type": "removed",
            "auto_order_qty": 3, "confirmed_order_qty": 0,
        }])

    for i in range(2):
        analysis_repo.save_diffs([{
            "store_id": "46513",
            "order_date": f"2026-02-{15+i:02d}",
            "receiving_date": "2026-02-18",
            "item_cd": "A003", "item_nm": "생수",
            "mid_cd": "039", "diff_type": "removed",
            "auto_order_qty": 10, "confirmed_order_qty": 0,
        }])

    # 추가 데이터: B001 = 5회 추가, B002 = 2회 추가
    for i in range(5):
        analysis_repo.save_diffs([{
            "store_id": "46513",
            "order_date": f"2026-02-{10+i:02d}",
            "receiving_date": "2026-02-18",
            "item_cd": "B001", "item_nm": "바나나우유",
            "mid_cd": "047", "diff_type": "added",
            "auto_order_qty": 0, "confirmed_order_qty": 4 + i,
        }])

    for i in range(2):
        analysis_repo.save_diffs([{
            "store_id": "46513",
            "order_date": f"2026-02-{15+i:02d}",
            "receiving_date": "2026-02-18",
            "item_cd": "B002", "item_nm": "흰우유",
            "mid_cd": "047", "diff_type": "added",
            "auto_order_qty": 0, "confirmed_order_qty": 3,
        }])

    # DiffFeedbackAdjuster 생성 (테스트용 repo 주입)
    adjuster = DiffFeedbackAdjuster(store_id="46513")
    # repo 직접 주입 (load_feedback 내부의 repo 대신)
    adjuster._removal_cache = {}
    adjuster._addition_cache = {}

    # 직접 통계 로드
    removal_stats = analysis_repo.get_item_removal_stats("46513", days=30)
    for row in removal_stats:
        adjuster._removal_cache[row["item_cd"]] = {
            "item_nm": row.get("item_nm", ""),
            "mid_cd": row.get("mid_cd", ""),
            "removal_count": row["removal_count"],
            "total_appearances": row["total_appearances"],
        }

    addition_stats = analysis_repo.get_item_addition_stats("46513", days=30)
    for row in addition_stats:
        adjuster._addition_cache[row["item_cd"]] = {
            "item_nm": row.get("item_nm", ""),
            "mid_cd": row.get("mid_cd", ""),
            "addition_count": row["addition_count"],
            "avg_qty": row.get("avg_qty", 1),
        }

    adjuster._cache_loaded = True
    return adjuster


# ── DiffFeedbackAdjuster 테스트 ────────────────────────


class TestDiffFeedbackAdjuster:
    """제거 페널티 + 추가 부스트 테스트"""

    def test_removal_penalty_threshold_3to5(self, feedback_with_data):
        """A001: 5회 제거 → 3~5 범위 → 0.7"""
        # A001은 5회 제거됨 (3 <= 5 < 6)
        penalty = feedback_with_data.get_removal_penalty("A001")
        assert penalty == 0.7

    def test_removal_penalty_threshold_10(self, feedback_with_data):
        """A002: 11회 제거 → 10+ → 0.3"""
        penalty = feedback_with_data.get_removal_penalty("A002")
        assert penalty == 0.3

    def test_no_penalty_below_threshold(self, feedback_with_data):
        """A003: 2회 제거 → 임계값 미만 → 1.0 (페널티 없음)"""
        penalty = feedback_with_data.get_removal_penalty("A003")
        assert penalty == 1.0

    def test_no_penalty_unknown_item(self, feedback_with_data):
        """알 수 없는 상품 → 1.0"""
        penalty = feedback_with_data.get_removal_penalty("UNKNOWN")
        assert penalty == 1.0

    def test_addition_boost_above_threshold(self, feedback_with_data):
        """B001: 5회 추가 → 부스트 대상"""
        boost = feedback_with_data.get_addition_boost("B001")
        assert boost["should_inject"] is True
        assert boost["addition_count"] == 5
        assert boost["boost_qty"] >= 1

    def test_addition_boost_below_threshold(self, feedback_with_data):
        """B002: 2회 추가 → 미달"""
        boost = feedback_with_data.get_addition_boost("B002")
        assert boost["should_inject"] is False
        assert boost["addition_count"] == 2

    def test_addition_boost_unknown(self, feedback_with_data):
        """알 수 없는 상품 → 부스트 없음"""
        boost = feedback_with_data.get_addition_boost("UNKNOWN")
        assert boost["should_inject"] is False
        assert boost["addition_count"] == 0

    def test_frequently_added_items(self, feedback_with_data):
        """반복 추가 상품 목록"""
        items = feedback_with_data.get_frequently_added_items()
        # B001만 3회 이상 (5회)
        assert len(items) == 1
        assert items[0]["item_cd"] == "B001"
        assert items[0]["count"] == 5

    def test_frequently_added_custom_threshold(self, feedback_with_data):
        """커스텀 임계값으로 반복 추가 조회"""
        items = feedback_with_data.get_frequently_added_items(min_count=2)
        # B001(5회), B002(2회) 모두 포함
        assert len(items) == 2
        item_cds = {i["item_cd"] for i in items}
        assert "B001" in item_cds
        assert "B002" in item_cds

    def test_penalty_preserves_min_qty(self):
        """페널티 적용 시 최소 1개 보장 (호출 측 책임)"""
        # 시뮬레이션: order_qty=2, penalty=0.3 → int(2 * 0.3) = 0 → max(1, 0) = 1
        order_qty = 2
        penalty = 0.3
        result = max(1, int(order_qty * penalty))
        assert result == 1


class TestDiffFeedbackEmpty:
    """데이터 없을 때 동작"""

    def test_empty_cache(self):
        """캐시 비어있으면 모두 기본값"""
        adj = DiffFeedbackAdjuster(store_id="46513")
        adj._cache_loaded = True  # 빈 캐시

        assert adj.get_removal_penalty("ANY") == 1.0
        assert adj.get_addition_boost("ANY")["should_inject"] is False
        assert adj.get_frequently_added_items() == []
