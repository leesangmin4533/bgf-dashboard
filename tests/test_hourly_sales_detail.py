# -*- coding: utf-8 -*-
"""시간대별 매출 상세(품목별) 수집 테스트

테스트 대상:
- HourlySalesDetailRepository: raw/detail 저장/조회, UPSERT, 수집현황
- HourlySalesDetailCollector: SSV 파싱, 파라미터 치환, heartbeat
- HourlyDetailService: collect_previous_hour 자정 전환, skip-collected
"""

import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 프로젝트 루트 추가
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# SSV 구분자
RS = '\u001e'
US = '\u001f'


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
    """HourlySalesDetailRepository 인스턴스 (임시 DB)"""
    from src.infrastructure.database.repos.hourly_sales_detail_repo import (
        HourlySalesDetailRepository,
    )
    r = HourlySalesDetailRepository.__new__(HourlySalesDetailRepository)
    r.store_id = "99999"
    r._db_path = temp_db
    r.db_type = "store"

    # _get_conn 오버라이드 (임시 DB 직접 연결)
    def _get_conn():
        conn = sqlite3.connect(str(temp_db))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    r._get_conn = _get_conn
    r._now = lambda: datetime.now().isoformat()

    def _to_int(v):
        if v is None or v == "":
            return 0
        try:
            return int(v)
        except (ValueError, TypeError):
            return 0

    def _to_float(v):
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    r._to_int = _to_int
    r._to_float = _to_float
    r.ensure_table()
    return r


@pytest.fixture
def sample_items():
    """12시 품목 샘플 데이터"""
    return [
        {
            'item_cd': '8801234567890',
            'item_nm': 'CU 삼각김밥',
            'sale_qty': 5,
            'sale_amt': 5500.0,
            'receipt_amt': 5000.0,
            'rate': 25.0,
            'month_evt': '1+1',
            'ord_item': '8801234567890',
        },
        {
            'item_cd': '8809876543210',
            'item_nm': '카스 후레쉬 500ml',
            'sale_qty': 3,
            'sale_amt': 7500.0,
            'receipt_amt': 7200.0,
            'rate': 35.0,
            'month_evt': '',
            'ord_item': '8809876543210',
        },
        {
            'item_cd': '8805551112222',
            'item_nm': '바나나우유 240ml',
            'sale_qty': 2,
            'sale_amt': 3000.0,
            'receipt_amt': 2800.0,
            'rate': 15.0,
            'month_evt': '할인',
            'ord_item': '',
        },
    ]


@pytest.fixture
def sample_ssv_prdt3():
    """selPrdT3 SSV 응답 샘플"""
    header = f"SSV:UTF-8{RS}ErrorCode:string=0{RS}ErrorMsg:string={RS}"
    header += f"xm_tid:string=1234567890{RS}"
    header += f"Dataset:dsList{RS}"

    # 컬럼 헤더
    cols = (
        f"_RowType_{US}ITEM_CD:string(256){US}ITEM_NM:string(256)"
        f"{US}SALE_QTY:bigdecimal(0){US}RECT_AMT:bigdecimal(0)"
        f"{US}G_RECT_AMT:bigdecimal(0){US}RATE:bigdecimal(0)"
        f"{US}MONTH_EVT:string(256){US}ORD_ITEM:string(256)"
    )

    # 3개 품목 데이터
    rows = [
        f"N{US}8801234567890{US}CU 삼각김밥{US}5{US}5500{US}5000{US}25.0{US}1+1{US}8801234567890",
        f"N{US}8809876543210{US}카스 후레쉬 500ml{US}3{US}7500{US}7200{US}35.0{US}{US}8809876543210",
        f"N{US}8805551112222{US}바나나우유 240ml{US}2{US}3000{US}2800{US}15.0{US}할인{US}",
    ]

    return header + cols + RS + RS.join(rows)


