"""
DirectFrameFetcher 단위 테스트

SSV 파싱 + 데이터 추출 함수 검증
(브라우저 없이 실행 가능)
"""
import pytest
from src.collectors.direct_frame_fetcher import (
    replace_ssv_column,
    parse_all_datasets,
    parse_receiving_chit_list,
    parse_receiving_items,
    parse_waste_slip_list,
    parse_waste_slip_detail,
    parse_order_status_result,
    DirectReceivingFetcher,
    DirectWasteSlipFetcher,
    DirectWasteSlipDetailFetcher,
    DirectOrderStatusFetcher,
    install_interceptor,
    get_captures,
    RS, US,
)


# ═══════════════════════════════════════════════════════════════
# 테스트 SSV 데이터 (실제 캡처 기반)
# ═══════════════════════════════════════════════════════════════

SAMPLE_RECEIVING_CHITLIST_SSV = (
    f"SSV:UTF-8{RS}"
    f"ErrorCode:string=0{RS}"
    f"Dataset:dsListPopup{RS}"
    f"_RowType_{US}NAP_PLAN_YMD:string(8){US}DGFW_YMD:string(8){US}"
    f"CENTER_NM:string(20){US}CHIT_NO:string(10){US}"
    f"AIS_HMS:string(8){US}ACP_HMS:string(8){US}"
    f"ORD_YMD:string(8){US}OSTORE_ORD_ID:string(5){RS}"
    f"N{US}20260227{US}20260227{US}수도권이온저온2{US}CH001{US}"
    f"02270645{US}02270700{US}20260225{US}OT001{RS}"
    f"N{US}20260227{US}20260227{US}수도권이온상온{US}CH002{US}"
    f"02270800{US}02270815{US}20260225{US}OT002{RS}"
)

SAMPLE_RECEIVING_ITEMS_SSV = (
    f"SSV:UTF-8{RS}"
    f"ErrorCode:string=0{RS}"
    f"Dataset:dsList{RS}"
    f"_RowType_{US}CHIT_NO:string(10){US}CHIT_SEQ:string(3){US}"
    f"ITEM_CD:string(13){US}ITEM_NM:string(40){US}"
    f"CUST_NM:string(20){US}ORD_QTY:bigdecimal(5){US}"
    f"NAP_QTY:bigdecimal(5){US}NAP_PLAN_QTY:bigdecimal(5){US}"
    f"ORD_UNIT_QTY:bigdecimal(5){US}CENTER_CD:string(5){US}"
    f"ITEM_WONGA:bigdecimal(9){RS}"
    f"N{US}CH001{US}001{US}8801234567890{US}도)테스트도시락{US}"
    f"도시락공장{US}10{US}10{US}10{US}1{US}C001{US}3500{RS}"
    f"N{US}CH001{US}002{US}8801234567891{US}김)삼각김밥{US}"
    f"김밥공장{US}20{US}18{US}20{US}1{US}C001{US}800{RS}"
)

SAMPLE_WASTE_SLIP_SSV = (
    f"SSV:UTF-8{RS}"
    f"ErrorCode:string=0{RS}"
    f"Dataset:dsList{RS}"
    f"_RowType_{US}CHIT_FLAG:string(2){US}CHIT_ID:string(2){US}"
    f"CHIT_ID_NM:string(10){US}CHIT_YMD:string(8){US}"
    f"CHIT_NO:string(10){US}ITEM_CNT:bigdecimal(5){US}"
    f"CENTER_CD:string(5){US}CENTER_NM:string(20){US}"
    f"WONGA_AMT:bigdecimal(11){US}MAEGA_AMT:bigdecimal(11){RS}"
    f"N{US}10{US}04{US}폐기{US}20260227{US}WS001{US}5{US}"
    f"C001{US}수도권센터{US}15000{US}20000{RS}"
    f"N{US}10{US}04{US}폐기{US}20260226{US}WS002{US}3{US}"
    f"C001{US}수도권센터{US}8000{US}12000{RS}"
)

