"""ML 대분류(large_cd) 슈퍼그룹 피처 테스트

- large_cd -> 슈퍼그룹 매핑 정확성
- FEATURE_NAMES 41개 검증
- build_features()에 large_cd 원핫 인코딩 검증
- 기존 피처와의 독립성 검증
- 모델 예측 호환성 검증
- data_provider/data_pipeline large_cd 포함 검증
"""

import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.prediction.ml.feature_builder import (
    LARGE_CD_SUPERGROUPS,
    MLFeatureBuilder,
    get_large_cd_supergroup,
)


# ──────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────

def _make_daily_sales(days: int = 14, avg_qty: int = 5):
    """테스트용 일별 판매 데이터 생성"""
    today = datetime.now()
    sales = []
    for i in range(days):
        date = (today - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        weekday = (today - timedelta(days=days - 1 - i)).weekday()
        variation = 1.0 + (weekday - 3) * 0.1
        qty = max(1, int(avg_qty * variation))
        sales.append({
            "sales_date": date,
            "sale_qty": qty,
            "stock_qty": avg_qty * 2,
        })
    return sales


class _MockMLModel:
    """joblib 직렬화 가능한 mock 모델"""
    def __init__(self, value=5.0):
        self.value = value

    def predict(self, X):
        return np.ones(len(X)) * self.value


# ──────────────────────────────────────────────
# 슈퍼그룹 매핑 테스트
# ──────────────────────────────────────────────

class TestLargeCdSupergroup:
    """대분류 슈퍼그룹 매핑 테스트"""

    def test_large_cd_supergroup_mapping_food(self):
        """food 슈퍼그룹 매핑: 01, 02"""
        assert get_large_cd_supergroup("01") == "lcd_food"
        assert get_large_cd_supergroup("02") == "lcd_food"

    def test_large_cd_supergroup_mapping_snack(self):
        """snack 슈퍼그룹 매핑: 11, 12, 13, 14"""
        assert get_large_cd_supergroup("11") == "lcd_snack"
        assert get_large_cd_supergroup("12") == "lcd_snack"
        assert get_large_cd_supergroup("13") == "lcd_snack"
        assert get_large_cd_supergroup("14") == "lcd_snack"

    def test_large_cd_supergroup_mapping_grocery(self):
        """grocery 슈퍼그룹 매핑: 21, 22, 23"""
        assert get_large_cd_supergroup("21") == "lcd_grocery"
        assert get_large_cd_supergroup("22") == "lcd_grocery"
        assert get_large_cd_supergroup("23") == "lcd_grocery"

    def test_large_cd_supergroup_mapping_beverage(self):
        """beverage 슈퍼그룹 매핑: 31, 33, 34"""
        assert get_large_cd_supergroup("31") == "lcd_beverage"
        assert get_large_cd_supergroup("33") == "lcd_beverage"
        assert get_large_cd_supergroup("34") == "lcd_beverage"

    def test_large_cd_supergroup_mapping_non_food(self):
        """non_food 슈퍼그룹 매핑: 41, 42, 43, 44, 45, 91"""
        assert get_large_cd_supergroup("41") == "lcd_non_food"
        assert get_large_cd_supergroup("42") == "lcd_non_food"
        assert get_large_cd_supergroup("43") == "lcd_non_food"
        assert get_large_cd_supergroup("44") == "lcd_non_food"
        assert get_large_cd_supergroup("45") == "lcd_non_food"
        assert get_large_cd_supergroup("91") == "lcd_non_food"

    def test_large_cd_unknown_returns_none(self):
        """매핑 없는 large_cd -> None"""
        assert get_large_cd_supergroup("99") is None
        assert get_large_cd_supergroup("00") is None
        assert get_large_cd_supergroup("50") is None

    def test_large_cd_none_returns_none(self):
        """None large_cd -> None"""
        assert get_large_cd_supergroup(None) is None

    def test_large_cd_empty_returns_none(self):
        """빈 문자열 large_cd -> None"""
        assert get_large_cd_supergroup("") is None

    def test_large_cd_zero_padded(self):
        """한 자리 large_cd도 zfill(2)로 패딩 처리"""
        assert get_large_cd_supergroup("1") == "lcd_food"
        assert get_large_cd_supergroup("2") == "lcd_food"

    def test_supergroups_cover_all_known_codes(self):
        """모든 슈퍼그룹 코드가 중복 없이 매핑"""
        all_codes = []
        for codes in LARGE_CD_SUPERGROUPS.values():
            all_codes.extend(codes)
        # 중복 없음
        assert len(all_codes) == len(set(all_codes))


# ──────────────────────────────────────────────
# Feature Builder 테스트
# ──────────────────────────────────────────────

class TestMLFeatureBuilderLargeCd:
    """Feature Builder 피처 테스트 (ml-improvement: 41→31, 중복 원핫 제거)"""

    def test_feature_names_count_35(self):
        """FEATURE_NAMES 길이 35개 (food-ml-dual-model: +4)"""
        assert len(MLFeatureBuilder.FEATURE_NAMES) == 45

    def test_lcd_features_removed(self):
        """대분류 슈퍼그룹 원핫 5개 제거 확인 (ml-improvement Phase C)"""
        names = MLFeatureBuilder.FEATURE_NAMES
        assert "is_lcd_food" not in names
        assert "is_lcd_snack" not in names
        assert "is_lcd_grocery" not in names
        assert "is_lcd_beverage" not in names
        assert "is_lcd_non_food" not in names

    def test_category_group_onehot_removed(self):
        """카테고리 그룹 원핫 5개 제거 확인 (ml-improvement Phase C)"""
        names = MLFeatureBuilder.FEATURE_NAMES
        assert "is_food_group" not in names
        assert "is_alcohol_group" not in names
        assert "is_tobacco_group" not in names
        assert "is_perishable_group" not in names
        assert "is_general_group" not in names

    def test_feature_array_length_35(self):
        """생성된 feature 배열 길이 35"""
        daily_sales = _make_daily_sales(14, 5)
        target_date = daily_sales[-1]["sales_date"]

        features = MLFeatureBuilder.build_features(
            daily_sales=daily_sales,
            target_date=target_date,
            mid_cd="001",
            large_cd="01",
        )
        assert features is not None
        assert len(features) == 45

    def test_retained_features_intact(self):
        """유지된 피처 정상 동작"""
        daily_sales = _make_daily_sales(14, 5)
        target_date = daily_sales[-1]["sales_date"]

        features = MLFeatureBuilder.build_features(
            daily_sales=daily_sales,
            target_date=target_date,
            mid_cd="001",
        )
        assert features is not None
        assert features[0] > 0  # daily_avg_7

    def test_large_cd_supergroup_lookup_still_works(self):
        """get_large_cd_supergroup 함수는 여전히 정상 (삭제 아님)"""
        from src.prediction.ml.feature_builder import get_large_cd_supergroup
        assert get_large_cd_supergroup("01") == "lcd_food"
        assert get_large_cd_supergroup("12") == "lcd_snack"
        assert get_large_cd_supergroup("99") is None


# ──────────────────────────────────────────────
# Batch Features 테스트
# ──────────────────────────────────────────────

class TestBatchFeaturesLargeCd:
    """build_batch_features large_cd 전달 테스트"""

    def test_build_batch_features_with_large_cd(self):
        """batch 생성 시 large_cd 전달"""
        daily_sales = _make_daily_sales(14, 5)
        target_date = daily_sales[-1]["sales_date"]

        items_data = [
            {
                "daily_sales": daily_sales,
                "target_date": target_date,
                "mid_cd": "001",
                "actual_sale_qty": 3,
                "stock_qty": 10,
                "pending_qty": 0,
                "promo_active": False,
                "item_cd": "TEST001",
                "large_cd": "01",  # food
            },
            {
                "daily_sales": daily_sales,
                "target_date": target_date,
                "mid_cd": "039",
                "actual_sale_qty": 5,
                "stock_qty": 20,
                "pending_qty": 0,
                "promo_active": False,
                "item_cd": "TEST002",
                "large_cd": "31",  # beverage
            },
        ]

        X, y, codes = MLFeatureBuilder.build_batch_features(items_data)
        assert X is not None
        assert X.shape == (2, 45)  # hourly-sales-features: 39→45


# ──────────────────────────────────────────────
# 모델 호환성 테스트
# ──────────────────────────────────────────────

class TestModelCompatLargeCd:
    """모델 호환성 테스트"""

    def test_feature_hash_changes(self):
        """FEATURE_NAMES 변경 시 hash 변경 확인"""
        from src.prediction.ml.model import MLPredictor

        # 현재 hash 계산
        current_hash = MLPredictor._feature_hash()
        assert isinstance(current_hash, str)
        assert len(current_hash) == 8  # md5 8자리

    def test_model_predict_with_35_features(self):
        """35개 피처로 모델 예측 가능 (food-ml-dual-model)"""
        from src.prediction.ml.model import MLPredictor

        predictor = MLPredictor(model_dir=tempfile.mkdtemp())
        # 모델 수동 주입
        predictor.models["food_group"] = _MockMLModel(value=7.0)

        features = np.random.rand(39).astype(np.float32)
        result = predictor.predict(features, "001")
        assert result is not None
        assert result == 7.0


# ──────────────────────────────────────────────
# Data Provider 테스트
# ──────────────────────────────────────────────

class TestDataProviderLargeCd:
    """data_provider.py large_cd 포함 테스트"""

    def test_data_provider_returns_large_cd(self):
        """get_product_info()에 large_cd 포함"""
        # 임시 DB 생성
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                item_cd TEXT PRIMARY KEY,
                item_nm TEXT,
                mid_cd TEXT,
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS product_details (
                item_cd TEXT PRIMARY KEY,
                item_nm TEXT,
                expiration_days INTEGER,
                order_unit_qty INTEGER,
                sell_price INTEGER,
                margin_rate REAL,
                lead_time_days INTEGER,
                orderable_day TEXT,
                large_cd TEXT
            )
        """)
        conn.execute("""
            INSERT INTO products (item_cd, item_nm, mid_cd, updated_at)
            VALUES ('TEST001', '테스트상품', '001', '2026-01-01')
        """)
        conn.execute("""
            INSERT INTO product_details
            (item_cd, item_nm, expiration_days, order_unit_qty, sell_price,
             margin_rate, lead_time_days, orderable_day, large_cd)
            VALUES ('TEST001', '테스트상품', 3, 1, 1000, 30.0, 1, '일월화수목금토', '01')
        """)
        conn.commit()
        conn.close()

        from src.prediction.data_provider import PredictionDataProvider
        provider = PredictionDataProvider(db_path=db_path, store_id="test")
        info = provider.get_product_info("TEST001")

        assert info is not None
        assert "large_cd" in info
        assert info["large_cd"] == "01"

        # 정리
        Path(db_path).unlink(missing_ok=True)

    def test_data_provider_returns_large_cd_null(self):
        """large_cd가 NULL인 상품도 정상 반환"""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                item_cd TEXT PRIMARY KEY, item_nm TEXT, mid_cd TEXT, updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS product_details (
                item_cd TEXT PRIMARY KEY, item_nm TEXT,
                expiration_days INTEGER, order_unit_qty INTEGER,
                sell_price INTEGER, margin_rate REAL,
                lead_time_days INTEGER, orderable_day TEXT,
                large_cd TEXT
            )
        """)
        conn.execute("""
            INSERT INTO products (item_cd, item_nm, mid_cd, updated_at)
            VALUES ('TEST002', '테스트상품2', '014', '2026-01-01')
        """)
        conn.execute("""
            INSERT INTO product_details
            (item_cd, item_nm, expiration_days, order_unit_qty, sell_price,
             margin_rate, lead_time_days, orderable_day, large_cd)
            VALUES ('TEST002', '테스트상품2', 365, 1, 2000, 25.0, 1, '일월화수목금토', NULL)
        """)
        conn.commit()
        conn.close()

        from src.prediction.data_provider import PredictionDataProvider
        provider = PredictionDataProvider(db_path=db_path, store_id="test")
        info = provider.get_product_info("TEST002")

        assert info is not None
        assert "large_cd" in info
        assert info["large_cd"] is None

        Path(db_path).unlink(missing_ok=True)


