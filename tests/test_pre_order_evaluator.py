"""PreOrderEvaluator 사전 발주 평가 테스트

FORCE/URGENT/NORMAL/PASS/SKIP 판정 로직 검증
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.prediction.pre_order_evaluator import (
    EvalDecision,
    PreOrderEvalResult,
    PreOrderEvaluator,
    DECISION_LABELS,
)
from src.prediction.eval_config import EvalConfig


@pytest.fixture
def eval_db(tmp_path):
    """평가기 테스트용 DB"""
    db_file = tmp_path / "test_eval.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE products (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            mid_cd TEXT
        );

        CREATE TABLE daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT NOT NULL,
            sales_date TEXT NOT NULL,
            sale_qty INTEGER DEFAULT 0,
            stock_qty INTEGER DEFAULT 0,
            mid_cd TEXT,
            ord_qty INTEGER DEFAULT 0,
            buy_qty INTEGER DEFAULT 0,
            UNIQUE(item_cd, sales_date)
        );

        CREATE TABLE realtime_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT NOT NULL,
            check_date TEXT NOT NULL,
            stock_qty INTEGER DEFAULT 0,
            pending_qty INTEGER DEFAULT 0,
            is_available INTEGER DEFAULT 1,
            UNIQUE(item_cd, check_date)
        );

        CREATE TABLE product_details (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            mid_cd TEXT,
            order_unit_qty INTEGER DEFAULT 1,
            lead_time_days REAL,
            orderable_day TEXT DEFAULT '일월화수목금토'
        );

        CREATE TABLE receiving_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT,
            order_date TEXT,
            receiving_date TEXT,
            order_qty INTEGER DEFAULT 0,
            received_qty INTEGER DEFAULT 0,
            store_id TEXT DEFAULT '46513',
            chit_no TEXT,
            UNIQUE(store_id, receiving_date, item_cd, chit_no)
        );
    """)

    conn.commit()
    return str(db_file), conn


def _insert_product(conn, item_cd, item_nm, mid_cd):
    """상품 등록 헬퍼"""
    conn.execute(
        "INSERT OR REPLACE INTO products (item_cd, item_nm, mid_cd) VALUES (?, ?, ?)",
        (item_cd, item_nm, mid_cd),
    )


def _insert_sales(conn, item_cd, mid_cd, days=30, avg_qty=5, stock_qty=10):
    """판매 데이터 삽입 헬퍼"""
    today = datetime.now()
    for d in range(days):
        date = (today - timedelta(days=d)).strftime("%Y-%m-%d")
        conn.execute(
            "INSERT OR REPLACE INTO daily_sales "
            "(item_cd, sales_date, sale_qty, stock_qty, mid_cd) "
            "VALUES (?, ?, ?, ?, ?)",
            (item_cd, date, avg_qty, stock_qty, mid_cd),
        )


def _insert_inventory(conn, item_cd, stock_qty, pending_qty=0):
    """실시간 재고 삽입 헬퍼"""
    check_date = datetime.now().strftime("%Y-%m-%d")
    conn.execute(
        "INSERT OR REPLACE INTO realtime_inventory "
        "(item_cd, check_date, stock_qty, pending_qty, is_available) "
        "VALUES (?, ?, ?, ?, 1)",
        (item_cd, check_date, stock_qty, pending_qty),
    )


class TestEvalDecision:
    """EvalDecision Enum 테스트"""

    def test_decision_values(self):
        """5가지 결정 값 확인"""
        assert EvalDecision.FORCE_ORDER.value == "FORCE_ORDER"
        assert EvalDecision.URGENT_ORDER.value == "URGENT_ORDER"
        assert EvalDecision.NORMAL_ORDER.value == "NORMAL_ORDER"
        assert EvalDecision.PASS.value == "PASS"
        assert EvalDecision.SKIP.value == "SKIP"

    def test_decision_labels(self):
        """결정별 한글 라벨 존재"""
        for decision in EvalDecision:
            assert decision in DECISION_LABELS
            assert isinstance(DECISION_LABELS[decision], str)


