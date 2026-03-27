"""
DirectPopupFetcher 단위 테스트

SSV 파싱 + 데이터 추출 함수 검증
(브라우저 없이 실행 가능)
"""
import pytest
from src.collectors.direct_popup_fetcher import (
    parse_popup_detail,
    parse_popup_ord,
    parse_popup_sale,
    extract_product_detail,
    extract_fail_reason,
    extract_promotion,
    DirectPopupFetcher,
    RS, US,
)


# ═══════════════════════════════════════════════════════════════
# 테스트 SSV 데이터 (실제 캡처 기반)
# ═══════════════════════════════════════════════════════════════

SAMPLE_DETAIL_SSV = (
    f"SSV:UTF-8{RS}"
    f"ErrorCode:string=0{RS}"
    f"ErrorMsg:string={RS}"
    f"xm_tid:string=123{RS}"
    f"Dataset:dsItemDetail{RS}"
    f"_RowType_{US}STORE_CD:string(5){US}ITEM_CD:string(13){US}"
    f"ITEM_NM:string(36){US}LARGE_CD:string(2){US}LARGE_NM:string(20){US}"
    f"MID_CD:string(3){US}MID_NM:string(20){US}"
    f"SMALL_CD:string(3){US}SMALL_NM:string(20){US}"
    f"CLASS_NM:string(100){US}EXPIRE_DAY:string(3){US}"
    f"ORD_UNIT_QTY:bigdecimal(5){US}ORD_UNIT_NM:string(10){US}"
    f"CASE_UNIT_QTY:bigdecimal(5){US}ITEM_MAEGA:bigdecimal(9){US}"
    f"ORD_PSS_ID_NM:string(10){US}ORD_STOP_SYMD:string(8){US}"
    f"ORD_STOP_EYMD:string(8){US}EVT01:string(4000){US}"
    f"EVT01_MOBI:string(4000){US}REASON_ID:string(5){RS}"
    f"N{US}46513{US}8809196620052{US}도)압도적두툼돈까스정식1{US}"
    f"01{US}간편식사{US}001{US}도시락{US}001{US}정식도시락{US}"
    f"간편식사 > 도시락 > 정식도시락{US}1{US}"
    f"1{US}낱개{US}12{US}5900{US}가능{US}{US}{US}"
    f"당월 : 아침애 | 26.01.01~26.12.31 | 행사 요일 : 매일 | 방식 : 지원없음{US}"
    f"당월 : 아침애 | 26.01.01~26.12.31{US}{RS}"
)

SAMPLE_ORD_SSV = (
    f"SSV:UTF-8{RS}"
    f"ErrorCode:string=0{RS}"
    f"ErrorMsg:string={RS}"
    f"xm_tid:string=456{RS}"
    f"Dataset:dsItemDetailOrd{RS}"
    f"_RowType_{US}STORE_CD:string(5){US}ITEM_CD:string(13){US}"
    f"ITEM_NM:string(36){US}ORD_ADAY:string(14){US}"
    f"ORD_UNIT_QTY:bigdecimal(5){US}ORD_PSS_CHK_NM:string(10){US}"
    f"ORD_STOP_SYMD:string(8){US}ORD_STOP_EYMD:string(8){US}"
    f"ORD_LEADTIME:string(2){US}NOW_QTY:bigdecimal(5){RS}"
    f"N{US}46513{US}8809196620052{US}도)압도적두툼돈까스정식1{US}"
    f"일월화수목금토{US}1{US}가능{US}{US}{US}2{US}0{RS}"
)

SAMPLE_SALE_SSV = (
    f"SSV:UTF-8{RS}"
    f"ErrorCode:string=0{RS}"
    f"Dataset:dsOrderSale{RS}"
    f"_RowType_{US}ORD_YMD:string(8){US}ITEM_CD:string(13){US}"
    f"ORD_QTY:string(40){US}BUY_QTY:string(40){US}"
    f"SALE_QTY:string(40){US}DISUSE_QTY:string(40){RS}"
    f"N{US}20260226{US}8809196620052{US}1{US}1{US}1{US}0{RS}"
    f"N{US}20260225{US}8809196620052{US}2{US}2{US}1{US}1{RS}"
)

SAMPLE_DETAIL_STOPPED_SSV = (
    f"SSV:UTF-8{RS}"
    f"Dataset:dsItemDetail{RS}"
    f"_RowType_{US}ITEM_CD:string(13){US}ITEM_NM:string(36){US}"
    f"MID_CD:string(3){US}ORD_PSS_ID_NM:string(10){US}"
    f"ORD_STOP_SYMD:string(8){US}ORD_STOP_EYMD:string(8){US}"
    f"REASON_ID:string(5){US}EXPIRE_DAY:string(3){RS}"
    f"N{US}8801234567890{US}테스트상품{US}010{US}불가{US}"
    f"20260301{US}20260401{US}03{US}30{RS}"
)


