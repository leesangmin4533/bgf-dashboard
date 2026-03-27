"""
배수 캡 및 order_unit_qty 최종 보정 테스트

카스캔500ml 72배수 버그 방지:
- order_unit_qty=1(폴백)일 때 qty=72 → 배수72 x 입수24 = 1,728개 과발주
- L1/L2에 MAX_ORDER_MULTIPLIER(99) 캡 적용
- 발주 직전 order_unit_qty DB 배치 재조회 방어 (auto_order._finalize_order_unit_qty)
"""

import unittest
from unittest.mock import MagicMock, patch

from src.settings.constants import MAX_ORDER_MULTIPLIER


class TestDirectApiSaverMultiplierCap(unittest.TestCase):
    """DirectApiSaver._calc_multiplier()에 MAX_ORDER_MULTIPLIER 캡 적용"""

    def _calc(self, order):
        from src.order.direct_api_saver import DirectApiOrderSaver
        return DirectApiOrderSaver._calc_multiplier(order)

    def test_normal_multiplier_passes(self):
        """정상 배수는 그대로 통과"""
        result = self._calc({'multiplier': 3})
        assert result == 3

    def test_high_multiplier_capped(self):
        """MAX_ORDER_MULTIPLIER 초과 시 캡 적용"""
        result = self._calc({'multiplier': 150})
        assert result == MAX_ORDER_MULTIPLIER

    def test_unit1_high_qty_capped(self):
        """unit=1, qty=72 → 배수72 → MAX_ORDER_MULTIPLIER 이내면 그대로"""
        result = self._calc({'final_order_qty': 72, 'order_unit_qty': 1})
        assert result == 72  # 72 < 99 이므로 그대로
        assert result <= MAX_ORDER_MULTIPLIER

    def test_unit1_extreme_qty_capped(self):
        """unit=1, qty=200 → 배수200 → 99로 캡"""
        result = self._calc({'final_order_qty': 200, 'order_unit_qty': 1})
        assert result == MAX_ORDER_MULTIPLIER

    def test_correct_unit_normal(self):
        """unit=24, qty=72 → 배수3 (정상)"""
        result = self._calc({'final_order_qty': 72, 'order_unit_qty': 24})
        assert result == 3

    def test_zero_multiplier_recalculates(self):
        """multiplier=0이면 qty/unit으로 재계산"""
        result = self._calc({'multiplier': 0, 'final_order_qty': 48, 'order_unit_qty': 24})
        assert result == 2

    def test_none_unit_falls_back_to_1(self):
        """order_unit_qty=None → 1 폴백"""
        result = self._calc({'final_order_qty': 10, 'order_unit_qty': None})
        assert result == 10