class TestPreOrderEvalResult:
    """PreOrderEvalResult 데이터 구조 테스트"""

    def test_create_result(self):
        """결과 객체 생성"""
        result = PreOrderEvalResult(
            item_cd="TEST001",
            item_nm="테스트",
            mid_cd="049",
            decision=EvalDecision.FORCE_ORDER,
            reason="현재 품절",
            exposure_days=0.0,
            stockout_frequency=0.3,
            popularity_score=0.8,
            current_stock=0,
            pending_qty=0,
            daily_avg=5.0,
        )
        assert result.item_cd == "TEST001"
        assert result.decision == EvalDecision.FORCE_ORDER
        assert result.trend_score == 1.0  # 기본값


class TestExposureDays:
    """노출 시간 계산 테스트"""

    def test_exposure_normal(self, eval_db):
        """정상 노출 시간 계산"""
        db_path, conn = eval_db
        conn.close()

        config = EvalConfig()
        evaluator = PreOrderEvaluator(db_path=Path(db_path), config=config)

        # (재고10 + 미입고5) / 일평균5 = 3.0일
        exposure = evaluator._calculate_exposure_days(10, 5, 5.0)
        assert exposure == 3.0

    def test_exposure_zero_sales(self, eval_db):
        """판매 이력 없으면 999.0"""
        db_path, conn = eval_db
        conn.close()

        evaluator = PreOrderEvaluator(db_path=Path(db_path))
        exposure = evaluator._calculate_exposure_days(10, 0, 0.0)
        assert exposure == 999.0

    def test_exposure_with_lead_time(self, eval_db):
        """리드타임 고려한 노출 시간"""
        db_path, conn = eval_db
        conn.close()

        evaluator = PreOrderEvaluator(db_path=Path(db_path))
        # raw = (10+0)/5 = 2.0, lead_time=1.0 → 1.0
        exposure = evaluator._calculate_exposure_days(10, 0, 5.0, lead_time=1.0)
        assert exposure == 1.0

    def test_exposure_negative_clamped(self, eval_db):
        """음수 노출시간은 0으로 클램핑"""
        db_path, conn = eval_db
        conn.close()

        evaluator = PreOrderEvaluator(db_path=Path(db_path))
        # raw = (1+0)/5 = 0.2, lead_time=1.0 → max(0.2-1.0, 0) = 0.0
        exposure = evaluator._calculate_exposure_days(1, 0, 5.0, lead_time=1.0)
        assert exposure == 0.0


