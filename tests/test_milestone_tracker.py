"""마일스톤 트래커 단위 테스트"""

import sqlite3
import pytest
from unittest.mock import patch, MagicMock

from src.analysis.milestone_tracker import MilestoneTracker


# ── _judge 판정 ──

class TestJudge:
    """_judge 메서드 테스트"""

    def setup_method(self):
        self.tracker = MilestoneTracker()

    def test_achieved_when_value_equals_target(self):
        assert self.tracker._judge(1.05, 1.05) == "ACHIEVED"

    def test_achieved_when_value_below_target(self):
        assert self.tracker._judge(0.98, 1.05) == "ACHIEVED"

    def test_approaching_when_value_slightly_above(self):
        # 1.05 * 1.2 = 1.26, 값이 1.20이면 APPROACHING
        assert self.tracker._judge(1.20, 1.05) == "APPROACHING"

    def test_not_met_when_value_far_above(self):
        # 1.05 * 1.2 = 1.26, 값이 1.30이면 NOT_MET
        assert self.tracker._judge(1.30, 1.05) == "NOT_MET"

    def test_none_value_returns_no_data(self):
        assert self.tracker._judge(None, 1.05) == "NO_DATA"

    def test_zero_target_achieved(self):
        assert self.tracker._judge(0, 3) == "ACHIEVED"


# ── K1 예측 안정성 ──

class TestCalculateK1:

    def setup_method(self):
        self.tracker = MilestoneTracker()

    def test_k1_stable_predictions(self):
        """mae_7d/mae_14d 비율이 낮으면 ACHIEVED"""
        metrics = {
            "store1": {
                "prediction_accuracy": {
                    "categories": [
                        {"mid_cd": "001", "mae_7d": 1.0, "mae_14d": 1.0},  # ratio=1.0
                        {"mid_cd": "010", "mae_7d": 0.8, "mae_14d": 0.9},  # ratio=0.89
                    ]
                }
            }
        }
        result = self.tracker._calculate_k1(metrics)
        assert result["status"] == "ACHIEVED"
        assert result["value"] < 1.05

    def test_k1_unstable_predictions(self):
        """mae_7d/mae_14d 비율이 높으면 NOT_MET"""
        metrics = {
            "store1": {
                "prediction_accuracy": {
                    "categories": [
                        {"mid_cd": "001", "mae_7d": 2.0, "mae_14d": 1.0},  # ratio=2.0
                    ]
                }
            }
        }
        result = self.tracker._calculate_k1(metrics)
        assert result["status"] == "NOT_MET"

    def test_k1_insufficient_data(self):
        """데이터 부족 시 NO_DATA"""
        metrics = {
            "store1": {
                "prediction_accuracy": {"insufficient_data": True}
            }
        }
        result = self.tracker._calculate_k1(metrics)
        assert result["status"] == "NO_DATA"

    def test_k1_zero_mae_14d_skipped(self):
        """mae_14d가 0이면 해당 카테고�� 스킵"""
        metrics = {
            "store1": {
                "prediction_accuracy": {
                    "categories": [
                        {"mid_cd": "001", "mae_7d": 1.0, "mae_14d": 0},
                    ]
                }
            }
        }
        result = self.tracker._calculate_k1(metrics)
        assert result["status"] == "NO_DATA"


# ── K2 폐기율 ──

class TestCalculateK2:

    def setup_method(self):
        self.tracker = MilestoneTracker()

    def test_k2_low_waste(self):
        """폐기율이 낮으면 ACHIEVED"""
        metrics = {
            "store1": {
                "waste_rate": {
                    "categories": [
                        {"mid_cd": "001", "rate_30d": 0.02},  # food 2%
                        {"mid_cd": "010", "rate_30d": 0.01},  # 비food 1%
                    ]
                }
            }
        }
        result = self.tracker._calculate_k2(metrics)
        assert result["status"] == "ACHIEVED"

    def test_k2_high_food_waste(self):
        """food 폐기율이 높으면 NOT_MET"""
        metrics = {
            "store1": {
                "waste_rate": {
                    "categories": [
                        {"mid_cd": "001", "rate_30d": 0.10},  # food 10%
                        {"mid_cd": "010", "rate_30d": 0.01},
                    ]
                }
            }
        }
        result = self.tracker._calculate_k2(metrics)
        assert result["status"] != "ACHIEVED"


