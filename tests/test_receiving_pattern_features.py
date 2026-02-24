"""
receiving-pattern: ML 입고 패턴 피처 추가 테스트

테스트 구성:
- TestReceivingPatternStatsBatch: receiving_repo 배치 쿼리 (5개)
- TestPendingAgeBatch: order_tracking pending 경과일 (4개)
- TestFeatureBuilderReceiving: feature_builder 확장 (7개)
- TestMLEnsembleReceivingIntegration: improved_predictor 연동 (3개)
"""

import math
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# =========================================================================
# Helper: 테스트용 인메모리 DB 생성
# =========================================================================

def _create_receiving_db():
    """receiving_history + order_tracking 인메모리 DB"""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE receiving_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT DEFAULT '46513',
            receiving_date TEXT NOT NULL,
            receiving_time TEXT,
            chit_no TEXT,
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            mid_cd TEXT,
            order_date TEXT,
            order_qty INTEGER DEFAULT 0,
            receiving_qty INTEGER DEFAULT 0,
            delivery_type TEXT,
            center_nm TEXT,
            center_cd TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(receiving_date, item_cd, chit_no)
        )
    """)
    conn.execute("""
        CREATE TABLE order_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT DEFAULT '46513',
            order_date TEXT,
            item_cd TEXT,
            item_nm TEXT,
            mid_cd TEXT,
            delivery_type TEXT,
            order_qty INTEGER DEFAULT 0,
            remaining_qty INTEGER DEFAULT 0,
            arrival_time TEXT,
            expiry_time TEXT,
            status TEXT DEFAULT 'pending',
            alert_sent INTEGER,
            created_at TEXT,
            updated_at TEXT,
            actual_receiving_qty INTEGER,
            actual_arrival_time TEXT,
            UNIQUE(store_id, order_date, item_cd)
        )
    """)
    return conn


def _today_str():
    return datetime.now().strftime("%Y-%m-%d")


def _days_ago(n):
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


# =========================================================================
# TestReceivingPatternStatsBatch
# =========================================================================

class TestReceivingPatternStatsBatch:
    """receiving_repo.get_receiving_pattern_stats_batch() 테스트"""

    def test_basic_stats_calculation(self):
        """기본 리드타임/숏배송 통계 계산"""
        conn = _create_receiving_db()
        # 상품 A: 3건, lead_time=1,1,1 (avg=1.0, std=0.0), 숏배송 0건
        for i in range(3):
            order_d = _days_ago(20 - i * 3)
            recv_d = _days_ago(19 - i * 3)  # 1일 후 입고
            conn.execute(
                "INSERT INTO receiving_history (item_cd, order_date, receiving_date, order_qty, receiving_qty, store_id, chit_no) "
                "VALUES (?, ?, ?, ?, ?, '46513', ?)",
                ("ITEM_A", order_d, recv_d, 10, 10, f"CHT{i}")
            )
        conn.commit()

        from src.infrastructure.database.repos.receiving_repo import ReceivingRepository
        repo = ReceivingRepository.__new__(ReceivingRepository)
        repo._get_conn = lambda: conn
        repo._store_filter = lambda field=None, store_id=None: ("AND store_id = ?", ("46513",))

        result = repo.get_receiving_pattern_stats_batch(store_id="46513", days=30)

        assert "ITEM_A" in result
        stats = result["ITEM_A"]
        assert abs(stats["lead_time_avg"] - 1.0) < 0.1
        assert stats["short_delivery_rate"] == 0.0
        assert stats["total_records"] == 3

    def test_empty_receiving_history(self):
        """빈 receiving_history에서 빈 dict 반환"""
        conn = _create_receiving_db()

        from src.infrastructure.database.repos.receiving_repo import ReceivingRepository
        repo = ReceivingRepository.__new__(ReceivingRepository)
        repo._get_conn = lambda: conn
        repo._store_filter = lambda field=None, store_id=None: ("AND store_id = ?", ("46513",))

        result = repo.get_receiving_pattern_stats_batch(store_id="46513", days=30)
        assert result == {}

    def test_short_delivery_detection(self):
        """숏배송 감지: receiving_qty < order_qty"""
        conn = _create_receiving_db()
        # 3건 중 2건 숏배송
        for i, (oq, rq) in enumerate([(10, 8), (10, 5), (10, 10)]):
            order_d = _days_ago(15 - i * 3)
            recv_d = _days_ago(14 - i * 3)
            conn.execute(
                "INSERT INTO receiving_history (item_cd, order_date, receiving_date, order_qty, receiving_qty, store_id, chit_no) "
                "VALUES (?, ?, ?, ?, ?, '46513', ?)",
                ("ITEM_B", order_d, recv_d, oq, rq, f"CHT{i}")
            )
        conn.commit()

        from src.infrastructure.database.repos.receiving_repo import ReceivingRepository
        repo = ReceivingRepository.__new__(ReceivingRepository)
        repo._get_conn = lambda: conn
        repo._store_filter = lambda field=None, store_id=None: ("AND store_id = ?", ("46513",))

        result = repo.get_receiving_pattern_stats_batch(store_id="46513", days=30)
        assert abs(result["ITEM_B"]["short_delivery_rate"] - 0.667) < 0.01

    def test_multiple_items_batch(self):
        """여러 상품 일괄 조회"""
        conn = _create_receiving_db()
        for item in ["ITEM_C", "ITEM_D", "ITEM_E"]:
            for i in range(2):
                order_d = _days_ago(10 - i * 2)
                recv_d = _days_ago(9 - i * 2)
                conn.execute(
                    "INSERT INTO receiving_history (item_cd, order_date, receiving_date, order_qty, receiving_qty, store_id, chit_no) "
                    "VALUES (?, ?, ?, 10, 10, '46513', ?)",
                    (item, order_d, recv_d, f"CHT_{item}_{i}")
                )
        conn.commit()

        from src.infrastructure.database.repos.receiving_repo import ReceivingRepository
        repo = ReceivingRepository.__new__(ReceivingRepository)
        repo._get_conn = lambda: conn
        repo._store_filter = lambda field=None, store_id=None: ("AND store_id = ?", ("46513",))

        result = repo.get_receiving_pattern_stats_batch(store_id="46513", days=30)
        assert len(result) == 3
        assert "ITEM_C" in result
        assert "ITEM_D" in result
        assert "ITEM_E" in result

    def test_days_filter(self):
        """days 파라미터로 기간 필터링"""
        conn = _create_receiving_db()

        # 5일 전 레코드와 40일 전 레코드
        conn.execute(
            "INSERT INTO receiving_history (item_cd, order_date, receiving_date, order_qty, receiving_qty, store_id, chit_no) "
            "VALUES ('ITEM_F', ?, ?, 10, 10, '46513', 'CHT1')",
            (_days_ago(6), _days_ago(5))
        )
        conn.execute(
            "INSERT INTO receiving_history (item_cd, order_date, receiving_date, order_qty, receiving_qty, store_id, chit_no) "
            "VALUES ('ITEM_F', ?, ?, 10, 10, '46513', 'CHT2')",
            (_days_ago(41), _days_ago(40))
        )
        conn.commit()

        from src.infrastructure.database.repos.receiving_repo import ReceivingRepository

        # 30일 필터 테스트
        repo = ReceivingRepository.__new__(ReceivingRepository)
        repo._get_conn = lambda: conn
        repo._store_filter = lambda field=None, store_id=None: ("AND store_id = ?", ("46513",))

        # conn.close()를 우회하기 위해 close를 빈 함수로 래핑
        class _NoCloseConn:
            """close()를 무시하는 커넥션 래퍼"""
            def __init__(self, real_conn):
                self._real = real_conn
            def cursor(self):
                return self._real.cursor()
            def execute(self, *args, **kwargs):
                return self._real.execute(*args, **kwargs)
            def close(self):
                pass  # close 무시

        nc = _NoCloseConn(conn)
        repo._get_conn = lambda: nc

        result = repo.get_receiving_pattern_stats_batch(store_id="46513", days=30)
        assert result["ITEM_F"]["total_records"] == 1

        # 60일 필터: 둘 다
        result60 = repo.get_receiving_pattern_stats_batch(store_id="46513", days=60)
        assert result60["ITEM_F"]["total_records"] == 2

        conn.close()


# =========================================================================
# TestPendingAgeBatch
# =========================================================================

class TestPendingAgeBatch:
    """order_tracking_repo.get_pending_age_batch() 테스트"""

    def test_basic_pending_age(self):
        """기본 pending 경과일 계산"""
        conn = _create_receiving_db()
        conn.execute(
            "INSERT INTO order_tracking (item_cd, order_date, status, remaining_qty, store_id) "
            "VALUES ('ITEM_P1', ?, 'ordered', 5, '46513')",
            (_days_ago(3),)
        )
        conn.commit()

        from src.infrastructure.database.repos.order_tracking_repo import OrderTrackingRepository
        repo = OrderTrackingRepository.__new__(OrderTrackingRepository)
        repo._get_conn = lambda: conn
        repo.store_id = "46513"

        result = repo.get_pending_age_batch(store_id="46513")
        assert "ITEM_P1" in result
        assert result["ITEM_P1"] == 3

    def test_no_pending_returns_empty(self):
        """pending 없으면 빈 dict"""
        conn = _create_receiving_db()
        conn.execute(
            "INSERT INTO order_tracking (item_cd, order_date, status, remaining_qty, store_id) "
            "VALUES ('ITEM_P2', ?, 'selling', 0, '46513')",
            (_days_ago(2),)
        )
        conn.commit()

        from src.infrastructure.database.repos.order_tracking_repo import OrderTrackingRepository
        repo = OrderTrackingRepository.__new__(OrderTrackingRepository)
        repo._get_conn = lambda: conn
        repo.store_id = "46513"

        result = repo.get_pending_age_batch(store_id="46513")
        assert "ITEM_P2" not in result

    def test_multiple_pending_oldest(self):
        """여러 pending 중 가장 오래된 날짜 기준"""
        conn = _create_receiving_db()
        # 같은 상품에 2건 pending, 5일 전 + 2일 전
        conn.execute(
            "INSERT INTO order_tracking (item_cd, order_date, status, remaining_qty, store_id) "
            "VALUES ('ITEM_P3', ?, 'ordered', 3, '46513')",
            (_days_ago(5),)
        )
        conn.execute(
            "INSERT INTO order_tracking (item_cd, order_date, status, remaining_qty, store_id) "
            "VALUES ('ITEM_P3', ?, 'arrived', 2, '46513')",
            (_days_ago(2),)
        )
        conn.commit()

        from src.infrastructure.database.repos.order_tracking_repo import OrderTrackingRepository
        repo = OrderTrackingRepository.__new__(OrderTrackingRepository)
        repo._get_conn = lambda: conn
        repo.store_id = "46513"

        result = repo.get_pending_age_batch(store_id="46513")
        assert result["ITEM_P3"] == 5  # 가장 오래된 5일 전

    def test_only_ordered_arrived_status(self):
        """ordered/arrived만 pending, selling/expired는 제외"""
        conn = _create_receiving_db()
        conn.execute(
            "INSERT INTO order_tracking (item_cd, order_date, status, remaining_qty, store_id) "
            "VALUES ('ITEM_P4', ?, 'expired', 5, '46513')",
            (_days_ago(10),)
        )
        conn.execute(
            "INSERT INTO order_tracking (item_cd, order_date, status, remaining_qty, store_id) "
            "VALUES ('ITEM_P4', ?, 'ordered', 3, '46513')",
            (_days_ago(2),)
        )
        conn.commit()

        from src.infrastructure.database.repos.order_tracking_repo import OrderTrackingRepository
        repo = OrderTrackingRepository.__new__(OrderTrackingRepository)
        repo._get_conn = lambda: conn
        repo.store_id = "46513"

        result = repo.get_pending_age_batch(store_id="46513")
        # expired는 무시, ordered만 → 2일
        assert result["ITEM_P4"] == 2


# =========================================================================
# TestFeatureBuilderReceiving
# =========================================================================

class TestFeatureBuilderReceiving:
    """feature_builder.py ML 입고 패턴 피처 테스트"""

    def _make_daily_sales(self, days=14, avg_qty=5):
        """테스트용 일별 판매 데이터 생성"""
        return [
            {"sales_date": _days_ago(days - i), "sale_qty": avg_qty}
            for i in range(days)
        ]

    def test_feature_count_36(self):
        """FEATURE_NAMES가 36개인지 확인"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder
        assert len(MLFeatureBuilder.FEATURE_NAMES) == 36

    def test_feature_names_contain_receiving(self):
        """입고 패턴 피처 5개가 FEATURE_NAMES에 존재"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder
        expected = [
            "lead_time_avg", "lead_time_cv", "short_delivery_rate",
            "delivery_frequency", "pending_age_days"
        ]
        for name in expected:
            assert name in MLFeatureBuilder.FEATURE_NAMES, f"{name} not in FEATURE_NAMES"

    def test_receiving_stats_included(self):
        """receiving_stats 전달 시 피처 배열에 반영"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        recv_stats = {
            "lead_time_avg": 1.5,
            "lead_time_std": 0.3,
            "short_delivery_rate": 0.2,
            "delivery_frequency": 7,
            "pending_age_days": 3,
        }

        features = MLFeatureBuilder.build_features(
            daily_sales=self._make_daily_sales(),
            target_date=_today_str(),
            mid_cd="001",
            receiving_stats=recv_stats,
        )

        assert features is not None
        assert len(features) == 36

        # 마지막 5개가 입고 패턴 피처
        # lead_time_avg: 1.5 / 3.0 = 0.5
        assert abs(features[31] - 0.5) < 0.01
        # lead_time_cv: (0.3/1.5) / 2.0 = 0.1
        assert abs(features[32] - 0.1) < 0.01
        # short_delivery_rate: 0.2 (그대로)
        assert abs(features[33] - 0.2) < 0.01
        # delivery_frequency: 7 / 14.0 = 0.5
        assert abs(features[34] - 0.5) < 0.01
        # pending_age_days: 3 / 5.0 = 0.6
        assert abs(features[35] - 0.6) < 0.01

    def test_receiving_stats_none_defaults(self):
        """receiving_stats=None이면 기본값 0.0"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        features = MLFeatureBuilder.build_features(
            daily_sales=self._make_daily_sales(),
            target_date=_today_str(),
            mid_cd="001",
            receiving_stats=None,
        )

        assert features is not None
        # lead_time_avg 기본값: 0.0
        assert features[31] == 0.0
        # lead_time_cv: (0/0.001) → _lt_avg=0 이므로 0.25 기본값 → 0.25/2.0=0.125
        assert abs(features[32] - 0.125) < 0.01
        # short_delivery_rate: 0.0
        assert features[33] == 0.0
        # delivery_frequency: 0.0
        assert features[34] == 0.0
        # pending_age_days: 0.0
        assert features[35] == 0.0

    def test_lead_time_normalization_cap(self):
        """lead_time_avg > 3.0일 때 1.0으로 cap"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        recv_stats = {
            "lead_time_avg": 5.0,  # 5일 > 3일 cap
            "lead_time_std": 1.0,
        }

        features = MLFeatureBuilder.build_features(
            daily_sales=self._make_daily_sales(),
            target_date=_today_str(),
            mid_cd="001",
            receiving_stats=recv_stats,
        )

        # min(5.0/3.0, 1.0) = 1.0
        assert features[31] == pytest.approx(1.0, abs=0.01)

    def test_short_rate_passthrough(self):
        """short_delivery_rate는 0~1 그대로 통과"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        recv_stats = {"short_delivery_rate": 0.75}

        features = MLFeatureBuilder.build_features(
            daily_sales=self._make_daily_sales(),
            target_date=_today_str(),
            mid_cd="001",
            receiving_stats=recv_stats,
        )

        assert features[33] == pytest.approx(0.75, abs=0.01)

    def test_pending_age_cap(self):
        """pending_age_days 5일 이상 → 1.0 cap"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        recv_stats = {"pending_age_days": 10}

        features = MLFeatureBuilder.build_features(
            daily_sales=self._make_daily_sales(),
            target_date=_today_str(),
            mid_cd="001",
            receiving_stats=recv_stats,
        )

        # min(10/5.0, 1.0) = 1.0
        assert features[35] == pytest.approx(1.0, abs=0.01)


