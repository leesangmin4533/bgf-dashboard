"""
D-1: 2차 배송 보정 모듈 (second_delivery_adjuster.py)

실행 시점: 14:00 (독립 잡, run_scheduler.py에 등록)
목적: 오늘 오전 실적을 보고 2차 상품(익일 07:00 도착) 추가 발주 여부 결정

구조 원칙:
  - DB 판단 (Selenium 없음): run_second_delivery_adjustment() → AdjustmentResult
  - Selenium 실행 (부스트 대상 있을 때만): execute_boost_orders(result, driver)
  - 판단과 실행을 분리하여 부스트 대상 없으면 Selenium 세션 미사용

흐름:
  ① 오늘 발주된 2차 상품 조회 (delivery_type='2차', order_source='auto')
  ② hourly_sales_detail 레코드 존재 여부 → 미수집 판별
  ③ 오전(0~13시) 실제 판매량 집계
  ④ 과거 30일 hourly_pattern → 기대 오전 비율 계산
  ⑤ morning_ratio = 실제 오전 / (07:00 예측량 × 기대 오전 비율)
     ratio > 1.5 → 추가 발주 대상 (delta분)
     ratio < 0.5 → 감량 불가, d1_adjustment_log 에 기록만
     else        → 스킵
  ⑥ 추가 발주: execute_boost_orders(result, driver) 에서 실행
  ⑦ d1_adjustment_log 에 결과 저장

주의:
  - 07:00 원본 발주(order_source='auto')는 절대 수정하지 않음
  - 감량 불가 (이미 BGF에 제출된 발주는 회수 불가)
  - 2차 상품은 익일 07:00 도착 → "내일 수요 추정 보정"
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── 보정 임계값 ────────────────────────────────────────────────────

BOOST_THRESHOLD  = 1.5   # 오전 실적이 기대의 1.5배 초과 → 추가 발주
REDUCE_THRESHOLD = 0.5   # 오전 실적이 기대의 0.5배 미만 → 로그만 (감량 불가)
BOOST_RATE       = 0.20  # 추가 발주 비율 (기존 발주량의 20%)

PATTERN_LOOKBACK_DAYS = 30
MORNING_HOURS         = list(range(14))    # 0~13시
DEFAULT_MORNING_RATIO = 0.45               # 패턴 데이터 없을 때 기본값

# 프로젝트 루트 (bgf_auto/)
_PROJECT_ROOT = Path(__file__).parent.parent.parent


# ── 데이터 클래스 ──────────────────────────────────────────────────

@dataclass
class ItemBoostOrder:
    """execute_boost_orders() 에 전달할 추가 발주 정보"""
    item_cd:        str
    delta_qty:      int
    order_unit_qty: int = 1


@dataclass
class ItemMorningResult:
    """상품별 오전 보정 판단 결과"""
    item_cd:               str
    predicted_qty:         float = 0.0
    expected_morning_ratio: float = DEFAULT_MORNING_RATIO
    actual_morning_qty:    float = 0.0
    morning_ratio:         float = 0.0
    action:                str   = "skip"   # boost | reduce_log | skip | error
    delta_qty:             int   = 0
    order_unit_qty:        int   = 1
    reason:                str   = ""


@dataclass
class AdjustmentResult:
    """run_second_delivery_adjustment() 반환값"""
    run_at:                  str = ""
    store_id:                str = ""
    today:                   str = ""
    total_second_items:      int = 0
    morning_data_available:  int = 0
    boost_targets:           int = 0    # 추가 발주 대상 수 (실행 전)
    reduce_logged:           int = 0
    skipped:                 int = 0
    errors:                  int = 0
    error_msg:               str = ""
    details:      List[ItemMorningResult] = field(default_factory=list)
    boost_orders: List[ItemBoostOrder]    = field(default_factory=list)


# ── DB 유틸리티 ────────────────────────────────────────────────────

def _get_conn(store_id: str):
    """
    DBRouter 표준 방식으로 store DB 연결.
    row_factory=sqlite3.Row 설정으로 dict-style 접근 가능.
    """
    from src.infrastructure.database.connection import DBRouter
    conn = DBRouter.get_store_connection(store_id)
    conn.row_factory = sqlite3.Row
    return conn


def _attach_common(conn) -> None:
    """
    product_details(common.db) 를 현재 conn 에 ATTACH.
    이후 common.product_details 로 접근 가능.
    """
    common_path = _PROJECT_ROOT / "data" / "common.db"
    conn.execute(f"ATTACH DATABASE '{common_path}' AS common")


def _detach_common(conn) -> None:
    try:
        conn.execute("DETACH DATABASE common")
    except Exception:
        pass


def _ensure_log_table(conn) -> None:
    """d1_adjustment_log 테이블 보장 (없으면 생성)"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS d1_adjustment_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            log_date        TEXT NOT NULL,
            item_cd         TEXT NOT NULL,
            action          TEXT NOT NULL,
            morning_ratio   REAL,
            predicted_qty   REAL,
            actual_morning  REAL,
            delta_qty       INTEGER,
            executed        INTEGER DEFAULT 0,
            reason          TEXT,
            created_at      TEXT NOT NULL
        )
    """)
    conn.commit()


# ── 쿼리 함수 ──────────────────────────────────────────────────────

def _get_second_delivery_items(conn, today: str) -> List[Dict]:
    """
    오늘 발주된 2차 상품 목록.
    product_details 는 common.db → ATTACH 후 common.product_details 참조.
    """
    rows = conn.execute("""
        SELECT ot.item_cd,
               ot.order_qty,
               ot.order_date,
               COALESCE(common.product_details.order_unit_qty, 1) AS order_unit_qty
        FROM order_tracking ot
        LEFT JOIN common.product_details
               ON ot.item_cd = common.product_details.item_cd
        WHERE ot.order_date    = ?
          AND ot.delivery_type = '2차'
          AND ot.order_source  = 'auto'
        ORDER BY ot.item_cd
    """, (today,)).fetchall()
    return [dict(r) for r in rows]


def _has_morning_data(conn, item_cd: str, today: str) -> bool:
    """
    오늘 hourly_sales_detail 레코드 존재 여부.
    0개 팔린 것과 미수집을 구분하기 위해 별도 확인.
    """
    row = conn.execute("""
        SELECT 1 FROM hourly_sales_detail
        WHERE item_cd = ? AND sales_date = ?
        LIMIT 1
    """, (item_cd, today)).fetchone()
    return row is not None


def _get_actual_morning_qty(conn, item_cd: str, today: str) -> float:
    """오늘 0~13시 실제 판매량 합산"""
    row = conn.execute("""
        SELECT COALESCE(SUM(sale_qty), 0) AS qty
        FROM hourly_sales_detail
        WHERE item_cd    = ?
          AND sales_date = ?
          AND hour BETWEEN 0 AND 13
    """, (item_cd, today)).fetchone()
    return float(row["qty"]) if row else 0.0


def _get_expected_morning_ratio(conn, item_cd: str) -> float:
    """
    과거 30일 hourly_sales_detail 패턴으로 기대 오전(0~13시) 비율 계산.
    데이터 없거나 신뢰성 낮으면 DEFAULT_MORNING_RATIO 반환.
    """
    cutoff = (datetime.now() - timedelta(days=PATTERN_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT hour, SUM(sale_qty) AS qty
        FROM hourly_sales_detail
        WHERE item_cd    = ?
          AND sales_date >= ?
        GROUP BY hour
    """, (item_cd, cutoff)).fetchall()

    if not rows:
        return DEFAULT_MORNING_RATIO

    pattern    = {row["hour"]: float(row["qty"]) for row in rows}
    full_total = sum(pattern.values())
    if full_total <= 0:
        return DEFAULT_MORNING_RATIO

    morning_total = sum(pattern.get(h, 0.0) for h in MORNING_HOURS)
    ratio = morning_total / full_total
    return ratio if ratio > 0.05 else DEFAULT_MORNING_RATIO


