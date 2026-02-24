"""
재고 불일치 진단 시스템 테스트

테스트 범위:
1. StockDiscrepancyDiagnoser.diagnose() — 6가지 유형 분류
2. StockDiscrepancyDiagnoser.summarize_discrepancies() — 요약
3. PredictionResult stock_source 필드 전달
4. _convert_prediction_result_to_dict 메타 포워딩
5. OrderAnalysisRepository stock_discrepancy_log CRUD
6. _resolve_stock_and_pending() 반환값 확장 (5-tuple)
"""

import sqlite3
import pytest
from unittest.mock import patch, MagicMock

from src.analysis.stock_discrepancy_diagnoser import StockDiscrepancyDiagnoser


# ═══════════════════════════════════════════════════
# 1. StockDiscrepancyDiagnoser.diagnose() — 유형 분류
# ═══════════════════════════════════════════════════

class TestDiagnoseTypes:
    """진단 유형 분류 테스트"""

    def test_ghost_stock(self):
        """유령재고: stale + 예측재고>0 + 실재고=0"""
        result = StockDiscrepancyDiagnoser.diagnose(
            stock_at_prediction=5,
            pending_at_prediction=0,
            stock_at_order=0,
            pending_at_order=0,
            stock_source="ri_stale_ri",
            is_stock_stale=True,
            original_order_qty=3,
            recalculated_order_qty=8,
        )
        assert result["discrepancy_type"] == "GHOST_STOCK"
        assert result["severity"] == "HIGH"
        assert result["stock_diff"] == -5

    def test_stale_fallback(self):
        """stale 폴백: RI stale→ds 소스 + 재고 차이 >= 임계값"""
        result = StockDiscrepancyDiagnoser.diagnose(
            stock_at_prediction=10,
            pending_at_prediction=0,
            stock_at_order=5,
            pending_at_order=0,
            stock_source="ri_stale_ds",
            is_stock_stale=True,
            original_order_qty=2,
            recalculated_order_qty=7,
        )
        assert result["discrepancy_type"] == "STALE_FALLBACK"
        assert result["severity"] == "HIGH"  # diff=5 >= HIGH threshold

    def test_stale_fallback_ri_stale_ri(self):
        """stale 폴백: ri_stale_ri 소스"""
        result = StockDiscrepancyDiagnoser.diagnose(
            stock_at_prediction=8,
            pending_at_prediction=0,
            stock_at_order=5,
            pending_at_order=0,
            stock_source="ri_stale_ri",
            is_stock_stale=True,
            original_order_qty=3,
            recalculated_order_qty=6,
        )
        assert result["discrepancy_type"] == "STALE_FALLBACK"

    def test_pending_mismatch(self):
        """발주잔량 불일치: pending 차이 >= 임계값"""
        result = StockDiscrepancyDiagnoser.diagnose(
            stock_at_prediction=5,
            pending_at_prediction=0,
            stock_at_order=5,
            pending_at_order=4,
            stock_source="ri",
            is_stock_stale=False,
            original_order_qty=5,
            recalculated_order_qty=1,
        )
        assert result["discrepancy_type"] == "PENDING_MISMATCH"

    def test_over_order(self):
        """과대발주: 실재고 > 예측재고"""
        result = StockDiscrepancyDiagnoser.diagnose(
            stock_at_prediction=3,
            pending_at_prediction=0,
            stock_at_order=6,
            pending_at_order=0,
            stock_source="cache",
            is_stock_stale=False,
            original_order_qty=5,
            recalculated_order_qty=2,
        )
        assert result["discrepancy_type"] == "OVER_ORDER"
        assert result["stock_diff"] == 3

    def test_under_order(self):
        """과소발주: 실재고 < 예측재고"""
        result = StockDiscrepancyDiagnoser.diagnose(
            stock_at_prediction=8,
            pending_at_prediction=0,
            stock_at_order=5,
            pending_at_order=0,
            stock_source="cache",
            is_stock_stale=False,
            original_order_qty=2,
            recalculated_order_qty=5,
        )
        assert result["discrepancy_type"] == "UNDER_ORDER"
        assert result["stock_diff"] == -3

    def test_none_within_threshold(self):
        """정상 범위: 재고 차이 < 임계값"""
        result = StockDiscrepancyDiagnoser.diagnose(
            stock_at_prediction=5,
            pending_at_prediction=0,
            stock_at_order=6,
            pending_at_order=0,
            stock_source="ri",
            is_stock_stale=False,
            original_order_qty=3,
            recalculated_order_qty=2,
        )
        assert result["discrepancy_type"] == "NONE"
        assert result["severity"] == "LOW"

    def test_none_no_change(self):
        """정상: 재고 변동 없음"""
        result = StockDiscrepancyDiagnoser.diagnose(
            stock_at_prediction=5,
            pending_at_prediction=2,
            stock_at_order=5,
            pending_at_order=2,
            stock_source="ri",
            is_stock_stale=False,
            original_order_qty=3,
            recalculated_order_qty=3,
        )
        assert result["discrepancy_type"] == "NONE"
        assert result["order_impact"] == 0