# ──────────────────────────────────────────────
# Data Pipeline 테스트
# ──────────────────────────────────────────────

class TestDataPipelineLargeCd:
    """data_pipeline.py large_cd 포함 테스트"""

    def test_data_pipeline_meta_includes_large_cd(self):
        """get_items_meta()에 large_cd 포함"""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        conn = sqlite3.connect(db_path)
        # 통합 DB 모드: products + product_details + daily_sales 모두 같은 DB
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                item_cd TEXT PRIMARY KEY, item_nm TEXT, mid_cd TEXT, updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS product_details (
                item_cd TEXT PRIMARY KEY, item_nm TEXT,
                expiration_days INTEGER, margin_rate REAL, large_cd TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_sales (
                item_cd TEXT, sales_date TEXT, sale_qty INTEGER,
                stock_qty INTEGER, buy_qty INTEGER, mid_cd TEXT,
                store_id TEXT, disuse_qty INTEGER DEFAULT 0,
                PRIMARY KEY (item_cd, sales_date)
            )
        """)
        conn.execute("""
            INSERT INTO products VALUES ('ITEM001', '테스트', '001', '2026-01-01')
        """)
        conn.execute("""
            INSERT INTO product_details (item_cd, item_nm, expiration_days, margin_rate, large_cd)
            VALUES ('ITEM001', '테스트', 3, 30.0, '01')
        """)
        conn.commit()
        conn.close()

        from src.prediction.ml.data_pipeline import MLDataPipeline
        pipeline = MLDataPipeline(db_path=db_path)
        meta = pipeline.get_items_meta(["ITEM001"])

        assert "ITEM001" in meta
        assert "large_cd" in meta["ITEM001"]
        assert meta["ITEM001"]["large_cd"] == "01"

        Path(db_path).unlink(missing_ok=True)

    def test_data_pipeline_meta_large_cd_null(self):
        """product_details에 large_cd가 NULL인 경우"""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                item_cd TEXT PRIMARY KEY, item_nm TEXT, mid_cd TEXT, updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS product_details (
                item_cd TEXT PRIMARY KEY, item_nm TEXT,
                expiration_days INTEGER, margin_rate REAL, large_cd TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_sales (
                item_cd TEXT, sales_date TEXT, sale_qty INTEGER,
                stock_qty INTEGER, buy_qty INTEGER, mid_cd TEXT,
                store_id TEXT, disuse_qty INTEGER DEFAULT 0,
                PRIMARY KEY (item_cd, sales_date)
            )
        """)
        conn.execute("""
            INSERT INTO products VALUES ('ITEM002', '테스트2', '014', '2026-01-01')
        """)
        conn.execute("""
            INSERT INTO product_details (item_cd, item_nm, expiration_days, margin_rate, large_cd)
            VALUES ('ITEM002', '테스트2', 365, 25.0, NULL)
        """)
        conn.commit()
        conn.close()

        from src.prediction.ml.data_pipeline import MLDataPipeline
        pipeline = MLDataPipeline(db_path=db_path)
        meta = pipeline.get_items_meta(["ITEM002"])

        assert "ITEM002" in meta
        assert meta["ITEM002"]["large_cd"] is None

        Path(db_path).unlink(missing_ok=True)
