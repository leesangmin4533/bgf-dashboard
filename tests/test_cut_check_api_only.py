"""
CUT 상품 확인 Direct API 전환 테스트

설계 문서: docs/02-design/features/cut-check-api-only.design.md
변경 대상:
  - direct_api_fetcher.py > extract_item_data() 빈 응답 처리
  - order_prep_collector.py > _process_api_result() 빈 응답 결과 반환
  - order_prep_collector.py > _collect_via_direct_api() 폴백 조건
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.collectors.direct_api_fetcher import (
    parse_ssv_dataset,
    ssv_row_to_dict,
    parse_full_ssv_response,
    extract_item_data,
    _safe_int,
    RS, US,
)


# ============================================================
# SSV 테스트 데이터 생성 헬퍼
# ============================================================

def make_ssv_record(columns, rows):
    """SSV 레코드 생성 (헤더 + 데이터 행)"""
    header = US.join(f"{col}:String" for col in ['_RowType_'] + columns)
    data_rows = []
    for row in rows:
        data_rows.append(US.join(['N'] + row))
    return RS.join([header] + data_rows)


def make_ssv_header_only(columns):
    """헤더만 있는 SSV 레코드 (CUT/미취급 상품 시뮬레이션)"""
    return US.join(f"{col}:String" for col in ['_RowType_'] + columns)


def make_full_ssv_response(
    ds_item_data=None,
    ds_item_empty=False,
    ds_order_sale_data=None,
    gd_list_data=None,
    gd_list_empty=False,
    ds_week_data=None,
):
    """전체 SSV 응답 생성

    Args:
        ds_item_data: dsItem 행 데이터 (있으면 정상 상품)
        ds_item_empty: True면 dsItem 헤더만 (CUT 상품 시뮬레이션)
        gd_list_data: gdList 행 데이터
        gd_list_empty: True면 gdList 헤더만
    """
    records = []
    ds_item_cols = ['ITEM_CD', 'ITEM_NM', 'NOW_QTY', 'ORD_UNIT_QTY', 'EXPIRE_DAY']
    gd_list_cols = ['ITEM_CD', 'ITEM_NM', 'MONTH_EVT', 'NEXT_MONTH_EVT', 'CUT_ITEM_YN',
                    'HQ_MAEGA_SET', 'PROFIT_RATE']

    if ds_item_data:
        records.append(make_ssv_record(ds_item_cols, ds_item_data))
    elif ds_item_empty:
        records.append(make_ssv_header_only(ds_item_cols))

    if ds_order_sale_data:
        cols = ['ORD_YMD', 'ITEM_CD', 'ORD_QTY', 'BUY_QTY', 'SALE_QTY', 'DISUSE_QTY']
        records.append(make_ssv_record(cols, ds_order_sale_data))

    if gd_list_data:
        records.append(make_ssv_record(gd_list_cols, gd_list_data))
    elif gd_list_empty:
        records.append(make_ssv_header_only(gd_list_cols))

    if ds_week_data:
        cols = ['ORD_YMD']
        records.append(make_ssv_record(cols, ds_week_data))

    return RS.join(records)


# ============================================================
# 1. extract_item_data: 빈 행 → CUT 처리
# ============================================================

class TestExtractEmptyRowsCut:
    """설계 테스트 #1: dsItem.rows=[] → success=True, is_cut=True, is_empty=True"""

    def test_extract_empty_rows_returns_cut(self):
        """CUT/미취급 상품: dsItem 헤더만 있고 행이 없는 경우"""
        # dsItem 헤더만 있고 행 데이터 없음 (CUT 상품의 실제 응답 패턴)
        ssv = make_full_ssv_response(ds_item_empty=True, gd_list_empty=True)
        parsed = parse_full_ssv_response(ssv)

        # dsItem이 존재하지만 rows가 비어있어야 함
        assert 'dsItem' in parsed
        assert len(parsed['dsItem']['rows']) == 0

        result = extract_item_data(parsed, '8801068933666')
        assert result['success'] is True
        assert result['is_cut_item'] is True
        assert result['is_empty_response'] is True
        assert result['item_cd'] == '8801068933666'
        # 빈 응답이므로 기본값
        assert result['item_nm'] == ''
        assert result['current_stock'] == 0
        assert result['order_unit_qty'] == 1

    def test_extract_empty_rows_no_selenium_trigger(self):
        """빈 응답은 success=True이므로 Selenium 폴백이 트리거되지 않아야 함"""
        ssv = make_full_ssv_response(ds_item_empty=True)
        parsed = parse_full_ssv_response(ssv)
        result = extract_item_data(parsed, '5010314700003')

        # success=True → Selenium 폴백 대상이 아님
        assert result['success'] is True
        # 동시에 CUT으로 마킹
        assert result['is_cut_item'] is True