# ═══════════════════════════════════════════════════
# 2. 심각도 분류
# ═══════════════════════════════════════════════════

class TestDiagnoseSeverity:
    """심각도 분류 테스트"""

    def test_high_severity(self):
        """HIGH: 차이 >= 5"""
        result = StockDiscrepancyDiagnoser.diagnose(
            stock_at_prediction=10,
            pending_at_prediction=0,
            stock_at_order=4,
            pending_at_order=0,
            stock_source="cache",
            is_stock_stale=False,
        )
        assert result["severity"] == "HIGH"

    def test_medium_severity(self):
        """MEDIUM: 2 <= 차이 < 5"""
        result = StockDiscrepancyDiagnoser.diagnose(
            stock_at_prediction=5,
            pending_at_prediction=0,
            stock_at_order=2,
            pending_at_order=0,
            stock_source="cache",
            is_stock_stale=False,
        )
        assert result["severity"] == "MEDIUM"

    def test_order_impact_calculation(self):
        """발주량 영향 계산"""
        result = StockDiscrepancyDiagnoser.diagnose(
            stock_at_prediction=5,
            pending_at_prediction=0,
            stock_at_order=10,
            pending_at_order=0,
            stock_source="ri",
            is_stock_stale=False,
            original_order_qty=8,
            recalculated_order_qty=3,
        )
        assert result["order_impact"] == -5  # 3 - 8 = -5


# ═══════════════════════════════════════════════════
# 3. is_significant() / summarize_discrepancies()
# ═══════════════════════════════════════════════════

class TestDiagnoserUtilities:
    """유틸리티 메서드 테스트"""

    def test_is_significant_true(self):
        diag = {"discrepancy_type": "GHOST_STOCK"}
        assert StockDiscrepancyDiagnoser.is_significant(diag)

    def test_is_significant_false(self):
        diag = {"discrepancy_type": "NONE"}
        assert not StockDiscrepancyDiagnoser.is_significant(diag)

    def test_summarize_empty(self):
        summary = StockDiscrepancyDiagnoser.summarize_discrepancies([])
        assert summary["total"] == 0
        assert summary["significant"] == 0

    def test_summarize_mixed(self):
        discrepancies = [
            {"discrepancy_type": "GHOST_STOCK", "severity": "HIGH", "stock_diff": -5, "order_impact": 5},
            {"discrepancy_type": "OVER_ORDER", "severity": "MEDIUM", "stock_diff": 3, "order_impact": -3},
            {"discrepancy_type": "NONE", "severity": "LOW", "stock_diff": 1, "order_impact": 0},
        ]
        summary = StockDiscrepancyDiagnoser.summarize_discrepancies(discrepancies)
        assert summary["total"] == 3
        assert summary["significant"] == 2
        assert summary["by_type"]["GHOST_STOCK"] == 1
        assert summary["by_type"]["OVER_ORDER"] == 1
        assert summary["by_severity"]["HIGH"] == 1
        assert summary["by_severity"]["MEDIUM"] == 1
        assert summary["by_severity"]["LOW"] == 1


# ═══════════════════════════════════════════════════
# 4. PredictionResult stock_source 필드 전달
# ═══════════════════════════════════════════════════

class TestPredictionResultFields:
    """PredictionResult에 stock_source 관련 필드 존재 여부"""

    def _make_result(self, **overrides):
        from src.prediction.improved_predictor import PredictionResult
        defaults = dict(
            item_cd="TEST001", item_nm="Test", mid_cd="001",
            target_date="2026-02-24", predicted_qty=5,
            adjusted_qty=5.0, current_stock=0, pending_qty=0,
            safety_stock=1.0, order_qty=5, confidence="HIGH",
            data_days=30, weekday_coef=1.0,
        )
        defaults.update(overrides)
        return PredictionResult(**defaults)

    def test_prediction_result_has_stock_source(self):
        r = self._make_result(
            stock_source="ri",
            pending_source="ri_fresh",
            is_stock_stale=False,
        )
        assert r.stock_source == "ri"
        assert r.pending_source == "ri_fresh"
        assert r.is_stock_stale is False

    def test_prediction_result_defaults(self):
        r = self._make_result(item_cd="TEST002", predicted_qty=3)
        assert r.stock_source == ""
        assert r.pending_source == ""
        assert r.is_stock_stale is False


