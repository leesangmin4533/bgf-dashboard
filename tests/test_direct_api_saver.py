"""
DirectApiOrderSaver 테스트 (27개)

SSV body 빌드, API 호출, 응답 파싱, 에러 핸들링, 검증 테스트
"""

import json
import os
import pytest
from unittest.mock import MagicMock, PropertyMock, patch

from src.order.direct_api_saver import (
    DirectApiOrderSaver,
    PREFETCH_ITEMS_JS,
    SaveResult,
    RS, US,
)


# =====================================================================
# Fixtures
# =====================================================================

_AVAIL_OK = json.dumps({'available': True, 'ordYn': '', 'ordClose': ''})


@pytest.fixture
def mock_driver():
    """Mock Selenium WebDriver"""
    driver = MagicMock()
    driver.execute_script = MagicMock(return_value=None)
    return driver


@pytest.fixture
def saver(mock_driver):
    """DirectApiOrderSaver 인스턴스"""
    s = DirectApiOrderSaver(mock_driver, timeout_ms=5000, max_batch=50)
    return s


@pytest.fixture
def sample_orders():
    """샘플 발주 목록"""
    return [
        {'item_cd': '8801043036016', 'final_order_qty': 5, 'order_unit_qty': 1, 'multiplier': 5},
        {'item_cd': '8809112345678', 'final_order_qty': 12, 'order_unit_qty': 6, 'multiplier': 2},
        {'item_cd': '8800100200300', 'final_order_qty': 3, 'order_unit_qty': 1, 'multiplier': 3},
    ]


@pytest.fixture
def saver_with_template(saver):
    """템플릿이 설정된 saver"""
    saver._save_template = {
        'url': '/stbj030/insSave',
        'body': f'_RowType_:STRING:256{US}ITEM_CD:STRING:256{US}ORD_QTY:INT:0{RS}N{US}TEST{US}1',
    }
    saver._save_endpoint = '/stbj030/insSave'
    return saver


# =====================================================================
# SSV Body 빌드 테스트
# =====================================================================

class TestBuildSsvBody:
    """SSV body 빌드 테스트"""

    def test_build_single_item(self, saver):
        """단일 상품 SSV body"""
        orders = [{'item_cd': '1234567890123', 'multiplier': 3}]
        body = saver.build_ssv_body(orders, '20260227')

        assert RS in body
        assert US in body
        assert '1234567890123' in body
        assert '3' in body

    def test_build_multiple_items(self, saver, sample_orders):
        """복수 상품 SSV body"""
        body = saver.build_ssv_body(sample_orders, '20260227')
        records = body.split(RS)

        # 헤더 + 3행
        assert len(records) == 4
        assert '8801043036016' in records[1]
        assert '8809112345678' in records[2]

    def test_build_empty_orders(self, saver):
        """빈 주문 목록"""
        body = saver.build_ssv_body([], '20260227')
        # 헤더만 있어야 함
        records = body.split(RS)
        assert len(records) == 1

    def test_build_calculates_multiplier(self, saver):
        """multiplier 미지정 시 자동 계산"""
        orders = [{'item_cd': '1234', 'final_order_qty': 7, 'order_unit_qty': 3, 'multiplier': 0}]
        body = saver.build_ssv_body(orders, '20260227')
        records = body.split(RS)
        # 7/3 = 2.33 -> 올림 3
        fields = records[1].split(US)
        assert '3' in fields  # multiplier = 3

    def test_build_with_template(self, saver_with_template, sample_orders):
        """템플릿 기반 SSV body"""
        body = saver_with_template.build_ssv_body(
            sample_orders, '20260227',
            template_body=saver_with_template._save_template['body'],
        )
        assert '8801043036016' in body


# =====================================================================
# API 호출 테스트
# =====================================================================