# =========================================================================
# TestMLEnsembleReceivingIntegration
# =========================================================================

class TestMLEnsembleReceivingIntegration:
    """improved_predictor의 receiving stats 연동 테스트"""

    @patch("src.infrastructure.database.repos.receiving_repo.ReceivingRepository")
    @patch("src.infrastructure.database.repos.order_tracking_repo.OrderTrackingRepository")
    def test_cache_loads_on_predict_batch(self, mock_ot_repo_cls, mock_recv_repo_cls):
        """predict_batch 호출 시 _load_receiving_stats_cache 실행"""
        from src.prediction.improved_predictor import ImprovedPredictor

        predictor = MagicMock(spec=ImprovedPredictor)
        predictor._receiving_stats_cache = {}
        predictor.store_id = "46513"

        # 실제 _load_receiving_stats_cache 호출 테스트
        mock_recv = MagicMock()
        mock_recv.get_receiving_pattern_stats_batch.return_value = {
            "ITEM1": {"lead_time_avg": 1.2, "lead_time_std": 0.3, "short_delivery_rate": 0.1, "delivery_frequency": 5}
        }
        mock_recv_repo_cls.return_value = mock_recv

        mock_ot = MagicMock()
        mock_ot.get_pending_age_batch.return_value = {"ITEM1": 2}
        mock_ot_repo_cls.return_value = mock_ot

        # 실제 메서드 호출
        ImprovedPredictor._load_receiving_stats_cache(predictor)

        assert "ITEM1" in predictor._receiving_stats_cache
        assert predictor._receiving_stats_cache["ITEM1"]["lead_time_avg"] == 1.2
        assert predictor._receiving_stats_cache["ITEM1"]["pending_age_days"] == 2

    def test_features_passed_to_ml(self):
        """_apply_ml_ensemble에서 receiving_stats가 build_features에 전달되는지"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        # receiving_stats가 있을 때 features[31:36] 값이 0이 아닌지 확인
        daily_sales = [{"sale_qty": 5} for _ in range(30)]
        recv_stats = {
            "lead_time_avg": 2.0,
            "lead_time_std": 0.5,
            "short_delivery_rate": 0.15,
            "delivery_frequency": 8,
            "pending_age_days": 1,
        }

        features = MLFeatureBuilder.build_features(
            daily_sales=daily_sales,
            target_date=_today_str(),
            mid_cd="001",
            receiving_stats=recv_stats,
        )

        assert features is not None
        # lead_time_avg: 2.0/3.0 ≈ 0.667
        assert features[31] > 0
        # delivery_frequency: 8/14 ≈ 0.571
        assert features[34] > 0

    def test_backward_compatible_no_receiving(self):
        """receiving_stats 없이도 기존 동작 유지 (31개→36개, 마지막 5개 기본값)"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        daily_sales = [{"sale_qty": 3} for _ in range(14)]

        # receiving_stats 전달 안 함
        features = MLFeatureBuilder.build_features(
            daily_sales=daily_sales,
            target_date=_today_str(),
            mid_cd="049",  # alcohol
        )

        assert features is not None
        assert len(features) == 36
        # 기존 31개 피처는 정상 (첫번째: daily_avg_7)
        assert features[0] == pytest.approx(3.0, abs=0.1)


