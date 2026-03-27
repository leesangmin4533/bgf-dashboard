# -*- coding: utf-8 -*-
"""시간대별 매출 수집 + 동적 비율 테스트

테스트 대상:
- HourlySalesRepository: DB 저장/조회/비율계산
- HourlySalesCollector: SSV 파싱
- food.py: _get_time_demand_ratio 폴백
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 프로젝트 루트 추가
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


# ─── Fixtures ─────────────────────────────────────────────

@pytest.fixture
def temp_db():
    """임시 DB 파일 생성"""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = Path(f.name)
    yield db_path
    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest.fixture
def repo(temp_db):
    """HourlySalesRepository 인스턴스"""
    from src.infrastructure.database.repos.hourly_sales_repo import HourlySalesRepository
    r = HourlySalesRepository(db_path=temp_db)
    r.ensure_table()
    return r


@pytest.fixture
def sample_24h_data():
    """24시간 샘플 데이터 (편의점 매출 패턴)"""
    # 편의점 전형적 매출 패턴: 점심(12시)과 저녁(18시) 피크
    hourly_amts = {
        0: 50000, 1: 30000, 2: 20000, 3: 10000,
        4: 10000, 5: 30000, 6: 80000, 7: 150000,
        8: 200000, 9: 180000, 10: 160000, 11: 250000,
        12: 350000, 13: 280000, 14: 200000, 15: 180000,
        16: 220000, 17: 300000, 18: 350000, 19: 280000,
        20: 200000, 21: 150000, 22: 100000, 23: 70000,
    }
    return [
        {
            'hour': h,
            'sale_amt': float(amt),
            'sale_cnt': amt // 10000,
            'sale_cnt_danga': 10000.0,
            'rate': round(amt / sum(hourly_amts.values()) * 100, 1),
        }
        for h, amt in hourly_amts.items()
    ]


@pytest.fixture
def sample_ssv_response():
    """STMB010 SSV 응답 샘플"""
    RS = '\u001e'
    US = '\u001f'

    header = f"SSV:UTF-8{RS}ErrorCode:string=0{RS}ErrorMsg:string={RS}"
    header += f"xm_tid:string=1234567890{RS}"
    header += f"Dataset:dsList{RS}"

    # 컬럼 헤더
    cols = (
        f"_RowType_{US}HMS:string(2){US}AMT:bigdecimal(0)"
        f"{US}CNT:bigdecimal(0){US}CNT_DANGA:bigdecimal(0)"
        f"{US}RATE:bigdecimal(0){US}PAGE_CNT:bigdecimal(0)"
    )

    # 24시간 데이터
    rows = []
    for h in range(24):
        amt = 100000 + h * 10000  # 단순 증가 패턴
        cnt = 10 + h
        danga = amt // cnt if cnt > 0 else 0
        rate = round(amt / (100000 * 24 + 10000 * 276) * 100, 1)
        row = f"N{US}{h:02d}{US}{amt}{US}{cnt}{US}{danga}{US}{rate}{US}0"
        rows.append(row)

    return header + cols + RS + RS.join(rows)


# ─── HourlySalesRepository Tests ─────────────────────────

class TestHourlySalesRepository:

    def test_ensure_table(self, repo):
        """테이블 생성 확인"""
        import sqlite3
        conn = sqlite3.connect(str(repo._db_path))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hourly_sales'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_save_hourly_sales(self, repo, sample_24h_data):
        """24시간 데이터 저장"""
        saved = repo.save_hourly_sales(sample_24h_data, '2026-02-27')
        assert saved == 24

    def test_save_upsert(self, repo, sample_24h_data):
        """동일 날짜 재저장 (UPSERT)"""
        repo.save_hourly_sales(sample_24h_data, '2026-02-27')
        # 값 변경 후 재저장
        modified = [dict(d) for d in sample_24h_data]
        modified[12]['sale_amt'] = 999999.0
        saved = repo.save_hourly_sales(modified, '2026-02-27')
        assert saved == 24

        # 변경된 값 확인
        result = repo.get_hourly_sales('2026-02-27')
        hour_12 = [r for r in result if r['hour'] == 12][0]
        assert hour_12['sale_amt'] == 999999.0

    def test_save_empty_data(self, repo):
        """빈 데이터 저장"""
        saved = repo.save_hourly_sales([], '2026-02-27')
        assert saved == 0

    def test_get_hourly_sales(self, repo, sample_24h_data):
        """시간대별 매출 조회"""
        repo.save_hourly_sales(sample_24h_data, '2026-02-27')
        result = repo.get_hourly_sales('2026-02-27')
        assert len(result) == 24
        assert result[0]['hour'] == 0
        assert result[23]['hour'] == 23

    def test_get_hourly_sales_no_data(self, repo):
        """데이터 없는 날짜 조회"""
        result = repo.get_hourly_sales('2099-01-01')
        assert result == []

    def test_get_recent_hourly_distribution(self, repo, sample_24h_data):
        """최근 N일 시간대별 분포"""
        # 3일분 데이터 저장
        for i in range(3):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            repo.save_hourly_sales(sample_24h_data, date)

        dist = repo.get_recent_hourly_distribution(days=7)
        assert len(dist) == 24
        # 12시가 피크여야 함
        assert dist[12] > dist[3]

    def test_get_recent_distribution_no_data(self, repo):
        """데이터 없을 때 빈 딕셔너리"""
        dist = repo.get_recent_hourly_distribution(days=7)
        assert dist == {}

    def test_calculate_time_demand_ratio(self, repo, sample_24h_data):
        """배송차수 비율 계산 (1차/2차 분리, hourly_sales_detail 기반)"""
        # hourly_sales_detail + daily_sales 테이블 생성 + 3일분 데이터 삽입
        conn = repo._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hourly_sales_detail (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sales_date TEXT NOT NULL,
                    hour INTEGER NOT NULL,
                    item_cd TEXT NOT NULL DEFAULT '',
                    sale_qty INTEGER DEFAULT 0,
                    sale_amt REAL DEFAULT 0,
                    UNIQUE(sales_date, hour, item_cd)
                )
            """)
            # daily_sales: 1차 상품 매핑 (item_nm LIKE '%1', mid_cd=001)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_sales (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sales_date TEXT, item_cd TEXT, item_nm TEXT,
                    mid_cd TEXT, sale_qty INTEGER DEFAULT 0,
                    ord_qty INTEGER DEFAULT 0, disuse_qty INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                INSERT OR IGNORE INTO daily_sales
                (sales_date, item_cd, item_nm, mid_cd, sale_qty)
                VALUES ('2026-01-01', 'TEST001', 'food1', '001', 1)
            """)
            for i in range(1, 4):
                date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
                for entry in sample_24h_data:
                    conn.execute("""
                        INSERT OR REPLACE INTO hourly_sales_detail
                        (sales_date, hour, item_cd, sale_qty, sale_amt)
                        VALUES (?, ?, 'TEST001', ?, ?)
                    """, (date, entry['hour'], entry['sale_cnt'], entry['sale_amt']))
            conn.commit()
        finally:
            conn.close()

        ratios = repo.calculate_time_demand_ratio()
        assert '1차' in ratios
        assert '2차' in ratios
        assert ratios['2차'] == 1.00
        # 1차 비율: 07~19시 합계 / 전체 합계
        # 샘플 데이터에서 07~19시 매출이 전체의 약 73%
        assert 0.50 <= ratios['1차'] <= 0.90

    def test_calculate_ratio_no_data(self, repo):
        """데이터 없을 때 빈 딕셔너리"""
        ratios = repo.calculate_time_demand_ratio()
        assert ratios == {}

    @staticmethod
    def _insert_hourly_detail(repo, data_list, days_ago_list):
        """hourly_sales_detail + daily_sales 테이블에 테스트 데이터 삽입 헬퍼"""
        conn = repo._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hourly_sales_detail (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sales_date TEXT NOT NULL,
                    hour INTEGER NOT NULL,
                    item_cd TEXT NOT NULL DEFAULT '',
                    sale_qty INTEGER DEFAULT 0,
                    sale_amt REAL DEFAULT 0,
                    UNIQUE(sales_date, hour, item_cd)
                )
            """)
            # daily_sales: 1차 상품 매핑 (item_nm LIKE '%1', mid_cd IN 001~005)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_sales (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sales_date TEXT, item_cd TEXT, item_nm TEXT,
                    mid_cd TEXT, sale_qty INTEGER DEFAULT 0,
                    ord_qty INTEGER DEFAULT 0, disuse_qty INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                INSERT OR IGNORE INTO daily_sales
                (sales_date, item_cd, item_nm, mid_cd, sale_qty)
                VALUES ('2026-01-01', 'TEST001', 'food1', '001', 1)
            """)
            for days_ago in days_ago_list:
                date = (datetime.now() - timedelta(days=days_ago)).strftime('%Y-%m-%d')
                for entry in data_list:
                    conn.execute("""
                        INSERT OR REPLACE INTO hourly_sales_detail
                        (sales_date, hour, item_cd, sale_qty, sale_amt)
                        VALUES (?, ?, 'TEST001', ?, ?)
                    """, (date, entry['hour'], entry.get('sale_cnt', 1), entry['sale_amt']))
            conn.commit()
        finally:
            conn.close()

    def test_ratio_clamp_min(self, repo):
        """비율 하한 클램프 (0.50)"""
        # 야간 매출만 있는 극단적 케이스
        night_data = [
            {'hour': h, 'sale_amt': 100000.0 if h < 7 or h >= 20 else 1.0,
             'sale_cnt': 10, 'sale_cnt_danga': 10000.0, 'rate': 0}
            for h in range(24)
        ]
        self._insert_hourly_detail(repo, night_data, [3])

        ratios = repo.calculate_time_demand_ratio(days=9999)
        # '1차' key: first delivery batch daytime ratio
        assert '1차' in ratios, f"'1차' key not found in ratios: {list(ratios.keys())}"
        assert ratios['1차'] >= 0.50  # 하한 클램프

    def test_ratio_clamp_max(self, repo):
        """비율 상한 클램프 (0.90)"""
        # 주간 매출만 있는 극단적 케이스
        day_data = [
            {'hour': h, 'sale_amt': 100000.0 if 7 <= h < 20 else 1.0,
             'sale_cnt': 10, 'sale_cnt_danga': 10000.0, 'rate': 0}
            for h in range(24)
        ]
        self._insert_hourly_detail(repo, day_data, [3])

        ratios = repo.calculate_time_demand_ratio(days=9999)
        assert '1차' in ratios, f"'1차' key not found in ratios: {list(ratios.keys())}"
        assert ratios['1차'] <= 0.90  # 상한 클램프


# ─── HourlySalesCollector Tests ──────────────────────────

class TestHourlySalesCollector:

    def test_parse_response(self, sample_ssv_response):
        """SSV 응답 파싱"""
        from src.collectors.hourly_sales_collector import HourlySalesCollector

        collector = HourlySalesCollector()
        result = collector._parse_response(sample_ssv_response)

        assert len(result) == 24
        assert result[0]['hour'] == 0
        assert result[23]['hour'] == 23
        assert result[0]['sale_amt'] > 0
        assert result[0]['sale_cnt'] > 0

    def test_parse_empty_response(self):
        """빈 응답 파싱"""
        from src.collectors.hourly_sales_collector import HourlySalesCollector

        collector = HourlySalesCollector()
        result = collector._parse_response("")
        assert result == []

    def test_parse_invalid_response(self):
        """잘못된 SSV 응답"""
        from src.collectors.hourly_sales_collector import HourlySalesCollector

        collector = HourlySalesCollector()
        result = collector._parse_response("not-ssv-data")
        assert result == []

    def test_replace_date_param(self):
        """날짜 파라미터 치환"""
        from src.collectors.hourly_sales_collector import HourlySalesCollector

        collector = HourlySalesCollector()
        template = "SSV:utf-8\u001ecalFromDay=20260226\u001ecalToDay=20260226"
        result = collector._replace_date_param(template, "20260301")
        assert "calFromDay=20260301" in result
        assert "calToDay=20260301" in result

    def test_collect_no_driver(self):
        """드라이버 없을 때 빈 리스트"""
        from src.collectors.hourly_sales_collector import HourlySalesCollector

        collector = HourlySalesCollector(driver=None)
        result = collector.collect("20260227")
        assert result == []

    def test_collect_invalid_date(self):
        """잘못된 날짜 형식"""
        from src.collectors.hourly_sales_collector import HourlySalesCollector

        collector = HourlySalesCollector()
        result = collector.collect("2026")
        assert result == []

    def test_safe_float(self):
        """안전한 float 변환"""
        from src.collectors.hourly_sales_collector import HourlySalesCollector

        assert HourlySalesCollector._safe_float("123.45") == 123.45
        assert HourlySalesCollector._safe_float("") == 0.0
        assert HourlySalesCollector._safe_float(None) == 0.0
        assert HourlySalesCollector._safe_float("abc") == 0.0

    def test_safe_int(self):
        """안전한 int 변환"""
        from src.collectors.hourly_sales_collector import HourlySalesCollector

        assert HourlySalesCollector._safe_int("42") == 42
        assert HourlySalesCollector._safe_int("42.7") == 42
        assert HourlySalesCollector._safe_int("") == 0
        assert HourlySalesCollector._safe_int(None) == 0


# ─── food.py 동적 비율 통합 테스트 ──────────────────────

class TestDynamicTimeRatio:

    def test_fallback_no_store_id(self):
        """store_id 없을 때 고정값 폴백"""
        from src.prediction.categories.food import _get_time_demand_ratio

        ratio = _get_time_demand_ratio("1차", store_id=None)
        assert ratio == 0.70

    def test_fallback_db_error(self):
        """DB 에러 시 고정값 폴백"""
        from src.prediction.categories.food import _get_time_demand_ratio

        with patch(
            'src.infrastructure.database.repos.hourly_sales_repo.HourlySalesRepository',
            side_effect=Exception("DB error")
        ):
            ratio = _get_time_demand_ratio("1차", store_id="99999")
            assert ratio == 0.70

    def test_fallback_no_data(self, temp_db):
        """DB에 데이터 없을 때 고정값 폴백"""
        from src.prediction.categories.food import _get_time_demand_ratio

        with patch(
            'src.infrastructure.database.repos.hourly_sales_repo.HourlySalesRepository'
        ) as MockRepo:
            mock_instance = MagicMock()
            mock_instance.calculate_time_demand_ratio.return_value = {}
            MockRepo.return_value = mock_instance

            ratio = _get_time_demand_ratio("1차", store_id="46513")
            assert ratio == 0.70

    def test_dynamic_ratio_from_db(self):
        """DB 데이터로 동적 비율 반환"""
        from src.prediction.categories.food import _get_time_demand_ratio

        with patch(
            'src.infrastructure.database.repos.hourly_sales_repo.HourlySalesRepository'
        ) as MockRepo:
            mock_instance = MagicMock()
            mock_instance.calculate_time_demand_ratio.return_value = {
                "1차": 0.72, "2차": 1.00
            }
            MockRepo.return_value = mock_instance

            ratio = _get_time_demand_ratio("1차", store_id="46513")
            assert ratio == 0.72

    def test_second_delivery_always_100(self):
        """2차 배송은 항상 1.00"""
        from src.prediction.categories.food import _get_time_demand_ratio

        with patch(
            'src.infrastructure.database.repos.hourly_sales_repo.HourlySalesRepository'
        ) as MockRepo:
            mock_instance = MagicMock()
            mock_instance.calculate_time_demand_ratio.return_value = {
                "1차": 0.72, "2차": 1.00
            }
            MockRepo.return_value = mock_instance

            ratio = _get_time_demand_ratio("2차", store_id="46513")
            assert ratio == 1.00

    def test_gap_consumption_uses_dynamic_ratio(self):
        """calculate_delivery_gap_consumption이 동적 비율 사용"""
        from src.prediction.categories.food import calculate_delivery_gap_consumption

        with patch(
            'src.prediction.categories.food._get_time_demand_ratio',
            return_value=0.72
        ):
            result = calculate_delivery_gap_consumption(
                daily_avg=10.0,
                item_nm="도시락1",
                expiry_group="ultra_short",
                mid_cd="001",
                store_id="46513",
            )
            # result = 10.0 * 0.72 * coefficient
            assert result > 0


# ─── 통합 플로우 테스트 ──────────────────────────────────

class TestIntegrationFlow:

    def test_full_save_and_ratio(self, repo, sample_24h_data):
        """저장 → 비율 계산 전체 플로우 (hourly_sales_detail 기반)"""
        # hourly_sales_detail 에 7일분 데이터 저장
        TestHourlySalesRepository._insert_hourly_detail(
            repo, sample_24h_data, list(range(1, 8))
        )

        # 비율 계산
        ratios = repo.calculate_time_demand_ratio()
        assert ratios['1차'] > 0.50
        assert ratios['1차'] < 0.90
        assert ratios['2차'] == 1.00

    def test_parse_and_save(self, repo, sample_ssv_response):
        """SSV 파싱 → DB 저장 플로우"""
        from src.collectors.hourly_sales_collector import HourlySalesCollector

        collector = HourlySalesCollector()
        parsed = collector._parse_response(sample_ssv_response)
        assert len(parsed) == 24

        saved = repo.save_hourly_sales(parsed, '2026-02-27')
        assert saved == 24

        result = repo.get_hourly_sales('2026-02-27')
        assert len(result) == 24