class TestDecisionMatrix:
    """결정 매트릭스 테스트"""

    @pytest.fixture
    def evaluator(self, eval_db):
        """테스트용 평가기"""
        db_path, conn = eval_db
        conn.close()
        config = EvalConfig()
        return PreOrderEvaluator(db_path=Path(db_path), config=config)

    def test_force_order_stockout(self, evaluator):
        """현재 품절 → FORCE_ORDER"""
        decision, reason = evaluator._make_decision(
            current_stock=0,
            exposure_days=0.0,
            popularity_level="high",
            stockout_frequency=0.0,
            daily_avg=5.0,
        )
        assert decision == EvalDecision.FORCE_ORDER
        assert "품절" in reason

    def test_urgent_order_low_exposure_high_pop(self, evaluator):
        """노출<1일 + 고인기 → URGENT_ORDER"""
        decision, reason = evaluator._make_decision(
            current_stock=2,
            exposure_days=0.5,
            popularity_level="high",
            stockout_frequency=0.0,
            daily_avg=5.0,
        )
        assert decision == EvalDecision.URGENT_ORDER

    def test_urgent_order_low_exposure_medium_pop(self, evaluator):
        """노출<1일 + 중인기 → URGENT_ORDER"""
        decision, reason = evaluator._make_decision(
            current_stock=2,
            exposure_days=0.5,
            popularity_level="medium",
            stockout_frequency=0.0,
            daily_avg=5.0,
        )
        assert decision == EvalDecision.URGENT_ORDER

    def test_normal_order_low_exposure_low_pop(self, evaluator):
        """노출<1일 + 저인기 → NORMAL_ORDER"""
        decision, reason = evaluator._make_decision(
            current_stock=2,
            exposure_days=0.5,
            popularity_level="low",
            stockout_frequency=0.0,
            daily_avg=5.0,
        )
        assert decision == EvalDecision.NORMAL_ORDER

    def test_normal_order_medium_exposure_high_pop(self, evaluator):
        """노출 1~2일 + 고인기 → NORMAL_ORDER"""
        decision, reason = evaluator._make_decision(
            current_stock=5,
            exposure_days=1.5,
            popularity_level="high",
            stockout_frequency=0.0,
            daily_avg=5.0,
        )
        assert decision == EvalDecision.NORMAL_ORDER

    def test_pass_medium_exposure_low_pop(self, evaluator):
        """노출 1~2일 + 저인기 → PASS"""
        decision, reason = evaluator._make_decision(
            current_stock=5,
            exposure_days=1.5,
            popularity_level="low",
            stockout_frequency=0.0,
            daily_avg=5.0,
        )
        assert decision == EvalDecision.PASS

    def test_skip_sufficient_stock_low_pop(self, evaluator):
        """재고충분(>=3일) + 저인기 → SKIP"""
        decision, reason = evaluator._make_decision(
            current_stock=20,
            exposure_days=4.0,
            popularity_level="low",
            stockout_frequency=0.0,
            daily_avg=5.0,
        )
        assert decision == EvalDecision.SKIP

    def test_pass_sufficient_stock_high_pop(self, evaluator):
        """재고충분(>=3일) + 고인기 → PASS (스킵 아님)"""
        decision, reason = evaluator._make_decision(
            current_stock=20,
            exposure_days=4.0,
            popularity_level="high",
            stockout_frequency=0.0,
            daily_avg=5.0,
        )
        assert decision == EvalDecision.PASS

    def test_stockout_frequency_upgrade(self, evaluator):
        """품절빈도 높으면 1단계 상승"""
        # PASS → NORMAL로 업그레이드 (노출<2일 + 품절빈도 >= 0.15)
        decision, reason = evaluator._make_decision(
            current_stock=5,
            exposure_days=1.5,
            popularity_level="low",
            stockout_frequency=0.20,  # >= 0.15 임계값
            daily_avg=5.0,
        )
        # 저인기 + 1~2일 노출 → PASS → 품절빈도로 NORMAL 업그레이드
        assert decision == EvalDecision.NORMAL_ORDER
        assert "품절빈도" in reason


class TestPopularity:
    """인기도 계산 테스트"""

    @pytest.fixture
    def evaluator(self, eval_db):
        db_path, conn = eval_db
        conn.close()
        return PreOrderEvaluator(db_path=Path(db_path))

    def test_popularity_score_range(self, evaluator):
        """인기도 점수는 0~1 범위"""
        all_daily_avgs = [1.0, 3.0, 5.0, 8.0, 10.0]

        score = evaluator._calculate_popularity(
            daily_avg=5.0,
            sell_day_ratio=0.8,
            trend_ratio=1.2,
            all_daily_avgs=all_daily_avgs,
        )
        assert 0.0 <= score <= 1.0

    def test_popularity_zero_data(self, evaluator):
        """데이터 없을 때 인기도 매우 낮음"""
        score = evaluator._calculate_popularity(
            daily_avg=0.0,
            sell_day_ratio=0.0,
            trend_ratio=1.0,
            all_daily_avgs=[],
        )
        # trend_ratio=1.0이면 트렌드 정규화값이 약간 > 0이므로 완전 0은 아님
        assert 0.0 <= score <= 0.2

    def test_popularity_level_high(self, evaluator):
        """고인기 등급"""
        evaluator._adaptive_thresholds = {"high": 0.6, "medium": 0.3}
        assert evaluator._get_popularity_level(0.8) == "high"

    def test_popularity_level_medium(self, evaluator):
        """중인기 등급"""
        evaluator._adaptive_thresholds = {"high": 0.6, "medium": 0.3}
        assert evaluator._get_popularity_level(0.4) == "medium"

    def test_popularity_level_low(self, evaluator):
        """저인기 등급"""
        evaluator._adaptive_thresholds = {"high": 0.6, "medium": 0.3}
        assert evaluator._get_popularity_level(0.2) == "low"


