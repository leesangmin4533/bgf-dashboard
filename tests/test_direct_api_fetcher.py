"""
Direct API Fetcher 단위 테스트

SSV 파싱, 데이터 변환, 배치 처리, 폴백 로직 검증
"""

import json
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
    extract_dsitem_all_columns,
    _safe_int,
    _clean_text,
    DirectApiFetcher,
    RS, US,
)


# ============================================================
# SSV 테스트 데이터 생성 헬퍼
# ============================================================

def make_ssv_record(columns, rows):
    """SSV 레코드 생성 (헤더 + 데이터 행)"""
    # 헤더: 컬럼명:타입 형식
    header = US.join(f"{col}:String" for col in ['_RowType_'] + columns)
    data_rows = []
    for row in rows:
        data_rows.append(US.join(['N'] + row))
    return RS.join([header] + data_rows)


def make_full_ssv_response(
    ds_item_data=None,
    ds_order_sale_data=None,
    gd_list_data=None,
    ds_week_data=None,
):
    """전체 SSV 응답 생성"""
    records = []

    if ds_item_data:
        cols = ['ITEM_CD', 'ITEM_NM', 'NOW_QTY', 'ORD_UNIT_QTY', 'EXPIRE_DAY']
        records.append(make_ssv_record(cols, ds_item_data))

    if ds_order_sale_data:
        cols = ['ORD_YMD', 'ITEM_CD', 'ORD_QTY', 'BUY_QTY', 'SALE_QTY', 'DISUSE_QTY']
        records.append(make_ssv_record(cols, ds_order_sale_data))

    if gd_list_data:
        cols = ['ITEM_CD', 'ITEM_NM', 'MONTH_EVT', 'NEXT_MONTH_EVT', 'CUT_ITEM_YN',
                'HQ_MAEGA_SET', 'PROFIT_RATE']
        records.append(make_ssv_record(cols, gd_list_data))

    if ds_week_data:
        cols = ['ORD_YMD']
        records.append(make_ssv_record(cols, ds_week_data))

    return RS.join(records)


# ============================================================
# parse_ssv_dataset 테스트
# ============================================================

class TestParseSsvDataset:
    def test_parse_single_row(self):
        ssv = make_ssv_record(
            ['ITEM_CD', 'ITEM_NM', 'NOW_QTY'],
            [['8801001', '테스트상품', '10']]
        )
        result = parse_ssv_dataset(ssv, 'ITEM_NM')
        assert result is not None
        assert 'ITEM_NM' in result['columns']
        assert len(result['rows']) == 1
        # _RowType_ 포함하여 columns에 있으므로 인덱스 보정 필요 없음
        assert 'ITEM_NM' in result['columns']

    def test_parse_multiple_rows(self):
        ssv = make_ssv_record(
            ['ORD_YMD', 'ORD_QTY', 'BUY_QTY', 'SALE_QTY', 'DISUSE_QTY'],
            [
                ['20260225', '5', '5', '3', '0'],
                ['20260226', '3', '0', '4', '1'],
                ['20260227', '0', '3', '2', '0'],
            ]
        )
        result = parse_ssv_dataset(ssv, 'ORD_QTY')
        assert result is not None
        assert len(result['rows']) == 3

    def test_parse_empty_data(self):
        result = parse_ssv_dataset('', 'ITEM_NM')
        assert result is None

    def test_parse_no_matching_marker(self):
        ssv = make_ssv_record(['COL_A', 'COL_B'], [['1', '2']])
        result = parse_ssv_dataset(ssv, 'NONEXISTENT')
        assert result is None

    def test_parse_empty_rows(self):
        """데이터 행이 없는 경우"""
        header = US.join(f"{col}:String" for col in ['_RowType_', 'ITEM_NM', 'NOW_QTY'])
        ssv = header  # 헤더만 있고 데이터 없음
        result = parse_ssv_dataset(ssv, 'ITEM_NM')
        assert result is not None
        assert len(result['rows']) == 0


# ============================================================
# ssv_row_to_dict 테스트
# ============================================================

