"""
정확도 리포트 생성 모듈
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
from pathlib import Path

from src.utils.logger import get_logger
from .tracker import AccuracyTracker, AccuracyMetrics, CategoryAccuracy

logger = get_logger(__name__)


@dataclass
class AccuracyReport:
    """정확도 리포트"""
    generated_at: str
    period: str

    # 요약
    overall_metrics: AccuracyMetrics

    # 상세
    category_breakdown: List[CategoryAccuracy] = field(default_factory=list)
    worst_items: List[Dict[str, Any]] = field(default_factory=list)
    best_items: List[Dict[str, Any]] = field(default_factory=list)

    # 트렌드
    daily_mape_trend: List[Dict[str, Any]] = field(default_factory=list)

    # 알림
    alerts: List[str] = field(default_factory=list)


class AccuracyReporter:
    """정확도 리포터"""

    # 알림 기준
    ALERT_THRESHOLDS = {
        "mape_warning": 25,         # 전체 MAPE > 25%
        "mape_critical": 35,        # 카테고리 MAPE > 35%
        "over_prediction": 60,      # 과대예측 비율 > 60%
        "under_prediction": 60,     # 과소예측 비율 > 60%
    }

    def __init__(self, store_id: Optional[str] = None) -> None:
        self.store_id = store_id
        self.tracker = AccuracyTracker(store_id=store_id)

    def generate_daily_report(self) -> AccuracyReport:
        """
        일일 정확도 리포트 생성

        포함 내용:
            - 어제 예측 vs 오늘 실제 비교
            - 전체 MAPE, MAE
            - 카테고리별 정확도
            - 문제 상품 목록
        """
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        # 실제 판매량 업데이트
        updated = self.tracker.update_actual_sales(yesterday)

        # 정확도 계산
        overall = self.tracker.get_accuracy_by_date(yesterday)
        categories = self.tracker.get_accuracy_by_category(days=1)
        worst = self.tracker.get_worst_items(days=1, limit=5)
        best = self.tracker.get_best_items(days=1, limit=5)

        # 알림 체크
        alerts = self.check_alerts(overall, categories)

        return AccuracyReport(
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            period=f"{yesterday} (일일)",
            overall_metrics=overall,
            category_breakdown=categories,
            worst_items=worst,
            best_items=best,
            daily_mape_trend=[],
            alerts=alerts
        )

    def generate_weekly_report(self) -> AccuracyReport:
        """
        주간 정확도 리포트 생성

        추가 내용:
            - 일별 MAPE 추이
            - 주간 트렌드 분석
        """
        # 정확도 계산
        overall = self.tracker.get_accuracy_by_period(days=7)
        categories = self.tracker.get_accuracy_by_category(days=7)
        worst = self.tracker.get_worst_items(days=7, limit=10)
        best = self.tracker.get_best_items(days=7, limit=10)
        trend = self.tracker.get_daily_mape_trend(days=7)

        # 알림 체크
        alerts = self.check_alerts(overall, categories)

        return AccuracyReport(
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            period=f"{overall.period_start} ~ {overall.period_end} (주간)",
            overall_metrics=overall,
            category_breakdown=categories,
            worst_items=worst,
            best_items=best,
            daily_mape_trend=trend,
            alerts=alerts
        )

    def format_for_kakao(self, report: AccuracyReport) -> str:
        """
        카카오톡 알림용 텍스트 포맷
        """
        lines = []

        # 헤더
        date_str = datetime.now().strftime("%m/%d")
        lines.append(f"[예측 정확도 리포트 ({date_str})]")
        lines.append("")

        # 전체 요약
        m = report.overall_metrics
        if m.total_predictions == 0:
            lines.append("* 예측 데이터 없음")
            return "\n".join(lines)

        lines.append(f"* 전체: MAPE {m.mape}%, ±2개 적중률 {m.accuracy_within_2}%")
        lines.append(f"* 예측 건수: {m.total_predictions}건")
        lines.append("")

        # 편향 분석
        if m.over_prediction_rate > m.under_prediction_rate + 10:
            lines.append(f"* 경향: 과대예측 ({m.over_prediction_rate}%)")
        elif m.under_prediction_rate > m.over_prediction_rate + 10:
            lines.append(f"* 경향: 과소예측 ({m.under_prediction_rate}%)")

        # 카테고리별 (상위 5개만)
        if report.category_breakdown:
            lines.append("")
            lines.append("[카테고리별]")
            for cat in report.category_breakdown[:5]:
                emoji = "!" if cat.metrics.mape > 25 else ""
                lines.append(f"- {cat.mid_nm}: MAPE {cat.metrics.mape}% {emoji}")

        # 문제 상품
        if report.worst_items:
            lines.append("")
            lines.append("[주의 필요 상품]")
            for item in report.worst_items[:3]:
                name = item["item_nm"][:10] + "..." if len(item["item_nm"]) > 10 else item["item_nm"]
                lines.append(f"- {name}: MAPE {item['mape']}%")

        # 알림
        if report.alerts:
            lines.append("")
            lines.append("[알림]")
            for alert in report.alerts:
                lines.append(alert)

        return "\n".join(lines)

    def format_for_console(self, report: AccuracyReport) -> str:
        """콘솔 출력용 포맷"""
        lines = []
        lines.append("=" * 60)
        lines.append(f"예측 정확도 리포트")
        lines.append(f"생성: {report.generated_at}")
        lines.append(f"기간: {report.period}")
        lines.append("=" * 60)

        m = report.overall_metrics
        if m.total_predictions == 0:
            lines.append("예측 데이터 없음")
            return "\n".join(lines)

        lines.append("")
        lines.append("[전체 요약]")
        lines.append(f"  예측 건수: {m.total_predictions}건")
        lines.append(f"  MAPE: {m.mape}%")
        lines.append(f"  MAE: {m.mae}개")
        lines.append(f"  RMSE: {m.rmse}")
        lines.append("")
        lines.append(f"  정확 적중: {m.accuracy_exact}%")
        lines.append(f"  ±1개 이내: {m.accuracy_within_1}%")
        lines.append(f"  ±2개 이내: {m.accuracy_within_2}%")
        lines.append(f"  ±3개 이내: {m.accuracy_within_3}%")
        lines.append("")
        lines.append(f"  과대예측: {m.over_prediction_rate}% (평균 +{m.avg_over_amount}개)")
        lines.append(f"  과소예측: {m.under_prediction_rate}% (평균 -{m.avg_under_amount}개)")

        if report.category_breakdown:
            lines.append("")
            lines.append("[카테고리별]")
            lines.append(f"  {'카테고리':<10} | {'MAPE':>6} | {'MAE':>5} | {'±2적중':>6}")
            lines.append("-" * 45)
            for cat in report.category_breakdown[:10]:
                lines.append(
                    f"  {cat.mid_nm:<10} | {cat.metrics.mape:>5.1f}% | "
                    f"{cat.metrics.mae:>5.1f} | {cat.metrics.accuracy_within_2:>5.1f}%"
                )

        if report.worst_items:
            lines.append("")
            lines.append("[정확도 낮은 상품]")
            for item in report.worst_items[:5]:
                lines.append(f"  - {item['item_nm'][:20]}: MAPE {item['mape']}%, bias {item['bias']:+.1f}")

        if report.daily_mape_trend:
            lines.append("")
            lines.append("[일별 MAPE 추이]")
            for d in report.daily_mape_trend:
                bar = "*" * min(int(d['mape'] / 5), 20)
                lines.append(f"  {d['date']}: {d['mape']:>5.1f}% {bar}")

        if report.alerts:
            lines.append("")
            lines.append("[알림]")
            for alert in report.alerts:
                lines.append(f"  {alert}")

        return "\n".join(lines)

    def format_for_csv(self, report: AccuracyReport) -> str:
        """CSV 파일용 포맷"""
        lines = []

        # 헤더
        lines.append("type,key,value")

        # 전체 지표
        m = report.overall_metrics
        lines.append(f"overall,period,{report.period}")
        lines.append(f"overall,total_predictions,{m.total_predictions}")
        lines.append(f"overall,mape,{m.mape}")
        lines.append(f"overall,mae,{m.mae}")
        lines.append(f"overall,rmse,{m.rmse}")
        lines.append(f"overall,accuracy_within_1,{m.accuracy_within_1}")
        lines.append(f"overall,accuracy_within_2,{m.accuracy_within_2}")
        lines.append(f"overall,over_prediction_rate,{m.over_prediction_rate}")
        lines.append(f"overall,under_prediction_rate,{m.under_prediction_rate}")

        # 카테고리별
        for cat in report.category_breakdown:
            lines.append(f"category,{cat.mid_cd},{cat.metrics.mape}")

        # 문제 상품
        for item in report.worst_items:
            lines.append(f"worst_item,{item['item_cd']},{item['mape']}")

        return "\n".join(lines)

    def check_alerts(
        self,
        metrics: AccuracyMetrics,
        categories: Optional[List[CategoryAccuracy]] = None
    ) -> List[str]:
        """
        알림 조건 체크

        알림 기준:
            - 전체 MAPE > 25%
            - 특정 카테고리 MAPE > 35%
            - 과소예측 비율 > 60%
            - 과대예측 비율 > 60%
        """
        alerts = []

        if metrics.total_predictions == 0:
            return alerts

        # 전체 MAPE 체크
        if metrics.mape > self.ALERT_THRESHOLDS["mape_warning"]:
            alerts.append(f"! 전체 MAPE {metrics.mape}% (기준: {self.ALERT_THRESHOLDS['mape_warning']}%)")

        # 과대예측 체크
        if metrics.over_prediction_rate > self.ALERT_THRESHOLDS["over_prediction"]:
            alerts.append(f"! 과대예측 경향 {metrics.over_prediction_rate}% → 재고 과다 위험")

        # 과소예측 체크
        if metrics.under_prediction_rate > self.ALERT_THRESHOLDS["under_prediction"]:
            alerts.append(f"! 과소예측 경향 {metrics.under_prediction_rate}% → 품절 위험")

        # 카테고리별 체크
        if categories:
            for cat in categories:
                if cat.metrics.mape > self.ALERT_THRESHOLDS["mape_critical"]:
                    alerts.append(f"! {cat.mid_nm} MAPE {cat.metrics.mape}% (주의)")

        return alerts

    def save_report(self, report: AccuracyReport, filename: Optional[str] = None) -> str:
        """
        리포트 파일 저장

        Args:
            report: 리포트 객체
            filename: 파일명 (None이면 자동 생성)

        Returns:
            저장된 파일 경로
        """
        if filename is None:
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"accuracy_report_{date_str}.txt"

        # 로그 디렉토리에 저장
        log_dir = Path(__file__).parent.parent.parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)

        filepath = log_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(self.format_for_console(report))

        return str(filepath)


# =============================================================================
# 스케줄러 헬퍼 함수
# =============================================================================
def run_daily_accuracy_update() -> Dict[str, Any]:
    """
    일일 실제 판매량 업데이트 (스케줄러용)

    Returns:
        {"success": bool, "updated": int}
    """
    try:
        tracker = AccuracyTracker()
        updated = tracker.update_actual_sales()
        return {"success": True, "updated": updated}
    except Exception as e:
        return {"success": False, "error": str(e)}


def run_daily_accuracy_report() -> Dict[str, Any]:
    """
    일일 정확도 리포트 생성 및 알림 (스케줄러용)

    Returns:
        {"success": bool, "report": AccuracyReport, "alerts": list}
    """
    try:
        reporter = AccuracyReporter()
        report = reporter.generate_daily_report()

        # 파일 저장
        filepath = reporter.save_report(report)

        # 카카오 알림 (알림이 있을 때만)
        kakao_sent = False
        if report.alerts:
            try:
                from src.notification.kakao_notifier import KakaoNotifier, DEFAULT_REST_API_KEY
                notifier = KakaoNotifier(DEFAULT_REST_API_KEY)
                if notifier.access_token:
                    message = reporter.format_for_kakao(report)
                    notifier.send_message(message)
                    kakao_sent = True
            except Exception as e:
                logger.warning(f"일일 정확도 리포트 카카오 알림 실패: {e}")

        return {
            "success": True,
            "report": report,
            "filepath": filepath,
            "kakao_sent": kakao_sent
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def run_weekly_accuracy_report() -> Dict[str, Any]:
    """
    주간 정확도 리포트 생성 및 알림 (스케줄러용)
    """
    try:
        reporter = AccuracyReporter()
        report = reporter.generate_weekly_report()

        # 파일 저장
        filepath = reporter.save_report(report, f"accuracy_weekly_{datetime.now().strftime('%Y%m%d')}.txt")

        # 카카오 알림
        kakao_sent = False
        try:
            from src.notification.kakao_notifier import KakaoNotifier, DEFAULT_REST_API_KEY
            notifier = KakaoNotifier(DEFAULT_REST_API_KEY)
            if notifier.access_token:
                message = reporter.format_for_kakao(report)
                notifier.send_message(message)
                kakao_sent = True
        except Exception as e:
            logger.warning(f"주간 정확도 리포트 카카오 알림 실패: {e}")

        return {
            "success": True,
            "report": report,
            "filepath": filepath,
            "kakao_sent": kakao_sent
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# 테스트
# =============================================================================
if __name__ == "__main__":
    print("정확도 리포터 테스트")
    print("=" * 60)

    reporter = AccuracyReporter()

    # 주간 리포트 생성
    print("\n주간 리포트 생성 중...")
    report = reporter.generate_weekly_report()

    # 콘솔 출력
    print(reporter.format_for_console(report))

    # 카카오 포맷
    print("\n" + "=" * 60)
    print("카카오톡 포맷:")
    print("=" * 60)
    print(reporter.format_for_kakao(report))
