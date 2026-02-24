"""
발주 차이 추적 시스템 테스트

- OrderDiffAnalyzer: 순수 비교 로직
- OrderAnalysisRepository: DB CRUD
- OrderDiffTracker: 오케스트레이터 + 에러 격리
"""

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis.order_diff_analyzer import OrderDiffAnalyzer
from src.infrastructure.database.repos.order_analysis_repo import (
    OrderAnalysisRepository,
)


# ── 테스트 헬퍼 ─────────────────────────────────────────


def _make_snapshot_item(item_cd, final_order_qty, **kwargs):
    """스냅샷 아이템 생성 헬퍼"""
    base = {
        "item_cd": item_cd,
        "item_nm": f"상품_{item_cd}",
        "mid_cd": "001",
        "predicted_qty": kwargs.get("predicted_qty", 10),
        "recommended_qty": kwargs.get("recommended_qty", final_order_qty),
        "final_order_qty": final_order_qty,
        "current_stock": kwargs.get("current_stock", 5),
        "pending_qty": kwargs.get("pending_qty", 0),
        "eval_decision": kwargs.get("eval_decision", "NORMAL_ORDER"),
        "order_unit_qty": kwargs.get("order_unit_qty", 1),
        "order_success": kwargs.get("order_success", 1),
        "confidence": kwargs.get("confidence", "0.85"),
        "data_days": kwargs.get("data_days", 30),
    }
    base.update(kwargs)
    return base


def _make_receiving_item(item_cd, order_qty, receiving_qty=None, **kwargs):
    """입고 아이템 생성 헬퍼"""
    base = {
        "item_cd": item_cd,
        "item_nm": f"상품_{item_cd}",
        "mid_cd": "001",
        "order_date": "2026-02-17",
        "order_qty": order_qty,
        "receiving_qty": receiving_qty if receiving_qty is not None else order_qty,
        "receiving_date": kwargs.get("receiving_date", "2026-02-18"),
    }
    base.update(kwargs)
    return base


# ── OrderDiffAnalyzer 테스트 ─────────────────────────────