class TestSsvRowToDict:
    def test_basic(self):
        cols = ['_RowType_', 'ITEM_CD', 'ITEM_NM']
        row = ['N', '8801001', '테스트']
        d = ssv_row_to_dict(cols, row)
        assert d['ITEM_CD'] == '8801001'
        assert d['ITEM_NM'] == '테스트'

    def test_missing_values(self):
        cols = ['A', 'B', 'C', 'D']
        row = ['1', '2']  # C, D 없음
        d = ssv_row_to_dict(cols, row)
        assert d['A'] == '1'
        assert d['C'] == ''
        assert d['D'] == ''


# ============================================================
# parse_full_ssv_response 테스트
# ============================================================

class TestParseFullSsvResponse:
    def test_all_datasets(self):
        ssv = make_full_ssv_response(
            ds_item_data=[['8801001', '테스트상품', '10', '6', '3']],
            ds_order_sale_data=[
                ['20260225', '8801001', '6', '6', '5', '0'],
                ['20260226', '8801001', '0', '0', '3', '1'],
            ],
            gd_list_data=[['8801001', '테스트상품', '1+1', '', '0', '1500', '25.5']],
            ds_week_data=[['20260225'], ['20260226'], ['20260227']],
        )
        result = parse_full_ssv_response(ssv)
        assert 'dsItem' in result
        assert 'dsOrderSale' in result
        assert 'gdList' in result

    def test_partial_response(self):
        """dsItem만 있는 응답"""
        ssv = make_full_ssv_response(
            ds_item_data=[['8801001', '테스트상품', '5', '1', '2']],
        )
        result = parse_full_ssv_response(ssv)
        assert 'dsItem' in result
        assert 'dsOrderSale' not in result

    def test_empty_response(self):
        result = parse_full_ssv_response('')
        assert result == {}


# ============================================================
# extract_item_data 테스트
# ============================================================

class TestExtractItemData:
    def test_full_extraction(self):
        ssv = make_full_ssv_response(
            ds_item_data=[['8801001235765', 'CU 생수 500ml', '10', '6', '365']],
            ds_order_sale_data=[
                ['20260225', '8801001235765', '6', '6', '5', '0'],
                ['20260226', '8801001235765', '12', '0', '3', '1'],
            ],
            gd_list_data=[['8801001235765', 'CU 생수 500ml', '1+1', '2+1', '0', '800', '30.0']],
        )
        parsed = parse_full_ssv_response(ssv)
        result = extract_item_data(parsed, '8801001235765')

        assert result['success'] is True
        assert result['item_nm'] == 'CU 생수 500ml'
        assert result['current_stock'] == 10
        assert result['order_unit_qty'] == 6
        assert result['expiration_days'] == 365
        assert result['current_month_promo'] == '1+1'
        assert result['next_month_promo'] == '2+1'
        assert result['is_cut_item'] is False
        assert result['sell_price'] == '800'
        assert result['margin_rate'] == '30.0'
        assert len(result['history']) == 2
        assert result['history'][0]['ord_qty'] == 6
        assert result['history'][1]['ord_qty'] == 12

    def test_cut_item(self):
        ssv = make_full_ssv_response(
            ds_item_data=[['8801001', '중지상품', '0', '1', '']],
            gd_list_data=[['8801001', '중지상품', '', '', '1', '', '']],
        )
        parsed = parse_full_ssv_response(ssv)
        result = extract_item_data(parsed, '8801001')
        assert result['is_cut_item'] is True

    def test_no_dsitem(self):
        """dsItem이 없는 응답"""
        parsed = {}
        result = extract_item_data(parsed, '8801001')
        assert result['success'] is False
        assert result['item_cd'] == '8801001'

    def test_promo_with_newline(self):
        """행사값에 줄바꿈이 포함된 경우"""
        ssv = make_full_ssv_response(
            ds_item_data=[['8801001', '상품A', '5', '1', '']],
            gd_list_data=[['8801001', '상품A', '1+1\r\n', '\r\n2+1', '0', '', '']],
        )
        parsed = parse_full_ssv_response(ssv)
        result = extract_item_data(parsed, '8801001')
        assert result['current_month_promo'] == '1+1'
        assert result['next_month_promo'] == '2+1'

    def test_empty_promo(self):
        """행사 없는 상품"""
        ssv = make_full_ssv_response(
            ds_item_data=[['8801001', '상품B', '3', '1', '']],
            gd_list_data=[['8801001', '상품B', '', '', '0', '1000', '20']],
        )
        parsed = parse_full_ssv_response(ssv)
        result = extract_item_data(parsed, '8801001')
        assert result['current_month_promo'] == ''
        assert result['next_month_promo'] == ''


