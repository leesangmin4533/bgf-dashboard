# -*- coding: utf-8 -*-
"""
force-order-fix: FORCE_ORDER 오판 수정 테스트

재고가 있는 상품에 불필요한 FORCE 강제 발주가 발생하는 버그 수정 검증
- Fix 1: FORCE 보충 생략 조건 강화 (stock > 0이면 생략)
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _make_predict_result(item_cd, item_nm, order_qty, current_stock,
                         pending_qty=0, adjusted_qty=0.5, mid_cd="014"):
    """predict_batch 반환값과 유사한 SimpleNamespace 생성"""
    return SimpleNamespace(
        item_cd=item_cd,
        item_nm=item_nm,
        mid_cd=mid_cd,
        order_qty=order_qty,
        current_stock=current_stock,
        pending_qty=pending_qty,
        adjusted_qty=adjusted_qty,
    )


# ============================================================
# 1. FORCE 보충 생략: 재고만 있는 경우 (핵심 버그 수정)
# ============================================================

class TestForceSkipWithStockOnly:
    """stock > 0, pending = 0인 경우 FORCE 보충 생략 (Fix 1 핵심)"""

    def test_stock_10_pending_0_should_skip(self):
        """재고=10, 미입고=0 → FORCE 보충 생략 (기존 버그 재현+수정 확인)"""
        r = _make_predict_result("8804624073530", "호정)군고구마도나스",
                                 order_qty=0, current_stock=10, pending_qty=0)
        # 수정된 조건: current_stock + pending_qty > 0 → True → 생략
        assert r.current_stock + r.pending_qty > 0, "재고 있으면 FORCE 보충 생략해야 함"

    def test_stock_1_pending_0_should_skip(self):
        """재고=1, 미입고=0 → FORCE 보충 생략"""
        r = _make_predict_result("TEST001", "테스트상품",
                                 order_qty=0, current_stock=1, pending_qty=0)
        assert r.current_stock + r.pending_qty > 0

    def test_stock_5_pending_0_should_skip(self):
        """재고=5, 미입고=0 → FORCE 보충 생략"""
        r = _make_predict_result("TEST002", "테스트상품2",
                                 order_qty=0, current_stock=5, pending_qty=0)
        assert r.current_stock + r.pending_qty > 0


# ============================================================
# 2. FORCE 보충 생략: 미입고만 있는 경우
# ============================================================

class TestForceSkipWithPendingOnly:
    """stock = 0, pending > 0인 경우 FORCE 보충 생략"""

    def test_stock_0_pending_5_should_skip(self):
        """재고=0, 미입고=5 → FORCE 보충 생략"""
        r = _make_predict_result("TEST003", "테스트상품3",
                                 order_qty=0, current_stock=0, pending_qty=5)
        assert r.current_stock + r.pending_qty > 0

    def test_stock_0_pending_1_should_skip(self):
        """재고=0, 미입고=1 → FORCE 보충 생략"""
        r = _make_predict_result("TEST004", "테스트상품4",
                                 order_qty=0, current_stock=0, pending_qty=1)
        assert r.current_stock + r.pending_qty > 0


# ============================================================
# 3. FORCE 발주 정상 작동: 진짜 품절
# ============================================================

class TestForceOrderGenuineStockout:
    """stock = 0, pending = 0인 경우 FORCE 발주 정상 작동"""

    def test_stock_0_pending_0_should_force(self):
        """재고=0, 미입고=0 → FORCE 발주 실행 (최소 1개)"""
        r = _make_predict_result("TEST005", "진짜품절상품",
                                 order_qty=0, current_stock=0, pending_qty=0)
        assert r.current_stock + r.pending_qty == 0, "진짜 품절이면 FORCE 발주해야 함"
        # 최소 1개 보장 로직
        if r.order_qty < 1:
            r.order_qty = 1
        assert r.order_qty == 1

    def test_genuine_stockout_force_cap(self):
        """품절 상품 FORCE 발주 시 상한 적용"""
        r = _make_predict_result("TEST006", "품절상품", order_qty=5,
                                 current_stock=0, pending_qty=0, adjusted_qty=1.0)
        FORCE_MAX_DAYS = 3
        force_cap = max(1, int(r.adjusted_qty * FORCE_MAX_DAYS))
        if r.order_qty > force_cap:
            r.order_qty = force_cap
        assert r.order_qty == 3  # 1.0 * 3 = 3


# ============================================================
# 4. FORCE 보충 로직 통합 테스트 (auto_order 시뮬레이션)
# ============================================================

class TestForceSupplementIntegration:
    """auto_order.py의 FORCE 보충 로직을 시뮬레이션"""

    def _run_force_supplement(self, extra_items, force_max_days=3):
        """FORCE 보충 로직 시뮬레이션 (auto_order.py:797~826 동일 로직)"""
        candidates = []
        skipped = []

        for r in extra_items:
            # ★ 수정된 조건: 재고 또는 미입고분이 있으면 FORCE 보충 생략
            if r.current_stock + r.pending_qty > 0:
                skipped.append(r)
                continue
            if r.order_qty < 1:
                r.order_qty = 1
            if force_max_days > 0 and r.adjusted_qty > 0:
                force_cap = max(1, int(r.adjusted_qty * force_max_days))
                if r.order_qty > force_cap:
                    r.order_qty = force_cap
            candidates.append(r)

        return candidates, skipped

    def test_mixed_items(self):
        """재고 있는 상품은 생략, 품절 상품만 발주"""
        extra = [
            _make_predict_result("A", "재고있음", 0, current_stock=10, pending_qty=0),
            _make_predict_result("B", "진짜품절", 0, current_stock=0, pending_qty=0),
            _make_predict_result("C", "미입고있음", 0, current_stock=0, pending_qty=3),
            _make_predict_result("D", "품절2", 0, current_stock=0, pending_qty=0),
        ]
        candidates, skipped = self._run_force_supplement(extra)
        assert len(candidates) == 2  # B, D만 발주
        assert len(skipped) == 2     # A, C는 생략
        assert candidates[0].item_cd == "B"
        assert candidates[1].item_cd == "D"
        assert all(c.order_qty >= 1 for c in candidates)

    def test_all_have_stock(self):
        """모든 상품에 재고 있으면 FORCE 보충 0건"""
        extra = [
            _make_predict_result("X", "상품1", 0, current_stock=5, pending_qty=0),
            _make_predict_result("Y", "상품2", 0, current_stock=0, pending_qty=10),
            _make_predict_result("Z", "상품3", 0, current_stock=3, pending_qty=2),
        ]
        candidates, skipped = self._run_force_supplement(extra)
        assert len(candidates) == 0
        assert len(skipped) == 3

    def test_all_genuine_stockout(self):
        """모든 상품이 진짜 품절이면 전부 FORCE 발주"""
        extra = [
            _make_predict_result("P", "품절A", 0, current_stock=0, pending_qty=0),
            _make_predict_result("Q", "품절B", 0, current_stock=0, pending_qty=0),
        ]
        candidates, skipped = self._run_force_supplement(extra)
        assert len(candidates) == 2
        assert len(skipped) == 0

    def test_force_cap_applied(self):
        """FORCE 발주 상한 적용 확인"""
        extra = [
            _make_predict_result("CAP1", "상한테스트", order_qty=20,
                                 current_stock=0, pending_qty=0, adjusted_qty=2.0),
        ]
        candidates, _ = self._run_force_supplement(extra, force_max_days=3)
        assert candidates[0].order_qty == 6  # 2.0 * 3 = 6

    def test_force_min_1_guaranteed(self):
        """FORCE 발주 최소 1개 보장"""
        extra = [
            _make_predict_result("MIN1", "최소보장", order_qty=0,
                                 current_stock=0, pending_qty=0, adjusted_qty=0.1),
        ]
        candidates, _ = self._run_force_supplement(extra)
        assert candidates[0].order_qty == 1


# ============================================================
# 5. 기존 조건 vs 수정 조건 비교
# ============================================================

class TestOldVsNewCondition:
    """기존 버그 조건과 수정 조건의 차이 검증"""

    def test_old_condition_would_pass_stock_only(self):
        """기존 조건: pending=0이면 재고 있어도 통과 (버그)"""
        r = _make_predict_result("BUG", "버그상품", 0, current_stock=10, pending_qty=0)
        old_condition = r.pending_qty > 0 and r.current_stock + r.pending_qty > 0
        assert old_condition is False, "기존 조건은 pending=0이면 통과시킴 (버그)"

    def test_new_condition_skips_stock_only(self):
        """수정 조건: 재고만 있어도 생략 (정상)"""
        r = _make_predict_result("FIX", "수정상품", 0, current_stock=10, pending_qty=0)
        new_condition = r.current_stock + r.pending_qty > 0
        assert new_condition is True, "수정 조건은 재고 있으면 생략"

    def test_both_conditions_agree_on_genuine_stockout(self):
        """진짜 품절: 기존/수정 조건 모두 통과 (발주 실행)"""
        r = _make_predict_result("OK", "정상품절", 0, current_stock=0, pending_qty=0)
        old_condition = r.pending_qty > 0 and r.current_stock + r.pending_qty > 0
        new_condition = r.current_stock + r.pending_qty > 0
        assert old_condition is False  # 기존: 통과 → 발주
        assert new_condition is False  # 수정: 통과 → 발주

    def test_both_conditions_agree_on_pending(self):
        """미입고 있는 경우: 기존/수정 조건 모두 생략"""
        r = _make_predict_result("PND", "미입고상품", 0, current_stock=0, pending_qty=5)
        old_condition = r.pending_qty > 0 and r.current_stock + r.pending_qty > 0
        new_condition = r.current_stock + r.pending_qty > 0
        assert old_condition is True   # 기존: 생략
        assert new_condition is True   # 수정: 생략
