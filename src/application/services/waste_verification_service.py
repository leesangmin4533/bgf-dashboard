"""
폐기 검증 서비스 (WasteVerificationService)

폐기 전표(waste_slips/waste_slip_items) vs 추적모듈 대조 검증

Level 1: 전표 헤더 건수 비교 (verify_date)
Level 2: 상품코드 매칭 (verify_date_deep)
Level 3: 매칭 상품의 수량 비교 + 비교 보고서 생성

원인:
  - daily_sales의 disuse_qty는 매출분석 > 중분류별 매출 화면에서 수집
  - 이 화면의 DISUSE_QTY가 불완전 (약 30%만 반영)
  - 정확한 폐기 데이터: 검수전표 > 통합 전표 조회 > 전표구분='폐기' > 팝업 상세 품목
"""

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.infrastructure.database.repos import SalesRepository
from src.infrastructure.database.repos.waste_slip_repo import WasteSlipRepository
from src.report.waste_verification_reporter import WasteVerificationReporter
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 불일치 판단 임계값
GAP_THRESHOLD_PCT = 10.0   # 10% 이상 차이이면 MISMATCH


class WasteVerificationService:
    """폐기 데이터 검증 서비스

    폐기 전표 데이터(공식)와 daily_sales.disuse_qty(매출분석)를 대조하여
    누락/불일치를 감지합니다.
    """

    def __init__(self, store_id: Optional[str] = None) -> None:
        self.store_id = store_id
        self.slip_repo = WasteSlipRepository(store_id=store_id)
        self.sales_repo = SalesRepository(store_id=store_id)
        self.reporter = WasteVerificationReporter(store_id=store_id)

    def verify_date(
        self,
        target_date: str,
        auto_save: bool = True,
    ) -> Dict[str, Any]:
        """특정 날짜의 폐기 데이터 검증

        Args:
            target_date: 검증 대상 날짜 (YYYY-MM-DD)
            auto_save: 검증 결과 자동 저장 여부

        Returns:
            {
                date, status, slip_count, slip_item_count,
                daily_sales_disuse_count, gap, gap_percentage
            }
        """
        # 1) 폐기 전표 데이터
        slip_data = self.slip_repo.get_waste_verification_data(
            target_date, self.store_id
        )
        slip_count = slip_data.get("slip_count", 0)
        slip_item_count = slip_data.get("item_count", 0)

        # 2) daily_sales 폐기 건수 (disuse_qty > 0)
        ds_disuse_count = self._get_daily_sales_disuse_count(target_date)

        # 3) 비교 및 판정
        if slip_item_count == 0 and ds_disuse_count == 0:
            status = "NO_DATA"
            gap = 0
            gap_pct = 0.0
        elif slip_item_count == 0:
            status = "SLIP_MISSING"
            gap = -ds_disuse_count
            gap_pct = -100.0
        elif ds_disuse_count == 0:
            status = "SALES_MISSING"
            gap = slip_item_count
            gap_pct = 100.0
        else:
            gap = slip_item_count - ds_disuse_count
            gap_pct = (gap / slip_item_count * 100) if slip_item_count > 0 else 0
            if abs(gap_pct) <= GAP_THRESHOLD_PCT:
                status = "OK"
            else:
                status = "MISMATCH"

        result = {
            "date": target_date,
            "status": status,
            "slip_count": slip_count,
            "slip_item_count": slip_item_count,
            "daily_sales_disuse_count": ds_disuse_count,
            "gap": gap,
            "gap_percentage": round(gap_pct, 1),
        }

        # 4) 검증 결과 저장
        if auto_save:
            try:
                self.slip_repo.save_verification_result(
                    verification_date=target_date,
                    slip_count=slip_count,
                    slip_item_count=slip_item_count,
                    daily_sales_disuse_count=ds_disuse_count,
                    gap=gap,
                    gap_percentage=round(gap_pct, 1),
                    status=status,
                    details=json.dumps(
                        {"wonga_total": slip_data.get("wonga_total", 0),
                         "maega_total": slip_data.get("maega_total", 0)},
                    ),
                    store_id=self.store_id,
                )
            except Exception as e:
                logger.warning(f"[Verify] 결과 저장 실패: {e}")

        return result

    def verify_range(
        self,
        from_date: str,
        to_date: str,
        auto_save: bool = True,
    ) -> Dict[str, Any]:
        """기간 전체 검증

        Args:
            from_date: 시작일 (YYYY-MM-DD)
            to_date: 종료일 (YYYY-MM-DD)
            auto_save: 결과 자동 저장 여부

        Returns:
            {
                summary: {total_days, ok, mismatch, no_data, ...},
                daily: [{date, status, ...}, ...]
            }
        """
        from_dt = datetime.strptime(from_date, "%Y-%m-%d")
        to_dt = datetime.strptime(to_date, "%Y-%m-%d")

        daily_results = []
        stats = {
            "total_days": 0,
            "OK": 0,
            "MISMATCH": 0,
            "NO_DATA": 0,
            "SLIP_MISSING": 0,
            "SALES_MISSING": 0,
            "total_slip_items": 0,
            "total_sales_disuse": 0,
            "total_gap": 0,
        }

        current = from_dt
        while current <= to_dt:
            date_str = current.strftime("%Y-%m-%d")
            result = self.verify_date(date_str, auto_save=auto_save)

            daily_results.append(result)
            stats["total_days"] += 1
            stats[result["status"]] = stats.get(result["status"], 0) + 1
            stats["total_slip_items"] += result.get("slip_item_count", 0)
            stats["total_sales_disuse"] += result.get(
                "daily_sales_disuse_count", 0
            )
            stats["total_gap"] += result.get("gap", 0)

            current += timedelta(days=1)

        # 전체 갭 비율
        if stats["total_slip_items"] > 0:
            stats["overall_gap_pct"] = round(
                stats["total_gap"] / stats["total_slip_items"] * 100, 1
            )
        else:
            stats["overall_gap_pct"] = 0

        logger.info(
            f"[Verify] 기간 검증 완료: {from_date}~{to_date} | "
            f"OK={stats['OK']} MISMATCH={stats['MISMATCH']} "
            f"갭={stats['total_gap']}건 ({stats['overall_gap_pct']}%)"
        )

        return {"summary": stats, "daily": daily_results}

    def verify_date_deep(
        self,
        target_date: str,
        generate_report: bool = True,
        auto_save: bool = True,
    ) -> Dict[str, Any]:
        """전표 vs 추적모듈 심층 검증 (Level 1-3)

        Level 1: 기존 헤더 건수 비교
        Level 2: 상품코드 매칭 (waste_slip_items vs tracking)
        Level 3: 매칭 상품의 수량 비교 + 비교 보고서 생성

        Args:
            target_date: 검증 대상 날짜 (YYYY-MM-DD)
            generate_report: 비교 보고서 생성 여부
            auto_save: 검증 결과 자동 저장 여부

        Returns:
            {date, level1, level2_summary, report_path, ...}
        """
        # Level 1: 기존 헤더 검증
        level1 = self.verify_date(target_date, auto_save=auto_save)

        # Level 2-3: 상세 품목 비교
        try:
            comparison_data = self.reporter.get_comparison_data(
                target_date
            )
            summary = comparison_data.get("summary", {})
        except Exception as e:
            logger.warning(f"[VerifyDeep] 상세 비교 실패: {e}")
            summary = {
                "slip_count": 0,
                "tracking_count": 0,
                "matched": 0,
                "slip_only": 0,
                "tracking_only": 0,
                "miss_rate": 0,
            }

        # 보고서 생성
        report_path = None
        if generate_report:
            try:
                report_path = self.reporter.generate_daily_report(
                    target_date
                )
            except Exception as e:
                logger.warning(f"[VerifyDeep] 보고서 생성 실패: {e}")

        result = {
            "date": target_date,
            "level1": level1,
            "level2_summary": summary,
            "report_path": report_path,
        }

        logger.info(
            f"[VerifyDeep] {target_date}: "
            f"L1={level1.get('status')} | "
            f"매칭={summary.get('matched', 0)} "
            f"전표만={summary.get('slip_only', 0)} "
            f"추적만={summary.get('tracking_only', 0)} "
            f"누락율={summary.get('miss_rate', 0)}%"
            + (f" | 보고서={report_path}" if report_path else "")
        )

        return result

    def _get_daily_sales_disuse_count(self, target_date: str) -> int:
        """daily_sales에서 특정 날짜의 폐기 건수 (disuse_qty > 0인 행 수)"""
        try:
            conn = self.sales_repo._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) FROM daily_sales
                WHERE sales_date = ?
                  AND COALESCE(disuse_qty, 0) > 0
                """,
                (target_date,),
            )
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.warning(f"[Verify] daily_sales 조회 실패: {e}")
            return 0

    def get_verification_history(
        self, days: int = 30
    ) -> List[Dict[str, Any]]:
        """최근 검증 이력 조회"""
        return self.slip_repo.get_verification_history(
            days=days, store_id=self.store_id
        )

    def get_gap_summary(
        self,
        from_date: str,
        to_date: str,
    ) -> Dict[str, Any]:
        """전표 vs 매출분석 갭 요약"""
        slip_summary = self.slip_repo.get_daily_waste_summary(
            from_date, to_date, self.store_id
        )

        total_slip_items = sum(
            s.get("item_count", 0) for s in slip_summary
        )
        total_slip_wonga = sum(
            s.get("wonga_total", 0) for s in slip_summary
        )
        total_slip_maega = sum(
            s.get("maega_total", 0) for s in slip_summary
        )

        # daily_sales 폐기 총 건수
        try:
            conn = self.sales_repo._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) as cnt, COALESCE(SUM(disuse_qty), 0) as total_qty
                FROM daily_sales
                WHERE sales_date BETWEEN ? AND ?
                  AND COALESCE(disuse_qty, 0) > 0
                """,
                (from_date, to_date),
            )
            row = cursor.fetchone()
            ds_count = row[0] if row else 0
            ds_total_qty = row[1] if row else 0
        except Exception:
            ds_count = 0
            ds_total_qty = 0

        gap = total_slip_items - ds_count
        gap_pct = (
            round(gap / total_slip_items * 100, 1)
            if total_slip_items > 0
            else 0
        )

        return {
            "period": f"{from_date} ~ {to_date}",
            "slip_days": len(slip_summary),
            "slip_total_items": total_slip_items,
            "slip_total_wonga": total_slip_wonga,
            "slip_total_maega": total_slip_maega,
            "daily_sales_disuse_count": ds_count,
            "daily_sales_disuse_qty": ds_total_qty,
            "gap": gap,
            "gap_percentage": gap_pct,
            "daily_summary": slip_summary,
        }
