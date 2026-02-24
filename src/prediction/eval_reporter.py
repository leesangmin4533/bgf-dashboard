"""
사전 발주 평가 일일 리포트 생성기 (EvalReporter)

매일 실행 후 보정 결과, 적중률, 파라미터 변경 이력을 텍스트 파일로 기록.
운영자가 추후 확인하고 재정비할 수 있도록 상세한 일별 문서를 남긴다.

파일: data/logs/eval_report_YYYY-MM-DD.txt
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, IO, List, Optional

from src.utils.logger import get_logger
from src.infrastructure.database.repos import (
    EvalOutcomeRepository,
    CalibrationRepository,
)
from src.prediction.eval_config import EvalConfig

logger = get_logger(__name__)


class EvalReporter:
    """사전 발주 평가 일일 리포트 생성기"""

    def __init__(self, config: Optional[EvalConfig] = None, store_id: Optional[str] = None) -> None:
        self.config = config or EvalConfig.load()
        self.store_id = store_id
        self.outcome_repo = EvalOutcomeRepository(store_id=self.store_id)
        self.calibration_repo = CalibrationRepository(store_id=self.store_id)
        self._log_dir = Path(__file__).parent.parent.parent / "data" / "logs"
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def generate_daily_report(
        self,
        verification_result: Optional[Dict[str, Any]] = None,
        calibration_result: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        일일 보정 리포트 생성

        Args:
            verification_result: verify_yesterday() 결과
            calibration_result: calibrate() 결과

        Returns:
            생성된 파일 경로 또는 None
        """
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
        time_str = now.strftime("%H:%M:%S")

        filename = f"eval_report_{date_str}.txt"
        filepath = self._log_dir / filename

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                self._write_header(f, date_str, weekday_kr, time_str)
                self._write_current_params(f)
                self._write_verification_summary(f, verification_result, date_str)
                self._write_accuracy_stats(f)
                self._write_calibration_result(f, calibration_result)
                self._write_param_change_history(f)
                self._write_decision_distribution(f, date_str)
                self._write_recommendations(f)
                self._write_footer(f)

            logger.info(f"일일 리포트 저장: {filepath}")
            return str(filepath)

        except Exception as e:
            logger.warning(f"일일 리포트 저장 실패: {e}")
            return None

    def _write_header(self, f: IO[str], date_str: str, weekday: str, time_str: str) -> None:
        """헤더 작성"""
        f.write("=" * 72 + "\n")
        f.write("BGF 사전 발주 평가 - 일일 보정 리포트\n")
        f.write(f"날짜: {date_str} ({weekday})\n")
        f.write(f"생성 시각: {time_str}\n")
        f.write("=" * 72 + "\n\n")

    def _write_current_params(self, f: IO[str]) -> None:
        """현재 파라미터 값 기록"""
        f.write("[1. 현재 파라미터]\n")
        f.write("-" * 72 + "\n")

        weights = self.config.get_popularity_weights()
        diff = self.config.diff_from_default()

        rows = [
            ("일평균 기간", f"{int(self.config.daily_avg_days.value)}일",
             f"기본={int(self.config.daily_avg_days.default)}일"),
            ("가중치: 일평균", f"{weights['daily_avg']:.4f}",
             f"기본={self.config.weight_daily_avg.default:.2f}"),
            ("가중치: 판매일비율", f"{weights['sell_day_ratio']:.4f}",
             f"기본={self.config.weight_sell_day_ratio.default:.2f}"),
            ("가중치: 트렌드", f"{weights['trend']:.4f}",
             f"기본={self.config.weight_trend.default:.2f}"),
            ("인기도 고인기 백분위", f"P{self.config.popularity_high_percentile.value:.0f}",
             f"기본=P{self.config.popularity_high_percentile.default:.0f}"),
            ("인기도 저인기 백분위", f"P{self.config.popularity_low_percentile.value:.0f}",
             f"기본=P{self.config.popularity_low_percentile.default:.0f}"),
            ("노출 긴급 임계", f"{self.config.exposure_urgent.value:.1f}일",
             f"기본={self.config.exposure_urgent.default:.1f}일"),
            ("노출 일반 임계", f"{self.config.exposure_normal.value:.1f}일",
             f"기본={self.config.exposure_normal.default:.1f}일"),
            ("노출 충분 임계", f"{self.config.exposure_sufficient.value:.1f}일",
             f"기본={self.config.exposure_sufficient.default:.1f}일"),
            ("품절빈도 임계", f"{self.config.stockout_freq_threshold.value:.1%}",
             f"기본={self.config.stockout_freq_threshold.default:.1%}"),
        ]

        for label, value, default_info in rows:
            changed = "★" if any(label.replace(" ", "_").lower().startswith(d.split("_")[0]) for d in diff) else " "
            f.write(f"  {changed} {label:<22} {value:<12} ({default_info})\n")

        if diff:
            f.write(f"\n  ※ 기본값 대비 변경된 파라미터: {len(diff)}개 (★ 표시)\n")
        else:
            f.write(f"\n  ※ 모든 파라미터 기본값 사용 중\n")
        f.write("\n")

    def _write_verification_summary(self, f: IO[str], result: Optional[Dict[str, Any]], date_str: str) -> None:
        """사후 검증 결과 기록"""
        f.write("[2. 어제 결정 사후 검증]\n")
        f.write("-" * 72 + "\n")

        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        if not result or result.get("verified", 0) == 0:
            f.write(f"  대상일: {yesterday}\n")
            f.write("  검증 데이터 없음 (아직 데이터 미축적)\n\n")
            return

        verified = result["verified"]
        correct = result["correct"]
        under = result.get("under", 0)
        over = result.get("over", 0)
        miss = result.get("miss", 0)
        accuracy = correct / verified if verified > 0 else 0

        f.write(f"  대상일: {yesterday}\n")
        f.write(f"  검증 건수: {verified}건\n")
        f.write(f"  적중률: {accuracy:.1%} ({correct}/{verified})\n")
        f.write(f"\n")
        f.write(f"  ┌─────────┬──────┬──────┐\n")
        f.write(f"  │ 판정    │ 건수 │ 비율 │\n")
        f.write(f"  ├─────────┼──────┼──────┤\n")
        f.write(f"  │ 적중    │ {correct:>4} │ {correct/verified*100:>4.1f}% │\n")
        f.write(f"  │ 과소    │ {under:>4} │ {under/verified*100:>4.1f}% │\n")
        f.write(f"  │ 과잉    │ {over:>4} │ {over/verified*100:>4.1f}% │\n")
        f.write(f"  │ 미스    │ {miss:>4} │ {miss/verified*100:>4.1f}% │\n")
        f.write(f"  └─────────┴──────┴──────┘\n")

        # 해석
        if accuracy >= 0.7:
            f.write(f"  → 적중률 양호 ({accuracy:.1%})\n")
        elif accuracy >= 0.5:
            f.write(f"  → 적중률 보통 ({accuracy:.1%}), 지속 관찰 필요\n")
        else:
            f.write(f"  → ⚠ 적중률 저조 ({accuracy:.1%}), 파라미터 재검토 권장\n")

        if miss > 0:
            f.write(f"  → SKIP 후 품절 {miss}건 발생 - 노출 충분 임계값 검토 필요\n")
        f.write("\n")

    def _write_accuracy_stats(self, f: IO[str]) -> None:
        """누적 적중률 통계 기록"""
        f.write("[3. 누적 적중률 (30일)]\n")
        f.write("-" * 72 + "\n")

        stats = self.outcome_repo.get_accuracy_stats(days=30)
        total = stats.get("total_verified", 0)

        if total == 0:
            f.write("  데이터 없음 (아직 검증 데이터 미축적)\n\n")
            return

        overall = stats.get("overall_accuracy", 0)
        f.write(f"  총 검증 건수: {total}건 | 전체 적중률: {overall:.1%}\n\n")

        f.write(f"  ┌──────────────┬──────┬──────┬──────┬──────┬──────┬────────┐\n")
        f.write(f"  │ 결정         │ 건수 │ 적중 │ 과소 │ 과잉 │ 미스 │ 적중률 │\n")
        f.write(f"  ├──────────────┼──────┼──────┼──────┼──────┼──────┼────────┤\n")

        decision_order = ["FORCE_ORDER", "URGENT_ORDER", "NORMAL_ORDER", "PASS", "SKIP"]
        labels = {
            "FORCE_ORDER": "강제 발주",
            "URGENT_ORDER": "긴급 발주",
            "NORMAL_ORDER": "일반 발주",
            "PASS": "안전재고위임",
            "SKIP": "발주 스킵",
        }

        by_decision = stats.get("by_decision", {})
        for d in decision_order:
            info = by_decision.get(d, {})
            t = info.get("total", 0)
            c = info.get("correct", 0)
            u = info.get("under_order", 0)
            o = info.get("over_order", 0)
            m = info.get("miss", 0)
            acc = info.get("accuracy", 0)
            label = labels.get(d, d)
            f.write(f"  │ {label:<12} │ {t:>4} │ {c:>4} │ {u:>4} │ {o:>4} │ {m:>4} │ {acc:>5.1%} │\n")

        f.write(f"  └──────────────┴──────┴──────┴──────┴──────┴──────┴────────┘\n")

        # SKIP 후 품절 상세
        skip_stats = self.outcome_repo.get_skip_stockout_stats(days=30)
        if skip_stats.get("total_skip", 0) > 0:
            f.write(f"\n  SKIP 상세: {skip_stats['total_skip']}건 중 ")
            f.write(f"품절 {skip_stats['stockout_after_skip']}건 ")
            f.write(f"(미스율={skip_stats['miss_rate']:.1%})\n")
            if skip_stats.get("min_exposure_days") is not None:
                f.write(f"  → 품절 발생 최소 노출: {skip_stats['min_exposure_days']:.1f}일, ")
                f.write(f"평균 노출: {skip_stats.get('avg_exposure_days', 0):.1f}일\n")

        f.write("\n")

    def _write_calibration_result(self, f: IO[str], result: Optional[Dict[str, Any]]) -> None:
        """보정 결과 기록"""
        f.write("[4. 자동 보정 결과]\n")
        f.write("-" * 72 + "\n")

        if not result:
            f.write("  보정 미실행\n\n")
            return

        calibrated = result.get("calibrated", False)
        changes = result.get("changes", [])
        reason = result.get("reason", "")

        if not calibrated:
            f.write(f"  보정 미실행: {reason}\n\n")
            return

        f.write(f"  변경 파라미터: {len(changes)}개\n\n")

        for i, ch in enumerate(changes, 1):
            f.write(f"  [{i}] {ch['param']}\n")
            f.write(f"      이전: {ch['old']}\n")
            f.write(f"      변경: {ch['new']}\n")
            f.write(f"      사유: {ch.get('reason', '-')}\n\n")

        f.write("  ※ 변경된 설정은 config/eval_params.json에 저장됨\n")
        f.write("  ※ 수동 조정 시 해당 파일을 직접 편집 가능\n\n")

    def _write_param_change_history(self, f: IO[str]) -> None:
        """최근 파라미터 변경 이력"""
        f.write("[5. 파라미터 변경 이력 (최근 30일)]\n")
        f.write("-" * 72 + "\n")

        history = self.calibration_repo.get_recent_calibrations(days=30)

        if not history:
            f.write("  변경 이력 없음\n\n")
            return

        f.write(f"  총 {len(history)}건\n\n")
        f.write(f"  {'날짜':<12} {'파라미터':<28} {'이전':>8} {'→':^3} {'변경':>8} {'사유'}\n")
        f.write(f"  {'-'*12} {'-'*28} {'-'*8} {'-'*3} {'-'*8} {'-'*20}\n")

        for h in history[:20]:  # 최근 20건
            date = h.get("calibration_date", "")[:10]
            param = h.get("param_name", "")[:28]
            old = h.get("old_value", 0)
            new = h.get("new_value", 0)
            reason = (h.get("reason", "") or "")[:30]
            f.write(f"  {date:<12} {param:<28} {old:>8.4f} → {new:>8.4f} {reason}\n")

        if len(history) > 20:
            f.write(f"  ... 외 {len(history) - 20}건\n")

        f.write("\n")

    def _write_decision_distribution(self, f: IO[str], date_str: str) -> None:
        """오늘 평가 결정 분포"""
        f.write("[6. 오늘 평가 결정 분포]\n")
        f.write("-" * 72 + "\n")

        today_results = self.outcome_repo.get_outcomes_by_date(date_str)

        if not today_results:
            f.write("  오늘 평가 데이터 없음 (eval_outcomes에 저장 전이거나 미실행)\n\n")
            return

        # 결정별 집계
        decision_counts = {}
        total = len(today_results)
        for r in today_results:
            d = r.get("decision", "UNKNOWN")
            decision_counts[d] = decision_counts.get(d, 0) + 1

        f.write(f"  총 평가: {total}개\n\n")

        labels = {
            "FORCE_ORDER": "강제 발주",
            "URGENT_ORDER": "긴급 발주",
            "NORMAL_ORDER": "일반 발주",
            "PASS": "안전재고 위임",
            "SKIP": "발주 스킵",
        }

        order = ["FORCE_ORDER", "URGENT_ORDER", "NORMAL_ORDER", "PASS", "SKIP"]
        for d in order:
            cnt = decision_counts.get(d, 0)
            pct = cnt / total * 100 if total > 0 else 0
            bar = "█" * int(pct / 2)
            label = labels.get(d, d)
            f.write(f"  {label:<12} {cnt:>5}개 ({pct:>5.1f}%) {bar}\n")

        f.write("\n")

    def _write_recommendations(self, f: IO[str]) -> None:
        """개선 권장 사항"""
        f.write("[7. 운영자 참고사항]\n")
        f.write("-" * 72 + "\n")

        recommendations = []

        # 적중률 기반 권장
        stats = self.outcome_repo.get_accuracy_stats(days=30)
        total = stats.get("total_verified", 0)
        overall = stats.get("overall_accuracy", 0)

        if total < 50:
            recommendations.append(
                f"검증 데이터 부족 ({total}건). 최소 50건 이상 축적 후 자동 보정이 시작됩니다."
            )
        else:
            if overall < 0.5:
                recommendations.append(
                    f"전체 적중률 {overall:.1%}로 저조합니다. "
                    f"config/eval_params.json의 파라미터 수동 검토를 권장합니다."
                )
            elif overall >= 0.75:
                recommendations.append(
                    f"전체 적중률 {overall:.1%}로 양호합니다. 현재 설정을 유지하세요."
                )

        # SKIP 후 품절
        skip_stats = self.outcome_repo.get_skip_stockout_stats(days=30)
        if skip_stats.get("miss_rate", 0) > 0.20:
            recommendations.append(
                f"SKIP 후 품절률 {skip_stats['miss_rate']:.1%}가 높습니다. "
                f"exposure_sufficient 값을 수동으로 올려보세요 "
                f"(현재={self.config.exposure_sufficient.value:.1f}일)."
            )

        # 기본값 대비 변경
        diff = self.config.diff_from_default()
        if diff:
            params = ", ".join(diff.keys())
            recommendations.append(
                f"기본값 대비 변경된 파라미터 {len(diff)}개: {params}"
            )

        if not recommendations:
            recommendations.append("특이사항 없음. 시스템 정상 운영 중.")

        for i, rec in enumerate(recommendations, 1):
            f.write(f"  {i}. {rec}\n")

        f.write("\n")
        f.write("  ※ 수동 파라미터 조정: config/eval_params.json 편집 후 재시작\n")
        f.write("  ※ 기본값 복원: 해당 파일 삭제 시 기본값으로 자동 복원\n")

    def _write_footer(self, f: IO[str]) -> None:
        """푸터"""
        f.write("\n" + "=" * 72 + "\n")
        f.write("리포트 끝\n")
        f.write("=" * 72 + "\n")
