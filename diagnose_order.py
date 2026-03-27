#!/usr/bin/env python3
"""
발주 진단 CLI 도구 (diagnose_order.py)

특정 상품의 발주 계산 과정을 역추적하여 진단 결과를 출력합니다.

사용법:
    python diagnose_order.py --store 46513 --item 8801043022262 --date 2026-03-07
    python diagnose_order.py --session e690a029 --item 8801043022262
"""

import argparse
import io
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Windows cp949 인코딩 에러 방지
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace"
    )

# 프로젝트 루트
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "bgf_auto.log"


# ── DB 연결 헬퍼 ──────────────────────────────────────────────


def get_store_conn(store_id: str) -> sqlite3.Connection:
    """매장별 DB 연결 (+ common ATTACH)"""
    store_path = DATA_DIR / "stores" / f"{store_id}.db"
    if not store_path.exists():
        raise FileNotFoundError(f"매장 DB 없음: {store_path}")
    conn = sqlite3.connect(str(store_path), timeout=10)
    conn.row_factory = sqlite3.Row
    common_path = DATA_DIR / "common.db"
    if common_path.exists():
        conn.execute(f"ATTACH DATABASE '{common_path}' AS common")
    return conn


def get_common_conn() -> sqlite3.Connection:
    """공통 DB 연결"""
    common_path = DATA_DIR / "common.db"
    if not common_path.exists():
        raise FileNotFoundError(f"공통 DB 없음: {common_path}")
    conn = sqlite3.connect(str(common_path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# ── 로그 파서 ─────────────────────────────────────────────────


def find_session_from_log(date_str: str, store_id: str) -> str:
    """로그에서 해당 날짜+매장의 최신 세션 ID 반환

    로그 포맷: 2026-03-07 07:01:51 | INFO     | e690a029 | src... | Optimized flow started | session=e690a029
    """
    if not LOG_FILE.exists():
        return "N/A"

    # session 시작 라인 매칭
    pattern = re.compile(
        rf"^{re.escape(date_str)}\s+\S+\s*\|\s*\w+\s*\|\s*(\w{{8}})\s*\|.*"
        r"Optimized flow started \| session=(\w{8})"
    )
    last_sid = None
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pattern.match(line)
                if m:
                    last_sid = m.group(2)
    except Exception:
        pass
    return last_sid if last_sid else "N/A"


def find_date_from_session(session_id: str):
    """세션 ID로 로그에서 날짜+매장 추출

    로그 포맷: {date} {time} | {level} | {session_id} | {logger} | {message}
    매장 ID: [46513] 또는 store_id=46513 또는 매장 DB 초기화 완료: 46513
    """
    if not LOG_FILE.exists():
        return None, None

    # 세션 ID가 로그의 3번째 컬럼
    pattern = re.compile(
        rf"^(\d{{4}}-\d{{2}}-\d{{2}})\s+\S+\s*\|\s*\w+\s*\|\s*{re.escape(session_id)}\s*\|"
    )
    date_str = None
    store_id = None
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pattern.match(line)
                if m:
                    if not date_str:
                        date_str = m.group(1)
                    if not store_id:
                        # [46513] 또는 store_id 46513 또는 매장 DB 초기화: 46513
                        sm = re.search(r"\[(\d{5})\]", line)
                        if not sm:
                            sm = re.search(r"(?:store[_= :]+|매장.*?:\s*)(\d{5})", line, re.IGNORECASE)
                        if sm:
                            store_id = sm.group(1)
                    if date_str and store_id:
                        break
    except Exception:
        pass
    return date_str, store_id


def parse_log_build_info(date_str: str):
    """로그에서 [BUILD] 정보 파싱 → (commit, scheduler_started)"""
    commit = "N/A"
    scheduler_started = "N/A"
    if not LOG_FILE.exists():
        return commit, scheduler_started

    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if date_str not in line:
                    continue
                if "[BUILD]" in line or "commit" in line.lower():
                    cm = re.search(r"commit[=: ]+([a-f0-9]{7,40})", line, re.IGNORECASE)
                    if cm:
                        commit = cm.group(1)
                if "scheduler" in line.lower() and "start" in line.lower():
                    tm = re.search(r"(\d{2}:\d{2}:\d{2})", line)
                    if tm:
                        scheduler_started = tm.group(1)
    except Exception:
        pass
    return commit, scheduler_started


# ── 데이터 조회 함수들 ────────────────────────────────────────


def query_stock(conn, item_cd: str):
    """realtime_inventory에서 재고 조회"""
    try:
        row = conn.execute(
            "SELECT stock_qty, pending_qty, order_unit_qty, is_available, "
            "is_cut_item, query_fail_count, unavail_reason, queried_at "
            "FROM realtime_inventory WHERE item_cd = ? "
            "ORDER BY queried_at DESC LIMIT 1",
            (item_cd,),
        ).fetchone()
        if row:
            return dict(row)
        return None
    except Exception as e:
        print(f"  [WARN] realtime_inventory 조회 오류: {e}")
        return "UNKNOWN"


def query_pending_receiving(conn, item_cd: str):
    """receiving_history에서 미입고 건 조회"""
    try:
        rows = conn.execute(
            "SELECT receiving_date, order_date, order_qty, receiving_qty, delivery_type "
            "FROM receiving_history WHERE item_cd = ? "
            "AND (receiving_qty IS NULL OR receiving_qty = 0) "
            "ORDER BY order_date DESC LIMIT 5",
            (item_cd,),
        ).fetchall()
        return [dict(r) for r in rows] if rows else None
    except Exception:
        return "UNKNOWN"


def query_product_details(common_conn, item_cd: str):
    """product_details에서 상품 정보 조회 (common.db)"""
    try:
        row = common_conn.execute(
            "SELECT item_cd, item_nm, expiration_days, orderable_day, orderable_status, "
            "order_unit_qty, sell_price, margin_rate, large_cd, small_cd, small_nm "
            "FROM product_details WHERE item_cd = ?",
            (item_cd,),
        ).fetchone()
        if row:
            return dict(row)
        return None
    except Exception:
        return "UNKNOWN"


def query_product_name(common_conn, item_cd: str):
    """products에서 상품명+중분류 조회"""
    try:
        row = common_conn.execute(
            "SELECT item_nm, mid_cd FROM products WHERE item_cd = ?",
            (item_cd,),
        ).fetchone()
        if row:
            return dict(row)
        return None
    except Exception:
        return "UNKNOWN"


def query_promo(conn, item_cd: str, date_str: str):
    """promotions에서 해당 날짜 행사 정보 조회"""
    try:
        row = conn.execute(
            "SELECT promo_type, start_date, end_date, is_active "
            "FROM promotions WHERE item_cd = ? "
            "AND start_date <= ? AND end_date >= ? AND is_active = 1 "
            "ORDER BY start_date DESC LIMIT 1",
            (item_cd, date_str, date_str),
        ).fetchone()
        if row:
            return dict(row)
        return None
    except Exception:
        return "UNKNOWN"


def _get_prediction_columns(conn):
    """prediction_logs 테이블의 실제 컬럼 목록 조회"""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(prediction_logs)").fetchall()]
        return cols
    except Exception:
        return []


def query_prediction(conn, item_cd: str, date_str: str):
    """prediction_logs에서 해당 날짜의 최신 예측 조회

    prediction_date(예측 수행일) 매칭 -> 없으면 target_date(예측 대상일) 매칭
    """
    try:
        # 실제 존재하는 컬럼만 SELECT
        all_cols = _get_prediction_columns(conn)
        want = [
            "prediction_date", "target_date", "item_cd", "mid_cd",
            "predicted_qty", "adjusted_qty", "safety_stock", "current_stock",
            "order_qty", "weekday_coef", "confidence", "model_type",
            "stock_source", "pending_source", "is_stock_stale", "created_at",
        ]
        select_cols = [c for c in want if c in all_cols]
        if not select_cols:
            select_cols = ["*"]
        col_str = ", ".join(select_cols)

        # 1) prediction_date 매칭 (발주일 = 예측 수행일)
        row = conn.execute(
            f"SELECT {col_str} FROM prediction_logs "
            "WHERE item_cd = ? AND prediction_date = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (item_cd, date_str),
        ).fetchone()
        if row:
            return dict(row)

        # 2) target_date 매칭 (배송일 = 예측 대상일)
        row = conn.execute(
            f"SELECT {col_str} FROM prediction_logs "
            "WHERE item_cd = ? AND target_date = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (item_cd, date_str),
        ).fetchone()
        if row:
            return dict(row)

        return None
    except Exception as e:
        print(f"  [WARN] prediction_logs 조회 오류: {e}")
        return "UNKNOWN"


def query_order_tracking(conn, item_cd: str, date_str: str):
    """order_tracking에서 해당 날짜 발주 조회"""
    try:
        row = conn.execute(
            "SELECT order_date, item_cd, item_nm, mid_cd, delivery_type, "
            "order_qty, status, order_source, created_at "
            "FROM order_tracking "
            "WHERE item_cd = ? AND order_date = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (item_cd, date_str),
        ).fetchone()
        if row:
            return dict(row)
        return None
    except Exception:
        return "UNKNOWN"


def query_eval_outcome(conn, item_cd: str, date_str: str):
    """eval_outcomes에서 해당 날짜 평가 결과 조회"""
    try:
        row = conn.execute(
            "SELECT eval_date, decision, daily_avg, current_stock, pending_qty, "
            "exposure_days, popularity_score "
            "FROM eval_outcomes "
            "WHERE item_cd = ? AND eval_date = ? "
            "ORDER BY id DESC LIMIT 1",
            (item_cd, date_str),
        ).fetchone()
        if row:
            return dict(row)
        return None
    except Exception:
        return "UNKNOWN"


def query_fail_reason(conn, item_cd: str, date_str: str):
    """order_fail_reasons에서 해당 날짜 실패 사유 조회"""
    try:
        row = conn.execute(
            "SELECT stop_reason, orderable_status, orderable_day, order_status, checked_at "
            "FROM order_fail_reasons "
            "WHERE item_cd = ? AND eval_date = ? "
            "ORDER BY id DESC LIMIT 1",
            (item_cd, date_str),
        ).fetchone()
        if row:
            return dict(row)
        return None
    except Exception:
        return "UNKNOWN"


def query_recent_sales(conn, item_cd: str, date_str: str, days: int = 7):
    """최근 N일 판매 이력 조회"""
    try:
        rows = conn.execute(
            "SELECT sales_date, sale_qty, stock_qty, disuse_qty "
            "FROM daily_sales "
            "WHERE item_cd = ? AND sales_date <= ? "
            "ORDER BY sales_date DESC LIMIT ?",
            (item_cd, date_str, days),
        ).fetchall()
        return [dict(r) for r in rows] if rows else None
    except Exception:
        return "UNKNOWN"


# ── 값 포맷 헬퍼 ─────────────────────────────────────────────


def fmt(value, fallback="N/A"):
    """값 포맷: None → N/A, 예외 → UNKNOWN, 그 외 → 그대로"""
    if value == "UNKNOWN":
        return "UNKNOWN"
    if value is None:
        return fallback
    return value


def fmt_num(value):
    """숫자 포맷: None → N/A, 0 → 0"""
    if value == "UNKNOWN":
        return "UNKNOWN"
    if value is None:
        return "N/A"
    return value


# ── 판정 로직 ─────────────────────────────────────────────────


def judge(expected_order, tracked_order, stock, adj_pred):
    """발주 판정 문구 생성"""
    # 핵심 데이터 누락 체크
    if stock in ("N/A", "UNKNOWN") or adj_pred in ("N/A", "UNKNOWN"):
        return "핵심 데이터 누락 -- 진단 불가"

    if tracked_order in ("N/A", "UNKNOWN"):
        return "해당 날짜 발주 기록 없음"

    if expected_order in ("N/A", "UNKNOWN"):
        return f"계산값 없음, 실제 발주={tracked_order}"

    # 이후 숫자 비교
    try:
        e = int(float(expected_order))
        t = int(float(tracked_order))
    except (ValueError, TypeError):
        return f"비교 불가: expected={expected_order}, tracked={tracked_order}"

    if e == t:
        return "정상: 계산값과 실제 발주 일치"

    if e == 0 and t > 0:
        return (
            "계산상 0이어야 하나 실제 발주 발생 -- "
            "promo/round 로직 또는 버전 확인 필요"
        )

    if e > 0 and t == 0:
        return (
            "계산상 발주가 있어야 하나 실제 미발주 -- "
            "필터/차단 로직 확인 필요"
        )

    return f"불일치: 계산={e}, 실제={t}"


# ── 메인 진단 ─────────────────────────────────────────────────


def run_diagnosis(store_id: str, item_cd: str, date_str: str, session_id: str):
    """진단 실행 및 결과 출력"""
    sep = "=" * 60

    print(sep)
    print(f"  발주 진단 리포트")
    print(sep)
    print(f"  매장: {store_id}")
    print(f"  상품: {item_cd}")
    print(f"  날짜: {date_str}")
    print(f"  세션: {session_id}")
    print(sep)
    print()

    # ── DB 연결 ──
    try:
        conn = get_store_conn(store_id)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return
    try:
        common_conn = get_common_conn()
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        conn.close()
        return

    # ── 1. 상품 기본 정보 ──
    print("[1] 상품 기본 정보")
    print("-" * 40)
    prod = query_product_name(common_conn, item_cd)
    details = query_product_details(common_conn, item_cd)

    if prod == "UNKNOWN":
        print(f"  item_nm    : UNKNOWN")
        print(f"  mid_cd     : UNKNOWN")
    elif prod is None:
        print(f"  item_nm    : N/A (products 미등록)")
        print(f"  mid_cd     : N/A")
    else:
        print(f"  item_nm    : {fmt(prod.get('item_nm'))}")
        print(f"  mid_cd     : {fmt(prod.get('mid_cd'))}")

    if details == "UNKNOWN":
        print(f"  unit       : UNKNOWN")
    elif details is None:
        print(f"  unit       : N/A (product_details 미등록)")
    else:
        print(f"  unit       : {fmt_num(details.get('order_unit_qty'))}")
        print(f"  expiry_days: {fmt_num(details.get('expiration_days'))}")
        print(f"  orderable  : {fmt(details.get('orderable_status'))}")
        print(f"  ord_day    : {fmt(details.get('orderable_day'))}")
        print(f"  sell_price : {fmt_num(details.get('sell_price'))}")
        print(f"  small_cd   : {fmt(details.get('small_cd'))}")
    print()

    # ── 2. 재고 현황 ──
    print("[2] 재고 현황 (realtime_inventory)")
    print("-" * 40)
    inv = query_stock(conn, item_cd)
    stock_val = "N/A"
    pending_val = "N/A"

    if inv == "UNKNOWN":
        print(f"  stock      : UNKNOWN")
        print(f"  pending    : UNKNOWN")
        stock_val = "UNKNOWN"
    elif inv is None:
        print(f"  stock      : N/A (재고 데이터 없음)")
        print(f"  pending    : N/A")
    else:
        stock_val = inv.get("stock_qty")
        pending_val = inv.get("pending_qty")
        print(f"  stock      : {fmt_num(stock_val)}")
        print(f"  pending    : {fmt_num(pending_val)}")
        print(f"  unit_qty   : {fmt_num(inv.get('order_unit_qty'))}")
        print(f"  available  : {fmt_num(inv.get('is_available'))}")
        print(f"  cut_item   : {fmt_num(inv.get('is_cut_item'))}")
        print(f"  fail_count : {fmt_num(inv.get('query_fail_count'))}")
        if inv.get("unavail_reason"):
            print(f"  unavail    : {inv['unavail_reason']}")
        print(f"  queried_at : {fmt(inv.get('queried_at'))}")
    print()

    # ── 3. 미입고 현황 ──
    print("[3] 미입고 현황 (receiving_history)")
    print("-" * 40)
    pending_recv = query_pending_receiving(conn, item_cd)
    if pending_recv == "UNKNOWN":
        print(f"  미입고     : UNKNOWN")
    elif pending_recv is None:
        print(f"  미입고     : 없음 (0건)")
    else:
        print(f"  미입고     : {len(pending_recv)}건")
        for i, r in enumerate(pending_recv, 1):
            print(f"    [{i}] order_date={fmt(r.get('order_date'))}, "
                  f"order_qty={fmt_num(r.get('order_qty'))}, "
                  f"type={fmt(r.get('delivery_type'))}")
    print()

    # ── 4. 행사 정보 ──
    print("[4] 행사 정보 (promotions)")
    print("-" * 40)
    promo = query_promo(conn, item_cd, date_str)
    if promo == "UNKNOWN":
        print(f"  promo      : UNKNOWN")
    elif promo is None:
        print(f"  promo      : 없음 (해당 날짜 활성 행사 없음)")
    else:
        print(f"  promo_type : {fmt(promo.get('promo_type'))}")
        print(f"  start_date : {fmt(promo.get('start_date'))}")
        print(f"  end_date   : {fmt(promo.get('end_date'))}")
    print()

    # ── 5. 예측 결과 ──
    print("[5] 예측 결과 (prediction_logs)")
    print("-" * 40)
    pred = query_prediction(conn, item_cd, date_str)
    adj_pred_val = "N/A"
    safety_val = "N/A"
    pred_order_qty = "N/A"

    if pred == "UNKNOWN":
        print(f"  adj_pred   : UNKNOWN")
        adj_pred_val = "UNKNOWN"
    elif pred is None:
        print(f"  예측 기록  : N/A (해당 날짜 prediction_logs 없음)")
    else:
        adj_pred_val = pred.get("adjusted_qty")
        safety_val = pred.get("safety_stock")
        pred_order_qty = pred.get("order_qty")
        print(f"  pred_qty   : {fmt_num(pred.get('predicted_qty'))}")
        print(f"  adj_pred   : {fmt_num(adj_pred_val)}")
        print(f"  safety     : {fmt_num(safety_val)}")
        print(f"  cur_stock  : {fmt_num(pred.get('current_stock'))}")
        print(f"  order_qty  : {fmt_num(pred_order_qty)}")
        print(f"  weekday_c  : {fmt_num(pred.get('weekday_coef'))}")
        print(f"  confidence : {fmt(pred.get('confidence'))}")
        print(f"  model      : {fmt(pred.get('model_type'))}")
        print(f"  stock_src  : {fmt(pred.get('stock_source'))}")
        print(f"  pend_src   : {fmt(pred.get('pending_source'))}")
        print(f"  stale      : {fmt_num(pred.get('is_stock_stale'))}")
        print(f"  created_at : {fmt(pred.get('created_at'))}")
    print()

    # ── 6. 평가 결과 ──
    print("[6] 사전 평가 (eval_outcomes)")
    print("-" * 40)
    eval_out = query_eval_outcome(conn, item_cd, date_str)
    daily_avg_val = "N/A"

    if eval_out == "UNKNOWN":
        print(f"  eval       : UNKNOWN")
    elif eval_out is None:
        print(f"  eval       : N/A (해당 날짜 평가 없음)")
    else:
        daily_avg_val = eval_out.get("daily_avg")
        print(f"  decision   : {fmt(eval_out.get('decision'))}")
        print(f"  daily_avg  : {fmt_num(daily_avg_val)}")
        print(f"  cur_stock  : {fmt_num(eval_out.get('current_stock'))}")
        print(f"  pending    : {fmt_num(eval_out.get('pending_qty'))}")
        print(f"  exposure   : {fmt_num(eval_out.get('exposure_days'))}")
        print(f"  popularity : {fmt_num(eval_out.get('popularity_score'))}")
    print()

    # ── 7. 발주 추적 ──
    print("[7] 실제 발주 (order_tracking)")
    print("-" * 40)
    tracked = query_order_tracking(conn, item_cd, date_str)
    tracked_order_val = "N/A"

    if tracked == "UNKNOWN":
        print(f"  tracked    : UNKNOWN")
        tracked_order_val = "UNKNOWN"
    elif tracked is None:
        print(f"  tracked    : N/A (해당 날짜 발주 기록 없음)")
    else:
        tracked_order_val = tracked.get("order_qty")
        print(f"  order_qty  : {fmt_num(tracked_order_val)}")
        print(f"  delivery   : {fmt(tracked.get('delivery_type'))}")
        print(f"  status     : {fmt(tracked.get('status'))}")
        print(f"  source     : {fmt(tracked.get('order_source'))}")
        print(f"  created_at : {fmt(tracked.get('created_at'))}")
    print()

    # ── 8. 발주 실패 사유 ──
    print("[8] 발주 실패 사유 (order_fail_reasons)")
    print("-" * 40)
    fail = query_fail_reason(conn, item_cd, date_str)
    if fail == "UNKNOWN":
        print(f"  fail_reason: UNKNOWN")
    elif fail is None:
        print(f"  fail_reason: N/A (실패 사유 없음)")
    else:
        print(f"  stop_reason: {fmt(fail.get('stop_reason'))}")
        print(f"  ord_status : {fmt(fail.get('orderable_status'))}")
        print(f"  ord_day    : {fmt(fail.get('orderable_day'))}")
        print(f"  order_stat : {fmt(fail.get('order_status'))}")
    print()

    # ── 9. 최근 7일 판매 이력 ──
    print("[9] 최근 7일 판매 (daily_sales)")
    print("-" * 40)
    sales = query_recent_sales(conn, item_cd, date_str)
    if sales == "UNKNOWN":
        print(f"  sales      : UNKNOWN")
    elif sales is None:
        print(f"  sales      : N/A (판매 이력 없음)")
    else:
        print(f"  {'날짜':<12} {'판매':>5} {'재고':>5} {'폐기':>5}")
        for s in sales:
            print(
                f"  {fmt(s.get('sales_date')):<12} "
                f"{fmt_num(s.get('sale_qty')):>5} "
                f"{fmt_num(s.get('stock_qty')):>5} "
                f"{fmt_num(s.get('disuse_qty')):>5}"
            )
    print()

    # ── 10. 빌드 정보 ──
    print("[10] 빌드 / 스케줄러")
    print("-" * 40)
    commit, sched_started = parse_log_build_info(date_str)
    print(f"  commit     : {commit}")
    print(f"  sched_start: {sched_started}")
    print()

    # ── 발주량 역산 ──
    print("[계산] 발주량 역산")
    print("-" * 40)

    # expected_order 계산:
    # prediction_logs.order_qty 가 가장 정확한 "시스템 계산값"
    expected_order = "N/A"
    if pred_order_qty not in ("N/A", "UNKNOWN", None):
        expected_order = pred_order_qty
        print(f"  pred_order : {expected_order} (prediction_logs.order_qty)")
    elif adj_pred_val not in ("N/A", "UNKNOWN", None):
        # 역산 시도: need = adj_pred + safety - stock - pending
        try:
            s = stock_val if stock_val not in ("N/A", "UNKNOWN", None) else 0
            p = pending_val if pending_val not in ("N/A", "UNKNOWN", None) else 0
            sf = safety_val if safety_val not in ("N/A", "UNKNOWN", None) else 0
            need = float(adj_pred_val) + float(sf) - float(s) - float(p)
            expected_order = max(0, int(need))
            unit = 1
            if details and details != "UNKNOWN" and details.get("order_unit_qty"):
                unit = details["order_unit_qty"]
            if unit > 1 and expected_order > 0:
                expected_order = ((expected_order + unit - 1) // unit) * unit
            print(f"  역산       : adj({adj_pred_val}) + safety({sf}) - stock({s}) - pending({p}) = need({need:.1f})")
            print(f"  expected   : {expected_order} (unit={unit} 올림)")
        except Exception as e:
            print(f"  역산 실패  : {e}")
            expected_order = "N/A"
    else:
        print(f"  예측 데이터 없음 -역산 불가")

    # tracked
    t_val = tracked_order_val
    if t_val in ("UNKNOWN",):
        t_val = "UNKNOWN"
    elif t_val is None:
        t_val = "N/A"

    print(f"  tracked    : {fmt_num(tracked_order_val)}")
    print()

    # ── 판정 ──
    print(sep)

    # 판정을 위한 값 정규화
    def normalize(v):
        if v in ("N/A", "UNKNOWN", None):
            return v
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return v

    e = normalize(expected_order)
    t = normalize(tracked_order_val)
    s_norm = normalize(stock_val)
    a_norm = normalize(adj_pred_val)

    verdict = judge(e, t, fmt_num(s_norm), fmt_num(a_norm))
    print(f"  [판정] {verdict}")
    print(sep)

    # 정리
    conn.close()
    common_conn.close()


# ── CLI ───────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="발주 진단 도구 -특정 상품의 발주 계산 과정 역추적",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예:
  python diagnose_order.py --store 46513 --item 8801043022262 --date 2026-03-07
  python diagnose_order.py --session e690a029 --item 8801043022262
        """,
    )
    parser.add_argument("--store", type=str, help="매장 코드 (예: 46513)")
    parser.add_argument("--item", type=str, required=True, help="상품 코드 (바코드)")
    parser.add_argument("--date", type=str, help="조회 날짜 (YYYY-MM-DD)")
    parser.add_argument("--session", type=str, help="세션 ID (--date보다 우선)")

    args = parser.parse_args()

    # --session 모드
    if args.session:
        session_id = args.session
        date_str, found_store = find_date_from_session(session_id)

        if not date_str:
            # 세션 ID를 로그에서 못 찾으면, --date 필요
            if not args.date:
                print(f"[ERROR] 세션 '{session_id}' 을 로그에서 찾을 수 없습니다.")
                print("         --date 를 함께 지정하세요.")
                sys.exit(1)
            date_str = args.date

        store_id = args.store or found_store
        if not store_id:
            print("[ERROR] 매장 코드를 특정할 수 없습니다. --store 를 지정하세요.")
            sys.exit(1)

        run_diagnosis(store_id, args.item, date_str, session_id)
        return

    # --store + --date 모드
    if not args.store or not args.date:
        parser.print_usage()
        print("\n[ERROR] --store + --date 또는 --session 중 하나는 필수입니다.")
        sys.exit(1)

    store_id = args.store
    date_str = args.date

    # 날짜 형식 검증
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print(f"[ERROR] 날짜 형식 오류: '{date_str}' (YYYY-MM-DD 형식)")
        sys.exit(1)

    # 로그에서 세션 ID 찾기
    session_id = find_session_from_log(date_str, store_id)

    run_diagnosis(store_id, args.item, date_str, session_id)


if __name__ == "__main__":
    main()