class TestEvaluateSingle:
    """단일 상품 평가 통합 테스트"""

    def test_evaluate_existing_product(self, eval_db):
        """존재하는 상품 평가"""
        db_path, conn = eval_db

        _insert_product(conn, "BEER001", "테스트맥주", "049")
        _insert_sales(conn, "BEER001", "049", days=30, avg_qty=5, stock_qty=3)
        _insert_inventory(conn, "BEER001", stock_qty=3, pending_qty=0)
        conn.commit()
        conn.close()

        evaluator = PreOrderEvaluator(db_path=Path(db_path))
        result = evaluator.evaluate("BEER001")

        assert result is not None
        assert result.item_cd == "BEER001"
        assert result.mid_cd == "049"
        assert isinstance(result.decision, EvalDecision)
        assert result.daily_avg > 0
        assert result.exposure_days >= 0

    def test_evaluate_nonexistent_product(self, eval_db):
        """존재하지 않는 상품 → None"""
        db_path, conn = eval_db
        conn.close()

        evaluator = PreOrderEvaluator(db_path=Path(db_path))
        result = evaluator.evaluate("NOTEXIST")

        assert result is None

    def test_evaluate_stockout_product(self, eval_db):
        """품절 상품 → FORCE_ORDER"""
        db_path, conn = eval_db

        _insert_product(conn, "SOLD_OUT", "품절상품", "049")
        _insert_sales(conn, "SOLD_OUT", "049", days=30, avg_qty=5, stock_qty=0)
        _insert_inventory(conn, "SOLD_OUT", stock_qty=0, pending_qty=0)
        conn.commit()
        conn.close()

        evaluator = PreOrderEvaluator(db_path=Path(db_path))
        result = evaluator.evaluate("SOLD_OUT")

        assert result is not None
        assert result.current_stock == 0
        assert result.decision == EvalDecision.FORCE_ORDER


class TestGetFilteredItems:
    """get_filtered_items 결과 분리 테스트"""

    def test_filter_by_decision(self):
        """결정별 분류"""
        results = {
            "A": PreOrderEvalResult("A", "상품A", "049", EvalDecision.FORCE_ORDER, "", 0.0, 0.0, 0.8, 0, 0, 5.0),
            "B": PreOrderEvalResult("B", "상품B", "049", EvalDecision.URGENT_ORDER, "", 0.5, 0.0, 0.6, 2, 0, 5.0),
            "C": PreOrderEvalResult("C", "상품C", "049", EvalDecision.NORMAL_ORDER, "", 1.5, 0.0, 0.5, 5, 0, 5.0),
            "D": PreOrderEvalResult("D", "상품D", "049", EvalDecision.PASS, "", 2.5, 0.0, 0.4, 10, 0, 5.0),
            "E": PreOrderEvalResult("E", "상품E", "049", EvalDecision.SKIP, "", 5.0, 0.0, 0.1, 20, 0, 1.0),
        }

        evaluator_cls = PreOrderEvaluator.__new__(PreOrderEvaluator)
        order_codes, skip_codes = PreOrderEvaluator.get_filtered_items(evaluator_cls, results)

        # FORCE → URGENT → NORMAL 순서
        assert order_codes == ["A", "B", "C"]
        # SKIP
        assert skip_codes == {"E"}
        # PASS는 어디에도 포함되지 않음
        assert "D" not in order_codes
        assert "D" not in skip_codes

    def test_empty_results(self):
        """빈 결과"""
        evaluator_cls = PreOrderEvaluator.__new__(PreOrderEvaluator)
        order_codes, skip_codes = PreOrderEvaluator.get_filtered_items(evaluator_cls, {})

        assert order_codes == []
        assert skip_codes == set()