SAMPLE_ORDER_STATUS_SSV = (
    f"SSV:UTF-8{RS}"
    f"ErrorCode:string=0{RS}"
    # dsResult
    f"Dataset:dsResult{RS}"
    f"_RowType_{US}ORD_YMD:string(8){US}ITEM_CD:string(13){US}"
    f"ITEM_NM:string(36){US}MID_CD:string(3){US}MID_NM:string(20){US}"
    f"ORD_CNT:bigdecimal(5){US}ORD_UNIT_QTY:bigdecimal(5){US}"
    f"ITEM_WONGA:bigdecimal(9){US}NOW_QTY:bigdecimal(5){US}"
    f"NAP_NEXTORD:string(5){US}ORD_INPUT_ID:string(5){US}"
    f"ORD_PSS_ID:string(5){RS}"
    f"N{US}20260227{US}8801234567890{US}도)테스트도시락{US}001{US}도시락{US}"
    f"5{US}1{US}3500{US}10{US}Y{US}AUTO{US}01{RS}"
    f"N{US}20260227{US}8801234567891{US}김)삼각김밥{US}003{US}김밥{US}"
    f"3{US}1{US}800{US}5{US}N{US}MANUAL{US}01{RS}"
    # dsOrderSale
    f"Dataset:dsOrderSale{RS}"
    f"_RowType_{US}ORD_YMD:string(8){US}ITEM_CD:string(13){US}"
    f"JIP_ITEM_CD:string(13){US}ORD_QTY:bigdecimal(5){US}"
    f"BUY_QTY:bigdecimal(5){US}SALE_QTY:bigdecimal(5){US}"
    f"DISUSE_QTY:bigdecimal(5){US}SUM_UNIT_ID:string(5){RS}"
    f"N{US}20260226{US}8801234567890{US}8801234567890{US}5{US}5{US}4{US}1{US}EA{RS}"
    f"N{US}20260225{US}8801234567890{US}8801234567890{US}3{US}3{US}2{US}0{US}EA{RS}"
    # dsWeek
    f"Dataset:dsWeek{RS}"
    f"_RowType_{US}ORD_YMD:string(8){RS}"
    f"N{US}20260227{RS}"
    f"N{US}20260226{RS}"
    f"N{US}20260225{RS}"
)

SAMPLE_SSV_BODY = (
    f"SSV:UTF-8{RS}"
    f"Dataset:dsSearch{RS}"
    f"_RowType_{US}strNapYmd:string(8){US}strAcpYmd:string(8){US}"
    f"strChitNo:string(10){US}strStoreCd:string(5){RS}"
    f"N{US}20260227{US}20260227{US}CH001{US}46513{RS}"
)


# ═══════════════════════════════════════════════════════════════
# replace_ssv_column 테스트
# ═══════════════════════════════════════════════════════════════

class TestReplaceSsvColumn:
    def test_replace_date(self):
        result = replace_ssv_column(SAMPLE_SSV_BODY, 'strNapYmd', '20260301')
        assert '20260301' in result
        # 다른 컬럼은 유지
        assert '46513' in result

    def test_replace_chit_no(self):
        result = replace_ssv_column(SAMPLE_SSV_BODY, 'strChitNo', 'CH999')
        assert 'CH999' in result
        # 원래 값은 교체됨
        assert f'{US}CH001{US}' not in result

    def test_replace_nonexistent_column(self):
        """존재하지 않는 컬럼 → 원본 유지"""
        result = replace_ssv_column(SAMPLE_SSV_BODY, 'strNotExist', 'val')
        assert result == SAMPLE_SSV_BODY

    def test_empty_body(self):
        result = replace_ssv_column('', 'strNapYmd', '20260301')
        assert result == ''

    def test_multiple_replacements(self):
        result = replace_ssv_column(SAMPLE_SSV_BODY, 'strNapYmd', '20260301')
        result = replace_ssv_column(result, 'strAcpYmd', '20260302')
        assert '20260301' in result
        assert '20260302' in result


