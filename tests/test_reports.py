"""리포트 모듈 테스트

- BaseReport 기본 기능
- DailyOrderReport 결과 구조 검증
- WeeklyTrendReportHTML 결과 구조 검증
"""

import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def report_db(tmp_path):
    """리포트 테스트용 DB (판매/예측 데이터 포함)"""
    db_file = tmp_path / "test_report.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row

    conn.executescript("""
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

        CREATE TABLE products (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            mid_cd TEXT
        );

        CREATE TABLE eval_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT,
            eval_date TEXT,
            predicted_qty REAL,
            actual_qty REAL,
            accuracy REAL,
            store_id TEXT DEFAULT '46513',
            UNIQUE(store_id, eval_date, item_cd)
        );

        CREATE TABLE prediction_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT,
            target_date TEXT,
            predicted_qty REAL,
            model_type TEXT DEFAULT 'rule',
            store_id TEXT DEFAULT '46513'
        );
    """)

    # 샘플 데이터 삽입
    today = datetime.now()
    items = [
        ("ITEM001", "도시락A", "001", 10),
        ("ITEM002", "맥주B", "049", 5),
        ("ITEM003", "담배C", "072", 8),
    ]

    for item_cd, item_nm, mid_cd, avg_qty in items:
        conn.execute(
            "INSERT INTO products (item_cd, item_nm, mid_cd) VALUES (?, ?, ?)",
            (item_cd, item_nm, mid_cd),
        )
        for d in range(14):
            date = (today - timedelta(days=d)).strftime("%Y-%m-%d")
            conn.execute(
                "INSERT OR REPLACE INTO daily_sales "
                "(item_cd, sales_date, sale_qty, stock_qty, mid_cd) "
                "VALUES (?, ?, ?, ?, ?)",
                (item_cd, date, avg_qty, avg_qty * 2, mid_cd),
            )

    conn.commit()
    conn.close()
    return str(db_file)


class TestBaseReport:
    """BaseReport 기본 기능 테스트"""

    def test_number_format(self):
        """숫자 포맷팅"""
        from src.report.base_report import BaseReport

        assert BaseReport._number_format(1234) == "1,234"
        assert BaseReport._number_format(0) == "0"
        assert BaseReport._number_format(None) == "0"
        assert BaseReport._number_format(1234.5) == "1,234"

    def test_percent_format(self):
        """퍼센트 포맷팅"""
        from src.report.base_report import BaseReport

        assert BaseReport._percent_format(95.5) == "95.5%"
        assert BaseReport._percent_format(None) == "0%"

    def test_to_json(self):
        """JSON 직렬화"""
        import json
        from src.report.base_report import BaseReport

        result = BaseReport._to_json({"key": "값"})
        parsed = json.loads(result)
        assert parsed["key"] == "값"

    def test_init_creates_env(self, report_db):
        """초기화 시 Jinja2 환경 생성"""
        from src.report.base_report import BaseReport

        report = BaseReport(db_path=report_db)
        assert report.env is not None
        assert report.db_path == report_db

    def test_generate_raises_not_implemented(self, report_db):
        """BaseReport.generate()는 NotImplementedError"""
        from src.report.base_report import BaseReport

        report = BaseReport(db_path=report_db)
        with pytest.raises(NotImplementedError):
            report.generate()

    def test_save_creates_file(self, report_db, tmp_path):
        """save() 메서드로 HTML 파일 저장"""
        from src.report.base_report import BaseReport

        report = BaseReport(db_path=report_db)
        report.REPORT_SUB_DIR = "test_output"

        # data/reports/test_output 디렉토리에 저장
        path = report.save("<html>test</html>", "test.html")
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "<html>test</html>"