class TestMedianHelper:
    """중앙값 계산 헬퍼 테스트"""

    def test_median_odd(self):
        assert PreOrderEvaluator._median([1, 3, 5]) == 3

    def test_median_even(self):
        assert PreOrderEvaluator._median([1, 3, 5, 7]) == 4.0

    def test_median_single(self):
        assert PreOrderEvaluator._median([42]) == 42

    def test_median_empty(self):
        assert PreOrderEvaluator._median([]) == 0.0

    def test_median_unsorted(self):
        assert PreOrderEvaluator._median([5, 1, 3]) == 3


class TestCalendarDayDailyAvg:
    """달력일 기준 일평균 계산 테스트"""

    def test_batch_load_calendar_based_avg(self, eval_db):
        """_batch_load_daily_sales_stats()가 달력일 기준 분모를 사용"""
        db_path, conn = eval_db
        today = datetime.now()

        _insert_product(conn, "SPARSE_EVAL", "간헐상품", "049")

        # 14일 중 5일만 판매 기록 (각 4개 판매, stock=10)
        for days_ago in [1, 3, 5, 8, 12]:
            date = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            conn.execute(
                "INSERT INTO daily_sales (item_cd, sales_date, sale_qty, stock_qty, mid_cd) "
                "VALUES (?, ?, ?, ?, ?)",
                ("SPARSE_EVAL", date, 4, 10, "049"),
            )
        conn.commit()

        config = EvalConfig()
        evaluator = PreOrderEvaluator(db_path=Path(db_path), config=config)

        stats = evaluator._batch_load_daily_sales_stats(["SPARSE_EVAL"], conn)

        # 총 판매 = 5일 × 4개 = 20개
        # 달력일 기반: 20 / 14 ≈ 1.43 (품절일 0이므로 달력일=14)
        # 기존 가용일 기반: 20 / 5 = 4.0
        daily_avg = stats["SPARSE_EVAL"]["daily_avg"]
        assert daily_avg < 2.0, (
            f"달력일 기준 daily_avg({daily_avg:.2f})가 2.0 미만이어야 함 "
            f"(기존 가용일 기준이면 4.0이 나옴)"
        )
        assert daily_avg > 1.0, f"daily_avg({daily_avg:.2f})가 너무 낮음"

        conn.close()


