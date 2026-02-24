"""
폐기 검증 시스템 테스트

테스트 대상:
  - WasteSlipRepository: waste_slip_items CRUD
  - WasteVerificationReporter: 비교 보고서 생성
  - WasteVerificationService: verify_date_deep
  - FoodWasteRateCalibrator: waste_slip_items 소스 전환
"""

import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ================================================================
# WasteSlipRepository 테스트
# ================================================================


class TestWasteSlipRepositorySaveItems:
    """waste_slip_items 저장 테스트"""

    def test_save_waste_slip_items_basic(self, in_memory_db):
        """기본 품목 저장"""
        with patch(
            "src.infrastructure.database.base_repository.BaseRepository._get_conn",
            return_value=in_memory_db,
        ), patch(
            "src.infrastructure.database.base_repository.BaseRepository._now",
            return_value="2026-02-19 08:00:00",
        ):
            from src.infrastructure.database.repos.waste_slip_repo import (
                WasteSlipRepository,
            )

            repo = WasteSlipRepository(store_id="46513")
            items = [
                {
                    "CHIT_YMD": "20260218",
                    "CHIT_NO": "10044420011",
                    "CHIT_SEQ": 1,
                    "ITEM_CD": "8809899722116",
                    "ITEM_NM": "뉴전주비빔소불고기",
                    "LARGE_CD": "001",
                    "LARGE_NM": "도시락",
                    "QTY": 1,
                    "WONGA_PRICE": 3089,
                    "WONGA_AMT": 3089,
                    "MAEGA_PRICE": 5500,
                    "MAEGA_AMT": 5500,
                    "CUST_NM": "OO식품",
                    "CENTER_NM": "CU물류",
                },
                {
                    "CHIT_YMD": "20260218",
                    "CHIT_NO": "10044420011",
                    "CHIT_SEQ": 2,
                    "ITEM_CD": "8801062895847",
                    "ITEM_NM": "통통이오란다쿠키슈",
                    "LARGE_CD": "002",
                    "LARGE_NM": "빵/디저트류",
                    "QTY": 1,
                    "WONGA_PRICE": 1456,
                    "WONGA_AMT": 1456,
                    "MAEGA_PRICE": 2500,
                    "MAEGA_AMT": 2500,
                },
            ]

            saved = repo.save_waste_slip_items(items, "46513")
            assert saved == 2

            # DB 확인
            rows = in_memory_db.execute(
                "SELECT * FROM waste_slip_items ORDER BY chit_seq"
            ).fetchall()
            assert len(rows) == 2
            assert dict(rows[0])["item_cd"] == "8809899722116"
            assert dict(rows[0])["chit_date"] == "2026-02-18"
            assert dict(rows[0])["qty"] == 1
            assert dict(rows[0])["wonga_amt"] == 3089.0
            assert dict(rows[1])["item_cd"] == "8801062895847"

    def test_save_waste_slip_items_upsert(self, in_memory_db):
        """UPSERT (INSERT OR REPLACE) 동작"""
        with patch(
            "src.infrastructure.database.base_repository.BaseRepository._get_conn",
            return_value=in_memory_db,
        ), patch(
            "src.infrastructure.database.base_repository.BaseRepository._now",
            return_value="2026-02-19 08:00:00",
        ):
            from src.infrastructure.database.repos.waste_slip_repo import (
                WasteSlipRepository,
            )

            repo = WasteSlipRepository(store_id="46513")

            # 첫 저장
            items1 = [
                {
                    "CHIT_YMD": "20260218",
                    "CHIT_NO": "10044420011",
                    "ITEM_CD": "8809899722116",
                    "QTY": 1,
                    "WONGA_AMT": 3089,
                },
            ]
            repo.save_waste_slip_items(items1, "46513")

            # 같은 키로 재저장 (수량 변경)
            items2 = [
                {
                    "CHIT_YMD": "20260218",
                    "CHIT_NO": "10044420011",
                    "ITEM_CD": "8809899722116",
                    "QTY": 2,
                    "WONGA_AMT": 6178,
                },
            ]
            repo.save_waste_slip_items(items2, "46513")

            rows = in_memory_db.execute(
                "SELECT * FROM waste_slip_items"
            ).fetchall()
            assert len(rows) == 1  # UPSERT이므로 1건만
            assert dict(rows[0])["qty"] == 2
            assert dict(rows[0])["wonga_amt"] == 6178.0

    def test_save_waste_slip_items_decimal_dict(self, in_memory_db):
        """Decimal dict {hi: value} 처리"""
        with patch(
            "src.infrastructure.database.base_repository.BaseRepository._get_conn",
            return_value=in_memory_db,
        ), patch(
            "src.infrastructure.database.base_repository.BaseRepository._now",
            return_value="2026-02-19 08:00:00",
        ):
            from src.infrastructure.database.repos.waste_slip_repo import (
                WasteSlipRepository,
            )

            repo = WasteSlipRepository(store_id="46513")
            items = [
                {
                    "CHIT_YMD": "20260218",
                    "CHIT_NO": "10044420011",
                    "ITEM_CD": "8809899722116",
                    "QTY": {"hi": 3},
                    "WONGA_AMT": {"hi": 9267},
                    "MAEGA_AMT": {"hi": 16500},
                },
            ]

            saved = repo.save_waste_slip_items(items, "46513")
            assert saved == 1

            row = dict(
                in_memory_db.execute(
                    "SELECT * FROM waste_slip_items"
                ).fetchone()
            )
            assert row["qty"] == 3
            assert row["wonga_amt"] == 9267.0
            assert row["maega_amt"] == 16500.0

    def test_save_empty_items(self, in_memory_db):
        """빈 목록 저장"""
        with patch(
            "src.infrastructure.database.base_repository.BaseRepository._get_conn",
            return_value=in_memory_db,
        ):
            from src.infrastructure.database.repos.waste_slip_repo import (
                WasteSlipRepository,
            )

            repo = WasteSlipRepository(store_id="46513")
            assert repo.save_waste_slip_items([], "46513") == 0

    def test_skip_items_without_item_cd(self, in_memory_db):
        """item_cd 없는 항목은 스킵"""
        with patch(
            "src.infrastructure.database.base_repository.BaseRepository._get_conn",
            return_value=in_memory_db,
        ), patch(
            "src.infrastructure.database.base_repository.BaseRepository._now",
            return_value="2026-02-19 08:00:00",
        ):
            from src.infrastructure.database.repos.waste_slip_repo import (
                WasteSlipRepository,
            )

            repo = WasteSlipRepository(store_id="46513")
            items = [
                {"CHIT_YMD": "20260218", "CHIT_NO": "123", "ITEM_CD": ""},
                {"CHIT_YMD": "20260218", "CHIT_NO": "123", "QTY": 1},
            ]
            saved = repo.save_waste_slip_items(items, "46513")
            assert saved == 0