# ── K3 발주 실패율 ──

class TestCalculateK3:

    def setup_method(self):
        self.tracker = MilestoneTracker()

    def test_k3_low_failure(self):
        """실패율 5% 미만 → ACHIEVED"""
        metrics = {
            "store1": {
                "order_failure": {
                    "recent_7d": 2, "prev_7d": 3, "total_order_7d": 100
                }
            }
        }
        result = self.tracker._calculate_k3(metrics)
        assert result["status"] == "ACHIEVED"
        assert result["value"] == 0.02

    def test_k3_high_failure(self):
        """실패율 10% → NOT_MET"""
        metrics = {
            "store1": {
                "order_failure": {
                    "recent_7d": 10, "prev_7d": 5, "total_order_7d": 100
                }
            }
        }
        result = self.tracker._calculate_k3(metrics)
        assert result["status"] != "ACHIEVED"

    def test_k3_no_orders(self):
        """총 발주 0건 → NO_DATA"""
        metrics = {
            "store1": {
                "order_failure": {
                    "recent_7d": 0, "prev_7d": 0, "total_order_7d": 0
                }
            }
        }
        result = self.tracker._calculate_k3(metrics)
        assert result["status"] == "NO_DATA"

    def test_k3_multi_store(self):
        """다중 매장 합산"""
        metrics = {
            "store1": {
                "order_failure": {
                    "recent_7d": 1, "prev_7d": 2, "total_order_7d": 50
                }
            },
            "store2": {
                "order_failure": {
                    "recent_7d": 1, "prev_7d": 1, "total_order_7d": 50
                }
            },
        }
        result = self.tracker._calculate_k3(metrics)
        # 2/100 = 0.02 → ACHIEVED
        assert result["value"] == 0.02
        assert result["status"] == "ACHIEVED"


# ── K4 무결성 ──

class TestCalculateK4:

    def setup_method(self):
        self.tracker = MilestoneTracker()

    def test_k4_clean(self):
        """연속 이상 0일 → ACHIEVED"""
        metrics = {
            "store1": {
                "integrity_unresolved": {
                    "checks": [
                        {"name": "ghost_stock", "consecutive_days": 0},
                        {"name": "expired_batch", "consecutive_days": 1},
                    ]
                }
            }
        }
        result = self.tracker._calculate_k4(metrics)
        assert result["value"] == 1
        assert result["status"] == "ACHIEVED"

    def test_k4_long_anomaly(self):
        """연속 5일 이상 → NOT_MET"""
        metrics = {
            "store1": {
                "integrity_unresolved": {
                    "checks": [
                        {"name": "ghost_stock", "consecutive_days": 5},
                    ]
                }
            }
        }
        result = self.tracker._calculate_k4(metrics)
        assert result["value"] == 5
        assert result["status"] == "NOT_MET"


# ── 리포트 메시지 ──

class TestBuildReportMessage:

    def setup_method(self):
        self.tracker = MilestoneTracker()

    def test_report_contains_all_kpis(self):
        kpis = {
            "K1": {"value": 1.03, "target": 1.05, "status": "ACHIEVED"},
            "K2": {"food": 0.025, "total": 0.015, "food_target": 0.03,
                   "total_target": 0.02, "status": "ACHIEVED"},
            "K3": {"value": 0.03, "target": 0.05, "status": "ACHIEVED"},
            "K4": {"value": 1, "target": 3, "status": "ACHIEVED"},
        }
        msg = self.tracker._build_report_message(kpis, 2)
        assert "마일스톤 주간 리포트" in msg
        assert "K1" in msg
        assert "K2" in msg
        assert "K3" in msg
        assert "K4" in msg
        assert "달성: 4/4" in msg

    def test_completion_message(self):
        """전체 달성 + 연속 2주 → 완료 메시지"""
        kpis = {
            "K1": {"value": 1.03, "target": 1.05, "status": "ACHIEVED"},
            "K2": {"food": 0.025, "total": 0.015, "food_target": 0.03,
                   "total_target": 0.02, "status": "ACHIEVED"},
            "K3": {"value": 0.03, "target": 0.05, "status": "ACHIEVED"},
            "K4": {"value": 1, "target": 3, "status": "ACHIEVED"},
        }
        msg = self.tracker._build_report_message(kpis, 2)
        assert "완료" in msg
        assert "4단계" in msg

    def test_suggestion_when_not_achieved(self):
        """미달성 시 다음 작업 제안"""
        kpis = {
            "K1": {"value": 1.15, "target": 1.05, "status": "APPROACHING"},
            "K2": {"food": 0.025, "total": 0.015, "food_target": 0.03,
                   "total_target": 0.02, "status": "ACHIEVED"},
            "K3": {"value": 0.03, "target": 0.05, "status": "ACHIEVED"},
            "K4": {"value": 1, "target": 3, "status": "ACHIEVED"},
        }
        msg = self.tracker._build_report_message(kpis, 0)
        assert "다음:" in msg
        assert "is_payday" in msg  # K1 미달성 → is_payday 제안


