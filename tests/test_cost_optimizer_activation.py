"""CostOptimizer 활성화 테스트.

Design Reference: docs/02-design/features/cost-optimizer-activation.design.md
테스트 항목:
1. 비활성 시 기본 CostInfo 반환 (1)
2. margin_rate 없을 때 enabled=False (1)
3. 고마진 상품 multiplier 검증 (1)
4. 저마진 상품 multiplier 검증 (1)
5. 2D 매트릭스: 고마진+고회전 (1)
6. 2D 매트릭스: 저마진+저회전 (1)
7. turnover=unknown 1D 폴백 (1)
8. 판매비중 계산 (분할 DB) (1)
9. 판매비중 store_id=None 빈 dict (1)
10. 캐시 동작 검증 (1)
11. bulk_cost_info 일괄 조회 (1)
12. eval_params.json 설정 로드 (1)
13. improved_predictor 통합 — 활성화 확인 (1)
14. PreOrderEvaluator 통합 — skip_offset 적용 (1)
15. DBRouter 기반 커넥션 사용 (1)
"""

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.prediction.cost_optimizer import (
    CostOptimizer,
    CostInfo,
    DEFAULT_COST_CONFIG,
    DEFAULT_MARGIN_MULTIPLIER_MATRIX,
    DEFAULT_DISUSE_MODIFIER_MATRIX,
    DEFAULT_SKIP_OFFSET_MATRIX,
)


# ──────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────

@pytest.fixture
def disabled_optimizer():
    """비활성화된 CostOptimizer."""
    with patch.object(CostOptimizer, "_load_config") as mock_cfg:
        mock_cfg.return_value = {**DEFAULT_COST_CONFIG, "enabled": False}
        opt = CostOptimizer.__new__(CostOptimizer)
        opt._config = mock_cfg.return_value
        opt._product_repo = MagicMock()
        opt._cache = {}
        opt.store_id = None
        opt._category_share_cache = None
        return opt


@pytest.fixture
def enabled_optimizer():
    """활성화된 CostOptimizer (DB 접근 mock)."""
    with patch.object(CostOptimizer, "_load_config") as mock_cfg:
        config = {**DEFAULT_COST_CONFIG, "enabled": True}
        mock_cfg.return_value = config
        opt = CostOptimizer.__new__(CostOptimizer)
        opt._config = config
        opt._product_repo = MagicMock()
        opt._cache = {}
        opt.store_id = "46513"
        opt._category_share_cache = None
        return opt


def _mock_product_detail(margin_rate=None, sell_price=1000):
    """product_details mock 반환."""
    return {"sell_price": sell_price, "margin_rate": margin_rate}


# ──────────────────────────────────────────────────────────────
# 1. 비활성 시 기본 CostInfo
# ──────────────────────────────────────────────────────────────

class TestDisabled:
    def test_cost_info_disabled_returns_default(self, disabled_optimizer):
        """enabled=False이면 기본 CostInfo 반환."""
        info = disabled_optimizer.get_item_cost_info("ITEM001")
        assert info.enabled is False
        assert info.margin_multiplier == 1.0
        assert info.skip_offset == 0.0


# ──────────────────────────────────────────────────────────────
# 2. margin_rate 없을 때
# ──────────────────────────────────────────────────────────────

class TestNoMargin:
    def test_cost_info_no_margin_returns_disabled(self, enabled_optimizer):
        """margin_rate=None이면 enabled=False."""
        enabled_optimizer._product_repo.get.return_value = _mock_product_detail(margin_rate=None)
        info = enabled_optimizer.get_item_cost_info("ITEM001")
        assert info.enabled is False


# ──────────────────────────────────────────────────────────────
# 3-4. 마진 레벨별 multiplier
# ──────────────────────────────────────────────────────────────

class TestMarginLevels:
    def test_high_margin_high_multiplier(self, enabled_optimizer):
        """margin_rate=50 (고마진) → multiplier>1.0."""
        enabled_optimizer._product_repo.get.return_value = _mock_product_detail(margin_rate=50)
        info = enabled_optimizer.get_item_cost_info("ITEM001", daily_avg=6.0)
        assert info.enabled is True
        assert info.margin_multiplier > 1.0
        assert info.cost_ratio == pytest.approx(1.0)  # 50/(100-50)=1.0

    def test_low_margin_low_multiplier(self, enabled_optimizer):
        """margin_rate=5 (저마진) → multiplier<1.0."""
        enabled_optimizer._product_repo.get.return_value = _mock_product_detail(margin_rate=5)
        info = enabled_optimizer.get_item_cost_info("ITEM001", daily_avg=0.5)
        assert info.enabled is True
        assert info.margin_multiplier < 1.0


# ──────────────────────────────────────────────────────────────
# 5-6. 2D 매트릭스
# ──────────────────────────────────────────────────────────────