# ============================================================
# _safe_int 테스트
# ============================================================

class TestSafeInt:
    def test_normal(self):
        assert _safe_int('10') == 10
        assert _safe_int('0') == 0

    def test_float_string(self):
        assert _safe_int('10.5') == 10

    def test_empty(self):
        assert _safe_int('') == 0
        assert _safe_int(None) == 0

    def test_invalid(self):
        assert _safe_int('abc') == 0
        assert _safe_int('N/A') == 0


# ============================================================
# _clean_text 테스트
# ============================================================

class TestCleanText:
    def test_normal(self):
        assert _clean_text('1+1') == '1+1'

    def test_with_newline(self):
        assert _clean_text('1+1\r\n') == '1+1'
        assert _clean_text('\r\n2+1\r\n') == '2+1'

    def test_with_cr(self):
        assert _clean_text('할인\r') == '할인'

    def test_empty(self):
        assert _clean_text('') == ''
        assert _clean_text(None) == ''


# ============================================================
# DirectApiFetcher 테스트
# ============================================================

class TestDirectApiFetcher:
    def setup_method(self):
        self.mock_driver = MagicMock()
        self.fetcher = DirectApiFetcher(self.mock_driver, concurrency=3, timeout_ms=3000)

    def test_init(self):
        assert self.fetcher.driver is self.mock_driver
        assert self.fetcher.concurrency == 3
        assert self.fetcher.timeout_ms == 3000
        assert self.fetcher._request_template is None

    def test_set_request_template(self):
        self.fetcher.set_request_template('template_body')
        assert self.fetcher._request_template == 'template_body'

    def test_capture_request_template_existing(self):
        """이미 캡처된 템플릿이 있는 경우"""
        self.mock_driver.execute_script.return_value = 'captured_body'
        result = self.fetcher.capture_request_template()
        assert result is True
        assert self.fetcher._request_template == 'captured_body'

    def test_capture_request_template_none(self):
        """캡처할 것이 없는 경우 (인터셉터 설치)"""
        self.mock_driver.execute_script.return_value = None
        result = self.fetcher.capture_request_template()
        assert result is False

    def test_fetch_item_data_no_template(self):
        """템플릿 없이 호출 시 None 반환"""
        result = self.fetcher.fetch_item_data('8801001')
        assert result is None

    def test_fetch_item_data_success(self):
        """단일 상품 조회 성공"""
        self.fetcher.set_request_template('strItemCd=PLACEHOLDER')

        ssv = make_full_ssv_response(
            ds_item_data=[['8801001', '테스트', '5', '1', '30']],
            ds_order_sale_data=[['20260226', '8801001', '3', '3', '2', '0']],
            gd_list_data=[['8801001', '테스트', '1+1', '', '0', '1000', '25']],
        )
        self.mock_driver.execute_script.return_value = ssv

        result = self.fetcher.fetch_item_data('8801001')
        assert result is not None
        assert result['success'] is True
        assert result['item_nm'] == '테스트'
        assert result['current_stock'] == 5
        assert result['current_month_promo'] == '1+1'

    def test_fetch_item_data_exception(self):
        """조회 중 예외 발생"""
        self.fetcher.set_request_template('template')
        self.mock_driver.execute_script.side_effect = Exception("Network error")

        result = self.fetcher.fetch_item_data('8801001')
        assert result is None

    def test_fetch_items_batch_no_template(self):
        """템플릿 없이 배치 호출"""
        result = self.fetcher.fetch_items_batch(['8801001', '8801002'])
        assert result == {}

    def test_fetch_items_batch_empty(self):
        """빈 목록 배치 호출"""
        self.fetcher.set_request_template('template')
        result = self.fetcher.fetch_items_batch([])
        assert result == {}

    def test_fetch_items_batch_success(self):
        """배치 조회 성공"""
        self.fetcher.set_request_template('strItemCd=PLACEHOLDER')

        ssv1 = make_full_ssv_response(
            ds_item_data=[['8801001', '상품A', '10', '6', '3']],
            gd_list_data=[['8801001', '상품A', '', '', '0', '1000', '20']],
        )
        ssv2 = make_full_ssv_response(
            ds_item_data=[['8801002', '상품B', '5', '1', '7']],
            gd_list_data=[['8801002', '상품B', '2+1', '', '0', '2000', '30']],
        )

        # 1차: _validate_template probe, 2차: 배치 JS 실행
        self.mock_driver.execute_script.side_effect = [
            # _validate_template probe 결과
            {'status': 200, 'ok': True, 'len': 500, 'hasItem': True},
            # 배치 JS 결과
            [
                {'barcode': '8801001', 'text': ssv1, 'ok': True, 'status': 200},
                {'barcode': '8801002', 'text': ssv2, 'ok': True, 'status': 200},
            ],
        ]

        result = self.fetcher.fetch_items_batch(['8801001', '8801002'])
        assert len(result) == 2
        assert result['8801001']['success'] is True
        assert result['8801001']['item_nm'] == '상품A'
        assert result['8801002']['success'] is True
        assert result['8801002']['current_month_promo'] == '2+1'

    def test_fetch_items_batch_partial_failure(self):
        """배치 조회 중 일부 실패"""
        self.fetcher.set_request_template('template')

        ssv1 = make_full_ssv_response(
            ds_item_data=[['8801001', '상품A', '10', '6', '3']],
            gd_list_data=[['8801001', '상품A', '', '', '0', '', '']],
        )

        # 1차: _validate_template probe, 2차: 배치 JS 결과
        self.mock_driver.execute_script.side_effect = [
            {'status': 200, 'ok': True, 'len': 500, 'hasItem': True},
            [
                {'barcode': '8801001', 'text': ssv1, 'ok': True, 'status': 200},
                {'barcode': '8801002', 'error': 'timeout', 'ok': False, 'status': 0},
            ],
        ]

        result = self.fetcher.fetch_items_batch(['8801001', '8801002'])
        assert '8801001' in result
        assert '8801002' not in result

    def test_fetch_items_batch_js_exception(self):
        """JS 실행 자체 실패"""
        self.fetcher.set_request_template('template')
        # 1차: _validate_template 성공, 2차: JS 배치 실행에서 예외
        self.mock_driver.execute_script.side_effect = [
            {'status': 200, 'ok': True, 'len': 500, 'hasItem': True},
            Exception("Browser crash"),
        ]

        result = self.fetcher.fetch_items_batch(['8801001'])
        assert result == {}

    def test_fetch_items_batch_validation_failure(self):
        """배치 전 템플릿 검증 실패 시 빈 결과 반환"""
        self.fetcher.set_request_template('template')
        # _validate_template이 HTTP 에러 반환
        self.mock_driver.execute_script.return_value = {
            'status': 403, 'ok': False, 'error': 'Forbidden'
        }

        result = self.fetcher.fetch_items_batch(['8801001'])
        assert result == {}
        # 템플릿이 무효화되었는지 확인
        assert self.fetcher._request_template is None

    def test_ensure_template_with_existing(self):
        """이미 템플릿이 있으면 True"""
        self.fetcher.set_request_template('existing')
        assert self.fetcher.ensure_template() is True

    def test_ensure_template_capture_success(self):
        """캡처 성공"""
        self.mock_driver.execute_script.return_value = 'new_template'
        assert self.fetcher.ensure_template() is True