class TestWasteSlipRepositoryGetItems:
    """waste_slip_items 조회 테스트"""

    def _insert_test_items(self, conn):
        """테스트 데이터 삽입"""
        conn.execute("""
            INSERT INTO waste_slip_items
            (store_id, chit_date, chit_no, chit_seq, item_cd, item_nm,
             large_cd, large_nm, qty, wonga_price, wonga_amt,
             maega_price, maega_amt, cust_nm, center_nm,
             created_at, updated_at)
            VALUES
            ('46513', '2026-02-18', '10044420011', 1, '8809899722116',
             '뉴전주비빔소불고기', '001', '도시락', 1, 3089, 3089,
             5500, 5500, 'OO식품', 'CU물류', '2026-02-19', '2026-02-19'),
            ('46513', '2026-02-18', '10044420012', 1, '8809899722116',
             '뉴전주비빔소불고기', '001', '도시락', 2, 3089, 6178,
             5500, 11000, 'OO식품', 'CU물류', '2026-02-19', '2026-02-19'),
            ('46513', '2026-02-18', '10044420011', 2, '8801062895847',
             '통통이오란다쿠키슈', '002', '빵/디저트류', 1, 1456, 1456,
             2500, 2500, '', '', '2026-02-19', '2026-02-19'),
            ('46513', '2026-02-19', '10044420020', 1, '8809899722161',
             '정성가득9첩한상', '001', '도시락', 1, 3539, 3539,
             6500, 6500, '', 'CU물류', '2026-02-19', '2026-02-19')
        """)
        conn.commit()

    def test_get_waste_slip_items_by_date(self, in_memory_db):
        """날짜별 조회"""
        self._insert_test_items(in_memory_db)

        with patch(
            "src.infrastructure.database.base_repository.BaseRepository._get_conn",
            return_value=in_memory_db,
        ):
            from src.infrastructure.database.repos.waste_slip_repo import (
                WasteSlipRepository,
            )

            repo = WasteSlipRepository(store_id="46513")
            items = repo.get_waste_slip_items("2026-02-18", "46513")
            assert len(items) == 3

            items_19 = repo.get_waste_slip_items("2026-02-19", "46513")
            assert len(items_19) == 1
            assert items_19[0]["item_cd"] == "8809899722161"

    def test_get_waste_slip_items_summary(self, in_memory_db):
        """상품코드별 합산 요약"""
        self._insert_test_items(in_memory_db)

        with patch(
            "src.infrastructure.database.base_repository.BaseRepository._get_conn",
            return_value=in_memory_db,
        ):
            from src.infrastructure.database.repos.waste_slip_repo import (
                WasteSlipRepository,
            )

            repo = WasteSlipRepository(store_id="46513")
            summary = repo.get_waste_slip_items_summary(
                "2026-02-18", "46513"
            )
            assert len(summary) == 2  # 2개 고유 상품

            # 8809899722116: 전표 2건에 걸쳐 총 qty=3
            item_116 = next(
                s for s in summary
                if s["item_cd"] == "8809899722116"
            )
            assert item_116["total_qty"] == 3
            assert item_116["total_wonga"] == 9267.0  # 3089 + 6178
            assert item_116["slip_count"] == 2  # 2개 전표

            # 8801062895847: 1건
            item_847 = next(
                s for s in summary
                if s["item_cd"] == "8801062895847"
            )
            assert item_847["total_qty"] == 1
            assert item_847["slip_count"] == 1

    def test_get_waste_slip_items_empty_date(self, in_memory_db):
        """데이터 없는 날짜 조회"""
        with patch(
            "src.infrastructure.database.base_repository.BaseRepository._get_conn",
            return_value=in_memory_db,
        ):
            from src.infrastructure.database.repos.waste_slip_repo import (
                WasteSlipRepository,
            )

            repo = WasteSlipRepository(store_id="46513")
            items = repo.get_waste_slip_items("2026-01-01", "46513")
            assert items == []