class TestOrderDiffAnalyzer:
    """순수 비교 로직 테스트"""

    def test_compare_identical(self):
        """동일한 데이터: diff 없음"""
        auto = [
            _make_snapshot_item("A001", 10),
            _make_snapshot_item("A002", 5),
        ]
        recv = [
            _make_receiving_item("A001", 10),
            _make_receiving_item("A002", 5),
        ]

        result = OrderDiffAnalyzer.compare(
            auto, recv, store_id="46513", order_date="2026-02-17"
        )

        assert result["unchanged_count"] == 2
        assert len(result["diffs"]) == 0
        assert result["summary"]["items_unchanged"] == 2
        assert result["summary"]["items_qty_changed"] == 0
        assert result["summary"]["items_added"] == 0
        assert result["summary"]["items_removed"] == 0
        assert result["summary"]["match_rate"] == 1.0

    def test_compare_qty_changed(self):
        """수량 변경: 사용자가 발주량 수정"""
        auto = [_make_snapshot_item("A001", 10)]
        recv = [_make_receiving_item("A001", 15)]  # 10 → 15 증량

        result = OrderDiffAnalyzer.compare(
            auto, recv, store_id="46513", order_date="2026-02-17"
        )

        assert len(result["diffs"]) == 1
        diff = result["diffs"][0]
        assert diff["diff_type"] == "qty_changed"
        assert diff["auto_order_qty"] == 10
        assert diff["confirmed_order_qty"] == 15
        assert diff["qty_diff"] == 5  # 15 - 10

    def test_compare_item_added(self):
        """사용자 추가: 자동발주에 없던 상품을 사용자가 수동 발주"""
        auto = [_make_snapshot_item("A001", 10)]
        recv = [
            _make_receiving_item("A001", 10),
            _make_receiving_item("B001", 20),  # 사용자 추가
        ]

        result = OrderDiffAnalyzer.compare(
            auto, recv, store_id="46513", order_date="2026-02-17"
        )

        assert result["unchanged_count"] == 1
        assert result["summary"]["items_added"] == 1
        added = [d for d in result["diffs"] if d["diff_type"] == "added"]
        assert len(added) == 1
        assert added[0]["item_cd"] == "B001"
        assert added[0]["auto_order_qty"] == 0
        assert added[0]["confirmed_order_qty"] == 20

    def test_compare_item_removed(self):
        """사용자 삭제: 자동발주했지만 사용자가 취소"""
        auto = [
            _make_snapshot_item("A001", 10),
            _make_snapshot_item("A002", 5),
        ]
        recv = [_make_receiving_item("A001", 10)]  # A002 취소됨

        result = OrderDiffAnalyzer.compare(
            auto, recv, store_id="46513", order_date="2026-02-17"
        )

        assert result["unchanged_count"] == 1
        assert result["summary"]["items_removed"] == 1
        removed = [d for d in result["diffs"] if d["diff_type"] == "removed"]
        assert len(removed) == 1
        assert removed[0]["item_cd"] == "A002"
        assert removed[0]["auto_order_qty"] == 5
        assert removed[0]["confirmed_order_qty"] == 0

    def test_compare_receiving_diff(self):
        """입고 차이: 발주량=확정량이지만 실제 입고량이 다름"""
        auto = [_make_snapshot_item("A001", 10)]
        recv = [_make_receiving_item("A001", 10, receiving_qty=8)]  # 10 발주, 8 입고

        result = OrderDiffAnalyzer.compare(
            auto, recv, store_id="46513", order_date="2026-02-17"
        )

        assert len(result["diffs"]) == 1
        diff = result["diffs"][0]
        assert diff["diff_type"] == "receiving_diff"
        assert diff["confirmed_order_qty"] == 10
        assert diff["receiving_qty"] == 8
        assert diff["receiving_diff"] == -2

    def test_compare_mixed(self):
        """복합: 변경 + 추가 + 삭제 + 동일"""
        auto = [
            _make_snapshot_item("A001", 10),   # 동일
            _make_snapshot_item("A002", 5),    # 수량 변경 (5→8)
            _make_snapshot_item("A003", 3),    # 삭제
        ]
        recv = [
            _make_receiving_item("A001", 10),   # 동일
            _make_receiving_item("A002", 8),    # 수량 변경
            _make_receiving_item("B001", 15),   # 추가
        ]

        result = OrderDiffAnalyzer.compare(
            auto, recv, store_id="46513", order_date="2026-02-17"
        )

        assert result["unchanged_count"] == 1
        assert result["summary"]["items_qty_changed"] == 1
        assert result["summary"]["items_added"] == 1
        assert result["summary"]["items_removed"] == 1
        assert len(result["diffs"]) == 3

    def test_compare_empty_both(self):
        """양쪽 모두 빈 리스트"""
        result = OrderDiffAnalyzer.compare([], [], store_id="46513")
        assert result["unchanged_count"] == 0
        assert len(result["diffs"]) == 0
        assert result["summary"]["match_rate"] == 0.0

    def test_compare_empty_auto(self):
        """자동발주 없음, 입고만 있음 (모두 사용자 추가)"""
        recv = [_make_receiving_item("A001", 10)]
        result = OrderDiffAnalyzer.compare([], recv, store_id="46513")
        assert result["summary"]["items_added"] == 1
        assert result["summary"]["total_auto_items"] == 0

    def test_compare_empty_receiving(self):
        """입고 없음, 자동발주만 있음 (모두 삭제)"""
        auto = [_make_snapshot_item("A001", 10)]
        result = OrderDiffAnalyzer.compare(auto, [], store_id="46513")
        assert result["summary"]["items_removed"] == 1
        assert result["summary"]["total_confirmed_items"] == 0

    def test_compare_duplicate_receiving(self):
        """동일 상품 여러 전표: 수량 합산"""
        auto = [_make_snapshot_item("A001", 20)]
        recv = [
            _make_receiving_item("A001", 10),   # 전표 1
            _make_receiving_item("A001", 10),   # 전표 2
        ]

        result = OrderDiffAnalyzer.compare(
            auto, recv, store_id="46513", order_date="2026-02-17"
        )

        # 10 + 10 = 20 → 자동발주 20과 동일
        assert result["unchanged_count"] == 1
        assert len(result["diffs"]) == 0

    def test_classify_diff(self):
        """diff_type 분류 테스트"""
        assert OrderDiffAnalyzer.classify_diff(10, 10, 10, True, True) == "unchanged"
        assert OrderDiffAnalyzer.classify_diff(10, 15, 15, True, True) == "qty_changed"
        assert OrderDiffAnalyzer.classify_diff(10, 10, 8, True, True) == "receiving_diff"
        assert OrderDiffAnalyzer.classify_diff(10, 0, 0, True, False) == "removed"
        assert OrderDiffAnalyzer.classify_diff(0, 10, 10, False, True) == "added"

    def test_direct_delivery_excluded(self):
        """1차/2차/신선 배송 상품은 비교에서 제외"""
        auto = [
            _make_snapshot_item("A001", 10, delivery_type="일반"),      # 비교 대상
            _make_snapshot_item("A002", 5, delivery_type="1차"),        # 제외
            _make_snapshot_item("A003", 3, delivery_type="2차"),        # 제외
            _make_snapshot_item("A004", 8, delivery_type="신선"),       # 제외
        ]
        recv = [
            _make_receiving_item("A001", 10),  # 동일
        ]

        result = OrderDiffAnalyzer.compare(
            auto, recv, store_id="46513", order_date="2026-02-17"
        )

        # A001만 비교 → unchanged=1, 나머지 3건은 not_comparable
        assert result["unchanged_count"] == 1
        assert result["summary"]["items_not_comparable"] == 3
        assert result["summary"]["items_removed"] == 0
        assert result["summary"]["match_rate"] == 1.0

    def test_direct_delivery_mixed_with_changes(self):
        """1차/2차 제외 + 센터매입 변경/추가/삭제 혼합"""
        auto = [
            _make_snapshot_item("A001", 10, delivery_type="일반"),      # qty_changed
            _make_snapshot_item("A002", 5, delivery_type="일반"),       # removed
            _make_snapshot_item("A003", 3, delivery_type="1차"),        # not_comparable
        ]
        recv = [
            _make_receiving_item("A001", 15),  # 10→15 증량
            _make_receiving_item("B001", 20),  # 추가
        ]

        result = OrderDiffAnalyzer.compare(
            auto, recv, store_id="46513", order_date="2026-02-17"
        )

        assert result["summary"]["items_qty_changed"] == 1   # A001
        assert result["summary"]["items_removed"] == 1        # A002
        assert result["summary"]["items_added"] == 1          # B001
        assert result["summary"]["items_not_comparable"] == 1  # A003

    def test_no_delivery_type_treated_as_ambient(self):
        """delivery_type 없는 스냅샷은 비교 대상 (기존 호환)"""
        auto = [
            _make_snapshot_item("A001", 10),  # delivery_type 미설정
            _make_snapshot_item("A002", 5),
        ]
        recv = [
            _make_receiving_item("A001", 10),
            _make_receiving_item("A002", 5),
        ]

        result = OrderDiffAnalyzer.compare(
            auto, recv, store_id="46513", order_date="2026-02-17"
        )

        assert result["unchanged_count"] == 2
        assert result["summary"]["items_not_comparable"] == 0

    def test_empty_delivery_type_treated_as_ambient(self):
        """delivery_type이 빈 문자열이면 비교 대상"""
        auto = [_make_snapshot_item("A001", 10, delivery_type="")]
        recv = [_make_receiving_item("A001", 10)]

        result = OrderDiffAnalyzer.compare(
            auto, recv, store_id="46513", order_date="2026-02-17"
        )

        assert result["unchanged_count"] == 1
        assert result["summary"]["items_not_comparable"] == 0

    def test_match_rate_calculation(self):
        """match_rate = unchanged / (auto + added)"""
        auto = [
            _make_snapshot_item("A001", 10),
            _make_snapshot_item("A002", 5),
            _make_snapshot_item("A003", 3),
            _make_snapshot_item("A004", 8),
        ]
        recv = [
            _make_receiving_item("A001", 10),   # 동일
            _make_receiving_item("A002", 10),   # 수량 변경
            _make_receiving_item("A003", 3),    # 동일
            # A004 삭제
            _make_receiving_item("B001", 7),    # 추가
        ]

        result = OrderDiffAnalyzer.compare(
            auto, recv, store_id="46513", order_date="2026-02-17"
        )

        # unchanged=2, total_items = 4(auto) + 1(added) = 5
        assert result["summary"]["match_rate"] == round(2 / 5, 4)

    def test_no_receiving_data_flagged(self):
        """입고 데이터 0건이면 has_receiving_data=0 표시"""
        auto = [
            _make_snapshot_item("A001", 10),
            _make_snapshot_item("A002", 5),
        ]
        # receiving 0건 (입고 기록 없는 날짜)
        result = OrderDiffAnalyzer.compare(
            auto, [], store_id="46513", order_date="2026-02-06"
        )
        assert result["summary"]["has_receiving_data"] == 0
        assert result["summary"]["total_confirmed_items"] == 0
        assert result["summary"]["items_removed"] == 2

    def test_has_receiving_data_flagged(self):
        """입고 데이터 있으면 has_receiving_data=1 표시"""
        auto = [_make_snapshot_item("A001", 10)]
        recv = [_make_receiving_item("A001", 10)]
        result = OrderDiffAnalyzer.compare(
            auto, recv, store_id="46513", order_date="2026-02-07"
        )
        assert result["summary"]["has_receiving_data"] == 1
        assert result["summary"]["total_confirmed_items"] == 1