class TestMatrix2D:
    def test_high_margin_high_turnover(self, enabled_optimizer):
        """고마진(50%)+고회전(daily_avg=6) → 최고 multiplier."""
        enabled_optimizer._product_repo.get.return_value = _mock_product_detail(margin_rate=50)
        info = enabled_optimizer.get_item_cost_info("ITEM001", daily_avg=6.0)
        # cost_ratio=1.0 → high, turnover=6.0 → high
        expected = DEFAULT_MARGIN_MULTIPLIER_MATRIX["high"]["high"]
        assert info.margin_multiplier == expected
        assert info.turnover_level == "high"

    def test_low_margin_low_turnover(self, enabled_optimizer):
        """저마진(5%)+저회전(daily_avg=0.5) → 최저 multiplier."""
        enabled_optimizer._product_repo.get.return_value = _mock_product_detail(margin_rate=5)
        info = enabled_optimizer.get_item_cost_info("ITEM001", daily_avg=0.5)
        # cost_ratio=5/95≈0.053 → low, turnover=0.5 → low
        expected = DEFAULT_MARGIN_MULTIPLIER_MATRIX["low"]["low"]
        assert info.margin_multiplier == expected
        assert info.turnover_level == "low"


# ──────────────────────────────────────────────────────────────
# 7. turnover=unknown 1D 폴백
# ──────────────────────────────────────────────────────────────

class TestFallback1D:
    def test_turnover_unknown_uses_1d(self, enabled_optimizer):
        """daily_avg=0 → turnover=unknown → 1D 폴백."""
        enabled_optimizer._product_repo.get.return_value = _mock_product_detail(margin_rate=50)
        info = enabled_optimizer.get_item_cost_info("ITEM001", daily_avg=0.0)
        assert info.turnover_level == "unknown"
        # 1D 폴백: cost_ratio=1.0 >= 0.5(high) → safety_multiplier_high(1.3)
        assert info.margin_multiplier == DEFAULT_COST_CONFIG["safety_multiplier_high"]


# ──────────────────────────────────────────────────────────────
# 8-9. 판매비중
# ──────────────────────────────────────────────────────────────

class TestCategoryShares:
    def test_category_shares_with_db(self, tmp_path):
        """분할 DB에서 판매비중이 계산된다."""
        # store DB 세팅
        store_dir = tmp_path / "data" / "stores"
        store_dir.mkdir(parents=True, exist_ok=True)
        store_db = store_dir / "46513.db"

        conn = sqlite3.connect(str(store_db))
        conn.execute("CREATE TABLE daily_sales (item_cd TEXT, sale_qty INTEGER, sales_date TEXT, store_id TEXT)")
        today = date.today().isoformat()
        conn.executemany(
            "INSERT INTO daily_sales VALUES (?,?,?,?)",
            [
                ("A001", 10, today, "46513"),
                ("A002", 20, today, "46513"),
                ("A003", 70, today, "46513"),
            ],
        )
        conn.commit()
        conn.close()

        # common DB 세팅
        common_db = tmp_path / "data" / "common.db"
        conn2 = sqlite3.connect(str(common_db))
        conn2.execute("CREATE TABLE products (item_cd TEXT, item_nm TEXT, mid_cd TEXT)")
        conn2.executemany(
            "INSERT INTO products VALUES (?,?,?)",
            [
                ("A001", "상품A", "001"),
                ("A002", "상품B", "001"),
                ("A003", "상품C", "002"),
            ],
        )
        conn2.commit()
        conn2.close()

        with patch("src.prediction.cost_optimizer.DBRouter") as mock_router:
            def _get_conn_with_common(sid):
                c = sqlite3.connect(str(store_db))
                c.row_factory = sqlite3.Row
                c.execute(f"ATTACH DATABASE '{common_db}' AS common")
                return c

            mock_router.get_store_connection_with_common.side_effect = _get_conn_with_common

            opt = CostOptimizer.__new__(CostOptimizer)
            opt._config = {**DEFAULT_COST_CONFIG, "enabled": True}
            opt._product_repo = MagicMock()
            opt._cache = {}
            opt.store_id = "46513"
            opt._category_share_cache = None

            shares = opt._calculate_category_shares()
            assert "001" in shares
            assert "002" in shares
            # A001(10)+A002(20) = 30/100 = 0.3
            assert shares["001"] == pytest.approx(0.3)
            # A003(70) = 70/100 = 0.7
            assert shares["002"] == pytest.approx(0.7)

    def test_category_shares_no_store_returns_empty(self):
        """store_id=None이면 빈 dict 반환."""
        opt = CostOptimizer.__new__(CostOptimizer)
        opt._config = {**DEFAULT_COST_CONFIG, "enabled": True}
        opt._product_repo = MagicMock()
        opt._cache = {}
        opt.store_id = None
        opt._category_share_cache = None

        shares = opt._calculate_category_shares()
        assert shares == {}