# ============================================================
# 2. extract_item_data: 정상 상품
# ============================================================

class TestExtractNormalItem:
    """설계 테스트 #2: dsItem.rows=[data] → success=True, is_cut=False"""

    def test_extract_normal_item(self):
        """정상 상품: 행 데이터가 있는 경우"""
        ssv = make_full_ssv_response(
            ds_item_data=[['8801771304173', 'CU)더큰컵 아메리카노', '5', '6', '3']],
            gd_list_data=[['8801771304173', 'CU)더큰컵 아메리카노', '1+1', '', '0', '1500', '28.5']],
        )
        parsed = parse_full_ssv_response(ssv)
        result = extract_item_data(parsed, '8801771304173')

        assert result['success'] is True
        assert result['is_cut_item'] is False
        assert result['is_empty_response'] is False
        assert result['item_nm'] == 'CU)더큰컵 아메리카노'
        assert result['current_stock'] == 5
        assert result['order_unit_qty'] == 6


# ============================================================
# 3. extract_item_data: 실제 CUT 상품 (행 있음 + CUT_ITEM_YN=1)
# ============================================================

class TestExtractActualCutItem:
    """설계 테스트 #3: rows=[data], CUT_ITEM_YN=1 → is_cut=True"""

    def test_extract_actual_cut_item(self):
        """행 데이터가 있지만 CUT_ITEM_YN=1인 상품 (기존 CUT 감지)"""
        ssv = make_full_ssv_response(
            ds_item_data=[['8801001', 'CUT된상품', '0', '1', '']],
            gd_list_data=[['8801001', 'CUT된상품', '', '', '1', '', '']],
        )
        parsed = parse_full_ssv_response(ssv)
        result = extract_item_data(parsed, '8801001')

        assert result['success'] is True
        assert result['is_cut_item'] is True
        # 행이 있으므로 빈 응답은 아님
        assert result['is_empty_response'] is False

    def test_cut_item_yn_overrides_empty_default(self):
        """gdList의 CUT_ITEM_YN=1이 dsItem 빈 응답 기본값을 덮어씀"""
        # dsItem에는 행이 있고 gdList에 CUT_ITEM_YN=1
        ssv = make_full_ssv_response(
            ds_item_data=[['8801001', '상품X', '0', '1', '']],
            gd_list_data=[['8801001', '상품X', '', '', '1', '500', '10']],
        )
        parsed = parse_full_ssv_response(ssv)
        result = extract_item_data(parsed, '8801001')

        # gdList의 CUT_ITEM_YN=1으로 is_cut_item이 True
        assert result['is_cut_item'] is True
        assert result['sell_price'] == '500'


# ============================================================
# 4. extract_item_data: dsItem 자체가 없는 경우
# ============================================================

class TestExtractNoDsitem:
    """설계 테스트 #4: dsItem=None → success=False (기존 유지)"""

    def test_extract_no_dsitem(self):
        """dsItem 자체가 파싱되지 않은 경우 (SSV 파싱 실패)"""
        parsed = {}
        result = extract_item_data(parsed, '8801001')
        assert result['success'] is False
        assert result['is_cut_item'] is False
        assert result['is_empty_response'] is False

    def test_extract_no_dsitem_only_gdlist(self):
        """dsItem 키가 없는 parsed dict → success=False"""
        # 실제 SSV에서는 dsItem과 gdList가 통합 데이터셋이므로
        # dsItem 없이 gdList만 있는 경우는 발생하지 않음
        # 여기서는 parsed dict에서 dsItem 키가 빠진 경우를 테스트
        parsed = {'gdList': {
            'columns': ['_RowType_', 'CUT_ITEM_YN'],
            'rows': [['N', '0']],
        }}
        result = extract_item_data(parsed, '8801001')
        # dsItem이 없으므로 success=False
        assert result['success'] is False