# ── OrderAnalysisRepository 테스트 ───────────────────────


class TestOrderAnalysisRepository:
    """DB CRUD 테스트"""

    @pytest.fixture
    def repo(self, tmp_path):
        """임시 DB 파일 사용"""
        db_path = str(tmp_path / "test_analysis.db")
        return OrderAnalysisRepository(db_path=db_path)

    def test_save_and_get_snapshot(self, repo):
        """스냅샷 저장 및 조회"""
        items = [
            _make_snapshot_item("A001", 10),
            _make_snapshot_item("A002", 5),
        ]
        count = repo.save_order_snapshot("46513", "2026-02-17", items)
        assert count == 2

        rows = repo.get_snapshot_by_date("46513", "2026-02-17")
        assert len(rows) == 2
        assert rows[0]["item_cd"] == "A001"
        assert rows[0]["final_order_qty"] == 10

    def test_snapshot_upsert(self, repo):
        """스냅샷 UPSERT (같은 키 → 덮어쓰기)"""
        items = [_make_snapshot_item("A001", 10)]
        repo.save_order_snapshot("46513", "2026-02-17", items)

        # 수정된 값으로 다시 저장
        items2 = [_make_snapshot_item("A001", 20)]
        repo.save_order_snapshot("46513", "2026-02-17", items2)

        rows = repo.get_snapshot_by_date("46513", "2026-02-17")
        assert len(rows) == 1
        assert rows[0]["final_order_qty"] == 20

    def test_save_and_get_diffs(self, repo):
        """diff 저장 및 조회"""
        diffs = [
            {
                "store_id": "46513",
                "order_date": "2026-02-17",
                "receiving_date": "2026-02-18",
                "item_cd": "A001",
                "item_nm": "도시락",
                "mid_cd": "001",
                "diff_type": "qty_changed",
                "auto_order_qty": 10,
                "predicted_qty": 12,
                "eval_decision": "NORMAL_ORDER",
                "confirmed_order_qty": 15,
                "receiving_qty": 15,
                "qty_diff": 5,
                "receiving_diff": 0,
            },
        ]
        count = repo.save_diffs(diffs)
        assert count == 1

        rows = repo.get_diffs_by_date("46513", "2026-02-17")
        assert len(rows) == 1
        assert rows[0]["diff_type"] == "qty_changed"
        assert rows[0]["qty_diff"] == 5

    def test_save_and_get_summary(self, repo):
        """요약 저장 및 조회"""
        summary = {
            "store_id": "46513",
            "order_date": "2026-02-17",
            "receiving_date": "2026-02-18",
            "total_auto_items": 10,
            "total_confirmed_items": 12,
            "items_unchanged": 8,
            "items_qty_changed": 1,
            "items_added": 2,
            "items_removed": 1,
            "total_auto_qty": 50,
            "total_confirmed_qty": 65,
            "total_receiving_qty": 60,
            "match_rate": 0.6667,
        }
        count = repo.save_summary(summary)
        assert count == 1

        row = repo.get_summary_by_date("46513", "2026-02-17")
        assert row is not None
        assert row["items_unchanged"] == 8
        assert row["match_rate"] == pytest.approx(0.6667, rel=1e-3)

    def test_get_diffs_by_period(self, repo):
        """기간별 diff 조회"""
        for date in ["2026-02-15", "2026-02-16", "2026-02-17"]:
            repo.save_diffs([{
                "store_id": "46513",
                "order_date": date,
                "receiving_date": "2026-02-18",
                "item_cd": "A001",
                "diff_type": "qty_changed",
                "auto_order_qty": 10,
                "confirmed_order_qty": 15,
                "qty_diff": 5,
            }])

        rows = repo.get_diffs_by_period("46513", "2026-02-15", "2026-02-16")
        assert len(rows) == 2

    def test_store_isolation(self, repo):
        """매장 격리: store_id별 데이터 분리"""
        items = [_make_snapshot_item("A001", 10)]
        repo.save_order_snapshot("46513", "2026-02-17", items)
        repo.save_order_snapshot("46704", "2026-02-17", items)

        rows_513 = repo.get_snapshot_by_date("46513", "2026-02-17")
        rows_704 = repo.get_snapshot_by_date("46704", "2026-02-17")
        assert len(rows_513) == 1
        assert len(rows_704) == 1

        # 서로 다른 store_id
        rows_other = repo.get_snapshot_by_date("99999", "2026-02-17")
        assert len(rows_other) == 0

    def test_empty_operations(self, repo):
        """빈 데이터 처리"""
        assert repo.save_order_snapshot("46513", "2026-02-17", []) == 0
        assert repo.save_diffs([]) == 0
        assert repo.save_summary({}) == 0
        assert repo.get_snapshot_by_date("46513", "2026-02-17") == []
        assert repo.get_summary_by_date("46513", "2026-02-17") is None

    def test_analysis_queries(self, repo):
        """분석 쿼리 기본 동작"""
        # 데이터 없어도 에러 없이 빈 리스트 반환
        assert repo.get_most_modified_items("46513") == []
        assert repo.get_modification_trend("46513") == []
        assert repo.get_category_modification_stats("46513") == []

    def test_save_summary_has_receiving_data(self, repo):
        """has_receiving_data 필드 저장/조회"""
        # 입고 데이터 있는 날짜
        repo.save_summary({
            "store_id": "46513", "order_date": "2026-02-07",
            "receiving_date": "2026-02-08",
            "total_auto_items": 10, "total_confirmed_items": 8,
            "items_unchanged": 6, "items_qty_changed": 1,
            "items_added": 1, "items_removed": 2,
            "match_rate": 0.5, "has_receiving_data": 1,
        })
        # 입고 데이터 없는 날짜
        repo.save_summary({
            "store_id": "46513", "order_date": "2026-02-06",
            "receiving_date": "",
            "total_auto_items": 15, "total_confirmed_items": 0,
            "items_unchanged": 0, "items_removed": 15,
            "match_rate": 0.0, "has_receiving_data": 0,
        })

        # 전체 조회
        all_rows = repo.get_summaries_by_period("46513", "2026-02-06", "2026-02-07")
        assert len(all_rows) == 2

        # 유효 날짜만 조회
        valid = repo.get_valid_summaries_by_period("46513", "2026-02-06", "2026-02-07")
        assert len(valid) == 1
        assert valid[0]["order_date"] == "2026-02-07"
        assert valid[0]["has_receiving_data"] == 1

    def test_filtered_match_rate(self, repo):
        """유효 날짜만으로 match_rate 계산"""
        # 유효 날짜 2개 + 무효 날짜 1개
        for dt, matched, has_recv in [
            ("2026-02-15", 6, 1),
            ("2026-02-16", 4, 1),
            ("2026-02-17", 0, 0),  # 입고 미기록
        ]:
            repo.save_summary({
                "store_id": "46513", "order_date": dt,
                "receiving_date": "", "total_auto_items": 10,
                "total_confirmed_items": 8 if has_recv else 0,
                "items_unchanged": matched,
                "items_added": 0, "items_removed": 10 - matched,
                "match_rate": matched / 10,
                "has_receiving_data": has_recv,
            })

        result = repo.get_filtered_match_rate("46513", days=30)
        # 유효 2일, 무효 1일
        assert result["valid_dates"] == 2
        assert result["invalid_dates"] == 1
        # 평균 match_rate = (0.6 + 0.4) / 2 = 0.5
        assert result["avg_match_rate"] == pytest.approx(0.5, abs=0.01)

    def test_item_removal_stats(self, repo):
        """상품별 제거 통계 조회"""
        # A001: 3회 제거, 1회 unchanged
        for i, dt in enumerate(["removed", "removed", "removed", "unchanged"]):
            repo.save_diffs([{
                "store_id": "46513",
                "order_date": f"2026-02-{15+i:02d}",
                "receiving_date": "2026-02-18",
                "item_cd": "A001", "item_nm": "진라면",
                "mid_cd": "032", "diff_type": dt,
                "auto_order_qty": 6, "confirmed_order_qty": 0 if dt == "removed" else 6,
            }])

        stats = repo.get_item_removal_stats("46513", days=30)
        assert len(stats) >= 1
        a001 = next(s for s in stats if s["item_cd"] == "A001")
        assert a001["removal_count"] == 3
        assert a001["total_appearances"] == 4

    def test_item_addition_stats(self, repo):
        """상품별 추가 통계 조회"""
        for i in range(4):
            repo.save_diffs([{
                "store_id": "46513",
                "order_date": f"2026-02-{14+i:02d}",
                "receiving_date": "2026-02-18",
                "item_cd": "B001", "item_nm": "바나나우유",
                "mid_cd": "047", "diff_type": "added",
                "auto_order_qty": 0, "confirmed_order_qty": 5 + i,
            }])

        stats = repo.get_item_addition_stats("46513", days=30)
        assert len(stats) >= 1
        b001 = next(s for s in stats if s["item_cd"] == "B001")
        assert b001["addition_count"] == 4
        assert b001["avg_qty"] == pytest.approx(6.5, abs=0.1)  # (5+6+7+8)/4


