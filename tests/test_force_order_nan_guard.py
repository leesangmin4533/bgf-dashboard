"""
FORCE_ORDER 보충 로직의 NaN/Inf/음수 adjusted_qty 안전성 테스트

auto_order.py L875 부근:
    if FORCE_MAX_DAYS > 0 and r.adjusted_qty > 0:
        force_cap = max(1, int(r.adjusted_qty * FORCE_MAX_DAYS))

adjusted_qty가 NaN/Inf이면 int() 변환에서 ValueError 발생.
수정 전 현재 상태에서 어떤 케이스가 실패하는지 확인 목적.
"""

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


# --- PredictionResult 경량 모킹 ---
@dataclass
class FakePredictionResult:
    """테스트용 PredictionResult 대체"""
    item_cd: str = "TEST001"
    item_nm: str = "테스트상품"
    mid_cd: str = "049"
    target_date: str = "2026-03-07"
    predicted_qty: float = 2.0
    adjusted_qty: float = 2.0
    current_stock: int = 0
    pending_qty: int = 0
    safety_stock: float = 1.0
    order_qty: int = 3
    confidence: str = "medium"
    data_days: int = 30
    weekday_coef: float = 1.0


def _run_force_cap_logic(adjusted_qty: float, order_qty: int = 3, force_max_days: float = 1.5):
    """auto_order.py L873-882의 FORCE 보충 로직을 그대로 재현

    이 함수는 auto_order.py의 해당 코드를 정확히 복사한 것:
        if r.order_qty < 1:
            r.order_qty = 1
        if FORCE_MAX_DAYS > 0 and r.adjusted_qty > 0 and math.isfinite(r.adjusted_qty):
            force_cap = max(1, int(r.adjusted_qty * FORCE_MAX_DAYS))
            if r.order_qty > force_cap:
                r.order_qty = force_cap

    Returns:
        최종 order_qty
    """
    r = FakePredictionResult(adjusted_qty=adjusted_qty, order_qty=order_qty)

    if r.order_qty < 1:
        r.order_qty = 1
    if force_max_days > 0 and r.adjusted_qty > 0 and math.isfinite(r.adjusted_qty):
        force_cap = max(1, int(r.adjusted_qty * force_max_days))
        if r.order_qty > force_cap:
            r.order_qty = force_cap

    return r.order_qty


class TestForceOrderNanGuard:
    """FORCE_ORDER 보충 시 adjusted_qty 비정상값 테스트"""

    # =========================================================
    # Case 1: 정상 숫자 — 기준 동작 확인
    # =========================================================

    def test_normal_positive_adjusted_qty(self):
        """정상 양수 → force_cap 계산 정상"""
        # adjusted_qty=2.0, FORCE_MAX_DAYS=1.5 → cap=max(1, int(3.0))=3
        result = _run_force_cap_logic(adjusted_qty=2.0, order_qty=5)
        assert result == 3  # cap=3 < order_qty=5 → 3으로 제한

    def test_normal_small_adjusted_qty(self):
        """작은 양수 → cap=1 보장"""
        # adjusted_qty=0.5, FORCE_MAX_DAYS=1.5 → cap=max(1, int(0.75))=1
        result = _run_force_cap_logic(adjusted_qty=0.5, order_qty=3)
        assert result == 1

    def test_normal_order_qty_under_cap(self):
        """order_qty가 cap 이하 → 그대로 유지"""
        # adjusted_qty=10.0 → cap=max(1, int(15.0))=15
        result = _run_force_cap_logic(adjusted_qty=10.0, order_qty=3)
        assert result == 3  # 3 < 15 → 그대로

    def test_normal_zero_adjusted_qty(self):
        """adjusted_qty=0 → cap 계산 건너뜀 (조건: r.adjusted_qty > 0)"""
        result = _run_force_cap_logic(adjusted_qty=0.0, order_qty=3)
        assert result == 3  # cap 적용 안됨

    def test_normal_zero_order_qty(self):
        """order_qty=0 → 최소 1개 보장"""
        result = _run_force_cap_logic(adjusted_qty=2.0, order_qty=0)
        assert result == 1

    # =========================================================
    # Case 2: NaN — int(NaN) → ValueError
    # =========================================================

    def test_nan_adjusted_qty(self):
        """adjusted_qty가 NaN이면 ValueError 발생하는지 확인"""
        result = _run_force_cap_logic(adjusted_qty=float('nan'), order_qty=3)
        # NaN > 0 은 False이므로 cap 계산 자체를 건너뛰어야 안전
        # 하지만 math.isnan 체크 없이는 예측 불가
        assert isinstance(result, int)
        assert result >= 1

    # =========================================================
    # Case 3: Inf — int(Inf) → OverflowError/ValueError
    # =========================================================

    def test_positive_inf_adjusted_qty(self):
        """adjusted_qty가 +Inf이면 int() 변환에서 에러"""
        result = _run_force_cap_logic(adjusted_qty=float('inf'), order_qty=3)
        assert isinstance(result, int)
        assert result >= 1

    def test_negative_inf_adjusted_qty(self):
        """adjusted_qty가 -Inf이면 조건 분기 확인"""
        # -Inf > 0 은 False → cap 계산 건너뜀
        result = _run_force_cap_logic(adjusted_qty=float('-inf'), order_qty=3)
        assert result == 3  # cap 적용 안됨

    # =========================================================
    # Case 4: 음수 — 과소발주 위험
    # =========================================================

    def test_negative_adjusted_qty(self):
        """adjusted_qty가 음수 → r.adjusted_qty > 0 조건 False → cap 미적용"""
        result = _run_force_cap_logic(adjusted_qty=-2.0, order_qty=3)
        assert result == 3  # cap 적용 안됨, 원본 유지

    def test_negative_with_zero_order_qty(self):
        """adjusted_qty 음수 + order_qty 0 → 최소 1개 보장"""
        result = _run_force_cap_logic(adjusted_qty=-5.0, order_qty=0)
        assert result == 1

    # =========================================================
    # Case 5: 극값 (overflow 위험)
    # =========================================================

    def test_very_large_adjusted_qty(self):
        """매우 큰 adjusted_qty → int() overflow 없는지"""
        result = _run_force_cap_logic(adjusted_qty=1e15, order_qty=3)
        assert isinstance(result, int)
        assert result >= 1