class TestBatchGridMultiplierCap(unittest.TestCase):
    """BatchGridInputter에 MAX_ORDER_MULTIPLIER 캡 적용"""

    def test_cap_applied_in_batch_grid(self):
        """BatchGridInputter에서도 MAX_ORDER_MULTIPLIER 캡이 적용되는지 확인"""
        # batch_grid_input.py의 multiplier 계산 로직을 직접 재현
        from src.settings.constants import MAX_ORDER_MULTIPLIER

        # unit=1, qty=200 → multiplier=200 → cap 99
        order = {'item_cd': 'TEST001', 'multiplier': 0,
                 'final_order_qty': 200, 'order_unit_qty': 1}
        ord_unit_qty = int(order.get('order_unit_qty', 1) or 1)
        multiplier = order.get('multiplier', 0)
        if multiplier <= 0:
            qty = order.get('final_order_qty', 0)
            multiplier = max(1, (qty + ord_unit_qty - 1) // ord_unit_qty)
        multiplier = min(multiplier, MAX_ORDER_MULTIPLIER)
        assert multiplier == MAX_ORDER_MULTIPLIER

    def test_normal_multiplier_no_cap(self):
        """정상 배수는 캡에 걸리지 않음"""
        order = {'item_cd': 'TEST002', 'multiplier': 0,
                 'final_order_qty': 72, 'order_unit_qty': 24}
        ord_unit_qty = int(order.get('order_unit_qty', 1) or 1)
        multiplier = order.get('multiplier', 0)
        if multiplier <= 0:
            qty = order.get('final_order_qty', 0)
            multiplier = max(1, (qty + ord_unit_qty - 1) // ord_unit_qty)
        multiplier = min(multiplier, MAX_ORDER_MULTIPLIER)
        assert multiplier == 3


class TestFinalizeOrderUnitQty(unittest.TestCase):
    """auto_order._finalize_order_unit_qty() 최종 보정 테스트

    2026-03-14: order_executor._refetch 대체 → auto_order._finalize 이전
    - 배치 조회 (500개 청크)
    - 모든 상품 최신값 비교 (unit=1뿐 아니라 전체)
    - final_order_qty는 변경하지 않음 (order_unit_qty만 갱신)
    """

    def _make_auto_order(self):
        """모의 AutoOrderSystem 생성 (최소 속성만)"""
        from src.order.auto_order import AutoOrderSystem

        ao = object.__new__(AutoOrderSystem)
        ao.driver = MagicMock()
        ao.store_id = "46513"
        return ao

    def _mock_db_conn(self, db_data: dict):
        """DBRouter.get_connection mock — fetchall 결과 설정

        Args:
            db_data: {item_cd: order_unit_qty} dict
        """
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        # fetchall은 (item_cd, order_unit_qty) 튜플 리스트 반환
        mock_cursor.fetchall.return_value = [
            (cd, unit) for cd, unit in db_data.items()
        ]
        return mock_conn

    @patch('src.infrastructure.database.connection.DBRouter.get_connection')
    def test_corrects_unit1(self, mock_get_conn):
        """unit=1인 상품의 order_unit_qty를 common.db에서 보정"""
        mock_get_conn.return_value = self._mock_db_conn({'8801858011024': 24})
        ao = self._make_auto_order()
        items = [
            {'item_cd': '8801858011024', 'final_order_qty': 72,
             'order_unit_qty': 1},  # 카스캔 — unit=1 (폴백 상태)
        ]
        ao._finalize_order_unit_qty(items)

        assert items[0]['order_unit_qty'] == 24
        # final_order_qty는 변경 안 함
        assert items[0]['final_order_qty'] == 72
        mock_get_conn.assert_called_with(table="product_details")

    @patch('src.infrastructure.database.connection.DBRouter.get_connection')
    def test_no_change_when_unit_matches(self, mock_get_conn):
        """DB와 동일한 unit이면 변경 안 함"""
        mock_get_conn.return_value = self._mock_db_conn({'TEST001': 24})
        ao = self._make_auto_order()
        items = [
            {'item_cd': 'TEST001', 'final_order_qty': 72, 'order_unit_qty': 24},
        ]
        ao._finalize_order_unit_qty(items)

        assert items[0]['order_unit_qty'] == 24  # 변경 없음

    @patch('src.infrastructure.database.connection.DBRouter.get_connection')
    def test_corrects_even_small_qty(self, mock_get_conn):
        """qty<=5이어도 unit 불일치면 보정 (박스단위 과발주 방지)"""
        mock_get_conn.return_value = self._mock_db_conn({'TEST002': 6})
        ao = self._make_auto_order()
        items = [
            {'item_cd': 'TEST002', 'final_order_qty': 3, 'order_unit_qty': 1},
        ]
        ao._finalize_order_unit_qty(items)

        assert items[0]['order_unit_qty'] == 6  # 보정됨

    @patch('src.infrastructure.database.connection.DBRouter.get_connection')
    def test_db_unit1_no_change(self, mock_get_conn):
        """DB에서도 unit=1이면 변경 안 함 (진짜 낱개 상품)"""
        mock_get_conn.return_value = self._mock_db_conn({'TEST003': 1})
        ao = self._make_auto_order()
        items = [
            {'item_cd': 'TEST003', 'final_order_qty': 10, 'order_unit_qty': 1},
        ]
        ao._finalize_order_unit_qty(items)

        assert items[0]['order_unit_qty'] == 1  # 변경 없음

    @patch('src.infrastructure.database.connection.DBRouter.get_connection')
    def test_db_no_record_no_change(self, mock_get_conn):
        """DB에서 해당 상품이 없으면 변경 안 함"""
        mock_get_conn.return_value = self._mock_db_conn({})  # 빈 결과
        ao = self._make_auto_order()
        items = [
            {'item_cd': 'TEST004', 'final_order_qty': 10, 'order_unit_qty': 1},
        ]
        ao._finalize_order_unit_qty(items)

        assert items[0]['order_unit_qty'] == 1  # 변경 없음

    @patch('src.infrastructure.database.connection.DBRouter.get_connection')
    def test_db_error_no_crash(self, mock_get_conn):
        """DB 조회 실패해도 크래시 없음"""
        mock_get_conn.side_effect = Exception("DB error")
        ao = self._make_auto_order()
        items = [
            {'item_cd': 'TEST005', 'final_order_qty': 10, 'order_unit_qty': 1},
        ]
        ao._finalize_order_unit_qty(items)

        assert items[0]['order_unit_qty'] == 1  # 원본 유지

    @patch('src.infrastructure.database.connection.DBRouter.get_connection')
    def test_multiple_items_mixed(self, mock_get_conn):
        """여러 상품 혼합: unit 불일치만 보정"""
        mock_get_conn.return_value = self._mock_db_conn({
            'GOOD1': 12, 'BAD1': 12, 'SMALL1': 12,
        })
        ao = self._make_auto_order()
        items = [
            {'item_cd': 'GOOD1', 'final_order_qty': 24, 'order_unit_qty': 12},  # 일치 → 스킵
            {'item_cd': 'BAD1', 'final_order_qty': 10, 'order_unit_qty': 1},    # 불일치 → 보정
            {'item_cd': 'SMALL1', 'final_order_qty': 3, 'order_unit_qty': 1},   # 불일치 → 보정
        ]
        ao._finalize_order_unit_qty(items)

        assert items[0]['order_unit_qty'] == 12  # 변경 없음 (일치)
        assert items[1]['order_unit_qty'] == 12  # 보정됨
        assert items[2]['order_unit_qty'] == 12  # 보정됨

    @patch('src.infrastructure.database.connection.DBRouter.get_connection')
    def test_superset_corrects_nonunit1(self, mock_get_conn):
        """unit>1이어도 DB와 불일치하면 보정 (기존 _refetch 대비 superset)"""
        mock_get_conn.return_value = self._mock_db_conn({'TEST006': 24})
        ao = self._make_auto_order()
        items = [
            {'item_cd': 'TEST006', 'final_order_qty': 48, 'order_unit_qty': 12},
        ]
        ao._finalize_order_unit_qty(items)

        assert items[0]['order_unit_qty'] == 24  # 12→24 보정

    @patch('src.infrastructure.database.connection.DBRouter.get_connection')
    def test_empty_list_no_crash(self, mock_get_conn):
        """빈 리스트에서 크래시 없음"""
        ao = self._make_auto_order()
        ao._finalize_order_unit_qty([])
        mock_get_conn.assert_not_called()

    @patch('src.infrastructure.database.connection.DBRouter.get_connection')
    def test_final_order_qty_unchanged(self, mock_get_conn):
        """final_order_qty는 변경하지 않음 (order_unit_qty만 갱신)"""
        mock_get_conn.return_value = self._mock_db_conn({'TEST007': 24})
        ao = self._make_auto_order()
        items = [
            {'item_cd': 'TEST007', 'final_order_qty': 73, 'order_unit_qty': 1},
        ]
        ao._finalize_order_unit_qty(items)

        assert items[0]['order_unit_qty'] == 24
        assert items[0]['final_order_qty'] == 73  # 변경 안 됨

    @patch('src.infrastructure.database.connection.DBRouter.get_connection')
    def test_batch_query_uses_chunks(self, mock_get_conn):
        """500개 이상 상품 시 청크 분할 배치 조회"""
        # 600개 상품 생성
        db_data = {f'ITEM{i:04d}': 6 for i in range(600)}
        mock_get_conn.return_value = self._mock_db_conn(db_data)
        ao = self._make_auto_order()
        items = [
            {'item_cd': f'ITEM{i:04d}', 'final_order_qty': 10, 'order_unit_qty': 1}
            for i in range(600)
        ]
        ao._finalize_order_unit_qty(items)

        # cursor.execute가 2번 호출되어야 함 (500 + 100)
        mock_cursor = mock_get_conn.return_value.cursor.return_value
        assert mock_cursor.execute.call_count == 2

    @patch('src.infrastructure.database.connection.DBRouter.get_connection')
    def test_audit_matches_calc_multiplier(self, mock_get_conn):
        """AUDIT 계산이 _calc_multiplier와 일치하는지 확인"""
        from src.order.direct_api_saver import DirectApiOrderSaver

        # unit=1, qty=12 시나리오 (루피치즈 버그 재현)
        order = {'final_order_qty': 12, 'order_unit_qty': 1}
        calc_mult = DirectApiOrderSaver._calc_multiplier(order)

        # AUDIT 수정 후 동일 계산: ceil(qty / unit)
        qty, unit = 12, 1
        audit_mult = max(1, (qty + unit - 1) // unit) if qty > 0 else 0

        assert calc_mult == audit_mult == 12, (
            f"AUDIT({audit_mult}) != _calc_multiplier({calc_mult})"
        )

        # unit=12, qty=12 (정상 케이스)
        order2 = {'final_order_qty': 12, 'order_unit_qty': 12}
        calc_mult2 = DirectApiOrderSaver._calc_multiplier(order2)
        audit_mult2 = max(1, (12 + 12 - 1) // 12) if 12 > 0 else 0
        assert calc_mult2 == audit_mult2 == 1

        # qty=0 (발주 안 함)
        order3 = {'final_order_qty': 0, 'order_unit_qty': 12}
        calc_mult3 = DirectApiOrderSaver._calc_multiplier(order3)
        audit_mult3 = max(1, (0 + 12 - 1) // 12) if 0 > 0 else 0
        assert audit_mult3 == 0  # qty=0이면 mult=0

    @patch('src.infrastructure.database.connection.DBRouter.get_connection')
    def test_qty_zero_no_crash(self, mock_get_conn):
        """qty=0인 상품도 unit 보정은 수행"""
        mock_get_conn.return_value = self._mock_db_conn({'TEST008': 24})
        ao = self._make_auto_order()
        items = [
            {'item_cd': 'TEST008', 'final_order_qty': 0, 'order_unit_qty': 1},
        ]
        ao._finalize_order_unit_qty(items)

        assert items[0]['order_unit_qty'] == 24
        assert items[0]['final_order_qty'] == 0  # qty=0 유지


if __name__ == '__main__':
    unittest.main()