# ================================================================
# WasteVerificationReporter 테스트
# ================================================================


class TestWasteVerificationReporter:
    """비교 보고서 테스트"""

    def _setup_test_data(self, conn):
        """테스트 데이터 설정"""
        # waste_slip_items
        conn.execute("""
            INSERT INTO waste_slip_items
            (store_id, chit_date, chit_no, chit_seq, item_cd, item_nm,
             large_nm, qty, wonga_amt, maega_amt, created_at)
            VALUES
            ('46513', '2026-02-18', '100', 1, 'ITEM_A', '상품A',
             '도시락', 1, 3000, 5000, '2026-02-19'),
            ('46513', '2026-02-18', '100', 2, 'ITEM_B', '상품B',
             '빵류', 2, 2000, 4000, '2026-02-19'),
            ('46513', '2026-02-18', '101', 1, 'ITEM_C', '상품C',
             '유음료', 1, 1500, 2500, '2026-02-19')
        """)

        # daily_sales (disuse_qty > 0) - daily_sales에는 item_nm 없음
        conn.execute("""
            INSERT INTO daily_sales
            (sales_date, item_cd, mid_cd, sale_qty, disuse_qty,
             store_id)
            VALUES
            ('2026-02-18', 'ITEM_A', '001', 5, 1, '46513'),
            ('2026-02-18', 'ITEM_D', '002', 3, 1, '46513')
        """)
        conn.commit()

    def test_comparison_matched(self, in_memory_db):
        """매칭 상품 비교"""
        self._setup_test_data(in_memory_db)

        with patch(
            "src.infrastructure.database.base_repository.BaseRepository._get_conn",
            return_value=in_memory_db,
        ):
            from src.report.waste_verification_reporter import (
                WasteVerificationReporter,
            )

            reporter = WasteVerificationReporter(store_id="46513")
            data = reporter.get_comparison_data("2026-02-18")

            summary = data["summary"]
            comparison = data["comparison"]

            # ITEM_A: 전표 + 추적 모두 존재
            assert summary["matched"] == 1
            matched = comparison["matched"]
            assert len(matched) == 1
            assert matched[0]["item_cd"] == "ITEM_A"
            assert matched[0]["slip_qty"] == 1
            assert matched[0]["tracking_qty"] == 1

    def test_comparison_slip_only(self, in_memory_db):
        """전표에만 있는 상품 (추적 누락)"""
        self._setup_test_data(in_memory_db)

        with patch(
            "src.infrastructure.database.base_repository.BaseRepository._get_conn",
            return_value=in_memory_db,
        ):
            from src.report.waste_verification_reporter import (
                WasteVerificationReporter,
            )

            reporter = WasteVerificationReporter(store_id="46513")
            data = reporter.get_comparison_data("2026-02-18")

            slip_only = data["comparison"]["slip_only"]
            # ITEM_B, ITEM_C: 전표에만 있음
            assert len(slip_only) == 2
            codes = {i["item_cd"] for i in slip_only}
            assert "ITEM_B" in codes
            assert "ITEM_C" in codes

    def test_comparison_tracking_only(self, in_memory_db):
        """추적에만 있는 상품 (전표 미반영)"""
        self._setup_test_data(in_memory_db)

        with patch(
            "src.infrastructure.database.base_repository.BaseRepository._get_conn",
            return_value=in_memory_db,
        ):
            from src.report.waste_verification_reporter import (
                WasteVerificationReporter,
            )

            reporter = WasteVerificationReporter(store_id="46513")
            data = reporter.get_comparison_data("2026-02-18")

            tracking_only = data["comparison"]["tracking_only"]
            # ITEM_D: 추적에만 있음
            assert len(tracking_only) == 1
            assert tracking_only[0]["item_cd"] == "ITEM_D"

    def test_miss_rate_calculation(self, in_memory_db):
        """누락율 계산"""
        self._setup_test_data(in_memory_db)

        with patch(
            "src.infrastructure.database.base_repository.BaseRepository._get_conn",
            return_value=in_memory_db,
        ):
            from src.report.waste_verification_reporter import (
                WasteVerificationReporter,
            )

            reporter = WasteVerificationReporter(store_id="46513")
            data = reporter.get_comparison_data("2026-02-18")

            summary = data["summary"]
            # 전표 3건, 매칭 1건, 전표만 2건 -> 누락율 66.7%
            assert summary["slip_count"] == 3
            assert summary["slip_only"] == 2
            assert summary["miss_rate"] == 66.7

    def test_report_file_creation(self, in_memory_db):
        """보고서 파일 생성"""
        self._setup_test_data(in_memory_db)

        with patch(
            "src.infrastructure.database.base_repository.BaseRepository._get_conn",
            return_value=in_memory_db,
        ):
            from src.report.waste_verification_reporter import (
                WasteVerificationReporter,
            )

            reporter = WasteVerificationReporter(store_id="46513")
            # 임시 디렉토리 사용
            with tempfile.TemporaryDirectory() as tmpdir:
                reporter._log_dir = Path(tmpdir)
                filepath = reporter.generate_daily_report("2026-02-18")

                assert filepath is not None
                assert os.path.exists(filepath)

                # 파일 내용 확인
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()

                assert "BGF 폐기 검증 보고서" in content
                assert "2026-02-18" in content
                assert "전표 상세 품목" in content
                assert "추적모듈 데이터" in content
                assert "비교 결과" in content
                assert "요약" in content
                assert "ITEM_A" in content