class TestSaveOrders:
    """save_orders() 테스트"""

    def test_save_success(self, saver_with_template, sample_orders, mock_driver):
        """정상 저장 성공 (transaction 실패 → fetch 성공)"""
        # _save_via_transaction: AVAIL → PREFETCH → None, POPULATE → None → returns None
        # _save_via_fetch: fetch() → 성공 응답
        mock_driver.execute_script.side_effect = [
            _AVAIL_OK,  # CHECK_ORDER_AVAILABILITY_JS
            None,  # PREFETCH_ITEMS_JS → None (no template)
            None,  # POPULATE_DATASET_JS → None
            json.dumps({
                'ok': True, 'status': 200,
                'text': f'SSV:UTF-8{RS}ErrorCode:string=0{RS}ErrorMsg:string='
            }),  # _save_via_fetch: fetch()
        ]

        result = saver_with_template.save_orders(sample_orders, '20260227')
        assert result.success is True
        assert result.saved_count == 3
        assert 'direct_api' in result.method

    def test_save_via_transaction_success(self, saver_with_template, sample_orders, mock_driver):
        """gfn_transaction 직접 성공"""
        mock_driver.execute_script.side_effect = [
            _AVAIL_OK,  # CHECK_ORDER_AVAILABILITY_JS
            '[]',  # PREFETCH → empty results
            json.dumps({'success': True, 'added': 3, 'total': 3, 'dsRowCount': 3}),
            json.dumps({'started': True, 'added': 3}),  # CALL_GFN_TRANSACTION_JS
            json.dumps({'success': True, 'errCd': '0', 'errMsg': '', 'added': 3}),
        ]

        result = saver_with_template.save_orders(sample_orders, '20260227')
        assert result.success is True
        assert result.saved_count == 3
        assert result.method == 'direct_api_transaction'

    def test_save_api_timeout(self, saver_with_template, sample_orders, mock_driver):
        """API 타임아웃 (transaction 실패 → fetch 타임아웃)"""
        mock_driver.execute_script.side_effect = [
            _AVAIL_OK,  # CHECK_ORDER_AVAILABILITY_JS
            None,  # PREFETCH → None
            None,  # POPULATE → None → transaction returns None
            json.dumps({
                'ok': False, 'error': 'AbortError: signal timed out'
            }),  # _save_via_fetch
        ]

        result = saver_with_template.save_orders(sample_orders, '20260227')
        assert result.success is False
        assert 'timed out' in result.message

    def test_save_server_error(self, saver_with_template, sample_orders, mock_driver):
        """서버 에러 (500)"""
        mock_driver.execute_script.side_effect = [
            _AVAIL_OK,  # CHECK_ORDER_AVAILABILITY_JS
            None,  # PREFETCH → None
            None,  # POPULATE → None
            json.dumps({
                'ok': False, 'status': 500, 'text': 'Internal Server Error'
            }),  # _save_via_fetch
        ]

        result = saver_with_template.save_orders(sample_orders, '20260227')
        assert result.success is False
        assert 'HTTP 500' in result.message

    def test_save_auth_failure(self, saver_with_template, sample_orders, mock_driver):
        """인증 실패 (401)"""
        mock_driver.execute_script.side_effect = [
            _AVAIL_OK,  # CHECK_ORDER_AVAILABILITY_JS
            None,  # PREFETCH → None
            None,  # POPULATE → None
            json.dumps({
                'ok': False, 'status': 401, 'text': 'Unauthorized'
            }),  # _save_via_fetch
        ]

        result = saver_with_template.save_orders(sample_orders, '20260227')
        assert result.success is False

    def test_save_no_template(self, saver, sample_orders, mock_driver):
        """템플릿 없이 저장 시도 (transaction 실패, fetch도 불가)"""
        mock_driver.execute_script.return_value = None
        result = saver.save_orders(sample_orders, '20260227')
        assert result.success is False
        assert 'failed' in result.message.lower()

    @patch('src.order.direct_api_saver.time.sleep')
    def test_save_chunked_over_batch(self, _mock_sleep, saver_with_template, mock_driver):
        """배치 크기 초과 시 청크 분할 처리"""
        import json
        orders = [{'item_cd': f'item_{i}', 'multiplier': 1} for i in range(51)]

        # Mock: 각 청크별 5회 execute_script 호출
        # check_avail + prefetch + populate + gfn_transaction + poll
        populate_ok = json.dumps({'success': True, 'added': 50, 'dsRowCount': 50, 'rowType0': 2, 'avgFieldsPerRow': 0})
        populate_ok2 = json.dumps({'success': True, 'added': 1, 'dsRowCount': 1, 'rowType0': 2, 'avgFieldsPerRow': 0})
        tx_ok = json.dumps({'started': True, 'added': 50})
        tx_ok2 = json.dumps({'started': True, 'added': 1})
        poll_ok = json.dumps({'success': True, 'added': 50, 'errCd': '0', 'errMsg': '', 'svcId': 'savOrd'})
        poll_ok2 = json.dumps({'success': True, 'added': 1, 'errCd': '0', 'errMsg': '', 'svcId': 'savOrd'})

        mock_driver.execute_script.side_effect = [
            # chunk 1 (50건): check_avail + prefetch + populate + gfn_transaction + poll
            _AVAIL_OK, None, populate_ok, tx_ok, poll_ok,
            # chunk 2 (1건): check_avail + prefetch + populate + gfn_transaction + poll
            _AVAIL_OK, None, populate_ok2, tx_ok2, poll_ok2,
        ]
        # switch_to.alert 속성 접근 시 예외 발생 → alert 루프 즉시 종료
        type(mock_driver.switch_to).alert = PropertyMock(side_effect=Exception('no alert'))

        result = saver_with_template.save_orders(orders, '20260227')
        assert result.success is True
        assert result.saved_count == 51
        assert result.method == 'direct_api_chunked'
        assert 'chunks' in result.message

    @patch('src.order.direct_api_saver.time.sleep')
    def test_save_chunked_partial_fail(self, _mock_sleep, saver_with_template, mock_driver):
        """청크 분할 중 두 번째 청크 실패 시 전체 실패"""
        import json
        orders = [{'item_cd': f'item_{i}', 'multiplier': 1} for i in range(51)]

        populate_ok = json.dumps({'success': True, 'added': 50, 'dsRowCount': 50, 'rowType0': 2, 'avgFieldsPerRow': 0})
        tx_ok = json.dumps({'started': True, 'added': 50})
        poll_ok = json.dumps({'success': True, 'added': 50, 'errCd': '0', 'errMsg': '', 'svcId': 'savOrd'})

        mock_driver.execute_script.side_effect = [
            # chunk 1 (50건): check_avail + prefetch + populate + gfn_transaction + poll
            _AVAIL_OK, None, populate_ok, tx_ok, poll_ok,
            # chunk 2 (1건): check_avail + prefetch + populate 실패 (None)
            _AVAIL_OK, None, None,
        ]
        type(mock_driver.switch_to).alert = PropertyMock(side_effect=Exception('no alert'))

        result = saver_with_template.save_orders(orders, '20260227')
        assert result.success is False
        assert result.saved_count == 50  # 첫 번째 청크만 성공
        assert 'chunk 2/2 failed' in result.message

    def test_save_empty_orders(self, saver_with_template):
        """빈 주문 저장"""
        result = saver_with_template.save_orders([], '20260227')
        assert result.success is True
        assert result.saved_count == 0

    def test_save_dry_run(self, saver_with_template, sample_orders):
        """dry-run 모드"""
        result = saver_with_template.save_orders(sample_orders, '20260227', dry_run=True)
        assert result.success is True
        assert result.saved_count == 3
        assert 'dry_run' in result.message

    def test_save_date_format_normalization(self, saver_with_template, sample_orders, mock_driver):
        """날짜 형식 정규화 (YYYY-MM-DD -> YYYYMMDD)"""
        mock_driver.execute_script.side_effect = [
            _AVAIL_OK,  # CHECK_ORDER_AVAILABILITY_JS
            None,  # PREFETCH → None
            None,  # POPULATE → None → transaction returns None
            json.dumps({
                'ok': True, 'status': 200,
                'text': f'SSV:UTF-8{RS}ErrorCode:string=0{RS}ErrorMsg:string='
            }),  # _save_via_fetch → 성공
        ]
        result = saver_with_template.save_orders(sample_orders, '2026-02-27')
        assert result.success is True


