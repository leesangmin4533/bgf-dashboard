"""
소멸성 상품(perishable) 카테고리 모듈 단위 테스트

대상: src/prediction/categories/perishable.py
- is_perishable_category(): 소멸성 카테고리 여부 확인
- PERISHABLE_EXPIRY_CONFIG: 유통기한별 안전재고 설정값 검증
- analyze_perishable_pattern(): 유통기한별 안전재고일수 분기
- get_safety_stock_with_perishable_pattern(): 비대상 카테고리 fallback
- max_stock 계산: max(유통기한일 x 일평균, 3일치 x 일평균)
- skip_order: 재고+미입고 >= max_stock
"""

import sys
import sqlite3
import pytest
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from prediction.categories.perishable import (
    is_perishable_category,
    PERISHABLE_CATEGORIES,
    PERISHABLE_EXPIRY_CONFIG,
    PERISHABLE_EXPIRY_FALLBACK,
    PERISHABLE_DYNAMIC_SAFETY_CONFIG,
    DEFAULT_WEEKDAY_COEF,
    MIN_MAX_STOCK_DAYS,
    _get_perishable_expiry_group,
    analyze_perishable_pattern,
    get_safety_stock_with_perishable_pattern,
    PerishablePatternResult,
)


# =============================================================================
# is_perishable_category 테스트
# =============================================================================
class TestIsPerishableCategory:
    """소멸성 상품 카테고리 판별 테스트"""

    @pytest.mark.unit
    def test_013_is_perishable(self):
        """'013' (떡)은 소멸성 카테고리"""
        assert is_perishable_category("013") is True

    @pytest.mark.unit
    def test_026_is_perishable(self):
        """'026' (과일/채소)은 소멸성 카테고리"""
        assert is_perishable_category("026") is True

    @pytest.mark.unit
    def test_046_is_perishable(self):
        """'046' (요구르트)은 소멸성 카테고리"""
        assert is_perishable_category("046") is True

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", [
        "010", "049", "050", "072", "001", "032", "999", "", "13", "0013",
    ])
    def test_non_perishable_categories(self, mid_cd):
        """소멸성이 아닌 카테고리는 False 반환"""
        assert is_perishable_category(mid_cd) is False

    @pytest.mark.unit
    def test_perishable_categories_list(self):
        """PERISHABLE_CATEGORIES 상수에 3개 코드 포함 확인"""
        assert "013" in PERISHABLE_CATEGORIES
        assert "026" in PERISHABLE_CATEGORIES
        assert "046" in PERISHABLE_CATEGORIES
        assert len(PERISHABLE_CATEGORIES) == 3


# =============================================================================
# PERISHABLE_EXPIRY_CONFIG 설정값 검증
# =============================================================================
class TestPerishableExpiryConfig:
    """유통기한별 안전재고 설정값 테스트"""

    @pytest.mark.unit
    def test_ultra_short_config(self):
        """ultra_short (1-3일): safety_days = 0.3"""
        cfg = PERISHABLE_EXPIRY_CONFIG["ultra_short"]
        assert cfg["min_days"] == 1
        assert cfg["max_days"] == 3
        assert cfg["safety_days"] == 0.3

    @pytest.mark.unit
    def test_short_config(self):
        """short (4-7일): safety_days = 0.5"""
        cfg = PERISHABLE_EXPIRY_CONFIG["short"]
        assert cfg["min_days"] == 4
        assert cfg["max_days"] == 7
        assert cfg["safety_days"] == 0.5

    @pytest.mark.unit
    def test_medium_config(self):
        """medium (8-14일): safety_days = 0.8"""
        cfg = PERISHABLE_EXPIRY_CONFIG["medium"]
        assert cfg["min_days"] == 8
        assert cfg["max_days"] == 14
        assert cfg["safety_days"] == 0.8

    @pytest.mark.unit
    def test_long_config(self):
        """long (15일+): safety_days = 1.0"""
        cfg = PERISHABLE_EXPIRY_CONFIG["long"]
        assert cfg["min_days"] == 15
        assert cfg["max_days"] == 9999
        assert cfg["safety_days"] == 1.0

    @pytest.mark.unit
    def test_all_groups_have_required_keys(self):
        """모든 그룹에 min_days, max_days, safety_days, description 키 존재"""
        required_keys = {"min_days", "max_days", "safety_days", "description"}
        for group_name, group_cfg in PERISHABLE_EXPIRY_CONFIG.items():
            assert required_keys.issubset(group_cfg.keys()), f"{group_name} 키 누락"

    @pytest.mark.unit
    def test_safety_days_increase_with_expiry(self):
        """유통기한 길수록 안전재고일수 증가"""
        groups = ["ultra_short", "short", "medium", "long"]
        days = [PERISHABLE_EXPIRY_CONFIG[g]["safety_days"] for g in groups]
        assert days == sorted(days), "안전재고일수가 단조 증가해야 함"