# ═══════════════════════════════════════════════════════════════
# parse_all_datasets 테스트
# ═══════════════════════════════════════════════════════════════

class TestParseAllDatasets:
    def test_order_status_multiple_datasets(self):
        datasets = parse_all_datasets(SAMPLE_ORDER_STATUS_SSV)
        assert 'dsResult' in datasets
        assert 'dsOrderSale' in datasets
        assert 'dsWeek' in datasets

    def test_dsresult_rows(self):
        datasets = parse_all_datasets(SAMPLE_ORDER_STATUS_SSV)
        assert len(datasets['dsResult']['rows']) == 2

    def test_dsordersale_rows(self):
        datasets = parse_all_datasets(SAMPLE_ORDER_STATUS_SSV)
        assert len(datasets['dsOrderSale']['rows']) == 2

    def test_dsweek_rows(self):
        datasets = parse_all_datasets(SAMPLE_ORDER_STATUS_SSV)
        assert len(datasets['dsWeek']['rows']) == 3

    def test_empty_ssv(self):
        assert parse_all_datasets('') == {}
        assert parse_all_datasets(None) == {}

    def test_single_dataset(self):
        datasets = parse_all_datasets(SAMPLE_WASTE_SLIP_SSV)
        assert 'dsList' in datasets
        assert len(datasets['dsList']['rows']) == 2


# ═══════════════════════════════════════════════════════════════
# Receiving 파싱 테스트
# ═══════════════════════════════════════════════════════════════

class TestParseReceivingChitList:
    def test_basic_parse(self):
        rows = parse_receiving_chit_list(SAMPLE_RECEIVING_CHITLIST_SSV)
        assert len(rows) == 2
        assert rows[0]['CHIT_NO'] == 'CH001'
        assert rows[0]['DGFW_YMD'] == '20260227'
        assert rows[0]['CENTER_NM'] == '수도권이온저온2'
        assert rows[0]['AIS_HMS'] == '02270645'

    def test_second_row(self):
        rows = parse_receiving_chit_list(SAMPLE_RECEIVING_CHITLIST_SSV)
        assert rows[1]['CHIT_NO'] == 'CH002'
        assert rows[1]['CENTER_NM'] == '수도권이온상온'

    def test_empty(self):
        assert parse_receiving_chit_list('') == []
        assert parse_receiving_chit_list(None) == []


class TestParseReceivingItems:
    def test_basic_parse(self):
        rows = parse_receiving_items(SAMPLE_RECEIVING_ITEMS_SSV)
        assert len(rows) == 2
        assert rows[0]['ITEM_CD'] == '8801234567890'
        assert rows[0]['ITEM_NM'] == '도)테스트도시락'
        assert rows[0]['ORD_QTY'] == '10'
        assert rows[0]['NAP_QTY'] == '10'

    def test_second_item(self):
        rows = parse_receiving_items(SAMPLE_RECEIVING_ITEMS_SSV)
        assert rows[1]['ITEM_CD'] == '8801234567891'
        assert rows[1]['NAP_QTY'] == '18'

    def test_empty(self):
        assert parse_receiving_items('') == []


# ═══════════════════════════════════════════════════════════════
# Waste Slip 파싱 테스트
# ═══════════════════════════════════════════════════════════════

class TestParseWasteSlipList:
    def test_basic_parse(self):
        rows = parse_waste_slip_list(SAMPLE_WASTE_SLIP_SSV)
        assert len(rows) == 2
        assert rows[0]['CHIT_FLAG'] == '10'
        assert rows[0]['CHIT_YMD'] == '20260227'
        assert rows[0]['CHIT_NO'] == 'WS001'
        assert rows[0]['ITEM_CNT'] == '5'

    def test_second_slip(self):
        rows = parse_waste_slip_list(SAMPLE_WASTE_SLIP_SSV)
        assert rows[1]['CHIT_NO'] == 'WS002'
        assert rows[1]['ITEM_CNT'] == '3'

    def test_empty(self):
        assert parse_waste_slip_list('') == []
        assert parse_waste_slip_list(None) == []