@pytest.fixture
def sample_ssv_prdt3_empty():
    """selPrdT3 빈 응답 (데이터 없음)"""
    header = f"SSV:UTF-8{RS}ErrorCode:string=0{RS}ErrorMsg:string={RS}"
    header += f"xm_tid:string=1234567890{RS}"
    header += f"Dataset:dsList{RS}"
    cols = (
        f"_RowType_{US}ITEM_CD:string(256){US}ITEM_NM:string(256)"
        f"{US}SALE_QTY:bigdecimal(0){US}RECT_AMT:bigdecimal(0)"
    )
    return header + cols  # 데이터 행 없음


@pytest.fixture
def template_body():
    """XHR body 템플릿 샘플"""
    return (
        f"SSV:utf-8{RS}"
        f"calFromDay=20260307{RS}"
        f"calToDay=20260307{RS}"
        f"strYmd=20260307{RS}"
        f"strHms=12{RS}"
        f"strFromYmd=20260307{RS}"
        f"strToYmd=20260307{RS}"
        f"strTime=12"
    )


# ─── HourlySalesDetailRepository Tests ────────────────────

class TestHourlySalesDetailRepository:

    def test_ensure_table_creates_tables(self, repo, temp_db):
        """테이블 2개 + 인덱스 생성 확인"""
        conn = sqlite3.connect(str(temp_db))
        cursor = conn.cursor()

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='raw_hourly_sales_detail'"
        )
        assert cursor.fetchone() is not None

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='hourly_sales_detail'"
        )
        assert cursor.fetchone() is not None

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_hsd_date_hour'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_save_raw(self, repo):
        """raw 응답 저장"""
        repo.save_raw('2026-03-07', 12, 'SSV:UTF-8...raw_data...', 5)
        conn = repo._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT ssv_response, item_count FROM raw_hourly_sales_detail "
            "WHERE sales_date='2026-03-07' AND hour=12"
        )
        row = cursor.fetchone()
        conn.close()
        assert row is not None
        assert 'raw_data' in row[0]
        assert row[1] == 5

    def test_save_raw_upsert(self, repo):
        """raw UPSERT: 동일 날짜+시간 재저장"""
        repo.save_raw('2026-03-07', 12, 'first', 3)
        repo.save_raw('2026-03-07', 12, 'second', 5)

        conn = repo._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT ssv_response, item_count FROM raw_hourly_sales_detail "
            "WHERE sales_date='2026-03-07' AND hour=12"
        )
        row = cursor.fetchone()
        conn.close()
        assert row[0] == 'second'
        assert row[1] == 5

    def test_save_detail(self, repo, sample_items):
        """품목별 상세 저장"""
        saved = repo.save_detail('2026-03-07', 12, sample_items)
        assert saved == 3

    def test_save_detail_upsert(self, repo, sample_items):
        """상세 UPSERT: 동일 날짜+시간+품목 재저장"""
        repo.save_detail('2026-03-07', 12, sample_items)

        # sale_qty 변경 후 재저장
        modified = [dict(d) for d in sample_items]
        modified[0]['sale_qty'] = 99
        saved = repo.save_detail('2026-03-07', 12, modified)
        assert saved == 3

        result = repo.get_hourly_detail('2026-03-07', 12)
        item_0 = [r for r in result if r['item_cd'] == '8801234567890'][0]
        assert item_0['sale_qty'] == 99

    def test_save_detail_empty(self, repo):
        """빈 리스트 저장"""
        saved = repo.save_detail('2026-03-07', 12, [])
        assert saved == 0

    def test_save_detail_skip_empty_item_cd(self, repo):
        """item_cd 비어있는 항목 건너뛰기"""
        items = [{'item_cd': '', 'item_nm': 'empty', 'sale_qty': 1}]
        saved = repo.save_detail('2026-03-07', 12, items)
        assert saved == 0

    def test_get_hourly_detail(self, repo, sample_items):
        """특정 시간대 품목 조회 (sale_amt DESC 정렬)"""
        repo.save_detail('2026-03-07', 12, sample_items)
        result = repo.get_hourly_detail('2026-03-07', 12)
        assert len(result) == 3
        # sale_amt 내림차순: 카스(7500) > 삼각김밥(5500) > 바나나우유(3000)
        assert result[0]['item_cd'] == '8809876543210'
        assert result[1]['item_cd'] == '8801234567890'
        assert result[2]['item_cd'] == '8805551112222'

    def test_get_hourly_detail_no_data(self, repo):
        """데이터 없는 시간대 조회"""
        result = repo.get_hourly_detail('2099-01-01', 0)
        assert result == []

    def test_get_daily_item_summary(self, repo, sample_items):
        """일별 품목 집계 (여러 시간대 합산)"""
        repo.save_detail('2026-03-07', 10, sample_items)
        # 12시에 삼각김밥 추가 판매
        repo.save_detail('2026-03-07', 12, [
            {
                'item_cd': '8801234567890',
                'item_nm': 'CU 삼각김밥',
                'sale_qty': 3,
                'sale_amt': 3300.0,
                'receipt_amt': 3000.0,
                'rate': 20.0,
                'month_evt': '',
                'ord_item': '',
            },
        ])

        summary = repo.get_daily_item_summary('2026-03-07')
        assert len(summary) == 3

        kimbap = [s for s in summary if s['item_cd'] == '8801234567890'][0]
        assert kimbap['total_qty'] == 8  # 5 + 3
        assert kimbap['total_amt'] == 8800.0  # 5500 + 3300
        assert kimbap['first_hour'] == 10
        assert kimbap['last_hour'] == 12
        assert kimbap['active_hours'] == 2

    def test_get_item_hourly_pattern(self, repo, sample_items):
        """상품별 시간대 판매 패턴"""
        today = datetime.now().strftime('%Y-%m-%d')
        repo.save_detail(today, 10, sample_items)
        repo.save_detail(today, 14, [
            {
                'item_cd': '8801234567890',
                'item_nm': 'CU 삼각김밥',
                'sale_qty': 7,
                'sale_amt': 7700.0,
                'receipt_amt': 7000.0,
                'rate': 30.0,
                'month_evt': '',
                'ord_item': '',
            },
        ])

        pattern = repo.get_item_hourly_pattern('8801234567890', days=7)
        assert 10 in pattern
        assert 14 in pattern
        assert pattern[10] == 5.0
        assert pattern[14] == 7.0

    def test_get_collected_hours(self, repo, sample_items):
        """수집 완료 시간대 목록"""
        repo.save_detail('2026-03-07', 10, sample_items)
        repo.save_detail('2026-03-07', 14, sample_items[:1])

        collected = repo.get_collected_hours('2026-03-07')
        assert collected == [10, 14]

    def test_get_collected_hours_no_data(self, repo):
        """데이터 없으면 빈 리스트"""
        collected = repo.get_collected_hours('2099-01-01')
        assert collected == []

    def test_get_failed_hours_past_date(self, repo, sample_items):
        """과거 날짜: 0~23시 중 미수집 시간대"""
        repo.save_detail('2026-01-01', 10, sample_items)
        repo.save_detail('2026-01-01', 14, sample_items[:1])

        failed = repo.get_failed_hours('2026-01-01')
        assert 10 not in failed
        assert 14 not in failed
        assert 0 in failed
        assert 23 in failed
        assert len(failed) == 22  # 24 - 2

    def test_get_failed_hours_today(self, repo, sample_items):
        """오늘 날짜: 현재시각 이전 시간대 중 미수집"""
        today = datetime.now().strftime('%Y-%m-%d')
        repo.save_detail(today, 0, sample_items)

        failed = repo.get_failed_hours(today)
        # 0시는 수집됨, 현재시각 이전 나머지가 실패 목록
        assert 0 not in failed
        now_hour = datetime.now().hour
        if now_hour > 1:
            assert 1 in failed