class TestForceIntermittentSuppression:
    """간헐수요 유통기한1일 FORCE_ORDER 억제 테스트"""

    @pytest.fixture
    def evaluator(self, eval_db):
        db_path, conn = eval_db
        conn.close()
        config = EvalConfig()
        return PreOrderEvaluator(db_path=Path(db_path), config=config)

    def test_intermittent_1day_expiry_pass(self, evaluator):
        """품절 + 간헐수요(30%) + 유통1일(주먹밥) -> PASS"""
        decision, reason = evaluator._make_decision(
            current_stock=0,
            exposure_days=0.0,
            popularity_level="low",
            stockout_frequency=0.5,
            daily_avg=0.3,
            mid_cd="002",  # 주먹밥 (유통기한 1일)
            sell_day_ratio=0.3,  # 30% < 50%
        )
        assert decision == EvalDecision.PASS
        assert "간헐수요" in reason
        assert "유통1일" in reason

    def test_frequent_1day_expiry_force(self, evaluator):
        """품절 + 빈번판매(70%) + 유통1일 -> FORCE_ORDER (억제 안 함)"""
        decision, reason = evaluator._make_decision(
            current_stock=0,
            exposure_days=0.0,
            popularity_level="high",
            stockout_frequency=0.3,
            daily_avg=3.0,
            mid_cd="001",  # 도시락 (유통기한 1일)
            sell_day_ratio=0.7,  # 70% >= 50%
        )
        assert decision == EvalDecision.FORCE_ORDER

    def test_intermittent_2day_expiry_force(self, evaluator):
        """품절 + 간헐수요 + 유통2일(샌드위치) -> FORCE_ORDER (2일은 대상 아님)"""
        decision, reason = evaluator._make_decision(
            current_stock=0,
            exposure_days=0.0,
            popularity_level="low",
            stockout_frequency=0.5,
            daily_avg=0.3,
            mid_cd="004",  # 샌드위치 (유통기한 2일)
            sell_day_ratio=0.3,  # 30% < 50%
        )
        assert decision == EvalDecision.FORCE_ORDER

    def test_intermittent_non_food_force(self, evaluator):
        """품절 + 간헐수요 + 비푸드(맥주) -> FORCE_ORDER (CATEGORY_EXPIRY_DAYS에 없음)"""
        decision, reason = evaluator._make_decision(
            current_stock=0,
            exposure_days=0.0,
            popularity_level="low",
            stockout_frequency=0.5,
            daily_avg=0.3,
            mid_cd="049",  # 맥주 (CATEGORY_EXPIRY_DAYS에 없음)
            sell_day_ratio=0.3,
        )
        assert decision == EvalDecision.FORCE_ORDER

    def test_intermittent_with_recent_order(self, evaluator):
        """품절 + 간헐수요 + 유통1일 + 어제 발주 -> PASS (최근발주 사유 포함)"""
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        decision, reason = evaluator._make_decision(
            current_stock=0,
            exposure_days=0.0,
            popularity_level="low",
            stockout_frequency=0.5,
            daily_avg=0.3,
            mid_cd="003",  # 김밥 (유통기한 1일)
            sell_day_ratio=0.3,
            last_order_date=yesterday,
        )
        assert decision == EvalDecision.PASS
        assert "최근발주" in reason

    def test_intermittent_boundary_50pct(self, evaluator):
        """품절 + 판매빈도 정확히 50% + 유통1일 -> FORCE_ORDER (< 아니므로 억제 안 함)"""
        decision, reason = evaluator._make_decision(
            current_stock=0,
            exposure_days=0.0,
            popularity_level="medium",
            stockout_frequency=0.3,
            daily_avg=1.0,
            mid_cd="001",
            sell_day_ratio=0.5,  # 정확히 50% -> 조건은 < 이므로 통과 안 함
        )
        assert decision == EvalDecision.FORCE_ORDER

    @patch("src.prediction.pre_order_evaluator.ENABLE_FORCE_INTERMITTENT_SUPPRESSION", False)
    def test_feature_flag_disabled(self, evaluator):
        """Feature flag False -> 기존 FORCE_ORDER 유지"""
        decision, reason = evaluator._make_decision(
            current_stock=0,
            exposure_days=0.0,
            popularity_level="low",
            stockout_frequency=0.5,
            daily_avg=0.3,
            mid_cd="002",
            sell_day_ratio=0.3,
        )
        assert decision == EvalDecision.FORCE_ORDER


class TestLastOrderDate:
    """최근 발주일 조회 테스트"""

    def test_get_last_order_date_found(self, eval_db):
        """발주 이력 있을 때 날짜 반환"""
        db_path, conn = eval_db
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO daily_sales (item_cd, sales_date, sale_qty, stock_qty, mid_cd, ord_qty) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("TEST_ORD", yesterday, 0, 0, "002", 1),
        )
        conn.commit()
        conn.close()

        evaluator = PreOrderEvaluator(db_path=Path(db_path))
        result = evaluator._get_last_order_date("TEST_ORD")
        assert result == yesterday

    def test_get_last_order_date_none(self, eval_db):
        """발주 이력 없을 때 None 반환"""
        db_path, conn = eval_db
        conn.close()

        evaluator = PreOrderEvaluator(db_path=Path(db_path))
        result = evaluator._get_last_order_date("NONEXIST")
        assert result is None