# ── OrderDiffTracker 테스트 ──────────────────────────────


class TestOrderDiffTracker:
    """오케스트레이터 테스트"""

    @pytest.fixture
    def tracker(self, tmp_path):
        """테스트용 트래커"""
        from src.analysis.order_diff_tracker import OrderDiffTracker

        tracker = OrderDiffTracker(store_id="46513")
        # repo를 tmp DB로 교체
        tracker._repo = OrderAnalysisRepository(
            db_path=str(tmp_path / "test_analysis.db")
        )
        return tracker

    def test_save_snapshot_basic(self, tracker):
        """기본 스냅샷 저장"""
        order_list = [
            {
                "item_cd": "A001",
                "item_nm": "도시락",
                "mid_cd": "001",
                "predicted_sales": 12,
                "recommended_qty": 15,
                "final_order_qty": 15,
                "current_stock": 5,
                "pending_receiving_qty": 0,
                "order_unit_qty": 5,
                "confidence": 0.85,
                "data_days": 30,
            }
        ]
        results = [
            {"item_cd": "A001", "success": True, "actual_qty": 15}
        ]

        count = tracker.save_snapshot("2026-02-17", order_list, results)
        assert count == 1

        # 저장 확인
        rows = tracker.repo.get_snapshot_by_date("46513", "2026-02-17")
        assert len(rows) == 1
        assert rows[0]["final_order_qty"] == 15
        assert rows[0]["order_success"] == 1

    def test_save_snapshot_with_eval_results(self, tracker):
        """eval_results 포함 스냅샷"""
        order_list = [
            {"item_cd": "A001", "item_nm": "도시락", "mid_cd": "001",
             "final_order_qty": 10}
        ]
        results = [{"item_cd": "A001", "success": True, "actual_qty": 10}]

        # EvalResult mock
        mock_eval = MagicMock()
        mock_eval.decision.name = "FORCE_ORDER"
        eval_results = {"A001": mock_eval}

        count = tracker.save_snapshot("2026-02-17", order_list, results, eval_results)
        assert count == 1

        rows = tracker.repo.get_snapshot_by_date("46513", "2026-02-17")
        assert rows[0]["eval_decision"] == "FORCE_ORDER"

    def test_compare_and_save(self, tracker):
        """스냅샷 저장 후 입고 데이터 비교"""
        # 1) 스냅샷 저장 (비푸드 mid_cd → delivery_type="일반" → 비교 대상)
        order_list = [
            {"item_cd": "A001", "item_nm": "과자", "mid_cd": "015",
             "final_order_qty": 10},
            {"item_cd": "A002", "item_nm": "음료", "mid_cd": "016",
             "final_order_qty": 5},
        ]
        results = [
            {"item_cd": "A001", "success": True, "actual_qty": 10},
            {"item_cd": "A002", "success": True, "actual_qty": 5},
        ]
        tracker.save_snapshot("2026-02-17", order_list, results)

        # 2) 입고 데이터: A001 증량, A002 동일, B001 추가
        receiving_data = [
            _make_receiving_item("A001", 15, receiving_qty=15),  # 10→15
            _make_receiving_item("A002", 5, receiving_qty=5),
            _make_receiving_item("B001", 20, receiving_qty=20),  # 추가
        ]

        result = tracker.compare_and_save("2026-02-17", receiving_data)

        assert result is not None
        assert result["unchanged_count"] == 1  # A002
        assert result["summary"]["items_qty_changed"] == 1  # A001
        assert result["summary"]["items_added"] == 1  # B001

        # DB에 diff가 저장됨
        diffs = tracker.repo.get_diffs_by_date("46513", "2026-02-17")
        assert len(diffs) == 2  # qty_changed + added

    def test_compare_no_snapshot(self, tracker):
        """스냅샷 없이 비교: 모든 입고가 '추가'로 분류"""
        receiving_data = [_make_receiving_item("A001", 10)]
        result = tracker.compare_and_save("2026-02-17", receiving_data)

        assert result is not None
        assert result["summary"]["items_added"] == 1

    def test_save_snapshot_delivery_type(self, tracker):
        """스냅샷에 delivery_type이 포함됨"""
        order_list = [
            {
                "item_cd": "A001",
                "item_nm": "도)압도적두툼돈까스정식1",
                "mid_cd": "001",   # ALERT_CATEGORIES 도시락
                "final_order_qty": 10,
            },
            {
                "item_cd": "A002",
                "item_nm": "코카콜라500ML",
                "mid_cd": "ZZZ",   # 비푸드
                "final_order_qty": 5,
            },
        ]
        results = [
            {"item_cd": "A001", "success": True, "actual_qty": 10},
            {"item_cd": "A002", "success": True, "actual_qty": 5},
        ]

        count = tracker.save_snapshot("2026-02-17", order_list, results)
        assert count == 2

        rows = tracker.repo.get_snapshot_by_date("46513", "2026-02-17")
        dt_map = {r["item_cd"]: r["delivery_type"] for r in rows}

        # A001 (item_nm 끝 "1", mid_cd "001" in ALERT_CATEGORIES) → "1차"
        assert dt_map["A001"] == "1차"
        # A002 (비푸드) → "일반"
        assert dt_map["A002"] == "일반"

    def test_error_isolation_snapshot(self, tracker):
        """스냅샷 저장 실패 시 0 반환 (예외 전파 없음)"""
        # repo를 고장낸 mock으로 교체
        tracker._repo = MagicMock()
        tracker._repo.save_order_snapshot.side_effect = Exception("DB error")

        count = tracker.save_snapshot(
            "2026-02-17",
            [{"item_cd": "A001", "final_order_qty": 10}],
            [{"item_cd": "A001", "success": True}],
        )
        assert count == 0  # 에러 없이 0 반환

    def test_error_isolation_compare(self, tracker):
        """비교 실패 시 None 반환 (예외 전파 없음)"""
        tracker._repo = MagicMock()
        tracker._repo.get_snapshot_by_date.side_effect = Exception("DB error")

        result = tracker.compare_and_save("2026-02-17", [])
        assert result is None  # 에러 없이 None 반환


