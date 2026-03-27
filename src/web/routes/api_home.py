"""홈 대시보드 REST API

DashboardService를 통해 데이터를 제공합니다.
"""
import os
import sqlite3
import sys
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request, current_app, session

from src.application.services.dashboard_service import DashboardService
from src.infrastructure.database.connection import DBRouter
from src.utils.logger import get_logger
from src.web.routes.api_auth import admin_required, login_required

logger = get_logger(__name__)

home_bp = Blueprint("home", __name__)

# API 응답 캐시 (store_id별 TTL 기반)
_status_cache = {}  # {cache_key: {"data": ..., "expires": ...}}
_STATUS_CACHE_TTL = 5  # 초


def _is_pid_running(pid):
    """PID가 실행 중인지 확인 (Windows/Unix 호환)"""
    if sys.platform == 'win32':
        import ctypes
        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


@home_bp.route("/status", methods=["GET"])
def status():
    """홈 대시보드 통합 데이터 (5초 캐시)"""
    now = time.time()
    store_id = request.args.get('store_id')
    cache_key = f"store:{store_id or 'all'}"

    cached = _status_cache.get(cache_key)
    if cached and cached["data"] is not None and now < cached["expires"]:
        return jsonify(cached["data"])

    project_root = current_app.config["PROJECT_ROOT"]
    if store_id:
        svc = DashboardService(store_id=store_id)
    else:
        db_path = current_app.config.get("DB_PATH")
        svc = DashboardService(db_path=db_path)

    pred_cache = current_app.config.get("LAST_PREDICTIONS", {})
    predictions = pred_cache.get(store_id) if store_id else None

    data = {
        "scheduler": _get_scheduler_status(project_root),
        "last_order": svc.get_last_order(),
        "today_summary": svc.get_today_summary(cached_predictions=predictions),
        "expiry_risk": svc.get_expiry_risk(),
        "pipeline": svc.get_pipeline_status(
            script_task=current_app.config.get("SCRIPT_TASK")
        ),
        "recent_events": svc.get_recent_events(),
        "fail_reasons": svc.get_fail_reasons(),
        "order_trend_7d": svc.get_order_trend_7d(),
        "sales_trend_7d": svc.get_sales_trend_7d(),
        "waste_trend_7d": svc.get_waste_trend_7d(),
    }

    _status_cache[cache_key] = {"data": data, "expires": now + _STATUS_CACHE_TTL}

    return jsonify(data)


@home_bp.route("/store-comparison", methods=["GET"])
def store_comparison():
    """매장 간 비교 요약 API"""
    svc = DashboardService()
    data = svc.get_store_comparison()
    return jsonify({"stores": data, "count": len(data)})