# ============================================================
# OrderPrepCollector API 통합 테스트
# ============================================================

class TestOrderPrepCollectorDirectApi:
    """OrderPrepCollector의 Direct API 통합 로직 테스트"""

    def setup_method(self):
        self.mock_driver = MagicMock()

    @patch('src.collectors.order_prep_collector.RealtimeInventoryRepository')
    @patch('src.collectors.order_prep_collector.ProductDetailRepository')
    @patch('src.collectors.order_prep_collector.PromotionRepository')
    @patch('src.collectors.order_prep_collector.PromotionManager')
    @patch('src.collectors.order_prep_collector.SalesRepository')
    def test_init_creates_direct_api(self, mock_sr, mock_pm, mock_pr, mock_pdr, mock_rir):
        """__init__에서 DirectApiFetcher 생성 확인"""
        from src.collectors.order_prep_collector import OrderPrepCollector
        collector = OrderPrepCollector(driver=self.mock_driver, save_to_db=False)
        assert collector._direct_api is not None

    @patch('src.collectors.order_prep_collector.RealtimeInventoryRepository')
    @patch('src.collectors.order_prep_collector.ProductDetailRepository')
    @patch('src.collectors.order_prep_collector.PromotionRepository')
    @patch('src.collectors.order_prep_collector.PromotionManager')
    @patch('src.collectors.order_prep_collector.SalesRepository')
    def test_init_no_driver_no_direct_api(self, mock_sr, mock_pm, mock_pr, mock_pdr, mock_rir):
        """driver=None이면 _direct_api도 None"""
        from src.collectors.order_prep_collector import OrderPrepCollector
        collector = OrderPrepCollector(driver=None, save_to_db=False)
        assert collector._direct_api is None

    @patch('src.collectors.order_prep_collector.RealtimeInventoryRepository')
    @patch('src.collectors.order_prep_collector.ProductDetailRepository')
    @patch('src.collectors.order_prep_collector.PromotionRepository')
    @patch('src.collectors.order_prep_collector.PromotionManager')
    @patch('src.collectors.order_prep_collector.SalesRepository')
    def test_process_api_result_basic(self, mock_sr, mock_pm, mock_pr, mock_pdr, mock_rir):
        """_process_api_result 기본 동작"""
        from src.collectors.order_prep_collector import OrderPrepCollector
        collector = OrderPrepCollector(driver=self.mock_driver, save_to_db=False)

        api_data = {
            'item_cd': '8801001',
            'item_nm': '테스트상품',
            'current_stock': 10,
            'order_unit_qty': 6,
            'expiration_days': 30,
            'current_month_promo': '1+1',
            'next_month_promo': '',
            'is_cut_item': False,
            'sell_price': '1000',
            'margin_rate': '25',
            'history': [
                {'date': '20260225', 'item_cd': '8801001', 'ord_qty': 6, 'buy_qty': 6, 'sale_qty': 3, 'disuse_qty': 0},
                {'date': '20260226', 'item_cd': '8801001', 'ord_qty': 6, 'buy_qty': 0, 'sale_qty': 4, 'disuse_qty': 1},
            ],
            'week_dates': ['20260225', '20260226', '20260227'],
            'success': True,
        }

        result = collector._process_api_result('8801001', api_data)
        assert result['success'] is True
        assert result['item_nm'] == '테스트상품'
        assert result['current_stock'] == 10
        assert result['current_month_promo'] == '1+1'
        assert result['is_cut_item'] is False

    @patch('src.collectors.order_prep_collector.RealtimeInventoryRepository')
    @patch('src.collectors.order_prep_collector.ProductDetailRepository')
    @patch('src.collectors.order_prep_collector.PromotionRepository')
    @patch('src.collectors.order_prep_collector.PromotionManager')
    @patch('src.collectors.order_prep_collector.SalesRepository')
    def test_process_api_result_invalid_promo(self, mock_sr, mock_pm, mock_pr, mock_pdr, mock_rir):
        """발주단위명이 행사로 들어온 경우 필터링"""
        from src.collectors.order_prep_collector import OrderPrepCollector
        collector = OrderPrepCollector(driver=self.mock_driver, save_to_db=False)

        api_data = {
            'item_cd': '8801001',
            'item_nm': '테스트',
            'current_stock': 5,
            'order_unit_qty': 1,
            'expiration_days': None,
            'current_month_promo': '낱개',  # 무효
            'next_month_promo': 'BOX',      # 무효
            'is_cut_item': False,
            'sell_price': '',
            'margin_rate': '',
            'history': [],
            'week_dates': [],
            'success': True,
        }

        result = collector._process_api_result('8801001', api_data)
        assert result['current_month_promo'] == ''
        assert result['next_month_promo'] == ''

    @patch('src.collectors.order_prep_collector.RealtimeInventoryRepository')
    @patch('src.collectors.order_prep_collector.ProductDetailRepository')
    @patch('src.collectors.order_prep_collector.PromotionRepository')
    @patch('src.collectors.order_prep_collector.PromotionManager')
    @patch('src.collectors.order_prep_collector.SalesRepository')
    def test_process_api_result_failed(self, mock_sr, mock_pm, mock_pr, mock_pdr, mock_rir):
        """API 결과 실패 시"""
        from src.collectors.order_prep_collector import OrderPrepCollector
        collector = OrderPrepCollector(driver=self.mock_driver, save_to_db=False)

        result = collector._process_api_result('8801001', {'success': False})
        assert result['success'] is False

        result2 = collector._process_api_result('8801001', None)
        assert result2['success'] is False


