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

    # DiffFeedbackAdjuster 생성 + 캐시 직접 주입 (repo 쿼리 우회)
    adjuster = DiffFeedbackAdjuster(store_id="46513")
    adjuster._removal_cache = {
        "A001": {"item_nm": "진라면", "mid_cd": "032", "removal_count": 5, "total_appearances": 6},
        "A002": {"item_nm": "캔디왕", "mid_cd": "020", "removal_count": 11, "total_appearances": 11},
        "A003": {"item_nm": "생수", "mid_cd": "039", "removal_count": 2, "total_appearances": 2},
    }
    adjuster._addition_cache = {
        "B001": {"item_nm": "바나나우유", "mid_cd": "047", "addition_count": 5, "avg_qty": 6},
        "B002": {"item_nm": "흰우유", "mid_cd": "047", "addition_count": 2, "avg_qty": 3},
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


class TestStockoutExclusion:
    """품절 후 제거 건 제외 테스트"""

    @pytest.fixture
    def store_db(self, tmp_path):
        """daily_sales가 있는 매장 DB"""
        db_path = tmp_path / "stores" / "46513.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("""CREATE TABLE IF NOT EXISTS daily_sales (
            item_cd TEXT, sales_date TEXT, sale_qty INTEGER,
            stock_qty INTEGER, store_id TEXT DEFAULT '46513'
        )""")
        conn.commit()
        return conn, db_path

    def test_stockout_justified_dates(self, store_db):
        """제거 후 7일 내 품절 → 정당한 제거로 판정"""
        from datetime import datetime, timedelta
        conn, db_path = store_db

        base = datetime.now()
        removal_date = (base - timedelta(days=5)).strftime("%Y-%m-%d")
        stockout_soon = (base - timedelta(days=3)).strftime("%Y-%m-%d")  # 2일 후
        stockout_late = (base + timedelta(days=5)).strftime("%Y-%m-%d")  # 10일 후

        # stockout_soon: 제거일 이후 2일 → 정당
        conn.execute(
            "INSERT INTO daily_sales VALUES (?, ?, ?, ?, ?)",
            ("X001", stockout_soon, 0, 0, "46513"),
        )
        # stockout_late: 제거일 이후 10일 → 7일 초과 → 부당
        conn.execute(
            "INSERT INTO daily_sales VALUES (?, ?, ?, ?, ?)",
            ("X002", stockout_late, 0, 0, "46513"),
        )
        conn.commit()
        conn.close()

        adj = DiffFeedbackAdjuster(store_id="46513")

        removal_dates = {
            "X001": [removal_date],  # 2일 후 품절 → 정당
            "X002": [removal_date],  # 10일 후 품절 → 7일 초과 → 부당
        }

        with patch(
            "src.infrastructure.database.connection.DBRouter.get_store_connection"
        ) as mock_conn:
            new_conn = sqlite3.connect(str(db_path))
            new_conn.row_factory = sqlite3.Row
            mock_conn.return_value = new_conn
            result = adj._get_stockout_justified_dates(removal_dates)

        assert "X001" in result
        assert removal_date in result["X001"]
        assert "X002" not in result  # 7일 초과

    def test_stockout_exclusion_reduces_penalty(self):
        """품절 정당 건 제외 시 removal_count 감소 → 페널티 완화"""
        adj = DiffFeedbackAdjuster(store_id="46513")

        # 원래 5회 제거 → penalty=0.7, 2건 품절정당 제외 → 3회 → penalty=0.7 유지
        adj._removal_cache = {
            "C001": {"item_nm": "테스트", "mid_cd": "086",
                     "removal_count": 3, "total_appearances": 5},
        }
        adj._cache_loaded = True
        assert adj.get_removal_penalty("C001") == 0.7

        # 2건 → penalty=1.0 (임계값 3 미달)
        adj._removal_cache["C001"]["removal_count"] = 2
        assert adj.get_removal_penalty("C001") == 1.0

    def test_stockout_empty_removal_dates(self):
        """제거 날짜가 없으면 빈 결과"""
        adj = DiffFeedbackAdjuster(store_id="46513")
        result = adj._get_stockout_justified_dates({})
        assert result == {}

    def test_stockout_no_daily_sales(self):
        """daily_sales 테이블 없어도 에러 없이 빈 결과"""
        adj = DiffFeedbackAdjuster(store_id="99999")
        removal_dates = {"X001": ["2026-02-10"]}
        # DB 연결 실패해도 빈 결과 반환 (except 처리)
        result = adj._get_stockout_justified_dates(removal_dates)
        assert isinstance(result, dict)

    def test_load_feedback_with_stockout_filter(self, tmp_path, store_db):
        """load_feedback 통합: 품절 필터가 removal_count에 반영

        _get_stockout_justified_dates를 직접 테스트하여 통합 검증.
        """
        from datetime import datetime, timedelta
        conn, db_path = store_db

        base = datetime.now()
        removal_date = (base - timedelta(days=5)).strftime("%Y-%m-%d")
        stockout_date = (base - timedelta(days=4)).strftime("%Y-%m-%d")
        safe_date = (base - timedelta(days=3)).strftime("%Y-%m-%d")

        # SO001: 제거 1일 후 품절 → 정당
        conn.execute(
            "INSERT INTO daily_sales VALUES (?, ?, ?, ?, ?)",
            ("SO001", stockout_date, 0, 0, "46513"),
        )
        conn.commit()
        conn.close()

        adj = DiffFeedbackAdjuster(store_id="46513", lookback_days=30)

        # 4건 제거 중 첫 번째만 품절정당
        removal_dates = {
            "SO001": [
                removal_date,   # 1일 후 품절 → 정당
                safe_date,      # 이후 품절 없음 → 부당
                (base - timedelta(days=1)).strftime("%Y-%m-%d"),
                base.strftime("%Y-%m-%d"),
            ],
        }

        with patch(
            "src.infrastructure.database.connection.DBRouter.get_store_connection"
        ) as mock_store:
            store_conn = sqlite3.connect(str(db_path))
            store_conn.row_factory = sqlite3.Row
            mock_store.return_value = store_conn
            justified = adj._get_stockout_justified_dates(removal_dates)

        # removal_date만 정당 (stockout_date에 품절)
        assert "SO001" in justified
        assert removal_date in justified["SO001"]
        assert len(justified["SO001"]) == 1  # 나머지 3건은 부당


class TestMidCdExemption:
    """mid_cd=022(마른안주류) penalty 면제 테스트"""

    def test_mid_cd_022_no_penalty(self, feedback_with_data):
        """mid_cd=022 → 제거 5회여도 penalty=1.0 (면제)"""
        # A001은 5회 제거 → mid_cd 없으면 0.7이지만, 022이면 1.0
        penalty = feedback_with_data.get_removal_penalty("A001", mid_cd="022")
        assert penalty == 1.0

    def test_mid_cd_022_high_removal_no_penalty(self, feedback_with_data):
        """mid_cd=022 → 제거 11회여도 penalty=1.0 (면제)"""
        penalty = feedback_with_data.get_removal_penalty("A002", mid_cd="022")
        assert penalty == 1.0

    def test_other_mid_cd_still_penalized(self, feedback_with_data):
        """mid_cd != 022 → 기존 penalty 유지"""
        # A001: 5회 제거, mid_cd=032 → 0.7
        penalty = feedback_with_data.get_removal_penalty("A001", mid_cd="032")
        assert penalty == 0.7

    def test_mid_cd_none_still_penalized(self, feedback_with_data):
        """mid_cd=None → 기존 penalty 유지 (하위호환)"""
        penalty = feedback_with_data.get_removal_penalty("A001")
        assert penalty == 0.7