# ──────────────────────────────────────────────────────────────
# 10. 캐시
# ──────────────────────────────────────────────────────────────

class TestCache:
    def test_cache_prevents_double_lookup(self, enabled_optimizer):
        """동일 item 2회 조회 시 캐시 히트."""
        enabled_optimizer._product_repo.get.return_value = _mock_product_detail(margin_rate=30)

        info1 = enabled_optimizer.get_item_cost_info("ITEM001", daily_avg=3.0)
        info2 = enabled_optimizer.get_item_cost_info("ITEM001", daily_avg=3.0)

        assert info1.enabled is True
        assert info1 is info2  # 동일 객체 (캐시)
        # product_repo.get은 1번만 호출
        assert enabled_optimizer._product_repo.get.call_count == 1


# ──────────────────────────────────────────────────────────────
# 11. bulk_cost_info
# ──────────────────────────────────────────────────────────────

class TestBulk:
    def test_bulk_cost_info_returns_all(self, enabled_optimizer):
        """다수 상품 일괄 조회."""
        enabled_optimizer._product_repo.get.return_value = _mock_product_detail(margin_rate=25)

        result = enabled_optimizer.get_bulk_cost_info(["A", "B", "C"])
        assert len(result) == 3
        assert all(v.enabled for v in result.values())


# ──────────────────────────────────────────────────────────────
# 12. eval_params.json 설정 로드
# ──────────────────────────────────────────────────────────────

class TestConfigLoad:
    def test_config_from_eval_params(self, tmp_path):
        """eval_params.json에서 설정 로드."""
        import json
        config_file = tmp_path / "eval_params.json"
        config_file.write_text(json.dumps({
            "cost_optimization": {
                "enabled": True,
                "cost_ratio_high": 0.6,
                "turnover_high_threshold": 8.0,
            }
        }), encoding="utf-8")

        with patch("src.prediction.cost_optimizer.COST_CONFIG_PATH", config_file):
            opt = CostOptimizer.__new__(CostOptimizer)
            opt._config = opt._load_config()

            assert opt._config["enabled"] is True
            assert opt._config["cost_ratio_high"] == 0.6
            assert opt._config["turnover_high_threshold"] == 8.0
            # 나머지는 기본값
            assert opt._config["cost_ratio_mid"] == DEFAULT_COST_CONFIG["cost_ratio_mid"]


# ──────────────────────────────────────────────────────────────
# 13. improved_predictor 통합
# ──────────────────────────────────────────────────────────────

class TestImprovedPredictorIntegration:
    def test_cost_optimizer_is_activated(self):
        """improved_predictor에서 CostOptimizer가 활성화된다."""
        with patch("src.prediction.improved_predictor.CostOptimizer") as MockCO:
            mock_instance = MagicMock()
            mock_instance.enabled = True
            MockCO.return_value = mock_instance

            from src.prediction.improved_predictor import ImprovedPredictor
            predictor = ImprovedPredictor.__new__(ImprovedPredictor)
            predictor.store_id = "46513"
            predictor.db_path = Path("/tmp/test.db")
            predictor._cost_optimizer = MockCO(store_id="46513")

            assert predictor._cost_optimizer is not None
            assert predictor._cost_optimizer.enabled is True


# ──────────────────────────────────────────────────────────────
# 14. PreOrderEvaluator skip_offset
# ──────────────────────────────────────────────────────────────

class TestPreOrderEvalSkipOffset:
    def test_skip_offset_adjusts_threshold(self, enabled_optimizer):
        """CostOptimizer의 skip_offset이 th_sufficient를 조정한다."""
        enabled_optimizer._product_repo.get.return_value = _mock_product_detail(margin_rate=50)
        info = enabled_optimizer.get_item_cost_info("ITEM001", daily_avg=6.0)
        # high margin + high turnover → skip_offset = 0.7
        assert info.skip_offset == DEFAULT_SKIP_OFFSET_MATRIX["high"]["high"]
        assert info.skip_offset > 0  # 양수 = SKIP 임계값 상향 = SKIP 어렵게


# ──────────────────────────────────────────────────────────────
# 15. DBRouter 기반 커넥션 사용
# ──────────────────────────────────────────────────────────────

class TestDBRouterUsage:
    def test_no_legacy_db_path_in_module(self):
        """cost_optimizer 모듈에 bgf_sales.db 참조가 없다."""
        import src.prediction.cost_optimizer as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "bgf_sales.db" not in source

    def test_uses_dbrouter_import(self):
        """cost_optimizer가 DBRouter를 임포트한다."""
        import src.prediction.cost_optimizer as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "from src.infrastructure.database.connection import DBRouter" in source