# ============================================================
# 설정 상수 테스트
# ============================================================

class TestDirectApiConstants:
    def test_constants_exist(self):
        from src.settings.constants import (
            USE_DIRECT_API,
            DIRECT_API_CONCURRENCY,
            DIRECT_API_TIMEOUT_MS,
            DIRECT_API_DELAY_MS,
        )
        assert isinstance(USE_DIRECT_API, bool)
        assert USE_DIRECT_API is True
        assert DIRECT_API_CONCURRENCY == 5
        assert DIRECT_API_TIMEOUT_MS == 5000
        assert DIRECT_API_DELAY_MS == 30


class TestExtractDsitemAllColumns:
    """extract_dsitem_all_columns() 테스트 — dsGeneralGrid 채우기용 전체 필드 추출"""

    def test_extract_all_columns(self):
        """dsItem에서 전체 컬럼 추출"""
        header = US.join([
            '_RowType_', 'STORE_CD:string(5)', 'ITEM_CD:string(13)',
            'ITEM_NM:string(36)', 'PITEM_ID:string(1)',
            'ORD_UNIT_QTY:bigdecimal(5)', 'MID_NM:string(30)',
            'HQ_MAEGA_SET:bigdecimal(0)', 'PROFIT_RATE:bigdecimal(5)',
        ])
        row = US.join(['N', '46513', '8801045571416', '오뚜기)스파게티컵',
                        '0', '12', '면류', '1800', '28.5'])
        ssv = (
            f'SSV:UTF-8{RS}ErrorCode:string=0{RS}ErrorMsg:string={RS}'
            f'Dataset:dsItem{RS}{header}{RS}{row}{RS}'
        )
        result = extract_dsitem_all_columns(ssv)
        assert result is not None
        assert result['STORE_CD'] == '46513'
        assert result['ITEM_CD'] == '8801045571416'
        assert result['ITEM_NM'] == '오뚜기)스파게티컵'
        assert result['ORD_UNIT_QTY'] == '12'
        assert result['MID_NM'] == '면류'
        assert '_RowType_' not in result  # addRow가 자동 설정하므로 제외

    def test_extract_empty_response(self):
        """빈 SSV 응답"""
        result = extract_dsitem_all_columns('')
        assert result is None

    def test_extract_no_dsitem(self):
        """dsItem 없는 응답"""
        ssv = f'SSV:UTF-8{RS}ErrorCode:string=0{RS}'
        result = extract_dsitem_all_columns(ssv)
        assert result is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
