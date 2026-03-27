#!/usr/bin/env python3
"""
compare_snapshot.py 자체 검증 테스트
"""
import json
import tempfile
import os
import pytest

from tests.golden.compare_snapshot import compare_snapshots, CompareResult


def _create_temp_snapshot(data: dict) -> str:
    """임시 스냅샷 파일 생성"""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


@pytest.fixture
def sample_snapshot():
    """테스트용 스냅샷"""
    return {
        "store_id": "99999",
        "date": "2026-03-22",
        "generated_at": "2026-03-22T10:00:00",
        "generator_version": "1.0.0",
        "total_products": 3,
        "total_qty": 10,
        "products": [
            {
                "product_code": "A001",
                "product_name": "테스트상품A",
                "category_mid": "001",
                "category_large": "",
                "final_qty": 3,
                "order_unit": 1,
                "decided_by": "ML",
                "stages": {
                    "after_rule": 3,
                    "after_rop": 3,
                    "after_promo": 3,
                    "after_ml": 3,
                    "after_diff": 3,
                    "after_promo_floor": 3,
                    "after_sub": 3,
                    "after_cap": 3,
                    "after_round": 3,
                    "final": 3,
                    "before_floor": 3,
                    "after_floor": 3,
                    "after_food_cap": 3,
                    "after_manual_deduct": 3,
                },
            },
            {
                "product_code": "B002",
                "product_name": "테스트상품B",
                "category_mid": "049",
                "category_large": "",
                "final_qty": 5,
                "order_unit": 1,
                "decided_by": "rule",
                "stages": {
                    "after_rule": 5,
                    "after_rop": 5,
                    "after_promo": 5,
                    "after_ml": 5,
                    "after_diff": 5,
                    "after_promo_floor": 5,
                    "after_sub": 5,
                    "after_cap": 5,
                    "after_round": 5,
                    "final": 5,
                    "before_floor": 5,
                    "after_floor": 5,
                    "after_food_cap": 5,
                    "after_manual_deduct": 5,
                },
            },
            {
                "product_code": "C003",
                "product_name": "테스트상품C",
                "category_mid": "072",
                "category_large": "",
                "final_qty": 2,
                "order_unit": 1,
                "decided_by": "rule",
                "stages": {
                    "after_rule": 2,
                    "after_rop": 2,
                    "after_promo": 2,
                    "after_ml": 2,
                    "after_diff": 2,
                    "after_promo_floor": 2,
                    "after_sub": 2,
                    "after_cap": 2,
                    "after_round": 2,
                    "final": 2,
                    "before_floor": 2,
                    "after_floor": 2,
                    "after_food_cap": 2,
                    "after_manual_deduct": 2,
                },
            },
        ],
    }


class TestCompareSnapshotsIdentical:
    """동일 스냅샷 비교 → PASS"""

    def test_same_file_gives_pass(self, sample_snapshot):
        path = _create_temp_snapshot(sample_snapshot)
        try:
            result = compare_snapshots(path, path)
            assert result.is_pass
            assert result.matched == 3
            assert result.match_rate == 100.0
            assert len(result.diffs) == 0
        finally:
            os.unlink(path)

    def test_same_content_different_files(self, sample_snapshot):
        path1 = _create_temp_snapshot(sample_snapshot)
        path2 = _create_temp_snapshot(sample_snapshot)
        try:
            result = compare_snapshots(path1, path2)
            assert result.is_pass
        finally:
            os.unlink(path1)
            os.unlink(path2)


class TestCompareSnapshotsDiff:
    """다른 스냅샷 비교 → DIFF"""

    def test_qty_difference_detected(self, sample_snapshot):
        import copy
        after_data = copy.deepcopy(sample_snapshot)
        after_data["products"][0]["final_qty"] = 5  # 3→5
        after_data["products"][0]["stages"]["final"] = 5
        after_data["products"][0]["stages"]["after_manual_deduct"] = 5

        path1 = _create_temp_snapshot(sample_snapshot)
        path2 = _create_temp_snapshot(after_data)
        try:
            result = compare_snapshots(path1, path2)
            assert not result.is_pass
            assert result.matched == 2
            assert len(result.diffs) == 1
            assert result.diffs[0].product_code == "A001"
            assert result.diffs[0].before_qty == 3
            assert result.diffs[0].after_qty == 5
        finally:
            os.unlink(path1)
            os.unlink(path2)

    def test_missing_product_detected(self, sample_snapshot):
        import copy
        after_data = copy.deepcopy(sample_snapshot)
        after_data["products"] = after_data["products"][:2]  # C003 제거

        path1 = _create_temp_snapshot(sample_snapshot)
        path2 = _create_temp_snapshot(after_data)
        try:
            result = compare_snapshots(path1, path2)
            assert not result.is_pass
            assert len(result.only_in_before) == 1
            assert "C003" in result.only_in_before
        finally:
            os.unlink(path1)
            os.unlink(path2)

    def test_added_product_detected(self, sample_snapshot):
        import copy
        after_data = copy.deepcopy(sample_snapshot)
        after_data["products"].append({
            "product_code": "D004",
            "product_name": "추가상품",
            "category_mid": "010",
            "final_qty": 1,
            "stages": {},
        })

        path1 = _create_temp_snapshot(sample_snapshot)
        path2 = _create_temp_snapshot(after_data)
        try:
            result = compare_snapshots(path1, path2)
            assert not result.is_pass
            assert len(result.only_in_after) == 1
            assert "D004" in result.only_in_after
        finally:
            os.unlink(path1)
            os.unlink(path2)


class TestCompareSnapshotsEdgeCases:
    """엣지 케이스"""

    def test_empty_snapshots(self):
        empty = {"products": []}
        path1 = _create_temp_snapshot(empty)
        path2 = _create_temp_snapshot(empty)
        try:
            result = compare_snapshots(path1, path2)
            assert result.is_pass
            assert result.match_rate == 100.0
        finally:
            os.unlink(path1)
            os.unlink(path2)

    def test_match_rate_calculation(self, sample_snapshot):
        import copy
        after_data = copy.deepcopy(sample_snapshot)
        # 1/3 변경
        after_data["products"][0]["final_qty"] = 99
        path1 = _create_temp_snapshot(sample_snapshot)
        path2 = _create_temp_snapshot(after_data)
        try:
            result = compare_snapshots(path1, path2)
            assert result.matched == 2
            assert abs(result.match_rate - 66.7) < 1.0
        finally:
            os.unlink(path1)
            os.unlink(path2)