# ============================================================
# 5. _process_api_result: 빈 응답 → CUT 마킹
# ============================================================

class TestProcessApiResultEmptyResponse:
    """설계 테스트 #5: is_empty_response=True → CUT 마킹 + 즉시 반환"""

    @patch('src.collectors.order_prep_collector.RealtimeInventoryRepository')
    @patch('src.collectors.order_prep_collector.ProductDetailRepository')
    @patch('src.collectors.order_prep_collector.PromotionRepository')
    @patch('src.collectors.order_prep_collector.PromotionManager')
    @patch('src.collectors.order_prep_collector.SalesRepository')
    def test_process_api_result_empty_response(self, mock_sr, mock_pm, mock_pr, mock_pdr, mock_rir):
        """빈 응답(CUT/미취급) API 결과 → 즉시 CUT 반환"""
        from src.collectors.order_prep_collector import OrderPrepCollector
        collector = OrderPrepCollector(driver=MagicMock(), save_to_db=False)

        api_data = {
            'item_cd': '8801068933666',
            'item_nm': '',
            'current_stock': 0,
            'order_unit_qty': 1,
            'expiration_days': None,
            'current_month_promo': '',
            'next_month_promo': '',
            'is_cut_item': True,
            'is_empty_response': True,
            'sell_price': '',
            'margin_rate': '',
            'history': [],
            'week_dates': [],
            'success': True,
        }

        result = collector._process_api_result('8801068933666', api_data)
        assert result['success'] is True
        assert result['is_cut_item'] is True
        assert result['is_empty_response'] is True
        assert result['pending_qty'] == 0
        assert result['current_stock'] == 0
        assert result['item_cd'] == '8801068933666'

    @patch('src.collectors.order_prep_collector.RealtimeInventoryRepository')
    @patch('src.collectors.order_prep_collector.ProductDetailRepository')
    @patch('src.collectors.order_prep_collector.PromotionRepository')
    @patch('src.collectors.order_prep_collector.PromotionManager')
    @patch('src.collectors.order_prep_collector.SalesRepository')
    def test_empty_response_skips_pending_calc(self, mock_sr, mock_pm, mock_pr, mock_pdr, mock_rir):
        """빈 응답은 미입고 계산을 건너뛰어야 함 (early return)"""
        from src.collectors.order_prep_collector import OrderPrepCollector
        collector = OrderPrepCollector(driver=MagicMock(), save_to_db=False)

        # 미입고 계산 메서드가 호출되지 않아야 함
        collector._calculate_pending_simplified = MagicMock()
        collector._calculate_pending_with_comparison = MagicMock()

        api_data = {
            'success': True,
            'is_empty_response': True,
            'is_cut_item': True,
        }

        result = collector._process_api_result('8801001', api_data)
        assert result['success'] is True
        # 미입고 계산 함수가 호출되지 않았어야 함
        collector._calculate_pending_simplified.assert_not_called()
        collector._calculate_pending_with_comparison.assert_not_called()


# ============================================================
# 6. _process_api_result: 정상 결과 → 기존 로직
# ============================================================