class TestDailyOrderReport:
    """일일 발주 리포트 테스트"""

    def _make_prediction_result(self, item_cd="ITEM001", item_nm="테스트", mid_cd="001",
                                 order_qty=5, predicted_qty=8.0, adjusted_qty=9.0,
                                 safety_stock=3.0, current_stock=2, pending_qty=0,
                                 confidence="high", data_days=30, weekday_coef=1.1):
        """테스트용 PredictionResult 생성"""
        from src.prediction.improved_predictor import PredictionResult
        return PredictionResult(
            item_cd=item_cd,
            item_nm=item_nm,
            mid_cd=mid_cd,
            target_date="2026-02-08",
            predicted_qty=predicted_qty,
            adjusted_qty=adjusted_qty,
            current_stock=current_stock,
            pending_qty=pending_qty,
            safety_stock=safety_stock,
            order_qty=order_qty,
            confidence=confidence,
            data_days=data_days,
            weekday_coef=weekday_coef,
        )

    def test_build_context(self, report_db):
        """_build_context가 필수 키를 반환하는지 확인"""
        from src.report.daily_order_report import DailyOrderReport

        report = DailyOrderReport(db_path=report_db)
        predictions = [
            self._make_prediction_result("ITEM001", "도시락A", "001", order_qty=5),
            self._make_prediction_result("ITEM002", "맥주B", "049", order_qty=3),
            self._make_prediction_result("ITEM003", "담배C", "072", order_qty=0),
        ]

        context = report._build_context(predictions, "2026-02-08")

        assert "target_date" in context
        assert "summary" in context
        assert "category_data" in context
        assert "items" in context

    def test_summary_calculation(self, report_db):
        """요약 통계 계산"""
        from src.report.daily_order_report import DailyOrderReport

        report = DailyOrderReport(db_path=report_db)
        predictions = [
            self._make_prediction_result("A", order_qty=5),
            self._make_prediction_result("B", order_qty=3),
            self._make_prediction_result("C", order_qty=0),
        ]

        summary = report._calc_summary(predictions)

        assert summary["total_items"] == 3
        assert summary["total_order_qty"] == 8  # 5 + 3 + 0

    def test_generate_creates_file(self, report_db):
        """generate()가 HTML 파일을 생성하는지 확인"""
        from src.report.daily_order_report import DailyOrderReport

        report = DailyOrderReport(db_path=report_db)
        predictions = [
            self._make_prediction_result("ITEM001", "도시락A", "001", order_qty=5),
        ]

        path = report.generate(predictions, target_date="2026-02-08")

        assert path.exists()
        assert path.suffix == ".html"
        content = path.read_text(encoding="utf-8")
        assert len(content) > 0


class TestWeeklyTrendReport:
    """주간 트렌드 리포트 테스트"""

    def test_init(self, report_db):
        """초기화"""
        from src.report.weekly_trend_report import WeeklyTrendReportHTML

        report = WeeklyTrendReportHTML(db_path=report_db)
        assert report.REPORT_SUB_DIR == "weekly"
        assert report.TEMPLATE_NAME == "weekly_trend.html"

    def test_query_weekly_summary(self, report_db):
        """주간 요약 쿼리"""
        from src.report.weekly_trend_report import WeeklyTrendReportHTML

        report = WeeklyTrendReportHTML(db_path=report_db)
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        summary = report._query_weekly_summary(yesterday)

        assert isinstance(summary, dict)

    def test_query_top_items(self, report_db):
        """상위 판매 상품 쿼리"""
        from src.report.weekly_trend_report import WeeklyTrendReportHTML

        report = WeeklyTrendReportHTML(db_path=report_db)
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        top = report._query_top_items(yesterday)

        # dict 또는 list 형태 (구현에 따라 다를 수 있음)
        assert isinstance(top, (list, dict))
        if isinstance(top, dict):
            assert "top_sellers" in top

    def test_generate_creates_file(self, report_db):
        """generate()가 HTML 파일을 생성하는지 확인"""
        from src.report.weekly_trend_report import WeeklyTrendReportHTML

        report = WeeklyTrendReportHTML(db_path=report_db)
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        path = report.generate(end_date=yesterday)

        assert path.exists()
        assert path.suffix == ".html"
        content = path.read_text(encoding="utf-8")
        assert len(content) > 0


class TestReportDBConnection:
    """DB 커넥션 관리 테스트"""

    def test_get_connection(self, report_db):
        """_get_connection 정상 동작"""
        from src.report.base_report import BaseReport

        report = BaseReport(db_path=report_db)
        conn = report._get_connection()

        assert conn is not None
        # Row factory 설정 확인
        assert conn.row_factory == sqlite3.Row
        conn.close()

    def test_get_connection_invalid_path(self, tmp_path):
        """존재하지 않는 DB 경로 → 빈 DB 생성 (SQLite 동작)"""
        from src.report.base_report import BaseReport

        db_path = str(tmp_path / "nonexistent" / "test.db")
        report = BaseReport(db_path=db_path)

        # SQLite는 존재하지 않는 디렉토리면 에러
        # 존재하는 디렉토리 + 새 파일이면 자동 생성
        new_db = str(tmp_path / "new_test.db")
        report2 = BaseReport(db_path=new_db)
        conn = report2._get_connection()
        assert conn is not None
        conn.close()