# ── 연속 달성 주 (G-1, G-2) ──

class TestConsecutiveWeeks:
    """_count_consecutive_achieved 테스트"""

    def _make_db(self, rows):
        """in-memory DB에 milestone_snapshots 테스트 데이터 생성"""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE milestone_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            stage TEXT NOT NULL DEFAULT '3',
            k1_value REAL, k1_status TEXT,
            k2_food_value REAL, k2_total_value REAL, k2_status TEXT,
            k3_value REAL, k3_status TEXT,
            k4_value REAL, k4_status TEXT,
            all_achieved INTEGER DEFAULT 0,
            consecutive_weeks INTEGER DEFAULT 0,
            created_at TEXT,
            UNIQUE(snapshot_date, stage)
        )""")
        for date_str, achieved in rows:
            conn.execute(
                "INSERT INTO milestone_snapshots (snapshot_date, stage, all_achieved) VALUES (?, '3', ?)",
                (date_str, 1 if achieved else 0),
            )
        conn.commit()
        return conn

    def test_consecutive_weeks_two(self):
        """2주 연속 전체 달성 → 완료 조건 충족"""
        tracker = MilestoneTracker()
        conn = self._make_db([
            ("2026-04-06", True),
            ("2026-04-13", True),
        ])
        result = tracker._count_consecutive_achieved(conn=conn)
        assert result == 2
        conn.close()

    def test_consecutive_weeks_broken(self):
        """중간에 실패 → 연속 카운트 0으로 리셋"""
        tracker = MilestoneTracker()
        conn = self._make_db([
            ("2026-03-30", True),
            ("2026-04-06", False),   # 실패로 연속 끊김
            ("2026-04-13", True),
        ])
        # 최근부터 역순: 04-13(True) → 04-06(False) → stop
        result = tracker._count_consecutive_achieved(conn=conn)
        assert result == 1  # 04-13 하나만 연속
        conn.close()

    def test_consecutive_weeks_zero(self):
        """최근이 미달성 → 0"""
        tracker = MilestoneTracker()
        conn = self._make_db([
            ("2026-04-06", True),
            ("2026-04-13", False),
        ])
        result = tracker._count_consecutive_achieved(conn=conn)
        assert result == 0
        conn.close()

    def test_consecutive_weeks_empty(self):
        """데이터 없음 → 0"""
        tracker = MilestoneTracker()
        conn = self._make_db([])
        result = tracker._count_consecutive_achieved(conn=conn)
        assert result == 0
        conn.close()


# ── 통합 테스트 (G-3, G-4) ──

class TestIntegration:

    @patch("src.analysis.milestone_tracker.StoreContext")
    @patch("src.analysis.milestone_tracker.OpsMetrics")
    @patch("src.analysis.milestone_tracker.DBRouter")
    def test_evaluate_full_flow(self, mock_router, mock_ops, mock_ctx):
        """collect → calculate → save → report 전체 흐름"""
        # Mock: 1매장 반환
        mock_store = MagicMock()
        mock_store.store_id = "46513"
        mock_ctx.get_all_active.return_value = [mock_store]

        # Mock: OpsMetrics 반환
        mock_ops.return_value.collect_all.return_value = {
            "prediction_accuracy": {
                "categories": [{"mid_cd": "001", "mae_7d": 1.0, "mae_14d": 1.0}]
            },
            "order_failure": {"recent_7d": 1, "prev_7d": 2, "total_order_7d": 50},
            "waste_rate": {
                "categories": [
                    {"mid_cd": "001", "rate_30d": 0.02},
                    {"mid_cd": "010", "rate_30d": 0.01},
                ]
            },
            "integrity_unresolved": {
                "checks": [{"name": "ghost_stock", "consecutive_days": 0}]
            },
            "collection_failure": {"types": [{"type": "sales", "consecutive_fails": 0}]},
        }

        # Mock: common DB connection (in-memory)
        mock_conn = sqlite3.connect(":memory:")
        mock_conn.row_factory = sqlite3.Row
        mock_conn.execute("""CREATE TABLE milestone_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT, stage TEXT DEFAULT '3',
            k1_value REAL, k1_status TEXT,
            k2_food_value REAL, k2_total_value REAL, k2_status TEXT,
            k3_value REAL, k3_status TEXT,
            k4_value REAL, k4_status TEXT,
            all_achieved INTEGER DEFAULT 0,
            consecutive_weeks INTEGER DEFAULT 0,
            created_at TEXT,
            UNIQUE(snapshot_date, stage)
        )""")
        mock_router.get_common_connection.return_value = mock_conn

        tracker = MilestoneTracker()
        result = tracker.evaluate()

        # 결과 검증
        assert "kpis" in result
        assert "report_message" in result
        assert result["achieved_count"] >= 0
        assert "마일스톤 주간 리포트" in result["report_message"]

    @patch("src.analysis.milestone_tracker.StoreContext")
    @patch("src.analysis.milestone_tracker.OpsMetrics")
    @patch("src.analysis.milestone_tracker.DBRouter")
    def test_snapshot_saved_to_db(self, mock_router, mock_ops, mock_ctx):
        """evaluate 후 milestone_snapshots에 행이 저장되는지 확인"""
        mock_store = MagicMock()
        mock_store.store_id = "46513"
        mock_ctx.get_all_active.return_value = [mock_store]

        mock_ops.return_value.collect_all.return_value = {
            "prediction_accuracy": {
                "categories": [{"mid_cd": "001", "mae_7d": 1.0, "mae_14d": 1.0}]
            },
            "order_failure": {"recent_7d": 1, "prev_7d": 2, "total_order_7d": 50},
            "waste_rate": {
                "categories": [{"mid_cd": "001", "rate_30d": 0.02}]
            },
            "integrity_unresolved": {
                "checks": [{"name": "ghost_stock", "consecutive_days": 0}]
            },
            "collection_failure": {"types": []},
        }

        # MagicMock으로 감싸서 close()를 무시, 실제 DB 연산은 위임
        real_conn = sqlite3.connect(":memory:")
        real_conn.row_factory = sqlite3.Row
        real_conn.execute("""CREATE TABLE milestone_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT, stage TEXT DEFAULT '3',
            k1_value REAL, k1_status TEXT,
            k2_food_value REAL, k2_total_value REAL, k2_status TEXT,
            k3_value REAL, k3_status TEXT,
            k4_value REAL, k4_status TEXT,
            all_achieved INTEGER DEFAULT 0,
            consecutive_weeks INTEGER DEFAULT 0,
            created_at TEXT,
            UNIQUE(snapshot_date, stage)
        )""")
        wrapper = MagicMock(wraps=real_conn)
        wrapper.close = MagicMock()  # close()를 no-op으로
        mock_router.get_common_connection.return_value = wrapper

        tracker = MilestoneTracker()
        tracker.evaluate()

        # DB에 행이 저장되었는지 확인
        row = real_conn.execute(
            "SELECT * FROM milestone_snapshots WHERE stage = '3'"
        ).fetchone()
        assert row is not None
        assert row["k1_status"] in ("ACHIEVED", "APPROACHING", "NOT_MET", "NO_DATA")
        real_conn.close()