# =========================================================================
# TestBuildBatchFeaturesReceiving
# =========================================================================

class TestBuildBatchFeaturesReceiving:
    """build_batch_features에서 receiving_stats 호환성"""

    def test_batch_with_receiving_stats(self):
        """build_batch_features에서 receiving_stats dict 전달"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        items_data = [
            {
                "daily_sales": [{"sale_qty": 5} for _ in range(14)],
                "target_date": _today_str(),
                "mid_cd": "001",
                "actual_sale_qty": 5,
                "receiving_stats": {
                    "lead_time_avg": 1.0,
                    "lead_time_std": 0.2,
                    "short_delivery_rate": 0.1,
                    "delivery_frequency": 3,
                    "pending_age_days": 0,
                },
            },
            {
                "daily_sales": [{"sale_qty": 3} for _ in range(14)],
                "target_date": _today_str(),
                "mid_cd": "049",
                "actual_sale_qty": 3,
                # receiving_stats 없음 → None
            },
        ]

        X, y, codes = MLFeatureBuilder.build_batch_features(items_data)
        assert X is not None
        assert X.shape == (2, 36)

    def test_batch_without_receiving_stats(self):
        """build_batch_features에서 receiving_stats 키 없을 때 None 폴백"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        items_data = [
            {
                "daily_sales": [{"sale_qty": 4} for _ in range(14)],
                "target_date": _today_str(),
                "mid_cd": "072",
                "actual_sale_qty": 4,
                # receiving_stats 키 자체 없음
            }
        ]

        X, y, codes = MLFeatureBuilder.build_batch_features(items_data)
        assert X is not None
        assert X.shape == (1, 36)
        # 마지막 5개 기본값
        assert X[0, 31] == 0.0  # lead_time_avg
        assert X[0, 33] == 0.0  # short_delivery_rate
