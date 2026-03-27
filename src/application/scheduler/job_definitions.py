"""
JobDefinitions -- 스케줄 작업 정의 레지스트리

모든 정기 실행 작업을 선언적으로 정의합니다.
run_scheduler.py의 30+ 래퍼 함수를 대체합니다.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class JobDefinition:
    """스케줄 작업 정의

    Args:
        name: 작업 이름
        flow_class: Use Case 클래스 (run() 메서드 필요)
        schedule: 실행 시간 (예: "07:00", "Mon 08:00")
        multi_store: True면 모든 매장에 대해 병렬 실행
        needs_driver: True면 Selenium 드라이버 필요
        enabled: 활성화 여부
    """
    name: str
    flow_class: str  # 문자열로 저장 (lazy import)
    schedule: str = ""
    schedules: List[str] = field(default_factory=list)  # 다중 스케줄
    multi_store: bool = True
    needs_driver: bool = False
    enabled: bool = True


# 모든 정기 실행 작업 정의
SCHEDULED_JOBS: List[JobDefinition] = [
    JobDefinition(
        name="daily_order",
        flow_class="src.application.use_cases.daily_order_flow.DailyOrderFlow",
        schedule="07:00",
        multi_store=True,
        needs_driver=True,
    ),
    JobDefinition(
        name="expiry_alert",
        flow_class="src.application.use_cases.expiry_alert_flow.ExpiryAlertFlow",
        schedules=["21:00", "13:00", "09:00", "01:00"],
        multi_store=True,
    ),
    JobDefinition(
        name="weekly_report",
        flow_class="src.application.use_cases.weekly_report_flow.WeeklyReportFlow",
        schedule="Mon 08:00",
        multi_store=True,
    ),
    JobDefinition(
        name="waste_report",
        flow_class="src.application.use_cases.waste_report_flow.WasteReportFlow",
        schedule="23:00",
        multi_store=True,
    ),
    JobDefinition(
        name="ml_training",
        flow_class="src.application.use_cases.ml_training_flow.MLTrainingFlow",
        schedule="23:45",
        multi_store=True,  # 매일 증분학습 (90일 윈도우)
    ),
    JobDefinition(
        name="ml_training_full",
        flow_class="src.application.use_cases.ml_training_flow.MLTrainingFlow",
        schedule="Sun 03:00",
        multi_store=True,  # 주간 전체 학습 (90일 윈도우)
    ),
    JobDefinition(
        name="batch_collect",
        flow_class="src.application.use_cases.batch_collect_flow.BatchCollectFlow",
        schedule="11:00",
        multi_store=True,
        needs_driver=True,
    ),
    JobDefinition(
        name="dessert_decision_weekly",
        flow_class="src.application.use_cases.dessert_decision_flow.DessertDecisionFlow",
        schedule="Mon 22:00",
        multi_store=True,
    ),
    JobDefinition(
        name="dessert_decision_biweekly",
        flow_class="src.application.use_cases.dessert_decision_flow.DessertDecisionFlow",
        schedule="Mon 22:15",
        multi_store=True,
    ),
    JobDefinition(
        name="dessert_decision_monthly",
        flow_class="src.application.use_cases.dessert_decision_flow.DessertDecisionFlow",
        schedule="22:30",
        multi_store=True,
    ),
    JobDefinition(
        name="monthly_store_analysis",
        flow_class="src.application.services.analysis_service.run_store_analysis",
        schedule="04:00",
        multi_store=False,  # 자체 매장 루프 사용 (run_scheduler 래퍼에서 처리)
    ),
    JobDefinition(
        name="post_order_pending_sync",
        flow_class="src.application.use_cases.pending_sync_flow.PendingSyncFlow",
        schedule="10:10",
        multi_store=True,
        needs_driver=True,  # 자체 로그인 (독립 세션)
        enabled=True,
    ),
]