@home_bp.route("/scheduler/start", methods=["POST"])
@admin_required
def scheduler_start():
    """스케줄러 백그라운드 시작 (admin only)"""
    project_root = current_app.config["PROJECT_ROOT"]
    sched_status = _get_scheduler_status(project_root)
    if sched_status["running"]:
        return jsonify({"error": "이미 동작중입니다", "pid": sched_status["pid"]}), 409

    scheduler_script = Path(project_root) / "run_scheduler.py"
    if not scheduler_script.exists():
        return jsonify({"error": "run_scheduler.py를 찾을 수 없습니다"}), 404

    try:
        proc = subprocess.Popen(
            [sys.executable, str(scheduler_script)],
            cwd=str(project_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return jsonify({"ok": True, "pid": proc.pid})
    except Exception as e:
        logger.error(f"스케줄러 시작 실패: {e}")
        return jsonify({"error": "스케줄러 시작에 실패했습니다"}), 500


@home_bp.route("/scheduler/stop", methods=["POST"])
@admin_required
def scheduler_stop():
    """스케줄러 중지 (admin only)"""
    project_root = current_app.config["PROJECT_ROOT"]
    sched_status = _get_scheduler_status(project_root)
    if not sched_status["running"]:
        return jsonify({"error": "스케줄러가 동작중이 아닙니다"}), 409

    pid = sched_status["pid"]
    try:
        if sys.platform == "win32":
            import ctypes
            PROCESS_TERMINATE = 0x0001
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if handle:
                kernel32.TerminateProcess(handle, 0)
                kernel32.CloseHandle(handle)
        else:
            import signal
            os.kill(pid, signal.SIGTERM)

        lock_file = Path(project_root) / "data" / "scheduler.lock"
        if lock_file.exists():
            try:
                lock_file.unlink()
            except OSError:
                pass

        return jsonify({"ok": True, "pid": pid})
    except Exception as e:
        logger.error(f"스케줄러 중지 실패: {e}")
        return jsonify({"error": "스케줄러 중지에 실패했습니다"}), 500


# 스케줄러 작업 목록 (run_scheduler.py 기반 정적 정의)
_SCHEDULER_JOBS = [
    {"time": "06:30", "freq": "매일", "name": "토큰 갱신", "desc": "카카오 API 토큰 사전 갱신"},
    {"time": "07:00", "freq": "매일", "name": "데이터 수집 + 자동 발주", "desc": "판매 데이터 수집 및 발주 실행"},
    {"time": "07:30", "freq": "매일", "name": "배송 확인 (2차)", "desc": "2차 배송 도착 후 배치 동기화"},
    {"time": "09:00", "freq": "매일", "name": "폐기 전 수집", "desc": "10:00 폐기 알림용 데이터 수집"},
    {"time": "09:30", "freq": "매일", "name": "폐기 알림 (10시)", "desc": "10:00 폐기 30분 전 알림 발송"},
    {"time": "11:00", "freq": "매일", "name": "상품 상세 수집", "desc": "유통기한 미등록 상품 일괄 수집"},
    {"time": "13:00", "freq": "매일", "name": "폐기 전 수집", "desc": "14:00 폐기 알림용 데이터 수집"},
    {"time": "13:30", "freq": "매일", "name": "폐기 알림 (14시)", "desc": "14:00 폐기 30분 전 알림 발송"},
    {"time": "20:30", "freq": "매일", "name": "배송 확인 (1차)", "desc": "1차 배송 도착 후 배치 동기화"},
    {"time": "21:00", "freq": "매일", "name": "폐기 전 수집", "desc": "22:00 폐기 알림용 데이터 수집"},
    {"time": "21:30", "freq": "매일", "name": "폐기 알림 (22시)", "desc": "22:00 폐기 30분 전 알림 발송"},
    {"time": "22:00", "freq": "매일", "name": "폐기 전 수집", "desc": "00:00 만료(빵) 알림용 수집"},
    {"time": "23:00", "freq": "매일", "name": "폐기 보고서 + 만료 알림", "desc": "일일 폐기 보고서(엑셀) + 자정 만료 알림"},
    {"time": "23:30", "freq": "매일", "name": "배치 만료 처리", "desc": "유통기한 만료 배치 폐기 처리"},
    {"time": "01:00", "freq": "매일", "name": "폐기 전 수집", "desc": "02:00 폐기 알림용 데이터 수집"},
    {"time": "01:30", "freq": "매일", "name": "폐기 알림 (02시)", "desc": "02:00 폐기 30분 전 알림 발송"},
    {"time": "08:00", "freq": "매주 월", "name": "주간 리포트", "desc": "카테고리 트렌드 + 예측 정확도 분석"},
]


@home_bp.route("/scheduler/jobs", methods=["GET"])
def scheduler_jobs():
    """스케줄러 등록 작업 목록"""
    project_root = current_app.config["PROJECT_ROOT"]
    sched_status = _get_scheduler_status(project_root)

    now = datetime.now()
    now_hm = now.strftime("%H:%M")
    today_dow = now.weekday()  # 0=월요일

    jobs = []
    for j in _SCHEDULER_JOBS:
        # 다음 실행 여부 판별
        is_weekly = "주" in j["freq"]
        if is_weekly:
            done_today = today_dow != 0 or now_hm > j["time"]
        else:
            done_today = now_hm > j["time"]

        jobs.append({
            "time": j["time"],
            "freq": j["freq"],
            "name": j["name"],
            "desc": j["desc"],
            "done_today": done_today,
        })

    return jsonify({
        "running": sched_status["running"],
        "pid": sched_status["pid"],
        "total_jobs": len(jobs),
        "jobs": jobs,
    })


@home_bp.route("/summary", methods=["GET"])
@login_required
def summary():
    """점주용 홈 요약 API (발주핏 v2 대시보드용)

    기존 /status 데이터를 간소화하여 반환합니다.
    """
    store_id = session.get("store_id") or request.args.get("store_id")
    if store_id:
        svc = DashboardService(store_id=store_id)
    else:
        db_path = current_app.config.get("DB_PATH")
        svc = DashboardService(db_path=db_path)

    project_root = current_app.config["PROJECT_ROOT"]
    last_order = svc.get_last_order()

    # 발주 상태 판정
    if last_order.get("success_count", 0) > 0 and last_order.get("fail_count", 0) == 0:
        order_status = "completed"
    elif last_order.get("fail_count", 0) > 0:
        order_status = "failed"
    else:
        sched = _get_scheduler_status(project_root)
        order_status = "pending" if sched.get("running") else "pending"

    # 매출 데이터 직접 쿼리 (hourly_sales_detail.sale_amt 실제 매출 우선, 폴백: qty*price)
    today_sales = 0
    today_sales_diff = 0
    monthly_profit = 0
    try:
        conn = _get_summary_conn(store_id)
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

            # 오늘 매출 (hourly_sales_detail 우선)
            row_today = conn.execute("""
                SELECT COALESCE(SUM(sale_amt), 0)
                FROM hourly_sales_detail
                WHERE sales_date = ?
            """, (today,)).fetchone()
            today_sales = row_today[0] if row_today else 0
            if not today_sales:
                row_today = conn.execute("""
                    SELECT COALESCE(SUM(ds.sale_qty * COALESCE(pd.sell_price, 0)), 0)
                    FROM daily_sales ds
                    LEFT JOIN product_details pd ON ds.item_cd = pd.item_cd
                    WHERE ds.sales_date = ?
                """, (today,)).fetchone()
                today_sales = row_today[0] if row_today else 0

            # 어제 매출 (hourly_sales_detail 우선)
            row_yest = conn.execute("""
                SELECT COALESCE(SUM(sale_amt), 0)
                FROM hourly_sales_detail
                WHERE sales_date = ?
            """, (yesterday,)).fetchone()
            yest_sales = row_yest[0] if row_yest else 0
            if not yest_sales:
                row_yest = conn.execute("""
                    SELECT COALESCE(SUM(ds.sale_qty * COALESCE(pd.sell_price, 0)), 0)
                    FROM daily_sales ds
                    LEFT JOIN product_details pd ON ds.item_cd = pd.item_cd
                    WHERE ds.sales_date = ?
                """, (yesterday,)).fetchone()
                yest_sales = row_yest[0] if row_yest else 0

            if yest_sales > 0:
                today_sales_diff = round(
                    (today_sales - yest_sales) / yest_sales * 100, 1
                )

            # 이번달 순수익 (hourly_sales_detail.sale_amt × margin_rate 기반)
            row_month = conn.execute("""
                SELECT COALESCE(SUM(
                    hsd.sale_amt * COALESCE(pd.margin_rate, 25) / 100.0
                ), 0)
                FROM hourly_sales_detail hsd
                LEFT JOIN product_details pd ON hsd.item_cd = pd.item_cd
                WHERE hsd.sales_date >= date('now', 'start of month')
            """).fetchone()
            monthly_profit = int(row_month[0]) if row_month else 0
            if not monthly_profit:
                row_month = conn.execute("""
                    SELECT COALESCE(SUM(
                        ds.sale_qty * COALESCE(pd.sell_price, 0)
                        * COALESCE(pd.margin_rate, 25) / 100.0
                    ), 0)
                    FROM daily_sales ds
                    LEFT JOIN product_details pd ON ds.item_cd = pd.item_cd
                    WHERE ds.sales_date >= date('now', 'start of month')
                """).fetchone()
                monthly_profit = int(row_month[0]) if row_month else 0
        finally:
            conn.close()
    except Exception as e:
        logger.warning("홈 매출 쿼리 실패: %s", e)

    # 폐기 위험
    expiry = svc.get_expiry_risk()
    expiry_items = expiry.get("items", []) if isinstance(expiry, dict) else []
    waste_risk_count = len(expiry_items)

    return jsonify({
        "order_status": order_status,
        "order_time": last_order.get("time", "07:00"),
        "today_sales": today_sales,
        "today_sales_diff": today_sales_diff,
        "waste_risk_count": waste_risk_count,
        "monthly_profit": monthly_profit,
        "order_success": last_order.get("success_count", 0),
        "order_fail": last_order.get("fail_count", 0),
    })


@home_bp.route("/waste-risk-products", methods=["GET"])
@login_required
def waste_risk_products():
    """폐기 위험 상품 목록 (점주 대시보드용)"""
    store_id = session.get("store_id") or request.args.get("store_id")
    if store_id:
        svc = DashboardService(store_id=store_id)
    else:
        db_path = current_app.config.get("DB_PATH")
        svc = DashboardService(db_path=db_path)

    expiry = svc.get_expiry_risk()
    items = expiry.get("items", []) if isinstance(expiry, dict) else []

    result = []
    for item in items[:20]:
        expiry_date = item.get("expiry_date", "")
        # 만료 시간 포맷팅
        expires_at = expiry_date
        try:
            if " " in expiry_date:
                # %H:%M:%S 또는 %H:%M 두 포맷 모두 처리
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                    try:
                        dt = datetime.strptime(expiry_date, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    dt = None
                if dt:
                    today = datetime.now().strftime("%Y-%m-%d")
                    if dt.strftime("%Y-%m-%d") == today:
                        expires_at = "오늘 " + dt.strftime("%H:%M")
                    else:
                        expires_at = dt.strftime("%m/%d %H:%M")
            else:
                dt = datetime.strptime(expiry_date, "%Y-%m-%d")
                today = datetime.now().strftime("%Y-%m-%d")
                if expiry_date == today:
                    expires_at = "오늘"
                else:
                    expires_at = dt.strftime("%m/%d")
        except Exception:
            pass

        result.append({
            "name": item.get("item_nm", ""),
            "expires_at": expires_at,
            "quantity": item.get("remaining_qty", 0),
        })

    return jsonify(result)


@home_bp.route("/profit-breakdown", methods=["GET"])
@login_required
def profit_breakdown():
    """이번달 순수익 중분류별 비중 (비중 높은 순 정렬)"""
    store_id = session.get("store_id") or request.args.get("store_id")
    items = []
    total_profit = 0
    try:
        conn = _get_summary_conn(store_id)
        try:
            rows = conn.execute("""
                SELECT COALESCE(p.mid_cd, '기타') AS mid_cd,
                       COALESCE(mc.mid_nm, p.mid_cd, '기타') AS mid_nm,
                       COALESCE(SUM(hsd.sale_amt), 0) AS total_sales,
                       COALESCE(SUM(
                           hsd.sale_amt * COALESCE(pd.margin_rate, 25) / 100.0
                       ), 0) AS profit
                FROM hourly_sales_detail hsd
                LEFT JOIN products p ON hsd.item_cd = p.item_cd
                LEFT JOIN product_details pd ON hsd.item_cd = pd.item_cd
                LEFT JOIN mid_categories mc ON p.mid_cd = mc.mid_cd
                WHERE hsd.sales_date >= date('now', 'start of month')
                GROUP BY COALESCE(p.mid_cd, '기타')
                HAVING profit > 0
                ORDER BY profit DESC
            """).fetchall()
            if not rows:
                rows = conn.execute("""
                    SELECT COALESCE(p.mid_cd, '기타') AS mid_cd,
                           COALESCE(mc.mid_nm, p.mid_cd, '기타') AS mid_nm,
                           COALESCE(SUM(ds.sale_qty * COALESCE(pd.sell_price, 0)), 0) AS total_sales,
                           COALESCE(SUM(
                               ds.sale_qty * COALESCE(pd.sell_price, 0)
                               * COALESCE(pd.margin_rate, 25) / 100.0
                           ), 0) AS profit
                    FROM daily_sales ds
                    LEFT JOIN products p ON ds.item_cd = p.item_cd
                    LEFT JOIN product_details pd ON ds.item_cd = pd.item_cd
                    LEFT JOIN mid_categories mc ON p.mid_cd = mc.mid_cd
                    WHERE ds.sales_date >= date('now', 'start of month')
                    GROUP BY COALESCE(p.mid_cd, '기타')
                    HAVING profit > 0
                    ORDER BY profit DESC
                """).fetchall()
            for r in rows:
                profit = int(r[3])
                total_profit += profit
                items.append({
                    "mid_cd": r[0],
                    "name": r[1],
                    "sales": int(r[2]),
                    "profit": profit,
                })
        finally:
            conn.close()
    except Exception as e:
        logger.warning("순수익 비중 쿼리 실패: %s", e)

    # 비중(%) 계산
    for item in items:
        item["pct"] = round(item["profit"] / total_profit * 100, 1) if total_profit > 0 else 0

    return jsonify({"items": items[:30], "total_profit": total_profit})


@home_bp.route("/analytics/weekly", methods=["GET"])
@login_required
def analytics_weekly():
    """이번주 현황 API (점주 대시보드용)"""
    store_id = session.get("store_id") or request.args.get("store_id")
    if store_id:
        svc = DashboardService(store_id=store_id)
    else:
        db_path = current_app.config.get("DB_PATH")
        svc = DashboardService(db_path=db_path)

    # 직접 쿼리: 날짜별 매출액 + 요일 라벨
    day_labels = ["월", "화", "수", "목", "금", "토", "일"]
    daily_sales = []
    days = []
    date_range = ""
    total_sales = 0
    total_ordered = 0
    total_waste = 0

    try:
        conn = _get_summary_conn(store_id)
        try:
            # 7일 매출 (hourly_sales_detail.sale_amt 실제 매출 우선, 폴백: qty*price)
            sales_rows = conn.execute("""
                SELECT sales_date, COALESCE(SUM(sale_amt), 0)
                FROM hourly_sales_detail
                WHERE sales_date >= date('now', '-7 days')
                GROUP BY sales_date
                ORDER BY sales_date ASC
            """).fetchall()
            if not sales_rows:
                sales_rows = conn.execute("""
                    SELECT ds.sales_date, COALESCE(SUM(ds.sale_qty * COALESCE(pd.sell_price, 0)), 0)
                    FROM daily_sales ds
                    LEFT JOIN product_details pd ON ds.item_cd = pd.item_cd
                    WHERE ds.sales_date >= date('now', '-7 days')
                    GROUP BY ds.sales_date
                    ORDER BY ds.sales_date ASC
                """).fetchall()

            for r in sales_rows:
                daily_sales.append(r[1])
                try:
                    dt = datetime.strptime(r[0], "%Y-%m-%d")
                    days.append(day_labels[dt.weekday()])
                except Exception:
                    days.append("")

            if sales_rows and len(sales_rows) >= 2:
                try:
                    start = datetime.strptime(sales_rows[0][0], "%Y-%m-%d")
                    end = datetime.strptime(sales_rows[-1][0], "%Y-%m-%d")
                    date_range = "%d월 %d일 - %d월 %d일" % (
                        start.month, start.day, end.month, end.day
                    )
                except Exception:
                    pass

            total_sales = sum(daily_sales)

            # 발주 건수
            order_row = conn.execute("""
                SELECT COALESCE(SUM(order_qty), 0), COUNT(*)
                FROM order_tracking
                WHERE order_date >= date('now', '-7 days')
            """).fetchone()
            total_ordered = order_row[1] if order_row else 0

            # 폐기 건수
            waste_row = conn.execute("""
                SELECT COALESCE(SUM(disuse_qty), 0), COUNT(*)
                FROM daily_sales
                WHERE sales_date >= date('now', '-7 days')
                  AND disuse_qty > 0
            """).fetchone()
            total_waste = waste_row[1] if waste_row else 0
        finally:
            conn.close()
    except Exception as e:
        logger.warning("주간 분석 쿼리 실패: %s", e)

    avg_daily = int(total_sales / len(daily_sales)) if daily_sales else 0

    # 발주 적중률
    order_accuracy = 0
    if total_ordered > 0:
        order_accuracy = round(
            max(0, min(100, (1 - total_waste / max(total_ordered, 1)) * 100)), 1
        )

    # 시간대별 매출 (hourly_sales_detail 실제 매출, 당일)
    hourly_data = []
    try:
        h_conn = _get_summary_conn(store_id)
        try:
            today_str = datetime.now().strftime("%Y-%m-%d")
            h_rows = h_conn.execute("""
                SELECT hour, COALESCE(SUM(sale_amt), 0)
                FROM hourly_sales_detail
                WHERE sales_date = ?
                GROUP BY hour
                ORDER BY hour ASC
            """, (today_str,)).fetchall()
            hourly_map = {r[0]: r[1] for r in h_rows}
            for h in range(24):
                hourly_data.append(hourly_map.get(h, 0))
        finally:
            h_conn.close()
    except Exception as e:
        logger.warning("시간대별 매출 조회 실패: %s", e)
        hourly_data = [0] * 24

    return jsonify({
        "daily_sales": daily_sales,
        "days": days,
        "date_range": date_range,
        "order_accuracy": order_accuracy,
        "waste_saved": total_waste,
        "waste_saved_diff": 0,
        "total_sales": total_sales,
        "avg_daily_sales": avg_daily,
        "order_count": total_ordered,
        "waste_count": total_waste,
        "hourly_sales": hourly_data,
    })


def _get_summary_conn(store_id):
    """매장 DB 연결 반환 (summary/weekly API용)"""
    if store_id:
        try:
            from src.infrastructure.database.connection import attach_common_with_views
            conn = DBRouter.get_store_connection(store_id)
            return attach_common_with_views(conn, store_id)
        except Exception:
            pass
    db_path = current_app.config.get("DB_PATH")
    if db_path:
        return sqlite3.connect(db_path, timeout=10)
    return DBRouter.get_connection("store")


def _get_scheduler_status(project_root):
    """스케줄러 동작 상태 확인 (stale lock 자동 정리)"""
    lock_file = Path(project_root) / "data" / "scheduler.lock"
    running = False
    pid = None

    if lock_file.exists():
        try:
            pid = int(lock_file.read_text().strip())
            running = _is_pid_running(pid)
        except (ValueError, FileNotFoundError):
            running = False

        if not running:
            try:
                lock_file.unlink()
            except OSError:
                pass
            pid = None

    return {
        "running": running,
        "pid": pid,
    }
