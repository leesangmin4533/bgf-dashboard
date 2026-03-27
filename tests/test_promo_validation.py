"""
행사(promo_type) 유효성 검증 + 오염 방지 테스트

order_prep_collector.py의 _is_valid_promo 함수와
promotion_repo.py의 _is_valid_promo_type 함수가
발주단위명(낱개/묶음/BOX)을 올바르게 거부하는지 검증합니다.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.collectors.order_prep_collector import _is_valid_promo, _normalize_promo
from src.infrastructure.database.repos.promotion_repo import _is_valid_promo_type


class TestNormalizePromo:
    """_normalize_promo 줄바꿈 정규화 테스트"""

    def test_crlf_removed(self):
        # "아침애\r\n할인" → 줄바꿈 제거 후 "아침애 할인" → "할인" 키워드 추출
        assert _normalize_promo("아침애\r\n할인") == "할인"

    def test_lf_removed(self):
        # "아침애\n할인" → "할인" 키워드 추출
        assert _normalize_promo("아침애\n할인") == "할인"

    def test_cr_removed(self):
        # "아침애\r할인" → "할인" 키워드 추출
        assert _normalize_promo("아침애\r할인") == "할인"

    def test_no_change_needed(self):
        assert _normalize_promo("1+1") == "1+1"

    def test_empty(self):
        assert _normalize_promo("") == ""

    def test_strip_whitespace(self):
        assert _normalize_promo("  할인  ") == "할인"


class TestIsValidPromo:
    """_is_valid_promo 유효성 검증 테스트"""

    # 유효한 행사 타입
    @pytest.mark.parametrize("value", [
        "1+1", "2+1", "3+1", "10+1",
        "할인", "덤",
    ])
    def test_valid_promo_types(self, value):
        assert _is_valid_promo(value) is True

    # 유효한 행사: 복합 행사 (BGF 실제 데이터)
    @pytest.mark.parametrize("value", [
        "아침애\r\n할인",      # 실제 MONTH_EVT 값 (줄바꿈 포함)
        "아침애 할인",         # 정규화 후 값
        "아침애할인",          # 공백 없는 경우
        "특가할인",            # 다른 할인 유형
        "기획덤",              # 덤 포함
    ])
    def test_valid_compound_promo(self, value):
        assert _is_valid_promo(value) is True

    # 무효한 값: 발주단위명
    @pytest.mark.parametrize("value", [
        "낱개", "묶음", "BOX", "지함",
    ])
    def test_invalid_unit_names(self, value):
        assert _is_valid_promo(value) is False

    # 무효한 값: 순수 숫자 (발주단위수량)
    @pytest.mark.parametrize("value", [
        "1", "6", "10", "12", "24", "150",
    ])
    def test_invalid_pure_numbers(self, value):
        assert _is_valid_promo(value) is False

    # 무효한 값: 빈값/None
    def test_empty_string(self):
        assert _is_valid_promo("") is False

    def test_none_like(self):
        # None은 str 타입이 아니지만 빈 문자열로 전달됨
        assert _is_valid_promo("") is False


class TestIsValidPromoType:
    """promotion_repo._is_valid_promo_type 테스트 (동일 로직)"""

    @pytest.mark.parametrize("value,expected", [
        ("1+1", True),
        ("2+1", True),
        ("할인", True),
        ("덤", True),
        ("아침애\r\n할인", True),   # 줄바꿈 포함 복합 행사
        ("아침애 할인", True),       # 정규화된 복합 행사
        ("특가할인", True),          # 할인 포함
        ("기획덤", True),            # 덤 포함
        ("낱개", False),
        ("묶음", False),
        ("BOX", False),
        ("지함", False),
        ("1", False),
        ("12", False),
        ("", False),
    ])
    def test_promo_type_validation(self, value, expected):
        assert _is_valid_promo_type(value) is expected


class TestPromoColumnNames:
    """gdList 데이터셋 컬럼명 확인 (BGF 사이트 검증 기반)"""

    def test_correct_column_names_in_code(self):
        """order_prep_collector.py가 올바른 컬럼명(MONTH_EVT/NEXT_MONTH_EVT)을 사용하는지"""
        import inspect
        from src.collectors.order_prep_collector import OrderPrepCollector

        # collect_for_item 메서드의 소스 코드에서 컬럼명 확인
        source = inspect.getsource(OrderPrepCollector.collect_for_item)

        # 올바른 컬럼명이 존재
        assert "MONTH_EVT" in source, "MONTH_EVT 컬럼명이 코드에 존재해야 함"
        assert "NEXT_MONTH_EVT" in source, "NEXT_MONTH_EVT 컬럼명이 코드에 존재해야 함"

        # 잘못된 컬럼 인덱스 접근이 없어야 함
        assert "getColID(11)" not in source, "getColID(11)은 ORD_UNIT이므로 사용하면 안 됨"
        assert "getColID(12)" not in source, "getColID(12)은 ORD_UNIT_QTY이므로 사용하면 안 됨"


class TestPromotionRepoValidation:
    """promotion_repo.py 저장 전 검증 테스트"""

    def test_save_monthly_promo_rejects_invalid(self):
        """무효한 promo_type으로 save_monthly_promo 호출 시 저장하지 않음"""
        from unittest.mock import patch, MagicMock
        from src.infrastructure.database.repos.promotion_repo import PromotionRepository

        repo = PromotionRepository.__new__(PromotionRepository)
        repo._db_path = None
        repo.store_id = "46513"

        # _get_conn을 mock하여 DB 접근 방지
        with patch.object(PromotionRepository, '_get_conn') as mock_conn:
            # 무효 값으로 호출
            result = repo.save_monthly_promo(
                item_cd="TEST001",
                item_nm="테스트상품",
                current_month_promo="낱개",
                next_month_promo="1",
                store_id="46513"
            )
            # DB 연결이 호출되지 않아야 함 (early return)
            mock_conn.assert_not_called()
            assert result['saved'] is False

    def test_save_monthly_promo_allows_valid(self):
        """유효한 promo_type은 저장 로직으로 진행"""
        from unittest.mock import patch, MagicMock
        from src.infrastructure.database.repos.promotion_repo import PromotionRepository

        repo = PromotionRepository.__new__(PromotionRepository)
        repo._db_path = None
        repo.store_id = "46513"

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(PromotionRepository, '_get_conn', return_value=mock_conn), \
             patch.object(PromotionRepository, '_now', return_value='2026-02-27 10:00:00'):
            result = repo.save_monthly_promo(
                item_cd="TEST001",
                item_nm="테스트상품",
                current_month_promo="1+1",
                next_month_promo="",
                store_id="46513"
            )
            # DB 연결이 호출되어야 함
            mock_conn.cursor.assert_called_once()