# =============================================================================
# _get_perishable_expiry_group 테스트
# =============================================================================
class TestGetPerishableExpiryGroup:
    """유통기한 그룹 분류 테스트"""

    @pytest.mark.unit
    @pytest.mark.parametrize("days,expected_group", [
        (1, "ultra_short"),
        (2, "ultra_short"),
        (3, "ultra_short"),
        (4, "short"),
        (7, "short"),
        (8, "medium"),
        (14, "medium"),
        (15, "long"),
        (100, "long"),
    ])
    def test_expiry_group_classification(self, days, expected_group):
        """유통기한별 그룹 분류 정확성"""
        group_name, _ = _get_perishable_expiry_group(days)
        assert group_name == expected_group


# =============================================================================
# analyze_perishable_pattern 테스트 (DB 의존)
# =============================================================================
class TestAnalyzePerishablePattern:
    """소멸성 상품 패턴 분석 테스트"""

    @pytest.fixture
    def perishable_db(self, tmp_path):
        """소멸성 상품 테스트 DB 생성"""
        db_file = tmp_path / "test_perishable.db"
        conn = sqlite3.connect(str(db_file))

        conn.execute("""
            CREATE TABLE daily_sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_cd TEXT NOT NULL,
                sales_date TEXT NOT NULL,
                sale_qty INTEGER DEFAULT 0,
                stock_qty INTEGER DEFAULT 0,
                mid_cd TEXT,
                UNIQUE(item_cd, sales_date)
            )
        """)
        conn.execute("""
            CREATE TABLE product_details (
                item_cd TEXT PRIMARY KEY,
                item_nm TEXT,
                mid_cd TEXT,
                order_unit_qty INTEGER DEFAULT 1,
                expiration_days INTEGER,
                orderable_day TEXT DEFAULT '일월화수목금토'
            )
        """)

        # 상품 정보 등록 (유통기한 2일 - ultra_short)
        conn.execute(
            "INSERT INTO product_details (item_cd, item_nm, mid_cd, expiration_days) "
            "VALUES (?, ?, ?, ?)",
            ("ITEM_RICE_CAKE", "떡", "013", 2)
        )

        # 30일치 판매 데이터 (일평균 약 10개)
        today = datetime.now()
        for days_ago in range(30):
            date = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            conn.execute(
                "INSERT OR REPLACE INTO daily_sales (item_cd, sales_date, sale_qty, mid_cd) "
                "VALUES (?, ?, ?, ?)",
                ("ITEM_RICE_CAKE", date, 10, "013")
            )

        conn.commit()
        conn.close()
        return str(db_file)

    @pytest.mark.db
    def test_analyze_returns_dataclass(self, perishable_db):
        """analyze_perishable_pattern이 PerishablePatternResult 반환"""
        result = analyze_perishable_pattern(
            "ITEM_RICE_CAKE", "013", db_path=perishable_db
        )
        assert isinstance(result, PerishablePatternResult)

    @pytest.mark.db
    def test_ultra_short_safety_days(self, perishable_db):
        """유통기한 2일(ultra_short) 상품 → safety_days=0.3"""
        result = analyze_perishable_pattern(
            "ITEM_RICE_CAKE", "013", db_path=perishable_db
        )
        assert result.expiry_group == "ultra_short"
        assert result.safety_days == 0.3

    @pytest.mark.db
    def test_daily_avg_calculation(self, perishable_db):
        """일평균 판매량 계산 확인"""
        result = analyze_perishable_pattern(
            "ITEM_RICE_CAKE", "013", db_path=perishable_db
        )
        assert result.daily_avg > 0
        assert result.total_sales > 0

    @pytest.mark.db
    def test_max_stock_calculation(self, perishable_db):
        """max_stock = max(유통기한일 x 일평균, 3일치 x 일평균)"""
        result = analyze_perishable_pattern(
            "ITEM_RICE_CAKE", "013", db_path=perishable_db
        )
        # 유통기한 2일, MIN_MAX_STOCK_DAYS=3 이므로 3일치가 더 큼
        expected_max = max(2 * result.daily_avg, MIN_MAX_STOCK_DAYS * result.daily_avg)
        assert abs(result.max_stock - round(expected_max, 2)) < 0.01

    @pytest.mark.db
    def test_min_max_stock_days_applied(self, perishable_db):
        """유통기한이 짧아도 최소 3일치 max_stock 보장"""
        result = analyze_perishable_pattern(
            "ITEM_RICE_CAKE", "013", db_path=perishable_db
        )
        # 유통기한 2일 < MIN_MAX_STOCK_DAYS(3) 이므로 3일치 적용
        assert result.max_stock >= MIN_MAX_STOCK_DAYS * result.daily_avg - 0.01

    @pytest.mark.db
    def test_skip_order_when_over_max_stock(self, perishable_db):
        """재고+미입고 >= max_stock이면 skip_order=True"""
        result = analyze_perishable_pattern(
            "ITEM_RICE_CAKE", "013", db_path=perishable_db,
            current_stock=100, pending_qty=100
        )
        assert result.skip_order is True
        assert "상한선 초과" in result.skip_reason

    @pytest.mark.db
    def test_no_skip_when_under_max_stock(self, perishable_db):
        """재고+미입고 < max_stock이면 skip_order=False"""
        result = analyze_perishable_pattern(
            "ITEM_RICE_CAKE", "013", db_path=perishable_db,
            current_stock=0, pending_qty=0
        )
        assert result.skip_order is False
        assert result.skip_reason == ""

    @pytest.mark.db
    def test_expiry_data_source_db(self, perishable_db):
        """DB에 유통기한 있으면 expiry_data_source='db'"""
        result = analyze_perishable_pattern(
            "ITEM_RICE_CAKE", "013", db_path=perishable_db
        )
        assert result.expiry_data_source == "db"

    @pytest.mark.db
    def test_expiry_data_source_fallback(self, perishable_db):
        """DB에 유통기한 없으면 expiry_data_source='fallback'"""
        result = analyze_perishable_pattern(
            "UNKNOWN_ITEM", "013", db_path=perishable_db
        )
        assert result.expiry_data_source == "fallback"
        # fallback: 013(떡) = 3일
        assert result.expiration_days == PERISHABLE_EXPIRY_FALLBACK["013"]


