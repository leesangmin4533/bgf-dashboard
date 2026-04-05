"""마일���톤 KPI 측정 + 판정 + 리포트

주간(일요일 00:00) 실행. 기존 OpsMetrics 데이터를 재사용하여
K1~K4 목표 달성 여부를 판정하고, milestone_snapshots에 저장.
"""

from datetime import date, datetime
from typing import Dict, List, Optional

from src.analysis.ops_metrics import OpsMetrics
from src.infrastructure.database.connection import DBRouter
from src.settings.constants import (
    MILESTONE_TARGETS,
    MILESTONE_APPROACHING_RATIO,
    MILESTONE_COMPLETION_WEEKS,
)
from src.settings.store_context import StoreContext
from src.utils.logger import get_logger

logger = get_logger(__name__)

_FOOD_MIDS = ("001", "002", "003", "004", "005")


class MilestoneTracker:
    """주간 마일스톤 KPI 측��� + 판정"""

    def evaluate(self) -> dict:
        """메인 진입점: 전 매장 KPI 측정 → 판정 → 저장 → 리포트 반환"""
        all_metrics = self._collect_all_stores()
        if not all_metrics:
            logger.warning("[Milestone] 데이터 수집 실패 — 활성 매장 없음")
            return {"error": "no_data"}

        k1 = self._calculate_k1(all_metrics)
        k2 = self._calculate_k2(all_metrics)
        k3 = self._calculate_k3(all_metrics)
        k4 = self._calculate_k4(all_metrics)

        kpis = {"K1": k1, "K2": k2, "K3": k3, "K4": k4}
        all_achieved = all(
            kpi.get("status") == "ACHIEVED" for kpi in kpis.values()
        )

        self._save_snapshot(kpis, all_achieved)
        consecutive = self._count_consecutive_achieved()

        message = self._build_report_message(kpis, consecutive)

        result = {
            "kpis": kpis,
            "all_achieved": all_achieved,
            "achieved_count": sum(
                1 for kpi in kpis.values() if kpi.get("status") == "ACHIEVED"
            ),
            "consecutive_weeks": consecutive,
            "report_message": message,
        }

        if all_achieved and consecutive >= MILESTONE_COMPLETION_WEEKS:
            logger.info(
                f"[Milestone] 3단계 완료! 연속 {consecutive}주 달성"
            )

        return result

    # ── 데이터 수집 ──

    def _collect_all_stores(self) -> Dict[str, dict]:
        """전 매장 OpsMetrics 수집"""
        stores = StoreContext.get_all_active()
        result = {}
        for ctx in stores:
            try:
                metrics = OpsMetrics(ctx.store_id).collect_all()
                result[ctx.store_id] = metrics
            except Exception as e:
                logger.warning(f"[Milestone] {ctx.store_id} 수집 실패: {e}")
        return result

    # ── KPI 계산 ──

    def _calculate_k1(self, all_metrics: Dict[str, dict]) -> dict:
        """K1 예측 안정성: 전 매장 카테고리별 mae_7d/mae_14d 평균"""
        ratios = []
        for store_id, metrics in all_metrics.items():
            pred = metrics.get("prediction_accuracy", {})
            if pred.get("insufficient_data"):
                continue
            for cat in pred.get("categories", []):
                mae_7d = cat.get("mae_7d", 0)
                mae_14d = cat.get("mae_14d", 0)
                if mae_14d > 0:
                    ratios.append(mae_7d / mae_14d)

        if not ratios:
            return {"value": None, "status": "NO_DATA", "detail": "데이터 부족"}

        avg_ratio = sum(ratios) / len(ratios)
        target = MILESTONE_TARGETS["K1_prediction_stability"]
        status = self._judge(avg_ratio, target)

        return {
            "value": round(avg_ratio, 3),
            "target": target,
            "status": status,
            "detail": f"{len(ratios)}개 카테고리 평균",
        }

    def _calculate_k2(self, all_metrics: Dict[str, dict]) -> dict:
        """K2 폐기율: 전 매장 food/전체 폐기율"""
        food_waste = 0
        food_total = 0
        all_waste = 0
        all_total = 0

        for store_id, metrics in all_metrics.items():
            waste = metrics.get("waste_rate", {})
            if waste.get("insufficient_data"):
                continue
            for cat in waste.get("categories", []):
                mid_cd = cat.get("mid_cd", "")
                rate_30d = cat.get("rate_30d", 0)
                # rate_30d는 waste/(waste+sales) 비율
                # 원시 수량이 아니라 비율만 있으므로 카테고리별 가중평균 불가
                # → 카테고리 수 기준 단순평균 사용
                if mid_cd in _FOOD_MIDS:
                    food_waste += rate_30d
                    food_total += 1
                all_waste += rate_30d
                all_total += 1

        food_rate = food_waste / food_total if food_total > 0 else None
        total_rate = all_waste / all_total if all_total > 0 else None

        if food_rate is None and total_rate is None:
            return {
                "food": None, "total": None,
                "status": "NO_DATA", "detail": "데이터 부족",
            }

        # food와 total 둘 다 목표 이내여야 ACHIEVED
        food_target = MILESTONE_TARGETS["K2_waste_rate_food"]
        total_target = MILESTONE_TARGETS["K2_waste_rate_total"]

        food_ok = food_rate is not None and food_rate <= food_target
        total_ok = total_rate is not None and total_rate <= total_target

        if food_ok and total_ok:
            status = "ACHIEVED"
        elif (food_rate is not None and food_rate <= food_target * MILESTONE_APPROACHING_RATIO
              and total_rate is not None and total_rate <= total_target * MILESTONE_APPROACHING_RATIO):
            status = "APPROACHING"
        else:
            status = "NOT_MET"

        return {
            "food": round(food_rate, 4) if food_rate is not None else None,
            "total": round(total_rate, 4) if total_rate is not None else None,
            "food_target": food_target,
            "total_target": total_target,
            "status": status,
        }

    def _calculate_k3(self, all_metrics: Dict[str, dict]) -> dict:
        """K3 발주 실패율: fail_count_7d / total_order_7d"""
        total_fails = 0
        total_orders = 0

        for store_id, metrics in all_metrics.items():
            order = metrics.get("order_failure", {})
            if order.get("insufficient_data"):
                continue
            total_fails += order.get("recent_7d", 0)
            total_orders += order.get("total_order_7d", 0)

        if total_orders == 0:
            return {"value": None, "status": "NO_DATA", "detail": "발주 데이터 없음"}

        rate = total_fails / total_orders
        target = MILESTONE_TARGETS["K3_order_failure_rate"]
        status = self._judge(rate, target)

        return {
            "value": round(rate, 4),
            "target": target,
            "status": status,
            "detail": f"실패 {total_fails} / 전체 {total_orders}",
        }

    def _calculate_k4(self, all_metrics: Dict[str, dict]) -> dict:
        """K4 무결성: max(consecutive_anomaly_days) 전 체크, 전 매장"""
        max_consecutive = 0

        for store_id, metrics in all_metrics.items():
            integrity = metrics.get("integrity_unresolved", {})
            if integrity.get("insufficient_data"):
                continue
            for check in integrity.get("checks", []):
                days = check.get("consecutive_days", 0)
                if days > max_consecutive:
                    max_consecutive = days

        target = MILESTONE_TARGETS["K4_integrity_max_consecutive"]
        status = self._judge(max_consecutive, target)

        return {
            "value": max_consecutive,
            "target": target,
            "status": status,
        }

    # ── 판정 ──

    def _judge(self, value: float, target: float) -> str:
        """단일 KPI 판정 (lower_is_better)"""
        if value is None:
            return "NO_DATA"
        if value <= target:
            return "ACHIEVED"
        elif value <= target * MILESTONE_APPROACHING_RATIO:
            return "APPROACHING"
        else:
            return "NOT_MET"

    # ── DB 저장 ──

    def _save_snapshot(self, kpis: dict, all_achieved: bool) -> None:
        """milestone_snapshots에 주간 스냅샷 저장 (common.db)"""
        conn = DBRouter.get_common_connection()
        try:
            today = date.today().isoformat()
            consecutive = self._count_consecutive_achieved(conn=conn)
            if all_achieved:
                consecutive += 1

            k1 = kpis["K1"]
            k2 = kpis["K2"]
            k3 = kpis["K3"]
            k4 = kpis["K4"]

            conn.execute(
                """INSERT OR REPLACE INTO milestone_snapshots
                   (snapshot_date, stage, k1_value, k1_status,
                    k2_food_value, k2_total_value, k2_status,
                    k3_value, k3_status, k4_value, k4_status,
                    all_achieved, consecutive_weeks)
                   VALUES (?, '3', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    today,
                    k1.get("value"), k1.get("status"),
                    k2.get("food"), k2.get("total"), k2.get("status"),
                    k3.get("value"), k3.get("status"),
                    k4.get("value"), k4.get("status"),
                    1 if all_achieved else 0,
                    consecutive,
                ),
            )
            conn.commit()
            logger.info(f"[Milestone] 스냅샷 저장: {today}, 달성 {sum(1 for k in kpis.values() if k.get('status') == 'ACHIEVED')}/4")
        except Exception as e:
            logger.warning(f"[Milestone] 스냅샷 저장 실패: {e}")
        finally:
            conn.close()

    def _count_consecutive_achieved(self, conn=None) -> int:
        """milestone_snapshots에서 연속 전체 달성 주 수 (최근부터 역순)"""
        should_close = conn is None
        if conn is None:
            conn = DBRouter.get_common_connection()
        try:
            cursor = conn.execute(
                """SELECT all_achieved FROM milestone_snapshots
                   WHERE stage = '3'
                   ORDER BY snapshot_date DESC
                   LIMIT 10"""
            )
            rows = cursor.fetchall()
            consecutive = 0
            for row in rows:
                if row["all_achieved"] if isinstance(row, dict) else row[0]:
                    consecutive += 1
                else:
                    break
            return consecutive
        except Exception as e:
            logger.warning(f"[Milestone] 연속 주 계산 실패: {e}")
            return 0
        finally:
            if should_close:
                conn.close()

    # ── 리포트 ──

    def _build_report_message(self, kpis: dict, consecutive: int) -> str:
        """카카오 리포트 텍스트 생성"""
        today = date.today().strftime("%m-%d")
        lines = [f"[마일스톤 주간 리포트] {today}", ""]

        # K1
        k1 = kpis["K1"]
        if k1.get("value") is not None:
            icon = self._status_icon(k1["status"])
            lines.append(f"K1 예측 안정성: {k1['value']:.2f} / {k1['target']} {icon}")
        else:
            lines.append("K1 예측 안정성: 데이터 부족")

        # K2
        k2 = kpis["K2"]
        if k2.get("food") is not None:
            icon = self._status_icon(k2["status"])
            food_pct = f"{k2['food']:.1%}"
            total_pct = f"{k2['total']:.1%}" if k2.get("total") else "N/A"
            lines.append(
                f"K2 폐기율: food {food_pct}/{k2['food_target']:.0%} | "
                f"전체 {total_pct}/{k2['total_target']:.0%} {icon}"
            )
        else:
            lines.append("K2 폐기율: 데이터 부족")

        # K3
        k3 = kpis["K3"]
        if k3.get("value") is not None:
            icon = self._status_icon(k3["status"])
            lines.append(f"K3 발주 실패: {k3['value']:.1%} / {k3['target']:.0%} {icon}")
        else:
            lines.append("K3 발주 실패: 데이터 부족")

        # K4
        k4 = kpis["K4"]
        icon = self._status_icon(k4["status"])
        lines.append(f"K4 무결성: {k4['value']}일 / {k4['target']}일 {icon}")

        # 요약
        achieved = sum(
            1 for k in kpis.values() if k.get("status") == "ACHIEVED"
        )
        lines.append("")
        lines.append(
            f"달성: {achieved}/4 | "
            f"연속: {consecutive}주/{MILESTONE_COMPLETION_WEEKS}주"
        )

        # 완료 판정
        if achieved == 4 and consecutive >= MILESTONE_COMPLETION_WEEKS:
            lines.append("")
            lines.append("3단계 '발주 품질 개선' 완료!")
            lines.append("-> 4단계 '수익성 최적화' 계획 수립 필요")
        elif achieved < 4:
            # 미달성 KPI에 기여하는 다음 작업 제안
            suggestion = self._suggest_next_action(kpis)
            if suggestion:
                lines.append(f"-> 다음: {suggestion}")

        return "\n".join(lines)

    def _status_icon(self, status: str) -> str:
        """상태별 아이콘"""
        return {
            "ACHIEVED": "V",
            "APPROACHING": "~",
            "NOT_MET": "X",
            "NO_DATA": "?",
        }.get(status, "?")

    def _suggest_next_action(self, kpis: dict) -> Optional[str]:
        """미달성 KPI 기반 다음 작업 제안"""
        # Plan 문서의 이슈→KPI 매핑 기반
        suggestions = {
            "K1": "ML is_payday 검증 (예측 안정성)",
            "K2": "행사 종료 감량 자동화 (폐기율)",
            "K3": "발주 실행 안정화",
            "K4": "GHOST_STOCK 승격 검토 (무결성)",
        }
        for key in ["K2", "K1", "K3", "K4"]:  # 우선순위 순
            kpi = kpis.get(key, {})
            if kpi.get("status") not in ("ACHIEVED", "NO_DATA"):
                return suggestions.get(key)
        return None