class TestProcessApiResultNormal:
    """설계 테스트 #6: is_empty_response=False → 기존 로직 (미입고 계산 등)"""

    @patch('src.collectors.order_prep_collector.RealtimeInventoryRepository')
    @patch('src.collectors.order_prep_collector.ProductDetailRepository')
    @patch('src.collectors.order_prep_collector.PromotionRepository')
    @patch('src.collectors.order_prep_collector.PromotionManager')
    @patch('src.collectors.order_prep_collector.SalesRepository')
    def test_process_api_result_normal(self, mock_sr, mock_pm, mock_pr, mock_pdr, mock_rir):
        """정상 API 결과 → 기존 미입고 계산 + DB 저장 로직 실행"""
        from src.collectors.order_prep_collector import OrderPrepCollector
        collector = OrderPrepCollector(driver=MagicMock(), save_to_db=False)

        api_data = {
            'item_cd': '8801771304173',
            'item_nm': 'CU)더큰컵 아메리카노',
            'current_stock': 5,
            'order_unit_qty': 6,
            'expiration_days': 3,
            'current_month_promo': '1+1',
            'next_month_promo': '',
            'is_cut_item': False,
            'is_empty_response': False,
            'sell_price': '1500',
            'margin_rate': '28.5',
            'history': [
                {'date': '20260225', 'item_cd': '8801771304173', 'ord_qty': 6, 'buy_qty': 6, 'sale_qty': 5, 'disuse_qty': 0},
                {'date': '20260226', 'item_cd': '8801771304173', 'ord_qty': 6, 'buy_qty': 0, 'sale_qty': 3, 'disuse_qty': 1},
            ],
            'week_dates': ['20260225', '20260226', '20260227'],
            'success': True,
        }

        result = collector._process_api_result('8801771304173', api_data)
        assert result['success'] is True
        assert result['is_cut_item'] is False
        assert result['item_nm'] == 'CU)더큰컵 아메리카노'
        assert result['current_stock'] == 5
        # 정상 상품은 is_empty_response가 없거나 False
        assert result.get('is_empty_response', False) is False


# ============================================================
# 7. Selenium 폴백: 진짜 실패만 (빈 응답은 제외)
# ============================================================

class TestSeleniumFallbackOnlyRealFailure:
    """설계 테스트 #7: HTTP 에러만 폴백, 빈 응답은 폴백 안 함"""

    @patch('src.collectors.order_prep_collector.RealtimeInventoryRepository')
    @patch('src.collectors.order_prep_collector.ProductDetailRepository')
    @patch('src.collectors.order_prep_collector.PromotionRepository')
    @patch('src.collectors.order_prep_collector.PromotionManager')
    @patch('src.collectors.order_prep_collector.SalesRepository')
    def test_empty_response_not_in_fallback_list(self, mock_sr, mock_pm, mock_pr, mock_pdr, mock_rir):
        """빈 응답(CUT)은 success=True이므로 Selenium 폴백 대상에 포함되지 않아야 함"""
        from src.collectors.order_prep_collector import OrderPrepCollector
        collector = OrderPrepCollector(driver=MagicMock(), save_to_db=False)

        # _collect_via_direct_api 내부의 폴백 조건 시뮬레이션
        # success=True인 빈 응답 결과
        results = {
            'CUT_001': {'item_cd': 'CUT_001', 'success': True, 'is_empty_response': True, 'is_cut_item': True},
            'CUT_002': {'item_cd': 'CUT_002', 'success': True, 'is_empty_response': True, 'is_cut_item': True},
            'FAIL_001': {'item_cd': 'FAIL_001', 'success': False},  # 진짜 실패
        }
        remaining_codes = ['CUT_001', 'CUT_002', 'FAIL_001']

        # _collect_via_direct_api의 폴백 조건과 동일한 로직
        failed = [ic for ic in remaining_codes if not results.get(ic, {}).get('success')]

        # CUT 상품은 success=True이므로 폴백 대상이 아님
        assert 'CUT_001' not in failed
        assert 'CUT_002' not in failed
        # 진짜 실패만 폴백 대상
        assert 'FAIL_001' in failed
        assert len(failed) == 1

    def test_batch_result_empty_vs_failure(self):
        """배치 결과에서 빈 응답(CUT)과 실패를 구분 가능한지 검증"""
        # CUT 상품의 extract_item_data 결과
        ssv_cut = make_full_ssv_response(ds_item_empty=True)
        parsed_cut = parse_full_ssv_response(ssv_cut)
        result_cut = extract_item_data(parsed_cut, '8801068933666')

        # 파싱 실패 (dsItem 없음)
        result_fail = extract_item_data({}, '9999999999999')

        # CUT: success=True → 폴백 불필요
        assert result_cut['success'] is True
        assert result_cut['is_cut_item'] is True

        # 파싱 실패: success=False → 폴백 필요
        assert result_fail['success'] is False
        assert result_fail['is_cut_item'] is False