# ═══════════════════════════════════════════════════════════════
# parse 함수 테스트
# ═══════════════════════════════════════════════════════════════

class TestParsePopupDetail:
    def test_basic_parse(self):
        row = parse_popup_detail(SAMPLE_DETAIL_SSV)
        assert row is not None
        assert row['ITEM_CD'] == '8809196620052'
        assert row['ITEM_NM'] == '도)압도적두툼돈까스정식1'
        assert row['MID_CD'] == '001'
        assert row['LARGE_CD'] == '01'
        assert row['EXPIRE_DAY'] == '1'

    def test_empty_ssv(self):
        assert parse_popup_detail('') is None
        assert parse_popup_detail(None) is None

    def test_no_item_nm(self):
        """ITEM_NM이 없는 데이터셋은 None"""
        ssv = f"SSV:UTF-8{RS}Dataset:dsOther{RS}_RowType_{US}COL_A:string{RS}N{US}val{RS}"
        assert parse_popup_detail(ssv) is None


class TestParsePopupOrd:
    def test_basic_parse(self):
        row = parse_popup_ord(SAMPLE_ORD_SSV)
        assert row is not None
        assert row['ORD_ADAY'] == '일월화수목금토'
        assert row['ORD_UNIT_QTY'] == '1'
        assert row['ORD_PSS_CHK_NM'] == '가능'
        assert row['ORD_LEADTIME'] == '2'

    def test_empty(self):
        assert parse_popup_ord('') is None


class TestParsePopupSale:
    def test_basic_parse(self):
        rows = parse_popup_sale(SAMPLE_SALE_SSV)
        assert len(rows) == 2
        assert rows[0]['ORD_YMD'] == '20260226'
        assert rows[0]['SALE_QTY'] == '1'
        assert rows[1]['DISUSE_QTY'] == '1'

    def test_empty(self):
        assert parse_popup_sale('') == []


# ═══════════════════════════════════════════════════════════════
# extract 함수 테스트
# ═══════════════════════════════════════════════════════════════

class TestExtractProductDetail:
    def test_full_extract(self):
        detail = parse_popup_detail(SAMPLE_DETAIL_SSV)
        ord_row = parse_popup_ord(SAMPLE_ORD_SSV)
        result = extract_product_detail(detail, ord_row, '8809196620052')

        assert result is not None
        assert result['item_cd'] == '8809196620052'
        assert result['item_nm'] == '도)압도적두툼돈까스정식1'
        assert result['mid_cd'] == '001'
        assert result['large_cd'] == '01'
        assert result['large_nm'] == '간편식사'
        assert result['mid_nm'] == '도시락'
        assert result['small_cd'] == '001'
        assert result['class_nm'] == '간편식사 > 도시락 > 정식도시락'
        assert result['expiration_days'] == 1
        assert result['orderable_day'] == '일월화수목금토'
        assert result['orderable_status'] == '가능'
        assert result['order_unit_qty'] == 1
        assert result['sell_price'] == 5900
        assert result['case_unit_qty'] == 12

    def test_detail_only(self):
        """ord 없이 detail만으로도 동작"""
        detail = parse_popup_detail(SAMPLE_DETAIL_SSV)
        result = extract_product_detail(detail, None, '8809196620052')
        assert result is not None
        assert result['item_nm'] == '도)압도적두툼돈까스정식1'
        assert result['orderable_day'] is None  # ord 없으면 None

    def test_none_detail(self):
        assert extract_product_detail(None, None, '1234') is None


class TestExtractFailReason:
    def test_normal_item(self):
        detail = parse_popup_detail(SAMPLE_DETAIL_SSV)
        ord_row = parse_popup_ord(SAMPLE_ORD_SSV)
        result = extract_fail_reason(detail, ord_row, '8809196620052')

        assert result is not None
        assert result['orderable_status'] == '가능'
        assert result['orderable_day'] == '일월화수목금토'
        assert result['stop_reason'] is None  # 정지 아님

    def test_stopped_item(self):
        """정지 상품 → stop_reason 추론"""
        detail = parse_popup_detail(SAMPLE_DETAIL_STOPPED_SSV)
        result = extract_fail_reason(detail, None, '8801234567890')

        assert result is not None
        assert result['orderable_status'] == '불가'
        assert result['order_stop_date'] == '20260301'
        assert result['stop_reason'] == '일시공급불가'  # 자동 추론

    def test_none(self):
        assert extract_fail_reason(None, None, '1234') is None