# =============================================================================
# get_safety_stock_with_perishable_pattern 테스트
# =============================================================================
class TestGetSafetyStockWithPerishablePattern:
    """통합 안전재고 계산 테스트"""

    @pytest.mark.unit
    def test_non_perishable_returns_default_fallback(self):
        """소멸성 카테고리가 아니면 default 모듈로 위임 (패턴=None)"""
        safety, pattern = get_safety_stock_with_perishable_pattern(
            mid_cd="049",  # 맥주 - 소멸성 아님
            daily_avg=5.0,
            expiration_days=90,
        )
        assert pattern is None
        assert safety > 0

    @pytest.mark.unit
    def test_perishable_without_item_cd_uses_default_days(self):
        """item_cd 없이 소멸성 카테고리 호출 시 기본 안전재고일수 적용"""
        default_days = PERISHABLE_DYNAMIC_SAFETY_CONFIG["default_safety_days"]
        daily_avg = 5.0

        safety, pattern = get_safety_stock_with_perishable_pattern(
            mid_cd="013",
            daily_avg=daily_avg,
            expiration_days=3,
            item_cd=None,
        )
        assert pattern is None
        assert abs(safety - daily_avg * default_days) < 0.01


# =============================================================================
# DEFAULT_WEEKDAY_COEF 검증
# =============================================================================
class TestDefaultWeekdayCoef:
    """기본 요일별 계수 설정 테스트"""

    @pytest.mark.unit
    def test_weekday_coef_has_7_entries(self):
        """요일별 계수 딕셔너리에 7개 요일 모두 존재"""
        assert len(DEFAULT_WEEKDAY_COEF) == 7
        for day in range(7):
            assert day in DEFAULT_WEEKDAY_COEF

    @pytest.mark.unit
    def test_weekend_slightly_higher(self):
        """주말(토/일) 계수가 평일보다 소폭 높음"""
        assert DEFAULT_WEEKDAY_COEF[5] == 1.05  # 토
        assert DEFAULT_WEEKDAY_COEF[6] == 1.05  # 일

    @pytest.mark.unit
    def test_weekday_coefs_are_1_0(self):
        """평일(월~금) 계수는 1.0"""
        for day in range(5):  # 0=월 ~ 4=금
            assert DEFAULT_WEEKDAY_COEF[day] == 1.0

    @pytest.mark.unit
    def test_all_coefs_are_positive(self):
        """모든 계수가 양수"""
        for day in range(7):
            assert DEFAULT_WEEKDAY_COEF[day] > 0