# ═══════════════════════════════════════════════════════════════
# Order Status 파싱 테스트
# ═══════════════════════════════════════════════════════════════

class TestParseOrderStatusResult:
    def test_dsresult(self):
        result = parse_order_status_result(SAMPLE_ORDER_STATUS_SSV)
        assert len(result['dsResult']) == 2
        assert result['dsResult'][0]['ITEM_CD'] == '8801234567890'
        assert result['dsResult'][0]['MID_CD'] == '001'
        assert result['dsResult'][0]['ORD_CNT'] == '5'

    def test_dsordersale(self):
        result = parse_order_status_result(SAMPLE_ORDER_STATUS_SSV)
        assert len(result['dsOrderSale']) == 2
        assert result['dsOrderSale'][0]['ORD_QTY'] == '5'
        assert result['dsOrderSale'][0]['BUY_QTY'] == '5'
        assert result['dsOrderSale'][0]['DISUSE_QTY'] == '1'

    def test_dsweek(self):
        result = parse_order_status_result(SAMPLE_ORDER_STATUS_SSV)
        assert len(result['dsWeek']) == 3
        assert result['dsWeek'][0] == '20260227'

    def test_empty(self):
        result = parse_order_status_result('')
        assert result['dsResult'] == []
        assert result['dsOrderSale'] == []
        assert result['dsWeek'] == []


# ═══════════════════════════════════════════════════════════════
# DirectReceivingFetcher 클래스 테스트
# ═══════════════════════════════════════════════════════════════

class TestDirectReceivingFetcher:
    def test_init(self):
        fetcher = DirectReceivingFetcher(driver=None)
        assert fetcher.concurrency == 3
        assert fetcher.timeout_ms == 8000
        assert not fetcher.has_template

    def test_set_templates(self):
        fetcher = DirectReceivingFetcher(driver=None)
        fetcher.set_templates("chitlist_body", "search_body")
        assert fetcher.has_template
        assert fetcher._chitlist_template == "chitlist_body"
        assert fetcher._search_template == "search_body"

    def test_set_templates_search_fallback(self):
        """search 미제공 시 chitlist로 폴백"""
        fetcher = DirectReceivingFetcher(driver=None)
        fetcher.set_templates("chitlist_body")
        assert fetcher._search_template == "chitlist_body"

    def test_fetch_without_template(self):
        fetcher = DirectReceivingFetcher(driver=None)
        assert fetcher.fetch_chit_list('20260227') == []
        assert fetcher.fetch_items_for_chits(['CH001'], '20260227') == {}

    def test_empty_chit_nos(self):
        fetcher = DirectReceivingFetcher(driver=None)
        fetcher.set_templates("t1", "t2")
        assert fetcher.fetch_items_for_chits([], '20260227') == {}


# ═══════════════════════════════════════════════════════════════
# DirectWasteSlipFetcher 클래스 테스트
# ═══════════════════════════════════════════════════════════════

class TestDirectWasteSlipFetcher:
    def test_init(self):
        fetcher = DirectWasteSlipFetcher(driver=None)
        assert fetcher.timeout_ms == 8000
        assert not fetcher.has_template

    def test_set_template(self):
        fetcher = DirectWasteSlipFetcher(driver=None)
        fetcher.set_template("search_body")
        assert fetcher.has_template
        assert fetcher._search_template == "search_body"

    def test_fetch_without_template(self):
        fetcher = DirectWasteSlipFetcher(driver=None)
        assert fetcher.fetch_waste_slips('20260226', '20260227') == []