# ─── HourlySalesDetailCollector Tests ─────────────────────

class TestHourlySalesDetailCollector:

    def test_parse_prdt3_response(self, sample_ssv_prdt3):
        """selPrdT3 SSV 응답 파싱"""
        from src.collectors.hourly_sales_detail_collector import (
            HourlySalesDetailCollector,
        )
        collector = HourlySalesDetailCollector()
        result = collector._parse_prdt3_response(sample_ssv_prdt3)

        assert len(result) == 3
        assert result[0]['item_cd'] == '8801234567890'
        assert result[0]['item_nm'] == 'CU 삼각김밥'
        assert result[0]['sale_qty'] == 5
        assert result[0]['sale_amt'] == 5500.0
        assert result[0]['receipt_amt'] == 5000.0
        assert result[0]['rate'] == 25.0
        assert result[0]['month_evt'] == '1+1'

    def test_parse_prdt3_empty_response(self, sample_ssv_prdt3_empty):
        """빈 SSV 응답 → 빈 리스트"""
        from src.collectors.hourly_sales_detail_collector import (
            HourlySalesDetailCollector,
        )
        collector = HourlySalesDetailCollector()
        result = collector._parse_prdt3_response(sample_ssv_prdt3_empty)
        assert result == []

    def test_parse_prdt3_invalid(self):
        """잘못된 SSV 응답"""
        from src.collectors.hourly_sales_detail_collector import (
            HourlySalesDetailCollector,
        )
        collector = HourlySalesDetailCollector()
        assert collector._parse_prdt3_response("") == []
        assert collector._parse_prdt3_response("not-ssv") == []

    def test_parse_prdt3_skip_zero_items(self):
        """sale_qty=0, sale_amt=0인 품목 건너뛰기"""
        from src.collectors.hourly_sales_detail_collector import (
            HourlySalesDetailCollector,
        )
        cols = (
            f"_RowType_{US}ITEM_CD:string(256){US}ITEM_NM:string(256)"
            f"{US}SALE_QTY:bigdecimal(0){US}RECT_AMT:bigdecimal(0)"
            f"{US}G_RECT_AMT:bigdecimal(0){US}RATE:bigdecimal(0)"
            f"{US}MONTH_EVT:string(256){US}ORD_ITEM:string(256)"
        )
        row_zero = f"N{US}8800000000000{US}빈상품{US}0{US}0{US}0{US}0{US}{US}"
        row_valid = f"N{US}8811111111111{US}유효상품{US}2{US}3000{US}2800{US}10{US}{US}"

        ssv = f"SSV:UTF-8{RS}Dataset:dsList{RS}{cols}{RS}{row_zero}{RS}{row_valid}"

        collector = HourlySalesDetailCollector()
        result = collector._parse_prdt3_response(ssv)
        assert len(result) == 1
        assert result[0]['item_cd'] == '8811111111111'

    def test_replace_params_date_and_hour(self, template_body):
        """날짜 + 시간대 파라미터 치환"""
        from src.collectors.hourly_sales_detail_collector import (
            HourlySalesDetailCollector,
        )
        collector = HourlySalesDetailCollector()
        result = collector._replace_params(template_body, '20260310', 18)

        assert 'calFromDay=20260310' in result
        assert 'calToDay=20260310' in result
        assert 'strYmd=20260310' in result
        assert 'strTime=18' in result
        # calFromDay/calToDay/strYmd 치환 확인 (strFromYmd/strToYmd는 미치환)
        assert 'calFromDay=20260307' not in result

    def test_replace_params_single_digit_hour(self, template_body):
        """한 자리 시간(0~9) → 2자리 패딩"""
        from src.collectors.hourly_sales_detail_collector import (
            HourlySalesDetailCollector,
        )
        collector = HourlySalesDetailCollector()
        result = collector._replace_params(template_body, '20260310', 5)
        assert 'strTime=05' in result

    def test_replace_date_only(self, template_body):
        """날짜만 치환 (heartbeat용)"""
        from src.collectors.hourly_sales_detail_collector import (
            HourlySalesDetailCollector,
        )
        collector = HourlySalesDetailCollector()
        result = collector._replace_date_only(template_body, '20260315')
        assert 'calFromDay=20260315' in result
        assert 'calToDay=20260315' in result
        assert 'strYmd=20260315' in result
        # 시간대는 변경되지 않음
        assert 'strHms=12' in result

    def test_safe_float(self):
        """안전한 float 변환"""
        from src.collectors.hourly_sales_detail_collector import (
            HourlySalesDetailCollector,
        )
        assert HourlySalesDetailCollector._safe_float("123.45") == 123.45
        assert HourlySalesDetailCollector._safe_float("") == 0.0
        assert HourlySalesDetailCollector._safe_float(None) == 0.0
        assert HourlySalesDetailCollector._safe_float("abc") == 0.0

    def test_safe_int(self):
        """안전한 int 변환"""
        from src.collectors.hourly_sales_detail_collector import (
            HourlySalesDetailCollector,
        )
        assert HourlySalesDetailCollector._safe_int("42") == 42
        assert HourlySalesDetailCollector._safe_int("42.7") == 42
        assert HourlySalesDetailCollector._safe_int("") == 0
        assert HourlySalesDetailCollector._safe_int(None) == 0
        assert HourlySalesDetailCollector._safe_int("xyz") == 0

    def test_collect_hour_no_driver(self):
        """드라이버 없으면 빈 리스트"""
        from src.collectors.hourly_sales_detail_collector import (
            HourlySalesDetailCollector,
        )
        collector = HourlySalesDetailCollector(driver=None)
        result = collector.collect_hour('20260307', 12)
        assert result == []

    def test_collect_hour_invalid_date(self):
        """잘못된 날짜 → 빈 리스트"""
        from src.collectors.hourly_sales_detail_collector import (
            HourlySalesDetailCollector,
        )
        collector = HourlySalesDetailCollector()
        assert collector.collect_hour('2026', 12) == []

    def test_collect_hour_invalid_hour(self):
        """잘못된 시간 → 빈 리스트"""
        from src.collectors.hourly_sales_detail_collector import (
            HourlySalesDetailCollector,
        )
        collector = HourlySalesDetailCollector()
        assert collector.collect_hour('20260307', 25) == []
        assert collector.collect_hour('20260307', -1) == []

    def test_failed_queue(self):
        """실패 큐 추가 및 카운트"""
        from src.collectors.hourly_sales_detail_collector import (
            HourlySalesDetailCollector,
        )
        collector = HourlySalesDetailCollector()
        assert collector.failed_count == 0

        collector._failed_queue.append(('20260307', 12))
        collector._failed_queue.append(('20260307', 13))
        assert collector.failed_count == 2

    def test_check_heartbeat_within_time(self):
        """55분 이내 → heartbeat 스킵"""
        from src.collectors.hourly_sales_detail_collector import (
            HourlySalesDetailCollector,
        )
        collector = HourlySalesDetailCollector()
        collector._last_heartbeat = time.time()  # 방금 heartbeat

        with patch.object(collector, 'send_heartbeat') as mock_hb:
            collector._check_heartbeat()
            mock_hb.assert_not_called()

    def test_check_heartbeat_expired(self):
        """55분 초과 → heartbeat 호출"""
        from src.collectors.hourly_sales_detail_collector import (
            HourlySalesDetailCollector,
        )
        collector = HourlySalesDetailCollector()
        collector._last_heartbeat = time.time() - 56 * 60  # 56분 전

        with patch.object(collector, 'send_heartbeat') as mock_hb:
            collector._check_heartbeat()
            mock_hb.assert_called_once()

    def test_backfill_stats_structure(self):
        """backfill 반환값 구조 확인"""
        from src.collectors.hourly_sales_detail_collector import (
            HourlySalesDetailCollector,
        )
        collector = HourlySalesDetailCollector()

        # collect_all_hours 모킹 → 빈 결과
        with patch.object(collector, 'collect_all_hours', return_value={}):
            stats = collector.backfill(
                days=2,
                delay_per_hour=0,
                delay_per_day=0,
            )
            assert 'total_days' in stats
            assert 'success_days' in stats
            assert 'total_items' in stats
            assert stats['total_days'] >= 2

    def test_backfill_skip_collected(self):
        """이미 수집된 날짜 건너뛰기"""
        from src.collectors.hourly_sales_detail_collector import (
            HourlySalesDetailCollector,
        )
        collector = HourlySalesDetailCollector()

        # 모든 시간대 수집 완료로 체커 설정
        def all_collected(date_str):
            return list(range(24))

        with patch.object(collector, 'collect_all_hours', return_value={}) as mock_cah:
            stats = collector.backfill(
                days=3,
                delay_per_hour=0,
                delay_per_day=0,
                skip_collected=True,
                collected_checker=all_collected,
            )
            # collect_all_hours가 호출되지 않아야 함 (전부 건너뜀)
            mock_cah.assert_not_called()
            assert stats['total_days'] >= 3

    def test_retry_failed_empty_queue(self):
        """빈 실패 큐 → 0 반환"""
        from src.collectors.hourly_sales_detail_collector import (
            HourlySalesDetailCollector,
        )
        collector = HourlySalesDetailCollector()
        assert collector.retry_failed() == 0