# ================================================================
# WasteVerificationService 테스트
# ================================================================


class TestWasteVerificationServiceDeep:
    """verify_date_deep 테스트"""

    def _setup_test_data(self, conn):
        """테스트 데이터"""
        # waste_slips (헤더)
        conn.execute("""
            INSERT INTO waste_slips
            (store_id, chit_date, chit_no, chit_flag, chit_id,
             chit_id_nm, item_cnt, wonga_amt, maega_amt, created_at)
            VALUES
            ('46513', '2026-02-18', '100', '1', '04', '폐기',
             2, 5000, 9000, '2026-02-19')
        """)

        # waste_slip_items (상세)
        conn.execute("""
            INSERT INTO waste_slip_items
            (store_id, chit_date, chit_no, chit_seq, item_cd, item_nm,
             large_nm, qty, wonga_amt, maega_amt, created_at)
            VALUES
            ('46513', '2026-02-18', '100', 1, 'A001', '상품1',
             '도시락', 1, 3000, 5000, '2026-02-19'),
            ('46513', '2026-02-18', '100', 2, 'A002', '상품2',
             '빵류', 1, 2000, 4000, '2026-02-19')
        """)

        # daily_sales (item_nm 없음)
        conn.execute("""
            INSERT INTO daily_sales
            (sales_date, item_cd, mid_cd, sale_qty, disuse_qty,
             store_id)
            VALUES
            ('2026-02-18', 'A001', '001', 5, 1, '46513')
        """)

        # waste_verification_log 테이블도 필요
        conn.commit()

    def test_verify_date_deep_basic(self, in_memory_db):
        """심층 검증 기본"""
        self._setup_test_data(in_memory_db)

        with patch(
            "src.infrastructure.database.base_repository.BaseRepository._get_conn",
            return_value=in_memory_db,
        ), patch(
            "src.infrastructure.database.base_repository.BaseRepository._now",
            return_value="2026-02-19 08:00:00",
        ):
            from src.application.services.waste_verification_service import (
                WasteVerificationService,
            )

            service = WasteVerificationService(store_id="46513")

            # 보고서 생성 없이 테스트
            result = service.verify_date_deep(
                "2026-02-18",
                generate_report=False,
                auto_save=True,
            )

            assert result["date"] == "2026-02-18"
            assert "level1" in result
            assert "level2_summary" in result
            assert result["report_path"] is None

            # Level 1 검증
            l1 = result["level1"]
            assert l1["slip_count"] == 1
            assert l1["slip_item_count"] == 2

            # Level 2 검증
            l2 = result["level2_summary"]
            assert l2["matched"] == 1  # A001 양쪽
            assert l2["slip_only"] == 1  # A002 전표만
            assert l2["tracking_only"] == 0

    def test_verify_date_deep_with_report(self, in_memory_db):
        """보고서 생성 포함"""
        self._setup_test_data(in_memory_db)

        with patch(
            "src.infrastructure.database.base_repository.BaseRepository._get_conn",
            return_value=in_memory_db,
        ), patch(
            "src.infrastructure.database.base_repository.BaseRepository._now",
            return_value="2026-02-19 08:00:00",
        ):
            from src.application.services.waste_verification_service import (
                WasteVerificationService,
            )

            service = WasteVerificationService(store_id="46513")

            with tempfile.TemporaryDirectory() as tmpdir:
                service.reporter._log_dir = Path(tmpdir)

                result = service.verify_date_deep(
                    "2026-02-18",
                    generate_report=True,
                    auto_save=False,
                )

                assert result["report_path"] is not None
                assert os.path.exists(result["report_path"])