def _get_predicted_qty(conn, item_cd: str, today: str) -> Optional[float]:
    """
    prediction_logs 에서 오늘 예측값(adjusted_qty) 조회.
    prediction_date=today 기준으로 최신 1건 반환.
    """
    row = conn.execute("""
        SELECT adjusted_qty
        FROM prediction_logs
        WHERE item_cd         = ?
          AND prediction_date = ?
        ORDER BY rowid DESC
        LIMIT 1
    """, (item_cd, today)).fetchone()
    return float(row["adjusted_qty"]) if row else None


def _calc_delta(order_qty: int, boost_rate: float, order_unit_qty: int) -> int:
    """추가 발주량 계산. 발주단위 배수로 올림."""
    raw   = order_qty * boost_rate
    units = math.ceil(raw / order_unit_qty)
    return max(units, 1) * order_unit_qty


def _save_log(conn, today: str, r: ItemMorningResult, executed: bool = False) -> None:
    """d1_adjustment_log 에 결과 저장"""
    conn.execute("""
        INSERT INTO d1_adjustment_log
            (log_date, item_cd, action, morning_ratio, predicted_qty,
             actual_morning, delta_qty, executed, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        today, r.item_cd, r.action,
        round(r.morning_ratio, 4),
        r.predicted_qty,
        r.actual_morning_qty,
        r.delta_qty,
        1 if executed else 0,
        r.reason,
        datetime.now().isoformat(),
    ))
    conn.commit()


# ── Phase 1: DB 판단 (Selenium 없음) ──────────────────────────────

def run_second_delivery_adjustment(store_id: str) -> AdjustmentResult:
    """
    14:00에 DB만으로 보정 여부를 판단하고 AdjustmentResult 반환.
    Selenium 세션 불필요.

    result.boost_orders 가 비어있으면 Selenium 불필요.
    비어있지 않으면 execute_boost_orders(result, driver) 를 호출해야 함.
    """
    today  = datetime.now().strftime("%Y-%m-%d")
    result = AdjustmentResult(
        run_at   = datetime.now().isoformat(),
        store_id = store_id,
        today    = today,
    )

    try:
        conn = _get_conn(store_id)
        _attach_common(conn)
        _ensure_log_table(conn)

        # ① 2차 상품 목록
        second_items = _get_second_delivery_items(conn, today)
        result.total_second_items = len(second_items)

        if not second_items:
            logger.info(f"[D-1] 오늘 2차 발주 상품 없음 (store={store_id})")
            _detach_common(conn)
            conn.close()
            return result

        logger.info(f"[D-1] 2차 상품 {len(second_items)}개 판단 시작")

        for item in second_items:
            item_cd        = item["item_cd"]
            order_qty      = item["order_qty"]
            order_unit_qty = item["order_unit_qty"]

            r = ItemMorningResult(item_cd=item_cd, order_unit_qty=order_unit_qty)

            try:
                # ② 미수집 여부 판별 (0판매 ≠ 미수집)
                if not _has_morning_data(conn, item_cd, today):
                    r.action = "skip"
                    r.reason = "hourly_sales_detail 미수집"
                    result.skipped += 1
                    _save_log(conn, today, r)
                    result.details.append(r)
                    continue

                result.morning_data_available += 1

                # ③ 07:00 예측값
                predicted_qty = _get_predicted_qty(conn, item_cd, today)
                if predicted_qty is None or predicted_qty <= 0:
                    r.action = "skip"
                    r.reason = "prediction_logs 없음"
                    result.skipped += 1
                    _save_log(conn, today, r)
                    result.details.append(r)
                    continue

                r.predicted_qty = predicted_qty

                # ④ 기대 오전 비율 + 실제 오전 판매량
                r.expected_morning_ratio = _get_expected_morning_ratio(conn, item_cd)
                r.actual_morning_qty     = _get_actual_morning_qty(conn, item_cd, today)

                expected_morning = predicted_qty * r.expected_morning_ratio
                if expected_morning <= 0:
                    r.action = "skip"
                    r.reason = "기대 오전량 0"
                    result.skipped += 1
                    _save_log(conn, today, r)
                    result.details.append(r)
                    continue

                # ⑤ morning_ratio 계산
                r.morning_ratio = r.actual_morning_qty / expected_morning

                # ⑥ 판단
                if r.morning_ratio > BOOST_THRESHOLD:
                    delta        = _calc_delta(order_qty, BOOST_RATE, order_unit_qty)
                    r.action     = "boost"
                    r.delta_qty  = delta
                    r.reason     = (
                        f"morning_ratio={r.morning_ratio:.2f} > {BOOST_THRESHOLD} "
                        f"→ +{delta}개 추가 발주 예정 (내일 재고 보강)"
                    )
                    result.boost_targets += 1
                    result.boost_orders.append(
                        ItemBoostOrder(item_cd=item_cd, delta_qty=delta,
                                       order_unit_qty=order_unit_qty)
                    )

                elif r.morning_ratio < REDUCE_THRESHOLD:
                    r.action = "reduce_log"
                    r.reason = (
                        f"morning_ratio={r.morning_ratio:.2f} < {REDUCE_THRESHOLD} "
                        f"→ 감량 불가 (이미 제출), 내일 예측 피드백용 기록"
                    )
                    result.reduce_logged += 1

                else:
                    r.action = "skip"
                    r.reason = (
                        f"morning_ratio={r.morning_ratio:.2f} "
                        f"({REDUCE_THRESHOLD}~{BOOST_THRESHOLD} 정상 범위)"
                    )
                    result.skipped += 1

                # 판단 결과 로그 저장 (실행 여부는 아직 False)
                _save_log(conn, today, r, executed=False)

            except Exception as e:
                r.action = "error"
                r.reason = str(e)
                result.errors += 1
                logger.warning(f"[D-1] {item_cd} 판단 실패: {e}")

            result.details.append(r)

        _detach_common(conn)
        conn.close()

    except Exception as e:
        result.error_msg = str(e)
        logger.error(f"[D-1] 판단 단계 실패 (store={store_id}): {e}")

    logger.info(
        f"[D-1] 판단 완료 | store={store_id} "
        f"total={result.total_second_items} "
        f"boost_targets={result.boost_targets} "
        f"reduce_logged={result.reduce_logged} "
        f"skipped={result.skipped} "
        f"errors={result.errors}"
    )

    return result


# ── Phase 2: Selenium 실행 (부스트 대상 있을 때만) ─────────────────

def execute_boost_orders(result: AdjustmentResult, driver) -> Dict:
    """
    run_second_delivery_adjustment() 결과에서 boost_orders 를 실제 발주.

    Args:
        result: 판단 단계 결과
        driver: 14:00 새 Selenium 세션 (run_scheduler.py 에서 주입)

    Returns:
        {"executed": N, "failed": M}
    """
    if not result.boost_orders:
        return {"executed": 0, "failed": 0}

    today    = result.today
    store_id = result.store_id
    executed = 0
    failed   = 0

    try:
        from src.order.order_executor import OrderExecutor
        executor = OrderExecutor(driver=driver, store_id=store_id)
    except Exception as e:
        logger.error(f"[D-1] OrderExecutor 초기화 실패: {e}")
        return {"executed": 0, "failed": len(result.boost_orders)}

    conn = _get_conn(store_id)
    _ensure_log_table(conn)

    for bo in result.boost_orders:
        try:
            order_result = executor.execute_order(
                item_cd=bo.item_cd,
                qty=bo.delta_qty,
            )
            if order_result.get("success"):
                executed += 1
                # d1_adjustment_log executed 갱신
                conn.execute("""
                    UPDATE d1_adjustment_log
                    SET executed = 1
                    WHERE log_date = ? AND item_cd = ? AND action = 'boost'
                """, (today, bo.item_cd))
                conn.commit()
                logger.info(f"[D-1] BOOST 완료 | {bo.item_cd} +{bo.delta_qty}개")
            else:
                failed += 1
                msg = order_result.get("message", "unknown")
                logger.warning(f"[D-1] BOOST 실패 | {bo.item_cd} +{bo.delta_qty}개: {msg}")

        except Exception as e:
            failed += 1
            logger.warning(f"[D-1] BOOST 예외 | {bo.item_cd}: {e}")

    conn.close()

    logger.info(f"[D-1] 발주 실행 완료 | executed={executed} failed={failed}")
    return {"executed": executed, "failed": failed}


# ── CLI ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="D-1: 2차 배송 보정 (판단 단계)")
    parser.add_argument("--store", "-s", required=True)
    args = parser.parse_args()

    r = run_second_delivery_adjustment(args.store)
    print(json.dumps({
        "run_at":                 r.run_at,
        "store_id":               r.store_id,
        "today":                  r.today,
        "total_second_items":     r.total_second_items,
        "morning_data_available": r.morning_data_available,
        "boost_targets":          r.boost_targets,
        "reduce_logged":          r.reduce_logged,
        "skipped":                r.skipped,
        "errors":                 r.errors,
        "details": [
            {
                "item_cd":                d.item_cd,
                "action":                 d.action,
                "morning_ratio":          round(d.morning_ratio, 3),
                "expected_morning_ratio": round(d.expected_morning_ratio, 3),
                "actual_morning_qty":     d.actual_morning_qty,
                "predicted_qty":          d.predicted_qty,
                "delta_qty":              d.delta_qty,
                "reason":                 d.reason,
            }
            for d in r.details
        ],
    }, ensure_ascii=False, indent=2))