# ─── HourlyDetailService Tests ───────────────────────────

class TestHourlyDetailService:

    def test_collect_previous_hour_normal(self):
        """정상 시간대 (13시 → 12시 수집)"""
        from src.application.services.hourly_detail_service import (
            HourlyDetailService,
        )

        with patch.object(HourlyDetailService, '__init__', lambda self, **kw: None):
            service = HourlyDetailService(store_id="99999")
            service.store_id = "99999"
            service.repo = MagicMock()
            service._collector = None
            service._driver = None

        mock_now = datetime(2026, 3, 7, 13, 10, 0)
        with patch('src.application.services.hourly_detail_service.datetime') as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

            with patch.object(service, 'collect_hour', return_value=5) as mock_ch:
                result = service.collect_previous_hour()
                mock_ch.assert_called_once_with('2026-03-07', 12, None)
                assert result == 5

    def test_collect_previous_hour_midnight(self):
        """자정 직후 (0시 → 어제 23시 수집)"""
        from src.application.services.hourly_detail_service import (
            HourlyDetailService,
        )

        with patch.object(HourlyDetailService, '__init__', lambda self, **kw: None):
            service = HourlyDetailService(store_id="99999")
            service.store_id = "99999"
            service.repo = MagicMock()
            service._collector = None
            service._driver = None

        mock_now = datetime(2026, 3, 8, 0, 10, 0)
        with patch('src.application.services.hourly_detail_service.datetime') as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

            with patch.object(service, 'collect_hour', return_value=3) as mock_ch:
                result = service.collect_previous_hour()
                mock_ch.assert_called_once_with('2026-03-07', 23, None)
                assert result == 3

    def test_collect_all_hours_skip_collected(self):
        """이미 수집된 시간대 건너뛰기"""
        from src.application.services.hourly_detail_service import (
            HourlyDetailService,
        )

        with patch.object(HourlyDetailService, '__init__', lambda self, **kw: None):
            service = HourlyDetailService(store_id="99999")
            service.store_id = "99999"
            service.repo = MagicMock()
            service._collector = MagicMock()
            service._driver = None

        # 0~11시 이미 수집, 과거 날짜
        service.repo.get_collected_hours.return_value = list(range(12))

        mock_now = datetime(2026, 3, 7, 15, 0, 0)
        with patch('src.application.services.hourly_detail_service.datetime') as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

            # collect_all_hours 호출 시 12~23시만 대상
            service._collector.collect_all_hours.return_value = {
                12: [{'item_cd': 'A'}],
                13: [{'item_cd': 'B'}],
            }

            result = service.collect_all_hours('2026-03-06')
            # 12~23시(12시간) 수집 요청
            call_args = service._collector.collect_all_hours.call_args
            target_hours = call_args[1].get('hours') or call_args[0][1]
            assert all(h >= 12 for h in target_hours)

    def test_collect_all_hours_all_done(self):
        """전부 수집 완료 → 빈 결과"""
        from src.application.services.hourly_detail_service import (
            HourlyDetailService,
        )

        with patch.object(HourlyDetailService, '__init__', lambda self, **kw: None):
            service = HourlyDetailService(store_id="99999")
            service.store_id = "99999"
            service.repo = MagicMock()
            service._collector = MagicMock()
            service._driver = None

        service.repo.get_collected_hours.return_value = list(range(24))

        result = service.collect_all_hours('2026-03-06')
        assert result == {'collected': 0, 'items': 0, 'failed': 0}

    def test_backfill_iterates_dates_and_saves(self):
        """backfill이 날짜별 순회하여 collect_all_hours → DB 저장"""
        from src.application.services.hourly_detail_service import (
            HourlyDetailService,
        )

        with patch.object(HourlyDetailService, '__init__', lambda self, **kw: None):
            service = HourlyDetailService(store_id="99999")
            service.store_id = "99999"
            service.repo = MagicMock()
            service._collector = MagicMock()
            service._driver = None

        # collect_all_hours가 호출될 때 빈 결과 반환하도록 설정
        service.repo.get_collected_hours.return_value = []

        # collect_all_hours를 모킹 (service 메서드)
        with patch.object(service, 'collect_all_hours') as mock_cah, \
             patch.object(service, '_preflight_selday'), \
             patch('src.application.services.hourly_detail_service._time'):
            mock_cah.return_value = {
                'collected': 24, 'items': 50, 'failed': 0
            }
            stats = service.backfill(days=3)

        # 3일분(어제부터 3일 전까지) 호출 확인
        assert mock_cah.call_count >= 3
        assert stats['total_days'] >= 3
        assert stats['total_items'] >= 0

    def test_send_heartbeat_delegates(self):
        """heartbeat가 collector에 위임"""
        from src.application.services.hourly_detail_service import (
            HourlyDetailService,
        )

        with patch.object(HourlyDetailService, '__init__', lambda self, **kw: None):
            service = HourlyDetailService(store_id="99999")
            service.store_id = "99999"
            service.repo = MagicMock()
            service._collector = MagicMock()
            service._driver = None

        service._collector.send_heartbeat.return_value = True
        assert service.send_heartbeat() is True
        service._collector.send_heartbeat.assert_called_once()