# ═══════════════════════════════════════════════════════════════
# DirectOrderStatusFetcher 클래스 테스트
# ═══════════════════════════════════════════════════════════════

class TestDirectOrderStatusFetcher:
    def test_init(self):
        fetcher = DirectOrderStatusFetcher(driver=None)
        assert fetcher.timeout_ms == 10000
        assert not fetcher.has_template

    def test_set_template(self):
        fetcher = DirectOrderStatusFetcher(driver=None)
        fetcher.set_template("search_body")
        assert fetcher.has_template

    def test_fetch_without_template(self):
        fetcher = DirectOrderStatusFetcher(driver=None)
        result = fetcher.fetch_order_status()
        assert result == {'dsResult': [], 'dsOrderSale': [], 'dsWeek': []}


# ═══════════════════════════════════════════════════════════════
# OrderStatusCollector 변환 함수 테스트
# ═══════════════════════════════════════════════════════════════

class TestOrderStatusConverters:
    def test_convert_api_dsresult(self):
        from src.collectors.order_status_collector import OrderStatusCollector

        api_rows = [
            {'ITEM_CD': '123', 'ITEM_NM': 'test', 'MID_CD': '001',
             'ORD_UNIT_QTY': '6', 'ORD_CNT': '2'},
        ]
        result = OrderStatusCollector._convert_api_dsresult(api_rows)
        assert len(result) == 1
        assert result[0]['ITEM_CD'] == '123'
        assert result[0]['ORD_UNIT_QTY'] == '6'

    def test_convert_api_dsordersale(self):
        from src.collectors.order_status_collector import OrderStatusCollector

        api_rows = [
            {'ORD_YMD': '20260227', 'ITEM_CD': '123',
             'ORD_QTY': '5', 'BUY_QTY': '5'},
        ]
        result = OrderStatusCollector._convert_api_dsordersale(api_rows)
        assert len(result) == 1
        assert result[0]['ORD_QTY'] == '5'

    def test_extract_order_unit_from_api(self):
        from src.collectors.order_status_collector import OrderStatusCollector

        collector = OrderStatusCollector.__new__(OrderStatusCollector)
        api_rows = [
            {'ITEM_CD': '8801234567890', 'ITEM_NM': 'test', 'MID_CD': '001',
             'ORD_UNIT_QTY': '12'},
            {'ITEM_CD': '8801234567891', 'ITEM_NM': 'test2', 'MID_CD': '003',
             'ORD_UNIT_QTY': ''},
            {'ITEM_CD': '', 'ITEM_NM': 'empty', 'MID_CD': '',
             'ORD_UNIT_QTY': '1'},
        ]
        result = collector._extract_order_unit_from_api(api_rows)
        assert len(result) == 2  # empty ITEM_CD는 제외
        assert result[0]['item_cd'] == '8801234567890'
        assert result[0]['order_unit_qty'] == 12
        assert result[1]['order_unit_qty'] == 1  # empty → 1


# ═══════════════════════════════════════════════════════════════
# ReceivingCollector._build_receiving_record 테스트
# ═══════════════════════════════════════════════════════════════

class TestBuildReceivingRecord:
    def test_basic_conversion(self):
        from src.collectors.receiving_collector import ReceivingCollector

        collector = ReceivingCollector.__new__(ReceivingCollector)
        collector.store_id = None
        collector.repo = None

        chit = {
            'CHIT_NO': 'CH001',
            'DGFW_YMD': '20260227',
            'ORD_YMD': '20260225',
            'AIS_HMS': '02270645',
            'CENTER_NM': '수도권이온저온2',
        }
        item = {
            'ITEM_CD': '8801234567890',
            'ITEM_NM': '도)테스트도시락',
            'CUST_NM': '도시락공장',
            'ORD_QTY': '10',
            'NAP_QTY': '10',
            'NAP_PLAN_QTY': '10',
            'CENTER_CD': 'C001',
        }

        record = collector._build_receiving_record(chit, item, '20260227')
        assert record['receiving_date'] == '2026-02-27'
        assert record['receiving_time'] == '06:45'
        assert record['chit_no'] == 'CH001'
        assert record['item_cd'] == '8801234567890'
        assert record['order_qty'] == 10
        assert record['receiving_qty'] == 10
        assert record['delivery_type'] == 'ambient'  # 도시락이지만 접미사 1/2 없음 → ambient
        assert record['order_date'] == '2026-02-25'


