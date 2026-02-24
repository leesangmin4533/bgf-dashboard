"""
CLI 진입점 -- 모든 CLI 명령의 통합 디스패처

Usage:
    python -m src.presentation.cli.main order --now --dry-run --max-items 5
    python -m src.presentation.cli.main predict --store 46513
    python -m src.presentation.cli.main report --weekly
    python -m src.presentation.cli.main alert --expiry
    python -m src.presentation.cli.main store --list
"""

import argparse
import sys
from src.utils.logger import get_logger

logger = get_logger(__name__)


def create_parser() -> argparse.ArgumentParser:
    """CLI 파서 생성"""
    parser = argparse.ArgumentParser(
        prog="bgf-auto",
        description="BGF 자동 발주 시스템 CLI",
    )

    subparsers = parser.add_subparsers(dest="command", help="명령")

    # order 명령
    order_parser = subparsers.add_parser("order", help="발주 실행")
    order_parser.add_argument("--now", action="store_true", help="즉시 실행")
    order_parser.add_argument("--dry-run", action="store_true", default=True, help="테스트 모드")
    order_parser.add_argument("--live", action="store_true", help="실제 발주 실행")
    order_parser.add_argument("--max-items", type=int, default=0, help="최대 발주 수")
    order_parser.add_argument("--min-order-qty", type=int, default=1, help="최소 발주량")
    order_parser.add_argument("--store", type=str, default=None, help="매장 ID")
    order_parser.add_argument("--collect", action="store_true", help="판매 데이터 수집 포함")
    order_parser.add_argument("--categories", nargs="*", help="특정 카테고리만")

    # predict 명령
    predict_parser = subparsers.add_parser("predict", help="예측만 실행")
    predict_parser.add_argument("--store", type=str, default=None, help="매장 ID")
    predict_parser.add_argument("--min-order-qty", type=int, default=0, help="최소 발주량")

    # report 명령
    report_parser = subparsers.add_parser("report", help="리포트 생성")
    report_parser.add_argument("--weekly", action="store_true", help="주간 리포트")
    report_parser.add_argument("--waste", action="store_true", help="폐기 리포트")
    report_parser.add_argument("--store", type=str, default=None, help="매장 ID")

    # alert 명령
    alert_parser = subparsers.add_parser("alert", help="알림 실행")
    alert_parser.add_argument("--expiry", action="store_true", help="유통기한 알림")
    alert_parser.add_argument("--days", type=int, default=3, help="임박 기준 일수")
    alert_parser.add_argument("--store", type=str, default=None, help="매장 ID")

    # store 명령
    store_parser = subparsers.add_parser("store", help="매장 관리")
    store_parser.add_argument("--list", action="store_true", help="매장 목록")
    store_parser.add_argument("--add", type=str, default=None, help="매장 추가 (store_id)")
    store_parser.add_argument("--name", type=str, default="", help="매장명")

    return parser


def _get_store_ctx(store_id=None):
    """StoreContext 생성"""
    from src.settings.store_context import StoreContext
    if store_id:
        return StoreContext.from_store_id(store_id)
    return StoreContext.default()


def cmd_order(args):
    """발주 명령 실행"""
    from src.application.use_cases.daily_order_flow import DailyOrderFlow

    ctx = _get_store_ctx(args.store)
    dry_run = not args.live
    flow = DailyOrderFlow(store_ctx=ctx)
    result = flow.run(
        collect_sales=args.collect,
        dry_run=dry_run,
        max_items=args.max_items,
        min_order_qty=args.min_order_qty,
        categories=args.categories,
    )
    print(f"발주 결과: success={result.get('success')}, "
          f"predictions={result.get('predictions_count', 0)}, "
          f"filtered={result.get('filtered_count', 0)}")
    return result


def cmd_predict(args):
    """예측 명령 실행"""
    from src.application.use_cases.predict_only import PredictOnlyFlow

    ctx = _get_store_ctx(args.store)
    flow = PredictOnlyFlow(store_ctx=ctx)
    predictions = flow.run(min_order_qty=args.min_order_qty)
    print(f"예측 결과: {len(predictions)}개 상품")
    for p in predictions[:10]:
        print(f"  {p.get('item_nm', '')[:20]:20s} qty={p.get('final_order_qty', 0)}")
    if len(predictions) > 10:
        print(f"  ... 외 {len(predictions) - 10}개")
    return predictions


def cmd_report(args):
    """리포트 명령 실행"""
    ctx = _get_store_ctx(args.store)
    if args.weekly:
        from src.application.use_cases.weekly_report_flow import WeeklyReportFlow
        flow = WeeklyReportFlow(store_ctx=ctx)
        result = flow.run()
        print(f"주간 리포트: success={result.get('success')}")
    elif args.waste:
        from src.application.use_cases.waste_report_flow import WasteReportFlow
        flow = WasteReportFlow(store_ctx=ctx)
        result = flow.run()
        print(f"폐기 리포트: success={result.get('success')}")
    else:
        print("--weekly 또는 --waste를 지정하세요")
        return


def cmd_alert(args):
    """알림 명령 실행"""
    ctx = _get_store_ctx(args.store)
    if args.expiry:
        from src.application.use_cases.expiry_alert_flow import ExpiryAlertFlow
        flow = ExpiryAlertFlow(store_ctx=ctx)
        result = flow.run(days=args.days)
        print(f"유통기한 알림: {result}")
    else:
        print("--expiry를 지정하세요")


def cmd_store(args):
    """매장 관리 명령"""
    from src.application.services.store_service import StoreService

    service = StoreService()
    if args.list:
        stores = service.get_active_stores()
        print(f"활성 매장: {len(stores)}개")
        for s in stores:
            print(f"  {s.store_id}: {s.store_name} ({s.location})")
    elif args.add:
        ctx = service.add_store(args.add, args.name or f"매장_{args.add}")
        print(f"매장 추가: {ctx.store_id} ({ctx.store_name})")
    else:
        print("--list 또는 --add를 지정하세요")


def main():
    """CLI 메인 진입점"""
    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "order": cmd_order,
        "predict": cmd_predict,
        "report": cmd_report,
        "alert": cmd_alert,
        "store": cmd_store,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