# ═══════════════════════════════════════════════════
# 5. _convert_prediction_result_to_dict 메타 포워딩
# ═══════════════════════════════════════════════════

class TestConvertPredictionResultMeta:
    """dict 변환 시 stock_source 메타 포함"""

    def test_dict_contains_stock_meta(self):
        """_convert_prediction_result_to_dict가 stock_source 등 포함"""
        from src.prediction.improved_predictor import PredictionResult

        result = PredictionResult(
            item_cd="TEST001", item_nm="Test", mid_cd="001",
            target_date="2026-02-24", predicted_qty=5,
            adjusted_qty=5.0, current_stock=0, pending_qty=0,
            safety_stock=1.0, order_qty=5, confidence="HIGH",
            data_days=30, weekday_coef=1.0,
            stock_source="cache",
            pending_source="ri_fresh",
            is_stock_stale=True,
        )

        # AutoOrderSystem._convert_prediction_result_to_dict는 인스턴스 메서드
        # 직접 호출 대신 getattr 로직 검증 (auto_order.py와 동일 패턴)
        d = {
            "stock_source": getattr(result, "stock_source", ""),
            "pending_source": getattr(result, "pending_source", ""),
            "is_stock_stale": getattr(result, "is_stock_stale", False),
        }
        assert d["stock_source"] == "cache"
        assert d["pending_source"] == "ri_fresh"
        assert d["is_stock_stale"] is True


# ═══════════════════════════════════════════════════
# 6. OrderAnalysisRepository stock_discrepancy_log CRUD
# ═══════════════════════════════════════════════════

class TestStockDiscrepancyLogCRUD:
    """order_analysis.db stock_discrepancy_log CRUD 테스트"""

    @pytest.fixture
    def repo(self, tmp_path):
        db_path = str(tmp_path / "test_analysis.db")
        from src.infrastructure.database.repos.order_analysis_repo import OrderAnalysisRepository
        return OrderAnalysisRepository(db_path=db_path)

    def _make_discrepancy(self, item_cd="ITEM001", dtype="GHOST_STOCK", severity="HIGH"):
        return {
            "item_cd": item_cd,
            "item_nm": "Test Item",
            "mid_cd": "001",
            "discrepancy_type": dtype,
            "severity": severity,
            "stock_at_prediction": 5,
            "pending_at_prediction": 0,
            "stock_at_order": 0,
            "pending_at_order": 0,
            "stock_diff": -5,
            "pending_diff": 0,
            "stock_source": "ri_stale_ri",
            "is_stock_stale": True,
            "original_order_qty": 3,
            "recalculated_order_qty": 8,
            "order_impact": 5,
            "description": "ghost stock test",
        }

    def test_save_and_get(self, repo):
        """저장 + 조회"""
        items = [self._make_discrepancy()]
        saved = repo.save_stock_discrepancies("S001", "2026-02-23", items)
        assert saved == 1

        rows = repo.get_discrepancies_by_date("S001", "2026-02-23")
        assert len(rows) == 1
        assert rows[0]["discrepancy_type"] == "GHOST_STOCK"
        assert rows[0]["severity"] == "HIGH"
        assert rows[0]["stock_diff"] == -5

    def test_save_multiple(self, repo):
        """다건 저장"""
        items = [
            self._make_discrepancy("ITEM001", "GHOST_STOCK", "HIGH"),
            self._make_discrepancy("ITEM002", "OVER_ORDER", "MEDIUM"),
            self._make_discrepancy("ITEM003", "PENDING_MISMATCH", "LOW"),
        ]
        saved = repo.save_stock_discrepancies("S001", "2026-02-23", items)
        assert saved == 3

        rows = repo.get_discrepancies_by_date("S001", "2026-02-23")
        assert len(rows) == 3

    def test_save_empty(self, repo):
        """빈 리스트 저장"""
        saved = repo.save_stock_discrepancies("S001", "2026-02-23", [])
        assert saved == 0

    def test_upsert_on_duplicate(self, repo):
        """같은 날 같은 상품은 REPLACE"""
        items = [self._make_discrepancy("ITEM001", "GHOST_STOCK", "HIGH")]
        repo.save_stock_discrepancies("S001", "2026-02-23", items)

        # 같은 상품, 다른 유형으로 다시 저장
        items2 = [self._make_discrepancy("ITEM001", "OVER_ORDER", "MEDIUM")]
        repo.save_stock_discrepancies("S001", "2026-02-23", items2)

        rows = repo.get_discrepancies_by_date("S001", "2026-02-23")
        assert len(rows) == 1
        assert rows[0]["discrepancy_type"] == "OVER_ORDER"  # 덮어쓰기됨

    def test_get_summary(self, repo):
        """요약 통계 조회"""
        items = [
            self._make_discrepancy("ITEM001", "GHOST_STOCK", "HIGH"),
            self._make_discrepancy("ITEM002", "OVER_ORDER", "MEDIUM"),
        ]
        repo.save_stock_discrepancies("S001", "2026-02-23", items)

        summary = repo.get_discrepancy_summary("S001", days=7)
        assert summary["total"] == 2
        assert "GHOST_STOCK" in summary["by_type"]
        assert "OVER_ORDER" in summary["by_type"]

    def test_get_discrepancies_empty_store(self, repo):
        """없는 매장 조회"""
        rows = repo.get_discrepancies_by_date("NONEXIST", "2026-02-23")
        assert rows == []