# ============================================================
# 8. 전체 흐름: 빈 응답 → _cut_items에 추가
# ============================================================

class TestPrefetchCutDetectionFromEmpty:
    """설계 테스트 #8: 전체 흐름 — 빈 응답 → 발주 제외"""

    def test_cut_detection_end_to_end(self):
        """extract_item_data → _process_api_result → is_cut_item=True 전체 흐름"""
        # 1단계: SSV 파싱 (CUT 상품)
        ssv = make_full_ssv_response(ds_item_empty=True, gd_list_empty=True)
        parsed = parse_full_ssv_response(ssv)

        # 2단계: extract_item_data
        api_data = extract_item_data(parsed, '8801068933666')
        assert api_data['success'] is True
        assert api_data['is_cut_item'] is True
        assert api_data['is_empty_response'] is True

        # 3단계: 호출자가 is_cut_item으로 발주 제외 판단
        # (auto_order.py의 prefetch 결과 처리 시뮬레이션)
        cut_items = set()
        if api_data.get('is_cut_item'):
            cut_items.add('8801068933666')

        assert '8801068933666' in cut_items

    def test_mixed_batch_cut_and_normal(self):
        """배치에서 CUT과 정상 상품이 섞인 경우"""
        # 정상 상품
        ssv_normal = make_full_ssv_response(
            ds_item_data=[['8801771304173', '정상상품', '10', '6', '3']],
            gd_list_data=[['8801771304173', '정상상품', '', '', '0', '1500', '28']],
        )
        parsed_normal = parse_full_ssv_response(ssv_normal)
        result_normal = extract_item_data(parsed_normal, '8801771304173')

        # CUT 상품 (빈 응답)
        ssv_cut = make_full_ssv_response(ds_item_empty=True)
        parsed_cut = parse_full_ssv_response(ssv_cut)
        result_cut = extract_item_data(parsed_cut, '8801068933666')

        # 호출자 로직 시뮬레이션
        batch_results = {
            '8801771304173': result_normal,
            '8801068933666': result_cut,
        }

        cut_items = set()
        for item_cd, r in batch_results.items():
            if r.get('success') and r.get('is_cut_item'):
                cut_items.add(item_cd)

        # CUT 상품만 제외 목록에 포함
        assert '8801068933666' in cut_items
        assert '8801771304173' not in cut_items

        # 정상 상품은 발주 가능
        assert result_normal['success'] is True
        assert result_normal['is_cut_item'] is False

    @patch('src.collectors.order_prep_collector.RealtimeInventoryRepository')
    @patch('src.collectors.order_prep_collector.ProductDetailRepository')
    @patch('src.collectors.order_prep_collector.PromotionRepository')
    @patch('src.collectors.order_prep_collector.PromotionManager')
    @patch('src.collectors.order_prep_collector.SalesRepository')
    def test_process_then_prefetch_integration(self, mock_sr, mock_pm, mock_pr, mock_pdr, mock_rir):
        """_process_api_result → prefetch 결과로 CUT 감지 통합 테스트"""
        from src.collectors.order_prep_collector import OrderPrepCollector
        collector = OrderPrepCollector(driver=MagicMock(), save_to_db=False)

        # extract_item_data 결과 (CUT 빈 응답)
        api_data = {
            'item_cd': '8801068933666',
            'item_nm': '',
            'current_stock': 0,
            'order_unit_qty': 1,
            'expiration_days': None,
            'current_month_promo': '',
            'next_month_promo': '',
            'is_cut_item': True,
            'is_empty_response': True,
            'sell_price': '',
            'margin_rate': '',
            'history': [],
            'week_dates': [],
            'success': True,
        }

        # _process_api_result 실행
        result = collector._process_api_result('8801068933666', api_data)

        # prefetch 결과에서 CUT 감지 (auto_order.py의 실제 로직)
        assert result['success'] is True
        assert result['is_cut_item'] is True
        assert result['pending_qty'] == 0

        # auto_order.py: _cut_items.add() 조건
        if result.get('is_cut_item'):
            detected = True
        else:
            detected = False
        assert detected is True


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