class TestExtractPromotion:
    def test_with_evt(self):
        detail = parse_popup_detail(SAMPLE_DETAIL_SSV)
        result = extract_promotion(detail, '8809196620052')

        assert result is not None
        assert result['item_cd'] == '8809196620052'
        assert '아침애' in result['evt_text']

    def test_no_evt(self):
        """EVT01 빈 값"""
        detail = {'ITEM_CD': '123', 'ITEM_NM': 'test', 'EVT01': '', 'EVT01_MOBI': ''}
        result = extract_promotion(detail, '123')
        assert result is not None
        assert result['evt_text'] == ''

    def test_none(self):
        assert extract_promotion(None, '1234') is None


# ═══════════════════════════════════════════════════════════════
# DirectPopupFetcher 클래스 테스트
# ═══════════════════════════════════════════════════════════════

class TestDirectPopupFetcher:
    def test_init(self):
        fetcher = DirectPopupFetcher(driver=None)
        assert fetcher.concurrency == 5
        assert fetcher.timeout_ms == 8000
        assert not fetcher.has_template

    def test_set_templates(self):
        fetcher = DirectPopupFetcher(driver=None)
        fetcher.set_templates("detail_body", "ord_body")
        assert fetcher.has_template
        assert fetcher._detail_template == "detail_body"
        assert fetcher._ord_template == "ord_body"

    def test_fetch_without_template(self):
        fetcher = DirectPopupFetcher(driver=None)
        assert fetcher.fetch_item_detail("123") is None
        assert fetcher.fetch_items_batch(["123"]) == {}

    def test_fetch_product_details_no_template(self):
        fetcher = DirectPopupFetcher(driver=None)
        assert fetcher.fetch_product_details(["123"]) == {}

    def test_fetch_fail_reasons_no_template(self):
        fetcher = DirectPopupFetcher(driver=None)
        assert fetcher.fetch_fail_reasons(["123"]) == {}

    def test_fetch_promotions_no_template(self):
        fetcher = DirectPopupFetcher(driver=None)
        assert fetcher.fetch_promotions(["123"]) == {}

    def test_empty_item_codes(self):
        fetcher = DirectPopupFetcher(driver=None)
        fetcher.set_templates("t1", "t2")
        assert fetcher.fetch_items_batch([]) == {}


# ═══════════════════════════════════════════════════════════════
# EVT01 파싱 통합 테스트
# ═══════════════════════════════════════════════════════════════

class TestEvtTextParsing:
    """promotion_collector._parse_evt_text_to_promo 연동 테스트"""

    def test_parse_evt_text_single_line(self):
        from src.collectors.promotion_collector import PromotionCollector

        collector = PromotionCollector.__new__(PromotionCollector)
        result = collector._parse_evt_text_to_promo(
            '123', 'test',
            '당월 : 1+1 | 26.03.01~26.03.31 | 행사 요일 : 매일 | 방식 : 판촉비'
        )
        assert result is not None
        assert result.promo_type == '1+1'
        assert result.start_date == '2026-03-01'
        assert result.end_date == '2026-03-31'

    def test_parse_evt_text_multiline(self):
        from src.collectors.promotion_collector import PromotionCollector

        collector = PromotionCollector.__new__(PromotionCollector)
        evt = (
            '당월 : 1+1 | 26.03.01~26.03.31 | 행사 요일 : 매일 | 방식 : 판촉비\n'
            '익월 : 2+1 | 26.04.01~26.04.30 | 행사 요일 : 매일 | 방식 : 행사원가'
        )
        result = collector._parse_evt_text_to_promo('123', 'test', evt)
        assert result.promo_type == '1+1'
        assert result.next_promo_type == '2+1'
        assert result.next_start_date == '2026-04-01'

    def test_parse_evt_text_empty(self):
        from src.collectors.promotion_collector import PromotionCollector

        collector = PromotionCollector.__new__(PromotionCollector)
        result = collector._parse_evt_text_to_promo('123', 'test', '')
        assert result is not None
        assert result.promo_type is None
        assert result.next_promo_type is None

    def test_parse_evt_text_non_promo(self):
        """아침애, 할인 등 N+N이 아닌 행사"""
        from src.collectors.promotion_collector import PromotionCollector

        collector = PromotionCollector.__new__(PromotionCollector)
        evt = '당월 : 아침애 | 26.01.01~26.12.31 | 행사 요일 : 매일 | 방식 : 지원없음'
        result = collector._parse_evt_text_to_promo('123', 'test', evt)
        assert result is not None
        assert result.promo_type == '아침애'