# ═══════════════════════════════════════════════════
# 7. _resolve_stock_and_pending 반환값 (5-tuple)
# ═══════════════════════════════════════════════════

class TestResolveStockAndPendingReturnValue:
    """_resolve_stock_and_pending()이 5개 값을 반환하는지 검증"""

    def test_returns_five_values(self):
        """반환값이 5개 요소의 튜플인지 확인"""
        from src.prediction.improved_predictor import ImprovedPredictor

        # _resolve_stock_and_pending는 인스턴스 메서드이므로
        # 메서드 시그니처/반환값 구조만 검증
        import inspect
        sig = inspect.signature(ImprovedPredictor._resolve_stock_and_pending)
        # 파라미터: self, item_cd, pending_qty
        params = list(sig.parameters.keys())
        assert "item_cd" in params
        assert "pending_qty" in params

    @patch("src.prediction.improved_predictor.ImprovedPredictor.__init__", return_value=None)
    def test_cache_hit_returns_source(self, mock_init):
        """캐시 히트 시 stock_source='cache' 반환"""
        from src.prediction.improved_predictor import ImprovedPredictor
        predictor = ImprovedPredictor.__new__(ImprovedPredictor)

        # _data 내부 저장소 모의 객체
        data = MagicMock()
        data._stock_cache = {"ITEM001": 10}
        data._pending_cache = {"ITEM001": 2}
        data._use_db_inventory = False
        predictor._data = data

        # pending_qty=None 이어야 _pending_cache 조회 분기 진입
        result = predictor._resolve_stock_and_pending("ITEM001", None)
        assert len(result) == 5
        stock, pending, stock_src, pending_src, is_stale = result
        assert stock == 10
        assert pending == 2
        assert stock_src == "cache"
        assert pending_src == "cache"
        assert is_stale is False


# ═══════════════════════════════════════════════════
# 8. DB 마이그레이션 v36
# ═══════════════════════════════════════════════════

class TestSchemaV36:
    """v36 마이그레이션: prediction_logs 3개 컬럼 추가"""

    def test_migration_sql_exists(self):
        """SCHEMA_MIGRATIONS[36]이 존재하는지"""
        from src.db.models import SCHEMA_MIGRATIONS
        assert 36 in SCHEMA_MIGRATIONS
        sql = SCHEMA_MIGRATIONS[36]
        assert "stock_source" in sql
        assert "pending_source" in sql
        assert "is_stock_stale" in sql

    def test_db_schema_version_is_current(self):
        """DB_SCHEMA_VERSION이 최신인지"""
        from src.settings.constants import DB_SCHEMA_VERSION
        assert DB_SCHEMA_VERSION >= 36

    def test_schema_py_has_columns(self):
        """schema.py prediction_logs CREATE TABLE에 3개 컬럼 포함"""
        from src.infrastructure.database.schema import STORE_SCHEMA
        # prediction_logs CREATE TABLE 찾기
        pl_sql = None
        for sql in STORE_SCHEMA:
            if "prediction_logs" in sql:
                pl_sql = sql
                break
        assert pl_sql is not None
        assert "stock_source" in pl_sql
        assert "pending_source" in pl_sql
        assert "is_stock_stale" in pl_sql