# =====================================================================
# 검증 테스트
# =====================================================================

class TestVerifySave:
    """verify_save() 테스트"""

    def test_verify_success(self, saver_with_template, sample_orders, mock_driver):
        """검증 성공"""
        mock_driver.execute_script.return_value = {
            'items': [
                {'item_cd': '8801043036016', 'ord_qty': 5},
                {'item_cd': '8809112345678', 'ord_qty': 2},
                {'item_cd': '8800100200300', 'ord_qty': 3},
            ],
            'count': 3,
        }

        result = saver_with_template.verify_save(sample_orders)
        assert result['verified'] is True
        assert result['matched'] == 3

    def test_verify_mismatch(self, saver_with_template, sample_orders, mock_driver):
        """검증 불일치"""
        mock_driver.execute_script.return_value = {
            'items': [
                {'item_cd': '8801043036016', 'ord_qty': 5},
                {'item_cd': '8809112345678', 'ord_qty': 999},  # 불일치
                {'item_cd': '8800100200300', 'ord_qty': 3},
            ],
            'count': 3,
        }

        result = saver_with_template.verify_save(sample_orders)
        assert result['verified'] is False
        assert len(result['mismatched']) == 1

    def test_verify_grid_replaced_after_save(self, saver_with_template, sample_orders, mock_driver):
        """gfn_transaction 후 outDS 덮어쓰기로 그리드 교체 → 스킵 처리"""
        # 서버 응답이 그리드를 다른 데이터로 교체한 경우
        # matched=0, mismatched=0, missing=전부 → grid_replaced_after_save
        mock_driver.execute_script.return_value = {
            'items': [
                {'item_cd': 'COMPLETELY_DIFFERENT_1', 'ord_qty': 10},
                {'item_cd': 'COMPLETELY_DIFFERENT_2', 'ord_qty': 20},
            ],
            'count': 2,
        }

        result = saver_with_template.verify_save(sample_orders)
        assert result['verified'] is True
        assert result['skipped'] is True
        assert result['reason'] == 'grid_replaced_after_save'

    def test_verify_grid_replaced_many_items(self, saver_with_template, mock_driver):
        """대량 발주 후 그리드 교체 스킵 (실제 운영 시나리오)"""
        orders = [
            {'item_cd': f'880{i:010d}', 'final_order_qty': 1, 'order_unit_qty': 1, 'multiplier': 1}
            for i in range(104)
        ]
        mock_driver.execute_script.return_value = {
            'items': [
                {'item_cd': f'999{j:010d}', 'ord_qty': 0}
                for j in range(200)  # 서버 응답으로 완전히 다른 200건 로드
            ],
            'count': 200,
        }

        result = saver_with_template.verify_save(orders)
        assert result['verified'] is True
        assert result['skipped'] is True
        assert result['reason'] == 'grid_replaced_after_save'
        assert result['total'] == 104

    def test_verify_partial_match_not_skipped(self, saver_with_template, sample_orders, mock_driver):
        """일부 일치 + 일부 누락은 스킵 아닌 실패 반환"""
        mock_driver.execute_script.return_value = {
            'items': [
                {'item_cd': '8801043036016', 'ord_qty': 5},  # 일치
                {'item_cd': 'OTHER_ITEM', 'ord_qty': 10},
            ],
            'count': 2,
        }

        result = saver_with_template.verify_save(sample_orders)
        # matched=1, missing=2 → 스킵 조건 불만족 (matched > 0)
        assert result['verified'] is False
        assert result['matched'] == 1
        assert len(result['missing']) == 2
        assert 'skipped' not in result

    def test_verify_grid_cleared_still_skipped(self, saver_with_template, sample_orders, mock_driver):
        """그리드 0행 (기존 스킵 로직 유지)"""
        mock_driver.execute_script.return_value = {
            'items': [],
            'count': 0,
        }

        result = saver_with_template.verify_save(sample_orders)
        assert result['verified'] is True
        assert result['skipped'] is True
        assert result['reason'] == 'grid_cleared_after_save'

    def test_verify_returns_ds_source(self, saver_with_template, mock_driver):
        """T3: dsGeneralGrid 직접 참조 → grid_count=0일 때도 dsSource 무관 스킵"""
        mock_driver.execute_script.return_value = {
            'items': [],
            'count': 0,
            'dsSource': 'dsGeneralGrid',
            'sampleItems': [],
        }
        orders = [{'item_cd': 'X', 'final_order_qty': 1, 'order_unit_qty': 1}]
        result = saver_with_template.verify_save(orders)
        assert result['skipped'] is True  # grid_count==0 → grid_cleared

    def test_verify_grid_replaced_with_ds_source(self, saver_with_template, mock_driver):
        """T4: dsSource 포함 반환 + 그리드 교체 패턴"""
        mock_driver.execute_script.return_value = {
            'items': [{'item_cd': 'DIFF', 'ord_qty': 1}],
            'count': 1,
            'dsSource': 'dsGeneralGrid',
            'sampleItems': ['DIFF'],
        }
        orders = [{'item_cd': 'A', 'final_order_qty': 1, 'order_unit_qty': 1}]
        result = saver_with_template.verify_save(orders)
        assert result['skipped'] is True  # grid_replaced
        assert result['reason'] == 'grid_replaced_after_save'


