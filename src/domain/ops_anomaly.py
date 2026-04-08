"""운영 지표 이상 판정 (순수 함수)

I/O 없음, 테스트 용이. dict 입력 → OpsAnomaly 리스트 반환.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from src.settings.constants import (
    OPS_PREDICTION_ACCURACY_RATIO,
    OPS_ORDER_FAILURE_RATIO,
    OPS_WASTE_RATE_RATIO,
    OPS_COLLECTION_CONSECUTIVE_DAYS,
    OPS_INTEGRITY_CONSECUTIVE_DAYS,
)

from src.utils.logger import get_logger

logger = get_logger(__name__)


# 영역 매핑: 지표 → 이슈 체인 파일
METRIC_TO_FILE = {
    "prediction_accuracy": "prediction.md",
    "order_failure": "order-execution.md",
    "waste_rate": "expiry-tracking.md",
    "collection_failure": "data-collection.md",
    "integrity_unresolved": "scheduling.md",
    # 2026-04-08: ops-metrics-monitor-extension
    "false_consumed_post_guard": "expiry-tracking.md",
    "verification_log_files_missing": "expiry-tracking.md",
}

# P1 승격 조건
_PREDICTION_MULTI_CATEGORY_P1 = 3  # 3개+ 카테고리 동시 하락
_FOOD_MIDS = ("001", "002", "003", "004", "005")
_INTEGRITY_P1_CONSECUTIVE_DAYS = 14


@dataclass
class OpsAnomaly:
    """감지된 이상 항목"""

    metric_name: str  # 지표명
    issue_chain_file: str  # 등록 대상 파일
    title: str  # 이슈 제목
    priority: str  # P1, P2, P3
    description: str  # 목표/동기 텍스트
    evidence: dict = field(default_factory=dict)  # 감지 근거 수치
    store_id: str = ""  # 감지 매장 (매장 간 중복 제거용)


def detect_anomalies(metrics: dict) -> List[OpsAnomaly]:
    """5개 지표 데이터를 받아 이상 항목 리스트 반환

    Args:
        metrics: {
            "prediction_accuracy": {...},
            "order_failure": {...},
            "waste_rate": {...},
            "collection_failure": {...},
            "integrity_unresolved": {...},
        }
    """
    anomalies: List[OpsAnomaly] = []

    checkers = [
        ("prediction_accuracy", _check_prediction_accuracy),
        ("order_failure", _check_order_failure),
        ("waste_rate", _check_waste_rate),
        ("collection_failure", _check_collection_failure),
        ("integrity_unresolved", _check_integrity_unresolved),
        # 2026-04-08: ops-metrics-monitor-extension
        ("false_consumed", _check_false_consumed_post_guard),
        ("verification_log_files", _check_verification_log_files),
    ]

    for metric_name, checker in checkers:
        data = metrics.get(metric_name)
        if not data or data.get("insufficient_data"):
            continue
        try:
            result = checker(data)
            if isinstance(result, list):
                anomalies.extend(result)
            elif result is not None:
                anomalies.append(result)
        except Exception as e:
            logger.warning(f"[OpsAnomaly] {metric_name} 판정 실패: {e}")

    return anomalies


def _check_prediction_accuracy(data: dict) -> Optional[OpsAnomaly]:
    """카테고리별 7d MAE vs 14d MAE 비교 -> 20% 이상 악화 감지"""
    categories = data.get("categories", [])
    if not categories:
        return None

    degraded = []
    for cat in categories:
        mae_7d = cat.get("mae_7d", 0)
        mae_14d = cat.get("mae_14d", 0)
        if mae_14d > 0 and mae_7d > mae_14d * OPS_PREDICTION_ACCURACY_RATIO:
            degraded.append(cat)

    if not degraded:
        return None

    # 우선순위 결정: 3개+ 카테고리 동시 하락이면 P1
    priority = "P1" if len(degraded) >= _PREDICTION_MULTI_CATEGORY_P1 else "P2"
    mid_cds = [c.get("mid_cd", "?") for c in degraded]
    mid_str = ", ".join(mid_cds[:5])

    worst = max(degraded, key=lambda c: c.get("mae_7d", 0) / max(c.get("mae_14d", 1), 0.01))
    ratio_pct = int(worst["mae_7d"] / max(worst["mae_14d"], 0.01) * 100 - 100)

    return OpsAnomaly(
        metric_name="prediction_accuracy",
        issue_chain_file=METRIC_TO_FILE["prediction_accuracy"],
        title=f"예측 정확도 하락 조사 ({len(degraded)}개 카테고리)",
        priority=priority,
        description=(
            f"카테고리 {mid_str} 7일 MAE가 14일 평균 대비 악화. "
            f"최대 {ratio_pct}% 상승 (mid {worst.get('mid_cd', '?')})"
        ),
        evidence={
            "degraded_count": len(degraded),
            "degraded_categories": degraded,
        },
    )


def _check_order_failure(data: dict) -> Optional[OpsAnomaly]:
    """최근 7d 실패건수 vs 이전 7d -> 50% 이상 증가 감지"""
    recent = data.get("recent_7d", 0)
    prev = data.get("prev_7d", 0)

    if prev == 0:
        # 이전 기간 실패 0건이면 최근 3건 이상일 때만 감지
        if recent >= 3:
            return OpsAnomaly(
                metric_name="order_failure",
                issue_chain_file=METRIC_TO_FILE["order_failure"],
                title="발주 실패 급증 조사",
                priority="P1",
                description=f"최근 7일 발주 실패 {recent}건 (이전 7일 0건)",
                evidence={"recent_7d": recent, "prev_7d": prev},
            )
        return None

    if recent > prev * OPS_ORDER_FAILURE_RATIO:
        ratio_pct = int(recent / prev * 100 - 100)
        return OpsAnomaly(
            metric_name="order_failure",
            issue_chain_file=METRIC_TO_FILE["order_failure"],
            title="발주 실패 급증 조사",
            priority="P1",  # 항상 P1
            description=f"최근 7일 발주 실패 {recent}건, 이전 7일 {prev}건 대비 {ratio_pct}% 증가",
            evidence={"recent_7d": recent, "prev_7d": prev, "ratio": recent / prev},
        )

    return None


def _check_waste_rate(data: dict) -> List[OpsAnomaly]:
    """카테고리별 7d 폐기율 vs 30d 평균 -> 1.5배 이상 감지 (복수 가능)"""
    categories = data.get("categories", [])
    anomalies = []

    for cat in categories:
        rate_7d = cat.get("rate_7d", 0)
        rate_30d = cat.get("rate_30d", 0)
        mid_cd = cat.get("mid_cd", "?")

        if rate_30d <= 0:
            continue

        if rate_7d > rate_30d * OPS_WASTE_RATE_RATIO:
            # 우선순위: 푸드(001~005)면 P1
            priority = "P1" if mid_cd in _FOOD_MIDS else "P2"
            ratio_pct = int(rate_7d / rate_30d * 100 - 100)

            anomalies.append(OpsAnomaly(
                metric_name="waste_rate",
                issue_chain_file=METRIC_TO_FILE["waste_rate"],
                title=f"{mid_cd} 폐기율 상승 조사",
                priority=priority,
                description=(
                    f"카테고리 {mid_cd} 7일 폐기율 {rate_7d:.1%}이 "
                    f"30일 평균 {rate_30d:.1%} 대비 {ratio_pct}% 상승"
                ),
                evidence={"mid_cd": mid_cd, "rate_7d": rate_7d, "rate_30d": rate_30d},
            ))

    return anomalies


def _check_collection_failure(data: dict) -> Optional[OpsAnomaly]:
    """동일 수집 유형 3일 연속 실패 감지"""
    types = data.get("types", [])

    failing = []
    for t in types:
        if t.get("consecutive_fails", 0) >= OPS_COLLECTION_CONSECUTIVE_DAYS:
            failing.append(t)

    if not failing:
        return None

    type_names = [t.get("type", "?") for t in failing]
    worst = max(failing, key=lambda t: t.get("consecutive_fails", 0))

    return OpsAnomaly(
        metric_name="collection_failure",
        issue_chain_file=METRIC_TO_FILE["collection_failure"],
        title=f"데이터 수집 연속 실패 ({', '.join(type_names)})",
        priority="P1",  # 항상 P1
        description=(
            f"수집 유형 {', '.join(type_names)}이(가) "
            f"최대 {worst['consecutive_fails']}일 연속 실패"
        ),
        evidence={"failing_types": failing},
    )


def _check_integrity_unresolved(data: dict) -> Optional[OpsAnomaly]:
    """특정 check_name 7일 연속 anomaly > 0 감지"""
    checks = data.get("checks", [])

    unresolved = []
    for c in checks:
        if c.get("consecutive_days", 0) >= OPS_INTEGRITY_CONSECUTIVE_DAYS:
            unresolved.append(c)

    if not unresolved:
        return None

    worst = max(unresolved, key=lambda c: c.get("consecutive_days", 0))
    names = [c.get("name", "?") for c in unresolved]

    # 14일 연속이면 P1 승격
    priority = "P1" if worst["consecutive_days"] >= _INTEGRITY_P1_CONSECUTIVE_DAYS else "P2"

    return OpsAnomaly(
        metric_name="integrity_unresolved",
        issue_chain_file=METRIC_TO_FILE["integrity_unresolved"],
        title=f"자전 시스템 미해결 항목 ({', '.join(names[:3])})",
        priority=priority,
        description=(
            f"체크 {worst.get('name', '?')}이(가) {worst['consecutive_days']}일 연속 "
            f"anomaly 발생 중 ({len(unresolved)}개 항목)"
        ),
        evidence={"unresolved_checks": unresolved},
    )


def _check_false_consumed_post_guard(data: dict) -> Optional[OpsAnomaly]:
    """가드 우회 감지: 만료 24h 이내 시점에 consumed 마킹된 단기유통기한 배치

    2026-04-08 도입 (ops-metrics-monitor-extension).
    BatchSync FR-02 우회 사건 후속. 가드 위반의 SQL 정의 그대로 사용.
    """
    cnt = data.get("cnt", 0)
    if cnt == 0:
        return None
    sample = (data.get("sample_items") or "")[:60]
    return OpsAnomaly(
        metric_name="false_consumed_post_guard",
        issue_chain_file=METRIC_TO_FILE["false_consumed_post_guard"],
        title=f"BatchSync 가드 우회 의심 {cnt}건 (24h 내)",
        priority="P2",
        description=(
            f"단기유통기한(<=7일) 배치 {cnt}건이 만료 24h 이내에 consumed 마킹됨. "
            f"가드 우회 경로 또는 신규 false consumed 재발 의심. "
            f"최근 발생: {data.get('latest_at')}, 샘플: {sample}"
        ),
        evidence={
            "count": cnt,
            "latest_at": data.get("latest_at"),
            "sample_items": data.get("sample_items"),
        },
    )


def _check_verification_log_files(data: dict) -> Optional[OpsAnomaly]:
    """매장별 검증 로그 파일 누락 감지

    2026-04-08 도입 (ops-metrics-monitor-extension).
    waste_verification_reporter 분리 로직(fce1594) 회귀 감지용.
    """
    missing_count = data.get("missing_count", 0)
    if missing_count == 0:
        return None
    missing_stores = data.get("missing_stores", [])
    yesterday = data.get("yesterday", "?")
    expected = data.get("expected_count", 0)
    return OpsAnomaly(
        metric_name="verification_log_files_missing",
        issue_chain_file=METRIC_TO_FILE["verification_log_files_missing"],
        title=f"매장별 검증 로그 {missing_count}개 누락 ({yesterday})",
        priority="P3",
        description=(
            f"어제({yesterday}) 매장별 폐기 검증 로그 파일이 {missing_count}개 누락. "
            f"예상 {expected}, 누락 매장: {', '.join(missing_stores)}. "
            f"waste_verification_reporter 분리 로직 또는 23:00 waste_report_flow 회귀 의심."
        ),
        evidence={
            "missing_count": missing_count,
            "expected_count": expected,
            "missing_stores": missing_stores,
            "yesterday": yesterday,
        },
    )