# ═══════════════════════════════════════════════════════════════
# INTERCEPTOR_JS 존재 확인
# ═══════════════════════════════════════════════════════════════

class TestInterceptor:
    def test_interceptor_js_exists(self):
        from src.collectors.direct_frame_fetcher import INTERCEPTOR_JS
        assert 'window.__collectorCaptures' in INTERCEPTOR_JS
        assert 'fetch' in INTERCEPTOR_JS
        assert 'XMLHttpRequest' in INTERCEPTOR_JS

    def test_install_interceptor_no_driver(self):
        """driver=None → False 반환 (예외 없이)"""
        result = install_interceptor(None)
        assert result is False

    def test_get_captures_no_driver(self):
        """driver=None → 빈 리스트"""
        result = get_captures(None)
        assert result == []


# ═══════════════════════════════════════════════════════════════
# 폐기 전표 상세 품목 (searchDetailType1) 테스트 데이터
# ═══════════════════════════════════════════════════════════════

SAMPLE_WASTE_DETAIL_SSV = (
    f"SSV:UTF-8{RS}"
    f"ErrorCode:string=0{RS}"
    f"Dataset:dsListType0{RS}"
    f"_RowType_{US}ITEM_CD:string(13){US}ITEM_NM:string(40){US}"
    f"LARGE_NM:string(20){US}QTY:bigdecimal(5){US}"
    f"WONGA:bigdecimal(9){US}WONGA_AMT:bigdecimal(11){US}"
    f"MAEGA:bigdecimal(9){US}MAEGA_AMT:bigdecimal(11){US}"
    f"VENDOR_NM:string(30){RS}"
    f"N{US}8801234567890{US}도)테스트도시락{US}도시락{US}2{US}"
    f"3500{US}7000{US}5000{US}10000{US}도시락공장{RS}"
    f"N{US}8801234567891{US}김)삼각김밥{US}김밥{US}3{US}"
    f"800{US}2400{US}1200{US}3600{US}김밥공장{RS}"
    f"Dataset:dsListType1{RS}"
    f"_RowType_{US}ITEM_CD:string(13){US}ITEM_NM:string(40){US}"
    f"LARGE_NM:string(20){US}QTY:bigdecimal(5){US}"
    f"WONGA:bigdecimal(9){US}WONGA_AMT:bigdecimal(11){US}"
    f"MAEGA:bigdecimal(9){US}MAEGA_AMT:bigdecimal(11){US}"
    f"VENDOR_NM:string(30){RS}"
    f"N{US}8801234567892{US}빵)크림빵{US}빵{US}1{US}"
    f"1500{US}1500{US}2000{US}2000{US}빵공장{RS}"
)

SAMPLE_WASTE_DETAIL_EMPTY_SSV = (
    f"SSV:UTF-8{RS}"
    f"ErrorCode:string=0{RS}"
    f"Dataset:dsListType0{RS}"
    f"_RowType_{US}ITEM_CD:string(13){US}ITEM_NM:string(40){RS}"
)

SAMPLE_WASTE_DETAIL_BODY = (
    f"SSV:UTF-8{RS}"
    f"Dataset:dsSearch{RS}"
    f"_RowType_{US}strChitDiv:string(2){US}strChitNo:string(11){US}"
    f"strChitNoList:string(100){US}strChitYmd:string(8){US}"
    f"strStoreCd:string(5){RS}"
    f"N{US}10{US}69462040501{US}{US}20260325{US}46513{RS}"
)