# =====================================================================
# 템플릿 관리 테스트
# =====================================================================

class TestTemplateManagement:
    """템플릿 캡처/로드 테스트"""

    def test_has_template_false(self, saver):
        """템플릿 없음"""
        assert saver.has_template is False

    def test_has_template_true(self, saver_with_template):
        """템플릿 있음"""
        assert saver_with_template.has_template is True

    def test_set_template_from_file(self, saver, tmp_path):
        """파일에서 템플릿 로드"""
        capture_file = tmp_path / "capture.json"
        capture_file.write_text(json.dumps({
            'save_gfn_transactions': [{
                'txId': 'save01',
                'svcURL': '/stbj030/insSave',
                'inDS': 'dsOrder=dsOrder',
                'outDS': '',
                'args': '',
            }],
            'save_xhr_requests': [],
        }), encoding='utf-8')

        result = saver.set_template_from_file(str(capture_file))
        assert result is True
        assert saver.has_template is True
        assert saver._save_endpoint == '/stbj030/insSave'

    def test_set_template_file_not_found(self, saver):
        """존재하지 않는 파일"""
        result = saver.set_template_from_file('/nonexistent/file.json')
        assert result is False


# =====================================================================
# 프리페치 테스트
# =====================================================================

class TestPrefetchItemDetails:
    """_prefetch_item_details() 테스트"""

    def test_prefetch_success(self, saver, mock_driver):
        """프리페치 성공 — selSearch SSV 파싱"""
        # selSearch SSV 응답 (dsItem)
        ssv_resp = (
            'SSV:UTF-8\x1eErrorCode:string=0\x1eErrorMsg:string=\x1e'
            'Dataset:dsItem\x1e'
            '_RowType_\x1fSTORE_CD:string(5)\x1fITEM_CD:string(13)\x1f'
            'ITEM_NM:string(36)\x1fPITEM_ID:string(1)\x1f'
            'ORD_UNIT_QTY:bigdecimal(5)\x1fMID_NM:string(30)\x1e'
            'N\x1f46513\x1f8801043036016\x1f테스트상품\x1f0\x1f12\x1f면류\x1e'
        )

        mock_driver.execute_script.return_value = json.dumps([
            {'itemCd': '8801043036016', 'text': ssv_resp, 'ok': True},
        ])

        result = saver._prefetch_item_details(['8801043036016'])
        assert '8801043036016' in result
        assert result['8801043036016']['STORE_CD'] == '46513'
        assert result['8801043036016']['ITEM_NM'] == '테스트상품'
        assert result['8801043036016']['MID_NM'] == '면류'

    def test_prefetch_no_template(self, saver, mock_driver):
        """프리페치 템플릿 없음 — 빈 딕셔너리 반환"""
        mock_driver.execute_script.return_value = json.dumps({
            'error': 'no_selSearch_template'
        })

        result = saver._prefetch_item_details(['8801043036016'])
        assert result == {}

    def test_prefetch_null_response(self, saver, mock_driver):
        """프리페치 null 응답"""
        mock_driver.execute_script.return_value = None
        result = saver._prefetch_item_details(['8801043036016'])
        assert result == {}

    def test_prefetch_exception(self, saver, mock_driver):
        """프리페치 예외 — 빈 딕셔너리 반환"""
        mock_driver.execute_script.side_effect = Exception("timeout")
        result = saver._prefetch_item_details(['8801043036016'])
        assert result == {}

    def test_prefetch_empty_list(self, saver, mock_driver):
        """빈 목록 프리페치"""
        result = saver._prefetch_item_details([])
        assert result == {}
        mock_driver.execute_script.assert_not_called()