# ── OrderDiffBackfiller 테스트 ───────────────────────────


class TestOrderDiffBackfiller:
    """과거 데이터 백필 테스트"""

    @pytest.fixture
    def store_db(self, tmp_path):
        """테스트용 매장 DB (eval_outcomes + order_tracking + receiving_history)"""
        db_path = tmp_path / "stores" / "99999.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # eval_outcomes 테이블
        conn.execute("""
            CREATE TABLE eval_outcomes (
                id INTEGER PRIMARY KEY,
                store_id TEXT, eval_date TEXT, item_cd TEXT, mid_cd TEXT,
                decision TEXT, daily_avg REAL, current_stock INTEGER,
                pending_qty INTEGER, predicted_qty INTEGER,
                actual_order_qty INTEGER, order_status TEXT,
                exposure_days REAL, popularity_score REAL,
                actual_sold_qty INTEGER, next_day_stock INTEGER,
                was_stockout INTEGER, was_waste INTEGER, outcome TEXT,
                weekday TEXT, delivery_batch TEXT, sell_price REAL,
                margin_rate REAL, disuse_qty INTEGER, promo_type TEXT,
                trend_score REAL, stockout_freq REAL,
                UNIQUE(eval_date, item_cd)
            )
        """)

        # order_tracking 테이블
        conn.execute("""
            CREATE TABLE order_tracking (
                id INTEGER PRIMARY KEY,
                store_id TEXT, order_date TEXT, item_cd TEXT, item_nm TEXT,
                mid_cd TEXT, delivery_type TEXT, order_qty INTEGER,
                remaining_qty INTEGER, arrival_time TEXT, expiry_time TEXT,
                status TEXT, alert_sent INTEGER, created_at TEXT,
                updated_at TEXT, actual_receiving_qty INTEGER,
                actual_arrival_time TEXT, order_source TEXT,
                UNIQUE(order_date, item_cd)
            )
        """)

        # receiving_history 테이블
        conn.execute("""
            CREATE TABLE receiving_history (
                id INTEGER PRIMARY KEY,
                store_id TEXT, receiving_date TEXT, receiving_time TEXT,
                chit_no TEXT, item_cd TEXT, item_nm TEXT, mid_cd TEXT,
                order_date TEXT, order_qty INTEGER, receiving_qty INTEGER,
                delivery_type TEXT, center_nm TEXT, center_cd TEXT,
                plan_qty INTEGER
            )
        """)
        conn.execute("CREATE INDEX idx_receiving_order_date ON receiving_history(order_date)")

        # products 테이블 (eval_outcomes JOIN용)
        conn.execute("""
            CREATE TABLE products (
                item_cd TEXT PRIMARY KEY, item_nm TEXT, mid_cd TEXT
            )
        """)
        conn.execute(
            "INSERT INTO products VALUES (?, ?, ?)",
            ("A001", "도시락", "001"),
        )
        conn.execute(
            "INSERT INTO products VALUES (?, ?, ?)",
            ("A002", "음료", "002"),
        )
        conn.execute(
            "INSERT INTO products VALUES (?, ?, ?)",
            ("B001", "과자", "015"),
        )

        conn.commit()
        conn.close()
        return db_path

    @pytest.fixture
    def backfiller(self, tmp_path, store_db):
        """테스트용 백필러 (DB 경로 패치)"""
        from src.analysis.order_diff_backfill import OrderDiffBackfiller

        bf = OrderDiffBackfiller(store_id="99999")

        # analysis_repo를 tmp DB로 교체
        bf._analysis_repo = OrderAnalysisRepository(
            db_path=str(tmp_path / "test_analysis.db")
        )

        # store DB 경로 패치
        store_db_path = store_db
        original_get_store_conn = bf._get_store_connection

        def patched_get_store_conn():
            conn = sqlite3.connect(str(store_db_path))
            conn.row_factory = sqlite3.Row
            return conn

        bf._get_store_connection = patched_get_store_conn

        return bf

    def _insert_eval(self, store_db, date, item_cd, predicted, actual, decision, stock=5):
        conn = sqlite3.connect(str(store_db))
        conn.execute(
            """INSERT INTO eval_outcomes
            (store_id, eval_date, item_cd, mid_cd, decision,
             predicted_qty, actual_order_qty, order_status, current_stock, pending_qty)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("99999", date, item_cd, "001", decision,
             predicted, actual, "success", stock, 0),
        )
        conn.commit()
        conn.close()

    def _insert_tracking(self, store_db, date, item_cd, item_nm, qty,
                         source="auto", delivery_type="ambient"):
        conn = sqlite3.connect(str(store_db))
        conn.execute(
            """INSERT INTO order_tracking
            (store_id, order_date, item_cd, item_nm, mid_cd,
             delivery_type, order_qty, order_source, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("99999", date, item_cd, item_nm, "001",
             delivery_type, qty, source, "ordered"),
        )
        conn.commit()
        conn.close()

    def _insert_receiving(self, store_db, order_date, recv_date, item_cd, item_nm, order_qty, recv_qty):
        conn = sqlite3.connect(str(store_db))
        conn.execute(
            """INSERT INTO receiving_history
            (store_id, receiving_date, order_date, item_cd, item_nm,
             mid_cd, order_qty, receiving_qty)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("99999", recv_date, order_date, item_cd, item_nm,
             "001", order_qty, recv_qty),
        )
        conn.commit()
        conn.close()

    def test_backfill_date_basic(self, backfiller, store_db):
        """기본 백필: eval + tracking + receiving 데이터 있음"""
        date = "2026-02-15"

        # eval: A001 예측=12, 실발주=10
        self._insert_eval(store_db, date, "A001", 12, 10, "NORMAL_ORDER")
        # tracking: A001 발주=10
        self._insert_tracking(store_db, date, "A001", "도시락", 10)
        # receiving: A001 확정발주=15 (사용자가 10→15 증량), 입고=15
        self._insert_receiving(store_db, date, "2026-02-16", "A001", "도시락", 15, 15)

        result = backfiller.backfill_date(date)

        assert result is not None
        assert result["snapshot_count"] == 1

        # diff: 10(auto) vs 15(confirmed) → qty_changed
        assert len(result["diffs"]) == 1
        diff = result["diffs"][0]
        assert diff["diff_type"] == "qty_changed"
        assert diff["auto_order_qty"] == 10
        assert diff["confirmed_order_qty"] == 15
        assert diff["qty_diff"] == 5

    def test_backfill_date_user_added(self, backfiller, store_db):
        """사용자 추가 상품 감지"""
        date = "2026-02-15"

        # eval + tracking: A001만 자동발주
        self._insert_eval(store_db, date, "A001", 10, 10, "NORMAL_ORDER")
        self._insert_tracking(store_db, date, "A001", "도시락", 10)

        # receiving: A001(동일) + B001(사용자 추가)
        self._insert_receiving(store_db, date, "2026-02-16", "A001", "도시락", 10, 10)
        self._insert_receiving(store_db, date, "2026-02-16", "B001", "과자", 20, 20)

        result = backfiller.backfill_date(date)

        assert result is not None
        assert result["unchanged_count"] == 1  # A001
        added = [d for d in result["diffs"] if d["diff_type"] == "added"]
        assert len(added) == 1
        assert added[0]["item_cd"] == "B001"

    def test_backfill_date_no_data(self, backfiller, store_db):
        """데이터 없는 날짜 → None"""
        result = backfiller.backfill_date("2025-01-01")
        assert result is None

    def test_backfill_range(self, backfiller, store_db):
        """기간 백필"""
        for i, date in enumerate(["2026-02-14", "2026-02-15", "2026-02-16"]):
            qty = 10 + i
            self._insert_eval(store_db, date, "A001", qty, qty, "NORMAL_ORDER")
            self._insert_tracking(store_db, date, "A001", "도시락", qty)
            recv_date = f"2026-02-{15 + i}"
            self._insert_receiving(store_db, date, recv_date, "A001", "도시락", qty, qty)

        result = backfiller.backfill_range("2026-02-14", "2026-02-16")

        assert result["processed"] == 3
        assert result["skipped"] == 0
        assert result["total_snapshots"] == 3

    def test_build_snapshot_eval_priority(self, backfiller, store_db):
        """eval_outcomes의 predicted_qty + order_tracking의 order_qty 조합"""
        date = "2026-02-15"

        # eval: predicted=15, actual_order=12 (배수 조정 전)
        self._insert_eval(store_db, date, "A001", 15, 12, "FORCE_ORDER")
        # tracking: order_qty=15 (배수 조정 후 실제 입력값)
        self._insert_tracking(store_db, date, "A001", "도시락", 15)

        result = backfiller.backfill_date(date)

        # snapshot에서 order_tracking의 order_qty(15)가 final_order_qty로 사용됨
        snapshot = backfiller.analysis_repo.get_snapshot_by_date("99999", date)
        assert len(snapshot) == 1
        assert snapshot[0]["final_order_qty"] == 15  # tracking 우선
        assert snapshot[0]["predicted_qty"] == 15     # eval에서

    def test_backfill_snapshot_skip_zero_qty(self, backfiller, store_db):
        """발주량 0 상품은 스냅샷에서 제외"""
        date = "2026-02-15"

        # SKIP 평가: predicted 있지만 actual_order_qty=0
        self._insert_eval(store_db, date, "A001", 5, 0, "SKIP")
        # tracking에도 없음

        result = backfiller.backfill_date(date)
        assert result is None  # 유효 스냅샷 0건

    def test_backfill_delivery_type_from_tracking(self, backfiller, store_db):
        """백필 스냅샷에 order_tracking의 delivery_type 포함"""
        date = "2026-02-15"

        self._insert_eval(store_db, date, "A001", 10, 10, "NORMAL_ORDER")
        self._insert_tracking(store_db, date, "A001", "도시락", 10,
                              delivery_type="1차")
        self._insert_receiving(store_db, date, "2026-02-16",
                               "A001", "도시락", 10, 10)

        result = backfiller.backfill_date(date)
        assert result is not None

        snapshot = backfiller.analysis_repo.get_snapshot_by_date("99999", date)
        assert len(snapshot) == 1
        assert snapshot[0]["delivery_type"] == "1차"