# ═══════════════════════════════════════════════════════════════
# parse_waste_slip_detail 테스트
# ═══════════════════════════════════════════════════════════════

class TestParseWasteSlipDetail:
    def test_basic_parse(self):
        """dsListType0 + dsListType1 합쳐서 반환"""
        items = parse_waste_slip_detail(SAMPLE_WASTE_DETAIL_SSV)
        assert len(items) == 3  # 2건 (Type0) + 1건 (Type1)

    def test_first_item(self):
        items = parse_waste_slip_detail(SAMPLE_WASTE_DETAIL_SSV)
        assert items[0]['ITEM_CD'] == '8801234567890'
        assert items[0]['ITEM_NM'] == '도)테스트도시락'
        assert items[0]['QTY'] == '2'
        assert items[0]['WONGA'] == '3500'
        assert items[0]['WONGA_AMT'] == '7000'
        assert items[0]['VENDOR_NM'] == '도시락공장'
        assert items[0]['_dsType'] == '0'

    def test_dstype_separation(self):
        """각 품목에 _dsType 구분이 정확한지 확인"""
        items = parse_waste_slip_detail(SAMPLE_WASTE_DETAIL_SSV)
        assert items[0]['_dsType'] == '0'
        assert items[1]['_dsType'] == '0'
        assert items[2]['_dsType'] == '1'

    def test_type1_item(self):
        items = parse_waste_slip_detail(SAMPLE_WASTE_DETAIL_SSV)
        assert items[2]['ITEM_CD'] == '8801234567892'
        assert items[2]['ITEM_NM'] == '빵)크림빵'
        assert items[2]['QTY'] == '1'

    def test_empty_datasets(self):
        """데이터 행 없는 응답 → 빈 리스트"""
        items = parse_waste_slip_detail(SAMPLE_WASTE_DETAIL_EMPTY_SSV)
        assert items == []

    def test_empty_string(self):
        assert parse_waste_slip_detail('') == []

    def test_none(self):
        assert parse_waste_slip_detail(None) == []


# ═══════════════════════════════════════════════════════════════
# DirectWasteSlipDetailFetcher 클래스 테스트
# ═══════════════════════════════════════════════════════════════

class TestDirectWasteSlipDetailFetcher:
    def test_init(self):
        fetcher = DirectWasteSlipDetailFetcher(driver=None)
        assert fetcher.timeout_ms == 8000
        assert not fetcher.has_template

    def test_set_template(self):
        fetcher = DirectWasteSlipDetailFetcher(driver=None)
        fetcher.set_template("detail_body")
        assert fetcher.has_template
        assert fetcher._detail_template == "detail_body"

    def test_fetch_without_template(self):
        """템플릿 없으면 빈 리스트 반환"""
        fetcher = DirectWasteSlipDetailFetcher(driver=None)
        assert fetcher.fetch_slip_details('WS001', '20260325') == []

    def test_fetch_all_without_template(self):
        """템플릿 없으면 빈 리스트 반환"""
        fetcher = DirectWasteSlipDetailFetcher(driver=None)
        slips = [{'CHIT_NO': 'WS001', 'CHIT_YMD': '20260325'}]
        assert fetcher.fetch_all_slip_details(slips) == []

    def test_fetch_all_empty_list(self):
        """빈 전표 목록 → 빈 리스트"""
        fetcher = DirectWasteSlipDetailFetcher(driver=None)
        fetcher.set_template("body")
        assert fetcher.fetch_all_slip_details([]) == []

    def test_endpoint_constant(self):
        assert DirectWasteSlipDetailFetcher.ENDPOINT == '/stgj020/searchDetailType1'

    def test_custom_timeout(self):
        fetcher = DirectWasteSlipDetailFetcher(driver=None, timeout_ms=5000)
        assert fetcher.timeout_ms == 5000