# ─── 통합 플로우 테스트 ──────────────────────────────────

class TestIntegrationFlow:

    def test_parse_and_save_flow(self, repo, sample_ssv_prdt3):
        """SSV 파싱 → DB 저장 → 조회 전체 플로우"""
        from src.collectors.hourly_sales_detail_collector import (
            HourlySalesDetailCollector,
        )

        collector = HourlySalesDetailCollector()
        items = collector._parse_prdt3_response(sample_ssv_prdt3)
        assert len(items) == 3

        # raw 저장
        repo.save_raw('2026-03-07', 12, sample_ssv_prdt3, len(items))

        # detail 저장
        saved = repo.save_detail('2026-03-07', 12, items)
        assert saved == 3

        # 조회 확인
        result = repo.get_hourly_detail('2026-03-07', 12)
        assert len(result) == 3
        item_cds = {r['item_cd'] for r in result}
        assert '8801234567890' in item_cds
        assert '8809876543210' in item_cds
        assert '8805551112222' in item_cds

    def test_multi_hour_collection_and_summary(self, repo, sample_items):
        """여러 시간대 수집 → 일별 집계"""
        # 10시, 12시, 14시 데이터 저장
        repo.save_detail('2026-03-07', 10, sample_items)
        repo.save_detail('2026-03-07', 12, sample_items)
        repo.save_detail('2026-03-07', 14, sample_items[:1])  # 삼각김밥(qty=5)

        # 수집 시간대 확인
        collected = repo.get_collected_hours('2026-03-07')
        assert collected == [10, 12, 14]

        # 일별 집계
        summary = repo.get_daily_item_summary('2026-03-07')
        kimbap = [s for s in summary if s['item_cd'] == '8801234567890'][0]
        assert kimbap['total_qty'] == 15  # 5 + 5 + 5(14시)
        assert kimbap['active_hours'] == 3
        assert kimbap['first_hour'] == 10
        assert kimbap['last_hour'] == 14

    def test_month_evt_preserved(self, repo, sample_items):
        """행사명(MONTH_EVT) 보존 확인"""
        repo.save_detail('2026-03-07', 12, sample_items)
        result = repo.get_hourly_detail('2026-03-07', 12)

        evt_map = {r['item_cd']: r['month_evt'] for r in result}
        assert evt_map['8801234567890'] == '1+1'
        assert evt_map['8809876543210'] == ''
        assert evt_map['8805551112222'] == '할인'