# ================================================================
# FoodWasteRateCalibrator 소스 전환 테스트
# ================================================================


class TestCalibratorSlipSource:
    """FoodWasteRateCalibrator의 waste_slip_items 소스 전환"""

    def _setup_db(self, conn):
        """테스트 DB 설정"""
        # daily_sales (mid_cd별 판매+폐기)
        conn.execute("""
            INSERT INTO daily_sales
            (sales_date, item_cd, mid_cd, sale_qty, disuse_qty,
             store_id)
            VALUES
            ('2026-02-18', 'ITEM_X', '001', 10, 2, '46513'),
            ('2026-02-17', 'ITEM_X', '001', 8, 1, '46513'),
            ('2026-02-18', 'ITEM_Y', '001', 5, 0, '46513')
        """)

        # waste_slip_items (전표 기반 폐기)
        conn.execute("""
            INSERT INTO waste_slip_items
            (store_id, chit_date, chit_no, item_cd, item_nm,
             qty, wonga_amt, maega_amt, created_at)
            VALUES
            ('46513', '2026-02-18', '100', 'ITEM_X', '상품X',
             3, 9000, 15000, '2026-02-19'),
            ('46513', '2026-02-17', '99', 'ITEM_X', '상품X',
             2, 6000, 10000, '2026-02-19')
        """)
        conn.commit()

    @staticmethod
    def _copy_table(src_conn, dst_conn, table_name, where_clause=""):
        """테이블 데이터 복제 (동적 컬럼 수)"""
        query = f"SELECT * FROM {table_name}"
        if where_clause:
            query += f" WHERE {where_clause}"
        rows = src_conn.execute(query).fetchall()
        for row in rows:
            placeholders = ",".join(["?"] * len(tuple(row)))
            dst_conn.execute(
                f"INSERT INTO {table_name} VALUES ({placeholders})",
                tuple(row),
            )

    @staticmethod
    def _copy_schema(src_conn, dst_conn):
        """테이블 스키마 복제"""
        schema = src_conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table'"
        ).fetchall()
        for row in schema:
            if row[0]:
                try:
                    dst_conn.execute(row[0])
                except sqlite3.OperationalError:
                    pass

    def test_calibrator_slip_source_priority(self, in_memory_db):
        """waste_slip_items 데이터 우선 사용"""
        self._setup_db(in_memory_db)

        with tempfile.NamedTemporaryFile(
            suffix=".db", delete=False
        ) as tmp:
            tmp_path = tmp.name

        try:
            file_conn = sqlite3.connect(tmp_path)
            self._copy_schema(in_memory_db, file_conn)
            self._copy_table(in_memory_db, file_conn, "daily_sales")
            self._copy_table(in_memory_db, file_conn, "waste_slip_items")
            file_conn.commit()
            file_conn.close()

            from src.prediction.food_waste_calibrator import (
                FoodWasteRateCalibrator,
            )

            calibrator = FoodWasteRateCalibrator(
                store_id="46513", db_path=tmp_path
            )
            stats = calibrator._get_waste_stats("001")

            # waste_slip_items 데이터가 있으므로 slip_items 소스 사용
            assert stats["waste_source"] == "slip_items"
            # 전표 기반 폐기 합계: 3 + 2 = 5
            assert stats["total_waste"] == 5
            # 판매량은 daily_sales 기반
            assert stats["total_sold"] == 23  # 10 + 8 + 5

        finally:
            os.unlink(tmp_path)

    def test_calibrator_fallback_to_daily_sales(self, in_memory_db):
        """waste_slip_items 데이터 없을 때 daily_sales 폴백"""
        # daily_sales만 있고 waste_slip_items는 비어있는 경우
        in_memory_db.execute("""
            INSERT INTO daily_sales
            (sales_date, item_cd, mid_cd, sale_qty, disuse_qty,
             store_id)
            VALUES
            ('2026-02-18', 'ITEM_Z', '999', 10, 3, '46513')
        """)
        in_memory_db.commit()

        with tempfile.NamedTemporaryFile(
            suffix=".db", delete=False
        ) as tmp:
            tmp_path = tmp.name

        try:
            file_conn = sqlite3.connect(tmp_path)
            self._copy_schema(in_memory_db, file_conn)
            self._copy_table(
                in_memory_db, file_conn, "daily_sales",
                "mid_cd = '999'"
            )
            file_conn.commit()
            file_conn.close()

            from src.prediction.food_waste_calibrator import (
                FoodWasteRateCalibrator,
            )

            calibrator = FoodWasteRateCalibrator(
                store_id="46513", db_path=tmp_path
            )
            stats = calibrator._get_waste_stats("999")

            # waste_slip_items 없으므로 daily_sales 폴백
            assert stats["waste_source"] == "daily_sales"
            assert stats["total_waste"] == 3

        finally:
            os.unlink(tmp_path)
